import asyncio
from app.execution.runner import QAPlanRunner
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from tests.test_knowledge_guided_planner import MockInvestigationReport

async def main():
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()
    cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

    pat_case = next((tc for tc in cases if tc.test_id == "TC-PAT-03"), None)
    val_case = next((tc for tc in cases if tc.test_id == "TC-VAL-03"), None)

    runner = QAPlanRunner()

    if pat_case:
        print(f"\n--- Testing {pat_case.test_id}: {pat_case.name} ---")
        res1 = await runner.run_test(pat_case, "https://www.saucedemo.com/")
        print(f"Result: {res1.status.value} (Steps: {res1.steps_completed}/{res1.total_steps})")
        assert res1.status.value == "PASSED", f"Failed: {res1.failure_reason}"

    if val_case:
        print(f"\n--- Testing {val_case.test_id}: {val_case.name} ---")
        res2 = await runner.run_test(val_case, "https://www.saucedemo.com/")
        print(f"Result: {res2.status.value} (Steps: {res2.steps_completed}/{res2.total_steps})")
        assert res2.status.value == "PASSED", f"Failed: {res2.failure_reason}"

    print("\nALL VERIFICATIONS PASSED!")

if __name__ == "__main__":
    asyncio.run(main())
