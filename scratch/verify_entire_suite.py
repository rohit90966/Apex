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
    
    passed = []
    failed = []
    
    print(f"Total candidate test suite cases: {len(cases)}")
    for tc in cases:
        print(f"\n[{tc.test_id}] {tc.name} ({len(tc.steps)} steps)")
        res = await runner.run_test(tc, "https://www.saucedemo.com/")
        if res.status.value == "PASSED":
            print(f"  --> PASSED ({res.steps_completed}/{res.total_steps})")
            passed.append(tc.test_id)
        else:
            print(f"  --> FAILED ({res.steps_completed}/{res.total_steps}): {res.failure_reason}")
            failed.append((tc.test_id, res.failure_reason))

    print("\n" + "=" * 60)
    print(f"SUITE SUMMARY: Executed {len(cases)} | Passed {len(passed)} | Failed {len(failed)}")
    if failed:
        print("Failures:")
        for fid, reason in failed:
            print(f"  - {fid}: {reason}")
    else:
        print("100% OF TESTS PASSED CLEANLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
