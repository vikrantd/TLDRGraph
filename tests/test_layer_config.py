"""
Tests for LLM-driven, Configurable Architectural Layers (Phase 3).
"""

import json
import os
import pytest
from click.testing import CliRunner

from tldrgraph.cli import cli
from tldrgraph.classifier import classify_node, classify_node_with_source
from tldrgraph.graph_loader import GraphLoader
from tldrgraph.layer_config import (
    CONFIG_FILENAME_YAML,
    compute_registry_hash,
    load_layer_config,
    save_layer_config,
    validate_layer_config,
)
from tldrgraph.layers import (
    DEFAULT_LAYERS,
    LAYER_API,
    LAYER_DATA,
    LAYER_SERVICE,
    LAYER_UI,
    LAYER_UTILITY,
    Layer,
    LayerRegistry,
    default_registry,
    get_registry,
    set_registry,
    use_registry,
)
from tldrgraph.propose_layers import (
    apply_proposed_layers,
    generate_propose_request,
)
from tldrgraph.rules import Rule


@pytest.fixture(autouse=True)
def reset_active_registry():
    """Ensures the active registry is always restored to default after each test."""
    set_registry(default_registry())
    yield
    set_registry(default_registry())


# --------------------------------------------------------------------------- #
# 1. Rule Predicate Tests
# --------------------------------------------------------------------------- #

def test_rule_matching_file_and_exclude():
    rule = Rule(file_contains=("frontend/",), exclude_file=("docker", ".github"))
    assert rule.matches("node1", {"file": "frontend/src/app/page.tsx"})
    assert not rule.matches("node2", {"file": "frontend/Dockerfile"})
    assert not rule.matches("node3", {"file": "frontend/.github/workflows/deploy.yml"})
    assert not rule.matches("node4", {"file": "backend/src/main.ts"})


def test_rule_matching_labels_and_suffixes():
    rule = Rule(label_contains=("service", "manager"), label_ends_with=("entity", "dao"))
    assert rule.matches("n1", {"label": "ApplicationsService"})
    assert rule.matches("n2", {"label": "PensionCaseEntity"})
    assert rule.matches("n3", {"label": "UserDAO"})
    assert not rule.matches("n4", {"label": "CaseController"})


def test_rule_matching_types_and_id_prefixes():
    rule = Rule(type_in=("api_endpoint", "db_model"), id_prefix=("endpoint_", "prisma_model_"))
    assert rule.matches("endpoint_get_me", {"type": "api_endpoint"})
    assert rule.matches("prisma_model_user", {"type": "db_model"})
    assert not rule.matches("backend_src_main", {"type": "symbol"})


def test_rule_regex_predicates():
    rule = Rule(path_regex=r"^backend/src/(auth|users)/.*\.ts$")
    assert rule.matches("n1", {"file": "backend/src/auth/auth.service.ts"})
    assert rule.matches("n2", {"file": "backend/src/users/user.entity.ts"})
    assert not rule.matches("n3", {"file": "backend/src/cases/case.service.ts"})


def test_invalid_rule_regex_raises_error():
    with pytest.raises(ValueError, match="Invalid regex pattern"):
        Rule(path_regex=r"[unclosed_regex(")


def test_rule_serialization_roundtrip():
    original = Rule(
        file_contains=("frontend/",),
        exclude_file=("docker",),
        type_in=("api_endpoint",),
        id_prefix=("ep_",),
    )
    rec = original.as_record()
    restored = Rule.from_record(rec)
    assert restored.file_contains == ("frontend/",)
    assert restored.exclude_file == ("docker",)
    assert restored.type_in == ("api_endpoint",)
    assert restored.id_prefix == ("ep_",)


# --------------------------------------------------------------------------- #
# 2. Layer Configuration Validation & Hash Tests
# --------------------------------------------------------------------------- #

def test_validate_layer_config_valid():
    valid_data = {
        "utility_id": "utility",
        "layers": [
            {"id": "ui", "name": "Presentation", "order": 1, "description": "UI", "rules": [{"file_contains": ["frontend/"]}]},
            {"id": "backend", "name": "Backend Core", "order": 2, "description": "API/Service", "rules": [{"file_contains": ["backend/"]}]},
            {"id": "utility", "name": "Utility", "order": 3, "description": "Catch-all", "rules": []},
        ]
    }
    validate_layer_config(valid_data)


def test_validate_layer_config_duplicate_id():
    data = {
        "utility_id": "utility",
        "layers": [
            {"id": "ui", "name": "UI 1", "order": 1, "rules": []},
            {"id": "ui", "name": "UI 2", "order": 2, "rules": []},
            {"id": "utility", "name": "Utility", "order": 3, "rules": []},
        ]
    }
    with pytest.raises(ValueError, match="Duplicate layer id 'ui'"):
        validate_layer_config(data)


def test_validate_layer_config_duplicate_order():
    data = {
        "utility_id": "utility",
        "layers": [
            {"id": "ui", "name": "UI", "order": 1, "rules": []},
            {"id": "api", "name": "API", "order": 1, "rules": []},
            {"id": "utility", "name": "Utility", "order": 2, "rules": []},
        ]
    }
    with pytest.raises(ValueError, match="Duplicate order 1"):
        validate_layer_config(data)


def test_validate_layer_config_missing_utility_id():
    data = {
        "utility_id": "missing_util",
        "layers": [
            {"id": "ui", "name": "UI", "order": 1, "rules": []},
            {"id": "api", "name": "API", "order": 2, "rules": []},
        ]
    }
    with pytest.raises(ValueError, match="Designated utility_id 'missing_util' is not in the list"):
        validate_layer_config(data)


def test_compute_registry_hash_sensitivity():
    r1 = default_registry()
    h1 = compute_registry_hash(r1)

    r2 = r1.replacing(LAYER_UI, name="Renamed UI")
    h2 = compute_registry_hash(r2)

    assert h1 != h2
    assert h1 == compute_registry_hash(default_registry())


# --------------------------------------------------------------------------- #
# 3. Layer Provenance & Default Classification
# --------------------------------------------------------------------------- #

def test_classify_node_provenance():
    # Rule match
    layer, source = classify_node_with_source("n1", {"file": "frontend/src/app/page.tsx"})
    assert layer.id == LAYER_UI
    assert source == "rule"

    # Default fallback
    layer_def, source_def = classify_node_with_source("unknown_symbol", {"file": "somewhere/random/helper.xyz"})
    assert layer_def.id == LAYER_UTILITY
    assert source_def == "default"


# --------------------------------------------------------------------------- #
# 4. Propose Layers & Apply Layers CLI Commands
# --------------------------------------------------------------------------- #

def test_propose_layers_cli_command(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "page.tsx").write_text("export default function Page() {}", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.controller.ts").write_text("class AppController {}", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["propose-layers", "--path", str(tmp_path)])
    assert res.exit_code == 0

    req_file = tmp_path / ".tldrgraph" / "propose_layers_request.json"
    assert req_file.is_file()
    data = json.loads(req_file.read_text(encoding="utf-8"))
    assert data["schema"] == "codechakra/propose-layers-request@1"
    assert "evidence" in data


def test_apply_layers_cli_command(tmp_path):
    (tmp_path / ".tldrgraph").mkdir(parents=True)
    resp_file = tmp_path / ".tldrgraph" / "propose_layers_response.json"
    proposal = {
        "utility_id": "utility",
        "layers": [
            {
                "id": "presentation",
                "name": "Presentation",
                "order": 1,
                "description": "UI layer",
                "rules": [{"file_contains": ["frontend/"]}]
            },
            {
                "id": "domain",
                "name": "Domain",
                "order": 2,
                "description": "Domain logic",
                "rules": [{"file_contains": ["backend/"]}]
            },
            {
                "id": "utility",
                "name": "Utility",
                "order": 3,
                "description": "Catch-all",
                "rules": []
            }
        ]
    }
    resp_file.write_text(json.dumps(proposal), encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["apply-layers", "--path", str(tmp_path)])
    assert res.exit_code == 0

    config_file = tmp_path / ".tldrgraph" / "layers.config.yaml"
    assert config_file.is_file()

    registry, chash = load_layer_config(str(tmp_path))
    assert registry.ids() == ("presentation", "domain", "utility")


# --------------------------------------------------------------------------- #
# 5. Gate 5 Demonstration: Enrichment Survives Layer Set Change
# --------------------------------------------------------------------------- #

def test_enrichment_survives_layer_set_change(mini_repo):
    """
    Demonstrates Gate 5:
    1. Scan repo with default 6 layers.
    2. Apply enrichment response (intent, fields, cross-layer calls).
    3. Install a 4-layer custom layer set (presentation, api, domain, utility).
    4. Rescan repo.
    5. Verify that intents, fields, and bridge edges survive while layer assignment is updated.
    """
    # 1. Initial scan
    l1 = GraphLoader(str(mini_repo.root))
    g1 = l1.load_or_extract(enrich_llm=False)

    case_view = mini_repo.nid("ui_page")
    pension_calc = mini_repo.nid("svc_pension")
    assert g1.nodes[case_view]["layer_id"] == LAYER_UI
    assert g1.nodes[pension_calc]["layer_id"] == LAYER_SERVICE

    # 2. Apply enrichment
    resp_path = mini_repo.root / ".tldrgraph" / "enrichment_response.json"
    resp_path.write_text(json.dumps([
        {
            "id": case_view,
            "intent": "Pension case view rendering detailed case metrics",
            "fields": ["caseId", "pensionerName"],
            "calls": ["PensionCalculatorService"]
        }
    ]), encoding="utf-8")

    runner = CliRunner()
    apply_res = runner.invoke(cli, ["apply-enrichment", "--path", str(mini_repo.root)])
    assert apply_res.exit_code == 0

    # Verify bridge edge exists
    l2 = GraphLoader(str(mini_repo.root))
    g2 = l2.load_or_extract(enrich_llm=False)
    assert g2.nodes[case_view]["intent"] == "Pension case view rendering detailed case metrics"
    assert g2.nodes[case_view]["fields"] == ["caseId", "pensionerName"]
    assert g2.has_edge(case_view, pension_calc)
    assert g2[case_view][pension_calc]["relation"] == "cross_layer_link"

    # 3. Install alternative 4-layer architecture
    custom_layers = {
        "utility_id": "infra_utility",
        "layers": [
            {
                "id": "presentation",
                "name": "Custom Presentation",
                "order": 1,
                "description": "Frontend UI",
                "rules": [{"file_contains": ["frontend/"]}]
            },
            {
                "id": "app_api",
                "name": "Custom API",
                "order": 2,
                "description": "HTTP Endpoints",
                "rules": [{"file_contains": ["controller"]}]
            },
            {
                "id": "domain_logic",
                "name": "Custom Domain Logic",
                "order": 3,
                "description": "Business Calculations",
                "rules": [{"file_contains": ["service", "calc", "backend/"]}]
            },
            {
                "id": "infra_utility",
                "name": "Custom Infrastructure & Utility",
                "order": 4,
                "description": "Shared and Infra",
                "rules": []
            }
        ]
    }
    config_file = mini_repo.root / ".tldrgraph" / "layers.config.yaml"
    import yaml
    config_file.write_text(yaml.dump(custom_layers), encoding="utf-8")

    # 4. Rescan with new layer config
    l3 = GraphLoader(str(mini_repo.root))
    g3 = l3.load_or_extract(enrich_llm=False)

    # 5. Verify:
    # - Layer assignments changed to custom layer IDs
    assert g3.nodes[case_view]["layer_id"] == "presentation"
    assert g3.nodes[case_view]["layer"] == "Custom Presentation"
    assert g3.nodes[pension_calc]["layer_id"] == "domain_logic"
    assert g3.nodes[pension_calc]["layer"] == "Custom Domain Logic"

    # - Enriched intent, fields, and bridge edge ALL survived!
    assert g3.nodes[case_view]["intent"] == "Pension case view rendering detailed case metrics"
    assert g3.nodes[case_view]["fields"] == ["caseId", "pensionerName"]
    assert g3.has_edge(case_view, pension_calc)
    assert g3[case_view][pension_calc]["relation"] == "cross_layer_link"


def test_an_unconfigured_repo_gets_one_honest_bucket_not_six_guesses(tmp_path):
    """
    HARD GATE. `load_layer_config` used to hand back a six-layer
    UI/API/Service/Data/Async/DevOps default when no config existed, so every
    node in an unscanned repo was confidently filed into an architecture nobody
    had derived from it. A map that is wrong everywhere it looks right is worse
    than no map.
    """
    registry, _ = load_layer_config(str(tmp_path))

    assert len(registry) == 1
    assert registry.ordered()[0].name == "Unclassified"
    assert registry.utility_id == registry.ordered()[0].id
    assert registry.ordered()[0].rules == ()


def test_the_example_layer_set_is_never_written_to_disk(tmp_path):
    load_layer_config(str(tmp_path))
    assert not (tmp_path / ".tldrgraph" / "layers.config.yaml").exists()
