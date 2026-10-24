import asyncio
import sys
from app.inspector import WebsiteInspector
from app.execution.runner import QAPlanRunner
from app.schemas import TestCase, PageCategory, Priority, ExpectedBehaviorSource

async def main():
    print("Starting inspection of SauceDemo...")
    inspector = WebsiteInspector()
    inspection = await inspector.inspect("https://www.saucedemo.com/")
    print(f"Inspected page title: {inspection.page_title}")
    print(f"Found {len(inspection.input_fields_summary)} input fields: {[(f.name, f.input_type) for f in inspection.input_fields_summary]}")
    
    runner = QAPlanRunner()
    runner.set_inspection(inspection)
    
    cases = [
        TestCase(
            __test__=False,
            test_id="TC-SAUCE-01",
            name="Password masking test",
            objective="Ensure password input field masks entered characters for shoulder-surfing protection.",
            page_category=PageCategory.LOGIN,
            action_description="Locate password input and verify type is password",
            expected_outcome="Password field masks input characters",
            expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
            priority=Priority.HIGH,
            selection_reason="Security requirement for credential masking",
            confidence=1.0,
        ),
        TestCase(
            __test__=False,
            test_id="TC-SAUCE-02",
            name="Accessibility inspection",
            objective="Check that form controls have accessible names and aria attributes for screen readers.",
            page_category=PageCategory.LOGIN,
            action_description="Inspect page controls for accessible names",
            expected_outcome="All controls have accessible names",
            expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
            priority=Priority.MEDIUM,
            selection_reason="Accessibility compliance",
            confidence=0.9,
        ),
        TestCase(
            __test__=False,
            test_id="TC-SAUCE-03",
            name="Empty credentials validation",
            objective="Ensure submitting without username displays validation error",
            page_category=PageCategory.LOGIN,
            action_description="Leave username and password empty, then click submit",
            expected_outcome="Validation message indicates username is required",
            expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
            priority=Priority.HIGH,
            selection_reason="Form validation check",
            confidence=0.95,
        ),
        TestCase(
            __test__=False,
            test_id="TC-SAUCE-04",
            name="Missing password validation",
            objective="Ensure submitting without password displays validation error",
            page_category=PageCategory.LOGIN,
            action_description="Fill username with 'standard_user', leave password empty, click submit",
            expected_outcome="Validation message indicates password is required",
            expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
            priority=Priority.HIGH,
            selection_reason="Form validation check",
            confidence=0.95,
        ),
        TestCase(
            __test__=False,
            test_id="TC-SAUCE-05",
            name="Whitespace credentials rejection",
            objective="Ensure whitespace-only credentials do not grant access",
            page_category=PageCategory.LOGIN,
            action_description="Enter whitespace in username and password, then click submit",
            expected_outcome="Submission is rejected, remaining on login view or displaying error",
            expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
            priority=Priority.HIGH,
            selection_reason="Input boundary check",
            confidence=0.85,
        ),
    ]
    
    print("\nExecuting test cases via Playwright against https://www.saucedemo.com/...")
    results = await runner.run_all(cases, "https://www.saucedemo.com/")
    print("\n--- RESULTS ---")
    for r in results:
        status_symbol = "PASS" if r.status.value == "PASSED" else "FAIL"
        print(f"[{status_symbol}] {r.test_id}: {r.test_name} -> status={r.status.value}, failure={r.failure_reason}")

if __name__ == "__main__":
    asyncio.run(main())
