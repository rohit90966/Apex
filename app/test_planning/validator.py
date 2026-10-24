"""
Deterministic Grounded Test Validator for VerityQA.

Enforces the target independence contract on every candidate test case:
  * every interaction selector was observed on this target during this investigation,
  * every navigation stays on the investigated origin and targets an observed route,
  * every literal expectation (URL fragment, visible text) was observed on this target, entered
    by the test itself, or stated in an explicit requirement,
  * the case has at least one meaningful oracle (not 'body is visible'),
  * a case named/declared as an empty-field test really submits an empty value,
  * the case passes the safety governor,
  * semantically duplicate cases are dropped.
Rejected cases are returned with a reason instead of being silently repaired.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.intelligence.target_context import ObservedInventory, canonical_origin, route_of
from app.investigation_agent.governor import SafetyGovernor
from app.schemas import ActionType, AssertionType, TestCase, TestCaseStatus, is_meaningful_assertion

logger = logging.getLogger(__name__)

CREDENTIAL_PLACEHOLDERS = {"${CREDENTIAL_PASSWORD}", "${CREDENTIAL_USERNAME}"}
_INTERACTIONS = (ActionType.CLICK, ActionType.FILL, ActionType.SELECT)


class GroundedTestValidator:
    """
    Strict deterministic validator ensuring tests are executable, grounded in the current
    target's evidence, carry a meaningful oracle, and are safe.
    """

    def __init__(
        self,
        target_base_url: str = "",
        governor: Optional[SafetyGovernor] = None,
        inventory: Optional[ObservedInventory] = None,
        requirements: Optional[List[str]] = None,
    ):
        self.target_base_url = target_base_url.rstrip("/")
        self.origin = canonical_origin(target_base_url)
        self.governor = governor or SafetyGovernor()
        self.inventory = inventory
        self.requirements = [r.lower() for r in (requirements or []) if r]
        self._selectors: Set[str] = inventory.selectors() if inventory else set()

    # ── individual rules ──────────────────────────────────────────────────
    def _text_grounded(self, text: str, test_case: TestCase) -> bool:
        needle = (text or "").strip().lower()
        if not needle:
            return False
        typed = [str(s.value).lower() for s in test_case.steps if s.action == ActionType.FILL and s.value]
        if any(needle in v or v in needle for v in typed if v):
            return True
        if any(needle in r for r in self.requirements):
            return True
        return bool(self.inventory and self.inventory.text_observed(needle))

    def _check_grounding(self, test_case: TestCase) -> Optional[str]:
        if self.inventory is None:
            return None
        if test_case.target_origin and self.origin and test_case.target_origin != self.origin:
            return f"Case belongs to origin {test_case.target_origin}, not {self.origin}."
        for step in test_case.steps:
            if step.action in _INTERACTIONS and step.selector not in self._selectors:
                return f"Step {step.step_number} selector '{step.selector}' was not observed on this target."
            if step.action == ActionType.NAVIGATE:
                url = step.value or ""
                if canonical_origin(url) != self.origin:
                    return f"Step {step.step_number} navigates outside the investigated origin: {url}"
                if not self.inventory.route_observed(route_of(url)):
                    return f"Step {step.step_number} navigates to route '{route_of(url)}' that was never observed."
            if step.action == ActionType.SELECT:
                control = next((c for c in self.inventory.controls if c.selector == step.selector), None)
                field_opts = [o for form in self.inventory.forms for f in form.fields if f.selector == step.selector for o in f.options]
                options = (control.options + control.option_labels) if control else field_opts
                if options and step.value not in options:
                    return f"Step {step.step_number} selects option '{step.value}' that was not observed on '{step.selector}'."
            a = step.assertion
            if a is None:
                continue
            if a.assertion_type in (AssertionType.URL_CONTAINS, AssertionType.URL_EQUALS):
                if not self.inventory.route_observed(a.expected_value or ""):
                    return f"Step {step.step_number} expects URL '{a.expected_value}' that was never observed on this target."
            if a.assertion_type in (AssertionType.TEXT_VISIBLE,) and a.expected_source != "DERIVED_INFERENCE":
                if not self._text_grounded(a.expected_value or "", test_case):
                    return f"Step {step.step_number} expects text '{a.expected_value}' that is not grounded in this target's evidence."
            if a.target_selector and a.target_selector.lower() not in ("body", "html") \
                    and a.assertion_type not in (AssertionType.ELEMENT_NOT_VISIBLE, AssertionType.TEXT_NOT_VISIBLE) \
                    and a.target_selector not in self._selectors:
                return f"Step {step.step_number} asserts on selector '{a.target_selector}' that was not observed."
        return None

    @staticmethod
    def _check_intent(test_case: TestCase) -> Optional[str]:
        """The declared input class of a field-level case must match what the steps actually enter."""
        derived = test_case.derived_values or {}
        field_sel = derived.get("field_selector")
        if field_sel and "input_value" in derived:
            fills = [s for s in test_case.steps if s.action == ActionType.FILL and s.selector == field_sel]
            if not fills:
                return f"Case declares a test of '{field_sel}' but never fills it."
            if fills[-1].value != derived["input_value"]:
                return (f"Case declares input {derived['input_value']!r} for '{field_sel}' "
                        f"but fills {fills[-1].value!r}.")
            if derived.get("input_class") == "EMPTY" and (fills[-1].value or "") != "":
                return "Case is declared as an empty-value test but fills a non-empty value."
        name = test_case.name.lower()
        if any(k in name for k in ("empty", "blank")) and field_sel is None:
            if not any(s.action == ActionType.FILL and (s.value or "").strip() == "" for s in test_case.steps):
                return "Case is named as an empty-field test but no field is left empty."
        return None

    @staticmethod
    def _check_assertions(test_case: TestCase) -> Optional[str]:
        assertions = [s.assertion for s in test_case.steps if s.assertion is not None]
        if not assertions:
            return "Test case contains 0 assertions. Zero-assertion tests cannot count as valid executable tests."
        for a in assertions:
            if a.assertion_type == AssertionType.CUSTOM:
                return "CUSTOM assertions have no executable oracle."
            if a.assertion_type == AssertionType.LIST_SORTED:
                try:
                    spec = json.loads(a.expected_value or "")
                except ValueError:
                    return "LIST_SORTED assertion has no machine-readable ordering rule."
                if spec.get("key") not in ("text", "number") or spec.get("direction") not in ("asc", "desc"):
                    return "LIST_SORTED assertion ordering rule is incomplete."
        if test_case.test_kind != "EXPLORATORY" and not any(is_meaningful_assertion(a) for a in assertions):
            return "Test case has no meaningful oracle (only trivially-true assertions)."
        return None

    def validate_test_case(
        self,
        test_case: TestCase,
        discovered_controls: Optional[Set[str]] = None,
        discovered_urls: Optional[Set[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validates a single TestCase. Returns (is_valid, rejection_reason)."""
        if not test_case.steps:
            return False, "Test case contains 0 action steps."

        reason = self._check_assertions(test_case)
        if reason:
            return False, reason

        for step in test_case.steps:
            act = step.action
            if act in _INTERACTIONS:
                if not step.selector or not step.selector.strip():
                    return False, f"Step {step.step_number} ({act.value}) has an empty selector."
                is_safe, why = self.governor.can_execute(
                    action_type=act.value,
                    target=step.selector,
                    current_url=self.target_base_url,
                    dry_run=True,
                )
                if not is_safe:
                    return False, f"Step {step.step_number} violates safety policy: {why}"
            if act == ActionType.FILL and step.value is None:
                return False, f"Step {step.step_number} fills '{step.selector}' without a value."
            if act == ActionType.SELECT and not step.value:
                return False, f"Step {step.step_number} selects on '{step.selector}' without an option value."

        if test_case.steps[0].action not in (ActionType.NAVIGATE, ActionType.CLICK, ActionType.FILL):
            return False, f"First step cannot be {test_case.steps[0].action.value}."

        reason = self._check_intent(test_case) or self._check_grounding(test_case)
        if reason:
            return False, reason
        return True, None

    @staticmethod
    def semantic_signature(test_case: TestCase) -> str:
        parts = []
        for s in test_case.steps:
            a = s.assertion
            parts.append("|".join([
                s.action.value, s.selector or "", s.value or "",
                a.assertion_type.value if a else "", (a.target_selector or "") if a else "", (a.expected_value or "") if a else "",
            ]))
        return "\n".join(parts)

    def validate_suite(
        self,
        test_cases: List[TestCase],
        target_count_min: int = 15,
        target_count_max: int = 40,
    ) -> Tuple[List[TestCase], List[Tuple[TestCase, str]]]:
        """Validates all test cases, splitting them into valid and rejected (with reasons)."""
        valid_cases: List[TestCase] = []
        rejected_cases: List[Tuple[TestCase, str]] = []
        seen_signatures: Set[str] = set()

        for tc in test_cases:
            is_valid, reason = self.validate_test_case(tc)
            if not is_valid:
                tc.status = TestCaseStatus.INVALID
                tc.invalid_reason = reason
                rejected_cases.append((tc, reason or "Validation failure"))
                continue
            sig = self.semantic_signature(tc)
            if sig in seen_signatures:
                rejected_cases.append((tc, "Duplicate action and oracle sequence"))
                continue
            seen_signatures.add(sig)
            tc.status = TestCaseStatus.PROPOSED
            tc.invalid_reason = None
            valid_cases.append(tc)

        return valid_cases, rejected_cases

    @staticmethod
    def rejection_summary(rejected: List[Tuple[TestCase, str]]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for _, reason in rejected:
            key = reason.split("'")[0].split(":")[0].strip()[:80]
            out[key] = out.get(key, 0) + 1
        return out
