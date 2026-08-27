"""
Autonomous agent enrichment loop and prompts for TLDRGraph CLI.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
import click

from . import agent_runner
from .cli_enrichment import (
    REQUEST_FILENAME,
    apply_enrichment_items,
    build_enrichment_batch,
    coerce_enrichment_items,
    compute_degrees,
    enrichment_instructions,
    needs_agent_enrichment,
    state_path,
    write_payload,
)
from .graph_loader import GraphLoader

AGENT_ENRICH_PROMPT = """You are enriching a code architecture graph for the repository you currently have open.

For each node listed below, OPEN its source file at the given path and read the
actual implementation before writing anything. An intent paraphrased from the
symbol name is worse than none: it poisons semantic search with confident noise.

{instructions}

Return ONLY a JSON array, no prose and no markdown fence, of this shape:

[
  {{
    "id": "<node id copied verbatim>",
    "intent": "What this symbol does, why it exists, and its execution logic. Markdown allowed.",
    "input_fields": ["argument", "payloadField"],
    "output_fields": ["returnedField", "emittedEvent"],
    "calls": ["DownstreamService", "src/services/calc.ts:calculate", "some_table"]
  }}
]

Include every id exactly once. If a file is unreadable or the symbol is trivial,
still return the id with a short honest intent and empty field/call lists.

Repository root: {root}

Nodes ({count}):
{nodes}
"""


def build_agent_enrichment_prompt(root: str, batch: List[Dict[str, Any]]) -> str:
    return AGENT_ENRICH_PROMPT.format(
        instructions="\n".join(f"- {line}" for line in enrichment_instructions()),
        root=root,
        count=len(batch),
        nodes=json.dumps(batch, indent=2, default=str),
    )


def _process_single_agent_batch(
    path: str,
    loader: GraphLoader,
    agent: Any,
    batch: List[Dict[str, Any]],
    model: Optional[str],
    totals: Dict[str, Any],
    errors: List[str],
) -> bool:
    root = os.path.abspath(path)
    try:
        raw = agent_runner.run_agent_json(
            agent, build_agent_enrichment_prompt(root, batch), root, model=model
        )
    except agent_runner.AgentError as err:
        totals["failed_batches"] += 1
        errors.append(str(err))
        click.echo(f"   ⚠️  {err}")
        return False

    items = coerce_enrichment_items(raw)
    if not items:
        totals["failed_batches"] += 1
        errors.append("agent returned no enrichment objects")
        click.echo("   ⚠️  Agent returned no enrichment objects; stopping the loop.")
        return False

    batch_ids = {node["id"] for node in batch}
    stats = apply_enrichment_items(loader, path, items, f"agent:{agent.name}")
    totals["batches"] += 1
    totals["applied"] += len(stats["applied_ids"])
    totals["bridges"] += stats["bridges"]
    totals["unresolved"] += len(stats["unresolved"])

    if not stats["applied_ids"]:
        errors.append("agent returned ids that are not in the graph")
        click.echo("   ⚠️  None of the returned ids matched the graph; stopping the loop.")
        return False

    cleared = sum(
        1 for nid in batch_ids
        if loader.graph.has_node(nid) and not needs_agent_enrichment(loader.graph.nodes[nid])
    )
    if not cleared:
        errors.append("agent answers left every node still un-enriched")
        click.echo("   ⚠️  That batch cleared no nodes (empty intents?); stopping the loop.")
        return False

    return True


def run_agent_enrichment(
    path: str,
    loader: GraphLoader,
    agent: Any,
    batch_size: int = 200,
    max_nodes: int = 0,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    totals = {"applied": 0, "bridges": 0, "unresolved": 0, "batches": 0, "failed_batches": 0}
    errors: List[str] = []
    processed = 0

    while True:
        degrees = compute_degrees(loader.graph)
        limit = batch_size
        if max_nodes:
            remaining_budget = max_nodes - processed
            if remaining_budget <= 0:
                break
            limit = min(batch_size, remaining_budget)

        request = build_enrichment_batch(path, loader, degrees, limit=limit, skip_cursor=True)
        batch = request["batch"]
        if not batch:
            break

        write_payload(state_path(path, REQUEST_FILENAME), request["payload"])
        click.echo(f"   🤖 Batch {totals['batches'] + 1}: {len(batch)} node(s) ({request.get('remaining_after', 0)} left after this)...")

        if not _process_single_agent_batch(path, loader, agent, batch, model, totals, errors):
            break
        processed += len(batch)

    totals["errors"] = errors
    return totals
