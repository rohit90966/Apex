"""
EVALUATOR-ONLY ground truth. Never imported by the agent (app/*) or passed to it.

Each planted defect is matched against a finding by *what the finding tested* (oracle class, field
or control, input class) rather than by keywords in its prose. One ground-truth defect counts once
per target and mode, however many findings point at it.
"""

from typing import Any, Callable, Dict, List

Finding = Dict[str, Any]


def _field(f: Finding) -> str:
    return ((f.get("derived_values") or {}).get("field_selector") or "")


def _control(f: Finding) -> str:
    return ((f.get("derived_values") or {}).get("control") or "")


def _input_class(f: Finding) -> str:
    return ((f.get("derived_values") or {}).get("input_class") or "")


def _text(f: Finding) -> str:
    return " ".join(str(f.get(k) or "") for k in ("name", "failure_reason", "actual_behavior")).lower()


def _actions(f: Finding) -> str:
    """What the finding's test did (its name and steps), excluding page text echoed in failure messages."""
    return " ".join([str(f.get("name") or "")] + [str(s) for s in f.get("steps") or []]).lower()


def _expected_anomaly(f: Finding, token: str) -> bool:
    import re
    return bool(re.search(rf"\b{re.escape(token)}\b", " ".join(str(f.get(k) or "") for k in ("name", "failure_reason"))))


GROUND_TRUTH: Dict[str, List[Dict[str, Any]]] = {
    "harbor_events": [
        {"id": "HE-01", "title": "Email format not validated by the server on registration", "route": "/register",
         "match": lambda f: _field(f) == "#email" and _input_class(f) == "FORMAT_INVALID"},
        {"id": "HE-02", "title": "Password confirmation mismatch accepted", "route": "/register",
         "match": lambda f: _field(f) == "#confirm-password" or _input_class(f) == "MISMATCH" or "mismatch" in _actions(f)},
        {"id": "HE-03", "title": "Price sort orders formatted text instead of amounts", "route": "/events",
         "match": lambda f: f.get("oracle_class") == "LIST_ORDERING" and _control(f) == "#sort" and "price" in _actions(f)},
        {"id": "HE-04", "title": "Featured events ignore the category filter", "route": "/events",
         "match": lambda f: f.get("oracle_class") == "FILTER_CONSISTENCY" and _control(f) == "#category"},
        {"id": "HE-05", "title": "Ticket quantity range not enforced by the server", "route": "/events/1",
         "match": lambda f: _field(f) == "#quantity" and _input_class(f) not in ("", "EMPTY")},
        {"id": "HE-06", "title": "Cancelling a booking does not change its status", "route": "/bookings",
         "match": lambda f: "cancel" in _actions(f)},
        {"id": "HE-07", "title": "Price breakdown renders NaN service fee", "route": "/events/1",
         "match": lambda f: _expected_anomaly(f, "NaN") or "breakdown" in _actions(f)},
    ],
    # Legacy fixtures (archive/legacy_demo_sites), kept as unrelated applications for generalization checks.
    "apex_portal": [
        {"id": "BUG-01", "title": "Password minimum length not enforced on login", "route": "/login",
         "match": lambda f: _field(f) == "#password" and (_input_class(f) == "TOO_SHORT" or "length" in _actions(f))},
        {"id": "BUG-02", "title": "Terms of Service acceptance not enforced on registration", "route": "/registration",
         "match": lambda f: _field(f) == "#terms"},
        {"id": "BUG-03", "title": "Password confirmation mismatch accepted", "route": "/registration",
         "match": lambda f: _field(f) == "#confirm-password" or "mismatch" in _actions(f)},
        {"id": "BUG-04", "title": "Empty search query accepted without validation", "route": "/search",
         "match": lambda f: _field(f) in ("#q", '[name="q"]') and _input_class(f) in ("EMPTY", "WHITESPACE")},
    ],
    "taskdash": [
        {"id": "BUG-F2-01", "title": "Empty task title accepted", "route": "/create",
         "match": lambda f: _field(f) == "#title" and _input_class(f) in ("EMPTY", "WHITESPACE")},
        {"id": "BUG-F2-02", "title": "Priority filter ignores the selected priority", "route": "/tasks",
         "match": lambda f: f.get("oracle_class") == "FILTER_CONSISTENCY" and _control(f) == "#priority-filter"},
        {"id": "BUG-F2-03", "title": "Mark Done does not update task status", "route": "/tasks",
         "match": lambda f: f.get("oracle_class") not in ("FILTER_CONSISTENCY", "LIST_ORDERING")
                            and ("mark done" in _actions(f) or "/complete" in _actions(f))},
    ],
    "benchmark_shop": [
        {"id": "BUG-BENCH-01", "title": "Tax calculation renders NaN", "route": "/cart",
         "match": lambda f: _expected_anomaly(f, "NaN") or "calculate-tax" in _actions(f)},
        {"id": "BUG-BENCH-02", "title": "Cart badge not updated after removing an item", "route": "/cart",
         "match": lambda f: "remove-item" in _actions(f) and "badge" in _text(f)},
    ],
}

# Modes in which the planted defects are present.
DEFECT_MODES = {"buggy"}


def defects_for(target_key: str) -> List[Dict[str, Any]]:
    return GROUND_TRUTH.get(target_key, [])


def has_ground_truth(target_key: str) -> bool:
    return target_key in GROUND_TRUTH
