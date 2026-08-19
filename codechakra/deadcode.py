"""
Reachability cascade for CodeChakra: which nodes are genuinely unreferenced?

The cascade is: deterministic links (HTTP route seam, Prisma model seam) ->
LLM/agent inferred links -> and only if *still* nothing points at a node does it
become a deletion candidate.

The hard part is not finding nodes with no inbound edges -- that is one line.
The hard part is not lying about what that means. Measured on the AIParas repo,
87 nodes have ``in_degree == 0`` and almost none of them are dead: roughly 60 are
structural entry points (Next.js page/layout files, NestJS ``main.ts`` and
modules, Prisma seeds) and most of the remainder are tool-invoked configuration
(``next.config.js``, ``eslint.config.mjs``, ``prisma.config.ts``,
``.githooks/pre-commit``). Nothing imports those and nothing ever will; they are
invoked by a framework, a bundler, or CI.

So two guards are built in:

* a large table of framework / tooling entry-point conventions, and
* an ``enrichment_coverage`` floor. Below it, an unreferenced node is reported
  as ``"unreviewed"``, never ``"candidate"``. Absence of evidence is not
  evidence of absence: if the LLM/agent pass never ran, timed out, had no API
  key, or produced only template boilerplate, the graph simply is not complete
  enough for silence to mean anything.

Nothing in this module deletes anything, and no status here means deletion is
safe on its own.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .layers import get_registry, layer_id_of

__all__ = [
    "CANDIDATE_COVERAGE_FLOOR",
    "STATUS_LIVE",
    "STATUS_ENTRY_POINT",
    "STATUS_CANDIDATE",
    "STATUS_UNREVIEWED",
    "compute_enrichment_coverage",
    "entry_point_reason",
    "classify_dead_code",
]

#: Minimum genuine enrichment coverage before an unreferenced node may be
#: called a deletion candidate rather than merely unreviewed.
CANDIDATE_COVERAGE_FLOOR = 0.8

STATUS_LIVE = "live"
STATUS_ENTRY_POINT = "entry_point"
STATUS_CANDIDATE = "candidate"
STATUS_UNREVIEWED = "unreviewed"
#: Not reviewable source at all -- an external package reference or a non-code
#: artifact graphify emitted (a comment/rationale node). Keeping these out of
#: "unreviewed" stops them from padding a human review list.
STATUS_NOT_CODE = "not_code"

#: Enrichment produced by the offline template heuristic. It never read the
#: source, so it must not count towards coverage.
HEURISTIC_ENRICHMENT_SOURCE = "heuristic"

#: Relation that marks a symbol as re-exported from a barrel module, i.e. part
#: of a package's public API. graphify emits it directly.
REEXPORT_RELATION = "re_exports"

#: graphify `file_type` values that are not executable source. A prose rationale
#: or concept node has nothing to delete.
NON_CODE_NODE_TYPES = {"rationale", "concept", "doc", "documentation"}

#: Next.js file conventions: the router invokes these by filename.
_NEXT_CONVENTIONS = {
    "page", "layout", "route", "middleware", "head", "error", "loading",
    "not-found", "template", "default", "global-error", "instrumentation",
    "sitemap", "robots", "manifest", "opengraph-image", "icon", "apple-icon",
}

#: Config files a CLI reads by name. None of them is ever imported.
_TOOL_MANIFESTS = {
    "nest-cli.json", "components.json", "vercel.json", "turbo.json",
    "jsconfig.json", "nodemon.json", "angular.json", "now.json",
    "renovate.json", "lerna.json", "manifest.json",
}

_SCRIPT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_CONFIG_EXTS = (".js", ".ts", ".mjs", ".cjs", ".json")


def _normalize(path: str) -> str:
    return (path or "").replace(os.sep, "/").strip()


def compute_enrichment_coverage(graph) -> float:
    """
    Fraction of non-utility nodes carrying genuine enrichment.

    "Utility" is the registry's utility bucket, identified by its stable id --
    renaming its display string must not change which nodes are counted.

    Genuine means a non-empty ``intent`` that did NOT come from the offline
    template heuristic. The heuristic derives its text from a node's layer and
    label alone -- it has not read a single line of source -- so counting it
    would let generated boilerplate unlock deletion candidates.
    """
    utility_id = get_registry().utility_id
    considered = 0
    enriched = 0

    for _, data in graph.nodes(data=True):
        if layer_id_of(data) == utility_id:
            continue
        considered += 1
        if not (data.get("intent") or "").strip():
            continue
        if (data.get("enrichment_source") or "") == HEURISTIC_ENRICHMENT_SOURCE:
            continue
        enriched += 1

    if not considered:
        return 0.0
    return enriched / considered


def _is_config_file(basename: str) -> bool:
    stem, ext = os.path.splitext(basename)
    if ext not in _CONFIG_EXTS:
        return False
    return stem.endswith(".config") or stem.endswith("rc") and "." in basename[:-len(ext)]


def entry_point_reason(file_path: str, node_data: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """
    Return a human-readable reason if the file is invoked by a framework, a
    build tool, or CI rather than by an import. ``None`` means "not obviously
    an entry point".
    """
    path = _normalize(file_path)
    if not path:
        return None

    lower = path.lower()
    basename = os.path.basename(path)
    lower_base = basename.lower()
    stem, ext = os.path.splitext(lower_base)

    # --- hand-run data scripts living beside a Prisma schema ----------------
    # e.g. backend/prisma/import-ack-requests.ts -- executed manually via
    # ts-node/tsx, never imported, so absence of inbound edges says nothing.
    parent = os.path.basename(os.path.dirname(lower))
    if (parent == "prisma" and ext in (".ts", ".js", ".mjs", ".cjs")
            and stem != "seed" and "migration" not in lower):
        return "tool-invoked: hand-run script beside the Prisma schema"

    # --- CI, git hooks and other dotfile-driven tooling ---------------------
    if lower.startswith(".github/") or "/.github/" in lower:
        return "tool-invoked: GitHub Actions / CI configuration"
    if lower.startswith(".githooks/") or "/.githooks/" in lower:
        return "tool-invoked: git hook"
    if lower_base.startswith(".") and "." not in lower_base[1:]:
        return "tool-invoked: dotfile configuration"
    if lower_base.startswith(".") and ext in (".yml", ".yaml", ".json", ".js", ".mjs", ".cjs", ".ts", ""):
        return "tool-invoked: dotfile configuration"

    # --- Containers and declarative infra ----------------------------------
    if lower_base.startswith("dockerfile") or lower_base == "docker-compose.yml" \
            or lower_base.endswith(".dockerfile"):
        return "tool-invoked: container build definition"
    if ext in (".yml", ".yaml"):
        return "tool-invoked: declarative YAML config read by external tooling"

    # --- Tests --------------------------------------------------------------
    if ".test." in lower_base or ".spec." in lower_base \
            or lower_base.startswith("test_") or lower_base.endswith("_test.py"):
        return "test-runner invoked: test/spec file"
    if any(part in lower for part in ("/__tests__/", "/__mocks__/", "/e2e/", "/tests/", "test/")):
        if ext in _SCRIPT_EXTS or ext == ".py":
            return "test-runner invoked: lives in a test directory"

    # --- Standalone scripts -------------------------------------------------
    # scripts/ holds things npm scripts, CI and git hooks execute directly.
    # Nothing imports them, so in_degree is 0 by construction.
    if lower.startswith("scripts/") or "/scripts/" in lower:
        return "tool-invoked: lives in scripts/ (executed directly, never imported)"

    # --- Shell scripts ------------------------------------------------------
    if ext in (".sh", ".bash", ".zsh", ".ps1"):
        return "shell-invoked: script executed directly, never imported"

    # --- Package manifests --------------------------------------------------
    if lower_base in ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        return "tool-invoked: package manifest read by the package manager"
    if lower_base.startswith("tsconfig") and ext == ".json":
        return "tool-invoked: TypeScript compiler configuration"
    if lower_base in _TOOL_MANIFESTS:
        return f"tool-invoked: {basename} is read by its CLI, never imported"

    # --- Build / tool configuration ----------------------------------------
    if _is_config_file(lower_base):
        return "tool-invoked: build or tooling configuration file"

    # --- Prisma migrations and seeds ---------------------------------------
    if "prisma/migrations/" in lower or "/migrations/" in lower:
        return "tool-invoked: Prisma migration applied by the CLI"
    if "prisma/seed" in lower or stem == "seed" or stem.startswith("seed-") or stem.startswith("seed."):
        return "tool-invoked: Prisma seed script run via the CLI"

    # --- NestJS -------------------------------------------------------------
    if stem == "main" and ext in _SCRIPT_EXTS:
        return "framework entry: application bootstrap (main.ts)"
    if lower_base.endswith(".module.ts") or lower_base.endswith(".module.js"):
        return "framework-routed: NestJS module instantiated by the DI container"
    if lower_base.endswith(".controller.ts") or lower_base.endswith(".controller.js"):
        return "framework-routed: NestJS controller mounted by the HTTP router"

    # --- Next.js file conventions ------------------------------------------
    if ext in _SCRIPT_EXTS and stem in _NEXT_CONVENTIONS:
        return f"framework-routed: Next.js file convention ({basename})"

    return None


def _has_reexport_edge(graph, node_id) -> bool:
    for _, _, data in graph.in_edges(node_id, data=True):
        if data.get("relation") == REEXPORT_RELATION:
            return True
    for _, _, data in graph.out_edges(node_id, data=True):
        if data.get("relation") == REEXPORT_RELATION:
            return True
    return False


def _inbound_summary(graph, node_id) -> str:
    relations = sorted({
        str(data.get("relation") or "calls")
        for _, _, data in graph.in_edges(node_id, data=True)
    })
    count = graph.in_degree(node_id)
    shown = ", ".join(relations[:3])
    if len(relations) > 3:
        shown += ", ..."
    return f"{count} inbound edge{'s' if count != 1 else ''} ({shown})"


def _looks_external(file_path: str, root_dir: Optional[str]) -> bool:
    """
    True when the recorded `file` is a bare module specifier such as ``next`` or
    ``tailwindcss`` rather than a path to a file in this repository.
    """
    path = _normalize(file_path)
    if not path:
        return False
    if root_dir:
        return not os.path.isfile(os.path.join(root_dir, path))
    # No root to check against: a bare name with no separator and no extension
    # is a package specifier, not a path.
    return "/" not in path and not os.path.splitext(path)[1]


def classify_dead_code(graph, enrichment_coverage: float,
                       root_dir: Optional[str] = None) -> None:
    """
    Set ``dead_code_status`` and ``dead_code_reason`` on every node in ``graph``.

    Must run last, after AST edges, deterministic seam edges and any LLM/agent
    bridges are all present -- an edge added afterwards would silently invalidate
    every status computed here.

    ``enrichment_coverage`` is the fraction of non-utility nodes carrying genuine
    (non-heuristic) enrichment; see :func:`compute_enrichment_coverage`. Below
    :data:`CANDIDATE_COVERAGE_FLOOR` nothing is ever promoted past
    ``"unreviewed"``.
    """
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
            continue

        node_type = str(data.get("type") or "").lower()
        if node_type in NON_CODE_NODE_TYPES:
            data["dead_code_status"] = STATUS_NOT_CODE
            data["dead_code_reason"] = (
                f"not source code: graphify '{node_type}' node (prose, not a symbol)"
            )
            continue

        reason = entry_point_reason(data.get("file") or "", data)
        if reason:
            data["dead_code_status"] = STATUS_ENTRY_POINT
            data["dead_code_reason"] = reason
            continue

        if _has_reexport_edge(graph, node_id):
            data["dead_code_status"] = STATUS_ENTRY_POINT
            data["dead_code_reason"] = (
                "public API: re-exported from a barrel module (re_exports relation)"
            )
            continue

        file_path = (data.get("file") or "").strip()
        if file_path and _looks_external(file_path, root_dir):
            data["dead_code_status"] = STATUS_NOT_CODE
            data["dead_code_reason"] = (
                f"not source code: '{file_path}' does not resolve to a file in this "
                "repository (external package or unresolved module reference)"
            )
            continue

        # No recorded file means there is nothing to inspect and nothing to
        # delete -- typically an imported external symbol. Never a candidate.
        if not (data.get("file") or "").strip():
            data["dead_code_status"] = STATUS_UNREVIEWED
            data["dead_code_reason"] = (
                "no inbound edges, but no source file recorded for this node; "
                "nothing local to review"
            )
            continue

        # graphify materializes bare npm package names ("next", "tailwindcss")
        # as pseudo-nodes whose source_file is the package name. A repo-relative
        # path to real source always has an extension, so anything without one
        # is not a file anybody could delete.
        file_path = (data.get("file") or "").strip()
        if not os.path.splitext(os.path.basename(file_path))[1]:
            data["dead_code_status"] = STATUS_UNREVIEWED
            data["dead_code_reason"] = (
                f"no inbound edges, but {file_path!r} is not a resolvable source file "
                "(external package or synthetic node); nothing local to review"
            )
            continue

        if coverage >= CANDIDATE_COVERAGE_FLOOR:
            data["dead_code_status"] = STATUS_CANDIDATE
            data["dead_code_reason"] = (
                f"no inbound edges; not a framework entry point; {coverage_note} "
                f"(at or above the {round(CANDIDATE_COVERAGE_FLOOR * 100)}% bar) "
                "-- verify by hand before deleting"
            )
        else:
            data["dead_code_status"] = STATUS_UNREVIEWED
            data["dead_code_reason"] = (
                f"no inbound edges; not a framework entry point; but only {coverage_note} "
                f"(below the {round(CANDIDATE_COVERAGE_FLOOR * 100)}% bar) "
                "-- the graph is not complete enough to call this dead"
            )
