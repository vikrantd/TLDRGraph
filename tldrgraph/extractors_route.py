"""
Route extraction and endpoint matching algorithms for TLDRGraph.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HTTP_ROUTE_RELATION = "http_route_link"
CALLS_ENDPOINT_RELATION = "calls_endpoint"
HANDLED_BY_RELATION = "handled_by"
ENDPOINT_NODE_PREFIX = "endpoint_"
ENDPOINT_NODE_TYPE = "endpoint"
ROUTE_PARAM = ":param"

_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\$\{[^{}]*\}")

_CONTROLLER_RE = re.compile(
    r"@Controller\s*\(\s*(?:(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote))?"
)

_ROUTE_DECORATOR_RE = re.compile(
    r"@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All)\s*"
    r"\(\s*(?:(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote))?"
)

_HANDLER_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:async\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^<>]*>)?\s*\("
)


def normalize_http_method(method: str) -> str:
    """del -> delete; everything else lowercased."""
    method = (method or "").strip().lower()
    return "delete" if method == "del" else method


def _strip_scheme_and_prefix(path: str) -> str:
    if path.startswith("${"):
        close = path.find("}")
        if close != -1:
            path = path[close + 1:]
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", path)
    if match:
        path = path[match.end():]
    for cut in ("?", "#"):
        idx = path.find(cut)
        if idx != -1:
            path = path[:idx]
    return _TEMPLATE_PLACEHOLDER_RE.sub(ROUTE_PARAM, path)


def _normalize_route_segment(segment: str) -> Optional[str]:
    seg = segment.strip()
    if not seg:
        return None
    if (
        seg.startswith(":") or seg == "*" or ROUTE_PARAM in seg
        or seg.startswith("$") or seg.startswith("[") or seg.isdigit()
    ):
        return ROUTE_PARAM
    return seg.lower()


def normalize_route_path(raw: str) -> str:
    """Collapse URL literal or decorator path into single comparable form."""
    if raw is None:
        return "/"
    path = _strip_scheme_and_prefix(raw.strip())
    raw_segments = [_normalize_route_segment(s) for s in path.split("/")]
    segments = [s for s in raw_segments if s is not None]

    if segments and segments[0] == "api":
        segments = segments[1:]

    return "/" + "/".join(segments)


def endpoint_node_id(method: str, path: str) -> str:
    """Stable id for one endpoint identity: GET /auth/me -> endpoint_get_auth_me."""
    method = normalize_http_method(method) or "get"
    slug = re.sub(r"[^a-z0-9]+", "_", (path or "/").lower()).strip("_")
    return f"{ENDPOINT_NODE_PREFIX}{method}_{slug or 'root'}"


def endpoint_label(method: str, path: str) -> str:
    """('get', '/auth/me') -> 'GET /auth/me'."""
    return f"{normalize_http_method(method).upper()} {path or '/'}"


def _find_handler_name(lines: Sequence[str], decorator_index: int, lookahead: int = 25) -> Optional[str]:
    for offset in range(1, lookahead + 1):
        idx = decorator_index + offset
        if idx >= len(lines):
            return None
        line = lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("@") or stripped.startswith("//")                 or stripped.startswith("/*") or stripped.startswith("*"):
            continue
        match = _HANDLER_RE.match(line)
        if match:
            name = match.group("name")
            if name in {"if", "for", "while", "switch", "catch", "return", "constructor"}:
                return None
            return name
        return None
    return None


def extract_backend_routes(file_path: str, content: str) -> List[Dict[str, Any]]:
    """Backend route declarations: @Controller composed with each method decorator."""
    lines = content.splitlines()
    routes: List[Dict[str, Any]] = []
    current_base = ""

    for index, line in enumerate(lines):
        controller = _CONTROLLER_RE.search(line)
        if controller:
            current_base = controller.group("path") or ""
            continue

        decorator = _ROUTE_DECORATOR_RE.search(line)
        if not decorator or line.strip().startswith("//") or line.strip().startswith("*"):
            continue

        route_path = decorator.group("path") or ""
        combined = f"{current_base}/{route_path}"
        routes.append({
            "file": file_path,
            "line": index + 1,
            "method": normalize_http_method(decorator.group("method")),
            "base": current_base,
            "raw_path": route_path,
            "path": normalize_route_path(combined),
            "handler": _find_handler_name(lines, index),
        })

    return routes


def _paths_compatible(call_path: str, route_path: str) -> bool:
    call_segments = call_path.strip("/").split("/")
    route_segments = route_path.strip("/").split("/")
    if len(call_segments) != len(route_segments):
        return False
    for call_segment, route_segment in zip(call_segments, route_segments):
        if route_segment == ROUTE_PARAM or call_segment == route_segment:
            continue
        return False
    return True


def match_http_routes(
    calls: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Pair each frontend call site with backend route(s) that serve it."""
    exact: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    by_method: Dict[str, List[Mapping[str, Any]]] = {}
    for route in routes:
        exact.setdefault((route["method"], route["path"]), []).append(route)
        by_method.setdefault(route["method"], []).append(route)

    pairs: List[Tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for call in calls:
        key = (call["method"], call["path"])
        matched = exact.get(key)
        if not matched:
            matched = [
                route for route in by_method.get(call["method"], [])
                if _paths_compatible(call["path"], route["path"])
            ]
        for route in matched:
            pairs.append((call, route))
    return pairs


def resolve_route_handler(index: Any, route: Mapping[str, Any]) -> Optional[str]:
    """Resolve a route decorator to the handler node."""
    handler = route.get("handler")
    if handler:
        node_id = index.node_named(route["file"], handler)
        if node_id:
            return node_id
        node_id = index.node_named(route["file"], f"{handler}()")
        if node_id:
            return node_id
    return index.owner_of(route["file"], route["line"] + 1)


def build_http_route_edges(
    calls: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
    index: Any,
) -> List[Dict[str, Any]]:
    """Frontend call site -> backend handler edge records."""
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for call, route in match_http_routes(calls, routes):
        source = index.owner_of(call["file"], call["line"])
        if not source:
            continue
        target = resolve_route_handler(index, route)
        if not target or target == source:
            continue
        key = (source, target)
        if key in edges:
            continue
        edges[key] = {
            "source": source,
            "target": target,
            "relation": HTTP_ROUTE_RELATION,
            "confidence": 1.0,
            "method": call["method"],
            "path": call["path"],
            "route_path": route["path"],
        }
    return list(edges.values())


def collect_endpoints(
    calls: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Fold route declarations and call sites into one record per endpoint."""
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for route in routes:
        key = (route["method"], route["path"])
        record = grouped.get(key)
        if record is None:
            record = grouped[key] = {
                "id": endpoint_node_id(*key),
                "method": key[0],
                "path": key[1],
                "label": endpoint_label(*key),
                "routes": [],
                "call_sites": [],
            }
        record["routes"].append(dict(route))

    for call, route in match_http_routes(calls, routes):
        record = grouped.get((route["method"], route["path"]))
        if record is None:
            continue
        site = (call["file"], call["line"])
        if any((c["file"], c["line"]) == site for c in record["call_sites"]):
            continue
        record["call_sites"].append(dict(call))

    for record in grouped.values():
        record["routes"].sort(key=lambda r: (r.get("file") or "", r.get("line") or 0))
        record["call_sites"].sort(key=lambda c: (c.get("file") or "", c.get("line") or 0))
        primary = record["routes"][0]
        record["file"] = primary.get("file") or ""
        record["line"] = primary.get("line") or 0
        record["handlers"] = [r["handler"] for r in record["routes"] if r.get("handler")]

    return [grouped[key] for key in sorted(grouped)]


def build_endpoint_edges(
    endpoints: Sequence[Mapping[str, Any]],
    index: Any,
) -> List[Dict[str, Any]]:
    """Builds calls_endpoint and handled_by edges for endpoints."""
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for endpoint in endpoints:
        endpoint_id = endpoint["id"]
        for call in endpoint.get("call_sites") or []:
            source = index.owner_of(call["file"], call["line"])
            if not source or source == endpoint_id:
                continue
            edges.setdefault((source, endpoint_id), {
                "source": source,
                "target": endpoint_id,
                "relation": CALLS_ENDPOINT_RELATION,
                "confidence": 1.0,
                "method": endpoint["method"],
                "path": endpoint["path"],
                "call_file": call["file"],
                "call_line": call["line"],
            })

        for route in endpoint.get("routes") or []:
            target = resolve_route_handler(index, route)
            if not target or target == endpoint_id:
                continue
            edges.setdefault((endpoint_id, target), {
                "source": endpoint_id,
                "target": target,
                "relation": HANDLED_BY_RELATION,
                "confidence": 1.0,
                "method": endpoint["method"],
                "path": endpoint["path"],
                "handler": route.get("handler") or "",
            })
    return list(edges.values())

def collect_backend_routes(root_dir: str) -> List[Dict[str, Any]]:
    from .extractors_client import iter_source_files
    routes: List[Dict[str, Any]] = []
    for relative, file_content in iter_source_files(root_dir):
        if "@Controller" not in file_content:
            continue
        routes.extend(extract_backend_routes(relative.replace(os.sep, "/"), file_content))
    return routes
