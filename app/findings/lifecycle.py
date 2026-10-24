"""
Finding lifecycle: classifications, how evidence maps onto them, and how they may change.

Classifications (strongest first):
  CONFIRMED_DEFECT   reproduced failure against an *established* expectation (explicit requirement,
                     browser-enforced HTML constraint, or behaviour observed earlier in this run), or an
                     investigation lead reproduced 2-of-2 by a technical/contradiction oracle
  LIKELY_DEFECT      reproduced failure against a *strong but not established* expectation (HTML declaration
                     checked server-side, semantic field convention, generic invariant, accessibility heuristic,
                     investigation lead from a declared/convention oracle)
  UNVERIFIED_ANOMALY reproduced deviation from an inferred expectation (option-label semantics, LLM proposal)
  INCONCLUSIVE       failure not reproduced, or the oracle could not observe what it needs
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

from typing import Optional

CONFIRMED = "CONFIRMED_DEFECT"
LIKELY = "LIKELY_DEFECT"
ANOMALY = "UNVERIFIED_ANOMALY"
INCONCLUSIVE = "INCONCLUSIVE"
INFRA = "TEST_INFRASTRUCTURE_FAILURE"
DEFINITION = "TEST_DEFINITION_ERROR"
BLOCKED = "BLOCKED"
PASSED = "PASSED"

RANK = {CONFIRMED: 6, LIKELY: 5, ANOMALY: 4, INCONCLUSIVE: 3, INFRA: 2, DEFINITION: 2, BLOCKED: 1, PASSED: 0}
DEFECT_CLASSES = {CONFIRMED, LIKELY, ANOMALY}

# expected_source of the failing assertion -> classification of a reproduced failure
ESTABLISHED_SOURCES = {"REQUIREMENT", "HTML_CONSTRAINT", "OBSERVED"}
STRONG_SOURCES = {"HTML_DECLARED", "SEMANTIC_CONVENTION", "GENERIC_INVARIANT", "ACCESSIBILITY_HEURISTIC",
                  "INVESTIGATION_DECLARED", "INVESTIGATION_CONVENTION"}


def classify_reproduced_failure(expected_source: Optional[str]) -> str:
    if expected_source in ESTABLISHED_SOURCES:
        return CONFIRMED
    if expected_source in STRONG_SOURCES:
        return LIKELY
    return ANOMALY


def classify_investigation_lead(status: str, oracle_type: Optional[str]) -> Optional[str]:
    """Investigation leads only become findings once the reproducer confirmed them."""
    if str(status) != "CONFIRMED":
        return None
    oracle = str(getattr(oracle_type, "value", oracle_type) or "").upper()
    if oracle in ("TECHNICAL", "CONTRADICTION"):
        return CONFIRMED
    if oracle in ("DECLARED", "CONVENTION"):
        return LIKELY
    return ANOMALY


def stronger(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if a is None:
        return b
    if b is None:
        return a
    return a if RANK.get(a, -1) >= RANK.get(b, -1) else b
