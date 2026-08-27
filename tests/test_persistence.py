"""
Defect 2 -- nothing is persisted.

The graph, and in particular the expensively-derived cross-layer bridge edges,
live only in the process that built them. ``export_yaml`` writes counts plus a
50-node sample per layer and zero edges, so a second CLI invocation starts from
nothing.

Contract under test:
  * a scan writes ``.tldrgraph/graph.json``
  * that snapshot holds EVERY node id and EVERY edge, not a sample
  * ``save_graph()`` / ``load_graph_snapshot()`` round-trip
  * on re-scan, bridge edges + intent + fields + non-placeholder summaries are
    carried forward; ``rebuild=True`` drops all of it
"""

import json

import pytest

from tldrgraph import __version__
from tldrgraph import graph_loader as gl_mod
from tldrgraph.graph_loader import GraphLoader


SENTINEL_INTENT = "Computes commuted pension value for a sanctioned case."
SENTINEL_FIELDS = ["basicPay", "commutationFactor", "qualifyingService"]


def _snapshot(mini_repo) -> dict:
    return json.loads(mini_repo.snapshot_path.read_text(encoding="utf-8"))


def _edge_triples(graph):
    return {(u, v, d.get("relation")) for u, v, d in graph.edges(data=True)}


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_bridge_relations_constant_exists():
    assert hasattr(gl_mod, "BRIDGE_RELATIONS"), (
        "graph_loader must define a module-level BRIDGE_RELATIONS set"
    )
    assert gl_mod.BRIDGE_RELATIONS == {"llm_cross_layer_link", "cross_layer_link"}


# ---------------------------------------------------------------------------
# Snapshot completeness
# ---------------------------------------------------------------------------

def test_scan_writes_graph_json(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)
    assert mini_repo.snapshot_path.exists(), (
        "a scan must persist .tldrgraph/graph.json"
    )


def test_snapshot_has_schema_envelope(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)
    snap = _snapshot(mini_repo)

    assert snap["tldrgraph_version"] == __version__
    assert snap["schema_version"] == 1
    assert isinstance(snap["built_at"], str) and snap["built_at"]
    assert isinstance(snap["nodes"], list)
    assert isinstance(snap["edges"], list)


def test_snapshot_contains_every_node_not_a_sample(loader, mini_repo, no_network):
    graph = loader.load_or_extract(enrich_llm=True)
    snap = _snapshot(mini_repo)

    snapshot_ids = {n["id"] for n in snap["nodes"]}
    live_ids = set(graph.nodes)
    assert snapshot_ids == live_ids, (
        f"snapshot is missing {sorted(live_ids - snapshot_ids)!r} / "
        f"has extra {sorted(snapshot_ids - live_ids)!r}"
    )


def test_snapshot_node_records_carry_the_contract_keys(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)
    snap = _snapshot(mini_repo)
    by_id = {n["id"]: n for n in snap["nodes"]}

    required = {
        "id", "label", "file", "layer", "type", "community",
        "degree", "summary", "fields", "intent", "source_location",
    }
    for key in mini_repo.node_specs:
        rec = by_id[mini_repo.nid(key)]
        missing = required - set(rec)
        assert not missing, f"node {key!r} snapshot record missing {sorted(missing)!r}"


def test_snapshot_preserves_source_location(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)
    by_id = {n["id"]: n for n in _snapshot(mini_repo)["nodes"]}

    for key, spec in mini_repo.node_specs.items():
        expected_loc = spec[2]
        assert by_id[mini_repo.nid(key)]["source_location"] == expected_loc


def test_snapshot_contains_every_edge(loader, mini_repo, no_network):
    graph = loader.load_or_extract(enrich_llm=True)
    snap = _snapshot(mini_repo)

    snapshot_edges = {(e["source"], e["target"], e["relation"]) for e in snap["edges"]}
    live_edges = _edge_triples(graph)
    assert snapshot_edges == live_edges, (
        f"snapshot is missing edges {sorted(live_edges - snapshot_edges)!r} / "
        f"has extra {sorted(snapshot_edges - live_edges)!r}"
    )


def test_load_graph_snapshot_round_trips(loader, mini_repo, no_network):
    graph = loader.load_or_extract(enrich_llm=True)
    snap = loader.load_graph_snapshot()

    assert snap is not None
    assert {n["id"] for n in snap["nodes"]} == set(graph.nodes)


def test_load_graph_snapshot_returns_none_when_absent(loader, mini_repo):
    assert not mini_repo.snapshot_path.exists()
    assert loader.load_graph_snapshot() is None


def test_save_graph_returns_the_written_path(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=False)
    path = loader.save_graph()
    assert path == str(mini_repo.snapshot_path)


# ---------------------------------------------------------------------------
# Carry-forward across process boundaries
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded(mini_repo):
    """
    Scan once (no enrichment, so nothing is auto-filled), then hand-place a
    bridge edge and genuine enrichment onto the graph and persist it.
    """
    l1 = GraphLoader(str(mini_repo.root))
    l1.load_or_extract(enrich_llm=False)

    src = mini_repo.nid("ui_page")
    tgt = mini_repo.nid("async_poll")   # no AST edge between these two
    assert not l1.graph.has_edge(src, tgt)
    l1.graph.add_edge(src, tgt, relation="llm_cross_layer_link", confidence=0.91)

    svc = mini_repo.nid("svc_pension")
    node = l1.graph.nodes[svc]
    node["intent"] = SENTINEL_INTENT
    node["fields"] = list(SENTINEL_FIELDS)
    node["summary"] = f"{node['layer']}: {node['label']} - {SENTINEL_INTENT}"

    l1.save_graph()
    return {"loader": l1, "bridge": (src, tgt), "svc": svc}


def test_bridge_edge_survives_a_fresh_loader(seeded, mini_repo):
    src, tgt = seeded["bridge"]

    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    assert g2.has_edge(src, tgt), "cross-layer bridge edge was not carried forward"
    assert g2.edges[src, tgt]["relation"] == "llm_cross_layer_link"
    assert g2.edges[src, tgt].get("confidence") == pytest.approx(0.91)


def test_intent_survives_a_fresh_loader(seeded, mini_repo):
    svc = seeded["svc"]

    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    assert g2.nodes[svc]["intent"] == SENTINEL_INTENT


def test_fields_and_real_summary_survive_a_fresh_loader(seeded, mini_repo):
    svc = seeded["svc"]

    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    assert g2.nodes[svc]["fields"] == SENTINEL_FIELDS
    assert SENTINEL_INTENT in g2.nodes[svc]["summary"]
    assert g2.nodes[svc]["summary"] != mini_repo.placeholder_summary("svc_pension")


def test_placeholder_summaries_are_not_carried_forward(seeded, mini_repo):
    """
    A node nobody enriched still has the generated
    "{layer}: {label} located at {file}" summary; carrying it forward is a
    no-op, but it must never be mistaken for real enrichment.
    """
    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    key = "util_format"
    node = g2.nodes[mini_repo.nid(key)]
    assert node["summary"] == mini_repo.placeholder_summary(key)
    assert not node["intent"]


def test_rebuild_drops_bridge_edges_and_intent(seeded, mini_repo):
    src, tgt = seeded["bridge"]
    svc = seeded["svc"]

    l3 = GraphLoader(str(mini_repo.root))
    g3 = l3.load_or_extract(enrich_llm=False, rebuild=True)

    assert not g3.has_edge(src, tgt), "rebuild=True must not carry bridge edges"
    assert not g3.nodes[svc]["intent"], "rebuild=True must not carry intent"
    assert g3.nodes[svc]["fields"] == []
    assert g3.nodes[svc]["summary"] == mini_repo.placeholder_summary("svc_pension")


def test_bridge_edge_with_a_vanished_endpoint_is_dropped(seeded, mini_repo):
    """
    Snapshot edges are only carried forward when both endpoints still exist in
    the freshly-rebuilt node set.
    """
    src, tgt = seeded["bridge"]

    # Hand-edit the snapshot to point at a node graphify no longer emits.
    snap = _snapshot(mini_repo)
    ghost = "node_that_no_longer_exists"
    for edge in snap["edges"]:
        if edge["source"] == src and edge["target"] == tgt:
            edge["target"] = ghost
    mini_repo.snapshot_path.write_text(json.dumps(snap), encoding="utf-8")

    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    assert not g2.has_node(ghost)
    assert not g2.has_edge(src, ghost)


def test_non_bridge_snapshot_edges_are_not_resurrected(seeded, mini_repo):
    """
    Only relations in BRIDGE_RELATIONS carry forward. AST edges are rebuilt
    from graphify, so a stale 'calls' edge in the snapshot must not survive.
    """
    snap = _snapshot(mini_repo)
    stale_src = mini_repo.nid("util_format")
    stale_tgt = mini_repo.nid("data_prisma")
    snap["edges"].append(
        {"source": stale_src, "target": stale_tgt, "relation": "calls", "confidence": 1.0}
    )
    mini_repo.snapshot_path.write_text(json.dumps(snap), encoding="utf-8")

    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)

    assert not g2.has_edge(stale_src, stale_tgt), (
        "a stale non-bridge edge from the snapshot was resurrected"
    )
