"""
Tests for BugInvestigator evidence-based classification.
Validates the 7-class InvestigationVerdict system.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.schemas import (
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    ActionType,
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.bugs.investigator import (
    BugInvestigator,
    InvestigationVerdict,
    _is_locator_error,
    _is_automation_error,
    _is_environment_error,
    _is_test_definition_error,
)


def _make_test_case(
    name="Test",
    objective="Verify behavior",
    page_category=PageCategory.LOGIN,
    derived_values=None,
):
    return TestCase(
        test_id="TC-001",
        name=name,
        objective=objective,
        page_category=page_category,
        action_description="Submit form",
        expected_outcome="Expected outcome",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.MEDIUM,
        selection_reason="Test",
        confidence=0.8,
        derived_values=derived_values,
    )


def _make_result(
    status=ExecutionStatus.FAILED,
    failure_reason="",
    step_results=None,
    dom_snapshot=None,
    console_logs=None,
):
    return ExecutionResult(
        test_id="TC-001",
        test_name="Test",
        status=status,
        total_steps=1,
        steps_completed=1,
        failure_reason=failure_reason,
        step_results=step_results or [],
        dom_snapshot_snippet=dom_snapshot,
        console_logs=console_logs or [],
    )


def _make_step(error_message="", assertion_expected=None):
    return StepExecutionResult(
        step_number=1,
        action=ActionType.CLICK,
        description="Click submit",
        status=ExecutionStatus.FAILED,
        error_message=error_message,
        assertion_expected=assertion_expected,
    )


class TestLocatorErrorDetection:
    def test_detects_playwright_waiting_for_selector(self):
        result = _make_result(
            failure_reason="Playwright error: waiting for selector 'input#username'"
        )
        assert _is_locator_error(result) is True

    def test_detects_element_not_found_in_step(self):
        step = _make_step(error_message="waiting for selector 'input#username' to be visible")
        result = _make_result(step_results=[step])
        assert _is_locator_error(result) is True

    def test_no_false_positive_on_text_not_visible(self):
        """Text not visible failure is NOT automatically a locator error."""
        result = _make_result(failure_reason="Expected text 'Welcome' was not visible")
        assert _is_locator_error(result) is False

    def test_detects_timeout_exceeded(self):
        result = _make_result(
            failure_reason="timeout exceeded while waiting for element"
        )
        assert _is_locator_error(result) is True


class TestAutomationErrorDetection:
    def test_detects_execution_context_destroyed(self):
        result = _make_result(failure_reason="execution context was destroyed")
        assert _is_automation_error(result) is True

    def test_detects_target_closed(self):
        result = _make_result(failure_reason="Target closed")
        assert _is_automation_error(result) is True

    def test_no_false_positive_on_assertion_failure(self):
        result = _make_result(failure_reason="Expected text 'required' was not visible")
        assert _is_automation_error(result) is False


class TestEnvironmentErrorDetection:
    def test_detects_connection_refused(self):
        result = _make_result(failure_reason="connection refused on port 8001")
        assert _is_environment_error(result) is True

    def test_detects_network_error(self):
        result = _make_result(failure_reason="net::ERR_CONNECTION_REFUSED")
        assert _is_environment_error(result) is True


class TestTestDefinitionErrorDetection:
    def test_detects_expected_required_text(self):
        result = _make_result(
            failure_reason="expected text 'required' was not visible"
        )
        tc = _make_test_case()
        is_err, rationale = _is_test_definition_error(tc, result)
        assert is_err is True
        assert "required" in rationale.lower() or "html5" in rationale.lower()

    def test_detects_welcome_in_non_login_test(self):
        step = _make_step(assertion_expected="Welcome")
        result = _make_result(step_results=[step])
        tc = _make_test_case(
            objective="Verify password masking obscures input",
            name="Password Masking Test",
            page_category=PageCategory.LOGIN,
        )
        # Override objective to be non-login-success
        tc_modified = tc.model_copy(update={
            "objective": "Verify password masking",
            "name": "Password field masking test",
        })
        is_err, rationale = _is_test_definition_error(tc_modified, result)
        assert is_err is True
        assert "welcome" in rationale.lower()

    def test_detects_assumed_at_least_without_evidence(self):
        """'at least N characters' assertion without DOM minlength evidence is a test oracle error."""
        step = _make_step(assertion_expected="at least 6 characters")
        result = _make_result(
            failure_reason="Expected text 'at least' was not visible",
            step_results=[step],
        )
        tc = _make_test_case(
            objective="Verify password length minimum enforcement",
            name="Short password rejected",
            derived_values=None,  # No evidence
        )
        is_err, rationale = _is_test_definition_error(tc, result)
        assert is_err is True
        assert "assumed" in rationale.lower() or "no minlength" in rationale.lower() \
               or "not traceable" in rationale.lower() or "minlength" in rationale.lower()

    def test_no_false_positive_for_genuine_defect(self):
        """Genuine product failure should NOT be flagged as test definition error."""
        result = _make_result(
            failure_reason="Expected text 'Terms acceptance' not shown after submission bypass"
        )
        tc = _make_test_case(
            objective="Verify terms of service acceptance is enforced on backend",
            name="ToS bypass test",
        )
        is_err, rationale = _is_test_definition_error(tc, result)
        # Should not be flagged as test definition error
        assert is_err is False


class TestBugInvestigatorClassification:
    def test_locator_error_not_classified_as_product_defect(self):
        """CRITICAL: LOCATOR_ERROR must never become VERIFIED_PRODUCT_DEFECT."""
        investigator = BugInvestigator()
        result = _make_result(
            failure_reason="Playwright error: waiting for selector 'input#username'"
        )
        tc = _make_test_case()
        verdict, rationale = investigator._classify_failure(tc, result)
        assert verdict == InvestigationVerdict.LOCATOR_ERROR
        assert verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT

    def test_automation_error_not_classified_as_product_defect(self):
        result = _make_result(failure_reason="execution context was destroyed")
        tc = _make_test_case()
        investigator = BugInvestigator()
        verdict, _ = investigator._classify_failure(tc, result)
        assert verdict == InvestigationVerdict.AUTOMATION_ERROR
        assert verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT

    def test_test_definition_error_classification(self):
        result = _make_result(
            failure_reason="expected text 'required' was not visible"
        )
        tc = _make_test_case()
        investigator = BugInvestigator()
        verdict, _ = investigator._classify_failure(tc, result)
        assert verdict == InvestigationVerdict.TEST_DEFINITION_ERROR

    def test_inconclusive_for_unknown_failure(self):
        result = _make_result(
            failure_reason="Page showed 'Error: Internal Server Error'"
        )
        tc = _make_test_case()
        investigator = BugInvestigator()
        verdict, _ = investigator._classify_failure(tc, result)
        # Unknown failure starts as INCONCLUSIVE
        assert verdict == InvestigationVerdict.INCONCLUSIVE

    @pytest.mark.asyncio
    async def test_investigate_returns_bug_report_with_correct_verdict_in_title(self):
        """investigate_failure must include verdict classification in report title."""
        mock_runner = MagicMock()
        investigator = BugInvestigator(runner=mock_runner)

        result = _make_result(
            failure_reason="Playwright error: waiting for selector 'input#username'"
        )
        tc = _make_test_case()

        report = await investigator.investigate_failure(result, tc, "http://example.com")
        assert "LOCATOR_ERROR" in report.title

    @pytest.mark.asyncio
    async def test_locator_error_not_retried(self):
        """Locator errors must NOT trigger retry attempts."""
        mock_runner = MagicMock()
        mock_runner.run_test = AsyncMock()
        investigator = BugInvestigator(runner=mock_runner)

        result = _make_result(
            failure_reason="Playwright error: waiting for selector 'input#bad'"
        )
        tc = _make_test_case()

        await investigator.investigate_failure(result, tc, "http://example.com")
        # run_test should NOT have been called for a locator error
        mock_runner.run_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_inconclusive_is_retried(self):
        """INCONCLUSIVE failures MUST be retried to confirm reproducibility."""
        mock_runner = MagicMock()
        mock_runner.run_test = AsyncMock(return_value=_make_result(
            status=ExecutionStatus.FAILED,
            failure_reason="Server returned error page"
        ))
        investigator = BugInvestigator(runner=mock_runner, verification_retries=2)

        result = _make_result(
            failure_reason="Server returned error page"
        )
        tc = _make_test_case()

        report = await investigator.investigate_failure(result, tc, "http://example.com")
        assert mock_runner.run_test.call_count == 2  # retried twice

    def test_severity_low_for_non_product_defects(self):
        investigator = BugInvestigator()
        result = _make_result()
        tc = _make_test_case()
        severity = investigator._determine_severity(tc, result, InvestigationVerdict.LOCATOR_ERROR)
        from app.schemas import BugSeverity
        assert severity == BugSeverity.LOW

    def test_no_credentials_in_report(self):
        """Bug report must not expose API keys or passwords."""
        from app.bugs.investigator import BugInvestigator
        import asyncio

        async def _run():
            mock_runner = MagicMock()
            investigator = BugInvestigator(runner=mock_runner)
            result = _make_result(failure_reason="execution context was destroyed")
            tc = _make_test_case()
            report = await investigator.investigate_failure(result, tc, "http://example.com")
            report_text = str(report.model_dump())
            # Common secret patterns must not appear
            import re
            assert not re.search(r"GOOGLE_API_KEY|api_key=|Bearer [A-Za-z0-9]+", report_text, re.I)
            return report

        asyncio.run(_run())
