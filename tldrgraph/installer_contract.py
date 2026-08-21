"""
Contract generation and fallback templates for the TLDRGraph installer.
"""

from __future__ import annotations

import os
from typing import Optional

from .layers import LayerRegistry, get_registry


#: Where the contract is installed inside a target repository.
CONTRACT_REL_PATH = os.path.join(".tldrgraph", "AGENT_CONTRACT.md")

#: Canonical contract lives next to the package (repo checkout / editable install).
_CONTRACT_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AGENT_CONTRACT.md"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "AGENT_CONTRACT.md"),
)


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


_LOOP = """One command does everything -- layers, extraction, enrichment -- and is resumable:

```bash
tldrgraph init          # prints a NEXT ACTION block whenever it needs you
# do exactly what that block says (design layers, or read source and write intents)
tldrgraph init --yes    # run it again; repeat until it prints status: done
```

`init` never guesses. It has no template to fall back on, so if this repository has no
architecture yet it stops and asks you to design one from the code. When it needs
enrichment it hands you a batch, and you OPEN THE SOURCE FILES before writing anything.

The underlying steps stay available for scripting:

```bash
tldrgraph queue-enrichment --limit 50   # writes .tldrgraph/enrichment_request.yaml
tldrgraph apply-enrichment              # merges intents + bridge edges into the graph
```

The `/tldrgraph-init` command installed in your agent's command directory is this loop
written out in full."""

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


_FALLBACK_TEMPLATE = """# TLDRGraph Agent Contract

**Audience: the coding agent with this repository open.**

TLDRGraph maps this codebase into {count} architectural layers:

{layers_prose}

Structure within a layer comes free from the AST. Indirect dispatch, queue/event hops,
dynamic routes and all natural-language intent must come from you, because you can open
the files.

## The loop

{loop}

| File | Written by | Read by |
| --- | --- | --- |
| `.tldrgraph/enrichment_request.json` | `queue-enrichment` | you |
| `.tldrgraph/enrichment_response.json` | **you** | `apply-enrichment` |
| `.tldrgraph/pending_enrichment.json` | *(legacy)* | `apply-enrichment`, only if no response file exists |

## Response schema

A JSON array of objects -- nothing else, no markdown fence, no commentary:

{response_schema}

- `id` (**required**) - the node id, verbatim from the request.
- `intent` - 1-2 sentences of plain English; this is what semantic search matches.
- `fields` - the form fields / API params / DB columns actually handled.
- `calls` - the downstream APIs / services / DB tables this symbol reaches.

`fields` and `calls` may be empty or omitted.

## Hard rules

{rules}

## Queue priority

Candidates are ordered by `cross_layer_degree` (neighbours in a different layer)
descending, then total `degree` (in + out) descending, then node id. Hub nodes and
cross-layer seams first.

`queue-enrichment` records progress in `.tldrgraph/enrichment_cursor.json`, so running it
twice advances instead of repeating. `--requeue` re-hands-out in-flight ids, `--reset`
clears all progress, `--limit 0` queues everything.

## dead-code

{dead_code_note}
"""


def make_agent_contract_fallback(registry: Optional[LayerRegistry] = None) -> str:
    """Constructs the fallback AGENT_CONTRACT.md content."""
    layers_prose = generate_layers_prose(registry)
    count = len(registry.ordered()) - 1 if registry else 6
    return _FALLBACK_TEMPLATE.format(
        count=count,
        layers_prose=layers_prose,
        loop=_LOOP,
        response_schema=_RESPONSE_SCHEMA,
        rules=_RULES_SHORT,
        dead_code_note=_DEAD_CODE_NOTE,
    )


def contract_text(registry: Optional[LayerRegistry] = None) -> str:
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
