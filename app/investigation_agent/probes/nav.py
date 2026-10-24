"""
Navigation and Route Probes for Stateful Investigation.
Tests link health and route transitions:
- Verifies same-origin navigation links do not produce 4xx/5xx or broken page views.
- Tests unauthenticated vs authenticated boundaries.
"""

from typing import List, Dict, Any, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    Prediction,
)


def generate_nav_probes(obs: UIStateObservation) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """Generates navigation link probes for same-origin links."""
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []

    for link in obs.navigation_links[:10]:
        href = link.get("href")
        if not href or link.get("is_external") or link.get("is_fragment"):
            continue

        text = link.get("text") or href
        lower_combined = f"{text} {href}".lower()
        if any(w in lower_combined for w in ("logout", "log-out", "log out", "signout", "sign-out", "sign out", "reset app")):
            continue
        script = ProbeScript(
            name=f"nav_link_{hash(href) % 100000}",
            family="nav",
            description=f"Navigates to same-origin link '{text}' ({href})",
            steps=[ProbeStep(action_type="NAVIGATE_LINK", raw_target=href)],
        )

        pred = Prediction(
            expect=f"Successfully loads route {href} without 4xx/5xx errors",
            signals=["no_server_error", "status_2xx"]
        )

        probes.append((script, pred, {
            "kind": "link_health",
            "href": href,
            "text": text,
            "rule": f"Navigation link '{text}' must resolve to a valid route"
        }))

    return probes
