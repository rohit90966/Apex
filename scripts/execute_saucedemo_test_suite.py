import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.test_generation.contract_test_generator import ContractTestSuiteArtifact
from app.test_generation.test_runner import PlaywrightContractTestRunner


async def main():
    suite_path = "reports/saucedemo_generated_test_suite_v2.json"
    assert os.path.exists(suite_path), f"{suite_path} does not exist"

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = ContractTestSuiteArtifact.model_validate_json(f.read())

    print(f"Loaded test suite with {len(suite.test_cases)} test cases.")

    runner = PlaywrightContractTestRunner(
        headless=True,
        sandbox_mode=False,
        sandbox_origins=["https://www.saucedemo.com"],
    )

    report = await runner.run_suite(
        suite,
        target_base_url="https://www.saucedemo.com",
        test_username="standard_user",
        test_password="secret_sauce",
    )

    print("\n" + "=" * 60)
    print("PLAYWRIGHT CONTRACT TEST EXECUTION REPORT")
    print("=" * 60)
    print(f"Total Cases: {report.total_generated_cases}")
    print(f"Executed Cases: {report.executed_count}")
    print(f"Passed Cases: {report.passed_count}")
    print(f"Failed Cases: {report.failed_count}")
    print(f"Defect Detected: {report.defect_detected_count}")
    print(f"Setup Failures: {report.setup_failures_count}")
    print(f"Safety Blocked: {report.safety_blocked_count}")
    print(f"Skipped Hypotheses: {report.skipped_hypotheses_count}")
    print(f"Setup Success Rate: {report.setup_success_rate:.1%}")
    print(f"Assertion Validity Rate: {report.assertion_validity_rate:.1%}")

    report_path = "reports/saucedemo_test_execution_report_v2.json"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))
    print(f"\nExecution report saved to {report_path}")

    # Inspect any failures or results
    for r in report.results:
        status_str = r.status.value if hasattr(r.status, "value") else str(r.status)
        print(f"[{status_str}] {r.test_id}: {r.title} (duration: {r.duration_seconds}s)")
        if r.error_message:
            print(f"    Error: {r.error_message[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
