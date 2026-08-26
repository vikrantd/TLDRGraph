"""
The enrichment cycle that gives a workflow its business language.

The AST pass runs first and produces the true shape of a process. It cannot name
that shape in business terms, so this module asks an agent to: it writes a
request listing every shape still speaking in code, the agent answers with plain
sentences, and the answers are stored against the code they describe.

The store lives in the project (``.tldrgraph/bpmn_phrases.yaml``), so a repo's
own phrasing travels with the repo rather than with this package.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

REQUEST_FILE = "bpmn_request.yaml"
RESPONSE_FILE = "bpmn_response.yaml"
STORE_FILE = "bpmn_phrases.yaml"

SCHEMA = "tldrgraph/bpmn-request@1"

INSTRUCTIONS = [
    "Open and READ the source around each 'at' location before writing anything.",
    "Write for someone who does not read code: a business reader, not an engineer.",
    "'say' for a decision is a QUESTION ending in '?' - 'Is the balance sufficient?'",
    "'yes' and 'no' name the OUTCOMES of that decision, not the words yes and no:"
    " 'Balance covers it' / 'Not enough funds'.",
    "'say' for a loop starts 'For each ...'; for an error path it starts 'If ...'.",
    "'say' for an activity is a short verb phrase describing the business effect:"
    " 'Charge the customer', never 'call chargeCustomer()'.",
    "Never mention function, file or variable names in 'say'.",
    "Copy 'when' back exactly as given - it records the code the phrase describes,"
    " and a phrase whose code has changed is dropped rather than shown wrongly.",
    "Copy each 'key' verbatim.",
    f"Write a YAML list of {{key, say, when, yes, no}} to .tldrgraph/{RESPONSE_FILE}",
    "Then run: tldrgraph apply-bpmn",
]


def _tldrgraph_dir(root_dir: str) -> str:
    return os.path.join(os.path.abspath(root_dir), ".tldrgraph")


def store_path(root_dir: str) -> str:
    return os.path.join(_tldrgraph_dir(root_dir), STORE_FILE)


def request_path(root_dir: str) -> str:
    return os.path.join(_tldrgraph_dir(root_dir), REQUEST_FILE)


def response_path(root_dir: str) -> str:
    return os.path.join(_tldrgraph_dir(root_dir), RESPONSE_FILE)


def load_store(root_dir: str) -> Dict[str, Dict[str, str]]:
    """Reads the project's phrase store, tolerating a missing or broken file."""
    path = store_path(root_dir)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}

    phrases = data.get("phrases") if isinstance(data, dict) else None
    if not isinstance(phrases, dict):
        return {}
    return {str(k): v for k, v in phrases.items() if isinstance(v, dict)}


def save_store(root_dir: str, phrases: Dict[str, Dict[str, str]]) -> str:
    path = store_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "schema": "tldrgraph/bpmn-phrases@1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phrases": {key: phrases[key] for key in sorted(phrases)},
    }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True, width=100)
    return path


def _shape_key(element: Dict[str, Any]) -> str:
    return f"{element.get('file')}:{element.get('line', 0)}:{element.get('kind', '')}"


def _needs_phrasing(element: Dict[str, Any]) -> bool:
    """A shape needs words when it is still describing itself in code."""
    if element.get("minor"):
        return False
    if element.get("kind") in ("gateway", "loop", "error"):
        return True
    # An activity that resolved to a named symbol already reads well enough.
    return element.get("kind") == "task" and not element.get("node_id")


def collect_candidates(
    workflows: List[Dict[str, Any]],
    known: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Every shape without a current phrase, most decision-heavy flow first."""
    candidates: Dict[str, Dict[str, Any]] = {}

    for workflow in workflows:
        process = workflow.get("process") or {}
        for element in process.get("elements") or []:
            if not _needs_phrasing(element):
                continue
            key = _shape_key(element)
            detail = (element.get("detail") or "").strip()
            entry = known.get(key)
            if entry and entry.get("when", "").strip() == detail:
                continue                      # already phrased, and still accurate
            if key in candidates:
                candidates[key]["seen_in"].append(workflow["id"])
                continue
            candidates[key] = {
                "key": key,
                "kind": element.get("kind"),
                "at": f"{element.get('file')}:{element.get('line')}",
                "when": detail,
                "reads_now": element.get("label"),
                "step": element.get("step_title") or "",
                "seen_in": [workflow["id"]],
                "stale": bool(entry),
            }

    ordered = sorted(
        candidates.values(),
        key=lambda c: (-len(c["seen_in"]), c["kind"], c["key"]),
    )
    return ordered


def write_request(root_dir: str, candidates: List[Dict[str, Any]], limit: int = 120) -> Tuple[str, int]:
    """Writes the work order for the agent. Returns the path and how many queued."""
    queued = candidates[:limit]
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": f".tldrgraph/{RESPONSE_FILE}",
        "instructions": INSTRUCTIONS,
        "progress": {
            "unphrased_total": len(candidates),
            "queued_now": len(queued),
            "remaining_after": max(len(candidates) - len(queued), 0),
        },
        "shapes": [
            {
                "key": c["key"],
                "kind": c["kind"],
                "at": c["at"],
                "when": c["when"],
                "reads_now": c["reads_now"],
                "in_step": c["step"],
                "used_by": c["seen_in"],
                **({"note": "phrase exists but the code changed - rewrite it"} if c["stale"] else {}),
            }
            for c in queued
        ],
    }

    path = request_path(root_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True, width=100)
    return path, len(queued)


def apply_response(root_dir: str, response: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Folds the agent's answers into the store, refusing what it cannot trust."""
    if response is None:
        try:
            with open(response_path(root_dir), "r", encoding="utf-8") as handle:
                response = yaml.safe_load(handle) or []
        except (OSError, yaml.YAMLError) as err:
            return {"applied": 0, "skipped": 0, "error": str(err)}

    if not isinstance(response, list):
        return {"applied": 0, "skipped": 0, "error": "expected a YAML list of phrases"}

    store = load_store(root_dir)
    applied = 0
    skipped: List[str] = []

    for item in response:
        if not isinstance(item, dict):
            skipped.append("not a mapping")
            continue
        key = str(item.get("key") or "").strip()
        say = str(item.get("say") or "").strip()
        when = str(item.get("when") or "").strip()
        if not key or not say:
            skipped.append(key or "missing key")
            continue
        if len(key.rsplit(":", 2)) != 3:
            skipped.append(key)               # not a shape key
            continue

        entry = {"say": say, "when": when}
        for branch in ("yes", "no"):
            word = str(item.get(branch) or "").strip()
            if word:
                entry[branch] = word
        store[key] = entry
        applied += 1

    path = save_store(root_dir, store)
    return {"applied": applied, "skipped": len(skipped), "store": path, "rejected": skipped[:10]}
