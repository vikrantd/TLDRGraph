<p align="center">
  <img src="assets/tldrgraph_logo.svg" alt="TLDRGraph Logo" width="380" />
</p>

<h1 align="center">TLDRGraph 🌐</h1>

<p align="center">
  <strong>See the flow of your spaghetti code, VibeCoders.</strong> 🍝➡️⚡<br>
  <em>Dynamic Multi-Layer Code Flow, Instant Semantic Call Tracing, and Interactive Architectural Navigation.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/tldrgraph/"><img src="https://img.shields.io/pypi/v/tldrgraph.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/tldrgraph/"><img src="https://img.shields.io/pypi/pyversions/tldrgraph.svg" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/safishamsi/graphify"><img src="https://img.shields.io/badge/AST%20Engine-Graphify-emerald.svg" alt="Powered by Graphify"></a>
</p>

---

## 💡 What is TLDRGraph?

Modern codebases are messy. Microservices, multi-layer abstractions, dynamic API routes, and ORM calls create cognitive overload.

**TLDRGraph** cuts through the noise. It dynamically classifies your repository into tailored architectural layers, extracts cross-layer execution seams, tracks changes using zero-token SHA-256 hash gating, and provides both **CLI flow tables** and a **lightning-fast standalone visualizer**.

---

## 🏛️ Agent-Designed Architectural Layers

TLDRGraph does not pick your architecture from a menu, and **it ships no layer
templates at all**. On the first run it hands the repository to your coding agent
— with the symbols it just extracted, not merely a directory listing — and the
layer set the agent designs is written to `.tldrgraph/layers.config.yaml`, named
after your codebase's own concepts.

The agent is given *ideas*, not a template: a handful of one-line sketches of how
different kinds of codebase can divide, explicitly labelled as belonging to other
repositories, followed by the real question — *where does responsibility change
hands in this code?*

If no agent answers, TLDRGraph stops and asks. An unconfigured repository has a
single `Unclassified` bucket, not six confident guesses: a generic layer set is
wrong everywhere it looks right.

## 🚀 Quickstart

### 1. Install TLDRGraph
```bash
pip install tldrgraph
```
### 2. Build the graph — through your coding agent

**Do not run `tldrgraph init` manually in a terminal.** Start the
`tldrgraph-init` workflow in whichever coding agent you use; the agent reads the
repository, runs `init`, and handles every required follow-up. In Claude Code or
Cursor, run `/tldrgraph-init`. In Codex, open `/skills` and select
`tldrgraph-init`, or invoke `$tldrgraph-init`.

The agent designs the repository-specific layers, extracts the graph, asks once
before enrichment token spend, enriches every eligible node in 200-node batches,
and downloads/builds local dense embeddings. That approval is remembered for the
current candidate set until enrichment is complete.

`--batch 200` controls chunk size while still processing everything. `--limit
200` intentionally stops after 200 total nodes. Embeddings remain enabled unless
you explicitly pass `--embeddings off`.

If no supported agent is usable, it preserves everything already built and
prints a `NEXT ACTION` handoff. Follow that handoff and rerun the same command;
TLDRGraph never invents architecture or source intent.

It can report four resumable states:

| status | what it needs |
| --- | --- |
| `needs_layers` | Read the code and design the architecture. No template will be applied for you. |
| `needs_confirmation` | Shows how many nodes need enrichment and how many agent rounds that is. **Your agent asks you before spending tokens.** |
| `needs_enrichment` | A batch of nodes to open, read, and describe. |
| `needs_embeddings` | Enrichment is complete, but the required dense model/index could not be built. |

Your agent drives the whole process with the installed `tldrgraph-init`
workflow. Give it any scope or batch-size constraints you need; it will choose
the appropriate `init` options. `scan` and `enrich` are aliases for `init`, kept
for existing scripts, and should likewise be run by the agent rather than
manually.

### 3. Explore the Architecture Visually
```bash
tldrgraph ui --serve
```
Opens the interactive canvas:
- **Modules overview** at low zoom.
- **Symbol details** (classes, methods, inputs, outputs) as you zoom in.
- **Click-to-isolate** focused nodes with upstream callers and downstream callees.
- **Live source viewing** on demand with zero static HTML bloat.
- **⚠️ Dead Nodes filter** to immediately isolate unreferenced candidate symbols.

### 4. Query Execution Flows
```bash
tldrgraph query "pension application approval flow"
```
Outputs readable Markdown execution flow tables tracing the request across UI, API, Service, and DB layers.

### 5. Trace Exact Call Paths
```bash
tldrgraph trace "ApplicationsController" "JhPensionApplication"
```

### 6. Review Dead Code & Reachability
```bash
tldrgraph dead-code
```
Surfaces orphaned components, unreferenced models, and unused files for human review.

---

## 📊 Retrieval Benchmark: SWE-bench Lite

To evaluate codebase localization performance against industry baselines, TLDRGraph was benchmarked on **40 real-world GitHub issues** from the standard **SWE-bench Lite** dataset (measuring ground-truth modified file identification from natural language problem statements):

| Retrieval Engine | File Recall@1 | File Recall@5 | File Recall@10 | MRR | Context Budget | Search Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BM25 Lexical Search** | 55.0% | 65.0% | 77.5% | 0.610 | ~28,500 tokens | 1.01 ms |
| **Chunked Dense Vector RAG** | 42.5% | 70.0% | 77.5% | 0.535 | ~22,400 tokens | 22.37 ms |
| **Mem0 (Memory Vector Store)** | 35.0% | 70.0% | 77.5% | 0.508 | ~12,000 tokens | 22.69 ms |
| **Graphify (AST Knowledge Graph)** | 57.5% | 65.0% | 72.5% | 0.616 | ~9,500 tokens | 0.76 ms |
| **Aider Repo-Map (AST PageRank)** | 55.0% | 67.5% | 77.5% | 0.611 | ~8,200 tokens | 1.09 ms |
| **TLDRGraph (AST Zero-Token)** | **60.0%** | **75.0%** | **82.5%** | **0.672** | **~2,400 tokens** | 23.44 ms |
| **TLDRGraph (4-5 Line LLM Enriched)** | **62.5%** | **72.5%** | **80.0%** | **0.682** | **~3,200 tokens** | 23.93 ms |

> **Key Takeaways:**
> - **State-of-the-Art MRR (0.682)**: Combining agent-designed architectural layers with deep semantic intent achieves **0.682 MRR** and **62.5% Recall@1** (beating Mem0's 35.0%, Chunked RAG's 42.5%, Aider's 55.0%, and Graphify's 57.5%).
> - **Broad Multi-Layer Recall**: **TLDRGraph (AST Zero-Token)** scores **75.0% Recall@5** and **82.5% Recall@10** with **0 LLM tokens spent**.
> - **2.5×–4.0× Token Efficiency**: Compact, structured intent flows require only **~2,400–3,200 tokens** compared to ~8,200 tokens for Aider, ~9,500 tokens for Graphify, ~12,000 tokens for Mem0, and ~28,500 tokens for raw file chunking.

---

## 🤖 Works with any coding agent

TLDRGraph automatically launches a supported agent CLI when possible. Inside an
existing coding-agent session, or when no supported CLI is authenticated, it
falls back to a portable file handoff that any agent can drive.

Every tool gets the **same two artifacts and no more**: one body of instructions
and one `tldrgraph-init` command, byte-identical everywhere.

| Artifact | Where |
| --- | --- |
| **Instructions** | `AGENTS.md` — the cross-tool standard, read by Claude Code, Cursor, Codex, Antigravity, opencode, Gemini CLI, Zed and Copilot |
| | `.clinerules/`, `.windsurf/rules/` — only for tools not known to read AGENTS.md |
| **Command / skill** | `.claude/commands/`, `.cursor/commands/`, `.agents/skills/` (Codex), `.clinerules/workflows/`, `.windsurf/workflows/`, `.opencode/command/`, `.roo/commands/`, `.kilocode/workflows/`, `.goosehints/`, `.continue/prompts/` |

Codex intentionally uses `.agents/skills/tldrgraph-init/SKILL.md`, not a
`.codex/commands/` mirror. Codex does not load repository commands from
`.codex/commands`; its supported repository-local workflow location is
`.agents/skills`. Open `/skills` and select `tldrgraph-init`, or invoke it as
`$tldrgraph-init`. TLDRGraph writes the same workflow body there that it writes
for Claude Code and Cursor.

Tools with a marker directory are installed only when the repo shows them in
use; `tldrgraph install --all-agents` writes them all. Adding a tool is one row
in `TARGETS` in [agent_commands.py](tldrgraph/agent_commands.py) — **paths only,
never execution code.**

No tool gets special treatment. Earlier versions shipped a Claude-only skill file
*plus* a `CLAUDE.md` section *plus* a Cursor rule *plus* an Antigravity rule, each
worded differently and each a different length; they contradicted each other
within a release. `tldrgraph install` deletes those on sight.

### Agent execution controls

The `tldrgraph-init` workflow is the supported entry point. It can use the
agent's native session or a portable handoff, and preserves the graph while
providing the next action if agent work is unavailable. Run the workflow from
your coding agent; do not invoke `tldrgraph init` directly.

## 📁 Artifacts & Output Formats

Scanning a repository adds **one** directory, `.tldrgraph/` — graphify's raw export is
kept inside it rather than in a second top-level `graphify-out/`:

- `.tldrgraph/graph.json` : Persisted multi-layer graph snapshot with cross-layer edges.
- `.tldrgraph/layers.config.yaml`: The agent-designed layer definition. **Commit this.**
- `.tldrgraph/AGENT_CONTRACT.md`: The request/response contract. **Commit this.**
- `.tldrgraph/layers.yaml`: Layer distribution and node definitions.
- `.tldrgraph/flows.yaml` : Exported trace paths.
- `.tldrgraph/graphify_graph.json`, `.tldrgraph/graphify_manifest.json`: graphify's raw
  AST export and file manifest (renamed so they cannot collide with the enriched snapshot).
- `.tldrgraph/graphify/`: graphify's own AST cache.
- `.tldrgraph/tldrgraph.db`: Local SQLite content-hash cache for zero-token incremental updates.
- `.tldrgraph/TLDRGRAPH_VISUALIZER.html`: Standalone zero-dependency visualizer.

`tldrgraph install` (and every `scan`) adds a managed block to your `.gitignore` that
ignores the generated artifacts while keeping `layers.config.yaml` and `AGENT_CONTRACT.md`
committable, so your whole team shares one architecture map.

Upgrading from an older version? A leftover `graphify-out/` is no longer read or written;
`scan` will point it out so you can delete it.

---

## 🙏 Acknowledgements & Upstream Credits

TLDRGraph is built on the shoulders of giants. Sincere credit and special thanks to:
- **[Graphify](https://github.com/safishamsi/graphify)** by [Safi Shamsi](https://github.com/safishamsi) — for the AST parsing and knowledge graph extraction foundation.

---

## 📄 License

Distributed under the [MIT License](LICENSE).
