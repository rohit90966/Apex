"""
Targeted regression and generalization tests for the Advanced Stateful Investigation Agent.
Covers all 8 Task 5 mandated verification areas:
1. Checkout transition followed by separate return-to-cart transition.
2. Meaningful same-URL state change (e.g. empty vs populated cart).
3. Stable identity under irrelevant DOM volatility (timestamps, dynamic ads, non-semantic text).
4. Generic frontier prioritization without SauceDemo-specific keywords.
5. Accurate primary-path versus exploratory-branch reporting.
6. Honest budget-exhaustion reporting.
7. Evidence traceability for confirmed transitions.
8. Investigation-only execution-path isolation.
"""

import pytest
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    FrontierStatus,
    InvestigationStatus,
    StateTransitionRecord,
    EvidenceEntry,
)
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.state import InvestigationAgentState


def test_state_identity_same_url_different_state():
    """AC-01: Meaningfully different UI conditions at same URL produce distinct state IDs."""
    obs_empty = StatefulBrowserSession.compute_state_identity(
        url="https://app.example.com/products",
        headings=["Catalog Products"],
        dynamic_signals={"counter_value": "0", "has_catalog_grid": True},
        forms=[]
    )
    obs_populated = StatefulBrowserSession.compute_state_identity(
        url="https://app.example.com/products",
        headings=["Catalog Products"],
        dynamic_signals={"counter_value": "2", "has_catalog_grid": True},
        forms=[]
    )
    assert obs_empty != obs_populated
    assert "catalog_c0" in obs_empty
    assert "catalog_c2" in obs_populated


def test_state_identity_stability_under_irrelevant_volatility():
    """AC-02: Equivalent observations with volatile noise (timestamps, numbers) produce identical IDs."""
    id1 = StatefulBrowserSession.compute_state_identity(
        url="https://app.example.com/dashboard?session_id=12345&time=998877",
        headings=["Overview Dashboard 2026-10-21 14:00:00"],
        dynamic_signals={"has_catalog_grid": True, "counter_value": "0"},
        forms=[]
    )
    id2 = StatefulBrowserSession.compute_state_identity(
        url="https://app.example.com/dashboard?session_id=99999&time=112233",
        headings=["Overview Dashboard 2026-10-21 15:30:00"],
        dynamic_signals={"has_catalog_grid": True, "counter_value": "0"},
        forms=[]
    )
    # The cleaned state ID must be identical despite query parameters and timestamp shifts in the heading
    assert id1 == id2


def test_generic_frontier_prioritization_without_hardcoded_keywords():
    """Task 3 & 5.4: Generic prioritization operates on workflow progression tokens."""
    frontier = {}
    obs = UIStateObservation(
        state_id="st_portal_dashboard",
        url="https://enterprise.local/dashboard",
        normalized_route="enterprise.local/dashboard",
        page_title="Enterprise Dashboard",
        interactive_controls=[
            ElementReference(
                element_id="btn_continue",
                role="button",
                accessible_name="Proceed to Next Step",
                tag_name="button",
                selector="button.btn-proceed",
            ),
            ElementReference(
                element_id="btn_cancel",
                role="button",
                accessible_name="Cancel / Reset",
                tag_name="button",
                selector="button.btn-reset",
            ),
            ElementReference(
                element_id="tab_settings",
                role="tab",
                accessible_name="Security Settings",
                tag_name="a",
                selector="a#tab-settings",
            )
        ],
        dynamic_signals={"counter_value": "3"}
    )

    FrontierManager.populate_from_observation(frontier, obs, [])
    cand = FrontierManager.select_next_candidate(frontier, "st_portal_dashboard")

    # Workflow progression (Proceed) must be prioritized over Cancel/Reset
    assert cand is not None
    assert "proceed" in cand.rationale.lower()
    assert cand.priority_score > 3.0


def test_primary_path_vs_exploratory_branch_reporting():
    """Task 5.1 & 5.5: Verified distinction of primary progression vs return branches."""
    tr1 = StateTransitionRecord(
        source_state_id="st_cart",
        action_type="CLICK_CONTROL",
        target_element_desc="button#checkout",
        action_purpose="Proceed to checkout",
        actual_outcome="Navigated to checkout-step-one.html",
        destination_state_id="st_checkout_step_1",
        evidence_ids=["ev-1"],
    )
    tr2 = StateTransitionRecord(
        source_state_id="st_checkout_step_1",
        action_type="CLICK_CONTROL",
        target_element_desc="a.shopping_cart_link",
        action_purpose="Return to cart view",
        actual_outcome="Navigated back to cart.html",
        destination_state_id="st_cart",
        evidence_ids=["ev-2"],
    )

    state: InvestigationAgentState = {
        "target_url": "https://example.com",
        "investigation_id": "inv-test-branch",
        "start_time": 1700000000.0,
        "investigation_status": InvestigationStatus.COMPLETED,
        "stopping_reason": "Budget completed",
        "states": {},
        "transitions": [tr1, tr2],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }

    report = InvestigationReportBuilder.build_report(state)
    assert len(report.user_workflows) == 2
    # First step advances to new state -> Primary Workflow
    assert "[Primary Workflow]" in report.user_workflows[0].action_taken
    # Second step returns to an already seen state (st_cart) -> Exploratory / Return Branch
    assert "[Exploratory / Return Branch]" in report.user_workflows[1].action_taken


def test_honest_budget_exhaustion_reporting():
    """Task 5.6: Stopping because of budget exhaustion is distinct from genuine frontier exhaustion."""
    from app.investigation_agent.workflow import InvestigationOrchestrator

    state_budget_exhausted: InvestigationAgentState = {
        "actions_attempted": 10,
        "max_actions": 10,
        "start_time": 1700000000.0,
        "states": {},
    }
    orc = InvestigationOrchestrator(headless=True)
    res = orc.node_evaluate_stopping_conditions(state_budget_exhausted)
    orc.close()

    assert "stopping_reason" in res
    assert "Action budget exhausted" in res["stopping_reason"]


def test_evidence_traceability_for_confirmed_transitions():
    """Task 5.7: Confirmed transitions link directly to valid evidence ledger IDs."""
    ev = EvidenceEntry(
        investigation_id="inv-trace-1",
        source_state_id="st_1",
        evidence_type="STATE_TRANSITION",
        summary="Executed Add to Cart",
        screenshot_artifact_id="screenshot-test.png",
    )
    tr = StateTransitionRecord(
        source_state_id="st_1",
        action_type="CLICK_CONTROL",
        target_element_desc="btn-add",
        action_purpose="Add product",
        actual_outcome="Cart badge updated",
        destination_state_id="st_2",
        evidence_ids=[ev.evidence_id],
    )

    state: InvestigationAgentState = {
        "target_url": "https://example.com",
        "investigation_id": "inv-trace-1",
        "start_time": 1700000000.0,
        "investigation_status": InvestigationStatus.COMPLETED,
        "stopping_reason": "Completed",
        "states": {},
        "transitions": [tr],
        "frontier": {},
        "evidence_ledger": [ev],
        "rules_and_hypotheses": [],
    }

    report = InvestigationReportBuilder.build_report(state)
    assert len(report.evidence_and_traceability) == 1
    assert report.evidence_and_traceability[0]["evidence_id"] == ev.evidence_id
    assert report.user_workflows[0].evidence_id == ev.evidence_id


def test_investigation_only_isolation_contract():
    """Task 5.8: Investigation output contains NO test cases, test suites, or bug reports."""
    state: InvestigationAgentState = {
        "target_url": "https://example.com",
        "investigation_id": "inv-pure",
        "start_time": 1700000000.0,
        "investigation_status": InvestigationStatus.COMPLETED,
        "stopping_reason": "Completed",
        "states": {},
        "transitions": [],
        "frontier": {},
        "evidence_ledger": [],
        "rules_and_hypotheses": [],
    }
    report = InvestigationReportBuilder.build_report(state)
    rep_dict = report.model_dump()
    assert "test_cases" not in rep_dict
    assert "bug_reports" not in rep_dict
    assert "execution_results" not in rep_dict
    assert "test_suites" not in rep_dict
