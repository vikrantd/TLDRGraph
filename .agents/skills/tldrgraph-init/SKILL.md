---
name: tldrgraph-init
description: Build or continue this repository's TLDRGraph architecture graph (layers, extraction, enrichment)
---

# TLDRGraph: build this repository's architecture graph

One command, run repeatedly until it says DONE. `tldrgraph init` never guesses:
it stops and tells you exactly what it needs.

```bash
tldrgraph init
```

Read the `NEXT ACTION` block it prints, do what it says, then run `tldrgraph init`
again. Repeat until the output says `status: done`. There are only three things
it can ask for.

## 1. `status: needs_layers`

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

4. Run `tldrgraph init` again.

### What a layer set looks like

Sketches from other codebases, to show the *shape* of an answer. They are not a
menu and none of them will fit this repository -- read the code and name what you
actually find.

- A web app might split presentation from request handling from domain logic
  from persistence, with background jobs and deployment config as their own tiers.
- A CLI tool might split the command surface from the processing engine from
  local state, with adapters to outside systems separate again.
- A library might split its public API from the core implementation from its
  data types, with backend adapters separate.
- A data pipeline might split ingestion from transformation from model training
  from serving.

The useful question is not "which of these is it?" but "where does responsibility
change hands in *this* code, and what would a new engineer need named?"

### Rules that hold for any answer

- 3 to 6 layers, plus exactly one catch-all whose `id` equals `utility_id` and
  whose `rules` are `[]`.
- Unique `id` and `name` per layer; sequential integer `order` from 1.
- Rule keys: `file_contains`, `exclude_file`, `path_regex`, `label_contains`,
  `exclude_label`, `label_ends_with`, `type_in`, `id_prefix`. Values are lists of
  strings. Rules are evaluated in `order` and the first match wins.
- Derive rules from paths and symbol names you actually saw. A rule matching
  nothing is worse than no rule; a rule matching everything collapses the map.

## 2. `status: needs_confirmation`

The output shows how many nodes need enrichment and how many agent round-trips
that implies. **Ask the user whether to proceed, and show them that estimate.**
Do not decide for them.

- They agree: `tldrgraph init --yes`
- Smaller first pass: `tldrgraph init --yes --limit 100`
- They decline: stop. The graph is already built and queryable.

## 3. `status: needs_enrichment`

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

4. Run `tldrgraph init --yes` again. It applies the response and hands you the
   next batch, until there is nothing left.

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
