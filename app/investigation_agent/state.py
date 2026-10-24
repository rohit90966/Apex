"""
State TypedDict for the LangGraph Investigation Agent workflow.
"""

from typing import Dict, List, Optional, Any, TypedDict
from app.investigation_agent.models import (
    UIStateObservation,
    StateTransitionRecord,
    FrontierItem,
    RuleOrHypothesis,
    EvidenceEntry,
    InvestigationReport,
    InvestigationStatus,
    ApplicationUnderstandingModel,
)


class InvestigationAgentState(TypedDict, total=False):
    # Session identifiers & user inputs
    investigation_id: str
    target_url: str
    user_description: Optional[str]
    user_requirements: Optional[List[str]]
    auth_credentials: Optional[Dict[str, str]]

    # Runtime budgets & telemetry
    start_time: float
    browser_execution_time: float
    max_actions: int
    max_time_seconds: float
    max_states: int
    candidates_discovered: int
    actions_evaluated: int
    actions_attempted: int
    actions_successful: int
    actions_verified_effects: int
    actions_failed: int
    actions_safety_blocked: int
    retries_count: int
    route_transitions_observed: int
    same_route_state_transitions: int
    deduplicated_candidates: int
    action_failures: List[Dict[str, Any]]
    planning_strategy: str                          # "LLM_ADAPTIVE", "DETERMINISTIC_FALLBACK", "MIXED"
    llm_calls_count: int
    llm_fallbacks_count: int
    llm_latency_seconds: float
    consecutive_unproductive_probes: int
    resume_checkpoint: Optional[str]

    # Application state graph & Understanding Model
    current_state: Optional[UIStateObservation]
    previous_state: Optional[UIStateObservation]
    states: Dict[str, UIStateObservation]           # state_id -> UIStateObservation
    transitions: List[StateTransitionRecord]
    application_model: Optional[ApplicationUnderstandingModel]

    # Investigation Frontier & Memory
    frontier: Dict[str, FrontierItem]               # item_id -> FrontierItem
    visited_state_signatures: List[str]
    attempted_action_keys: List[str]

    # Evidence & Rules
    evidence_ledger: List[EvidenceEntry]
    rules_and_hypotheses: List[RuleOrHypothesis]

    # Authentication tracking
    auth_boundary_detected: bool
    auth_attempted: bool
    auth_verified: bool
    auth_signals: List[str]
    protected_features_unlocked: List[str]

    # Active Loop Control
    proposed_action: Optional[Dict[str, Any]]
    action_validation_passed: bool
    action_execution_result: Optional[Dict[str, Any]]
    action_outcome_verified: bool

    # Hypothesis & Agenda Probing (v2)
    agenda: Optional[Any]
    current_question: Optional[Any]
    current_probe: Optional[Any]
    current_probe_result: Optional[Any]
    probe_verdict: Optional[str]
    probe_summary: Optional[str]
    probe_comparison: Optional[Any]
    page_understanding: Optional[Any]
    bug_leads: Optional[List[Any]]
    journeys: Optional[List[Any]]
    active_journey_id: Optional[str]
    probes_executed: int
    max_probes: int
    max_mutations: int
    mutations_count: int
    safety_block_count: int
    decision_snapshots: Optional[List[Dict[str, Any]]]
    exploration_policy: Optional[str]
    sandbox_mode: Optional[bool]
    sandbox_origins: Optional[List[str]]
    handoff_contract: Optional[Any]
    # Probes that never executed their action (wrong page, navigation failure, safety/budget block)
    unexecuted_probes: Optional[List[Dict[str, Any]]]

    # Termination
    stopping_reason: str
    investigation_status: InvestigationStatus
    final_report: Optional[InvestigationReport]
