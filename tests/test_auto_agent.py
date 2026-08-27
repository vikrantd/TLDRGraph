"""
Automatic agent path: layer design, in-scan enrichment, and the managed .gitignore.

Nothing here spawns a real coding agent. Everything above the subprocess boundary
is driven by a fake AgentCLI; the boundary itself is exercised once against
``/bin/echo`` so the argv/parse contract is not just asserted in a mock.
"""

from __future__ import annotations

import json

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tldrgraph import agent_commands, agent_runner, cli_pipeline, installer as installer_module, paths
from tldrgraph.cli import cli
from tldrgraph.cli_pipeline import resolve_init_embeddings
from tldrgraph.init_policy import (
    APPROVAL_FILENAME,
    enrichment_approval_is_active,
    remember_full_enrichment_approval,
)
from tldrgraph.propose_layers import (
    NEEDS_LAYERS,
    auto_configure_layers,
    propose_layers_with_agent,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

VALID_LAYER_SET = {
    "utility_id": "shared",
    "layers": [
        {"id": "entry", "name": "Layer 1: Entry", "order": 1, "description": "entry points",
         "rules": [{"file_contains": ["cli.py"]}]},
        {"id": "core", "name": "Layer 2: Core", "order": 2, "description": "core logic",
         "rules": [{"file_contains": ["engine"]}]},
        {"id": "shared", "name": "Shared", "order": 3, "description": "catch-all", "rules": []},
    ],
}


def fake_agent(name: str = "fake") -> agent_runner.AgentCLI:
    """An AgentCLI that is never actually executed (run_agent is monkeypatched)."""
    return agent_runner.AgentCLI(
        name=name, binary="/nonexistent/" + name, display=f"Fake {name}",
        build_args=lambda prompt: ["-p", prompt],
    )


@pytest.fixture
def agent_allowed(monkeypatch):
    """
    Undo the suite-wide agent kill switch for tests that need the path live.

    Clearing the switch alone is dangerous: a developer machine usually has a
    real ``claude`` or ``gemini`` on PATH, and an un-stubbed call would spawn it
    for real. So the subprocess boundary is nailed shut here -- a test that
    forgets to stub it fails immediately instead of quietly spending tokens.
    """
    monkeypatch.delenv(agent_runner.ENV_DISABLE, raising=False)
    for marker in agent_runner.NESTED_MARKERS:
        monkeypatch.delenv(marker, raising=False)

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "a test reached the real agent subprocess: stub agent_runner.run_agent"
        )

    monkeypatch.setattr(agent_runner, "run_agent", _forbidden)
    return True


@pytest.fixture
def cli_repo(tmp_path) -> Path:
    """A minimal Python CLI repo that graphify can extract without network access."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\n[project.scripts]\nsample = "sample.cli:main"\n',
        encoding="utf-8",
    )
    pkg = tmp_path / "sample"
    pkg.mkdir()
    (pkg / "cli.py").write_text(
        "from .engine import Engine\n\n\ndef main():\n    return Engine().run()\n",
        encoding="utf-8",
    )
    (pkg / "engine.py").write_text(
        "class Engine:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. Agent discovery
# --------------------------------------------------------------------------- #

def test_agent_is_not_used_when_disabled(monkeypatch):
    monkeypatch.setenv(agent_runner.ENV_DISABLE, "1")
    assert agent_runner.find_agent_cli() is None
    assert agent_runner.agent_status()["reason"] == "disabled"


def test_agent_is_not_spawned_from_inside_another_agent(monkeypatch, agent_allowed):
    """
    A coding agent running `tldrgraph scan` must not cause a second agent to be
    spawned: the host already holds the context, so it should be told what to do.
    """
    monkeypatch.setenv("CLAUDECODE", "1")
    assert agent_runner.nesting_marker() == "CLAUDECODE"
    assert agent_runner.find_agent_cli() is None
    assert agent_runner.agent_status()["reason"] == "nested"


def test_nesting_can_be_opted_into(monkeypatch, agent_allowed, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv(agent_runner.ENV_ALLOW_NESTED, "1")
    monkeypatch.setenv(agent_runner.ENV_FORCE_CLI, "/bin/echo")
    assert agent_runner.find_agent_cli() is not None


def test_missing_binary_reports_not_found(monkeypatch, agent_allowed):
    monkeypatch.setattr(agent_runner.shutil, "which", lambda _binary: None)
    status = agent_runner.agent_status()
    assert status["reason"] == "not_found"
    assert status["agent"] is None


# --------------------------------------------------------------------------- #
# 2. Output parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure, here you go:\n```\n[{"id": "x"}]\n```\nHope that helps!', [{"id": "x"}]),
    ('Here is the result: [{"id": "x"}]', [{"id": "x"}]),
])
def test_json_is_recovered_from_however_the_agent_wrapped_it(text, expected):
    assert agent_runner.extract_json(text) == expected


def test_unparseable_output_raises_rather_than_returning_junk():
    with pytest.raises(agent_runner.AgentError):
        agent_runner.extract_json("I could not complete that request.")
    with pytest.raises(agent_runner.AgentError):
        agent_runner.extract_json("")


def test_run_agent_crosses_the_real_subprocess_boundary(tmp_path):
    """The one test that actually forks: argv construction and stdout capture."""
    echo = agent_runner.AgentCLI(
        name="echo", binary="/bin/echo", display="echo",
        build_args=lambda prompt: [prompt],
    )
    assert agent_runner.run_agent_json(echo, '{"ok": true}', str(tmp_path)) == {"ok": True}


def test_nonzero_exit_becomes_an_agent_error(tmp_path):
    failing = agent_runner.AgentCLI(
        name="false", binary="/usr/bin/false", display="false",
        build_args=lambda prompt: [],
    )
    with pytest.raises(agent_runner.AgentError):
        agent_runner.run_agent(failing, "anything", str(tmp_path))


def test_claude_error_envelope_is_surfaced():
    envelope = json.dumps({"is_error": True, "result": "rate limited"})
    with pytest.raises(agent_runner.AgentError, match="rate limited"):
        agent_runner._claude_parse(envelope)


def test_claude_reports_failure_with_exit_code_zero():
    """
    Verified against real `claude -p --output-format json` output: an expired
    OAuth token comes back as ``is_error: true`` on a SUCCESSFUL exit status.
    Trusting the exit code alone would write the error message into the graph
    as if it were an intent.
    """
    real_envelope = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "duration_ms": 1857,
        "num_turns": 1,
        "result": "Failed to authenticate. API Error: 401 OAuth access token has expired.",
        "session_id": "c2a7e5f8-a303-44da-9243-0c3cc3f4d329",
    })
    with pytest.raises(agent_runner.AgentError, match="Failed to authenticate"):
        agent_runner._claude_parse(real_envelope)


def test_claude_success_envelope_is_unwrapped():
    envelope = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": '{"utility_id": "shared", "layers": []}',
    })
    assert agent_runner.extract_json(agent_runner._claude_parse(envelope)) == {
        "utility_id": "shared", "layers": [],
    }


# --------------------------------------------------------------------------- #
# 3. Layer design by agent
# --------------------------------------------------------------------------- #

def test_agent_layer_proposal_is_validated_and_returned(monkeypatch, cli_repo):
    monkeypatch.setattr(agent_runner, "run_agent", lambda *a, **k: json.dumps(VALID_LAYER_SET))
    proposal, detail = propose_layers_with_agent(str(cli_repo), agent=fake_agent())
    assert detail == "fake"
    assert [layer["id"] for layer in proposal["layers"]] == ["entry", "core", "shared"]


def test_invalid_agent_layer_proposal_is_rejected_not_written(monkeypatch, cli_repo):
    """A layer set naming a utility_id that does not exist must never be saved."""
    broken = {"utility_id": "nope", "layers": VALID_LAYER_SET["layers"]}
    monkeypatch.setattr(agent_runner, "run_agent", lambda *a, **k: json.dumps(broken))
    proposal, detail = propose_layers_with_agent(str(cli_repo), agent=fake_agent())
    assert proposal is None
    assert "invalid layer set" in detail


def test_agent_prompt_tells_the_agent_to_read_files(cli_repo):
    from tldrgraph.propose_layers import build_agent_layer_prompt, collect_layer_evidence

    prompt = build_agent_layer_prompt(str(cli_repo), collect_layer_evidence(str(cli_repo)))
    assert "Read the entry points" in prompt
    assert "NOT a substitute for opening the files" in prompt
    assert str(cli_repo) in prompt


def test_auto_configure_prefers_the_agent_over_the_archetype(monkeypatch, cli_repo):
    monkeypatch.setattr(agent_runner, "run_agent", lambda *a, **k: json.dumps(VALID_LAYER_SET))
    reg, cfg_path, source = auto_configure_layers(str(cli_repo), agent=fake_agent(), use_llm=False, use_agent=True)

    assert source == "agent:fake"
    assert reg.ids() == ("entry", "core", "shared")


def test_no_agent_means_no_layers_and_no_config_file(cli_repo):
    """The archetype fallback is gone: nothing writes layers it did not derive."""
    reg, cfg_path, source = auto_configure_layers(
        str(cli_repo), enricher=None, use_llm=False, use_agent=False
    )
    assert source == NEEDS_LAYERS
    assert reg is None and cfg_path is None
    assert not (cli_repo / ".tldrgraph" / "layers.config.yaml").exists()


def test_an_agent_authored_config_is_never_silently_replaced(monkeypatch, cli_repo):
    monkeypatch.setattr(agent_runner, "run_agent", lambda *a, **k: json.dumps(VALID_LAYER_SET))
    auto_configure_layers(str(cli_repo), agent=fake_agent(), use_llm=False, use_agent=True)

    calls = []

    def _should_not_run(*args, **kwargs):
        calls.append(1)
        return json.dumps(VALID_LAYER_SET)

    monkeypatch.setattr(agent_runner, "run_agent", _should_not_run)
    _, _, source = auto_configure_layers(str(cli_repo), agent=fake_agent(), use_llm=False, use_agent=True)
    assert source == "existing_config"
    assert calls == [], "a settled config must not cost another agent call"


# --------------------------------------------------------------------------- #
# 4. `tldrgraph init` -- one command, resumable
# --------------------------------------------------------------------------- #

def _is_enrichment_prompt(prompt: str) -> bool:
    return "Nodes (" in prompt


def _fake_answer(prompt: str) -> str:
    """
    One fake agent for the whole run: it designs layers when asked for layers,
    and enriches every node id when asked for enrichment.
    """
    if not _is_enrichment_prompt(prompt):
        return json.dumps(VALID_LAYER_SET)

    start = prompt.index("Nodes (")
    nodes = json.loads(prompt[prompt.index("[", start):])
    return json.dumps([
        {
            "id": node["id"],
            "intent": f"Reads and returns the {node['label']} result.",
            "input_fields": ["alpha"],
            "output_fields": ["beta"],
            "calls": [],
        }
        for node in nodes
    ])


def _answer_layers(repo) -> None:
    """Play the agent's part for phase 1, the way the NEXT ACTION block asks."""
    state = repo / ".tldrgraph"
    state.mkdir(exist_ok=True)
    (state / "propose_layers_response.json").write_text(
        json.dumps(VALID_LAYER_SET), encoding="utf-8"
    )


def _stub_agent_cli(monkeypatch, answer=_fake_answer):
    monkeypatch.setattr(agent_runner, "run_agent",
                        lambda agent, prompt, cwd, timeout=None, model=None: answer(prompt))
    monkeypatch.setattr(agent_runner, "find_agent_cli", lambda **kw: fake_agent())
    monkeypatch.setattr(
        agent_runner, "agent_status",
        lambda: {"agent": fake_agent(), "reason": "ready", "detail": "Fake fake"},
    )


def test_init_stops_and_asks_for_layers_first(cli_repo):
    """Phase 1: no architecture, no template, so it must stop and ask."""
    res = CliRunner().invoke(cli, ["init", str(cli_repo)])
    assert res.exit_code == 0, res.output
    assert "status: needs_layers" in res.output
    assert "propose_layers_request.json" in res.output
    assert not (cli_repo / ".tldrgraph" / "layers.config.yaml").exists()


def test_init_extracts_before_asking_so_the_evidence_has_real_symbols(cli_repo):
    """
    The layer request must carry extracted symbols, not just a directory
    listing -- two repos with identical file trees can do entirely different
    things, and the agent is being asked to name what this one does.
    """
    CliRunner().invoke(cli, ["init", str(cli_repo)])
    payload = json.loads(
        (cli_repo / ".tldrgraph" / "propose_layers_request.json").read_text(encoding="utf-8")
    )
    symbols = payload["evidence"]["extracted_symbols"]
    assert symbols["total_symbols"] > 0
    assert any("cli.py" in path for path in symbols["symbols_by_file"])


def test_init_resumes_after_the_agent_answers_the_layers(cli_repo):
    """Phase 1 → 2: answering the request and re-running gets past the gate."""
    CliRunner().invoke(cli, ["init", str(cli_repo)])
    _answer_layers(cli_repo)

    res = CliRunner().invoke(cli, ["init", str(cli_repo)])
    assert res.exit_code == 0, res.output
    assert "status: needs_layers" not in res.output
    assert (cli_repo / ".tldrgraph" / "layers.config.yaml").is_file()
    assert (cli_repo / ".tldrgraph" / "graph.json").is_file()


def test_init_asks_before_spending_tokens_and_shows_the_estimate(cli_repo):
    """Phase 3 gate: the user is told the size of the job before it starts."""
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo)])

    assert res.exit_code == 0, res.output
    assert "status: needs_confirmation" in res.output
    assert "ASK THE USER" in res.output
    assert "tldrgraph init --yes" in res.output


def test_the_estimate_is_machine_readable(cli_repo):
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--json"])
    assert res.exit_code == 0, res.output

    payload = json.loads(res.output)
    assert payload["status"] == "needs_confirmation"
    progress = payload["progress"]
    assert progress["remaining"] > 0
    assert progress["agent_rounds"] >= 1
    assert progress["total_nodes"] >= progress["remaining"]


def test_yes_hands_out_an_enrichment_batch(cli_repo):
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes"])

    assert res.exit_code == 0, res.output
    assert "status: needs_enrichment" in res.output
    request = cli_repo / ".tldrgraph" / "enrichment_request.yaml"
    assert request.is_file()
    assert yaml.safe_load(request.read_text(encoding="utf-8"))["nodes"]


def test_init_applies_the_agents_enrichment_and_reaches_done(cli_repo):
    """The full loop, played out the way an agent would: init, answer, init."""
    _answer_layers(cli_repo)
    runner = CliRunner()
    runner.invoke(cli, ["init", str(cli_repo), "--yes"])

    state = cli_repo / ".tldrgraph"
    for _ in range(20):
        request = yaml.safe_load((state / "enrichment_request.yaml").read_text(encoding="utf-8"))
        (state / "enrichment_response.yaml").write_text(
            yaml.dump([
                {
                    "id": node["id"],
                    "intent": f"Handles {node['label']}.",
                    "input_fields": [],
                    "output_fields": [],
                    "calls": [],
                }
                for node in request["nodes"]
            ]),
            encoding="utf-8",
        )
        res = runner.invoke(cli, ["init", str(cli_repo)])
        assert res.exit_code == 0, res.output
        if "status: done" in res.output:
            break
    else:
        raise AssertionError("init never reached status: done")

    snapshot = json.loads((state / "graph.json").read_text(encoding="utf-8"))
    assert all(
        n.get("enrichment_source") == "agent"
        for n in snapshot["nodes"]
        if n.get("layer_id") != "shared"
    )
    assert not (state / APPROVAL_FILENAME).exists()


def test_full_approval_survives_manual_batches_without_reconfirmation(cli_repo):
    _answer_layers(cli_repo)
    runner = CliRunner()
    first = runner.invoke(cli, ["init", str(cli_repo), "--yes", "--batch", "1"])
    assert "status: needs_enrichment" in first.output

    state = cli_repo / ".tldrgraph"
    request = yaml.safe_load((state / "enrichment_request.yaml").read_text(encoding="utf-8"))
    (state / "enrichment_response.yaml").write_text(
        yaml.dump([{"id": request["nodes"][0]["id"], "intent": "Source-backed intent."}]),
        encoding="utf-8",
    )

    continued = runner.invoke(cli, ["init", str(cli_repo), "--batch", "1"])
    assert continued.exit_code == 0, continued.output
    assert "status: needs_confirmation" not in continued.output
    assert "status: needs_enrichment" in continued.output or "status: done" in continued.output


def test_limited_approval_does_not_authorize_the_remaining_campaign(cli_repo):
    _answer_layers(cli_repo)
    runner = CliRunner()
    first = runner.invoke(cli, ["init", str(cli_repo), "--yes", "--limit", "1"])
    assert "status: needs_enrichment" in first.output
    assert not (cli_repo / ".tldrgraph" / APPROVAL_FILENAME).exists()

    request = yaml.safe_load(
        (cli_repo / ".tldrgraph" / "enrichment_request.yaml").read_text(encoding="utf-8")
    )
    (cli_repo / ".tldrgraph" / "enrichment_response.yaml").write_text(
        yaml.dump([{"id": request["nodes"][0]["id"], "intent": "One approved node."}]),
        encoding="utf-8",
    )
    resumed = runner.invoke(cli, ["init", str(cli_repo)])
    assert "status: needs_confirmation" in resumed.output


def test_approval_does_not_cover_new_candidate_ids(tmp_path):
    approved = [{"id": "a"}, {"id": "b"}]
    remember_full_enrichment_approval(str(tmp_path), approved)
    assert enrichment_approval_is_active(str(tmp_path), [{"id": "b"}])
    assert not enrichment_approval_is_active(str(tmp_path), [{"id": "b"}, {"id": "new"}])


def test_an_applied_response_is_not_applied_twice(cli_repo):
    """
    The response file is renamed once merged. Left in place, the next `init`
    would re-apply the same answers and re-forge the same edges forever.
    """
    _answer_layers(cli_repo)
    runner = CliRunner()
    runner.invoke(cli, ["init", str(cli_repo), "--yes"])

    state = cli_repo / ".tldrgraph"
    request = yaml.safe_load((state / "enrichment_request.yaml").read_text(encoding="utf-8"))
    (state / "enrichment_response.yaml").write_text(
        yaml.dump([{"id": n["id"], "intent": "Does a thing."} for n in request["nodes"]]),
        encoding="utf-8",
    )
    runner.invoke(cli, ["init", str(cli_repo), "--yes"])

    assert not (state / "enrichment_response.yaml").exists()
    assert (state / "enrichment_response.applied.yaml").is_file()


def test_limit_caps_the_first_pass(cli_repo):
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--batch", "2", "--limit", "2"])
    assert res.exit_code == 0, res.output

    request = yaml.safe_load(
        (cli_repo / ".tldrgraph" / "enrichment_request.yaml").read_text(encoding="utf-8")
    )
    assert len(request["nodes"]) == 2


def test_agent_cli_is_automatic_by_default(monkeypatch, cli_repo, agent_allowed):
    calls = []
    monkeypatch.setattr(agent_runner, "find_agent_cli",
                        lambda **kw: calls.append(1) or fake_agent())
    monkeypatch.setattr(agent_runner, "run_agent", lambda *a, **k: _fake_answer(a[1]))

    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes"])
    assert res.exit_code == 0, res.output
    assert calls, "init must auto-detect an agent CLI by default"
    assert "status: done" in res.output


def test_no_agent_cli_forces_the_manual_handoff(monkeypatch, cli_repo, agent_allowed):
    calls = []
    monkeypatch.setattr(agent_runner, "find_agent_cli",
                        lambda **kw: calls.append(1) or fake_agent())
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--no-agent-cli"])
    assert res.exit_code == 0, res.output
    assert calls == []
    assert "status: needs_enrichment" in res.output


def test_init_defaults_to_two_hundred_node_batches():
    help_result = CliRunner().invoke(cli, ["init", "--help"])
    assert help_result.exit_code == 0
    assert "default:" in help_result.output and "200" in help_result.output


def test_init_defaults_embeddings_on_but_honours_environment(monkeypatch):
    monkeypatch.delenv("TLDRGRAPH_EMBEDDINGS", raising=False)
    assert resolve_init_embeddings(None) == "on"
    monkeypatch.setenv("TLDRGRAPH_EMBEDDINGS", "off")
    assert resolve_init_embeddings(None) == "off"
    assert resolve_init_embeddings("auto") == "auto"


def test_interactive_init_asks_once_then_finishes(monkeypatch, cli_repo, agent_allowed):
    _answer_layers(cli_repo)
    _stub_agent_cli(monkeypatch)
    monkeypatch.setattr(cli_pipeline, "stdin_is_interactive", lambda: True)
    res = CliRunner().invoke(cli, ["init", str(cli_repo)], input="\n")
    assert res.exit_code == 0, res.output
    assert res.output.count("Enrich now?") == 1
    assert "status: done" in res.output


def test_automatic_agent_keeps_json_output_parseable(monkeypatch, cli_repo, agent_allowed):
    _stub_agent_cli(monkeypatch)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["status"] == "done"


def test_embedding_failure_is_resumable(monkeypatch, cli_repo, agent_allowed):
    _stub_agent_cli(monkeypatch)
    monkeypatch.setattr(cli_pipeline, "_embedding_failure", lambda loader: "model unavailable")
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes"])
    assert res.exit_code == 0, res.output
    assert "status: needs_embeddings" in res.output
    assert "model unavailable" in res.output


def test_agent_cli_runs_the_whole_loop_when_asked(monkeypatch, cli_repo, agent_allowed):
    _stub_agent_cli(monkeypatch)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--agent-cli"])

    assert res.exit_code == 0, res.output
    assert "status: done" in res.output
    snapshot = json.loads((cli_repo / ".tldrgraph" / "graph.json").read_text(encoding="utf-8"))
    assert any(n.get("enrichment_source") == "agent" for n in snapshot["nodes"])


def test_agent_cli_failure_does_not_lose_the_graph(monkeypatch, cli_repo, agent_allowed):
    def _explode(agent, prompt, cwd, timeout=None, model=None):
        if _is_enrichment_prompt(prompt):
            raise agent_runner.AgentError("boom")
        return json.dumps(VALID_LAYER_SET)

    _stub_agent_cli(monkeypatch)
    monkeypatch.setattr(agent_runner, "run_agent", _explode)

    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--agent-cli"])
    assert res.exit_code == 0, res.output
    assert "boom" in res.output
    assert (cli_repo / ".tldrgraph" / "graph.json").is_file()


def test_empty_intents_do_not_spin_the_loop_forever(monkeypatch, cli_repo, agent_allowed):
    """
    An answer that echoes every id but writes no intent applies cleanly and
    clears nothing. Measuring progress by ids returned would re-request the same
    batch forever; progress is measured by nodes leaving the candidate set.
    """
    def _empty_answers(prompt):
        if not _is_enrichment_prompt(prompt):
            return json.dumps(VALID_LAYER_SET)
        start = prompt.index("Nodes (")
        nodes = json.loads(prompt[prompt.index("[", start):])
        return json.dumps([{"id": n["id"], "intent": "", "calls": []} for n in nodes])

    _stub_agent_cli(monkeypatch, answer=_empty_answers)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--yes", "--agent-cli"])
    assert res.exit_code == 0, res.output
    assert "cleared no nodes" in res.output


# --------------------------------------------------------------------------- #
# 5. One state directory
# --------------------------------------------------------------------------- #

def test_scan_creates_no_graphify_out_directory(cli_repo):
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo)])
    assert res.exit_code == 0, res.output

    assert not (cli_repo / "graphify-out").exists(), "scanning must add one folder, not two"
    assert (cli_repo / ".tldrgraph" / paths.GRAPHIFY_GRAPH_FILENAME).is_file()
    assert (cli_repo / ".tldrgraph" / paths.SNAPSHOT_FILENAME).is_file()


def test_graphify_export_does_not_overwrite_the_enriched_snapshot(cli_repo):
    """
    The two files are different artifacts. Sharing a name inside .tldrgraph/
    would mean the raw AST export silently clobbering enriched intents.
    """
    assert paths.GRAPHIFY_GRAPH_FILENAME != paths.SNAPSHOT_FILENAME

    _answer_layers(cli_repo)
    CliRunner().invoke(cli, ["init", str(cli_repo)])
    raw = json.loads((cli_repo / ".tldrgraph" / paths.GRAPHIFY_GRAPH_FILENAME).read_text())
    snapshot = json.loads((cli_repo / ".tldrgraph" / paths.SNAPSHOT_FILENAME).read_text())

    assert "layers" in snapshot or "nodes" in snapshot
    assert snapshot.get("nodes") is not None
    assert raw.get("nodes") is not None
    assert raw is not snapshot


# --------------------------------------------------------------------------- #
# 6. Managed .gitignore
# --------------------------------------------------------------------------- #

def test_gitignore_is_created_with_the_contract_kept(tmp_path):
    result = installer_module.ensure_gitignore(str(tmp_path))
    assert result["status"] == "created"

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".tldrgraph/*" in text
    assert "!.tldrgraph/AGENT_CONTRACT.md" in text
    assert "!.tldrgraph/layers.config.yaml" in text


def test_gitignore_uses_star_so_the_negations_can_work(tmp_path):
    """
    git never descends into an excluded directory, so `.tldrgraph/` would make
    every `!` line below it dead. The managed block must exclude entries instead.
    """
    installer_module.ensure_gitignore(str(tmp_path))
    lines = [
        l.strip() for l in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]
    assert ".tldrgraph/*" in lines
    assert ".tldrgraph/" not in lines


def test_an_existing_directory_ignore_is_neutralized(tmp_path):
    (tmp_path / ".gitignore").write_text(
        "*.pyc\n.tldrgraph/\nbuild/\n", encoding="utf-8"
    )
    installer_module.ensure_gitignore(str(tmp_path))
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")

    assert "# .tldrgraph/" in text, "the old directory ignore must be commented out"
    assert "*.pyc" in text and "build/" in text, "unrelated entries must survive"
    assert ".tldrgraph/*" in text


def test_gitignore_is_idempotent(tmp_path):
    installer_module.ensure_gitignore(str(tmp_path))
    first = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    second_result = installer_module.ensure_gitignore(str(tmp_path))

    assert second_result["status"] == "unchanged"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == first
    assert first.count(installer_module.GITIGNORE_BEGIN) == 1


def test_install_writes_the_gitignore_and_the_one_command(tmp_path):
    written = installer_module.install_agent_rules(str(tmp_path))

    assert Path(written["gitignore"]).is_file()
    cmd = Path(written["Claude Code (command)"])
    assert cmd.is_file()
    body = cmd.read_text(encoding="utf-8")
    assert "tldrgraph init" in body
    assert "--batch 200" in body and "--limit 200" in body
    assert "Never add `--limit`" in body and "`--embeddings off` unless" in body
    assert "without asking the user again" in body
    assert "/skills" in body and "$tldrgraph-init" in body
    # Every branch of the state machine must be documented in the command.
    for status in ("needs_layers", "needs_confirmation", "needs_enrichment"):
        assert status in body, status


def test_the_command_lands_in_every_convention_the_repo_uses(tmp_path):
    """Adding an agent tool is one row in the table, never new execution code."""
    (tmp_path / ".clinerules").mkdir()
    (tmp_path / ".opencode").mkdir()

    written = agent_commands.install_agent_commands(str(tmp_path))

    assert (tmp_path / ".clinerules" / "workflows" / "tldrgraph-init.md").is_file()
    assert (tmp_path / ".opencode" / "command" / "tldrgraph-init.md").is_file()
    # Windsurf leaves no marker here, so nothing of its is written.
    assert not (tmp_path / ".windsurf").exists()
    assert any(agent_commands.AGENTS_MD in key for key in written)


def test_all_agents_writes_every_known_tool(tmp_path):
    agent_commands.install_agent_commands(str(tmp_path), all_agents=True)
    for target in agent_commands.TARGETS:
        if target.command_path:
            assert (tmp_path / target.command_path).is_file(), target.name
        if target.instructions_path:
            assert (tmp_path / target.instructions_path).is_file(), target.name


def test_every_agent_gets_identical_instructions(tmp_path):
    """
    The whole point of the rewrite. Five bespoke rule files with five different
    wordings drifted apart and contradicted each other.
    """
    agent_commands.install_agent_commands(str(tmp_path), all_agents=True)

    bodies = set()
    for target in agent_commands.TARGETS:
        if target.instructions_path:
            text = (tmp_path / target.instructions_path).read_text(encoding="utf-8")
            bodies.add(text.split("---", 2)[-1].strip())
    agents_md = (tmp_path / agent_commands.AGENTS_MD).read_text(encoding="utf-8")
    bodies.add(
        agents_md.split(agent_commands.BLOCK_BEGIN)[1]
        .split(agent_commands.BLOCK_END)[0].strip()
    )
    assert len(bodies) == 1, "instruction files have drifted apart"


def test_every_agent_gets_an_identical_command(tmp_path):
    agent_commands.install_agent_commands(str(tmp_path), all_agents=True)
    bodies = {
        (tmp_path / t.command_path).read_text(encoding="utf-8").split("---", 2)[-1].strip()
        for t in agent_commands.TARGETS if t.command_path
    }
    assert len(bodies) == 1, "command files have drifted apart"


def test_codex_skill_matches_the_claude_command(tmp_path):
    """Codex gets the same workflow through its supported repo-local skill path."""
    agent_commands.install_agent_commands(str(tmp_path))

    claude = tmp_path / ".claude" / "commands" / "tldrgraph-init.md"
    codex = tmp_path / ".agents" / "skills" / "tldrgraph-init" / "SKILL.md"
    assert codex.is_file()
    assert codex.read_text(encoding="utf-8") == claude.read_text(encoding="utf-8")


def test_codex_uses_supported_repo_skill_not_a_dead_dot_codex_command(tmp_path):
    agent_commands.install_agent_commands(str(tmp_path))
    assert (tmp_path / ".agents/skills/tldrgraph-init/SKILL.md").is_file()
    assert not (tmp_path / ".codex/commands/tldrgraph-init.md").exists()


def test_no_tool_gets_a_bespoke_extra_artifact(tmp_path):
    """
    Claude used to get a skill AND a CLAUDE.md section AND a command, while
    Cursor got one rule file. Every tool now gets the same two artifacts at most.
    """
    agent_commands.install_agent_commands(str(tmp_path), all_agents=True)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / "CLAUDE.md").exists()

    claude = next(t for t in agent_commands.TARGETS if t.name == "Claude Code")
    # Claude Code reads AGENTS.md, so it needs no instructions file of its own.
    assert claude.instructions_path is None


def test_superseded_files_are_removed_on_install(tmp_path):
    """Two descriptions of the workflow means an agent reads a contradiction."""
    skill = tmp_path / ".claude" / "skills" / "tldrgraph" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("old workflow", encoding="utf-8")
    cursor_rule = tmp_path / ".cursor" / "rules" / "tldrgraph.mdc"
    cursor_rule.parent.mkdir(parents=True)
    cursor_rule.write_text("old rule", encoding="utf-8")

    installer_module.install_agent_rules(str(tmp_path))

    assert not skill.exists()
    assert not cursor_rule.exists()


def test_our_claude_md_block_is_removed_but_user_content_survives(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        "# My notes\n\nAlways run black.\n\n"
        f"{installer_module.CLAUDE_MD_BEGIN}\nold tldrgraph section\n"
        f"{installer_module.CLAUDE_MD_END}\n",
        encoding="utf-8",
    )
    installer_module.install_agent_rules(str(tmp_path))

    text = claude_md.read_text(encoding="utf-8")
    assert "Always run black." in text
    assert "old tldrgraph section" not in text
    assert installer_module.CLAUDE_MD_BEGIN not in text


def test_a_claude_md_we_created_alone_is_deleted(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        f"{installer_module.CLAUDE_MD_BEGIN}\nonly our block\n"
        f"{installer_module.CLAUDE_MD_END}\n",
        encoding="utf-8",
    )
    installer_module.install_agent_rules(str(tmp_path))
    assert not claude_md.exists(), "a file with nothing but our block should go"


def test_agents_md_is_merged_never_clobbered(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# House rules\n\nUse tabs.\n", encoding="utf-8")
    agent_commands.install_agent_commands(str(tmp_path))
    agent_commands.install_agent_commands(str(tmp_path))

    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Use tabs." in text, "the user's own AGENTS.md content must survive"
    assert text.count(agent_commands.BLOCK_BEGIN) == 1


def test_init_installs_the_gitignore_block_and_the_agent_command(cli_repo):
    """`init` is self-bootstrapping: no separate `install` step to remember."""
    CliRunner().invoke(cli, ["init", str(cli_repo)])
    assert ".tldrgraph/*" in (cli_repo / ".gitignore").read_text(encoding="utf-8")
    assert (cli_repo / ".claude" / "commands" / "tldrgraph-init.md").is_file()
    assert "TLDRGraph" in (cli_repo / "AGENTS.md").read_text(encoding="utf-8")


def test_gitignore_warnings_no_longer_contradict_the_managed_block(tmp_path):
    installer_module.ensure_gitignore(str(tmp_path))
    warnings = installer_module.gitignore_warnings(str(tmp_path))
    assert not any(".tldrgraph" in w for w in warnings)


def test_invented_ids_are_reported_not_silently_dropped(cli_repo):
    """
    An id that is not in the graph contributes nothing. Dropping it in silence
    leaves an agent looping and re-inventing the same id every round.
    """
    _answer_layers(cli_repo)
    runner = CliRunner()
    runner.invoke(cli, ["init", str(cli_repo), "--yes"])

    state = cli_repo / ".tldrgraph"
    request = yaml.safe_load((state / "enrichment_request.yaml").read_text(encoding="utf-8"))
    real = request["nodes"][0]["id"]
    (state / "enrichment_response.yaml").write_text(
        yaml.dump([
            {"id": real, "intent": "A real node."},
            {"id": "totally_made_up_node_id", "intent": "Not in the graph."},
        ]),
        encoding="utf-8",
    )

    res = runner.invoke(cli, ["init", str(cli_repo), "--yes"])
    assert res.exit_code == 0, res.output
    assert "totally_made_up_node_id" in res.output
    assert "not in the graph" in res.output
    assert "Copy ids verbatim" in res.output


def test_model_selection_reaches_the_agent_argv():
    """--agent-model / $TLDRGRAPH_AGENT_MODEL must land on the real command line."""
    claude = next(a for a in agent_runner.KNOWN_AGENTS if a.name == "claude")
    gemini = next(a for a in agent_runner.KNOWN_AGENTS if a.name == "gemini")

    assert "--model" not in claude.argv("hi")
    assert claude.argv("hi", model="opus")[1:3] == ["--model", "opus"]
    # Every CLI spells the flag differently; that lives in the table, not in
    # branching at the call site.
    assert gemini.argv("hi", model="gemini-2.5-pro")[1:3] == ["-m", "gemini-2.5-pro"]


def test_model_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(agent_runner.ENV_MODEL, "sonnet")
    claude = next(a for a in agent_runner.KNOWN_AGENTS if a.name == "claude")
    assert claude.argv("hi")[1:3] == ["--model", "sonnet"]


def test_init_re_extracts_so_deleted_code_leaves_the_graph(cli_repo):
    """
    HARD GATE. `init` used to reuse an existing graphify export and only
    re-extract when it was missing. Every later phase then worked from a
    snapshot of whatever the code used to be: agents were handed deleted
    functions to write intents for, and anything added since was invisible.
    """
    _answer_layers(cli_repo)
    runner = CliRunner()
    runner.invoke(cli, ["init", str(cli_repo)])

    raw = json.loads(
        (cli_repo / ".tldrgraph" / paths.GRAPHIFY_GRAPH_FILENAME).read_text(encoding="utf-8")
    )
    assert any(n.get("label", "").startswith("Engine") for n in raw["nodes"])

    # Delete the class and add a new one, exactly as an edit between runs would.
    (cli_repo / "sample" / "engine.py").write_text(
        "class Rebuilt:\n    def go(self):\n        return 2\n", encoding="utf-8"
    )
    runner.invoke(cli, ["init", str(cli_repo)])

    raw = json.loads(
        (cli_repo / ".tldrgraph" / paths.GRAPHIFY_GRAPH_FILENAME).read_text(encoding="utf-8")
    )
    labels = {n.get("label", "") for n in raw["nodes"]}
    assert any(l.startswith("Rebuilt") for l in labels), "new code must appear"
    assert not any(l.startswith("Engine") for l in labels), "deleted code must go"


def test_prose_nodes_are_never_queued_for_enrichment():
    """
    graphify emits `rationale` nodes whose label IS a sentence of documentation.
    Queueing one asks the agent to pay for copying a docstring back onto itself.
    On this repository they were 390 of 1419 nodes -- nearly half the backlog.
    """
    from tldrgraph.cli import needs_agent_enrichment

    code = {"layer_id": "api", "type": "code", "label": "CasesController"}
    prose = {"layer_id": "api", "type": "rationale",
             "label": "Handles the pension approval workflow end to end."}

    assert needs_agent_enrichment(code)
    assert not needs_agent_enrichment(prose)


def test_the_enrichment_queue_and_dead_code_agree_on_what_is_not_code():
    """Both must read the same set, or one will contradict the other."""
    from tldrgraph import cli as cli_module
    from tldrgraph.deadcode import NON_CODE_NODE_TYPES as deadcode_set

    assert cli_module.NON_CODE_NODE_TYPES is deadcode_set


def test_json_mode_emits_parseable_json_only(cli_repo):
    """
    graphify prints progress and warnings to stdout. Under --json those land in
    front of the payload and break every parser reading it.
    """
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--json"])
    assert res.exit_code == 0, res.output

    payload = json.loads(res.stdout)
    assert payload["status"] in {"needs_confirmation", "needs_enrichment", "done"}


def test_enriched_count_does_not_include_excluded_nodes(cli_repo):
    """
    "enriched" used to be computed as total-minus-candidates, so utility and
    prose nodes -- never eligible in the first place -- were reported as done.
    A fresh graph claimed hundreds enriched before a single intent existed.
    """
    _answer_layers(cli_repo)
    res = CliRunner().invoke(cli, ["init", str(cli_repo), "--json"])
    payload = json.loads(res.stdout)

    assert payload["progress"]["enriched"] == 0, "nothing has been enriched yet"
    assert payload["progress"]["excluded"] >= 0
    assert payload["progress"]["remaining"] > 0
