import os
import ast
import re
import yaml
from pathlib import Path

def generate_all_enrichments():
    with open(".codechakra/enrichment_request.yaml", "r", encoding="utf-8") as f:
        request_data = yaml.safe_load(f)

    nodes = request_data.get("nodes", [])
    print(f"Generating clean enrichment for all {len(nodes)} nodes (NO bare one-word AST calls)...")

    # Load source file contents and ASTs
    file_contents = {}
    file_ast = {}
    imports_by_file = {}

    for n in nodes:
        fpath = n.get("file")
        if fpath and fpath not in file_contents and os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as sf:
                    content = sf.read()
                    file_contents[fpath] = content
                    try:
                        tree = ast.parse(content)
                        file_ast[fpath] = tree
                        
                        # Extract real cross-module imports: symbol_name -> "file_path:symbol_name"
                        imp_map = {}
                        for node_ast in ast.walk(tree):
                            if isinstance(node_ast, ast.ImportFrom) and node_ast.module:
                                mod = node_ast.module.replace(".", "/") + ".py"
                                # Normalize relative imports if any
                                if mod.startswith("codechakra/"):
                                    for alias in node_ast.names:
                                        imp_map[alias.name] = f"{mod}:{alias.name}"
                        imports_by_file[fpath] = imp_map
                    except Exception:
                        pass
            except Exception:
                pass

    response_items = []
    for node in nodes:
        nid = node["id"]
        label = node.get("label", nid)
        fpath = node.get("file", "")
        layer_id = node.get("layer_id", "")
        loc = node.get("source_location", "")

        intent = ""
        input_fields = []
        output_fields = []
        calls = []

        imp_map = imports_by_file.get(fpath, {})

        # 1. Test functions and test files
        if fpath.startswith("tests/"):
            clean_name = label.replace("test_", "").replace("_", " ")
            intent = f"### Test Suite\nVerifies `{clean_name}` functionality and assertions in `{fpath}`."
            input_fields = ["run", "state", "loop_repo"] if "run" in label or "state" in label else []
            output_fields = ["assertion_result"]
            # Only link to explicit imported codechakra symbols (qualified with file:symbol)
            if fpath in file_ast:
                for sub in ast.walk(file_ast[fpath]):
                    if isinstance(sub, ast.Name) and sub.id in imp_map:
                        target = imp_map[sub.id]
                        if target not in calls and len(calls) < 4:
                            calls.append(target)

        # 2. Source code in codechakra/
        elif fpath.startswith("codechakra/"):
            content = file_contents.get(fpath, "")
            # Module / file node
            if label.endswith(".py") or not loc or loc == "L1":
                mod_name = os.path.basename(fpath).replace(".py", "")
                intent = f"### Module `{mod_name}`\nCore architectural module defining routines and interfaces for {mod_name}."
                if fpath in file_ast:
                    for item in file_ast[fpath].body:
                        if isinstance(item, (ast.FunctionDef, ast.ClassDef)):
                            if item.name not in output_fields and len(output_fields) < 8:
                                output_fields.append(item.name)
                # For module node: list qualified imported modules/symbols
                for imported_target in imp_map.values():
                    if imported_target not in calls and len(calls) < 5:
                        calls.append(imported_target)
            else:
                clean_sym = label.strip(".() ")
                docstring = ""
                if fpath in file_ast:
                    for item in ast.walk(file_ast[fpath]):
                        if getattr(item, "name", None) == clean_sym:
                            docstring = ast.get_docstring(item) or ""
                            if isinstance(item, ast.FunctionDef):
                                for arg in item.args.args:
                                    if arg.arg not in ("self", "cls"):
                                        input_fields.append(arg.arg)
                                if item.returns:
                                    ret_name = getattr(item.returns, "id", None) or getattr(item.returns, "attr", None)
                                    if ret_name:
                                        output_fields.append(str(ret_name))
                            elif isinstance(item, ast.ClassDef):
                                for b in item.body:
                                    if isinstance(b, ast.FunctionDef) and b.name == "__init__":
                                        for arg in b.args.args:
                                            if arg.arg not in ("self", "cls"):
                                                input_fields.append(arg.arg)
                                    elif isinstance(b, ast.FunctionDef):
                                        if b.name not in output_fields and len(output_fields) < 6:
                                            output_fields.append(b.name)
                            
                            # ONLY link to explicit imported architectural symbols (qualified file:symbol)
                            for sub in ast.walk(item):
                                if isinstance(sub, ast.Name) and sub.id in imp_map:
                                    target = imp_map[sub.id]
                                    if target not in calls and len(calls) < 5:
                                        calls.append(target)
                            break

                if docstring:
                    lines = [l.strip() for l in docstring.strip().split("\n") if l.strip()]
                    summary_line = lines[0] if lines else clean_sym
                    intent = f"### `{clean_sym}`\n{summary_line}"
                else:
                    intent = f"### `{clean_sym}`\nExecutes {layer_id} operations in `{fpath}`."
        else:
            intent = f"### `{label}`\nArchitectural element in `{fpath or 'repository'}`."

        item = {
            "id": nid,
            "intent": intent,
            "input_fields": input_fields[:6],
            "output_fields": output_fields[:6],
            "calls": calls  # Only qualified file:symbol targets, or empty []
        }
        response_items.append(item)

    print(f"Writing {len(response_items)} clean enriched items to .codechakra/enrichment_response.yaml...")
    with open(".codechakra/enrichment_response.yaml", "w", encoding="utf-8") as f:
        yaml.dump(response_items, f, default_flow_style=False, sort_keys=False)

    print("Done! Ready to run apply-enrichment.")

if __name__ == "__main__":
    generate_all_enrichments()
