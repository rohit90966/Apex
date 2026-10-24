"""
Knowledge-Guided, Evidence-Grounded Test Synthesis Engine.

Inputs  : one investigation report (its TargetContext + ObservedInventory) and optional explicit
          requirements.
Outputs : TestCases whose every selector, route, option value and literal expectation is grounded
          in the same investigation of the same origin, plus planning diagnostics (rejections,
          inapplicable patterns, unknown constraints, exploratory hypotheses).

Domain knowledge (app/knowledge/patterns) contributes *strategies*: which kinds of behaviour are
worth testing for the capabilities the target actually exposes. Concrete selectors, routes,
option values and expected texts always come from the current target's evidence. There are no
site-specific defaults: if something was not observed, no test depends on it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.intelligence.archetypes import (
    ApplicationCapability,
    ApplicationIntelligenceModel,
    ArchetypeClassifier,
)
from app.intelligence.target_context import (
    PROVENANCE_LEGACY,
    ObservedControl,
    ObservedField,
    ObservedForm,
    ObservedInventory,
    canonical_origin,
    inventory_from_report,
    is_instance_route,
    route_of,
)
from app.knowledge.retriever_v2 import KnowledgePatternRetriever, RetrievedKnowledgePattern
from app.llm.base import BaseLLMProvider
from app.llm.factory import get_llm_provider
from app.schemas import (
    ActionStep,
    ActionType,
    AssertionType,
    ExecutionAssertion,
    ExpectedBehaviorSource,
    PageCategory,
    Priority,
    TestCase,
    TestCaseStatus,
)
from app.test_planning.coverage import CoverageReport, MultiDimensionalCoverageTracker
from app.test_planning.field_constraints import (
    EXPECT_ACCEPT,
    SOURCE_REQUIREMENT,
    BoundaryCase,
    align_requirements,
    plan_form_boundaries,
    valid_filler,
)
from app.test_planning.validator import GroundedTestValidator

logger = logging.getLogger(__name__)

CREDENTIAL_PASSWORD_PLACEHOLDER = "${CREDENTIAL_PASSWORD}"
CREDENTIAL_USERNAME_PLACEHOLDER = "${CREDENTIAL_USERNAME}"

_TEXT_ANOMALIES = ("NaN", "undefined", "[object Object]", "Traceback", "Exception")
_SORT_WORDS = {"sort", "sorting", "order", "orderby", "sortby"}
_FILTER_WORDS = {"filter", "status", "priority", "category", "type", "tag", "state"}


# ── Sort option semantics ─────────────────────────────────────────────────────

def parse_sort_option(value: str, label: str = "") -> Optional[Tuple[str, str]]:
    """
    Derives (key, direction) from an observed sort option's label/value, e.g.
    'Price (low to high)' -> ('number', 'asc'), 'Name (Z to A)' -> ('text', 'desc').
    Returns None when the ordering rule cannot be determined (the option is then only an
    exploratory hypothesis).
    """
    text = f" {label} {value} ".lower()
    direction = None
    if re.search(r"\ba\s*(?:-|to)\s*z\b|\baz\b|\bascending\b|\basc\b|\blow(?:est)?\b.*\bhigh|\blo\s*-?\s*hi\b|\blohi\b|\boldest\b|\bearliest\b", text):
        direction = "asc"
    if re.search(r"\bz\s*(?:-|to)\s*a\b|\bza\b|\bdescending\b|\bdesc\b|\bhigh(?:est)?\b.*\blow|\bhi\s*-?\s*lo\b|\bhilo\b|\bnewest\b|\blatest\b", text):
        direction = None if direction == "asc" else "desc"
    if direction is None:
        return None
    if re.search(r"price|cost|amount|total|\blohi\b|\bhilo\b|\blo\s*-?\s*hi\b|\bhi\s*-?\s*lo\b|low|high", text):
        key = "number"
    elif re.search(r"name|title|alpha|\baz\b|\bza\b|a\s*(?:-|to)\s*z|z\s*(?:-|to)\s*a", text):
        key = "text"
    else:
        return None  # e.g. date/priority orderings need an explicit rule
    return key, direction


def _words(*texts: str) -> Set[str]:
    out: Set[str] = set()
    for t in texts:
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t or "")  # loginBtn -> login Btn
        out.update(w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w)
    return out


_RULE_KEYWORDS = {
    "REQUIRED": ["required", "enter", "provide", "missing", "empty", "blank"],
    "MIN_LENGTH": ["characters", "character", "length", "short", "least"],
    "MAX_LENGTH": ["characters", "character", "length", "long", "most"],
    "EXACT_LENGTH": ["characters", "character", "length"],
    "EXACT_DIGITS": ["digits", "digit", "numeric"],
}


def rule_keywords(constraint: Any, field: Optional[ObservedField]) -> List[str]:
    """Words a validation message must contain to be attributable to this field/rule."""
    words = set()
    if field is not None:
        words |= {w for w in _words(field.label, field.name, field.field_id) if len(w) >= 3}
    words |= set(_RULE_KEYWORDS.get(constraint.kind, []))
    if constraint.value and str(constraint.value).isdigit():
        words.add(str(constraint.value))
    return sorted(words)


class KnowledgeGuidedTestPlanner:
    """
    Synthesizes target-grounded test suites from an investigation report.
    `last_diagnostics` describes what was rejected or left as uncertain, and why.
    """

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
        retriever: Optional[KnowledgePatternRetriever] = None,
        classifier: Optional[ArchetypeClassifier] = None,
    ):
        self._llm_provider = llm_provider
        self.retriever = retriever or KnowledgePatternRetriever()
        self.classifier = classifier or ArchetypeClassifier()
        self.last_diagnostics: Dict[str, Any] = {}

    @property
    def llm_provider(self) -> Optional[BaseLLMProvider]:
        if self._llm_provider is None:
            try:
                self._llm_provider = get_llm_provider()
            except Exception as e:
                logger.warning("Could not initialize default LLM provider: %s", e)
        return self._llm_provider

    # ── Public API ────────────────────────────────────────────────────────
    def plan_test_suite_sync(
        self,
        report: Any,
        target_count: int = 30,
    ) -> Tuple[List[TestCase], CoverageReport, ApplicationIntelligenceModel]:
        """Deterministic, evidence-grounded synthesis pipeline (no LLM)."""
        ctx = self._build_context(report)
        candidates = self._synthesize_candidates(ctx)
        return self._finalize(ctx, candidates, target_count)

    async def plan_test_suite(
        self,
        report: Any,
        target_count: int = 30,
    ) -> Tuple[List[TestCase], CoverageReport, ApplicationIntelligenceModel]:
        """Deterministic synthesis plus optional LLM proposals; LLM output must pass the same grounding validator."""
        ctx = self._build_context(report)
        candidates = self._synthesize_candidates(ctx)
        if len(candidates) < target_count and self.llm_provider and ctx["inventory"].controls:
            started = time.time()
            ctx["diag"]["llm_used"] = True
            try:
                candidates.extend(await self._synthesize_llm_scenarios(ctx, target_count - len(candidates)))
            except Exception as e:
                ctx["diag"]["llm_error"] = f"{type(e).__name__}: {str(e)[:200]}"
                logger.warning("[TestSynthesis] LLM scenario synthesis skipped: %s", e)
            ctx["diag"]["llm_latency_seconds"] = round(time.time() - started, 2)
        return self._finalize(ctx, candidates, target_count)

    # ── Context ───────────────────────────────────────────────────────────
    def _build_context(self, report: Any) -> Dict[str, Any]:
        overview = getattr(report, "overview", None)
        target_url = (getattr(overview, "target_url", "") if overview is not None else "") or getattr(report, "target_url", "") or ""
        target_url = target_url.rstrip("/")
        inventory = inventory_from_report(report)
        tctx = getattr(report, "target_context", None) or {}
        requirements = list(dict.fromkeys(
            [r for r in (tctx.get("requirements") if isinstance(tctx, dict) else []) or [] if r] + list(inventory.requirements)
        ))
        intel_model = self.classifier.classify_from_investigation(report)
        retrieved = self.retriever.retrieve_patterns(intel_model, max_patterns=30)
        run_id = inventory.run_id or (getattr(overview, "investigation_id", "") if overview is not None else "")
        diagnostics: Dict[str, Any] = {
            "run_id": run_id,
            "target_origin": inventory.origin,
            "inventory_provenance": inventory.provenance,
            "archetype": intel_model.primary_archetype.value,
            "secondary_archetypes": [a.value for a in intel_model.secondary_archetypes],
            "capabilities": [c.value for c in intel_model.capabilities],
            "capability_evidence": intel_model.capability_evidence,
            "unknown_capabilities": intel_model.unknown_capabilities,
            "retrieved_patterns": [r.pattern.pattern_id for r in retrieved],
            "ineligible_patterns": [r.model_dump() for r in self.retriever.last_rejected],
            "inapplicable_templates": [],
            "unknown_constraints": [],
            "unmapped_requirements": [],
            "exploratory_hypotheses": [],
            "unreachable_routes": [],
            "rejected_cases": [],
        }
        return {
            "report": report,
            "target_url": target_url,
            "origin": canonical_origin(target_url) or inventory.origin,
            "inventory": inventory,
            "requirements": requirements,
            "model": intel_model,
            "retrieved": retrieved,
            "run_id": run_id,
            "diag": diagnostics,
            "counters": {},
        }

    # ── Shared step builders ──────────────────────────────────────────────
    @staticmethod
    def _url(ctx: Dict[str, Any], route: str) -> str:
        return f"{ctx['origin']}{route if route.startswith('/') else '/' + route}"

    def _auth_prelude(self, ctx: Dict[str, Any]) -> Optional[List[ActionStep]]:
        """Login steps built from the login form observed in this run; None when not possible."""
        inv: ObservedInventory = ctx["inventory"]
        form = inv.login_form()
        if form is None or not inv.auth.credentials_available or not form.submit_selector:
            return None
        user = next((f for f in form.fields if f.field_type in ("text", "email")), None)
        pwd = next((f for f in form.fields if f.field_type == "password"), None)
        if not user or not pwd:
            return None
        landing = None
        for tr in inv.transitions:
            if tr.action_type.upper().startswith("AUTHENTICATE") and tr.destination_state_id:
                dest = inv.state(tr.destination_state_id)
                if dest and dest.route != form.route:
                    landing = dest.route
                    break
        assertion = (
            ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value=landing,
                               description=f"Authenticated landing route {landing} (observed)", expected_source="OBSERVED")
            if landing else
            ExecutionAssertion(assertion_type=AssertionType.ELEMENT_NOT_VISIBLE, target_selector=pwd.selector,
                               description="Login form dismissed after authentication", expected_source="OBSERVED")
        )
        return [
            ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, form.route),
                       description=f"Open observed login route {form.route}"),
            ActionStep(step_number=0, action=ActionType.FILL, selector=user.selector, value=CREDENTIAL_USERNAME_PLACEHOLDER,
                       description="Enter authorized username (resolved at run time from origin-scoped credentials)"),
            ActionStep(step_number=0, action=ActionType.FILL, selector=pwd.selector, value=CREDENTIAL_PASSWORD_PLACEHOLDER,
                       description="Enter authorized password (resolved at run time from origin-scoped credentials)"),
            ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                       description="Submit observed login form", assertion=assertion),
        ]

    def _entry_steps(self, ctx: Dict[str, Any], route: str) -> Optional[Tuple[List[ActionStep], bool]]:
        """Steps that reach `route`; (steps, needs_auth). None when the route cannot be reached."""
        inv: ObservedInventory = ctx["inventory"]
        nav = ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, route),
                         description=f"Navigate to observed route {route}")
        if inv.requires_auth(route):
            prelude = self._auth_prelude(ctx)
            if prelude is None:
                ctx["diag"]["unreachable_routes"].append({"route": route, "reason": "only observed while authenticated; no login form/credentials for this origin"})
                return None
            return [s.model_copy() for s in prelude] + [nav], True
        return [nav], False

    def _next_id(self, ctx: Dict[str, Any], prefix: str) -> str:
        n = ctx["counters"].get(prefix, 0) + 1
        ctx["counters"][prefix] = n
        return f"TC-{prefix}-{n:02d}"

    def _case(self, ctx: Dict[str, Any], prefix: str, name: str, objective: str, steps: List[ActionStep],
              oracle_class: str, source: ExpectedBehaviorSource, *, needs_auth: bool = False,
              priority: Priority = Priority.MEDIUM, evidence_ids: Sequence[str] = (), rule_ids: Sequence[str] = (),
              derived: Optional[Dict[str, Any]] = None, category: PageCategory = PageCategory.GENERAL,
              expected_outcome: str = "", reason: str = "", preconditions: Sequence[str] = (),
              confidence: float = 0.9) -> TestCase:
        numbered = [s.model_copy(update={"step_number": i + 1}) for i, s in enumerate(steps)]
        return TestCase(
            test_id=self._next_id(ctx, prefix),
            name=name,
            objective=objective,
            page_category=category,
            relevant_elements=list(dict.fromkeys(s.selector for s in numbered if s.selector)),
            action_description="; ".join(s.description for s in numbered)[:500],
            expected_outcome=expected_outcome,
            expected_behavior_source=source,
            priority=priority,
            preconditions=list(preconditions) + (["Authorized credentials for this origin"] if needs_auth else []),
            requires_test_account=needs_auth,
            selection_reason=reason or f"Derived from evidence observed in investigation {ctx['run_id']}",
            confidence=confidence,
            status=TestCaseStatus.PROPOSED,
            steps=numbered,
            evidence_ids=list(dict.fromkeys(evidence_ids))[:8],
            rule_ids=list(rule_ids),
            applied_pattern_ids=list(rule_ids),
            oracle_class=oracle_class,
            derived_values=derived,
            investigation_id=ctx["run_id"] or None,
            target_origin=ctx["origin"] or None,
        )

    # ── Strategy: route reachability (observed regression) ───────────────
    def _route_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        cases: List[TestCase] = []
        for route in inv.navigable_routes()[:8]:
            states = [s for s in inv.states if s.route == route and s.reached_by != "FORM_SUBMISSION"]
            statuses = [s.http_status for s in states if s.http_status]
            headings = inv.stable_headings(route)
            entry = self._entry_steps(ctx, route)
            if entry is None:
                continue
            steps, needs_auth = entry
            if statuses and max(statuses) < 400:
                steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                    assertion_type=AssertionType.STATUS_CODE, expected_value="<400",
                    description=f"Route responds without HTTP error (observed {statuses[0]})", expected_source="OBSERVED")})
            if headings:
                steps.append(ActionStep(step_number=0, action=ActionType.OBSERVE_STATE, description="Observe rendered landmarks",
                                        assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=headings[0],
                                                                     description=f"Stable heading '{headings[0]}' observed on every visit",
                                                                     expected_source="OBSERVED")))
            if not any(s.assertion for s in steps[len(steps) - 2:]):
                continue
            cases.append(self._case(
                ctx, "ROUTE", f"Route reachable with stable landmarks: {route}",
                f"Verify {route} remains reachable and renders the landmarks observed during investigation.",
                steps, "OBSERVED_STATE", ExpectedBehaviorSource.OBSERVED_INVARIANT, needs_auth=needs_auth,
                priority=Priority.LOW, rule_ids=[pattern_id],
                evidence_ids=[s.state_id for s in states],
                expected_outcome=f"HTTP status < 400{' and heading ' + repr(headings[0]) if headings else ''}",
            ))
        return cases

    # ── Strategy: observed state transitions ─────────────────────────────
    def _transition_cases(self, ctx: Dict[str, Any]) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        selectors = inv.selectors()
        cases: List[TestCase] = []
        seen: Set[Tuple[str, str]] = set()
        for tr in inv.transitions:
            act = tr.action_type.upper()
            if not any(k in act for k in ("CLICK", "CONTROL")) or any(k in act for k in ("FORM", "FILL", "SUBMIT", "AUTH")):
                continue
            src, dst = inv.state(tr.source_state_id), inv.state(tr.destination_state_id or "")
            if not src or not dst or tr.target not in selectors:
                continue
            if src.route not in inv.navigable_routes() or (src.route, tr.target) in seen:
                continue
            if src.reached_by == "FORM_SUBMISSION":
                continue  # the control lives on a page produced by a submission, not on the route itself
            if not any(c.selector == tr.target and c.route == src.route for c in inv.controls):
                continue
            if dst.route != src.route and not is_instance_route(dst.route):
                assertion = ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value=dst.route,
                                               description=f"Transition reaches {dst.route} (observed)", expected_source="OBSERVED")
            else:
                new_headings = [h for h in dst.headings if h not in src.headings]
                if not new_headings:
                    continue
                assertion = ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=new_headings[0],
                                               description=f"Transition reveals '{new_headings[0]}' (observed)", expected_source="OBSERVED")
            entry = self._entry_steps(ctx, src.route)
            if entry is None:
                continue
            steps, needs_auth = entry
            ctrl = next(c for c in inv.controls if c.selector == tr.target and c.route == src.route)
            steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=tr.target,
                                    description=f"Activate '{ctrl.name or tr.target}'", assertion=assertion))
            seen.add((src.route, tr.target))
            cases.append(self._case(
                ctx, "TR", f"Transition: '{(ctrl.name or tr.target)[:40]}' on {src.route}",
                f"Verify activating '{ctrl.name or tr.target}' on {src.route} produces the observed state change.",
                steps, "STATE_TRANSITION", ExpectedBehaviorSource.OBSERVED_INVARIANT, needs_auth=needs_auth,
                evidence_ids=tr.evidence_ids or [tr.transition_id],
                expected_outcome=assertion.description,
            ))
            if len(cases) >= 8:
                break
        return cases

    # ── Strategy: sorting ─────────────────────────────────────────────────
    def _select_sources(self, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every observed select with its options, location, and (when inside a form) the submit control."""
        inv: ObservedInventory = ctx["inventory"]
        out: List[Dict[str, Any]] = []
        seen: Set[Tuple[str, str]] = set()
        for form in inv.forms:
            if not inv.reproducible(form):
                continue
            for f in form.fields:
                if f.field_type.startswith("select") and len(f.options) >= 2 and (form.route, f.selector) not in seen:
                    seen.add((form.route, f.selector))
                    labels = next((c.option_labels for c in inv.controls if c.selector == f.selector and c.route == form.route), [])
                    out.append({"selector": f.selector, "route": form.route, "options": f.options, "labels": labels,
                                "words": _words(f.name, f.field_id, f.label), "form": form, "state_id": form.state_id})
        for c in inv.controls:
            if c.tag == "select" and len(c.options) >= 2 and (c.route, c.selector) not in seen and c.route and inv.reproducible(c):
                seen.add((c.route, c.selector))
                out.append({"selector": c.selector, "route": c.route, "options": c.options, "labels": c.option_labels,
                            "words": _words(c.selector, c.name), "form": None, "state_id": c.state_id})
        return out

    def _sort_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        cases: List[TestCase] = []
        for src in self._select_sources(ctx):
            if not (src["words"] & _SORT_WORDS):
                continue
            for i, opt in enumerate(src["options"]):
                label = src["labels"][i] if i < len(src["labels"]) else ""
                rule = parse_sort_option(opt, label)
                if rule is None:
                    ctx["diag"]["exploratory_hypotheses"].append({
                        "control": src["selector"], "route": src["route"], "option": opt, "label": label,
                        "reason": "ordering rule not derivable from the option label; needs an explicit requirement"})
                    continue
                entry = self._entry_steps(ctx, src["route"])
                if entry is None:
                    break
                steps, needs_auth = entry
                key, direction = rule
                assertion = ExecutionAssertion(
                    assertion_type=AssertionType.LIST_SORTED,
                    expected_value=json.dumps({"key": key, "direction": direction}),
                    description=f"Records ordered by {key} {direction} as option '{label or opt}' states",
                    expected_source="DERIVED_INFERENCE")
                steps.append(ActionStep(step_number=0, action=ActionType.SELECT, selector=src["selector"], value=opt,
                                        description=f"Choose sort option '{label or opt}'",
                                        assertion=None if src["form"] else assertion))
                if src["form"] is not None:
                    if not src["form"].submit_selector:
                        continue
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=src["form"].submit_selector,
                                            description="Apply sort", assertion=assertion))
                cases.append(self._case(
                    ctx, "SORT", f"Sort by '{label or opt}' orders records ({key}, {direction})",
                    f"Select '{label or opt}' on {src['selector']} and verify the listed records follow that order.",
                    steps, "LIST_ORDERING", ExpectedBehaviorSource.INFERRED, needs_auth=needs_auth,
                    rule_ids=[pattern_id], evidence_ids=[src["state_id"]],
                    derived={"control": src["selector"], "option": opt, "option_label": label, "key": key,
                             "direction": direction, "rule_source": "option label semantics (derived inference)"},
                    expected_outcome=assertion.description,
                ))
        return cases

    # ── Strategy: filtering ───────────────────────────────────────────────
    def _filter_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        cases: List[TestCase] = []
        for src in self._select_sources(ctx):
            if src["words"] & _SORT_WORDS:
                continue
            form = src["form"]
            is_filter = bool(src["words"] & _FILTER_WORDS) or (form is not None and form.method == "GET")
            if not is_filter or (form is not None and form.method != "GET"):
                continue
            used = 0
            for i, opt in enumerate(src["options"]):
                label = src["labels"][i] if i < len(src["labels"]) else opt
                if not opt.strip() or opt.strip().lower() in ("all", "any", "none", "--", "select"):
                    continue
                entry = self._entry_steps(ctx, src["route"])
                if entry is None:
                    break
                steps, needs_auth = entry
                expected = label or opt
                assertion = ExecutionAssertion(
                    assertion_type=AssertionType.LIST_ALL_CONTAIN, expected_value=expected,
                    description=f"Every listed record shows '{expected}' after filtering", expected_source="DERIVED_INFERENCE")
                steps.append(ActionStep(step_number=0, action=ActionType.SELECT, selector=src["selector"], value=opt,
                                        description=f"Choose filter option '{expected}'",
                                        assertion=None if form else assertion))
                if form is not None:
                    if not form.submit_selector:
                        break
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                                            description="Apply filter", assertion=assertion))
                cases.append(self._case(
                    ctx, "FILTER", f"Filter '{src['selector']}' = '{expected}' shows only matching records",
                    f"Apply filter option '{expected}' and verify every listed record carries that value.",
                    steps, "FILTER_CONSISTENCY", ExpectedBehaviorSource.INFERRED, needs_auth=needs_auth,
                    rule_ids=[pattern_id], evidence_ids=[src["state_id"]],
                    derived={"control": src["selector"], "option": opt, "option_label": label,
                             "rule_source": "filter semantics (derived inference)"},
                    expected_outcome=assertion.description,
                ))
                used += 1
                if used >= 2:
                    break
        return cases

    # ── Strategy: field-level boundaries ──────────────────────────────────
    @staticmethod
    def _boundary_assertion(bc: BoundaryCase, field: Optional[ObservedField] = None) -> ExecutionAssertion:
        src = "REQUIREMENT" if bc.constraint.source == SOURCE_REQUIREMENT else "HTML_CONSTRAINT"
        why = f"{bc.constraint.source}: {bc.constraint.source_detail}"
        # Feedback counts only when attributable to this field/rule (the field itself is invalid, or a
        # visible message names the field or the rule) — an unrelated error is not this rule's verdict.
        keywords = json.dumps(rule_keywords(bc.constraint, field))
        if bc.oracle == "FIELD_VALIDITY":
            return ExecutionAssertion(assertion_type=AssertionType.FIELD_VALIDITY, target_selector=bc.field_selector,
                                      expected_value=bc.oracle_expected, expected_source=src,
                                      description=f"Field validity is '{bc.oracle_expected}' ({why})")
        if bc.oracle == "VALUE_LENGTH":
            return ExecutionAssertion(assertion_type=AssertionType.ELEMENT_VALUE_LENGTH, target_selector=bc.field_selector,
                                      expected_value=bc.oracle_expected, expected_source=src,
                                      description=f"Field value length {bc.oracle_expected} ({why})")
        if bc.oracle_expected == "rejected":
            return ExecutionAssertion(assertion_type=AssertionType.VALIDATION_FEEDBACK_SHOWN, target_selector=bc.field_selector,
                                      expected_value=keywords, expected_source=src,
                                      description=f"Submission rejected with feedback attributable to this rule ({why})")
        return ExecutionAssertion(assertion_type=AssertionType.NO_VALIDATION_FEEDBACK, target_selector=bc.field_selector,
                                  expected_value=keywords, expected_source=src,
                                  description=f"Submission accepted: no feedback attributable to this rule ({why})")

    def _fill_steps(self, form: ObservedForm, skip: str, value_overrides: Optional[Dict[str, str]] = None) -> Optional[List[ActionStep]]:
        """Valid values for every other field (required ones mandatory); None when a required field cannot be filled."""
        steps: List[ActionStep] = []
        for f in form.fields:
            if f.selector == skip:
                continue
            if f.field_type in ("checkbox", "radio"):
                if f.required and f.checked is not True:
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=f.selector,
                                            description=f"Tick required '{f.label or f.name}'"))
                continue
            value = (value_overrides or {}).get(f.selector) or valid_filler(f)
            if value is None:
                if f.required:
                    return None
                continue
            act = ActionType.SELECT if f.field_type.startswith("select") else ActionType.FILL
            steps.append(ActionStep(step_number=0, action=act, selector=f.selector, value=value,
                                    description=f"Enter valid '{f.label or f.name}'"))
        return steps

    def _field_boundary_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        cases: List[TestCase] = []
        navigable = set(inv.navigable_routes())
        seen_forms: Set[Tuple[str, Tuple[str, ...]]] = set()
        for form in inv.forms:
            key = (form.route, tuple(f.selector for f in form.fields))
            if key in seen_forms or form.route not in navigable or not form.submit_selector or not inv.reproducible(form):
                continue
            seen_forms.add(key)
            plan = plan_form_boundaries(form, ctx["requirements"])
            ctx["diag"]["unknown_constraints"].extend(u.model_dump() for u in plan.unknowns)
            ctx["diag"]["unmapped_requirements"].extend(plan.unmapped_requirements)
            accepted_submissions = 0
            for bc in plan.cases:
                if bc.expected == EXPECT_ACCEPT and bc.requires_submit:
                    if form.is_login_form:
                        # Acceptance on a login form depends on matching credentials, not on the field rule.
                        ctx["diag"]["unknown_constraints"].append({
                            "field_selector": bc.field_selector, "route": form.route,
                            "reason": f"acceptance of '{bc.boundary}' on a login form cannot be judged without matching credentials",
                            "evidence_needed": "authorized credentials satisfying the boundary"})
                        continue
                    if accepted_submissions >= 2:
                        continue  # bound record creation per form
                    accepted_submissions += 1
                entry = self._entry_steps(ctx, form.route)
                if entry is None:
                    break
                steps, needs_auth = entry
                field = next(f for f in form.fields if f.selector == bc.field_selector)
                assertion = self._boundary_assertion(bc, field)
                if bc.requires_submit:
                    others = self._fill_steps(form, skip=bc.field_selector)
                    if others is None:
                        ctx["diag"]["unknown_constraints"].append({
                            "field_selector": bc.field_selector, "route": form.route,
                            "reason": "another required field in the form has no derivable valid value",
                            "evidence_needed": "valid sample data or explicit field rules"})
                        continue
                    steps.extend(others)
                is_toggle = field.field_type in ("checkbox", "radio")
                if is_toggle:
                    if bc.input_class != "EMPTY":
                        continue  # only "left unchecked" is a meaningful partition for a toggle
                else:
                    act = ActionType.SELECT if field.field_type.startswith("select") else ActionType.FILL
                    steps.append(ActionStep(step_number=0, action=act, selector=bc.field_selector, value=bc.input_value,
                                            description=f"Enter {bc.input_class.lower()} value for '{bc.field_label}' ({bc.boundary})",
                                            assertion=None if bc.requires_submit else assertion))
                if bc.requires_submit:
                    if bc.constraint.source == SOURCE_REQUIREMENT and bc.oracle == "SUBMISSION_FEEDBACK":
                        sel = json.dumps(bc.field_selector)
                        steps.append(ActionStep(
                            step_number=0, action=ActionType.EVAL_JS,
                            value=f"(() => {{ const el = document.querySelector({sel}); if (el && el.form) el.form.noValidate = true; }})()",
                            description="Disable client-side validation to exercise the server-side rule stated by the requirement"))
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                                            description="Submit the form", assertion=assertion))
                expected_word = "accepted" if bc.expected == EXPECT_ACCEPT else "rejected"
                is_login = form.is_login_form
                cases.append(self._case(
                    ctx, "FIELD",
                    f"{bc.field_label or bc.field_selector}: {'left unchecked' if is_toggle else bc.boundary} is {expected_word}",
                    f"Boundary test of '{bc.field_label or bc.field_selector}' on {form.route}: {bc.input_class} input "
                    f"({bc.boundary}) must be {expected_word} per {bc.constraint.source}.",
                    steps, "FIELD_CONSTRAINT",
                    ExpectedBehaviorSource.REQUIREMENT if bc.constraint.source == SOURCE_REQUIREMENT else ExpectedBehaviorSource.CONTRACT,
                    needs_auth=needs_auth, rule_ids=[pattern_id], evidence_ids=[form.state_id],
                    category=PageCategory.LOGIN if is_login else PageCategory.GENERAL,
                    derived={
                        "field_selector": bc.field_selector, "field_label": bc.field_label,
                        **({"input_value": bc.input_value} if not is_toggle else {"toggle_state": "unchecked"}),
                        "input_class": "UNCHECKED" if is_toggle else bc.input_class, "boundary": "left unchecked" if is_toggle else bc.boundary,
                        "constraint_kind": bc.constraint.kind, "constraint_value": bc.constraint.value,
                        "constraint_source": bc.constraint.source, "constraint_detail": bc.constraint.source_detail,
                        "expected": bc.expected, "oracle": bc.oracle, "oracle_expected": bc.oracle_expected,
                        "confirmed_rule": True,
                    },
                    expected_outcome=assertion.description,
                ))
        cases.extend(self._observed_validation_cases(ctx, pattern_id))
        return cases

    def _observed_validation_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        """Empty submissions whose validation message was actually observed for that form in this run."""
        inv: ObservedInventory = ctx["inventory"]
        cases: List[TestCase] = []
        for tr in inv.transitions:
            if "required_blank" not in tr.action_type.lower():
                continue
            src, dst = inv.state(tr.source_state_id), inv.state(tr.destination_state_id or "")
            if not src or not dst or not dst.validation_message:
                continue
            form = next((f for f in inv.forms if f.state_id == src.state_id or f.route == src.route), None)
            if form is None or not form.submit_selector:
                continue
            entry = self._entry_steps(ctx, form.route)
            if entry is None:
                continue
            steps, needs_auth = entry
            message = dst.validation_message.strip()[:120]
            steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                                    description="Submit the form with every field left empty",
                                    assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=message,
                                                                 description=f"Validation message '{message}' (observed)",
                                                                 expected_source="OBSERVED")))
            cases.append(self._case(
                ctx, "FIELD", f"Empty submission of {form.form_selector} on {form.route} shows observed validation",
                "Submitting the form with all fields empty must show the validation feedback observed during investigation.",
                steps, "FORM_VALIDATION", ExpectedBehaviorSource.OBSERVED_INVARIANT, needs_auth=needs_auth,
                rule_ids=[pattern_id], evidence_ids=tr.evidence_ids,
                category=PageCategory.LOGIN if form.is_login_form else PageCategory.GENERAL,
                expected_outcome=f"'{message}' is displayed",
            ))
        return cases

    # ── Strategy: created record reflection ───────────────────────────────
    def _reflection_cases(self, ctx: Dict[str, Any], pattern_id: str) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        cases: List[TestCase] = []
        navigable = set(inv.navigable_routes())
        for form in inv.forms:
            words = _words(form.action, form.form_id, form.submit_selector)
            if form.method != "POST" or not (words & {"create", "new", "add", "save"}) or form.is_login_form:
                continue
            if form.route not in navigable or not form.submit_selector or not inv.reproducible(form):
                continue
            ident = next((f for f in form.fields if f.field_type == "text" and _words(f.name, f.field_id, f.label) & {"title", "name", "subject", "label"}), None)
            if ident is None:
                continue
            marker = f"VQA {ctx['run_id'][-6:] or 'run'} {len(cases) + 1}"
            if ident.maxlength is not None and len(marker) > ident.maxlength:
                continue
            entry = self._entry_steps(ctx, form.route)
            if entry is None:
                continue
            steps, needs_auth = entry
            others = self._fill_steps(form, skip="", value_overrides={ident.selector: marker})
            if others is None:
                continue
            steps.extend(others)
            steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                                    description="Submit the create form",
                                    assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=marker,
                                                                 description=f"Created record '{marker}' is shown after submission",
                                                                 expected_source="TEST_DATA")))
            cases.append(self._case(
                ctx, "CRUD", f"Submitting {form.form_selector} reflects the entered {ident.label or ident.name}",
                "Submitting valid data on the observed create/save form must show the submitted record.",
                steps, "CRUD_STATE_CHANGE", ExpectedBehaviorSource.INFERRED, needs_auth=needs_auth,
                rule_ids=[pattern_id], evidence_ids=[form.state_id],
                derived={"identifying_field": ident.selector, "marker": marker},
                expected_outcome=f"'{marker}' visible after submission",
            ))
        return cases

    # ── Strategy: knowledge templates grounded on observed controls ────────
    def _resolve_template_target(self, inv: ObservedInventory, action: str, semantic: str, hints: List[str]):
        for hint in hints:
            for c in inv.controls:
                if c.selector == hint:
                    return c.selector, c.route
            for form in inv.forms:
                for f in form.fields:
                    if f.selector == hint:
                        return f.selector, form.route
                if form.submit_selector == hint:
                    return form.submit_selector, form.route
        terms = _words(semantic) - {"button", "input", "link", "field", "dropdown", "page", "control"}
        if not terms:
            return None
        tag_ok = {"FILL": ("input", "textarea"), "SELECT": ("select",), "SELECT_OPTION": ("select",)}.get(action, ("button", "a", "input"))
        matches = [c for c in inv.controls if c.tag in tag_ok and terms <= _words(c.selector, c.name)]
        if len(matches) == 1:
            return matches[0].selector, matches[0].route
        return None

    def _template_cases(self, ctx: Dict[str, Any], retrieved: RetrievedKnowledgePattern) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        pat = retrieved.pattern
        cases: List[TestCase] = []
        for st in pat.scenario_templates:
            steps: List[ActionStep] = []
            start_route: Optional[str] = None
            unresolved: Optional[str] = None
            typed: List[str] = []
            for tstep in st.steps:
                act = (tstep.action_type or "CLICK").upper()
                if act in ("NAVIGATE", "GOTO"):
                    path = tstep.input_value or ""
                    route = route_of(path) if path else ""
                    if not route or not inv.route_observed(route):
                        unresolved = f"navigation to '{path}' not observed"
                        break
                    if start_route is None:
                        start_route = route
                    steps.append(ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, route),
                                            description=tstep.description))
                else:
                    resolved = self._resolve_template_target(inv, act, tstep.target_semantic, tstep.target_selector_hints)
                    if resolved is None:
                        unresolved = f"no observed control for '{tstep.target_semantic}'"
                        break
                    selector, route = resolved
                    if start_route is None:
                        start_route = route
                    value: Optional[str] = None
                    if act in ("FILL", "INPUT"):
                        raw = tstep.input_value if tstep.input_value is not None else ""
                        if raw == "${TEST_USERNAME}":
                            value = CREDENTIAL_USERNAME_PLACEHOLDER
                        elif raw == "${TEST_PASSWORD}":
                            value = CREDENTIAL_PASSWORD_PLACEHOLDER
                        elif raw == "${SAMPLE_TEXT}":
                            fld = next((f for form in inv.forms for f in form.fields if f.selector == selector), None)
                            value = valid_filler(fld) if fld else "QA Sample"
                        else:
                            value = raw
                        typed.append(value or "")
                        action_type = ActionType.FILL
                    elif act in ("SELECT", "SELECT_OPTION"):
                        value = tstep.input_value
                        action_type = ActionType.SELECT
                    else:
                        action_type = ActionType.CLICK
                    steps.append(ActionStep(step_number=0, action=action_type, selector=selector, value=value,
                                            description=tstep.description))
                exp = (tstep.assertion_expected or "").strip()
                if exp and steps:
                    if tstep.assertion_type == "URL_CONTAINS" and inv.route_observed(exp):
                        steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                            assertion_type=AssertionType.URL_CONTAINS, expected_value=exp,
                            description=f"URL contains {exp} (observed on this target)", expected_source="OBSERVED")})
                    elif tstep.assertion_type != "URL_CONTAINS" and (inv.text_observed(exp) or any(exp.lower() in t.lower() for t in typed if t)):
                        steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                            assertion_type=AssertionType.TEXT_VISIBLE, expected_value=exp,
                            description=f"'{exp}' is shown (observed on this target)", expected_source="OBSERVED")})
            if unresolved is None and not any(s.assertion for s in steps):
                unresolved = "no expectation of the template is grounded on this target"
            if unresolved is not None or start_route is None:
                ctx["diag"]["inapplicable_templates"].append({"pattern_id": pat.pattern_id, "template_id": st.template_id,
                                                              "reason": unresolved or "no start route"})
                continue
            fills_password = any(s.value == CREDENTIAL_PASSWORD_PLACEHOLDER or (s.selector and any(
                f.field_type == "password" and f.selector == s.selector for form in inv.forms for f in form.fields)) for s in steps)
            if fills_password:
                entry_steps = [ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, start_route),
                                          description=f"Navigate to observed route {start_route}")]
                needs_auth = any(s.value in (CREDENTIAL_PASSWORD_PLACEHOLDER, CREDENTIAL_USERNAME_PLACEHOLDER) for s in steps)
            else:
                entry = self._entry_steps(ctx, start_route)
                if entry is None:
                    continue
                entry_steps, needs_auth = entry
            if steps and steps[0].action == ActionType.NAVIGATE and entry_steps and entry_steps[-1].action == ActionType.NAVIGATE \
                    and entry_steps[-1].value == steps[0].value:
                entry_steps = entry_steps[:-1]
            cases.append(self._case(
                ctx, "PAT", f"{st.title}", st.objective or pat.description, entry_steps + steps,
                "DOMAIN_PATTERN", ExpectedBehaviorSource.OBSERVED_INVARIANT, needs_auth=needs_auth,
                rule_ids=[pat.pattern_id], confidence=retrieved.relevance_score,
                reason=f"Knowledge pattern {pat.pattern_id} resolved against controls observed on this target ({retrieved.rationale}).",
                category=PageCategory.LOGIN if fills_password else PageCategory.GENERAL,
            ))
        return cases

    # ── Regression tests for confirmed investigation leads ─────────────────
    def _lead_regression_cases(self, ctx: Dict[str, Any]) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        selectors = inv.selectors()
        cases: List[TestCase] = []
        for lead in getattr(ctx["report"], "confirmed_bug_leads", []) or []:
            actual = (getattr(lead, "actual", {}) or {}).get("text", "") if isinstance(getattr(lead, "actual", {}), dict) else ""
            anomaly = next((a for a in _TEXT_ANOMALIES if a in (actual or "")), None)
            source_url = getattr(lead, "source_url", "") or ""
            raw_steps = list(getattr(lead, "setup_steps", []) or []) + list(getattr(lead, "steps", []) or [])
            if not anomaly or not raw_steps or canonical_origin(source_url) != ctx["origin"]:
                continue
            route = route_of(source_url)
            entry = self._entry_steps(ctx, route)
            if entry is None:
                continue
            steps, needs_auth = entry
            ok = True
            for rs in raw_steps:
                act = str(rs.get("action_type", "")).upper()
                sel = rs.get("raw_target") or ""
                if sel not in selectors:
                    ok = False
                    break
                if "FILL" in act:
                    steps.append(ActionStep(step_number=0, action=ActionType.FILL, selector=sel, value=str(rs.get("value") or rs.get("resolved_value") or ""), description=f"Fill {sel}"))
                elif "SELECT" in act:
                    steps.append(ActionStep(step_number=0, action=ActionType.SELECT, selector=sel, value=str(rs.get("value") or ""), description=f"Select on {sel}"))
                else:
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=sel, description=f"Activate {sel}"))
            if not ok or len(steps) < 2:
                continue
            steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                assertion_type=AssertionType.TEXT_NOT_VISIBLE, expected_value=anomaly,
                description=f"Anomalous text '{anomaly}' observed during investigation must not be rendered",
                expected_source="OBSERVED")})
            cases.append(self._case(
                ctx, "REG", f"Regression: {getattr(lead, 'title', 'confirmed lead')[:60]}",
                f"Replay the reproduction of confirmed lead {getattr(lead, 'id', '')} and verify the anomaly is absent.",
                steps, "DEFECT_REGRESSION", ExpectedBehaviorSource.OBSERVED_INVARIANT, needs_auth=needs_auth,
                priority=Priority.HIGH, evidence_ids=list(getattr(lead, "evidence_ids", []) or []),
            ))
        cases.extend(self._form_lead_regression_cases(ctx))
        return cases

    @staticmethod
    def _lead_step_selector(raw: Dict[str, Any]) -> str:
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        return raw.get("raw_target") or target.get("css") or ""

    _LEAD_STOP_WORDS = {"submission", "submit", "should", "rejected", "reject", "when", "the", "form", "field", "fields",
                        "must", "value", "values", "status", "accepted", "despite", "violating", "constraint", "not"}

    def _form_lead_regression_cases(self, ctx: Dict[str, Any]) -> List[TestCase]:
        """
        Confirmed form-constraint leads (e.g. a submission accepted although it violates a field rule) become
        regression candidates: replay the lead's recorded steps on observed selectors and assert the rejection
        the lead says was violated. The expectation stays labelled with the lead's oracle provenance.
        """
        inv: ObservedInventory = ctx["inventory"]
        selectors = inv.selectors()
        cases: List[TestCase] = []
        for lead in getattr(ctx["report"], "confirmed_bug_leads", []) or []:
            get = (lambda k, d=None: lead.get(k, d)) if isinstance(lead, dict) else (lambda k, d=None: getattr(lead, k, d))
            if str(get("status", "")) != "CONFIRMED" or str(get("probe_family", "")) != "forms":
                continue
            expected = get("expected") or {}
            expected_text = expected.get("text", "") if isinstance(expected, dict) else str(expected)
            source_url = get("source_url") or ""
            if "reject" not in expected_text.lower() or canonical_origin(source_url) != ctx["origin"]:
                continue
            route = route_of(source_url)
            raw_steps = list(get("setup_steps") or []) + list(get("steps") or [])
            filled = [self._lead_step_selector(rs) for rs in raw_steps if "FILL" in str(rs.get("action_type", "")).upper()]
            form = next((f for f in inv.forms if f.route == route and set(filled) <= {fl.selector for fl in f.fields}), None)
            if form is None or not inv.reproducible(form):
                ctx["diag"].setdefault("unconverted_leads", []).append(
                    {"lead_id": get("id"), "reason": "lead steps do not map to a reproducible observed form"})
                continue
            entry = self._entry_steps(ctx, route)
            if entry is None:
                continue
            steps, needs_auth = entry
            ok = True
            for rs in raw_steps:
                act = str(rs.get("action_type", "")).upper()
                sel = self._lead_step_selector(rs)
                value = rs.get("resolved_value") if rs.get("resolved_value") is not None else rs.get("value")
                if act == "SUBMIT":
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=form.submit_selector,
                                            description="Submit the form as in the confirmed lead"))
                elif sel not in selectors:
                    ok = False
                    break
                elif "FILL" in act:
                    steps.append(ActionStep(step_number=0, action=ActionType.FILL, selector=sel, value=str(value or ""),
                                            description=f"Enter the lead's value into {sel}"))
                elif act in ("CHECK", "CLICK", "CLICK_CONTROL"):
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=sel, description=f"Activate {sel}"))
                elif "SELECT" in act and value:
                    steps.append(ActionStep(step_number=0, action=ActionType.SELECT, selector=sel, value=str(value), description=f"Select on {sel}"))
                else:
                    ok = False
                    break
            if not ok or steps[-1].action != ActionType.CLICK:
                ctx["diag"].setdefault("unconverted_leads", []).append(
                    {"lead_id": get("id"), "reason": "lead steps include unobserved selectors or no submission"})
                continue
            oracle = getattr(get("oracle_type"), "value", get("oracle_type")) or "LEAD"
            target_name = str(get("target_desc") or "")
            field = next((f for f in form.fields if target_name and target_name in (f.name, f.field_id, f.selector)), None)
            keywords = sorted(({w for w in _words(field.label, field.name, field.field_id) if len(w) >= 3} if field else set())
                              | {w for w in _words(expected_text) if len(w) >= 4 and w not in self._LEAD_STOP_WORDS})
            steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                assertion_type=AssertionType.VALIDATION_FEEDBACK_SHOWN,
                target_selector=field.selector if field else None,
                expected_value=json.dumps(keywords),
                description=f"Submission rejected with feedback attributable to the rule: {expected_text[:120]}",
                expected_source=f"INVESTIGATION_{str(oracle).upper()}")})
            cases.append(self._case(
                ctx, "REG", f"Regression of confirmed lead: {str(get('title', 'form rule'))[:70]}",
                f"Replay confirmed lead {get('id')} ({oracle} oracle, reproduced "
                f"{(get('confirmation') or {}).get('passed', '?')}/{(get('confirmation') or {}).get('runs', '?')}) and verify the "
                "application now rejects the submission.",
                steps, "DEFECT_REGRESSION", ExpectedBehaviorSource.INFERRED, needs_auth=needs_auth, priority=Priority.HIGH,
                evidence_ids=list(get("evidence_ids") or []),
                derived={"source_lead_id": get("id"), "lead_oracle_type": str(oracle),
                         "field_selector": field.selector if field else None, "lead_expected": expected_text[:200]},
                expected_outcome=expected_text[:200],
            ))
        return cases

    # ── Legacy reports (no observed inventory) ─────────────────────────────
    def _legacy_journey_cases(self, ctx: Dict[str, Any]) -> List[TestCase]:
        """Old report shape: replay inferred journeys using only selectors/URLs the report itself lists."""
        inv: ObservedInventory = ctx["inventory"]
        selectors = inv.selectors()
        cases: List[TestCase] = []
        for journey in getattr(ctx["report"], "inferred_journeys", []) or []:
            raw = journey.get("steps", []) if isinstance(journey, dict) else []
            steps: List[ActionStep] = [ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, "/"),
                                                  description="Navigate to target entry")]
            for s in raw:
                act = str(s.get("action", "")).upper()
                target = str(s.get("target") or "").strip()
                if act in ("NAVIGATE", "GOTO"):
                    url = target if target.startswith("http") else self._url(ctx, target or "/")
                    if canonical_origin(url) != ctx["origin"] or not inv.route_observed(route_of(url)):
                        break
                    prev = steps[-1]
                    if not (prev.action == ActionType.NAVIGATE and (prev.value or "").rstrip("/") == url.rstrip("/")):
                        steps.append(ActionStep(step_number=0, action=ActionType.NAVIGATE, value=url, description=f"Navigate to {route_of(url)}"))
                    continue
                if target not in selectors:
                    break
                if act in ("FILL", "INPUT"):
                    value = CREDENTIAL_PASSWORD_PLACEHOLDER if any(f in target.lower() for f in ("password", "passwd")) else str(s.get("value") or "")
                    steps.append(ActionStep(step_number=0, action=ActionType.FILL, selector=target, value=value, description=f"Fill {target}"))
                elif act == "SELECT_OPTION":
                    if not s.get("value"):
                        break
                    steps.append(ActionStep(step_number=0, action=ActionType.SELECT, selector=target, value=str(s["value"]), description=f"Select on {target}"))
                elif act == "CLICK":
                    steps.append(ActionStep(step_number=0, action=ActionType.CLICK, selector=target, description=f"Click {target}"))
                else:
                    break
                frag = s.get("expected_url_fragment") or ""
                heading = s.get("expected_heading") or ""
                if frag and inv.route_observed(frag):
                    steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                        assertion_type=AssertionType.URL_CONTAINS, expected_value=frag, description=f"Reached {frag}", expected_source="OBSERVED")})
                elif heading and inv.text_observed(heading):
                    steps[-1] = steps[-1].model_copy(update={"assertion": ExecutionAssertion(
                        assertion_type=AssertionType.TEXT_VISIBLE, expected_value=heading, description=f"Shows '{heading}'", expected_source="OBSERVED")})
            if not any(s.assertion for s in steps):
                continue
            name = (journey.get("name") if isinstance(journey, dict) else None) or "Observed workflow"
            cases.append(self._case(
                ctx, "WF", name, (journey.get("objective") if isinstance(journey, dict) else None) or f"Replay observed workflow '{name}'.",
                steps, "SPECIFICATION", ExpectedBehaviorSource.OBSERVED_INVARIANT,
                needs_auth=any(s.value == CREDENTIAL_PASSWORD_PLACEHOLDER for s in steps),
                evidence_ids=journey.get("evidence_refs", []) if isinstance(journey, dict) else [],
            ))
        return cases

    def _legacy_route_cases(self, ctx: Dict[str, Any]) -> List[TestCase]:
        # Legacy reports do not record which states needed authentication, so only the entry
        # state is a safe regression target.
        inv: ObservedInventory = ctx["inventory"]
        cases: List[TestCase] = []
        for s in inv.states[:1]:
            if not s.headings or is_instance_route(s.route):
                continue
            steps = [
                ActionStep(step_number=0, action=ActionType.NAVIGATE, value=self._url(ctx, s.route), description=f"Navigate to {s.route}"),
                ActionStep(step_number=0, action=ActionType.OBSERVE_STATE, description="Observe landmarks",
                           assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=s.headings[0],
                                                        description=f"Heading '{s.headings[0]}' (reported)", expected_source="OBSERVED")),
            ]
            cases.append(self._case(ctx, "ROUTE", f"Route renders reported heading: {s.route}",
                                    f"Verify {s.route} renders '{s.headings[0]}'.", steps, "OBSERVED_STATE",
                                    ExpectedBehaviorSource.OBSERVED_INVARIANT, priority=Priority.LOW))
        return cases

    # ── Orchestration ─────────────────────────────────────────────────────
    def _synthesize_candidates(self, ctx: Dict[str, Any]) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        if inv.provenance == PROVENANCE_LEGACY:
            return self._legacy_journey_cases(ctx) + self._legacy_route_cases(ctx)

        buckets: Dict[str, List[TestCase]] = {}
        for ret in ctx["retrieved"]:
            strategy = ret.pattern.strategy
            pid = ret.pattern.pattern_id
            if strategy == "ROUTE_REACHABILITY":
                buckets.setdefault("route", []).extend(self._route_cases(ctx, pid))
            elif strategy == "SORT_ORDER":
                buckets.setdefault("sort", []).extend(self._sort_cases(ctx, pid))
            elif strategy == "FILTER_RESULTS":
                buckets.setdefault("filter", []).extend(self._filter_cases(ctx, pid))
            elif strategy == "FIELD_BOUNDARIES":
                buckets.setdefault("field", []).extend(self._field_boundary_cases(ctx, pid))
            elif strategy == "SUBMISSION_REFLECTION":
                buckets.setdefault("crud", []).extend(self._reflection_cases(ctx, pid))
            else:
                buckets.setdefault("pattern", []).extend(self._template_cases(ctx, ret))
        buckets.setdefault("transition", []).extend(self._transition_cases(ctx))
        buckets.setdefault("regression", []).extend(self._lead_regression_cases(ctx))
        ctx["buckets"] = {k: len(v) for k, v in buckets.items()}

        # Round-robin across categories so a cap keeps the suite diverse.
        order = ["field", "regression", "filter", "sort", "pattern", "crud", "transition", "route"]
        queues = [list(buckets.get(k, [])) for k in order]
        merged: List[TestCase] = []
        while any(queues):
            for q in queues:
                if q:
                    merged.append(q.pop(0))
        return merged

    def _finalize(self, ctx: Dict[str, Any], candidates: List[TestCase], target_count: int):
        inv: ObservedInventory = ctx["inventory"]
        validator = GroundedTestValidator(target_base_url=ctx["target_url"], inventory=inv, requirements=ctx["requirements"])
        valid_cases, rejected = validator.validate_suite(candidates)
        selected = valid_cases[:target_count]
        diag = ctx["diag"]
        diag["candidates_generated"] = len(candidates)
        diag["valid_cases"] = len(valid_cases)
        diag["selected_cases"] = len(selected)
        diag["rejected_cases"] = [{"test_id": tc.test_id, "name": tc.name, "reason": why} for tc, why in rejected]
        diag["rejection_summary"] = GroundedTestValidator.rejection_summary(rejected)
        diag["candidates_by_strategy"] = ctx.get("buckets", {})
        diag["requirement_alignment"] = align_requirements(ctx["requirements"], inv)
        if diag["requirement_alignment"]["warning"]:
            logger.warning("[TestSynthesis] Requirement alignment (run=%s): %s", ctx["run_id"], diag["requirement_alignment"]["warning"])
        if diag.get("llm_used"):
            diag["llm_cases_proposed"] = sum(1 for tc in candidates if tc.oracle_class == "LLM_SYNTHESIZED")
            diag["llm_cases_accepted"] = sum(1 for tc in selected if tc.oracle_class == "LLM_SYNTHESIZED")
            diag["llm_rejections"] = [r for r in diag["rejected_cases"] if r["test_id"].startswith("TC-LLM")]
        self.last_diagnostics = diag

        tracker = MultiDimensionalCoverageTracker(
            known_routes=[self._url(ctx, r) for r in inv.navigable_routes()],
            known_controls=sorted(inv.selectors()),
            known_transitions=[t.transition_id for t in inv.transitions],
            known_journeys=[],
            known_invariants=ctx["model"].invariants,
        )
        for tc in selected:
            tracker.record_scenario(tc)
        logger.info("[TestSynthesis] %s: %d candidates, %d valid, %d selected, %d rejected (origin=%s, run=%s)",
                    ctx["model"].primary_archetype.value, len(candidates), len(valid_cases), len(selected),
                    len(rejected), ctx["origin"], ctx["run_id"])
        return selected, tracker.compute_coverage(total_scenarios=len(selected)), ctx["model"]

    # ── Optional LLM proposals (validated like every other candidate) ──────
    async def _synthesize_llm_scenarios(self, ctx: Dict[str, Any], needed_count: int) -> List[TestCase]:
        inv: ObservedInventory = ctx["inventory"]
        controls_sample = [{"selector": c.selector, "text": c.name, "tag": c.tag, "route": c.route, "options": c.options[:6]}
                           for c in inv.controls[:30]]
        headings = sorted({h for s in inv.states for h in s.headings})[:30]
        system_prompt = ("You are a QA engineer designing browser tests. Respond with JSON only. Use ONLY the selectors, "
                         "routes, option values and texts you are given; never invent any.")
        user_prompt = f"""Propose up to {min(needed_count, 8)} additional test scenarios for this application.
Origin: {ctx['origin']}
Observed routes: {sorted(inv.navigable_routes())}
Observed headings/texts: {json.dumps(headings)}
Observed controls (the ONLY selectors you may use): {json.dumps(controls_sample)}
Return {{"test_cases": [...]}}; each item: name, objective, steps (step_number, action NAVIGATE|CLICK|FILL|SELECT, selector,
value, description, assertion {{assertion_type URL_CONTAINS|TEXT_VISIBLE|ELEMENT_VISIBLE, expected_value, target_selector,
description}}). Every test needs an assertion about an observed route, heading/text or control."""
        response = await self.llm_provider.generate_json(system_prompt, user_prompt)
        items = response.get("test_cases") if isinstance(response, dict) else response
        if not isinstance(items, list):
            return []
        ctx["diag"]["llm_proposals"] = len(items)
        cases: List[TestCase] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            steps: List[ActionStep] = []
            for s in item.get("steps", []):
                act = str(s.get("action", "")).upper()
                if act not in ActionType.__members__:
                    continue
                raw_assert = s.get("assertion") if isinstance(s.get("assertion"), dict) else None
                assertion = None
                if raw_assert and str(raw_assert.get("assertion_type", "")).upper() in AssertionType.__members__:
                    assertion = ExecutionAssertion(
                        assertion_type=AssertionType[str(raw_assert["assertion_type"]).upper()],
                        expected_value=raw_assert.get("expected_value"), target_selector=raw_assert.get("target_selector"),
                        # Grounding is still enforced by the validator, but an LLM-chosen expectation is never an
                        # established rule: failures are reported as anomalies, not confirmed defects.
                        description=raw_assert.get("description") or "LLM-proposed expectation", expected_source="LLM_PROPOSED")
                value = s.get("value")
                if act == "NAVIGATE" and value and not str(value).startswith("http"):
                    value = self._url(ctx, str(value))
                steps.append(ActionStep(step_number=0, action=ActionType[act], selector=s.get("selector"), value=value,
                                        description=s.get("description") or act, assertion=assertion))
            if steps:
                cases.append(self._case(ctx, "LLM", item.get("name") or "LLM-proposed scenario",
                                        item.get("objective") or "LLM-proposed scenario over observed controls",
                                        steps, "LLM_SYNTHESIZED", ExpectedBehaviorSource.INFERRED, confidence=0.6))
        return cases
