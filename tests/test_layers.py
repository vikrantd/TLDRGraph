"""
The layer registry, and the proof that layer *identity* is decoupled from layer
*display text*.

The centrepiece is ``test_renaming_display_names_changes_nothing_but_the_text``:
it renames every layer's display string and asserts that classification, flow
ordering, heuristic enrichment branching and dead-code utility handling all
behave exactly as before. If a rename ever changes behaviour again, that test
fails and the coupling is back.
"""

import networkx as nx
import pytest

from tldrgraph.classifier import LayerType, classify_node
from tldrgraph.deadcode import compute_enrichment_coverage
from tldrgraph.flow_engine import FlowEngine
from tldrgraph.graph_loader import GraphLoader
from tldrgraph.layers import (
    LAYER_API,
    LAYER_ASYNC,
    LAYER_DATA,
    LAYER_DEVOPS,
    LAYER_SERVICE,
    LAYER_UI,
    LAYER_UTILITY,
    Layer,
    LayerRegistry,
    default_registry,
    get_registry,
    layer_id_of,
    use_registry,
)
from tldrgraph.llm_enricher import LLMEnricher, build_system_prompt


#: One representative node per layer: (layer id, node id, node data).
CLASSIFICATION_CASES = [
    (LAYER_UI, "ui_button", {"label": "SubmitButton",
                             "source_file": "frontend/src/app/cases/page.tsx"}),
    (LAYER_API, "api_ctrl", {"label": "CasesController",
                             "source_file": "backend/src/cases/cases.controller.ts"}),
    (LAYER_SERVICE, "svc_flow", {"label": "CaseWorkflowService",
                                 "source_file": "backend/src/cases/case-workflow.service.ts"}),
    (LAYER_DATA, "db_case", {"label": "CaseRepository",
                             "source_file": "backend/src/cases/case.repository.ts"}),
    (LAYER_ASYNC, "job_poll", {"label": "CaseStatusPollingJob",
                               "source_file": "backend/src/polling/case-status.polling.ts"}),
    (LAYER_DEVOPS, "ci", {"label": "CiDeploy",
                          "source_file": ".github/workflows/ci.yml"}),
    (LAYER_UTILITY, "util_fmt", {"label": "formatCurrency",
                                 "source_file": "shared/utils/format.ts"}),
]


def _renamed_registry() -> LayerRegistry:
    """
    The default layer set with every display name replaced by something with no
    shared vocabulary: no "Layer N:" prefix, no "UI"/"API"/"Service"/"Data"/
    "DevOps" substring, no "General / Utility". Ids and order are untouched.
    """
    renames = {
        LAYER_UI: "Tier One :: Presentation Surface",
        LAYER_API: "Tier Two :: Request Boundary",
        LAYER_SERVICE: "Tier Three :: Business Rules",
        LAYER_DATA: "Tier Four :: Storage",
        LAYER_ASYNC: "Tier Five :: Background Work",
        LAYER_DEVOPS: "Tier Six :: Platform",
        LAYER_UTILITY: "Everything Else",
    }
    registry = default_registry()
    for layer_id, name in renames.items():
        registry = registry.replacing(layer_id, name=name)
    return registry


@pytest.fixture
def renamed():
    """Activates the renamed registry for the duration of a test."""
    registry = _renamed_registry()
    with use_registry(registry):
        yield registry


# --------------------------------------------------------------------------- #
# Registry basics
# --------------------------------------------------------------------------- #

def test_default_registry_ships_the_six_layers_plus_utility():
    registry = default_registry()
    assert registry.ids() == (LAYER_UI, LAYER_API, LAYER_SERVICE, LAYER_DATA,
                              LAYER_ASYNC, LAYER_DEVOPS, LAYER_UTILITY)
    assert registry.utility_id == LAYER_UTILITY
    assert [layer.order for layer in registry] == [1, 2, 3, 4, 5, 6, 7]
    assert all(layer.description for layer in registry)


def test_display_names_are_unchanged_from_the_hardcoded_enum():
    """Persisted .tldrgraph/ data and 231 existing tests depend on these."""
    registry = default_registry()
    assert registry.name(LAYER_UI) == "Layer 1: UI Trigger"
    assert registry.name(LAYER_API) == "Layer 2: API Gateway"
    assert registry.name(LAYER_SERVICE) == "Layer 3: Domain Service"
    assert registry.name(LAYER_DATA) == "Layer 4: Data & Persistence"
    assert registry.name(LAYER_ASYNC) == "Layer 5: Async & System Tasks"
    assert registry.name(LAYER_DEVOPS) == "Layer 6: DevOps & Infrastructure"
    assert registry.name(LAYER_UTILITY) == "General / Utility"
    assert registry.names() == tuple(member.value for member in LayerType)


def test_order_is_explicit_not_parsed_out_of_the_name():
    """A layer whose name carries no number still ranks where it was told to."""
    registry = LayerRegistry.from_records(
        [
            {"id": "b", "name": "no digits here", "order": 1},
            {"id": "a", "name": "Layer 9: decoy", "order": 2},
            {"id": LAYER_UTILITY, "name": "rest", "order": 3},
        ],
    )
    assert registry.ids() == ("b", "a", LAYER_UTILITY)
    assert registry.order("b") < registry.order("a")
    assert registry.unranked_order > registry.order(LAYER_UTILITY)


def test_registry_rejects_duplicate_ids_and_names():
    with pytest.raises(ValueError):
        LayerRegistry([Layer("x", "One", 1), Layer("x", "Two", 2)], utility_id="x")
    with pytest.raises(ValueError):
        LayerRegistry([Layer("x", "Same", 1), Layer("y", "Same", 2)], utility_id="x")
    with pytest.raises(ValueError):
        LayerRegistry([Layer("x", "One", 1)], utility_id="nope")


def test_layer_id_of_prefers_the_id_and_falls_back_to_the_display_name():
    assert layer_id_of({"layer_id": LAYER_API, "layer": "anything at all"}) == LAYER_API
    # Legacy record: display name only.
    assert layer_id_of({"layer": "Layer 2: API Gateway"}) == LAYER_API
    assert layer_id_of({}) == ""
    assert layer_id_of({"layer": "a layer nobody registered"}) == ""


def test_use_registry_restores_the_previous_one(renamed):
    assert get_registry().name(LAYER_UI) == "Tier One :: Presentation Surface"


def test_registry_is_restored_after_the_context_exits():
    assert get_registry().name(LAYER_UI) == "Layer 1: UI Trigger"


# --------------------------------------------------------------------------- #
# Nodes carry both attributes
# --------------------------------------------------------------------------- #

def test_scan_stamps_both_layer_and_layer_id(mini_repo):
    loader = GraphLoader(str(mini_repo.root))
    graph = loader.load_or_extract(enrich_llm=False)

    registry = get_registry()
    for _, data in graph.nodes(data=True):
        assert data["layer_id"] in registry
        assert data["layer"] == registry.name(data["layer_id"])


def test_snapshot_records_layer_id(mini_repo):
    loader = GraphLoader(str(mini_repo.root))
    loader.load_or_extract(enrich_llm=False)

    snapshot = loader.load_graph_snapshot()
    assert snapshot["nodes"]
    for node in snapshot["nodes"]:
        assert node["layer_id"] == get_registry().id_for_name(node["layer"])


def test_pre_layer_id_snapshots_still_load(mini_repo):
    """Old .tldrgraph/graph.json files carry only the display name."""
    import json

    loader = GraphLoader(str(mini_repo.root))
    loader.load_or_extract(enrich_llm=False)

    path = loader.snapshot_path()
    raw = json.loads(open(path, encoding="utf-8").read())
    for node in raw["nodes"]:
        node.pop("layer_id", None)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(raw, handle)

    restored = GraphLoader(str(mini_repo.root)).load_graph_snapshot()
    assert restored is not None
    by_id = {n["id"]: n for n in restored["nodes"]}
    for node in raw["nodes"]:
        derived = by_id[node["id"]]["layer_id"]
        assert derived == get_registry().id_for_name(node["layer"])
        assert derived != ""


def test_nodes_by_layer_id_mirrors_nodes_by_layer(mini_repo):
    loader = GraphLoader(str(mini_repo.root))
    loader.load_or_extract(enrich_llm=False)

    registry = get_registry()
    assert set(loader.nodes_by_layer) == set(registry.names())
    assert set(loader.nodes_by_layer_id) == set(registry.ids())
    for layer in registry:
        assert loader.nodes_by_layer_id[layer.id] is loader.nodes_by_layer[layer.name]


# --------------------------------------------------------------------------- #
# THE POINT OF THIS PHASE: renaming a display string changes nothing but text
# --------------------------------------------------------------------------- #

def test_renaming_display_names_changes_nothing_but_the_text(renamed):
    """
    Rename every layer's display string, then check the four places that used to
    read meaning out of that string: classification, flow ordering, heuristic
    enrichment branching, and the dead-code utility bucket.
    """
    # --- 1. classification ------------------------------------------------ #
    for layer_id, node_id, node_data in CLASSIFICATION_CASES:
        layer = classify_node(node_id, node_data)
        assert layer.id == layer_id, node_id
        # ...and the display name it reports is the renamed one.
        assert layer.name == renamed.name(layer_id)

    # --- 2. flow ordering ------------------------------------------------- #
    graph = nx.DiGraph()
    chain = [LAYER_DEVOPS, LAYER_ASYNC, LAYER_DATA, LAYER_SERVICE, LAYER_API, LAYER_UI]
    for layer_id in chain:
        graph.add_node(layer_id, id=layer_id, label=layer_id,
                       layer_id=layer_id, layer=renamed.name(layer_id))
    # Wire them backwards so the walk only reaches them all via the edges.
    for src, tgt in zip(chain, chain[1:]):
        graph.add_edge(src, tgt, relation="calls")

    engine = FlowEngine(graph, _NullVectorStore(), ".")
    steps = engine._bridge_aware_walk(LAYER_DEVOPS, max_steps=10)
    # Sorted by the registry's explicit order: ui(1) ... devops(6).
    assert [s["id"] for s in steps] == [
        LAYER_UI, LAYER_API, LAYER_SERVICE, LAYER_DATA, LAYER_ASYNC, LAYER_DEVOPS
    ]

    # --- 3. heuristic enrichment branching -------------------------------- #
    enricher = LLMEnricher()
    batch = [
        {"id": layer_id, "label": f"Sym_{layer_id}", "file": "src/x.ts",
         "layer_id": layer_id, "layer": renamed.name(layer_id)}
        for layer_id, _, _ in CLASSIFICATION_CASES
    ]
    intents = {item["id"]: item["intent"] for item in enricher._heuristic_enrichment(batch)}
    assert intents[LAYER_UI].startswith("User Interface component")
    assert intents[LAYER_API].startswith("REST API Controller endpoint")
    assert intents[LAYER_SERVICE].startswith("Domain Service logic")
    assert intents[LAYER_DATA].startswith("Database entity/schema model")
    assert intents[LAYER_DEVOPS].startswith("DevOps deployment")
    # async and utility have no template and fall through to the generic one --
    # exactly as they did under the old substring chain.
    assert intents[LAYER_ASYNC].startswith("Core module symbol")
    assert intents[LAYER_UTILITY].startswith("Core module symbol")

    # --- 4. dead-code utility handling ------------------------------------ #
    coverage_graph = nx.DiGraph()
    coverage_graph.add_node("enriched", layer_id=LAYER_SERVICE,
                            layer=renamed.name(LAYER_SERVICE),
                            intent="Read the source.", enrichment_source="agent")
    coverage_graph.add_node("bare", layer_id=LAYER_API,
                            layer=renamed.name(LAYER_API),
                            intent="", enrichment_source="")
    # Utility nodes are excluded from the denominator, by id, under any name.
    coverage_graph.add_node("skipped", layer_id=LAYER_UTILITY,
                            layer=renamed.name(LAYER_UTILITY),
                            intent="", enrichment_source="")
    assert compute_enrichment_coverage(coverage_graph) == 0.5

    # --- and the prompt follows the rename -------------------------------- #
    prompt = build_system_prompt()
    assert "Tier Three :: Business Rules" in prompt
    assert "Layer 3: Domain Service" not in prompt
    assert "Everything Else" not in prompt   # utility is not an architectural layer


def test_renaming_does_not_change_the_classification_of_a_real_repo(mini_repo):
    """Every node lands in the same layer id before and after a rename."""
    before = {
        nid: data["layer_id"]
        for nid, data in GraphLoader(str(mini_repo.root)).load_or_extract(
            enrich_llm=False).nodes(data=True)
    }

    with use_registry(_renamed_registry()) as renamed_registry:
        graph = GraphLoader(str(mini_repo.root)).load_or_extract(enrich_llm=False, rebuild=True)
        after = {nid: data["layer_id"] for nid, data in graph.nodes(data=True)}
        display = {data["layer"] for _, data in graph.nodes(data=True)}

    assert after == before
    # The display names really did change -- the test above is not vacuous.
    assert display <= set(renamed_registry.names())
    assert not display & set(default_registry().names())


def test_utility_bucket_is_the_registry_id_not_the_string(renamed):
    """cli's enrichment queue skips the utility bucket under any display name."""
    from tldrgraph import cli as cli_module

    assert not cli_module.needs_agent_enrichment(
        {"layer_id": LAYER_UTILITY, "layer": renamed.name(LAYER_UTILITY),
         "intent": "", "enrichment_source": ""})
    assert cli_module.needs_agent_enrichment(
        {"layer_id": LAYER_API, "layer": renamed.name(LAYER_API),
         "intent": "", "enrichment_source": ""})


def test_layer_object_still_answers_to_the_legacy_enum_api():
    """Existing call sites read `.value`; existing tests compare to LayerType."""
    layer = classify_node("api_ctrl", {"source_file": "backend/src/x.controller.ts"})
    assert layer.value == layer.name == LayerType.LAYER_2_API.value
    assert layer == LayerType.LAYER_2_API
    assert layer != LayerType.LAYER_3_SERVICE


class _NullVectorStore:
    """FlowEngine only needs `search` for resolution; the walk never calls it."""

    def search(self, query, top_k=5):
        return []
