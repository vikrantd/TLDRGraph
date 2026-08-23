"""
Evidence collection and archetype detection for architectural layer proposal.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Set

from . import paths

ARCHETYPE_CLI = "cli_application"
ARCHETYPE_FULLSTACK = "fullstack_web"
ARCHETYPE_BACKEND = "backend_service"
ARCHETYPE_LIBRARY = "library_sdk"
ARCHETYPE_DATA_ML = "data_ml_pipeline"
ARCHETYPE_GENERIC = "generic_modular"

IGNORE_DIRS = {
    ".git", "node_modules", "dist", ".next", "__pycache__", ".tldrgraph",
    "graphify-out", ".pytest_cache", ".venv", "venv", "build", "egg-info",
    ".egg-info"
}

CODE_EXTENSIONS = (
    ".ts", ".tsx", ".js", ".jsx", ".py", ".prisma", ".yaml", ".yml",
    ".json", ".go", ".rs", ".java", ".cpp", ".c", ".h", ".rb", ".php"
)

_EVIDENCE_TOP_FILES = 25
_EVIDENCE_SYMBOLS_PER_FILE = 12


def sample_repo_files(root_dir: str, max_per_dir: int = 10) -> Dict[str, List[str]]:
    """Samples representative file paths grouped by directory."""
    clusters: Dict[str, List[str]] = {}
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS and not d.startswith(".") and not d.endswith(".egg-info")
        ]
        rel_dir = os.path.relpath(root, root_dir)
        if rel_dir == ".":
            rel_dir = "root"

        code_files = [f for f in files if not f.startswith(".") and f.endswith(CODE_EXTENSIONS)]
        if code_files:
            clusters[rel_dir] = sorted(code_files)[:max_per_dir]
    return clusters


def _inspect_node_packages(root_dir: str, markers: Dict[str, Any]) -> None:
    pkg_paths = [
        os.path.join(root_dir, "package.json"),
        os.path.join(root_dir, "frontend", "package.json"),
        os.path.join(root_dir, "backend", "package.json"),
    ]
    for p in pkg_paths:
        if not os.path.isfile(p):
            continue
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


def _inspect_python_configs(root_dir: str, markers: Dict[str, Any]) -> None:
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


def _inspect_repo_flags(root_dir: str, markers: Dict[str, Any]) -> None:
    markers["has_prisma_schema"] = os.path.isfile(os.path.join(root_dir, "prisma", "schema.prisma"))
    markers["has_nest_cli"] = os.path.isfile(os.path.join(root_dir, "nest-cli.json"))
    markers["has_next_config"] = any(
        os.path.isfile(os.path.join(root_dir, f))
        for f in ("next.config.js", "next.config.mjs", "next.config.ts")
    )
    markers["has_docker"] = any(
        os.path.isfile(os.path.join(root_dir, f))
        for f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml")
    )
    ci_dir = os.path.join(root_dir, ".github", "workflows")
    markers["has_ci_workflows"] = os.path.isdir(ci_dir) and bool(os.listdir(ci_dir))


def collect_framework_markers(root_dir: str) -> Dict[str, Any]:
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
    _inspect_node_packages(root_dir, markers)
    _inspect_python_configs(root_dir, markers)
    _inspect_repo_flags(root_dir, markers)
    markers["languages"] = sorted(list(markers["languages"]))
    return markers


def _is_cli_archetype(markers: Dict[str, Any], cli_markers: Set[str], root_dir: str) -> bool:
    if not (markers.get("has_cli_entry") or cli_markers):
        return False
    has_frontend = os.path.isdir(os.path.join(root_dir, "frontend")) or markers.get("has_next_config")
    has_backend = os.path.isdir(os.path.join(root_dir, "backend")) or markers.get("has_nest_cli")
    return not (has_frontend and has_backend)


def _has_ui_markers(markers: Dict[str, Any], frameworks: Set[str], root_dir: str) -> bool:
    return (
        bool(frameworks & {"next", "react", "vue", "angular", "svelte"})
        or bool(markers.get("has_next_config"))
        or os.path.isdir(os.path.join(root_dir, "frontend"))
    )


def _has_api_markers(markers: Dict[str, Any], frameworks: Set[str], root_dir: str) -> bool:
    return (
        bool(frameworks & {"@nestjs/core", "express", "fastify", "fastapi", "django", "flask"})
        or bool(markers.get("has_nest_cli"))
        or os.path.isdir(os.path.join(root_dir, "backend"))
    )


def detect_repository_archetype(root_dir: str) -> str:
    """Analyzes repository markers and structure to determine archetype."""
    markers = collect_framework_markers(os.path.abspath(root_dir))
    frameworks = set(markers.get("frameworks", []))
    cli_markers = set(markers.get("cli_markers", []))

    if _is_cli_archetype(markers, cli_markers, root_dir):
        return ARCHETYPE_CLI

    has_ui = _has_ui_markers(markers, frameworks, root_dir)
    has_api = _has_api_markers(markers, frameworks, root_dir)

    if has_ui and has_api:
        return ARCHETYPE_FULLSTACK
    if has_api:
        return ARCHETYPE_BACKEND
    if bool(frameworks & {"torch", "tensorflow", "pandas", "numpy", "scikit-learn", "dbt", "airflow"}):
        return ARCHETYPE_DATA_ML
    if os.path.isfile(os.path.join(root_dir, "pyproject.toml")) or os.path.isfile(os.path.join(root_dir, "setup.py")) or os.path.isfile(os.path.join(root_dir, "Cargo.toml")):
        return ARCHETYPE_LIBRARY
    return ARCHETYPE_GENERIC


def extracted_symbol_evidence(root_dir: str) -> Dict[str, Any]:
    """Real symbols per file, taken from graphify raw AST export."""
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
        if not isinstance(node, dict) or node.get("file_type") != "code":
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
