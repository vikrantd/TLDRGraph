"""Persistent init authorization and embedding policy helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from .dense_embedder import EMBEDDINGS_ENV_VAR, POLICY_ON, resolve_policy

APPROVAL_FILENAME = "enrichment_approval.json"
APPROVAL_SCHEMA = "tldrgraph/enrichment-approval@1"


def resolve_default_on_embeddings(requested: Optional[str]) -> str:
    """Enable downloads unless a CLI argument or environment override says otherwise."""
    if requested is None and not os.environ.get(EMBEDDINGS_ENV_VAR, "").strip():
        return POLICY_ON
    return resolve_policy(requested)


def resolve_init_embeddings(requested: Optional[str]) -> str:
    """Init downloads/builds embeddings unless explicitly overridden."""
    return resolve_default_on_embeddings(requested)


def embedding_failure(loader: Any) -> Optional[str]:
    diag = loader.vector_store.diagnostics()
    required_but_missing = (
        loader.graph.number_of_nodes()
        and diag["policy"] == POLICY_ON
        and diag["backend"] != "hybrid"
    )
    if required_but_missing:
        return str(diag.get("embedder_reason") or "dense embedding backend unavailable")
    return None


def embedding_summary(loader: Any) -> str:
    backend = loader.vector_store.diagnostics()["backend"]
    if backend == "hybrid":
        return "Dense embedding index is ready."
    return "TF-IDF index is ready; dense embeddings are disabled or cached-only."


def approval_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), ".tldrgraph", APPROVAL_FILENAME)


def remember_full_enrichment_approval(root: str, candidates: List[Dict[str, Any]]) -> str:
    """Authorize exactly the current campaign's candidate IDs until exhausted."""
    path = approval_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "schema": APPROVAL_SCHEMA,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "candidate_ids": sorted(str(item["id"]) for item in candidates),
    }
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temp_path, path)
    return path


def enrichment_approval_is_active(root: str, candidates: List[Dict[str, Any]]) -> bool:
    """Accept only a non-empty candidate set contained in the approved campaign."""
    try:
        with open(approval_path(root), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    approved = payload.get("candidate_ids")
    if payload.get("schema") != APPROVAL_SCHEMA or not isinstance(approved, list):
        return False
    current_ids = {str(item["id"]) for item in candidates}
    return bool(current_ids) and current_ids.issubset({str(item) for item in approved})


def clear_enrichment_approval(root: str) -> None:
    try:
        os.remove(approval_path(root))
    except FileNotFoundError:
        pass
