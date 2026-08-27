"""
Agent integration: one body of instructions, written the same way for every tool.

TLDRGraph is driven **by** the agent through a file handshake, never the reverse.
Every tool can read a file, read source, and run a shell command, so that is the
entire integration surface -- no per-tool flags, auth, or headless quirks.

Two artifacts, and only two:

* **instructions** -- always-loaded context. ``AGENTS.md`` is the cross-tool
  standard and is always written. A tool gets its own copy *only* if it does not
  read AGENTS.md, and that copy is byte-identical apart from frontmatter.
* **command** -- the ``/tldrgraph-init`` workflow, in each tool's command
  directory.

No tool gets bespoke prose and no tool gets extra artifacts. There used to be a
Claude-only skill file plus a CLAUDE.md section plus a Cursor rule plus an
Antigravity rule, each with different wording and different lengths; they drifted
apart immediately and contradicted each other. Adding a tool is one row in
TARGETS -- paths only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: Delimiters for the managed region inside a user-owned instructions file.
BLOCK_BEGIN = "<!-- BEGIN TLDRGRAPH -->"
BLOCK_END = "<!-- END TLDRGRAPH -->"

#: The workflow name installed everywhere. Deliberately not bare `tldrgraph`:
#: that would collide with the package's own CLI name in some shells.
COMMAND_NAME = "tldrgraph-init"

#: The cross-tool instructions standard. Read by Claude Code, Cursor, Codex,
#: Antigravity, opencode, Gemini CLI, Zed and Copilot, so those tools need no file of their own.
AGENTS_MD = "AGENTS.md"


@dataclass(frozen=True)
class AgentTarget:
    """One coding tool's file conventions. Paths only -- never behaviour."""
    name: str
    command_path: Optional[str] = None
    instructions_path: Optional[str] = None
    marker: Optional[str] = None
    frontmatter: bool = True


#: Every tool TLDRGraph knows, with paths for commands, skills, and instructions.
TARGETS: Tuple[AgentTarget, ...] = (
    AgentTarget("Claude Code", command_path=f".claude/commands/{COMMAND_NAME}.md"),
    AgentTarget("Cursor", command_path=f".cursor/commands/{COMMAND_NAME}.md"),
    AgentTarget("opencode", command_path=f".opencode/command/{COMMAND_NAME}.md",
                marker=".opencode"),
    # Codex's repository-local skill convention. Antigravity also reads this path.
    AgentTarget("Codex",
                command_path=f".agents/skills/{COMMAND_NAME}/SKILL.md"),
    AgentTarget("Cline",
                command_path=f".clinerules/workflows/{COMMAND_NAME}.md",
                instructions_path=".clinerules/tldrgraph.md",
                marker=".clinerules"),
    AgentTarget("Windsurf",
                command_path=f".windsurf/workflows/{COMMAND_NAME}.md",
                instructions_path=".windsurf/rules/tldrgraph.md",
                marker=".windsurf"),
    AgentTarget("Roo Code", command_path=f".roo/commands/{COMMAND_NAME}.md",
                marker=".roo"),
    AgentTarget("Kilo Code", command_path=f".kilocode/workflows/{COMMAND_NAME}.md",
                marker=".kilocode"),
    AgentTarget("Goose", command_path=f".goosehints/{COMMAND_NAME}.md",
                marker=".goosehints", frontmatter=False),
    AgentTarget("Continue", command_path=f".continue/prompts/{COMMAND_NAME}.prompt",
                marker=".continue", frontmatter=False),
)

#: Files earlier versions installed. Removed on install so a repo does not carry
#: two contradictory descriptions of the workflow.
SUPERSEDED = (
    ".claude/skills/tldrgraph/SKILL.md",
    ".claude/skills/codechakra/SKILL.md",
    ".claude/commands/tldrgraph-layers.md",
    ".claude/commands/tldrgraph-enrich.md",
    ".cursor/rules/tldrgraph.mdc",
    ".agents/workflows/tldrgraph.md",
    ".agents/workflows/tldrgraph-init.md",
    ".agents/rules/tldrgraph.md",
    ".agents/rules/tldrgraph-init.md",
)


# The single source of content.

#: Always-loaded context. Short on purpose: it competes with everything else in
#: the agent's window, so it says what TLDRGraph is for and where the detail is.
INSTRUCTIONS_BODY = f"""## TLDRGraph

This repository is mapped into architectural layers designed from its own source,
with per-symbol intents you can search and trace.

### ⛔ FORBIDDEN TOOL BEHAVIORS
- **DO NOT use `grep_search`, `ripgrep`, or `find_by_name` as your primary discovery tool.** Blind text searching is strictly prohibited for codebase exploration, feature lookup, and understanding component flows.
- **DO NOT guess symbol locations or execution paths.**

### ✅ MANDATORY FIRST-STEP PROTOCOL
Whenever the user asks where a feature lives, how a component works, or what a symbol connects to, your first tool call MUST be `run_command` with one of the following:

```bash
tldrgraph query "<feature in plain English>"   # semantic search + end-to-end flow
tldrgraph trace "<Source>" "<Target>"          # exact path between two symbols
tldrgraph layers                               # node counts per layer
tldrgraph dead-code                            # review candidates, never a delete list
```

**Discovery Pattern**:
1. Run `tldrgraph query "<query>"` or `tldrgraph trace "<from>" "<to>"` to identify the exact file, layer, and line range.
2. Use `view_file` on the target file path returned by TLDRGraph to inspect the code.

Those are read-only and never trigger enrichment.

**To build or refresh the graph**, run `tldrgraph init`. It automatically handles
layer design, extraction, source-aware enrichment in 200-node batches, and dense
embeddings when a supported agent CLI is available. If it prints a `NEXT ACTION`
fallback, follow that handoff without guessing from symbol names.

Full workflow: `.claude/commands/{COMMAND_NAME}.md` (identical copies live in every
other agent directory). Schema: `.tldrgraph/AGENT_CONTRACT.md`.

`tldrgraph dead-code` lists **review candidates, not confirmed dead code**.
`unreviewed` means "not enough evidence to conclude" and is never removable.

## Code Quality & Architectural Standards
- **File Length Limit**: Every source file in `tldrgraph/` must be strictly under 400 lines. Split large modules into cohesive sub-units.
- **Function Complexity**: Functions and methods must be focused (<= 50 lines) with low cyclomatic complexity (<= 15).
- **Modularity & Re-exports**: Keep modules decoupled; preserve backwards compatibility with top-level package re-exports.
"""

#: The full `init` workflow.
COMMAND_BODY = """# TLDRGraph: build this repository's architecture graph

In Claude Code or Cursor, invoke `/tldrgraph-init`. In Codex CLI, type `/skills`
and select `tldrgraph-init`, or mention `$tldrgraph-init` directly.

One command handles layer design, extraction, enrichment, and embeddings:

```bash
tldrgraph init
```

By default TLDRGraph detects `claude`, `cursor-agent`, or `gemini`, asks once before enrichment token spend, processes every
eligible node in batches of 200, and downloads/builds the local embedding model.
Use `--yes` for non-interactive approval, `--batch N` to override the batch size,
`--embeddings off|auto|on` to override embeddings, or `--no-agent-cli` for the
manual file handoff.

After the user approves the full run, use exactly `tldrgraph init --yes`. The
approval is saved for the current candidate set, so later `tldrgraph init` calls
must continue without asking again. `--batch 200` means all nodes in 200-node
batches; `--limit 200` means stop after only 200 nodes. Never add `--limit` or
`--embeddings off` unless the user explicitly requests a partial or no-embedding run.

If no supported agent is available or dense embeddings cannot be built, `init`
preserves the graph and prints a resumable status. It never guesses source intent
or architectural layers.

## `status: needs_layers`

TLDRGraph ships **no layer templates** and will not invent an architecture.
Design one from this repository.

1. Read `.tldrgraph/propose_layers_request.json`. It carries the symbols and
   files extraction already found -- a starting point, not a substitute for
   opening the code.
2. **Open real source files**: entry points first, then a representative file
   from each cluster in the evidence. Work out what this codebase actually does
   and where responsibility changes hands.
3. Write `.tldrgraph/propose_layers_response.json`:

```json
{
  "utility_id": "<id of your catch-all layer>",
  "layers": [
    {
      "id": "short_machine_id",
      "name": "Layer 1: Human Friendly Name",
      "order": 1,
      "description": "One sentence on what lives here",
      "rules": [
        {"file_contains": ["substring"], "exclude_file": ["optional"]},
        {"label_contains": ["SymbolNamePart"]}
      ]
    }
  ]
}
```

4. Run `tldrgraph init` again; it continues with enrichment and embeddings.

### Rules that hold for any answer

- 3 to 6 layers, plus exactly one catch-all whose `id` equals `utility_id` and
  whose `rules` are `[]`.
- Unique `id` and `name` per layer; sequential integer `order` from 1.
- Rule keys: `file_contains`, `exclude_file`, `path_regex`, `label_contains`,
  `exclude_label`, `label_ends_with`, `type_in`, `id_prefix`. Values are lists of
  strings. Rules are evaluated in `order` and the first match wins.
- Derive rules from paths and symbol names you actually saw. A rule matching
  nothing is worse than no rule; a rule matching everything collapses the map.

## `status: needs_confirmation`

The output shows how many nodes need enrichment and how many agent round-trips
that implies. **Ask the user whether to proceed, and show them that estimate.**
Do not decide for them.

- They agree: `tldrgraph init --yes` saves approval for the full campaign
- Smaller first pass: `tldrgraph init --yes --limit 100`
- They decline: stop. The graph is already built and queryable.

## `status: needs_enrichment`

1. Read `.tldrgraph/enrichment_request.yaml`.
2. **Open the source file of every node in it.** This is the entire point: an
   intent paraphrased from a symbol name poisons semantic search with
   confident-sounding noise.
3. Write `.tldrgraph/enrichment_response.yaml` -- a *different* file from the
   request, which is regenerated on every run:

```yaml
- id: "<node id copied verbatim from the request>"
  intent: |
    What this symbol does, why it exists, and its execution logic.
  input_fields: [caseId, remarks]
  output_fields: [status, disposition]
  calls: [ApplicationsService, pension_cases]
```

4. Run `tldrgraph init` again. Approval is already saved. If another
   `needs_enrichment` batch appears, process it immediately and repeat this loop
   without asking the user again. Continue until `status: done`.

Inside an existing Codex/Claude/Cursor session, nested-agent protection may stop
the CLI from launching a second agent. In that case **you are the enrichment
agent**: process every 200-node batch yourself. A `needs_enrichment` status is a
continuation instruction, not a reason to stop or request confirmation.

**Copy every `id` verbatim.** A constructed id matches nothing, is dropped, and
gets reported back to you -- but the work is wasted.

**Never invent `fields` or `calls`.** Omit what you cannot verify in the code: an
empty list is a correct answer, a wrong `calls` entry becomes a real wrong edge.

## Once it says DONE

```bash
tldrgraph query "<feature in plain English>"
tldrgraph trace "<Source>" "<Target>"
tldrgraph layers
tldrgraph ui --serve
```

Read-only, and they never trigger enrichment. Full schema:
`.tldrgraph/AGENT_CONTRACT.md`.
"""


def _frontmatter(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n"


def command_text(target: Optional[AgentTarget] = None) -> str:
    """The command file body, with frontmatter when the target expects it."""
    if target is not None and not target.frontmatter:
        return COMMAND_BODY
    return _frontmatter(
        COMMAND_NAME,
        "Build or continue this repository's TLDRGraph architecture graph "
        "(layers, extraction, enrichment)",
    ) + COMMAND_BODY


def instructions_text(target: Optional[AgentTarget] = None) -> str:
    """
    The instructions body. Identical everywhere -- that is the whole point.

    AGENTS.md gets it bare, since it is merged into a user-owned file. A per-tool
    copy gets frontmatter if that tool expects it.
    """
    if target is None or not target.frontmatter:
        return INSTRUCTIONS_BODY
    return _frontmatter(
        "tldrgraph", "TLDRGraph architecture graph: trace before you edit"
    ) + INSTRUCTIONS_BODY


# --------------------------------------------------------------------------- #
# Installation
# --------------------------------------------------------------------------- #

def _write_if_changed(path: str, content: str) -> bool:
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


def active_targets(root_dir: str, all_agents: bool = False) -> List[AgentTarget]:
    """
    Targets to write for this repository.

    Unconditional tools always; the rest only when the repo shows the tool is in
    use, so a project does not accumulate dotfiles for agents it has never
    opened. ``all_agents`` writes them all.
    """
    root = os.path.abspath(root_dir)
    return [
        t for t in TARGETS
        if all_agents or t.marker is None or os.path.isdir(os.path.join(root, t.marker))
    ]


def remove_superseded(root_dir: str = ".") -> List[str]:
    """
    Deletes files earlier versions installed, returning what was removed.

    Left in place they describe a workflow that no longer exists, and an agent
    reading both gets contradictory instructions.
    """
    root = os.path.abspath(root_dir)
    removed: List[str] = []
    for rel in SUPERSEDED:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed.append(rel)
            except OSError:
                continue
            # Clean up a directory we created and just emptied.
            parent = os.path.dirname(path)
            try:
                if parent != root and not os.listdir(parent):
                    os.rmdir(parent)
            except OSError:
                pass
    return removed


def install_agent_commands(root_dir: str = ".", all_agents: bool = False) -> Dict[str, str]:
    """
    Writes the instructions and the ``tldrgraph-init`` command for every active
    tool, plus the AGENTS.md block that covers everything else.

    Returns ``{display_name: path}``.
    """
    from .installer import upsert_block  # local import: installer imports us back

    root = os.path.abspath(root_dir)
    written: Dict[str, str] = {}

    # AGENTS.md first: it is the standard, and it is merged, never clobbered.
    agents_md = os.path.join(root, AGENTS_MD)
    existing: Optional[str] = None
    if os.path.isfile(agents_md):
        try:
            with open(agents_md, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = None
    _write_if_changed(
        agents_md,
        upsert_block(existing or "", INSTRUCTIONS_BODY, BLOCK_BEGIN, BLOCK_END),
    )
    written[f"{AGENTS_MD} (instructions, all agents)"] = agents_md

    for target in active_targets(root, all_agents=all_agents):
        if target.command_path:
            path = os.path.join(root, target.command_path)
            _write_if_changed(path, command_text(target))
            written[f"{target.name} (command)"] = path
        if target.instructions_path:
            path = os.path.join(root, target.instructions_path)
            _write_if_changed(path, instructions_text(target))
            written[f"{target.name} (instructions)"] = path

    return written
