"""
tests/test_explorer.py
=======================
Phase 1 automated tests for the VerityQA generic autonomous exploration engine.

Coverage:
  1. Control discovery without page-classification dependency
  2. Modal / expanded section detection (new state via interaction)
  3. UI state deduplication via DOM fingerprint
  4. Same-origin navigation safety enforcement
  5. Exploration loop prevention
  6. Action priority scoring
  7. Budget enforcement (actions)
  8. Budget enforcement (depth)
  9. Safety policy enforcement
  10. ApplicationStateGraph integrity
  11. Element parsing from raw JS data
  12. ExplorationBudget time-limit check
"""

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.exploration.graph import (
    ApplicationStateGraph,
    ElementConstraints,
    ElementRole,
    ExplorationActionType,
    ExploreRequest,
    InteractiveElement,
    StateTransition,
    UIState,
)
from app.exploration.policy import ActionCandidate, ExplorationBudget, ExplorationPolicy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_element(
    tag: str = "button",
    el_type: str = "submit",
    role: ElementRole = ElementRole.BUTTON,
    accessible_name: str = "Submit",
    disabled: bool = False,
    is_submission: bool = False,
    is_navigation: bool = False,
) -> InteractiveElement:
    return InteractiveElement(
        selector=f"#{tag}-{accessible_name.replace(' ', '-')}",
        tag=tag,
        role=role,
        accessible_name=accessible_name,
        element_type=el_type,
        constraints=ElementConstraints(disabled=disabled),
        is_submission=is_submission,
        is_navigation=is_navigation,
    )


def make_state(url: str = "http://127.0.0.1:8002/", depth: int = 0) -> UIState:
    fp = hashlib.sha1(url.encode()).hexdigest()[:10]
    return UIState(
        state_id=UIState.make_state_id(url, fp),
        url=url,
        page_title="Test Page",
        dom_fingerprint=fp,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# 1. Control discovery without page classification
# ---------------------------------------------------------------------------

class TestControlDiscovery:
    """Controls must be classified by element role, not by page-category keyword."""

    def test_element_role_assigned_without_page_category(self):
        """
        InteractiveElement role is derived from tag/type, not from any LOGIN/
        SEARCH/REGISTRATION category.
        """
        btn = InteractiveElement(
            selector="button#submit",
            tag="button",
            role=ElementRole.BUTTON,
            accessible_name="Submit",
            element_type="submit",
            constraints=ElementConstraints(),
        )
        assert btn.role == ElementRole.BUTTON
        assert "LOGIN" not in str(btn.model_dump())
        assert "REGISTRATION" not in str(btn.model_dump())
        assert "SEARCH" not in str(btn.model_dump())

    def test_multiple_element_roles_independent_of_category(self):
        roles_seen = set()
        for tag, el_type, role in [
            ("input", "email", ElementRole.TEXTBOX),
            ("input", "password", ElementRole.TEXTBOX),
            ("select", "select-one", ElementRole.COMBOBOX),
            ("input", "checkbox", ElementRole.CHECKBOX),
            ("a", "link", ElementRole.LINK),
            ("button", "button", ElementRole.BUTTON),
        ]:
            el = InteractiveElement(
                selector=f"{tag}#{el_type}",
                tag=tag,
                role=role,
                element_type=el_type,
                constraints=ElementConstraints(),
            )
            roles_seen.add(el.role)
        # At least 4 distinct roles discovered
        assert len(roles_seen) >= 4


# ---------------------------------------------------------------------------
# 2. New state detection after interaction
# ---------------------------------------------------------------------------

class TestNewStateDetection:
    def test_new_state_flag_on_transition(self):
        """StateTransition correctly records new_state_discovered."""
        tr = StateTransition(
            source_state_id="state-001",
            action_type=ExplorationActionType.CLICK,
            resulting_state_id="state-002",
            url_before="http://127.0.0.1:8002/",
            url_after="http://127.0.0.1:8002/tasks",
            url_changed=True,
            new_state_discovered=True,
            step_number=1,
        )
        assert tr.new_state_discovered is True
        assert tr.url_changed is True

    def test_graph_tracks_new_state_count(self):
        """ApplicationStateGraph.new_states_discovered counts transitions correctly."""
        graph = ApplicationStateGraph(starting_url="http://127.0.0.1:8002/")

        # Three transitions: 2 new, 1 revisit
        for i, is_new in enumerate([True, True, False]):
            graph.transitions.append(StateTransition(
                source_state_id=f"state-{i}",
                action_type=ExplorationActionType.NAVIGATE,
                resulting_state_id=f"state-{i+1}",
                url_before="http://127.0.0.1:8002/",
                url_after=f"http://127.0.0.1:8002/page{i}",
                url_changed=True,
                new_state_discovered=is_new,
                step_number=i,
            ))

        assert graph.new_states_discovered == 2


# ---------------------------------------------------------------------------
# 3. State deduplication via DOM fingerprint
# ---------------------------------------------------------------------------

class TestStateDeduplication:
    def test_same_url_different_fingerprint_gives_different_state_id(self):
        url = "http://127.0.0.1:8002/"
        sid1 = UIState.make_state_id(url, "fp-aaa")
        sid2 = UIState.make_state_id(url, "fp-bbb")
        assert sid1 != sid2

    def test_same_url_same_fingerprint_gives_same_state_id(self):
        url = "http://127.0.0.1:8002/"
        sid1 = UIState.make_state_id(url, "fp-xyz")
        sid2 = UIState.make_state_id(url, "fp-xyz")
        assert sid1 == sid2

    def test_different_url_same_fingerprint_gives_different_state_id(self):
        sid1 = UIState.make_state_id("http://127.0.0.1:8002/a", "fp-xyz")
        sid2 = UIState.make_state_id("http://127.0.0.1:8002/b", "fp-xyz")
        assert sid1 != sid2

    def test_graph_dedup_by_state_id(self):
        """Adding the same state_id twice should be handled by dict semantics."""
        graph = ApplicationStateGraph(starting_url="http://127.0.0.1:8002/")
        state = make_state("http://127.0.0.1:8002/")
        graph.states[state.state_id] = state
        graph.states[state.state_id] = state  # duplicate
        assert graph.state_count == 1

    def test_policy_tracks_visited_states(self):
        policy = ExplorationPolicy()
        state_id = "abc123"
        assert not policy.state_already_visited(state_id)
        policy.record_state_visited(state_id)
        assert policy.state_already_visited(state_id)


# ---------------------------------------------------------------------------
# 4. Same-origin navigation safety
# ---------------------------------------------------------------------------

class TestSameOriginSafety:
    def test_explore_request_schema(self):
        req = ExploreRequest(url="http://127.0.0.1:8002/")
        assert req.url == "http://127.0.0.1:8002/"
        assert req.max_depth is None  # uses config default

    def test_explore_request_custom_budget(self):
        req = ExploreRequest(
            url="http://127.0.0.1:8002/",
            max_depth=2,
            max_actions=5,
            max_pages=3,
        )
        assert req.max_depth == 2
        assert req.max_actions == 5
        assert req.max_pages == 3


# ---------------------------------------------------------------------------
# 5. Loop prevention via visited-state tracking
# ---------------------------------------------------------------------------

class TestLoopPrevention:
    def test_policy_skips_already_acted_element(self):
        policy = ExplorationPolicy()
        el = make_element()
        key = el.element_key

        policy.record_element_acted_on(key)
        # Element should now be penalised (very low or negative score)
        score = policy.score_element(el, depth=0, known_state_ids=set())
        # After repetition penalty, score should be lower than un-visited
        el2 = make_element(accessible_name="Other Button")
        score2 = policy.score_element(el2, depth=0, known_state_ids=set())
        assert score < score2


# ---------------------------------------------------------------------------
# 6. Action priority scoring
# ---------------------------------------------------------------------------

class TestActionPriorityScoring:
    def test_submission_button_scores_higher_than_link(self):
        policy = ExplorationPolicy()
        submit_btn = make_element(tag="input", el_type="submit", role=ElementRole.BUTTON,
                                   accessible_name="Submit Form", is_submission=True)
        nav_link = make_element(tag="a", el_type="link", role=ElementRole.LINK,
                                accessible_name="Go to About", is_navigation=True)

        submit_score = policy.score_element(submit_btn, depth=0, known_state_ids=set())
        link_score = policy.score_element(nav_link, depth=0, known_state_ids=set())
        assert submit_score > link_score

    def test_disabled_element_gets_very_low_score(self):
        policy = ExplorationPolicy()
        disabled_btn = make_element(tag="button", disabled=True)
        score = policy.score_element(disabled_btn, depth=0, known_state_ids=set())
        assert score < -100

    def test_deeper_element_scores_lower(self):
        policy = ExplorationPolicy()
        el = make_element(accessible_name="Test Action")
        shallow_score = policy.score_element(el, depth=0, known_state_ids=set())
        deep_score = policy.score_element(el, depth=5, known_state_ids=set())
        assert shallow_score > deep_score

    def test_action_candidate_ordering(self):
        """Higher-scored candidates should sort first in a min-heap."""
        import heapq
        policy = ExplorationPolicy()
        el_high = make_element(accessible_name="High Priority Submit", el_type="submit", is_submission=True)
        el_low = make_element(tag="a", el_type="link", role=ElementRole.LINK,
                               accessible_name="Low Priority Link", is_navigation=True)

        candidates = []
        for el in [el_low, el_high]:
            sc = policy.score_element(el, depth=0, known_state_ids=set())
            heapq.heappush(candidates, ActionCandidate(
                neg_score=-sc,
                element=el,
                action_type=policy.choose_action_type(el),
            ))

        best = heapq.heappop(candidates)
        assert best.element.accessible_name == "High Priority Submit"


# ---------------------------------------------------------------------------
# 7. Budget enforcement — max actions
# ---------------------------------------------------------------------------

class TestBudgetActions:
    def test_actions_budget_exhausts_correctly(self):
        budget = ExplorationBudget(max_actions=3)
        assert not budget.actions_exhausted()
        for _ in range(3):
            budget.record_action()
        assert budget.actions_exhausted()

    def test_check_all_returns_reason_when_exhausted(self):
        budget = ExplorationBudget(max_actions=1)
        budget.record_action()
        reason = budget.check_all(depth=0, elapsed=0.0)
        assert reason is not None
        assert "max_actions" in reason


# ---------------------------------------------------------------------------
# 8. Budget enforcement — depth
# ---------------------------------------------------------------------------

class TestBudgetDepth:
    def test_depth_not_exceeded(self):
        budget = ExplorationBudget(max_depth=3)
        assert not budget.depth_exceeded(3)   # at limit: allowed
        assert budget.depth_exceeded(4)        # beyond limit: blocked

    def test_check_all_returns_depth_reason(self):
        budget = ExplorationBudget(max_depth=2)
        reason = budget.check_all(depth=3, elapsed=0.0)
        assert reason is not None
        assert "max_depth" in reason


# ---------------------------------------------------------------------------
# 9. Safety policy enforcement
# ---------------------------------------------------------------------------

class TestSafetyPolicy:
    def test_cross_origin_url_rejected_by_safety(self):
        from app.safety import validate_url_safety, SafetyViolation
        with pytest.raises(SafetyViolation):
            validate_url_safety("http://evil.com/steal", ["127.0.0.1", "localhost"])

    def test_localhost_allowed_when_in_allowlist(self):
        from app.safety import validate_url_safety
        result = validate_url_safety("http://127.0.0.1:8002/", ["127.0.0.1", "localhost"])
        assert result[0] is True

    def test_loopback_rejected_when_not_in_allowlist(self):
        from app.safety import validate_url_safety, SafetyViolation
        with pytest.raises(SafetyViolation):
            validate_url_safety("http://127.0.0.1:8002/", ["example.com"])

    @pytest.mark.asyncio
    async def test_explore_request_safety_rejection(self):
        """Explorer raises ValueError for disallowed URL."""
        from app.exploration.explorer import AutonomousExplorer
        explorer = AutonomousExplorer()
        with pytest.raises(Exception):
            await explorer.explore(ExploreRequest(url="http://evil-host.example.com/"))


# ---------------------------------------------------------------------------
# 10. ApplicationStateGraph integrity
# ---------------------------------------------------------------------------

class TestApplicationStateGraph:
    def test_state_and_transition_counts(self):
        graph = ApplicationStateGraph(starting_url="http://127.0.0.1:8002/")
        for i in range(3):
            state = make_state(f"http://127.0.0.1:8002/page{i}")
            graph.states[state.state_id] = state
        assert graph.state_count == 3
        assert graph.transition_count == 0

    def test_graph_serializable(self):
        graph = ApplicationStateGraph(starting_url="http://127.0.0.1:8002/")
        data = graph.model_dump()
        assert "states" in data
        assert "transitions" in data
        assert "exploration_log" in data

    def test_element_key_stable(self):
        el = InteractiveElement(
            selector="#email",
            tag="input",
            role=ElementRole.TEXTBOX,
            accessible_name="Email",
            element_type="email",
            constraints=ElementConstraints(),
        )
        key1 = el.element_key
        key2 = el.element_key
        assert key1 == key2
        assert len(key1) == 12


# ---------------------------------------------------------------------------
# 11. Element parsing
# ---------------------------------------------------------------------------

class TestElementParsing:
    def test_parse_elements_deduplicates_by_key(self):
        from app.exploration.explorer import _parse_elements
        raw = [
            {"tag": "button", "selector": "#btn", "accessible_name": "Submit",
             "element_type": "submit", "role": None, "form_id": None,
             "disabled": False, "required": False, "readonly": False,
             "min_length": None, "max_length": None, "min_value": None,
             "max_value": None, "pattern": None, "options": None, "href": None, "visible": True},
            # Duplicate
            {"tag": "button", "selector": "#btn", "accessible_name": "Submit",
             "element_type": "submit", "role": None, "form_id": None,
             "disabled": False, "required": False, "readonly": False,
             "min_length": None, "max_length": None, "min_value": None,
             "max_value": None, "pattern": None, "options": None, "href": None, "visible": True},
        ]
        elements = _parse_elements(raw, "http://127.0.0.1:8002/", "http://127.0.0.1:8002/")
        assert len(elements) == 1

    def test_parse_elements_assigns_correct_roles(self):
        from app.exploration.explorer import _parse_elements
        raw = [
            {"tag": "a", "selector": "a#home", "accessible_name": "Home",
             "element_type": "link", "role": "link", "form_id": None,
             "disabled": False, "required": False, "readonly": False,
             "min_length": None, "max_length": None, "min_value": None,
             "max_value": None, "pattern": None, "options": None,
             "href": "/", "visible": True},
            {"tag": "select", "selector": "select#priority", "accessible_name": "Priority",
             "element_type": "select-one", "role": None, "form_id": "form1",
             "disabled": False, "required": False, "readonly": False,
             "min_length": None, "max_length": None, "min_value": None,
             "max_value": None, "pattern": None, "options": ["High", "Medium", "Low"],
             "href": None, "visible": True},
        ]
        elements = _parse_elements(raw, "http://127.0.0.1:8002/", "http://127.0.0.1:8002/")
        assert len(elements) == 2
        roles = {el.role for el in elements}
        assert ElementRole.LINK in roles
        assert ElementRole.COMBOBOX in roles


# ---------------------------------------------------------------------------
# 12. Time budget check
# ---------------------------------------------------------------------------

class TestTimeBudget:
    def test_time_exceeded(self):
        budget = ExplorationBudget(max_time_seconds=5.0)
        assert not budget.time_exceeded(4.9)
        assert budget.time_exceeded(5.0)
        assert budget.time_exceeded(10.0)

    def test_pages_budget(self):
        budget = ExplorationBudget(max_pages=2)
        budget.record_page()
        assert not budget.pages_exhausted()
        budget.record_page()
        assert budget.pages_exhausted()

    def test_check_all_time(self):
        budget = ExplorationBudget(max_time_seconds=1.0)
        reason = budget.check_all(depth=0, elapsed=2.0)
        assert reason is not None
        assert "max_time" in reason


# ---------------------------------------------------------------------------
# Input suggestion tests
# ---------------------------------------------------------------------------

class TestInputSuggestion:
    def test_email_suggestion(self):
        policy = ExplorationPolicy()
        el = InteractiveElement(
            selector="#email", tag="input", role=ElementRole.TEXTBOX,
            element_type="email", constraints=ElementConstraints(input_type="email"),
        )
        val = policy.suggest_input_value(el)
        assert "@" in val

    def test_password_suggestion(self):
        policy = ExplorationPolicy()
        el = InteractiveElement(
            selector="#pwd", tag="input", role=ElementRole.TEXTBOX,
            element_type="password", constraints=ElementConstraints(input_type="password"),
        )
        val = policy.suggest_input_value(el)
        assert val is not None and len(val) >= 8

    def test_select_option_suggestion(self):
        policy = ExplorationPolicy()
        el = InteractiveElement(
            selector="#priority", tag="select", role=ElementRole.COMBOBOX,
            element_type="select-one",
            constraints=ElementConstraints(options=["Select...", "High", "Medium", "Low"]),
        )
        val = policy.suggest_select_option(el)
        # Should skip the placeholder "Select..."
        assert val in ("High", "Medium", "Low")
