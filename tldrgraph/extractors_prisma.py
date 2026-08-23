"""
Prisma ORM schema parsing and DB model calls extractor for TLDRGraph.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .extractors_client import iter_source_files

PRISMA_MODEL_NODE_PREFIX = "prisma_model_"
DB_MODEL_RELATION = "db_model_link"

_PRISMA_MODEL_RE = re.compile(r"^[ \t]*model[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\{", re.MULTILINE)
_PRISMA_FIELD_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z_\[\]][^\s]*)")
_PRISMA_CALL_RE = re.compile(
    r"(?<![\w.])(?:this\s*\.\s*)?(?P<client>prisma|tx|trx|transaction|db|dbClient|prismaClient)"
    r"\s*\.\s*(?P<accessor>[a-z][A-Za-z0-9_]*)\s*\.\s*(?P<op>[A-Za-z0-9_]+)\s*\("
)
_PRISMA_TYPE_REF_RE = re.compile(r"Prisma\.(?P<model>[A-Za-z0-9_]+?)(?:Include|Select|Args|FindManyArgs)\b")
_OBJECT_KEY_RE = re.compile(r"(?<![\w.])(?P<key>[A-Za-z_$][\w$]*)\s*:")

PRISMA_OPERATIONS = frozenset({
    "findMany", "findUnique", "findFirst", "findUniqueOrThrow", "findFirstOrThrow",
    "create", "createMany", "createManyAndReturn", "update", "updateMany",
    "updateManyAndReturn", "upsert", "delete", "deleteMany", "count",
    "aggregate", "groupBy",
})


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def prisma_model_node_id(model_name: str) -> str:
    return f"{PRISMA_MODEL_NODE_PREFIX}{model_name.lower()}"


def accessor_to_model_name(accessor: str) -> str:
    if not accessor:
        return ""
    return accessor[0].upper() + accessor[1:]


def extract_prisma_models(content: str) -> List[Dict[str, Any]]:
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
    by_name = {model["name"].lower(): model["name"] for model in models}
    relation_map: Dict[str, Dict[str, str]] = {}
    for model in models:
        relations: Dict[str, str] = {}
        for field_name, raw_type in (model.get("field_types") or {}).items():
            base_type = raw_type.rstrip("?").rstrip("[]").rstrip("?").strip()
            target = by_name.get(base_type.lower())
            if target:
                relations[field_name] = target
        relation_map[model["name"]] = relations
    return relation_map


def _balanced_argument_text(content: str, open_paren: int, limit: int = 12000) -> str:
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


def _related_models(argument_text: str, root_model: str, relation_map: Mapping[str, Mapping[str, str]]) -> List[str]:
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


def _extract_prisma_delegate_calls(
    file_path: str,
    content: str,
    known_models: Optional[Mapping[str, str]],
    relation_map: Optional[Mapping[str, Mapping[str, str]]],
) -> List[Dict[str, Any]]:
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
        base_call = {
            "file": file_path, "line": line, "client": match.group("client"),
            "accessor": accessor, "op": op, "model": model, "via": "delegate",
        }
        calls.append(base_call)

        if relation_map:
            argument_text = _balanced_argument_text(content, match.end() - 1)
            for related in _related_models(argument_text, model, relation_map):
                calls.append({**base_call, "model": related, "via": "relation"})
    return calls


def _extract_prisma_type_ref_calls(
    file_path: str,
    content: str,
    known_models: Optional[Mapping[str, str]],
    relation_map: Optional[Mapping[str, Mapping[str, str]]],
) -> List[Dict[str, Any]]:
    if not relation_map:
        return []
    calls: List[Dict[str, Any]] = []
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
                "file": file_path, "line": line, "client": "prisma",
                "accessor": model[0].lower() + model[1:], "op": "include",
                "model": related, "via": "type_reference",
            })
    return calls


def extract_prisma_calls(
    file_path: str,
    content: str,
    known_models: Optional[Mapping[str, str]] = None,
    relation_map: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> List[Dict[str, Any]]:
    delegate_calls = _extract_prisma_delegate_calls(file_path, content, known_models, relation_map)
    type_ref_calls = _extract_prisma_type_ref_calls(file_path, content, known_models, relation_map)
    return delegate_calls + type_ref_calls


def collect_prisma_models(root_dir: str) -> List[Dict[str, Any]]:
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


def collect_prisma_calls(
    root_dir: str,
    known_models: Optional[Mapping[str, str]] = None,
    relation_map: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> List[Dict[str, Any]]:
    if known_models is None:
        models = collect_prisma_models(root_dir)
        known_models = {m["name"].lower(): m["name"] for m in models if "name" in m}
        if relation_map is None:
            relation_map = build_relation_map(models)
    calls: List[Dict[str, Any]] = []
    for relative, content in iter_source_files(root_dir):
        if "prisma." not in content and "tx." not in content:
            continue
        calls.extend(extract_prisma_calls(
            relative.replace(os.sep, "/"), content, known_models, relation_map
        ))
    return calls


def build_db_model_edges(
    calls: Sequence[Mapping[str, Any]],
    index: Any,
) -> List[Dict[str, Any]]:
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
