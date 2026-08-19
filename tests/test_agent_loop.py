"""
Regression suite for the CodeChakra host-agent loop.

Covers the three pieces that make `queue-enrichment -> agent -> apply-enrichment`
survivable:

* request and response are **separate files** (the request is regenerated every run,
  so an in-place answer would be destroyed);
* the queue **pages** -- running it twice advances instead of repeating;
* the queue is ordered by **real** graph degree, not graphify's absent ``degree`` key.

Plus the installer, the `dead-code` review list, and the read-only guarantee on
`query` / `trace` / `layers`.

Fixtures are local to this module on purpose; nothing here reads the real repository,
the real ``graphify-out/`` or the real ``.codechakra/``, and nothing makes a network call.
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from codechakra import cli as cli_module
from codechakra import installer as installer_module
from codechakra.cli import (
    CURSOR_FILENAME,
    LEGACY_FILENAME,
    REQUEST_FILENAME,
    RESPONSE_FILENAME,
    STATE_DIR,
    cli,
    coerce_enrichment_items,
    compute_degrees,
)


# --------------------------------------------------------------------------- #
# Local fixtures
# --------------------------------------------------------------------------- #

#: label -> (node id, source file, layer-determining path)
LOOP_NODES = {
    # A deliberate spread of degrees so ordering is observable.
    "CasesController": ("be_cases_controller", "backend/src/cases/cases.controller.ts"),
    "CaseWorkflowService": ("be_case_workflow_service", "backend/src/cases/case-workflow.service.ts"),
    "PensionCalculatorService": ("be_pension_calc_service", "backend/src/pension/pension-calculator.service.ts"),
    "PrismaCaseModel": ("be_prisma_case_model", "backend/prisma/schema.prisma"),
    "SubmitCaseButton": ("fe_cases_page_submit", "frontend/src/app/cases/page.tsx"),
    "CaseStatusPollingJob": ("be_case_status_polling", "backend/src/polling/case-status.polling.ts"),
    "LonelyLeaf": ("be_lonely_leaf", "backend/src/misc/lonely.ts"),
}

#: (source label, target label, relation)
LOOP_EDGES = [
    ("SubmitCaseButton", "CasesController", "calls"),
    ("CasesController", "CaseWorkflowService", "calls"),
    ("CaseWorkflowService", "PensionCalculatorService", "calls"),
    ("CaseWorkflowService", "PrismaCaseModel", "references"),
    ("CaseStatusPollingJob", "CaseWorkflowService", "calls"),
    ("CasesController", "PrismaCaseModel", "references"),
]

FILE_BODIES = {
    "backend/src/cases/cases.controller.ts": "@Controller('cases')\nexport class CasesController {}\n",
    "backend/src/cases/case-workflow.service.ts": "export class CaseWorkflowService {}\n",
    "backend/src/pension/pension-calculator.service.ts": "export class PensionCalculatorService {}\n",
    "backend/prisma/schema.prisma": "model PrismaCaseModel {\n  id Int @id\n}\n",
    "frontend/src/app/cases/page.tsx": "export function SubmitCaseButton() { return null; }\n",
    "backend/src/polling/case-status.polling.ts": "export class CaseStatusPollingJob {}\n",
    "backend/src/misc/lonely.ts": "export const lonelyLeaf = 1;\n",
}


@pytest.fixture
def loop_repo(tmp_path) -> Path:
    """A hermetic mini-repo with graphify output, rooted in tmp_path."""
    root = tmp_path / "looprepo"
    (root / "graphify-out").mkdir(parents=True)

    for rel, body in FILE_BODIES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")

    nodes = [
        {
            "id": nid,
            "label": label,
            "file_type": "code",
            "source_file": src,
            "source_location": "L1",
            "norm_label": label.lower(),
        }
        for label, (nid, src) in LOOP_NODES.items()
    ]
    links = [
        {
            "relation": relation,
            "confidence_score": 1.0,
            "source": LOOP_NODES[src][0],
            "target": LOOP_NODES[tgt][0],
        }
        for src, tgt, relation in LOOP_EDGES
    ]
    (root / "graphify-out" / "graph.json").write_text(
        json.dumps({"directed": True, "nodes": nodes, "links": links}, indent=2),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def run(loop_repo):
    """Invoke a CodeChakra command against the mini-repo and assert it succeeded."""
    runner = CliRunner()

    def _run(*args, expect_ok=True):
        result = runner.invoke(cli, list(args) + ["--path", str(loop_repo)])
        if expect_ok and result.exit_code != 0:
            raise AssertionError(
                f"`codechakra {' '.join(args)}` failed ({result.exit_code}):\n"
                f"{result.output}\n{result.exception!r}"
            )
        return result

    return _run


@pytest.fixture
def state(loop_repo):
    """Reader for files under the mini-repo's .codechakra/ directory."""

    def _state(filename):
        path = loop_repo / STATE_DIR / filename
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    return _state


@pytest.fixture
def no_enrichment(monkeypatch):
    """Any call into the LLM enricher during the test is a hard failure."""
    from codechakra.llm_enricher import LLMEnricher

    def _boom(self, nodes_batch):  # pragma: no cover - only fires on regression
        raise AssertionError("a read-only command triggered LLM enrichment")

    monkeypatch.setattr(LLMEnricher, "enrich_batch", _boom)
    return True


def _write_response(root: Path, items, filename=RESPONSE_FILENAME) -> Path:
    path = root / STATE_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return path


def _snapshot(root: Path) -> dict:
    return json.loads((root / STATE_DIR / "graph.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Request / response separation
# --------------------------------------------------------------------------- #

def test_queue_writes_request_not_the_legacy_pending_file(run, loop_repo, state):
    run("queue-enrichment", "--limit", "3")

    request = state(REQUEST_FILENAME)
    assert request is not None, "queue-enrichment must write the request file"
    assert request["schema"] == "codechakra/enrichment-request@1"
    assert len(request["nodes"]) == 3
    assert request["response_file"] == os.path.join(STATE_DIR, RESPONSE_FILENAME)
    assert not (loop_repo / STATE_DIR / RESPONSE_FILENAME).exists()


def test_request_is_regenerated_so_the_response_must_be_a_separate_file(run, state):
    run("queue-enrichment", "--limit", "2")
    first = state(REQUEST_FILENAME)
    run("queue-enrichment", "--limit", "2")
    second = state(REQUEST_FILENAME)

    first_ids = {n["id"] for n in first["nodes"]}
    second_ids = {n["id"] for n in second["nodes"]}
    assert first_ids and second_ids
    assert first_ids.isdisjoint(second_ids), "an in-place answer in the request would be lost"


def test_apply_refuses_to_treat_the_request_as_a_response(run, loop_repo):
    run("queue-enrichment", "--limit", "2")
    request_path = str(loop_repo / STATE_DIR / REQUEST_FILENAME)

    result = run("apply-enrichment", request_path, expect_ok=False)
    assert result.exit_code != 0
    assert "request, not the response" in result.output


def test_apply_reads_the_response_and_leaves_the_request_untouched(run, loop_repo, state):
    run("queue-enrichment", "--limit", "3")
    request_before = json.dumps(state(REQUEST_FILENAME), sort_keys=True)

    target = state(REQUEST_FILENAME)["nodes"][0]["id"]
    _write_response(loop_repo, [{"id": target, "intent": "Handles case submission.",
                                 "fields": ["caseId"], "calls": []}])

    run("apply-enrichment")
    assert json.dumps(state(REQUEST_FILENAME), sort_keys=True) == request_before

    node = next(n for n in _snapshot(loop_repo)["nodes"] if n["id"] == target)
    assert node["intent"] == "Handles case submission."
    assert node["fields"] == ["caseId"]


def test_legacy_pending_enrichment_file_is_still_honoured(run, loop_repo):
    """The user's hand-written pending_enrichment.json must never be stranded."""
    run("queue-enrichment", "--limit", "7")
    _write_response(
        loop_repo,
        [{"id": LOOP_NODES["CasesController"][0], "intent": "Legacy hand-written intent.",
          "fields": ["caseId"], "calls": []}],
        filename=LEGACY_FILENAME,
    )

    result = run("apply-enrichment")
    assert "legacy" in result.output.lower()

    node = next(n for n in _snapshot(loop_repo)["nodes"]
                if n["id"] == LOOP_NODES["CasesController"][0])
    assert node["intent"] == "Legacy hand-written intent."


def test_response_file_wins_over_the_legacy_file(run, loop_repo):
    run("queue-enrichment", "--limit", "7")
    nid = LOOP_NODES["CasesController"][0]
    _write_response(loop_repo, [{"id": nid, "intent": "From legacy."}], filename=LEGACY_FILENAME)
    _write_response(loop_repo, [{"id": nid, "intent": "From response."}])

    run("apply-enrichment")
    node = next(n for n in _snapshot(loop_repo)["nodes"] if n["id"] == nid)
    assert node["intent"] == "From response."


def test_apply_without_any_response_file_fails_with_guidance(run):
    result = run("apply-enrichment", expect_ok=False)
    assert result.exit_code != 0
    assert RESPONSE_FILENAME in result.output
    assert "queue-enrichment" in result.output


@pytest.mark.parametrize("payload,expected", [
    ([{"id": "a"}], 1),
    ({"enrichments": [{"id": "a"}, {"id": "b"}]}, 2),
    ({"nodes": [{"id": "a"}]}, 1),
    ({"results": [{"id": "a"}]}, 1),
    ({"unrelated": 3}, 0),
    ("nonsense", 0),
])
def test_coerce_enrichment_items_accepts_array_and_wrappers(payload, expected):
    assert len(coerce_enrichment_items(payload)) == expected


# --------------------------------------------------------------------------- #
# Paging
# --------------------------------------------------------------------------- #

def test_queue_twice_advances_through_the_backlog(run, state):
    run("queue-enrichment", "--limit", "2")
    batch_one = {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}
    run("queue-enrichment", "--limit", "2")
    batch_two = {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}

    assert len(batch_one) == len(batch_two) == 2
    assert batch_one.isdisjoint(batch_two)
    assert set(state(CURSOR_FILENAME)["queued"]) == batch_one | batch_two


def test_progress_counts_shrink_as_the_queue_advances(run, state):
    run("queue-enrichment", "--limit", "2")
    first = state(REQUEST_FILENAME)["progress"]
    run("queue-enrichment", "--limit", "2")
    second = state(REQUEST_FILENAME)["progress"]

    assert first["remaining_after"] == first["total_candidates"] - 2
    assert second["remaining_after"] == first["remaining_after"] - 2


def test_reset_rewinds_the_queue(run, state):
    run("queue-enrichment", "--limit", "2")
    first = {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}
    run("queue-enrichment", "--limit", "2", "--reset")
    assert {n["id"] for n in state(REQUEST_FILENAME)["nodes"]} == first


def test_requeue_hands_out_abandoned_batches_again(run, state):
    run("queue-enrichment", "--limit", "2")
    first = {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}
    run("queue-enrichment", "--limit", "2", "--requeue")
    assert {n["id"] for n in state(REQUEST_FILENAME)["nodes"]} == first


def test_limit_zero_queues_everything_remaining(run, state):
    run("queue-enrichment", "--limit", "0")
    request = state(REQUEST_FILENAME)
    assert request["progress"]["remaining_after"] == 0
    assert len(request["nodes"]) == request["progress"]["total_candidates"]


def test_applied_nodes_are_never_queued_again(run, loop_repo, state):
    run("queue-enrichment", "--limit", "2")
    ids = [n["id"] for n in state(REQUEST_FILENAME)["nodes"]]
    _write_response(loop_repo, [{"id": i, "intent": f"Intent for {i}."} for i in ids])
    run("apply-enrichment")

    assert set(state(CURSOR_FILENAME)["applied"]) == set(ids)
    run("queue-enrichment", "--limit", "0", "--requeue", "--reset")
    assert {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}.isdisjoint(ids)


def test_ids_answered_but_not_yet_applied_are_not_requeued(run, loop_repo, state):
    run("queue-enrichment", "--limit", "2")
    ids = [n["id"] for n in state(REQUEST_FILENAME)["nodes"]]
    _write_response(loop_repo, [{"id": i, "intent": f"Intent for {i}."} for i in ids])

    run("queue-enrichment", "--limit", "0", "--reset")
    assert {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}.isdisjoint(ids)


# --------------------------------------------------------------------------- #
# Priority ordering
# --------------------------------------------------------------------------- #

def test_compute_degrees_uses_in_plus_out_and_counts_layer_crossings():
    import networkx as nx

    graph = nx.DiGraph()
    graph.add_node("a", layer="Layer 1: UI Trigger")
    graph.add_node("b", layer="Layer 2: API Gateway")
    graph.add_node("c", layer="Layer 2: API Gateway")
    graph.add_edge("a", "b")
    graph.add_edge("c", "b")

    degrees = compute_degrees(graph)
    assert degrees["b"] == (2, 1)   # two edges, one of which crosses a layer
    assert degrees["a"] == (1, 1)
    assert degrees["c"] == (1, 0)


def test_queue_orders_hubs_and_seams_before_leaves(run, state):
    run("queue-enrichment", "--limit", "0")
    nodes = state(REQUEST_FILENAME)["nodes"]

    by_id = {n["id"]: n for n in nodes}
    ranks = {n["id"]: n["rank"] for n in nodes}

    workflow = LOOP_NODES["CaseWorkflowService"][0]   # hub: highest degree
    lonely = LOOP_NODES["LonelyLeaf"][0]              # leaf: degree 0
    assert by_id[workflow]["degree"] > by_id[lonely]["degree"] == 0
    assert ranks[workflow] < ranks[lonely]

    # Every connected node outranks the disconnected leaf.
    for node in nodes:
        if node["degree"] > 0:
            assert node["rank"] < ranks[lonely], node["id"]

    keys = [(-n["cross_layer_degree"], -n["degree"], n["id"]) for n in nodes]
    assert keys == sorted(keys), "queue must be sorted by seam, then degree, then id"


def test_degree_is_recomputed_not_read_from_graphify(run, loop_repo, state):
    """graphify emits no `degree` key, so the raw value is 0 for every node."""
    raw = json.loads((loop_repo / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    assert all("degree" not in n for n in raw["nodes"])

    run("queue-enrichment", "--limit", "0")
    assert any(n["degree"] > 0 for n in state(REQUEST_FILENAME)["nodes"])

    persisted = {n["id"]: n.get("degree", 0) for n in _snapshot(loop_repo)["nodes"]}
    assert persisted[LOOP_NODES["CaseWorkflowService"][0]] == 4


def test_utility_layer_nodes_are_not_queued(run, state):
    run("queue-enrichment", "--limit", "0")
    for node in state(REQUEST_FILENAME)["nodes"]:
        assert node["layer"] != "General / Utility"


# --------------------------------------------------------------------------- #
# Bridges land in the snapshot
# --------------------------------------------------------------------------- #

def test_calls_become_cross_layer_bridge_edges(run, loop_repo):
    run("queue-enrichment", "--limit", "0")
    src = LOOP_NODES["SubmitCaseButton"][0]
    _write_response(loop_repo, [{
        "id": src,
        "intent": "Submits a new pension case from the cases page.",
        "fields": ["caseId"],
        "calls": ["CaseStatusPollingJob"],
    }])
    result = run("apply-enrichment")
    assert "bridge edge" in result.output

    edges = _snapshot(loop_repo)["edges"]
    bridges = [e for e in edges
               if e["source"] == src and e["relation"] == "cross_layer_link"]
    assert bridges, "an exact symbol name must resolve into a bridge edge"
    assert bridges[0]["target"] == LOOP_NODES["CaseStatusPollingJob"][0]
    assert bridges[0]["confidence"] >= 0.35


def test_vague_call_targets_are_reported_not_silently_linked(run, loop_repo):
    run("queue-enrichment", "--limit", "0")
    src = LOOP_NODES["SubmitCaseButton"][0]
    _write_response(loop_repo, [{
        "id": src,
        "intent": "Submits a new pension case.",
        "calls": ["zzzz nonexistent thing"],
    }])
    result = run("apply-enrichment")
    assert "below the score floor" in result.output


def test_unknown_ids_are_skipped_and_reported(run, loop_repo):
    run("queue-enrichment", "--limit", "0")
    _write_response(loop_repo, [
        {"id": "not_a_real_node", "intent": "nope"},
        {"id": LOOP_NODES["CasesController"][0], "intent": "Real one."},
    ])
    result = run("apply-enrichment")
    assert "not in the graph" in result.output
    assert "Applied 1 enrichment" in result.output


# --------------------------------------------------------------------------- #
# Read commands stay read-only
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("args", [
    ("layers",),
    ("query", "pension case"),
    ("trace", "CasesController", "PrismaCaseModel"),
    ("dead-code",),
])
def test_read_commands_never_trigger_enrichment(run, no_enrichment, args):
    result = run(*args)
    assert result.exit_code == 0


def test_queue_and_apply_also_avoid_enrichment(run, loop_repo, no_enrichment):
    run("queue-enrichment", "--limit", "1")
    _write_response(loop_repo, [{"id": LOOP_NODES["CasesController"][0], "intent": "x."}])
    run("apply-enrichment")


# --------------------------------------------------------------------------- #
# dead-code
# --------------------------------------------------------------------------- #

def _seed_dead_code_statuses(root: Path):
    snapshot_path = root / STATE_DIR / "graph.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assignments = {
        LOOP_NODES["LonelyLeaf"][0]: ("candidate", "no inbound edges after bridge cascade"),
        LOOP_NODES["SubmitCaseButton"][0]: ("entry_point", "Next.js page component"),
        LOOP_NODES["CasesController"][0]: ("live", "referenced by SubmitCaseButton"),
        LOOP_NODES["PrismaCaseModel"][0]: ("unreviewed", "enrichment coverage below floor"),
    }
    for node in snapshot["nodes"]:
        status, reason = assignments.get(node["id"], ("", ""))
        node["dead_code_status"] = status
        node["dead_code_reason"] = reason
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def _snapshot_without_dead_code_attrs(root: Path):
    """A snapshot from a build that predates the reachability pass."""
    path = root / STATE_DIR / "graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "nodes": [{"id": nid, "label": label, "file": src, "layer": "Layer 3: Domain Service"}
                  for label, (nid, src) in LOOP_NODES.items()],
        "edges": [],
    }, indent=2), encoding="utf-8")


def test_dead_code_degrades_gracefully_without_the_attributes(run, loop_repo):
    _snapshot_without_dead_code_attrs(loop_repo)
    result = run("dead-code")
    assert "No reachability review data yet" in result.output
    assert result.exit_code == 0


def test_dead_code_json_degrades_gracefully_too(run, loop_repo):
    _snapshot_without_dead_code_attrs(loop_repo)
    result = run("dead-code", "--json")
    payload = json.loads(result.output)
    assert payload["available"] is False
    assert payload["nodes"] == []
    assert payload["count"] == 0


def test_dead_code_defaults_to_candidates(run, loop_repo):
    run("queue-enrichment", "--limit", "1")
    _seed_dead_code_statuses(loop_repo)

    result = run("dead-code")
    assert "LonelyLeaf" in result.output
    assert "no inbound edges after bridge cascade" in result.output
    assert "SubmitCaseButton" not in result.output
    assert "backend/src/misc/lonely.ts" in result.output


def test_dead_code_output_frames_results_as_review_candidates(run, loop_repo):
    run("queue-enrichment", "--limit", "1")
    _seed_dead_code_statuses(loop_repo)

    result = run("dead-code")
    lowered = result.output.lower()
    assert "review candidates, not confirmed dead code" in lowered
    assert "evidence, not proof" in lowered
    assert "reflection" in lowered
    assert "safe to delete" not in lowered


def test_dead_code_unreviewed_is_never_presented_as_removable(run, loop_repo):
    run("queue-enrichment", "--limit", "1")
    _seed_dead_code_statuses(loop_repo)

    result = run("dead-code", "--status", "unreviewed")
    assert "PrismaCaseModel" in result.output
    assert "NOT ENOUGH EVIDENCE" in result.output
    assert "not removable" in result.output.lower()


def test_dead_code_status_filter_and_all(run, loop_repo):
    run("queue-enrichment", "--limit", "1")
    _seed_dead_code_statuses(loop_repo)

    assert "CasesController" in run("dead-code", "--status", "live").output
    all_out = run("dead-code", "--status", "all").output
    for label in ("LonelyLeaf", "SubmitCaseButton", "CasesController", "PrismaCaseModel"):
        assert label in all_out


def test_dead_code_json_mode_is_machine_readable(run, loop_repo):
    run("queue-enrichment", "--limit", "1")
    _seed_dead_code_statuses(loop_repo)

    payload = json.loads(run("dead-code", "--json").output)
    assert payload["available"] is True
    assert payload["count"] == 1
    row = payload["nodes"][0]
    assert row["label"] == "LonelyLeaf"
    assert row["status"] == "candidate"
    assert row["reason"]
    assert row["layer"] and row["file"]
    assert "not confirmed dead code" in payload["disclaimer"].lower()


def test_dead_code_has_no_delete_capability(run):
    help_text = run("dead-code", "--help").output.lower()
    assert "delete" in help_text  # only as a disclaimer
    assert "--delete" not in help_text
    assert "--remove" not in help_text
    assert "--fix" not in help_text


# --------------------------------------------------------------------------- #
# Installer
# --------------------------------------------------------------------------- #

def test_install_writes_claude_cursor_and_antigravity(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))

    expected = {
        "contract": ".codechakra/AGENT_CONTRACT.md",
        "claude_skill": ".claude/skills/codechakra/SKILL.md",
        "claude_md": "CLAUDE.md",
        "cursor_rule": ".cursor/rules/codechakra.mdc",
        "antigravity_rule": ".agents/rules/codechakra.md",
        "antigravity_workflow": ".agents/workflows/codechakra.md",
    }
    assert set(written) == set(expected)
    for key, rel in expected.items():
        assert Path(written[key]).is_file()
        assert Path(written[key]) == tmp_path / rel


def test_every_rule_file_points_at_the_contract_and_the_loop(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))
    for key, path in written.items():
        if key == "contract":
            continue
        text = Path(path).read_text(encoding="utf-8")
        assert "AGENT_CONTRACT.md" in text, key
        assert "queue-enrichment" in text, key
        assert "apply-enrichment" in text, key


def test_rules_tell_the_agent_to_read_the_source_and_not_invent(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))
    for key in ("claude_skill", "claude_md", "cursor_rule", "antigravity_rule", "contract"):
        text = Path(written[key]).read_text(encoding="utf-8").lower()
        assert "read the source" in text or "read the actual source" in text, key
        assert "invent" in text, key
        assert "0.35" in text, key


def test_install_is_idempotent(tmp_path):
    first = installer_module.install_agent_rules(str(tmp_path))
    contents = {k: Path(v).read_text(encoding="utf-8") for k, v in first.items()}
    mtimes = {k: os.path.getmtime(v) for k, v in first.items()}

    second = installer_module.install_agent_rules(str(tmp_path))
    assert second == first
    for key, path in second.items():
        assert Path(path).read_text(encoding="utf-8") == contents[key]
        assert os.path.getmtime(path) == mtimes[key], f"{key} was rewritten unnecessarily"


def test_existing_claude_md_is_never_clobbered(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My project\n\nHand-written house rules.\n", encoding="utf-8")

    installer_module.install_agent_rules(str(tmp_path))
    text = claude_md.read_text(encoding="utf-8")
    assert "Hand-written house rules." in text
    assert installer_module.CLAUDE_MD_BEGIN in text
    assert installer_module.CLAUDE_MD_END in text


def test_claude_md_section_is_replaced_in_place_not_appended(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Mine\n\nkeep me\n", encoding="utf-8")

    installer_module.install_agent_rules(str(tmp_path))
    installer_module.install_agent_rules(str(tmp_path))
    text = claude_md.read_text(encoding="utf-8")

    assert text.count(installer_module.CLAUDE_MD_BEGIN) == 1
    assert text.count(installer_module.CLAUDE_MD_END) == 1
    assert text.count("keep me") == 1


def test_upsert_preserves_text_after_the_managed_section():
    begin, end = installer_module.CLAUDE_MD_BEGIN, installer_module.CLAUDE_MD_END
    existing = f"head\n\n{begin}\nold body\n{end}\n\ntail\n"
    updated = installer_module.upsert_delimited_section(existing, "new body")

    assert "head" in updated and "tail" in updated
    assert "old body" not in updated
    assert "new body" in updated


def test_installed_contract_matches_the_shipped_document(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))
    installed = Path(written["contract"]).read_text(encoding="utf-8")

    shipped = Path(installer_module._CONTRACT_CANDIDATES[0])
    if not shipped.is_file():  # pragma: no cover - non-editable install
        pytest.skip("AGENT_CONTRACT.md not shipped alongside the package")
    assert installed == shipped.read_text(encoding="utf-8")


def test_contract_documents_the_full_response_schema(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))
    text = Path(written["contract"]).read_text(encoding="utf-8")
    for token in ("\"id\"", "\"intent\"", "\"fields\"", "\"calls\"",
                  "enrichment_request.json", "enrichment_response.json",
                  "pending_enrichment.json", "0.35", "cross_layer_degree"):
        assert token in text, token


def test_install_command_reports_the_gitignore_problem(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules\n.agents\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["install", "--path", str(tmp_path)])

    assert result.exit_code == 0
    assert ".gitignore:2" in result.output
    assert ".agents" in result.output


def test_gitignore_warnings_are_empty_when_nothing_is_hidden(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules\n*.log\n", encoding="utf-8")
    assert installer_module.gitignore_warnings(str(tmp_path)) == []


def test_install_command_lists_what_it_wrote(tmp_path):
    result = CliRunner().invoke(cli, ["install", "--path", str(tmp_path)])
    assert result.exit_code == 0
    for key in ("contract", "claude_skill", "claude_md", "cursor_rule",
                "antigravity_rule", "antigravity_workflow"):
        assert key in result.output


# --------------------------------------------------------------------------- #
# Command surface preserved
# --------------------------------------------------------------------------- #

def test_existing_command_names_are_preserved():
    names = set(cli.commands)
    assert {"scan", "query", "trace", "layers", "install",
            "queue-enrichment", "apply-enrichment", "dead-code"} <= names


def test_scan_still_accepts_rebuild(loop_repo):
    result = CliRunner().invoke(cli, ["scan", str(loop_repo), "--rebuild"])
    assert result.exit_code == 0, result.output
    assert "Scan complete" in result.output


def test_query_still_accepts_top_k_and_path(loop_repo):
    result = CliRunner().invoke(
        cli, ["query", "pension calculation", "--top-k", "2", "--path", str(loop_repo)]
    )
    assert result.exit_code == 0, result.output


def test_state_filenames_are_the_documented_ones():
    assert REQUEST_FILENAME == "enrichment_request.json"
    assert RESPONSE_FILENAME == "enrichment_response.json"
    assert LEGACY_FILENAME == "pending_enrichment.json"
    assert cli_module.STATE_DIR == ".codechakra"


# --------------------------------------------------------------------------- #
# Enrichment provenance
# --------------------------------------------------------------------------- #

def test_heuristic_intents_still_count_as_candidates():
    """The offline template enricher never read the source, so its output is not done."""
    assert cli_module.needs_agent_enrichment(
        {"layer": "Layer 2: API Gateway", "intent": "", "enrichment_source": ""})
    assert cli_module.needs_agent_enrichment(
        {"layer": "Layer 2: API Gateway", "intent": "Template text.",
         "enrichment_source": cli_module.HEURISTIC_ENRICHMENT_SOURCE})
    assert not cli_module.needs_agent_enrichment(
        {"layer": "Layer 2: API Gateway", "intent": "Real text.", "enrichment_source": "agent"})
    assert not cli_module.needs_agent_enrichment(
        {"layer": "General / Utility", "intent": "", "enrichment_source": ""})


def test_apply_stamps_agent_provenance(run, loop_repo, state):
    run("queue-enrichment", "--limit", "1")
    nid = state(REQUEST_FILENAME)["nodes"][0]["id"]
    _write_response(loop_repo, [{"id": nid, "intent": "Read from the source file."}])
    run("apply-enrichment")

    node = next(n for n in _snapshot(loop_repo)["nodes"] if n["id"] == nid)
    assert node["enrichment_source"] == cli_module.AGENT_ENRICHMENT_SOURCE


def test_heuristic_node_is_requeued_but_agent_node_is_not(run, loop_repo, state):
    run("queue-enrichment", "--limit", "1")
    nid = state(REQUEST_FILENAME)["nodes"][0]["id"]
    _write_response(loop_repo, [{"id": nid, "intent": "Read from the source file."}])
    run("apply-enrichment")

    # Downgrade the provenance to heuristic in the snapshot; it must come back.
    snapshot_path = loop_repo / STATE_DIR / "graph.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for node in snapshot["nodes"]:
        if node["id"] == nid:
            node["enrichment_source"] = cli_module.HEURISTIC_ENRICHMENT_SOURCE
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    (loop_repo / STATE_DIR / RESPONSE_FILENAME).unlink()

    run("queue-enrichment", "--limit", "0", "--reset")
    assert nid in {n["id"] for n in state(REQUEST_FILENAME)["nodes"]}
