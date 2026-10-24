"""
app/exploration/graph.py
========================
Application State Graph models for VerityQA autonomous exploration.

These models capture pages, UI states, interactive elements, and
state transitions discovered during autonomous browser exploration.
They are entirely independent of page-category templates (no LOGIN/
SEARCH/REGISTRATION required — these categories may still be detected
as optional metadata but they do NOT drive exploration).
"""

from __future__ import annotations

import hashlib
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Element-level models
# ---------------------------------------------------------------------------

class ElementRole(str, Enum):
    """ARIA/semantic role of a discovered interactive element."""
    BUTTON = "button"
    LINK = "link"
    TEXTBOX = "textbox"
    COMBOBOX = "combobox"  # <select>
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SEARCH = "search"
    SPINBUTTON = "spinbutton"
    SLIDER = "slider"
    SWITCH = "switch"
    TAB = "tab"
    MENUITEM = "menuitem"
    OPTION = "option"
    GENERIC_INTERACTIVE = "generic_interactive"


class ElementConstraints(BaseModel):
    """HTML / ARIA constraints observed on an interactive element."""
    required: bool = False
    disabled: bool = False
    readonly: bool = False
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    pattern: Optional[str] = None
    input_type: Optional[str] = None        # text, email, password, number, date …
    options: Optional[List[str]] = None     # for <select>


class InteractiveElement(BaseModel):
    """
    A single interactive DOM element discovered on a page.

    The `element_key` is a stable fingerprint derived from the selector and
    the accessible name so that the same element can be recognised across
    multiple visits to the same page.
    """
    element_id: str = Field(default_factory=lambda: f"el-{uuid.uuid4().hex[:8]}")
    selector: str                             # CSS selector that reliably locates this element
    tag: str                                  # raw HTML tag (button, input, a, select, …)
    role: ElementRole = ElementRole.GENERIC_INTERACTIVE
    accessible_name: Optional[str] = None     # innerText / aria-label / placeholder
    element_type: Optional[str] = None        # input type if <input>
    form_id: Optional[str] = None            # parent form id if inside a form
    constraints: ElementConstraints = Field(default_factory=ElementConstraints)
    # Exploration scoring hints
    is_navigation: bool = False              # True if this likely navigates away (links, nav menus)
    is_submission: bool = False              # True if this submits data (submit buttons)

    @property
    def element_key(self) -> str:
        """Stable fingerprint: hash of selector + accessible_name + tag."""
        raw = f"{self.tag}:{self.selector}:{self.accessible_name or ''}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# State-level models
# ---------------------------------------------------------------------------

class UIState(BaseModel):
    """
    A snapshot of the application at a particular moment:
    a specific URL with a specific set of visible interactive elements.

    Two states are considered *identical* if they share the same `state_id`,
    which is derived from (url, dom_fingerprint).  This enables reliable
    loop detection without expensive DOM diffing.
    """
    state_id: str                         # fingerprint: sha1(url + dom_fingerprint)[:16]
    url: str
    page_title: str = ""
    dom_fingerprint: str                  # short hash of key DOM structure
    interactive_elements: List[InteractiveElement] = Field(default_factory=list)
    screenshot_artifact_id: Optional[str] = None
    depth: int = 0                        # exploration depth from starting state
    discovered_at_step: int = 0           # action count when this state was first reached
    # Optional metadata — NOT used for routing decisions
    page_category_hint: Optional[str] = None   # LOGIN/SEARCH/GENERAL etc. for reporting only

    @classmethod
    def make_state_id(cls, url: str, dom_fingerprint: str) -> str:
        raw = f"{url}::{dom_fingerprint}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def element_count(self) -> int:
        return len(self.interactive_elements)


# ---------------------------------------------------------------------------
# Transition-level models
# ---------------------------------------------------------------------------

class ExplorationActionType(str, Enum):
    """Types of actions the exploration engine can perform."""
    NAVIGATE = "NAVIGATE"          # direct URL navigation
    CLICK = "CLICK"                # click a button, link, or other control
    FILL_AND_SUBMIT = "FILL_AND_SUBMIT"   # fill a form then submit
    FILL_FIELD = "FILL_FIELD"      # fill a single field (no submit)
    SELECT_OPTION = "SELECT_OPTION"
    PRESS_KEY = "PRESS_KEY"


class StateTransition(BaseModel):
    """
    A single observed transition in the application state graph.

    Records the action that caused the transition, the source and target
    states, and any evidence captured.
    """
    transition_id: str = Field(default_factory=lambda: f"tr-{uuid.uuid4().hex[:8]}")
    source_state_id: str
    action_type: ExplorationActionType
    target_element_key: Optional[str] = None   # element_key of the element acted upon
    target_element_selector: Optional[str] = None
    target_element_name: Optional[str] = None
    action_value: Optional[str] = None          # value filled / option selected
    resulting_state_id: Optional[str] = None    # None if navigation failed
    url_before: str = ""
    url_after: str = ""
    url_changed: bool = False
    new_state_discovered: bool = False           # True if resulting_state was not previously seen
    duration_seconds: float = 0.0
    error: Optional[str] = None
    step_number: int = 0


# ---------------------------------------------------------------------------
# Exploration log entry
# ---------------------------------------------------------------------------

class ExplorationLogEntry(BaseModel):
    """One entry in the exploration event log."""
    step: int
    event_type: str   # STATE_DISCOVERED, ACTION_TAKEN, BUDGET_ENFORCED, LOOP_DETECTED, SAFETY_BLOCKED, ERROR
    message: str
    state_id: Optional[str] = None
    transition_id: Optional[str] = None
    url: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Top-level ApplicationStateGraph
# ---------------------------------------------------------------------------

class ApplicationStateGraph(BaseModel):
    """
    The complete model of the application's observable state space.

    Built incrementally during exploration.  Each discovered state and
    each observed transition is added here.  The exploration trace
    provides a full audit trail of how the graph was built.
    """
    graph_id: str = Field(default_factory=lambda: f"asg-{uuid.uuid4().hex[:10]}")
    starting_url: str
    states: Dict[str, UIState] = Field(default_factory=dict)       # state_id → UIState
    transitions: List[StateTransition] = Field(default_factory=list)
    exploration_log: List[ExplorationLogEntry] = Field(default_factory=list)
    total_actions_taken: int = 0
    budget_exhausted: bool = False
    budget_reason: Optional[str] = None
    duration_seconds: float = 0.0
    warnings: List[str] = Field(default_factory=list)

    # Convenience properties
    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def transition_count(self) -> int:
        return len(self.transitions)

    @property
    def new_states_discovered(self) -> int:
        return sum(1 for t in self.transitions if t.new_state_discovered)


# ---------------------------------------------------------------------------
# Exploration result — returned by the /api/explore endpoint
# ---------------------------------------------------------------------------

class ExploreRequest(BaseModel):
    """Request body for POST /api/explore."""
    url: str
    description: Optional[str] = None
    max_depth: Optional[int] = None
    max_actions: Optional[int] = None
    max_pages: Optional[int] = None
    max_time_seconds: Optional[float] = None


class ExploreResponse(BaseModel):
    """Response for POST /api/explore."""
    explore_id: str = Field(default_factory=lambda: f"exp-{uuid.uuid4().hex[:10]}")
    target_url: str
    graph: ApplicationStateGraph
    states_discovered: int
    transitions_observed: int
    new_states_found: int
    exploration_complete: bool
    duration_seconds: float
    summary: str
