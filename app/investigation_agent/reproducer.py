"""
Isolated Bug Lead Reproducer and Minimizer.
Enforces the strict 2-of-2 CONFIRMED rule in fresh browser contexts:
- 2 of 2 successful reproductions -> CONFIRMED
- 1 of 2 successful reproductions -> UNSTABLE
- 0 of 2 successful reproductions -> REJECTED
Applies delta-debugging step minimization to find the minimal replayable sequence
while strictly preserving prerequisite setup actions.
"""

import time
import logging
from typing import List, Dict, Any, Tuple, Optional

from app.investigation_agent.models import (
    BugLead,
    ProbeScript,
    ProbeStep,
    ProbeResult,
    SemanticTarget,
    OracleStrength,
    OracleType,
    ReproductionOutcome,
)
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.browser_actions import InvestigationActionExecutor
from app.investigation_agent.probe_runner import ProbeRunner
from app.investigation_agent.comparator import classify_form_submission, FormSubmitOutcome
from app.investigation_agent.oracles import evaluate_always_on_oracles

logger = logging.getLogger(__name__)


class BugReproducer:
    """
    Reproduces and minimizes bug leads in fresh, isolated browser sessions.
    Strictly verifies browser state preconditions before action execution.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless

    def _convert_dict_steps_to_script(self, lead: BugLead, steps_list: List[Dict[str, Any]]) -> ProbeScript:
        """Converts step dictionaries into a runnable ProbeScript."""
        probe_steps = []
        for s in steps_list:
            target_data = s.get("target")
            sem_target = None
            raw_target = s.get("raw_target")

            if isinstance(target_data, dict):
                sem_target = SemanticTarget(**target_data)
            elif isinstance(target_data, SemanticTarget):
                sem_target = target_data
            elif isinstance(target_data, str) and not raw_target:
                raw_target = target_data

            probe_steps.append(
                ProbeStep(
                    action_type=s.get("action_type", "CLICK_CONTROL"),
                    target=sem_target,
                    raw_target=raw_target,
                    resolved_value=s.get("value") or s.get("resolved_value"),
                    key_to_press=s.get("key") or s.get("key_to_press"),
                    required_url=s.get("required_url") or lead.source_url,
                    is_prerequisite=s.get("is_prerequisite", False),
                )
            )

        return ProbeScript(
            name=f"repro_{lead.id}",
            family=lead.probe_family or "reproduce",
            description=f"Reproduction of {lead.title}",
            steps=probe_steps,
        )

    def _reconstruct_setup_steps(
        self,
        target_url: str,
        lead: BugLead,
        transitions: Optional[List[Any]] = None,
        states: Optional[Dict[str, Any]] = None,
        auth_credentials: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Reconstructs prerequisite setup steps to reach the lead's source state."""
        if lead.setup_steps:
            return list(lead.setup_steps)

        setup_steps: List[Dict[str, Any]] = []
        if not transitions:
            return setup_steps

        target_state = lead.source_state_id
        if not target_state or target_state == "initial":
            return setup_steps

        # Graph search backwards from target_state
        parent_map = {}
        for t in transitions:
            d_id = getattr(t, "destination_state_id", None)
            s_id = getattr(t, "source_state_id", None)
            if d_id and s_id and d_id not in parent_map:
                parent_map[d_id] = (s_id, t)

        curr = target_state
        path = []
        visited = set()
        while curr in parent_map and curr not in visited:
            visited.add(curr)
            prev, trans = parent_map[curr]
            path.append(trans)
            curr = prev

        path.reverse()

        for t in path:
            act = getattr(t, "action_type", "CLICK_CONTROL")
            target_desc = getattr(t, "target_element_desc", "")
            if "AUTHENTICATE" in act:
                if auth_credentials:
                    setup_steps.extend([
                        {"action_type": "FILL", "raw_target": "#user-name, input[name=user-name], input[name=username]", "value": auth_credentials.get("username", "testuser"), "is_prerequisite": True},
                        {"action_type": "FILL", "raw_target": "#password, input[name=password]", "value": auth_credentials.get("password", "testpassword"), "is_prerequisite": True},
                        {"action_type": "CLICK_CONTROL", "raw_target": "#login-button, input[type=submit], button[type=submit]", "is_prerequisite": True},
                    ])
            elif act in ("CLICK_CONTROL", "NAVIGATE_LINK") and target_desc:
                setup_steps.append({
                    "action_type": act,
                    "raw_target": target_desc,
                    "is_prerequisite": True,
                })

        return setup_steps

    def _test_replay_run(
        self,
        target_url: str,
        script: ProbeScript,
        lead: BugLead,
        setup_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[bool, ReproductionOutcome, Optional[ProbeResult], str]:
        """
        Executes a single replay run in a brand-new, isolated browser session
        inside a dedicated worker thread with no active asyncio event loop.
        Returns (reproduced: bool, outcome: ReproductionOutcome, result: ProbeResult, details: str).
        """
        import concurrent.futures

        def _execute():
            session = StatefulBrowserSession(headless=self.headless)
            runner = ProbeRunner()

            try:
                session.start()

                # Phase 1: Setup & Precondition Enforcement
                # Always start at target_url (entry point)
                ok, nav_url, status = session.navigate(target_url)
                if not ok or status == 404:
                    return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Initial navigation to target URL {target_url} failed (status={status})"

                # Execute prerequisite setup steps if any
                effective_setup = setup_steps if setup_steps is not None else (lead.setup_steps or [])
                if effective_setup:
                    for s_idx, s in enumerate(effective_setup):
                        act_type = s.get("action_type", "CLICK_CONTROL")
                        raw_target = s.get("raw_target") or (s.get("target", {}).get("css") if isinstance(s.get("target"), dict) else str(s.get("target") or ""))
                        val = s.get("value") or s.get("resolved_value")
                        ok_step, outcome_str, fail_reason, _ = InvestigationActionExecutor.execute_action(
                            session=session,
                            action_type=act_type,
                            target=raw_target,
                            value=val,
                            timeout_ms=3000,
                        )
                        if not ok_step:
                            return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Prerequisite setup step {s_idx} ({act_type} on {raw_target}) failed: {outcome_str} ({fail_reason})"
                elif lead.source_url and lead.source_url != target_url:
                    # Attempt direct navigation to source URL
                    ok_nav, nav_url, status = session.navigate(lead.source_url)
                    if not ok_nav or status == 404:
                        return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Navigation to required source URL {lead.source_url} failed with status {status}"
                    if target_url.rstrip("/") == nav_url.rstrip("/") and lead.source_url.rstrip("/") != target_url.rstrip("/"):
                        return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Direct navigation to {lead.source_url} redirected to entry URL (session/auth required)"

                # Pre-probe verification: target elements must exist and be visible
                if script.steps:
                    first_step = script.steps[0]
                    first_target = first_step.raw_target or (first_step.target.css if first_step.target else None)
                    if first_target and first_step.action_type not in ("NAVIGATE_LINK", "OBSERVE", "WAIT"):
                        res_target = session.resolve_target(first_target)
                        if not res_target.get("present"):
                            return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Target '{first_target}' not present in DOM after setup ({res_target.get('reason')})"
                        if not res_target.get("visible"):
                            return False, ReproductionOutcome.REPRODUCTION_SETUP_FAILED, None, f"Target '{first_target}' is not visible in DOM after setup"

                # Capture baseline observation before probe execution
                before_obs = session.capture_observation("repro_before")
                pre_existing_errors = set(session.console_logs)
                session.drain_mutations()

                # Phase 2: Probe Action Execution
                res = runner.run_probe(session=session, script=script)

                if res.status != "SUCCESS":
                    # Action execution failed (timeout, missing element, execution error)
                    # Selector timeouts and test execution errors must NEVER be confirmed as bugs!
                    return False, ReproductionOutcome.ACTION_EXECUTION_FAILED, res, f"Probe action execution failed: {res.error_message}"

                # Phase 3: Defect Violation Verification
                reproduced = False
                violation_reason = ""

                # 1. Form bypass
                if lead.probe_family == "forms" and (
                    "accepted" in lead.title.lower()
                    or "bypass" in lead.title.lower()
                    or "accepted" in lead.actual.get("text", "").lower()
                ):
                    outcome = classify_form_submission(res.diff, res.before_obs, res.after_obs, res.network_events)
                    if outcome == FormSubmitOutcome.ACCEPTED:
                        reproduced = True
                        violation_reason = "Form bypass reproduced: form submission accepted"

                # 2. Console error / technical violations
                if not reproduced and (
                    lead.oracle_type == OracleType.TECHNICAL
                    or "console" in lead.title.lower()
                    or "error" in lead.title.lower()
                ):
                    # Filter console errors: MUST be new, not pre-existing, and not benign 404
                    new_errors = [
                        err for err in res.console_errors
                        if err.message not in pre_existing_errors
                        and not ("failed to load resource" in err.message.lower() and "404" in err.message.lower())
                        and not any(k in err.message.lower() for k in ("favicon", "analytics", "tracking"))
                    ]
                    lead_err_text = lead.actual.get("text", "").lower() or lead.title.lower()
                    for err in new_errors:
                        err_msg = err.message.lower()
                        if (
                            lead.oracle_type == OracleType.TECHNICAL
                            or any(word in err_msg for word in lead_err_text.split() if len(word) > 4)
                            or "error" in err_msg
                        ):
                            reproduced = True
                            violation_reason = f"Uncaught console error reproduced: {err.message[:120]}"
                            break

                # 3. Text anomaly / visual diff verification
                if not reproduced and lead.actual.get("text"):
                    expected_actual = lead.actual.get("text", "").lower()
                    current_actual = [t.lower() for t in (res.diff.text_added if res.diff else [])]
                    current_anomalies = [t.lower() for t in (res.diff.text_anomalies_added if res.diff else [])]
                    if any(expected_actual in t for t in current_actual) or any(t in expected_actual for t in current_anomalies):
                        reproduced = True
                        violation_reason = f"Visual/text anomaly reproduced: {expected_actual[:80]}"

                if reproduced:
                    return True, ReproductionOutcome.CONFIRMED_DEFECT, res, violation_reason
                else:
                    return False, ReproductionOutcome.NOT_REPRODUCED, res, "Probe actions executed cleanly but alleged violation was not observed"

            except Exception as e:
                logger.warning("[BugReproducer] Replay run failed with unhandled exception: %s", e)
                return False, ReproductionOutcome.INCONCLUSIVE, None, f"Unhandled reproduction exception: {e}"

            finally:
                session.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute)
            return future.result()

    def minimize_steps(
        self,
        target_url: str,
        lead: BugLead,
        initial_steps: List[Dict[str, Any]],
        setup_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Minimizes step sequence by removing non-essential probe steps while anomaly persists.
        Prerequisite setup steps are NEVER pruned or modified.
        """
        if len(initial_steps) <= 1:
            return list(initial_steps)

        current_steps = list(initial_steps)
        logger.info("[BugReproducer] Starting step minimization for %s (%d steps)", lead.id, len(current_steps))

        idx = 0
        while idx < len(current_steps):
            step_to_remove = current_steps[idx]
            # Inviolable check: never remove prerequisite steps
            if step_to_remove.get("is_prerequisite") or step_to_remove.get("action_type") in ("NAVIGATE_LINK", "AUTHENTICATE"):
                idx += 1
                continue

            candidate_steps = current_steps[:idx] + current_steps[idx + 1 :]
            if not candidate_steps:
                break

            candidate_script = self._convert_dict_steps_to_script(lead, candidate_steps)
            reproduced, outcome, _, reason = self._test_replay_run(
                target_url, candidate_script, lead, setup_steps=setup_steps
            )

            if reproduced and outcome == ReproductionOutcome.CONFIRMED_DEFECT:
                logger.debug("[BugReproducer] Step %d successfully eliminated", idx)
                current_steps = candidate_steps
                # Check same index again
            else:
                idx += 1

        logger.info("[BugReproducer] Minimized %s from %d to %d steps", lead.id, len(initial_steps), len(current_steps))
        return current_steps

    def reproduce_lead(
        self,
        target_url: str,
        lead: BugLead,
        transitions: Optional[List[Any]] = None,
        states: Optional[Dict[str, Any]] = None,
        auth_credentials: Optional[Dict[str, str]] = None,
    ) -> BugLead:
        """
        Executes the strict 2-of-2 confirmation protocol and step minimization loop.
        """
        if lead.strength not in (OracleStrength.HIGH, OracleStrength.MEDIUM):
            lead.status = "UNSTABLE"
            lead.reproduction_outcome = ReproductionOutcome.INCONCLUSIVE
            return lead

        if not lead.steps:
            lead.steps = [{"action_type": "OBSERVE", "raw_target": None}]

        # Reconstruct prerequisite setup steps if not already attached
        if not lead.setup_steps:
            lead.setup_steps = self._reconstruct_setup_steps(
                target_url=target_url,
                lead=lead,
                transitions=transitions,
                states=states,
                auth_credentials=auth_credentials,
            )

        script = self._convert_dict_steps_to_script(lead, lead.steps)

        logger.info("[BugReproducer] Executing 2-of-2 confirmation protocol for lead '%s'", lead.title)

        # Run 1
        rep_1, outcome_1, res_1, reason_1 = self._test_replay_run(
            target_url, script, lead, setup_steps=lead.setup_steps
        )

        # Run 2
        rep_2, outcome_2, res_2, reason_2 = self._test_replay_run(
            target_url, script, lead, setup_steps=lead.setup_steps
        )

        lead.reproduction_runs = [
            {"run": 1, "reproduced": rep_1, "outcome": outcome_1.value, "reason": reason_1},
            {"run": 2, "reproduced": rep_2, "outcome": outcome_2.value, "reason": reason_2},
        ]

        success_count = (1 if rep_1 else 0) + (1 if rep_2 else 0)
        lead.confirmation = {"runs": 2, "passed": success_count}

        if success_count == 2:
            lead.status = "CONFIRMED"
            lead.reproduction_outcome = ReproductionOutcome.CONFIRMED_DEFECT
            lead.reproduction_failure_reason = None
            logger.info("[BugReproducer] Lead '%s' CONFIRMED (2 of 2). Minimizing steps...", lead.title)

            # Minimize steps
            min_steps = self.minimize_steps(target_url, lead, lead.steps, setup_steps=lead.setup_steps)
            lead.steps = min_steps

            if res_2 and res_2.after_obs:
                lead.after_screenshot_id = res_2.after_obs.screenshot_artifact_id

        elif success_count == 1:
            lead.status = "UNSTABLE"
            lead.reproduction_outcome = ReproductionOutcome.INCONCLUSIVE
            fail_reason = reason_1 if not rep_1 else reason_2
            lead.reproduction_failure_reason = f"Unstable reproduction (1 of 2 succeeded): {fail_reason}"
            logger.warning("[BugReproducer] Lead '%s' is UNSTABLE (1 of 2): %s", lead.title, lead.reproduction_failure_reason)

        else:
            # 0 of 2 reproductions
            lead.status = "REJECTED"
            if outcome_1 == ReproductionOutcome.REPRODUCTION_SETUP_FAILED or outcome_2 == ReproductionOutcome.REPRODUCTION_SETUP_FAILED:
                lead.reproduction_outcome = ReproductionOutcome.REPRODUCTION_SETUP_FAILED
                lead.reproduction_failure_reason = f"Reproduction setup failed: {reason_1 or reason_2}"
            elif outcome_1 == ReproductionOutcome.ACTION_EXECUTION_FAILED or outcome_2 == ReproductionOutcome.ACTION_EXECUTION_FAILED:
                lead.reproduction_outcome = ReproductionOutcome.ACTION_EXECUTION_FAILED
                lead.reproduction_failure_reason = f"Action execution failed during replay: {reason_1 or reason_2}"
            else:
                lead.reproduction_outcome = ReproductionOutcome.NOT_REPRODUCED
                lead.reproduction_failure_reason = f"Defect not reproduced in 2 attempts: {reason_1 or reason_2}"

            logger.info("[BugReproducer] Lead '%s' REJECTED (outcome=%s, reason=%s)",
                        lead.title, lead.reproduction_outcome, lead.reproduction_failure_reason)

        return lead

    def reproduce_all(
        self,
        target_url: str,
        leads: List[BugLead],
        transitions: Optional[List[Any]] = None,
        states: Optional[Dict[str, Any]] = None,
        auth_credentials: Optional[Dict[str, str]] = None,
    ) -> List[BugLead]:
        """
        Reproduces all candidate leads.
        """
        results: List[BugLead] = []
        for lead in leads:
            reproduced_lead = self.reproduce_lead(
                target_url=target_url,
                lead=lead,
                transitions=transitions,
                states=states,
                auth_credentials=auth_credentials,
            )
            results.append(reproduced_lead)
        return results
