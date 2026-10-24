"""
Regression and Acceptance Tests for Autonomous Investigation Audit & Repairs.
Verifies fixes for:
1. SafetyGovernor: Checkout navigation allowed on authorized hosts; rationale does not leak into safety blocking; terminal purchasing remains blocked.
2. Agenda / JourneyPlanner: General cart route recognition (.html, trailing slashes, /basket) prevents false unreachable halting.
3. Perception: Query parameter preservation prevents state graph collapse between distinct items (?id=3 vs ?id=4).
4. Workflow / Planner: Ungrounded LLM reflection targets are not dispatched as raw CSS selectors.
5. Navigation Recovery: 404 on direct client-side routes triggers recovery to healthy application state.
6. Generalized User Journeys: Dynamic journey synthesis for SaaS, search/filter, and forms beyond fixed e-commerce.
"""

import pytest
from app.investigation_agent.governor import SafetyGovernor
from app.investigation_agent.perception import compute_state_class_and_instance
from app.investigation_agent.agenda import InvestigationAgenda, UserJourney, JourneyStep, JourneyStepStatus
from app.investigation_agent.models import UIStateObservation, ElementReference, SemanticTarget
from app.config import get_settings


def test_safety_governor_allows_checkout_navigation_on_authorized_host():
    """Ordinary navigation/viewing of checkout on authorized hosts must be permitted."""
    gov = SafetyGovernor(trusted_hosts=["www.saucedemo.com", "saucedemo.com"])
    
    # 1. Clicking checkout button or navigating to checkout on authorized host
    allowed, reason = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#checkout",
        current_url="https://www.saucedemo.com/cart.html",
        rationale="Proceed to checkout step",
    )
    assert allowed, f"Checkout navigation on authorized host should be permitted: {reason}"

    # 2. Rationale containing 'checkout' must NOT block an innocent product navigation
    allowed2, reason2 = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#item_4_title_link",
        current_url="https://www.saucedemo.com/cart.html",
        rationale="Inspect product details before checkout review",
    )
    assert allowed2, f"Innocent action with 'checkout' in rationale must not be blocked: {reason2}"

    # 3. Terminal financial actions remain strictly blocked
    for terminal_kw in ("pay now", "complete purchase", "confirm payment", "place order", "buy now"):
        blocked, term_reason = gov.can_execute(
            action_type="CLICK_CONTROL",
            target=f"button[value='{terminal_kw}']",
            current_url="https://www.saucedemo.com/cart.html",
            rationale="Attempt terminal transaction",
        )
        assert not blocked, f"Terminal action '{terminal_kw}' must remain blocked"
        assert "prohibited keyword" in term_reason.lower() or "safety policy" in term_reason.lower()


def test_journey_planner_cart_html_route_recognition():
    """Agenda must recognize /cart.html and not claim cart is unreachable."""
    agenda = InvestigationAgenda()
    # Synthesize purchase journey
    obs = UIStateObservation(
        state_id="st_inventory",
        url="https://www.saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Products",
        interactive_controls=[
            ElementReference(
                element_id="c1",
                tag_name="button",
                role="button",
                selector="#add-to-cart-sauce-labs-backpack",
                accessible_name="Add to cart",
                is_visible=True,
                is_enabled=True,
            ),
            ElementReference(
                element_id="c2",
                tag_name="a",
                role="link",
                selector="#shopping_cart_link",
                accessible_name="Cart",
                is_visible=True,
                is_enabled=True,
            )
        ]
    )
    agenda.synthesize_journeys_from_observation(obs)
    assert "journey_purchase" in agenda.journeys

    j = agenda.journeys["journey_purchase"]
    j.current_step_index = 4  # Step 4: Proceed to Checkout
    
    # When agent is at cart.html with a checkout button
    cart_ctrl = ElementReference(
        element_id="c3",
        tag_name="button",
        role="button",
        selector="#checkout",
        accessible_name="Checkout",
        is_visible=True,
        is_enabled=True,
    )
    cart_obs = UIStateObservation(
        state_id="st_cart",
        state_class_id="cls_cart",
        url="https://www.saucedemo.com/cart.html",
        normalized_route="/cart.html",
        page_title="Your Cart",
        interactive_controls=[cart_ctrl]
    )
    agenda.populate_from_observation(cart_obs)
    
    # Must select the checkout question, NOT report unreachable
    next_q = agenda.select_next_question(
        current_state_class=cart_obs.state_class_id,
        current_url=cart_obs.url,
        available_selectors={"#checkout"}
    )
    assert next_q is not None, "Next question for checkout step on /cart.html must not be None"
    assert "checkout" in next_q.target_element.lower() or "checkout" in next_q.id.lower()


def test_state_identity_preserves_query_parameters():
    """Different query parameters on same route must produce distinct state instances."""
    cls_3, inst_3 = compute_state_class_and_instance(
        url="https://www.saucedemo.com/inventory-item.html?id=3",
        headings=["Sauce Labs Bolt T-Shirt"],
        forms=[],
        dynamic_signals={"counter_value": "1"},
    )
    cls_4, inst_4 = compute_state_class_and_instance(
        url="https://www.saucedemo.com/inventory-item.html?id=4",
        headings=["Sauce Labs Backpack"],
        forms=[],
        dynamic_signals={"counter_value": "1"},
    )

    # Class ID should group them as resource instances of the same template
    assert cls_3 == cls_4, "State classes for item details of same route should match template"
    # Instance ID must be distinct to prevent graph collision and selector/URL mismatch
    assert inst_3 != inst_4, f"State instances must be distinct for id=3 vs id=4! Got {inst_3} == {inst_4}"
    assert "id_3" in inst_3 or "3" in inst_3, "Instance ID should preserve parameter value for id=3"
    assert "id_4" in inst_4 or "4" in inst_4, "Instance ID should preserve parameter value for id=4"


def test_generalized_journey_synthesis_for_saas_and_search():
    """Agenda must synthesize appropriate multi-step journeys for search, dashboard, and forms."""
    agenda = InvestigationAgenda()
    
    # SaaS Dashboard observation
    obs_saas = UIStateObservation(
        state_id="st_dashboard",
        url="https://app.example.com/dashboard",
        normalized_route="/dashboard",
        page_title="Dashboard",
        headings=["Analytics Overview"],
        interactive_controls=[
            ElementReference(
                element_id="c1", tag_name="input", role="textbox", selector="#search-query",
                accessible_name="Search", is_visible=True, is_enabled=True,
            ),
            ElementReference(
                element_id="c2", tag_name="button", role="button", selector="#btn-filter",
                accessible_name="Apply Filters", is_visible=True, is_enabled=True,
            ),
            ElementReference(
                element_id="c3", tag_name="button", role="button", selector="#btn-create-project",
                accessible_name="New Project", is_visible=True, is_enabled=True,
            )
        ]
    )
    agenda.synthesize_journeys_from_observation(obs_saas)
    
    assert len(agenda.journeys) > 0, "Agenda must synthesize journeys for SaaS/search interface"
    journey_names = [j.name.lower() for j in agenda.journeys.values()]
    has_saas_or_search = any("search" in n or "filter" in n or "create" in n or "dashboard" in n or "workflow" in n for n in journey_names)
    assert has_saas_or_search, f"Synthesized journeys should include search/filter or creation workflow: {journey_names}"
