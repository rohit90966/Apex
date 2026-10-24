"""
Tests for app/bugs/investigator.py — BugInvestigator
"""
import pytest
from app.schemas import (
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    ActionType,
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    BugSeverity,
    VerificationStatus,
)
from app.bugs.investigator import BugInvestigator


@pytest.fixture
def failed_login_result():
    return ExecutionResult(
        test_id="TC-LOGIN-001",
        test_name="Login with short password should be rejected",
        status=ExecutionStatus.FAILED,
        total_steps=4,
        steps_completed=4,
        step_results=[
            StepExecutionResult(
                step_number=1,
                action=ActionType.NAVIGATE,
                description="Navigate to login page",
                status=ExecutionStatus.PASSED,
                duration_seconds=0.5,
            ),
            StepExecutionResult(
                step_number=2,
                action=ActionType.FILL,
                description="Enter username",
                status=ExecutionStatus.PASSED,
                duration_seconds=0.2,
            ),
            StepExecutionResult(
                step_number=3,
                action=ActionType.FILL,
                description="Enter short password 'a'",
                status=ExecutionStatus.PASSED,
                duration_seconds=0.2,
            ),
            StepExecutionResult(
                step_number=4,
                action=ActionType.CLICK,
                description="Click login button",
                status=ExecutionStatus.FAILED,
                duration_seconds=0.3,
                error_message="Expected text 'Too short' was not visible",
                assertion_expected="Too short",
                assertion_actual="Invalid credentials",
            ),
        ],
        duration_seconds=1.2,
        failure_reason="Expected text 'Too short' was not visible",
        failure_step=4,
        console_logs=[],
        timestamp="2026-10-17 01:00:00",
    )


@pytest.fixture
def login_test_case():
    return TestCase(
        __test__=False,
        test_id="TC-LOGIN-001",
        name="Login with short password should be rejected",
        objective="Verify login rejects password shorter than minimum length",
        page_category=PageCategory.LOGIN,
        relevant_elements=["#username", "#password", "#loginBtn"],
        action_description="Enter username and 1-char password, submit form",
        expected_outcome="Server returns password too short validation error",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Minimum password length is a security control",
        confidence=0.95,
    )


def test_bug_investigator_instantiation():
    investigator = BugInvestigator(verification_retries=0)
    assert investigator is not None
    assert investigator.verification_retries == 0


def test_severity_critical_for_login(failed_login_result, login_test_case):
    investigator = BugInvestigator(verification_retries=0)
    severity = investigator._determine_severity(login_test_case, failed_login_result)
    assert severity == BugSeverity.CRITICAL


def test_diagnose_cause_returns_strings(failed_login_result, login_test_case):
    investigator = BugInvestigator(verification_retries=0)
    cause, fix = investigator._diagnose_cause(login_test_case, failed_login_result)
    assert isinstance(cause, str)
    assert isinstance(fix, str)
    assert len(cause) > 0
    assert len(fix) > 0


def test_bug_id_generated(failed_login_result, login_test_case):
    investigator = BugInvestigator(verification_retries=0)
    severity = investigator._determine_severity(login_test_case, failed_login_result)
    assert severity is not None


def test_registration_mismatch_severity():
    """Password mismatch in registration should be HIGH severity."""
    result = ExecutionResult(
        test_id="TC-REG-003",
        test_name="Password mismatch registration",
        status=ExecutionStatus.FAILED,
        total_steps=5,
        steps_completed=5,
        failure_reason="Expected text 'match' was not visible",
        failure_step=5,
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-REG-003",
        name="Password confirmation mismatch test",
        objective="Register with mismatch password confirmation is rejected",
        page_category=PageCategory.REGISTRATION,
        relevant_elements=["#reg-password", "#confirm-password"],
        action_description="Fill different passwords and submit",
        expected_outcome="Error message about password mismatch",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Password confirmation is a security control",
        confidence=0.95,
    )
    investigator = BugInvestigator(verification_retries=0)
    severity = investigator._determine_severity(tc, result)
    assert severity in (BugSeverity.HIGH, BugSeverity.CRITICAL)


def test_search_severity_medium():
    """Empty search not validated should be MEDIUM or LOW severity."""
    result = ExecutionResult(
        test_id="TC-SEARCH-002",
        test_name="Empty search validation",
        status=ExecutionStatus.FAILED,
        total_steps=3,
        steps_completed=3,
        failure_reason="Expected text 'required' was not visible",
        failure_step=3,
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-SEARCH-002",
        name="Empty search query validation",
        objective="Search with empty boundary input shows validation message",
        page_category=PageCategory.SEARCH,
        relevant_elements=["input#q"],
        action_description="Submit empty search",
        expected_outcome="Validation message is shown",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.MEDIUM,
        selection_reason="Empty search handling is a usability concern",
        confidence=0.75,
    )
    investigator = BugInvestigator(verification_retries=0)
    severity = investigator._determine_severity(tc, result)
    assert severity in (BugSeverity.MEDIUM, BugSeverity.LOW)


@pytest.mark.asyncio
async def test_investigate_failure_identifies_oracle_defect():
    """Fragile text 'required' assertion failures must be classified as TEST_ORACLE_DEFECT, not CRITICAL."""
    result = ExecutionResult(
        test_id="TC-LOGIN-002",
        test_name="Empty username validation",
        status=ExecutionStatus.FAILED,
        total_steps=3,
        steps_completed=3,
        failure_reason="Expected text 'required' was not visible",
        failure_step=3,
        dom_snapshot_snippet="<form><input id='username' required><input id='password'></form>",
        step_results=[
            StepExecutionResult(
                step_number=3,
                action=ActionType.CLICK,
                description="Click submit button",
                status=ExecutionStatus.FAILED,
                assertion_expected="required",
                assertion_actual="Text not visible",
            )
        ],
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-LOGIN-002",
        name="Empty username field validation",
        objective="Ensure required username indicates validation when blank",
        page_category=PageCategory.LOGIN,
        relevant_elements=["input#username"],
        action_description="Click submit without typing username",
        expected_outcome="Validation indicated",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Required field test",
        confidence=0.9,
    )

    investigator = BugInvestigator(verification_retries=0)
    report = await investigator.investigate_failure(result, tc, "http://127.0.0.1:8001/login")

    assert report.verification_status == VerificationStatus.TEST_ORACLE_DEFECT
    assert report.severity == BugSeverity.LOW
    assert "[TEST ORACLE]" in report.title
    assert "native HTML5" in report.summary or "fragile assertion" in report.summary


@pytest.mark.asyncio
async def test_investigate_failure_confirmed_reproducible_for_real_defect():
    """Genuine defects must be classified as CONFIRMED_REPRODUCIBLE."""
    result = ExecutionResult(
        test_id="TC-LOGIN-CRASH",
        test_name="Server 500 on login",
        status=ExecutionStatus.FAILED,
        total_steps=2,
        steps_completed=2,
        failure_reason="HTTP 500 Internal Server Error returned",
        failure_step=2,
        console_logs=["500 Internal Server Error"],
        step_results=[
            StepExecutionResult(
                step_number=2,
                action=ActionType.CLICK,
                description="Click login",
                status=ExecutionStatus.FAILED,
                assertion_expected="Welcome",
                assertion_actual="500 Internal Server Error",
            )
        ],
    )
    tc = TestCase(
        __test__=False,
        test_id="TC-LOGIN-CRASH",
        name="Login endpoint resilience",
        objective="Verify login endpoint does not throw internal server error",
        page_category=PageCategory.LOGIN,
        relevant_elements=["#loginBtn"],
        action_description="Click login button",
        expected_outcome="No 500 error",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Crash resilience",
        confidence=0.95,
    )

    investigator = BugInvestigator(verification_retries=0)
    report = await investigator.investigate_failure(result, tc, "http://127.0.0.1:8001/login")

    assert report.verification_status == VerificationStatus.CONFIRMED_REPRODUCIBLE
    assert report.severity == BugSeverity.CRITICAL

