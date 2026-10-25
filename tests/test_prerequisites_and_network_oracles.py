"""
Regressions from a public e-commerce run (generic mechanisms, synthetic inputs):
  * third-party request failures and 401/403 access-control answers are never product leads
  * a page reached by a link after an interaction (cart after "add item") is replayed through that interaction
  * replayed prerequisites follow the recorded order (no splicing steps from different visits)
  * recorded option choices are replayable prerequisites
  * card layouts: a name sort is evaluated on each record's title when titles are unambiguous
"""

from types import SimpleNamespace

from app.execution import list_oracles
from app.intelligence.target_context import inventory_from_report
from app.investigation_agent.oracles import evaluate_always_on_oracles
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from test_multi_website_generalization import _report, _state

ORIGIN = "http://127.0.0.1:8655"  # local host: the safety governor only allows interactions on authorised hosts


def _probe(*events):
    return SimpleNamespace(before_obs=SimpleNamespace(url=ORIGIN + "/cart", console_error_messages=[]), after_obs=None,
                           console_errors=[], diff=None, script_id="p", network_events=list(events))


def _net(url, status, method="GET"):
    return SimpleNamespace(url=url, status=status, method=method, ok=status < 400, failure=None, seq=1, response_snippet="")


def test_third_party_and_access_control_responses_are_not_product_leads():
    titles = [f.title for f in evaluate_always_on_oracles(_probe(
        _net("https://telemetry.vendor.example/submit?token=x", 401, "POST"),   # third-party, unauthorised
        _net("https://cdn.vendor.example/lib.js", 404),                          # third-party asset
        _net("https://telemetry.vendor.example/x", 503),                         # third-party outage
        _net(ORIGIN + "/api/account", 401),                                      # first-party access control
        _net(ORIGIN + "/api/admin", 403),
    ))]
    assert titles == []
    titles = [f.title for f in evaluate_always_on_oracles(_probe(_net(ORIGIN + "/img/missing.png", 404),
                                                                 _net(ORIGIN + "/api/cart", 422),
                                                                 _net(ORIGIN + "/api/cart", 500)))]
    assert "Resource Not Found (404)" in titles and "Client Error Response (422)" in titles and len(titles) == 3


def shop_report_with_cart_history(record_select_value=True):
    """catalog --add item--> catalog(1) --sort--> catalog(1, sorted) --cart link--> cart(1); later another visit adds a
    different item from a detail page, and the detail page was first reached from the cart (out of order)."""
    states = [
        _state("s_cat", ORIGIN, "/catalog", ["Catalog"], reached="AUTHENTICATION"),
        _state("s_cat1", ORIGIN, "/catalog", ["Catalog"], reached="INTERACTION"),
        _state("s_cat1z", ORIGIN, "/catalog", ["Catalog"], reached="INTERACTION"),
        _state("s_cart1", ORIGIN, "/cart", ["Your cart"], reached="NAVIGATION"),
        _state("s_item1", ORIGIN, "/item", ["Item"], reached="NAVIGATION"),
        _state("s_item", ORIGIN, "/item", ["Item"], reached="NAVIGATION"),
    ]
    states[4]["url"] = ORIGIN + "/item?id=4"
    states[5]["url"] = ORIGIN + "/item?id=3"
    controls = [
        {"selector": "#add-bolt", "tag": "button", "name": "Add to cart", "state_id": "s_cat", "url": ORIGIN + "/catalog", "route": "/catalog"},
        {"selector": "#sort", "tag": "select", "name": "Sort", "options": ["az", "za"], "option_labels": ["A to Z", "Z to A"],
         "state_id": "s_cat1", "url": ORIGIN + "/catalog", "route": "/catalog"},
        {"selector": "#cart-link", "tag": "a", "name": "Cart", "href": "/cart", "state_id": "s_cat1z", "url": ORIGIN + "/catalog", "route": "/catalog"},
        {"selector": "#remove-bolt", "tag": "button", "name": "Remove", "state_id": "s_cart1", "url": ORIGIN + "/cart", "route": "/cart"},
        {"selector": "#item-link", "tag": "a", "name": "Bolt", "href": "/item?id=4", "state_id": "s_cart1", "url": ORIGIN + "/cart", "route": "/cart"},
        {"selector": "#cart-link-2", "tag": "a", "name": "Cart", "href": "/cart", "state_id": "s_item1", "url": ORIGIN + "/item", "route": "/item"},
        {"selector": "#add", "tag": "button", "name": "Add to cart", "state_id": "s_item", "url": ORIGIN + "/item", "route": "/item"},
    ]
    t = lambda tid, a, b, act, tgt, value=None: {"transition_id": tid, "source_state_id": a, "destination_state_id": b,
                                                 "action_type": act, "target": tgt, "value": value, "evidence_ids": [tid]}
    transitions = [
        t("t1", "s_cat", "s_cat1", "CLICK_CONTROL:add", "#add-bolt"),
        t("t2", "s_cat1", "s_cat1z", "SELECT_OPTION:sort", "#sort", "za" if record_select_value else None),
        t("t3", "s_cat1z", "s_cart1", "CLICK_CONTROL:cart", "#cart-link"),
        t("t4", "s_cart1", "s_item1", "CLICK_CONTROL:item", "#item-link"),
        t("t5", "s_item1", "s_cart1", "CLICK_CONTROL:cart", "#cart-link-2"),
        t("t6", "s_cart1", "s_cart1", "CLICK_CONTROL:remove", "#remove-bolt"),
        t("t7", "s_item", "s_item1", "CLICK_CONTROL:add", "#add"),     # recorded after t5
    ]
    return _report(ORIGIN, "run-shop-cart", states, controls, [], transitions)


def test_cart_reached_after_an_interaction_is_replayed_in_recorded_order():
    inv = inventory_from_report(shop_report_with_cart_history())
    path = KnowledgeGuidedTestPlanner._replayable_path(inv, inv.state("s_cart1"))
    assert [p.transition_id for p in path] == ["t1", "t2", "t3"]       # add → sort → cart, not the spliced t7 → t5


def test_unrecorded_choice_makes_the_path_unreplayable_rather_than_wrong():
    inv = inventory_from_report(shop_report_with_cart_history(record_select_value=False))
    assert KnowledgeGuidedTestPlanner._replayable_path(inv, inv.state("s_cart1")) is None


def test_planned_cart_test_creates_its_item_first():
    cases, _, _ = KnowledgeGuidedTestPlanner().plan_test_suite_sync(shop_report_with_cart_history(), target_count=300)
    remove = [c for c in cases if c.steps and c.steps[-1].selector == "#remove-bolt"]
    assert remove, "the cart command must be tested"
    steps = remove[0].steps
    assert [(s.action.value, s.selector, s.value) for s in steps[1:4]] == [
        ("CLICK", "#add-bolt", None), ("SELECT", "#sort", "za"), ("CLICK", "#cart-link", None)]
    assert all(s.setup for s in steps[:4]) and not steps[-1].setup


def test_name_sort_on_cards_uses_unambiguous_titles():
    cards = {"values": ["Backpack", "Bike Light", "Onesie"], "texts": ["Backpack $29.99", "Bike Light $9.99", "Onesie $7.99"],
             "fields": [[], [], []], "headers": []}
    assert list_oracles.evaluate_sorted(cards, {"key": "text", "any_direction": True, "column_hint": "name"})[0] == "pass"
    shuffled = {**cards, "values": ["Bike Light", "Backpack", "Onesie"]}
    assert list_oracles.evaluate_sorted(shuffled, {"key": "text", "any_direction": True})[0] == "fail"
    duplicated = {**cards, "values": ["Backpack", "Backpack", "Onesie"]}
    assert list_oracles.evaluate_sorted(duplicated, {"key": "text", "any_direction": True})[0] == "not_evaluable"
