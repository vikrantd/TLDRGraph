"""
Click subcommands for TLDRGraph CLI.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple
import click

from . import paths, vector_store as vs_mod
from .cli_enrichment import (
    AGENT_ENRICHMENT_SOURCE,
    AUDIT_LOG_FILENAME,
    CURSOR_FILENAME,
    LEGACY_FILENAME,
    LEGACY_REQUEST_FILENAME,
    LEGACY_RESPONSE_FILENAME,
    REQUEST_FILENAME,
    RESPONSE_FILENAME,
    STATE_DIR,
    apply_enrichment_items,
    build_enrichment_batch,
    coerce_enrichment_items,
    compute_degrees,
    needs_agent_enrichment,
    read_payload,
    stamp_degrees,
    state_path,
    write_cursor,
    write_payload,
)
from .flow_engine import FlowEngine
from .graph_loader import GraphLoader
from .installer import ensure_gitignore, gitignore_warnings, install_agent_rules
from .propose_layers import (
    RESPONSE_FILENAME as PROPOSE_RESPONSE_FILENAME,
    apply_proposed_layers,
    auto_configure_layers,
    generate_propose_request,
)
from .visualizer import generate_visualizer_html

DEAD_CODE_STATUSES = ("candidate", "unreviewed", "live", "entry_point", "not_code")
DEAD_CODE_STATUS_NOTES = {
    "candidate": "Nothing observed reaches these. That is evidence, not proof - review before touching.",
    "unreviewed": "NOT ENOUGH EVIDENCE to conclude anything. These are not removable.",
    "live": "Reached by something in the graph.",
    "entry_point": "Roots: route handlers, CLI entries, cron jobs, exported public API.",
    "not_code": "Not reviewable source: external package references and prose nodes.",
}


def fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def snapshot_or_graph_nodes(root: str) -> Tuple[List[Dict[str, Any]], str]:
    loader = GraphLoader(root)
    snapshot = loader.load_graph_snapshot()
    if snapshot and isinstance(snapshot.get("nodes"), list):
        return [n for n in snapshot["nodes"] if isinstance(n, dict)], "snapshot"
    loader.load_or_extract(enrich_llm=False)
    return [dict(data, id=str(nid)) for nid, data in loader.graph.nodes(data=True)], "graph"


def serve_visualizer(path: str, html_path: str, port: int, open_browser: bool = True) -> None:
    import functools
    import http.server
    import socketserver
    import threading
    import webbrowser

    root = os.path.abspath(path)
    rel_html = os.path.relpath(os.path.abspath(html_path), root).replace(os.sep, "/")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

    handler = functools.partial(QuietHandler, directory=root)
    socketserver.TCPServer.allow_reuse_address = True

    try:
        httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise click.ClickException(f"Could not bind port {port}: {exc}. Pick another with --port.")

    url = f"127.0.0.1:{port}/{rel_html}"
    click.echo(f"📡 Serving {root} at http://{url}")
    click.echo("   Source files load live from this server. Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://{url}")).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\n👋 Visualizer server stopped.")
    finally:
        httpd.server_close()


def print_doctor_report(d: Dict[str, Any]) -> None:
    backend_note = {
        vs_mod.BACKEND_TFIDF: "TF-IDF cosine only — lexical / exact-identifier retrieval. No model, no ONNX, no network.",
        vs_mod.BACKEND_HYBRID: "TF-IDF + dense ONNX embeddings, fused. Lexical retrieval is preserved; dense adds natural-language intent matching.",
    }[d["backend"]]

    click.echo("\n🩺 TLDRGraph doctor\n")
    click.echo(f"  Retrieval backend   : {d['backend'].upper()}\n                        {backend_note}")
    click.echo(f"  Policy              : {d['policy']}  (${d['policy_env_var']}, or --embeddings off|auto|on)")
    click.echo(f"  Bridge score floor  : {d['score_floor']}   (per backend: {d['score_floors']})")
    if d["backend"] == vs_mod.BACKEND_HYBRID:
        f = d["fusion"]
        span = round(1 - f["dense_baseline"], 2)
        click.echo(f"  Fusion              : (1-w)*tfidf + w*clamp((cos - {f['dense_baseline']}) / {span}, 0, 1)")
        click.echo(f"                        w = {f['dense_weight_identifier']} for identifier queries (< {f['prose_min_words']} words), {f['dense_weight_prose']} for prose")

    click.echo("\n  Embedding model")
    fe = d["fastembed_version"] or "NOT INSTALLED  (pip install 'codechakra[embeddings]')"
    click.echo(f"    fastembed         : {fe}")
    click.echo(f"    model             : {d['model_name']}{'  [' + d['model_repo'] + ']' if d['model_repo'] else ''}")
    click.echo(f"    cached on disk    : {'yes' if d['model_present'] else 'NO'}")
    click.echo(f"    cache dir         : {d['model_cache_dir']}")
    click.echo(f"    embedder live     : {'yes' if d['embedder_available'] else 'NO'}")
    if not d["embedder_available"]:
        click.echo(f"    reason            : {d['embedder_reason']}")
    click.echo(f"    dimension         : {d['embedding_dim'] if d['embedding_dim'] else '-'}")

    click.echo("\n  Index")
    click.echo(f"    path              : {d['index_path']}")
    if not d["index_exists"]:
        click.echo("    state             : MISSING — run `tldrgraph scan .` first")
    else:
        click.echo(f"    size              : {fmt_bytes(d['index_bytes'])}")
        stale = d["index_format_version"] != d["expected_format_version"]
        click.echo(f"    format version    : {d['index_format_version']} (expected {d['expected_format_version']}){'  ← STALE, will be rebuilt on next scan' if stale else ''}")
    click.echo(f"    documents         : {d['document_count']}")
    coverage = d["embedding_coverage"]
    total = d["document_count"] or 0
    pct = f"{(100.0 * coverage / total):.1f}%" if total else "n/a"
    click.echo(f"    embedding coverage: {coverage}/{total} ({pct})")
    click.echo(f"    vector sidecar    : {d['embeddings_sidecar'] if d['embeddings_sidecar_exists'] else '(none)'}{'  ' + fmt_bytes(d['embeddings_sidecar_bytes']) if d['embeddings_sidecar_exists'] else ''}")
    click.echo()


def run_queue_enrichment(path: str, limit: int, requeue: bool, reset: bool) -> None:
    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)
    degrees = stamp_degrees(loader)
    loader.save_graph()

    request = build_enrichment_batch(path, loader, degrees, limit=limit, requeue=requeue, reset=reset)
    batch = request["batch"]
    cursor = request["cursor"]
    req_path = state_path(path, REQUEST_FILENAME)
    resp_path = state_path(path, RESPONSE_FILENAME)
    write_payload(req_path, request["payload"])
    cursor_path = write_cursor(path, cursor["queued"] + [n["id"] for n in batch], cursor["applied"])

    click.echo(f"📋 Queued {len(batch)} node(s) in {req_path}")
    click.echo("   Ordering: cross-layer seams first, then hub degree (in+out), then id.")
    if batch:
        top = batch[0]
        click.echo(f"   Top of queue: {top['label']} (degree {top['degree']}, cross-layer {top['cross_layer_degree']})")
    click.echo(f"   Progress: {request['already_enriched']} enriched • {len(batch)} in this batch • {request['remaining_after']} remaining of {request['total_candidates']} candidates")
    click.echo(f"   Cursor: {cursor_path}")
    if not batch:
        click.echo("   Nothing left to queue. Use --requeue for abandoned batches, or --reset to start over.")
        return
    click.echo(f"\n👉 Write the response to {resp_path}, then run `tldrgraph apply-enrichment`.")


def run_apply_enrichment(path: str, enrichment_file: Optional[str]) -> None:
    if not enrichment_file:
        for filename in (RESPONSE_FILENAME, LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
            candidate = state_path(path, filename)
            if os.path.isfile(candidate):
                enrichment_file = candidate
                if filename in (LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
                    click.echo(f"ℹ️  Reading enrichment from {filename}.")
                break
    if not enrichment_file:
        raise click.ClickException(
            f"No enrichment response found. Expected {state_path(path, RESPONSE_FILENAME)} (or {state_path(path, LEGACY_RESPONSE_FILENAME)}). Run `tldrgraph queue-enrichment` first."
        )

    raw = read_payload(enrichment_file)
    if raw is None:
        raise click.ClickException(f"Could not parse payload from {enrichment_file}")

    for req_fn in (REQUEST_FILENAME, LEGACY_REQUEST_FILENAME):
        request_path = state_path(path, req_fn)
        if os.path.abspath(enrichment_file) == os.path.abspath(request_path):
            raise click.ClickException(
                f"{request_path} is the request, not the response. Write the agent's answer to {state_path(path, RESPONSE_FILENAME)} instead - the request is regenerated on every `queue-enrichment` run."
            )

    items = coerce_enrichment_items(raw)
    if not items:
        raise click.ClickException(f"{enrichment_file} contains no enrichment objects. Expected a list of {{id, intent, fields, calls}}.")

    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)
    stats = apply_enrichment_items(loader, path, items, enrichment_file)
    applied_ids = stats["applied_ids"]
    unknown_ids = stats["unknown_ids"]
    unresolved = stats["unresolved"]
    still_pending = sum(1 for _, d in loader.graph.nodes(data=True) if needs_agent_enrichment(d))

    click.echo(f"✅ Applied {len(applied_ids)} enrichment(s) from {enrichment_file}")
    click.echo(f"🔗 Created {stats['bridges']} cross-layer bridge edge(s) (score floor {stats['floor']}, backend {loader.vector_store.backend})")
    if unresolved:
        preview = ", ".join(sorted(set(unresolved))[:6])
        click.echo(f"⚠️  {len(unresolved)} call target(s) below the score floor / unmatched: {preview}")
    if unknown_ids:
        preview = ", ".join(unknown_ids[:3])
        click.echo(f"⚠️  {len(unknown_ids)} id(s) not in the graph, skipped: {preview}")
    click.echo(f"💾 Graph snapshot updated at: {stats['snapshot_path']}")
    click.echo(f"📊 {still_pending} candidate(s) still un-enriched. Run `tldrgraph queue-enrichment` for the next batch.")


def _print_dead_code_json(rows: List[Dict[str, Any]], status: str, source: str) -> None:
    click.echo(json.dumps({
        "status": status,
        "source": source,
        "available": True,
        "disclaimer": "Review candidates, not confirmed dead code. 'unreviewed' means insufficient evidence and is never removable. Verify in the source before removing anything.",
        "count": len(rows),
        "nodes": rows,
    }, indent=2))


def _print_dead_code_human(rows: List[Dict[str, Any]], status: str, source: str) -> None:
    label = "all statuses" if status == "all" else f"status '{status}'"
    click.echo(f"\n🔎 TLDRGraph reachability review - {label} ({len(rows)} node(s), from {source})")
    click.echo("   These are REVIEW CANDIDATES, not confirmed dead code.")
    if status in DEAD_CODE_STATUS_NOTES:
        click.echo(f"   {DEAD_CODE_STATUS_NOTES[status]}")
    click.echo("   Static analysis cannot see reflection, DI, string-built routes or template refs.\n")

    if not rows:
        click.echo("   (nothing matched)")
        return

    for row in rows:
        click.echo(f"  • {row['label']}  [{row['status']}]")
        click.echo(f"      layer : {row['layer']}")
        click.echo(f"      file  : {row['file'] or '(unknown)'}")
        click.echo(f"      reason: {row['reason'] or '(no reason recorded)'}")
    click.echo("\n👉 Hand this list to an agent to verify against the source. TLDRGraph will not delete anything for you.")


def run_dead_code_report(path: str, status: str, limit: int, as_json: bool) -> None:
    nodes, source = snapshot_or_graph_nodes(path)
    annotated = [n for n in nodes if n.get("dead_code_status")]
    if not annotated:
        message = "No reachability review data yet: no node carries a 'dead_code_status'. Re-run `tldrgraph scan .` with a build that computes it."
        if as_json:
            click.echo(json.dumps({"status": status, "source": source, "available": False, "note": message, "count": 0, "nodes": []}, indent=2))
        else:
            click.echo(f"ℹ️  {message}")
        return

    selected = [n for n in annotated if status == "all" or str(n.get("dead_code_status", "")).lower() == status]
    selected.sort(key=lambda n: (str(n.get("layer", "")), str(n.get("file", "")), str(n.get("label", n.get("id", "")))))
    if limit and limit > 0:
        selected = selected[:limit]

    rows = [{
        "id": str(n.get("id", "")),
        "label": n.get("label", n.get("id", "")),
        "file": n.get("file", ""),
        "layer": n.get("layer", ""),
        "status": n.get("dead_code_status", ""),
        "reason": n.get("dead_code_reason", ""),
    } for n in selected]

    if as_json:
        _print_dead_code_json(rows, status, source)
    else:
        _print_dead_code_human(rows, status, source)
