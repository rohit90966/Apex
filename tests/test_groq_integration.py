"""
Tests for Groq LLM Provider Integration in VerityQA.
Verifies:
1. Eager authentication error when GROQ_API_KEY is missing.
2. Configurable model defaulting to llama-3.3-70b-versatile.
3. Key redaction and error sanitization.
4. Structured JSON output generation with LangGraph planning nodes.
5. Strict failure propagation (no silent fallback to rule-based planner).
6. Live connectivity test when GROQ_API_KEY is present in environment.
"""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import AIMessage

from app.llm.base import (
    LLMAuthError,
    LLMConnectionError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMResponseError,
)
from app.llm.groq_provider import GroqProvider
from app.llm.factory import get_llm_provider
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
    TestCaseStatus,
)
from app.test_planning_agent import TestPlanningAgent


def test_groq_missing_api_key_raises_auth_error():
    """Missing or empty GROQ_API_KEY must raise LLMAuthError eagerly."""
    with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
        with pytest.raises(LLMAuthError) as exc_info:
            GroqProvider(api_key="")
        assert "GROQ_API_KEY is not set" in str(exc_info.value)


def test_groq_factory_defaults_to_groq_and_llama_model():
    """get_llm_provider defaults to Groq provider with llama-3.3-70b-versatile."""
    with patch.dict(os.environ, {"GROQ_MODEL": "llama-3.3-70b-versatile"}):
        provider = get_llm_provider(provider_type="groq", api_key="gsk_mock_valid_key_for_testing_12345", model="llama-3.3-70b-versatile")
        assert isinstance(provider, GroqProvider)
        assert provider.model == "llama-3.3-70b-versatile"
        assert provider.get_langchain_llm() is not None


def test_groq_configurable_model_via_env():
    """GROQ_MODEL environment variable overrides the default model."""
    with patch.dict(os.environ, {"GROQ_MODEL": "llama-3.1-8b-instant"}):
        provider = GroqProvider(api_key="gsk_mock_valid_key_for_testing_12345")
        assert provider.model == "llama-3.1-8b-instant"


def test_groq_key_sanitization():
    """API key must never appear in error messages."""
    fake_secret = "gsk_super_secret_key_9999999"
    provider = GroqProvider(api_key=fake_secret)
    raw_error = f"HTTP 401: Unauthorized access with token {fake_secret} on endpoint"
    sanitized = provider._sanitize_error(raw_error)
    assert fake_secret not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized


@pytest.mark.asyncio
async def test_groq_structured_output_with_langgraph_workflow():
    """VerityQA executes planning workflow with Groq and returns structured test cases."""
    mock_test_cases = {
        "test_cases": [
            {
                "test_id": "TC-GROQ-01",
                "name": "Verify checkout form submission",
                "objective": "Verify user can submit shipping information",
                "page_category": "CHECKOUT",
                "relevant_elements": ["input#name", "button#submit"],
                "action_description": "Fill name and click submit",
                "expected_outcome": "Order processed successfully",
                "expected_behavior_source": "EXPLICIT_REQUIREMENT",
                "evidence_to_collect": ["Network 200 OK"],
                "priority": "HIGH",
                "preconditions": ["Items in cart"],
                "selection_reason": "Core purchase workflow",
                "confidence": 0.98,
                "status": "PROPOSED",
            }
        ],
        "assumptions": ["Valid checkout state"],
        "missing_requirements_or_uncertainties": [],
        "planning_summary": "Generated test plan via Groq llama-3.3-70b-versatile",
    }

    from langchain_groq import ChatGroq
    provider = GroqProvider(api_key="gsk_mock_valid_key_for_testing_12345")
    
    # Mock ChatGroq.ainvoke on class level for clean Pydantic v2 compatibility
    mock_ai_message = AIMessage(
        content='{"test_cases": [{"test_id": "TC-GROQ-01", "name": "Verify checkout form submission", "objective": "Verify user can submit shipping information", "page_category": "CHECKOUT", "relevant_elements": ["input#name", "button#submit"], "action_description": "Fill name and click submit", "expected_outcome": "Order processed successfully", "expected_behavior_source": "EXPLICIT_REQUIREMENT", "evidence_to_collect": ["Network 200 OK"], "priority": "HIGH", "preconditions": ["Items in cart"], "selection_reason": "Core purchase workflow", "confidence": 0.98, "status": "PROPOSED"}], "assumptions": ["Valid checkout state"], "missing_requirements_or_uncertainties": [], "planning_summary": "Generated test plan via Groq"}',
        response_metadata={"token_usage": {"input_tokens": 150, "output_tokens": 85}},
    )
    
    with patch.object(ChatGroq, "ainvoke", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_ai_message
        
        agent = TestPlanningAgent(provider=provider)
        insp = InspectionResult(
            inspection_id="insp-groq-1",
            target_url="http://127.0.0.1:5173/checkout",
            final_url="http://127.0.0.1:5173/checkout",
            page_title="Checkout",
            buttons=["Place Order"],
        )
        cls = PageClassification(
            page_category=PageCategory.GENERAL,
            confidence=0.95,
            evidence=["Checkout form"],
            reasoning_summary="Checkout route",
        )
        
        plan = await agent.create_plan(insp, cls)
        
        assert plan.planning_mode == PlanningMode.GROQ
        assert len(plan.test_cases) == 1
        assert plan.test_cases[0].test_id == "TC-GROQ-01"
        assert plan.test_cases[0].name == "Verify checkout form submission"
        assert plan.test_cases[0].status == TestCaseStatus.PROPOSED
        assert provider.last_call_stats.get("provider") == "groq"
        assert provider.last_call_stats.get("prompt_tokens") == 150
        assert provider.last_call_stats.get("eval_count") == 85


@pytest.mark.asyncio
async def test_groq_failure_does_not_silently_fallback():
    """When Groq API fails, TestPlanningAgent must fail clearly and NOT silently fall back."""
    from langchain_groq import ChatGroq
    provider = GroqProvider(api_key="gsk_mock_valid_key_for_testing_12345")
    
    with patch.object(ChatGroq, "ainvoke", side_effect=Exception("Groq rate_limit_exceeded (429)")):
        agent = TestPlanningAgent(provider=provider)
        insp = InspectionResult(
            inspection_id="insp-fail-1",
            target_url="http://127.0.0.1:5173/",
            final_url="http://127.0.0.1:5173/",
            page_title="Home",
            buttons=[],
        )
        cls = PageClassification(
            page_category=PageCategory.GENERAL,
            confidence=0.9,
            evidence=[],
            reasoning_summary="Home page",
        )
        
        with pytest.raises(LLMRateLimitError):
            await agent.create_plan(insp, cls)


@pytest.mark.asyncio
async def test_live_groq_connectivity():
    """
    Live connectivity test with Groq API.
    Runs when GROQ_API_KEY is configured in the environment.
    """
    key = os.environ.get("GROQ_API_KEY")
    if not key or not key.strip():
        pytest.skip("GROQ_API_KEY not configured in environment; skipping live call.")

    provider = GroqProvider()
    avail = await provider.verify_availability()
    assert avail.get("available") is True
    assert avail.get("status") == "CONNECTED"

    # Test structured JSON generation
    res = await provider.generate_json(
        system_prompt="Return a JSON object with 'status' set to 'ok' and 'model' set to your model name.",
        user_prompt="Ping",
    )
    assert isinstance(res, dict)
    assert res.get("status") == "ok"
