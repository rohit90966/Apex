"""
app/evidence/derivation/boundary_value.py — Deterministic Boundary-Value Analysis engine.

Rules implemented (no LLM calls, pure Python):
  BVA-MAXLENGTH-001  : maxlength attribute → n-1, n, n+1 boundary ideas
  BVA-MINLENGTH-001  : minlength attribute → n-1, n, n+1 boundary ideas
  BVA-MIN-001        : min attribute on numeric/date input → below, at, above
  BVA-MAX-001        : max attribute on numeric/date input → below, at, above
  BVA-REQUIRED-001   : required field → empty-value rejection idea
  BVA-PATTERN-001    : pattern attribute → matching and non-matching ideas
  BVA-READONLY-001   : readonly field → immutability observation idea
  BVA-DISABLED-001   : disabled field → inaccessibility observation idea

Mutation guarantee:
  Changing maxlength from 20 to 12 changes all boundary values from {19,20,21} → {11,12,13}.
  The evidence_id and idea_id are deterministic based on field identity.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional

from app.schemas import FormFieldMetadata
from app.evidence.models import (
    EvidenceKind,
    EvidenceStatus,
    FieldConstraintEvidence,
    OracleClass,
    TestIdea,
    make_evidence_id,
)


# ---------------------------------------------------------------------------
# Stable idea ID helper
# ---------------------------------------------------------------------------

def _idea_id(rule_id: str, evidence_id: str, suffix: str) -> str:
    raw = f"{rule_id}|{evidence_id}|{suffix}"
    return "idea-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Boundary Value Analysis engine
# ---------------------------------------------------------------------------

class BoundaryValueEngine:
    """
    Pure-deterministic boundary-value test-idea derivation.
    Input:  FormFieldMetadata (with constraint attributes populated from DOM)
    Output: List[TestIdea] — each traceable to evidence_id + rule_id
    No LLM calls. No side effects.
    """

    # Approved rule catalog
    APPROVED_RULES = {
        "BVA-MAXLENGTH-001",
        "BVA-MINLENGTH-001",
        "BVA-MIN-001",
        "BVA-MAX-001",
        "BVA-REQUIRED-001",
        "BVA-PATTERN-001",
        "BVA-READONLY-001",
        "BVA-DISABLED-001",
        "INVAR-PRESENCE-001",
        "INVAR-MASKING-001",
        "INVAR-A11Y-001",
        "INVAR-LOAD-001",
    }

    # ----- public interface -------------------------------------------------

    @classmethod
    def derive_from_inspection(
        cls,
        fields: List[FormFieldMetadata],
        page_url: str,
        page_id: str,
    ) -> tuple[List[FieldConstraintEvidence], List[TestIdea]]:
        """
        Iterate over all form fields, record constraint evidence, derive test ideas.
        Returns (evidence_records, test_ideas).
        """
        all_evidence: List[FieldConstraintEvidence] = []
        all_ideas: List[TestIdea] = []

        for field in fields:
            evs, ideas = cls.derive_from_field(field, page_url=page_url, page_id=page_id)
            all_evidence.extend(evs)
            all_ideas.extend(ideas)

        return all_evidence, all_ideas

    @classmethod
    def derive_from_field(
        cls,
        field: FormFieldMetadata,
        page_url: str,
        page_id: str,
    ) -> tuple[List[FieldConstraintEvidence], List[TestIdea]]:
        """
        Derive boundary evidence and test ideas for a single field.
        Returns (evidence_records, test_ideas).
        """
        evidence: List[FieldConstraintEvidence] = []
        ideas: List[TestIdea] = []

        field_discriminator = f"field:{field.name or field.field_id or field.label or 'unnamed'}:{field.input_type or 'text'}"

        # maxlength: Precondition: n must be > 0
        if field.maxlength is not None and field.maxlength > 0:
            ev, new_ideas = cls._maxlength(field, page_url, page_id, field_discriminator)
            evidence.append(ev)
            ideas.extend(new_ideas)

        # minlength: Precondition: n >= 0
        if field.minlength is not None and field.minlength >= 0:
            ev, new_ideas = cls._minlength(field, page_url, page_id, field_discriminator)
            evidence.append(ev)
            ideas.extend(new_ideas)

        # min (numeric / date)
        if field.min_value is not None:
            ev, new_ideas = cls._min_value(field, page_url, page_id, field_discriminator)
            if ev and new_ideas:
                evidence.append(ev)
                ideas.extend(new_ideas)

        # max (numeric / date)
        if field.max_value is not None:
            ev, new_ideas = cls._max_value(field, page_url, page_id, field_discriminator)
            if ev and new_ideas:
                evidence.append(ev)
                ideas.extend(new_ideas)

        # required
        if field.is_required:
            ev, idea = cls._required(field, page_url, page_id, field_discriminator)
            evidence.append(ev)
            ideas.append(idea)

        # pattern
        if field.pattern:
            ev, new_ideas = cls._pattern(field, page_url, page_id, field_discriminator)
            if ev and new_ideas:
                evidence.append(ev)
                ideas.extend(new_ideas)

        # readonly
        if field.readonly:
            ev, idea = cls._readonly(field, page_url, page_id, field_discriminator)
            evidence.append(ev)
            ideas.append(idea)

        # disabled
        if field.disabled:
            ev, idea = cls._disabled(field, page_url, page_id, field_discriminator)
            evidence.append(ev)
            ideas.append(idea)

        return evidence, ideas

    # ----- private derivation methods ---------------------------------------

    @staticmethod
    def _make_ev(
        field: FormFieldMetadata,
        page_url: str,
        page_id: str,
        discriminator: str,
        constraint_attr: str,
        constraint_value,
        description: str,
    ) -> FieldConstraintEvidence:
        ev_id = make_evidence_id(page_url, f"{discriminator}:{constraint_attr}")
        return FieldConstraintEvidence(
            evidence_id=ev_id,
            kind=EvidenceKind.FIELD_CONSTRAINT,
            status=EvidenceStatus.OBSERVED,
            page_id=page_id,
            page_url=page_url,
            source="DOM_INSPECTION",
            description=description,
            observed_value=constraint_value,
            field_name=field.name or field.field_id or "unnamed",
            field_label=field.label,
            input_type=field.input_type,
            constraint_attr=constraint_attr,
            constraint_value=constraint_value,
            attributes={"field_discriminator": discriminator},
        )

    @classmethod
    def _maxlength(cls, field, page_url, page_id, disc):
        n = field.maxlength
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "maxlength", n,
            f"Field '{label}' has observed HTML attribute maxlength={n}."
        )
        ideas = []
        for btype, bval, desc, outcome in [
            ("maxlength_below", n - 1,
             f"Propose entering {n-1} characters (below maxlength={n})",
             f"Client-side DOM accepts input; field value length is {n-1}. (Note: Server-side validation limit is unknown until tested)."),
            ("maxlength_exact", n,
             f"Propose entering exactly {n} characters (at maxlength boundary)",
             f"Client-side DOM accepts input; field value length is {n}. (Note: Server-side acceptance is subject to backend limits)."),
            ("maxlength_above", n + 1,
             f"Propose attempting to enter {n+1} characters (above maxlength={n})",
             f"Client-side browser constraint prevents typing beyond {n} characters or truncates at {n} characters. (Note: Server-side behavior if bypassed is unknown)."),
        ]:
            ideas.append(TestIdea(
                idea_id=_idea_id("BVA-MAXLENGTH-001", ev.evidence_id, btype),
                rule_id="BVA-MAXLENGTH-001",
                evidence_ids=[ev.evidence_id],
                oracle_class=OracleClass.CONSTRAINT_DERIVED,
                name=f"Boundary: {label} maxlength={n} — {btype.split('_')[-1]}",
                objective=f"Verify client-side constraint enforcement for {label} with maxlength={n}",
                action_description=desc,
                expected_outcome=outcome,
                derived_values={"boundary_value": bval, "boundary_type": btype, "maxlength": n},
                priority="MEDIUM",
            ))
        return ev, ideas

    @classmethod
    def _minlength(cls, field, page_url, page_id, disc):
        n = field.minlength
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "minlength", n,
            f"Field '{label}' has observed HTML attribute minlength={n}."
        )
        ideas = []
        below_val = max(0, n - 1)
        # Note: HTML minlength allows empty values unless 'required' is also set
        below_outcome = (
            f"If non-empty input of length {below_val} is provided, browser constraint validation flags field as too short per minlength={n}."
            if below_val > 0 else
            f"Empty input does not violate minlength alone under standard HTML5 specs unless 'required' is also present."
        )

        for btype, bval, desc, outcome in [
            ("minlength_below", below_val,
             f"Propose entering {below_val} character(s) (below minlength={n})",
             below_outcome),
            ("minlength_exact", n,
             f"Propose entering exactly {n} characters (at minlength boundary)",
             f"Client-side constraint validation passes minlength={n} check."),
            ("minlength_above", n + 1,
             f"Propose entering {n+1} characters (above minlength={n})",
             f"Client-side constraint validation passes minlength={n} check."),
        ]:
            ideas.append(TestIdea(
                idea_id=_idea_id("BVA-MINLENGTH-001", ev.evidence_id, btype),
                rule_id="BVA-MINLENGTH-001",
                evidence_ids=[ev.evidence_id],
                oracle_class=OracleClass.CONSTRAINT_DERIVED,
                name=f"Boundary: {label} minlength={n} — {btype.split('_')[-1]}",
                objective=f"Verify client-side minlength={n} validation for {label}",
                action_description=desc,
                expected_outcome=outcome,
                derived_values={"boundary_value": bval, "boundary_type": btype, "minlength": n},
                priority="MEDIUM",
            ))
        return ev, ideas

    @classmethod
    def _min_value(cls, field, page_url, page_id, disc):
        val_str = str(field.min_value).strip()
        label = field.label or field.name or "field"
        itype = (field.input_type or "text").lower()

        # Parse numeric step if available
        step_val = 1.0
        if field.step and field.step.lower() != "any":
            try:
                step_val = float(field.step)
            except ValueError:
                step_val = 1.0

        # Try numeric parse
        num_val = None
        try:
            num_val = float(val_str)
        except ValueError:
            num_val = None

        if num_val is not None and itype in ("number", "range", "text"):
            below_val = num_val - step_val
            ev = cls._make_ev(
                field, page_url, page_id, disc, "min", val_str,
                f"Numeric field '{label}' has min={val_str} (step={step_val})."
            )
            ideas = [
                TestIdea(
                    idea_id=_idea_id("BVA-MIN-001", ev.evidence_id, "below_min"),
                    rule_id="BVA-MIN-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} min={val_str} — below minimum",
                    objective=f"Verify client-side validation detects value below min={val_str}",
                    action_description=f"Propose entering numeric value {below_val} (< {val_str})",
                    expected_outcome=f"Client-side HTML constraint validation reports value underflow (< {val_str}).",
                    derived_values={"constraint_attr": "min", "constraint_value": val_str, "boundary_type": "below_min", "boundary_value": below_val},
                    priority="HIGH",
                ),
                TestIdea(
                    idea_id=_idea_id("BVA-MIN-001", ev.evidence_id, "at_min"),
                    rule_id="BVA-MIN-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} min={val_str} — at minimum",
                    objective=f"Verify client-side validation accepts value at min={val_str}",
                    action_description=f"Propose entering numeric value exactly equal to {val_str}",
                    expected_outcome=f"Client-side constraint validation accepts value at min={val_str}.",
                    derived_values={"constraint_attr": "min", "constraint_value": val_str, "boundary_type": "at_min", "boundary_value": num_val},
                    priority="MEDIUM",
                ),
            ]
            return ev, ideas

        # Check date format YYYY-MM-DD
        if itype in ("date", "datetime-local") and re.match(r"^\d{4}-\d{2}-\d{2}", val_str):
            ev = cls._make_ev(
                field, page_url, page_id, disc, "min", val_str,
                f"Date field '{label}' has min date constraint={val_str}."
            )
            ideas = [
                TestIdea(
                    idea_id=_idea_id("BVA-MIN-001", ev.evidence_id, "at_min_date"),
                    rule_id="BVA-MIN-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} min={val_str} — at minimum date",
                    objective=f"Verify date input allows minimum date {val_str}",
                    action_description=f"Propose selecting minimum allowable date {val_str}",
                    expected_outcome=f"Date input accepts minimum date {val_str}.",
                    derived_values={"constraint_attr": "min", "constraint_value": val_str, "boundary_type": "at_min"},
                    priority="MEDIUM",
                ),
            ]
            return ev, ideas

        return None, []

    @classmethod
    def _max_value(cls, field, page_url, page_id, disc):
        val_str = str(field.max_value).strip()
        label = field.label or field.name or "field"
        itype = (field.input_type or "text").lower()

        step_val = 1.0
        if field.step and field.step.lower() != "any":
            try:
                step_val = float(field.step)
            except ValueError:
                step_val = 1.0

        num_val = None
        try:
            num_val = float(val_str)
        except ValueError:
            num_val = None

        if num_val is not None and itype in ("number", "range", "text"):
            above_val = num_val + step_val
            ev = cls._make_ev(
                field, page_url, page_id, disc, "max", val_str,
                f"Numeric field '{label}' has max={val_str} (step={step_val})."
            )
            ideas = [
                TestIdea(
                    idea_id=_idea_id("BVA-MAX-001", ev.evidence_id, "at_max"),
                    rule_id="BVA-MAX-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} max={val_str} — at maximum",
                    objective=f"Verify client-side validation accepts value at max={val_str}",
                    action_description=f"Propose entering numeric value exactly equal to {val_str}",
                    expected_outcome=f"Client-side constraint validation accepts value at max={val_str}.",
                    derived_values={"constraint_attr": "max", "constraint_value": val_str, "boundary_type": "at_max", "boundary_value": num_val},
                    priority="MEDIUM",
                ),
                TestIdea(
                    idea_id=_idea_id("BVA-MAX-001", ev.evidence_id, "above_max"),
                    rule_id="BVA-MAX-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} max={val_str} — above maximum",
                    objective=f"Verify client-side validation detects value above max={val_str}",
                    action_description=f"Propose entering numeric value {above_val} (> {val_str})",
                    expected_outcome=f"Client-side HTML constraint validation reports value overflow (> {val_str}).",
                    derived_values={"constraint_attr": "max", "constraint_value": val_str, "boundary_type": "above_max", "boundary_value": above_val},
                    priority="HIGH",
                ),
            ]
            return ev, ideas

        if itype in ("date", "datetime-local") and re.match(r"^\d{4}-\d{2}-\d{2}", val_str):
            ev = cls._make_ev(
                field, page_url, page_id, disc, "max", val_str,
                f"Date field '{label}' has max date constraint={val_str}."
            )
            ideas = [
                TestIdea(
                    idea_id=_idea_id("BVA-MAX-001", ev.evidence_id, "at_max_date"),
                    rule_id="BVA-MAX-001",
                    evidence_ids=[ev.evidence_id],
                    oracle_class=OracleClass.CONSTRAINT_DERIVED,
                    name=f"Boundary: {label} max={val_str} — at maximum date",
                    objective=f"Verify date input allows maximum date {val_str}",
                    action_description=f"Propose selecting maximum allowable date {val_str}",
                    expected_outcome=f"Date input accepts maximum date {val_str}.",
                    derived_values={"constraint_attr": "max", "constraint_value": val_str, "boundary_type": "at_max"},
                    priority="MEDIUM",
                ),
            ]
            return ev, ideas

        return None, []

    @classmethod
    def _required(cls, field, page_url, page_id, disc):
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "required", True,
            f"Field '{label}' has observed HTML boolean attribute 'required'."
        )
        idea = TestIdea(
            idea_id=_idea_id("BVA-REQUIRED-001", ev.evidence_id, "empty_submit"),
            rule_id="BVA-REQUIRED-001",
            evidence_ids=[ev.evidence_id],
            oracle_class=OracleClass.CONSTRAINT_DERIVED,
            name=f"Required: {label} — client-side empty submission prompt",
            objective=f"Verify browser prompts for required field '{label}' on submission attempt",
            action_description=f"Propose leaving '{label}' empty and attempting client-side form submission",
            expected_outcome=f"Browser HTML5 form validation flags '{label}' as valueMissing and prevents client submission (unless form has novalidate).",
            derived_values={"constraint_attr": "required", "constraint_value": True, "boundary_type": "empty_submit"},
            priority="HIGH",
        )
        return ev, idea

    @classmethod
    def _pattern(cls, field, page_url, page_id, disc):
        pat = field.pattern
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "pattern", pat,
            f"Field '{label}' has observed regex pattern='{pat}'."
        )

        # Validate that the pattern compiles
        try:
            regex = re.compile(pat)
        except re.error:
            # Skip invalid regex without crashing or creating invalid tests
            return None, []

        # Generate verified non-matching string
        test_non_matching = "!!!___INVALID_PATTERN_MATCH___!!!"
        if regex.fullmatch(test_non_matching):
            test_non_matching = ""  # fallback

        ideas = [
            TestIdea(
                idea_id=_idea_id("BVA-PATTERN-001", ev.evidence_id, "non_matching"),
                rule_id="BVA-PATTERN-001",
                evidence_ids=[ev.evidence_id],
                oracle_class=OracleClass.CONSTRAINT_DERIVED,
                name=f"Pattern: {label} — non-matching input triggers patternMismatch",
                objective=f"Verify '{label}' flags input not matching regex pattern '{pat}'",
                action_description=f"Propose entering text not conforming to pattern '{pat}'",
                expected_outcome=f"Browser reports patternMismatch constraint violation per pattern='{pat}'.",
                derived_values={"constraint_attr": "pattern", "constraint_value": pat, "boundary_type": "non_matching", "sample_non_matching": test_non_matching},
                priority="HIGH",
            ),
            TestIdea(
                idea_id=_idea_id("BVA-PATTERN-001", ev.evidence_id, "matching"),
                rule_id="BVA-PATTERN-001",
                evidence_ids=[ev.evidence_id],
                oracle_class=OracleClass.CONSTRAINT_DERIVED,
                name=f"Pattern: {label} — pattern matching verification",
                objective=f"Verify '{label}' accepts input satisfying pattern '{pat}'",
                action_description=f"Propose entering text conforming to pattern '{pat}'",
                expected_outcome=f"Browser constraint validation passes pattern check.",
                derived_values={"constraint_attr": "pattern", "constraint_value": pat, "boundary_type": "matching"},
                priority="MEDIUM",
            ),
        ]
        return ev, ideas

    @classmethod
    def _readonly(cls, field, page_url, page_id, disc):
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "readonly", True,
            f"Field '{label}' has observed HTML attribute 'readonly'."
        )
        idea = TestIdea(
            idea_id=_idea_id("BVA-READONLY-001", ev.evidence_id, "immutable"),
            rule_id="BVA-READONLY-001",
            evidence_ids=[ev.evidence_id],
            oracle_class=OracleClass.CONSTRAINT_DERIVED,
            name=f"Readonly: {label} — client-side field immutability proposal",
            objective=f"Verify readonly field '{label}' prevents direct user text entry",
            action_description=f"Propose verifying that focus on '{label}' does not allow keyboard character edits",
            expected_outcome=f"Client DOM attribute prevents standard user keyboard input for '{label}'.",
            derived_values={"constraint_attr": "readonly", "constraint_value": True, "boundary_type": "immutable"},
            priority="MEDIUM",
        )
        return ev, idea

    @classmethod
    def _disabled(cls, field, page_url, page_id, disc):
        label = field.label or field.name or "field"
        ev = cls._make_ev(
            field, page_url, page_id, disc, "disabled", True,
            f"Field '{label}' has observed HTML attribute 'disabled'."
        )
        idea = TestIdea(
            idea_id=_idea_id("BVA-DISABLED-001", ev.evidence_id, "inaccessible"),
            rule_id="BVA-DISABLED-001",
            evidence_ids=[ev.evidence_id],
            oracle_class=OracleClass.CONSTRAINT_DERIVED,
            name=f"Disabled: {label} — client-side field inaccessibility proposal",
            objective=f"Verify disabled field '{label}' is not interactive",
            action_description=f"Propose inspecting that '{label}' is rendered in a non-interactive state",
            expected_outcome=f"Client DOM attribute marks field '{label}' as disabled and omitted from default tab order.",
            derived_values={"constraint_attr": "disabled", "constraint_value": True, "boundary_type": "inaccessible"},
            priority="LOW",
        )
        return ev, idea
