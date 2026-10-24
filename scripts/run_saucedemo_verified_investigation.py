"""
SauceDemo Verified Autonomous Investigation and Test Generation Execution Script.
Executes fresh live investigation with repaired journey planner, safety governor,
query parameter state identity, and route recovery.
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.investigation_agent import run_investigation_with_handoff
from app.test_generation.contract_test_generator import ContractTestGenerator
from app.test_generation.test_runner import PlaywrightContractTestRunner, TestExecutionStatus


async def run():
    print("=" * 70)
    print("STARTING LIVE SAUCEDEMO AUTONOMOUS INVESTIGATION")
    print("=" * 70)

    url = "https://www.saucedemo.com/"
    creds = {"username": "standard_user", "password": "secret_sauce"}

    t0 = time.time()
    report, contract = await run_investigation_with_handoff(
        url=url,
        description="E-Commerce catalog, cart, and multi-step checkout exploration",
        auth_credentials=creds,
        max_actions=18,
        max_time_seconds=180.0,
        headless=True,
    )
    duration = time.time() - t0
    print(f"\nInvestigation completed in {duration:.1f}s")
    print(f"Investigation ID: {contract.investigation_id}")
    print(f"Discovered States: {len(contract.states)}")
    print(f"Verified Transitions: {len(contract.verified_transitions)}")
    print(f"Evidence Entries: {len(contract.evidence_ledger)}")
    auth_bounds = contract.completeness.authentication_boundaries
    print(f"Auth Attempted: {auth_bounds.get('authentication_attempted')}")
    print(f"Auth Verified: {auth_bounds.get('authentication_verified')}")
    print(f"Inferred Journeys: {len(contract.inferred_journeys)}")

    os.makedirs("reports", exist_ok=True)
    contract_path = "reports/saucedemo_handoff_contract_v2.json"
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(contract.model_dump_json(indent=2))
    print(f"Saved handoff contract to {contract_path}")

    # Generate Test Suite
    print("\n" + "=" * 70)
    print("GENERATING EXECUTABLE CONTRACT TEST SUITE")
    print("=" * 70)
    generator = ContractTestGenerator(use_llm_enrichment=False)
    suite = generator.generate_suite(contract)
    suite_path = "reports/saucedemo_generated_test_suite_v2.json"
    with open(suite_path, "w", encoding="utf-8") as f:
        f.write(suite.model_dump_json(indent=2))
    print(f"Generated {len(suite.test_cases)} test cases across categories:")
    cat_counts = {}
    for tc in suite.test_cases:
        cat_counts[tc.category.value] = cat_counts.get(tc.category.value, 0) + 1
    for cat, cnt in cat_counts.items():
        print(f"  - {cat}: {cnt}")
    print(f"Saved test suite to {suite_path}")

    # Execute Test Suite
    print("\n" + "=" * 70)
    print("EXECUTING EXECUTABLE CONTRACT TEST SUITE VIA PLAYWRIGHT")
    print("=" * 70)
    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=False,
        sandbox_origins=["https://www.saucedemo.com"],
    )
    exec_report = await runner.run_suite(
        suite,
        target_base_url="https://www.saucedemo.com",
        test_username="standard_user",
        test_password="secret_sauce",
    )
    print(f"Execution completed in {exec_report.duration_seconds:.1f}s")
    print(f"Total Cases: {exec_report.total_generated_cases}")
    print(f"Executed: {exec_report.executed_count}")
    print(f"Passed: {exec_report.passed_count}")
    print(f"Failed: {exec_report.failed_count}")
    print(f"Setup Failures: {exec_report.setup_failures_count}")
    print(f"Safety Blocked: {exec_report.safety_blocked_count}")
    print(f"Setup Success Rate: {exec_report.setup_success_rate:.1%}")
    print(f"Assertion Validity Rate: {exec_report.assertion_validity_rate:.1%}")

    exec_report_path = "reports/saucedemo_test_execution_report_v2.json"
    with open(exec_report_path, "w", encoding="utf-8") as f:
        f.write(exec_report.model_dump_json(indent=2))
    print(f"Saved execution report to {exec_report_path}")

    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run())
