"""
Frozen Public Interface for the VerityQA Autonomous Investigation Engine (v1.0.0-frozen).
=========================================================================================
STATUS: FEATURE-FROZEN FOR MILESTONE HANDOFF TO TEST-CASE GENERATION.

Public Entry Points:
1. `run_stateful_investigation(...) -> InvestigationReport`
2. `run_investigation_with_handoff(...) -> Tuple[InvestigationReport, InvestigationHandoffContract]`
3. `export_investigation_handoff(final_state, report=None) -> InvestigationHandoffContract`
"""

import asyncio
from typing import Optional, Dict, Any, List, Callable, Tuple
from app.investigation_agent.models import InvestigationReport
from app.investigation_agent.state import InvestigationAgentState
from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from app.investigation_agent.handoff_contract import (
    CONTRACT_SCHEMA_VERSION,
    InvestigationHandoffContract,
    build_handoff_contract,
)

INVESTIGATION_ENGINE_FROZEN: bool = True
INVESTIGATION_ENGINE_VERSION: str = "1.0.0-frozen"


def export_investigation_handoff(
    final_state: Dict[str, Any],
    report: Optional[InvestigationReport] = None,
) -> InvestigationHandoffContract:
    """
    Stable public interface to export a versioned, validated InvestigationHandoffContract
    from a completed investigation run without exposing internal LangGraph runtime state.
    """
    return InvestigationOrchestrator.export_handoff_contract(final_state, report=report)


async def run_investigation_with_handoff(
    url: str,
    description: Optional[str] = None,
    requirements: Optional[List[str]] = None,
    auth_credentials: Optional[Dict[str, str]] = None,
    max_actions: int = 0,
    max_time_seconds: float = 300.0,
    headless: Optional[bool] = None,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    resume_checkpoint: Optional[str] = None,
    exploration_policy: str = "balanced",
    sandbox_mode: bool = False,
    sandbox_origins: Optional[List[str]] = None,
) -> Tuple[InvestigationReport, InvestigationHandoffContract]:
    """
    Frozen public entry point that runs an investigation and returns both
    the 14-section InvestigationReport and the versioned InvestigationHandoffContract.
    """
    def _sync_run() -> Tuple[InvestigationReport, InvestigationHandoffContract]:
        orchestrator = InvestigationOrchestrator(
            headless=headless,
            event_callback=event_callback,
            sandbox_mode=sandbox_mode,
            sandbox_origins=sandbox_origins,
        )
        graph = build_investigation_workflow(orchestrator)

        initial_state: InvestigationAgentState = {
            "target_url": url,
            "user_description": description,
            "user_requirements": requirements,
            "auth_credentials": auth_credentials,
            "max_actions": max_actions,
            "max_probes": max_actions,
            "max_time_seconds": max_time_seconds,
            "max_states": 20,
            "resume_checkpoint": resume_checkpoint,
            "exploration_policy": exploration_policy,
            "sandbox_mode": sandbox_mode,
            "sandbox_origins": list(sandbox_origins or []),
        }

        final_state = graph.invoke(initial_state)
        report = final_state.get("final_report")
        contract = export_investigation_handoff(final_state, report=report)
        return report, contract

    return await asyncio.to_thread(_sync_run)


async def run_stateful_investigation(
    url: str,
    description: Optional[str] = None,
    requirements: Optional[List[str]] = None,
    auth_credentials: Optional[Dict[str, str]] = None,
    max_actions: int = 0,
    max_time_seconds: float = 300.0,
    headless: Optional[bool] = None,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    resume_checkpoint: Optional[str] = None,
    investigation_id: Optional[str] = None,
) -> InvestigationReport:
    """
    Executes the autonomous, stateful, authentication-aware investigation workflow.
    max_actions <= 0 enables adaptive exploration without a fixed probe ceiling.
    resume_checkpoint allows resuming a paused/interrupted investigation from disk.
    investigation_id, when given, is used as the investigation ID (e.g. the persisted run ID).
    Returns ONLY the InvestigationReport.
    Does NOT invoke test planning, test execution, or defect triage.
    """
    def _sync_run():
        orchestrator = InvestigationOrchestrator(headless=headless, event_callback=event_callback)
        graph = build_investigation_workflow(orchestrator)

        initial_state: InvestigationAgentState = {
            "target_url": url,
            "user_description": description,
            "user_requirements": requirements,
            "auth_credentials": auth_credentials,
            "max_actions": max_actions,
            "max_probes": max_actions,
            "max_time_seconds": max_time_seconds,
            "max_states": 20,
            "resume_checkpoint": resume_checkpoint,
        }
        if investigation_id:
            initial_state["investigation_id"] = investigation_id

        final_state = graph.invoke(initial_state)
        return final_state.get("final_report")

    # Run in worker thread to prevent blocking FastAPI async event loop
    report = await asyncio.to_thread(_sync_run)
    return report
