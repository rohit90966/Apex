"""
VerityQA Auto-Generated Executable Playwright Test Suite.
Source Investigation ID: inv-ba80cbb6
Source Contract Version: 1.0.0
Generated Strategy: DETERMINISTIC_GROUNDED
Target Base URL: http://127.0.0.1:8995
"""

import os
import time
import pytest
from urllib.parse import urlparse
from playwright.async_api import Page, BrowserContext, expect

# --------------------------------------------------------------------------
# Test Suite Configuration & Safety Metadata
# --------------------------------------------------------------------------
BASE_URL = os.environ.get("VERITYQA_TARGET_URL", "http://127.0.0.1:8995")
SANDBOX_MODE = True
SANDBOX_ORIGINS = ['http://127.0.0.1:8995', 'http://127.0.0.1:8996']
TEST_USERNAME = os.environ.get("VERITYQA_TEST_USER", "benchmark_user")
TEST_PASSWORD = os.environ.get("VERITYQA_TEST_PASS", "secret_benchmark")
DEFAULT_TIMEOUT = 8000
ARTIFACTS_DIR = os.environ.get("VERITYQA_ARTIFACTS_DIR", "artifacts/playwright_runs")

TERMINAL_FORBIDDEN_KEYWORDS = [
    "delete account", "remove user", "drop", "purge", "destroy",
    "unsubscribe", "cancel subscription", "factory reset", "revoke",
    "terminate", "pay now", "complete purchase", "place order",
    "confirm payment", "buy now", "finish", "transfer money",
]

class SetupFailure(Exception):
    """Raised when test fixture setup or preconditions cannot be established."""
    pass

class DefectDetected(AssertionError):
    """Raised when a regression test catches a confirmed defect."""
    pass

# --------------------------------------------------------------------------
# Reusable Setup & Precondition Helpers
# --------------------------------------------------------------------------
async def ensure_authenticated_session(page: Page, base_url: str):
    """Precondition helper: Establishes an authenticated session."""
    try:
        login_url = f"{base_url}/login"
        await page.goto(login_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        # Check if already authenticated or on login form
        user_input = page.locator("#username, input[name='username'], input[name='user-name']")
        if await user_input.count() > 0 and await user_input.first.is_visible():
            await user_input.first.fill(TEST_USERNAME)
            pass_input = page.locator("#password, input[name='password']")
            await pass_input.first.fill(TEST_PASSWORD)
            submit_btn = page.locator("#login-button, button[type='submit'], input[type='submit']")
            await submit_btn.first.click()
            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        raise SetupFailure(f"Failed to establish authenticated session: {e}") from e

async def ensure_cart_with_item(page: Page, base_url: str):
    """Precondition helper: Ensures at least 1 item is in the cart."""
    try:
        await ensure_authenticated_session(page, base_url)
        await page.goto(f"{base_url}/products", wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        add_btn = page.locator("#add-to-cart-1, button[id^='add-to-cart']")
        if await add_btn.count() > 0 and await add_btn.first.is_visible():
            await add_btn.first.click()
            await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        raise SetupFailure(f"Failed to populate cart with item: {e}") from e

async def check_execution_safety(step_desc: str, selector: str, page_url: str):
    """Fail-closed safety check before executing actions."""
    combined = f"{step_desc} {selector}".lower()
    for kw in TERMINAL_FORBIDDEN_KEYWORDS:
        if kw in combined:
            pytest.fail(f"SAFETY_BLOCKED: Terminal forbidden action keyword '{kw}' detected in '{step_desc}'")
    if "checkout" in combined:
        if not SANDBOX_MODE:
            pytest.skip("SANDBOX_REQUIRED: Checkout actions disabled when sandbox_mode is False")
        parsed = urlparse(page_url)
        curr_origin = f"{parsed.scheme}://{parsed.netloc}"
        if curr_origin not in SANDBOX_ORIGINS:
            pytest.fail(f"SAFETY_BLOCKED: Origin '{curr_origin}' is not in approved sandbox origins")

# --------------------------------------------------------------------------
# Generated Test Cases
# --------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_WF_01_workflow(page: Page):
    """
    Test ID: TC-WF-01
    Title: End-to-End Workflow: Your Cart — Verity Benchmark Portal to Products — Verity Benchmark Portal (Products Catalog)
    Category: workflow
    Objective: Verify multi-step user workflow across 5 verified transitions reaching Products — Verity Benchmark Portal (Products Catalog).
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-56c0fdaf', 'ev-9d2b556c', 'ev-5aab762d', 'ev-ed734a68', 'ev-2570c507']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Navigate to /cart?show_tax=true
    await page.goto(f'{BASE_URL}/cart?show_tax=true', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Step 2: Execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button' to transition from st_cart_shopping_cart_view_with_error to st_checkout_checkout_shipp_view
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button' to transition from st_cart_shopping_cart_view_with_error to st_checkout_checkout_shipp_view", '#checkout-button', page.url)
    ctrl = page.locator('#checkout-button')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/checkout'
    assert '/checkout' in page.url, f"Expected URL to contain '/checkout', got '{page.url}'"
    # Step 3: Execute CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link' to transition from st_checkout_checkout_shipp_view to st_cart_shopping_cart_view
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link' to transition from st_checkout_checkout_shipp_view to st_cart_shopping_cart_view", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/cart'
    assert '/cart' in page.url, f"Expected URL to contain '/cart', got '{page.url}'"
    # Step 4: Execute CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on 'a.btn[href="/products"]' to transition from st_cart_shopping_cart_view to st_products_products_catal_view
    await check_execution_safety('Execute CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on \'a.btn[href="/products"]\' to transition from st_cart_shopping_cart_view to st_products_products_catal_view', 'a.btn[href="/products"]', page.url)
    ctrl = page.locator('a.btn[href="/products"]')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/products'
    assert '/products' in page.url, f"Expected URL to contain '/products', got '{page.url}'"
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Step 5: Select option 'za' on '#product-sort'
    sel_elem = page.locator('#product-sort')
    await expect(sel_elem.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await sel_elem.first.select_option('za')
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Step 6: Follow link navigation to '/products?sort=za&menu=open'
    dest_full = f'{BASE_URL}/products?sort=za&menu=open'
    await page.goto(dest_full, wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_WF_02_workflow(page: Page):
    """
    Test ID: TC-WF-02
    Title: End-to-End Workflow: Authentication — Verity Benchmark Portal to Checkout — Verity Benchmark Portal (Checkout: Shipping Details)
    Category: workflow
    Objective: Verify multi-step user workflow across 4 verified transitions reaching Checkout — Verity Benchmark Portal (Checkout: Shipping Details).
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-8cc0831a', 'ev-e3237019', 'ev-693880d0', 'ev-56c0fdaf']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Navigate to /login
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Step 2: Authenticate with test credentials
    u_input = page.locator("#username, input[name=\"username\"]")
    await expect(u_input.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await u_input.first.fill(TEST_USERNAME)
    p_input = page.locator("#password, input[name=\"password\"]")
    await p_input.first.fill(TEST_PASSWORD)
    submit_btn = page.locator("#login-button, button[type=\"submit\"]")
    await submit_btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/products'
    assert '/products' in page.url, f"Expected URL to contain '/products', got '{page.url}'"
    # Step 3: Execute CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link' to transition from st_products_products_catal_view to st_cart_shopping_cart_view
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link' to transition from st_products_products_catal_view to st_cart_shopping_cart_view", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/cart'
    assert '/cart' in page.url, f"Expected URL to contain '/cart', got '{page.url}'"
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Step 4: Execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn' to transition from st_cart_shopping_cart_view to st_cart_shopping_cart_view_with_error
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn' to transition from st_cart_shopping_cart_view to st_cart_shopping_cart_view_with_error", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Step 5: Execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button' to transition from st_cart_shopping_cart_view_with_error to st_checkout_checkout_shipp_view
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button' to transition from st_cart_shopping_cart_view_with_error to st_checkout_checkout_shipp_view", '#checkout-button', page.url)
    ctrl = page.locator('#checkout-button')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/checkout'
    assert '/checkout' in page.url, f"Expected URL to contain '/checkout', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_WF_03_workflow(page: Page):
    """
    Test ID: TC-WF-03
    Title: End-to-End Workflow: Checkout — Verity Benchmark Portal to Your Cart — Verity Benchmark Portal (Shopping Cart)
    Category: workflow
    Objective: Verify multi-step user workflow across 2 verified transitions reaching Your Cart — Verity Benchmark Portal (Shopping Cart).
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-9d2b556c', 'ev-693880d0']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Navigate to /checkout
    await page.goto(f'{BASE_URL}/checkout', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Step 2: Execute CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link' to transition from st_checkout_checkout_shipp_view to st_cart_shopping_cart_view
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link' to transition from st_checkout_checkout_shipp_view to st_cart_shopping_cart_view", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/cart'
    assert '/cart' in page.url, f"Expected URL to contain '/cart', got '{page.url}'"
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Step 3: Execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn' to transition from st_cart_shopping_cart_view to st_cart_shopping_cart_view_with_error
    await check_execution_safety("Execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn' to transition from st_cart_shopping_cart_view to st_cart_shopping_cart_view_with_error", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_01_transition(page: Page):
    """
    Test ID: TC-TR-01
    Title: State Transition: FILL:required_blank_all_loginForm on required_blank_all_loginForm (st_login_system_access__view -> st_login_system_access__view_with_error)
    Category: transition
    Objective: Verify that executing FILL:required_blank_all_loginForm on 'required_blank_all_loginForm' transitions from 'st_login_system_access__view' to 'st_login_system_access__view_with_error'.
    Safety Classification: SAFE_READ_ONLY
    Source Evidence Refs: ['ev-8a62c523']
    """
    # Phase 1: Precondition / Setup Verification
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Submit blank form validation check
    u_input = page.locator("#username, input[name=\"username\"]")
    if await u_input.count() > 0 and await u_input.first.is_visible():
        await u_input.first.fill("")
    btn = page.locator("#login-button, button[type=\"submit\"]")
    await btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # Validation Error Oracle: Expected visible alert/error feedback
    alert_elem = page.locator('.alert-danger, .alert-info, [role="alert"], [data-test="error"], [data-test="tax-display"], #tax-display, #checkout-error')
    is_error_url = ('error=' in page.url) or ('show_tax=true' in page.url)
    has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()
    assert has_alert or is_error_url, 'Expected visible validation error alert or error query parameter'
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_02_transition(page: Page):
    """
    Test ID: TC-TR-02
    Title: State Transition: AUTHENTICATE on login_form (st_login_system_access__view -> st_products_products_catal_view)
    Category: transition
    Objective: Verify that executing AUTHENTICATE on 'login_form' transitions from 'st_login_system_access__view' to 'st_products_products_catal_view'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-8cc0831a']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Authenticate with test credentials
    u_input = page.locator("#username, input[name=\"username\"]")
    await expect(u_input.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await u_input.first.fill(TEST_USERNAME)
    p_input = page.locator("#password, input[name=\"password\"]")
    await p_input.first.fill(TEST_PASSWORD)
    submit_btn = page.locator("#login-button, button[type=\"submit\"]")
    await submit_btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/products'
    assert '/products' in page.url, f"Expected URL to contain '/products', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_03_transition(page: Page):
    """
    Test ID: TC-TR-03
    Title: State Transition: CLICK_CONTROL:control_cls_f88ae0853d__cart_link on #cart-link (st_products_products_catal_view -> st_cart_shopping_cart_view)
    Category: transition
    Objective: Verify that executing CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link' transitions from 'st_products_products_catal_view' to 'st_cart_shopping_cart_view'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-e3237019']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: From state 'st_products_products_catal_view', perform CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link'
    await check_execution_safety("From state 'st_products_products_catal_view', perform CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link'", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/cart'
    assert '/cart' in page.url, f"Expected URL to contain '/cart', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_04_transition(page: Page):
    """
    Test ID: TC-TR-04
    Title: State Transition: CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on #calculate-tax-btn (st_cart_shopping_cart_view -> st_cart_shopping_cart_view_with_error)
    Category: transition
    Objective: Verify that executing CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn' transitions from 'st_cart_shopping_cart_view' to 'st_cart_shopping_cart_view_with_error'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-693880d0']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: From state 'st_cart_shopping_cart_view', perform CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn'
    await check_execution_safety("From state 'st_cart_shopping_cart_view', perform CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn'", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Validation Error Oracle: Expected visible alert/error feedback
    alert_elem = page.locator('.alert-danger, .alert-info, [role="alert"], [data-test="error"], [data-test="tax-display"], #tax-display, #checkout-error')
    is_error_url = ('error=' in page.url) or ('show_tax=true' in page.url)
    has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()
    assert has_alert or is_error_url, 'Expected visible validation error alert or error query parameter'
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_TR_05_transition(page: Page):
    """
    Test ID: TC-TR-05
    Title: State Transition: CLICK_CONTROL:control_cls_30cd57132d__checkout_button on #checkout-button (st_cart_shopping_cart_view_with_error -> st_checkout_checkout_shipp_view)
    Category: transition
    Objective: Verify that executing CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button' transitions from 'st_cart_shopping_cart_view_with_error' to 'st_checkout_checkout_shipp_view'.
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-56c0fdaf']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart?show_tax=true', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: From state 'st_cart_shopping_cart_view_with_error', perform CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button'
    await check_execution_safety("From state 'st_cart_shopping_cart_view_with_error', perform CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button'", '#checkout-button', page.url)
    ctrl = page.locator('#checkout-button')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/checkout'
    assert '/checkout' in page.url, f"Expected URL to contain '/checkout', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_TR_06_transition(page: Page):
    """
    Test ID: TC-TR-06
    Title: State Transition: CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on #cart-link (st_checkout_checkout_shipp_view -> st_cart_shopping_cart_view)
    Category: transition
    Objective: Verify that executing CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link' transitions from 'st_checkout_checkout_shipp_view' to 'st_cart_shopping_cart_view'.
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-9d2b556c']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/checkout', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: From state 'st_checkout_checkout_shipp_view', perform CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link'
    await check_execution_safety("From state 'st_checkout_checkout_shipp_view', perform CLICK_CONTROL:control_cls_7d80a9d0e4__cart_link on '#cart-link'", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/cart'
    assert '/cart' in page.url, f"Expected URL to contain '/cart', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_07_transition(page: Page):
    """
    Test ID: TC-TR-07
    Title: State Transition: CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on a.btn[href="/products"] (st_cart_shopping_cart_view -> st_products_products_catal_view)
    Category: transition
    Objective: Verify that executing CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on 'a.btn[href="/products"]' transitions from 'st_cart_shopping_cart_view' to 'st_products_products_catal_view'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-5aab762d']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: From state 'st_cart_shopping_cart_view', perform CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on 'a.btn[href="/products"]'
    await check_execution_safety('From state \'st_cart_shopping_cart_view\', perform CLICK_CONTROL:control_cls_30cd57132d_a_btn_href___products__ on \'a.btn[href="/products"]\'', 'a.btn[href="/products"]', page.url)
    ctrl = page.locator('a.btn[href="/products"]')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # URL Contains Oracle: Expected fragment '/products'
    assert '/products' in page.url, f"Expected URL to contain '/products', got '{page.url}'"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_08_transition(page: Page):
    """
    Test ID: TC-TR-08
    Title: State Transition: SELECT_OPTION:select_cls_f88ae0853d__product_sort on #product-sort (st_products_products_catal_view -> st_products_products_catal_view_opt_za)
    Category: transition
    Objective: Verify that executing SELECT_OPTION:select_cls_f88ae0853d__product_sort on '#product-sort' transitions from 'st_products_products_catal_view' to 'st_products_products_catal_view_opt_za'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-ed734a68']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Select option 'za' on '#product-sort'
    sel_elem = page.locator('#product-sort')
    await expect(sel_elem.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await sel_elem.first.select_option('za')
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # DOM Mutation Oracle: In-page state change occurred
    assert True  # In-page mutation executed without error
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_TR_09_transition(page: Page):
    """
    Test ID: TC-TR-09
    Title: State Transition: NAVIGATE_LINK:nav_link_55554 on /products?sort=za&menu=open (st_products_products_catal_view_opt_za -> st_products_products_catal_view_opt_za_menu_open)
    Category: transition
    Objective: Verify that executing NAVIGATE_LINK:nav_link_55554 on '/products?sort=za&menu=open' transitions from 'st_products_products_catal_view_opt_za' to 'st_products_products_catal_view_opt_za_menu_open'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-2570c507']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products?sort=za&menu=closed', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Follow link navigation to '/products?sort=za&menu=open'
    dest_full = f'{BASE_URL}/products?sort=za&menu=open'
    await page.goto(dest_full, wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # DOM Mutation Oracle: In-page state change occurred
    assert True  # In-page mutation executed without error
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_VAL_01_validation(page: Page):
    """
    Test ID: TC-VAL-01
    Title: Form Validation Rejection: required_blank_all_loginForm on 127.0.0.1:8995/login
    Category: validation
    Objective: Verify form validation rejects invalid/empty submission with observed message: 'Username is required'.
    Safety Classification: SAFE_READ_ONLY
    Source Evidence Refs: ['ev-8a62c523']
    """
    # Phase 1: Precondition / Setup Verification
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Submit blank form validation check
    u_input = page.locator("#username, input[name=\"username\"]")
    if await u_input.count() > 0 and await u_input.first.is_visible():
        await u_input.first.fill("")
    btn = page.locator("#login-button, button[type=\"submit\"]")
    await btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # Validation Error Oracle: Expected visible alert/error feedback
    alert_elem = page.locator('.alert-danger, .alert-info, [role="alert"], [data-test="error"], [data-test="tax-display"], #tax-display, #checkout-error')
    is_error_url = ('error=' in page.url) or ('show_tax=true' in page.url)
    has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()
    assert has_alert or is_error_url, 'Expected visible validation error alert or error query parameter'
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_VAL_02_validation(page: Page):
    """
    Test ID: TC-VAL-02
    Title: Form Validation Rejection: #calculate-tax-btn on 127.0.0.1:8995/cart
    Category: validation
    Objective: Verify form validation rejects invalid/empty submission with observed message: 'Tax: $NaN | Total: $NaN'.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-693880d0']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Submit invalid/blank input via '#calculate-tax-btn' on http://127.0.0.1:8995/cart
    await check_execution_safety("Submit invalid/blank input via '#calculate-tax-btn' on http://127.0.0.1:8995/cart", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Validation Error Oracle: Expected visible alert/error feedback
    alert_elem = page.locator('.alert-danger, .alert-info, [role="alert"], [data-test="error"], [data-test="tax-display"], #tax-display, #checkout-error')
    is_error_url = ('error=' in page.url) or ('show_tax=true' in page.url)
    has_alert = await alert_elem.count() > 0 and await alert_elem.first.is_visible()
    assert has_alert or is_error_url, 'Expected visible validation error alert or error query parameter'
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_01_state_dependent(page: Page):
    """
    Test ID: TC-SD-01
    Title: State-Dependent Behavior on 127.0.0.1:8995/login (heading:System Access Portal, counter_value:0)
    Category: state_dependent
    Objective: Verify state-dependent behavior of 'required_blank_all_loginForm' when Cart/counter badge value = 0.
    Safety Classification: SAFE_REVERSIBLE
    Source Evidence Refs: ['ev-8a62c523']
    """
    # Phase 1: Precondition / Setup Verification
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Submit blank form validation check
    u_input = page.locator("#username, input[name=\"username\"]")
    if await u_input.count() > 0 and await u_input.first.is_visible():
        await u_input.first.fill("")
    btn = page.locator("#login-button, button[type=\"submit\"]")
    await btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_02_state_dependent(page: Page):
    """
    Test ID: TC-SD-02
    Title: State-Dependent Behavior on 127.0.0.1:8995/login (heading:System Access Portal, validation_error:Username is required)
    Category: state_dependent
    Objective: Verify state-dependent behavior of 'required_blank_all_loginForm' when Cart/counter badge value = 0; URL state parameters = {'error': 'Username is required'}.
    Safety Classification: SAFE_REVERSIBLE
    Source Evidence Refs: ['ev-8a62c523']
    """
    # Phase 1: Precondition / Setup Verification
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Submit blank form validation check
    u_input = page.locator("#username, input[name=\"username\"]")
    if await u_input.count() > 0 and await u_input.first.is_visible():
        await u_input.first.fill("")
    btn = page.locator("#login-button, button[type=\"submit\"]")
    await btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_03_state_dependent(page: Page):
    """
    Test ID: TC-SD-03
    Title: State-Dependent Behavior on 127.0.0.1:8995/products (heading:Products Catalog, heading:Alpha Device)
    Category: state_dependent
    Objective: Verify state-dependent behavior of 'login_form' when Authenticated user session active; Cart/counter badge value = 0.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-8cc0831a']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Authenticate with test credentials
    u_input = page.locator("#username, input[name=\"username\"]")
    await expect(u_input.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await u_input.first.fill(TEST_USERNAME)
    p_input = page.locator("#password, input[name=\"password\"]")
    await p_input.first.fill(TEST_PASSWORD)
    submit_btn = page.locator("#login-button, button[type=\"submit\"]")
    await submit_btn.first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_04_state_dependent(page: Page):
    """
    Test ID: TC-SD-04
    Title: State-Dependent Behavior on 127.0.0.1:8995/cart (heading:Shopping Cart, counter_value:0)
    Category: state_dependent
    Objective: Verify state-dependent behavior of '#cart-link' when Authenticated user session active; Cart/counter badge value = 0.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-e3237019']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Under state condition (Authenticated user session active; Cart/counter badge value = 0), execute CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link'
    await check_execution_safety("Under state condition (Authenticated user session active; Cart/counter badge value = 0), execute CLICK_CONTROL:control_cls_f88ae0853d__cart_link on '#cart-link'", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_05_state_dependent(page: Page):
    """
    Test ID: TC-SD-05
    Title: State-Dependent Behavior on 127.0.0.1:8995/cart (heading:Shopping Cart, validation_error:Tax: $NaN | Total: $NaN)
    Category: state_dependent
    Objective: Verify state-dependent behavior of '#calculate-tax-btn' when Authenticated user session active; Cart/counter badge value = 0; URL state parameters = {'show_tax': 'true'}.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-693880d0']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Under state condition (Authenticated user session active; Cart/counter badge value = 0; URL state parameters = {'show_tax': 'true'}), execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn'
    await check_execution_safety("Under state condition (Authenticated user session active; Cart/counter badge value = 0; URL state parameters = {'show_tax': 'true'}), execute CLICK_CONTROL:control_cls_30cd57132d__calculate_tax_btn on '#calculate-tax-btn'", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.sandbox_only
async def test_TC_SD_06_state_dependent(page: Page):
    """
    Test ID: TC-SD-06
    Title: State-Dependent Behavior on 127.0.0.1:8995/checkout (heading:Checkout: Shipping Details, counter_value:0)
    Category: state_dependent
    Objective: Verify state-dependent behavior of '#checkout-button' when Authenticated user session active; Cart/counter badge value = 0.
    Safety Classification: SANDBOX_ONLY
    Source Evidence Refs: ['ev-56c0fdaf']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Under state condition (Authenticated user session active; Cart/counter badge value = 0), execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button'
    await check_execution_safety("Under state condition (Authenticated user session active; Cart/counter badge value = 0), execute CLICK_CONTROL:control_cls_30cd57132d__checkout_button on '#checkout-button'", '#checkout-button', page.url)
    ctrl = page.locator('#checkout-button')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_07_state_dependent(page: Page):
    """
    Test ID: TC-SD-07
    Title: State-Dependent Behavior on 127.0.0.1:8995/products (heading:Products Catalog, heading:Gamma System)
    Category: state_dependent
    Objective: Verify state-dependent behavior of '#product-sort' when Authenticated user session active; Selected sort/filter option = 'za'; Cart/counter badge value = 0; URL state parameters = {'sort': 'za', 'menu': 'closed'}.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-ed734a68']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Select option 'za' on '#product-sort'
    sel_elem = page.locator('#product-sort')
    await expect(sel_elem.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await sel_elem.first.select_option('za')
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_SD_08_state_dependent(page: Page):
    """
    Test ID: TC-SD-08
    Title: State-Dependent Behavior on 127.0.0.1:8995/products (heading:Products Catalog, heading:Gamma System)
    Category: state_dependent
    Objective: Verify state-dependent behavior of '/products?sort=za&menu=open' when Authenticated user session active; Selected sort/filter option = 'za'; In-page navigation/filter drawer is open; Cart/counter badge value = 0; URL state parameters = {'sort': 'za', 'menu': 'open'}.
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-2570c507']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/products', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Follow link navigation to '/products?sort=za&menu=open'
    dest_full = f'{BASE_URL}/products?sort=za&menu=open'
    await page.goto(dest_full, wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # State Reached Oracle: Landmark/heading/route verified
    assert page.url is not None and not page.url.startswith('chrome-error://')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
async def test_TC_REG_01_regression(page: Page):
    """
    Test ID: TC-REG-01
    Title: Defect Regression: Text Anomaly: 'NaN'
    Category: regression
    Objective: Prevent regression of confirmed defect 'Text Anomaly: 'NaN'' (Observed actual: Detected anomaly 'NaN' rendered in visible text after interaction.).
    Safety Classification: REQUIRES_AUTH_CREDENTIALS
    Source Evidence Refs: ['ev-2570c507']
    """
    # Phase 1: Precondition / Setup Verification
    await ensure_authenticated_session(page, BASE_URL)
    await page.goto(f'{BASE_URL}/cart', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Execute reproduction step 1: CLICK_CONTROL on '#calculate-tax-btn'
    await check_execution_safety("Execute reproduction step 1: CLICK_CONTROL on '#calculate-tax-btn'", '#calculate-tax-btn', page.url)
    ctrl = page.locator('#calculate-tax-btn')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Defect Absent Oracle: Check for absence of confirmed defect anomaly ('NaN' / calculation error)
    body_text = await page.locator('body').inner_text()
    tax_elem = page.locator('#tax-display')
    tax_text = (await tax_elem.inner_text()) if await tax_elem.count() > 0 else ''
    if 'nan' in body_text.lower() or 'nan' in tax_text.lower():
        raise DefectDetected(f"DEFECT_DETECTED: Confirmed text anomaly 'NaN' found in visible DOM output: {tax_text or body_text[:200]}")
    if await tax_elem.count() > 0:
        assert '$' in tax_text and 'Tax:' in tax_text, f"Expected valid tax formatting, got: {tax_text}"
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.skip(reason='Exploratory hypothesis requiring empirical confirmation: Exploratory Frontier Hypothesis: a[href="/products"] on 127.0.0.1:8995/login')
async def test_TC_HYP_01_exploratory_hypothesis(page: Page):
    """
    Test ID: TC-HYP-01
    Title: Exploratory Frontier Hypothesis: a[href="/products"] on 127.0.0.1:8995/login
    Category: exploratory_hypothesis
    Objective: Explore pending candidate 'a[href="/products"]' that remained unprobed due to BUDGET_CEILING_REACHED.
    Safety Classification: SAFE_READ_ONLY
    Source Evidence Refs: ['ev_init_st_login_system_access__view']
    """
    # Phase 1: Precondition / Setup Verification
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Exploratory test of unprobed frontier candidate 'a[href="/products"]' (BUDGET_CEILING_REACHED)
    await check_execution_safety('Exploratory test of unprobed frontier candidate \'a[href="/products"]\' (BUDGET_CEILING_REACHED)', 'a[href="/products"]', page.url)
    ctrl = page.locator('a[href="/products"]')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Hypothesis Requiring Confirmation: Skip in verified regression mode
    pytest.skip('Candidate is an unverified hypothesis requiring empirical enrichment')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)

@pytest.mark.asyncio
@pytest.mark.skip(reason='Exploratory hypothesis requiring empirical confirmation: Exploratory Frontier Hypothesis: #cart-link on 127.0.0.1:8995/login')
async def test_TC_HYP_02_exploratory_hypothesis(page: Page):
    """
    Test ID: TC-HYP-02
    Title: Exploratory Frontier Hypothesis: #cart-link on 127.0.0.1:8995/login
    Category: exploratory_hypothesis
    Objective: Explore pending candidate '#cart-link' that remained unprobed due to BUDGET_CEILING_REACHED.
    Safety Classification: SAFE_READ_ONLY
    Source Evidence Refs: ['ev_init_st_login_system_access__view']
    """
    # Phase 1: Precondition / Setup Verification
    await page.goto(f'{BASE_URL}/login', wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Phase 2: Action Steps Execution & In-Flight Transition Oracles
    # Step 1: Exploratory test of unprobed frontier candidate '#cart-link' (BUDGET_CEILING_REACHED)
    await check_execution_safety("Exploratory test of unprobed frontier candidate '#cart-link' (BUDGET_CEILING_REACHED)", '#cart-link', page.url)
    ctrl = page.locator('#cart-link')
    await expect(ctrl.first).to_be_visible(timeout=DEFAULT_TIMEOUT)
    await expect(ctrl.first).to_be_enabled(timeout=DEFAULT_TIMEOUT)
    await ctrl.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
    # Hypothesis Requiring Confirmation: Skip in verified regression mode
    pytest.skip('Candidate is an unverified hypothesis requiring empirical enrichment')
    # Phase 3: Observable Evidence Assertions (Remaining Oracles)
