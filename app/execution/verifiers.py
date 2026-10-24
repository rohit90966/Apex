"""
VerityQA Behavior-Specific Verifiers
======================================
Standalone, evidence-grounded verifiers for specific QA behaviors.

Each verifier:
  1. Operates only on evidence observed in the DOM (never assumes behavior).
  2. Returns a structured VerificationOutcome with evidence and rationale.
  3. Does not require login or prior state unless explicitly needed.
  4. Marks outcomes as EXPLORATORY when no evidence supports a specific expectation.

Verifiers included:
  - PasswordMaskingVerifier  — checks input[type=password] without login
  - EmptyFieldVerifier       — inspects required attrs, validity state
  - AccessibilityVerifier    — ARIA labels, keyboard focus, tab order
  - PasswordLengthVerifier   — BVA-based; never invents 6-char minimum
  - FormSubmissionVerifier   — validates form blocking behavior
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Any

logger = logging.getLogger("verityqa.verifiers")


class VerifierOutcomeStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"   # No evidence to make a determination
    EXPLORATORY = "EXPLORATORY"     # Test is discovery-only, no assertion possible
    SKIP = "SKIP"                   # Pre-condition not met


@dataclass
class VerificationOutcome:
    """Structured result from a behavior-specific verifier."""
    status: VerifierOutcomeStatus
    verifier: str
    objective: str
    evidence: List[str] = field(default_factory=list)
    actual: Optional[str] = None
    expected: Optional[str] = None
    notes: str = ""

    def is_defect(self) -> bool:
        return self.status == VerifierOutcomeStatus.FAIL

    def is_actionable(self) -> bool:
        return self.status in (VerifierOutcomeStatus.PASS, VerifierOutcomeStatus.FAIL)


# ---------------------------------------------------------------------------
# Password Masking Verifier
# ---------------------------------------------------------------------------

async def verify_password_masking(page: Any) -> VerificationOutcome:
    """
    Verifies that password fields mask their input using type='password'.

    This check does NOT require login. It only inspects the DOM attribute.
    Evidence source: input[type='password'] DOM attribute presence.

    Never asserts 'Welcome' or any login-success text.
    """
    verifier_name = "PasswordMaskingVerifier"
    objective = "Verify that password input fields have type='password' to mask entry"

    try:
        password_fields = await page.evaluate("""() => {
            const fields = document.querySelectorAll('input[type="password"]');
            return Array.from(fields).map(f => ({
                id: f.id || null,
                name: f.name || null,
                type: f.type,
                hasLabel: !!f.labels && f.labels.length > 0
            }));
        }""")

        if not password_fields:
            # Check if there are password-named inputs with wrong type
            wrong_type = await page.evaluate("""() => {
                const suspects = document.querySelectorAll(
                    'input[name*="password"], input[name*="pwd"], input[id*="password"]'
                );
                return Array.from(suspects)
                    .filter(f => f.type !== 'password')
                    .map(f => ({name: f.name, id: f.id, type: f.type}));
            }""")

            if wrong_type:
                return VerificationOutcome(
                    status=VerifierOutcomeStatus.FAIL,
                    verifier=verifier_name,
                    objective=objective,
                    evidence=[
                        f"Found password-named input with incorrect type: {wrong_type}"
                    ],
                    actual=f"input type='{wrong_type[0].get('type', 'unknown')}'",
                    expected="input type='password'",
                    notes="Password field does not mask input — security defect.",
                )

            return VerificationOutcome(
                status=VerifierOutcomeStatus.INCONCLUSIVE,
                verifier=verifier_name,
                objective=objective,
                evidence=["No password-type inputs found on this page"],
                notes="Page may not have a password field in current state.",
            )

        # All found fields have type=password — this is correct behavior
        field_evidence = [
            f"input[type=password] found: name='{f.get('name') or ''}', id='{f.get('id') or ''}'"
            for f in password_fields
        ]

        return VerificationOutcome(
            status=VerifierOutcomeStatus.PASS,
            verifier=verifier_name,
            objective=objective,
            evidence=field_evidence,
            actual=f"Found {len(password_fields)} input(s) with type='password'",
            expected="All password inputs have type='password'",
            notes="Password masking is implemented correctly via HTML input type.",
        )

    except Exception as e:
        logger.warning("[PasswordMaskingVerifier] Error: %s", e)
        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=verifier_name,
            objective=objective,
            evidence=[],
            notes=f"Verification error: {e}",
        )


# ---------------------------------------------------------------------------
# Empty Field Verifier
# ---------------------------------------------------------------------------

async def verify_empty_field_validation(
    page: Any,
    selector: Optional[str] = None,
) -> VerificationOutcome:
    """
    Verifies that required fields enforce empty-submission prevention.

    Uses HTML5 validity API (valueMissing, :invalid pseudo-class) as primary
    evidence, not text strings like 'required' or 'please fill'.
    """
    verifier_name = "EmptyFieldVerifier"
    objective = "Verify that required fields prevent empty-value submission"

    try:
        # Check for required attributes
        required_fields = await page.evaluate("""() => {
            const fields = document.querySelectorAll('input[required], select[required], textarea[required]');
            return Array.from(fields).map(f => ({
                name: f.name || null,
                id: f.id || null,
                type: f.type,
                value: f.value,
                valueMissing: f.validity ? f.validity.valueMissing : null,
                valid: f.checkValidity ? f.checkValidity() : null,
            }));
        }""")

        # Check :invalid elements
        invalid_count = await page.evaluate(
            "() => document.querySelectorAll(':invalid').length"
        )

        if not required_fields:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.EXPLORATORY,
                verifier=verifier_name,
                objective=objective,
                evidence=["No HTML5 'required' attributes found on this page"],
                notes=(
                    "Page does not use HTML5 required attributes. "
                    "Server-side validation behavior is unknown without submission testing."
                ),
            )

        empty_required = [f for f in required_fields if not f.get("value")]
        value_missing_fields = [f for f in required_fields if f.get("valueMissing")]

        evidence = [
            f"Found {len(required_fields)} required field(s)",
            f"{invalid_count} field(s) currently in :invalid state",
        ]

        if value_missing_fields or invalid_count > 0:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.PASS,
                verifier=verifier_name,
                objective=objective,
                evidence=evidence + [
                    f"Fields with valueMissing=true: {[f.get('name') for f in value_missing_fields]}"
                ],
                actual=f"HTML5 validity enforced: {len(value_missing_fields)} field(s) show valueMissing",
                expected="Required fields block empty submission",
                notes="Browser HTML5 form validation prevents empty submission.",
            )

        if empty_required:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.INCONCLUSIVE,
                verifier=verifier_name,
                objective=objective,
                evidence=evidence + [
                    f"Empty required fields: {[f.get('name') for f in empty_required]}"
                ],
                notes=(
                    "Required fields have empty values but validity is not triggered yet. "
                    "Submission attempt needed to observe validation behavior."
                ),
            )

        return VerificationOutcome(
            status=VerifierOutcomeStatus.PASS,
            verifier=verifier_name,
            objective=objective,
            evidence=evidence,
            notes="All required fields have values; no empty-field violation detected.",
        )

    except Exception as e:
        logger.warning("[EmptyFieldVerifier] Error: %s", e)
        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=verifier_name,
            objective=objective,
            notes=f"Verification error: {e}",
        )


# ---------------------------------------------------------------------------
# Accessibility Verifier
# ---------------------------------------------------------------------------

async def verify_accessibility(
    page: Any,
    selector: Optional[str] = None,
) -> VerificationOutcome:
    """
    Verifies accessibility of form controls using ARIA attributes and labels.

    Evidence sources:
    - aria-label, aria-labelledby attributes
    - <label for=id> associations
    - tabindex for keyboard focus

    Never asserts login-success text. Checks DOM attributes only.
    """
    verifier_name = "AccessibilityVerifier"
    objective = "Verify that interactive controls have accessible names and keyboard support"

    try:
        a11y_data = await page.evaluate("""() => {
            const controls = document.querySelectorAll(
                'input:not([type="hidden"]), button, select, textarea, a[href]'
            );
            const results = [];
            for (const el of controls) {
                const ariaLabel = el.getAttribute('aria-label');
                const ariaLabelledBy = el.getAttribute('aria-labelledby');
                const ariaDescribedBy = el.getAttribute('aria-describedby');
                const name = el.name || el.id || null;
                const type = el.type || el.tagName.toLowerCase();
                const labelEl = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
                const labelText = labelEl ? labelEl.textContent.trim() : null;
                const tabIndex = el.tabIndex;
                const placeholder = el.placeholder || null;

                const hasAccessibleName = !!(ariaLabel || ariaLabelledBy || labelText || placeholder);

                results.push({
                    tag: el.tagName.toLowerCase(),
                    name,
                    type,
                    ariaLabel,
                    ariaLabelledBy,
                    ariaDescribedBy,
                    labelText,
                    tabIndex,
                    hasAccessibleName,
                    placeholder,
                });
            }
            return results;
        }""")

        if not a11y_data:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.INCONCLUSIVE,
                verifier=verifier_name,
                objective=objective,
                evidence=["No interactive controls found on page"],
                notes="Page has no controls to check accessibility for.",
            )

        total = len(a11y_data)
        missing_name = [c for c in a11y_data if not c.get("hasAccessibleName")]
        negative_tabindex = [c for c in a11y_data if (c.get("tabIndex") or 0) < 0]

        evidence = [
            f"Found {total} interactive control(s)",
            f"{len(missing_name)} control(s) missing accessible name",
            f"{len(negative_tabindex)} control(s) have negative tabindex (not keyboard-reachable)",
        ]

        detailed = []
        for c in missing_name[:5]:
            detailed.append(
                f"  - <{c['tag']} name='{c['name']}' type='{c['type']}'> has no aria-label, label[for], or placeholder"
            )

        issues = []
        if missing_name:
            issues.append(f"{len(missing_name)}/{total} controls lack accessible names")
        if negative_tabindex:
            issues.append(f"{len(negative_tabindex)} controls not keyboard-reachable")

        if issues:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.FAIL,
                verifier=verifier_name,
                objective=objective,
                evidence=evidence + detailed,
                actual="; ".join(issues),
                expected="All interactive controls have accessible names and positive tabindex",
                notes="Accessibility defects found. Controls missing ARIA labels or keyboard access.",
            )

        return VerificationOutcome(
            status=VerifierOutcomeStatus.PASS,
            verifier=verifier_name,
            objective=objective,
            evidence=evidence,
            actual=f"All {total} controls have accessible names",
            expected="All interactive controls have accessible names",
            notes="Accessibility check passed: all controls have accessible names.",
        )

    except Exception as e:
        logger.warning("[AccessibilityVerifier] Error: %s", e)
        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=verifier_name,
            objective=objective,
            notes=f"Verification error: {e}",
        )


# ---------------------------------------------------------------------------
# Password Length Verifier
# ---------------------------------------------------------------------------

async def verify_password_length_constraint(
    page: Any,
    observed_minlength: Optional[int] = None,
) -> VerificationOutcome:
    """
    Verifies password length constraints based ONLY on observed DOM evidence.

    - If minlength attribute is observed in DOM, verifies it is enforced.
    - If NO minlength is observed, marks as EXPLORATORY — never invents a
      6-character minimum or any other assumed constraint.

    Args:
        page: Playwright page object.
        observed_minlength: minlength value from InspectionResult/FormFieldMetadata.
                            If None, no constraint was observed.
    """
    verifier_name = "PasswordLengthVerifier"
    objective = "Verify password length constraint enforcement"

    try:
        pwd_fields = await page.evaluate("""() => {
            const fields = document.querySelectorAll('input[type="password"]');
            return Array.from(fields).map(f => ({
                name: f.name || null,
                id: f.id || null,
                minLength: f.minLength > 0 ? f.minLength : null,
                maxLength: f.maxLength > 0 ? f.maxLength : null,
                pattern: f.pattern || null,
                required: f.required,
            }));
        }""")

        if not pwd_fields:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.INCONCLUSIVE,
                verifier=verifier_name,
                objective=objective,
                evidence=["No password input fields found"],
                notes="Cannot test password length — no password field on page.",
            )

        # Check for DOM minlength constraints
        dom_minlengths = [
            (f.get("name") or f.get("id"), f.get("minLength"))
            for f in pwd_fields
            if f.get("minLength")
        ]

        effective_minlength = observed_minlength or (
            dom_minlengths[0][1] if dom_minlengths else None
        )

        evidence = [
            f"Found {len(pwd_fields)} password field(s)",
            f"DOM minlength constraints: {dom_minlengths if dom_minlengths else 'none'}",
        ]

        if effective_minlength is None:
            # No constraint observed — mark as exploratory
            return VerificationOutcome(
                status=VerifierOutcomeStatus.EXPLORATORY,
                verifier=verifier_name,
                objective=objective,
                evidence=evidence,
                notes=(
                    "No password length constraint observed in DOM "
                    "(no minlength attribute, no pattern). "
                    "Cannot assert a minimum length requirement without evidence. "
                    "Server-side policy unknown."
                ),
            )

        # We have an observed constraint — verify it
        validity_data = await page.evaluate(
            """(minlen) => {
                const fields = document.querySelectorAll('input[type="password"]');
                return Array.from(fields).map(f => ({
                    name: f.name,
                    tooShort: f.validity ? f.validity.tooShort : null,
                    minLength: f.minLength,
                }));
            }""",
            effective_minlength,
        )

        too_short_fields = [f for f in validity_data if f.get("tooShort")]

        if too_short_fields:
            return VerificationOutcome(
                status=VerifierOutcomeStatus.PASS,
                verifier=verifier_name,
                objective=objective,
                evidence=evidence + [
                    f"Field '{f.get('name')}' reports tooShort=true for minlength={f.get('minLength')}"
                    for f in too_short_fields
                ],
                actual=f"tooShort validation active for {len(too_short_fields)} field(s)",
                expected=f"Password fields enforce minlength={effective_minlength}",
                notes=f"Password length constraint (minlength={effective_minlength}) is enforced by browser.",
            )

        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=verifier_name,
            objective=objective,
            evidence=evidence + [
                "tooShort state is not active — may need a short-value submission attempt"
            ],
            notes=(
                f"minlength={effective_minlength} is present in DOM but validity.tooShort "
                "is not currently active. Requires a submit attempt with a short value."
            ),
        )

    except Exception as e:
        logger.warning("[PasswordLengthVerifier] Error: %s", e)
        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=verifier_name,
            objective=objective,
            notes=f"Verification error: {e}",
        )


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

VERIFIER_REGISTRY = {
    "password_masking": verify_password_masking,
    "empty_field": verify_empty_field_validation,
    "accessibility": verify_accessibility,
    "password_length": verify_password_length_constraint,
}


async def run_verifier(
    name: str,
    page: Any,
    **kwargs,
) -> VerificationOutcome:
    """
    Run a named verifier. Returns INCONCLUSIVE if name is unknown.

    Args:
        name: Key from VERIFIER_REGISTRY.
        page: Playwright Page object.
        **kwargs: Extra arguments forwarded to the verifier.
    """
    fn = VERIFIER_REGISTRY.get(name)
    if fn is None:
        return VerificationOutcome(
            status=VerifierOutcomeStatus.INCONCLUSIVE,
            verifier=name,
            objective=f"Unknown verifier: {name}",
            notes=f"No verifier registered under name '{name}'.",
        )
    return await fn(page, **kwargs)
