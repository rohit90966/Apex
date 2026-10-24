import pytest
from app.schemas import (
    InspectionResult,
    FormMetadata,
    FormFieldMetadata,
    PageCategory,
    PageClassification,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.test_planner import DeterministicPlanner


def test_deterministic_planner_login_patterns():
    inspection = InspectionResult(
        inspection_id="insp-login",
        target_url="http://127.0.0.1:8001/login",
        final_url="http://127.0.0.1:8001/login",
        page_title="Sign In",
        buttons=["Log In"],
        forms=[
            FormMetadata(
                form_id="f1",
                fields=[
                    FormFieldMetadata(name="user", input_type="text"),
                    FormFieldMetadata(name="pass", input_type="password"),
                ],
            )
        ],
    )
    classification = PageClassification(
        page_category=PageCategory.LOGIN,
        confidence=0.88,
        evidence=["Observed login form"],
        reasoning_summary="Login page recognized.",
    )

    plan = DeterministicPlanner.generate_plan(
        inspection=inspection,
        classification=classification,
        requirements=["User must be locked after 5 failed attempts"],
        max_cases=5,
    )

    assert plan.plan_id.startswith("plan-")
    assert len(plan.test_cases) > 0
    assert len(plan.test_cases) <= 5

    # Check that requirement test exists
    req_cases = [c for c in plan.test_cases if c.expected_behavior_source == ExpectedBehaviorSource.REQUIREMENT]
    assert len(req_cases) >= 1

    # Check status is always PROPOSED
    for tc in plan.test_cases:
        assert tc.status == TestCaseStatus.PROPOSED


def test_deterministic_planner_feature_derivation():
    inspection = InspectionResult(
        inspection_id="insp-features",
        target_url="http://127.0.0.1:8001/dashboard",
        final_url="http://127.0.0.1:8001/dashboard",
        page_title="Dashboard",
        buttons=["Logout", "Filter"],
    )
    classification = PageClassification(
        page_category=PageCategory.GENERAL,
        confidence=0.85,
        evidence=["Dashboard elements"],
        reasoning_summary="General dashboard",
    )

    class MockFeature:
        def __init__(self, name, ftype, elements):
            self.feature_name = name
            self.feature_type = ftype
            self.target_elements = elements

    features = [
        MockFeature("Export CSV Report", "DATA_EXPORT", ["#export-csv"]),
        MockFeature("Search Filter", "FILTER_BAR", ["#filter-input"]),
    ]

    plan = DeterministicPlanner.generate_plan(
        inspection=inspection,
        classification=classification,
        features=features,
        max_cases=10,
    )

    feature_tests = [tc for tc in plan.test_cases if "Export CSV Report" in tc.name or "Search Filter" in tc.name]
    assert len(feature_tests) == 2
    # Ensure test IDs are unique
    ids = [tc.test_id for tc in plan.test_cases]
    assert len(ids) == len(set(ids))


def test_deterministic_planner_semantic_deduplication():
    inspection = InspectionResult(
        inspection_id="insp-dedup",
        target_url="http://127.0.0.1:8001/contact",
        final_url="http://127.0.0.1:8001/contact",
        page_title="Contact Us",
        forms=[
            FormMetadata(
                form_id="f1",
                fields=[
                    FormFieldMetadata(name="email", is_required=True, field_id="email"),
                ],
            ),
            # Duplicate form with same required field
            FormMetadata(
                form_id="f2",
                fields=[
                    FormFieldMetadata(name="email", is_required=True, field_id="email"),
                ],
            ),
        ],
    )
    classification = PageClassification(
        page_category=PageCategory.CONTACT_FORM,
        confidence=0.9,
        evidence=["Contact form"],
        reasoning_summary="Contact us",
    )

    plan = DeterministicPlanner.generate_plan(
        inspection=inspection,
        classification=classification,
        max_cases=15,
    )

    # Every test_id must be unique
    ids = [tc.test_id for tc in plan.test_cases]
    assert len(ids) == len(set(ids))
    # No duplicate objectives for the same rule/elements
    objs = [tc.objective for tc in plan.test_cases]
    assert len(objs) == len(set(objs))
