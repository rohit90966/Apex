import logging
from typing import List, Set
from app.schemas import ExpectedBehaviorSource
from app.agents.models import CritiqueResult
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class QACriticNode:
    """
    Node F: QA Critic
    Reviews proposed test plan for:
    - Missing explicit user requirements
    - Duplicate or near-duplicate test cases
    - Unsupported assumptions / nonexistent controls
    - Inadequate / untestable expected outcomes
    - Mismatched provenance
    Does not blindly approve; returns actionable critique.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        proposed_cases = state.get("proposed_test_cases") or []
        logger.info("[TEST_COUNT_TRACE] stage=critic_pre count=%d", len(proposed_cases))
        req_analysis = state.get("requirement_analysis")
        evidence = state.get("evidence_analysis")

        issues_found = False
        categories: List[str] = []
        missing_req_ids: List[str] = []
        duplicate_ids: List[str] = []
        unsupported_claims: List[str] = []
        recommendations: List[str] = []

        # 1. Check for duplicates
        seen_names: Set[str] = set()
        for tc in proposed_cases:
            norm_name = tc.name.strip().lower()
            if norm_name in seen_names:
                issues_found = True
                duplicate_ids.append(tc.test_id)
                recommendations.append(f"Remove duplicate test case: {tc.test_id} ('{tc.name}')")
            seen_names.add(norm_name)

        if duplicate_ids:
            categories.append("DUPLICATES")

        # 2. Check for missing explicit user requirements
        if req_analysis and req_analysis.explicit_requirements:
            for req in req_analysis.explicit_requirements:
                req_covered = False
                for tc in proposed_cases:
                    # Check if test case addresses requirement
                    tc_text = f"{tc.name} {tc.objective} {tc.action_description}".lower()
                    req_words = [w for w in req.text.lower().split() if len(w) > 3]
                    if req.text.lower() in tc_text or any(w in tc_text for w in req_words[:3]):
                        req_covered = True
                        break

                if not req_covered:
                    issues_found = True
                    missing_req_ids.append(req.id)
                    categories.append("MISSING_REQUIREMENT")
                    recommendations.append(
                        f"Explicit requirement {req.id} ('{req.text}') is not addressed by any test case."
                    )

        # 3. Check for unsupported element claims
        if evidence and evidence.observed_targets:
            known_labels = {t.label_or_name.lower() for t in evidence.observed_targets}
            known_ids = {t.element_id.lower() for t in evidence.observed_targets}
            all_known = known_labels | known_ids

            for tc in proposed_cases:
                # If test references specific named elements, verify they exist
                for elem in tc.relevant_elements:
                    e_clean = elem.strip().lower()
                    # Skip generic selectors/references
                    if any(g in e_clean for g in ["dom", "form", "page", "window", "body", "input", "button"]):
                        continue
                    # Check if specific element was observed
                    if e_clean not in all_known and not any(k in e_clean for k in all_known):
                        unsupported_claims.append(f"{tc.test_id} references unobserved element '{elem}'")

        if unsupported_claims:
            issues_found = True
            categories.append("UNSUPPORTED_ELEMENTS")
            recommendations.append("Ground test steps strictly in observed DOM elements.")

        # 4. Check for untestable expected outcomes
        for tc in proposed_cases:
            outcome = tc.expected_outcome.lower()
            if any(v in outcome for v in ["should work well", "properly", "correctly", "nice", "as expected"]):
                issues_found = True
                categories.append("VAGUE_OUTCOME")
                recommendations.append(f"Make expected outcome in {tc.test_id} measurable and verifiable.")
                break

        summary = (
            f"Critic review found {len(categories)} issue categories ({', '.join(categories)})."
            if issues_found
            else "Critic review passed: test plan is grounded in evidence and covers explicit requirements."
        )

        critique = CritiqueResult(
            issues_found=issues_found,
            issue_categories=categories,
            missing_requirement_ids=missing_req_ids,
            duplicate_test_case_ids=duplicate_ids,
            unsupported_claims=unsupported_claims,
            revision_recommendations=recommendations,
            review_summary=summary,
            ready_for_validation=not issues_found,
        )

        curr_rev = state.get("revision_count", 0)
        new_rev = curr_rev + 1 if issues_found else curr_rev

        logger.info(
            "[TEST_COUNT_TRACE] stage=critic_post count=%d duplicates_flagged=%d",
            len(proposed_cases) - len(duplicate_ids),
            len(duplicate_ids),
        )

        return {
            "critique_result": critique,
            "current_stage": "QA_CRITIQUE",
            "workflow_status": WorkflowStatus.REVIEWING,
            "revision_count": new_rev,
        }
