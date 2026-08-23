"""
Canonical on-disk locations for everything TLDRGraph writes.

One state directory, ``.tldrgraph/``, holds all of it -- including graphify's
raw AST export, which used to live in a second top-level ``graphify-out/``
folder. Scanning a repository therefore adds one directory, not two.

graphify's export is renamed on the way in (``graphify_graph.json``) because
``graph.json`` inside ``.tldrgraph/`` is already taken by TLDRGraph's *enriched*
snapshot, which is a different artifact with a different schema.

This module deliberately imports nothing from the package so that every other
module can depend on it without creating a cycle.
"""

from __future__ import annotations

import os

#: The single state directory, relative to the repository root.
STATE_DIRNAME = ".tldrgraph"

#: TLDRGraph's own enriched graph snapshot.
SNAPSHOT_FILENAME = "graph.json"

#: graphify's raw AST export and file manifest, inside the state directory.
GRAPHIFY_GRAPH_FILENAME = "graphify_graph.json"
GRAPHIFY_MANIFEST_FILENAME = "graphify_manifest.json"

#: Pre-consolidation location. Never read and never written any more -- kept so
#: `scan` can point out that a leftover folder is now dead weight.
LEGACY_GRAPHIFY_DIRNAME = "graphify-out"

#: Where graphify keeps its own AST cache, relative to the scanned root.
#: Forward slash on purpose: graphify does ``Path(root) / Path(value)``, which
#: splits this correctly on every platform, and a backslash would not.
GRAPHIFY_WORK_SUBDIR = f"{STATE_DIRNAME}/graphify"

#: graphify's own override for where it writes, read once when it is imported.
GRAPHIFY_OUT_ENV = "GRAPHIFY_OUT"


def pin_graphify_output_dir() -> None:
    """
    Points graphify's cache inside our state directory, before it is imported.

    graphify defaults to a top-level ``graphify-out/`` and reads
    ``$GRAPHIFY_OUT`` once at import time, so relocating it has to happen this
    early -- otherwise scanning a repository leaves two directories behind no
    matter where TLDRGraph puts its own files.

    An explicit ``$GRAPHIFY_OUT`` from the environment always wins: that is the
    user pointing graphify somewhere on purpose. This only ever changes the
    current process, so a separate ``graphify`` CLI run is unaffected.
    """
    os.environ.setdefault(GRAPHIFY_OUT_ENV, GRAPHIFY_WORK_SUBDIR)


pin_graphify_output_dir()


def state_dir(root_dir: str) -> str:
    """The .tldrgraph directory for ``root_dir``."""
    return os.path.join(root_dir, STATE_DIRNAME)


def state_path(root_dir: str, filename: str) -> str:
    """A file inside the state directory."""
    return os.path.join(root_dir, STATE_DIRNAME, filename)


def snapshot_path(root_dir: str) -> str:
    """TLDRGraph's enriched graph snapshot."""
    return state_path(root_dir, SNAPSHOT_FILENAME)


def graphify_graph_path(root_dir: str) -> str:
    """graphify's raw AST export."""
    return state_path(root_dir, GRAPHIFY_GRAPH_FILENAME)


def graphify_manifest_path(root_dir: str) -> str:
    """graphify's file manifest, source of the semantic hashes the gate uses."""
    return state_path(root_dir, GRAPHIFY_MANIFEST_FILENAME)


def legacy_graphify_dir(root_dir: str) -> str:
    """The obsolete graphify-out/ directory, for cleanup hints only."""
    return os.path.join(root_dir, LEGACY_GRAPHIFY_DIRNAME)
