"""
Schema definitions for the VerityQA Domain Knowledge Base.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.intelligence.archetypes import WebsiteArchetype, ApplicationCapability


class ScenarioTemplateStep(BaseModel):
    step_number: int
    action_type: str
    target_semantic: str
    target_selector_hints: List[str] = Field(default_factory=list)
    input_value: Optional[str] = None
    expected_state_or_fragment: Optional[str] = None
    assertion_type: str = "VISIBLE"
    assertion_expected: Optional[str] = None
    description: str


class ScenarioTemplate(BaseModel):
    template_id: str
    title: str
    objective: str
    category: str = "functional"
    steps: List[ScenarioTemplateStep] = Field(default_factory=list)
    assertions: List[Dict[str, Any]] = Field(default_factory=list)


class NegativeScenarioSpec(BaseModel):
    scenario_id: str
    title: str
    objective: str
    boundary_type: str
    invalid_input: Dict[str, Any] = Field(default_factory=dict)
    expected_error_message_fragment: str
    expected_status_code_or_url: Optional[str] = None


QA_KNOWLEDGE_SCHEMA_VERSION = "1.0"
QA_KNOWLEDGE_KINDS = ("DEFECT_CLASS", "TEST_STRATEGY", "ORACLE", "APP_PATTERN")


class Applicability(BaseModel):
    """Predicates over the evidence profile of a target (see app/knowledge/qa_retrieval.py)."""
    all: List[str] = Field(default_factory=list, description="Every predicate must hold")
    any: List[str] = Field(default_factory=list, description="At least one predicate must hold (if non-empty)")
    none: List[str] = Field(default_factory=list, description="Contradicting evidence: entry is excluded if any holds")


class Automation(BaseModel):
    status: str = Field(default="NOT_AUTOMATED", description="AUTOMATED | PARTIAL | NOT_AUTOMATED")
    implementation: Optional[str] = Field(default=None, description="Planner strategy key implementing this entry")


class QAKnowledgeEntry(BaseModel):
    """
    One structured unit of QA knowledge: a defect class, a test-design strategy, a verification oracle,
    or an application/workflow pattern. Entries are hypotheses about what is worth testing; they never
    assert that a target has a defect.
    """
    id: str
    kind: str
    category: str
    title: str
    description: str
    applicability: Applicability = Field(default_factory=Applicability)
    preconditions: List[str] = Field(default_factory=list)
    required_observations: List[str] = Field(default_factory=list)
    hypothesis_method: str = ""
    test_strategy: str = ""
    oracle_strategy: str = ""
    related_ids: List[str] = Field(default_factory=list, description="Linked defect classes / strategies / oracles")
    expected_evidence: List[str] = Field(default_factory=list)
    false_positive_warnings: List[str] = Field(default_factory=list)
    safety: List[str] = Field(default_factory=list)
    confidence_requirements: str = ""
    limitations: List[str] = Field(default_factory=list)
    regression_template: Optional[str] = None
    automation: Automation = Field(default_factory=Automation)
    risk: str = "MEDIUM"
    provenance: str = "VerityQA QA knowledge base (curated from standard test-design practice)"
    version: str = "1.0.0"
    review_status: str = "REVIEWED"


class DomainKnowledgePattern(BaseModel):
    """
    Structured domain knowledge pattern representing testing invariants,
    scenario templates, risk classifications, and validation heuristics.
    """
    pattern_id: str = Field(..., description="Unique pattern identifier (e.g. PAT-ECOM-CART-01)")
    name: str = Field(..., description="Human-readable pattern title")
    archetypes: List[WebsiteArchetype] = Field(..., description="Target archetypes where this pattern applies")
    capabilities: List[ApplicationCapability] = Field(default_factory=list, description="Required capabilities")
    description: str = Field(..., description="Detailed description of the behavioral contract")
    preconditions: List[str] = Field(default_factory=list, description="Required setup or state preconditions")
    risk_level: str = Field(default="MEDIUM", description="Risk level (LOW, MEDIUM, HIGH, CRITICAL)")
    scenario_templates: List[ScenarioTemplate] = Field(default_factory=list, description="Concrete scenario blueprints")
    invariants: List[str] = Field(default_factory=list, description="Domain invariants that must hold true")
    negative_scenarios: List[NegativeScenarioSpec] = Field(default_factory=list, description="Boundary/negative cases")
    evidence_requirements: List[str] = Field(default_factory=list, description="Evidence to collect during test execution")
    version: str = Field(default="1.0.0", description="Semantic pattern version")
    # How the planner applies the pattern: TEMPLATE (resolve scenario_templates against observed
    # controls) or a parameterized strategy implemented by the planner over observed evidence:
    # ROUTE_REACHABILITY | SORT_ORDER | FILTER_RESULTS | FIELD_BOUNDARIES | SUBMISSION_REFLECTION.
    strategy: str = Field(default="TEMPLATE")
    objective: str = Field(default="", description="Intent of the strategy, independent of any site")
    required_evidence: List[str] = Field(default_factory=list, description="Observations a target must expose before the pattern applies")
    oracle_requirements: List[str] = Field(default_factory=list, description="What must be known to judge the outcome")
    safety_constraints: List[str] = Field(default_factory=list)
    # Assumptions inherited from the site the template was written for. They are never treated
    # as facts: a template step or expectation is used only when grounded on the current target.
    domain_assumptions: List[str] = Field(default_factory=list)
