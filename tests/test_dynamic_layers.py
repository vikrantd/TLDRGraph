"""
Comprehensive tests for Dynamic Multi-Layer Architecture and Discovery.
"""

import json
import os
import pytest
from click.testing import CliRunner

from codechakra.cli import cli
from codechakra.classifier import classify_node
from codechakra.graph_loader import GraphLoader
from codechakra.layer_config import load_layer_config, save_layer_config
from codechakra.layers import LayerRegistry, get_registry, set_registry, default_registry
from codechakra.propose_layers import (
    ARCHETYPE_BACKEND,
    ARCHETYPE_CLI,
    ARCHETYPE_FULLSTACK,
    ARCHETYPE_GENERIC,
    ARCHETYPE_LIBRARY,
    archetype_layer_set,
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
# 2. Archetype Layer Set Structure & Validation
# --------------------------------------------------------------------------- #

def test_archetype_layer_sets_are_valid():
    for arch in (ARCHETYPE_CLI, ARCHETYPE_FULLSTACK, ARCHETYPE_BACKEND, ARCHETYPE_LIBRARY, ARCHETYPE_GENERIC):
        data = archetype_layer_set(arch)
        assert "utility_id" in data
        assert "layers" in data
        assert len(data["layers"]) >= 3
        # Should build into a valid LayerRegistry
        reg = LayerRegistry.from_records(data["layers"], utility_id=data["utility_id"])
        assert reg.utility_id == data["utility_id"]
        assert len(reg) == len(data["layers"])


def test_cli_archetype_classifies_cli_components(tmp_path):
    data = archetype_layer_set(ARCHETYPE_CLI)
    reg = LayerRegistry.from_records(data["layers"], utility_id=data["utility_id"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("codechakra.layers.get_registry", lambda: reg)
        mp.setattr("codechakra.classifier.get_registry", lambda: reg)

        # CLI command
        layer_cli = classify_node("n1", {"file": "codechakra/cli.py", "label": "scan"})
        assert layer_cli.id == "cli"

        # Core engine
        layer_eng = classify_node("n2", {"file": "codechakra/flow_engine.py", "label": "query_flow"})
        assert layer_eng.id == "engine"

        # Storage
        layer_store = classify_node("n3", {"file": "codechakra/vector_store.py", "label": "LocalVectorStore"})
        assert layer_store.id == "storage"

        # Agent / Visualizer
        layer_int = classify_node("n4", {"file": "codechakra/visualizer.py", "label": "generate_html"})
        assert layer_int.id == "integrations"

        # Fallback / Utility
        layer_util = classify_node("n5", {"file": "codechakra/helpers.py", "label": "format_date"})
        assert layer_util.id == "utility"


# --------------------------------------------------------------------------- #
# 3. LLM Proposal & Auto-Configuration
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


def test_auto_configure_layers_offline_fallback(tmp_path):
    # Set up a CLI repo
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\nmycmd = "mycmd.cli:main"\n', encoding="utf-8"
    )
    reg, cfg_path, source = auto_configure_layers(str(tmp_path), enricher=None, use_llm=False)
    assert source == "archetype:cli_application"
    assert os.path.isfile(cfg_path)
    assert "cli" in reg.ids()
    assert "engine" in reg.ids()
    assert "storage" in reg.ids()


# --------------------------------------------------------------------------- #
# 4. End-to-End CLI Scan with Dynamic Layers
# --------------------------------------------------------------------------- #

def test_cli_propose_layers_auto_command(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\ntool = "tool.cli:main"\n', encoding="utf-8"
    )
    runner = CliRunner()
    res = runner.invoke(cli, ["propose-layers", "--path", str(tmp_path), "--auto"])
    assert res.exit_code == 0
    assert "Automatically configured" in res.output

    cfg_file = tmp_path / ".codechakra" / "layers.config.yaml"
    assert cfg_file.is_file()


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
    assert "Configured" in res.output or "Scanning repository" in res.output

    # Verify layers.config.yaml was created with CLI archetype
    cfg_file = tmp_path / ".codechakra" / "layers.config.yaml"
    assert cfg_file.is_file()

    # Query command should work with the dynamic layers
    res_layers = runner.invoke(cli, ["layers", "--path", str(tmp_path)])
    assert res_layers.exit_code == 0
    assert "CodeChakra Multi-Layer Architecture Summary" in res_layers.output
