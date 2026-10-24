"""
app/agents/deterministic_derivation.py — DeterministicDerivationNode.

Runs BEFORE PlanGeneratorNode in the LangGraph workflow.
Reads InspectionResult + InvestigationReport, applies BoundaryValueEngine,
verifies results with OracleVerifier, and writes:
  - evidence_ledger: List[FieldConstraintEvidence]
  - test_ideas:      List[TestIdea]
  - unknowns:        List[UnknownItem]

These feed the LLM prompt context so the LLM knows what constraints already
have deterministic test ideas, reducing hallucination of invalid boundaries.

No LLM calls.  No form submissions.  Purely observational.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.evidence.models import (
    EvidenceKind,
    EvidenceRecord,
    EvidenceStatus,
    FieldConstraintEvidence,
    OracleClass,
    TestIdea,
    UnknownItem,
    make_evidence_id,
    make_unknown_id,
)
from app.evidence.oracle_verifier import OracleVerifier
from app.schemas import FormFieldMetadata

logger = logging.getLogger(__name__)


class DeterministicDerivationNode:
    """
    LangGraph node: derives evidence and test ideas deterministically from
    DOM constraints BEFORE the LLM plan generator runs.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        inspection = state.get("inspection_evidence")
        investigation_report = state.get("investigation_report")
        warnings: List[str] = list(state.get("warnings") or [])

        all_evidence: List[FieldConstraintEvidence] = []
        all_ideas: List[TestIdea] = []
        unknowns: List[UnknownItem] = []

        # --- Collect fields from primary inspection ---
        primary_fields: List[FormFieldMetadata] = []
        primary_url = inspection.target_url if inspection else "unknown"
        primary_page_id = inspection.inspection_id if inspection else "unknown"

        if inspection:
            for form in inspection.forms:
                primary_fields.extend(form.fields)

        if primary_fields:
            ev, ideas = BoundaryValueEngine.derive_from_inspection(
                fields=primary_fields,
                page_url=primary_url,
                page_id=primary_page_id,
            )
            all_evidence.extend(ev)
            all_ideas.extend(ideas)
            logger.info(
                "DeterministicDerivation: primary page yielded %d evidence records, %d test ideas",
                len(ev),
                len(ideas),
            )

        seen_evidence_ids: Set[str] = {e.evidence_id for e in all_evidence}
        seen_idea_ids: Set[str] = {i.idea_id for i in all_ideas}

        # --- Collect fields from crawler investigation pages ---
        if investigation_report:
            norm_primary = primary_url.rstrip("/").lower()
            for page_ev in (investigation_report.inventory.pages or []):
                # Avoid re-deriving the primary page already processed above
                if page_ev.url and page_ev.url.rstrip("/").lower() == norm_primary:
                    continue

                for form in (page_ev.forms or []):
                    page_fields = form.fields
                    if page_fields:
                        ev, ideas = BoundaryValueEngine.derive_from_inspection(
                            fields=page_fields,
                            page_url=page_ev.url,
                            page_id=page_ev.page_id,
                        )
                        for e in ev:
                            if e.evidence_id not in seen_evidence_ids:
                                seen_evidence_ids.add(e.evidence_id)
                                all_evidence.append(e)
                        for idea in ideas:
                            if idea.idea_id not in seen_idea_ids:
                                seen_idea_ids.add(idea.idea_id)
                                all_ideas.append(idea)

        # --- When forms have no constraint attributes observed, derive presence & invariants ---
        if not all_evidence and not all_ideas and inspection:
            for form in inspection.forms:
                for fld in form.fields:
                    f_name = fld.name or fld.field_id or fld.label or "unnamed"
                    f_type = fld.input_type or "text"
                    pres_ev_id = make_evidence_id(primary_url, f"field:{f_name}:{f_type}:presence")
                    if pres_ev_id not in seen_evidence_ids:
                        seen_evidence_ids.add(pres_ev_id)
                        all_evidence.append(
                            EvidenceRecord(
                                evidence_id=pres_ev_id,
                                kind=EvidenceKind.FIELD_PRESENCE,
                                status=EvidenceStatus.OBSERVED,
                                page_id=primary_page_id,
                                page_url=primary_url,
                                source="DOM_INSPECTION",
                                description=f"Observed form field '{fld.label or f_name}' of type '{f_type}'",
                                observed_value=f_type,
                                attributes={"field_name": f_name, "input_type": f_type},
                            )
                        )

                    # Password masking invariant idea
                    if f_type.lower() == "password" or "password" in f_name.lower():
                        pw_idea_id = "idea-" + hashlib.sha256(f"INVAR-MASKING-001|{pres_ev_id}".encode()).hexdigest()[:10]
                        if pw_idea_id not in seen_idea_ids:
                            seen_idea_ids.add(pw_idea_id)
                            all_ideas.append(
                                TestIdea(
                                    idea_id=pw_idea_id,
                                    rule_id="INVAR-MASKING-001",
                                    evidence_ids=[pres_ev_id],
                                    oracle_class=OracleClass.GENERIC_HEURISTIC,
                                    name=f"Verify password masking invariant for {f_name}",
                                    objective="Ensure characters typed into password input field are masked and obscured in DOM",
                                    action_description=f"Locate password input '{f_name}', inspect type='password' attribute, and verify character obscuring",
                                    expected_outcome="Field renders masked characters; input type remains 'password' with no plaintext DOM leakage",
                                    derived_values={"field_name": f_name, "input_type": "password"},
                                    priority="HIGH",
                                )
                            )
                    else:
                        pres_idea_id = "idea-" + hashlib.sha256(f"INVAR-PRESENCE-001|{pres_ev_id}".encode()).hexdigest()[:10]
                        if pres_idea_id not in seen_idea_ids and len(all_ideas) < 6:
                            seen_idea_ids.add(pres_idea_id)
                            all_ideas.append(
                                TestIdea(
                                    idea_id=pres_idea_id,
                                    rule_id="INVAR-PRESENCE-001",
                                    evidence_ids=[pres_ev_id],
                                    oracle_class=OracleClass.GENERIC_HEURISTIC,
                                    name=f"Verify presence and rendering of input '{f_name}'",
                                    objective=f"Ensure observed input control '{f_name}' is rendered, visible, and enabled for user input",
                                    action_description=f"Check DOM visibility, layout bounds, and enabled state for input '{f_name}'",
                                    expected_outcome="Input element is visible in the viewport and interactable",
                                    derived_values={"field_name": f_name, "input_type": f_type},
                                    priority="MEDIUM",
                                )
                            )

            # Interactive button accessibility invariant ideas
            for b in (inspection.buttons[:2] if inspection.buttons else []):
                btn_ev_id = make_evidence_id(primary_url, f"button:{b}:presence")
                if btn_ev_id not in seen_evidence_ids:
                    seen_evidence_ids.add(btn_ev_id)
                    all_evidence.append(
                        EvidenceRecord(
                            evidence_id=btn_ev_id,
                            kind=EvidenceKind.FIELD_PRESENCE,
                            status=EvidenceStatus.OBSERVED,
                            page_id=primary_page_id,
                            page_url=primary_url,
                            source="DOM_INSPECTION",
                            description=f"Observed interactive button '{b}'",
                            observed_value=b,
                            attributes={"button_label": b},
                        )
                    )
                btn_idea_id = "idea-" + hashlib.sha256(f"INVAR-A11Y-001|{btn_ev_id}".encode()).hexdigest()[:10]
                if btn_idea_id not in seen_idea_ids:
                    seen_idea_ids.add(btn_idea_id)
                    all_ideas.append(
                        TestIdea(
                            idea_id=btn_idea_id,
                            rule_id="INVAR-A11Y-001",
                            evidence_ids=[btn_ev_id],
                            oracle_class=OracleClass.GENERIC_HEURISTIC,
                            name=f"Verify interactive button accessibility for '{b}'",
                            objective=f"Ensure button '{b}' exposes accessible name, is focusable, and responds to keyboard focus",
                            action_description=f"Inspect accessible name, role, and focus indicator for button '{b}'",
                            expected_outcome=f"Button '{b}' has accessible name and can receive focus",
                            derived_values={"button_label": b},
                            priority="MEDIUM",
                        )
                    )

            # Page load integrity invariant idea
            load_ev_id = make_evidence_id(primary_url, "page:load:integrity")
            if load_ev_id not in seen_evidence_ids:
                seen_evidence_ids.add(load_ev_id)
                all_evidence.append(
                    EvidenceRecord(
                        evidence_id=load_ev_id,
                        kind=EvidenceKind.STRUCTURAL,
                        status=EvidenceStatus.OBSERVED,
                        page_id=primary_page_id,
                        page_url=primary_url,
                        source="DOM_INSPECTION",
                        description=f"Page load structure for '{primary_url}'",
                        observed_value=primary_url,
                    )
                )
            load_idea_id = "idea-" + hashlib.sha256(f"INVAR-LOAD-001|{load_ev_id}".encode()).hexdigest()[:10]
            if load_idea_id not in seen_idea_ids:
                seen_idea_ids.add(load_idea_id)
                all_ideas.append(
                    TestIdea(
                        idea_id=load_idea_id,
                        rule_id="INVAR-LOAD-001",
                        evidence_ids=[load_ev_id],
                        oracle_class=OracleClass.GENERIC_HEURISTIC,
                        name="Verify page load integrity and absence of console errors",
                        objective="Ensure page renders completely without uncaught JavaScript exceptions or fatal asset load failures",
                        action_description=f"Load '{primary_url}' and monitor browser console and network responses",
                        expected_outcome="Page loads with HTTP 200, primary DOM container renders, and no uncaught console errors are emitted",
                        derived_values={"target_url": primary_url},
                        priority="HIGH",
                    )
                )

        # --- Unknowns: fields with no constraint attributes observed ---
        if inspection:
            for form in inspection.forms:
                for fld in form.fields:
                    if not _has_any_constraint(fld):
                        q = (
                            f"Field '{fld.label or fld.name or 'unnamed'}' "
                            f"(type={fld.input_type or 'text'}) has no HTML constraint attributes. "
                            "What are the server-side validation rules?"
                        )
                        unknowns.append(UnknownItem(
                            unknown_id=make_unknown_id(q + primary_url),
                            question=q,
                            category="SERVER_VALIDATION_UNKNOWN",
                            impact="MEDIUM",
                            resolution_criteria=(
                                "Obtain server-side validation spec or API contract "
                                "to derive oracle for this field."
                            ),
                            resolution_source="SPECIFICATION_OR_API_CONTRACT",
                            related_evidence_ids=[
                                make_evidence_id(primary_url, f"field:{fld.name or fld.field_id or fld.label or 'unnamed'}:{fld.input_type or 'text'}:presence")
                            ],
                        ))

        # --- Oracle verification ---
        user_reqs = state.get("user_requirements") or []
        if all_evidence or all_ideas:
            vr = OracleVerifier.verify(all_evidence, all_ideas, known_requirements=user_reqs)
            if not vr.is_valid:
                for err in vr.errors:
                    warnings.append(f"[DeterministicDerivation] OracleVerifier error: {err}")
                    logger.warning("OracleVerifier: %s", err)
            for w in vr.warnings:
                warnings.append(f"[DeterministicDerivation] OracleVerifier warning: {w}")

        logger.info(
            "DeterministicDerivation complete: %d evidence records, %d test ideas, %d unknowns.",
            len(all_evidence),
            len(all_ideas),
            len(unknowns),
        )

        return {
            "evidence_ledger": all_evidence,
            "test_ideas": all_ideas,
            "unknowns": unknowns,
            "warnings": warnings,
            "current_stage": "DETERMINISTIC_DERIVATION",
            "workflow_status": WorkflowStatus.ANALYZING,
        }


def _has_any_constraint(fld: FormFieldMetadata) -> bool:
    """Returns True if any HTML constraint attribute is present."""
    return any([
        fld.maxlength is not None,
        fld.minlength is not None,
        fld.min_value is not None,
        fld.max_value is not None,
        fld.pattern is not None,
        fld.is_required,
        fld.readonly,
        fld.disabled,
    ])
