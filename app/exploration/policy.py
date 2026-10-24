"""
app/exploration/policy.py
=========================
Adaptive exploration policy for VerityQA autonomous exploration.

Provides:
  - ExplorationBudget  — configurable limits (depth, actions, pages, time)
  - ActionCandidate    — a scored candidate action to take next
  - ExplorationPolicy  — priority-queue–based scoring and selection

Scoring formula (higher = preferred):
  score = novelty_weight   * novelty_score
        + risk_weight      * risk_score
        + coverage_weight  * coverage_gain
        - repetition_penalty * repetitions_so_far
        - depth_penalty      * depth

No page-category templates are used.  Elements are scored purely on
structural and semantic signals extracted from the DOM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from app.exploration.graph import (
    ExplorationActionType,
    InteractiveElement,
)

logger = logging.getLogger("verityqa.exploration.policy")


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

class ExplorationBudget:
    """
    Configurable exploration limits.  All limits are hard-stops; the
    engine will not exceed any of them.
    """

    def __init__(
        self,
        max_depth: int = 3,
        max_actions: int = 30,
        max_pages: int = 10,
        max_time_seconds: float = 120.0,
    ):
        self.max_depth = max_depth
        self.max_actions = max_actions
        self.max_pages = max_pages
        self.max_time_seconds = max_time_seconds

        # Runtime counters
        self._actions_taken: int = 0
        self._pages_visited: int = 0

    def record_action(self) -> None:
        self._actions_taken += 1

    def record_page(self) -> None:
        self._pages_visited += 1

    def actions_exhausted(self) -> bool:
        return self._actions_taken >= self.max_actions

    def pages_exhausted(self) -> bool:
        return self._pages_visited >= self.max_pages

    def depth_exceeded(self, depth: int) -> bool:
        return depth > self.max_depth

    def time_exceeded(self, elapsed_seconds: float) -> bool:
        return elapsed_seconds >= self.max_time_seconds

    def check_all(self, depth: int, elapsed: float) -> Optional[str]:
        """Return reason string if any budget is exceeded, else None."""
        if self.actions_exhausted():
            return f"max_actions={self.max_actions} reached"
        if self.pages_exhausted():
            return f"max_pages={self.max_pages} reached"
        if self.depth_exceeded(depth):
            return f"max_depth={self.max_depth} exceeded at depth={depth}"
        if self.time_exceeded(elapsed):
            return f"max_time_seconds={self.max_time_seconds:.0f}s exceeded"
        return None

    @property
    def actions_taken(self) -> int:
        return self._actions_taken

    @property
    def pages_visited(self) -> int:
        return self._pages_visited


# ---------------------------------------------------------------------------
# Candidate action
# ---------------------------------------------------------------------------

@dataclass(order=True)
class ActionCandidate:
    """
    A candidate action to be taken during exploration.

    `score` is stored as a negative value so that Python's heapq (min-heap)
    pops the *highest-scoring* candidate first.
    """
    neg_score: float = field(compare=True)      # -score for min-heap ordering
    element: InteractiveElement = field(compare=False)
    action_type: ExplorationActionType = field(compare=False)
    suggested_value: Optional[str] = field(default=None, compare=False)
    source_state_id: str = field(default="", compare=False)
    depth: int = field(default=0, compare=False)

    @property
    def score(self) -> float:
        return -self.neg_score


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class ExplorationPolicy:
    """
    Scores and selects the next action to take during autonomous exploration.

    Design principles:
    - No page-category templates — decisions are based solely on element
      structure and exploration state.
    - Novelty: prefer actions leading to states not yet visited.
    - Risk: prefer form submissions and interactive inputs over nav links
      (they're more likely to reveal behaviour to test).
    - Loop detection: penalise elements already acted on in this session.
    """

    # Configurable scoring weights
    NOVELTY_WEIGHT = 2.0
    RISK_WEIGHT = 1.5
    COVERAGE_WEIGHT = 1.0
    REPETITION_PENALTY = 3.0
    DEPTH_PENALTY = 0.5

    # Per-element-type base risk scores
    _RISK_SCORES: Dict[str, float] = {
        "submit": 3.0,
        "button": 2.5,
        "text": 2.0,
        "email": 2.0,
        "password": 2.0,
        "number": 1.8,
        "search": 1.8,
        "select-one": 1.5,
        "select": 1.5,
        "combobox": 1.5,
        "checkbox": 1.2,
        "radio": 1.2,
        "link": 0.8,
        "a": 0.8,
    }

    def __init__(self) -> None:
        self._visited_element_keys: Set[str] = set()
        self._visited_state_ids: Set[str] = set()

    def record_state_visited(self, state_id: str) -> None:
        self._visited_state_ids.add(state_id)

    def record_element_acted_on(self, element_key: str) -> None:
        self._visited_element_keys.add(element_key)

    def state_already_visited(self, state_id: str) -> bool:
        return state_id in self._visited_state_ids

    def score_element(
        self,
        element: InteractiveElement,
        current_depth: int = 0,
        known_state_ids: Optional[Set[str]] = None,
        depth: Optional[int] = None,
    ) -> float:
        """Compute a priority score for acting on *element*."""
        if depth is not None:
            current_depth = depth
        key = element.element_key
        repetitions = 1 if key in self._visited_element_keys else 0

        # Novelty: we don't yet know whether clicking leads to a new state,
        # so we use a proxy: prefer elements we haven't acted on yet.
        novelty = 1.0 if repetitions == 0 else 0.0

        # Risk: structural likelihood of revealing interesting behaviour
        el_type = (element.element_type or element.role.value or "").lower()
        risk = self._RISK_SCORES.get(el_type, 1.0)
        # Submission actions get a bonus
        if element.is_submission:
            risk = max(risk, 3.0)
        # Pure nav links are deprioritised but not excluded
        if element.is_navigation:
            risk = min(risk, 0.9)
        # Disabled elements — no point
        if element.constraints.disabled:
            return -999.0

        # Coverage gain: prefer elements that could expand the state space
        coverage = 1.2 if element.is_submission else 0.8

        score = (
            self.NOVELTY_WEIGHT * novelty
            + self.RISK_WEIGHT * risk
            + self.COVERAGE_WEIGHT * coverage
            - self.REPETITION_PENALTY * repetitions
            - self.DEPTH_PENALTY * current_depth
        )
        return score

    def choose_action_type(self, element: InteractiveElement) -> ExplorationActionType:
        """Decide which action type to perform on an element."""
        tag = element.tag.lower()
        el_type = (element.element_type or "").lower()
        role = element.role.value.lower()

        if tag == "a" or role == "link":
            return ExplorationActionType.NAVIGATE
        if tag == "select" or role == "combobox":
            return ExplorationActionType.SELECT_OPTION
        if el_type in ("text", "email", "password", "search", "number", "tel", "url", "date"):
            return ExplorationActionType.FILL_FIELD
        if el_type == "submit" or element.is_submission:
            return ExplorationActionType.CLICK
        return ExplorationActionType.CLICK

    def suggest_input_value(self, element: InteractiveElement) -> Optional[str]:
        """
        Produce a safe, representative test value for a fill action.
        The value is chosen to be syntactically valid and boundary-aware
        without depending on any hardcoded page workflow.
        """
        el_type = (element.element_type or "").lower()
        name = (element.accessible_name or element.constraints.input_type or "").lower()
        c = element.constraints

        if el_type == "email":
            return "test.explorer@verityqa.local"
        if el_type == "password":
            # Use a value that satisfies typical 8-char minimums
            return "ExploreQA1!"
        if el_type == "number":
            if c.min_value:
                return c.min_value
            return "1"
        if el_type == "date":
            return "2024-01-15"
        if el_type == "search":
            return "test"
        if "phone" in name or "tel" in name:
            return "+1234567890"
        if "url" in name:
            return "https://example.com"
        if c.max_length and c.max_length < 20:
            # Produce value at boundary
            return "A" * min(c.max_length, 10)
        # Generic text
        if "name" in name:
            return "QA Explorer"
        if "message" in name or "comment" in name or "description" in name:
            return "Automated exploration test input."
        if el_type == "textarea":
            return "Automated exploration test input."
        return "verityqa_test"

    def suggest_select_option(self, element: InteractiveElement) -> Optional[str]:
        """Return the first non-empty option value for a <select>."""
        opts = element.constraints.options or []
        for opt in opts:
            stripped = opt.strip()
            clean_lower = stripped.lower().rstrip(".").rstrip()
            if stripped and clean_lower not in ("", "select", "choose", "-- select --", "please select"):
                return stripped
        return None
