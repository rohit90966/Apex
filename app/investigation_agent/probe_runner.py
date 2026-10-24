"""
Probe Runner and Execution Engine for Stateful Investigation.
Runs multi-step probe scripts with:
- Deterministic synthetic data generator (never uses real credentials).
- ValueSpec resolving (literal, unique_email, valid_password, length edge, mismatch).
- Per-step safety gating via SafetyGovernor.
- Hard 3 s per-step timeout and 10-step cap.
- Comprehensive observation diff computation (routes, classes, alerts, text, network, errors).
"""

import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    NetworkEvent,
    ConsoleOrPageError,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    ProbeStep,
    ProbeScript,
    StateDiff,
    ProbeResult,
    ActionFailureReason,
)
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.governor import SafetyGovernor

logger = logging.getLogger(__name__)


def generate_synthetic_value(
    spec: Optional[ValueSpec],
    run_id: str = "run01",
    field_name: str = "",
    field_meta: Optional[Dict[str, Any]] = None,
    context_values: Optional[Dict[str, str]] = None,
) -> str:
    """
    Deterministic synthetic data generator.
    Generates realistic, benign input data guaranteed never to look like real credentials or personal data.
    """
    if spec is None:
        return ""

    context = context_values or {}
    meta = field_meta or {}

    if spec.kind == ValueSpecType.LITERAL:
        return spec.value or ""

    elif spec.kind == ValueSpecType.UNIQUE_EMAIL:
        return f"qa+{run_id}@example.test"

    elif spec.kind == ValueSpecType.VALID_PASSWORD:
        return f"Vqa!{run_id}9x"

    elif spec.kind == ValueSpecType.BLANK:
        return ""

    elif spec.kind == ValueSpecType.TEXT_OF_LENGTH:
        n = spec.length if spec.length is not None else 10
        # Deterministic alpha-numeric repeating pattern
        base = "Abc123XyZ_"
        return (base * ((n // len(base)) + 1))[:n]

    elif spec.kind == ValueSpecType.MISMATCH_OF:
        ref_field = spec.ref_field or "password"
        ref_val = context.get(ref_field)
        if ref_val:
            return ref_val + "_diff9x"
        return f"Mismatch!{run_id}Z"

    elif spec.kind == ValueSpecType.CONSTRAINT_EDGE:
        # Calculate target edge length from field metadata or spec
        target_len = 5
        if spec.constraint_type == "minlength" or "minlength" in meta:
            base_len = int(meta.get("minlength") or 5)
            target_len = max(0, base_len + spec.edge_offset)
        elif spec.constraint_type == "maxlength" or "maxlength" in meta:
            base_len = int(meta.get("maxlength") or 20)
            target_len = max(0, base_len + spec.edge_offset)
        elif spec.length is not None:
            target_len = max(0, spec.length + spec.edge_offset)

        return "A" * target_len

    return spec.value or ""


def compute_diff(
    before: UIStateObservation,
    after: UIStateObservation,
    network_events: List[NetworkEvent],
    console_errors: List[ConsoleOrPageError],
    mutations: List[Dict[str, Any]],
) -> StateDiff:
    """
    Computes a comprehensive structural and behavioral diff between before and after observations.
    """
    route_changed = before.normalized_route != after.normalized_route
    class_changed = before.state_class_id != after.state_class_id
    state_id_changed = before.state_id != after.state_id

    # Text added/removed (word-level set diff)
    before_words = set(before.visible_text_snippet.split())
    after_words = set(after.visible_text_snippet.split())
    text_added = sorted(list(after_words - before_words))[:20]
    text_removed = sorted(list(before_words - after_words))[:20]

    # Alerts added
    before_alerts = set(before.dynamic_signals.get("alerts", []))
    if before.validation_error_message:
        before_alerts.add(before.validation_error_message)
    after_alerts = set(after.dynamic_signals.get("alerts", []))
    if after.validation_error_message:
        after_alerts.add(after.validation_error_message)
    alerts_added = sorted(list(after_alerts - before_alerts))

    # Invalid fields delta
    before_invalids = set(before.dynamic_signals.get("aria_invalid_fields", []))
    after_invalids = set(after.dynamic_signals.get("aria_invalid_fields", []))
    invalid_delta = {
        "newly_invalid": sorted(list(after_invalids - before_invalids)),
        "cleared_invalid": sorted(list(before_invalids - after_invalids)),
    }

    # Counter delta
    b_count_str = str(before.dynamic_signals.get("counter_value", "0"))
    a_count_str = str(after.dynamic_signals.get("counter_value", "0"))
    counter_delta = None
    if b_count_str.isdigit() and a_count_str.isdigit():
        counter_delta = int(a_count_str) - int(b_count_str)

    # Controls added/removed
    before_controls = {c.selector for c in before.interactive_controls if c.selector}
    after_controls = {c.selector for c in after.interactive_controls if c.selector}
    controls_added = sorted(list(after_controls - before_controls))[:15]
    controls_removed = sorted(list(before_controls - after_controls))[:15]

    # Auth delta (storage & cookies)
    b_cookies = set(before.storage_keys.get("cookies", []))
    a_cookies = set(after.storage_keys.get("cookies", []))
    b_storage = set(before.storage_keys.get("local_storage", []))
    a_storage = set(after.storage_keys.get("local_storage", []))
    auth_delta = {
        "cookies_added": sorted(list(a_cookies - b_cookies)),
        "cookies_removed": sorted(list(b_cookies - a_cookies)),
        "storage_added": sorted(list(a_storage - b_storage)),
        "storage_removed": sorted(list(b_storage - a_storage)),
    }

    # Network summary
    req_count = len(network_events)
    c4xx = sum(1 for e in network_events if e.status and 400 <= e.status < 500)
    c5xx = sum(1 for e in network_events if e.status and 500 <= e.status < 600)
    endpoints = sorted(list({e.url.split("?")[0] for e in network_events}))[:10]
    network_summary = {
        "request_count": req_count,
        "status_4xx_count": c4xx,
        "status_5xx_count": c5xx,
        "endpoints": endpoints,
    }

    # Text anomalies added
    before_anomalies = set(before.text_anomalies)
    after_anomalies = set(after.text_anomalies)
    text_anomalies_added = sorted(list(after_anomalies - before_anomalies))

    return StateDiff(
        route_changed=route_changed,
        class_changed=class_changed,
        state_id_changed=state_id_changed,
        text_added=text_added,
        text_removed=text_removed,
        alerts_added=alerts_added,
        invalid_fields_delta=invalid_delta,
        counter_delta=counter_delta,
        controls_added=controls_added,
        controls_removed=controls_removed,
        auth_delta=auth_delta,
        network_summary=network_summary,
        console_errors_count=len(console_errors),
        text_anomalies_added=text_anomalies_added,
    )


class ProbeRunner:
    """
    Executes a structured ProbeScript step-by-step with:
    - Pre-step safety checks via SafetyGovernor
    - 3.0s per-step execution timeout
    - Step cap of 10 steps
    - Pre- and post-execution observation and StateDiff calculation
    """

    def __init__(self, governor: Optional[SafetyGovernor] = None):
        self.governor = governor or SafetyGovernor()

    def run_probe(
        self,
        session: StatefulBrowserSession,
        script: ProbeScript,
        run_id: str = "run01"
    ) -> ProbeResult:
        t0 = time.time()
        p = session.page
        current_url = p.url or ""

        # Pre-probe telemetry marks
        net_mark = session.mark()
        err_mark = session.mark_errors()
        session.drain_mutations()

        # Capture initial observation
        before_obs = session.capture_observation("probe_before")

        steps_executed = 0
        context_values: Dict[str, str] = {}
        error_message: Optional[str] = None
        status = "SUCCESS"

        # Cap execution at 10 steps
        steps_to_run = script.steps[:10]

        for step in steps_to_run:
            target_obj = step.target if step.target is not None else (step.raw_target or "")
            target_str = step.raw_target or (
                step.target.css or step.target.name or "target" if isinstance(step.target, SemanticTarget) else str(target_obj)
            )

            # 1. Safety Gate
            allowed, reason = self.governor.can_execute(
                action_type=step.action_type,
                target=target_str,
                current_url=session.page.url or current_url,
                rationale=script.description,
            )
            if not allowed:
                status = "SAFETY_BLOCKED"
                error_message = reason
                logger.warning("[ProbeRunner] Step blocked by safety governor: %s", reason)
                break

            # 2. Synthetic Value Resolution
            if step.value_spec is not None:
                resolved_value = generate_synthetic_value(
                    spec=step.value_spec,
                    run_id=run_id,
                    context_values=context_values,
                )
                step.resolved_value = resolved_value
            else:
                resolved_value = step.resolved_value or ""
            if step.raw_target and resolved_value:
                context_values[step.raw_target] = resolved_value
            if isinstance(step.target, SemanticTarget) and step.target.name and resolved_value:
                context_values[step.target.name] = resolved_value

            # 3. Action Execution (3s timeout) with Precondition Enforcement
            req_url = getattr(step, "required_url", None)
            req_state = getattr(step, "required_state_id", None)
            ok, outcome, fail_reason, telemetry = InvestigationActionExecutor.execute_action(
                session=session,
                action_type=step.action_type,
                target=target_obj,
                value=resolved_value,
                key=step.key_to_press,
                timeout_ms=3000,
                required_url=req_url,
                required_state_id=req_state,
            )
            steps_executed += 1

            if not ok:
                status = "FAILED"
                error_message = f"Step failed ({step.action_type} on {target_str}): {outcome}"
                logger.info("[ProbeRunner] Step failed (reason=%s): %s", fail_reason, error_message)
                # Stop on first hard error
                break

        # Post-probe settle and telemetry capture
        session.settle()
        after_obs = session.capture_observation("probe_after")
        network_events = session.events_since(net_mark)
        console_errors = session.errors_since(err_mark)
        mutations = session.drain_mutations()

        diff = compute_diff(
            before=before_obs,
            after=after_obs,
            network_events=network_events,
            console_errors=console_errors,
            mutations=mutations,
        )

        duration = round(time.time() - t0, 3)

        return ProbeResult(
            script_id=script.script_id,
            status=status,
            steps_executed=steps_executed,
            before_obs=before_obs,
            after_obs=after_obs,
            diff=diff,
            network_events=network_events,
            console_errors=console_errors,
            mutations=mutations,
            error_message=error_message,
            duration_seconds=duration,
            script=script,
        )
