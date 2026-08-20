<!-- BEGIN TLDRGRAPH -->
## TLDRGraph

This repository is mapped into architectural layers designed from its own source,
with per-symbol intents you can search and trace.

**Before planning or implementing a feature**, trace it instead of grepping:

```bash
tldrgraph query "<feature in plain English>"   # semantic search + end-to-end flow
tldrgraph trace "<Source>" "<Target>"          # exact path between two symbols
tldrgraph layers                               # node counts per layer
tldrgraph dead-code                            # review candidates, never a delete list
```

Those are read-only and never trigger enrichment.

**To build or continue the graph**, run `tldrgraph init`, do what the `NEXT ACTION`
block prints, and run it again -- repeat until `status: done`. It has no template
fallback: if this repository has no architecture yet, it will stop and ask you to
design one from the code. Do not skip reading the files.

Full workflow: `.claude/commands/tldrgraph-init.md` (identical copies live in every
other agent directory). Schema: `.tldrgraph/AGENT_CONTRACT.md`.

`tldrgraph dead-code` lists **review candidates, not confirmed dead code**.
`unreviewed` means "not enough evidence to conclude" and is never removable.
<!-- END TLDRGRAPH -->
