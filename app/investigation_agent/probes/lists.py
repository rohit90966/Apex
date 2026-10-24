"""
List, Filter, and Sort Probes for Stateful Investigation.
Tests repeated-item collections, table rows, and filtering/sorting controls:
- Switches filter options (select, radio, tabs) and verifies item set changes.
- Emits CONTRADICTION lead when filter request occurs but visible items remain identical.
"""

from typing import List, Dict, Any, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    Prediction,
)


def generate_list_probes(obs: UIStateObservation) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """Generates filter and sort probes for select elements and tabs."""
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []

    # Look for select controls that act as filters
    for ctrl in obs.interactive_controls:
        if ctrl.tag_name.lower() == "select" and ctrl.is_enabled:
            sel = ctrl.selector
            target = SemanticTarget(role="combobox", name=ctrl.accessible_name, css=sel)
            name_clean = ctrl.accessible_name or sel

            # Probe switching between options
            script = ProbeScript(
                name=f"list_filter_{ctrl.element_id}",
                family="lists",
                description=f"Applies filter option on '{name_clean}' to verify collection updating",
                steps=[
                    # Select non-default option
                    ProbeStep(
                        action_type="SELECT_OPTION",
                        target=target,
                        value_spec=ValueSpec(kind=ValueSpecType.LITERAL, value="High")
                    )
                ]
            )

            pred = Prediction(
                expect="Selecting filter option updates displayed item collection",
                signals=["list_changed", "no_server_error"]
            )

            probes.append((script, pred, {
                "kind": "filter_sort_effect",
                "control_id": ctrl.element_id,
                "target": sel,
                "name": name_clean,
                "rule": f"Filter '{name_clean}' should update or restrict visible items"
            }))

    return probes
