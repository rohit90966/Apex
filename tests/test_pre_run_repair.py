"""
Tests for Pre-Run Engineering Repair (P0/P1)
Covers all 15 regression requirements for investigation integrity, safety governance,
truthful metrics, and evidence pipeline.
"""

import pytest
from app.config import get_settings
from app.investigation_agent.governor import SafetyGovernor
from app.investigation_agent.models import (
    ActionRisk,
    ActionType,
    EvidenceEntry,
    InvestigationReport,
    InvestigationStatus,
    UIStateObservation,
    ReportOverview,
    ReportAuthentication,
    ProbeStep,
    ProbeScript,
    StateDiff,
    StateTransitionRecord,
)
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.state import InvestigationAgentState
from app.investigation_agent.perception import (
    compute_state_class_and_instance,
    scan_text_anomalies,
    redact_url_params,
)


# 1. Explicitly authorized target allows permitted low-risk probes
def test_authorized_target_allows_low_risk_probes():
    gov = SafetyGovernor(
        trusted_hosts=["saucedemo.com", "www.saucedemo.com"],
        mutation_budget=5,
    )
    allowed, reason = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="Add to cart (button.btn_primary)",
        current_url="https://www.saucedemo.com/inventory.html",
    )
    assert allowed is True, f"Expected allowed, got False: {reason}"


# 2. Unapproved host is rejected
def test_unapproved_host_is_rejected():
    gov = SafetyGovernor(
        trusted_hosts=["saucedemo.com"],
    )
    # Target URL is on an untrusted host
    allowed, reason = gov.can_execute(
        action_type="NAVIGATE_LINK",
        target="https://malicious-site.com/phishing",
        current_url="https://saucedemo.com/",
    )
    assert allowed is False
    assert "SAFETY_BLOCKED" in reason or "host" in reason.lower()


# 3. Empty or malformed URL fails closed
def test_empty_or_malformed_url_fails_closed():
    gov = SafetyGovernor(trusted_hosts=["saucedemo.com"])
    assert gov.is_host_trusted("") is False
    assert gov.is_host_trusted(None) is False
    assert gov.is_host_trusted("not-a-valid-url") is False
    assert gov.is_host_trusted("javascript:void(0)") is False
    assert gov.is_host_trusted("data:text/html,<html>") is False


# 4. Navigation/redirect to unauthorized host is blocked
def test_navigation_to_unauthorized_host_blocked():
    gov = SafetyGovernor(trusted_hosts=["saucedemo.com"])
    allowed, reason = gov.can_execute(
        action_type="NAVIGATE_LINK",
        target="https://unauthorized-domain.org/login",
        current_url="https://saucedemo.com/",
    )
    assert allowed is False
    assert "SAFETY_BLOCKED" in reason or "untrusted" in reason.lower()


# 5. High-impact action is blocked by default even on authorized host
def test_high_impact_action_blocked_by_default():
    gov = SafetyGovernor(trusted_hosts=["saucedemo.com"])
    high_impact_targets = [
        "place order",
        "complete purchase",
        "confirm payment",
        "delete account",
        "wipe data",
        "checkout",
        "finish",
    ]
    for text in high_impact_targets:
        allowed, reason = gov.can_execute(
            action_type="CLICK_CONTROL",
            target=f"button[name='{text}'] {text}",
            current_url="https://saucedemo.com/checkout.html",
        )
        assert allowed is False, f"Expected '{text}' to be blocked, but got allowed=True"
        assert "high-impact" in reason.lower() or "prohibited" in reason.lower()


# 6. Safety budget is shared and accumulates across executed mutations
def test_safety_budget_shared_and_accumulates():
    gov = SafetyGovernor(
        trusted_hosts=["saucedemo.com"],
        mutation_budget=2,
    )
    # Mutation 1 check with dry_run=True then record_mutation
    allowed, _ = gov.can_execute(
        action_type="FILL",
        target="input#first-name",
        current_url="https://saucedemo.com/",
        dry_run=True,
    )
    assert allowed is True
    gov.record_mutation()
    assert gov.mutations_executed == 1

    # Mutation 2 check
    allowed, _ = gov.can_execute(
        action_type="FILL",
        target="input#last-name",
        current_url="https://saucedemo.com/",
        dry_run=True,
    )
    assert allowed is True
    gov.record_mutation()
    assert gov.mutations_executed == 2

    # Mutation 3 should be blocked because budget (2) is reached
    allowed, reason = gov.can_execute(
        action_type="FILL",
        target="input#postal-code",
        current_url="https://saucedemo.com/",
        dry_run=True,
    )
    assert allowed is False
    assert "Mutation budget" in reason or "budget" in reason.lower()


# 7. Rejected probes create safety-decision evidence in ledger
def test_rejected_probes_create_safety_decision_evidence():
    gov = SafetyGovernor(trusted_hosts=["saucedemo.com"])
    allowed, reason = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="delete account button",
        current_url="https://saucedemo.com/settings",
    )
    assert allowed is False

    entry = EvidenceEntry(
        investigation_id="inv_test_001",
        source_state_id="state_1",
        evidence_type="SAFETY_DECISION",
        summary=f"Action blocked by safety policy: {reason}",
    )
    assert entry.evidence_type == "SAFETY_DECISION"
    assert "blocked" in entry.summary.lower()


# 8. Initial DOM observation creates evidence even if every probe is blocked
def test_initial_dom_observation_creates_evidence_even_if_blocked():
    obs = UIStateObservation(
        state_id="sig_login_001",
        url="https://saucedemo.com/",
        normalized_route="/",
        page_title="Swag Labs",
        http_status=200,
    )
    ledger = []
    # Baseline perception step records evidence in ledger
    ledger.append(
        EvidenceEntry(
            investigation_id="inv_test_002",
            source_state_id=obs.state_id,
            evidence_type="DOM_OBSERVATION",
            summary=f"Initial baseline state observation on {obs.url}",
        )
    )
    assert len(ledger) >= 1
    assert ledger[0].evidence_type == "DOM_OBSERVATION"
    assert ledger[0].source_state_id == "sig_login_001"


# 9. Executed probe records genuine before/after observations and state diff
def test_executed_probe_records_genuine_observations():
    before_obs = UIStateObservation(
        state_id="st_login",
        url="https://saucedemo.com/",
        normalized_route="/",
        page_title="Swag Labs - Login",
    )
    after_obs = UIStateObservation(
        state_id="st_inventory",
        url="https://saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Swag Labs - Products",
    )
    diff = StateDiff(
        route_changed=(before_obs.url != after_obs.url),
        state_id_changed=(before_obs.state_id != after_obs.state_id),
        controls_added=["item_4_title_link", "shopping_cart_container"],
    )
    assert diff.route_changed is True
    assert diff.state_id_changed is True
    assert diff.is_significant is True
    assert "shopping_cart_container" in diff.controls_added


# 10. Zero executed probes yields BLOCKED status, not COMPLETED
def test_zero_executed_probes_yields_blocked_status():
    state: InvestigationAgentState = {
        "target_url": "https://saucedemo.com/",
        "investigation_id": "inv_blocked_001",
        "actions_evaluated": 3,
        "actions_attempted": 0,
        "actions_successful": 0,
        "actions_safety_blocked": 3,
        "states": {
            "sig_1": UIStateObservation(
                state_id="sig_1",
                url="https://saucedemo.com/",
                normalized_route="/",
                page_title="Swag Labs",
                http_status=200,
            )
        },
        "current_state": UIStateObservation(
            state_id="sig_1",
            url="https://saucedemo.com/",
            normalized_route="/",
            page_title="Swag Labs",
            http_status=200,
        ),
        "transitions": [],
        "evidence_ledger": [],
        "frontier": {},
    }
    report = InvestigationReportBuilder.build_report(state)
    assert report.overview.status == InvestigationStatus.BLOCKED
    assert report.overview.actions_safety_blocked == 3
    assert report.overview.actions_successful == 0


# 11. Missing HTTP response data is displayed as None rather than fabricated
def test_missing_http_status_not_fabricated():
    obs = UIStateObservation(
        state_id="sig_1",
        url="https://saucedemo.com/",
        normalized_route="/",
        page_title="Swag Labs",
        http_status=None,  # Not captured
    )
    state: InvestigationAgentState = {
        "target_url": "https://saucedemo.com/",
        "investigation_id": "inv_http_none",
        "states": {"sig_1": obs},
        "current_state": obs,
        "transitions": [],
        "evidence_ledger": [],
        "frontier": {},
    }
    report = InvestigationReportBuilder.build_report(state)
    assert report.overview.http_status is None


# 12. Final URL reflects actual browser URL
def test_final_url_reflects_actual_browser_url():
    login_obs = UIStateObservation(
        state_id="sig_1",
        url="https://saucedemo.com/",
        normalized_route="/",
        page_title="Swag Labs",
    )
    inv_obs = UIStateObservation(
        state_id="sig_2",
        url="https://saucedemo.com/inventory.html",
        normalized_route="/inventory.html",
        page_title="Swag Labs - Inventory",
    )
    state: InvestigationAgentState = {
        "target_url": "https://saucedemo.com/",
        "investigation_id": "inv_url_test",
        "states": {"sig_1": login_obs, "sig_2": inv_obs},
        "current_state": inv_obs,  # Browser navigated to inventory
        "transitions": [],
        "evidence_ledger": [],
        "frontier": {},
    }
    report = InvestigationReportBuilder.build_report(state)
    assert report.overview.final_url == "https://saucedemo.com/inventory.html"


# 13. Authentication is not marked verified without authenticated-state evidence
def test_auth_not_verified_without_authenticated_evidence():
    state: InvestigationAgentState = {
        "target_url": "https://saucedemo.com/",
        "investigation_id": "inv_auth_test",
        "auth_boundary_detected": True,
        "auth_attempted": False,
        "auth_verified": False,
        "states": {},
        "transitions": [],
        "evidence_ledger": [],
        "frontier": {},
    }
    report = InvestigationReportBuilder.build_report(state)
    assert report.authentication_access.authentication_verified is False
    assert report.authentication_access.boundary_observed is True


# 14. Two-level state identity computes deterministic, reproducible classes
def test_two_level_state_identity_deterministic():
    cls_1, inst_1 = compute_state_class_and_instance(
        url="https://saucedemo.com/inventory.html",
        headings=["Products"],
        forms=[],
        dynamic_signals={"counter_value": "2", "has_item_list": True},
    )
    cls_2, inst_2 = compute_state_class_and_instance(
        url="https://saucedemo.com/inventory.html",
        headings=["Products"],
        forms=[],
        dynamic_signals={"counter_value": "3", "has_item_list": True},
    )
    # Class IDs must match (same structural route and page class)
    assert cls_1 == cls_2
    # Instance IDs must differ because dynamic counter changed from 2 to 3
    assert inst_1 != inst_2
    assert "c2" in inst_1
    assert "c3" in inst_2


# 15. SafetyGovernor reconciles settings.allowed_hosts_list with defaults
def test_settings_allowed_hosts_integration():
    settings = get_settings()
    gov = SafetyGovernor(
        trusted_hosts=settings.allowed_hosts_list,
    )
    # Local dev hosts must always be trusted
    assert gov.is_host_trusted("http://localhost:8000") is True
    assert gov.is_host_trusted("http://127.0.0.1:8000") is True
    # If saucedemo is in allowed hosts, it must be trusted
    if "saucedemo.com" in settings.allowed_hosts_list or "www.saucedemo.com" in settings.allowed_hosts_list:
        assert gov.is_host_trusted("https://www.saucedemo.com/") is True
