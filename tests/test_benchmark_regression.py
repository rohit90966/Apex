"""
Regression test suite for VerityQA Planted-Bug Benchmark & Execution Engine.
Covers all 4 planted defects, route classification, step synthesis,
oracle defect filtering, and metric calculation guards.
"""
import pytest
from app.schemas import (
    TestCase,
    PageCategory,
    ExpectedBehaviorSource,
    Priority,
    ActionType,
    AssertionType,
    BugReport,
    BugSeverity,
    VerificationStatus,
)
from app.benchmark.manifest import BenchmarkEvaluator, PLANTED_BUG_MANIFEST, MANIFEST_BY_ID
from app.execution.runner import QAPlanRunner


# ── Step Synthesis Tests for the 4 Planted Defects ──────────────────────────

def test_login_password_length_step_synthesis():
    """Verify runner synthesizes 1-char password and length assertion for BUG-01."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-LOGIN-LEN",
        name="Verify password minimum length policy enforcement",
        objective="Ensure entering a password below the minimum length threshold is rejected with a clear length validation error.",
        page_category=PageCategory.LOGIN,
        action_description="Enter valid username and single character password ('a') to verify minimum length policy rejection.",
        expected_outcome="Validation explicitly rejects short password with clear length error message.",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Password policy boundary check",
        confidence=0.95,
    )
    steps = runner.synthesize_steps(tc, "http://127.0.0.1:8001/login")
    assert len(steps) >= 3

    # Check password fill value
    pwd_step = next((s for s in steps if s.action == ActionType.FILL and "password" in (s.description or "").lower()), None)
    assert pwd_step is not None
    assert pwd_step.value == "a"  # 1-char boundary value

    # Check submit assertion
    click_step = next((s for s in steps if s.action == ActionType.CLICK and s.assertion), None)
    assert click_step is not None
    assert click_step.assertion.assertion_type == AssertionType.TEXT_VISIBLE
    assert "at least" in click_step.assertion.expected_value.lower()


def test_registration_terms_bypass_step_synthesis():
    """Verify runner synthesizes unchecked terms and removes client-side required for BUG-02."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-REG-TERMS",
        name="Verify mandatory Terms of Service acceptance enforcement",
        objective="Ensure registration attempt without accepting terms of service is rejected and prevents account creation.",
        page_category=PageCategory.REGISTRATION,
        action_description="Complete registration details without checking terms checkbox, then submit registration form.",
        expected_outcome="Server and client block registration, displaying mandatory terms acceptance validation requirement.",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Terms compliance check",
        confidence=0.95,
    )
    steps = runner.synthesize_steps(tc, "http://127.0.0.1:8001/registration")

    # Must contain EVAL_JS to remove client required attribute
    eval_step = next((s for s in steps if s.action == ActionType.EVAL_JS), None)
    assert eval_step is not None
    assert "terms" in eval_step.value

    # Submit step must assert that Account Created is NOT visible
    submit_step = next((s for s in steps if s.action == ActionType.CLICK and s.assertion), None)
    assert submit_step is not None
    assert submit_step.assertion.assertion_type == AssertionType.TEXT_NOT_VISIBLE
    assert submit_step.assertion.expected_value == "Account Created"


def test_registration_password_mismatch_step_synthesis():
    """Verify runner synthesizes differing passwords and mismatch assertion for BUG-03."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-REG-MISMATCH",
        name="Verify password confirmation mismatch validation",
        objective="Ensure entering mismatched passwords in primary and confirmation fields triggers immediate validation error.",
        page_category=PageCategory.REGISTRATION,
        action_description="Enter differing strings in password and confirm password fields.",
        expected_outcome="System indicates password mismatch and blocks account creation.",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Password confirmation check",
        confidence=0.95,
    )
    steps = runner.synthesize_steps(tc, "http://127.0.0.1:8001/registration")

    pwd_step = next((s for s in steps if s.action == ActionType.FILL and "input#reg-password" in (s.selector or "")), None)
    confirm_step = next((s for s in steps if s.action == ActionType.FILL and "input#confirm-password" in (s.selector or "")), None)
    assert pwd_step is not None
    assert confirm_step is not None
    assert pwd_step.value != confirm_step.value

    # Submit step must assert mismatch text
    submit_step = next((s for s in steps if s.action == ActionType.CLICK and s.assertion), None)
    assert submit_step is not None
    assert submit_step.assertion.assertion_type == AssertionType.TEXT_VISIBLE
    assert "match" in submit_step.assertion.expected_value.lower()


def test_search_empty_query_step_synthesis():
    """Verify runner synthesizes whitespace query and input prompt assertion for BUG-04."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-SEARCH-EMPTY",
        name="Verify empty and whitespace search query input validation",
        objective="Ensure executing a search with empty or whitespace input requires query input and prompts for search terms.",
        page_category=PageCategory.SEARCH,
        action_description="Submit search form with whitespace or empty input.",
        expected_outcome="Application prompts user with clear message requiring search terms and does not accept blank query as valid search.",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.MEDIUM,
        selection_reason="Empty search boundary check",
        confidence=0.88,
    )
    steps = runner.synthesize_steps(tc, "http://127.0.0.1:8001/search")

    fill_step = next((s for s in steps if s.action == ActionType.FILL), None)
    assert fill_step is not None
    assert fill_step.value == "   "

    submit_step = next((s for s in steps if s.action == ActionType.CLICK and s.assertion), None)
    assert submit_step is not None
    assert submit_step.assertion.assertion_type == AssertionType.TEXT_VISIBLE
    assert "enter" in submit_step.assertion.expected_value.lower()


# ── Benchmark Evaluator Filtering and Metric Tests ───────────────────────────

def test_evaluator_filters_out_test_oracle_defects():
    """Test oracle defects must never count as true positives or false positives."""
    evaluator = BenchmarkEvaluator()
    bugs = [
        BugReport(
            bug_id="BUG-ORACLE-01",
            title="[TEST ORACLE] Login: Assertion specification error",
            severity=BugSeverity.LOW,
            verification_status=VerificationStatus.TEST_ORACLE_DEFECT,
            reproduction_rate="3/3",
            test_id="TC-LOGIN-REQ",
            target_url="http://127.0.0.1:8001/login",
            summary="Expected text 'required' was not visible",
            expected_behavior="Text required visible",
            actual_behavior="Expected text 'required' was not visible",
            suspected_cause="Test oracle flaw: asserted literal string",
        )
    ]
    summary = evaluator.evaluate(bugs)
    assert summary.true_positives_detected == 0
    assert summary.false_positives == 0
    assert summary.missed_bugs == 4
    assert summary.precision == 0.0
    assert summary.recall == 0.0
    assert summary.f1_score == 0.0


def test_evaluator_all_four_planted_bugs_detected():
    """All 4 planted bugs discovered correctly yield 100% precision, recall, and F1."""
    evaluator = BenchmarkEvaluator()
    bugs = [
        BugReport(
            bug_id="BUG-01-REP",
            title="[Authentication] Login password length validation bypass",
            severity=BugSeverity.MEDIUM,
            verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
            reproduction_rate="3/3",
            test_id="TC-LOGIN-LEN",
            target_url="http://127.0.0.1:8001/login",
            summary="Server accepts 1-character password without enforcing minimum length policy",
            expected_behavior="Reject short password",
            actual_behavior="Expected text 'at least' was not visible",
            suspected_cause="Password length validation bypass",
        ),
        BugReport(
            bug_id="BUG-02-REP",
            title="[Registration] Terms of service acceptance bypass",
            severity=BugSeverity.HIGH,
            verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
            reproduction_rate="3/3",
            test_id="TC-REG-TERMS",
            target_url="http://127.0.0.1:8001/registration",
            summary="Registration succeeds without terms checkbox being checked",
            expected_behavior="Reject registration without terms",
            actual_behavior="Expected text 'Account Created' to NOT be visible",
            suspected_cause="Terms acceptance validation bypass",
        ),
        BugReport(
            bug_id="BUG-03-REP",
            title="[Registration] Password mismatch bypass",
            severity=BugSeverity.HIGH,
            verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
            reproduction_rate="3/3",
            test_id="TC-REG-MISMATCH",
            target_url="http://127.0.0.1:8001/registration",
            summary="Server creates account despite mismatched password and confirm_password",
            expected_behavior="Reject mismatch",
            actual_behavior="Expected text 'Passwords do not match' was not visible",
            suspected_cause="Password confirmation validation missing",
        ),
        BugReport(
            bug_id="BUG-04-REP",
            title="[Search] Empty search query accepted",
            severity=BugSeverity.MEDIUM,
            verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
            reproduction_rate="3/3",
            test_id="TC-SEARCH-EMPTY",
            target_url="http://127.0.0.1:8001/search",
            summary="Server returns generic 'no results found for' instead of input validation prompt",
            expected_behavior="Prompt for search terms",
            actual_behavior="Expected text 'Please enter a search term' was not visible",
            suspected_cause="Empty search query validation missing",
        ),
    ]
    summary = evaluator.evaluate(bugs)
    assert summary.true_positives_detected == 4
    assert summary.false_positives == 0
    assert summary.missed_bugs == 0
    assert summary.precision == 1.0
    assert summary.recall == 1.0
    assert summary.f1_score == 1.0
    assert set(summary.detected_bug_ids) == {"BUG-01", "BUG-02", "BUG-03", "BUG-04"}
    assert len(summary.detailed_reports) == 4


def test_evaluator_zero_division_safety():
    """Ensure evaluator handles empty lists and 0 TP/FP gracefully without ZeroDivisionError."""
    evaluator = BenchmarkEvaluator()
    summary = evaluator.evaluate([])
    assert summary.precision == 0.0
    assert summary.recall == 0.0
    assert summary.f1_score == 0.0
