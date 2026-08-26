"""
The node names each grammar uses for the constructs BPMN cares about.

Every tree-sitter grammar spells the same ideas differently - a branch is
``if_statement`` in JavaScript and ``if`` in Ruby - so the walker stays generic
and the differences live here as data. Supporting another language means adding
one row, not writing another parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple
import os


@dataclass(frozen=True)
class LanguageSpec:
    """The node names one grammar uses for the constructs BPMN cares about."""

    name: str
    module: str                       # tree-sitter package to import
    entry: str                        # function on that package returning the grammar
    extensions: Tuple[str, ...]
    functions: Set[str]
    branches: Set[str]
    loops: Set[str]
    tries: Set[str]
    catches: Set[str]
    finallys: Set[str]
    returns: Set[str]
    throws: Set[str]
    calls: Set[str]
    blocks: Set[str]
    skips: Set[str] = field(default_factory=set)      # nested definitions
    condition_fields: Tuple[str, ...] = ("condition",)
    body_fields: Tuple[str, ...] = ("body", "consequence")
    else_fields: Tuple[str, ...] = ("alternative",)
    # What a loop runs over: grammars name it differently ("right" for for-of,
    # "condition" for while, "value" for a range clause).
    loop_fields: Tuple[str, ...] = ("right", "value", "condition")
    name_field: str = "name"


_JS_LIKE = dict(
    functions={"function_declaration", "function_expression", "function", "method_definition",
               "arrow_function", "generator_function_declaration"},
    branches={"if_statement"},
    loops={"for_statement", "for_in_statement", "for_of_statement", "while_statement", "do_statement"},
    tries={"try_statement"},
    catches={"catch_clause"},
    finallys={"finally_clause"},
    returns={"return_statement"},
    throws={"throw_statement"},
    calls={"call_expression", "new_expression"},
    blocks={"statement_block", "program"},
    skips={"class_declaration", "function_declaration", "method_definition"},
)

LANGUAGES: Tuple[LanguageSpec, ...] = (
    LanguageSpec(name="javascript", module="tree_sitter_javascript", entry="language",
                 extensions=(".js", ".jsx", ".mjs", ".cjs"), **_JS_LIKE),
    LanguageSpec(name="typescript", module="tree_sitter_typescript", entry="language_typescript",
                 extensions=(".ts", ".mts", ".cts"), **_JS_LIKE),
    LanguageSpec(name="tsx", module="tree_sitter_typescript", entry="language_tsx",
                 extensions=(".tsx",), **_JS_LIKE),
    LanguageSpec(
        name="go", module="tree_sitter_go", entry="language", extensions=(".go",),
        functions={"function_declaration", "method_declaration", "func_literal"},
        branches={"if_statement"}, loops={"for_statement", "range_clause"},
        tries=set(), catches=set(), finallys=set(),
        returns={"return_statement"}, throws={"go_statement"},
        calls={"call_expression"}, blocks={"block", "source_file"},
        skips={"function_declaration", "method_declaration", "type_declaration"},
    ),
    LanguageSpec(
        name="java", module="tree_sitter_java", entry="language", extensions=(".java",),
        functions={"method_declaration", "constructor_declaration"},
        branches={"if_statement"},
        loops={"for_statement", "enhanced_for_statement", "while_statement", "do_statement"},
        tries={"try_statement"}, catches={"catch_clause"}, finallys={"finally_clause"},
        returns={"return_statement"}, throws={"throw_statement"},
        calls={"method_invocation", "object_creation_expression"},
        blocks={"block", "program"},
        skips={"class_declaration", "method_declaration", "interface_declaration"},
    ),
    LanguageSpec(
        name="ruby", module="tree_sitter_ruby", entry="language", extensions=(".rb",),
        functions={"method", "singleton_method"},
        branches={"if", "unless"}, loops={"while", "until", "for", "do_block"},
        tries={"begin"}, catches={"rescue"}, finallys={"ensure"},
        returns={"return"}, throws={"raise"},
        calls={"call", "method_call"}, blocks={"body_statement", "program", "then", "else"},
        skips={"class", "module", "method"},
        condition_fields=("condition",), body_fields=("body", "consequence"),
    ),
    LanguageSpec(
        name="rust", module="tree_sitter_rust", entry="language", extensions=(".rs",),
        functions={"function_item"},
        branches={"if_expression", "match_expression"},
        loops={"for_expression", "while_expression", "loop_expression"},
        tries=set(), catches=set(), finallys=set(),
        returns={"return_expression"}, throws={"macro_invocation"},
        calls={"call_expression", "macro_invocation"},
        blocks={"block", "source_file"},
        skips={"function_item", "impl_item", "struct_item"},
        condition_fields=("condition",), body_fields=("consequence", "body"),
    ),
)


_SPEC_BY_EXT = {ext: spec for spec in LANGUAGES for ext in spec.extensions}


def spec_for(rel_path: str) -> Optional[LanguageSpec]:
    """The grammar covering this file, or ``None`` when nothing does."""
    return _SPEC_BY_EXT.get(os.path.splitext(rel_path)[1].lower())


def supported_extensions() -> Tuple[str, ...]:
    """Every file extension the BPMN view can read control flow from."""
    return tuple(sorted(_SPEC_BY_EXT))
