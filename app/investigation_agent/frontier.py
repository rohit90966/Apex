"""
Persistent Investigation Frontier Manager.
Tracks discovered pages, semantic UI states, candidate interactions, and unexplored workflows.
Applies deterministic prioritization scores to select highest-yield, safe candidate actions.
"""

from typing import Dict, List, Optional, Tuple, Set, Any
from app.config import get_settings
from app.safety import validate_url_safety, SafetyViolation
from app.investigation_agent.models import (
    FrontierItem,
    FrontierStatus,
    UIStateObservation,
    ElementReference,
    ActionFailureReason,
)


class FrontierManager:
    """
    Manages the investigation frontier.
    Scores candidates based on novelty, safety, and likelihood of unlocking new application functionality.
    Enforces current-state action compatibility to prevent stale-control timeouts.
    """

    @classmethod
    def populate_from_observation(
        cls,
        frontier: Dict[str, FrontierItem],
        obs: UIStateObservation,
        visited_signatures: List[str]
    ) -> int:
        """
        Discovers new frontier items (links, buttons, tabs, interactive elements)
        from a fresh observation and registers them with appropriate priority scores.
        """
        settings = get_settings()
        allowed_hosts = settings.allowed_hosts_list
        added_count = 0
        existing_targets: Set[str] = {item.target for item in frontier.values()}

        # 1. Navigation Links (distinguish internal routes, fragments, and external links)
        for link in obs.navigation_links:
            href = link.get("href", "").strip()
            if not href or href == "#":
                continue

            is_fragment = link.get("is_fragment", False) or href.startswith("#")
            is_external = link.get("is_external", False) or href.startswith("http://") or href.startswith("https://")

            # Check if external host is permitted by safety policy
            is_allowed = True
            if is_external:
                try:
                    validate_url_safety(href, allowed_hosts)
                except SafetyViolation:
                    is_allowed = False

            item_id = f"nav_{href}"
            if item_id not in frontier:
                if not is_allowed:
                    frontier[item_id] = FrontierItem(
                        item_id=item_id,
                        item_type="page",
                        target=href,
                        parent_state_id=obs.state_id,
                        status=FrontierStatus.BLOCKED,
                        priority_score=0.0,
                        rationale=f"Safety blocked: External navigation outside allowed hosts policy ({href})",
                        is_external=True,
                        is_fragment=is_fragment,
                        last_failure_reason=ActionFailureReason.SAFETY_BLOCKED
                    )
                else:
                    is_visited = any(href in sig for sig in visited_signatures)
                    priority = 0.2 if is_visited else 2.5
                    frontier[item_id] = FrontierItem(
                        item_id=item_id,
                        item_type="page",
                        target=href,
                        parent_state_id=obs.state_id,
                        status=FrontierStatus.DISCOVERED,
                        priority_score=priority,
                        rationale=f"Navigation link '{link.get('text', '')}' to {href}",
                        is_external=is_external,
                        is_fragment=is_fragment
                    )
                    added_count += 1

        # 2. Generalized Interactive Controls
        seen_labels_on_page: Dict[str, str] = {}
        for ctrl in obs.interactive_controls:
            ctrl_name = ctrl.accessible_name.lower().strip()
            ctrl_sel = ctrl.selector.lower()
            ctrl_role = ctrl.role.lower()
            route_key = (obs.normalized_route or obs.state_id).replace("/", "_").strip("_")
            item_id = f"act_{route_key}_{ctrl.element_id}"

            # Deduplication: Avoid re-registering identical selector on the same route across in-page mutations
            if item_id in frontier or any(
                existing.item_type == "control"
                and existing.target == ctrl.selector
                and (existing.parent_state_id == obs.state_id or route_key in existing.item_id)
                for existing in frontier.values()
            ):
                continue

            # Skip redundant image-only card links when text links exist
            if (ctrl.declared_constraints or {}).get("is_image_only_link", False) or "img_link" in ctrl_sel or "img-link" in ctrl_sel:
                continue

            # Deterministic priority based on workflow progression, state modification, and safety
            is_destructive = any(k in ctrl_name or k in ctrl_sel for k in ("delete", "remove", "logout", "signout", "cancel", "reset"))
            is_progression = any(k in ctrl_name or k in ctrl_sel for k in ("proceed", "continue", "next", "advance", "forward", "submit-order", "checkout", "cart"))
            is_state_action = any(k in ctrl_name or k in ctrl_sel for k in ("add", "select", "choose", "plus", "pick", "apply", "create", "sort", "filter")) or ctrl.tag_name.lower() == "select"
            is_tab_or_nav = ctrl_role in ("tab", "tablist", "menuitem")

            # Mark duplicate repeated card buttons (e.g. 6 "Add to cart" buttons) as DEFERRED after first representative
            is_duplicate_label = bool(ctrl_name and ctrl_role == "button" and ctrl_name in seen_labels_on_page)
            if ctrl_name and ctrl_role == "button" and not is_duplicate_label:
                seen_labels_on_page[ctrl_name] = item_id

            if is_destructive:
                priority = 0.4
                rationale = f"Potentially mutating or terminal action: {ctrl.accessible_name}"
            elif is_progression:
                priority = 4.5
                rationale = f"Workflow progression: Proceed to next stage '{ctrl.accessible_name}'"
            elif is_state_action:
                priority = 3.8
                rationale = f"State-modifying interaction: {ctrl.accessible_name}"
            elif is_tab_or_nav:
                priority = 2.2
                rationale = f"Navigation/tab container interaction: {ctrl.accessible_name}"
            else:
                priority = 1.5
                rationale = f"Interactive {ctrl.role}: {ctrl.accessible_name}"

            frontier[item_id] = FrontierItem(
                item_id=item_id,
                item_type="control",
                target=ctrl.selector,
                parent_state_id=obs.state_id,
                status=FrontierStatus.DEFERRED if is_duplicate_label else FrontierStatus.DISCOVERED,
                priority_score=0.2 if is_duplicate_label else priority,
                rationale=rationale,
                last_reason="Deferred: equivalence class represented by primary control on view" if is_duplicate_label else None,
            )
            if not is_duplicate_label:
                added_count += 1

        return added_count

    @classmethod
    def sync_with_probe_outcome(
        cls,
        frontier: Dict[str, FrontierItem],
        question: Optional[Any],
        before_obs: Optional[UIStateObservation],
        after_obs: Optional[UIStateObservation],
        safety_blocked: bool = False,
        summary: str = "",
    ) -> None:
        """
        Synchronizes FrontierItem statuses with executed InvestigationQuestions so
        frontier metrics accurately reflect investigated, blocked, and pending items.
        """
        if not frontier:
            return

        target_el = (getattr(question, "target_element", None) or "") if question else ""
        meta = (getattr(question, "metadata", None) or {}) if question else {}
        sel = meta.get("selector") or meta.get("target") or target_el
        href = meta.get("href") or ""
        fam = getattr(question, "probe_family", "") if question else ""

        form_submit_sels = set()
        if before_obs and fam in ("forms", "auth"):
            for f in (before_obs.forms or []):
                if f.get("submit_control_selector"):
                    form_submit_sels.add(f.get("submit_control_selector"))

        after_url = (after_obs.url if after_obs else "") or ""
        after_norm = (after_obs.normalized_route if after_obs else "") or ""

        for item in frontier.values():
            matched = False
            if sel and item.target == sel:
                matched = True
            elif href and item.target == href:
                matched = True
            elif item.target in form_submit_sels:
                matched = True
            elif item.item_type == "page" and after_url and (item.target in after_url or item.target in after_norm):
                matched = True

            if matched:
                item.attempt_count += 1
                item.status = FrontierStatus.BLOCKED if safety_blocked else FrontierStatus.INVESTIGATED
                if summary:
                    item.last_reason = summary[:140]

    @classmethod
    def get_compatible_candidates(
        cls,
        frontier: Dict[str, FrontierItem],
        current_state_id: str,
        current_obs: Optional[UIStateObservation] = None,
    ) -> List[FrontierItem]:
        """
        Returns all candidate actions that can physically be executed in the current browser state.
        Ensures elements are present and unvisited links belong to eligible routes.
        """
        candidates: List[FrontierItem] = [
            item for item in frontier.values()
            if item.status == FrontierStatus.DISCOVERED and item.attempt_count == 0
        ]
        if not candidates:
            return []

        current_selectors: Set[str] = set()
        if current_obs:
            current_selectors = {c.selector for c in current_obs.interactive_controls}

        def is_compatible(item: FrontierItem) -> bool:
            if item.item_type == "page":
                if current_obs and (item.target == current_obs.url or item.target == current_obs.normalized_route):
                    return False
                return True
            elif item.item_type == "control":
                if item.parent_state_id == current_state_id:
                    return True
                if current_selectors and item.target in current_selectors:
                    return True
                return False
            return True

        return [c for c in candidates if is_compatible(c)]

    @classmethod
    def select_next_candidate(
        cls,
        frontier: Dict[str, FrontierItem],
        current_state_id: str,
        current_obs: Optional[UIStateObservation] = None,
        llm_decision: Optional[Any] = None,
    ) -> Optional[FrontierItem]:
        """
        Picks the next investigative candidate.
        If an LLM recommendation is provided and physically compatible with the active DOM,
        it is given primary authority.
        Otherwise, applies multi-factor deterministic scoring incorporating novelty,
        coverage diversity, state locality, and prior failure penalties.
        """
        compatible_candidates = cls.get_compatible_candidates(frontier, current_state_id, current_obs)

        if not compatible_candidates:
            # Fallback to navigation links to transition out of dead-ends
            candidates: List[FrontierItem] = [
                item for item in frontier.values()
                if item.status == FrontierStatus.DISCOVERED and item.attempt_count == 0 and item.item_type == "page"
            ]
            if candidates:
                candidates.sort(key=lambda x: x.priority_score, reverse=True)
                return candidates[0]
            return None

        # 1. LLM Recommendation Priority
        if llm_decision and hasattr(llm_decision, "selected_candidate_id"):
            sel_id = llm_decision.selected_candidate_id
            match = next((c for c in compatible_candidates if c.item_id == sel_id), None)
            if match:
                return match

        # 2. Multi-factor deterministic scoring
        def sort_key(item: FrontierItem) -> float:
            score = item.priority_score
            # Strong preference for controls residing in the active view
            if item.parent_state_id == current_state_id:
                score += 2.0
            # Penalize prior failures if any
            if item.attempt_count > 0:
                score -= 1.5 * item.attempt_count
            return score

        compatible_candidates.sort(key=sort_key, reverse=True)
        return compatible_candidates[0]


# Compatibility aliases
FrontierCandidate = FrontierItem
FrontierQueue = FrontierManager

