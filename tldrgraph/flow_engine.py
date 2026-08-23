"""
Flow Engine for TLDRGraph: Multi-layer pathfinding and YAML / Markdown export.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from tabulate import tabulate
import yaml

from .flow_traversal import (
    BRIDGE_EDGE_COST,
    BRIDGE_RELATIONS,
    CANDIDATE_POOL,
    CONTAINER_TYPES,
    CROSS_LAYER_EDGE_COST,
    DEFAULT_MAX_STEPS,
    DETERMINISTIC_BRIDGE_RELATIONS,
    MATCH_MARGIN,
    SAME_LAYER_EDGE_COST,
    SOURCE_EXTENSIONS,
    bridge_aware_walk,
    is_container_node,
    label_keys,
    normalize_label,
    node_preference,
    resolve_node_id,
)
from .hierarchy import is_test_node
from .layers import get_registry, layer_id_of
from .vector_store import LocalVectorStore


class FlowEngine:
    def __init__(self, graph: nx.DiGraph, vector_store: LocalVectorStore, root_dir: str = "."):
        self.graph = graph
        self.vector_store = vector_store
        self.root_dir = root_dir

    @staticmethod
    def _normalize_label(text: str) -> str:
        return normalize_label(text)

    def _label_keys(self, data: Dict[str, Any]) -> Set[str]:
        return label_keys(data)

    def _is_container_node(self, node_id: str, data: Dict[str, Any]) -> bool:
        return is_container_node(node_id, data)

    def _preference(self, node_id: str) -> float:
        return node_preference(self.graph, node_id)

    def _resolve_node_id(self, query: str) -> Optional[Tuple[str, float, Dict[str, Any]]]:
        return resolve_node_id(self.graph, self.vector_store, query)

    def _layer_of(self, node_id: str) -> str:
        return self.graph.nodes.get(node_id, {}).get("layer", get_registry().utility.name)

    def _layer_id_of(self, node_id: str) -> str:
        data = self.graph.nodes.get(node_id, {})
        return layer_id_of(data) or get_registry().utility_id

    @staticmethod
    def _layer_rank(layer: str) -> int:
        registry = get_registry()
        found = registry.by_name(layer)
        return found.order if found else registry.unranked_order

    def _layer_rank_of_node(self, node_id: str) -> int:
        registry = get_registry()
        return registry.order(self._layer_id_of(node_id))

    def _bridge_aware_walk(self, start_node_id: str, max_steps: int) -> List[Dict[str, Any]]:
        return bridge_aware_walk(self.graph, start_node_id, max_steps, self._format_node_step)

    @staticmethod
    def _layers_of_steps(steps: List[Dict[str, Any]]) -> List[str]:
        seen: List[str] = []
        for s in steps:
            layer = s.get("layer")
            if layer and layer not in seen:
                seen.append(layer)
        return seen

    def _trace_target(
        self,
        start_node_id: str,
        source_score: float,
        source_data: Dict[str, Any],
        target_query: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Attempts explicit shortest-path trace to target_query."""
        resolved_target = self._resolve_node_id(target_query)
        if not resolved_target:
            return None, {"error": f"No matching target node found for '{target_query}'"}
        target_node_id, _, _ = resolved_target

        try:
            path = nx.shortest_path(self.graph, start_node_id, target_node_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            unreachable = {
                "requested_target": target_node_id,
                "target_reached": False,
                "note": (
                    f"No directed path from '{start_node_id}' to '{target_node_id}' "
                    f"({type(exc).__name__}). Showing a downstream trace from the source instead."
                ),
            }
            return None, unreachable

        flow_steps = [self._format_node_step(nid) for nid in path]
        result = {
            "source": start_node_id,
            "source_label": source_data.get("label", start_node_id),
            "match_score": round(float(source_score), 3),
            "target": target_node_id,
            "target_reached": True,
            "length": len(path),
            "steps": flow_steps,
            "layers": self._layers_of_steps(flow_steps),
            "order": "path",
        }
        return result, None

    def trace_path(
        self,
        source_query: str,
        target_query: str = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> Dict[str, Any]:
        """Traces execution path across layers starting from source_query."""
        resolved_source = self._resolve_node_id(source_query)
        if not resolved_source:
            return {"error": f"No matching node found for '{source_query}'"}
        start_node_id, source_score, source_data = resolved_source

        unreachable: Optional[Dict[str, Any]] = None
        if target_query:
            direct_result, unreachable_info = self._trace_target(
                start_node_id, source_score, source_data, target_query
            )
            if direct_result:
                return direct_result
            if "error" in unreachable_info:
                return unreachable_info
            unreachable = unreachable_info

        steps = self._bridge_aware_walk(start_node_id, max_steps=max_steps)
        result: Dict[str, Any] = {
            "source": start_node_id,
            "source_label": source_data.get("label", start_node_id),
            "match_score": round(float(source_score), 3),
            "length": len(steps),
            "steps": steps,
            "layers": self._layers_of_steps(steps),
            "order": "layer",
            "max_steps": max_steps,
        }
        if unreachable:
            result.update(unreachable)
        return result

    def query_flow(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Hybrid semantic search + downstream flow expansion."""
        matches = self.vector_store.search(query_text, top_k=top_k)
        results = []
        for doc, score in matches:
            node_id = doc["id"]
            trace = self.trace_path(node_id) if self.graph.has_node(node_id) else {"steps": []}
            steps = trace.get("steps", [])
            results.append({
                "match_score": round(score, 3),
                "root_node": doc["label"],
                "root_id": node_id,
                "layer_id": self._layer_id_of(node_id),
                "layer": doc.get("layer"),
                "file": doc.get("file"),
                "flow": steps,
                "layers": self._layers_of_steps(steps),
                "layer_ids": [s.get("layer_id") for s in steps if s.get("layer_id")],
            })
        return results

    def _format_node_step(self, node_id: str) -> Dict[str, Any]:
        node_data = self.graph.nodes.get(node_id, {})
        input_fields = node_data.get("input_fields", [])
        output_fields = node_data.get("output_fields", [])
        fields = node_data.get("fields", []) or (list(input_fields) + list(output_fields))
        is_test = node_data.get("is_test")
        if is_test is None:
            is_test = is_test_node(node_data.get("file", ""), node_data.get("label", ""))
        return {
            "id": node_id,
            "label": node_data.get("label", node_id),
            "layer_id": self._layer_id_of(node_id),
            "layer": node_data.get("layer", "Unknown"),
            "file": node_data.get("file", ""),
            "is_test": bool(is_test),
            "intent": node_data.get("intent") or node_data.get("summary", ""),
            "input_fields": input_fields,
            "output_fields": output_fields,
            "fields": fields,
        }

    def export_flows_yaml(self, flows: List[Dict[str, Any]], filename: str = ".tldrgraph/flows.yaml") -> str:
        out_path = os.path.join(self.root_dir, filename)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"flows": flows}, f, default_flow_style=False, sort_keys=False)
        return out_path

    @staticmethod
    def render_markdown_table(steps: List[Dict[str, Any]]) -> str:
        headers = ["Layer", "Component / Symbol", "Intent & Action", "Input Fields", "Output Fields", "File Location"]
        rows = [
            [
                s.get("layer", ""),
                s.get("label", s.get("id", "")),
                s.get("intent", ""),
                ", ".join(s.get("input_fields", [])) if s.get("input_fields") else (", ".join(s.get("fields", [])) if s.get("fields") else "-"),
                ", ".join(s.get("output_fields", [])) if s.get("output_fields") else "-",
                s.get("file", ""),
            ]
            for s in steps
        ]
        return tabulate(rows, headers=headers, tablefmt="github")
