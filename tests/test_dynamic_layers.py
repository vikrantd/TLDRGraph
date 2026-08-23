"""
Comprehensive tests for Dynamic Multi-Layer Architecture and Discovery.
"""

import json
import os
import pytest
from click.testing import CliRunner

from tldrgraph.cli import cli
from tldrgraph.classifier import classify_node
from tldrgraph.graph_loader import GraphLoader
from tldrgraph.layer_config import load_layer_config, save_layer_config, validate_layer_config
from tldrgraph.layers import LayerRegistry, get_registry, set_registry, default_registry
from tldrgraph.propose_layers import (
    ARCHETYPE_BACKEND,
    ARCHETYPE_CLI,
    ARCHETYPE_FULLSTACK,
    ARCHETYPE_GENERIC,
    ARCHETYPE_LIBRARY,
    NEEDS_LAYERS,
    auto_configure_layers,
    detect_repository_archetype,
    propose_layers_with_llm,
)


@pytest.fixture(autouse=True)
def reset_active_registry():
    """Ensures active registry is restored to default after each test."""
    set_registry(default_registry())
    yield
    set_registry(default_registry())


# --------------------------------------------------------------------------- #
# 1. Archetype Detection Tests
# --------------------------------------------------------------------------- #

def test_detect_archetype_cli_repo(tmp_path):
    # Setup CLI repo structure
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mycli"\n[project.scripts]\nmycli = "mycli.cli:main"\n',
        encoding="utf-8"
    )
    (tmp_path / "mycli").mkdir()
    (tmp_path / "mycli" / "cli.py").write_text("import click\n@click.command()\ndef main(): pass\n", encoding="utf-8")

    archetype = detect_repository_archetype(str(tmp_path))
    assert archetype == ARCHETYPE_CLI


def test_detect_archetype_fullstack_repo(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"dependencies": {"next": "14.0.0", "react": "18.0.0"}}),
        encoding="utf-8"
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "package.json").write_text(
        json.dumps({"dependencies": {"@nestjs/core": "10.0.0", "prisma": "5.0.0"}}),
        encoding="utf-8"
    )

    archetype = detect_repository_archetype(str(tmp_path))
    assert archetype == ARCHETYPE_FULLSTACK


def test_detect_archetype_backend_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = ["fastapi>=0.100.0", "uvicorn>=0.20.0"]\n',
        encoding="utf-8"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "api.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

    archetype = detect_repository_archetype(str(tmp_path))
    assert archetype == ARCHETYPE_BACKEND


def test_detect_archetype_library_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mathlib"\nversion = "0.1.0"\n',
        encoding="utf-8"
    )
    (tmp_path / "mathlib").mkdir()
    (tmp_path / "mathlib" / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    archetype = detect_repository_archetype(str(tmp_path))
    assert archetype == ARCHETYPE_LIBRARY


# --------------------------------------------------------------------------- #
# 2. Rule matching against a hand-written layer set
# --------------------------------------------------------------------------- #

#: A layer set of the shape an agent is asked to produce. Written out here on
#: purpose: TLDRGraph ships no layer templates any more, so the rule engine is
#: tested against a fixture rather than against production defaults.
SAMPLE_LAYER_SET = {
    "utility_id": "utility",
    "layers": [
        {"id": "cli", "name": "Layer 1: CLI", "order": 1, "description": "commands",
         "rules": [{"file_contains": ["cli.py", "/cli/", "commands/"]}]},
        {"id": "engine", "name": "Layer 2: Engine", "order": 2, "description": "logic",
         "rules": [{"file_contains": ["flow_engine", "classifier", "extractors"]}]},
        {"id": "storage", "name": "Layer 3: Storage", "order": 3, "description": "state",
         "rules": [{"file_contains": ["vector_store", "graph_loader", "hash_gate"]}]},
        {"id": "integrations", "name": "Layer 4: Integrations", "order": 4,
         "description": "agents and UI",
         "rules": [{"file_contains": ["installer", "visualizer", "llm_enricher"]}]},
        {"id": "utility", "name": "General / Utility", "order": 5,
         "description": "catch-all", "rules": []},
    ],
}


def test_a_proposed_layer_set_builds_a_registry():
    validate_layer_config(SAMPLE_LAYER_SET)
    reg = LayerRegistry.from_records(
        SAMPLE_LAYER_SET["layers"], utility_id=SAMPLE_LAYER_SET["utility_id"]
    )
    assert reg.utility_id == "utility"
    assert len(reg) == len(SAMPLE_LAYER_SET["layers"])


def test_file_rules_route_symbols_to_their_layer(tmp_path):
    reg = LayerRegistry.from_records(
        SAMPLE_LAYER_SET["layers"], utility_id=SAMPLE_LAYER_SET["utility_id"]
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("tldrgraph.layers.get_registry", lambda: reg)
        mp.setattr("tldrgraph.classifier.get_registry", lambda: reg)

        assert classify_node("n1", {"file": "tldrgraph/cli.py", "label": "scan"}).id == "cli"
        assert classify_node("n2", {"file": "tldrgraph/flow_engine.py",
                                    "label": "query_flow"}).id == "engine"
        assert classify_node("n3", {"file": "tldrgraph/vector_store.py",
                                    "label": "LocalVectorStore"}).id == "storage"
        assert classify_node("n4", {"file": "tldrgraph/installer.py",
                                    "label": "install"}).id == "integrations"
        # Nothing matches -> the catch-all, never a guess.
        assert classify_node("n5", {"file": "tldrgraph/labels.py",
                                    "label": "build"}).id == "utility"


# --------------------------------------------------------------------------- #
# 3. Layer synthesis
# --------------------------------------------------------------------------- #

class MockLLMEnricher:
    def __init__(self, proposal_dict=None):
        self.proposal_dict = proposal_dict or {
            "utility_id": "utility",
            "layers": [
                {
                    "id": "compiler_frontend",
                    "name": "Layer 1: Lexer & Parser",
                    "order": 1,
                    "description": "Tokenization and AST construction",
                    "rules": [{"file_contains": ["parser", "lexer"]}]
                },
                {
                    "id": "compiler_optimizer",
                    "name": "Layer 2: Optimizer Passes",
                    "order": 2,
                    "description": "IR transformations and dead code elimination",
                    "rules": [{"file_contains": ["optimize", "ir/"]}]
                },
                {
                    "id": "compiler_backend",
                    "name": "Layer 3: Code Generation",
                    "order": 3,
                    "description": "LLVM and machine assembly emission",
                    "rules": [{"file_contains": ["codegen", "emit"]}]
                },
                {
                    "id": "utility",
                    "name": "General / Utility",
                    "order": 4,
                    "description": "Shared compiler utilities",
                    "rules": []
                }
            ]
        }

    def propose_layers(self, evidence):
        return self.proposal_dict


def test_propose_layers_with_llm(tmp_path):
    mock = MockLLMEnricher()
    proposal = propose_layers_with_llm(str(tmp_path), enricher=mock)
    assert proposal is not None
    assert proposal["utility_id"] == "utility"
    assert len(proposal["layers"]) == 4


def test_auto_configure_layers_with_llm(tmp_path):
    mock = MockLLMEnricher()
    reg, cfg_path, source = auto_configure_layers(str(tmp_path), enricher=mock, use_llm=True)
    assert source == "llm_synthesis"
    assert os.path.isfile(cfg_path)
    assert reg.ids() == ("compiler_frontend", "compiler_optimizer", "compiler_backend", "utility")

    # Second call should load existing config
    reg2, cfg2, source2 = auto_configure_layers(str(tmp_path), enricher=mock, use_llm=True, force=False)
    assert source2 == "existing_config"
    assert reg2.ids() == reg.ids()


def test_no_layer_source_means_no_layers_not_a_template(tmp_path):
    """
    HARD GATE. TLDRGraph used to synthesize a generic archetype layer set here.
    That silently became the answer and classified badly. With nothing able to
    read the code, the only honest result is "ask the agent".
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\nmycmd = "mycmd.cli:main"\n', encoding="utf-8"
    )
    reg, cfg_path, source = auto_configure_layers(
        str(tmp_path), enricher=None, use_llm=False, use_agent=False
    )
    assert source == NEEDS_LAYERS
    assert reg is None and cfg_path is None
    assert not (tmp_path / ".tldrgraph" / "layers.config.yaml").exists(), (
        "a layer config must never be written without a real source"
    )


# --------------------------------------------------------------------------- #
# 4. End-to-End CLI Scan with Dynamic Layers
# --------------------------------------------------------------------------- #

def test_cli_propose_layers_falls_through_to_a_request(tmp_path):
    """With nothing able to read the code, --auto must queue a request, not a template."""
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\ntool = "tool.cli:main"\n', encoding="utf-8"
    )
    runner = CliRunner()
    res = runner.invoke(cli, ["propose-layers", "--path", str(tmp_path), "--auto"])
    assert res.exit_code == 0
    assert "no template to fall back on" in res.output
    assert (tmp_path / ".tldrgraph" / "propose_layers_request.json").is_file()
    assert not (tmp_path / ".tldrgraph" / "layers.config.yaml").exists()


def test_cli_scan_initializes_dynamic_layers_automatically(tmp_path):
    # Create minimal CLI repo files
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample-tool"\n[project.scripts]\nsample = "sample.cli:main"\n',
        encoding="utf-8"
    )
    (tmp_path / "sample").mkdir()
    (tmp_path / "sample" / "cli.py").write_text(
        "import click\n@click.command()\ndef main(): pass\n", encoding="utf-8"
    )
    (tmp_path / "sample" / "engine.py").write_text(
        "class ProcessingEngine: pass\n", encoding="utf-8"
    )
    (tmp_path / "sample" / "storage.py").write_text(
        "class CacheStore: pass\n", encoding="utf-8"
    )

    runner = CliRunner()
    res = runner.invoke(cli, ["scan", str(tmp_path)])
    assert res.exit_code == 0
    # No agent is reachable in tests, so the scan must stop and ask rather than
    # classify this repo with layers it never derived.
    assert "status: needs_layers" in res.output
    assert "tldrgraph init" in res.output

    assert not (tmp_path / ".tldrgraph" / "layers.config.yaml").exists()

    # The request the agent is asked to answer must actually be there, carrying
    # the symbols extraction already found -- filenames alone are not evidence.
    request = tmp_path / ".tldrgraph" / "propose_layers_request.json"
    assert request.is_file()
    payload = json.loads(request.read_text(encoding="utf-8"))
    assert payload["evidence"]["extracted_symbols"]["total_symbols"] > 0

    # Answer it the way an agent would, and the next run gets all the way through.
    (tmp_path / ".tldrgraph" / "propose_layers_response.json").write_text(
        json.dumps(SAMPLE_LAYER_SET), encoding="utf-8"
    )
    res2 = runner.invoke(cli, ["init", str(tmp_path)])
    assert res2.exit_code == 0, res2.output
    assert "status: needs_layers" not in res2.output
    assert (tmp_path / ".tldrgraph" / "layers.config.yaml").is_file()

    res_layers = runner.invoke(cli, ["layers", "--path", str(tmp_path)])
    assert res_layers.exit_code == 0
    assert "TLDRGraph Multi-Layer Architecture Summary" in res_layers.output
