"""
Snapshot persistence, hash-based gating, and dirty detection for TLDRGraph.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple
import networkx as nx

from . import __version__, paths
from .layers import get_registry, layer_id_of

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_FILENAME = "graph.json"
FILE_HASHES_FILENAME = "file_hashes.json"
STATE_DIRNAME = paths.STATE_DIRNAME
GRAPHIFY_GRAPH_FILENAME = paths.GRAPHIFY_GRAPH_FILENAME
GRAPHIFY_MANIFEST_FILENAME = paths.GRAPHIFY_MANIFEST_FILENAME
LEGACY_GRAPHIFY_DIRNAME = paths.LEGACY_GRAPHIFY_DIRNAME
graphify_graph_path = paths.graphify_graph_path
graphify_manifest_path = paths.graphify_manifest_path

TEST_PATTERNS = (
    "test_", "_test.", ".test.", ".spec.", "/__tests__/", "/__mocks__/",
    "/tests/", "/test/", "/e2e/", "conftest.py"
)


def is_test_node(file_path: str, label: str) -> bool:
    if not file_path and not label:
        return False
    fp = file_path.lower()
    lbl = label.lower()
    return any(p in fp for p in TEST_PATTERNS) or any(p in lbl for p in TEST_PATTERNS)


def is_placeholder_summary(summary: str, node: Dict[str, Any]) -> bool:
    if not summary:
        return True
    label = str(node.get("label") or "")
    file_path = str(node.get("file") or "")
    tail = f"{label} located at {file_path}"
    return summary.endswith(tail)


def disk_hash(root_dir: str, file_path: str) -> Optional[str]:
    abs_path = os.path.join(root_dir, file_path)
    if not os.path.isfile(abs_path):
        return None
    try:
        with open(abs_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def node_signature(root_dir: str, node_attrs: Dict[str, Any], file_hashes: Dict[str, str]) -> str:
    node_id = str(node_attrs.get("id") or "")
    label = node_attrs.get("label") or ""
    file_path = node_attrs.get("file") or ""
    source_location = node_attrs.get("source_location") or ""

    file_hash = file_hashes.get(file_path)
    if not file_hash and file_path:
        file_hash = disk_hash(root_dir, file_path)
        if file_hash:
            file_hashes[file_path] = file_hash
    if not file_hash:
        return label + file_path
    return f"{file_hash}|{node_id}|{source_location}"


def load_file_hashes(root_dir: str) -> Dict[str, str]:
    manifest_path = paths.graphify_manifest_path(root_dir)
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return {}
    if not isinstance(manifest, dict):
        return {}

    hashes: Dict[str, str] = {}
    for rel_path, meta in manifest.items():
        if not isinstance(meta, dict):
            continue
        file_hash = meta.get("semantic_hash") or meta.get("ast_hash")
        if file_hash:
            hashes[rel_path] = str(file_hash)
    return hashes


def load_graph_snapshot(root_dir: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(root_dir, ".tldrgraph", SNAPSHOT_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict) or "nodes" not in data or not isinstance(data["nodes"], list):
        return None

    registry = get_registry()
    for node in data["nodes"]:
        if isinstance(node, dict) and not node.get("layer_id"):
            node["layer_id"] = registry.id_for_name(str(node.get("layer") or ""))
    return data


def _serialize_node(node_id: str, data: Dict[str, Any], root_dir: str, file_hashes: Dict[str, str]) -> Dict[str, Any]:
    fields = data.get("fields", []) or (list(data.get("input_fields", [])) + list(data.get("output_fields", [])))
    return {
        "id": str(node_id),
        "label": data.get("label", str(node_id)),
        "display_label": data.get("display_label") or data.get("label", str(node_id)),
        "file": data.get("file", ""),
        "layer": data.get("layer", get_registry().utility.name),
        "layer_id": layer_id_of(data),
        "layer_source": data.get("layer_source", "rule"),
        "type": data.get("type", "symbol"),
        "community": data.get("community"),
        "degree": data.get("degree", 0),
        "summary": data.get("summary", ""),
        "input_fields": data.get("input_fields", []),
        "output_fields": data.get("output_fields", []),
        "fields": fields,
        "intent": data.get("intent", ""),
        "enrichment_source": data.get("enrichment_source", ""),
        "source_location": data.get("source_location"),
        "dead_code_status": data.get("dead_code_status", ""),
        "dead_code_reason": data.get("dead_code_reason", ""),
        "is_test": bool(data.get("is_test", is_test_node(data.get("file", ""), data.get("label", "")))),
        "signature": node_signature(root_dir, data, file_hashes),
    }


def save_graph_snapshot(
    root_dir: str,
    graph: nx.DiGraph,
    layers_config_hash: str,
    file_hashes: Dict[str, str],
) -> str:
    out_path = os.path.join(root_dir, ".tldrgraph", SNAPSHOT_FILENAME)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    nodes = [_serialize_node(node_id, data, root_dir, file_hashes) for node_id, data in graph.nodes(data=True)]
    edges = [
        {
            "source": str(src),
            "target": str(tgt),
            "relation": data.get("relation", "calls"),
            "confidence": float(data.get("confidence", 1.0)),
        }
        for src, tgt, data in graph.edges(data=True)
    ]

    snapshot = {
        "tldrgraph_version": __version__,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "layers_config_hash": layers_config_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    return out_path


def _restore_io_fields(current: Dict[str, Any], old: Dict[str, Any]) -> bool:
    in_f = old.get("input_fields") or []
    out_f = old.get("output_fields") or []
    fields = old.get("fields") or []
    touched = False
    if in_f:
        current["input_fields"] = in_f
        touched = True
    if out_f:
        current["output_fields"] = out_f
        touched = True
    if fields:
        current["fields"] = fields
        touched = True
    elif in_f or out_f:
        current["fields"] = list(in_f) + list(out_f)
        touched = True
    return touched


def _restore_node_fields(current: Dict[str, Any], old: Dict[str, Any]) -> bool:
    touched = _restore_io_fields(current, old)
    if old.get("layer_source") == "agent":
        old_lid = old.get("layer_id")
        if old_lid and old_lid in get_registry():
            current["layer_id"] = old_lid
            current["layer"] = get_registry().name(old_lid)
            current["layer_source"] = "agent"

    intent = old.get("intent") or ""
    if intent:
        current["intent"] = intent
        current["enrichment_source"] = old.get("enrichment_source") or ""
        touched = True

    current["is_test"] = old["is_test"] if "is_test" in old else is_test_node(current.get("file", ""), current.get("label", ""))

    summary = old.get("summary") or ""
    if summary and not is_placeholder_summary(summary, old) and not is_placeholder_summary(summary, current):
        current["summary"] = f"{current['layer']}: {current['label']} - {intent}" if intent else summary
        touched = True

    return touched


def _restore_edges(snapshot: Dict[str, Any], graph: nx.DiGraph, bridge_relations: Set[str]) -> int:
    edges_restored = 0
    for old_edge in snapshot.get("edges", []) or []:
        if not isinstance(old_edge, dict):
            continue
        relation = old_edge.get("relation")
        if relation not in bridge_relations:
            continue
        src = str(old_edge.get("source"))
        tgt = str(old_edge.get("target"))
        if not (graph.has_node(src) and graph.has_node(tgt)):
            continue
        try:
            confidence = float(old_edge.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        graph.add_edge(src, tgt, relation=relation, confidence=confidence)
        edges_restored += 1
    return edges_restored


def carry_forward_snapshot(
    root_dir: str,
    graph: nx.DiGraph,
    file_hashes: Dict[str, str],
    bridge_relations: Set[str],
) -> Tuple[int, int, Set[str]]:
    snapshot = load_graph_snapshot(root_dir)
    if not snapshot:
        return 0, 0, set()

    nodes_restored = 0
    restored_clean_ids = set()
    for old in snapshot.get("nodes", []):
        if not isinstance(old, dict):
            continue
        node_id = str(old.get("id"))
        if not graph.has_node(node_id):
            continue

        current = graph.nodes[node_id]
        if _restore_node_fields(current, old):
            nodes_restored += 1

        if current.get("intent") and old.get("signature") and old["signature"] == node_signature(root_dir, current, file_hashes):
            restored_clean_ids.add(node_id)

    edges_restored = _restore_edges(snapshot, graph, bridge_relations)
    return nodes_restored, edges_restored, restored_clean_ids
