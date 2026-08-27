# CLI Reference: Init, Scan & Enrich

Commands for initializing and updating the repository graph.

---

## `tldrgraph init`

Initializes or refreshes the multi-layer graph, discovers layers, and generates embeddings.

```bash
tldrgraph init [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--batch` | integer | `200` | Chunk size for LLM enrichment batching. |
| `--limit` | integer | `None` | Caps total enriched nodes in this run. |
| `--embeddings` | `auto` / `off` | `auto` | Toggle dense FastEmbed ONNX embedding generation. |
| `--yes`, `-y` | boolean | `False` | Assume yes to confirmation prompts. |
| `--json` | boolean | `False` | Output status as structured JSON. |

### Aliases
`tldrgraph scan` and `tldrgraph enrich` are aliases for `tldrgraph init`, maintained for backward compatibility.

---

## `tldrgraph install`

Installs coding agent slash commands, skills, and execution rules across your local tools.

```bash
tldrgraph install [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--all-agents` | boolean | `False` | Install workflows for all supported agents regardless of project markers. |
| `--tool` | string | `None` | Target a specific agent (e.g. `claude`, `cursor`, `codex`, `windsurf`). |

---

## Resumable States

When running in automated agent workflows, `tldrgraph init` reports resumable execution states:

| Status | Meaning | Action Required |
| :--- | :--- | :--- |
| `needs_layers` | Layers are unconfigured. | Agent reads code and designs `.tldrgraph/layers.config.yaml`. |
| `needs_confirmation` | LLM token spend confirmation. | Agent prompts user once before enrichment batch. |
| `needs_enrichment` | Nodes awaiting semantic summary. | Agent opens source files and adds architectural intent. |
| `needs_embeddings` | Embeddings not yet compiled. | Downloads ONNX model and indexes vector store. |
| `done` | Initialization complete. | Graph is ready for querying and visualization. |
