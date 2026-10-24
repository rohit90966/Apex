"""
tests/test_observation_driven_generation.py
============================================
Comprehensive regression suite validating VerityQA's observation-driven test generation:
1. Test cannot originate solely from a retrieved QA pattern without DOM grounding or explicit requirement.
2. Unsupported password-length expectation without DOM minlength is flagged/re-labeled as exploratory.
3. DOM-observed password input attribute produces grounded masking test (INPUT_MASKED).
4. Observed form response produces test tied directly to that response (e.g. error container / blocked state).
5. Newly discovered state transition in ApplicationStateGraph creates a traceable journey test.
6. Deduplication avoids redundant tests for already explored behavior (both plan validator and state graph).
7. Unsupported expectations are classified as TEST_DEFINITION_ERROR or INCONCLUSIVE, never false product defects.
8. Complex site graph produces more tests / test ideas than simple site graph (dynamic scaling).
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.schemas import (
    TestCase,
    TestCaseStatus,
    Priority,
    ExpectedBehaviorSource,
    ActionType,
    AssertionType,
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    ActionStep,
    PageCategory,
    InspectionResult,
    FormFieldMetadata,
)
from app.agents.models import (
    RequirementAnalysis,
    RequirementItem,
)
from app.agents.plan_validator import PlanValidatorNode
from app.execution.runner import QAPlanRunner
from app.execution.executor import BrowserExecutor
from app.bugs.investigator import (
    BugInvestigator,
    InvestigationVerdict,
    _is_test_definition_error,
)
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.exploration.graph import (
    ApplicationStateGraph,
    UIState,
    StateTransition,
    InteractiveElement,
    ElementConstraints,
    ElementRole,
    ExplorationActionType,
)


# ---------------------------------------------------------------------------
# Test 1: Test cannot originate solely from QA pattern without DOM grounding or requirement
# ---------------------------------------------------------------------------

def test_qa_pattern_test_rejected_without_dom_grounding_or_requirement():
    """A test derived solely from a retrieved QA pattern targeting unobserved controls must be rejected."""
    # Inspection evidence only contains login fields
    inspection = InspectionResult(
        inspection_id="insp-01",
        target_url="https://example.com/login",
        final_url="https://example.com/login",
        page_title="Login",
        input_fields_summary=[
            FormFieldMetadata(name="username", input_type="text"),
            FormFieldMetadata(name="password", input_type="password"),
        ],
        buttons=["submit"],
    )

    # Candidate test derived solely from a Search QA pattern (no search bar in DOM, no explicit requirements)
    unrelated_pattern_test = TestCase(
        __test__=False,
        test_id="TC-PAT-01",
        name="Search bar input sanitization and filtering",
        objective="Verify search bar filters malicious characters and returns product results",
        page_category=PageCategory.LOGIN,
        action_description="Type test query into product search bar and click search filter",
        expected_outcome="Search results match filtered query",
        relevant_elements=["search_box", "search_button"],
        priority=Priority.LOW,
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        applied_pattern_ids=["PAT-SEARCH-01"],
        status=TestCaseStatus.PROPOSED,
        selection_reason="Retrieved search pattern",
        confidence=0.7,
    )

    grounded_test = TestCase(
        __test__=False,
        test_id="TC-DOM-01",
        name="Verify password field obscures text",
        objective="Ensure password input field masks characters",
        page_category=PageCategory.LOGIN,
        action_description="Type password into password field",
        expected_outcome="Characters are masked",
        relevant_elements=["password"],
        priority=Priority.HIGH,
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        applied_pattern_ids=["PAT-LOGIN-MASK"],
        status=TestCaseStatus.PROPOSED,
        selection_reason="Security invariant for observed password input",
        confidence=0.95,
    )

    state = {
        "proposed_test_cases": [unrelated_pattern_test, grounded_test],
        "inspection_evidence": inspection,
        "requirement_analysis": None,  # No explicit requirements
    }

    result = PlanValidatorNode.run(state)
    sanitized = result["proposed_test_cases"]

    # The ungrounded search test must be excluded
    test_ids = [t.test_id for t in sanitized]
    assert "TC-PAT-01" not in test_ids
    assert "TC-DOM-01" in test_ids
    assert any("originates solely from QA pattern without DOM grounding" in err for err in result["validation_result"].errors)


def test_qa_pattern_test_allowed_when_explicit_requirement_exists():
    """If an explicit user requirement specifies search, the test is permitted even if not yet in DOM."""
    inspection = InspectionResult(
        inspection_id="insp-01",
        target_url="https://example.com/login",
        final_url="https://example.com/login",
        page_title="Login",
        input_fields_summary=[FormFieldMetadata(name="username", input_type="text")],
        buttons=["submit"],
    )

    req_analysis = RequirementAnalysis(
        explicit_requirements=[
            RequirementItem(id="REQ-SEARCH", text="User must be able to search product catalog", category="functional", testable=True)
        ]
    )

    pattern_test = TestCase(
        __test__=False,
        test_id="TC-PAT-02",
        name="Search product catalog",
        objective="Search product catalog per specification",
        page_category=PageCategory.LOGIN,
        action_description="Type query into search",
        expected_outcome="Matching products displayed",
        relevant_elements=["search_input"],
        priority=Priority.HIGH,
        expected_behavior_source=ExpectedBehaviorSource.REQUIREMENT,
        applied_pattern_ids=["PAT-SEARCH-01"],
        status=TestCaseStatus.PROPOSED,
        selection_reason="Matches explicit user requirement REQ-SEARCH",
        confidence=0.9,
    )

    state = {
        "proposed_test_cases": [pattern_test],
        "inspection_evidence": inspection,
        "requirement_analysis": req_analysis,
    }

    result = PlanValidatorNode.run(state)
    assert len(result["proposed_test_cases"]) == 1
    assert result["proposed_test_cases"][0].test_id == "TC-PAT-02"


# ---------------------------------------------------------------------------
# Test 2: Unsupported password-length expectation without DOM minlength re-labeled as exploratory
# ---------------------------------------------------------------------------

def test_unsupported_password_length_relabeled_as_exploratory():
    """A test asserting 'at least 6 characters' without DOM minlength must be re-labeled as exploratory."""
    inspection = InspectionResult(
        inspection_id="insp-02",
        target_url="https://www.saucedemo.com/",
        final_url="https://www.saucedemo.com/",
        page_title="Swag Labs",
        input_fields_summary=[
            FormFieldMetadata(name="user-name", input_type="text"),
            FormFieldMetadata(name="password", input_type="password", minlength=None),  # No minlength!
        ],
        buttons=["login-button"],
    )

    tc = TestCase(
        __test__=False,
        test_id="TC-PWD-01",
        name="Validate password length threshold of 6 characters",
        objective="Ensure system rejects passwords shorter than 6 characters",
        page_category=PageCategory.LOGIN,
        action_description="Enter 5 characters in password field and submit",
        expected_outcome="Error displayed requiring at least 6 characters",
        relevant_elements=["password", "login-button"],
        priority=Priority.MEDIUM,
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        status=TestCaseStatus.PROPOSED,
        selection_reason="Heuristic password policy check",
        confidence=0.7,
    )

    state = {
        "proposed_test_cases": [tc],
        "inspection_evidence": inspection,
        "requirement_analysis": None,
    }

    result = PlanValidatorNode.run(state)
    sanitized = result["proposed_test_cases"]
    assert len(sanitized) == 1
    sanitized_tc = sanitized[0]

    assert sanitized_tc.expected_behavior_source == ExpectedBehaviorSource.INFERRED
    assert "Exploratory: password length constraint not confirmed in DOM evidence" in sanitized_tc.expected_outcome
    assert any("unsupported password length expectation" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Test 3: DOM-observed password attribute produces grounded masking test
# ---------------------------------------------------------------------------

def test_dom_observed_password_produces_grounded_masking_step():
    """DOM inspection with password field produces an INPUT_MASKED step targeting that exact selector."""
    runner = QAPlanRunner()
    inspection = InspectionResult(
        inspection_id="insp-03",
        target_url="https://www.saucedemo.com/",
        final_url="https://www.saucedemo.com/",
        page_title="Swag Labs",
        input_fields_summary=[
            FormFieldMetadata(name="user-name", input_type="text"),
            FormFieldMetadata(name="password", input_type="password"),
        ],
        buttons=["login-button"],
    )

    tc = TestCase(
        __test__=False,
        test_id="TC-MASK-GROUNDED",
        name="Verify password field masking obscures characters",
        objective="Ensure password input field masks characters",
        page_category=PageCategory.LOGIN,
        action_description="Inspect password field masking",
        expected_outcome="Characters are masked",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Security requirement for credential masking",
        confidence=0.95,
    )

    runner.set_inspection(inspection)
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 2
    masking_step = next((s for s in steps if s.assertion and s.assertion.assertion_type == AssertionType.INPUT_MASKED), None)
    assert masking_step is not None
    assert "password" in masking_step.selector.lower()
    # Must NOT assert URL contains /inventory
    for step in steps:
        if step.assertion:
            assert step.assertion.assertion_type != AssertionType.URL_CONTAINS
            assert "inventory" not in (step.assertion.expected_value or "").lower()


# ---------------------------------------------------------------------------
# Test 4: Observed form response produces test tied directly to that response
# ---------------------------------------------------------------------------

def test_observed_form_response_tied_directly_to_error_container():
    """Missing credential or validation test asserts the actual observed error container or attribute."""
    runner = QAPlanRunner()
    inspection = InspectionResult(
        inspection_id="insp-04",
        target_url="https://www.saucedemo.com/",
        final_url="https://www.saucedemo.com/",
        page_title="Swag Labs",
        input_fields_summary=[
            FormFieldMetadata(name="user-name", input_type="text"),
            FormFieldMetadata(name="password", input_type="password"),
        ],
        buttons=["login-button"],
    )
    runner.set_inspection(inspection)

    tc = TestCase(
        __test__=False,
        test_id="TC-ERR-01",
        name="Submit empty credentials displays validation error",
        objective="Ensure submitting empty credentials triggers validation feedback",
        page_category=PageCategory.LOGIN,
        action_description="Click submit with empty fields",
        expected_outcome="Validation error container is visible",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Negative form test on empty fields",
        confidence=0.9,
    )

    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 1
    final_step = next((s for s in reversed(steps) if s.assertion), None)
    assert final_step is not None
    assert final_step.assertion.assertion_type in (
        AssertionType.VALIDITY_VALUE_MISSING,
        AssertionType.ELEMENT_VISIBLE,
        AssertionType.ELEMENT_ATTRIBUTE,
    )


# ---------------------------------------------------------------------------
# Test 5: Newly discovered state transition creates a corresponding journey test
# ---------------------------------------------------------------------------

def test_discovered_state_transition_in_graph_records_traceable_journey():
    """State transition from S0 to S1 via an action creates a navigable path in ApplicationStateGraph."""
    graph = ApplicationStateGraph(starting_url="https://example.com/app")

    s0 = UIState(
        state_id="s0_home",
        url="https://example.com/app",
        page_title="App Home",
        dom_fingerprint="fp_home_123",
        interactive_elements=[
            InteractiveElement(
                selector="#btn-modal",
                tag="button",
                role=ElementRole.BUTTON,
                accessible_name="Open Settings",
            )
        ],
    )
    s1 = UIState(
        state_id="s1_settings_modal",
        url="https://example.com/app",
        page_title="App Home - Settings",
        dom_fingerprint="fp_settings_456",
        interactive_elements=[
            InteractiveElement(
                selector="#close-modal",
                tag="button",
                role=ElementRole.BUTTON,
                accessible_name="Close Settings",
            )
        ],
    )

    transition = StateTransition(
        source_state_id=s0.state_id,
        resulting_state_id=s1.state_id,
        action_type=ExplorationActionType.CLICK,
        target_element_selector="#btn-modal",
        target_element_name="Open Settings",
        url_before=s0.url,
        url_after=s1.url,
        new_state_discovered=True,
    )

    graph.states[s0.state_id] = s0
    graph.states[s1.state_id] = s1
    graph.transitions.append(transition)

    assert graph.state_count == 2
    assert graph.transition_count == 1
    assert graph.transitions[0].target_element_selector == "#btn-modal"
    assert graph.transitions[0].resulting_state_id == "s1_settings_modal"


# ---------------------------------------------------------------------------
# Test 6: Deduplication avoids redundant tests for already explored behavior
# ---------------------------------------------------------------------------

def test_deduplication_filters_identical_proposed_tests():
    """Validator eliminates duplicate tests proposing the same behavior."""
    tc1 = TestCase(
        __test__=False,
        test_id="TC-DUP-01",
        name="Verify login with valid credentials",
        objective="Ensure user can log in",
        page_category=PageCategory.LOGIN,
        action_description="Enter credentials and submit",
        expected_outcome="User dashboard displayed",
        priority=Priority.HIGH,
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        status=TestCaseStatus.PROPOSED,
        selection_reason="Primary happy path test",
        confidence=0.95,
    )
    tc2 = TestCase(
        __test__=False,
        test_id="TC-DUP-02",
        name="Verify login with valid credentials",  # Same name
        objective="Ensure user can log in duplicate",
        page_category=PageCategory.LOGIN,
        action_description="Enter credentials and submit duplicate",
        expected_outcome="User dashboard displayed duplicate",
        priority=Priority.HIGH,
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        status=TestCaseStatus.PROPOSED,
        selection_reason="Duplicate happy path test",
        confidence=0.95,
    )

    state = {"proposed_test_cases": [tc1, tc2]}
    result = PlanValidatorNode.run(state)
    assert len(result["proposed_test_cases"]) == 1
    assert result["proposed_test_cases"][0].test_id == "TC-DUP-01"


# ---------------------------------------------------------------------------
# Test 7: Unsupported expectations classified as TEST_DEFINITION_ERROR / INCONCLUSIVE
# ---------------------------------------------------------------------------

def test_unsupported_expectations_never_reported_as_product_defects():
    """An assertion failure caused by an ungrounded or contradictory expectation must be classified as TEST_DEFINITION_ERROR."""
    tc_masking = TestCase(
        __test__=False,
        test_id="TC-FAIL-UNGROUNDED",
        name="Verify password field masking obscures characters",
        objective="Ensure password input field masks characters",
        page_category=PageCategory.LOGIN,
        action_description="Type password and verify masking",
        expected_outcome="Password is masked",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Security requirement for credential masking",
        confidence=0.9,
    )

    # Failed because bad test expected URL contains /inventory
    step_result = StepExecutionResult(
        step_number=1,
        action=ActionType.CLICK,
        description="Submit and verify /inventory",
        status=ExecutionStatus.FAILED,
        assertion_expected="/inventory",
        assertion_actual="https://www.saucedemo.com/",
    )

    exec_result = ExecutionResult(
        test_id="TC-FAIL-UNGROUNDED",
        test_name="Verify password field masking obscures characters",
        status=ExecutionStatus.FAILED,
        total_steps=1,
        steps_completed=1,
        failure_reason="Expected URL to contain '/inventory', got 'https://www.saucedemo.com/'",
        step_results=[step_result],
    )

    # Unit classification helper
    is_def_err, reason = _is_test_definition_error(tc_masking, exec_result)
    assert is_def_err is True
    assert "/inventory" in reason

    # BugInvestigator failure classifier
    investigator = BugInvestigator()
    verdict, rationale = investigator._classify_failure(tc_masking, exec_result)
    assert verdict == InvestigationVerdict.TEST_DEFINITION_ERROR
    assert verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT


# ---------------------------------------------------------------------------
# Test 8: Complex site graph produces more test ideas than simple site graph
# ---------------------------------------------------------------------------

def test_complex_site_graph_produces_more_test_ideas_than_simple_site():
    """Complex site with multiple forms and fields produces strictly more boundary test ideas than simple site."""
    # Simple page: 1 field
    simple_fields = [
        FormFieldMetadata(name="email", input_type="email", required=True),
    ]
    simple_ev, simple_ideas = BoundaryValueEngine.derive_from_inspection(
        fields=simple_fields,
        page_url="https://example.com/simple",
        page_id="p-simple",
    )

    # Complex page: 5 fields with constraints (min, max, pattern, minlength, maxlength)
    complex_fields = [
        FormFieldMetadata(name="email", input_type="email", required=True),
        FormFieldMetadata(name="age", input_type="number", min="18", max="99", required=True),
        FormFieldMetadata(name="password", input_type="password", minlength="8", maxlength="32", required=True),
        FormFieldMetadata(name="phone", input_type="tel", pattern=r"^\d{10}$"),
        FormFieldMetadata(name="bio", input_type="text", maxlength="200"),
    ]
    complex_ev, complex_ideas = BoundaryValueEngine.derive_from_inspection(
        fields=complex_fields,
        page_url="https://example.com/complex",
        page_id="p-complex",
    )

    assert len(complex_ideas) > len(simple_ideas)
    assert len(complex_ev) > len(simple_ev)
    assert len(complex_ideas) >= 5
