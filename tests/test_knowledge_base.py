import pytest
import shutil
import tempfile
from app.knowledge.models import QAPatternDocument
from app.knowledge.vector_store import ChromaVectorStore
from app.knowledge.retriever import KnowledgeRetriever
from app.investigation.models import ObservedFeature


@pytest.fixture
def temp_chroma_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_chroma_vector_store_ingestion_and_idempotence(temp_chroma_dir):
    store = ChromaVectorStore(persist_dir=temp_chroma_dir)
    pattern = QAPatternDocument(
        pattern_id="PAT-TEST-001",
        title="Test Invariant Pattern",
        description="Verify test invariant",
        domain_tags=["test", "general"],
        feature_tags=["button"],
        testing_technique="security_invariant",
        scenario_guidance="Inspect button",
        expected_result_guidance="Button works",
        risk_guidance="Risk",
    )
    store.ingest_patterns([pattern])
    assert store.count() == 1

    # Idempotent re-ingestion
    store.ingest_patterns([pattern])
    assert store.count() == 1


def test_knowledge_retriever_hybrid_and_feature_matching(temp_chroma_dir):
    store = ChromaVectorStore(persist_dir=temp_chroma_dir)
    retriever = KnowledgeRetriever(vector_store=store)

    feat = ObservedFeature(
        feature_id="f-auth",
        name="User Authentication",
        category="auth",
        description="Login form",
    )
    results = retriever.retrieve(query="login password", features=[feat], top_k=3)
    assert len(results) > 0
    # Should match password masking pattern from seed data
    pattern_ids = [r.pattern.pattern_id for r in results]
    assert any("PAT-AUTH" in pid for pid in pattern_ids)
