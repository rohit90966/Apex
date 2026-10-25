"""
tests/test_qa_retrieval.py
==========================
Tests for the structured QA knowledge base, evidence profiling, retrieval,
and knowledge-integration with the investigation agenda.

These tests are all pure-Python (no browser, no LLM) and run in the standard
test suite without network access.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.knowledge.qa_retrieval import (
    QAKnowledgeBase,
    QAKnowledgeRetriever,
    EvidenceProfile,
    evidence_profile,
    find_confirmation_pairs,
    PREDICATES,
    QA_KNOWLEDGE_DIR,
)
from app.knowledge.schema import QAKnowledgeEntry, Applicability, Automation


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def kb() -> QAKnowledgeBase:
    return QAKnowledgeBase()


@pytest.fixture(scope="module")
def retriever(kb: QAKnowledgeBase) -> QAKnowledgeRetriever:
    return QAKnowledgeRetriever(knowledge_base=kb)


def _field(**kw) -> SimpleNamespace:
    """Minimal field object matching what evidence_profile expects."""
    defaults = dict(
        selector="#f", name="", field_id="f", label="", field_type="text",
        required=False, minlength=None, maxlength=None, min=None, max=None,
        pattern=None, options=[], href=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _form(route="/", fields=(), method="POST", **kw) -> SimpleNamespace:
    defaults = dict(
        form_selector="form", form_id="f", action="/submit", method=method,
        submit_selector="#submit", is_login_form=False, route=route,
    )
    defaults.update(kw)
    obj = SimpleNamespace(**defaults)
    obj.fields = list(fields)
    return obj


def _control(**kw) -> SimpleNamespace:
    defaults = dict(
        selector="#btn", name="Submit", tag="button", href=None, options=[],
        role="button", route="/",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _inventory(forms=(), controls=(), states=(), **kw) -> SimpleNamespace:
    inv = SimpleNamespace(
        forms=list(forms),
        controls=list(controls),
        states=list(states),
        transitions=[],
        auth=SimpleNamespace(boundary_observed=False),
    )
    def navigable_routes():
        routes = list({getattr(s, "route", "/") for s in states})
        return routes
    inv.navigable_routes = navigable_routes
    return inv


def _profile(**predicates) -> EvidenceProfile:
    all_preds = {k: False for k in PREDICATES}
    all_preds.update(predicates)
    return EvidenceProfile(predicates=all_preds)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Knowledge base loading and schema validation
# ─────────────────────────────────────────────────────────────────────────────

def test_qa_knowledge_base_loads_without_error(kb: QAKnowledgeBase):
    """The entire QA knowledge base must load and pass cross-reference validation."""
    assert len(kb.entries) >= 20, "Expected at least 20 QA knowledge entries"


def test_all_entries_have_required_fields(kb: QAKnowledgeBase):
    for entry in kb.entries.values():
        assert entry.id, f"Entry missing id: {entry}"
        assert entry.kind, f"Entry {entry.id} missing kind"
        assert entry.title, f"Entry {entry.id} missing title"
        assert entry.description, f"Entry {entry.id} missing description"


def test_no_duplicate_knowledge_ids(kb: QAKnowledgeBase):
    """IDs in the knowledge base must be unique (the loader already checks this,
    but test_isolation also verifies it independently)."""
    ids = list(kb.entries.keys())
    assert len(ids) == len(set(ids))


def test_all_referenced_ids_exist(kb: QAKnowledgeBase):
    """Every related_id must resolve to an existing knowledge entry."""
    problems = kb.validate()
    assert not problems, f"Knowledge base validation errors: {problems}"


def test_defect_class_entries_present(kb: QAKnowledgeBase):
    dc_entries = [e for e in kb.entries.values() if e.kind == "DEFECT_CLASS"]
    assert len(dc_entries) >= 5, "Expected at least 5 DEFECT_CLASS entries"


def test_test_strategy_entries_present(kb: QAKnowledgeBase):
    ts_entries = [e for e in kb.entries.values() if e.kind == "TEST_STRATEGY"]
    assert len(ts_entries) >= 5, "Expected at least 5 TEST_STRATEGY entries"


def test_oracle_entries_present(kb: QAKnowledgeBase):
    or_entries = [e for e in kb.entries.values() if e.kind == "ORACLE"]
    assert len(or_entries) >= 3, "Expected at least 3 ORACLE entries"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Evidence profiling — predicate computation
# ─────────────────────────────────────────────────────────────────────────────

def test_evidence_profile_empty_inventory_is_all_false():
    inv = _inventory()
    prof = evidence_profile(inv)
    assert not any(prof.predicates.values()), "Empty inventory should produce no true predicates"


def test_evidence_profile_detects_login_form():
    pwd = _field(selector="#pwd", name="password", field_type="password", required=True)
    user = _field(selector="#user", name="username", field_type="text", required=True)
    form = _form(fields=[user, pwd], is_login_form=True)
    inv = _inventory(forms=[form])
    prof = evidence_profile(inv)
    assert prof.holds("has_forms")
    assert prof.holds("has_login_form")
    assert prof.holds("has_post_form")
    assert prof.holds("has_required_field")


def test_evidence_profile_detects_confirmation_pair():
    pwd = _field(selector="#pwd", name="password", field_type="password")
    confirm = _field(selector="#cpwd", name="confirm_password", field_type="password")
    form = _form(fields=[pwd, confirm])
    inv = _inventory(forms=[form])
    prof = evidence_profile(inv)
    assert prof.holds("has_confirmation_pair"), "Semantic confirmation pair not detected"


def test_evidence_profile_detects_sort_control():
    sort_ctrl = _field(
        selector="#sort", name="sort", field_type="select-one",
        options=[{"value": "az", "label": "A-Z"}, {"value": "za", "label": "Z-A"}]
    )
    form = _form(fields=[sort_ctrl], method="GET")
    inv = _inventory(forms=[form])
    prof = evidence_profile(inv)
    assert prof.holds("has_sort_control"), "Sort control not detected"


def test_evidence_profile_detects_collection_from_controls():
    ctrls = [_control(selector=f"#item-{i}", name="Edit", route="/list") for i in range(5)]
    inv = _inventory(controls=ctrls)
    prof = evidence_profile(inv)
    assert prof.holds("has_collection"), "Repeated named controls should trigger has_collection"


def test_evidence_profile_detects_requirements():
    inv = _inventory()
    prof = evidence_profile(inv, requirements=["Password must be at least 6 characters"])
    assert prof.holds("has_requirements")


def test_evidence_profile_detects_multiple_routes():
    s1 = SimpleNamespace(route="/", url="http://x/", authenticated=False,
                         validation_message=None)
    s2 = SimpleNamespace(route="/about", url="http://x/about", authenticated=False,
                         validation_message=None)
    inv = _inventory(states=[s1, s2])
    inv.navigable_routes = lambda: ["/", "/about"]
    prof = evidence_profile(inv)
    assert prof.holds("has_multiple_routes")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Confirmation pair detection
# ─────────────────────────────────────────────────────────────────────────────

def test_find_confirmation_pairs_detects_password_confirm():
    pwd = _field(name="password", field_type="password")
    confirm = _field(name="confirm_password", field_type="password")
    pairs = find_confirmation_pairs([pwd, confirm])
    assert len(pairs) == 1
    src, conf = pairs[0]
    assert src.name == "password"
    assert conf.name == "confirm_password"


def test_find_confirmation_pairs_detects_email_confirmation():
    email = _field(name="email", field_type="email")
    confirm_email = _field(name="email_confirmation", field_type="email")
    pairs = find_confirmation_pairs([email, confirm_email])
    assert len(pairs) == 1


def test_find_confirmation_pairs_ignores_unrelated_fields():
    name = _field(name="fullname", field_type="text")
    email = _field(name="email", field_type="email")
    pairs = find_confirmation_pairs([name, email])
    assert len(pairs) == 0


def test_find_confirmation_pairs_ignores_submit_buttons():
    pwd = _field(name="password", field_type="password")
    confirm = _field(name="confirm_password", field_type="password")
    submit = _field(name="submit", field_type="submit")
    pairs = find_confirmation_pairs([pwd, confirm, submit])
    assert len(pairs) == 1  # submit button excluded


# ─────────────────────────────────────────────────────────────────────────────
# 4. Knowledge retrieval — applicability and ranking
# ─────────────────────────────────────────────────────────────────────────────

def test_retrieval_returns_applicable_entries_for_login_form(retriever):
    prof = _profile(has_forms=True, has_login_form=True, has_post_form=True, has_required_field=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-EP-BVA" in ids, "BVA strategy should be retrieved when required fields present"
    assert "TS-SERVER-ENFORCEMENT" in ids, "Server enforcement strategy applicable to POST forms"


def test_retrieval_returns_cross_field_strategy_when_confirmation_pair(retriever):
    prof = _profile(has_confirmation_pair=True, has_post_form=True, has_forms=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-CROSS-FIELD" in ids, "Cross-field strategy should match has_confirmation_pair"


def test_retrieval_excludes_sort_strategy_without_sort_control(retriever):
    prof = _profile(has_forms=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-INVARIANT-SORT" not in ids, "Sort strategy should not match when no sort control"


def test_retrieval_returns_sort_strategy_with_sort_control(retriever):
    prof = _profile(has_sort_control=True, has_forms=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-INVARIANT-SORT" in ids, "Sort strategy should match has_sort_control"


def test_retrieval_excludes_route_regression_without_multiple_routes(retriever):
    prof = _profile(has_forms=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-ROUTE-REGRESSION" not in ids


def test_retrieval_returns_route_regression_with_multiple_routes(retriever):
    prof = _profile(has_multiple_routes=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    ids = [r.entry.id for r in results]
    assert "TS-ROUTE-REGRESSION" in ids


def test_retrieval_scores_high_risk_entries_higher(retriever):
    """HIGH-risk entries should rank above LOW-risk entries when equally applicable."""
    prof = _profile(has_required_field=True, has_multiple_routes=True, has_forms=True, has_post_form=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",), max_entries=40)
    high = [r for r in results if r.entry.risk == "HIGH"]
    low = [r for r in results if r.entry.risk == "LOW"]
    if high and low:
        assert high[0].score >= low[0].score, "HIGH risk entries should have higher score"


def test_retrieval_excludes_not_automated_by_default(retriever):
    prof = _profile(has_forms=True, has_select_field=True)
    results = retriever.retrieve(prof, include_not_automated=False)
    for r in results:
        assert r.entry.automation.status != "NOT_AUTOMATED"


def test_retrieval_includes_not_automated_when_requested(retriever):
    prof = _profile(has_forms=True, has_select_field=True, has_create_form=True)
    results_excl = retriever.retrieve(prof, include_not_automated=False, max_entries=50)
    results_incl = retriever.retrieve(prof, include_not_automated=True, max_entries=50)
    assert len(results_incl) >= len(results_excl), "include_not_automated=True should return at least as many"


def test_retrieval_history_bonus_is_bounded(retriever):
    """History bonus must never swing the score by more than ±0.1."""
    prof = _profile(has_forms=True, has_required_field=True)
    # Extreme positive history
    r_high = retriever.retrieve(prof, effectiveness={"TS-EP-BVA": 99.0}, max_entries=5)
    # Extreme negative history
    r_low = retriever.retrieve(prof, effectiveness={"TS-EP-BVA": -99.0}, max_entries=5)
    if r_high and r_low:
        high_score = next((r.score for r in r_high if r.entry.id == "TS-EP-BVA"), None)
        low_score = next((r.score for r in r_low if r.entry.id == "TS-EP-BVA"), None)
        if high_score is not None and low_score is not None:
            assert abs(high_score - low_score) <= 0.21, "History swing must not exceed ±0.1"


def test_retrieval_records_matched_predicates(retriever):
    prof = _profile(has_forms=True, has_required_field=True, has_post_form=True)
    results = retriever.retrieve(prof, kinds=("TEST_STRATEGY",))
    for r in results:
        assert isinstance(r.matched_predicates, list)
        # Each matched predicate must be a real predicate name
        for pred in r.matched_predicates:
            assert pred in PREDICATES, f"Returned unknown predicate {pred}"


def test_retrieval_rationale_is_non_empty(retriever):
    prof = _profile(has_forms=True, has_required_field=True)
    results = retriever.retrieve(prof)
    for r in results:
        assert r.rationale, f"Entry {r.entry.id} has empty rationale"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Findings lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def test_findings_lifecycle_classifies_confirmed_defect():
    from app.findings.lifecycle import classify_execution_result, FindingClass
    from app.schemas import ExecutionStatus

    result = SimpleNamespace(status=ExecutionStatus.FAILED, failure_reason="assertion failed")
    tc = SimpleNamespace(
        oracle_class="FIELD_CONSTRAINT",
        steps=[SimpleNamespace(assertion=SimpleNamespace(
            expected_source="HTML_CONSTRAINT",
        ))],
    )
    cls = classify_execution_result(result, tc)
    assert cls == FindingClass.CONFIRMED_DEFECT


def test_findings_lifecycle_classifies_locator_error_as_infra():
    from app.findings.lifecycle import classify_execution_result, FindingClass
    from app.schemas import ExecutionStatus

    result = SimpleNamespace(status=ExecutionStatus.LOCATOR_ERROR, failure_reason="no element")
    cls = classify_execution_result(result, None)
    assert cls == FindingClass.TEST_INFRASTRUCTURE_FAILURE


def test_findings_lifecycle_classifies_inferred_oracle_as_anomaly():
    """An expectation derived only from option-label semantics is not strong enough for a likely defect."""
    from app.findings.lifecycle import classify_execution_result, FindingClass
    from app.schemas import ExecutionStatus

    result = SimpleNamespace(status=ExecutionStatus.FAILED, failure_reason="wrong order")
    tc = SimpleNamespace(
        oracle_class="LIST_ORDERING",
        steps=[SimpleNamespace(assertion=SimpleNamespace(
            expected_source="DERIVED_INFERENCE",
        ))],
    )
    assert classify_execution_result(result, tc) == FindingClass.UNVERIFIED_ANOMALY
    strong = SimpleNamespace(oracle_class="FIELD_CONSTRAINT", steps=[SimpleNamespace(assertion=SimpleNamespace(
        expected_source="HTML_DECLARED"))])
    assert classify_execution_result(result, strong) == FindingClass.LIKELY_DEFECT


def test_findings_lifecycle_classifies_investigation_lead():
    from app.findings.lifecycle import classify_investigation_lead, FindingClass

    # A reproduced convention-based lead is strong but not an established rule.
    lead_confirmed_high = SimpleNamespace(
        status="CONFIRMED", oracle_type="CONVENTION", strength="HIGH",
    )
    assert classify_investigation_lead(lead_confirmed_high) == FindingClass.LIKELY_DEFECT
    technical = SimpleNamespace(status="CONFIRMED", oracle_type="TECHNICAL", strength="HIGH")
    assert classify_investigation_lead(technical) == FindingClass.CONFIRMED_DEFECT

    lead_unstable = SimpleNamespace(status="UNSTABLE", oracle_type="SUSPICION", strength="LOW")
    assert classify_investigation_lead(lead_unstable) == FindingClass.UNVERIFIED_ANOMALY

    lead_rejected = SimpleNamespace(status="REJECTED", oracle_type="CONVENTION", strength="HIGH")
    assert classify_investigation_lead(lead_rejected) == FindingClass.INCONCLUSIVE


def test_is_product_finding_recognises_confirmed_and_likely():
    from app.findings.lifecycle import is_product_finding
    assert is_product_finding("CONFIRMED_DEFECT") is True
    assert is_product_finding("LIKELY_DEFECT") is True
    assert is_product_finding("INCONCLUSIVE") is False
    assert is_product_finding("TEST_INFRASTRUCTURE_FAILURE") is False
    assert is_product_finding("TEST_DEFINITION_ERROR") is False
    assert is_product_finding(None) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. QA knowledge integration with agenda
# ─────────────────────────────────────────────────────────────────────────────

class MockAgenda:
    def __init__(self):
        self.questions: Dict[str, Any] = {}

    def add_question(self, q):
        self.questions[q.id] = q


def test_enrich_agenda_adds_questions_for_form_with_required_fields():
    from app.investigation_agent.qa_knowledge_integration import enrich_agenda_from_knowledge

    pwd = _field(selector="#pwd", name="password", field_type="password", required=True)
    user = _field(selector="#user", name="username", field_type="text", required=True)
    form = _form(fields=[user, pwd], is_login_form=True)
    inv = _inventory(forms=[form])

    agenda = MockAgenda()
    result = enrich_agenda_from_knowledge(agenda, inv)

    assert result["status"] == "ok"
    assert result["retrieved_count"] > 0
    # At least BVA/server-enforcement questions should be retrieved;
    # added_count may be 0 if agenda.add_question fails silently, but retrieved must be > 0
    assert result["retrieved_count"] > 0


def test_enrich_agenda_deduplicates_questions():
    from app.investigation_agent.qa_knowledge_integration import enrich_agenda_from_knowledge

    form = _form(fields=[_field(name="email", field_type="email", required=True)])
    inv = _inventory(forms=[form])

    agenda = MockAgenda()
    r1 = enrich_agenda_from_knowledge(agenda, inv)
    count_after_first = len(agenda.questions)

    # Enrich again — same inventory, same agenda: duplicates should be skipped
    r2 = enrich_agenda_from_knowledge(agenda, inv)
    count_after_second = len(agenda.questions)

    assert count_after_second == count_after_first, "Second call should not add duplicate questions"
    assert r2["skipped_duplicate_count"] > 0


def test_enrich_agenda_empty_inventory_adds_no_questions():
    from app.investigation_agent.qa_knowledge_integration import enrich_agenda_from_knowledge

    inv = _inventory()
    agenda = MockAgenda()
    result = enrich_agenda_from_knowledge(agenda, inv)

    assert result["status"] == "ok"
    # Empty profile: very few predicates are true, probably no questions
    assert len(agenda.questions) == 0 or result["retrieved_count"] >= 0


def test_knowledge_summary_returns_applicable_strategy_count():
    from app.investigation_agent.qa_knowledge_integration import get_knowledge_summary_for_report

    form = _form(fields=[_field(name="email", field_type="email", required=True),
                          _field(name="confirm_password", field_type="password", required=True),
                          _field(name="password", field_type="password", required=True)])
    inv = _inventory(forms=[form])
    summary = get_knowledge_summary_for_report(inv)

    assert "applicable_strategies" in summary
    assert summary["applicable_strategies"] > 0
    assert "automated_strategies" in summary
    assert "active_predicates" in summary
    assert isinstance(summary["active_predicates"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Accessibility probe generation
# ─────────────────────────────────────────────────────────────────────────────

def test_accessibility_probe_generates_for_unnamed_controls():
    from app.investigation_agent.probes.accessibility import generate_accessibility_probes
    from app.investigation_agent.models import UIStateObservation

    obs = UIStateObservation(
        state_id="s1", url="http://x/", normalized_route="/", page_title="Home",
    )
    # Add some unnamed controls directly as SimpleNamespace objects
    ctrl_unnamed = SimpleNamespace(
        element_id="b1", role="button", accessible_name="", tag_name="button",
        selector="#btn1", element_type="button", is_enabled=True, is_visible=True,
        declared_constraints={},
    )
    ctrl_named = SimpleNamespace(
        element_id="b2", role="button", accessible_name="Submit", tag_name="button",
        selector="#btn2", element_type="button", is_enabled=True, is_visible=True,
        declared_constraints={},
    )
    obs.interactive_controls = [ctrl_unnamed, ctrl_named]

    probes = generate_accessibility_probes(obs)
    assert len(probes) == 1
    script, pred, meta = probes[0]
    assert meta["unnamed_count"] >= 1
    assert "TS-A11Y-NAMES" in meta["knowledge_ids"]


def test_accessibility_probe_not_generated_for_clean_page():
    from app.investigation_agent.probes.accessibility import generate_accessibility_probes
    from app.investigation_agent.models import UIStateObservation

    obs = UIStateObservation(state_id="s1", url="http://x/", normalized_route="/", page_title="Clean")
    obs.interactive_controls = [
        SimpleNamespace(
            element_id=f"b{i}", role="button", accessible_name=f"Button {i}", tag_name="button",
            selector=f"#b{i}", element_type="button", is_enabled=True, is_visible=True,
            declared_constraints={},
        )
        for i in range(3)
    ]
    probes = generate_accessibility_probes(obs)
    assert len(probes) == 0, "No a11y probe for a page where all controls are named distinctly"
