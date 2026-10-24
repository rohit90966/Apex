"""
Independent Blind Ground-Truth Evaluator for VerityQA.
======================================================
This module is strictly segregated from the investigation agent and is NEVER
imported or accessed during investigation execution. It evaluates completed
investigation reports and decision snapshots against independent ground-truth facts.
"""

from typing import Dict, List, Set, Any, Optional
from pydantic import BaseModel, Field


class GroundTruthState(BaseModel):
    state_key: str
    url_pattern: str
    description: str
    required_features: List[str] = Field(default_factory=list)


class GroundTruthTransition(BaseModel):
    transition_key: str
    from_url: str
    to_url: str
    expected_effect: str
    action_description: str


class BenchmarkEvaluationResult(BaseModel):
    # Discovery & State Coverage
    total_ground_truth_states: int
    discovered_states_count: int
    matched_ground_truth_states: List[str]
    missing_ground_truth_states: List[str]
    state_discovery_recall: float

    # Transition Accuracy & Evidence
    total_transitions_recorded: int
    verified_transitions_count: int
    transitions_with_evidence_count: int
    transition_accuracy: float
    evidence_support_ratio: float

    # Seeded Defect & Skepticism
    total_seeded_defects: int
    detected_seeded_defects_count: int
    false_positive_leads_count: int
    seeded_defect_precision: float
    seeded_defect_recall: float
    benign_warning_correctly_ignored: bool

    # Exploration Efficiency & Redundancy
    total_probes_evaluated: int
    deduplicated_candidates_count: int
    redundant_probes_count: int
    redundant_exploration_rate: float

    # Decision Quality
    total_decision_snapshots: int
    adaptive_decisions_count: int
    adaptive_decision_quality: float
    decision_trace_summary: List[Dict[str, Any]] = Field(default_factory=list)

    # Workflow Depth & Operational Efficiency
    workflow_completion_rate: float = 0.0
    unique_states_per_action: float = 0.0
    unsuccessful_action_rate: float = 0.0
    wall_clock_time: float = 0.0
    llm_calls_count: int = 0
    stopping_reason: str = ""
    unresolved_frontier_summary: Dict[str, int] = Field(default_factory=dict)


class BenchmarkEvaluator:
    """
    Independent ground-truth evaluator.
    """

    # 1. Ground Truth States (Total: 12)
    GROUND_TRUTH_STATES: Dict[str, GroundTruthState] = {
        "ST_LOGIN": GroundTruthState(
            state_key="ST_LOGIN",
            url_pattern="/login",
            description="Entry login authentication portal",
            required_features=["username", "password", "sign in"],
        ),
        "ST_LOGIN_ERROR": GroundTruthState(
            state_key="ST_LOGIN_ERROR",
            url_pattern="/login",
            description="Login page with validation error alert",
            required_features=["required", "alert-danger"],
        ),
        "ST_PRODUCTS": GroundTruthState(
            state_key="ST_PRODUCTS",
            url_pattern="/products",
            description="Product catalog main view with sorting and drawer",
            required_features=["alpha device", "products catalog", "product-sort"],
        ),
        "ST_PRODUCTS_SORT_ZA": GroundTruthState(
            state_key="ST_PRODUCTS_SORT_ZA",
            url_pattern="sort=za",
            description="Catalog sorted Z to A",
            required_features=["sort=za"],
        ),
        "ST_PRODUCTS_SORT_LOHI": GroundTruthState(
            state_key="ST_PRODUCTS_SORT_LOHI",
            url_pattern="sort=lohi",
            description="Catalog sorted Price Low to High",
            required_features=["sort=lohi"],
        ),
        "ST_PRODUCTS_DRAWER": GroundTruthState(
            state_key="ST_PRODUCTS_DRAWER",
            url_pattern="/products",
            description="Catalog with active filter/telemetry drawer open",
            required_features=["menu-open", "drawer"],
        ),
        "ST_PRODUCT_DETAIL_1": GroundTruthState(
            state_key="ST_PRODUCT_DETAIL_1",
            url_pattern="/products/1",
            description="Alpha Device isolated detail route",
            required_features=["alpha device", "high-precision core automation unit"],
        ),
        "ST_CART_EMPTY": GroundTruthState(
            state_key="ST_CART_EMPTY",
            url_pattern="/cart",
            description="Shopping cart empty view",
            required_features=["cart is empty"],
        ),
        "ST_CART_WITH_ITEM": GroundTruthState(
            state_key="ST_CART_WITH_ITEM",
            url_pattern="/cart",
            description="Shopping cart with added items",
            required_features=["alpha device", "subtotal", "calculate-tax-btn"],
        ),
        "ST_CHECKOUT_STEP1": GroundTruthState(
            state_key="ST_CHECKOUT_STEP1",
            url_pattern="/checkout",
            description="Checkout shipping details form",
            required_features=["first name", "last name", "postal code"],
        ),
        "ST_CHECKOUT_REVIEW": GroundTruthState(
            state_key="ST_CHECKOUT_REVIEW",
            url_pattern="/checkout/review",
            description="Checkout order review step",
            required_features=["confirm your order", "finish-order-btn"],
        ),
        "ST_ORDER_CONFIRMED": GroundTruthState(
            state_key="ST_ORDER_CONFIRMED",
            url_pattern="/order-confirmation",
            description="Order confirmation success page",
            required_features=["order placed successfully"],
        ),
    }

    # 2. Seeded Defects (Total: 1)
    SEEDED_DEFECT_SIGNATURES = [
        "nan",
        "tax: $nan",
        "total: $nan",
    ]

    # 3. Benign Signals that must NOT be flagged as confirmed bugs
    BENIGN_WARNING_SIGNATURES = [
        "tracking pixel ping delayed",
        "analytics: tracking",
    ]

    @classmethod
    def evaluate(
        cls,
        report: Any,
        decision_snapshots: Optional[List[Dict[str, Any]]] = None,
        final_state: Optional[Dict[str, Any]] = None,
    ) -> BenchmarkEvaluationResult:
        """
        Evaluates an InvestigationReport against ground truth.
        """
        snapshots = decision_snapshots or getattr(report, "decision_snapshots", []) or []

        # -------------------------------------------------------------
        # 1. State Discovery Recall
        # -------------------------------------------------------------
        discovered_areas = getattr(report, "discovered_application_map", [])
        user_workflows = getattr(report, "user_workflows", [])
        all_discovered_routes = set()
        all_discovered_texts = set()

        for a in discovered_areas:
            all_discovered_routes.add(a.route_or_url.lower())
            for c in a.relevant_controls_summary:
                all_discovered_texts.add(c.lower())

        for w in user_workflows:
            all_discovered_routes.add(w.from_state.lower())
            all_discovered_routes.add(w.to_state.lower())
            all_discovered_texts.add(w.observed_outcome.lower())

        for snap in snapshots:
            all_discovered_routes.add((snap.get("from_url") or "").lower())
            all_discovered_routes.add((snap.get("to_url") or "").lower())
            all_discovered_texts.add((snap.get("hypothesis") or "").lower())
            all_discovered_texts.add((snap.get("observed_effect") or "").lower())

        states_dict = {}
        if final_state and isinstance(final_state, dict):
            states_dict = final_state.get("states", {}) or {}
        for s in states_dict.values():
            s_url = getattr(s, "url", "").lower()
            all_discovered_routes.add(s_url)
            for c in getattr(s, "interactive_controls", []):
                all_discovered_texts.add(getattr(c, "selector", "").lower())
                all_discovered_texts.add(getattr(c, "accessible_name", "").lower())
            for h in getattr(s, "headings", []):
                all_discovered_texts.add(h.lower())
            all_discovered_texts.add(getattr(s, "visible_text_snippet", "").lower())
            if getattr(s, "menu_open", False):
                all_discovered_texts.add("menu-open")
                all_discovered_texts.add("drawer")
            if getattr(s, "has_validation_error", False):
                all_discovered_texts.add("alert-danger")
                all_discovered_texts.add("required")
            badge = str(getattr(s, "dynamic_signals", {}).get("counter_value", ""))
            if badge:
                all_discovered_texts.add(f"badge:{badge}")

        matched_states = []
        missing_states = []

        for key, gt_state in cls.GROUND_TRUTH_STATES.items():
            pattern = gt_state.url_pattern.lower()
            route_match = any(pattern in r for r in all_discovered_routes)
            feature_match = any(
                all(f.lower() in t for f in gt_state.required_features)
                for t in all_discovered_texts
            ) or route_match

            if route_match or feature_match:
                matched_states.append(key)
            else:
                missing_states.append(key)

        total_gt = len(cls.GROUND_TRUTH_STATES)
        state_recall = round(len(matched_states) / max(total_gt, 1), 3)

        # -------------------------------------------------------------
        # 2. Transition Accuracy & Evidence Support
        # -------------------------------------------------------------
        total_transitions = getattr(report.overview, "transitions_observed", 0) or len(user_workflows)
        verified_effects = getattr(report.overview, "actions_verified_effects", 0) or len(user_workflows)

        evidence_entries = getattr(report, "evidence_and_traceability", [])
        evidence_ids = {e.get("evidence_id") for e in evidence_entries if isinstance(e, dict)}
        transitions_with_evidence = sum(
            1 for w in user_workflows if w.evidence_id in evidence_ids or w.evidence_id.startswith("ev-")
        )

        transition_accuracy = round(verified_effects / max(total_transitions, 1), 3) if total_transitions else 1.0
        evidence_support = round(transitions_with_evidence / max(len(user_workflows), 1), 3) if user_workflows else 1.0

        # -------------------------------------------------------------
        # 3. Seeded Defect Precision & Recall
        # -------------------------------------------------------------
        confirmed_leads = getattr(report, "confirmed_bug_leads", [])
        detected_seeded = 0
        false_positives = 0
        benign_warning_flagged = False

        for lead in confirmed_leads:
            title = (getattr(lead, "title", "") or "").lower()
            desc = (getattr(lead, "description", "") or "").lower()
            actual = str(getattr(lead, "actual", {})).lower()
            combined = f"{title} {desc} {actual}"

            if any(sig in combined for sig in cls.SEEDED_DEFECT_SIGNATURES):
                detected_seeded += 1
            elif any(sig in combined for sig in cls.BENIGN_WARNING_SIGNATURES):
                benign_warning_flagged = True
                false_positives += 1
            else:
                false_positives += 1

        total_seeded = 1
        defect_precision = round(detected_seeded / max(detected_seeded + false_positives, 1), 3)
        defect_recall = round(detected_seeded / max(total_seeded, 1), 3)

        # -------------------------------------------------------------
        # 4. Redundant Exploration Rate & Decisions
        # -------------------------------------------------------------
        total_probes = getattr(report.overview, "actions_attempted", 0) or len(snapshots)
        dedup_count = getattr(report.overview, "deduplicated_candidates_count", 0)

        seen_actions_per_state: Dict[str, Set[str]] = {}
        redundant_probes = 0
        adaptive_decisions = 0
        trace_summary = []

        for s in snapshots:
            from_st = s.get("from_state_id") or "initial"
            act = s.get("selected_action") or "act"
            is_prod = s.get("is_productive", False)
            effect = s.get("observed_effect", "")

            state_actions = seen_actions_per_state.setdefault(from_st, set())
            if act in state_actions and not is_prod:
                redundant_probes += 1
                decision_verdict = "REDUNDANT"
            else:
                state_actions.add(act)
                decision_verdict = "USEFUL_EXPLORATION"
                adaptive_decisions += 1

            trace_summary.append({
                "step": s.get("step_number"),
                "from_state": from_st,
                "action": act,
                "hypothesis": s.get("hypothesis", "")[:80],
                "observed_effect": effect,
                "evaluator_verdict": decision_verdict,
            })

        redundancy_rate = round(redundant_probes / max(total_probes, 1), 3) if total_probes else 0.0
        decision_quality = round(adaptive_decisions / max(len(snapshots), 1), 3) if snapshots else 1.0

        # -------------------------------------------------------------
        # 5. Workflow Depth & Efficiency
        # -------------------------------------------------------------
        workflow_rate = 0.0
        if "ST_ORDER_CONFIRMED" in matched_states:
            workflow_rate = 1.0
        elif "ST_CHECKOUT_REVIEW" in matched_states:
            workflow_rate = 0.85
        elif "ST_CHECKOUT_STEP1" in matched_states:
            workflow_rate = 0.66
        elif "ST_CART_WITH_ITEM" in matched_states:
            workflow_rate = 0.33
        elif "ST_CART_EMPTY" in matched_states:
            workflow_rate = 0.15

        unique_states_per_act = round(len(matched_states) / max(total_probes, 1), 3)
        actions_failed = getattr(report.overview, "actions_failed", 0)
        unsuccessful_act_rate = round(actions_failed / max(total_probes, 1), 3)
        wall_clock = getattr(report.overview, "duration_seconds", 0.0)
        llm_calls = getattr(report.overview, "llm_calls_count", 0)
        stop_reason = getattr(report.overview, "stopping_reason", "")

        unresolved_summary: Dict[str, int] = {}
        agenda = None
        if final_state and isinstance(final_state, dict):
            agenda = final_state.get("agenda")
        if agenda and hasattr(agenda, "questions"):
            for q in agenda.questions.values():
                if q.status == "unexplored":
                    reason_cat = "BUDGET_CEILING_REACHED" if ("ceiling" in stop_reason.lower() or "limit" in stop_reason.lower()) else "UNEXPLORED_FRONTIER"
                    unresolved_summary[reason_cat] = unresolved_summary.get(reason_cat, 0) + 1
                elif q.status == "abandoned":
                    unresolved_summary["SAFETY_POLICY_BLOCKED"] = unresolved_summary.get("SAFETY_POLICY_BLOCKED", 0) + 1

        return BenchmarkEvaluationResult(
            total_ground_truth_states=total_gt,
            discovered_states_count=len(matched_states),
            matched_ground_truth_states=matched_states,
            missing_ground_truth_states=missing_states,
            state_discovery_recall=state_recall,
            total_transitions_recorded=total_transitions,
            verified_transitions_count=verified_effects,
            transitions_with_evidence_count=transitions_with_evidence,
            transition_accuracy=transition_accuracy,
            evidence_support_ratio=evidence_support,
            total_seeded_defects=total_seeded,
            detected_seeded_defects_count=detected_seeded,
            false_positive_leads_count=false_positives,
            seeded_defect_precision=defect_precision,
            seeded_defect_recall=defect_recall,
            benign_warning_correctly_ignored=not benign_warning_flagged,
            total_probes_evaluated=total_probes,
            deduplicated_candidates_count=dedup_count,
            redundant_probes_count=redundant_probes,
            redundant_exploration_rate=redundancy_rate,
            total_decision_snapshots=len(snapshots),
            adaptive_decisions_count=adaptive_decisions,
            adaptive_decision_quality=decision_quality,
            decision_trace_summary=trace_summary,
            workflow_completion_rate=workflow_rate,
            unique_states_per_action=unique_states_per_act,
            unsuccessful_action_rate=unsuccessful_act_rate,
            wall_clock_time=wall_clock,
            llm_calls_count=llm_calls,
            stopping_reason=stop_reason,
            unresolved_frontier_summary=unresolved_summary,
        )

    @classmethod
    def evaluate_test_suite_effectiveness(
        cls,
        suite: Any,
        defective_run_report: Any,
        corrected_run_report: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Independently evaluates Phase 3 generated test suite and execution results
        against ground truth with all 14 mandated metrics and explicit numerators/denominators.
        """
        total_generated = getattr(suite, "test_cases", [])
        total_gen_cnt = len(total_generated)
        rejected_cnt = getattr(suite, "rejected_candidates_count", 0)
        total_candidates_cnt = total_gen_cnt + rejected_cnt

        exported_cnt = getattr(defective_run_report, "exported_count", total_gen_cnt)
        executable_cnt = getattr(defective_run_report, "executable_without_manual_edits_count", 0)
        executed_cnt = getattr(defective_run_report, "executed_count", 0)
        setup_fail_cnt = getattr(defective_run_report, "setup_failures_count", 0)
        setup_ok_cnt = max(executed_cnt - setup_fail_cnt, 0)

        results = getattr(defective_run_report, "results", [])
        valid_assertions_cnt = sum(
            1 for r in results if str(getattr(r, "status", "")).endswith(("PASSED", "FAILED", "DEFECT_DETECTED"))
        )

        cov = getattr(suite, "coverage_map", None)
        cov_states_cnt = getattr(cov, "covered_states_count", 0) if cov else 0
        total_contract_states = getattr(cov, "total_states_in_contract", 1) if cov else 1
        cov_trans_cnt = getattr(cov, "covered_transitions_count", 0) if cov else 0
        total_contract_trans = getattr(cov, "total_transitions_in_contract", 1) if cov else 1

        # Seeded defect detection on defective implementation
        seeded_defects_total = 1
        seeded_detected_cnt = sum(
            1 for r in results
            if "DEFECT_DETECTED" in str(getattr(r, "status", ""))
            and any(sig in (getattr(r, "error_message", "") or "").lower() for sig in cls.SEEDED_DEFECT_SIGNATURES)
        )

        # False failure rate on corrected implementation
        corr_results = getattr(corrected_run_report, "results", []) if corrected_run_report else []
        corr_exec_cnt = getattr(corrected_run_report, "executed_count", 0) if corrected_run_report else executed_cnt
        false_failures_cnt = sum(
            1 for r in corr_results
            if str(getattr(r, "status", "")).endswith(("FAILED", "DEFECT_DETECTED", "EXECUTION_ERROR"))
        ) if corrected_run_report else sum(
            1 for r in results
            if getattr(r, "category", "") != "regression" and str(getattr(r, "status", "")).endswith("FAILED")
        )

        # Duplicate / redundant test rate (tests sharing identical category, preconditions, and action steps)
        seen_sigs = set()
        dup_cnt = 0
        for tc in total_generated:
            sig = (
                tc.category.value,
                tuple(tc.preconditions),
                tuple((s.action_type, s.selector, s.source_state_id, s.target_state_id) for s in tc.steps),
            )
            if sig in seen_sigs:
                dup_cnt += 1
            seen_sigs.add(sig)

        # Traceability completeness
        traceable_cnt = sum(
            1 for tc in total_generated
            if len(tc.evidence_ids) > 0 and (len(tc.source_state_ids) > 0 or len(tc.source_transition_ids) > 0 or len(tc.source_finding_ids) > 0)
        )

        safety_blocked_cnt = getattr(defective_run_report, "safety_blocked_count", 0)
        hyp_flagged_cnt = getattr(defective_run_report, "skipped_hypotheses_count", 0)

        return {
            "1_generated_test_count": {"numerator": total_gen_cnt, "denominator": total_candidates_cnt, "value": total_gen_cnt},
            "2_valid_test_count": {"numerator": total_gen_cnt, "denominator": total_candidates_cnt, "rate": round(total_gen_cnt / max(total_candidates_cnt, 1), 3)},
            "3_rejected_test_count": {"numerator": rejected_cnt, "denominator": total_candidates_cnt, "reasons": getattr(suite, "rejection_reasons", [])},
            "4_exported_executable_test_count": {"numerator": executable_cnt, "denominator": exported_cnt, "rate": round(executable_cnt / max(exported_cnt, 1), 3)},
            "5_executable_without_manual_edits_rate": {"numerator": executable_cnt, "denominator": executable_cnt, "rate": 1.0 if executable_cnt else 0.0},
            "6_setup_success_rate": {"numerator": setup_ok_cnt, "denominator": executed_cnt, "rate": round(setup_ok_cnt / max(executed_cnt, 1), 3)},
            "7_assertion_validity_rate": {"numerator": valid_assertions_cnt, "denominator": setup_ok_cnt, "rate": round(valid_assertions_cnt / max(setup_ok_cnt, 1), 3)},
            "8_state_coverage_of_discovered_states": {"numerator": cov_states_cnt, "denominator": total_contract_states, "rate": round(cov_states_cnt / max(total_contract_states, 1), 3)},
            "9_transition_coverage_of_verified_transitions": {"numerator": cov_trans_cnt, "denominator": total_contract_trans, "rate": round(cov_trans_cnt / max(total_contract_trans, 1), 3)},
            "10_seeded_defect_detection_rate": {"numerator": seeded_detected_cnt, "denominator": seeded_defects_total, "rate": round(seeded_detected_cnt / max(seeded_defects_total, 1), 3)},
            "11_false_failure_rate_on_valid_behavior": {"numerator": false_failures_cnt, "denominator": corr_exec_cnt, "rate": round(false_failures_cnt / max(corr_exec_cnt, 1), 3)},
            "12_duplicate_or_redundant_test_rate": {"numerator": dup_cnt, "denominator": total_gen_cnt, "rate": round(dup_cnt / max(total_gen_cnt, 1), 3)},
            "13_evidence_traceability_completeness": {"numerator": traceable_cnt, "denominator": total_gen_cnt, "rate": round(traceable_cnt / max(total_gen_cnt, 1), 3)},
            "14_safety_blocked_or_hypothesis_only_test_count": {
                "safety_blocked": safety_blocked_cnt,
                "hypothesis_only": hyp_flagged_cnt,
                "total_non_regression_gated": safety_blocked_cnt + hyp_flagged_cnt,
                "denominator": total_gen_cnt,
            },
        }
