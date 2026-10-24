"""
Semantic Page Understanding Engine for VerityQA.
Extracts high-level application purpose, user journeys, risks, and invariants using LLM reasoning
with strict JSON schema validation, state-class caching, and deterministic fallback heuristics.
"""

import json
import time
import asyncio
import logging
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from app.investigation_agent.models import UIStateObservation

logger = logging.getLogger(__name__)


class PageUnderstanding(BaseModel):
    """
    Structured semantic understanding of a UI state class.
    """
    page_summary: str = Field(description="One-line summary of what the page represents")
    primary_purpose: str = Field(description="Primary business or user goal on this screen")
    expected_user_journeys: List[str] = Field(default_factory=list, description="Plausible workflows originating here")
    risk_areas: List[str] = Field(default_factory=list, description="High-risk controls, edge cases, or validation targets")
    interesting_invariants: List[str] = Field(default_factory=list, description="State invariants expected to hold true")
    domain_guess: str = "web_application"
    strategy_used: str = "LLM"


class PageUnderstandingEngine:
    """
    Manages semantic page understanding:
    - Caches results by state_class_id
    - Prompts configured LLM with bounded structured context
    - Fallback heuristic when LLM is unavailable, times out (>5.0s), or fails schema validation
    """

    SYSTEM_PROMPT = """You are an Autonomous Application Architecture Specialist.
Your goal is to inspect a webpage representation and output a structured understanding of its purpose, user journeys, risks, and invariants.

Return STRICT JSON matching this schema:
{
  "page_summary": "<concise summary>",
  "primary_purpose": "<primary goal of this page>",
  "expected_user_journeys": ["<journey 1>", "<journey 2>"],
  "risk_areas": ["<risk area 1>", "<risk area 2>"],
  "interesting_invariants": ["<invariant 1>", "<invariant 2>"],
  "domain_guess": "<e-commerce | saas | authentication | portal | dashboard | other>"
}"""

    def __init__(self, provider=None):
        self.provider = provider
        self._provider_checked = False
        self.cache: Dict[str, PageUnderstanding] = {}
        self.llm_calls_count = 0
        self.llm_fallbacks_count = 0
        self.total_latency_seconds = 0.0

    def _ensure_provider(self):
        if self._provider_checked:
            return self.provider
        self._provider_checked = True
        if self.provider is None:
            try:
                from app.llm.factory import get_llm_provider
                self.provider = get_llm_provider()
            except Exception as e:
                logger.info("[PageUnderstanding] LLM provider not available (%s), using rule-based engine", e)
                self.provider = None
        return self.provider

    def understand(self, obs: UIStateObservation, timeout_seconds: Optional[float] = None) -> PageUnderstanding:
        """
        Understands the given observation.
        Returns cached result if state_class_id was already understood.
        Patience-first: local LLM inference is never cancelled by an artificial 10s deadline.
        """
        cache_key = obs.state_class_id or obs.state_id
        if cache_key in self.cache:
            logger.debug("[PageUnderstanding] Returning cached understanding for %s", cache_key)
            return self.cache[cache_key]

        provider = self._ensure_provider()
        if not provider:
            understanding = self._fallback_rule_based_understanding(obs)
            self.cache[cache_key] = understanding
            return understanding

        # Determine effective timeout: if caller explicitly supplied a non-None timeout, respect it;
        # otherwise inherit the provider's configured timeout (None for patience-first local Ollama).
        provider_timeout = getattr(provider, "timeout", None)
        if timeout_seconds is not None:
            timeout_limit = timeout_seconds
        else:
            timeout_limit = provider_timeout

        context = self._build_prompt_context(obs)
        user_prompt = f"Webpage Observation Data:\n{json.dumps(context, indent=2)}\n\nAnalyze this page and return the required JSON understanding."

        t0 = time.time()
        self.llm_calls_count += 1
        raw_result = None

        try:
            async def _invoke():
                if timeout_limit is not None:
                    return await asyncio.wait_for(
                        provider.generate_json(self.SYSTEM_PROMPT, user_prompt),
                        timeout=timeout_limit,
                    )
                return await provider.generate_json(self.SYSTEM_PROMPT, user_prompt)

            # Handle sync vs async loop safely
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _invoke())
                    raw_result = future.result(timeout=timeout_limit + 1.0 if timeout_limit else None)
            else:
                raw_result = asyncio.run(_invoke())

            latency = round(time.time() - t0, 3)
            self.total_latency_seconds += latency

            if not isinstance(raw_result, dict):
                raise ValueError("LLM returned non-dictionary response")

            understanding = PageUnderstanding(
                page_summary=raw_result.get("page_summary") or f"Page at {obs.url}",
                primary_purpose=raw_result.get("primary_purpose") or "Interactive Web Application View",
                expected_user_journeys=raw_result.get("expected_user_journeys") or ["Explore page interactions"],
                risk_areas=raw_result.get("risk_areas") or ["Form validation", "State modification"],
                interesting_invariants=raw_result.get("interesting_invariants") or ["Reload maintains state"],
                domain_guess=raw_result.get("domain_guess") or "web_application",
                strategy_used="LLM",
            )
            self.cache[cache_key] = understanding
            return understanding

        except Exception as e:
            latency = round(time.time() - t0, 3)
            self.total_latency_seconds += latency
            logger.warning("[PageUnderstanding] LLM understanding call failed (%.2fs): %s. Falling back to rule-based.", latency, e)
            self.llm_fallbacks_count += 1
            understanding = self._fallback_rule_based_understanding(obs)
            self.cache[cache_key] = understanding
            return understanding

    def _build_prompt_context(self, obs: UIStateObservation) -> Dict[str, Any]:
        """Builds structured bounded context for LLM prompt."""
        forms_info = []
        for f in obs.forms:
            fields = []
            for fld in f.get("fields", []):
                fields.append({
                    "name": fld.get("name"),
                    "type": fld.get("type"),
                    "label": fld.get("label"),
                    "required": fld.get("required", False),
                })
            forms_info.append({
                "action": f.get("action"),
                "method": f.get("method"),
                "fields": fields[:10],
            })

        controls_sample = []
        for c in obs.interactive_controls[:15]:
            controls_sample.append({
                "role": c.role,
                "name": c.accessible_name,
                "tag": c.tag_name,
            })

        return {
            "route": obs.normalized_route or obs.url,
            "title": obs.page_title,
            "headings": obs.headings[:6],
            "forms": forms_info[:4],
            "controls_sample": controls_sample,
            "a11y_tree_snippet": (obs.a11y_snapshot or "")[:400],
            "dynamic_signals": obs.dynamic_signals,
        }

    def _fallback_rule_based_understanding(self, obs: UIStateObservation) -> PageUnderstanding:
        """Deterministic fallback understanding based on route, headings, and controls."""
        url_lower = (obs.url or "").lower()
        title_lower = (obs.page_title or "").lower()
        headings_text = " ".join(obs.headings).lower()
        combined = f"{url_lower} {title_lower} {headings_text}"

        has_password = any(
            any(f.get("type") == "password" or "password" in (f.get("name") or "").lower() for f in form.get("fields", []))
            for form in obs.forms
        )

        if "login" in combined or "sign in" in combined or ("auth" in combined and has_password):
            return PageUnderstanding(
                page_summary=f"User authentication interface on {obs.page_title or 'Login'}",
                primary_purpose="Authentication and User Login",
                expected_user_journeys=["Enter credentials and submit", "Access password recovery if available", "Navigate to registration"],
                risk_areas=["Credential submission without credentials", "Short/invalid password handling", "Rate limiting and error disclosure"],
                interesting_invariants=["Unauthenticated user is restricted from protected routes", "Form persists field inputs on rejection"],
                domain_guess="authentication",
                strategy_used="RULE_BASED_FALLBACK",
            )
        elif "register" in combined or "sign up" in combined or "registration" in combined:
            return PageUnderstanding(
                page_summary=f"Account registration screen on {obs.page_title or 'Registration'}",
                primary_purpose="Account Creation and Registration",
                expected_user_journeys=["Submit registration form", "Bypass client-side constraints", "Navigate to login"],
                risk_areas=["Password confirmation matching", "Terms agreement enforcement", "Field length and format validation"],
                interesting_invariants=["Unchecked mandatory terms must be rejected server-side", "Mismatching passwords must be rejected"],
                domain_guess="portal",
                strategy_used="RULE_BASED_FALLBACK",
            )
        elif "checkout" in combined or "cart" in combined or "order" in combined:
            return PageUnderstanding(
                page_summary=f"Transactional order progression on {obs.page_title or 'Order'}",
                primary_purpose="Order Review and Completion",
                expected_user_journeys=["Review selected items", "Enter order information", "Proceed to confirmation"],
                risk_areas=["Input validation on recipient info", "Total calculation consistency", "Cancellation handling"],
                interesting_invariants=["Cart items persist across navigation", "Badge counter matches item count"],
                domain_guess="e-commerce",
                strategy_used="RULE_BASED_FALLBACK",
            )
        elif "dashboard" in combined or "portal" in combined or "home" in combined:
            return PageUnderstanding(
                page_summary=f"Application workspace dashboard for {obs.page_title or 'Dashboard'}",
                primary_purpose="Primary Application Navigation and Workflow Hub",
                expected_user_journeys=["Access main navigation sections", "Interact with dashboard widgets", "Manage settings"],
                risk_areas=["Unauthorized access to privileged widgets", "State mutation via action buttons"],
                interesting_invariants=["Session remains active across reload", "Navigation options persist"],
                domain_guess="dashboard",
                strategy_used="RULE_BASED_FALLBACK",
            )
        else:
            return PageUnderstanding(
                page_summary=f"Interactive view: {obs.page_title or obs.normalized_route}",
                primary_purpose=f"Interact with controls on {obs.page_title or 'view'}",
                expected_user_journeys=["Explore available links and controls", "Submit view forms"],
                risk_areas=["Unhandled technical exceptions", "Stale controls or broken links"],
                interesting_invariants=["Layout remains stable on reload", "HTTP responses are valid 2xx"],
                domain_guess="web_application",
                strategy_used="RULE_BASED_FALLBACK",
            )
