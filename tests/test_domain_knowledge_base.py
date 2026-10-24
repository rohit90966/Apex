"""
Unit tests for Domain Knowledge Base and Retriever v2.
"""

import pytest
from app.intelligence.archetypes import (
    WebsiteArchetype,
    ApplicationCapability,
    ApplicationIntelligenceModel,
)
from app.knowledge.repository import get_knowledge_repository, DomainKnowledgeRepository
from app.knowledge.retriever_v2 import KnowledgePatternRetriever


def test_knowledge_repository_loading():
    repo = get_knowledge_repository()
    patterns = repo.list_all_patterns()
    assert len(patterns) >= 10

    # Verify E-Commerce patterns
    ecom_patterns = repo.find_by_archetype(WebsiteArchetype.ECOMMERCE)
    assert len(ecom_patterns) >= 5
    assert any(p.pattern_id == "PAT-ECOM-CART-01" for p in ecom_patterns)
    assert any(p.pattern_id == "PAT-ECOM-CHECKOUT-02" for p in ecom_patterns)

    # Verify Auth patterns
    auth_patterns = repo.find_by_capability(ApplicationCapability.USER_AUTHENTICATION)
    assert len(auth_patterns) >= 4
    assert any(p.pattern_id == "PAT-AUTH-LOGIN-01" for p in auth_patterns)


def test_knowledge_pattern_retriever_ranking():
    retriever = KnowledgePatternRetriever()
    ecom_model = ApplicationIntelligenceModel(
        target_url="https://www.saucedemo.com/",
        primary_archetype=WebsiteArchetype.ECOMMERCE,
        capabilities=[
            ApplicationCapability.CATALOG_BROWSING,
            ApplicationCapability.CART_MANAGEMENT,
            ApplicationCapability.CHECKOUT_FLOW,
            ApplicationCapability.USER_AUTHENTICATION,
            ApplicationCapability.PAGINATION_SORTING,
            ApplicationCapability.DRAWER_NAVIGATION,
        ],
        confidence_scores={WebsiteArchetype.ECOMMERCE: 0.95},
    )

    retrieved = retriever.retrieve_patterns(ecom_model)
    assert len(retrieved) >= 6
    # Top patterns should be ecommerce/auth
    assert retrieved[0].relevance_score >= 0.7
    top_ids = [r.pattern.pattern_id for r in retrieved]
    assert "PAT-ECOM-CART-01" in top_ids
    assert "PAT-AUTH-LOGIN-01" in top_ids
