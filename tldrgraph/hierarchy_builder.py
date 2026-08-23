"""
Compound hierarchy construction algorithms for TLDRGraph.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import extractors, paths
from .classifier import classify_node
from .labels import build_display_labels
from .layers import (
    LAYER_API,
    LAYER_DATA,
    LAYER_SERVICE,
    LAYER_UI,
    get_registry,
    layer_name,
)

HIERARCHY_SCHEMA = "tldrgraph/hierarchy@2"
TIER_PAGE = "page"
TIER_COMPONENT = "component"
TIER_MODULE = "module"
TIER_ELEMENT = "element"
PAGE_CONTAINS_RELATION = "page_contains"
MULTI_PARENT_RULE = "multi-parent"
PAGE_STEMS = {"page", "layout"}
_PAGE_EXTS = (".tsx", ".jsx", ".ts", ".js")
IMPORT_RELATIONS = {"imports", "imports_from"}
_REDERIVED_RELATIONS = {
    extractors.HTTP_ROUTE_RELATION,
    extractors.DB_MODEL_RELATION,
    extractors.CALLS_ENDPOINT_RELATION,
    extractors.HANDLED_BY_RELATION,
}


def slug(text: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(text))


def container_id(file_path: str) -> str:
    return "container_" + slug(file_path)


def subnode_id(graph_node_id: str) -> str:
    return "sub_" + slug(graph_node_id)


def is_endpoint_record(node: Dict[str, Any]) -> bool:
    return (
        node.get("type") == extractors.ENDPOINT_NODE_TYPE
        or str(node.get("id", "")).startswith(extractors.ENDPOINT_NODE_PREFIX)
    )


def is_page_file(file_path: str) -> bool:
    base = os.path.basename((file_path or "").replace(os.sep, "/"))
    stem, ext = os.path.splitext(base)
    return ext in _PAGE_EXTS and stem.lower() in PAGE_STEMS


def is_test_node(file_path: str = "", label: str = "") -> bool:
    f = (file_path or "").lower().replace("\\", "/")
    base = os.path.basename(f)
    lbl = (label or "").lower()

    if any(part in f for part in ("/tests/", "/test/", "/__tests__/", "/__mocks__/", "/spec/", "/specs/", "/e2e/")) \
            or f.startswith(("tests/", "test/", "__tests__/", "__mocks__/", "spec/", "specs/", "e2e/")):
        return True

    if ".test." in base or ".spec." in base or base.startswith("test_") or base.endswith(("_test.py", "_spec.py", "_test.ts", "_spec.ts", "_test.js", "_spec.js")):
        return True

    if lbl.startswith(("test_", "test ", "describe(", "it(", "test(")):
        return True

    return False


def page_route(file_path: str) -> str:
    path = (file_path or "").replace(os.sep, "/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    stem = os.path.splitext(parts[-1])[0].lower()
    segments = parts[:-1]
    if "app" in segments:
        segments = segments[len(segments) - 1 - segments[::-1].index("app") + 1:]
    elif "pages" in segments:
        segments = segments[len(segments) - 1 - segments[::-1].index("pages") + 1:]
    route = "/" + "/".join(segments)
    return route if stem == "page" else f"{route} ({stem})"


class HierarchyState:
    """Encapsulates state during compound hierarchy generation."""

    def __init__(self, root_dir: str):
        self.root_dir = os.path.abspath(root_dir)
        self.containers: Dict[str, Dict[str, Any]] = {}
        self.subnode_map: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.seen_edges: Set[Tuple[str, str, str, str, str]] = set()

    def get_or_create_container(
        self,
        file_path: str,
        default_layer: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        cid = container_id(file_path)
        if cid not in self.containers:
            if default_layer:
                classified_layer = default_layer
                layer_obj = get_registry().by_name(default_layer)
                classified_layer_id = layer_obj.id if layer_obj else ""
            else:
                layer_obj = classify_node(file_path, {"file": file_path})
                classified_layer = layer_obj.name
                classified_layer_id = layer_obj.id
            c_label = label or os.path.basename(file_path)
            is_test = is_test_node(file_path, c_label)
            self.containers[cid] = {
                "id": cid,
                "label": c_label,
                "file": file_path,
                "layer": classified_layer,
                "layer_id": classified_layer_id,
                "tier": TIER_PAGE if is_page_file(file_path) else TIER_MODULE,
                "is_test": is_test,
                "intent": "",
                "input_fields": [],
                "output_fields": [],
                "fields": [],
                "subnodes": [],
                "parent_containers": [],
                "child_containers": [],
                "shared": False,
                "out_containers": set(),
                "in_containers": set(),
            }
        return self.containers[cid]

    def add_subnode(self, container: Dict[str, Any], subnode: Dict[str, Any]) -> Dict[str, Any]:
        sid = subnode["id"]
        if sid in self.subnode_map:
            return self.subnode_map[sid]
        subnode["tier"] = TIER_ELEMENT
        subnode["container_id"] = container["id"]
        subnode.setdefault("display_label", subnode.get("label", ""))
        self.subnode_map[sid] = subnode
        container["subnodes"].append(subnode)
        return subnode

    def add_edge(
        self,
        src_c: Dict[str, Any],
        tgt_c: Dict[str, Any],
        src_sub: Optional[str],
        tgt_sub: Optional[str],
        relation: str,
        label: str,
        **extra: Any,
    ) -> None:
        key = (src_c["id"], tgt_c["id"], src_sub or "", tgt_sub or "", relation)
        if key in self.seen_edges:
            return
        self.seen_edges.add(key)
        src_c["out_containers"].add(tgt_c["id"])
        tgt_c["in_containers"].add(src_c["id"])
        self.edges.append({
            "source_container": src_c["id"],
            "target_container": tgt_c["id"],
            "source_subnode": src_sub,
            "target_subnode": tgt_sub,
            "relation": relation,
            "label": label,
            **extra,
        })


def _is_valid_page_import_files(src_f: str, tgt_f: str) -> bool:
    if not src_f or not tgt_f or src_f == tgt_f:
        return False
    return is_page_file(src_f) and not is_page_file(tgt_f)


def _find_page_import_pair(
    e: Dict[str, Any],
    ast_node_map: Dict[str, Dict[str, Any]],
    containers: Dict[str, Dict[str, Any]],
    ui_id: str,
) -> Optional[Tuple[str, str]]:
    if str(e.get("relation") or "") not in IMPORT_RELATIONS:
        return None
    src_n = ast_node_map.get(str(e.get("source")))
    tgt_n = ast_node_map.get(str(e.get("target")))
    if not (src_n and tgt_n):
        return None
    src_f, tgt_f = src_n.get("file") or "", tgt_n.get("file") or ""
    if not _is_valid_page_import_files(src_f, tgt_f):
        return None
    src_c, tgt_c = containers.get(container_id(src_f)), containers.get(container_id(tgt_f))
    if not src_c or not tgt_c:
        return None
    if src_c.get("layer_id") != ui_id or tgt_c.get("layer_id") != ui_id:
        return None
    return src_f, tgt_f


def link_pages_to_components(
    containers: Dict[str, Dict[str, Any]],
    ast_edges: List[Dict[str, Any]],
    ast_node_map: Dict[str, Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Derive page -> component containment from import edges."""
    reg = get_registry()
    ui_id = LAYER_UI if LAYER_UI in reg else reg.ordered()[0].id
    pairs: Set[Tuple[str, str]] = set()

    for e in ast_edges:
        pair = _find_page_import_pair(e, ast_node_map, containers, ui_id)
        if pair:
            pairs.add(pair)

    importers: Dict[str, List[str]] = {}
    for src_f, tgt_f in sorted(pairs):
        importers.setdefault(tgt_f, []).append(src_f)

    linked: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for component_file, page_files in sorted(importers.items()):
        component_c = containers[container_id(component_file)]
        component_c["tier"] = TIER_COMPONENT
        component_c["parent_containers"] = sorted(container_id(p) for p in page_files)
        component_c["shared"] = len(page_files) > 1

        for page_file in sorted(page_files):
            page_c = containers[container_id(page_file)]
            if component_c["id"] not in page_c["child_containers"]:
                page_c["child_containers"].append(component_c["id"])
            linked.append((page_c, component_c))
    return linked
