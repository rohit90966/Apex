"""
Test Plan Synthesizer for Stateful Investigation.
Transforms discovered application map, inferred journeys, business rules,
and confirmed bug leads into executable, evidence-grounded TestCase objects.
"""

from typing import List, Dict, Any, Optional, Tuple
import uuid

from app.schemas import (
    TestCase,
    ActionStep,
    ActionType,
    ExecutionAssertion,
    AssertionType,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
)
from app.investigation_agent.models import InvestigationReport

# FILL value standing in for the target's password; resolved by QAPlanRunner at execution time.
CREDENTIAL_PASSWORD_PLACEHOLDER = "${CREDENTIAL_PASSWORD}"
NO_STEPS_RULE_REASON = "No executable steps could be derived from this rule; it needs a defined probe before it can run."
NO_STEPS_BUG_REASON = "The reproduction steps for this lead were not captured in executable form."

_INTERACTION_ACTIONS = (ActionType.CLICK, ActionType.FILL, ActionType.SELECT)


def _is_password_target(selector: str) -> bool:
    return "password" in (selector or "").lower()


def _step_assertion(step: Dict[str, Any], target_url: str) -> Optional[ExecutionAssertion]:
    """Builds the assertion for a journey step from the state it was observed to reach."""
    url_fragment = step.get("expected_url_fragment") or ""
    legacy_url = step.get("expected_url") or ""
    if not url_fragment and legacy_url and legacy_url != target_url:
        url_fragment = legacy_url.replace(target_url, "").split("?")[0] or legacy_url
    if url_fragment:
        return ExecutionAssertion(
            assertion_type=AssertionType.URL_CONTAINS,
            expected_value=url_fragment,
            description=f"Verify transition to route {url_fragment}",
        )
    if step.get("expected_heading"):
        return ExecutionAssertion(
            assertion_type=AssertionType.TEXT_VISIBLE,
            expected_value=step["expected_heading"],
            description=f"Verify landmark heading '{step['expected_heading']}' is visible",
        )
    if step.get("expected_selector"):
        return ExecutionAssertion(
            assertion_type=AssertionType.ELEMENT_VISIBLE,
            target_selector=step["expected_selector"],
            description=f"Verify key element '{step['expected_selector']}' is present",
        )
    return None


def _journey_invalid_reason(steps: List[ActionStep]) -> Optional[str]:
    """Explains why a synthesized journey cannot be executed meaningfully, or None when it can."""
    if len(steps) <= 1:
        return "No executable steps beyond opening the entry page."
    for s in steps:
        if s.action in _INTERACTION_ACTIONS and not s.selector:
            return f"Step {s.step_number} ({s.action.value}) has no target selector."
    if not any(s.assertion for s in steps):
        return "No assertions could be derived; executing it would verify nothing."
    return None


def synthesize_with_diagnostics(
    report: InvestigationReport,
    max_cases: int = 35,
) -> Tuple[List[TestCase], Dict[str, Any]]:
    """
    Canonical test synthesis for one investigation: (test cases, planning diagnostics).

    Reports carrying an observed inventory (schema 2.0) are planned exclusively from that
    inventory; if nothing can be grounded the result is empty and the diagnostics say why.
    Older reports without an inventory fall back to the legacy journey/rule synthesizer below.
    """
    import os
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    planner = KnowledgeGuidedTestPlanner()
    if os.environ.get("VERITYQA_LLM_TEST_PROPOSALS") == "1" and getattr(report, "observed_inventory", None):
        # Opt-in: LLM-proposed scenarios on top of the deterministic plan, validated by the same grounding
        # rules. Run in a worker thread so this stays safe inside a running event loop.
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            cases, coverage, _model = pool.submit(
                lambda: asyncio.run(planner.plan_test_suite(report, target_count=max_cases))).result()
    else:
        cases, coverage, _model = planner.plan_test_suite_sync(report, target_count=max_cases)
    diagnostics = dict(planner.last_diagnostics)
    diagnostics["coverage_percentage"] = coverage.overall_coverage_percentage
    if cases or getattr(report, "observed_inventory", None):
        return cases[:max_cases], diagnostics
    diagnostics["fallback"] = "legacy report without observed inventory: legacy journey synthesizer used"
    return _legacy_synthesis(report, max_cases), diagnostics


def synthesize_test_cases_from_investigation(
    report: InvestigationReport,
    max_cases: int = 35,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> List[TestCase]:
    """
    Synthesizes evidence-grounded TestCases for an investigation report (see synthesize_with_diagnostics).
    When `diagnostics` is given it is filled with the planning diagnostics.
    """
    cases, diag = synthesize_with_diagnostics(report, max_cases)
    if diagnostics is not None:
        diagnostics.update(diag)
    return cases


def _legacy_synthesis(report: InvestigationReport, max_cases: int) -> List[TestCase]:
    test_cases: List[TestCase] = []
    target_url = report.overview.target_url or "http://127.0.0.1:8000/"

    # 1. Synthesize User Journey Test Cases from Inferred Journeys.
    # InvestigationReportBuilder emits journey steps with separate fields: action, target, value,
    # expected_url_fragment, expected_heading, expected_selector, evidence_refs.
    for idx, journey in enumerate(report.inferred_journeys[:4]):
        steps = journey.get("steps", [])
        if not steps:
            continue

        tc_id = f"TC-INV-JOURNEY-{idx + 1:02d}"
        name = journey.get("name") or f"Discovered Workflow Journey {idx + 1}"
        objective = journey.get("objective") or "Verify multi-state progression and critical user journey"

        action_steps: List[ActionStep] = []
        # Step 1: Navigate to target URL
        action_steps.append(
            ActionStep(
                step_number=1,
                action=ActionType.NAVIGATE,
                value=target_url,
                description=f"Navigate to {target_url}",
            )
        )

        relevant_elements: List[str] = []
        evidence_ids: List[str] = list(journey.get("evidence_refs", []))

        step_num = 2
        for s in steps:
            act = str(s.get("action", "")).upper()
            target = str(s.get("target") or "").strip()
            if " ON " in act and not target:
                # Compact form, e.g. "CLICK_CONTROL on #login-button"
                raw_action = str(s.get("action", ""))
                act, target = act.split(" ON ", 1)[0].strip(), raw_action[raw_action.lower().index(" on ") + 4:].strip()
            if act.startswith("CLICK"):
                act = "CLICK"
            evidence_ids.extend(s.get("evidence_refs", []) or [])

            if act == "NAVIGATE":
                if not target or target.rstrip("/") == target_url.rstrip("/"):
                    continue  # Entry navigation is already step 1
                action_type, selector, val = ActionType.NAVIGATE, None, target
            elif act == "CLICK":
                action_type, selector, val = ActionType.CLICK, target or None, None
            elif act == "FILL":
                action_type, selector = ActionType.FILL, target or None
                # Never copy a credential into a test case; the runner resolves the placeholder at execution time.
                val = CREDENTIAL_PASSWORD_PLACEHOLDER if _is_password_target(target) else s.get("value")
            elif act == "SELECT_OPTION":
                action_type, selector, val = ActionType.SELECT, target or None, s.get("value")
            else:
                # e.g. VERIFY_CONSTRAINT describes an observation, not an executable action.
                continue

            if selector:
                relevant_elements.append(selector)

            action_steps.append(
                ActionStep(
                    step_number=step_num,
                    action=action_type,
                    selector=selector,
                    value=val,
                    description=s.get("description") or f"{act} {target}".strip(),
                    assertion=_step_assertion(s, target_url),
                )
            )
            step_num += 1

        # Add final milestone assertion if none attached to last step
        if action_steps and not action_steps[-1].assertion:
            assertions = journey.get("assertions", [])
            for a in assertions:
                if a.get("type") == "URL_CONTAINS":
                    action_steps[-1].assertion = ExecutionAssertion(
                        assertion_type=AssertionType.URL_CONTAINS,
                        expected_value=a.get("expected", ""),
                        description="Verify final journey URL",
                    )
                    break
                elif a.get("type") == "HEADING_VISIBLE":
                    action_steps[-1].assertion = ExecutionAssertion(
                        assertion_type=AssertionType.TEXT_VISIBLE,
                        expected_value=a.get("expected", ""),
                        description="Verify final journey landmark heading",
                    )
                    break

        invalid_reason = _journey_invalid_reason(action_steps)
        tc = TestCase(
            test_id=tc_id,
            name=name,
            objective=objective,
            page_category=PageCategory.GENERAL,
            relevant_elements=list(dict.fromkeys(relevant_elements)),
            action_description=f"Execute {len(action_steps)} steps traversing discovered workflow.",
            expected_outcome="User progression completes with verified state assertions.",
            expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
            evidence_to_collect=["state_diff", "screenshot", "url_change"],
            priority=Priority.HIGH,
            preconditions=journey.get("preconditions", ["Discovered valid path"]),
            requires_test_account=bool(report.authentication_access.credentials_available),
            safe_for_future_automation=True,
            selection_reason="Validates end-to-end critical user journey discovered during autonomous exploration.",
            confidence=0.95,
            status=TestCaseStatus.INVALID if invalid_reason else TestCaseStatus.PROPOSED,
            invalid_reason=invalid_reason,
            evidence_ids=list(dict.fromkeys(evidence_ids))[:5],
            oracle_class="SPECIFICATION",
            steps=action_steps,
        )
        test_cases.append(tc)

    # 2. Synthesize Form Constraint / Boundary Tests from Business Rules & Hypotheses
    for idx, rule in enumerate(report.business_rules_and_hypotheses[:4]):
        if len(test_cases) >= max_cases:
            break

        tc_id = f"TC-INV-RULE-{idx + 1:02d}"
        ev_ids = list(getattr(rule, "supporting_evidence_ids", []) or getattr(rule, "evidence_ids", []))[:3]
        rule_cat = getattr(rule.category, "value", str(rule.category))
        tc = TestCase(
            test_id=tc_id,
            name=f"Rule Verification: {rule.description[:50]}",
            objective=f"Verify business invariant or hypothesis: {rule.description}",
            page_category=PageCategory.GENERAL,
            relevant_elements=[],
            action_description=f"Submit constrained values to probe rule {rule.rule_id}",
            expected_outcome="System adheres to invariant without unhandled error or bypass.",
            expected_behavior_source=ExpectedBehaviorSource.INFERRED,
            evidence_to_collect=["validation_alert", "network_status"],
            priority=Priority.MEDIUM,
            selection_reason=f"Validates discovered business rule {rule.rule_id} ({rule_cat}).",
            confidence=0.90,
            status=TestCaseStatus.INVALID,
            invalid_reason=NO_STEPS_RULE_REASON,
            rule_ids=[rule.rule_id],
            evidence_ids=ev_ids,
            oracle_class="CONSTRAINT_DERIVED",
        )
        test_cases.append(tc)

    # 3. Synthesize Regression Verification Tests from Confirmed Bug Leads
    for idx, lead in enumerate(report.confirmed_bug_leads[:3]):
        if len(test_cases) >= max_cases:
            break

        tc_id = f"TC-INV-BUG-{idx + 1:02d}"
        lead_id_val = getattr(lead, "id", getattr(lead, "lead_id", "bug"))
        exp_text = "Clean interaction without technical or specification errors"
        if isinstance(lead.expected, dict):
            exp_text = lead.expected.get("text", exp_text)
        elif isinstance(lead.expected, str):
            exp_text = lead.expected

        tc = TestCase(
            test_id=tc_id,
            name=f"Defect Regression: {lead.title[:50]}",
            objective=f"Verify fix and prevent regression for confirmed bug: {lead.title}",
            page_category=PageCategory.GENERAL,
            relevant_elements=[lead.target_desc] if lead.target_desc and lead.target_desc != "browser" else [],
            action_description=f"Execute minimized reproduction sequence for {lead_id_val}",
            expected_outcome=exp_text,
            expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
            evidence_to_collect=["console_errors", "network_status", "screenshot"],
            priority=Priority.HIGH,
            selection_reason="Strictly confirmed defect via 2-of-2 isolated context reproduction.",
            confidence=1.0,
            status=TestCaseStatus.INVALID,
            invalid_reason=NO_STEPS_BUG_REASON,
            evidence_ids=list(getattr(lead, "evidence_ids", []))[:5],
            oracle_class="GENERIC_HEURISTIC",
        )
        test_cases.append(tc)

    return test_cases
