"""
CLI Entry Point for TLDRGraph: Multi-layer code flow & hybrid semantic search engine.
"""

import contextlib
import os
import json
import yaml
import click
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .graph_loader import (
    GraphLoader,
    BRIDGE_SCORE_FLOOR,
    bridge_score_floor,
    resolve_call_target,
)
from .flow_engine import FlowEngine
from .installer import ensure_gitignore, install_agent_rules, gitignore_warnings
from .visualizer import generate_visualizer_html
from .layers import get_registry, layer_id_of
from .layer_config import config_path
from .propose_layers import (
    RESPONSE_FILENAME as PROPOSE_RESPONSE_FILENAME,
    auto_configure_layers,
    generate_propose_request,
    apply_proposed_layers,
)
from . import agent_runner, paths
from . import vector_store as vs_mod

#: Shared option for the retrieval-backend policy. ``off`` (default) is pure
#: TF-IDF and touches nothing optional; ``auto`` uses dense embeddings only if
#: the model is already cached; ``on`` permits a one-time model download.
embeddings_option = click.option(
    "--embeddings", "embeddings",
    type=click.Choice([vs_mod.POLICY_OFF, vs_mod.POLICY_AUTO, vs_mod.POLICY_ON]),
    default=None,
    help="Retrieval backend policy. Defaults to $TLDRGRAPH_EMBEDDINGS, itself 'off'.",
)

try:  # provenance tag written by the offline template enricher
    from .deadcode import HEURISTIC_ENRICHMENT_SOURCE, NON_CODE_NODE_TYPES
except ImportError:  # pragma: no cover - deadcode module is optional
    HEURISTIC_ENRICHMENT_SOURCE = "heuristic"
    NON_CODE_NODE_TYPES = {"rationale", "concept", "doc", "documentation"}

#: Provenance stamped on nodes enriched through the host-agent loop.
AGENT_ENRICHMENT_SOURCE = "agent"

#: State directory, relative to the repository root.
STATE_DIR = ".tldrgraph"

#: Written by `queue-enrichment`, read by the coding agent.
REQUEST_FILENAME = "enrichment_request.yaml"
LEGACY_REQUEST_FILENAME = "enrichment_request.json"

#: Written by the coding agent, read by `apply-enrichment`.
RESPONSE_FILENAME = "enrichment_response.yaml"
LEGACY_RESPONSE_FILENAME = "enrichment_response.json"

#: Pre-split filename. Still honoured as a response so hand-written work is never stranded.
LEGACY_FILENAME = "pending_enrichment.json"

#: Paging / progress state shared by queue-enrichment and apply-enrichment.
CURSOR_FILENAME = "enrichment_cursor.json"

#: Audit log recording all applied enrichments and forged linkages.
AUDIT_LOG_FILENAME = "enrichment_audit.log"

#: Display name of the utility bucket, resolved from the layer registry.
#: Exported for humans and legacy callers only -- the checks below compare the
#: stable layer *id*, so renaming this layer changes nothing but the text.
UTILITY_LAYER = get_registry().utility.name

#: dead_code_status values, from least to most reviewed.
DEAD_CODE_STATUSES = ("candidate", "unreviewed", "live", "entry_point", "not_code")

DEAD_CODE_STATUS_NOTES = {
    "candidate": "Nothing observed reaches these. That is evidence, not proof - review before touching.",
    "unreviewed": "NOT ENOUGH EVIDENCE to conclude anything. These are not removable.",
    "live": "Reached by something in the graph.",
    "entry_point": "Roots: route handlers, CLI entries, cron jobs, exported public API.",
    "not_code": "Not reviewable source: external package references and prose nodes.",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _state_path(root: str, filename: str) -> str:
    return os.path.join(root, STATE_DIR, filename)


def _read_payload(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if path.endswith(".json") or content.startswith("{") or content.startswith("["):
                try:
                    return json.loads(content)
                except Exception:
                    pass
            return yaml.safe_load(content)
    except (OSError, ValueError):
        return None


def _write_payload(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if path.endswith(".json"):
            json.dump(payload, f, indent=2)
        else:
            yaml.dump(payload, f, default_flow_style=False, sort_keys=False)
    return path


_read_json = _read_payload
_write_json = _write_payload


def coerce_enrichment_items(data: Any) -> List[Dict[str, Any]]:
    """
    Accepts the canonical bare array, or a convenience wrapper object, and returns the
    list of enrichment dicts. Anything else yields an empty list.
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("enrichments", "nodes", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _read_cursor(root: str) -> Dict[str, List[str]]:
    data = _read_payload(_state_path(root, CURSOR_FILENAME))
    if not isinstance(data, dict):
        data = {}
    queued = data.get("queued")
    applied = data.get("applied")
    return {
        "queued": [str(x) for x in queued] if isinstance(queued, list) else [],
        "applied": [str(x) for x in applied] if isinstance(applied, list) else [],
    }


def _write_cursor(root: str, queued: List[str], applied: List[str]) -> str:
    return _write_payload(_state_path(root, CURSOR_FILENAME), {
        "schema": "codechakra/enrichment-cursor@1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "queued": sorted(set(queued)),
        "applied": sorted(set(applied)),
    })


def compute_degrees(graph) -> Dict[str, Tuple[int, int]]:
    """
    Real graph degree per node: ``{node_id: (degree, cross_layer_degree)}``.

    ``degree`` is in + out edges. ``cross_layer_degree`` counts the distinct neighbours
    (either direction) that sit in a *different* architectural layer -- the cross-layer
    seams where agent attention pays off most.

    graphify never emits a ``degree`` key, so anything reading ``node["degree"]`` off the
    raw export sees 0 for every node. This recomputes it from the edges.
    """
    layers = {nid: layer_id_of(data) for nid, data in graph.nodes(data=True)}
    result: Dict[str, Tuple[int, int]] = {}
    for nid in graph.nodes():
        neighbours = set(graph.successors(nid)) | set(graph.predecessors(nid))
        own_layer = layers.get(nid, "")
        cross = sum(1 for n in neighbours if layers.get(n, "") != own_layer)
        result[nid] = (graph.in_degree(nid) + graph.out_degree(nid), cross)
    return result


def _stamp_degrees(loader: GraphLoader) -> Dict[str, Tuple[int, int]]:
    """Writes the recomputed degree back onto node attrs so the snapshot stops saying 0."""
    degrees = compute_degrees(loader.graph)
    for nid, (degree, cross) in degrees.items():
        node = loader.graph.nodes[nid]
        node["degree"] = degree
        node["cross_layer_degree"] = cross
    return degrees


def needs_agent_enrichment(data: Dict[str, Any]) -> bool:
    """
    True when a node still deserves the host agent's attention.

    An intent produced by the offline template heuristic does not count: it is derived
    from the label and layer alone, without reading a single line of source, which is
    exactly the gap this loop exists to close.

    Two kinds of node are excluded outright. The utility bucket is a catch-all
    rather than an architectural layer. And graphify's prose nodes -- ``rationale``
    and friends, whose *label is already a sentence of documentation* -- are not
    symbols at all: asking an agent to describe one means paying it to copy a
    docstring back onto itself. ``deadcode`` already calls these "not source
    code"; the same set is reused here so the two cannot disagree.
    """
    if layer_id_of(data) == get_registry().utility_id:
        return False
    if str(data.get("type") or "").lower() in NON_CODE_NODE_TYPES:
        return False
    source = data.get("enrichment_source") or ""
    if source == AGENT_ENRICHMENT_SOURCE:
        return False
    return True


def _enrichment_candidates(loader: GraphLoader, degrees: Dict[str, Tuple[int, int]]) -> List[Dict[str, Any]]:
    """
    Un-enriched, non-utility nodes ordered by importance.

    Sort key, documented in AGENT_CONTRACT.md:
      1. cross_layer_degree, descending  -- seam nodes first
      2. degree (in + out), descending   -- hub nodes before leaves
      3. node id, ascending              -- deterministic tie-break
    """
    candidates: List[Dict[str, Any]] = []
    for nid, data in loader.graph.nodes(data=True):
        if not needs_agent_enrichment(data):
            continue
        degree, cross = degrees.get(nid, (0, 0))
        candidates.append({
            "id": nid,
            "label": data.get("label", nid),
            "layer_id": layer_id_of(data),
            "layer": data.get("layer", ""),
            "file": data.get("file", ""),
            "source_location": data.get("source_location"),
            "degree": degree,
            "cross_layer_degree": cross,
            "existing_intent_source": data.get("enrichment_source") or "",
        })
    candidates.sort(key=lambda c: (-c["cross_layer_degree"], -c["degree"], c["id"]))
    return candidates


#: Instructions embedded in every enrichment request. Shared by the file-based
#: handshake and the headless-agent prompt so both describe the same contract.
def _enrichment_instructions() -> List[str]:
    return [
        "Open and READ each node's source file before writing its intent - you have the repo.",
        "Write 'intent' in Markdown (single-line summary or rich multiline markdown). Add as much context as needed.",
        "In 'input_fields', list input arguments, parameters, payload attributes, or request body fields.",
        "In 'output_fields', list return values, response schemas, emitted event names, or mutated state fields.",
        "In 'calls', specify exact downstream targets: node ID, file:symbol (e.g. 'src/services/calc.ts:calculate'), file path, or symbol name.",
        "When multiple methods share the same name across files, use 'file_path:method' or exact node 'id' to ensure precise linking.",
        "Never invent fields or calls - omit what you cannot verify in the source. An empty list is a correct answer.",
        "Copy each 'id' verbatim.",
    ]


def build_enrichment_batch(
    path: str,
    loader: GraphLoader,
    degrees: Dict[str, Tuple[int, int]],
    limit: int,
    requeue: bool = False,
    reset: bool = False,
    skip_cursor: bool = False,
) -> Dict[str, Any]:
    """
    Selects the next batch of nodes deserving enrichment and builds the request payload.

    ``skip_cursor`` ignores queue bookkeeping entirely, which is what the
    automatic in-scan loop wants: it applies each batch before asking for the
    next, so the graph itself is the only progress record it needs.

    Returns a dict with the request ``payload``, the ``batch``, and progress counts.
    """
    cursor = {"queued": [], "applied": []} if reset else _read_cursor(path)

    skip: set = set()
    if not skip_cursor:
        # Anything already answered in a response file counts as done, even if
        # apply-enrichment has not run yet.
        answered = set(cursor["applied"])
        for filename in (RESPONSE_FILENAME, LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
            for item in coerce_enrichment_items(_read_payload(_state_path(path, filename))):
                if item.get("id"):
                    answered.add(str(item["id"]))
        skip = set(answered)
        if not requeue:
            skip |= set(cursor["queued"])

    candidates = _enrichment_candidates(loader, degrees)
    total_candidates = len(candidates)
    pending = [c for c in candidates if c["id"] not in skip]

    batch = pending if limit <= 0 else pending[:limit]
    for rank, node in enumerate(batch, 1):
        node["rank"] = rank

    already_enriched = sum(
        1 for _, d in loader.graph.nodes(data=True)
        if layer_id_of(d) != get_registry().utility_id and not needs_agent_enrichment(d)
    )
    remaining_after = max(len(pending) - len(batch), 0)

    payload = {
        "schema": "codechakra/enrichment-request@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": os.path.join(STATE_DIR, RESPONSE_FILENAME),
        "contract": os.path.join(STATE_DIR, "AGENT_CONTRACT.md"),
        "instructions": _enrichment_instructions() + [
            f"Write a YAML list of {{id, intent, input_fields, output_fields, calls}} to "
            f"{os.path.join(STATE_DIR, RESPONSE_FILENAME)} (NOT back into this request file).",
            "Then run: tldrgraph apply-enrichment",
        ],
        "ordering": "cross_layer_degree desc, then degree (in+out) desc, then id asc",
        "progress": {
            "total_candidates": total_candidates,
            "already_enriched": already_enriched,
            "queued_now": len(batch),
            "remaining_after": remaining_after,
        },
        "nodes": batch,
    }

    return {
        "payload": payload,
        "batch": batch,
        "cursor": cursor,
        "total_candidates": total_candidates,
        "already_enriched": already_enriched,
        "remaining_after": remaining_after,
    }


def apply_enrichment_items(
    loader: GraphLoader,
    path: str,
    items: List[Dict[str, Any]],
    source_label: str,
) -> Dict[str, Any]:
    """
    Merges enrichment objects into the graph, hash-gate cache and vector index.

    This is the one implementation behind both `apply-enrichment` (file handshake)
    and the automatic in-scan agent loop, so a bridge edge forged automatically is
    resolved exactly the same way as one applied by hand.

    Returns stats: applied_ids, unknown_ids, bridges, unresolved, snapshot_path.
    """
    floor = bridge_score_floor(loader.vector_store)

    applied_ids: List[str] = []
    unknown_ids: List[str] = []
    unresolved: List[str] = []
    bridges = 0

    for item in items:
        nid = item.get("id")
        if not nid:
            continue
        nid = str(nid)
        if not loader.graph.has_node(nid):
            unknown_ids.append(nid)
            continue

        node_data = loader.graph.nodes[nid]
        intent = item.get("intent", "")
        input_fields = item.get("input_fields", []) or []
        output_fields = item.get("output_fields", []) or []
        legacy_fields = item.get("fields", []) or []
        calls = item.get("calls", []) or []

        # Optional layer_id override from agent
        override_lid = item.get("layer_id")
        if override_lid and override_lid in get_registry():
            node_data["layer_id"] = override_lid
            node_data["layer"] = get_registry().name(override_lid)
            node_data["layer_source"] = AGENT_ENRICHMENT_SOURCE

        if intent:
            node_data["intent"] = intent
            first_line = intent.strip().split("\n")[0].lstrip("#- *").strip()
            node_data["summary"] = f"{node_data['layer']}: {node_data['label']} - {first_line or intent}"
            node_data["enrichment_source"] = AGENT_ENRICHMENT_SOURCE

        if input_fields or output_fields:
            node_data["input_fields"] = input_fields
            node_data["output_fields"] = output_fields
            node_data["fields"] = list(input_fields) + list(output_fields)
        elif legacy_fields:
            node_data["input_fields"] = legacy_fields
            node_data["output_fields"] = []
            node_data["fields"] = legacy_fields

        fields_dict = {
            "input_fields": node_data.get("input_fields", []),
            "output_fields": node_data.get("output_fields", []),
            "fields": node_data.get("fields", [])
        }
        loader.hash_gate.update_node(
            node_id=nid,
            file_path=node_data.get("file", ""),
            content=loader.node_signature(node_data),
            layer=node_data.get("layer", ""),
            summary=node_data.get("summary", ""),
            fields_json=json.dumps(fields_dict),
            intent=node_data.get("intent", "")
        )

        for call_target in calls:
            tgt_id, score = resolve_call_target(
                loader.graph, loader.vector_store, call_target, nid, floor
            )
            if tgt_id:
                loader.graph.add_edge(
                    nid, tgt_id,
                    relation="cross_layer_link",
                    confidence=float(score)
                )
                bridges += 1
            else:
                unresolved.append(str(call_target))

        applied_ids.append(nid)

    # Re-index so the applied intents/fields are actually searchable, then persist.
    loader.vector_store.add_documents(loader.docs_to_index)
    _stamp_degrees(loader)
    snapshot_path = loader.save_graph()
    loader.export_yaml()

    cursor = _read_cursor(path)
    remaining_queued = [i for i in cursor["queued"] if i not in set(applied_ids)]
    _write_cursor(path, remaining_queued, cursor["applied"] + applied_ids)

    _append_enrichment_audit(path, items, applied_ids, bridges, unresolved, source_label)

    return {
        "applied_ids": applied_ids,
        "unknown_ids": unknown_ids,
        "bridges": bridges,
        "unresolved": unresolved,
        "floor": floor,
        "snapshot_path": snapshot_path,
    }


def _append_enrichment_audit(
    path: str,
    items: List[Dict[str, Any]],
    applied_ids: List[str],
    bridges: int,
    unresolved: List[str],
    source_label: str,
) -> None:
    """Appends one batch to .tldrgraph/enrichment_audit.log. Never raises."""
    audit_path = _state_path(path, AUDIT_LOG_FILENAME)
    timestamp = datetime.now(timezone.utc).isoformat()
    applied = set(applied_ids)
    try:
        os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- Enrichment Batch Applied: {timestamp} ---\n")
            f.write(f"Source file: {source_label}\n")
            f.write(f"Applied nodes ({len(applied_ids)}): {', '.join(applied_ids)}\n")
            f.write(f"Bridge edges created: {bridges}\n")
            for item in items:
                nid = str(item.get("id") or "")
                if nid in applied:
                    f.write(f"  • [{nid}] intent:\n    {item.get('intent', '')}\n")
                    if item.get("input_fields"):
                        f.write(f"    input_fields: {item.get('input_fields')}\n")
                    if item.get("output_fields"):
                        f.write(f"    output_fields: {item.get('output_fields')}\n")
                    elif item.get("fields"):
                        f.write(f"    fields: {item.get('fields')}\n")
                    if item.get("calls"):
                        f.write(f"    calls: {item.get('calls')}\n")
            if unresolved:
                f.write(f"Unresolved call targets: {', '.join(unresolved)}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Automatic agent enrichment
# --------------------------------------------------------------------------- #

#: Prompt handed to a headless agent CLI for one enrichment batch.
AGENT_ENRICH_PROMPT = """You are enriching a code architecture graph for the repository you currently have open.

For each node listed below, OPEN its source file at the given path and read the
actual implementation before writing anything. An intent paraphrased from the
symbol name is worse than none: it poisons semantic search with confident noise.

{instructions}

Return ONLY a JSON array, no prose and no markdown fence, of this shape:

[
  {{
    "id": "<node id copied verbatim>",
    "intent": "What this symbol does, why it exists, and its execution logic. Markdown allowed.",
    "input_fields": ["argument", "payloadField"],
    "output_fields": ["returnedField", "emittedEvent"],
    "calls": ["DownstreamService", "src/services/calc.ts:calculate", "some_table"]
  }}
]

Include every id exactly once. If a file is unreadable or the symbol is trivial,
still return the id with a short honest intent and empty field/call lists.

Repository root: {root}

Nodes ({count}):
{nodes}
"""


def build_agent_enrichment_prompt(root: str, batch: List[Dict[str, Any]]) -> str:
    """Renders the headless-agent prompt for one enrichment batch."""
    return AGENT_ENRICH_PROMPT.format(
        instructions="\n".join(f"- {line}" for line in _enrichment_instructions()),
        root=root,
        count=len(batch),
        nodes=json.dumps(batch, indent=2, default=str),
    )


def run_agent_enrichment(
    path: str,
    loader: GraphLoader,
    agent: Any,
    batch_size: int = 25,
    max_nodes: int = 0,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Drives the full enrich loop against a headless agent CLI until the backlog
    is empty, applying each batch before requesting the next.

    Progress is durable: every batch is merged, re-indexed and saved as it lands,
    so an interrupted run keeps everything it already earned.
    """
    root = os.path.abspath(path)
    totals = {"applied": 0, "bridges": 0, "unresolved": 0, "batches": 0, "failed_batches": 0}
    errors: List[str] = []
    processed = 0

    while True:
        degrees = compute_degrees(loader.graph)
        limit = batch_size
        if max_nodes:
            remaining_budget = max_nodes - processed
            if remaining_budget <= 0:
                break
            limit = min(batch_size, remaining_budget)

        request = build_enrichment_batch(path, loader, degrees, limit=limit, skip_cursor=True)
        batch = request["batch"]
        if not batch:
            break

        # Keep the request file current so an interrupted run leaves the host
        # agent a usable handoff instead of a stale batch.
        _write_payload(_state_path(path, REQUEST_FILENAME), request["payload"])

        click.echo(f"   🤖 Batch {totals['batches'] + 1}: {len(batch)} node(s) "
                   f"({request['remaining_after']} left after this)...")
        try:
            raw = agent_runner.run_agent_json(
                agent, build_agent_enrichment_prompt(root, batch), root, model=model
            )
        except agent_runner.AgentError as err:
            totals["failed_batches"] += 1
            errors.append(str(err))
            click.echo(f"   ⚠️  {err}")
            break

        items = coerce_enrichment_items(raw)
        if not items:
            totals["failed_batches"] += 1
            errors.append("agent returned no enrichment objects")
            click.echo("   ⚠️  Agent returned no enrichment objects; stopping the loop.")
            break

        batch_ids = {node["id"] for node in batch}
        stats = apply_enrichment_items(loader, path, items, f"agent:{agent.name}")
        totals["batches"] += 1
        totals["applied"] += len(stats["applied_ids"])
        totals["bridges"] += stats["bridges"]
        totals["unresolved"] += len(stats["unresolved"])
        processed += len(batch)

        if not stats["applied_ids"]:
            # Nothing landed: the agent is answering with ids we do not have.
            # Looping again would just repeat the same batch forever.
            errors.append("agent returned ids that are not in the graph")
            click.echo("   ⚠️  None of the returned ids matched the graph; stopping the loop.")
            break

        # Progress is measured by nodes leaving the candidate set, not by ids
        # echoed back. An answer with empty intents applies cleanly yet clears
        # nothing, and would otherwise re-request the same batch forever.
        cleared = sum(
            1 for nid in batch_ids
            if loader.graph.has_node(nid) and not needs_agent_enrichment(loader.graph.nodes[nid])
        )
        if not cleared:
            errors.append("agent answers left every node still un-enriched")
            click.echo("   ⚠️  That batch cleared no nodes (empty intents?); stopping the loop.")
            break

    totals["errors"] = errors
    return totals


def _snapshot_or_graph_nodes(root: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Node records for read-only commands. Prefers the persisted snapshot (a pure read);
    falls back to building the graph without enrichment when no snapshot exists yet.
    """
    loader = GraphLoader(root)
    snapshot = loader.load_graph_snapshot()
    if snapshot and isinstance(snapshot.get("nodes"), list):
        return [n for n in snapshot["nodes"] if isinstance(n, dict)], "snapshot"
    loader.load_or_extract(enrich_llm=False)
    return [dict(data, id=str(nid)) for nid, data in loader.graph.nodes(data=True)], "graph"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

@click.group()
def cli():
    """TLDRGraph: Token-Efficient Hybrid Code Flow & Semantic Navigation Engine (Dynamic Multi-Layer)"""
    pass


# --------------------------------------------------------------------------- #
# `tldrgraph init` -- the one command
# --------------------------------------------------------------------------- #

#: Statuses `init` can end on. Stable strings: agent command files and any
#: `--json` consumer branch on these.
STATUS_DONE = "done"
STATUS_NEEDS_LAYERS = "needs_layers"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_NEEDS_ENRICHMENT = "needs_enrichment"

#: Written by `init` after it merges a response, so the same answers are never
#: applied twice on the next run.
APPLIED_RESPONSE_FILENAME = "enrichment_response.applied.yaml"


@contextlib.contextmanager
def _stdout_to_stderr_if(active: bool):
    """Redirects stdout to stderr while ``active``, so --json stays parseable."""
    if not active:
        yield
        return
    import sys
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _stdin_is_interactive() -> bool:
    """True when there is a real terminal to prompt on."""
    try:
        import sys
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _emit_status(status: str, phase: str, lines: List[str],
                 progress: Optional[Dict[str, Any]] = None,
                 as_json: bool = False) -> None:
    """
    Prints the block an agent reads to decide what to do next.

    The format is deliberately dull and greppable: every agent tool, whatever
    its prompt conventions, can find `status:` and follow the numbered steps.
    """
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
    if status == STATUS_DONE:
        click.echo("TLDRGRAPH INIT — COMPLETE")
    else:
        click.echo("TLDRGRAPH INIT — NEXT ACTION REQUIRED")
    click.echo(f"status: {status}")
    click.echo(rule)
    for line in lines:
        click.echo(line)
    click.echo(rule + "\n")


def _apply_pending_layer_response(path: str) -> Optional[str]:
    """
    Applies .tldrgraph/propose_layers_response.json if the agent has written one.

    Part of the one-command promise: the agent writes the file and runs `init`
    again, rather than having to remember `tldrgraph apply-layers`.
    """
    for filename in (PROPOSE_RESPONSE_FILENAME, "propose_layers_response.yaml"):
        candidate = _state_path(path, filename)
        if os.path.isfile(candidate):
            return apply_proposed_layers(path, candidate)
    return None


def _apply_pending_enrichment_response(path: str, loader: GraphLoader) -> Optional[Dict[str, Any]]:
    """
    Merges .tldrgraph/enrichment_response.yaml if the agent has written one,
    then renames it so a later `init` cannot apply the same answers twice.

    Returns the apply stats, or None when there was nothing to apply.
    """
    source = None
    for filename in (RESPONSE_FILENAME, LEGACY_RESPONSE_FILENAME,
                     "pending_enrichment.yaml", LEGACY_FILENAME):
        candidate = _state_path(path, filename)
        if os.path.isfile(candidate):
            source = candidate
            break
    if not source:
        return None

    items = coerce_enrichment_items(_read_payload(source))
    if not items:
        return None

    stats = apply_enrichment_items(loader, path, items, source)
    try:
        os.replace(source, _state_path(path, APPLIED_RESPONSE_FILENAME))
    except OSError:
        pass
    return stats


def _init_pipeline(path: str, assume_yes: bool, batch_size: int, max_nodes: int,
                   rebuild: bool, relayer: bool, agent_cli: bool, agent_model: Optional[str],
                   embeddings: Optional[str], as_json: bool) -> str:
    """
    Layers, extraction and enrichment in one resumable pass.

    Every deterministic step runs here; the moment judgement is needed that only
    an agent can supply, this stops and prints what to do. Re-running picks up
    exactly where it left off, so the whole workflow is `init`, act, `init`,
    act, `init`. Returns the terminal status.
    """
    root = os.path.abspath(path)

    if not as_json:
        click.echo(f"🔄 [TLDRGraph] {root}")

    # 0. Make the repo agent-ready. Idempotent: unchanged files are not touched.
    ensure_gitignore(path)
    install_agent_rules(path)

    # 1. Extraction first, so the layer evidence carries real symbols rather
    #    than a directory listing -- and so we can size the job up front.
    #
    #    This runs EVERY time, not just when the export is missing. Reusing an
    #    existing export means every later phase works from whatever the code
    #    looked like when it was written: an agent gets handed deleted functions
    #    to describe, and code added since is invisible. graphify caches per file
    #    by content hash, so a re-run with nothing changed is nearly free.
    loader = GraphLoader(path, embeddings=embeddings)
    if not as_json:
        click.echo("📦 Extracting AST with graphify...")
    # graphify writes progress and warnings to stdout. Under --json that would
    # sit in front of the payload and make it unparseable, so it goes to stderr,
    # where a human still sees it and a parser does not.
    with _stdout_to_stderr_if(as_json):
        loader._run_graphify()
    loader.file_hashes = loader._load_file_hashes()

    # 2. Layers. No template, no fallback: either they exist, or we ask.
    applied_cfg = None
    if relayer or not config_path(root):
        applied_cfg = _apply_pending_layer_response(path)
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
        _emit_status(STATUS_NEEDS_LAYERS, "layers", [
            "This repository has no architectural layer set, and TLDRGraph will",
            "not invent one from a template. Design it from the code:",
            "",
            f"  1. Read {os.path.relpath(request_path, root)}",
            "  2. OPEN real source files -- entry points first, then one file from",
            "     each cluster in the evidence. Do not skip this step.",
            f"  3. Write {os.path.join(STATE_DIR, PROPOSE_RESPONSE_FILENAME)} with",
            '     {"utility_id": "...", "layers": [{id, name, order, description, rules}]}',
            "     3-6 layers plus one catch-all whose id equals utility_id and whose",
            "     rules are []. Name them after THIS repository's concepts.",
            "  4. Run: tldrgraph init",
        ], as_json=as_json)
        return STATUS_NEEDS_LAYERS

    if not as_json:
        if applied_cfg:
            label = "designed by your agent"
        elif source == "existing_config":
            label = "already configured"
        else:
            label = source
        click.echo(f"🏛️  {len(registry)} architectural layers ({label})")

    # 3. Full build: classify, index, persist.
    graph = loader.load_or_extract(rebuild=rebuild)
    _stamp_degrees(loader)
    snapshot_path = loader.save_graph()
    loader.export_yaml()

    if not as_json:
        click.echo(f"✅ {graph.number_of_nodes()} nodes, {graph.number_of_edges()} relationships")
        diag = loader.vector_store.diagnostics()
        click.echo(f"🔎 Retrieval: {diag['backend']} (floor {diag['score_floor']})")
        click.echo(f"💾 {snapshot_path}")

    # 4. Enrichment. Apply anything the agent already answered, then either
    #    finish, ask permission, or hand out the next batch.
    applied = _apply_pending_enrichment_response(path, loader)
    if applied and not as_json:
        click.echo(f"🧠 Applied {len(applied['applied_ids'])} enrichment(s), "
                   f"{applied['bridges']} bridge edge(s)")
        # An id that is not in the graph is silently worthless, and an agent
        # that invented one will keep inventing it. Say so, with examples.
        if applied["unknown_ids"]:
            preview = ", ".join(applied["unknown_ids"][:4])
            click.echo(f"   ⚠️  {len(applied['unknown_ids'])} id(s) are not in the graph "
                       f"and were dropped: {preview}")
            click.echo("      Copy ids verbatim from the request; do not construct them.")
        if applied["unresolved"]:
            preview = ", ".join(sorted(set(applied["unresolved"]))[:4])
            click.echo(f"   ⚠️  {len(applied['unresolved'])} call target(s) matched nothing "
                       f"above the score floor: {preview}")

    generate_visualizer_html(path)

    candidates = _enrichment_candidates(loader, compute_degrees(loader.graph))
    total = loader.graph.number_of_nodes()
    # Counting `total - candidates` as "enriched" would fold in every node that
    # was never eligible -- the utility bucket and graphify's prose nodes -- and
    # report a large number before a single intent had been written.
    enriched = sum(
        1 for _, d in loader.graph.nodes(data=True)
        if (d.get("enrichment_source") or "") == AGENT_ENRICHMENT_SOURCE
    )
    excluded = total - enriched - len(candidates)

    if not candidates:
        _emit_status(STATUS_DONE, "enrichment", [
            f"{total} nodes across {len(registry)} layers. {enriched} enriched from "
            f"source; {excluded} not eligible (utility bucket and prose nodes).",
            "",
            '  tldrgraph query "<feature in plain English>"',
            '  tldrgraph trace "<Source>" "<Target>"',
            "  tldrgraph layers",
            "  tldrgraph ui --serve",
        ], progress={"total_nodes": total, "enriched": enriched, "remaining": 0},
            as_json=as_json)
        return STATUS_DONE

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
        if _stdin_is_interactive():
            click.echo(f"\n🧠 {len(candidates)} node(s) need an intent read from the source "
                       f"({rounds} batch(es) of {batch_size}).")
            if click.confirm("   Enrich now?", default=True):
                assume_yes = True
            else:
                click.echo("   Skipped. Run `tldrgraph init` again when ready.")
                return STATUS_NEEDS_CONFIRMATION
        else:
            _emit_status(STATUS_NEEDS_CONFIRMATION, "enrichment", [
                f"The graph is built and queryable: {total} nodes, {enriched} enriched "
                f"from source, {excluded} not eligible (utility bucket, prose nodes).",
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

    # 5. Enrich: drive an agent CLI if one was opted into, else hand off.
    if agent_cli:
        agent = agent_runner.find_agent_cli()
        if agent is not None:
            if not as_json:
                click.echo(f"\n🤖 Enriching via {agent.display}...")
            totals = run_agent_enrichment(path, loader, agent, batch_size=batch_size,
                                          max_nodes=max_nodes, model=agent_model)
            remaining = len(_enrichment_candidates(loader, compute_degrees(loader.graph)))
            status = STATUS_DONE if not remaining else STATUS_NEEDS_ENRICHMENT
            _emit_status(status, "enrichment", [
                f"Enriched {totals['applied']} node(s) in {totals['batches']} batch(es); "
                f"{totals['bridges']} bridge edge(s).",
                f"{remaining} still un-enriched."
                if remaining else "Nothing left to enrich.",
            ] + (["Run `tldrgraph init --yes` to continue."] if remaining else []),
                progress={**progress, "remaining": remaining}, as_json=as_json)
            return status
        if not as_json:
            click.echo(f"   ℹ️  No agent CLI available "
                       f"({agent_runner.agent_status()['detail']}); handing off instead.")

    request = build_enrichment_batch(
        path, loader, compute_degrees(loader.graph),
        limit=batch_size if not max_nodes else min(batch_size, max_nodes),
        skip_cursor=True,
    )
    request_path = _write_payload(_state_path(path, REQUEST_FILENAME), request["payload"])

    _emit_status(STATUS_NEEDS_ENRICHMENT, "enrichment", [
        f"{len(request['batch'])} node(s) queued, {len(candidates)} remaining overall.",
        "",
        f"  1. Read {os.path.relpath(request_path, root)}",
        "  2. OPEN the source file of every node in it. An intent guessed from a",
        "     symbol name is worse than none -- it poisons semantic search.",
        f"  3. Write {os.path.join(STATE_DIR, RESPONSE_FILENAME)} as a YAML list of",
        "     {id, intent, input_fields, output_fields, calls}. Never invent",
        "     fields or calls; omit what you cannot verify.",
        "  4. Run: tldrgraph init --yes",
        "",
        "Repeat until this says status: done.",
    ], progress=progress, as_json=as_json)
    return STATUS_NEEDS_ENRICHMENT


_init_options = [
    click.argument("path", default=".", type=click.Path(exists=True)),
    click.option("--yes", "-y", "assume_yes", is_flag=True,
                 help="Proceed with enrichment without asking (agents: only after the user agrees)"),
    click.option("--batch", "batch_size", default=25, show_default=True,
                 help="Nodes handed to the agent per round"),
    click.option("--limit", "max_nodes", default=0, show_default=True,
                 help="Cap on nodes to enrich this run. 0 enriches every candidate."),
    click.option("--rebuild", is_flag=True, help="Re-extract and rebuild enrichment from scratch"),
    click.option("--relayer", is_flag=True, help="Discard the layer set and design it again"),
    click.option("--agent-cli", is_flag=True,
                 help="Shell out to an agent CLI (claude/cursor-agent/gemini) instead of "
                      "handing off. Off by default: agent CLIs differ per tool and can hang."),
    click.option("--agent-model", default=None,
                 help="Model for --agent-cli (e.g. opus, sonnet, gemini-2.5-pro). Defaults "
                      "to $TLDRGRAPH_AGENT_MODEL. Ignored on the handshake path, where your "
                      "own agent session picks the model."),
    click.option("--json", "as_json", is_flag=True, help="Emit machine-readable status"),
    embeddings_option,
]


def _with_init_options(fn):
    for option in reversed(_init_options):
        fn = option(fn)
    return fn


@cli.command()
@_with_init_options
def init(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """
    Build this repository's graph: layers, extraction, and enrichment, in one command.

    Resumable. Run it, do whatever the NEXT ACTION block says, run it again.
    Layers are always designed from your code -- there is no template fallback.
    """
    _init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer,
                   agent_cli, agent_model, embeddings, as_json)


@cli.command()
@_with_init_options
def scan(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """Alias for `init`, kept for existing scripts and agent rules."""
    _init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer,
                   agent_cli, agent_model, embeddings, as_json)


@cli.command()
@_with_init_options
def enrich(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """Alias for `init`, which already resumes enrichment where it left off."""
    _init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer,
                   agent_cli, agent_model, embeddings, as_json)


@cli.command(name="ui")
@click.option("--path", default=".", help="Repository root path")
@click.option("--serve", is_flag=True, help="Serve the repo locally so the visualizer can read source files")
@click.option("--port", default=8777, help="Port for --serve")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the visualizer in a browser (with --serve)")
def visualizer_cmd(path, serve, port, open_browser):
    """Generate and view interactive standalone HTML visualizer (.tldrgraph/TLDRGRAPH_VISUALIZER.html)."""
    html_path = generate_visualizer_html(path)
    click.echo(f"\n🌐 [TLDRGraph Visualizer]: {os.path.abspath(html_path)}")

    if not serve:
        click.echo("Open this file in any web browser to explore all architectural layers and cross-layer connections interactively!")
        click.echo("Source code is read live: use 'Connect project' in the page, or rerun with --serve to skip the prompt.\n")
        return

    serve_visualizer(path, html_path, port, open_browser)


def serve_visualizer(path: str, html_path: str, port: int, open_browser: bool = True) -> None:
    """
    Serves the repository root over localhost so the visualizer can fetch source
    files directly. Read-only, bound to the loopback interface.
    """
    import functools
    import http.server
    import socketserver
    import threading
    import webbrowser

    root = os.path.abspath(path)
    rel_html = os.path.relpath(os.path.abspath(html_path), root).replace(os.sep, "/")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass

    handler = functools.partial(QuietHandler, directory=root)
    socketserver.TCPServer.allow_reuse_address = True

    try:
        httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise click.ClickException(
            f"Could not bind port {port}: {exc}. Pick another with --port."
        )

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


@cli.command()
@click.argument("query_text")
@click.option("--top-k", default=3, help="Number of flow candidates to return")
@click.option("--path", default=".", help="Repository root path")
@embeddings_option
def query(query_text, top_k, path, embeddings):
    """Hybrid search + trace end-to-end multi-layer execution flows. Read-only: never enriches."""
    loader = GraphLoader(path, embeddings=embeddings)
    graph = loader.load_or_extract(enrich_llm=False)
    engine = FlowEngine(graph, loader.vector_store, root_dir=path)

    results = engine.query_flow(query_text, top_k=top_k)
    if not results:
        click.echo(f"❌ No matching flows found for: '{query_text}'")
        return

    # Export to flows.yaml
    yaml_file = engine.export_flows_yaml(results)
    click.echo(f"\n🔍 [TLDRGraph Flow Query]: '{query_text}'")
    click.echo(f"💾 Saved flow paths in YAML: {yaml_file}\n")

    for i, res in enumerate(results, 1):
        click.echo(f"━━━ [Option {i}] Root: {res['root_node']} ({res['layer']}) (Score: {res['match_score']}) ━━━")
        table = engine.render_markdown_table(res["flow"])
        click.echo(table)
        click.echo()


@cli.command()
@click.argument("source")
@click.argument("target", required=False)
@click.option("--path", default=".", help="Repository root path")
def trace(source, target, path):
    """Trace exact execution path between two symbols across layers. Read-only: never enriches."""
    loader = GraphLoader(path)
    graph = loader.load_or_extract(enrich_llm=False)
    engine = FlowEngine(graph, loader.vector_store, root_dir=path)

    res = engine.trace_path(source, target)
    if "error" in res:
        click.echo(f"❌ {res['error']}")
        return

    click.echo(f"\n🔄 [TLDRGraph Trace]: '{res.get('source')}' ➔ '{res.get('target', 'downstream')}'")
    table = engine.render_markdown_table(res.get("steps", []))
    click.echo(table)
    click.echo()


@cli.command()
@click.option("--path", default=".", help="Repository root path")
def layers(path):
    """View node count summary across all architectural layers. Read-only: never enriches."""
    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)

    click.echo("\n🏛️  TLDRGraph Multi-Layer Architecture Summary:\n")
    for layer, nodes in loader.nodes_by_layer.items():
        click.echo(f"  • {layer.ljust(35)} : {len(nodes)} nodes")
    click.echo(f"\nTotal Nodes Mapped: {loader.graph.number_of_nodes()}")


@cli.command("queue-enrichment")
@click.option("--path", default=".", help="Repository root path")
@click.option("--limit", default=50, show_default=True,
              help="Maximum nodes to queue in this batch. 0 queues every remaining candidate.")
@click.option("--requeue", is_flag=True,
              help="Also re-queue ids handed out earlier but never applied (abandoned batches).")
@click.option("--reset", is_flag=True,
              help="Forget all queue progress and start again from the highest-priority node.")
def queue_enrichment(path, limit, requeue, reset):
    """
    Queue the highest-value un-enriched nodes for the coding agent.

    Writes .tldrgraph/enrichment_request.yaml. The agent reads the source files and
    writes its answer to a DIFFERENT file, .tldrgraph/enrichment_response.yaml, which
    `apply-enrichment` then merges. Running this twice advances through the backlog
    instead of repeating the same nodes.
    """
    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)
    degrees = _stamp_degrees(loader)
    loader.save_graph()

    request = build_enrichment_batch(
        path, loader, degrees, limit=limit, requeue=requeue, reset=reset
    )
    batch = request["batch"]
    cursor = request["cursor"]
    total_candidates = request["total_candidates"]
    already_enriched = request["already_enriched"]
    remaining_after = request["remaining_after"]

    request_path = _state_path(path, REQUEST_FILENAME)
    response_path = _state_path(path, RESPONSE_FILENAME)
    _write_payload(request_path, request["payload"])

    cursor_path = _write_cursor(path, cursor["queued"] + [n["id"] for n in batch], cursor["applied"])

    click.echo(f"📋 Queued {len(batch)} node(s) in {request_path}")
    click.echo(f"   Ordering: cross-layer seams first, then hub degree (in+out), then id.")
    if batch:
        top = batch[0]
        click.echo(f"   Top of queue: {top['label']} "
                   f"(degree {top['degree']}, cross-layer {top['cross_layer_degree']})")
    click.echo(f"   Progress: {already_enriched} enriched • {len(batch)} in this batch • "
               f"{remaining_after} remaining of {total_candidates} candidates")
    click.echo(f"   Cursor: {cursor_path}")
    if not batch:
        click.echo("   Nothing left to queue. Use --requeue for abandoned batches, or --reset to start over.")
        return
    click.echo(f"\n👉 Write the response to {response_path}, then run `tldrgraph apply-enrichment`.")


@cli.command("apply-enrichment")
@click.argument("enrichment_file", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--path", default=".", help="Repository root path")
def apply_enrichment(enrichment_file, path):
    """
    Apply the agent's enrichment response into the graph, SQLite cache and vector index.

    With no argument, reads .tldrgraph/enrichment_response.yaml (or .json), falling back to
    legacy response files when present.
    """
    if not enrichment_file:
        for filename in (RESPONSE_FILENAME, LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
            candidate = _state_path(path, filename)
            if os.path.isfile(candidate):
                enrichment_file = candidate
                if filename in (LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
                    click.echo(f"ℹ️  Reading enrichment from {filename}.")
                break
    if not enrichment_file:
        raise click.ClickException(
            f"No enrichment response found. Expected {_state_path(path, RESPONSE_FILENAME)} "
            f"(or {_state_path(path, LEGACY_RESPONSE_FILENAME)}). "
            "Run `tldrgraph queue-enrichment` first."
        )

    raw = _read_payload(enrichment_file)
    if raw is None:
        raise click.ClickException(f"Could not parse payload from {enrichment_file}")

    for req_fn in (REQUEST_FILENAME, LEGACY_REQUEST_FILENAME):
        request_path = _state_path(path, req_fn)
        if os.path.abspath(enrichment_file) == os.path.abspath(request_path):
            raise click.ClickException(
                f"{request_path} is the request, not the response. Write the agent's answer to "
                f"{_state_path(path, RESPONSE_FILENAME)} instead - the request is regenerated "
                "on every `queue-enrichment` run."
            )

    items = coerce_enrichment_items(raw)
    if not items:
        raise click.ClickException(
            f"{enrichment_file} contains no enrichment objects. Expected a list of "
            "{id, intent, fields, calls}."
        )

    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)

    stats = apply_enrichment_items(loader, path, items, enrichment_file)
    applied_ids = stats["applied_ids"]
    unknown_ids = stats["unknown_ids"]
    unresolved = stats["unresolved"]

    still_pending = sum(1 for _, d in loader.graph.nodes(data=True) if needs_agent_enrichment(d))

    click.echo(f"✅ Applied {len(applied_ids)} enrichment(s) from {enrichment_file}")
    click.echo(f"🔗 Created {stats['bridges']} cross-layer bridge edge(s) "
               f"(score floor {stats['floor']}, backend {loader.vector_store.backend})")
    if unresolved:
        preview = ", ".join(sorted(set(unresolved))[:6])
        click.echo(f"⚠️  {len(unresolved)} call target(s) below the score floor / unmatched: {preview}")
    if unknown_ids:
        preview = ", ".join(unknown_ids[:3])
        click.echo(f"⚠️  {len(unknown_ids)} id(s) not in the graph, skipped: {preview}")
    click.echo(f"💾 Graph snapshot updated at: {stats['snapshot_path']}")
    click.echo(f"📊 {still_pending} candidate(s) still un-enriched. Run `tldrgraph queue-enrichment` for the next batch.")


@cli.command("dead-code")
@click.option("--path", default=".", help="Repository root path")
@click.option("--status", "status", default="candidate", show_default=True,
              type=click.Choice(list(DEAD_CODE_STATUSES) + ["all"], case_sensitive=False),
              help="Which review status to list. 'candidate' = nothing observed reaches it "
                   "(evidence, not proof). 'unreviewed' = not enough evidence to conclude, "
                   "never treat as removable.")
@click.option("--limit", default=0, help="Max rows to print. 0 shows all.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for agent consumption.")
def dead_code(path, status, limit, as_json):
    """
    List nodes by reachability review status - REVIEW CANDIDATES, NOT CONFIRMED DEAD CODE.

    Static analysis cannot see reflection, DI containers, string-built routes or template
    references, so a 'candidate' is a node worth a human or agent review, not a node that
    is safe to delete. 'unreviewed' explicitly means there was not enough evidence to
    conclude anything. This command never deletes and never proposes deletion.
    """
    status = status.lower()
    nodes, source = _snapshot_or_graph_nodes(path)

    annotated = [n for n in nodes if n.get("dead_code_status")]
    if not annotated:
        message = (
            "No reachability review data yet: no node carries a 'dead_code_status'. "
            "Re-run `tldrgraph scan .` with a build that computes it."
        )
        if as_json:
            click.echo(json.dumps({
                "status": status,
                "source": source,
                "available": False,
                "note": message,
                "count": 0,
                "nodes": [],
            }, indent=2))
        else:
            click.echo(f"ℹ️  {message}")
        return

    selected = [
        n for n in annotated
        if status == "all" or str(n.get("dead_code_status", "")).lower() == status
    ]
    selected.sort(key=lambda n: (
        str(n.get("layer", "")),
        str(n.get("file", "")),
        str(n.get("label", n.get("id", ""))),
    ))
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
        click.echo(json.dumps({
            "status": status,
            "source": source,
            "available": True,
            "disclaimer": "Review candidates, not confirmed dead code. "
                          "'unreviewed' means insufficient evidence and is never removable. "
                          "Verify in the source before removing anything.",
            "count": len(rows),
            "nodes": rows,
        }, indent=2))
        return

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
    click.echo("\n👉 Hand this list to an agent to verify against the source. "
               "TLDRGraph will not delete anything for you.")


def _fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


@cli.command()
@click.option("--path", default=".", help="Repository root path")
@embeddings_option
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for agent consumption.")
def doctor(path, embeddings, as_json):
    """
    Report which retrieval backend is ACTUALLY live, and why.

    This command exists so TLDRGraph can never again claim a capability it does
    not have. Everything printed here is verified at run time: the model is
    looked for on disk, the embedder is actually constructed, and the embedding
    coverage is counted off the loaded index rather than assumed.
    """
    store = vs_mod.LocalVectorStore(
        os.path.join(path, STATE_DIR, "vector_index.json"),
        embeddings=embeddings,
    )
    d = store.diagnostics()

    if as_json:
        click.echo(json.dumps(d, indent=2, default=str))
        return

    backend_note = {
        vs_mod.BACKEND_TFIDF: "TF-IDF cosine only — lexical / exact-identifier retrieval. "
                              "No model, no ONNX, no network.",
        vs_mod.BACKEND_HYBRID: "TF-IDF + dense ONNX embeddings, fused. Lexical retrieval "
                               "is preserved; dense adds natural-language intent matching.",
    }[d["backend"]]

    click.echo("\n🩺 TLDRGraph doctor\n")
    click.echo(f"  Retrieval backend   : {d['backend'].upper()}")
    click.echo(f"                        {backend_note}")
    click.echo(f"  Policy              : {d['policy']}  (${d['policy_env_var']}, "
               f"or --embeddings off|auto|on)")
    click.echo(f"  Bridge score floor  : {d['score_floor']}   "
               f"(per backend: {d['score_floors']})")
    if d["backend"] == vs_mod.BACKEND_HYBRID:
        f = d["fusion"]
        span = round(1 - f["dense_baseline"], 2)
        click.echo(f"  Fusion              : (1-w)*tfidf + w*clamp((cos - "
                   f"{f['dense_baseline']}) / {span}, 0, 1)")
        click.echo(f"                        w = {f['dense_weight_identifier']} for identifier "
                   f"queries (< {f['prose_min_words']} words), "
                   f"{f['dense_weight_prose']} for prose")

    click.echo("\n  Embedding model")
    fe = d["fastembed_version"] or "NOT INSTALLED  (pip install 'codechakra[embeddings]')"
    click.echo(f"    fastembed         : {fe}")
    click.echo(f"    model             : {d['model_name']}"
               f"{'  [' + d['model_repo'] + ']' if d['model_repo'] else ''}")
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
        click.echo(f"    size              : {_fmt_bytes(d['index_bytes'])}")
        stale = d["index_format_version"] != d["expected_format_version"]
        click.echo(f"    format version    : {d['index_format_version']} "
                   f"(expected {d['expected_format_version']})"
                   f"{'  ← STALE, will be rebuilt on next scan' if stale else ''}")
    click.echo(f"    documents         : {d['document_count']}")
    coverage = d["embedding_coverage"]
    total = d["document_count"] or 0
    pct = f"{(100.0 * coverage / total):.1f}%" if total else "n/a"
    click.echo(f"    embedding coverage: {coverage}/{total} ({pct})")
    click.echo(f"    vector sidecar    : "
               f"{d['embeddings_sidecar'] if d['embeddings_sidecar_exists'] else '(none)'}"
               f"{'  ' + _fmt_bytes(d['embeddings_sidecar_bytes']) if d['embeddings_sidecar_exists'] else ''}")

    click.echo()
    if d["backend"] == vs_mod.BACKEND_TFIDF and d["policy"] == vs_mod.POLICY_OFF:
        click.echo("👉 Dense embeddings are OFF by default. Enable with "
                   "`--embeddings auto` (uses a cached model, never downloads) "
                   "or `--embeddings on` (permits a one-time ~67 MB download).")
    elif d["backend"] == vs_mod.BACKEND_TFIDF:
        click.echo("👉 Dense embeddings were requested but are NOT live — see the reason "
                   "above. Search is running on TF-IDF alone.")
    else:
        click.echo("👉 Hybrid retrieval is live. Bridge resolution uses the hybrid floor "
                   f"({d['score_floor']}), not the TF-IDF one.")
    click.echo()


@cli.command()
@click.option("--path", default=".", help="Repository root path")
@click.option("--all-agents", is_flag=True,
              help="Write the /tldrgraph-init command for every agent tool TLDRGraph "
                   "knows, not just the ones this repo shows signs of using.")
def install(path, all_agents):
    """
    Install TLDRGraph agent rules for Claude Code, Cursor and Antigravity.

    Also adds a managed .gitignore block: generated state under .tldrgraph/ is
    ignored, while AGENT_CONTRACT.md and layers.config.yaml stay committable so
    the whole team shares one architecture map.
    """
    gitignore = ensure_gitignore(path)
    res = install_agent_rules(path, all_agents=all_agents)
    click.echo("✅ TLDRGraph agent skills & rules installed successfully:")
    for k, v in res.items():
        # Reported separately below, with its status.
        if k == "gitignore":
            continue
        click.echo(f"  • {k}: {v}")
    click.echo(f"  • gitignore: {gitignore['path']} ({gitignore['status']})")
    click.echo("\n💡 Your agent can now run /tldrgraph-init (or just `tldrgraph init`) "
               "to build the whole graph.")

    for warning in gitignore_warnings(path):
        click.echo(f"⚠️  {warning}")


@cli.command("propose-layers")
@click.option("--path", default=".", help="Repository root path")
@click.option("--auto", is_flag=True,
              help="Try to synthesize the layer set now via an agent CLI or a configured LLM")
@click.option("--force", is_flag=True, help="Force overwrite an existing layers.config.yaml")
def propose_layers_cmd(path, auto, force):
    """
    Write the layer-proposal request for the agent, or try to synthesize it now.

    `tldrgraph init` calls this for you. Reach for it directly only to re-open the
    architecture question without rebuilding anything else.
    """
    if auto:
        reg, out_path, source = auto_configure_layers(path, force=force, use_agent=True)
        if reg is not None:
            click.echo(f"✅ Configured {len(reg)} architectural layers ({source}) in {out_path}")
            click.echo("🔄 Run `tldrgraph init` to reclassify nodes with the new layer set.")
            return
        click.echo("ℹ️  Nothing could design the layers automatically, and TLDRGraph "
                   "has no template to fall back on.")

    req_path = generate_propose_request(path)
    click.echo(f"📋 Queued layer proposal request in {req_path}")
    resp_rel = os.path.join(STATE_DIR, PROPOSE_RESPONSE_FILENAME)
    click.echo(f"👉 Read it, READ THE SOURCE, write {resp_rel}, then run `tldrgraph init`.")


@cli.command("apply-layers")
@click.argument("response_file", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--path", default=".", help="Repository root path")
def apply_layers_cmd(response_file, path):
    """Validate and apply proposed architectural layers into .tldrgraph/layers.config.yaml."""
    try:
        out_path = apply_proposed_layers(path, response_file)
        click.echo(f"✅ Applied and validated architectural layer set in {out_path}")
        click.echo("🔄 Run `tldrgraph scan .` to reclassify nodes with the new layer set.")
    except Exception as err:
        raise click.ClickException(str(err))


def main():
    cli()


if __name__ == "__main__":
    main()
