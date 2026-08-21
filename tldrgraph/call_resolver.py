"""
Cross-layer call target resolution and vector floor calculation for TLDRGraph.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
import networkx as nx

from .vector_store import BACKEND_TFIDF, SCORE_FLOORS, LocalVectorStore

BRIDGE_SCORE_FLOOR = SCORE_FLOORS[BACKEND_TFIDF]


def bridge_score_floor(vector_store: Any) -> float:
    try:
        return float(vector_store.score_floor)
    except Exception:
        pass
    try:
        backend = getattr(vector_store, "backend", None)
        return float(SCORE_FLOORS.get(backend, BRIDGE_SCORE_FLOOR))
    except Exception:
        return BRIDGE_SCORE_FLOOR


def placeholder_summary(layer: str, label: str, file_path: str) -> str:
    return f"{layer}: {label} located at {file_path}"


def _parse_dict_target_spec(
    call_target: Dict[str, Any], source_id: str, graph: nx.DiGraph
) -> Tuple[Optional[str], str, str, bool]:
    tgt_id = call_target.get("id") or call_target.get("target_id")
    if tgt_id and graph.has_node(str(tgt_id)) and str(tgt_id) != source_id:
        return str(tgt_id), "", "", True
    target_file = str(call_target.get("file") or call_target.get("path") or "")
    target_symbol = str(call_target.get("symbol") or call_target.get("name") or call_target.get("label") or "")
    return None, target_file, target_symbol, False


def _parse_target_spec(
    call_target: Any, source_id: str, graph: nx.DiGraph
) -> Tuple[Optional[str], str, str, bool]:
    if isinstance(call_target, dict):
        return _parse_dict_target_spec(call_target, source_id, graph)

    target_str = str(call_target).strip()
    if graph.has_node(target_str) and target_str != source_id:
        return target_str, "", "", True

    if ":" in target_str and not target_str.startswith("http:") and not target_str.startswith("https:"):
        parts = target_str.split(":", 1)
        return None, parts[0].strip(), parts[1].strip(), False

    return None, "", target_str, False


def _find_symbol_candidates(
    graph: nx.DiGraph,
    target_symbol: str,
    target_file: str,
    source_id: str,
) -> List[Tuple[str, Dict[str, Any]]]:
    candidates: List[Tuple[str, Dict[str, Any]]] = []
    symbol_lower = target_symbol.lower()
    for nid, data in graph.nodes(data=True):
        if nid == source_id or target_file and target_file not in data.get("file", ""):
            continue
        label = data.get("label", "")
        if label == target_symbol or label.lower() == symbol_lower:
            candidates.append((nid, data))
        elif label.rstrip("()") == target_symbol or label.rstrip("()").lower() == symbol_lower:
            candidates.append((nid, data))
    return candidates


def _rank_candidate(cand: Tuple[str, Dict[str, Any]], target_symbol: str, source_dir: str) -> Tuple[int, int, int]:
    _, data = cand
    c_file = data.get("file", "")
    label = data.get("label", "")
    c_dir = os.path.dirname(c_file)
    case_score = 2 if label == target_symbol else 1
    dead_code_score = 0 if data.get("dead_code_status") == "candidate" else 2
    dir_score = 2 if (source_dir and c_dir == source_dir) else 0
    return (dead_code_score, case_score, dir_score)


def _resolve_symbol_match(
    graph: nx.DiGraph, target_symbol: str, target_file: str, source_id: str, source_dir: str
) -> Optional[str]:
    candidates = _find_symbol_candidates(graph, target_symbol, target_file, source_id)
    if not candidates:
        return None
    candidates.sort(key=lambda c: _rank_candidate(c, target_symbol, source_dir), reverse=True)
    return candidates[0][0]


def _search_vector_store(
    vector_store: LocalVectorStore, query: str, source_id: str, floor: float
) -> Tuple[Optional[str], float]:
    matches = vector_store.search(query, top_k=1)
    if not matches:
        return None, 0.0
    tgt_doc, score = matches[0]
    tgt_id = tgt_doc.get("id")
    if tgt_id and tgt_id != source_id and score >= floor:
        return tgt_id, float(score)
    return None, 0.0


def resolve_call_target(
    graph: nx.DiGraph,
    vector_store: LocalVectorStore,
    call_target: Any,
    source_id: str,
    floor: Optional[float] = None,
) -> Tuple[Optional[str], float]:
    if call_target is None:
        return None, 0.0

    threshold = bridge_score_floor(vector_store) if floor is None else floor
    exact_id, target_file, target_symbol, is_exact = _parse_target_spec(call_target, source_id, graph)
    if is_exact and exact_id:
        return exact_id, 1.0
    if not target_symbol and not target_file:
        return None, 0.0

    source_file = graph.nodes[source_id].get("file", "") if graph.has_node(source_id) else ""
    source_dir = os.path.dirname(source_file)

    if target_symbol:
        sym_match = _resolve_symbol_match(graph, target_symbol, target_file, source_id, source_dir)
        if sym_match:
            return sym_match, 1.0

    search_query = target_symbol or str(call_target)
    return _search_vector_store(vector_store, search_query, source_id, threshold)
