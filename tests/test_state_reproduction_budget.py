"""
Regression tests for browser-state preconditions, target-aware reproduction, mutation-budget accounting,
finding grouping and execution-status semantics (see reports/root_cause_matrix.md, "Repair round").

Synthetic fixture "Ledger Desk" (not any benchmark site): public item pages sharing one template with a query
string that changes state, a protected area behind a login form whose selectors vary by variant, and a JS
total that renders NaN when the fixture is defective. Everything runs through production code paths
(InvestigationOrchestrator.node_execute_probe, BugReproducer, ProbeRunner, SafetyGovernor, build_findings).
"""

import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.investigation_agent.browser_actions import _urls_compatible
from app.investigation_agent.governor import SafetyGovernor
from app.investigation_agent.models import BugLead, OracleStrength, OracleType, ProbeScript, ProbeStep

PASSWORD = "ledger-pass-1"

LOGIN_VARIANTS = {
    # A "#user-name" style form and one with entirely different selectors and submit control.
    "underscore": ('<input id="user-name" name="user-name"><input id="password" name="password" type="password">'
                   '<button id="login-button" type="submit">Login</button>'),
    "custom": ('<input id="acct-id" name="email"><input id="acct-secret" name="secret" type="password">'
               '<button type="submit">Enter</button>'),
}


def ledger_app(variant="custom", defective=True) -> FastAPI:
    app = FastAPI()
    total_js = ("t.textContent = 'Total: $' + (10 * window.RATE).toFixed(2);" if defective
                else "t.textContent = 'Total: $12.00';")

    def item_page(body):
        return HTMLResponse(f"<html><body><main><h1>Ledger Desk</h1>{body}"
                            f"<button id='calc'>Recalculate</button><p id='total'>Total: $10.00</p>"
                            f"<script>document.getElementById('calc').onclick = () => {{ const t = document.getElementById('total'); {total_js} }};</script>"
                            f"</main></body></html>")

    @app.get("/", response_class=HTMLResponse)
    def home():
        return "<html><body><main><h1>Ledger Desk</h1><a href='/items/1'>Item 1</a> <a href='/signin'>Sign in</a></main></body></html>"

    @app.get("/items/{iid}", response_class=HTMLResponse)
    def item(iid: int, mode: str = "summary"):
        return item_page(f"<h2>Item {iid} ({mode})</h2>")

    @app.get("/signin", response_class=HTMLResponse)
    def signin_page(error: str = ""):
        alert = '<div role="alert">Wrong credentials</div>' if error else ""
        return f"<html><body><main><h1>Sign in</h1>{alert}<form method='post' action='/signin'>{LOGIN_VARIANTS[variant]}</form></main></body></html>"

    @app.post("/signin")
    async def signin(request: Request):
        form = await request.form()
        user = form.get("user-name") or form.get("email")
        pwd = form.get("password") or form.get("secret")
        if user == "auditor" and pwd == PASSWORD:
            resp = RedirectResponse("/reports", status_code=303)
            resp.set_cookie("ledger", "ok")
            return resp
        return RedirectResponse("/signin?error=1", status_code=303)

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request):
        if request.cookies.get("ledger") != "ok":
            return RedirectResponse("/signin", status_code=303)
        return "<html><body><main><h1>Reports</h1><a href='/reports/7'>Q3</a> <a href='/logout'>Log out</a></main></body></html>"

    @app.get("/reports/{rid}", response_class=HTMLResponse)
    def report(rid: int, request: Request):
        if request.cookies.get("ledger") != "ok":
            return RedirectResponse("/signin", status_code=303)
        return item_page(f"<h2>Report {rid}</h2><a href='/logout'>Log out</a>")

    @app.get("/notes", response_class=HTMLResponse)
    def notes():
        return "<html><body><main><h1>Notes</h1><form><input id='note' name='note'></form></main></body></html>"

    return app


@pytest.fixture
def ledger():
    servers = []

    def start(**kw):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(ledger_app(**kw), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        servers.append(server)
        return f"http://127.0.0.1:{port}"

    yield start
    for s in servers:
        s.should_exit = True


# ── URL / state identity ────────────────────────────────────────────────────────────────────────────

def test_page_identity_includes_path_and_query_exactly():
    assert _urls_compatible("http://h/events/2?breakdown=1", "http://h/events/2?breakdown=1")
    assert not _urls_compatible("http://h/events/3?breakdown=1", "http://h/events/2?breakdown=1")
    assert not _urls_compatible("http://h/events/1", "/events/10")           # no substring matches
    assert not _urls_compatible("http://h/items/1?mode=a", "http://h/items/1?mode=b")
    assert _urls_compatible("http://h/a/?y=2&x=1#top", "http://h/a?x=1&y=2")  # order/slash/fragment irrelevant
    assert _urls_compatible("http://h/events/2", "/events/2")


# ── Probe preconditions through the production workflow node ───────────────────────────────────────

@pytest.fixture
def orchestrator():
    from app.investigation_agent.workflow import InvestigationOrchestrator
    orch = InvestigationOrchestrator(headless=True)
    orch.session.start()
    yield orch
    orch.session.close()


def _probe(required_url, selector="#calc"):
    from app.investigation_agent.agenda import InvestigationQuestion
    script = ProbeScript(name="p", family="controls", steps=[ProbeStep(action_type="CLICK_CONTROL", raw_target=selector)])
    q = InvestigationQuestion(id=f"q_{selector}_{required_url[-12:]}", target_state_class="*", hypothesis="h",
                              probe_family="controls", target_element=selector, metadata={"source_url": required_url})
    return {"current_probe": script, "current_question": q, "actions_attempted": 0}


def test_probe_runs_when_current_state_matches(ledger, orchestrator):
    base = ledger()
    orchestrator.session.navigate(base + "/items/1")
    out = orchestrator.node_execute_probe(_probe(base + "/items/1"))
    assert out["actions_attempted"] == 1 and out["current_probe_result"].status == "SUCCESS"


def test_probe_recovers_by_navigating_when_route_or_query_differs(ledger, orchestrator):
    base = ledger()
    orchestrator.session.navigate(base + "/items/2")                  # same template, wrong instance
    out = orchestrator.node_execute_probe(_probe(base + "/items/1"))
    assert out["current_probe_result"].status == "SUCCESS" and orchestrator.session.page.url.endswith("/items/1")
    orchestrator.session.navigate(base + "/items/1?mode=summary")      # same path, different state
    out = orchestrator.node_execute_probe(_probe(base + "/items/1?mode=detail"))
    assert out["current_probe_result"].status == "SUCCESS" and "mode=detail" in orchestrator.session.page.url


def test_probe_is_blocked_when_state_cannot_be_reached_safely(ledger, orchestrator):
    base = ledger()
    orchestrator.session.navigate(base + "/items/1")
    out = orchestrator.node_execute_probe(_probe(base + "/reports/7"))  # protected: redirects to /signin
    res = out["current_probe_result"]
    assert res.status == "FAILED" and "STATE_NOT_REACHED" in res.error_message
    assert "actions_attempted" not in out                               # not an attempted action
    assert orchestrator._unexecuted_probes[-1]["reason"] == "STATE_NOT_REACHED"


def test_stale_selector_is_not_acted_on_and_creates_no_lead(ledger, orchestrator):
    base = ledger()
    orchestrator.session.navigate(base + "/items/2")
    state = _probe(base + "/items/1", selector="#gone")
    out = orchestrator.node_execute_probe(state)
    assert "TARGET_NOT_PRESENT" in out["current_probe_result"].error_message and "actions_attempted" not in out
    before = len(orchestrator.lead_manager.get_leads())
    orchestrator.node_perceive_after_and_compare({**state, **out})
    assert len(orchestrator.lead_manager.get_leads()) == before          # no defect from an unexecuted probe
    assert [u["question_id"] for u in orchestrator._unexecuted_probes].count(state["current_question"].id) == 1


def test_blocked_probes_consume_neither_probe_nor_mutation_budget(ledger, orchestrator):
    base = ledger()
    orchestrator.session.navigate(base + "/items/1")
    for _ in range(5):
        out = orchestrator.node_execute_probe(_probe(base + "/items/1", selector="#gone"))
        assert "actions_attempted" not in out
    assert orchestrator.runner.governor.mutations_executed == 0


# ── Mutation budget accounting ──────────────────────────────────────────────────────────────────────

def _fill(selector, value="x"):
    return ProbeScript(name="f", family="forms", steps=[ProbeStep(action_type="FILL", raw_target=selector, resolved_value=value)])


def test_governor_approval_never_consumes_budget_and_commit_never_exceeds_it():
    gov = SafetyGovernor(mutation_budget=2)
    url = "http://127.0.0.1:9/form"
    for _ in range(5):
        assert gov.can_execute("FILL", "#a", url)[0]                     # approvals are free
    assert gov.mutations_executed == 0
    assert gov.record_mutation() and gov.record_mutation()
    assert not gov.record_mutation()                                     # at the limit: refused, not counted
    assert gov.mutations_executed == 2 and gov.mutations_blocked == 1
    allowed, reason = gov.can_execute("FILL", "#a", url)
    assert not allowed and "budget exhausted (2/2)" in reason.lower()
    assert gov.budget_status()["executed"] == 2


def test_probe_runner_counts_each_executed_mutation_once(ledger):
    from app.investigation_agent.browser_session import StatefulBrowserSession
    from app.investigation_agent.probe_runner import ProbeRunner
    base = ledger()
    session = StatefulBrowserSession(headless=True)
    session.start()
    try:
        session.navigate(base + "/notes")
        runner = ProbeRunner(governor=SafetyGovernor(mutation_budget=2))
        assert runner.run_probe(session, _fill("#note")).status == "SUCCESS"
        assert runner.governor.mutations_executed == 1                   # success: exactly one
        assert runner.run_probe(session, _fill("#absent")).status == "FAILED"
        assert runner.governor.mutations_executed == 1                   # failed action: not a mutation
        assert runner.run_probe(session, _fill("#note")).status == "SUCCESS"   # retry: counted once
        assert runner.governor.mutations_executed == 2
        blocked = runner.run_probe(session, _fill("#note"))
        assert blocked.status == "SAFETY_BLOCKED" and "budget exhausted" in blocked.error_message.lower()
        assert runner.governor.mutations_executed == 2                   # never exceeds the budget
    finally:
        session.close()


# ── Target-aware reproduction ───────────────────────────────────────────────────────────────────────

def _lead(base, protected=True, title="Text Anomaly: 'NaN'"):
    page = f"{base}/reports/7" if protected else f"{base}/items/3"
    return BugLead(title=title, oracle_type=OracleType.TECHNICAL, strength=OracleStrength.MEDIUM, probe_family="controls",
                   question_id="q", source_state_id="s_page", source_url=page, target_desc="#calc",
                   steps=[{"action_type": "CLICK_CONTROL", "raw_target": "#calc"}],
                   actual={"text": "Detected anomaly 'NaN' rendered in visible text after interaction."})


def _graph(base, protected=True):
    st = lambda sid, path: SimpleNamespace(state_id=sid, url=base + path)
    states = {"s_signin": st("s_signin", "/signin"), "s_reports": st("s_reports", "/reports"),
              "s_page": st("s_page", "/reports/7" if protected else "/items/3")}
    tr = lambda src, dst, act, tgt="": SimpleNamespace(source_state_id=src, destination_state_id=dst, action_type=act,
                                                       target_element_desc=tgt)
    transitions = [tr("s_signin", "s_reports", "AUTHENTICATE", "login_form")]
    transitions.append(tr("s_reports", "s_page", "NAVIGATE_LINK", "a[href='/reports/7']") if protected
                       else tr("s_entry", "s_page", "NAVIGATE_LINK", "/items/3"))
    return transitions, states


def _reproduce(base, creds, protected=True, **kw):
    from app.investigation_agent.reproducer import BugReproducer
    transitions, states = _graph(base, protected)
    return BugReproducer(headless=True).reproduce_lead(base + "/", _lead(base, protected), transitions=transitions,
                                                       states=states, auth_credentials=creds)


GOOD = {"username": "auditor", "password": PASSWORD}


@pytest.mark.parametrize("variant", ["underscore", "custom"])
def test_reproduction_authenticates_through_the_targets_own_login_form(ledger, variant):
    base = ledger(variant=variant, defective=True)
    lead = _reproduce(base, GOOD)
    assert lead.status == "CONFIRMED", lead.reproduction_failure_reason
    assert lead.setup_steps[0]["action_type"] == "AUTHENTICATE"
    assert "password" not in str(lead.setup_steps).lower() and PASSWORD not in str(lead.setup_steps)
    assert "#user-name" not in str(lead.setup_steps)                    # no fixed selectors


def test_public_page_needs_no_login(ledger):
    base = ledger(variant="custom", defective=True)
    lead = _reproduce(base, None, protected=False)
    assert lead.status == "CONFIRMED" and not lead.setup_steps


def test_valid_setup_with_contradicting_evidence_disproves(ledger):
    base = ledger(variant="custom", defective=False)
    lead = _reproduce(base, GOOD)
    assert lead.status == "REJECTED" and lead.reproduction_outcome.value == "NOT_REPRODUCED"


@pytest.mark.parametrize("creds,reason", [({"username": "auditor", "password": "wrong"}, "Authentication setup failed"),
                                          (None, "no credentials")])
def test_setup_failure_is_unresolved_not_disproven(ledger, monkeypatch, creds, reason):
    for k in ("VERITYQA_TEST_USERNAME", "VERITYQA_TEST_PASSWORD", "VERITYQA_TEST_CREDENTIALS_ORIGIN", "TARGET_USERNAME", "TARGET_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    base = ledger(variant="custom", defective=True)
    lead = _reproduce(base, creds)
    assert lead.status == "SETUP_BLOCKED", (lead.status, lead.reproduction_failure_reason)
    assert reason in lead.reproduction_failure_reason and "remains unresolved" in lead.reproduction_failure_reason
    assert "current=" in lead.reproduction_failure_reason                # where it stopped is recorded


def test_absent_in_page_prerequisite_is_unresolved(ledger):
    from app.investigation_agent.reproducer import BugReproducer
    base = ledger(variant="custom", defective=True)
    lead = _lead(base, protected=False)
    lead.setup_steps = [{"action_type": "CLICK_CONTROL", "raw_target": "#not-there", "is_prerequisite": True}]
    lead = BugReproducer(headless=True).reproduce_lead(base + "/", lead)
    assert lead.status == "SETUP_BLOCKED" and "#not-there" in lead.reproduction_failure_reason


# ── Authentication verification ─────────────────────────────────────────────────────────────────────

def test_rejected_login_is_not_verified_even_with_page_landmarks(ledger):
    from app.investigation_agent.authenticator import AuthenticationManager
    from app.investigation_agent.browser_session import StatefulBrowserSession
    base = ledger(variant="custom")
    session = StatefulBrowserSession(headless=True)
    session.start()
    try:
        session.navigate(base + "/signin")                              # page has <main>: not evidence of login
        ok, why, _ = AuthenticationManager.attempt_authentication(session, {"username": "auditor", "password": "nope"})
        assert not ok and "login error" in why.lower()
        session.navigate(base + "/signin")
        ok, why, _ = AuthenticationManager.attempt_authentication(session, GOOD)
        assert ok and "login form dismissed" in why
    finally:
        session.close()


def test_registry_credentials_are_never_applied_to_local_ports():
    from app.investigation_agent.authenticator import AuthenticationManager
    for url in ("http://127.0.0.1:8001/", "http://localhost:8001/"):
        assert AuthenticationManager.discover_credentials(url, None) is None


# ── Grouped findings keep each observation ──────────────────────────────────────────────────────────

def test_repeated_symptom_is_grouped_with_individual_evidence():
    from app.findings.registry import build_findings
    lead = lambda lid, n, status: {"id": lid, "status": status, "title": "Text Anomaly: 'NaN'", "probe_family": "controls",
                                   "source_url": f"http://h/events/{n}", "target_desc": f"/events/{n}?breakdown=1",
                                   "expected": {"text": "clean"}, "actual": {"text": "NaN"}, "evidence_ids": [f"ev{n}"],
                                   "reproduction_failure_reason": "setup failed" if status != "REJECTED" else "not reproduced"}
    report = {"overview": {"target_url": "http://h/"},
              "unstable_or_rejected_leads": [lead("a", 1, "SETUP_BLOCKED"), lead("b", 2, "SETUP_BLOCKED"), lead("c", 3, "SETUP_BLOCKED"),
                                             lead("d", 4, "REJECTED")]}
    report["unstable_or_rejected_leads"].append({**lead("e", 5, "SETUP_BLOCKED"), "source_url": "http://h/cart",
                                                 "target_desc": "/cart"})               # different page: separate
    out = build_findings(report, [], [], "http://h/")
    groups = {u["route"]: u for u in out["unresolved"]}
    assert set(groups) == {"/events/{id}", "/cart"}
    assert [o["lead_id"] for o in groups["/events/{id}"]["occurrences"]] == ["a", "b", "c"]
    assert [o["evidence_ids"] for o in groups["/events/{id}"]["occurrences"]] == [["ev1"], ["ev2"], ["ev3"]]
    assert [d["occurrences"][0]["lead_id"] for d in out["disproven"]] == ["d"]  # disproven kept separate
    assert out["findings"] == []                                         # nothing unverified becomes a finding


# ── Execution status semantics ──────────────────────────────────────────────────────────────────────

def _exec(base, steps):
    import asyncio
    from app.execution.executor import BrowserExecutor
    return asyncio.run(BrowserExecutor().execute_steps("TC-S", "s", steps, initial_url=base + "/"))


def _step(n, **kw):
    from app.schemas import ActionStep
    return ActionStep(step_number=n, description=kw.pop("description", "step"), **kw)


def _check(atype, value):
    from app.schemas import ExecutionAssertion
    return ExecutionAssertion(assertion_type=atype, expected_value=value, description="check")


def test_setup_failure_is_blocked_with_no_assertions_counted(ledger):
    from app.schemas import ActionType, AssertionType, ExecutionStatus
    base = ledger(defective=True)
    res = _exec(base, [_step(1, action=ActionType.NAVIGATE, value=base + "/items/1", setup=True,
                             assertion=_check(AssertionType.URL_CONTAINS, "/reports")),
                       _step(2, action=ActionType.CLICK, selector="#calc",
                             assertion=_check(AssertionType.TEXT_NOT_VISIBLE, "NaN"))])
    assert res.status == ExecutionStatus.BLOCKED
    assert res.assertions_evaluated == 0 and res.meaningful_assertions_evaluated == 0
    assert res.evidence_retained["setup_failure"]["step"] == 1


def test_real_assertion_failure_after_successful_setup_is_failed(ledger):
    from app.schemas import ActionType, AssertionType, ExecutionStatus
    base = ledger(defective=True)
    res = _exec(base, [_step(1, action=ActionType.NAVIGATE, value=base + "/items/1", setup=True,
                             assertion=_check(AssertionType.URL_CONTAINS, "/items/1")),
                       _step(2, action=ActionType.CLICK, selector="#calc",
                             assertion=_check(AssertionType.TEXT_NOT_VISIBLE, "NaN"))])
    assert res.status == ExecutionStatus.FAILED and res.failure_step == 2
    assert res.assertions_evaluated == 1                                   # only the test's own assertion
    assert res.evidence_retained["setup_checks"] == [{"step": 1, "passed": True, "actual": res.evidence_retained["setup_checks"][0]["actual"]}]


def test_execute_api_summary_counts_each_status_separately(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from app.schemas import ExecutionResult, ExecutionStatus, TestCase
    statuses = [ExecutionStatus.PASSED, ExecutionStatus.FAILED, ExecutionStatus.BLOCKED, ExecutionStatus.BLOCKED,
                ExecutionStatus.LOCATOR_ERROR, ExecutionStatus.INCONCLUSIVE, ExecutionStatus.TEST_DEFINITION_ERROR]

    async def fake_run_all(test_cases, target_url, max_concurrent=1):
        return [ExecutionResult(test_id=tc.test_id, test_name=tc.name, total_steps=1, steps_completed=1, status=st,
                                assertions_evaluated=1 if st in (ExecutionStatus.PASSED, ExecutionStatus.FAILED) else 0)
                for tc, st in zip(test_cases, statuses)]

    monkeypatch.setattr(main.test_runner, "run_all", fake_run_all)
    cases = [TestCase(test_id=f"T{i}", name="n", objective="o", action_description="a", expected_outcome="e",
                      selection_reason="r", confidence=0.5).model_dump(mode="json") for i in range(len(statuses))]
    body = TestClient(main.app).post("/api/tests/execute", json={"target_url": "http://127.0.0.1:9/", "test_cases": cases,
                                                                 "verify_bugs": False}).json()
    assert (body["executed_count"], body["passed_count"], body["failed_count"], body["blocked_count"],
            body["error_count"], body["incomplete_count"], body["invalid_count"], body["assertions_reached_count"],
            body["setup_failures_count"]) == (7, 1, 1, 2, 1, 1, 1, 2, 2)
