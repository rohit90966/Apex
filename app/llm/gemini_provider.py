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


class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini API integration using the official google-genai SDK.
    Supports structured JSON generation with response_mime_type='application/json',
    configurable temperature, request timeouts, bounded exponential backoff retries,
    and granular diagnostic tracking without leaking credentials.
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
        # Read API key from parameter, settings, or process environment
        self._api_key = api_key or settings.google_api_key or os.environ.get("GOOGLE_API_KEY")
        self.model = model or settings.gemini_model
        self.timeout = timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds
        self.temperature = temperature if temperature is not None else settings.gemini_temperature
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries

        self.request_count = 0
        self.requests_log = []
        self.last_call_stats = {}

        # Validate API key eagerly
        if not self._api_key or not self._api_key.strip():
            raise LLMAuthError(
                "GOOGLE_API_KEY is not set. Please set the GOOGLE_API_KEY environment variable "
                "or configure it in your .env file."
            )

        # Initialize the Google GenAI client
        try:
            from google import genai
            self.client = genai.Client(api_key=self._api_key)
        except Exception as e:
            logger.error("Failed to initialize Google GenAI client: %s", self._sanitize_error(str(e)))
            raise LLMConnectionError(f"Failed to initialize Gemini client: {self._sanitize_error(str(e))}")

    def _sanitize_error(self, message: str) -> str:
        """Ensures that the API key is NEVER leaked in any error message or log."""
        if self._api_key and len(self._api_key) > 6:
            return message.replace(self._api_key, "[REDACTED_API_KEY]")
        return message

    async def verify_availability(self) -> Dict[str, Any]:
        """
        Actively checks whether the configured model is available and accepts requests.
        Uses count_tokens probe (zero generation cost) to test the exact model and credentials.
        """
        try:
            await asyncio.to_thread(
                self.client.models.count_tokens,
                model=self.model,
                contents="ping",
            )
            return {
                "available": True,
                "model": self.model,
                "status": "CONNECTED",
                "message": f"Model '{self.model}' verified and active.",
            }
        except Exception as e:
            msg = self._sanitize_error(str(e))
            msg_lower = msg.lower()
            if any(k in msg_lower for k in ["404", "not_found", "no longer available"]):
                return {
                    "available": False,
                    "model": self.model,
                    "status": "UNAVAILABLE_MODEL_NOT_FOUND",
                    "message": f"Model '{self.model}' is not available (404 Not Found).",
                    "error": msg[:200],
                }
            elif any(k in msg_lower for k in ["api_key_invalid", "permission_denied", "unauthenticated", "401", "403"]):
                return {
                    "available": False,
                    "model": self.model,
                    "status": "AUTH_ERROR",
                    "message": "Gemini API key is invalid or lacks permissions.",
                    "error": msg[:200],
                }
            return {
                "available": False,
                "model": self.model,
                "status": "ERROR",
                "message": f"Failed to verify Gemini model '{self.model}'.",
                "error": msg[:200],
            }

    async def list_available_models(self) -> List[str]:
        """Discovers available models that support generateContent."""
        try:
            models = await asyncio.to_thread(list, self.client.models.list())
            result = []
            for m in models:
                name = getattr(m, "name", "")
                actions = getattr(m, "supported_actions", [])
                if "generateContent" in actions:
                    clean_name = name.replace("models/", "")
                    result.append(clean_name)
            return result
        except Exception as e:
            logger.warning("Failed to list Gemini models: %s", self._sanitize_error(str(e)))
            return []

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a prompt against Gemini and returns parsed JSON.
        Implements bounded retries with exponential backoff for transient failures.
        """
        self.request_count += 1
        req_id = self.request_count
        prompt_chars = len(system_prompt) + len(user_prompt)

        logger.info(
            "[Gemini Request #%d] Dispatching to %s (prompt_chars=%d, temp=%.2f, timeout=%.1fs)",
            req_id,
            self.model,
            prompt_chars,
            self.temperature,
            self.timeout,
        )

        from google.genai import types

        # Configure structured JSON output with system instruction
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self.temperature,
            response_mime_type="application/json",
        )

        last_error = None
        start_time = time.time()

        for attempt in range(self.max_retries + 1):
            try:
                attempt_t0 = time.time()
                # Run the synchronous SDK call in a worker thread to keep the asyncio loop unblocked
                # and enforce timeout safely
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.model,
                        contents=user_prompt,
                        config=config,
                    ),
                    timeout=self.timeout,
                )
                call_duration = time.time() - attempt_t0

                content = response.text or ""
                if not content.strip():
                    raise LLMResponseError("Gemini returned an empty response.")

                # Clean and parse JSON
                cleaned = clean_json_text(content)
                cleaned = repair_common_json_flaws(cleaned)

                try:
                    parsed_json = json.loads(cleaned)
                    if not isinstance(parsed_json, dict):
                        raise LLMSchemaValidationError("Root JSON object is not a dictionary.")
                except (json.JSONDecodeError, ValueError) as parse_err:
                    logger.warning("[Gemini Request #%d] JSON decode failed: %s", req_id, parse_err)
                    raise LLMParseError(f"Failed to parse Gemini output as JSON: {parse_err}")

                # Usage and timing telemetry
                total_duration = round(time.time() - start_time, 2)
                usage_meta = getattr(response, "usage_metadata", None)
                prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) if usage_meta else 0
                eval_tokens = getattr(usage_meta, "candidates_token_count", 0) if usage_meta else 0
                tok_per_sec = round(eval_tokens / call_duration, 1) if call_duration > 0 and eval_tokens else 0.0

                req_stat = {
                    "request_id": req_id,
                    "purpose": "plan_generation",
                    "provider": "gemini",
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
                    "provider": "gemini",
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
                    "[Gemini Request #%d] Succeeded in %.2fs (%d tokens, %.1f tok/s, %d attempts)",
                    req_id,
                    total_duration,
                    eval_tokens,
                    tok_per_sec,
                    attempt + 1,
                )
                return parsed_json

            except asyncio.TimeoutError:
                last_error = LLMTimeoutError(
                    f"Gemini request timed out after {self.timeout}s on attempt {attempt + 1}"
                )
                logger.warning("[Gemini Request #%d] Timeout on attempt %d: %s", req_id, attempt + 1, last_error)

            except (LLMParseError, LLMSchemaValidationError):
                raise

            except Exception as exc:
                sanitized_msg = self._sanitize_error(str(exc))
                exc_lower = sanitized_msg.lower()

                # Permanent failure modes: NEVER retry these
                if any(k in exc_lower for k in ["404", "not_found", "not found", "no longer available"]):
                    logger.error("[Gemini Request #%d] Model '%s' not found or unavailable: %s", req_id, self.model, sanitized_msg)
                    raise LLMModelNotFoundError(f"Gemini model '{self.model}' not found or unavailable: {sanitized_msg[:160]}")
                elif any(k in exc_lower for k in ["api_key_invalid", "permission_denied", "unauthenticated", "invalid api key", "401", "403"]):
                    logger.error("[Gemini Request #%d] Authentication failed: %s", req_id, sanitized_msg)
                    raise LLMAuthError(f"Gemini authentication failed: {sanitized_msg[:140]}")
                elif any(k in exc_lower for k in ["resource_exhausted", "quota", "rate_limit", "429"]):
                    last_error = LLMRateLimitError(f"Gemini rate limit or quota exceeded: {sanitized_msg[:140]}")
                    logger.warning("[Gemini Request #%d] Rate limit on attempt %d: %s", req_id, attempt + 1, sanitized_msg)
                elif any(k in exc_lower for k in ["unavailable", "503", "connection", "deadline"]):
                    last_error = LLMConnectionError(f"Gemini service unavailable: {sanitized_msg[:140]}")
                    logger.warning("[Gemini Request #%d] Transient connection error on attempt %d: %s", req_id, attempt + 1, sanitized_msg)
                else:
                    last_error = LLMResponseError(f"Gemini API error: {sanitized_msg[:140]}")
                    logger.warning("[Gemini Request #%d] API error on attempt %d: %s", req_id, attempt + 1, sanitized_msg)

            # Apply backoff before retrying
            if attempt < self.max_retries:
                backoff = (2 ** attempt) * 1.5
                logger.info("[Gemini Request #%d] Waiting %.1fs before retry attempt %d...", req_id, backoff, attempt + 2)
                await asyncio.sleep(backoff)

        # Exhausted all retries
        raise last_error or LLMResponseError("Gemini request failed after all retry attempts.")
