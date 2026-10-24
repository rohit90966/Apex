"""
app/investigation_agent/probes/accessibility.py
===============================================
Accessibility probe generator: ACCESSIBLE_NAMES strategy.

For each observed interactive control, produces a lightweight probe that checks:
1. The element has a non-empty accessible name (from aria-label, aria-labelledby,
   associated <label>, or title).
2. No two sibling controls in the same container share the exact same accessible name
   unless they are genuinely equivalent alternatives (e.g. two "Close" buttons at
   different end-of-DOM positions).

This implements the ACCESSIBLE_NAMES planner strategy key and the TS-A11Y-NAMES
knowledge entry.  The probe is read-only (no mutation) and safe for any target.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    Prediction,
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40]


def generate_accessibility_probes(
    obs: UIStateObservation,
) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """
    Generate accessibility-check probes for the current page observation.

    Returns a list of (script, prediction, metadata) triples, at most one per
    route (we group controls by route so the probe stays page-scoped).
    """
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []
    controls = getattr(obs, "interactive_controls", []) or []
    if not controls:
        return probes

    route = getattr(obs, "normalized_route", "/") or "/"
    route_slug = _slugify(route) or "root"

    # Classify controls by name for duplicate-name detection.
    names: Dict[str, List[str]] = {}  # name -> [selector, ...]
    unnamed: List[str] = []

    for ctrl in controls:
        name = (getattr(ctrl, "accessible_name", None) or "").strip()
        selector = getattr(ctrl, "selector", "") or ""
        if not name:
            unnamed.append(selector)
        else:
            names.setdefault(name, []).append(selector)

    duplicate_names = {n: sels for n, sels in names.items() if len(sels) > 1}
    issues: List[str] = []

    if unnamed:
        issues.append(f"{len(unnamed)} interactive control(s) lack an accessible name: {unnamed[:5]}")
    if duplicate_names:
        for n, sels in list(duplicate_names.items())[:3]:
            issues.append(f"Duplicate accessible name '{n}' on: {sels[:3]}")

    if not issues:
        return probes  # Clean page — no a11y probe needed

    # Build a read-only OBSERVE_STATE probe that just captures the page state.
    steps = [
        ProbeStep(
            action_type="OBSERVE_STATE",
            raw_target=route,
        )
    ]
    script = ProbeScript(
        name=f"a11y_names_{route_slug}",
        family="accessibility",
        description=f"Accessible-name audit on {route}: {len(unnamed)} unnamed, {len(duplicate_names)} duplicate-named controls",
        steps=steps,
    )
    pred = Prediction(
        expect="All interactive controls have distinct accessible names",
        signals=["no_a11y_violation"],
    )
    meta: Dict[str, Any] = {
        "kind": "accessibility_names",
        "route": route,
        "unnamed_count": len(unnamed),
        "unnamed_selectors": unnamed[:10],
        "duplicate_names": {n: sels for n, sels in list(duplicate_names.items())[:5]},
        "issues": issues,
        "oracle_type": "A11Y_HEURISTIC",
        "knowledge_ids": ["TS-A11Y-NAMES", "DC-A11Y-NAMES"],
    }
    probes.append((script, pred, meta))
    return probes
