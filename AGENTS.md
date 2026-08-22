<!-- BEGIN TLDRGRAPH -->
## TLDRGraph

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

**To build or continue the graph**, run `tldrgraph init`, do what the `NEXT ACTION`
block prints, and run it again -- repeat until `status: done`. It has no template
fallback: if this repository has no architecture yet, it will stop and ask you to
design one from the code. Do not skip reading the files.

Full workflow: `.claude/commands/tldrgraph-init.md` (identical copies live in every
other agent directory). Schema: `.tldrgraph/AGENT_CONTRACT.md`.

`tldrgraph dead-code` lists **review candidates, not confirmed dead code**.
`unreviewed` means "not enough evidence to conclude" and is never removable.

## Code Quality & Architectural Standards
- **File Length Limit**: Every source file in `tldrgraph/` must be strictly under 400 lines. Split large modules into cohesive sub-units.
- **Function Complexity**: Functions and methods must be focused (<= 50 lines) with low cyclomatic complexity (<= 15).
- **Modularity & Re-exports**: Keep modules decoupled; preserve backwards compatibility with top-level package re-exports.
<!-- END TLDRGRAPH -->
