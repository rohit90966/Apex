import os
import json
import logging
from typing import List, Optional, Dict, Any
import chromadb
from chromadb.config import Settings as ChromaSettings
from app.config import get_settings
from app.knowledge.models import QAPatternDocument
from app.knowledge.embeddings import OllamaEmbeddingProvider
from app.knowledge.seed_data import SEED_QA_PATTERNS

logger = logging.getLogger(__name__)

COLLECTION_NAME = "verityqa_patterns"
KNOWLEDGE_VERSION = "1.0.0"


class ChromaVectorStore:
    """
    Persistent local ChromaDB vector store for curated QA testing patterns.
    Implements idempotent ingestion, version metadata validation, and persistent storage.
    """

    def __init__(self, persist_dir: Optional[str] = None):
        settings = get_settings()
        self.persist_dir = persist_dir or os.path.abspath(settings.chroma_persist_dir)
        os.makedirs(self.persist_dir, exist_ok=True)

        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.embed_provider = OllamaEmbeddingProvider()
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"version": KNOWLEDGE_VERSION, "domain": "software_qa"},
        )

    def ingest_patterns(self, patterns: Optional[List[QAPatternDocument]] = None, force_rebuild: bool = False):
        """Idempotently adds patterns to the Chroma collection."""
        docs_to_ingest = patterns or SEED_QA_PATTERNS

        if force_rebuild:
            logger.info("Rebuilding Chroma vector collection...")
            self.client.delete_collection(COLLECTION_NAME)
            self.collection = self.client.create_collection(
                name=COLLECTION_NAME,
                metadata={"version": KNOWLEDGE_VERSION, "domain": "software_qa"},
            )

        existing_ids = set(self.collection.get()["ids"])

        new_docs = [p for p in docs_to_ingest if p.pattern_id not in existing_ids]
        if not new_docs:
            logger.debug("Vector store already up to date (%d patterns indexed).", len(existing_ids))
            return

        logger.info("Indexing %d new QA patterns into ChromaDB...", len(new_docs))
        ids = [p.pattern_id for p in new_docs]
        documents = [p.to_embedding_text() for p in new_docs]
        metadatas = [
            {
                "pattern_id": p.pattern_id,
                "title": p.title,
                "domain_tags": ",".join(p.domain_tags),
                "feature_tags": ",".join(p.feature_tags),
                "technique": p.testing_technique,
                "json_data": p.model_dump_json(),
            }
            for p in new_docs
        ]

        embeddings = [self.embed_provider.get_embedding_sync(doc) for doc in documents]

        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        logger.info("Ingestion complete. Total collection count: %d", self.collection.count())

    def count(self) -> int:
        return self.collection.count()
