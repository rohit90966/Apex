"""
Target-independence, cross-target contamination, field-boundary and export-integrity tests.

Synthetic investigation reports stand in for live runs: each carries an observed inventory exactly as
InvestigationReportBuilder emits it, so planning behaviour is deterministic and fast.
"""

import ast
import json
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.execution.runner import QAPlanRunner
from app.export.playwright_exporter import PlaywrightTestExporter
from app.intelligence.archetypes import ArchetypeClassifier, ApplicationIntelligenceModel, ApplicationCapability
from app.intelligence.target_context import (
    ObservedField, ObservedForm, ObservedInventory, canonical_origin, inventory_from_report,
)
from app.investigation_agent.authenticator import AuthenticationManager
from app.knowledge.retriever_v2 import KnowledgePatternRetriever
from app.schemas import (
    ActionStep, ActionType, AssertionType, ExecutionAssertion, ExecutionStatus, TestCase, TestCaseStatus,
    is_meaningful_assertion,
)
from app.test_planning.field_constraints import plan_form_boundaries, parse_requirement, match_requirement_field
from app.test_planning.planner import KnowledgeGuidedTestPlanner, parse_sort_option
from app.test_planning.validator import GroundedTestValidator

SAUCEDEMO_ARTEFACTS = ["/inventory.html", "#user-name", "#login-button", "standard_user", "secret_sauce",
                       "#new-project", "#project-name", "#save-btn", "product_sort_container", "#first-name",
                       "#postal-code", "#finish", "add-to-cart"]
SAUCEDEMO_SORT_VALUES = {"za", "lohi", "hilo", "az"}


# ── Synthetic reports ─────────────────────────────────────────────────────────

def _state(sid, origin, route, headings, auth=False, reached="NAVIGATION", status=200, validation=None):
    return {"state_id": sid, "url": f"{origin}{route}", "route": route, "title": headings[0] if headings else "",
            "headings": headings, "http_status": status, "authenticated": auth, "text_snippet": " ".join(headings),
            "validation_message": validation, "reached_by": reached}


def _report(origin, run_id, states, controls=(), forms=(), transitions=(), auth=None, requirements=()):
    inventory = {"run_id": run_id, "origin": origin, "provenance": "LIVE_INVESTIGATION", "states": list(states),
                 "controls": list(controls), "forms": list(forms), "transitions": list(transitions),
                 "auth": auth or {}, "requirements": list(requirements)}
    return SimpleNamespace(
        overview=SimpleNamespace(target_url=origin + "/", investigation_id=run_id),
        target_context={"run_id": run_id, "origin": origin, "start_url": origin + "/", "requirements": list(requirements)},
        observed_inventory=inventory, inferred_journeys=[], confirmed_bug_leads=[], business_rules_and_hypotheses=[],
    )


def taskflow_report(run_id="run-taskflow", origin="http://127.0.0.1:8003", requirements=()):
    """A TaskFlow-like SaaS app: no login, custom sort options, profile and task forms."""
    states = [
        _state("s0", origin, "/", ["TaskFlow Dashboard"], reached="ENTRY"),
        _state("s1", origin, "/tasks", ["Tasks"]),
        _state("s2", origin, "/profile", ["Profile"]),
        _state("s3", origin, "/tasks/new", ["New Task"]),
    ]
    controls = [
        {"selector": "#task-sort-select", "tag": "select", "control_type": "select", "name": "Sort tasks",
         "options": ["due_asc", "due_desc", "priority", "title_asc", "title_desc"],
         "option_labels": ["Due date (earliest)", "Due date (latest)", "Priority", "Title (A-Z)", "Title (Z-A)"],
         "state_id": "s1", "url": f"{origin}/tasks", "route": "/tasks"},
        {"selector": "a[href=\"/tasks/new\"]", "tag": "a", "name": "Add task", "href": "/tasks/new",
         "state_id": "s1", "url": f"{origin}/tasks", "route": "/tasks"},
    ]
    forms = [
        {"form_id": "profile-form", "form_selector": "#profile-form", "action": "/profile", "method": "POST",
         "submit_selector": "#save-profile", "state_id": "s2", "url": f"{origin}/profile", "route": "/profile",
         "fields": [
             {"selector": "#display-name", "name": "display_name", "field_id": "display-name", "label": "Display name",
              "field_type": "text", "required": True, "maxlength": 20},
             {"selector": "#phone", "name": "phone", "field_id": "phone", "label": "Phone number", "field_type": "tel"},
             {"selector": "#email", "name": "email", "field_id": "email", "label": "Email", "field_type": "email", "required": True},
         ]},
        {"form_id": "task-form", "form_selector": "#task-form", "action": "/tasks/create", "method": "POST",
         "submit_selector": "#create-task", "state_id": "s3", "url": f"{origin}/tasks/new", "route": "/tasks/new",
         "fields": [
             {"selector": "#task-title", "name": "title", "field_id": "task-title", "label": "Task title",
              "field_type": "text", "required": True, "maxlength": 80},
             {"selector": "#estimate", "name": "estimate", "field_id": "estimate", "label": "Estimate (hours)",
              "field_type": "number", "min": "1", "max": "40"},
         ]},
    ]
    transitions = [{"transition_id": "t1", "source_state_id": "s1", "destination_state_id": "s3",
                    "action_type": "CLICK_CONTROL:control_add_task", "target": "a[href=\"/tasks/new\"]",
                    "route_change": True, "evidence_ids": ["ev-1"]}]
    return _report(origin, run_id, states, controls, forms, transitions, requirements=requirements)


def shop_report(run_id="run-shop", origin="https://www.saucedemo.com", with_creds=True):
    """A SauceDemo-like shop: login gate, authenticated catalogue with SauceDemo's own sort values."""
    states = [
        _state("l0", origin, "/", ["Swag Labs"], reached="ENTRY"),
        _state("i1", origin, "/inventory.html", ["Products"], auth=True, reached="FORM_SUBMISSION"),
        _state("i2", origin, "/inventory.html", ["Products"], auth=True, reached="NAVIGATION"),
    ]
    controls = [{"selector": "[data-test='product-sort-container']", "tag": "select", "name": "sort",
                 "options": ["az", "za", "lohi", "hilo"],
                 "option_labels": ["Name (A to Z)", "Name (Z to A)", "Price (low to high)", "Price (high to low)"],
                 "state_id": "i2", "url": f"{origin}/inventory.html", "route": "/inventory.html", "authenticated": True}]
    forms = [{"form_id": "login", "form_selector": "form", "method": "POST", "submit_selector": "#login-button",
              "state_id": "l0", "url": f"{origin}/", "route": "/",
              "fields": [{"selector": "#user-name", "name": "user-name", "field_id": "user-name", "field_type": "text"},
                         {"selector": "#password", "name": "password", "field_id": "password", "field_type": "password"}]}]
    transitions = [{"transition_id": "ta", "source_state_id": "l0", "destination_state_id": "i1",
                    "action_type": "AUTHENTICATE", "target": "#login-button", "route_change": True}]
    auth = {"boundary_observed": True, "login_route": "/", "credentials_available": with_creds, "verified": with_creds}
    return _report(origin, run_id, states, controls, forms, transitions, auth=auth)


def other_crud_report(run_id="run-crud2", origin="http://127.0.0.1:8102"):
    """A second CRUD app with entirely different selectors and routes."""
    states = [_state("c0", origin, "/", ["Board"], reached="ENTRY"), _state("c1", origin, "/x/cards.php", ["Cards"])]
    forms = [{"form_id": "card", "form_selector": "[data-qa='card-form']", "action": "/x/cards.php?op=create", "method": "POST",
              "submit_selector": "[data-qa='card-save']", "state_id": "c1", "url": f"{origin}/x/cards.php", "route": "/x/cards.php",
              "fields": [{"selector": "[data-qa='card-name']", "name": "name", "label": "Card name", "field_type": "text",
                          "required": True, "maxlength": 30}]}]
    return _report(origin, run_id, states, forms=forms)


@pytest.fixture(autouse=True)
def authorize_public_demo(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "verityqa_allowed_hosts", settings.verityqa_allowed_hosts + ",www.saucedemo.com")


def plan(report, requirements=None):
    planner = KnowledgeGuidedTestPlanner()
    cases, _, model = planner.plan_test_suite_sync(report, target_count=200)
    return cases, planner.last_diagnostics, model


def signatures(cases):
    return sorted(GroundedTestValidator.semantic_signature(tc) for tc in cases)


def all_text(cases):
    return json.dumps([tc.model_dump(mode="json") for tc in cases])


# ── Root causes of the TaskFlow contamination ────────────────────────────────

def test_taskflow_plan_contains_no_saucedemo_artefacts():
    cases, diag, _ = plan(taskflow_report())
    assert cases
    blob = all_text(cases)
    for artefact in SAUCEDEMO_ARTEFACTS:
        assert artefact not in blob, f"SauceDemo artefact {artefact!r} leaked into a TaskFlow plan"
    selects = [s for tc in cases for s in tc.steps if s.action == ActionType.SELECT]
    assert selects and not ({s.value for s in selects} & SAUCEDEMO_SORT_VALUES)
    assert not any(tc.page_category.value == "LOGIN" for tc in cases), "no login form was observed"
    assert all(tc.target_origin == "http://127.0.0.1:8003" and tc.investigation_id == "run-taskflow" for tc in cases)


def test_every_step_is_grounded_in_the_run_inventory():
    report = taskflow_report()
    inv = inventory_from_report(report)
    cases, _, _ = plan(report)
    for tc in cases:
        assert any(is_meaningful_assertion(s.assertion) for s in tc.steps)
        for s in tc.steps:
            if s.action in (ActionType.CLICK, ActionType.FILL, ActionType.SELECT):
                assert s.selector in inv.selectors()
            if s.action == ActionType.NAVIGATE:
                assert canonical_origin(s.value) == inv.origin and inv.route_observed(s.value)
            if s.assertion and s.assertion.assertion_type == AssertionType.URL_CONTAINS:
                assert inv.route_observed(s.assertion.expected_value)


def test_sort_options_use_observed_values_and_real_ordering_oracle():
    cases, diag, _ = plan(taskflow_report())
    sort_cases = [tc for tc in cases if tc.oracle_class == "LIST_ORDERING"]
    assert {tc.derived_values["option"] for tc in sort_cases} == {"title_asc", "title_desc"}
    for tc in sort_cases:
        a = tc.steps[-1].assertion
        assert a.assertion_type == AssertionType.LIST_SORTED and a.expected_source == "DERIVED_INFERENCE"
        assert json.loads(a.expected_value)["key"] == "text"
    unresolved = {h["option"] for h in diag["exploratory_hypotheses"]}
    assert {"due_asc", "due_desc", "priority"} <= unresolved, "unknown orderings must stay hypotheses"


def test_knowledge_template_selectors_never_become_test_selectors():
    report = taskflow_report()
    cases, diag, _ = plan(report)
    assert "#new-project" not in all_text(cases)
    # The SaaS creation pattern is either ineligible (capability missing) or inapplicable (no observed control).
    ids = {t["pattern_id"] for t in diag["inapplicable_templates"]} | {p["pattern_id"] for p in diag["ineligible_patterns"]}
    assert "PAT-SAAS-CRUD-01" in ids


def test_saucedemo_capabilities_retained_with_own_evidence():
    with_creds = shop_report()
    cases, diag, model = plan(with_creds)
    assert ApplicationCapability.USER_AUTHENTICATION in model.capabilities
    assert ApplicationCapability.PAGINATION_SORTING in model.capabilities
    sort_cases = [tc for tc in cases if tc.oracle_class == "LIST_ORDERING"]
    assert {tc.derived_values["option"] for tc in sort_cases} == {"az", "za", "lohi", "hilo"}
    for tc in sort_cases:
        assert tc.requires_test_account
        login = [s for s in tc.steps if s.selector in ("#user-name", "#password")]
        assert {s.value for s in login} == {"${CREDENTIAL_USERNAME}", "${CREDENTIAL_PASSWORD}"}
        assert tc.steps[3].assertion.expected_value == "/inventory.html"  # observed post-login landing route


# ── Invariants and adversarial scenarios ─────────────────────────────────────

def test_scenarios_1_2_investigation_order_does_not_change_plans():
    alone_tf, alone_sd = signatures(plan(taskflow_report())[0]), signatures(plan(shop_report())[0])
    planner = KnowledgeGuidedTestPlanner()
    first_sd = signatures(planner.plan_test_suite_sync(shop_report(), 200)[0])
    then_tf = signatures(planner.plan_test_suite_sync(taskflow_report(), 200)[0])
    first_tf = signatures(planner.plan_test_suite_sync(taskflow_report(), 200)[0])
    then_sd = signatures(planner.plan_test_suite_sync(shop_report(), 200)[0])
    assert then_tf == first_tf == alone_tf
    assert then_sd == first_sd == alone_sd


def test_scenario_3_repeated_investigation_of_same_target_is_stable():
    # Only the per-run record marker (which keeps created records distinguishable) may differ.
    first = [s.replace("VQA r1", "VQA <run>") for s in signatures(plan(taskflow_report("r1"))[0])]
    second = [s.replace("VQA r2", "VQA <run>") for s in signatures(plan(taskflow_report("r2"))[0])]
    assert first == second


def test_scenario_4_same_archetype_different_target_uses_its_own_selectors():
    tf, _, _ = plan(taskflow_report())
    other, _, _ = plan(other_crud_report())
    tf_sel = {s.selector for tc in tf for s in tc.steps if s.selector}
    other_sel = {s.selector for tc in other for s in tc.steps if s.selector}
    assert other_sel and not (tf_sel & other_sel)
    assert all(canonical_origin(s.value) == "http://127.0.0.1:8102" for tc in other for s in tc.steps if s.action == ActionType.NAVIGATE)


@pytest.mark.asyncio
async def test_scenario_5_case_from_one_origin_is_refused_on_another():
    case = plan(taskflow_report())[0][0]
    result = await QAPlanRunner().run_test(case, "http://127.0.0.1:8002/")
    assert result.status == ExecutionStatus.TEST_DEFINITION_ERROR
    assert "Cross-target execution refused" in result.failure_reason


def test_scenario_6_inventory_from_another_origin_is_rejected():
    report = taskflow_report()
    report.overview.target_url = "http://127.0.0.1:9999/"
    with pytest.raises(ValueError):
        inventory_from_report(report)


def test_scenario_7_patterns_require_evidenced_capabilities():
    model = ApplicationIntelligenceModel(target_url="http://x.test", capabilities=[ApplicationCapability.FORM_VALIDATION])
    retriever = KnowledgePatternRetriever()
    ids = {r.pattern.pattern_id for r in retriever.retrieve_patterns(model, max_patterns=50)}
    assert "PAT-ECOM-CART-01" not in ids and "PAT-BANK-XFER-01" not in ids
    assert "PAT-GEN-FIELDS-01" in ids and "PAT-GEN-ROUTE-01" in ids
    assert any(r.pattern_id == "PAT-ECOM-CART-01" for r in retriever.last_rejected)


def test_scenario_8_unconventional_routes_and_selectors_are_used_as_observed():
    cases, _, _ = plan(other_crud_report())
    assert any(s.selector == "[data-qa='card-name']" for tc in cases for s in tc.steps)
    assert any((s.value or "").endswith("/x/cards.php") for tc in cases for s in tc.steps if s.action == ActionType.NAVIGATE)


def test_scenario_9_selector_seen_only_in_another_investigation_is_rejected():
    inv = inventory_from_report(taskflow_report())
    validator = GroundedTestValidator("http://127.0.0.1:8003", inventory=inv)
    case = TestCase(test_id="X", name="leak", objective="o", steps=[
        ActionStep(step_number=1, action=ActionType.NAVIGATE, value="http://127.0.0.1:8003/tasks", description="go"),
        ActionStep(step_number=2, action=ActionType.CLICK, selector="#react-burger-menu-btn", description="click",
                   assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Tasks", description="d")),
    ])
    ok, reason = validator.validate_test_case(case)
    assert not ok and "was not observed" in reason


@pytest.mark.asyncio
async def test_scenario_10_auth_needed_without_credentials_is_blocked_not_guessed(monkeypatch):
    for k in ("VERITYQA_TEST_USERNAME", "VERITYQA_TEST_PASSWORD", "TARGET_USERNAME", "TARGET_PASSWORD", "VERITYQA_TEST_CREDENTIALS_ORIGIN"):
        monkeypatch.delenv(k, raising=False)
    cases, diag, _ = plan(shop_report(with_creds=False))
    assert not [tc for tc in cases if tc.oracle_class == "LIST_ORDERING"]
    assert any(u["route"] == "/inventory.html" for u in diag["unreachable_routes"])
    case = TestCase(test_id="A", name="auth", objective="o", target_origin="http://127.0.0.1:8003", steps=[
        ActionStep(step_number=1, action=ActionType.FILL, selector="#pw", value="${CREDENTIAL_PASSWORD}", description="pw",
                   assertion=ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value="/x", description="d"))])
    result = await QAPlanRunner().run_test(case, "http://127.0.0.1:8003/")
    assert result.status == ExecutionStatus.BLOCKED


def test_scenario_10b_credentials_never_flow_to_unrelated_origins(monkeypatch):
    monkeypatch.setenv("VERITYQA_TEST_USERNAME", "u")
    monkeypatch.setenv("VERITYQA_TEST_PASSWORD", "p")
    monkeypatch.delenv("VERITYQA_TEST_CREDENTIALS_ORIGIN", raising=False)
    assert AuthenticationManager.discover_credentials("http://127.0.0.1:8003/") is None
    monkeypatch.setenv("VERITYQA_TEST_CREDENTIALS_ORIGIN", "http://127.0.0.1:8003")
    assert AuthenticationManager.discover_credentials("http://127.0.0.1:8003/tasks")["source"] == "ENVIRONMENT"
    assert AuthenticationManager.discover_credentials("http://127.0.0.1:8004/") is None
    # demo registry is keyed by exact host:port, never by substring
    assert AuthenticationManager.discover_credentials("http://127.0.0.1:80011/") is None
    assert AuthenticationManager.discover_credentials("https://evil-saucedemo.com.example/") is None


def test_scenario_11_empty_or_failed_investigation_produces_no_invented_tests():
    report = _report("http://127.0.0.1:8003", "run-empty", states=[])
    from app.investigation_agent.test_synthesis import synthesize_with_diagnostics
    cases, diag = synthesize_with_diagnostics(report)
    assert cases == [] and "fallback" not in diag


@pytest.mark.asyncio
async def test_scenario_12_llm_provider_error_does_not_break_planning():
    class Broken:
        async def generate(self, **kw):
            raise RuntimeError("provider down")
    planner = KnowledgeGuidedTestPlanner(llm_provider=Broken())
    cases, _, _ = await planner.plan_test_suite(taskflow_report(), target_count=200)
    assert signatures(cases) == signatures(plan(taskflow_report())[0])


def test_scenario_13_unavailable_target_is_reported_unavailable():
    from scripts.run_multi_website_benchmark import _reachable
    assert _reachable("http://127.0.0.1:9/", timeout=0.5) is False


def test_scenario_14_requirement_overrides_conflicting_html_rule():
    form = ObservedForm.model_validate(taskflow_report().observed_inventory["forms"][1])
    plan_ = plan_form_boundaries(form, ["Task title must be at most 50 characters"])
    title_cases = [c for c in plan_.cases if c.field_selector == "#task-title" and c.constraint.kind == "MAX_LENGTH"]
    assert {c.input_value.__len__() for c in title_cases} == {49, 50, 51}
    assert all(c.constraint.source == "REQUIREMENT" for c in title_cases)
    assert next(c for c in title_cases if len(c.input_value) == 51).oracle == "SUBMISSION_FEEDBACK"


def test_scenario_15_field_with_unknown_range_is_uncertain_not_tested():
    cases, diag, _ = plan(taskflow_report())
    assert not any((tc.derived_values or {}).get("field_selector") == "#phone" for tc in cases)
    phone = [u for u in diag["unknown_constraints"] if u["field_selector"] == "#phone"]
    assert phone and "tel" in phone[0]["reason"]


def test_states_reached_inside_the_authenticated_session_require_login():
    """Pages reached (transitively) after a verified login need the login prelude; the login page does not."""
    from app.intelligence.target_context import build_inventory_from_state

    def obs(sid, route, t):
        return SimpleNamespace(url=f"https://shop.test{route}", page_title=sid, headings=[sid], http_status=200,
                               is_authenticated=False, visible_text_snippet="", validation_error_message=None,
                               validation_error_locator=None, captured_at=t, interactive_controls=[], forms=[])

    def tr(src, dst, act):
        return SimpleNamespace(transition_id=f"{src}-{dst}", source_state_id=src, destination_state_id=dst,
                               action_type=act, target_element_desc="#x", is_route_change=True, status="confirmed",
                               observed_effect="ROUTE_TRANSITION", evidence_ids=[])

    states = {"login": obs("login", "/", 10.0), "inv": obs("inv", "/inventory.html", 20.0),
              "cart": obs("cart", "/cart.html", 5.0)}  # capture time out of order on purpose
    inv = build_inventory_from_state({
        "target_url": "https://shop.test/", "states": states, "auth_verified": True,
        "transitions": [tr("login", "inv", "AUTHENTICATE"), tr("inv", "cart", "CLICK_CONTROL:control_cart")],
    })
    assert not inv.requires_auth("/")
    assert inv.requires_auth("/inventory.html") and inv.requires_auth("/cart.html")


# ── Field-level boundary design ──────────────────────────────────────────────

def _field_form(*fields, novalidate=False):
    return ObservedForm(form_id="f", form_selector="#f", method="POST", submit_selector="#go", route="/f",
                        novalidate=novalidate, fields=list(fields))


def test_maxlength_20_partitions():
    plan_ = plan_form_boundaries(_field_form(ObservedField(selector="#n", label="Name", maxlength=20)))
    lengths = sorted(len(c.input_value) for c in plan_.cases)
    assert lengths == [19, 20, 21]
    over = next(c for c in plan_.cases if len(c.input_value) == 21)
    assert over.oracle == "VALUE_LENGTH" and over.oracle_expected == "==20" and over.constraint.source == "HTML_CONSTRAINT"


def test_exact_ten_digit_phone_requirement_partitions():
    phone = ObservedField(selector="#phone", name="phone", label="Phone number", field_type="tel")
    plan_ = plan_form_boundaries(_field_form(phone), ["Phone number must contain exactly 10 digits"])
    values = {c.input_class: c for c in plan_.cases}
    by_len = {len(c.input_value): c.expected for c in plan_.cases if c.input_value.isdigit()}
    assert by_len == {9: "REJECT", 10: "ACCEPT", 11: "REJECT", 12: "REJECT"}
    assert values["NON_NUMERIC"].expected == "REJECT" and values["MIXED_SYMBOLS"].expected == "REJECT"
    assert all(c.constraint.source == "REQUIREMENT" for c in plan_.cases)


def test_tel_type_alone_implies_no_length_rule():
    plan_ = plan_form_boundaries(_field_form(ObservedField(selector="#phone", label="Phone", field_type="tel")))
    assert plan_.cases == [] and plan_.unknowns


def test_required_numeric_pattern_and_email_constraints():
    plan_ = plan_form_boundaries(_field_form(
        ObservedField(selector="#t", label="Title", required=True),
        ObservedField(selector="#h", label="Hours", field_type="number", min="1", max="40"),
        ObservedField(selector="#z", label="Zip", pattern="[0-9]{5}"),
        ObservedField(selector="#e", label="Email", field_type="email"),
    ))
    got = {(c.field_selector, c.oracle_expected) for c in plan_.cases}
    assert ("#t", "valueMissing") in got
    assert ("#h", "rangeOverflow") in got and ("#h", "rangeUnderflow") in got
    assert ("#z", "patternMismatch") in got and ("#z", "valid") in got
    assert ("#e", "typeMismatch") in got


def test_novalidate_form_without_requirement_has_no_required_oracle():
    plan_ = plan_form_boundaries(_field_form(ObservedField(selector="#t", required=True), novalidate=True))
    assert plan_.cases == []


def test_requirement_mapping_is_conservative():
    fields = [ObservedField(selector="#pw", label="Password"), ObservedField(selector="#pw2", label="Confirm password")]
    assert match_requirement_field("Password must be at least 6 characters", fields) is None  # ambiguous
    assert parse_requirement("Title must be between 3 and 50 characters", fields[0])[0].kind == "MIN_LENGTH"


def test_empty_value_tests_really_fill_empty_values():
    cases, _, _ = plan(taskflow_report())
    empty = [tc for tc in cases if (tc.derived_values or {}).get("input_class") == "EMPTY"]
    assert empty
    for tc in empty:
        target = tc.derived_values["field_selector"]
        assert [s.value for s in tc.steps if s.action == ActionType.FILL and s.selector == target] == [""]


# ── Validator: unsupported assumptions are rejected ──────────────────────────

def _validator():
    return GroundedTestValidator("http://127.0.0.1:8003", inventory=inventory_from_report(taskflow_report()))


def _case(steps, name="case", derived=None):
    return TestCase(test_id="V", name=name, objective="o", steps=steps, derived_values=derived)


def _nav(url="http://127.0.0.1:8003/tasks", assertion=None):
    return ActionStep(step_number=1, action=ActionType.NAVIGATE, value=url, description="n", assertion=assertion)


def test_validator_rejects_trivial_only_oracle():
    ok, reason = _validator().validate_test_case(_case([_nav(assertion=ExecutionAssertion(
        assertion_type=AssertionType.ELEMENT_VISIBLE, target_selector="body", description="b"))]))
    assert not ok and "meaningful oracle" in reason


def test_validator_rejects_cross_origin_navigation_and_unobserved_route():
    v = _validator()
    a = ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Tasks", description="t")
    assert not v.validate_test_case(_case([_nav("https://www.saucedemo.com/inventory.html", a)]))[0]
    assert not v.validate_test_case(_case([_nav("http://127.0.0.1:8003/inventory.html", a)]))[0]


def test_validator_rejects_ungrounded_expected_text_and_url():
    v = _validator()
    bad_text = ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value="Username is required", description="t")
    bad_url = ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value="/inventory", description="u")
    assert "not grounded" in v.validate_test_case(_case([_nav(assertion=bad_text)]))[1]
    assert "never observed" in v.validate_test_case(_case([_nav(assertion=bad_url)]))[1]


def test_validator_rejects_empty_named_case_that_fills_values():
    v = _validator()
    steps = [_nav("http://127.0.0.1:8003/profile"),
             ActionStep(step_number=2, action=ActionType.FILL, selector="#display-name", value="standard", description="f"),
             ActionStep(step_number=3, action=ActionType.CLICK, selector="#save-profile", description="c",
                        assertion=ExecutionAssertion(assertion_type=AssertionType.VALIDATION_FEEDBACK_SHOWN, description="v"))]
    ok, reason = v.validate_test_case(_case(steps, "Display name: empty is rejected",
                                            {"field_selector": "#display-name", "input_value": "", "input_class": "EMPTY"}))
    assert not ok and "fills" in reason
    ok2, reason2 = v.validate_test_case(_case(steps, "Empty Username"))
    assert not ok2 and "empty" in reason2.lower()


def test_validator_rejects_unobserved_select_option():
    steps = [_nav(), ActionStep(step_number=2, action=ActionType.SELECT, selector="#task-sort-select", value="za", description="s",
                                assertion=ExecutionAssertion(assertion_type=AssertionType.LIST_SORTED,
                                                             expected_value='{"key": "text", "direction": "desc"}', description="d"))]
    ok, reason = _validator().validate_test_case(_case(steps))
    assert not ok and "not observed" in reason


# ── Sort option semantics ────────────────────────────────────────────────────

@pytest.mark.parametrize("value,label,expected", [
    ("za", "Name (Z to A)", ("text", "desc")),
    ("lohi", "Price (low to high)", ("number", "asc")),
    ("hilo", "", ("number", "desc")),
    ("title_asc", "Title (A-Z)", ("text", "asc")),
    ("due_asc", "Due date (earliest)", None),
    ("priority", "Priority", None),
])
def test_parse_sort_option(value, label, expected):
    assert parse_sort_option(value, label) == expected


# ── Classifier ───────────────────────────────────────────────────────────────

def test_classifier_mixed_unknown_and_missing_evidence():
    clf = ArchetypeClassifier()
    tf_model = clf.classify_from_investigation(taskflow_report())
    assert ApplicationCapability.FORM_VALIDATION in tf_model.capabilities
    assert ApplicationCapability.PAGINATION_SORTING in tf_model.capabilities
    assert ApplicationCapability.CART_MANAGEMENT not in tf_model.capabilities
    assert all(tf_model.capability_evidence[c.value] for c in tf_model.capabilities)
    empty = clf.classify_from_investigation(_report("http://127.0.0.1:8003", "e", states=[]))
    assert empty.capabilities == [] and empty.primary_archetype.value == "GENERIC_WEB"
    # Conflicting evidence: an e-commerce word in a heading is not a cart capability without structure.
    report = taskflow_report()
    report.observed_inventory["states"][0]["headings"] = ["Shopping cart of ideas"]
    model = clf.classify_from_investigation(report)
    assert ApplicationCapability.CART_MANAGEMENT not in model.capabilities


# ── Export integrity ─────────────────────────────────────────────────────────

def test_export_preserves_assertions_and_never_prints_pass():
    report = shop_report()
    cases, _, _ = plan(report)
    cases.append(TestCase(test_id="INV", name="invalid", objective="o", status=TestCaseStatus.INVALID,
                          invalid_reason="bad oracle", steps=[]))
    exporter = PlaywrightTestExporter()
    script = exporter.export_to_python_script(cases, "https://www.saucedemo.com/")
    tree = ast.parse(script)
    funcs = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name.startswith("test_")}
    assert len(funcs) == len(cases)
    assert "print(" not in script and "PASS]" not in script
    assert "secret_sauce" not in script and "standard_user" not in script
    semantics = {s["test_id"]: s for s in exporter.export_semantics(cases, "https://www.saucedemo.com/")}
    for (name, fn), tc in zip(funcs.items(), cases):
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "check"]
        assert len(calls) == len(semantics[tc.test_id]["assertions"])
    assert "pytest.skip('INVALID: bad oracle')" in script


def test_export_semantics_match_runner_steps():
    cases, _, _ = plan(taskflow_report())
    runner = QAPlanRunner()
    exporter = PlaywrightTestExporter(runner)
    for sem, tc in zip(exporter.export_semantics(cases, "http://127.0.0.1:8003/"), cases):
        steps = runner.synthesize_steps(tc, "http://127.0.0.1:8003/")
        assert sem["actions"] == [(s.action.value, s.selector, s.value) for s in steps]
        assert sem["assertions"] == [(s.assertion.assertion_type.value, s.assertion.target_selector, s.assertion.expected_value)
                                     for s in steps if s.assertion]
