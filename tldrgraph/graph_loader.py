"""
Graph Loader and Multi-Layer Builder for TLDRGraph.
Ingests graph.json or filesystem AST, runs layer classification and hash gating.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple
import networkx as nx
import yaml

from . import __version__, extractors, paths
from .call_resolver import (
    BRIDGE_SCORE_FLOOR,
    bridge_score_floor,
    placeholder_summary,
    resolve_call_target,
)
from .classifier import classify_node, classify_node_with_source
from .deadcode import classify_dead_code, compute_enrichment_coverage
from .hash_gate import HashGate
from .hierarchy import is_test_node
from .labels import build_display_labels
from .layer_config import compute_registry_hash, load_layer_config
from .layers import (
    LAYER_API,
    LAYER_DATA,
    LAYER_DEVOPS,
    get_registry,
    layer_id_of,
)
from .llm_enricher import LLMEnricher
from .node_registrar import (
    apply_deterministic_edges,
    register_endpoint_nodes,
    register_prisma_model_nodes,
    scan_devops_files,
)
from .snapshot_sync import (
    GRAPHIFY_GRAPH_FILENAME,
    GRAPHIFY_MANIFEST_FILENAME,
    LEGACY_GRAPHIFY_DIRNAME,
    SNAPSHOT_FILENAME,
    SNAPSHOT_SCHEMA_VERSION,
    STATE_DIRNAME,
    carry_forward_snapshot,
    graphify_graph_path,
    graphify_manifest_path,
    load_file_hashes,
    load_graph_snapshot,
    node_signature,
    save_graph_snapshot,
)
from .vector_store import BACKEND_TFIDF, SCORE_FLOORS, LocalVectorStore

BRIDGE_RELATIONS = {"llm_cross_layer_link", "cross_layer_link"}
DETERMINISTIC_RELATIONS = {
    extractors.HTTP_ROUTE_RELATION,
    extractors.DB_MODEL_RELATION,
    extractors.CALLS_ENDPOINT_RELATION,
    extractors.HANDLED_BY_RELATION,
}


class GraphLoader:
    def __init__(self, root_dir: str = ".", embeddings: Optional[str] = None):
        self.root_dir = os.path.abspath(root_dir)
        self.registry, self.layers_config_hash = load_layer_config(self.root_dir)
        self.graph = nx.DiGraph()
        self.nodes_by_layer: Dict[str, List[Dict[str, Any]]] = {}
        self.nodes_by_layer_id: Dict[str, List[Dict[str, Any]]] = {}
        self._reset_layer_buckets()
        self.docs_to_index: List[Dict[str, Any]] = []
        self.restored_clean_ids: Set[str] = set()
        self.deterministic_edge_counts: Dict[str, int] = {}
        self.prisma_model_count: int = 0
        self.endpoint_count: int = 0
        self.endpoints: List[Dict[str, Any]] = []
        self.hash_gate = HashGate(os.path.join(self.root_dir, ".tldrgraph/tldrgraph.db"))
        self.vector_store = LocalVectorStore(
            os.path.join(self.root_dir, ".tldrgraph/vector_index.json"),
            embeddings=embeddings,
        )
        self.enricher = LLMEnricher()
        self.file_hashes: Dict[str, str] = load_file_hashes(self.root_dir)

    def _load_file_hashes(self) -> Dict[str, str]:
        return load_file_hashes(self.root_dir)

    def _reset_layer_buckets(self) -> None:
        self.nodes_by_layer = {}
        self.nodes_by_layer_id = {}
        for layer in get_registry():
            bucket: List[Dict[str, Any]] = []
            self.nodes_by_layer[layer.name] = bucket
            self.nodes_by_layer_id[layer.id] = bucket

    def node_signature(self, node_attrs: Dict[str, Any]) -> str:
        return node_signature(self.root_dir, node_attrs, self.file_hashes)

    def snapshot_path(self) -> str:
        return os.path.join(self.root_dir, ".tldrgraph", SNAPSHOT_FILENAME)

    def save_graph(self) -> str:
        return save_graph_snapshot(
            self.root_dir, self.graph, getattr(self, "layers_config_hash", ""), self.file_hashes
        )

    def load_graph_snapshot(self) -> Optional[Dict[str, Any]]:
        return load_graph_snapshot(self.root_dir)

    def _carry_forward_snapshot(self) -> Tuple[int, int]:
        n_restored, e_restored, clean_ids = carry_forward_snapshot(
            self.root_dir, self.graph, self.file_hashes, BRIDGE_RELATIONS
        )
        self.restored_clean_ids = clean_ids
        return n_restored, e_restored

    def _run_graphify(self) -> str:
        from pathlib import Path
        from graphify.detect import detect, save_manifest
        from graphify.extract import collect_files, extract
        from graphify.build import build_from_json
        from graphify.cluster import cluster
        from graphify.export import to_json

        root_path = Path(self.root_dir).resolve()
        out_dir = root_path / STATE_DIRNAME
        out_dir.mkdir(parents=True, exist_ok=True)
        graph_json_path = out_dir / GRAPHIFY_GRAPH_FILENAME

        det = detect(root_path)
        code_files = []
        for f in det.get("files", {}).get("code", []):
            p = Path(f)
            code_files.extend(collect_files(p) if p.is_dir() else [p])

        if code_files:
            extraction = extract(code_files, cache_root=root_path)
            g = build_from_json(extraction, root=str(root_path))
            communities = cluster(g) if g.number_of_nodes() > 0 else {}
            to_json(g, communities, str(graph_json_path), force=True)
        else:
            g = nx.DiGraph()
            to_json(g, {}, str(graph_json_path), force=True)

        if det.get("files"):
            try:
                save_manifest(det["files"], str(out_dir / GRAPHIFY_MANIFEST_FILENAME), root=root_path)
            except Exception:
                pass

        return str(graph_json_path)

    def _apply_display_labels(self) -> int:
        node_records = [{"id": node_id, **data} for node_id, data in self.graph.nodes(data=True)]
        edge_records = [
            {"source": src, "target": tgt, "relation": data.get("relation")}
            for src, tgt, data in self.graph.edges(data=True)
        ]
        display = build_display_labels(node_records, edge_records)
        for node_id, value in display.items():
            self.graph.nodes[node_id]["display_label"] = value
        return len(display)


    def _attach_cached_node_state(
        self, live_attrs: Dict[str, Any], layer_obj: Any, label: str, file_path: str
    ) -> bool:
        node_id = live_attrs["id"]
        is_dirty, cached = self.hash_gate.check_node(
            node_id, node_signature(self.root_dir, live_attrs, self.file_hashes)
        )
        cached_summary = (cached or {}).get("summary") or ""
        usable_cache = (
            not is_dirty
            and cached_summary
            and not placeholder_summary(layer_obj.name, label, file_path).endswith(cached_summary)
        )
        if not usable_cache:
            return False

        live_attrs["summary"] = cached_summary
        live_attrs["intent"] = cached.get("intent") or ""
        if live_attrs["intent"]:
            live_attrs["enrichment_source"] = "cache"
        try:
            raw_fields = json.loads(cached.get("fields_json") or "[]")
            if isinstance(raw_fields, dict):
                live_attrs["input_fields"] = raw_fields.get("input_fields", [])
                live_attrs["output_fields"] = raw_fields.get("output_fields", [])
                live_attrs["fields"] = raw_fields.get("fields", []) or (
                    list(live_attrs["input_fields"]) + list(live_attrs["output_fields"])
                )
            elif isinstance(raw_fields, list):
                live_attrs["input_fields"] = raw_fields
                live_attrs["output_fields"] = []
                live_attrs["fields"] = raw_fields
        except Exception:
            pass
        return True

    def _ingest_ast_node(
        self,
        node: Dict[str, Any],
        dirty_nodes_for_llm: List[Dict[str, Any]],
        enrich_llm: bool,
    ) -> None:
        node_id = str(node.get("id"))
        label = node.get("label") or node_id
        file_path = node.get("source_file") or node.get("file") or node.get("path") or ""
        layer_obj, layer_source = classify_node_with_source(node_id, node)
        layer_id = layer_obj.id

        node_attrs = {
            "id": node_id,
            "label": label,
            "file": file_path,
            "layer": layer_obj.name,
            "layer_id": layer_id,
            "layer_source": layer_source,
            "type": node.get("file_type", "symbol"),
            "community": node.get("community"),
            "degree": node.get("degree", 0),
            "source_location": node.get("source_location"),
            "summary": placeholder_summary(layer_obj.name, label, file_path),
            "is_test": is_test_node(file_path, label),
            "input_fields": [],
            "output_fields": [],
            "fields": [],
            "intent": "",
            "enrichment_source": "",
        }
        self.graph.add_node(node_id, **node_attrs)
        live_attrs = self.graph.nodes[node_id]

        if not self._attach_cached_node_state(live_attrs, layer_obj, label, file_path):
            if enrich_llm and not get_registry().is_utility(layer_id):
                dirty_nodes_for_llm.append(live_attrs)

        self.nodes_by_layer_id[layer_id].append(live_attrs)
        self.docs_to_index.append(live_attrs)

    def _ingest_ast_edges(self, raw_edges: List[Dict[str, Any]]) -> None:
        for edge in raw_edges:
            src, tgt = str(edge.get("source")), str(edge.get("target"))
            rel = edge.get("relation", edge.get("kind", "calls"))
            try:
                conf = float(edge.get("confidence_score", 1.0))
            except (TypeError, ValueError):
                conf = 1.0
            if self.graph.has_node(src) and self.graph.has_node(tgt):
                self.graph.add_edge(src, tgt, relation=rel, confidence=conf)

    def load_or_extract(self, enrich_llm: bool = True, rebuild: bool = False) -> nx.DiGraph:
        graph_json_path = graphify_graph_path(self.root_dir)
        if not os.path.exists(graph_json_path):
            self._run_graphify()
            self.file_hashes = load_file_hashes(self.root_dir)

        self.registry, self.layers_config_hash = load_layer_config(self.root_dir)
        self.graph = nx.DiGraph()
        self._reset_layer_buckets()
        self.docs_to_index = []
        dirty_nodes_for_llm: List[Dict[str, Any]] = []

        if os.path.exists(graph_json_path):
            with open(graph_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for node in data.get("nodes", []):
                self._ingest_ast_node(node, dirty_nodes_for_llm, enrich_llm)
            self._ingest_ast_edges(data.get("links", data.get("edges", [])))

        scan_devops_files(self.root_dir, self.graph, self.nodes_by_layer_id, self.docs_to_index)
        self.prisma_model_count = register_prisma_model_nodes(
            self.root_dir, self.graph, self.nodes_by_layer_id, self.docs_to_index,
            self.hash_gate, self.file_hashes, enrich_llm=enrich_llm, dirty_nodes_for_llm=dirty_nodes_for_llm,
        )
        self.endpoint_count, self.endpoints = register_endpoint_nodes(
            self.root_dir, self.graph, self.nodes_by_layer_id, self.docs_to_index,
            self.hash_gate, self.file_hashes, enrich_llm=enrich_llm, dirty_nodes_for_llm=dirty_nodes_for_llm,
        )
        self.deterministic_edge_counts = apply_deterministic_edges(self.root_dir, self.graph, self.endpoints)
        self._apply_display_labels()

        if not rebuild:
            self._carry_forward_snapshot()
            if self.restored_clean_ids:
                dirty_nodes_for_llm = [n for n in dirty_nodes_for_llm if n.get("id") not in self.restored_clean_ids]

        self.vector_store.add_documents(self.docs_to_index)
        if enrich_llm and dirty_nodes_for_llm:
            self._run_llm_enrichment(dirty_nodes_for_llm)

        self.vector_store.add_documents(self.docs_to_index)
        classify_dead_code(self.graph, compute_enrichment_coverage(self.graph), root_dir=self.root_dir)
        self.save_graph()
        self.export_yaml()
        return self.graph

    def _apply_llm_enriched_item(self, item: Dict[str, Any]) -> None:
        nid = item.get("id")
        if not nid or not self.graph.has_node(nid):
            return

        intent = item.get("intent", "")
        input_fields = item.get("input_fields", []) or []
        output_fields = item.get("output_fields", []) or []
        legacy_fields = item.get("fields", []) or []
        calls = item.get("calls", []) or []

        node_data = self.graph.nodes[nid]
        if intent:
            node_data["intent"] = intent
            first_line = intent.strip().split("\n")[0].lstrip("#- *").strip()
            node_data["summary"] = f"{node_data['layer']}: {node_data['label']} - {first_line or intent}"
            node_data["enrichment_source"] = item.get("source") or "llm"
        if input_fields or output_fields:
            node_data["input_fields"] = input_fields
            node_data["output_fields"] = output_fields
            node_data["fields"] = list(input_fields) + list(output_fields)
        elif legacy_fields:
            node_data["input_fields"] = legacy_fields
            node_data["output_fields"] = []
            node_data["fields"] = legacy_fields

        fields_dict = {
            "input_fields": node_data.get("input_fields", []),
            "output_fields": node_data.get("output_fields", []),
            "fields": node_data.get("fields", []),
        }
        self.hash_gate.update_node(
            node_id=nid,
            file_path=node_data.get("file", ""),
            content=node_signature(self.root_dir, node_data, self.file_hashes),
            layer=node_data.get("layer", ""),
            summary=node_data.get("summary", ""),
            fields_json=json.dumps(fields_dict),
            intent=node_data.get("intent", ""),
        )

        floor = bridge_score_floor(self.vector_store)
        for call_target in calls:
            tgt_id, score = resolve_call_target(self.graph, self.vector_store, call_target, nid, floor=floor)
            if tgt_id:
                self.graph.add_edge(nid, tgt_id, relation="llm_cross_layer_link", confidence=score)

    def _run_llm_enrichment(self, nodes_to_enrich: List[Dict[str, Any]], batch_size: int = 15) -> None:
        for i in range(0, len(nodes_to_enrich), batch_size):
            batch = nodes_to_enrich[i:i + batch_size]
            enriched_items = self.enricher.enrich_batch(batch)
            for item in enriched_items:
                self._apply_llm_enriched_item(item)

    def export_yaml(self, output_dir: Optional[str] = None) -> str:
        if output_dir is None:
            output_dir = os.path.join(self.root_dir, ".tldrgraph")
        os.makedirs(output_dir, exist_ok=True)
        yaml_path = os.path.join(output_dir, "layers.yaml")

        summary_data = {
            "tldrgraph_version": __version__,
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "layers": {},
        }
        for layer_name, nodes in self.nodes_by_layer.items():
            summary_data["layers"][layer_name] = {
                "count": len(nodes),
                "nodes": [{"id": n["id"], "label": n["label"], "file": n["file"]} for n in nodes[:50]],
            }

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(summary_data, f, default_flow_style=False, sort_keys=False)
        return yaml_path
