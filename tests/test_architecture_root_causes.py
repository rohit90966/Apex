"""
Regression tests for the root causes found in the Harbor Events audit (reports/root_cause_matrix.md).

Each test reproduces a failure *mechanism*, not a Harbor Events defect: the planner/executor fixtures
below are a synthetic parts catalogue (different domain, routes, selectors and data), and the planner
inputs are hand-built inventories. Nothing here is imported by, or known to, the production agent.

  RC-1  instance pages (/x/42) were never planned, so forms only reachable there were untested
  RC-2  investigation mutation budget counted navigation as mutation and stopped exploration early
  RC-3  repeated executions reused identity data, so retries tested a different precondition
  RC-4  a passing or infrastructure-error retry downgraded a reproduced failure without evidence
  RC-5  empty-value <option> (placeholder/reset) was recorded as a selectable filter value
  RC-6  filter/sort oracles relied on whole-record substrings and label-derived direction only
  RC-7  per-record commands (cancel/archive) had no post-action check of the record
  RC-8  a size cap exhausted the first form's boundary cases, dropping every later form
"""

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app import test_data
from app.execution import list_oracles
from app.execution.executor import BrowserExecutor, record_unchanged
from app.intelligence.target_context import inventory_from_report, route_template
from app.investigation_agent.workflow import probe_sent_state_changing_request
from app.schemas import (
    ActionStep, ActionType, AssertionType, ExecutionAssertion, ExecutionResult, ExecutionStatus, TestCase,
)
from app.test_planning.field_constraints import valid_filler
from app.intelligence.target_context import ObservedField
from test_multi_website_generalization import _report, _state

ORIGIN = "http://127.0.0.1:8555"


def check(**kw):
    kw.setdefault("description", f"{kw['assertion_type']} check")
    return ExecutionAssertion(**kw)


def step(**kw):
    kw.setdefault("description", f"{kw.get('action')} {kw.get('selector') or kw.get('value') or ''}")
    return ActionStep(**kw)


def plan(report, target_count=300):
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    planner = KnowledgeGuidedTestPlanner()
    cases, _, _ = planner.plan_test_suite_sync(report, target_count=target_count)
    return cases, planner.last_diagnostics


def catalogue_report(link_instances=True):
    """A parts catalogue: list page linking to item pages; the order form exists only on item pages."""
    states = [
        _state("s0", ORIGIN, "/", ["Parts Depot"], reached="ENTRY"),
        _state("s1", ORIGIN, "/parts", ["Parts"]),
        _state("s2", ORIGIN, "/parts/17", ["Hex Bolt"]),
        _state("s3", ORIGIN, "/parts/18", ["Lock Nut"]),
        _state("s4", ORIGIN, "/requests", ["Open requests"]),
    ]
    controls = [{"selector": "#nav-requests", "tag": "a", "name": "Requests", "href": "/requests", "state_id": "s0",
                 "url": f"{ORIGIN}/", "route": "/"},
                {"selector": "#nav-parts", "tag": "a", "name": "Parts", "href": "/parts", "state_id": "s0",
                 "url": f"{ORIGIN}/", "route": "/"}]
    if link_instances:
        controls += [{"selector": f'a[href="/parts/{n}"]', "tag": "a", "name": name, "href": f"/parts/{n}",
                      "state_id": "s1", "url": f"{ORIGIN}/parts", "route": "/parts"}
                     for n, name in ((17, "Hex Bolt"), (18, "Lock Nut"))]
    controls += [{"selector": f"#withdraw-{n}", "tag": "button", "control_type": "submit", "name": "Withdraw",
                  "state_id": "s4", "url": f"{ORIGIN}/requests", "route": "/requests"} for n in (3, 4)]
    order_fields = [
        {"selector": "#units", "name": "units", "field_id": "units", "label": "Units", "field_type": "number",
         "required": True, "min": "1", "max": "50"},
        {"selector": "#requester", "name": "requester", "field_id": "requester", "label": "Requested by",
         "field_type": "text", "required": True, "maxlength": 40},
    ]
    forms = [
        {"form_id": "order", "form_selector": "#order", "action": f"/parts/{n}/order", "method": "POST",
         "submit_selector": "#order-button", "state_id": sid, "url": f"{ORIGIN}/parts/{n}", "route": f"/parts/{n}",
         "fields": order_fields} for n, sid in ((17, "s2"), (18, "s3"))
    ] + [
        {"form_id": "withdraw", "form_selector": "form", "action": "/requests/3/withdraw", "method": "POST",
         "submit_selector": "#withdraw-3", "state_id": "s4", "url": f"{ORIGIN}/requests", "route": "/requests",
         "fields": []},
    ]
    return _report(ORIGIN, "run-parts", states, controls, forms)


# ── RC-1 / RC-8: instance pages are planned once per template, and caps keep every form ─────────────

def test_forms_on_linked_instance_pages_are_planned_once_per_template():
    inv = inventory_from_report(catalogue_report())
    assert inv.linked_instance_routes() == ["/parts/17"]          # one representative of /parts/{id}
    assert "/parts/17" not in inv.navigable_routes()                # route tests still exclude instances
    cases, _ = plan(catalogue_report())
    units = [c for c in cases if (c.derived_values or {}).get("field_selector") == "#units"]
    assert units, "the order form on the item page must be tested"
    assert {(c.derived_values or {}).get("input_class") for c in units} >= {"BELOW_MIN", "ABOVE_MAX"}
    assert all(any("/parts/17" in (s.value or "") for s in c.steps) for c in units)
    assert not any(any("/parts/18" in (s.value or "") for s in c.steps) for c in cases)


def test_unlinked_instance_pages_are_not_planned():
    inv = inventory_from_report(catalogue_report(link_instances=False))
    assert inv.linked_instance_routes() == []
    cases, _ = plan(catalogue_report(link_instances=False))
    assert not [c for c in cases if (c.derived_values or {}).get("field_selector") == "#units"]


def test_route_template():
    assert route_template("/parts/17/order") == "/parts/{id}/order"
    assert route_template("/tasks/3f2a9c1b") == "/tasks/{id}"
    assert route_template("/about") == "/about"


def test_size_cap_keeps_every_form_represented():
    cases, _ = plan(catalogue_report(), target_count=12)
    pages = {route_template(s.route) for c in cases for s in [inventory_from_report(catalogue_report()).state(e)
                                                              for e in c.evidence_ids or []] if s}
    assert "/parts/{id}" in pages and "/requests" in pages


# ── RC-2: mutation budget accounting ────────────────────────────────────────────────────────────────

def test_only_state_changing_requests_count_as_mutations():
    ev = lambda m: SimpleNamespace(method=m)
    assert not probe_sent_state_changing_request(SimpleNamespace(network_events=[ev("GET"), ev("GET")]))
    assert not probe_sent_state_changing_request(SimpleNamespace(network_events=[]))
    assert probe_sent_state_changing_request(SimpleNamespace(network_events=[ev("GET"), ev("POST")]))
    assert probe_sent_state_changing_request(SimpleNamespace(network_events=[ev("delete")]))


# ── RC-3: per-execution unique identity data ─────────────────────────────────────────────────────────

def test_identity_fillers_carry_a_length_preserving_unique_token():
    email = valid_filler(ObservedField(selector="#e", field_type="email"))
    user = valid_filler(ObservedField(selector="#u", name="username", field_type="text"))
    plain = valid_filler(ObservedField(selector="#c", name="city", field_type="text"))
    assert test_data.UNIQUE_TOKEN in email and test_data.UNIQUE_TOKEN in user
    assert test_data.UNIQUE_TOKEN not in plain
    a, b = test_data.resolve(email, test_data.new_token()), test_data.resolve(email, test_data.new_token())
    assert a != b and len(a) == len(b) == len(email)


def test_runner_resolves_a_fresh_token_for_every_execution(monkeypatch):
    from app.execution.runner import QAPlanRunner
    seen = []

    async def fake_execute(test_id, test_name, steps, initial_url=None):
        seen.append([s.value for s in steps if s.action == ActionType.FILL])
        return ExecutionResult(test_id=test_id, test_name=test_name, status=ExecutionStatus.PASSED, total_steps=len(steps),
                               steps_completed=len(steps), assertions_evaluated=1, meaningful_assertions_evaluated=1)

    runner = QAPlanRunner()
    monkeypatch.setattr(runner.executor, "execute_steps", fake_execute)
    tc = TestCase(test_id="TC-U", name="u", objective="o", action_description="a", expected_outcome="e",
                  selection_reason="r", confidence=0.9,
                  steps=[step(step_number=1, action=ActionType.NAVIGATE, value=f"{ORIGIN}/signup"),
                         step(step_number=2, action=ActionType.FILL, selector="#email",
                                    value=f"qa.{test_data.UNIQUE_TOKEN}@example.com")])
    r1 = asyncio.run(runner.run_test(tc, ORIGIN + "/"))
    r2 = asyncio.run(runner.run_test(tc, ORIGIN + "/"))
    assert seen[0] != seen[1] and test_data.UNIQUE_TOKEN not in seen[0][0]
    assert r1.evidence_retained["test_data_namespace"] != r2.evidence_retained["test_data_namespace"]


# ── RC-4: retry evidence ─────────────────────────────────────────────────────────────────────────────

def _failing_case(source="SEMANTIC_CONVENTION"):
    return TestCase(test_id="TC-R", name="r", objective="o", action_description="a", expected_outcome="e",
                    selection_reason="r", confidence=0.9,
                    steps=[step(step_number=1, action=ActionType.NAVIGATE, value=f"{ORIGIN}/"),
                           step(step_number=2, action=ActionType.CLICK, selector="#go",
                                      assertion=check(assertion_type=AssertionType.TEXT_VISIBLE,
                                                                   expected_value="Done", expected_source=source))])


def _result(status, step=2):
    return ExecutionResult(test_id="TC-R", test_name="r", status=status, total_steps=2, steps_completed=2,
                           failure_step=step if status != ExecutionStatus.PASSED else None,
                           failure_reason="Expected text 'Done'" if status == ExecutionStatus.FAILED else None)


def _investigate(retries):
    from app.bugs.investigator import BugInvestigator
    queue = list(retries)

    class Runner:
        async def run_test(self, tc, url):
            return queue.pop(0)

    inv = BugInvestigator(runner=Runner(), verification_retries=len(retries))
    return asyncio.run(inv.investigate_failure(_result(ExecutionStatus.FAILED), _failing_case(), ORIGIN))


def test_consistent_reproduction_is_likely_and_records_attempts():
    report = _investigate([_result(ExecutionStatus.FAILED), _result(ExecutionStatus.FAILED)])
    assert report.classification == "LIKELY_DEFECT" and report.reproduction_rate == "3/3 runs failed"
    assert [a["attempt"] for a in report.attempts] == [1, 2, 3] and report.verdict_rationale


def test_infrastructure_errors_on_retry_neither_confirm_nor_refute():
    report = _investigate([_result(ExecutionStatus.ENVIRONMENT_ERROR), _result(ExecutionStatus.INCONCLUSIVE)])
    assert report.classification == "INCONCLUSIVE"
    assert "could not be re-evaluated" in report.verdict_rationale
    assert [a["counted"] for a in report.attempts] == [True, False, False]


def test_failure_before_the_step_under_test_is_not_counted():
    # Retry 1 failed in its prelude (step 1) and never reached the oracle; retry 2 reproduced the failure.
    report = _investigate([_result(ExecutionStatus.FAILED, step=1), _result(ExecutionStatus.FAILED)])
    assert report.classification == "LIKELY_DEFECT" and report.reproduction_rate == "2/2 runs failed"
    assert report.attempts[1]["counted"] is False and report.attempts[1]["reproduces_initial"] is False


def test_failure_at_a_later_step_is_not_a_reproduction():
    report = _investigate([_result(ExecutionStatus.FAILED, step=3), _result(ExecutionStatus.FAILED)])
    assert report.classification == "INCONCLUSIVE"
    assert report.attempts[1]["reproduces_initial"] is False


def test_a_passing_retry_is_recorded_not_treated_as_proof():
    report = _investigate([_result(ExecutionStatus.PASSED), _result(ExecutionStatus.FAILED)])
    assert report.classification == "INCONCLUSIVE"
    assert "does not prove correct behaviour" in report.verdict_rationale
    assert [a["status"] for a in report.attempts] == ["FAILED", "PASSED", "FAILED"]


# ── RC-6: collection oracles (pure) ──────────────────────────────────────────────────────────────────

TABLE = {"headers": ["Name", "Kind", "Weight"],
         "fields": [["Hex Bolt", "Bolt", "120 g"], ["Lock Nut", "Nut", "18 g"], ["Flat Washer", "Washer", "9.5 g"]],
         "texts": ["Hex Bolt Bolt 120 g", "Lock Nut Nut 18 g", "Flat Washer Washer 9.5 g"],
         "values": [120.0, 18.0, 9.5]}


def test_sort_oracle_reads_the_named_column_and_separates_monotonic_from_direction():
    spec = {"key": "number", "column_hint": "weight"}
    # 120, 18, 9.5 is monotonic (descending): a wrong *direction*, not a broken sort
    assert list_oracles.evaluate_sorted(TABLE, {**spec, "any_direction": True})[0] == "pass"
    assert list_oracles.evaluate_sorted(TABLE, {**spec, "direction": "asc"})[0] == "fail"
    shuffled = {**TABLE, "fields": [TABLE["fields"][i] for i in (1, 0, 2)]}   # 18, 120, 9.5: lexicographic-style
    assert list_oracles.evaluate_sorted(shuffled, {**spec, "any_direction": True})[0] == "fail"


def test_filter_oracle_compares_the_records_own_field():
    domain = ["Bolt", "Nut", "Washer"]
    assert list_oracles.evaluate_filter(TABLE, "Nut", domain)[0] == "fail"
    only_nuts = {"fields": [["Lock Nut", "Nut", "18 g"]], "texts": ["Lock Nut Nut 18 g"]}
    assert list_oracles.evaluate_filter(only_nuts, "Nut", domain)[0] == "pass"
    # A name containing the selected word does not make a Washer a Nut (no substring matching)
    tricky = {"fields": [["Nut Driver Washer", "Washer", "1 g"]], "texts": ["Nut Driver Washer Washer 1 g"]}
    assert list_oracles.evaluate_filter(tricky, "Nut", domain)[0] == "fail"
    # No field drawn from the options: not evaluable, never a guess
    unknown = {"fields": [["Hex Bolt", "120 g"]], "texts": ["Hex Bolt 120 g"]}
    assert list_oracles.evaluate_filter(unknown, "Nut", domain)[0] == "not_evaluable"


def test_record_unchanged_rule():
    assert record_unchanged({"id": "r3", "text": "x"}, 1)
    assert not record_unchanged({"id": "r3", "text": "x"}, 0)
    assert record_unchanged({"id": "", "text": "x", "count": 2}, 2)
    assert not record_unchanged({"id": "", "text": "x", "count": 2}, 1)
    assert not record_unchanged(None, 5)


# ── RC-7: per-record command tests are planned from evidence ────────────────────────────────────────

def test_record_command_cases_are_planned_for_field_less_instance_posts():
    cases, diag = plan(catalogue_report())
    rec = [c for c in cases if c.oracle_class == "RECORD_EFFECT"]
    assert len(rec) == 1 and rec[0].steps[-1].selector == "#withdraw-3"
    assert rec[0].steps[-1].assertion.assertion_type == AssertionType.RECORD_CHANGED
    assert "TS-RECORD-COMMAND" in rec[0].knowledge_ids


# ── Live mechanism checks on a synthetic app (executor + perception) ─────────────────────────────────

PARTS = [{"id": 1, "name": "Hex Bolt", "kind": "Bolt", "weight": 120.0},
         {"id": 2, "name": "Lock Nut", "kind": "Nut", "weight": 18.0},
         {"id": 3, "name": "Flat Washer", "kind": "Washer", "weight": 9.5},
         {"id": 4, "name": "Wing Nut", "kind": "Nut", "weight": 22.0}]


def parts_app(defective: bool) -> FastAPI:
    app = FastAPI()
    archived = set()

    @app.get("/parts", response_class=HTMLResponse)
    def parts(kind: str = "", order: str = ""):
        rows = [p for p in PARTS if p["id"] not in archived]
        if kind:
            rows = [p for p in rows if p["kind"] == kind or (defective and p["kind"] == "Washer")]
        if order == "w_up":
            rows.sort(key=(lambda p: f"{p['weight']} g") if defective else (lambda p: p["weight"]))
        opts = '<option value="">All kinds</option>' + "".join(f'<option value="{k}">{k}</option>' for k in ("Bolt", "Nut", "Washer"))
        body = "".join(
            f'<tr id="part-{p["id"]}"><td>{p["name"]}</td><td>{p["kind"]}</td><td>{p["weight"]} g</td>'
            f'<td><form method="post" action="/parts/{p["id"]}/archive"><button id="archive-{p["id"]}">Archive</button></form></td></tr>'
            for p in rows)
        return (f'<html><body><h1>Parts</h1><form method="get" id="f"><select id="kind" name="kind">{opts}</select>'
                f'<select id="order" name="order"><option value="w_up">Weight: Low to High</option></select>'
                f'<button id="apply">Apply</button></form>'
                f'<table><thead><tr><th>Name</th><th>Kind</th><th>Weight</th><th></th></tr></thead><tbody>{body}</tbody></table>'
                f'</body></html>')

    @app.get("/limited", response_class=HTMLResponse)
    def limited():
        return '<html><body><label for="code">Code</label><input id="code" maxlength="4"></body></html>'

    @app.post("/parts/{pid}/archive")
    def archive(pid: int):
        if not defective:
            archived.add(pid)
        return RedirectResponse("/parts?msg=archived", status_code=303)

    return app


@pytest.fixture
def parts_server():
    servers = []

    def start(defective):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(parts_app(defective), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        servers.append(server)
        return f"http://127.0.0.1:{port}"

    yield start
    for s in servers:
        s.should_exit = True


def _run(base, steps):
    return asyncio.run(BrowserExecutor().execute_steps("TC-X", "x", steps, initial_url=base + "/parts"))


def _steps(base, *tail):
    return [step(step_number=1, action=ActionType.NAVIGATE, value=base + "/parts")] + [
        s.model_copy(update={"step_number": i + 2}) for i, s in enumerate(tail)]


@pytest.mark.parametrize("defective,expected", [(True, ExecutionStatus.FAILED), (False, ExecutionStatus.PASSED)])
def test_record_changed_oracle_live(parts_server, defective, expected):
    base = parts_server(defective)
    res = _run(base, _steps(base, step(
        step_number=0, action=ActionType.CLICK, selector="#archive-2",
        assertion=check(assertion_type=AssertionType.RECORD_CHANGED, target_selector="#archive-2"))))
    assert res.status == expected, res.failure_reason


@pytest.mark.parametrize("defective,expected", [(True, ExecutionStatus.FAILED), (False, ExecutionStatus.PASSED)])
def test_filter_and_sort_oracles_live(parts_server, defective, expected):
    import json
    base = parts_server(defective)
    flt = _run(base, _steps(base,
        step(step_number=0, action=ActionType.SELECT, selector="#kind", value="Nut"),
        step(step_number=0, action=ActionType.CLICK, selector="#apply"),
        step(step_number=0, action=ActionType.OBSERVE_STATE, assertion=check(
            assertion_type=AssertionType.LIST_ALL_CONTAIN,
            expected_value=json.dumps({"value": "Nut", "domain": ["Bolt", "Nut", "Washer"]})))))
    srt = _run(base, _steps(base,
        step(step_number=0, action=ActionType.SELECT, selector="#order", value="w_up"),
        step(step_number=0, action=ActionType.CLICK, selector="#apply"),
        step(step_number=0, action=ActionType.OBSERVE_STATE, assertion=check(
            assertion_type=AssertionType.LIST_SORTED,
            expected_value=json.dumps({"key": "number", "any_direction": True, "column_hint": "weight"})))))
    assert flt.status == expected, flt.failure_reason
    assert srt.status == expected, srt.failure_reason


def test_placeholder_option_is_not_recorded_as_a_value(parts_server):
    from app.investigation_agent.browser_session import StatefulBrowserSession
    base = parts_server(False)
    session = StatefulBrowserSession(headless=True)
    session.start()
    try:
        session.navigate(base + "/parts")
        obs = session.capture_observation("t")
        kind = next(c for c in obs.interactive_controls if c.selector == "#kind")
        assert kind.declared_constraints["select_options"] == ["Bolt", "Nut", "Washer"]
        assert kind.declared_constraints["select_option_labels"] == ["Bolt", "Nut", "Washer"]
    finally:
        session.close()


# ── RC-10: setup (prerequisite) failures are not verdicts on the behaviour under test ───────────────

def test_planner_marks_entry_navigation_and_click_prerequisites_as_setup():
    from test_multi_website_generalization import shop_report
    cases, _ = plan(shop_report())
    # Cases that reach a protected page as a precondition (login journeys test the login itself and are excluded).
    authed = [c for c in cases if c.steps and c.steps[0].requires_login]
    assert authed
    for c in authed:
        entry = c.steps[0]                                     # login (if needed) happens inside this setup step
        assert entry.action == ActionType.NAVIGATE and entry.setup and entry.login_url
        assert not any("${CREDENTIAL" in (s.value or "") for s in c.steps)
        assert not c.steps[-1].setup                           # the behaviour under test is not setup


def test_setup_failure_is_blocked_with_evidence(parts_server):
    base = parts_server(False)
    res = _run(base, _steps(base,
        step(step_number=0, action=ActionType.CLICK, selector="#apply", setup=True,
             assertion=check(assertion_type=AssertionType.URL_CONTAINS, expected_value="/dashboard")),
        step(step_number=0, action=ActionType.OBSERVE_STATE, assertion=check(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Parts"))))
    assert res.status == ExecutionStatus.BLOCKED and res.failure_reason.startswith("SETUP_FAILED")
    ev = res.evidence_retained["setup_failure"]
    assert ev["step"] == 2 and ev["underlying_status"] == "FAILED" and "/parts" in ev["url"]


def test_setup_fill_that_does_not_keep_its_value_is_blocked(parts_server):
    base = parts_server(False)
    res = _run(base, [step(step_number=1, action=ActionType.NAVIGATE, value=base + "/limited"),
                      step(step_number=2, action=ActionType.FILL, selector="#code", value="ABCDEFGH", setup=True),
                      step(step_number=3, action=ActionType.OBSERVE_STATE,
                           assertion=check(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Code"))])
    assert res.status == ExecutionStatus.BLOCKED
    assert res.evidence_retained["setup_failure"]["underlying_status"] == "AUTOMATION_ERROR"
    assert "ABCDEFGH" not in res.failure_reason          # lengths only, never the value


def test_blocked_initial_result_is_setup_failed_and_not_retried():
    from app.bugs.investigator import BugInvestigator

    class Runner:
        async def run_test(self, tc, url):
            raise AssertionError("must not retry a setup failure")

    blocked = ExecutionResult(test_id="TC-R", test_name="r", status=ExecutionStatus.BLOCKED, total_steps=2, steps_completed=1,
                              failure_step=1, failure_reason="SETUP_FAILED: step 1")
    report = asyncio.run(BugInvestigator(runner=Runner()).investigate_failure(blocked, _failing_case(), ORIGIN))
    assert report.classification == "BLOCKED" and len(report.attempts) == 1


def test_setup_failed_and_infrastructure_retries_neither_confirm_nor_refute():
    blocked = ExecutionResult(test_id="TC-R", test_name="r", status=ExecutionStatus.BLOCKED, total_steps=2, steps_completed=1,
                              failure_step=1, failure_reason="SETUP_FAILED: step 1",
                              evidence_retained={"setup_failure": {"step": 1}})
    report = _investigate([blocked, _result(ExecutionStatus.LOCATOR_ERROR), _result(ExecutionStatus.FAILED)])
    assert report.classification == "LIKELY_DEFECT" and report.reproduction_rate == "2/2 runs failed"
    assert [a["counted"] for a in report.attempts] == [True, False, False, True]
    assert report.attempts[1]["setup_failure"] == {"step": 1}


def test_convention_level_sort_check_requires_an_unambiguous_key():
    no_headers = {"texts": ["Bolt 3 pack $5.00", "Nut 10 pack $2.00"], "values": [3.0, 10.0], "fields": [[], []]}
    spec = {"key": "number", "any_direction": True, "column_hint": "price"}
    # One amount per record: evaluable without headers
    assert list_oracles.evaluate_sorted(no_headers, spec)[0] == "pass"
    two_amounts = {"texts": ["Bolt $5.00 was $7.00", "Nut $2.00"], "values": [5.0, 2.0], "fields": [[], []]}
    assert list_oracles.evaluate_sorted(two_amounts, spec)[0] == "not_evaluable"
    text_key = {"texts": ["b", "a"], "values": ["b", "a"], "fields": [[], []]}
    assert list_oracles.evaluate_sorted(text_key, {"key": "text", "any_direction": True})[0] == "not_evaluable"
    # The inference-level direction check keeps its record-text fallback
    assert list_oracles.evaluate_sorted(text_key, {"key": "text", "direction": "asc"})[0] == "fail"


def test_commands_whose_effect_is_elsewhere_are_not_record_commands():
    from app.knowledge.qa_retrieval import is_record_command_form
    form = lambda action, submit: SimpleNamespace(method="POST", fields=[], action=action, submit_selector=submit)
    assert is_record_command_form(form("/orders/12/cancel", "#cancel-12"))
    assert not is_record_command_form(form("/invites/12/resend", "#resend-12"))
    assert not is_record_command_form(form("/docs/12/duplicate", "#duplicate-12"))
    assert not is_record_command_form(form("/orders/cancel", "#cancel"))          # not one record
    assert not is_record_command_form(SimpleNamespace(method="POST", action="/orders/12/note", submit_selector="#n",
                                                      fields=[SimpleNamespace(field_type="text")]))
