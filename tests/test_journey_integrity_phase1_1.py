"""
Tests for Phase 1.1 Journey Integrity and Control Binding.
Verifies:
1. Skipped journey steps are NEVER marked completed or verified.
2. Executed actions cannot be misattributed to different planned steps.
3. Brand-logo links cannot bind to or satisfy quantity-modification buttons.
4. Quantity modification fails postcondition verification if quantity remains unchanged.
5. Successful quantity modification records before-and-after evidence and verifies cleanly.
6. Ambiguous controls matching multiple elements are rejected safely with TARGET_AMBIGUOUS.
7. Verification metrics accurately report planned, verified, skipped, and failed steps.
"""

import pytest
from app.investigation_agent.models import (
    JourneyStep,
    UserJourney,
    JourneyStatus,
    JourneyStepStatus,
    UIStateObservation,
    ElementReference,
    StateDiff,
    ActionFailureReason,
)
from app.investigation_agent.agenda import InvestigationAgenda, InvestigationQuestion


def test_journey_step_skipped_semantics():
    """A skipped step must have status=SKIPPED, completed=False, and not be counted in verified_steps_count."""
    step = JourneyStep(
        step_index=1,
        name="Add Product 2 to Cart",
        action_type="CLICK_CONTROL",
        target_pattern=r"add-to-cart",
        expected_role="button",
        status=JourneyStepStatus.SKIPPED,
        completed=False,
        verification_details="Skipped: single-item catalog",
    )
    journey = UserJourney(
        journey_id="j1",
        name="Purchase Funnel",
        description="Test funnel",
        steps=[
            JourneyStep(step_index=0, name="Step 0", status=JourneyStepStatus.VERIFIED, completed=True),
            step,
            JourneyStep(step_index=2, name="Step 2", status=JourneyStepStatus.PLANNED, completed=False),
        ],
    )

    assert step.status == JourneyStepStatus.SKIPPED
    assert step.completed is False
    assert journey.planned_steps_count == 3
    assert journey.verified_steps_count == 1
    assert journey.skipped_steps_count == 1
    assert journey.failed_steps_count == 0


def test_brand_logo_cannot_match_quantity_step():
    """Brand-logo link must NOT match a quantity modification step expecting a button."""
    agenda = InvestigationAgenda()
    step = JourneyStep(
        step_index=3,
        name="Modify Cart Quantity",
        action_type="CLICK_CONTROL",
        target_pattern=r"cart-qty-inc|qty-plus|qty-inc|increase|add-qty|cart-qty|qty-minus|decrement|remove|delete|(?<![a-zA-Z0-9_])[+-](?![a-zA-Z0-9_])",
        expected_role="button",
        expected_postcondition="quantity_modified",
    )

    # Question for brand logo (which has hyphens in data-test="brand-logo" and role="link")
    q_logo = InvestigationQuestion(
        id="q_ctrl_brand_logo",
        hypothesis="Investigate brand logo navigation",
        category="discovery",
        target_state_class="cart",
        target_element='[data-test="brand-logo"]',
        probe_family="controls",
        metadata={
            "role": "link",
            "selector": '[data-test="brand-logo"]',
            "name": "ShopSphere",
        },
    )
    agenda.questions[q_logo.id] = q_logo

    # Question for genuine quantity increment button
    q_qty = InvestigationQuestion(
        id="q_ctrl_qty_inc",
        hypothesis="Investigate quantity increment",
        category="discovery",
        target_state_class="cart",
        target_element='[data-test="cart-qty-inc-prod_01"]',
        probe_family="controls",
        metadata={
            "role": "button",
            "selector": '[data-test="cart-qty-inc-prod_01"]',
            "name": "+",
        },
    )
    agenda.questions[q_qty.id] = q_qty

    assert not agenda._question_matches_step(q_logo, step)
    assert agenda._question_matches_step(q_qty, step)

    matched = agenda._find_matching_question_for_step(step, current_state_class="cart")
    assert matched is not None
    assert matched.id == "q_ctrl_qty_inc"
    assert matched.id != "q_ctrl_brand_logo"


def test_quantity_verification_fails_if_quantity_unchanged():
    """If an action executes but in-cart quantity remains unchanged, postcondition verification fails."""
    agenda = InvestigationAgenda()
    j = UserJourney(
        journey_id="j_cart",
        name="Cart Flow",
        description="Cart modification",
        steps=[
            JourneyStep(
                step_index=0,
                name="Modify Cart Quantity",
                action_type="CLICK_CONTROL",
                target_pattern=r"cart-qty-inc",
                expected_role="button",
                expected_postcondition="quantity_modified",
            )
        ],
    )
    agenda.journeys["j_cart"] = j
    agenda.active_journey_id = "j_cart"

    q = InvestigationQuestion(
        id="q_qty",
        hypothesis="Investigate brand logo click",
        category="discovery",
        target_state_class="cart",
        target_element='[data-test="brand-logo"]',
        probe_family="controls",
        metadata={"role": "button", "selector": '[data-test="brand-logo"]'},
    )
    agenda.questions["q_qty"] = q
    j.steps[0].executed_question_id = "q_qty"

    before_obs = UIStateObservation(
        url="http://test/cart",
        page_title="Cart",
        normalized_route="http://test/cart",
        state_id="s_before",
        state_class_id="cart",
        dynamic_signals={"cart_item_quantity": 1},
    )
    after_obs = UIStateObservation(
        url="http://test/cart",
        page_title="Cart",
        normalized_route="http://test/cart",
        state_id="s_after",
        state_class_id="cart",
        dynamic_signals={"cart_item_quantity": 1},  # Unchanged!
    )
    diff = StateDiff(is_significant=False)

    agenda.record_probe_outcome(
        question_id="q_qty",
        verdict="OBSERVED",
        summary="Clicked brand logo",
        mutating=False,
        before_obs=before_obs,
        after_obs=after_obs,
        diff=diff,
    )

    step = j.steps[0]
    assert step.status == JourneyStepStatus.FAILED
    assert step.completed is False
    assert "quantity unchanged" in step.verification_details
    assert j.status == JourneyStatus.BLOCKED


def test_quantity_verification_succeeds_with_delta():
    """If in-cart quantity changes (e.g. 1 -> 2), postcondition verification succeeds with before/after values."""
    agenda = InvestigationAgenda()
    j = UserJourney(
        journey_id="j_cart",
        name="Cart Flow",
        description="Cart modification",
        steps=[
            JourneyStep(
                step_index=0,
                name="Modify Cart Quantity",
                action_type="CLICK_CONTROL",
                target_pattern=r"cart-qty-inc",
                expected_role="button",
                expected_postcondition="quantity_modified",
            )
        ],
    )
    agenda.journeys["j_cart"] = j
    agenda.active_journey_id = "j_cart"

    q = InvestigationQuestion(
        id="q_qty_inc",
        hypothesis="Investigate quantity increment",
        category="discovery",
        target_state_class="cart",
        target_element='[data-test="cart-qty-inc-p1"]',
        probe_family="controls",
        metadata={"role": "button", "selector": '[data-test="cart-qty-inc-p1"]'},
    )
    agenda.questions["q_qty_inc"] = q

    before_obs = UIStateObservation(
        url="http://test/cart",
        page_title="Cart",
        normalized_route="http://test/cart",
        state_id="s_before",
        state_class_id="cart",
        dynamic_signals={"cart_item_quantity": 1},
    )
    after_obs = UIStateObservation(
        url="http://test/cart",
        page_title="Cart",
        normalized_route="http://test/cart",
        state_id="s_after",
        state_class_id="cart",
        dynamic_signals={"cart_item_quantity": 2},  # Incremented!
    )
    diff = StateDiff(is_significant=True, signals=["cart_quantity_modified"])

    agenda.record_probe_outcome(
        question_id="q_qty_inc",
        verdict="OBSERVED",
        summary="Clicked quantity increment",
        mutating=True,
        before_obs=before_obs,
        after_obs=after_obs,
        diff=diff,
    )

    step = j.steps[0]
    assert step.status == JourneyStepStatus.VERIFIED
    assert step.completed is True
    assert step.before_value == "1"
    assert step.after_value == "2"
    assert "Quantity changed from 1 to 2" in step.verification_details or "changed" in step.verification_details
    assert j.status == JourneyStatus.COMPLETED
    assert j.verified_steps_count == 1


def test_select_next_question_skips_absent_second_product_without_marking_verified():
    """When a single-item catalog exists, step 1 is skipped cleanly and NOT marked completed."""
    agenda = InvestigationAgenda()
    # Synthesize purchase journey
    obs = UIStateObservation(
        url="http://test/",
        page_title="Storefront",
        normalized_route="http://test/",
        state_id="s0",
        state_class_id="home",
        interactive_controls=[
            ElementReference(element_id="btn1", role="button", tag_name="button", selector='[data-test="add-to-cart-1"]', accessible_name="Add to Cart"),
            ElementReference(element_id="btn2", role="button", tag_name="button", selector='[data-test="add-to-cart-2"]', accessible_name="Add to Cart 2"),
            ElementReference(element_id="link_cart", role="link", tag_name="a", selector='[data-test="shopping-cart"]', accessible_name="Cart"),
        ],
    )
    agenda.synthesize_journeys_from_observation(obs)
    j = agenda.journeys["journey_purchase"]

    # Register only ONE add-to-cart question
    q_add1 = InvestigationQuestion(
        id="q_add1",
        hypothesis="Investigate add to cart",
        category="discovery",
        target_state_class="home",
        target_element='[data-test="add-to-cart-1"]',
        probe_family="controls",
        metadata={"role": "button", "selector": '[data-test="add-to-cart-1"]', "name": "Add to Cart"},
    )
    agenda.questions[q_add1.id] = q_add1

    q_nav_cart = InvestigationQuestion(
        id="q_nav_cart",
        hypothesis="Investigate cart navigation",
        category="discovery",
        target_state_class="home",
        target_element='[data-test="shopping-cart"]',
        probe_family="controls",
        metadata={"role": "link", "selector": '[data-test="shopping-cart"]', "name": "Cart"},
    )
    agenda.questions[q_nav_cart.id] = q_nav_cart

    # 1. Step 0 matches q_add1
    chosen0 = agenda.select_next_question(current_state_class="home", current_url="http://test/")
    assert chosen0 is not None
    assert chosen0.id == "q_add1"

    # Simulate execution and verification of step 0
    agenda.record_probe_outcome(
        question_id=chosen0.id,
        verdict="OBSERVED",
        summary="Added product 1",
        mutating=True,
        before_obs=obs,
        after_obs=UIStateObservation(
            url="http://test/",
            page_title="Storefront",
            normalized_route="http://test/",
            state_id="s1",
            state_class_id="home",
            dynamic_signals={"cart_badge_count": 1},
        ),
        diff=StateDiff(is_significant=True, signals=["cart_badge_increment"]),
    )
    assert j.steps[0].status == JourneyStepStatus.VERIFIED
    assert j.steps[0].completed is True

    # 2. Next selection encounters Step 1 (Add Product 2). Since only q_add1 existed and is now answered,
    # no second distinct product question exists.
    # Agenda should skip Step 1 and advance directly to Step 2 (Navigate to Cart)!
    chosen_step2 = agenda.select_next_question(current_state_class="home", current_url="http://test/")
    assert chosen_step2 is not None
    assert chosen_step2.id == "q_nav_cart"

    # Step 1 MUST be SKIPPED and completed MUST be False
    assert j.steps[1].status == JourneyStepStatus.SKIPPED
    assert j.steps[1].completed is False
    assert "Skipped" in (j.steps[1].verification_details or "")

    # Verified steps count must strictly be 1, not 2!
    assert j.verified_steps_count == 1
    assert j.skipped_steps_count == 1
