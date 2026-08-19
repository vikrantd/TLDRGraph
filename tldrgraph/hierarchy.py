"""
Multi-Layer Compound Hierarchy Builder for TLDRGraph.

Emits a **3-tier** hierarchical graph (schema ``tldrgraph/hierarchy@2``):

======  =============  =========================================================
Tier    Node kind      What it is
======  =============  =========================================================
1       ``page``       A routed entry file (Next.js ``page.tsx`` / ``layout.tsx``)
        ``module``     Any other top-level container: a controller, a service, a
                       schema, a devops config -- anything that is not owned by a
                       page.
2       ``component``  A UI file a page imports. Nested *inside* its page.
3       ``element``    A subnode: an endpoint, a service method, a symbol, a
                       database table. Owned by a tier-1 or tier-2 container.
======  =============  =========================================================

Why it changed
--------------
The previous shape was 2 tiers (file container -> symbol subnode) and produced
two visible defects, which are unrelated and are fixed separately:

*Call-site duplication.* Every frontend HTTP call site materialized its own
"Data Fetch"/"Form / Action" subnode, so ``GET /auth/me`` existed 19 times (once
per calling file) plus a 20th time as an "API Endpoint" -- 20 unrelated nodes for
one route. Those per-call-site subnodes are gone. An endpoint is now a single
identity (:func:`tldrgraph.extractors.collect_endpoints`), living in the API
layer, with ``calls_endpoint`` edges coming in from every caller and
``handled_by`` edges going out to its handler. Components reach services *through*
endpoints, which is what the graph always claimed and never actually encoded.

*Label collision.* ``.constructor()`` appeared 32 times, but those were 32
genuinely distinct symbols whose labels omit their owner. They are now given a
``display_label`` (``AuditService.constructor()``) while ``label`` stays exactly
as graphify emitted it -- see :mod:`tldrgraph.labels` for why that separation is
load-bearing.

Containment rule for shared components: explicit multi-parent
-------------------------------------------------------------
Containment is a **DAG, not a tree**. A component belongs to *every* page that
imports it, because that is what the source says: ``AaoDeskView`` really is part
of both ``aao-desk/page.tsx`` and ``dashboard/page.tsx``, and picking one of them
would be a coin flip presented as a fact. So each component carries
``parent_containers`` -- the full sorted list -- and ``shared`` is simply
``len(parent_containers) > 1``.

For consumers that must have a single-parent tree (an indented outline, a
treemap), ``parent_container`` names the *primary* parent: the first entry of
``parent_containers`` in sorted order. It is a deterministic pick from a list the
consumer can always see in full, not a hidden decision.

The rejected alternative was hoisting shared components to the top level. It
keeps a clean tree but breaks the user's model for the very first example
measured: two pages import ``AaoDeskView``, so under hoisting it would have been
part of no page at all.

The graph cannot cycle: a page is never the *target* of ``page_contains``, so
containment only ever runs page -> component.
"""

import os
import json
import re
from typing import Dict, Any, List, Optional, Tuple, Set

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
from . import extractors

#: Version of the structure returned by :func:`build_multilayer_hierarchy`.
#: Phase 4's visualizer is written against this contract.
HIERARCHY_SCHEMA = "tldrgraph/hierarchy@2"

#: Container tiers.
TIER_PAGE = "page"
TIER_COMPONENT = "component"
TIER_MODULE = "module"
#: Subnode tier.
TIER_ELEMENT = "element"

#: Relation emitted for page -> component containment.
PAGE_CONTAINS_RELATION = "page_contains"

#: How a component imported by more than one page is placed. See the module
#: docstring; recorded in the output so a consumer never has to guess.
MULTI_PARENT_RULE = "multi-parent"

#: Next.js file conventions that make a file a routed entry point.
PAGE_STEMS = {"page", "layout"}
_PAGE_EXTS = (".tsx", ".jsx", ".ts", ".js")

#: AST relations that mean "this file pulls in that file".
IMPORT_RELATIONS = {"imports", "imports_from"}

#: Seam relations this module re-derives from source itself. Never copied out of
#: a persisted snapshot -- that would double them, and would make the emitted
#: hierarchy depend on which graph file happened to be on disk.
_REDERIVED_RELATIONS = {
    extractors.HTTP_ROUTE_RELATION,
    extractors.DB_MODEL_RELATION,
    extractors.CALLS_ENDPOINT_RELATION,
    extractors.HANDLED_BY_RELATION,
}


def _slug(text: Any) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', str(text))


def container_id(file_path: str) -> str:
    return "container_" + _slug(file_path)


def subnode_id(graph_node_id: str) -> str:
    """Subnode ids mirror their graph node id, prefixed. One convention, always."""
    return "sub_" + _slug(graph_node_id)


def _is_endpoint_record(node: Dict[str, Any]) -> bool:
    """Is this AST/snapshot node one of the synthesized endpoint nodes?"""
    return (node.get("type") == extractors.ENDPOINT_NODE_TYPE
            or str(node.get("id", "")).startswith(extractors.ENDPOINT_NODE_PREFIX))


def is_page_file(file_path: str) -> bool:
    """A Next.js routed entry file -- the top of a UI branch."""
    base = os.path.basename((file_path or "").replace(os.sep, "/"))
    stem, ext = os.path.splitext(base)
    return ext in _PAGE_EXTS and stem.lower() in PAGE_STEMS


def is_test_node(file_path: str = "", label: str = "") -> bool:
    """Returns True if the file path or symbol label represents a test, spec, or test helper."""
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
    """
    The URL route a Next.js page file serves: ``frontend/src/app/applications/
    aao-desk/page.tsx`` -> ``/applications/aao-desk``.

    Page containers are otherwise all called ``page.tsx`` -- 32 of them in this
    repository -- which is the same collision the symbol labels had, one tier up.
    A ``layout.tsx`` is tagged so it does not collide with its own route's page.
    """
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


def build_multilayer_hierarchy(root_dir: str = ".") -> Dict[str, Any]:
    """
    Scans the repository and builds the 3-tier compound graph described above.

    Returns ``{schema, tiers, multi_parent_rule, containers, edges, stats}``.
    ``containers`` is a *flat* list; the tree is expressed by ``parent_container``
    / ``child_containers`` on each record, so a renderer can walk it either way.
    """
    root_dir = os.path.abspath(root_dir)

    # ------------------------------------------------------------------ #
    # 1. Deterministic source elements
    # ------------------------------------------------------------------ #
    frontend_calls = extractors.collect_frontend_calls(root_dir)
    backend_routes = extractors.collect_backend_routes(root_dir)
    prisma_models = extractors.collect_prisma_models(root_dir)
    endpoints = extractors.collect_endpoints(frontend_calls, backend_routes)

    # Load base AST graph if available
    graph_json_path = os.path.join(root_dir, ".tldrgraph", "graph.json")
    if not os.path.exists(graph_json_path):
        graph_json_path = os.path.join(root_dir, "graphify-out", "graph.json")

    ast_nodes: List[Dict[str, Any]] = []
    ast_edges: List[Dict[str, Any]] = []
    if os.path.exists(graph_json_path):
        try:
            with open(graph_json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                ast_nodes = raw.get("nodes", [])
                ast_edges = raw.get("edges", raw.get("links", []))
        except Exception:
            pass

    # Node lookup maps from the AST. `file` is normalized here so every consumer
    # below (NodeIndex, display labels, container keys) reads the same key.
    ast_by_file: Dict[str, List[Dict[str, Any]]] = {}
    ast_node_map: Dict[str, Dict[str, Any]] = {}
    ast_records: List[Dict[str, Any]] = []
    for n in ast_nodes:
        nid = str(n.get("id", ""))
        if not nid:
            continue
        fpath = n.get("source_file") or n.get("file") or ""
        record = dict(n)
        record["id"] = nid
        record["file"] = fpath
        ast_node_map[nid] = record
        ast_records.append(record)
        if fpath:
            ast_by_file.setdefault(fpath, []).append(record)

    # Owner-qualified labels. `label` is left untouched -- see tldrgraph.labels.
    display_labels = build_display_labels(ast_records, ast_edges)

    # Call-site -> owning symbol attribution, shared with the core graph.
    # Endpoints are not in here: they are synthesized, not AST symbols.
    ast_index = extractors.NodeIndex(ast_records)

    containers: Dict[str, Dict[str, Any]] = {}
    subnode_map: Dict[str, Dict[str, Any]] = {}

    def get_or_create_container(file_path: str, default_layer: Optional[str] = None,
                                label: Optional[str] = None) -> Dict[str, Any]:
        cid = container_id(file_path)
        if cid not in containers:
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
            containers[cid] = {
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
                "in_containers": set()
            }
        return containers[cid]

    def add_subnode(container: Dict[str, Any], subnode: Dict[str, Any]) -> Dict[str, Any]:
        sid = subnode["id"]
        if sid in subnode_map:
            return subnode_map[sid]
        subnode["tier"] = TIER_ELEMENT
        subnode["container_id"] = container["id"]
        subnode.setdefault("display_label", subnode.get("label", ""))
        subnode_map[sid] = subnode
        container["subnodes"].append(subnode)
        return subnode

    # ------------------------------------------------------------------ #
    # 2. Tier-3 elements: AST symbols
    #
    # Runs first so endpoint / call-site attribution below can point at real
    # subnodes that already exist.
    # ------------------------------------------------------------------ #
    for fpath, nlist in ast_by_file.items():
        if fpath == "backend/prisma/schema.prisma":
            continue
        layer_obj = classify_node(fpath, {"file": fpath})
        if layer_obj.id == get_registry().utility_id:
            continue

        container = get_or_create_container(fpath, layer_obj.name)
        for n in nlist:
            # When the source graph is the TLDRGraph snapshot rather than raw
            # graphify output it already contains synthesized endpoint nodes.
            # They get their own tier-3 records below, with the endpoint kind and
            # the route metadata; picking them up here would bury them as
            # generic symbols.
            if _is_endpoint_record(n):
                continue
            lbl = n.get("label", "")
            if not lbl or lbl == container["label"]:
                if n.get("intent") and not container["intent"]:
                    container["intent"] = n.get("intent")
                continue

            nid = str(n.get("id"))
            add_subnode(container, {
                "id": subnode_id(nid),
                "graph_node_id": nid,
                "label": lbl,
                "display_label": display_labels.get(nid, lbl),
                "type": "method_symbol",
                "kind": "Service Method" if layer_obj.id == LAYER_SERVICE else "Symbol",
                "file": fpath,
                "layer": layer_obj.name,
                "layer_id": layer_obj.id,
                "source_location": n.get("source_location"),
                "intent": n.get("intent") or n.get("summary") or f"{lbl} in {os.path.basename(fpath)}",
                "input_fields": n.get("input_fields", []),
                "output_fields": n.get("output_fields", []),
                "fields": n.get("fields", []),
                "is_test": container["is_test"] or is_test_node(fpath, lbl)
            })

    # ------------------------------------------------------------------ #
    # 3. Tier-3 elements: endpoints (one per normalized METHOD /path)
    #
    # These replace BOTH the per-call-site "Data Fetch"/"Form / Action" subnodes
    # and the per-declaration "API Endpoint" subnodes. One route, one node.
    # ------------------------------------------------------------------ #
    reg = get_registry()
    api_layer = reg.by_id(LAYER_API) or reg.ordered()[0]
    for endpoint in endpoints:
        container = get_or_create_container(endpoint["file"], api_layer.name)
        handlers = endpoint.get("handlers") or []
        handler_note = f" mapped to {', '.join(handlers)}" if handlers else ""
        add_subnode(container, {
            "id": subnode_id(endpoint["id"]),
            "graph_node_id": endpoint["id"],
            "label": endpoint["label"],
            "display_label": endpoint["label"],
            "type": extractors.ENDPOINT_NODE_TYPE,
            "kind": "API Endpoint",
            "method": endpoint["method"],
            "path": endpoint["path"],
            "handlers": handlers,
            "handler": handlers[0] if handlers else "",
            "call_site_count": len(endpoint.get("call_sites") or []),
            "line": endpoint.get("line"),
            "source_location": f"L{endpoint['line']}" if endpoint.get("line") else None,
            "file": endpoint["file"],
            "layer": api_layer.name,
            "layer_id": api_layer.id,
            "is_test": container["is_test"],
            "intent": f"REST endpoint handling {endpoint['label']}{handler_note}"
        })

    # ------------------------------------------------------------------ #
    # 4. Tier-3 elements: Prisma models
    # ------------------------------------------------------------------ #
    data_layer = reg.by_id(LAYER_DATA) or reg.utility
    prisma_container = get_or_create_container(
        "backend/prisma/schema.prisma", data_layer.name, label="schema.prisma"
    )
    for model_info in prisma_models:
        model_name = model_info.get("name", "")
        if not model_name:
            continue
        add_subnode(prisma_container, {
            "id": f"sub_db_{model_name.lower()}",
            "graph_node_id": extractors.prisma_model_node_id(model_name),
            "label": f"Model: {model_name}",
            "display_label": f"Model: {model_name}",
            "model_name": model_name,
            "type": "db_model",
            "kind": "Database Table",
            "file": "backend/prisma/schema.prisma",
            "layer": data_layer.name,
            "layer_id": data_layer.id,
            "fields": model_info.get("fields", []) if isinstance(model_info, dict) else [],
            "is_test": False,
            "intent": f"PostgreSQL database table storing {model_name} records and relations"
        })

    # ------------------------------------------------------------------ #
    # 5. Connect tier-3 elements to containers
    # ------------------------------------------------------------------ #
    # Outgoing calls from AST symbols to endpoints/services
    for fpath, nlist in ast_by_file.items():
        src_cid = container_id(fpath)
        if src_cid not in containers:
            continue
        for n in nlist:
            nid = str(n.get("id"))
            sub_id = subnode_id(nid)
            if sub_id not in subnode_map:
                continue

            for target in (n.get("calls") or []):
                # Target could be an endpoint or another symbol
                tgt_cid = None
                for c in containers.values():
                    for s in c["subnodes"]:
                        if s.get("label") == target or s.get("display_label") == target:
                            tgt_cid = c["id"]
                            break
                    if tgt_cid:
                        break
                if tgt_cid and tgt_cid != src_cid:
                    containers[src_cid]["out_containers"].add(tgt_cid)
                    containers[tgt_cid]["in_containers"].add(src_cid)

    # ------------------------------------------------------------------ #
    # 5. Edges
    # ------------------------------------------------------------------ #
    edges: List[Dict[str, Any]] = []
    seen_edges: Set[Tuple[str, str, str, str, str]] = set()

    def add_edge(src_c: Dict[str, Any], tgt_c: Dict[str, Any],
                 src_sub: Optional[str], tgt_sub: Optional[str],
                 relation: str, label: str, **extra: Any) -> None:
        key = (src_c["id"], tgt_c["id"], src_sub or "", tgt_sub or "", relation)
        if key in seen_edges:
            return
        seen_edges.add(key)
        src_c["out_containers"].add(tgt_c["id"])
        tgt_c["in_containers"].add(src_c["id"])
        edges.append({
            "source_container": src_c["id"],
            "target_container": tgt_c["id"],
            "source_subnode": src_sub,
            "target_subnode": tgt_sub,
            "relation": relation,
            "label": label,
            **extra
        })

    def resolve_element(file_path: str, line: int) -> Optional[str]:
        """The subnode owning a source line, when one exists."""
        owner = ast_index.owner_of(file_path, line)
        if not owner:
            return None
        sid = subnode_id(owner)
        return sid if sid in subnode_map else None

    # 5a. caller -> endpoint -> handler. This is the chain the whole phase is
    # about: a component reaches a service by way of an endpoint.
    for endpoint in endpoints:
        ep_sub = subnode_id(endpoint["id"])
        ep_container = containers[container_id(endpoint["file"])]

        for call in endpoint.get("call_sites") or []:
            caller_layer = classify_node(call["file"], {"file": call["file"]})
            caller_container = get_or_create_container(call["file"], caller_layer.name)
            add_edge(
                caller_container, ep_container,
                resolve_element(call["file"], call["line"]), ep_sub,
                extractors.CALLS_ENDPOINT_RELATION, endpoint["label"],
                method=endpoint["method"], path=endpoint["path"],
                call_line=call["line"],
            )

        for route in endpoint.get("routes") or []:
            handler_node = extractors.resolve_route_handler(ast_index, route)
            if not handler_node:
                continue
            handler_sub = subnode_id(handler_node)
            if handler_sub not in subnode_map:
                continue
            handler_container = containers.get(subnode_map[handler_sub]["container_id"])
            if not handler_container:
                continue
            add_edge(
                ep_container, handler_container, ep_sub, handler_sub,
                extractors.HANDLED_BY_RELATION,
                route.get("handler") or endpoint["label"],
                method=endpoint["method"], path=endpoint["path"],
            )

    # 5b. Service -> DB Prisma links (unchanged behaviour)
    known_models = {m["name"].lower(): m["name"] for m in prisma_models if "name" in m}
    relation_map = extractors.build_relation_map(prisma_models)
    prisma_calls = extractors.collect_prisma_calls(root_dir, known_models, relation_map)

    for pcall in prisma_calls:
        fpath = pcall["file"]
        model_name = pcall["model"]
        src_c = get_or_create_container(fpath, layer_name(LAYER_SERVICE) or get_registry().utility.name)
        add_edge(
            src_c, prisma_container,
            f"sub_{_slug(fpath)}_L{pcall['line']}", f"sub_db_{model_name.lower()}",
            "db_model_link", f"prisma.{model_name}.{pcall.get('op', 'query')}()",
            model=model_name, op=pcall.get("op", "query"),
        )

    # 5c. Inter-module AST links.
    #
    # The deterministic seams are skipped here even though the TLDRGraph
    # snapshot carries them: 5a and 5b already re-derived them from source, and
    # letting the snapshot add its own copies would make this function's output
    # depend on whether it happened to read graphify-out/ or .tldrgraph/.
    for e in ast_edges:
        if str(e.get("relation") or "") in _REDERIVED_RELATIONS:
            continue
        src_n = ast_node_map.get(str(e.get("source")))
        tgt_n = ast_node_map.get(str(e.get("target")))
        if not (src_n and tgt_n):
            continue
        if _is_endpoint_record(src_n) or _is_endpoint_record(tgt_n):
            continue
        src_f = src_n.get("file") or ""
        tgt_f = tgt_n.get("file") or ""
        if not (src_f and tgt_f) or src_f == tgt_f:
            continue
        src_c = get_or_create_container(src_f)
        tgt_c = get_or_create_container(tgt_f)
        relation = e.get("relation", "calls")
        add_edge(
            src_c, tgt_c,
            subnode_id(str(src_n.get("id"))), subnode_id(str(tgt_n.get("id"))),
            relation, relation,
        )

    # ------------------------------------------------------------------ #
    # 6. Tier 1 -> tier 2 containment: page owns the components it imports
    # ------------------------------------------------------------------ #
    page_contains = _link_pages_to_components(containers, ast_edges, ast_node_map)
    for page_c, component_c in page_contains:
        add_edge(page_c, component_c, None, None,
                 PAGE_CONTAINS_RELATION, f"renders {component_c['label']}")

    # ------------------------------------------------------------------ #
    # 7. Container display labels -- same collision problem, one tier up.
    # ------------------------------------------------------------------ #
    container_display = build_display_labels(
        [{"id": c["id"],
          "label": page_route(c["file"]) if c["tier"] == TIER_PAGE else c["label"],
          "file": c["file"]}
         for c in containers.values()],
        (),
    )
    for c in containers.values():
        c["display_label"] = container_display.get(c["id"]) or c["label"]

    # ------------------------------------------------------------------ #
    # 8. Serialize
    # ------------------------------------------------------------------ #
    serializable_containers = []
    for c in containers.values():
        serializable_containers.append({
            "id": c["id"],
            "label": c["label"],
            "display_label": c.get("display_label") or c["label"],
            "file": c["file"],
            "layer": c["layer"],
            "layer_id": c["layer_id"],
            "tier": c["tier"],
            "is_test": c.get("is_test", False),
            # Full containment (a DAG). `parent_container` is the deterministic
            # primary for single-parent consumers -- see the module docstring.
            "parent_containers": list(c["parent_containers"]),
            "parent_container": c["parent_containers"][0] if c["parent_containers"] else None,
            "child_containers": sorted(c["child_containers"]),
            "depth": 1 if c["parent_containers"] else 0,
            "shared": c["shared"],
            "intent": c["intent"] or f"Module {c['label']} in {c['layer']}",
            "input_fields": c.get("input_fields", []),
            "output_fields": c.get("output_fields", []),
            "fields": c.get("fields", []),
            "subnode_count": len(c["subnodes"]),
            "subnodes": c["subnodes"],
            "out_count": len(c["out_containers"]),
            "in_count": len(c["in_containers"]),
            "out_containers": sorted(c["out_containers"]),
            "in_containers": sorted(c["in_containers"])
        })

    tier_counts: Dict[str, int] = {}
    for c in serializable_containers:
        tier_counts[c["tier"]] = tier_counts.get(c["tier"], 0) + 1

    return {
        "schema": HIERARCHY_SCHEMA,
        "tiers": [TIER_PAGE, TIER_COMPONENT, TIER_ELEMENT],
        "multi_parent_rule": MULTI_PARENT_RULE,
        "containers": serializable_containers,
        "edges": edges,
        "stats": {
            "containers": len(serializable_containers),
            "elements": len(subnode_map),
            "edges": len(edges),
            "endpoints": len(endpoints),
            "containers_by_tier": tier_counts,
        },
    }


def _link_pages_to_components(containers: Dict[str, Dict[str, Any]],
                              ast_edges: List[Dict[str, Any]],
                              ast_node_map: Dict[str, Dict[str, Any]]
                              ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Derive page -> component containment from the AST import edges.

    graphify emits one import edge per imported *symbol*, so
    ``admin/page.tsx -> ChargeAssignmentsEditor.tsx`` appears five times. Pairs
    are deduplicated by ``(source_file, target_file)`` before anything is decided.

    Mutates the container records in place (``tier``, ``parent_container``,
    ``child_containers``, ``imported_by``, ``shared``) and returns the deduped
    (page, component) pairs so the caller can emit one edge each. See the module
    docstring for the shared-component rule.
    """
    reg = get_registry()
    ui_id = LAYER_UI if LAYER_UI in reg else reg.ordered()[0].id
    pairs: Set[Tuple[str, str]] = set()

    for e in ast_edges:
        if str(e.get("relation") or "") not in IMPORT_RELATIONS:
            continue
        src_n = ast_node_map.get(str(e.get("source")))
        tgt_n = ast_node_map.get(str(e.get("target")))
        if not (src_n and tgt_n):
            continue
        src_f = src_n.get("file") or ""
        tgt_f = tgt_n.get("file") or ""
        if not (src_f and tgt_f) or src_f == tgt_f:
            continue
        if not is_page_file(src_f) or is_page_file(tgt_f):
            continue
        src_c = containers.get(container_id(src_f))
        tgt_c = containers.get(container_id(tgt_f))
        if not src_c or not tgt_c:
            continue
        # Both ends must really be UI; a page importing a shared type from a
        # utility module is not rendering a component.
        if src_c.get("layer_id") != ui_id or tgt_c.get("layer_id") != ui_id:
            continue
        pairs.add((src_f, tgt_f))

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
