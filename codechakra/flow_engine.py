"""
Flow Engine for CodeChakra: Multi-layer pathfinding and YAML / Markdown export.

Design notes
------------
The engine has two jobs that used to be conflated:

1. *Resolving* a human string ("AaoDeskView", "pension approval flow") to a single
   node **id** in the graph.
2. *Traversing* from that id so that the resulting steps read as an execution flow
   across the 6 architectural layers.

Resolution is now id-first and explicit (see ``_resolve_node_id``), and traversal
is bridge-aware: edges that cross a layer boundary -- and especially the persisted
cross-layer *bridge* relations -- are expanded before same-layer edges, so a
cross-layer hop can never be starved by a crowd of sibling symbols.
"""

import os
import re
import heapq
import itertools
import yaml
import networkx as nx
from typing import List, Dict, Any, Tuple, Optional, Set
from tabulate import tabulate
from .vector_store import LocalVectorStore
from .layers import get_registry, layer_id_of

# --------------------------------------------------------------------------- #
# Bridge relations
#
# The authoritative set lives in graph_loader (those are the edges that are NOT
# re-derivable from graphify and therefore get carried forward across re-scans).
# We read it rather than hardcoding it, and union in the deterministic relation
# names that are being added in parallel -- tolerating their absence for now.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - trivial import guard
    from .graph_loader import BRIDGE_RELATIONS as _LOADER_BRIDGE_RELATIONS
except Exception:  # pragma: no cover
    _LOADER_BRIDGE_RELATIONS = set()

#: Deterministic bridges another producer may add. Listed here so the traversal
#: prioritises them the moment they start appearing in the graph.
DETERMINISTIC_BRIDGE_RELATIONS = {"http_route_link", "db_model_link"}

BRIDGE_RELATIONS: Set[str] = set(_LOADER_BRIDGE_RELATIONS) | DETERMINISTIC_BRIDGE_RELATIONS

#: Layer 1 -> Layer 6 -> everything else. The rank comes from each layer's
#: explicit ``order`` in the registry -- never from parsing the "Layer N: "
#: prefix out of a display name, and never from declaration order.

#: Traversal costs. A bridge is free, a plain layer change is cheap, and staying
#: inside the same layer is expensive -- so a Dijkstra expansion always drains the
#: cross-layer frontier before it wanders sideways.
BRIDGE_EDGE_COST = 0.0
CROSS_LAYER_EDGE_COST = 1.0
SAME_LAYER_EDGE_COST = 5.0

#: Default number of nodes a trace will collect. 10 could not fit a 6-layer path.
DEFAULT_MAX_STEPS = 25

#: How many vector hits to consider when re-ranking file-vs-symbol candidates.
CANDIDATE_POOL = 25

#: A candidate may only displace the top vector hit if it scores within this
#: *relative* margin of it. Keeps the symbol preference from overriding a
#: genuinely much better match.
MATCH_MARGIN = 0.15

#: Node ``type`` values that denote a container rather than something that acts.
CONTAINER_TYPES = {
    "file", "module", "directory", "dir", "folder", "package",
    "container", "devops_config", "concept",
}

#: File extensions that mark a label as a filename (graphify labels file nodes
#: with their basename, e.g. "AaoDeskView.tsx").
SOURCE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".go", ".rb",
    ".php", ".cs", ".rs", ".kt", ".swift", ".scala", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".m", ".mm", ".sql", ".prisma", ".graphql", ".proto", ".yml",
    ".yaml", ".json", ".toml", ".ini", ".cfg", ".env", ".md", ".sh", ".bash",
    ".tf", ".dockerfile", ".vue", ".svelte", ".html", ".css", ".scss",
}


class FlowEngine:
    def __init__(self, graph: nx.DiGraph, vector_store: LocalVectorStore, root_dir: str = "."):
        self.graph = graph
        self.vector_store = vector_store
        self.root_dir = root_dir

    # ------------------------------------------------------------------ #
    # Node identity helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_label(text: str) -> str:
        """
        Collapses a label/query to a comparable identifier key.
        ``"AaoDeskView()"`` -> ``"aaodeskview"``; ``"case_workflow"`` -> ``"caseworkflow"``.
        """
        t = (text or "").strip().lower()
        t = re.sub(r"\(\s*\)\s*$", "", t)
        return re.sub(r"[^a-z0-9]+", "", t)

    def _label_keys(self, data: Dict[str, Any]) -> Set[str]:
        """Every normalized key a node should answer to on an exact-name query."""
        label = data.get("label") or ""
        keys = {self._normalize_label(label)}
        stem, ext = os.path.splitext(label)
        if ext and stem:
            # so the *file* node "AaoDeskView.tsx" is still an exact-name candidate
            # for the query "AaoDeskView" -- and then loses the tie to the symbol.
            keys.add(self._normalize_label(stem))
        keys.discard("")
        return keys

    def _is_container_node(self, node_id: str, data: Dict[str, Any]) -> bool:
        """
        True for file / module / directory nodes -- things that *hold* code rather
        than things that *do* something. Three independent signals, because
        graphify tags every code node ``file_type == "code"`` and so ``type``
        alone cannot separate a file from a symbol:

        1. an explicit container ``type``;
        2. the label is the basename of the node's own file;
        3. the label carries a known source-file extension.
        """
        node_type = str(data.get("type") or "").lower()
        if node_type in CONTAINER_TYPES:
            return True

        label = data.get("label") or ""
        file_path = data.get("file") or ""
        if label and file_path and os.path.basename(file_path) == label:
            return True

        ext = os.path.splitext(label)[1].lower()
        if ext and ext in SOURCE_EXTENSIONS:
            return True

        return False

    def _has_bridge_out(self, node_id: str) -> bool:
        return any(
            d.get("relation") in BRIDGE_RELATIONS
            for _, _, d in self.graph.out_edges(node_id, data=True)
        )

    def _preference(self, node_id: str) -> float:
        """
        Structural desirability of a node as a *trace root*, independent of text
        relevance. Higher is better. Deliberately coarse so it only ever acts as
        a tiebreak between comparable matches.
        """
        if not self.graph.has_node(node_id):
            return -1.0
        data = self.graph.nodes[node_id]
        score = 0.0
        if not self._is_container_node(node_id, data):
            score += 2.0          # a symbol acts; a file merely contains
        if self.graph.out_degree(node_id) > 0:
            score += 1.0          # something to actually trace through
        if self._has_bridge_out(node_id):
            score += 0.5          # owns a cross-layer bridge
        return score

    # ------------------------------------------------------------------ #
    # Resolution: free text / node id  ->  node id
    # ------------------------------------------------------------------ #

    def _resolve_node_id(self, query: str) -> Optional[Tuple[str, float, Dict[str, Any]]]:
        """
        Resolves *query* to a single node id. Returns ``(node_id, score, node_data)``
        or ``None``.

        Resolution order, most authoritative first:

        0. **Exact node id.** If the string is already a node in the graph, use it
           verbatim -- no searching at all.
        1. **Exact name.** For single-token queries (no whitespace), any node whose
           normalized label -- or whose label with a source extension stripped --
           equals the normalized query. Ties are broken by ``_preference`` first
           (symbol over file, has-out-edges over leaf) and vector score second.
           This is what makes ``"AaoDeskView"`` resolve to the *symbol*
           ``AaoDeskView()`` rather than the file ``AaoDeskView.tsx``.
        2. **Vector search with a symbol preference.** Among hits within
           ``MATCH_MARGIN`` of the top score, prefer the one with the higher
           ``_preference``. Outside that margin the top hit always wins, so a
           genuinely much better match is never overridden.
        """
        if not query:
            return None

        # -- 0. exact node id -------------------------------------------------
        if self.graph.has_node(query):
            return query, 1.0, dict(self.graph.nodes[query])

        matches = self.vector_store.search(query, top_k=CANDIDATE_POOL)
        score_by_id = {doc["id"]: score for doc, score in matches}

        # -- 1. exact name ----------------------------------------------------
        norm_query = self._normalize_label(query)
        if norm_query and not re.search(r"\s", query.strip()):
            exact = [
                nid for nid, data in self.graph.nodes(data=True)
                if norm_query in self._label_keys(data)
            ]
            if exact:
                best = max(
                    exact,
                    key=lambda nid: (self._preference(nid), score_by_id.get(nid, 0.0)),
                )
                return best, score_by_id.get(best, 1.0), dict(self.graph.nodes[best])

        # -- 2. vector search, symbol-preferring within the margin ------------
        if not matches:
            return None

        top_doc, top_score = matches[0]
        floor = top_score * (1.0 - MATCH_MARGIN)
        near = [(doc, s) for doc, s in matches if s >= floor and self.graph.has_node(doc["id"])]
        if near:
            best_doc, best_score = max(
                near, key=lambda ds: (self._preference(ds[0]["id"]), ds[1])
            )
            return best_doc["id"], best_score, dict(self.graph.nodes[best_doc["id"]])

        if not self.graph.has_node(top_doc["id"]):
            return None
        return top_doc["id"], top_score, dict(self.graph.nodes[top_doc["id"]])

    # ------------------------------------------------------------------ #
    # Traversal
    # ------------------------------------------------------------------ #

    def _layer_of(self, node_id: str) -> str:
        """Display name of a node's layer. Display only -- never branch on it."""
        return self.graph.nodes.get(node_id, {}).get(
            "layer", get_registry().utility.name)

    def _layer_id_of(self, node_id: str) -> str:
        """
        Stable layer id of a node. A node with no recorded layer falls into the
        utility bucket, exactly as the display-name default used to.
        """
        data = self.graph.nodes.get(node_id, {})
        return layer_id_of(data) or get_registry().utility_id

    @staticmethod
    def _layer_rank(layer: str) -> int:
        """
        Rank for a layer *display name*, resolved through the registry.

        Kept for callers that only hold the rendered name; internal traversal
        uses :meth:`_layer_rank_of_node`, which keys off the stable id.
        """
        registry = get_registry()
        found = registry.by_name(layer)
        return found.order if found else registry.unranked_order

    def _layer_rank_of_node(self, node_id: str) -> int:
        registry = get_registry()
        return registry.order(self._layer_id_of(node_id))

    def _edge_cost(self, src: str, tgt: str, data: Dict[str, Any]) -> float:
        """Bridges are free, layer changes are cheap, same-layer hops are dear."""
        if data.get("relation") in BRIDGE_RELATIONS:
            return BRIDGE_EDGE_COST
        if self._layer_id_of(src) != self._layer_id_of(tgt):
            return CROSS_LAYER_EDGE_COST
        return SAME_LAYER_EDGE_COST

    def _bridge_aware_walk(self, start_node_id: str, max_steps: int) -> List[Dict[str, Any]]:
        """
        Cheapest-cost forward expansion from *start_node_id*, collecting at most
        *max_steps* nodes, then re-ordered Layer 1 -> Layer 6 so the rendered table
        reads top-down as an execution flow.
        """
        counter = itertools.count()
        heap: List[Tuple[float, int, str]] = [(0.0, next(counter), start_node_id)]
        best_cost: Dict[str, float] = {start_node_id: 0.0}
        arrival: Dict[str, Dict[str, Any]] = {}
        collected: List[Tuple[str, float, int]] = []
        settled: Set[str] = set()

        while heap and len(collected) < max_steps:
            cost, seq, node_id = heapq.heappop(heap)
            if node_id in settled:
                continue
            settled.add(node_id)
            collected.append((node_id, cost, seq))

            if not self.graph.has_node(node_id):
                continue
            for _, neighbor, data in self.graph.out_edges(node_id, data=True):
                if neighbor in settled:
                    continue
                new_cost = cost + self._edge_cost(node_id, neighbor, data)
                if neighbor not in best_cost or new_cost < best_cost[neighbor]:
                    best_cost[neighbor] = new_cost
                    relation = data.get("relation", "calls")
                    arrival[neighbor] = {
                        "via_relation": relation,
                        "via_bridge": relation in BRIDGE_RELATIONS,
                        "from": node_id,
                    }
                    heapq.heappush(heap, (new_cost, next(counter), neighbor))

        collected.sort(key=lambda item: (self._layer_rank_of_node(item[0]), item[1], item[2]))

        steps: List[Dict[str, Any]] = []
        for node_id, cost, _seq in collected:
            step = self._format_node_step(node_id)
            step["cost"] = round(float(cost), 3)
            meta = arrival.get(node_id)
            if meta:
                step["via_relation"] = meta["via_relation"]
                step["via_bridge"] = bool(meta["via_bridge"])
                step["from"] = meta["from"]
            else:
                step["via_relation"] = None
                step["via_bridge"] = False
                step["from"] = None
            steps.append(step)
        return steps

    @staticmethod
    def _layers_of_steps(steps: List[Dict[str, Any]]) -> List[str]:
        seen: List[str] = []
        for s in steps:
            layer = s.get("layer")
            if layer and layer not in seen:
                seen.append(layer)
        return seen

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def trace_path(
        self,
        source_query: str,
        target_query: str = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> Dict[str, Any]:
        """
        Traces an execution path across layers starting from *source_query*.

        *source_query* / *target_query* accept either free text (what the CLI
        passes) or an exact node id. When *target_query* is given but no path
        exists, the result reports that explicitly (``target_reached`` is False and
        ``requested_target`` names the node that could not be reached) and falls
        back to a downstream trace instead of silently pretending it succeeded.
        """
        resolved_source = self._resolve_node_id(source_query)
        if not resolved_source:
            return {"error": f"No matching node found for '{source_query}'"}
        start_node_id, source_score, source_data = resolved_source

        unreachable: Optional[Dict[str, Any]] = None

        if target_query:
            resolved_target = self._resolve_node_id(target_query)
            if not resolved_target:
                return {"error": f"No matching target node found for '{target_query}'"}
            target_node_id, target_score, _ = resolved_target

            try:
                path = nx.shortest_path(self.graph, start_node_id, target_node_id)
            except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
                unreachable = {
                    "requested_target": target_node_id,
                    "target_reached": False,
                    "note": (
                        f"No directed path from '{start_node_id}' to '{target_node_id}' "
                        f"({type(exc).__name__}). Showing a downstream trace from the "
                        f"source instead."
                    ),
                }
            else:
                # Keep true path order here: for an explicit A -> B query the hop
                # sequence *is* the flow, so re-sorting it by layer would lie.
                flow_steps = [self._format_node_step(nid) for nid in path]
                return {
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
            # NB: `target` is deliberately omitted so `res.get("target", "downstream")`
            # in cli.py degrades to "downstream" rather than claiming a target it
            # never reached. The requested target is reported under its own key.
            result.update(unreachable)
        return result

    def query_flow(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Hybrid semantic search + downstream flow expansion.

        Each match is traced from the **id of the document that matched**, not by
        re-searching its label -- so the reported root and the traced root are
        always the same node.
        """
        matches = self.vector_store.search(query_text, top_k=top_k)
        results = []

        for doc, score in matches:
            node_id = doc["id"]
            if self.graph.has_node(node_id):
                trace = self.trace_path(node_id)
            else:
                trace = {"steps": []}
            steps = trace.get("steps", [])
            results.append({
                "match_score": round(score, 3),
                "root_node": doc["label"],
                "root_id": node_id,
                "layer": doc.get("layer"),
                "file": doc.get("file"),
                "flow": steps,
                "layers": self._layers_of_steps(steps),
            })

        return results

    def _format_node_step(self, node_id: str) -> Dict[str, Any]:
        node_data = self.graph.nodes.get(node_id, {})
        fields = node_data.get("fields", [])
        return {
            "id": node_id,
            "label": node_data.get("label", node_id),
            "layer": node_data.get("layer", "Unknown"),
            "file": node_data.get("file", ""),
            "intent": node_data.get("intent") or node_data.get("summary", ""),
            "fields": fields
        }

    def export_flows_yaml(self, flows: List[Dict[str, Any]], filename: str = ".codechakra/flows.yaml") -> str:
        out_path = os.path.join(self.root_dir, filename)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump({"flows": flows}, f, default_flow_style=False, sort_keys=False)
        return out_path

    @staticmethod
    def render_markdown_table(steps: List[Dict[str, Any]]) -> str:
        headers = ["Layer", "Component / Symbol", "Intent & Action", "Fields / Payload", "File Location"]
        rows = [
            [
                s.get("layer", ""),
                s.get("label", s.get("id", "")),
                s.get("intent", ""),
                ", ".join(s.get("fields", [])) if s.get("fields") else "-",
                s.get("file", "")
            ]
            for s in steps
        ]
        return tabulate(rows, headers=headers, tablefmt="github")
