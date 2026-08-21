"""
Deterministic cross-layer seam extractors for TLDRGraph.
"""

from __future__ import annotations

import bisect
import os
import re
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .extractors_client import (
    collect_frontend_calls,
    extract_frontend_calls,
    iter_source_files,
)
from .extractors_prisma import (
    DB_MODEL_RELATION,
    PRISMA_MODEL_NODE_PREFIX,
    accessor_to_model_name,
    build_db_model_edges,
    build_relation_map,
    collect_prisma_calls,
    collect_prisma_models,
    extract_prisma_calls,
    extract_prisma_models,
    prisma_model_node_id,
)
from .extractors_route import (
    CALLS_ENDPOINT_RELATION,
    ENDPOINT_NODE_PREFIX,
    ENDPOINT_NODE_TYPE,
    HANDLED_BY_RELATION,
    HTTP_ROUTE_RELATION,
    ROUTE_PARAM,
    build_endpoint_edges,
    build_http_route_edges,
    collect_backend_routes,
    collect_endpoints,
    endpoint_label,
    endpoint_node_id,
    extract_backend_routes,
    match_http_routes,
    normalize_http_method,
    normalize_route_path,
    resolve_route_handler,
)

__all__ = [
    "HTTP_ROUTE_RELATION",
    "DB_MODEL_RELATION",
    "CALLS_ENDPOINT_RELATION",
    "HANDLED_BY_RELATION",
    "PRISMA_MODEL_NODE_PREFIX",
    "ENDPOINT_NODE_PREFIX",
    "ENDPOINT_NODE_TYPE",
    "normalize_route_path",
    "normalize_http_method",
    "prisma_model_node_id",
    "endpoint_node_id",
    "endpoint_label",
    "collect_endpoints",
    "build_endpoint_edges",
    "resolve_route_handler",
    "accessor_to_model_name",
    "extract_frontend_calls",
    "extract_backend_routes",
    "extract_prisma_models",
    "extract_prisma_calls",
    "build_relation_map",
    "match_http_routes",
    "NodeIndex",
    "iter_source_files",
    "collect_prisma_models",
    "collect_frontend_calls",
    "collect_backend_routes",
    "collect_prisma_calls",
    "build_http_route_edges",
    "build_db_model_edges",
]

_route_target = resolve_route_handler


def _parse_source_location(source_location: Any) -> Optional[int]:
    if source_location is None:
        return None
    text = str(source_location).strip()
    if not text:
        return None
    if text[0] in "Ll":
        text = text[1:]
    text = text.split("-")[0].split(":")[0].strip()
    try:
        return int(text)
    except ValueError:
        return None


class NodeIndex:
    """Maps (file, line) to the graph node that owns that line."""

    def __init__(self, node_records: Iterable[Mapping[str, Any]]):
        self._lines: Dict[str, List[int]] = {}
        self._ids: Dict[str, List[str]] = {}
        self._file_level: Dict[str, str] = {}
        self._by_name: Dict[str, Dict[str, str]] = {}

        staged: Dict[str, List[Tuple[int, str]]] = {}
        for record in node_records:
            node_id = record.get("id")
            file_path = record.get("file") or ""
            if not node_id or not file_path:
                continue
            line = _parse_source_location(record.get("source_location"))

            label = str(record.get("label") or "")
            normalized = label.strip().lstrip(".").rstrip(")").rstrip("(").lower()
            if normalized:
                self._by_name.setdefault(file_path, {}).setdefault(normalized, str(node_id))

            basename = os.path.basename(file_path)
            if label == basename or label == os.path.splitext(basename)[0]:
                self._file_level[file_path] = str(node_id)
            else:
                self._file_level.setdefault(file_path, str(node_id))

            if line is None:
                continue
            staged.setdefault(file_path, []).append((line, str(node_id)))

        for file_path, entries in staged.items():
            entries.sort(key=lambda item: item[0])
            self._lines[file_path] = [line for line, _ in entries]
            self._ids[file_path] = [node_id for _, node_id in entries]
            self._file_level.setdefault(file_path, entries[0][1])

    def files(self) -> Iterable[str]:
        return set(self._lines) | set(self._file_level)

    def file_node(self, file_path: str) -> Optional[str]:
        return self._file_level.get(file_path)

    def owner_of(self, file_path: str, line: int) -> Optional[str]:
        lines = self._lines.get(file_path)
        if lines:
            position = bisect.bisect_right(lines, line)
            if position:
                return self._ids[file_path][position - 1]
        return self._file_level.get(file_path)

    def node_named(self, file_path: str, name: str) -> Optional[str]:
        if not name:
            return None
        return (self._by_name.get(file_path) or {}).get(name.strip().lower())
