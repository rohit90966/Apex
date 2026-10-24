"""
tests/test_explorer_form_fill.py
==================================
Regression tests for the explorer's stale-element recovery fix.

Root cause:  The explorer's FILL_FIELD and SELECT_OPTION handlers previously
called page.locator().fill() without checking whether the element still exists
on the current page.  After a form submission the page transitions to a success
state (e.g. "Task Created") — the form fields are gone, so the locator times out.

Fix:  A _element_ready() pre-flight check is run before every FILL/SELECT action.
If the element is not present/visible, the explorer re-navigates to the source
state's URL to restore the form, then retries.

These tests verify the fix without requiring a live browser.
"""
import pytest
from unittest.mock import MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_explorer():
    from app.exploration.explorer import AutonomousExplorer
    return AutonomousExplorer()


def _make_mock_page(element_present: bool = True, element_disabled: bool = False):
    """Return a Playwright sync Page mock."""
    page = MagicMock()
    locator = MagicMock()

    # is_visible / is_disabled return sync values in sync Playwright
    locator.first.is_visible = MagicMock(return_value=element_present)
    locator.first.is_disabled = MagicMock(return_value=element_disabled)
    locator.first.fill = MagicMock()
    locator.first.select_option = MagicMock()

    page.locator = MagicMock(return_value=locator)
    page.url = "http://127.0.0.1:8002/create"
    page.goto = MagicMock()
    page.wait_for_timeout = MagicMock()
    return page, locator


# ── _element_ready tests ───────────────────────────────────────────────────────

class TestElementReady:
    """Unit tests for AutonomousExplorer._element_ready()"""

    def test_returns_true_when_element_visible_and_enabled(self):
        explorer = _make_explorer()
        page, locator = _make_mock_page(element_present=True, element_disabled=False)
        assert explorer._element_ready(page, "#title", timeout_ms=1000) is True

    def test_returns_false_when_element_not_visible(self):
        explorer = _make_explorer()
        page, locator = _make_mock_page(element_present=False)
        assert explorer._element_ready(page, "#title", timeout_ms=500) is False

    def test_returns_false_when_element_disabled(self):
        explorer = _make_explorer()
        page, locator = _make_mock_page(element_present=True, element_disabled=True)
        assert explorer._element_ready(page, "#title", timeout_ms=500) is False

    def test_returns_false_on_exception(self):
        explorer = _make_explorer()
        page = MagicMock()
        page.locator = MagicMock(side_effect=Exception("playwright error"))
        assert explorer._element_ready(page, "#broken", timeout_ms=500) is False

    def test_returns_false_when_is_visible_raises(self):
        explorer = _make_explorer()
        page = MagicMock()
        locator = MagicMock()
        locator.first.is_visible = MagicMock(side_effect=Exception("timeout"))
        page.locator = MagicMock(return_value=locator)
        assert explorer._element_ready(page, "#title", timeout_ms=500) is False


# ── FILL_FIELD recovery tests ──────────────────────────────────────────────────

class TestFillFieldRecovery:
    """
    Tests that FILL_FIELD re-navigates to source_state.url when element is stale
    (not present on current page) before attempting to fill.
    """

    def test_fill_field_succeeds_when_element_present(self):
        """No re-navigation needed when element is immediately ready."""
        from app.exploration.graph import (
            ExplorationActionType, InteractiveElement, ElementRole, ElementConstraints, UIState
        )
        from app.exploration.policy import ActionCandidate, ExplorationPolicy
        import hashlib

        explorer = _make_explorer()
        page, locator = _make_mock_page(element_present=True)
        policy = ExplorationPolicy()

        el = InteractiveElement(
            selector="#title",
            tag="input",
            role=ElementRole.TEXTBOX,
            element_type="text",
            accessible_name="Title",
            constraints=ElementConstraints(required=True),
        )
        source_state = UIState(
            state_id="abc123",
            url="http://127.0.0.1:8002/create",
            page_title="Create Task",
            dom_fingerprint="fp",
            interactive_elements=[el],
            depth=1,
        )
        candidate = ActionCandidate(
            neg_score=-90,
            element=el,
            action_type=ExplorationActionType.FILL_FIELD,
            suggested_value=None,
            source_state_id="abc123",
            depth=1,
        )

        explorer._execute_action(
            page, candidate, policy,
            start_url="http://127.0.0.1:8002/",
            allowed_hosts=[],
            log_fn=lambda *a, **kw: None,
            source_state=source_state,
        )

        # Should fill without any re-navigation
        page.goto.assert_not_called()
        locator.first.fill.assert_called_once()

    def test_fill_field_re_navigates_when_element_stale(self):
        """
        When the element is not on the current page (stale after submit),
        _execute_action should re-navigate to source_state.url before filling.
        """
        from app.exploration.graph import (
            ExplorationActionType, InteractiveElement, ElementRole, ElementConstraints, UIState
        )
        from app.exploration.policy import ActionCandidate, ExplorationPolicy

        explorer = _make_explorer()
        policy = ExplorationPolicy()

        # First call to is_visible returns False (element not present on current page)
        # After re-navigation, second call returns True
        page = MagicMock()
        locator = MagicMock()
        call_count = {"n": 0}

        def is_visible_side_effect(timeout):
            call_count["n"] += 1
            return call_count["n"] > 1  # False first time, True after re-nav

        locator.first.is_visible = MagicMock(side_effect=is_visible_side_effect)
        locator.first.is_disabled = MagicMock(return_value=False)
        locator.first.fill = MagicMock()
        page.locator = MagicMock(return_value=locator)
        page.goto = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.url = "http://127.0.0.1:8002/create"  # same URL, different DOM state

        el = InteractiveElement(
            selector="#title",
            tag="input",
            role=ElementRole.TEXTBOX,
            element_type="text",
            accessible_name="Title",
            constraints=ElementConstraints(required=True),
        )
        source_state = UIState(
            state_id="abc123",
            url="http://127.0.0.1:8002/create",
            page_title="Create Task",
            dom_fingerprint="fp",
            interactive_elements=[el],
            depth=1,
        )
        candidate = ActionCandidate(
            neg_score=-90,
            element=el,
            action_type=ExplorationActionType.FILL_FIELD,
            suggested_value=None,
            source_state_id="abc123",
            depth=1,
        )

        log_events = []
        explorer._execute_action(
            page, candidate, policy,
            start_url="http://127.0.0.1:8002/",
            allowed_hosts=[],
            log_fn=lambda evt, msg, **kw: log_events.append(evt),
            source_state=source_state,
        )

        # Re-navigation must have been triggered
        page.goto.assert_called_once_with(
            "http://127.0.0.1:8002/create",
            wait_until="domcontentloaded",
            timeout=10_000,
        )
        assert "RECOVERY" in log_events
        locator.first.fill.assert_called_once()

    def test_fill_field_raises_if_element_missing_after_renav(self):
        """
        If the element is still missing after re-navigation, a PlaywrightError is raised
        and the error is propagated (no silent failure).
        """
        from playwright.sync_api import Error as PlaywrightError
        from app.exploration.graph import (
            ExplorationActionType, InteractiveElement, ElementRole, ElementConstraints, UIState
        )
        from app.exploration.policy import ActionCandidate, ExplorationPolicy

        explorer = _make_explorer()
        policy = ExplorationPolicy()

        page = MagicMock()
        locator = MagicMock()
        locator.first.is_visible = MagicMock(return_value=False)   # always absent
        locator.first.is_disabled = MagicMock(return_value=False)
        page.locator = MagicMock(return_value=locator)
        page.goto = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.url = "http://127.0.0.1:8002/create"

        el = InteractiveElement(
            selector="#title",
            tag="input",
            role=ElementRole.TEXTBOX,
            element_type="text",
            accessible_name="Title",
            constraints=ElementConstraints(required=True),
        )
        source_state = UIState(
            state_id="abc123",
            url="http://127.0.0.1:8002/create",
            page_title="Create Task",
            dom_fingerprint="fp",
            interactive_elements=[el],
            depth=1,
        )
        candidate = ActionCandidate(
            neg_score=-90,
            element=el,
            action_type=ExplorationActionType.FILL_FIELD,
            suggested_value=None,
            source_state_id="abc123",
            depth=1,
        )

        with pytest.raises(PlaywrightError):
            explorer._execute_action(
                page, candidate, policy,
                start_url="http://127.0.0.1:8002/",
                allowed_hosts=[],
                log_fn=lambda *a, **kw: None,
                source_state=source_state,
            )

    def test_fill_field_no_crash_with_no_source_state(self):
        """
        When source_state is None (e.g. called without context), no re-navigation
        is attempted. If element is absent a PlaywrightError is raised, not AttributeError.
        """
        from playwright.sync_api import Error as PlaywrightError
        from app.exploration.graph import (
            ExplorationActionType, InteractiveElement, ElementRole, ElementConstraints
        )
        from app.exploration.policy import ActionCandidate, ExplorationPolicy

        explorer = _make_explorer()
        policy = ExplorationPolicy()

        page = MagicMock()
        locator = MagicMock()
        locator.first.is_visible = MagicMock(return_value=False)
        locator.first.is_disabled = MagicMock(return_value=False)
        page.locator = MagicMock(return_value=locator)
        page.goto = MagicMock()
        page.wait_for_timeout = MagicMock()
        page.url = "http://127.0.0.1:8002/create"

        el = InteractiveElement(
            selector="#title",
            tag="input",
            role=ElementRole.TEXTBOX,
            element_type="text",
            accessible_name="Title",
            constraints=ElementConstraints(required=True),
        )
        candidate = ActionCandidate(
            neg_score=-90,
            element=el,
            action_type=ExplorationActionType.FILL_FIELD,
            suggested_value=None,
            source_state_id="abc123",
            depth=1,
        )

        with pytest.raises(PlaywrightError):
            explorer._execute_action(
                page, candidate, policy,
                start_url="http://127.0.0.1:8002/",
                allowed_hosts=[],
                log_fn=lambda *a, **kw: None,
                source_state=None,   # No source state
            )
        # No re-navigation attempted
        page.goto.assert_not_called()
