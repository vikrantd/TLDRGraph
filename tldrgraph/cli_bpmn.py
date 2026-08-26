"""
Command handlers for the workflow (BPMN) enrichment cycle.

The AST pass runs first and is always the source of truth for structure; these
commands only deal with the words layered on top of it.
"""

from __future__ import annotations

import click

from .bpmn_enrichment import apply_response, collect_candidates, load_store, write_request
from .visualizer.data import prepare_visualizer_data


def run_bpmn_enrich(path: str, limit: int) -> None:
    """Builds the workflows, then queues every shape still lacking a phrase."""
    click.echo("Extracting workflow control flow...")
    data = prepare_visualizer_data(path)
    workflows = data.get("workflows") or []

    known = load_store(path)
    candidates = collect_candidates(workflows, known)
    if not candidates:
        click.echo(f"Every workflow shape across {len(workflows)} workflows already reads in plain language.")
        return

    request, queued = write_request(path, candidates, limit or len(candidates))
    stale = sum(1 for c in candidates if c["stale"])

    click.echo(f"\nWorkflows:        {len(workflows)}")
    click.echo(f"Unphrased shapes: {len(candidates)}" + (f" ({stale} whose code changed)" if stale else ""))
    click.echo(f"Queued now:       {queued}")
    click.echo(f"\nRequest written to {request}")
    click.echo("The agent should read the source at each 'at' location, write "
               ".tldrgraph/bpmn_response.yaml, then run: tldrgraph apply-bpmn")


def run_apply_bpmn(path: str) -> None:
    """Merges the agent's answers into the project's phrase store."""
    result = apply_response(path)
    if result.get("error"):
        click.echo(f"Could not read the response: {result['error']}")
        click.echo("Expected a YAML list of {key, say, when, yes, no} at .tldrgraph/bpmn_response.yaml")
        return

    click.echo(f"Applied {result['applied']} phrases to {result['store']}")
    if result.get("skipped"):
        click.echo(f"Skipped {result['skipped']} entries that were missing a key or a phrase.")
        for item in result.get("rejected") or []:
            click.echo(f"  - {item}")
    click.echo("Rebuild the visualizer to see them: tldrgraph ui")
