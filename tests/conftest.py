"""
Hermetic fixtures for the TLDRGraph regression suite.

Everything the tests touch lives under pytest's ``tmp_path``. Nothing here reads
the real repository, the real ``graphify-out/`` directory, or the real
``.tldrgraph/`` state, and nothing makes a network call.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import hygiene
#
# The distribution is installed editable from `<repo>/codechakra`, but when
# pytest is launched from the repo root the *outer* `tldrgraph/` directory
# (which has no __init__.py) shadows the real package as a namespace package,
# and `tldrgraph.__version__` silently disappears. Put the real package parent
# first on sys.path so `import tldrgraph` always resolves to the source tree
# under test regardless of cwd.
# ---------------------------------------------------------------------------
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)
if _PKG_PARENT in sys.path:
    sys.path.remove(_PKG_PARENT)
sys.path.insert(0, _PKG_PARENT)

from tldrgraph.classifier import LayerType  # noqa: E402
from tldrgraph.graph_loader import GraphLoader  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic graphify output
# ---------------------------------------------------------------------------

#: logical name -> graphify node id
NODE_IDS = {
    "ui_page": "frontend_src_app_cases_page_submitcasebutton",
    "ui_modal": "frontend_src_components_caseformmodal_caseformmodal",
    "api_controller": "backend_src_cases_cases_controller_casescontroller",
    "svc_workflow": "backend_src_cases_case_workflow_service_caseworkflowservice",
    "svc_pension": "backend_src_pension_pension_calculator_service_pensioncalculatorservice",
    "data_prisma": "backend_prisma_schema_prisma_prismacasemodel",
    "async_poll": "backend_src_polling_case_status_polling_casestatuspollingjob",
    "devops_ci": "github_workflows_ci_yml_cideployworkflow",
    "util_format": "shared_utils_format_formatcurrency",
}

#: logical name -> (label, source_file, source_location, file_type, community, expected layer)
NODE_SPECS = {
    "ui_page": (
        "SubmitCaseButton",
        "frontend/src/app/cases/page.tsx",
        "L88",
        "code",
        0,
        LayerType.LAYER_1_UI,
    ),
    "ui_modal": (
        "CaseFormModal",
        "frontend/src/components/CaseFormModal.tsx",
        "L12",
        "code",
        0,
        LayerType.LAYER_1_UI,
    ),
    "api_controller": (
        "CasesController",
        "backend/src/cases/cases.controller.ts",
        "L31",
        "code",
        1,
        LayerType.LAYER_2_API,
    ),
    "svc_workflow": (
        "CaseWorkflowService",
        "backend/src/cases/case-workflow.service.ts",
        "L44",
        "code",
        1,
        LayerType.LAYER_3_SERVICE,
    ),
    "svc_pension": (
        "PensionCalculatorService",
        "backend/src/pension/pension-calculator.service.ts",
        "L17",
        "code",
        2,
        LayerType.LAYER_3_SERVICE,
    ),
    "data_prisma": (
        "PrismaCaseModel",
        "backend/prisma/schema.prisma",
        "L7",
        "code",
        3,
        LayerType.LAYER_4_DATA,
    ),
    "async_poll": (
        "CaseStatusPollingJob",
        "backend/src/polling/case-status.polling.ts",
        "L23",
        "code",
        4,
        LayerType.LAYER_5_ASYNC,
    ),
    "devops_ci": (
        "CiDeployWorkflow",
        ".github/workflows/ci.yml",
        "L1",
        "config",
        5,
        LayerType.LAYER_6_DEVOPS,
    ),
    "util_format": (
        "formatCurrency",
        "shared/utils/format.ts",
        "L3",
        "code",
        6,
        LayerType.UNKNOWN,
    ),
}

#: AST edges emitted by graphify, as (src key, tgt key, relation)
EDGE_SPECS = [
    ("ui_modal", "ui_page", "imports"),
    ("ui_page", "api_controller", "calls"),
    ("api_controller", "svc_workflow", "calls"),
    ("svc_workflow", "svc_pension", "calls"),
    ("svc_workflow", "data_prisma", "references"),
    ("async_poll", "svc_workflow", "calls"),
]

#: (src key, tgt key) with no AST edge -- free for bridge-edge tests.
FREE_PAIR = ("ui_page", "async_poll")

#: source_file -> tiny but real on-disk content
FILE_CONTENTS = {
    "frontend/src/app/cases/page.tsx": (
        "export function SubmitCaseButton() {\n"
        "  return <button onClick={submitCase}>Submit Case</button>;\n"
        "}\n"
    ),
    "frontend/src/components/CaseFormModal.tsx": (
        "export function CaseFormModal() {\n"
        "  return <SubmitCaseButton />;\n"
        "}\n"
    ),
    "backend/src/cases/cases.controller.ts": (
        "@Controller('cases')\n"
        "export class CasesController {\n"
        "  create(dto) { return this.workflow.run(dto); }\n"
        "}\n"
    ),
    "backend/src/cases/case-workflow.service.ts": (
        "export class CaseWorkflowService {\n"
        "  run(dto) { return this.pension.compute(dto); }\n"
        "}\n"
    ),
    "backend/src/pension/pension-calculator.service.ts": (
        "export class PensionCalculatorService {\n"
        "  compute(input) { return input.basicPay * 0.5; }\n"
        "}\n"
    ),
    "backend/prisma/schema.prisma": (
        "model PrismaCaseModel {\n  id Int @id\n  status String\n}\n"
    ),
    "backend/src/polling/case-status.polling.ts": (
        "export class CaseStatusPollingJob {\n"
        "  @Cron('*/5 * * * *') tick() { return this.workflow.sweep(); }\n"
        "}\n"
    ),
    ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n",
    "shared/utils/format.ts": "export const formatCurrency = (n) => `Rs ${n}`;\n",
}


def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class MiniRepo:
    """Handle onto the synthetic repository materialized in ``tmp_path``."""

    def __init__(self, root: Path):
        self.root = root
        self.node_ids = dict(NODE_IDS)
        self.node_specs = dict(NODE_SPECS)
        self.edge_specs = list(EDGE_SPECS)

    # -- convenience -------------------------------------------------------
    @property
    def graphify_dir(self) -> Path:
        return self.root / "graphify-out"

    @property
    def graph_json(self) -> Path:
        return self.graphify_dir / "graph.json"

    @property
    def manifest_json(self) -> Path:
        return self.graphify_dir / "manifest.json"

    @property
    def tldrgraph_dir(self) -> Path:
        return self.root / ".tldrgraph"

    @property
    def snapshot_path(self) -> Path:
        return self.tldrgraph_dir / "graph.json"

    @property
    def index_path(self) -> Path:
        return self.tldrgraph_dir / "vector_index.json"

    @property
    def db_path(self) -> Path:
        return self.tldrgraph_dir / "tldrgraph.db"

    def nid(self, key: str) -> str:
        return self.node_ids[key]

    def source_file(self, key: str) -> str:
        return self.node_specs[key][1]

    def label(self, key: str) -> str:
        return self.node_specs[key][0]

    def expected_layer(self, key: str) -> str:
        return self.node_specs[key][5].value

    def placeholder_summary(self, key: str) -> str:
        label, src, *_ = self.node_specs[key]
        return f"{self.expected_layer(key)}: {label} located at {src}"

    def read_manifest(self) -> dict:
        return json.loads(self.manifest_json.read_text(encoding="utf-8"))

    def write_manifest(self, data: dict) -> None:
        self.manifest_json.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def bump_semantic_hash(self, key: str, new_value: str = "deadbeef" * 4) -> None:
        """Simulate a real content edit landing in graphify's manifest."""
        manifest = self.read_manifest()
        manifest[self.source_file(key)]["semantic_hash"] = new_value
        manifest[self.source_file(key)]["mtime"] = time.time()
        self.write_manifest(manifest)


@pytest.fixture(autouse=True)
def env_no_llm(monkeypatch):
    """
    Force the deterministic offline path.

    Clears every provider key the enricher looks at and points OLLAMA_HOST at a
    port nothing listens on, so ``_call_ollama`` fails immediately with
    connection-refused instead of reaching the network.
    """
    for var in (
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)
    # Port 9 (discard) on loopback: refused instantly, never leaves the host.
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    return True


@pytest.fixture
def mini_repo(tmp_path, env_no_llm) -> MiniRepo:
    """Materialize a hermetic mini-repo with graphify output + real sources."""
    root = tmp_path / "minirepo"
    graphify = root / "graphify-out"
    graphify.mkdir(parents=True)

    # 1. Real source files on disk (so the sha256 fallback path is reachable).
    for rel, content in FILE_CONTENTS.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    # 2. graphify-out/graph.json
    nodes = []
    for key, (label, src, loc, ftype, community, _layer) in NODE_SPECS.items():
        nodes.append(
            {
                "id": NODE_IDS[key],
                "label": label,
                "file_type": ftype,
                "source_file": src,
                "source_location": loc,
                "community": community,
                "norm_label": label.lower(),
                "_callable": ftype == "code",
                "_origin": "ast",
            }
        )

    links = []
    for src_key, tgt_key, relation in EDGE_SPECS:
        links.append(
            {
                "relation": relation,
                "context": "call",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "weight": 1.0,
                "source": NODE_IDS[src_key],
                "target": NODE_IDS[tgt_key],
                "source_file": NODE_SPECS[src_key][1],
                "source_location": NODE_SPECS[src_key][2],
                "_origin": "ast",
            }
        )

    graph_doc = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": nodes,
        "links": links,
        "hyperedges": [],
        "built_at_commit": "0000000",
    }
    (graphify / "graph.json").write_text(json.dumps(graph_doc, indent=2), encoding="utf-8")

    # 3. graphify-out/manifest.json -- one entry per source_file.
    now = time.time()
    manifest = {}
    for rel, content in FILE_CONTENTS.items():
        manifest[rel] = {
            "mtime": now,
            "seen": now,
            "ast_hash": _hash("ast:" + content),
            "semantic_hash": _hash("sem:" + content),
        }
    (graphify / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return MiniRepo(root)


@pytest.fixture
def loader(mini_repo) -> GraphLoader:
    """A GraphLoader rooted at the synthetic mini-repo (nothing scanned yet)."""
    return GraphLoader(str(mini_repo.root))


@pytest.fixture
def new_loader(mini_repo):
    """Factory for additional, independent GraphLoaders on the same root."""

    def _factory() -> GraphLoader:
        return GraphLoader(str(mini_repo.root))

    return _factory


@pytest.fixture
def stub_enricher(monkeypatch):
    """
    Replace ``LLMEnricher.enrich_batch`` with a deterministic stub.

    Usage::

        stub_enricher(lambda node: ["PensionCalculatorService"])

    The callable receives each node dict and returns the ``calls`` list for it.
    """
    from tldrgraph.llm_enricher import LLMEnricher

    def _install(calls_for, intent_for=None, fields_for=None):
        def fake_enrich_batch(self, nodes_batch):
            out = []
            for n in nodes_batch:
                out.append(
                    {
                        "id": n["id"],
                        "intent": (
                            intent_for(n)
                            if intent_for
                            else f"Stubbed intent for {n.get('label', n['id'])}."
                        ),
                        "fields": fields_for(n) if fields_for else ["caseId"],
                        "calls": list(calls_for(n) or []),
                    }
                )
            return out

        monkeypatch.setattr(LLMEnricher, "enrich_batch", fake_enrich_batch)
        return fake_enrich_batch

    return _install


@pytest.fixture
def no_network(monkeypatch):
    """Hard guarantee: any urlopen attempt during the test is a failure."""
    import urllib.request

    def _boom(*a, **kw):  # pragma: no cover - only fires on regression
        raise AssertionError("test attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    return True


def assert_no_real_repo(path):
    """Guard: never let a test point at the developer's actual working tree."""
    resolved = os.path.abspath(str(path))
    assert "/minirepo" in resolved or "pytest" in resolved, (
        f"refusing to operate on non-temporary path {resolved!r}"
    )
