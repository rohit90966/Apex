import pytest
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
    FormMetadata,
    FormFieldMetadata,
)
from app.llm.base import BaseLLMProvider, LLMConnectionError
from app.agents.workflow import build_qa_planning_workflow, should_revise
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.agents.models import CritiqueResult
from app.test_planning_agent import TestPlanningAgent


class MockOllamaWorkflowProvider(BaseLLMProvider):
    async def generate_json(self, system_prompt: str, user_prompt: str):
        return {
            "test_cases": [
                {
                    "test_id": "TC-AGENTIC-01",
                    "name": "Verify login field authentication",
                    "objective": "Verify credentials can be entered",
                    "page_category": "LOGIN",
                    "relevant_elements": ["username", "password"],
                    "action_description": "Inspect credentials inputs",
                    "expected_outcome": "Inputs accept text securely",
                    "expected_behavior_source": "INVARIANT",
                    "evidence_to_collect": ["DOM tree"],
                    "priority": "HIGH",
                    "preconditions": ["Page loaded"],
                    "selection_reason": "Strategy requirement",
                    "confidence": 0.95,
                    "status": "PROPOSED",
                }
            ]
        }


class MockOllamaFailingProvider(BaseLLMProvider):
    async def generate_json(self, system_prompt: str, user_prompt: str):
        raise LLMConnectionError("Ollama daemon unavailable")


@pytest.mark.asyncio
async def test_full_agentic_workflow_success():
    inspection = InspectionResult(
        inspection_id="insp-wf-1",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login Portal",
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="f1",
                fields=[
                    FormFieldMetadata(name="username", input_type="text"),
                    FormFieldMetadata(name="password", input_type="password"),
                ],
            )
        ],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.92,
        evidence=["Observed login form"],
        reasoning_summary="Login recognized",
    )

    agent = TestPlanningAgent(provider=MockOllamaWorkflowProvider())
    plan = await agent.create_plan(
        inspection=inspection,
        classification=classification,
        requirements=["Ensure secure login with password masking"],
        description="User login flow",
    )

    assert plan.planning_mode == PlanningMode.OLLAMA
    assert len(plan.test_cases) == 1
    assert plan.test_cases[0].test_id == "TC-AGENTIC-01"
    assert plan.workflow_status == "COMPLETED"
    assert plan.coverage_summary is not None
    assert plan.review_summary is not None


@pytest.mark.asyncio
async def test_workflow_fallback_on_llm_failure():
    inspection = InspectionResult(
        inspection_id="insp-wf-2",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login Portal",
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="f1",
                fields=[
                    FormFieldMetadata(name="user", input_type="text"),
                    FormFieldMetadata(name="pass", input_type="password"),
                ],
            )
        ],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.90,
        evidence=["Observed login"],
        reasoning_summary="Login",
    )

    agent = TestPlanningAgent(provider=MockOllamaFailingProvider())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert plan.workflow_status == "FALLBACK"
    assert len(plan.test_cases) > 0
    assert len(plan.warnings) > 0


def test_should_revise_routing_logic():
    # 1. Issues found and within max revisions
    state: AgentWorkflowState = {
        "revision_count": 0,
        "max_revisions": 2,
        "critique_result": CritiqueResult(
            issues_found=True,
            issue_categories=["MISSING_REQUIREMENT"],
        ),
    }
    next_node = should_revise(state)
    assert next_node == "strategy_planner"
    assert state["revision_count"] == 1

    # 2. Max revisions reached -> goes to plan_validator
    state["revision_count"] = 2
    next_node_terminated = should_revise(state)
    assert next_node_terminated == "plan_validator"
