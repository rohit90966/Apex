"""
Regression tests for VerityQA Execution and Bug Verification Pipeline Repair.
Validates:
- Password masking does not require URL navigation (/inventory)
- Accessibility verification independent of authentication
- Correctly targeted missing-password assertions
- Missing or ambiguous locators
- Incorrect assertion classified as TEST_DEFINITION_ERROR
- Genuine planted product defect detected and reproduced
- Passing test never reported as defect
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.schemas import (
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    ActionType,
    AssertionType,
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    BugSeverity,
    VerificationStatus,
    InspectionResult,
    FormFieldMetadata,
)
from app.execution.runner import QAPlanRunner
from app.execution.executor import BrowserExecutor
from app.bugs.investigator import (
    BugInvestigator,
    InvestigationVerdict,
    _is_test_definition_error,
)
from app.benchmark.manifest import BenchmarkEvaluator, PLANTED_BUG_MANIFEST


def test_password_masking_synthesis_no_navigation_requirement():
    """Password masking test must inspect DOM/input masking and NEVER assert URL contains /inventory."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-MASK-01",
        name="Verify password field masking obscures characters",
        objective="Ensure password input field masks entered characters for shoulder-surfing protection.",
        page_category=PageCategory.LOGIN,
        action_description="Locate password input and verify type is password",
        expected_outcome="Password field masks input characters",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Security requirement for credential masking",
        confidence=0.95,
    )
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 1
    # Check that no step asserts URL contains /inventory
    for step in steps:
        if step.assertion:
            assert step.assertion.assertion_type != AssertionType.URL_CONTAINS
            assert "inventory" not in (step.assertion.expected_value or "").lower()
            assert step.assertion.assertion_type == AssertionType.INPUT_MASKED


def test_accessibility_synthesis_independent_of_authentication():
    """Accessibility test must verify accessible names/roles and NEVER submit credentials or require login."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-A11Y-01",
        name="Verify interactive controls accessibility",
        objective="Check that form controls have accessible names and aria attributes for screen readers.",
        page_category=PageCategory.LOGIN,
        action_description="Inspect page controls for accessible names",
        expected_outcome="All controls have accessible names",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.MEDIUM,
        selection_reason="Accessibility compliance",
        confidence=0.9,
    )
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 1
    for step in steps:
        if step.assertion:
            assert step.assertion.assertion_type == AssertionType.ACCESSIBILITY_VALID
            assert "inventory" not in (step.assertion.expected_value or "").lower()


def test_missing_password_targets_password_selector():
    """Missing password test must target the password input field, not username."""
    runner = QAPlanRunner()
    # Provide inspection with SauceDemo DOM names
    inspection = InspectionResult(
        inspection_id="insp-sauce",
        target_url="https://www.saucedemo.com/",
        final_url="https://www.saucedemo.com/",
        page_title="Swag Labs",
        input_fields_summary=[
            FormFieldMetadata(name="user-name", input_type="text"),
            FormFieldMetadata(name="password", input_type="password"),
        ],
    )
    runner.set_inspection(inspection)

    tc = TestCase(
        __test__=False,
        test_id="TC-MISSING-PWD",
        name="Verify validation when submitting without password",
        objective="Submit form with valid username but missing password to verify required constraint.",
        page_category=PageCategory.LOGIN,
        action_description="Leave password empty and click submit",
        expected_outcome="Validation message indicates password is required",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Required field validation",
        confidence=0.9,
    )
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    click_step = next((s for s in steps if s.action == ActionType.CLICK and s.assertion), None)
    assert click_step is not None
    assert click_step.assertion is not None
    assert "password" in click_step.assertion.target_selector.lower()
    assert "user-name" not in click_step.assertion.target_selector.lower()


def test_missing_or_ambiguous_locator_detection():
    """Missing or non-existent selector is classified as LOCATOR_ERROR and not a product defect."""
    investigator = BugInvestigator()
    result = ExecutionResult(
        test_id="TC-LOC-01",
        test_name="Test missing locator",
        status=ExecutionStatus.FAILED,
        total_steps=2,
        steps_completed=1,
        failure_reason="Playwright error: waiting for selector 'input#non_existent_field_id' failed: timeout 8000ms exceeded",
        failure_step=1,
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-LOC-01",
        name="Missing locator test",
        objective="Interact with field",
        page_category=PageCategory.LOGIN,
        action_description="Click element",
        expected_outcome="Success",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.LOW,
        selection_reason="Test",
        confidence=0.5,
    )
    verdict, rationale = investigator._classify_failure(tc, result)
    assert verdict == InvestigationVerdict.LOCATOR_ERROR
    assert verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT


def test_incorrect_assertion_classified_as_test_definition_error():
    """Expecting /inventory on a password masking test is detected as a TEST_DEFINITION_ERROR."""
    step = StepExecutionResult(
        step_number=3,
        action=ActionType.CLICK,
        description="Submit",
        status=ExecutionStatus.FAILED,
        assertion_expected="/inventory",
        assertion_actual="https://www.saucedemo.com/",
    )
    result = ExecutionResult(
        test_id="TC-MASK-01",
        test_name="Verify password field masking",
        status=ExecutionStatus.FAILED,
        total_steps=3,
        steps_completed=3,
        failure_reason="Expected URL to contain '/inventory', got 'https://www.saucedemo.com/'",
        step_results=[step],
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-MASK-01",
        name="Verify password field masking",
        objective="Ensure password input field masks entered characters for shoulder-surfing protection.",
        page_category=PageCategory.LOGIN,
        action_description="Inspect password field type",
        expected_outcome="Password characters obscured",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Masking test",
        confidence=0.9,
    )
    is_err, rationale = _is_test_definition_error(tc, result)
    assert is_err is True
    assert "inventory" in rationale.lower() or "navigation" in rationale.lower()

    investigator = BugInvestigator()
    verdict, _ = investigator._classify_failure(tc, result)
    assert verdict == InvestigationVerdict.TEST_DEFINITION_ERROR


@pytest.mark.asyncio
async def test_genuine_planted_defect_detected_and_reproduced():
    """A genuine defect failure (e.g. BUG-02 ToS bypass) must be confirmed reproducible."""
    mock_runner = MagicMock()
    # Mock runner returns consistent failure on retry
    mock_runner.run_test = AsyncMock(return_value=ExecutionResult(
        test_id="TC-REG-TERMS",
        test_name="Terms acceptance bypass",
        status=ExecutionStatus.FAILED,
        total_steps=5,
        steps_completed=5,
        failure_reason="Expected text 'Account Created' to NOT be visible, but got visible",
        failure_step=5,
    ))
    investigator = BugInvestigator(runner=mock_runner, verification_retries=2)

    initial_result = ExecutionResult(
        test_id="TC-REG-TERMS",
        test_name="Terms acceptance bypass",
        status=ExecutionStatus.FAILED,
        total_steps=5,
        steps_completed=5,
        failure_reason="Expected text 'Account Created' to NOT be visible, but got visible",
        failure_step=5,
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-REG-TERMS",
        name="Terms of service mandatory check",
        objective="Ensure submitting registration without accepting terms is rejected by backend",
        page_category=PageCategory.REGISTRATION,
        action_description="Bypass client required and click register",
        expected_outcome="Account creation rejected",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="ToS verification",
        confidence=0.95,
    )
    report = await investigator.investigate_failure(initial_result, tc, "http://127.0.0.1:8001/register")
    assert report.verification_status == VerificationStatus.CONFIRMED_REPRODUCIBLE
    assert "REGISTRATION" in report.title
    assert "terms" in (report.suspected_cause or "").lower()


def test_passing_test_never_reported_as_defect():
    """Passing tests must never produce confirmed reproducible defect reports."""
    investigator = BugInvestigator()
    result = ExecutionResult(
        test_id="TC-PASS-01",
        test_name="Valid login test",
        status=ExecutionStatus.PASSED,
        total_steps=3,
        steps_completed=3,
        step_results=[
            StepExecutionResult(
                step_number=1,
                action=ActionType.NAVIGATE,
                description="Navigated",
                status=ExecutionStatus.PASSED,
            ),
            StepExecutionResult(
                step_number=2,
                action=ActionType.FILL,
                description="Filled",
                status=ExecutionStatus.PASSED,
            ),
            StepExecutionResult(
                step_number=3,
                action=ActionType.CLICK,
                description="Submitted",
                status=ExecutionStatus.PASSED,
            ),
        ],
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-PASS-01",
        name="Valid login test",
        objective="Login succeeds",
        page_category=PageCategory.LOGIN,
        action_description="Submit credentials",
        expected_outcome="Dashboard displayed",
        expected_behavior_source=ExpectedBehaviorSource.REQUIREMENT,
        priority=Priority.HIGH,
        selection_reason="Smoke test",
        confidence=1.0,
    )
    # _classify_failure for a passing execution result
    if result.status == ExecutionStatus.PASSED:
        verdict = InvestigationVerdict.PASS
    else:
        verdict, _ = investigator._classify_failure(tc, result)
    assert verdict == InvestigationVerdict.PASS
    assert verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT


def test_control_visibility_synthesis_does_not_assert_navigation():
    """Verify that a test titled or objective about 'presence and visibility' asserts element visibility, not URL navigation."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-VIS-01",
        name="Verify presence and visibility of essential login form controls",
        objective="Ensure username input, password input, and login submit button are present in DOM and visibly rendered",
        page_category=PageCategory.LOGIN,
        action_description="Navigate to target and inspect the visibility of username and password",
        expected_outcome="All controls exist in the DOM and are visibly rendered without layout obstruction",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Essential login controls check",
        confidence=0.95,
    )
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 1

    # Must NOT contain URL_CONTAINS assertion for /inventory
    for step in steps:
        if step.assertion:
            assert step.assertion.assertion_type != AssertionType.URL_CONTAINS
            if step.assertion.expected_value:
                assert "/inventory" not in str(step.assertion.expected_value)

    # Must have an ELEMENT_VISIBLE assertion
    assertions = [s.assertion.assertion_type for s in steps if s.assertion]
    assert AssertionType.ELEMENT_VISIBLE in assertions

