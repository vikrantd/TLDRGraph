"""
Init workflow pipeline for TLDRGraph CLI.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any, Dict, List, Optional
import click

from . import agent_runner, paths
from .cli_agent_loop import build_agent_enrichment_prompt, run_agent_enrichment
from .cli_enrichment import (
    AGENT_ENRICHMENT_SOURCE,
    REQUEST_FILENAME,
    RESPONSE_FILENAME,
    STATE_DIR,
    apply_enrichment_items,
    build_enrichment_batch,
    coerce_enrichment_items,
    compute_degrees,
    enrichment_candidates,
    needs_agent_enrichment,
    read_payload,
    stamp_degrees,
    state_path,
    write_payload,
)
from .graph_loader import GraphLoader
from .installer import ensure_gitignore, install_agent_rules
from .layer_config import config_path
from .layers import get_registry
from .propose_layers import (
    RESPONSE_FILENAME as PROPOSE_RESPONSE_FILENAME,
    apply_proposed_layers,
    auto_configure_layers,
    generate_propose_request,
)
from .visualizer import generate_visualizer_html

STATUS_DONE = "done"
STATUS_NEEDS_LAYERS = "needs_layers"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_NEEDS_ENRICHMENT = "needs_enrichment"
APPLIED_RESPONSE_FILENAME = "enrichment_response.applied.yaml"


@contextlib.contextmanager
def stdout_to_stderr_if(active: bool):
    if not active:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


def stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def emit_status(status: str, phase: str, lines: List[str], progress: Optional[Dict[str, Any]] = None, as_json: bool = False) -> None:
    if as_json:
        click.echo(json.dumps({
            "status": status,
            "phase": phase,
            "next_action": lines,
            "progress": progress or {},
        }, indent=2))
        return

    rule = "─" * 68
    click.echo(f"\n{rule}")
    click.echo("TLDRGRAPH INIT — COMPLETE" if status == STATUS_DONE else "TLDRGRAPH INIT — NEXT ACTION REQUIRED")
    click.echo(f"status: {status}")
    click.echo(rule)
    for line in lines:
        click.echo(line)
    click.echo(f"{rule}\n")


def apply_pending_layer_response(path: str) -> Optional[str]:
    for filename in (PROPOSE_RESPONSE_FILENAME, "propose_layers_response.yaml"):
        candidate = state_path(path, filename)
        if os.path.isfile(candidate):
            return apply_proposed_layers(path, candidate)
    return None


def apply_pending_enrichment_response(path: str, loader: GraphLoader) -> Optional[Dict[str, Any]]:
    for filename in (RESPONSE_FILENAME, "enrichment_response.json", "pending_enrichment.yaml", "pending_enrichment.json"):
        candidate = state_path(path, filename)
        if os.path.isfile(candidate):
            items = coerce_enrichment_items(read_payload(candidate))
            if not items:
                continue
            stats = apply_enrichment_items(loader, path, items, candidate)
            try:
                os.replace(candidate, state_path(path, APPLIED_RESPONSE_FILENAME))
            except OSError:
                pass
            return stats
    return None


def _check_confirmation(candidates: List[Dict[str, Any]], total: int, enriched: int, excluded: int, rounds: int, batch_size: int, progress: Dict[str, Any], as_json: bool) -> Optional[str]:
    if stdin_is_interactive():
        click.echo(f"\n🧠 {len(candidates)} node(s) need an intent read from the source ({rounds} batch(es) of {batch_size}).")
        if not click.confirm("   Enrich now?", default=True):
            click.echo("   Skipped. Run `tldrgraph init` again when ready.")
            return STATUS_NEEDS_CONFIRMATION
        return None
    emit_status(STATUS_NEEDS_CONFIRMATION, "enrichment", [
        f"The graph is built and queryable: {total} nodes, {enriched} enriched from source, {excluded} not eligible (utility bucket, prose nodes).",
        "",
        f"{len(candidates)} node(s) still carry generated summaries rather than",
        "an intent read from the source. Enriching them means roughly",
        f"{rounds} round(s) of {batch_size} nodes, and each round costs tokens.",
        "",
        "ASK THE USER whether to proceed, showing them that estimate. Then:",
        "",
        "  they agree          → tldrgraph init --yes",
        "  smaller first pass  → tldrgraph init --yes --limit 100",
        "  they decline        → stop here; the graph is already usable",
    ], progress=progress, as_json=as_json)
    return STATUS_NEEDS_CONFIRMATION


def _run_agent_cli_enrichment(
    path: str,
    loader: GraphLoader,
    batch_size: int,
    max_nodes: int,
    agent_model: Optional[str],
    progress: Dict[str, Any],
    as_json: bool,
) -> Optional[str]:
    agent = agent_runner.find_agent_cli()
    if agent is None:
        if not as_json:
            click.echo(f"   ℹ️  No agent CLI available ({agent_runner.agent_status()['detail']}); handing off instead.")
        return None

    if not as_json:
        click.echo(f"\n🤖 Enriching via {agent.display}...")
    totals = run_agent_enrichment(path, loader, agent, batch_size=batch_size, max_nodes=max_nodes, model=agent_model)
    rem = len(enrichment_candidates(loader, compute_degrees(loader.graph)))
    status = STATUS_DONE if not rem else STATUS_NEEDS_ENRICHMENT
    emit_status(status, "enrichment", [
        f"Enriched {totals['applied']} node(s) in {totals['batches']} batch(es); {totals['bridges']} bridge edge(s).",
        f"{rem} still un-enriched." if rem else "Nothing left to enrich.",
    ] + (["Run `tldrgraph init --yes` to continue."] if rem else []),
        progress={**progress, "remaining": rem}, as_json=as_json)
    return status


def _emit_manual_enrichment_handoff(
    path: str,
    root: str,
    loader: GraphLoader,
    candidates: List[Dict[str, Any]],
    progress: Dict[str, Any],
    batch_size: int,
    max_nodes: int,
    as_json: bool,
) -> str:
    limit = batch_size if not max_nodes else min(batch_size, max_nodes)
    req = build_enrichment_batch(path, loader, compute_degrees(loader.graph), limit=limit, skip_cursor=True)
    req_path = write_payload(state_path(path, REQUEST_FILENAME), req["payload"])

    emit_status(STATUS_NEEDS_ENRICHMENT, "enrichment", [
        f"{len(req['batch'])} node(s) queued, {len(candidates)} remaining overall.",
        "",
        f"  1. Read {os.path.relpath(req_path, root)}",
        "  2. OPEN the source file of every node in it. An intent guessed from a",
        "     symbol name is worse than none -- it poisons semantic search.",
        f"  3. Write {os.path.join(STATE_DIR, RESPONSE_FILENAME)} (YAML list of",
        "     {id, intent, input_fields, output_fields, calls}). Copy each id verbatim.",
        "  4. Run: tldrgraph init",
    ], progress=progress, as_json=as_json)
    return STATUS_NEEDS_ENRICHMENT


def _emit_enrichment_done(total: int, enriched: int, excluded: int, registry: Any, as_json: bool) -> str:
    emit_status(STATUS_DONE, "enrichment", [
        f"{total} nodes across {len(registry)} layers. {enriched} enriched from source; {excluded} not eligible (utility bucket and prose nodes).",
        "",
        '  tldrgraph query "<feature in plain English>"',
        '  tldrgraph trace "<Source>" "<Target>"',
        "  tldrgraph layers",
        "  tldrgraph ui --serve",
    ], progress={"total_nodes": total, "enriched": enriched, "remaining": 0}, as_json=as_json)
    return STATUS_DONE


def _handle_enrichment_step(
    path: str,
    root: str,
    loader: GraphLoader,
    registry: Any,
    assume_yes: bool,
    batch_size: int,
    max_nodes: int,
    agent_cli: bool,
    agent_model: Optional[str],
    as_json: bool,
) -> str:
    candidates = enrichment_candidates(loader, compute_degrees(loader.graph))
    total = loader.graph.number_of_nodes()
    enriched = sum(
        1 for _, d in loader.graph.nodes(data=True)
        if (d.get("enrichment_source") or "") == AGENT_ENRICHMENT_SOURCE
    )
    excluded = total - enriched - len(candidates)

    if not candidates:
        return _emit_enrichment_done(total, enriched, excluded, registry, as_json)

    planned = min(len(candidates), max_nodes) if max_nodes else len(candidates)
    rounds = (planned + batch_size - 1) // batch_size
    progress = {
        "total_nodes": total,
        "enriched": enriched,
        "excluded": excluded,
        "remaining": len(candidates),
        "planned_this_run": planned,
        "batch_size": batch_size,
        "agent_rounds": rounds,
    }

    if not assume_yes:
        conf_status = _check_confirmation(candidates, total, enriched, excluded, rounds, batch_size, progress, as_json)
        if conf_status:
            return conf_status

    if agent_cli:
        res = _run_agent_cli_enrichment(path, loader, batch_size, max_nodes, agent_model, progress, as_json)
        if res is not None:
            return res

    return _emit_manual_enrichment_handoff(path, root, loader, candidates, progress, batch_size, max_nodes, as_json)


def _ensure_layers_configured(
    path: str,
    root: str,
    relayer: bool,
    agent_cli: bool,
    agent_model: Optional[str],
    as_json: bool,
) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
    applied_cfg = None
    if relayer or not config_path(root):
        applied_cfg = apply_pending_layer_response(path)
        if applied_cfg and not as_json:
            click.echo(f"🏗️  Applied the agent's layer design → {applied_cfg}")

    notes: List[str] = []
    registry, cfg_path, source = auto_configure_layers(
        path, force=relayer and not applied_cfg, use_agent=agent_cli,
        agent_model=agent_model, notes=notes,
    )
    for note in notes:
        if not as_json:
            click.echo(f"   ℹ️  {note}")

    if registry is None:
        request_path = generate_propose_request(path)
        emit_status(STATUS_NEEDS_LAYERS, "layers", [
            "This repository has no architectural layer set, and TLDRGraph will",
            "not invent one from a template. Design it from the code:",
            "",
            f"  1. Read {os.path.relpath(request_path, root)}",
            "  2. OPEN real source files -- entry points first, then one file from each cluster in the evidence. Do not skip this step.",
            f"  3. Write {os.path.join(STATE_DIR, PROPOSE_RESPONSE_FILENAME)} with",
            '     {"utility_id": "...", "layers": [{id, name, order, description, rules}]}',
            "     3-6 layers plus one catch-all whose id equals utility_id and whose",
            "     rules are []. Name them after THIS repository's concepts.",
            "  4. Run: tldrgraph init",
        ], as_json=as_json)
        return None, None, None

    if not as_json:
        label = "designed by your agent" if applied_cfg else ("already configured" if source == "existing_config" else source)
        click.echo(f"🏛️  {len(registry)} architectural layers ({label})")

    return registry, cfg_path, source


def _report_enrichment_applied_status(applied: Optional[Dict[str, Any]], as_json: bool) -> None:
    if not applied or as_json:
        return
    click.echo(f"🧠 Applied {len(applied['applied_ids'])} enrichment(s), {applied['bridges']} bridge edge(s)")
    if applied["unknown_ids"]:
        preview = ", ".join(applied["unknown_ids"][:4])
        click.echo(f"   ⚠️  {len(applied['unknown_ids'])} id(s) are not in the graph and were dropped: {preview}")
        click.echo("      Copy ids verbatim from the request; do not construct them.")
    if applied["unresolved"]:
        preview = ", ".join(sorted(set(applied["unresolved"]))[:4])
        click.echo(f"   ⚠️  {len(applied['unresolved'])} call target(s) matched nothing above the score floor: {preview}")


def init_pipeline(
    path: str,
    assume_yes: bool,
    batch_size: int,
    max_nodes: int,
    rebuild: bool,
    relayer: bool,
    agent_cli: bool,
    agent_model: Optional[str],
    embeddings: Optional[str],
    as_json: bool,
) -> str:
    root = os.path.abspath(path)
    if not as_json:
        click.echo(f"🔄 [TLDRGraph] {root}")

    ensure_gitignore(path)
    install_agent_rules(path)

    loader = GraphLoader(path, embeddings=embeddings)
    if not as_json:
        click.echo("📦 Extracting AST with graphify...")
    with stdout_to_stderr_if(as_json):
        loader._run_graphify()
    loader.file_hashes = loader._load_file_hashes()

    registry, cfg_path, source = _ensure_layers_configured(path, root, relayer, agent_cli, agent_model, as_json)
    if registry is None:
        return STATUS_NEEDS_LAYERS

    graph = loader.load_or_extract(rebuild=rebuild)
    stamp_degrees(loader)
    snapshot_path = loader.save_graph()
    loader.export_yaml()

    if not as_json:
        click.echo(f"✅ {graph.number_of_nodes()} nodes, {graph.number_of_edges()} relationships")
        diag = loader.vector_store.diagnostics()
        click.echo(f"🔎 Retrieval: {diag['backend']} (floor {diag['score_floor']})\n💾 {snapshot_path}")

    applied = apply_pending_enrichment_response(path, loader)
    _report_enrichment_applied_status(applied, as_json)

    generate_visualizer_html(path)

    return _handle_enrichment_step(
        path, root, loader, registry, assume_yes, batch_size, max_nodes, agent_cli, agent_model, as_json
    )
