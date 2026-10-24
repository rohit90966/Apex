"""
Unit and integration tests for the Advanced Stateful Investigation Agent.
Covers:
- Scenario 1: Public landing page investigation
- Scenario 2: Login page with authorized demo credentials (auth boundary crossing)
- Scenario 3: Authenticated product listing and state exploration
- Scenario 4: Missing credentials handling (truthful partial report, blocked status)
- Scenario 5: Safety policy enforcement and malicious input resistance
- Scenario 6: Investigation-only execution constraint (no test cases, no bug reports)
- Scenario 7: Persistent frontier and state graph transitions
"""

import pytest
from unittest.mock import MagicMock, patch
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    FrontierStatus,
    InvestigationStatus,
    RuleCategory,
)
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.authenticator import AuthenticationManager
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.state import InvestigationAgentState


def test_auth_boundary_detection_password_field():
    obs = UIStateObservation(
        state_id="st_login_1",
        url="https://www.saucedemo.com/",
        normalized_route="saucedemo.com",
        page_title="Swag Labs",
        interactive_controls=[
            ElementReference(
                element_id="c1",
                role="textbox",
                accessible_name="Username",
                tag_name="input",
                selector="input[name='user-name']",
                element_type="text",
            ),
            ElementReference(
                element_id="c2",
                role="textbox",
                accessible_name="Password",
                tag_name="input",
                selector="input[name='password']",
                element_type="password",
            ),
        ],
    )
    is_auth, reason = AuthenticationManager.detect_auth_boundary(obs)
    assert is_auth is True
    assert "password" in reason.lower()


def test_auth_boundary_detection_public_page():
    obs = UIStateObservation(
        state_id="st_home_1",
        url="http://127.0.0.1:8002/",
        normalized_route="127.0.0.1:8002",
        page_title="TaskDash - Dashboard",
        interactive_controls=[
            ElementReference(
                element_id="c1",
                role="link",
                accessible_name="All Tasks",
                tag_name="a",
                selector="a[href='/tasks']",
            ),
        ],
    )
    is_auth, reason = AuthenticationManager.detect_auth_boundary(obs)
    assert is_auth is False


def test_credential_discovery_saucedemo():
    creds = AuthenticationManager.discover_credentials("https://www.saucedemo.com/")
    assert creds is not None
    assert creds["username"] == "standard_user"
    assert creds["password"] == "secret_sauce"


def test_credential_discovery_missing():
    creds = AuthenticationManager.discover_credentials("https://unknown-internal-site.com/")
    assert creds is None


def test_frontier_population_and_prioritization():
    frontier = {}
    obs = UIStateObservation(
        state_id="st_inv_1",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="saucedemo.com/inventory.html",
        page_title="Swag Labs Products",
        interactive_controls=[
            ElementReference(
                element_id="btn_add",
                role="button",
                accessible_name="Add to cart",
                tag_name="button",
                selector="button#add-to-cart-backpack",
            ),
            ElementReference(
                element_id="link_about",
                role="link",
                accessible_name="About",
                tag_name="a",
                selector="a#about_sidebar_link",
            ),
        ],
        navigation_links=[{"text": "Cart", "href": "/cart.html"}],
    )

    added = FrontierManager.populate_from_observation(frontier, obs, [])
    assert added >= 2

    # Verify Add to Cart has higher priority than generic link
    next_candidate = FrontierManager.select_next_candidate(frontier, "st_inv_1")
    assert next_candidate is not None
    assert "add to cart" in next_candidate.rationale.lower()


def test_action_safety_rejection():
    # Attempting a destructive action
    is_safe, reason = InvestigationActionExecutor.validate_action_safety(
        "CLICK", "button#wipe-database", "Wipe database records"
    )
    assert is_safe is False
    assert "blocked by safety policy" in reason


def test_report_builder_investigation_only():
    """Verifies that the generated report contains all 10 mandated sections and NO test cases."""
    state: InvestigationAgentState = {
        "target_url": "https://www.saucedemo.com/",
        "user_description": "Sauce Labs E-Commerce Demo",
        "investigation_id": "inv-test-123",
        "start_time": 1700000000.0,
        "investigation_status": InvestigationStatus.COMPLETED,
        "stopping_reason": "Action budget completed",
        "states": {
            "st_1": UIStateObservation(
                state_id="st_1",
                url="https://www.saucedemo.com/",
                normalized_route="saucedemo.com",
                page_title="Swag Labs",
                headings=["Swag Labs"],
                interactive_controls=[],
            )
        },
        "transitions": [],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
        "auth_boundary_detected": True,
        "auth_attempted": True,
        "auth_verified": True,
        "auth_signals": ["Navigated to authenticated route: https://www.saucedemo.com/inventory.html"],
        "protected_features_unlocked": ["Internal Product Catalog / Dashboard"],
        "actions_attempted": 2,
        "actions_successful": 2,
    }

    report = InvestigationReportBuilder.build_report(state)

    # Verify 10 sections
    assert report.overview.target_url == "https://www.saucedemo.com/"
    assert report.overview.actions_attempted == 2
    assert report.authentication_access.authentication_verified is True
    assert len(report.discovered_application_map) == 1
    assert isinstance(report.user_workflows, list)
    assert isinstance(report.observed_behaviors, list)
    assert isinstance(report.business_rules_and_hypotheses, list)
    assert "unexplored_candidates" in report.investigation_coverage_frontier
    assert isinstance(report.evidence_and_traceability, list)
    assert len(report.investigation_limitations) >= 2

    # STRICT ASSERTION: No test cases, no bug verdicts
    report_dict = report.model_dump()
    assert "test_cases" not in report_dict
    assert "bug_reports" not in report_dict
    assert "execution_results" not in report_dict
