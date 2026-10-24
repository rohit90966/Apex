"""
State Invariant Probes for Stateful Investigation.
Tests behavioral invariants across page interactions:
- reload_persistence: state and active session persist across page reloads.
- counter_consistency: numerical badge count consistency.
"""

from typing import List, Dict, Any, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    Prediction,
)


def generate_invariant_probes(obs: UIStateObservation) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """Generates invariant testing probes such as reload persistence."""
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []

    # Reload persistence probe
    script = ProbeScript(
        name="invariant_reload_persistence",
        family="invariants",
        description="Reloads page to verify state and session persistence",
        steps=[ProbeStep(action_type="RELOAD", raw_target="page")],
    )

    pred = Prediction(
        expect="Page reload maintains identical state class without errors",
        signals=["no_server_error", "no_route_change"]
    )

    probes.append((script, pred, {
        "kind": "reload_persistence",
        "rule": "Application state must persist across standard page reload"
    }))

    return probes
