# CodeChakra Agent Contract

**Audience: the coding agent with this repository open** (Claude Code, Cursor, Antigravity).

CodeChakra builds a 6-layer architectural graph from the graphify AST export. Structure
*within* a layer, and the high-volume deterministic seams between layers, are extracted
automatically. What cannot be extracted automatically is:

- indirect dispatch, queue / event hops, dynamically-built routes;
- the natural-language **intent** that makes semantic search work at all.

That is your job. You are not a fallback for a hosted model — you are the primary
enrichment path, because **you can open the files**. The API path only ever sees a label
and a path (`snippet` is never populated), so it guesses. You do not have to guess.

---

## The loop

```bash
codechakra queue-enrichment --limit 50   # 1. writes .codechakra/enrichment_request.json
#                                          2. you read it, read the SOURCE, and write
#                                             .codechakra/enrichment_response.json
codechakra apply-enrichment              # 3. merges into the graph, cache and index
codechakra queue-enrichment --limit 50   # 4. repeat -- the queue advances automatically
```

Request and response are **separate files**. Never write your answer back into
`enrichment_request.json`; it is regenerated on every run and your work would be lost.

| File | Written by | Read by |
| --- | --- | --- |
| `.codechakra/enrichment_request.json` | `queue-enrichment` | you |
| `.codechakra/enrichment_response.json` | **you** | `apply-enrichment` |
| `.codechakra/enrichment_cursor.json` | both commands | both commands |
| `.codechakra/pending_enrichment.json` | *(legacy)* | `apply-enrichment`, only if no response file exists |

---

## Request schema (`enrichment_request.json`)

```jsonc
{
  "schema": "codechakra/enrichment-request@1",
  "generated_at": "2026-08-19T00:00:00+00:00",
  "response_file": ".codechakra/enrichment_response.json",
  "contract": ".codechakra/AGENT_CONTRACT.md",
  "progress": {
    "total_candidates": 1873,   // un-enriched, non-utility nodes
    "already_enriched": 12,     // nodes that already carry an intent
    "queued_now": 50,           // entries in "nodes" below
    "remaining_after": 1823     // still waiting after this batch is applied
  },
  "nodes": [
    {
      "id": "backend_src_applications_applications_controller_applicationscontroller",
      "label": "ApplicationsController",
      "layer": "Layer 2: API Gateway",
      "file": "backend/src/applications/applications.controller.ts",
      "source_location": "L31",
      "degree": 41,               // in + out edges in the AST graph
      "cross_layer_degree": 17,   // of those, how many cross a layer boundary
      "rank": 1,                  // 1 = highest priority in this batch
      "existing_intent_source": "heuristic"  // "" when the node has no intent at all
    }
  ]
}
```

`file` is repo-relative. `source_location` is graphify's line hint and may be `null`.

`existing_intent_source` is `"heuristic"` when the node already carries an intent written
by the offline template enricher. That text was generated from the label and layer alone
— it has not read a line of source — so the node is still a candidate and your answer
should overwrite it. Applied answers are stamped `"agent"` and are never re-queued.

---

## Response schema (`enrichment_response.json`)

A **JSON array** of objects. Nothing else — no wrapper prose, no markdown fence, no
trailing commentary.

```json
[
  {
    "id": "backend_src_applications_applications_controller_applicationscontroller",
    "intent": "REST gateway for the pension application lifecycle. Authorizes DEO/AAO/AO/DAG roles, dispatches cases to ApplicationsService and records status transitions.",
    "fields": ["caseId", "transitionPayload", "remarks", "sanctionOrderNo", "disposition"],
    "calls": ["ApplicationsService", "JwtAuthGuard", "RolesGuard", "pension_cases"]
  }
]
```

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string, **required** | The node id, copied **verbatim** from the request. An id that is not in the graph is skipped silently. |
| `intent` | string | 1–2 sentences of plain English: what this symbol does and why it exists. This is the text semantic search matches against, so use the domain vocabulary a person would actually type. |
| `fields` | array of strings | The form fields / API params / DB columns this symbol actually handles. |
| `calls` | array of strings | The downstream APIs, services, DB tables or modules this symbol reaches. Each entry becomes a candidate cross-layer bridge edge. |

`fields` and `calls` may be omitted or empty. An object with only `id` and `intent` is
valid and useful.

An object wrapper is also accepted for convenience — `{"enrichments": [...]}`,
`{"nodes": [...]}`, `{"items": [...]}` or `{"results": [...]}` — but the bare array is
the canonical form.

---

## Hard rules

1. **Open and read the actual source file before writing an intent.** You have the repo
   checked out; that is the entire reason this path exists. Read `file` (use
   `source_location` to find the symbol), and read enough of its imports and callees to
   describe what it really does. An intent paraphrased from the label is worse than no
   intent, because it poisons search with confident-sounding noise.

2. **Do not invent fields or calls. Omit what you cannot verify in the code.** If you
   read the file and it handles three params, list three. Do not pad the list with what a
   symbol of that name "usually" has. `"fields": []` is a correct, honest answer.
   A wrong `calls` entry creates a real, wrong edge in the graph that later queries will
   follow.

3. **`calls` entries are resolved by TF-IDF search with a `0.35` score floor.** Exact
   identifiers resolve well; prose does not.

   | Good | Bad |
   | --- | --- |
   | `ApplicationsService` | `the application service` |
   | `calc.ts` | `some calculation helper` |
   | `pension_cases` | `the database` |
   | `JwtAuthGuard` | `auth stuff` |

   Anything scoring below the floor is dropped silently, so a vague entry is simply
   wasted work. Prefer the exact symbol name, file name, or table/model name as it
   appears in the source.

4. **Copy `id` verbatim.** Do not normalize, shorten or re-case it.

5. **Answer only the nodes in the request.** Extra ids are ignored; missing ids just come
   back in a later batch.

---

## Priority order in the queue

The queue is not arbitrary — a node that many things depend on is worth more of your
attention than a leaf. A node is a **candidate** when it sits outside `General / Utility`
and either has no intent at all, or has one that came from the offline template heuristic
(`enrichment_source: "heuristic"`, i.e. nobody read the source). Candidates are sorted by:

1. **`cross_layer_degree` descending** — neighbours that sit in a *different* layer.
   These are the seams CodeChakra exists to describe, and they are exactly where the AST
   alone is weakest.
2. **`degree` descending** — total in + out edges. Hub nodes first.
3. **node id ascending** — only to make the ordering deterministic.

Both degrees are computed from the live graph. (The `degree` key that graphify emits is
absent, so anything reading `node["degree"]` from the raw export sees `0`; CodeChakra
recomputes it and stamps it back into `.codechakra/graph.json`.)

---

## Paging and progress

`queue-enrichment` remembers what it has handed out in `.codechakra/enrichment_cursor.json`:

- `applied` — ids successfully merged by `apply-enrichment`. Never re-queued.
- `queued` — ids handed out but not yet applied ("in flight"). Skipped by default.

So running `queue-enrichment` twice in a row **advances** to the next batch instead of
repeating. Two escape hatches:

- `--requeue` — also hand out in-flight ids again (use when a batch was abandoned).
- `--reset` — clear all progress and start again from the highest-priority node.
- `--limit 0` — no cap; queue every remaining candidate at once.

---

## What `apply-enrichment` does with your answer

For each object it can match to a node:

1. sets `intent`, rewrites `summary` to `"<layer>: <label> - <intent>"`, sets `fields`;
2. writes the node into the SQLite hash-gate cache, keyed by a content signature, so the
   work survives re-scans and is not redone until the file actually changes;
3. resolves every `calls` entry through the vector index and, above the `0.35` floor,
   adds a `cross_layer_link` edge;
4. re-indexes and persists `.codechakra/graph.json` plus `.codechakra/layers.yaml`.

It then records the ids in the cursor so the next `queue-enrichment` moves on.

---

## Related commands

```bash
codechakra scan .                       # rebuild layers + index from graphify-out/
codechakra query "pension approval"     # semantic search + end-to-end flow trace
codechakra trace AaoDeskView pension_cases
codechakra layers                       # node counts per layer
codechakra dead-code --status candidate # nodes worth a human/agent review
```

`query`, `trace`, `layers` and `dead-code` are read commands: they never trigger
enrichment.

### `dead-code` is a review list, not a delete list

`dead-code` reports `dead_code_status` per node:

| Status | Means |
| --- | --- |
| `live` | Reached by something. |
| `entry_point` | A root: route handler, CLI entry, cron job, exported public API. |
| `candidate` | **Worth reviewing.** Nothing observed reaches it — which is evidence, not proof. |
| `unreviewed` | **Not enough evidence to conclude anything.** Never treat as removable. |

CodeChakra has no delete capability and will not gain one. Reflection, DI containers,
string-built routes, template references and test-only entry points all produce nodes the
static graph cannot see. Confirm with the source before removing anything.
