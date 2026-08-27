# CLI Reference: Query & Trace

Commands for searching execution flows and tracing call paths.

---

## `tldrgraph query`

Performs hybrid semantic search across the codebase and returns end-to-end execution flow tables.

```bash
tldrgraph query "<search prompt>" [OPTIONS]
```

### Examples

```bash
# Query an architectural flow in plain English
tldrgraph query "how does user signup and email verification work"

# Request top 10 execution flows
tldrgraph query "jwt token validation" --top-k 10

# Query using lexical TF-IDF only (skip dense model)
tldrgraph query "database connection pool" --embeddings off
```

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--top-k` | integer | `5` | Number of flow paths to retrieve and format. |
| `--embeddings` | `auto` / `off` | `auto` | Whether to use dense vector search or fall back to lexical. |
| `--json` | boolean | `False` | Output results as JSON. |

---

## `tldrgraph trace`

Finds the shortest cross-layer dependency path between two specific symbols.

```bash
tldrgraph trace "<SourceSymbol>" "<TargetSymbol>" [OPTIONS]
```

### Example

```bash
tldrgraph trace "AuthController" "UserRepository"
```

Output:
```text
Trace Path (3 hops, spans 3 layers):
  [Layer 1: CLI & Agent Surface] AuthController (app/controllers/auth.py:24)
    ──calls──> AuthService.login (app/services/auth.py:52)
  [Layer 2: Pipeline & Ingestion] AuthService (app/services/auth.py:52)
    ──queries──> UserRepository.find_by_email (app/repos/user.py:18)
  [Layer 3: Analysis & Extraction Engine] UserRepository (app/repos/user.py:18)
```

### Options

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--directed` | boolean | `True` | Respect call direction (only trace forward calls). |
| `--max-depth` | integer | `10` | Maximum call depth to explore. |
| `--json` | boolean | `False` | Output raw path triples as JSON. |
