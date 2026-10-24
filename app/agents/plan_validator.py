import logging
from typing import List, Set
from app.schemas import (
    TestCase,
    TestCaseStatus,
    Priority,
    ExpectedBehaviorSource,
)
from app.agents.models import (
    ValidationResult,
    CoverageSummary,
    RequirementCoverage,
)
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class PlanValidatorNode:
    """
    Node G: Deterministic Plan Validator
    Pure Python validation:
    - Verifies all statuses equal PROPOSED
    - Checks required fields and allowed enum values
    - Deduplicates identical test cases
    - Computes deterministic requirement coverage summary
    - Rejects unsupported status transitions or code injection attempts
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        proposed_cases = state.get("proposed_test_cases") or []
        logger.info("[TEST_COUNT_TRACE] stage=validator_pre count=%d", len(proposed_cases))
        req_analysis = state.get("requirement_analysis")
        max_cases = state.get("max_cases", 15)

        errors: List[str] = []
        warnings: List[str] = list(state.get("warnings") or [])

        # 1. Enforce PROPOSED status on all test cases
        sanitized_cases: List[TestCase] = []
        seen_names: Set[str] = set()
        dedup_count = 0

        for idx, tc in enumerate(proposed_cases):
            # Status check
            if tc.status != TestCaseStatus.PROPOSED:
                errors.append(f"Test case {tc.test_id} has invalid status '{tc.status}'. Must be PROPOSED.")
                tc.status = TestCaseStatus.PROPOSED

            # Deduplication
            name_key = tc.name.strip().lower()
            if name_key in seen_names:
                dedup_count += 1
                continue
            seen_names.add(name_key)

            # Prevent code execution strings in actions
            dangerous_tokens = ["exec(", "eval(", "os.system(", "subprocess.", "__import__", "import sys"]
            if any(dt in tc.action_description for dt in dangerous_tokens):
                errors.append(f"Security violation: executable code token detected in {tc.test_id}")
                continue

            # Observation-driven evidence enforcement:
            # 1. Reject test if it originates solely from QA pattern without any observed DOM elements or explicit requirements
            inspection_ev = state.get("inspection_evidence")
            all_observed_names = set()
            if inspection_ev:
                for fld in (inspection_ev.input_fields_summary or []):
                    if fld.name:
                        all_observed_names.add(fld.name.lower())
                    if fld.field_id:
                        all_observed_names.add(fld.field_id.lower())
                for b in (inspection_ev.buttons or []):
                    all_observed_names.add(b.lower())

            has_explicit_req = bool(req_analysis and req_analysis.explicit_requirements)
            if tc.applied_pattern_ids and not has_explicit_req and tc.expected_behavior_source != ExpectedBehaviorSource.REQUIREMENT:
                tc_all_text = f"{tc.name} {tc.objective} {tc.action_description} {' '.join(tc.relevant_elements or [])}".lower()
                has_dom_match = any(obs in tc_all_text for obs in all_observed_names) if all_observed_names else False
                if any(generic in tc_all_text for generic in ["page load", "console error", "document", "html", "load integrity", "asset"]):
                    has_dom_match = True
                if not has_dom_match and all_observed_names:
                    errors.append(f"Test case {tc.test_id} rejected: originates solely from QA pattern without DOM grounding or explicit requirement.")
                    continue


            # 2. Check for unsupported password-length expectations without DOM minlength
            tc_text = f"{tc.name} {tc.objective} {tc.expected_outcome}".lower()
            if any(term in tc_text for term in ["at least 6", "minimum 6", "6-character", "length threshold of 6"]) and tc.expected_behavior_source != ExpectedBehaviorSource.REQUIREMENT:
                has_dom_minlen = False
                if inspection_ev:
                    for fld in (inspection_ev.input_fields_summary or []):
                        if (fld.input_type or "").lower() == "password" and fld.minlength:
                            has_dom_minlen = True
                            break
                if not has_dom_minlen:
                    warnings.append(f"Test case {tc.test_id} has unsupported password length expectation without observed minlength; re-labeled as exploratory.")
                    tc.expected_behavior_source = ExpectedBehaviorSource.INFERRED
                    if "exploratory" not in tc.expected_outcome.lower():
                        tc.expected_outcome = f"{tc.expected_outcome} (Exploratory: password length constraint not confirmed in DOM evidence)."

            sanitized_cases.append(tc)

        # Enforce max limit
        if len(sanitized_cases) > max_cases:
            warnings.append(f"Truncated {len(sanitized_cases) - max_cases} test cases exceeding max limit ({max_cases}).")
            sanitized_cases = sanitized_cases[:max_cases]

        # 2. Compute Requirement Coverage
        coverage_details: List[RequirementCoverage] = []
        covered_cnt = 0
        partially_covered_cnt = 0
        uncovered_cnt = 0

        if req_analysis and req_analysis.explicit_requirements:
            for req in req_analysis.explicit_requirements:
                req_lower = req.text.lower()
                covering_tests = []
                for tc in sanitized_cases:
                    tc_text = f"{tc.name} {tc.objective} {tc.action_description}".lower()
                    words = [w for w in req_lower.split() if len(w) > 3]
                    if req_lower in tc_text or (words and sum(1 for w in words if w in tc_text) >= 2):
                        covering_tests.append(tc.test_id)

                if len(covering_tests) >= 1:
                    status = "COVERED"
                    covered_cnt += 1
                else:
                    status = "UNCOVERED"
                    uncovered_cnt += 1

                coverage_details.append(
                    RequirementCoverage(
                        requirement_id=req.id,
                        requirement_text=req.text,
                        status=status,
                        covering_test_ids=covering_tests,
                    )
                )

        coverage_summary = CoverageSummary(
            covered_count=covered_cnt,
            partially_covered_count=partially_covered_cnt,
            uncovered_count=uncovered_cnt,
            cannot_assess_count=0,
            total_requirements=len(coverage_details),
            details=coverage_details,
        )

        is_valid = len(errors) == 0 and len(sanitized_cases) > 0

        logger.info(
            "[TEST_COUNT_TRACE] stage=validator_post count=%d deduplicated=%d rejected=%d",
            len(sanitized_cases),
            dedup_count,
            len(errors),
        )

        val_result = ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            deduplicated_count=dedup_count,
            coverage=coverage_summary,
        )

        return {
            "proposed_test_cases": sanitized_cases,
            "validation_result": val_result,
            "coverage_summary": coverage_summary,
            "warnings": warnings,
            "current_stage": "PLAN_VALIDATION",
            "workflow_status": WorkflowStatus.COMPLETED if is_valid else WorkflowStatus.FAILED,
        }
