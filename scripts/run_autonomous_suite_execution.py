"""
Execute Autonomous Contract Test Suite against ShopSphere.
"""

import asyncio
import json
import os
import sys

# Ensure root dir is in sys.path
sys.path.insert(0, os.path.abspath("."))

from app.test_generation.contract_test_generator import ContractTestSuiteArtifact
from app.test_generation.test_runner import PlaywrightContractTestRunner


async def main():
    suite_file = "reports/shopsphere_generated_test_suite_v1.json"
    if not os.path.exists(suite_file):
        print(f"Error: Suite file {suite_file} not found.")
        return

    with open(suite_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    suite = ContractTestSuiteArtifact.model_validate(data)
    print(f"Loaded test suite: {len(suite.test_cases)} test cases from {suite_file}")

    target_url = "http://127.0.0.1:5173"
    runner = PlaywrightContractTestRunner(
        headless=True,
        default_timeout_ms=8000,
        artifacts_dir="artifacts/playwright_autonomous_runs",
        sandbox_mode=True,
        sandbox_origins=[target_url],
    )

    print(f"Executing suite against {target_url}...")
    report = await runner.run_suite(suite, target_base_url=target_url)

    output_file = "reports/shopsphere_autonomous_execution_report.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))

    print("\n--- Autonomous Execution Summary ---")
    print(f"Total Generated Cases:  {report.total_generated_cases}")
    print(f"Executable Cases:       {report.executable_without_manual_edits_count}")
    print(f"Executed Cases:         {report.executed_count}")
    print(f"Passed Cases:           {report.passed_count}")
    print(f"Failed Cases:           {report.failed_count}")
    print(f"Defect Detected Cases:  {report.defect_detected_count}")
    print(f"Setup Failures:         {report.setup_failures_count}")
    print(f"Safety Blocked:         {report.safety_blocked_count}")
    print(f"Skipped Hypotheses:     {report.skipped_hypotheses_count}")
    print(f"Setup Success Rate:     {report.setup_success_rate * 100:.1f}%")
    print(f"Assertion Validity:     {report.assertion_validity_rate * 100:.1f}%")
    print(f"Duration:               {report.duration_seconds}s")
    print(f"Report saved to:        {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
