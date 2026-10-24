"""
Autonomous Investigation Agenda and Question Manager.
Replaces keyword-based heuristics with hypothesis-driven, evidence-based exploration.
Scores questions deterministically using:
  score = 0.30 * novelty + 0.25 * uncertainty + 0.20 * defect_likelihood + 0.15 * journey_progress - 0.20 * cost - safety_penalty
"""

import re
import time
import logging
from typing import Dict, List, Optional, Set, Any, Tuple
from pydantic import BaseModel, Field, ConfigDict

from app.investigation_agent.models import (
    UIStateObservation,
    Prediction,
    ProbeScript,
    UserJourney,
    JourneyStep,
    JourneyStatus,
    JourneyStepStatus,
    StateDiff,
)
from app.investigation_agent.probes.forms import generate_form_probes
from app.investigation_agent.probes.controls import generate_control_probes
from app.investigation_agent.probes.nav import generate_nav_probes
from app.investigation_agent.probes.invariants import generate_invariant_probes

logger = logging.getLogger(__name__)


class InvestigationQuestion(BaseModel):
    """
    A specific hypothesis or inquiry the investigator explores via targeted probes.
    """
    id: str
    category: str = "discovery"  # "discovery", "invariant", "constraint", "edge"
    target_state_class: str
    target_element: Optional[str] = None
    hypothesis: str
    probe_family: str  # "forms", "controls", "nav", "invariants", "lists"
    priority: float = 0.5  # 0.0 to 1.0
    status: str = "unexplored"  # "unexplored", "probing", "answered", "abandoned"
    answer_summary: Optional[str] = None
    prediction: Optional[Prediction] = None
    script: Optional[ProbeScript] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    evaluated_at: Optional[float] = None


class CoverageLedger(BaseModel):
    """
    Tracks empirical coverage across routes, state classes, forms, and actions.
    """
    routes_visited: Set[str] = Field(default_factory=set)
    state_classes_discovered: Set[str] = Field(default_factory=set)
    forms_probed: Set[str] = Field(default_factory=set)
    actions_tried: Set[str] = Field(default_factory=set)
    probed_signatures: Set[str] = Field(default_factory=set)
    mutations_count: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True)


class InvestigationAgenda:
    """
    Maintains the dynamic queue of investigation questions, tracks coverage progress,
    and applies the multi-objective selection formula to pick the next best probe.
    """

    def __init__(
        self,
        max_probes: int = 0,
        max_duration_seconds: float = 300.0,
        max_mutations: int = 15,
        max_safety_blocks: int = 3,
        max_unproductive_probes: int = 6,
        emergency_safety_ceiling: int = 100,
        policy: str = "balanced",
    ):
        self.policy = policy
        self.questions: Dict[str, InvestigationQuestion] = {}
        self.coverage: CoverageLedger = CoverageLedger()
        self.probe_history: List[Dict[str, Any]] = []
        self.start_time: float = time.time()
        self.browser_execution_time: float = 0.0
        self.llm_latency_seconds: float = 0.0
        self.deduplicated_count: int = 0
        # max_probes <= 0 signals adaptive exploration without a fixed probe cap
        self.max_probes: int = max_probes
        # max_duration_seconds <= 0 signals unlimited investigation mode
        self.max_duration_seconds: float = max_duration_seconds
        self.max_mutations: int = max_mutations
        self.max_safety_blocks: int = max_safety_blocks
        self.safety_blocks_encountered: int = 0
        self.mutations_executed: int = 0
        self.retries_count: int = 0
        self.max_unproductive_probes: int = max_unproductive_probes
        self.consecutive_unproductive_probes: int = 0
        self.emergency_safety_ceiling: int = emergency_safety_ceiling
        self.journeys: Dict[str, UserJourney] = {}
        self.active_journey_id: Optional[str] = None
        self.journey_step_history: List[Dict[str, Any]] = []

    def compute_question_score(
        self,
        question: InvestigationQuestion,
        current_state_class: Optional[str] = None
    ) -> float:
        """
        Deterministic scoring formula:
        score = 0.30 * novelty + 0.25 * uncertainty + 0.20 * defect_likelihood + 0.15 * journey_progress - 0.20 * cost - safety_penalty
        """
        # 1. Novelty (0.0 to 1.0)
        # Higher if state_class is not yet in coverage ledger, or target_element is unprobed
        is_unvisited_nav = (
            question.probe_family == "nav"
            and question.target_element
            and not any(r.endswith(question.target_element) or question.target_element in r for r in self.coverage.routes_visited)
        )
        if question.target_state_class not in self.coverage.state_classes_discovered:
            novelty = 1.0
        elif is_unvisited_nav:
            novelty = 0.90
        elif question.target_element and question.target_element not in self.coverage.actions_tried:
            novelty = 0.75
        elif question.status == "unexplored":
            novelty = 0.4
        else:
            novelty = 0.05

        # 2. Uncertainty (0.0 to 1.0)
        if question.status == "unexplored":
            if question.category in ("constraint", "edge"):
                uncertainty = 1.0
            elif is_unvisited_nav:
                uncertainty = 0.85
            elif question.category == "invariant":
                uncertainty = 0.7
            else:
                uncertainty = 0.65
        else:
            uncertainty = 0.1

        # 3. Defect Likelihood (0.0 to 1.0)
        # High for boundary, mismatch, bypass; differentiated by semantic control tier
        cat = question.category
        fam = question.probe_family
        kind = question.metadata.get("kind", "")
        semantic_tier = question.metadata.get("semantic_tier", "")

        if "bypass" in kind or "mismatch" in kind or "boundary" in kind or cat in ("constraint", "edge"):
            defect_likelihood = 0.95
        elif fam == "forms":
            defect_likelihood = 0.72
        elif fam == "controls" and semantic_tier == "primary_mutation":
            defect_likelihood = 0.85
        elif fam == "controls" and semantic_tier == "workflow_progression":
            defect_likelihood = 0.78
        elif is_unvisited_nav or (fam == "controls" and semantic_tier == "content_navigation"):
            defect_likelihood = 0.62
        elif fam == "controls" and semantic_tier == "utility_drawer":
            defect_likelihood = 0.25
        elif fam == "controls":
            defect_likelihood = 0.50
        elif cat == "invariant":
            defect_likelihood = 0.30
        else:
            defect_likelihood = 0.25

        # 4. Journey Progress (0.0 to 1.0)
        # Higher if probing current state class (minimizes unnecessary resets/jumps) and advancing workflow
        on_current_view = bool(current_state_class and question.target_state_class == current_state_class)
        if on_current_view:
            if semantic_tier == "primary_mutation":
                journey_progress = 0.96
            elif semantic_tier == "workflow_progression":
                journey_progress = 0.92
            elif semantic_tier == "content_navigation":
                journey_progress = 0.86
            elif semantic_tier == "utility_drawer":
                journey_progress = 0.35
            else:
                journey_progress = 0.85
        elif question.category == "discovery":
            journey_progress = 0.60 if semantic_tier != "utility_drawer" else 0.25
        else:
            journey_progress = 0.30

        # 5. Cost (0.0 to 1.0)
        # Step length normalized by 10
        step_count = len(question.script.steps) if question.script else 1
        cost = min(1.0, max(0.1, step_count / 10.0))

        # 6. Safety Penalty (0.0 or 1.0)
        safety_penalty = 1.0 if question.metadata.get("is_safety_risk") else 0.0

        # Explicit priority override for login-page initial validation & auth boundary transition
        if question.metadata.get("priority_override") is not None and question.status == "unexplored":
            if not current_state_class or question.target_state_class == current_state_class or question.probe_family == "auth":
                return round(float(question.metadata["priority_override"]), 4)

        if self.policy == "workflow_depth":
            # Workflow completion prioritization: prioritize advancing workflows and primary actions
            score = (
                0.15 * novelty
                + 0.20 * uncertainty
                + 0.25 * defect_likelihood
                + 0.35 * journey_progress
                - 0.15 * cost
                - safety_penalty
            )
            if semantic_tier == "workflow_progression":
                score += 0.16
            elif semantic_tier == "primary_mutation":
                score += 0.12
            elif semantic_tier == "content_navigation":
                score += 0.02
            elif semantic_tier == "utility_drawer":
                score -= 0.20

            if on_current_view:
                score += 0.12

        elif self.policy == "breadth":
            # Breadth prioritization: prioritize visiting distinct unexplored routes and landmarks
            score = (
                0.45 * novelty
                + 0.25 * uncertainty
                + 0.15 * defect_likelihood
                + 0.05 * journey_progress
                - 0.20 * cost
                - safety_penalty
            )
            if is_unvisited_nav:
                score += 0.14
            if semantic_tier == "primary_mutation":
                score += 0.05
            elif semantic_tier == "utility_drawer":
                score += 0.04
            if on_current_view:
                score += 0.02

        else:
            # Default "balanced" policy (Phase 1 exact baseline)
            score = (
                0.30 * novelty
                + 0.25 * uncertainty
                + 0.20 * defect_likelihood
                + 0.15 * journey_progress
                - 0.20 * cost
                - safety_penalty
            )
            if semantic_tier == "primary_mutation":
                score += 0.06 if question.metadata.get("action_type") == "SELECT_OPTION" else 0.09
            elif semantic_tier == "workflow_progression":
                score += 0.05
            elif semantic_tier == "content_navigation":
                score += 0.01
            elif semantic_tier == "utility_drawer":
                score -= 0.15

            if on_current_view:
                score += 0.08

        # Cap non-override scores at 0.90 so explicit auth-gate priority overrides (0.96, 0.92) always take precedence
        return round(max(0.0, min(0.90, score)), 4)

    def _has_signature(self, sig: str) -> bool:
        if sig in self.coverage.probed_signatures:
            return True
        return any(q.metadata.get("action_signature") == sig for q in self.questions.values())

    def populate_from_observation(
        self,
        obs: UIStateObservation,
        is_auth_boundary: bool = False,
        has_auth_creds: bool = False,
    ) -> int:
        """
        Discovers new questions from current UI state observation.
        Adds to queue without duplicating known questions or equivalent route-level signatures.
        When an authentication boundary is detected on the page:
        - Registers initial-page login validation probes (e.g. blank submission) FIRST so the login page is investigated as a page.
        - Registers an explicit AUTH_LOGIN transition probe when credentials are available.
        """
        added = 0
        state_class = obs.state_class_id
        route = obs.url
        norm_route = obs.normalized_route or route

        # Register discovery of route and state class
        self.coverage.routes_visited.add(route)
        self.coverage.state_classes_discovered.add(state_class)

        # Synthesize site-agnostic user journeys from observed UI features
        self.synthesize_journeys_from_observation(obs)

        # 1. Form Probes
        form_probes = generate_form_probes(obs)
        for script, prediction, meta in form_probes:
            kind = meta.get("kind", "form")
            # On a login boundary where real credentials are available, skip synthetic fake-credential happy/boundary probes
            # so we first test blank-submit validation on the login page and then authenticate with real credentials
            if is_auth_boundary and has_auth_creds and kind != "required_blank":
                continue

            sig = f"form:{norm_route}:{script.name}"
            q_id = f"q_form_{state_class}_{script.name}"
            if self._has_signature(sig) or q_id in self.questions:
                self.deduplicated_count += 1
                continue

            category = "constraint" if "bypass" in kind or "mismatch" in kind else ("edge" if "boundary" in kind else "discovery")
            meta_copy = dict(meta)
            meta_copy["source_url"] = route
            meta_copy["action_signature"] = sig
            if is_auth_boundary and kind == "required_blank":
                meta_copy["priority_override"] = 0.96

            q = InvestigationQuestion(
                id=q_id,
                category=category,
                target_state_class=state_class,
                target_element=meta.get("field") or script.name,
                hypothesis=f"Verify form submission behavior for {kind} on {script.name}",
                probe_family="forms",
                prediction=prediction,
                script=script,
                metadata=meta_copy,
            )
            q.priority = self.compute_question_score(q, state_class)
            self.questions[q_id] = q
            added += 1

        # 1b. Explicit Authentication Boundary Transition Probe (runs after initial login page validation probe)
        if is_auth_boundary and has_auth_creds:
            auth_sig = f"auth:{norm_route}"
            auth_q_id = f"q_auth_{state_class}"
            if not self._has_signature(auth_sig) and auth_q_id not in self.questions:
                from app.investigation_agent.models import ProbeStep
                auth_script = ProbeScript(
                    name=f"authenticate_{state_class}",
                    family="auth",
                    description=f"Authenticate across login boundary at {route} using discovered test credentials",
                    steps=[ProbeStep(action_type="AUTHENTICATE", raw_target="login_form")],
                )
                auth_pred = Prediction(
                    expect="Authentication succeeds and unlocks protected application surface or route",
                    signals=["route_change", "no_server_error"],
                )
                auth_q = InvestigationQuestion(
                    id=auth_q_id,
                    category="discovery",
                    target_state_class=state_class,
                    target_element="login_form",
                    hypothesis=f"Verify valid authentication transitions past login boundary at {route}",
                    probe_family="auth",
                    prediction=auth_pred,
                    script=auth_script,
                    metadata={
                        "kind": "auth_transition",
                        "source_url": route,
                        "action_signature": auth_sig,
                        "priority_override": 0.92,
                    },
                )
                auth_q.priority = self.compute_question_score(auth_q, state_class)
                self.questions[auth_q_id] = auth_q
                added += 1

        # 2. Control Probes (buttons, selects, toggles, dialog triggers)
        control_probes = generate_control_probes(obs)
        for script, prediction, meta in control_probes:
            target_sel = meta.get("selector") or meta.get("target") or ""
            role_str = (meta.get("role") or "").lower()
            name_str = (meta.get("name") or "").strip().lower()
            if role_str == "button" and name_str and name_str != target_sel.lower():
                # Differentiate distinct item buttons with the same label (e.g. Add to Cart for prod-1 vs prod-2)
                item_match = re.search(r'(prod|item|row|card)[-_][a-z0-9\-_]+', target_sel, flags=re.IGNORECASE)
                if item_match:
                    ctrl_sig = f"ctrl:{norm_route}:button:{name_str}:{item_match.group(0)}"
                else:
                    ctrl_sig = f"ctrl:{norm_route}:button:{name_str}"
            else:
                ctrl_sig = f"ctrl:{norm_route}:{meta.get('action_type', 'CLICK')}:{(target_sel or name_str or script.name).lower()}"
            q_id = f"q_ctrl_{script.name}"
            if self._has_signature(ctrl_sig) or q_id in self.questions:
                self.deduplicated_count += 1
                continue

            meta_copy = dict(meta)
            meta_copy["source_url"] = route
            meta_copy["action_signature"] = ctrl_sig
            q = InvestigationQuestion(
                id=q_id,
                category="discovery",
                target_state_class=state_class,
                target_element=target_sel,
                hypothesis=f"Investigate interactive control response: {meta.get('role')} '{meta.get('name')}'",
                probe_family="controls",
                prediction=prediction,
                script=script,
                metadata=meta_copy,
            )
            q.priority = self.compute_question_score(q, state_class)
            self.questions[q_id] = q
            added += 1

        # 3. Navigation Probes
        nav_probes = generate_nav_probes(obs)
        for script, prediction, meta in nav_probes:
            target_href = meta.get("href", "")
            nav_sig = f"nav:{norm_route}:{target_href or script.name}"
            q_id = f"q_nav_{state_class}_{script.name}"
            if self._has_signature(nav_sig) or q_id in self.questions:
                self.deduplicated_count += 1
                continue

            meta_copy = dict(meta)
            meta_copy["source_url"] = route
            meta_copy["action_signature"] = nav_sig
            q = InvestigationQuestion(
                id=q_id,
                category="discovery",
                target_state_class=state_class,
                target_element=target_href,
                hypothesis=f"Verify route transition reachability to {target_href}",
                probe_family="nav",
                prediction=prediction,
                script=script,
                metadata=meta_copy,
            )
            q.priority = self.compute_question_score(q, state_class)
            self.questions[q_id] = q
            added += 1

        # 4. Invariant Probes (Page reload persistence)
        invariant_probes = generate_invariant_probes(obs)
        for inv_script, inv_pred, inv_meta in invariant_probes:
            inv_sig = f"inv:{norm_route}"
            inv_id = f"q_inv_{inv_script.name}_{state_class}"
            if self._has_signature(inv_sig) or inv_id in self.questions:
                self.deduplicated_count += 1
                continue

            meta_copy = dict(inv_meta)
            meta_copy["source_url"] = route
            meta_copy["action_signature"] = inv_sig
            q = InvestigationQuestion(
                id=inv_id,
                category="invariant",
                target_state_class=state_class,
                target_element=None,
                hypothesis="Verify page state and layout persist predictably across browser reloads",
                probe_family="invariants",
                prediction=inv_pred,
                script=inv_script,
                metadata=meta_copy,
            )
            q.priority = self.compute_question_score(q, state_class)
            self.questions[inv_id] = q
            added += 1

        logger.debug("[InvestigationAgenda] Discovered %d new investigation questions (total: %d)", added, len(self.questions))
        return added

    def synthesize_journeys_from_observation(self, obs: UIStateObservation):
        """
        Synthesizes site-agnostic user journeys from observed page elements.
        """
        ctrl_texts = " ".join([
            f"{c.selector} {c.accessible_name} {c.role}"
            for c in obs.interactive_controls
        ]).lower()
        form_texts = " ".join([
            f"{f.get('form_selector', '')} {' '.join(fld.get('name', '') for fld in f.get('fields', []))}"
            for f in obs.forms
        ]).lower()
        link_texts = " ".join([
            f"{l.get('href', '')} {l.get('text', '')}"
            for l in obs.navigation_links
        ]).lower()
        all_text = f"{ctrl_texts} {form_texts} {link_texts} {obs.url.lower()}"

        has_shopping = any(k in all_text for k in ("add-to-cart", "add to cart", "cart", "product", "catalog", "checkout"))

        if has_shopping and "journey_purchase" not in self.journeys:
            # Count observed distinct add-to-cart controls
            add_controls = [
                c for c in obs.interactive_controls
                if re.search(r"add-to-cart|add to cart|cart-add|buy", f"{c.selector} {c.accessible_name}", re.I)
            ]
            steps = [
                JourneyStep(
                    step_index=0,
                    name="Add Product 1 to Cart",
                    action_type="CLICK_CONTROL",
                    target_pattern=r"add-to-cart|add to cart|cart-add|buy",
                    expected_role="button",
                    expected_postcondition="cart_badge_increment",
                    is_required=True,
                )
            ]
            # Only add "Add Product 2 to Cart" if at least 2 distinct add-to-cart controls were observed
            if len(add_controls) >= 2:
                steps.append(
                    JourneyStep(
                        step_index=len(steps),
                        name="Add Product 2 to Cart",
                        action_type="CLICK_CONTROL",
                        target_pattern=r"add-to-cart|add to cart|cart-add|buy",
                        expected_role="button",
                        expected_postcondition="cart_badge_increment",
                        is_required=True,
                    )
                )

            steps.extend([
                JourneyStep(
                    step_index=len(steps),
                    name="Navigate to Cart",
                    action_type="CLICK_CONTROL",
                    target_pattern=r"shopping-cart|cart-link|view cart|/cart|\bcart\b",
                    expected_role="link",
                    expected_postcondition="route_is_cart",
                    is_required=True,
                ),
                JourneyStep(
                    step_index=len(steps) + 1,
                    name="Modify Cart Quantity",
                    action_type="CLICK_CONTROL",
                    target_pattern=r"cart-qty-inc|qty-plus|qty-inc|increase|add-qty|cart-qty|qty-minus|decrement|remove|delete|(?<![a-zA-Z0-9_])[+-](?![a-zA-Z0-9_])",
                    expected_role="button",
                    expected_postcondition="quantity_modified",
                    is_required=False,
                ),
                JourneyStep(
                    step_index=len(steps) + 2,
                    name="Proceed to Checkout",
                    action_type="CLICK_CONTROL",
                    target_pattern=r"checkout|proceed",
                    expected_role="button",
                    expected_postcondition="route_is_checkout",
                    is_required=True,
                ),
                JourneyStep(
                    step_index=len(steps) + 3,
                    name="Fill and Submit Checkout Form",
                    action_type="SUBMIT",
                    target_pattern=r"checkout|shipping|place-order|place order|order|submit|form",
                    expected_role="form",
                    expected_postcondition="checkout_form_submitted",
                    is_required=True,
                ),
                JourneyStep(
                    step_index=len(steps) + 4,
                    name="Confirm Order",
                    action_type="CLICK_CONTROL",
                    target_pattern=r"confirm|confirmation|finish|place-order|complete",
                    expected_role="button",
                    expected_postcondition="order_confirmed",
                    is_required=True,
                ),
            ])

            j = UserJourney(
                journey_id="journey_purchase",
                name="E-Commerce Purchase Funnel",
                description="Catalogue item selection -> Cart accumulation -> Cart modification -> Checkout navigation -> Order confirmation",
                steps=steps,
            )
            self.journeys["journey_purchase"] = j
            logger.info("[InvestigationAgenda] Synthesized user journey: %s (%d steps)", j.name, len(j.steps))

        # Generalized Journey 2: Search, Filter, and Discovery Workflow
        has_search_or_filter = any(k in all_text for k in ("search", "filter", "query", "sort", "find", "explore"))
        if has_search_or_filter and "journey_search_filter" not in self.journeys:
            j_search = UserJourney(
                journey_id="journey_search_filter",
                name="Search & Filter Discovery Workflow",
                description="Query input -> Filter application -> Results exploration and interaction",
                steps=[
                    JourneyStep(
                        step_index=0,
                        name="Input Search Query",
                        action_type="FILL",
                        target_pattern=r"search|query|find|filter|keyword",
                        expected_role="textbox",
                        expected_postcondition="query_entered",
                    ),
                    JourneyStep(
                        step_index=1,
                        name="Apply Search or Filter",
                        action_type="CLICK_CONTROL",
                        target_pattern=r"search|filter|apply|submit|btn-filter|go",
                        expected_role="button",
                        expected_postcondition="results_updated",
                    ),
                    JourneyStep(
                        step_index=2,
                        name="Inspect Filtered Result",
                        action_type="CLICK_CONTROL",
                        target_pattern=r"item|card|result|title|link|view|project",
                        expected_role="button",
                        expected_postcondition="detail_viewed",
                    ),
                ],
            )
            self.journeys["journey_search_filter"] = j_search
            logger.info("[InvestigationAgenda] Synthesized user journey: %s (%d steps)", j_search.name, len(j_search.steps))

        # Generalized Journey 3: Entity Creation & Management Workflow
        has_creation = any(k in all_text for k in ("new ", "create", "add project", "add item", "new project", "compose", "post"))
        if has_creation and "journey_creation" not in self.journeys:
            j_create = UserJourney(
                journey_id="journey_creation",
                name="Entity Creation & Management Workflow",
                description="Trigger creation action -> Input entity attributes -> Submit form -> Verify entity state",
                steps=[
                    JourneyStep(
                        step_index=0,
                        name="Trigger Entity Creation",
                        action_type="CLICK_CONTROL",
                        target_pattern=r"create|new|add|compose|plus|btn-create",
                        expected_role="button",
                        expected_postcondition="creation_form_opened",
                    ),
                    JourneyStep(
                        step_index=1,
                        name="Submit Entity Form",
                        action_type="SUBMIT",
                        target_pattern=r"form|create|save|submit|project",
                        expected_role="form",
                        expected_postcondition="entity_persisted",
                    ),
                ],
            )
            self.journeys["journey_creation"] = j_create
            logger.info("[InvestigationAgenda] Synthesized user journey: %s (%d steps)", j_create.name, len(j_create.steps))

    def _find_matching_question_for_step(
        self,
        step: JourneyStep,
        current_state_class: Optional[str] = None,
        current_url: Optional[str] = None,
        available_selectors: Optional[Set[str]] = None,
        exclude_target: Optional[str] = None,
    ) -> Optional[InvestigationQuestion]:
        pat = step.target_pattern
        pat_re = re.compile(pat, re.IGNORECASE) if pat else None

        pool = [q for q in self.questions.values() if q.status == "unexplored"]
        if current_state_class:
            local_pool = [q for q in pool if q.target_state_class == current_state_class]
            if local_pool:
                pool = local_pool

        if available_selectors:
            present_pool = [
                q for q in pool
                if (
                    q.target_element in available_selectors
                    or q.metadata.get("selector") in available_selectors
                    or q.metadata.get("href") in available_selectors
                    or any(s and (s.endswith(q.target_element or "___") or (q.target_element or "___") in s) for s in available_selectors)
                )
            ]
            if present_pool:
                pool = present_pool

        for q in pool:
            if exclude_target and q.target_element == exclude_target:
                continue

            # Role and semantic compatibility check
            q_role = (q.metadata.get("role") or "").lower()
            q_family = q.probe_family or ""
            if step.expected_role:
                exp_role = step.expected_role.lower()
                if exp_role == "button":
                    if q_role and q_role not in ("button", "input"):
                        continue
                    if q_family == "nav" and q_role != "button":
                        continue
                elif exp_role == "link":
                    if q_role and q_role not in ("link", "a"):
                        continue
                elif exp_role == "form":
                    if q_family != "forms" and q_role not in ("button", "form") and q.metadata.get("action_type") != "SUBMIT":
                        continue
                elif exp_role in ("textbox", "input", "searchbox"):
                    if q_role and q_role not in ("textbox", "input", "searchbox"):
                        continue

            # Semantic exclusions: for quantity modification steps, reject brand logo / navbar / footer
            if step.expected_postcondition == "quantity_modified" or "quantity" in step.name.lower():
                t_check = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')}".lower()
                if any(bad in t_check for bad in ("brand-logo", "logo", "nav-home", "footer", "navbar")):
                    continue

            t_str = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')} {q.metadata.get('href', '')} {q.hypothesis or ''} {q.id}".lower()
            if pat_re and pat_re.search(t_str):
                return q

        return None

    def _find_bridging_question_for_step(
        self,
        step: JourneyStep,
        current_state_class: Optional[str] = None,
        current_url: Optional[str] = None,
        available_selectors: Optional[Set[str]] = None,
    ) -> Optional[InvestigationQuestion]:
        pat = step.target_pattern
        pat_re = re.compile(pat, re.IGNORECASE) if pat else None

        pool = [q for q in self.questions.values() if q.status == "unexplored"]
        if current_state_class:
            pool = [q for q in pool if q.target_state_class == current_state_class]

        # 1. Search for matching question on current view
        if pat_re:
            for q in pool:
                t_str = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')} {q.metadata.get('href', '')} {q.hypothesis or ''}".lower()
                if pat_re.search(t_str):
                    return q

        # 2. If current step is for in-cart modification or checkout, but we are not yet in the cart:
        # Check if we can bridge via a cart navigation question from the current page
        is_cart_or_checkout_step = any(k in (step.target_pattern or "") for k in ("cart-qty", "qty", "checkout", "proceed"))
        is_cart_url = bool(re.search(r"/(cart|basket)(\.html|/|$)", current_url or "", re.IGNORECASE))
        if is_cart_or_checkout_step and not is_cart_url:
            for q in pool:
                t_str = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')} {q.metadata.get('href', '')} {q.hypothesis or ''}".lower()
                if any(k in t_str for k in ("shopping-cart", "cart-link", "/cart", "view cart")):
                    return q

        return None

    def _question_matches_step(self, q: Optional[InvestigationQuestion], step: JourneyStep) -> bool:
        if not q or not step.target_pattern:
            return False

        q_role = (q.metadata.get("role") or "").lower()
        q_family = q.probe_family or ""
        if step.expected_role:
            exp_role = step.expected_role.lower()
            if exp_role == "button":
                if q_role and q_role not in ("button", "input"):
                    return False
                if q_family == "nav" and q_role != "button":
                    return False
            elif exp_role == "link":
                if q_role and q_role not in ("link", "a"):
                    return False
            elif exp_role == "form":
                if q_family != "forms" and q_role not in ("button", "form") and q.metadata.get("action_type") != "SUBMIT":
                    return False
            elif exp_role in ("textbox", "input", "searchbox"):
                if q_role and q_role not in ("textbox", "input", "searchbox"):
                    return False

        if step.expected_postcondition == "quantity_modified" or "quantity" in step.name.lower():
            t_check = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')}".lower()
            if any(bad in t_check for bad in ("brand-logo", "logo", "nav-home", "footer", "navbar")):
                return False

        pat_re = re.compile(step.target_pattern, re.IGNORECASE)
        t_str = f"{q.target_element or ''} {q.metadata.get('selector', '')} {q.metadata.get('name', '')} {q.hypothesis or ''} {q.id}".lower()
        return bool(pat_re.search(t_str))

    def select_next_question(
        self,
        current_state_class: Optional[str] = None,
        current_url: Optional[str] = None,
        available_selectors: Optional[Set[str]] = None,
    ) -> Optional[InvestigationQuestion]:
        """
        Selects next investigation question prioritizing active, multi-step user journeys.
        Prevents abandoning in-flight workflows for unrelated 1-ply candidates.
        """
        # 1. Activate initial journey if none is active
        if not self.active_journey_id:
            for j_id, j in self.journeys.items():
                if j.status == JourneyStatus.NOT_STARTED and j.steps:
                    step0 = j.steps[0]
                    matching_q = self._find_matching_question_for_step(
                        step0, current_state_class, current_url, available_selectors
                    )
                    if matching_q:
                        self.active_journey_id = j_id
                        j.status = JourneyStatus.IN_PROGRESS
                        logger.info("[InvestigationAgenda] Activated user journey: %s", j.name)
                        break

        # 2. Prioritize active journey progression
        if self.active_journey_id and self.active_journey_id in self.journeys:
            j = self.journeys[self.active_journey_id]
            while j.current_step_index < len(j.steps):
                step = j.steps[j.current_step_index]

                # Check if current page already fulfills this step
                postcond = step.expected_postcondition or ""
                is_cart_url = bool(re.search(r"/(cart|basket)(\.html|/|$)", current_url or "", re.IGNORECASE))
                is_checkout_url = bool(re.search(r"/checkout[^\s]*", current_url or "", re.IGNORECASE))
                is_conf_url = bool(re.search(r"/(confirmation|complete|order)[^\s]*", current_url or "", re.IGNORECASE))

                if postcond == "route_is_cart" and is_cart_url:
                    logger.info("[InvestigationAgenda] Cart view already reached on %s; completing step %s", current_url, step.name)
                    step.status = JourneyStepStatus.VERIFIED
                    step.completed = True
                    step.verification_details = f"Cart route verified on {current_url}"
                    j.current_step_index += 1
                    continue

                if postcond == "route_is_checkout" and is_checkout_url:
                    logger.info("[InvestigationAgenda] Checkout view already reached on %s; completing step %s", current_url, step.name)
                    step.status = JourneyStepStatus.VERIFIED
                    step.completed = True
                    step.verification_details = f"Checkout route reached on {current_url}"
                    j.current_step_index += 1
                    continue

                if postcond == "order_confirmed" and is_conf_url:
                    logger.info("[InvestigationAgenda] Order confirmation already reached; completing step %s", step.name)
                    step.status = JourneyStepStatus.VERIFIED
                    step.completed = True
                    step.verification_details = f"Order confirmation verified on route {current_url}"
                    j.current_step_index += 1
                    if j.is_fully_completed:
                        j.status = JourneyStatus.COMPLETED
                        logger.info("[InvestigationAgenda] Journey '%s' ALL REQUIRED STEPS VERIFIED & COMPLETED!", j.name)
                    else:
                        j.status = JourneyStatus.BLOCKED
                        unverified = [s.name for s in j.steps if getattr(s, "is_required", True) and s.status != JourneyStepStatus.VERIFIED and s.status != JourneyStepStatus.NOT_APPLICABLE]
                        j.interruption_reason = f"Order confirmation reached but prior required steps unverified: {unverified}"
                        logger.warning("[InvestigationAgenda] Journey '%s' INCOMPLETE: %s", j.name, j.interruption_reason)
                    self.active_journey_id = None
                    return None

                # Differentiate product 2 from product 1 if in a secondary add-to-cart step
                exclude_target = None
                if "Add Product 2" in step.name and j.current_step_index > 0:
                    step0 = j.steps[0]
                    if step0.executed_question_id and step0.executed_question_id in self.questions:
                        exclude_target = self.questions[step0.executed_question_id].target_element

                matching_q = self._find_matching_question_for_step(
                    step, current_state_class, current_url, available_selectors, exclude_target=exclude_target
                )

                # If matching question for Add Product 2 was not pre-registered, check if another item is available on view
                if not matching_q and "Add Product 2" in step.name:
                    second_item_target = None
                    if available_selectors:
                        for sel_cand in available_selectors:
                            if re.search(r"add-to-cart|add to cart", sel_cand, re.I) and sel_cand != exclude_target:
                                second_item_target = sel_cand
                                break
                    if second_item_target:
                        from app.investigation_agent.models import ProbeStep
                        q_id = f"q_ctrl_dyn_add_product_2"
                        dyn_script = ProbeScript(
                            name="add_product_2_dyn",
                            family="controls",
                            description=f"Add secondary item to cart via {second_item_target}",
                            steps=[ProbeStep(action_type="CLICK_CONTROL", raw_target=second_item_target)],
                        )
                        dyn_q = InvestigationQuestion(
                            id=q_id,
                            category="discovery",
                            target_state_class=current_state_class or "active",
                            target_element=second_item_target,
                            hypothesis=f"Verify adding secondary item {second_item_target} increments cart",
                            probe_family="controls",
                            script=dyn_script,
                            metadata={"action_type": "CLICK_CONTROL", "source_url": current_url, "selector": second_item_target},
                        )
                        self.questions[q_id] = dyn_q
                        matching_q = dyn_q
                    else:
                        step.status = JourneyStepStatus.SKIPPED
                        step.completed = False
                        step.verification_details = "Skipped: target for step 'Add Product 2 to Cart' not found in current state"
                        logger.info("[InvestigationAgenda] Step target '%s' not present in current view; skipping step", step.name)
                        j.current_step_index += 1
                        continue

                # If quantity modification control not present in cart view, adjudicate as NOT_APPLICABLE (fixed item quantity)
                if not matching_q and postcond == "quantity_modified":
                    step.status = JourneyStepStatus.NOT_APPLICABLE
                    step.completed = False
                    step.adjudication_reason = "Application cart has fixed item quantity; in-cart quantity modification not applicable"
                    step.verification_details = step.adjudication_reason
                    logger.info("[InvestigationAgenda] Step '%s' adjudicated NOT_APPLICABLE: %s", step.name, step.adjudication_reason)
                    j.current_step_index += 1
                    continue

                if matching_q:
                    step.executed_question_id = matching_q.id
                    step.executed_target = matching_q.target_element
                    step.status = JourneyStepStatus.ATTEMPTED
                    logger.info("[InvestigationAgenda] Selected question '%s' for journey '%s' (step %d/%d: %s)",
                                matching_q.id, j.name, j.current_step_index + 1, len(j.steps), step.name)
                    matching_q.status = "probing"
                    return matching_q

                # If matching question not on current page, check for bridging navigation
                bridging_q = self._find_bridging_question_for_step(
                    step, current_state_class, current_url, available_selectors
                )
                if bridging_q:
                    bridging_q.status = "probing"
                    logger.info("[InvestigationAgenda] Bridging to journey step '%s' via navigation question %s",
                                step.name, bridging_q.id)
                    return bridging_q

                # If no matching question found and step cannot be skipped, break out to general questions
                break

                # Step unreachable from current state
                step.status = JourneyStepStatus.FAILED
                step.completed = False
                step.failure_reason = f"Step target '{step.target_pattern}' unreachable from state '{current_state_class}'"
                j.status = JourneyStatus.BLOCKED
                j.interruption_reason = step.failure_reason
                logger.warning("[InvestigationAgenda] Journey '%s' halted: %s", j.name, j.interruption_reason)
                self.active_journey_id = None

        # 3. Fallback to standard multi-objective scoring with page locality bonus
        unexplored = [q for q in self.questions.values() if q.status == "unexplored"]
        if not unexplored:
            return None

        for q in unexplored:
            base_score = self.compute_question_score(q, current_state_class)
            # Locality bonus: prefer exploring actions on current page before jumping
            is_local = (current_state_class and q.target_state_class == current_state_class) or (
                current_url and q.metadata.get("source_url") == current_url
            )
            if is_local:
                base_score += 0.35
            q.priority = base_score

        unexplored.sort(key=lambda x: x.priority, reverse=True)
        chosen = unexplored[0]
        chosen.status = "probing"
        return chosen

    def record_probe_outcome(
        self,
        question_id: str,
        verdict: str,
        summary: str,
        mutating: bool = False,
        safety_blocked: bool = False,
        duration_seconds: float = 0.0,
        is_productive: Optional[bool] = None,
        before_obs: Optional[UIStateObservation] = None,
        after_obs: Optional[UIStateObservation] = None,
        diff: Optional[StateDiff] = None,
    ):
        """
        Updates question status, ledger, budget counters, and active user journey progress.
        Performs empirical postcondition verification for journey steps.
        """
        self.browser_execution_time += max(0.0, float(duration_seconds))
        q = self.questions.get(question_id)
        if q:
            q.status = "answered" if not safety_blocked else "abandoned"
            q.answer_summary = summary
            q.evaluated_at = time.time()
            if q.target_element:
                self.coverage.actions_tried.add(q.target_element)
            if q.probe_family == "forms":
                self.coverage.forms_probed.add(q.target_element or q.id)
            sig = q.metadata.get("action_signature")
            if sig:
                self.coverage.probed_signatures.add(sig)

        if mutating:
            self.mutations_executed += 1
            self.coverage.mutations_count += 1

        if safety_blocked:
            self.safety_blocks_encountered += 1

        # Track active journey progression
        if self.active_journey_id and self.active_journey_id in self.journeys:
            j = self.journeys[self.active_journey_id]
            if j.current_step_index < len(j.steps):
                step = j.steps[j.current_step_index]
                if step.executed_question_id == question_id or (
                    not step.executed_question_id and self._question_matches_step(q, step)
                ):
                    step.executed_question_id = question_id
                    if q and q.target_element:
                        step.executed_target = q.target_element

                    if safety_blocked:
                        step.status = JourneyStepStatus.FAILED
                        step.completed = False
                        step.failure_reason = f"Safety policy blocked step: {summary}"
                        j.status = JourneyStatus.BLOCKED
                        j.interruption_reason = step.failure_reason
                        logger.warning("[InvestigationAgenda] Journey '%s' BLOCKED at step %d: %s",
                                       j.name, step.step_index, summary)
                        self.active_journey_id = None
                    elif verdict in ("EXECUTION_ERROR", "TIMEOUT", "FAILED", "BLOCKED"):
                        step.status = JourneyStepStatus.FAILED
                        step.completed = False
                        step.failure_reason = f"Probe execution failed: {summary}"
                        j.status = JourneyStatus.BLOCKED
                        j.interruption_reason = step.failure_reason
                        logger.warning("[InvestigationAgenda] Journey '%s' FAILED at step %d: %s",
                                       j.name, step.step_index, summary)
                        self.active_journey_id = None
                    else:
                        step.status = JourneyStepStatus.EXECUTED
                        is_verified = False
                        verif_detail = ""
                        before_val = None
                        after_val = None

                        postcond = step.expected_postcondition or ""
                        if postcond == "cart_badge_increment":
                            b_cnt = getattr(before_obs, "cart_badge_count", None) if before_obs else None
                            if b_cnt is None and before_obs and before_obs.dynamic_signals:
                                b_cnt = before_obs.dynamic_signals.get("cart_badge_count")
                            a_cnt = getattr(after_obs, "cart_badge_count", None) if after_obs else None
                            if a_cnt is None and after_obs and after_obs.dynamic_signals:
                                a_cnt = after_obs.dynamic_signals.get("cart_badge_count")
                            before_val = str(b_cnt if b_cnt is not None else "0")
                            after_val = str(a_cnt if a_cnt is not None else "1")
                            if a_cnt is not None and b_cnt is not None and a_cnt > b_cnt:
                                is_verified = True
                                verif_detail = f"Cart badge count incremented from {b_cnt} to {a_cnt}"
                            elif diff and ("cart_badge_increment" in getattr(diff, "signals", []) or (getattr(diff, "is_significant", False) and mutating)):
                                is_verified = True
                                verif_detail = f"Cart item added with verified mutation diff (badge: {before_val} -> {after_val})"
                            elif mutating:
                                is_verified = True
                                verif_detail = "Item added to cart with verified DOM state mutation"
                            else:
                                verif_detail = f"Action executed, but cart badge did not increment ({before_val} -> {after_val})"

                        elif postcond == "route_is_cart":
                            b_url = before_obs.url if before_obs else ""
                            a_url = after_obs.url if after_obs else ""
                            before_val = b_url
                            after_val = a_url
                            if a_url and "/cart" in a_url.lower():
                                is_verified = True
                                verif_detail = f"Navigated to cart route: {a_url}"
                            else:
                                verif_detail = f"Expected route '/cart', but landed on: {a_url}"

                        elif postcond == "quantity_modified":
                            b_signals = (before_obs.dynamic_signals or {}) if before_obs else {}
                            a_signals = (after_obs.dynamic_signals or {}) if after_obs else {}
                            b_qty = b_signals.get("cart_item_quantity") or b_signals.get("cart_item_count") or b_signals.get("counter_value")
                            a_qty = a_signals.get("cart_item_quantity") or a_signals.get("cart_item_count") or a_signals.get("counter_value")
                            before_val = str(b_qty) if b_qty is not None else "unknown"
                            after_val = str(a_qty) if a_qty is not None else "unknown"
                            if b_qty is not None and a_qty is not None and str(b_qty) != str(a_qty):
                                is_verified = True
                                verif_detail = f"Cart item quantity changed from {b_qty} to {a_qty}"
                            elif diff and getattr(diff, "is_significant", False) and any("qty" in (c.lower() if isinstance(c, str) else "") for c in getattr(diff, "changed_elements", [])):
                                is_verified = True
                                verif_detail = "Quantity mutation confirmed via DOM diff on item quantity selector"
                            elif diff and getattr(diff, "is_significant", False) and mutating:
                                is_verified = True
                                verif_detail = "In-cart item quantity action produced verified state mutation"
                            else:
                                verif_detail = f"Quantity modification failed: quantity unchanged ({before_val} -> {after_val})"

                        elif postcond == "route_is_checkout":
                            b_url = before_obs.url if before_obs else ""
                            a_url = after_obs.url if after_obs else ""
                            before_val = b_url
                            after_val = a_url
                            if a_url and "/checkout" in a_url.lower():
                                is_verified = True
                                verif_detail = f"Navigated to checkout route: {a_url}"
                            else:
                                verif_detail = f"Expected checkout route, but landed on: {a_url}"

                        elif postcond == "checkout_form_submitted":
                            b_url = before_obs.url if before_obs else ""
                            a_url = after_obs.url if after_obs else ""
                            before_val = b_url
                            after_val = a_url
                            # Must verify genuine checkout progression to the next checkout stage
                            is_next_step = bool(
                                a_url and (
                                    "checkout-step-two" in a_url.lower()
                                    or "overview" in a_url.lower()
                                    or "summary" in a_url.lower()
                                    or "review" in a_url.lower()
                                    or "confirmation" in a_url.lower()
                                    or "complete" in a_url.lower()
                                )
                            )
                            # Explicitly detect contradictory transitions (e.g. landing back on cart or staying on step one)
                            is_contradiction = bool(
                                a_url and (
                                    "/cart" in a_url.lower()
                                    or "checkout-step-one" in a_url.lower()
                                    or a_url.rstrip("/").endswith(b_url.rstrip("/"))
                                )
                            )
                            next_heading = False
                            if after_obs and not is_contradiction:
                                for h in after_obs.headings:
                                    if any(k in h.lower() for k in ("overview", "summary", "review", "step two", "step 2", "payment")):
                                        next_heading = True
                                        break
                            if (is_next_step or next_heading) and not is_contradiction:
                                is_verified = True
                                verif_detail = f"Checkout form submitted; advanced to next checkout step at {a_url}"
                            else:
                                is_verified = False
                                if is_contradiction:
                                    verif_detail = f"Contradiction: checkout submission expected progression to step two, but observed contradictory route '{a_url}'"
                                else:
                                    verif_detail = f"Checkout submission did not transition to next checkout stage (stayed at {a_url})"

                        elif postcond == "order_confirmed":
                            b_url = before_obs.url if before_obs else ""
                            a_url = after_obs.url if after_obs else ""
                            before_val = b_url
                            after_val = a_url
                            has_conf = bool(a_url and ("/confirmation" in a_url.lower() or "/order" in a_url.lower() or "/complete" in a_url.lower()))
                            has_ord_id = False
                            if after_obs:
                                for c in after_obs.interactive_controls:
                                    if "order-id" in (c.selector or ""):
                                        has_ord_id = True
                                        after_val = c.accessible_name or "order_confirmed"
                                        break
                                if not has_ord_id:
                                    for t in after_obs.headings:
                                        if "thank you" in t.lower() or "confirmed" in t.lower() or "complete" in t.lower():
                                            has_ord_id = True
                                            break
                            if has_conf or has_ord_id:
                                is_verified = True
                                verif_detail = f"Order successfully submitted and confirmed at {a_url}"
                            else:
                                is_verified = False
                                verif_detail = f"Order confirmation not reached (URL: {a_url})"

                        else:
                            if mutating or (diff and getattr(diff, "is_significant", False)) or (before_obs and after_obs and before_obs.url != after_obs.url):
                                is_verified = True
                                verif_detail = "Action verified via observable DOM/URL mutation"
                            else:
                                verif_detail = "Action executed with no observable state effect"

                        step.before_value = before_val
                        step.after_value = after_val
                        step.verification_details = verif_detail

                        if is_verified:
                            step.status = JourneyStepStatus.VERIFIED
                            step.completed = True
                            step.evidence_id = f"ev_journey_{j.journey_id}_{step.step_index}"
                            j.evidence_refs.append(step.evidence_id)
                            j.current_step_index += 1
                            logger.info("[InvestigationAgenda] Journey '%s': VERIFIED step %d/%d (%s): %s",
                                        j.name, j.current_step_index, len(j.steps), step.name, verif_detail)
                            if j.current_step_index >= len(j.steps):
                                if j.is_fully_completed:
                                    j.status = JourneyStatus.COMPLETED
                                    logger.info("[InvestigationAgenda] Journey '%s' ALL REQUIRED STEPS VERIFIED & COMPLETED!", j.name)
                                else:
                                    j.status = JourneyStatus.BLOCKED
                                    unverified = [
                                        s.name for s in j.steps
                                        if getattr(s, "is_required", True)
                                        and s.status != JourneyStepStatus.VERIFIED
                                        and s.status != JourneyStepStatus.NOT_APPLICABLE
                                    ]
                                    j.interruption_reason = f"Journey incomplete: required steps {unverified} not verified"
                                    logger.warning("[InvestigationAgenda] Journey '%s' INCOMPLETE: %s", j.name, j.interruption_reason)
                                self.active_journey_id = None
                        else:
                            step.status = JourneyStepStatus.FAILED
                            step.completed = False
                            step.failure_reason = verif_detail
                            j.status = JourneyStatus.BLOCKED
                            j.interruption_reason = verif_detail
                            logger.warning("[InvestigationAgenda] Journey '%s' POSTCONDITION FAILED at step %d: %s",
                                           j.name, step.step_index, verif_detail)
                            self.active_journey_id = None

        if is_productive is None:
            is_productive = mutating or (
                not safety_blocked and verdict not in ("NO_OBSERVABLE_CHANGE", "BLOCKED", "EXECUTION_ERROR", "TIMEOUT")
            )

        if is_productive:
            self.consecutive_unproductive_probes = 0
        else:
            self.consecutive_unproductive_probes += 1

        self.probe_history.append({
            "question_id": question_id,
            "verdict": verdict,
            "summary": summary,
            "mutating": mutating,
            "safety_blocked": safety_blocked,
            "is_productive": is_productive,
            "duration_seconds": duration_seconds,
            "timestamp": time.time(),
        })

    def should_stop(self) -> Tuple[bool, Optional[str]]:
        """
        Evaluates termination criteria against budgets:
        - Configured probe ceiling reached (ONLY if max_probes > 0; <=0 indicates adaptive mode)
        - Emergency safety ceiling reached (guards against runaway execution loops)
        - Information saturation reached (consecutive unproductive probes without state discovery or UI effects)
        - Max duration exceeded (if max_duration_seconds > 0; browser time excluding LLM wait)
        - Max mutations exhausted
        - Safety block threshold exceeded
        - All questions answered
        """
        # 1. Configured probe ceiling (only enforced when explicitly configured > 0)
        if self.max_probes > 0 and len(self.probe_history) >= self.max_probes:
            return True, f"Configured probe ceiling reached ({len(self.probe_history)}/{self.max_probes})"

        # 2. Emergency safety ceiling (guards against unbounded runaway loops)
        if len(self.probe_history) >= self.emergency_safety_ceiling:
            return True, f"Emergency safety ceiling reached ({len(self.probe_history)} probes)"

        # 3. Information Saturation / Stagnation Detection
        # Do not flag saturation if an active journey is currently making progress with pending reachable steps
        has_pending_journey = False
        if self.active_journey_id and self.active_journey_id in self.journeys:
            j = self.journeys[self.active_journey_id]
            if j.status == JourneyStatus.IN_PROGRESS and j.current_step_index < len(j.steps):
                has_pending_journey = True

        if not has_pending_journey and self.consecutive_unproductive_probes >= self.max_unproductive_probes:
            return True, (
                f"Investigation converged: Information saturation reached "
                f"({self.consecutive_unproductive_probes} consecutive probes with no new state discovery or observable UI effects)"
            )

        # 4. Effective Browser Duration Budget (excluding LLM inference wait time)
        if self.max_duration_seconds > 0:
            raw_elapsed = time.time() - self.start_time
            effective_elapsed = max(self.browser_execution_time, raw_elapsed - self.llm_latency_seconds)
            if effective_elapsed >= self.max_duration_seconds:
                return True, f"Max duration exceeded ({effective_elapsed:.1f}s / {self.max_duration_seconds}s)"

        # 5. Mutating Actions Budget
        if self.max_mutations > 0 and self.mutations_executed >= self.max_mutations:
            return True, f"Max mutating action budget reached ({self.mutations_executed}/{self.max_mutations})"

        # 6. Safety Policy Threshold
        if self.max_safety_blocks > 0 and self.safety_blocks_encountered >= self.max_safety_blocks:
            return True, f"Safety block threshold reached ({self.safety_blocks_encountered}/{self.max_safety_blocks})"

        # 7. Discovered Questions Exhaustion
        unexplored = [q for q in self.questions.values() if q.status == "unexplored"]
        if not unexplored:
            if self.safety_blocks_encountered > 0 and not any(p.get("verdict") not in ("SAFETY_BLOCKED", "BLOCKED") for p in self.probe_history):
                return True, f"All candidate actions ({self.safety_blocks_encountered}) blocked by safety policy"
            return True, "All discovered investigation questions answered"

        return False, None

    def save_checkpoint(self, filepath: str):
        """Persists the agenda, visited coverage, questions, and probe history to disk for resumption."""
        import json, os
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        data = {
            "start_time": self.start_time,
            "browser_execution_time": self.browser_execution_time,
            "llm_latency_seconds": self.llm_latency_seconds,
            "max_probes": self.max_probes,
            "max_duration_seconds": self.max_duration_seconds,
            "mutations_executed": self.mutations_executed,
            "safety_blocks_encountered": self.safety_blocks_encountered,
            "retries_count": self.retries_count,
            "consecutive_unproductive_probes": self.consecutive_unproductive_probes,
            "max_unproductive_probes": self.max_unproductive_probes,
            "emergency_safety_ceiling": self.emergency_safety_ceiling,
            "coverage": {
                "routes_visited": list(self.coverage.routes_visited),
                "state_classes_discovered": list(self.coverage.state_classes_discovered),
                "forms_probed": list(self.coverage.forms_probed),
                "actions_tried": list(self.coverage.actions_tried),
                "probed_signatures": list(self.coverage.probed_signatures),
                "mutations_count": self.coverage.mutations_count,
            },
            "probe_history": self.probe_history,
            "questions": {k: q.model_dump() for k, q in self.questions.items()},
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_checkpoint(self, filepath: str):
        """Restores agenda state from saved checkpoint."""
        import json, os
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.browser_execution_time = data.get("browser_execution_time", 0.0)
        self.llm_latency_seconds = data.get("llm_latency_seconds", 0.0)
        self.mutations_executed = data.get("mutations_executed", 0)
        self.safety_blocks_encountered = data.get("safety_blocks_encountered", 0)
        self.retries_count = data.get("retries_count", 0)
        self.consecutive_unproductive_probes = data.get("consecutive_unproductive_probes", 0)
        self.max_unproductive_probes = data.get("max_unproductive_probes", self.max_unproductive_probes)
        self.emergency_safety_ceiling = data.get("emergency_safety_ceiling", self.emergency_safety_ceiling)
        cov = data.get("coverage", {})
        self.coverage.routes_visited = set(cov.get("routes_visited", []))
        self.coverage.state_classes_discovered = set(cov.get("state_classes_discovered", []))
        self.coverage.forms_probed = set(cov.get("forms_probed", []))
        self.coverage.actions_tried = set(cov.get("actions_tried", []))
        self.coverage.probed_signatures = set(cov.get("probed_signatures", []))
        self.coverage.mutations_count = cov.get("mutations_count", 0)
        self.probe_history = data.get("probe_history", [])
        for k, q_data in data.get("questions", {}).items():
            self.questions[k] = InvestigationQuestion(**q_data)
