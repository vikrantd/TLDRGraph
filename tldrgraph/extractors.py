"""
Deterministic cross-layer seam extractors for TLDRGraph.

graphify only emits AST edges (imports / calls / contains / references), so it
structurally cannot cross a process boundary: ``api.get('/auth/me')`` in the
frontend is not an *import* of the NestJS controller that serves it, and
``prisma.jhPensionApplication.findMany()`` is not an import of a table declared
in ``schema.prisma``. Both seams are, however, perfectly deterministic in the
source text.

This module is deliberately pure: every function takes file contents (or a
directory to walk) and returns plain records. Nothing here mutates a graph, so
all of it is unit-testable without building one.

Two seams are extracted:

``http_route_link``  Layer 1 (UI) -> Layer 2 (API)
    frontend call sites with a literal route string, matched against backend
    route decorators (``@Controller('base')`` composed with ``@Get(':id')``).

``db_model_link``    caller -> Layer 4 (Data)
    ``prisma.<accessor>.<op>(`` call sites, matched against ``model X { }``
    blocks parsed straight out of ``schema.prisma``.
"""

from __future__ import annotations

import bisect
import os
import re
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

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

#: Relation names for the two deterministic seams. These are intentionally NOT
#: part of graph_loader.BRIDGE_RELATIONS: they are re-derived from source on
#: every scan, so carrying them forward from a snapshot would let them go stale.
HTTP_ROUTE_RELATION = "http_route_link"
DB_MODEL_RELATION = "db_model_link"

#: The two halves of the endpoint seam. An *endpoint* is a first-class node --
#: one per normalized ``METHOD /path`` -- so a route has an identity of its own
#: instead of existing only as an edge label. That is what makes "components
#: reach services through endpoints" literally true in the graph:
#:
#:     frontend caller --calls_endpoint--> endpoint --handled_by--> handler
#:
#: Both are re-derived from source on every scan, exactly like the two relations
#: above, and for the same reason must never join BRIDGE_RELATIONS.
CALLS_ENDPOINT_RELATION = "calls_endpoint"
HANDLED_BY_RELATION = "handled_by"

PRISMA_MODEL_NODE_PREFIX = "prisma_model_"

#: Id prefix and node ``type`` of a synthesized endpoint node.
ENDPOINT_NODE_PREFIX = "endpoint_"
ENDPOINT_NODE_TYPE = "endpoint"

#: Placeholder that every dynamic route segment normalizes to.
ROUTE_PARAM = ":param"

#: Directories never worth walking.
_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "out", "coverage",
    ".turbo", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
    "graphify-out", ".tldrgraph", ".idea", ".vscode",
}

_SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

#: Identifiers that plausibly denote an HTTP client instance.
_CLIENT_IDENTS = r"(?:api|apiClient|apiInstance|axios|axiosInstance|http|httpClient|client|request)"

#: ``api.get<Foo[]>("/x")`` / ``api.post(`/y/${id}`, body)`` / ``api.del('/z')``
_API_CALL_RE = re.compile(
    r"(?<![\w.])" + _CLIENT_IDENTS + r"\s*\.\s*"
    r"(?P<method>get|post|put|patch|delete|del|head|options)\s*"
    r"(?:<[^<>;{}]*>\s*)?"                       # optional TS generic argument
    r"\(\s*"
    r"(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote)"
)

#: ``fetch('/api/x', { method: 'POST' })``
_FETCH_CALL_RE = re.compile(
    r"(?<![\w.])fetch\s*\(\s*(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote)"
)

_FETCH_METHOD_RE = re.compile(r"method\s*:\s*['\"`](?P<method>[A-Za-z]+)['\"`]")

#: ``@Controller('applications')`` / ``@Controller()``
_CONTROLLER_RE = re.compile(
    r"@Controller\s*\(\s*(?:(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote))?"
)

#: ``@Get(':id/status')`` / ``@Post()``
_ROUTE_DECORATOR_RE = re.compile(
    r"@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All)\s*"
    r"\(\s*(?:(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote))?"
)

#: A method signature line inside a class body, used to name the route handler.
_HANDLER_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:async\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^<>]*>)?\s*\("
)

#: ``model JhPensionApplication {``
_PRISMA_MODEL_RE = re.compile(r"^[ \t]*model[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\{", re.MULTILINE)

#: ``id String @id`` -- a column declaration inside a model block.
_PRISMA_FIELD_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z_\[\]][^\s]*)")

#: ``this.prisma.user.update(`` / ``tx.jhPensionApplication.create(``
_PRISMA_CALL_RE = re.compile(
    r"(?<![\w.])(?:this\s*\.\s*)?(?P<client>prisma|tx|trx|transaction|db|dbClient|prismaClient)"
    r"\s*\.\s*(?P<accessor>[a-z][A-Za-z0-9_]*)\s*\.\s*(?P<op>[A-Za-z0-9_]+)\s*\("
)

#: Prisma delegate operations. Gating on this set keeps ``tx.res.status()``
#: style false positives out even before the model-name check.
PRISMA_OPERATIONS = frozenset({
    "findMany", "findUnique", "findFirst", "findUniqueOrThrow", "findFirstOrThrow",
    "create", "createMany", "createManyAndReturn", "update", "updateMany",
    "updateManyAndReturn", "upsert", "delete", "deleteMany", "count",
    "aggregate", "groupBy",
})

#: ``} satisfies Prisma.JhPensionApplicationInclude`` / ``: Prisma.UserSelect``.
#: Large include/select objects are routinely hoisted into a named const and
#: passed to the query by reference, so they never appear inside the call's own
#: parentheses. This anchors such an object back to the model it describes.
_PRISMA_TYPE_REF_RE = re.compile(
    r"Prisma\.(?P<model>[A-Za-z0-9_]+?)(?:Include|Select|Args|FindManyArgs)\b"
)

_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\$\{[^{}]*\}")


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def normalize_http_method(method: str) -> str:
    """``del`` -> ``delete``; everything else lowercased."""
    method = (method or "").strip().lower()
    if method == "del":
        return "delete"
    return method


def normalize_route_path(raw: str) -> str:
    """
    Collapse a frontend URL literal or a backend decorator path into a single
    comparable form.

      ``/applications/${id}``          -> ``/applications/:param``
      ``:id/status``                   -> ``/:param/status``
      ``/admin/users?id=${userId}``    -> ``/admin/users``
      ``${BACKEND_URL}/csrf-token``    -> ``/csrf-token``
      ``/api/master/statuses``         -> ``/master/statuses``

    A leading ``api`` segment is dropped on both sides: the frontend axios
    baseURL and the NestJS ``setGlobalPrefix('api')`` cancel each other out, and
    whichever side happens to spell it must not break the match.
    """
    if raw is None:
        return "/"
    path = raw.strip()

    # Strip an absolute origin, whether literal or interpolated.
    if path.startswith("${"):
        close = path.find("}")
        if close != -1:
            path = path[close + 1:]
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*", path)
    if match:
        path = path[match.end():]

    # Query string and fragment carry no routing information.
    for cut in ("?", "#"):
        idx = path.find(cut)
        if idx != -1:
            path = path[:idx]

    path = _TEMPLATE_PLACEHOLDER_RE.sub(ROUTE_PARAM, path)

    segments: List[str] = []
    for segment in path.split("/"):
        segment = segment.strip()
        if not segment:
            continue
        if segment.startswith(":") or segment == "*" or ROUTE_PARAM in segment \
                or segment.startswith("$") or segment.startswith("[") or segment.isdigit():
            segments.append(ROUTE_PARAM)
        else:
            segments.append(segment.lower())

    if segments and segments[0] == "api":
        segments = segments[1:]

    return "/" + "/".join(segments)


def prisma_model_node_id(model_name: str) -> str:
    return f"{PRISMA_MODEL_NODE_PREFIX}{model_name.lower()}"


def endpoint_node_id(method: str, path: str) -> str:
    """
    Stable id for one endpoint identity: ``GET /auth/me`` -> ``endpoint_get_auth_me``.

    Built from the *normalized* method and path, so every call site that resolves
    to the same route lands on the same id no matter how it spelled the URL.
    ``:param`` segments slugify to ``param``; a literal ``param`` segment would
    collide, which has never occurred in practice and is preferred to an
    unreadable id.
    """
    method = normalize_http_method(method) or "get"
    slug = re.sub(r"[^a-z0-9]+", "_", (path or "/").lower()).strip("_")
    return f"{ENDPOINT_NODE_PREFIX}{method}_{slug or 'root'}"


def endpoint_label(method: str, path: str) -> str:
    """``("get", "/auth/me")`` -> ``"GET /auth/me"``."""
    return f"{normalize_http_method(method).upper()} {path or '/'}"


def accessor_to_model_name(accessor: str) -> str:
    """``jhPensionApplication`` -> ``JhPensionApplication``."""
    if not accessor:
        return ""
    return accessor[0].upper() + accessor[1:]


# --------------------------------------------------------------------------- #
# Per-file extraction (pure functions over content)
# --------------------------------------------------------------------------- #

def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _is_route_literal(path: str) -> bool:
    """Only accept things that actually look like a request path."""
    stripped = path.strip()
    if not stripped:
        return False
    return stripped.startswith("/") or stripped.startswith("${") or "://" in stripped


def extract_frontend_calls(file_path: str, content: str) -> List[Dict[str, Any]]:
    """
    Frontend HTTP call sites with a literal (or template-literal) route.

    Returns records of ``{file, line, method, raw_path, path}`` where ``path``
    is already normalized.
    """
    calls: List[Dict[str, Any]] = []

    for match in _API_CALL_RE.finditer(content):
        raw_path = match.group("path")
        if not _is_route_literal(raw_path):
            continue
        calls.append({
            "file": file_path,
            "line": _line_of(content, match.start()),
            "method": normalize_http_method(match.group("method")),
            "raw_path": raw_path,
            "path": normalize_route_path(raw_path),
            "kind": "api_client",
        })

    for match in _FETCH_CALL_RE.finditer(content):
        raw_path = match.group("path")
        if not _is_route_literal(raw_path):
            continue
        # fetch() defaults to GET; an options object may override it.
        window = content[match.end():match.end() + 300]
        method_match = _FETCH_METHOD_RE.search(window)
        method = normalize_http_method(method_match.group("method")) if method_match else "get"
        calls.append({
            "file": file_path,
            "line": _line_of(content, match.start()),
            "method": method,
            "raw_path": raw_path,
            "path": normalize_route_path(raw_path),
            "kind": "fetch",
        })

    return calls


def _find_handler_name(lines: Sequence[str], decorator_index: int, lookahead: int = 25) -> Optional[str]:
    """
    Walk forward from a route decorator to the method it decorates, skipping
    stacked decorators, comments and blank lines.
    """
    for offset in range(1, lookahead + 1):
        idx = decorator_index + offset
        if idx >= len(lines):
            return None
        line = lines[idx]
        stripped = line.strip()
        if not stripped or stripped.startswith("@") or stripped.startswith("//") \
                or stripped.startswith("/*") or stripped.startswith("*"):
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
    """
    Backend route declarations: ``@Controller('base')`` composed with each
    method decorator below it.

    Returns ``{file, line, method, path, raw_path, base, handler}``.
    """
    lines = content.splitlines()
    routes: List[Dict[str, Any]] = []

    current_base = ""
    for index, line in enumerate(lines):
        controller = _CONTROLLER_RE.search(line)
        if controller:
            current_base = controller.group("path") or ""
            continue

        decorator = _ROUTE_DECORATOR_RE.search(line)
        if not decorator:
            continue
        # Ignore a decorator that appears inside a comment.
        if line.strip().startswith("//") or line.strip().startswith("*"):
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


def extract_prisma_models(content: str) -> List[Dict[str, Any]]:
    """
    Parse ``model X { ... }`` blocks out of a ``schema.prisma``.

    Returns ``{name, line, fields}`` where ``fields`` are the declared column /
    relation names in declaration order.
    """
    lines = content.splitlines()
    models: List[Dict[str, Any]] = []

    for match in _PRISMA_MODEL_RE.finditer(content):
        name = match.group("name")
        start_line = _line_of(content, match.start())

        fields: List[str] = []
        field_types: Dict[str, str] = {}
        for raw_line in lines[start_line:]:
            stripped = raw_line.strip()
            if stripped.startswith("}"):
                break
            if not stripped or stripped.startswith("//") or stripped.startswith("@@"):
                continue
            field_match = _PRISMA_FIELD_RE.match(raw_line)
            if field_match:
                fields.append(field_match.group("name"))
                field_types[field_match.group("name")] = field_match.group("type")

        models.append({
            "name": name,
            "line": start_line,
            "fields": fields,
            "field_types": field_types,
        })

    return models


def build_relation_map(models: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    ``{ModelName: {relationFieldName: TargetModelName}}``.

    A Prisma field whose declared type resolves to another model is a relation.
    This is what makes nested access visible: ``prisma.jhPensionApplication
    .findUnique({ include: { nominees: true } })`` never names
    ``JhApplicationNominee``, yet it unmistakably reads that table.
    """
    by_name = {model["name"].lower(): model["name"] for model in models}
    relation_map: Dict[str, Dict[str, str]] = {}

    for model in models:
        relations: Dict[str, str] = {}
        for field_name, raw_type in (model.get("field_types") or {}).items():
            base_type = raw_type.rstrip("?").rstrip("[]").rstrip("?").strip()
            target = by_name.get(base_type.lower())
            if target and target != model["name"]:
                relations[field_name] = target
            elif target:
                relations[field_name] = target
        relation_map[model["name"]] = relations

    return relation_map


def _balanced_argument_text(content: str, open_paren: int, limit: int = 12000) -> str:
    """Text between a call's parentheses, stopping at the matching close."""
    depth = 0
    end = min(len(content), open_paren + limit)
    for index in range(open_paren, end):
        char = content[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[open_paren + 1:index]
    return content[open_paren + 1:end]


_OBJECT_KEY_RE = re.compile(r"(?<![\w.])(?P<key>[A-Za-z_$][\w$]*)\s*:")


def _related_models(argument_text: str, root_model: str,
                    relation_map: Mapping[str, Mapping[str, str]]) -> List[str]:
    """
    Models reached through relation keys inside a Prisma call's argument object.

    Only keys that are declared relation fields of a model already reachable
    from ``root_model`` count, so an unrelated object key named ``name`` or
    ``status`` cannot pull a table in.
    """
    keys = {match.group("key") for match in _OBJECT_KEY_RE.finditer(argument_text)}
    if not keys:
        return []

    reachable = {root_model}
    discovered: List[str] = []
    frontier = [root_model]

    while frontier:
        current = frontier.pop()
        for field_name, target in (relation_map.get(current) or {}).items():
            if field_name in keys and target not in reachable:
                reachable.add(target)
                discovered.append(target)
                frontier.append(target)

    return discovered


def extract_prisma_calls(file_path: str, content: str,
                        known_models: Optional[Mapping[str, str]] = None,
                        relation_map: Optional[Mapping[str, Mapping[str, str]]] = None
                        ) -> List[Dict[str, Any]]:
    """
    ``prisma.<accessor>.<op>(`` call sites.

    ``known_models`` maps ``lowercased model name -> declared model name``; when
    supplied, an accessor that does not resolve to a declared model is dropped.
    That gate is what makes accepting the transaction client (``tx.``) safe.

    ``relation_map`` (see :func:`build_relation_map`) additionally resolves
    tables touched through ``include``/``select``/nested-write keys. Without it,
    a model that is only ever read as a relation looks unused, and a naive
    dead-code pass would propose dropping a table the app actively reads.

    Returns ``{file, line, client, accessor, op, model, via}`` where ``via`` is
    ``"delegate"`` for the accessor itself and ``"relation"`` for a nested one.
    """
    calls: List[Dict[str, Any]] = []

    for match in _PRISMA_CALL_RE.finditer(content):
        op = match.group("op")
        if op not in PRISMA_OPERATIONS:
            continue
        accessor = match.group("accessor")
        model = accessor_to_model_name(accessor)
        if known_models is not None:
            resolved = known_models.get(model.lower())
            if not resolved:
                continue
            model = resolved

        line = _line_of(content, match.start())
        calls.append({
            "file": file_path,
            "line": line,
            "client": match.group("client"),
            "accessor": accessor,
            "op": op,
            "model": model,
            "via": "delegate",
        })

        if not relation_map:
            continue
        argument_text = _balanced_argument_text(content, match.end() - 1)
        for related in _related_models(argument_text, model, relation_map):
            calls.append({
                "file": file_path,
                "line": line,
                "client": match.group("client"),
                "accessor": accessor,
                "op": op,
                "model": related,
                "via": "relation",
            })

    if not relation_map:
        return calls

    # Hoisted include/select objects, anchored by their Prisma.<Model>Include type.
    for match in _PRISMA_TYPE_REF_RE.finditer(content):
        model = match.group("model")
        if known_models is not None:
            resolved = known_models.get(model.lower())
            if not resolved:
                continue
            model = resolved
        elif model not in relation_map:
            continue

        window = content[max(0, match.start() - 8000):match.start()]
        line = _line_of(content, match.start())
        for related in [model, *_related_models(window, model, relation_map)]:
            calls.append({
                "file": file_path,
                "line": line,
                "client": "prisma",
                "accessor": model[0].lower() + model[1:],
                "op": "include",
                "model": related,
                "via": "type_reference",
            })

    return calls


# --------------------------------------------------------------------------- #
# Route matching
# --------------------------------------------------------------------------- #

def _paths_compatible(call_path: str, route_path: str) -> bool:
    """A backend ``:param`` segment absorbs any single frontend segment."""
    call_segments = call_path.strip("/").split("/")
    route_segments = route_path.strip("/").split("/")
    if len(call_segments) != len(route_segments):
        return False
    for call_segment, route_segment in zip(call_segments, route_segments):
        if route_segment == ROUTE_PARAM or call_segment == route_segment:
            continue
        return False
    return True


def match_http_routes(calls: Sequence[Mapping[str, Any]],
                      routes: Sequence[Mapping[str, Any]]) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """
    Pair each frontend call site with the backend route(s) that serve it.

    Exact normalized method+path wins. Only when nothing matches exactly does a
    wildcard-tolerant pass run, so ``GET /applications/deo-history`` prefers its
    own literal handler over ``@Get(':id')``.
    """
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


# --------------------------------------------------------------------------- #
# Attribution: call site -> owning graph node
# --------------------------------------------------------------------------- #

def _parse_source_location(source_location: Any) -> Optional[int]:
    """graphify writes ``"L88"``; be forgiving about anything else."""
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
    """
    Maps ``(file, line)`` to the graph node that owns that line.

    The owner is the node in the same file whose declaration starts on the
    closest line at or above the call site. When no symbol node qualifies, the
    file-level node graphify emits for the path is used instead.
    """

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
        """Closest declaration at or above ``line``; file-level node otherwise."""
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


# --------------------------------------------------------------------------- #
# Repository walking
# --------------------------------------------------------------------------- #

def iter_source_files(root_dir: str, extensions: Sequence[str] = _SOURCE_EXTS,
                      filenames: Sequence[str] = ()) -> Iterator[Tuple[str, str]]:
    """
    Yield ``(repo_relative_path, content)`` for every readable source file,
    skipping vendored / generated directories.
    """
    # followlinks=True so a workspace whose packages are symlinked in is still
    # scanned; the realpath guard keeps a symlink cycle from looping forever.
    visited: set = set()
    for current_root, dirnames, files in os.walk(root_dir, followlinks=True):
        real_root = os.path.realpath(current_root)
        if real_root in visited:
            dirnames[:] = []
            continue
        visited.add(real_root)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if filenames:
                if name not in filenames:
                    continue
            elif not name.endswith(tuple(extensions)):
                continue
            absolute = os.path.join(current_root, name)
            relative = os.path.relpath(absolute, root_dir)
            try:
                with open(absolute, "r", encoding="utf-8", errors="ignore") as handle:
                    yield relative, handle.read()
            except OSError:
                continue


def collect_prisma_models(root_dir: str) -> List[Dict[str, Any]]:
    """
    Every ``model X { }`` declared in any ``schema.prisma`` under ``root_dir``.
    Records carry ``{name, line, fields, file}``.
    """
    models: List[Dict[str, Any]] = []
    seen: set = set()
    for relative, content in iter_source_files(root_dir, filenames=("schema.prisma",)):
        normalized_path = relative.replace(os.sep, "/")
        for model in extract_prisma_models(content):
            if model["name"].lower() in seen:
                continue
            seen.add(model["name"].lower())
            models.append({**model, "file": normalized_path})
    return models


def collect_frontend_calls(root_dir: str) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for relative, content in iter_source_files(root_dir):
        if "api." not in content and "fetch(" not in content:
            continue
        calls.extend(extract_frontend_calls(relative.replace(os.sep, "/"), content))
    return calls


def collect_backend_routes(root_dir: str) -> List[Dict[str, Any]]:
    routes: List[Dict[str, Any]] = []
    for relative, content in iter_source_files(root_dir):
        if "@Controller" not in content:
            continue
        routes.extend(extract_backend_routes(relative.replace(os.sep, "/"), content))
    return routes


def collect_prisma_calls(root_dir: str, known_models: Mapping[str, str],
                         relation_map: Optional[Mapping[str, Mapping[str, str]]] = None
                         ) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for relative, content in iter_source_files(root_dir):
        if "prisma." not in content and "tx." not in content:
            continue
        calls.extend(extract_prisma_calls(
            relative.replace(os.sep, "/"), content, known_models, relation_map
        ))
    return calls


# --------------------------------------------------------------------------- #
# Edge construction
# --------------------------------------------------------------------------- #

def resolve_route_handler(index: NodeIndex, route: Mapping[str, Any]) -> Optional[str]:
    """
    Resolve a route decorator to the handler node. The decorator sits *above*
    its method, so name resolution is tried first and line proximity (which
    would land on the enclosing class) only as a fallback.
    """
    handler = route.get("handler")
    if handler:
        node_id = index.node_named(route["file"], handler)
        if node_id:
            return node_id
        node_id = index.node_named(route["file"], f"{handler}()")
        if node_id:
            return node_id
    return index.owner_of(route["file"], route["line"] + 1)


#: Retained private alias -- this used to be the only name.
_route_target = resolve_route_handler


def build_http_route_edges(calls: Sequence[Mapping[str, Any]],
                           routes: Sequence[Mapping[str, Any]],
                           index: NodeIndex) -> List[Dict[str, Any]]:
    """
    Frontend call site -> backend handler, as ``http_route_link`` edge records.
    Call sites that cannot be attributed to any node are skipped silently.
    """
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for call, route in match_http_routes(calls, routes):
        source = index.owner_of(call["file"], call["line"])
        if not source:
            continue
        target = _route_target(index, route)
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


def collect_endpoints(calls: Sequence[Mapping[str, Any]],
                      routes: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fold route declarations and frontend call sites into one record per
    **endpoint identity**, keyed by normalized ``(method, path)``.

    This is the fix for the duplication the 2-tier hierarchy showed: nineteen
    files calling ``GET /auth/me`` used to materialize nineteen unrelated
    "Data Fetch" nodes plus a twentieth "API Endpoint" node, with nothing tying
    them together. Here they are nineteen ``call_sites`` on a single record.

    Each record is ``{id, method, path, label, file, line, routes, call_sites,
    handlers}``. ``file``/``line`` point at the *backend declaration* (the first
    route by ``(file, line)``), so an endpoint node can carry a real
    ``source_location``. Endpoints exist only for declared backend routes: a
    frontend call that matches no route has no endpoint identity to belong to,
    exactly as it has no ``http_route_link`` today.
    """
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


def build_endpoint_edges(endpoints: Sequence[Mapping[str, Any]],
                         index: NodeIndex) -> List[Dict[str, Any]]:
    """
    The two endpoint seams, as edge records:

    * ``calls_endpoint``  frontend call site's owning node -> endpoint node
    * ``handled_by``      endpoint node -> backend handler node

    ``index`` must NOT contain the endpoint nodes themselves: they are recorded
    at their decorator's line inside the controller, so they would otherwise win
    the nearest-declaration lookup and a route would resolve to itself.
    """
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


def build_db_model_edges(calls: Sequence[Mapping[str, Any]],
                         index: NodeIndex) -> List[Dict[str, Any]]:
    """
    Prisma call site -> the model node for the table it touches, as
    ``db_model_link`` edge records.
    """
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for call in calls:
        source = index.owner_of(call["file"], call["line"])
        if not source:
            continue
        target = prisma_model_node_id(call["model"])
        if target == source:
            continue
        key = (source, target)
        if key in edges:
            continue
        edges[key] = {
            "source": source,
            "target": target,
            "relation": DB_MODEL_RELATION,
            "confidence": 1.0,
            "model": call["model"],
            "op": call["op"],
        }

    return list(edges.values())
