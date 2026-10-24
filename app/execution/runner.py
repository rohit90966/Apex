import re
import logging
from typing import List, Optional
from app.schemas import (
    TestCase,
    ActionStep,
    ActionType,
    ExecutionAssertion,
    AssertionType,
    ExecutionResult,
    ExecutionStatus,
    InspectionResult,
    PageCategory,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.execution.executor import BrowserExecutor
from app.execution.locator import LocatorResolver, LocatorConfidence
from app.intelligence.target_context import canonical_origin

logger = logging.getLogger("verityqa.runner")

# Credential placeholders written by planners; resolved only from credentials scoped to the target origin.
CREDENTIAL_PASSWORD_PLACEHOLDER = "${CREDENTIAL_PASSWORD}"
CREDENTIAL_USERNAME_PLACEHOLDER = "${CREDENTIAL_USERNAME}"


class QAPlanRunner:
    """
    Translates high-level declarative TestCases into concrete sequences of ActionSteps,
    and executes them via BrowserExecutor.

    When an InspectionResult is provided (via set_inspection), selectors are
    grounded against observed DOM evidence using LocatorResolver before being
    included in the generated step sequence.
    """

    def __init__(self, executor: Optional[BrowserExecutor] = None):
        self.executor = executor or BrowserExecutor()
        self._inspection: Optional[InspectionResult] = None

    def set_inspection(self, inspection: InspectionResult) -> None:
        """Attach an InspectionResult so that synthesize_steps can ground selectors."""
        self._inspection = inspection

    def _resolve_selector(
        self,
        selector: str,
        purpose: str = "",
        field_type: str = "",
    ) -> str:
        """
        Attempt to ground a composite selector against observed DOM evidence.
        Returns the most specific confirmed selector, or the original if unverifiable.
        """
        resolver = LocatorResolver(self._inspection)
        resolution = resolver.resolve(selector, purpose=purpose, field_type=field_type)
        if resolution.confidence in (LocatorConfidence.HIGH, LocatorConfidence.MEDIUM):
            logger.debug(
                "Locator grounded: '%s' → '%s' (confidence=%s)",
                selector,
                resolution.resolved_selector,
                resolution.confidence,
            )
            return resolution.resolved_selector
        if resolution.confidence == LocatorConfidence.NONE and resolution.alternatives:
            logger.info(
                "Locator mismatch: '%s' not in DOM; using alternative '%s'",
                selector,
                resolution.alternatives[0],
            )
            return resolution.alternatives[0]
        # LOW confidence or UNVERIFIED — return original (broad composite) unchanged
        return selector

    def synthesize_steps(self, test_case: TestCase, target_url: str) -> List[ActionStep]:
        """
        Synthesizes deterministic browser action steps from test case metadata.
        Uses structured DOM evidence when available.
        """
        # If test case already carries executable steps (Phase 2 Adaptive Generation), use them directly
        if test_case.steps:
            resolved_steps: List[ActionStep] = []
            for s in test_case.steps:
                if isinstance(s, ActionStep):
                    resolved_steps.append(s)
                elif isinstance(s, dict):
                    resolved_steps.append(ActionStep(**s))
            if resolved_steps:
                return resolved_steps

        steps: List[ActionStep] = []
        step_idx = 1

        # 1. Step 1: Navigate to the target URL
        steps.append(
            ActionStep(
                step_number=step_idx,
                action=ActionType.NAVIGATE,
                value=target_url,
                description=f"Navigate to {target_url}",
            )
        )
        step_idx += 1

        # Check for derived boundary values (BVA cases)
        derived = test_case.derived_values or {}
        bva_val = derived.get("boundary_value")
        bva_type = derived.get("boundary_type", "")

        # Look at relevant elements to infer form fields
        elements = test_case.relevant_elements or []

        # Comprehensive context check for journey matching
        obj_lower = test_case.objective.lower()
        name_lower = test_case.name.lower()
        action_lower = test_case.action_description.lower()
        url_lower = target_url.lower()
        all_text = f"{obj_lower} {name_lower} {action_lower} {url_lower}"

        is_search = (
            test_case.page_category == PageCategory.SEARCH
            or "/search" in url_lower
            or "search" in obj_lower
            or "search" in name_lower
        )
        is_registration = (
            test_case.page_category == PageCategory.REGISTRATION
            or "/register" in url_lower
            or "/registration" in url_lower
            or any(k in all_text for k in ["register", "registration", "signup", "sign-up", "mismatch", "terms of service", "terms acceptance"])
        ) and not is_search
        is_login = (
            test_case.page_category == PageCategory.LOGIN
            or "/login" in url_lower
            or any(k in all_text for k in ["login", "signin", "sign-in", "credential", "auth"])
        ) and not is_search and not is_registration

        # ── 2. Check for Specific Objective Types First ───────────────────────
        is_masking_test = any(k in all_text for k in ["mask", "obscur", "shoulder", "password type", "type=password", "type='password'"])
        is_a11y_test = any(k in all_text for k in ["accessib", "a11y", "aria", "screen reader", "keyboard navigation", "tabindex"])

        if is_masking_test:
            # Objective: Password Masking — inspect password field DOM type directly
            pwd_sel = self._resolve_selector(
                "input#password, input[name='password'], [data-test='password'], input[type='password']",
                purpose="password",
                field_type="password",
            )
            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.WAIT_FOR_ELEMENT,
                    selector=pwd_sel,
                    description=f"Locate password input field for masking verification",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.INPUT_MASKED,
                        target_selector=pwd_sel,
                        description="Verify password field uses type='password' to mask sensitive entry",
                    ),
                )
            )
            return steps

        if is_a11y_test:
            # Objective: Accessibility — check accessible names, roles, focusability
            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.WAIT_FOR_ELEMENT,
                    selector="body",
                    description="Verify interactive controls have accessible names and labels",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.ACCESSIBILITY_VALID,
                        description="Ensure form controls and buttons provide accessible labels/ARIA attributes",
                    ),
                )
            )
            return steps

        is_page_load = any(k in all_text for k in ["page load", "load integrity", "console error", "http status", "render completely"])

        if is_page_load:
            # Objective: Page Load & Integrity — verify body renders and HTTP 200 without attempting auth
            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.WAIT_FOR_ELEMENT,
                    selector="body",
                    description="Verify page renders completely and body is visible",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        target_selector="body",
                        description="Ensure body content is rendered without fatal errors",
                    ),
                )
            )
            return steps

        is_visibility_test = any(
            k in all_text for k in [
                "presence and visibility",
                "element visibility",
                "form element visibility",
                "controls presence",
                "controls are present",
                "visibly rendered",
                "fields are rendered",
            ]
        ) and not any(k in all_text for k in ["attempt submission", "click login and expect", "successful authentication", "login succeeds"])

        if is_visibility_test:
            # Objective: Verify presence and visibility of essential controls — do NOT require login/navigation
            u_sel = self._resolve_selector(
                "input#username, input#user-name, input[name='username'], input[name='user-name'], [data-test='username'], input[type='text'], input[type='email']",
                purpose="username",
                field_type="text",
            )
            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.WAIT_FOR_ELEMENT,
                    selector=u_sel,
                    description=f"Verify form control presence and visibility: {u_sel}",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        target_selector=u_sel,
                        description=f"Ensure form element '{u_sel}' is visible in DOM",
                    ),
                )
            )
            return steps

        if is_login:
            # Login journey synthesis — selectors grounded against observed DOM. Valid credentials are
            # placeholders resolved at run time from credentials scoped to the target origin.
            username_val = CREDENTIAL_USERNAME_PLACEHOLDER
            password_val = CREDENTIAL_PASSWORD_PLACEHOLDER

            is_length_test = any(
                k in all_text for k in ["length", "short", "minlength", "minimum", "boundary", "1-character", "1 char", "policy"]
            )
            is_empty_username = any(k in all_text for k in ["empty username", "missing username", "blank username", "username required"])
            is_missing_password = any(k in all_text for k in ["missing password", "empty password", "blank password", "password required", "without password"])
            is_whitespace_test = any(k in all_text for k in ["whitespace", "spaces only", "trailing space", "leading space"])
            is_empty_test = (
                is_empty_username
                or is_missing_password
                or any(k in all_text for k in ["empty", "required", "blank", "missing", "unentered"])
            ) and not is_length_test and not is_whitespace_test
            is_invalid_test = any(
                k in all_text for k in ["invalid credentials", "unregistered", "wrong", "bad password", "locked_out"]
            )

            # Derive minlength from DOM evidence (if available)
            observed_minlength = None
            if self._inspection:
                for fld in (self._inspection.input_fields_summary or []):
                    if (fld.input_type or "").lower() == "password" and fld.minlength:
                        observed_minlength = fld.minlength
                        break

            # Ground selectors against observed DOM
            username_selector = self._resolve_selector(
                "input#username, input#user-name, input[name='username'], input[name='user-name'], [data-test='username'], input[type='email']",
                purpose="username",
                field_type="text",
            )
            password_selector = self._resolve_selector(
                "input#password, input[name='password'], [data-test='password'], input[type='password']",
                purpose="password",
                field_type="password",
            )
            submit_selector = "button[type='submit'], #loginBtn, #login-button, [data-test='login-button'], input[type='submit']"

            if is_length_test:
                username_val = "qa.tester@example.com"
                password_val = "a"  # 1-character lower boundary for a minimum-length policy
                if (
                    observed_minlength
                    or test_case.expected_behavior_source == ExpectedBehaviorSource.REQUIREMENT
                    or any(k in (test_case.expected_outcome or "").lower() for k in ["clear length", "error message", "rejects short password"])
                ):
                    expected_assertion = ExecutionAssertion(
                        assertion_type=AssertionType.TEXT_VISIBLE,
                        expected_value="at least",
                        description=f"Expect validation message enforcing minimum password length policy",
                    )
                else:
                    expected_assertion = ExecutionAssertion(
                        assertion_type=AssertionType.VALIDITY_INVALID,
                        target_selector=password_selector,
                        description="Expect browser form validity to flag too-short password if minlength is enforced",
                    )
            elif is_missing_password:
                username_val = "qa.tester@example.com"
                password_val = ""
                expected_assertion = ExecutionAssertion(
                    assertion_type=AssertionType.VALIDITY_VALUE_MISSING,
                    target_selector=password_selector,
                    expected_value="valueMissing",
                    description="Expect password field validation to reject submission without password",
                )
            elif is_empty_test:
                username_val = ""
                password_val = ""
                expected_assertion = ExecutionAssertion(
                    assertion_type=AssertionType.VALIDITY_VALUE_MISSING,
                    target_selector=username_selector,
                    expected_value="valueMissing",
                    description="Expect browser validity valueMissing or form validation to block submission on missing username",
                )
            elif is_whitespace_test:
                username_val = "   "
                password_val = "   "
                expected_assertion = ExecutionAssertion(
                    assertion_type=AssertionType.FORM_SUBMISSION_BLOCKED,
                    expected_value=target_url,
                    description="Expect whitespace credentials to not grant access; page should remain on login view or show error",
                )
            elif is_invalid_test:
                username_val = "nonexistent_user@example.com"
                password_val = "wrong_password"
                expected_assertion = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Invalid",
                    description="Expect invalid credentials error message",
                )
            else:
                # Genuine positive login journey: the login form must be dismissed after authentication.
                expected_assertion = ExecutionAssertion(
                    assertion_type=AssertionType.ELEMENT_NOT_VISIBLE,
                    target_selector=password_selector,
                    description="Expect the login form to be dismissed after successful authentication",
                )

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector=username_selector,
                    value=username_val,
                    description=f"Enter username '{username_val}'",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector=password_selector,
                    value=password_val,
                    description="Enter password",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.CLICK,
                    selector=submit_selector,
                    description="Click submit login button",
                    assertion=expected_assertion,
                )
            )

        elif is_registration:
            # Registration journey synthesis
            name_val = "Alex Carter"
            email_val = "alex@example.com"
            pwd_val = "SecurePass123!"
            confirm_val = pwd_val

            is_mismatch = any(
                k in all_text for k in ["mismatch", "confirmation", "confirm password", "differing", "different"]
            )
            is_terms = any(
                k in all_text for k in ["terms", "tos", "agreement", "unaccepted", "mandatory terms"]
            )

            if is_mismatch:
                confirm_val = "DifferentPass999!"
            elif bva_val:
                pwd_val = str(bva_val)
                confirm_val = str(bva_val)

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector="input#fullname, input[name='fullname'], input[name='name']",
                    value=name_val,
                    description=f"Fill full name '{name_val}'",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector="input#email, input[name='email']",
                    value=email_val,
                    description=f"Fill email '{email_val}'",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector="input#reg-password, input[name='password']",
                    value=pwd_val,
                    description=f"Fill password '{pwd_val}'",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector="input#confirm-password, input[name='confirm_password']",
                    value=confirm_val,
                    description=f"Fill confirm password '{confirm_val}'",
                )
            )
            step_idx += 1

            if is_terms:
                # Testing Terms of Service bypass: Do NOT check terms, and test backend defense against client-side bypass
                steps.append(
                    ActionStep(
                        step_number=step_idx,
                        action=ActionType.EVAL_JS,
                        value="document.getElementById('terms')?.removeAttribute('required')",
                        description="Bypass client-side required constraint to test backend terms enforcement",
                    )
                )
                step_idx += 1

                assert_rule = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_NOT_VISIBLE,
                    expected_value="Account Created",
                    description="Expect backend registration to reject submission without accepted terms and NOT create account",
                )
            elif is_mismatch:
                # Check terms so submission can proceed to test mismatch
                steps.append(
                    ActionStep(
                        step_number=step_idx,
                        action=ActionType.CLICK,
                        selector="input#terms, input[name='terms']",
                        description="Check terms of service checkbox",
                    )
                )
                step_idx += 1

                assert_rule = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Passwords do not match",
                    description="Expect password mismatch validation error",
                )
            else:
                # Standard registration
                steps.append(
                    ActionStep(
                        step_number=step_idx,
                        action=ActionType.CLICK,
                        selector="input#terms, input[name='terms']",
                        description="Check terms of service checkbox",
                    )
                )
                step_idx += 1

                assert_rule = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Account Created",
                    description="Expect registration success indication",
                )

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.CLICK,
                    selector="button[type='submit'], #registerBtn",
                    description="Click register button",
                    assertion=assert_rule,
                )
            )

        elif is_search:
            # Search journey synthesis
            is_empty_or_blank = any(
                k in all_text for k in ["empty", "blank", "whitespace", "unvalidated", "boundary"]
            )
            if is_empty_or_blank:
                search_query = "   "  # Whitespace tests empty query handling and bypasses client required
                search_assert = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="Please enter a search term",
                    description="Expect clear validation message prompting for search term on blank query",
                )
            elif bva_val:
                search_query = str(bva_val)
                search_assert = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="result",
                    description="Verify search returns results or results message",
                )
            else:
                search_query = "Microservices"
                search_assert = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="result",
                    description="Verify search returns results or results message",
                )

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.FILL,
                    selector="input[type='search'], input#q, input[name='q']",
                    value=search_query,
                    description=f"Enter search query '{search_query}'",
                )
            )
            step_idx += 1

            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.CLICK,
                    selector="button[type='submit'], #searchBtn",
                    description="Click search submit button",
                    assertion=search_assert,
                )
            )

        else:
            # Fallback general interaction: verify page title or primary heading
            steps.append(
                ActionStep(
                    step_number=step_idx,
                    action=ActionType.WAIT_FOR_ELEMENT,
                    selector="h1, h2, form, main",
                    description="Verify page DOM elements render",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        target_selector="body",
                        description="Ensure body content is visible",
                    ),
                )
            )

        return steps

    @staticmethod
    def origin_violation(test_case: TestCase, steps: List[ActionStep], target_url: str) -> Optional[str]:
        """Why a case may not run against target_url (it belongs to, or navigates to, another origin)."""
        origin = canonical_origin(target_url)
        if test_case.target_origin and test_case.target_origin != origin:
            return (f"Test case was derived from origin {test_case.target_origin} "
                    f"and cannot be executed against {origin or target_url}.")
        for s in steps:
            if s.action == ActionType.NAVIGATE and s.value and s.value.startswith(("http://", "https://")) \
                    and canonical_origin(s.value) != origin:
                return f"Step {s.step_number} navigates to another origin ({canonical_origin(s.value)})."
        return None

    async def run_test(self, test_case: TestCase, target_url: str) -> ExecutionResult:
        """Synthesizes steps and executes a single test case."""
        if test_case.status == TestCaseStatus.INVALID:
            # Never substitute generic steps for an invalid case: that would let it "pass" without testing anything.
            return ExecutionResult(
                test_id=test_case.test_id,
                test_name=test_case.name,
                status=ExecutionStatus.TEST_DEFINITION_ERROR,
                total_steps=len(test_case.steps or []),
                steps_completed=0,
                failure_reason=f"Invalid test case: {test_case.invalid_reason or 'not executable'}",
            )
        steps = self.synthesize_steps(test_case, target_url)
        violation = self.origin_violation(test_case, steps, target_url)
        if violation:
            return ExecutionResult(
                test_id=test_case.test_id,
                test_name=test_case.name,
                status=ExecutionStatus.TEST_DEFINITION_ERROR,
                total_steps=len(steps),
                steps_completed=0,
                failure_reason=f"Cross-target execution refused: {violation}",
            )
        steps, missing_credential = self._resolve_credential_placeholders(steps, target_url)
        if missing_credential:
            return ExecutionResult(
                test_id=test_case.test_id,
                test_name=test_case.name,
                status=ExecutionStatus.BLOCKED,
                total_steps=len(steps),
                steps_completed=0,
                failure_reason=(f"Test requires authorized credentials for {canonical_origin(target_url)}, "
                                "but none are configured for that origin."),
            )
        return await self.executor.execute_steps(
            test_id=test_case.test_id,
            test_name=test_case.name,
            steps=steps,
            initial_url=target_url,
        )

    @staticmethod
    def _resolve_credential_placeholders(steps: List[ActionStep], target_url: str):
        """
        Replaces credential placeholders with the credentials configured for the target's origin,
        in memory only. Returns (steps, missing_credential).
        """
        placeholders = {CREDENTIAL_PASSWORD_PLACEHOLDER: "password", CREDENTIAL_USERNAME_PLACEHOLDER: "username"}
        if not any(s.value in placeholders for s in steps):
            return steps, False
        from app.investigation_agent.authenticator import AuthenticationManager
        creds = AuthenticationManager.discover_credentials(target_url, None) or {}
        needed = {placeholders[s.value] for s in steps if s.value in placeholders}
        if any(not creds.get(key) for key in needed):
            return steps, True
        resolved = [
            s.model_copy(update={"value": creds[placeholders[s.value]]}) if s.value in placeholders else s
            for s in steps
        ]
        return resolved, False

    async def run_all(
        self, test_cases: List[TestCase], target_url: str, max_concurrent: int = 1
    ) -> List[ExecutionResult]:
        """Executes a list of test cases sequentially or with bounded concurrency."""
        results: List[ExecutionResult] = []
        for tc in test_cases:
            try:
                logger.info("Executing test case %s: %s", tc.test_id, tc.name)
                res = await self.run_test(tc, target_url)
                results.append(res)
            except Exception as e:
                logger.error("Failed executing test case %s: %s", tc.test_id, e)
                results.append(
                    ExecutionResult(
                        test_id=tc.test_id,
                        test_name=tc.name,
                        status=ExecutionStatus.ERROR,
                        total_steps=0,
                        steps_completed=0,
                        failure_reason=str(e),
                    )
                )
        return results
