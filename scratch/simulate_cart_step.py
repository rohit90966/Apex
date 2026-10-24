import sys
sys.path.insert(0, ".")
from app.investigation_agent.models import UIStateObservation, ElementReference
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

probes = generate_control_probes(obs)
print(f"Generated {len(probes)} control probes:")
for p, pred, meta in probes:
    print(f"  {p.name} | target: {meta.get('target')} | sel: {meta.get('selector')}")

agenda = InvestigationAgenda()
agenda.synthesize_journeys_from_observation(obs)
added = agenda.populate_from_observation(obs)
print(f"\nAdded {added} questions to agenda. Total questions: {len(agenda.questions)}")
for qid, q in agenda.questions.items():
    print(f"  {qid} | target: {q.target_element} | meta sel: {q.metadata.get('selector')}")

# Now test what select_next_question does!
j = agenda.journeys.get("journey_purchase")
print(f"\nJourney: {j.name}, step: {j.current_step_index}")
j.current_step_index = 3  # Step 3 is Modify Cart Quantity!
step = j.steps[3]
print(f"Step 3: {step.name}, pattern: {step.target_pattern}")

available_selectors = {c.selector for c in controls}
matching_q = agenda._find_matching_question_for_step(step, "cls_3c5b3c4175", "http://127.0.0.1:5173/cart", available_selectors)
print(f"Matching Q for Step 3: {matching_q.id if matching_q else None}")

chosen = agenda.select_next_question("cls_3c5b3c4175", "http://127.0.0.1:5173/cart", available_selectors)
print(f"Chosen question: {chosen.id if chosen else None} | target: {chosen.target_element if chosen else None}")
