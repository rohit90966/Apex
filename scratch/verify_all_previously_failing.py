import asyncio
import sys
sys.path.insert(0, ".")
from app.execution.runner import QAPlanRunner
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from tests.test_knowledge_guided_planner import MockInvestigationReport

async def main():
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()
    cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

    runner = QAPlanRunner()
    
    test_ids_to_verify = [
        "TC-PAT-01",  # Authenticate with valid test credentials
        "TC-PAT-02",  # Logout user session via drawer
        "TC-PAT-03",  # Add product item to cart and verify badge count
        "TC-PAT-04",  # Remove item from shopping cart
        "TC-PAT-07",  # Checkout step two numeric tax
        "TC-PAT-09",  # Submit valid customer info on checkout step one
        "TC-PAT-11",  # Login with empty password boundary
        "TC-TR-06",   # Item detail add-to-cart
        "TC-VAL-03",  # Missing first name on checkout
        "TC-VAL-04",  # Missing postal code on checkout
    ]

    print(f"Total candidate cases: {len(cases)}")
    for tc in cases:
        if tc.test_id in test_ids_to_verify:
            print(f"\n=======================================================")
            print(f"RUNNING: [{tc.test_id}] {tc.name}")
            print(f"Steps: {len(tc.steps)}")
            for s in tc.steps:
                print(f"  {s.step_number}. {s.action.value} {s.selector or s.value} -> {s.assertion.assertion_type.value if s.assertion else 'None'}")
            
            res = await runner.run_test(tc, "https://www.saucedemo.com/")
            print(f"RESULT: {res.status.value} (Completed {res.steps_completed}/{res.total_steps})")
            if res.failure_reason:
                print(f"FAILURE REASON: {res.failure_reason}")
            assert res.status.value == "PASSED", f"Test {tc.test_id} failed: {res.failure_reason}"

    print("\n=======================================================")
    print("ALL VERIFIED TESTS EXECUTED AND PASSED 100%!")

if __name__ == "__main__":
    asyncio.run(main())
