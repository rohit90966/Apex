from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, HttpUrl, field_validator


class PageCategory(str, Enum):
    LOGIN = "LOGIN"
    REGISTRATION = "REGISTRATION"
    SEARCH = "SEARCH"
    CONTACT_FORM = "CONTACT_FORM"
    GENERAL = "GENERAL"
    UNKNOWN = "UNKNOWN"


class ExpectedBehaviorSource(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    INVARIANT = "INVARIANT"
    OBSERVED_INVARIANT = "OBSERVED_INVARIANT"
    CONTRACT = "CONTRACT"
    INFERRED = "INFERRED"


class Priority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class TestCaseStatus(str, Enum):
    PROPOSED = "PROPOSED"
    # Cannot be executed meaningfully (no executable steps, missing selectors, or no assertions).
    INVALID = "INVALID"
    __test__ = False


class PlanningMode(str, Enum):
    GROQ = "GROQ"
    GEMINI = "GEMINI"
    OLLAMA = "OLLAMA"
    RULE_BASED_FALLBACK = "RULE_BASED_FALLBACK"


class InspectionRequest(BaseModel):
    url: str = Field(..., description="Target website URL to inspect")
    description: Optional[str] = Field(default=None, description="Optional brief website description")
    requirements: Optional[List[str]] = Field(default=None, description="Optional list of testing requirements")


class PageClassification(BaseModel):
    page_category: PageCategory
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    evidence: List[str] = Field(default_factory=list, description="Observed evidence supporting the classification")
    reasoning_summary: str = Field(..., description="Explainable reasoning for the classification")


class FormFieldMetadata(BaseModel):
    name: Optional[str] = None
    input_type: Optional[str] = None
    label: Optional[str] = None
    placeholder: Optional[str] = None
    is_required: bool = False
    options: Optional[List[str]] = None
    # HTML constraint attributes — captured from DOM
    field_id: Optional[str] = None
    disabled: bool = False
    readonly: bool = False
    minlength: Optional[int] = None
    maxlength: Optional[int] = None
    min_value: Optional[str] = None   # kept as str: supports floats, dates, datetimes
    max_value: Optional[str] = None
    step: Optional[str] = None
    pattern: Optional[str] = None
    autocomplete: Optional[str] = None
    multiple: bool = False
    checked: Optional[bool] = None


class FormMetadata(BaseModel):
    form_id: Optional[str] = None
    action: Optional[str] = None
    method: Optional[str] = None
    fields: List[FormFieldMetadata] = Field(default_factory=list)


class ElementMetadata(BaseModel):
    tag: str
    accessible_name: Optional[str] = None
    element_type: Optional[str] = None
    text_content: Optional[str] = None


class InspectionResult(BaseModel):
    inspection_id: str
    target_url: str
    final_url: str
    page_title: str
    http_status: Optional[int] = None
    headings: List[str] = Field(default_factory=list)
    visible_text_snippet: str = ""
    buttons: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    forms: List[FormMetadata] = Field(default_factory=list)
    input_fields_summary: List[FormFieldMetadata] = Field(default_factory=list)
    screenshot_artifact_id: Optional[str] = None
    console_errors: List[str] = Field(default_factory=list)
    page_errors: List[str] = Field(default_factory=list)
    inspection_duration_seconds: float = 0.0


class TestCase(BaseModel):
    __test__ = False
    test_id: str
    name: str
    objective: str
    page_category: PageCategory = PageCategory.GENERAL
    relevant_elements: List[str] = Field(default_factory=list)
    action_description: str = ""
    expected_outcome: str = ""
    expected_behavior_source: ExpectedBehaviorSource = ExpectedBehaviorSource.OBSERVED_INVARIANT
    evidence_to_collect: List[str] = Field(default_factory=list)
    priority: Priority = Priority.MEDIUM
    preconditions: List[str] = Field(default_factory=list)
    requires_test_account: bool = False
    safe_for_future_automation: bool = True
    selection_reason: str = "Empirically discovered scenario"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    status: TestCaseStatus = TestCaseStatus.PROPOSED
    applied_pattern_ids: List[str] = Field(default_factory=list)
    # Evidence traceability — populated by DeterministicDerivationNode; optional for LLM-generated cases
    evidence_ids: List[str] = Field(default_factory=list)
    rule_ids: List[str] = Field(default_factory=list)
    oracle_class: Optional[str] = None    # CONSTRAINT_DERIVED | SPECIFICATION | GENERIC_HEURISTIC | UNKNOWN
    derived_values: Optional[Dict[str, Any]] = None   # e.g. {"boundary_value": 20, "boundary_type": "maxlength_exact"}
    # Adaptive execution steps (Phase 2)
    steps: Optional[List[Any]] = None
    # Structured contract specification
    spec: Optional[Dict[str, Any]] = None
    invalid_reason: Optional[str] = None
    # Target independence contract: the investigation and origin this case was derived from.
    # The runner refuses to execute a case against a different origin.
    investigation_id: Optional[str] = None
    target_origin: Optional[str] = None
    # FUNCTIONAL cases carry a meaningful oracle; EXPLORATORY cases record behaviour without a verdict.
    test_kind: str = "FUNCTIONAL"
    spec_version: str = "2.0"
    # QA knowledge entries (strategies / defect classes / oracles) that produced this case.
    knowledge_ids: List[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, v: Any) -> str:
        if v is None:
            return "Discovered Test Scenario"
        s = str(v).strip()
        return s if s else "Discovered Test Scenario"

    @field_validator("objective", mode="before")
    @classmethod
    def _coerce_objective(cls, v: Any) -> str:
        if v is None:
            return "Verify target element behavior and state transitions"
        s = str(v).strip()
        return s if s else "Verify target element behavior and state transitions"

    @field_validator("action_description", "expected_outcome", mode="before")
    @classmethod
    def _coerce_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class TestPlan(BaseModel):
    __test__ = False
    plan_id: str
    inspection_id: str
    page_classification: PageClassification
    test_cases: List[TestCase] = Field(default_factory=list)
    planning_mode: PlanningMode
    assumptions: List[str] = Field(default_factory=list)
    missing_requirements_or_uncertainties: List[str] = Field(default_factory=list)
    planning_summary: str
    warnings: List[str] = Field(default_factory=list)
    # Step 2 & 3 LangGraph agentic workflow metadata
    workflow_status: Optional[str] = None
    current_stage: Optional[str] = None
    revision_count: int = 0
    format_retry_count: int = 0
    review_summary: Optional[str] = None
    coverage_summary: Optional[Dict[str, Any]] = None
    # Step 3: Deep Investigation & Vector Knowledge metadata
    pages_discovered: Optional[int] = None
    pages_inspected: Optional[int] = None
    features_detected: Optional[List[Dict[str, Any]]] = None
    retrieved_knowledge_count: Optional[int] = None
    retrieved_pattern_ids: Optional[List[str]] = None
    applied_knowledge_count: Optional[int] = None
    applied_pattern_ids: Optional[List[str]] = None
    # Evidence-grounded planning metadata (Step 2 upgrade)
    evidence_ledger_count: Optional[int] = None
    unknowns_count: Optional[int] = None
    deterministic_test_count: Optional[int] = None
    # Observability & timing instrumentation
    execution_duration_seconds: Optional[float] = None
    stage_timings: Optional[Dict[str, float]] = None
    ollama_stats: Optional[Dict[str, Any]] = None
    llm_stats: Optional[Dict[str, Any]] = None


class InspectionResponse(BaseModel):
    inspection: InspectionResult
    classification: PageClassification
    test_plan: TestPlan
    investigation_summary: Optional[Dict[str, Any]] = None


# ==============================================================================
# Autonomous Execution, Bug Verification & Benchmark Schemas (Code Apex IM-02)
# ==============================================================================

class ActionType(str, Enum):
    NAVIGATE = "NAVIGATE"
    FILL = "FILL"
    CLICK = "CLICK"
    SELECT = "SELECT"
    PRESS_KEY = "PRESS_KEY"
    WAIT_FOR_ELEMENT = "WAIT_FOR_ELEMENT"
    SCREENSHOT = "SCREENSHOT"
    EVAL_JS = "EVAL_JS"
    # Deliberate bypass: sets a field value via JS input-event dispatch, circumventing
    # browser-native HTML5 required/pattern validation so the server sees the raw value.
    # Only used when an explicit requirement authorises a server-side boundary test
    # that browser validation would otherwise prevent from reaching the server.
    SET_VALUE_JS = "SET_VALUE_JS"
    # Read-only DOM snapshot before an action (records pre-action state)
    OBSERVE_STATE = "OBSERVE_STATE"



class AssertionType(str, Enum):
    URL_CONTAINS = "URL_CONTAINS"
    URL_EQUALS = "URL_EQUALS"
    TEXT_VISIBLE = "TEXT_VISIBLE"
    TEXT_NOT_VISIBLE = "TEXT_NOT_VISIBLE"
    ELEMENT_VISIBLE = "ELEMENT_VISIBLE"
    ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    ELEMENT_ENABLED = "ELEMENT_ENABLED"
    STATUS_CODE = "STATUS_CODE"
    VALIDITY_VALUE_MISSING = "VALIDITY_VALUE_MISSING"
    VALIDITY_INVALID = "VALIDITY_INVALID"
    FORM_SUBMISSION_BLOCKED = "FORM_SUBMISSION_BLOCKED"
    ELEMENT_ATTRIBUTE = "ELEMENT_ATTRIBUTE"
    INPUT_MASKED = "INPUT_MASKED"
    ACCESSIBILITY_VALID = "ACCESSIBILITY_VALID"
    CUSTOM = "CUSTOM"
    # Evidence-grounded oracles (schema 2.0)
    FIELD_VALIDITY = "FIELD_VALIDITY"                    # expected_value: valueMissing|tooLong|tooShort|rangeOverflow|rangeUnderflow|patternMismatch|typeMismatch|valid
    ELEMENT_VALUE_LENGTH = "ELEMENT_VALUE_LENGTH"        # expected_value: "==N" | "<=N" | ">=N"
    VALIDATION_FEEDBACK_SHOWN = "VALIDATION_FEEDBACK_SHOWN"
    NO_VALIDATION_FEEDBACK = "NO_VALIDATION_FEEDBACK"
    LIST_SORTED = "LIST_SORTED"                          # expected_value: JSON {"key": "text|number", "direction": "asc|desc"}
    LIST_ALL_CONTAIN = "LIST_ALL_CONTAIN"                # every record of the detected collection contains expected_value
    SELECTED_VALUE = "SELECTED_VALUE"                    # control's current value equals expected_value
    NO_TEXT_ANOMALY = "NO_TEXT_ANOMALY"                  # visible text has no NaN/undefined/[object Object]/tracebacks


# Assertions that cannot fail on a rendered page and therefore never count as a verification.
TRIVIAL_ASSERTION_SELECTORS = {"body", "html", ":root"}


def is_meaningful_assertion(assertion: Optional["ExecutionAssertion"]) -> bool:
    """False for assertions that verify nothing about product behaviour (e.g. 'body is visible')."""
    if assertion is None:
        return False
    if assertion.assertion_type == AssertionType.CUSTOM:
        return False
    if assertion.assertion_type == AssertionType.ELEMENT_VISIBLE:
        return (assertion.target_selector or "").strip().lower() not in TRIVIAL_ASSERTION_SELECTORS
    if assertion.assertion_type in (AssertionType.TEXT_VISIBLE, AssertionType.TEXT_NOT_VISIBLE, AssertionType.URL_CONTAINS,
                                    AssertionType.URL_EQUALS, AssertionType.SELECTED_VALUE):
        return bool((assertion.expected_value or "").strip())
    return True


class ExecutionAssertion(BaseModel):
    assertion_type: AssertionType
    target_selector: Optional[str] = None
    expected_value: Optional[str] = None
    description: str
    # Where the expected outcome comes from: OBSERVED (seen in this run), HTML_CONSTRAINT, REQUIREMENT,
    # DERIVED_INFERENCE (e.g. ordering inferred from an option label), TEST_DATA (value entered by the test).
    expected_source: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_desc(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class ActionStep(BaseModel):
    step_number: int
    action: ActionType
    selector: Optional[str] = None
    value: Optional[str] = None
    description: str
    assertion: Optional[ExecutionAssertion] = None

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_desc(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)


class ExecutionStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    PRODUCT_BEHAVIOR_DEVIATION = "PRODUCT_BEHAVIOR_DEVIATION"
    TEST_DEFINITION_ERROR = "TEST_DEFINITION_ERROR"
    LOCATOR_ERROR = "LOCATOR_ERROR"
    AUTOMATION_ERROR = "AUTOMATION_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"


class StepExecutionResult(BaseModel):
    step_number: int
    action: ActionType
    description: str
    status: ExecutionStatus
    duration_seconds: float = 0.0
    screenshot_path: Optional[str] = None
    error_message: Optional[str] = None
    assertion_expected: Optional[str] = None
    assertion_actual: Optional[str] = None


class ExecutionResult(BaseModel):
    test_id: str
    test_name: str
    status: ExecutionStatus
    total_steps: int
    steps_completed: int
    step_results: List[StepExecutionResult] = Field(default_factory=list)
    duration_seconds: float = 0.0
    failure_reason: Optional[str] = None
    failure_step: Optional[int] = None
    final_screenshot: Optional[str] = None
    dom_snapshot_snippet: Optional[str] = None
    console_logs: List[str] = Field(default_factory=list)
    timestamp: str = ""
    assertions_evaluated: int = 0
    assertions_satisfied: int = 0
    # Evaluated assertions that can actually detect a product defect (excludes e.g. 'body visible').
    meaningful_assertions_evaluated: int = 0
    evidence_retained: Dict[str, Any] = Field(default_factory=dict)



class BugSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class VerificationStatus(str, Enum):
    CONFIRMED_REPRODUCIBLE = "CONFIRMED_REPRODUCIBLE"
    LIKELY_DEFECT = "LIKELY_DEFECT"
    POTENTIAL_DEFECT = "POTENTIAL_DEFECT"
    ENVIRONMENTAL_FAILURE = "ENVIRONMENTAL_FAILURE"
    OBSERVED_NOT_REPRODUCED = "OBSERVED_NOT_REPRODUCED"
    TEST_ORACLE_DEFECT = "TEST_ORACLE_DEFECT"


class BugReport(BaseModel):
    bug_id: str
    title: str
    severity: BugSeverity
    verification_status: VerificationStatus
    reproduction_rate: str  # e.g. "3/3 runs failed consistently"
    test_id: str
    target_url: str
    summary: str
    steps_to_reproduce: List[str] = Field(default_factory=list)
    expected_behavior: str
    actual_behavior: str
    evidence_screenshot: Optional[str] = None
    reproduction_trace: Optional[str] = None
    dom_context: Optional[str] = None
    console_errors: List[str] = Field(default_factory=list)
    suspected_cause: Optional[str] = None
    suggested_fix: Optional[str] = None
    # Finding lifecycle class: CONFIRMED_DEFECT | LIKELY_DEFECT | UNVERIFIED_ANOMALY | INCONCLUSIVE |
    # TEST_INFRASTRUCTURE_FAILURE | TEST_DEFINITION_ERROR (see app/findings/lifecycle.py)
    classification: Optional[str] = None
    expected_source: Optional[str] = None


class BenchmarkManifestItem(BaseModel):
    bug_id: str
    category: str
    route: str
    title: str
    description: str
    trigger_condition: str
    expected_behavior: str
    actual_buggy_behavior: str


class BenchmarkRunSummary(BaseModel):
    benchmark_name: str
    total_planted_bugs: int
    true_positives_detected: int
    false_positives: int
    missed_bugs: int
    precision: float
    recall: float
    f1_score: float
    detected_bug_ids: List[str] = Field(default_factory=list)
    missed_bug_ids: List[str] = Field(default_factory=list)
    detailed_reports: List[BugReport] = Field(default_factory=list)


class QARunRequest(BaseModel):
    url: str
    description: Optional[str] = None
    execute_tests: bool = True
    verify_bugs: bool = True
    export_scripts: bool = True


class QARunResponse(BaseModel):
    run_id: str
    target_url: str
    inspection: InspectionResult
    classification: PageClassification
    test_plan: TestPlan
    execution_results: List[ExecutionResult] = Field(default_factory=list)
    bug_reports: List[BugReport] = Field(default_factory=list)
    exported_script: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)


class ExecuteSuiteRequest(BaseModel):
    target_url: str
    test_cases: List[TestCase] = Field(default_factory=list)
    verify_bugs: bool = True
    # When set, the run's persisted test cases are executed (optionally only test_ids) and the
    # result is persisted against the run. Mutually exclusive with test_cases.
    run_id: Optional[str] = None
    test_ids: Optional[List[str]] = None


class ExecuteSuiteResponse(BaseModel):
    execution_id: str
    target_url: str
    total_cases: int
    executed_count: int
    passed_count: int
    failed_count: int
    setup_failures_count: int
    assertion_validity_rate: float
    results: List[ExecutionResult] = Field(default_factory=list)
    bug_reports: List[BugReport] = Field(default_factory=list)
    duration_seconds: float = 0.0
    run_id: Optional[str] = None
    # Count of results per ExecutionStatus value (PASSED, FAILED, ERROR, BLOCKED, TEST_DEFINITION_ERROR, ...).
    status_counts: Dict[str, int] = Field(default_factory=dict)


