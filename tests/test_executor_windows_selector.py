import sys
import pytest
import asyncio
from app.execution.executor import BrowserExecutor
from app.schemas import ActionStep, ActionType, ExecutionAssertion, AssertionType, ExecutionStatus


@pytest.mark.asyncio
async def test_browser_executor_direct():
    executor = BrowserExecutor(headless=True)
    steps = [
        ActionStep(
            step_number=1,
            action=ActionType.NAVIGATE,
            value="https://example.com",
            description="Navigate to example.com",
            # A meaningful oracle: 'body is visible' alone verifies nothing and is reported INCOMPLETE.
            assertion=ExecutionAssertion(
                assertion_type=AssertionType.URL_CONTAINS,
                expected_value="example.com",
                description="Verify navigation reached example.com",
            ),
        )
    ]

    result = await executor.execute_steps(
        test_id="TC-DIRECT-01",
        test_name="Direct Execution Test",
        steps=steps,
    )

    assert result.status == ExecutionStatus.PASSED
    assert result.steps_completed == 1


def test_browser_executor_from_selector_loop():
    """Simulates Uvicorn running under _WindowsSelectorEventLoop on Windows."""
    if sys.platform != "win32":
        pytest.skip("Windows-specific test for SelectorEventLoop")

    selector_loop = asyncio.SelectorEventLoop()
    asyncio.set_event_loop(selector_loop)

    async def run_under_selector():
        executor = BrowserExecutor(headless=True)
        steps = [
            ActionStep(
                step_number=1,
                action=ActionType.NAVIGATE,
                value="https://example.com",
                description="Navigate to example.com under selector loop",
                assertion=ExecutionAssertion(
                    assertion_type=AssertionType.URL_CONTAINS,
                    expected_value="example.com",
                    description="Verify navigation reached example.com",
                ),
            )
        ]
        return await executor.execute_steps(
            test_id="TC-SEL-01",
            test_name="Selector Loop Execution Test",
            steps=steps,
        )

    try:
        result = selector_loop.run_until_complete(run_under_selector())
        assert result.status == ExecutionStatus.PASSED
        assert result.steps_completed == 1
    finally:
        selector_loop.close()
