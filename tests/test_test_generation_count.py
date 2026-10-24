"""
tests/test_test_generation_count.py — Comprehensive tests for test generation count & grounding.

Validates:
1. Model returning 3 valid cases when 6 requested (pipeline preserves them without crashing).
2. Pipeline preserves all 6 valid distinct cases through critic & validator.
3. Duplicate cases removed without reducing unique valid cases.
4. Observed input constraints produce appropriate test ideas.
5. Missing constraints do NOT produce invented boundary tests.
6. Page-load test does not require login navigation.
7. Model returns fewer cases when insufficient evidence exists.
8. Deterministic fallback preserves same grounding rules.
"""

import pytest
from unittest.mock import AsyncMock
from app.schemas import (
    InspectionResult,
    PageClassification,
    PageCategory,
    PlanningMode,
    FormMetadata,
    FormFieldMetadata,
    TestCase,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.test_planning_agent import TestPlanningAgent
from app.test_planner import DeterministicPlanner
from app.llm.base import BaseLLMProvider
from app.agents.deterministic_derivation import DeterministicDerivationNode
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.execution.runner import QAPlanRunner


def _create_sample_inspection(with_constraints: bool = False) -> InspectionResult:
    """Creates a sample inspection resembling Saucedemo or a standard form."""
    if with_constraints:
        fields = [
            FormFieldMetadata(
                field_id="user-name",
                name="user-name",
                input_type="text",
                label="Username",
                maxlength=20,
                is_required=True,
            ),
            FormFieldMetadata(
                field_id="password",
                name="password",
                input_type="password",
                label="Password",
                minlength=6,
                is_required=True,
            ),
        ]
    else:
        # Unconstrained fields like Saucedemo
        fields = [
            FormFieldMetadata(
                field_id="user-name",
                name="user-name",
                input_type="text",
                label="Username",
            ),
            FormFieldMetadata(
                field_id="password",
                name="password",
                input_type="password",
                label="Password",
            ),
        ]

    return InspectionResult(
        inspection_id="insp-count-test-01",
        target_url="https://www.saucedemo.com/",
        final_url="https://www.saucedemo.com/",
        http_status=200,
        page_title="Swag Labs",
        forms=[FormMetadata(form_id="login_form", fields=fields)],
        buttons=["Login"],
        links=[],
        input_fields_summary=fields,
        inspection_duration_seconds=1.2,
    )


class MockProviderWithCases(BaseLLMProvider):
    def __init__(self, cases):
        self.model = "gemini-flash-latest"
        self._cases = cases

    async def generate_json(self, system_prompt: str, user_prompt: str):
        return {"test_cases": self._cases}


@pytest.mark.asyncio
async def test_condition_1_model_returning_3_valid_cases_preserved():
    """Condition 1: Model returning 3 valid cases when 6 requested is preserved without crashing."""
    inspection = _create_sample_inspection(with_constraints=False)
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.95,
        reasoning_summary="Test login classification",
    )

    three_cases = [
        {
            "test_id": "TC-01",
            "name": "Verify login form controls presence",
            "objective": "Verify user-name and password fields are rendered",
            "relevant_elements": ["user-name", "password"],
            "action_description": "Inspect DOM for user-name and password input fields",
            "expected_outcome": "Both input fields are visible and interactable",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-02",
            "name": "Verify password masking invariant",
            "objective": "Verify password characters are masked",
            "relevant_elements": ["password"],
            "action_description": "Type into password field and check type=password attribute",
            "expected_outcome": "Field type is password and characters are obscured",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-03",
            "name": "Verify empty credential submission validation",
            "objective": "Verify blank login attempt is handled gracefully",
            "relevant_elements": ["Login", "user-name"],
            "action_description": "Click Login with empty fields",
            "expected_outcome": "Client-side validation highlights error without unexpected crash",
            "priority": "HIGH",
            "expected_behavior_source": "INFERRED",
        },
    ]

    provider = MockProviderWithCases(three_cases)
    agent = TestPlanningAgent(provider=provider)

    plan = await agent.create_plan(inspection=inspection, classification=classification)
    assert plan is not None
    assert len(plan.test_cases) == 3
    assert [tc.test_id for tc in plan.test_cases] == ["TC-01", "TC-02", "TC-03"]
    assert plan.planning_mode == PlanningMode.GEMINI


@pytest.mark.asyncio
async def test_condition_2_pipeline_preserves_all_6_valid_distinct_cases():
    """Condition 2: Pipeline preserves all 6 valid distinct cases through critic & validator."""
    inspection = _create_sample_inspection(with_constraints=False)
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.95,
        reasoning_summary="Test login classification",
    )

    six_cases = [
        {
            "test_id": "TC-01",
            "name": "Verify login form element visibility and layout",
            "objective": "Ensure user-name and password fields are visible",
            "relevant_elements": ["user-name", "password"],
            "action_description": "Check visibility of login inputs",
            "expected_outcome": "Form controls are rendered and visible",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-02",
            "name": "Verify password field masking invariant",
            "objective": "Ensure password input obscures characters",
            "relevant_elements": ["password"],
            "action_description": "Inspect password input type attribute",
            "expected_outcome": "Field type is password and characters are obscured",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-03",
            "name": "Verify empty credentials submission handling",
            "objective": "Ensure clicking submit with empty inputs triggers validation",
            "relevant_elements": ["Login", "user-name"],
            "action_description": "Click Login without entering credentials",
            "expected_outcome": "Validation error message indicates missing input",
            "priority": "HIGH",
            "expected_behavior_source": "INFERRED",
        },
        {
            "test_id": "TC-04",
            "name": "Verify login button accessibility and focusability",
            "objective": "Ensure Login button has accessible name and can receive focus",
            "relevant_elements": ["Login"],
            "action_description": "Inspect accessible name and focus behavior for Login button",
            "expected_outcome": "Login button exposes accessible name and focus ring",
            "priority": "MEDIUM",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-05",
            "name": "Verify page load integrity and absence of console errors",
            "objective": "Ensure initial page load emits no uncaught exceptions",
            "relevant_elements": ["https://www.saucedemo.com/"],
            "action_description": "Load URL and listen for console errors",
            "expected_outcome": "Page loads with HTTP 200 and 0 console errors",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-06",
            "name": "Verify whitespace input submission handling",
            "objective": "Ensure submitting whitespace-only credentials does not bypass validation",
            "relevant_elements": ["user-name", "Login"],
            "action_description": "Enter space characters into user-name and submit",
            "expected_outcome": "Client-side validation rejects whitespace input",
            "priority": "MEDIUM",
            "expected_behavior_source": "INFERRED",
        },
    ]

    provider = MockProviderWithCases(six_cases)
    agent = TestPlanningAgent(provider=provider)

    plan = await agent.create_plan(inspection=inspection, classification=classification)
    assert plan is not None
    assert len(plan.test_cases) == 6
    test_names = [tc.name for tc in plan.test_cases]
    assert len(set(test_names)) == 6


@pytest.mark.asyncio
async def test_condition_3_duplicate_cases_removed_without_reducing_unique_valid():
    """Condition 3: Duplicate cases removed without reducing unique valid cases."""
    inspection = _create_sample_inspection(with_constraints=False)
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.95,
        reasoning_summary="Test login classification",
    )

    cases_with_duplicate = [
        {
            "test_id": "TC-01",
            "name": "Verify password masking",
            "objective": "Ensure password input obscures characters",
            "relevant_elements": ["password"],
            "action_description": "Inspect password type",
            "expected_outcome": "Characters obscured",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-02",
            "name": "Verify password masking",  # Duplicate name
            "objective": "Ensure password input obscures characters",
            "relevant_elements": ["password"],
            "action_description": "Inspect password type",
            "expected_outcome": "Characters obscured",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        },
        {
            "test_id": "TC-03",
            "name": "Verify empty credentials submission",
            "objective": "Ensure empty submission triggers error",
            "relevant_elements": ["Login", "user-name"],
            "action_description": "Click Login empty",
            "expected_outcome": "Error displayed",
            "priority": "HIGH",
            "expected_behavior_source": "INFERRED",
        },
        {
            "test_id": "TC-04",
            "name": "Verify login button accessibility",
            "objective": "Ensure button has accessible name",
            "relevant_elements": ["Login"],
            "action_description": "Inspect accessible name",
            "expected_outcome": "Accessible name present",
            "priority": "MEDIUM",
            "expected_behavior_source": "INVARIANT",
        },
    ]

    provider = MockProviderWithCases(cases_with_duplicate)
    agent = TestPlanningAgent(provider=provider)

    plan = await agent.create_plan(inspection=inspection, classification=classification)
    assert plan is not None
    # 1 duplicate removed, 3 unique preserved
    assert len(plan.test_cases) == 3
    names = [tc.name for tc in plan.test_cases]
    assert names.count("Verify password masking") == 1
    assert "Verify empty credentials submission" in names
    assert "Verify login button accessibility" in names


def test_condition_4_observed_constraints_produce_appropriate_test_ideas():
    """Condition 4: Observed input constraints produce appropriate test ideas."""
    inspection = _create_sample_inspection(with_constraints=True)
    state = {
        "inspection_evidence": inspection,
        "investigation_report": None,
        "warnings": [],
        "user_requirements": [],
    }

    result = DeterministicDerivationNode.run(state)
    ideas = result["test_ideas"]
    evidence = result["evidence_ledger"]

    # maxlength=20 should produce BVA-MAXLENGTH-001 ideas
    maxlength_ideas = [i for i in ideas if i.rule_id == "BVA-MAXLENGTH-001"]
    assert len(maxlength_ideas) >= 3
    b_values = {i.derived_values.get("boundary_value") for i in maxlength_ideas}
    assert b_values == {19, 20, 21}

    # minlength=6 should produce BVA-MINLENGTH-001 ideas
    minlength_ideas = [i for i in ideas if i.rule_id == "BVA-MINLENGTH-001"]
    assert len(minlength_ideas) >= 3
    min_values = {i.derived_values.get("boundary_value") for i in minlength_ideas}
    assert min_values == {5, 6, 7}


def test_condition_5_missing_constraints_do_not_produce_invented_boundary_tests():
    """Condition 5: Missing constraints do NOT produce invented boundary tests."""
    inspection = _create_sample_inspection(with_constraints=False)
    state = {
        "inspection_evidence": inspection,
        "investigation_report": None,
        "warnings": [],
        "user_requirements": [],
    }

    result = DeterministicDerivationNode.run(state)
    ideas = result["test_ideas"]
    unknowns = result["unknowns"]

    # NO BVA ideas should be derived because no constraint attributes exist
    bva_ideas = [i for i in ideas if i.rule_id.startswith("BVA-")]
    assert len(bva_ideas) == 0

    # Instead, unknowns are recorded for server-side validation
    assert len(unknowns) >= 2
    assert all("no HTML constraint attributes" in u.question for u in unknowns)

    # Invariants are derived for presence, masking, and accessibility
    invar_ideas = [i for i in ideas if i.rule_id.startswith("INVAR-")]
    assert len(invar_ideas) >= 3
    rule_ids = {i.rule_id for i in invar_ideas}
    assert "INVAR-MASKING-001" in rule_ids
    assert "INVAR-A11Y-001" in rule_ids
    assert "INVAR-LOAD-001" in rule_ids


def test_condition_6_page_load_test_does_not_require_login_navigation():
    """Condition 6: Page-load test does not require login navigation (expects initial URL/DOM state, no /inventory)."""
    runner = QAPlanRunner()
    tc = TestCase(
        __test__=False,
        test_id="TC-LOAD-01",
        name="Verify page load integrity and absence of console errors",
        objective="Ensure page renders completely without uncaught console errors",
        page_category=PageCategory.LOGIN,
        action_description="Load the target URL and monitor browser console",
        expected_outcome="Page loads with HTTP 200, container renders, no console errors",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Page load baseline invariant",
        confidence=0.98,
    )
    steps = runner.synthesize_steps(tc, "https://www.saucedemo.com/")
    assert len(steps) >= 1

    # Ensure no step requires or asserts navigation to /inventory
    for s in steps:
        if s.assertion and s.assertion.expected_value:
            assert "/inventory" not in str(s.assertion.expected_value)
        if s.value:
            assert "/inventory" not in str(s.value)


@pytest.mark.asyncio
async def test_condition_7_model_returns_fewer_cases_when_insufficient_evidence():
    """Condition 7: Model returns fewer cases when insufficient evidence exists."""
    # Inspection with 0 forms and 0 buttons
    empty_inspection = InspectionResult(
        inspection_id="insp-empty-01",
        target_url="https://example.com/empty",
        final_url="https://example.com/empty",
        http_status=200,
        page_title="Blank Page",
        forms=[],
        buttons=[],
        links=[],
        input_fields_summary=[],
        inspection_duration_seconds=0.5,
    )
    classification = PageClassification(
        page_category=PageCategory.GENERAL,
        confidence=0.50,
        reasoning_summary="Test general classification",
    )

    # Model only returns 1 generic page-load test
    single_case = [
        {
            "test_id": "TC-01",
            "name": "Verify page load integrity",
            "objective": "Verify page loads without errors",
            "relevant_elements": ["https://example.com/empty"],
            "action_description": "Load target page",
            "expected_outcome": "HTTP status 200 and no errors",
            "priority": "HIGH",
            "expected_behavior_source": "INVARIANT",
        }
    ]

    provider = MockProviderWithCases(single_case)
    agent = TestPlanningAgent(provider=provider)

    plan = await agent.create_plan(inspection=empty_inspection, classification=classification)
    assert plan is not None
    assert len(plan.test_cases) == 1
    assert plan.test_cases[0].test_id == "TC-01"


def test_condition_8_deterministic_fallback_preserves_same_grounding_rules():
    """Condition 8: Deterministic fallback preserves same grounding rules (no invented password length)."""
    inspection = _create_sample_inspection(with_constraints=False)
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.95,
        reasoning_summary="Test login classification",
    )

    fallback_plan = DeterministicPlanner.generate_plan(
        inspection=inspection,
        classification=classification,
        max_cases=15,
        planning_mode=PlanningMode.RULE_BASED_FALLBACK,
    )

    assert fallback_plan is not None
    # Verify fallback generates 6 grounded test cases
    assert len(fallback_plan.test_cases) == 6

    # Verify no test case invents a password minimum length policy without minlength attribute
    for tc in fallback_plan.test_cases:
        tc_text = f"{tc.name} {tc.objective}".lower()
        assert "minimum length" not in tc_text or "minlength" in tc_text
        assert "at least 6" not in tc_text

    # Verify key test cases exist
    names = [tc.name for tc in fallback_plan.test_cases]
    assert any("visibility" in n.lower() for n in names)
    assert any("masking" in n.lower() for n in names)
    assert any("empty submission" in n.lower() for n in names)
    assert any("page load" in n.lower() for n in names)
    assert any("accessible" in n.lower() for n in names)
