"""
app/evidence/oracle_verifier.py — Deterministic oracle and evidence verifier.

Validates that:
1. Evidence IDs referenced in TestIdeas exist in the ledger.
2. Derived boundary values match the rule's deterministic calculation.
3. No server-side assumptions are labeled CONSTRAINT_DERIVED.
4. No duplicate test ideas (by rule_id + evidence_id combination).
5. Constraint values in FieldConstraintEvidence match the field they claim to observe.

No LLM calls. No side effects. Pure validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.evidence.models import (
    EvidenceRecord,
    FieldConstraintEvidence,
    OracleClass,
    TestIdea,
)
from app.evidence.derivation.boundary_value import BoundaryValueEngine


@dataclass
class VerificationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    verified_idea_count: int = 0


class OracleVerifier:
    """
    Deterministic verifier for evidence records and derived test ideas.
    Enforces strict audit standards:
    1. Evidence IDs referenced in TestIdeas must exist in the ledger.
    2. Rule IDs must be in the approved rule catalog.
    3. Derived boundary values must match the rule's exact calculation.
    4. CONSTRAINT_DERIVED must reference valid FieldConstraintEvidence.
    5. SPECIFICATION requires an explicit requirement reference.
    6. No unsupported server-side claims from client HTML attributes.
    7. Duplicate detection without removing distinct test ideas.
    """

    @staticmethod
    def verify(
        evidence_records: List[EvidenceRecord],
        test_ideas: List[TestIdea],
        known_requirements: Optional[List[str]] = None,
    ) -> VerificationResult:
        errors: List[str] = []
        warnings: List[str] = []

        # Build lookup
        ledger: Dict[str, EvidenceRecord] = {ev.evidence_id: ev for ev in evidence_records}
        seen_combos: Set[str] = set()

        for idea in test_ideas:
            # 1. Rule ID must exist in approved catalog
            if idea.rule_id not in BoundaryValueEngine.APPROVED_RULES and not idea.rule_id.startswith("REQ-"):
                errors.append(f"[{idea.idea_id}] Unknown or unapproved rule_id '{idea.rule_id}'")

            # 2. All referenced evidence IDs must exist
            if not idea.evidence_ids and idea.oracle_class == OracleClass.CONSTRAINT_DERIVED:
                errors.append(f"[{idea.idea_id}] CONSTRAINT_DERIVED test idea has no supporting evidence_ids")

            for ev_id in idea.evidence_ids:
                if ev_id not in ledger:
                    errors.append(f"[{idea.idea_id}] references unknown evidence_id '{ev_id}'")
                else:
                    ev = ledger[ev_id]
                    # Verify target field consistency if available
                    if isinstance(ev, FieldConstraintEvidence):
                        expected_attr = idea.derived_values.get("constraint_attr")
                        if expected_attr and ev.constraint_attr != expected_attr:
                            errors.append(
                                f"[{idea.idea_id}] cited constraint_attr '{expected_attr}' does not match evidence constraint_attr '{ev.constraint_attr}'"
                            )

            # 3. Duplicate detection: rule_id + primary evidence_id + boundary_type
            combo_key = f"{idea.rule_id}|{idea.evidence_ids[0] if idea.evidence_ids else 'none'}|{idea.derived_values.get('boundary_type', '')}"
            if combo_key in seen_combos:
                warnings.append(f"[{idea.idea_id}] duplicate idea key '{combo_key}' — possible redundant derivation")
            seen_combos.add(combo_key)

            # 4. CONSTRAINT_DERIVED must reference FieldConstraintEvidence
            if idea.oracle_class == OracleClass.CONSTRAINT_DERIVED:
                for ev_id in idea.evidence_ids:
                    ev = ledger.get(ev_id)
                    if ev is not None and not isinstance(ev, FieldConstraintEvidence):
                        errors.append(
                            f"[{idea.idea_id}] claims CONSTRAINT_DERIVED but evidence '{ev_id}' is not FieldConstraintEvidence (got {type(ev).__name__})"
                        )

            # 5. SPECIFICATION must require actual requirements
            if idea.oracle_class == OracleClass.SPECIFICATION:
                if not known_requirements or len(known_requirements) == 0:
                    errors.append(
                        f"[{idea.idea_id}] claims SPECIFICATION oracle class but no explicit user requirements exist in context"
                    )

            # 6. Validate BVA-MAXLENGTH boundary values
            if idea.rule_id == "BVA-MAXLENGTH-001":
                dv = idea.derived_values
                n = dv.get("maxlength")
                btype = dv.get("boundary_type", "")
                bval = dv.get("boundary_value")
                if n is not None and bval is not None:
                    expected = {
                        "maxlength_below": n - 1,
                        "maxlength_exact": n,
                        "maxlength_above": n + 1,
                    }.get(btype)
                    if expected is not None and bval != expected:
                        errors.append(
                            f"[{idea.idea_id}] BVA-MAXLENGTH-001 boundary_value={bval} does not match expected={expected} for boundary_type='{btype}', maxlength={n}"
                        )

            # 7. Validate BVA-MINLENGTH boundary values
            if idea.rule_id == "BVA-MINLENGTH-001":
                dv = idea.derived_values
                n = dv.get("minlength")
                btype = dv.get("boundary_type", "")
                bval = dv.get("boundary_value")
                if n is not None and bval is not None:
                    expected = {
                        "minlength_below": max(0, n - 1),
                        "minlength_exact": n,
                        "minlength_above": n + 1,
                    }.get(btype)
                    if expected is not None and bval != expected:
                        errors.append(
                            f"[{idea.idea_id}] BVA-MINLENGTH-001 boundary_value={bval} does not match expected={expected} for boundary_type='{btype}', minlength={n}"
                        )

            # 8. Check that expected outcome does not assert unverified server-side behavior
            outcome_lower = idea.expected_outcome.lower()
            if "server rejects" in outcome_lower or "backend blocks" in outcome_lower or "database prevents" in outcome_lower:
                errors.append(
                    f"[{idea.idea_id}] asserts unverified server-side behavior ('{idea.expected_outcome[:60]}...') solely from client-side DOM evidence"
                )

        is_valid = len(errors) == 0
        return VerificationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            verified_idea_count=len(test_ideas),
        )
