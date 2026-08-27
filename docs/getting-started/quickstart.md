# Quickstart: 5-Minute Tour

Get up and running with TLDRGraph in your repository.

---

## Step 1: Initialize the Graph with Your Agent

!!! tip "Agent-Driven Architecture"
    **Do not run `tldrgraph init` manually in a terminal.** TLDRGraph is designed to be agent-driven: your coding agent inspects the repository's symbols and designs architectural layers tailored specifically to your project.

Trigger the initialization command within your coding agent:

=== "Claude Code / Cursor"

    ```bash
    /tldrgraph-init
    ```

=== "Codex"

    Open `/skills` and select `tldrgraph-init`, or run:
    ```bash
    $tldrgraph-init
    ```

=== "Other Agents (Antigravity, Windsurf, Roo)"

    Ask your agent:
    ```text
    Please run the /tldrgraph-init workflow as specified in AGENTS.md.
    ```

### What Happens Behind the Scenes:
1. **AST Extraction**: Parses Python, TypeScript, and JavaScript source files into an AST symbol graph.
2. **Dynamic Layer Design**: Your agent reads the extracted symbols and writes `.tldrgraph/layers.config.yaml` with customized layer boundaries.
3. **Approval & Enrichment**: Asks once before spending LLM tokens to summarize key bottleneck symbols in 200-node chunks.
4. **Vector Indexing**: Automatically builds local FastEmbed ONNX dense embeddings.

---

## Step 2: Explore the Interactive Visualizer

Launch the self-contained visualizer:

```bash
tldrgraph ui --serve
```

This opens `.tldrgraph/TLDRGRAPH_VISUALIZER.html` in your default web browser:
- **Architecture Map**: Zoom out to see multi-layer module clusters; zoom in to see classes, functions, and cross-layer connection lines.
- **Workflows Explorer**: Click on the Workflows tab to view step-by-step decision journeys with inputs, outputs, and branch conditions.
- **Node Inspector**: Click any node to isolate upstream callers, downstream callees, and read live file content.

---

## Step 3: Query Execution Flows via CLI

Ask natural language queries about how features work:

```bash
tldrgraph query "pension calculation and approval flow"
```

TLDRGraph retrieves top execution flows spanning across UI, API, Service, and DB layers, formatting them as compact Markdown tables.

---

## Step 4: Trace Exact Call Paths

Trace the shortest cross-layer dependency path between any two symbols:

```bash
tldrgraph trace "ApplicationsController" "PensionCalculator"
```

Output:
```text
Trace Path: ApplicationsController -> PensionService -> PensionCalculator
  Layer 1: ApplicationsController [API Layer]
    └── calls PensionService.calculate() (tldrgraph/service.py:42)
  Layer 2: PensionService [Business Logic]
    └── calls PensionCalculator.evaluate() (tldrgraph/calculator.py:18)
```

---

## Step 5: Review Dead Code Candidates

Inspect unreferenced symbols that may be safe to refactor or remove:

```bash
tldrgraph dead-code
```

Surfaces orphaned functions, unused database models, and unreachable handlers with forward reachability confidence scores.
