"""
app/evidence/models.py — Evidence ledger typed models.

All IDs are deterministic (based on URL + field name) so the same target always
produces the same evidence_id within a run.  LLMs may READ evidence but must
never CREATE or MODIFY records.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EvidenceKind(str, Enum):
    FIELD_CONSTRAINT = "FIELD_CONSTRAINT"   # HTML attribute constraint on an input
    FIELD_PRESENCE   = "FIELD_PRESENCE"     # Element exists (or is absent)
    STRUCTURAL       = "STRUCTURAL"         # Page-level structural observation
    FEATURE          = "FEATURE"            # Detected functional feature
    UNKNOWN          = "UNKNOWN"            # Cannot be classified


class EvidenceStatus(str, Enum):
    OBSERVED  = "OBSERVED"    # Directly read from DOM
    DERIVED   = "DERIVED"     # Inferred from observed data by deterministic rule
    ASSUMPTION = "ASSUMPTION" # Assumed in absence of observation
    UNKNOWN   = "UNKNOWN"     # Status cannot be determined


class OracleClass(str, Enum):
    CONSTRAINT_DERIVED  = "CONSTRAINT_DERIVED"   # Expected outcome derived from HTML attribute
    SPECIFICATION       = "SPECIFICATION"         # Expected outcome from explicit requirement
    GENERIC_HEURISTIC   = "GENERIC_HEURISTIC"    # General QA best-practice
    UNKNOWN             = "UNKNOWN"              # Oracle not determinable


# ---------------------------------------------------------------------------
# Core evidence record
# ---------------------------------------------------------------------------

class EvidenceRecord(BaseModel):
    """Atomic, traceable unit of observed website evidence."""
    evidence_id: str         # stable deterministic hash
    kind: EvidenceKind
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    page_id: str             # PageEvidence.page_id or InspectionResult.inspection_id
    page_url: str
    source: str = "DOM_INSPECTION"   # where the evidence came from
    description: str
    observed_value: Optional[Any] = None    # raw attribute/observed value
    attributes: Dict[str, Any] = Field(default_factory=dict)


class FieldConstraintEvidence(EvidenceRecord):
    """
    Specialisation for HTML input-field constraint attributes (maxlength, minlength,
    min, max, pattern, etc.).  Inherits all EvidenceRecord fields.
    """
    kind: EvidenceKind = EvidenceKind.FIELD_CONSTRAINT
    field_name: str
    field_label: Optional[str] = None
    input_type: Optional[str] = None
    constraint_attr: str    # e.g. "maxlength", "minlength", "min", "max", "pattern"
    constraint_value: Any   # the raw attribute value (int, str, float …)


# ---------------------------------------------------------------------------
# Unknown items ledger
# ---------------------------------------------------------------------------

class UnknownCategory(str, Enum):
    NO_HTML_CONSTRAINT     = "NO_HTML_CONSTRAINT"
    SERVER_VALIDATION_UNKNOWN = "SERVER_VALIDATION_UNKNOWN"
    BUSINESS_BEHAVIOR_UNKNOWN = "BUSINESS_BEHAVIOR_UNKNOWN"
    FEATURE_INACCESSIBLE   = "FEATURE_INACCESSIBLE"
    INSPECTION_FAILED      = "INSPECTION_FAILED"
    REQUIREMENT_NEEDED     = "REQUIREMENT_NEEDED"


class UnknownItem(BaseModel):
    """Typed unknowns ledger entry with stable ID and explicit categorization."""
    unknown_id: str
    question: str
    category: UnknownCategory = UnknownCategory.SERVER_VALIDATION_UNKNOWN
    impact: str    # HIGH | MEDIUM | LOW
    resolution_criteria: str
    resolution_source: str = "SPECIFICATION_OR_API_CONTRACT" # PASSIVE_INSPECTION | SPECIFICATION_OR_API_CONTRACT | AUTHENTICATED_SESSION
    related_evidence_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal test-idea model (pre-LLM, deterministic derivation output)
# ---------------------------------------------------------------------------

class TestIdea(BaseModel):
    """
    Deterministically derived test idea — NOT a TestCase.
    Converted to TestCase by DeterministicDerivationNode.
    """
    __test__ = False
    idea_id: str
    rule_id: str           # e.g. "BVA-MAXLENGTH-001"
    evidence_ids: List[str]
    oracle_class: OracleClass
    name: str
    objective: str
    action_description: str
    expected_outcome: str
    derived_values: Dict[str, Any] = Field(default_factory=dict)
    priority: str = "MEDIUM"   # HIGH | MEDIUM | LOW


# ---------------------------------------------------------------------------
# Deterministic stable ID helper
# ---------------------------------------------------------------------------

def make_evidence_id(page_url: str, discriminator: str) -> str:
    """
    Produce a stable, deterministic 12-character hex evidence ID.
    Same (page_url, discriminator) always produces the same ID within a run.
    """
    raw = f"{page_url}|{discriminator}"
    return "ev-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def make_unknown_id(question: str) -> str:
    return "unk-" + hashlib.sha256(question.encode()).hexdigest()[:10]
