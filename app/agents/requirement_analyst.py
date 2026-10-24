import logging
from typing import List, Optional
from app.agents.models import RequirementAnalysis, RequirementItem
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class RequirementAnalystNode:
    """
    Node A: Requirement Analyst
    Extracts explicit user requirements, objectives, constraints, and identifies ambiguities.
    Never invents unstated business rules.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        req_texts = state.get("user_requirements") or []
        desc = state.get("website_description") or ""

        items: List[RequirementItem] = []
        ambiguities: List[str] = []
        constraints: List[str] = []

        for idx, text in enumerate(req_texts):
            clean_text = text.strip()
            if not clean_text:
                continue

            req_id = f"REQ-{idx+1:02d}"
            item_constraints = []
            item_ambiguities = []
            item_objectives = [clean_text]

            # Detect constraints and edge qualifiers
            lower_text = clean_text.lower()
            if any(w in lower_text for w in ["must", "required", "shall", "cannot", "prevent", "lock", "limit"]):
                item_constraints.append("Enforced behavioral constraint")

            # Check if ambiguous / underspecified
            if any(w in lower_text for w in ["appropriate", "fast", "user-friendly", "secure", "properly"]):
                ambig_note = f"Qualitative term without measurable criteria in '{clean_text}'"
                item_ambiguities.append(ambig_note)
                ambiguities.append(ambig_note)

            items.append(
                RequirementItem(
                    id=req_id,
                    text=clean_text,
                    is_explicit=True,
                    objectives=item_objectives,
                    constraints=item_constraints,
                    ambiguities=item_ambiguities,
                    assumptions=[],
                )
            )

        summary = (
            f"Analyzed {len(items)} explicit user requirement(s)."
            if items
            else "No explicit testing requirements provided; baseline QA invariants will be primary."
        )

        analysis = RequirementAnalysis(
            explicit_requirements=items,
            inferred_intent=desc if desc else None,
            overall_ambiguities=ambiguities,
            identified_constraints=constraints,
            summary=summary,
        )

        return {
            "requirement_analysis": analysis,
            "current_stage": "REQUIREMENT_ANALYSIS",
            "workflow_status": WorkflowStatus.ANALYZING,
        }
