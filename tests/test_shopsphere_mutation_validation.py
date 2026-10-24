"""
ShopSphere Benchmark Mutation Validation Test Suite.
===================================================
Targeted Playwright regression and verification tests for all 8 benchmark mutations (B01-B08).
Validates that each test reliably catches the defect when active, and passes cleanly when clean.
"""

import os
import re
import urllib.request
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("SHOPSPHERE_TEST_URL", "http://127.0.0.1:5173")


@pytest.fixture(autouse=True)
def reset_store_before_each():
    """Reset store state before each benchmark test."""
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/reset", data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as res:
            pass
    except Exception:
        pass


# --------------------------------------------------------------------------
# B01: Cart Grand Total Calculation
# --------------------------------------------------------------------------

def test_b01_cart_grand_total_multiplier(page):
    """
    B01: Verify cart grand total accurately sums line totals + tax when item qty > 1.
    prod-01 ($149.99) x 2 = $299.98 subtotal - $15.00 discount = $284.98 taxable + $28.50 tax = $313.48 grand total.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-01"]')
    page.locator('[data-product-id="prod-01"]').click()
    page.wait_for_timeout(300)

    page.click('[data-test="cart-link"]')
    page.wait_for_selector('[data-test="cart-qty-inc-prod-01"]')
    page.click('[data-test="cart-qty-inc-prod-01"]')
    page.wait_for_timeout(400)

    qty_text = page.locator('[data-test="cart-qty-value-prod-01"]').inner_text()
    assert qty_text == "2", f"Expected quantity 2, got '{qty_text}'"

    subtotal = page.locator('[data-test="cart-subtotal"]').inner_text()
    assert subtotal == "$299.98", f"Expected subtotal $299.98, got '{subtotal}'"

    grand_total = page.locator('[data-test="cart-grand-total"]').inner_text()
    assert grand_total == "$313.48", (
        f"Defect B01 detected: Grand Total is '{grand_total}', expected '$313.48' ($299.98 subtotal - $15 discount + $28.50 tax)"
    )


# --------------------------------------------------------------------------
# B02: Reversed Price Sorting
# --------------------------------------------------------------------------

def test_b02_reversed_price_sorting(page):
    """
    B02: Verify ascending price sort arranges products from lowest to highest.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-test="sort-select"]')
    page.select_option('[data-test="sort-select"]', "price_asc")
    page.wait_for_timeout(400)

    price_locators = page.locator('.product-price')
    count = price_locators.count()
    assert count >= 2, f"Expected at least 2 product prices, found {count}"

    prices = []
    for i in range(count):
        raw = price_locators.nth(i).inner_text().replace("$", "").replace(",", "").strip()
        prices.append(float(raw))

    # Assert ascending sort
    for i in range(len(prices) - 1):
        assert prices[i] <= prices[i + 1], (
            f"Defect B02 detected: Price at position {i} (${prices[i]:.2f}) is greater than price at position {i+1} (${prices[i+1]:.2f}). "
            f"Catalogue is sorted in descending order: {prices[:4]}"
        )


# --------------------------------------------------------------------------
# B03: Required-Field Validation Bypass (Postal Code)
# --------------------------------------------------------------------------

def test_b03_required_postal_code_bypass(page):
    """
    B03: Verify checkout form requires postal code and blocks order completion if empty.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-07"]')
    page.locator('[data-product-id="prod-07"]').click()
    page.wait_for_timeout(300)

    page.goto(f"{BASE_URL}/checkout")
    page.wait_for_selector('[data-test="place-order-button"]')

    page.fill('[data-test="input-customer-name"]', "Test User")
    page.fill('[data-test="input-email"]', "test.user@example.com")
    page.fill('[data-test="input-address"]', "123 Test Avenue")
    page.fill('[data-test="input-city"]', "Test City")
    page.fill('[data-test="input-state"]', "CA")
    page.fill('[data-test="input-postal-code"]', "")

    page.click('[data-test="place-order-button"]')
    page.wait_for_timeout(500)

    assert "/confirmation/" not in page.url, (
        f"Defect B03 detected: Order was placed with empty postal code! Navigated to '{page.url}'"
    )
    assert page.locator('[data-test="error-postal-code"]').is_visible(), (
        "Expected validation error for postal code to be visible"
    )


# --------------------------------------------------------------------------
# B04: Shopping Cart Item Removal Consistency
# --------------------------------------------------------------------------

def test_b04_inconsistent_item_removal(page):
    """
    B04: Verify removing an item updates both badge and cart table contents/subtotal.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-01"]')
    page.locator('[data-product-id="prod-01"]').click()
    page.wait_for_timeout(300)

    page.goto(f"{BASE_URL}/cart")
    page.wait_for_selector('[data-test="cart-remove-prod-01"]')
    page.click('[data-test="cart-remove-prod-01"]')
    page.wait_for_timeout(400)

    badge = page.locator('[data-test="cart-badge"]').inner_text()
    assert badge == "0", f"Expected badge '0', got '{badge}'"

    # Defect B04 leaves item in cart table and subtotal > 0 despite badge = 0
    item_count = page.locator('[data-test="cart-item-prod-01"]').count()
    subtotal = page.locator('[data-test="cart-subtotal"]').inner_text() if page.locator('[data-test="cart-subtotal"]').count() > 0 else "$0.00"

    assert item_count == 0, (
        f"Defect B04 detected: Item prod-01 remains in cart table after removal (badge is 0, subtotal is {subtotal})"
    )


# --------------------------------------------------------------------------
# B05: Cart Totals Recalculation on Quantity Modification
# --------------------------------------------------------------------------

def test_b05_stale_total_after_quantity_change(page):
    """
    B05: Verify cart subtotal immediately updates when item quantity is modified.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-01"]')
    page.locator('[data-product-id="prod-01"]').click()
    page.wait_for_timeout(300)

    page.goto(f"{BASE_URL}/cart")
    page.wait_for_selector('[data-test="cart-qty-inc-prod-01"]')
    page.click('[data-test="cart-qty-inc-prod-01"]')
    page.wait_for_timeout(400)

    line_total = page.locator('[data-test="cart-line-total-prod-01"]').inner_text()
    subtotal = page.locator('[data-test="cart-subtotal"]').inner_text()

    assert line_total == "$299.98", f"Expected line total '$299.98', got '{line_total}'"
    assert subtotal == "$299.98", (
        f"Defect B05 detected: Cart subtotal is stale '{subtotal}', line total updated to '{line_total}'"
    )


# --------------------------------------------------------------------------
# B06: Shopping Cart Session Persistence Across Page Reload
# --------------------------------------------------------------------------

def test_b06_cart_persistence_regression(page):
    """
    B06: Verify cart contents persist after page reload.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-01"]')
    page.locator('[data-product-id="prod-01"]').click()
    page.wait_for_timeout(300)

    # In-app navigation so initial transition is not a reload
    page.click('[data-test="cart-link"]')
    page.wait_for_selector('[data-test="cart-heading"]')
    assert page.locator('[data-test="cart-badge"]').inner_text() == "1"

    page.reload()
    page.wait_for_timeout(500)

    badge = page.locator('[data-test="cart-badge"]').inner_text()
    item_present = page.locator('[data-test="cart-item-prod-01"]').count() > 0
    is_empty_rendered = "Your Cart is Empty" in page.content()

    assert badge == "1" and item_present and not is_empty_rendered, (
        f"Defect B06 detected: Cart was wiped after page reload (badge='{badge}', item_present={item_present}, empty={is_empty_rendered})"
    )


# --------------------------------------------------------------------------
# B07: Promotional Discount Threshold Calculation
# --------------------------------------------------------------------------

def test_b07_incorrect_discount_threshold(page):
    """
    B07: Verify promotional discount of $15.00 applies to orders over $100.00.
    prod-01 ($149.99) >= $100.00 must receive -$15.00 discount.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-product-id="prod-01"]')
    page.locator('[data-product-id="prod-01"]').click()
    page.wait_for_timeout(300)

    page.goto(f"{BASE_URL}/cart")
    page.wait_for_selector('[data-test="cart-discount"]')

    discount = page.locator('[data-test="cart-discount"]').inner_text()
    assert discount == "-$15.00", (
        f"Defect B07 detected: Order of $149.99 received discount '{discount}', expected '-$15.00' ($15 off over $100.00)"
    )


# --------------------------------------------------------------------------
# B08: Storefront Product Card Navigation Target
# --------------------------------------------------------------------------

def test_b08_wrong_product_detail_navigation(page):
    """
    B08: Verify clicking prod-02 (Fitness Watch) navigates to prod-02 and displays watch details.
    """
    page.goto(f"{BASE_URL}/")
    page.wait_for_selector('[data-test="product-title-prod-02"]')
    page.click('[data-test="product-title-prod-02"]')
    page.wait_for_selector('[data-test="product-name"]')

    url = page.url
    name = page.locator('[data-test="product-name"]').inner_text()

    assert url.endswith("/products/prod-02"), (
        f"Defect B08 detected: Clicked prod-02 but navigated to '{url}'"
    )
    assert "Fitness Watch" in name, (
        f"Defect B08 detected: Expected 'Fitness Watch' in title, got '{name}'"
    )
