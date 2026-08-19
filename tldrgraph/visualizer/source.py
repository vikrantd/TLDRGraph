"""
Source code extraction for the visualizer.

The graph records only where a symbol *starts* (``source_location`` is a bare
``"L88"``), so to show real code on the canvas we have to work out where the
symbol ends ourselves. Two strategies cover almost everything:

- indentation languages (Python and friends): the body runs until a non-blank
  line dedents back to the header's own indentation
- brace languages (JS/TS, Java, Go, C-likes): count braces from the header
  until they balance

Anything we cannot classify falls back to a fixed window of lines. Every slice
is capped so one enormous function cannot bloat the standalone HTML.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

#: Hard ceilings, per symbol, on what gets inlined into the HTML payload.
MAX_CODE_LINES = 80
MAX_CODE_CHARS = 6000

#: Lines to show when the language is unknown and we cannot detect a block end.
FALLBACK_WINDOW = 24

BRACE_EXTENSIONS = {
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "java", "go", "c", "h", "cc", "cpp",
    "hpp", "cs", "kt", "kts", "swift", "scala", "rs", "php", "dart", "groovy",
}

INDENT_EXTENSIONS = {"py", "pyi", "rb", "yaml", "yml", "coffee"}

#: Extension -> highlighter hint the canvas app uses for tokenizing.
LANGUAGE_BY_EXTENSION = {
    "py": "python", "pyi": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "java": "java", "go": "go", "rs": "rust", "rb": "ruby", "php": "php",
    "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cs": "csharp",
    "kt": "kotlin", "kts": "kotlin", "swift": "swift", "scala": "scala",
    "sql": "sql", "sh": "shell", "bash": "shell",
    "yaml": "yaml", "yml": "yaml", "json": "json", "toml": "toml", "md": "markdown",
}


def parse_line_number(source_location: Any) -> Optional[int]:
    """Reads graphify's ``"L88"`` (and a few looser spellings) as an int."""
    if source_location is None:
        return None
    text = str(source_location).strip()
    if not text:
        return None
    if text[0] in "Ll":
        text = text[1:]
    text = text.split("-")[0].split(":")[0].strip()
    try:
        return int(text)
    except ValueError:
        return None


def _extension(path: str) -> str:
    return os.path.splitext(path)[1].lstrip(".").lower()


def language_for(path: str) -> str:
    return LANGUAGE_BY_EXTENSION.get(_extension(path), "plain")


#: Keywords that introduce a declaration in the languages we care about.
DECLARATION_KEYWORDS = (
    "def", "class", "function", "func", "fn", "type", "interface", "struct",
    "enum", "impl", "trait", "module", "export", "public", "private",
    "protected", "internal", "static", "const", "let", "var", "abstract",
    "final", "async", "sub", "package",
)

_DECLARATION_RE = re.compile(
    r"^\s*(?:@\w|(?:" + "|".join(DECLARATION_KEYWORDS) + r")\b)"
)


def symbol_name(label: str, display_label: str = "") -> str:
    """
    Reduces a graph label to the bare identifier to look for in source.

    ``".load_or_extract()"`` and ``"GraphLoader.load_or_extract()"`` both become
    ``"load_or_extract"``.
    """
    name = (label or display_label or "").strip()
    name = name.split("(")[0].strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name.strip()


def _declares(line: str, name: str) -> bool:
    """True when ``line`` plausibly declares ``name`` (rather than calling it)."""
    if not name:
        return False
    if not re.search(r"\b" + re.escape(name) + r"\b", line):
        return False
    if _DECLARATION_RE.match(line):
        return True
    # Assignment or annotated member: ``name = ...`` / ``name: Type``.
    return bool(re.match(r"^\s*" + re.escape(name) + r"\s*[:=]", line))


def _find_declaration(lines: List[str], name: str) -> Optional[int]:
    """Scans for the best declaration line of ``name``; returns a 1-based line."""
    if not name:
        return None

    strong = re.compile(
        r"^\s*(?:async\s+)?(?:def|class|function|func|fn|interface|struct|type|enum)\s+"
        + re.escape(name) + r"\b"
    )
    for idx, line in enumerate(lines):
        if strong.match(line):
            return idx + 1

    for idx, line in enumerate(lines):
        if _declares(line, name):
            return idx + 1
    return None


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _find_end_by_indent(lines: List[str], start_idx: int) -> int:
    """Returns the exclusive end index of an indentation-delimited block."""
    header_indent = _indent_of(lines[start_idx])

    # Multi-line signatures: keep consuming until the header line stops being
    # an obvious continuation (open bracket or trailing comma / operator).
    idx = start_idx
    depth = 0
    while idx < len(lines):
        depth += lines[idx].count("(") + lines[idx].count("[") + lines[idx].count("{")
        depth -= lines[idx].count(")") + lines[idx].count("]") + lines[idx].count("}")
        if depth <= 0:
            break
        idx += 1

    body_start = idx + 1
    end = body_start
    for i in range(body_start, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _indent_of(lines[i]) <= header_indent:
            return end
        end = i + 1
    return end


def _find_end_by_braces(lines: List[str], start_idx: int) -> int:
    """Returns the exclusive end index of a brace-delimited block."""
    depth = 0
    opened = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
        if opened and depth <= 0:
            return i + 1
        # A one-line declaration with no body at all (interface member, field).
        if not opened and i > start_idx and lines[i].strip().endswith(";"):
            return i + 1
    return min(len(lines), start_idx + FALLBACK_WINDOW)


def _leading_context(lines: List[str], start_idx: int) -> int:
    """Walks back over decorators and an attached comment block."""
    idx = start_idx
    while idx > 0:
        prev = lines[idx - 1].strip()
        if prev.startswith("@") or prev.startswith("#") or prev.startswith("//"):
            idx -= 1
            continue
        break
    return idx


class SourceIndex:
    """Reads and caches project files, and slices symbol bodies out of them."""

    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self._cache: Dict[str, Optional[List[str]]] = {}

    def _lines(self, rel_path: str) -> Optional[List[str]]:
        if rel_path in self._cache:
            return self._cache[rel_path]

        abs_path = os.path.join(self.root_dir, rel_path)
        lines: Optional[List[str]] = None
        try:
            if os.path.isfile(abs_path) and os.path.getsize(abs_path) <= 2_000_000:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().split("\n")
        except OSError:
            lines = None

        self._cache[rel_path] = lines
        return lines

    def locate_symbol(
        self,
        rel_path: str,
        source_location: Any,
        name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Returns ``{start, end, relocated, language}`` for the symbol, or ``None``
        when it cannot be resolved. No source text is returned: the visualizer
        loads file content live, and only needs the line range up front.

        A snapshot can outlive the edits made since it was built, so a recorded
        line is trusted only when the source there still declares the symbol.
        Otherwise we re-find the declaration by name; pointing at the wrong
        function would be worse than pointing at none.
        """
        if not rel_path or rel_path in ("root", "project root"):
            return None

        lines = self._lines(rel_path)
        if not lines:
            return None

        recorded = parse_line_number(source_location)
        start_line: Optional[int] = None
        relocated = False

        if recorded and 1 <= recorded <= len(lines):
            if not name or _declares(lines[recorded - 1], name):
                start_line = recorded

        if start_line is None:
            found = _find_declaration(lines, name)
            if found is None:
                return None
            start_line = found
            relocated = bool(recorded) and found != recorded

        start_idx = _leading_context(lines, start_line - 1)
        ext = _extension(rel_path)

        if ext in BRACE_EXTENSIONS:
            end_idx = _find_end_by_braces(lines, start_line - 1)
        elif ext in INDENT_EXTENSIONS:
            end_idx = _find_end_by_indent(lines, start_line - 1)
        else:
            end_idx = _find_end_by_indent(lines, start_line - 1)
            if end_idx <= start_line:
                end_idx = min(len(lines), start_line - 1 + FALLBACK_WINDOW)

        end_idx = max(end_idx, start_line)

        # Trailing blank lines carry no information; drop them.
        while end_idx > start_idx and not lines[end_idx - 1].strip():
            end_idx -= 1
        if end_idx <= start_idx:
            return None

        return {
            "start": start_idx + 1,
            "end": end_idx,
            "relocated": relocated,
            "language": language_for(rel_path),
        }

    def slice_symbol(
        self,
        rel_path: str,
        source_location: Any,
        name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        :meth:`locate_symbol` plus the source text itself, capped so one huge
        symbol cannot run away. Used by tooling that wants the code inline.
        """
        found = self.locate_symbol(rel_path, source_location, name)
        if found is None:
            return None

        lines = self._lines(rel_path) or []
        start_idx = found["start"] - 1
        end_idx = found["end"]
        truncated = False

        if end_idx - start_idx > MAX_CODE_LINES:
            end_idx = start_idx + MAX_CODE_LINES
            truncated = True

        code = "\n".join(lines[start_idx:end_idx])
        if len(code) > MAX_CODE_CHARS:
            code = code[:MAX_CODE_CHARS]
            truncated = True

        result = dict(found)
        result["code"] = code
        result["truncated"] = truncated
        return result
