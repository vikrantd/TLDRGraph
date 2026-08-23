#!/usr/bin/env python3
"""
Code health and complexity linter for TLDRGraph.

Enforces:
1. Max file length (< 400 lines).
2. Max function length (<= 50 lines).
3. Max function cyclomatic complexity (<= 15).
"""

import ast
import argparse
import glob
import os
import sys
from typing import Dict, List, Tuple


def calculate_cyclomatic_complexity(node: ast.AST) -> int:
    """Calculates McCabe cyclomatic complexity for a function AST node."""
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler, ast.With, ast.AsyncWith)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, (ast.IfExp, ast.Assert)):
            complexity += 1
    return complexity


def check_file(
    filepath: str,
    max_file_lines: int = 400,
    max_func_lines: int = 50,
    max_complexity: int = 15,
) -> Tuple[List[str], List[str]]:
    """
    Checks a single Python file against health rules.
    Returns (file_errors, function_errors).
    """
    file_errors = []
    func_errors = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as err:
        return [f"Could not read {filepath}: {err}"], []

    line_count = len(lines)
    if line_count >= max_file_lines:
        file_errors.append(
            f"{filepath}: {line_count} lines (exceeds limit of {max_file_lines} lines)"
        )

    try:
        tree = ast.parse("".join(lines), filename=filepath)
    except SyntaxError as err:
        return [f"{filepath}: Syntax error: {err}"], []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = node.name
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", start_line)
            length = end_line - start_line + 1

            complexity = calculate_cyclomatic_complexity(node)

            if length > max_func_lines:
                func_errors.append(
                    f"{filepath}:{start_line} function '{func_name}' is {length} lines (limit: {max_func_lines})"
                )

            if complexity > max_complexity:
                func_errors.append(
                    f"{filepath}:{start_line} function '{func_name}' complexity is {complexity} (limit: {max_complexity})"
                )

    return file_errors, func_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="TLDRGraph Code Health Checker")
    parser.add_argument("--target-dir", default="tldrgraph", help="Directory to check")
    parser.add_argument("--max-file-lines", type=int, default=400, help="Max lines per file")
    parser.add_argument("--max-func-lines", type=int, default=50, help="Max lines per function")
    parser.add_argument("--max-complexity", type=int, default=15, help="Max cyclomatic complexity")
    parser.add_argument("--check-tests", action="store_true", help="Also check tests directory")
    args = parser.parse_args()

    pattern = os.path.join(args.target_dir, "**", "*.py")
    files = sorted(glob.glob(pattern, recursive=True))

    all_file_errors = []
    all_func_errors = []

    for filepath in files:
        f_errs, fn_errs = check_file(
            filepath,
            max_file_lines=args.max_file_lines,
            max_func_lines=args.max_func_lines,
            max_complexity=args.max_complexity,
        )
        all_file_errors.extend(f_errs)
        all_func_errors.extend(fn_errs)

    print(f"🔍 Checked {len(files)} Python files in {args.target_dir}/")

    if not all_file_errors and not all_func_errors:
        print("✅ All files and functions comply with code health standards!")
        return 0

    if all_file_errors:
        print(f"\n❌ {len(all_file_errors)} file(s) exceed line limits (max {args.max_file_lines}):")
        for err in all_file_errors:
            print(f"   • {err}")

    if all_func_errors:
        print(f"\n❌ {len(all_func_errors)} function(s) exceed complexity/length limits:")
        for err in all_func_errors:
            print(f"   • {err}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
