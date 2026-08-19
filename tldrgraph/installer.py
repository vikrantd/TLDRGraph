"""
Agent Installer for TLDRGraph.

Writes host-agent rules for the three coding agents that actually drive the
enrichment loop, plus the agent contract they all point at:

    <root>/.tldrgraph/AGENT_CONTRACT.md      -- the request/response schema
    <root>/.claude/skills/codechakra/SKILL.md -- Claude Code skill
    <root>/CLAUDE.md                          -- delimited TLDRGraph section
    <root>/.cursor/rules/tldrgraph.mdc       -- Cursor project rule
    <root>/.agents/rules/tldrgraph.md        -- Antigravity rule
    <root>/.agents/workflows/tldrgraph.md    -- Antigravity workflow

Every write is idempotent: unchanged files are left alone, and CLAUDE.md is
*never* clobbered -- only the region between the TLDRGraph markers is replaced.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .layer_config import load_layer_config
from .layers import LayerRegistry, get_registry

#: Delimiters for the managed region inside a user-owned CLAUDE.md.
CLAUDE_MD_BEGIN = "<!-- BEGIN TLDRGRAPH -->"
CLAUDE_MD_END = "<!-- END TLDRGRAPH -->"

#: Where the contract is installed inside a target repository.
CONTRACT_REL_PATH = os.path.join(".tldrgraph", "AGENT_CONTRACT.md")

#: Canonical contract lives next to the package (repo checkout / editable install).
_CONTRACT_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AGENT_CONTRACT.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AGENT_CONTRACT.md"),
)


# --------------------------------------------------------------------------- #
# Shared prose generators
# --------------------------------------------------------------------------- #

def generate_layers_prose(registry: Optional[LayerRegistry] = None) -> str:
    """Generates a numbered markdown list of active architectural layers."""
    reg = registry or get_registry()
    lines = []
    for layer in reg.ordered():
        if layer.id == reg.utility_id:
            continue
        desc = f" - {layer.description}" if layer.description else ""
        lines.append(f"{layer.order}. **{layer.name}**{desc}")
    return "\n".join(lines)


_LOOP = """```bash
tldrgraph queue-enrichment --limit 50   # writes .tldrgraph/enrichment_request.yaml
# read the request, OPEN THE SOURCE FILES, write .tldrgraph/enrichment_response.yaml
tldrgraph apply-enrichment              # merges intents + bridge edges into the graph
tldrgraph queue-enrichment --limit 50   # repeat; the queue advances by itself
```"""

_RULES_SHORT = """- **Read the source file before writing an intent.** You have the repo open -- that is
  the entire reason this path exists. An intent paraphrased from the symbol name is worse
  than none, because it poisons semantic search with confident-sounding noise.
- **Never invent `fields` or `calls`. Omit what you cannot verify in the code.** An empty
  list is a correct answer; a wrong `calls` entry becomes a real, wrong edge in the graph.
- **`calls` are resolved with 2-tier precision.** Exact symbol names
  (`ApplicationsService`), file names (`calc.ts`), IDs, and table names (`pension_cases`)
  match with 100% confidence; fallback vector search handles related terms with a 0.35 score floor.
- **Write the response to a different file than the request.** The request is regenerated
  on every run."""

_RESPONSE_SCHEMA = """```yaml
- id: "<node id copied verbatim from the request>"
  intent: |
    ### Summary / Role in Markdown
    What this symbol does, why it exists, and execution logic.
  input_fields:
    - caseId
    - remarks
  output_fields:
    - status
    - disposition
  calls:
    - ApplicationsService
    - pension_cases
```"""

_DEAD_CODE_NOTE = """`tldrgraph dead-code` lists **review candidates, not confirmed dead code**.
`candidate` means nothing observed reaches the node -- evidence, not proof.
`unreviewed` means there was not enough evidence to conclude anything, and must never be
treated as removable. Reflection, DI containers, string-built routes and template
references all hide real callers from a static graph. Confirm in the source before
removing anything. TLDRGraph has no delete capability."""


# --------------------------------------------------------------------------- #
# Contract (fallback used only when the shipped AGENT_CONTRACT.md is missing)
# --------------------------------------------------------------------------- #

def make_agent_contract_fallback(registry: Optional[LayerRegistry] = None) -> str:
    layers_prose = generate_layers_prose(registry)
    count = len(registry.ordered()) - 1 if registry else 6
    return f"""# TLDRGraph Agent Contract

**Audience: the coding agent with this repository open.**

TLDRGraph maps this codebase into {count} architectural layers:

{layers_prose}

Structure within a layer comes free from the AST. Indirect dispatch, queue/event hops,
dynamic routes and all natural-language intent must come from you, because you can open
the files.

## The loop

{_LOOP}

| File | Written by | Read by |
| --- | --- | --- |
| `.tldrgraph/enrichment_request.json` | `queue-enrichment` | you |
| `.tldrgraph/enrichment_response.json` | **you** | `apply-enrichment` |
| `.tldrgraph/pending_enrichment.json` | *(legacy)* | `apply-enrichment`, only if no response file exists |

## Response schema

A JSON array of objects -- nothing else, no markdown fence, no commentary:

{_RESPONSE_SCHEMA}

- `id` (**required**) - the node id, verbatim from the request.
- `intent` - 1-2 sentences of plain English; this is what semantic search matches.
- `fields` - the form fields / API params / DB columns actually handled.
- `calls` - the downstream APIs / services / DB tables this symbol reaches.

`fields` and `calls` may be empty or omitted.

## Hard rules

{_RULES_SHORT}

## Queue priority

Candidates are ordered by `cross_layer_degree` (neighbours in a different layer)
descending, then total `degree` (in + out) descending, then node id. Hub nodes and
cross-layer seams first.

`queue-enrichment` records progress in `.tldrgraph/enrichment_cursor.json`, so running it
twice advances instead of repeating. `--requeue` re-hands-out in-flight ids, `--reset`
clears all progress, `--limit 0` queues everything.

## dead-code

{_DEAD_CODE_NOTE}
"""


def _contract_text(registry: Optional[LayerRegistry] = None) -> str:
    """The canonical AGENT_CONTRACT.md if it ships alongside the package, else the fallback."""
    for candidate in _CONTRACT_CANDIDATES:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    text = f.read()
                if text.strip():
                    return text
            except OSError:
                continue
    return make_agent_contract_fallback(registry)


# --------------------------------------------------------------------------- #
# Per-agent payloads
# --------------------------------------------------------------------------- #

def make_claude_skill(registry: Optional[LayerRegistry] = None) -> str:
    layers_prose = generate_layers_prose(registry)
    count = len(registry.ordered()) - 1 if registry else 6
    return f"""---
name: codechakra
description: >-
  Trace and enrich this repository's {count}-layer architecture graph. Use when asked how a
  feature flows end to end, which layer a symbol belongs to, what reaches a DB table or
  endpoint, or when asked to enrich / describe un-enriched TLDRGraph nodes.
---

# TLDRGraph: {count}-Layer Code Flow & Semantic Navigation

{layers_prose}

## Before planning or changing a feature

```bash
tldrgraph query "<feature in plain English>"    # semantic search + end-to-end flow
tldrgraph trace "<Source>" "<Target>"           # exact path between two symbols
tldrgraph layers                                # node counts per layer
```

`query`, `trace`, `layers` and `dead-code` are read-only: they never fire enrichment.
`.tldrgraph/layers.yaml` and `.tldrgraph/flows.yaml` hold the exported context.

## Enrichment loop -- this is your job, not an API's

The hosted-LLM path only ever sees a label and a file path. You have the repo open, so
you are the primary enrichment path.

{_LOOP}

Response format (`.tldrgraph/enrichment_response.json`), a bare JSON array:

{_RESPONSE_SCHEMA}

{_RULES_SHORT}

Full schema: `{CONTRACT_REL_PATH}`.

## dead-code

```bash
tldrgraph dead-code                  # defaults to --status candidate
tldrgraph dead-code --status unreviewed --json
```

{_DEAD_CODE_NOTE}
"""


def make_claude_md_section(registry: Optional[LayerRegistry] = None) -> str:
    count = len(registry.ordered()) - 1 if registry else 6
    names = " -> ".join(l.name.split(":")[-1].strip() for l in (registry or get_registry()).ordered() if l.id != (registry or get_registry()).utility_id)
    return f"""## TLDRGraph ({count}-layer code flow engine)

This repo is mapped by TLDRGraph into {count} architectural layers ({names}). Snapshot: `.tldrgraph/graph.json`, summaries:
`.tldrgraph/layers.yaml`.

**Before planning or implementing a feature**, trace it rather than grepping:

```bash
tldrgraph query "<feature in plain English>"
tldrgraph trace "<Source>" "<Target>"
```

**When nodes are un-enriched, enrich them yourself** -- you can open the files, the API
path cannot:

{_LOOP}

Write a bare JSON array of `{{id, intent, fields, calls}}` to the response file.
{_RULES_SHORT}

Full contract: `{CONTRACT_REL_PATH}`.

`tldrgraph dead-code` lists **review candidates, not confirmed dead code**; `unreviewed`
means "not enough evidence to conclude" and is never removable. Confirm in the source.
"""


def make_cursor_rule(registry: Optional[LayerRegistry] = None) -> str:
    layers_prose = generate_layers_prose(registry)
    count = len(registry.ordered()) - 1 if registry else 6
    return f"""---
description: TLDRGraph {count}-layer code flow, semantic navigation, and agent enrichment loop
globs: ["**/*"]
alwaysApply: true
---

# TLDRGraph Multi-Layer Code Flow Rules

{layers_prose}

## Navigate before you edit

- Run `tldrgraph query "<feature>"` or `tldrgraph trace "<Source>" "<Target>"` to get
  the real end-to-end path across layers before planning a change.
- Read `.tldrgraph/layers.yaml` and `.tldrgraph/flows.yaml` for architectural context.
- `query`, `trace`, `layers`, `dead-code` are read-only and never trigger enrichment.

## Enrichment loop (you are the primary enrichment path)

{_LOOP}

Response file is a bare JSON array:

{_RESPONSE_SCHEMA}

{_RULES_SHORT}

Full schema: `{CONTRACT_REL_PATH}`.

## dead-code

{_DEAD_CODE_NOTE}
"""


def make_antigravity_rule(registry: Optional[LayerRegistry] = None) -> str:
    layers_prose = generate_layers_prose(registry)
    count = len(registry.ordered()) - 1 if registry else 6
    return f"""---
description: TLDRGraph {count}-Layer Code Flow & Semantic Navigation Engine
globs: **/*
---

# TLDRGraph Multi-Layer Code Flow Rules

This project uses TLDRGraph to map full-stack code into {count} layers:

{layers_prose}

## Rules for Antigravity

- Before planning or implementing any feature, run `tldrgraph query "<feature>"` or
  `tldrgraph trace "<Source>" "<Target>"` to trace exact end-to-end execution paths.
- Read `.tldrgraph/layers.yaml` and `.tldrgraph/flows.yaml` for architectural context.
- Un-enriched nodes are enriched by **you**, locally, with no third-party API key:

{_LOOP}

Response file is a bare JSON array:

{_RESPONSE_SCHEMA}

{_RULES_SHORT}

Full schema: `{CONTRACT_REL_PATH}`.

## dead-code

{_DEAD_CODE_NOTE}
"""


def make_antigravity_workflow(registry: Optional[LayerRegistry] = None) -> str:
    count = len(registry.ordered()) - 1 if registry else 6
    return f"""---
name: codechakra
description: Multi-layer code flow tracing, semantic search, and agent enrichment ({count} Layers)
---

# Workflow: TLDRGraph Flow Engine

1. `tldrgraph scan .` -- refresh AST, layers, and the local vector index.
2. `tldrgraph query "<feature>"` -- inspect the full {count}-layer execution path.
3. `tldrgraph layers` -- architectural layer summary.
4. Enrichment loop, highest-value nodes first:

{_LOOP}

5. `tldrgraph dead-code` -- list review candidates (never a delete list).

Full schema for step 4: `{CONTRACT_REL_PATH}`.
"""


# Legacy module-level aliases for backwards compatibility
_LAYERS = generate_layers_prose()
AGENT_CONTRACT_FALLBACK = make_agent_contract_fallback()
CLAUDE_SKILL = make_claude_skill()
CLAUDE_MD_SECTION = make_claude_md_section()
CURSOR_RULE = make_cursor_rule()
ANTIGRAVITY_RULE = make_antigravity_rule()
ANTIGRAVITY_WORKFLOW = make_antigravity_workflow()


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #

def _write_if_changed(path: str, content: str) -> bool:
    """Writes ``content`` to ``path`` unless it is already byte-identical. Returns True if written."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == content:
                    return False
        except OSError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def upsert_delimited_section(existing: Optional[str], section_body: str) -> str:
    """
    Returns ``existing`` with the TLDRGraph-delimited region replaced by
    ``section_body``, appending the region if it is not present yet.
    """
    block = f"{CLAUDE_MD_BEGIN}\n{section_body.strip()}\n{CLAUDE_MD_END}\n"

    if not existing or not existing.strip():
        return block

    start = existing.find(CLAUDE_MD_BEGIN)
    end = existing.find(CLAUDE_MD_END)
    if start != -1 and end != -1 and end > start:
        head = existing[:start]
        tail = existing[end + len(CLAUDE_MD_END):]
        tail = tail.lstrip("\n")
        return f"{head}{block}{tail}"

    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return f"{existing}{separator}{block}"


def install_agent_rules(root_dir: str = ".") -> Dict[str, str]:
    """
    Installs TLDRGraph rules for Claude Code, Cursor and Antigravity, plus the shared
    agent contract.
    """
    root = os.path.abspath(root_dir)
    registry, _ = load_layer_config(root)
    written: Dict[str, str] = {}

    # 0. The contract every rule file points at.
    contract_path = os.path.join(root, CONTRACT_REL_PATH)
    _write_if_changed(contract_path, _contract_text(registry))
    written["contract"] = contract_path

    # 1. Claude Code -- skill + a delimited section in CLAUDE.md.
    skill_path = os.path.join(root, ".claude", "skills", "tldrgraph", "SKILL.md")
    _write_if_changed(skill_path, make_claude_skill(registry))
    written["claude_skill"] = skill_path

    claude_md_path = os.path.join(root, "CLAUDE.md")
    existing = None
    if os.path.isfile(claude_md_path):
        try:
            with open(claude_md_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = None
    _write_if_changed(claude_md_path, upsert_delimited_section(existing, make_claude_md_section(registry)))
    written["claude_md"] = claude_md_path

    # 2. Cursor project rule.
    cursor_path = os.path.join(root, ".cursor", "rules", "tldrgraph.mdc")
    _write_if_changed(cursor_path, make_cursor_rule(registry))
    written["cursor_rule"] = cursor_path

    # 3. Antigravity rule + workflow.
    antigravity_rule = os.path.join(root, ".agents", "rules", "tldrgraph.md")
    _write_if_changed(antigravity_rule, make_antigravity_rule(registry))
    written["antigravity_rule"] = antigravity_rule

    antigravity_workflow = os.path.join(root, ".agents", "workflows", "tldrgraph.md")
    _write_if_changed(antigravity_workflow, make_antigravity_workflow(registry))
    written["antigravity_workflow"] = antigravity_workflow

    return written


def gitignore_warnings(root_dir: str = ".") -> List[str]:
    """Returns human-readable warnings for installed rule directories ignored by .gitignore."""
    gitignore_path = os.path.join(os.path.abspath(root_dir), ".gitignore")
    if not os.path.isfile(gitignore_path):
        return []
    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []

    watched = {".agents": ".agents/", ".claude": ".claude/", ".cursor": ".cursor/",
               ".tldrgraph": ".tldrgraph/", "CLAUDE.md": "CLAUDE.md"}
    warnings: List[str] = []
    for lineno, raw in enumerate(lines, 1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        key = entry.lstrip("/").rstrip("/")
        if key in watched:
            warnings.append(
                f".gitignore:{lineno} ignores '{entry}' - rules written under "
                f"{watched[key]} will not reach the rest of the team."
            )
    return warnings
