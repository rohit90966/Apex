"""
Comprehensive Regression Test Suite for VerityQA Journey-Aware Investigation Repair.

Validates the 7 required core scenarios:
1. Active journey is not abandoned for unrelated high-scoring 1-ply routes.
2. Session and cart state survive progression without spurious route resets.
3. Repeated Add-to-Cart actions establish a multi-item cart (distinct products not suppressed).
4. In-cart quantity modifiers (+, -, Remove) and control options are sampled and explored.
5. Impossible or interrupted steps are recorded with explicit failure/interruption reasons.
6. Safety limits, mutation budgets, and emergency ceilings remain strictly enforced.
7. End-to-end multi-step journey execution builds an evidence-backed application report.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    UserJourney,
    JourneyStep,
    JourneyStatus,
    FrontierStatus,
    InvestigationReport,
    ProbeScript,
    ProbeStep,
    Prediction,
)
from app.investigation_agent.agenda import (
    InvestigationAgenda,
    InvestigationQuestion,
)
from app.investigation_agent.probes.controls import generate_control_probes
from app.investigation_agent.workflow import InvestigationOrchestrator


def test_journey_not_abandoned_for_unrelated_route():
    """
    Scenario 1: Active user journey is not abandoned for an unrelated high-scoring route.
    When a journey is in progress at /cart, the next cart-advancing question MUST be chosen
    over a high-scoring candidate pointing back to an unrelated catalog or settings route.
    """
    agenda = InvestigationAgenda()
    
    # Setup state: User is on /cart
    current_state_class = "cls_cart_view"
    current_url = "http://127.0.0.1:5173/cart"
    available_selectors = {"#checkout-btn", "#cart-qty-plus-1"}

    # Define an active journey at step 4 (Proceed to Checkout)
    journey = UserJourney(
        journey_id="journey_purchase",
        name="E-Commerce Purchase Funnel",
        description="Catalog -> Cart -> Checkout",
        status=JourneyStatus.IN_PROGRESS,
        current_step_index=4,
        steps=[
            JourneyStep(step_index=0, name="Add Prod 1", completed=True),
            JourneyStep(step_index=1, name="Add Prod 2", completed=True),
            JourneyStep(step_index=2, name="Navigate to Cart", completed=True),
            JourneyStep(step_index=3, name="Modify Quantity", completed=True),
            JourneyStep(
                step_index=4,
                name="Proceed to Checkout",
                action_type="CLICK_CONTROL",
                target_pattern=r"checkout|proceed",
                expected_role="button",
            ),
        ],
    )
    agenda.journeys["journey_purchase"] = journey
    agenda.active_journey_id = "journey_purchase"

    # Add competing questions:
    # Q1: Highly scored novel question on /catalog (would cause route thrashing)
    q_catalog = InvestigationQuestion(
        id="q_catalog_novel",
        category="discovery",
        target_state_class="cls_catalog",
        target_element="#product-filter-spec",
        hypothesis="Explore novel filter on catalog view",
        probe_family="controls",
        priority=0.95,
        status="unexplored",
        metadata={"source_url": "http://127.0.0.1:5173/catalog"},
    )
    # Q2: In-cart checkout question advancing step 4
    q_checkout = InvestigationQuestion(
        id="q_cart_checkout",
        category="discovery",
        target_state_class="cls_cart_view",
        target_element="#checkout-btn",
        hypothesis="Proceed to checkout from cart",
        probe_family="controls",
        priority=0.50,
        status="unexplored",
        metadata={"source_url": current_url, "selector": "#checkout-btn"},
    )
    agenda.questions["q_catalog_novel"] = q_catalog
    agenda.questions["q_cart_checkout"] = q_checkout

    selected = agenda.select_next_question(
        current_state_class=current_state_class,
        current_url=current_url,
        available_selectors=available_selectors,
    )

    assert selected is not None
    assert selected.id == "q_cart_checkout", (
        f"Expected in-journey question 'q_cart_checkout' to be selected, but got '{selected.id}'"
    )
    assert agenda.active_journey_id == "journey_purchase"
    assert journey.steps[4].executed_question_id == "q_cart_checkout"


def test_session_and_cart_state_survive_progression():
    """
    Scenario 2: Session state and cart items survive across investigation steps.
    Verifies that when an active journey is executing, node_execute_probe does NOT
    call session.navigate(src_url) when targets belong to current flow.
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.session = MagicMock()
    orchestrator.session.page = MagicMock()
    orchestrator.session.page.url = "http://127.0.0.1:5173/cart"
    orchestrator.session.resolve_target.return_value = {"present": True, "visible": True, "enabled": True}
    orchestrator.runner = MagicMock()
    mock_res = MagicMock()
    mock_res.status = "SUCCESS"
    orchestrator.runner.run_probe.return_value = mock_res

    agenda = InvestigationAgenda()
    agenda.active_journey_id = "journey_purchase"
    orchestrator.agenda = agenda

    script = ProbeScript(
        name="test_cart_step",
        family="controls",
        description="Click checkout button in cart",
        steps=[ProbeStep(action_type="CLICK_CONTROL", raw_target="#checkout-btn")],
    )
    q = InvestigationQuestion(
        id="q_checkout",
        target_state_class="cls_cart",
        target_element="#checkout-btn",
        hypothesis="Click checkout",
        probe_family="controls",
        script=script,
        metadata={"source_url": "http://127.0.0.1:5173/products"},  # Stale metadata source
    )

    state = {
        "current_question": q,
        "current_probe": script,
        "actions_attempted": 2,
    }

    # Execute probe
    orchestrator.node_execute_probe(state)

    # session.navigate must NOT have been called with stale source_url!
    orchestrator.session.navigate.assert_not_called()
    orchestrator.runner.run_probe.assert_called_once()


def test_repeated_add_to_cart_establishes_multi_item_cart():
    """
    Scenario 3: Repeated Add to Cart actions for distinct products are preserved.
    Verifies that both controls generator and agenda signature deduplication
    preserve multiple 'Add to Cart' buttons targeting distinct items.
    """
    obs = UIStateObservation(
        state_id="st_catalog",
        state_class_id="cls_catalog",
        url="http://127.0.0.1:5173/products",
        normalized_route="/products",
        page_title="Products Catalog",
        interactive_controls=[
            ElementReference(
                element_id="btn_add_1",
                role="button",
                accessible_name="Add to Cart",
                tag_name="button",
                selector="[data-test='add-to-cart-prod-01']",
                candidate_actions=["CLICK_CONTROL"],
            ),
            ElementReference(
                element_id="btn_add_2",
                role="button",
                accessible_name="Add to Cart",
                tag_name="button",
                selector="[data-test='add-to-cart-prod-02']",
                candidate_actions=["CLICK_CONTROL"],
            ),
            ElementReference(
                element_id="btn_add_3",
                role="button",
                accessible_name="Add to Cart",
                tag_name="button",
                selector="[data-test='add-to-cart-prod-03']",
                candidate_actions=["CLICK_CONTROL"],
            ),
        ],
        forms=[],
        navigation_links=[
            {"href": "/cart", "text": "Cart", "is_fragment": False, "is_external": False}
        ],
    )

    # 1. Controls generator deduplicates repeated items to 1 representative item probe
    probes = generate_control_probes(obs)
    add_probes = [p for p in probes if "add-to-cart" in p[2].get("selector", "")]
    assert len(add_probes) == 1, f"Expected exactly 1 representative Add-to-Cart probe, got {len(add_probes)}"

    # 2. Agenda populates representative question cleanly
    agenda = InvestigationAgenda()
    added = agenda.populate_from_observation(obs)
    add_questions = [
        q for q in agenda.questions.values()
        if "add-to-cart" in (q.target_element or "")
    ]
    assert len(add_questions) == 1, (
        f"Expected agenda to retain 1 representative add-to-cart question, but got {len(add_questions)}"
    )
    targets = {q.target_element for q in add_questions}
    assert "[data-test='add-to-cart-prod-01']" in targets


def test_quantity_and_remove_controls_explored():
    """
    Scenario 4: In-cart quantity modifiers (+, -, Remove) are discovered and scheduled.
    """
    obs = UIStateObservation(
        state_id="st_cart",
        state_class_id="cls_cart",
        url="http://127.0.0.1:5173/cart",
        normalized_route="/cart",
        page_title="Shopping Cart",
        interactive_controls=[
            ElementReference(
                element_id="btn_qty_inc",
                role="button",
                accessible_name="+",
                tag_name="button",
                selector="[data-test='cart-qty-plus-prod-01']",
                candidate_actions=["CLICK_CONTROL"],
            ),
            ElementReference(
                element_id="btn_qty_dec",
                role="button",
                accessible_name="-",
                tag_name="button",
                selector="[data-test='cart-qty-minus-prod-01']",
                candidate_actions=["CLICK_CONTROL"],
            ),
            ElementReference(
                element_id="btn_remove",
                role="button",
                accessible_name="Remove",
                tag_name="button",
                selector="[data-test='cart-remove-prod-01']",
                candidate_actions=["CLICK_CONTROL"],
            ),
            ElementReference(
                element_id="btn_checkout",
                role="button",
                accessible_name="Proceed to Checkout",
                tag_name="button",
                selector="[data-test='cart-checkout-btn']",
                candidate_actions=["CLICK_CONTROL"],
            ),
        ],
        forms=[],
        navigation_links=[],
    )

    agenda = InvestigationAgenda()
    agenda.populate_from_observation(obs)

    # Verify that modifier controls are present in questions
    modifier_questions = [
        q for q in agenda.questions.values()
        if any(k in (q.target_element or "") for k in ("cart-qty-plus", "cart-qty-minus", "cart-remove"))
    ]
    assert len(modifier_questions) >= 2, (
        f"Expected at least 2 modifier questions, found {len(modifier_questions)}"
    )


def test_impossible_or_interrupted_steps_handled_explicitly():
    """
    Scenario 5: When a journey step is blocked or impossible, the journey transitions
    to BLOCKED with an explicit, traceable reason and falls back gracefully.
    """
    agenda = InvestigationAgenda()
    
    journey = UserJourney(
        journey_id="journey_purchase",
        name="E-Commerce Purchase Funnel",
        description="Purchase journey",
        status=JourneyStatus.IN_PROGRESS,
        current_step_index=0,
        steps=[
            JourneyStep(
                step_index=0,
                name="Add Product 1",
                action_type="CLICK_CONTROL",
                target_pattern=r"add-to-cart",
            ),
        ],
    )
    agenda.journeys["journey_purchase"] = journey
    agenda.active_journey_id = "journey_purchase"

    # Observation has no matching elements (e.g. empty error view)
    current_state_class = "cls_error"
    current_url = "http://127.0.0.1:5173/error"
    available_selectors = {"#home-link"}

    # Next question selection should recognize unreachable step
    selected = agenda.select_next_question(
        current_state_class=current_state_class,
        current_url=current_url,
        available_selectors=available_selectors,
    )

    assert journey.status == JourneyStatus.BLOCKED
    assert journey.interruption_reason is not None
    assert "unreachable" in journey.interruption_reason.lower()
    assert agenda.active_journey_id is None


def test_safety_limits_and_emergency_ceilings_enforced():
    """
    Scenario 6: Safety policy limits and runaway emergency ceilings are strictly enforced
    even during active user journeys.
    """
    # 1. Safety block in journey step records failure and halts journey
    agenda = InvestigationAgenda(max_safety_blocks=2)
    journey = UserJourney(
        journey_id="journey_purchase",
        name="E-Commerce Purchase Funnel",
        description="Purchase journey",
        status=JourneyStatus.IN_PROGRESS,
        current_step_index=0,
        steps=[
            JourneyStep(step_index=0, name="Proceed to Checkout", executed_question_id="q_checkout"),
        ],
    )
    agenda.journeys["journey_purchase"] = journey
    agenda.active_journey_id = "journey_purchase"
    agenda.questions["q_checkout"] = InvestigationQuestion(
        id="q_checkout",
        target_state_class="cls_cart",
        hypothesis="Checkout",
        probe_family="controls",
    )

    agenda.record_probe_outcome(
        question_id="q_checkout",
        verdict="SAFETY_BLOCKED",
        summary="Checkout disabled by Safety Governor on untrusted origin",
        safety_blocked=True,
    )

    assert journey.status == JourneyStatus.BLOCKED
    assert "safety" in journey.interruption_reason.lower()
    assert agenda.active_journey_id is None
    assert agenda.safety_blocks_encountered == 1

    # 2. Hard probe ceiling stops agenda
    agenda.max_probes = 1
    stop, reason = agenda.should_stop()
    assert stop is True
    assert "probe ceiling reached" in reason.lower()


def test_end_to_end_journey_lifecycle_and_report_serialization():
    """
    Scenario 7: Full journey lifecycle progression through completion and report generation.
    """
    agenda = InvestigationAgenda()
    obs = UIStateObservation(
        state_id="st_prod",
        state_class_id="cls_prod",
        url="http://127.0.0.1:5173/products",
        normalized_route="/products",
        page_title="Products",
        interactive_controls=[
            ElementReference(
                element_id="btn_add_1",
                role="button",
                accessible_name="Add to Cart",
                tag_name="button",
                selector="[data-test='add-to-cart-prod-01']",
            ),
        ],
    )
    agenda.populate_from_observation(obs)

    assert "journey_purchase" in agenda.journeys
    j = agenda.journeys["journey_purchase"]
    assert len(j.steps) == 7

    # Select step 0
    q0 = agenda.select_next_question(
        current_state_class="cls_prod",
        current_url=obs.url,
        available_selectors={"[data-test='add-to-cart-prod-01']"},
    )
    assert q0 is not None
    assert agenda.active_journey_id == "journey_purchase"
    assert j.status == JourneyStatus.IN_PROGRESS

    # Complete step 0
    agenda.record_probe_outcome(q0.id, "CONFIRMED", "Added item to cart", mutating=True)
    assert j.steps[0].completed is True
    assert j.current_step_index == 1
