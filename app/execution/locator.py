"""
VerityQA Locator Grounding Infrastructure
==========================================
Validates CSS selectors against inspected DOM evidence (InspectionResult)
before they are used in test execution.

This prevents the two classes of failure:
  - LOCATOR_ERROR: selector matches zero elements in the actual DOM
  - AMBIGUOUS_LOCATOR: selector matches multiple unrelated elements

Design principles:
  - Evidence-based only — derived from actual InspectionResult.input_fields_summary
  - No SauceDemo-specific or site-specific hard-coding
  - Returns confidence scores so callers can decide whether to trust the resolution
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

from app.schemas import FormFieldMetadata, InspectionResult

logger = logging.getLogger("verityqa.locator")


class LocatorConfidence(str, Enum):
    """Confidence level of a resolved locator."""
    HIGH = "HIGH"       # Exact attribute match found in DOM evidence
    MEDIUM = "MEDIUM"   # Partial/fuzzy match found in DOM evidence
    LOW = "LOW"         # Candidate is generic; no DOM evidence to confirm
    NONE = "NONE"       # DOM evidence explicitly contradicts the candidate


class LocatorStatus(str, Enum):
    RESOLVED = "RESOLVED"           # Selector confirmed against DOM evidence
    UNVERIFIED = "UNVERIFIED"       # No DOM evidence available to confirm or deny
    AMBIGUOUS = "AMBIGUOUS"         # Multiple distinct DOM elements match
    MISMATCH = "MISMATCH"           # Candidate attribute conflicts with observed DOM
    RESOLUTION_ERROR = "RESOLUTION_ERROR"  # Internal error during resolution


@dataclass
class LocatorCandidate:
    """A single CSS selector candidate with optional purpose hint."""
    selector: str
    purpose: str = ""       # e.g. "username", "password", "submit"
    field_type: str = ""    # e.g. "text", "password", "email"


@dataclass
class LocatorResolution:
    """Result of resolving a locator candidate against DOM evidence."""
    original_selector: str
    resolved_selector: str
    confidence: LocatorConfidence
    status: LocatorStatus
    matched_field: Optional[FormFieldMetadata] = None
    reason: str = ""
    alternatives: List[str] = field(default_factory=list)


class LocatorResolutionError(Exception):
    """Raised when a selector cannot be resolved to any DOM element."""

    def __init__(self, selector: str, reason: str):
        self.selector = selector
        self.reason = reason
        super().__init__(f"LocatorResolutionError for '{selector}': {reason}")


class LocatorResolver:
    """
    Validates and resolves CSS selector candidates against an InspectionResult.

    Usage:
        resolver = LocatorResolver(inspection_result)
        resolution = resolver.resolve("input#username", purpose="username")
        if resolution.confidence in (LocatorConfidence.HIGH, LocatorConfidence.MEDIUM):
            use_selector = resolution.resolved_selector
    """

    def __init__(self, inspection: Optional[InspectionResult] = None):
        self.inspection = inspection

    def _fields(self) -> List[FormFieldMetadata]:
        if not self.inspection:
            return []
        return self.inspection.input_fields_summary or []

    def resolve(
        self,
        selector: str,
        purpose: str = "",
        field_type: str = "",
    ) -> LocatorResolution:
        """
        Resolve a single selector candidate against observed DOM evidence.

        Args:
            selector: CSS selector string (may be compound: 'a, b, c')
            purpose: Human-readable hint for this field's role (e.g. 'username')
            field_type: Expected HTML input type hint (e.g. 'password', 'email')

        Returns:
            LocatorResolution with confidence and best-matching selector.
        """
        fields = self._fields()

        if not fields:
            # No DOM evidence — cannot confirm or deny
            return LocatorResolution(
                original_selector=selector,
                resolved_selector=selector,
                confidence=LocatorConfidence.LOW,
                status=LocatorStatus.UNVERIFIED,
                reason="No DOM evidence available; selector is unverified",
            )

        if self._is_generic_selector(selector):
            return LocatorResolution(
                original_selector=selector,
                resolved_selector=selector,
                confidence=LocatorConfidence.LOW,
                status=LocatorStatus.UNVERIFIED,
                reason="Generic selector; no specific DOM field to match against",
            )

        # Expand compound selectors like "a, b, c"
        candidates = [s.strip() for s in selector.split(",") if s.strip()]

        # Try each candidate against each field
        best: Optional[Tuple[str, LocatorConfidence, FormFieldMetadata]] = None

        for cand in candidates:
            for fld in fields:
                conf = self._match_candidate(cand, fld, purpose, field_type)
                if conf == LocatorConfidence.NONE:
                    continue
                if best is None or self._conf_rank(conf) > self._conf_rank(best[1]):
                    best = (cand, conf, fld)
                    if conf == LocatorConfidence.HIGH:
                        break  # perfect match, stop immediately
            if best and best[1] == LocatorConfidence.HIGH:
                break

        if best is None:
            # No candidate matched any field — but don't error if it's a generic selector
            if self._is_generic_selector(selector):
                return LocatorResolution(
                    original_selector=selector,
                    resolved_selector=selector,
                    confidence=LocatorConfidence.LOW,
                    status=LocatorStatus.UNVERIFIED,
                    reason="Generic selector; no specific DOM field to match against",
                )

            # Try to build a grounded alternative from DOM evidence
            alt = self._build_alternative(purpose, field_type, fields)
            alternatives = [alt] if alt else []
            return LocatorResolution(
                original_selector=selector,
                resolved_selector=alt or selector,
                confidence=LocatorConfidence.NONE,
                status=LocatorStatus.MISMATCH,
                alternatives=alternatives,
                reason=(
                    f"Selector '{selector}' does not match any observed DOM field. "
                    + (f"Suggested alternative: '{alt}'" if alt else "No alternative found.")
                ),
            )

        resolved_sel, conf, matched_fld = best
        # If we matched a field, prefer the most specific selector we can build
        grounded = self._build_grounded_selector(matched_fld, resolved_sel)

        # Check for multiple distinct fields that also match (ambiguity check)
        matching_fields = [
            fld for fld in fields
            if self._match_candidate(resolved_sel, fld, purpose, field_type) != LocatorConfidence.NONE
        ]
        if len(matching_fields) > 1 and conf != LocatorConfidence.HIGH:
            alts = [self._build_grounded_selector(f, resolved_sel) for f in matching_fields]
            return LocatorResolution(
                original_selector=selector,
                resolved_selector=grounded,
                confidence=LocatorConfidence.MEDIUM,
                status=LocatorStatus.AMBIGUOUS,
                matched_field=matched_fld,
                alternatives=alts,
                reason=f"Selector '{resolved_sel}' matches {len(matching_fields)} DOM fields; using best match.",
            )

        return LocatorResolution(
            original_selector=selector,
            resolved_selector=grounded,
            confidence=conf,
            status=LocatorStatus.RESOLVED,
            matched_field=matched_fld,
            reason=f"Matched DOM field: name='{matched_fld.name}', type='{matched_fld.input_type}'",
        )

    def resolve_best(
        self,
        candidates: List[LocatorCandidate],
    ) -> LocatorResolution:
        """
        Resolve the best locator from a list of candidates, returning the
        highest-confidence match.
        """
        best_resolution: Optional[LocatorResolution] = None
        for cand in candidates:
            res = self.resolve(cand.selector, cand.purpose, cand.field_type)
            if best_resolution is None or self._conf_rank(res.confidence) > self._conf_rank(best_resolution.confidence):
                best_resolution = res
            if res.confidence == LocatorConfidence.HIGH:
                break
        return best_resolution or LocatorResolution(
            original_selector="",
            resolved_selector="",
            confidence=LocatorConfidence.NONE,
            status=LocatorStatus.RESOLUTION_ERROR,
            reason="No candidates provided",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_candidate(
        self,
        selector: str,
        fld: FormFieldMetadata,
        purpose: str,
        field_type: str,
    ) -> LocatorConfidence:
        """
        Score how well a CSS selector matches a single FormFieldMetadata record.
        Returns NONE if the selector is explicitly contradicted by the field data.
        """
        sel = selector.lower()

        # Extract attribute selectors: [name='x'], [type='x'], #id
        name_hints = re.findall(r"\[name=['\"]([^'\"]+)['\"]\]", sel)
        type_hints = re.findall(r"\[type=['\"]([^'\"]+)['\"]\]", sel)
        id_hints = re.findall(r"#([\w-]+)", sel)

        fld_name = (fld.name or "").lower()
        fld_type = (fld.input_type or "").lower()
        fld_id = (fld.field_id or "").lower()

        # Explicit name conflict — definitely wrong field
        for nh in name_hints:
            if fld_name and nh != fld_name:
                return LocatorConfidence.NONE

        # Explicit type conflict
        for th in type_hints:
            if fld_type and th != fld_type:
                return LocatorConfidence.NONE

        # Explicit ID conflict
        for ih in id_hints:
            if fld_id and ih != fld_id:
                return LocatorConfidence.NONE

        # Positive matches
        score = 0

        # Name exact match is highest confidence signal
        for nh in name_hints:
            if fld_name and nh == fld_name:
                score += 100

        # Type exact match
        for th in type_hints:
            if fld_type and th == fld_type:
                score += 50

        # ID match
        for ih in id_hints:
            if fld_id and ih == fld_id:
                score += 80

        # Purpose hint matching (fuzzy)
        if purpose:
            purpose_l = purpose.lower()
            if fld_name and purpose_l in fld_name:
                score += 30
            if fld_type and purpose_l == fld_type:
                score += 20

        # field_type hint
        if field_type and fld_type == field_type.lower():
            score += 40

        if score >= 100:
            return LocatorConfidence.HIGH
        if score >= 50:
            return LocatorConfidence.MEDIUM
        if score >= 20:
            return LocatorConfidence.LOW

        # Generic selector — can't confirm but not contradicted
        return LocatorConfidence.LOW

    def _build_grounded_selector(self, fld: FormFieldMetadata, fallback: str) -> str:
        """Build the most specific possible CSS selector from a FormFieldMetadata record."""
        if fld.field_id:
            return f"#{fld.field_id}"
        if fld.name and fld.input_type:
            return f"input[name='{fld.name}']"
        if fld.name:
            return f"[name='{fld.name}']"
        if fld.input_type:
            return f"input[type='{fld.input_type}']"
        return fallback

    def _build_alternative(
        self, purpose: str, field_type: str, fields: List[FormFieldMetadata]
    ) -> Optional[str]:
        """
        Attempt to build a grounded selector for a given purpose from DOM fields.
        Used when the original selector doesn't match.
        """
        purpose_l = (purpose or "").lower()
        type_l = (field_type or "").lower()

        for fld in fields:
            fld_name = (fld.name or "").lower()
            fld_type = (fld.input_type or "").lower()

            name_match = purpose_l and (purpose_l in fld_name or fld_name in purpose_l)
            type_match = type_l and fld_type == type_l

            if name_match or type_match:
                return self._build_grounded_selector(fld, "")

        return None

    def _is_generic_selector(self, selector: str) -> bool:
        """Return True if selector is too generic to validate against specific fields."""
        generic_patterns = [
            r"^button",
            r"^h[1-6]",
            r"^body",
            r"^main",
            r"^form$",
            r"^\*$",
            r"^\[type=['\"]submit['\"]",
            r"^input\[type=['\"]submit['\"]",
        ]
        sel = selector.strip().lower()
        return any(re.match(p, sel) for p in generic_patterns)

    @staticmethod
    def _conf_rank(conf: LocatorConfidence) -> int:
        return {
            LocatorConfidence.HIGH: 4,
            LocatorConfidence.MEDIUM: 3,
            LocatorConfidence.LOW: 2,
            LocatorConfidence.NONE: 0,
        }.get(conf, 0)
