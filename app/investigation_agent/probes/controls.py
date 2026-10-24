"""
Interactive Control Probes for Stateful Investigation.
Tests interactive elements (buttons, links, toggles):
- Verifies that visible, enabled controls have an observable effect.
- Detects NO_OP controls (dead buttons).
- Detects CONTRADICTION where a mutation/action reports success over network but DOM remains unchanged.
"""

import re
from typing import List, Dict, Any, Tuple
from app.investigation_agent.models import (
    UIStateObservation,
    ProbeScript,
    ProbeStep,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    Prediction,
)


def _classify_control_semantics(tag: str, role: str, name_clean: str, sel: str) -> str:
    """
    Website-agnostic semantic classification of interactive controls to guide
    information-seeking prioritization (avoiding flat DOM-order clicking).
    """
    combined = f"{name_clean} {sel} {role}".lower()

    if tag == "select" or role in ("combobox", "listbox"):
        return "primary_mutation"

    if any(k in combined for k in (
        "burger", "menu-btn", "menu_btn", "open menu", "close menu", "drawer", "sidebar", "close-menu",
        "cross-btn", "back-to-", "continue-shopping", "cancel", "dismiss"
    )):
        return "utility_drawer"

    if tag in ("button", "select", "a") or role in ("button", "combobox", "listbox", "link"):
        if tag == "select" or role in ("combobox", "listbox") or any(k in combined for k in (
            "add", "create", "save", "apply", "update", "filter", "sort",
            "search", "calculate", "enable", "toggle", "new"
        )):
            return "primary_mutation"

    if any(k in combined for k in (
        "checkout", "proceed", "next", "continue", "finish", "cart",
        "basket", "bag", "review", "confirm"
    )) or role == "tab" or ".tab" in sel:
        return "workflow_progression"

    if tag == "a" or role == "link" or "link" in sel:
        return "content_navigation"

    return "standard_control"


def generate_control_probes(obs: UIStateObservation) -> List[Tuple[ProbeScript, Prediction, Dict[str, Any]]]:
    """Generates control interaction probes for visible interactive buttons, selects, and action links."""
    probes: List[Tuple[ProbeScript, Prediction, Dict[str, Any]]] = []

    from urllib.parse import urlparse
    curr_path = urlparse(obs.url or "").path or "/"

    editable_forms = [
        f for f in (obs.forms or [])
        if any(
            (fld.get("type") or "").lower() not in ("submit", "button", "reset", "image", "hidden")
            for fld in f.get("fields", [])
        )
    ]
    button_only_submit_selectors = {
        f.get("submit_control_selector")
        for f in (obs.forms or [])
        if f not in editable_forms and f.get("submit_control_selector")
    }
    form_submit_selectors = {
        f.get("submit_control_selector") for f in editable_forms if f.get("submit_control_selector")
    }
    form_field_selectors = {
        fld.get("stable_selector")
        for f in editable_forms
        for fld in f.get("fields", [])
        if fld.get("stable_selector")
    }
    seen_selectors = set()
    pattern_counts: Dict[str, int] = {}
    label_counts: Dict[str, int] = {}

    # Check if page has text links so we can skip redundant image-only thumbnail links in cards
    has_text_links = any(
        c.tag_name.lower() == "a"
        and not (c.declared_constraints or {}).get("is_image_only_link", False)
        and "img_link" not in (c.selector or "").lower()
        and "img-link" not in (c.selector or "").lower()
        for c in obs.interactive_controls
    )

    # Filter out form submit buttons (covered by form probes) and session-ending logout links
    for ctrl in obs.interactive_controls[:30]:
        if not ctrl.is_enabled or not ctrl.is_visible:
            continue

        tag = ctrl.tag_name.lower()
        role = ctrl.role.lower()
        el_type = (ctrl.element_type or "").lower()
        if ctrl.selector in form_submit_selectors or ctrl.selector in form_field_selectors:
            continue
        if el_type == "submit" and ctrl.selector not in button_only_submit_selectors:
            continue
        if tag not in ("button", "a", "select") and role not in ("button", "link", "tab", "combobox") and el_type not in ("button", "submit"):
            continue

        sel = ctrl.selector
        if not sel or sel in seen_selectors:
            continue
        # Skip self-links pointing to the current page route
        if f'href="{curr_path}"' in sel or (curr_path == "/" and 'href="/"' in sel):
            continue
        if curr_path != "/" and curr_path.strip("/") and (
            sel.endswith(f"_{curr_path.strip('/').split('.')[0]}_sidebar_link")
            or f'href="{curr_path}"' in str((ctrl.declared_constraints or {}).get("href", ""))
        ):
            continue

        # Skip redundant image-only card links when text links are available on the view
        constraints = ctrl.declared_constraints or {}
        if has_text_links and (
            constraints.get("is_image_only_link")
            or "img_link" in sel.lower()
            or "img-link" in sel.lower()
        ):
            continue

        name_clean = ctrl.accessible_name or sel or "ctrl"
        href_val = str(constraints.get("href") or "")
        lower_combined = f"{name_clean} {sel} {href_val}".lower()
        if any(w in lower_combined for w in ("logout", "log out", "sign out", "signout", "reset app")):
            continue

        # Skip external social links that lead away from application under test
        if any(w in lower_combined for w in ("twitter.com", "facebook.com", "linkedin.com", "x.com", "instagram.com", "youtube.com", "social-")):
            continue

        # Website-agnostic equivalence class sampling:
        # Determine if this control belongs to a cart/item accumulation or modifier workflow
        is_add_or_cart = any(k in lower_combined for k in ("add-to-cart", "add to cart", "cart-add", "buy"))
        is_qty_or_mod = any(k in lower_combined for k in ("cart-qty", "qty", "quantity", "plus", "minus", "increase", "decrease", "remove"))

        # 1. Normalize numeric indices and slugified item identifiers
        normalized_pattern = re.sub(r'\d+', 'N', sel)
        normalized_pattern = re.sub(
            r'(add-to-cart|remove|delete|edit|view|item|product|card|row)[-_][a-z0-9\-_]+',
            r'\1_item',
            normalized_pattern,
            flags=re.IGNORECASE,
        )
        if is_add_or_cart:
            max_per_pattern = 2
        elif is_qty_or_mod:
            max_per_pattern = 3
        else:
            max_per_pattern = 1 if ("_item" in normalized_pattern or "item_N" in normalized_pattern or "item-N" in normalized_pattern) else 2

        if pattern_counts.get(normalized_pattern, 0) >= max_per_pattern:
            continue

        # 2. Cap identical button action labels (up to 2 for add-to-cart accumulation, up to 3 for modifiers)
        norm_label = f"{tag}:{role}:{name_clean.strip().lower()}"
        max_for_label = 3 if is_qty_or_mod else (2 if is_add_or_cart else 1)
        if tag == "button" and name_clean and name_clean != sel:
            if label_counts.get(norm_label, 0) >= max_for_label:
                continue
            label_counts[norm_label] = label_counts.get(norm_label, 0) + 1

        pattern_counts[normalized_pattern] = pattern_counts.get(normalized_pattern, 0) + 1
        seen_selectors.add(sel)

        semantic_tier = _classify_control_semantics(tag, role, name_clean, sel)
        clean_slug = re.sub(r"[^a-zA-Z0-9_]", "_", f"{obs.state_class_id}_{sel}")[:48]

        if tag == "select":
            raw_options = constraints.get("select_options") or []
            options = [
                (opt.get("value") or opt.get("text") or "") if isinstance(opt, dict) else str(opt)
                for opt in raw_options
            ]
            options = [o for o in options if o]
            # Pick a non-default option (index 1) if available so the selection mutates the view
            chosen_opt = options[1] if len(options) > 1 else (options[0] if options else "")
            if not chosen_opt:
                continue
            target = SemanticTarget(role="combobox", name=ctrl.accessible_name if ctrl.accessible_name != sel else None, css=sel)
            script = ProbeScript(
                name=f"select_{clean_slug}",
                family="controls",
                description=f"Selects option '{chosen_opt}' in dropdown '{name_clean}' ({sel}) to evaluate sorting/filtering effect",
                steps=[
                    ProbeStep(
                        action_type="SELECT_OPTION",
                        target=target,
                        raw_target=sel,
                        value_spec=ValueSpec(kind=ValueSpecType.LITERAL, value=chosen_opt),
                    )
                ],
            )
            pred = Prediction(
                expect=f"Selecting option '{chosen_opt}' updates view ordering or displayed items",
                signals=["no_server_error"],
            )
        else:
            # ctrl.role falls back to the tag name ("a", "button"); map to a valid ARIA role so get_by_role can resolve it
            aria_role = role if role in ("button", "link", "tab", "menuitem", "checkbox", "switch") else (
                "link" if tag == "a" else ("button" if tag == "button" or el_type == "button" else None)
            )
            target = SemanticTarget(role=aria_role, name=ctrl.accessible_name if aria_role else None, css=sel)
            script = ProbeScript(
                name=f"control_{clean_slug}",
                family="controls",
                description=f"Clicks control '{name_clean}' ({sel}) to evaluate observed effect",
                steps=[ProbeStep(action_type="CLICK_CONTROL", target=target, raw_target=sel)],
            )
            pred = Prediction(
                expect="Control interaction produces state mutation or navigation",
                signals=["no_server_error"]
            )

        probes.append((script, pred, {
            "kind": "control_effect",
            "control_id": ctrl.element_id,
            "selector": sel,
            "target": sel,
            "role": ctrl.role,
            "name": name_clean,
            "semantic_tier": semantic_tier,
        }))

    return probes
