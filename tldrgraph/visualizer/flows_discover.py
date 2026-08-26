"""
Workflow discovery for repositories TLDRGraph has never seen.

The curated blueprints describe this project's own journeys. Every other
repository - a Next.js app, a NestJS service, a Go API - needs its workflows
found rather than declared. This module reads the call graph, picks the places
work actually begins, and follows each one through the layers it touches.

What comes out has the same shape as a curated workflow, so the BPMN view, the
phrasing layer and the renderer treat both alike.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import networkx as nx

MAX_STEPS = 7
MIN_STEPS = 3

# Files where a request, a command or a page starts. Ordered by how strongly
# each one signals an entry point.
ENTRY_PATTERNS: Tuple[Tuple[str, str, int], ...] = (
    (r"(^|/)(app|pages)/.*/(route|page)\.(t|j)sx?$", "Web request", 6),
    (r"\.controller\.(t|j)s$", "API request", 6),
    (r"(^|/)(routes?|controllers?|handlers?|endpoints?)/", "API request", 5),
    (r"(^|/)(cli|cmd|commands?)/", "Command line", 5),
    (r"(^|/)(jobs?|tasks?|workers?|queues?)/", "Background job", 4),
    (r"(^|/)(services?|usecases?|application)/", "Service call", 3),
    (r"(^|/)(main|index|server|app)\.(t|j)sx?$", "Application start", 3),
    (r"(^|/)main\.py$", "Application start", 3),
)

ENTRY_SYMBOLS: Tuple[Tuple[str, str, int], ...] = (
    (r"^(get|post|put|patch|delete|head|options)$", "Web request", 6),
    (r"^handle[A-Z_]", "Web request", 5),
    (r"^(main|run|serve|start|bootstrap)$", "Application start", 4),
    (r"^on[A-Z]", "Event", 3),
)

# Anything here is scaffolding rather than a journey worth showing.
SKIP_DIRS = ("tests/", "test/", "spec/", "__tests__/", "benchmarks/", "node_modules/",
             "dist/", "build/", "vendor/", "migrations/")


def _is_candidate_file(file_path: str) -> bool:
    path = (file_path or "").replace("\\", "/").lower()
    return bool(path) and not any(part in path for part in SKIP_DIRS)


def _entry_score(node: Dict[str, Any]) -> Tuple[int, str]:
    """How strongly this symbol looks like a place work begins, and why."""
    file_path = (node.get("file") or "").replace("\\", "/")
    label = re.sub(r"\(.*\)", "", node.get("label") or "").strip()
    bare = label.split(".")[-1]

    best, reason = 0, ""
    for pattern, category, score in ENTRY_PATTERNS:
        if re.search(pattern, file_path, re.IGNORECASE) and score > best:
            best, reason = score, category
    for pattern, category, score in ENTRY_SYMBOLS:
        if re.match(pattern, bare, re.IGNORECASE) and score > best:
            best, reason = score, category
    return best, reason


def _humanize(text: str) -> str:
    cleaned = re.sub(r"\(.*?\)", "", str(text or "")).strip().split(".")[-1].lstrip("_")
    cleaned = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).replace("_", " ")
    words = [w for w in cleaned.split() if w]
    if not words:
        return "Run the process"
    words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def _module_of(file_path: str) -> str:
    base = os.path.basename(file_path or "")
    return os.path.splitext(base)[0] or "app"


def rank_entry_points(
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    limit: int,
) -> List[Tuple[str, str]]:
    """The most promising starting points, best first, as (node_id, category)."""
    scored: List[Tuple[float, str, str]] = []

    for node_id, node in nodes_by_id.items():
        if node.get("is_test") or not _is_candidate_file(node.get("file")):
            continue
        if not graph.has_node(node_id):
            continue

        signal, category = _entry_score(node)
        out_degree = graph.out_degree(node_id)
        in_degree = graph.in_degree(node_id)
        if signal == 0 and in_degree > 0:
            continue                       # something calls it, so it is not a start
        if out_degree < 2:
            continue                       # nothing downstream to show

        # Prefer a strong entry signal, then reach, then being called by nothing.
        weight = signal * 10 + min(out_degree, 12) - min(in_degree, 6)
        scored.append((weight, node_id, category or "Process"))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(node_id, category) for _, node_id, category in scored[:limit]]


def _next_step(
    graph: nx.DiGraph,
    current: str,
    visited: Set[str],
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """The most meaningful next call: a layer change beats staying put."""
    here = nodes_by_id.get(current) or {}
    best: Optional[Tuple[float, str]] = None

    for _, target, data in graph.out_edges(current, data=True):
        if target in visited or target not in nodes_by_id:
            continue
        node = nodes_by_id[target]
        if node.get("is_test") or not _is_candidate_file(node.get("file")):
            continue
        relation = data.get("relation") or "calls"
        if relation in ("contains", "rationale_for", "imports", "imports_from"):
            continue

        score = float(graph.out_degree(target))
        if node.get("layer_id") and node.get("layer_id") != here.get("layer_id"):
            score += 12                    # crossing a layer is the interesting move
        if node.get("file") != here.get("file"):
            score += 4
        if relation == "cross_layer_link":
            score += 3
        if best is None or score > best[0]:
            best = (score, target)

    return best[1] if best else None


def _walk_steps(
    graph: nx.DiGraph,
    root: str,
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Follows the call graph forward from a root into an ordered chain."""
    chain = [root]
    visited = {root}
    current = root

    while len(chain) < MAX_STEPS:
        nxt = _next_step(graph, current, visited, nodes_by_id)
        if not nxt:
            break
        chain.append(nxt)
        visited.add(nxt)
        current = nxt

    return chain


def discover_workflows(
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
    format_step: Callable[..., Dict[str, Any]],
    collect_support: Callable[..., List[Dict[str, Any]]],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """Finds the journeys in a repository nobody has described by hand."""
    workflows: List[Dict[str, Any]] = []
    claimed: Set[str] = set()

    for root, category in rank_entry_points(graph, nodes_by_id, limit * 3):
        if len(workflows) >= limit:
            break
        if root in claimed:
            continue

        chain = _walk_steps(graph, root, nodes_by_id)
        if len(chain) < MIN_STEPS:
            continue
        # Two journeys that mostly retrace each other are one journey.
        if len(set(chain) & claimed) > len(chain) // 2:
            continue
        claimed.update(chain)

        steps = []
        layers_involved: List[str] = []
        for index, node_id in enumerate(chain):
            record = format_step(node_id, index + 1)
            if index > 0:
                edge = graph.get_edge_data(chain[index - 1], node_id) or {}
                record["via_relation"] = edge.get("relation") or "calls"
                record["from_node"] = chain[index - 1]
            else:
                record["via_relation"] = "entry_point"
                record["from_node"] = None
            if record.get("layer") and record["layer"] not in layers_involved:
                layers_involved.append(record["layer"])
            steps.append(record)

        support = collect_support(steps)
        root_node = nodes_by_id[root]
        title = _humanize(root_node.get("label") or root)
        module = _module_of(root_node.get("file"))

        workflows.append({
            "id": "flow_found_" + re.sub(r"[^a-z0-9_]", "_", root.lower())[:60],
            "title": f"{title} ({module})",
            "category": category,
            "root_node": root_node.get("label") or root,
            "root_id": root,
            "file": root_node.get("file") or "",
            "layer_id": root_node.get("layer_id") or "utility",
            "layer": root_node.get("layer") or "",
            "summary": (
                f"{category} beginning at {root_node.get('label') or root} in "
                f"{root_node.get('file') or 'this repository'}, followed through "
                f"{len(steps)} steps across {len(layers_involved)} layer(s)."
            ),
            "step_count": len(steps),
            "layers_involved": layers_involved,
            "node_ids": [s["node_id"] for s in steps] + [sp["node_id"] for sp in support],
            "steps": steps,
            "support": support,
            "discovered": True,
        })

    return workflows
