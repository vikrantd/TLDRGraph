"""
Phase 1 -- the vector store's docstring used to claim FastEmbed (ONNX) support
that did not exist anywhere in the package. These tests pin down what is real.

Two hard constraints shape everything here:

1. **Nothing in this file may touch the network or download a model.** Every
   dense-path test is gated on the model already being cached locally and skips
   cleanly otherwise, and the gate itself is pure filesystem inspection. The
   TF-IDF path -- which is what CI and the default install actually run -- is
   covered unconditionally.

2. **TF-IDF must remain byte-for-byte the default.** Bridge resolution in
   ``graph_loader`` / ``cli`` resolves agent-supplied ``calls`` names through
   ``search()`` against a 0.35 floor. That is exact-identifier retrieval, TF-IDF
   is good at it, and a dense backend silently taking over would wreck it.
"""

import json
import os

import pytest

from codechakra import vector_store as vs
from codechakra.graph_loader import BRIDGE_SCORE_FLOOR, bridge_score_floor
from codechakra.vector_store import (
    BACKEND_HYBRID,
    BACKEND_TFIDF,
    DenseEmbedder,
    INDEX_FORMAT_VERSION,
    LocalVectorStore,
    SCORE_FLOORS,
)


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #

DOCS = [
    {
        "id": "svc_pension",
        "label": "PensionCalculatorService",
        "layer": "Layer 3: Service & Business Logic",
        "file": "backend/src/pension/pension-calculator.service.ts",
        "intent": "Computes the commuted value and monthly entitlement for a case.",
        "fields": ["basicPay", "commutationFactor"],
    },
    {
        "id": "guard_auth",
        "label": "JwtAuthGuard",
        "layer": "Layer 2: API & Routing",
        "file": "backend/src/auth/session-auth.guard.ts",
        "intent": "Rejects requests that carry no valid session before any handler runs.",
        "fields": [],
    },
    {
        "id": "ctrl_cases",
        "label": "CasesController",
        "layer": "Layer 2: API & Routing",
        "file": "backend/src/cases/cases.controller.ts",
        "intent": "HTTP entry point for creating and listing pension cases.",
        "fields": ["caseId"],
    },
    {
        "id": "model_case",
        "label": "PensionCase",
        "layer": "Layer 4: Data & Persistence",
        "file": "backend/prisma/schema.prisma",
        "intent": "Database table holding one row per submitted pension case.",
        "fields": ["id", "status"],
    },
    {
        "id": "util_fmt",
        "label": "formatCurrency",
        "layer": "Utility & Shared",
        "file": "shared/utils/format.ts",
        "intent": "Renders a number as rupees.",
        "fields": [],
    },
]

#: Cheap, offline, filesystem-only probe. Never triggers a download.
MODEL_AVAILABLE = (
    DenseEmbedder.fastembed_version() is not None
    and DenseEmbedder.model_present()
)

requires_model = pytest.mark.skipif(
    not MODEL_AVAILABLE,
    reason="embedding model not cached locally; refusing to download one in a test",
)


@pytest.fixture
def index_path(tmp_path):
    return str(tmp_path / ".codechakra" / "vector_index.json")


@pytest.fixture
def tfidf_store(index_path):
    store = LocalVectorStore(index_path, embeddings="off")
    store.add_documents([dict(d) for d in DOCS])
    return store


@pytest.fixture
def hybrid_store(index_path):
    store = LocalVectorStore(index_path, embeddings="auto")
    store.add_documents([dict(d) for d in DOCS])
    return store


# --------------------------------------------------------------------------- #
# The default is TF-IDF, and it is honest about it
# --------------------------------------------------------------------------- #

def test_default_backend_is_tfidf(index_path, monkeypatch):
    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    store = LocalVectorStore(index_path)
    assert store.policy == vs.POLICY_OFF
    assert store.backend == BACKEND_TFIDF


def test_off_policy_never_imports_fastembed(index_path, monkeypatch):
    """
    The whole point of the optional extra: on the default path the store must not
    reach for fastembed at all, so an install without it behaves identically.
    """
    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    store = LocalVectorStore(index_path)
    store.add_documents([dict(d) for d in DOCS])

    assert store.embedder is None
    assert store.doc_embeddings is None
    assert store.backend == BACKEND_TFIDF
    assert not os.path.exists(store.embeddings_sidecar_path())


@pytest.mark.parametrize("value,expected", [
    (None, vs.POLICY_OFF),
    ("", vs.POLICY_OFF),
    ("off", vs.POLICY_OFF),
    ("0", vs.POLICY_OFF),
    ("false", vs.POLICY_OFF),
    ("auto", vs.POLICY_AUTO),
    ("AUTO", vs.POLICY_AUTO),
    ("on", vs.POLICY_ON),
    ("1", vs.POLICY_ON),
    ("true", vs.POLICY_ON),
    ("nonsense", vs.POLICY_OFF),
])
def test_policy_parsing(value, expected, monkeypatch):
    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    assert vs._resolve_policy(value) == expected


def test_env_var_selects_policy(index_path, monkeypatch):
    monkeypatch.setenv(vs.EMBEDDINGS_ENV_VAR, "auto")
    assert LocalVectorStore(index_path).policy == vs.POLICY_AUTO


def test_constructor_argument_beats_env_var(index_path, monkeypatch):
    monkeypatch.setenv(vs.EMBEDDINGS_ENV_VAR, "on")
    assert LocalVectorStore(index_path, embeddings="off").policy == vs.POLICY_OFF


def test_module_docstring_does_not_overclaim():
    """
    The regression this whole phase exists for: the module claimed FastEmbed
    (ONNX) support while containing no embedding code whatsoever. If it names the
    feature it must also name the fallback.
    """
    doc = vs.__doc__ or ""
    assert "TF-IDF" in doc
    lowered = doc.lower()
    assert "optional" in lowered or "opt-in" in lowered
    assert "falls back" in lowered or "fall back" in lowered or "fallback" in lowered


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #

def test_auto_policy_falls_back_when_fastembed_is_missing(index_path, monkeypatch):
    """Simulate the package simply not being installed."""
    import builtins
    real_import = builtins.__import__

    def _no_fastembed(name, *args, **kwargs):
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError("no fastembed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_fastembed)
    monkeypatch.setattr(vs, "_MODEL_CACHE", {})

    store = LocalVectorStore(index_path, embeddings="auto")
    store.add_documents([dict(d) for d in DOCS])

    assert store.backend == BACKEND_TFIDF
    assert "fastembed" in store.embedder.reason
    assert store.search("PensionCalculatorService", top_k=1), "search must still work"


def test_auto_policy_never_downloads_when_model_absent(index_path, monkeypatch, tmp_path):
    """
    ``auto`` pointed at an empty cache directory must degrade to TF-IDF rather
    than fetching ~67 MB. This is the guarantee that makes ``auto`` safe to run
    offline and in CI.
    """
    empty_cache = str(tmp_path / "empty-model-cache")
    store = LocalVectorStore(index_path, embeddings="auto", model_cache_dir=empty_cache)
    store.add_documents([dict(d) for d in DOCS])

    assert store.backend == BACKEND_TFIDF
    assert store.doc_embeddings is None
    assert store.search("JwtAuthGuard", top_k=1)[0][0]["id"] == "guard_auth"


def test_broken_embedder_degrades_to_tfidf(tfidf_store, monkeypatch):
    """A model that loads but fails to encode must not take search down with it."""
    class Broken:
        available = True
        reason = ""
        dim = 384
        _np = None

        def encode(self, texts):
            return None

    monkeypatch.setattr(type(tfidf_store), "embedder", property(lambda self: Broken()))
    assert tfidf_store._dense_scores("anything") is None
    hits = tfidf_store.search("PensionCalculatorService", top_k=1)
    assert hits and hits[0][0]["id"] == "svc_pension"


def test_model_present_probe_is_filesystem_only(tmp_path):
    """The gate ``auto`` relies on must not need a network or a real model."""
    assert DenseEmbedder.model_present(cache_dir=str(tmp_path / "nope")) is False


# --------------------------------------------------------------------------- #
# Score floors are per backend
# --------------------------------------------------------------------------- #

def test_tfidf_floor_is_unchanged():
    """The historical value. Bridge behaviour is calibrated against it."""
    assert SCORE_FLOORS[BACKEND_TFIDF] == 0.35
    assert BRIDGE_SCORE_FLOOR == 0.35


def test_backends_do_not_share_a_floor():
    assert SCORE_FLOORS[BACKEND_HYBRID] != SCORE_FLOORS[BACKEND_TFIDF]


def test_hybrid_floor_is_derived_from_the_lexical_weight():
    """
    Not a tuned constant: a document that exactly meets the TF-IDF floor with
    zero dense signal fuses to (1 - w_identifier) * 0.35, so cutting there keeps
    hybrid exactly as permissive as TF-IDF in the worst case.
    """
    expected = round((1.0 - vs.DENSE_WEIGHT_IDENTIFIER) * SCORE_FLOORS[BACKEND_TFIDF], 3)
    assert SCORE_FLOORS[BACKEND_HYBRID] == expected
    assert SCORE_FLOORS[BACKEND_HYBRID] < SCORE_FLOORS[BACKEND_TFIDF]


def test_store_reports_the_floor_of_its_live_backend(tfidf_store):
    assert tfidf_store.score_floor == SCORE_FLOORS[BACKEND_TFIDF]
    assert bridge_score_floor(tfidf_store) == SCORE_FLOORS[BACKEND_TFIDF]


def test_bridge_score_floor_survives_a_junk_store():
    class Junk:
        @property
        def score_floor(self):
            raise RuntimeError("boom")

    assert bridge_score_floor(Junk()) == BRIDGE_SCORE_FLOOR


# --------------------------------------------------------------------------- #
# Query shape drives the fusion weight
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("query", [
    "ApplicationsService", "calc.ts", "pension_cases", "prisma",
    "AaoDeskView()", "generate-epo-template-odisha.ts", "two words",
])
def test_identifier_queries_stay_lexical_dominant(query):
    assert LocalVectorStore._dense_weight(query) == vs.DENSE_WEIGHT_IDENTIFIER


@pytest.mark.parametrize("query", [
    "where is the commutation amount calculated",
    "how does a pension case get approved",
    "what stops an unauthorized user from acting",
])
def test_prose_queries_go_dense_dominant(query):
    assert LocalVectorStore._dense_weight(query) == vs.DENSE_WEIGHT_PROSE


def test_lexical_weight_dominates_for_identifiers():
    assert vs.DENSE_WEIGHT_IDENTIFIER < 0.5
    assert vs.DENSE_WEIGHT_PROSE > 0.5


# --------------------------------------------------------------------------- #
# Index format versioning
# --------------------------------------------------------------------------- #

def test_index_records_its_format_version(tfidf_store, index_path):
    data = json.loads(open(index_path, encoding="utf-8").read())
    assert data["format_version"] == INDEX_FORMAT_VERSION


def test_legacy_v1_index_is_rebuilt_not_crashed(index_path):
    """
    A pre-Phase-1 index has no ``format_version``. Loading it must not raise and
    must not be half-interpreted: the store comes up empty and the next
    ``add_documents`` rebuilds it.
    """
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({
            "documents": [{"id": "old", "label": "Legacy", "layer": "L", "file": "a.ts"}],
            "idf": {"legacy": 1.0},
            "doc_vectors": [{"legacy": 1.0}],
        }, f)

    store = LocalVectorStore(index_path, embeddings="off")
    assert store.documents == []
    assert store.search("Legacy") == []
    assert store.diagnostics()["index_format_version"] == 1

    store.add_documents([dict(d) for d in DOCS])
    assert store.search("PensionCalculatorService", top_k=1)[0][0]["id"] == "svc_pension"
    assert json.loads(open(index_path, encoding="utf-8").read())["format_version"] == \
        INDEX_FORMAT_VERSION


def test_future_format_version_is_rebuilt_not_crashed(index_path):
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"format_version": 999, "documents": [{"id": "x"}],
                   "idf": {}, "doc_vectors": [{}]}, f)
    store = LocalVectorStore(index_path, embeddings="off")
    assert store.documents == []


def test_corrupt_index_is_survived(index_path):
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("{not json at all")
    store = LocalVectorStore(index_path, embeddings="off")
    assert store.documents == []
    store.add_documents([dict(d) for d in DOCS])
    assert store.search("JwtAuthGuard", top_k=1)


# --------------------------------------------------------------------------- #
# Diagnostics / doctor
# --------------------------------------------------------------------------- #

def test_diagnostics_reports_the_backend_that_is_really_live(tfidf_store):
    d = tfidf_store.diagnostics()
    assert d["backend"] == BACKEND_TFIDF
    assert d["document_count"] == len(DOCS)
    assert d["index_exists"] and d["index_bytes"] > 0
    assert d["embedding_coverage"] == 0
    assert d["score_floor"] == SCORE_FLOORS[BACKEND_TFIDF]
    assert d["index_format_version"] == INDEX_FORMAT_VERSION


def test_diagnostics_explains_why_dense_is_off(index_path, monkeypatch):
    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    d = LocalVectorStore(index_path).diagnostics()
    assert d["embedder_available"] is False
    assert d["embedder_reason"], "doctor must always be able to say WHY"


def test_doctor_command_runs_without_embeddings(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from codechakra.cli import cli

    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    result = CliRunner().invoke(cli, ["doctor", "--path", str(tmp_path), "--embeddings", "off"])
    assert result.exit_code == 0, result.output
    assert "TFIDF" in result.output
    assert "MISSING" in result.output  # no index scanned yet


def test_doctor_json_is_machine_readable(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from codechakra.cli import cli

    monkeypatch.delenv(vs.EMBEDDINGS_ENV_VAR, raising=False)
    result = CliRunner().invoke(
        cli, ["doctor", "--path", str(tmp_path), "--embeddings", "off", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["backend"] == BACKEND_TFIDF
    assert payload["score_floors"][BACKEND_TFIDF] == 0.35
    for key in ("model_present", "embedding_coverage", "embedding_dim",
                "index_format_version", "model_cache_dir", "fastembed_version"):
        assert key in payload


# --------------------------------------------------------------------------- #
# Text preparation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("CaseWorkflowService", "Case Workflow Service"),
    ("clearCsrfToken", "clear Csrf Token"),
    ("pension_cases", "pension cases"),
    ("AAODesk", "AAO Desk"),
])
def test_identifiers_are_split_into_words_for_the_dense_side(raw, expected):
    """A prose-trained model cannot read ``getPensionCaseById`` unsplit."""
    assert LocalVectorStore._humanize(raw) == expected


def test_embed_text_prefers_intent_over_placeholder_summary():
    store_text = LocalVectorStore._embed_text(LocalVectorStore, {
        "label": "CaseWorkflowService",
        "layer": "Layer 3",
        "file": "backend/src/cases/case-workflow.service.ts",
        "summary": "Layer 3: CaseWorkflowService located at backend/src/cases/case-workflow.service.ts",
        "intent": "Advances a case to the next desk.",
    })
    assert "Advances a case to the next desk." in store_text
    assert "located at" not in store_text
    assert "Case Workflow Service" in store_text


def test_content_hash_tracks_searchable_content():
    a = {"label": "X", "layer": "L", "file": "f.ts", "intent": "one"}
    b = dict(a, intent="two")
    h = LocalVectorStore._content_hash
    t = LocalVectorStore._embed_text
    assert h(t(LocalVectorStore, a)) == h(t(LocalVectorStore, dict(a)))
    assert h(t(LocalVectorStore, a)) != h(t(LocalVectorStore, b))


# --------------------------------------------------------------------------- #
# Dense path -- ONLY when the model is already cached locally
# --------------------------------------------------------------------------- #

@requires_model
def test_hybrid_backend_activates_when_model_is_present(hybrid_store):
    assert hybrid_store.backend == BACKEND_HYBRID
    assert hybrid_store.embedder.available
    assert hybrid_store.embedder.dim == 384
    assert hybrid_store.doc_embeddings is not None
    assert len(hybrid_store.doc_embeddings) == len(DOCS)
    assert hybrid_store.score_floor == SCORE_FLOORS[BACKEND_HYBRID]


@requires_model
def test_hybrid_still_resolves_exact_identifiers(hybrid_store):
    """
    The load-bearing regression guard. Bridge resolution must keep landing on the
    same node, above the hybrid floor.
    """
    for query, expected in [
        ("PensionCalculatorService", "svc_pension"),
        ("JwtAuthGuard", "guard_auth"),
        ("CasesController", "ctrl_cases"),
        ("formatCurrency", "util_fmt"),
    ]:
        hits = hybrid_store.search(query, top_k=1)
        assert hits, f"{query} resolved to nothing under hybrid"
        doc, score = hits[0]
        assert doc["id"] == expected, f"{query} -> {doc['id']}, expected {expected}"
        assert score >= hybrid_store.score_floor, f"{query} fell below the hybrid floor"


@requires_model
def test_gibberish_creates_no_match_under_hybrid(hybrid_store):
    """Dense cosines never reach zero on their own -- the rescale must handle it."""
    for junk in ("qqqzzzxxwwvvuu", "xyzzy plugh frotz"):
        hits = hybrid_store.search(junk, top_k=1)
        assert not hits or hits[0][1] < hybrid_store.score_floor, (
            f"{junk!r} produced a bridge-worthy score: {hits[0][1] if hits else None}"
        )


@requires_model
def test_dense_beats_tfidf_on_a_query_with_no_lexical_overlap(tfidf_store, hybrid_store):
    """
    'who is not allowed to touch a record' shares no token with JwtAuthGuard or
    its file, so TF-IDF has nothing to work with.
    """
    query = "what blocks a request from someone who is not signed in"
    lex_rank = [d["id"] for d, _ in tfidf_store.search(query, top_k=5)]
    hyb_rank = [d["id"] for d, _ in hybrid_store.search(query, top_k=5)]

    assert hyb_rank, "hybrid returned nothing"
    assert hyb_rank[0] == "guard_auth", f"hybrid ranked {hyb_rank}"
    assert lex_rank[:1] != ["guard_auth"] or "guard_auth" not in lex_rank, (
        "corpus is too easy for this assertion to mean anything"
    )


@requires_model
def test_vectors_are_cached_by_content_hash(hybrid_store):
    """Re-indexing identical content must encode nothing."""
    hybrid_store.add_documents([dict(d) for d in DOCS])
    d = hybrid_store.diagnostics()
    assert d["encoded_last_run"] == 0
    assert d["reused_last_run"] == len(DOCS)


@requires_model
def test_only_changed_documents_are_re_encoded(hybrid_store):
    edited = [dict(x) for x in DOCS]
    edited[0] = dict(edited[0], intent="Completely different intent text now.")
    hybrid_store.add_documents(edited)

    d = hybrid_store.diagnostics()
    assert d["encoded_last_run"] == 1, "a one-node edit re-encoded the whole corpus"
    assert d["reused_last_run"] == len(DOCS) - 1


@requires_model
def test_vectors_survive_a_process_restart(hybrid_store, index_path):
    """A fresh store reloads vectors from the sidecar and encodes nothing."""
    assert os.path.exists(hybrid_store.embeddings_sidecar_path())

    reloaded = LocalVectorStore(index_path, embeddings="auto")
    assert reloaded.backend == BACKEND_HYBRID
    assert reloaded.doc_embeddings is not None
    assert len(reloaded.doc_embeddings) == len(DOCS)
    assert reloaded.diagnostics()["encoded_last_run"] == 0


@requires_model
def test_sidecar_is_float32_not_json_floats(hybrid_store):
    import numpy as np
    with np.load(hybrid_store.embeddings_sidecar_path(), allow_pickle=False) as data:
        assert data["vectors"].dtype == np.float32
        assert data["vectors"].shape[1] == 384
        assert len(data["hashes"]) == len(DOCS)


@requires_model
def test_dense_vectors_are_l2_normalized(hybrid_store):
    import numpy as np
    norms = np.linalg.norm(hybrid_store.doc_embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


@requires_model
def test_sidecar_from_a_different_model_is_ignored(hybrid_store, index_path):
    """Vectors from another model are not comparable and must not be reused."""
    data = json.loads(open(index_path, encoding="utf-8").read())
    data["embeddings"]["model"] = "some/other-model"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    reloaded = LocalVectorStore(index_path, embeddings="auto")
    assert reloaded._vector_cache == {}
    assert reloaded.backend == BACKEND_TFIDF


@requires_model
def test_doctor_reports_hybrid_when_it_is_live(tmp_path, hybrid_store):
    from click.testing import CliRunner
    from codechakra.cli import cli

    root = os.path.dirname(os.path.dirname(hybrid_store.index_path))
    result = CliRunner().invoke(cli, ["doctor", "--path", root, "--embeddings", "auto", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["backend"] == BACKEND_HYBRID
    assert payload["model_present"] is True
    assert payload["embedding_dim"] == 384
    assert payload["embedding_coverage"] == len(DOCS)
    assert payload["embeddings_sidecar_exists"] is True


# --------------------------------------------------------------------------- #
# The TF-IDF path is untouched
# --------------------------------------------------------------------------- #

def test_tfidf_ranking_is_unchanged_by_this_phase(tfidf_store):
    """Exact-identifier retrieval, the thing bridge resolution depends on."""
    for query, expected in [
        ("PensionCalculatorService", "svc_pension"),
        ("JwtAuthGuard", "guard_auth"),
        ("CasesController", "ctrl_cases"),
        ("formatCurrency", "util_fmt"),
        ("session-auth.guard.ts", "guard_auth"),
    ]:
        hits = tfidf_store.search(query, top_k=1)
        assert hits and hits[0][0]["id"] == expected
        assert hits[0][1] >= SCORE_FLOORS[BACKEND_TFIDF]


def test_tfidf_rejects_gibberish(tfidf_store):
    assert tfidf_store.search("qqqzzzxxwwvvuu") == []


def test_empty_store_searches_cleanly(index_path):
    assert LocalVectorStore(index_path, embeddings="off").search("anything") == []


def test_graph_loader_defaults_to_tfidf(mini_repo):
    """End-to-end: a normal scan must not silently activate a dense backend."""
    from codechakra.graph_loader import GraphLoader

    loader = GraphLoader(str(mini_repo.root))
    loader.load_or_extract(enrich_llm=False)
    assert loader.vector_store.backend == BACKEND_TFIDF
    assert bridge_score_floor(loader.vector_store) == 0.35
