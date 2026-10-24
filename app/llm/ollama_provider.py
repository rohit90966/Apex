import json
import asyncio
import logging
import re
from typing import Dict, Any, Optional
import httpx
from app.config import get_settings
from app.llm.base import (
    BaseLLMProvider,
    LLMConnectionError,
    LLMTimeoutError,
    LLMResponseError,
    LLMResponseTruncatedError,
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


class OllamaProvider(BaseLLMProvider):
    """
    Local Ollama API integration using httpx.
    Supports structured JSON generation, truncation detection, and bounded 1-step repair.
    """

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        # None => no artificial inference deadline (patience-first local reasoning).
        self.timeout = settings.ollama_timeout_seconds
        self.connect_timeout = getattr(settings, "ollama_connect_timeout_seconds", 10.0)
        self.num_predict = getattr(settings, "ollama_num_predict", 1500)
        self.num_ctx = getattr(settings, "ollama_num_ctx", 4096)
        self.max_format_retries = getattr(settings, "ollama_max_format_retries", 1)
        self.last_format_retries = 0
        self.request_count = 0
        self.requests_log = []
        self.last_call_stats = {}

    def _httpx_timeout(self) -> httpx.Timeout:
        """
        httpx documented form: Timeout(None, connect=X) disables read/write/pool deadlines while keeping a
        bounded connect phase, so an unreachable server surfaces as LLMConnectionError instead of hanging.
        """
        return httpx.Timeout(self.timeout, connect=self.connect_timeout)

    async def _post_chat(self, client: httpx.AsyncClient, payload: dict) -> dict:
        endpoint = f"{self.base_url}/api/chat"
        response = await client.post(endpoint, json=payload)

        # If format schema was rejected by Ollama version, fallback to generic json format
        if response.status_code == 400 and isinstance(payload.get("format"), dict):
            logger.warning("Ollama rejected schema format, retrying with format='json'.")
            fallback_payload = dict(payload)
            fallback_payload["format"] = "json"
            response = await client.post(endpoint, json=fallback_payload)

        if response.status_code != 200:
            raise LLMResponseError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:150]}"
            )

        data = response.json()
        return data

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.last_format_retries = 0
        self.request_count += 1
        req_id = self.request_count
        prompt_chars = len(system_prompt) + len(user_prompt)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": schema if schema else "json",
            "options": {
                "temperature": 0.1,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }

        import time
        start_time = time.time()
        logger.info(
            "[Ollama Request #%d] Dispatched to %s (prompt=%d chars, num_predict=%d, num_ctx=%d)",
            req_id,
            self.model,
            prompt_chars,
            self.num_predict,
            self.num_ctx,
        )

        try:
            async with httpx.AsyncClient(timeout=self._httpx_timeout()) as client:
                data = await self._post_chat(client, payload)
                call_duration = time.time() - start_time

                # 1. Truncation Detection
                done_reason = data.get("done_reason")
                if done_reason == "length":
                    logger.error(
                        "[Ollama Request #%d] Truncated: token budget of %d reached (done_reason=length).",
                        req_id,
                        self.num_predict,
                    )
                    raise LLMResponseTruncatedError(
                        f"Model output truncated: generation reached token budget of {self.num_predict} tokens."
                    )

                message = data.get("message", {})
                content = message.get("content", "")

                eval_count = data.get("eval_count", 0)
                eval_duration_sec = data.get("eval_duration", 0) / 1e9
                tok_per_sec = round(eval_count / eval_duration_sec, 1) if eval_duration_sec > 0 else 0.0
                prompt_eval_count = data.get("prompt_eval_count", 0)

                req_stat = {
                    "request_id": req_id,
                    "purpose": "plan_generation",
                    "model": self.model,
                    "prompt_size_chars": prompt_chars,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "duration_seconds": round(call_duration, 2),
                    "tokens_per_second": tok_per_sec,
                    "done_reason": done_reason or "stop",
                }
                self.requests_log.append(req_stat)

                self.last_call_stats = {
                    "model": self.model,
                    "configured_num_predict": self.num_predict,
                    "configured_num_ctx": self.num_ctx,
                    "prompt_size_chars": prompt_chars,
                    "prompt_eval_count": prompt_eval_count,
                    "eval_count": eval_count,
                    "duration_seconds": round(call_duration, 2),
                    "tokens_per_second": tok_per_sec,
                    "tokens_per_sec": tok_per_sec,
                    "done_reason": done_reason or "stop",
                    "format_retries": 0,
                    "json_repair_occurred": False,
                    "json_repair_duration_seconds": 0.0,
                    "total_requests": 1,
                    "requests_detail": [req_stat],
                }

                logger.info(
                    "[Ollama Request #%d] Completed in %.2fs: %d output tokens (%.1f tok/s, prompt_eval=%d tokens, done_reason=%s)",
                    req_id,
                    call_duration,
                    eval_count,
                    tok_per_sec,
                    prompt_eval_count,
                    done_reason or "stop",
                )

                if not content or not content.strip():
                    raise LLMResponseError("Ollama returned empty response message content.")

                # 2. Extract and sanitize JSON
                cleaned = clean_json_text(content)
                cleaned = repair_common_json_flaws(cleaned)

                try:
                    parsed_json = json.loads(cleaned)
                    if not isinstance(parsed_json, dict):
                        raise LLMSchemaValidationError("Root JSON object is not a dictionary.")
                    return parsed_json
                except (json.JSONDecodeError, ValueError) as parse_err:
                    # 3. Targeted 1-step format repair if budget permits
                    if self.max_format_retries > 0:
                        self.request_count += 1
                        repair_req_id = self.request_count
                        logger.warning(
                            "[Ollama Request #%d - JSON Repair] Initial parse failed (%s). Attempting repair.",
                            repair_req_id,
                            parse_err,
                        )
                        repair_payload = {
                            "model": self.model,
                            "messages": [
                                {"role": "system", "content": "You are a JSON repair tool. Return ONLY strictly valid JSON without explanation."},
                                {"role": "user", "content": f"The following JSON text has a syntax error: {parse_err}\n\nMalformed text:\n{content[:2500]}\n\nFix the syntax and output strictly valid JSON only."},
                            ],
                            "stream": False,
                            "format": "json",
                            "options": {
                                "temperature": 0.0,
                                "num_predict": min(self.num_predict, 1000),
                                "num_ctx": self.num_ctx,
                            },
                        }
                        try:
                            rep_t0 = time.time()
                            repair_data = await self._post_chat(client, repair_payload)
                            rep_dur = time.time() - rep_t0
                            repair_content = repair_data.get("message", {}).get("content", "")
                            repaired_cleaned = repair_common_json_flaws(clean_json_text(repair_content))
                            repaired_json = json.loads(repaired_cleaned)
                            if isinstance(repaired_json, dict):
                                self.last_format_retries = 1
                                rep_eval = repair_data.get("eval_count", 0)
                                rep_stat = {
                                    "request_id": repair_req_id,
                                    "purpose": "json_repair",
                                    "model": self.model,
                                    "prompt_size_chars": len(str(repair_payload)),
                                    "eval_count": rep_eval,
                                    "duration_seconds": round(rep_dur, 2),
                                    "done_reason": repair_data.get("done_reason", "stop"),
                                }
                                self.requests_log.append(rep_stat)
                                self.last_call_stats["format_retries"] = 1
                                self.last_call_stats["json_repair_occurred"] = True
                                self.last_call_stats["json_repair_duration_seconds"] = round(rep_dur, 2)
                                self.last_call_stats["duration_seconds"] = round(call_duration + rep_dur, 2)
                                self.last_call_stats["total_requests"] = 2
                                self.last_call_stats["requests_detail"].append(rep_stat)
                                logger.info(
                                    "[Ollama Request #%d - JSON Repair] Succeeded in %.2fs (%d tokens). Total LLM duration: %.2fs",
                                    repair_req_id,
                                    rep_dur,
                                    rep_eval,
                                    call_duration + rep_dur,
                                )
                                return repaired_json
                        except Exception as repair_exc:
                            logger.warning(
                                "[Ollama Request #%d - JSON Repair] Repair failed: %s",
                                repair_req_id,
                                repair_exc,
                            )

                    raise LLMParseError(f"Failed to parse model output as JSON: {parse_err}")

        except httpx.ConnectError as e:
            logger.warning("Failed to connect to Ollama at %s: %s", self.base_url, e)
            raise LLMConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. Ensure 'ollama serve' is running."
            )
        except httpx.ConnectTimeout as e:
            logger.warning("Connecting to Ollama at %s timed out after %ss: %s", self.base_url, self.connect_timeout, e)
            raise LLMConnectionError(
                f"Timed out connecting to Ollama at {self.base_url} (connect phase only, {self.connect_timeout}s)."
            )
        except httpx.TimeoutException as e:
            # Only reachable when an explicit OLLAMA_TIMEOUT_SECONDS deadline was configured by the operator.
            logger.warning("Ollama query exceeded operator-configured deadline of %s seconds: %s", self.timeout, e)
            raise LLMTimeoutError(f"Ollama request exceeded configured deadline of {self.timeout}s.")
        except (LLMResponseError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error("Unexpected error querying Ollama: %s", e)
            raise LLMResponseError(f"Unexpected error communicating with Ollama: {str(e)[:120]}")
