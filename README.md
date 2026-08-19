# CodeChakra ☸️

**Token-Efficient Dynamic Multi-Layer Code Flow & Semantic Navigation Engine**

CodeChakra dynamically maps codebases into customized, interconnected architectural layers (synthesized by LLMs or auto-detected by codebase archetype), tracks changes using a zero-token SHA-256 hash gate, indexes nodes with offline local vector search, and outputs readable **YAML** and **Markdown execution flow tables**.

---

## 🏛️ Dynamic Architectural Layers

Rather than forcing every project into a rigid model, CodeChakra dynamically adapts its architectural layers to match the repository:
- **CLI Applications:** CLI & Commands $\rightarrow$ Core Flow Engine & Logic $\rightarrow$ Graph Loader, Storage & Index $\rightarrow$ Agent Loop & Visualizer $\rightarrow$ Utilities
- **Full-Stack Web:** Presentation & UI $\rightarrow$ API Gateway $\rightarrow$ Domain Services $\rightarrow$ Data & Persistence $\rightarrow$ Async Tasks $\rightarrow$ DevOps
- **Backend APIs:** API & Handlers $\rightarrow$ Domain Services $\rightarrow$ Persistence & Repositories $\rightarrow$ Background Jobs $\rightarrow$ Utilities
- **Libraries & SDKs:** Public API & Interfaces $\rightarrow$ Core Processing Engine $\rightarrow$ Types & Models $\rightarrow$ Adapters $\rightarrow$ Utilities
- **Custom LLM-Generated:** LLMs synthesize project-specific multi-layer architectures and classification rules directly into `.codechakra/layers.config.yaml`.

---

## 🚀 Quickstart

### 1. Install CodeChakra locally
```bash
pip install -e ./codechakra
```

### 2. Scan & Classify Repository
```bash
codechakra scan .
```
This produces `.codechakra/layers.yaml` containing the complete categorized layer tree.

### 3. Query Execution Flows
```bash
codechakra query "AAO pension approval flow"
```

### 4. Trace Exact Call Paths
```bash
codechakra trace "AaoDeskView" "pension_cases"
```

### 4b. Layer Summary
```bash
codechakra layers
```

`query`, `trace`, `layers` and `dead-code` are **read commands**: they never trigger
enrichment and never rewrite your enriched intents.

### 4c. Explore the Architecture Visually
```bash
codechakra ui --serve
```

Opens the interactive canvas: modules at low zoom, symbols as you zoom in, and
click-to-focus that isolates a node with its callers and callees. The focused
card shows the symbol's intent, inputs, outputs and its **live source code**,
and `V` opens the whole file with a symbol outline.

Source is read from disk on demand, never baked into the HTML. `--serve` runs a
read-only localhost server so the page can fetch files directly. Without it,
`codechakra ui` still writes the standalone page - open it and use **Connect
project** to grant the browser read access to the repository folder.

### 5. Install Agent Rules (for AI Assistants)
```bash
codechakra install
```

Writes rules for all three coding agents, each pointing at the same contract:

| Path | Agent |
| --- | --- |
| `.codechakra/AGENT_CONTRACT.md` | the request/response schema everything points at |
| `.claude/skills/codechakra/SKILL.md` + a delimited section in `CLAUDE.md` | Claude Code |
| `.cursor/rules/codechakra.mdc` | Cursor |
| `.agents/rules/codechakra.md`, `.agents/workflows/codechakra.md` | Antigravity |

`install` is idempotent. It never clobbers an existing `CLAUDE.md` — only the region
between `<!-- BEGIN CODECHAKRA -->` and `<!-- END CODECHAKRA -->` is replaced. It also
warns when your `.gitignore` hides one of the directories it just wrote to.

---

## 🤖 The host-agent enrichment loop

The coding agent that has the repo open is the **primary** enrichment path, not a
fallback. It can open the files; the hosted-API path only ever sees a label and a path.

```bash
codechakra queue-enrichment --limit 50   # writes .codechakra/enrichment_request.json
# the agent reads the SOURCE FILES and writes .codechakra/enrichment_response.json
codechakra apply-enrichment              # merges intents, fields and bridge edges
codechakra queue-enrichment --limit 50   # repeat -- the queue advances by itself
```

Request and response are **separate files**; the request is regenerated on every run.

| File | Written by | Read by |
| --- | --- | --- |
| `.codechakra/enrichment_request.json` | `queue-enrichment` | the agent |
| `.codechakra/enrichment_response.json` | the agent | `apply-enrichment` |
| `.codechakra/enrichment_cursor.json` | both | both (paging state) |
| `.codechakra/pending_enrichment.json` | *(legacy)* | `apply-enrichment`, only when no response file exists |

The response is a JSON array of `{id, intent, fields, calls}`. Full schema, including the
two rules that matter most — *read the source before writing an intent* and *never invent
fields or calls* — is in [`AGENT_CONTRACT.md`](AGENT_CONTRACT.md).

**Queue priority.** graphify emits no `degree` key, so the old queue order was arbitrary.
CodeChakra now recomputes degree from the graph and orders candidates by
`cross_layer_degree` (neighbours in a different layer) descending, then total `degree`
(in + out) descending, then id. Cross-layer seams and hub nodes get agent attention first.

**Paging.** `--limit N` (default 50, `0` for everything), `--requeue` to re-hand-out an
abandoned batch, `--reset` to start over. Progress is reported on every run.

**Provenance.** Answers applied through this loop are stamped `enrichment_source: "agent"`
and never re-queued. Intents produced by the offline template heuristic are stamped
`"heuristic"` and *stay* candidates — that text never read a line of source.

---

## 🔎 Reachability review (`dead-code`)

```bash
codechakra dead-code                      # defaults to --status candidate
codechakra dead-code --status unreviewed --json
```

Lists nodes by `dead_code_status` with label, file, layer and the evidence string.

**These are review candidates, not confirmed dead code.** `candidate` means nothing
observed reaches the node — evidence, not proof. `unreviewed` means there was not enough
evidence to conclude anything and is *never* removable. Reflection, DI containers,
string-built routes and template references all hide real callers from a static graph.
CodeChakra has no delete capability and will not gain one.

---

## 📁 Output Formats
- `.codechakra/graph.json`  : Persisted node + edge snapshot, including agent bridges and intents.
- `.codechakra/layers.yaml` : Human-readable layer distribution and node definitions.
- `.codechakra/flows.yaml`  : Exported trace paths and flows for visualization tools.
- `.codechakra/codechakra.db`: Local SQLite content-hash cache for zero-token incremental updates.
- `.codechakra/AGENT_CONTRACT.md`: The enrichment request/response contract, installed by `codechakra install`.
- `.codechakra/CODECHAKRA_VISUALIZER.html`: Standalone interactive visualizer. Self-contained (no CDN, no bundler);
  project source is read live rather than embedded, so the page stays small and never goes stale.
