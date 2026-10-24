"""
Tests for app/execution/executor.py and app/execution/runner.py
"""
import pytest
from app.schemas import (
    ActionStep,
    ActionType,
    ExecutionAssertion,
    AssertionType,
    ExecutionStatus,
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.execution.runner import QAPlanRunner


@pytest.fixture
def sample_login_tc():
    return TestCase(
        __test__=False,
        test_id="TC-LOGIN-001",
        name="Verify login form required fields",
        objective="Login with empty credentials triggers validation",
        page_category=PageCategory.LOGIN,
        relevant_elements=["#username", "#password"],
        action_description="Leave both fields empty and click login",
        expected_outcome="Form shows validation error",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Required field validation is fundamental",
        confidence=0.9,
    )


@pytest.fixture
def sample_registration_tc():
    return TestCase(
        __test__=False,
        test_id="TC-REG-001",
        name="Verify password mismatch rejection",
        objective="Register with mismatching passwords is rejected",
        page_category=PageCategory.REGISTRATION,
        relevant_elements=["#reg-password", "#confirm-password"],
        action_description="Fill passwords with different values and submit",
        expected_outcome="Error: passwords do not match",
        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Password confirmation is a security control",
        confidence=0.95,
    )


@pytest.fixture
def sample_search_tc():
    return TestCase(
        __test__=False,
        test_id="TC-SEARCH-001",
        name="Verify search with valid keyword",
        objective="Search returns results for a valid query",
        page_category=PageCategory.SEARCH,
        relevant_elements=["input#q"],
        action_description="Enter 'Microservices' and submit search",
        expected_outcome="Results are displayed",
        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
        priority=Priority.MEDIUM,
        selection_reason="Core search functionality must work",
        confidence=0.8,
    )


def test_runner_instantiation():
    runner = QAPlanRunner()
    assert runner is not None
    assert runner.executor is not None


def test_synthesize_steps_login(sample_login_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_login_tc, "http://127.0.0.1:8001/login")
    assert len(steps) >= 1
    # First step should navigate
    assert steps[0].action == ActionType.NAVIGATE
    assert "login" in steps[0].value.lower()


def test_synthesize_steps_registration(sample_registration_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_registration_tc, "http://127.0.0.1:8001/registration")
    assert len(steps) >= 3
    actions = [s.action for s in steps]
    assert ActionType.NAVIGATE in actions
    assert ActionType.FILL in actions


def test_synthesize_steps_search(sample_search_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_search_tc, "http://127.0.0.1:8001/search")
    assert len(steps) >= 2
    actions = [s.action for s in steps]
    assert ActionType.NAVIGATE in actions
    assert ActionType.FILL in actions
    # Verify click with assertion is the last step
    last_step = steps[-1]
    assert last_step.action == ActionType.CLICK
    assert last_step.assertion is not None


def test_step_numbers_sequential(sample_login_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_login_tc, "http://127.0.0.1:8001/login")
    for i, step in enumerate(steps, 1):
        assert step.step_number == i


def test_action_step_structure(sample_login_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_login_tc, "http://127.0.0.1:8001/login")
    for step in steps:
        assert isinstance(step, ActionStep)
        assert step.description is not None
        assert len(step.description) > 0


def test_assertion_fields_valid(sample_search_tc):
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_search_tc, "http://127.0.0.1:8001/search")
    steps_with_assertions = [s for s in steps if s.assertion is not None]
    assert len(steps_with_assertions) >= 1
    for step in steps_with_assertions:
        assert isinstance(step.assertion, ExecutionAssertion)
        assert step.assertion.assertion_type in AssertionType.__members__.values()


def test_synthesize_steps_login_uses_validity_value_missing(sample_login_tc):
    """Empty credentials test must assert VALIDITY_VALUE_MISSING, not fragile TEXT_VISIBLE 'required'."""
    runner = QAPlanRunner()
    steps = runner.synthesize_steps(sample_login_tc, "http://127.0.0.1:8001/login")
    last_step = steps[-1]
    assert last_step.assertion is not None
    assert last_step.assertion.assertion_type == AssertionType.VALIDITY_VALUE_MISSING
    assert "missing username" in last_step.assertion.description.lower() or "validity" in last_step.assertion.description.lower()


@pytest.mark.asyncio
async def test_executor_evaluates_validity_value_missing():
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value={
        "passed": True,
        "el_validity": {"valueMissing": True, "valid": False, "validationMessage": "Please fill out this field."},
        "missing_required_fields": ["username"],
        "visible_errors": [],
        "invalid_count": 1,
        "url": "http://127.0.0.1:8001/login",
    })

    assertion = ExecutionAssertion(
        assertion_type=AssertionType.VALIDITY_VALUE_MISSING,
        target_selector="input#username",
        expected_value="valueMissing",
        description="Verify valueMissing",
    )
    passed, actual, err = await executor._verify_assertion(mock_page, assertion, timeout_ms=1000)
    assert passed is True
    # The executor's reason string includes the valueMissing confirmation
    assert "valueMissing=true" in actual or "Validation confirmed" in actual
    assert err is None


@pytest.mark.asyncio
async def test_executor_text_visible_html5_fallback():
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()
    # get_by_text returns invisible
    mock_locator = MagicMock()
    mock_locator.get_by_text.return_value.is_visible = AsyncMock(return_value=False)
    mock_page.locator.return_value = mock_locator
    # But HTML5 validation is active
    mock_page.evaluate = AsyncMock(return_value=True)

    assertion = ExecutionAssertion(
        assertion_type=AssertionType.TEXT_VISIBLE,
        expected_value="required",
        description="Verify required message",
    )
    passed, actual, err = await executor._verify_assertion(mock_page, assertion, timeout_ms=1000)
    # Resilient fallback passes because browser HTML5 validity prevented submission
    assert passed is True
    assert "HTML5 browser form validation prevented submission" in actual


# ── SET_VALUE_JS action tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_executor_set_value_js_single_selector():
    """SET_VALUE_JS dispatches JS to set value on a single selector."""
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value=None)

    step = ActionStep(
        step_number=1,
        action=ActionType.SET_VALUE_JS,
        selector="input[name='password']",
        value="a",
        description="Set 1-char password bypassing browser validation",
    )
    # Should not raise
    await executor._execute_action(mock_page, step, timeout_ms=1000)
    mock_page.evaluate.assert_called_once()
    # Verify the selector list was passed (single item)
    call_args = mock_page.evaluate.call_args
    assert call_args is not None
    passed_args = call_args[0][1]  # second positional arg = [selectors, value]
    assert passed_args[0] == ["input[name='password']"]
    assert passed_args[1] == "a"


@pytest.mark.asyncio
async def test_executor_set_value_js_multi_selector():
    """SET_VALUE_JS tries each selector in a comma-separated list."""
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value=None)

    step = ActionStep(
        step_number=1,
        action=ActionType.SET_VALUE_JS,
        selector="#username, input[name='email'], input[type='text']",
        value="test@example.com",
        description="Set username with fallback selectors",
    )
    await executor._execute_action(mock_page, step, timeout_ms=1000)
    mock_page.evaluate.assert_called_once()
    passed_args = mock_page.evaluate.call_args[0][1]
    assert len(passed_args[0]) == 3
    assert "#username" in passed_args[0]


@pytest.mark.asyncio
async def test_executor_set_value_js_requires_selector():
    """SET_VALUE_JS raises ValueError if selector is missing."""
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()

    step = ActionStep(
        step_number=1,
        action=ActionType.SET_VALUE_JS,
        selector=None,
        value="test",
        description="Missing selector",
    )
    with pytest.raises(ValueError, match="selector"):
        await executor._execute_action(mock_page, step, timeout_ms=1000)


@pytest.mark.asyncio
async def test_executor_set_value_js_requires_value():
    """SET_VALUE_JS raises ValueError if value is missing."""
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()

    step = ActionStep(
        step_number=1,
        action=ActionType.SET_VALUE_JS,
        selector="#password",
        value=None,
        description="Missing value",
    )
    with pytest.raises(ValueError, match="value"):
        await executor._execute_action(mock_page, step, timeout_ms=1000)


@pytest.mark.asyncio
async def test_executor_observe_state_is_noop():
    """OBSERVE_STATE performs no browser interaction (read-only placeholder)."""
    from unittest.mock import AsyncMock, MagicMock
    from app.execution.executor import BrowserExecutor

    executor = BrowserExecutor()
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock()

    step = ActionStep(
        step_number=1,
        action=ActionType.OBSERVE_STATE,
        description="Capture state before assertion",
    )
    # Should complete without any call to page methods
    await executor._execute_action(mock_page, step, timeout_ms=1000)
    mock_page.evaluate.assert_not_called()
    mock_page.goto.assert_not_called()
