import logging
from typing import List
from app.schemas import Priority, PageCategory
from app.agents.models import RiskAnalysis, RiskItem
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class RiskAnalystNode:
    """
    Node C: Risk and Coverage Analyst
    Identifies functional, negative-input, usability, accessibility, and security risks.
    Prioritizes scenarios based on page category and observed DOM targets.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        classification = state.get("page_classification")
        evidence = state.get("evidence_analysis")
        req_analysis = state.get("requirement_analysis")

        risks: List[RiskItem] = []
        unaddressed_reqs: List[str] = []

        category = classification.page_category if classification else PageCategory.GENERAL
        targets = evidence.observed_targets if evidence else []

        # 1. Unaddressed requirements check
        if req_analysis and req_analysis.explicit_requirements:
            for req in req_analysis.explicit_requirements:
                unaddressed_reqs.append(req.id)
                risks.append(
                    RiskItem(
                        risk_id=f"RISK-REQ-{req.id}",
                        title=f"Unverified User Requirement: {req.text[:40]}...",
                        description=f"Explicit requirement '{req.text}' must be validated against implementation.",
                        category="functional",
                        severity=Priority.HIGH,
                        likelihood=Priority.HIGH,
                        target_elements=[],
                        mitigating_scenarios=[f"Validate {req.text}"],
                    )
                )

        # 2. Category-specific risks
        if category == PageCategory.LOGIN:
            # Sensitive data exposure
            pw_targets = [t.label_or_name for t in targets if t.element_type == "password"]
            if pw_targets:
                risks.append(
                    RiskItem(
                        risk_id="RISK-LOGIN-01",
                        title="Plaintext Credential Exposure",
                        description="Password inputs must obscure sensitive characters visually to protect against shoulder surfing.",
                        category="security",
                        severity=Priority.HIGH,
                        likelihood=Priority.MEDIUM,
                        target_elements=pw_targets,
                        mitigating_scenarios=["Verify password masking invariant"],
                    )
                )

            # Unhandled empty submission
            risks.append(
                RiskItem(
                    risk_id="RISK-LOGIN-02",
                    title="Unhandled Empty Input Submission",
                    description="Submitting empty credentials could trigger unhandled server exceptions or bypass validation.",
                    category="negative_input",
                    severity=Priority.HIGH,
                    likelihood=Priority.HIGH,
                    target_elements=[t.label_or_name for t in targets],
                    mitigating_scenarios=["Empty submission validation test"],
                )
            )

        elif category == PageCategory.REGISTRATION:
            pw_targets = [t for t in targets if t.element_type == "password"]
            if len(pw_targets) >= 2:
                risks.append(
                    RiskItem(
                        risk_id="RISK-REG-01",
                        title="Password Confirmation Inconsistency",
                        description="Mismatched passwords might be accepted if client/server validation fails.",
                        category="functional",
                        severity=Priority.HIGH,
                        likelihood=Priority.MEDIUM,
                        target_elements=[t.label_or_name for t in pw_targets],
                        mitigating_scenarios=["Verify password confirmation mismatch validation"],
                    )
                )
            risks.append(
                RiskItem(
                    risk_id="RISK-REG-02",
                    title="Incomplete Required Data Submission",
                    description="Omission of required registration parameters must be caught client-side.",
                    category="negative_input",
                    severity=Priority.HIGH,
                    likelihood=Priority.HIGH,
                    target_elements=[t.label_or_name for t in targets],
                    mitigating_scenarios=["Validate mandatory field constraints"],
                )
            )

        elif category == PageCategory.SEARCH:
            risks.append(
                RiskItem(
                    risk_id="RISK-SEARCH-01",
                    title="Blank or Whitespace Search Query Exception",
                    description="Empty search queries might lead to uncaught backend query exceptions or degraded performance.",
                    category="negative_input",
                    severity=Priority.MEDIUM,
                    likelihood=Priority.HIGH,
                    target_elements=["search input"],
                    mitigating_scenarios=["Verify empty and whitespace query handling"],
                )
            )

        # Baseline Accessibility & Load Invariants
        risks.append(
            RiskItem(
                risk_id="RISK-BASE-01",
                title="Interactive Element Accessibility Inaccessibility",
                description="Controls lacking accessible names or ARIA roles fail assistive technology guidelines.",
                category="accessibility",
                severity=Priority.MEDIUM,
                likelihood=Priority.MEDIUM,
                target_elements=[t.label_or_name for t in targets if t.element_type in ("button", "submit", "a")],
                mitigating_scenarios=["Verify accessible names on interactive controls"],
            )
        )

        # Page Load & Rendering Integrity Invariant
        risks.append(
            RiskItem(
                risk_id="RISK-BASE-02",
                title="Page Load Integrity and Console Errors",
                description="Uncaught JavaScript exceptions, network 4xx/5xx asset failures, or broken DOM rendering compromise page usability.",
                category="functional",
                severity=Priority.HIGH,
                likelihood=Priority.LOW,
                target_elements=["document body", "main"],
                mitigating_scenarios=["Verify page load integrity and absence of console errors"],
            )
        )

        # Form Controls Presence and Rendering Invariant
        input_targets = [t.label_or_name for t in targets if t.element_type in ("text", "password", "email", "search", "textarea", "select")]
        if input_targets:
            risks.append(
                RiskItem(
                    risk_id="RISK-BASE-03",
                    title="Essential Form Controls Visibility",
                    description="Essential input fields must be properly rendered and visible in DOM before interaction.",
                    category="functional",
                    severity=Priority.HIGH,
                    likelihood=Priority.LOW,
                    target_elements=input_targets,
                    mitigating_scenarios=["Verify essential form controls presence and visibility"],
                )
            )

        rationale = (
            f"Prioritized {len(risks)} risk items based on '{category.value}' domain severity, "
            f"explicit user requirements, and observed DOM input constraints."
        )

        analysis = RiskAnalysis(
            risks=risks,
            unaddressed_requirements=unaddressed_reqs,
            priority_rationale=rationale,
            summary=f"Identified {len(risks)} actionable risks across security, functional, and input validation areas.",
        )

        return {
            "risk_analysis": analysis,
            "current_stage": "RISK_ANALYSIS",
            "workflow_status": WorkflowStatus.ANALYZING,
        }
