"""
Workflow and execution flow extraction for the visualizer.

Constructs genuine, logical architectural workflows representing real user journeys
and subsystem execution pipelines in the codebase.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

from ..layers import get_registry, layer_id_of
from .flows_blueprints import CURATED_BLUEPRINTS
from .flows_discover import discover_workflows
from .source import SourceIndex, symbol_name



def _match_node_in_dict(nodes: Dict[str, Dict[str, Any]], fpath: str, clean_sym: str) -> Optional[str]:
    for nid, node in nodes.items():
        n_file = (node.get("file") or "").lower()
        if fpath in n_file or n_file in fpath:
            n_lbl = (node.get("label") or "").replace("()", "").strip().lower()
            if clean_sym in n_lbl or n_lbl in clean_sym or clean_sym == n_lbl.split(".")[-1]:
                return str(nid)
    return None


def _match_node_in_graph(
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    fpath: str,
    sym_name: str,
) -> Optional[str]:
    """Finds best matching node ID in graph for a file and symbol name."""
    clean_sym = sym_name.replace("()", "").strip().lower()
    f_clean = fpath.lower()
    res = _match_node_in_dict(nodes_by_id, f_clean, clean_sym)
    if res:
        return res
    raw_nodes = {str(nid): data for nid, data in graph.nodes(data=True)}
    return _match_node_in_dict(raw_nodes, f_clean, clean_sym)


def _format_existing_node(
    existing: Dict[str, Any],
    node_id: str,
    step_num: int,
    custom_intent: str,
) -> Dict[str, Any]:
    return {
        "step_number": step_num,
        "node_id": node_id,
        "symbol": existing["label"],
        "display_label": existing.get("display_label") or existing["label"],
        "file": existing["file"],
        "layer_id": existing["layer_id"],
        "layer": existing["layer"],
        "type": existing.get("type", "function"),
        "intent": custom_intent or existing.get("intent", ""),
        "input_fields": existing.get("input_fields", []),
        "output_fields": existing.get("output_fields", []),
        "fields": existing.get("fields", []),
        "code_start": existing.get("code_start", 0),
        "code_end": existing.get("code_end", 0),
    }


def _format_step_record(
    node_id: str,
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
    step_num: int,
    custom_intent: str = "",
) -> Dict[str, Any]:
    """Formats a single node in a workflow sequence."""
    existing = nodes_by_id.get(node_id)
    if existing:
        return _format_existing_node(existing, node_id, step_num, custom_intent)

    raw = graph.nodes.get(node_id, {})
    fpath = (raw.get("file") or "").strip()
    lbl = raw.get("label") or node_id
    dlbl = raw.get("display_label") or lbl
    lid = layer_id_of(raw) or "utility"
    layer_name = raw.get("layer") or get_registry().name(lid)
    loc = sources.locate_symbol(fpath, raw.get("source_location"), symbol_name(lbl, dlbl)) or {}

    return {
        "step_number": step_num,
        "node_id": node_id,
        "symbol": lbl,
        "display_label": dlbl,
        "file": fpath,
        "layer_id": lid,
        "layer": layer_name,
        "type": raw.get("type", "function"),
        "intent": custom_intent or raw.get("intent") or raw.get("summary", ""),
        "input_fields": raw.get("input_fields", []),
        "output_fields": raw.get("output_fields", []),
        "fields": raw.get("fields", []),
        "code_start": loc.get("start", 0),
        "code_end": loc.get("end", 0),
    }


SUPPORT_PER_STEP = 6

# Callers that exist to exercise the code rather than take part in it.
NON_PRODUCT_DIRS = ("tests/", "test/", "benchmarks/", "scripts/")


def _collect_curated_steps(
    bp_steps: List[Tuple[str, str, str]],
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    steps = []
    layers_inv: List[str] = []

    for idx, (fpath, sym, custom_intent) in enumerate(bp_steps):
        nid = _match_node_in_graph(graph, nodes_by_id, fpath, sym)
        if not nid:
            nid = f"synthetic_{re.sub(r'[^a-zA-Z0-9_]', '_', fpath)}_{sym}"

        s_rec = _format_step_record(nid, graph, nodes_by_id, sources, idx + 1, custom_intent)
        if idx > 0:
            prev_id = steps[-1]["node_id"]
            edge_data = graph.get_edge_data(prev_id, nid) or {}
            s_rec["via_relation"] = edge_data.get("relation") or "calls"
            s_rec["from_node"] = prev_id
        else:
            s_rec["via_relation"] = "entry_point"
            s_rec["from_node"] = None

        if s_rec["layer"] and s_rec["layer"] not in layers_inv:
            layers_inv.append(s_rec["layer"])
        steps.append(s_rec)

    return steps, layers_inv


def _is_support_candidate(node_id: str, nodes_by_id: Dict[str, Dict[str, Any]]) -> bool:
    """A support node has to be a renderable production symbol."""
    node = nodes_by_id.get(node_id)
    if not node or node.get("is_test"):
        return False
    fpath = (node.get("file") or "").replace("\\", "/")
    return not fpath.startswith(NON_PRODUCT_DIRS)


def _collect_support_nodes(
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Gathers the symbols each step actually calls or is called by.

    Sharing a file with a step says nothing about taking part in the workflow -
    a flat command surface like cli.py holds many unrelated neighbours - so the
    call graph decides membership instead. What a step calls ranks above what
    calls it, and among equals the least connected symbol wins, which keeps
    focused helpers and leaves ubiquitous utilities out.
    """
    claimed = {s["node_id"] for s in steps}
    support: List[Dict[str, Any]] = []

    for step in steps:
        sid = step["node_id"]
        # A step the canvas cannot render (a private helper, say) carries no
        # drawable edges, so its neighbours would arrive with nothing to attach to.
        if not graph.has_node(sid) or sid not in nodes_by_id:
            continue

        ranked = []
        seen = set()
        groups = (("calls", graph.successors(sid)), ("called_by", graph.predecessors(sid)))
        for relation, neighbours in groups:
            for nid in neighbours:
                if nid in seen or nid in claimed or not _is_support_candidate(nid, nodes_by_id):
                    continue
                seen.add(nid)
                ranked.append((0 if relation == "calls" else 1, graph.degree(nid), str(nid), nid, relation))

        ranked.sort()
        for _, _, _, nid, relation in ranked[:SUPPORT_PER_STEP]:
            claimed.add(nid)
            support.append({
                "node_id": nid,
                "step_node_id": sid,
                "step_number": step["step_number"],
                "relation": relation,
            })

    return support


def _build_curated_workflow(
    bp: Dict[str, Any],
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
) -> Optional[Dict[str, Any]]:
    """Builds a curated workflow object with resolved graph nodes and child symbols."""
    steps, layers_inv = _collect_curated_steps(bp["steps"], graph, nodes_by_id, sources)
    if not steps:
        return None

    support = _collect_support_nodes(graph, nodes_by_id, steps)
    all_node_ids = [s["node_id"] for s in steps] + [sp["node_id"] for sp in support]

    root_step = steps[0]
    return {
        "id": bp["id"],
        "title": bp["title"],
        "category": bp.get("category", "General"),
        "root_node": root_step["symbol"],
        "root_id": root_step["node_id"],
        "file": root_step["file"],
        "layer_id": root_step["layer_id"],
        "layer": root_step["layer"],
        "summary": bp["summary"],
        "step_count": len(steps),
        "layers_involved": layers_inv,
        "node_ids": all_node_ids,
        "steps": steps,
        "support": support,
    }



def _resolved_ratio(steps: List[Dict[str, Any]], nodes_by_id: Dict[str, Dict[str, Any]]) -> float:
    """How much of a blueprint actually exists in this repository."""
    if not steps:
        return 0.0
    real = sum(1 for s in steps if s["node_id"] in nodes_by_id)
    return real / len(steps)


def extract_visualizer_workflows(
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    sources: SourceIndex,
    max_workflows: int = 20,
) -> List[Dict[str, Any]]:
    """Workflows for this repository: the curated ones first, then what we find.

    The blueprints describe TLDRGraph's own journeys, so on any other repository
    they resolve to nothing and are dropped. Discovery then reads the call graph
    and finds that repository's real entry points instead, which is what makes
    the workflow view work on a JavaScript, TypeScript or Go project.
    """
    workflows: List[Dict[str, Any]] = []

    for bp in CURATED_BLUEPRINTS:
        wf = _build_curated_workflow(bp, graph, nodes_by_id, sources)
        # A blueprint that barely matches is describing a different codebase.
        # Half its steps resolving is the line: a foreign repo scores near zero.
        if wf and _resolved_ratio(wf["steps"], nodes_by_id) >= 0.5 and len(workflows) < max_workflows:
            workflows.append(wf)

    if len(workflows) < max_workflows:
        def format_step(node_id: str, step_number: int) -> Dict[str, Any]:
            return _format_step_record(node_id, graph, nodes_by_id, sources, step_number, "")

        def collect_support(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return _collect_support_nodes(graph, nodes_by_id, steps)

        found = discover_workflows(
            graph, nodes_by_id, format_step, collect_support,
            limit=max_workflows - len(workflows),
        )
        taken = {w["root_id"] for w in workflows}
        workflows.extend(w for w in found if w["root_id"] not in taken)

    return workflows
