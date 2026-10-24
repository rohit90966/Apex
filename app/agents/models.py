from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas import Priority, ExpectedBehaviorSource


class RequirementItem(BaseModel):
    id: str
    text: str
    is_explicit: bool = True
    objectives: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    ambiguities: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


class RequirementAnalysis(BaseModel):
    explicit_requirements: List[RequirementItem] = Field(default_factory=list)
    inferred_intent: Optional[str] = None
    overall_ambiguities: List[str] = Field(default_factory=list)
    identified_constraints: List[str] = Field(default_factory=list)
    summary: str = ""


class ObservedTarget(BaseModel):
    element_id: str
    element_type: str
    label_or_name: str
    testable_actions: List[str] = Field(default_factory=list)
    is_observed_fact: bool = True


class EvidenceAnalysis(BaseModel):
    observed_targets: List[ObservedTarget] = Field(default_factory=list)
    unobserved_features: List[str] = Field(default_factory=list)
    missing_context: List[str] = Field(default_factory=list)
    summary: str = ""


class RiskItem(BaseModel):
    risk_id: str
    title: str
    description: str
    category: str  # functional, negative_input, security, usability, accessibility
    severity: Priority
    likelihood: Priority
    target_elements: List[str] = Field(default_factory=list)
    mitigating_scenarios: List[str] = Field(default_factory=list)


class RiskAnalysis(BaseModel):
    risks: List[RiskItem] = Field(default_factory=list)
    unaddressed_requirements: List[str] = Field(default_factory=list)
    priority_rationale: str = ""
    summary: str = ""


class StrategyScenario(BaseModel):
    scenario_id: str
    title: str
    objective: str
    category: str
    priority: Priority
    provenance: ExpectedBehaviorSource
    supporting_evidence: List[str] = Field(default_factory=list)
    mapped_requirement_ids: List[str] = Field(default_factory=list)


class TestStrategy(BaseModel):
    focus_areas: List[str] = Field(default_factory=list)
    scenarios: List[StrategyScenario] = Field(default_factory=list)
    untestable_requirements: List[str] = Field(default_factory=list)
    rationale: str = ""


class CritiqueResult(BaseModel):
    issues_found: bool = False
    issue_categories: List[str] = Field(default_factory=list)
    missing_requirement_ids: List[str] = Field(default_factory=list)
    duplicate_test_case_ids: List[str] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    revision_recommendations: List[str] = Field(default_factory=list)
    review_summary: str = ""
    ready_for_validation: bool = True


class RequirementCoverage(BaseModel):
    requirement_id: str
    requirement_text: str
    status: str  # COVERED, PARTIALLY_COVERED, UNCOVERED, CANNOT_ASSESS
    covering_test_ids: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class CoverageSummary(BaseModel):
    covered_count: int = 0
    partially_covered_count: int = 0
    uncovered_count: int = 0
    cannot_assess_count: int = 0
    total_requirements: int = 0
    details: List[RequirementCoverage] = Field(default_factory=list)


class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    deduplicated_count: int = 0
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
