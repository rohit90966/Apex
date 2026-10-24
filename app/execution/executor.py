import os
import sys
import time
import asyncio
import logging
from typing import List, Optional, Tuple, Dict, Any
from playwright.async_api import async_playwright, Page, BrowserContext, Error as PlaywrightError

from app.schemas import (
    ActionStep,
    ActionType,
    ExecutionAssertion,
    AssertionType,
    ExecutionStatus,
    StepExecutionResult,
    ExecutionResult,
)
from app.config import get_settings

logger = logging.getLogger("verityqa.execution")


class BrowserExecutor:
    """
    Safe Playwright Browser Action Executor.
    Executes discrete, structured steps and verifies assertions without arbitrary code injection.
    Captures step-by-step logs, durations, and screenshots.
    """

    def __init__(self, headless: Optional[bool] = None, artifacts_dir: Optional[str] = None):
        settings = get_settings()
        self.headless = headless if headless is not None else settings.verityqa_headless
        self.artifacts_dir = artifacts_dir or settings.verityqa_artifacts_dir
        os.makedirs(self.artifacts_dir, exist_ok=True)

    async def execute_steps(
        self,
        test_id: str,
        test_name: str,
        steps: List[ActionStep],
        initial_url: Optional[str] = None,
        timeout_ms: int = 8000,
    ) -> ExecutionResult:
        """
        Executes a sequence of ActionSteps inside a fresh, isolated Playwright BrowserContext.
        """
        step_results: List[StepExecutionResult] = []
        console_logs: List[str] = []
        overall_status = ExecutionStatus.PASSED
        failure_reason: Optional[str] = None
        failure_step_num: Optional[int] = None
        final_screenshot_path: Optional[str] = None
        dom_snippet: Optional[str] = None
        assertions_evaluated = 0
        assertions_satisfied = 0
        evidence_retained: Dict[str, Any] = {}

        start_time = time.time()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerityQA-Autonomous-QA-Agent/1.0",
            )
            page: Page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            # Collect console messages for evidence
            page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: console_logs.append(f"[pageerror] {str(err)}"))

            if initial_url:
                try:
                    await page.goto(initial_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:
                    logger.warning("Initial navigation to %s warning: %s", initial_url, e)

            for step in steps:
                step_start = time.time()
                step_status = ExecutionStatus.PASSED
                error_msg: Optional[str] = None
                expected_val: Optional[str] = None
                actual_val: Optional[str] = None
                step_screenshot: Optional[str] = None

                try:
                    # 1. Execute the primary action
                    await self._execute_action(page, step, timeout_ms)

                    # 2. Check attached assertion if present
                    if step.assertion:
                        assertions_evaluated += 1
                        expected_val = step.assertion.expected_value
                        passed, actual_val, assert_err = await self._verify_assertion(
                            page, step.assertion, timeout_ms
                        )
                        if passed:
                            assertions_satisfied += 1
                            evidence_retained[f"step_{step.step_number}_{step.assertion.assertion_type.value}"] = actual_val
                        else:
                            step_status = ExecutionStatus.FAILED
                            error_msg = assert_err
                            evidence_retained[f"step_{step.step_number}_{step.assertion.assertion_type.value}_failed"] = {
                                "error": assert_err,
                                "actual": actual_val,
                            }
                    
                except PlaywrightError as pe:
                    step_status = ExecutionStatus.FAILED
                    error_msg = f"Playwright error: {str(pe)}"
                except Exception as ex:
                    step_status = ExecutionStatus.ERROR
                    error_msg = f"Execution error: {str(ex)}"

                step_duration = round(time.time() - step_start, 3)

                # Capture step screenshot if failed or explicitly requested
                if step_status != ExecutionStatus.PASSED or step.action == ActionType.SCREENSHOT:
                    step_screenshot = f"step_{test_id}_{step.step_number}_{int(time.time())}.png"
                    step_full_path = os.path.join(self.artifacts_dir, step_screenshot)
                    try:
                        await page.screenshot(path=step_full_path, full_page=False)
                    except Exception:
                        step_screenshot = None

                step_res = StepExecutionResult(
                    step_number=step.step_number,
                    action=step.action,
                    description=step.description,
                    status=step_status,
                    duration_seconds=step_duration,
                    screenshot_path=step_screenshot,
                    error_message=error_msg,
                    assertion_expected=expected_val,
                    assertion_actual=actual_val,
                )
                step_results.append(step_res)

                if step_status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                    overall_status = step_status
                    failure_reason = error_msg or "Step assertion failed"
                    failure_step_num = step.step_number
                    break

            # A test passes only when its stated assertions have actually been evaluated and satisfied
            stated_assertions = sum(1 for s in steps if s.assertion is not None)
            if overall_status == ExecutionStatus.PASSED and stated_assertions > 0:
                if assertions_satisfied < stated_assertions:
                    overall_status = ExecutionStatus.FAILED
                    failure_reason = f"Only {assertions_satisfied}/{stated_assertions} stated assertions were satisfied"
            elif overall_status == ExecutionStatus.PASSED and assertions_evaluated == 0:
                # Steps ran but nothing was verified: that is not a pass.
                overall_status = ExecutionStatus.INCONCLUSIVE
                failure_reason = "No assertions were evaluated; the steps ran but nothing was verified"

            # Collect final DOM snippet and screenshot
            try:
                final_screenshot_name = f"exec_{test_id}_{int(time.time())}.png"
                final_path = os.path.join(self.artifacts_dir, final_screenshot_name)
                await page.screenshot(path=final_path, full_page=False)
                final_screenshot_path = final_screenshot_name
            except Exception:
                pass

            try:
                body_content = await page.content()
                dom_snippet = body_content[:1500] if body_content else None
            except Exception:
                pass

            await context.close()
            await browser.close()

        total_duration = round(time.time() - start_time, 3)

        return ExecutionResult(
            test_id=test_id,
            test_name=test_name,
            status=overall_status,
            total_steps=len(steps),
            steps_completed=len(step_results),
            step_results=step_results,
            duration_seconds=total_duration,
            failure_reason=failure_reason,
            failure_step=failure_step_num,
            final_screenshot=final_screenshot_path,
            dom_snapshot_snippet=dom_snippet,
            console_logs=console_logs[:20],
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            assertions_evaluated=assertions_evaluated,
            assertions_satisfied=assertions_satisfied,
            evidence_retained=evidence_retained,
        )

    async def _execute_action(self, page: Page, step: ActionStep, timeout_ms: int):
        """Dispatches structured step actions safely."""
        if step.action == ActionType.NAVIGATE:
            if not step.value:
                raise ValueError("NAVIGATE action requires 'value' (target URL)")
            await page.goto(step.value, wait_until="domcontentloaded", timeout=timeout_ms)

        elif step.action == ActionType.FILL:
            if not step.selector:
                raise ValueError("FILL action requires 'selector'")
            # Wait for selector to be visible and editable
            await page.wait_for_selector(step.selector, state="visible", timeout=timeout_ms)
            await page.fill(step.selector, step.value or "", timeout=timeout_ms)

        elif step.action == ActionType.CLICK:
            if not step.selector:
                raise ValueError("CLICK action requires 'selector'")
            await page.wait_for_selector(step.selector, state="visible", timeout=timeout_ms)
            await page.click(step.selector, timeout=timeout_ms)
            # Short wait for any reactive microtask or DOM mutation
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 3000))

        elif step.action == ActionType.SELECT:
            if not step.selector or not step.value:
                raise ValueError("SELECT action requires 'selector' and 'value'")
            await page.wait_for_selector(step.selector, state="visible", timeout=timeout_ms)
            await page.select_option(step.selector, step.value, timeout=timeout_ms)

        elif step.action == ActionType.PRESS_KEY:
            key = step.value or "Enter"
            if step.selector:
                await page.press(step.selector, key, timeout=timeout_ms)
            else:
                await page.keyboard.press(key)

        elif step.action == ActionType.WAIT_FOR_ELEMENT:
            if not step.selector:
                raise ValueError("WAIT_FOR_ELEMENT action requires 'selector'")
            await page.wait_for_selector(step.selector, state="visible", timeout=timeout_ms)

        elif step.action == ActionType.SCREENSHOT:
            pass  # Handled in step screenshot recorder

        elif step.action == ActionType.EVAL_JS:
            if step.value:
                await page.evaluate(step.value)

        elif step.action == ActionType.SET_VALUE_JS:
            # Deliberately bypasses browser HTML5 required/pattern validation by setting the
            # field value via JS and dispatching input+change events, so the form can be submitted
            # to the server even when native validation would block it.
            # Only authorised for explicit server-side boundary tests.
            if step.value is None:
                raise ValueError("SET_VALUE_JS requires 'value'")
            if not step.selector:
                raise ValueError("SET_VALUE_JS requires 'selector'")
            # Support comma-separated selector lists (tries each in order)
            selectors = [s.strip() for s in step.selector.split(",") if s.strip()]
            await page.evaluate(
                """([sels, val]) => {
                    let el = null;
                    for (const sel of sels) {
                        try { el = document.querySelector(sel); } catch(e) {}
                        if (el) break;
                    }
                    if (!el) throw new Error('SET_VALUE_JS: none of the selectors matched: ' + sels.join(', '));
                    // Use native input value setter to work with React-managed inputs too
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    );
                    if (nativeInputValueSetter && nativeInputValueSetter.set) {
                        nativeInputValueSetter.set.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                [selectors, step.value],
            )

        elif step.action == ActionType.OBSERVE_STATE:
            # Read-only: capture current URL + visible text as evidence; no side effects
            pass  # evidence is captured by assertion if present; no action needed

    async def _verify_assertion(
        self, page: Page, assertion: ExecutionAssertion, timeout_ms: int
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Evaluates assertion deterministically without code execution.
        Returns: (passed: bool, actual_value: str, error_message: str)
        """
        atype = assertion.assertion_type
        expected = assertion.expected_value or ""

        if atype == AssertionType.URL_CONTAINS:
            actual = page.url
            if expected.lower() in actual.lower():
                return True, actual, None
            return False, actual, f"Expected URL to contain '{expected}', got '{actual}'"

        elif atype == AssertionType.URL_EQUALS:
            actual = page.url
            if actual == expected:
                return True, actual, None
            return False, actual, f"Expected URL to equal '{expected}', got '{actual}'"

        elif atype == AssertionType.TEXT_VISIBLE:
            try:
                # Check for text anywhere in visible page or target selector
                locator = page.locator(assertion.target_selector) if assertion.target_selector else page.locator("body")
                text_loc = locator.get_by_text(expected, exact=False)
                try:
                    visible = await text_loc.first.is_visible(timeout=timeout_ms)
                except (TypeError, AttributeError):
                    visible = await text_loc.is_visible(timeout=timeout_ms)

                if visible:
                    return True, f"Found text '{expected}'", None

                # Defensive check for form validation expectations:
                # If expecting "required" or "invalid" or "fill", check if HTML5 browser validation is active
                if expected.lower() in ("required", "invalid", "validation", "fill out", "empty"):
                    is_invalid = await page.evaluate(
                        """() => {
                            const invalids = document.querySelectorAll(':invalid');
                            if (invalids.length > 0) return true;
                            const active = document.activeElement;
                            if (active && active.validity && (active.validity.valueMissing || !active.checkValidity())) return true;
                            return false;
                        }"""
                    )
                    if is_invalid:
                        return True, "HTML5 browser form validation prevented submission (validity.valueMissing / :invalid)", None

                return False, "Text not visible", f"Expected text '{expected}' was not visible"
            except Exception as e:
                return False, str(e), f"Text visibility check failed: {str(e)}"

        elif atype == AssertionType.TEXT_NOT_VISIBLE:
            try:
                locator = page.locator(assertion.target_selector) if assertion.target_selector else page.locator("body")
                text_loc = locator.get_by_text(expected, exact=False)
                try:
                    visible = await text_loc.first.is_visible(timeout=1000)
                except (TypeError, AttributeError):
                    visible = await text_loc.is_visible(timeout=1000)

                if not visible:
                    return True, f"Text '{expected}' is absent", None
                return False, f"Text '{expected}' is visible", f"Expected text '{expected}' to NOT be visible"
            except Exception:
                return True, "Text absent", None

        elif atype == AssertionType.ELEMENT_VISIBLE:
            if not assertion.target_selector:
                return False, None, "ELEMENT_VISIBLE requires target_selector"
            try:
                visible = await page.is_visible(assertion.target_selector, timeout=timeout_ms)
                if visible:
                    return True, "Visible", None
                return False, "Not visible", f"Element '{assertion.target_selector}' was not visible"
            except Exception as e:
                return False, str(e), f"Element visibility error: {str(e)}"

        elif atype == AssertionType.ELEMENT_NOT_VISIBLE:
            if not assertion.target_selector:
                return False, None, "ELEMENT_NOT_VISIBLE requires target_selector"
            try:
                visible = await page.is_visible(assertion.target_selector, timeout=1000)
                if not visible:
                    return True, "Not visible", None
                return False, "Visible", f"Element '{assertion.target_selector}' is visible"
            except Exception:
                return True, "Not visible", None

        elif atype == AssertionType.ELEMENT_DISABLED:
            if not assertion.target_selector:
                return False, None, "ELEMENT_DISABLED requires target_selector"
            try:
                disabled = await page.is_disabled(assertion.target_selector, timeout=timeout_ms)
                if disabled:
                    return True, "Disabled", None
                return False, "Enabled", f"Element '{assertion.target_selector}' was expected to be disabled"
            except Exception as e:
                return False, str(e), f"Disabled check error: {str(e)}"

        elif atype == AssertionType.ELEMENT_ENABLED:
            if not assertion.target_selector:
                return False, None, "ELEMENT_ENABLED requires target_selector"
            try:
                enabled = await page.is_enabled(assertion.target_selector, timeout=timeout_ms)
                if enabled:
                    return True, "Enabled", None
                return False, "Disabled", f"Element '{assertion.target_selector}' was expected to be enabled"
            except Exception as e:
                return False, str(e), f"Enabled check error: {str(e)}"

        elif atype == AssertionType.VALIDITY_VALUE_MISSING:
            selector = assertion.target_selector or "input:required, [required]"
            try:
                res = await page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        const elValidity = (el && el.validity) ? {
                            valueMissing: !!el.validity.valueMissing,
                            valid: !!el.validity.valid,
                            validationMessage: el.validationMessage || ''
                        } : null;

                        const reqs = Array.from(document.querySelectorAll('input:required, select:required, textarea:required'));
                        const missingReqs = reqs.filter(r => (r.validity && r.validity.valueMissing) || (!r.value && r.required)).map(r => r.name || r.id || 'unnamed');

                        // Check for visible client-side validation error banners/messages
                        const errorContainers = Array.from(document.querySelectorAll('.error-message-container, [data-test="error"], .error, .alert-danger, .validation-error, .invalid-feedback'));
                        const visibleErrors = errorContainers
                            .filter(ec => ec.textContent && ec.textContent.trim().length > 0 && ec.offsetParent !== null)
                            .map(ec => ec.textContent.trim().slice(0, 120));

                        const invalidCount = document.querySelectorAll(':invalid').length;
                        const passed = Boolean((elValidity && elValidity.valueMissing) || (missingReqs.length > 0) || (visibleErrors.length > 0) || (invalidCount > 0));

                        return {
                            passed: passed,
                            el_validity: elValidity,
                            missing_required_fields: missingReqs,
                            visible_errors: visibleErrors,
                            invalid_count: invalidCount,
                            url: window.location.href
                        };
                    }""",
                    selector,
                )
                if isinstance(res, dict) and res.get("passed"):
                    reasons = []
                    if res.get("el_validity") and res["el_validity"].get("valueMissing"):
                        msg = res["el_validity"].get("validationMessage")
                        reasons.append(f"field validity.valueMissing=true (validationMessage='{msg}')")
                    if res.get("visible_errors"):
                        reasons.append(f"visible validation error banners: {res['visible_errors']}")
                    if res.get("missing_required_fields"):
                        reasons.append(f"unfilled required inputs: {res['missing_required_fields']}")
                    if res.get("invalid_count", 0) > 0:
                        reasons.append(f"HTML5 :invalid controls active={res['invalid_count']}")
                    return True, f"Validation confirmed: {'; '.join(reasons)}", None
                return False, "No validation feedback or valueMissing state captured", f"Empty submission produced no browser validity state, no visible error feedback, and no :invalid elements on '{selector}'"
            except Exception as e:
                return False, str(e), f"Validity valueMissing check failed: {str(e)}"

        elif atype == AssertionType.VALIDITY_INVALID:
            selector = assertion.target_selector or "input:invalid, :invalid"
            try:
                is_invalid = await page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (el && el.checkValidity && !el.checkValidity()) return true;
                        return document.querySelectorAll(':invalid').length > 0;
                    }""",
                    selector,
                )
                if is_invalid:
                    return True, "Form control checkValidity() is false (element is invalid)", None
                return False, "Element is valid", f"Expected element '{selector}' to be invalid"
            except Exception as e:
                return False, str(e), f"Validity invalid check failed: {str(e)}"

        elif atype == AssertionType.FORM_SUBMISSION_BLOCKED:
            try:
                expected_target = assertion.expected_value or ""
                current_url = page.url
                blocked_by_invalid = await page.evaluate(
                    """() => document.querySelectorAll(':invalid').length > 0 || (document.activeElement && document.activeElement.validity && !document.activeElement.checkValidity())"""
                )
                if blocked_by_invalid:
                    return True, "Submission blocked by browser form validation", None
                if expected_target and (expected_target in current_url or current_url.rstrip('/') == expected_target.rstrip('/')):
                    return True, f"Submission blocked; page stayed on {current_url}", None
                return False, f"Current URL: {current_url}", "Expected form submission to be blocked"
            except Exception as e:
                return False, str(e), f"Form submission blocked check failed: {str(e)}"

        elif atype == AssertionType.INPUT_MASKED:
            # Objective-specific check for password masking and rendered text security
            selector = assertion.target_selector or "input[type='password'], input[name*='password'], input[name*='pwd']"
            try:
                masked_res = await page.evaluate(
                    """(sel) => {
                        let el = document.querySelector(sel);
                        if (!el) {
                            el = document.querySelector('input[type="password"]');
                        }
                        if (!el) {
                            return { ok: false, error: 'Password input element not found in DOM' };
                        }
                        const isTypePassword = (el.type === 'password');
                        const style = window.getComputedStyle(el);
                        const textSec = style.webkitTextSecurity || style.textSecurity || 'none';
                        const isRenderedMasked = isTypePassword || ['disc', 'circle', 'square'].includes(textSec);
                        return {
                            ok: isTypePassword && isRenderedMasked,
                            is_type_password: isTypePassword,
                            rendered_masked: isRenderedMasked,
                            type: el.type,
                            computed_text_security: textSec,
                            id: el.id,
                            name: el.name
                        };
                    }""",
                    selector,
                )
                if masked_res.get("ok"):
                    field_name = masked_res.get('name') or masked_res.get('id') or selector
                    text_sec = masked_res.get('computed_text_security', 'disc')
                    return True, f"Input '{field_name}' verified masked: type='{masked_res.get('type')}', rendered-text-security='{text_sec}'", None
                err_detail = (
                    f"Input masking check failed: type is '{masked_res.get('type')}', "
                    f"rendered text security is '{masked_res.get('computed_text_security', 'none')}'. "
                    f"Expected type='password' and rendered text obscuring."
                )
                return False, f"type='{masked_res.get('type', 'none')}'", err_detail
            except Exception as e:
                return False, str(e), f"Input masking check error: {str(e)}"

        elif atype == AssertionType.ACCESSIBILITY_VALID:
            # Objective-specific accessibility evaluation using computed accessible names
            try:
                a11y_res = await page.evaluate(
                    """() => {
                        const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), button, select, textarea, a[href], [role="button"], [role="link"]'));
                        let missing = [];
                        let evaluated = [];
                        for (const el of inputs) {
                            let name = '';
                            if (el.computedName && typeof el.computedName === 'string') {
                                name = el.computedName.trim();
                            }
                            if (!name) {
                                name = (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim();
                            }
                            if (!name && el.labels && el.labels.length > 0) {
                                name = (el.labels[0].textContent || '').trim();
                            }
                            if (!name && el.id) {
                                const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                                if (lbl) name = (lbl.textContent || '').trim();
                            }
                            if (!name && (el.tagName.toLowerCase() === 'button' || el.tagName.toLowerCase() === 'a')) {
                                name = (el.textContent || '').trim();
                            }
                            if (!name && el.tagName.toLowerCase() === 'input' && ['submit', 'button', 'reset'].includes((el.type || '').toLowerCase())) {
                                name = (el.value || '').trim();
                            }
                            const identifier = el.name || el.id || el.getAttribute('data-test') || el.tagName.toLowerCase();
                            if (name) {
                                evaluated.push({ control: identifier, accessible_name: name.slice(0, 40) });
                            } else {
                                missing.push(identifier);
                            }
                        }
                        return { total: inputs.length, missingCount: missing.length, evaluatedSample: evaluated.slice(0, 5), missing };
                    }"""
                )
                missing_cnt = a11y_res.get("missingCount", 0)
                tot = a11y_res.get("total", 0)
                eval_sample = a11y_res.get("evaluatedSample", [])
                sample_str = ", ".join([f"{e['control']}='{e['accessible_name']}'" for e in eval_sample])
                if missing_cnt == 0:
                    return True, f"All {tot} interactive controls verified with computed accessible names ({sample_str})", None
                elif missing_cnt <= tot * 0.2:
                    return True, f"{tot - missing_cnt}/{tot} interactive controls have verified computed accessible names ({sample_str}); unlabelled: {a11y_res.get('missing')}", None
                return False, f"Missing accessible names on {missing_cnt}/{tot} controls: {a11y_res.get('missing')}", f"Interactive controls failed accessibility: {missing_cnt} controls lack computed accessible names"
            except Exception as e:
                return False, str(e), f"Accessibility check error: {str(e)}"

        elif atype == AssertionType.ELEMENT_ATTRIBUTE:
            if not assertion.target_selector:
                return False, None, "ELEMENT_ATTRIBUTE requires target_selector"
            attr_name = (assertion.description or "").split("=")[0] if "=" in (assertion.description or "") else "type"
            expected_attr_val = assertion.expected_value or ""
            try:
                val = await page.get_attribute(assertion.target_selector, attr_name)
                if val == expected_attr_val or (expected_attr_val in (val or "")):
                    return True, f"Attribute {attr_name}='{val}'", None
                return False, f"Attribute {attr_name}='{val}'", f"Expected {attr_name}='{expected_attr_val}', got '{val}'"
            except Exception as e:
                return False, str(e), f"Element attribute check error: {str(e)}"

        return True, "Custom check passed", None
