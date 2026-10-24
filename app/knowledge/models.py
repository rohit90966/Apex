from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class QAPatternDocument(BaseModel):
    __test__ = False
    pattern_id: str
    title: str
    description: str
    domain_tags: List[str] = Field(default_factory=list)  # auth, ecommerce, fintech, booking, general, saas
    feature_tags: List[str] = Field(default_factory=list)  # password, search, cart, checkout, table, api
    testing_technique: str  # boundary_value, equivalence_partitioning, security_invariant, negative_input, accessibility
    applicability_conditions: List[str] = Field(default_factory=list)
    required_evidence: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    scenario_guidance: str
    expected_result_guidance: str
    risk_guidance: str
    known_assumptions: List[str] = Field(default_factory=list)
    provenance: str = "CURATED_QA_STANDARD"
    version: str = "1.0.0"

    def to_embedding_text(self) -> str:
        """Serializes document into a high-signal text representation for vector embedding."""
        return (
            f"Title: {self.title}\n"
            f"Domain: {', '.join(self.domain_tags)}\n"
            f"Features: {', '.join(self.feature_tags)}\n"
            f"Technique: {self.testing_technique}\n"
            f"Description: {self.description}\n"
            f"Scenario: {self.scenario_guidance}\n"
            f"Expected: {self.expected_result_guidance}\n"
            f"Risk: {self.risk_guidance}"
        )


class RetrievalResult(BaseModel):
    __test__ = False
    pattern: QAPatternDocument
    score: float = 1.0
    matched_by: str = "HYBRID"  # SEMANTIC, METADATA, or HYBRID
