"""
Stable, Versioned Investigation-to-Test-Generation Handoff Contract (v1.0.0).
=============================================================================
Defines the canonical, schema-validated output artifact produced by the frozen
VerityQA Investigation Engine and consumed by the Test-Case Generation module
without depending on internal LangGraph runtime state.

Guarantees:
- Strict versioned schema (1.0.0) with deterministic validation rules.
- Full evidence referential integrity (no invented or dangling evidence IDs).
- Screenshot references included ONLY when the artifact file actually exists on disk.
- Complete redaction of secrets, credentials, session cookies, and auth tokens.
"""

import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field, model_validator

from app.investigation_agent.perception import redact_url_params

CONTRACT_SCHEMA_VERSION = "1.0.0"

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|authorization|cookie|session_id|bench_session|jwt|bearer)",
    re.IGNORECASE,
)

SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"bench_session\s*=\s*[^\s;\"']+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]+", re.IGNORECASE),
    re.compile(r"secret_benchmark", re.IGNORECASE),
]


def sanitize_text_secrets(text: Optional[str], extra_secrets: Optional[Set[str]] = None) -> Optional[str]:
    """Redacts sensitive credentials, tokens, or session cookies from free text."""
    if text is None:
        return None
    cleaned = str(text)
    for pat in SENSITIVE_VALUE_PATTERNS:
        cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
    if extra_secrets:
        for sec in extra_secrets:
            if sec and len(sec) >= 4 and sec in cleaned:
                cleaned = cleaned.replace(sec, "[REDACTED_SECRET]")
    return cleaned


def _deep_sanitize_obj(obj: Any, extra_secrets: Optional[Set[str]] = None) -> Any:
    """Recursively redacts sensitive secrets from nested dictionaries, lists, and strings."""
    if isinstance(obj, str):
        return sanitize_text_secrets(obj, extra_secrets)
    if isinstance(obj, list):
        return [_deep_sanitize_obj(item, extra_secrets) for item in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SENSITIVE_KEY_PATTERN.search(k):
                out[k] = "[REDACTED_SECRET]"
            else:
                out[k] = _deep_sanitize_obj(v, extra_secrets)
        return out
    return obj


def _verify_artifact_exists(artifact_id: Optional[str]) -> Optional[str]:
    """
    Returns artifact_id ONLY if a corresponding file actually exists on disk.
    Never claims screenshots or traces exist when they were not captured to disk.
    """
    if not artifact_id or not isinstance(artifact_id, str):
        return None
    cand = Path(artifact_id)
    if cand.is_file():
        return artifact_id
    for base_dir in ("artifacts", "reports/screenshots", "screenshots"):
        p = Path(base_dir) / artifact_id
        if p.is_file():
            return str(p)
        p_png = Path(base_dir) / f"{artifact_id}.png"
        if p_png.is_file():
            return str(p_png)
    return None


# ---------------------------------------------------------------------------
# A. Application Model Structures
# ---------------------------------------------------------------------------

class ContractControlDescriptor(BaseModel):
    element_id: str
    role: str
    accessible_name: str = ""
    tag_name: str
    selector: str
    element_type: Optional[str] = None
    is_enabled: bool = True
    is_visible: bool = True
    declared_constraints: Dict[str, Any] = Field(default_factory=dict)


class ContractFormDescriptor(BaseModel):
    form_id: str
    form_selector: str
    action: str = ""
    method: str = "GET"
    submit_control_selector: Optional[str] = None
    fields: List[Dict[str, Any]] = Field(default_factory=list)


class ContractStateRecord(BaseModel):
    state_id: str
    state_class_id: str = ""
    url: str
    normalized_route: str
    page_title: str = ""
    description: str = ""
    state_signature: str
    distinguishing_observations: List[str] = Field(default_factory=list)
    requires_authentication: bool = False
    known_preconditions: List[str] = Field(default_factory=list)
    url_parameters: Dict[str, str] = Field(default_factory=dict)
    in_page_state: Dict[str, Any] = Field(default_factory=dict)
    headings: List[str] = Field(default_factory=list)
    interactive_controls: List[ContractControlDescriptor] = Field(default_factory=list)
    forms: List[ContractFormDescriptor] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# B. Verified Transitions Structures
# ---------------------------------------------------------------------------

class ContractGroundedControl(BaseModel):
    selector: Optional[str] = None
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    target_description: str = ""


class ContractTransitionRecord(BaseModel):
    transition_id: str
    source_state_id: str
    destination_state_id: Optional[str] = None
    action_type: str
    grounded_control: ContractGroundedControl
    required_preconditions: List[str] = Field(default_factory=list)
    observed_postconditions: List[str] = Field(default_factory=list)
    actual_effect: str  # ROUTE_TRANSITION, IN_PAGE_STATE_MUTATION, VALIDATION_FEEDBACK, NO_OBSERVABLE_CHANGE, SAFETY_BLOCKED
    verification_outcome: str  # VERIFIED, BLOCKED, FAILED, UNCERTAIN
    repeatability: str  # REPEATABLE, BLOCKED, UNCERTAIN
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# C. Evidence Structures
# ---------------------------------------------------------------------------

class ContractEvidenceRecord(BaseModel):
    evidence_id: str
    timestamp: float
    associated_state_id: str
    before_state_id: Optional[str] = None
    after_state_id: Optional[str] = None
    evidence_type: str
    dom_or_a11y_observation: str
    action_summary: Optional[str] = None
    pre_snapshot_summary: Optional[str] = None
    post_snapshot_summary: Optional[str] = None
    observed_result: str
    verification_status: str  # VERIFIED, BLOCKED, OBSERVED, UNVERIFIED
    locator: Optional[str] = None
    validation_message: Optional[str] = None
    screenshot_artifact_path: Optional[str] = None
    trace_artifact_path: Optional[str] = None


# ---------------------------------------------------------------------------
# D. Defect Findings Structures
# ---------------------------------------------------------------------------

class ContractDefectFinding(BaseModel):
    finding_id: str
    title: str
    classification: str  # CONFIRMED_DEFECT, UNVERIFIED_SUSPICION
    oracle_type: str
    severity: str = "MEDIUM"
    state_class: str = ""
    target_control: Optional[str] = None
    reproduction_verified: bool = False
    reproduction_steps: List[Dict[str, Any]] = Field(default_factory=list)
    expected_behavior: Optional[str] = None
    actual_behavior: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.5
    limitations: Optional[str] = None


# ---------------------------------------------------------------------------
# E. Investigation Completeness Structures
# ---------------------------------------------------------------------------

class ContractPendingCandidate(BaseModel):
    candidate_id: str
    candidate_type: str
    target: str
    parent_state_id: Optional[str] = None
    unresolved_reason: str


class ContractSafetyBlockedAction(BaseModel):
    action_type: str
    target: str
    source_state_id: str
    reason: str
    evidence_id: Optional[str] = None


class ContractCompletenessSummary(BaseModel):
    discovered_states_count: int
    verified_transitions_count: int
    pending_frontier_candidates: List[ContractPendingCandidate] = Field(default_factory=list)
    unresolved_reasons_breakdown: Dict[str, int] = Field(default_factory=dict)
    safety_blocked_actions: List[ContractSafetyBlockedAction] = Field(default_factory=list)
    authentication_boundaries: Dict[str, Any] = Field(default_factory=dict)
    investigation_uncertainty: float = 0.0
    stopping_reason: str = ""
    completion_status: str = "completed"


# ---------------------------------------------------------------------------
# Root Contract Schema
# ---------------------------------------------------------------------------

class InvestigationHandoffContract(BaseModel):
    schema_version: str = CONTRACT_SCHEMA_VERSION
    investigation_id: str
    exported_at: float = Field(default_factory=time.time)
    application_origin: str
    target_url: str
    investigated_routes: List[str] = Field(default_factory=list)

    # A. Application Model
    states: List[ContractStateRecord] = Field(default_factory=list)

    # B. Verified Transitions
    verified_transitions: List[ContractTransitionRecord] = Field(default_factory=list)

    # C. Evidence
    evidence_ledger: List[ContractEvidenceRecord] = Field(default_factory=list)

    # D. Defect Findings
    defect_findings: List[ContractDefectFinding] = Field(default_factory=list)

    # E. Investigation Completeness
    completeness: ContractCompletenessSummary

    # Optional Reconstructed User Journeys for Workflow Synthesis
    inferred_journeys: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract_invariants(self) -> "InvestigationHandoffContract":
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported contract schema_version '{self.schema_version}', expected '{CONTRACT_SCHEMA_VERSION}'")

        known_evidence_ids = {ev.evidence_id for ev in self.evidence_ledger}
        known_state_ids = {st.state_id for st in self.states}

        # Verify transition state and evidence references
        for tr in self.verified_transitions:
            if known_state_ids and tr.source_state_id not in known_state_ids and tr.source_state_id != "initial":
                raise ValueError(f"Transition '{tr.transition_id}' references unknown source_state_id '{tr.source_state_id}'")
            if tr.destination_state_id and known_state_ids and tr.destination_state_id not in known_state_ids:
                raise ValueError(f"Transition '{tr.transition_id}' references unknown destination_state_id '{tr.destination_state_id}'")
            for eid in tr.evidence_ids:
                if eid not in known_evidence_ids:
                    raise ValueError(f"Transition '{tr.transition_id}' references non-existent evidence_id '{eid}'")

        # Verify defect finding evidence references and classification rules
        for df in self.defect_findings:
            if df.classification not in ("CONFIRMED_DEFECT", "UNVERIFIED_SUSPICION"):
                raise ValueError(f"Invalid defect classification '{df.classification}'")
            for eid in df.evidence_ids:
                if eid not in known_evidence_ids:
                    raise ValueError(f"Defect finding '{df.finding_id}' references non-existent evidence_id '{eid}'")
            if df.classification == "CONFIRMED_DEFECT" and not df.reproduction_verified:
                raise ValueError(f"Defect '{df.finding_id}' marked CONFIRMED_DEFECT without reproduction_verified=True")

        # Verify no nonexistent screenshot paths are claimed
        for ev in self.evidence_ledger:
            if ev.screenshot_artifact_path and not Path(ev.screenshot_artifact_path).is_file():
                raise ValueError(f"Evidence '{ev.evidence_id}' claims non-existent screenshot file '{ev.screenshot_artifact_path}'")

        return self


def _is_positive_counter(val: Any) -> bool:
    """Safely checks if counter_value is positive, handling str ('1', 'c1') and int (1)."""
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        val_clean = val.strip().lower().replace("c", "")
        if val_clean.isdigit():
            return int(val_clean) > 0
    return False


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Safely retrieves an attribute or dictionary key from an in-memory object or checkpoint dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_handoff_contract(
    final_state: Dict[str, Any],
    report: Optional[Any] = None,
) -> InvestigationHandoffContract:
    """
    Constructs and validates an InvestigationHandoffContract from a completed
    investigation state and/or InvestigationReport.
    Strips all credentials, secrets, session tokens, and unverified artifact claims.
    """
    inv_id = final_state.get("investigation_id") or (_get(_get(report, "overview"), "investigation_id", "inv-0001") if report else "inv-0001")
    raw_target_url = final_state.get("target_url") or (_get(_get(report, "overview"), "target_url", "") if report else "")
    target_url = redact_url_params(raw_target_url)

    parsed_origin = urllib.parse.urlparse(target_url)
    app_origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}" if parsed_origin.scheme and parsed_origin.netloc else target_url

    # Collect secret values to scrub
    extra_secrets: Set[str] = set()
    raw_creds = final_state.get("auth_credentials") or {}
    if isinstance(raw_creds, dict):
        for k, v in raw_creds.items():
            if v and isinstance(v, str) and len(v) >= 4 and k.lower() in ("password", "passwd", "secret", "token", "api_key"):
                extra_secrets.add(v)

    states_dict: Dict[str, Any] = final_state.get("states") or {}
    transitions_list: List[Any] = final_state.get("transitions") or []
    raw_ledger: List[Any] = final_state.get("evidence_ledger") or []
    agenda = final_state.get("agenda")

    # 1. Build Evidence Ledger First (so we have canonical evidence IDs)
    contract_evidence: List[ContractEvidenceRecord] = []
    seen_ev_ids: Set[str] = set()

    for ev in raw_ledger:
        eid = _get(ev, "evidence_id", None)
        if not eid or eid in seen_ev_ids:
            continue
        seen_ev_ids.add(eid)

        ev_type = _get(ev, "evidence_type", "DOM_OBSERVATION")
        src_st = _get(ev, "source_state_id", "initial")
        before_st = _get(ev, "before_state_ref", None) or src_st
        after_st = _get(ev, "after_state_ref", None)
        raw_summary = sanitize_text_secrets(_get(ev, "summary", ""), extra_secrets) or ""
        val_msg = sanitize_text_secrets(_get(ev, "validation_message", None), extra_secrets)
        loc = _get(ev, "locator", None)
        raw_shot = _get(ev, "screenshot_artifact_id", None)
        verified_shot = _verify_artifact_exists(raw_shot)

        if ev_type == "SAFETY_DECISION":
            v_status = "BLOCKED"
        elif ev_type in ("PROBE_RESULT", "STATE_TRANSITION", "AUTH_VERIFICATION"):
            v_status = "VERIFIED"
        else:
            v_status = "OBSERVED"

        pre_snap = None
        post_snap = None
        if before_st and before_st in states_dict:
            b_obs = states_dict[before_st]
            pre_snap = f"URL={redact_url_params(_get(b_obs, 'url', ''))} | title='{_get(b_obs, 'page_title', '')}' | controls={len(_get(b_obs, 'interactive_controls', []))}"
        if after_st and after_st in states_dict:
            a_obs = states_dict[after_st]
            post_snap = f"URL={redact_url_params(_get(a_obs, 'url', ''))} | title='{_get(a_obs, 'page_title', '')}' | controls={len(_get(a_obs, 'interactive_controls', []))}"

        contract_evidence.append(
            ContractEvidenceRecord(
                evidence_id=eid,
                timestamp=float(_get(ev, "timestamp", time.time())),
                associated_state_id=src_st,
                before_state_id=before_st,
                after_state_id=after_st,
                evidence_type=ev_type,
                dom_or_a11y_observation=raw_summary,
                action_summary=raw_summary if ev_type != "DOM_OBSERVATION" else None,
                pre_snapshot_summary=pre_snap,
                post_snapshot_summary=post_snap,
                observed_result=raw_summary,
                verification_status=v_status,
                locator=loc,
                validation_message=val_msg,
                screenshot_artifact_path=verified_shot,
                trace_artifact_path=None,
            )
        )

    # 2. Build States
    contract_states: List[ContractStateRecord] = []
    investigated_routes: List[str] = []
    auth_login_url = _get(_get(report, "authentication_access"), "login_url", None) if report else None

    for st_id, obs in states_dict.items():
        clean_url = redact_url_params(_get(obs, "url", ""))
        norm_route = redact_url_params(_get(obs, "normalized_route", "") or clean_url)
        if norm_route and norm_route not in investigated_routes:
            investigated_routes.append(norm_route)

        parsed_u = urllib.parse.urlparse(clean_url)
        url_params: Dict[str, str] = {}
        for k, v in urllib.parse.parse_qsl(parsed_u.query, keep_blank_values=True):
            if SENSITIVE_KEY_PATTERN.search(k):
                url_params[k] = "[REDACTED]"
            else:
                url_params[k] = sanitize_text_secrets(v, extra_secrets) or ""

        distinguishing: List[str] = []
        for h in (_get(obs, "headings", []) or [])[:5]:
            distinguishing.append(f"heading:{h}")
        if _get(obs, "has_validation_error", False):
            distinguishing.append(f"validation_error:{_get(obs, 'validation_error_message', 'present')}")
        if _get(obs, "menu_open", False):
            distinguishing.append("menu_open:true")
        dyn = _get(obs, "dynamic_signals", {}) or {}
        if dyn.get("selected_option"):
            distinguishing.append(f"selected_option:{dyn['selected_option']}")
        if dyn.get("counter_value") is not None:
            distinguishing.append(f"counter_value:{dyn['counter_value']}")

        in_page = {
            "menu_open": bool(_get(obs, "menu_open", False)),
            "has_validation_error": bool(_get(obs, "has_validation_error", False)),
            "validation_error_message": sanitize_text_secrets(_get(obs, "validation_error_message", None), extra_secrets),
            "validation_error_locator": _get(obs, "validation_error_locator", None),
            "selected_option": dyn.get("selected_option"),
            "counter_value": dyn.get("counter_value"),
        }

        is_login_route = "login" in norm_route.lower() or (auth_login_url and norm_route == redact_url_params(auth_login_url)) or st_id.startswith("st_root_view")
        requires_auth = bool(_get(obs, "is_authenticated", False) or (final_state.get("auth_verified") and not is_login_route))
        preconditions: List[str] = []
        if requires_auth:
            preconditions.append("Requires authenticated session")
        if url_params:
            preconditions.append(f"Query parameters: {url_params}")

        controls_list: List[ContractControlDescriptor] = []
        for c in (_get(obs, "interactive_controls", []) or [])[:30]:
            controls_list.append(
                ContractControlDescriptor(
                    element_id=_get(c, "element_id", ""),
                    role=_get(c, "role", ""),
                    accessible_name=sanitize_text_secrets(_get(c, "accessible_name", ""), extra_secrets) or "",
                    tag_name=_get(c, "tag_name", ""),
                    selector=_get(c, "selector", ""),
                    element_type=_get(c, "element_type", None),
                    is_enabled=bool(_get(c, "is_enabled", True)),
                    is_visible=bool(_get(c, "is_visible", True)),
                    declared_constraints=_deep_sanitize_obj(dict(_get(c, "declared_constraints", {}) or {}), extra_secrets),
                )
            )

        forms_list: List[ContractFormDescriptor] = []
        for f in (_get(obs, "forms", []) or []):
            sanitized_fields = []
            for fld in (_get(f, "fields", []) or []):
                f_copy = {
                    k: _deep_sanitize_obj(v, extra_secrets) for k, v in (fld.items() if isinstance(fld, dict) else fld.__dict__.items())
                    if not SENSITIVE_KEY_PATTERN.search(k) and k not in ("value", "default_value")
                }
                sanitized_fields.append(f_copy)
            forms_list.append(
                ContractFormDescriptor(
                    form_id=str(_get(f, "form_id") or "form"),
                    form_selector=str(_get(f, "form_selector") or "form"),
                    action=redact_url_params(str(_get(f, "action") or "")),
                    method=str(_get(f, "method") or "GET").upper(),
                    submit_control_selector=_get(f, "submit_control_selector"),
                    fields=sanitized_fields,
                )
            )

        headings = _deep_sanitize_obj(list(_get(obs, "headings", []) or []), extra_secrets)
        desc = sanitize_text_secrets(f"{_get(obs, 'page_title', 'Page')} ({headings[0] if headings else norm_route})", extra_secrets) or ""

        contract_states.append(
            ContractStateRecord(
                state_id=st_id,
                state_class_id=_get(obs, "state_class_id", "") or st_id,
                url=clean_url,
                normalized_route=norm_route,
                page_title=sanitize_text_secrets(_get(obs, "page_title", ""), extra_secrets) or "",
                description=desc,
                state_signature=f"{_get(obs, 'state_class_id', st_id)}::{st_id}",
                distinguishing_observations=_deep_sanitize_obj(distinguishing, extra_secrets),
                requires_authentication=requires_auth,
                known_preconditions=_deep_sanitize_obj(preconditions, extra_secrets),
                url_parameters=url_params,
                in_page_state=_deep_sanitize_obj(in_page, extra_secrets),
                headings=headings,
                interactive_controls=controls_list,
                forms=forms_list,
            )
        )

    # 3. Build Verified Transitions
    contract_transitions: List[ContractTransitionRecord] = []
    known_state_ids = {s.state_id for s in contract_states}

    for tr in transitions_list:
        src_id = _get(tr, "source_state_id", "initial")
        dst_id = _get(tr, "destination_state_id", None)
        if src_id not in known_state_ids and contract_states:
            src_id = contract_states[0].state_id
        if dst_id and dst_id not in known_state_ids:
            dst_id = None

        valid_ev_ids = [eid for eid in (_get(tr, "evidence_ids", []) or []) if eid in seen_ev_ids]
        if not valid_ev_ids and contract_evidence:
            # Match by state reference if available
            matched_ev = next(
                (e.evidence_id for e in contract_evidence if e.after_state_id == dst_id or e.associated_state_id == src_id),
                contract_evidence[0].evidence_id,
            )
            valid_ev_ids = [matched_ev]

        target_desc = sanitize_text_secrets(_get(tr, "target_element_desc", ""), extra_secrets) or ""
        # Match control metadata from source state if available
        matched_ctrl_role = None
        matched_ctrl_name = None
        src_state_rec = next((s for s in contract_states if s.state_id == src_id), None)
        if src_state_rec:
            for c in src_state_rec.interactive_controls:
                if c.selector == target_desc or c.element_id == target_desc:
                    matched_ctrl_role = c.role
                    matched_ctrl_name = c.accessible_name
                    break

        postconditions: List[str] = []
        dst_state_rec = next((s for s in contract_states if s.state_id == dst_id), None)
        if dst_state_rec:
            postconditions.append(f"Reached state '{dst_state_rec.state_id}' at '{dst_state_rec.url}'")
            if dst_state_rec.in_page_state.get("has_validation_error"):
                postconditions.append(f"Validation error displayed: {dst_state_rec.in_page_state.get('validation_error_message')}")
            if dst_state_rec.in_page_state.get("menu_open"):
                postconditions.append("In-page drawer/menu opened")
            if dst_state_rec.in_page_state.get("selected_option"):
                postconditions.append(f"Option '{dst_state_rec.in_page_state.get('selected_option')}' active")
            if dst_state_rec.headings:
                postconditions.append(f"Heading '{dst_state_rec.headings[0]}' visible")

        exec_status = _get(tr, "execution_status", "EXECUTED")
        if exec_status == "SAFETY_BLOCKED" or _get(tr, "status", "") == "safety_blocked":
            ver_outcome = "BLOCKED"
            repeatability = "BLOCKED"
        elif _get(tr, "transition_verified", False):
            ver_outcome = "VERIFIED"
            repeatability = "REPEATABLE"
        else:
            ver_outcome = "UNCERTAIN"
            repeatability = "UNCERTAIN"

        preconds = list(_get(tr, "preconditions", []) or [])
        if src_state_rec and src_state_rec.requires_authentication and "Requires authenticated session" not in preconds:
            preconds.append("Requires authenticated session")
        if src_state_rec and _is_positive_counter(src_state_rec.in_page_state.get("counter_value")):
            cart_pre = f"Cart/counter badge value = {src_state_rec.in_page_state['counter_value']}"
            if cart_pre not in preconds:
                preconds.append(cart_pre)

        contract_transitions.append(
            ContractTransitionRecord(
                transition_id=_get(tr, "transition_id", f"trans-{len(contract_transitions)+1}"),
                source_state_id=src_id,
                destination_state_id=dst_id,
                action_type=_get(tr, "action_type", "CLICK_CONTROL"),
                grounded_control=ContractGroundedControl(
                    selector=target_desc if target_desc.startswith(("#", ".", "[")) or "/" in target_desc else None,
                    role=matched_ctrl_role,
                    accessible_name=matched_ctrl_name,
                    target_description=target_desc,
                ),
                required_preconditions=preconds,
                observed_postconditions=postconditions,
                actual_effect=_get(tr, "observed_effect", "ROUTE_TRANSITION"),
                verification_outcome=ver_outcome,
                repeatability=repeatability,
                evidence_ids=valid_ev_ids,
                confidence=float(_get(tr, "confidence", 1.0)),
            )
        )

    # 4. Build Defect Findings
    contract_defects: List[ContractDefectFinding] = []
    raw_leads = final_state.get("bug_leads") or []
    if report:
        confirmed_from_rep = _get(report, "confirmed_bug_leads", []) or []
        unstable_from_rep = _get(report, "unstable_or_rejected_leads", []) or []
        if confirmed_from_rep or unstable_from_rep:
            raw_leads = list(confirmed_from_rep) + list(unstable_from_rep)

    seen_lead_ids: Set[str] = set()
    for lead in raw_leads:
        lid = _get(lead, "id", _get(lead, "lead_id", f"lead_{len(contract_defects)+1}"))
        if lid in seen_lead_ids:
            continue
        seen_lead_ids.add(lid)

        status_str = _get(lead, "status", "LEAD")
        if status_str == "REJECTED":
            continue

        is_confirmed = (status_str == "CONFIRMED")
        classification = "CONFIRMED_DEFECT" if is_confirmed else "UNVERIFIED_SUSPICION"

        valid_ev = [eid for eid in (_get(lead, "evidence_ids", []) or []) if eid in seen_ev_ids]
        if not valid_ev and contract_evidence:
            valid_ev = [contract_evidence[-1].evidence_id]

        exp_dict = _get(lead, "expected", {}) or {}
        act_dict = _get(lead, "actual", {}) or {}
        exp_text = sanitize_text_secrets(exp_dict.get("text") if isinstance(exp_dict, dict) else str(exp_dict), extra_secrets)
        act_text = sanitize_text_secrets(act_dict.get("text") if isinstance(act_dict, dict) else str(act_dict), extra_secrets)

        sanitized_steps = []
        for s in (_get(lead, "steps", []) or []):
            s_copy = dict(s) if isinstance(s, dict) else dict(s.__dict__)
            if "resolved_value" in s_copy:
                s_copy["resolved_value"] = sanitize_text_secrets(s_copy["resolved_value"], extra_secrets)
            sanitized_steps.append(s_copy)

        raw_orc = _get(lead, "oracle_type", "TECHNICAL")
        orc_type = getattr(raw_orc, "value", str(raw_orc))
        contract_defects.append(
            ContractDefectFinding(
                finding_id=lid,
                title=sanitize_text_secrets(_get(lead, "title", "Defect Finding"), extra_secrets) or "Defect Finding",
                classification=classification,
                oracle_type=orc_type,
                severity=_get(lead, "severity_guess", "MEDIUM"),
                state_class=_get(lead, "state_class", ""),
                target_control=_get(lead, "target_desc", None),
                reproduction_verified=is_confirmed,
                reproduction_steps=sanitized_steps if is_confirmed else [],
                expected_behavior=exp_text if exp_text else None,
                actual_behavior=act_text if act_text else None,
                evidence_ids=valid_ev,
                confidence=1.0 if is_confirmed else 0.5,
                limitations=None if is_confirmed else "Requires isolated 2-of-2 reproduction confirmation",
            )
        )

    # 5. Build Completeness Summary
    pending_candidates: List[ContractPendingCandidate] = []
    unresolved_breakdown: Dict[str, int] = {}
    safety_blocked_list: List[ContractSafetyBlockedAction] = []

    stop_reason = final_state.get("stopping_reason") or (_get(_get(report, "overview"), "stopping_reason", "Completed") if report else "Completed")

    questions_map = _get(agenda, "questions", None) if agenda else None
    if questions_map and isinstance(questions_map, dict):
        for q in questions_map.values():
            q_status = _get(q, "status", "")
            if q_status == "unexplored":
                reason_cat = (
                    "BUDGET_CEILING_REACHED"
                    if any(w in stop_reason.lower() for w in ("ceiling", "limit", "budget", "time"))
                    else "UNEXPLORED_FRONTIER"
                )
                unresolved_breakdown[reason_cat] = unresolved_breakdown.get(reason_cat, 0) + 1
                q_id = _get(q, "id", "q_pending")
                pending_candidates.append(
                    ContractPendingCandidate(
                        candidate_id=q_id,
                        candidate_type=_get(q, "probe_family", "controls"),
                        target=redact_url_params(_get(q, "target_element", None) or q_id),
                        parent_state_id=_get(q, "target_state_class", None),
                        unresolved_reason=reason_cat,
                    )
                )
            elif q_status == "abandoned":
                unresolved_breakdown["SAFETY_POLICY_BLOCKED"] = unresolved_breakdown.get("SAFETY_POLICY_BLOCKED", 0) + 1

    for ev in contract_evidence:
        if ev.evidence_type == "SAFETY_DECISION":
            safety_blocked_list.append(
                ContractSafetyBlockedAction(
                    action_type="SAFETY_GATED_STEP",
                    target=ev.locator or "high_impact_control",
                    source_state_id=ev.associated_state_id,
                    reason=ev.observed_result,
                    evidence_id=ev.evidence_id,
                )
            )

    auth_summary = {
        "boundary_observed": bool(final_state.get("auth_boundary_detected", False)),
        "login_url": redact_url_params(auth_login_url or ""),
        "authentication_attempted": bool(final_state.get("auth_attempted", False)),
        "authentication_verified": bool(final_state.get("auth_verified", False)),
        "protected_routes_unlocked": [
            redact_url_params(r) for r in (final_state.get("protected_features_unlocked") or [])
        ],
    }

    app_model = final_state.get("application_model")
    uncertainty = float(getattr(app_model, "investigation_uncertainty", 0.0)) if app_model else 0.0
    comp_status = getattr(report.overview.status, "value", str(report.overview.status)) if (report and getattr(report, "overview", None)) else "completed"

    completeness = ContractCompletenessSummary(
        discovered_states_count=len(contract_states),
        verified_transitions_count=len(contract_transitions),
        pending_frontier_candidates=pending_candidates,
        unresolved_reasons_breakdown=unresolved_breakdown,
        safety_blocked_actions=safety_blocked_list,
        authentication_boundaries=auth_summary,
        investigation_uncertainty=uncertainty,
        stopping_reason=stop_reason,
        completion_status=comp_status,
    )

    journeys = _deep_sanitize_obj(getattr(report, "inferred_journeys", []) if report else [], extra_secrets)

    return InvestigationHandoffContract(
        schema_version=CONTRACT_SCHEMA_VERSION,
        investigation_id=inv_id,
        application_origin=app_origin,
        target_url=target_url,
        investigated_routes=investigated_routes,
        states=contract_states,
        verified_transitions=contract_transitions,
        evidence_ledger=contract_evidence,
        defect_findings=contract_defects,
        completeness=completeness,
        inferred_journeys=journeys,
    )
