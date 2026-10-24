import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, Any, Optional, List

from app.config import get_settings
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

logger = logging.getLogger(__name__)


def clean_json_text(text: str) -> str:
    """Strips markdown code fences and cleans leading/trailing whitespace."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def repair_common_json_flaws(text: str) -> str:
    """Applies defensive regex fixes for common LLM JSON syntax artifacts like trailing commas."""
    cleaned = re.sub(r",\s*([\]}])", r"\1", text)
    return cleaned


class GroqProvider(BaseLLMProvider):
    """
    Groq Cloud API integration using the official langchain-groq SDK.
    Uses llama-3.3-70b-versatile as default model, supporting high-throughput
    structured JSON generation with response_format={'type': 'json_object'}.
    Ensures credentials are never printed, logged, or leaked.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        settings = get_settings()
        if api_key is not None:
            self._api_key = api_key
        else:
            self._api_key = os.environ.get("GROQ_API_KEY") or getattr(settings, "groq_api_key", None)
        self.model = model or os.environ.get("GROQ_MODEL") or getattr(settings, "groq_model", "llama-3.3-70b-versatile")
        self.timeout = timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds
        self.temperature = temperature if temperature is not None else getattr(settings, "groq_temperature", 0.1)
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries

        self.request_count = 0
        self.requests_log: List[Dict[str, Any]] = []
        self.last_call_stats: Dict[str, Any] = {}

        # Validate API key eagerly
        if not self._api_key or not self._api_key.strip():
            raise LLMAuthError(
                "GROQ_API_KEY is not set. Please set the GROQ_API_KEY environment variable "
                "or configure it in your .env file."
            )

        # Initialize the LangChain Groq client
        try:
            from langchain_groq import ChatGroq
            self.llm = ChatGroq(
                model_name=self.model,
                groq_api_key=self._api_key,
                temperature=self.temperature,
                request_timeout=self.timeout,
                max_retries=self.max_retries,
                model_kwargs={"response_format": {"type": "json_object"}},
            )
        except Exception as e:
            logger.error("Failed to initialize ChatGroq client: %s", self._sanitize_error(str(e)))
            raise LLMConnectionError(f"Failed to initialize Groq client: {self._sanitize_error(str(e))}")

    def _sanitize_error(self, message: str) -> str:
        """Ensures that the API key is NEVER leaked in any error message or log."""
        if self._api_key and len(self._api_key) > 6:
            return message.replace(self._api_key, "[REDACTED_API_KEY]")
        return message

    def get_langchain_llm(self):
        """Returns the underlying ChatGroq instance for direct LangChain / LangGraph integration."""
        return self.llm

    async def verify_availability(self) -> Dict[str, Any]:
        """
        Actively checks whether the configured Groq model is available and accepts requests.
        """
        try:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=self._api_key)
            await client.models.retrieve(self.model)
            return {
                "available": True,
                "model": self.model,
                "status": "CONNECTED",
                "message": f"Groq model '{self.model}' verified and active.",
            }
        except Exception as e:
            msg = self._sanitize_error(str(e))
            msg_lower = msg.lower()
            if any(k in msg_lower for k in ["404", "not_found", "model_not_found"]):
                return {
                    "available": False,
                    "model": self.model,
                    "status": "UNAVAILABLE_MODEL_NOT_FOUND",
                    "message": f"Groq model '{self.model}' is not available (404 Not Found).",
                    "error": msg[:200],
                }
            elif any(k in msg_lower for k in ["api_key_invalid", "permission_denied", "unauthenticated", "401", "403"]):
                return {
                    "available": False,
                    "model": self.model,
                    "status": "AUTH_ERROR",
                    "message": "Groq API key is invalid or lacks permissions.",
                    "error": msg[:200],
                }
            return {
                "available": False,
                "model": self.model,
                "status": "ERROR",
                "message": f"Failed to verify Groq model '{self.model}'.",
                "error": msg[:200],
            }

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a prompt against Groq and returns parsed JSON.
        Implements bounded retries with exponential backoff for transient failures.
        """
        self.request_count += 1
        req_id = self.request_count
        prompt_chars = len(system_prompt) + len(user_prompt)

        logger.info(
            "[Groq Request #%d] Dispatching to %s (prompt_chars=%d, temp=%.2f, timeout=%.1fs)",
            req_id,
            self.model,
            prompt_chars,
            self.temperature,
            self.timeout,
        )

        from langchain_core.messages import SystemMessage, HumanMessage

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        last_error = None
        start_time = time.time()

        for attempt in range(self.max_retries + 1):
            try:
                attempt_t0 = time.time()
                response = await asyncio.wait_for(
                    self.llm.ainvoke(messages),
                    timeout=self.timeout,
                )
                call_duration = time.time() - attempt_t0

                content = getattr(response, "content", "")
                if isinstance(content, list):
                    # Handle content block lists if returned
                    content = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                content = str(content)

                if not content.strip():
                    raise LLMResponseError("Groq returned an empty response.")

                # Clean and parse JSON
                cleaned = clean_json_text(content)
                cleaned = repair_common_json_flaws(cleaned)

                try:
                    parsed_json = json.loads(cleaned)
                    if not isinstance(parsed_json, dict):
                        raise LLMSchemaValidationError("Root JSON object is not a dictionary.")
                except (json.JSONDecodeError, ValueError) as parse_err:
                    logger.warning("[Groq Request #%d] JSON decode failed: %s", req_id, parse_err)
                    raise LLMParseError(f"Failed to parse Groq output as JSON: {parse_err}")

                # Usage and timing telemetry
                total_duration = round(time.time() - start_time, 2)
                usage_meta = getattr(response, "usage_metadata", {}) or {}
                if not usage_meta and hasattr(response, "response_metadata"):
                    usage_meta = response.response_metadata.get("token_usage", {})

                prompt_tokens = usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0
                eval_tokens = usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0
                tok_per_sec = round(eval_tokens / call_duration, 1) if call_duration > 0 and eval_tokens else 0.0

                req_stat = {
                    "request_id": req_id,
                    "purpose": "plan_generation",
                    "provider": "groq",
                    "model": self.model,
                    "prompt_size_chars": prompt_chars,
                    "prompt_tokens": prompt_tokens,
                    "eval_count": eval_tokens,
                    "duration_seconds": round(call_duration, 2),
                    "tokens_per_second": tok_per_sec,
                    "attempts": attempt + 1,
                }
                self.requests_log.append(req_stat)

                self.last_call_stats = {
                    "provider": "groq",
                    "model": self.model,
                    "prompt_size_chars": prompt_chars,
                    "prompt_tokens": prompt_tokens,
                    "eval_count": eval_tokens,
                    "duration_seconds": total_duration,
                    "tokens_per_second": tok_per_sec,
                    "tokens_per_sec": tok_per_sec,
                    "format_retries": 0,
                    "attempts": attempt + 1,
                    "requests_detail": [req_stat],
                }

                logger.info(
                    "[Groq Request #%d] Succeeded in %.2fs (%d tokens, %.1f tok/s, %d attempts)",
                    req_id,
                    total_duration,
                    eval_tokens,
                    tok_per_sec,
                    attempt + 1,
                )
                return parsed_json

            except asyncio.TimeoutError:
                last_error = LLMTimeoutError(
                    f"Groq request timed out after {self.timeout}s on attempt {attempt + 1}"
                )
                logger.warning("[Groq Request #%d] Timeout on attempt %d: %s", req_id, attempt + 1, last_error)

            except (LLMParseError, LLMSchemaValidationError):
                raise

            except Exception as exc:
                sanitized_msg = self._sanitize_error(str(exc))
                exc_lower = sanitized_msg.lower()

                # Permanent failure modes: NEVER retry these
                if any(k in exc_lower for k in ["404", "not_found", "model_not_found"]):
                    logger.error("[Groq Request #%d] Model '%s' not found: %s", req_id, self.model, sanitized_msg)
                    raise LLMModelNotFoundError(f"Groq model '{self.model}' not found: {sanitized_msg[:160]}")
                elif any(k in exc_lower for k in ["api_key_invalid", "permission_denied", "unauthenticated", "invalid_api_key", "401", "403"]):
                    logger.error("[Groq Request #%d] Authentication failed: %s", req_id, sanitized_msg)
                    raise LLMAuthError(f"Groq authentication failed: {sanitized_msg[:140]}")
                elif any(k in exc_lower for k in ["rate_limit_exceeded", "quota", "rate_limit", "429"]):
                    last_error = LLMRateLimitError(f"Groq rate limit exceeded: {sanitized_msg[:140]}")
                    logger.warning("[Groq Request #%d] Rate limit on attempt %d: %s", req_id, attempt + 1, sanitized_msg)
                elif any(k in exc_lower for k in ["unavailable", "503", "connection", "deadline"]):
                    last_error = LLMConnectionError(f"Groq service unavailable: {sanitized_msg[:140]}")
                    logger.warning("[Groq Request #%d] Transient connection error on attempt %d: %s", req_id, attempt + 1, sanitized_msg)
                else:
                    last_error = LLMResponseError(f"Groq API error: {sanitized_msg[:140]}")
                    logger.warning("[Groq Request #%d] API error on attempt %d: %s", req_id, attempt + 1, sanitized_msg)

            # Apply backoff before retrying
            if attempt < self.max_retries:
                backoff = (2 ** attempt) * 1.0
                logger.info("[Groq Request #%d] Waiting %.1fs before retry attempt %d...", req_id, backoff, attempt + 2)
                await asyncio.sleep(backoff)

        # Exhausted all retries
        raise last_error or LLMResponseError("Groq request failed after all retry attempts.")
