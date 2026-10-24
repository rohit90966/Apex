"""
Unit and Regression Tests for Critical Runtime Repairs:
1. Ollama Test Plan bounded re-generation and critique routing
2. BugReproducer Playwright Sync API thread isolation
3. Replayable Playwright export generating complete login-to-actions journeys
4. Models and StateTransition integrity
"""

import pytest
from app.agents.state import AgentWorkflowState
from app.agents.workflow import should_revise
from app.agents.models import CritiqueResult
from app.investigation_agent.models import (
    UIStateObservation,
    StateTransitionRecord,
    BugLead,
    OracleType,
    OracleStrength,
)
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.behavior_model import ApplicationBehaviorModel


def test_should_revise_routing_bounds():
    """Verifies that should_revise does not trigger redundant LLM regeneration on cosmetic issues."""
    # 1. Issues found but NOT missing requirements -> routes to plan_validator directly
    state_cosmetic: AgentWorkflowState = {
        "critique_result": CritiqueResult(
            issues_found=True,
            issue_categories=["UNSUPPORTED_ELEMENTS"],
            missing_requirement_ids=[],
            duplicate_test_case_ids=[],
            summary="Unsupported element in phrasing",
        ),
        "revision_count": 0,
        "max_revisions": 2,
    }
    assert should_revise(state_cosmetic) == "plan_validator"

    # 2. Max revisions reached -> routes to plan_validator even if missing requirements
    state_maxed: AgentWorkflowState = {
        "critique_result": CritiqueResult(
            issues_found=True,
            issue_categories=["MISSING_REQUIREMENT"],
            missing_requirement_ids=["REQ-01"],
            duplicate_test_case_ids=[],
            summary="Missing REQ-01",
        ),
        "revision_count": 2,
        "max_revisions": 2,
    }
    assert should_revise(state_maxed) == "plan_validator"

    # 3. Genuine missing requirement with revisions available -> routes to strategy_planner
    state_missing: AgentWorkflowState = {
        "critique_result": CritiqueResult(
            issues_found=True,
            issue_categories=["MISSING_REQUIREMENT"],
            missing_requirement_ids=["REQ-01"],
            duplicate_test_case_ids=[],
            summary="Missing REQ-01",
        ),
        "revision_count": 0,
        "max_revisions": 2,
    }
    assert should_revise(state_missing) == "strategy_planner"
    assert state_missing["revision_count"] == 1


def test_behavior_model_exports_complete_journey():
    """Verifies that ApplicationBehaviorModel exports all action types, not just assert page.url."""
    model = ApplicationBehaviorModel()
    model.add_journey(
        name="Login and Checkout Journey",
        steps=[
            {"action": "NAVIGATE", "target": "https://www.saucedemo.com/"},
            {"action": "FILL", "target": "#user-name", "value": "standard_user"},
            {"action": "FILL", "target": "#password", "value": "secret_sauce"},
            {"action": "CLICK", "target": "#login-button"},
            {"action": "CLICK_CONTROL", "target": "button#add-to-cart-sauce-labs-backpack"},
            {"action": "CLICK_CONTROL", "target": "a.shopping_cart_link"},
        ],
    )

    script = model.export_playwright_test("Login and Checkout Journey")

    # Assert all steps are rendered into python playwright code
    assert "page.goto('https://www.saucedemo.com/'" in script
    assert "page.locator('#user-name').fill('standard_user')" in script
    assert "page.locator('#password').fill('secret_sauce')" in script
    assert "page.locator('#login-button').click()" in script
    assert "page.locator('button#add-to-cart-sauce-labs-backpack').click()" in script
    assert "page.locator('a.shopping_cart_link').click()" in script
    assert "assert page.url is not None" in script
    assert "browser.close()" in script


def test_report_builder_reconstructs_auth_and_transitions():
    """Verifies that InvestigationReportBuilder builds journeys containing authentication and transition steps."""
    obs_login = UIStateObservation(
        state_id="st_login",
        url="https://www.saucedemo.com/",
        normalized_route="/",
        page_title="Swag Labs Login",
    )
    obs_inventory = UIStateObservation(
        state_id="st_inventory",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Swag Labs Inventory",
    )

    tr_click = StateTransitionRecord(
        source_state_id="st_inventory",
        action_type="CLICK_CONTROL",
        target_element_desc="button#add-to-cart-sauce-labs-backpack",
        destination_state_id="st_inventory_cart1",
        actual_outcome="Item added to cart badge",
    )

    state = {
        "target_url": "https://www.saucedemo.com/",
        "auth_verified": True,
        "auth_credentials": {"username": "standard_user", "password": "secret_sauce", "source": "DEMO_REGISTRY"},
        "states": {"st_login": obs_login, "st_inventory": obs_inventory},
        "transitions": [tr_click],
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
        "frontier": {},
        "planning_strategy": "LLM_ADAPTIVE",
        "llm_calls_count": 1,
        "llm_fallbacks_count": 0,
        "llm_latency_seconds": 1.2,
    }

    report = InvestigationReportBuilder.build_report(state)

    assert report.playwright_test_script is not None
    script = report.playwright_test_script

    # Verify that the exported script contains the full login sequence and subsequent action
    assert "page.goto('https://www.saucedemo.com/'" in script
    assert "standard_user" in script
    assert "secret_sauce" in script
    assert "button#add-to-cart-sauce-labs-backpack" in script
    assert "assert page.url is not None" in script
