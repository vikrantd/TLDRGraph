"""
Owner-qualified *display* labels.

The problem this solves is not duplication, it is collision. Measured on the
AIParas graph, ``.constructor()`` appears 32 times -- 32 genuinely distinct
symbols in 32 different files. They are not copies of each other; graphify simply
names a method without its owning class, so every renderer shows 32 identical
rows and the user reads that as "duplicate methods".

What must NOT change
--------------------
``label`` stays exactly as graphify emitted it. It is what the vector store
indexes (:meth:`LocalVectorStore._doc_text` reads ``doc["label"]``) and what
bridge resolution matches an agent's ``calls`` target against -- an agent writes
``CasesService`` or ``clearCsrfToken``, i.e. the bare identifier. Qualifying the
label in place would silently change every TF-IDF score in the corpus.

So the qualified form is a *separate* attribute, ``display_label``, written
alongside. Nothing here is ever indexed or matched against; it exists purely so a
human (and the Phase 4 visualizer) can tell two ``.constructor()`` rows apart.

Ownership comes from the AST itself
-----------------------------------
graphify already emits the containment edge -- ``CasesController --method-->
.constructor()``. There is no heuristic here: the owner is read off the graph,
and a node with no owner edge keeps its bare label unless that label is
ambiguous, in which case the file basename disambiguates it.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, Iterable, Mapping, Optional

__all__ = [
    "OWNER_RELATIONS",
    "qualify",
    "build_display_labels",
]

#: AST relations that mean "the source declares the target". graphify uses
#: ``method`` for class members and ``contains`` for lexical nesting.
OWNER_RELATIONS = frozenset({"method", "contains", "defines"})


#: How many directory levels the disambiguation pass may walk up before giving
#: up. Two files with the same name at the same depth in unrelated trees are
#: separated well before this.
_MAX_PATH_DEPTH = 6


def _basename(file_path: Any) -> str:
    return os.path.basename(str(file_path or "").replace(os.sep, "/"))


def _path_suffix(file_path: Any, depth: int) -> str:
    """The last *depth* path segments: ``1`` is the basename, ``2`` adds its dir."""
    parts = [p for p in str(file_path or "").replace(os.sep, "/").split("/") if p]
    if not parts:
        return ""
    return "/".join(parts[-depth:])


def qualify(label: str, owner_label: str) -> str:
    """
    ``(".constructor()", "AuditService")`` -> ``"AuditService.constructor()"``.

    A leading dot in the label already *is* the member separator, so it is
    reused rather than doubled.
    """
    label = str(label or "")
    owner_label = str(owner_label or "").strip()
    if not owner_label:
        return label
    if not label:
        return owner_label
    if label.startswith("."):
        return f"{owner_label}{label}"
    return f"{owner_label}.{label}"


def _owner_is_useful(owner: Mapping[str, Any], node: Mapping[str, Any]) -> bool:
    """
    Would qualifying with this owner actually say something?

    A file-level node owns most top-level symbols, and ``page.tsx.emptyForm``
    reads worse than ``emptyForm`` plus a basename suffix, so file-ish owners are
    rejected here and the basename pass below handles those nodes instead.
    """
    owner_label = str(owner.get("label") or "").strip()
    if not owner_label:
        return False
    basename = _basename(owner.get("file"))
    if owner_label == basename or owner_label == os.path.splitext(basename)[0]:
        return False
    # A label carrying an extension is a filename however it was recorded.
    return "." not in os.path.splitext(owner_label)[0] or not os.path.splitext(owner_label)[1]


def build_display_labels(nodes: Iterable[Mapping[str, Any]],
                         edges: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """
    ``{node_id: display_label}`` for every node handed in.

    Two passes:

    1. qualify with the declaring node's label, where the AST declares one;
    2. for anything whose result is *still* not unique across the graph, append
       the file basename.

    Both passes are order-independent: ties are broken by sorting, so the same
    graph always produces the same labels.
    """
    node_by_id: Dict[str, Mapping[str, Any]] = {}
    order: list = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in node_by_id:
            continue
        node_by_id[node_id] = node
        order.append(node_id)

    owner_of: Dict[str, str] = {}
    for edge in edges:
        if str(edge.get("relation") or "") not in OWNER_RELATIONS:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target or source == target:
            continue
        if target not in node_by_id or source not in node_by_id:
            continue
        # Deterministic when the AST somehow declares a node twice.
        existing = owner_of.get(target)
        if existing is None or source < existing:
            owner_of[target] = source

    display: Dict[str, str] = {}
    for node_id in order:
        node = node_by_id[node_id]
        label = str(node.get("label") or node_id)
        owner_id = owner_of.get(node_id)
        owner = node_by_id.get(owner_id) if owner_id else None
        if owner is not None and _owner_is_useful(owner, node):
            display[node_id] = qualify(label, str(owner.get("label") or ""))
        else:
            display[node_id] = label

    # Pass 2: whatever is still ambiguous gets the shortest path suffix that
    # separates it. One directory level at a time, so the common case stays
    # "readString() (extraction.strategy.ts)" and only genuinely same-named files
    # in different directories pay for the longer form.
    qualified = dict(display)
    for depth in range(1, _MAX_PATH_DEPTH + 1):
        collisions = Counter(display.values())
        ambiguous = [nid for nid in order if collisions[display[nid]] > 1]
        if not ambiguous:
            break
        for node_id in ambiguous:
            suffix = _path_suffix(node_by_id[node_id].get("file"), depth)
            if suffix:
                display[node_id] = f"{qualified[node_id]} ({suffix})"

    return display


def display_label_of(node: Mapping[str, Any]) -> str:
    """The qualified label if one was computed, else the bare label."""
    return str(node.get("display_label") or node.get("label") or node.get("id") or "")
