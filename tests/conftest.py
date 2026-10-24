"""
Pytest configuration for top-level VerityQA tests.
Provides a standard Playwright page fixture for end-to-end tests.
"""

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="function")
def page():
    """Isolated Playwright page fixture for end-to-end test execution."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        pg = context.new_page()
        pg.set_default_timeout(8000)
        yield pg
        context.close()
        browser.close()
