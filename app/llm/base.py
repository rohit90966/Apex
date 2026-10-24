from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class LLMProviderError(Exception):
    """Base exception for LLM provider errors."""
    pass


class LLMAuthError(LLMProviderError):
    """Raised when authentication fails (missing/invalid API key)."""
    pass


class LLMRateLimitError(LLMProviderError):
    """Raised when model request hits rate limits or quota exhaustion."""
    pass


class LLMConnectionError(LLMProviderError):
    """Raised when cannot connect to LLM endpoint."""
    pass


class LLMModelNotFoundError(LLMProviderError):
    """Raised when the specified LLM model is not found (404) or unavailable."""
    pass


class LLMTimeoutError(LLMProviderError):
    """Raised when LLM request times out."""
    pass


class LLMResponseError(LLMProviderError):
    """Raised when LLM response is invalid or malformed."""
    pass


class LLMResponseTruncatedError(LLMResponseError):
    """Raised when LLM output was truncated by token generation budget (num_predict)."""
    pass


class LLMParseError(LLMResponseError):
    """Raised when LLM response cannot be parsed as valid JSON."""
    pass


class LLMSchemaValidationError(LLMResponseError):
    """Raised when LLM response JSON does not conform to the expected schema."""
    pass


class BaseLLMProvider(ABC):
    """Abstract interface for local LLM providers."""

    @abstractmethod
    async def generate_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """
        Executes a prompt against the provider and returns parsed JSON.
        Raises LLMProviderError derivatives upon failure.
        """
        pass
