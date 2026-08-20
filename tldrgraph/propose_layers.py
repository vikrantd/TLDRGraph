"""
Propose Layers: Dynamic Multi-Layer Discovery & Configuration Generator.

Samples repository evidence (directory tree, framework markers, extracted
symbols, dependencies, entry points) and hands it to an agent that reads the
actual source before deciding what the layers are.

**There is no template fallback, by design.** A generic archetype layer set
looks plausible, classifies badly, and -- worst of all -- silently becomes the
answer. TLDRGraph would rather stop and ask. If no layer set can be obtained,
``needs_layers`` is returned and the caller tells the agent what to write.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import agent_runner, paths
from .layer_config import (
    CONFIG_FILENAME_YAML,
    config_path,
    load_layer_config,
    save_layer_config,
    validate_layer_config,
)
from .layers import LayerRegistry, get_registry

REQUEST_FILENAME = "propose_layers_request.json"
RESPONSE_FILENAME = "propose_layers_response.json"

# Archetype constants
ARCHETYPE_CLI = "cli_application"
ARCHETYPE_FULLSTACK = "fullstack_web"
ARCHETYPE_BACKEND = "backend_service"
ARCHETYPE_LIBRARY = "library_sdk"
ARCHETYPE_DATA_ML = "data_ml_pipeline"
ARCHETYPE_GENERIC = "generic_modular"


def _sample_repo_files(root_dir: str, max_per_dir: int = 10) -> Dict[str, List[str]]:
    """Samples representative file paths grouped by directory."""
    clusters: Dict[str, List[str]] = {}
    ignore_dirs = {
        ".git", "node_modules", "dist", ".next", "__pycache__", ".tldrgraph",
        "graphify-out", ".pytest_cache", ".venv", "venv", "build", "egg-info",
        ".egg-info"
    }

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [
            d for d in dirs
            if d not in ignore_dirs and not d.startswith(".") and not d.endswith(".egg-info")
        ]
        rel_dir = os.path.relpath(root, root_dir)
        if rel_dir == ".":
            rel_dir = "root"

        code_files = [
            f for f in files
            if not f.startswith(".") and f.endswith((
                ".ts", ".tsx", ".js", ".jsx", ".py", ".prisma", ".yaml", ".yml",
                ".json", ".go", ".rs", ".java", ".cpp", ".c", ".h", ".rb", ".php"
            ))
        ]
        if code_files:
            clusters[rel_dir] = sorted(code_files)[:max_per_dir]

    return clusters


def _collect_framework_markers(root_dir: str) -> Dict[str, Any]:
    """Detects presence of key frameworks, build tools, entry points, and schema files."""
    markers: Dict[str, Any] = {
        "frameworks": [],
        "cli_markers": [],
        "entry_points": [],
        "has_prisma_schema": False,
        "has_nest_cli": False,
        "has_next_config": False,
        "has_docker": False,
        "has_ci_workflows": False,
        "has_cli_entry": False,
        "languages": set(),
    }

    # Check Node / JavaScript / TypeScript package.json
    pkg_paths = [
        os.path.join(root_dir, "package.json"),
        os.path.join(root_dir, "frontend", "package.json"),
        os.path.join(root_dir, "backend", "package.json")
    ]
    for p in pkg_paths:
        if os.path.isfile(p):
            markers["languages"].add("javascript/typescript")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                    for fw in (
                        "next", "react", "@nestjs/core", "prisma", "@prisma/client",
                        "express", "fastify", "vue", "angular", "svelte",
                        "commander", "yargs", "inquirer", "chalk", "meow"
                    ):
                        if fw in deps and fw not in markers["frameworks"]:
                            markers["frameworks"].append(fw)
                            if fw in ("commander", "yargs", "inquirer", "chalk", "meow"):
                                markers["cli_markers"].append(fw)
                    if "bin" in data:
                        markers["has_cli_entry"] = True
                        markers["entry_points"].append("package.json:bin")
            except Exception:
                pass

    # Check Python pyproject.toml / setup.py / requirements.txt
    pyproject_path = os.path.join(root_dir, "pyproject.toml")
    if os.path.isfile(pyproject_path):
        markers["languages"].add("python")
        try:
            with open(pyproject_path, "r", encoding="utf-8") as f:
                content = f.read()
                if "project.scripts" in content or "console_scripts" in content:
                    markers["has_cli_entry"] = True
                    markers["entry_points"].append("pyproject.toml:project.scripts")
                for fw in ("click", "typer", "argparse", "fastapi", "flask", "django",
                           "sqlalchemy", "prisma", "torch", "pandas", "numpy", "celery", "networkx"):
                    if fw in content and fw not in markers["frameworks"]:
                        markers["frameworks"].append(fw)
                        if fw in ("click", "typer", "argparse"):
                            markers["cli_markers"].append(fw)
        except Exception:
            pass

    setup_py = os.path.join(root_dir, "setup.py")
    if os.path.isfile(setup_py):
        markers["languages"].add("python")
        try:
            with open(setup_py, "r", encoding="utf-8") as f:
                content = f.read()
                if "entry_points" in content or "console_scripts" in content:
                    markers["has_cli_entry"] = True
                    markers["entry_points"].append("setup.py:entry_points")
        except Exception:
            pass

    # Check Rust Cargo.toml
    cargo_path = os.path.join(root_dir, "Cargo.toml")
    if os.path.isfile(cargo_path):
        markers["languages"].add("rust")
        try:
            with open(cargo_path, "r", encoding="utf-8") as f:
                content = f.read()
                if "[[bin]]" in content or "clap" in content:
                    markers["has_cli_entry"] = True
                    markers["cli_markers"].append("clap")
                for fw in ("clap", "actix", "axum", "tokio", "diesel", "serde"):
                    if fw in content and fw not in markers["frameworks"]:
                        markers["frameworks"].append(fw)
        except Exception:
            pass

    # Check Go go.mod
    go_mod = os.path.join(root_dir, "go.mod")
    if os.path.isfile(go_mod):
        markers["languages"].add("go")
        try:
            with open(go_mod, "r", encoding="utf-8") as f:
                content = f.read()
                for fw in ("cobra", "gin-gonic", "gorm", "fiber", "chi"):
                    if fw in content and fw not in markers["frameworks"]:
                        markers["frameworks"].append(fw)
                        if fw == "cobra":
                            markers["cli_markers"].append("cobra")
                            markers["has_cli_entry"] = True
        except Exception:
            pass

    # Direct entry point inspection
    for entry in ("cli.py", "main.py", "app.py", "cmd", "bin", "cli", "src/cli.py", "src/main.py"):
        p = os.path.join(root_dir, entry)
        if os.path.exists(p):
            markers["entry_points"].append(entry)
            if "cli" in entry:
                markers["has_cli_entry"] = True

    markers["has_prisma_schema"] = (
        os.path.isfile(os.path.join(root_dir, "backend", "prisma", "schema.prisma")) or
        os.path.isfile(os.path.join(root_dir, "prisma", "schema.prisma"))
    )
    markers["has_nest_cli"] = (
        os.path.isfile(os.path.join(root_dir, "backend", "nest-cli.json")) or
        os.path.isfile(os.path.join(root_dir, "nest-cli.json"))
    )
    markers["has_next_config"] = any(
        os.path.isfile(os.path.join(root_dir, "frontend", f)) or os.path.isfile(os.path.join(root_dir, f))
        for f in ("next.config.js", "next.config.mjs", "next.config.ts")
    )
    markers["has_docker"] = any(
        os.path.isfile(os.path.join(root_dir, f))
        for f in ("Dockerfile", "docker-compose.yml", "backend/Dockerfile", "frontend/Dockerfile")
    )
    markers["has_ci_workflows"] = (
        os.path.isdir(os.path.join(root_dir, ".github", "workflows")) or
        os.path.isdir(os.path.join(root_dir, "frontend", ".github", "workflows"))
    )

    markers["languages"] = sorted(markers["languages"])
    return markers


def detect_repository_archetype(root_dir: str) -> str:
    """
    Analyzes repository markers and structure to determine its architectural archetype.
    """
    markers = _collect_framework_markers(os.path.abspath(root_dir))
    frameworks = set(markers.get("frameworks", []))
    cli_markers = set(markers.get("cli_markers", []))

    # 1. CLI Application
    if markers.get("has_cli_entry") or cli_markers:
        # If it has frontend and backend separate web apps, it could be a monorepo with CLI
        has_frontend_dir = os.path.isdir(os.path.join(root_dir, "frontend")) or markers.get("has_next_config")
        has_backend_dir = os.path.isdir(os.path.join(root_dir, "backend")) or markers.get("has_nest_cli")
        if not (has_frontend_dir and has_backend_dir):
            return ARCHETYPE_CLI

    # 2. Full-Stack Web Application
    has_ui = bool(frameworks & {"next", "react", "vue", "angular", "svelte"}) or markers.get("has_next_config") or os.path.isdir(os.path.join(root_dir, "frontend"))
    has_api = bool(frameworks & {"@nestjs/core", "express", "fastify", "fastapi", "django", "flask"}) or markers.get("has_nest_cli") or os.path.isdir(os.path.join(root_dir, "backend"))
    if has_ui and has_api:
        return ARCHETYPE_FULLSTACK

    # 3. Backend Service / API
    if has_api:
        return ARCHETYPE_BACKEND

    # 4. Data / ML Pipeline
    if bool(frameworks & {"torch", "tensorflow", "pandas", "numpy", "scikit-learn", "dbt", "airflow"}):
        return ARCHETYPE_DATA_ML

    # 5. Library / SDK
    if os.path.isfile(os.path.join(root_dir, "pyproject.toml")) or os.path.isfile(os.path.join(root_dir, "setup.py")) or os.path.isfile(os.path.join(root_dir, "Cargo.toml")):
        return ARCHETYPE_LIBRARY

    return ARCHETYPE_GENERIC


#: Sketches of how codebases *can* divide, to show the shape of an answer.
#:
#: Deliberately prose, not JSON: a copyable template gets copied. The point is
#: to convey that a layer is "a place where responsibility changes hands", then
#: get out of the way so the agent names what it actually found.
LAYER_SET_IDEAS = [
    "A web app might split presentation from request handling from domain logic "
    "from persistence, with background jobs and deployment config as their own tiers.",
    "A CLI tool might split the command surface from the processing engine from "
    "local state, with adapters to outside systems separate again.",
    "A library might split its public API from the core implementation from its "
    "data types, with backend adapters separate.",
    "A data pipeline might split ingestion from transformation from model "
    "training from serving.",
    "None of these will fit this repository. The useful question is not 'which "
    "one is it?' but 'where does responsibility change hands in THIS code, and "
    "what would a new engineer need named?'",
]


#: Prompt for a coding-agent CLI running headless inside the repository.
#:
#: The difference from the hosted-LLM prompt is the first instruction: the agent
#: has the repo on disk, so it is told to READ files rather than infer a layer
#: set from a directory listing. That is the entire reason this path exists.
AGENT_LAYERS_PROMPT = """You are designing the architectural layer map for the repository you currently have open.

Do this before answering:
1. Read the entry points and a representative sample of real source files across
   the main directories. The evidence bundle below lists candidates; it is a
   starting point, NOT a substitute for opening the files.
2. Work out what this codebase actually does and how responsibility is split.
   Name the layers after THIS repository's concepts, not a generic template.
   For the shape of an answer only:
{ideas}
3. Derive matching rules from real paths and real symbol names you saw.

Then return ONLY a JSON object, no prose and no markdown fence, of this shape:

{{
  "utility_id": "<id of the catch-all layer>",
  "layers": [
    {{
      "id": "short_machine_id",
      "name": "Layer 1: Human Friendly Name",
      "order": 1,
      "description": "One sentence on what lives here",
      "rules": [
        {{"file_contains": ["substring", "another"], "exclude_file": ["optional"]}},
        {{"label_contains": ["SymbolNamePart"]}}
      ]
    }}
  ]
}}

Hard requirements:
- 3 to 6 layers plus exactly one catch-all utility layer.
- Every layer needs a unique `id`, a unique `name`, and a sequential integer
  `order` starting at 1.
- Exactly one layer's `id` equals `utility_id`, and that layer has `rules: []`.
- Supported rule keys: file_contains, exclude_file, path_regex, label_contains,
  exclude_label, label_ends_with, type_in, id_prefix. Each value is a list of strings.
- Rules must match paths that really exist here. A rule that matches nothing is
  worse than no rule, and a rule that matches everything collapses the map.

Repository root: {root}

Evidence bundle (sampled automatically, verify against the real files):
{evidence}
"""


def build_agent_layer_prompt(root: str, evidence: Dict[str, Any]) -> str:
    """Renders the headless-agent prompt for a layer proposal."""
    return AGENT_LAYERS_PROMPT.format(
        root=root,
        ideas="\n".join(f"     - {idea}" for idea in LAYER_SET_IDEAS),
        evidence=json.dumps(evidence, indent=2, default=str),
    )


#: How many of the busiest files, and symbols within them, to put in front of the
#: agent. Enough to show the shape of the codebase, small enough to stay cheap.
_EVIDENCE_TOP_FILES = 40
_EVIDENCE_SYMBOLS_PER_FILE = 8


def _extracted_symbol_evidence(root_dir: str) -> Dict[str, Any]:
    """
    Real symbols per file, taken from graphify's raw AST export.

    Filenames alone under-describe a codebase: two repos with identical
    directory listings can be doing entirely different things. ``init`` runs
    extraction *before* asking for layers precisely so this is available, and
    files are ranked by symbol count so the busiest ones lead.

    Returns ``{}`` when nothing has been extracted yet -- the caller degrades to
    filename evidence rather than failing.
    """
    graph_path = paths.graphify_graph_path(os.path.abspath(root_dir))
    if not os.path.isfile(graph_path):
        return {}

    try:
        with open(graph_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}

    by_file: Dict[str, List[str]] = {}
    for node in raw.get("nodes", []):
        if not isinstance(node, dict):
            continue
        # graphify also emits `rationale` nodes whose label is a sentence of
        # docstring prose. Those are ~a third of the export and would fill the
        # evidence with English instead of the symbol names the rules match on.
        if node.get("file_type") != "code":
            continue
        src = node.get("source_file") or node.get("file") or node.get("path")
        label = node.get("label") or node.get("id")
        if not src or not label:
            continue
        by_file.setdefault(str(src), []).append(str(label))

    if not by_file:
        return {}

    busiest = sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return {
        "total_files": len(by_file),
        "total_symbols": sum(len(v) for v in by_file.values()),
        "symbols_by_file": {
            path: sorted(set(labels))[:_EVIDENCE_SYMBOLS_PER_FILE]
            for path, labels in busiest[:_EVIDENCE_TOP_FILES]
        },
    }


def collect_layer_evidence(root_dir: str = ".") -> Dict[str, Any]:
    """The sampled repository evidence handed to an agent or LLM."""
    root = os.path.abspath(root_dir)
    evidence: Dict[str, Any] = {
        "framework_markers": _collect_framework_markers(root),
        "sampled_directory_clusters": _sample_repo_files(root),
        "detected_archetype": detect_repository_archetype(root),
    }
    extracted = _extracted_symbol_evidence(root)
    if extracted:
        evidence["extracted_symbols"] = extracted
    return evidence


def propose_layers_with_agent(
    root_dir: str = ".",
    agent: Optional[Any] = None,
    model: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Asks a headless coding-agent CLI to read the repo and design its layer set.

    Returns ``(config, agent_name)`` on success, or ``(None, reason)`` where
    reason is a short human-readable string explaining why nothing came back.
    """
    if agent is None:
        agent = agent_runner.find_agent_cli()
    if agent is None:
        status = agent_runner.agent_status()
        return None, str(status.get("detail") or status.get("reason") or "no agent available")

    root = os.path.abspath(root_dir)
    prompt = build_agent_layer_prompt(root, collect_layer_evidence(root))

    try:
        proposal = agent_runner.run_agent_json(agent, prompt, root, model=model)
    except agent_runner.AgentError as err:
        return None, str(err)

    if isinstance(proposal, dict) and "layers" not in proposal:
        for key in ("proposal", "layer_set", "config"):
            nested = proposal.get(key)
            if isinstance(nested, dict) and "layers" in nested:
                proposal = nested
                break

    if not isinstance(proposal, dict) or "layers" not in proposal:
        return None, f"{agent.display} did not return a layer set"

    try:
        validate_layer_config(proposal)
    except ValueError as err:
        return None, f"{agent.display} returned an invalid layer set: {err}"

    return proposal, agent.name


def propose_layers_with_llm(root_dir: str = ".", enricher: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """
    Collects repository evidence and asks the LLM to synthesize a tailored layer definition.
    Returns validated configuration dictionary if successful, or None.
    """
    if enricher is None:
        try:
            from .llm_enricher import LLMEnricher
            enricher = LLMEnricher()
        except Exception:
            return None

    root = os.path.abspath(root_dir)
    evidence = collect_layer_evidence(root)

    try:
        proposal = enricher.propose_layers(evidence)
        if proposal and isinstance(proposal, dict):
            # Normalize wrapper keys if model wrapped it
            for key in ("proposal", "layer_set", "config"):
                if isinstance(proposal.get(key), dict) and "layers" in proposal[key]:
                    proposal = proposal[key]
                    break
            validate_layer_config(proposal)
            return proposal
    except Exception:
        pass

    return None


#: Returned as the ``source`` when no layer set could be obtained. The caller
#: turns this into a NEXT ACTION for the agent instead of inventing layers.
NEEDS_LAYERS = "needs_layers"


def auto_configure_layers(
    root_dir: str = ".",
    enricher: Optional[Any] = None,
    force: bool = False,
    use_llm: bool = True,
    use_agent: bool = False,
    agent: Optional[Any] = None,
    agent_model: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> Tuple[Optional[LayerRegistry], Optional[str], str]:
    """
    Resolves this repository's architectural layers, or reports that it cannot.

    In order:

    1. An existing configuration, unless ``force``.
    2. A headless coding-agent CLI -- **opt-in** (``use_agent``), because every
       agent has different flags, auth and headless semantics and some ship no
       working CLI at all. The file handshake is the path that generalises.
    3. A hosted LLM, if an API key is configured. Sees the evidence bundle only.

    If none of those produces a layer set, this returns ``(None, None,
    NEEDS_LAYERS)``. It never falls back to a template: layers that were not
    derived from this repository are worse than no layers, because they are
    wrong in a way that looks right.

    ``notes`` collects human-readable explanations of every path tried and
    skipped, so the caller can tell the user what happened.

    Returns ``(LayerRegistry | None, saved_config_path | None, source)``.
    """
    root = os.path.abspath(root_dir)
    existing_path = config_path(root)
    log = notes if notes is not None else []

    if existing_path and not force:
        reg, _ = load_layer_config(root)
        return reg, existing_path, "existing_config"

    # 1. Coding agent CLI, opt-in.
    if use_agent:
        if agent is None:
            agent = agent_runner.find_agent_cli()
        proposal, detail = propose_layers_with_agent(root, agent=agent, model=agent_model)
        if proposal:
            registry = LayerRegistry.from_records(
                proposal["layers"], utility_id=str(proposal["utility_id"])
            )
            out_path = save_layer_config(root, registry)
            return registry, out_path, f"agent:{detail}"
        if detail:
            log.append(f"Agent CLI layer proposal unavailable: {detail}")

    # 2. Hosted LLM, evidence-only.
    if use_llm:
        proposal = propose_layers_with_llm(root, enricher=enricher)
        if proposal:
            utility_id = str(proposal["utility_id"])
            registry = LayerRegistry.from_records(proposal["layers"], utility_id=utility_id)
            out_path = save_layer_config(root, registry)
            return registry, out_path, "llm_synthesis"

    return None, None, NEEDS_LAYERS


def generate_propose_request(root_dir: str = ".") -> str:
    """
    Samples repo evidence and writes .tldrgraph/propose_layers_request.json for the agent.

    Returns the absolute path of the generated request file.
    """
    root = os.path.abspath(root_dir)
    state_dir = os.path.join(root, ".tldrgraph")
    os.makedirs(state_dir, exist_ok=True)
    req_path = os.path.join(state_dir, REQUEST_FILENAME)

    evidence = collect_layer_evidence(root)
    existing = config_path(root)
    if existing:
        evidence["active_layers"] = [layer.as_record() for layer in get_registry().ordered()]
        evidence["active_utility_id"] = get_registry().utility_id

    payload = {
        "schema": "codechakra/propose-layers-request@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": os.path.join(".tldrgraph", RESPONSE_FILENAME),
        "config_target": os.path.join(".tldrgraph", CONFIG_FILENAME_YAML),
        "instructions": [
            "Analyze the repository evidence and propose a dynamic architectural layer set tailored to this codebase.",
            "Each layer must have: id (unique machine string), name (display string), order (1..N), description, and rules.",
            "Rules support: file_contains, exclude_file, path_regex, label_contains, exclude_label, label_ends_with, type_in, id_prefix.",
            "Must designate a utility_id matching one of the proposed layer ids (the catch-all bucket, with empty rules).",
            "Name the layers after THIS repository's concepts. TLDRGraph ships no layer templates and none will be applied for you.",
            "A rule that matches nothing is worse than no rule; a rule that matches everything collapses the map.",
            "See 'layer_set_ideas' below for the SHAPE of an answer. They are sketches from other codebases, not a menu -- none of them fits this repository.",
            f"Write the JSON or YAML response to .tldrgraph/{RESPONSE_FILENAME}, then run `tldrgraph init`.",
        ],
        "layer_set_ideas": LAYER_SET_IDEAS,
        "evidence": evidence,
    }

    with open(req_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return req_path


def apply_proposed_layers(root_dir: str = ".", response_file: Optional[str] = None) -> str:
    """
    Reads the agent's proposed layer response, validates it, and writes layers.config.yaml.

    Returns the path to the written layers.config.yaml.
    """
    root = os.path.abspath(root_dir)
    if not response_file:
        response_file = os.path.join(root, ".tldrgraph", RESPONSE_FILENAME)

    if not os.path.isfile(response_file):
        raise ValueError(
            f"No proposal response file found at {response_file}. Run `tldrgraph propose-layers` first."
        )

    try:
        with open(response_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if response_file.endswith(".json") or content.startswith("{"):
                data = json.loads(content)
            else:
                data = yaml.safe_load(content)
    except Exception as err:
        raise ValueError(f"Could not parse layer proposal from {response_file}: {err}") from err

    # Handle wrapper object if present
    if isinstance(data, dict) and "layers" not in data:
        for key in ("proposal", "layer_set", "config"):
            if isinstance(data.get(key), dict) and "layers" in data[key]:
                data = data[key]
                break

    validate_layer_config(data)
    utility_id = str(data["utility_id"])
    registry = LayerRegistry.from_records(data["layers"], utility_id=utility_id)
    out_path = save_layer_config(root, registry)
    return out_path
