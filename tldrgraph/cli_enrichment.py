"""
Enrichment batching, application, and audit logging for TLDRGraph.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

from . import paths
from .graph_loader import (
    BRIDGE_SCORE_FLOOR,
    GraphLoader,
    bridge_score_floor,
    resolve_call_target,
)
from .layers import get_registry, layer_id_of

try:
    from .deadcode import HEURISTIC_ENRICHMENT_SOURCE, NON_CODE_NODE_TYPES
except ImportError:
    HEURISTIC_ENRICHMENT_SOURCE = "heuristic"
    NON_CODE_NODE_TYPES = {"rationale", "concept", "doc", "documentation"}

AGENT_ENRICHMENT_SOURCE = "agent"
STATE_DIR = ".tldrgraph"
REQUEST_FILENAME = "enrichment_request.yaml"
LEGACY_REQUEST_FILENAME = "enrichment_request.json"
RESPONSE_FILENAME = "enrichment_response.yaml"
LEGACY_RESPONSE_FILENAME = "enrichment_response.json"
LEGACY_FILENAME = "pending_enrichment.json"
CURSOR_FILENAME = "enrichment_cursor.json"
AUDIT_LOG_FILENAME = "enrichment_audit.log"


def state_path(root: str, filename: str) -> str:
    return os.path.join(root, STATE_DIR, filename)


def read_payload(path: str) -> Any:
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


def write_payload(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if path.endswith(".json"):
            json.dump(payload, f, indent=2)
        else:
            yaml.dump(payload, f, default_flow_style=False, sort_keys=False)
    return path


def coerce_enrichment_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("enrichments", "nodes", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def read_cursor(root: str) -> Dict[str, List[str]]:
    data = read_payload(state_path(root, CURSOR_FILENAME))
    if not isinstance(data, dict):
        data = {}
    queued = data.get("queued")
    applied = data.get("applied")
    return {
        "queued": [str(x) for x in queued] if isinstance(queued, list) else [],
        "applied": [str(x) for x in applied] if isinstance(applied, list) else [],
    }


def write_cursor(root: str, queued: List[str], applied: List[str]) -> str:
    return write_payload(state_path(root, CURSOR_FILENAME), {
        "schema": "codechakra/enrichment-cursor@1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "queued": sorted(set(queued)),
        "applied": sorted(set(applied)),
    })


def compute_degrees(graph: Any) -> Dict[str, Tuple[int, int]]:
    layers = {nid: layer_id_of(data) for nid, data in graph.nodes(data=True)}
    result: Dict[str, Tuple[int, int]] = {}
    for nid in graph.nodes():
        neighbours = set(graph.successors(nid)) | set(graph.predecessors(nid))
        own_layer = layers.get(nid, "")
        cross = sum(1 for n in neighbours if layers.get(n, "") != own_layer)
        result[nid] = (graph.in_degree(nid) + graph.out_degree(nid), cross)
    return result


def stamp_degrees(loader: GraphLoader) -> Dict[str, Tuple[int, int]]:
    degrees = compute_degrees(loader.graph)
    for nid, (degree, cross) in degrees.items():
        node = loader.graph.nodes[nid]
        node["degree"] = degree
        node["cross_layer_degree"] = cross
    return degrees


def needs_agent_enrichment(data: Dict[str, Any]) -> bool:
    if layer_id_of(data) == get_registry().utility_id:
        return False
    if str(data.get("type") or "").lower() in NON_CODE_NODE_TYPES:
        return False
    source = data.get("enrichment_source") or ""
    if source == AGENT_ENRICHMENT_SOURCE:
        return False
    return True


def enrichment_candidates(loader: GraphLoader, degrees: Dict[str, Tuple[int, int]]) -> List[Dict[str, Any]]:
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


def enrichment_instructions() -> List[str]:
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


def _resolve_batch_skip_set(path: str, cursor: Dict[str, List[str]], requeue: bool) -> Set[str]:
    answered = set(cursor["applied"])
    for filename in (RESPONSE_FILENAME, LEGACY_RESPONSE_FILENAME, "pending_enrichment.yaml", LEGACY_FILENAME):
        for item in coerce_enrichment_items(read_payload(state_path(path, filename))):
            if item.get("id"):
                answered.add(str(item["id"]))
    skip = set(answered)
    if not requeue:
        skip |= set(cursor["queued"])
    return skip


def _build_request_payload(
    batch: List[Dict[str, Any]],
    total_candidates: int,
    already_enriched: int,
    remaining_after: int,
) -> Dict[str, Any]:
    return {
        "schema": "codechakra/enrichment-request@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": os.path.join(STATE_DIR, RESPONSE_FILENAME),
        "contract": os.path.join(STATE_DIR, "AGENT_CONTRACT.md"),
        "instructions": enrichment_instructions() + [
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


def build_enrichment_batch(
    path: str,
    loader: GraphLoader,
    degrees: Dict[str, Tuple[int, int]],
    limit: int,
    requeue: bool = False,
    reset: bool = False,
    skip_cursor: bool = False,
) -> Dict[str, Any]:
    cursor = {"queued": [], "applied": []} if reset else read_cursor(path)
    skip = set() if skip_cursor else _resolve_batch_skip_set(path, cursor, requeue)

    candidates = enrichment_candidates(loader, degrees)
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
    payload = _build_request_payload(batch, total_candidates, already_enriched, remaining_after)

    return {
        "payload": payload,
        "batch": batch,
        "cursor": cursor,
        "total_candidates": total_candidates,
        "already_enriched": already_enriched,
        "remaining_after": remaining_after,
    }


def _update_node_fields(node_data: Dict[str, Any], item: Dict[str, Any]) -> None:
    input_fields = item.get("input_fields", []) or []
    output_fields = item.get("output_fields", []) or []
    legacy_fields = item.get("fields", []) or []
    if input_fields or output_fields:
        node_data["input_fields"] = input_fields
        node_data["output_fields"] = output_fields
        node_data["fields"] = list(input_fields) + list(output_fields)
    elif legacy_fields:
        node_data["input_fields"] = legacy_fields
        node_data["output_fields"] = []
        node_data["fields"] = legacy_fields


def _apply_single_node_enrichment(
    loader: GraphLoader,
    item: Dict[str, Any],
    nid: str,
    floor: float,
    unresolved: List[str],
) -> int:
    node_data = loader.graph.nodes[nid]
    intent = item.get("intent", "")
    calls = item.get("calls", []) or []

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

    _update_node_fields(node_data, item)

    fields_dict = {
        "input_fields": node_data.get("input_fields", []),
        "output_fields": node_data.get("output_fields", []),
        "fields": node_data.get("fields", []),
    }
    loader.hash_gate.update_node(
        node_id=nid,
        file_path=node_data.get("file", ""),
        content=loader.node_signature(node_data),
        layer=node_data.get("layer", ""),
        summary=node_data.get("summary", ""),
        fields_json=json.dumps(fields_dict),
        intent=node_data.get("intent", ""),
    )

    bridges = 0
    for call_target in calls:
        tgt_id, score = resolve_call_target(loader.graph, loader.vector_store, call_target, nid, floor)
        if tgt_id:
            loader.graph.add_edge(nid, tgt_id, relation="cross_layer_link", confidence=float(score))
            bridges += 1
        else:
            unresolved.append(str(call_target))
    return bridges


def apply_enrichment_items(
    loader: GraphLoader,
    path: str,
    items: List[Dict[str, Any]],
    source_label: str,
) -> Dict[str, Any]:
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

        bridges += _apply_single_node_enrichment(loader, item, nid, floor, unresolved)
        applied_ids.append(nid)

    loader.vector_store.add_documents(loader.docs_to_index)
    stamp_degrees(loader)
    snapshot_path = loader.save_graph()
    loader.export_yaml()

    cursor = read_cursor(path)
    remaining_queued = [i for i in cursor["queued"] if i not in set(applied_ids)]
    write_cursor(path, remaining_queued, cursor["applied"] + applied_ids)
    append_enrichment_audit(path, items, applied_ids, bridges, unresolved, source_label)

    return {
        "applied_ids": applied_ids,
        "unknown_ids": unknown_ids,
        "bridges": bridges,
        "unresolved": unresolved,
        "floor": floor,
        "snapshot_path": snapshot_path,
    }


def append_enrichment_audit(
    path: str,
    items: List[Dict[str, Any]],
    applied_ids: List[str],
    bridges: int,
    unresolved: List[str],
    source_label: str,
) -> None:
    audit_path = state_path(path, AUDIT_LOG_FILENAME)
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
