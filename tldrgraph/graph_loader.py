"""
Graph Loader and Multi-Layer Builder for TLDRGraph.
Ingests graph.json or filesystem AST, runs layer classification and hash gating.
"""

import os
import json
import hashlib
import yaml
import networkx as nx
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional, Set
from . import __version__, extractors
from .classifier import classify_node, classify_node_with_source
from .layer_config import load_layer_config, compute_registry_hash
from .layers import (
    LAYER_API,
    LAYER_DATA,
    LAYER_DEVOPS,
    get_registry,
    layer_id_of,
)
from .deadcode import classify_dead_code, compute_enrichment_coverage
from .hash_gate import HashGate
from .labels import build_display_labels
from .vector_store import BACKEND_TFIDF, SCORE_FLOORS, LocalVectorStore
from .llm_enricher import LLMEnricher
from .hierarchy import is_test_node

#: Relations that represent LLM/agent inferred bridges between layers. These are the
#: only edges that are NOT re-derivable from graphify, so they are carried forward
#: across re-scans from the persisted snapshot.
#:
#: The deterministic seam relations (``http_route_link`` / ``db_model_link``)
#: deliberately do NOT belong here. They are re-parsed out of the source on every
#: scan, so carrying them forward would resurrect routes and tables that have
#: since been renamed or deleted.
BRIDGE_RELATIONS = {"llm_cross_layer_link", "cross_layer_link"}

#: Deterministic, re-derivable cross-layer relations. Informational only --
#: kept separate from BRIDGE_RELATIONS on purpose.
DETERMINISTIC_RELATIONS = {
    extractors.HTTP_ROUTE_RELATION,
    extractors.DB_MODEL_RELATION,
    extractors.CALLS_ENDPOINT_RELATION,
    extractors.HANDLED_BY_RELATION,
}

#: Minimum vector-match score required before a bridge edge is created, for the
#: **TF-IDF** backend. Kept as a module constant because it is part of the public
#: surface (the agent contract quotes it, and tests import it).
#:
#: Do NOT compare a live search result against this directly: the hybrid backend
#: returns a fused score on a different scale and has its own calibrated floor.
#: Use :func:`bridge_score_floor` (or ``store.score_floor``) instead.
BRIDGE_SCORE_FLOOR = SCORE_FLOORS[BACKEND_TFIDF]


def bridge_score_floor(vector_store: LocalVectorStore) -> float:
    """The bridge floor calibrated for whichever backend *vector_store* is really running."""
    try:
        return float(vector_store.score_floor)
    except Exception:
        return BRIDGE_SCORE_FLOOR

#: Schema version of .tldrgraph/graph.json
SNAPSHOT_SCHEMA_VERSION = 1

#: Filename of the persisted TLDRGraph graph snapshot (inside .tldrgraph/).
SNAPSHOT_FILENAME = "graph.json"


def placeholder_summary(layer: str, label: str, file_path: str) -> str:
    """The generated summary used before any enrichment has happened."""
    return f"{layer}: {label} located at {file_path}"


def resolve_call_target(
    graph: nx.DiGraph,
    vector_store: LocalVectorStore,
    call_target: Any,
    source_id: str,
    floor: Optional[float] = None,
) -> Tuple[Optional[str], float]:
    """
    High-precision multi-tier resolution of downstream call targets specified by agent/LLM:
    1. Structured Target:
       - dict with {"id": ...} or {"target_id": ...} -> exact ID match.
       - dict with {"file": ..., "label": ...} -> matched by (file, label).
    2. Exact node_id in graph.
    3. Qualified string ("file_path:symbol" or "file_path#symbol"):
       - Matches symbol within the specified file path.
    4. Exact source file match:
       - "path/to/file.ts" matches file/module node.
    5. Exact symbol label match with Smart Disambiguation:
       - If multiple candidates share the same label (e.g. active service vs dead code):
         a. Prioritize active/live nodes over dead_code_status == "candidate".
         b. Prioritize exact case over case-insensitive match.
         c. Prioritize nodes in the same directory/module as source_id.
    6. Vector search fallback with calibrated score floor.

    Returns (target_node_id, confidence) or (None, 0.0).
    """
    if call_target is None:
        return None, 0.0

    if floor is None:
        floor = bridge_score_floor(vector_store)

    target_id: Optional[str] = None
    target_file: Optional[str] = None
    target_symbol: Optional[str] = None

    # 1. Handle structured dict target
    if isinstance(call_target, dict):
        target_id = call_target.get("id") or call_target.get("target_id")
        target_file = call_target.get("file") or call_target.get("path")
        target_symbol = call_target.get("label") or call_target.get("symbol")
        if target_id and graph.has_node(str(target_id)) and str(target_id) != source_id:
            return str(target_id), 1.0
        if not target_symbol and not target_file:
            return None, 0.0
    else:
        target_str = str(call_target).strip()
        if not target_str:
            return None, 0.0

        # 2. Check exact node_id in graph
        if graph.has_node(target_str) and target_str != source_id:
            return target_str, 1.0

        # 3. Check qualified string: "path/to/file.ts:symbol" or "path/to/file.ts#symbol"
        if ":" in target_str and not target_str.startswith("http"):
            parts = target_str.split(":", 1)
            target_file, target_symbol = parts[0].strip(), parts[1].strip()
        elif "#" in target_str:
            parts = target_str.split("#", 1)
            target_file, target_symbol = parts[0].strip(), parts[1].strip()
        else:
            target_symbol = target_str

    source_file = ""
    if graph.has_node(source_id):
        source_file = str(graph.nodes[source_id].get("file") or graph.nodes[source_id].get("source_file") or "")
    source_dir = os.path.dirname(source_file)

    # 4. If both file and symbol are specified (or parsed from qualified string)
    if target_file and target_symbol:
        target_file_norm = target_file.replace("\\", "/").lower()
        target_sym_lower = target_symbol.lower()
        for nid, data in graph.nodes(data=True):
            if nid == source_id:
                continue
            file_path = str(data.get("file") or data.get("source_file") or "").replace("\\", "/").lower()
            label = str(data.get("label") or "")
            if (file_path == target_file_norm or file_path.endswith("/" + target_file_norm) or os.path.basename(file_path) == target_file_norm):
                if label == target_symbol or label.lower() == target_sym_lower:
                    return nid, 1.0

    # 5. If target matches a file path directly
    if target_file and not target_symbol:
        target_file_norm = target_file.replace("\\", "/").lower()
        for nid, data in graph.nodes(data=True):
            if nid == source_id:
                continue
            file_path = str(data.get("file") or data.get("source_file") or "").replace("\\", "/").lower()
            if file_path == target_file_norm or file_path.endswith("/" + target_file_norm) or os.path.basename(file_path) == target_file_norm:
                return nid, 1.0

    # 6. Exact label match with Smart Disambiguation
    if target_symbol:
        target_sym_lower = target_symbol.lower()
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for nid, data in graph.nodes(data=True):
            if nid == source_id:
                continue
            label = str(data.get("label") or "")
            file_path = str(data.get("file") or data.get("source_file") or "")
            file_base = os.path.basename(file_path)
            if label == target_symbol or label.lower() == target_sym_lower or file_path == target_symbol or file_base == target_symbol:
                candidates.append((nid, data))

        if len(candidates) == 1:
            return candidates[0][0], 1.0

        if len(candidates) > 1:
            # Score candidates to disambiguate active components from dead code
            def _rank_candidate(cand: Tuple[str, Dict[str, Any]]) -> Tuple[int, int, int]:
                nid, data = cand
                label = str(data.get("label") or "")
                c_file = str(data.get("file") or data.get("source_file") or "")
                c_dir = os.path.dirname(c_file)
                case_score = 2 if label == target_symbol else 1
                dead_code_score = 0 if data.get("dead_code_status") == "candidate" else 2
                dir_score = 2 if (source_dir and c_dir == source_dir) else 0
                return (dead_code_score, case_score, dir_score)

            candidates.sort(key=_rank_candidate, reverse=True)
            return candidates[0][0], 1.0

    # 7. Vector search fallback
    search_query = target_symbol or str(call_target)
    matches = vector_store.search(search_query, top_k=1)
    if matches:
        tgt_doc, score = matches[0]
        tgt_id = tgt_doc.get("id")
        if tgt_id and tgt_id != source_id and score >= floor:
            return tgt_id, float(score)

    return None, 0.0


class GraphLoader:
    def __init__(self, root_dir: str = ".", embeddings: Optional[str] = None):
        """
        *embeddings* selects the retrieval backend policy (``off`` / ``auto`` /
        ``on``). ``None`` -- the default -- defers to ``TLDRGRAPH_EMBEDDINGS``,
        which itself defaults to ``off``, i.e. pure TF-IDF.
        """
        self.root_dir = os.path.abspath(root_dir)
        self.registry, self.layers_config_hash = load_layer_config(self.root_dir)
        self.graph = nx.DiGraph()
        #: Display-name keyed buckets. Kept keyed by name for backward
        #: compatibility with existing callers, exports and tests.
        self.nodes_by_layer: Dict[str, List[Dict[str, Any]]] = {}
        #: The same list objects keyed by stable layer id -- what logic reads.
        self.nodes_by_layer_id: Dict[str, List[Dict[str, Any]]] = {}
        self._reset_layer_buckets()
        self.docs_to_index: List[Dict[str, Any]] = []
        self.restored_clean_ids: Set[str] = set()
        #: relation -> number of deterministic edges added by the last scan
        self.deterministic_edge_counts: Dict[str, int] = {}
        #: Prisma models synthesized into Layer 4 by the last scan
        self.prisma_model_count: int = 0
        #: Endpoint identities synthesized into the API layer by the last scan
        self.endpoint_count: int = 0
        #: Endpoint records from the last scan (see extractors.collect_endpoints)
        self.endpoints: List[Dict[str, Any]] = []
        self.hash_gate = HashGate(os.path.join(self.root_dir, ".tldrgraph/tldrgraph.db"))
        self.vector_store = LocalVectorStore(
            os.path.join(self.root_dir, ".tldrgraph/vector_index.json"),
            embeddings=embeddings,
        )
        self.enricher = LLMEnricher()
        self.file_hashes: Dict[str, str] = self._load_file_hashes()

    def _reset_layer_buckets(self) -> None:
        """(Re)creates the per-layer node buckets from the active layer registry."""
        self.nodes_by_layer = {}
        self.nodes_by_layer_id = {}
        for layer in get_registry():
            bucket: List[Dict[str, Any]] = []
            self.nodes_by_layer[layer.name] = bucket
            self.nodes_by_layer_id[layer.id] = bucket

    # ------------------------------------------------------------------ #
    # Content signatures (hash gate)
    # ------------------------------------------------------------------ #

    def _load_file_hashes(self) -> Dict[str, str]:
        """
        Loads graphify-out/manifest.json into {repo_relative_path: content_hash}.
        Prefers semantic_hash and falls back to ast_hash.
        """
        manifest_path = os.path.join(self.root_dir, "graphify-out", "manifest.json")
        if not os.path.exists(manifest_path):
            return {}
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            return {}
        if not isinstance(manifest, dict):
            return {}

        hashes: Dict[str, str] = {}
        for rel_path, meta in manifest.items():
            if not isinstance(meta, dict):
                continue
            file_hash = meta.get("semantic_hash") or meta.get("ast_hash")
            if file_hash:
                hashes[rel_path] = str(file_hash)
        return hashes

    def _disk_hash(self, file_path: str) -> Optional[str]:
        """sha256 of a file's bytes, memoized into self.file_hashes."""
        abs_path = os.path.join(self.root_dir, file_path)
        if not os.path.isfile(abs_path):
            return None
        try:
            with open(abs_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return None
        self.file_hashes[file_path] = file_hash
        return file_hash

    def node_signature(self, node_attrs: Dict[str, Any]) -> str:
        """
        Content signature used by the hash gate. Built from the *file content* hash
        so that rewriting a function body correctly marks its node dirty.
        Falls back to hashing the file on disk, then to the legacy label+file.
        """
        node_id = str(node_attrs.get("id") or "")
        label = node_attrs.get("label") or ""
        file_path = node_attrs.get("file") or ""
        source_location = node_attrs.get("source_location") or ""

        file_hash = self.file_hashes.get(file_path)
        if not file_hash and file_path:
            file_hash = self._disk_hash(file_path)
        if not file_hash:
            return label + file_path
        return f"{file_hash}|{node_id}|{source_location}"

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def snapshot_path(self) -> str:
        return os.path.join(self.root_dir, ".tldrgraph", SNAPSHOT_FILENAME)

    def save_graph(self) -> str:
        """
        Persists the full node + edge graph (including cross-layer bridges) to
        .tldrgraph/graph.json. Returns the written path.
        """
        out_path = self.snapshot_path()
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            nodes.append({
                "id": str(node_id),
                "label": data.get("label", str(node_id)),
                # Owner-qualified, for humans and renderers only. `label` above
                # is what the vector store indexes and what agent-supplied call
                # targets are matched against -- see tldrgraph.labels.
                "display_label": data.get("display_label") or data.get("label", str(node_id)),
                "file": data.get("file", ""),
                "layer": data.get("layer", get_registry().utility.name),
                # Stable machine id. Everything downstream keys off this; the
                # display name above is preserved only for humans and for
                # artifacts written before this field existed.
                "layer_id": layer_id_of(data),
                "layer_source": data.get("layer_source", "rule"),
                "type": data.get("type", "symbol"),
                "community": data.get("community"),
                "degree": data.get("degree", 0),
                "summary": data.get("summary", ""),
                "input_fields": data.get("input_fields", []),
                "output_fields": data.get("output_fields", []),
                "fields": data.get("fields", []) or (list(data.get("input_fields", [])) + list(data.get("output_fields", []))),
                "intent": data.get("intent", ""),
                "enrichment_source": data.get("enrichment_source", ""),
                "source_location": data.get("source_location"),
                "dead_code_status": data.get("dead_code_status", ""),
                "dead_code_reason": data.get("dead_code_reason", ""),
                "is_test": bool(data.get("is_test", is_test_node(data.get("file", ""), data.get("label", "")))),
                "signature": self.node_signature(data),
            })

        edges = []
        for src, tgt, data in self.graph.edges(data=True):
            edges.append({
                "source": str(src),
                "target": str(tgt),
                "relation": data.get("relation", "calls"),
                "confidence": float(data.get("confidence", 1.0)),
            })

        snapshot = {
            "tldrgraph_version": __version__,
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "layers_config_hash": getattr(self, "layers_config_hash", ""),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "nodes": nodes,
            "edges": edges,
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        return out_path

    def load_graph_snapshot(self) -> Optional[Dict[str, Any]]:
        """Reads .tldrgraph/graph.json. Returns None if absent or corrupt."""
        snapshot_path = self.snapshot_path()
        if not os.path.exists(snapshot_path):
            return None
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return None

        # Snapshots written before layer_id existed carry only the display name.
        # Derive the id once, here, so no consumer has to think about it.
        registry = get_registry()
        for node in data["nodes"]:
            if isinstance(node, dict) and not node.get("layer_id"):
                node["layer_id"] = registry.id_for_name(str(node.get("layer") or ""))
        return data

    #: Structural tail of a generated summary, independent of the layer name.
    PLACEHOLDER_TAIL = "{label} located at {file}"

    def _is_placeholder_summary(self, summary: str, node_attrs: Dict[str, Any]) -> bool:
        """
        Is this summary machine-generated rather than real enrichment?

        Decided from the CONTENT, by matching the *tail* of the generated form --
        deliberately NOT the whole string,
        because the full form embeds the layer's DISPLAY NAME. Renaming a layer
        (which a later phase allows) would make an exact match fail, and every
        stale placeholder would then be carried forward as genuine enrichment,
        inflating the coverage gate that promotes nodes to deletion candidates.

        Content is the single source of truth here on purpose: a stored boolean
        goes stale the moment anything assigns `summary` directly, and a stale
        flag silently discards real enrichment.
        """
        if not summary:
            return True
        tail = self.PLACEHOLDER_TAIL.format(
            label=node_attrs.get("label", ""),
            file=node_attrs.get("file", ""),
        )
        return summary.endswith(tail)

    def _carry_forward_snapshot(self) -> Tuple[int, int]:
        """
        Merges previously persisted enrichment back onto the freshly built graph:
        intents, fields, enriched summaries, and cross-layer bridge edges.
        Returns (nodes_restored, edges_restored).
        """
        snapshot = self.load_graph_snapshot()
        if not snapshot:
            return 0, 0

        nodes_restored = 0
        self.restored_clean_ids = set()
        for old in snapshot.get("nodes", []):
            if not isinstance(old, dict):
                continue
            node_id = str(old.get("id"))
            if not self.graph.has_node(node_id):
                continue

            current = self.graph.nodes[node_id]
            touched = False

            # If the node was explicitly assigned to a layer by an agent, preserve that
            if old.get("layer_source") == "agent":
                old_lid = old.get("layer_id")
                if old_lid and old_lid in get_registry():
                    current["layer_id"] = old_lid
                    current["layer"] = get_registry().name(old_lid)
                    current["layer_source"] = "agent"

            intent = old.get("intent") or ""
            if intent:
                current["intent"] = intent
                # Provenance travels with the intent: heuristic template text must
                # keep looking like heuristic text after a rescan, or it would
                # silently start counting towards dead-code enrichment coverage.
                current["enrichment_source"] = old.get("enrichment_source") or ""
                touched = True

            input_fields = old.get("input_fields") or []
            if input_fields:
                current["input_fields"] = input_fields
                touched = True

            output_fields = old.get("output_fields") or []
            if output_fields:
                current["output_fields"] = output_fields
                touched = True

            fields = old.get("fields") or []
            if fields:
                current["fields"] = fields
                touched = True
            elif input_fields or output_fields:
                current["fields"] = list(input_fields) + list(output_fields)
                touched = True

            if "is_test" in old:
                current["is_test"] = old["is_test"]
            else:
                current["is_test"] = is_test_node(current.get("file", ""), current.get("label", ""))

            summary = old.get("summary") or ""
            if summary and not self._is_placeholder_summary(summary, old) \
                    and not self._is_placeholder_summary(summary, current):
                # If layer name changed between scans, update the summary prefix
                if intent:
                    current["summary"] = f"{current['layer']}: {current['label']} - {intent}"
                else:
                    current["summary"] = summary
                touched = True

            if touched:
                nodes_restored += 1

            # If the persisted signature still matches the file content, the restored
            # enrichment is current -- the node must not be re-sent to the enricher
            # (which would overwrite a rich agent intent with a generic one).
            if current.get("intent") and old.get("signature") \
                    and old["signature"] == self.node_signature(current):
                self.restored_clean_ids.add(node_id)

        edges_restored = 0
        for old_edge in snapshot.get("edges", []) or []:
            if not isinstance(old_edge, dict):
                continue
            relation = old_edge.get("relation")
            if relation not in BRIDGE_RELATIONS:
                continue
            src = str(old_edge.get("source"))
            tgt = str(old_edge.get("target"))
            if not (self.graph.has_node(src) and self.graph.has_node(tgt)):
                continue
            try:
                confidence = float(old_edge.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            self.graph.add_edge(src, tgt, relation=relation, confidence=confidence)
            edges_restored += 1

        return nodes_restored, edges_restored

    def _run_graphify(self) -> str:
        """
        Runs graphify AST extraction and builds graphify-out/graph.json.
        TLDRGraph relies directly on graphify as the core extraction engine.
        """
        from pathlib import Path
        from graphify.detect import detect, save_manifest
        from graphify.extract import collect_files, extract
        from graphify.build import build_from_json
        from graphify.cluster import cluster
        from graphify.export import to_json

        root_path = Path(self.root_dir).resolve()
        out_dir = root_path / "graphify-out"
        out_dir.mkdir(parents=True, exist_ok=True)
        graph_json_path = out_dir / "graph.json"

        det = detect(root_path)
        code_files = []
        for f in det.get("files", {}).get("code", []):
            p = Path(f)
            code_files.extend(collect_files(p) if p.is_dir() else [p])

        if code_files:
            extraction = extract(code_files, cache_root=root_path)
            G = build_from_json(extraction, root=str(root_path))
            communities = cluster(G) if G.number_of_nodes() > 0 else {}
            to_json(G, communities, str(graph_json_path), force=True)
        else:
            G = nx.DiGraph()
            to_json(G, {}, str(graph_json_path), force=True)

        if det.get("files"):
            try:
                save_manifest(det["files"], str(out_dir / "manifest.json"), root=root_path)
            except Exception:
                pass

        return str(graph_json_path)

    # ------------------------------------------------------------------ #
    # Build
    # ------------------------------------------------------------------ #

    def load_or_extract(self, enrich_llm: bool = True, rebuild: bool = False) -> nx.DiGraph:
        graph_json_path = os.path.join(self.root_dir, "graphify-out", "graph.json")

        if not os.path.exists(graph_json_path):
            self._run_graphify()

        # Reload latest layer config from disk / environment
        self.registry, self.layers_config_hash = load_layer_config(self.root_dir)

        # Nodes and AST edges are always rebuilt fresh from graphify.
        self.graph = nx.DiGraph()
        self._reset_layer_buckets()
        self.docs_to_index = []
        docs_to_index = self.docs_to_index
        dirty_nodes_for_llm: List[Dict[str, Any]] = []

        if os.path.exists(graph_json_path):
            with open(graph_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_nodes = data.get("nodes", [])
            raw_edges = data.get("links", data.get("edges", []))

            for node in raw_nodes:
                node_id = str(node.get("id"))
                label = node.get("label") or node_id
                file_path = node.get("source_file") or node.get("file") or node.get("path") or ""
                layer_obj, layer_source = classify_node_with_source(node_id, node)
                layer_id = layer_obj.id
                layer = layer_obj.name

                node_attrs = {
                    "id": node_id,
                    "label": label,
                    "file": file_path,
                    "layer": layer,
                    "layer_id": layer_id,
                    "layer_source": layer_source,
                    "type": node.get("file_type", "symbol"),
                    "community": node.get("community"),
                    "degree": node.get("degree", 0),
                    "source_location": node.get("source_location"),
                    "summary": placeholder_summary(layer, label, file_path),
                    "is_test": is_test_node(file_path, label),
                    "input_fields": [],
                    "output_fields": [],
                    "fields": [],
                    "intent": "",
                    "enrichment_source": ""
                }

                # Register first, then work with the LIVE networkx attribute dict so
                # that every later mutation propagates into the indexed documents.
                self.graph.add_node(node_id, **node_attrs)
                live_attrs = self.graph.nodes[node_id]

                # Check hash gate against a real content signature
                is_dirty, cached = self.hash_gate.check_node(node_id, self.node_signature(live_attrs))
                cached_summary = (cached or {}).get("summary") or ""
                usable_cache = (
                    not is_dirty
                    and cached_summary
                    and not self._is_placeholder_summary(cached_summary, live_attrs)
                )

                if usable_cache:
                    live_attrs["summary"] = cached_summary
                    live_attrs["intent"] = cached.get("intent") or ""
                    # The SQLite cache has no provenance column. Mark it as such;
                    # _carry_forward_snapshot() runs later and overwrites this with
                    # the real source recorded in .tldrgraph/graph.json.
                    if live_attrs["intent"]:
                        live_attrs["enrichment_source"] = "cache"
                    try:
                        raw_fields = json.loads(cached.get("fields_json") or "[]")
                        if isinstance(raw_fields, dict):
                            live_attrs["input_fields"] = raw_fields.get("input_fields", [])
                            live_attrs["output_fields"] = raw_fields.get("output_fields", [])
                            live_attrs["fields"] = raw_fields.get("fields", []) or (list(live_attrs["input_fields"]) + list(live_attrs["output_fields"]))
                        elif isinstance(raw_fields, list):
                            live_attrs["input_fields"] = raw_fields
                            live_attrs["output_fields"] = []
                            live_attrs["fields"] = raw_fields
                    except Exception:
                        pass
                elif enrich_llm and not get_registry().is_utility(layer_id):
                    dirty_nodes_for_llm.append(live_attrs)

                self.nodes_by_layer_id[layer_id].append(live_attrs)
                docs_to_index.append(live_attrs)

            for edge in raw_edges:
                src = str(edge.get("source"))
                tgt = str(edge.get("target"))
                relation = edge.get("relation", edge.get("kind", "calls"))
                try:
                    confidence = float(edge.get("confidence_score", 1.0))
                except (TypeError, ValueError):
                    confidence = 1.0
                if self.graph.has_node(src) and self.graph.has_node(tgt):
                    self.graph.add_edge(src, tgt, relation=relation, confidence=confidence)

        # Ingest Layer 6 DevOps files
        self._scan_devops_files(docs_to_index)

        # Synthesize Layer 4 nodes for the real database tables. graphify never
        # sees schema.prisma, so without this the persistence layer contains only
        # the Prisma client plumbing and no actual model.
        self._register_prisma_model_nodes(
            docs_to_index, enrich_llm=enrich_llm, dirty_nodes_for_llm=dirty_nodes_for_llm
        )

        # Synthesize one API-layer node per endpoint identity. Same reasoning as
        # the Prisma models: a route is a real thing in this system, and until it
        # had a node it could only ever be an edge label -- which is why the same
        # route showed up once per call site downstream.
        self._register_endpoint_nodes(
            docs_to_index, enrich_llm=enrich_llm, dirty_nodes_for_llm=dirty_nodes_for_llm
        )

        # Deterministic cross-layer seams. Must run after every node exists (the
        # Prisma models and endpoints above are edge targets) and before
        # save_graph(). These edges are re-derived from source on every scan and
        # are deliberately not carried forward from the snapshot -- see
        # BRIDGE_RELATIONS.
        self._apply_deterministic_edges()

        # Owner-qualified display labels. Runs once every node and every
        # containment edge exists; writes only `display_label`, never `label`.
        self._apply_display_labels()

        # Restore previously persisted enrichment & bridge edges
        self.restored_clean_ids = set()
        if not rebuild:
            self._carry_forward_snapshot()
            # Drop nodes whose restored enrichment is still current for their content.
            if self.restored_clean_ids:
                dirty_nodes_for_llm = [
                    n for n in dirty_nodes_for_llm
                    if n.get("id") not in self.restored_clean_ids
                ]

        # Index BEFORE enrichment so bridge resolution searches a populated index
        self.vector_store.add_documents(docs_to_index)

        # Batch LLM Enrichment for dirty / un-enriched high-value nodes
        if enrich_llm and dirty_nodes_for_llm:
            self._run_llm_enrichment(dirty_nodes_for_llm)

        # Re-index so freshly generated intents / fields / summaries are searchable
        self.vector_store.add_documents(docs_to_index)

        # Reachability cascade -- LAST, once AST, deterministic and LLM/agent
        # edges are all in place. An edge added after this point would silently
        # invalidate every dead_code_status computed here.
        classify_dead_code(self.graph, compute_enrichment_coverage(self.graph),
                           root_dir=self.root_dir)

        self.save_graph()
        self.export_yaml()
        return self.graph

    # ------------------------------------------------------------------ #
    # Deterministic seams
    # ------------------------------------------------------------------ #

    def _register_prisma_model_nodes(self, docs_to_index: List[Dict[str, Any]],
                                     enrich_llm: bool = False,
                                     dirty_nodes_for_llm: Optional[List[Dict[str, Any]]] = None) -> int:
        """
        Creates one Layer 4 node per ``model X { }`` block in schema.prisma.

        Registered exactly like a graphify node: hash-gated, appended to
        nodes_by_layer and docs_to_index as the LIVE networkx attribute dict so
        later enrichment propagates into the index.
        """
        data_layer = get_registry().by_id(LAYER_DATA) or get_registry().utility
        layer_id = data_layer.id
        layer = data_layer.name
        created = 0

        for model in extractors.collect_prisma_models(self.root_dir):
            node_id = extractors.prisma_model_node_id(model["name"])
            if self.graph.has_node(node_id):
                continue

            label = model["name"]
            file_path = model["file"]
            node_attrs = {
                "id": node_id,
                "label": label,
                "file": file_path,
                "layer": layer,
                "layer_id": layer_id,
                "layer_source": "rule",
                "type": "db_model",
                "community": None,
                "degree": 0,
                "source_location": f"L{model['line']}",
                "summary": placeholder_summary(layer, label, file_path),
                # Real column names parsed out of the schema -- not invented.
                "fields": list(model.get("fields") or []),
                "intent": "",
                "enrichment_source": ""
            }

            self.graph.add_node(node_id, **node_attrs)
            live_attrs = self.graph.nodes[node_id]

            is_dirty, cached = self.hash_gate.check_node(node_id, self.node_signature(live_attrs))
            cached_summary = (cached or {}).get("summary") or ""
            if not is_dirty and cached_summary \
                    and not self._is_placeholder_summary(cached_summary, live_attrs):
                live_attrs["summary"] = cached_summary
                live_attrs["intent"] = cached.get("intent") or ""
                if live_attrs["intent"]:
                    live_attrs["enrichment_source"] = "cache"
            elif enrich_llm and dirty_nodes_for_llm is not None:
                dirty_nodes_for_llm.append(live_attrs)

            self.nodes_by_layer_id[layer_id].append(live_attrs)
            docs_to_index.append(live_attrs)
            created += 1

        self.prisma_model_count = created
        return created

    def _register_endpoint_nodes(self, docs_to_index: List[Dict[str, Any]],
                                 enrich_llm: bool = False,
                                 dirty_nodes_for_llm: Optional[List[Dict[str, Any]]] = None) -> int:
        """
        Creates one API-layer node per normalized ``METHOD /path``.

        Endpoints are derived from *backend route declarations*, so each node can
        carry a real ``source_location`` pointing at the decorator that declares
        it. A frontend call that matches no declared route contributes no node --
        the same rule ``http_route_link`` already follows.

        Registered exactly like a Prisma model node: hash-gated against the
        controller file's content, appended to ``nodes_by_layer`` and
        ``docs_to_index`` as the LIVE attribute dict.
        """
        api_layer = get_registry().by_id(LAYER_API) or get_registry().utility
        created = 0

        try:
            frontend_calls = extractors.collect_frontend_calls(self.root_dir)
            backend_routes = extractors.collect_backend_routes(self.root_dir)
        except OSError:
            frontend_calls, backend_routes = [], []

        self.endpoints = extractors.collect_endpoints(frontend_calls, backend_routes)

        for endpoint in self.endpoints:
            node_id = endpoint["id"]
            if self.graph.has_node(node_id):
                continue

            label = endpoint["label"]
            file_path = endpoint["file"]
            handlers = endpoint.get("handlers") or []
            node_attrs = {
                "id": node_id,
                "label": label,
                "display_label": label,
                "file": file_path,
                "layer": api_layer.name,
                "layer_id": api_layer.id,
                "layer_source": "rule",
                "type": extractors.ENDPOINT_NODE_TYPE,
                "community": None,
                "degree": 0,
                "source_location": f"L{endpoint['line']}" if endpoint.get("line") else None,
                "summary": placeholder_summary(api_layer.name, label, file_path),
                "is_test": False,
                "fields": [],
                "intent": "",
                "enrichment_source": "",
                # Provenance for consumers: which declarations and which call
                # sites folded into this one identity.
                "method": endpoint["method"],
                "path": endpoint["path"],
                "handlers": handlers,
                "call_site_count": len(endpoint.get("call_sites") or []),
            }

            self.graph.add_node(node_id, **node_attrs)
            live_attrs = self.graph.nodes[node_id]

            is_dirty, cached = self.hash_gate.check_node(node_id, self.node_signature(live_attrs))
            cached_summary = (cached or {}).get("summary") or ""
            if not is_dirty and cached_summary \
                    and not self._is_placeholder_summary(cached_summary, live_attrs):
                live_attrs["summary"] = cached_summary
                live_attrs["intent"] = cached.get("intent") or ""
                if live_attrs["intent"]:
                    live_attrs["enrichment_source"] = "cache"
            elif enrich_llm and dirty_nodes_for_llm is not None:
                dirty_nodes_for_llm.append(live_attrs)

            self.nodes_by_layer_id[api_layer.id].append(live_attrs)
            docs_to_index.append(live_attrs)
            created += 1

        self.endpoint_count = created
        return created

    def _apply_display_labels(self) -> int:
        """
        Stamps ``display_label`` on every node. Never touches ``label``.
        """
        node_records = [{"id": node_id, **data} for node_id, data in self.graph.nodes(data=True)]
        edge_records = [
            {"source": src, "target": tgt, "relation": data.get("relation")}
            for src, tgt, data in self.graph.edges(data=True)
        ]
        display = build_display_labels(node_records, edge_records)
        for node_id, value in display.items():
            self.graph.nodes[node_id]["display_label"] = value
        return len(display)

    def _apply_deterministic_edges(self) -> Dict[str, int]:
        """
        Re-derives the HTTP route seam (Layer 1 -> Layer 2), the endpoint seams
        (caller -> endpoint -> handler) and the Prisma model seam (caller ->
        Layer 4) from the source text and adds them to the graph.

        ``http_route_link`` is **kept alongside** the endpoint seam rather than
        being replaced by it. It is the direct caller->handler shortcut that the
        flow engine, the dead-code cascade and the existing tests all read; the
        endpoint pair adds the routed path *through* the endpoint identity. One
        is a summary of the other, and both are cheap.

        Existing edges are never overwritten: an AST relation carries more
        information than a re-derived seam, so it wins.
        """
        counts: Dict[str, int] = {relation: 0 for relation in DETERMINISTIC_RELATIONS}

        # Endpoint nodes are deliberately excluded. They are recorded at the
        # decorator's line inside the controller, so an endpoint would win the
        # nearest-declaration lookup and a route would resolve to itself instead
        # of to its handler.
        index = extractors.NodeIndex(
            {"id": node_id, **data}
            for node_id, data in self.graph.nodes(data=True)
            if data.get("type") != extractors.ENDPOINT_NODE_TYPE
        )

        try:
            frontend_calls = extractors.collect_frontend_calls(self.root_dir)
            backend_routes = extractors.collect_backend_routes(self.root_dir)
            http_edges = extractors.build_http_route_edges(frontend_calls, backend_routes, index)
        except OSError:
            http_edges = []

        endpoint_edges = extractors.build_endpoint_edges(self.endpoints, index)

        known_models = {
            data.get("label", "").lower(): data.get("label", "")
            for _, data in self.graph.nodes(data=True)
            if data.get("type") == "db_model"
        }
        known_models.pop("", None)

        try:
            relation_map = extractors.build_relation_map(
                extractors.collect_prisma_models(self.root_dir)
            )
            prisma_calls = extractors.collect_prisma_calls(
                self.root_dir, known_models, relation_map
            )
            db_edges = extractors.build_db_model_edges(prisma_calls, index)
        except OSError:
            db_edges = []

        for edge in list(http_edges) + list(endpoint_edges) + list(db_edges):
            src, tgt = edge["source"], edge["target"]
            if not (self.graph.has_node(src) and self.graph.has_node(tgt)):
                continue
            if self.graph.has_edge(src, tgt):
                continue
            self.graph.add_edge(
                src, tgt,
                relation=edge["relation"],
                confidence=float(edge.get("confidence", 1.0)),
            )
            counts[edge["relation"]] = counts.get(edge["relation"], 0) + 1

        self.deterministic_edge_counts = counts
        return counts

    def _run_llm_enrichment(self, nodes_to_enrich: List[Dict[str, Any]], batch_size: int = 15):
        for i in range(0, len(nodes_to_enrich), batch_size):
            batch = nodes_to_enrich[i:i + batch_size]
            enriched_items = self.enricher.enrich_batch(batch)

            for item in enriched_items:
                nid = item.get("id")
                if not nid or not self.graph.has_node(nid):
                    continue

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

                # Update HashGate cache
                fields_dict = {
                    "input_fields": node_data.get("input_fields", []),
                    "output_fields": node_data.get("output_fields", []),
                    "fields": node_data.get("fields", [])
                }
                self.hash_gate.update_node(
                    node_id=nid,
                    file_path=node_data.get("file", ""),
                    content=self.node_signature(node_data),
                    layer=node_data.get("layer", ""),
                    summary=node_data.get("summary", ""),
                    fields_json=json.dumps(fields_dict),
                    intent=node_data.get("intent", "")
                )

                # Link cross-layer calls if targets match confidently enough.
                # The floor tracks the live retrieval backend -- a fused hybrid
                # score is not comparable with a raw TF-IDF cosine.
                floor = bridge_score_floor(self.vector_store)
                for call_target in calls:
                    matches = self.vector_store.search(call_target, top_k=1)
                    if not matches:
                        continue
                    tgt_doc, score = matches[0]
                    tgt_id = tgt_doc.get("id")
                    if tgt_id and tgt_id != nid and score >= floor:
                        self.graph.add_edge(
                            nid, tgt_id,
                            relation="llm_cross_layer_link",
                            confidence=float(score)
                        )

    def _scan_devops_files(self, docs_to_index: List[Dict[str, Any]]):
        devops_paths = [
            "docker", "charts", "backend/Dockerfile", "frontend/Dockerfile",
            "frontend/.github/workflows", ".github/workflows"
        ]
        devops_layer_obj = get_registry().by_id(LAYER_DEVOPS) or get_registry().utility
        devops_layer = devops_layer_obj.name
        devops_layer_id = devops_layer_obj.id

        for rel_target in devops_paths:
            full_path = os.path.join(self.root_dir, rel_target)
            if not os.path.exists(full_path):
                continue

            if os.path.isfile(full_path):
                node_id = f"devops_{rel_target.replace('/', '_').replace('.', '_')}"
                node_attrs = {
                    "id": node_id,
                    "label": os.path.basename(rel_target),
                    "file": rel_target,
                    "layer": devops_layer,
                    "layer_id": devops_layer_id,
                    "layer_source": "rule",
                    "type": "devops_config",
                    "community": None,
                    "degree": 0,
                    "source_location": None,
                    "summary": f"DevOps Infrastructure file: {rel_target}",
                    "fields": [],
                    "intent": "",
                    "enrichment_source": ""
                }
                self.graph.add_node(node_id, **node_attrs)
                live_attrs = self.graph.nodes[node_id]
                self.nodes_by_layer_id[devops_layer_id].append(live_attrs)
                docs_to_index.append(live_attrs)
            elif os.path.isdir(full_path):
                for root, _, files in os.walk(full_path):
                    for f in files:
                        if f.startswith("."):
                            continue
                        f_rel = os.path.relpath(os.path.join(root, f), self.root_dir)
                        node_id = f"devops_{f_rel.replace('/', '_').replace('.', '_')}"
                        node_attrs = {
                            "id": node_id,
                            "label": f,
                            "file": f_rel,
                            "layer": devops_layer,
                            "layer_id": devops_layer_id,
                            "layer_source": "rule",
                            "type": "devops_config",
                            "community": None,
                            "degree": 0,
                            "source_location": None,
                            "summary": f"DevOps Infrastructure: {f_rel}",
                            "fields": [],
                            "intent": "",
                            "enrichment_source": ""
                        }
                        self.graph.add_node(node_id, **node_attrs)
                        live_attrs = self.graph.nodes[node_id]
                        self.nodes_by_layer_id[devops_layer_id].append(live_attrs)
                        docs_to_index.append(live_attrs)

    def export_yaml(self, output_dir: str = None) -> str:
        """
        Exports the classified multi-layer architecture into YAML for visualization/dashboards.
        """
        if output_dir is None:
            output_dir = os.path.join(self.root_dir, ".tldrgraph")
        os.makedirs(output_dir, exist_ok=True)

        yaml_path = os.path.join(output_dir, "layers.yaml")

        summary_data = {
            "tldrgraph_version": __version__,
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "layers": {}
        }

        for layer_name, nodes in self.nodes_by_layer.items():
            summary_data["layers"][layer_name] = {
                "count": len(nodes),
                "nodes": [
                    {
                        "id": n["id"],
                        "label": n["label"],
                        "file": n["file"]
                    } for n in nodes[:50] # Top 50 sample nodes per layer in summary
                ]
            }

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(summary_data, f, default_flow_style=False, sort_keys=False)

        return yaml_path
