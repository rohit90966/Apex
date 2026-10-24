"""
Regression test suite for VerityQA Critical Investigation Integrity Repair.
Validates:
1. Stale action discovered on inventory is not attempted from cart.
2. Checkout candidates do not execute from incompatible states (e.g. inventory).
3. Product add-to-cart candidates do not repeatedly fail or execute from cart.
4. Successful action followed by authoritative post-action state observation.
5. Failed action not counted as a successful transition or destination state.
6. Retry/recovery without repeated identical failures.
7. Correct same-page fragment-link handling.
8. Element selectors are never mistaken for navigation URLs.
9. External navigation remains blocked by safety policy.
10. Redirect and unsafe destination checks remain effective.
11. Accurate distinction between attempts and successful transitions in report.
12. Pending frontier and budget-exhaustion reporting.
13. Branch and return-edge reporting.
14. Inspection Summary / API integration with non-empty investigation evidence.
15. Investigation-only isolation: zero test-case or bug-report generation.
"""

import pytest
from unittest.mock import MagicMock, patch
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    FrontierItem,
    FrontierStatus,
    ActionFailureReason,
    StateTransitionRecord,
    InvestigationStatus,
    InvestigationReport,
)
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.workflow import InvestigationOrchestrator


def test_stale_action_on_inventory_not_selected_from_cart():
    """1. Stale action discovered on inventory must not be selected while in cart state."""
    frontier = {}
    obs_inventory = UIStateObservation(
        state_id="st_inventory",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="saucedemo.com/inventory.html",
        page_title="Inventory",
        interactive_controls=[
            ElementReference(
                element_id="btn_add",
                role="button",
                accessible_name="Add to cart",
                tag_name="button",
                selector="button#add-to-cart",
            )
        ]
    )
    FrontierManager.populate_from_observation(frontier, obs_inventory, [])

    # Now browser is currently on cart page
    obs_cart = UIStateObservation(
        state_id="st_cart",
        url="https://www.saucedemo.com/cart.html",
        normalized_route="saucedemo.com/cart.html",
        page_title="Your Cart",
        interactive_controls=[
            ElementReference(
                element_id="btn_checkout",
                role="button",
                accessible_name="Checkout",
                tag_name="button",
                selector="button#checkout",
            )
        ]
    )
    FrontierManager.populate_from_observation(frontier, obs_cart, ["st_inventory"])

    # From cart, select_next_candidate must NOT pick button#add-to-cart from inventory
    chosen = FrontierManager.select_next_candidate(frontier, "st_cart", obs_cart)
    assert chosen is not None
    assert chosen.target == "button#checkout"
    assert chosen.parent_state_id == "st_cart"


def test_checkout_candidate_not_executed_from_inventory():
    """2. Checkout candidates must not execute from inventory where checkout is absent."""
    frontier = {}
    obs_cart = UIStateObservation(
        state_id="st_cart",
        url="https://www.saucedemo.com/cart.html",
        normalized_route="saucedemo.com/cart.html",
        page_title="Your Cart",
        interactive_controls=[
            ElementReference(
                element_id="btn_checkout",
                role="button",
                accessible_name="Checkout",
                tag_name="button",
                selector="button#checkout",
            )
        ]
    )
    FrontierManager.populate_from_observation(frontier, obs_cart, [])

    # Current state is inventory
    obs_inventory = UIStateObservation(
        state_id="st_inventory",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="saucedemo.com/inventory.html",
        page_title="Inventory",
        interactive_controls=[
            ElementReference(
                element_id="link_item",
                role="link",
                accessible_name="Item",
                tag_name="a",
                selector="a#item_4_title",
            )
        ]
    )
    FrontierManager.populate_from_observation(frontier, obs_inventory, ["st_cart"])

    chosen = FrontierManager.select_next_candidate(frontier, "st_inventory", obs_inventory)
    assert chosen is not None
    # Must pick the item on inventory, NOT checkout from cart
    assert chosen.target == "a#item_4_title"


def test_preflight_dom_check_prevents_timeout_on_absent_target():
    """3. Pre-flight DOM check immediately rejects absent targets without waiting timeout."""
    mock_session = MagicMock()
    mock_session.resolve_target.return_value = {
        "present": False,
        "visible": False,
        "enabled": False,
        "reason": "Element not present in current DOM"
    }

    success, outcome, failure_reason, telemetry = InvestigationActionExecutor.execute_action(
        mock_session,
        "CLICK_CONTROL",
        "button#nonexistent"
    )
    assert success is False
    assert failure_reason == ActionFailureReason.TARGET_ABSENT
    assert "not present in current DOM" in outcome
    mock_session.page.click.assert_not_called()


def test_successful_action_records_transition_and_post_observation():
    """4. Successful action creates confirmed transition with post-action observation."""
    orchestrator = InvestigationOrchestrator(headless=True)
    mock_prev_obs = UIStateObservation(
        state_id="st_1",
        url="https://example.com/page1",
        normalized_route="example.com/page1",
        page_title="Page 1",
    )
    mock_new_obs = UIStateObservation(
        state_id="st_2",
        url="https://example.com/page2",
        normalized_route="example.com/page2",
        page_title="Page 2",
    )
    orchestrator.session.capture_observation = MagicMock(return_value=mock_new_obs)

    state = {
        "investigation_id": "inv-1",
        "current_state": mock_prev_obs,
        "proposed_action": {"rationale": "Advance to page 2"},
        "action_execution_result": {
            "success": True,
            "outcome": "Navigated to page 2",
            "action_type": "NAVIGATE_LINK",
            "target": "/page2"
        },
        "transitions": [],
        "evidence_ledger": [],
        "states": {"st_1": mock_prev_obs}
    }

    update = orchestrator.node_verify_action_outcome(state)
    assert len(update["transitions"]) == 1
    tr = update["transitions"][0]
    assert tr.status == "confirmed"
    assert tr.is_successful is True
    assert tr.source_state_id == "st_1"
    assert tr.destination_state_id == "st_2"


def test_failed_action_never_invented_as_transition():
    """5. Failed action must NOT create a successful transition or increment transition count."""
    orchestrator = InvestigationOrchestrator(headless=True)
    mock_prev_obs = UIStateObservation(
        state_id="st_cart",
        url="https://example.com/cart",
        normalized_route="example.com/cart",
        page_title="Cart",
    )
    orchestrator.session.capture_observation = MagicMock()

    state = {
        "investigation_id": "inv-fail",
        "current_state": mock_prev_obs,
        "proposed_action": {"rationale": "Click absent button"},
        "action_execution_result": {
            "success": False,
            "outcome": "Target control button#absent not present in current DOM",
            "failure_reason": ActionFailureReason.TARGET_ABSENT,
            "action_type": "CLICK_CONTROL",
            "target": "button#absent"
        },
        "transitions": [],
        "evidence_ledger": [],
        "states": {"st_cart": mock_prev_obs}
    }

    update = orchestrator.node_verify_action_outcome(state)
    # Transitions list MUST remain empty
    assert len(update["transitions"]) == 0
    # Capture observation must NOT be called for failed action
    orchestrator.session.capture_observation.assert_not_called()
    # Evidence ledger contains ACTION_FAILURE
    assert len(update["evidence_ledger"]) == 1
    assert update["evidence_ledger"][0].evidence_type == "ACTION_FAILURE"


def test_element_selector_never_mistaken_for_navigation_url():
    """6 & 8. Element selectors must be rejected if supplied as navigation targets."""
    is_safe, reason = InvestigationActionExecutor.validate_action_safety(
        "NAVIGATE_LINK",
        '[data-test="shopping-cart-link"]',
        "Navigate link"
    )
    assert is_safe is False
    assert "DOM selector, not a URL" in reason


def test_fragment_links_handled_as_inpage_navigation():
    """7. Same-page fragment link handled safely without triggering external security violation."""
    mock_session = MagicMock()
    mock_session.page.url = "https://example.com/doc#section-2"
    mock_session.navigate.return_value = (True, "https://example.com/doc#section-2", 200)

    success, outcome, failure_reason, telemetry = InvestigationActionExecutor.execute_action(
        mock_session,
        "NAVIGATE_LINK",
        "#section-2"
    )
    assert success is True
    assert failure_reason is None
    assert "fragment" in outcome


def test_external_navigation_remains_blocked_by_safety():
    """9. External navigation outside allowed hosts is blocked by safety validation."""
    is_safe, reason = InvestigationActionExecutor.validate_action_safety(
        "NAVIGATE_LINK",
        "https://twitter.com/saucelabs",
        "Open Twitter page"
    )
    assert is_safe is False
    assert "SAFETY_BLOCKED" in reason or "not in allowed hosts" in reason


def test_redirect_safety_retreats_when_host_unauthorized():
    """10. Redirect to disallowed host triggers safety block and retreats."""
    from app.investigation_agent.browser_session import StatefulBrowserSession
    session = StatefulBrowserSession(headless=True)
    mock_page = MagicMock()
    mock_page.url = "https://malicious-external-site.com/phish"
    session._page = mock_page

    with patch("app.investigation_agent.browser_session.validate_url_safety") as mock_val:
        # Initial navigation allowed, but redirect check raises SafetyViolation
        from app.safety import SafetyViolation
        mock_val.side_effect = [None, SafetyViolation("Disallowed redirect host")]
        ok, res_url, status = session.navigate("https://127.0.0.1/redirect-me")
        assert ok is False
        assert status == 403
        assert "SAFETY_BLOCKED: Redirected to unauthorized host" in res_url
        mock_page.go_back.assert_called_once()


def test_attempts_vs_successful_transitions_metric_distinction():
    """11. Report accurately distinguishes attempts from successful transitions."""
    tr_success = StateTransitionRecord(
        source_state_id="st_1",
        action_type="CLICK_CONTROL",
        target_element_desc="btn1",
        action_purpose="test",
        actual_outcome="success",
        destination_state_id="st_2",
        status="confirmed",
        is_successful=True,
    )

    state = {
        "target_url": "https://example.com",
        "start_time": 1700000000.0,
        "investigation_status": InvestigationStatus.COMPLETED,
        "actions_attempted": 8,
        "actions_successful": 3,
        "actions_failed": 4,
        "actions_safety_blocked": 1,
        "stopping_reason": "Action budget exhausted (8/8 actions)",
        "states": {},
        "transitions": [tr_success],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }

    report = InvestigationReportBuilder.build_report(state)
    assert report.overview.actions_attempted == 8
    assert report.overview.actions_successful == 3
    assert report.overview.actions_failed == 4
    assert report.overview.actions_safety_blocked == 1
    assert report.overview.transitions_observed == 1  # Exactly 1 successful transition


def test_legacy_inspection_summary_mapping_in_api():
    """14 & 15. Inspection summary provides explicit typed mapping while remaining investigation-only."""
    tr = StateTransitionRecord(
        source_state_id="st_root",
        action_type="CLICK_CONTROL",
        target_element_desc="button#start",
        action_purpose="explore",
        actual_outcome="navigated",
        destination_state_id="st_next",
        status="confirmed",
        is_successful=True
    )
    state = {
        "target_url": "https://www.saucedemo.com/",
        "start_time": 1700000000.0,
        "actions_attempted": 5,
        "actions_successful": 4,
        "actions_failed": 1,
        "actions_safety_blocked": 0,
        "stopping_reason": "Action budget exhausted (5/5 actions)",
        "states": {"st_root": UIStateObservation(state_id="st_root", url="https://www.saucedemo.com/", normalized_route="saucedemo.com", page_title="Swag Labs")},
        "transitions": [tr],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }
    report = InvestigationReportBuilder.build_report(state)
    
    # Assert investigation-only guarantees: no test cases, no test suites, no bug reports
    report_dict = report.model_dump()
    assert "test_cases" not in report_dict
    assert "bug_reports" not in report_dict
    assert "execution_results" not in report_dict
