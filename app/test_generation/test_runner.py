"""
VerityQA Reproducible Contract Test Runner (Phase 3).
====================================================
Executes `ContractTestSuiteArtifact` test cases via Playwright in isolated browser contexts.
Produces structured execution reports with verifiable evidence traceability,
explicit metric denominators, setup failure discrimination, and defect detection tracking.
"""

import os
import re
import time
import uuid
import logging
from enum import Enum
from typing import List, Dict, Optional, Any, Set, Tuple
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Page, BrowserContext, Error as PlaywrightError

from app.test_generation.contract_test_generator import (
    ContractTestSuiteArtifact,
    GeneratedContractTestCase,
    GroundedTestStep,
    ExpectedResultSpec,
    TestCategoryType,
    ExecutionSafetyClassification,
)
from app.investigation_agent.governor import SafetyGovernor

logger = logging.getLogger(__name__)


class TestExecutionStatus(str, Enum):
    __test__ = False
    PASSED = "PASSED"
    FAILED = "FAILED"
    DEFECT_DETECTED = "DEFECT_DETECTED"
    SETUP_FAILURE = "SETUP_FAILURE"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    SKIPPED_HYPOTHESIS = "SKIPPED_HYPOTHESIS"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class FailurePhase(str, Enum):
    __test__ = False
    SAFETY = "SAFETY"
    SETUP = "SETUP"
    EXECUTION = "EXECUTION"
    ASSERTION = "ASSERTION"


class TestCaseExecutionResult(BaseModel):
    __test__ = False
    test_id: str
    title: str
    category: str
    status: TestExecutionStatus
    duration_seconds: float
    source_evidence_ids: List[str] = Field(default_factory=list)
    source_state_ids: List[str] = Field(default_factory=list)
    source_transition_ids: List[str] = Field(default_factory=list)
    source_finding_ids: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    failure_phase: Optional[FailurePhase] = None
    screenshot_path: Optional[str] = None
    console_logs: List[str] = Field(default_factory=list)
    dom_snippet: Optional[str] = None
    assertions_evaluated: int = 0
    assertions_passed: int = 0


class NonExecutableTestCaseInfo(BaseModel):
    __test__ = False
    test_id: str
    title: str
    category: str
    reason: str


class ContractTestExecutionReport(BaseModel):
    __test__ = False
    execution_id: str
    source_investigation_id: str
    target_url: str
    executed_at: float = Field(default_factory=time.time)
    duration_seconds: float = 0.0

    # Primary Metrics & Denominators
    total_generated_cases: int
    exported_count: int
    executable_without_manual_edits_count: int
    executed_count: int
    passed_count: int
    failed_count: int
    defect_detected_count: int
    setup_failures_count: int
    safety_blocked_count: int
    skipped_hypotheses_count: int

    # Ratios
    setup_success_rate: float
    assertion_validity_rate: float
    defect_detection_rate: float
    false_positive_rate: float
    redundant_tests_count: int
    evidence_traceability_coverage: float

    # Detailed Results
    results: List[TestCaseExecutionResult] = Field(default_factory=list)
    non_executable_cases: List[NonExecutableTestCaseInfo] = Field(default_factory=list)


def _is_bare_identifier(sel: str) -> bool:
    """Returns True only if sel is a bare alphanumeric token with no CSS syntax, excluding HTML tags."""
    if not sel or not isinstance(sel, str):
        return False
    if any(c in sel for c in (".", "#", "[", "]", ":", ">", " ", "+", "~", "\"", "'", "/", "=")):
        return False
    common_tags = {
        "a", "button", "input", "select", "textarea", "form", "div", "span", "p",
        "h1", "h2", "h3", "h4", "h5", "h6", "body", "table", "tr", "td", "th", "ul", "ol", "li", "nav", "main", "header", "footer"
    }
    return bool(re.match(r"^[a-zA-Z0-9_\-]+$", sel)) and (sel.lower() not in common_tags)


import sys
import asyncio
if sys.platform == "win32":
    try:
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass


class PlaywrightContractTestRunner:
    """
    Direct asynchronous Playwright executor for ContractTestSuiteArtifact test cases.
    """

    def __init__(
        self,
        headless: bool = True,
        default_timeout_ms: int = 8000,
        artifacts_dir: str = "artifacts/playwright_runs",
        sandbox_mode: bool = True,
        sandbox_origins: Optional[List[str]] = None,
    ):
        self.headless = headless
        self.default_timeout_ms = default_timeout_ms
        self.artifacts_dir = artifacts_dir
        self.sandbox_mode = sandbox_mode
        self.sandbox_origins = [o.rstrip("/") for o in (sandbox_origins or [])]
        os.makedirs(self.artifacts_dir, exist_ok=True)

    async def run_suite(
        self,
        suite: ContractTestSuiteArtifact,
        target_base_url: Optional[str] = None,
        test_username: str = "benchmark_user",
        test_password: str = "secret_benchmark",
        stop_on_first_failure: bool = False,
    ) -> ContractTestExecutionReport:
        """
        Executes all eligible test cases in the suite and generates a structured evaluation report.
        Transparently handles Windows SelectorEventLoop environments (e.g. uvicorn --reload)
        by delegating execution to a dedicated ProactorEventLoop thread.
        """
        import sys, asyncio
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            if not isinstance(loop, getattr(asyncio, "ProactorEventLoop", ())):
                return await asyncio.to_thread(
                    self._run_suite_in_thread,
                    suite,
                    target_base_url,
                    test_username,
                    test_password,
                    stop_on_first_failure,
                )
        return await self._run_suite_impl(
            suite,
            target_base_url,
            test_username,
            test_password,
            stop_on_first_failure,
        )

    def _run_suite_in_thread(
        self,
        suite: ContractTestSuiteArtifact,
        target_base_url: Optional[str] = None,
        test_username: str = "benchmark_user",
        test_password: str = "secret_benchmark",
        stop_on_first_failure: bool = False,
    ) -> ContractTestExecutionReport:
        import asyncio
        p_loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(p_loop)
        try:
            return p_loop.run_until_complete(
                self._run_suite_impl(
                    suite,
                    target_base_url,
                    test_username,
                    test_password,
                    stop_on_first_failure,
                )
            )
        finally:
            p_loop.close()

    async def _run_suite_impl(
        self,
        suite: ContractTestSuiteArtifact,
        target_base_url: Optional[str] = None,
        test_username: str = "benchmark_user",
        test_password: str = "secret_benchmark",
        stop_on_first_failure: bool = False,
    ) -> ContractTestExecutionReport:
        start_time = time.time()
        exec_id = f"exec-{uuid.uuid4().hex[:8]}"
        base_url = (target_base_url or suite.target_url).rstrip("/")
        parsed_base = urlparse(base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

        # Approved sandbox origins are strictly configured (fail-closed)
        approved_origins = set(self.sandbox_origins)

        results: List[TestCaseExecutionResult] = []
        non_executable: List[NonExecutableTestCaseInfo] = []

        total_cases = len(suite.test_cases)
        exported_count = total_cases
        executable_count = 0

        # Classify executability beforehand
        for tc in suite.test_cases:
            if tc.category == TestCategoryType.EXPLORATORY_HYPOTHESIS:
                non_executable.append(
                    NonExecutableTestCaseInfo(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        reason="Unverified exploration hypothesis; requires empirical confirmation rather than regression execution",
                    )
                )
            elif any(any(kw in f"{s.action_type} {s.selector}".lower() for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS) for s in tc.steps):
                non_executable.append(
                    NonExecutableTestCaseInfo(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        reason="Contains terminal destructive or irreversible financial action",
                    )
                )
            else:
                executable_count += 1

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )

            for tc in suite.test_cases:
                # Handle hypothesis candidate
                if tc.category == TestCategoryType.EXPLORATORY_HYPOTHESIS:
                    results.append(
                        TestCaseExecutionResult(
                            test_id=tc.test_id,
                            title=tc.title,
                            category=tc.category.value,
                            status=TestExecutionStatus.SKIPPED_HYPOTHESIS,
                            duration_seconds=0.0,
                            source_evidence_ids=tc.evidence_ids,
                            source_state_ids=tc.source_state_ids,
                            source_transition_ids=tc.source_transition_ids,
                            source_finding_ids=tc.source_finding_ids,
                            error_message="Skipped: Candidate is an unverified hypothesis requiring enrichment",
                        )
                    )
                    continue

                # Isolated browser context for each test case
                context: BrowserContext = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="VerityQA-Contract-TestRunner/1.0",
                )
                page: Page = await context.new_page()
                page.set_default_timeout(self.default_timeout_ms)

                res = await self._execute_single_test_case(
                    context=context,
                    page=page,
                    tc=tc,
                    base_url=base_url,
                    approved_origins=approved_origins,
                    test_username=test_username,
                    test_password=test_password,
                )
                results.append(res)
                await context.close()

                if stop_on_first_failure and res.status in (TestExecutionStatus.FAILED, TestExecutionStatus.EXECUTION_ERROR):
                    break

            await browser.close()

        total_duration = round(time.time() - start_time, 2)

        # Calculate metrics
        passed_cnt = sum(1 for r in results if r.status == TestExecutionStatus.PASSED)
        failed_cnt = sum(1 for r in results if r.status == TestExecutionStatus.FAILED)
        defect_detected_cnt = sum(1 for r in results if r.status == TestExecutionStatus.DEFECT_DETECTED)
        setup_fail_cnt = sum(1 for r in results if r.status == TestExecutionStatus.SETUP_FAILURE)
        safety_blocked_cnt = sum(1 for r in results if r.status == TestExecutionStatus.SAFETY_BLOCKED)
        skipped_hyp_cnt = sum(1 for r in results if r.status == TestExecutionStatus.SKIPPED_HYPOTHESIS)
        executed_cnt = sum(1 for r in results if r.status not in (TestExecutionStatus.SKIPPED_HYPOTHESIS, TestExecutionStatus.SAFETY_BLOCKED))

        setup_success_rate = round((executed_cnt - setup_fail_cnt) / max(executed_cnt, 1), 3) if executed_cnt else 1.0

        # Assertion validity: ratio of clean evaluations among executed tests
        clean_runs = [r for r in results if r.status in (TestExecutionStatus.PASSED, TestExecutionStatus.FAILED, TestExecutionStatus.DEFECT_DETECTED)]
        assertion_validity_rate = round(len(clean_runs) / max(executed_cnt - setup_fail_cnt, 1), 3) if (executed_cnt - setup_fail_cnt) else 1.0

        # Defect detection rate: confirmed regression tests that caught defect
        reg_tests = [r for r in results if r.category == TestCategoryType.REGRESSION.value]
        defect_detection_rate = round(
            sum(1 for r in reg_tests if r.status in (TestExecutionStatus.DEFECT_DETECTED, TestExecutionStatus.FAILED)) / max(len(reg_tests), 1), 3
        ) if reg_tests else 1.0

        # False positive rate on non-regression tests
        functional_tests = [r for r in results if r.category != TestCategoryType.REGRESSION.value and r.status in (TestExecutionStatus.PASSED, TestExecutionStatus.FAILED)]
        false_positive_rate = round(sum(1 for r in functional_tests if r.status == TestExecutionStatus.FAILED) / max(len(functional_tests), 1), 3) if functional_tests else 0.0

        # Evidence traceability coverage
        with_evidence = sum(1 for r in results if len(r.source_evidence_ids) > 0)
        evidence_coverage_ratio = round(with_evidence / max(len(results), 1), 3)

        return ContractTestExecutionReport(
            execution_id=exec_id,
            source_investigation_id=suite.source_investigation_id,
            target_url=base_url,
            duration_seconds=total_duration,
            total_generated_cases=total_cases,
            exported_count=exported_count,
            executable_without_manual_edits_count=executable_count,
            executed_count=executed_cnt,
            passed_count=passed_cnt,
            failed_count=failed_cnt,
            defect_detected_count=defect_detected_cnt,
            setup_failures_count=setup_fail_cnt,
            safety_blocked_count=safety_blocked_cnt,
            skipped_hypotheses_count=skipped_hyp_cnt,
            setup_success_rate=setup_success_rate,
            assertion_validity_rate=assertion_validity_rate,
            defect_detection_rate=defect_detection_rate,
            false_positive_rate=false_positive_rate,
            redundant_tests_count=0,
            evidence_traceability_coverage=evidence_coverage_ratio,
            results=results,
            non_executable_cases=non_executable,
        )

    async def _execute_single_test_case(
        self,
        context: BrowserContext,
        page: Page,
        tc: GeneratedContractTestCase,
        base_url: str,
        approved_origins: Set[str],
        test_username: str,
        test_password: str,
    ) -> TestCaseExecutionResult:
        """Executes a single test case through safety check, setup, action, and assertion phases."""
        tc_start = time.time()
        console_logs: List[str] = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_logs.append(f"[pageerror] {str(err)}"))

        # -------------------------------------------------------------
        # Phase 0: Fail-Closed Safety Verification
        # -------------------------------------------------------------
        parsed_target = urlparse(base_url)
        target_origin = f"{parsed_target.scheme}://{parsed_target.netloc}"

        for s in tc.steps:
            action_target_str = f"{s.action_type} {s.selector or ''} {s.description or ''}".lower()
            norm_action_target = re.sub(r'[-_]', ' ', action_target_str)
            for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS:
                if kw in action_target_str or kw in norm_action_target:
                    return TestCaseExecutionResult(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        status=TestExecutionStatus.SAFETY_BLOCKED,
                        duration_seconds=round(time.time() - tc_start, 3),
                        source_evidence_ids=tc.evidence_ids,
                        source_state_ids=tc.source_state_ids,
                        source_transition_ids=tc.source_transition_ids,
                        source_finding_ids=tc.source_finding_ids,
                        failure_phase=FailurePhase.SAFETY,
                        error_message=f"SAFETY_BLOCKED: Terminal forbidden keyword '{kw}' detected in step '{s.description}'",
                    )
            if "checkout" in action_target_str or "checkout" in norm_action_target:
                if self.sandbox_mode:
                    is_authorized_origin = target_origin in approved_origins
                else:
                    is_authorized_origin = (
                        target_origin in approved_origins
                        or any(h in target_origin.lower() for h in get_settings().allowed_hosts_list)
                    )
                if not is_authorized_origin:
                    err_msg = (
                        f"SAFETY_BLOCKED: Origin '{target_origin}' is not an approved sandbox origin"
                        if self.sandbox_mode
                        else f"SAFETY_BLOCKED: Origin '{target_origin}' is not an authorized test origin for checkout interaction"
                    )
                    return TestCaseExecutionResult(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        status=TestExecutionStatus.SAFETY_BLOCKED,
                        duration_seconds=round(time.time() - tc_start, 3),
                        source_evidence_ids=tc.evidence_ids,
                        source_state_ids=tc.source_state_ids,
                        source_transition_ids=tc.source_transition_ids,
                        source_finding_ids=tc.source_finding_ids,
                        failure_phase=FailurePhase.SAFETY,
                        error_message=err_msg,
                    )

        # -------------------------------------------------------------
        # Phase 1: Setup & Precondition Establishment
        # -------------------------------------------------------------
        # 1. Determine starting URL
        first_step = tc.steps[0] if tc.steps else None
        first_act = first_step.action_type.split(":")[0].upper() if first_step else ""

        # Resilient login selectors matching standard auth patterns across apps
        login_u_sel = "#user-name, input[name='user-name'], #username, input[name='username'], input[name='email'], input[id='user-name']"
        login_p_sel = "#password, input[name='password'], input[id='password']"
        login_btn_sel = "#login-button, input[name='login-button'], input[type='submit'], button[type='submit'], button#loginBtn"

        start_url: str = base_url
        if first_act in ("NAVIGATE", "GOTO") and first_step and first_step.selector:
            dest = first_step.selector
            if dest.startswith("http"):
                p_dest = urlparse(dest)
                start_url = f"{base_url}{p_dest.path}{'?' + p_dest.query if p_dest.query else ''}"
            else:
                start_url = f"{base_url}{dest if dest.startswith('/') else '/' + dest}"
        else:
            # Infer starting URL from preconditions or source state
            prec_text = " ".join(tc.preconditions)
            url_match = re.search(r"https?://[^\s\)]+", prec_text)
            if not url_match:
                all_text = " ".join([tc.title] + [s.description for s in tc.steps] + [e.description for e in tc.expected_results])
                url_match = re.search(r"https?://[^\s\)\'\"]+", all_text)

            if url_match:
                extracted = url_match.group(0)
                parsed_extracted = urlparse(extracted)
                start_url = f"{base_url}{parsed_extracted.path}{'?' + parsed_extracted.query if parsed_extracted.query else ''}"
            elif any("cart" in s_id for s_id in tc.source_state_ids):
                start_url = f"{base_url}/cart.html" if "html" in str(tc.source_state_ids) else f"{base_url}/cart"
            elif any("products" in s_id or "inventory" in s_id for s_id in tc.source_state_ids):
                if any("inventory-item" in s_id or "id_" in s_id for s_id in tc.source_state_ids):
                    param_m = re.search(r"id_(\d+)", str(tc.source_state_ids))
                    item_id = param_m.group(1) if param_m else "3"
                    start_url = f"{base_url}/inventory-item.html?id={item_id}"
                else:
                    start_url = f"{base_url}/inventory.html" if "html" in str(tc.source_state_ids) else f"{base_url}/products"
            elif any("checkout" in s_id for s_id in tc.source_state_ids):
                start_url = f"{base_url}/checkout-step-one.html" if "step-on" in str(tc.source_state_ids) else f"{base_url}/checkout"

        req_auth = tc.safety_metadata.requires_test_credentials or any(
            "auth" in p.lower() or "session" in p.lower() for p in tc.preconditions
        )
        is_add_action = any("add-to-cart" in (s.selector or "").lower() or "add" in (s.description or "").lower() for s in tc.steps)
        is_remove_action = any("remove" in (s.selector or "").lower() or "remove" in (s.description or "").lower() for s in tc.steps)
        req_cart = any("cart" in p.lower() and "badge" in p.lower() and "0" not in p.lower() for p in tc.preconditions)
        should_populate_cart = (req_cart or is_remove_action or any("carthtml" in sid for sid in tc.source_state_ids)) and not is_add_action

        if first_act == "AUTHENTICATE":
            start_url = base_url
            is_login_test = True
        else:
            is_login_test = (start_url.rstrip("/") == base_url.rstrip("/") or "/login" in start_url) and (first_act in ("AUTHENTICATE", "FILL"))

        try:
            # If the test is NOT directly testing the login form, establish authentication session if required
            if req_auth and not is_login_test:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
                u_loc = page.locator(login_u_sel)
                if await u_loc.count() == 0:
                    await page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=self.default_timeout_ms)
                    u_loc = page.locator(login_u_sel)
                if await u_loc.count() > 0 and await u_loc.first.is_visible():
                    await u_loc.first.fill(test_username)
                    p_loc = page.locator(login_p_sel)
                    await p_loc.first.fill(test_password)
                    btn = page.locator(login_btn_sel)
                    await btn.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                    if "error=" in page.url:
                        raise RuntimeError(f"Authentication failed during setup: {page.url}")

                # Populate cart item if required by precondition
                if should_populate_cart:
                    combined_info = " ".join([tc.title] + [s.selector or "" for s in tc.steps] + tc.preconditions)
                    item_m = re.search(r'item[-_]?(\d+)', combined_info)
                    target_item_id = item_m.group(1) if item_m else None

                    prod_url = f"{base_url}/inventory.html" if "saucedemo" in base_url else f"{base_url}/products"
                    await page.goto(prod_url, wait_until="domcontentloaded", timeout=self.default_timeout_ms)

                    if target_item_id:
                        item_link = page.locator(f"#item_{target_item_id}_title_link, a[href*='id={target_item_id}']")
                        if await item_link.count() > 0 and await item_link.first.is_visible():
                            await item_link.first.click()
                            await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                            add_btn = page.locator("#add-to-cart, button[id^='add-to-cart']")
                            if await add_btn.count() > 0 and await add_btn.first.is_visible():
                                await add_btn.first.click()
                                await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                        else:
                            await page.goto(f"{base_url}/inventory-item.html?id={target_item_id}", wait_until="domcontentloaded", timeout=self.default_timeout_ms)
                            add_btn = page.locator("#add-to-cart, button[id^='add-to-cart']")
                            if await add_btn.count() > 0 and await add_btn.first.is_visible():
                                await add_btn.first.click()
                                await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                    else:
                        add_btn = page.locator("#add-to-cart-sauce-labs-backpack, #add-to-cart-1, button[id^='add-to-cart'], .btn_inventory, button:has-text('Add to cart')")
                        if await add_btn.count() > 0 and await add_btn.first.is_visible():
                            await add_btn.first.click()
                            await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)

            # Ensure browser reaches initial state URL if first step is not a navigate
            if first_act not in ("NAVIGATE", "GOTO"):
                await page.goto(start_url, wait_until="domcontentloaded", timeout=self.default_timeout_ms)

            # If drawer/menu is required by preconditions, open it after reaching start_url
            req_menu = any("drawer" in p.lower() or ("menu" in p.lower() and "open" in p.lower()) for p in tc.preconditions)
            if req_menu:
                menu_btn = page.locator("#react-burger-menu-btn, button[id*='burger'], button[id*='menu'], button[aria-label*='menu'], [aria-controls*='menu'], button.navbar-toggler")
                if await menu_btn.count() > 0 and await menu_btn.first.is_visible():
                    await menu_btn.first.click()
                    await page.wait_for_timeout(400)

        except Exception as ex:
            return TestCaseExecutionResult(
                test_id=tc.test_id,
                title=tc.title,
                category=tc.category.value,
                status=TestExecutionStatus.SETUP_FAILURE,
                duration_seconds=round(time.time() - tc_start, 3),
                source_evidence_ids=tc.evidence_ids,
                source_state_ids=tc.source_state_ids,
                source_transition_ids=tc.source_transition_ids,
                source_finding_ids=tc.source_finding_ids,
                failure_phase=FailurePhase.SETUP,
                error_message=f"Setup failure: {str(ex)}",
                console_logs=console_logs,
            )

        # -------------------------------------------------------------
        # Phase 2: Action Steps Execution & In-Flight Oracle Verification
        # -------------------------------------------------------------
        satisfied_oracles: Set[int] = set()
        assertions_eval = len(tc.expected_results)
        assertions_sat = 0

        try:
            for s in tc.steps:
                act = s.action_type.split(":")[0].upper()
                sel = s.selector or ""

                if act in ("NAVIGATE", "GOTO", "NAVIGATE_LINK", "NAV_LINK"):
                    if sel.startswith("http"):
                        p_sel = urlparse(sel)
                        dest = f"{base_url}{p_sel.path}{'?' + p_sel.query if p_sel.query else ''}"
                    else:
                        dest = f"{base_url}{sel if sel.startswith('/') else '/' + sel}"
                    try:
                        await page.goto(dest, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
                    except Exception as nav_err:
                        if "interrupted by another navigation" in str(nav_err).lower():
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                            except Exception:
                                pass
                            await page.goto(dest, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
                        else:
                            raise

                elif act == "AUTHENTICATE":
                    u_loc = page.locator(login_u_sel)
                    await u_loc.first.wait_for(state="visible", timeout=self.default_timeout_ms)
                    await u_loc.first.fill(test_username)
                    p_loc = page.locator(login_p_sel)
                    await p_loc.first.fill(test_password)
                    btn = page.locator(login_btn_sel)
                    await btn.first.click()
                    try:
                        await page.wait_for_url(lambda u: "login" not in u.lower() and u.rstrip("/") != base_url.rstrip("/"), timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(400)

                elif act == "SET_NOVALIDATE":
                    await page.evaluate("""() => {
                        const forms = document.querySelectorAll('form');
                        for (const f of forms) {
                            f.noValidate = true;
                            f.setAttribute('novalidate', '');
                        }
                    }""")
                    field_sel = sel if sel and not _is_bare_identifier(sel) else (
                        f"#{sel}, input[name='{sel}'], [data-test='{sel}']" if sel and sel != "form" else None
                    )
                    if field_sel:
                        f_loc = page.locator(field_sel)
                        if await f_loc.count() > 0 and await f_loc.first.is_visible():
                            await f_loc.first.fill(s.input_value or "a")
                    sub_loc = page.locator("input[type='submit'], button[type='submit'], #login-button, button.btn_primary")
                    if await sub_loc.count() > 0 and await sub_loc.first.is_visible():
                        await sub_loc.first.click()
                    else:
                        await page.evaluate("""() => {
                            const f = document.querySelector('form');
                            if (f) {
                                if (typeof f.requestSubmit === 'function') f.requestSubmit();
                                else f.submit();
                            }
                        }""")
                    await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)

                elif act == "FILL":
                    if "blank" in sel.lower() or "blank" in s.description.lower():
                        u_loc = page.locator(login_u_sel)
                        if await u_loc.count() > 0 and await u_loc.first.is_visible():
                            await u_loc.first.fill("")
                        btn = page.locator(login_btn_sel)
                        if await btn.count() > 0 and await btn.first.is_visible():
                            await btn.first.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                    elif "happy" in sel.lower() or sel.startswith("form_"):
                        await page.evaluate("""() => {
                            const inputs = document.querySelectorAll('input:not([type=submit]):not([type=button]):not([type=hidden]):not([type=checkbox])');
                            const sampleValues = ['Jane', 'Doe', '94103', 'qa.tester', 'QaSample1!'];
                            let idx = 0;
                            for (const inp of inputs) {
                                if (inp.offsetParent !== null) {
                                    inp.value = sampleValues[idx % sampleValues.length];
                                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                                    idx++;
                                }
                            }
                        }""")
                        sub_loc = page.locator("input[type='submit'], button[type='submit'], #continue, #login-button, button.btn_primary")
                        if await sub_loc.count() > 0 and await sub_loc.first.is_visible():
                            await sub_loc.first.click()
                        else:
                            await page.evaluate("""() => {
                                const f = document.querySelector('form');
                                if (f) {
                                    if (typeof f.requestSubmit === 'function') f.requestSubmit();
                                    else f.submit();
                                }
                            }""")
                        await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                    else:
                        resolved_sel = sel
                        if _is_bare_identifier(sel):
                            resolved_sel = f"#{sel}, input[name='{sel}'], [data-test='{sel}'], [name='{sel}']"
                        inp = page.locator(resolved_sel)
                        await inp.first.wait_for(state="visible", timeout=self.default_timeout_ms)
                        await inp.first.fill(s.input_value or "test_input")

                elif act in ("SELECT_OPTION", "SELECT"):
                    opt_val = "za"
                    if "opt_" in (s.target_state_id or ""):
                        opt_val = (s.target_state_id or "").split("opt_")[1].split("_")[0]
                    elif s.input_value:
                        opt_val = s.input_value
                    sel_elem = page.locator(sel)
                    await sel_elem.first.wait_for(state="visible", timeout=self.default_timeout_ms)
                    await sel_elem.first.select_option(opt_val)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)
                    except Exception:
                        pass

                else:
                    # Default: CLICK_CONTROL or CLICK
                    resolved_sel = sel
                    if _is_bare_identifier(sel):
                        resolved_sel = f"#{sel}, [data-test='{sel}'], button[name='{sel}'], button#{sel}, #{sel.replace('_', '-')}"
                    loc = page.locator(resolved_sel)
                    await loc.first.wait_for(state="visible", timeout=self.default_timeout_ms)
                    await loc.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=self.default_timeout_ms)

                # Step-level oracle check: verify transitions immediately when executed
                is_initial_entry_step = (s.step_number == 1 and act in ("NAVIGATE", "GOTO") and len(tc.steps) > 1)
                if not is_initial_entry_step:
                    step_evs = set(s.evidence_refs)
                    for idx, er in enumerate(tc.expected_results):
                        if idx in satisfied_oracles:
                            continue
                        matches_target_state = bool(
                        s.target_state_id
                        and (f"Reached state '{s.target_state_id}'" in er.description or s.target_state_id == er.expected_value)
                    )
                        matches_evidence = bool(step_evs and (set(er.evidence_refs) & step_evs))
                        if "Reached state '" in er.description:
                            if not matches_target_state:
                                continue
                        elif not matches_evidence:
                            continue

                        failure_res = await self._evaluate_single_oracle(
                            er=er,
                            page=page,
                            tc=tc,
                            tc_start=tc_start,
                            console_logs=console_logs,
                            assertions_eval=assertions_eval,
                            assertions_sat=assertions_sat,
                        )
                        if failure_res is not None:
                            return failure_res
                        satisfied_oracles.add(idx)
                        assertions_sat += 1

        except Exception as ex:
            shot_path = os.path.join(self.artifacts_dir, f"error_{tc.test_id}_{uuid.uuid4().hex[:6]}.png")
            try:
                await page.screenshot(path=shot_path)
            except Exception:
                shot_path = None
            return TestCaseExecutionResult(
                test_id=tc.test_id,
                title=tc.title,
                category=tc.category.value,
                status=TestExecutionStatus.EXECUTION_ERROR,
                duration_seconds=round(time.time() - tc_start, 3),
                source_evidence_ids=tc.evidence_ids,
                source_state_ids=tc.source_state_ids,
                source_transition_ids=tc.source_transition_ids,
                source_finding_ids=tc.source_finding_ids,
                failure_phase=FailurePhase.EXECUTION,
                error_message=f"Execution error on step {s.step_number} ({s.action_type}): {str(ex)}",
                screenshot_path=shot_path,
                console_logs=console_logs,
            )

        # -------------------------------------------------------------
        # Phase 3: Assertions & Expected Results (Remaining Oracles)
        # -------------------------------------------------------------
        for idx, er in enumerate(tc.expected_results):
            if idx in satisfied_oracles:
                continue
            failure_res = await self._evaluate_single_oracle(
                er=er,
                page=page,
                tc=tc,
                tc_start=tc_start,
                console_logs=console_logs,
                assertions_eval=assertions_eval,
                assertions_sat=assertions_sat,
            )
            if failure_res is not None:
                return failure_res
            satisfied_oracles.add(idx)
            assertions_sat += 1

        # Zero-assertion tests must never count as PASS
        if assertions_eval == 0:
            return TestCaseExecutionResult(
                test_id=tc.test_id,
                title=tc.title,
                category=tc.category.value,
                status=TestExecutionStatus.FAILED,
                duration_seconds=round(time.time() - tc_start, 3),
                source_evidence_ids=tc.evidence_ids,
                source_state_ids=tc.source_state_ids,
                source_transition_ids=tc.source_transition_ids,
                source_finding_ids=tc.source_finding_ids,
                console_logs=console_logs,
                failure_phase=FailurePhase.ASSERTION,
                error_message="Test completed with 0 evaluated assertions; zero-assertion tests cannot count as PASS.",
                assertions_evaluated=0,
                assertions_passed=0,
            )

        return TestCaseExecutionResult(
            test_id=tc.test_id,
            title=tc.title,
            category=tc.category.value,
            status=TestExecutionStatus.PASSED,
            duration_seconds=round(time.time() - tc_start, 3),
            source_evidence_ids=tc.evidence_ids,
            source_state_ids=tc.source_state_ids,
            source_transition_ids=tc.source_transition_ids,
            source_finding_ids=tc.source_finding_ids,
            console_logs=console_logs,
            assertions_evaluated=assertions_eval,
            assertions_passed=assertions_sat,
        )

    async def _evaluate_single_oracle(
        self,
        er: ExpectedResultSpec,
        page: Page,
        tc: GeneratedContractTestCase,
        tc_start: float,
        console_logs: List[str],
        assertions_eval: int,
        assertions_sat: int,
    ) -> Optional[TestCaseExecutionResult]:
        """Evaluates an oracle assertion. Returns a TestCaseExecutionResult if failed/defect, or None if satisfied."""
        v_type = er.verification_type
        exp_val = er.expected_value or ""

        if v_type == "DEFECT_ABSENT":
            # Check that defect anomaly 'NaN' is absent from visible DOM
            try:
                body_text = await page.locator("body").inner_text()
                tax_loc = page.locator("#tax-display")
                tax_text = (await tax_loc.inner_text()) if await tax_loc.count() > 0 else ""

                if "nan" in body_text.lower() or "nan" in tax_text.lower():
                    shot_path = os.path.join(self.artifacts_dir, f"defect_{tc.test_id}_{uuid.uuid4().hex[:6]}.png")
                    try:
                        await page.screenshot(path=shot_path)
                    except Exception:
                        shot_path = None
                    return TestCaseExecutionResult(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        status=TestExecutionStatus.DEFECT_DETECTED,
                        duration_seconds=round(time.time() - tc_start, 3),
                        source_evidence_ids=tc.evidence_ids,
                        source_state_ids=tc.source_state_ids,
                        source_transition_ids=tc.source_transition_ids,
                        source_finding_ids=tc.source_finding_ids,
                        failure_phase=FailurePhase.ASSERTION,
                        error_message=f"DEFECT_DETECTED: Confirmed defect reproduced: text anomaly 'NaN' found in output '{tax_text or body_text[:120]}'",
                        screenshot_path=shot_path,
                        console_logs=console_logs,
                        assertions_evaluated=assertions_eval,
                        assertions_passed=0,
                    )
            except Exception as ex:
                return TestCaseExecutionResult(
                    test_id=tc.test_id,
                    title=tc.title,
                    category=tc.category.value,
                    status=TestExecutionStatus.FAILED,
                    duration_seconds=round(time.time() - tc_start, 3),
                    source_evidence_ids=tc.evidence_ids,
                    source_state_ids=tc.source_state_ids,
                    source_transition_ids=tc.source_transition_ids,
                    source_finding_ids=tc.source_finding_ids,
                    failure_phase=FailurePhase.ASSERTION,
                    error_message=f"Defect oracle assertion error: {str(ex)}",
                    console_logs=console_logs,
                    assertions_evaluated=assertions_eval,
                    assertions_passed=assertions_sat,
                )

        elif v_type == "URL_CONTAINS":
            if "://" in exp_val:
                frag = urlparse(exp_val).path
            elif "/" in exp_val:
                frag = "/" + exp_val.split("/", 1)[-1]
            else:
                frag = exp_val

            parsed_curr = urlparse(page.url)
            curr_path = parsed_curr.path
            curr_full = page.url
            if frag not in curr_path and frag not in curr_full:
                return TestCaseExecutionResult(
                    test_id=tc.test_id,
                    title=tc.title,
                    category=tc.category.value,
                    status=TestExecutionStatus.FAILED,
                    duration_seconds=round(time.time() - tc_start, 3),
                    source_evidence_ids=tc.evidence_ids,
                    source_state_ids=tc.source_state_ids,
                    source_transition_ids=tc.source_transition_ids,
                    source_finding_ids=tc.source_finding_ids,
                    failure_phase=FailurePhase.ASSERTION,
                    error_message=f"URL assertion failed: Expected '{frag}' in URL '{page.url}'",
                    console_logs=console_logs,
                    assertions_evaluated=assertions_eval,
                    assertions_passed=assertions_sat,
                )

        elif v_type == "VALIDATION_ERROR_PRESENT":
            alert_elem = page.locator(".alert-danger, .alert-info, [role='alert'], [data-test='error'], [data-test='tax-display'], #tax-display, #checkout-error")
            has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()
            has_err_url = ("error=" in page.url) or ("show_tax=true" in page.url)
            if not (has_alert or has_err_url):
                return TestCaseExecutionResult(
                    test_id=tc.test_id,
                    title=tc.title,
                    category=tc.category.value,
                    status=TestExecutionStatus.FAILED,
                    duration_seconds=round(time.time() - tc_start, 3),
                    source_evidence_ids=tc.evidence_ids,
                    source_state_ids=tc.source_state_ids,
                    source_transition_ids=tc.source_transition_ids,
                    source_finding_ids=tc.source_finding_ids,
                    failure_phase=FailurePhase.ASSERTION,
                    error_message="Validation error assertion failed: Expected visible error alert or error query parameter",
                    console_logs=console_logs,
                    assertions_evaluated=assertions_eval,
                    assertions_passed=assertions_sat,
                )

        elif v_type in ("DOM_MUTATION_OBSERVED", "STATE_REACHED"):
            desc = er.description or ""
            body_text = await page.locator("body").inner_text()
            if not page.url or page.url.startswith("chrome-error://") or not body_text.strip():
                return TestCaseExecutionResult(
                    test_id=tc.test_id,
                    title=tc.title,
                    category=tc.category.value,
                    status=TestExecutionStatus.FAILED,
                    duration_seconds=round(time.time() - tc_start, 3),
                    source_evidence_ids=tc.evidence_ids,
                    source_state_ids=tc.source_state_ids,
                    source_transition_ids=tc.source_transition_ids,
                    source_finding_ids=tc.source_finding_ids,
                    failure_phase=FailurePhase.ASSERTION,
                    error_message=f"State reached assertion failed: invalid page URL '{page.url}' or empty DOM body",
                    console_logs=console_logs,
                    assertions_evaluated=assertions_eval,
                    assertions_passed=assertions_sat,
                )

            # Check concrete heading if present in description
            h_match = re.search(r"Heading '([^']+)' visible|heading:([^',\]\)]+)", desc)
            if h_match:
                expected_heading = (h_match.group(1) or h_match.group(2) or "").strip()
                if expected_heading:
                    if expected_heading.lower() not in body_text.lower():
                        await page.wait_for_timeout(500)
                        body_text = await page.locator("body").inner_text()
                    if expected_heading.lower() not in body_text.lower():
                        return TestCaseExecutionResult(
                            test_id=tc.test_id,
                            title=tc.title,
                            category=tc.category.value,
                            status=TestExecutionStatus.FAILED,
                            duration_seconds=round(time.time() - tc_start, 3),
                            source_evidence_ids=tc.evidence_ids,
                            source_state_ids=tc.source_state_ids,
                            source_transition_ids=tc.source_transition_ids,
                            source_finding_ids=tc.source_finding_ids,
                            failure_phase=FailurePhase.ASSERTION,
                            error_message=f"Heading assertion failed: Expected heading '{expected_heading}' in visible DOM",
                            console_logs=console_logs,
                            assertions_evaluated=assertions_eval,
                            assertions_passed=assertions_sat,
                        )

            # Check concrete option if present in description
            opt_match = re.search(r"Option '([^']+)' active|selected_option:([^',\]\)]+)", desc)
            if opt_match:
                expected_opt = (opt_match.group(1) or opt_match.group(2) or "").strip()
                if expected_opt:
                    sel_loc = page.locator("select, #product-sort, [data-test='product-sort-container']")
                    opt_ok = (f"sort={expected_opt}" in page.url) or (
                        await sel_loc.count() > 0 and await sel_loc.first.input_value() == expected_opt
                    )
                    if not opt_ok:
                        return TestCaseExecutionResult(
                            test_id=tc.test_id,
                            title=tc.title,
                            category=tc.category.value,
                            status=TestExecutionStatus.FAILED,
                            duration_seconds=round(time.time() - tc_start, 3),
                            source_evidence_ids=tc.evidence_ids,
                            source_state_ids=tc.source_state_ids,
                            source_transition_ids=tc.source_transition_ids,
                            source_finding_ids=tc.source_finding_ids,
                            failure_phase=FailurePhase.ASSERTION,
                            error_message=f"Option assertion failed: Expected active option '{expected_opt}' in URL or select control",
                            console_logs=console_logs,
                            assertions_evaluated=assertions_eval,
                            assertions_passed=assertions_sat,
                        )

            # Check concrete drawer/menu open state if present
            if "drawer/menu opened" in desc or "menu_open:true" in desc or "menu=open" in desc:
                drawer_loc = page.locator("#filter-drawer.menu-open, .bm-menu-wrap[aria-hidden='false'], #react-burger-cross-btn, #inventory_sidebar_link")
                drawer_ok = ("menu=open" in page.url) or (await drawer_loc.count() > 0 and await drawer_loc.first.is_visible())
                if not drawer_ok:
                    return TestCaseExecutionResult(
                        test_id=tc.test_id,
                        title=tc.title,
                        category=tc.category.value,
                        status=TestExecutionStatus.FAILED,
                        duration_seconds=round(time.time() - tc_start, 3),
                        source_evidence_ids=tc.evidence_ids,
                        source_state_ids=tc.source_state_ids,
                        source_transition_ids=tc.source_transition_ids,
                        source_finding_ids=tc.source_finding_ids,
                        failure_phase=FailurePhase.ASSERTION,
                        error_message="Drawer assertion failed: Expected in-page drawer/menu to be open and visible",
                        console_logs=console_logs,
                        assertions_evaluated=assertions_eval,
                        assertions_passed=assertions_sat,
                    )

        return None
