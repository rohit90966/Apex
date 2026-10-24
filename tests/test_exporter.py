"""
Tests for app/export/playwright_exporter.py — PlaywrightTestExporter
"""
import pytest
from app.schemas import (
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
)
from app.export.playwright_exporter import PlaywrightTestExporter


@pytest.fixture
def login_test_case():
    return TestCase(
        __test__=False,
        test_id="TC-LOGIN-001",
        name="Valid login flow",
        objective="Login with valid credentials succeeds",
        page_category=PageCategory.LOGIN,
        relevant_elements=["#username", "#password"],
        action_description="Fill credentials and click login",
        expected_outcome="Redirected to dashboard with welcome message",
        expected_behavior_source=ExpectedBehaviorSource.REQUIREMENT,
        priority=Priority.HIGH,
        selection_reason="Login is a critical user journey",
        confidence=0.95,
    )


@pytest.fixture
def search_test_case():
    return TestCase(
        __test__=False,
        test_id="TC-SEARCH-001",
        name="Product search returns results",
        objective="Search returns relevant results for valid keyword",
        page_category=PageCategory.SEARCH,
        relevant_elements=["input#q", "#searchBtn"],
        action_description="Enter keyword and submit search",
        expected_outcome="Results list appears",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.MEDIUM,
        selection_reason="Core search flow verification",
        confidence=0.85,
    )


@pytest.fixture
def exporter():
    return PlaywrightTestExporter()


def test_exporter_instantiation(exporter):
    assert exporter is not None
    assert exporter.runner is not None


def test_export_generates_string(exporter, login_test_case):
    script = exporter.export_to_python_script(
        [login_test_case],
        "http://127.0.0.1:8001/login",
    )
    assert isinstance(script, str)
    assert len(script) > 0


def test_export_contains_imports(exporter, login_test_case):
    script = exporter.export_to_python_script(
        [login_test_case],
        "http://127.0.0.1:8001/login",
    )
    assert "import pytest" in script
    assert "from playwright.sync_api import" in script


def test_export_contains_test_function(exporter, login_test_case):
    script = exporter.export_to_python_script(
        [login_test_case],
        "http://127.0.0.1:8001/login",
    )
    assert "def test_" in script
    assert "Page" in script


def test_export_contains_target_url(exporter, login_test_case):
    url = "http://127.0.0.1:8001/login"
    script = exporter.export_to_python_script([login_test_case], url)
    assert url in script


def test_export_multiple_tests(exporter, login_test_case, search_test_case):
    script = exporter.export_to_python_script(
        [login_test_case, search_test_case],
        "http://127.0.0.1:8001",
    )
    assert script.count("def test_") == 2


def test_export_contains_docstring(exporter, login_test_case):
    script = exporter.export_to_python_script(
        [login_test_case],
        "http://127.0.0.1:8001/login",
    )
    assert "Test ID:" in script
    assert "Objective:" in script


def test_export_contains_page_goto(exporter, login_test_case):
    script = exporter.export_to_python_script(
        [login_test_case],
        "http://127.0.0.1:8001/login",
    )
    assert "page.goto(" in script


def test_export_contains_navigate_step(exporter, search_test_case):
    script = exporter.export_to_python_script(
        [search_test_case],
        "http://127.0.0.1:8001/search",
    )
    assert "page.goto(" in script
    assert "page.locator(" in script
