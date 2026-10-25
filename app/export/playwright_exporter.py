"""
Standalone Playwright (Python) export of executed test plans.

The exporter renders exactly the steps the internal runner executes (QAPlanRunner.synthesize_steps)
and every assertion attached to them, using the same oracle semantics as BrowserExecutor:
  * no step is reconstructed from summaries or descriptions,
  * nothing prints PASS: a test passes only when pytest sees every _check() succeed,
  * an oracle that cannot observe what it needs skips as INCOMPLETE instead of passing,
  * INVALID cases are exported as skipped with their reason,
  * credentials are never written into the script: they are read from VERITYQA_TEST_USERNAME /
    VERITYQA_TEST_PASSWORD, and only when VERITYQA_TEST_CREDENTIALS_ORIGIN equals the target origin.
`export_semantics` returns the structured action/assertion list the code is generated from, so
parity with the runner can be checked without comparing source text.
"""

import json
import re
from typing import Any, Dict, List, Optional

from app.execution.executor import COLLECTION_SCRIPT, RECORD_COUNT_SCRIPT, RECORD_SNAPSHOT_SCRIPT, TEXT_ANOMALY_PATTERNS
from app.execution.runner import (
    CREDENTIAL_PASSWORD_PLACEHOLDER,
    CREDENTIAL_USERNAME_PLACEHOLDER,
    QAPlanRunner,
)
from app.intelligence.target_context import canonical_origin
from app.schemas import ActionStep, ActionType, AssertionType, TestCase, TestCaseStatus
from app.test_data import UNIQUE_TOKEN
from app.auth.login import DISCOVERY_SCRIPT as AUTH_DISCOVERY_SCRIPT

_HELPERS = '''
STATUS = {"last": None}


@pytest.fixture
def page():
    """Isolated Chromium page per test."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=os.environ.get("VERITYQA_HEADED") != "1")
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        pg = context.new_page()
        pg.set_default_timeout(int(os.environ.get("VERITYQA_TIMEOUT_MS", "8000")))
        STATUS["last"] = None

        def _on_response(resp):
            try:
                if resp.request.resource_type == "document" and resp.frame == pg.main_frame:
                    STATUS["last"] = resp.status
            except Exception:
                pass

        pg.on("response", _on_response)
        yield pg
        context.close()
        browser.close()


def credential(kind):
    """Origin-scoped credential from the environment; the test is BLOCKED (skipped) without one."""
    # A website profile whose ORIGIN is exactly this target (VERITYQA_CREDENTIALS_<PROFILE>_ORIGIN/_USERNAME/_PASSWORD).
    profiles = [k[len("VERITYQA_CREDENTIALS_"):-len("_ORIGIN")] for k, v in os.environ.items()
                if k.startswith("VERITYQA_CREDENTIALS_") and k.endswith("_ORIGIN") and v.rstrip("/") == TARGET_ORIGIN]
    if len(profiles) == 1 and os.environ.get(f"VERITYQA_CREDENTIALS_{profiles[0]}_{kind.upper()}"):
        return os.environ[f"VERITYQA_CREDENTIALS_{profiles[0]}_{kind.upper()}"]
    if os.environ.get("VERITYQA_TEST_CREDENTIALS_ORIGIN", "").rstrip("/") != TARGET_ORIGIN:
        pytest.skip(f"BLOCKED: no credentials authorized for {TARGET_ORIGIN} (set VERITYQA_TEST_CREDENTIALS_ORIGIN)")
    value = os.environ.get("VERITYQA_TEST_" + kind.upper())
    if not value:
        pytest.skip(f"BLOCKED: VERITYQA_TEST_{kind.upper()} is not set")
    return value


def unique(value, token):
    """Per-test unique data: identity values never collide with records an earlier run created."""
    return value.replace(UNIQUE_TOKEN, token)


def record_changed(page, snapshot):
    """The record a command acted on must be removed or changed (same semantics as the executor)."""
    if not snapshot:
        incomplete("the control is not inside a repeated record (row, item or card)")
    page.wait_for_load_state("domcontentloaded")
    count = page.evaluate(RECORD_COUNT_SCRIPT, snapshot)
    assert count < (1 if snapshot.get("id") else snapshot.get("count", 1)), \
        f"Record unchanged after the action: {snapshot['text'][:120]!r}"


def setup_check(kind, page, selector, expected):
    """A prerequisite's check: failing it means the behaviour under test was never reached (BLOCKED)."""
    try:
        check(kind, page, selector, expected)
    except AssertionError as err:
        pytest.skip(f"BLOCKED: SETUP_FAILED: {err}")


def setup_fill(page, selector, value):
    """Fill a prerequisite field and require that it keeps the value (lengths only in the message)."""
    page.locator(selector).first.fill(value)
    kept = page.locator(selector).first.input_value()
    if kept != value:
        pytest.skip(f"BLOCKED: SETUP_FAILED: field {selector} holds {len(kept)} chars after filling {len(value)}")


def _page_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path.rstrip("/") or "/"


def _login_gate(seen):
    return bool(seen.get("challenge")) or any(f["password_count"] == 1 and f["identity_count"] >= 1 for f in seen.get("forms", []))


def open_page(page, url, requires_login=False, login_url=None):
    """Reach a page with the authentication it needs (same rules as VerityQA's executor); BLOCKED skips otherwise."""
    page.goto(url, wait_until="domcontentloaded")
    seen = page.evaluate(AUTH_DISCOVERY_SCRIPT)
    gate = _page_path(page.url) != _page_path(url) and _login_gate(seen)
    if not gate and (not requires_login or seen.get("logout_visible")):
        return
    username, password = credential("username"), credential("password")   # skips as BLOCKED when not configured
    if not gate and login_url:
        page.goto(login_url, wait_until="domcontentloaded")
        seen = page.evaluate(AUTH_DISCOVERY_SCRIPT)
    if seen.get("challenge"):
        pytest.skip(f"BLOCKED: AUTH_UNRESOLVED: {seen['challenge']} challenge; automated login is not attempted")
    forms = [f for f in seen.get("forms", []) if f["password_count"] == 1 and f["identity_count"] >= 1
             and f["username_selector"] and f["password_selector"]]
    if len(forms) > 1 and len([f for f in forms if f["login_words"]]) == 1:
        forms = [f for f in forms if f["login_words"]]
    if len(forms) != 1:
        pytest.skip(f"BLOCKED: AUTH_UNRESOLVED: {len(forms)} candidate login forms")
    form = forms[0]
    page.locator(form["username_selector"]).first.fill(username)
    page.locator(form["password_selector"]).first.fill(password)
    if form["submit_selector"]:
        page.locator(form["submit_selector"]).first.click()
    else:
        page.locator(form["password_selector"]).first.press("Enter")
    page.wait_for_load_state("domcontentloaded")
    after = page.evaluate(AUTH_DISCOVERY_SCRIPT)
    if after.get("error") or after.get("password_visible"):
        pytest.skip("BLOCKED: LOGIN_FAILED: " + (after.get("error") or "login form still present"))
    page.goto(url, wait_until="domcontentloaded")
    if _page_path(page.url) != _page_path(url):
        pytest.skip(f"BLOCKED: required page not reached after login (landed on {page.url})")


def incomplete(reason):
    pytest.skip("INCOMPLETE: " + reason)


def check(kind, page, selector, expected):
    """Evaluates one oracle with the same semantics as the VerityQA BrowserExecutor."""
    if kind == "URL_CONTAINS":
        assert expected.lower() in page.url.lower(), f"Expected URL to contain {expected!r}, got {page.url!r}"
    elif kind == "URL_EQUALS":
        assert page.url == expected, f"Expected URL {expected!r}, got {page.url!r}"
    elif kind == "TEXT_VISIBLE":
        scope = page.locator(selector) if selector else page.locator("body")
        expect(scope.get_by_text(expected, exact=False).first).to_be_visible()
    elif kind == "TEXT_NOT_VISIBLE":
        scope = page.locator(selector) if selector else page.locator("body")
        expect(scope.get_by_text(expected, exact=False).first).not_to_be_visible()
    elif kind == "ELEMENT_VISIBLE":
        expect(page.locator(selector).first).to_be_visible()
    elif kind == "ELEMENT_NOT_VISIBLE":
        expect(page.locator(selector).first).not_to_be_visible()
    elif kind == "ELEMENT_DISABLED":
        expect(page.locator(selector).first).to_be_disabled()
    elif kind == "ELEMENT_ENABLED":
        expect(page.locator(selector).first).to_be_enabled()
    elif kind == "STATUS_CODE":
        status = STATUS["last"]
        if status is None:
            incomplete("no document response status was captured")
        m = re.match(r"^\\s*(<=|>=|<|>|==)?\\s*(\\d{3})\\s*$", expected)
        op, num = (m.group(1) or "=="), int(m.group(2))
        ok = {"<": status < num, "<=": status <= num, ">": status > num, ">=": status >= num, "==": status == num}[op]
        assert ok, f"Expected HTTP status {op}{num}, got {status}"
    elif kind == "FIELD_VALIDITY":
        res = page.evaluate("""(sel) => { const el = document.querySelector(sel); if (!el || !el.validity) return null;
            const v = el.validity; return {valid: v.valid, flags: ['valueMissing','typeMismatch','patternMismatch','tooLong',
            'tooShort','rangeUnderflow','rangeOverflow','stepMismatch','badInput'].filter(k => v[k])}; }""", selector)
        assert res is not None, f"Field {selector!r} is not present (the form may have been submitted)"
        if expected == "valid":
            assert res["valid"], f"Expected {selector!r} to be valid, flags={res['flags']}"
        elif expected == "invalid":
            assert not res["valid"], f"Expected {selector!r} to be invalid"
        else:
            assert expected in res["flags"], f"Expected validity {expected!r} on {selector!r}, flags={res['flags']}"
    elif kind == "ELEMENT_VALUE_LENGTH":
        length = page.evaluate("(sel) => { const el = document.querySelector(sel); return el ? (el.value || '').length : null; }", selector)
        assert length is not None, f"Field {selector!r} is not present"
        m = re.match(r"^\\s*(<=|>=|==)?\\s*(\\d+)\\s*$", expected)
        op, num = (m.group(1) or "=="), int(m.group(2))
        assert {"<=": length <= num, ">=": length >= num, "==": length == num}[op], f"Expected length {op}{num}, got {length}"
    elif kind in ("VALIDATION_FEEDBACK_SHOWN", "NO_VALIDATION_FEEDBACK"):
        page.wait_for_load_state("domcontentloaded")
        feedback = page.evaluate("""(sel) => { const out = []; const visible = el => !!(el && el.offsetParent !== null && (el.innerText || '').trim());
            const f = sel ? document.querySelector(sel) : null;
            if (f && f.validity && !f.validity.valid) out.push('field invalid'); if (f && f.getAttribute('aria-invalid') === 'true') out.push('aria-invalid');
            for (const m of document.querySelectorAll('[role="alert"], .error, .errors, .error-message, .invalid-feedback, .alert-danger, .field-error, .validation-error, [class*="error-message"], [data-test="error"], [aria-live="assertive"]'))
              if (visible(m)) out.push('message: ' + m.innerText.trim().slice(0, 120)); return out; }""", selector or "")
        keywords = [k.lower() for k in json.loads(expected)] if expected.strip().startswith("[") else []
        attributable = [f for f in feedback if not f.startswith("message: ") or not keywords or any(k in f.lower() for k in keywords)]
        if kind == "VALIDATION_FEEDBACK_SHOWN":
            assert attributable, f"Expected feedback attributable to this rule; shown: {feedback or 'none'}"
        else:
            assert not attributable, f"Expected the submission to be accepted, but rule feedback was shown: {attributable}"
    elif kind == "SELECTED_VALUE":
        assert page.evaluate("(sel) => { const el = document.querySelector(sel); return el ? el.value : null; }", selector) == expected
    elif kind in ("LIST_SORTED", "LIST_ALL_CONTAIN"):
        page.wait_for_load_state("domcontentloaded")
        structured = kind == "LIST_SORTED" or expected.lstrip().startswith("{")
        spec = json.loads(expected) if structured else {}
        data = page.evaluate(COLLECTION_SCRIPT, {"key": spec.get("key", "text"), "needle": ""})
        if structured:
            if kind == "LIST_SORTED":
                verdict, _actual, message = evaluate_sorted(data, spec)
            else:
                verdict, _actual, message = evaluate_filter(data, spec.get("value", ""), spec.get("domain") or [])
            if verdict == "not_evaluable":
                incomplete(message)
            assert verdict == "pass", message
        else:
            texts = data.get("texts") or []
            if not texts:
                incomplete("could not identify any listed records")
            missing = [t[:80] for t in texts if expected.lower() not in t.lower()]
            assert not missing, f"Expected every record to contain {expected!r}; non-matching: {missing[:3]}"
    elif kind == "NO_TEXT_ANOMALY":
        text = page.evaluate("() => (document.body && document.body.innerText) || ''")
        found = [p for p in TEXT_ANOMALY_PATTERNS if re.search(p, text)]
        assert not found, f"Rendered text contains anomalies matching {found}"
    elif kind == "ACCESSIBILITY_VALID":
        missing = page.evaluate("""() => { const els = Array.from(document.querySelectorAll('input:not([type="hidden"]), button, select, textarea, a[href], [role="button"], [role="link"]'));
            let missing = 0; for (const el of els) { let n = (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title') || el.getAttribute('placeholder') || '').trim();
            if (!n && el.labels && el.labels.length) n = (el.labels[0].textContent || '').trim();
            if (!n && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) n = (l.textContent || '').trim(); }
            if (!n && ['BUTTON', 'A'].includes(el.tagName)) n = (el.textContent || '').trim();
            if (!n && el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes((el.type || '').toLowerCase())) n = (el.value || '').trim();
            if (!n) missing++; } return {missing: missing, total: els.length}; }""")
        assert missing["missing"] <= missing["total"] * 0.2, f"{missing['missing']}/{missing['total']} controls lack accessible names"
    elif kind == "VALIDITY_VALUE_MISSING":
        res = page.evaluate("""(sel) => { const el = document.querySelector(sel); const inv = document.querySelectorAll(':invalid').length;
            return {missing: !!(el && el.validity && el.validity.valueMissing), invalid: inv}; }""", selector or "input:required")
        assert res["missing"] or res["invalid"] > 0, "Expected a required-field (valueMissing) validation state"
    elif kind == "VALIDITY_INVALID":
        assert page.evaluate("(sel) => { const el = document.querySelector(sel); return !!(el && el.checkValidity && !el.checkValidity()) || document.querySelectorAll(':invalid').length > 0; }", selector or ":invalid")
    elif kind == "FORM_SUBMISSION_BLOCKED":
        blocked = page.evaluate("() => document.querySelectorAll(':invalid').length > 0")
        assert blocked or (expected and expected.rstrip('/') in page.url), "Expected form submission to be blocked"
    elif kind == "INPUT_MASKED":
        assert page.evaluate("(sel) => { const el = document.querySelector(sel) || document.querySelector('input[type=password]'); return !!el && el.type === 'password'; }", selector or "input[type=password]")
    else:
        incomplete(f"assertion type {kind} has no exportable oracle")
'''


def _func_name(tc: TestCase, idx: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", f"{tc.test_id}_{tc.name}").lower().strip("_")
    return f"test_{re.sub(r'_+', '_', safe)[:80]}_{idx}"


def _doc(text: Optional[str]) -> str:
    """Text safe to place inside a generated docstring."""
    return (text or "").replace("\\", "\\\\").replace('"""', "'''").replace("\n", " ")


def _oracle_source() -> str:
    """Source of the shared collection oracles (imports stripped; the script imports re itself)."""
    import inspect
    from app.execution import list_oracles
    src = inspect.getsource(list_oracles)
    return "\n".join(line for line in src.splitlines() if not line.startswith("import "))


def _value_expr(value: Optional[str]) -> str:
    if value == CREDENTIAL_PASSWORD_PLACEHOLDER:
        return 'credential("password")'
    if value == CREDENTIAL_USERNAME_PLACEHOLDER:
        return 'credential("username")'
    if value and UNIQUE_TOKEN in value:
        return f"unique({value!r}, run_token)"
    return repr(value or "")


class PlaywrightTestExporter:
    """Exports executed test plans into self-contained pytest + Playwright scripts."""

    def __init__(self, runner: QAPlanRunner = None):
        self.runner = runner or QAPlanRunner()

    def steps_for(self, tc: TestCase, target_url: str) -> List[ActionStep]:
        """The exact steps the runner executes for this case (shared source of truth)."""
        return self.runner.synthesize_steps(tc, target_url)

    def export_semantics(self, test_cases: List[TestCase], target_url: str) -> List[Dict[str, Any]]:
        """Structured view of what the exported script will do, per test case."""
        out = []
        for tc in test_cases:
            invalid = tc.status == TestCaseStatus.INVALID
            steps = [] if invalid else self.steps_for(tc, target_url)
            out.append({
                "test_id": tc.test_id,
                "status": "INVALID" if invalid else "EXECUTABLE",
                "actions": [(s.action.value, s.selector, s.value) for s in steps],
                "assertions": [(s.assertion.assertion_type.value, s.assertion.target_selector, s.assertion.expected_value)
                               for s in steps if s.assertion],
            })
        return out

    def export_to_python_script(self, test_cases: List[TestCase], target_url: str) -> str:
        """Generates a standalone pytest module (requires: pip install pytest playwright)."""
        origin = canonical_origin(target_url) or target_url
        lines = [
            '"""',
            "Automated regression suite generated by VerityQA from an evidence-grounded test plan.",
            f"Target application: {target_url}",
            f"Target origin: {origin}",
            "Requirements: pip install pytest playwright && python -m playwright install chromium",
            "Run: pytest -v <this_file.py>",
            "Credentials (only for tests that need them): VERITYQA_TEST_CREDENTIALS_ORIGIN, VERITYQA_TEST_USERNAME,",
            "VERITYQA_TEST_PASSWORD. Outcomes: passed = all oracles held; failed = an oracle was violated;",
            "skipped BLOCKED/INCOMPLETE/INVALID = not a verdict.",
            '"""',
            "import json",
            "import os",
            "import re",
            "import secrets",
            "",
            "import pytest",
            "from playwright.sync_api import Page, expect, sync_playwright",
            "",
            f"TARGET_ORIGIN = {origin!r}",
            f"UNIQUE_TOKEN = {UNIQUE_TOKEN!r}",
            f"AUTH_DISCOVERY_SCRIPT = {AUTH_DISCOVERY_SCRIPT!r}",
            f"COLLECTION_SCRIPT = {COLLECTION_SCRIPT!r}",
            f"TEXT_ANOMALY_PATTERNS = {TEXT_ANOMALY_PATTERNS!r}",
            f"RECORD_SNAPSHOT_SCRIPT = {RECORD_SNAPSHOT_SCRIPT!r}",
            f"RECORD_COUNT_SCRIPT = {RECORD_COUNT_SCRIPT!r}",
        ]
        lines.extend(_HELPERS.splitlines())
        lines.extend(_oracle_source().splitlines())
        lines.append("")

        for idx, tc in enumerate(test_cases, 1):
            lines.append("")
            lines.append(f"def {_func_name(tc, idx)}(page: Page):")
            lines.append('    """')
            lines.append(f"    Test ID: {tc.test_id}")
            lines.append(f"    Objective: {_doc(tc.objective)}")
            lines.append(f"    Expected: {_doc(tc.expected_outcome)}")
            lines.append(f"    Expected-behaviour source: {getattr(tc.expected_behavior_source, 'value', tc.expected_behavior_source)}")
            if tc.investigation_id:
                lines.append(f"    Investigation: {tc.investigation_id}")
            lines.append('    """')
            if tc.status == TestCaseStatus.INVALID:
                lines.append(f"    pytest.skip({('INVALID: ' + (tc.invalid_reason or 'not executable'))!r})")
                continue
            steps = self.steps_for(tc, target_url)
            violation = QAPlanRunner.origin_violation(tc, steps, target_url)
            if violation:
                lines.append(f"    pytest.skip({('INVALID: ' + violation)!r})")
                continue
            lines.append(f"    page.goto({target_url!r}, wait_until='domcontentloaded')")
            lines.append(f"    run_token = secrets.token_hex({len(UNIQUE_TOKEN)})[:{len(UNIQUE_TOKEN)}]")
            for s in steps:
                lines.append(f"    # Step {s.step_number}: {s.description}".replace("\n", " "))
                sel = repr(s.selector or "")
                record_check = bool(s.assertion and s.assertion.assertion_type == AssertionType.RECORD_CHANGED)
                if record_check:
                    lines.append(f"    snapshot = page.evaluate(RECORD_SNAPSHOT_SCRIPT, {sel})")
                if s.action == ActionType.NAVIGATE and s.setup:
                    lines.append(f"    open_page(page, {s.value or target_url!r}, {bool(s.requires_login)!r}, {s.login_url!r})")
                elif s.action == ActionType.NAVIGATE:
                    lines.append(f"    page.goto({s.value or target_url!r}, wait_until='domcontentloaded')")
                elif s.action == ActionType.FILL and s.setup:
                    lines.append(f"    setup_fill(page, {sel}, {_value_expr(s.value)})")
                elif s.action == ActionType.FILL:
                    lines.append(f"    page.locator({sel}).first.fill({_value_expr(s.value)})")
                elif s.action == ActionType.CLICK:
                    lines.append(f"    page.locator({sel}).first.click()")
                    lines.append("    page.wait_for_load_state('domcontentloaded')")
                elif s.action == ActionType.SELECT:
                    lines.append(f"    page.locator({sel}).first.select_option({s.value or ''!r})")
                    lines.append("    page.wait_for_timeout(300)")
                    lines.append("    page.wait_for_load_state('domcontentloaded')")
                elif s.action == ActionType.PRESS_KEY:
                    lines.append(f"    page.keyboard.press({s.value or 'Enter'!r})" if not s.selector
                                 else f"    page.locator({sel}).first.press({s.value or 'Enter'!r})")
                elif s.action == ActionType.WAIT_FOR_ELEMENT:
                    lines.append(f"    page.locator({sel}).first.wait_for(state='visible')")
                elif s.action in (ActionType.EVAL_JS,):
                    lines.append(f"    page.evaluate({s.value or ''!r})")
                elif s.action == ActionType.SET_VALUE_JS:
                    lines.append(f"    page.locator({sel}).first.evaluate('(el, v) => {{ el.value = v; el.dispatchEvent(new Event(\"input\", {{bubbles: true}})); }}', {_value_expr(s.value)})")
                if record_check:
                    lines.append("    record_changed(page, snapshot)")
                elif s.assertion:
                    a = s.assertion
                    lines.append(f"    {'setup_check' if s.setup else 'check'}({a.assertion_type.value!r}, page, {a.target_selector!r}, {_value_expr(a.expected_value)})")
        lines.append("")
        return "\n".join(lines)
