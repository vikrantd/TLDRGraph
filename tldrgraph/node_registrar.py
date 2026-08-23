"""
Node registration and deterministic edge synthesis for TLDRGraph.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
import networkx as nx

from . import extractors
from .call_resolver import placeholder_summary
from .layers import LAYER_API, LAYER_DATA, LAYER_DEVOPS, get_registry
from .snapshot_sync import is_placeholder_summary, is_test_node, node_signature


def _check_and_attach_cache(
    root_dir: str,
    live_attrs: Dict[str, Any],
    node_id: str,
    hash_gate: Any,
    file_hashes: Dict[str, str],
    enrich_llm: bool,
    dirty_nodes_for_llm: Optional[List[Dict[str, Any]]],
) -> None:
    is_dirty, cached = hash_gate.check_node(node_id, node_signature(root_dir, live_attrs, file_hashes))
    cached_summary = (cached or {}).get("summary") or ""
    if not is_dirty and cached_summary and not is_placeholder_summary(cached_summary, live_attrs):
        live_attrs["summary"] = cached_summary
        live_attrs["intent"] = cached.get("intent") or ""
        if live_attrs["intent"]:
            live_attrs["enrichment_source"] = "cache"
    elif enrich_llm and dirty_nodes_for_llm is not None:
        dirty_nodes_for_llm.append(live_attrs)


def _scan_devops_paths(root_dir: str, devops_paths: List[str]) -> List[str]:
    found = []
    for rel_path in devops_paths:
        full_path = os.path.join(root_dir, rel_path)
        if os.path.isdir(full_path):
            for r, _, files in os.walk(full_path):
                for f in files:
                    if not f.startswith("."):
                        found.append(os.path.relpath(os.path.join(r, f), root_dir))
        elif os.path.isfile(full_path):
            found.append(rel_path)
    return found


def scan_devops_files(
    root_dir: str,
    graph: nx.DiGraph,
    nodes_by_layer_id: Dict[str, List[Dict[str, Any]]],
    docs_to_index: List[Dict[str, Any]],
    devops_paths: Optional[List[str]] = None,
) -> None:
    if devops_paths is None:
        devops_paths = [
            "docker", "charts", "backend/Dockerfile", "frontend/Dockerfile",
            "frontend/.github/workflows", ".github/workflows",
        ]

    devops_layer = get_registry().by_id(LAYER_DEVOPS) or get_registry().utility
    layer_id = devops_layer.id
    layer = devops_layer.name

    for rel_file in _scan_devops_paths(root_dir, devops_paths):
        node_id = f"devops_{rel_file.replace(os.sep, '_').replace('.', '_')}"
        if not graph.has_node(node_id):
            basename = os.path.basename(rel_file)
            node_attrs = {
                "id": node_id,
                "label": basename,
                "file": rel_file,
                "layer": layer,
                "layer_id": layer_id,
                "layer_source": "rule",
                "type": "infra_config",
                "community": None,
                "degree": 0,
                "summary": placeholder_summary(layer, basename, rel_file),
                "intent": "",
                "enrichment_source": "",
            }
            graph.add_node(node_id, **node_attrs)
            live_attrs = graph.nodes[node_id]
            nodes_by_layer_id[layer_id].append(live_attrs)
            docs_to_index.append(live_attrs)


def _build_prisma_model_attrs(model: Dict[str, Any], data_layer: Any) -> Dict[str, Any]:
    label = model["name"]
    file_path = model["file"]
    return {
        "id": extractors.prisma_model_node_id(model["name"]),
        "label": label,
        "file": file_path,
        "layer": data_layer.name,
        "layer_id": data_layer.id,
        "layer_source": "rule",
        "type": "db_model",
        "community": None,
        "degree": 0,
        "source_location": f"L{model['line']}",
        "summary": placeholder_summary(data_layer.name, label, file_path),
        "fields": list(model.get("fields") or []),
        "intent": "",
        "enrichment_source": "",
    }


def register_prisma_model_nodes(
    root_dir: str,
    graph: nx.DiGraph,
    nodes_by_layer_id: Dict[str, List[Dict[str, Any]]],
    docs_to_index: List[Dict[str, Any]],
    hash_gate: Any,
    file_hashes: Dict[str, str],
    enrich_llm: bool = False,
    dirty_nodes_for_llm: Optional[List[Dict[str, Any]]] = None,
) -> int:
    data_layer = get_registry().by_id(LAYER_DATA) or get_registry().utility
    created = 0

    for model in extractors.collect_prisma_models(root_dir):
        node_id = extractors.prisma_model_node_id(model["name"])
        if graph.has_node(node_id):
            continue

        node_attrs = _build_prisma_model_attrs(model, data_layer)
        graph.add_node(node_id, **node_attrs)
        live_attrs = graph.nodes[node_id]

        _check_and_attach_cache(
            root_dir, live_attrs, node_id, hash_gate, file_hashes,
            enrich_llm, dirty_nodes_for_llm
        )

        nodes_by_layer_id[data_layer.id].append(live_attrs)
        docs_to_index.append(live_attrs)
        created += 1

    return created


def _build_endpoint_node_attrs(endpoint: Dict[str, Any], api_layer: Any) -> Dict[str, Any]:
    label = endpoint["label"]
    file_path = endpoint["file"]
    line = endpoint.get("line")
    return {
        "id": endpoint["id"],
        "label": label,
        "file": file_path,
        "layer": api_layer.name,
        "layer_id": api_layer.id,
        "layer_source": "rule",
        "type": extractors.ENDPOINT_NODE_TYPE,
        "community": None,
        "degree": 0,
        "source_location": f"L{line}" if line else None,
        "summary": placeholder_summary(api_layer.name, label, file_path),
        "is_test": is_test_node(file_path, label),
        "input_fields": [],
        "output_fields": [],
        "fields": [],
        "intent": "",
        "enrichment_source": "",
        "method": endpoint.get("method", ""),
        "path": endpoint.get("path", ""),
        "handlers": endpoint.get("handlers") or [],
        "call_site_count": len(endpoint.get("call_sites") or []),
    }


def register_endpoint_nodes(
    root_dir: str,
    graph: nx.DiGraph,
    nodes_by_layer_id: Dict[str, List[Dict[str, Any]]],
    docs_to_index: List[Dict[str, Any]],
    hash_gate: Any,
    file_hashes: Dict[str, str],
    enrich_llm: bool = False,
    dirty_nodes_for_llm: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    api_layer = get_registry().by_id(LAYER_API) or get_registry().utility
    created = 0

    try:
        frontend_calls = extractors.collect_frontend_calls(root_dir)
        backend_routes = extractors.collect_backend_routes(root_dir)
    except OSError:
        frontend_calls, backend_routes = [], []

    endpoints = extractors.collect_endpoints(frontend_calls, backend_routes)

    for endpoint in endpoints:
        node_id = endpoint["id"]
        if graph.has_node(node_id):
            continue

        node_attrs = _build_endpoint_node_attrs(endpoint, api_layer)
        graph.add_node(node_id, **node_attrs)
        live_attrs = graph.nodes[node_id]

        _check_and_attach_cache(
            root_dir, live_attrs, node_id, hash_gate, file_hashes,
            enrich_llm, dirty_nodes_for_llm
        )

        nodes_by_layer_id[api_layer.id].append(live_attrs)
        docs_to_index.append(live_attrs)
        created += 1

    return created, endpoints


def apply_deterministic_edges(
    root_dir: str,
    graph: nx.DiGraph,
    endpoints: List[Dict[str, Any]],
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    index = extractors.NodeIndex([{"id": nid, **data} for nid, data in graph.nodes(data=True)])

    endpoint_edges = extractors.build_endpoint_edges(endpoints, index)
    try:
        frontend_calls = extractors.collect_frontend_calls(root_dir)
        backend_routes = extractors.collect_backend_routes(root_dir)
        http_edges = extractors.build_http_route_edges(frontend_calls, backend_routes, index)
    except OSError:
        http_edges = []

    try:
        prisma_calls = extractors.collect_prisma_calls(root_dir)
        db_edges = extractors.build_db_model_edges(prisma_calls, index)
    except OSError:
        db_edges = []

    for edge in list(http_edges) + list(endpoint_edges) + list(db_edges):
        src, tgt = edge["source"], edge["target"]
        if not (graph.has_node(src) and graph.has_node(tgt)):
            continue
        if graph.has_edge(src, tgt):
            continue
        graph.add_edge(
            src, tgt,
            relation=edge["relation"],
            confidence=float(edge.get("confidence", 1.0)),
        )
        counts[edge["relation"]] = counts.get(edge["relation"], 0) + 1

    return counts
