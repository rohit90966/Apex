"""
Tests for Phase 3: Oracles, Comparator, Probe Library, and Bug Leads.
Validates:
- Always-on oracles detection of 5xx errors, exceptions, and text anomalies.
- Comparator prediction verification and form submission classification (ACCEPTED, REJECTED, UNCERTAIN).
- Probe execution against clean_app producing ZERO HIGH or MEDIUM bug leads.
- Probe execution against demo_app (Fixture 1) detecting planted bypasses without hardcoded IDs.
"""

import pytest
import uvicorn
import threading
import time
from app.investigation_agent.models import (
    UIStateObservation,
    NetworkEvent,
    ConsoleOrPageError,
    ProbeResult,
    StateDiff,
    Prediction,
    ComparatorVerdict,
    FormSubmitOutcome,
    OracleType,
    OracleStrength,
)
from app.investigation_agent.oracles import evaluate_always_on_oracles
from app.investigation_agent.comparator import (
    compare_prediction_with_diff,
    classify_form_submission,
)
from app.investigation_agent.leads import BugLeadManager
from app.investigation_agent.probes.forms import generate_form_probes
from app.investigation_agent.probe_runner import ProbeRunner
from app.investigation_agent.browser_session import StatefulBrowserSession
from tests.fixtures.clean_app import clean_app
from demo_app.main import app as fixture1_app


@pytest.fixture(scope="module")
def clean_server():
    config = uvicorn.Config(clean_app, host="127.0.0.1", port=8989, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.0)
    yield "http://127.0.0.1:8989"
    server.should_exit = True


@pytest.fixture(scope="module")
def fixture1_server():
    config = uvicorn.Config(fixture1_app, host="127.0.0.1", port=8990, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.0)
    yield "http://127.0.0.1:8990"
    server.should_exit = True


def test_always_on_oracles_detection():
    # 1. 500 error detection
    res_500 = ProbeResult(
        script_id="test_probe_500",
        status="SUCCESS",
        network_events=[NetworkEvent(seq=1, method="POST", url="http://127.0.0.1/api", resource_type="xhr", status=500, ok=False, response_snippet="Database Crash")],
    )
    findings = evaluate_always_on_oracles(res_500)
    assert any(f.oracle_type == OracleType.TECHNICAL and f.strength == OracleStrength.HIGH and "500" in f.title for f in findings)

    # 2. Console exception detection
    res_exc = ProbeResult(
        script_id="test_probe_exc",
        status="SUCCESS",
        console_errors=[ConsoleOrPageError(seq=1, error_type="pageerror", message="Uncaught TypeError: cannot read null")],
    )
    findings_exc = evaluate_always_on_oracles(res_exc)
    assert any("TypeError" in f.description for f in findings_exc)


def test_comparator_and_form_classification():
    diff_ok = StateDiff(route_changed=True, class_changed=True)
    before_obs = UIStateObservation(state_id="st_before", state_class_id="cls_1", url="http://127.0.0.1/", normalized_route="", page_title="T")
    after_obs = UIStateObservation(state_id="st_after", state_class_id="cls_2", url="http://127.0.0.1/dash", normalized_route="dash", page_title="D")
    net_ok = [NetworkEvent(seq=1, method="POST", url="http://127.0.0.1/submit", resource_type="document", status=200, ok=True)]

    # Matched prediction
    pred = Prediction(expect="Route change to dashboard", signals=["route_change", "no_server_error"])
    comp = compare_prediction_with_diff(pred, diff_ok, before_obs, after_obs, net_ok)
    assert comp.verdict == ComparatorVerdict.MATCHED

    # Contradicted prediction (expected status 4xx, got status 200)
    pred_contradicted = Prediction(expect="Rejection error", signals=["status_4xx", "alert_present"])
    comp_contra = compare_prediction_with_diff(pred_contradicted, diff_ok, before_obs, after_obs, net_ok)
    assert comp_contra.verdict == ComparatorVerdict.CONTRADICTED

    # Form classification: ACCEPTED
    outcome_acc = classify_form_submission(diff_ok, before_obs, after_obs, net_ok)
    assert outcome_acc == FormSubmitOutcome.ACCEPTED

    # Form classification: REJECTED (due to alert or 4xx)
    diff_err = StateDiff(alerts_added=["Invalid password length"])
    outcome_rej = classify_form_submission(diff_err, before_obs, after_obs, [])
    assert outcome_rej == FormSubmitOutcome.REJECTED


def test_probes_on_clean_app_produce_zero_leads(clean_server):
    """
    On a properly validated application (clean_app), automated probes must produce
    ZERO HIGH or MEDIUM bug leads (zero false positives).
    """
    session = StatefulBrowserSession(headless=True)
    lead_manager = BugLeadManager()
    runner = ProbeRunner()

    try:
        session.start()

        for route in ("/login", "/register"):
            target_url = f"{clean_server}{route}"
            session.navigate(target_url)
            obs = session.capture_observation("clean_test")
            form_probes = generate_form_probes(obs)

            for script, prediction, meta in form_probes:
                # Reset to fresh page before each probe
                session.navigate(target_url)
                res = runner.run_probe(session=session, script=script)
                outcome = classify_form_submission(res.diff, res.before_obs, res.after_obs, res.network_events)
                comp = compare_prediction_with_diff(prediction, res.diff, res.before_obs, res.after_obs, res.network_events)

                if meta.get("kind") in ("constraint_bypass_minlength", "constraint_bypass_checkbox"):
                    lead_manager.create_lead_from_form_outcome(
                        outcome=outcome,
                        expected_outcome=meta["expected_outcome"],
                        rule_description=meta["rule"],
                        probe_family="forms",
                        question_id=script.name,
                        state_class=obs.state_class_id,
                        target_desc=meta.get("field", "form"),
                        oracle_type=OracleType.DECLARED,
                        strength=OracleStrength.HIGH,
                        result=res,
                    )
                elif meta.get("kind") == "mismatch":
                    lead_manager.create_lead_from_form_outcome(
                        outcome=outcome,
                        expected_outcome=meta["expected_outcome"],
                        rule_description=meta["rule"],
                        probe_family="forms",
                        question_id=script.name,
                        state_class=obs.state_class_id,
                        target_desc=meta.get("field", "form"),
                        oracle_type=OracleType.CONVENTION,
                        strength=OracleStrength.MEDIUM,
                        result=res,
                    )
                elif meta.get("kind") == "boundary_password_length":
                    if comp.verdict == ComparatorVerdict.CONTRADICTED:
                        lead_manager.create_lead_from_form_outcome(
                            outcome=FormSubmitOutcome.ACCEPTED,
                            expected_outcome=meta["expected_outcome"],
                            rule_description=meta["rule"],
                            probe_family="forms",
                            question_id=script.name,
                            state_class=obs.state_class_id,
                            target_desc=meta.get("field", "form"),
                            oracle_type=OracleType.CONVENTION,
                            strength=OracleStrength.MEDIUM,
                            result=res,
                        )

        high_or_med_leads = [
            l for l in lead_manager.get_leads()
            if l.strength in (OracleStrength.HIGH, OracleStrength.MEDIUM)
        ]
        assert len(high_or_med_leads) == 0, f"Clean app produced false positive bug leads: {[l.title for l in high_or_med_leads]}"

    finally:
        session.close()


def test_probes_on_fixture1_detect_planted_bugs(fixture1_server):
    """
    On demo_app (Fixture 1), generic probes must detect planted bypasses:
    - Minimum password length bypass on login
    - Terms acceptance bypass on registration
    - Confirm password mismatch bypass on registration
    """
    session = StatefulBrowserSession(headless=True)
    lead_manager = BugLeadManager()
    runner = ProbeRunner()

    try:
        session.start()

        # 1. Test Login (Planted Bug: short password accepted without validation)
        login_url = f"{fixture1_server}/login"
        session.navigate(login_url)
        obs_login = session.capture_observation("f1_login")
        login_probes = generate_form_probes(obs_login)

        for script, prediction, meta in login_probes:
            session.navigate(login_url)
            res = runner.run_probe(session=session, script=script)
            outcome = classify_form_submission(res.diff, res.before_obs, res.after_obs, res.network_events)
            comp = compare_prediction_with_diff(prediction, res.diff, res.before_obs, res.after_obs, res.network_events)

            if meta.get("kind") == "boundary_password_length":
                if comp.verdict == ComparatorVerdict.CONTRADICTED:
                    lead_manager.create_lead_from_form_outcome(
                        outcome=FormSubmitOutcome.ACCEPTED,
                        expected_outcome=meta["expected_outcome"],
                        rule_description=meta["rule"],
                        probe_family="forms",
                        question_id=script.name,
                        state_class=obs_login.state_class_id,
                        target_desc=meta.get("field", "form"),
                        oracle_type=OracleType.CONVENTION,
                        strength=OracleStrength.MEDIUM,
                        result=res,
                    )

        # 2. Test Registration (Planted Bugs: terms bypass and mismatch bypass)
        reg_url = f"{fixture1_server}/registration"
        session.navigate(reg_url)
        obs_reg = session.capture_observation("f1_reg")
        reg_probes = generate_form_probes(obs_reg)

        for script, prediction, meta in reg_probes:
            session.navigate(reg_url)
            res = runner.run_probe(session=session, script=script)
            outcome = classify_form_submission(res.diff, res.before_obs, res.after_obs, res.network_events)

            if meta.get("kind") == "constraint_bypass_checkbox":
                lead_manager.create_lead_from_form_outcome(
                    outcome=outcome,
                    expected_outcome=meta["expected_outcome"],
                    rule_description=meta["rule"],
                    probe_family="forms",
                    question_id=script.name,
                    state_class=obs_reg.state_class_id,
                    target_desc=meta.get("field", "form"),
                    oracle_type=OracleType.DECLARED,
                    strength=OracleStrength.HIGH,
                    result=res,
                )
            elif meta.get("kind") == "mismatch":
                lead_manager.create_lead_from_form_outcome(
                    outcome=outcome,
                    expected_outcome=meta["expected_outcome"],
                    rule_description=meta["rule"],
                    probe_family="forms",
                    question_id=script.name,
                    state_class=obs_reg.state_class_id,
                    target_desc=meta.get("field", "form"),
                    oracle_type=OracleType.CONVENTION,
                    strength=OracleStrength.MEDIUM,
                    result=res,
                )

        leads = lead_manager.get_leads()
        lead_titles = [l.title for l in leads]

        # Verify planted bypasses were detected
        assert any("password" in t.lower() or "length" in t.lower() for t in lead_titles), f"Failed to detect password length bypass: {lead_titles}"
        assert any("term" in t.lower() or "checkbox" in t.lower() for t in lead_titles), f"Failed to detect terms bypass: {lead_titles}"
        assert any("match" in t.lower() or "confirm" in t.lower() for t in lead_titles), f"Failed to detect password mismatch bypass: {lead_titles}"

    finally:
        session.close()
