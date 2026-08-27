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

**To build or refresh the graph**, run `tldrgraph init`. It automatically handles
layer design, extraction, source-aware enrichment in 200-node batches, and dense
embeddings when a supported agent CLI is available. If it prints a `NEXT ACTION`
fallback, follow that handoff without guessing from symbol names.

Full workflow: `.claude/commands/tldrgraph-init.md` (identical copies live in every
other agent directory). Schema: `.tldrgraph/AGENT_CONTRACT.md`.

`tldrgraph dead-code` lists **review candidates, not confirmed dead code**.
`unreviewed` means "not enough evidence to conclude" and is never removable.

## Code Quality & Architectural Standards
- **File Length Limit**: Every source file in `tldrgraph/` must be strictly under 400 lines. Split large modules into cohesive sub-units.
- **Function Complexity**: Functions and methods must be focused (<= 50 lines) with low cyclomatic complexity (<= 15).
- **Modularity & Re-exports**: Keep modules decoupled; preserve backwards compatibility with top-level package re-exports.
<!-- END TLDRGRAPH -->
