from enum import Enum
from typing import List, Optional, Dict, Any
from typing_extensions import TypedDict
from app.schemas import (
    InspectionResult,
    PageClassification,
    TestCase,
    PlanningMode,
)
from app.agents.models import (
    RequirementAnalysis,
    EvidenceAnalysis,
    RiskAnalysis,
    TestStrategy,
    CritiqueResult,
    ValidationResult,
    CoverageSummary,
)


class WorkflowStatus(str, Enum):
    INITIALIZED = "INITIALIZED"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    REVIEWING = "REVIEWING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FALLBACK = "FALLBACK"
    FAILED = "FAILED"


class AgentWorkflowState(TypedDict, total=False):
    request_id: str
    user_requirements: Optional[List[str]]
    website_description: Optional[str]
    inspection_evidence: InspectionResult
    page_classification: PageClassification

    # Analyses and intermediate stage artifacts
    requirement_analysis: Optional[RequirementAnalysis]
    evidence_analysis: Optional[EvidenceAnalysis]
    risk_analysis: Optional[RiskAnalysis]
    test_strategy: Optional[TestStrategy]
    proposed_test_cases: List[TestCase]
    critique_result: Optional[CritiqueResult]
    validation_result: Optional[ValidationResult]
    coverage_summary: Optional[CoverageSummary]

    # Step 3: Investigation & Vector Knowledge Base
    investigation_report: Optional[Any]
    feature_inventory: Optional[List[Any]]
    retrieved_patterns: Optional[List[Any]]

    # Evidence-grounded planning (Step 2 upgrade)
    evidence_ledger: Optional[List[Any]]    # List[FieldConstraintEvidence]
    test_ideas: Optional[List[Any]]         # List[TestIdea]
    unknowns: Optional[List[Any]]           # List[UnknownItem]
    format_retry_count: Optional[int]

    # Workflow execution controls
    planning_mode: PlanningMode
    warnings: List[str]
    error_code: Optional[str]
    revision_count: int
    max_revisions: int
    current_stage: str
    workflow_status: WorkflowStatus
    stage_timings: Optional[Dict[str, float]]
    ollama_stats: Optional[Dict[str, Any]]
