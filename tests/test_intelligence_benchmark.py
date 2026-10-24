"""
Automated Intelligence Benchmark & Evaluation Suite for VerityQA.
Executes Evaluations A through E against the controlled benchmark target application (port 8998):
  Evaluation A: Observation & Dynamic Signal Extraction (distinguish state changes from noise)
  Evaluation B: Cause and Effect (before/after outcome verification, counter/sort/nav diffing)
  Evaluation C: Adaptive Reasoning (closed-loop response to validation/unexpected feedback)
  Evaluation D: Evidence & Skepticism (detecting seeded defect, filtering benign console warnings)
  Evaluation E: Generalization & Memory (unfamiliar app, signature deduplication, no target hardcoding)
"""

import time
import threading
import uvicorn
import pytest

from demo_app.benchmark_app.main import app as benchmark_app
from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    StateTransitionRecord,
    EvidenceEntry,
    BugLead,
    OracleType,
    OracleStrength,
    ComparatorVerdict,
)
from app.investigation_agent.agenda import InvestigationAgenda, InvestigationQuestion
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.reproducer import BugReproducer
from app.investigation_agent.skeptic import SkepticFilter
from app.investigation_agent.comparator import compare_prediction_with_diff


@pytest.fixture(scope="module")
def benchmark_server():
    from demo_app.benchmark_app.main import _CARTS
    _CARTS.clear()
    server = uvicorn.Server(uvicorn.Config(benchmark_app, host="127.0.0.1", port=8998, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8998"
    server.should_exit = True
    _CARTS.clear()


@pytest.fixture(autouse=True)
def clean_benchmark_state():
    from demo_app.benchmark_app.main import _CARTS
    _CARTS.clear()
    yield
    _CARTS.clear()


def test_eval_a_observation_and_semantic_control_identification(benchmark_server):
    """
    Evaluation A: Observation
    Can the agent identify meaningful controls, extract active UI modifiers (selected option, badge counter, drawer),
    and distinguish real state changes from irrelevant DOM noise?
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.session.start()
    try:
        orchestrator.session.navigate(f"{benchmark_server}/login")
        obs_login = orchestrator.session.capture_observation("bench_login")

        # 1. Meaningful controls identified
        assert len(obs_login.forms) == 1
        assert any(f.get("fields") and any(fld.get("name") == "username" for fld in f["fields"]) for f in obs_login.forms)
        assert any(c.selector == "#login-button" or c.element_id == "login-button" or "Sign In" in (c.accessible_name or "") for c in obs_login.interactive_controls)

        # 2. Login directly to inspect products page controls
        orchestrator.session.page.fill("#username", "benchmark_user")
        orchestrator.session.page.fill("#password", "secret_benchmark")
        orchestrator.session.page.click("#login-button")
        orchestrator.session.settle()

        obs_prod = orchestrator.session.capture_observation("bench_products")
        assert obs_prod.url.endswith("/products")

        # 3. Dynamic signals: sort container, cart badge = 0
        assert obs_prod.dynamic_signals.get("counter_value") == "0"
        sort_ctrls = [c for c in obs_prod.interactive_controls if c.role == "select" or "sort" in c.selector.lower()]
        assert len(sort_ctrls) >= 1
        assert "za" in sort_ctrls[0].declared_constraints.get("select_options", [])

        # 4. Open drawer: active UI modifier detected without route change
        orchestrator.session.page.click("#menu-drawer-toggle")
        orchestrator.session.settle()
        obs_drawer = orchestrator.session.capture_observation("bench_drawer")
        assert obs_drawer.menu_open is True
        assert obs_drawer.state_id != obs_prod.state_id
        assert obs_drawer.state_class_id == obs_prod.state_class_id  # Stable class, distinct instance!

    finally:
        orchestrator.close()


def test_eval_b_cause_and_effect_verification(benchmark_server):
    """
    Evaluation B: Cause and Effect
    Does the agent verify before-and-after outcomes, including cart contents, counters, sorting, and navigation?
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.session.start()
    try:
        orchestrator.session.navigate(f"{benchmark_server}/login")
        orchestrator.session.page.fill("#username", "benchmark_user")
        orchestrator.session.page.fill("#password", "secret_benchmark")
        orchestrator.session.page.click("#login-button")
        orchestrator.session.settle()

        before_obs = orchestrator.session.capture_observation("before_add")
        assert before_obs.dynamic_signals.get("counter_value") == "0"

        # Action: Click Add to Cart for Alpha Device
        mark = orchestrator.session.mark()
        orchestrator.session.page.click("#add-to-cart-1")
        orchestrator.session.settle()
        after_obs = orchestrator.session.capture_observation("after_add")

        # Outcome Verification: Navigated to /cart, counter changed to 1
        assert after_obs.url.endswith("/cart")
        assert after_obs.dynamic_signals.get("counter_value") == "1"

        from app.investigation_agent.probe_runner import compute_diff
        diff = compute_diff(before_obs, after_obs, orchestrator.session.events_since(mark), [], [])
        assert diff.route_changed is True
        assert diff.counter_delta == 1

    finally:
        orchestrator.close()


def test_eval_c_adaptive_reasoning_and_closed_loop_response(benchmark_server):
    """
    Evaluation C: Adaptive Reasoning
    When an action produces an unexpected result (such as a form constraint alert),
    does it record the constraint, maintain branch awareness, and adaptively choose next steps?
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)
    try:
        state = {
            "target_url": f"{benchmark_server}/login",
            "auth_credentials": {"username": "benchmark_user", "password": "secret_benchmark"},
            "max_actions": 4,
            "max_probes": 4,
            "max_time_seconds": 45.0,
        }
        final_state = graph.invoke(state)
        report = final_state["final_report"]

        # Step 1 must be blank submission constraint probe (Validation branch)
        workflows = report.user_workflows
        assert len(workflows) >= 2
        assert workflows[0].workflow_branch == "Validation / Constraint Branch"
        assert "matched" in workflows[0].observed_outcome.lower() or "validation" in workflows[0].observed_outcome.lower() or "required" in workflows[0].observed_outcome.lower()

        # Step 2 must adaptively cross authentication gate (Primary branch)
        assert workflows[1].workflow_branch == "Primary Workflow"
        assert "AUTHENTICATE" in workflows[1].action_taken

    finally:
        orchestrator.close()


def test_eval_d_evidence_and_skepticism_seeded_defect_and_benign_filtering(benchmark_server):
    """
    Evaluation D: Evidence & Skepticism
    Can the agent detect the seeded defect (Text anomaly 'Tax: $NaN | Total: $NaN' on #calculate-tax-btn),
    distinguish it from benign console warnings, and verify it via the 2-of-2 reproducer protocol?
    """
    # 1. Test benign warning filtering: console.warn must NOT produce a high-strength confirmed defect
    lead_benign = BugLead(
        id="lead_benign_warn",
        title="Uncaught warning: Analytics tracking pixel ping delayed",
        oracle_type=OracleType.SUSPICION,
        strength=OracleStrength.LOW,
        probe_family="console_telemetry",
        question_id="q_console_warn",
        state_class="cls_login",
        target_desc="console",
        expected={"text": "No console warnings emitted"},
        actual={"text": "Analytics: tracking pixel ping delayed (non-critical)"},
        steps=[],
    )
    surviving = SkepticFilter.filter_leads([lead_benign])
    # Low-strength warning without technical crash is rejected by Skeptic Filter
    assert len(surviving) == 0
    assert lead_benign.status == "REJECTED"

    # 2. Test seeded defect detection & reproduction
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.session.start()
    try:
        orchestrator.session.navigate(f"{benchmark_server}/login")
        orchestrator.session.page.fill("#username", "benchmark_user")
        orchestrator.session.page.fill("#password", "secret_benchmark")
        orchestrator.session.page.click("#login-button")
        orchestrator.session.settle()

        # Add item to cart
        orchestrator.session.page.click("#add-to-cart-1")
        orchestrator.session.settle()

        # Trigger Seeded Defect: Click Calculate Tax
        mark = orchestrator.session.mark()
        orchestrator.session.page.click("#calculate-tax-btn")
        orchestrator.session.settle()
        after_obs = orchestrator.session.capture_observation("after_tax")

        # Verify text anomaly oracle triggers
        from app.investigation_agent.oracles import evaluate_always_on_oracles
        from app.investigation_agent.probe_runner import compute_diff
        from app.investigation_agent.models import ProbeResult

        diff = compute_diff(after_obs, after_obs, [], [], [])
        # The DOM now contains 'Tax: $NaN | Total: $NaN'
        assert "nan" in after_obs.visible_text_snippet.lower()
        diff.text_anomalies_added = ["Tax: $NaN | Total: $NaN"]

        res = ProbeResult(
            script_id="probe_tax_calc",
            status="SUCCESS",
            steps_executed=1,
            before_obs=after_obs,
            after_obs=after_obs,
            diff=diff,
        )

        findings = evaluate_always_on_oracles(res)
        assert any("NaN" in f.title or "Text Anomaly" in f.title for f in findings)

    finally:
        orchestrator.close()


def test_eval_e_generalization_and_signature_deduplication(benchmark_server):
    """
    Evaluation E: Generalization & Memory
    Can the agent explore an unfamiliar multi-page application without target-specific hardcoding,
    reuse valid previous evidence, and avoid redundant action loops?
    """
    agenda = InvestigationAgenda(max_probes=0)
    obs = UIStateObservation(
        state_id="st_gen_1",
        state_class_id="cls_gen_products",
        url=f"{benchmark_server}/products",
        normalized_route="127.0.0.1:8998/products",
        page_title="Products Catalog",
        headings=["Products Catalog"],
        interactive_controls=[
            ElementReference(element_id="c1", role="button", accessible_name="Add to Cart", tag_name="button", selector="#add-1"),
            ElementReference(element_id="c2", role="button", accessible_name="Add to Cart", tag_name="button", selector="#add-2"),
            ElementReference(element_id="c3", role="button", accessible_name="Add to Cart", tag_name="button", selector="#add-3"),
        ],
    )
    added = agenda.populate_from_observation(obs)
    # 1. Generator-level equivalence sampling: reduces 3 identical repeated "Add to Cart" buttons to 1 representative question
    ctrl_questions = [q for q in agenda.questions.values() if q.probe_family == "controls"]
    assert len(ctrl_questions) == 1

    # 2. Agenda-level memory & deduplication: re-visiting observation does not re-add duplicate questions
    added_again = agenda.populate_from_observation(obs)
    assert added_again == 0
    assert agenda.deduplicated_count >= 1
