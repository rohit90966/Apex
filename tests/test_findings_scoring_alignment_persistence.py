"""
Investigation-finding scoring, confirmed-lead regression tests, requirement alignment, execution-result
persistence, and probe transparency.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.config import get_settings

ORIGIN = "http://127.0.0.1:8001"
APEX_REQUIREMENTS = [
    "Username is required to sign in", "Password is required to sign in", "Password must be at least 6 characters",
    "Full name is required for registration", "Accepting the Terms of Service is required", "Search query is required",
]
SAUCEDEMO_REQUIREMENTS = [
    "Users must authenticate with standard credentials to view inventory",
    "Adding an item to the cart must increment the cart badge count",
    "Cart items must be preserved through the checkout funnel",
    "Checkout step two requires valid first name, last name, and postal code",
]


def _state(sid, route, headings, reached="NAVIGATION"):
    return {"state_id": sid, "url": f"{ORIGIN}{route}", "route": route, "title": headings[0], "headings": headings,
            "http_status": 200, "authenticated": False, "text_snippet": " ".join(headings), "reached_by": reached}


def _field(sel, name, label, ftype="text", **kw):
    return {"selector": sel, "name": name, "field_id": sel.lstrip("#"), "label": label, "field_type": ftype, **kw}


def mismatch_lead(status="CONFIRMED", confirm_css="#confirm-password"):
    """Shape of a lead as produced by the investigation reproducer (form-rule violation)."""
    def fill(css, name, value):
        return {"action_type": "FILL", "target": {"name": name, "css": css}, "raw_target": None, "resolved_value": value}
    return {
        "id": "lead_6c3ba8f2", "title": "Bypass: Confirmation field 'confirm_password' must match 'password'",
        "oracle_type": "CONVENTION", "strength": "HIGH", "probe_family": "forms", "question_id": "q1",
        "source_url": f"{ORIGIN}/registration", "target_desc": "confirm_password", "status": status,
        "steps": [fill("#fullname", "fullname", "Abc123Xy"), fill("#email", "email", "qa@example.test"),
                  fill("#reg-password", "password", "Vqa!run_139x"), fill(confirm_css, "confirm_password", "Vqa!run_139x_diff"),
                  {"action_type": "CHECK", "target": {"name": "terms", "css": "#terms"}},
                  {"action_type": "SUBMIT", "target": None, "raw_target": "form"}],
        "setup_steps": [],
        "expected": {"text": "Submission should be rejected: Reject submission when passwords do not match",
                     "provenance": "form_validation_rule"},
        "actual": {"text": "Form submission accepted (status ACCEPTED) despite violating constraint"},
        "evidence_ids": ["probe_probe_fcc05c05", "st_register"], "confirmation": {"runs": 2, "passed": 2},
    }


def apex_report(requirements, leads=()):
    states = [_state("s0", "/", ["Apex Demo Target System"], "ENTRY"), _state("s1", "/login", ["Sign In to Apex"]),
              _state("s2", "/registration", ["Create an Apex Account"]), _state("s3", "/search", ["Catalog Search"])]
    forms = [
        {"form_id": "login", "form_selector": "form", "action": "/login", "method": "POST", "submit_selector": "#loginBtn",
         "state_id": "s1", "url": f"{ORIGIN}/login", "route": "/login",
         "fields": [_field("#username", "username", "Username or Email", required=True),
                    _field("#password", "password", "Password", "password", required=True)]},
        {"form_id": "register", "form_selector": "form", "action": "/register", "method": "POST", "submit_selector": "#registerBtn",
         "state_id": "s2", "url": f"{ORIGIN}/registration", "route": "/registration",
         "fields": [_field("#fullname", "fullname", "Full Name", required=True),
                    _field("#email", "email", "Work Email", "email", required=True),
                    _field("#reg-password", "password", "Password", "password", required=True),
                    _field("#confirm-password", "confirm_password", "Confirm Password", "password", required=True),
                    _field("#terms", "terms", "I accept the Terms of Service", "checkbox", required=True)]},
        {"form_id": "search", "form_selector": "form", "action": "/search", "method": "GET", "submit_selector": "#searchBtn",
         "state_id": "s3", "url": f"{ORIGIN}/search", "route": "/search",
         "fields": [_field("#q", "q", "Search items", "search", required=True)]},
    ]
    return SimpleNamespace(
        overview=SimpleNamespace(target_url=ORIGIN + "/", investigation_id="inv-apex"),
        target_context={"run_id": "inv-apex", "origin": ORIGIN, "requirements": list(requirements)},
        observed_inventory={"run_id": "inv-apex", "origin": ORIGIN, "provenance": "LIVE_INVESTIGATION", "states": states,
                            "controls": [], "forms": forms, "transitions": [], "auth": {}, "requirements": []},
        inferred_journeys=[], confirmed_bug_leads=list(leads), business_rules_and_hypotheses=[],
    )


def plan(report):
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    planner = KnowledgeGuidedTestPlanner()
    cases, _, _ = planner.plan_test_suite_sync(report, target_count=200)
    return cases, planner.last_diagnostics


# ── A. Evidence-based benchmark scoring ──────────────────────────────────────

def test_confirmed_investigation_lead_counts_as_detection():
    from benchmarks.evaluator import findings_from_investigation, score
    findings = findings_from_investigation(SimpleNamespace(confirmed_bug_leads=[mismatch_lead()]))
    assert findings[0]["derived_values"]["field_selector"] == "#confirm-password"
    s = score("apex_portal", "buggy", findings)
    assert s["detected_defect_ids"] == ["BUG-03"]
    assert s["detected_by_investigation"] == ["BUG-03"] and s["detected_by_execution"] == []
    assert (s["tp"], s["fp"], s["fn"]) == (1, 0, 3)


def test_unconfirmed_leads_are_never_counted():
    from benchmarks.evaluator import findings_from_investigation
    assert findings_from_investigation(SimpleNamespace(confirmed_bug_leads=[mismatch_lead(status="UNSTABLE")])) == []


def test_execution_and_investigation_detections_of_one_defect_count_once():
    from benchmarks.evaluator import findings_from_investigation, score
    execution = {"test_id": "TC-FIELD-07", "source": "execution", "oracle_class": "FIELD_CONSTRAINT",
                 "classification": "CONFIRMED_REPRODUCIBLE", "name": "Password: minlength-1 (5) is rejected", "steps": [],
                 "derived_values": {"field_selector": "#password", "input_class": "TOO_SHORT"}}
    exec_bug03 = {"test_id": "TC-REG-01", "source": "execution", "oracle_class": "DEFECT_REGRESSION",
                  "classification": "CONFIRMED_REPRODUCIBLE", "name": "Regression of confirmed lead", "steps": [],
                  "derived_values": {"field_selector": "#confirm-password"}}
    lead = findings_from_investigation(SimpleNamespace(confirmed_bug_leads=[mismatch_lead()]))
    s = score("apex_portal", "buggy", [execution, exec_bug03] + lead)
    assert s["tp"] == 2 and s["fp"] == 0
    assert s["detected_by_both"] == ["BUG-03"]
    assert s["detected_by_execution"] == ["BUG-01", "BUG-03"]


def test_confirmed_lead_on_defect_free_build_is_a_false_alarm():
    from benchmarks.evaluator import findings_from_investigation, score
    s = score("apex_portal", "baseline", findings_from_investigation(SimpleNamespace(confirmed_bug_leads=[mismatch_lead()])))
    assert s["false_alarms_on_defect_free_build"] == 1
    assert s["false_positives_by_source"]["investigation"] == ["LEAD:lead_6c3ba8f2"]


# ── B. Confirmed lead -> regression test ─────────────────────────────────────

def test_confirmed_form_lead_becomes_grounded_regression_test():
    from app.schemas import ActionType, AssertionType
    cases, diag = plan(apex_report(APEX_REQUIREMENTS, [mismatch_lead()]))
    reg = [tc for tc in cases if tc.oracle_class == "DEFECT_REGRESSION"]
    assert len(reg) == 1
    tc = reg[0]
    assert tc.derived_values["source_lead_id"] == "lead_6c3ba8f2"
    assert "probe_probe_fcc05c05" in tc.evidence_ids
    assert tc.steps[0].value == f"{ORIGIN}/registration"
    assert ("#confirm-password", "Vqa!run_139x_diff") in [(s.selector, s.value) for s in tc.steps if s.action == ActionType.FILL]
    last = tc.steps[-1]
    assert last.selector == "#registerBtn" and last.assertion.assertion_type == AssertionType.VALIDATION_FEEDBACK_SHOWN
    assert last.assertion.expected_source == "INVESTIGATION_CONVENTION"
    assert {"password", "match"} <= set(json.loads(last.assertion.expected_value))
    assert tc.status.value == "PROPOSED"  # not passed or failed until it actually executes


def test_lead_with_unobserved_selectors_is_not_converted():
    cases, diag = plan(apex_report(APEX_REQUIREMENTS, [mismatch_lead(confirm_css="#not-observed")]))
    assert not [tc for tc in cases if tc.oracle_class == "DEFECT_REGRESSION"]
    assert diag["unconverted_leads"][0]["lead_id"] == "lead_6c3ba8f2"


def test_inferred_lead_expectation_is_not_an_established_rule():
    """A failing lead regression is reported as an anomaly, while the lead itself is scored by the evaluator."""
    from app.bugs.investigator import _ESTABLISHED_SOURCES
    assert "INVESTIGATION_CONVENTION" not in _ESTABLISHED_SOURCES


# ── C. Requirement-to-application alignment ─────────────────────────────────

def test_saucedemo_requirements_on_apex_produce_alignment_warning():
    _, diag = plan(apex_report(SAUCEDEMO_REQUIREMENTS))
    al = diag["requirement_alignment"]
    assert al["matched"] == 0 and al["warning"] and "different application" in al["warning"]
    statuses = {r["requirement"]: r["status"] for r in al["requirements"]}
    assert statuses["Adding an item to the cart must increment the cart badge count"] == "UNMATCHED"


def test_apex_requirements_align_with_apex_fields():
    cases, diag = plan(apex_report(APEX_REQUIREMENTS))
    al = diag["requirement_alignment"]
    assert al["matched"] == len(APEX_REQUIREMENTS) and al["warning"] is None
    covered = {(tc.derived_values or {}).get("constraint_detail") for tc in cases}
    assert set(APEX_REQUIREMENTS) <= covered  # every matched requirement produced at least one test


# ── D. Execution-result persistence ─────────────────────────────────────────

@pytest.fixture
def client_with_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.runs import RunStore
    from app.schemas import ExecutionResult, ExecutionStatus

    store = RunStore(str(tmp_path / "runs"))
    monkeypatch.setattr(main, "run_store", store)
    case = {"test_id": "TC-1", "name": "case", "objective": "o", "target_origin": ORIGIN,
            "steps": [{"step_number": 1, "action": "NAVIGATE", "value": ORIGIN + "/", "description": "go"}]}
    run = store.create(ORIGIN + "/", None, [])
    store.mark_running(run.run_id)
    store.complete(run.run_id, report={"run_id": run.run_id}, test_cases=[case])
    seen = {}

    async def fake_run_all(test_cases, target_url, max_concurrent=1):
        seen["ids"] = [tc.test_id for tc in test_cases]
        return [ExecutionResult(test_id="TC-1", test_name="case", status=ExecutionStatus.PASSED, total_steps=1,
                                steps_completed=1, assertions_evaluated=1, meaningful_assertions_evaluated=1)]

    monkeypatch.setattr(main.test_runner, "run_all", fake_run_all)
    return TestClient(main.app), run.run_id, seen, store


def test_execution_by_run_id_is_persisted_and_retrievable(client_with_run):
    client, run_id, seen, _ = client_with_run
    res = client.post("/api/tests/execute", json={"target_url": ORIGIN + "/", "run_id": run_id, "verify_bugs": False})
    assert res.status_code == 200, res.text
    assert seen["ids"] == ["TC-1"] and res.json()["run_id"] == run_id
    detail = client.get(f"/api/runs/{run_id}").json()  # the API the dashboard loads runs from
    assert detail["latest_execution"]["execution_id"] == res.json()["execution_id"]
    assert detail["run"]["execution_count"] == 1


def test_adhoc_execution_without_run_id_still_works_and_is_not_persisted(client_with_run):
    client, run_id, _, _ = client_with_run
    case = {"test_id": "TC-1", "name": "case", "objective": "o",
            "steps": [{"step_number": 1, "action": "NAVIGATE", "value": ORIGIN + "/", "description": "go"}]}
    res = client.post("/api/tests/execute", json={"target_url": ORIGIN + "/", "test_cases": [case], "verify_bugs": False})
    assert res.status_code == 200 and res.json()["run_id"] is None
    assert client.get(f"/api/runs/{run_id}").json()["latest_execution"] is None


def test_persistence_failure_is_reported_not_swallowed(client_with_run, monkeypatch):
    client, run_id, _, store = client_with_run

    def broken(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(store, "save_execution", broken)
    res = client.post("/api/tests/execute", json={"target_url": ORIGIN + "/", "run_id": run_id, "verify_bugs": False})
    assert res.status_code == 500 and "could not be saved" in res.text


def test_dashboard_builds_run_scoped_execute_request():
    script = """
import { buildExecuteRequest } from './app/static/src/runDisplay.mjs';
const withRun = buildExecuteRequest({ run_id: 'inv-1', test_cases: [{ test_id: 'A' }] }, 'http://x/');
const adhoc = buildExecuteRequest({ test_cases: [{ test_id: 'A' }] }, 'http://x/');
console.log(JSON.stringify({ withRun, adhoc }));
"""
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, cwd=str(get_root()))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["withRun"] == {"target_url": "http://x/", "run_id": "inv-1"}  # no test_cases: server runs the saved ones
    assert data["adhoc"] == {"target_url": "http://x/", "test_cases": [{"test_id": "A"}]}


def get_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[1]


# ── E. Probe transparency and login-result states ───────────────────────────

def _obs(sid, route):
    return SimpleNamespace(url=f"{ORIGIN}{route}", page_title=sid, headings=[sid], http_status=200, is_authenticated=False,
                           visible_text_snippet="", validation_error_message=None, validation_error_locator=None,
                           captured_at=0.0, interactive_controls=[], forms=[])


def _tr(src, dst, act):
    return SimpleNamespace(transition_id=f"{src}-{dst}", source_state_id=src, destination_state_id=dst, action_type=act,
                           target_element_desc="#x", is_route_change=False, status="confirmed", observed_effect="", evidence_ids=[])


def test_login_result_rendered_at_login_route_is_not_reproducible_by_navigation():
    from app.intelligence.target_context import build_inventory_from_state
    inv = build_inventory_from_state({
        "target_url": ORIGIN + "/", "auth_verified": True,
        "states": {"form": _obs("form", "/login"), "welcome": _obs("welcome", "/login")},
        "transitions": [_tr("form", "welcome", "AUTHENTICATE")],
    })
    assert inv.state("welcome").reached_by == "FORM_SUBMISSION"
    assert not inv.requires_auth("/login")


def test_unexecuted_probes_are_reported_not_counted_as_coverage():
    from app.investigation_agent.models import UIStateObservation
    from app.investigation_agent.report_builder import InvestigationReportBuilder
    home = UIStateObservation(state_id="s", url=ORIGIN + "/", normalized_route="/", page_title="Home")
    state = {"target_url": ORIGIN + "/", "states": {"s": home}, "transitions": [], "evidence_ledger": [],
             "rules_and_hypotheses": [], "frontier": {},
             "unexecuted_probes": [
                 {"question_id": "q1", "reason": "MUTATION_BUDGET_EXHAUSTED", "target": "#fullname", "detail": "budget"},
                 {"question_id": "q2", "reason": "TARGET_NOT_ON_CURRENT_PAGE", "target": "a[href='/x']", "detail": "pre"}]}
    report = InvestigationReportBuilder.build_report(state)
    cov = report.investigation_coverage_frontier
    assert cov["unexecuted_probes_count"] == 2
    assert cov["unexecuted_probes_by_reason"] == {"MUTATION_BUDGET_EXHAUSTED": 1, "TARGET_NOT_ON_CURRENT_PAGE": 1}
    assert any("Mutation budget exhausted" in lim for lim in report.investigation_limitations)
    assert report.overview.actions_attempted == 0
