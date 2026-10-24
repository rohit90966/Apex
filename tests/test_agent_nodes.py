import pytest
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    FormMetadata,
    FormFieldMetadata,
    TestCase,
    TestCaseStatus,
    Priority,
    ExpectedBehaviorSource,
)
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.agents.requirement_analyst import RequirementAnalystNode
from app.agents.evidence_interpreter import EvidenceInterpreterNode
from app.agents.risk_analyst import RiskAnalystNode
from app.agents.strategy_planner import StrategyPlannerNode
from app.agents.qa_critic import QACriticNode


def test_requirement_analyst_explicit_and_ambiguous():
    state: AgentWorkflowState = {
        "user_requirements": [
            "Password must be at least 8 characters",
            "Page must be fast and user-friendly",
        ],
        "website_description": "Demo portal",
    }
    result = RequirementAnalystNode.run(state)
    analysis = result["requirement_analysis"]

    assert len(analysis.explicit_requirements) == 2
    assert analysis.explicit_requirements[0].id == "REQ-01"
    # Should identify ambiguity in second requirement
    assert len(analysis.explicit_requirements[1].ambiguities) > 0


def test_evidence_interpreter_login_elements():
    inspection = InspectionResult(
        inspection_id="insp-1",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Sign In",
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="f1",
                fields=[
                    FormFieldMetadata(name="username", input_type="text", label="User Name"),
                    FormFieldMetadata(name="password", input_type="password", label="Password", is_required=True),
                ],
            )
        ],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Form observed"],
        reasoning_summary="Login recognized",
    )
    state: AgentWorkflowState = {
        "inspection_evidence": inspection,
        "page_classification": classification,
    }

    result = EvidenceInterpreterNode.run(state)
    analysis = result["evidence_analysis"]

    assert len(analysis.observed_targets) >= 3  # username, password, button
    assert any(t.label_or_name == "Password" for t in analysis.observed_targets)
    assert any("verify_required_enforcement" in t.testable_actions for t in analysis.observed_targets)


def test_risk_analyst_prioritization():
    state = RequirementAnalystNode.run({
        "user_requirements": ["Account lockout after 3 failed tries"],
    })
    state["page_classification"] = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Observed login"],
        reasoning_summary="Login",
    )
    risk_res = RiskAnalystNode.run(state)
    risk_analysis = risk_res["risk_analysis"]

    assert len(risk_analysis.risks) >= 2
    # Ensure unaddressed requirement is mapped as high severity risk
    req_risks = [r for r in risk_analysis.risks if "RISK-REQ" in r.risk_id]
    assert len(req_risks) == 1
    assert req_risks[0].severity == Priority.HIGH


def test_strategy_planner_scenarios():
    state = RequirementAnalystNode.run({
        "user_requirements": ["Must allow password reset"],
    })
    state["page_classification"] = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.9,
        evidence=["Observed login"],
        reasoning_summary="Login",
    )
    state.update(RiskAnalystNode.run(state))
    strat_res = StrategyPlannerNode.run(state)
    strategy = strat_res["test_strategy"]

    assert len(strategy.scenarios) > 0
    assert any(s.provenance == ExpectedBehaviorSource.REQUIREMENT for s in strategy.scenarios)


def test_qa_critic_detects_duplicates_and_missing_reqs():
    req_state = RequirementAnalystNode.run({
        "user_requirements": ["Must enforce two factor authentication"],
    })

    tc1 = TestCase(
        test_id="TC-01",
        name="Verify login button exists",
        objective="Verify button",
        page_category=PageCategory.LOGIN,
        relevant_elements=["btn"],
        action_description="Click login",
        expected_outcome="Button responds",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        evidence_to_collect=["DOM"],
        priority=Priority.HIGH,
        selection_reason="Essential control",
        confidence=0.9,
        status=TestCaseStatus.PROPOSED,
    )
    # Duplicate name
    tc2 = TestCase(
        test_id="TC-02",
        name="Verify login button exists",
        objective="Verify button again",
        page_category=PageCategory.LOGIN,
        relevant_elements=["btn"],
        action_description="Click login again",
        expected_outcome="Button responds",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        evidence_to_collect=["DOM"],
        priority=Priority.HIGH,
        selection_reason="Essential control",
        confidence=0.9,
        status=TestCaseStatus.PROPOSED,
    )

    req_state["proposed_test_cases"] = [tc1, tc2]
    critic_res = QACriticNode.run(req_state)
    critique = critic_res["critique_result"]

    assert critique.issues_found is True
    assert "DUPLICATES" in critique.issue_categories
    assert "MISSING_REQUIREMENT" in critique.issue_categories
    assert "TC-02" in critique.duplicate_test_case_ids
