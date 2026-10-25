"""
Finding lifecycle: classifications, how evidence maps onto them, and how they may change.

Classifications (strongest first):
  CONFIRMED_DEFECT   reproduced failure against an *established* expectation (explicit requirement,
                     browser-enforced HTML constraint, or behaviour observed earlier in this run), or an
                     investigation lead reproduced 2-of-2 by a technical/contradiction oracle
  LIKELY_DEFECT      reproduced failure against a *strong but not established* expectation (HTML declaration
                     checked server-side, semantic field convention, generic invariant, accessibility heuristic,
                     investigation lead from a declared/convention oracle)
  UNVERIFIED_ANOMALY deviation from an inferred expectation (option-label semantics, LLM proposal), or a lead
                     observed but not reproduced
  INCONCLUSIVE       failure not reproduced, oracle could not observe what it needs, or lead rejected
  TEST_INFRASTRUCTURE_FAILURE  locator/automation/environment problems: never an application defect
  TEST_DEFINITION_ERROR        invalid test
  BLOCKED            not executed (e.g. missing authorized credentials)
  PASSED             verification passed

Promotion rules:
  * evidence only promotes: a finding's classification is the strongest supported by any of its sources;
  * a later passing or inconclusive execution is recorded as an observation but never erases a confirmed or
    likely investigation finding (different oracles answer different questions);
  * an anomaly becomes LIKELY/CONFIRMED only when a stronger-sourced test or lead reproduces it.
"""

from enum import Enum
from typing import Any, Optional


class FindingClass(str, Enum):
    CONFIRMED_DEFECT = "CONFIRMED_DEFECT"
    LIKELY_DEFECT = "LIKELY_DEFECT"
    UNVERIFIED_ANOMALY = "UNVERIFIED_ANOMALY"
    INCONCLUSIVE = "INCONCLUSIVE"
    TEST_INFRASTRUCTURE_FAILURE = "TEST_INFRASTRUCTURE_FAILURE"
    TEST_DEFINITION_ERROR = "TEST_DEFINITION_ERROR"
    BLOCKED = "BLOCKED"
    PASSED = "PASSED"


CONFIRMED = FindingClass.CONFIRMED_DEFECT.value
LIKELY = FindingClass.LIKELY_DEFECT.value
ANOMALY = FindingClass.UNVERIFIED_ANOMALY.value
INCONCLUSIVE = FindingClass.INCONCLUSIVE.value
INFRA = FindingClass.TEST_INFRASTRUCTURE_FAILURE.value
DEFINITION = FindingClass.TEST_DEFINITION_ERROR.value
BLOCKED = FindingClass.BLOCKED.value
PASSED = FindingClass.PASSED.value

RANK = {CONFIRMED: 6, LIKELY: 5, ANOMALY: 4, INCONCLUSIVE: 3, INFRA: 2, DEFINITION: 2, BLOCKED: 1, PASSED: 0}
DEFECT_CLASSES = {CONFIRMED, LIKELY, ANOMALY}

# expected_source of the failing assertion -> classification of a reproduced failure
ESTABLISHED_SOURCES = {"REQUIREMENT", "HTML_CONSTRAINT", "OBSERVED"}
STRONG_SOURCES = {"HTML_DECLARED", "SEMANTIC_CONVENTION", "GENERIC_INVARIANT", "ACCESSIBILITY_HEURISTIC",
                  "INVESTIGATION_DECLARED", "INVESTIGATION_CONVENTION"}

_INFRA_STATUSES = {"LOCATOR_ERROR", "AUTOMATION_ERROR", "ENVIRONMENT_ERROR", "ERROR"}


def _value(x: Any) -> str:
    return str(getattr(x, "value", x) or "")


def classify_reproduced_failure(expected_source: Optional[str]) -> FindingClass:
    if expected_source in ESTABLISHED_SOURCES:
        return FindingClass.CONFIRMED_DEFECT
    if expected_source in STRONG_SOURCES:
        return FindingClass.LIKELY_DEFECT
    return FindingClass.UNVERIFIED_ANOMALY


def failing_expected_source(result: Any, test_case: Any) -> Optional[str]:
    """expected_source of the assertion that failed (the result's failure_step), else the case's first one."""
    steps = list(getattr(test_case, "steps", None) or [])
    failure_step = getattr(result, "failure_step", None)
    chosen = None
    for step in steps:
        assertion = step.get("assertion") if isinstance(step, dict) else getattr(step, "assertion", None)
        if assertion is None:
            continue
        source = assertion.get("expected_source") if isinstance(assertion, dict) else getattr(assertion, "expected_source", None)
        num = step.get("step_number") if isinstance(step, dict) else getattr(step, "step_number", None)
        if failure_step is not None and num == failure_step:
            return source
        chosen = chosen or source
    return chosen


def classify_execution_result(result: Any, test_case: Any) -> FindingClass:
    """
    Classification of one execution outcome before reproduction. A FAILED result is classified by the
    strength of the expectation it violated; the bug investigator's retries decide reproducibility.
    """
    status = _value(getattr(result, "status", ""))
    if status == "PASSED":
        return FindingClass.PASSED
    if status in _INFRA_STATUSES:
        return FindingClass.TEST_INFRASTRUCTURE_FAILURE
    if status == "TEST_DEFINITION_ERROR":
        return FindingClass.TEST_DEFINITION_ERROR
    if status in ("BLOCKED", "SKIPPED"):
        return FindingClass.BLOCKED
    if status in ("FAILED", "PRODUCT_BEHAVIOR_DEVIATION"):
        return classify_reproduced_failure(failing_expected_source(result, test_case))
    return FindingClass.INCONCLUSIVE


def classify_investigation_lead(lead: Any, oracle_type: Any = None) -> FindingClass:
    """
    Accepts a lead object/dict, or (status, oracle_type). Only reproducer-confirmed leads become defects;
    observed-but-unconfirmed leads are anomalies; rejected leads are inconclusive.
    """
    if isinstance(lead, str):
        status, oracle = lead, oracle_type
    elif isinstance(lead, dict):
        status, oracle = lead.get("status"), lead.get("oracle_type")
    else:
        status, oracle = getattr(lead, "status", None), getattr(lead, "oracle_type", None)
    status, oracle = _value(status).upper(), _value(oracle).upper()
    if status == "CONFIRMED":
        if oracle in ("TECHNICAL", "CONTRADICTION"):
            return FindingClass.CONFIRMED_DEFECT
        if oracle in ("DECLARED", "CONVENTION"):
            return FindingClass.LIKELY_DEFECT
        return FindingClass.UNVERIFIED_ANOMALY
    if status in ("UNSTABLE", "SUSPECTED", "LEAD", "SETUP_BLOCKED"):  # observed, not verified, not disproven
        return FindingClass.UNVERIFIED_ANOMALY
    return FindingClass.INCONCLUSIVE


def is_product_finding(classification: Optional[str]) -> bool:
    """Reproduced findings about the application (not infrastructure, definitions, or inconclusive outcomes)."""
    return _value(classification) in (CONFIRMED, LIKELY)


def stronger(a: Optional[str], b: Optional[str]) -> Optional[str]:
    a, b = (_value(a) or None), (_value(b) or None)
    if a is None:
        return b
    if b is None:
        return a
    return a if RANK.get(a, -1) >= RANK.get(b, -1) else b
