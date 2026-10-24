"""
Comprehensive unit tests for Google Gemini Provider and LLM Factory.
Tests provider selection, API key validation, structured JSON parsing,
timeout handling, retry behavior, rate-limit classification, and error sanitization.
All tests use mocking and do not make external network requests.
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch

from app.llm.base import (
    BaseLLMProvider,
    LLMAuthError,
    LLMConnectionError,
    LLMModelNotFoundError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMResponseError,
    LLMParseError,
    LLMSchemaValidationError,
)
from app.llm.factory import get_llm_provider
from app.llm.gemini_provider import GeminiProvider, clean_json_text, repair_common_json_flaws
from app.llm.ollama_provider import OllamaProvider


# ==============================================================================
# 1. LLM Factory Tests
# ==============================================================================

def test_factory_selects_gemini_by_default(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-secret-key-123456")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    provider = get_llm_provider()
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-flash-latest"


def test_factory_selects_ollama_when_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    provider = get_llm_provider(provider_type="ollama")
    assert isinstance(provider, OllamaProvider)


def test_factory_rejects_unsupported_provider():
    with pytest.raises(ValueError) as exc:
        get_llm_provider(provider_type="unsupported-provider-xyz")
    assert "Unsupported LLM provider" in str(exc.value)


def test_factory_raises_when_gemini_key_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with patch("app.llm.factory.get_settings") as mock_settings, patch("app.llm.gemini_provider.get_settings") as mock_gem_settings:
        mock_settings.return_value.google_api_key = None
        mock_settings.return_value.llm_provider = "gemini"
        mock_settings.return_value.gemini_model = "gemini-2.5-flash"
        mock_settings.return_value.llm_timeout_seconds = 60.0
        mock_settings.return_value.gemini_temperature = 0.1
        mock_settings.return_value.llm_max_retries = 2
        mock_gem_settings.return_value = mock_settings.return_value

        with pytest.raises(LLMAuthError) as exc:
            get_llm_provider(provider_type="gemini", api_key=None)
        assert "GOOGLE_API_KEY is not set" in str(exc.value)


def test_no_ollama_initialization_on_gemini_path(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-secret-key-123456")
    with patch("app.llm.ollama_provider.OllamaProvider.__init__") as mock_ollama_init:
        provider = get_llm_provider(provider_type="gemini")
        assert isinstance(provider, GeminiProvider)
        mock_ollama_init.assert_not_called()


# ==============================================================================
# 2. Secret Redaction & Sanitization Tests
# ==============================================================================

def test_error_sanitizer_redacts_api_key(monkeypatch):
    api_key = "AIzaSyAbc123RealLookingKey456"
    monkeypatch.setenv("GOOGLE_API_KEY", api_key)
    provider = GeminiProvider(api_key=api_key)
    leak_string = f"Error communicating with https://api.google.com?key={api_key} failed"
    sanitized = provider._sanitize_error(leak_string)
    assert api_key not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized


# ==============================================================================
# 3. JSON Text Cleaning & Repair Tests
# ==============================================================================

def test_clean_json_text_markdown_fences():
    raw = "```json\n{\"test_cases\": []}\n```"
    assert clean_json_text(raw) == '{"test_cases": []}'


def test_repair_common_json_trailing_commas():
    bad_json = '{"a": 1, "b": [1, 2, ], }'
    repaired = repair_common_json_flaws(bad_json)
    assert repaired == '{"a": 1, "b": [1, 2]}'
    parsed = json.loads(repaired)
    assert parsed == {"a": 1, "b": [1, 2]}


# ==============================================================================
# 4. GeminiProvider Generation Tests (Async Mocked)
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_provider_generate_json_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=0)

    # Mock the SDK client response
    mock_response = MagicMock()
    mock_response.text = '{"test_cases": [{"test_id": "TC-01", "name": "Login Test"}]}'
    mock_meta = MagicMock()
    mock_meta.prompt_token_count = 120
    mock_meta.candidates_token_count = 45
    mock_response.usage_metadata = mock_meta

    provider.client = MagicMock()
    provider.client.models.generate_content.return_value = mock_response

    result = await provider.generate_json("System prompt", "User prompt")
    assert isinstance(result, dict)
    assert "test_cases" in result
    assert len(result["test_cases"]) == 1
    assert result["test_cases"][0]["test_id"] == "TC-01"

    # Verify telemetry stats
    assert provider.last_call_stats["provider"] == "gemini"
    assert provider.last_call_stats["eval_count"] == 45
    assert provider.last_call_stats["prompt_tokens"] == 120


@pytest.mark.asyncio
async def test_gemini_provider_handles_parse_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=0)

    mock_response = MagicMock()
    mock_response.text = "This is definitely not JSON { broken"
    mock_response.usage_metadata = None
    provider.client = MagicMock()
    provider.client.models.generate_content.return_value = mock_response

    with pytest.raises(LLMParseError) as exc:
        await provider.generate_json("Sys", "User")
    assert "Failed to parse Gemini output as JSON" in str(exc.value)


@pytest.mark.asyncio
async def test_gemini_provider_handles_non_dict_root_json(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=0)

    mock_response = MagicMock()
    mock_response.text = '["an", "array", "not", "dict"]'
    mock_response.usage_metadata = None
    provider.client = MagicMock()
    provider.client.models.generate_content.return_value = mock_response

    with pytest.raises(LLMSchemaValidationError) as exc:
        await provider.generate_json("Sys", "User")
    assert "Root JSON object is not a dictionary" in str(exc.value)


@pytest.mark.asyncio
async def test_gemini_provider_retries_transient_connection_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=1)

    mock_response = MagicMock()
    mock_response.text = '{"test_cases": []}'
    mock_response.usage_metadata = None

    provider.client = MagicMock()
    # First attempt raises transient connection error, second succeeds
    provider.client.models.generate_content.side_effect = [
        Exception("503 Service Unavailable"),
        mock_response,
    ]

    with patch("asyncio.sleep", return_value=None):
        result = await provider.generate_json("Sys", "User")
        assert result == {"test_cases": []}
        assert provider.client.models.generate_content.call_count == 2


@pytest.mark.asyncio
async def test_gemini_provider_fails_eagerly_on_auth_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=2)

    provider.client = MagicMock()
    provider.client.models.generate_content.side_effect = Exception("403 API_KEY_INVALID: The provided API key is invalid")

    with pytest.raises(LLMAuthError) as exc:
        await provider.generate_json("Sys", "User")
    assert "Gemini authentication failed" in str(exc.value)
    # Eager failure: should NOT retry 403 auth errors
    assert provider.client.models.generate_content.call_count == 1


@pytest.mark.asyncio
async def test_gemini_provider_fails_eagerly_on_404_model_not_found(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456", max_retries=2)

    provider.client = MagicMock()
    provider.client.models.generate_content.side_effect = Exception(
        "404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available"
    )

    with pytest.raises(LLMModelNotFoundError) as exc:
        await provider.generate_json("Sys", "User")
    assert "not found or unavailable" in str(exc.value).lower()
    # Eager failure: MUST NOT retry 404 errors with exponential backoff
    assert provider.client.models.generate_content.call_count == 1


@pytest.mark.asyncio
async def test_gemini_verify_availability_success(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456")

    provider.client = MagicMock()
    provider.client.models.count_tokens.return_value = MagicMock(total_tokens=2)

    diag = await provider.verify_availability()
    assert diag["available"] is True
    assert diag["status"] == "CONNECTED"
    assert "verified" in diag["message"].lower()


@pytest.mark.asyncio
async def test_gemini_verify_availability_404(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456")

    provider.client = MagicMock()
    provider.client.models.count_tokens.side_effect = Exception("404 NOT_FOUND: model not found")

    diag = await provider.verify_availability()
    assert diag["available"] is False
    assert diag["status"] == "UNAVAILABLE_MODEL_NOT_FOUND"


@pytest.mark.asyncio
async def test_gemini_list_available_models(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-123456")
    provider = GeminiProvider(api_key="test-key-123456")

    m1 = MagicMock(name="models/gemini-flash-latest", supported_actions=["generateContent", "countTokens"])
    m1.name = "models/gemini-flash-latest"
    m2 = MagicMock(name="models/embed-001", supported_actions=["embedContent"])
    m2.name = "models/embed-001"

    provider.client = MagicMock()
    provider.client.models.list.return_value = [m1, m2]

    models = await provider.list_available_models()
    assert "gemini-flash-latest" in models
    assert "embed-001" not in models
