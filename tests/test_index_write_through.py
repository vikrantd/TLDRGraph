"""
Defect 3 -- enrichment never reaches the index.

``graph.add_node(id, **attrs)`` copies the attribute dict into networkx, so the
dicts collected in ``docs_to_index`` / ``nodes_by_layer`` are a different object
from ``graph.nodes[nid]``. Enrichment mutates the latter; indexing consumes the
former. Measured on the real repo: 0 of 2371 indexed documents carried an
``intent``.

Second half of the same defect: bridge resolution calls
``vector_store.search()`` during the enrichment pass, but on a fresh directory
the index has not been populated yet -- so every lookup returns [] and no bridge
is ever created on a first scan.

Contract under test:
  * indexed documents pick up enrichment (live attribute dicts)
  * enrichment text is actually searchable
  * bridges resolve on a completely fresh scan
  * matches below a 0.35 score floor create no edge; the score is recorded as
    the edge's ``confidence``
"""

import json

import pytest

from tldrgraph import graph_loader as gl_mod
from tldrgraph.graph_loader import GraphLoader


SCORE_FLOOR = 0.35
GIBBERISH = "qqqzzzxxwwvvuu"


def _index(mini_repo) -> dict:
    return json.loads(mini_repo.index_path.read_text(encoding="utf-8"))


def _bridges(graph):
    relations = getattr(gl_mod, "BRIDGE_RELATIONS", {"llm_cross_layer_link", "cross_layer_link"})
    return [
        (u, v, d) for u, v, d in graph.edges(data=True) if d.get("relation") in relations
    ]


# ---------------------------------------------------------------------------
# Enrichment reaching the index
# ---------------------------------------------------------------------------

def test_vector_index_file_is_written(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)
    assert mini_repo.index_path.exists()


def test_indexed_documents_carry_intent(loader, mini_repo, no_network):
    """The headline symptom: every indexed doc had an empty intent."""
    loader.load_or_extract(enrich_llm=True)

    docs = _index(mini_repo)["documents"]
    assert docs, "vector index has no documents"

    with_intent = [d for d in docs if d.get("intent")]
    assert with_intent, (
        f"0 of {len(docs)} indexed documents have a non-empty 'intent' -- "
        "enrichment is mutating copies, not the indexed dicts"
    )


def test_graph_and_indexed_docs_are_the_same_objects(loader, mini_repo, no_network):
    """
    ``docs_to_index`` must hold the live networkx attribute dicts so that later
    mutations through ``graph.nodes[nid]`` propagate into what gets indexed.
    """
    loader.load_or_extract(enrich_llm=True)

    assert hasattr(loader, "docs_to_index"), (
        "GraphLoader must expose docs_to_index holding the live node attr dicts"
    )

    nid = mini_repo.nid("svc_pension")
    live = loader.graph.nodes[nid]
    doc = next(d for d in loader.docs_to_index if d["id"] == nid)
    assert doc is live, "docs_to_index holds a copy, not the live node attr dict"

    bucket = loader.nodes_by_layer[mini_repo.expected_layer("svc_pension")]
    entry = next(d for d in bucket if d["id"] == nid)
    assert entry is live, "nodes_by_layer holds a copy, not the live node attr dict"


def test_enriched_intent_is_searchable(loader, mini_repo, no_network):
    """
    Put a token that appears nowhere else into a node's intent, re-index, and
    the store must be able to find that node by it.
    """
    loader.load_or_extract(enrich_llm=True)

    nid = mini_repo.nid("svc_pension")
    loader.graph.nodes[nid]["intent"] = "zzqqx sentinel marker for regression search"
    loader.vector_store.add_documents(loader.docs_to_index)

    hits = loader.vector_store.search("zzqqx")
    assert hits, "'zzqqx' placed in a node's intent is not searchable"
    assert nid in [doc["id"] for doc, _ in hits]


def test_hash_gate_records_intent_after_enrichment(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)

    nid = mini_repo.nid("svc_pension")
    signature = loader.node_signature(dict(loader.graph.nodes[nid]))
    is_dirty, cached = loader.hash_gate.check_node(nid, signature)

    assert not is_dirty, "node should be clean immediately after being enriched"
    assert cached is not None
    assert cached.get("intent"), "enrichment did not persist intent into the hash gate"


# ---------------------------------------------------------------------------
# Bridge resolution ordering + score floor
# ---------------------------------------------------------------------------

def test_bridge_is_created_on_a_completely_fresh_scan(mini_repo, stub_enricher, no_network):
    """
    Index must be populated BEFORE enrichment runs, otherwise every
    ``vector_store.search()`` during bridge resolution returns [].
    """
    ui = mini_repo.nid("ui_page")
    target_label = mini_repo.label("svc_pension")  # "PensionCalculatorService"
    stub_enricher(lambda n: [target_label] if n["id"] == ui else [])

    # The state directory now also holds graphify's raw export, so its mere
    # existence proves nothing. What must be absent is TLDRGraph's own state.
    assert not mini_repo.snapshot_path.exists(), "snapshot must be fresh"
    assert not mini_repo.index_path.exists(), "vector index must be fresh"
    assert not mini_repo.db_path.exists(), "hash-gate cache must be fresh"

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    bridges = _bridges(graph)
    assert bridges, (
        "no bridge edge created on a fresh scan -- the vector index is being "
        "populated after enrichment instead of before it"
    )
    assert (ui, mini_repo.nid("svc_pension")) in [(u, v) for u, v, _ in bridges]


def test_bridge_edge_records_the_match_score_as_confidence(
    mini_repo, stub_enricher, no_network
):
    ui = mini_repo.nid("ui_page")
    stub_enricher(lambda n: [mini_repo.label("svc_pension")] if n["id"] == ui else [])

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    bridges = _bridges(graph)
    assert bridges
    for u, v, data in bridges:
        assert "confidence" in data, f"bridge {u}->{v} has no confidence"
        assert isinstance(data["confidence"], (int, float))
        assert data["confidence"] >= SCORE_FLOOR, (
            f"bridge {u}->{v} kept a below-floor match ({data['confidence']})"
        )


def test_gibberish_call_target_creates_no_bridge(mini_repo, stub_enricher, no_network):
    stub_enricher(lambda n: [GIBBERISH])

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    assert _bridges(graph) == [], (
        "a call target matching nothing in the repo produced a bridge edge"
    )


def test_weak_match_below_floor_creates_no_bridge(mini_repo, stub_enricher, no_network):
    """
    A generic token like 'ts' matches many documents with a tiny score. The old
    code took ``top_k=1`` unconditionally; the floor must reject it.
    """
    stub_enricher(lambda n: ["ts"])

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    for u, v, data in _bridges(graph):
        assert data.get("confidence", 0.0) >= SCORE_FLOOR, (
            f"weak match kept: {u}->{v} at {data.get('confidence')}"
        )


def test_bridge_never_self_links(mini_repo, stub_enricher, no_network):
    stub_enricher(lambda n: [n.get("label", "")])

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    for u, v, _ in _bridges(graph):
        assert u != v, f"self-referential bridge edge on {u}"


def test_bridges_land_in_the_persisted_snapshot(mini_repo, stub_enricher, no_network):
    ui = mini_repo.nid("ui_page")
    stub_enricher(lambda n: [mini_repo.label("svc_pension")] if n["id"] == ui else [])

    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=True)

    bridges = {(u, v) for u, v, _ in _bridges(graph)}
    assert bridges

    snap = json.loads(mini_repo.snapshot_path.read_text(encoding="utf-8"))
    relations = getattr(gl_mod, "BRIDGE_RELATIONS", {"llm_cross_layer_link", "cross_layer_link"})
    persisted = {
        (e["source"], e["target"]) for e in snap["edges"] if e["relation"] in relations
    }
    assert bridges <= persisted, f"bridges missing from snapshot: {bridges - persisted}"
