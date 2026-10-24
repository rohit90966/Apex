import os
import logging
from typing import Optional
from app.config import get_settings
from app.llm.base import BaseLLMProvider, LLMAuthError

logger = logging.getLogger(__name__)


def get_llm_provider(
    provider_type: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> BaseLLMProvider:
    """
    Central factory for obtaining the configured LLM provider instance.
    Defaults to Groq ('groq') with llama-3.3-70b-versatile.
    """
    settings = get_settings()
    selected = (provider_type or settings.llm_provider).strip().lower()

    if selected == "groq":
        from app.llm.groq_provider import GroqProvider
        active_model = model or getattr(settings, "groq_model", "llama-3.3-70b-versatile") or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        logger.info("Initializing Groq provider (model=%s)", active_model)
        return GroqProvider(
            api_key=api_key or getattr(settings, "groq_api_key", None) or os.environ.get("GROQ_API_KEY"),
            model=active_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=getattr(settings, "groq_temperature", 0.1),
            max_retries=settings.llm_max_retries,
        )

    elif selected == "gemini":
        from app.llm.gemini_provider import GeminiProvider
        logger.info("Initializing Google Gemini provider (model=%s)", model or settings.gemini_model)
        return GeminiProvider(
            api_key=api_key or settings.google_api_key or os.environ.get("GOOGLE_API_KEY"),
            model=model or settings.gemini_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.gemini_temperature,
            max_retries=settings.llm_max_retries,
        )

    elif selected == "ollama":
        from app.llm.ollama_provider import OllamaProvider
        logger.info("Initializing local Ollama provider (model=%s, url=%s)", settings.ollama_model, settings.ollama_base_url)
        return OllamaProvider()

    else:
        raise ValueError(
            f"Unsupported LLM provider: '{selected}'. Supported providers are 'groq', 'gemini', and 'ollama'."
        )
