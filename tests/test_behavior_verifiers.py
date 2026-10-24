"""
Tests for behavior-specific verifiers.
Uses mock Playwright page objects — no live browser needed.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.execution.verifiers import (
    verify_password_masking,
    verify_empty_field_validation,
    verify_accessibility,
    verify_password_length_constraint,
    run_verifier,
    VerifierOutcomeStatus,
)


def _make_page(evaluate_return=None, evaluate_side_effects=None):
    """Create a mock Playwright page with configurable evaluate results."""
    page = MagicMock()
    if evaluate_side_effects:
        page.evaluate = AsyncMock(side_effect=evaluate_side_effects)
    else:
        page.evaluate = AsyncMock(return_value=evaluate_return)
    return page


class TestPasswordMaskingVerifier:
    @pytest.mark.asyncio
    async def test_pass_when_type_password_present(self):
        """Verifier PASSES when input[type=password] exists in DOM."""
        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            # First call: querySelectorAll input[type="password"]
            [{"id": "password", "name": "password", "type": "password", "hasLabel": True}],
        ])
        outcome = await verify_password_masking(page)
        assert outcome.status == VerifierOutcomeStatus.PASS
        assert "type='password'" in outcome.actual.lower() or "type=password" in outcome.actual.lower()

    @pytest.mark.asyncio
    async def test_fail_when_password_field_not_masked(self):
        """Verifier FAILS when password-named input has wrong type."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=[
            # First call: no input[type="password"]
            [],
            # Second call: password-named inputs with wrong type
            [{"name": "password", "id": "pwd", "type": "text"}],
        ])
        outcome = await verify_password_masking(page)
        assert outcome.status == VerifierOutcomeStatus.FAIL
        assert "type='password'" in outcome.expected.lower()

    @pytest.mark.asyncio
    async def test_inconclusive_when_no_password_field(self):
        """Verifier is INCONCLUSIVE when no password field exists at all."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=[[], []])
        outcome = await verify_password_masking(page)
        assert outcome.status == VerifierOutcomeStatus.INCONCLUSIVE

    @pytest.mark.asyncio
    async def test_does_not_assert_welcome_text(self):
        """Password masking verifier must never check for 'Welcome' text."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {"id": "password", "name": "password", "type": "password", "hasLabel": True}
        ])
        outcome = await verify_password_masking(page)
        # Ensure no mention of "Welcome" in any outcome field
        for field_val in [outcome.actual or "", outcome.expected or "", outcome.notes]:
            assert "welcome" not in field_val.lower()


class TestEmptyFieldVerifier:
    @pytest.mark.asyncio
    async def test_pass_when_required_fields_have_validity(self):
        """Verifier PASSES when required fields have valueMissing=True."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=[
            # required fields query
            [{"name": "username", "id": "user-name", "type": "text",
              "value": "", "valueMissing": True, "valid": False}],
            # invalid count
            1,
        ])
        outcome = await verify_empty_field_validation(page)
        assert outcome.status == VerifierOutcomeStatus.PASS

    @pytest.mark.asyncio
    async def test_exploratory_when_no_required_attrs(self):
        """Verifier is EXPLORATORY when no required attributes are in DOM."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=[
            # No required fields
            [],
            # Invalid count = 0
            0,
        ])
        outcome = await verify_empty_field_validation(page)
        assert outcome.status == VerifierOutcomeStatus.EXPLORATORY


class TestAccessibilityVerifier:
    @pytest.mark.asyncio
    async def test_pass_when_all_controls_have_accessible_names(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {
                "tag": "input", "name": "user-name", "type": "text",
                "ariaLabel": None, "ariaLabelledBy": None,
                "labelText": "Username", "tabIndex": 0,
                "hasAccessibleName": True, "placeholder": None
            },
            {
                "tag": "input", "name": "password", "type": "password",
                "ariaLabel": None, "ariaLabelledBy": None,
                "labelText": "Password", "tabIndex": 0,
                "hasAccessibleName": True, "placeholder": None
            },
        ])
        outcome = await verify_accessibility(page)
        assert outcome.status == VerifierOutcomeStatus.PASS

    @pytest.mark.asyncio
    async def test_fail_when_controls_missing_accessible_name(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {
                "tag": "input", "name": "user-name", "type": "text",
                "ariaLabel": None, "ariaLabelledBy": None,
                "labelText": None, "tabIndex": 0,
                "hasAccessibleName": False, "placeholder": None
            },
        ])
        outcome = await verify_accessibility(page)
        assert outcome.status == VerifierOutcomeStatus.FAIL

    @pytest.mark.asyncio
    async def test_does_not_require_login_success(self):
        """Accessibility verifier must not assert login success."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[])
        outcome = await verify_accessibility(page)
        # Should be INCONCLUSIVE, not checking login text
        assert outcome.status == VerifierOutcomeStatus.INCONCLUSIVE
        for field_val in [outcome.actual or "", outcome.expected or "", outcome.notes]:
            assert "welcome" not in field_val.lower()
            assert "login success" not in field_val.lower()


class TestPasswordLengthVerifier:
    @pytest.mark.asyncio
    async def test_exploratory_when_no_minlength_observed(self):
        """CRITICAL: Never invent a 6-char minimum when no evidence exists."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {"name": "password", "id": "password", "minLength": None, "maxLength": None,
             "pattern": None, "required": True}
        ])
        # No observed_minlength passed in — no constraint in DOM
        outcome = await verify_password_length_constraint(page, observed_minlength=None)
        assert outcome.status == VerifierOutcomeStatus.EXPLORATORY
        assert "exploratory" in outcome.notes.lower() or "no password length constraint" in outcome.notes.lower() \
               or "cannot assert" in outcome.notes.lower()

    @pytest.mark.asyncio
    async def test_pass_when_minlength_enforced(self):
        """PASS when DOM minlength attribute causes tooShort validity."""
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=[
            # First call: get fields
            [{"name": "password", "id": "password", "minLength": 6,
              "maxLength": None, "pattern": None, "required": True}],
            # Second call: validity check
            [{"name": "password", "tooShort": True, "minLength": 6}],
        ])
        outcome = await verify_password_length_constraint(page, observed_minlength=6)
        assert outcome.status == VerifierOutcomeStatus.PASS

    @pytest.mark.asyncio
    async def test_never_invents_6_char_min(self):
        """Verifier must not claim a 6-character minimum without DOM evidence."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {"name": "password", "id": "password", "minLength": None,
             "maxLength": None, "pattern": None, "required": True}
        ])
        outcome = await verify_password_length_constraint(page, observed_minlength=None)
        # Must NOT mention "6 characters" without evidence
        assert "6 character" not in outcome.notes.lower()
        assert "at least 6" not in outcome.notes.lower()
        assert outcome.status == VerifierOutcomeStatus.EXPLORATORY

    @pytest.mark.asyncio
    async def test_inconclusive_when_no_password_field(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[])
        outcome = await verify_password_length_constraint(page)
        assert outcome.status == VerifierOutcomeStatus.INCONCLUSIVE


class TestRunVerifierDispatch:
    @pytest.mark.asyncio
    async def test_unknown_verifier_returns_inconclusive(self):
        page = MagicMock()
        outcome = await run_verifier("nonexistent_verifier", page)
        assert outcome.status == VerifierOutcomeStatus.INCONCLUSIVE

    @pytest.mark.asyncio
    async def test_dispatch_to_password_masking(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value=[
            {"id": "password", "name": "password", "type": "password", "hasLabel": True}
        ])
        outcome = await run_verifier("password_masking", page)
        assert outcome.status == VerifierOutcomeStatus.PASS
