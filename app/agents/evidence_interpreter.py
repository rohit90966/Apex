import logging
from typing import List
from app.agents.models import EvidenceAnalysis, ObservedTarget
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class EvidenceInterpreterNode:
    """
    Node B: Evidence Interpreter
    Grounds planning by mapping observed forms, buttons, inputs, and attributes to testable targets.
    Treats page DOM & text as UNTRUSTED DATA. Never executes instructions contained in page text.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        inspection = state.get("inspection_evidence")
        classification = state.get("page_classification")

        targets: List[ObservedTarget] = []
        unobserved: List[str] = []
        missing_context: List[str] = []

        if inspection:
            # Map forms and fields
            for form in inspection.forms:
                for fld in form.fields:
                    actions = ["inspect_presence", "check_accessibility"]
                    itype = (fld.input_type or "text").lower()
                    if itype in ("text", "email", "search", "password", "textarea"):
                        actions.extend(["enter_valid_text", "enter_empty", "test_boundary"])
                    if fld.is_required:
                        actions.append("verify_required_enforcement")

                    targets.append(
                        ObservedTarget(
                            element_id=fld.name or fld.label or f"field_{itype}",
                            element_type=itype,
                            label_or_name=fld.label or fld.name or itype,
                            testable_actions=actions,
                            is_observed_fact=True,
                        )
                    )

            # Map buttons
            for btn in inspection.buttons:
                targets.append(
                    ObservedTarget(
                        element_id=btn,
                        element_type="button",
                        label_or_name=btn,
                        testable_actions=["check_visible", "check_accessibility", "click_submission"],
                        is_observed_fact=True,
                    )
                )

            # Note unobserved / missing context
            if classification:
                cat = classification.page_category
                if cat == "LOGIN":
                    has_mfa = any("mfa" in (t.label_or_name or "").lower() or "code" in (t.label_or_name or "").lower() for t in targets)
                    if not has_mfa:
                        unobserved.append("Multi-Factor Authentication (MFA) input controls")
                    missing_context.append("Valid login credentials not present in static page")
                elif cat == "REGISTRATION":
                    has_captcha = any("captcha" in (t.label_or_name or "").lower() for t in targets)
                    if not has_captcha:
                        unobserved.append("CAPTCHA / Bot prevention challenge")

        summary = (
            f"Mapped {len(targets)} testable DOM elements grounded in inspection evidence. "
            f"Noted {len(unobserved)} unobserved expected patterns."
        )

        analysis = EvidenceAnalysis(
            observed_targets=targets,
            unobserved_features=unobserved,
            missing_context=missing_context,
            summary=summary,
        )

        return {
            "evidence_analysis": analysis,
            "current_stage": "EVIDENCE_INTERPRETATION",
            "workflow_status": WorkflowStatus.ANALYZING,
        }
