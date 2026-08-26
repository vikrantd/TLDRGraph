"""
Builds the BPMN payload the Workflow Explorer renders.

Takes the deterministic control flow from :mod:`tldrgraph.bpmn_extract`, chains
one workflow's steps into a single process, resolves each activity to the graph
node it calls, sorts every element into a swimlane, and layers the business
phrasing over the top. The result is a diagram a non-engineer can read: named
activities, explicit decisions, and a visible boundary between what the tool
does by itself and what a person or an outside system does.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from ..bpmn_extract import extract_process
from .bpmn_phrasing import (
    branch_labels,
    load_project_phrases,
    phrase_for_element,
    phrase_for_node,
    phrase_for_step,
)

# Swimlanes, top to bottom. "You" holds anything a person does or asks for,
# "External systems" holds work that leaves the tool, and everything else is
# TLDRGraph acting on its own.
LANE_USER = "user"
LANE_SYSTEM = "system"
LANE_EXTERNAL = "external"

LANES = [
    {"id": LANE_USER, "name": "You", "note": "What a person asks for or answers"},
    {"id": LANE_SYSTEM, "name": "TLDRGraph", "note": "What the tool does automatically"},
    {"id": LANE_EXTERNAL, "name": "External systems", "note": "Files, database, coding agent, network"},
]

# Entry points a person triggers directly.
USER_FILES = ("tldrgraph/cli.py",)
USER_CALLS = {"prompt", "confirm", "input", "echo", "secho"}


def _humanize(text: str) -> str:
    """Turns an identifier into a plain-language phrase as a last resort."""
    cleaned = re.sub(r"\(.*?\)", "", str(text or "")).strip()
    cleaned = cleaned.split(".")[-1].lstrip("_")
    cleaned = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).replace("_", " ")
    words = [w for w in cleaned.split() if w]
    if not words:
        return "Do the work"
    words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


# Method names common to dictionaries, lists and strings. Matching these against
# a project symbol of the same name mislabels an activity, so they never resolve.
AMBIGUOUS_CALLS = {
    "get", "set", "add", "append", "extend", "items", "keys", "values", "update",
    "pop", "join", "split", "strip", "format", "sort", "sorted", "copy", "close",
    "read", "write", "next", "len", "str", "int", "float", "list", "dict", "print",
}


def _resolve_call(
    call_names: List[str],
    file_hint: str,
    nodes_by_id: Dict[str, Dict[str, Any]],
    label_index: Dict[str, List[str]],
) -> Optional[str]:
    """Finds the graph node an activity's call refers to, nearest file first."""
    for name in call_names:
        if name in AMBIGUOUS_CALLS:
            continue
        candidates = label_index.get(name.lower())
        if not candidates:
            continue
        same_file = [c for c in candidates if nodes_by_id[c].get("file") == file_hint]
        pick = same_file or candidates
        return pick[0]
    return None


def _build_label_index(nodes_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for nid, node in nodes_by_id.items():
        raw = (node.get("label") or "").replace("()", "").strip()
        if not raw:
            continue
        for key in {raw.lower(), raw.split(".")[-1].lower()}:
            index.setdefault(key, []).append(nid)
    return index


def _lane_for(element: Dict[str, Any], step_file: str, is_entry_step: bool) -> str:
    if element.get("external"):
        return LANE_EXTERNAL
    calls = element.get("calls") or []
    if any(c in USER_CALLS for c in calls):
        return LANE_USER
    if is_entry_step and element["kind"] == "start" and step_file in USER_FILES:
        return LANE_USER
    return LANE_SYSTEM


def _opaque_step(step: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """Fallback when a step's body cannot be parsed: one honest black-box task."""
    return {
        "symbol": step["symbol"],
        "file": step["file"],
        "line": step.get("code_start") or 0,
        "elements": [{
            "id": f"{prefix}__opaque",
            "kind": "task",
            "label": step["symbol"],
            "detail": "",
            "line": step.get("code_start") or 0,
            "calls": [],
            "node_id": step["node_id"],
            "external": None,
        }],
        "flows": [],
    }


def _entry_targets(process: Dict[str, Any]) -> List[str]:
    """The elements a step begins with, once its own start event is removed."""
    starts = {e["id"] for e in process["elements"] if e["kind"] == "start"}
    if not starts:
        first = process["elements"][0]["id"] if process["elements"] else None
        return [first] if first else []
    targets = [f["target"] for f in process["flows"] if f["source"] in starts]
    return targets or []


def _terminal_ids(process: Dict[str, Any], dropped: Set[str]) -> List[str]:
    """Elements with nothing after them - where this step hands over."""
    live = [e for e in process["elements"] if e["id"] not in dropped]
    outgoing = {f["source"] for f in process["flows"] if f["kind"] != "loop_back"}
    ends = [e["id"] for e in live if e["id"] not in outgoing]
    if ends:
        return ends
    return [e["id"] for e in live if e["kind"] in ("end", "error")] or ([live[-1]["id"]] if live else [])


def build_workflow_process(
    root_dir: str,
    workflow: Dict[str, Any],
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Chains a workflow's steps into one lane-aware BPMN process."""
    label_index = _build_label_index(nodes_by_id)
    steps = workflow.get("steps") or []

    elements: List[Dict[str, Any]] = []
    flows: List[Dict[str, Any]] = []
    prev_ends: List[str] = []

    start_id = f"{workflow['id']}__begin"
    entry_step = steps[0] if steps else None
    elements.append({
        "id": start_id,
        "kind": "start",
        "label": phrase_for_step(workflow["id"], 0, "start") or "Someone starts this workflow",
        "detail": (entry_step or {}).get("file", ""),
        "lane": LANE_USER,
        "step": 0,
        "line": 0,
        "node_id": None,
        "external": None,
        "minor": False,
    })
    prev_ends = [start_id]

    for index, step in enumerate(steps):
        prefix = f"{workflow['id']}__s{index + 1}"
        process = extract_process(root_dir, step["file"], step["symbol"], prefix)
        if not process or not process.get("elements"):
            process = _opaque_step(step, prefix)

        starts = {e["id"] for e in process["elements"] if e["kind"] == "start"}
        entries = _entry_targets(process)

        step_title = phrase_for_step(workflow["id"], index + 1, "title") or _humanize(step["symbol"])
        for raw in process["elements"]:
            if raw["id"] in starts:
                continue

            node_id = raw.get("node_id") or _resolve_call(
                raw.get("calls") or [], step["file"], nodes_by_id, label_index
            )
            lane = _lane_for(raw, step["file"], index == 0)

            # An intermediate step's exits are handovers, not the end of the story.
            kind = raw["kind"]
            if kind == "end" and index < len(steps) - 1:
                kind = "handoff"

            phrased = phrase_for_element(workflow["id"], index + 1, dict(raw, file=step["file"]))
            if not phrased and node_id:
                phrased = phrase_for_node(node_id)
            if not phrased and node_id in nodes_by_id:
                phrased = _humanize(nodes_by_id[node_id].get("label") or "")
            if not phrased:
                phrased = _humanize_element(raw)

            elements.append({
                "id": raw["id"],
                "kind": kind,
                "label": phrased,
                "detail": raw.get("detail") or raw.get("label") or "",
                "lane": lane,
                "step": index + 1,
                "step_title": step_title,
                "line": raw.get("line") or 0,
                "file": step["file"],
                "node_id": node_id,
                "external": raw.get("external"),
                # Local bookkeeping: kept so nothing is hidden, drawn quietly.
                "minor": raw["kind"] == "task" and not node_id and not raw.get("external"),
            })

        # A decision's exits read better as outcomes than as Yes/No, when
        # someone has written what each outcome means.
        gateway_words = {}
        for raw in process["elements"]:
            if raw["kind"] != "gateway":
                continue
            yes, no = branch_labels(dict(raw, file=step["file"]))
            if yes or no:
                gateway_words[raw["id"]] = (yes, no)

        for flow in process["flows"]:
            if flow["source"] in starts:
                continue
            words = gateway_words.get(flow["source"])
            if words:
                if flow.get("label") == "Yes" and words[0]:
                    flow = dict(flow, label=words[0])
                elif flow.get("label") == "No" and words[1]:
                    flow = dict(flow, label=words[1])
            flows.append(dict(flow))

        for end_id in prev_ends:
            for entry in entries:
                flows.append({
                    "source": end_id,
                    "target": entry,
                    "label": f"Step {index + 1}",
                    "kind": "step",
                })

        prev_ends = _terminal_ids(process, starts)

    finish_id = f"{workflow['id']}__finish"
    elements.append({
        "id": finish_id,
        "kind": "end",
        "label": phrase_for_step(workflow["id"], 0, "finish") or "Workflow complete",
        "detail": "",
        "lane": LANE_SYSTEM,
        "step": len(steps) + 1,
        "line": 0,
        "node_id": None,
        "external": None,
        "minor": False,
    })
    for end_id in prev_ends:
        flows.append({"source": end_id, "target": finish_id, "label": "", "kind": "sequence"})

    used_lanes = {e["lane"] for e in elements}
    lanes = [dict(l) for l in LANES if l["id"] in used_lanes]

    return {
        "lanes": lanes,
        "elements": elements,
        "flows": _dedupe_flows(flows, {e["id"] for e in elements}),
    }


# Shapes of condition that carry the same meaning wherever they appear. Turning
# them into questions costs nothing and covers the decisions nobody has phrased
# by hand yet.
CONDITION_PATTERNS: List[Tuple[str, str]] = [
    # JavaScript and TypeScript idioms.
    (r"^!(.+?)\.length$", "Is {0} empty?"),
    (r"^(.+?)\.length\s*(?:===|==)\s*0$", "Is {0} empty?"),
    (r"^(.+?)\.length\s*>\s*0$", "Are there any {0}?"),
    (r"^!(.+)$", "Is there no {0}?"),
    (r"^(.+?)\s*(?:===|==)\s*(?:null|undefined)$", "Is {0} missing?"),
    (r"^(.+?)\s*!==\s*(?:null|undefined)$", "Do we have {0}?"),
    (r"^(.+?)\s*===\s*(.+)$", "Is {0} exactly {1}?"),
    (r"^(.+?)\s*!==\s*(.+)$", "Is {0} anything other than {1}?"),
    # Python idioms.
    (r"^not\s+(.+?)\.exists\(\)$", "Is {0} missing?"),
    (r"^not\s+(.+?)\.is_file\(\)$", "Is {0} missing?"),
    (r"^(.+?)\.exists\(\)$", "Does {0} already exist?"),
    (r"^(.+?)\.is_file\(\)$", "Is {0} a file we can read?"),
    (r"^(.+?)\.is_dir\(\)$", "Is {0} a folder?"),
    (r"^not\s+(.+)$", "Is there no {0}?"),
    (r"^len\((.+?)\)\s*>\s*0$", "Are there any {0}?"),
    (r"^len\((.+?)\)\s*==\s*0$", "Is {0} empty?"),
    (r"^(.+?)\s+in\s+(.+)$", "Is {0} part of {1}?"),
    (r"^(.+?)\s+not in\s+(.+)$", "Is {0} absent from {1}?"),
    (r"^(.+?)\s+is\s+None$", "Is {0} missing?"),
    (r"^(.+?)\s+is not\s+None$", "Do we have {0}?"),
    (r"^(.+?)\s*>=\s*(.+)$", "Is {0} at least {1}?"),
    (r"^(.+?)\s*<=\s*(.+)$", "Is {0} at most {1}?"),
    (r"^(.+?)\s*>\s*(.+)$", "Is {0} more than {1}?"),
    (r"^(.+?)\s*<\s*(.+)$", "Is {0} less than {1}?"),
    (r"^(.+?)\s*==\s*(.+)$", "Is {0} exactly {1}?"),
    (r"^(.+?)\s*!=\s*(.+)$", "Is {0} anything other than {1}?"),
]


def _readable_operand(text: str) -> str:
    """Trims an expression down to the thing a reader would name."""
    cleaned = str(text or "").strip()
    # Only unwrap parentheses that wrap the whole thing: stripping blindly would
    # break "in_degree(node_id)" into something that reads like a typo.
    while cleaned.startswith("(") and cleaned.endswith(")") and cleaned.count("(") == cleaned.count(")"):
        cleaned = cleaned[1:-1].strip()
    cleaned = re.sub(r"^[a-z_]+\.", "", cleaned)                # drop the receiver
    cleaned = re.sub(r"\([^()]*\)", "", cleaned)                # a call reads better by name
    cleaned = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", cleaned)    # camelCase reads as words
    cleaned = cleaned.replace("_", " ").replace("'", "").replace('"', "")

    words = []
    for word in cleaned.split():
        # Lower a capitalised word, but leave an acronym like URL alone.
        words.append(word.lower() if word[:1].isupper() and word[1:].islower() else word)
    return " ".join(words)


def _unwrap(text: str) -> str:
    """Drops parentheses wrapping the whole condition, and only those."""
    while text.startswith("(") and text.endswith(")") and text.count("(") == text.count(")"):
        text = text[1:-1].strip()
    return text


def _question_from(condition: str) -> str:
    text = _unwrap(" ".join(str(condition or "").split()))
    if not text:
        return "Which way?"
    for pattern, template in CONDITION_PATTERNS:
        match = re.match(pattern, text)
        if match:
            parts = [_readable_operand(g) for g in match.groups()]
            return template.format(*parts)
    return f"Is it true that {_readable_operand(text)}?"


def _humanize_element(raw: Dict[str, Any]) -> str:
    """Plain-language fallback for an element with no authored phrasing."""
    kind = raw["kind"]
    detail = raw.get("detail") or raw.get("label") or ""
    if kind == "gateway":
        return _question_from(detail)
    if kind == "loop":
        return f"For each {_readable_operand(detail)}" if detail else "Repeat for each item"
    if kind == "error":
        return f"If {_readable_operand(detail)} goes wrong" if detail else "If something fails"
    if kind == "end":
        return "Finish and hand back the result"
    calls = raw.get("calls") or []
    return _humanize(calls[0]) if calls else _humanize(detail)


def _dedupe_flows(flows: List[Dict[str, Any]], valid: Set[str]) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str, str]] = set()
    out: List[Dict[str, Any]] = []
    for flow in flows:
        if flow["source"] not in valid or flow["target"] not in valid:
            continue
        key = (flow["source"], flow["target"], flow.get("label", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(flow)
    return out


def attach_bpmn_processes(
    root_dir: str,
    workflows: List[Dict[str, Any]],
    graph: nx.DiGraph,
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> None:
    """Adds a ``process`` block to every workflow, in place."""
    load_project_phrases(root_dir)
    for workflow in workflows:
        workflow["process"] = build_workflow_process(root_dir, workflow, graph, nodes_by_id)
