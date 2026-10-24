"""
scripts/validate_end_to_end.py
==============================
End-to-end evidence and exploration validator for VerityQA:
1. Validates Port 8001 (Benchmark demo app with BUG-01..04).
2. Validates Port 8002 (TaskDash - non-login, tab-based navigation, modal dialogs, tasks).
3. Traces observed DOM -> actions -> assertions -> outcomes -> defects.
4. Outputs structured JSON and console summary.
"""

import asyncio
import json
import os
import sys
import time

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.exploration.explorer import AutonomousExplorer
from app.exploration.graph import ExploreRequest
from app.schemas import (
    TestCase,
    TestCaseStatus,
    Priority,
    ExpectedBehaviorSource,
    ActionType,
    AssertionType,
    ExecutionStatus,
    PageCategory,
)
from app.inspector import WebsiteInspector
from app.execution.runner import QAPlanRunner
from app.execution.executor import BrowserExecutor
from app.bugs.investigator import BugInvestigator, InvestigationVerdict
from app.benchmark.manifest import BenchmarkEvaluator, PLANTED_BUG_MANIFEST


async def validate_fixture1_benchmark():
    print("\n" + "=" * 60)
    print("RUN 1: Fixture 1 Benchmark Validation (http://127.0.0.1:8001/)")
    print("=" * 60)
    from app.main import run_benchmark
    response = await run_benchmark()
    body = json.loads(response.body.decode("utf-8"))
    print(f"Total planted bugs evaluated: {body.get('total_planted_bugs')}")
    print(f"True Positives: {body.get('true_positives_detected')}")
    print(f"False Positives: {body.get('false_positives')}")
    print(f"False Negatives (Missed): {body.get('missed_bugs')}")
    print(f"Precision: {body.get('precision', 0) * 100:.1f}%")
    print(f"Recall: {body.get('recall', 0) * 100:.1f}%")
    print(f"F1 Score: {body.get('f1_score', 0) * 100:.1f}%")
    print(f"Detected Bug IDs: {body.get('detected_bug_ids')}")
    print(f"Missed Bug IDs: {body.get('missed_bug_ids')}")
    return body


async def validate_fixture2_exploration():
    print("\n" + "=" * 60)
    print("RUN 2: Fixture 2 Autonomous Exploration (http://127.0.0.1:8002/)")
    print("Workflow: TaskDash (Non-login, Tab-based navigation, Modals, Tasks)")
    print("=" * 60)
    explorer = AutonomousExplorer()
    req = ExploreRequest(
        url="http://127.0.0.1:8002/",
        max_depth=2,
        max_actions=10,
        max_pages=5,
        max_time_seconds=30.0,
    )
    result = await explorer.explore(req)
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"States discovered: {result.states_discovered}")
    print(f"Transitions observed: {result.transitions_observed}")
    print(f"New states found: {result.new_states_found}")
    total_elements = sum(len(s.interactive_elements) for s in result.graph.states.values())
    print(f"Interactive elements discovered: {total_elements}")
    print(f"Budget exit reason: {result.graph.budget_reason}")

    print("\nDiscovered States:")
    for sid, st in result.graph.states.items():
        print(f"  - State [{sid}]: URL='{st.url}', Title='{st.page_title}', Controls={len(st.interactive_elements)}")

    print("\nObserved State Transitions:")
    for tr in result.graph.transitions:
        clean_name = str(tr.target_element_name).encode("ascii", errors="replace").decode("ascii")
        print(f"  - {tr.source_state_id} --[{tr.action_type.value}: {tr.target_element_selector} ('{clean_name}')]--> {tr.resulting_state_id} (new={tr.new_state_discovered})")

    return result


async def validate_fixture2_execution_and_assertions():
    print("\n" + "=" * 60)
    print("RUN 3: Observation-Driven Test Execution on Fixture 2")
    print("=" * 60)
    inspector = WebsiteInspector()
    inspection = await inspector.inspect("http://127.0.0.1:8002/")
    print(f"Page title: {inspection.page_title}")
    print(f"Interactive inputs: {len(inspection.input_fields_summary)}")
    print(f"Buttons: {inspection.buttons}")

    runner = QAPlanRunner()
    runner.set_inspection(inspection)

    # 1. Test: Empty task creation (Testing BUG-F2-01: empty title submitted)
    tc_empty = TestCase(
        __test__=False,
        test_id="TC-F2-EMPTY",
        name="Submit empty task triggers validation error",
        objective="Ensure task creation requires non-empty title",
        page_category=PageCategory.GENERAL,
        action_description="Open task modal and click submit without entering title",
        expected_outcome="Validation error displayed or submission blocked",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Required field constraint on task creation",
        confidence=0.9,
    )

    # 2. Test: Accessibility check on controls
    tc_a11y = TestCase(
        __test__=False,
        test_id="TC-F2-A11Y",
        name="Verify interactive controls have accessible names",
        objective="Check that buttons, tabs, and inputs have computed accessible names",
        page_category=PageCategory.GENERAL,
        action_description="Inspect interactive controls for accessible names",
        expected_outcome="All interactive controls have computed accessible names",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.MEDIUM,
        selection_reason="Accessibility compliance",
        confidence=0.9,
    )

    # 3. Test: Tab navigation state transition
    tc_tab = TestCase(
        __test__=False,
        test_id="TC-F2-TAB",
        name="Switching tabs displays corresponding task view",
        objective="Verify clicking tab switches task view",
        page_category=PageCategory.GENERAL,
        action_description="Click 'Done' tab and verify view renders",
        expected_outcome="Done tasks tab is active",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.MEDIUM,
        selection_reason="Tab navigation invariant",
        confidence=0.9,
    )

    tests = [tc_empty, tc_a11y, tc_tab]
    results = await runner.run_all(tests, "http://127.0.0.1:8002/")

    investigator = BugInvestigator(runner=runner)
    for tc, res in zip(tests, results):
        print(f"\nTest: [{res.status.value}] {tc.test_id}: {tc.name}")
        print(f"  Steps completed: {res.steps_completed}/{res.total_steps}")
        print(f"  Assertions evaluated: {res.assertions_evaluated}, Satisfied: {res.assertions_satisfied}")
        if res.evidence_retained:
            print(f"  Evidence retained: {json.dumps(res.evidence_retained, indent=4)}")
        if res.status != ExecutionStatus.PASSED:
            print(f"  Failure reason: {res.failure_reason}")
            # Run investigation
            report = await investigator.investigate_failure(res, tc, "http://127.0.0.1:8002/")
            print(f"  Investigation Verdict: {report.verification_status.value}")
            print(f"  Summary: {report.summary}")

    return results


async def main():
    print("Starting VerityQA End-to-End Validation Run...")
    summary1 = await validate_fixture1_benchmark()
    exp_res = await validate_fixture2_exploration()
    exec_res = await validate_fixture2_execution_and_assertions()
    print("\n" + "=" * 60)
    print("END-TO-END VALIDATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
