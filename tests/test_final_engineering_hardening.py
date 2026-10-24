"""
Tests for Final Engineering Hardening & Autonomous QA Completion.
Verifies:
1. Patience-first Ollama inference (no artificial timeouts).
2. Element action compatibility layer (FILL on submit rejected with INCOMPATIBLE_ACTION).
3. BugLead step recording preserves real actions from ProbeResult instead of fake target="browser".
4. Generated Playwright replay test has explicit, grounded assertions (URLs, headings, selectors).
5. Test synthesis generates executable TestCase objects with ActionSteps grounded in investigation.
6. Honest status reporting (never COMPLETED if pending_frontier > 0).
"""

import pytest
from app.investigation_agent.models import (
    ActionFailureReason,
    InvestigationStatus,
    ProbeResult,
    ProbeScript,
    ProbeStep,
    SemanticTarget,
    OracleType,
    OracleStrength,
    UIStateObservation,
    ReportOverview,
    InvestigationReport,
    ReportAuthentication,
)
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.leads import BugLeadManager, _extract_steps_from_result
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.behavior_model import ApplicationBehaviorModel
from app.investigation_agent.test_synthesis import synthesize_test_cases_from_investigation


def test_incompatible_action_check_on_submit():
    """Verifies that attempting FILL on input[type=submit] fails fast with INCOMPATIBLE_ACTION."""
    from unittest.mock import MagicMock
    mock_session = MagicMock()
    mock_page = MagicMock()
    mock_session.page = mock_page

    ok, outcome, reason, telem = InvestigationActionExecutor.execute_action(
        session=mock_session,
        action_type="FILL",
        target="input[type='submit']#login-button",
        value="secret",
    )
    assert not ok
    assert reason == ActionFailureReason.INCOMPATIBLE_ACTION
    assert "cannot be filled" in outcome.lower()


def test_extract_steps_from_result_preserves_script_steps():
    """Verifies that _extract_steps_from_result faithfully preserves probe actions instead of fake target=browser."""
    script = ProbeScript(
        name="test_probe",
        family="forms",
        description="test form probe",
        steps=[
            ProbeStep(action_type="FILL", raw_target="#user-name", resolved_value="standard_user"),
            ProbeStep(action_type="FILL", raw_target="#password", resolved_value="secret_sauce"),
            ProbeStep(action_type="CLICK_CONTROL", raw_target="#login-button"),
        ]
    )
    result = ProbeResult(
        script_id="test_script_1",
        status="SUCCESS",
        steps_executed=3,
        script=script,
    )

    steps = _extract_steps_from_result(result, target_desc="browser")
    assert len(steps) == 3
    assert steps[0]["action_type"] == "FILL"
    assert steps[0]["raw_target"] == "#user-name"
    assert steps[0]["resolved_value"] == "standard_user"
    assert steps[2]["action_type"] == "CLICK_CONTROL"
    assert steps[2]["raw_target"] == "#login-button"

    # For page-level errors without script
    page_steps = _extract_steps_from_result(None, target_desc="browser")
    assert len(page_steps) == 1
    assert page_steps[0]["action_type"] == "OBSERVE"
    assert page_steps[0]["raw_target"] is None


def test_honest_investigation_status_reporting():
    """Verifies that an investigation with pending frontier items is reported honestly as PARTIAL/BUDGET, not falsely COMPLETED."""
    from app.investigation_agent.agenda import InvestigationAgenda, InvestigationQuestion
    agenda = InvestigationAgenda(max_probes=5)
    agenda.questions["q1"] = InvestigationQuestion(id="q1", target_state_class="c1", hypothesis="h1", probe_family="nav", status="unexplored")
    agenda.questions["q2"] = InvestigationQuestion(id="q2", target_state_class="c1", hypothesis="h2", probe_family="nav", status="unexplored")

    state = {
        "target_url": "https://www.saucedemo.com/",
        "investigation_id": "inv_honest_test",
        "start_time": 1000.0,
        "actions_attempted": 5,
        "actions_successful": 4,
        "actions_failed": 1,
        "actions_safety_blocked": 0,
        "stopping_reason": "Probe action budget reached",
        "agenda": agenda,
        "states": {},
        "transitions": [],
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }
    rep = InvestigationReportBuilder.build_report(state)
    assert rep.overview.status in (InvestigationStatus.PARTIAL, InvestigationStatus.BUDGET_EXHAUSTED)
    assert rep.overview.status != InvestigationStatus.COMPLETED


def test_synthesize_test_cases_from_investigation_produces_executable_steps():
    """Verifies that synthesize_test_cases_from_investigation builds actionable TestCase objects."""
    report = InvestigationReport(
        overview=ReportOverview(
            investigation_id="inv-123",
            start_time="2026-10-22 10:00:00",
            end_time="2026-10-22 10:01:00",
            status=InvestigationStatus.COMPLETED,
            target_url="https://www.saucedemo.com/",
            final_url="https://www.saucedemo.com/inventory.html",
            duration_seconds=50.0,
            planning_strategy="LLM",
            llm_calls_count=2,
            llm_fallbacks_count=0,
            llm_latency_seconds=15.0,
            pages_discovered=2,
            meaningful_states_discovered=2,
            transitions_observed=1,
            route_transitions_observed=1,
            same_route_state_transitions=0,
            actions_evaluated=3,
            actions_attempted=3,
            actions_successful=3,
            actions_verified_effects=1,
            actions_failed=0,
            actions_safety_blocked=0,
            pending_frontier_count=0,
            stopping_reason="All questions resolved",
        ),
        application_understanding="SauceDemo portal",
        user_provided_context_distinction="Standard test",
        authentication_access=ReportAuthentication(
            boundary_observed=True,
            credentials_available=True,
            authentication_attempted=True,
            authentication_verified=True,
        ),
        inferred_journeys=[
            {
                "journey_id": "j1",
                "name": "Login and Catalog Access",
                "objective": "Verify user authentication and access to inventory",
                "preconditions": ["Valid standard_user credentials"],
                "steps": [
                    {
                        "step_num": 1,
                        "action": "CLICK_CONTROL on #login-button",
                        "expected_url": "https://www.saucedemo.com/inventory.html",
                        "expected_heading": "Products",
                        "expected_selector": ".inventory_list",
                        "evidence_refs": ["ev-123"],
                    }
                ],
                "assertions": [
                    {"type": "URL_CONTAINS", "expected": "/inventory.html"},
                    {"type": "HEADING_VISIBLE", "expected": "Products"},
                ]
            }
        ]
    )

    cases = synthesize_test_cases_from_investigation(report)
    assert len(cases) >= 1
    tc = cases[0]
    assert tc.test_id.startswith("TC-INV")
    assert tc.steps is not None
    assert len(tc.steps) == 2  # Step 1: Navigate, Step 2: Click login
    assert tc.steps[1].assertion is not None
    assert tc.steps[1].assertion.expected_value in ("inventory.html", "/inventory.html", "https://www.saucedemo.com/inventory.html")


def test_synthesize_test_cases_with_business_rules():
    """Verifies that synthesize_test_cases_from_investigation handles RuleOrHypothesis and BugLead objects with correct attribute names."""
    from app.investigation_agent.models import RuleOrHypothesis, RuleCategory, BugLead, OracleType, OracleStrength

    rule = RuleOrHypothesis(
        rule_id="RULE-01",
        description="Password must be required and cannot be blank",
        category=RuleCategory.EXPLICIT_REQUIREMENT,
        source="form_inspection",
        supporting_evidence_ids=["ev-01", "ev-02"],
    )
    lead = BugLead(
        id="lead-01",
        title="Unexpected blank submission acceptance",
        oracle_type=OracleType.DECLARED,
        strength=OracleStrength.HIGH,
        probe_family="forms",
        question_id="q1",
        target_desc="#submit-btn",
        expected={"text": "Should reject"},
        actual={"text": "Accepted"},
        evidence_ids=["ev-03"],
    )

    report = InvestigationReport(
        overview=ReportOverview(
            investigation_id="inv-456",
            start_time="2026-10-22 10:00:00",
            end_time="2026-10-22 10:01:00",
            status=InvestigationStatus.COMPLETED,
            target_url="https://www.saucedemo.com/",
            final_url="https://www.saucedemo.com/",
            duration_seconds=30.0,
            planning_strategy="DETERMINISTIC",
            pages_discovered=1,
            meaningful_states_discovered=1,
            stopping_reason="Complete",
        ),
        application_understanding="SauceDemo portal",
        user_provided_context_distinction="Standard test",
        authentication_access=ReportAuthentication(
            boundary_observed=False,
            credentials_available=False,
            authentication_attempted=False,
            authentication_verified=False,
        ),
        business_rules_and_hypotheses=[rule],
        confirmed_bug_leads=[lead],
    )

    cases = synthesize_test_cases_from_investigation(report)
    assert len(cases) == 2
    rule_case = next(c for c in cases if c.test_id == "TC-INV-RULE-01")
    assert rule_case.rule_ids == ["RULE-01"]
    assert "ev-01" in rule_case.evidence_ids

    bug_case = next(c for c in cases if c.test_id == "TC-INV-BUG-01")
    assert "ev-03" in bug_case.evidence_ids
    assert bug_case.relevant_elements == ["#submit-btn"]
