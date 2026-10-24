import sys
import re
sys.path.insert(0, ".")
from app.investigation_agent.models import UIStateObservation, ElementReference, JourneyStep, UserJourney
from app.investigation_agent.agenda import InvestigationAgenda
from app.investigation_agent.probes.controls import generate_control_probes

controls = [
    ElementReference(
        element_id="ctrl_0",
        role="link",
        accessible_name="ShopSphere Home",
        tag_name="a",
        selector="[data-test='brand-logo']",
        declared_constraints={"href": "/"},
    ),
    ElementReference(
        element_id="ctrl_1",
        role="button",
        accessible_name="Decrease Quantum Wireless Noise-Cancelling Headphones quantity",
        tag_name="button",
        selector="[data-test='cart-qty-dec-prod_01']",
    ),
    ElementReference(
        element_id="ctrl_2",
        role="button",
        accessible_name="Increase Quantum Wireless Noise-Cancelling Headphones quantity",
        tag_name="button",
        selector="[data-test='cart-qty-inc-prod_01']",
    ),
    ElementReference(
        element_id="ctrl_3",
        role="button",
        accessible_name="Remove Quantum Wireless Noise-Cancelling Headphones from cart",
        tag_name="button",
        selector="[data-test='cart-remove-prod_01']",
    ),
    ElementReference(
        element_id="ctrl_4",
        role="button",
        accessible_name="Proceed to Checkout",
        tag_name="button",
        selector="[data-test='checkout-button']",
    ),
]

obs = UIStateObservation(
    state_id="st_cart_1",
    state_class_id="cls_3c5b3c4175",
    url="http://127.0.0.1:5173/cart",
    normalized_route="/cart",
    page_title="Shopping Cart",
    headings=["Shopping Cart (1 item)"],
    interactive_controls=controls,
    forms=[],
    navigation_links=[],
)

agenda = InvestigationAgenda()
agenda.synthesize_journeys_from_observation(obs)
agenda.populate_from_observation(obs)

# Test Step 3 with updated target_pattern and role check
step3 = JourneyStep(
    step_index=3,
    name="Modify Cart Quantity",
    action_type="CLICK_CONTROL",
    target_pattern=r"cart-qty|qty-plus|qty-inc|quantity|increase|add-qty|qty-minus|decrement|remove|delete|promo|(?<![a-zA-Z0-9_])[+-](?![a-zA-Z0-9_])",
    expected_role="button",
)

pat_re = re.compile(step3.target_pattern, re.IGNORECASE)

print("Testing candidate questions against Step 3:")
for qid, q in agenda.questions.items():
    q_role = (q.metadata.get("role") or q.metadata.get("tag") or "").lower()
    q_tag = (q.metadata.get("tag") or "").lower()
    
    # 1. Role check
    role_match = (q_role in ("button", "ctrl") or q_tag in ("button", "input"))
    
    # 2. Semantic exclusion
    q_text = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')}".lower()
    is_excluded = any(k in q_text for k in ("brand-logo", "home", "login", "logo", "search"))
    
    # 3. Regex match
    t_str = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')} {q.metadata.get('href', '')} {q.hypothesis or ''} {q.id}".lower()
    regex_match = bool(pat_re.search(t_str))
    
    eligible = role_match and not is_excluded and regex_match
    print(f"  {qid}: role={role_match}, excluded={is_excluded}, regex={regex_match} => ELIGIBLE: {eligible}")
