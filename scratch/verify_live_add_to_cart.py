import asyncio
from app.execution.runner import QAPlanRunner
from app.test_planning.planner import KnowledgeGuidedTestPlanner
from tests.test_knowledge_guided_planner import MockInvestigationReport

async def main():
    planner = KnowledgeGuidedTestPlanner()
    report = MockInvestigationReport()
    cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

    # Find the add to cart test case
    add_to_cart_case = next((tc for tc in cases if "Add Product to Cart" in tc.name), None)
    assert add_to_cart_case is not None, "Could not find Add Product to Cart test case!"

    print(f"Testing test case: {add_to_cart_case.test_id} - {add_to_cart_case.name}")
    print(f"Steps count: {len(add_to_cart_case.steps)}")
    for s in add_to_cart_case.steps:
        print(f"  {s.step_number}. {s.action.value} {s.selector or s.value}")

    runner = QAPlanRunner()
    result = await runner.run_test(add_to_cart_case, "https://www.saucedemo.com/")

    print("\nExecution Result:")
    print(f"  Status: {result.status.value}")
    print(f"  Steps completed: {result.steps_completed} / {result.total_steps}")
    print(f"  Failure reason: {result.failure_reason}")
    print(f"  Duration: {result.duration_seconds}s")
    for st in result.step_results:
        print(f"    Step {st.step_number} ({st.action.value}): {st.status.value} - {st.error_message or 'OK'}")

    assert result.status.value == "PASSED", f"Execution failed: {result.failure_reason}"
    print("\nSUCCESS: Add to cart executed and PASSED cleanly!")

if __name__ == "__main__":
    asyncio.run(main())
