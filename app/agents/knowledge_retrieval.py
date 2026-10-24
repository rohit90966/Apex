import logging
from typing import List, Optional
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.knowledge.retriever import KnowledgeRetriever
from app.knowledge.models import RetrievalResult

logger = logging.getLogger(__name__)


class KnowledgeRetrievalNode:
    """
    Retrieval-Augmented Node: Retrieves domain-relevant QA test patterns
    from the local ChromaDB vector store based on detected features, requirements, and page context.
    """

    def __init__(self, retriever: Optional[KnowledgeRetriever] = None):
        self.retriever = retriever or KnowledgeRetriever()

    def run(self, state: AgentWorkflowState) -> dict:
        features = state.get("feature_inventory") or []
        req_analysis = state.get("requirement_analysis")
        desc = state.get("website_description") or ""
        classification = state.get("page_classification")
        inspection = state.get("inspection_evidence")

        # Build retrieval query from user requirements, description, or page context
        query_parts = []
        if req_analysis and req_analysis.explicit_requirements:
            query_parts.extend([r.text for r in req_analysis.explicit_requirements])
        if desc:
            query_parts.append(desc)

        # Fallback to rich contextual query if requirements and description are blank
        if not query_parts:
            cat = classification.page_category.value if classification else "GENERAL"
            title = inspection.page_title if inspection else ""
            query_parts.append(f"{cat} {title}")
            for feat in features[:3]:
                feat_name = getattr(feat, "name", "")
                feat_cat = getattr(feat, "category", "")
                if feat_name or feat_cat:
                    query_parts.append(f"{feat_cat} {feat_name}")

        query = " ".join(query_parts).strip()

        # Retrieve matching QA patterns
        retrieved_results = self.retriever.retrieve(
            query=query,
            features=features,
            top_k=5,
        )

        patterns = [r.pattern for r in retrieved_results]
        logger.info(
            "KnowledgeRetrievalNode retrieved %d QA pattern(s) using query '%s' and %d feature(s).",
            len(patterns),
            query[:60],
            len(features),
        )

        return {
            "retrieved_patterns": patterns,
            "current_stage": "KNOWLEDGE_RETRIEVAL",
            "workflow_status": WorkflowStatus.ANALYZING,
        }
