"""
Phase 2 regression suite: endpoint identity, display labels, and the 3-tier
page -> component -> element hierarchy.

Everything here is hermetic -- a small repository is materialized under
``tmp_path`` with its own graphify output. Nothing reads the real repo.

The fixture is deliberately shaped around the three defects this phase fixes:

* ``GET /orders`` is called from *two* different component files, so a per-call-
  site model produces two nodes and an identity model produces one;
* two ``.constructor()`` symbols exist in two different classes, so bare labels
  collide and owner-qualified ones must not;
* ``OrderStats`` is imported by two pages, so the multi-parent containment rule
  is exercised rather than assumed.
"""

import json

import pytest

from conftest import write_example_layer_config
from tldrgraph import paths
from tldrgraph import extractors as ex
from tldrgraph import labels as lb
from tldrgraph.graph_loader import GraphLoader
from tldrgraph.hierarchy import (
    HIERARCHY_SCHEMA,
    MULTI_PARENT_RULE,
    PAGE_CONTAINS_RELATION,
    TIER_COMPONENT,
    TIER_ELEMENT,
    TIER_PAGE,
    build_multilayer_hierarchy,
    container_id,
    is_page_file,
    page_route,
    subnode_id,
)
from tldrgraph.layers import LAYER_API, LAYER_UI


ORDERS_PAGE = """\
import { OrderList } from './components/OrderList';
import { OrderStats } from './components/OrderStats';
export default function OrdersPage() { return null; }
"""

DASHBOARD_PAGE = """\
import { OrderStats } from '../orders/components/OrderStats';
export default function DashboardPage() { return null; }
"""

ORDER_LIST = """\
export function OrderList() {
  const load = () => api.get('/orders');
  const add = () => api.post('/orders', {});
  return null;
}
"""

ORDER_STATS = """\
export function OrderStats() {
  const load = () => api.get('/orders');
  return null;
}
"""

ORDERS_CONTROLLER = """\
@Controller('orders')
export class OrdersController {
  constructor(private svc: OrdersService) {}
  @Get()
  async findAll() { return this.svc.findAll() }

  @Post()
  async create(dto) { return this.svc.create(dto) }
}
"""

ORDERS_SERVICE = """\
export class OrdersService {
  constructor() {}
  findAll() { return []; }
  create(dto) { return dto; }
}
"""

FILES = {
    "frontend/src/app/orders/page.tsx": ORDERS_PAGE,
    "frontend/src/app/dashboard/page.tsx": DASHBOARD_PAGE,
    "frontend/src/app/orders/components/OrderList.tsx": ORDER_LIST,
    "frontend/src/app/orders/components/OrderStats.tsx": ORDER_STATS,
    "backend/src/orders/orders.controller.ts": ORDERS_CONTROLLER,
    "backend/src/orders/orders.service.ts": ORDERS_SERVICE,
}

#: (id, label, file, source_location)
AST_NODES = [
    ("ui_orders_page", "OrdersPage()", "frontend/src/app/orders/page.tsx", "L3"),
    ("ui_dashboard_page", "DashboardPage()", "frontend/src/app/dashboard/page.tsx", "L2"),
    ("ui_order_list", "OrderList()", "frontend/src/app/orders/components/OrderList.tsx", "L1"),
    ("ui_order_stats", "OrderStats()", "frontend/src/app/orders/components/OrderStats.tsx", "L1"),
    ("ctl", "OrdersController", "backend/src/orders/orders.controller.ts", "L2"),
    ("ctl_ctor", ".constructor()", "backend/src/orders/orders.controller.ts", "L3"),
    ("ctl_findall", ".findAll()", "backend/src/orders/orders.controller.ts", "L5"),
    ("ctl_create", ".create()", "backend/src/orders/orders.controller.ts", "L8"),
    ("svc", "OrdersService", "backend/src/orders/orders.service.ts", "L1"),
    ("svc_ctor", ".constructor()", "backend/src/orders/orders.service.ts", "L2"),
    ("svc_findall", ".findAll()", "backend/src/orders/orders.service.ts", "L3"),
]

AST_EDGES = [
    ("ui_orders_page", "ui_order_list", "imports"),
    ("ui_orders_page", "ui_order_stats", "imports"),
    # graphify emits one edge per imported symbol -- the duplicate must collapse.
    ("ui_orders_page", "ui_order_stats", "imports_from"),
    ("ui_dashboard_page", "ui_order_stats", "imports"),
    ("ctl", "ctl_ctor", "method"),
    ("ctl", "ctl_findall", "method"),
    ("ctl", "ctl_create", "method"),
    ("svc", "svc_ctor", "method"),
    ("svc", "svc_findall", "method"),
    ("ctl_findall", "svc_findall", "calls"),
]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A hermetic mini repo with graphify output and real source on disk."""
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")

    root = tmp_path / "repo"
    for rel, content in FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    graphify = root / paths.STATE_DIRNAME
    graphify.mkdir(parents=True, exist_ok=True)
    write_example_layer_config(root)
    graph_doc = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": nid, "label": label, "file_type": "code",
             "source_file": src, "source_location": loc, "_origin": "ast"}
            for nid, label, src, loc in AST_NODES
        ],
        "links": [
            {"source": s, "target": t, "relation": r, "confidence_score": 1.0}
            for s, t, r in AST_EDGES
        ],
        "hyperedges": [],
    }
    (graphify / paths.GRAPHIFY_GRAPH_FILENAME).write_text(json.dumps(graph_doc), encoding="utf-8")
    (graphify / paths.GRAPHIFY_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def hierarchy(repo):
    return build_multilayer_hierarchy(str(repo))


@pytest.fixture
def graph(repo):
    return GraphLoader(str(repo)).load_or_extract(enrich_llm=False, rebuild=True)


def containers_by_file(hierarchy_data):
    return {c["file"]: c for c in hierarchy_data["containers"]}


def all_subnodes(hierarchy_data):
    return [s for c in hierarchy_data["containers"] for s in c["subnodes"]]


def edges_of(hierarchy_data, relation):
    return [e for e in hierarchy_data["edges"] if e["relation"] == relation]


# --------------------------------------------------------------------------- #
# 1. Endpoint identity -- the call-site duplication fix
# --------------------------------------------------------------------------- #

def test_endpoint_node_id_is_stable_and_derived_from_the_normalized_route():
    assert ex.endpoint_node_id("get", "/auth/me") == "endpoint_get_auth_me"
    # `del` is an alias, and query strings / template params normalize away, so
    # every spelling of one route lands on one id.
    assert ex.endpoint_node_id("del", "/applications/:param") == \
        ex.endpoint_node_id("delete", "/applications/:param")
    assert ex.endpoint_node_id("get", "/") == "endpoint_get_root"
    assert ex.endpoint_label("get", "/auth/me") == "GET /auth/me"


def test_collect_endpoints_folds_every_call_site_into_one_identity(repo):
    calls = ex.collect_frontend_calls(str(repo))
    routes = ex.collect_backend_routes(str(repo))
    endpoints = {e["id"]: e for e in ex.collect_endpoints(calls, routes)}

    assert set(endpoints) == {"endpoint_get_orders", "endpoint_post_orders"}

    get_orders = endpoints["endpoint_get_orders"]
    # Two different component files call GET /orders. One identity, two sites.
    assert len(get_orders["call_sites"]) == 2
    assert {c["file"] for c in get_orders["call_sites"]} == {
        "frontend/src/app/orders/components/OrderList.tsx",
        "frontend/src/app/orders/components/OrderStats.tsx",
    }
    # source_location points at the *backend* declaration.
    assert get_orders["file"] == "backend/src/orders/orders.controller.ts"
    assert get_orders["line"] == 4
    assert get_orders["handlers"] == ["findAll"]


def test_endpoints_are_first_class_api_layer_nodes_in_the_core_graph(graph):
    node = graph.nodes["endpoint_get_orders"]

    assert node["type"] == ex.ENDPOINT_NODE_TYPE
    assert node["label"] == "GET /orders"
    assert node["layer_id"] == LAYER_API
    assert node["file"] == "backend/src/orders/orders.controller.ts"
    assert node["source_location"] == "L4"
    assert node["call_site_count"] == 2
    assert node["handlers"] == ["findAll"]


def test_core_graph_routes_callers_through_the_endpoint_to_the_handler(graph):
    inbound = {u for u, _, d in graph.in_edges("endpoint_get_orders", data=True)
               if d["relation"] == ex.CALLS_ENDPOINT_RELATION}
    outbound = {v for _, v, d in graph.out_edges("endpoint_get_orders", data=True)
                if d["relation"] == ex.HANDLED_BY_RELATION}

    assert inbound == {"ui_order_list", "ui_order_stats"}
    assert outbound == {"ctl_findall"}
    # ...and the handler still reaches the service through the plain AST edge,
    # so caller -> endpoint -> handler -> service is one connected path.
    assert graph.has_edge("ctl_findall", "svc_findall")


def test_http_route_link_is_preserved_alongside_the_endpoint_seam(graph):
    """
    The direct caller -> handler shortcut is kept, not superseded. The flow
    engine, the dead-code cascade and the existing suite all read it.
    """
    direct = {(u, v) for u, v, d in graph.edges(data=True)
              if d["relation"] == ex.HTTP_ROUTE_RELATION}
    assert ("ui_order_list", "ctl_findall") in direct
    assert ("ui_order_stats", "ctl_findall") in direct


def test_a_route_never_resolves_to_the_endpoint_node_itself(graph):
    """
    Endpoint nodes are recorded at their decorator's line inside the controller,
    so they would win NodeIndex's nearest-declaration lookup. If they leak into
    the index, every route resolves to itself and the handler is never reached.
    """
    for _, target, data in graph.out_edges("endpoint_get_orders", data=True):
        if data["relation"] == ex.HANDLED_BY_RELATION:
            assert not target.startswith(ex.ENDPOINT_NODE_PREFIX)

    for _, target, data in graph.edges(data=True):
        if data["relation"] == ex.HTTP_ROUTE_RELATION:
            assert not target.startswith(ex.ENDPOINT_NODE_PREFIX)


def test_endpoint_nodes_do_not_become_dead_code_candidates(graph):
    """An endpoint nobody calls is framework-mounted, never a deletion candidate."""
    for node_id, data in graph.nodes(data=True):
        if data.get("type") == ex.ENDPOINT_NODE_TYPE:
            assert data["dead_code_status"] in ("live", "entry_point")


def test_hierarchy_has_one_element_per_endpoint_not_one_per_call_site(hierarchy):
    endpoints = [s for s in all_subnodes(hierarchy) if s["kind"] == "API Endpoint"]
    labels = [s["label"] for s in endpoints]

    assert sorted(labels) == ["GET /orders", "POST /orders"]
    assert len(labels) == len(set(labels)), "an endpoint was materialized twice"

    get_orders = next(s for s in endpoints if s["label"] == "GET /orders")
    assert get_orders["id"] == subnode_id("endpoint_get_orders")
    assert get_orders["graph_node_id"] == "endpoint_get_orders"
    assert get_orders["call_site_count"] == 2

    inbound = [e for e in edges_of(hierarchy, ex.CALLS_ENDPOINT_RELATION)
               if e["target_subnode"] == get_orders["id"]]
    assert len(inbound) == 2, "each caller needs its own edge into the one endpoint"


def test_per_call_site_fetch_subnodes_are_gone(hierarchy):
    """
    The old shape emitted a "Data Fetch"/"Form / Action" node per call site --
    that is what produced 19 copies of GET /auth/me. They must not come back.
    """
    kinds = {s["kind"] for s in all_subnodes(hierarchy)}
    assert "Data Fetch" not in kinds
    assert "Form / Action" not in kinds


def test_hierarchy_endpoint_connects_component_container_to_handler(hierarchy):
    by_file = containers_by_file(hierarchy)
    component = by_file["frontend/src/app/orders/components/OrderList.tsx"]
    controller = by_file["backend/src/orders/orders.controller.ts"]
    endpoint_sub = subnode_id("endpoint_get_orders")

    calls = [e for e in edges_of(hierarchy, ex.CALLS_ENDPOINT_RELATION)
             if e["source_container"] == component["id"]]
    assert {e["target_subnode"] for e in calls} == {
        endpoint_sub, subnode_id("endpoint_post_orders")}
    assert all(e["target_container"] == controller["id"] for e in calls)
    # The caller end is attributed to a real element, not just to the file.
    assert all(e["source_subnode"] == subnode_id("ui_order_list") for e in calls)

    handled = [e for e in edges_of(hierarchy, ex.HANDLED_BY_RELATION)
               if e["source_subnode"] == endpoint_sub]
    assert [e["target_subnode"] for e in handled] == [subnode_id("ctl_findall")]


# --------------------------------------------------------------------------- #
# 2. Display labels -- the collision fix
# --------------------------------------------------------------------------- #

def test_qualify_reuses_a_leading_dot_as_the_member_separator():
    assert lb.qualify(".constructor()", "AuditService") == "AuditService.constructor()"
    assert lb.qualify("emptyForm", "CaseModal") == "CaseModal.emptyForm"
    assert lb.qualify(".run()", "") == ".run()"
    assert lb.qualify("", "AuditService") == "AuditService"


def test_display_labels_disambiguate_colliding_constructors(hierarchy):
    subs = {s["id"]: s for s in all_subnodes(hierarchy)}
    controller_ctor = subs[subnode_id("ctl_ctor")]
    service_ctor = subs[subnode_id("svc_ctor")]

    assert controller_ctor["display_label"] == "OrdersController.constructor()"
    assert service_ctor["display_label"] == "OrdersService.constructor()"


def test_bare_label_is_never_rewritten(hierarchy, graph):
    """
    HARD GATE. The vector store indexes ``label`` and agent-supplied ``calls``
    targets are matched against it as a bare identifier. Qualifying in place
    would silently move every score in the corpus.
    """
    subs = {s["id"]: s for s in all_subnodes(hierarchy)}
    assert subs[subnode_id("ctl_ctor")]["label"] == ".constructor()"
    assert subs[subnode_id("svc_ctor")]["label"] == ".constructor()"
    assert subs[subnode_id("svc")]["label"] == "OrdersService"

    for node_id, label, _file, _loc in AST_NODES:
        assert graph.nodes[node_id]["label"] == label


def test_every_element_display_label_is_unique(hierarchy):
    display = [s["display_label"] for s in all_subnodes(hierarchy)]
    duplicates = {d for d in display if display.count(d) > 1}
    assert not duplicates, f"colliding display labels: {sorted(duplicates)}"


def test_display_labels_fall_back_to_the_path_when_there_is_no_owner():
    """Two same-named symbols in same-named files still separate."""
    nodes = [
        {"id": "a", "label": "helper", "file": "backend/src/x/util.ts"},
        {"id": "b", "label": "helper", "file": "backend/src/y/util.ts"},
        {"id": "c", "label": "unique", "file": "backend/src/z/util.ts"},
    ]
    display = lb.build_display_labels(nodes, [])
    assert display["c"] == "unique", "an unambiguous label must be left alone"
    assert display["a"] != display["b"]
    assert "util.ts" in display["a"] and "x" in display["a"]


def test_snapshot_persists_display_label_next_to_label(repo):
    loader = GraphLoader(str(repo))
    loader.load_or_extract(enrich_llm=False, rebuild=True)
    snapshot = json.loads((repo / ".tldrgraph/graph.json").read_text(encoding="utf-8"))

    by_id = {n["id"]: n for n in snapshot["nodes"]}
    assert by_id["ctl_ctor"]["label"] == ".constructor()"
    assert by_id["ctl_ctor"]["display_label"] == "OrdersController.constructor()"
    assert all("display_label" in n for n in snapshot["nodes"])


# --------------------------------------------------------------------------- #
# 3. The page -> component -> element tier
# --------------------------------------------------------------------------- #

def test_is_page_file_recognizes_the_next_conventions():
    assert is_page_file("frontend/src/app/orders/page.tsx")
    assert is_page_file("frontend/src/app/layout.tsx")
    assert not is_page_file("frontend/src/app/orders/components/OrderList.tsx")
    assert not is_page_file("backend/src/orders/orders.controller.ts")


def test_page_route_gives_a_page_its_url_instead_of_page_tsx():
    assert page_route("frontend/src/app/applications/aao-desk/page.tsx") == \
        "/applications/aao-desk"
    assert page_route("frontend/src/app/page.tsx") == "/"
    assert page_route("frontend/src/app/layout.tsx") == "/ (layout)"


def test_schema_declares_the_three_tiers_and_the_containment_rule(hierarchy):
    assert hierarchy["schema"] == HIERARCHY_SCHEMA
    assert hierarchy["tiers"] == [TIER_PAGE, TIER_COMPONENT, "element"]
    assert hierarchy["multi_parent_rule"] == MULTI_PARENT_RULE
    assert hierarchy["stats"]["endpoints"] == 2
    assert all(s["tier"] == TIER_ELEMENT for s in all_subnodes(hierarchy))


def test_a_page_owns_the_components_it_imports(hierarchy):
    by_file = containers_by_file(hierarchy)
    page = by_file["frontend/src/app/orders/page.tsx"]
    order_list = by_file["frontend/src/app/orders/components/OrderList.tsx"]

    assert page["tier"] == TIER_PAGE
    assert page["display_label"] == "/orders"
    assert order_list["tier"] == TIER_COMPONENT
    assert order_list["parent_containers"] == [page["id"]]
    assert order_list["parent_container"] == page["id"]
    assert order_list["shared"] is False
    assert order_list["depth"] == 1
    assert order_list["id"] in page["child_containers"]


def test_a_component_imported_by_two_pages_has_both_parents(hierarchy):
    """
    The documented rule: containment is a DAG. A shared component belongs to
    every page that imports it, and ``parent_container`` is the deterministic
    primary drawn from a list the consumer can see in full.
    """
    by_file = containers_by_file(hierarchy)
    stats = by_file["frontend/src/app/orders/components/OrderStats.tsx"]
    orders_page = by_file["frontend/src/app/orders/page.tsx"]
    dashboard_page = by_file["frontend/src/app/dashboard/page.tsx"]

    assert stats["shared"] is True
    assert stats["parent_containers"] == sorted([orders_page["id"], dashboard_page["id"]])
    assert stats["parent_container"] == stats["parent_containers"][0]
    assert stats["id"] in orders_page["child_containers"]
    assert stats["id"] in dashboard_page["child_containers"]


def test_page_contains_edges_are_deduped_by_file_pair(hierarchy):
    """
    graphify emits one import edge per imported *symbol*, so the fixture's
    page -> OrderStats appears twice. Containment must count it once.
    """
    page_id = container_id("frontend/src/app/orders/page.tsx")
    stats_id = container_id("frontend/src/app/orders/components/OrderStats.tsx")

    matching = [e for e in edges_of(hierarchy, PAGE_CONTAINS_RELATION)
                if e["source_container"] == page_id and e["target_container"] == stats_id]
    assert len(matching) == 1
    # Three pairs in total: page->list, page->stats, dashboard->stats.
    assert len(edges_of(hierarchy, PAGE_CONTAINS_RELATION)) == 3


def test_containment_never_points_back_at_a_page(hierarchy):
    by_id = {c["id"]: c for c in hierarchy["containers"]}
    for container in hierarchy["containers"]:
        for parent in container["parent_containers"]:
            assert by_id[parent]["tier"] == TIER_PAGE


def test_backend_files_are_modules_not_components(hierarchy):
    by_file = containers_by_file(hierarchy)
    controller = by_file["backend/src/orders/orders.controller.ts"]
    assert controller["tier"] == "module"
    assert controller["parent_containers"] == []


# --------------------------------------------------------------------------- #
# 4. Structural invariants the renderer depends on
# --------------------------------------------------------------------------- #

def test_ids_are_unique_and_every_reference_resolves(hierarchy):
    container_ids = [c["id"] for c in hierarchy["containers"]]
    subnode_ids = [s["id"] for s in all_subnodes(hierarchy)]

    assert len(container_ids) == len(set(container_ids))
    assert len(subnode_ids) == len(set(subnode_ids))

    known_containers = set(container_ids)
    for s in all_subnodes(hierarchy):
        assert s["container_id"] in known_containers
    for e in hierarchy["edges"]:
        assert e["source_container"] in known_containers
        assert e["target_container"] in known_containers

    for c in hierarchy["containers"]:
        for child in c["child_containers"]:
            assert child in known_containers
        for parent in c["parent_containers"]:
            assert parent in known_containers


def test_parent_and_child_links_are_symmetric(hierarchy):
    by_id = {c["id"]: c for c in hierarchy["containers"]}
    for c in hierarchy["containers"]:
        for parent in c["parent_containers"]:
            assert c["id"] in by_id[parent]["child_containers"]
        for child in c["child_containers"]:
            assert c["id"] in by_id[child]["parent_containers"]


def test_reading_the_snapshot_neither_doubles_seams_nor_buries_endpoints(repo):
    """
    ``build_multilayer_hierarchy`` prefers ``.tldrgraph/graph.json`` over
    graphify's raw export when it exists, and that snapshot carries both
    the synthesized endpoint nodes and the re-derivable seam edges. Reading it
    must not emit the seams twice, and must not fold endpoints back into the
    generic symbol tier.

    Only the *derived* structure is compared. The snapshot is a ``DiGraph``, so
    it cannot hold two relations between the same pair -- an unrelated,
    pre-existing property that makes a byte-for-byte edge comparison meaningless.
    """
    from_graphify = build_multilayer_hierarchy(str(repo))

    GraphLoader(str(repo)).load_or_extract(enrich_llm=False, rebuild=True)
    assert (repo / ".tldrgraph/graph.json").exists()
    from_snapshot = build_multilayer_hierarchy(str(repo))

    def shape(data):
        seams = {
            relation: sorted(
                (e["source_subnode"] or "", e["target_subnode"] or "")
                for e in edges_of(data, relation)
            )
            for relation in (ex.CALLS_ENDPOINT_RELATION, ex.HANDLED_BY_RELATION,
                             ex.DB_MODEL_RELATION, PAGE_CONTAINS_RELATION)
        }
        return (
            sorted((c["id"], c["tier"], c["subnode_count"]) for c in data["containers"]),
            sorted((s["id"], s["kind"], s["display_label"]) for s in all_subnodes(data)),
            seams,
        )

    assert shape(from_graphify) == shape(from_snapshot)
    # And the raw seam relation is never copied out of the snapshot on top of
    # the freshly derived one.
    assert not edges_of(from_snapshot, ex.HTTP_ROUTE_RELATION)


def test_layer_ids_are_carried_alongside_display_names(hierarchy):
    """Phase 0: logic keys off the stable id, never the display string."""
    by_file = containers_by_file(hierarchy)
    assert by_file["frontend/src/app/orders/page.tsx"]["layer_id"] == LAYER_UI
    assert by_file["backend/src/orders/orders.controller.ts"]["layer_id"] == LAYER_API

    endpoint = next(s for s in all_subnodes(hierarchy) if s["kind"] == "API Endpoint")
    assert endpoint["layer_id"] == LAYER_API


def test_is_test_identifier_present_on_containers_and_subnodes(hierarchy):
    """Every container and subnode carries is_test boolean identifier."""
    for c in hierarchy["containers"]:
        assert "is_test" in c
        assert isinstance(c["is_test"], bool)
        for s in c.get("subnodes", []):
            assert "is_test" in s
            assert isinstance(s["is_test"], bool)
