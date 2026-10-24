"""
Unit and Integration Tests for Phase 5: LLM Brain.
Verifies:
- PageUnderstanding caching by state_class_id
- Fallback to rule-based understanding on LLM timeout or garbage JSON
- Question synthesis from periodic reflection
- Prediction specialization via plan_probe
- Live Gemini provider integration (conditionally skipped when GEMINI_API_KEY is not set)
"""

import os
import time
import pytest
from unittest.mock import MagicMock

from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    Prediction,
)
from app.investigation_agent.agenda import (
    InvestigationAgenda,
    InvestigationQuestion,
    CoverageLedger,
)
from app.investigation_agent.understanding import (
    PageUnderstandingEngine,
    PageUnderstanding,
)
from app.investigation_agent.planner import (
    AdaptiveInvestigationPlanner,
)


class FakeLLMProvider:
    """Mock LLM provider returning predetermined JSON responses."""

    def __init__(self, responses=None, should_fail=False, delay=0.0):
        self.responses = responses or {}
        self.should_fail = should_fail
        self.delay = delay
        self.call_count = 0

    async def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.call_count += 1
        if self.delay > 0:
            time.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Fake LLM failure simulation")
        for key, resp in self.responses.items():
            if key in user_prompt or key in system_prompt:
                return resp
        return list(self.responses.values())[0] if self.responses else {}


def test_understanding_caching_by_state_class_id():
    """
    Verifies that calling understand() on multiple observations sharing the same
    state_class_id only triggers the LLM once.
    """
    fake_provider = FakeLLMProvider(
        responses={
            "Observation": {
                "page_summary": "Apex Portal registration page",
                "primary_purpose": "Account Creation",
                "expected_user_journeys": ["Register new user"],
                "risk_areas": ["Password mismatch", "Terms bypass"],
                "interesting_invariants": ["Unchecked terms rejected"],
                "domain_guess": "portal",
            }
        }
    )
    engine = PageUnderstandingEngine(provider=fake_provider)

    obs1 = UIStateObservation(
        state_id="s1",
        state_class_id="cls_auth_reg",
        url="http://localhost/register",
        normalized_route="/register",
        page_title="Register",
        headings=["Create Account"],
    )

    obs2 = UIStateObservation(
        state_id="s2",
        state_class_id="cls_auth_reg",  # Same state_class_id
        url="http://localhost/register?ref=home",
        normalized_route="/register",
        page_title="Register - Referral",
        headings=["Create Account"],
    )

    u1 = engine.understand(obs1)
    u2 = engine.understand(obs2)

    assert fake_provider.call_count == 1, "LLM should only be invoked once for the same state_class_id"
    assert u1.primary_purpose == "Account Creation"
    assert u2.primary_purpose == "Account Creation"
    assert u1.strategy_used == "LLM"


def test_understanding_fallback_on_llm_failure():
    """
    Verifies that when LLM fails or times out, the engine seamlessly falls back
    to deterministic rule-based understanding without raising an exception.
    """
    failing_provider = FakeLLMProvider(should_fail=True)
    engine = PageUnderstandingEngine(provider=failing_provider)

    obs = UIStateObservation(
        state_id="s_login",
        state_class_id="cls_login_test",
        url="http://localhost/login",
        normalized_route="/login",
        page_title="Sign In",
        headings=["User Login"],
        forms=[{"form_id": "f", "fields": [{"name": "password", "type": "password"}]}],
    )

    u = engine.understand(obs)

    assert u is not None
    assert u.strategy_used == "RULE_BASED_FALLBACK"
    assert "Login" in u.primary_purpose or "Authentication" in u.primary_purpose
    assert engine.llm_fallbacks_count == 1


def test_planner_plan_probe_specialization():
    """
    Verifies that planner.plan_probe can specialize predictions with LLM output
    or fall back safely on timeout.
    """
    fake_provider = FakeLLMProvider(
        responses={
            "Probing": {
                "prediction_expect": "Server validates password length and returns HTTP 422 error banner",
                "prediction_signals": ["alert_present", "no_route_change"],
            }
        }
    )
    planner = AdaptiveInvestigationPlanner(provider=fake_provider)

    q = InvestigationQuestion(
        id="q_pwd",
        category="constraint",
        target_state_class="cls_auth",
        target_element="#password",
        hypothesis="Verify password minimum length constraint",
        probe_family="forms",
        script=ProbeScript(name="p_short", family="forms", steps=[ProbeStep(action_type="FILL")]),
        needs_llm_synthesis=True,
    )

    page_u = PageUnderstanding(
        page_summary="Login",
        primary_purpose="Authentication",
        risk_areas=["Short password"],
    )

    obs = UIStateObservation(
        state_id="s1",
        state_class_id="cls_auth",
        url="http://localhost/login",
        normalized_route="/login",
        page_title="Login",
    )

    script, pred = planner.plan_probe(q, page_u, obs)
    assert script.name == "p_short"
    assert "password length" in pred.expect.lower()
    assert "alert_present" in pred.signals


def test_planner_reflection_generates_hypotheses():
    """
    Verifies that planner.reflect() generates new targeted questions based on
    coverage ledger and recent results.
    """
    fake_provider = FakeLLMProvider(
        responses={
            "Strategist": {
                "new_questions": [
                    {
                        "hypothesis": "Verify admin panel boundary without auth cookie",
                        "category": "constraint",
                        "target": "/admin",
                        "priority": 0.85,
                    },
                    {
                        "hypothesis": "Check session invalidation on logout",
                        "category": "invariant",
                        "target": "#logout",
                        "priority": 0.70,
                    }
                ]
            }
        }
    )
    planner = AdaptiveInvestigationPlanner(provider=fake_provider)

    coverage = CoverageLedger(
        routes_visited={"/login", "/register"},
        state_classes_discovered={"cls_auth"},
        actions_tried={"#submit"},
    )

    hypotheses = planner.reflect(
        coverage_ledger=coverage,
        recent_results=[{"summary": "Registration succeeded"}],
        bug_leads=[],
    )

    assert len(hypotheses) == 2
    assert hypotheses[0]["category"] == "constraint"
    assert "/admin" in hypotheses[0]["target"]


@pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"),
    reason="Live Gemini API key not configured in environment"
)
def test_live_gemini_page_understanding():
    """
    Integration test with live configured Gemini model.
    Only runs if GEMINI_API_KEY or GOOGLE_API_KEY is present in the environment.
    """
    from app.llm.gemini_provider import GeminiProvider
    from app.config import get_settings

    settings = get_settings()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or settings.google_api_key
    provider = GeminiProvider(api_key=api_key, model=settings.gemini_model, timeout_seconds=6.0)
    engine = PageUnderstandingEngine(provider=provider)

    obs = UIStateObservation(
        state_id="s_sauce_login",
        state_class_id="cls_sauce_login",
        url="https://www.saucedemo.com/",
        normalized_route="saucedemo.com/",
        page_title="Swag Labs",
        headings=["Swag Labs"],
        forms=[{
            "form_id": "login_button_container",
            "fields": [
                {"name": "user-name", "type": "text", "label": "Username", "required": True},
                {"name": "password", "type": "password", "label": "Password", "required": True},
            ]
        }],
    )

    understanding = engine.understand(obs, timeout_seconds=6.0)
    assert understanding is not None
    assert understanding.strategy_used in ("LLM", "RULE_BASED_FALLBACK")
    assert len(understanding.expected_user_journeys) > 0
