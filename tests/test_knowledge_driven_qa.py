"""
Knowledge-driven planning, finding lifecycle, findings registry and investigation memory.
"""

import json
import os
from types import SimpleNamespace

import pytest

from app.intelligence.target_context import inventory_from_report
from app.knowledge.qa_retrieval import (
    PREDICATES, QAKnowledgeBase, QAKnowledgeRetriever, evidence_profile, get_qa_knowledge_base,
)
from test_findings_scoring_alignment_persistence import APEX_REQUIREMENTS, apex_report, mismatch_lead
from test_multi_website_generalization import taskflow_report


def plan(report, planner=None):
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    planner = planner or KnowledgeGuidedTestPlanner()
    cases, _, _ = planner.plan_test_suite_sync(report, target_count=300)
    return cases, planner.last_diagnostics


# ── Knowledge base integrity and retrieval ───────────────────────────────────

def test_knowledge_base_is_valid_and_traceable():
    kb = get_qa_knowledge_base()
    assert kb.validate() == []
    kinds = {e.kind for e in kb.entries.values()}
    assert kinds == {"DEFECT_CLASS", "TEST_STRATEGY", "ORACLE", "APP_PATTERN"}
    categories = {e.category for e in kb.by_kind("DEFECT_CLASS")}
    assert {"input_validation", "functional_workflow", "state_consistency", "authentication", "accessibility"} <= categories
    for e in kb.entries.values():
        for p in e.applicability.all + e.applicability.any + e.applicability.none:
            assert p in PREDICATES


def test_retrieval_selects_relevant_and_excludes_irrelevant_strategies():
    apex = evidence_profile(inventory_from_report(apex_report([])))
    tf = evidence_profile(inventory_from_report(taskflow_report()))
    r = QAKnowledgeRetriever()
    apex_ids = {x.entry.id for x in r.retrieve(apex)}
    tf_ids = {x.entry.id for x in r.retrieve(tf)}
    assert {"TS-SERVER-ENFORCEMENT", "TS-CROSS-FIELD", "TS-EP-BVA"} <= apex_ids
    assert "TS-INVARIANT-SORT" not in apex_ids          # Apex has no sort control
    assert "TS-INVARIANT-SORT" in tf_ids and "TS-CROSS-FIELD" not in tf_ids
    excluded = {e.id: e.reason for e in r.last_excluded}
    assert "missing required evidence" in excluded["TS-CROSS-FIELD"]


def test_contradicting_evidence_excludes_entry(tmp_path):
    entry = {"id": "TS-X", "kind": "TEST_STRATEGY", "category": "x", "title": "x", "description": "x",
             "applicability": {"all": ["has_forms"], "none": ["has_login_form"]},
             "automation": {"status": "AUTOMATED", "implementation": "FIELD_BOUNDARIES"}}
    (tmp_path / "k.json").write_text(json.dumps([entry]), encoding="utf-8")
    retriever = QAKnowledgeRetriever(QAKnowledgeBase(str(tmp_path)))
    profile = evidence_profile(inventory_from_report(apex_report([])))  # has a login form
    assert retriever.retrieve(profile) == []
    assert "contradicting evidence" in retriever.last_excluded[0].reason


def test_missing_information_yields_no_strategies_rather_than_guesses():
    empty = SimpleNamespace(forms=[], controls=[], states=[], transitions=[], auth=None, navigable_routes=lambda: [])
    selected = QAKnowledgeRetriever().retrieve(evidence_profile(empty))
    assert selected == []


def test_invalid_knowledge_is_rejected(tmp_path):
    bad = {"id": "TS-BAD", "kind": "TEST_STRATEGY", "category": "x", "title": "x", "description": "x",
           "applicability": {"all": ["has_unicorns"]}, "related_ids": ["DC-NOPE"]}
    (tmp_path / "k.json").write_text(json.dumps([bad]), encoding="utf-8")
    with pytest.raises(ValueError) as err:
        QAKnowledgeBase(str(tmp_path))
    assert "has_unicorns" in str(err.value) and "DC-NOPE" in str(err.value)


# ── Knowledge drives planning ────────────────────────────────────────────────

def test_knowledge_produces_new_mechanisms_with_traceability():
    cases, diag = plan(apex_report([]))  # no requirements: rules come from HTML and semantics only
    kb = get_qa_knowledge_base()
    server = [tc for tc in cases if (tc.derived_values or {}).get("layer") == "SERVER"]
    cross = [tc for tc in cases if (tc.derived_values or {}).get("constraint_kind") == "CROSS_FIELD_EQUALITY"]
    assert {tc.derived_values["field_selector"] for tc in server} >= {"#terms", "#q", "#fullname"}
    assert not any(tc.derived_values["field_selector"] == "#password" and tc.derived_values["input_class"] == "TOO_SHORT"
                   for tc in server), "length rules are not probed server-side on login forms"
    assert [tc.derived_values["field_selector"] for tc in cross] == ["#confirm-password"]
    assert cross[0].steps[-1].assertion.expected_source == "SEMANTIC_CONVENTION"
    for tc in cases:
        assert tc.knowledge_ids, tc.test_id
        assert all(kb.get(k) for k in tc.knowledge_ids)
    assert "SERVER_SIDE_ENFORCEMENT" in diag["strategies_applied"]
    assert any(s["id"] == "TS-CROSS-FIELD" for s in diag["knowledge_selected"])


def test_removing_a_strategy_removes_its_tests(tmp_path):
    """Planning follows the knowledge base: without the server-enforcement entry, no such tests are made."""
    import shutil
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    from app.knowledge import qa_retrieval
    for name in os.listdir(qa_retrieval.QA_KNOWLEDGE_DIR):
        shutil.copy(os.path.join(qa_retrieval.QA_KNOWLEDGE_DIR, name), tmp_path / name)
    strategies = json.loads((tmp_path / "test_strategies.json").read_text(encoding="utf-8"))
    (tmp_path / "test_strategies.json").write_text(json.dumps([s for s in strategies if s["id"] != "TS-SERVER-ENFORCEMENT"]), encoding="utf-8")
    defects = json.loads((tmp_path / "defect_taxonomy.json").read_text(encoding="utf-8"))
    for d in defects:
        d["related_ids"] = [r for r in d.get("related_ids", []) if r != "TS-SERVER-ENFORCEMENT"]
    (tmp_path / "defect_taxonomy.json").write_text(json.dumps(defects), encoding="utf-8")
    original = qa_retrieval._KB
    try:
        qa_retrieval._KB = QAKnowledgeBase(str(tmp_path))
        cases, diag = plan(apex_report([]))
    finally:
        qa_retrieval._KB = original
    assert not [tc for tc in cases if (tc.derived_values or {}).get("layer") == "SERVER"]
    assert "SERVER_SIDE_ENFORCEMENT" not in diag["strategies_applied"]


def test_requirement_rules_are_not_duplicated_by_server_probes():
    cases, _ = plan(apex_report(APEX_REQUIREMENTS))
    server_terms = [tc for tc in cases if (tc.derived_values or {}).get("layer") == "SERVER" and tc.derived_values["field_selector"] == "#terms"]
    assert server_terms == []  # "Accepting the Terms of Service is required" already exercises it server-side


def test_render_anomaly_invariant_added_to_replayed_interactions():
    cases, _ = plan(taskflow_report())
    from app.schemas import AssertionType
    replayed = [tc for tc in cases if tc.oracle_class in ("STATE_TRANSITION", "OBSERVED_STATE")]
    assert replayed and all(any(s.assertion and s.assertion.assertion_type == AssertionType.NO_TEXT_ANOMALY for s in tc.steps)
                            for tc in replayed)


# ── Finding lifecycle in the bug investigator ────────────────────────────────

@pytest.mark.asyncio
async def test_html_declared_server_failure_becomes_likely_defect():
    from app.bugs.investigator import BugInvestigator
    from app.schemas import ExecutionResult, ExecutionStatus
    cases, _ = plan(apex_report([]))
    tc = next(t for t in cases if (t.derived_values or {}).get("layer") == "SERVER")
    failed = ExecutionResult(test_id=tc.test_id, test_name=tc.name, status=ExecutionStatus.FAILED, total_steps=len(tc.steps),
                             steps_completed=len(tc.steps), failure_step=tc.steps[-1].step_number,
                             failure_reason="Expected feedback attributable to this rule; none was shown")

    class Runner:
        async def run_test(self, *a, **k):
            return failed
    bug = await BugInvestigator(runner=Runner(), verification_retries=2).investigate_failure(failed, tc, "http://127.0.0.1:8001/")
    assert bug.classification == "LIKELY_DEFECT" and bug.verification_status.value == "LIKELY_DEFECT"
    assert bug.expected_source == "HTML_DECLARED"


# ── Findings registry ────────────────────────────────────────────────────────

def _exec(results, bugs=(), exec_id="exec-00000001"):
    return {"execution_id": exec_id, "results": results, "bug_reports": list(bugs)}


def test_registry_merges_lead_and_regression_without_downgrade():
    from app.findings.registry import build_findings
    lead = mismatch_lead()
    report = {"confirmed_bug_leads": [lead], "overview": {"target_url": "http://127.0.0.1:8001/"}}
    reg = {"test_id": "TC-REG-01", "oracle_class": "DEFECT_REGRESSION", "derived_values": {"source_lead_id": lead["id"]},
           "steps": [{"action": "NAVIGATE", "value": "http://127.0.0.1:8001/registration"}]}
    passed = _exec([{"test_id": "TC-REG-01", "status": "PASSED"}])
    out = build_findings(report, [reg], [passed])
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f["classification"] == "LIKELY_DEFECT"                  # passing re-run does not erase it
    assert f["regression_test_ids"] == ["TC-REG-01"] and f["observations"][0]["status"] == "PASSED"
    failing = _exec([{"test_id": "TC-REG-01", "status": "FAILED"}],
                    [{"test_id": "TC-REG-01", "classification": "LIKELY_DEFECT", "title": "regression"}], "exec-00000002")
    f2 = build_findings(report, [reg], [passed, failing])["findings"][0]
    assert [s["kind"] for s in f2["sources"]] == ["investigation", "execution"]


def test_registry_excludes_infrastructure_and_unconfirmed_leads():
    from app.findings.registry import build_findings
    report = {"confirmed_bug_leads": [mismatch_lead(status="UNSTABLE")], "overview": {"target_url": "http://x.test/"}}
    case = {"test_id": "T1", "derived_values": {"field_selector": "#a", "constraint_kind": "REQUIRED"}, "steps": []}
    infra = _exec([{"test_id": "T1", "status": "LOCATOR_ERROR"}])
    assert build_findings(report, [case], [infra])["findings"] == []


def test_store_persists_findings_and_exposes_them(tmp_path):
    from app.runs import RunStore
    store = RunStore(str(tmp_path))
    run = store.create("http://127.0.0.1:8001/")
    store.complete(run.run_id, {"confirmed_bug_leads": [mismatch_lead()]}, [])
    detail = store.get(run.run_id)
    assert detail.findings["counts"] == {"LIKELY_DEFECT": 1}
    assert (tmp_path / run.run_id / "findings.json").is_file()
    case = {"test_id": "SRV-1", "derived_values": {"field_selector": "#q", "constraint_kind": "REQUIRED", "input_class": "EMPTY", "layer": "SERVER"},
            "steps": [{"action": "NAVIGATE", "value": "http://127.0.0.1:8001/search"}]}
    store.complete(run.run_id, {"confirmed_bug_leads": [mismatch_lead()]}, [case])
    store.save_execution(run.run_id, _exec([{"test_id": "SRV-1", "status": "FAILED"}],
                                           [{"test_id": "SRV-1", "classification": "LIKELY_DEFECT", "title": "q"}], "exec-0000000a"))
    findings = store.get(run.run_id).findings
    assert findings["counts"] == {"LIKELY_DEFECT": 2} and findings["sources"] == {"investigation": 1, "execution": 1}


# ── Investigation memory ─────────────────────────────────────────────────────

def test_memory_records_counts_only_and_caps_influence(tmp_path, monkeypatch):
    from app.knowledge import memory
    monkeypatch.setenv("VERITYQA_MEMORY_DIR", str(tmp_path))
    cases = [{"test_id": f"T{i}", "knowledge_ids": ["TS-SERVER-ENFORCEMENT"],
              "steps": [{"action": "FILL", "value": "s3cr3t-value"}]} for i in range(4)]
    results = [{"test_id": f"T{i}", "status": "FAILED"} for i in range(4)]
    bugs = [{"test_id": f"T{i}", "classification": "LIKELY_DEFECT"} for i in range(4)]
    memory.record_execution("inv-1", "http://127.0.0.1:8001", cases, results, bugs)
    eff = memory.strategy_effectiveness()
    assert 0 < eff["TS-SERVER-ENFORCEMENT"] <= 0.1
    blob = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert "s3cr3t-value" not in blob
    assert memory.site_history("http://127.0.0.1:8001")[0]["run_id"] == "inv-1"
    assert memory.site_history("http://127.0.0.1:8002") == []  # never shared across origins
    monkeypatch.setenv("VERITYQA_MEMORY_DISABLED", "1")
    assert memory.strategy_effectiveness() == {}
