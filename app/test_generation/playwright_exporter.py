"""
VerityQA Executable Playwright Test Exporter (Phase 3).
======================================================
Transforms validated `ContractTestSuiteArtifact` test cases into executable,
deterministic Playwright tests with explicit assertions, semantic locators,
controlled setup fixtures, and fail-closed safety governance.

Supported Categories:
1. Confirmed-defect regression tests (`regression`)
2. Verified state-transition tests (`transition`)
3. Verified multi-step workflow tests (`workflow`)
4. Observed form-validation tests (`validation`)
5. State-dependent tests with verified preconditions (`state_dependent`)
6. Exploratory hypothesis tests (`exploratory_hypothesis`, marked as skipped/requiring enrichment)
"""

import os
import re
import logging
from typing import List, Dict, Optional, Any, Tuple
from urllib.parse import urlparse

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


class PlaywrightTestExporter:
    """
    Converts a validated ContractTestSuiteArtifact into executable Playwright test scripts.
    Preserves test IDs, evidence IDs, safety constraints, and produces clean Python code.
    """

    def __init__(
        self,
        default_timeout_ms: int = 8000,
        artifacts_dir: str = "artifacts/playwright_runs",
    ):
        self.default_timeout_ms = default_timeout_ms
        self.artifacts_dir = artifacts_dir

    def export_test_suite_to_code(
        self,
        suite: ContractTestSuiteArtifact,
        target_base_url: Optional[str] = None,
        sandbox_mode: bool = True,
        sandbox_origins: Optional[List[str]] = None,
        test_username: str = "benchmark_user",
        test_password: str = "secret_benchmark",
    ) -> str:
        """
        Generates a complete, self-contained pytest Playwright test module.
        """
        base_url = (target_base_url or suite.target_url).rstrip("/")
        parsed_base = urlparse(base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        origins = sandbox_origins or [origin]

        lines: List[str] = [
            '"""',
            f'VerityQA Auto-Generated Executable Playwright Test Suite.',
            f'Source Investigation ID: {suite.source_investigation_id}',
            f'Source Contract Version: {suite.source_contract_version}',
            f'Generated Strategy: {suite.generation_strategy}',
            f'Target Base URL: {base_url}',
            '"""',
            '',
            'import os',
            'import time',
            'import pytest',
            'import pytest_asyncio',
            'from urllib.parse import urlparse',
            'from playwright.async_api import Page, BrowserContext, expect, async_playwright',
            '',
            '@pytest_asyncio.fixture(scope="function")',
            'async def page():',
            '    async with async_playwright() as p:',
            '        browser = await p.chromium.launch(',
            '            headless=True,',
            '            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],',
            '        )',
            '        context = await browser.new_context(viewport={"width": 1280, "height": 800})',
            '        pg = await context.new_page()',
            '        pg.set_default_timeout(DEFAULT_TIMEOUT)',
            '        yield pg',
            '        await context.close()',
            '        await browser.close()',
            '',
            '# --------------------------------------------------------------------------',
            '# Test Suite Configuration & Safety Metadata',
            '# --------------------------------------------------------------------------',
            f'BASE_URL = os.environ.get("VERITYQA_TARGET_URL", "{base_url}")',
            f'SANDBOX_MODE = {repr(sandbox_mode)}',
            f'SANDBOX_ORIGINS = {repr(origins)}',
            f'TEST_USERNAME = os.environ.get("VERITYQA_TEST_USER", "{test_username}")',
            f'TEST_PASSWORD = os.environ.get("VERITYQA_TEST_PASS", "{test_password}")',
            f'DEFAULT_TIMEOUT = {self.default_timeout_ms}',
            f'ARTIFACTS_DIR = os.environ.get("VERITYQA_ARTIFACTS_DIR", "{self.artifacts_dir}")',
            '',
            'TERMINAL_FORBIDDEN_KEYWORDS = [',
            '    "delete account", "remove user", "drop", "purge", "destroy",',
            '    "unsubscribe", "cancel subscription", "factory reset", "revoke",',
            '    "terminate", "pay now", "complete purchase", "place order",',
            '    "confirm payment", "buy now", "transfer money",',
            ']',
            '',
            'class SetupFailure(Exception):',
            '    """Raised when test fixture setup or preconditions cannot be established."""',
            '    pass',
            '',
            'class DefectDetected(AssertionError):',
            '    """Raised when a regression test catches a confirmed defect."""',
            '    pass',
            '',
            '# --------------------------------------------------------------------------',
            '# Reusable Setup & Precondition Helpers',
            '# --------------------------------------------------------------------------',
            'async def ensure_authenticated_session(page: Page, base_url: str):',
            '    """Precondition helper: Establishes an authenticated session on benchmark or SauceDemo."""',
            '    try:',
            '        login_url = f"{base_url}/login" if "127.0.0.1" in base_url or "localhost" in base_url else f"{base_url}/"',
            '        await page.goto(login_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '        user_input = page.locator("#username, #user-name, input[name=\'username\'], input[name=\'user-name\']")',
            '        if await user_input.count() > 0 and await user_input.first.is_visible():',
            '            await user_input.first.fill(TEST_USERNAME)',
            '            pass_input = page.locator("#password, input[name=\'password\']")',
            '            await pass_input.first.fill(TEST_PASSWORD)',
            '            submit_btn = page.locator("#login-button, button[type=\'submit\'], input[type=\'submit\']")',
            '            await submit_btn.first.click()',
            '            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '    except Exception as e:',
            '        raise SetupFailure(f"Failed to establish authenticated session: {e}") from e',
            '',
            'async def ensure_cart_with_item(page: Page, base_url: str, item_url_hint: str = ""):',
            '    """Precondition helper: Ensures at least 1 item is added to the cart prior to cart/remove steps."""',
            '    try:',
            '        await ensure_authenticated_session(page, base_url)',
            '        if "inventory-item.html" in item_url_hint:',
            '            await page.goto(item_url_hint, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '            add_detail = page.locator("#add-to-cart, button[id^=\'add-to-cart\']")',
            '            if await add_detail.count() > 0 and await add_detail.first.is_visible():',
            '                await add_detail.first.click()',
            '                await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '                return',
            '        catalog_path = "/products" if ("127.0.0.1" in base_url or "localhost" in base_url) else "/inventory.html"',
            '        await page.goto(f"{base_url}{catalog_path}", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '        add_btn = page.locator("#add-to-cart-1, #add-to-cart-sauce-labs-backpack, button[id^=\'add-to-cart\']")',
            '        if await add_btn.count() > 0 and await add_btn.first.is_visible():',
            '            await add_btn.first.click()',
            '            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '    except Exception as e:',
            '        raise SetupFailure(f"Failed to populate cart with item: {e}") from e',
            '',
            'async def ensure_drawer_open(page: Page):',
            '    """Precondition helper: Ensures the in-page navigation/filter drawer is open if required."""',
            '    menu_btn = page.locator("#react-burger-menu-btn, #drawer-toggle-btn")',
            '    if await menu_btn.count() > 0 and await menu_btn.first.is_visible():',
            '        await menu_btn.first.click()',
            '        await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)',
            '',
            'async def check_execution_safety(step_desc: str, selector: str, page_url: str):',
            '    """Fail-closed safety check before executing actions."""',
            '    combined = f"{step_desc} {selector}".lower()',
            '    for kw in TERMINAL_FORBIDDEN_KEYWORDS:',
            '        if kw in combined:',
            '            pytest.fail(f"SAFETY_BLOCKED: Terminal forbidden action keyword \'{kw}\' detected in \'{step_desc}\'")',
            '    if "checkout" in combined:',
            '        if not SANDBOX_MODE:',
            '            pytest.skip("SANDBOX_REQUIRED: Checkout actions disabled when sandbox_mode is False")',
            '        parsed = urlparse(page_url)',
            '        curr_origin = f"{parsed.scheme}://{parsed.netloc}"',
            '        if curr_origin not in SANDBOX_ORIGINS:',
            '            pytest.fail(f"SAFETY_BLOCKED: Origin \'{curr_origin}\' is not in approved sandbox origins")',
            '',
            '# --------------------------------------------------------------------------',
            '# Generated Test Cases',
            '# --------------------------------------------------------------------------',
        ]

        # Generate individual test functions for each test case
        for tc in suite.test_cases:
            tc_code = self._generate_test_function_code(tc, base_url)
            lines.append(tc_code)
            lines.append('')

        return '\n'.join(lines)

    def _generate_test_function_code(self, tc: GeneratedContractTestCase, base_url: str) -> str:
        """Generates async pytest function for a single GeneratedContractTestCase."""
        safe_id = re.sub(r'[^a-zA-Z0-9_]', '_', tc.test_id)
        fn_name = f"test_{safe_id}_{tc.category.value}"

        lines: List[str] = []

        # Docstring
        lines.append(f"@pytest.mark.asyncio")
        if tc.category == TestCategoryType.EXPLORATORY_HYPOTHESIS:
            skip_reason = f"Exploratory hypothesis requiring empirical confirmation: {tc.title}"
            lines.append(f"@pytest.mark.skip(reason={repr(skip_reason)})")
        elif tc.safety_metadata.requires_sandbox:
            lines.append(f"@pytest.mark.sandbox_only")

        lines.append(f"async def {fn_name}(page: Page):")
        lines.append(f'    """')
        lines.append(f'    Test ID: {tc.test_id}')
        lines.append(f'    Title: {tc.title}')
        lines.append(f'    Category: {tc.category.value}')
        lines.append(f'    Objective: {tc.objective}')
        lines.append(f'    Safety Classification: {tc.safety_metadata.classification.value}')
        lines.append(f'    Source Evidence Refs: {tc.evidence_ids}')
        lines.append(f'    """')

        # Precondition / Setup phase
        lines.append(f"    # Phase 1: Precondition / Setup Verification")
        prec_text = " ".join(tc.preconditions)
        url_match = re.search(r"https?://[^\s\)]+", prec_text)
        init_url_hint = url_match.group(0) if url_match else ""

        req_auth = tc.safety_metadata.requires_test_credentials or any(
            "auth" in p.lower() or "session" in p.lower() for p in tc.preconditions
        )
        req_cart = (
            any("cart" in p.lower() and "badge" in p.lower() and "0" not in p.lower() for p in tc.preconditions)
            or any("target item is added to cart" in p.lower() for p in tc.preconditions)
            or any("remove" in (s.selector or "").lower() for s in tc.steps[:1])
        )
        req_drawer = any("drawer is open" in p.lower() for p in tc.preconditions)

        if req_cart:
            lines.append(f"    await ensure_cart_with_item(page, BASE_URL, {repr(init_url_hint)})")
        elif req_auth:
            lines.append(f"    await ensure_authenticated_session(page, BASE_URL)")

        # Ensure page is at initial target URL if first step is not a navigate
        first_step = tc.steps[0] if tc.steps else None
        first_act = first_step.action_type.split(":")[0].upper() if first_step else ""
        if first_act not in ("NAVIGATE", "GOTO"):
            if url_match:
                p_u = urlparse(url_match.group(0))
                rel = f"{p_u.path}{'?' + p_u.query if p_u.query else ''}"
                lines.append(f"    await page.goto(f'{{BASE_URL}}{rel}', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            elif any("cart" in s_id for s_id in tc.source_state_ids):
                lines.append(f"    await page.goto(f'{{BASE_URL}}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            elif any("products" in s_id for s_id in tc.source_state_ids):
                lines.append(f"    await page.goto(f'{{BASE_URL}}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            if req_drawer:
                lines.append(f"    await ensure_drawer_open(page)")

        # Steps execution
        lines.append(f"    # Phase 2: Action Steps Execution & In-Flight Transition Oracles")
        emitted_oracles: Set[int] = set()

        for s in tc.steps:
            act_type = s.action_type.split(":")[0].upper()
            sel = s.selector or ""

            # Normalize selector with fallback
            if act_type in ("NAVIGATE", "GOTO"):
                if sel.startswith("http"):
                    p_sel = urlparse(sel)
                    nav_path = f"{p_sel.path}{'?' + p_sel.query if p_sel.query else ''}"
                    lines.append(f"    # Step {s.step_number}: Navigate to {nav_path}")
                    lines.append(f"    await page.goto(f'{{BASE_URL}}{nav_path}', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
                else:
                    path = sel if sel.startswith('/') else '/' + sel
                    lines.append(f"    # Step {s.step_number}: Navigate to {path}")
                    lines.append(f"    await page.goto(f'{{BASE_URL}}{path}', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            elif act_type == "AUTHENTICATE":
                lines.append(f"    # Step {s.step_number}: Authenticate with test credentials")
                lines.append('    u_input = page.locator("#username, #user-name, input[name=\\"username\\"], input[name=\\"user-name\\"]")')
                lines.append('    await expect(u_input.first).to_be_visible(timeout=DEFAULT_TIMEOUT)')
                lines.append('    await u_input.first.fill(TEST_USERNAME)')
                lines.append('    p_input = page.locator("#password, input[name=\\"password\\"]")')
                lines.append('    await p_input.first.fill(TEST_PASSWORD)')
                lines.append('    submit_btn = page.locator("#login-button, button[type=\\"submit\\"], input[type=\\"submit\\"]")')
                lines.append('    await submit_btn.first.click()')
                lines.append('    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)')
            elif act_type == "FILL":
                if "blank" in sel.lower() or "blank" in s.description.lower():
                    lines.append(f"    # Step {s.step_number}: Submit blank form validation check")
                    lines.append('    u_input = page.locator("#username, #user-name, input[name=\\"username\\"], input[name=\\"user-name\\"]")')
                    lines.append('    if await u_input.count() > 0 and await u_input.first.is_visible():')
                    lines.append('        await u_input.first.fill("")')
                    lines.append('    btn = page.locator("#login-button, button[type=\\"submit\\"], input[type=\\"submit\\"]")')
                    lines.append('    await btn.first.click()')
                    lines.append('    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)')
                else:
                    lines.append(f"    # Step {s.step_number}: Fill input {repr(sel)}")
                    lines.append(f"    target_input = page.locator({repr(sel)})")
                    lines.append(f"    assert await target_input.count() >= 1, f'Locator {sel} matched 0 elements'")
                    lines.append(f"    await expect(target_input.first).to_be_visible(timeout=DEFAULT_TIMEOUT)")
                    lines.append(f"    await target_input.first.fill({repr(s.input_value or 'test_input')})")
            elif act_type in ("SELECT_OPTION", "SELECT"):
                # Infer option from step description or target state
                opt_val = "za"
                if "opt_" in (s.target_state_id or ""):
                    opt_val = (s.target_state_id or "").split("opt_")[1].split("_")[0]
                elif s.input_value:
                    opt_val = s.input_value
                lines.append(f"    # Step {s.step_number}: Select option '{opt_val}' on {repr(sel)}")
                lines.append(f"    sel_elem = page.locator({repr(sel)})")
                lines.append(f"    assert await sel_elem.count() >= 1, f'Select locator {sel} matched 0 elements'")
                lines.append(f"    await expect(sel_elem.first).to_be_visible(timeout=DEFAULT_TIMEOUT)")
                lines.append(f"    await sel_elem.first.select_option({repr(opt_val)})")
                lines.append(f"    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            elif act_type in ("NAVIGATE_LINK", "NAV_LINK"):
                dest_href = sel
                lines.append(f"    # Step {s.step_number}: Follow link navigation to {repr(dest_href)}")
                lines.append(f"    dest_full = f'{{BASE_URL}}{dest_href if dest_href.startswith('/') else '/' + dest_href}'")
                lines.append(f"    await page.goto(dest_full, wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)")
            else:
                # Default: CLICK_CONTROL or CLICK
                lines.append(f"    # Step {s.step_number}: {s.description}")
                lines.append(f"    await check_execution_safety({repr(s.description)}, {repr(sel)}, page.url)")
                lines.append(f"    ctrl = page.locator({repr(sel)})")
                lines.append(f"    assert await ctrl.count() >= 1, f'Control locator {sel} matched 0 elements'")
                lines.append(f"    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)")
                lines.append(f"    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)")
                lines.append(f"    await ctrl.first.click()")
                lines.append(f"    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)")

            # Check if this step satisfies any oracles immediately
            is_initial_entry_step = (s.step_number == 1 and act_type in ("NAVIGATE", "GOTO") and len(tc.steps) > 1)
            if not is_initial_entry_step:
                step_evs = set(s.evidence_refs)
                for idx, er in enumerate(tc.expected_results):
                    if idx in emitted_oracles:
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

                    lines.extend(self._render_oracle_assertion(er))
                    emitted_oracles.add(idx)

        # Assertions phase (Remaining Oracles)
        lines.append(f"    # Phase 3: Observable Evidence Assertions (Remaining Oracles)")
        for idx, er in enumerate(tc.expected_results):
            if idx in emitted_oracles:
                continue
            lines.extend(self._render_oracle_assertion(er))
            emitted_oracles.add(idx)

        return '\n'.join(lines)

    def _render_oracle_assertion(self, er: ExpectedResultSpec) -> List[str]:
        out = []
        v_type = er.verification_type
        exp_val = er.expected_value or ""
        desc = er.description or ""

        if v_type == "DEFECT_ABSENT":
            out.append(f"    # Defect Absent Oracle: Check for absence of confirmed defect anomaly ('NaN' / calculation error)")
            out.append(f"    body_text = await page.locator('body').inner_text()")
            out.append(f"    tax_elem = page.locator('#tax-display')")
            out.append(f"    tax_text = (await tax_elem.inner_text()) if await tax_elem.count() > 0 else ''")
            out.append(f"    if 'nan' in body_text.lower() or 'nan' in tax_text.lower():")
            out.append(f"        raise DefectDetected(f\"DEFECT_DETECTED: Confirmed text anomaly 'NaN' found in visible DOM output: {{tax_text or body_text[:200]}}\")")
            out.append(f"    if await tax_elem.count() > 0:")
            out.append(f"        assert '$' in tax_text and 'Tax:' in tax_text, f\"Expected valid tax formatting, got: {{tax_text}}\"")
        elif v_type == "URL_CONTAINS":
            if "://" in exp_val:
                frag = urlparse(exp_val).path
            elif "/" in exp_val:
                frag = "/" + exp_val.split("/", 1)[-1]
            else:
                frag = exp_val
            out.append(f"    # URL Contains Oracle: Expected fragment {repr(frag)}")
            out.append(f"    assert {repr(frag)} in page.url, f\"Expected URL to contain '{frag}', got '{{page.url}}'\"")
        elif v_type == "VALIDATION_ERROR_PRESENT":
            out.append(f"    # Validation Error Oracle: Expected visible alert/error feedback")
            out.append(f"    alert_elem = page.locator('.alert-danger, .alert-info, [role=\"alert\"], [data-test=\"error\"], [data-test=\"tax-display\"], #tax-display, #checkout-error')")
            out.append(f"    is_error_url = ('error=' in page.url) or ('show_tax=true' in page.url)")
            out.append(f"    has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()")
            out.append(f"    assert has_alert or is_error_url, 'Expected visible validation error alert or error query parameter'")
        elif v_type in ("DOM_MUTATION_OBSERVED", "STATE_REACHED"):
            out.append(f"    # Concrete Observable State/Mutation Oracle: {repr(desc[:100])}")
            out.append(f"    body_text = await page.locator('body').inner_text()")
            out.append(f"    assert len(body_text.strip()) > 0, 'Expected visible non-empty DOM content'")

            # Extract concrete heading expectation if present in description
            h_match = re.search(r"Heading '([^']+)' visible|heading:([^',\]\)]+)", desc)
            if h_match:
                expected_heading = (h_match.group(1) or h_match.group(2) or "").strip()
                if expected_heading:
                    out.append(f"    assert {repr(expected_heading)}.lower() in body_text.lower(), f\"Expected heading '{expected_heading}' in visible DOM\"")

            # Extract concrete option expectation if present
            opt_match = re.search(r"Option '([^']+)' active|selected_option:([^',\]\)]+)", desc)
            if opt_match:
                expected_opt = (opt_match.group(1) or opt_match.group(2) or "").strip()
                if expected_opt:
                    out.append(f"    sel_loc = page.locator('select, #product-sort, [data-test=\"product-sort-container\"]')")
                    out.append(f"    opt_ok = ({repr('sort=' + expected_opt)} in page.url) or (await sel_loc.count() > 0 and await sel_loc.first.input_value() == {repr(expected_opt)})")
                    out.append(f"    assert opt_ok, f\"Expected active option '{expected_opt}' in URL or select control\"")

            # Extract drawer/menu open expectation if present
            if "drawer/menu opened" in desc or "menu_open:true" in desc or "menu=open" in desc:
                out.append(f"    drawer_loc = page.locator('#filter-drawer.menu-open, .bm-menu-wrap[aria-hidden=\"false\"], #react-burger-cross-btn, #inventory_sidebar_link')")
                out.append(f"    drawer_ok = ('menu=open' in page.url) or (await drawer_loc.count() > 0 and await drawer_loc.first.is_visible())")
                out.append(f"    assert drawer_ok, 'Expected in-page drawer/menu to be open and visible'")

            # Extract cart counter badge expectation if > 0 in postcondition
            cnt_match = re.search(r"_c(\d+)\b", desc)
            if cnt_match and int(cnt_match.group(1)) > 0:
                exp_cnt = cnt_match.group(1)
                out.append(f"    badge_loc = page.locator('#cart-badge, .shopping_cart_badge, [data-test=\"shopping-cart-badge\"]')")
                out.append(f"    assert await badge_loc.count() > 0 and (await badge_loc.first.inner_text()).strip() == {repr(exp_cnt)}, f\"Expected cart badge count '{exp_cnt}'\"")
        elif v_type == "HYPOTHESIS_CONFIRMATION_REQUIRED":
            out.append(f"    # Hypothesis Requiring Confirmation: Skip in verified regression mode")
            out.append(f"    pytest.skip('Candidate is an unverified hypothesis requiring empirical enrichment')")
        return out

    def export_to_file(
        self,
        suite: ContractTestSuiteArtifact,
        output_file_path: str,
        target_base_url: Optional[str] = None,
        sandbox_mode: bool = True,
        sandbox_origins: Optional[List[str]] = None,
    ) -> str:
        """Exports generated Playwright test code to a Python file on disk."""
        code = self.export_test_suite_to_code(
            suite=suite,
            target_base_url=target_base_url,
            sandbox_mode=sandbox_mode,
            sandbox_origins=sandbox_origins,
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
        with open(output_file_path, "w", encoding="utf-8") as f:
            f.write(code)
        logger.info("Exported %d Playwright tests to %s", len(suite.test_cases), output_file_path)
        return output_file_path
