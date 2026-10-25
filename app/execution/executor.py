import os
import re
import sys
import json
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
    is_meaningful_assertion,
)
from app.config import get_settings

logger = logging.getLogger("verityqa.execution")

# Prefix of an assertion error meaning "the oracle could not observe what it needs" (INCOMPLETE, not FAIL).
NOT_EVALUABLE = "NOT_EVALUABLE:"

# User-facing text that indicates a broken computation or an unhandled error.
# Same value-position rule as the investigation perception (app/investigation_agent/perception.py): a
# placeholder is an anomaly where a value is rendered, not in prose. Plain strings so the exporter can embed them.
TEXT_ANOMALY_PATTERNS = (r"(?m)(?:^|[:=$€£₹])[ \t]*NaN\b", r"(?m)(?:^|[:=$€£₹])[ \t]*undefined\b",
                         r"\[object Object\]", r"Traceback \(most recent call last\)")

# Finds the page's primary record collection (largest group of same-signature visible siblings outside
# navigation chrome) and extracts per-record sort keys and texts. Generic: no site-specific selectors.
COLLECTION_SCRIPT = """
(args) => {
  const key = args.key;
  // Cells are fields of a record, never records themselves.
  const skipTags = new Set(['A', 'BUTTON', 'OPTION', 'LABEL', 'INPUT', 'SCRIPT', 'STYLE', 'BR', 'SELECT', 'TH', 'TD']);
  const chrome = 'nav, header, footer, select, datalist, [role="navigation"], [role="menu"], [aria-hidden="true"]';
  const sig = el => el.tagName + '.' + (typeof el.className === 'string'
      ? el.className.trim().split(/\\s+/).filter(c => c && !/^(active|selected|current|odd|even)$/i.test(c)).sort().join('.') : '');
  const shown = el => el.tagName === 'TR' ? el.getClientRects().length > 0 : el.offsetParent !== null;
  const numberOf = t => {
    const m = t.match(/[$\\u20ac\\u00a3\\u20b9]\\s*-?\\d[\\d,]*(?:\\.\\d+)?/) || t.match(/-?\\d[\\d,]*(?:\\.\\d+)?/);
    return m ? parseFloat(m[0].replace(/[^\\d.\\-]/g, '')) : null;
  };
  const primaryText = el => {
    const p = el.querySelector('[class*="name"], [class*="title"], h1, h2, h3, h4, h5, a, strong, b');
    const t = ((p && p.innerText) || el.innerText || '').trim();
    return t.split('\\n')[0].trim();
  };
  // A record's own fields: table cells for rows, otherwise its leaf elements' texts.
  const fieldsOf = el => el.tagName === 'TR'
      ? Array.from(el.cells).map(c => (c.innerText || '').trim())
      : Array.from(el.querySelectorAll('*')).filter(n => n.children.length === 0)
          .map(n => (n.innerText || n.textContent || '').trim()).filter(t => t);
  const headersOf = el => {
    const table = el.tagName === 'TR' ? el.closest('table') : null;
    if (!table) return [];
    const head = table.querySelectorAll('thead th');
    const cells = head.length ? head : (table.querySelector('tr') ? table.querySelector('tr').querySelectorAll('th') : []);
    return Array.from(cells).map(h => (h.innerText || '').trim());
  };
  let best = null;
  for (const parent of document.querySelectorAll('body *')) {
    if (parent.closest(chrome)) continue;
    const kids = Array.from(parent.children).filter(k => !skipTags.has(k.tagName) && shown(k) && !k.querySelector('th'));
    if (kids.length < 2) continue;
    const counts = {};
    kids.forEach(k => { const s = sig(k); counts[s] = (counts[s] || 0) + 1; });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    if (!top || top[1] < 2) continue;
    const items = kids.filter(k => sig(k) === top[0] && (k.innerText || '').trim().length > 0);
    if (items.length < 2) continue;
    const values = items.map(it => key === 'number' ? numberOf(it.innerText || '') : primaryText(it)).filter(v => v !== null && v !== '');
    if (values.length < items.length) continue;
    const avgLen = items.reduce((n, it) => n + (it.innerText || '').length, 0) / items.length;
    const score = items.length + (items[0].tagName === 'TR' || items[0].tagName === 'LI' ? 0.5 : 0) + (avgLen > 20 ? 0.75 : 0);
    if (!best || score > best.score) {
      best = {score: score, values: values, texts: items.map(it => (it.innerText || '').replace(/\\s+/g, ' ').trim()),
              fields: items.map(fieldsOf), headers: headersOf(items[0]), signature: top[0]};
    }
  }
  return best || {values: [], texts: [], signature: null};
}
"""


# The repeated record (row, list item, card) that contains a control, captured before acting on it.
RECORD_SNAPSHOT_SCRIPT = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
  let rec = el.closest('tr, li, article, [role="row"], [role="listitem"]');
  for (let a = el.parentElement; !rec && a && a !== document.body; a = a.parentElement) {
    const p = a.parentElement;
    if (p && Array.from(p.children).filter(k => k.tagName === a.tagName && k.className === a.className).length >= 2) rec = a;
  }
  if (!rec || !rec.parentElement) return null;
  const text = norm(rec.innerText);
  const count = Array.from(rec.parentElement.children).filter(k => k.tagName === rec.tagName && norm(k.innerText) === text).length;
  return {id: rec.id || '', tag: rec.tagName, text: text, count: count};
}
"""

# How many records identical to the snapshot exist now (by id when the record has one).
RECORD_COUNT_SCRIPT = """
(s) => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
  if (s.id) {
    const r = document.getElementById(s.id);
    return r && norm(r.innerText) === s.text ? 1 : 0;
  }
  return Array.from(document.querySelectorAll(s.tag)).filter(k => norm(k.innerText) === s.text).length;
}
"""


def record_unchanged(snapshot, count_after):
    """True when the record a command acted on is still present with identical content."""
    return bool(snapshot) and count_after >= (1 if snapshot.get("id") else snapshot.get("count", 1))

from app.execution import list_oracles  # noqa: E402
from app.auth import credentials as auth_registry  # noqa: E402
from app.auth.session import SessionCache, ensure_page, session_reuse_enabled  # noqa: E402

SETUP_FAILED = "SETUP_FAILED"


class AuthSetupFailed(Exception):
    """The page a test needs could not be reached with the authentication it requires."""

    def __init__(self, access):
        super().__init__(f"{access.state.value}: {access.reason}")
        self.access = access


class SetupValueLost(Exception):
    """A prerequisite input did not keep the value it was filled with."""

if sys.platform == "win32":
    try:
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


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
        self.session_cache = SessionCache()   # authenticated sessions of the current run (memory only)
        self._context = None
        self._auth_reused = False
        self._auth_events = []

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
        Transparently handles Windows SelectorEventLoop environments (e.g. uvicorn --reload)
        by delegating execution to a dedicated ProactorEventLoop thread.
        """
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            if not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ())):
                return await asyncio.to_thread(
                    self._execute_steps_in_thread,
                    test_id,
                    test_name,
                    steps,
                    initial_url,
                    timeout_ms,
                )
        return await self._execute_steps_impl(test_id, test_name, steps, initial_url, timeout_ms)

    def _execute_steps_in_thread(
        self,
        test_id: str,
        test_name: str,
        steps: List[ActionStep],
        initial_url: Optional[str] = None,
        timeout_ms: int = 8000,
    ) -> ExecutionResult:
        p_loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(p_loop)
        try:
            return p_loop.run_until_complete(
                self._execute_steps_impl(test_id, test_name, steps, initial_url, timeout_ms)
            )
        finally:
            p_loop.close()

    async def _execute_steps_impl(
        self,
        test_id: str,
        test_name: str,
        steps: List[ActionStep],
        initial_url: Optional[str] = None,
        timeout_ms: int = 8000,
    ) -> ExecutionResult:
        step_results: List[StepExecutionResult] = []
        console_logs: List[str] = []
        overall_status = ExecutionStatus.PASSED
        failure_reason: Optional[str] = None
        failure_step_num: Optional[int] = None
        final_screenshot_path: Optional[str] = None
        dom_snippet: Optional[str] = None
        assertions_evaluated = 0
        assertions_satisfied = 0
        meaningful_evaluated = 0
        evidence_retained: Dict[str, Any] = {}

        start_time = time.time()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            # A fresh context per test. An authenticated session from earlier in this run (same origin and
            # credential profile only) is started from a copy of its storage state; contexts are never shared.
            self._auth_reused = False
            self._auth_events = []
            storage_state = None
            entry = next((s for s in steps if s.action == ActionType.NAVIGATE and s.setup and s.value), None)
            if entry is not None and session_reuse_enabled():
                status, profile = auth_registry.resolve(entry.value)
                if profile is not None:
                    storage_state = self.session_cache.get(auth_registry.canonical_origin(entry.value), profile.name)
                    self._auth_reused = storage_state is not None
            context_kwargs = {"viewport": {"width": 1280, "height": 800}, "user_agent": "VerityQA-Autonomous-QA-Agent/1.0"}
            if storage_state is not None:
                context_kwargs["storage_state"] = storage_state
            context: BrowserContext = await browser.new_context(**context_kwargs)
            self._context = context
            page: Page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            # Collect console messages for evidence
            page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
            page.on("pageerror", lambda err: console_logs.append(f"[pageerror] {str(err)}"))

            self._last_status = None
            page.on("response", lambda resp: self._record_document_status(page, resp))

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

                # 1. Execute the primary action. Failures here are never product verdicts:
                #    a missing/unactionable element is a LOCATOR_ERROR, an unreachable target an
                #    ENVIRONMENT_ERROR, anything else an AUTOMATION_ERROR.
                record_snapshot = None
                if step.assertion and step.assertion.assertion_type == AssertionType.RECORD_CHANGED and step.selector:
                    try:
                        record_snapshot = await page.evaluate(RECORD_SNAPSHOT_SCRIPT, step.selector)
                    except PlaywrightError:
                        record_snapshot = None
                self._record_snapshot = record_snapshot
                try:
                    await self._execute_action(page, step, timeout_ms)
                except PlaywrightError as pe:
                    step_status = self._classify_action_error(step, pe)
                    error_msg = f"Playwright error: {str(pe)}"
                except SetupValueLost as lost:
                    step_status = ExecutionStatus.AUTOMATION_ERROR
                    error_msg = f"Setup value not retained: {lost}"
                except AuthSetupFailed as blocked:
                    step_status = ExecutionStatus.BLOCKED
                    error_msg = str(blocked)
                except Exception as ex:
                    step_status = ExecutionStatus.ERROR
                    error_msg = f"Execution error: {str(ex)}"

                # 2. Check the attached assertion: only a failed evaluable assertion is FAILED.
                if step_status == ExecutionStatus.PASSED and step.assertion:
                    try:
                        expected_val = step.assertion.expected_value
                        passed, actual_val, assert_err = await self._verify_assertion(
                            page, step.assertion, timeout_ms
                        )
                        if assert_err and assert_err.startswith(NOT_EVALUABLE):
                            step_status = ExecutionStatus.INCONCLUSIVE
                            error_msg = assert_err
                        elif step.setup:
                            # A precondition check: recorded, but not one of the test's assertions.
                            evidence_retained.setdefault("setup_checks", []).append(
                                {"step": step.step_number, "passed": bool(passed), "actual": actual_val})
                            if not passed:
                                step_status = ExecutionStatus.FAILED
                                error_msg = assert_err
                        else:
                            assertions_evaluated += 1
                            if is_meaningful_assertion(step.assertion):
                                meaningful_evaluated += 1
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
                        step_status = ExecutionStatus.AUTOMATION_ERROR
                        error_msg = f"Assertion evaluation error: {str(pe)}"
                    except Exception as ex:
                        step_status = ExecutionStatus.ERROR
                        error_msg = f"Assertion evaluation error: {str(ex)}"

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

                if step_status != ExecutionStatus.PASSED:
                    overall_status = step_status
                    failure_reason = error_msg or "Step assertion failed"
                    failure_step_num = step.step_number
                    if step.setup:
                        # The behaviour under test was never reached: a setup failure, not a product verdict.
                        evidence_retained["setup_failure"] = {
                            "step": step.step_number, "action": step.action.value, "selector": step.selector,
                            "underlying_status": step_status.value, "reason": failure_reason[:300],
                            "url": page.url, "screenshot": step_screenshot,
                        }
                        overall_status = ExecutionStatus.BLOCKED
                        failure_reason = f"{SETUP_FAILED}: step {step.step_number} ({step.action.value}) — {failure_reason}"
                    break

            if self._auth_events:
                evidence_retained["auth"] = self._auth_events
            # A test passes only when its stated assertions have actually been evaluated and satisfied
            stated_assertions = sum(1 for s in steps if s.assertion is not None and not s.setup)
            if overall_status == ExecutionStatus.PASSED and stated_assertions > 0:
                if assertions_satisfied < stated_assertions:
                    overall_status = ExecutionStatus.FAILED
                    failure_reason = f"Only {assertions_satisfied}/{stated_assertions} stated assertions were satisfied"
                elif meaningful_evaluated == 0:
                    # Only trivially-true checks (e.g. 'body is visible') ran: nothing was verified.
                    overall_status = ExecutionStatus.INCONCLUSIVE
                    failure_reason = "Only trivial assertions were evaluated; no product behaviour was verified"
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

        # Zero-assertion tests must never count as PASS
        if overall_status == ExecutionStatus.PASSED and assertions_evaluated == 0:
            overall_status = ExecutionStatus.FAILED
            failure_reason = "Test completed with 0 evaluated assertions. Zero-assertion tests cannot count as PASS."

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
            meaningful_assertions_evaluated=meaningful_evaluated,
            evidence_retained=evidence_retained,
        )

    def _record_document_status(self, page: Page, response: Any) -> None:
        """Remembers the HTTP status of the latest main-frame document response (for STATUS_CODE)."""
        try:
            if response.request.resource_type == "document" and response.frame == page.main_frame:
                self._last_status = response.status
        except Exception:
            pass

    @staticmethod
    def _classify_action_error(step: ActionStep, err: Exception) -> ExecutionStatus:
        text = str(err)
        if any(k in text for k in ("net::ERR_", "NS_ERROR_", "Target page, context or browser has been closed",
                                   "Browser has been closed", "Connection refused")):
            return ExecutionStatus.ENVIRONMENT_ERROR
        if step.selector and ("Timeout" in text or "waiting for" in text or "not visible" in text
                              or "did not match any elements" in text or "strict mode violation" in text
                              or "did not find some options" in text):
            return ExecutionStatus.LOCATOR_ERROR
        return ExecutionStatus.AUTOMATION_ERROR

    async def _execute_action(self, page: Page, step: ActionStep, timeout_ms: int):
        """Dispatches structured step actions safely."""
        if step.action == ActionType.NAVIGATE:
            if not step.value:
                raise ValueError("NAVIGATE action requires 'value' (target URL)")
            target_nav = step.value
            if target_nav.startswith("/"):
                cur_url = page.url
                if cur_url and not cur_url.startswith("about:"):
                    from urllib.parse import urlparse
                    p = urlparse(cur_url)
                    target_nav = f"{p.scheme}://{p.netloc}{target_nav}"
            if step.setup:
                # Entry navigation: reach the page with the authentication it needs, then verify it was reached.
                access = await ensure_page(page, self._context, target_nav, requires_login=step.requires_login,
                                           login_url=step.login_url, cache=self.session_cache,
                                           session_reused=self._auth_reused, timeout_ms=timeout_ms)
                self._auth_events.append({"step": step.step_number, "state": access.state.value, "profile": access.profile,
                                          "login_performed": access.login_performed, "session_reused": access.session_reused,
                                          "reason": access.reason[:200]})
                if not access.ok:
                    raise AuthSetupFailed(access)
            else:
                await page.goto(target_nav, wait_until="domcontentloaded", timeout=timeout_ms)

        elif step.action == ActionType.FILL:
            if not step.selector:
                raise ValueError("FILL action requires 'selector'")
            # Wait for selector to be visible and editable
            await page.wait_for_selector(step.selector, state="visible", timeout=timeout_ms)
            await page.fill(step.selector, step.value or "", timeout=timeout_ms)
            if step.setup:
                # A prerequisite value must be in the field when the step completes (lengths only: may be a secret).
                kept = await page.input_value(step.selector, timeout=timeout_ms)
                if kept != (step.value or ""):
                    raise SetupValueLost(f"field {step.selector} holds {len(kept)} chars after filling {len(step.value or '')}")

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
            # A change handler may reload or re-render the page; let that settle before any oracle runs.
            await page.wait_for_timeout(300)
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5000))

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

        elif atype == AssertionType.STATUS_CODE:
            status = getattr(self, "_last_status", None)
            if status is None:
                return False, None, f"{NOT_EVALUABLE} no document response status was captured"
            m = re.match(r"^\s*(<=|>=|<|>|==)?\s*(\d{3})\s*$", expected or "")
            if not m:
                return False, None, f"{NOT_EVALUABLE} unsupported STATUS_CODE expectation '{expected}'"
            op, num = m.group(1) or "==", int(m.group(2))
            ok = {"<": status < num, "<=": status <= num, ">": status > num, ">=": status >= num, "==": status == num}[op]
            return (True, str(status), None) if ok else (False, str(status), f"Expected HTTP status {op}{num}, got {status}")

        elif atype == AssertionType.FIELD_VALIDITY:
            res = await page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el || !el.validity) return null;
                    const v = el.validity;
                    const flags = ['valueMissing','typeMismatch','patternMismatch','tooLong','tooShort',
                                   'rangeUnderflow','rangeOverflow','stepMismatch','badInput'].filter(k => v[k]);
                    return {valid: v.valid, flags: flags, message: el.validationMessage || '', value_length: (el.value || '').length};
                }""",
                assertion.target_selector or "",
            )
            if res is None:
                return False, "field not present", f"Field '{assertion.target_selector}' is not present (the form may have been submitted)"
            actual = "valid" if res["valid"] else ",".join(res["flags"]) or "invalid"
            if expected == "valid":
                ok = bool(res["valid"])
            elif expected == "invalid":
                ok = not res["valid"]
            else:
                ok = expected in res["flags"]
            detail = f"validity={actual}; message='{res['message'][:80]}'"
            return (True, detail, None) if ok else (False, detail, f"Expected field validity '{expected}', observed '{actual}'")

        elif atype == AssertionType.ELEMENT_VALUE_LENGTH:
            length = await page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? (el.value || '').length : null; }",
                assertion.target_selector or "",
            )
            if length is None:
                return False, "field not present", f"Field '{assertion.target_selector}' is not present"
            m = re.match(r"^\s*(<=|>=|==)?\s*(\d+)\s*$", expected or "")
            if not m:
                return False, None, f"{NOT_EVALUABLE} unsupported length expectation '{expected}'"
            op, num = m.group(1) or "==", int(m.group(2))
            ok = {"<=": length <= num, ">=": length >= num, "==": length == num}[op]
            return (True, f"length={length}", None) if ok else (False, f"length={length}", f"Expected value length {op}{num}, got {length}")

        elif atype in (AssertionType.VALIDATION_FEEDBACK_SHOWN, AssertionType.NO_VALIDATION_FEEDBACK):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            feedback = await page.evaluate(
                """(sel) => {
                    const visible = el => !!(el && el.offsetParent !== null && (el.innerText || '').trim().length > 0);
                    const out = [];
                    const field = sel ? document.querySelector(sel) : null;
                    if (field && field.validity && !field.validity.valid) out.push('field invalid: ' + (field.validationMessage || 'constraint violated'));
                    if (field && field.getAttribute('aria-invalid') === 'true') out.push('field aria-invalid');
                    const msgs = document.querySelectorAll('[role="alert"], .error, .errors, .error-message, .invalid-feedback, .alert-danger, .field-error, .validation-error, [class*="error-message"], [data-test="error"], [aria-live="assertive"]');
                    for (const m of msgs) { if (visible(m)) out.push('message: ' + m.innerText.trim().slice(0, 120)); }
                    return out;
                }""",
                assertion.target_selector or "",
            )
            # With rule keywords, a message counts only when it names the field or the rule; field-level
            # invalidity always counts. Without keywords any validation feedback counts.
            try:
                keywords = [k.lower() for k in json.loads(expected)] if expected.strip().startswith("[") else []
            except ValueError:
                keywords = []
            attributable = [f for f in feedback if not f.startswith("message: ")
                            or not keywords or any(k in f.lower() for k in keywords)]
            unrelated = [f for f in feedback if f not in attributable]
            shown = bool(attributable)
            want = atype == AssertionType.VALIDATION_FEEDBACK_SHOWN
            detail = "; ".join(attributable) if attributable else (
                f"no feedback attributable to this rule (unrelated: {'; '.join(unrelated)})" if unrelated else "no validation feedback visible")
            if shown == want:
                return True, detail, None
            return False, detail, ("Expected the submission to be rejected with feedback attributable to this rule, but "
                                   + (f"only unrelated feedback was shown: {'; '.join(unrelated)}" if unrelated else "none was shown")
                                   if want else f"Expected the submission to be accepted, but rule feedback was shown: {detail}")

        elif atype == AssertionType.SELECTED_VALUE:
            value = await page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? el.value : null; }",
                assertion.target_selector or "",
            )
            if value is None:
                return False, "control not present", f"Control '{assertion.target_selector}' is not present"
            return (True, value, None) if value == expected else (False, value, f"Expected selected value '{expected}', got '{value}'")

        elif atype in (AssertionType.LIST_SORTED, AssertionType.LIST_ALL_CONTAIN):
            if atype == AssertionType.LIST_SORTED:
                try:
                    spec = json.loads(expected)
                except ValueError:
                    return False, None, f"{NOT_EVALUABLE} LIST_SORTED needs a JSON ordering rule"
            else:
                # {"value": ..., "domain": [...]} compares each record's own field; a bare string is the
                # legacy whole-record substring check.
                try:
                    spec = json.loads(expected) if (expected or "").lstrip().startswith("{") else {}
                except ValueError:
                    spec = {}
            key = spec.get("key", "text")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            data = await page.evaluate(COLLECTION_SCRIPT, {"key": key, "needle": ""})
            if atype == AssertionType.LIST_SORTED:
                verdict, actual, message = list_oracles.evaluate_sorted(data, spec)
            elif "domain" in spec:
                verdict, actual, message = list_oracles.evaluate_filter(data, spec.get("value", ""), spec["domain"])
            else:
                texts = (data or {}).get("texts") or []
                if not texts:
                    return False, None, f"{NOT_EVALUABLE} could not identify any listed records"
                missing = [t[:80] for t in texts if expected.lower() not in t.lower()]
                if missing:
                    return False, f"{len(missing)}/{len(texts)} records lack '{expected}'",                         f"Expected every listed record to contain '{expected}'; non-matching: {missing[:3]}"
                return True, f"all {len(texts)} records contain '{expected}'", None
            if verdict == "not_evaluable":
                return False, None, f"{NOT_EVALUABLE} {message}"
            return verdict == "pass", actual, message

        elif atype == AssertionType.RECORD_CHANGED:
            snapshot = getattr(self, "_record_snapshot", None)
            if not snapshot:
                return False, None, f"{NOT_EVALUABLE} the control is not inside a repeated record (row, item or card)"
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            count_after = await page.evaluate(RECORD_COUNT_SCRIPT, snapshot)
            if record_unchanged(snapshot, count_after):
                return False, snapshot["text"][:120], f"Record unchanged after the action: '{snapshot['text'][:120]}'"
            return True, f"record changed or removed (was: '{snapshot['text'][:80]}')", None

        elif atype == AssertionType.NO_TEXT_ANOMALY:
            text = await page.evaluate("() => (document.body && document.body.innerText) || ''")
            found = []
            for pattern in TEXT_ANOMALY_PATTERNS:
                m = re.search(pattern, text or "")
                if m:
                    start = max(0, m.start() - 40)
                    found.append(text[start:m.end() + 40].replace("\n", " ").strip())
            if found:
                return False, "; ".join(found[:3]), f"Rendered text contains anomalies: {found[:3]}"
            return True, "no rendering anomalies", None

        elif atype == AssertionType.CUSTOM:
            return False, None, f"{NOT_EVALUABLE} CUSTOM assertions have no executable oracle"

        return False, None, f"{NOT_EVALUABLE} unsupported assertion type {atype.value}"
