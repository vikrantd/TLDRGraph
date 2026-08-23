"""
Display-label disambiguation for TLDRGraph.
"""

from __future__ import annotations

from collections import Counter
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

OWNER_RELATIONS = frozenset({"method", "contains", "defines"})
_MAX_PATH_DEPTH = 3


def _basename(file_path: Optional[str]) -> str:
    if not file_path:
        return ""
    normalized = file_path.replace("\\", "/").strip("/")
    return os.path.basename(normalized)


def _path_suffix(file_path: Optional[str], depth: int) -> str:
    if not file_path:
        return ""
    parts = [p for p in file_path.replace("\\", "/").strip("/").split("/") if p]
    if not parts:
        return ""
    return "/".join(parts[-depth:])


def qualify(label: str, owner_label: str) -> str:
    label = (label or "").strip()
    owner_label = (owner_label or "").strip()
    if not owner_label:
        return label
    if not label:
        return owner_label
    if label.startswith(f"{owner_label}.") or label.startswith(f"{owner_label}::") or label.startswith(f"{owner_label}#"):
        return label
    if label.startswith("."):
        return f"{owner_label}{label}"
    return f"{owner_label}.{label}"


def _owner_is_useful(owner: Mapping[str, Any], node: Mapping[str, Any]) -> bool:
    owner_label = str(owner.get("label") or "").strip()
    if not owner_label:
        return False
    basename = _basename(owner.get("file"))
    if owner_label == basename or owner_label == os.path.splitext(basename)[0]:
        return False
    return "." not in os.path.splitext(owner_label)[0] or not os.path.splitext(owner_label)[1]


def _index_nodes(nodes: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, Mapping[str, Any]], List[str]]:
    node_by_id: Dict[str, Mapping[str, Any]] = {}
    order: List[str] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in node_by_id:
            continue
        node_by_id[node_id] = node
        order.append(node_id)
    return node_by_id, order


def _build_owner_map(edges: Iterable[Mapping[str, Any]], node_by_id: Dict[str, Mapping[str, Any]]) -> Dict[str, str]:
    owner_of: Dict[str, str] = {}
    for edge in edges:
        if str(edge.get("relation") or "") not in OWNER_RELATIONS:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target or source == target or target not in node_by_id or source not in node_by_id:
            continue
        existing = owner_of.get(target)
        if existing is None or source < existing:
            owner_of[target] = source
    return owner_of


def _qualify_with_owners(order: List[str], node_by_id: Dict[str, Mapping[str, Any]], owner_of: Dict[str, str]) -> Dict[str, str]:
    display: Dict[str, str] = {}
    for node_id in order:
        node = node_by_id[node_id]
        label = str(node.get("label") or node_id)
        owner_id = owner_of.get(node_id)
        owner = node_by_id.get(owner_id) if owner_id else None
        if owner is not None and _owner_is_useful(owner, node):
            display[node_id] = qualify(label, str(owner.get("label") or ""))
        else:
            display[node_id] = label
    return display


def _disambiguate_paths(order: List[str], node_by_id: Dict[str, Mapping[str, Any]], display: Dict[str, str]) -> None:
    qualified = dict(display)
    for depth in range(1, _MAX_PATH_DEPTH + 1):
        collisions = Counter(display.values())
        ambiguous = [nid for nid in order if collisions[display[nid]] > 1]
        if not ambiguous:
            break
        for node_id in ambiguous:
            suffix = _path_suffix(node_by_id[node_id].get("file"), depth)
            if suffix:
                display[node_id] = f"{qualified[node_id]} ({suffix})"


def build_display_labels(nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    node_by_id, order = _index_nodes(nodes)
    owner_of = _build_owner_map(edges, node_by_id)
    display = _qualify_with_owners(order, node_by_id, owner_of)
    _disambiguate_paths(order, node_by_id, display)
    return display


def display_label_of(node: Mapping[str, Any]) -> str:
    return str(node.get("display_label") or node.get("label") or node.get("id") or "")
