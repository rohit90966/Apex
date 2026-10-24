"""
Unit and Integration Tests for Phase 4: Investigation Agenda and Workflow Refactor.
Verifies:
- Question ranking formula (novelty, uncertainty, defect likelihood, journey, cost, safety)
- Coverage ledger updates and agenda lifecycle
- Budget enforcement and stopping criteria
- End-to-end hypothesis-driven graph execution on Demo App Fixture 1
"""

import time
import pytest
import uvicorn
import threading
from demo_app.main import app as demo_app
from app.investigation_agent.agenda import (
    InvestigationAgenda,
    InvestigationQuestion,
    CoverageLedger,
)
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    Prediction,
)
from app.investigation_agent.workflow import (
    InvestigationOrchestrator,
    build_investigation_workflow,
)


@pytest.fixture(scope="module")
def fixture1_agenda_server():
    server = uvicorn.Server(uvicorn.Config(demo_app, host="127.0.0.1", port=8993, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8993"
    server.should_exit = True


def test_agenda_question_scoring_formula():
    """
    Verifies that the multi-objective scoring formula:
    score = 0.30 * novelty + 0.25 * uncertainty + 0.20 * defect_likelihood + 0.15 * journey_progress - 0.20 * cost - safety_penalty
    ranks high-yield defect candidates and novel state classes above repetitive or low-yield actions.
    """
    agenda = InvestigationAgenda()

    # Pre-populate one discovered state class in coverage ledger
    agenda.coverage.state_classes_discovered.add("cls_known")

    q_novel_constraint = InvestigationQuestion(
        id="q1",
        category="constraint",
        target_state_class="cls_novel",
        target_element="#reg_password",
        hypothesis="Test server constraint on novel registration form",
        probe_family="forms",
        metadata={"kind": "constraint_bypass_minlength"},
        script=ProbeScript(name="p1", family="forms", steps=[ProbeStep(action_type="FILL")]),
    )

    q_known_nav = InvestigationQuestion(
        id="q2",
        category="discovery",
        target_state_class="cls_known",
        target_element="/about",
        hypothesis="Navigate to about page",
        probe_family="nav",
        metadata={"kind": "link"},
        script=ProbeScript(name="p2", family="nav", steps=[ProbeStep(action_type="NAVIGATE_LINK")]),
    )

    q_dangerous = InvestigationQuestion(
        id="q3",
        category="edge",
        target_state_class="cls_novel",
        target_element="#delete_all",
        hypothesis="Purge database records",
        probe_family="controls",
        metadata={"kind": "destructive", "is_safety_risk": True},
        script=ProbeScript(name="p3", family="controls", steps=[ProbeStep(action_type="CLICK_CONTROL")]),
    )

    score_novel_constraint = agenda.compute_question_score(q_novel_constraint, current_state_class="cls_novel")
    score_known_nav = agenda.compute_question_score(q_known_nav, current_state_class="cls_novel")
    score_dangerous = agenda.compute_question_score(q_dangerous, current_state_class="cls_novel")

    assert score_novel_constraint > score_known_nav, f"Novel constraint score ({score_novel_constraint}) must beat known nav ({score_known_nav})"
    assert score_dangerous == 0.0, f"Dangerous action with safety risk must be penalized to 0.0 (got {score_dangerous})"


def test_coverage_ledger_and_question_lifecycle():
    """
    Verifies question discovery, selection, status transition, and coverage updates.
    """
    agenda = InvestigationAgenda(max_probes=10)

    obs = UIStateObservation(
        state_id="s_login",
        state_class_id="cls_auth",
        url="http://127.0.0.1:8993/login",
        normalized_route="/login",
        page_title="Login",
        forms=[{
            "form_id": "login_form",
            "form_selector": "form",
            "fields": [
                {"name": "username", "id": "username", "type": "text", "required": True},
                {"name": "password", "id": "password", "type": "password", "required": True},
            ]
        }],
        interactive_controls=[],
        navigation_links=[{"href": "/registration", "text": "Register", "is_fragment": False, "is_external": False}],
    )

    new_q_count = agenda.populate_from_observation(obs)
    assert new_q_count > 0, "Agenda must extract questions from observation"
    assert "cls_auth" in agenda.coverage.state_classes_discovered
    assert "http://127.0.0.1:8993/login" in agenda.coverage.routes_visited

    # Select next question
    selected = agenda.select_next_question(current_state_class="cls_auth")
    assert selected is not None
    assert selected.status == "probing"

    # Record outcome
    agenda.record_probe_outcome(
        question_id=selected.id,
        verdict="CONTRADICTED",
        summary="Server failed to enforce boundary check",
        mutating=True,
    )

    assert selected.status == "answered"
    assert selected.answer_summary == "Server failed to enforce boundary check"
    assert agenda.mutations_executed == 1
    assert len(agenda.probe_history) == 1


def test_stopping_criteria_and_budgets():
    """
    Verifies that InvestigationAgenda enforces all hard budgets:
    - max_probes
    - max_mutations
    - max_safety_blocks
    - all questions exhausted
    """
    # 1. Probe limit
    agenda1 = InvestigationAgenda(max_probes=2)
    agenda1.questions["q1"] = InvestigationQuestion(
        id="q1", target_state_class="c1", hypothesis="h1", probe_family="forms", status="unexplored"
    )
    stop, reason = agenda1.should_stop()
    assert not stop

    agenda1.record_probe_outcome("q1", "CONFIRMED", "OK")
    agenda1.record_probe_outcome("q2", "CONFIRMED", "OK")
    stop, reason = agenda1.should_stop()
    assert stop
    assert "budget reached" in reason or "ceiling reached" in reason

    # 2. Mutation limit
    agenda2 = InvestigationAgenda(max_probes=10, max_mutations=2)
    agenda2.questions["q_pending"] = InvestigationQuestion(
        id="q_pending", target_state_class="c1", hypothesis="h1", probe_family="forms", status="unexplored"
    )
    agenda2.record_probe_outcome("q1", "CONFIRMED", "OK", mutating=True)
    stop, _ = agenda2.should_stop()
    assert not stop
    agenda2.record_probe_outcome("q2", "CONFIRMED", "OK", mutating=True)
    stop, reason = agenda2.should_stop()
    assert stop
    assert "mutating action budget" in reason

    # 3. Safety blocks limit
    agenda3 = InvestigationAgenda(max_probes=10, max_safety_blocks=2)
    agenda3.questions["q_pending"] = InvestigationQuestion(
        id="q_pending", target_state_class="c1", hypothesis="h1", probe_family="forms", status="unexplored"
    )
    agenda3.record_probe_outcome("q1", "BLOCKED", "Forbidden", safety_blocked=True)
    stop, _ = agenda3.should_stop()
    assert not stop
    agenda3.record_probe_outcome("q2", "BLOCKED", "Forbidden", safety_blocked=True)
    stop, reason = agenda3.should_stop()
    assert stop
    assert "Safety block threshold" in reason


def test_agenda_workflow_integration_on_fixture1(fixture1_agenda_server):
    """
    Integration test: Runs the new v2 LangGraph node cycle on Fixture 1.
    Verifies that the workflow:
    - Perceives states and updates agenda
    - Selects hypothesis-driven questions
    - Executes probes step-by-step
    - Evaluates stopping conditions cleanly when probe budget is met
    - Emits a well-structured final investigation report
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)

    initial_state = {
        "target_url": f"{fixture1_agenda_server}/registration",
        "investigation_id": "test_agenda_run",
        "max_probes": 2,  # Short budget for fast test turnaround
        "max_actions": 2,
        "max_time_seconds": 30.0,
        "max_mutations": 5,
    }

    final_state = graph.invoke(initial_state)

    # Verifications
    assert final_state is not None
    assert final_state.get("stopping_reason") is not None
    assert final_state.get("probes_executed", 0) >= 1
    assert len(final_state.get("evidence_ledger", [])) >= 1
    assert final_state.get("final_report") is not None
    assert final_state["final_report"].overview is not None
    assert final_state["final_report"].overview.target_url == f"{fixture1_agenda_server}/registration"
