"""
Form Probe Generators for Stateful Investigation.
Generates deterministic probe scripts for forms:
- happy: clean valid submission
- required_blank: blanking declared required fields and undeclared forms
- constraint_bypass: novalidate client bypass to test server-side minlength / checkbox enforcement
- mismatch: password confirmation mismatching
- boundary: min/max length edges
"""

import re
from typing import List, Dict, Any, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    Prediction,
)


def _resolve_field_value_spec(f: Dict[str, Any], is_blank: bool = False) -> ValueSpec:
    if is_blank:
        return ValueSpec(kind=ValueSpecType.BLANK)

    f_type = (f.get("type") or "").lower()
    f_name = (f.get("name") or "").lower()
    f_label = (f.get("label") or "").lower()

    if "email" in f_type or "email" in f_name or "email" in f_label:
        return ValueSpec(kind=ValueSpecType.UNIQUE_EMAIL)
    elif "password" in f_type or "password" in f_name or "password" in f_label:
        return ValueSpec(kind=ValueSpecType.VALID_PASSWORD)
    elif f.get("minlength"):
        return ValueSpec(kind=ValueSpecType.TEXT_OF_LENGTH, length=max(f["minlength"], 10))
    elif f_type in ("number", "tel") or any(k in (f_name + " " + f_label) for k in ("zip", "postal", "phone", "pin")):
        return ValueSpec(kind=ValueSpecType.LITERAL, value="94103")
    else:
        return ValueSpec(kind=ValueSpecType.TEXT_OF_LENGTH, length=8)


def generate_form_probes(obs: UIStateObservation) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """Generates all applicable form probing scripts for forms discovered on current page."""
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []

    for f_idx, form in enumerate(obs.forms):
        form_id = form.get("form_id") or f"form_{f_idx}"
        form_sel = form.get("form_selector") or (f"#{form['form_id']}" if form.get("form_id") and not form.get("form_id", "").startswith("form_") else "form")
        raw_fields = form.get("fields", [])
        # Strict Action Compatibility: Exclude non-editable elements (buttons, submits, resets, hidden) from form input fields
        fields = [f for f in raw_fields if (f.get("type") or "").lower() not in ("submit", "button", "reset", "image", "hidden")]
        if not fields:
            continue

        # ── 1. Happy Path Probe ──────────────────────────────────────────────
        happy_steps = []
        for f in fields:
            f_sel = f.get("stable_selector") or (f"#{f['id']}" if f.get("id") else f"[name='{f.get('name')}']")
            target = SemanticTarget(label=f.get("label"), css=f_sel, name=f.get("name"))
            f_type = (f.get("type") or "").lower()

            if f_type in ("checkbox", "radio"):
                happy_steps.append(ProbeStep(action_type="CHECK", target=target))
            elif f_type == "select":
                opts = f.get("select_options", [])
                opt_val = opts[0] if opts else "1"
                happy_steps.append(ProbeStep(action_type="SELECT_OPTION", target=target, value_spec=ValueSpec(kind=ValueSpecType.LITERAL, value=opt_val)))
            else:
                v_spec = _resolve_field_value_spec(f)
                happy_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=v_spec))

        happy_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
        happy_script = ProbeScript(
            name=f"form_happy_{form_id}",
            family="forms",
            description=f"Happy path submission of form {form_id}",
            steps=happy_steps,
        )
        happy_pred = Prediction(
            expect="Form accepted without errors",
            signals=["no_server_error", "status_2xx"]
        )
        probes.append((happy_script, happy_pred, {"kind": "happy", "form_id": form_id}))

        # ── 2. Constraint Bypass: Minlength Bypass via SET_NOVALIDATE ─────────
        for f in fields:
            if f.get("minlength") and f.get("minlength") > 1:
                min_len = f["minlength"]
                f_name = f.get("name") or f.get("id") or "field"
                f_sel = f.get("stable_selector") or (f"#{f['id']}" if f.get("id") else f"[name='{f.get('name')}']")

                bypass_steps = [ProbeStep(action_type="SET_NOVALIDATE", raw_target=form_sel)]
                for other_f in fields:
                    other_sel = other_f.get("stable_selector") or (f"#{other_f['id']}" if other_f.get("id") else f"[name='{other_f.get('name')}']")
                    target = SemanticTarget(label=other_f.get("label"), css=other_sel, name=other_f.get("name"))
                    if other_f.get("name") == f.get("name"):
                        # Short string violating minlength
                        bypass_steps.append(ProbeStep(
                            action_type="FILL",
                            target=target,
                            value_spec=ValueSpec(kind=ValueSpecType.CONSTRAINT_EDGE, constraint_type="minlength", edge_offset=-min_len + 1, length=1)
                        ))
                    else:
                        if (other_f.get("type") or "").lower() == "checkbox":
                            bypass_steps.append(ProbeStep(action_type="CHECK", target=target))
                        else:
                            bypass_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=_resolve_field_value_spec(other_f)))

                bypass_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
                script = ProbeScript(
                    name=f"constraint_bypass_minlength_{form_id}_{f_name}",
                    family="forms",
                    description=f"Tests server-side minlength enforcement on {f_name} with client validation bypassed",
                    steps=bypass_steps,
                )
                pred = Prediction(
                    expect=f"Server rejects short value (< {min_len} chars) for {f_name}",
                    signals=["alert_present", "no_route_change"]
                )
                probes.append((script, pred, {
                    "kind": "constraint_bypass_minlength",
                    "field": f_name,
                    "rule": f"Minimum length requirement for '{f_name}' must be enforced server-side",
                    "expected_outcome": f"Reject submission with error when '{f_name}' length is < {min_len}"
                }))

        # ── 3. Constraint Bypass: Required Checkbox (e.g. Terms) ─────────────
        for f in fields:
            if (f.get("type") or "").lower() == "checkbox" and (f.get("required") or "term" in (f.get("name") or "").lower() or "agree" in (f.get("name") or "").lower()):
                f_name = f.get("name") or f.get("id") or "checkbox"
                f_sel = f.get("stable_selector") or (f"#{f['id']}" if f.get("id") else f"[name='{f.get('name')}']")

                chk_steps = [ProbeStep(action_type="SET_NOVALIDATE", raw_target=form_sel)]
                for other_f in fields:
                    other_sel = other_f.get("stable_selector") or (f"#{other_f['id']}" if other_f.get("id") else f"[name='{other_f.get('name')}']")
                    target = SemanticTarget(label=other_f.get("label"), css=other_sel, name=other_f.get("name"))
                    if other_f.get("name") == f.get("name"):
                        chk_steps.append(ProbeStep(action_type="UNCHECK", target=target))
                    else:
                        if (other_f.get("type") or "").lower() == "checkbox":
                            chk_steps.append(ProbeStep(action_type="CHECK", target=target))
                        else:
                            chk_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=_resolve_field_value_spec(other_f)))

                chk_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
                script = ProbeScript(
                    name=f"constraint_bypass_checkbox_{form_id}_{f_name}",
                    family="forms",
                    description=f"Tests server-side mandatory checkbox enforcement on {f_name} with client validation bypassed",
                    steps=chk_steps,
                )
                pred = Prediction(
                    expect=f"Server rejects submission when required checkbox '{f_name}' is unchecked",
                    signals=["alert_present", "no_route_change"]
                )
                probes.append((script, pred, {
                    "kind": "constraint_bypass_checkbox",
                    "field": f_name,
                    "rule": f"Required acceptance of '{f_name}' must be enforced server-side",
                    "expected_outcome": f"Reject registration/submission when '{f_name}' is not checked"
                }))

        # ── 4. Password Confirmation Mismatch ────────────────────────────────
        pwd_fields = [f for f in fields if "password" in (f.get("name") or "").lower() or (f.get("type") or "").lower() == "password"]
        if len(pwd_fields) >= 2:
            p1 = pwd_fields[0]
            p2 = pwd_fields[1]
            p1_name = p1.get("name") or "password"
            p2_name = p2.get("name") or "confirm_password"

            mismatch_steps = []
            for f in fields:
                f_sel = f.get("stable_selector") or (f"#{f['id']}" if f.get("id") else f"[name='{f.get('name')}']")
                target = SemanticTarget(label=f.get("label"), css=f_sel, name=f.get("name"))
                if f.get("name") == p1.get("name"):
                    mismatch_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=ValueSpec(kind=ValueSpecType.VALID_PASSWORD)))
                elif f.get("name") == p2.get("name"):
                    mismatch_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=ValueSpec(kind=ValueSpecType.MISMATCH_OF, ref_field=p1_name)))
                elif (f.get("type") or "").lower() == "checkbox":
                    mismatch_steps.append(ProbeStep(action_type="CHECK", target=target))
                else:
                    mismatch_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=_resolve_field_value_spec(f)))

            mismatch_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
            script = ProbeScript(
                name=f"mismatch_{form_id}_{p1_name}_{p2_name}",
                family="forms",
                description=f"Tests password confirmation mismatch between {p1_name} and {p2_name}",
                steps=mismatch_steps,
            )
            pred = Prediction(
                expect="Server rejects mismatched password confirmation",
                signals=["alert_present", "no_route_change"]
            )
            probes.append((script, pred, {
                "kind": "mismatch",
                "field": p2_name,
                "rule": f"Confirmation field '{p2_name}' must match '{p1_name}'",
                "expected_outcome": "Reject submission when passwords do not match"
            }))

        # ── 5. Required Blank / Undeclared Blank Probe ─────────────────────────
        # Test submitting form completely blank
        blank_steps = []
        for f in fields:
            f_sel = f.get("stable_selector") or (f"#{f['id']}" if f.get("id") else f"[name='{f.get('name')}']")
            target = SemanticTarget(label=f.get("label"), css=f_sel, name=f.get("name"))
            if (f.get("type") or "").lower() == "checkbox":
                blank_steps.append(ProbeStep(action_type="UNCHECK", target=target))
            else:
                blank_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=ValueSpec(kind=ValueSpecType.BLANK)))

        blank_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
        script = ProbeScript(
            name=f"required_blank_all_{form_id}",
            family="forms",
            description=f"Submits form {form_id} with entirely blank fields",
            steps=blank_steps,
        )
        pred = Prediction(
            expect="Form rejects completely blank submission",
            signals=["alert_present", "no_route_change"]
        )
        probes.append((script, pred, {
            "kind": "required_blank",
            "rule": "Empty submission should not be accepted without validation",
            "expected_outcome": "Display validation error or prevent empty submission"
        }))

        # ── 6. Boundary: Password Length Boundary (< 6 chars) ────────────────
        for f in fields:
            if (f.get("type") or "").lower() == "password" or "password" in (f.get("name") or "").lower():
                f_name = f.get("name") or f.get("id") or "password"
                min_len = f.get("minlength") or 6

                short_steps = [ProbeStep(action_type="SET_NOVALIDATE", raw_target=form_sel)]
                for other_f in fields:
                    other_sel = other_f.get("stable_selector") or (f"#{other_f['id']}" if other_f.get("id") else f"[name='{other_f.get('name')}']")
                    target = SemanticTarget(label=other_f.get("label"), css=other_sel, name=other_f.get("name"))
                    if other_f.get("name") == f.get("name"):
                        short_steps.append(ProbeStep(
                            action_type="FILL",
                            target=target,
                            value_spec=ValueSpec(kind=ValueSpecType.TEXT_OF_LENGTH, length=1)
                        ))
                    else:
                        if (other_f.get("type") or "").lower() == "checkbox":
                            short_steps.append(ProbeStep(action_type="CHECK", target=target))
                        else:
                            short_steps.append(ProbeStep(action_type="FILL", target=target, value_spec=_resolve_field_value_spec(other_f)))

                short_steps.append(ProbeStep(action_type="SUBMIT", raw_target=form_sel))
                b_script = ProbeScript(
                    name=f"boundary_password_length_{form_id}_{f_name}",
                    family="forms",
                    description=f"Tests short password length boundary on '{f_name}'",
                    steps=short_steps,
                )
                b_pred = Prediction(
                    expect="Server rejects password with minimum length validation message",
                    signals=["alert_text_contains:character", "no_route_change"]
                )
                probes.append((b_script, b_pred, {
                    "kind": "boundary_password_length",
                    "field": f_name,
                    "rule": f"Password field '{f_name}' must enforce minimum length validation",
                    "expected_outcome": f"Reject submission with length validation message when '{f_name}' is < {min_len} characters"
                }))

    return probes
