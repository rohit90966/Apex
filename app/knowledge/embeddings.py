import logging
import hashlib
from typing import List, Optional
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)


class OllamaEmbeddingProvider:
    """
    Generates text embeddings using the local Ollama API (e.g. nomic-embed-text).
    Includes a deterministic hash-based fallback embedding for offline or test environments.
    Caches model availability so a missing embedding model (HTTP 404) falls back immediately
    without spamming repeated network requests.
    """

    _unavailable_models: set = set()

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_embed_model
        self.dimension = 384  # standard embedding vector size
        self.is_cloud_active = settings.llm_provider.lower() in ("groq", "gemini")

    def _fallback_embedding(self, text: str) -> List[float]:
        """Generates deterministic unit-normalized pseudo-embedding for testing/fallback."""
        h = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [(float(b) - 128.0) / 128.0 for b in h]
        # Extend to dimension
        while len(raw) < self.dimension:
            raw.extend(raw[:min(len(raw), self.dimension - len(raw))])
        raw = raw[:self.dimension]
        # Normalize
        norm = sum(x*x for x in raw) ** 0.5 or 1.0
        return [round(x / norm, 6) for x in raw]

    async def get_embedding(self, text: str) -> List[float]:
        model_key = f"{self.base_url}|{self.model}"
        if self.is_cloud_active or model_key in self._unavailable_models:
            return self._fallback_embedding(text)

        endpoint = f"{self.base_url}/api/embeddings"
        payload = {
            "model": self.model,
            "prompt": text,
        }
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.post(endpoint, json=payload)
            if res.status_code == 200:
                data = res.json()
                emb = data.get("embedding")
                if isinstance(emb, list) and len(emb) > 0:
                    return emb
            if res.status_code == 404:
                self._unavailable_models.add(model_key)
                logger.info("[Embeddings] Ollama embedding model '%s' not installed (404); using deterministic fallback embeddings.", self.model)
            return self._fallback_embedding(text)
        except Exception:
            self._unavailable_models.add(model_key)
            return self._fallback_embedding(text)

    def get_embedding_sync(self, text: str) -> List[float]:
        model_key = f"{self.base_url}|{self.model}"
        if self.is_cloud_active or model_key in self._unavailable_models:
            return self._fallback_embedding(text)

        endpoint = f"{self.base_url}/api/embeddings"
        payload = {
            "model": self.model,
            "prompt": text,
        }
        try:
            with httpx.Client(timeout=2.0) as client:
                res = client.post(endpoint, json=payload)
            if res.status_code == 200:
                data = res.json()
                emb = data.get("embedding")
                if isinstance(emb, list) and len(emb) > 0:
                    return emb
            if res.status_code == 404:
                self._unavailable_models.add(model_key)
                logger.info("[Embeddings] Ollama embedding model '%s' not installed (404); using deterministic fallback embeddings.", self.model)
            return self._fallback_embedding(text)
        except Exception:
            self._unavailable_models.add(model_key)
            return self._fallback_embedding(text)
