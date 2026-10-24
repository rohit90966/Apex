"""
Multi-Dimensional Coverage Model for VerityQA Test Planning.
Tracks route, control, transition, journey, validation, invariant, and negative scenario coverage.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class CoverageDimension(str, Enum):
    ROUTE = "ROUTE"
    CONTROL = "CONTROL"
    TRANSITION = "TRANSITION"
    JOURNEY = "JOURNEY"
    FORM_VALIDATION = "FORM_VALIDATION"
    INVARIANT = "INVARIANT"
    NEGATIVE = "NEGATIVE"


class CoverageStats(BaseModel):
    dimension: CoverageDimension
    total_elements: int
    covered_elements: int
    coverage_percentage: float = 0.0
    uncovered_sample: List[str] = Field(default_factory=list)

    @field_validator("uncovered_sample", mode="before")
    @classmethod
    def _coerce_uncovered(cls, v: Any) -> List[str]:
        if not v:
            return []
        return [str(item) for item in v if item is not None and str(item).strip()]


class CoverageReport(BaseModel):
    overall_coverage_percentage: float
    total_scenarios: int
    dimensions: Dict[CoverageDimension, CoverageStats]


class MultiDimensionalCoverageTracker:
    """
    Maintains empirical coverage ledgers and drives scenario selection to maximize diversity.
    """

    def __init__(
        self,
        known_routes: Optional[List[str]] = None,
        known_controls: Optional[List[str]] = None,
        known_transitions: Optional[List[str]] = None,
        known_journeys: Optional[List[str]] = None,
        known_invariants: Optional[List[str]] = None,
    ):
        self.all_routes: Set[str] = {str(r) for r in (known_routes or []) if r}
        self.all_controls: Set[str] = {str(c) for c in (known_controls or []) if c}
        self.all_transitions: Set[str] = {str(t) for t in (known_transitions or []) if t}
        self.all_journeys: Set[str] = {str(j) for j in (known_journeys or []) if j}
        self.all_invariants: Set[str] = {str(i) for i in (known_invariants or []) if i}

        self.covered_routes: Set[str] = set()
        self.covered_controls: Set[str] = set()
        self.covered_transitions: Set[str] = set()
        self.covered_journeys: Set[str] = set()
        self.covered_invariants: Set[str] = set()
        self.validation_scenarios_count: int = 0
        self.negative_scenarios_count: int = 0

    def record_scenario(self, scenario: Any) -> None:
        """Records elements exercised by a synthesized test scenario."""
        # Routes
        for r in self.all_routes:
            if hasattr(scenario, "steps"):
                for s in scenario.steps:
                    val = getattr(s, "value", "") or ""
                    desc = getattr(s, "description", "") or ""
                    if r in val or r in desc:
                        self.covered_routes.add(r)

        # Controls
        for c in self.all_controls:
            if hasattr(scenario, "steps"):
                for s in scenario.steps:
                    sel = getattr(s, "selector", "") or ""
                    if c in sel:
                        self.covered_controls.add(c)

        # Transitions
        tr_ids = getattr(scenario, "source_transition_ids", []) or []
        for tr in tr_ids:
            self.covered_transitions.add(tr)

        # Journeys / Workflows
        cat = getattr(scenario, "category", "") or getattr(scenario, "page_category", "")
        if "workflow" in str(cat).lower() or "journey" in str(scenario.name if hasattr(scenario, "name") else "").lower():
            self.covered_journeys.add(getattr(scenario, "name", str(scenario)))

        # Invariants
        if "invariant" in str(cat).lower() or "rule" in str(cat).lower():
            self.covered_invariants.add(getattr(scenario, "name", str(scenario)))

        # Validations and Negatives
        name_lower = getattr(scenario, "name", "").lower()
        if "validation" in str(cat).lower() or "boundary" in name_lower or "empty" in name_lower:
            self.validation_scenarios_count += 1
        if "negative" in str(cat).lower() or "defect" in name_lower or "error" in name_lower:
            self.negative_scenarios_count += 1

    def compute_coverage(self, total_scenarios: int) -> CoverageReport:
        """Calculates percentage coverage across each dimension."""
        dims: Dict[CoverageDimension, CoverageStats] = {}

        # 1. Routes
        tot_r = max(len(self.all_routes), 1)
        cov_r = len(self.covered_routes)
        dims[CoverageDimension.ROUTE] = CoverageStats(
            dimension=CoverageDimension.ROUTE,
            total_elements=len(self.all_routes),
            covered_elements=cov_r,
            coverage_percentage=round((cov_r / tot_r) * 100, 1),
            uncovered_sample=list(self.all_routes - self.covered_routes)[:5],
        )

        # 2. Controls
        tot_c = max(len(self.all_controls), 1)
        cov_c = len(self.covered_controls)
        dims[CoverageDimension.CONTROL] = CoverageStats(
            dimension=CoverageDimension.CONTROL,
            total_elements=len(self.all_controls),
            covered_elements=cov_c,
            coverage_percentage=round((cov_c / tot_c) * 100, 1),
            uncovered_sample=list(self.all_controls - self.covered_controls)[:5],
        )

        # 3. Transitions
        tot_t = max(len(self.all_transitions), 1)
        cov_t = len(self.covered_transitions)
        dims[CoverageDimension.TRANSITION] = CoverageStats(
            dimension=CoverageDimension.TRANSITION,
            total_elements=len(self.all_transitions),
            covered_elements=cov_t,
            coverage_percentage=round((cov_t / tot_t) * 100, 1),
            uncovered_sample=list(self.all_transitions - self.covered_transitions)[:5],
        )

        # 4. Journeys
        tot_j = max(len(self.all_journeys), 1)
        cov_j = len(self.covered_journeys)
        dims[CoverageDimension.JOURNEY] = CoverageStats(
            dimension=CoverageDimension.JOURNEY,
            total_elements=len(self.all_journeys),
            covered_elements=cov_j,
            coverage_percentage=round((cov_j / tot_j) * 100, 1),
            uncovered_sample=list(self.all_journeys - self.covered_journeys)[:5],
        )

        # 5. Form Validation
        dims[CoverageDimension.FORM_VALIDATION] = CoverageStats(
            dimension=CoverageDimension.FORM_VALIDATION,
            total_elements=max(self.validation_scenarios_count, 3),
            covered_elements=self.validation_scenarios_count,
            coverage_percentage=round(min(1.0, self.validation_scenarios_count / 3) * 100, 1),
        )

        # 6. Invariants
        tot_inv = max(len(self.all_invariants), 1)
        cov_inv = len(self.covered_invariants)
        dims[CoverageDimension.INVARIANT] = CoverageStats(
            dimension=CoverageDimension.INVARIANT,
            total_elements=len(self.all_invariants),
            covered_elements=cov_inv,
            coverage_percentage=round((cov_inv / tot_inv) * 100, 1),
            uncovered_sample=list(self.all_invariants - self.covered_invariants)[:5],
        )

        # 7. Negative Scenarios
        dims[CoverageDimension.NEGATIVE] = CoverageStats(
            dimension=CoverageDimension.NEGATIVE,
            total_elements=max(self.negative_scenarios_count, 2),
            covered_elements=self.negative_scenarios_count,
            coverage_percentage=round(min(1.0, self.negative_scenarios_count / 2) * 100, 1),
        )

        # Overall average
        avg_pct = round(sum(d.coverage_percentage for d in dims.values()) / len(dims), 1)

        return CoverageReport(
            overall_coverage_percentage=avg_pct,
            total_scenarios=total_scenarios,
            dimensions=dims,
        )
