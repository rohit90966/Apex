import pytest
from app.schemas import FormFieldMetadata
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.evidence.oracle_verifier import OracleVerifier
from app.evidence.models import OracleClass

def test_boundary_value_mutation_maxlength():
    """
    Mutation check requirement:
    Changing maxlength="20" to maxlength="12" must change proposed boundaries accordingly
    from {19, 20, 21} to {11, 12, 13}.
    """
    field_20 = FormFieldMetadata(
        name="username",
        input_type="text",
        label="Username",
        maxlength=20,
    )
    ev_20, ideas_20 = BoundaryValueEngine.derive_from_field(
        field=field_20,
        page_url="https://example.com/login",
        page_id="p-login-1",
    )
    
    assert len(ev_20) == 1
    assert ev_20[0].constraint_attr == "maxlength"
    assert ev_20[0].constraint_value == 20
    
    boundary_values_20 = {idea.derived_values.get("boundary_value") for idea in ideas_20}
    assert boundary_values_20 == {19, 20, 21}
    
    # Verify each idea has stable evidence_id and rule_id
    for idea in ideas_20:
        assert idea.rule_id == "BVA-MAXLENGTH-001"
        assert idea.evidence_ids == [ev_20[0].evidence_id]
        assert idea.oracle_class == OracleClass.CONSTRAINT_DERIVED

    # Mutation: change maxlength to 12
    field_12 = FormFieldMetadata(
        name="username",
        input_type="text",
        label="Username",
        maxlength=12,
    )
    ev_12, ideas_12 = BoundaryValueEngine.derive_from_field(
        field=field_12,
        page_url="https://example.com/login",
        page_id="p-login-1",
    )
    
    assert len(ev_12) == 1
    assert ev_12[0].constraint_value == 12
    
    boundary_values_12 = {idea.derived_values.get("boundary_value") for idea in ideas_12}
    assert boundary_values_12 == {11, 12, 13}

    # Verify with OracleVerifier
    vr_20 = OracleVerifier.verify(ev_20, ideas_20)
    assert vr_20.is_valid is True
    assert len(vr_20.errors) == 0

    vr_12 = OracleVerifier.verify(ev_12, ideas_12)
    assert vr_12.is_valid is True
    assert len(vr_12.errors) == 0


def test_boundary_value_minlength():
    field = FormFieldMetadata(
        name="password",
        input_type="password",
        label="Password",
        minlength=8,
    )
    evs, ideas = BoundaryValueEngine.derive_from_field(
        field=field,
        page_url="https://example.com/register",
        page_id="p-reg-1",
    )
    assert len(evs) == 1
    assert evs[0].constraint_attr == "minlength"
    assert evs[0].constraint_value == 8
    
    boundary_values = {idea.derived_values.get("boundary_value") for idea in ideas}
    assert boundary_values == {7, 8, 9}
    
    vr = OracleVerifier.verify(evs, ideas)
    assert vr.is_valid is True


def test_boundary_value_numeric_min_max_and_required():
    field = FormFieldMetadata(
        name="age",
        input_type="number",
        label="Age",
        min_value="18",
        max_value="65",
        is_required=True,
    )
    evs, ideas = BoundaryValueEngine.derive_from_field(
        field=field,
        page_url="https://example.com/apply",
        page_id="p-app-1",
    )
    
    attrs = {ev.constraint_attr for ev in evs}
    assert attrs == {"min", "max", "required"}
    
    rules = {idea.rule_id for idea in ideas}
    assert rules == {"BVA-MIN-001", "BVA-MAX-001", "BVA-REQUIRED-001"}
    
    vr = OracleVerifier.verify(evs, ideas)
    assert vr.is_valid is True
    assert len(vr.errors) == 0


def test_bva_derived_values_contain_explicit_boundary_types():
    """Verify that all BVA rules populate boundary_type to prevent duplicate combo keys."""
    # Test required field
    req_field = FormFieldMetadata(name="email", input_type="email", is_required=True)
    evs_req, ideas_req = BoundaryValueEngine.derive_from_field(req_field, "https://example.com", "p1")
    assert ideas_req[0].derived_values.get("boundary_type") == "empty_submit"

    # Test pattern field
    pat_field = FormFieldMetadata(name="zip", input_type="text", pattern=r"^\d{5}$")
    evs_pat, ideas_pat = BoundaryValueEngine.derive_from_field(pat_field, "https://example.com", "p1")
    assert {i.derived_values.get("boundary_type") for i in ideas_pat} == {"non_matching", "matching"}

    # Test readonly field
    ro_field = FormFieldMetadata(name="account_id", input_type="text", readonly=True)
    evs_ro, ideas_ro = BoundaryValueEngine.derive_from_field(ro_field, "https://example.com", "p1")
    assert ideas_ro[0].derived_values.get("boundary_type") == "immutable"

    # Test disabled field
    dis_field = FormFieldMetadata(name="submit_btn", input_type="submit", disabled=True)
    evs_dis, ideas_dis = BoundaryValueEngine.derive_from_field(dis_field, "https://example.com", "p1")
    assert ideas_dis[0].derived_values.get("boundary_type") == "inaccessible"


def test_multiple_required_fields_have_distinct_keys_and_no_warnings():
    """Verify multiple required fields on the same page produce distinct combo keys with zero warnings."""
    f1 = FormFieldMetadata(name="username", input_type="text", is_required=True)
    f2 = FormFieldMetadata(name="password", input_type="password", is_required=True)

    evs, ideas = BoundaryValueEngine.derive_from_inspection(
        fields=[f1, f2],
        page_url="https://example.com/login",
        page_id="p-login-1",
    )
    assert len(evs) == 2
    assert len(ideas) == 2
    assert evs[0].evidence_id != evs[1].evidence_id

    vr = OracleVerifier.verify(evs, ideas)
    assert vr.is_valid is True
    assert len(vr.errors) == 0
    # No duplicate warnings
    assert not any("duplicate idea key" in w for w in vr.warnings)


def test_deterministic_derivation_node_skips_primary_page_in_crawler_inventory():
    """Verify DeterministicDerivationNode deduplicates crawler pages matching primary URL."""
    from app.agents.deterministic_derivation import DeterministicDerivationNode
    from app.schemas import FormMetadata, InspectionResult
    from app.investigation.models import InvestigationReport, PageInventory, PageEvidence

    primary_url = "http://127.0.0.1:8001/login"
    f_user = FormFieldMetadata(name="username", input_type="text", is_required=True)
    f_pass = FormFieldMetadata(name="password", input_type="password", is_required=True)
    form = FormMetadata(form_id="login-form", fields=[f_user, f_pass])

    inspection = InspectionResult(
        inspection_id="insp-primary",
        target_url=primary_url,
        final_url=primary_url,
        page_title="Login",
        forms=[form],
    )

    # Crawler inventory also contains the primary page (same fields)
    crawler_page = PageEvidence(
        page_id="page-crawl-1",
        url=primary_url,
        final_url=primary_url,
        page_title="Login",
        forms=[form],
    )
    inv_report = InvestigationReport(
        investigation_id="inv-1",
        starting_url=primary_url,
        origin="http://127.0.0.1:8001",
        inventory=PageInventory(
            pages=[crawler_page],
            total_discovered=1,
            total_inspected=1,
        ),
    )

    state = {
        "inspection_evidence": inspection,
        "investigation_report": inv_report,
        "warnings": [],
    }

    result = DeterministicDerivationNode.run(state)
    # Must derive exactly 2 evidence records and 2 test ideas, not 4!
    assert len(result["evidence_ledger"]) == 2
    assert len(result["test_ideas"]) == 2
    # OracleVerifier warnings must NOT contain any duplicate idea warnings!
    dup_warnings = [w for w in result["warnings"] if "duplicate idea key" in w]
    assert len(dup_warnings) == 0

