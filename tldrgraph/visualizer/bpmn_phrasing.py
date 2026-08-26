"""
Business phrasing for the BPMN workflow view.

The control-flow extractor is deterministic but speaks in code: a gateway comes
out as ``graph.in_degree(node_id) > 0``. This module holds the human sentence
for those shapes - "Does anything else in the code call this symbol?" - so the
diagram reads as a business process rather than a listing.

Phrases are written by an agent that has read the source (see the enrichment
contract) and are keyed by where the shape lives, not by its generated id, so
the file survives renumbering. Each entry records the code it was written for;
when that code changes the phrase is dropped rather than shown against logic it
no longer describes, and the deterministic label takes over.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .bpmn_phrasebook import ELEMENT_PHRASES, NODE_PHRASES, STEP_PHRASES

# The project's own phrases, read from .tldrgraph/bpmn_phrases.yaml when a
# workflow payload is built. They win over the phrases shipped with the package,
# so a repo can describe its own processes in its own words.
_PROJECT_PHRASES: Dict[str, Dict[str, str]] = {}


def load_project_phrases(root_dir: str) -> int:
    """Loads a repository's authored phrases. Returns how many were found."""
    from ..bpmn_enrichment import load_store

    _PROJECT_PHRASES.clear()
    _PROJECT_PHRASES.update(load_store(root_dir))
    return len(_PROJECT_PHRASES)


def _entry_for(key: str) -> Optional[Dict[str, str]]:
    return _PROJECT_PHRASES.get(key) or ELEMENT_PHRASES.get(key)


def _element_key(element: Dict[str, Any], file_path: str = "") -> str:
    path = file_path or element.get("file") or ""
    return f"{path}:{element.get('line', 0)}:{element.get('kind', '')}"


def phrase_for_step(workflow_id: str, step_number: int, key: str) -> Optional[str]:
    """The authored phrase for a step title or a workflow's start/finish event."""
    return STEP_PHRASES.get((workflow_id, step_number, key))


def phrase_for_node(node_id: str) -> Optional[str]:
    """The authored business name for a graph symbol."""
    return NODE_PHRASES.get(node_id)


def phrase_for_element(workflow_id: str, step_number: int, element: Dict[str, Any]) -> Optional[str]:
    """The authored phrase for one shape, or ``None`` when it has drifted."""
    entry = _entry_for(_element_key(element))
    if not entry:
        return None
    guard = entry.get("when")
    if guard and guard.strip() != (element.get("detail") or "").strip():
        return None                                   # the code moved on
    return entry.get("say")


def branch_labels(element: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Authored labels for a gateway's two exits, when they were written."""
    entry = _entry_for(_element_key(element)) or {}
    return entry.get("yes"), entry.get("no")
