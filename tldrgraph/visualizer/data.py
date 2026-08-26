"""
Data preparation for the visualizer.

Turns the TLDRGraph graph snapshot (or a freshly extracted graph) into the
two-tier payload the standalone HTML app renders:

- Tier 1: parent modules / files
- Tier 2: public components, functions, classes and endpoints
  (private helper methods and pseudo-nodes are dropped)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

from ..hierarchy import is_test_node
from ..layer_config import load_layer_config
from ..layers import get_registry
from .flows_data import extract_visualizer_workflows
from .bpmn_data import attach_bpmn_processes
from .palette import FALLBACK_COLOR, palette_at
from .source import SourceIndex, language_for, symbol_name


def _load_snapshot(root_dir: str) -> Dict[str, Any]:
    """Reads ``.tldrgraph/graph.json``; returns ``{}`` when missing or unreadable."""
    snapshot_path = os.path.join(root_dir, ".tldrgraph", "graph.json")
    if not os.path.exists(snapshot_path):
        return {}
    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_snapshot(root_dir: str) -> Dict[str, Any]:
    """Builds a snapshot-shaped dict by running the extractors over ``root_dir``."""
    from ..graph_loader import GraphLoader

    loader = GraphLoader(root_dir)
    loader.load_or_extract(enrich_llm=False)
    nodes = [
        {
            "id": str(nid), "label": data.get("label", str(nid)),
            "display_label": data.get("display_label") or data.get("label", str(nid)),
            "file": data.get("file", ""), "layer": data.get("layer", "General / Utility"),
            "layer_id": data.get("layer_id", "utility"), "type": data.get("type", "code"),
            "intent": data.get("intent", ""), "input_fields": data.get("input_fields", []),
            "output_fields": data.get("output_fields", []), "fields": data.get("fields", []),
            "is_test": data.get("is_test", is_test_node(data.get("file", ""), data.get("label", str(nid)))),
            "source_location": data.get("source_location"), "dead_code_status": data.get("dead_code_status", "live"),
            "dead_code_reason": data.get("dead_code_reason", ""),
        }
        for nid, data in loader.graph.nodes(data=True)
    ]
    edges = [
        {"source": str(u), "target": str(v), "relation": d.get("relation", "calls"), "confidence": float(d.get("confidence", 1.0))}
        for u, v, d in loader.graph.edges(data=True)
    ]
    return {"nodes": nodes, "edges": edges}


def _is_private_or_file_label(lbl: str, dl: str, fpath: str) -> bool:
    if lbl.startswith("_") or lbl.startswith("._") or lbl.startswith(".__"):
        return True
    if dl.startswith("_") or "._" in dl or ".__" in dl:
        return True
    if fpath and (lbl == os.path.basename(fpath) or dl == os.path.basename(fpath)):
        return True
    return False


def _is_renderable_node(n: Dict[str, Any]) -> bool:
    """
    Keeps only public code symbols: drops rationale pseudo-nodes, prose-ish labels,
    private helpers, and the module's own self-node.
    """
    if n.get("dead_code_status") == "not_code" or n.get("type") == "rationale":
        return False

    lbl = n.get("label", "")
    if " " in lbl and not lbl.endswith(")") and not lbl.startswith("class ") and any(c in lbl for c in ":{}."):
        return False

    fpath = n.get("file", "")
    if not fpath.strip():
        return False

    dl = n.get("display_label", lbl)
    return not _is_private_or_file_label(lbl, dl, fpath)


def _build_layer_map(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Assigns a palette entry to every layer that actually has nodes, honoring the
    registry order first and then any ad-hoc layer ids found on the nodes.
    """
    registry = get_registry()
    present_layer_ids: Set[str] = {n.get("layer_id") or "utility" for n in nodes}

    layer_map: Dict[str, Dict[str, Any]] = {}
    palette_idx = 0

    for layer in registry.ordered():
        if layer.id not in present_layer_ids:
            continue
        p = FALLBACK_COLOR if layer.id == registry.utility_id else palette_at(palette_idx)
        palette_idx += 1
        layer_map[layer.id] = {
            "id": layer.id,
            "name": layer.name,
            "order": layer.order,
            "color": p["color"],
            "border": p["border"],
            "bg": p["bg"],
            "glow": p.get("glow", p["bg"]),
        }

    for n in nodes:
        lid = n.get("layer_id") or "utility"
        if lid in layer_map:
            continue
        p = palette_at(palette_idx)
        palette_idx += 1
        layer_map[lid] = {
            "id": lid,
            "name": n.get("layer") or "General / Utility",
            "order": len(layer_map) + 1,
            "color": p["color"],
            "border": p["border"],
            "bg": p["bg"],
            "glow": p.get("glow", p["bg"]),
        }

    return layer_map


def _module_id_for(fpath: str) -> str:
    return f"mod_{re.sub(r'[^a-zA-Z0-9_]', '_', fpath)}"


def _build_single_node_record(
    n: Dict[str, Any],
    layer_map: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
) -> Tuple[Dict[str, Any], str, str, bool, Dict[str, Any]]:
    nid = str(n["id"])
    fpath = (n.get("file") or "").strip()
    lid = n.get("layer_id") or "utility"
    layer_info = layer_map.get(lid, FALLBACK_COLOR)
    label = n.get("label") or nid
    display_label = n.get("display_label") or label
    test_flag = bool(n.get("is_test", is_test_node(fpath, label)))
    mod_id = _module_id_for(fpath)
    located = sources.locate_symbol(
        fpath, n.get("source_location"), symbol_name(label, display_label)
    ) or {}

    node_rec = {
        "id": nid, "label": label, "display_label": display_label, "file": fpath,
        "layer_id": lid, "layer": layer_info["name"], "type": n.get("type", "function"),
        "tier": 2, "is_test": test_flag, "intent": (n.get("intent") or "").strip(),
        "input_fields": n.get("input_fields") or [], "output_fields": n.get("output_fields") or [],
        "fields": n.get("fields") or [], "source_location": n.get("source_location") or "",
        "dead_code_status": n.get("dead_code_status") or "live", "path": fpath,
        "name": symbol_name(label, display_label), "language": language_for(fpath),
        "code_start": located.get("start", 0), "code_end": located.get("end", 0),
        "code_relocated": bool(located.get("relocated", False)), "module_id": mod_id,
        "inbound": [], "outbound": [],
    }
    return node_rec, mod_id, lid, test_flag, layer_info


def _ensure_module_record(
    modules_by_id: Dict[str, Dict[str, Any]],
    mod_id: str,
    fpath: str,
    lid: str,
    layer_info: Dict[str, Any],
    test_flag: bool,
    module_intent: str = "",
) -> None:
    if mod_id not in modules_by_id:
        mod_label = os.path.basename(fpath)
        modules_by_id[mod_id] = {
            "id": mod_id,
            "label": mod_label,
            "file": fpath,
            "layer_id": lid,
            "layer": layer_info["name"],
            "tier": 1,
            "is_test": test_flag,
            "intent": module_intent,
            "path": fpath,
            "language": language_for(fpath),
            "subnodes": [],
            "inbound_modules": set(),
            "outbound_modules": set(),
        }


def _build_nodes_and_modules(
    raw_nodes: List[Dict[str, Any]],
    layer_map: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
    file_intents: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Returns ``(nodes_by_id, modules_by_id)`` with modules owning their subnodes."""
    modules_by_id: Dict[str, Dict[str, Any]] = {}
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    f_intents = file_intents or {}

    for n in raw_nodes:
        node_rec, mod_id, lid, test_flag, layer_info = _build_single_node_record(n, layer_map, sources)
        nodes_by_id[node_rec["id"]] = node_rec
        mod_intent = f_intents.get(node_rec["file"], "")
        _ensure_module_record(modules_by_id, mod_id, node_rec["file"], lid, layer_info, test_flag, mod_intent)
        modules_by_id[mod_id]["subnodes"].append(node_rec)

    return nodes_by_id, modules_by_id


def _record_node_edges(
    src_node: Dict[str, Any],
    tgt_node: Dict[str, Any],
    relation: str,
    confidence: float,
) -> Dict[str, Any]:
    src_node["outbound"].append({
        "target_id": tgt_node["id"],
        "target_label": tgt_node["display_label"],
        "target_file": tgt_node["file"],
        "target_layer": tgt_node["layer"],
        "relation": relation,
        "confidence": confidence,
    })
    tgt_node["inbound"].append({
        "source_id": src_node["id"],
        "source_label": src_node["display_label"],
        "source_file": src_node["file"],
        "source_layer": src_node["layer"],
        "relation": relation,
        "confidence": confidence,
    })
    return {
        "source": src_node["id"],
        "target": tgt_node["id"],
        "source_mod": src_node["module_id"],
        "target_mod": tgt_node["module_id"],
        "relation": relation,
        "confidence": confidence,
    }


def _record_module_edges(
    modules_by_id: Dict[str, Dict[str, Any]],
    module_edges_map: Dict[Tuple[str, str], Dict[str, Any]],
    src_mod: str,
    tgt_mod: str,
    relation: str,
    confidence: float,
) -> None:
    if src_mod == tgt_mod:
        return
    modules_by_id[src_mod]["outbound_modules"].add(tgt_mod)
    modules_by_id[tgt_mod]["inbound_modules"].add(src_mod)

    key = (src_mod, tgt_mod)
    if key not in module_edges_map:
        module_edges_map[key] = {
            "source": src_mod,
            "target": tgt_mod,
            "count": 0,
            "relations": set(),
            "confidence": confidence,
        }
    module_edges_map[key]["count"] += 1
    module_edges_map[key]["relations"].add(relation)


def _build_edges(
    raw_edges: List[Dict[str, Any]],
    nodes_by_id: Dict[str, Dict[str, Any]],
    modules_by_id: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Walks the raw edges once, recording per-node inbound/outbound detail and
    rolling child edges up into aggregated module-to-module edges.
    """
    child_edges: List[Dict[str, Any]] = []
    module_edges_map: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for e in raw_edges:
        src_id, tgt_id = str(e["source"]), str(e["target"])
        if src_id not in nodes_by_id or tgt_id not in nodes_by_id:
            continue

        relation = e.get("relation", "calls")
        confidence = float(e.get("confidence", 1.0))
        src_node, tgt_node = nodes_by_id[src_id], nodes_by_id[tgt_id]

        child_edges.append(_record_node_edges(src_node, tgt_node, relation, confidence))
        _record_module_edges(modules_by_id, module_edges_map, src_node["module_id"], tgt_node["module_id"], relation, confidence)

    module_edges = [
        {
            "source": ed["source"],
            "target": ed["target"],
            "count": ed["count"],
            "relations": sorted(ed["relations"]),
            "confidence": ed["confidence"],
        }
        for ed in module_edges_map.values()
    ]
    return child_edges, module_edges


def _serialize_modules(modules_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts the in-progress module dicts (with sets) into JSON-safe records."""
    return [
        {
            "id": m["id"], "label": m["label"], "file": m["file"], "path": m["path"],
            "language": m["language"], "layer_id": m["layer_id"], "layer": m["layer"],
            "tier": 1, "is_test": m["is_test"], "intent": m["intent"],
            "subnode_count": len(m["subnodes"]), "subnodes": m["subnodes"],
            "inbound_modules": sorted(m["inbound_modules"]), "outbound_modules": sorted(m["outbound_modules"]),
        }
        for m in modules_by_id.values()
    ]


def _load_or_extract_snapshot(root_dir: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    snapshot = _load_snapshot(root_dir)
    nodes = snapshot.get("nodes", [])
    edges = snapshot.get("edges", [])
    if not nodes:
        extracted = _extract_snapshot(root_dir)
        nodes = extracted.get("nodes", [])
        edges = extracted.get("edges", [])
    return nodes, edges


def _build_nx_graph(raw_nodes: List[Dict[str, Any]], raw_edges: List[Dict[str, Any]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for n in raw_nodes:
        g.add_node(str(n["id"]), **n)
    for e in raw_edges:
        g.add_edge(str(e["source"]), str(e["target"]), relation=e.get("relation", "calls"))
    return g


def prepare_visualizer_data(root_dir: str) -> Dict[str, Any]:
    """Builds the complete two-tier payload inlined into the standalone HTML app."""
    load_layer_config(root_dir)
    raw_nodes, raw_edges = _load_or_extract_snapshot(root_dir)

    file_intents = {n["file"]: n["intent"].strip() for n in raw_nodes if n.get("file") and n.get("intent")}
    renderable = [n for n in raw_nodes if _is_renderable_node(n)]
    layer_map = _build_layer_map(renderable)

    sources = SourceIndex(root_dir)
    nodes_by_id, modules_by_id = _build_nodes_and_modules(renderable, layer_map, sources, file_intents)
    child_edges, module_edges = _build_edges(raw_edges, nodes_by_id, modules_by_id)
    modules = _serialize_modules(modules_by_id)

    graph = _build_nx_graph(raw_nodes, raw_edges)
    workflows = extract_visualizer_workflows(graph, nodes_by_id, sources)
    attach_bpmn_processes(root_dir, workflows, graph, nodes_by_id)

    active_layer_ids = {m["layer_id"] for m in modules}
    layers = sorted((l for l in layer_map.values() if l["id"] in active_layer_ids), key=lambda x: x["order"])

    return {
        "root": os.path.abspath(root_dir),
        "layers": layers,
        "modules": modules,
        "nodes": list(nodes_by_id.values()),
        "workflows": workflows,
        "module_edges": module_edges,
        "child_edges": child_edges,
        "stats": {
            "total_modules": len(modules),
            "total_nodes": len(nodes_by_id),
            "total_workflows": len(workflows),
            "total_module_edges": len(module_edges),
            "total_child_edges": len(child_edges),
            "test_modules": sum(1 for m in modules if m["is_test"]),
            "nodes_with_range": sum(1 for n in nodes_by_id.values() if n["code_start"]),
        },
    }
