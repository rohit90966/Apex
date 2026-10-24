from app.test_planning.planner import KnowledgeGuidedTestPlanner
from tests.test_knowledge_guided_planner import MockInvestigationReport

planner = KnowledgeGuidedTestPlanner()
report = MockInvestigationReport()
cases, coverage, model = planner.plan_test_suite_sync(report, target_count=30)

print(f"Total synthesized test cases: {len(cases)}")
for tc in cases:
    print(f"\n[{tc.test_id}] {tc.name} (requires_auth={tc.requires_test_account})")
    for s in tc.steps:
        print(f"  Step {s.step_number}: {s.action.value} {s.selector or s.value or ''} -> assert: {s.assertion.assertion_type.value if s.assertion else 'None'}")
