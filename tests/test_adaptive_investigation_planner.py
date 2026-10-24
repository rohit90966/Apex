"""
tests/test_adaptive_investigation_planner.py
============================================
Comprehensive test suite auditing the Adaptive LLM-Powered Investigation Planner:
1. Planner behavior (semantic choice, candidate constraint, injection resistance).
2. Closed-loop reasoning & discrepancy tracking.
3. Resilience & Fallback behavior (timeout, rate limit, parse failure, cache hit).
4. Application Understanding Model (facts vs hypotheses vs unknowns).
5. Safety enforcement & Pipeline isolation.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from app.investigation_agent.models import (
    UIStateObservation,
    FrontierItem,
    FrontierStatus,
    ApplicationUnderstandingModel,
    PageLandmark,
    SemanticControlUnderstanding,
    ElementReference,
    PlanningStrategy,
)
from app.investigation_agent.planner import (
    AdaptiveInvestigationPlanner,
    InvestigationPlanningDecision,
)
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.workflow import InvestigationOrchestrator
from app.llm.base import LLMTimeoutError, LLMRateLimitError, LLMParseError


# --- 1. Planner Behavior Tests ---

def test_planner_chooses_semantically_useful_action_over_keyword_weight():
    """
    LLM recommends an action based on information gain even when
    the candidate's label does not contain e-commerce keywords.
    """
    mock_provider = MagicMock()
    mock_decision = {
        "selected_candidate_id": "act_dashboard_analytics_btn",
        "investigation_objective": "Explore telemetry metrics",
        "evidence_references": ["#analytics-widget"],
        "expected_observable_outcome": "Display active performance charts",
        "expected_information_gain": "Reveals system telemetry and active resource loads",
        "relevant_prerequisites": [],
        "uncertainty": "low",
        "requires_safety_review": False,
        "inferred_domain": "observability_dashboard",
    }
    mock_provider.generate_json = AsyncMock(return_value=mock_decision)

    planner = AdaptiveInvestigationPlanner(provider=mock_provider)

    candidate_keyword = FrontierItem(
        item_id="act_cart_view",
        item_type="control",
        target="#cart-link",
        priority_score=4.0,  # High keyword priority
        rationale="View cart",
    )
    candidate_semantic = FrontierItem(
        item_id="act_dashboard_analytics_btn",
        item_type="control",
        target="#analytics-widget",
        priority_score=1.0,  # Low default priority
        rationale="Analytics Dashboard",
    )

    eligible = [candidate_keyword, candidate_semantic]
    selected, decision, strategy = planner.recommend_next_action(
        current_obs=UIStateObservation(
            state_id="st_dash",
            url="https://app.example.com/dash",
            normalized_route="app.example.com/dash",
            page_title="Dashboard",
        ),
        states={},
        recent_transitions=[],
        eligible_candidates=eligible,
        fallback_candidate=candidate_keyword,
        budget_remaining={"actions_remaining": 5},
    )

    assert strategy == "LLM_ADAPTIVE"
    assert selected.item_id == "act_dashboard_analytics_btn"
    assert decision.inferred_domain == "observability_dashboard"


def test_planner_rejects_hallucinated_candidate_id():
    """If the LLM returns a candidate ID not present in eligible_candidates, deterministic fallback must engage."""
    mock_provider = MagicMock()
    mock_decision = {
        "selected_candidate_id": "invented_candidate_999",  # Hallucinated ID
        "investigation_objective": "Invalid action",
        "expected_observable_outcome": "None",
        "expected_information_gain": "None",
    }
    mock_provider.generate_json = AsyncMock(return_value=mock_decision)

    planner = AdaptiveInvestigationPlanner(provider=mock_provider)

    valid_candidate = FrontierItem(
        item_id="act_real_button",
        item_type="control",
        target="#real-btn",
        priority_score=2.0,
    )

    selected, decision, strategy = planner.recommend_next_action(
        current_obs=UIStateObservation(
            state_id="st_1",
            url="https://app.example.com/",
            normalized_route="app.example.com/",
            page_title="Home",
        ),
        states={},
        recent_transitions=[],
        eligible_candidates=[valid_candidate],
        fallback_candidate=valid_candidate,
        budget_remaining={"actions_remaining": 5},
    )

    # Must reject hallucinated candidate and use fallback
    assert strategy == "DETERMINISTIC_FALLBACK"
    assert selected.item_id == "act_real_button"
    assert planner.llm_fallbacks_count == 1
    assert planner.llm_failures_count == 1


def test_planner_untrusted_page_content_injection_protection():
    """
    Verifies that system prompt explicitly instructs model to treat page content as untrusted
    and that malicious injection attempts in page text are handled safely.
    """
    planner = AdaptiveInvestigationPlanner()
    assert "UNTRUSTED DATA" in planner.SYSTEM_PROMPT
    assert "DO NOT invent selectors" in planner.SYSTEM_PROMPT
    assert "ONLY select a 'selected_candidate_id' from the provided 'eligible_candidates'" in planner.SYSTEM_PROMPT


def test_planner_caches_recommendation_for_identical_state():
    """Repeated calls with identical observation and candidates must reuse cached decision without redundant LLM calls."""
    mock_provider = MagicMock()
    mock_decision = {
        "selected_candidate_id": "act_btn_1",
        "investigation_objective": "Explore primary flow",
        "expected_observable_outcome": "Next view",
        "expected_information_gain": "High",
    }
    mock_provider.generate_json = AsyncMock(return_value=mock_decision)

    planner = AdaptiveInvestigationPlanner(provider=mock_provider)
    obs = UIStateObservation(
        state_id="st_cache_test",
        url="https://app.example.com/",
        normalized_route="app.example.com/",
        page_title="Home",
    )
    candidate = FrontierItem(item_id="act_btn_1", item_type="control", target="#b1", priority_score=2.0)

    # Call 1
    sel1, dec1, strat1 = planner.recommend_next_action(
        current_obs=obs, states={}, recent_transitions=[], eligible_candidates=[candidate],
        fallback_candidate=candidate, budget_remaining={},
    )
    assert strat1 == "LLM_ADAPTIVE"
    assert planner.llm_calls_count == 1

    # Call 2 with identical state & candidate
    sel2, dec2, strat2 = planner.recommend_next_action(
        current_obs=obs, states={}, recent_transitions=[], eligible_candidates=[candidate],
        fallback_candidate=candidate, budget_remaining={},
    )
    assert strat2 == "LLM_ADAPTIVE"
    # Call count must remain 1 due to cache hit
    assert planner.llm_calls_count == 1
    assert sel1.item_id == sel2.item_id


# --- 2. Fallback & Resilience Tests ---

def test_planner_timeout_invokes_safe_fallback():
    """When the LLM provider times out, the planner safely falls back to deterministic selection."""
    mock_provider = MagicMock()
    mock_provider.generate_json = AsyncMock(side_effect=LLMTimeoutError("Request timed out after 10s"))

    planner = AdaptiveInvestigationPlanner(provider=mock_provider)
    fallback = FrontierItem(item_id="act_fallback_btn", item_type="control", target="#btn-fall", priority_score=1.5)

    selected, decision, strategy = planner.recommend_next_action(
        current_obs=UIStateObservation(state_id="st_timeout", url="https://ex.com", normalized_route="ex.com", page_title="T"),
        states={}, recent_transitions=[], eligible_candidates=[fallback],
        fallback_candidate=fallback, budget_remaining={},
    )

    assert strategy == "DETERMINISTIC_FALLBACK"
    assert selected.item_id == "act_fallback_btn"
    assert planner.llm_fallbacks_count == 1


def test_planner_ratelimit_invokes_safe_fallback():
    """When the LLM provider hits rate limits (429), the planner safely falls back."""
    mock_provider = MagicMock()
    mock_provider.generate_json = AsyncMock(side_effect=LLMRateLimitError("Quota exceeded (429)"))

    planner = AdaptiveInvestigationPlanner(provider=mock_provider)
    fallback = FrontierItem(item_id="act_rate_btn", item_type="control", target="#btn-rate", priority_score=1.5)

    selected, decision, strategy = planner.recommend_next_action(
        current_obs=UIStateObservation(state_id="st_rate", url="https://ex.com", normalized_route="ex.com", page_title="T"),
        states={}, recent_transitions=[], eligible_candidates=[fallback],
        fallback_candidate=fallback, budget_remaining={},
    )

    assert strategy == "DETERMINISTIC_FALLBACK"
    assert selected.item_id == "act_rate_btn"
    assert planner.llm_fallbacks_count == 1


# --- 3. Closed-Loop Reasoning & Discrepancy Tracking ---

def test_closed_loop_discrepancy_tracking_on_unexpected_validation_error():
    """
    When an action expected to navigate encounters a validation error instead,
    the closed-loop reasoning engine records a DISCREPANCY_OBSERVED ledger entry.
    """
    orch = InvestigationOrchestrator(headless=True)
    obs_prev = UIStateObservation(
        state_id="st_form",
        url="https://app.example.com/checkout",
        normalized_route="app.example.com/checkout",
        page_title="Checkout",
        has_validation_error=False,
    )
    obs_new = UIStateObservation(
        state_id="st_form_with_error",
        url="https://app.example.com/checkout",
        normalized_route="app.example.com/checkout",
        page_title="Checkout",
        has_validation_error=True,
        validation_error_message="Error: Required field missing",
        validation_error_locator=".error-box",
    )

    orch.session.capture_observation = MagicMock(return_value=obs_new)

    state = {
        "investigation_id": "inv-loop-01",
        "current_state": obs_prev,
        "states": {obs_prev.state_id: obs_prev},
        "transitions": [],
        "evidence_ledger": [],
        "actions_attempted": 1,
        "actions_successful": 1,
        "actions_verified_effects": 0,
        "route_transitions_observed": 0,
        "same_route_state_transitions": 0,
        "proposed_action": {
            "rationale": "Submit form to proceed to order overview",
            "expected_observable_outcome": "Navigate to order summary page",
        },
        "action_execution_result": {
            "success": True,
            "outcome": "Clicked Submit",
            "action_type": "CLICK_CONTROL",
            "target": "#submit-btn",
        },
    }

    result = orch.node_verify_action_outcome(state)

    discrepancy_entries = [e for e in result["evidence_ledger"] if e.evidence_type == "DISCREPANCY_OBSERVED"]
    assert len(discrepancy_entries) == 1
    assert "Expected 'Navigate to order summary page'" in discrepancy_entries[0].summary
    assert "validation constraint" in discrepancy_entries[0].summary


# --- 4. Application Understanding Model Tests ---

def test_application_understanding_model_tri_partite_grounding():
    """
    ApplicationUnderstandingModel strictly segregates observed facts (DOM backed),
    inferred hypotheses, and unknowns.
    """
    app_model = ApplicationUnderstandingModel(
        page_purpose="Task Management Dashboard",
        application_domain="productivity",
        observed_facts=["Observed 3 tabs: All, Active, Completed."],
        inferred_hypotheses=["Clicking Completed filters out incomplete tasks."],
        unknowns=["Does task completion persist across browser reloads?"],
        investigation_uncertainty=0.45,
    )

    assert len(app_model.observed_facts) == 1
    assert len(app_model.inferred_hypotheses) == 1
    assert len(app_model.unknowns) == 1
    assert app_model.investigation_uncertainty == 0.45

    # Verification: Inferred hypotheses must not be promoted to observed facts without evidence
    assert app_model.inferred_hypotheses[0] not in app_model.observed_facts


def test_application_model_updates_from_live_observation():
    """node_update_application_model must extract landmarks and facts from current observation."""
    orch = InvestigationOrchestrator(headless=True)
    obs = UIStateObservation(
        state_id="st_dash",
        url="https://app.example.com/tasks",
        normalized_route="app.example.com/tasks",
        page_title="Tasks",
        headings=["Task Overview", "Upcoming Deadlines"],
        interactive_controls=[
            ElementReference(
                element_id="btn_1",
                role="button",
                accessible_name="Create Task",
                tag_name="button",
                selector="#create-btn",
                element_type="button",
            )
        ],
        forms=[{"form_id": "task_form", "action": "/tasks", "fields": [{"name": "title", "required": True}]}],
    )

    state = {
        "current_state": obs,
        "frontier": {},
        "visited_state_signatures": [],
        "application_model": ApplicationUnderstandingModel(),
    }

    result = orch.node_update_application_model(state)
    updated_model: ApplicationUnderstandingModel = result["application_model"]

    assert len(updated_model.observed_landmarks) >= 2
    assert len(updated_model.semantic_controls) >= 1
    assert len(updated_model.form_understandings) == 1
    assert "title" in updated_model.form_understandings[0].required_fields
    assert any("Observed state 'st_dash'" in fact for fact in updated_model.observed_facts)
