"""
Knowledge-Guided Pattern Retriever (v2).

Patterns are reusable testing *strategies*, not facts about a target. A pattern is retrieved only
when every capability it requires is present in the current target's intelligence model (which,
for live investigations, contains only structurally evidenced capabilities). Archetype affinity
affects ranking, never eligibility. Whether a retrieved pattern is actually *applicable* is
decided later by the planner, which must resolve every template step against controls observed
on the current target.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Set

from pydantic import BaseModel, Field

from app.intelligence.archetypes import ApplicationCapability, ApplicationIntelligenceModel, WebsiteArchetype
from app.knowledge.repository import DomainKnowledgeRepository, get_knowledge_repository
from app.knowledge.schema import DomainKnowledgePattern

logger = logging.getLogger(__name__)


class RetrievedKnowledgePattern(BaseModel):
    """Encapsulates a matched knowledge pattern with relevance score and provenance."""
    pattern: DomainKnowledgePattern
    relevance_score: float = Field(..., description="Matched affinity score [0.0 - 1.0]")
    matched_capabilities: List[ApplicationCapability] = Field(default_factory=list)
    rationale: str = Field(..., description="Explanation of why this pattern was retrieved")


class RejectedKnowledgePattern(BaseModel):
    pattern_id: str
    reason: str


class KnowledgePatternRetriever:
    """Ranks capability-eligible domain patterns against a target's ApplicationIntelligenceModel."""

    def __init__(self, repository: Optional[DomainKnowledgeRepository] = None):
        self.repository = repository or get_knowledge_repository()
        self.last_rejected: List[RejectedKnowledgePattern] = []

    def retrieve_patterns(
        self,
        model: ApplicationIntelligenceModel,
        min_score: float = 0.3,
        max_patterns: int = 12,
    ) -> List[RetrievedKnowledgePattern]:
        """Retrieves, ranks and returns eligible DomainKnowledgePatterns."""
        all_patterns = self.repository.list_all_patterns()
        results: List[RetrievedKnowledgePattern] = []
        self.last_rejected = []

        model_caps: Set[ApplicationCapability] = set(model.capabilities)
        primary_arch = model.primary_archetype
        secondary_archs = set(model.secondary_archetypes)

        for pat in all_patterns:
            required = set(pat.capabilities)
            missing = required - model_caps
            if missing:
                self.last_rejected.append(RejectedKnowledgePattern(
                    pattern_id=pat.pattern_id,
                    reason=f"required capabilities not evidenced on target: {sorted(c.value for c in missing)}",
                ))
                continue

            score = 0.0
            reasons: List[str] = []
            if required:
                score += 0.55
                reasons.append(f"Capabilities evidenced: {sorted(c.value for c in required)}")
            elif pat.strategy != "TEMPLATE":
                # Universal strategies (e.g. route reachability) need no capability; their
                # applicability is decided entirely by the evidence they are parameterized over.
                score += 0.5
                reasons.append(f"Universal strategy {pat.strategy}")
            else:
                score += 0.2

            if primary_arch in pat.archetypes:
                score += 0.4 * model.confidence_scores.get(primary_arch, 0.8)
                reasons.append(f"Primary archetype {primary_arch.value} match")
            elif any(sa in pat.archetypes for sa in secondary_archs):
                score += 0.2
                reasons.append("Secondary archetype match")
            elif WebsiteArchetype.GENERIC_WEB in pat.archetypes or WebsiteArchetype.AUTHENTICATION in pat.archetypes:
                score += 0.1

            score = min(1.0, round(score, 2))
            if score < min_score:
                self.last_rejected.append(RejectedKnowledgePattern(pattern_id=pat.pattern_id, reason=f"relevance {score} below {min_score}"))
                continue
            results.append(RetrievedKnowledgePattern(
                pattern=pat,
                relevance_score=score,
                matched_capabilities=sorted(required, key=lambda c: c.value),
                rationale="; ".join(reasons) or "Baseline domain relevance",
            ))

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:max_patterns]
