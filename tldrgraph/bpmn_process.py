"""
The language-neutral half of BPMN extraction.

A process is a graph of shapes and the flows between them. Threading statements
into that graph - opening a branch, closing it, sending a loop back on itself -
is the same work whatever the language, so it lives here. Each language front
end (``bpmn_extract`` for Python, ``bpmn_treesitter`` for everything else) walks
its own syntax tree and calls into this builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MAX_LABEL = 72


def short(text: str, limit: int = MAX_LABEL) -> str:
    """Trims a label to something that fits in a shape."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


@dataclass
class Element:
    """One BPMN shape."""

    id: str
    kind: str                       # start | task | gateway | loop | end | error
    label: str
    detail: str = ""
    line: int = 0
    calls: List[str] = field(default_factory=list)   # raw callee names, in order
    node_id: Optional[str] = None                    # resolved graph node, if any
    external: Optional[str] = None                   # external system, if any

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "line": self.line,
            "calls": self.calls,
            "node_id": self.node_id,
            "external": self.external,
        }


@dataclass
class Flow:
    """One sequence flow between two shapes."""

    source: str
    target: str
    label: str = ""
    kind: str = "sequence"          # sequence | loop_back | error

    def as_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "target": self.target, "label": self.label, "kind": self.kind}


class ProcessBuilder:
    """Walks a function body and threads statements into a BPMN element graph."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.elements: List[Element] = []
        self.flows: List[Flow] = []
        self.implicit_no: List[str] = []
        self._seq = 0

    def _new_id(self, kind: str) -> str:
        self._seq += 1
        return f"{self.prefix}__{kind}{self._seq}"

    def add(self, kind: str, label: str, **kwargs: Any) -> Element:
        el = Element(id=self._new_id(kind), kind=kind, label=label, **kwargs)
        self.elements.append(el)
        return el

    def link(self, sources: List[str], target: str, label: str = "", kind: str = "sequence") -> None:
        for src in sources:
            if src == target:
                continue
            if any(f.source == src and f.target == target and f.label == label for f in self.flows):
                continue
            self.flows.append(Flow(source=src, target=target, label=label, kind=kind))

    # -- statement walking ---------------------------------------------------
    def relabel_first(self, gate_id: str, ends: List[str], label: str) -> None:
        """Names the flow that leaves the gateway into the branch just walked."""
        for flow in self.flows:
            if flow.source == gate_id and not flow.label:
                flow.label = label
                return
        # The branch produced no element of its own (it only returned): the end
        # event is already linked, so find that flow instead.
        for flow in self.flows:
            if flow.source == gate_id and flow.target in ends:
                flow.label = flow.label or label
                return

    def finish(self, open_ends: List[str], end_line: int = 0) -> None:
        """Closes the process: name the implicit No paths, then cap the tail."""
        for gate_id in self.implicit_no:
            for flow in self.flows:
                if flow.source == gate_id and not flow.label:
                    flow.label = "No"

        if open_ends:
            done = self.add("end", "done", line=end_line)
            self.link(open_ends, done.id)

    def as_process(self, symbol: str, rel_path: str, line: int) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "file": rel_path,
            "line": line,
            "elements": [e.as_dict() for e in self.elements],
            "flows": [f.as_dict() for f in self.flows],
        }
