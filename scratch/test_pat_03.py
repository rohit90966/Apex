import asyncio
from app.execution.runner import QAPlanRunner
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from tests.test_knowledge_guided_planner import MockInvestigationReport

async def main():
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()
    cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

    for tc in cases:
        if "Empty Password" in tc.name:
            print(f"\nFound test: {tc.test_id} - {tc.name}")
            for s in tc.steps:
                print(f"  Step {s.step_number}: {s.action.value} {s.selector or s.value} -> assert: {s.assertion}")
            runner = QAPlanRunner()
            res = await runner.run_test(tc, "https://www.saucedemo.com/")
            print(f"Status: {res.status.value}")
            print(f"Failure reason: {res.failure_reason}")
            for st in res.step_results:
                print(f"  Step {st.step_number} ({st.action.value}): {st.status.value} - error: {st.error_message}")

if __name__ == "__main__":
    asyncio.run(main())
