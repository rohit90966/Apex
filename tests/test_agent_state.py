import pytest
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.schemas import PageClassification, PageCategory, InspectionResult, PlanningMode


def test_agent_workflow_state_initialization():
    inspection = InspectionResult(
        inspection_id="insp-test",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Sign In",
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form observed"],
        reasoning_summary="Login recognized",
    )

    state: AgentWorkflowState = {
        "request_id": "req-1",
        "user_requirements": ["Must support password masking"],
        "website_description": "User login portal",
        "inspection_evidence": inspection,
        "page_classification": classification,
        "proposed_test_cases": [],
        "warnings": [],
        "revision_count": 0,
        "max_revisions": 2,
        "current_stage": "INITIALIZED",
        "workflow_status": WorkflowStatus.INITIALIZED,
        "planning_mode": PlanningMode.OLLAMA,
    }

    assert state["workflow_status"] == WorkflowStatus.INITIALIZED
    assert state["revision_count"] == 0
    assert state["user_requirements"] == ["Must support password masking"]
