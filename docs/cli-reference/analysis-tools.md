# CLI Reference: Analysis & Dead Code

Tools for inspecting layers, reachability, and serving the visualizer.

---

## `tldrgraph dead-code`

Surfaces unreferenced components, orphaned functions, and unused files across the repository.

```bash
tldrgraph dead-code [OPTIONS]
```

### Interpretation of Results

- **Review Candidates**: The output lists symbols with zero incoming callers across known entry points.
- **Unreviewed**: Signals that insufficient evidence exists to confirm whether a symbol is called dynamically (e.g. via reflection, dynamic dispatch, or external consumers).
- **Never an Automatic Deletion List**: Always review candidates manually or with tests before removal.

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--layer` | integer | `None` | Filter candidates by architectural layer ID. |
| `--json` | boolean | `False` | Output dead-code candidates as JSON. |

---

## `tldrgraph layers`

Prints a summary of all configured architectural layers, file patterns, and node counts.

```bash
tldrgraph layers [OPTIONS]
```

### Example Output

```text
Layer ID | Layer Name                    | Nodes | File Count
---------+-------------------------------+-------+-----------
Layer 1  | CLI & Agent Surface           |    42 |          4
Layer 2  | Pipeline & Ingestion          |    38 |          3
Layer 3  | Analysis & Extraction Engine  |    91 |          8
Layer 4  | Vector Index & Retrieval      |    35 |          3
Layer 5  | Visualization & Web UI        |    47 |          5
Layer 6  | Core Types & Utilities        |    29 |          4
```

---

## `tldrgraph ui`

Generates and serves the interactive browser visualizer.

```bash
tldrgraph ui [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--serve` | boolean | `False` | Starts local HTTP server for on-demand live source code viewing. |
| `--port` | integer | `7777` | Port for the live source server. |
| `--no-open` | boolean | `False` | Generates HTML bundle without auto-opening a browser tab. |
