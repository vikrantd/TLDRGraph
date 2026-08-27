# Agent Contract Specification

TLDRGraph defines a formal request/response contract in `.tldrgraph/AGENT_CONTRACT.md`. This schema governs how agents interact with TLDRGraph in non-interactive or batch environments.

---

## The Contract Workflow

```mermaid
sequenceDiagram
    participant Agent as Coding Agent
    participant CLI as TLDRGraph CLI
    participant Config as .tldrgraph/layers.config.yaml

    Agent->>CLI: tldrgraph init --json
    CLI-->>Agent: {"status": "needs_layers", "symbols": [...]}
    Agent->>Config: Writes layers.config.yaml
    Agent->>CLI: tldrgraph init --json
    CLI-->>Agent: {"status": "needs_confirmation", "planned_nodes": 200}
    Agent->>Agent: Prompt user for LLM spend approval
    Agent->>CLI: tldrgraph init --batch 200 --yes --json
    CLI-->>Agent: {"status": "done", "nodes": 350, "edges": 520}
```

---

## Contract States & Payloads

### 1. `needs_layers`
Returned when `.tldrgraph/layers.config.yaml` is absent.

```json
{
  "status": "needs_layers",
  "reason": "Repository-specific architectural layers must be designed.",
  "extracted_symbols_count": 420,
  "top_directories": ["src/api", "src/services", "src/models"],
  "next_action": "Read repository symbols and create .tldrgraph/layers.config.yaml"
}
```

### 2. `needs_confirmation`
Returned before the agent begins spending tokens on LLM summaries.

```json
{
  "status": "needs_confirmation",
  "total_nodes": 350,
  "candidates_count": 180,
  "planned_this_run": 180,
  "batch_size": 200,
  "agent_rounds": 1,
  "next_action": "Ask user: 'TLDRGraph needs to enrich 180 architectural bottleneck symbols across 1 agent round. Proceed?'"
}
```

### 3. `needs_enrichment`
Returned when a batch of nodes is ready for semantic summarization.

```json
{
  "status": "needs_enrichment",
  "batch": [
    {
      "id": "tldrgraph/graph_loader.py::GraphLoader",
      "type": "class",
      "file": "tldrgraph/graph_loader.py",
      "line_start": 45,
      "line_end": 350,
      "current_intent": null
    }
  ]
}
```

### 4. `done`
Returned when extraction, layer classification, enrichment, and vector embeddings are up to date.

```json
{
  "status": "done",
  "nodes_count": 350,
  "edges_count": 520,
  "layers_count": 6,
  "embeddings_ready": true
}
```
