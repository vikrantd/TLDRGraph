"""
Agent Installer for TLDRGraph.

Writes the two things a repository needs to be agent-ready:

    <root>/.tldrgraph/AGENT_CONTRACT.md   -- the request/response schema
    <root>/.gitignore                     -- managed block for generated state

plus the per-tool instruction and command files, which live in
:mod:`.agent_commands` so that every agent gets byte-identical content.

This module used to generate five differently-worded rule files -- a Claude
skill, a CLAUDE.md section, a Cursor rule, an Antigravity rule and an Antigravity
workflow. They drifted apart immediately. There is now one body of instructions
and one command, written to whatever paths each tool uses.

Every write is idempotent: unchanged files are left alone, and user-owned files
are *never* clobbered -- only the region between the TLDRGraph markers changes.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .agent_commands import install_agent_commands, remove_superseded
from .layer_config import load_layer_config
from .layers import LayerRegistry, get_registry

#: Delimiters for the managed region inside a user-owned CLAUDE.md.
CLAUDE_MD_BEGIN = "<!-- BEGIN TLDRGRAPH -->"
CLAUDE_MD_END = "<!-- END TLDRGRAPH -->"

#: Delimiters for the managed region inside a user-owned .gitignore.
GITIGNORE_BEGIN = "# BEGIN TLDRGRAPH"
GITIGNORE_END = "# END TLDRGRAPH"

#: Files inside .tldrgraph/ that are worth committing: the contract every agent
#: reads, and the layer map, so a teammate's scan classifies the code the same
#: way instead of re-deriving its own.
GITIGNORE_KEEP = ("AGENT_CONTRACT.md", "layers.config.yaml")

#: The managed .gitignore body.
#:
#: `.tldrgraph/*` rather than `.tldrgraph/` is load-bearing: git never descends
#: into an excluded *directory*, so a trailing-slash ignore makes the negations
#: below unreachable. Excluding the directory's *entries* keeps them working.
GITIGNORE_BLOCK = "\n".join(
    [
        "# TLDRGraph analysis state. Generated artifacts are ignored; the agent",
        "# contract and layer map are committed so the whole team shares them.",
        ".tldrgraph/*",
    ]
    + [f"!.tldrgraph/{name}" for name in GITIGNORE_KEEP]
)

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

# Legacy module-level aliases for backwards compatibility.
_LAYERS = generate_layers_prose()
AGENT_CONTRACT_FALLBACK = make_agent_contract_fallback()


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


def upsert_block(existing: Optional[str], body: str, begin: str, end: str) -> str:
    """
    Returns ``existing`` with the region between ``begin`` and ``end`` replaced
    by ``body``, appending the region if it is not present yet.
    """
    block = f"{begin}\n{body.strip()}\n{end}\n"

    if not existing or not existing.strip():
        return block

    start = existing.find(begin)
    stop = existing.find(end)
    if start != -1 and stop != -1 and stop > start:
        head = existing[:start]
        tail = existing[stop + len(end):].lstrip("\n")
        return f"{head}{block}{tail}"

    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return f"{existing}{separator}{block}"


def strip_block(existing: str, begin: str, end: str) -> str:
    """Returns ``existing`` with the managed region and its markers removed."""
    start = existing.find(begin)
    stop = existing.find(end)
    if start == -1 or stop == -1 or stop < start:
        return existing
    return (existing[:start].rstrip("\n") + "\n" + existing[stop + len(end):].lstrip("\n")).lstrip("\n")


def upsert_delimited_section(existing: Optional[str], section_body: str) -> str:
    """
    Returns ``existing`` with the TLDRGraph-delimited region replaced by
    ``section_body``, appending the region if it is not present yet.
    """
    return upsert_block(existing, section_body, CLAUDE_MD_BEGIN, CLAUDE_MD_END)


def _neutralize_directory_ignores(text: str) -> str:
    """
    Comments out any hand-written ``.tldrgraph`` / ``.tldrgraph/`` line outside
    the managed block.

    Such a line ignores the *directory*, which stops git descending into it, so
    the managed block's ``!.tldrgraph/...`` negations could never take effect.
    The line is commented rather than deleted so the edit is visible and
    reversible in a diff.
    """
    if not text:
        return text

    out: List[str] = []
    inside_block = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped == GITIGNORE_BEGIN:
            inside_block = True
        elif stripped == GITIGNORE_END:
            inside_block = False
        elif not inside_block and stripped.lstrip("/").rstrip("/") == ".tldrgraph" \
                and not stripped.startswith("#"):
            out.append(f"# {raw}  # superseded by the TLDRGraph block below")
            continue
        out.append(raw)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def ensure_gitignore(root_dir: str = ".") -> Dict[str, str]:
    """
    Adds (or refreshes) TLDRGraph's managed block in the repository's .gitignore.

    Ignores everything generated under ``.tldrgraph/`` while keeping the agent
    contract and layer map committable. Creates the file if it does not exist.
    Idempotent: an already-correct .gitignore is left byte-identical.

    Returns ``{"path": ..., "status": created|updated|unchanged}``.
    """
    path = os.path.join(os.path.abspath(root_dir), ".gitignore")

    existing: Optional[str] = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = None

    updated = upsert_block(
        _neutralize_directory_ignores(existing or ""), GITIGNORE_BLOCK,
        GITIGNORE_BEGIN, GITIGNORE_END,
    )

    if existing == updated:
        return {"path": path, "status": "unchanged"}

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
    except OSError as err:
        return {"path": path, "status": f"failed: {err}"}

    return {"path": path, "status": "created" if existing is None else "updated"}


def install_agent_rules(root_dir: str = ".", all_agents: bool = False) -> Dict[str, str]:
    """
    Makes a repository agent-ready: the .gitignore block, the shared contract,
    and one body of instructions plus one command written to whatever paths each
    tool uses. No tool gets bespoke prose and no tool gets extra artifacts.

    ``all_agents`` writes for every tool TLDRGraph knows, not just the ones this
    repository shows signs of using.
    """
    root = os.path.abspath(root_dir)
    registry, _ = load_layer_config(root)
    written: Dict[str, str] = {}

    # 0a. Keep generated state out of git, but leave the contract and layer map
    #     committable so the team shares one architecture definition.
    written["gitignore"] = ensure_gitignore(root)["path"]

    # 0. The contract every rule file points at.
    contract_path = os.path.join(root, CONTRACT_REL_PATH)
    _write_if_changed(contract_path, _contract_text(registry))
    written["contract"] = contract_path

    # 1. The instructions and the command, identical for every tool, at
    #    whatever paths each one uses. See agent_commands.TARGETS.
    written.update(install_agent_commands(root, all_agents=all_agents))

    # 2. Remove what earlier versions installed. Left behind, those files
    #    describe a workflow that no longer exists.
    removed = remove_superseded(root)

    # 3. Our managed block inside a user-owned CLAUDE.md is superseded by
    #    AGENTS.md, which Claude Code also reads. Drop the block, keep whatever
    #    the user wrote around it, and delete the file only if we created it and
    #    nothing else is left.
    claude_md_path = os.path.join(root, "CLAUDE.md")
    if os.path.isfile(claude_md_path):
        try:
            with open(claude_md_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = None
        if existing and CLAUDE_MD_BEGIN in existing:
            remainder = strip_block(existing, CLAUDE_MD_BEGIN, CLAUDE_MD_END)
            if remainder.strip():
                _write_if_changed(claude_md_path, remainder)
            else:
                try:
                    os.remove(claude_md_path)
                    removed.append("CLAUDE.md")
                except OSError:
                    pass

    if removed:
        written["superseded (removed)"] = ", ".join(removed)

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

    # `.tldrgraph` is deliberately absent: ensure_gitignore now manages it with
    # negations that keep the contract and layer map committable, so warning
    # about it would contradict what this installer just wrote.
    watched = {".agents": ".agents/", ".claude": ".claude/", ".cursor": ".cursor/",
               "CLAUDE.md": "CLAUDE.md"}
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
