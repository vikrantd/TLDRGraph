"""
Control-flow extraction for Python sources.

Reads a symbol's body with the standard library parser and threads it into the
shared process model. Other languages go through ``bpmn_treesitter``; the two
front ends produce the same shapes, so the renderer and the phrasing layer never
need to know which language a workflow step came from.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional, Tuple

from .bpmn_externals import external_for
from .bpmn_process import ProcessBuilder, short
from .bpmn_treesitter import extract_treesitter_process, spec_for

# Statements that only shuffle values around carry no flow meaning of their own;
# their calls are still picked up, they simply do not become elements themselves.
def _unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:                                  # pragma: no cover - odd AST
        return ""


def _dotted(func: ast.AST) -> str:
    """The call as written - ``os.path.isfile``, ``data.get`` - where possible."""
    parts: List[str] = []
    node: Any = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(_dotted(node.func).split(".")[-1])
    return ".".join(reversed(parts))


def _call_names(node: ast.AST) -> Tuple[List[str], List[str]]:
    """Callee names inside a subtree, bare and qualified, in source order."""
    names: List[str] = []
    qualified: List[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if not name or name in names:
            continue
        names.append(name)
        qualified.append(_dotted(func) or name)
    return names, qualified


def _condition_label(test: ast.AST) -> str:
    """Renders a branch test as a question, without inventing meaning."""
    return short(_unparse(test))


class _PythonBuilder(ProcessBuilder):
    """Walks a Python function body and threads it into the shared model."""

    # -- statement walking ---------------------------------------------------

    def walk(self, body: List[ast.stmt], incoming: List[str]) -> List[str]:
        """Returns the open ends left after threading ``body``."""
        for stmt in body:
            incoming = self.statement(stmt, incoming)
        return incoming

    def statement(self, stmt: ast.stmt, incoming: List[str]) -> List[str]:
        if isinstance(stmt, ast.If):
            return self._branch(stmt, incoming)
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            return self._loop(stmt, incoming)
        if isinstance(stmt, ast.Try):
            return self._try(stmt, incoming)
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            return self._with(stmt, incoming)
        if isinstance(stmt, ast.Return):
            return self._end(stmt, incoming)
        if isinstance(stmt, ast.Raise):
            return self._raise(stmt, incoming)
        if isinstance(stmt, (ast.Break, ast.Continue)):
            return incoming
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return incoming                              # nested definition, not flow
        return self._work(stmt, incoming)

    def _work(self, stmt: ast.stmt, incoming: List[str]) -> List[str]:
        calls, qualified = _call_names(stmt)
        if not calls:
            return incoming                              # pure data shuffling
        label = short(_unparse(stmt))
        el = self.add(
            "task",
            label,
            detail=label,
            line=getattr(stmt, "lineno", 0),
            calls=calls,
            external=external_for(qualified),
        )
        self.link(incoming, el.id)
        return [el.id]

    def _branch(self, stmt: ast.If, incoming: List[str]) -> List[str]:
        gate = self.add(
            "gateway",
            _condition_label(stmt.test),
            detail=_unparse(stmt.test),
            line=getattr(stmt, "lineno", 0),
            calls=_call_names(stmt.test)[0],
        )
        self.link(incoming, gate.id)

        yes_ends = self.walk(stmt.body, [gate.id])
        self.relabel_first(gate.id, yes_ends, "Yes")

        if stmt.orelse:
            no_ends = self.walk(stmt.orelse, [gate.id])
            self.relabel_first(gate.id, no_ends, "No")
        else:
            # No else branch: whatever follows the if is the "No" path, and it is
            # linked later, so remember the gateway and label that flow at the end.
            no_ends = [gate.id]
            self.implicit_no.append(gate.id)

        return [e for e in yes_ends + no_ends if e]

    def _loop(self, stmt: ast.stmt, incoming: List[str]) -> List[str]:
        if isinstance(stmt, ast.While):
            over = short(_unparse(stmt.test))
            label = f"while {over}"
        else:
            over = short(_unparse(getattr(stmt, "iter", None)))
            label = f"for each {over}"

        loop = self.add(
            "loop",
            label,
            detail=over,
            line=getattr(stmt, "lineno", 0),
            calls=_call_names(getattr(stmt, "iter", None) or getattr(stmt, "test", stmt))[0],
        )
        self.link(incoming, loop.id)

        inner_ends = self.walk(stmt.body, [loop.id])
        self.link(inner_ends, loop.id, label="repeat", kind="loop_back")

        ends = [loop.id]
        if getattr(stmt, "orelse", None):
            ends = self.walk(stmt.orelse, ends)
        return ends

    def _try(self, stmt: ast.Try, incoming: List[str]) -> List[str]:
        ends = self.walk(stmt.body, incoming)

        handler_ends: List[str] = []
        for handler in stmt.handlers:
            caught = _unparse(handler.type) or "any error"
            err = self.add(
                "error",
                short(f"on {caught}"),
                detail=caught,
                line=getattr(handler, "lineno", 0),
            )
            # An error can fire anywhere inside the protected block.
            self.link(ends or incoming, err.id, label="on error", kind="error")
            handler_ends.extend(self.walk(handler.body, [err.id]) or [err.id])

        if stmt.orelse:
            ends = self.walk(stmt.orelse, ends)
        if stmt.finalbody:
            ends = self.walk(stmt.finalbody, ends + handler_ends)
            handler_ends = []

        return ends + handler_ends

    def _with(self, stmt: ast.stmt, incoming: List[str]) -> List[str]:
        items = ", ".join(_unparse(i.context_expr) for i in getattr(stmt, "items", []))
        calls, qualified = _call_names(stmt) if items else ([], [])
        if items:
            el = self.add(
                "task",
                short(items),
                detail=items,
                line=getattr(stmt, "lineno", 0),
                calls=calls,
                external=external_for(qualified),
            )
            self.link(incoming, el.id)
            incoming = [el.id]
        return self.walk(stmt.body, incoming)

    def _end(self, stmt: ast.Return, incoming: List[str]) -> List[str]:
        value = _unparse(stmt.value)
        el = self.add(
            "end",
            short(f"return {value}") if value else "return",
            detail=value,
            line=getattr(stmt, "lineno", 0),
            calls=_call_names(stmt)[0] if stmt.value is not None else [],
        )
        self.link(incoming, el.id)
        return []

    def _raise(self, stmt: ast.Raise, incoming: List[str]) -> List[str]:
        value = _unparse(stmt.exc)
        el = self.add(
            "error",
            short(f"raise {value}") if value else "raise",
            detail=value,
            line=getattr(stmt, "lineno", 0),
        )
        self.link(incoming, el.id)
        return []


def _find_function(tree: ast.AST, symbol: str) -> Optional[ast.AST]:
    """Finds ``symbol``, accepting both ``name`` and ``Class.method`` forms."""
    wanted = symbol.replace("()", "").strip()
    owner, _, method = wanted.rpartition(".")

    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method:
            continue
        if not owner:
            return node
        best = best or node
    if best:
        return best

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == wanted:
            init = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
            return init[0] if init else None
    return None


def extract_python_process(
    root_dir: str,
    rel_path: str,
    symbol: str,
    prefix: str,
) -> Optional[Dict[str, Any]]:
    """Builds the BPMN element graph for one symbol's body.

    Returns ``None`` when the file is not Python or the symbol cannot be found;
    callers fall back to a single opaque activity in that case.
    """
    if not rel_path.endswith(".py"):
        return None

    abs_path = os.path.join(root_dir, rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return None

    func = _find_function(tree, symbol)
    if func is None:
        return None

    builder = _PythonBuilder(prefix)
    start = builder.add("start", "start", line=getattr(func, "lineno", 0))
    open_ends = builder.walk(func.body, [start.id])
    builder.finish(open_ends, getattr(func, "end_lineno", 0) or 0)
    return builder.as_process(symbol, rel_path, getattr(func, "lineno", 0))


def process_stats(process: Dict[str, Any]) -> Tuple[int, int]:
    """Element and flow counts, used by tests and the payload summary."""
    return len(process.get("elements", [])), len(process.get("flows", []))


def extract_process(
    root_dir: str,
    rel_path: str,
    symbol: str,
    prefix: str,
) -> Optional[Dict[str, Any]]:
    """Extracts a symbol's control flow, whatever language it is written in.

    Python goes through the standard library parser because it gives the most
    faithful labels; everything else goes through tree-sitter. Returns ``None``
    when no parser covers the file or the symbol cannot be found, and callers
    fall back to a single opaque activity rather than inventing structure.
    """
    if rel_path.endswith(".py"):
        return extract_python_process(root_dir, rel_path, symbol, prefix)
    if spec_for(rel_path) is not None:
        return extract_treesitter_process(root_dir, rel_path, symbol, prefix)
    return None
