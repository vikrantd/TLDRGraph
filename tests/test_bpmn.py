"""Control-flow extraction and the BPMN payload the Workflow Explorer renders."""

import textwrap

import networkx as nx
import pytest

from tldrgraph.bpmn_extract import extract_process
from tldrgraph.visualizer.bpmn_data import (
    LANE_EXTERNAL,
    LANE_SYSTEM,
    _question_from,
    build_workflow_process,
)
from tldrgraph.visualizer.bpmn_phrasing import ELEMENT_PHRASES, phrase_for_element

SAMPLE = '''
def charge_customer(order, gateway):
    """Take payment for an order."""
    total = compute_total(order)
    if total <= 0:
        return {"status": "nothing to charge"}

    for attempt in retries(3):
        try:
            receipt = gateway.submit(total)
        except TimeoutError:
            log_retry(attempt)
            continue
        if receipt.ok:
            return {"status": "paid", "receipt": receipt}

    raise PaymentFailed(order)
'''


@pytest.fixture()
def sample_repo(tmp_path):
    (tmp_path / "billing.py").write_text(textwrap.dedent(SAMPLE), encoding="utf-8")
    return tmp_path


def test_extracts_every_branch_loop_and_exit(sample_repo):
    process = extract_process(str(sample_repo), "billing.py", "charge_customer", "p")
    kinds = [e["kind"] for e in process["elements"]]

    assert kinds.count("start") == 1
    # Both `if` statements become gateways, the `for` becomes a loop, the
    # `except` an error path, and each `return`/`raise` its own exit.
    assert kinds.count("gateway") == 2
    assert kinds.count("loop") == 1
    assert kinds.count("error") == 2       # the handler and the explicit raise
    assert kinds.count("end") >= 2


def test_branches_are_labelled_and_the_loop_returns(sample_repo):
    process = extract_process(str(sample_repo), "billing.py", "charge_customer", "p")
    labels = {f["label"] for f in process["flows"]}
    assert "Yes" in labels
    assert "No" in labels
    assert any(f["kind"] == "loop_back" for f in process["flows"])


def test_every_element_is_reachable(sample_repo):
    """Nothing may be left stranded: each shape connects to the process."""
    process = extract_process(str(sample_repo), "billing.py", "charge_customer", "p")
    linked = {f["source"] for f in process["flows"]} | {f["target"] for f in process["flows"]}
    stranded = [e["id"] for e in process["elements"] if e["id"] not in linked]
    assert stranded == []


def test_unparseable_symbol_is_reported_rather_than_guessed(sample_repo):
    assert extract_process(str(sample_repo), "billing.py", "no_such_function", "p") is None
    (sample_repo / "app.ts").write_text("export const x = 1;", encoding="utf-8")
    assert extract_process(str(sample_repo), "app.ts", "x", "p") is None


def test_external_work_is_not_confused_with_a_dictionary_lookup(tmp_path):
    (tmp_path / "m.py").write_text(textwrap.dedent('''
        def run(data, path):
            value = data.get("key")
            with open(path, "w") as handle:
                handle.write(value)
    '''), encoding="utf-8")
    process = extract_process(str(tmp_path), "m.py", "run", "p")
    externals = {e["external"] for e in process["elements"] if e["external"]}
    assert externals == {"File system"}


def _workflow(tmp_path):
    return {
        "id": "flow_test",
        "steps": [{
            "step_number": 1,
            "node_id": "billing_charge_customer",
            "symbol": "charge_customer",
            "file": "billing.py",
            "code_start": 1,
        }],
    }


def test_payload_carries_lanes_and_a_single_start_and_end(sample_repo):
    nodes_by_id = {
        "billing_charge_customer": {"label": "charge_customer()", "file": "billing.py"},
    }
    process = build_workflow_process(
        str(sample_repo), _workflow(sample_repo), nx.DiGraph(), nodes_by_id
    )

    kinds = [e["kind"] for e in process["elements"]]
    assert kinds.count("start") == 1
    assert kinds[-1] == "end"
    assert {l["id"] for l in process["lanes"]} <= {"user", LANE_SYSTEM, LANE_EXTERNAL}
    assert all(e["lane"] for e in process["elements"])

    ids = {e["id"] for e in process["elements"]}
    assert all(f["source"] in ids and f["target"] in ids for f in process["flows"])


def test_conditions_are_turned_into_questions():
    assert _question_from("not results") == "Is there no results?"
    assert _question_from("path.is_file()") == "Is path a file we can read?"
    assert _question_from("len(nodes) > 0") == "Are there any nodes?"
    assert _question_from("cfg is None") == "Is cfg missing?"


def test_authored_phrase_is_dropped_when_the_code_moves_on():
    key, entry = next(iter(ELEMENT_PHRASES.items()))
    path, line, kind = key.rsplit(":", 2)

    current = {"file": path, "line": int(line), "kind": kind, "detail": entry["when"]}
    assert phrase_for_element("any_flow", 1, current) == entry["say"]

    drifted = dict(current, detail=entry["when"] + " and something_new")
    assert phrase_for_element("any_flow", 1, drifted) is None


def test_shipped_phrases_still_match_the_code_they_describe():
    """Guards against phrasing quietly going stale as this repo changes."""
    from tldrgraph.visualizer import prepare_visualizer_data

    data = prepare_visualizer_data(".")
    seen = {}
    for workflow in data["workflows"]:
        for element in workflow["process"]["elements"]:
            seen[f"{element.get('file')}:{element['line']}:{element['kind']}"] = element["detail"]

    stale = [
        key for key, entry in ELEMENT_PHRASES.items()
        if key in seen and entry.get("when", "").strip() != (seen[key] or "").strip()
    ]
    assert stale == [], f"phrases describe code that has changed: {stale}"


# ---------------------------------------------------------------------------
# Languages other than Python
# ---------------------------------------------------------------------------

TS_SOURCE = '''
export async function chargeCustomer(order: Order, gateway: Gateway) {
  const total = computeTotal(order);
  if (total <= 0) {
    return { status: "nothing to charge" };
  }
  for (const attempt of retries(3)) {
    try {
      const receipt = await gateway.submit(total);
      if (receipt.ok) {
        return { status: "paid", receipt };
      }
    } catch (err) {
      logRetry(attempt);
      continue;
    }
  }
  throw new PaymentFailed(order);
}
'''


@pytest.fixture()
def ts_repo(tmp_path):
    (tmp_path / "billing.ts").write_text(TS_SOURCE, encoding="utf-8")
    (tmp_path / "billing.js").write_text(
        TS_SOURCE.replace(": Order", "").replace(": Gateway", ""), encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize("filename", ["billing.ts", "billing.js"])
def test_typescript_and_javascript_yield_the_same_shapes(ts_repo, filename):
    process = extract_process(str(ts_repo), filename, "chargeCustomer", "p")
    assert process is not None, f"{filename} produced no process"

    kinds = [e["kind"] for e in process["elements"]]
    assert kinds.count("gateway") == 2          # the two `if`s
    assert kinds.count("loop") == 1             # the `for ... of`
    assert kinds.count("error") == 2            # the `catch` and the `throw`
    assert kinds.count("end") >= 1              # the `return`s

    labels = {f["label"] for f in process["flows"]}
    assert {"Yes", "No"} <= labels
    assert any(f["kind"] == "loop_back" for f in process["flows"])


def test_a_loop_says_what_it_runs_over(ts_repo):
    process = extract_process(str(ts_repo), "billing.ts", "chargeCustomer", "p")
    loop = next(e for e in process["elements"] if e["kind"] == "loop")
    assert loop["label"] == "for each retries(3)"


def test_dispatcher_picks_the_parser_and_admits_when_it_cannot(ts_repo):
    assert extract_process(str(ts_repo), "billing.ts", "chargeCustomer", "p") is not None
    assert extract_process(str(ts_repo), "billing.ts", "noSuchFunction", "p") is None
    (ts_repo / "notes.txt").write_text("nothing to parse", encoding="utf-8")
    assert extract_process(str(ts_repo), "notes.txt", "anything", "p") is None


def test_every_supported_language_declares_its_extensions():
    from tldrgraph.bpmn_languages import LANGUAGES, spec_for, supported_extensions

    assert ".ts" in supported_extensions() and ".js" in supported_extensions()
    assert spec_for("src/app.tsx") is not None
    assert spec_for("src/app.unknown") is None
    for spec in LANGUAGES:
        assert spec.functions and spec.branches and spec.calls, spec.name


# ---------------------------------------------------------------------------
# Discovery and the enrichment cycle
# ---------------------------------------------------------------------------

def test_workflows_are_discovered_for_a_repository_with_no_blueprint():
    from tldrgraph.visualizer.flows_discover import discover_workflows

    graph = nx.DiGraph()
    chain = [
        ("api_handle_order", "handleOrder()", "src/routes/orders.ts", "api"),
        ("svc_charge", "chargeCustomer()", "src/services/billing.ts", "service"),
        ("gw_submit", "submit()", "src/gateways/stripe.ts", "external"),
        ("db_save", "saveReceipt()", "src/db/receipts.ts", "data"),
    ]
    nodes_by_id = {}
    for node_id, label, path, layer in chain:
        nodes_by_id[node_id] = {"label": label, "file": path, "layer_id": layer,
                                "layer": layer.title(), "is_test": False}
        graph.add_node(node_id, label=label, file=path)
    for src, tgt in zip(chain, chain[1:]):
        graph.add_edge(src[0], tgt[0], relation="calls")
    # Give the root the outgoing reach a real handler has.
    graph.add_edge("api_handle_order", "db_save", relation="calls")

    found = discover_workflows(
        graph, nodes_by_id,
        format_step=lambda node_id, step_number: {
            "node_id": node_id, "step_number": step_number,
            "symbol": nodes_by_id[node_id]["label"],
            "file": nodes_by_id[node_id]["file"],
            "layer": nodes_by_id[node_id]["layer"],
            "layer_id": nodes_by_id[node_id]["layer_id"],
        },
        collect_support=lambda steps: [],
    )

    assert found, "a repository with clear entry points should yield workflows"
    workflow = found[0]
    assert workflow["root_id"] == "api_handle_order"
    assert workflow["category"] == "API request"
    assert [s["node_id"] for s in workflow["steps"]][:2] == ["api_handle_order", "svc_charge"]
    assert "()" not in workflow["title"]


def test_tests_and_vendored_code_are_never_offered_as_a_journey():
    from tldrgraph.visualizer.flows_discover import rank_entry_points

    graph = nx.DiGraph()
    nodes_by_id = {}
    for node_id, path in [("t", "tests/test_orders.ts"), ("v", "node_modules/lib/index.js")]:
        nodes_by_id[node_id] = {"label": "handleOrder()", "file": path, "is_test": path.startswith("tests")}
        graph.add_node(node_id)
        for i in range(4):
            graph.add_edge(node_id, f"{node_id}_child{i}", relation="calls")

    assert rank_entry_points(graph, nodes_by_id, 10) == []


def test_enrichment_round_trip_stores_phrases_against_their_code(tmp_path):
    from tldrgraph.bpmn_enrichment import apply_response, collect_candidates, load_store, write_request

    workflows = [{
        "id": "flow_x",
        "process": {"elements": [
            {"kind": "gateway", "file": "billing.ts", "line": 4, "detail": "total <= 0",
             "label": "Is it true that total <= 0?", "minor": False, "step_title": "Charge"},
            {"kind": "task", "file": "billing.ts", "line": 3, "detail": "computeTotal(order)",
             "label": "Compute total", "node_id": "svc_total", "minor": False},
        ]}
    }]

    candidates = collect_candidates(workflows, load_store(str(tmp_path)))
    # The gateway needs words; the activity already resolved to a named symbol.
    assert [c["kind"] for c in candidates] == ["gateway"]

    path, queued = write_request(str(tmp_path), candidates)
    assert queued == 1 and path.endswith("bpmn_request.yaml")

    result = apply_response(str(tmp_path), [{
        "key": "billing.ts:4:gateway", "when": "total <= 0",
        "say": "Is there anything to charge?", "yes": "Nothing due", "no": "Amount owed",
    }])
    assert result["applied"] == 1

    stored = load_store(str(tmp_path))
    assert stored["billing.ts:4:gateway"]["say"] == "Is there anything to charge?"

    # Phrased and still accurate, so it drops out of the next batch.
    assert collect_candidates(workflows, stored) == []


def test_a_phrase_is_requeued_once_its_code_changes(tmp_path):
    from tldrgraph.bpmn_enrichment import apply_response, collect_candidates, load_store

    apply_response(str(tmp_path), [{
        "key": "billing.ts:4:gateway", "when": "total <= 0", "say": "Is there anything to charge?",
    }])
    moved_on = [{
        "id": "flow_x",
        "process": {"elements": [
            {"kind": "gateway", "file": "billing.ts", "line": 4, "detail": "total <= 0 || order.isVoid",
             "label": "auto", "minor": False},
        ]},
    }]
    candidates = collect_candidates(moved_on, load_store(str(tmp_path)))
    assert len(candidates) == 1 and candidates[0]["stale"] is True
