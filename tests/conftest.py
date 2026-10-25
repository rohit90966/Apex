"""
Pytest configuration for top-level VerityQA tests.
Provides a standard Playwright page fixture for end-to-end tests.
"""

import os
import sys

import pytest
from playwright.sync_api import sync_playwright

# The legacy demo sites (Apex portal, TaskDash, benchmark shop) were moved out of the repository root to
# archive/legacy_demo_sites; they remain test fixtures for cross-site behaviour, imported as `demo_app`.
_LEGACY_SITES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "archive", "legacy_demo_sites")
if os.path.isdir(_LEGACY_SITES) and _LEGACY_SITES not in sys.path:
    sys.path.append(_LEGACY_SITES)


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Tests never read or write the real investigation memory (data/memory)."""
    monkeypatch.setenv("VERITYQA_MEMORY_DIR", str(tmp_path / "memory"))


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
