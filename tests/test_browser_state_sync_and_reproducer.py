"""
Regression Tests for VerityQA:
Browser-State Synchronization and False Bug Confirmation Repairs.

Mandated Test Scenarios:
1. Attempting an action on the wrong page fails with an unsatisfied precondition error, not a selector timeout.
2. Bug reproducer does not confirm a bug when setup steps fail to reach the target state.
3. Bug reproducer does not confirm an uncaught console error lead when the action times out looking for an element.
4. Delta debugging does not prune prerequisite authentication or navigation steps.
5. Direct navigation to a route returning 404 is handled as a navigation failure and does not trigger spurious defect leads.
6. Checkout form submission requires verified transition to the next checkout step, not merely any DOM mutation.
7. Purchase journey generation does not include "Add Product 2" when only one product is observed.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.investigation_agent.models import (
    BugLead,
    ProbeScript,
    ProbeStep,
    ProbeResult,
    SemanticTarget,
    OracleStrength,
    OracleType,
    ReproductionOutcome,
    ActionFailureReason,
    UIStateObservation,
    ElementReference,
    StateDiff,
    ConsoleOrPageError,
    JourneyStepStatus,
    JourneyStatus,
)
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.reproducer import BugReproducer
from app.investigation_agent.agenda import InvestigationAgenda
from app.investigation_agent.oracles import evaluate_always_on_oracles
from app.investigation_agent.workflow import InvestigationOrchestrator


# --- Scenario 1 ---
def test_action_on_wrong_page_fails_with_precondition_error():
    """1. Attempting an action on the wrong page fails with an unsatisfied precondition error, not a selector timeout."""
    session = MagicMock()
    page = MagicMock()
    page.url = "https://www.saucedemo.com/"
    session.page = page

    # Attempt action that requires /checkout-step-one.html
    success, outcome, fail_reason, telemetry = InvestigationActionExecutor.execute_action(
        session=session,
        action_type="CLICK_CONTROL",
        target="#first-name",
        required_url="https://www.saucedemo.com/checkout-step-one.html",
    )

    assert success is False
    assert fail_reason == ActionFailureReason.PRECONDITION_FAILED
    assert "unsatisfied precondition" in outcome.lower()
    assert fail_reason != ActionFailureReason.TIMEOUT
    # Ensure it failed immediately without waiting for a 3-second timeout
    assert telemetry.get("duration", 0) < 1.0


# --- Scenario 2 ---
def test_bug_reproducer_rejects_when_setup_steps_fail():
    """2. Bug reproducer does not confirm a bug when setup steps fail to reach the target state."""
    reproducer = BugReproducer(headless=True)

    lead = BugLead(
        title="False Checkout Bug",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.HIGH,
        probe_family="forms",
        question_id="q_checkout",
        source_url="https://www.saucedemo.com/checkout-step-one.html",
        steps=[{"action_type": "FILL", "raw_target": "#first-name", "value": "Jane"}],
        setup_steps=[{"action_type": "CLICK_CONTROL", "raw_target": "#broken-setup-button"}],
    )

    # Mock _test_replay_run to simulate setup failure
    with patch.object(
        reproducer,
        "_test_replay_run",
        return_value=(
            False,
            ReproductionOutcome.REPRODUCTION_SETUP_FAILED,
            None,
            "Prerequisite setup step 0 failed: target absent",
        ),
    ):
        result_lead = reproducer.reproduce_lead("https://www.saucedemo.com/", lead)

    assert result_lead.status == "REJECTED"
    assert result_lead.status != "CONFIRMED"
    assert result_lead.reproduction_outcome == ReproductionOutcome.REPRODUCTION_SETUP_FAILED
    assert "setup failed" in result_lead.reproduction_failure_reason.lower()


# --- Scenario 3 ---
def test_bug_reproducer_rejects_console_error_on_selector_timeout():
    """3. Bug reproducer does not confirm an uncaught console error lead when the action times out looking for an element."""
    reproducer = BugReproducer(headless=True)

    lead = BugLead(
        title="Uncaught Console Error in Input Field",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.HIGH,
        probe_family="controls",
        question_id="q_timeout",
        steps=[{"action_type": "CLICK_CONTROL", "raw_target": "#nonexistent-input"}],
    )

    # Create a probe result representing a selector timeout
    failed_probe_res = ProbeResult(
        script_id="probe_test",
        status="FAILED",
        steps_executed=0,
        error_message="Step failed: Timeout 3000ms exceeded waiting for locator('#nonexistent-input')",
        console_errors=[ConsoleOrPageError(seq=1, error_type="console", message="Uncaught Error: fail")],
    )

    with patch.object(
        reproducer,
        "_test_replay_run",
        return_value=(
            False,
            ReproductionOutcome.ACTION_EXECUTION_FAILED,
            failed_probe_res,
            "Probe action execution failed: Timeout 3000ms",
        ),
    ):
        result_lead = reproducer.reproduce_lead("https://www.saucedemo.com/", lead)

    assert result_lead.status == "REJECTED"
    assert result_lead.status != "CONFIRMED"
    assert result_lead.reproduction_outcome == ReproductionOutcome.ACTION_EXECUTION_FAILED
    assert "action execution failed" in result_lead.reproduction_failure_reason.lower()


# --- Scenario 4 ---
def test_delta_debugging_does_not_prune_prerequisites():
    """4. Delta debugging does not prune prerequisite authentication or navigation steps."""
    reproducer = BugReproducer(headless=True)

    lead = BugLead(
        title="Reproducible Bug",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.HIGH,
        probe_family="controls",
        question_id="q_test",
        setup_steps=[
            {"action_type": "FILL", "raw_target": "#user-name", "value": "standard_user", "is_prerequisite": True},
            {"action_type": "CLICK_CONTROL", "raw_target": "#login-button", "is_prerequisite": True},
        ],
        steps=[
            {"action_type": "NAVIGATE_LINK", "raw_target": "/cart.html", "is_prerequisite": True},
            {"action_type": "CLICK_CONTROL", "raw_target": "#unnecessary-button"},
            {"action_type": "CLICK_CONTROL", "raw_target": "#buggy-action"},
        ],
    )

    # Mock _test_replay_run so that removing #unnecessary-button reproduces (CONFIRMED_DEFECT),
    # but removing #buggy-action does NOT reproduce.
    def mock_replay(target_url, script, lead, setup_steps=None):
        step_targets = [s.raw_target for s in script.steps]
        if "#buggy-action" in step_targets:
            return True, ReproductionOutcome.CONFIRMED_DEFECT, MagicMock(), "Defect reproduced"
        return False, ReproductionOutcome.NOT_REPRODUCED, MagicMock(), "Not reproduced"

    with patch.object(reproducer, "_test_replay_run", side_effect=mock_replay):
        min_steps = reproducer.minimize_steps("https://www.saucedemo.com/", lead, lead.steps, setup_steps=lead.setup_steps)

    # #unnecessary-button was pruned
    min_targets = [s.get("raw_target") for s in min_steps]
    assert "#unnecessary-button" not in min_targets

    # Prerequisite NAVIGATE_LINK step was NOT pruned
    assert "/cart.html" in min_targets
    assert "#buggy-action" in min_targets

    # Prerequisite setup steps remain intact
    assert len(lead.setup_steps) == 2
    assert lead.setup_steps[0]["raw_target"] == "#user-name"


# --- Scenario 5 ---
def test_direct_navigation_404_handled_as_navigation_failure_without_spurious_defect():
    """5. Direct navigation to a route returning 404 is handled as a navigation failure and does not trigger spurious defect leads."""
    # Test 1: Oracles ignore resource loading 404s
    probe_res = ProbeResult(
        script_id="probe_404",
        status="FAILED",
        steps_executed=0,
        error_message="Navigation failure: route returned status 404",
        console_errors=[
            ConsoleOrPageError(seq=1, error_type="console", message="Failed to load resource: the server responded with a status of 404 ()")
        ],
    )
    findings = evaluate_always_on_oracles(probe_res)
    # The 404 resource loading error must NOT produce an Uncaught Console Error finding
    uncaught_findings = [f for f in findings if "uncaught" in f.title.lower()]
    assert len(uncaught_findings) == 0

    # Test 2: Workflow skips oracle defect creation when probe fails from navigation failure
    orchestrator = MagicMock(spec=InvestigationOrchestrator)
    state = {
        "current_question": MagicMock(id="q_direct_nav", probe_family="controls", target_element="form", prediction=None),
        "current_probe_result": probe_res,
    }
    # Invoke the actual logic from node_perceive_after_and_compare
    result = InvestigationOrchestrator.node_perceive_after_and_compare(orchestrator, state)
    assert result == {}  # Gracefully returns empty dict, no defect leads generated


# --- Scenario 6 ---
def test_checkout_form_submission_requires_verified_transition():
    """6. Checkout form submission requires verified transition to the next checkout step, not merely any DOM mutation."""
    agenda = InvestigationAgenda()
    # Synthesize purchase journey
    obs_cart = UIStateObservation(
        state_id="st_cart",
        url="https://www.saucedemo.com/cart.html",
        normalized_route="/cart.html",
        page_title="Your Cart",
        interactive_controls=[
            ElementReference(element_id="btn_cart", role="button", selector="button.cart-add", accessible_name="Add to Cart", tag_name="button"),
            ElementReference(element_id="btn_cart2", role="button", selector="button.cart-add2", accessible_name="Add to Cart", tag_name="button"),
        ],
    )
    agenda.synthesize_journeys_from_observation(obs_cart)
    journey = agenda.journeys.get("journey_purchase")
    assert journey is not None

    # Find the checkout submission step
    submit_step = next(s for s in journey.steps if s.name == "Fill and Submit Checkout Form")
    # Set journey as active and current step to submit_step
    agenda.active_journey_id = "journey_purchase"
    journey.status = JourneyStatus.IN_PROGRESS
    journey.current_step_index = submit_step.step_index

    before_obs = UIStateObservation(
        state_id="st_checkout_1",
        url="https://www.saucedemo.com/checkout-step-one.html",
        normalized_route="/checkout-step-one.html",
        page_title="Checkout: Your Information",
    )

    # Sub-case A: Arbitrary DOM mutation on same route without advancing to checkout step 2
    after_obs_same_page = UIStateObservation(
        state_id="st_checkout_1_mutated",
        url="https://www.saucedemo.com/checkout-step-one.html",
        normalized_route="/checkout-step-one.html",
        page_title="Checkout: Your Information",
    )
    diff_mutation_only = StateDiff(controls_added=["div.flash-error"])

    q_id = f"q_{submit_step.step_index}"
    submit_step.executed_question_id = q_id

    agenda.record_probe_outcome(
        question_id=q_id,
        verdict="MATCHED",
        summary="Submitted checkout form",
        mutating=True,
        diff=diff_mutation_only,
        before_obs=before_obs,
        after_obs=after_obs_same_page,
    )
    # Must FAIL: mere DOM mutation does not prove checkout progress!
    assert submit_step.status == JourneyStepStatus.FAILED
    assert submit_step.completed is False
    assert journey.status == JourneyStatus.BLOCKED

    # Sub-case B: Verified transition to /checkout-step-two.html
    agenda.active_journey_id = "journey_purchase"
    journey.status = JourneyStatus.IN_PROGRESS
    submit_step.status = JourneyStepStatus.PLANNED
    submit_step.completed = False
    submit_step.executed_question_id = q_id

    after_obs_step_two = UIStateObservation(
        state_id="st_checkout_2",
        url="https://www.saucedemo.com/checkout-step-two.html",
        normalized_route="/checkout-step-two.html",
        page_title="Checkout: Overview",
        headings=["Checkout: Overview"],
    )
    diff_step_two = StateDiff(route_changed=True)

    agenda.record_probe_outcome(
        question_id=q_id,
        verdict="MATCHED",
        summary="Submitted checkout form",
        mutating=True,
        diff=diff_step_two,
        before_obs=before_obs,
        after_obs=after_obs_step_two,
    )
    # Must be VERIFIED: navigated to next checkout step!
    assert submit_step.status == JourneyStepStatus.VERIFIED
    assert submit_step.completed is True


# --- Scenario 7 ---
def test_purchase_journey_generation_single_product():
    """7. Purchase journey generation does not include 'Add Product 2' when only one product is observed."""
    agenda_single = InvestigationAgenda()
    # Observation with ONLY ONE add-to-cart button
    obs_single = UIStateObservation(
        state_id="st_inventory_single",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Products",
        interactive_controls=[
            ElementReference(
                element_id="btn_1",
                role="button",
                selector="button[data-test='add-to-cart-sauce-labs-backpack']",
                accessible_name="Add to cart",
                tag_name="button",
            )
        ],
    )
    agenda_single.synthesize_journeys_from_observation(obs_single)
    journey_single = agenda_single.journeys.get("journey_purchase")
    assert journey_single is not None

    step_names_single = [s.name for s in journey_single.steps]
    assert "Add Product 1 to Cart" in step_names_single
    assert "Add Product 2 to Cart" not in step_names_single
    assert "Confirm Order" not in step_names_single  # Not observed in initial catalogue DOM

    # Observation with TWO add-to-cart buttons
    agenda_two = InvestigationAgenda()
    obs_two = UIStateObservation(
        state_id="st_inventory_two",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Products",
        interactive_controls=[
            ElementReference(element_id="btn_1", role="button", selector="#add-to-cart-sauce-labs-backpack", accessible_name="Add to cart", tag_name="button"),
            ElementReference(element_id="btn_2", role="button", selector="#add-to-cart-sauce-labs-bike-light", accessible_name="Add to cart", tag_name="button"),
        ],
    )
    agenda_two.synthesize_journeys_from_observation(obs_two)
    journey_two = agenda_two.journeys.get("journey_purchase")
    assert journey_two is not None

    step_names_two = [s.name for s in journey_two.steps]
    assert "Add Product 1 to Cart" in step_names_two
    assert "Add Product 2 to Cart" in step_names_two
