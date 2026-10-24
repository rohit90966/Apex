"""
Bug Leads Ledger and Deduplication Manager for Stateful Investigation.
Tracks candidate defect findings (leads) with:
- Strict evidence grounding (network excerpts, console messages, screenshots).
- Deduplication by (probe_family, state_class, target, oracle_type).
- Epistemic lead states: LEAD, CONFIRMED, UNSTABLE, REJECTED.
- Guaranteed rejection of uncertain outcomes (UNCERTAIN never produces a lead).
"""

from typing import List, Dict, Any, Optional, Set, Tuple
from app.investigation_agent.models import (
    BugLead,
    OracleFinding,
    OracleType,
    OracleStrength,
    FormSubmitOutcome,
    ProbeResult,
)


def _extract_steps_from_result(result: Optional[ProbeResult], target_desc: str) -> List[Dict[str, Any]]:
    """Extracts actual executed steps or clean fallback steps for faithful reproduction."""
    script = getattr(result, "script", None) if result else None
    if script and getattr(script, "steps", None):
        steps_count = result.steps_executed if result.steps_executed > 0 else len(script.steps)
        out = []
        for s in script.steps[:steps_count]:
            target_val = None
            if s.target is not None:
                if hasattr(s.target, "model_dump"):
                    target_val = s.target.model_dump()
                elif hasattr(s.target, "dict"):
                    target_val = s.target.dict()
                elif isinstance(s.target, dict):
                    target_val = s.target
            out.append({
                "action_type": s.action_type,
                "target": target_val,
                "raw_target": s.raw_target,
                "resolved_value": s.resolved_value,
                "key_to_press": s.key_to_press,
            })
        if out:
            return out

    if target_desc and target_desc.lower() not in ("browser", "window", "page", "system", "console"):
        return [{
            "action_type": "CLICK_CONTROL",
            "raw_target": target_desc,
        }]
    return [{"action_type": "OBSERVE", "raw_target": None}]


class BugLeadManager:
    """
    Manages discovery, deduplication, and lifecycle of candidate defect leads.
    """

    def __init__(self):
        self.leads: List[BugLead] = []
        self._dedupe_keys: Set[Tuple[str, str, str, str]] = set()

    def add_lead(self, lead: BugLead) -> bool:
        """
        Adds candidate bug lead if not already recorded.
        Deduplicates by (probe_family, state_class, target_desc, oracle_type).
        """
        key = (lead.probe_family, lead.state_class, lead.target_desc, lead.oracle_type.value)
        if key in self._dedupe_keys:
            return False

        self._dedupe_keys.add(key)
        self.leads.append(lead)
        return True

    def create_lead_from_oracle_finding(
        self,
        finding: OracleFinding,
        probe_family: str,
        question_id: str,
        state_class: str,
        target_desc: str,
        result: ProbeResult,
    ) -> Optional[BugLead]:
        """Converts an OracleFinding into a structured BugLead."""
        console_msgs = [e.message for e in result.console_errors] if result.console_errors else None
        net_summary = result.diff.network_summary if result.diff else None

        lead = BugLead(
            title=finding.title,
            oracle_type=finding.oracle_type,
            strength=finding.strength,
            probe_family=probe_family,
            question_id=question_id,
            state_class=state_class,
            source_state_id=result.before_obs.state_id if result.before_obs else None,
            source_url=result.before_obs.url if result.before_obs else None,
            target_desc=target_desc,
            steps=_extract_steps_from_result(result, target_desc),
            expected={"text": "Clean interaction without technical or specification errors", "provenance": "always_on_oracle"},
            actual={"text": finding.description, "source_url": result.before_obs.url if result.before_obs else ""},
            evidence_ids=finding.evidence_refs,
            before_screenshot_id=result.before_obs.screenshot_artifact_id if result.before_obs else None,
            after_screenshot_id=result.after_obs.screenshot_artifact_id if result.after_obs else None,
            network_excerpt=net_summary,
            console_excerpt=console_msgs,
            severity_guess="HIGH" if finding.strength == OracleStrength.HIGH else "MEDIUM",
            status="LEAD",
        )
        if self.add_lead(lead):
            return lead
        return None

    def create_lead(
        self,
        title: str,
        description: str,
        state_class: str,
        probe_family: str,
        question_id: str,
        target_desc: str,
        oracle_type: OracleType,
        strength: OracleStrength,
        result: ProbeResult,
        evidence_ids: Optional[List[str]] = None,
    ) -> Optional[BugLead]:
        """Creates and adds a candidate defect lead directly."""
        console_msgs = [e.message for e in result.console_errors] if result.console_errors else None
        net_summary = result.diff.network_summary if result.diff else None

        actual_dict = {"text": description}
        if result and result.before_obs and getattr(result.before_obs, "url", None):
            actual_dict["source_url"] = result.before_obs.url

        lead = BugLead(
            title=title,
            oracle_type=oracle_type,
            strength=strength,
            probe_family=probe_family,
            question_id=question_id,
            state_class=state_class,
            source_state_id=result.before_obs.state_id if result and result.before_obs else None,
            source_url=result.before_obs.url if result and result.before_obs else None,
            target_desc=target_desc,
            steps=_extract_steps_from_result(result, target_desc),
            expected={"text": "Clean interaction without technical or specification errors", "provenance": "always_on_oracle"},
            actual=actual_dict,
            evidence_ids=evidence_ids or ([f"probe_{result.script_id}"] if result else []),
            before_screenshot_id=result.before_obs.screenshot_artifact_id if result and result.before_obs else None,
            after_screenshot_id=result.after_obs.screenshot_artifact_id if result and result.after_obs else None,
            network_excerpt=net_summary,
            console_excerpt=console_msgs,
            severity_guess="HIGH" if strength == OracleStrength.HIGH else "MEDIUM",
            status="LEAD",
        )
        if self.add_lead(lead):
            return lead
        return None

    def create_lead_from_form_outcome(
        self,
        outcome: FormSubmitOutcome,
        expected_outcome: str,
        rule_description: str,
        probe_family: str,
        question_id: str,
        state_class: str,
        target_desc: str,
        oracle_type: OracleType,
        strength: OracleStrength,
        result: ProbeResult,
    ) -> Optional[BugLead]:
        """
        Constructs a BugLead when a form submission violates expectation.
        Strict rule: UNCERTAIN outcomes never produce a bug lead.
        """
        if outcome == FormSubmitOutcome.UNCERTAIN:
            return None

        if outcome != FormSubmitOutcome.ACCEPTED:
            return None  # Outcome was rejected as expected

        # The submission was ACCEPTED when it should have been REJECTED!
        actual_desc = f"Form submission accepted (status {outcome.value}) despite violating constraint: {rule_description}"
        evidence_ids = [f"probe_{result.script_id}"]
        if result.after_obs:
            evidence_ids.append(result.after_obs.state_id)

        actual_dict = {"text": actual_desc}
        if result and result.before_obs and getattr(result.before_obs, "url", None):
            actual_dict["source_url"] = result.before_obs.url

        lead = BugLead(
            title=f"Bypass: {rule_description}",
            oracle_type=oracle_type,
            strength=strength,
            probe_family=probe_family,
            question_id=question_id,
            state_class=state_class,
            source_state_id=result.before_obs.state_id if result and result.before_obs else None,
            source_url=result.before_obs.url if result and result.before_obs else None,
            target_desc=target_desc,
            steps=_extract_steps_from_result(result, target_desc),
            expected={"text": f"Submission should be rejected: {expected_outcome}", "provenance": "form_validation_rule"},
            actual=actual_dict,
            evidence_ids=evidence_ids,
            before_screenshot_id=result.before_obs.screenshot_artifact_id if result.before_obs else None,
            after_screenshot_id=result.after_obs.screenshot_artifact_id if result.after_obs else None,
            network_excerpt=result.diff.network_summary if result.diff else None,
            console_excerpt=[e.message for e in result.console_errors] if result.console_errors else None,
            severity_guess="HIGH" if strength == OracleStrength.HIGH else "MEDIUM",
            status="LEAD",
        )
        if self.add_lead(lead):
            return lead
        return None

    def get_leads(self) -> List[BugLead]:
        return self.leads
