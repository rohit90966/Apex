"""
LangGraph Investigation Workflow and Orchestration Engine.
Coordinates the stateful, authentication-aware investigation loop:
Initialize -> Observe -> Update Model -> Identify Gaps -> Select Action
-> Validate Scope -> Execute Browser Action -> Verify Outcome -> Persist Evidence
-> Evaluate Stopping Conditions -> Generate Investigation Report.
"""

import time
import uuid
import logging
from typing import Dict, Any, Optional, Callable, Set, List
from langgraph.graph import StateGraph, END
from app.investigation_agent.state import InvestigationAgentState
from app.investigation_agent.models import (
    InvestigationStatus,
    FrontierStatus,
    ActionFailureReason,
    StateTransitionRecord,
    EvidenceEntry,
    RuleOrHypothesis,
    RuleCategory,
    ApplicationUnderstandingModel,
    PageLandmark,
    SemanticControlUnderstanding,
    FormValidationUnderstanding,
    PlanningStrategy,
    ProbeScript,
    ProbeResult,
)
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.authenticator import AuthenticationManager
from app.investigation_agent.frontier import FrontierManager
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.planner import AdaptiveInvestigationPlanner, InvestigationPlanningDecision
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.agenda import InvestigationAgenda, InvestigationQuestion
from app.investigation_agent.leads import BugLeadManager
from app.investigation_agent.probe_runner import ProbeRunner
from app.investigation_agent.comparator import (
    compare_prediction_with_diff,
    classify_form_submission,
    FormSubmitOutcome,
    ComparatorVerdict,
)
from app.investigation_agent.oracles import evaluate_always_on_oracles, OracleType, OracleStrength
from app.investigation_agent.understanding import PageUnderstandingEngine, PageUnderstanding

logger = logging.getLogger(__name__)


class InvestigationOrchestrator:
    """
    Stateful Investigation Agent orchestrator.
    Manages graph lifecycle, shared Playwright browser session, and execution budgets.
    Combines Adaptive LLM Planning with the Deterministic Reliability Layer.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        planner: Optional[AdaptiveInvestigationPlanner] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        sandbox_mode: bool = False,
        sandbox_origins: Optional[List[str]] = None,
    ):
        self.session = StatefulBrowserSession(headless=headless)
        self.planner = planner or AdaptiveInvestigationPlanner()
        self.lead_manager = BugLeadManager()
        from app.investigation_agent.governor import SafetyGovernor
        governor = SafetyGovernor(
            sandbox_mode=bool(sandbox_mode),
            sandbox_origins=list(sandbox_origins or []),
        )
        self.runner = ProbeRunner(governor=governor)
        self.understanding_engine = PageUnderstandingEngine(provider=self.planner.provider)
        self.agenda: Optional[InvestigationAgenda] = None
        self.event_callback = event_callback
        self._emitted_leads: Set[str] = set()
        # Probes that never executed their action (reported, never counted as coverage).
        self._unexecuted_probes: List[Dict[str, Any]] = []

    def _record_unexecuted(self, question_id: Optional[str], reason: str, target: Optional[str], detail: str) -> None:
        self._unexecuted_probes.append({"question_id": question_id, "reason": reason, "target": target, "detail": (detail or "")[:200]})

    def emit_event(self, event_type: str, data: Dict[str, Any]):
        if self.event_callback:
            try:
                payload = {"type": event_type, "timestamp": time.time(), **data}
                self.event_callback(payload)
            except Exception as e:
                logger.debug("[Workflow] event_callback failed: %s", e)

    def close(self):
        self.session.close()

    @staticmethod
    def _checkpoint_safe_credentials(creds: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Keeps only non-secret credential metadata for on-disk checkpoints."""
        if not isinstance(creds, dict):
            return None
        return {"username": creds.get("username"), "source": creds.get("source")}

    def save_session_checkpoint(self, state: InvestigationAgentState, filepath: Optional[str] = None) -> str:
        """
        Persists full investigation session to disk including:
        - Application model & understanding
        - Visited states & transitions graph
        - Dynamic frontier & priority queue
        - Full evidence ledger & observed rules/hypotheses
        - Agenda questions, coverage ledger, probe history, and durations
        - Telemetry counters and auth state
        """
        import json, os
        from app.config import get_settings
        settings = get_settings()
        inv_id = state.get("investigation_id") or "session"
        if not filepath:
            os.makedirs(settings.investigation_checkpoint_dir, exist_ok=True)
            filepath = os.path.join(settings.investigation_checkpoint_dir, f"{inv_id}.json")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        states_data = {}
        for sid, s in (state.get("states") or {}).items():
            states_data[sid] = s.model_dump() if hasattr(s, "model_dump") else s

        transitions_data = [
            t.model_dump() if hasattr(t, "model_dump") else t
            for t in (state.get("transitions") or [])
        ]

        frontier_data = {
            fid: (f.model_dump() if hasattr(f, "model_dump") else f)
            for fid, f in (state.get("frontier") or {}).items()
        }

        evidence_data = [
            e.model_dump() if hasattr(e, "model_dump") else e
            for e in (state.get("evidence_ledger") or [])
        ]

        rules_data = [
            r.model_dump() if hasattr(r, "model_dump") else r
            for r in (state.get("rules_and_hypotheses") or [])
        ]

        app_model = state.get("application_model")
        app_model_data = app_model.model_dump() if (app_model and hasattr(app_model, "model_dump")) else {}

        current_obs = state.get("current_state")
        current_state_data = current_obs.model_dump() if (current_obs and hasattr(current_obs, "model_dump")) else None

        agenda_data = {}
        browser_time = max(
            float(state.get("browser_execution_time", 0.0) or 0.0),
            float(self.agenda.browser_execution_time if self.agenda else 0.0)
        )
        if self.agenda:
            agenda_data = {
                "start_time": self.agenda.start_time,
                "browser_execution_time": browser_time,
                "llm_latency_seconds": self.agenda.llm_latency_seconds,
                "max_probes": self.agenda.max_probes,
                "max_duration_seconds": self.agenda.max_duration_seconds,
                "mutations_executed": self.agenda.mutations_executed,
                "safety_blocks_encountered": self.agenda.safety_blocks_encountered,
                "retries_count": self.agenda.retries_count,
                "consecutive_unproductive_probes": self.agenda.consecutive_unproductive_probes,
                "max_unproductive_probes": self.agenda.max_unproductive_probes,
                "emergency_safety_ceiling": self.agenda.emergency_safety_ceiling,
                "coverage": {
                    "routes_visited": list(self.agenda.coverage.routes_visited),
                    "state_classes_discovered": list(self.agenda.coverage.state_classes_discovered),
                    "forms_probed": list(self.agenda.coverage.forms_probed),
                    "actions_tried": list(self.agenda.coverage.actions_tried),
                    "probed_signatures": list(self.agenda.coverage.probed_signatures),
                    "mutations_count": self.agenda.coverage.mutations_count,
                },
                "probe_history": self.agenda.probe_history,
                "questions": {k: q.model_dump() for k, q in self.agenda.questions.items()},
            }

        payload = {
            "version": "2.0",
            "investigation_id": inv_id,
            "target_url": state.get("target_url"),
            "current_url": current_obs.url if current_obs else state.get("target_url"),
            "user_description": state.get("user_description"),
            "user_requirements": state.get("user_requirements"),
            # Passwords are never written to disk; load_session_checkpoint re-derives them.
            "auth_credentials": self._checkpoint_safe_credentials(state.get("auth_credentials")),
            "auth_boundary_detected": state.get("auth_boundary_detected", False),
            "auth_attempted": state.get("auth_attempted", False),
            "auth_verified": state.get("auth_verified", False),
            "auth_signals": state.get("auth_signals", []),
            "protected_features_unlocked": state.get("protected_features_unlocked", []),
            "start_time": state.get("start_time", time.time()),
            "browser_execution_time": browser_time,
            "llm_calls_count": state.get("llm_calls_count", 0),
            "llm_fallbacks_count": state.get("llm_fallbacks_count", 0),
            "llm_latency_seconds": state.get("llm_latency_seconds", 0.0),
            "planning_strategy": state.get("planning_strategy", "DETERMINISTIC"),
            "max_actions": state.get("max_actions", 0),
            "max_probes": state.get("max_probes", 0),
            "max_time_seconds": state.get("max_time_seconds", 300.0),
            "actions_evaluated": state.get("actions_evaluated", 0),
            "actions_attempted": state.get("actions_attempted", 0),
            "actions_successful": state.get("actions_successful", 0),
            "actions_verified_effects": state.get("actions_verified_effects", 0),
            "actions_failed": state.get("actions_failed", 0),
            "actions_safety_blocked": state.get("actions_safety_blocked", 0),
            "retries_count": state.get("retries_count", 0),
            "consecutive_unproductive_probes": self.agenda.consecutive_unproductive_probes if self.agenda else 0,
            "route_transitions_observed": state.get("route_transitions_observed", 0),
            "same_route_state_transitions": state.get("same_route_state_transitions", 0),
            "deduplicated_candidates": state.get("deduplicated_candidates", 0),
            "visited_state_signatures": state.get("visited_state_signatures", []),
            "attempted_action_keys": state.get("attempted_action_keys", []),
            "current_state": current_state_data,
            "states": states_data,
            "transitions": transitions_data,
            "frontier": frontier_data,
            "evidence_ledger": evidence_data,
            "rules_and_hypotheses": rules_data,
            "application_model": app_model_data,
            "agenda": agenda_data,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info("[Investigator] Session checkpoint saved to %s", filepath)
        return filepath

    def load_session_checkpoint(self, filepath: str) -> Dict[str, Any]:
        """Loads and restores session state dictionary from a saved checkpoint file."""
        import json, os
        from app.investigation_agent.models import (
            UIStateObservation, StateTransitionRecord, FrontierItem,
            EvidenceEntry, RuleOrHypothesis, ApplicationUnderstandingModel,
        )
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Checkpoint not found: {filepath}")

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        states = {
            sid: UIStateObservation(**s) for sid, s in data.get("states", {}).items()
        }
        transitions = [
            StateTransitionRecord(**t) for t in data.get("transitions", [])
        ]
        frontier = {
            fid: FrontierItem(**item) for fid, item in data.get("frontier", {}).items()
        }
        evidence_ledger = [
            EvidenceEntry(**e) for e in data.get("evidence_ledger", [])
        ]
        rules = [
            RuleOrHypothesis(**r) for r in data.get("rules_and_hypotheses", [])
        ]
        app_model = ApplicationUnderstandingModel(**data["application_model"]) if data.get("application_model") else ApplicationUnderstandingModel()
        current_obs = UIStateObservation(**data["current_state"]) if data.get("current_state") else None

        agenda_data = data.get("agenda", {})
        agenda = InvestigationAgenda(
            max_probes=agenda_data.get("max_probes", data.get("max_probes", 0)),
            max_duration_seconds=agenda_data.get("max_duration_seconds", data.get("max_time_seconds", 300.0)),
            max_mutations=agenda_data.get("mutations_executed", 0) + 15,
            max_safety_blocks=3,
            max_unproductive_probes=agenda_data.get("max_unproductive_probes", 6),
            emergency_safety_ceiling=agenda_data.get("emergency_safety_ceiling", 100),
        )
        agenda.start_time = data.get("start_time", time.time())
        agenda.browser_execution_time = float(agenda_data.get("browser_execution_time", data.get("browser_execution_time", 0.0)) or 0.0)
        agenda.llm_latency_seconds = float(agenda_data.get("llm_latency_seconds", data.get("llm_latency_seconds", 0.0)) or 0.0)
        agenda.mutations_executed = agenda_data.get("mutations_executed", 0)
        agenda.safety_blocks_encountered = agenda_data.get("safety_blocks_encountered", 0)
        agenda.retries_count = agenda_data.get("retries_count", 0)
        agenda.consecutive_unproductive_probes = agenda_data.get("consecutive_unproductive_probes", 0)
        cov = agenda_data.get("coverage", {})
        agenda.coverage.routes_visited = set(cov.get("routes_visited", []))
        agenda.coverage.state_classes_discovered = set(cov.get("state_classes_discovered", []))
        agenda.coverage.forms_probed = set(cov.get("forms_probed", []))
        agenda.coverage.actions_tried = set(cov.get("actions_tried", []))
        agenda.coverage.probed_signatures = set(cov.get("probed_signatures", []))
        agenda.coverage.mutations_count = cov.get("mutations_count", 0)
        agenda.probe_history = agenda_data.get("probe_history", [])
        for k, q_data in agenda_data.get("questions", {}).items():
            agenda.questions[k] = InvestigationQuestion(**q_data)

        self.agenda = agenda

        restored_state: Dict[str, Any] = {
            "investigation_id": data.get("investigation_id"),
            "target_url": data.get("target_url"),
            "current_state": current_obs,
            "states": states,
            "transitions": transitions,
            "frontier": frontier,
            "visited_state_signatures": data.get("visited_state_signatures", []),
            "attempted_action_keys": data.get("attempted_action_keys", []),
            "evidence_ledger": evidence_ledger,
            "rules_and_hypotheses": rules,
            "application_model": app_model,
            "user_description": data.get("user_description"),
            "user_requirements": data.get("user_requirements"),
            "auth_credentials": (
                AuthenticationManager.discover_credentials(data.get("target_url") or "", None)
                if data.get("auth_credentials") else None
            ),
            "auth_boundary_detected": data.get("auth_boundary_detected", False),
            "auth_attempted": data.get("auth_attempted", False),
            "auth_verified": data.get("auth_verified", False),
            "auth_signals": data.get("auth_signals", []),
            "protected_features_unlocked": data.get("protected_features_unlocked", []),
            "start_time": data.get("start_time", time.time()),
            "browser_execution_time": agenda.browser_execution_time,
            "llm_calls_count": data.get("llm_calls_count", 0),
            "llm_fallbacks_count": data.get("llm_fallbacks_count", 0),
            "llm_latency_seconds": data.get("llm_latency_seconds", 0.0),
            "planning_strategy": data.get("planning_strategy", "DETERMINISTIC"),
            "max_actions": data.get("max_actions", 0),
            "max_probes": data.get("max_probes", 0),
            "max_time_seconds": data.get("max_time_seconds", 300.0),
            "candidates_discovered": len(frontier),
            "actions_evaluated": data.get("actions_evaluated", 0),
            "actions_attempted": data.get("actions_attempted", 0),
            "actions_successful": data.get("actions_successful", 0),
            "actions_verified_effects": data.get("actions_verified_effects", 0),
            "actions_failed": data.get("actions_failed", 0),
            "actions_safety_blocked": data.get("actions_safety_blocked", 0),
            "retries_count": data.get("retries_count", 0),
            "consecutive_unproductive_probes": agenda.consecutive_unproductive_probes,
            "route_transitions_observed": data.get("route_transitions_observed", 0),
            "same_route_state_transitions": data.get("same_route_state_transitions", 0),
            "deduplicated_candidates": data.get("deduplicated_candidates", 0),
            "action_failures": [],
            "agenda": self.agenda,
            "probes_executed": len(agenda.probe_history),
            "bug_leads": [],
            "investigation_status": InvestigationStatus.COMPLETED,
        }
        return restored_state

    # --- LangGraph Nodes ---

    def node_initialize(self, state: InvestigationAgentState) -> Dict[str, Any]:
        logger.info("[Investigator] Initializing investigation for %s", state.get("target_url"))

        resume_cp = state.get("resume_checkpoint")
        if resume_cp:
            try:
                restored = self.load_session_checkpoint(resume_cp)
                target_url = restored.get("target_url")
                self.session.start()
                nav_url = restored.get("current_state").url if restored.get("current_state") else target_url
                self.session.navigate(nav_url)
                logger.info("[Investigator] Successfully resumed investigation from checkpoint '%s' at %s", resume_cp, nav_url)
                return restored
            except Exception as e:
                logger.warning("[Investigator] Failed to resume from checkpoint '%s': %s. Starting fresh.", resume_cp, e)

        target_url = state.get("target_url")

        # Discover credentials
        user_creds = state.get("auth_credentials")
        discovered_creds = AuthenticationManager.discover_credentials(target_url, user_creds)

        # Launch browser and navigate to target URL
        self.session.start()
        success, nav_url, status = self.session.navigate(target_url)

        # Initialize agenda & intelligence layer (max_probes <= 0 denotes adaptive mode without a fixed probe ceiling)
        max_probes = state.get("max_probes", 0) if "max_probes" in state else state.get("max_actions", 0)
        self.agenda = InvestigationAgenda(
            max_probes=max_probes,
            max_duration_seconds=state.get("max_time_seconds", 300.0),
            max_mutations=state.get("max_mutations", 15),
            max_safety_blocks=3,
            policy=state.get("exploration_policy", "balanced"),
        )
        self.lead_manager = BugLeadManager()
        from app.investigation_agent.governor import SafetyGovernor
        governor = SafetyGovernor(
            sandbox_mode=bool(state.get("sandbox_mode", False)),
            sandbox_origins=list(state.get("sandbox_origins") or []),
        )
        self.runner = ProbeRunner(governor=governor)

        return {
            "start_time": time.time(),
            "browser_execution_time": 0.0,
            "investigation_id": state.get("investigation_id") or f"inv-{uuid.uuid4().hex[:8]}",
            "states": {},
            "transitions": [],
            "frontier": {},
            "visited_state_signatures": [],
            "attempted_action_keys": [],
            "evidence_ledger": [],
            "rules_and_hypotheses": [],
            "candidates_discovered": 0,
            "actions_evaluated": 0,
            "actions_attempted": 0,
            "actions_successful": 0,
            "actions_verified_effects": 0,
            "actions_failed": 0,
            "actions_safety_blocked": 0,
            "retries_count": 0,
            "consecutive_unproductive_probes": 0,
            "max_probes": max_probes,
            "max_actions": max_probes,
            "route_transitions_observed": 0,
            "same_route_state_transitions": 0,
            "deduplicated_candidates": 0,
            "action_failures": [],
            "planning_strategy": "DETERMINISTIC",
            "llm_calls_count": 0,
            "llm_fallbacks_count": 0,
            "llm_latency_seconds": 0.0,
            "application_model": ApplicationUnderstandingModel(),
            "auth_credentials": discovered_creds,
            "auth_boundary_detected": False,
            "auth_attempted": False,
            "auth_verified": False,
            "auth_signals": [],
            "protected_features_unlocked": [],
            "investigation_status": InvestigationStatus.COMPLETED,
            "agenda": self.agenda,
            "probes_executed": 0,
            "bug_leads": [],
            "decision_snapshots": [],
        }

    # --- v2 Node Cycle ---

    def _enrich_application_model(
        self,
        app_model: Optional[ApplicationUnderstandingModel],
        obs: Optional[ Any ],
        page_understanding: Optional[PageUnderstanding] = None,
    ) -> ApplicationUnderstandingModel:
        """Updates the ApplicationUnderstandingModel with empirical observations from a state."""
        model = app_model or ApplicationUnderstandingModel()
        if not obs:
            return model

        if page_understanding:
            if page_understanding.domain_guess and model.application_domain == "general":
                model.application_domain = page_understanding.domain_guess
            if page_understanding.primary_purpose and model.page_purpose == "Web Application View":
                model.page_purpose = page_understanding.primary_purpose
            for h in (page_understanding.interesting_invariants or [])[:4]:
                if h not in model.inferred_hypotheses:
                    model.inferred_hypotheses.append(h)
            for r in (page_understanding.risk_areas or [])[:4]:
                if r not in model.unknowns:
                    model.unknowns.append(r)

        existing_landmarks = {l.label for l in model.observed_landmarks}
        for h in obs.headings:
            if h not in existing_landmarks:
                model.observed_landmarks.append(PageLandmark(role="heading", label=h))
                existing_landmarks.add(h)

        existing_ctrl_selectors = {c.selector for c in model.semantic_controls}
        for ctrl in obs.interactive_controls[:20]:
            if ctrl.selector not in existing_ctrl_selectors:
                model.semantic_controls.append(SemanticControlUnderstanding(
                    element_id=ctrl.element_id,
                    selector=ctrl.selector,
                    semantic_role=ctrl.role,
                    inferred_purpose=ctrl.accessible_name or ctrl.selector,
                    safety_risk="LOW",
                ))
                existing_ctrl_selectors.add(ctrl.selector)

        for f in obs.forms:
            fid = f.get("form_id", "form")
            req_fields = [fld.get("name") or fld.get("id") for fld in f.get("fields", []) if fld.get("required")]
            existing_f = next((fu for fu in model.form_understandings if fu.form_id == fid), None)
            if not existing_f:
                model.form_understandings.append(FormValidationUnderstanding(
                    form_id=fid,
                    target_action=f.get("action", ""),
                    required_fields=[rf for rf in req_fields if rf],
                ))

        if obs.state_id not in model.verified_states:
            model.verified_states.append(obs.state_id)

        fact_summary = f"Observed state '{obs.state_id}' on {obs.url} with {len(obs.interactive_controls)} controls and {len(obs.forms)} forms."
        if fact_summary not in model.observed_facts:
            model.observed_facts.append(fact_summary)

        if obs.has_validation_error and obs.validation_error_message:
            val_fact = f"Form validation constraint enforced on {obs.url}: '{obs.validation_error_message}' ({obs.validation_error_locator})"
            if val_fact not in model.observed_facts:
                model.observed_facts.append(val_fact)

        if obs.menu_open:
            menu_fact = f"Navigation drawer opened on {obs.url}: sliding menu items visible in active DOM"
            if menu_fact not in model.observed_facts:
                model.observed_facts.append(menu_fact)

        return model

    def node_perceive(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """
        Phase A & B: Perceives the initial live browser state and records its empirical evidence
        BEFORE any navigation or authentication transition is attempted.
        """
        obs = self.session.capture_observation(state_prefix=f"step_{state.get('actions_attempted', 0)}")
        logger.info("[Investigator:perceive] URL: %s, State: %s (class: %s, %d controls, %d forms)",
                    obs.url, obs.state_id, obs.state_class_id, len(obs.interactive_controls), len(obs.forms))

        states = dict(state.get("states") or {})
        states[obs.state_id] = obs

        visited = list(state.get("visited_state_signatures") or [])
        if obs.state_id not in visited:
            visited.append(obs.state_id)

        # Baseline Evidence Ledger Entry for initial page observation
        ledger = list(state.get("evidence_ledger") or [])
        if not any(getattr(e, "evidence_type", "") == "DOM_OBSERVATION" for e in ledger):
            init_ev = EvidenceEntry(
                investigation_id=state.get("investigation_id", "inv_unknown"),
                evidence_id=f"ev_init_{obs.state_id}",
                source_state_id=obs.state_id,
                evidence_type="DOM_OBSERVATION",
                summary=f"Captured baseline DOM observation of {obs.url} with {len(obs.interactive_controls)} interactive controls and {len(obs.forms)} forms",
                screenshot_artifact_id=getattr(obs, "screenshot_artifact_id", None),
                timestamp=time.time(),
            )
            ledger.append(init_ev)

        # Detect whether initial page is an authentication boundary (without auto-skipping the initial page)
        is_auth_boundary, boundary_reason = AuthenticationManager.detect_auth_boundary(obs)
        auth_signals = list(state.get("auth_signals") or [])
        if is_auth_boundary and boundary_reason and boundary_reason not in auth_signals:
            auth_signals.append(boundary_reason)
            logger.info("[Investigator:perceive] Auth boundary detected on initial page %s: %s", obs.url, boundary_reason)

        # Semantic page understanding (cached per state_class_id)
        page_understanding = self.understanding_engine.understand(obs)
        app_model = self._enrich_application_model(state.get("application_model"), obs, page_understanding)

        return {
            "current_state": obs,
            "states": states,
            "visited_state_signatures": visited,
            "page_understanding": page_understanding,
            "application_model": app_model,
            "evidence_ledger": ledger,
            "auth_boundary_detected": is_auth_boundary,
            "auth_signals": auth_signals,
        }

    def node_agenda_update(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Discovers new questions from observation and updates coverage ledger."""
        obs = state.get("current_state")
        if not obs:
            return {}

        if not self.agenda:
            max_probes = state.get("max_probes", 0) if "max_probes" in state else state.get("max_actions", 0)
            self.agenda = InvestigationAgenda(
                max_probes=max_probes,
                max_duration_seconds=state.get("max_time_seconds", 300.0),
                max_mutations=state.get("max_mutations", 15),
                policy=state.get("exploration_policy", "balanced"),
            )

        is_auth = bool(state.get("auth_boundary_detected")) and not bool(state.get("auth_verified"))
        has_creds = bool(state.get("auth_credentials"))
        new_q = self.agenda.populate_from_observation(
            obs,
            is_auth_boundary=is_auth,
            has_auth_creds=has_creds,
        )

        # Sync legacy frontier items
        frontier = dict(state.get("frontier") or {})
        visited_sigs = list(state.get("visited_state_signatures") or [])
        FrontierManager.populate_from_observation(frontier, obs, visited_sigs)

        return {
            "frontier": frontier,
            "candidates_discovered": max(len(frontier), len(self.agenda.questions)),
            "agenda": self.agenda,
        }

    def node_select_question(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Scores and selects the next highest-yield investigation question, prioritizing active journeys."""
        obs = state.get("current_state")
        current_class = obs.state_class_id if obs else None
        current_url = obs.url if obs else (self.session.page.url if (self.session and self.session.page) else None)
        available_selectors = {
            c.selector for c in (obs.interactive_controls if obs else [])
        }
        for link in (obs.navigation_links if obs else []):
            if link.get("href"):
                available_selectors.add(link["href"])
            if link.get("text"):
                available_selectors.add(link["text"])
            if link.get("selector"):
                available_selectors.add(link["selector"])

        if not self.agenda:
            return {"stopping_reason": "No agenda available"}

        q = self.agenda.select_next_question(
            current_state_class=current_class,
            current_url=current_url,
            available_selectors=available_selectors,
        )
        if not q:
            return {"stopping_reason": "All discovered investigation questions answered"}

        logger.info("[Investigator:select_question] Picked %s: [%s] %s (priority=%.2f, active_journey=%s)",
                    q.id, q.category, q.hypothesis, q.priority, self.agenda.active_journey_id)

        return {
            "current_question": q,
            "actions_evaluated": state.get("actions_evaluated", 0) + 1,
            "active_journey_id": self.agenda.active_journey_id,
            "journeys": list(self.agenda.journeys.values()),
        }

    def node_plan_probe(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Retrieves or synthesizes the probe script and prediction for the current question."""
        q: Optional[InvestigationQuestion] = state.get("current_question")
        if not q:
            return {"stopping_reason": "No probe script available for question"}

        obs = state.get("current_state")
        page_understanding = state.get("page_understanding")

        script, pred = self.planner.plan_probe(q, page_understanding, obs)
        q.script = script
        q.prediction = pred

        return {
            "current_probe": script,
            "proposed_action": {
                "action_type": script.steps[0].action_type if script.steps else "PROBE",
                "target": q.target_element or "probe",
                "rationale": q.hypothesis,
            }
        }

    def node_safety_gate(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Validates all probe steps against the SafetyGovernor."""
        q: Optional[InvestigationQuestion] = state.get("current_question")
        script: Optional[ProbeScript] = state.get("current_probe")
        if not script:
            return {"action_validation_passed": False}

        governor = self.runner.governor
        current_url = (
            (q.metadata.get("source_url") if q and q.metadata else None)
            or (self.session.page.url if (self.session.page and self.session.page.url) else state.get("target_url", ""))
        )

        for step in script.steps:
            target_str = step.raw_target or (step.target.css if step.target else "target")
            allowed, reason = governor.can_execute(step.action_type, target_str, current_url, rationale=script.description)
            if not allowed:
                logger.warning("[Investigator:safety_gate] Step blocked (%s on %s): %s", step.action_type, target_str, reason)
                if self.agenda and q:
                    self.agenda.record_probe_outcome(q.id, verdict="SAFETY_BLOCKED", summary=reason, safety_blocked=True, is_productive=False)

                # Persist block decision evidence to ledger
                ledger = list(state.get("evidence_ledger") or [])
                frontier = dict(state.get("frontier") or {})
                obs = state.get("current_state")
                FrontierManager.sync_with_probe_outcome(
                    frontier, q, obs, obs, safety_blocked=True, summary=reason
                )
                blocked_ev = EvidenceEntry(
                    investigation_id=state.get("investigation_id", "inv_unknown"),
                    evidence_id=f"ev_blocked_{q.id}_{int(time.time()*1000)}",
                    source_state_id=obs.state_id if obs else "unknown",
                    evidence_type="SAFETY_DECISION",
                    summary=f"SAFETY_DECISION: Step ({step.action_type} on {target_str}) blocked: {reason}",
                    timestamp=time.time(),
                )
                ledger.append(blocked_ev)

                self._record_unexecuted(q.id if q else None,
                                        "MUTATION_BUDGET_EXHAUSTED" if "Mutation budget exhausted" in reason else "SAFETY_POLICY",
                                        target_str, reason)
                return {
                    "action_validation_passed": False,
                    "actions_safety_blocked": state.get("actions_safety_blocked", 0) + 1,
                    "evidence_ledger": ledger,
                    "frontier": frontier,
                }

        return {"action_validation_passed": True}

    def node_execute_probe(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Executes the probe script in the live browser session."""
        script: Optional[ProbeScript] = state.get("current_probe")
        q: Optional[InvestigationQuestion] = state.get("current_question")
        if not script:
            return {}

        self.emit_event("PROBE_START", {
            "probe_id": script.script_id,
            "name": script.name,
            "target": q.target_element if q else "DOM",
            "description": script.description,
            "steps_count": len(script.steps),
            "predicted_outcome": getattr(q.prediction, "expect", getattr(q.prediction, "predicted_outcome", "")) if (q and q.prediction) else "",
        })

        # Handle explicit Authentication Boundary Transition probe
        if q and q.probe_family == "auth":
            from app.investigation_agent.probe_runner import compute_diff
            t0 = time.time()
            net_mark = self.session.mark()
            err_mark = self.session.mark_errors()
            self.session.drain_mutations()

            # Ensure we are on the login source URL with an active password input before authenticating
            source_url = q.metadata.get("source_url")
            if source_url and self.session.page:
                if (self.session.page.url or "").split("?")[0].rstrip("/") != source_url.split("?")[0].rstrip("/") or not self.session.resolve_target("input[type='password']").get("present"):
                    self.session.navigate(source_url)

            before_obs = self.session.capture_observation("auth_before")
            # If the login page currently carries a validation error from a prior negative form probe,
            # anchor the authentication transition to the clean login entry state on that route so
            # negative form validation and positive authentication remain distinct branches.
            if before_obs.has_validation_error:
                for st_obj in (state.get("states") or {}).values():
                    if st_obj.normalized_route == before_obs.normalized_route and not st_obj.has_validation_error:
                        before_obs.state_id = st_obj.state_id
                        before_obs.has_validation_error = False
                        break
            auth_creds = state.get("auth_credentials") or {}
            success, reason, signals = AuthenticationManager.attempt_authentication(self.session, auth_creds)
            self.session.settle()
            after_obs = self.session.capture_observation("auth_after")

            network_events = self.session.events_since(net_mark)
            console_errors = self.session.errors_since(err_mark)
            mutations = self.session.drain_mutations()
            diff = compute_diff(before_obs, after_obs, network_events, console_errors, mutations)

            res = ProbeResult(
                script_id=script.script_id,
                status="SUCCESS" if success else "FAILED",
                steps_executed=1,
                before_obs=before_obs,
                after_obs=after_obs,
                diff=diff,
                network_events=network_events,
                console_errors=console_errors,
                mutations=mutations,
                error_message=None if success else reason,
                duration_seconds=round(time.time() - t0, 3),
            )

            auth_signals = list(state.get("auth_signals") or [])
            for s in signals:
                if s not in auth_signals:
                    auth_signals.append(s)
            protected_features = list(state.get("protected_features_unlocked") or [])
            ledger = list(state.get("evidence_ledger") or [])

            if success:
                unlock_desc = f"Unlocked view at {after_obs.url}"
                if unlock_desc not in protected_features:
                    protected_features.append(unlock_desc)
                auth_ev = EvidenceEntry(
                    investigation_id=state.get("investigation_id", "inv_unknown"),
                    evidence_id=f"ev_auth_{after_obs.state_id}",
                    source_state_id=before_obs.state_id,
                    after_state_ref=after_obs.state_id,
                    evidence_type="AUTH_VERIFICATION",
                    summary=f"Authentication verified: {reason} -> navigated to {after_obs.url}",
                    screenshot_artifact_id=after_obs.screenshot_artifact_id,
                    timestamp=time.time(),
                )
                ledger.append(auth_ev)
                self.runner.governor.record_mutation()

            actions_attempted = state.get("actions_attempted", 0) + 1
            actions_successful = state.get("actions_successful", 0) + (1 if success else 0)
            actions_failed = state.get("actions_failed", 0) + (0 if success else 1)

            return {
                "current_probe_result": res,
                "actions_attempted": actions_attempted,
                "actions_successful": actions_successful,
                "actions_failed": actions_failed,
                "auth_attempted": True,
                "auth_verified": bool(state.get("auth_verified") or success),
                "auth_signals": auth_signals,
                "protected_features_unlocked": protected_features,
                "evidence_ledger": ledger,
            }

        # Ensure browser is at the question's source_url if the target is not present on current page
        if q and q.metadata.get("source_url") and self.session.page:
            src_url = q.metadata["source_url"]
            first_step = script.steps[0] if script.steps else None
            if first_step and first_step.action_type != "NAVIGATE_LINK":
                if not getattr(first_step, "required_url", None):
                    first_step.required_url = src_url
                t_sel = first_step.raw_target or (first_step.target.css if first_step.target else None)
                target_present = bool(t_sel and self.session.resolve_target(t_sel).get("present"))
                if not target_present:
                    curr_url = self.session.page.url if self.session.page else ""
                    # If target requires a different URL and an active journey is executing on current page,
                    # do not disrupt the journey or waste 3s waiting for an absent element.
                    if self.agenda and self.agenda.active_journey_id and curr_url.rstrip("/") != src_url.rstrip("/"):
                        logger.info("[Investigator:execute_probe] Target %s belongs to %s, current journey view is %s; skipping out-of-context probe", t_sel, src_url, curr_url)
                        before_obs = self.session.capture_observation("precond_skip_before")
                        res = ProbeResult(
                            script_id=script.script_id,
                            status="FAILED",
                            steps_executed=0,
                            before_obs=before_obs,
                            after_obs=before_obs,
                            error_message=f"Precondition unsatisfied: Target '{t_sel}' is for '{src_url}', but current active journey state is '{curr_url}'",
                            duration_seconds=0.01,
                            script=script,
                        )
                        # Nothing ran: this is not an attempted (or failed) action, only an unexecuted probe.
                        return {"current_probe_result": res}
                    else:
                        ok, nav_url, status = self.session.navigate(src_url)
                        if not ok or status == 404:
                            logger.warning("[Investigator:execute_probe] Direct navigation to required state %s failed (status=%s)", src_url, status)
                            # Handle as navigation failure: do not execute probe on entry page or wrong view
                            before_obs = self.session.capture_observation("nav_fail_before")
                            self.session.drain_mutations()
                            self.session.mark_errors()
                            res = ProbeResult(
                                script_id=script.script_id,
                                status="FAILED",
                                steps_executed=0,
                                before_obs=before_obs,
                                after_obs=before_obs,
                                error_message=f"Route precondition failed: navigation to '{src_url}' returned HTTP {status} (expected route guard / session required)",
                                duration_seconds=0.05,
                                script=script,
                            )
                            actions_attempted = state.get("actions_attempted", 0) + 1
                            return {
                                "current_probe_result": res,
                                "actions_attempted": actions_attempted,
                                "actions_successful": state.get("actions_successful", 0),
                                "actions_failed": state.get("actions_failed", 0) + 1,
                            }

        res = self.runner.run_probe(self.session, script, run_id=f"run_{state.get('actions_attempted', 0)}")
        actions_attempted = state.get("actions_attempted", 0) + 1
        actions_successful = state.get("actions_successful", 0) + (1 if res.status == "SUCCESS" else 0)
        actions_failed = state.get("actions_failed", 0) + (1 if res.status != "SUCCESS" else 0)

        if res.status == "SUCCESS":
            for step in script.steps:
                if step.action_type in ("FILL", "SUBMIT", "CHECK", "UNCHECK"):
                    self.runner.governor.record_mutation()

        return {
            "current_probe_result": res,
            "actions_attempted": actions_attempted,
            "actions_successful": actions_successful,
            "actions_failed": actions_failed,
        }

    def node_perceive_after_and_compare(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Compares probe predictions against empirical diff and checks oracles."""
        q: Optional[InvestigationQuestion] = state.get("current_question")
        res: Optional[ProbeResult] = state.get("current_probe_result")
        if not res or not q:
            return {}

        # Probes that never ran their action are recorded as unexecuted (not as tested coverage).
        err = res.error_message or ""
        if "Mutation budget exhausted" in err:
            self._record_unexecuted(q.id, "MUTATION_BUDGET_EXHAUSTED", q.target_element, err)

        # If the probe failed due to navigation failure or unsatisfied precondition, do not treat as an application defect lead
        if res.status == "FAILED" and (
            "Navigation failure" in err
            or "precondition" in err.lower()
        ):
            logger.info("[Investigator:perceive_after] Probe failed due to navigation/precondition; skipping oracle defect creation")
            self._record_unexecuted(q.id, "TARGET_NOT_ON_CURRENT_PAGE" if "precondition" in err.lower() else "NAVIGATION_FAILURE",
                                    q.target_element, err)
            return {}

        prediction = q.prediction
        comp = None
        if prediction:
            comp = compare_prediction_with_diff(prediction, res.diff, res.before_obs, res.after_obs, res.network_events)

        # Passive Oracles Check
        violations = evaluate_always_on_oracles(res)
        for v in violations:
            self.lead_manager.create_lead(
                title=v.title,
                description=v.description,
                state_class=res.after_obs.state_class_id if res.after_obs else "unknown",
                probe_family=q.probe_family,
                question_id=q.id,
                target_desc=(q.target_element or "browser") if q else "browser",
                oracle_type=v.oracle_type,
                strength=v.strength,
                result=res,
            )

        # Form Outcome Check
        if q.probe_family == "forms":
            outcome = classify_form_submission(res.diff, res.before_obs, res.after_obs, res.network_events)
            meta = q.metadata
            kind = meta.get("kind", "")
            if kind in ("constraint_bypass_minlength", "constraint_bypass_checkbox") and outcome == FormSubmitOutcome.ACCEPTED:
                self.lead_manager.create_lead_from_form_outcome(
                    outcome=outcome,
                    expected_outcome=meta.get("expected_outcome", "Form submission rejected"),
                    rule_description=meta.get("rule", "Client validation constraint enforced server side"),
                    probe_family="forms",
                    question_id=q.id,
                    state_class=res.before_obs.state_class_id,
                    target_desc=meta.get("field", "form"),
                    oracle_type=OracleType.DECLARED,
                    strength=OracleStrength.HIGH,
                    result=res,
                )
            elif kind == "mismatch" and outcome == FormSubmitOutcome.ACCEPTED:
                self.lead_manager.create_lead_from_form_outcome(
                    outcome=outcome,
                    expected_outcome=meta.get("expected_outcome", "Password mismatch rejected"),
                    rule_description=meta.get("rule", "Password confirmation must match password"),
                    probe_family="forms",
                    question_id=q.id,
                    state_class=res.before_obs.state_class_id,
                    target_desc=meta.get("field", "form"),
                    oracle_type=OracleType.CONVENTION,
                    strength=OracleStrength.MEDIUM,
                    result=res,
                )
            elif kind == "boundary_password_length" and comp and comp.verdict == ComparatorVerdict.CONTRADICTED:
                self.lead_manager.create_lead_from_form_outcome(
                    outcome=FormSubmitOutcome.ACCEPTED,
                    expected_outcome=meta.get("expected_outcome", "Short password rejected"),
                    rule_description=meta.get("rule", "Password length boundary enforced"),
                    probe_family="forms",
                    question_id=q.id,
                    state_class=res.before_obs.state_class_id,
                    target_desc=meta.get("field", "form"),
                    oracle_type=OracleType.CONVENTION,
                    strength=OracleStrength.MEDIUM,
                    result=res,
                )

        verdict_str = comp.verdict.value if comp else "OBSERVED"
        summary_str = comp.summary if comp else f"Probe executed ({res.status})"

        self.emit_event("PROBE_RESULT", {
            "probe_id": res.script_id,
            "status": res.status,
            "verdict": verdict_str,
            "summary": summary_str,
            "duration_ms": getattr(res, "duration_ms", getattr(res, "duration_seconds", 0.0) * 1000),
            "url_after": res.after_obs.url if res.after_obs else "",
            "state_id_after": res.after_obs.state_id if res.after_obs else "",
        })

        all_leads = self.lead_manager.get_leads()
        for l in all_leads:
            lid = getattr(l, "lead_id", getattr(l, "id", str(uuid.uuid4())))
            if lid not in self._emitted_leads:
                self._emitted_leads.add(lid)
                self.emit_event("LEAD_DETECTED", {
                    "lead_id": lid,
                    "title": getattr(l, "title", ""),
                    "severity": getattr(l, "severity", getattr(l, "severity_guess", "MEDIUM")),
                    "oracle_type": l.oracle_type.value if hasattr(l.oracle_type, "value") else str(l.oracle_type),
                    "description": getattr(l, "description", getattr(l, "title", "")),
                })

        return {
            "probe_verdict": verdict_str,
            "probe_summary": summary_str,
            "probe_comparison": comp,
            "bug_leads": all_leads,
        }

    def node_record(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """
        Phase C: Records probe outcome into agenda, dynamically discovers new states/controls/routes
        from after_obs, and updates ledger, transitions, and metrics.
        """
        q: Optional[InvestigationQuestion] = state.get("current_question")
        res: Optional[ProbeResult] = state.get("current_probe_result")
        verdict = state.get("probe_verdict", "OBSERVED")
        summary = state.get("probe_summary", "")
        comp = state.get("probe_comparison")

        is_mutating = bool(res and res.diff and res.diff.is_significant)
        is_route_change = bool(res and res.before_obs and res.after_obs and (res.before_obs.url.split('?')[0].rstrip('/') != res.after_obs.url.split('?')[0].rstrip('/')))
        is_state_change = bool(res and res.before_obs and res.after_obs and (res.before_obs.state_id != res.after_obs.state_id))
        is_productive = is_route_change or is_state_change or is_mutating
        if self.agenda and q:
            self.agenda.record_probe_outcome(
                question_id=q.id,
                verdict=verdict,
                summary=summary,
                mutating=is_mutating,
                duration_seconds=getattr(res, "duration_seconds", 0.0) if res else 0.0,
                is_productive=is_productive,
                before_obs=res.before_obs if res else None,
                after_obs=res.after_obs if res else None,
                diff=res.diff if res else None,
            )

        ledger = list(state.get("evidence_ledger") or [])
        transitions = list(state.get("transitions") or [])
        states_dict = dict(state.get("states") or {})
        visited_sigs = list(state.get("visited_state_signatures") or [])
        frontier = dict(state.get("frontier") or {})
        route_transitions = state.get("route_transitions_observed", 0)
        same_route_transitions = state.get("same_route_state_transitions", 0)
        verified_effects = state.get("actions_verified_effects", 0)
        current_obs = state.get("current_state")
        page_understanding = state.get("page_understanding")
        app_model = state.get("application_model")

        if res and res.after_obs:
            states_dict[res.after_obs.state_id] = res.after_obs
            if res.after_obs.state_id not in visited_sigs:
                visited_sigs.append(res.after_obs.state_id)

            is_route_change = bool(res.before_obs and (res.before_obs.url.split('?')[0].rstrip('/') != res.after_obs.url.split('?')[0].rstrip('/')))
            is_state_change = bool(res.before_obs and (res.before_obs.state_id != res.after_obs.state_id))

            # Dynamically populate new investigation questions and frontier items from the newly observed state
            if self.agenda:
                is_new_auth_gate, _ = AuthenticationManager.detect_auth_boundary(res.after_obs)
                has_untested_creds = bool(state.get("auth_credentials")) and not bool(state.get("auth_attempted"))
                self.agenda.populate_from_observation(
                    res.after_obs,
                    is_auth_boundary=is_new_auth_gate and has_untested_creds,
                    has_auth_creds=has_untested_creds,
                )
            FrontierManager.populate_from_observation(frontier, res.after_obs, visited_sigs)
            FrontierManager.sync_with_probe_outcome(
                frontier, q, res.before_obs, res.after_obs, safety_blocked=False, summary=summary
            )

            # Update active focus state and semantic understanding when state or route advances
            current_obs = res.after_obs
            page_understanding = self.understanding_engine.understand(res.after_obs)
            app_model = self._enrich_application_model(app_model, res.after_obs, page_understanding)

            ev_entry = EvidenceEntry(
                investigation_id=state.get("investigation_id", ""),
                source_state_id=res.before_obs.state_id if res.before_obs else "initial",
                after_state_ref=res.after_obs.state_id if res.after_obs else None,
                evidence_type="PROBE_RESULT",
                summary=f"Probe [{q.id if q else res.script_id}]: verdict={verdict}. {summary}",
                validation_message=res.after_obs.validation_error_message if res.after_obs.has_validation_error else None,
                locator=res.after_obs.validation_error_locator if res.after_obs.has_validation_error else None,
                screenshot_artifact_id=res.after_obs.screenshot_artifact_id,
            )
            ledger.append(ev_entry)

            if is_route_change or is_state_change:
                verified_effects += 1
                if is_route_change:
                    route_transitions += 1
                else:
                    same_route_transitions += 1

                if q and q.probe_family == "auth":
                    act_label = "AUTHENTICATE"
                elif q and q.script and q.script.steps:
                    first_act = q.script.steps[0].action_type
                    act_label = f"{first_act}:{q.script.name}" if first_act not in q.script.name.upper() else q.script.name
                else:
                    act_label = res.script_id

                obs_effect = getattr(comp, "observed_effect", None) or (
                    "ROUTE_TRANSITION" if is_route_change else (
                        "VALIDATION_FEEDBACK" if res.after_obs.has_validation_error else "IN_PAGE_STATE_MUTATION"
                    )
                )
                trans_record = StateTransitionRecord(
                    source_state_id=res.before_obs.state_id if res.before_obs else "initial",
                    source_observation_id=res.before_obs.screenshot_artifact_id if res.before_obs else None,
                    action_type=act_label,
                    target_element_desc=(q.target_element or "probe") if q else "probe",
                    action_purpose=q.hypothesis if q else "Probe execution",
                    actual_outcome=summary,
                    destination_state_id=res.after_obs.state_id,
                    destination_observation_id=res.after_obs.screenshot_artifact_id,
                    status="confirmed",
                    execution_status="EXECUTED" if res.status == "SUCCESS" else res.status,
                    observed_effect=obs_effect,
                    prediction_verdict=verdict,
                    transition_verified=True,
                    confidence=1.0,
                    is_successful=True,
                    is_route_change=is_route_change,
                    is_in_page_mutation=is_state_change and not is_route_change,
                    evidence_ids=[ev_entry.evidence_id],
                )
                transitions.append(trans_record)

        probes_executed = state.get("probes_executed", 0) + 1

        # Record empirical decision snapshot
        decision_snapshots = list(state.get("decision_snapshots") or [])
        if q and res:
            decision_snapshots.append({
                "step_number": len(decision_snapshots) + 1,
                "timestamp": time.time(),
                "from_state_id": res.before_obs.state_id if res.before_obs else "initial",
                "from_url": res.before_obs.url if res.before_obs else "",
                "question_id": q.id,
                "selected_action": q.target_element or "probe",
                "probe_family": q.probe_family,
                "category": q.category,
                "hypothesis": q.hypothesis,
                "priority_score": getattr(q, "priority", 0.0),
                "observed_effect": (
                    obs_effect if (is_route_change or is_state_change) else "NO_OBSERVABLE_CHANGE"
                ) if "obs_effect" in locals() else "NO_OBSERVABLE_CHANGE",
                "prediction_verdict": verdict,
                "to_state_id": res.after_obs.state_id if res.after_obs else None,
                "to_url": res.after_obs.url if res.after_obs else "",
                "is_productive": is_productive,
            })

        # Periodic reflection (every 8 probes)
        if probes_executed > 0 and probes_executed % 8 == 0 and self.agenda:
            try:
                new_hypotheses = self.planner.reflect(
                    coverage_ledger=self.agenda.coverage,
                    recent_results=self.agenda.probe_history,
                    bug_leads=self.lead_manager.get_leads(),
                )
                for h in new_hypotheses:
                    raw_target = (h.get("target") or "").strip()
                    # Ground target against DOM observation if possible
                    grounded_target = None
                    if res and res.after_obs:
                        for c in res.after_obs.interactive_controls:
                            if raw_target == c.selector or (c.accessible_name and raw_target.lower() in c.accessible_name.lower()):
                                grounded_target = c.selector
                                break
                    if not grounded_target:
                        if raw_target.startswith(("#", ".", "[", "button", "input", "a", "select")) and " " not in raw_target:
                            grounded_target = raw_target

                    if grounded_target:
                        h_id = f"q_reflect_{len(self.agenda.questions) + 1}"
                        if h_id not in self.agenda.questions:
                            q_refl = InvestigationQuestion(
                                id=h_id,
                                category=h.get("category", "constraint"),
                                target_state_class=res.after_obs.state_class_id if (res and res.after_obs) else "general",
                                target_element=grounded_target,
                                hypothesis=h.get("hypothesis", "Explore hypothesized risk area"),
                                probe_family="controls",
                                priority=float(h.get("priority", 0.75)),
                                metadata={"grounded": True, "source_url": res.after_obs.url if res and res.after_obs else ""},
                            )
                            self.agenda.questions[h_id] = q_refl
            except Exception as refl_err:
                logger.info("[Investigator:node_record] Periodic reflection skipped: %s", refl_err)

        total_llm_calls = self.planner.llm_calls_count + self.understanding_engine.llm_calls_count
        total_llm_fallbacks = self.planner.llm_fallbacks_count + self.understanding_engine.llm_fallbacks_count
        total_llm_latency = round(self.planner.llm_total_latency + self.understanding_engine.total_latency_seconds, 3)
        if self.agenda:
            self.agenda.llm_latency_seconds = total_llm_latency

        if app_model and self.agenda:
            unexplored_q = sum(1 for item in self.agenda.questions.values() if item.status == "unexplored")
            total_q = max(len(self.agenda.questions), 1)
            app_model.investigation_uncertainty = round(unexplored_q / total_q, 3)

        # Auto-persist checkpoint
        try:
            self.save_session_checkpoint({
                **state,
                "current_state": current_obs,
                "states": states_dict,
                "transitions": transitions,
                "evidence_ledger": ledger,
                "frontier": frontier,
                "application_model": app_model,
            })
        except Exception as cp_err:
            logger.debug("[Investigator:node_record] Autosave skipped: %s", cp_err)

        return {
            "current_state": current_obs,
            "page_understanding": page_understanding,
            "application_model": app_model,
            "evidence_ledger": ledger,
            "transitions": transitions,
            "states": states_dict,
            "visited_state_signatures": visited_sigs,
            "frontier": frontier,
            "candidates_discovered": max(len(frontier), len(self.agenda.questions) if self.agenda else 0),
            "deduplicated_candidates": self.agenda.deduplicated_count if self.agenda else state.get("deduplicated_candidates", 0),
            "route_transitions_observed": route_transitions,
            "same_route_state_transitions": same_route_transitions,
            "actions_verified_effects": verified_effects,
            "decision_snapshots": decision_snapshots,
            "probes_executed": probes_executed,
            "journeys": list(self.agenda.journeys.values()) if self.agenda else [],
            "active_journey_id": self.agenda.active_journey_id if self.agenda else None,
            "browser_execution_time": round(self.agenda.browser_execution_time, 2) if self.agenda else 0.0,
            "consecutive_unproductive_probes": self.agenda.consecutive_unproductive_probes if self.agenda else 0,
            "retries_count": self.agenda.retries_count if self.agenda else 0,
            "llm_calls_count": total_llm_calls,
            "llm_fallbacks_count": total_llm_fallbacks,
            "llm_latency_seconds": total_llm_latency,
        }

    def node_evaluate_stopping(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """Evaluates stopping conditions and budgets (excluding LLM inference wait time from browser duration budget)."""
        if state.get("stopping_reason"):
            return {}

        if self.agenda:
            self.agenda.llm_latency_seconds = float(state.get("llm_latency_seconds", 0.0) or 0.0)
            stop, reason = self.agenda.should_stop()
            if stop:
                return {"stopping_reason": reason}

        actions_taken = state.get("actions_attempted", 0)
        max_actions = state.get("max_probes", 0) if "max_probes" in state else state.get("max_actions", 0)
        if max_actions > 0 and actions_taken >= max_actions:
            return {"stopping_reason": f"Probe limit reached ({actions_taken}/{max_actions})"}

        raw_elapsed = time.time() - (state.get("start_time") or time.time())
        llm_wait = float(state.get("llm_latency_seconds", 0.0) or 0.0)
        effective_elapsed = max(0.0, raw_elapsed - llm_wait)
        max_time = state.get("max_time_seconds", 300.0)
        if max_time > 0 and effective_elapsed >= max_time:
            return {"stopping_reason": f"Time limit exceeded ({effective_elapsed:.1f}s / {max_time}s)"}

        return {}

    def node_observe_current_state(self, state: InvestigationAgentState) -> Dict[str, Any]:
        obs = self.session.capture_observation(state_prefix=f"step_{state.get('actions_attempted', 0)}")
        logger.info("[Investigator] Observed state '%s' at URL %s (%d controls, %d forms)",
                    obs.state_id, obs.url, len(obs.interactive_controls), len(obs.forms))

        # Check authentication boundary
        is_auth, auth_reason = AuthenticationManager.detect_auth_boundary(obs)

        # Record evidence of state observation
        evidence_entry = EvidenceEntry(
            investigation_id=state.get("investigation_id", ""),
            source_state_id=obs.state_id,
            evidence_type="DOM_OBSERVATION",
            summary=f"Captured observation of {obs.url} with {len(obs.interactive_controls)} controls and {len(obs.forms)} forms.",
            screenshot_artifact_id=obs.screenshot_artifact_id,
            console_errors=list(self.session.console_logs),
            network_errors=list(self.session.page_errors),
        )

        ledger = list(state.get("evidence_ledger", []))
        ledger.append(evidence_entry)

        states_dict = dict(state.get("states", {}))
        states_dict[obs.state_id] = obs

        visited_sigs = list(state.get("visited_state_signatures", []))
        if obs.state_id not in visited_sigs:
            visited_sigs.append(obs.state_id)

        update = {
            "previous_state": state.get("current_state"),
            "current_state": obs,
            "states": states_dict,
            "visited_state_signatures": visited_sigs,
            "evidence_ledger": ledger,
        }

        if is_auth and not state.get("auth_verified"):
            update["auth_boundary_detected"] = True
            if auth_reason:
                signals = list(state.get("auth_signals", []))
                signals.append(auth_reason)
                update["auth_signals"] = signals

        return update

    def node_update_application_model(self, state: InvestigationAgentState) -> Dict[str, Any]:
        current_obs = state.get("current_state")
        frontier = dict(state.get("frontier", {}))
        visited_sigs = state.get("visited_state_signatures", [])
        app_model = state.get("application_model") or ApplicationUnderstandingModel()

        # Populate frontier with freshly observed links and controls
        added = FrontierManager.populate_from_observation(frontier, current_obs, visited_sigs)
        logger.info("[Investigator] Updated application model: %d new frontier candidates registered", added)

        if current_obs:
            # 1. Update landmarks
            existing_landmarks = {l.label for l in app_model.observed_landmarks}
            for h in current_obs.headings:
                if h not in existing_landmarks:
                    app_model.observed_landmarks.append(PageLandmark(role="heading", label=h))

            # 2. Update semantic controls
            existing_ctrl_selectors = {c.selector for c in app_model.semantic_controls}
            for ctrl in current_obs.interactive_controls:
                if ctrl.selector not in existing_ctrl_selectors:
                    app_model.semantic_controls.append(SemanticControlUnderstanding(
                        element_id=ctrl.element_id,
                        selector=ctrl.selector,
                        semantic_role=ctrl.role,
                        inferred_purpose=ctrl.accessible_name or ctrl.selector,
                        safety_risk="LOW",
                    ))

            # 3. Update form validations
            for f in current_obs.forms:
                fid = f.get("form_id", "form")
                req_fields = [fld.get("name") or fld.get("id") for fld in f.get("fields", []) if fld.get("required")]
                existing_f = next((fu for fu in app_model.form_understandings if fu.form_id == fid), None)
                if not existing_f:
                    app_model.form_understandings.append(FormValidationUnderstanding(
                        form_id=fid,
                        target_action=f.get("action", ""),
                        required_fields=[rf for rf in req_fields if rf],
                    ))

            # 4. Verified state tracking
            if current_obs.state_id not in app_model.verified_states:
                app_model.verified_states.append(current_obs.state_id)

            # 5. Tri-partite Epistemic Grounding: Observed Facts vs Hypotheses vs Unknowns
            fact_summary = f"Observed state '{current_obs.state_id}' on {current_obs.url} with {len(current_obs.interactive_controls)} controls and {len(current_obs.forms)} forms."
            if fact_summary not in app_model.observed_facts:
                app_model.observed_facts.append(fact_summary)

            if current_obs.has_validation_error:
                val_fact = f"Form validation constraint enforced: '{current_obs.validation_error_message}' ({current_obs.validation_error_locator})"
                if val_fact not in app_model.observed_facts:
                    app_model.observed_facts.append(val_fact)

            if current_obs.menu_open:
                menu_fact = "Navigation drawer opened: sliding menu items visible in active DOM"
                if menu_fact not in app_model.observed_facts:
                    app_model.observed_facts.append(menu_fact)

            # Update uncertainty metric
            unexplored_count = sum(1 for item in frontier.values() if item.status == FrontierStatus.DISCOVERED)
            total_items = max(len(frontier), 1)
            app_model.investigation_uncertainty = round(unexplored_count / total_items, 3)

        return {
            "frontier": frontier,
            "application_model": app_model,
        }

    def node_identify_investigation_gaps(self, state: InvestigationAgentState) -> Dict[str, Any]:
        # Evaluates what remains unexplored
        frontier = state.get("frontier", {})
        unexplored = [k for k, v in frontier.items() if v.status == FrontierStatus.DISCOVERED]
        logger.info("[Investigator] Identified %d unexplored frontier items", len(unexplored))
        return {}

    def node_select_next_action(self, state: InvestigationAgentState) -> Dict[str, Any]:
        """
        Determines next investigative move using Adaptive LLM Planning + Deterministic Safety Layer.
        If an authentication boundary is present and credentials exist, prioritization goes to AUTHENTICATE.
        Otherwise, consults the adaptive planner to choose the most informative eligible candidate.
        """
        # Priority 1: Cross authentication boundary if detected and not yet attempted
        if state.get("auth_boundary_detected") and not state.get("auth_attempted"):
            creds = state.get("auth_credentials")
            if creds:
                logger.info("[Investigator] Auth boundary detected and credentials available; proposing AUTHENTICATE")
                return {
                    "proposed_action": {
                        "action_type": "AUTHENTICATE",
                        "target": "login_form",
                        "credentials": creds,
                        "rationale": "Cross authentication boundary using authorized demo credentials",
                        "expected_observable_outcome": "Navigated to internal authenticated application area",
                        "investigation_objective": "Cross authentication gate",
                    }
                }
            else:
                logger.warning("[Investigator] Auth boundary detected but NO credentials available. Marking auth as BLOCKED.")
                frontier = dict(state.get("frontier", {}))
                frontier["auth_protected_surface"] = FrontierItem(
                    item_id="auth_protected_surface",
                    item_type="auth_boundary",
                    target=state.get("target_url", ""),
                    status=FrontierStatus.BLOCKED,
                    rationale="Authentication required but no authorized credentials provided",
                    priority_score=0.0
                )
                return {
                    "proposed_action": None,
                    "frontier": frontier,
                    "stopping_reason": "Authentication required but no authorized test credentials configured"
                }

        # Priority 2: Adaptive Planning Selection from Frontier
        current_state_id = state.get("current_state").state_id if state.get("current_state") else ""
        current_obs = state.get("current_state")
        frontier = state.get("frontier", {})

        compatible_candidates = FrontierManager.get_compatible_candidates(frontier, current_state_id, current_obs)
        fallback_candidate = FrontierManager.select_next_candidate(frontier, current_state_id, current_obs)

        if not compatible_candidates and not fallback_candidate:
            unattempted = [c for c in frontier.values() if c.status == FrontierStatus.DISCOVERED and c.attempt_count == 0]
            if unattempted:
                stopping_reason = f"Reachable frontier in current view exhausted ({len(unattempted)} candidates remain pending in other views)"
            else:
                stopping_reason = "All discovered safe candidate actions have been investigated"
            return {
                "proposed_action": None,
                "stopping_reason": stopping_reason
            }

        budget_remaining = {
            "actions_remaining": state.get("max_actions", 12) - state.get("actions_attempted", 0),
            "max_actions": state.get("max_actions", 12),
        }

        candidate, decision, strategy = self.planner.recommend_next_action(
            current_obs=current_obs,
            states=state.get("states", {}),
            recent_transitions=state.get("transitions", []),
            eligible_candidates=compatible_candidates,
            fallback_candidate=fallback_candidate,
            budget_remaining=budget_remaining,
        )

        if not candidate:
            candidate = fallback_candidate

        if not candidate:
            return {"proposed_action": None, "stopping_reason": "No compatible action available"}

        action_type = "NAVIGATE_LINK" if candidate.item_type == "page" else "CLICK_CONTROL"
        action = {
            "candidate_id": candidate.item_id,
            "action_type": action_type,
            "target": candidate.target,
            "rationale": candidate.rationale,
            "source_state_id": candidate.parent_state_id or current_state_id,
            "expected_observable_outcome": decision.expected_observable_outcome if decision else "State transition or DOM mutation",
            "investigation_objective": decision.investigation_objective if decision else candidate.rationale,
            "planning_strategy": strategy,
        }
        logger.info("[Investigator] Selected action (%s): %s on %s (objective: %s)",
                    strategy, action_type, candidate.target, action.get("investigation_objective"))

        # Track cumulative planning strategy
        prev_strategy = state.get("planning_strategy", "DETERMINISTIC")
        if prev_strategy == "DETERMINISTIC":
            run_strategy = strategy
        elif prev_strategy != strategy:
            run_strategy = "MIXED"
        else:
            run_strategy = strategy

        # Update application model with inferred domain or purpose
        app_model = state.get("application_model")
        if app_model and decision:
            if decision.inferred_domain and app_model.application_domain == "general":
                app_model.application_domain = decision.inferred_domain
            if decision.inferred_page_purpose:
                app_model.page_purpose = decision.inferred_page_purpose
            for hypo in decision.new_hypotheses:
                if hypo not in app_model.inferred_hypotheses:
                    app_model.inferred_hypotheses.append(hypo)
            for unk in decision.new_unknowns:
                if unk not in app_model.unknowns:
                    app_model.unknowns.append(unk)

        return {
            "proposed_action": action,
            "planning_strategy": run_strategy,
            "llm_calls_count": self.planner.llm_calls_count,
            "llm_fallbacks_count": self.planner.llm_fallbacks_count,
            "llm_latency_seconds": round(self.planner.llm_total_latency, 3),
            "application_model": app_model,
        }

    def node_validate_action_and_scope(self, state: InvestigationAgentState) -> Dict[str, Any]:
        action = state.get("proposed_action")
        if not action:
            return {"action_validation_passed": False}

        evaluated_count = state.get("actions_evaluated", 0) + 1

        if action.get("action_type") == "AUTHENTICATE":
            return {
                "action_validation_passed": True,
                "actions_evaluated": evaluated_count,
            }

        # Validate against safety policies and forbidden keywords
        is_safe, reason = InvestigationActionExecutor.validate_action_safety(
            action.get("action_type", ""),
            action.get("target", ""),
            action.get("rationale", "")
        )
        if not is_safe:
            logger.warning("[Investigator] Action validation rejected: %s", reason)
            frontier = dict(state.get("frontier", {}))
            c_id = action.get("candidate_id")
            if c_id and c_id in frontier:
                frontier[c_id].status = FrontierStatus.BLOCKED
                frontier[c_id].last_reason = reason
                frontier[c_id].last_failure_reason = ActionFailureReason.SAFETY_BLOCKED

            ledger = list(state.get("evidence_ledger", []))
            ledger.append(EvidenceEntry(
                investigation_id=state.get("investigation_id", ""),
                source_state_id=state.get("current_state").state_id if state.get("current_state") else "initial",
                evidence_type="SAFETY_DECISION",
                summary=f"Safety Policy Enforced: {reason}",
            ))

            return {
                "action_validation_passed": False,
                "actions_evaluated": evaluated_count,
                "frontier": frontier,
                "evidence_ledger": ledger,
                "actions_safety_blocked": state.get("actions_safety_blocked", 0) + 1,
            }

        return {
            "action_validation_passed": True,
            "actions_evaluated": evaluated_count,
        }

    def node_execute_browser_action(self, state: InvestigationAgentState) -> Dict[str, Any]:
        action = state.get("proposed_action")
        attempts = state.get("actions_attempted", 0) + 1
        successes = state.get("actions_successful", 0)
        failed = state.get("actions_failed", 0)
        blocked = state.get("actions_safety_blocked", 0)

        if not action:
            return {"actions_attempted": attempts}

        action_type = action.get("action_type")
        logger.info("[Investigator] Executing action #%d: %s on %s", attempts, action_type, action.get("target"))

        if action_type == "AUTHENTICATE":
            creds = action.get("credentials", {})
            auth_ok, auth_summary, signals = AuthenticationManager.attempt_authentication(self.session, creds)
            unlocked = []
            if auth_ok:
                successes += 1
                unlocked.append("Internal Product Catalog / Dashboard")
                logger.info("[Investigator] Authentication succeeded! Unlocked internal application.")
            else:
                failed += 1
                logger.warning("[Investigator] Authentication failed: %s", auth_summary)

            return {
                "actions_attempted": attempts,
                "actions_successful": successes,
                "actions_failed": failed,
                "actions_safety_blocked": blocked,
                "auth_attempted": True,
                "auth_verified": auth_ok,
                "auth_signals": signals,
                "protected_features_unlocked": unlocked,
                "action_execution_result": {
                    "success": auth_ok,
                    "outcome": auth_summary,
                    "failure_reason": None if auth_ok else ActionFailureReason.EXECUTION_ERROR,
                    "action_type": "AUTHENTICATE"
                }
            }

        else:
            success, outcome, failure_reason, telemetry = InvestigationActionExecutor.execute_action(
                self.session,
                action_type,
                action.get("target", "")
            )
            if success:
                successes += 1
            else:
                failed += 1
                if failure_reason == ActionFailureReason.SAFETY_BLOCKED:
                    blocked += 1

            # Update candidate status in frontier
            frontier = dict(state.get("frontier", {}))
            c_id = action.get("candidate_id")
            if c_id and c_id in frontier:
                if success:
                    frontier[c_id].status = FrontierStatus.INVESTIGATED
                else:
                    if failure_reason in (ActionFailureReason.TARGET_ABSENT, ActionFailureReason.STALE_TARGET):
                        frontier[c_id].status = FrontierStatus.STALE_INCOMPATIBLE
                    elif failure_reason == ActionFailureReason.SAFETY_BLOCKED:
                        frontier[c_id].status = FrontierStatus.BLOCKED
                    else:
                        frontier[c_id].status = FrontierStatus.INVESTIGATED
                    frontier[c_id].last_failure_reason = failure_reason
                    frontier[c_id].last_reason = outcome

                frontier[c_id].attempt_count += 1

            return {
                "actions_attempted": attempts,
                "actions_successful": successes,
                "actions_failed": failed,
                "actions_safety_blocked": blocked,
                "frontier": frontier,
                "action_execution_result": {
                    "success": success,
                    "outcome": outcome,
                    "failure_reason": failure_reason,
                    "action_type": action_type,
                    "target": action.get("target")
                }
            }

    def node_verify_action_outcome(self, state: InvestigationAgentState) -> Dict[str, Any]:
        prev_obs = state.get("current_state")
        res = state.get("action_execution_result") or {}
        is_success = bool(res.get("success"))

        transitions = list(state.get("transitions", []))
        ledger = list(state.get("evidence_ledger", []))
        states_dict = dict(state.get("states", {}))
        new_obs = prev_obs

        verified_effects = state.get("actions_verified_effects", 0)
        route_transitions = state.get("route_transitions_observed", 0)
        same_route_transitions = state.get("same_route_state_transitions", 0)

        if is_success:
            # Capture resulting observation strictly after successful action
            new_obs = self.session.capture_observation(state_prefix=f"trans_{state.get('actions_attempted', 0)}")
            
            # Check for meaningful state advance or route transition
            is_route_change = bool(prev_obs and (prev_obs.url.split('?')[0].rstrip('/') != new_obs.url.split('?')[0].rstrip('/')))
            is_state_change = bool(prev_obs and (prev_obs.state_id != new_obs.state_id))

            if is_route_change or is_state_change:
                verified_effects += 1
                is_mutation = is_state_change and not is_route_change
                if is_route_change:
                    route_transitions += 1
                else:
                    same_route_transitions += 1

                # Construct semantic outcome description and evidence
                outcome_text = res.get("outcome", "")
                ev_type = "STATE_TRANSITION"
                val_msg = None
                val_loc = None

                if new_obs.has_validation_error and not (prev_obs and prev_obs.has_validation_error):
                    ev_type = "VALIDATION_MESSAGE"
                    val_msg = new_obs.validation_error_message
                    val_loc = new_obs.validation_error_locator
                    outcome_text = f"Encountered form validation error: '{val_msg}' at locator '{val_loc}'"
                elif new_obs.menu_open and not (prev_obs and prev_obs.menu_open):
                    outcome_text = "Navigation drawer opened: navigation menu options visible in live DOM"

                trans_record = StateTransitionRecord(
                    source_state_id=prev_obs.state_id if prev_obs else "initial",
                    source_observation_id=prev_obs.screenshot_artifact_id if prev_obs else None,
                    action_type=res.get("action_type", "UNKNOWN"),
                    target_element_desc=str(res.get("target") or "authentication"),
                    action_purpose=state.get("proposed_action", {}).get("rationale", ""),
                    actual_outcome=outcome_text,
                    destination_state_id=new_obs.state_id,
                    destination_observation_id=new_obs.screenshot_artifact_id,
                    status="confirmed",
                    confidence=1.0,
                    is_successful=True,
                    is_route_change=is_route_change,
                    is_in_page_mutation=is_mutation,
                )

                # Update evidence ledger with confirmed transition
                ev_entry = EvidenceEntry(
                    investigation_id=state.get("investigation_id", ""),
                    source_state_id=prev_obs.state_id if prev_obs else "initial",
                    action_id=trans_record.transition_id,
                    evidence_type=ev_type,
                    summary=f"Confirmed transition [{trans_record.action_type}]: {outcome_text}",
                    locator=val_loc,
                    validation_message=val_msg,
                    before_state_ref=prev_obs.state_id if prev_obs else None,
                    after_state_ref=new_obs.state_id,
                    screenshot_artifact_id=new_obs.screenshot_artifact_id,
                    related_transition_ids=[trans_record.transition_id]
                )
                trans_record.evidence_ids.append(ev_entry.evidence_id)
                transitions.append(trans_record)
                ledger.append(ev_entry)
                states_dict[new_obs.state_id] = new_obs
            else:
                # Action executed successfully in browser, but NO meaningful state change occurred (e.g. repeated click, no-op)
                # DO NOT invent a transition or count as verified state change
                ev_entry = EvidenceEntry(
                    investigation_id=state.get("investigation_id", ""),
                    source_state_id=prev_obs.state_id if prev_obs else "initial",
                    evidence_type="NO_STATE_CHANGE",
                    summary=f"Action executed on '{res.get('target')}', but produced no meaningful UI state change or navigation (remained in {prev_obs.state_id if prev_obs else 'initial'}).",
                    screenshot_artifact_id=new_obs.screenshot_artifact_id if new_obs else None,
                )
                ledger.append(ev_entry)
                states_dict[new_obs.state_id] = new_obs

            # Closed-Loop Discrepancy Tracking: Compare actual outcome against LLM expectation
            expected_outcome = state.get("proposed_action", {}).get("expected_observable_outcome")
            if expected_outcome:
                if new_obs.has_validation_error and "error" not in expected_outcome.lower():
                    ledger.append(EvidenceEntry(
                        investigation_id=state.get("investigation_id", ""),
                        source_state_id=prev_obs.state_id if prev_obs else "initial",
                        evidence_type="DISCREPANCY_OBSERVED",
                        summary=f"Closed-Loop Finding: Expected '{expected_outcome}', but application enforced validation constraint: '{new_obs.validation_error_message}'.",
                        locator=new_obs.validation_error_locator,
                        validation_message=new_obs.validation_error_message,
                        screenshot_artifact_id=new_obs.screenshot_artifact_id,
                    ))
                elif not is_route_change and not is_state_change and any(k in expected_outcome.lower() for k in ("navigat", "open", "advance", "next")):
                    ledger.append(EvidenceEntry(
                        investigation_id=state.get("investigation_id", ""),
                        source_state_id=prev_obs.state_id if prev_obs else "initial",
                        evidence_type="DISCREPANCY_OBSERVED",
                        summary=f"Closed-Loop Finding: Expected '{expected_outcome}', but control produced no visible DOM or route transition.",
                        screenshot_artifact_id=new_obs.screenshot_artifact_id if new_obs else None,
                    ))
        else:
            # Action failed or blocked: DO NOT invent a destination state or increment transitions
            fail_reason = res.get("failure_reason") or ActionFailureReason.EXECUTION_ERROR
            ev_type = "SAFETY_DECISION" if fail_reason == ActionFailureReason.SAFETY_BLOCKED else "ACTION_FAILURE"
            ev_entry = EvidenceEntry(
                investigation_id=state.get("investigation_id", ""),
                source_state_id=prev_obs.state_id if prev_obs else "initial",
                evidence_type=ev_type,
                summary=f"Action Failed [{fail_reason}]: {res.get('outcome')}",
                screenshot_artifact_id=prev_obs.screenshot_artifact_id if prev_obs else None
            )
            ledger.append(ev_entry)

        return {
            "previous_state": prev_obs,
            "current_state": new_obs,
            "states": states_dict,
            "transitions": transitions,
            "evidence_ledger": ledger,
            "actions_verified_effects": verified_effects,
            "route_transitions_observed": route_transitions,
            "same_route_state_transitions": same_route_transitions,
            "action_outcome_verified": True
        }

    def node_persist_evidence_and_update_frontier(self, state: InvestigationAgentState) -> Dict[str, Any]:
        # Additional frontier updates from the new state
        current_obs = state.get("current_state")
        frontier = dict(state.get("frontier", {}))
        visited_sigs = state.get("visited_state_signatures", [])

        FrontierManager.populate_from_observation(frontier, current_obs, visited_sigs)
        return {"frontier": frontier}

    def node_evaluate_stopping_conditions(self, state: InvestigationAgentState) -> Dict[str, Any]:
        # Check budgets
        actions_taken = state.get("actions_attempted", 0)
        max_actions = state.get("max_probes", 0) if "max_probes" in state else state.get("max_actions", 0)
        elapsed = time.time() - state.get("start_time", time.time())
        max_time = state.get("max_time_seconds", 120.0)
        states_count = len(state.get("states", {}))
        max_states = state.get("max_states", 0)

        if max_actions > 0 and actions_taken >= max_actions:
            return {"stopping_reason": f"Action budget exhausted ({actions_taken}/{max_actions} actions)"}
        if max_time > 0 and elapsed >= max_time:
            return {"stopping_reason": f"Time budget exhausted ({elapsed:.1f}s / {max_time}s)"}
        if max_states > 0 and states_count >= max_states:
            return {"stopping_reason": f"State discovery target achieved ({states_count} states)"}

        return {}

    def node_generate_investigation_report(self, state: InvestigationAgentState) -> Dict[str, Any]:
        logger.info("[Investigator] Running Skeptic & Reproducer pass on bug leads...")
        raw_leads = self.lead_manager.get_leads()
        target_url = state.get("target_url", "")

        # 1. Skeptic Filter
        from app.investigation_agent.skeptic import SkepticFilter
        surviving_leads = SkepticFilter.filter_leads(raw_leads)

        # 2. Reproducer & Step Minimizer
        from app.investigation_agent.reproducer import BugReproducer
        reproducer = BugReproducer(headless=True)
        reproduced_leads = reproducer.reproduce_all(
            target_url=target_url,
            leads=surviving_leads,
            transitions=state.get("transitions", []),
            states=state.get("states", {}),
            auth_credentials=state.get("auth_credentials"),
        )

        # Merge surviving reproduced leads and rejected leads
        all_final_leads = reproduced_leads + [l for l in raw_leads if l.status == "REJECTED"]
        state["bug_leads"] = all_final_leads

        confirmed = [l for l in all_final_leads if getattr(l, "status", "") == "CONFIRMED"]
        unstable = [l for l in all_final_leads if getattr(l, "status", "") in ("UNSTABLE", "SUSPECTED", "REJECTED")]

        self.emit_event("REPRO_RESULT", {
            "total_evaluated": len(raw_leads),
            "surviving_count": len(surviving_leads),
            "confirmed_count": len(confirmed),
            "unstable_count": len(unstable),
        })

        logger.info("[Investigator] Generating final 14-section Investigation Report")
        state["unexecuted_probes"] = list(self._unexecuted_probes)
        report = InvestigationReportBuilder.build_report(state)

        from app.investigation_agent.handoff_contract import build_handoff_contract
        handoff_contract = build_handoff_contract(state, report=report)

        self.emit_event("STOPPING", {
            "status": report.overview.status.value if hasattr(report.overview.status, "value") else str(report.overview.status),
            "reason": state.get("stopping_reason", "Completed"),
            "states_discovered": len(state.get("states", {})),
            "transitions_observed": len(state.get("transitions", [])),
            "confirmed_bug_leads": len(confirmed),
        })

        # Close browser session cleanly
        self.close()
        return {
            "final_report": report,
            "bug_leads": all_final_leads,
            "handoff_contract": handoff_contract,
        }

    @staticmethod
    def export_handoff_contract(final_state: Dict[str, Any], report: Optional[Any] = None):
        """
        Frozen public interface to export the schema-validated InvestigationHandoffContract
        from a completed investigation state and report.
        """
        from app.investigation_agent.handoff_contract import build_handoff_contract
        if final_state.get("handoff_contract") is not None:
            return final_state["handoff_contract"]
        return build_handoff_contract(final_state, report=report or final_state.get("final_report"))

    # --- Routing Logic ---

    @classmethod
    def should_continue_investigation(cls, state: InvestigationAgentState) -> str:
        """
        Conditional router: Decides whether to loop back for another investigative action
        or proceed to generate the final report.
        """
        stopping_reason = state.get("stopping_reason")
        if stopping_reason:
            return "generate_investigation_report"

        actions_taken = state.get("actions_attempted", 0)
        max_actions = state.get("max_probes", 0) if "max_probes" in state else state.get("max_actions", 0)
        if max_actions > 0 and actions_taken >= max_actions:
            return "generate_investigation_report"

        # Continue loop
        return "select_next_action"


def build_investigation_workflow(orchestrator: InvestigationOrchestrator):
    """
    Constructs and compiles the hypothesis-driven LangGraph investigation graph:
    perceive -> agenda_update -> select_question -> plan_probe -> safety_gate -> execute_probe -> perceive_after_and_compare -> record -> evaluate_stopping
    """
    builder = StateGraph(InvestigationAgentState)

    # v2 node cycle
    builder.add_node("initialize_investigation", orchestrator.node_initialize)
    builder.add_node("perceive", orchestrator.node_perceive)
    builder.add_node("agenda_update", orchestrator.node_agenda_update)
    builder.add_node("select_question", orchestrator.node_select_question)
    builder.add_node("plan_probe", orchestrator.node_plan_probe)
    builder.add_node("safety_gate", orchestrator.node_safety_gate)
    builder.add_node("execute_probe", orchestrator.node_execute_probe)
    builder.add_node("perceive_after_and_compare", orchestrator.node_perceive_after_and_compare)
    builder.add_node("record", orchestrator.node_record)
    builder.add_node("evaluate_stopping", orchestrator.node_evaluate_stopping)
    builder.add_node("generate_investigation_report", orchestrator.node_generate_investigation_report)

    # Legacy node registrations (retained for backward compatibility)
    builder.add_node("observe_current_state", orchestrator.node_observe_current_state)
    builder.add_node("update_application_model", orchestrator.node_update_application_model)
    builder.add_node("identify_investigation_gaps", orchestrator.node_identify_investigation_gaps)
    builder.add_node("select_next_action", orchestrator.node_select_next_action)
    builder.add_node("validate_action_and_scope", orchestrator.node_validate_action_and_scope)
    builder.add_node("execute_browser_action", orchestrator.node_execute_browser_action)
    builder.add_node("verify_action_outcome", orchestrator.node_verify_action_outcome)
    builder.add_node("persist_evidence_and_update_frontier", orchestrator.node_persist_evidence_and_update_frontier)
    builder.add_node("evaluate_stopping_conditions", orchestrator.node_evaluate_stopping_conditions)

    # Graph Wiring
    builder.set_entry_point("initialize_investigation")
    builder.add_edge("initialize_investigation", "perceive")
    builder.add_edge("perceive", "agenda_update")
    builder.add_edge("agenda_update", "select_question")

    def check_question_available(state: InvestigationAgentState) -> str:
        if state.get("stopping_reason") or not state.get("current_question"):
            return "generate_investigation_report"
        return "plan_probe"

    builder.add_conditional_edges(
        "select_question",
        check_question_available,
        {
            "plan_probe": "plan_probe",
            "generate_investigation_report": "generate_investigation_report",
        }
    )

    builder.add_edge("plan_probe", "safety_gate")

    def check_safety(state: InvestigationAgentState) -> str:
        if not state.get("action_validation_passed"):
            if state.get("stopping_reason"):
                return "generate_investigation_report"
            return "evaluate_stopping"
        return "execute_probe"

    builder.add_conditional_edges(
        "safety_gate",
        check_safety,
        {
            "execute_probe": "execute_probe",
            "evaluate_stopping": "evaluate_stopping",
            "generate_investigation_report": "generate_investigation_report",
        }
    )

    builder.add_edge("execute_probe", "perceive_after_and_compare")
    builder.add_edge("perceive_after_and_compare", "record")
    builder.add_edge("record", "evaluate_stopping")

    def check_continue(state: InvestigationAgentState) -> str:
        if state.get("stopping_reason"):
            return "generate_investigation_report"
        return "select_question"

    builder.add_conditional_edges(
        "evaluate_stopping",
        check_continue,
        {
            "select_question": "select_question",
            "generate_investigation_report": "generate_investigation_report",
        }
    )

    builder.add_edge("generate_investigation_report", END)

    return builder.compile()


# Compatibility aliases
InvestigationGraphState = InvestigationAgentState
build_investigation_graph = build_investigation_workflow

