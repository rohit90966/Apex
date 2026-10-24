"""
tests/test_investigation_evidence_semantics.py
==============================================
Regression test suite auditing evidence semantics, report accuracy,
meaningful state identity, transition verification, and dashboard metric mappings.
Strictly adheres to the Investigation-Only scope.
"""

import pytest
from unittest.mock import MagicMock
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.models import (
    UIStateObservation,
    StateTransitionRecord,
    EvidenceEntry,
    ReportOverview,
    InvestigationStatus,
    FrontierStatus,
    ActionFailureReason,
    RuleOrHypothesis,
    RuleCategory,
)
from app.investigation_agent.workflow import InvestigationOrchestrator
from app.investigation_agent.report_builder import InvestigationReportBuilder


def test_checkout_validation_becomes_distinct_state():
    """Checkout form without error vs checkout form with validation error must be distinct states."""
    url = "https://www.saucedemo.com/checkout-step-one.html"
    headings = ["Checkout: Your Information"]
    forms = [{"form_id": "checkout", "fields": [{"name": "firstName"}, {"name": "lastName"}]}]

    # Before submission: no error
    signals_initial = {
        "has_checkout_form": True,
        "has_validation_error": False,
        "menu_open": False,
    }
    state_id_initial = StatefulBrowserSession.compute_state_identity(
        url=url, headings=headings, dynamic_signals=signals_initial, forms=forms
    )

    # After submission: validation error observed
    signals_error = {
        "has_checkout_form": True,
        "has_validation_error": True,
        "validation_error_message": "Error: First Name is required",
        "validation_error_locator": '[data-test="error"]',
        "menu_open": False,
    }
    state_id_error = StatefulBrowserSession.compute_state_identity(
        url=url, headings=headings, dynamic_signals=signals_error, forms=forms
    )

    assert state_id_initial != state_id_error
    assert "with_error" in state_id_error
    assert "with_error" not in state_id_initial


def test_menu_visibility_change_without_url_change():
    """Opening navigation menu on the same route must produce distinct state and same-route transition."""
    url = "https://www.saucedemo.com/inventory.html"
    headings = ["Products"]
    forms = []

    # Menu closed
    signals_closed = {"has_catalog_grid": True, "counter_value": "0", "menu_open": False}
    state_closed = StatefulBrowserSession.compute_state_identity(
        url=url, headings=headings, dynamic_signals=signals_closed, forms=forms
    )

    # Menu opened
    signals_open = {"has_catalog_grid": True, "counter_value": "0", "menu_open": True}
    state_open = StatefulBrowserSession.compute_state_identity(
        url=url, headings=headings, dynamic_signals=signals_open, forms=forms
    )

    assert state_closed != state_open
    assert "menu_open" in state_open
    assert "menu_open" not in state_closed


def test_successful_click_with_no_observed_effect():
    """
    A successful browser click that produces no state change or navigation
    must NOT increment transitions or be counted as a state advance.
    """
    orch = InvestigationOrchestrator(headless=True)
    obs = UIStateObservation(
        state_id="st_inventory_products_catalog_c0",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="saucedemo.com/inventory.html",
        page_title="Products",
        headings=["Products"],
        screenshot_artifact_id="shot_1.png",
    )

    # Mock session to return the exact same observation after action
    orch.session.capture_observation = MagicMock(return_value=obs)

    state = {
        "investigation_id": "inv-test-01",
        "current_state": obs,
        "states": {obs.state_id: obs},
        "transitions": [],
        "evidence_ledger": [],
        "actions_attempted": 1,
        "actions_successful": 1,
        "actions_verified_effects": 0,
        "route_transitions_observed": 0,
        "same_route_state_transitions": 0,
        "action_execution_result": {
            "success": True,
            "outcome": "Clicked static container without dynamic response",
            "action_type": "CLICK_CONTROL",
            "target": "#static-div",
        },
        "proposed_action": {"rationale": "Test no-effect click"},
    }

    result = orch.node_verify_action_outcome(state)

    # Transitions must remain empty
    assert len(result["transitions"]) == 0
    assert result["actions_verified_effects"] == 0
    assert result["route_transitions_observed"] == 0
    assert result["same_route_state_transitions"] == 0

    # Ledger must contain NO_STATE_CHANGE record
    no_effect_entries = [e for e in result["evidence_ledger"] if e.evidence_type == "NO_STATE_CHANGE"]
    assert len(no_effect_entries) == 1
    assert "no meaningful UI state change" in no_effect_entries[0].summary


def test_same_state_repeated_observations_deduplicated():
    """Identical DOM observations on the same route must compute identical state IDs and deduplicate."""
    url = "https://www.saucedemo.com/cart.html"
    headings = ["Your Cart"]
    signals = {"has_item_list": True, "counter_value": "1", "menu_open": False}
    forms = []

    id_1 = StatefulBrowserSession.compute_state_identity(url, headings, signals, forms)
    id_2 = StatefulBrowserSession.compute_state_identity(url, headings, signals, forms)

    assert id_1 == id_2

    states_dict = {}
    states_dict[id_1] = "obs_1"
    states_dict[id_2] = "obs_2"
    assert len(states_dict) == 1


def test_action_and_transition_metrics_remain_distinct():
    """
    Overview metrics must accurately distinguish actions evaluated, attempted,
    successful, verified effects, and route vs same-route transitions.
    """
    overview = ReportOverview(
        target_url="https://www.saucedemo.com/",
        investigation_id="inv-test-02",
        start_time="2026-10-21 10:00:00",
        end_time="2026-10-21 10:00:15",
        status=InvestigationStatus.COMPLETED,
        pages_discovered=4,
        meaningful_states_discovered=5,
        candidates_discovered=50,
        actions_evaluated=12,
        actions_attempted=10,
        actions_successful=9,
        actions_verified_effects=8,
        actions_failed=1,
        actions_safety_blocked=2,
        transitions_observed=8,
        route_transitions_observed=5,
        same_route_state_transitions=3,
        in_page_mutations_observed=3,
        pending_frontier_count=38,
        evidence_ledger_count=15,
        stopping_reason="Budget exhausted",
    )

    # Relationships must hold without forcing values equal
    assert overview.actions_evaluated == overview.actions_attempted + overview.actions_safety_blocked
    assert overview.actions_attempted == overview.actions_successful + overview.actions_failed
    assert overview.transitions_observed == overview.route_transitions_observed + overview.same_route_state_transitions
    assert overview.actions_verified_effects == overview.transitions_observed
    assert overview.actions_verified_effects < overview.actions_successful  # 1 successful action had no effect
    assert overview.actions_attempted < overview.actions_evaluated         # 2 candidates were blocked before dispatch


def test_evidence_references_link_transition_to_observations():
    """Confirmed transitions must link source and destination observation IDs and evidence IDs."""
    orch = InvestigationOrchestrator(headless=True)
    obs1 = UIStateObservation(
        state_id="st_inventory_products_catalog_c0",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="saucedemo.com/inventory.html",
        page_title="Products",
        headings=["Products"],
        screenshot_artifact_id="shot_inv.png",
    )
    obs2 = UIStateObservation(
        state_id="st_cart_your_cart_itemlist_c1",
        url="https://www.saucedemo.com/cart.html",
        normalized_route="saucedemo.com/cart.html",
        page_title="Your Cart",
        headings=["Your Cart"],
        screenshot_artifact_id="shot_cart.png",
    )

    orch.session.capture_observation = MagicMock(return_value=obs2)

    state = {
        "investigation_id": "inv-test-03",
        "current_state": obs1,
        "states": {obs1.state_id: obs1},
        "transitions": [],
        "evidence_ledger": [],
        "actions_attempted": 1,
        "actions_successful": 1,
        "actions_verified_effects": 0,
        "route_transitions_observed": 0,
        "same_route_state_transitions": 0,
        "action_execution_result": {
            "success": True,
            "outcome": "Navigated to cart view",
            "action_type": "CLICK_CONTROL",
            "target": '[data-test="shopping-cart-link"]',
        },
        "proposed_action": {"rationale": "Open shopping cart"},
    }

    result = orch.node_verify_action_outcome(state)

    assert len(result["transitions"]) == 1
    tr = result["transitions"][0]
    assert tr.source_state_id == "st_inventory_products_catalog_c0"
    assert tr.destination_state_id == "st_cart_your_cart_itemlist_c1"
    assert tr.source_observation_id == "shot_inv.png"
    assert tr.destination_observation_id == "shot_cart.png"
    assert tr.is_route_change is True
    assert len(tr.evidence_ids) == 1

    ev = result["evidence_ledger"][-1]
    assert ev.evidence_id == tr.evidence_ids[0]
    assert tr.transition_id in ev.related_transition_ids


def test_dashboard_metric_to_label_mapping():
    """Dashboard summary mapping must never assign states to headings, actions to buttons, or transitions to links."""
    states_dict = {
        "st_1": UIStateObservation(
            state_id="st_1",
            url="https://www.saucedemo.com/",
            normalized_route="saucedemo.com/",
            page_title="Login",
            headings=["Swag Labs"],
            interactive_controls=[],
            forms=[{"form_id": "login", "fields": [{"name": "u"}, {"name": "p"}]}],
            navigation_links=[{"text": "L1", "href": "/a"}],
        ),
        "st_2": UIStateObservation(
            state_id="st_2",
            url="https://www.saucedemo.com/inventory.html",
            normalized_route="saucedemo.com/inventory.html",
            page_title="Inventory",
            headings=["Products", "Subheading"],
            interactive_controls=[],
            forms=[],
            navigation_links=[{"text": "L2", "href": "/b"}, {"text": "L3", "href": "/c"}],
        ),
    }

    state = {
        "target_url": "https://www.saucedemo.com/",
        "investigation_id": "inv-map-01",
        "start_time": 1000.0,
        "states": states_dict,
        "transitions": [],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
        "actions_attempted": 5,
        "actions_successful": 5,
        "actions_verified_effects": 4,
        "actions_failed": 0,
        "actions_safety_blocked": 0,
    }

    report = InvestigationReportBuilder.build_report(state)

    # Actual observed counts across states:
    # Headings: 1 ("Swag Labs") + 2 ("Products", "Subheading") = 3
    # Forms: 1 (login form)
    # Inputs: 2 (fields u and p)
    # Links: 3 (L1, L2, L3)
    assert report.overview.total_headings_discovered == 3
    assert report.overview.total_forms_discovered == 1
    assert report.overview.total_inputs_discovered == 2
    assert report.overview.total_links_discovered == 3
    assert report.overview.meaningful_states_discovered == 2

    # Verification: total headings is NOT states count
    assert report.overview.total_headings_discovered != report.overview.meaningful_states_discovered
    # Verification: total buttons/links is NOT transitions count
    assert report.overview.total_links_discovered != report.overview.transitions_observed


def test_missing_evidence_represented_as_unavailable_or_uncertain():
    """Rules and hypotheses must distinguish 100% verified facts from inferred hypotheses with lower confidence."""
    fact = RuleOrHypothesis(
        rule_id="RULE-FACT-001",
        description="Password input field masked by type='password'",
        category=RuleCategory.OBSERVED_BEHAVIOR,
        source="DOM Input Attribute",
        confidence=1.0,
        limitations="Client-side DOM inspection only",
    )

    hypo = RuleOrHypothesis(
        rule_id="HYPO-SPEC-001",
        description="Shopping cart state persists in localStorage across reloads",
        category=RuleCategory.INFERRED_HYPOTHESIS,
        source="Structural inference",
        confidence=0.75,
        limitations="Session reload test not executed",
    )

    assert fact.confidence == 1.0
    assert hypo.confidence < 1.0
    assert hypo.category == RuleCategory.INFERRED_HYPOTHESIS


def test_investigation_only_isolation():
    """Investigation report builder must never produce test cases, test suites, or bug verdicts."""
    state = {
        "target_url": "https://www.saucedemo.com/",
        "investigation_id": "inv-iso-01",
        "start_time": 1000.0,
        "states": {},
        "transitions": [],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }

    report = InvestigationReportBuilder.build_report(state)
    report_dict = report.model_dump()

    # Must NOT have downstream QA properties
    assert "test_cases" not in report_dict
    assert "test_suites" not in report_dict
    assert "bug_reports" not in report_dict
    assert "defects" not in report_dict
    assert "test_plan" not in report_dict
