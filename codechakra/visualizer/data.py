"""
Data preparation for the visualizer.

Turns the CodeChakra graph snapshot (or a freshly extracted graph) into the
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

from ..hierarchy import is_test_node
from ..layer_config import load_layer_config
from ..layers import get_registry
from .palette import FALLBACK_COLOR, palette_at
from .source import SourceIndex, language_for, symbol_name


def _load_snapshot(root_dir: str) -> Dict[str, Any]:
    """Reads ``.codechakra/graph.json``; returns ``{}`` when missing or unreadable."""
    snapshot_path = os.path.join(root_dir, ".codechakra", "graph.json")
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
    return {
        "nodes": [
            {
                "id": str(nid),
                "label": data.get("label", str(nid)),
                "display_label": data.get("display_label") or data.get("label", str(nid)),
                "file": data.get("file", ""),
                "layer": data.get("layer", "General / Utility"),
                "layer_id": data.get("layer_id", "utility"),
                "type": data.get("type", "code"),
                "summary": data.get("summary", ""),
                "intent": data.get("intent", ""),
                "input_fields": data.get("input_fields", []),
                "output_fields": data.get("output_fields", []),
                "fields": data.get("fields", []),
                "is_test": data.get("is_test", is_test_node(data.get("file", ""), data.get("label", ""))),
                "source_location": data.get("source_location"),
                "dead_code_status": data.get("dead_code_status", "live"),
            }
            for nid, data in loader.graph.nodes(data=True)
        ],
        "edges": [
            {
                "source": str(u),
                "target": str(v),
                "relation": d.get("relation", "calls"),
                "confidence": float(d.get("confidence", 1.0)),
            }
            for u, v, d in loader.graph.edges(data=True)
        ],
    }


def _is_renderable_node(n: Dict[str, Any]) -> bool:
    """
    Keeps only public code symbols: drops rationale pseudo-nodes, prose-ish labels,
    private helpers, and the module's own self-node.
    """
    if n.get("dead_code_status") == "not_code" or n.get("type") == "rationale":
        return False

    lbl = n.get("label", "")
    # Prose-like pseudo labels ("something: else", "a.b c") are not real symbols.
    if " " in lbl and not lbl.endswith(")") and not lbl.startswith("class ") and any(c in lbl for c in ":{}."):
        return False

    dl = n.get("display_label", lbl)
    fpath = n.get("file", "")

    if lbl.startswith("_") or lbl.startswith("._") or lbl.startswith(".__") or dl.startswith("_") or "._" in dl or ".__" in dl:
        return False

    if fpath and (lbl == os.path.basename(fpath) or dl == os.path.basename(fpath)):
        return False

    # No owning file means an imported name or a bare decorator reference, not a
    # symbol anyone can navigate to. These used to pile up in a "root_fixtures"
    # bucket that carried no information.
    if not fpath.strip():
        return False

    return True


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


def _build_nodes_and_modules(
    raw_nodes: List[Dict[str, Any]],
    layer_map: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Returns ``(nodes_by_id, modules_by_id)`` with modules owning their subnodes."""
    modules_by_id: Dict[str, Dict[str, Any]] = {}
    nodes_by_id: Dict[str, Dict[str, Any]] = {}

    for n in raw_nodes:
        nid = str(n["id"])
        fpath = (n.get("file") or "").strip()
        lid = n.get("layer_id") or "utility"
        layer_info = layer_map.get(lid, FALLBACK_COLOR)
        label = n.get("label") or nid
        display_label = n.get("display_label") or label
        test_flag = bool(n.get("is_test", is_test_node(fpath, label)))
        mod_id = _module_id_for(fpath)
        pretty_file = fpath
        located = sources.locate_symbol(
            fpath, n.get("source_location"), symbol_name(label, display_label)
        ) or {}

        nodes_by_id[nid] = {
            "id": nid,
            "label": label,
            "display_label": display_label,
            "file": pretty_file,
            "layer_id": lid,
            "layer": layer_info["name"],
            "type": n.get("type", "function"),
            "tier": 2,
            "is_test": test_flag,
            "intent": n.get("intent") or n.get("summary") or f"`{label}` in `{fpath}`.",
            "summary": n.get("summary") or "",
            "input_fields": n.get("input_fields") or [],
            "output_fields": n.get("output_fields") or [],
            "fields": n.get("fields") or [],
            "source_location": n.get("source_location") or "",
            "dead_code_status": n.get("dead_code_status") or "live",
            "path": fpath,
            "name": symbol_name(label, display_label),
            "language": language_for(fpath),
            "code_start": located.get("start", 0),
            "code_end": located.get("end", 0),
            "code_relocated": bool(located.get("relocated", False)),
            "module_id": mod_id,
            "inbound": [],
            "outbound": [],
        }

        if mod_id not in modules_by_id:
            mod_label = os.path.basename(fpath)
            modules_by_id[mod_id] = {
                "id": mod_id,
                "label": mod_label,
                "file": pretty_file,
                "layer_id": lid,
                "layer": layer_info["name"],
                "tier": 1,
                "is_test": test_flag,
                "intent": f"Module `{mod_label}` in {layer_info['name']}.",
                "path": fpath,
                "language": language_for(fpath),
                "subnodes": [],
                "inbound_modules": set(),
                "outbound_modules": set(),
            }
        modules_by_id[mod_id]["subnodes"].append(nodes_by_id[nid])

    return nodes_by_id, modules_by_id


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
        src_id = str(e["source"])
        tgt_id = str(e["target"])
        if src_id not in nodes_by_id or tgt_id not in nodes_by_id:
            continue

        relation = e.get("relation", "calls")
        confidence = float(e.get("confidence", 1.0))
        src_node = nodes_by_id[src_id]
        tgt_node = nodes_by_id[tgt_id]

        src_node["outbound"].append({
            "target_id": tgt_id,
            "target_label": tgt_node["display_label"],
            "target_file": tgt_node["file"],
            "target_layer": tgt_node["layer"],
            "relation": relation,
            "confidence": confidence,
        })
        tgt_node["inbound"].append({
            "source_id": src_id,
            "source_label": src_node["display_label"],
            "source_file": src_node["file"],
            "source_layer": src_node["layer"],
            "relation": relation,
            "confidence": confidence,
        })

        child_edges.append({
            "source": src_id,
            "target": tgt_id,
            "source_mod": src_node["module_id"],
            "target_mod": tgt_node["module_id"],
            "relation": relation,
            "confidence": confidence,
        })

        src_mod = src_node["module_id"]
        tgt_mod = tgt_node["module_id"]
        if src_mod == tgt_mod:
            continue

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
            "id": m["id"],
            "label": m["label"],
            "file": m["file"],
            "path": m["path"],
            "language": m["language"],
            "layer_id": m["layer_id"],
            "layer": m["layer"],
            "tier": 1,
            "is_test": m["is_test"],
            "intent": m["intent"],
            "subnode_count": len(m["subnodes"]),
            "subnodes": m["subnodes"],
            "inbound_modules": sorted(m["inbound_modules"]),
            "outbound_modules": sorted(m["outbound_modules"]),
        }
        for m in modules_by_id.values()
    ]


def prepare_visualizer_data(root_dir: str) -> Dict[str, Any]:
    """
    Builds the complete two-tier payload (layers, modules, nodes, edges, stats)
    that gets inlined into the standalone HTML app.
    """
    load_layer_config(root_dir)

    snapshot = _load_snapshot(root_dir)
    raw_nodes = snapshot.get("nodes", [])
    raw_edges = snapshot.get("edges", [])
    if not raw_nodes:
        snapshot = _extract_snapshot(root_dir)
        raw_nodes = snapshot["nodes"]
        raw_edges = snapshot["edges"]

    renderable = [n for n in raw_nodes if _is_renderable_node(n)]
    layer_map = _build_layer_map(renderable)

    sources = SourceIndex(root_dir)
    nodes_by_id, modules_by_id = _build_nodes_and_modules(renderable, layer_map, sources)
    child_edges, module_edges = _build_edges(raw_edges, nodes_by_id, modules_by_id)
    modules = _serialize_modules(modules_by_id)

    active_layer_ids = {m["layer_id"] for m in modules}
    layers = sorted(
        (l for l in layer_map.values() if l["id"] in active_layer_ids),
        key=lambda x: x["order"],
    )

    return {
        # Absolute root, so the app can build editor deep links per node.
        "root": os.path.abspath(root_dir),
        "layers": layers,
        "modules": modules,
        "nodes": list(nodes_by_id.values()),
        "module_edges": module_edges,
        "child_edges": child_edges,
        "stats": {
            "total_modules": len(modules),
            "total_nodes": len(nodes_by_id),
            "total_module_edges": len(module_edges),
            "total_child_edges": len(child_edges),
            "test_modules": sum(1 for m in modules if m["is_test"]),
            "nodes_with_range": sum(1 for n in nodes_by_id.values() if n["code_start"]),
        },
    }
