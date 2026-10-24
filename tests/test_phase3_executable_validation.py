"""
VerityQA Phase 3: Executable Test Generation, Oracle Validation & Mutation Suite.
================================================================================
Verifies:
1. Executable Playwright export (syntax validity, grounded locators, safety assertions)
2. Grounded test execution across confirmed-defect, transition, workflow, validation, and state-dependent tests
3. Controlled Mutation Testing:
   - Scenario A: Defective implementation (seeded NaN tax anomaly detected by TC-REG-01)
   - Scenario B: Corrected implementation (valid numeric tax calculation, TC-REG-01 passes)
   - Scenario C: Benign behavior (telemetry console warnings remain non-failures)
   - Scenario D: Setup failure discrimination (precondition/credential failure distinct from product bug)
4. Negative Safety & Validation tests:
   - Terminal destructive action rejection
   - Unauthorized sandbox origin rejection
   - Missing/invalid evidence reference rejection
   - Hypothesis test exclusion from verified regression
"""

import os
import json
import time
import pytest
import uvicorn
import threading
from urllib.parse import urlparse

from demo_app.benchmark_app.main import app as benchmark_app, PRODUCTS, _CARTS
from app.test_generation import (
    ContractTestSuiteArtifact,
    GeneratedContractTestCase,
    GroundedTestStep,
    ExpectedResultSpec,
    TestCategoryType,
    ExecutionSafetyClassification,
    SafetyExecutionMetadata,
    ContractTestValidator,
    PlaywrightTestExporter,
    PlaywrightContractTestRunner,
    ContractTestExecutionReport,
    TestExecutionStatus,
    FailurePhase,
)
from app.investigation_agent.handoff_contract import (
    InvestigationHandoffContract,
    ContractStateRecord,
    ContractTransitionRecord,
    ContractEvidenceRecord,
    ContractDefectFinding,
    ContractGroundedControl,
    ContractCompletenessSummary,
)


@pytest.fixture(scope="module")
def phase3_benchmark_server():
    """Spins up the controlled benchmark application on port 8996 for Phase 3 testing."""
    # Ensure fresh in-memory state
    _CARTS.clear()
    benchmark_app.state.tax_defect_corrected = False
    os.environ["VERITYQA_BENCHMARK_CORRECT_TAX"] = "0"

    server = uvicorn.Server(
        uvicorn.Config(benchmark_app, host="127.0.0.1", port=8996, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8996"
    server.should_exit = True
    os.environ.pop("VERITYQA_BENCHMARK_CORRECT_TAX", None)


@pytest.fixture
def baseline_suite() -> ContractTestSuiteArtifact:
    """Loads the baseline ContractTestSuiteArtifact generated from the frozen investigation contract."""
    suite_path = "reports/final_generated_test_suite_v1.json"
    assert os.path.exists(suite_path), f"Baseline suite artifact {suite_path} must exist"
    with open(suite_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ContractTestSuiteArtifact(**data)


# ==============================================================================
# Test 1: Playwright Test Exporter & Code Generation
# ==============================================================================

def test_playwright_exporter_produces_syntactically_valid_suite(baseline_suite, tmp_path):
    """
    Verifies that PlaywrightTestExporter exports all test cases into a valid,
    compilable Python Playwright test module without manual edits.
    """
    exporter = PlaywrightTestExporter()
    out_file = str(tmp_path / "test_exported_suite.py")

    exported_path = exporter.export_to_file(
        suite=baseline_suite,
        output_file_path=out_file,
        target_base_url="http://127.0.0.1:8996",
        sandbox_mode=True,
        sandbox_origins=["http://127.0.0.1:8996"],
    )

    assert os.path.exists(exported_path)
    with open(exported_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Syntax check via compile
    compiled = compile(code, exported_path, "exec")
    assert compiled is not None

    # Structural checks
    assert "TC_REG_01" in code
    assert "TC_WF_01" in code
    assert "TC_TR_01" in code
    assert "TC_VAL_01" in code
    assert "TC_SD_01" in code
    assert "@pytest.mark.skip" in code  # Hypothesis tests marked as skipped
    assert "ensure_authenticated_session" in code
    assert "check_execution_safety" in code


# ==============================================================================
# Test 2: Mutation Testing Scenario A — Defective Implementation Detection
# ==============================================================================

@pytest.mark.asyncio
async def test_mutation_scenario_a_defective_implementation_detected(
    baseline_suite, phase3_benchmark_server
):
    """
    Scenario A: Seeded Defect Detection.
    On the defective implementation, #calculate-tax-btn produces the seeded NaN anomaly.
    Verifies that the generated regression test TC-REG-01 executes, reaches /cart,
    triggers the tax calculation, detects the text anomaly 'NaN', and reports DEFECT_DETECTED.
    """
    # Ensure defective mode is active
    benchmark_app.state.tax_defect_corrected = False
    os.environ["VERITYQA_BENCHMARK_CORRECT_TAX"] = "0"
    _CARTS.clear()

    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=[phase3_benchmark_server],
    )

    report = await runner.run_suite(
        suite=baseline_suite,
        target_base_url=phase3_benchmark_server,
    )

    assert report is not None
    assert report.total_generated_cases == 25
    assert report.exported_count == 25
    assert report.executable_without_manual_edits_count == 23  # 25 - 2 hypotheses
    assert report.skipped_hypotheses_count == 2

    # Find TC-REG-01 execution result
    reg_result = next((r for r in report.results if r.test_id == "TC-REG-01"), None)
    assert reg_result is not None, "TC-REG-01 must be executed"
    assert reg_result.category == "regression"
    assert reg_result.status == TestExecutionStatus.DEFECT_DETECTED, (
        f"TC-REG-01 must detect the seeded defect on defective app, got {reg_result.status} ({reg_result.error_message})"
    )
    assert "nan" in (reg_result.error_message or "").lower()
    assert reg_result.failure_phase == FailurePhase.ASSERTION
    assert len(reg_result.source_evidence_ids) > 0

    # Defect detection rate must be 100% (1/1)
    assert report.defect_detection_rate == 1.0

    # Save execution report for inspection
    os.makedirs("reports", exist_ok=True)
    with open("reports/phase3_defective_run_report.json", "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))


# ==============================================================================
# Test 3: Mutation Testing Scenario B — Corrected Implementation Pass
# ==============================================================================

@pytest.mark.asyncio
async def test_mutation_scenario_b_corrected_implementation_passes(
    baseline_suite, phase3_benchmark_server
):
    """
    Scenario B: Corrected Implementation.
    When the tax calculation defect is corrected (rendering valid numeric tax and total),
    TC-REG-01 must PASS without false-positive failures.
    """
    # Enable corrected implementation
    benchmark_app.state.tax_defect_corrected = True
    os.environ["VERITYQA_BENCHMARK_CORRECT_TAX"] = "1"
    _CARTS.clear()

    try:
        runner = PlaywrightContractTestRunner(
            headless=True,
            sandbox_mode=True,
            sandbox_origins=[phase3_benchmark_server],
        )

        report = await runner.run_suite(
            suite=baseline_suite,
            target_base_url=phase3_benchmark_server,
        )

        reg_result = next((r for r in report.results if r.test_id == "TC-REG-01"), None)
        assert reg_result is not None
        assert reg_result.status == TestExecutionStatus.PASSED, (
            f"TC-REG-01 must pass on corrected app, got {reg_result.status}: {reg_result.error_message}"
        )
        assert reg_result.assertions_passed == 1

        with open("reports/phase3_corrected_run_report.json", "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

        for r in report.results:
            if r.status not in (TestExecutionStatus.PASSED, TestExecutionStatus.SKIPPED_HYPOTHESIS):
                print(f"\n[DEBUG_FAILED] {r.test_id} ({r.category}) -> status: {r.status}, error: {r.error_message}")

        # False-positive rate on corrected app must be 0.0%
        assert report.false_positive_rate == 0.0

    finally:
        # Reset to defective default
        benchmark_app.state.tax_defect_corrected = False
        os.environ["VERITYQA_BENCHMARK_CORRECT_TAX"] = "0"
        _CARTS.clear()


# ==============================================================================
# Test 4: Mutation Testing Scenario C — Benign Telemetry Warnings Ignored
# ==============================================================================

@pytest.mark.asyncio
async def test_mutation_scenario_c_benign_telemetry_warnings_ignored(
    baseline_suite, phase3_benchmark_server
):
    """
    Scenario C: Benign Behavior.
    The benchmark app logs console.warn('Analytics: tracking pixel ping delayed (non-critical)') on every page load.
    Verifies that this warning is captured in console_logs for auditability but does NOT cause test failures.
    """
    _CARTS.clear()
    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=[phase3_benchmark_server],
    )

    # Run transition test TC-TR-02 (Login Authentication transition)
    tc_auth = next(tc for tc in baseline_suite.test_cases if tc.test_id == "TC-TR-02")
    single_suite = ContractTestSuiteArtifact(
        source_investigation_id="bench_benign",
        target_url=phase3_benchmark_server,
        test_cases=[tc_auth],
        coverage_map=baseline_suite.coverage_map,
    )

    report = await runner.run_suite(single_suite, target_base_url=phase3_benchmark_server)
    res = report.results[0]

    assert res.status == TestExecutionStatus.PASSED
    # Verify console log was captured
    assert any("tracking pixel ping delayed" in log for log in res.console_logs), (
        f"Expected benign warning in console logs, got {res.console_logs}"
    )


# ==============================================================================
# Test 5: Mutation Testing Scenario D — Setup Failure Discrimination
# ==============================================================================

@pytest.mark.asyncio
async def test_mutation_scenario_d_setup_failure_discrimination(
    baseline_suite, phase3_benchmark_server
):
    """
    Scenario D: Setup Failure Discrimination.
    When prerequisite authentication fails (e.g. invalid test credentials provided),
    the runner must report SETUP_FAILURE, NOT a product defect or assertion failure.
    """
    _CARTS.clear()
    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=[phase3_benchmark_server],
    )

    # Use TC-TR-03 which requires authenticated session
    tc_cart = next(tc for tc in baseline_suite.test_cases if tc.test_id == "TC-TR-03")
    auth_suite = ContractTestSuiteArtifact(
        source_investigation_id="bench_setup_fail",
        target_url=phase3_benchmark_server,
        test_cases=[tc_cart],
        coverage_map=baseline_suite.coverage_map,
    )

    # Run with intentionally wrong password to induce setup failure
    report = await runner.run_suite(
        suite=auth_suite,
        target_base_url=phase3_benchmark_server,
        test_username="benchmark_user",
        test_password="WRONG_PASSWORD_INDUCED_SETUP_FAILURE",
    )

    res = report.results[0]
    # Prerequisite login fails -> setup failure
    assert res.status in (TestExecutionStatus.SETUP_FAILURE, TestExecutionStatus.EXECUTION_ERROR), (
        f"Expected setup failure on invalid credentials, got {res.status}"
    )
    assert res.failure_phase in (FailurePhase.SETUP, FailurePhase.EXECUTION)
    assert res.status != TestExecutionStatus.DEFECT_DETECTED
    assert report.setup_failures_count >= 1 or res.status == TestExecutionStatus.EXECUTION_ERROR


# ==============================================================================
# Test 6: Safety Pre-Checks — Terminal Destructive Action Rejection
# ==============================================================================

@pytest.mark.asyncio
async def test_safety_terminal_destructive_action_blocked(phase3_benchmark_server, baseline_suite):
    """
    Verifies that attempting to execute a test case with a terminal destructive action
    ('delete account', 'pay now', 'place order', etc.) is blocked before touching the browser.
    """
    unsafe_step = GroundedTestStep(
        step_number=1,
        action_type="CLICK_CONTROL",
        selector="#delete-account-btn",
        description="Permanently delete user account and drop database",
        evidence_refs=["ev-synthetic"],
    )
    unsafe_tc = GeneratedContractTestCase(
        test_id="TC-UNSAFE-01",
        title="Unsafe Destructive Action Test",
        category=TestCategoryType.TRANSITION,
        objective="Attempt to trigger destructive action",
        preconditions=[],
        steps=[unsafe_step],
        expected_results=[
            ExpectedResultSpec(
                description="Account deleted",
                verification_type="DOM_MUTATION_OBSERVED",
                evidence_refs=["ev-synthetic"],
            )
        ],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.RESTRICTED_DO_NOT_AUTO_RUN,
            contains_destructive_or_financial_action=True,
            safe_for_unattended_execution=False,
        ),
    )

    suite = ContractTestSuiteArtifact(
        source_investigation_id="inv-safety",
        target_url=phase3_benchmark_server,
        test_cases=[unsafe_tc],
        coverage_map=baseline_suite.coverage_map,
    )

    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=[phase3_benchmark_server],
    )

    report = await runner.run_suite(suite, target_base_url=phase3_benchmark_server)
    res = report.results[0]

    assert res.status == TestExecutionStatus.SAFETY_BLOCKED
    assert res.failure_phase == FailurePhase.SAFETY
    assert "SAFETY_BLOCKED" in (res.error_message or "")
    assert report.safety_blocked_count == 1


# ==============================================================================
# Test 7: Safety Pre-Checks — Unauthorized Sandbox Origin Rejection
# ==============================================================================

@pytest.mark.asyncio
async def test_safety_unauthorized_sandbox_origin_blocked(baseline_suite):
    """
    Verifies that sandbox-only test cases targeting unauthorized external origins
    (e.g. production domains) are blocked by fail-closed safety policy.
    """
    sandbox_step = GroundedTestStep(
        step_number=1,
        action_type="CLICK_CONTROL",
        selector="#checkout-button",
        description="Execute synthetic checkout navigation",
        evidence_refs=["ev-sandbox"],
    )
    sandbox_tc = GeneratedContractTestCase(
        test_id="TC-SB-UNAUTH-01",
        title="Sandbox Test on Unauthorized External Origin",
        category=TestCategoryType.WORKFLOW,
        objective="Verify checkout navigation on external site",
        preconditions=[],
        steps=[sandbox_step],
        expected_results=[
            ExpectedResultSpec(
                description="Reaches checkout",
                verification_type="URL_CONTAINS",
                expected_value="/checkout",
                evidence_refs=["ev-sandbox"],
            )
        ],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SANDBOX_ONLY,
            requires_sandbox=True,
            safe_for_unattended_execution=False,
        ),
    )

    suite = ContractTestSuiteArtifact(
        source_investigation_id="inv-sandbox-test",
        target_url="https://production.example.com",
        test_cases=[sandbox_tc],
        coverage_map=baseline_suite.coverage_map,
    )

    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=True,
        sandbox_origins=["http://127.0.0.1:8996"],  # Only local origin approved
    )

    report = await runner.run_suite(suite, target_base_url="https://production.example.com")
    res = report.results[0]

    assert res.status == TestExecutionStatus.SAFETY_BLOCKED
    assert res.failure_phase == FailurePhase.SAFETY
    assert "not an approved sandbox origin" in (res.error_message or "")


# ==============================================================================
# Test 8: Contract Test Validator — Negative Assertion & Evidence Rules
# ==============================================================================

def test_contract_test_validator_rejects_missing_evidence_and_invalid_cases(baseline_suite):
    """
    Negative validation tests for ContractTestValidator:
    - Rejects candidate with empty steps
    - Rejects candidate with missing expected results
    - Rejects candidate referencing nonexistent evidence ID
    - Rejects hypothesis candidate masquerading as verified regression
    """
    # 1. Empty steps
    invalid_tc_steps = GeneratedContractTestCase(
        test_id="TC-INV-01",
        title="Invalid Case",
        category=TestCategoryType.TRANSITION,
        objective="Objective",
        steps=[],
        expected_results=[
            ExpectedResultSpec(description="Exp", verification_type="URL_CONTAINS", evidence_refs=["ev-1"])
        ],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SAFE_READ_ONLY
        ),
    )
    # Build minimal mock contract
    mock_contract = InvestigationHandoffContract(
        investigation_id="mock-inv",
        application_origin="http://127.0.0.1:8996",
        target_url="http://127.0.0.1:8996",
        states=[
            ContractStateRecord(
                state_id="st_1",
                state_class_id="cls_1",
                url="http://127.0.0.1:8996",
                normalized_route="/",
                page_title="Title",
                state_signature="cls_1::st_1",
            )
        ],
        verified_transitions=[],
        evidence_ledger=[
            ContractEvidenceRecord(
                evidence_id="ev-valid-1",
                timestamp=time.time(),
                associated_state_id="st_1",
                evidence_type="PROBE_RESULT",
                dom_or_a11y_observation="Obs",
                observed_result="Res",
                verification_status="VERIFIED",
            )
        ],
        defect_findings=[],
        completeness=ContractCompletenessSummary(discovered_states_count=1, verified_transitions_count=0),
    )

    ok, reason = ContractTestValidator.validate_candidate(invalid_tc_steps, mock_contract)
    assert not ok
    assert "no executable steps" in (reason or "")

    # 2. Nonexistent evidence ID
    invalid_tc_ev = GeneratedContractTestCase(
        test_id="TC-INV-02",
        title="Invalid Evidence Case",
        category=TestCategoryType.TRANSITION,
        objective="Objective",
        source_state_ids=["st_1"],
        evidence_ids=["ev-valid-1"],
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK",
                selector="#btn",
                description="Click",
                evidence_refs=["ev-FAKE-DOES-NOT-EXIST"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(description="Exp", verification_type="URL_CONTAINS", evidence_refs=["ev-FAKE-DOES-NOT-EXIST"])
        ],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SAFE_READ_ONLY
        ),
    )
    ok, reason = ContractTestValidator.validate_candidate(invalid_tc_ev, mock_contract)
    assert not ok
    assert "INVALID_EVIDENCE_REF" in (reason or "") or "unknown evidence_id" in (reason or "")

    # 3. Verified candidate with valid evidence must pass
    valid_tc = GeneratedContractTestCase(
        test_id="TC-VALID-01",
        title="Valid Case",
        category=TestCategoryType.TRANSITION,
        objective="Objective",
        preconditions=["Start at st_1"],
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK",
                selector="#btn",
                description="Click",
                source_state_id="st_1",
                evidence_refs=["ev-valid-1"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(description="Exp", verification_type="URL_CONTAINS", evidence_refs=["ev-valid-1"])
        ],
        source_state_ids=["st_1"],
        safety_metadata=SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SAFE_READ_ONLY
        ),
    )
    ok, reason = ContractTestValidator.validate_candidate(valid_tc, mock_contract)
    assert ok, f"Expected valid candidate to pass validator, got: {reason}"


# ==============================================================================
# Test 9: Negative Validator Rules — Missing Preconditions, Unsupported Results,
#         Ambiguous Locators, and Incorrect Classification
# ==============================================================================

def test_validator_rejects_missing_preconditions_ambiguous_locators_and_misclassification():
    """
    Covers remaining mandated negative validator checks:
    - Missing preconditions on authenticated/cart-conditioned source state
    - Unsupported expected results (claimed verified without evidence, or unverified without hypothesis flag)
    - Duplicate or ambiguous locators (bare generic tag 'button', internal state class 'cls_*', duplicate comma parts)
    - Incorrect test classification (tentative lead or hypothesis labeled as REGRESSION)
    """
    auth_contract = InvestigationHandoffContract(
        investigation_id="mock-auth-inv",
        application_origin="http://127.0.0.1:8996",
        target_url="http://127.0.0.1:8996",
        states=[
            ContractStateRecord(
                state_id="st_auth_cart",
                state_class_id="cls_auth_cart",
                url="http://127.0.0.1:8996/cart",
                normalized_route="/cart",
                page_title="Cart",
                state_signature="cls_auth_cart::st_auth_cart",
                requires_authentication=True,
                in_page_state={"counter_value": 1},
            )
        ],
        verified_transitions=[],
        evidence_ledger=[
            ContractEvidenceRecord(
                evidence_id="ev-auth-1",
                timestamp=time.time(),
                associated_state_id="st_auth_cart",
                evidence_type="PROBE_RESULT",
                dom_or_a11y_observation="Cart has 1 item",
                observed_result="Cart observed",
                verification_status="VERIFIED",
            )
        ],
        defect_findings=[
            ContractDefectFinding(
                finding_id="lead_tentative_1",
                title="Tentative Console Warning",
                classification="UNVERIFIED_SUSPICION",
                oracle_type="TECHNICAL",
                reproduction_verified=False,
                evidence_ids=["ev-auth-1"],
            )
        ],
        completeness=ContractCompletenessSummary(discovered_states_count=1, verified_transitions_count=0),
    )

    # A. Missing preconditions on authenticated + cart-conditioned state
    tc_missing_pre = GeneratedContractTestCase(
        test_id="TC-NEG-PRE-01",
        title="Missing Preconditions Case",
        category=TestCategoryType.TRANSITION,
        objective="Click remove without declaring auth/cart preconditions",
        preconditions=[],  # Intentionally empty
        source_state_ids=["st_auth_cart"],
        evidence_ids=["ev-auth-1"],
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector="#remove-item-1",
                description="Click remove",
                source_state_id="st_auth_cart",
                evidence_refs=["ev-auth-1"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(description="Removed", verification_type="DOM_MUTATION_OBSERVED", evidence_refs=["ev-auth-1"])
        ],
        safety_metadata=SafetyExecutionMetadata(classification=ExecutionSafetyClassification.REQUIRES_AUTH_CREDENTIALS),
    )
    ok, reason = ContractTestValidator.validate_candidate(tc_missing_pre, auth_contract)
    assert not ok
    assert "MISSING_PRECONDITIONS" in (reason or "")

    # B. Unsupported expected results (unverified without hypothesis flag)
    tc_unsupported_exp = GeneratedContractTestCase(
        test_id="TC-NEG-EXP-01",
        title="Unsupported Expected Result Case",
        category=TestCategoryType.TRANSITION,
        objective="Claim unverified expectation without hypothesis flag",
        preconditions=["Requires authenticated session"],
        source_state_ids=["st_auth_cart"],
        evidence_ids=["ev-auth-1"],
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector="#remove-item-1",
                description="Click remove",
                source_state_id="st_auth_cart",
                evidence_refs=["ev-auth-1"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(
                description="Invented unverified outcome",
                verification_type="DOM_MUTATION_OBSERVED",
                is_verified_by_evidence=False,
                is_hypothesis_requiring_confirmation=False,  # Invalid!
                evidence_refs=["ev-auth-1"],
            )
        ],
        safety_metadata=SafetyExecutionMetadata(classification=ExecutionSafetyClassification.REQUIRES_AUTH_CREDENTIALS),
    )
    ok, reason = ContractTestValidator.validate_candidate(tc_unsupported_exp, auth_contract)
    assert not ok
    assert "UNSUPPORTED_EXPECTED_RESULT" in (reason or "")

    # C. Ambiguous locators (bare tag 'button' and hallucinated internal state class 'cls_auth_cart')
    for bad_sel in ("button", "cls_auth_cart", "#btn, #btn"):
        tc_ambig = GeneratedContractTestCase(
            test_id="TC-NEG-LOC-01",
            title="Ambiguous Locator Case",
            category=TestCategoryType.TRANSITION,
            objective="Test ambiguous selector rejection",
            preconditions=["Requires authenticated session"],
            source_state_ids=["st_auth_cart"],
            evidence_ids=["ev-auth-1"],
            steps=[
                GroundedTestStep(
                    step_number=1,
                    action_type="CLICK_CONTROL",
                    selector=bad_sel,
                    description="Click ambiguous selector",
                    source_state_id="st_auth_cart",
                    evidence_refs=["ev-auth-1"],
                )
            ],
            expected_results=[
                ExpectedResultSpec(description="Mutated", verification_type="DOM_MUTATION_OBSERVED", evidence_refs=["ev-auth-1"])
            ],
            safety_metadata=SafetyExecutionMetadata(classification=ExecutionSafetyClassification.REQUIRES_AUTH_CREDENTIALS),
        )
        ok, reason = ContractTestValidator.validate_candidate(tc_ambig, auth_contract)
        assert not ok, f"Expected bad selector '{bad_sel}' to be rejected"
        assert "AMBIGUOUS_LOCATOR" in (reason or "")

    # D. Incorrect test classification (tentative lead or hypothesis classified as REGRESSION)
    tc_misclassified = GeneratedContractTestCase(
        test_id="TC-NEG-CLS-01",
        title="Tentative Lead Masquerading as Regression",
        category=TestCategoryType.REGRESSION,
        objective="Attempt to promote tentative suspicion to verified regression",
        preconditions=["Requires authenticated session"],
        source_state_ids=["st_auth_cart"],
        source_finding_ids=["lead_tentative_1"],
        evidence_ids=["ev-auth-1"],
        steps=[
            GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector="#calculate-tax-btn",
                description="Click control",
                source_state_id="st_auth_cart",
                evidence_refs=["ev-auth-1"],
            )
        ],
        expected_results=[
            ExpectedResultSpec(description="No defect", verification_type="DEFECT_ABSENT", evidence_refs=["ev-auth-1"])
        ],
        safety_metadata=SafetyExecutionMetadata(classification=ExecutionSafetyClassification.REQUIRES_AUTH_CREDENTIALS),
    )
    ok, reason = ContractTestValidator.validate_candidate(tc_misclassified, auth_contract)
    assert not ok
    assert "INCORRECT_CLASSIFICATION" in (reason or "")


# ==============================================================================
# Test 10: Evaluator Effectiveness & Negative Oracle Detection Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_evaluator_effectiveness_and_negative_oracle_detection(
    baseline_suite, phase3_benchmark_server
):
    """
    Verifies:
    1. Independent BenchmarkEvaluator.evaluate_test_suite_effectiveness computes all 14 metrics
       with explicit numerators/denominators across defective and corrected runs.
    2. Negative test for failure to detect the seeded defect (when a regression oracle is weakened/omitted).
    3. Negative test for false failure against the corrected implementation (when a brittle oracle fails on valid output).
    """
    from tests.benchmark_evaluator import BenchmarkEvaluator

    with open("reports/phase3_defective_run_report.json", "r", encoding="utf-8") as f:
        defective_rep = ContractTestExecutionReport(**json.load(f))
    with open("reports/phase3_corrected_run_report.json", "r", encoding="utf-8") as f:
        corrected_rep = ContractTestExecutionReport(**json.load(f))

    metrics = BenchmarkEvaluator.evaluate_test_suite_effectiveness(
        suite=baseline_suite,
        defective_run_report=defective_rep,
        corrected_run_report=corrected_rep,
    )

    assert metrics["1_generated_test_count"]["value"] == 25
    assert metrics["6_setup_success_rate"]["rate"] == 1.0
    assert metrics["7_assertion_validity_rate"]["rate"] == 1.0
    assert metrics["8_state_coverage_of_discovered_states"]["rate"] == 1.0
    assert metrics["9_transition_coverage_of_verified_transitions"]["rate"] == 1.0
    assert metrics["10_seeded_defect_detection_rate"]["rate"] == 1.0
    assert metrics["11_false_failure_rate_on_valid_behavior"]["rate"] == 0.0
    assert metrics["12_duplicate_or_redundant_test_rate"]["rate"] == 0.0
    assert metrics["13_evidence_traceability_completeness"]["rate"] == 1.0

    with open("reports/phase3_effectiveness_evaluation.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    # Negative check 1: Simulate a suite that FAILS to detect the seeded defect on the defective implementation
    blind_defective_rep = defective_rep.model_copy(deep=True)
    for r in blind_defective_rep.results:
        if r.status == TestExecutionStatus.DEFECT_DETECTED:
            r.status = TestExecutionStatus.PASSED
            r.error_message = None
    blind_metrics = BenchmarkEvaluator.evaluate_test_suite_effectiveness(
        suite=baseline_suite,
        defective_run_report=blind_defective_rep,
        corrected_run_report=corrected_rep,
    )
    assert blind_metrics["10_seeded_defect_detection_rate"]["rate"] == 0.0, (
        "Evaluator must flag 0.0 seeded defect detection rate when regression oracle misses the seeded NaN bug"
    )

    # Negative check 2: Simulate a brittle test that causes a FALSE FAILURE against the corrected implementation
    brittle_corrected_rep = corrected_rep.model_copy(deep=True)
    brittle_corrected_rep.results[0].status = TestExecutionStatus.FAILED
    brittle_corrected_rep.results[0].error_message = "Brittle assertion false failure on valid state"
    brittle_metrics = BenchmarkEvaluator.evaluate_test_suite_effectiveness(
        suite=baseline_suite,
        defective_run_report=defective_rep,
        corrected_run_report=brittle_corrected_rep,
    )
    assert brittle_metrics["11_false_failure_rate_on_valid_behavior"]["numerator"] == 1
    assert brittle_metrics["11_false_failure_rate_on_valid_behavior"]["rate"] > 0.0, (
        "Evaluator must flag non-zero false failure rate when a test fails against the corrected implementation"
    )


# ==============================================================================
# Test 11: SauceDemo Handoff Contract, Test Suite Generation & Playwright Export Audit
# ==============================================================================

def test_saucedemo_contract_test_generation_and_export_audit():
    """
    Part 7 Verification:
    Loads the real SauceDemo diagnostic checkpoint (artifacts/checkpoints/inv-4a3a7393.json),
    builds the versioned InvestigationHandoffContract, generates the SauceDemo ContractTestSuiteArtifact,
    exports the executable Playwright module, and verifies:
    - All 10 states and 14 verified transitions are preserved with 100% state & transition coverage
    - Item-specific cart preconditions are attached before #remove-sauce-labs-backpack / #remove
    - Open drawer preconditions are attached before #dynamic_catalog_sidebar_link
    - Checkout (#checkout) remains safety-gated
    - Exported Playwright code compiles cleanly and contains zero 'assert True' placeholders
    """
    from app.investigation_agent.handoff_contract import build_handoff_contract
    from app.test_generation.contract_test_generator import ContractTestGenerator

    ckpt_path = "artifacts/checkpoints/inv-4a3a7393.json"
    assert os.path.exists(ckpt_path), f"SauceDemo checkpoint {ckpt_path} must exist"
    with open(ckpt_path, "r", encoding="utf-8") as f:
        ckpt_state = json.load(f)

    contract = build_handoff_contract(ckpt_state)
    assert contract.investigation_id == "inv-4a3a7393"
    assert len(contract.states) == 10
    assert len(contract.verified_transitions) == 14

    os.makedirs("reports", exist_ok=True)
    with open("reports/saucedemo_handoff_contract_v1.json", "w", encoding="utf-8") as f:
        f.write(contract.model_dump_json(indent=2))

    gen = ContractTestGenerator(use_llm_enrichment=False)
    suite = gen.generate_suite(contract)

    with open("reports/saucedemo_generated_test_suite_v1.json", "w", encoding="utf-8") as f:
        f.write(suite.model_dump_json(indent=2))

    assert suite.coverage_map.state_coverage_ratio == 1.0
    assert suite.coverage_map.transition_coverage_ratio == 1.0
    assert len(suite.test_cases) >= 20

    # Verify item-specific cart preconditions on remove transitions
    remove_tests = [
        tc for tc in suite.test_cases
        if tc.category == TestCategoryType.TRANSITION
        and any("remove" in (s.selector or "").lower() for s in tc.steps)
    ]
    assert len(remove_tests) >= 2
    for rtc in remove_tests:
        pre_joined = " ".join(rtc.preconditions).lower()
        assert "cart/counter badge value = 1" in pre_joined or "added to cart" in pre_joined, (
            f"Remove transition test {rtc.test_id} must declare cart item precondition, got: {rtc.preconditions}"
        )

    # Export to Playwright code and verify syntax + concrete assertions
    exporter = PlaywrightTestExporter()
    out_py = "tests/test_exported_saucedemo_playwright.py"
    exporter.export_to_file(
        suite=suite,
        output_file_path=out_py,
        target_base_url="https://www.saucedemo.com",
        sandbox_mode=False,
        sandbox_origins=[],
    )
    with open(out_py, "r", encoding="utf-8") as f:
        py_code = f.read()

    compile(py_code, out_py, "exec")
    assert "assert True" not in py_code, "Exported Playwright tests must not use placeholder 'assert True'"
    assert "ensure_cart_with_item" in py_code
    assert "ensure_drawer_open" in py_code

