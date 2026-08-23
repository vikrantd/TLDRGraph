"""
CLI Entry Point for TLDRGraph: Multi-layer code flow & hybrid semantic search engine.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple
import click
import yaml

from . import agent_runner, paths, vector_store as vs_mod
from .cli_commands import (
    DEAD_CODE_STATUS_NOTES,
    DEAD_CODE_STATUSES,
    fmt_bytes,
    print_doctor_report,
    run_apply_enrichment,
    run_dead_code_report,
    run_queue_enrichment,
    serve_visualizer,
    snapshot_or_graph_nodes,
)
from .cli_enrichment import (
    AGENT_ENRICHMENT_SOURCE,
    AUDIT_LOG_FILENAME,
    CURSOR_FILENAME,
    HEURISTIC_ENRICHMENT_SOURCE,
    LEGACY_FILENAME,
    LEGACY_REQUEST_FILENAME,
    LEGACY_RESPONSE_FILENAME,
    NON_CODE_NODE_TYPES,
    REQUEST_FILENAME,
    RESPONSE_FILENAME,
    STATE_DIR,
    apply_enrichment_items,
    build_enrichment_batch,
    coerce_enrichment_items,
    compute_degrees,
    enrichment_candidates,
    enrichment_instructions,
    needs_agent_enrichment,
    read_cursor,
    read_payload,
    stamp_degrees,
    state_path,
    write_cursor,
    write_payload,
)
from .cli_pipeline import (
    APPLIED_RESPONSE_FILENAME,
    STATUS_DONE,
    STATUS_NEEDS_CONFIRMATION,
    STATUS_NEEDS_ENRICHMENT,
    STATUS_NEEDS_LAYERS,
    apply_pending_enrichment_response,
    apply_pending_layer_response,
    build_agent_enrichment_prompt,
    emit_status,
    init_pipeline,
    run_agent_enrichment,
    stdout_to_stderr_if,
)
from .flow_engine import FlowEngine
from .graph_loader import (
    BRIDGE_SCORE_FLOOR,
    GraphLoader,
    bridge_score_floor,
    resolve_call_target,
)
from .installer import ensure_gitignore, gitignore_warnings, install_agent_rules
from .layer_config import config_path
from .layers import get_registry, layer_id_of
from .propose_layers import (
    RESPONSE_FILENAME as PROPOSE_RESPONSE_FILENAME,
    apply_proposed_layers,
    auto_configure_layers,
    generate_propose_request,
)
from .visualizer import generate_visualizer_html

embeddings_option = click.option(
    "--embeddings", "embeddings",
    type=click.Choice([vs_mod.POLICY_OFF, vs_mod.POLICY_AUTO, vs_mod.POLICY_ON]),
    default=None,
    help="Retrieval backend policy. Defaults to $TLDRGRAPH_EMBEDDINGS, itself 'off'.",
)

_init_options = [
    click.argument("path", default=".", type=click.Path(exists=True)),
    click.option("--yes", "-y", "assume_yes", is_flag=True,
                 help="Proceed with enrichment without asking (agents: only after the user agrees)"),
    click.option("--batch", "batch_size", default=25, show_default=True,
                 help="Nodes handed to the agent per round"),
    click.option("--limit", "max_nodes", default=0, show_default=True,
                 help="Cap on nodes to enrich this run. 0 enriches every candidate."),
    click.option("--rebuild", is_flag=True, help="Re-extract and rebuild enrichment from scratch"),
    click.option("--relayer", is_flag=True, help="Discard the layer set and design it again"),
    click.option("--agent-cli", is_flag=True,
                 help="Shell out to an agent CLI (claude/cursor-agent/gemini) instead of "
                      "handing off. Off by default: agent CLIs differ per tool and can hang."),
    click.option("--agent-model", default=None,
                 help="Model for --agent-cli (e.g. opus, sonnet, gemini-2.5-pro). Defaults "
                      "to $TLDRGRAPH_AGENT_MODEL. Ignored on the handshake path, where your "
                      "own agent session picks the model."),
    click.option("--json", "as_json", is_flag=True, help="Emit machine-readable status"),
    embeddings_option,
]


def _with_init_options(fn):
    for option in reversed(_init_options):
        fn = option(fn)
    return fn


_init_pipeline = init_pipeline
_apply_pending_enrichment_response = apply_pending_enrichment_response
_apply_pending_layer_response = apply_pending_layer_response
_emit_status = emit_status
_snapshot_or_graph_nodes = snapshot_or_graph_nodes
_stamp_degrees = stamp_degrees
_enrichment_candidates = enrichment_candidates
_enrichment_instructions = enrichment_instructions
_state_path = state_path
_read_payload = read_payload
_write_payload = write_payload
_read_cursor = read_cursor
_write_cursor = write_cursor
_fmt_bytes = fmt_bytes
with_init_options = _with_init_options


@click.group()
def cli():
    """TLDRGraph: Token-Efficient Hybrid Code Flow & Semantic Navigation Engine (Dynamic Multi-Layer)"""
    pass


@cli.command()
@_with_init_options
def init(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """Build this repository's graph: layers, extraction, and enrichment, in one command."""
    init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, embeddings, as_json)


@cli.command()
@_with_init_options
def scan(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """Alias for `init`, kept for existing scripts and agent rules."""
    init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, embeddings, as_json)


@cli.command()
@_with_init_options
def enrich(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, as_json, embeddings):
    """Alias for `init`, which already resumes enrichment where it left off."""
    init_pipeline(path, assume_yes, batch_size, max_nodes, rebuild, relayer, agent_cli, agent_model, embeddings, as_json)


@cli.command(name="ui")
@click.option("--path", default=".", help="Repository root path")
@click.option("--serve", is_flag=True, help="Serve the repo locally so the visualizer can read source files")
@click.option("--port", default=8777, help="Port for --serve")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the visualizer in a browser (with --serve)")
def visualizer_cmd(path, serve, port, open_browser):
    """Generate and view interactive standalone HTML visualizer (.tldrgraph/TLDRGRAPH_VISUALIZER.html)."""
    html_path = generate_visualizer_html(path)
    click.echo(f"\n🌐 [TLDRGraph Visualizer]: {os.path.abspath(html_path)}")
    if not serve:
        click.echo("Open this file in any web browser to explore all architectural layers and cross-layer connections interactively!")
        click.echo("Source code is read live: use 'Connect project' in the page, or rerun with --serve to skip the prompt.\n")
        return
    serve_visualizer(path, html_path, port, open_browser)


@cli.command()
@click.argument("query_text")
@click.option("--top-k", default=3, help="Number of flow candidates to return")
@click.option("--path", default=".", help="Repository root path")
@embeddings_option
def query(query_text, top_k, path, embeddings):
    """Hybrid search + trace end-to-end multi-layer execution flows. Read-only: never enriches."""
    loader = GraphLoader(path, embeddings=embeddings)
    graph = loader.load_or_extract(enrich_llm=False)
    engine = FlowEngine(graph, loader.vector_store, root_dir=path)
    results = engine.query_flow(query_text, top_k=top_k)
    if not results:
        click.echo(f"❌ No matching flows found for: '{query_text}'")
        return
    yaml_file = engine.export_flows_yaml(results)
    click.echo(f"\n🔍 [TLDRGraph Flow Query]: '{query_text}'\n💾 Saved flow paths in YAML: {yaml_file}\n")
    for i, res in enumerate(results, 1):
        click.echo(f"━━━ [Option {i}] Root: {res['root_node']} ({res['layer']}) (Score: {res['match_score']}) ━━━")
        click.echo(engine.render_markdown_table(res["flow"]) + "\n")


@cli.command()
@click.argument("source")
@click.argument("target", required=False)
@click.option("--path", default=".", help="Repository root path")
def trace(source, target, path):
    """Trace exact execution path between two symbols across layers. Read-only: never enriches."""
    loader = GraphLoader(path)
    graph = loader.load_or_extract(enrich_llm=False)
    engine = FlowEngine(graph, loader.vector_store, root_dir=path)
    res = engine.trace_path(source, target)
    if "error" in res:
        click.echo(f"❌ {res['error']}")
        return
    click.echo(f"\n🔄 [TLDRGraph Trace]: '{res.get('source')}' ➔ '{res.get('target', 'downstream')}'")
    click.echo(engine.render_markdown_table(res.get("steps", [])) + "\n")


@cli.command()
@click.option("--path", default=".", help="Repository root path")
def layers(path):
    """View node count summary across all architectural layers. Read-only: never enriches."""
    loader = GraphLoader(path)
    loader.load_or_extract(enrich_llm=False)
    click.echo("\n🏛️  TLDRGraph Multi-Layer Architecture Summary:\n")
    for layer, nodes in loader.nodes_by_layer.items():
        click.echo(f"  • {layer.ljust(35)} : {len(nodes)} nodes")
    click.echo(f"\nTotal Nodes Mapped: {loader.graph.number_of_nodes()}")


@cli.command("queue-enrichment")
@click.option("--path", default=".", help="Repository root path")
@click.option("--limit", default=50, show_default=True,
              help="Maximum nodes to queue in this batch. 0 queues every remaining candidate.")
@click.option("--requeue", is_flag=True,
              help="Also re-queue ids handed out earlier but never applied (abandoned batches).")
@click.option("--reset", is_flag=True,
              help="Forget all queue progress and start again from the highest-priority node.")
def queue_enrichment(path, limit, requeue, reset):
    """
    Queue the highest-value un-enriched nodes for the coding agent.

    Writes .tldrgraph/enrichment_request.yaml. The agent reads the source files and
    writes its answer to a DIFFERENT file, .tldrgraph/enrichment_response.yaml, which
    `apply-enrichment` then merges. Running this twice advances through the backlog
    instead of repeating the same nodes.
    """
    run_queue_enrichment(path, limit, requeue, reset)


@cli.command("apply-enrichment")
@click.argument("enrichment_file", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--path", default=".", help="Repository root path")
def apply_enrichment(enrichment_file, path):
    """
    Apply the agent's enrichment response into the graph, SQLite cache and vector index.

    With no argument, reads .tldrgraph/enrichment_response.yaml (or .json), falling back to
    legacy response files when present.
    """
    run_apply_enrichment(path, enrichment_file)


@cli.command("dead-code")
@click.option("--path", default=".", help="Repository root path")
@click.option("--status", "status", default="candidate", show_default=True,
              type=click.Choice(list(DEAD_CODE_STATUSES) + ["all"], case_sensitive=False),
              help="Which review status to list. 'candidate' = nothing observed reaches it "
                   "(evidence, not proof). 'unreviewed' = not enough evidence to conclude, "
                   "never treat as removable.")
@click.option("--limit", default=0, help="Max rows to print. 0 shows all.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for agent consumption.")
def dead_code(path, status, limit, as_json):
    """
    List nodes by reachability review status - REVIEW CANDIDATES, NOT CONFIRMED DEAD CODE.

    Static analysis cannot see reflection, DI containers, string-built routes or template
    references, so a 'candidate' is a node worth a human or agent review, not a node that
    is safe to delete. 'unreviewed' explicitly means there was not enough evidence to
    conclude anything. This command never deletes and never proposes deletion.
    """
    run_dead_code_report(path, status.lower(), limit, as_json)


@cli.command()
@click.option("--path", default=".", help="Repository root path")
@embeddings_option
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for agent consumption.")
def doctor(path, embeddings, as_json):
    """Report which retrieval backend is ACTUALLY live, and why."""
    store = vs_mod.LocalVectorStore(os.path.join(path, STATE_DIR, "vector_index.json"), embeddings=embeddings)
    d = store.diagnostics()
    if as_json:
        click.echo(json.dumps(d, indent=2, default=str))
        return
    print_doctor_report(d)


@cli.command()
@click.option("--path", default=".", help="Repository root path")
@click.option("--all-agents", is_flag=True,
              help="Write the /tldrgraph-init command for every agent tool TLDRGraph knows.")
def install(path, all_agents):
    """Install TLDRGraph agent rules for Claude Code, Cursor and Antigravity."""
    gitignore = ensure_gitignore(path)
    res = install_agent_rules(path, all_agents=all_agents)
    click.echo("✅ TLDRGraph agent skills & rules installed successfully:")
    for k, v in res.items():
        if k == "gitignore":
            continue
        click.echo(f"  • {k}: {v}")
    click.echo(f"  • gitignore: {gitignore['path']} ({gitignore['status']})")
    click.echo("\n💡 Your agent can now run /tldrgraph-init (or just `tldrgraph init`) to build the whole graph.")
    for warning in gitignore_warnings(path):
        click.echo(f"⚠️  {warning}")


@cli.command("propose-layers")
@click.option("--path", default=".", help="Repository root path")
@click.option("--auto", is_flag=True, help="Try to synthesize the layer set now via an agent CLI or LLM")
@click.option("--force", is_flag=True, help="Force overwrite an existing layers.config.yaml")
def propose_layers_cmd(path, auto, force):
    """Write the layer-proposal request for the agent, or try to synthesize it now."""
    if auto:
        reg, out_path, source = auto_configure_layers(path, force=force, use_agent=True)
        if reg is not None:
            click.echo(f"✅ Configured {len(reg)} architectural layers ({source}) in {out_path}")
            click.echo("🔄 Run `tldrgraph init` to reclassify nodes with the new layer set.")
            return
        click.echo("ℹ️  Nothing could design the layers automatically, and TLDRGraph has no template to fall back on.")

    req_path = generate_propose_request(path)
    click.echo(f"📋 Queued layer proposal request in {req_path}")
    resp_rel = os.path.join(STATE_DIR, PROPOSE_RESPONSE_FILENAME)
    click.echo(f"👉 Read it, READ THE SOURCE, write {resp_rel}, then run `tldrgraph init`.")


@cli.command("apply-layers")
@click.argument("response_file", required=False, type=click.Path(exists=True, dir_okay=False))
@click.option("--path", default=".", help="Repository root path")
def apply_layers_cmd(response_file, path):
    """Validate and apply proposed architectural layers into .tldrgraph/layers.config.yaml."""
    try:
        out_path = apply_proposed_layers(path, response_file)
        click.echo(f"✅ Applied and validated architectural layer set in {out_path}")
        click.echo("🔄 Run `tldrgraph scan .` to reclassify nodes with the new layer set.")
    except Exception as err:
        raise click.ClickException(str(err))


def main():
    cli()


if __name__ == "__main__":
    main()
