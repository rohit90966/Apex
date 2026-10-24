import pytest
from app.schemas import FormFieldMetadata, FormMetadata, PageClassification, PageCategory, InspectionResult
from app.evidence.models import (
    FieldConstraintEvidence,
    EvidenceKind,
    EvidenceStatus,
    OracleClass,
    TestIdea,
    make_evidence_id,
)
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.evidence.oracle_verifier import OracleVerifier
from app.investigation.models import InvestigationReport, PageEvidence, PageInventory


def test_oracle_verifier_rejects_unapproved_rule_id():
    ev = FieldConstraintEvidence(
        evidence_id="ev-123456789012",
        kind=EvidenceKind.FIELD_CONSTRAINT,
        status=EvidenceStatus.OBSERVED,
        page_id="p-1",
        page_url="http://127.0.0.1/test",
        description="field constraint",
        observed_value=20,
        field_name="user",
        constraint_attr="maxlength",
        constraint_value=20,
    )
    fake_idea = TestIdea(
        idea_id="idea-fake-1",
        rule_id="UNAPPROVED-RULE-999",  # not in approved catalog
        evidence_ids=[ev.evidence_id],
        oracle_class=OracleClass.CONSTRAINT_DERIVED,
        name="Fake test",
        objective="Verify fake rule",
        action_description="Do fake action",
        expected_outcome="Outcome",
        derived_values={"maxlength": 20, "boundary_type": "maxlength_exact", "boundary_value": 20},
    )
    res = OracleVerifier.verify([ev], [fake_idea])
    assert res.is_valid is False
    assert any("Unknown or unapproved rule_id" in err for err in res.errors)


def test_oracle_verifier_rejects_missing_evidence_id():
    fake_idea = TestIdea(
        idea_id="idea-fake-2",
        rule_id="BVA-MAXLENGTH-001",
        evidence_ids=["ev-nonexistent-999"],  # not in ledger
        oracle_class=OracleClass.CONSTRAINT_DERIVED,
        name="Test referencing nonexistent evidence",
        objective="Verify missing evidence",
        action_description="Action",
        expected_outcome="Outcome",
        derived_values={"maxlength": 20, "boundary_type": "maxlength_exact", "boundary_value": 20},
    )
    res = OracleVerifier.verify([], [fake_idea])
    assert res.is_valid is False
    assert any("references unknown evidence_id" in err for err in res.errors)


def test_oracle_verifier_rejects_server_side_assumption():
    ev = FieldConstraintEvidence(
        evidence_id="ev-123456789012",
        kind=EvidenceKind.FIELD_CONSTRAINT,
        status=EvidenceStatus.OBSERVED,
        page_id="p-1",
        page_url="http://127.0.0.1/test",
        description="field constraint",
        observed_value=20,
        field_name="user",
        constraint_attr="maxlength",
        constraint_value=20,
    )
    bad_idea = TestIdea(
        idea_id="idea-bad-server",
        rule_id="BVA-MAXLENGTH-001",
        evidence_ids=[ev.evidence_id],
        oracle_class=OracleClass.CONSTRAINT_DERIVED,
        name="Test claiming server rejection",
        objective="Verify server",
        action_description="Type 21 chars",
        expected_outcome="Server rejects submission with HTTP 400 Bad Request error.",
        derived_values={"maxlength": 20, "boundary_type": "maxlength_above", "boundary_value": 21},
    )
    res = OracleVerifier.verify([ev], [bad_idea])
    assert res.is_valid is False
    assert any("asserts unverified server-side behavior" in err for err in res.errors)


def test_oracle_verifier_rejects_unsupported_specification_claim():
    fake_idea = TestIdea(
        idea_id="idea-spec-1",
        rule_id="REQ-01",
        evidence_ids=[],
        oracle_class=OracleClass.SPECIFICATION,
        name="Test claiming spec",
        objective="Verify spec",
        action_description="Action",
        expected_outcome="Outcome",
    )
    # When no user requirements provided in context
    res = OracleVerifier.verify([], [fake_idea], known_requirements=[])
    assert res.is_valid is False
    assert any("claims SPECIFICATION oracle class but no explicit user requirements exist" in err for err in res.errors)


def test_pattern_rule_handles_invalid_regex_gracefully():
    # If DOM contains an invalid/unparseable regex, BoundaryValueEngine must not crash
    field = FormFieldMetadata(
        name="test_regex",
        input_type="text",
        label="Test Regex",
        pattern="[a-z(unclosed_group",
    )
    evs, ideas = BoundaryValueEngine.derive_from_field(
        field=field,
        page_url="http://127.0.0.1/regex",
        page_id="p-reg",
    )
    # Must skip invalid regex without error
    assert evs == []
    assert ideas == []


def test_numeric_step_and_date_boundaries():
    field_step = FormFieldMetadata(
        name="quantity",
        input_type="number",
        label="Quantity",
        min_value="5",
        max_value="25",
        step="5",
    )
    evs, ideas = BoundaryValueEngine.derive_from_field(
        field=field_step,
        page_url="http://127.0.0.1/cart",
        page_id="p-cart",
    )
    below_min = next(i for i in ideas if i.derived_values.get("boundary_type") == "below_min")
    assert below_min.derived_values["boundary_value"] == 0.0  # 5 - 5

    above_max = next(i for i in ideas if i.derived_values.get("boundary_type") == "above_max")
    assert above_max.derived_values["boundary_value"] == 30.0  # 25 + 5


@pytest.mark.asyncio
async def test_secondary_page_crawler_evidence_propagation():
    """
    Test that evidence collected from a secondary crawler page reaches
    DeterministicDerivationNode and produces verified test ideas.
    """
    from app.agents.deterministic_derivation import DeterministicDerivationNode

    primary_insp = InspectionResult(
        inspection_id="insp-primary",
        target_url="http://127.0.0.1:8001/",
        final_url="http://127.0.0.1:8001/",
        page_title="Home",
        forms=[],
    )

    secondary_page = PageEvidence(
        page_id="p-checkout-2",
        url="http://127.0.0.1:8001/checkout",
        final_url="http://127.0.0.1:8001/checkout",
        page_title="Checkout",
        forms=[
            FormMetadata(
                form_id="checkout-form",
                fields=[
                    FormFieldMetadata(
                        name="zipcode",
                        input_type="text",
                        label="Zip Code",
                        maxlength=5,
                        minlength=5,
                        is_required=True,
                    )
                ]
            )
        ]
    )

    inv_report = InvestigationReport(
        investigation_id="inv-test-99",
        starting_url="http://127.0.0.1:8001/",
        origin="http://127.0.0.1:8001/",
        inventory=PageInventory(
            total_discovered=2,
            total_inspected=2,
            pages=[secondary_page],
        )
    )

    state = {
        "inspection_evidence": primary_insp,
        "investigation_report": inv_report,
        "warnings": [],
    }

    result = DeterministicDerivationNode.run(state)
    ev_ledger = result["evidence_ledger"]
    test_ideas = result["test_ideas"]

    # Secondary page fields must have yielded evidence and test ideas
    assert len(ev_ledger) >= 3  # maxlength, minlength, required
    assert any(ev.field_name == "zipcode" for ev in ev_ledger)
    assert any(i.derived_values.get("maxlength") == 5 for i in test_ideas)
    assert any(i.derived_values.get("minlength") == 5 for i in test_ideas)
