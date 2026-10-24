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
    "apex_portal": [
        {"id": "BUG-01", "title": "Password minimum length not enforced on login",
         "match": lambda f: f.get("oracle_class") == "FIELD_CONSTRAINT" and _field(f) == "#password" and _input_class(f) == "TOO_SHORT"},
        {"id": "BUG-02", "title": "Terms of Service acceptance not enforced on registration",
         "match": lambda f: f.get("oracle_class") == "FIELD_CONSTRAINT" and _field(f) == "#terms"},
        {"id": "BUG-03", "title": "Password confirmation mismatch accepted",
         "match": lambda f: _field(f) == "#confirm-password" or "mismatch" in _actions(f)},
        {"id": "BUG-04", "title": "Empty search query accepted without validation",
         "match": lambda f: f.get("oracle_class") == "FIELD_CONSTRAINT" and _field(f) == "#q" and _input_class(f) in ("EMPTY", "WHITESPACE")},
    ],
    "taskdash": [
        {"id": "BUG-F2-01", "title": "Empty task title accepted",
         "match": lambda f: f.get("oracle_class") == "FIELD_CONSTRAINT" and _field(f) == "#title" and _input_class(f) in ("EMPTY", "WHITESPACE")},
        {"id": "BUG-F2-02", "title": "Priority filter ignores the selected priority",
         "match": lambda f: f.get("oracle_class") == "FILTER_CONSISTENCY" and _control(f) == "#priority-filter"},
        {"id": "BUG-F2-03", "title": "Mark Done does not update task status",
         "match": lambda f: f.get("oracle_class") not in ("FILTER_CONSISTENCY", "LIST_ORDERING")
                            and ("mark done" in _actions(f) or "/complete" in _actions(f))},
    ],
    "benchmark_shop": [
        {"id": "BUG-BENCH-01", "title": "Tax calculation renders NaN",
         "match": lambda f: _expected_anomaly(f, "NaN") or "calculate-tax" in _actions(f)},
        {"id": "BUG-BENCH-02", "title": "Cart badge not updated after removing an item",
         "match": lambda f: "remove-item" in _actions(f) and "badge" in _text(f)},
    ],
}

# Modes in which the planted defects are present.
DEFECT_MODES = {"buggy"}


def defects_for(target_key: str) -> List[Dict[str, Any]]:
    return GROUND_TRUTH.get(target_key, [])


def has_ground_truth(target_key: str) -> bool:
    return target_key in GROUND_TRUTH
