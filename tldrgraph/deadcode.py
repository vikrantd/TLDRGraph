"""
Dead-code review-candidate classifier for TLDRGraph.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Sequence

STATUS_CANDIDATE = "candidate"
STATUS_UNREVIEWED = "unreviewed"
STATUS_LIVE = "live"
STATUS_ENTRY_POINT = "entry_point"
STATUS_NOT_CODE = "not_code"

CANDIDATE_COVERAGE_FLOOR = 0.60
HEURISTIC_ENRICHMENT_SOURCE = "heuristic"
REEXPORT_RELATION = "re_exports"

NON_CODE_NODE_TYPES = {
    "rationale",
    "concept",
    "doc",
    "documentation",
    "markdown",
    "text",
}

_CONFIG_EXTS = {".js", ".cjs", ".mjs", ".ts", ".json", ".yaml", ".yml"}
_SCRIPT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_TOOL_MANIFESTS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum",
    "pyproject.toml", "poetry.lock", "requirements.txt",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
}
_NEXT_CONVENTIONS = {
    "page", "layout", "loading", "error", "not-found",
    "route", "template", "default", "middleware",
}


def _normalize(path: Optional[str]) -> str:
    if not path:
        return ""
    return path.replace("\\", "/").strip("/")


def compute_enrichment_coverage(graph, excluded_layer_ids: Sequence[str] = ("utility",)) -> float:
    excluded = set(excluded_layer_ids)
    considered = 0
    enriched = 0
    for _, data in graph.nodes(data=True):
        lid = data.get("layer_id")
        layer_name = data.get("layer", "")
        if lid in excluded or (not lid and (layer_name in {"General / Utility", "Utility"} or "utility" in layer_name.lower())):
            continue
        if str(data.get("type") or "").lower() in NON_CODE_NODE_TYPES:
            continue
        considered += 1
        source = data.get("enrichment_source") or ""
        if source and source != HEURISTIC_ENRICHMENT_SOURCE:
            enriched += 1

    return enriched / considered if considered else 0.0


def _is_config_file(basename: str) -> bool:
    stem, ext = os.path.splitext(basename)
    if ext not in _CONFIG_EXTS:
        return False
    return stem.endswith(".config") or (stem.endswith("rc") and "." in basename[:-len(ext)])


def _check_prisma_reason(lower: str, parent: str, stem: str, ext: str) -> Optional[str]:
    if parent == "prisma" and ext in (".ts", ".js", ".mjs", ".cjs") and stem != "seed" and "migration" not in lower:
        return "tool-invoked: hand-run script beside the Prisma schema"
    if "prisma/migrations/" in lower or "/migrations/" in lower:
        return "tool-invoked: Prisma migration applied by the CLI"
    if "prisma/seed" in lower or stem == "seed" or stem.startswith("seed-") or stem.startswith("seed."):
        return "tool-invoked: Prisma seed script run via the CLI"
    return None


def _check_infra_and_dotfiles(lower: str, lower_base: str, ext: str) -> Optional[str]:
    if lower.startswith(".github/") or "/.github/" in lower:
        return "tool-invoked: GitHub Actions / CI configuration"
    if lower.startswith(".githooks/") or "/.githooks/" in lower:
        return "tool-invoked: git hook"
    if lower_base.startswith(".") and ("." not in lower_base[1:] or ext in (".yml", ".yaml", ".json", ".js", ".mjs", ".cjs", ".ts", "")):
        return "tool-invoked: dotfile configuration"
    if lower_base.startswith("dockerfile") or lower_base == "docker-compose.yml" or lower_base.endswith(".dockerfile"):
        return "tool-invoked: container build definition"
    if ext in (".yml", ".yaml"):
        return "tool-invoked: declarative YAML config read by external tooling"
    return None


def _check_tests_and_scripts(lower: str, lower_base: str, ext: str) -> Optional[str]:
    if ".test." in lower_base or ".spec." in lower_base or lower_base.startswith("test_") or lower_base.endswith("_test.py"):
        return "test-runner invoked: test/spec file"
    if any(part in lower for part in ("/__tests__/", "/__mocks__/", "/e2e/", "/tests/", "test/")) and (ext in _SCRIPT_EXTS or ext == ".py"):
        return "test-runner invoked: lives in a test directory"
    if lower.startswith("scripts/") or "/scripts/" in lower:
        return "tool-invoked: lives in scripts/ (executed directly, never imported)"
    if ext in (".sh", ".bash", ".zsh", ".ps1"):
        return "shell-invoked: script executed directly, never imported"
    return None


def _check_manifests_and_frameworks(basename: str, lower_base: str, stem: str, ext: str) -> Optional[str]:
    if lower_base in ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        return "tool-invoked: package manifest read by the package manager"
    if lower_base.startswith("tsconfig") and ext == ".json":
        return "tool-invoked: TypeScript compiler configuration"
    if lower_base in _TOOL_MANIFESTS:
        return f"tool-invoked: {basename} is read by its CLI, never imported"
    if _is_config_file(lower_base):
        return "tool-invoked: build or tooling configuration file"
    if stem == "main" and ext in _SCRIPT_EXTS:
        return "framework entry: application bootstrap (main.ts)"
    if lower_base.endswith((".module.ts", ".module.js")):
        return "framework-routed: NestJS module instantiated by the DI container"
    if lower_base.endswith((".controller.ts", ".controller.js")):
        return "framework-routed: NestJS controller mounted by the HTTP router"
    if ext in _SCRIPT_EXTS and stem in _NEXT_CONVENTIONS:
        return f"framework-routed: Next.js file convention ({basename})"
    return None


def entry_point_reason(file_path: str, node_data: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    path = _normalize(file_path)
    if not path:
        return None

    lower = path.lower()
    basename = os.path.basename(path)
    lower_base = basename.lower()
    stem, ext = os.path.splitext(lower_base)
    parent = os.path.basename(os.path.dirname(lower))

    return (
        _check_prisma_reason(lower, parent, stem, ext)
        or _check_infra_and_dotfiles(lower, lower_base, ext)
        or _check_tests_and_scripts(lower, lower_base, ext)
        or _check_manifests_and_frameworks(basename, lower_base, stem, ext)
    )


def _has_reexport_edge(graph: Any, node_id: str) -> bool:
    for _, _, data in graph.in_edges(node_id, data=True):
        if data.get("relation") == REEXPORT_RELATION:
            return True
    for _, _, data in graph.out_edges(node_id, data=True):
        if data.get("relation") == REEXPORT_RELATION:
            return True
    return False


def _inbound_summary(graph: Any, node_id: str) -> str:
    relations = sorted({str(data.get("relation") or "calls") for _, _, data in graph.in_edges(node_id, data=True)})
    count = graph.in_degree(node_id)
    shown = ", ".join(relations[:3]) + (", ..." if len(relations) > 3 else "")
    return f"{count} inbound edge{'s' if count != 1 else ''} ({shown})"


def _looks_external(file_path: str, root_dir: Optional[str]) -> bool:
    path = _normalize(file_path)
    if not path:
        return False
    if root_dir:
        return not os.path.isfile(os.path.join(root_dir, path))
    return "/" not in path and not os.path.splitext(path)[1]


def _classify_unconnected_node(
    data: Dict[str, Any],
    node_id: str,
    graph: Any,
    coverage: float,
    coverage_note: str,
    root_dir: Optional[str],
) -> None:
    node_type = str(data.get("type") or "").lower()
    if node_type in NON_CODE_NODE_TYPES:
        data["dead_code_status"] = STATUS_NOT_CODE
        data["dead_code_reason"] = f"not source code: graphify '{node_type}' node (prose, not a symbol)"
        return

    reason = entry_point_reason(data.get("file") or "", data)
    if reason:
        data["dead_code_status"] = STATUS_ENTRY_POINT
        data["dead_code_reason"] = reason
        return

    if _has_reexport_edge(graph, node_id):
        data["dead_code_status"] = STATUS_ENTRY_POINT
        data["dead_code_reason"] = "public API: re-exported from a barrel module (re_exports relation)"
        return

    file_path = (data.get("file") or "").strip()
    if file_path and _looks_external(file_path, root_dir):
        data["dead_code_status"] = STATUS_NOT_CODE
        data["dead_code_reason"] = f"not source code: '{file_path}' does not resolve to a file in this repository (external package or unresolved module reference)"
        return

    if not file_path:
        data["dead_code_status"] = STATUS_UNREVIEWED
        data["dead_code_reason"] = "no inbound edges, but no source file recorded for this node; nothing local to review"
        return

    if not os.path.splitext(os.path.basename(file_path))[1]:
        data["dead_code_status"] = STATUS_UNREVIEWED
        data["dead_code_reason"] = f"no inbound edges, but {file_path!r} is not a resolvable source file (external package or synthetic node); nothing local to review"
        return

    if coverage >= CANDIDATE_COVERAGE_FLOOR:
        data["dead_code_status"] = STATUS_CANDIDATE
        data["dead_code_reason"] = f"no inbound edges; not a framework entry point; {coverage_note} (at or above the {round(CANDIDATE_COVERAGE_FLOOR * 100)}% bar) -- verify by hand before deleting"
    else:
        data["dead_code_status"] = STATUS_UNREVIEWED
        data["dead_code_reason"] = f"no inbound edges; not a framework entry point; but only {coverage_note} (below the {round(CANDIDATE_COVERAGE_FLOOR * 100)}% bar) -- the graph is not complete enough to call this dead"


def classify_dead_code(graph: Any, enrichment_coverage: float, root_dir: Optional[str] = None) -> None:
    try:
        coverage = float(enrichment_coverage)
    except (TypeError, ValueError):
        coverage = 0.0
    coverage = min(max(coverage, 0.0), 1.0)
    coverage_note = f"{round(coverage * 100)}% enrichment coverage"

    for node_id, data in graph.nodes(data=True):
        if graph.in_degree(node_id) > 0:
            data["dead_code_status"] = STATUS_LIVE
            data["dead_code_reason"] = f"referenced: {_inbound_summary(graph, node_id)}"
        else:
            _classify_unconnected_node(data, node_id, graph, coverage, coverage_note, root_dir)
