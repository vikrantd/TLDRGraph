"""
Propose Layers: Dynamic Multi-Layer Discovery & Configuration Generator.

Samples repository evidence (directory tree, framework markers, sample paths,
dependencies, and entry points) and determines the optimal architectural layer set:
1. Via LLM (Gemini, OpenAI, Ollama) if available or through the coding agent loop.
2. Via intelligent repository archetype detection (CLI app, Library, Full-stack Web,
   Backend API, Data/ML Pipeline, Generic Modular) as an offline, zero-token fallback.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

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
        ".git", "node_modules", "dist", ".next", "__pycache__", ".codechakra",
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


def archetype_layer_set(archetype: str, evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Returns a customized, multi-layer architectural definition tailored to the given archetype.
    """
    if archetype == ARCHETYPE_CLI:
        return {
            "utility_id": "utility",
            "layers": [
                {
                    "id": "cli",
                    "name": "Layer 1: CLI & Commands",
                    "order": 1,
                    "description": "Command line interface, argument parsing, options, and commands",
                    "rules": [
                        {"file_contains": ["cli.py", "/cli/", "commands/", "cmd/", "bin/"]},
                        {"file_contains": ["cli"], "exclude_file": ["flow_engine", "vector_store", "graph_loader", "classifier"]},
                        {"label_contains": ["cli_command", "cli_main", "main_cli", "subcommand"]}
                    ]
                },
                {
                    "id": "engine",
                    "name": "Layer 2: Core Flow Engine & Logic",
                    "order": 2,
                    "description": "Core algorithms, flow traversal, analysis, classification, and extractors",
                    "rules": [
                        {"file_contains": ["flow_engine", "classifier", "extractors", "deadcode", "propose_layers", "hierarchy", "engine"]},
                        {"label_contains": ["engine", "flow", "classify", "extract", "traverse", "walk", "rule"]}
                    ]
                },
                {
                    "id": "storage",
                    "name": "Layer 3: Graph Loader, Storage & Index",
                    "order": 3,
                    "description": "Graph ingestion, SQLite caching, hash gating, vector store, and configuration",
                    "rules": [
                        {"file_contains": ["graph_loader", "hash_gate", "vector_store", "layer_config", "layers", "rules"]},
                        {"label_contains": ["loader", "graph", "store", "cache", "gate", "config", "registry"]}
                    ]
                },
                {
                    "id": "integrations",
                    "name": "Layer 4: Agent Loop & Visualizer",
                    "order": 4,
                    "description": "Host-agent rules installer, LLM enrichment providers, and HTML visualizer",
                    "rules": [
                        {"file_contains": ["installer", "llm_enricher", "visualizer"]},
                        {"label_contains": ["installer", "visualizer", "enricher", "agent"]}
                    ]
                },
                {
                    "id": "utility",
                    "name": "General / Utility",
                    "order": 5,
                    "description": "Shared utility helpers, formatting, and common routines",
                    "rules": []
                }
            ]
        }

    if archetype == ARCHETYPE_FULLSTACK:
        return {
            "utility_id": "utility",
            "layers": [
                {
                    "id": "ui",
                    "name": "Layer 1: Presentation & UI",
                    "order": 1,
                    "description": "User interface components, pages, views, and forms",
                    "rules": [
                        {"file_contains": ["frontend/", "src/app", "src/components", "pages/"]},
                        {"label_contains": ["View", "Page", "Component", "Button", "Form"]}
                    ]
                },
                {
                    "id": "api",
                    "name": "Layer 2: API Gateway & Routing",
                    "order": 2,
                    "description": "Controllers, endpoints, route handlers, and guards",
                    "rules": [
                        {"file_contains": ["controller", "route", "api/"]},
                        {"label_contains": ["Controller", "Route", "Endpoint", "Guard"]}
                    ]
                },
                {
                    "id": "service",
                    "name": "Layer 3: Domain & Business Services",
                    "order": 3,
                    "description": "Business logic, workflows, calculation engines, and service layer",
                    "rules": [
                        {"file_contains": ["service", "calc", "workflow", "domain/"]},
                        {"label_contains": ["Service", "Calculator", "Workflow", "Manager"]}
                    ]
                },
                {
                    "id": "data",
                    "name": "Layer 4: Data & Persistence",
                    "order": 4,
                    "description": "Database models, repositories, schemas, and entities",
                    "rules": [
                        {"file_contains": ["prisma", "repository", "entities", "schema.prisma", "database"]},
                        {"label_contains": ["Repository", "Entity", "Model", "Prisma"]}
                    ]
                },
                {
                    "id": "async",
                    "name": "Layer 5: Async & Background Tasks",
                    "order": 5,
                    "description": "Cron jobs, queue workers, polling, and scheduled tasks",
                    "rules": [
                        {"file_contains": ["cron", "queue", "worker", "polling", "tasks"]},
                        {"label_contains": ["Job", "Worker", "Cron", "Polling"]}
                    ]
                },
                {
                    "id": "devops",
                    "name": "Layer 6: DevOps & Infrastructure",
                    "order": 6,
                    "description": "Docker, Kubernetes, CI/CD pipelines, and cloud deployment configs",
                    "rules": [
                        {"file_contains": ["docker", "k8s", "helm", ".github/workflows", "Dockerfile"]}
                    ]
                },
                {
                    "id": "utility",
                    "name": "General / Utility",
                    "order": 7,
                    "description": "Shared helpers and catch-all",
                    "rules": []
                }
            ]
        }

    if archetype == ARCHETYPE_BACKEND:
        return {
            "utility_id": "utility",
            "layers": [
                {
                    "id": "api",
                    "name": "Layer 1: API & Handlers",
                    "order": 1,
                    "description": "HTTP/gRPC endpoints, routing, controllers, and middleware",
                    "rules": [
                        {"file_contains": ["controller", "router", "endpoint", "handler", "api/"]},
                        {"label_contains": ["Controller", "Handler", "Router", "Endpoint"]}
                    ]
                },
                {
                    "id": "service",
                    "name": "Layer 2: Domain Services",
                    "order": 2,
                    "description": "Core business logic, domain calculations, and application services",
                    "rules": [
                        {"file_contains": ["service", "domain", "logic", "usecase"]},
                        {"label_contains": ["Service", "Logic", "Manager", "Workflow"]}
                    ]
                },
                {
                    "id": "data",
                    "name": "Layer 3: Persistence & Repositories",
                    "order": 3,
                    "description": "Database models, schemas, repositories, and persistence layer",
                    "rules": [
                        {"file_contains": ["repository", "model", "entity", "schema", "db"]},
                        {"label_contains": ["Repository", "Entity", "Model", "Table"]}
                    ]
                },
                {
                    "id": "async",
                    "name": "Layer 4: Async & Worker Tasks",
                    "order": 4,
                    "description": "Background queues, event consumers, and cron schedules",
                    "rules": [
                        {"file_contains": ["worker", "task", "job", "queue", "consumer"]},
                        {"label_contains": ["Worker", "Consumer", "Job", "Queue"]}
                    ]
                },
                {
                    "id": "utility",
                    "name": "General / Utility",
                    "order": 5,
                    "description": "Shared helpers and cross-cutting utilities",
                    "rules": []
                }
            ]
        }

    if archetype == ARCHETYPE_LIBRARY:
        return {
            "utility_id": "utility",
            "layers": [
                {
                    "id": "public_api",
                    "name": "Layer 1: Public API & Interfaces",
                    "order": 1,
                    "description": "Entry points, public facade, client classes, and exported functions",
                    "rules": [
                        {"file_contains": ["__init__.py", "api", "client", "index.ts", "public/"]},
                        {"label_contains": ["Client", "API", "Facade", "Interface"]}
                    ]
                },
                {
                    "id": "core_engine",
                    "name": "Layer 2: Core Processing & Engine",
                    "order": 2,
                    "description": "Core algorithms, parsing, processing, and computational logic",
                    "rules": [
                        {"file_contains": ["core", "engine", "processor", "parser", "builder"]},
                        {"label_contains": ["Engine", "Processor", "Parser", "Builder", "Transformer"]}
                    ]
                },
                {
                    "id": "types_models",
                    "name": "Layer 3: Types & Models",
                    "order": 3,
                    "description": "Data structures, types, interfaces, schemas, and entities",
                    "rules": [
                        {"file_contains": ["types", "models", "schema", "interfaces"]},
                        {"label_contains": ["Type", "Model", "Schema", "Config"]}
                    ]
                },
                {
                    "id": "adapters",
                    "name": "Layer 4: Adapters & Backends",
                    "order": 4,
                    "description": "Backend adapters, transports, network connectors, and storage drivers",
                    "rules": [
                        {"file_contains": ["adapter", "transport", "driver", "connector", "backend"]},
                        {"label_contains": ["Adapter", "Transport", "Driver", "Connector"]}
                    ]
                },
                {
                    "id": "utility",
                    "name": "General / Utility",
                    "order": 5,
                    "description": "Internal utilities and helpers",
                    "rules": []
                }
            ]
        }

    # Generic Modular Fallback
    return {
        "utility_id": "utility",
        "layers": [
            {
                "id": "interface",
                "name": "Layer 1: Entry Points & Interface",
                "order": 1,
                "description": "Entry points, CLI, HTTP routing, or user interfaces",
                "rules": [
                    {"file_contains": ["main", "app", "cli", "entry", "interface"]},
                    {"label_contains": ["main", "app", "cli", "Controller", "View"]}
                ]
            },
            {
                "id": "domain",
                "name": "Layer 2: Core Domain Logic",
                "order": 2,
                "description": "Business logic, algorithms, calculation, and core operations",
                "rules": [
                    {"file_contains": ["service", "domain", "core", "logic", "engine"]},
                    {"label_contains": ["Service", "Logic", "Engine", "Manager"]}
                ]
            },
            {
                "id": "storage",
                "name": "Layer 3: Storage & Persistence",
                "order": 3,
                "description": "Data storage, repositories, models, cache, and state",
                "rules": [
                    {"file_contains": ["data", "db", "storage", "repository", "model", "cache"]},
                    {"label_contains": ["Repository", "Model", "Store", "Cache"]}
                ]
            },
            {
                "id": "utility",
                "name": "General / Utility",
                "order": 4,
                "description": "Shared helpers and catch-all utilities",
                "rules": []
            }
        ]
    }


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
    evidence = {
        "framework_markers": _collect_framework_markers(root),
        "sampled_directory_clusters": _sample_repo_files(root),
        "detected_archetype": detect_repository_archetype(root),
    }

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


def auto_configure_layers(
    root_dir: str = ".",
    enricher: Optional[Any] = None,
    force: bool = False,
    use_llm: bool = True
) -> Tuple[LayerRegistry, str, str]:
    """
    Ensures .codechakra/layers.config.yaml exists by:
    1. Loading existing configuration if present and not force.
    2. Prompting LLM if use_llm is True and LLM is configured.
    3. Auto-detecting archetype and generating a tailored archetype layer set.

    Returns (LayerRegistry, saved_config_path, source_description).
    """
    root = os.path.abspath(root_dir)
    existing_path = config_path(root)

    if existing_path and not force:
        reg, _ = load_layer_config(root)
        return reg, existing_path, "existing_config"

    # Try LLM proposal
    if use_llm:
        proposal = propose_layers_with_llm(root, enricher=enricher)
        if proposal:
            utility_id = str(proposal["utility_id"])
            registry = LayerRegistry.from_records(proposal["layers"], utility_id=utility_id)
            out_path = save_layer_config(root, registry)
            return registry, out_path, "llm_synthesis"

    # Fallback to smart archetype detection
    archetype = detect_repository_archetype(root)
    layer_data = archetype_layer_set(archetype)
    validate_layer_config(layer_data)
    utility_id = str(layer_data["utility_id"])
    registry = LayerRegistry.from_records(layer_data["layers"], utility_id=utility_id)
    out_path = save_layer_config(root, registry)
    return registry, out_path, f"archetype:{archetype}"


def generate_propose_request(root_dir: str = ".") -> str:
    """
    Samples repo evidence and writes .codechakra/propose_layers_request.json for the agent.

    Returns the absolute path of the generated request file.
    """
    root = os.path.abspath(root_dir)
    state_dir = os.path.join(root, ".codechakra")
    os.makedirs(state_dir, exist_ok=True)
    req_path = os.path.join(state_dir, REQUEST_FILENAME)

    archetype = detect_repository_archetype(root)
    archetype_example = archetype_layer_set(archetype)

    evidence = {
        "detected_archetype": archetype,
        "framework_markers": _collect_framework_markers(root),
        "sampled_directory_clusters": _sample_repo_files(root),
        "active_layers": [layer.as_record() for layer in get_registry().ordered()],
        "active_utility_id": get_registry().utility_id,
    }

    payload = {
        "schema": "codechakra/propose-layers-request@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "response_file": os.path.join(".codechakra", RESPONSE_FILENAME),
        "config_target": os.path.join(".codechakra", CONFIG_FILENAME_YAML),
        "instructions": [
            "Analyze the repository evidence and propose a dynamic architectural layer set tailored to this codebase.",
            "Each layer must have: id (unique machine string), name (display string), order (1..N), description, and rules.",
            "Rules support: file_contains, exclude_file, path_regex, label_contains, exclude_label, label_ends_with, type_in, id_prefix.",
            "Must designate a utility_id matching one of the proposed layer ids (the fallback catch-all bucket).",
            f"Write the JSON or YAML response to .codechakra/{RESPONSE_FILENAME}, then run `codechakra apply-layers`.",
        ],
        "suggested_archetype_layers": archetype_example,
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
        response_file = os.path.join(root, ".codechakra", RESPONSE_FILENAME)

    if not os.path.isfile(response_file):
        raise ValueError(
            f"No proposal response file found at {response_file}. Run `codechakra propose-layers` first."
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
