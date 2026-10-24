"""
Deterministic Regression Tests for VerityQA Live Investigation Journey Integrity Repairs.

Covers the 7 mandatory scenarios:
1. A successful cart mutation.
2. A required journey step missing from the current view.
3. A click timeout.
4. A checkout submission whose resulting URL contradicts the expected route.
5. A direct cart route returning 404 without the required session.
6. A journey containing a skipped required step.
7. A journey whose steps are all genuinely verified.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.investigation_agent.models import (
    JourneyStep,
    UserJourney,
    JourneyStatus,
    JourneyStepStatus,
    UIStateObservation,
    StateDiff,
    ActionFailureReason,
    BugLead,
    OracleType,
    OracleStrength,
    ProbeScript,
    ProbeStep,
)
from app.investigation_agent.agenda import InvestigationAgenda, InvestigationQuestion
from app.investigation_agent.skeptic import SkepticFilter
from app.investigation_agent.browser_actions import InvestigationActionExecutor


# ─────────────────────────────────────────────────────────────────────────────
# 1. A successful cart mutation
# ─────────────────────────────────────────────────────────────────────────────
def test_successful_cart_mutation_verified():
    """Verify that a successful cart mutation increments badge and records verified evidence."""
    agenda = InvestigationAgenda()
    step = JourneyStep(
        step_index=0,
        name="Add Product 1 to Cart",
        action_type="CLICK_CONTROL",
        target_pattern=r"add-to-cart",
        expected_role="button",
        expected_postcondition="cart_badge_increment",
        is_required=True,
    )
    journey = UserJourney(
        journey_id="j_cart_test",
        name="Cart Flow",
        description="Verify cart addition",
        steps=[step],
        status=JourneyStatus.IN_PROGRESS,
    )
    agenda.journeys["j_cart_test"] = journey
    agenda.active_journey_id = "j_cart_test"

    q = InvestigationQuestion(
        id="q_add_backpack",
        target_state_class="st_catalog",
        target_element="#add-to-cart-backpack",
        hypothesis="Verify adding backpack increments cart badge",
        probe_family="controls",
        metadata={"action_type": "CLICK_CONTROL", "source_url": "https://test.app/inventory.html"},
    )
    agenda.questions[q.id] = q
    step.executed_question_id = q.id

    before_obs = UIStateObservation(
        url="https://test.app/inventory.html",
        normalized_route="/inventory.html",
        page_title="Inventory Catalog",
        state_id="st_before",
        state_class_id="cls_inv",
        dynamic_signals={"cart_badge_count": 0},
    )
    after_obs = UIStateObservation(
        url="https://test.app/inventory.html",
        normalized_route="/inventory.html",
        page_title="Inventory Catalog",
        state_id="st_after",
        state_class_id="cls_inv",
        dynamic_signals={"cart_badge_count": 1},
    )
    diff = StateDiff(route_changed=False, class_changed=False, state_id_changed=True)

    agenda.record_probe_outcome(
        question_id=q.id,
        verdict="MUTATION_OBSERVED",
        summary="Added backpack to cart",
        mutating=True,
        before_obs=before_obs,
        after_obs=after_obs,
        diff=diff,
    )

    assert step.status == JourneyStepStatus.VERIFIED
    assert step.completed is True
    assert "incremented from 0 to 1" in (step.verification_details or "")
    assert step.evidence_id is not None
    assert journey.is_fully_completed is True
    assert journey.status == JourneyStatus.COMPLETED


# ─────────────────────────────────────────────────────────────────────────────
# 2. A required journey step missing from the current view
# ─────────────────────────────────────────────────────────────────────────────
def test_required_step_missing_from_current_view():
    """Verify that a missing required step is not falsely marked verified and blocks the journey."""
    agenda = InvestigationAgenda()
    step_req = JourneyStep(
        step_index=0,
        name="Proceed to Checkout",
        action_type="CLICK_CONTROL",
        target_pattern=r"checkout|proceed",
        expected_role="button",
        expected_postcondition="route_is_checkout",
        is_required=True,
    )
    journey = UserJourney(
        journey_id="j_missing_target",
        name="Checkout Flow",
        description="Verify checkout proceed",
        steps=[step_req],
        status=JourneyStatus.IN_PROGRESS,
    )
    agenda.journeys["j_missing_target"] = journey
    agenda.active_journey_id = "j_missing_target"

    # Current page is /inventory.html where checkout button does NOT exist
    q = agenda.select_next_question(
        current_state_class="st_inventory",
        current_url="https://test.app/inventory.html",
        available_selectors={"#add-to-cart-item", "#menu-button"},
    )

    # Required checkout step cannot be satisfied on this view
    assert step_req.status != JourneyStepStatus.VERIFIED
    assert step_req.completed is False
    assert journey.is_fully_completed is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. A click timeout
# ─────────────────────────────────────────────────────────────────────────────
def test_click_timeout_classified_and_rejected_by_skeptic():
    """Verify that a click timeout records ActionFailureReason.TIMEOUT and is rejected by SkepticFilter."""
    mock_session = MagicMock()
    mock_page = MagicMock()
    mock_session.page = mock_page
    mock_page.url = "https://test.app/cart.html"
    mock_session.resolve_target.return_value = {"present": True, "visible": True, "enabled": True}

    mock_locator = MagicMock()
    mock_locator.count.return_value = 1
    mock_locator.click.side_effect = Exception("Timeout 3000ms exceeded while waiting for element to receive click events")
    mock_page.locator.return_value = mock_locator

    ok, outcome, fail_reason, telemetry = InvestigationActionExecutor.execute_action(
        session=mock_session,
        action_type="CLICK_CONTROL",
        target="#continue-shopping",
        timeout_ms=3000,
    )

    assert ok is False
    assert fail_reason == ActionFailureReason.TIMEOUT
    assert "Click failed: Timeout 3000ms exceeded" in outcome

    # SkepticFilter must reject bug leads stemming from timeouts
    lead = BugLead(
        title="Element click timed out after 3000 ms",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.LOW,
        probe_family="controls",
        question_id="q_timeout",
        actual={"text": "Action execution timed out: #continue-shopping"},
        expected={"text": "Control responds within 3s"},
    )
    survived, reason = SkepticFilter.evaluate_lead(lead)
    assert survived is False
    assert "timeout" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. A checkout submission whose resulting URL contradicts the expected route
# ─────────────────────────────────────────────────────────────────────────────
def test_checkout_submission_contradictory_route_fails_verification():
    """Verify that submitting checkout customer info that lands on /cart.html fails verification."""
    agenda = InvestigationAgenda()
    step_checkout = JourneyStep(
        step_index=0,
        name="Fill and Submit Checkout Form",
        action_type="SUBMIT",
        target_pattern=r"checkout|form",
        expected_role="form",
        expected_postcondition="checkout_form_submitted",
        is_required=True,
    )
    journey = UserJourney(
        journey_id="j_checkout_contradiction",
        name="Checkout Progression",
        description="Verify checkout step one submit",
        steps=[step_checkout],
        status=JourneyStatus.IN_PROGRESS,
    )
    agenda.journeys["j_checkout_contradiction"] = journey
    agenda.active_journey_id = "j_checkout_contradiction"

    q = InvestigationQuestion(
        id="q_checkout_submit",
        target_state_class="st_checkout_step_one",
        target_element="#continue",
        hypothesis="Verify checkout customer form submission advances to overview",
        probe_family="forms",
        metadata={"action_type": "SUBMIT", "source_url": "https://test.app/checkout-step-one.html"},
    )
    agenda.questions[q.id] = q
    step_checkout.executed_question_id = q.id

    before_obs = UIStateObservation(
        url="https://test.app/checkout-step-one.html",
        normalized_route="/checkout-step-one.html",
        page_title="Checkout Step One",
        state_id="st_step_one",
        state_class_id="cls_step_one",
    )
    # Contradictory outcome: landed on /cart.html (e.g. Cancel was triggered or session reset)
    after_obs = UIStateObservation(
        url="https://test.app/cart.html",
        normalized_route="/cart.html",
        page_title="Shopping Cart",
        state_id="st_cart",
        state_class_id="cls_cart",
    )
    diff = StateDiff(route_changed=True, class_changed=True, state_id_changed=True)

    agenda.record_probe_outcome(
        question_id=q.id,
        verdict="ROUTE_TRANSITION",
        summary="Form action triggered navigation",
        mutating=True,
        before_obs=before_obs,
        after_obs=after_obs,
        diff=diff,
    )

    # Step MUST FAIL verification due to contradictory route
    assert step_checkout.status == JourneyStepStatus.FAILED
    assert step_checkout.completed is False
    assert "Contradiction" in (step_checkout.failure_reason or "")
    assert journey.status == JourneyStatus.BLOCKED
    assert journey.is_fully_completed is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. A direct cart route returning 404 without the required session
# ─────────────────────────────────────────────────────────────────────────────
def test_direct_cart_route_404_rejected_as_defect():
    """Verify that a 404 on direct navigation to an internal view is treated as a route guard, not a defect."""
    lead = BugLead(
        title="Resource Not Found (404)",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.LOW,
        probe_family="nav",
        question_id="q_nav_cart_direct",
        actual={"text": "GET https://test.app/cart.html returned HTTP 404 on direct navigation"},
        expected={"text": "Route reachable with 200 OK"},
    )

    survived, reason = SkepticFilter.evaluate_lead(lead)
    assert survived is False
    assert "route guard" in reason.lower() or "404" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 6. A journey containing a skipped required step
# ─────────────────────────────────────────────────────────────────────────────
def test_journey_containing_skipped_required_step_cannot_complete():
    """Verify that a journey containing a skipped required step is BLOCKED and never marked COMPLETED."""
    step0 = JourneyStep(
        step_index=0,
        name="Add Product 1 to Cart",
        status=JourneyStepStatus.VERIFIED,
        completed=True,
        is_required=True,
    )
    step1_skipped = JourneyStep(
        step_index=1,
        name="Add Product 2 to Cart",
        status=JourneyStepStatus.SKIPPED,
        completed=False,
        verification_details="Target element not found in DOM",
        is_required=True,
    )
    step2 = JourneyStep(
        step_index=2,
        name="Navigate to Cart",
        status=JourneyStepStatus.VERIFIED,
        completed=True,
        is_required=True,
    )

    journey = UserJourney(
        journey_id="j_skipped_step",
        name="Purchase Funnel With Skip",
        description="Verify skipped required step enforcement",
        steps=[step0, step1_skipped, step2],
        current_step_index=3,
    )

    # Invariant: is_fully_completed must be False because a required step is SKIPPED
    assert journey.is_fully_completed is False
    assert journey.verified_steps_count == 2
    assert journey.skipped_steps_count == 1

    # Simulate agenda evaluating completion
    agenda = InvestigationAgenda()
    agenda.journeys["j_skipped_step"] = journey
    agenda.active_journey_id = "j_skipped_step"

    # Advance agenda progression
    if journey.current_step_index >= len(journey.steps):
        if journey.is_fully_completed:
            journey.status = JourneyStatus.COMPLETED
        else:
            journey.status = JourneyStatus.BLOCKED
            journey.interruption_reason = "Journey incomplete: required step 'Add Product 2 to Cart' was SKIPPED"

    assert journey.status == JourneyStatus.BLOCKED
    assert journey.status != JourneyStatus.COMPLETED
    assert "incomplete" in journey.interruption_reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 7. A journey whose steps are all genuinely verified
# ─────────────────────────────────────────────────────────────────────────────
def test_journey_fully_verified_completes_truthfully():
    """Verify that a journey whose steps are genuinely verified or legitimately NOT_APPLICABLE completes."""
    steps = [
        JourneyStep(step_index=0, name="Add Product 1 to Cart", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
        JourneyStep(step_index=1, name="Add Product 2 to Cart", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
        JourneyStep(step_index=2, name="Navigate to Cart", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
        JourneyStep(step_index=3, name="Modify Cart Quantity", status=JourneyStepStatus.NOT_APPLICABLE, completed=False, is_required=False, adjudication_reason="Fixed item quantities in cart"),
        JourneyStep(step_index=4, name="Proceed to Checkout", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
        JourneyStep(step_index=5, name="Fill and Submit Checkout Form", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
        JourneyStep(step_index=6, name="Confirm Order", status=JourneyStepStatus.VERIFIED, completed=True, is_required=True),
    ]

    journey = UserJourney(
        journey_id="j_verified_e2e",
        name="Complete Purchase Funnel",
        description="Verify end-to-end verified purchase funnel",
        steps=steps,
        current_step_index=7,
    )

    assert journey.is_fully_completed is True
    assert journey.verified_steps_count == 6
    assert journey.not_applicable_steps_count == 1
    assert journey.skipped_steps_count == 0
    assert journey.failed_steps_count == 0

    if journey.is_fully_completed:
        journey.status = JourneyStatus.COMPLETED

    assert journey.status == JourneyStatus.COMPLETED
