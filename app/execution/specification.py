"""
VerityQA Structured Test Specification
=======================================
Adds TestSpecification — a structured, traceable test description that must
accompany every generated test case.

Design:
  - Every assertion must reference a specific objective
  - Expected outcomes must be traced to evidence source (EXPLICIT, OBSERVED, etc.)
  - Prevents the pattern of reusing "Welcome" across unrelated tests
"""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ExpectationType(str, Enum):
    """How an expected outcome was established."""
    EXPLICIT = "EXPLICIT"           # Found verbatim in requirements / spec doc
    OBSERVED = "OBSERVED"           # Seen in the actual DOM / page behavior
    DETERMINISTIC = "DETERMINISTIC" # Derived by math/logic (BVA, constraint)
    INFERRED = "INFERRED"           # Logical inference from common practice
    EXPLORATORY = "EXPLORATORY"     # Unknown — test is discovery, not assertion


class TargetElementRef(BaseModel):
    """A reference to an actual DOM element observed during inspection."""
    selector: str = Field(..., description="CSS selector grounded in observed DOM")
    role: str = Field(..., description="Purpose of this element, e.g. 'username input'")
    observed_attribute: Optional[str] = Field(
        default=None,
        description="Key attribute observed, e.g. 'name=user-name', 'type=password'"
    )
    evidence_source: Optional[str] = Field(
        default=None,
        description="Where this element was discovered, e.g. 'InspectionResult.input_fields_summary[0]'"
    )


class ActionItem(BaseModel):
    """A single structured action in the test procedure."""
    step: int
    action_verb: str    # e.g. "navigate", "fill", "click", "assert", "observe"
    target: Optional[str] = None   # selector or URL
    value: Optional[str] = None    # input value if applicable
    description: str               # human-readable step description
    depends_on_step: Optional[int] = None  # ordering dependency


class VerificationStep(BaseModel):
    """A single verification action tied directly to the test objective."""
    step: int
    method: str         # e.g. "check_element_type", "check_text_visible", "check_validity"
    target: Optional[str] = None   # selector
    expected: Optional[str] = None # expected value / condition
    description: str
    objective_link: str  # which part of the objective this verifies


class TestSpecification(BaseModel):
    """
    Structured, evidence-grounded test specification.
    Every assertion must trace back to objective and evidence.
    """
    objective: str = Field(
        ...,
        description="Clear, single-sentence test objective"
    )
    source_evidence: List[str] = Field(
        default_factory=list,
        description="DOM evidence or specification references that motivate this test"
    )
    preconditions: List[str] = Field(
        default_factory=list,
        description="Required application state before test execution"
    )
    target_elements: List[TargetElementRef] = Field(
        default_factory=list,
        description="Actual DOM elements this test interacts with, grounded in inspection"
    )
    action_sequence: List[ActionItem] = Field(
        default_factory=list,
        description="Ordered test actions"
    )
    expected_outcome: str = Field(
        ...,
        description="Expected outcome, traceable to objective and evidence"
    )
    verification_method: str = Field(
        ...,
        description="How the expected outcome is verified (e.g. check DOM attribute, text visible)"
    )
    expectation_type: ExpectationType = Field(
        ...,
        description="How the expected outcome was established"
    )
    negative_assertion: bool = Field(
        default=False,
        description="If True, test asserts that something does NOT happen"
    )
    requires_authentication: bool = Field(
        default=False,
        description="Whether this test requires prior login"
    )
    isolation_required: bool = Field(
        default=True,
        description="Whether this test requires a fresh browser context"
    )
    not_applicable_reason: Optional[str] = Field(
        default=None,
        description="If set, this test cannot run and this explains why"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional structured evidence or notes"
    )

    def is_runnable(self) -> bool:
        """Return True if this test specification has enough information to execute."""
        return (
            self.not_applicable_reason is None
            and len(self.action_sequence) > 0
            and len(self.target_elements) > 0
        )

    def validate_assertion_traceability(self) -> List[str]:
        """
        Check that all verification steps link back to the objective.
        Returns list of issues found (empty = no issues).
        """
        issues = []
        if not self.objective:
            issues.append("Missing objective")
        if not self.source_evidence:
            issues.append("No source evidence specified")
        if self.expectation_type == ExpectationType.EXPLORATORY and not self.negative_assertion:
            issues.append(
                "EXPLORATORY expectation type should not have a positive assertion — "
                "use negative_assertion=True or mark test as discovery-only"
            )
        return issues
