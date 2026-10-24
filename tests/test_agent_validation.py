import pytest
from app.schemas import (
    TestCase,
    TestCaseStatus,
    Priority,
    ExpectedBehaviorSource,
    PageCategory,
)
from app.agents.models import RequirementAnalysis, RequirementItem
from app.agents.plan_validator import PlanValidatorNode
from app.agents.state import AgentWorkflowState, WorkflowStatus


def test_validator_enforces_proposed_status_and_dedup():
    tc1 = TestCase(
        test_id="TC-01",
        name="Unique test case",
        objective="Validate test",
        page_category=PageCategory.LOGIN,
        action_description="Look for form",
        expected_outcome="Form visible",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Reason",
        confidence=0.9,
        status=TestCaseStatus.PROPOSED,
    )
    tc2 = TestCase(
        test_id="TC-02",
        name="Unique test case",  # Duplicate name
        objective="Validate test again",
        page_category=PageCategory.LOGIN,
        action_description="Look for form again",
        expected_outcome="Form visible",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Reason",
        confidence=0.9,
        status=TestCaseStatus.PROPOSED,
    )

    state: AgentWorkflowState = {
        "proposed_test_cases": [tc1, tc2],
        "requirement_analysis": RequirementAnalysis(),
    }

    res = PlanValidatorNode.run(state)
    val = res["validation_result"]

    assert val.is_valid is True
    assert val.deduplicated_count == 1
    assert len(res["proposed_test_cases"]) == 1


def test_validator_calculates_requirement_coverage():
    req_item = RequirementItem(
        id="REQ-01",
        text="Password must obscure characters",
        is_explicit=True,
    )
    req_analysis = RequirementAnalysis(explicit_requirements=[req_item])

    tc = TestCase(
        test_id="TC-01",
        name="Verify password must obscure characters",
        objective="Ensure password masking",
        page_category=PageCategory.LOGIN,
        action_description="Check password type",
        expected_outcome="Characters obscured",
        expected_behavior_source=ExpectedBehaviorSource.REQUIREMENT,
        priority=Priority.HIGH,
        selection_reason="Requirement",
        confidence=0.95,
        status=TestCaseStatus.PROPOSED,
    )

    state: AgentWorkflowState = {
        "proposed_test_cases": [tc],
        "requirement_analysis": req_analysis,
    }

    res = PlanValidatorNode.run(state)
    cov = res["coverage_summary"]

    assert cov.total_requirements == 1
    assert cov.covered_count == 1
    assert cov.details[0].status == "COVERED"
    assert "TC-01" in cov.details[0].covering_test_ids


def test_validator_rejects_code_injection_strings():
    tc = TestCase(
        test_id="TC-INJECT",
        name="Injected test case",
        objective="Malicious objective",
        page_category=PageCategory.LOGIN,
        action_description="Run python exec('import os; os.system(\"calc\")')",
        expected_outcome="Calculation",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.HIGH,
        selection_reason="Reason",
        confidence=0.9,
        status=TestCaseStatus.PROPOSED,
    )

    state: AgentWorkflowState = {
        "proposed_test_cases": [tc],
    }

    res = PlanValidatorNode.run(state)
    val = res["validation_result"]

    assert len(val.errors) > 0
    assert any("executable code token detected" in err for err in val.errors)
