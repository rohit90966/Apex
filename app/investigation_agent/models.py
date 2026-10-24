"""
Data models and schemas for the Stateful Investigation Agent.
Defines the Application State Graph, Investigation Frontier, Evidence Ledger,
Hypothesis/Rule Registry, and the 10-section Investigation Report.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
import time
import uuid


class ActionType(str, Enum):
    NAVIGATE_LINK = "NAVIGATE_LINK"
    CLICK_CONTROL = "CLICK_CONTROL"
    FILL = "FILL"
    SELECT_OPTION = "SELECT_OPTION"
    CHECK = "CHECK"
    UNCHECK = "UNCHECK"
    PRESS = "PRESS"
    SUBMIT = "SUBMIT"
    SET_NOVALIDATE = "SET_NOVALIDATE"
    RELOAD = "RELOAD"
    GO_BACK = "GO_BACK"


class ValueSpecType(str, Enum):
    LITERAL = "literal"
    UNIQUE_EMAIL = "unique_email"
    VALID_PASSWORD = "valid_password"
    TEXT_OF_LENGTH = "text_of_length"
    BLANK = "blank"
    MISMATCH_OF = "mismatch_of"
    CONSTRAINT_EDGE = "constraint_edge"


class ValueSpec(BaseModel):
    kind: ValueSpecType = ValueSpecType.LITERAL
    value: Optional[str] = None
    length: Optional[int] = None
    ref_field: Optional[str] = None
    edge_offset: int = 0
    constraint_type: Optional[str] = None  # "minlength", "maxlength", "min", "max"


class SemanticTarget(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    css: Optional[str] = None
    resolved_strategy: Optional[str] = None


class ProbeStep(BaseModel):
    step_id: str = Field(default_factory=lambda: f"step_{uuid.uuid4().hex[:6]}")
    action_type: str
    target: Optional[SemanticTarget] = None
    raw_target: Optional[str] = None
    value_spec: Optional[ValueSpec] = None
    resolved_value: Optional[str] = None
    key_to_press: Optional[str] = None
    required_url: Optional[str] = None
    required_state_id: Optional[str] = None
    is_prerequisite: bool = False


class OracleType(str, Enum):
    DECLARED = "DECLARED"
    TECHNICAL = "TECHNICAL"
    CONTRADICTION = "CONTRADICTION"
    CONVENTION = "CONVENTION"
    SUSPICION = "SUSPICION"


class OracleStrength(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ComparatorVerdict(str, Enum):
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    CONTRADICTED = "CONTRADICTED"
    NO_EFFECT = "NO_EFFECT"
    UNEXPECTED = "UNEXPECTED"


class FormSubmitOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class JourneyStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ABANDONED = "ABANDONED"


class JourneyStepStatus(str, Enum):
    PLANNED = "PLANNED"
    ATTEMPTED = "ATTEMPTED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class JourneyStep(BaseModel):
    step_index: int
    name: str
    action_type: str = "CLICK_CONTROL"
    target_pattern: Optional[str] = None
    expected_role: Optional[str] = None
    required_state_class: Optional[str] = None
    is_hypothesis: bool = False
    is_required: bool = True
    status: JourneyStepStatus = JourneyStepStatus.PLANNED
    completed: bool = False  # Kept for backward compatibility; True ONLY when status == JourneyStepStatus.VERIFIED
    executed_question_id: Optional[str] = None
    executed_target: Optional[str] = None
    expected_postcondition: Optional[str] = None
    before_value: Optional[str] = None
    after_value: Optional[str] = None
    verification_details: Optional[str] = None
    failure_reason: Optional[str] = None
    evidence_id: Optional[str] = None
    adjudication_reason: Optional[str] = None


class UserJourney(BaseModel):
    journey_id: str
    name: str
    description: str
    steps: List[JourneyStep] = Field(default_factory=list)
    current_step_index: int = 0
    status: JourneyStatus = JourneyStatus.NOT_STARTED
    prerequisites_satisfied: bool = True
    interruption_reason: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)

    @property
    def planned_steps_count(self) -> int:
        return len(self.steps)

    @property
    def verified_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == JourneyStepStatus.VERIFIED)

    @property
    def failed_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == JourneyStepStatus.FAILED)

    @property
    def skipped_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == JourneyStepStatus.SKIPPED)

    @property
    def not_applicable_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == JourneyStepStatus.NOT_APPLICABLE)

    @property
    def is_fully_completed(self) -> bool:
        """
        A journey is complete ONLY when every required step is VERIFIED
        or explicitly adjudicated as NOT_APPLICABLE with documented justification.
        Never counts skipped, failed, or unverified steps as completed.
        """
        if not self.steps:
            return False
        for s in self.steps:
            if getattr(s, "is_required", True):
                if s.status != JourneyStepStatus.VERIFIED and s.status != JourneyStepStatus.NOT_APPLICABLE:
                    return False
        return True


class Prediction(BaseModel):
    expect: str
    signals: List[str] = Field(default_factory=list)


class OutcomeComparison(BaseModel):
    verdict: ComparatorVerdict
    execution_status: str = "EXECUTED"
    observed_effect: str = "NO_OBSERVABLE_CHANGE"  # "ROUTE_TRANSITION", "IN_PAGE_STATE_MUTATION", "VALIDATION_FEEDBACK", "NO_OBSERVABLE_CHANGE"
    transition_verified: bool = False
    matched_signals: List[str] = Field(default_factory=list)
    missing_signals: List[str] = Field(default_factory=list)
    extra_signals: List[str] = Field(default_factory=list)
    summary: str = ""


class OracleFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: f"orc_{uuid.uuid4().hex[:8]}")
    oracle_type: OracleType
    strength: OracleStrength
    title: str
    description: str
    evidence_refs: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class ReproductionOutcome(str, Enum):
    CONFIRMED_DEFECT = "CONFIRMED_DEFECT"
    REPRODUCTION_SETUP_FAILED = "REPRODUCTION_SETUP_FAILED"
    ACTION_EXECUTION_FAILED = "ACTION_EXECUTION_FAILED"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    INCONCLUSIVE = "INCONCLUSIVE"


class BugLead(BaseModel):
    id: str = Field(default_factory=lambda: f"lead_{uuid.uuid4().hex[:8]}")
    title: str
    oracle_type: OracleType
    strength: OracleStrength
    probe_family: str
    question_id: str
    state_class: str = ""
    source_state_id: Optional[str] = None
    source_url: Optional[str] = None
    target_desc: str = ""
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    setup_steps: List[Dict[str, Any]] = Field(default_factory=list)
    expected: Dict[str, str] = Field(default_factory=dict)  # {"text": ..., "provenance": ...}
    actual: Dict[str, str] = Field(default_factory=dict)    # {"text": ...}
    evidence_ids: List[str] = Field(default_factory=list)
    before_screenshot_id: Optional[str] = None
    after_screenshot_id: Optional[str] = None
    network_excerpt: Optional[Dict[str, Any]] = None
    console_excerpt: Optional[List[str]] = None
    severity_guess: str = "MEDIUM"
    status: str = "LEAD"  # "LEAD", "CONFIRMED", "UNSTABLE", "REJECTED"
    confirmation: Dict[str, Any] = Field(default_factory=lambda: {"runs": 0, "passed": 0})
    reproduction_outcome: Optional[ReproductionOutcome] = None
    reproduction_runs: List[Dict[str, Any]] = Field(default_factory=list)
    reproduction_failure_reason: Optional[str] = None

    @property
    def lead_id(self) -> str:
        return self.id

    @property
    def severity(self) -> str:
        return self.severity_guess


class ProbeScript(BaseModel):
    script_id: str = Field(default_factory=lambda: f"probe_{uuid.uuid4().hex[:8]}")
    name: str
    family: str
    description: str = ""
    steps: List[ProbeStep] = Field(default_factory=list)


class FrontierStatus(str, Enum):
    DISCOVERED = "discovered"
    PARTIALLY_INVESTIGATED = "partially_investigated"
    INVESTIGATED = "investigated"
    BLOCKED = "blocked"
    UNREACHABLE = "unreachable"
    DEFERRED = "deferred"
    STALE_INCOMPATIBLE = "stale_incompatible"
    UNKNOWN = "unknown"


class ActionFailureReason(str, Enum):
    STALE_TARGET = "stale_target"
    TARGET_ABSENT = "target_absent"
    TARGET_DISABLED = "target_disabled"
    TARGET_AMBIGUOUS = "target_ambiguous"
    INCOMPATIBLE_ACTION = "incompatible_action"
    TIMEOUT = "timeout"
    SAFETY_BLOCKED = "safety_blocked"
    NAVIGATION_REJECTED = "navigation_rejected"
    EXECUTION_ERROR = "execution_error"
    PRECONDITION_FAILED = "precondition_failed"


class InvestigationStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BLOCKED = "blocked"
    ERROR = "error"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNSAFE_STOPPED = "unsafe_stopped"


class ActionRisk(str, Enum):
    SAFE_READ = "safe_read"
    REVERSIBLE_INTERACTION = "reversible_interaction"
    POTENTIALLY_MUTATING = "potentially_mutating"
    UNSAFE_EXTERNAL = "unsafe_external"


class RuleCategory(str, Enum):
    EXPLICIT_REQUIREMENT = "explicit_requirement"
    OBSERVED_BEHAVIOR = "observed_behavior"
    INFERRED_HYPOTHESIS = "inferred_hypothesis"
    UNKNOWN = "unknown"


class ElementReference(BaseModel):
    element_id: str
    role: str
    accessible_name: str = ""
    tag_name: str
    selector: str
    element_type: Optional[str] = None
    is_enabled: bool = True
    is_visible: bool = True
    declared_constraints: Dict[str, Any] = Field(default_factory=dict)
    candidate_actions: List[str] = Field(default_factory=list)


class NetworkEvent(BaseModel):
    seq: int
    method: str
    url: str
    resource_type: str
    status: Optional[int] = None
    ok: bool = True
    duration_ms: Optional[float] = None
    failure: Optional[str] = None
    response_snippet: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class ConsoleOrPageError(BaseModel):
    seq: int
    error_type: str  # "console" or "pageerror"
    message: str
    timestamp: float = Field(default_factory=time.time)


class UIStateObservation(BaseModel):
    state_id: str
    state_class_id: str = ""
    url: str
    normalized_route: str
    page_title: str
    http_status: Optional[int] = None
    headings: List[str] = Field(default_factory=list)
    interactive_controls: List[ElementReference] = Field(default_factory=list)
    forms: List[Dict[str, Any]] = Field(default_factory=list)
    navigation_links: List[Dict[str, Any]] = Field(default_factory=list)
    visible_text_snippet: str = ""
    dynamic_signals: Dict[str, Any] = Field(default_factory=dict)  # e.g., active_tab, modals, menu_open
    has_validation_error: bool = False
    validation_error_message: Optional[str] = None
    validation_error_locator: Optional[str] = None
    menu_open: bool = False
    is_authenticated: bool = False
    a11y_snapshot: Optional[str] = None
    text_anomalies: List[str] = Field(default_factory=list)
    storage_keys: Dict[str, List[str]] = Field(default_factory=dict)
    console_error_messages: List[str] = Field(default_factory=list)
    screenshot_artifact_id: Optional[str] = None
    captured_at: float = Field(default_factory=time.time)


class StateDiff(BaseModel):
    route_changed: bool = False
    class_changed: bool = False
    state_id_changed: bool = False
    text_added: List[str] = Field(default_factory=list)
    text_removed: List[str] = Field(default_factory=list)
    alerts_added: List[str] = Field(default_factory=list)
    invalid_fields_delta: Dict[str, Any] = Field(default_factory=dict)
    counter_delta: Optional[int] = None
    controls_added: List[str] = Field(default_factory=list)
    controls_removed: List[str] = Field(default_factory=list)
    auth_delta: Dict[str, Any] = Field(default_factory=dict)
    network_summary: Dict[str, Any] = Field(default_factory=dict)
    console_errors_count: int = 0
    text_anomalies_added: List[str] = Field(default_factory=list)

    @property
    def is_significant(self) -> bool:
        return bool(
            self.route_changed
            or self.class_changed
            or self.state_id_changed
            or self.alerts_added
            or self.controls_added
            or self.controls_removed
            or (self.counter_delta is not None and self.counter_delta != 0)
        )


class ProbeResult(BaseModel):
    script_id: str
    status: str  # "SUCCESS", "FAILED", "SAFETY_BLOCKED"
    steps_executed: int = 0
    before_obs: Optional[UIStateObservation] = None
    after_obs: Optional[UIStateObservation] = None
    diff: Optional[StateDiff] = None
    network_events: List[NetworkEvent] = Field(default_factory=list)
    console_errors: List[ConsoleOrPageError] = Field(default_factory=list)
    mutations: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    script: Optional[Any] = None


class StateTransitionRecord(BaseModel):
    transition_id: str = Field(default_factory=lambda: f"trans-{uuid.uuid4().hex[:8]}")
    source_state_id: str
    source_observation_id: Optional[str] = None
    action_type: str
    target_element_desc: str
    action_purpose: str = ""
    preconditions: List[str] = Field(default_factory=list)
    actual_outcome: str
    destination_state_id: Optional[str] = None
    destination_observation_id: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    status: str = "confirmed"  # confirmed, no_effect, failed, safety_blocked, uncertain
    execution_status: str = "EXECUTED"  # EXECUTED, FAILED, SAFETY_BLOCKED
    observed_effect: str = "NO_OBSERVABLE_CHANGE"  # ROUTE_TRANSITION, IN_PAGE_STATE_MUTATION, VALIDATION_FEEDBACK, NO_OBSERVABLE_CHANGE
    prediction_verdict: str = "MATCHED"
    transition_verified: bool = False
    confidence: float = 1.0     # 1.0 for verified DOM evidence, <1.0 for uncertain inferences
    is_successful: bool = True
    is_route_change: bool = False
    is_in_page_mutation: bool = False
    failure_reason: Optional[ActionFailureReason] = None
    side_effects: Optional[str] = None
    executed_at: float = Field(default_factory=time.time)


class FrontierItem(BaseModel):
    item_id: str
    item_type: str  # "page", "control", "workflow", "auth_boundary"
    target: str     # url or element selector or workflow name
    parent_state_id: Optional[str] = None
    status: FrontierStatus = FrontierStatus.DISCOVERED
    priority_score: float = 1.0
    rationale: str = ""
    attempt_count: int = 0
    last_reason: Optional[str] = None
    last_failure_reason: Optional[ActionFailureReason] = None
    is_external: bool = False
    is_fragment: bool = False


class RuleOrHypothesis(BaseModel):
    rule_id: str
    description: str
    category: RuleCategory
    source: str
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    contradicting_evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    limitations: str = ""
    outstanding_questions: List[str] = Field(default_factory=list)


class EvidenceEntry(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:8]}")
    investigation_id: str
    timestamp: float = Field(default_factory=time.time)
    source_state_id: str
    action_id: Optional[str] = None
    evidence_type: str  # "DOM_OBSERVATION", "STATE_TRANSITION", "AUTH_VERIFICATION", "VALIDATION_MESSAGE", "SAFETY_DECISION", "ACTION_FAILURE", "NO_STATE_CHANGE"
    summary: str
    locator: Optional[str] = None
    validation_message: Optional[str] = None
    before_state_ref: Optional[str] = None
    after_state_ref: Optional[str] = None
    screenshot_artifact_id: Optional[str] = None
    console_errors: List[str] = Field(default_factory=list)
    network_errors: List[str] = Field(default_factory=list)
    related_transition_ids: List[str] = Field(default_factory=list)
    related_rule_ids: List[str] = Field(default_factory=list)


class PlanningStrategy(str, Enum):
    LLM_ADAPTIVE = "LLM_ADAPTIVE"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"
    MIXED = "MIXED"


class PageLandmark(BaseModel):
    role: str
    label: str
    selector: Optional[str] = None


class SemanticControlUnderstanding(BaseModel):
    element_id: str
    selector: str
    semantic_role: str
    inferred_purpose: str
    prerequisites: List[str] = Field(default_factory=list)
    safety_risk: str = "LOW"  # LOW, MEDIUM, HIGH


class FormValidationUnderstanding(BaseModel):
    form_id: str
    target_action: str
    required_fields: List[str] = Field(default_factory=list)
    observed_constraints: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)


class ApplicationUnderstandingModel(BaseModel):
    """
    Evidence-grounded, adaptive understanding of the target application.
    Distinguishes observed facts, inferred hypotheses, and unknowns.
    """
    page_purpose: str = "Web Application View"
    application_domain: str = "general"
    observed_landmarks: List[PageLandmark] = Field(default_factory=list)
    semantic_controls: List[SemanticControlUnderstanding] = Field(default_factory=list)
    form_understandings: List[FormValidationUnderstanding] = Field(default_factory=list)
    navigation_topology: List[Dict[str, str]] = Field(default_factory=list)
    verified_states: List[str] = Field(default_factory=list)
    confirmed_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    inferred_user_journeys: List[List[str]] = Field(default_factory=list)
    unexplored_opportunities: List[str] = Field(default_factory=list)

    # Strict Tri-Partite Epistemic Classification
    observed_facts: List[str] = Field(default_factory=list)          # Directly grounded in live DOM
    inferred_hypotheses: List[str] = Field(default_factory=list)     # Reasoning interpretations
    unknowns: List[str] = Field(default_factory=list)                # Gaps with insufficient evidence

    investigation_uncertainty: float = 0.0                            # 0.0 (certain) to 1.0 (uncertain)
    missing_evidence: List[str] = Field(default_factory=list)


# --- 10-Section Investigation Report Models ---

class ReportOverview(BaseModel):
    target_url: str
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    user_description: Optional[str] = None
    investigation_id: str
    start_time: str
    end_time: str
    duration_seconds: float = 0.0
    browser_execution_time: float = 0.0
    status: InvestigationStatus
    planning_strategy: str = "DETERMINISTIC"
    llm_calls_count: int = 0
    llm_fallbacks_count: int = 0
    llm_latency_seconds: float = 0.0
    retries_count: int = 0

    # Page & State Metrics
    pages_discovered: int
    meaningful_states_discovered: int

    # Action Metrics (Reconciled & Distinct)
    candidates_discovered: int = 0
    actions_evaluated: int = 0
    actions_attempted: int = 0
    actions_successful: int = 0
    actions_verified_effects: int = 0
    actions_failed: int = 0
    actions_safety_blocked: int = 0

    # Transition Metrics (Reconciled & Distinct)
    transitions_observed: int = 0
    route_transitions_observed: int = 0
    same_route_state_transitions: int = 0
    in_page_mutations_observed: int = 0

    # Frontier & Evidence Metrics
    pending_frontier_count: int = 0
    evidence_ledger_count: int = 0
    deduplicated_candidates_count: int = 0

    # Genuine Observed Counts across Discovered Views
    total_headings_discovered: int = 0
    total_forms_discovered: int = 0
    total_inputs_discovered: int = 0
    total_buttons_discovered: int = 0
    total_links_discovered: int = 0

    stopping_reason: str


class ReportAuthentication(BaseModel):
    boundary_observed: bool
    login_url: Optional[str] = None
    credentials_available: bool
    credentials_source: Optional[str] = None
    authentication_attempted: bool
    authentication_verified: bool
    verification_signals: List[str] = Field(default_factory=list)
    protected_functionality_accessible: List[str] = Field(default_factory=list)
    unresolved_or_blocked_areas: List[str] = Field(default_factory=list)


class DiscoveredPageArea(BaseModel):
    area_id: str
    route_or_url: str
    apparent_purpose: str
    relevant_controls_summary: List[str] = Field(default_factory=list)
    status: FrontierStatus
    evidence_ids: List[str] = Field(default_factory=list)
    limitations: str = ""


class WorkflowStepRecord(BaseModel):
    step_number: int
    workflow_branch: str = "Primary Workflow"
    from_state: str
    action_taken: str
    target_element: Optional[str] = None
    to_state: str
    observed_outcome: str
    evidence_id: str
    is_successful: bool = True
    failure_reason: Optional[str] = None


class InvestigationReport(BaseModel):
    """
    Pure investigation report with NO test cases, NO test suites, NO bug reports.
    Satisfies all 10 mandated report sections.
    """
    # Section A
    overview: ReportOverview
    # Section B
    application_understanding: str
    user_provided_context_distinction: str
    application_model: Optional[ApplicationUnderstandingModel] = None
    # Section C
    authentication_access: ReportAuthentication
    # Section D
    discovered_application_map: List[DiscoveredPageArea] = Field(default_factory=list)
    # Section E
    user_workflows: List[WorkflowStepRecord] = Field(default_factory=list)
    # Section F
    observed_behaviors: List[str] = Field(default_factory=list)
    # Section G
    business_rules_and_hypotheses: List[RuleOrHypothesis] = Field(default_factory=list)
    # Section H
    investigation_coverage_frontier: Dict[str, Any] = Field(default_factory=dict)
    # Section I
    evidence_and_traceability: List[Dict[str, Any]] = Field(default_factory=list)
    # Section J
    investigation_limitations: List[str] = Field(default_factory=list)

    # Phase 6 Extensions (Sections K, L, M, N)
    confirmed_bug_leads: List[BugLead] = Field(default_factory=list)
    unstable_or_rejected_leads: List[BugLead] = Field(default_factory=list)
    inferred_journeys: List[Dict[str, Any]] = Field(default_factory=list)
    journeys: List[UserJourney] = Field(default_factory=list)
    active_journey_id: Optional[str] = None
    behavior_model: Optional[Dict[str, Any]] = None
    playwright_test_script: Optional[str] = None
    decision_snapshots: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def markdown_report(self) -> str:
        """Returns the formatted markdown representation of the investigation report."""
        from app.investigation_agent.report_builder import InvestigationReportBuilder
        return InvestigationReportBuilder.to_markdown(self)
