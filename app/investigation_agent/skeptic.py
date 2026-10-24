"""
Skeptic Filter for VerityQA Bug Leads.
Eliminates false-positive leads by critically auditing empirical evidence:
- Rejects UNCERTAIN outcomes.
- Rejects leads based solely on lack of route change without validation signals or server errors.
- Disregards benign trailing-slash or HTTPS redirects.
- Filters out low-confidence suspicions lacking verifiable technical oracle evidence.
"""

import re
import logging
from typing import List, Tuple, Optional
from app.investigation_agent.models import BugLead, StateDiff, OracleType, OracleStrength

logger = logging.getLogger(__name__)


class SkepticFilter:
    """
    Audits candidate bug leads against falsification heuristics before reproduction.
    """

    @classmethod
    def evaluate_lead(cls, lead: BugLead, diff: Optional[StateDiff] = None) -> Tuple[bool, str]:
        """
        Returns (survived: bool, reason: str).
        """
        # 1. Reject UNCERTAIN or unverified outcomes
        if lead.status == "REJECTED":
            return False, "Lead was already rejected"

        actual_text = (lead.actual.get("text") or "").lower()
        expected_text = (lead.expected.get("text") or "").lower()

        if "uncertain" in actual_text:
            return False, "Outcome is UNCERTAIN (cannot verify intended vs defect behavior)"

        # 2. Reject leads claiming failure solely because URL didn't change
        # without any validation alerts, 5xx errors, exceptions, or explicit contradiction
        only_no_nav = "no route change" in actual_text and not any(
            k in actual_text for k in ("error", "exception", "failed", "accepted", "500", "violation")
        )
        if only_no_nav and diff:
            has_error = bool(diff.console_errors_count > 0 or diff.alerts_added or diff.text_anomalies_added)
            if not has_error:
                return False, "Absence of navigation alone without validation signal or error is insufficient evidence"

        # 3. Benign redirect / trailing slash filter
        if "redirect" in actual_text and "30" in actual_text:
            if not any(k in actual_text for k in ("loop", "infinite", "500", "error")):
                return False, "Standard benign HTTP redirect (301/302/307)"

        # 4. Low strength suspicions lacking technical evidence
        if lead.strength == OracleStrength.LOW and lead.oracle_type == OracleType.SUSPICION:
            if not lead.evidence_ids:
                return False, "Low strength suspicion without backing evidence references"

        # 5. Non-distinct expected vs actual
        if actual_text and expected_text and actual_text == expected_text:
            return False, "Expected behavior matches observed actual behavior"

        # 6. Reject leads arising from direct route probing 404s or deep-link route guards
        lead_title = (lead.title or "").lower()
        if "404" in actual_text or "404" in lead_title or "resource not found (404)" in lead_title:
            if any(k in actual_text or k in lead_title for k in ("cart", "checkout", "route", "direct navigation", "deep link")):
                return False, "Direct internal route access returning 404 is an expected route guard or SPA routing limitation, not an application defect"

        # 7. Reject leads arising from action execution timeouts or target absences
        if any(k in actual_text or k in lead_title for k in ("timeout", "timed out", "target absent", "precondition failed", "target_absent")):
            return False, "Selector timeouts and target absences are test execution/precondition issues, not confirmed application defects"

        return True, "Lead survived skeptic audit"

    @classmethod
    def filter_leads(cls, leads: List[BugLead]) -> List[BugLead]:
        """
        Audits a list of bug leads and returns only the surviving candidates.
        """
        surviving: List[BugLead] = []
        for lead in leads:
            survived, reason = cls.evaluate_lead(lead)
            if survived:
                surviving.append(lead)
            else:
                lead.status = "REJECTED"
                lead.actual["rejection_reason"] = reason
                logger.info("[SkepticFilter] Rejected candidate lead '%s': %s", lead.title, reason)

        return surviving
