import json
import logging
from typing import List, Optional, Set
from app.knowledge.models import QAPatternDocument, RetrievalResult
from app.knowledge.vector_store import ChromaVectorStore
from app.knowledge.seed_data import SEED_QA_PATTERNS
from app.investigation.models import ObservedFeature

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """
    Hybrid retriever that combines deterministic feature metadata filtering
    with semantic vector similarity matching over ChromaDB.
    """

    def __init__(self, vector_store: Optional[ChromaVectorStore] = None):
        self.store = vector_store or ChromaVectorStore()
        # Ensure knowledge base is initialized
        if self.store.count() == 0:
            self.store.ingest_patterns(SEED_QA_PATTERNS)

    def retrieve(
        self,
        query: str,
        features: Optional[List[ObservedFeature]] = None,
        domain_filter: Optional[str] = None,
        top_k: int = 5,
    ) -> List[RetrievalResult]:
        """
        Retrieves relevant QA patterns:
        1. Feature-based metadata matching
        2. Semantic similarity vector matching
        3. Deduplication and ranking
        """
        results: List[RetrievalResult] = []
        seen_ids: Set[str] = set()

        # 1. Feature metadata-based retrieval
        if features:
            for feat in features:
                f_cat = feat.category.lower()
                for pat in SEED_QA_PATTERNS:
                    if pat.pattern_id in seen_ids:
                        continue
                    if f_cat in pat.domain_tags or f_cat in pat.feature_tags or any(tag in feat.name.lower() for tag in pat.feature_tags):
                        results.append(RetrievalResult(pattern=pat, score=0.95, matched_by="METADATA"))
                        seen_ids.add(pat.pattern_id)
                        if len(results) >= top_k:
                            break
                if len(results) >= top_k:
                    break

        # 2. Semantic query matching via ChromaDB if top_k not reached
        remaining = top_k - len(results)
        if remaining > 0 and query:
            try:
                emb = self.store.embed_provider.get_embedding_sync(query)
                chroma_res = self.store.collection.query(
                    query_embeddings=[emb],
                    n_results=min(remaining + len(seen_ids), self.store.count()),
                )
                if chroma_res and chroma_res.get("metadatas"):
                    matched_metas = chroma_res["metadatas"][0]
                    matched_dists = chroma_res.get("distances", [[]])[0] if chroma_res.get("distances") else []

                    for idx, meta in enumerate(matched_metas):
                        pid = meta.get("pattern_id")
                        if pid and pid not in seen_ids:
                            json_str = meta.get("json_data")
                            if json_str:
                                p_obj = QAPatternDocument.model_validate_json(json_str)
                                dist = matched_dists[idx] if idx < len(matched_dists) else 0.5
                                score = round(max(0.0, 1.0 - (dist / 2.0)), 2)
                                results.append(RetrievalResult(pattern=p_obj, score=score, matched_by="SEMANTIC"))
                                seen_ids.add(pid)
                                if len(results) >= top_k:
                                    break
            except Exception as e:
                logger.warning("Semantic vector retrieval error: %s. Falling back to curated matching.", e)

        # Baseline invariant patterns if none retrieved
        if not results:
            for pat in SEED_QA_PATTERNS:
                if "general" in pat.domain_tags or "security" in pat.domain_tags:
                    results.append(RetrievalResult(pattern=pat, score=0.85, matched_by="METADATA"))
                    seen_ids.add(pat.pattern_id)
                    if len(results) >= 3:
                        break

        return results[:top_k]
