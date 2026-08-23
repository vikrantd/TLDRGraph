"""
Frontend HTTP and API call extraction for TLDRGraph.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "out", "coverage",
    ".turbo", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
    "graphify-out", ".tldrgraph", ".idea", ".vscode",
}

_SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_CLIENT_IDENTS = r"(?:api|apiClient|apiInstance|axios|axiosInstance|http|httpClient|client|request)"

_API_CALL_RE = re.compile(
    r"(?<![\w.])" + _CLIENT_IDENTS + r"\s*\.\s*"
    r"(?P<method>get|post|put|patch|delete|del|head|options)\s*"
    r"(?:<[^<>;{}]*>\s*)?"
    r"\(\s*"
    r"(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote)"
)

_FETCH_CALL_RE = re.compile(
    r"(?<![\w.])fetch\s*\(\s*(?P<quote>['\"`])(?P<path>[^'\"`\n]*)(?P=quote)"
)

_FETCH_METHOD_RE = re.compile(r"method\s*:\s*['\"`](?P<method>[A-Za-z]+)['\"`]")


def normalize_http_method(method: str) -> str:
    method = (method or "").strip().lower()
    return "delete" if method == "del" else method


def _line_of(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _is_route_literal(path: str) -> bool:
    stripped = path.strip()
    if not stripped:
        return False
    return stripped.startswith("/") or stripped.startswith("${") or "://" in stripped


def iter_source_files(
    root_dir: str,
    extensions: Sequence[str] = _SOURCE_EXTS,
    filenames: Sequence[str] = (),
) -> Iterator[Tuple[str, str]]:
    """Yield (repo_relative_path, content) for every readable source file."""
    visited: set = set()
    for current_root, dirnames, files in os.walk(root_dir, followlinks=True):
        real_root = os.path.realpath(current_root)
        if real_root in visited:
            dirnames[:] = []
            continue
        visited.add(real_root)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if filenames:
                if name not in filenames:
                    continue
            elif not name.endswith(tuple(extensions)):
                continue
            absolute = os.path.join(current_root, name)
            relative = os.path.relpath(absolute, root_dir)
            try:
                with open(absolute, "r", encoding="utf-8", errors="ignore") as handle:
                    yield relative, handle.read()
            except OSError:
                continue


def extract_frontend_calls(file_path: str, content: str) -> List[Dict[str, Any]]:
    """Frontend HTTP call sites with a literal (or template-literal) route."""
    from .extractors_route import normalize_route_path

    calls: List[Dict[str, Any]] = []
    for match in _API_CALL_RE.finditer(content):
        raw_path = match.group("path")
        if not _is_route_literal(raw_path):
            continue
        calls.append({
            "file": file_path,
            "line": _line_of(content, match.start()),
            "method": normalize_http_method(match.group("method")),
            "raw_path": raw_path,
            "path": normalize_route_path(raw_path),
            "kind": "api_client",
        })

    for match in _FETCH_CALL_RE.finditer(content):
        raw_path = match.group("path")
        if not _is_route_literal(raw_path):
            continue
        window = content[match.end():match.end() + 300]
        method_match = _FETCH_METHOD_RE.search(window)
        method = normalize_http_method(method_match.group("method")) if method_match else "get"
        calls.append({
            "file": file_path,
            "line": _line_of(content, match.start()),
            "method": method,
            "raw_path": raw_path,
            "path": normalize_route_path(raw_path),
            "kind": "fetch",
        })

    return calls


def collect_frontend_calls(root_dir: str) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for relative, content in iter_source_files(root_dir):
        if "api." not in content and "fetch(" not in content:
            continue
        calls.extend(extract_frontend_calls(relative.replace(os.sep, "/"), content))
    return calls
