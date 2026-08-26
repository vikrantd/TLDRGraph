"""
Control-flow extraction for every language that is not Python.

Uses the tree-sitter grammars that ship with the extraction toolchain, driven by
a small table per language family. Only the node names differ between grammars -
the shape of the work is identical - so one walker covers JavaScript, TypeScript,
Java, Go, Ruby, Rust and the rest, and adding a language means adding a row.

The output is the same process model the Python front end produces, so nothing
downstream knows or cares which parser ran.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .bpmn_externals import external_for
from .bpmn_languages import LanguageSpec, spec_for
from .bpmn_process import ProcessBuilder, short


_PARSER_CACHE: Dict[str, Any] = {}


def _parser(spec: LanguageSpec):
    """Builds (and remembers) the parser for a grammar, or ``None`` if absent."""
    if spec.name in _PARSER_CACHE:
        return _PARSER_CACHE[spec.name]

    parser = None
    try:
        from importlib import import_module

        from tree_sitter import Language, Parser

        grammar = import_module(spec.module)
        parser = Parser(Language(getattr(grammar, spec.entry)()))
    except Exception:                       # grammar not installed for this language
        parser = None

    _PARSER_CACHE[spec.name] = parser
    return parser


class _TreeSitterBuilder(ProcessBuilder):
    """Threads a tree-sitter subtree into the shared process model."""

    def __init__(self, prefix: str, spec: LanguageSpec, source: bytes):
        super().__init__(prefix)
        self.spec = spec
        self.source = source

    # -- text helpers --------------------------------------------------------

    def text(self, node) -> str:
        if node is None:
            return ""
        return self.source[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def line(self, node) -> int:
        return (node.start_point[0] + 1) if node is not None else 0

    @staticmethod
    def unwrap(text: str) -> str:
        """Drops the parentheses a grammar wraps a test in, but only those."""
        stripped = text.strip()
        while stripped.startswith("(") and stripped.endswith(")"):
            depth = 0
            for index, char in enumerate(stripped):
                depth += (char == "(") - (char == ")")
                if depth == 0 and index < len(stripped) - 1:
                    return stripped            # the parens are not one wrapper
            stripped = stripped[1:-1].strip()
        return stripped

    def condition_text(self, node) -> str:
        for fieldname in self.spec.condition_fields:
            child = node.child_by_field_name(fieldname)
            if child is not None:
                return short(self.unwrap(self.text(child)))
        # Some grammars leave the test as the first named child.
        for child in node.named_children:
            if child.type not in self.spec.blocks:
                return short(self.unwrap(self.text(child)))
        return ""

    def body_of(self, node):
        for fieldname in self.spec.body_fields:
            child = node.child_by_field_name(fieldname)
            if child is not None:
                return child
        for child in node.named_children:
            if child.type in self.spec.blocks:
                return child
        return None

    def statements(self, node) -> List[Any]:
        """The statements inside a block, or the node itself when it is one."""
        if node is None:
            return []
        if node.type in self.spec.blocks:
            return [c for c in node.named_children]
        return [node]

    def call_names(self, node) -> Tuple[List[str], List[str]]:
        bare: List[str] = []
        qualified: List[str] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current is not node and current.type in self.spec.functions:
                continue                       # a nested callback has its own flow
            if current.type in self.spec.calls:
                target = current.child_by_field_name("function") or \
                    current.child_by_field_name("constructor") or \
                    (current.named_children[0] if current.named_children else None)
                full = self.text(target).strip()
                if full:
                    name = full.split(".")[-1].split("(")[0].strip()
                    if name and name not in bare:
                        bare.append(name)
                        qualified.append(full.split("(")[0].strip())
            stack.extend(reversed(current.named_children))
        return bare, qualified

    # -- statement walking ---------------------------------------------------

    def walk(self, nodes: List[Any], incoming: List[str]) -> List[str]:
        for node in nodes:
            incoming = self.statement(node, incoming)
        return incoming

    def statement(self, node, incoming: List[str]) -> List[str]:
        kind = node.type
        spec = self.spec

        if kind in spec.branches:
            return self._branch(node, incoming)
        if kind in spec.loops:
            return self._loop(node, incoming)
        if kind in spec.tries:
            return self._try(node, incoming)
        if kind in spec.returns:
            return self._exit(node, incoming, "end")
        if kind in spec.throws:
            return self._exit(node, incoming, "error")
        if kind in spec.skips and kind not in spec.calls:
            return incoming                    # a nested definition is not this flow
        if kind in spec.blocks:
            return self.walk(self.statements(node), incoming)
        return self._work(node, incoming)

    def _work(self, node, incoming: List[str]) -> List[str]:
        bare, qualified = self.call_names(node)
        if not bare:
            return incoming                    # no work leaves this statement
        element = self.add(
            "task",
            short(self.text(node)),
            detail=short(self.text(node)),
            line=self.line(node),
            calls=bare,
            external=external_for(qualified),
        )
        self.link(incoming, element.id)
        return [element.id]

    def _branch(self, node, incoming: List[str]) -> List[str]:
        condition = self.condition_text(node)
        gate = self.add(
            "gateway", condition, detail=condition, line=self.line(node),
            calls=self.call_names(node.child_by_field_name("condition") or node)[0],
        )
        self.link(incoming, gate.id)

        yes_ends = self.walk(self.statements(self.body_of(node)), [gate.id])
        self.relabel_first(gate.id, yes_ends, "Yes")

        alternative = None
        for fieldname in self.spec.else_fields:
            alternative = node.child_by_field_name(fieldname)
            if alternative is not None:
                break
        if alternative is not None:
            # `else if` arrives as a nested branch; an `else_clause` wraps a block.
            inner = self.statements(alternative) if alternative.type in self.spec.blocks \
                else [c for c in alternative.named_children] or [alternative]
            no_ends = self.walk(inner, [gate.id])
            self.relabel_first(gate.id, no_ends, "No")
        else:
            no_ends = [gate.id]
            self.implicit_no.append(gate.id)

        return [e for e in yes_ends + no_ends if e]

    def _loop(self, node, incoming: List[str]) -> List[str]:
        over = ""
        for fieldname in self.spec.loop_fields:
            child = node.child_by_field_name(fieldname)
            if child is not None:
                over = short(self.unwrap(self.text(child)))
                break
        if not over:
            over = self.condition_text(node)
        loop = self.add(
            "loop", short(f"for each {over}" if over else "repeat"),
            detail=over, line=self.line(node), calls=self.call_names(node)[0],
        )
        self.link(incoming, loop.id)
        inner_ends = self.walk(self.statements(self.body_of(node)), [loop.id])
        self.link(inner_ends, loop.id, label="repeat", kind="loop_back")
        return [loop.id]

    def _try(self, node, incoming: List[str]) -> List[str]:
        body = self.body_of(node)
        ends = self.walk(self.statements(body), incoming)

        handler_ends: List[str] = []
        for child in node.named_children:
            if child.type in self.spec.catches:
                caught = short(self.text(child.child_by_field_name("parameter") or child).split("{")[0])
                err = self.add("error", short(f"on {caught}" if caught else "on error"),
                               detail=caught, line=self.line(child))
                self.link(ends or incoming, err.id, label="on error", kind="error")
                handler_ends.extend(self.walk(self.statements(self.body_of(child)), [err.id]) or [err.id])
            elif child.type in self.spec.finallys:
                ends = self.walk(self.statements(self.body_of(child)), ends + handler_ends)
                handler_ends = []

        return ends + handler_ends

    def _exit(self, node, incoming: List[str], kind: str) -> List[str]:
        text = short(self.text(node).rstrip(";"))
        element = self.add(kind, text or kind, detail=text, line=self.line(node),
                           calls=self.call_names(node)[0])
        self.link(incoming, element.id)
        return []


def _named(builder: _TreeSitterBuilder, node) -> str:
    name = node.child_by_field_name(builder.spec.name_field)
    return builder.text(name).strip() if name is not None else ""


def _find_function(builder: _TreeSitterBuilder, root, symbol: str):
    """Finds a function by name, accepting ``Class.method`` and bare forms."""
    wanted = symbol.replace("()", "").strip()
    method = wanted.rpartition(".")[2] or wanted

    fallback = None
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in builder.spec.functions:
            found = _named(builder, node)
            if found == method:
                return node
            if not found and fallback is None:
                # An arrow function assigned to a name: check the declarator.
                parent = node.parent
                if parent is not None and _named(builder, parent) == method:
                    return node
        stack.extend(node.named_children)
    return fallback


def extract_treesitter_process(
    root_dir: str,
    rel_path: str,
    symbol: str,
    prefix: str,
) -> Optional[Dict[str, Any]]:
    """Builds the process for one symbol in any grammar-backed language."""
    spec = spec_for(rel_path)
    if spec is None:
        return None

    parser = _parser(spec)
    if parser is None:
        return None

    try:
        with open(os.path.join(root_dir, rel_path), "rb") as handle:
            source = handle.read()
    except OSError:
        return None

    try:
        tree = parser.parse(source)
    except Exception:                          # pragma: no cover - grammar failure
        return None

    builder = _TreeSitterBuilder(prefix, spec, source)
    func = _find_function(builder, tree.root_node, symbol)
    if func is None:
        return None

    start = builder.add("start", "start", line=builder.line(func))
    open_ends = builder.walk(builder.statements(builder.body_of(func)), [start.id])
    builder.finish(open_ends, func.end_point[0] + 1)
    return builder.as_process(symbol, rel_path, builder.line(func))
