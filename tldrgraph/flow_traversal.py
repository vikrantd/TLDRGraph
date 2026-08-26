"""
Traversal and node resolution algorithms for FlowEngine.
"""

from __future__ import annotations

import heapq
import itertools
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from .hierarchy import is_test_node
from .layers import get_registry, layer_id_of
from .vector_store import LocalVectorStore

try:
    from .graph_loader import BRIDGE_RELATIONS as _LOADER_BRIDGE_RELATIONS
except Exception:
    _LOADER_BRIDGE_RELATIONS = set()

DETERMINISTIC_BRIDGE_RELATIONS = {"http_route_link", "db_model_link"}
BRIDGE_RELATIONS: Set[str] = set(_LOADER_BRIDGE_RELATIONS) | DETERMINISTIC_BRIDGE_RELATIONS

BRIDGE_EDGE_COST = 0.0
CROSS_LAYER_EDGE_COST = 1.0
SAME_LAYER_EDGE_COST = 5.0
DEFAULT_MAX_STEPS = 25
CANDIDATE_POOL = 25
MATCH_MARGIN = 0.15

CONTAINER_TYPES = {
    "file", "module", "directory", "dir", "folder", "package",
    "container", "devops_config", "concept",
}

SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".go", ".rb",
    ".php", ".cs", ".rs", ".kt", ".swift", ".scala", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".m", ".mm", ".sql", ".prisma", ".graphql", ".proto", ".yml",
    ".yaml", ".json", ".toml", ".ini", ".cfg", ".env", ".md", ".sh", ".bash",
    ".tf", ".dockerfile", ".vue", ".svelte", ".html", ".css", ".scss",
}


def normalize_label(text: str) -> str:
    """Collapses a label/query to a comparable identifier key."""
    t = (text or "").strip().lower()
    t = re.sub(r"\(\s*\)\s*$", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def label_keys(data: Dict[str, Any]) -> Set[str]:
    """Every normalized key a node answers to on an exact-name query."""
    label = data.get("label") or ""
    keys = {normalize_label(label)}
    stem, ext = os.path.splitext(label)
    if ext and stem:
        keys.add(normalize_label(stem))
    keys.discard("")
    return keys


def is_container_node(node_id: str, data: Dict[str, Any]) -> bool:
    """True for file / module / directory nodes."""
    node_type = str(data.get("type") or "").lower()
    if node_type in CONTAINER_TYPES:
        return True
    label = data.get("label") or ""
    file_path = data.get("file") or ""
    if label and file_path and os.path.basename(file_path) == label:
        return True
    ext = os.path.splitext(label)[1].lower()
    return bool(ext and ext in SOURCE_EXTENSIONS)


def node_preference(graph: nx.DiGraph, node_id: str) -> float:
    """Desirability of a node as a trace root. Higher is better."""
    if not graph.has_node(node_id):
        return -1.0
    data = graph.nodes[node_id]
    score = 0.0
    if not is_container_node(node_id, data):
        score += 2.0
    if graph.out_degree(node_id) > 0:
        score += 1.0
    has_bridge = any(d.get("relation") in BRIDGE_RELATIONS for _, _, d in graph.out_edges(node_id, data=True))
    if has_bridge:
        score += 0.5
    return score


def resolve_node_id(
    graph: nx.DiGraph,
    vector_store: LocalVectorStore,
    query: str,
) -> Optional[Tuple[str, float, Dict[str, Any]]]:
    """Resolves query to (node_id, score, node_data)."""
    if not query:
        return None
    if graph.has_node(query):
        return query, 1.0, dict(graph.nodes[query])

    raw_matches = vector_store.search(query, top_k=CANDIDATE_POOL)
    matches = [(doc, s) for doc, s in raw_matches if s >= vector_store.score_floor]
    score_by_id = {doc["id"]: score for doc, score in matches}
    norm_query = normalize_label(query)

    if norm_query and not re.search(r"\s", query.strip()):
        exact = [nid for nid, data in graph.nodes(data=True) if norm_query in label_keys(data)]
        if exact:
            best = max(exact, key=lambda nid: (node_preference(graph, nid), score_by_id.get(nid, 0.0)))
            return best, score_by_id.get(best, 1.0), dict(graph.nodes[best])

    if not matches:
        return None


    top_doc, top_score = matches[0]
    floor = top_score * (1.0 - MATCH_MARGIN)
    near = [(doc, s) for doc, s in matches if s >= floor and graph.has_node(doc["id"])]
    if near:
        best_doc, best_score = max(near, key=lambda ds: (node_preference(graph, ds[0]["id"]), ds[1]))
        return best_doc["id"], best_score, dict(graph.nodes[best_doc["id"]])

    if not graph.has_node(top_doc["id"]):
        return None
    return top_doc["id"], top_score, dict(graph.nodes[top_doc["id"]])


def edge_cost(graph: nx.DiGraph, src: str, tgt: str, data: Dict[str, Any]) -> float:
    """Cost of traversing an edge."""
    if data.get("relation") in BRIDGE_RELATIONS:
        return BRIDGE_EDGE_COST
    src_data = graph.nodes.get(src, {})
    tgt_data = graph.nodes.get(tgt, {})
    if layer_id_of(src_data) != layer_id_of(tgt_data):
        return CROSS_LAYER_EDGE_COST
    return SAME_LAYER_EDGE_COST


def _expand_neighbors(
    graph: nx.DiGraph,
    node_id: str,
    cost: float,
    settled: Set[str],
    best_cost: Dict[str, float],
    arrival: Dict[str, Dict[str, Any]],
    heap: List[Tuple[float, int, str]],
    counter: Any,
) -> None:
    if not graph.has_node(node_id):
        return
    for _, neighbor, data in graph.out_edges(node_id, data=True):
        if neighbor in settled:
            continue
        new_cost = cost + edge_cost(graph, node_id, neighbor, data)
        if neighbor not in best_cost or new_cost < best_cost[neighbor]:
            best_cost[neighbor] = new_cost
            relation = data.get("relation", "calls")
            arrival[neighbor] = {
                "via_relation": relation,
                "via_bridge": relation in BRIDGE_RELATIONS,
                "from": node_id,
            }
            heapq.heappush(heap, (new_cost, next(counter), neighbor))


def _build_walk_steps(
    collected: List[Tuple[str, float, int]],
    graph: nx.DiGraph,
    arrival: Dict[str, Dict[str, Any]],
    format_node_step_fn: Any,
) -> List[Dict[str, Any]]:
    registry = get_registry()
    collected.sort(key=lambda item: (
        registry.order(layer_id_of(graph.nodes.get(item[0], {})) or registry.utility_id),
        item[1],
        item[2]
    ))
    steps: List[Dict[str, Any]] = []
    for node_id, cost, _seq in collected:
        step = format_node_step_fn(node_id)
        step["cost"] = round(float(cost), 3)
        meta = arrival.get(node_id)
        if meta:
            step["via_relation"] = meta["via_relation"]
            step["via_bridge"] = bool(meta["via_bridge"])
            step["from"] = meta["from"]
        else:
            step["via_relation"] = None
            step["via_bridge"] = False
            step["from"] = None
        steps.append(step)
    return steps


def bridge_aware_walk(
    graph: nx.DiGraph,
    start_node_id: str,
    max_steps: int,
    format_node_step_fn: Any,
) -> List[Dict[str, Any]]:
    """Cheapest-cost forward expansion from start_node_id."""
    counter = itertools.count()
    heap: List[Tuple[float, int, str]] = [(0.0, next(counter), start_node_id)]
    best_cost: Dict[str, float] = {start_node_id: 0.0}
    arrival: Dict[str, Dict[str, Any]] = {}
    collected: List[Tuple[str, float, int]] = []
    settled: Set[str] = set()

    while heap and len(collected) < max_steps:
        cost, seq, node_id = heapq.heappop(heap)
        if node_id in settled:
            continue
        settled.add(node_id)
        collected.append((node_id, cost, seq))
        _expand_neighbors(graph, node_id, cost, settled, best_cost, arrival, heap, counter)

    return _build_walk_steps(collected, graph, arrival, format_node_step_fn)
