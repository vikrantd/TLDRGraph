"""
Compound Hierarchy: 3-Tier Multi-Layer UI & Module Tree Hierarchy for TLDRGraph.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from . import extractors, paths
from .hierarchy_builder import (
    HIERARCHY_SCHEMA,
    IMPORT_RELATIONS,
    MULTI_PARENT_RULE,
    PAGE_CONTAINS_RELATION,
    TIER_COMPONENT,
    TIER_ELEMENT,
    TIER_MODULE,
    TIER_PAGE,
    HierarchyState,
    container_id,
    is_endpoint_record,
    is_page_file,
    is_test_node,
    link_pages_to_components,
    page_route,
    slug,
    subnode_id,
)
from .labels import build_display_labels
from .layers import (
    LAYER_API,
    LAYER_DATA,
    LAYER_SERVICE,
    LAYER_UI,
    get_registry,
    layer_name,
)

_REDERIVED_RELATIONS = {
    PAGE_CONTAINS_RELATION,
    extractors.CALLS_ENDPOINT_RELATION,
    extractors.HANDLED_BY_RELATION,
}


def _load_ast_data(root_dir: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    gpath = paths.graphify_graph_path(root_dir)
    if not os.path.exists(gpath):
        return [], []
    try:
        with open(gpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("nodes", []), data.get("links", data.get("edges", []))
    except Exception:
        return [], []


def _populate_ast_symbols(state: HierarchyState, ast_by_file: Dict[str, List[Dict[str, Any]]], display_labels: Dict[str, str]) -> None:
    for fpath, symbols in ast_by_file.items():
        c = state.get_or_create_container(fpath)
        for s in symbols:
            nid = str(s.get("id"))
            loc = s.get("source_location") or ""
            disp = display_labels.get(nid) or s.get("label", nid)
            c["subnodes"].append({
                "id": subnode_id(nid),
                "graph_node_id": nid,
                "container_id": c["id"],
                "parent_container": c["id"],
                "label": s.get("label", nid),
                "display_label": disp,
                "file": fpath,
                "layer": c["layer"],
                "layer_id": c["layer_id"],
                "tier": TIER_ELEMENT,
                "source_location": loc,
                "kind": s.get("file_type") or s.get("type", "symbol"),
                "is_test": bool(c.get("is_test", False)),
                "intent": s.get("intent") or "",
                "fields": s.get("fields", []),
                "summary": s.get("summary") or f"{c['layer']}: {disp} in {fpath}",
            })


def _populate_endpoints(state: HierarchyState, endpoints: List[Dict[str, Any]]) -> None:
    for ep in endpoints:
        fpath = ep["file"]
        c = state.get_or_create_container(fpath, layer_name(LAYER_API) or get_registry().utility.name)
        c["subnodes"].append({
            "id": subnode_id(ep["id"]),
            "graph_node_id": ep["id"],
            "container_id": c["id"],
            "parent_container": c["id"],
            "label": ep["label"],
            "display_label": ep["label"],
            "file": fpath,
            "layer": c["layer"],
            "layer_id": c["layer_id"],
            "tier": TIER_ELEMENT,
            "source_location": f"L{ep.get('line')}" if ep.get("line") else None,
            "kind": "API Endpoint",
            "is_test": bool(c.get("is_test", False)),
            "call_site_count": len(ep.get("call_sites") or []),
            "intent": f"HTTP {ep['method'].upper()} endpoint for {ep['path']}",
            "fields": ep.get("fields", []),
            "summary": f"{c['layer']}: {ep['label']} in {fpath}",
            "method": ep["method"],
            "path": ep["path"],
        })


def _populate_prisma(state: HierarchyState, prisma_models: List[Dict[str, Any]]) -> Dict[str, Any]:
    schema_file = prisma_models[0]["file"] if prisma_models else "backend/prisma/schema.prisma"
    c = state.get_or_create_container(schema_file, layer_name(LAYER_DATA) or get_registry().utility.name)
    c["label"] = "Database Models (Prisma)"
    c["display_label"] = "Database Models (Prisma)"
    for m in prisma_models:
        c["subnodes"].append({
            "id": subnode_id(f"db_{m['name'].lower()}"),
            "graph_node_id": f"db_{m['name'].lower()}",
            "container_id": c["id"],
            "parent_container": c["id"],
            "label": m["name"],
            "display_label": m["name"],
            "file": schema_file,
            "layer": c["layer"],
            "layer_id": c["layer_id"],
            "tier": TIER_ELEMENT,
            "source_location": f"L{m['line']}",
            "kind": "Database Model",
            "is_test": bool(c.get("is_test", False)),
            "intent": f"Prisma table model for {m['name']}",
            "fields": m.get("fields", []),
            "summary": f"Data & DB: {m['name']} in {schema_file}",
        })
    return c


def _connect_single_call_site(
    state: HierarchyState,
    endpoint: Dict[str, Any],
    call_site: Dict[str, Any],
    ast_index: extractors.NodeIndex,
) -> None:
    caller_file = call_site.get("file")
    if not caller_file:
        return
    src_c = state.get_or_create_container(caller_file, layer_name(LAYER_UI) or get_registry().utility.name)
    tgt_c = state.get_or_create_container(endpoint["file"], layer_name(LAYER_API) or get_registry().utility.name)
    src_sub = None
    if call_site.get("caller_symbol"):
        caller_nid = ast_index.node_named(caller_file, call_site["caller_symbol"])
        if caller_nid:
            src_sub = subnode_id(str(caller_nid))
    if not src_sub and call_site.get("line"):
        owner_nid = ast_index.owner_of(caller_file, call_site["line"])
        if owner_nid:
            src_sub = subnode_id(str(owner_nid))

    state.add_edge(
        src_c, tgt_c, src_sub, subnode_id(endpoint["id"]),
        extractors.CALLS_ENDPOINT_RELATION, f"calls {endpoint['label']}",
        method=endpoint["method"], path=endpoint["path"],
    )


def _connect_single_route(
    state: HierarchyState,
    endpoint: Dict[str, Any],
    route: Dict[str, Any],
    ast_index: extractors.NodeIndex,
) -> None:
    handler_id = extractors.resolve_route_handler(ast_index, route)
    if not handler_id:
        return
    src_c = state.get_or_create_container(endpoint["file"], layer_name(LAYER_API) or get_registry().utility.name)
    tgt_c = state.get_or_create_container(route.get("file") or endpoint["file"], layer_name(LAYER_API) or get_registry().utility.name)
    state.add_edge(
        src_c, tgt_c, subnode_id(endpoint["id"]), subnode_id(str(handler_id)),
        extractors.HANDLED_BY_RELATION, route.get("handler") or endpoint["label"],
        method=endpoint["method"], path=endpoint["path"],
    )


def _connect_endpoint_calls(state: HierarchyState, endpoints: List[Dict[str, Any]], ast_index: extractors.NodeIndex) -> None:
    for endpoint in endpoints:
        for call_site in endpoint.get("call_sites") or []:
            _connect_single_call_site(state, endpoint, call_site, ast_index)
        for route in endpoint.get("routes") or []:
            _connect_single_route(state, endpoint, route, ast_index)


def _build_ast_mappings(ast_nodes: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    ast_by_file: Dict[str, List[Dict[str, Any]]] = {}
    ast_node_map: Dict[str, Dict[str, Any]] = {}
    ast_records: List[Dict[str, Any]] = []

    for n in ast_nodes:
        nid = str(n.get("id", ""))
        if not nid:
            continue
        fpath = n.get("source_file") or n.get("file") or ""
        rec = dict(n, id=nid, file=fpath)
        ast_node_map[nid] = rec
        ast_records.append(rec)
        if fpath:
            ast_by_file.setdefault(fpath, []).append(rec)

    return ast_by_file, ast_node_map, ast_records


def _connect_prisma_calls(
    state: HierarchyState, root_dir: str, prisma_models: List[Dict[str, Any]], prisma_container: Dict[str, Any]
) -> None:
    known_models = {m["name"].lower(): m["name"] for m in prisma_models if "name" in m}
    relation_map = extractors.build_relation_map(prisma_models)
    prisma_calls = extractors.collect_prisma_calls(root_dir, known_models, relation_map)
    for pcall in prisma_calls:
        fpath = pcall["file"]
        model_name = pcall["model"]
        src_c = state.get_or_create_container(fpath, layer_name(LAYER_SERVICE) or get_registry().utility.name)
        state.add_edge(
            src_c, prisma_container,
            f"sub_{slug(fpath)}_L{pcall['line']}", f"sub_db_{model_name.lower()}",
            "db_model_link", f"prisma.{model_name}.{pcall.get('op', 'query')}()",
            model=model_name, op=pcall.get("op", "query"),
        )


def _connect_cross_file_ast_edges(
    state: HierarchyState, ast_edges: List[Dict[str, Any]], ast_node_map: Dict[str, Dict[str, Any]]
) -> None:
    for e in ast_edges:
        if str(e.get("relation") or "") in _REDERIVED_RELATIONS:
            continue
        src_n = ast_node_map.get(str(e.get("source")))
        tgt_n = ast_node_map.get(str(e.get("target")))
        if not (src_n and tgt_n) or is_endpoint_record(src_n) or is_endpoint_record(tgt_n):
            continue
        src_f = src_n.get("file") or ""
        tgt_f = tgt_n.get("file") or ""
        if not (src_f and tgt_f) or src_f == tgt_f:
            continue
        src_c = state.get_or_create_container(src_f)
        tgt_c = state.get_or_create_container(tgt_f)
        rel = e.get("relation", "calls")
        state.add_edge(src_c, tgt_c, subnode_id(str(src_n.get("id"))), subnode_id(str(tgt_n.get("id"))), rel, rel)


def _serialize_containers(state: HierarchyState) -> List[Dict[str, Any]]:
    container_display = build_display_labels(
        [{"id": c["id"], "label": page_route(c["file"]) if c["tier"] == TIER_PAGE else c["label"], "file": c["file"]}
         for c in state.containers.values()],
        (),
    )
    for c in state.containers.values():
        c["display_label"] = container_display.get(c["id"]) or c["label"]

    serializable = []
    for c in state.containers.values():
        serializable.append({
            "id": c["id"],
            "label": c["label"],
            "display_label": c.get("display_label") or c["label"],
            "file": c["file"],
            "layer": c["layer"],
            "layer_id": c["layer_id"],
            "tier": c["tier"],
            "is_test": c.get("is_test", False),
            "parent_containers": list(c["parent_containers"]),
            "parent_container": c["parent_containers"][0] if c["parent_containers"] else None,
            "child_containers": sorted(c["child_containers"]),
            "depth": 1 if c["parent_containers"] else 0,
            "shared": c["shared"],
            "intent": c["intent"] or f"Module {c['label']} in {c['layer']}",
            "input_fields": c.get("input_fields", []),
            "output_fields": c.get("output_fields", []),
            "fields": c.get("fields", []),
            "subnode_count": len(c["subnodes"]),
            "subnodes": c["subnodes"],
            "out_count": len(c["out_containers"]),
            "in_count": len(c["in_containers"]),
            "out_containers": sorted(c["out_containers"]),
            "in_containers": sorted(c["in_containers"]),
        })
    return serializable


def build_multilayer_hierarchy(root_dir: str = ".") -> Dict[str, Any]:
    root_dir = os.path.abspath(root_dir)
    state = HierarchyState(root_dir)

    frontend_calls = extractors.collect_frontend_calls(root_dir)
    backend_routes = extractors.collect_backend_routes(root_dir)
    prisma_models = extractors.collect_prisma_models(root_dir)
    endpoints = extractors.collect_endpoints(frontend_calls, backend_routes)

    ast_nodes, ast_edges = _load_ast_data(root_dir)
    ast_by_file, ast_node_map, ast_records = _build_ast_mappings(ast_nodes)

    display_labels = build_display_labels(ast_records, ast_edges)
    ast_index = extractors.NodeIndex(ast_records)

    _populate_ast_symbols(state, ast_by_file, display_labels)
    _populate_endpoints(state, endpoints)
    prisma_container = _populate_prisma(state, prisma_models)
    _connect_endpoint_calls(state, endpoints, ast_index)
    _connect_prisma_calls(state, root_dir, prisma_models, prisma_container)
    _connect_cross_file_ast_edges(state, ast_edges, ast_node_map)

    page_contains = link_pages_to_components(state.containers, ast_edges, ast_node_map)
    for page_c, comp_c in page_contains:
        state.add_edge(page_c, comp_c, None, None, PAGE_CONTAINS_RELATION, f"renders {comp_c['label']}")

    serializable = _serialize_containers(state)
    tier_counts: Dict[str, int] = {}
    for c in serializable:
        t = c.get("tier", "")
        tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "schema": HIERARCHY_SCHEMA,
        "tiers": [TIER_PAGE, TIER_COMPONENT, TIER_ELEMENT],
        "multi_parent_rule": MULTI_PARENT_RULE,
        "stats": {
            "containers": len(serializable),
            "endpoints": len(endpoints),
            "pages": tier_counts.get(TIER_PAGE, 0),
            "components": tier_counts.get(TIER_COMPONENT, 0),
            "modules": tier_counts.get(TIER_MODULE, 0),
        },
        "containers": serializable,
        "edges": state.edges,
    }
