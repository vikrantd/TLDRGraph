"""
Classification Rule Engine for CodeChakra.

A Rule defines matching predicates against AST/graph nodes:
- path substrings, globs, or regexes
- label substrings, suffixes, or regexes
- node types and ID prefixes
- negative exclusions for paths and labels
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _compile_regex(pattern: Optional[str]) -> Optional[re.Pattern]:
    """Compiles a regex pattern safely, returning None if empty."""
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as err:
        raise ValueError(f"Invalid regex pattern {pattern!r}: {err}") from err


@dataclass(frozen=True)
class Rule:
    """One classification rule with positive and negative predicates."""

    file_contains: Tuple[str, ...] = ()
    exclude_file: Tuple[str, ...] = ()
    path_regex: Optional[str] = None
    exclude_path_regex: Optional[str] = None
    label_contains: Tuple[str, ...] = ()
    exclude_label: Tuple[str, ...] = ()
    label_ends_with: Tuple[str, ...] = ()
    exclude_label_ends_with: Tuple[str, ...] = ()
    label_regex: Optional[str] = None
    type_in: Tuple[str, ...] = ()
    id_prefix: Tuple[str, ...] = ()
    id_regex: Optional[str] = None

    _compiled_path_re: Optional[re.Pattern] = field(default=None, repr=False, compare=False)
    _compiled_exclude_path_re: Optional[re.Pattern] = field(default=None, repr=False, compare=False)
    _compiled_label_re: Optional[re.Pattern] = field(default=None, repr=False, compare=False)
    _compiled_id_re: Optional[re.Pattern] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        # Normalize collections to lowercase tuples where appropriate
        object.__setattr__(self, "file_contains", tuple(k.lower() for k in self.file_contains))
        object.__setattr__(self, "exclude_file", tuple(k.lower() for k in self.exclude_file))
        object.__setattr__(self, "label_contains", tuple(k.lower() for k in self.label_contains))
        object.__setattr__(self, "exclude_label", tuple(k.lower() for k in self.exclude_label))
        object.__setattr__(self, "label_ends_with", tuple(k.lower() for k in self.label_ends_with))
        object.__setattr__(self, "exclude_label_ends_with", tuple(k.lower() for k in self.exclude_label_ends_with))
        object.__setattr__(self, "type_in", tuple(self.type_in))
        object.__setattr__(self, "id_prefix", tuple(self.id_prefix))

        # Pre-compile regexes for fast matching
        object.__setattr__(self, "_compiled_path_re", _compile_regex(self.path_regex))
        object.__setattr__(self, "_compiled_exclude_path_re", _compile_regex(self.exclude_path_regex))
        object.__setattr__(self, "_compiled_label_re", _compile_regex(self.label_regex))
        object.__setattr__(self, "_compiled_id_re", _compile_regex(self.id_regex))

    def _matches_exclusions(self, file_path: str, label: str) -> bool:
        """Returns True if the node is excluded by any negative predicate."""
        if self.exclude_file and any(k in file_path for k in self.exclude_file):
            return True
        if self.exclude_label and any(k in label for k in self.exclude_label):
            return True
        if self.exclude_label_ends_with and any(label.endswith(k) for k in self.exclude_label_ends_with):
            return True
        if self._compiled_exclude_path_re and self._compiled_exclude_path_re.search(file_path):
            return True
        return False

    def _matches_inclusions(self, node_id: str, node_data: Mapping[str, Any],
                            file_path: str, label: str) -> bool:
        """Returns True if any positive predicate matches the node."""
        if self.type_in and node_data.get("type") in self.type_in:
            return True
        if self.id_prefix and any(str(node_id).startswith(p) for p in self.id_prefix):
            return True
        if self._compiled_id_re and self._compiled_id_re.search(str(node_id)):
            return True
        if self.file_contains and any(k in file_path for k in self.file_contains):
            return True
        if self._compiled_path_re and self._compiled_path_re.search(file_path):
            return True
        if self.label_contains and any(k in label for k in self.label_contains):
            return True
        if self.label_ends_with and any(label.endswith(k) for k in self.label_ends_with):
            return True
        if self._compiled_label_re and self._compiled_label_re.search(label):
            return True
        return False

    def matches(self, node_id: str, node_data: Mapping[str, Any]) -> bool:
        """Tests whether this rule matches the given node attributes."""
        file_path = str(
            node_data.get("source_file") or node_data.get("file")
            or node_data.get("path") or node_id or ""
        ).lower()
        label = str(node_data.get("label") or node_id or "").lower()

        if self._matches_exclusions(file_path, label):
            return False
        return self._matches_inclusions(node_id, node_data, file_path, label)

    def as_record(self) -> Dict[str, Any]:
        """Serializes this rule to a plain dict for YAML/JSON storage."""
        record: Dict[str, Any] = {}
        for key in (
            "file_contains", "exclude_file", "path_regex", "exclude_path_regex",
            "label_contains", "exclude_label", "label_ends_with",
            "exclude_label_ends_with", "label_regex", "type_in", "id_prefix", "id_regex"
        ):
            val = getattr(self, key)
            if val:
                record[key] = list(val) if isinstance(val, tuple) else val
        return record

    @classmethod
    def from_record(cls, data: Mapping[str, Any]) -> Rule:
        """Constructs a Rule from a plain mapping."""
        def _to_tuple(v: Any) -> Tuple[str, ...]:
            if isinstance(v, (list, tuple)):
                return tuple(str(x) for x in v)
            if isinstance(v, str) and v:
                return (v,)
            return ()

        return cls(
            file_contains=_to_tuple(data.get("file_contains")),
            exclude_file=_to_tuple(data.get("exclude_file")),
            path_regex=data.get("path_regex"),
            exclude_path_regex=data.get("exclude_path_regex"),
            label_contains=_to_tuple(data.get("label_contains")),
            exclude_label=_to_tuple(data.get("exclude_label")),
            label_ends_with=_to_tuple(data.get("label_ends_with")),
            exclude_label_ends_with=_to_tuple(data.get("exclude_label_ends_with")),
            label_regex=data.get("label_regex"),
            type_in=_to_tuple(data.get("type_in")),
            id_prefix=_to_tuple(data.get("id_prefix")),
            id_regex=data.get("id_regex"),
        )
