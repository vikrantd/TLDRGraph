"""
Regression tests for the TLDRGraph flow/query surface (``tldrgraph.flow_engine``).

Everything here is hermetic: a small synthetic graph plus a real
``LocalVectorStore`` written under pytest's ``tmp_path``. Nothing reads the real
repository or the real ``.tldrgraph/`` state, and
nothing makes a network call. Fixtures are defined locally in this file on
purpose -- the shared ``conftest.py`` is owned elsewhere.

The synthetic graph deliberately mirrors the shape that broke in production:

    DeskView.tsx                (Layer 1, *file* node -- wins a bare-identifier
     |                           vector query because its document is short)
     +-- DeskView()             (Layer 1, *symbol* node -- owns the bridge)
     |     +== cross_layer_link ==> OrdersController   (Layer 2)
     |                                +== cross_layer_link ==> OrdersService (Layer 3)
     |                                       +== cross_layer_link ==> PrismaService (Layer 4)
     +-- ~10 same-layer sibling symbols (the noise that used to fill the queue)
"""

import os
import sys
from pathlib import Path

import networkx as nx
import pytest

# --------------------------------------------------------------------------- #
# Import hygiene: make sure `tldrgraph` resolves to the source tree under test
# even when pytest is launched from the repo root (see conftest for the why).
# --------------------------------------------------------------------------- #
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)
if _PKG_PARENT in sys.path:
    sys.path.remove(_PKG_PARENT)
sys.path.insert(0, _PKG_PARENT)

from tldrgraph import flow_engine as fe  # noqa: E402
from tldrgraph.classifier import LayerType  # noqa: E402
from tldrgraph.flow_engine import FlowEngine  # noqa: E402
from tldrgraph.vector_store import LocalVectorStore  # noqa: E402


L1 = LayerType.LAYER_1_UI.value
L2 = LayerType.LAYER_2_API.value
L3 = LayerType.LAYER_3_SERVICE.value
L4 = LayerType.LAYER_4_DATA.value
L6 = LayerType.LAYER_6_DEVOPS.value

UI_FILE = "frontend_src_app_desk_deskview"
UI_SYMBOL = "frontend_src_app_desk_deskview_deskview"
API_CTRL = "backend_src_orders_orders_controller_orderscontroller"
SVC = "backend_src_orders_orders_service_ordersservice"
DATA = "backend_src_prisma_prisma_service_prismaservice"
ORPHAN = "backend_src_reports_reports_service_reportsservice"
DEVOPS = "devops_docker_compose_yml"

UI_FILE_PATH = "frontend/src/app/desk/DeskView.tsx"

#: Same-layer siblings that used to crowd out the bridge in the old flat BFS.
SIBLINGS = [
    "DeskRow", "DeskProps", "DeskHeader", "DeskFooter", "DeskBadge",
    "DeskFilter", "DeskToolbar", "DeskEmptyState", "DeskSpinner", "DeskLegend",
]


def _node(node_id, label, file_path, layer, node_type="code", intent="", fields=None):
    return {
        "id": node_id,
        "label": label,
        "file": file_path,
        "layer": layer,
        "type": node_type,
        "community": 0,
        "degree": 0,
        "source_location": "L1",
        "summary": f"{layer}: {label} located at {file_path}",
        "fields": fields or [],
        "intent": intent,
    }


def _build_nodes():
    nodes = [
        _node(UI_FILE, "DeskView.tsx", UI_FILE_PATH, L1),
        _node(
            UI_SYMBOL, "DeskView()", UI_FILE_PATH, L1,
            # A realistically long agent intent -- this is exactly what dilutes the
            # symbol's TF-IDF vector and lets the short file document outrank it.
            intent=(
                "Renders the allotment officer desk board and dispatches order "
                "review actions to the backend orders controller over HTTP, "
                "handling pagination, filtering, optimistic updates, csrf token "
                "refresh and toast notifications for every desk mutation."
            ),
            fields=["deskId", "officerId", "status"],
        ),
        _node(API_CTRL, "OrdersController", "backend/src/orders/orders.controller.ts", L2),
        _node(SVC, "OrdersService", "backend/src/orders/orders.service.ts", L3),
        _node(DATA, "PrismaService", "backend/src/prisma/prisma.service.ts", L4),
        _node(ORPHAN, "ReportsService", "backend/src/reports/reports.service.ts", L3),
        _node(DEVOPS, "docker-compose.yml", "docker/docker-compose.yml", L6,
              node_type="devops_config"),
    ]
    for name in SIBLINGS:
        nodes.append(
            _node(f"{UI_FILE}_{name.lower()}", name, UI_FILE_PATH, L1)
        )
    return nodes


@pytest.fixture()
def flow_graph():
    graph = nx.DiGraph()
    for attrs in _build_nodes():
        graph.add_node(attrs["id"], **attrs)

    # The file node contains every symbol declared in it (same layer, cheap noise).
    graph.add_edge(UI_FILE, UI_SYMBOL, relation="contains", confidence=1.0)
    for name in SIBLINGS:
        sid = f"{UI_FILE}_{name.lower()}"
        graph.add_edge(UI_FILE, sid, relation="contains", confidence=1.0)
        # ...and the component calls all of them, same layer.
        graph.add_edge(UI_SYMBOL, sid, relation="calls", confidence=1.0)

    # The one edge that actually leaves Layer 1.
    graph.add_edge(UI_SYMBOL, API_CTRL, relation="cross_layer_link", confidence=0.76)
    graph.add_edge(API_CTRL, SVC, relation="cross_layer_link", confidence=0.85)
    graph.add_edge(SVC, DATA, relation="cross_layer_link", confidence=0.67)
    # ORPHAN and DEVOPS stay unreachable on purpose.
    return graph


@pytest.fixture()
def engine(flow_graph, tmp_path):
    store = LocalVectorStore(str(tmp_path / ".tldrgraph" / "vector_index.json"))
    store.add_documents([dict(data) for _, data in flow_graph.nodes(data=True)])
    return FlowEngine(flow_graph, store, root_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# Preconditions -- the bug this module exists to prevent
# --------------------------------------------------------------------------- #

def test_bare_identifier_query_ranks_the_file_node_first(engine):
    """
    The precondition for every symbol-preference test below: on a bare identifier
    query the *file* node outranks the symbol that owns the bridge.
    """
    hits = engine.vector_store.search("DeskView", top_k=25)
    assert hits, "vector store returned nothing"
    assert hits[0][0]["id"] == UI_FILE

    ranked = [doc["id"] for doc, _ in hits]
    assert ranked.index(UI_SYMBOL) > 0, "symbol must not already be the top hit"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def test_resolve_prefers_symbol_node_over_file_node(engine):
    node_id, _score, data = engine._resolve_node_id("DeskView")
    assert node_id == UI_SYMBOL
    assert data["label"] == "DeskView()"


def test_resolve_accepts_an_exact_node_id_verbatim(engine):
    node_id, score, _ = engine._resolve_node_id(UI_FILE)
    assert node_id == UI_FILE, "an exact node id must never be re-searched"
    assert score == 1.0


def test_resolve_returns_none_for_a_query_that_matches_nothing(engine):
    assert engine._resolve_node_id("zzqqxx no such thing anywhere") is None


def test_symbol_preference_does_not_override_a_much_better_match(engine):
    """
    The preference only re-ranks candidates within ``MATCH_MARGIN`` of the top
    hit. A multi-word query that unambiguously names a container must still
    resolve to that container.
    """
    node_id, _score, _ = engine._resolve_node_id("docker compose devops infrastructure")
    assert node_id == DEVOPS


def test_container_detection_separates_file_nodes_from_symbols(engine):
    graph = engine.graph
    assert engine._is_container_node(UI_FILE, graph.nodes[UI_FILE]) is True
    assert engine._is_container_node(UI_SYMBOL, graph.nodes[UI_SYMBOL]) is False
    assert engine._is_container_node(DEVOPS, graph.nodes[DEVOPS]) is True
    assert engine._is_container_node(API_CTRL, graph.nodes[API_CTRL]) is False


def test_preference_ranks_bridge_owning_symbol_above_its_file(engine):
    assert engine._preference(UI_SYMBOL) > engine._preference(UI_FILE)


# --------------------------------------------------------------------------- #
# Traversal
# --------------------------------------------------------------------------- #

def test_trace_path_crosses_layers_and_reaches_the_bridge_target(engine):
    res = engine.trace_path("DeskView")
    assert res.get("error") is None
    assert res["source"] == UI_SYMBOL

    step_ids = [s["id"] for s in res["steps"]]
    assert API_CTRL in step_ids, "the direct bridge target must appear"
    assert len({s["layer"] for s in res["steps"]}) > 1, "trace must span >1 layer"


def test_trace_path_reaches_the_full_six_layer_chain(engine):
    res = engine.trace_path("DeskView")
    step_ids = [s["id"] for s in res["steps"]]
    for expected in (UI_SYMBOL, API_CTRL, SVC, DATA):
        assert expected in step_ids
    assert res["layers"] == [L1, L2, L3, L4]


def test_cross_layer_edges_are_not_starved_by_same_layer_siblings(engine):
    """
    With a cap smaller than the sibling count the old flat BFS could only ever
    return Layer 1. Bridges are free, so they must still be reached.
    """
    res = engine.trace_path("DeskView", max_steps=4)
    step_ids = [s["id"] for s in res["steps"]]
    assert len(res["steps"]) == 4
    assert API_CTRL in step_ids
    assert SVC in step_ids
    assert DATA in step_ids


def test_steps_are_ordered_layer_one_through_six(engine):
    res = engine.trace_path("DeskView")
    ranks = [fe.FlowEngine._layer_rank(s["layer"]) for s in res["steps"]]
    assert ranks == sorted(ranks), "steps must read Layer 1 -> Layer 6"


def test_max_steps_is_a_parameter_with_a_sane_default(engine):
    assert fe.DEFAULT_MAX_STEPS > 10, "10 cannot fit a 6-layer path"
    assert len(engine.trace_path("DeskView")["steps"]) <= fe.DEFAULT_MAX_STEPS
    assert len(engine.trace_path("DeskView", max_steps=2)["steps"]) == 2


def test_trace_path_accepts_an_exact_node_id_as_the_source(engine):
    res = engine.trace_path(UI_SYMBOL)
    assert res["source"] == UI_SYMBOL
    assert API_CTRL in [s["id"] for s in res["steps"]]


def test_steps_report_the_bridge_they_arrived_through(engine):
    res = engine.trace_path("DeskView")
    by_id = {s["id"]: s for s in res["steps"]}
    assert by_id[API_CTRL]["via_bridge"] is True
    assert by_id[API_CTRL]["via_relation"] == "cross_layer_link"
    assert by_id[API_CTRL]["from"] == UI_SYMBOL
    assert by_id[UI_SYMBOL]["via_bridge"] is False  # the root arrived from nowhere


# --------------------------------------------------------------------------- #
# Targeted traces and honest unreachability
# --------------------------------------------------------------------------- #

def test_trace_path_with_a_reachable_target_returns_the_path(engine):
    res = engine.trace_path(UI_SYMBOL, DATA)
    assert res["target"] == DATA
    assert res["target_reached"] is True
    assert res["length"] == len(res["steps"])
    assert [s["id"] for s in res["steps"]] == [UI_SYMBOL, API_CTRL, SVC, DATA]


def test_unreachable_target_is_reported_not_silently_swallowed(engine):
    res = engine.trace_path(UI_SYMBOL, ORPHAN)
    assert res["target_reached"] is False
    assert res["requested_target"] == ORPHAN
    assert "note" in res and res["note"]
    # `target` is omitted so cli.py's `res.get("target", "downstream")` degrades
    # to "downstream" instead of claiming a target that was never reached.
    assert "target" not in res
    assert res["steps"], "a downstream trace is still returned as a fallback"


def test_missing_graph_node_raises_no_exception(engine, monkeypatch):
    """``nx.NodeNotFound`` (not just ``NetworkXNoPath``) must be caught."""
    ghost = "node_that_is_not_in_the_graph"
    assert not engine.graph.has_node(ghost)

    real_resolve = engine._resolve_node_id

    def fake_resolve(query):
        if query == "ghost":
            return ghost, 1.0, {}
        return real_resolve(query)

    monkeypatch.setattr(engine, "_resolve_node_id", fake_resolve)

    res = engine.trace_path(UI_SYMBOL, "ghost")
    assert res["target_reached"] is False
    assert res["requested_target"] == ghost


def test_unresolvable_source_returns_an_error_key(engine):
    res = engine.trace_path("zzqqxx no such thing anywhere")
    assert "error" in res


def test_unresolvable_target_returns_an_error_key(engine):
    res = engine.trace_path(UI_SYMBOL, "zzqqxx no such thing anywhere")
    assert "error" in res


# --------------------------------------------------------------------------- #
# query_flow
# --------------------------------------------------------------------------- #

def test_query_flow_traces_the_matched_id_not_the_label(engine):
    """
    The old code called ``trace_path(doc["label"])``, which re-searched by label
    and could trace a *different* node than the one it reported.
    """
    results = engine.query_flow("DeskView", top_k=5)
    assert results
    for res in results:
        # Exactly one step arrived "from nowhere" -- that is the traversal root,
        # and it must be the node the search actually matched.
        roots = [s["id"] for s in res["flow"] if s.get("from") is None]
        assert roots == [res["root_id"]], (
            f"reported root {res['root_id']} is not the node that was traced ({roots})"
        )

    # Distinct matches must produce distinct traversal roots, not all collapse
    # onto whatever the label re-search happened to return.
    assert len({r["root_id"] for r in results}) == len(results)


def test_query_flow_root_id_matches_the_reported_label(engine):
    for res in engine.query_flow("DeskView", top_k=5):
        assert engine.graph.nodes[res["root_id"]]["label"] == res["root_node"]


def test_query_flow_surfaces_cross_layer_flows(engine):
    results = engine.query_flow("DeskView", top_k=5)
    assert any(len(res["layers"]) > 1 for res in results), (
        "at least one candidate flow must leave its own layer"
    )


def test_query_flow_keeps_the_keys_cli_consumes(engine):
    for res in engine.query_flow("DeskView", top_k=3):
        for key in ("match_score", "root_node", "layer", "file", "flow"):
            assert key in res


# --------------------------------------------------------------------------- #
# Public shape / bridge relation wiring
# --------------------------------------------------------------------------- #

def test_bridge_relations_come_from_the_loader_and_tolerate_new_names():
    from tldrgraph.graph_loader import BRIDGE_RELATIONS as LOADER_BRIDGES

    assert set(LOADER_BRIDGES) <= fe.BRIDGE_RELATIONS, "loader bridges must be honoured"
    # The deterministic relations another producer is adding in parallel are
    # prioritised the moment they show up, and their absence is not an error.
    assert {"http_route_link", "db_model_link"} <= fe.BRIDGE_RELATIONS


def test_deterministic_bridge_relation_gets_priority(flow_graph, tmp_path):
    flow_graph.add_edge(UI_SYMBOL, ORPHAN, relation="http_route_link", confidence=1.0)
    store = LocalVectorStore(str(tmp_path / ".tldrgraph" / "vector_index.json"))
    store.add_documents([dict(d) for _, d in flow_graph.nodes(data=True)])
    eng = FlowEngine(flow_graph, store, root_dir=str(tmp_path))

    res = eng.trace_path(UI_SYMBOL, max_steps=3)
    assert ORPHAN in [s["id"] for s in res["steps"]]


def test_trace_path_keeps_the_keys_cli_consumes(engine):
    res = engine.trace_path("DeskView")
    for key in ("source", "length", "steps"):
        assert key in res
    assert res["length"] == len(res["steps"])


def test_format_node_step_shape_is_unchanged(engine):
    step = engine._format_node_step(API_CTRL)
    assert set(step) == {
        "id", "label", "layer_id", "layer", "file", "source_location", "line",
        "is_test", "intent", "input_fields", "output_fields", "fields",
    }
    assert step["label"] == "OrdersController"
    assert step["source_location"] == "L1"
    assert step["line"] == 1


def test_render_markdown_table_still_renders(engine):
    table = FlowEngine.render_markdown_table(engine.trace_path("DeskView")["steps"])
    assert "Component / Symbol" in table
    assert "OrdersController" in table
    assert "backend/src/orders/orders.controller.ts:1" in table


def test_render_markdown_table_omits_line_suffix_when_unknown():
    table = FlowEngine.render_markdown_table([
        {
            "layer": L3,
            "label": "NoLineService",
            "intent": "No line data",
            "file": "backend/src/no-line.service.ts",
            "line": None,
        }
    ])
    assert "backend/src/no-line.service.ts" in table
    assert "backend/src/no-line.service.ts:None" not in table


def test_export_flows_yaml_round_trips(engine, tmp_path):
    import yaml

    flows = engine.query_flow("DeskView", top_k=2)
    out = engine.export_flows_yaml(flows, filename=".tldrgraph/flows.yaml")
    assert os.path.exists(out)
    loaded = yaml.safe_load(Path(out).read_text(encoding="utf-8"))
    assert len(loaded["flows"]) == len(flows)
