"""
Defect 1 -- the enrichment run crashes.

``graph_loader._run_llm_enrichment`` ends with copy-pasted tail code that
references an out-of-scope ``docs_to_index``, so any scan that actually enters
the enrichment branch dies with ``NameError``. These tests pin the basic
contract: a scan of a fresh directory with enrichment on must complete, produce
a populated graph, expose all six layers, and write ``layers.yaml``.
"""

import yaml

from codechakra.classifier import LayerType


SIX_LAYERS = [
    LayerType.LAYER_1_UI.value,
    LayerType.LAYER_2_API.value,
    LayerType.LAYER_3_SERVICE.value,
    LayerType.LAYER_4_DATA.value,
    LayerType.LAYER_5_ASYNC.value,
    LayerType.LAYER_6_DEVOPS.value,
]


def test_scan_with_enrichment_does_not_raise(loader, no_network):
    """A fresh scan with enrich_llm=True must not blow up (currently NameError)."""
    graph = loader.load_or_extract(enrich_llm=True)

    assert graph.number_of_nodes() > 0, "scan produced no nodes"
    assert graph.number_of_edges() > 0, "scan produced no edges"


def test_scan_without_enrichment_also_works(loader):
    graph = loader.load_or_extract(enrich_llm=False)
    assert graph.number_of_nodes() > 0
    assert graph.number_of_edges() > 0


def test_all_six_layers_present_in_nodes_by_layer(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)

    for layer in SIX_LAYERS:
        assert layer in loader.nodes_by_layer, f"missing layer bucket: {layer!r}"

    # The fixture deliberately places one node in each of the six layers.
    for layer in SIX_LAYERS:
        assert loader.nodes_by_layer[layer], f"layer {layer!r} classified zero nodes"


def test_every_fixture_node_lands_in_its_expected_layer(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)

    for key in mini_repo.node_specs:
        nid = mini_repo.nid(key)
        assert loader.graph.has_node(nid), f"node {key!r} ({nid}) missing from graph"
        assert loader.graph.nodes[nid]["layer"] == mini_repo.expected_layer(key), (
            f"{key!r} classified as {loader.graph.nodes[nid]['layer']!r}"
        )


def test_layers_yaml_is_written(loader, mini_repo, no_network):
    loader.load_or_extract(enrich_llm=True)

    yaml_path = mini_repo.codechakra_dir / "layers.yaml"
    assert yaml_path.exists(), "scan did not write .codechakra/layers.yaml"

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["total_nodes"] == loader.graph.number_of_nodes()
    assert data["total_edges"] == loader.graph.number_of_edges()
    for layer in SIX_LAYERS:
        assert layer in data["layers"]


def test_ast_edges_from_graphify_survive_the_scan(loader, mini_repo, no_network):
    graph = loader.load_or_extract(enrich_llm=True)

    for src_key, tgt_key, relation in mini_repo.edge_specs:
        src, tgt = mini_repo.nid(src_key), mini_repo.nid(tgt_key)
        assert graph.has_edge(src, tgt), f"lost AST edge {src_key} -> {tgt_key}"
        assert graph.edges[src, tgt]["relation"] == relation
