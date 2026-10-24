import pytest
from pydantic import ValidationError
from app.schemas import (
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
    PlanningMode,
    TestCase,
    InspectionRequest,
    PageClassification,
)


def test_test_case_schema_valid():
    tc = TestCase(
        test_id="TC-01",
        name="Verify login button exists",
        objective="Ensure login CTA is present on authentication screen",
        page_category=PageCategory.LOGIN,
        relevant_elements=["button#login"],
        action_description="Look for login button",
        expected_outcome="Button is present and visible",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        evidence_to_collect=["DOM Tree"],
        priority=Priority.HIGH,
        preconditions=["User visits /login"],
        requires_test_account=False,
        safe_for_future_automation=True,
        selection_reason="Essential login control",
        confidence=0.95,
        status=TestCaseStatus.PROPOSED,
    )
    assert tc.status == TestCaseStatus.PROPOSED
    assert tc.page_category == PageCategory.LOGIN


def test_test_case_invalid_confidence():
    with pytest.raises(ValidationError):
        TestCase(
            test_id="TC-01",
            name="Invalid",
            objective="Invalid",
            page_category=PageCategory.LOGIN,
            action_description="Invalid",
            expected_outcome="Invalid",
            expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
            priority=Priority.HIGH,
            selection_reason="Invalid",
            confidence=1.5,  # must be <= 1.0
            status=TestCaseStatus.PROPOSED,
        )


def test_inspection_request_schema():
    req = InspectionRequest(url="http://127.0.0.1:8001/login")
    assert req.url == "http://127.0.0.1:8001/login"
    assert req.requirements is None
