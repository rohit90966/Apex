"""
Tests for the Knowledge-Guided, Evidence-Grounded Test Synthesis Engine on legacy-shaped reports
(reports without an observed inventory). The contract is grounding, not quantity: every selector,
route and expectation must come from the report itself.
"""

import pytest

from app.config import get_settings
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from app.test_planning.coverage import CoverageDimension
from app.intelligence.archetypes import WebsiteArchetype, ApplicationCapability
from app.intelligence.target_context import canonical_origin
from app.schemas import TestCaseStatus, ActionType, is_meaningful_assertion


class MockInvestigationReport:
    """Legacy-shaped report of an investigated SauceDemo application."""
    def __init__(self):
        self.target_url = "https://www.saucedemo.com"
        self.overview = type("Overview", (), {"target_url": "https://www.saucedemo.com"})()
        self.authentication_access = type("AuthAccess", (), {"credentials_available": True})()
        self.inferred_journeys = [
            {
                "name": "Standard Checkout Workflow",
                "objective": "Verify complete purchase flow from inventory to cart to checkout",
                "steps": [
                    {"action": "NAVIGATE", "target": "/"},
                    {"action": "FILL", "target": "#user-name", "value": "standard_user"},
                    {"action": "FILL", "target": "#password", "value": "secret_sauce"},
                    {"action": "CLICK", "target": "#login-button", "expected_url_fragment": "/inventory.html"},
                    {"action": "CLICK", "target": "#add-to-cart-sauce-labs-backpack"},
                    {"action": "CLICK", "target": "[data-test='shopping-cart-link']", "expected_url_fragment": "/cart.html"},
                ],
                "evidence_refs": ["ev-wf-01", "ev-wf-02"],
            },
            {
                "name": "Catalog Browsing & Sorting Workflow",
                "objective": "Verify user can sort items and view product details",
                "steps": [
                    {"action": "NAVIGATE", "target": "/inventory.html"},
                    {"action": "SELECT_OPTION", "target": "[data-test='product-sort-container']", "value": "za"},
                    {"action": "CLICK", "target": "#item_4_title_link"},
                ],
                "evidence_refs": ["ev-wf-03"],
            },
        ]
        self.controls = [
            {"selector": "#user-name", "action_type": "FILL", "text": "Username"},
            {"selector": "#password", "action_type": "FILL", "text": "Password"},
            {"selector": "#login-button", "action_type": "CLICK", "text": "Login"},
            {"selector": "#add-to-cart-sauce-labs-backpack", "action_type": "CLICK", "text": "Add to cart"},
            {"selector": "[data-test='shopping-cart-link']", "action_type": "CLICK", "text": "Cart"},
            {"selector": "[data-test='product-sort-container']", "action_type": "SELECT", "text": "Sort"},
            {"selector": "#checkout", "action_type": "CLICK", "text": "Checkout"},
        ]
        self.states = [
            {"url": "https://www.saucedemo.com/", "heading": "Swag Labs"},
            {"url": "https://www.saucedemo.com/inventory.html", "heading": "Products"},
            {"url": "https://www.saucedemo.com/cart.html", "heading": "Your Cart"},
        ]
        self.transitions = []
        self.business_rules_and_hypotheses = []
        self.confirmed_bug_leads = []


@pytest.fixture(autouse=True)
def authorize_saucedemo(monkeypatch):
    """Form interactions are only planned for hosts the operator authorized (VERITYQA_ALLOWED_HOSTS)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "verityqa_allowed_hosts", settings.verityqa_allowed_hosts + ",www.saucedemo.com")


def _assert_grounded(cases, report):
    selectors = {c["selector"] for c in report.controls}
    origin = canonical_origin(report.target_url)
    for tc in cases:
        assert tc.status == TestCaseStatus.PROPOSED and tc.invalid_reason is None
        assert tc.target_origin == origin
        assert any(is_meaningful_assertion(s.assertion) for s in tc.steps), tc.test_id
        for s in tc.steps:
            if s.action in (ActionType.CLICK, ActionType.FILL, ActionType.SELECT):
                assert s.selector in selectors, f"{tc.test_id} uses unobserved selector {s.selector}"
            if s.action == ActionType.NAVIGATE:
                assert canonical_origin(s.value) == origin
            # Credentials are never copied into plans
            assert s.value != "secret_sauce"


def test_knowledge_guided_planner_synthesis_is_grounded():
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()

    cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

    assert len(cases) >= 1
    assert model.primary_archetype == WebsiteArchetype.ECOMMERCE
    assert ApplicationCapability.CART_MANAGEMENT in model.capabilities
    _assert_grounded(cases, report)
    journey = next(tc for tc in cases if tc.name == "Standard Checkout Workflow")
    assert any(s.value == "${CREDENTIAL_PASSWORD}" for s in journey.steps)
    assert journey.requires_test_account

    assert coverage.total_scenarios == len(cases)
    assert CoverageDimension.ROUTE in coverage.dimensions
    assert planner.last_diagnostics["inventory_provenance"] == "LEGACY_REPORT"


def test_synthesize_test_cases_from_investigation_integration():
    from app.investigation_agent.test_synthesis import synthesize_test_cases_from_investigation

    report = MockInvestigationReport()
    diagnostics = {}
    cases = synthesize_test_cases_from_investigation(report, max_cases=30, diagnostics=diagnostics)

    assert cases
    _assert_grounded(cases, report)
    assert diagnostics["target_origin"] == "https://www.saucedemo.com"


def test_synthesis_handles_none_objective_without_fallback():
    from app.investigation_agent.test_synthesis import synthesize_test_cases_from_investigation

    report = MockInvestigationReport()
    for j in report.inferred_journeys:
        j["objective"] = None
        j["name"] = None

    cases = synthesize_test_cases_from_investigation(report, max_cases=30)
    assert cases
    for tc in cases:
        assert tc.name and tc.objective
    _assert_grounded(cases, report)


def test_journey_without_grounded_oracle_is_not_emitted():
    """A replay whose steps carry no observed expectation would verify nothing."""
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()
    for j in report.inferred_journeys:
        for s in j["steps"]:
            s.pop("expected_url_fragment", None)
    cases, _, _ = planner.plan_test_suite_sync(report)
    assert not any(tc.oracle_class == "SPECIFICATION" for tc in cases)
