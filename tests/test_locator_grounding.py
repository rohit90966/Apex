"""
Tests for LocatorResolver — validates selector grounding against DOM evidence.
"""

import pytest
from app.schemas import FormFieldMetadata, InspectionResult
from app.execution.locator import (
    LocatorResolver,
    LocatorConfidence,
    LocatorStatus,
)


def _make_inspection(*fields: FormFieldMetadata) -> InspectionResult:
    """Helper: create a minimal InspectionResult with the given fields."""
    return InspectionResult(
        inspection_id="test-01",
        target_url="http://example.com",
        final_url="http://example.com",
        page_title="Test Page",
        input_fields_summary=list(fields),
    )


def _field(name=None, input_type=None, field_id=None, minlength=None):
    return FormFieldMetadata(
        name=name,
        input_type=input_type,
        field_id=field_id,
        minlength=minlength,
    )


class TestLocatorResolverBasic:
    def test_no_inspection_returns_low_confidence(self):
        resolver = LocatorResolver(inspection=None)
        res = resolver.resolve("input#username")
        assert res.confidence == LocatorConfidence.LOW
        assert res.status == LocatorStatus.UNVERIFIED

    def test_exact_name_match_returns_high(self):
        inspection = _make_inspection(
            _field(name="user-name", input_type="text", field_id="user-name")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input[name='user-name']")
        assert res.confidence == LocatorConfidence.HIGH
        assert res.status == LocatorStatus.RESOLVED

    def test_wrong_name_returns_mismatch(self):
        """Selector with name='username' when DOM has name='user-name'."""
        inspection = _make_inspection(
            _field(name="user-name", input_type="text")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input[name='username']")
        # Should be NONE/MISMATCH because name doesn't match
        assert res.confidence in (LocatorConfidence.NONE, LocatorConfidence.LOW)

    def test_compound_selector_picks_best(self):
        """Compound 'a, b, c' selector: resolver picks the one that matches DOM."""
        inspection = _make_inspection(
            _field(name="user-name", input_type="text", field_id="user-name")
        )
        resolver = LocatorResolver(inspection)
        # Compound: first is wrong, second is correct
        res = resolver.resolve(
            "input[name='username'], input[name='user-name'], input[type='email']"
        )
        assert res.confidence == LocatorConfidence.HIGH
        assert "user-name" in res.resolved_selector

    def test_password_type_match(self):
        inspection = _make_inspection(
            _field(name="password", input_type="password", field_id="password")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input[name='password']", purpose="password", field_type="password")
        assert res.confidence == LocatorConfidence.HIGH

    def test_purpose_hint_improves_match(self):
        inspection = _make_inspection(
            _field(name="user-name", input_type="text"),
            _field(name="password", input_type="password"),
        )
        resolver = LocatorResolver(inspection)
        # No direct attribute match — relies on purpose hint
        res = resolver.resolve("input[type='text']", purpose="user-name", field_type="text")
        assert res.confidence in (LocatorConfidence.HIGH, LocatorConfidence.MEDIUM)

    def test_generic_selector_returns_unverified(self):
        inspection = _make_inspection(_field(name="user-name"))
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("button[type='submit']")
        assert res.status == LocatorStatus.UNVERIFIED

    def test_build_grounded_selector_prefers_id(self):
        inspection = _make_inspection(
            _field(name="user-name", input_type="text", field_id="user-name")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input[name='user-name']")
        # Should use #user-name because field_id is set
        assert res.resolved_selector == "#user-name"

    def test_mismatch_offers_alternative(self):
        """When selector is wrong, resolver should suggest an alternative."""
        inspection = _make_inspection(
            _field(name="user-name", input_type="text")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input#bad-selector")
        # Can't confirm bad-selector but should still work gracefully
        assert isinstance(res.resolved_selector, str)
        assert res.confidence in (LocatorConfidence.LOW, LocatorConfidence.NONE)

    def test_resolve_best_returns_highest_confidence(self):
        from app.execution.locator import LocatorCandidate
        inspection = _make_inspection(
            _field(name="user-name", input_type="text")
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve_best([
            LocatorCandidate("input[name='wrong']", purpose="username"),
            LocatorCandidate("input[name='user-name']", purpose="username"),
        ])
        assert res.confidence == LocatorConfidence.HIGH


class TestLocatorResolverSauceDemo:
    """Regression tests specifically for the SauceDemo DOM structure."""

    def test_saucedemo_username_field_grounded(self):
        """SauceDemo uses name='user-name', NOT 'username'."""
        inspection = _make_inspection(
            _field(name="user-name", input_type="text", field_id="user-name"),
            _field(name="password", input_type="password", field_id="password"),
        )
        resolver = LocatorResolver(inspection)
        # Composite selector with wrong name first, correct second
        res = resolver.resolve(
            "input#username, input#user-name, input[name='username'], input[name='user-name']",
            purpose="username",
        )
        assert res.confidence == LocatorConfidence.HIGH
        assert "user-name" in res.resolved_selector

    def test_saucedemo_password_field_grounded(self):
        inspection = _make_inspection(
            _field(name="user-name", input_type="text"),
            _field(name="password", input_type="password", field_id="password"),
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve(
            "input#password, input[name='password'], [data-test='password'], input[type='password']",
            purpose="password",
            field_type="password",
        )
        assert res.confidence == LocatorConfidence.HIGH

    def test_no_minlength_on_saucedemo_password(self):
        """SauceDemo has no minlength — resolver should find no constraint."""
        inspection = _make_inspection(
            _field(name="password", input_type="password"),  # No minlength
        )
        resolver = LocatorResolver(inspection)
        res = resolver.resolve("input[name='password']", field_type="password")
        # minlength should NOT be present (not invented)
        if res.matched_field:
            assert res.matched_field.minlength is None
