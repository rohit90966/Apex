"""
Comparator and Form Submission Classification for Stateful Investigation.
Compares a Probe's Prediction against observed StateDiff and provides
generic, documented form submission outcome classification:
- ACCEPTED: Submission accepted by application without validation errors.
- REJECTED: Submission rejected due to visible alerts, aria-invalid, or 4xx responses.
- UNCERTAIN: Ambiguous state transition (never produces a bug lead).
"""

from typing import List, Dict, Any, Optional
from app.investigation_agent.models import (
    Prediction,
    OutcomeComparison,
    ComparatorVerdict,
    FormSubmitOutcome,
    StateDiff,
    UIStateObservation,
    NetworkEvent,
)


def evaluate_signal_on_diff(
    signal: str,
    diff: StateDiff,
    before: UIStateObservation,
    after: UIStateObservation,
    network_events: List[NetworkEvent]
) -> bool:
    """Evaluates whether an individual signal token holds on the observed diff."""
    sig = signal.strip()

    if sig == "alert_present":
        return len(diff.alerts_added) > 0 or after.has_validation_error

    if sig.startswith("alert_text_contains:"):
        target_text = sig.split(":", 1)[1].lower()
        all_alerts = diff.alerts_added + ([after.validation_error_message] if after.validation_error_message else [])
        return any(target_text in a.lower() for a in all_alerts if a)

    if sig == "no_route_change":
        return not diff.route_changed

    if sig == "route_change":
        return diff.route_changed

    if sig == "class_change":
        return diff.class_changed

    if sig == "auth_cookie_set":
        return len(diff.auth_delta.get("cookies_added", [])) > 0

    if sig == "auth_cookie_absent":
        return len(diff.auth_delta.get("cookies_added", [])) == 0

    if sig.startswith("field_invalid:"):
        f_name = sig.split(":", 1)[1]
        newly_invalid = diff.invalid_fields_delta.get("newly_invalid", [])
        return any(f_name in fld for fld in newly_invalid)

    if sig == "counter_changed":
        return diff.counter_delta is not None and diff.counter_delta != 0

    if sig == "list_changed":
        return len(diff.controls_added) > 0 or len(diff.controls_removed) > 0 or len(diff.text_added) > 3

    if sig == "list_unchanged":
        return len(diff.controls_added) == 0 and len(diff.controls_removed) == 0 and len(diff.text_added) == 0

    if sig.startswith("text_added:"):
        target_text = sig.split(":", 1)[1].lower()
        return any(target_text in word.lower() for word in diff.text_added) or target_text in after.visible_text_snippet.lower()

    if sig == "no_visible_change":
        return (not diff.route_changed and not diff.class_changed and not diff.state_id_changed and
                len(diff.text_added) == 0 and len(diff.alerts_added) == 0 and
                len(diff.controls_added) == 0 and len(diff.controls_removed) == 0 and
                (diff.counter_delta is None or diff.counter_delta == 0))

    if sig == "status_2xx":
        return any(e.status and 200 <= e.status < 300 for e in network_events)

    if sig == "status_4xx":
        return any(e.status and 400 <= e.status < 500 for e in network_events)

    if sig == "no_server_error":
        return diff.network_summary.get("status_5xx_count", 0) == 0 and diff.console_errors_count == 0

    return False


def compare_prediction_with_diff(
    prediction: Prediction,
    diff: StateDiff,
    before: UIStateObservation,
    after: UIStateObservation,
    network_events: List[NetworkEvent]
) -> OutcomeComparison:
    """
    Compares expected prediction signals against observed diff.
    Assigns verdict: MATCHED, PARTIAL, CONTRADICTED, NO_EFFECT, or UNEXPECTED.
    """
    matched: List[str] = []
    missing: List[str] = []

    for sig in prediction.signals:
        if evaluate_signal_on_diff(sig, diff, before, after, network_events):
            matched.append(sig)
        else:
            missing.append(sig)

    # Classify observed effect and verified transition status independently from prediction signals
    if diff.route_changed:
        observed_effect = "ROUTE_TRANSITION"
        transition_verified = True
    elif len(diff.alerts_added) > 0 or (after.has_validation_error and not before.has_validation_error) or len(diff.invalid_fields_delta.get("newly_invalid", [])) > 0:
        observed_effect = "VALIDATION_FEEDBACK"
        transition_verified = True
    elif diff.is_significant or len(diff.text_added) > 0 or len(diff.text_removed) > 0:
        observed_effect = "IN_PAGE_STATE_MUTATION"
        transition_verified = bool(diff.is_significant)
    else:
        observed_effect = "NO_OBSERVABLE_CHANGE"
        transition_verified = False

    # Check for unexpected technical crashes or server errors
    if diff.network_summary.get("status_5xx_count", 0) > 0 or diff.console_errors_count > 0:
        return OutcomeComparison(
            verdict=ComparatorVerdict.UNEXPECTED,
            execution_status="EXECUTED",
            observed_effect=observed_effect,
            transition_verified=transition_verified,
            matched_signals=matched,
            missing_signals=missing,
            extra_signals=["server_or_console_error"],
            summary=f"Encountered unexpected error during probe: {diff.console_errors_count} console errors, {diff.network_summary.get('status_5xx_count', 0)} 5xx errors.",
        )

    # Check for complete lack of effect when the prediction did not expect no_visible_change
    has_meaningful_network = any(
        e.method in ("POST", "PUT", "PATCH", "DELETE") and e.status and 200 <= e.status < 400
        for e in network_events
    )
    if (
        evaluate_signal_on_diff("no_visible_change", diff, before, after, network_events)
        and not has_meaningful_network
        and "no_visible_change" not in prediction.signals
    ):
        return OutcomeComparison(
            verdict=ComparatorVerdict.NO_EFFECT,
            execution_status="EXECUTED",
            observed_effect="NO_OBSERVABLE_CHANGE",
            transition_verified=False,
            matched_signals=matched,
            missing_signals=missing,
            summary="Action had no observable effect on DOM or application state.",
        )

    # Contradiction check: key negative vs positive expectations
    contradicted = False
    if "no_route_change" in prediction.signals and diff.route_changed:
        contradicted = True
    elif "route_change" in prediction.signals and not diff.route_changed:
        contradicted = True
    elif "status_4xx" in prediction.signals and any(e.status and 200 <= e.status < 300 for e in network_events):
        contradicted = True
    elif "alert_present" in prediction.signals and len(diff.alerts_added) == 0 and not after.has_validation_error:
        contradicted = True

    if contradicted and len(missing) > 0:
        verdict = ComparatorVerdict.CONTRADICTED
    elif len(missing) == 0:
        verdict = ComparatorVerdict.MATCHED
    elif len(matched) > 0:
        verdict = ComparatorVerdict.PARTIAL
    else:
        verdict = ComparatorVerdict.CONTRADICTED

    summary = f"Verdict {verdict.value} ({observed_effect}): {len(matched)}/{len(prediction.signals)} signals matched."
    return OutcomeComparison(
        verdict=verdict,
        execution_status="EXECUTED",
        observed_effect=observed_effect,
        transition_verified=transition_verified,
        matched_signals=matched,
        missing_signals=missing,
        extra_signals=[],
        summary=summary,
    )


def classify_form_submission(
    diff: StateDiff,
    before: UIStateObservation,
    after: UIStateObservation,
    network_events: List[NetworkEvent]
) -> FormSubmitOutcome:
    """
    Generic, documented heuristic to classify form submission responses:
    - ACCEPTED:
        No alert or aria-invalid AND (route/class changed OR main content replaced OR auth delta appeared OR 2xx/3xx POST succeeded)
    - REJECTED:
        Alert appeared OR aria-invalid set OR status 4xx response OR no visible change on POST
    - UNCERTAIN:
        Ambiguous outcome (strictly never converted into a bug lead)
    """
    has_alert = len(diff.alerts_added) > 0 or after.has_validation_error
    has_invalid_fields = len(diff.invalid_fields_delta.get("newly_invalid", [])) > 0
    has_4xx = any(e.status and 400 <= e.status < 500 for e in network_events)
    has_5xx = any(e.status and 500 <= e.status < 600 for e in network_events)

    if has_5xx:
        return FormSubmitOutcome.REJECTED

    if has_alert or has_invalid_fields or has_4xx:
        return FormSubmitOutcome.REJECTED

    # Check positive progress indicators
    has_auth_delta = len(diff.auth_delta.get("cookies_added", [])) > 0 or len(diff.auth_delta.get("storage_added", [])) > 0
    has_structural_change = diff.route_changed or diff.class_changed
    has_text_advance = len(diff.text_added) >= 3

    post_success = any(e.method == "POST" and e.status and 200 <= e.status < 400 for e in network_events)

    if not has_alert and not has_invalid_fields and not has_4xx:
        if has_structural_change or has_auth_delta or has_text_advance or post_success:
            return FormSubmitOutcome.ACCEPTED

    # If completely unchanged on POST submission
    if post_success and not has_structural_change and len(diff.text_added) == 0:
        return FormSubmitOutcome.REJECTED

    return FormSubmitOutcome.UNCERTAIN
