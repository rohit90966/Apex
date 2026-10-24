import pytest
from unittest.mock import AsyncMock
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
    TestCaseStatus,
)
from app.test_planning_agent import TestPlanningAgent
from app.llm.base import BaseLLMProvider, LLMConnectionError


class MockSuccessOllama(BaseLLMProvider):
    async def generate_json(self, system_prompt: str, user_prompt: str):
        return {
            "test_cases": [
                {
                    "test_id": "TC-AI-01",
                    "name": "Verify credential input validation",
                    "objective": "Verify system validates credentials properly",
                    "page_category": "LOGIN",
                    "relevant_elements": ["input#username"],
                    "action_description": "Enter test credentials",
                    "expected_outcome": "Appropriate auth response",
                    "expected_behavior_source": "INVARIANT",
                    "evidence_to_collect": ["DOM state"],
                    "priority": "HIGH",
                    "preconditions": ["Page ready"],
                    "selection_reason": "Identified login form",
                    "confidence": 0.95,
                    "status": "PROPOSED",
                }
            ],
            "assumptions": ["Valid test environment"],
            "missing_requirements_or_uncertainties": ["Account lock threshold unknown"],
            "planning_summary": "Generated test plan via Ollama",
        }


class MockSuccessGemini(BaseLLMProvider):
    def __init__(self):
        self.model = "gemini-2.5-flash"

    async def generate_json(self, system_prompt: str, user_prompt: str):
        return {
            "test_cases": [
                {
                    "test_id": "TC-GEMINI-01",
                    "name": "Verify credential input validation",
                    "objective": "Verify system validates credentials properly",
                    "page_category": "LOGIN",
                    "relevant_elements": ["input#username"],
                    "action_description": "Enter test credentials",
                    "expected_outcome": "Appropriate auth response",
                    "expected_behavior_source": "INVARIANT",
                    "evidence_to_collect": ["DOM state"],
                    "priority": "HIGH",
                    "preconditions": ["Page ready"],
                    "selection_reason": "Identified login form",
                    "confidence": 0.95,
                    "status": "PROPOSED",
                }
            ],
            "assumptions": ["Valid test environment"],
            "missing_requirements_or_uncertainties": ["Account lock threshold unknown"],
            "planning_summary": "Generated test plan via Gemini",
        }


class MockFailingOllama(BaseLLMProvider):
    async def generate_json(self, system_prompt: str, user_prompt: str):
        raise LLMConnectionError("Could not connect to Ollama")


@pytest.mark.asyncio
async def test_agent_success_with_gemini():
    inspection = InspectionResult(
        inspection_id="insp-gemini-1",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login",
        buttons=["Sign In"],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form found"],
        reasoning_summary="Login page",
    )

    agent = TestPlanningAgent(provider=MockSuccessGemini())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.GEMINI
    assert len(plan.test_cases) == 1
    assert plan.test_cases[0].test_id == "TC-GEMINI-01"
    assert plan.test_cases[0].status == TestCaseStatus.PROPOSED


@pytest.mark.asyncio
async def test_agent_success_with_ollama():
    inspection = InspectionResult(
        inspection_id="insp-1",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login",
        buttons=["Sign In"],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form found"],
        reasoning_summary="Login page",
    )

    agent = TestPlanningAgent(provider=MockSuccessOllama())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.OLLAMA
    assert len(plan.test_cases) == 1
    assert plan.test_cases[0].test_id == "TC-AI-01"
    assert plan.test_cases[0].status == TestCaseStatus.PROPOSED


@pytest.mark.asyncio
async def test_agent_fallback_when_ollama_unavailable():
    inspection = InspectionResult(
        inspection_id="insp-2",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login",
        buttons=["Sign In"],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form found"],
        reasoning_summary="Login page",
    )

    agent = TestPlanningAgent(provider=MockFailingOllama())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert len(plan.test_cases) > 0
    assert len(plan.warnings) > 0
    assert "planning unavailable or agentic workflow failed" in plan.warnings[0]


@pytest.mark.asyncio
async def test_agent_with_evidence_ledger():
    from app.schemas import FormMetadata, FormFieldMetadata
    inspection = InspectionResult(
        inspection_id="insp-3",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login",
        buttons=["Sign In"],
        forms=[
            FormMetadata(
                form_id="login-form",
                fields=[
                    FormFieldMetadata(
                        name="username",
                        input_type="text",
                        label="Username",
                        maxlength=20,
                        is_required=True,
                    ),
                    FormFieldMetadata(
                        name="password",
                        input_type="password",
                        label="Password",
                        minlength=6,
                        is_required=True,
                    )
                ]
            )
        ]
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form found"],
        reasoning_summary="Login page",
    )

    agent = TestPlanningAgent(provider=MockSuccessOllama())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.OLLAMA
    assert plan.evidence_ledger_count is not None
    assert plan.evidence_ledger_count >= 3  # maxlength, minlength, required fields
    assert plan.deterministic_test_count is not None
    assert plan.deterministic_test_count >= 5  # BVA boundary ideas derived
    assert plan.unknowns_count is not None
