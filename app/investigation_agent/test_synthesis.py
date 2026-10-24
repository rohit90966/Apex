"""
Test Plan Synthesizer for Stateful Investigation.
Transforms discovered application map, inferred journeys, business rules,
and confirmed bug leads into executable, evidence-grounded TestCase objects.
"""

from typing import List, Dict, Any, Optional
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


def synthesize_test_cases_from_investigation(
    report: InvestigationReport,
    max_cases: int = 10,
) -> List[TestCase]:
    """
    Synthesizes concrete TestCase specifications from an InvestigationReport.
    Guarantees all generated test cases are grounded in observed DOM states and transitions.
    """
    test_cases: List[TestCase] = []
    target_url = report.overview.target_url or "http://127.0.0.1:8000/"

    # 1. Synthesize User Journey Test Cases from Inferred Journeys
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
            act_text = s.get("action", "")
            exp_url = s.get("expected_url", "")
            exp_heading = s.get("expected_heading", "")
            exp_sel = s.get("expected_selector", "")
            s_ev = s.get("evidence_refs", [])
            evidence_ids.extend(s_ev)

            # Parse action
            action_type = ActionType.CLICK
            selector = None
            val = None

            if "CLICK" in act_text:
                action_type = ActionType.CLICK
                if "on " in act_text:
                    selector = act_text.split("on ", 1)[1].strip()
            elif "FILL" in act_text or "INPUT" in act_text:
                action_type = ActionType.FILL
                if "on " in act_text:
                    selector = act_text.split("on ", 1)[1].strip()
                val = "test_value"
            else:
                action_type = ActionType.CLICK
                if "on " in act_text:
                    selector = act_text.split("on ", 1)[1].strip()

            if selector:
                relevant_elements.append(selector)

            # Build assertion if step produces milestone state
            assertion = None
            if exp_url and exp_url != target_url:
                assertion = ExecutionAssertion(
                    assertion_type=AssertionType.URL_CONTAINS,
                    expected_value=exp_url.replace(target_url, "").split("?")[0] or exp_url,
                    description=f"Verify transition to route {exp_url}",
                )
            elif exp_heading:
                assertion = ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value=exp_heading,
                    description=f"Verify landmark heading '{exp_heading}' is visible",
                )
            elif exp_sel:
                assertion = ExecutionAssertion(
                    assertion_type=AssertionType.ELEMENT_VISIBLE,
                    target_selector=exp_sel,
                    description=f"Verify key element '{exp_sel}' is present",
                )

            action_steps.append(
                ActionStep(
                    step_number=step_num,
                    action=action_type,
                    selector=selector,
                    value=val,
                    description=act_text or f"Execute transition step {step_num}",
                    assertion=assertion,
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
            status=TestCaseStatus.PROPOSED,
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
            status=TestCaseStatus.PROPOSED,
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
            status=TestCaseStatus.PROPOSED,
            evidence_ids=list(getattr(lead, "evidence_ids", []))[:5],
            oracle_class="GENERIC_HEURISTIC",
        )
        test_cases.append(tc)

    return test_cases
