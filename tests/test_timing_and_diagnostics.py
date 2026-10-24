import pytest
import time
from unittest.mock import AsyncMock, MagicMock
import httpx

from app.agents.workflow import _wrap_timed_node
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.test_planning_agent import TestPlanningAgent
from app.llm.base import (
    BaseLLMProvider,
    LLMConnectionError,
    LLMTimeoutError,
    LLMParseError,
    LLMResponseTruncatedError,
)
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
)


def _make_dummy_inspection(target_url="http://127.0.0.1:8001/login"):
    return InspectionResult(
        inspection_id="insp-timing-1",
        target_url=target_url,
        final_url=target_url,
        page_title="Timing Test Login",
        buttons=["Sign In"],
        forms=[],
    )


def _make_dummy_classification():
    return PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.95,
        evidence=["Form found"],
        reasoning_summary="Login page classification",
    )


class MockSuccessProvider(BaseLLMProvider):
    def __init__(self):
        self.last_format_retries = 0
        self.last_call_stats = {
            "duration_seconds": 12.34,
            "eval_count": 420,
            "prompt_tokens_est": 350,
            "tokens_per_sec": 34.0,
            "done_reason": "stop",
        }

    async def generate_json(self, system_prompt, user_prompt, schema=None):
        return {
            "test_cases": [
                {
                    "test_id": "TC-TIMING-001",
                    "name": "Valid Login",
                    "objective": "Verify user can log in with valid credentials",
                    "action_description": "Propose entering valid credentials and submitting",
                    "expected_outcome": "User lands on dashboard securely",
                    "relevant_elements": ["Sign In"],
                    "priority": "HIGH",
                    "expected_behavior_source": "INVARIANT",
                    "applied_pattern_ids": [],
                }
            ],
            "planning_summary": "Generated test case for timing test",
            "assumptions": ["Standard login flow"],
            "missing_context": [],
        }


class MockTimeoutProvider(BaseLLMProvider):
    last_format_retries = 0

    async def generate_json(self, system_prompt, user_prompt, schema=None):
        raise LLMTimeoutError("Ollama request timed out after 300.0s.")


class MockConnectErrorProvider(BaseLLMProvider):
    last_format_retries = 0

    async def generate_json(self, system_prompt, user_prompt, schema=None):
        raise LLMConnectionError("Failed to connect to Ollama at http://localhost:11434")


class MockParseErrorProvider(BaseLLMProvider):
    last_format_retries = 0

    async def generate_json(self, system_prompt, user_prompt, schema=None):
        raise LLMParseError("JSON decode error: Expecting value: line 1 column 1")


class MockTruncatedProvider(BaseLLMProvider):
    last_format_retries = 0

    async def generate_json(self, system_prompt, user_prompt, schema=None):
        raise LLMResponseTruncatedError("Output was truncated due to token budget limit")


@pytest.mark.asyncio
async def test_wrap_timed_node_records_duration_and_updates_state():
    """Verify that _wrap_timed_node tracks duration in state['stage_timings']."""
    state: AgentWorkflowState = {
        "request_id": "req-time-test",
        "stage_timings": {},
        "current_stage": "INITIALIZED",
    }

    async def sample_async_node(st: AgentWorkflowState):
        time.sleep(0.05)  # small measurable sync delay
        return {"current_stage": "SAMPLE_DONE"}

    wrapped = _wrap_timed_node("sample_node", sample_async_node)
    result = await wrapped(state)

    assert result["current_stage"] == "SAMPLE_DONE"
    assert "stage_timings" in result
    assert "sample_node" in result["stage_timings"]
    assert result["stage_timings"]["sample_node"] >= 0.04


@pytest.mark.asyncio
async def test_planning_agent_success_captures_timings_and_ollama_stats():
    """Verify that a successful agent run records execution_duration_seconds, stage_timings, and ollama_stats."""
    agent = TestPlanningAgent(provider=MockSuccessProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.OLLAMA
    assert plan.execution_duration_seconds is not None
    assert plan.execution_duration_seconds >= 0.0
    assert plan.stage_timings is not None
    assert isinstance(plan.stage_timings, dict)
    assert len(plan.stage_timings) > 0
    # Verify stages like requirement_analyst or strategy_planner were recorded
    assert "requirement_analyst" in plan.stage_timings or "strategy_planner" in plan.stage_timings
    assert plan.ollama_stats is not None
    assert plan.ollama_stats.get("duration_seconds") == 12.34
    assert plan.ollama_stats.get("tokens_per_sec") == 34.0


@pytest.mark.asyncio
async def test_timeout_fallback_diagnostic_attribution():
    """Verify that an Ollama timeout failure is attributed with timeout duration and elapsed time."""
    agent = TestPlanningAgent(provider=MockTimeoutProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert plan.execution_duration_seconds is not None
    assert len(plan.warnings) > 0
    warning = plan.warnings[0]
    assert "Ollama planning unavailable" in warning
    assert "timed out" in warning.lower()
    assert "elapsed:" in warning


@pytest.mark.asyncio
async def test_connect_error_fallback_diagnostic_attribution():
    """Verify that an Ollama connect failure is attributed with host info and elapsed time."""
    agent = TestPlanningAgent(provider=MockConnectErrorProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert plan.execution_duration_seconds is not None
    assert len(plan.warnings) > 0
    warning = plan.warnings[0]
    assert "Ollama planning unavailable" in warning
    assert "Failed to connect to Ollama" in warning
    assert "elapsed:" in warning


@pytest.mark.asyncio
async def test_parse_error_fallback_diagnostic_attribution():
    """Verify that a malformed JSON error from Ollama is attributed with parse diagnostic."""
    agent = TestPlanningAgent(provider=MockParseErrorProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert len(plan.warnings) > 0
    warning = plan.warnings[0]
    assert "malformed JSON" in warning or "JSON" in warning
    assert "elapsed:" in warning


@pytest.mark.asyncio
async def test_truncation_error_fallback_diagnostic_attribution():
    """Verify that a token truncation error from Ollama is attributed with truncation diagnostic."""
    agent = TestPlanningAgent(provider=MockTruncatedProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert len(plan.warnings) > 0
    warning = plan.warnings[0]
    assert "truncated" in warning
    assert "elapsed:" in warning


@pytest.mark.asyncio
async def test_workflow_durations_summary_metrics_and_unknowns():
    """Verify that stage_timings includes total and node sum durations, and unknowns are surfaced."""
    from app.evidence.models import UnknownItem

    agent = TestPlanningAgent(provider=MockSuccessProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    # Pre-populate state with an unknown item via an investigation report mock or direct run
    unknown_item = UnknownItem(
        unknown_id="unk-1",
        question="What is the server-side validation rule for username?",
        category="SERVER_VALIDATION_UNKNOWN",
        impact="HIGH",
        resolution_criteria="Need API contract",
        resolution_source="API_CONTRACT",
    )

    class MockReport:
        features = []
        inventory = MagicMock(pages=[], total_discovered=1, total_inspected=1)

    plan = await agent.create_plan(
        inspection=inspection,
        classification=classification,
        investigation_report=MockReport(),
    )

    assert plan.planning_mode == PlanningMode.OLLAMA
    assert "_total_workflow_seconds" in plan.stage_timings
    assert "_sum_node_durations_seconds" in plan.stage_timings
    assert plan.stage_timings["_total_workflow_seconds"] >= 0.0
    assert plan.stage_timings["_sum_node_durations_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_plan_generator_evidence_grounding_and_client_side_note():
    """Verify PlanGeneratorNode applies client vs server note and grounds accessibility evidence."""
    from app.agents.plan_generator import PlanGeneratorNode
    from app.schemas import TestCaseStatus, ExpectedBehaviorSource

    class MockPlanGenProvider(BaseLLMProvider):
        last_format_retries = 0
        last_call_stats = {"eval_count": 150}

        async def generate_json(self, system_prompt, user_prompt, schema=None):
            return {
                "test_cases": [
                    {
                        "test_id": "TC-01",
                        "name": "Empty Username and Password Submission",
                        "objective": "Verify form rejection on blank submit",
                        "action_description": "Propose submitting blank form",
                        "expected_outcome": "Browser prevents submission",
                        "relevant_elements": ["#username", "#password"],
                        "priority": "HIGH",
                        "expected_behavior_source": "INVARIANT",
                        "applied_pattern_ids": ["PAT-AUTH-002"],
                    },
                    {
                        "test_id": "TC-02",
                        "name": "Accessible Names on Interactive Controls",
                        "objective": "Verify ARIA labels",
                        "action_description": "Propose inspecting accessibility labels",
                        "expected_outcome": "Controls have non-empty accessible names",
                        "relevant_elements": ["button", "input"],
                        "priority": "HIGH",
                        "expected_behavior_source": "REQUIREMENT",
                        "applied_pattern_ids": ["PAT-A11Y-001"],
                    },
                ]
            }

    node = PlanGeneratorNode(provider=MockPlanGenProvider())
    inspection = _make_dummy_inspection()
    classification = _make_dummy_classification()

    state = {
        "inspection_evidence": inspection,
        "page_classification": classification,
        "test_strategy": None,
        "retrieved_patterns": [],
        "evidence_ledger": [],
        "test_ideas": [],
        "warnings": [],
        "revision_count": 0,
    }

    result = await node.run(state)
    cases = result["proposed_test_cases"]
    assert len(cases) == 2

    # Case 1: Empty submit should have client vs backend note
    c1 = cases[0]
    assert c1.status == TestCaseStatus.PROPOSED
    assert "client-side" in c1.expected_outcome.lower() or "server-side" in c1.expected_outcome.lower()
    assert any("valuemissing" in e.lower() for e in c1.evidence_to_collect)

    # Case 2: Accessibility should have grounded a11y evidence
    c2 = cases[1]
    assert c2.status == TestCaseStatus.PROPOSED
    assert any("aria" in e.lower() for e in c2.evidence_to_collect)
    assert any("label" in e.lower() for e in c2.evidence_to_collect)

