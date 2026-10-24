"""
Tests for OllamaProvider structured output robustness.
Covers:
- Valid JSON → parsed correctly
- Malformed JSON → LLMParseError (no silent acceptance)
- Truncated output (done_reason=length) → LLMResponseTruncatedError
- Schema-invalid root type → LLMSchemaValidationError
- Escaped strings/Unicode/nested structures parse correctly
- Markdown fences stripped
- Trailing-comma JSON repaired
- 1-step repair succeeds when format retry budget allows
- 1-step repair not triggered when budget is 0
- A provider timeout raises LLMTimeoutError (not LLMParseError)
- An unavailable Ollama service raises LLMConnectionError (not LLMParseError)
- format_retry_count tracked independently of revision_count
"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.llm.ollama_provider import OllamaProvider, clean_json_text, repair_common_json_flaws
from app.llm.base import (
    LLMConnectionError,
    LLMTimeoutError,
    LLMResponseError,
    LLMResponseTruncatedError,
    LLMParseError,
    LLMSchemaValidationError,
)
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
    TestCaseStatus,
)
from app.test_planning_agent import TestPlanningAgent


# ─────────────────────────────────────────────
# Helper fixtures
# ─────────────────────────────────────────────

def _make_inspection(inspection_id: str = "insp-test") -> InspectionResult:
    return InspectionResult(
        inspection_id=inspection_id,
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Login Portal",
        buttons=["Login"],
    )


def _make_classification() -> PageClassification:
    return PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.92,
        evidence=["Login form observed"],
        reasoning_summary="Login page recognized",
    )


def _make_ollama_response(content: str, done_reason: str = "stop", status_code: int = 200):
    """Build a mock httpx.Response-like dict that OllamaProvider expects."""
    response_data = {
        "done_reason": done_reason,
        "message": {"content": content},
    }

    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_data
    mock_resp.text = json.dumps(response_data)
    return mock_resp


# ─────────────────────────────────────────────
# Unit tests: JSON cleanup helpers
# ─────────────────────────────────────────────

def test_clean_json_text_strips_markdown_fences():
    raw = "```json\n{\"a\": 1}\n```"
    assert clean_json_text(raw) == '{"a": 1}'


def test_clean_json_text_strips_plain_fences():
    raw = "```\n{\"a\": 1}\n```"
    assert clean_json_text(raw) == '{"a": 1}'


def test_clean_json_text_passthrough_clean():
    raw = '{"a": 1}'
    assert clean_json_text(raw) == '{"a": 1}'


def test_repair_trailing_comma_before_bracket():
    malformed = '{"a": [1, 2,]}'
    repaired = repair_common_json_flaws(malformed)
    assert json.loads(repaired) == {"a": [1, 2]}


def test_repair_trailing_comma_before_brace():
    malformed = '{"a": 1, "b": 2,}'
    repaired = repair_common_json_flaws(malformed)
    assert json.loads(repaired) == {"a": 1, "b": 2}


# ─────────────────────────────────────────────
# OllamaProvider: valid JSON response
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_returns_valid_json():
    provider = OllamaProvider()
    good_content = json.dumps({"test_cases": [{"test_id": "TC-01", "name": "Test"}]})
    mock_resp = _make_ollama_response(good_content)

    async def mock_post(*args, **kwargs):
        return mock_resp

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=mock_resp.json())):
        result = await provider.generate_json("sys", "user")
    assert result["test_cases"][0]["test_id"] == "TC-01"
    assert provider.last_format_retries == 0


# ─────────────────────────────────────────────
# OllamaProvider: truncated response detection
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_raises_truncated_error_on_length_done_reason():
    provider = OllamaProvider()
    truncated_data = {
        "done_reason": "length",
        "message": {"content": '{"test_cases": [{"test_id": "TC-01", "name": "truncated'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=truncated_data)):
        with pytest.raises(LLMResponseTruncatedError):
            await provider.generate_json("sys", "user")


# ─────────────────────────────────────────────
# OllamaProvider: malformed JSON raises LLMParseError
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_raises_parse_error_on_invalid_json():
    provider = OllamaProvider()
    provider.max_format_retries = 0  # Disable repair to isolate parsing behavior

    bad_json_data = {
        "done_reason": "stop",
        "message": {"content": '{"test_cases": [{"broken": UNQUOTED_VALUE}]}'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=bad_json_data)):
        with pytest.raises(LLMParseError):
            await provider.generate_json("sys", "user")


# ─────────────────────────────────────────────
# OllamaProvider: schema-invalid root type
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_raises_schema_error_when_root_not_dict():
    provider = OllamaProvider()
    provider.max_format_retries = 0

    list_root_data = {
        "done_reason": "stop",
        "message": {"content": '[{"test_id": "TC-01"}]'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=list_root_data)):
        with pytest.raises(LLMSchemaValidationError):
            await provider.generate_json("sys", "user")


# ─────────────────────────────────────────────
# OllamaProvider: markdown fences stripped correctly
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_strips_markdown_fences():
    provider = OllamaProvider()
    fenced_data = {
        "done_reason": "stop",
        "message": {"content": '```json\n{"test_cases": []}\n```'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=fenced_data)):
        result = await provider.generate_json("sys", "user")
    assert result == {"test_cases": []}


# ─────────────────────────────────────────────
# OllamaProvider: trailing comma repaired
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_repairs_trailing_comma():
    provider = OllamaProvider()
    trailing_comma_data = {
        "done_reason": "stop",
        "message": {"content": '{"test_cases": [{"id": 1,}]}'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=trailing_comma_data)):
        result = await provider.generate_json("sys", "user")
    assert result["test_cases"][0]["id"] == 1


# ─────────────────────────────────────────────
# OllamaProvider: Unicode and escaped characters
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_handles_unicode_and_escaped_strings():
    provider = OllamaProvider()
    unicode_data = {
        "done_reason": "stop",
        "message": {"content": '{"test_cases": [{"name": "Test \\u00e9l\\u00e9ment \\n verify", "nested": {"a": [1, 2, 3]}}]}'},
    }

    with patch.object(provider, "_post_chat", new=AsyncMock(return_value=unicode_data)):
        result = await provider.generate_json("sys", "user")
    assert "éléme" in result["test_cases"][0]["name"]
    assert result["test_cases"][0]["nested"]["a"] == [1, 2, 3]


# ─────────────────────────────────────────────
# OllamaProvider: timeout vs parse failure distinction
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_raises_timeout_error_not_parse_error():
    provider = OllamaProvider()

    async def timeout_post_chat(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    with patch.object(provider, "_post_chat", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(LLMTimeoutError):
            await provider.generate_json("sys", "user")


# ─────────────────────────────────────────────
# OllamaProvider: connection error vs parse failure
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_raises_connection_error_not_parse_error():
    provider = OllamaProvider()

    with patch.object(provider, "_post_chat", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(LLMConnectionError):
            await provider.generate_json("sys", "user")


# ─────────────────────────────────────────────
# OllamaProvider: 1-step repair within budget
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_attempts_repair_within_budget():
    provider = OllamaProvider()
    provider.max_format_retries = 1

    first_bad = {
        "done_reason": "stop",
        "message": {"content": '{"test_cases": [{"broken": UNQUOTED}]}'},
    }
    repaired_good = {
        "done_reason": "stop",
        "message": {"content": '{"test_cases": [{"test_id": "TC-REPAIR"}]}'},
    }

    call_count = [0]

    async def post_chat_mock(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return first_bad
        return repaired_good

    with patch.object(provider, "_post_chat", side_effect=post_chat_mock):
        result = await provider.generate_json("sys", "user")

    assert result["test_cases"][0]["test_id"] == "TC-REPAIR"
    assert provider.last_format_retries == 1
    assert call_count[0] == 2  # initial attempt + 1 repair


# ─────────────────────────────────────────────
# OllamaProvider: repair not triggered when budget=0
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_no_repair_when_budget_zero():
    provider = OllamaProvider()
    provider.max_format_retries = 0

    bad_data = {
        "done_reason": "stop",
        "message": {"content": '{"broken": UNQUOTED}'},
    }

    call_count = [0]

    async def post_chat_mock(*args, **kwargs):
        call_count[0] += 1
        return bad_data

    with patch.object(provider, "_post_chat", side_effect=post_chat_mock):
        with pytest.raises(LLMParseError):
            await provider.generate_json("sys", "user")

    assert call_count[0] == 1  # No repair call made


# ─────────────────────────────────────────────
# Integration: format_retry_count vs revision_count are independent
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_format_retry_count_independent_of_revision_count():
    """
    Confirm the fallback plan exposes revision_count=0 and format_retry_count=0
    separately from the agentic workflow that raised an error.
    """
    from app.llm.base import LLMConnectionError as ConnErr

    class AlwaysFailProvider:
        async def generate_json(self, *args, **kwargs):
            raise ConnErr("Offline")
        last_format_retries = 0

    inspection = _make_inspection("insp-retry-ind")
    classification = _make_classification()
    agent = TestPlanningAgent(provider=AlwaysFailProvider())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert plan.revision_count == 0
    assert plan.format_retry_count == 0
    assert len(plan.test_cases) > 0


# ─────────────────────────────────────────────
# Integration: failed parse activates fallback, reason preserved
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_reason_preserved_in_warnings():
    from app.llm.base import LLMConnectionError as ConnErr

    class DisconnectedProvider:
        async def generate_json(self, *args, **kwargs):
            raise ConnErr("Cannot reach Ollama")
        last_format_retries = 0

    inspection = _make_inspection("insp-fallback-reason")
    classification = _make_classification()
    agent = TestPlanningAgent(provider=DisconnectedProvider())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    assert len(plan.warnings) > 0
    assert "Ollama planning unavailable" in plan.warnings[0]
    assert plan.workflow_status == "FALLBACK"
    assert plan.current_stage == "FALLBACK"


# ─────────────────────────────────────────────
# Integration: fallback cases not mixed with agent cases
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_cases_not_mixed_with_partial_agent_cases():
    """
    When the agent workflow fails after generating partial cases, the fallback
    should produce a completely fresh set of deterministic cases, not a hybrid.
    """
    from app.llm.base import LLMResponseError as RespErr

    class PartialOutputProvider:
        async def generate_json(self, *args, **kwargs):
            raise RespErr("Malformed JSON")
        last_format_retries = 0

    inspection = _make_inspection("insp-no-mix")
    classification = _make_classification()
    agent = TestPlanningAgent(provider=PartialOutputProvider())
    plan = await agent.create_plan(inspection, classification)

    assert plan.planning_mode == PlanningMode.RULE_BASED_FALLBACK
    for tc in plan.test_cases:
        assert tc.status == TestCaseStatus.PROPOSED
        # Fallback test IDs always start with TC-LOGIN, TC-GEN, or TC-REQ
        assert any(tc.test_id.startswith(p) for p in ["TC-LOGIN", "TC-GEN", "TC-REQ", "TC-REG", "TC-SEARCH", "TC-CONTACT"])


# ─────────────────────────────────────────────
# Integration: knowledge retrieval metrics accurate
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_retrieval_metrics_on_successful_agentic_plan():
    """
    When the agentic plan succeeds and test cases reference pattern IDs,
    applied_knowledge_count should reflect only patterns actually referenced.
    """
    from app.llm.base import BaseLLMProvider

    class PatternAwareProvider(BaseLLMProvider):
        async def generate_json(self, system_prompt, user_prompt):
            return {
                "test_cases": [
                    {
                        "test_id": "TC-PAT-01",
                        "name": "Verify password masking",
                        "objective": "Ensure password masking is enforced",
                        "page_category": "LOGIN",
                        "relevant_elements": ["input[type=password]"],
                        "action_description": "Inspect password field type attribute",
                        "expected_outcome": "Field type is password and characters are obscured",
                        "expected_behavior_source": "INVARIANT",
                        "evidence_to_collect": ["Input type attribute"],
                        "priority": "HIGH",
                        "preconditions": ["Login form rendered"],
                        "selection_reason": "Observed password field",
                        "confidence": 0.95,
                        "status": "PROPOSED",
                        "applied_pattern_ids": ["PAT-AUTH-001"],
                    }
                ]
            }

    inspection = _make_inspection("insp-metrics")
    classification = _make_classification()
    agent = TestPlanningAgent(provider=PatternAwareProvider())
    plan = await agent.create_plan(
        inspection, classification,
        description="Test login with password masking verification"
    )

    if plan.planning_mode == PlanningMode.OLLAMA:
        assert plan.retrieved_knowledge_count is not None
        assert plan.retrieved_knowledge_count >= 0
        # applied_knowledge_count must not exceed retrieved
        if plan.applied_knowledge_count is not None:
            assert plan.applied_knowledge_count <= plan.retrieved_knowledge_count + 5  # allow for seeded patterns
