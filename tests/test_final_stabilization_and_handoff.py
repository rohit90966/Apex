"""
Final Stabilization, Sandbox Security, Handoff Contract, and Test-Case Generation Suite.
========================================================================================
Verifies:
1. Positive and negative security tests for SafetyGovernor sandbox authorization.
2. Versioned InvestigationHandoffContract schema, referential integrity, and secret redaction.
3. ContractTestGenerator across workflow, transition, validation, state-dependent, regression,
   and exploratory hypothesis categories, plus deterministic rejection of invalid/unsafe cases.
4. End-to-end acceptance run on the blind benchmark application with handoff export.
"""

import json
import socket
import threading
import time
from pathlib import Path
import pytest
import uvicorn

from app.investigation_agent import (
    INVESTIGATION_ENGINE_FROZEN,
    INVESTIGATION_ENGINE_VERSION,
    CONTRACT_SCHEMA_VERSION,
    InvestigationOrchestrator,
    build_investigation_workflow,
    export_investigation_handoff,
)
from app.investigation_agent.governor import SafetyGovernor
from app.investigation_agent.handoff_contract import (
    InvestigationHandoffContract,
    ContractStateRecord,
    ContractTransitionRecord,
    ContractGroundedControl,
    ContractEvidenceRecord,
    ContractDefectFinding,
    ContractCompletenessSummary,
    ContractPendingCandidate,
)
from app.test_generation import (
    ContractTestGenerator,
    ContractTestValidator,
    GeneratedContractTestCase,
    GroundedTestStep,
    ExpectedResultSpec,
    SafetyExecutionMetadata,
    ExecutionSafetyClassification,
    TestCategoryType,
)
from tests.benchmark_evaluator import BenchmarkEvaluator


# ---------------------------------------------------------------------------
# 1. Sandbox Safety Governor Positive & Negative Security Tests
# ---------------------------------------------------------------------------

def test_sandbox_disabled_by_default_blocks_local_checkout():
    """Sandbox authorization must be disabled by default and block checkout even on localhost."""
    gov = SafetyGovernor()
    assert gov.sandbox_mode is False
    allowed, reason = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#checkout-button",
        current_url="http://127.0.0.1:8992/cart",
        rationale="Proceed to Checkout",
    )
    assert allowed is False
    assert "prohibited keyword 'checkout'" in reason.lower()


def test_sandbox_authorized_local_origin_permits_synthetic_checkout_navigation_and_form():
    """Explicitly enabled sandbox on approved local origin permits synthetic checkout navigation and shipping form."""
    gov = SafetyGovernor(
        sandbox_mode=True,
        sandbox_origins=["http://127.0.0.1:8992"],
    )
    # 1. Click #checkout-button on approved origin
    allowed_click, _ = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#checkout-button",
        current_url="http://127.0.0.1:8992/cart",
        rationale="Proceed to Checkout",
    )
    assert allowed_click is True

    # 2. Navigate relative /checkout on approved origin
    allowed_nav, _ = gov.can_execute(
        action_type="NAVIGATE_LINK",
        target="/checkout",
        current_url="http://127.0.0.1:8992/cart",
        rationale="Navigate to /checkout",
    )
    assert allowed_nav is True

    # 3. Submit shipping form #checkoutForm on approved origin
    allowed_submit, _ = gov.can_execute(
        action_type="SUBMIT",
        target="#checkoutForm",
        current_url="http://127.0.0.1:8992/checkout",
        rationale="Happy path submission of form checkoutForm",
    )
    assert allowed_submit is True


def test_sandbox_blocks_unauthorized_external_or_unlisted_origins():
    """External domains and unlisted local ports must never receive sandbox checkout permissions."""
    gov = SafetyGovernor(
        sandbox_mode=True,
        sandbox_origins=[
            "http://127.0.0.1:8992",
            "https://www.saucedemo.com",  # External host must be rejected by _normalize_approved_sandbox_origins
            "https://stripe.com",
            "*",                          # Wildcard must be rejected
            "http://admin:secret@127.0.0.1:8992",  # Embedded credentials must be rejected
        ],
    )
    assert gov.sandbox_origins == ["http://127.0.0.1:8992"]

    # External site checkout remains blocked
    allowed_ext, reason_ext = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#checkout",
        current_url="https://www.saucedemo.com/cart.html",
        rationale="Checkout",
    )
    assert allowed_ext is False
    assert "prohibited keyword 'checkout'" in reason_ext.lower()

    # Different port on localhost that was not approved fails closed
    allowed_wrong_port, _ = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#checkout-button",
        current_url="http://127.0.0.1:9999/cart",
        rationale="Checkout",
    )
    assert allowed_wrong_port is False

    # Cross-origin destination link from approved origin to external checkout fails closed
    allowed_cross_dest, _ = gov.can_execute(
        action_type="NAVIGATE_LINK",
        target="https://external-payment.example.com/checkout",
        current_url="http://127.0.0.1:8992/cart",
        rationale="External checkout redirect",
    )
    assert allowed_cross_dest is False


def test_sandbox_never_authorizes_real_purchases_payments_or_destructive_actions():
    """Terminal purchase, payment, and destructive keywords remain strictly blocked even on authorized sandbox origin."""
    gov = SafetyGovernor(
        sandbox_mode=True,
        sandbox_origins=["http://127.0.0.1:8992"],
    )
    terminal_targets = [
        ("CLICK_CONTROL", "#finish-order-btn", "Place Order"),
        ("CLICK_CONTROL", "#pay-btn", "Pay Now"),
        ("SUBMIT", "#payment-form", "Complete Purchase"),
        ("CLICK_CONTROL", "#confirm-pay", "Confirm Payment"),
        ("CLICK_CONTROL", "#buy-now", "Buy Now"),
        ("CLICK_CONTROL", "#delete-acc", "Delete Account"),
        ("CLICK_CONTROL", "#transfer", "Transfer Money"),
    ]
    for act_type, target, rationale in terminal_targets:
        allowed, reason = gov.can_execute(
            action_type=act_type,
            target=target,
            current_url="http://127.0.0.1:8992/checkout/review",
            rationale=rationale,
        )
        assert allowed is False, f"Terminal action ({target} / {rationale}) should have been blocked in sandbox mode"
        assert "prohibited keyword" in reason.lower()


# ---------------------------------------------------------------------------
# 2. Handoff Contract Schema, Referential Integrity, and Secret Redaction Tests
# ---------------------------------------------------------------------------

def _create_representative_contract_fixture() -> InvestigationHandoffContract:
    return InvestigationHandoffContract(
        schema_version=CONTRACT_SCHEMA_VERSION,
        investigation_id="inv-fixture-01",
        application_origin="http://127.0.0.1:8992",
        target_url="http://127.0.0.1:8992/login",
        investigated_routes=["/login", "/products", "/cart", "/checkout"],
        states=[
            ContractStateRecord(
                state_id="st_login",
                state_class_id="cls_login",
                url="http://127.0.0.1:8992/login",
                normalized_route="/login",
                page_title="System Access",
                description="Login Entry Portal",
                state_signature="cls_login::st_login",
                distinguishing_observations=["heading:System Access Portal"],
                requires_authentication=False,
                in_page_state={"has_validation_error": False, "menu_open": False},
                headings=["System Access Portal"],
            ),
            ContractStateRecord(
                state_id="st_login_err",
                state_class_id="cls_login",
                url="http://127.0.0.1:8992/login?error=Username+is+required",
                normalized_route="/login",
                page_title="System Access",
                description="Login Validation Error State",
                state_signature="cls_login::st_login_err",
                distinguishing_observations=["validation_error:Username is required"],
                requires_authentication=False,
                in_page_state={
                    "has_validation_error": True,
                    "validation_error_message": "Username is required",
                    "validation_error_locator": "#login-error",
                },
                headings=["System Access Portal"],
            ),
            ContractStateRecord(
                state_id="st_products",
                state_class_id="cls_products",
                url="http://127.0.0.1:8992/products",
                normalized_route="/products",
                page_title="Products",
                description="Products Catalog",
                state_signature="cls_products::st_products",
                distinguishing_observations=["heading:Products Catalog"],
                requires_authentication=True,
                known_preconditions=["Requires authenticated session"],
                in_page_state={"has_validation_error": False, "menu_open": False, "counter_value": 0},
                headings=["Products Catalog"],
            ),
            ContractStateRecord(
                state_id="st_cart_item",
                state_class_id="cls_cart",
                url="http://127.0.0.1:8992/cart",
                normalized_route="/cart",
                page_title="Your Cart",
                description="Shopping Cart with Item",
                state_signature="cls_cart::st_cart_item",
                distinguishing_observations=["heading:Shopping Cart", "counter_value:1"],
                requires_authentication=True,
                known_preconditions=["Requires authenticated session"],
                in_page_state={"has_validation_error": False, "counter_value": 1},
                headings=["Shopping Cart"],
            ),
        ],
        verified_transitions=[
            ContractTransitionRecord(
                transition_id="tr_login_blank",
                source_state_id="st_login",
                destination_state_id="st_login_err",
                action_type="SUBMIT:required_blank_all_loginForm",
                grounded_control=ContractGroundedControl(
                    selector="#loginForm",
                    role="form",
                    accessible_name="Login Form",
                    target_description="required_blank_all_loginForm",
                ),
                required_preconditions=[],
                observed_postconditions=["Validation error displayed: Username is required"],
                actual_effect="VALIDATION_FEEDBACK",
                verification_outcome="VERIFIED",
                repeatability="REPEATABLE",
                evidence_ids=["ev-01"],
                confidence=1.0,
            ),
            ContractTransitionRecord(
                transition_id="tr_auth_success",
                source_state_id="st_login",
                destination_state_id="st_products",
                action_type="AUTHENTICATE",
                grounded_control=ContractGroundedControl(
                    selector="#loginForm",
                    role="form",
                    accessible_name="Sign In",
                    target_description="login_form",
                ),
                required_preconditions=["Requires valid test credentials"],
                observed_postconditions=["Reached state 'st_products' at 'http://127.0.0.1:8992/products'"],
                actual_effect="ROUTE_TRANSITION",
                verification_outcome="VERIFIED",
                repeatability="REPEATABLE",
                evidence_ids=["ev-02"],
                confidence=1.0,
            ),
            ContractTransitionRecord(
                transition_id="tr_add_to_cart",
                source_state_id="st_products",
                destination_state_id="st_cart_item",
                action_type="CLICK_CONTROL:add_to_cart_1",
                grounded_control=ContractGroundedControl(
                    selector="#add-to-cart-1",
                    role="button",
                    accessible_name="Add to Cart",
                    target_description="#add-to-cart-1",
                ),
                required_preconditions=["Requires authenticated session"],
                observed_postconditions=["Reached state 'st_cart_item' at 'http://127.0.0.1:8992/cart'"],
                actual_effect="ROUTE_TRANSITION",
                verification_outcome="VERIFIED",
                repeatability="REPEATABLE",
                evidence_ids=["ev-03"],
                confidence=1.0,
            ),
        ],
        evidence_ledger=[
            ContractEvidenceRecord(
                evidence_id="ev-01",
                timestamp=1700000001.0,
                associated_state_id="st_login",
                before_state_id="st_login",
                after_state_id="st_login_err",
                evidence_type="PROBE_RESULT",
                dom_or_a11y_observation="Blank login submission triggered validation alert 'Username is required'",
                observed_result="VALIDATION_FEEDBACK",
                verification_status="VERIFIED",
                locator="#login-error",
                validation_message="Username is required",
            ),
            ContractEvidenceRecord(
                evidence_id="ev-02",
                timestamp=1700000002.0,
                associated_state_id="st_login",
                before_state_id="st_login",
                after_state_id="st_products",
                evidence_type="AUTH_VERIFICATION",
                dom_or_a11y_observation="Authenticated transition to /products",
                observed_result="ROUTE_TRANSITION",
                verification_status="VERIFIED",
            ),
            ContractEvidenceRecord(
                evidence_id="ev-03",
                timestamp=1700000003.0,
                associated_state_id="st_products",
                before_state_id="st_products",
                after_state_id="st_cart_item",
                evidence_type="PROBE_RESULT",
                dom_or_a11y_observation="Clicked #add-to-cart-1 and transitioned to /cart with 1 item",
                observed_result="ROUTE_TRANSITION",
                verification_status="VERIFIED",
                locator="#add-to-cart-1",
            ),
            ContractEvidenceRecord(
                evidence_id="ev-04",
                timestamp=1700000004.0,
                associated_state_id="st_cart_item",
                before_state_id="st_cart_item",
                after_state_id="st_cart_item",
                evidence_type="PROBE_RESULT",
                dom_or_a11y_observation="Detected anomaly 'NaN' rendered in #tax-display ('Tax: $NaN | Total: $NaN')",
                observed_result="TEXT_ANOMALY_DETECTED",
                verification_status="VERIFIED",
                locator="#calculate-tax-btn",
            ),
        ],
        defect_findings=[
            ContractDefectFinding(
                finding_id="lead_tax_nan",
                title="Text Anomaly: 'NaN'",
                classification="CONFIRMED_DEFECT",
                oracle_type="TECHNICAL",
                severity="HIGH",
                state_class="cls_cart",
                target_control="#calculate-tax-btn",
                reproduction_verified=True,
                reproduction_steps=[{"action_type": "CLICK_CONTROL", "raw_target": "#calculate-tax-btn"}],
                expected_behavior="Clean interaction without technical or specification errors",
                actual_behavior="Detected anomaly 'NaN' rendered in visible text after interaction.",
                evidence_ids=["ev-04"],
                confidence=1.0,
            )
        ],
        completeness=ContractCompletenessSummary(
            discovered_states_count=4,
            verified_transitions_count=3,
            pending_frontier_candidates=[
                ContractPendingCandidate(
                    candidate_id="q_sort_lohi",
                    candidate_type="controls",
                    target="#product-sort",
                    parent_state_id="cls_products",
                    unresolved_reason="BUDGET_CEILING_REACHED",
                )
            ],
            unresolved_reasons_breakdown={"BUDGET_CEILING_REACHED": 1},
            safety_blocked_actions=[],
            authentication_boundaries={"boundary_observed": True, "authentication_verified": True},
            investigation_uncertainty=0.15,
            stopping_reason="Configured probe ceiling reached",
            completion_status="completed",
        ),
    )


def test_handoff_contract_validation_rules():
    """Verifies that the versioned handoff contract enforces referential integrity and rejects fabricated references."""
    assert INVESTIGATION_ENGINE_FROZEN is True
    assert INVESTIGATION_ENGINE_VERSION == "1.0.0-frozen"

    contract = _create_representative_contract_fixture()
    assert contract.schema_version == "1.0.0"

    # 1. Reject invented/dangling evidence ID in transition
    with pytest.raises(ValueError, match="non-existent evidence_id"):
        bad_dict = contract.model_dump()
        bad_dict["verified_transitions"][0]["evidence_ids"] = ["ev-invented-999"]
        InvestigationHandoffContract(**bad_dict)

    # 2. Reject non-existent screenshot file claim
    with pytest.raises(ValueError, match="non-existent screenshot file"):
        bad_dict2 = contract.model_dump()
        bad_dict2["evidence_ledger"][0]["screenshot_artifact_path"] = "non_existent_dir/fake_shot.png"
        InvestigationHandoffContract(**bad_dict2)

    # 3. Reject CONFIRMED_DEFECT without reproduction_verified=True
    with pytest.raises(ValueError, match="reproduction_verified=True"):
        bad_dict3 = contract.model_dump()
        bad_dict3["defect_findings"][0]["reproduction_verified"] = False
        InvestigationHandoffContract(**bad_dict3)


# ---------------------------------------------------------------------------
# 3. Contract-Driven Test-Case Generation & Deterministic Validation Tests
# ---------------------------------------------------------------------------

def test_contract_test_generator_produces_all_mandated_categories_and_coverage():
    """Verifies workflow, transition, validation, state-dependent, regression, and exploratory test generation."""
    contract = _create_representative_contract_fixture()
    generator = ContractTestGenerator()
    suite = generator.generate_suite(contract)

    assert suite.source_investigation_id == "inv-fixture-01"
    assert suite.source_contract_version == "1.0.0"
    assert len(suite.test_cases) >= 6

    categories = {tc.category for tc in suite.test_cases}
    assert TestCategoryType.WORKFLOW in categories
    assert TestCategoryType.TRANSITION in categories
    assert TestCategoryType.VALIDATION in categories
    assert TestCategoryType.STATE_DEPENDENT in categories
    assert TestCategoryType.REGRESSION in categories
    assert TestCategoryType.EXPLORATORY_HYPOTHESIS in categories

    # Verify every generated test has valid evidence traceability
    known_ev_ids = {e.evidence_id for e in contract.evidence_ledger}
    for tc in suite.test_cases:
        assert len(tc.evidence_ids) > 0
        for eid in tc.evidence_ids:
            assert eid in known_ev_ids
        if tc.category == TestCategoryType.EXPLORATORY_HYPOTHESIS:
            assert any(er.is_hypothesis_requiring_confirmation for er in tc.expected_results)
            assert len(tc.unresolved_assumptions) > 0

    # Verify coverage map
    assert suite.coverage_map.covered_states_count == 4
    assert suite.coverage_map.state_coverage_ratio == 1.0
    assert suite.coverage_map.covered_transitions_count == 3
    assert suite.coverage_map.transition_coverage_ratio == 1.0
    assert "lead_tax_nan" in suite.coverage_map.findings_covered


def test_contract_test_validator_rejects_unsafe_and_unsupported_candidates():
    """Verifies that the deterministic validator rejects unsafe actions, invalid evidence refs, and unsupported results."""
    contract = _create_representative_contract_fixture()

    # 1. Candidate with terminal forbidden keyword ("Place Order") must be rejected
    unsafe_tc = GeneratedContractTestCase(
        test_id="TC-UNSAFE-01",
        title="Unsafe Order Placement",
        category=TestCategoryType.TRANSITION,
        objective="Attempt to place live order",
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector="#finish-order-btn",
                description="Click Place Order button",
                source_state_id="st_cart_item",
                evidence_refs=["ev-03"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(
                description="Order placed",
                verification_type="STATE_REACHED",
                is_verified_by_evidence=True,
                evidence_refs=["ev-03"],
            )
        ],
        source_state_ids=["st_cart_item"],
        evidence_ids=["ev-03"],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.RESTRICTED_DO_NOT_AUTO_RUN,
            safe_for_unattended_execution=False,
        ),
    )
    ok_unsafe, reason_unsafe = ContractTestValidator.validate_candidate(unsafe_tc, contract)
    assert ok_unsafe is False
    assert "UNSAFE_ACTION_REJECTED" in reason_unsafe

    # 2. Candidate claiming verified expected result with no evidence references must be rejected
    unsupported_tc = GeneratedContractTestCase(
        test_id="TC-UNSUP-01",
        title="Unsupported Expected Result",
        category=TestCategoryType.TRANSITION,
        objective="Test with fabricated expectation",
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector="#add-to-cart-1",
                description="Click add to cart",
                source_state_id="st_products",
            )
        ],
        expected_results=[
            ExpectedResultSpec(
                description="Fabricated expectation with no evidence",
                verification_type="STATE_REACHED",
                is_verified_by_evidence=True,
                evidence_refs=[],
            )
        ],
        source_state_ids=["st_products"],
        evidence_ids=[],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SAFE_REVERSIBLE,
        ),
    )
    ok_unsup, reason_unsup = ContractTestValidator.validate_candidate(unsupported_tc, contract)
    assert ok_unsup is False
    assert "UNSUPPORTED_EXPECTED_RESULT" in reason_unsup


# ---------------------------------------------------------------------------
# 4. End-to-End Blind Acceptance Run & Handoff Export on Controlled Benchmark
# ---------------------------------------------------------------------------

class AcceptanceBenchmarkServer:
    def __init__(self, port: int = 8995):
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        from demo_app.benchmark_app.main import app as target_app

        config = uvicorn.Config(target_app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

        t0 = time.time()
        while time.time() - t0 < 10.0:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.1)
        raise RuntimeError(f"Acceptance server failed to start on port {self.port}")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3.0)


def test_end_to_end_acceptance_investigation_handoff_and_test_generation():
    """
    Runs one complete blind end-to-end investigation against the controlled benchmark application
    with explicit local sandbox authorization, validates the exported InvestigationHandoffContract,
    and generates the evidence-grounded test suite from the contract.
    """
    server = AcceptanceBenchmarkServer(port=8995)
    server.start()
    origin = f"http://127.0.0.1:{server.port}"

    orchestrator = InvestigationOrchestrator(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=[origin],
    )
    graph = build_investigation_workflow(orchestrator)

    try:
        initial_state = {
            "target_url": f"{origin}/login",
            "auth_credentials": {"username": "benchmark_user", "password": "secret_benchmark"},
            "max_actions": 10,
            "max_probes": 10,
            "max_time_seconds": 150.0,
            "exploration_policy": "workflow_depth",
            "sandbox_mode": True,
            "sandbox_origins": [origin],
        }
        final_state = graph.invoke(initial_state)
        report = final_state.get("final_report")
        contract = export_investigation_handoff(final_state, report=report)

        # Evaluate against independent ground truth
        eval_result = BenchmarkEvaluator.evaluate(
            report=report,
            decision_snapshots=final_state.get("decision_snapshots"),
            final_state=final_state,
        )

        # 1. Verify authentication, validation, catalog, cart, and synthetic checkout discovery
        assert "ST_LOGIN" in eval_result.matched_ground_truth_states
        assert "ST_LOGIN_ERROR" in eval_result.matched_ground_truth_states
        assert "ST_PRODUCTS" in eval_result.matched_ground_truth_states
        assert "ST_CART_WITH_ITEM" in eval_result.matched_ground_truth_states
        assert "ST_CHECKOUT_STEP1" in eval_result.matched_ground_truth_states
        assert eval_result.transition_accuracy == 1.0
        assert eval_result.benign_warning_correctly_ignored is True

        # 2. Verify contract integrity and secret redaction
        contract_json = contract.model_dump_json(indent=2)
        assert "secret_benchmark" not in contract_json
        assert "session_benchmark_user" not in contract_json
        assert contract.schema_version == "1.0.0"
        assert len(contract.states) >= 6
        assert len(contract.verified_transitions) >= 5
        assert len(contract.evidence_ledger) >= 6

        # 3. Generate test cases from the handoff contract
        generator = ContractTestGenerator()
        test_suite = generator.generate_suite(contract)

        assert len(test_suite.test_cases) >= 5
        categories = {tc.category for tc in test_suite.test_cases}
        assert TestCategoryType.WORKFLOW in categories
        assert TestCategoryType.TRANSITION in categories
        assert TestCategoryType.VALIDATION in categories

        # Save artifacts for inspection
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        (reports_dir / "final_handoff_contract_v1.json").write_text(contract_json, encoding="utf-8")
        (reports_dir / "final_generated_test_suite_v1.json").write_text(
            test_suite.model_dump_json(indent=2), encoding="utf-8"
        )
        (reports_dir / "final_acceptance_evaluation.json").write_text(
            eval_result.model_dump_json(indent=2), encoding="utf-8"
        )

    finally:
        orchestrator.close()
        server.stop()
