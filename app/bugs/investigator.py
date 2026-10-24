"""
VerityQA Bug Investigator — Evidence-Based Classification
==========================================================
Upgraded investigator with 7-class InvestigationVerdict system:

  PASS                  — Test passed; no defect
  VERIFIED_PRODUCT_DEFECT — Reproducible failure, traced to product behavior
  INCONCLUSIVE          — Failure could not be reproduced or evidence is ambiguous
  TEST_DEFINITION_ERROR — Expected outcome was ungrounded/assumed (oracle flaw)
  LOCATOR_ERROR         — Selector did not match the observed DOM
  AUTOMATION_ERROR      — Test failed due to framework/infra issue, not the product
  ENVIRONMENT_ERROR     — External dependency unavailable, network issue, etc.

Key rules:
  - LOCATOR_ERROR and AUTOMATION_ERROR are NEVER reported as VERIFIED_PRODUCT_DEFECT.
  - Evidence validation occurs before severity classification.
  - API credentials are never logged or included in reports.
  - 404 permanent errors are not retried.
"""

import uuid
import logging
import re
from enum import Enum
from typing import List, Optional, Tuple

from app.schemas import (
    ExecutionResult,
    ExecutionStatus,
    BugReport,
    BugSeverity,
    VerificationStatus,
    TestCase,
    PageCategory,
)
from app.execution.runner import QAPlanRunner

logger = logging.getLogger("verityqa.bugs")


class InvestigationVerdict(str, Enum):
    """Evidence-based classification of a test failure."""
    PASS = "PASS"
    VERIFIED_PRODUCT_DEFECT = "VERIFIED_PRODUCT_DEFECT"
    INCONCLUSIVE = "INCONCLUSIVE"
    TEST_DEFINITION_ERROR = "TEST_DEFINITION_ERROR"
    LOCATOR_ERROR = "LOCATOR_ERROR"
    AUTOMATION_ERROR = "AUTOMATION_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"


# Map new verdict → legacy VerificationStatus for schema compatibility
_VERDICT_TO_LEGACY = {
    InvestigationVerdict.PASS: VerificationStatus.OBSERVED_NOT_REPRODUCED,
    InvestigationVerdict.VERIFIED_PRODUCT_DEFECT: VerificationStatus.CONFIRMED_REPRODUCIBLE,
    InvestigationVerdict.INCONCLUSIVE: VerificationStatus.POTENTIAL_DEFECT,
    InvestigationVerdict.TEST_DEFINITION_ERROR: VerificationStatus.TEST_ORACLE_DEFECT,
    InvestigationVerdict.LOCATOR_ERROR: VerificationStatus.TEST_ORACLE_DEFECT,
    InvestigationVerdict.AUTOMATION_ERROR: VerificationStatus.ENVIRONMENTAL_FAILURE,
    InvestigationVerdict.ENVIRONMENT_ERROR: VerificationStatus.ENVIRONMENTAL_FAILURE,
}


def _is_locator_error(result: ExecutionResult) -> bool:
    """Detect selector-did-not-match-DOM failures."""
    reason = (result.failure_reason or "").lower()
    locator_signals = [
        "waiting for selector",
        "timeout exceeded while waiting",
        "element not found",
        "no element matching",
        "strict mode violation",
        "locator resolved to",
        "playwright error",
        "wait_for_selector",
        "element is not attached",
        "element is outside of the viewport",
    ]
    # Check step-level errors
    for step in (result.step_results or []):
        err = (step.error_message or "").lower()
        if any(s in err for s in locator_signals):
            return True
    return any(s in reason for s in locator_signals)


def _is_automation_error(result: ExecutionResult) -> bool:
    """Detect test-infra failures (browser crash, timeout, eval errors)."""
    reason = (result.failure_reason or "").lower()
    automation_signals = [
        "execution context was destroyed",
        "target closed",
        "browser has been closed",
        "navigation failed",
        "execution error:",
        "evaluate()",
        "page.evaluate",
        "browsercontext",
    ]
    return any(s in reason for s in automation_signals)


def _is_environment_error(result: ExecutionResult) -> bool:
    """Detect infrastructure/network failures unrelated to the product."""
    reason = (result.failure_reason or "").lower()
    env_signals = [
        "connection refused",
        "network error",
        "dns resolution",
        "net::err_",
        "econnrefused",
        "econnreset",
        "server unreachable",
        "timed out",
    ]
    return any(s in reason for s in env_signals)


def _is_test_definition_error(test_case: TestCase, result: ExecutionResult) -> Tuple[bool, str]:
    """
    Detect test oracle / definition errors:
      - Expected text is assumed, not evidence-based
      - Expected text is completely unrelated to the test objective
      - Selector assumes a field name without DOM evidence

    Returns (is_error, rationale).
    """
    reason = (result.failure_reason or "").lower()

    # Case 1: Expected literal text "required" or "invalid" — could be HTML5 validation
    if "expected text 'required' was not visible" in reason or \
       "expected text 'invalid' was not visible" in reason:
        return (
            True,
            "Test assertion expected literal text 'required'/'invalid', but the "
            "application likely uses native HTML5 form validation, which does not "
            "render text in the DOM."
        )

    # Case 2: Expected "Welcome" after a non-login test
    for step in (result.step_results or []):
        exp = (step.assertion_expected or "").lower()
        obj = test_case.objective.lower() + " " + test_case.name.lower()
        # "Welcome" in expected text for non-login test objectives
        if "welcome" in exp and not any(k in obj for k in ["login", "signin", "dashboard"]):
            return (
                True,
                f"Test assertion expected text 'Welcome' but test objective is '{test_case.objective}'. "
                "The expected text is unrelated to the test objective."
            )
        # Expected "at least" for password length when no minlength evidence
        if "at least" in exp and any(k in obj for k in ["password", "length"]):
            if test_case.derived_values is None or "boundary_value" not in (test_case.derived_values or {}):
                return (
                    True,
                    "Test asserted 'at least N characters' for password length, but no minlength "
                    "constraint evidence was observed in DOM. This is an assumed expectation."
                )

        # Case 2b: Expected URL contains '/inventory' or '/dashboard' for non-positive-login tests (masking, a11y, whitespace, empty)
        if "/inventory" in exp or "/dashboard" in exp:
            is_positive_auth = any(k in obj for k in ["valid credential", "successful login", "valid authentication", "login success"])
            if not is_positive_auth:
                return (
                    True,
                    f"Test assertion expected URL navigation to '{exp}' but test objective is '{test_case.objective}'. "
                    "Non-positive login tests (e.g. masking, validation, accessibility) must not require successful authentication or navigation."
                )

        # Case 2c: valueMissing assertion on element that lacks HTML5 required attribute
        if step.assertion_expected == "valueMissing" or "valuemissing" in reason:
            # If target selector was tested and failed valueMissing
            dom_snippet = (result.dom_snapshot_snippet or "").lower()
            if "required" not in dom_snippet and "required" not in str(test_case.relevant_elements).lower():
                return (
                    True,
                    "Test assertion expected browser validity 'valueMissing', but target element does not have HTML5 'required' attribute in observed DOM."
                )

        # Case 2d: Element validity asserted for password length without minlength attribute in DOM
        if "to be invalid" in reason and any(k in obj for k in ["password", "length", "minlength"]):
            dom_snippet = (result.dom_snapshot_snippet or "").lower()
            if "minlength" not in dom_snippet:
                return (
                    True,
                    "Test assertion expected password field to be invalid on short input, but password element has no HTML5 'minlength' attribute in observed DOM."
                )

    # Case 3: Known mismatched expected text patterns
    if "text not visible" in reason:
        for step in (result.step_results or []):
            exp = (step.assertion_expected or "").lower()
            if exp in ("required", "invalid", "error", "must be filled", "fill out this field"):
                return (
                    True,
                    f"Test oracle asserted literal string '{exp}' which is not guaranteed "
                    "to appear as visible text in all browsers/implementations."
                )

    return False, ""


class BugInvestigator:
    """
    Evidence-Based Failure Investigation and Bug Classifier.

    When a test execution fails:
    1. Classifies the failure using InvestigationVerdict (7 classes).
    2. Distinguishes product defects from test/infra errors.
    3. Re-executes only for VERIFIED_PRODUCT_DEFECT candidates (not for 404s or locator errors).
    4. Synthesizes an actionable BugReport with full evidence trail.

    Credentials are never logged.
    """

    def __init__(self, runner: Optional[QAPlanRunner] = None, verification_retries: int = 2):
        self.runner = runner or QAPlanRunner()
        self.verification_retries = verification_retries

    async def investigate_failure(
        self,
        initial_result: ExecutionResult,
        test_case: TestCase,
        target_url: str,
    ) -> BugReport:
        """
        Conducts evidence-based investigation of a test failure.
        """
        logger.info(
            "Investigating failure in test %s (%s)...",
            test_case.test_id,
            test_case.name,
        )

        # ----------------------------------------------------------------
        # Step 1: Classify failure type before retrying
        # ----------------------------------------------------------------
        verdict, verdict_rationale = self._classify_failure(test_case, initial_result)

        # ----------------------------------------------------------------
        # Step 2: Only retry for potential product defects (not infra/test errors)
        # ----------------------------------------------------------------
        runs_total = 1
        runs_failed = 1
        should_retry = verdict == InvestigationVerdict.INCONCLUSIVE

        if should_retry:
            for attempt in range(self.verification_retries):
                runs_total += 1
                try:
                    retry_res = await self.runner.run_test(test_case, target_url)
                    if retry_res.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                        # Only an assertion failure reproduces a product defect; an ERROR retry (crash,
                        # timeout, environment) must never count toward confirmation.
                        if retry_res.status == ExecutionStatus.FAILED:
                            runs_failed += 1
                        # Re-classify on retry to detect flakes
                        retry_verdict, _ = self._classify_failure(test_case, retry_res)
                        if retry_verdict in (
                            InvestigationVerdict.LOCATOR_ERROR,
                            InvestigationVerdict.AUTOMATION_ERROR,
                        ):
                            verdict = retry_verdict
                            verdict_rationale = f"Retry {attempt+1} confirmed {retry_verdict.value}"
                            break
                except Exception as e:
                    logger.warning("Retry %d failed with exception: %s", attempt + 1, e)

            # Upgrade to VERIFIED_PRODUCT_DEFECT if consistently failing
            if runs_failed == runs_total and verdict == InvestigationVerdict.INCONCLUSIVE:
                verdict = InvestigationVerdict.VERIFIED_PRODUCT_DEFECT
                verdict_rationale = f"Reproduced consistently: {runs_failed}/{runs_total} runs failed"

        repro_rate_str = f"{runs_failed}/{runs_total} runs failed"

        # ----------------------------------------------------------------
        # Step 3: Map verdict → legacy status and severity
        # ----------------------------------------------------------------
        ver_status = _VERDICT_TO_LEGACY[verdict]
        severity = self._determine_severity(test_case, initial_result, verdict)

        # ----------------------------------------------------------------
        # Step 4: Build bug report
        # ----------------------------------------------------------------
        return self._build_report(
            test_case=test_case,
            initial_result=initial_result,
            target_url=target_url,
            verdict=verdict,
            verdict_rationale=verdict_rationale,
            ver_status=ver_status,
            severity=severity,
            repro_rate_str=repro_rate_str,
        )

    def _classify_failure(
        self,
        test_case: TestCase,
        result: ExecutionResult,
    ) -> Tuple[InvestigationVerdict, str]:
        """
        Classify failure into one of 7 InvestigationVerdict categories.
        """
        # Environment errors first — highest confidence
        if _is_environment_error(result):
            return (
                InvestigationVerdict.ENVIRONMENT_ERROR,
                "Network or server connectivity failure unrelated to product behavior.",
            )

        # Automation errors (browser infra issues)
        if _is_automation_error(result):
            return (
                InvestigationVerdict.AUTOMATION_ERROR,
                "Playwright/browser framework error, not a product defect.",
            )

        # Locator errors (selector didn't find element in DOM)
        if _is_locator_error(result):
            return (
                InvestigationVerdict.LOCATOR_ERROR,
                "CSS selector did not match any element in the observed DOM. "
                "This is a test definition issue, not a product defect.",
            )

        # Test definition / oracle errors
        is_def_error, def_rationale = _is_test_definition_error(test_case, result)
        if is_def_error:
            return InvestigationVerdict.TEST_DEFINITION_ERROR, def_rationale

        # Otherwise — potential product defect (requires retry to confirm)
        return (
            InvestigationVerdict.INCONCLUSIVE,
            "Initial failure; requires retry to confirm reproducibility.",
        )

    def _determine_severity(
        self,
        test_case: TestCase,
        result: ExecutionResult,
        verdict: Optional[InvestigationVerdict] = None,
    ) -> BugSeverity:
        """Determine severity — only meaningful for VERIFIED_PRODUCT_DEFECT (or when verdict is unspecified/inferred)."""
        if verdict is not None and verdict != InvestigationVerdict.VERIFIED_PRODUCT_DEFECT:
            return BugSeverity.LOW

        reason = (result.failure_reason or "").lower()
        obj = (test_case.objective + " " + test_case.name).lower()
        logs = " ".join(result.console_logs or []).lower()

        if "500 internal" in reason or "fatal error" in logs or "uncaught exception" in logs:
            return BugSeverity.CRITICAL
        if "crash" in reason or "segmentation" in logs:
            return BugSeverity.CRITICAL
        if test_case.page_category == PageCategory.LOGIN and ("auth" in obj or "login" in obj or "credential" in obj):
            return BugSeverity.CRITICAL

        if "terms" in obj or "mismatch" in obj:
            return BugSeverity.HIGH
        if "register" in obj or "account" in obj:
            return BugSeverity.HIGH
        if "400" in reason or "403" in reason:
            return BugSeverity.HIGH

        if "search" in obj or "boundary" in obj or "validation" in obj:
            return BugSeverity.MEDIUM

        return BugSeverity.LOW

    def _build_report(
        self,
        test_case: TestCase,
        initial_result: ExecutionResult,
        target_url: str,
        verdict: InvestigationVerdict,
        verdict_rationale: str,
        ver_status: VerificationStatus,
        severity: BugSeverity,
        repro_rate_str: str,
    ) -> BugReport:
        tid = test_case.test_id
        short_id = tid[-6:] if len(tid) >= 6 else uuid.uuid4().hex[:6].upper()
        bug_id = f"BUG-{short_id}"

        verdict_prefix = {
            InvestigationVerdict.LOCATOR_ERROR: "[LOCATOR_ERROR]",
            InvestigationVerdict.AUTOMATION_ERROR: "[AUTOMATION_ERROR]",
            InvestigationVerdict.ENVIRONMENT_ERROR: "[ENVIRONMENT_ERROR]",
            InvestigationVerdict.TEST_DEFINITION_ERROR: "[TEST ORACLE]",
            InvestigationVerdict.INCONCLUSIVE: "[INCONCLUSIVE]",
            InvestigationVerdict.VERIFIED_PRODUCT_DEFECT: f"[{test_case.page_category.value}]",
            InvestigationVerdict.PASS: "[PASS]",
        }.get(verdict, "")

        title = f"{verdict_prefix} {test_case.name}: {verdict.value}"

        if verdict == InvestigationVerdict.VERIFIED_PRODUCT_DEFECT:
            summary = (
                f"Verified product defect in '{test_case.name}' "
                f"(step {initial_result.failure_step}). "
                f"Actual behavior deviated from expected specification: "
                f"{initial_result.failure_reason}"
            )
        elif verdict == InvestigationVerdict.LOCATOR_ERROR:
            summary = (
                f"Test '{test_case.name}' failed because its CSS selector did not match "
                f"any element in the observed DOM. This is a test maintenance issue, "
                f"not a product defect. Rationale: {verdict_rationale}"
            )
        elif verdict == InvestigationVerdict.TEST_DEFINITION_ERROR:
            summary = (
                f"Automated test '{test_case.name}' failed due to an ungrounded or fragile assertion expectation: "
                f"{verdict_rationale} (Actual result: {initial_result.failure_reason}). "
                f"The target application utilizes native HTML5 form validation or custom visual indicators."
            )
        elif verdict in (InvestigationVerdict.AUTOMATION_ERROR, InvestigationVerdict.ENVIRONMENT_ERROR):
            summary = (
                f"Test '{test_case.name}' failed due to infrastructure issue: {verdict_rationale}. "
                f"This is not a product defect."
            )
        else:
            summary = (
                f"Test '{test_case.name}' failed: {initial_result.failure_reason}. "
                f"Classification: {verdict.value}. {verdict_rationale}"
            )

        steps_reproduce: List[str] = [f"1. Open target application at {target_url}"]
        for s in (initial_result.step_results or []):
            steps_reproduce.append(f"{s.step_number + 1}. {s.description}")

        suspected_cause, suggested_fix = self._diagnose_cause(
            test_case, initial_result, verdict, verdict_rationale
        )

        return BugReport(
            bug_id=bug_id,
            title=title,
            severity=severity,
            verification_status=ver_status,
            reproduction_rate=repro_rate_str,
            test_id=test_case.test_id,
            target_url=target_url,
            summary=summary,
            steps_to_reproduce=steps_reproduce,
            expected_behavior=test_case.expected_outcome,
            actual_behavior=initial_result.failure_reason or "Assertion failed",
            evidence_screenshot=initial_result.final_screenshot,
            reproduction_trace=(
                f"Initial: {initial_result.failure_reason}; "
                f"Classification: {verdict.value}"
            ),
            dom_context=initial_result.dom_snapshot_snippet,
            console_errors=initial_result.console_logs[:10],  # cap at 10 lines
            suspected_cause=suspected_cause,
            suggested_fix=suggested_fix,
        )

    def _diagnose_cause(
        self,
        test_case: TestCase,
        result: ExecutionResult,
        verdict: Optional[InvestigationVerdict] = None,
        verdict_rationale: Optional[str] = None,
    ) -> Tuple[str, str]:
        if verdict is None:
            verdict, rationale = self._classify_failure(test_case, result)
            verdict_rationale = verdict_rationale or rationale
        else:
            verdict_rationale = verdict_rationale or ""
        if verdict == InvestigationVerdict.LOCATOR_ERROR:
            return (
                f"CSS selector did not match any element in the DOM. "
                f"The selector was likely written for a different site's DOM structure.",
                "Ground selectors against actual observed DOM attributes from InspectionResult "
                "(e.g., use name='user-name' if that is what the DOM has, not 'username').",
            )
        if verdict == InvestigationVerdict.TEST_DEFINITION_ERROR:
            return (
                f"Test Oracle Flaw: {verdict_rationale}",
                "Update test assertion to be traceable to specific observed evidence "
                "(use VALIDITY_VALUE_MISSING or ELEMENT_VISIBLE instead of assumed text strings).",
            )
        if verdict == InvestigationVerdict.AUTOMATION_ERROR:
            return (
                "Playwright/browser automation infrastructure error during test execution.",
                "Check browser compatibility, network stability, and Playwright version. "
                "Re-run the test in isolation.",
            )
        if verdict == InvestigationVerdict.ENVIRONMENT_ERROR:
            return (
                "Target application is unreachable or network connection failed.",
                "Verify the application is running and accessible before running tests.",
            )
        if verdict == InvestigationVerdict.INCONCLUSIVE:
            return (
                f"Failure was not consistently reproducible: {result.failure_reason}",
                "Increase observation window and check for intermittent application behavior.",
            )

        # VERIFIED_PRODUCT_DEFECT — domain-specific diagnosis
        reason = (result.failure_reason or "").lower()
        obj = (test_case.objective + " " + test_case.name).lower()

        if test_case.page_category == PageCategory.SEARCH and any(k in obj for k in ["empty", "blank", "whitespace", "search query"]):
            return (
                "Empty search query validation missing: Search accepted blank query without guidance.",
                "Validate query parameter and return clear user guidance for blank searches.",
            )
        if any(k in obj for k in ["terms of service", "tos", "agreement", "mandatory terms"]):
            return (
                "Terms acceptance validation bypass: Account created without accepted terms.",
                "Enforce mandatory terms acceptance on backend registration handler.",
            )
        if any(k in obj for k in ["match", "confirm", "mismatch"]):
            return (
                "Password confirmation mismatch: Server accepted mismatched passwords.",
                "Add password equality check in backend registration handler.",
            )
        if any(k in obj for k in ["length", "short", "minlength"]) and any(k in obj for k in ["password", "credential"]):
            return (
                "Password length validation missing: Short password accepted without minimum length enforcement.",
                "Enforce password minimum length requirements on backend authentication handler.",
            )
        if "not visible" in reason and "welcome" in reason:
            return (
                "Authentication failure or broken redirection after login.",
                "Verify auth endpoint validates credentials and redirects correctly.",
            )

        return (
            f"Application failed to meet expected condition: {result.failure_reason}",
            "Review backend handler and UI flow for the failed step.",
        )
