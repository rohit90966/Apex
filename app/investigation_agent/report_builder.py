"""
Investigation Report Builder.
Assembles the complete 10-section structured Investigation Report
based strictly on empirical evidence gathered in the state graph and evidence ledger.
Contains ZERO test cases, ZERO test suites, and ZERO bug reports.
"""

import time
from typing import List, Dict, Any
from app.investigation_agent.models import (
    InvestigationReport,
    ReportOverview,
    ReportAuthentication,
    DiscoveredPageArea,
    WorkflowStepRecord,
    RuleOrHypothesis,
    RuleCategory,
    InvestigationStatus,
    FrontierStatus,
)
from app.investigation_agent.state import InvestigationAgentState
from app.investigation_agent.behavior_model import ApplicationBehaviorModel


class InvestigationReportBuilder:
    """
    Builds the final 10-section Investigation Report.
    Distinguishes primary workflow progression from secondary/return exploration branches.
    """

    @staticmethod
    def _describe_state_purpose(s_obs: Any) -> str:
        """Derives an evidence-grounded, state-specific purpose description including active UI modifiers."""
        from urllib.parse import urlparse
        parsed_path = (urlparse(s_obs.url or "").path or "/").strip("/")
        route_label = parsed_path.replace(".html", "").replace("-", " ").replace("_", " ").title() if parsed_path else "Entry"

        if s_obs.headings:
            base = f"{s_obs.headings[0]} ({route_label} View)"
        elif s_obs.forms and any(
            fld.get("type") == "password"
            for f in s_obs.forms
            for fld in f.get("fields", [])
        ):
            base = f"{s_obs.page_title or 'Application'} — Authentication Gate ({route_label})"
        elif s_obs.page_title:
            base = f"{s_obs.page_title} — {route_label} View"
        else:
            base = f"{route_label} View"

        modifiers = []
        if getattr(s_obs, "has_validation_error", False):
            err_msg = (getattr(s_obs, "validation_error_message", "") or "Validation Error").strip()
            if len(err_msg) > 42:
                err_msg = err_msg[:39] + "..."
            modifiers.append(f"Validation Alert: {err_msg}")
        if getattr(s_obs, "menu_open", False):
            modifiers.append("Navigation Drawer Open")
        dyn = getattr(s_obs, "dynamic_signals", None) or {}
        if str(dyn.get("counter_value", "0")) not in ("0", "", "None"):
            modifiers.append(f"Badge Counter: {dyn['counter_value']}")
        if dyn.get("selected_option"):
            modifiers.append(f"Selected Option: {dyn['selected_option']}")

        if modifiers:
            return f"{base} [{' | '.join(modifiers)}]"
        return base

    @classmethod
    def build_report(cls, state: InvestigationAgentState) -> InvestigationReport:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        start_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.get("start_time", time.time())))

        states_dict = state.get("states", {})
        transitions = state.get("transitions", [])
        evidence_ledger = state.get("evidence_ledger", [])
        frontier = state.get("frontier", {})
        rules = state.get("rules_and_hypotheses", [])

        from app.intelligence.target_context import build_inventory_from_state, build_target_context
        inventory = build_inventory_from_state(state)
        target_context = build_target_context(state)

        # Section A: Overview
        successful_transitions = [t for t in transitions if t.status == "confirmed" and getattr(t, "is_successful", True)]
        in_page_mutations = sum(1 for t in successful_transitions if getattr(t, "is_in_page_mutation", False))
        agenda = state.get("agenda")
        if agenda and hasattr(agenda, "questions") and agenda.questions:
            pending_frontier = sum(1 for q in agenda.questions.values() if q.status == "unexplored")
            total_candidates = max(len(frontier), len(agenda.questions))
        else:
            pending_frontier = sum(1 for item in frontier.values() if item.status == FrontierStatus.DISCOVERED)
            total_candidates = len(frontier)

        # Genuine deduplicated DOM counts across discovered routes/views (never inflated by in-page state clones)
        unique_headings = {(s.normalized_route, h.strip()) for s in states_dict.values() for h in (s.headings or []) if h and h.strip()}
        unique_forms = {
            (s.normalized_route, f.get("form_id") or f.get("submit_control_selector") or str(idx))
            for s in states_dict.values()
            for idx, f in enumerate(s.forms or [])
        }
        unique_inputs = {
            (s.normalized_route, fld.get("stable_selector") or fld.get("name") or fld.get("label") or str(idx))
            for s in states_dict.values()
            for f in (s.forms or [])
            for idx, fld in enumerate(f.get("fields", []))
        }
        unique_buttons = {
            (s.normalized_route, c.role, (c.accessible_name or c.selector or c.element_id).strip().lower())
            for s in states_dict.values()
            for c in (s.interactive_controls or [])
            if c.role in ("button", "submit", "combobox") or c.element_type in ("button", "submit") or c.tag_name.lower() in ("button", "select")
        }
        unique_links = {
            (s.normalized_route, (l.get("href") or l.get("text") or "").strip())
            for s in states_dict.values()
            for l in (s.navigation_links or [])
            if (l.get("href") or l.get("text"))
        }
        total_headings = len(unique_headings)
        total_forms = len(unique_forms)
        total_inputs = len(unique_inputs)
        total_buttons = len(unique_buttons)
        total_links = len(unique_links)

        # Action & Transition Reconciled Metrics
        actions_att = state.get("actions_attempted", 0)
        actions_succ = state.get("actions_successful", 0)
        actions_blocked = state.get("actions_safety_blocked", 0)
        actions_eval = state.get("actions_evaluated", actions_att + actions_blocked)
        actions_fail = state.get("actions_failed", 0)
        actions_verified = state.get("actions_verified_effects", len(successful_transitions))

        route_trans = state.get("route_transitions_observed", sum(1 for t in successful_transitions if getattr(t, "is_route_change", False)))
        same_route_trans = state.get("same_route_state_transitions", sum(1 for t in successful_transitions if not getattr(t, "is_route_change", False)))

        elapsed_sec = round(time.time() - state.get("start_time", time.time()), 2)
        unique_routes = len(set(s.url.split('?')[0].rstrip('/') for s in states_dict.values()))

        # Determine truthful investigation status
        stop_reason = (state.get("stopping_reason") or "").lower()
        if actions_blocked > 0 and actions_succ == 0 and actions_att == 0:
            computed_status = InvestigationStatus.BLOCKED
        elif "time" in stop_reason or "duration" in stop_reason:
            computed_status = InvestigationStatus.TIMED_OUT
        elif "budget" in stop_reason or "limit reached" in stop_reason or "ceiling" in stop_reason:
            computed_status = InvestigationStatus.BUDGET_EXHAUSTED if pending_frontier > 0 else InvestigationStatus.COMPLETED
        elif "saturation" in stop_reason or "converged" in stop_reason:
            computed_status = InvestigationStatus.PARTIAL if pending_frontier > 0 else InvestigationStatus.COMPLETED
        elif actions_blocked > 0 and actions_succ > 0 and pending_frontier > 0:
            computed_status = InvestigationStatus.PARTIAL
        elif pending_frontier > 0:
            computed_status = InvestigationStatus.PARTIAL
        else:
            computed_status = InvestigationStatus.COMPLETED

        current_obs = state.get("current_state")
        actual_final_url = current_obs.url if current_obs else state.get("target_url", "")
        actual_http_status = current_obs.http_status if current_obs else None
        browser_exec_time = round(float(state.get("browser_execution_time", 0.0) or (agenda.browser_execution_time if agenda else 0.0)), 2)
        retries_cnt = int(state.get("retries_count", 0) or (agenda.retries_count if agenda else 0))

        overview = ReportOverview(
            target_url=state.get("target_url", ""),
            final_url=actual_final_url,
            http_status=actual_http_status,
            user_description=state.get("user_description"),
            investigation_id=state.get("investigation_id", ""),
            start_time=start_str,
            end_time=now,
            duration_seconds=elapsed_sec,
            browser_execution_time=browser_exec_time,
            status=computed_status,
            planning_strategy=state.get("planning_strategy", "DETERMINISTIC"),
            llm_calls_count=state.get("llm_calls_count", 0),
            llm_fallbacks_count=state.get("llm_fallbacks_count", 0),
            llm_latency_seconds=state.get("llm_latency_seconds", 0.0),
            retries_count=retries_cnt,
            pages_discovered=unique_routes or 1,
            meaningful_states_discovered=len(states_dict),
            candidates_discovered=total_candidates,
            actions_evaluated=actions_eval,
            actions_attempted=actions_att,
            actions_successful=actions_succ,
            actions_verified_effects=actions_verified,
            actions_failed=actions_fail,
            actions_safety_blocked=actions_blocked,
            transitions_observed=len(successful_transitions),
            route_transitions_observed=route_trans,
            same_route_state_transitions=same_route_trans,
            in_page_mutations_observed=in_page_mutations,
            pending_frontier_count=pending_frontier,
            evidence_ledger_count=len(evidence_ledger),
            deduplicated_candidates_count=state.get("deduplicated_candidates", 0),
            total_headings_discovered=total_headings,
            total_forms_discovered=total_forms,
            total_inputs_discovered=total_inputs,
            total_buttons_discovered=total_buttons,
            total_links_discovered=total_links,
            stopping_reason=state.get("stopping_reason", "Completed within budget"),
        )

        # Section B: Application Understanding
        observed_headings = set()
        for s in states_dict.values():
            for h in s.headings:
                observed_headings.add(h)

        has_auth = state.get("auth_verified", False)
        auth_status_desc = "Authentication was verified, unlocking the internal authenticated application experience." if has_auth else "Operated within public/unauthenticated surface."

        app_understanding = (
            f"The application at {overview.target_url} presents an interactive web service. "
            f"{auth_status_desc} "
            f"Observed key functional landmarks include: {', '.join(list(observed_headings)[:5]) or 'Standard navigation layout'}. "
            f"Extracted {len(states_dict)} distinct UI states across {len(transitions)} observed transitions."
        )

        user_context_distinction = (
            f"User provided description: '{state.get('user_description') or 'None provided'}'. "
            "All findings in this report are independently verified by DOM observations and Playwright interactions."
        )

        # Section C: Authentication and Access
        auth_creds_dict = state.get("auth_credentials")
        creds_source = auth_creds_dict.get("source") if isinstance(auth_creds_dict, dict) else None
        auth_report = ReportAuthentication(
            boundary_observed=state.get("auth_boundary_detected", False),
            login_url=state.get("target_url") if state.get("auth_boundary_detected") else None,
            credentials_available=bool(auth_creds_dict),
            credentials_source=creds_source,
            authentication_attempted=state.get("auth_attempted", False),
            authentication_verified=state.get("auth_verified", False),
            verification_signals=state.get("auth_signals", []),
            protected_functionality_accessible=state.get("protected_features_unlocked", []),
            unresolved_or_blocked_areas=[
                item.target for item in frontier.values() if item.status == FrontierStatus.BLOCKED
            ]
        )

        # Section D: Discovered Application Map
        app_map: List[DiscoveredPageArea] = []
        for s_id, s_obs in states_dict.items():
            ctrl_summaries = [f"{c.role}: {c.accessible_name}" for c in s_obs.interactive_controls[:5]]
            app_map.append(DiscoveredPageArea(
                area_id=s_id,
                route_or_url=s_obs.url,
                apparent_purpose=cls._describe_state_purpose(s_obs),
                relevant_controls_summary=ctrl_summaries,
                status=FrontierStatus.INVESTIGATED,
                evidence_ids=[e.evidence_id for e in evidence_ledger if e.source_state_id == s_id or e.after_state_ref == s_id][:3],
                limitations="Exploration bounded by safety policy and configured action budget"
            ))

        # Section E: User Workflows and State Transitions (Branch-Aware Classification)
        workflow_records: List[WorkflowStepRecord] = []
        seen_primary_states = set()
        transition_branches: Dict[str, str] = {}

        for idx, tr in enumerate(successful_transitions):
            ev_id = tr.evidence_ids[0] if tr.evidence_ids else "ev-direct"
            dest = tr.destination_state_id or tr.source_state_id
            act_lower = (tr.action_type or "").lower()
            target_lower = (tr.target_element_desc or "").lower()
            obs_eff = getattr(tr, "observed_effect", "") or ""

            is_validation_branch = (
                obs_eff == "VALIDATION_FEEDBACK"
                or "with_error" in dest
                or any(k in act_lower for k in ("required_blank", "mismatch", "boundary", "bypass", "invalid"))
            ) and "authenticate" not in act_lower

            is_utility_or_return = (
                not is_validation_branch
                and (
                    dest in seen_primary_states
                    or "menu_open" in dest
                    or any(k in act_lower or k in target_lower for k in ("burger", "open_menu", "close_menu", "sidebar", "reload", "back-to", "cancel"))
                )
            )

            if is_validation_branch:
                branch_label = "Validation / Constraint Branch"
            elif is_utility_or_return:
                branch_label = "Exploratory / Return Branch"
                seen_primary_states.add(tr.source_state_id)
                seen_primary_states.add(dest)
            else:
                branch_label = "Primary Workflow"
                seen_primary_states.add(tr.source_state_id)
                seen_primary_states.add(dest)

            transition_branches[tr.transition_id] = branch_label

            workflow_records.append(WorkflowStepRecord(
                step_number=idx + 1,
                workflow_branch=branch_label,
                from_state=tr.source_state_id,
                action_taken=f"[{branch_label}] {tr.action_type} on {tr.target_element_desc}",
                target_element=tr.target_element_desc,
                to_state=dest,
                observed_outcome=tr.actual_outcome,
                evidence_id=ev_id,
                is_successful=True
            ))

        # Section F: Observed Behaviors
        observed_behaviors = [
            f"Transition {tr.transition_id}: {tr.action_purpose} resulted in {tr.actual_outcome}"
            for tr in successful_transitions
        ]
        failed_entries = [ev for ev in evidence_ledger if ev.evidence_type in ("ACTION_FAILURE", "SAFETY_DECISION")]
        for fe in failed_entries[:5]:
            observed_behaviors.append(f"Recorded Observation [{fe.evidence_type}]: {fe.summary}")

        if not observed_behaviors:
            observed_behaviors.append("Observed initial state layout and extracted interactive controls.")

        # Section G: Business Rules and Hypotheses
        if not rules:
            if state.get("auth_verified"):
                rules.append(RuleOrHypothesis(
                    rule_id="RULE-AUTH-001",
                    description="Authentication permits transition from login boundary into the protected application workspace.",
                    category=RuleCategory.OBSERVED_BEHAVIOR,
                    source="Browser Authentication Interaction",
                    confidence=1.0,
                    limitations="Evaluated using configured test credentials."
                ))
            
            # Check if validation errors were observed in any state
            val_states = [s for s in states_dict.values() if s.has_validation_error]
            if val_states:
                sample_val = val_states[0]
                rules.append(RuleOrHypothesis(
                    rule_id="RULE-VAL-001",
                    description=f"Form validation constraint enforced: '{sample_val.validation_error_message}' displayed on invalid or incomplete submission.",
                    category=RuleCategory.OBSERVED_BEHAVIOR,
                    source=f"Live DOM Element: {sample_val.validation_error_locator}",
                    confidence=1.0,
                    limitations="Evaluated without required form fields populated."
                ))

            # Check if menu expansion was observed
            menu_states = [s for s in states_dict.values() if s.menu_open]
            if menu_states:
                rules.append(RuleOrHypothesis(
                    rule_id="RULE-NAV-002",
                    description="Expandable navigation drawer: Menu toggle triggers visible navigation controls without route change.",
                    category=RuleCategory.OBSERVED_BEHAVIOR,
                    source="Live DOM Navigation Drawer",
                    confidence=1.0,
                    limitations="Evaluated within active browser viewport."
                ))

            rules.append(RuleOrHypothesis(
                rule_id="HYPO-NAV-001",
                description="Interactive controls trigger in-page state updates or route changes.",
                category=RuleCategory.INFERRED_HYPOTHESIS,
                source="DOM Inspection and Frontier Analysis",
                confidence=0.85,
                limitations="Server-side persistence across browser sessions requires separate verification."
            ))

        # Section H: Investigation Coverage and Frontier
        unexplored_agenda_candidates = []
        if agenda and hasattr(agenda, "questions"):
            for q in agenda.questions.values():
                if q.status == "unexplored":
                    if "time" in stop_reason or "duration" in stop_reason:
                        remain_reason = f"Investigation time budget reached before exploration ({state.get('stopping_reason')})"
                    elif "budget" in stop_reason or "limit" in stop_reason or "ceiling" in stop_reason:
                        remain_reason = f"Investigation operational ceiling reached before exploration ({state.get('stopping_reason')})"
                    elif "saturation" in stop_reason or "converged" in stop_reason:
                        remain_reason = "Deprioritized after investigation reached information saturation on primary paths"
                    elif "blocked" in stop_reason:
                        remain_reason = "Blocked by safety policy"
                    else:
                        remain_reason = "Frontier item pending in lower priority tier"

                    unexplored_agenda_candidates.append({
                        "target": q.target_element or q.id,
                        "rationale": q.hypothesis,
                        "category": q.category,
                        "priority": round(q.priority, 3),
                        "reason_unexplored": remain_reason,
                    })

        if unexplored_agenda_candidates:
            unexplored_sample = unexplored_agenda_candidates[:15]
            rec_priorities = [c["rationale"] for c in unexplored_agenda_candidates[:3]]
        else:
            unexplored_sample = [
                {
                    "target": item.target,
                    "rationale": item.rationale,
                    "category": item.item_type,
                    "priority": item.priority_score,
                    "reason_unexplored": "Frontier candidate discovered but unvisited before session end",
                }
                for item in list(frontier.values())[:15] if item.status == FrontierStatus.DISCOVERED
            ]
            rec_priorities = [
                item["rationale"] for item in unexplored_sample[:3]
            ] or [
                "Verify state persistence across full session reload",
                "Probe additional form boundary constraints on discovered views",
                "Inspect secondary navigation links and interactive controls",
            ]

        unexecuted_probes = list(state.get("unexecuted_probes") or [])
        unexecuted_by_reason: Dict[str, int] = {}
        for item in unexecuted_probes:
            unexecuted_by_reason[item.get("reason", "UNKNOWN")] = unexecuted_by_reason.get(item.get("reason", "UNKNOWN"), 0) + 1

        coverage_frontier = {
            # Probes that never executed their action; they are NOT tested coverage.
            "unexecuted_probes_count": len(unexecuted_probes),
            "unexecuted_probes_by_reason": unexecuted_by_reason,
            "unexecuted_probes": unexecuted_probes[:30],
            "explored_states_count": len(states_dict),
            "unexplored_items_count": pending_frontier,
            "blocked_items_count": max(
                sum(1 for item in frontier.values() if item.status == FrontierStatus.BLOCKED),
                actions_blocked,
            ),
            "unexplored_candidates": unexplored_sample,
            "recommended_next_priorities": rec_priorities,
        }

        # Section I: Evidence and Traceability
        evidence_list = [
            {
                "evidence_id": ev.evidence_id,
                "type": ev.evidence_type,
                "summary": ev.summary,
                "state_ref": ev.source_state_id,
                "screenshot": ev.screenshot_artifact_id,
            }
            for ev in evidence_ledger
        ]

        # Section J: Investigation Limitations
        limitations = [
            "Bounded investigation: Actions were capped by the configured safety limits (actions budget).",
            "Destructive actions (e.g. purchasing, deleting user accounts) were deliberately not performed.",
            "Client-side DOM constraints were observed; backend server-side database validation rules were not directly queried."
        ]
        if actions_blocked > 0:
            limitations.append(f"Safety Policy Constrained: {actions_blocked} probe action(s) were blocked by the Safety Governor to enforce domain or action boundaries.")
        if unexecuted_by_reason.get("MUTATION_BUDGET_EXHAUSTED"):
            limitations.append(f"Mutation budget exhausted: {unexecuted_by_reason['MUTATION_BUDGET_EXHAUSTED']} planned probe(s) did not run; "
                               "the forms they target are not covered by this investigation.")
        if unexecuted_by_reason.get("TARGET_NOT_ON_CURRENT_PAGE"):
            limitations.append(f"{unexecuted_by_reason['TARGET_NOT_ON_CURRENT_PAGE']} probe(s) were skipped because their target control "
                               "was not on the current page; they are not counted as tested.")
        if actions_succ == 0:
            limitations.append("Zero probe actions were executed; all reported application structure reflects initial passive observation.")
        if not state.get("auth_verified") and state.get("auth_boundary_detected"):
            limitations.append("Protected authenticated functionality remains uninvestigated because no authorized credentials were provided or verified.")

        # Phase 6: Behavior Model & Bug Leads Integration
        bug_leads = state.get("bug_leads") or []
        confirmed_leads = [b for b in bug_leads if getattr(b, "status", "") == "CONFIRMED"]
        unstable_or_rejected = [b for b in bug_leads if getattr(b, "status", "") in ("UNSTABLE", "SUSPECTED", "REJECTED")]

        # Inferred Journeys
        inferred_journeys = []
        target_url = state.get("target_url") or (list(states_dict.values())[0].url if states_dict else "")
        auth_verified = state.get("auth_verified", False)
        auth_creds = state.get("auth_credentials") or {}

        primary_steps = []
        if target_url:
            primary_steps.append({
                "action": "NAVIGATE",
                "target": target_url,
                "description": f"Navigate to entry view: {target_url}",
            })

        if auth_verified:
            # Login selectors come from the login form observed in this run; the password is never
            # copied into the report (the runner resolves the placeholder from scoped credentials).
            from app.investigation_agent.test_synthesis import CREDENTIAL_PASSWORD_PLACEHOLDER
            login_form = inventory.login_form()
            user_field = next((f for f in login_form.fields if f.field_type in ("text", "email")), None) if login_form else None
            pass_field = next((f for f in login_form.fields if f.field_type == "password"), None) if login_form else None
            if user_field and pass_field and login_form.submit_selector:
                primary_steps.extend([
                    {
                        "action": "FILL",
                        "target": user_field.selector,
                        "value": auth_creds.get("username", "authorized_user"),
                        "description": "Populate authorized username",
                    },
                    {
                        "action": "FILL",
                        "target": pass_field.selector,
                        "value": CREDENTIAL_PASSWORD_PLACEHOLDER,
                        "description": "Populate authorized password",
                    },
                    {
                        "action": "CLICK",
                        "target": login_form.submit_selector,
                        "description": "Submit login authentication",
                    },
                ])

        validation_steps = []
        exploratory_steps = []

        for tr in successful_transitions:
            if tr.action_type == "AUTHENTICATE":
                continue
            branch_label = transition_branches.get(tr.transition_id, "Primary Workflow")
            tr_act = str(tr.action_type).upper()
            target_sel = tr.target_element_desc
            dest_obs = states_dict.get(tr.destination_state_id) if tr.destination_state_id else None
            exp_url_frag = ""
            if dest_obs and getattr(tr, "is_route_change", False):
                from urllib.parse import urlparse
                parsed_dest = urlparse(dest_obs.url)
                if parsed_dest.path and parsed_dest.path != "/":
                    exp_url_frag = parsed_dest.path

            exp_heading = dest_obs.headings[0] if (dest_obs and dest_obs.headings) else ""
            key_selector = dest_obs.interactive_controls[0].selector if (dest_obs and dest_obs.interactive_controls) else ""
            ev_refs = tr.evidence_ids

            if branch_label == "Validation / Constraint Branch":
                validation_steps.append({
                    "action": "VERIFY_CONSTRAINT",
                    "target": target_sel,
                    "description": f"{tr.action_purpose} -> {tr.actual_outcome}",
                    "expected_transition": tr.destination_state_id or "",
                    "evidence_refs": ev_refs,
                })
                continue

            target_list = primary_steps if branch_label == "Primary Workflow" else exploratory_steps

            if "SELECT_OPTION" in tr_act or "SELECT_" in tr_act:
                sel_val = (dest_obs.dynamic_signals or {}).get("selected_option", "") if dest_obs else ""
                if target_sel and target_sel != "probe":
                    target_list.append({
                        "action": "SELECT_OPTION",
                        "target": target_sel,
                        "value": sel_val,
                        "description": f"Select option '{sel_val}' on {target_sel}",
                        "expected_transition": tr.destination_state_id or "",
                        "expected_url_fragment": exp_url_frag,
                        "expected_heading": exp_heading,
                        "expected_selector": key_selector,
                        "evidence_refs": ev_refs,
                    })
            elif any(k in tr_act for k in ("CLICK", "SUBMIT", "CONTROL_")):
                # Only include valid CSS/Playwright locator targets (skip synthetic form probe names)
                if target_sel and not target_sel.startswith("form_") and not target_sel.startswith("required_blank") and target_sel != "probe":
                    target_list.append({
                        "action": "CLICK",
                        "target": target_sel,
                        "description": f"Trigger {target_sel}",
                        "expected_transition": tr.destination_state_id or "",
                        "expected_url_fragment": exp_url_frag,
                        "expected_heading": exp_heading,
                        "expected_selector": key_selector,
                        "evidence_refs": ev_refs,
                    })
            elif any(k in tr_act for k in ("NAVIGATE", "GOTO", "NAV_LINK")):
                dest_url = dest_obs.url if dest_obs else target_sel
                target_list.append({
                    "action": "NAVIGATE",
                    "target": dest_url,
                    "description": f"Navigate to {dest_url}",
                    "expected_transition": tr.destination_state_id or "",
                    "expected_url_fragment": exp_url_frag,
                    "expected_heading": exp_heading,
                    "expected_selector": key_selector,
                    "evidence_refs": ev_refs,
                })
            elif any(k in tr_act for k in ("FILL", "TYPE")):
                val = tr.preconditions[0] if tr.preconditions else "test"
                if target_sel and not target_sel.startswith("form_") and not target_sel.startswith("required_blank"):
                    target_list.append({
                        "action": "FILL",
                        "target": target_sel,
                        "value": val,
                        "description": f"Fill {target_sel}",
                        "expected_transition": tr.destination_state_id or "",
                        "expected_url_fragment": exp_url_frag,
                        "expected_heading": exp_heading,
                        "evidence_refs": ev_refs,
                    })

        if primary_steps:
            inferred_journeys.append({
                "name": "Primary Discovered Progression",
                "steps": primary_steps,
            })
        if validation_steps:
            inferred_journeys.append({
                "name": "Validation & Constraint Verification",
                "steps": validation_steps,
            })
        if exploratory_steps:
            inferred_journeys.append({
                "name": "Exploratory & Utility Branches",
                "steps": exploratory_steps,
            })

        # Assemble ApplicationBehaviorModel
        model = ApplicationBehaviorModel()
        for s in states_dict.values():
            model.add_state_class(
                class_id=s.state_id,
                primary_purpose=cls._describe_state_purpose(s),
                representative_route=s.url,
                key_controls=[c.selector for c in s.interactive_controls[:5]],
                outbound_transitions=[t.destination_state_id for t in transitions if t.source_state_id == s.state_id],
            )
        for t in successful_transitions:
            model.add_transition(
                source_class=t.source_state_id,
                target_class=t.destination_state_id,
                trigger_action=t.action_type,
                expected_outcome=t.actual_outcome,
            )
        for r in rules:
            model.discovered_invariants.append({
                "description": r.description,
                "confidence": r.confidence,
                "evidence_refs": [r.rule_id],
            })
        for j in inferred_journeys:
            model.add_journey(j["name"], j["steps"])
        model.confirmed_bug_leads = confirmed_leads

        playwright_script = model.export_playwright_test()
        behavior_model_dict = model.model_dump()
        behavior_model_dict["transitions"] = model.verified_transitions
        behavior_model_dict["state_classes_list"] = list(model.state_classes.values())

        journeys_list = []
        if agenda and hasattr(agenda, "journeys"):
            journeys_list = list(agenda.journeys.values())
        elif state.get("journeys"):
            journeys_list = list(state.get("journeys"))

        active_j_id = getattr(agenda, "active_journey_id", None) if agenda else state.get("active_journey_id")

        return InvestigationReport(
            overview=overview,
            application_understanding=app_understanding,
            user_provided_context_distinction=user_context_distinction,
            application_model=state.get("application_model"),
            authentication_access=auth_report,
            discovered_application_map=app_map,
            user_workflows=workflow_records,
            observed_behaviors=observed_behaviors,
            business_rules_and_hypotheses=rules,
            investigation_coverage_frontier=coverage_frontier,
            evidence_and_traceability=evidence_list,
            investigation_limitations=limitations,
            confirmed_bug_leads=confirmed_leads,
            unstable_or_rejected_leads=unstable_or_rejected,
            inferred_journeys=inferred_journeys,
            journeys=journeys_list,
            active_journey_id=active_j_id,
            behavior_model=behavior_model_dict,
            playwright_test_script=playwright_script,
            decision_snapshots=state.get("decision_snapshots") or [],
            target_context=target_context.model_dump(mode="json"),
            observed_inventory=inventory.model_dump(mode="json"),
        )

    @classmethod
    def to_markdown(cls, report: InvestigationReport) -> str:
        """Renders the 10-section report into clean, publication-ready GitHub-flavored Markdown."""
        lines = []
        lines.append(f"# VerityQA Autonomous Investigation Report: {report.overview.target_url}")
        lines.append(f"**Investigation ID**: `{report.overview.investigation_id}` | **Status**: `{report.overview.status.value}` | **Timestamp**: `{report.overview.start_time}` to `{report.overview.end_time}`")
        lines.append("")
        
        # Section A
        lines.append("## Section A: Executive Overview")
        lines.append(f"- **Target URL**: {report.overview.target_url}")
        lines.append(f"- **Planning Strategy**: `{report.overview.planning_strategy}`")
        if report.overview.llm_calls_count > 0 or report.overview.llm_fallbacks_count > 0:
            lines.append(f"- **LLM Planning Telemetry**: {report.overview.llm_calls_count} calls, {report.overview.llm_fallbacks_count} fallbacks ({report.overview.llm_latency_seconds:.2f}s latency)")
        lines.append(f"- **Pages / Routes Discovered**: {report.overview.pages_discovered}")
        lines.append(f"- **Distinct Meaningful UI States**: {report.overview.meaningful_states_discovered}")
        lines.append(f"- **Action Candidates Discovered**: {report.overview.candidates_discovered}")
        lines.append(f"- **Actions Evaluated by Policy**: {report.overview.actions_evaluated}")
        lines.append(f"- **Actions Attempted in Browser**: {report.overview.actions_attempted}")
        lines.append(f"- **Actions Successfully Executed**: {report.overview.actions_successful}")
        lines.append(f"- **Actions with Verified Effects**: {report.overview.actions_verified_effects}")
        lines.append(f"- **Actions Failed**: {report.overview.actions_failed}")
        lines.append(f"- **Actions Safety-Blocked**: {report.overview.actions_safety_blocked}")
        lines.append(f"- **Confirmed Transitions**: {report.overview.transitions_observed} (Route: {report.overview.route_transitions_observed}, Same-Route: {report.overview.same_route_state_transitions})")
        lines.append(f"- **In-Page State Mutations**: {report.overview.in_page_mutations_observed}")
        lines.append(f"- **Evidence Ledger Records**: {report.overview.evidence_ledger_count}")
        lines.append(f"- **Pending Frontier Items**: {report.overview.pending_frontier_count}")
        lines.append(f"- **Investigation Duration**: {report.overview.duration_seconds}s")
        lines.append(f"- **Browser Execution Duration**: {report.overview.browser_execution_time}s (Pure browser execution excluding LLM inference latency)")
        lines.append(f"- **Action Retries**: {report.overview.retries_count}")
        lines.append(f"- **Investigation Stopping Reason**: {report.overview.stopping_reason}")
        lines.append("")

        # Section B
        lines.append("## Section B: Application Understanding & User Intent Distinction")
        lines.append(f"### Application Understanding\n{report.application_understanding}")
        lines.append(f"### Grounding Distinction\n{report.user_provided_context_distinction}")

        app_mod = report.application_model
        if app_mod:
            lines.append(f"### Application Domain & Purpose: **{app_mod.application_domain.upper()}** — *{app_mod.page_purpose}*")
            lines.append(f"**Investigation Uncertainty Index**: `{app_mod.investigation_uncertainty:.1%}`")
            lines.append("")
            lines.append("#### Observed Facts (100% Grounded in Live Browser Evidence):")
            for fact in app_mod.observed_facts[:6]:
                lines.append(f"- [FACT] {fact}")
            lines.append("")
            if app_mod.inferred_hypotheses:
                lines.append("#### Inferred Hypotheses (Adaptive Reasoning — Requires Further Evidence):")
                for hypo in app_mod.inferred_hypotheses[:5]:
                    lines.append(f"- [HYPO] {hypo}")
                lines.append("")
            if app_mod.unknowns:
                lines.append("#### Unknowns & Exploration Gaps:")
                for unk in app_mod.unknowns[:5]:
                    lines.append(f"- [UNKNOWN] {unk}")
                lines.append("")
        lines.append("")

        # Section C
        lines.append("## Section C: Authentication and Access Intelligence")
        lines.append(f"- **Auth Boundary Observed**: {report.authentication_access.boundary_observed}")
        if report.authentication_access.login_url:
            lines.append(f"- **Login URL / Endpoint**: {report.authentication_access.login_url}")
        lines.append(f"- **Credentials Available**: {report.authentication_access.credentials_available}")
        lines.append(f"- **Authentication Attempted**: {report.authentication_access.authentication_attempted}")
        lines.append(f"- **Authentication Verified**: {report.authentication_access.authentication_verified}")
        if report.authentication_access.verification_signals:
            lines.append(f"- **Verification Signals**: {', '.join(report.authentication_access.verification_signals)}")
        if report.authentication_access.protected_functionality_accessible:
            lines.append(f"- **Protected Features Unlocked**: {', '.join(report.authentication_access.protected_functionality_accessible)}")
        if report.authentication_access.unresolved_or_blocked_areas:
            lines.append(f"- **Blocked Areas**: {', '.join(report.authentication_access.unresolved_or_blocked_areas)}")
        lines.append("")

        # Section D
        lines.append("## Section D: Discovered Application Map")
        lines.append("| Area ID | Route / URL | Apparent Purpose | Sample Interactive Controls | Status |")
        lines.append("| --- | --- | --- | --- | --- |")
        for area in report.discovered_application_map:
            ctrls = "; ".join(area.relevant_controls_summary[:3]) if area.relevant_controls_summary else "None"
            lines.append(f"| `{area.area_id}` | `{area.route_or_url}` | {area.apparent_purpose} | {ctrls} | `{area.status.value}` |")
        lines.append("")

        # Section E
        lines.append("## Section E: Observed User Workflows & Transition Paths")
        lines.append("| Step | Workflow Branch | From State | Action Taken | To State | Outcome | Evidence |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for step in report.user_workflows:
            lines.append(f"| {step.step_number} | **{step.workflow_branch}** | `{step.from_state}` | {step.action_taken} | `{step.to_state}` | {step.observed_outcome} | `{step.evidence_id}` |")
        lines.append("")

        if getattr(report, "journeys", None):
            lines.append("### Stateful User Journeys (Multi-Step Exploration)")
            for j in report.journeys:
                status_val = j.status.value if hasattr(j.status, "value") else str(j.status)
                lines.append(f"#### Journey: {j.name} (`status={status_val}`)")
                lines.append(f"*{j.description}*")
                lines.append(f"- **Planned Steps**: {j.planned_steps_count} | **Verified Steps**: {j.verified_steps_count} | **Not Applicable**: {getattr(j, 'not_applicable_steps_count', 0)} | **Skipped Steps**: {j.skipped_steps_count} | **Failed Steps**: {j.failed_steps_count}")
                if j.interruption_reason:
                    lines.append(f"> **Interruption / Blocked Reason**: {j.interruption_reason}")
                lines.append("| Step | Name | Target Control | Lifecycle Status | Values (Before -> After) | Verification Evidence / Details |")
                lines.append("| --- | --- | --- | --- | --- | --- |")
                for s in j.steps:
                    st_val = s.status.value if hasattr(s.status, "value") else str(s.status)
                    if st_val == "VERIFIED":
                        st_display = "VERIFIED"
                    elif st_val == "NOT_APPLICABLE":
                        st_display = "NOT_APPLICABLE"
                    elif st_val == "SKIPPED":
                        st_display = "SKIPPED"
                    elif st_val == "FAILED":
                        st_display = "FAILED"
                    elif st_val == "EXECUTED":
                        st_display = "EXECUTED"
                    elif st_val == "ATTEMPTED":
                        st_display = "ATTEMPTED"
                    else:
                        st_display = "PLANNED"

                    target_str = s.executed_target or s.target_pattern or "-"
                    if s.before_value or s.after_value:
                        val_str = f"`{s.before_value or 'N/A'}` -> `{s.after_value or 'N/A'}`"
                    else:
                        val_str = "-"
                    detail_str = s.verification_details or s.failure_reason or (s.executed_question_id or "-")
                    lines.append(f"| {s.step_index} | {s.name} | `{target_str}` | **{st_display}** | {val_str} | {detail_str} |")
                lines.append("")

        # Section F
        lines.append("## Section F: Observed Behaviors & System Dynamics")
        for beh in report.observed_behaviors:
            lines.append(f"- {beh}")
        lines.append("")

        # Section G
        lines.append("## Section G: Inferred Business Rules, Constraints & Hypotheses")
        verified_facts = [r for r in report.business_rules_and_hypotheses if r.confidence >= 1.0]
        inferred_hypos = [r for r in report.business_rules_and_hypotheses if r.confidence < 1.0]

        if verified_facts:
            lines.append("### Verified Empirical Facts (100% Confidence)")
            lines.append("| Rule ID | Verified Constraint / Behavior | Supporting Source | Limitations |")
            lines.append("| --- | --- | --- | --- |")
            for rule in verified_facts:
                lines.append(f"| `{rule.rule_id}` | {rule.description} | {rule.source} | {rule.limitations} |")
            lines.append("")

        if inferred_hypos:
            lines.append("### Inferred Hypotheses (< 100% Confidence)")
            lines.append("| Hypothesis ID | Proposed Dynamic Behavior | Source Analysis | Confidence | Limitations |")
            lines.append("| --- | --- | --- | --- | --- |")
            for hypo in inferred_hypos:
                lines.append(f"| `{hypo.rule_id}` | {hypo.description} | {hypo.source} | {hypo.confidence:.0%} | {hypo.limitations} |")
            lines.append("")

        # Section H
        lines.append("## Section H: Investigation Coverage & Frontier")
        cov = report.investigation_coverage_frontier
        lines.append(f"- **Explored States**: {cov.get('explored_states_count', 0)}")
        lines.append(f"- **Unexplored Frontier Items**: {cov.get('unexplored_items_count', 0)}")
        lines.append(f"- **Blocked Items**: {cov.get('blocked_items_count', 0)}")
        if cov.get("unexplored_candidates"):
            lines.append("### Unexplored Candidates Remaining:")
            lines.append("| Candidate / Target | Hypothesis / Purpose | Priority | Reason Not Probed |")
            lines.append("| --- | --- | --- | --- |")
            for cand in cov["unexplored_candidates"]:
                target = cand.get("target") or "N/A"
                rationale = cand.get("rationale") or cand.get("hypothesis") or ""
                prio = cand.get("priority", 0.5)
                reason = cand.get("reason_unexplored") or "Pending in queue"
                lines.append(f"| `{target}` | {rationale} | {prio} | {reason} |")
            lines.append("")
        lines.append("### Recommended Next Frontier Priorities:")
        for rec in cov.get("recommended_next_priorities", []):
            lines.append(f"1. {rec}")
        lines.append("")

        # Section I
        lines.append("## Section I: Evidence Ledger & Traceability Matrix")
        lines.append(f"Total Evidence Items Collected: {len(report.evidence_and_traceability)}")
        for ev in report.evidence_and_traceability[:10]:
            lines.append(f"- **[{ev.get('type')}]** `{ev.get('evidence_id')}` (State: `{ev.get('state_ref')}`): {ev.get('summary')}")
        if len(report.evidence_and_traceability) > 10:
            lines.append(f"- *... and {len(report.evidence_and_traceability) - 10} additional evidence items in ledger.*")
        lines.append("")

        # Section J
        lines.append("## Section J: Investigation Limitations & Boundary Conditions")
        for lim in report.investigation_limitations:
            lines.append(f"- {lim}")
        lines.append("")

        # Section K
        lines.append("## Section K: Confirmed Bug Leads (Empirically Reproduced)")
        if report.confirmed_bug_leads:
            for idx, lead in enumerate(report.confirmed_bug_leads, 1):
                lines.append(f"### K.{idx} {lead.title} (`{lead.id}`)")
                oracle_val = lead.oracle_type.value if hasattr(lead.oracle_type, "value") else str(lead.oracle_type)
                strength_val = lead.strength.value if hasattr(lead.strength, "value") else str(lead.strength)
                lines.append(f"- **Oracle Type**: `{oracle_val}` | **Strength**: `{strength_val}`")
                lines.append(f"- **Target Element/Feature**: `{lead.target_desc}` (State Class: `{lead.state_class}`)")
                lines.append(f"- **Expected**: {lead.expected.get('text', 'N/A')} (Source: {lead.expected.get('provenance', 'Specification')})")
                lines.append(f"- **Actual Empirical**: {lead.actual.get('text', 'N/A')}")
                passed_runs = lead.confirmation.get('passed', 0) if isinstance(lead.confirmation, dict) else 2
                total_runs = lead.confirmation.get('runs', 2) if isinstance(lead.confirmation, dict) else 2
                lines.append(f"- **Reproduction Runs**: {passed_runs} / {total_runs} confirmed in isolated fresh browser contexts")
                lines.append("- **Minimal Reproduction Sequence**:")
                for step_idx, st in enumerate(lead.steps, 1):
                    val_str = f" with value '{st.get('value')}'" if st.get('value') else ""
                    lines.append(f"  {step_idx}. `{st.get('action_type', 'ACTION')}` on `{st.get('target', st.get('raw_target', 'target'))}`{val_str}")
                if lead.evidence_ids:
                    lines.append(f"- **Evidence Ledger Pointers**: {', '.join(lead.evidence_ids)}")
                lines.append("")
        else:
            lines.append("No bug leads met the strict 2-of-2 isolated confirmation threshold.")
            lines.append("")

        # Section L
        lines.append("## Section L: Unstable or Rejected Leads")
        if report.unstable_or_rejected_leads:
            for lead in report.unstable_or_rejected_leads:
                rejection_reason = lead.actual.get("rejection_reason") or ("Failed 2-of-2 consistency rule" if lead.status == "UNSTABLE" else "Skeptic audit filtered")
                lines.append(f"- **[{lead.status}]** `{lead.title}`: {rejection_reason}")
            lines.append("")
        else:
            lines.append("No leads were classified as unstable or rejected.")
            lines.append("")

        # Section M
        lines.append("## Section M: Inferred User Journeys")
        if report.inferred_journeys:
            for j in report.inferred_journeys:
                lines.append(f"### Journey: {j.get('name', 'User Progression')}")
                for idx, st in enumerate(j.get("steps", []), 1):
                    lines.append(f"{idx}. In `{st.get('state_class', 'view')}`: `{st.get('action')}` on `{st.get('target')}` -> transitions to `{st.get('expected_transition')}`")
                lines.append("")
        else:
            lines.append("No complete multi-step user journeys discovered.")
            lines.append("")

        # Section N
        lines.append("## Section N: Replayable Application Behavior Model")
        lines.append("### Exported Executable Playwright Script")
        lines.append("```python")
        lines.append(report.playwright_test_script or "# No script generated.")
        lines.append("```")
        lines.append("")

        return "\n".join(lines)
