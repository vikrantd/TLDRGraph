"""
Deterministic cross-layer seam extraction + the dead-code reachability cascade.

graphify emits AST edges only, so the HTTP boundary and the Prisma schema are
invisible to it. ``codechakra.extractors`` re-derives both from source text and
``codechakra.deadcode`` decides what that (still) says about reachability.

Everything here is hermetic: fixtures are local to this module, built in
``tmp_path``, and no test reads the real repository.
"""

import json

import networkx as nx
import pytest

from codechakra import extractors as ex
from codechakra.classifier import LayerType, classify_node
from codechakra.deadcode import (
    CANDIDATE_COVERAGE_FLOOR,
    STATUS_CANDIDATE,
    STATUS_ENTRY_POINT,
    STATUS_LIVE,
    STATUS_NOT_CODE,
    STATUS_UNREVIEWED,
    classify_dead_code,
    compute_enrichment_coverage,
    entry_point_reason,
)
from codechakra.graph_loader import BRIDGE_RELATIONS, GraphLoader


# --------------------------------------------------------------------------- #
# Local fixture material
# --------------------------------------------------------------------------- #

FRONTEND_PAGE = """\
import { api } from "@/lib/api"

export default function ApplicationsPage() {
  const load = async () => {
    const me = await api.get("/auth/me")
    const list = await api.get<Application[]>("/applications")
    const one = await api.get(`/applications/${id}`)
    await api.put(`/applications/${id}/status`, body)
    await api.delete(`/admin/users?id=${userId}`)
  }
  return null
}
"""

BACKEND_CONTROLLER = """\
import { Controller, Get, Put } from '@nestjs/common'

@Controller('applications')
export class ApplicationsController {
    constructor(private readonly service: ApplicationsService) {}

    @Get()
    async findAll() {
        return this.service.findAll()
    }

    @Get(':id')
    @Roles('SO')
    async findOne(@Param('id') id: string) {
        return this.service.findOne(id)
    }

    @Put(':id/status')
    async updateStatus(@Param('id') id: string) {
        return this.service.updateStatus(id)
    }
}
"""

SCHEMA_PRISMA = """\
generator client {
  provider = "prisma-client-js"
}

model Office {
  id   String @id @default(cuid())
  name String @unique

  users User[]
}

model User {
  id       String @id
  username String

  officeId String
  office   Office @relation(fields: [officeId], references: [id])
  sessions Session[]
}

model Session {
  id     String @id
  userId String
  user   User   @relation(fields: [userId], references: [id])
}

model NeverTouched {
  id String @id
}
"""

SERVICE_TS = """\
export class ApplicationsService {
    async findAll() {
        return this.prisma.user.findMany({ where: { isActive: true } })
    }

    async findOne(id: string) {
        return this.prisma.office.findUnique({
            where: { id },
            include: { users: true },
        })
    }

    async wipe(id: string) {
        return this.prisma.$transaction(async (tx) => {
            await tx.session.deleteMany({ where: { userId: id } })
        })
    }
}
"""


@pytest.fixture
def seam_repo(tmp_path):
    """A minimal repo with one frontend page, one controller, a schema and a service."""
    files = {
        "frontend/src/app/applications/page.tsx": FRONTEND_PAGE,
        "backend/src/applications/applications.controller.ts": BACKEND_CONTROLLER,
        "backend/src/applications/applications.service.ts": SERVICE_TS,
        "backend/prisma/schema.prisma": SCHEMA_PRISMA,
    }
    for rel, content in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# Path / method normalization
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("/auth/me", "/auth/me"),
    ("/applications/${id}", "/applications/:param"),
    ("/applications/${row.id}/rc-verify", "/applications/:param/rc-verify"),
    (":id/status", "/:param/status"),
    ("applications/:id", "/applications/:param"),
    ("/admin/users?id=${userId}", "/admin/users"),
    ("/admin/users/charge-history?userId=${userId}", "/admin/users/charge-history"),
    ("${BACKEND_URL}/csrf-token", "/csrf-token"),
    ("http://localhost:3001/api/master/statuses", "/master/statuses"),
    ("/api/master/statuses", "/master/statuses"),
    ("/Master/Statuses", "/master/statuses"),
    ("", "/"),
    ("/", "/"),
])
def test_normalize_route_path(raw, expected):
    assert ex.normalize_route_path(raw) == expected


def test_normalize_http_method_maps_del_to_delete():
    assert ex.normalize_http_method("del") == "delete"
    assert ex.normalize_http_method("DELETE") == "delete"
    assert ex.normalize_http_method("Get") == "get"


# --------------------------------------------------------------------------- #
# Frontend call sites
# --------------------------------------------------------------------------- #

def test_extract_frontend_calls_finds_every_literal_route():
    calls = ex.extract_frontend_calls("frontend/src/app/applications/page.tsx", FRONTEND_PAGE)
    found = {(c["method"], c["path"]) for c in calls}

    assert ("get", "/auth/me") in found
    assert ("get", "/applications") in found, "TS generic argument broke the match"
    assert ("get", "/applications/:param") in found
    assert ("put", "/applications/:param/status") in found
    assert ("delete", "/admin/users") in found


def test_extract_frontend_calls_records_the_real_line_number():
    calls = ex.extract_frontend_calls("page.tsx", FRONTEND_PAGE)
    by_path = {c["path"]: c for c in calls}
    line = by_path["/auth/me"]["line"]
    assert FRONTEND_PAGE.splitlines()[line - 1].strip().startswith("const me")


def test_non_route_string_arguments_are_ignored():
    calls = ex.extract_frontend_calls("x.ts", "const v = cache.get('someKey')\n")
    assert calls == []


def test_fetch_call_picks_up_an_explicit_method():
    content = "await fetch('/applications/1', { method: 'POST', body })\n"
    calls = ex.extract_frontend_calls("x.ts", content)
    assert [(c["method"], c["path"]) for c in calls] == [("post", "/applications/:param")]


# --------------------------------------------------------------------------- #
# Backend routes
# --------------------------------------------------------------------------- #

def test_extract_backend_routes_composes_the_controller_prefix():
    routes = ex.extract_backend_routes("c.ts", BACKEND_CONTROLLER)
    found = {(r["method"], r["path"]) for r in routes}

    assert ("get", "/applications") in found, "bare @Get() must inherit the controller base"
    assert ("get", "/applications/:param") in found
    assert ("put", "/applications/:param/status") in found


def test_extract_backend_routes_resolves_the_handler_past_stacked_decorators():
    routes = {r["path"] + "|" + r["method"]: r for r in ex.extract_backend_routes("c.ts", BACKEND_CONTROLLER)}
    assert routes["/applications|get"]["handler"] == "findAll"
    # @Get(':id') is followed by @Roles('SO') before the method itself.
    assert routes["/applications/:param|get"]["handler"] == "findOne"


def test_controller_without_a_path_still_yields_absolute_routes():
    content = "@Controller()\nexport class AppController {\n  @Get('csrf-token')\n  token() {}\n}\n"
    routes = ex.extract_backend_routes("app.controller.ts", content)
    assert [(r["method"], r["path"]) for r in routes] == [("get", "/csrf-token")]


# --------------------------------------------------------------------------- #
# Route matching
# --------------------------------------------------------------------------- #

def test_literal_route_beats_a_wildcard_sibling():
    calls = [{"method": "get", "path": "/applications/deo-history", "file": "f", "line": 1}]
    routes = [
        {"method": "get", "path": "/applications/:param", "file": "c", "line": 5, "handler": "findOne"},
        {"method": "get", "path": "/applications/deo-history", "file": "c", "line": 9, "handler": "deoHistory"},
    ]
    pairs = ex.match_http_routes(calls, routes)
    assert [route["handler"] for _, route in pairs] == ["deoHistory"]


def test_wildcard_route_absorbs_a_concrete_id():
    calls = [{"method": "get", "path": "/applications/:param", "file": "f", "line": 1}]
    routes = [{"method": "get", "path": "/applications/:param", "file": "c", "line": 5, "handler": "findOne"}]
    assert len(ex.match_http_routes(calls, routes)) == 1


def test_method_mismatch_never_matches():
    calls = [{"method": "post", "path": "/auth/me", "file": "f", "line": 1}]
    routes = [{"method": "get", "path": "/auth/me", "file": "c", "line": 5, "handler": "me"}]
    assert ex.match_http_routes(calls, routes) == []


def test_segment_count_mismatch_never_matches():
    calls = [{"method": "get", "path": "/applications/1/files", "file": "f", "line": 1}]
    routes = [{"method": "get", "path": "/applications/:param", "file": "c", "line": 5, "handler": "findOne"}]
    assert ex.match_http_routes(calls, routes) == []


# --------------------------------------------------------------------------- #
# Prisma schema
# --------------------------------------------------------------------------- #

def test_extract_prisma_models_names_lines_and_columns():
    models = {m["name"]: m for m in ex.extract_prisma_models(SCHEMA_PRISMA)}
    assert set(models) == {"Office", "User", "Session", "NeverTouched"}

    office = models["Office"]
    assert SCHEMA_PRISMA.splitlines()[office["line"] - 1].strip() == "model Office {"
    assert office["fields"][:2] == ["id", "name"]
    assert "model" not in office["fields"], "the model header leaked into the column list"


def test_prisma_model_node_id_is_stable_and_lowercased():
    assert ex.prisma_model_node_id("JhPensionApplication") == "prisma_model_jhpensionapplication"


def test_accessor_to_model_name_pascalizes():
    assert ex.accessor_to_model_name("jhPensionApplication") == "JhPensionApplication"


def test_build_relation_map_links_models_through_their_relation_fields():
    relations = ex.build_relation_map(ex.extract_prisma_models(SCHEMA_PRISMA))
    assert relations["Office"]["users"] == "User"
    assert relations["User"]["sessions"] == "Session"
    assert "id" not in relations["Office"], "a scalar column is not a relation"


# --------------------------------------------------------------------------- #
# Prisma call sites
# --------------------------------------------------------------------------- #

def test_extract_prisma_calls_maps_accessors_to_declared_models():
    known = {m["name"].lower(): m["name"] for m in ex.extract_prisma_models(SCHEMA_PRISMA)}
    calls = ex.extract_prisma_calls("s.ts", SERVICE_TS, known)
    assert {c["model"] for c in calls} == {"User", "Office", "Session"}


def test_transaction_client_calls_are_captured():
    known = {m["name"].lower(): m["name"] for m in ex.extract_prisma_models(SCHEMA_PRISMA)}
    calls = ex.extract_prisma_calls("s.ts", SERVICE_TS, known)
    assert any(c["client"] == "tx" and c["model"] == "Session" for c in calls)


def test_unknown_accessor_is_dropped():
    known = {m["name"].lower(): m["name"] for m in ex.extract_prisma_models(SCHEMA_PRISMA)}
    calls = ex.extract_prisma_calls("s.ts", "tx.response.create({})\n", known)
    assert calls == []


def test_non_prisma_operation_is_dropped():
    known = {"user": "User"}
    calls = ex.extract_prisma_calls("s.ts", "prisma.user.hydrate({})\n", known)
    assert calls == []


def test_nested_include_reaches_a_related_model():
    """
    ``include: { users: true }`` never names ``User``. Missing it is exactly how
    a live table gets proposed for deletion.
    """
    models = ex.extract_prisma_models(SCHEMA_PRISMA)
    known = {m["name"].lower(): m["name"] for m in models}
    relations = ex.build_relation_map(models)

    content = "prisma.office.findUnique({ where: { id }, include: { users: true } })\n"
    calls = ex.extract_prisma_calls("s.ts", content, known, relations)

    assert {(c["model"], c["via"]) for c in calls} == {("Office", "delegate"), ("User", "relation")}


def test_unrelated_object_key_cannot_pull_in_a_model():
    models = ex.extract_prisma_models(SCHEMA_PRISMA)
    known = {m["name"].lower(): m["name"] for m in models}
    relations = ex.build_relation_map(models)

    content = "prisma.office.findUnique({ where: { name: true, id: true } })\n"
    calls = ex.extract_prisma_calls("s.ts", content, known, relations)
    assert {c["model"] for c in calls} == {"Office"}


def test_hoisted_include_const_is_anchored_by_its_prisma_type():
    models = ex.extract_prisma_models(SCHEMA_PRISMA)
    known = {m["name"].lower(): m["name"] for m in models}
    relations = ex.build_relation_map(models)

    content = (
        "const OFFICE_INCLUDE = {\n"
        "    users: true,\n"
        "} satisfies Prisma.OfficeInclude\n"
    )
    calls = ex.extract_prisma_calls("s.ts", content, known, relations)
    assert "User" in {c["model"] for c in calls}


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

@pytest.fixture
def node_index():
    return ex.NodeIndex([
        {"id": "file_node", "label": "page.tsx", "file": "a/page.tsx", "source_location": "L1"},
        {"id": "helper", "label": "loadCases()", "file": "a/page.tsx", "source_location": "L10"},
        {"id": "component", "label": "CasesPage()", "file": "a/page.tsx", "source_location": "L40"},
        {"id": "orphan", "label": "Orphan", "file": "b/other.ts", "source_location": None},
    ])


def test_owner_is_the_closest_declaration_at_or_above(node_index):
    assert node_index.owner_of("a/page.tsx", 12) == "helper"
    assert node_index.owner_of("a/page.tsx", 40) == "component"
    assert node_index.owner_of("a/page.tsx", 999) == "component"


def test_owner_falls_back_to_the_file_level_node(node_index):
    # Line 1 is the file node itself; nothing declared above it.
    assert node_index.owner_of("a/page.tsx", 1) == "file_node"
    # A file with no line-bearing node at all still resolves to its file node.
    assert node_index.owner_of("b/other.ts", 5) == "orphan"


def test_unknown_file_is_skipped_silently(node_index):
    assert node_index.owner_of("nowhere/at/all.ts", 3) is None


def test_handler_lookup_is_by_name_not_by_line(node_index):
    assert node_index.node_named("a/page.tsx", "loadCases") == "helper"
    assert node_index.node_named("a/page.tsx", "nope") is None


# --------------------------------------------------------------------------- #
# Edge construction
# --------------------------------------------------------------------------- #

def test_build_http_route_edges_links_caller_to_handler():
    calls = ex.extract_frontend_calls("f/page.tsx", FRONTEND_PAGE)
    routes = ex.extract_backend_routes("b/applications.controller.ts", BACKEND_CONTROLLER)
    index = ex.NodeIndex([
        {"id": "ui", "label": "ApplicationsPage()", "file": "f/page.tsx", "source_location": "L3"},
        {"id": "ctl", "label": "ApplicationsController", "file": "b/applications.controller.ts",
         "source_location": "L4"},
        {"id": "findall", "label": ".findAll()", "file": "b/applications.controller.ts",
         "source_location": "L8"},
        {"id": "findone", "label": ".findOne()", "file": "b/applications.controller.ts",
         "source_location": "L14"},
        {"id": "updatestatus", "label": ".updateStatus()", "file": "b/applications.controller.ts",
         "source_location": "L19"},
    ])

    edges = ex.build_http_route_edges(calls, routes, index)
    targets = {e["target"] for e in edges}

    assert targets == {"findall", "findone", "updatestatus"}
    assert {e["source"] for e in edges} == {"ui"}
    assert all(e["relation"] == ex.HTTP_ROUTE_RELATION for e in edges)
    assert all(e["confidence"] == 1.0 for e in edges)
    assert "ctl" not in targets, "the decorator resolved to the class, not the handler"


def test_build_db_model_edges_targets_the_model_node():
    known = {m["name"].lower(): m["name"] for m in ex.extract_prisma_models(SCHEMA_PRISMA)}
    calls = ex.extract_prisma_calls("b/applications.service.ts", SERVICE_TS, known)
    index = ex.NodeIndex([
        {"id": "svc", "label": "ApplicationsService", "file": "b/applications.service.ts",
         "source_location": "L1"},
    ])

    edges = ex.build_db_model_edges(calls, index)
    assert {e["target"] for e in edges} == {
        "prisma_model_user", "prisma_model_office", "prisma_model_session",
    }
    assert all(e["relation"] == ex.DB_MODEL_RELATION and e["confidence"] == 1.0 for e in edges)


def test_unattributable_call_site_is_skipped_not_raised():
    calls = [{"file": "not/indexed.ts", "line": 3, "model": "User", "op": "findMany"}]
    assert ex.build_db_model_edges(calls, ex.NodeIndex([])) == []


# --------------------------------------------------------------------------- #
# Classifier
# --------------------------------------------------------------------------- #

def test_label_containing_model_no_longer_forces_layer_4():
    """``ModelInputFile`` is an LLM contract type, not a database table."""
    layer = classify_node("backend_src_core_model_llm_contract_modelinputfile", {
        "label": "ModelInputFile",
        "source_file": "backend/src/core/model/llm.contract.ts",
    })
    assert layer != LayerType.LAYER_4_DATA


def test_synthesized_db_model_node_classifies_into_layer_4():
    layer = classify_node("prisma_model_office", {
        "label": "Office",
        "source_file": "backend/prisma/schema.prisma",
        "type": "db_model",
    })
    assert layer == LayerType.LAYER_4_DATA


def test_a_real_repository_still_classifies_into_layer_4():
    layer = classify_node("backend_src_cases_case_repository", {
        "label": "CaseRepository",
        "source_file": "backend/src/cases/case.repository.ts",
    })
    assert layer == LayerType.LAYER_4_DATA


# --------------------------------------------------------------------------- #
# Dead code cascade
# --------------------------------------------------------------------------- #

def _graph_with(*nodes, edges=()):
    graph = nx.DiGraph()
    for node_id, attrs in nodes:
        graph.add_node(node_id, **attrs)
    for src, tgt, relation in edges:
        graph.add_edge(src, tgt, relation=relation, confidence=1.0)
    return graph


@pytest.mark.parametrize("path,fragment", [
    ("frontend/src/app/cases/page.tsx", "Next.js"),
    ("frontend/src/app/layout.tsx", "Next.js"),
    ("frontend/src/app/api/x/route.ts", "Next.js"),
    ("frontend/src/middleware.ts", "Next.js"),
    ("frontend/src/app/error.tsx", "Next.js"),
    ("frontend/src/app/loading.tsx", "Next.js"),
    ("frontend/src/app/not-found.tsx", "Next.js"),
    ("backend/src/main.ts", "bootstrap"),
    ("backend/src/cases/cases.module.ts", "module"),
    ("backend/src/cases/cases.controller.ts", "controller"),
    ("backend/prisma/migrations/0001_init/migration.sql", "migration"),
    ("backend/prisma/seed.ts", "seed"),
    ("frontend/next.config.js", "configuration"),
    ("frontend/postcss.config.js", "configuration"),
    ("frontend/eslint.config.mjs", "configuration"),
    ("backend/prisma.config.ts", "configuration"),
    ("backend/package.json", "package manifest"),
    (".github/workflows/ci.yml", "CI"),
    (".githooks/pre-commit", "git hook"),
    ("backend/Dockerfile", "container"),
    ("docker/compose.yml", "YAML"),
    ("backend/src/cases/cases.service.spec.ts", "test"),
    ("scripts/check-limits.js", "scripts/"),
    ("scripts/migrate.sh", "scripts/"),
])
def test_known_entry_point_conventions(path, fragment):
    reason = entry_point_reason(path)
    assert reason and fragment.lower() in reason.lower(), f"{path} -> {reason!r}"


def test_ordinary_source_file_is_not_an_entry_point():
    assert entry_point_reason("frontend/src/components/RcBatchPrintModal.tsx") is None
    assert entry_point_reason("backend/src/cases/case-helpers.ts") is None


def test_node_with_inbound_edges_is_live():
    graph = _graph_with(
        ("a", {"file": "src/a.ts", "layer": "Layer 3: Domain Service"}),
        ("b", {"file": "src/b.ts", "layer": "Layer 3: Domain Service"}),
        edges=[("a", "b", "calls")],
    )
    classify_dead_code(graph, 1.0)

    assert graph.nodes["b"]["dead_code_status"] == STATUS_LIVE
    assert "calls" in graph.nodes["b"]["dead_code_reason"]


def test_entry_point_wins_over_candidate_even_at_full_coverage():
    graph = _graph_with(
        ("page", {"file": "frontend/src/app/cases/page.tsx", "layer": "Layer 1: UI Trigger"}),
    )
    classify_dead_code(graph, 1.0)

    assert graph.nodes["page"]["dead_code_status"] == STATUS_ENTRY_POINT
    assert "Next.js" in graph.nodes["page"]["dead_code_reason"]


def test_re_exported_node_is_an_entry_point():
    graph = _graph_with(
        ("barrel", {"file": "src/index.ts", "layer": "Layer 3: Domain Service"}),
        ("api", {"file": "src/thing.ts", "layer": "Layer 3: Domain Service"}),
    )
    # graphify orients re_exports from the symbol out to the barrel.
    graph.add_edge("api", "barrel", relation="re_exports", confidence=1.0)
    classify_dead_code(graph, 1.0)

    assert graph.nodes["api"]["dead_code_status"] == STATUS_ENTRY_POINT
    assert "public API" in graph.nodes["api"]["dead_code_reason"]


def test_orphan_becomes_a_candidate_only_above_the_coverage_floor():
    def status_at(coverage):
        graph = _graph_with(
            ("orphan", {"file": "frontend/src/components/Unused.tsx", "layer": "Layer 1: UI Trigger"}),
        )
        classify_dead_code(graph, coverage)
        return graph.nodes["orphan"]["dead_code_status"]

    assert status_at(CANDIDATE_COVERAGE_FLOOR) == STATUS_CANDIDATE
    assert status_at(1.0) == STATUS_CANDIDATE
    assert status_at(CANDIDATE_COVERAGE_FLOOR - 0.01) == STATUS_UNREVIEWED
    assert status_at(0.0) == STATUS_UNREVIEWED


def test_silent_llm_step_never_produces_a_candidate():
    """No API key / timeout / parse failure => coverage 0 => unreviewed, never candidate."""
    graph = _graph_with(
        ("orphan", {"file": "frontend/src/components/Unused.tsx", "layer": "Layer 1: UI Trigger"}),
    )
    classify_dead_code(graph, compute_enrichment_coverage(graph))

    assert graph.nodes["orphan"]["dead_code_status"] == STATUS_UNREVIEWED
    assert "not complete enough" in graph.nodes["orphan"]["dead_code_reason"]


def test_reason_is_specific_about_the_evidence():
    graph = _graph_with(
        ("orphan", {"file": "frontend/src/components/Unused.tsx", "layer": "Layer 1: UI Trigger"}),
    )
    classify_dead_code(graph, 0.92)
    reason = graph.nodes["orphan"]["dead_code_reason"]

    assert "no inbound edges" in reason
    assert "not a framework entry point" in reason
    assert "92%" in reason


def test_node_without_a_source_file_is_never_a_candidate():
    graph = _graph_with(("imported_decorator", {"file": "", "layer": "Layer 3: Domain Service"}))
    classify_dead_code(graph, 1.0)
    assert graph.nodes["imported_decorator"]["dead_code_status"] == STATUS_UNREVIEWED


def test_bare_package_pseudo_node_is_never_a_candidate():
    """graphify emits nodes whose source_file is just an npm package name."""
    graph = _graph_with(("next_next", {"file": "next", "layer": "General / Utility"}))
    classify_dead_code(graph, 1.0)
    # A bare package specifier is not reviewable source at all. The invariant this
    # test exists for is that it never reaches "candidate"; it is classified
    # not_code so it does not pad a human review list either.
    assert graph.nodes["next_next"]["dead_code_status"] != STATUS_CANDIDATE
    assert graph.nodes["next_next"]["dead_code_status"] == STATUS_NOT_CODE


def test_coverage_ignores_heuristic_template_intents():
    """
    The offline enricher generates an intent from layer+label alone; it has not
    read the source. Counting it would let boilerplate unlock deletion.
    """
    graph = _graph_with(
        ("a", {"layer": "Layer 3: Domain Service", "intent": "Template text.",
               "enrichment_source": "heuristic"}),
        ("b", {"layer": "Layer 3: Domain Service", "intent": "Really read the code.",
               "enrichment_source": "llm"}),
        ("c", {"layer": "General / Utility", "intent": "", "enrichment_source": ""}),
    )
    # 'c' is excluded from the denominator; only 'b' counts in the numerator.
    assert compute_enrichment_coverage(graph) == pytest.approx(0.5)


def test_coverage_is_zero_when_nothing_was_enriched():
    graph = _graph_with(("a", {"layer": "Layer 3: Domain Service", "intent": ""}))
    assert compute_enrichment_coverage(graph) == 0.0


# --------------------------------------------------------------------------- #
# End-to-end wiring through GraphLoader
# --------------------------------------------------------------------------- #

@pytest.fixture
def seam_loader(seam_repo, monkeypatch):
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")

    graphify = seam_repo / "graphify-out"
    graphify.mkdir(parents=True, exist_ok=True)
    nodes = [
        {"id": "ui_page", "label": "ApplicationsPage()", "file_type": "code",
         "source_file": "frontend/src/app/applications/page.tsx", "source_location": "L3"},
        {"id": "ctl", "label": "ApplicationsController", "file_type": "code",
         "source_file": "backend/src/applications/applications.controller.ts", "source_location": "L4"},
        {"id": "ctl_findall", "label": ".findAll()", "file_type": "code",
         "source_file": "backend/src/applications/applications.controller.ts", "source_location": "L8"},
        {"id": "ctl_findone", "label": ".findOne()", "file_type": "code",
         "source_file": "backend/src/applications/applications.controller.ts", "source_location": "L14"},
        {"id": "ctl_update", "label": ".updateStatus()", "file_type": "code",
         "source_file": "backend/src/applications/applications.controller.ts", "source_location": "L19"},
        {"id": "svc", "label": "ApplicationsService", "file_type": "code",
         "source_file": "backend/src/applications/applications.service.ts", "source_location": "L1"},
    ]
    graph_doc = {"directed": True, "multigraph": False, "graph": {}, "nodes": nodes,
                 "links": [{"source": "ctl", "target": "svc", "relation": "calls",
                            "confidence_score": 1.0}],
                 "hyperedges": []}
    (graphify / "graph.json").write_text(json.dumps(graph_doc), encoding="utf-8")
    (graphify / "manifest.json").write_text("{}", encoding="utf-8")

    return GraphLoader(str(seam_repo))


def test_prisma_models_become_real_layer_4_nodes(seam_loader, seam_repo):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)

    node = graph.nodes["prisma_model_office"]
    assert node["label"] == "Office"
    assert node["file"] == "backend/prisma/schema.prisma"
    assert node["layer"] == LayerType.LAYER_4_DATA.value
    assert node["type"] == "db_model"
    assert node["source_location"] == "L5"
    assert node["community"] is None
    assert node["degree"] == 0
    assert node["intent"] == ""
    assert node["summary"] == (
        f"{LayerType.LAYER_4_DATA.value}: Office located at backend/prisma/schema.prisma"
    )
    assert node["fields"][:2] == ["id", "name"]
    assert seam_loader.prisma_model_count == 4


def test_model_nodes_are_live_dicts_in_the_index_and_layer_bucket(seam_loader):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)
    live = graph.nodes["prisma_model_user"]

    bucket = seam_loader.nodes_by_layer[LayerType.LAYER_4_DATA.value]
    assert any(entry is live for entry in bucket), "nodes_by_layer holds a copy"
    assert any(doc is live for doc in seam_loader.docs_to_index), "docs_to_index holds a copy"


def test_http_route_link_bridges_layer_1_to_layer_2(seam_loader):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)

    bridges = [(u, v) for u, v, d in graph.edges(data=True)
               if d["relation"] == ex.HTTP_ROUTE_RELATION]
    assert bridges, "no HTTP seam was derived"
    assert {v for _, v in bridges} == {"ctl_findall", "ctl_findone", "ctl_update"}
    assert {u for u, _ in bridges} == {"ui_page"}

    for _, target in bridges:
        assert graph.nodes["ui_page"]["layer"] == LayerType.LAYER_1_UI.value
        assert graph.nodes[target]["layer"] == LayerType.LAYER_2_API.value


def test_db_model_link_reaches_the_model_nodes(seam_loader):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)

    links = {(u, v) for u, v, d in graph.edges(data=True)
             if d["relation"] == ex.DB_MODEL_RELATION}
    assert ("svc", "prisma_model_user") in links
    assert ("svc", "prisma_model_office") in links
    assert ("svc", "prisma_model_session") in links, "the tx. transaction client was missed"


def test_deterministic_relations_are_not_bridge_relations():
    """
    Deterministic edges are re-derived from source every scan. Carrying them
    forward from the snapshot would resurrect deleted routes and tables.
    """
    assert ex.HTTP_ROUTE_RELATION not in BRIDGE_RELATIONS
    assert ex.DB_MODEL_RELATION not in BRIDGE_RELATIONS


def test_deterministic_edges_are_rederived_not_carried_forward(seam_repo, seam_loader):
    seam_loader.load_or_extract(enrich_llm=False, rebuild=True)

    # Delete the call site; the seam edge must vanish on the next scan.
    service = seam_repo / "backend/src/applications/applications.service.ts"
    service.write_text("export class ApplicationsService {}\n", encoding="utf-8")

    second = GraphLoader(str(seam_repo))
    graph = second.load_or_extract(enrich_llm=False)

    stale = [(u, v) for u, v, d in graph.edges(data=True)
             if d["relation"] == ex.DB_MODEL_RELATION]
    assert stale == [], f"stale deterministic edges survived a rescan: {stale}"


def test_unqueried_model_is_reported_but_not_as_a_candidate_without_coverage(seam_loader):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)
    node = graph.nodes["prisma_model_nevertouched"]

    assert graph.in_degree("prisma_model_nevertouched") == 0
    assert node["dead_code_status"] == STATUS_UNREVIEWED


def test_scan_persists_dead_code_fields(seam_loader, seam_repo):
    seam_loader.load_or_extract(enrich_llm=False, rebuild=True)
    snapshot = json.loads((seam_repo / ".codechakra/graph.json").read_text(encoding="utf-8"))

    by_id = {n["id"]: n for n in snapshot["nodes"]}
    assert by_id["prisma_model_office"]["dead_code_status"]
    assert by_id["prisma_model_office"]["dead_code_reason"]
    for record in snapshot["nodes"]:
        assert record["dead_code_status"] in {
            STATUS_LIVE, STATUS_ENTRY_POINT, STATUS_CANDIDATE, STATUS_UNREVIEWED,
        }


def test_snapshot_still_holds_every_node_and_edge(seam_loader, seam_repo):
    graph = seam_loader.load_or_extract(enrich_llm=False, rebuild=True)
    snapshot = json.loads((seam_repo / ".codechakra/graph.json").read_text(encoding="utf-8"))

    assert {n["id"] for n in snapshot["nodes"]} == set(graph.nodes)
    assert {(e["source"], e["target"], e["relation"]) for e in snapshot["edges"]} == {
        (u, v, d["relation"]) for u, v, d in graph.edges(data=True)
    }
