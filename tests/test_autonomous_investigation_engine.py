"""
Comprehensive Regression & Verification Suite for the VerityQA Autonomous Investigation Engine.
Covers all 12 Phase E architectural verification scenarios:
1. Initial page exploration before authentication transition & stable state_class_id across alerts.
2. Authentication transition anchored to clean login entry state (not chained off error state).
3. Dynamic discovery of controls on newly reached pages (including standalone <select> dropdowns).
4. Website-agnostic equivalence-class deduplication of repeated controls & image-only links.
5. Semantic prioritization of state-changing and workflow-advancing controls over utility drawers.
6. Detection of in-page state transitions (badge counter, selected option, drawer, alert) without class churn.
7. Detection of route transitions vs in-page state mutations.
8. Explicit 4-part outcome semantics (execution_status, observed_effect, prediction_verdict, transition_verified) & NO_EFFECT detection.
9. Branch-aware separation of Validation/Constraint branches, Exploratory/Utility branches, and Primary Workflow.
10. Accurate frontier synchronization and deduplicated DOM element counts across in-page states.
11. Consistent status reporting across SSE stream and final report & LLM latency exclusion from browser budget.
12. Valid replayable Playwright test export from verified primary journey transitions.
"""

import time
import pytest
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    StateDiff,
    Prediction,
    ComparatorVerdict,
    StateTransitionRecord,
    FrontierStatus,
    InvestigationStatus,
    EvidenceEntry,
    NetworkEvent,
)
from app.investigation_agent.perception import compute_state_class_and_instance
from app.investigation_agent.probes.controls import generate_control_probes
from app.investigation_agent.agenda import InvestigationAgenda
from app.investigation_agent.comparator import compare_prediction_with_diff
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.report_builder import InvestigationReportBuilder


def _make_obs(
    state_id: str,
    url: str,
    normalized_route: str = "/",
    headings=None,
    controls=None,
    forms=None,
    nav_links=None,
    dynamic_signals=None,
    has_validation_error: bool = False,
    validation_error_message: str = None,
    menu_open: bool = False,
    a11y_snapshot: str = "",
) -> UIStateObservation:
    raw = UIStateObservation(
        state_id=state_id,
        url=url,
        normalized_route=normalized_route,
        page_title="Demo Portal",
        headings=headings or ["Products"],
        interactive_controls=controls or [],
        forms=forms or [],
        navigation_links=nav_links or [],
        dynamic_signals=dynamic_signals or {"counter_value": "0"},
        has_validation_error=has_validation_error,
        validation_error_message=validation_error_message,
        menu_open=menu_open,
        a11y_snapshot=a11y_snapshot,
    )
    sig = dict(dynamic_signals or {"counter_value": "0"})
    if has_validation_error:
        sig["has_validation_error"] = True
    if menu_open:
        sig["menu_open"] = True
    cls_id, inst_id = compute_state_class_and_instance(
        url=url,
        headings=raw.headings,
        forms=raw.forms,
        dynamic_signals=sig,
        a11y_snapshot=a11y_snapshot,
    )
    raw.state_class_id = cls_id
    raw.state_id = inst_id
    return raw


def test_1_and_6_stable_state_class_across_in_page_mutations_and_distinct_state_instances():
    """
    Scenarios 1 & 6: In-page mutations (validation error banner, menu drawer open,
    badge counter increment, select dropdown change) MUST produce distinct state_instance_ids
    while preserving the SAME structural state_class_id so LLM page understanding is cached
    and agenda questions are not duplicated.
    """
    obs_base = _make_obs(
        state_id="init",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        headings=["Products"],
        dynamic_signals={"counter_value": "0", "selected_option": "az"},
        a11y_snapshot="document: Products Item 1 Add to cart",
    )
    obs_cart_1 = _make_obs(
        state_id="cart1",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        headings=["Products"],
        dynamic_signals={"counter_value": "1", "selected_option": "az"},
        a11y_snapshot="document: Products 1 Item 1 Remove",
    )
    obs_sorted = _make_obs(
        state_id="sorted",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        headings=["Products"],
        dynamic_signals={"counter_value": "1", "selected_option": "za"},
        a11y_snapshot="document: Products 1 Item 6 Add to cart",
    )
    obs_drawer = _make_obs(
        state_id="drawer",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        headings=["Products"],
        dynamic_signals={"counter_value": "1", "selected_option": "za"},
        menu_open=True,
        a11y_snapshot="document: All Items About Logout Products",
    )

    # All four are in-page variations of the same structural page class
    assert obs_base.state_class_id == obs_cart_1.state_class_id
    assert obs_base.state_class_id == obs_sorted.state_class_id
    assert obs_base.state_class_id == obs_drawer.state_class_id

    # Yet each has a distinct state_id capturing the in-page mutation
    instance_ids = {obs_base.state_id, obs_cart_1.state_id, obs_sorted.state_id, obs_drawer.state_id}
    assert len(instance_ids) == 4
    assert "c1" in obs_cart_1.state_id
    assert "opt_za" in obs_sorted.state_id
    assert "menu_open" in obs_drawer.state_id


def test_3_4_5_control_probes_select_support_equivalence_dedup_and_semantic_prioritization():
    """
    Scenarios 3, 4 & 5:
    - Standalone <select> dropdowns generate SELECT_OPTION probes with non-default option.
    - Repeated item card buttons (e.g. 6 'Add to cart' buttons) and image-only links are deduplicated.
    - Agenda prioritizes primary mutations (Add to cart, Sort select) and workflow progression (Cart link)
      above utility drawers (Open Menu).
    """
    controls = [
        ElementReference(
            element_id="btn_menu",
            role="button",
            accessible_name="Open Menu",
            tag_name="button",
            selector="button#react-burger-menu-btn",
        ),
        ElementReference(
            element_id="link_sidebar_all",
            role="link",
            accessible_name="All Items",
            tag_name="a",
            selector="a#inventory_sidebar_link",
        ),
        ElementReference(
            element_id="sel_sort",
            role="combobox",
            accessible_name="Name (A to Z)",
            tag_name="select",
            selector="select.product_sort_container",
            declared_constraints={
                "select_options": [
                    {"value": "az", "text": "Name (A to Z)", "selected": True},
                    {"value": "za", "text": "Name (Z to A)", "selected": False},
                ]
            },
        ),
        ElementReference(
            element_id="link_cart",
            role="link",
            accessible_name="",
            tag_name="a",
            selector="a.shopping_cart_link",
        ),
    ]
    # Add 6 repeated item cards with image links, title links, and "Add to cart" buttons
    for idx in range(6):
        controls.append(
            ElementReference(
                element_id=f"img_link_{idx}",
                role="link",
                accessible_name=f"Item {idx} image",
                tag_name="a",
                selector=f"a#item_{idx}_img_link",
                declared_constraints={"is_image_only_link": True},
            )
        )
        controls.append(
            ElementReference(
                element_id=f"title_link_{idx}",
                role="link",
                accessible_name=f"Product Item {idx}",
                tag_name="a",
                selector=f"a#item_{idx}_title_link",
            )
        )
        controls.append(
            ElementReference(
                element_id=f"btn_add_{idx}",
                role="button",
                accessible_name="Add to cart",
                tag_name="button",
                selector=f"button#add-to-cart-item-{idx}",
            )
        )

    obs = _make_obs(
        state_id="inv",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        headings=["Products"],
        controls=controls,
    )

    probes = generate_control_probes(obs)
    probe_selectors = [meta["selector"] for _, _, meta in probes]

    # 1. Image-only links are skipped completely
    assert not any("img_link" in s for s in probe_selectors)
    # 2. Only 1 representative "Add to cart" button is probed (not all 6)
    add_probes = [s for s in probe_selectors if "add-to-cart" in s]
    assert len(add_probes) == 1
    # 3. Only 1 representative item title link is probed (not all 6)
    title_probes = [s for s in probe_selectors if "title_link" in s]
    assert len(title_probes) == 1
    # 4. Standalone <select> sort dropdown is probed with SELECT_OPTION -> "za"
    select_probes = [(sc, pr, m) for sc, pr, m in probes if m.get("selector") == "select.product_sort_container"]
    assert len(select_probes) == 1
    assert select_probes[0][0].steps[0].action_type == "SELECT_OPTION"
    assert select_probes[0][0].steps[0].value_spec.value == "za"

    # 5. Agenda scoring prioritizes Add to cart, Sort dropdown, Cart link, and Item link ABOVE Open Menu
    agenda = InvestigationAgenda(max_probes=15)
    agenda.populate_from_observation(obs)

    q_first = agenda.select_next_question(obs.state_class_id)
    assert q_first is not None
    assert q_first.target_element in ("button#add-to-cart-item-0", "select.product_sort_container")

    # Verify Open Menu has a lower priority than primary mutation, workflow progression, and content navigation
    scores_by_sel = {
        q.target_element: q.priority
        for q in agenda.questions.values()
        if q.probe_family == "controls"
    }
    assert scores_by_sel["button#add-to-cart-item-0"] > scores_by_sel["button#react-burger-menu-btn"]
    assert scores_by_sel["select.product_sort_container"] > scores_by_sel["button#react-burger-menu-btn"]
    assert scores_by_sel["a.shopping_cart_link"] > scores_by_sel["button#react-burger-menu-btn"]
    assert scores_by_sel["a#item_0_title_link"] > scores_by_sel["button#react-burger-menu-btn"]


def test_cross_state_agenda_signature_deduplication():
    """
    Scenario 4 & 10: Re-observing the same route after an in-page mutation must NOT
    duplicate existing form, control, or invariant questions in the agenda.
    """
    ctrl = ElementReference(
        element_id="btn_add",
        role="button",
        accessible_name="Add to cart",
        tag_name="button",
        selector="button#add-to-cart",
    )
    obs1 = _make_obs(
        state_id="s1",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        controls=[ctrl],
        dynamic_signals={"counter_value": "0"},
    )
    obs2 = _make_obs(
        state_id="s2",
        url="https://shop.example.com/inventory.html",
        normalized_route="shop.example.com/inventory.html",
        controls=[ctrl],
        dynamic_signals={"counter_value": "1"},
    )

    agenda = InvestigationAgenda(max_probes=15)
    added_1 = agenda.populate_from_observation(obs1)
    added_2 = agenda.populate_from_observation(obs2)

    assert added_1 >= 2
    assert added_2 == 0
    assert agenda.deduplicated_count >= 2


def test_8_comparator_distinguishes_no_effect_from_verified_transitions():
    """
    Scenario 8: An action that executes without server error but produces zero DOM/state change
    when an interactive effect was predicted must return NO_EFFECT (not MATCHED) and transition_verified=False.
    """
    obs_before = _make_obs("s1", "https://shop.example.com/inventory.html", "shop.example.com/inventory.html")
    obs_after = _make_obs("s1", "https://shop.example.com/inventory.html", "shop.example.com/inventory.html")
    empty_diff = StateDiff()
    pred = Prediction(expect="Control interaction produces observable state change", signals=["no_server_error"])

    comp = compare_prediction_with_diff(
        pred,
        empty_diff,
        obs_before,
        obs_after,
        network_events=[NetworkEvent(seq=1, method="GET", url="https://shop.example.com/ping", resource_type="xhr", status=200)],
    )
    assert comp.verdict == ComparatorVerdict.NO_EFFECT
    assert comp.observed_effect == "NO_OBSERVABLE_CHANGE"
    assert comp.transition_verified is False

    # Now test a genuine in-page state mutation (counter_delta = +1)
    mut_diff = StateDiff(state_id_changed=True, counter_delta=1)
    obs_after_mut = _make_obs(
        "s2",
        "https://shop.example.com/inventory.html",
        "shop.example.com/inventory.html",
        dynamic_signals={"counter_value": "1"},
    )
    comp_mut = compare_prediction_with_diff(pred, mut_diff, obs_before, obs_after_mut, [])
    assert comp_mut.verdict == ComparatorVerdict.MATCHED
    assert comp_mut.observed_effect == "IN_PAGE_STATE_MUTATION"
    assert comp_mut.transition_verified is True


def test_9_10_12_branch_aware_workflows_deduplicated_counts_and_clean_playwright_export():
    """
    Scenarios 9, 10 & 12:
    - Negative form validation ('required_blank_all_form_1' -> '_with_error') is classified as
      'Validation / Constraint Branch' and excluded from 'Primary Discovered Progression'.
    - Utility drawer toggle ('Open Menu' -> '_menu_open') is classified as 'Exploratory / Return Branch'.
    - Positive progression (AUTHENTICATE -> Add to Cart -> Select Sort -> Navigate Cart) forms
      'Primary Workflow' and exports a clean, multi-step Playwright test script.
    - DOM element counts (buttons, headings, inputs) are deduplicated across same-route in-page states.
    """
    controls_inv = [
        ElementReference(element_id="b1", role="button", accessible_name="Add to cart", tag_name="button", selector="button#add-to-cart"),
        ElementReference(element_id="s1", role="combobox", accessible_name="Sort", tag_name="select", selector="select.product_sort_container"),
    ]
    st_login = _make_obs("st_login", "https://www.saucedemo.com/", "www.saucedemo.com", headings=["Swag Labs"])
    st_login_err = _make_obs(
        "st_login_err",
        "https://www.saucedemo.com/",
        "www.saucedemo.com",
        headings=["Swag Labs"],
        has_validation_error=True,
        validation_error_message="Epic sadface: Username is required",
    )
    st_inv_0 = _make_obs("st_inv_0", "https://www.saucedemo.com/inventory.html", "www.saucedemo.com/inventory.html", headings=["Products"], controls=controls_inv, dynamic_signals={"counter_value": "0"})
    st_inv_1 = _make_obs("st_inv_1", "https://www.saucedemo.com/inventory.html", "www.saucedemo.com/inventory.html", headings=["Products"], controls=controls_inv, dynamic_signals={"counter_value": "1"})
    st_inv_sort = _make_obs("st_inv_sort", "https://www.saucedemo.com/inventory.html", "www.saucedemo.com/inventory.html", headings=["Products"], controls=controls_inv, dynamic_signals={"counter_value": "1", "selected_option": "za"})
    st_inv_menu = _make_obs("st_inv_menu", "https://www.saucedemo.com/inventory.html", "www.saucedemo.com/inventory.html", headings=["Products"], controls=controls_inv, dynamic_signals={"counter_value": "1"}, menu_open=True)
    st_cart = _make_obs("st_cart", "https://www.saucedemo.com/cart.html", "www.saucedemo.com/cart.html", headings=["Your Cart"])

    transitions = [
        StateTransitionRecord(
            source_state_id=st_login.state_id,
            action_type="required_blank_all_form_1",
            target_element_desc="form_1",
            action_purpose="Verify blank login rejection",
            actual_outcome="Validation error shown",
            destination_state_id=st_login_err.state_id,
            observed_effect="VALIDATION_FEEDBACK",
            is_in_page_mutation=True,
            evidence_ids=["ev-1"],
        ),
        StateTransitionRecord(
            source_state_id=st_login.state_id,
            action_type="AUTHENTICATE",
            target_element_desc="login_form",
            action_purpose="Authenticate with discovered credentials",
            actual_outcome="Navigated to /inventory.html",
            destination_state_id=st_inv_0.state_id,
            observed_effect="ROUTE_TRANSITION",
            is_route_change=True,
            evidence_ids=["ev-2"],
        ),
        StateTransitionRecord(
            source_state_id=st_inv_0.state_id,
            action_type="CLICK_CONTROL:control_button_add_to_cart",
            target_element_desc="button#add-to-cart",
            action_purpose="Add product to cart",
            actual_outcome="Cart badge counter changed 0 -> 1",
            destination_state_id=st_inv_1.state_id,
            observed_effect="IN_PAGE_STATE_MUTATION",
            is_in_page_mutation=True,
            evidence_ids=["ev-3"],
        ),
        StateTransitionRecord(
            source_state_id=st_inv_1.state_id,
            action_type="SELECT_OPTION:control_select_sort",
            target_element_desc="select.product_sort_container",
            action_purpose="Change sort order to Name (Z to A)",
            actual_outcome="Selected option za",
            destination_state_id=st_inv_sort.state_id,
            observed_effect="IN_PAGE_STATE_MUTATION",
            is_in_page_mutation=True,
            evidence_ids=["ev-4"],
        ),
        StateTransitionRecord(
            source_state_id=st_inv_sort.state_id,
            action_type="CLICK_CONTROL:control_button_open_menu",
            target_element_desc="button#react-burger-menu-btn",
            action_purpose="Toggle utility menu drawer",
            actual_outcome="Navigation drawer opened",
            destination_state_id=st_inv_menu.state_id,
            observed_effect="IN_PAGE_STATE_MUTATION",
            is_in_page_mutation=True,
            evidence_ids=["ev-5"],
        ),
        StateTransitionRecord(
            source_state_id=st_inv_sort.state_id,
            action_type="CLICK_CONTROL:control_a_shopping_cart_link",
            target_element_desc="a.shopping_cart_link",
            action_purpose="Navigate to shopping cart",
            actual_outcome="Navigated to /cart.html",
            destination_state_id=st_cart.state_id,
            observed_effect="ROUTE_TRANSITION",
            is_route_change=True,
            evidence_ids=["ev-6"],
        ),
    ]

    state = {
        "target_url": "https://www.saucedemo.com/",
        "investigation_id": "inv-branch-test",
        "start_time": time.time() - 12.0,
        "auth_boundary_detected": True,
        "auth_attempted": True,
        "auth_verified": True,
        "auth_credentials": {"username": "standard_user", "password": "secret_sauce", "source": "DOM"},
        "states": {
            s.state_id: s
            for s in (st_login, st_login_err, st_inv_0, st_inv_1, st_inv_sort, st_inv_menu, st_cart)
        },
        "transitions": transitions,
        "frontier": {},
        "evidence_ledger": [EvidenceEntry(evidence_id=f"ev-{i}", investigation_id="inv-branch-test", source_state_id=st_login.state_id, evidence_type="PROBE_RESULT", summary="ok") for i in range(1, 7)],
        "rules_and_hypotheses": [],
    }

    report = InvestigationReportBuilder.build_report(state)

    # Deduplicated button/control count across 4 inventory states is 2, not 8!
    assert report.overview.total_buttons_discovered == 2
    assert report.overview.total_headings_discovered == 3

    # Workflow branches are cleanly separated
    branches = [w.workflow_branch for w in report.user_workflows]
    assert branches[0] == "Validation / Constraint Branch"
    assert branches[1] == "Primary Workflow"
    assert branches[2] == "Primary Workflow"
    assert branches[3] == "Primary Workflow"
    assert branches[4] == "Exploratory / Return Branch"
    assert branches[5] == "Primary Workflow"

    # Inferred journeys separate Primary Progression, Validation, and Exploratory branches
    journey_names = [j["name"] for j in report.inferred_journeys]
    assert "Primary Discovered Progression" in journey_names
    assert "Validation & Constraint Verification" in journey_names
    assert "Exploratory & Utility Branches" in journey_names

    # Exported Playwright script includes login, add-to-cart, select_option('za'), and cart navigation,
    # and excludes the burger menu toggle and blank-form error probe
    script = report.playwright_test_script
    assert "button#add-to-cart" in script
    assert "select_option('za')" in script
    assert "a.shopping_cart_link" in script
    assert "react-burger-menu-btn" not in script


def test_11_llm_latency_excluded_from_browser_duration_budget():
    """
    Scenario 11: Agenda and orchestrator duration checks exclude LLM inference wait time
    so local model latency does not falsely trigger TIMED_OUT on browser exploration.
    """
    agenda = InvestigationAgenda(max_probes=10, max_duration_seconds=30.0)
    # Simulate 45s wall-clock elapsed, of which 35s was spent waiting for local Ollama inference
    agenda.start_time = time.time() - 45.0
    agenda.browser_execution_time = 8.5
    agenda.llm_latency_seconds = 35.0

    # Add an unexplored question so agenda is not empty
    obs = _make_obs("s1", "https://example.com/", "example.com")
    ctrl = ElementReference(element_id="b1", role="button", accessible_name="Action", tag_name="button", selector="button#act")
    obs.interactive_controls = [ctrl]
    agenda.populate_from_observation(obs)

    stop, reason = agenda.should_stop()
    assert stop is False
    assert reason is None


def test_13_adaptive_mode_does_not_stop_on_probe_counts():
    """
    Verifies that when max_probes <= 0 (adaptive mode), the investigation is NOT
    terminated solely because a fixed number of probes was reached.
    """
    from app.investigation_agent.agenda import InvestigationQuestion
    agenda = InvestigationAgenda(max_probes=0, max_duration_seconds=0, max_mutations=0)
    for i in range(25):
        q_id = f"q_custom_{i}"
        agenda.questions[q_id] = InvestigationQuestion(
            id=q_id,
            category="discovery",
            target_state_class="cls_mock",
            hypothesis=f"Test hypothesis {i}",
            probe_family="controls",
            priority=0.8,
            status="unexplored",
        )

    # Simulate 20 productive probes
    for i in range(20):
        q = agenda.select_next_question(current_state_class="cls_mock")
        assert q is not None
        agenda.record_probe_outcome(
            question_id=q.id,
            verdict="STATE_TRANSITION",
            summary=f"Discovered new sub-view {i}",
            mutating=True,
            is_productive=True,
        )

    assert len(agenda.probe_history) == 20
    stop, reason = agenda.should_stop()
    # 5 pending questions remain, and probes were productive: MUST NOT stop on probe count
    assert stop is False
    assert reason is None


def test_14_information_saturation_and_stagnation_detection():
    """
    Verifies that the investigation engine detects information saturation and stops
    when N consecutive probes yield no new state discovery, route transition, or UI effect.
    """
    from app.investigation_agent.agenda import InvestigationQuestion
    agenda = InvestigationAgenda(max_probes=0, max_unproductive_probes=6)
    for i in range(12):
        q_id = f"q_sat_{i}"
        agenda.questions[q_id] = InvestigationQuestion(
            id=q_id,
            category="discovery",
            target_state_class="cls_mock",
            hypothesis=f"Saturation test hypothesis {i}",
            probe_family="controls",
            priority=0.8,
            status="unexplored",
        )

    # 1. First probe is productive: resets saturation counter
    q1 = agenda.select_next_question()
    agenda.record_probe_outcome(q1.id, "STATE_TRANSITION", "Advanced state", mutating=True, is_productive=True)
    assert agenda.consecutive_unproductive_probes == 0

    # 2. Next 5 probes are unproductive (repeated clicks, no DOM mutation)
    for _ in range(5):
        q = agenda.select_next_question()
        agenda.record_probe_outcome(q.id, "NO_OBSERVABLE_CHANGE", "No DOM change", mutating=False, is_productive=False)

    assert agenda.consecutive_unproductive_probes == 5
    stop, reason = agenda.should_stop()
    assert stop is False  # threshold is 6

    # 3. 6th unproductive probe triggers convergence
    q6 = agenda.select_next_question()
    agenda.record_probe_outcome(q6.id, "NO_OBSERVABLE_CHANGE", "No DOM change", mutating=False, is_productive=False)
    assert agenda.consecutive_unproductive_probes == 6

    stop, reason = agenda.should_stop()
    assert stop is True
    assert "saturation" in reason.lower() or "converged" in reason.lower()


def test_15_investigation_orchestrator_session_checkpoint_save_and_resume(tmp_path):
    """
    Verifies that an active investigation session can be checkpointed to disk and
    resumed seamlessly without losing the state graph, frontier, agenda questions,
    evidence ledger, or application understanding model.
    """
    from app.investigation_agent.workflow import InvestigationOrchestrator
    from app.investigation_agent.models import ApplicationUnderstandingModel

    orchestrator = InvestigationOrchestrator(headless=True)
    st1 = _make_obs("s_login", "https://example.com/login", "example.com/login", headings=["Sign In"])
    st2 = _make_obs("s_dashboard", "https://example.com/dashboard", "example.com/dashboard", headings=["Dashboard"])

    trans = StateTransitionRecord(
        source_state_id=st1.state_id,
        action_type="AUTHENTICATE",
        target_element_desc="Login Form",
        actual_outcome="Logged in",
        destination_state_id=st2.state_id,
        is_successful=True,
        is_route_change=True,
    )

    ev = EvidenceEntry(
        investigation_id="inv-cp-test",
        source_state_id=st1.state_id,
        evidence_type="STATE_TRANSITION",
        summary="User crossed authentication boundary",
    )

    app_model = ApplicationUnderstandingModel(
        application_domain="Enterprise Task Portal",
        observed_facts=["Sign In heading present", "Dashboard route unlocked"],
        inferred_hypotheses=["Authentication session persists"],
    )

    agenda = InvestigationAgenda(max_probes=0)
    agenda.populate_from_observation(st1, is_auth_boundary=True, has_auth_creds=True)
    orchestrator.agenda = agenda

    state = {
        "investigation_id": "inv-cp-test",
        "target_url": "https://example.com/login",
        "start_time": time.time() - 20.0,
        "browser_execution_time": 4.5,
        "current_state": st2,
        "states": {st1.state_id: st1, st2.state_id: st2},
        "transitions": [trans],
        "evidence_ledger": [ev],
        "rules_and_hypotheses": [],
        "frontier": {},
        "application_model": app_model,
        "auth_verified": True,
        "actions_attempted": 2,
        "actions_successful": 2,
    }

    cp_file = str(tmp_path / "test_session_checkpoint.json")
    saved_path = orchestrator.save_session_checkpoint(state, filepath=cp_file)
    assert saved_path == cp_file

    # Create fresh orchestrator and restore
    new_orchestrator = InvestigationOrchestrator(headless=True)
    restored = new_orchestrator.load_session_checkpoint(cp_file)

    assert restored["investigation_id"] == "inv-cp-test"
    assert restored["target_url"] == "https://example.com/login"
    assert restored["current_state"].state_id == st2.state_id
    assert len(restored["states"]) == 2
    assert len(restored["transitions"]) == 1
    assert restored["transitions"][0].destination_state_id == st2.state_id
    assert len(restored["evidence_ledger"]) == 1
    assert restored["auth_verified"] is True
    assert restored["application_model"].application_domain == "Enterprise Task Portal"
    assert new_orchestrator.agenda is not None
    assert len(new_orchestrator.agenda.questions) >= 1
    assert restored["browser_execution_time"] == 4.5


def test_16_honest_status_and_unexplored_frontier_reason_reporting():
    """
    Verifies that when an investigation stops with pending frontier items:
    1. Status is truthfully set to BUDGET_EXHAUSTED or TIMED_OUT (never COMPLETED).
    2. Section H explicitly lists the unexplored candidates along with the specific reason.
    3. Separate telemetry metrics (duration, browser_execution_time, retries, mutations) are surfaced.
    """
    st_main = _make_obs("s_main", "https://example.com/portal", "example.com/portal", headings=["Portal"])
    agenda = InvestigationAgenda(max_probes=0)
    for i in range(5):
        st_main.interactive_controls.append(
            ElementReference(
                element_id=f"feat_{i}",
                role="button",
                accessible_name=f"Feature {i}",
                tag_name="button",
                selector=f"button#feat_{i}",
            )
        )
    agenda.populate_from_observation(st_main)

    state = {
        "target_url": "https://example.com/portal",
        "investigation_id": "inv-honest-test",
        "start_time": time.time() - 305.0,
        "browser_execution_time": 25.4,
        "llm_latency_seconds": 280.0,
        "current_state": st_main,
        "states": {st_main.state_id: st_main},
        "transitions": [],
        "evidence_ledger": [],
        "frontier": {},
        "agenda": agenda,
        "actions_attempted": 3,
        "actions_successful": 3,
        "stopping_reason": "Browser execution time budget reached (300.2s / 300.0s)",
    }

    report = InvestigationReportBuilder.build_report(state)

    # 1. Truthful status: NEVER COMPLETED when items remain unexplored!
    assert report.overview.status == InvestigationStatus.TIMED_OUT
    assert report.overview.pending_frontier_count >= 1
    assert report.overview.browser_execution_time == 25.4

    # 2. Section H lists candidates and explicit reasons
    cov = report.investigation_coverage_frontier
    unexplored = cov["unexplored_candidates"]
    assert len(unexplored) >= 1
    assert all("budget reached" in c["reason_unexplored"].lower() for c in unexplored)

    # 3. Markdown rendering contains the table with reasons and distinct browser duration
    md = InvestigationReportBuilder.to_markdown(report)
    assert "## Section H: Investigation Coverage & Frontier" in md
    assert "### Unexplored Candidates Remaining:" in md
    assert "| Reason Not Probed |" in md
    assert "Browser Execution Duration" in md
