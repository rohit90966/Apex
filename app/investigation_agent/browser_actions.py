"""
Safe, sandboxed browser action executor for stateful investigation.
Executes approved, bounded interactions (click, navigate, fill, select, check, press, submit)
and measures before-and-after observations without executing arbitrary code.

Supports:
- SemanticTarget resolution order: get_by_role -> get_by_label -> css fallback.
- Action vocabulary: NAVIGATE_LINK, CLICK_CONTROL, FILL, SELECT_OPTION, CHECK, UNCHECK,
  PRESS, SUBMIT, SET_NOVALIDATE, RELOAD, GO_BACK.
- Pre-flight live DOM compatibility checks to eliminate blind timeouts.
"""

import time
import logging
from typing import Dict, Any, Tuple, Optional, Union
from urllib.parse import urljoin
from playwright.sync_api import Locator, Page
from app.config import get_settings
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.models import (
    ActionFailureReason,
    SemanticTarget,
)
from app.investigation_agent.governor import SafetyGovernor
from app.safety import validate_url_safety, SafetyViolation

logger = logging.getLogger(__name__)


def resolve_semantic_target(page: Page, target: Union[SemanticTarget, str]) -> Tuple[Optional[Locator], str]:
    """
    Resolves target using the strict order:
    1. get_by_role with accessible name
    2. get_by_label
    3. css selector
    Returns (locator, strategy_used).
    """
    if isinstance(target, str):
        return page.locator(target), "css"

    if not isinstance(target, SemanticTarget):
        return None, "invalid_target"

    # Prefer exact ID / data-attribute / name CSS selector when present and visible in DOM
    if target.css and (target.css.startswith("#") or target.css.startswith("[data-") or "[name=" in target.css):
        try:
            loc = page.locator(target.css)
            if loc.count() > 0 and loc.first.is_visible():
                target.resolved_strategy = "css"
                return loc.first, "css"
        except Exception:
            pass

    # 1. get_by_role with name
    if target.role and target.name:
        try:
            loc = page.get_by_role(target.role, name=target.name)
            if loc.count() > 0 and loc.first.is_visible():
                target.resolved_strategy = "role_name"
                return loc.first, "role_name"
        except Exception:
            pass

    # 2. get_by_label
    if target.label:
        try:
            loc = page.get_by_label(target.label)
            if loc.count() > 0 and loc.first.is_visible():
                target.resolved_strategy = "label"
                return loc.first, "label"
        except Exception:
            pass

    # 3. CSS fallback
    if target.css:
        try:
            loc = page.locator(target.css)
            target.resolved_strategy = "css"
            return loc.first, "css"
        except Exception:
            pass

    return None, "unresolved"


def _urls_compatible(current_url: str, required_url: str) -> bool:
    """Checks if the current browser URL is compatible with the required state URL."""
    if not required_url or not current_url:
        return True
    from urllib.parse import parse_qsl, urlsplit

    def key(url: str, with_host: bool):
        parts = urlsplit(url.split("#")[0])
        path = parts.path.rstrip("/") or "/"
        query = tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))  # query is state: order-insensitive
        return ((parts.netloc.lower(),) if with_host else ()) + (path, query)

    # Exact page identity (path + query); the host is compared when both URLs carry one.
    with_host = "://" in required_url and "://" in current_url
    return key(current_url, with_host) == key(required_url, with_host)


class InvestigationActionExecutor:
    """
    Executes controlled, safe browser actions.
    Enforces action safety policies and bounded timeouts.
    """

    @classmethod
    def validate_action_safety(cls, action_type: str, target: str, rationale: str = "") -> Tuple[bool, str]:
        """
        Validates proposed interaction against safety constraints using SafetyGovernor.
        """
        target_clean = str(target).strip()
        if action_type == "NAVIGATE_LINK" and (
            target_clean.startswith("[") or target_clean.startswith(".") or (target_clean.startswith("#") and " " in target_clean)
        ):
            return False, f"Action blocked: target '{target_clean}' is a DOM selector, not a URL"
        governor = SafetyGovernor()
        return governor.can_execute(action_type=action_type, target=target, current_url="", rationale=rationale)

    @classmethod
    def execute_action(
        cls,
        session: StatefulBrowserSession,
        action_type: str,
        target: Union[str, SemanticTarget],
        value: Optional[str] = None,
        key: Optional[str] = None,
        timeout_ms: int = 3000,
        required_url: Optional[str] = None,
        required_state_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[ActionFailureReason], Dict[str, Any]]:
        """
        Executes bounded Playwright action with pre-flight current-state DOM checks.
        Returns (success, outcome_summary, failure_reason, telemetry).
        """
        p = session.page
        t0 = time.time()
        telemetry: Dict[str, Any] = {}

        # 0. Precondition Check: URL / State Compatibility
        if required_url and p and p.url:
            if not _urls_compatible(p.url, required_url):
                duration = round(time.time() - t0, 2)
                err_msg = f"Unsatisfied precondition: Current browser URL '{p.url}' does not match required state URL '{required_url}'"
                logger.warning("[InvestigationActionExecutor] %s", err_msg)
                return False, err_msg, ActionFailureReason.PRECONDITION_FAILED, {
                    "duration": duration,
                    "current_url": p.url,
                    "required_url": required_url,
                }

        # Convert target to string selector or resolve locator
        target_str = target.css if isinstance(target, SemanticTarget) and target.css else (
            target.name or target.label or "target" if isinstance(target, SemanticTarget) else str(target)
        )

        try:
            # 1. NAVIGATE_LINK
            if action_type == "NAVIGATE_LINK":
                target_clean = str(target).strip()

                # Selector check
                if target_clean.startswith("[") or target_clean.startswith(".") or (target_clean.startswith("#") and " " in target_clean):
                    return False, f"Target '{target_clean}' is a DOM selector, cannot navigate as URL", ActionFailureReason.NAVIGATION_REJECTED, {"duration": 0.0}

                # Fragment link
                if target_clean.startswith("#"):
                    ok, res_url, status = session.navigate(target_clean)
                    duration = round(time.time() - t0, 2)
                    if ok:
                        return True, f"Navigated to in-page fragment {target_clean}", None, {"duration": duration, "url_after": p.url}
                    else:
                        return False, res_url, ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                # Relative link resolution
                target_url = target_clean
                if target_clean.startswith("/"):
                    target_url = urljoin(p.url, target_clean)

                ok, res_url, status = session.navigate(target_url)
                duration = round(time.time() - t0, 2)

                if not ok:
                    if "SAFETY_BLOCKED" in res_url:
                        return False, res_url, ActionFailureReason.SAFETY_BLOCKED, {"duration": duration, "error": res_url}
                    return False, f"Navigation failed: {res_url}", ActionFailureReason.NAVIGATION_REJECTED, {"duration": duration, "error": res_url}

                return True, f"Navigated link to {p.url}", None, {"duration": duration, "url_after": p.url}

            # 2. CLICK_CONTROL
            elif action_type == "CLICK_CONTROL":
                locator, strat = resolve_semantic_target(p, target)
                if not locator:
                    return False, f"Target '{target_str}' could not be resolved", ActionFailureReason.TARGET_ABSENT, {"duration": 0.0}

                # Pre-flight resolution if CSS selector available
                css_candidate = target if isinstance(target, str) else (target.css if isinstance(target, SemanticTarget) and target.css else None)
                if css_candidate:
                    resolution = session.resolve_target(css_candidate)
                    if not resolution.get("present"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' not present in current DOM ({resolution.get('reason')})", ActionFailureReason.TARGET_ABSENT, {"duration": duration}
                    if not resolution.get("visible"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' is present but not visible", ActionFailureReason.STALE_TARGET, {"duration": duration}
                    if not resolution.get("enabled"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' is disabled", ActionFailureReason.TARGET_DISABLED, {"duration": duration}

                # Ambiguity check: reject ambiguous locators that match multiple visible elements
                try:
                    match_count = locator.count()
                    if match_count > 1:
                        visible_locators = [locator.nth(i) for i in range(match_count) if locator.nth(i).is_visible()]
                        if len(visible_locators) == 1:
                            locator = visible_locators[0]
                        else:
                            duration = round(time.time() - t0, 2)
                            return False, f"Target '{target_str}' is ambiguous ({len(visible_locators)} visible matches found)", ActionFailureReason.TARGET_AMBIGUOUS, {"duration": duration}
                except Exception:
                    pass

                try:
                    locator.click(timeout=timeout_ms)
                    session.settle()
                except Exception as click_err:
                    duration = round(time.time() - t0, 2)
                    err_str = str(click_err)
                    # If click was intercepted by a drawer/backdrop overlay, dismiss overlay and retry once
                    if "intercepts pointer events" in err_str or "obscured" in err_str or "obscures" in err_str:
                        try:
                            p.evaluate("""() => {
                                const overlays = document.querySelectorAll('.bm-overlay, .modal-backdrop, .overlay, .drawer-overlay');
                                overlays.forEach(el => el.remove());
                                const closeBtns = document.querySelectorAll('#react-burger-cross-btn, .close-button, .btn-close, [aria-label="Close"]');
                                if (closeBtns.length > 0) closeBtns[0].click();
                            }""")
                            session.settle()
                            locator.click(timeout=1500)
                            session.settle()
                            duration = round(time.time() - t0, 2)
                            return True, f"Clicked control '{target_str}' via {strat} (after clearing overlay)", None, {"duration": duration, "url_after": p.url, "strategy": strat}
                        except Exception:
                            pass
                    reason = ActionFailureReason.TIMEOUT if "Timeout" in err_str else ActionFailureReason.EXECUTION_ERROR
                    return False, f"Click failed: {err_str[:120]}", reason, {"duration": duration, "error": err_str}

                duration = round(time.time() - t0, 2)
                return True, f"Clicked control '{target_str}' via {strat}", None, {"duration": duration, "url_after": p.url, "strategy": strat}

            # 3. FILL
            elif action_type == "FILL":
                # Fast pre-flight check for obviously non-fillable controls
                target_lower = target_str.lower()
                if ("type='submit'" in target_lower or 'type="submit"' in target_lower or "type=submit" in target_lower
                        or ("button" in target_lower and "input" in target_lower)):
                    duration = round(time.time() - t0, 2)
                    return False, f"Incompatible action: Target '{target_str}' is input[type='submit'], cannot be filled", ActionFailureReason.INCOMPATIBLE_ACTION, {"duration": duration}

                locator, strat = resolve_semantic_target(p, target)
                if not locator:
                    return False, f"Target '{target_str}' could not be resolved for FILL", ActionFailureReason.TARGET_ABSENT, {"duration": 0.0}

                # Pre-flight resolution if CSS selector available
                css_candidate = target if isinstance(target, str) else (target.css if isinstance(target, SemanticTarget) and target.css else None)
                if css_candidate:
                    resolution = session.resolve_target(css_candidate)
                    if not resolution.get("present"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' not present in current DOM ({resolution.get('reason')})", ActionFailureReason.TARGET_ABSENT, {"duration": duration}
                    if not resolution.get("visible"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' is present but not visible", ActionFailureReason.STALE_TARGET, {"duration": duration}
                    if not resolution.get("enabled"):
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{css_candidate}' is disabled", ActionFailureReason.TARGET_DISABLED, {"duration": duration}

                try:
                    if locator.count() == 0:
                        duration = round(time.time() - t0, 2)
                        return False, f"Target '{target_str}' not found in current DOM", ActionFailureReason.TARGET_ABSENT, {"duration": duration}
                except Exception:
                    pass

                # Pre-flight Action Compatibility Layer
                try:
                    el_info = locator.first.evaluate("""(el) => {
                        return {
                            tag: el.tagName.toLowerCase(),
                            type: (el.getAttribute('type') || '').toLowerCase(),
                            isContentEditable: !!el.isContentEditable,
                            disabled: !!(el.disabled || el.hasAttribute('disabled')),
                            readOnly: !!(el.readOnly || el.hasAttribute('readonly'))
                        };
                    }""")
                    if isinstance(el_info, dict):
                        tag = el_info.get("tag", "")
                        el_type = el_info.get("type", "")
                        if tag not in ("input", "textarea") and not el_info.get("isContentEditable"):
                            duration = round(time.time() - t0, 2)
                            return False, f"Incompatible action: Target '{target_str}' is <{tag}>, not an editable input", ActionFailureReason.INCOMPATIBLE_ACTION, {"duration": duration}
                        if tag == "input" and el_type in ("submit", "button", "reset", "image", "checkbox", "radio", "file"):
                            duration = round(time.time() - t0, 2)
                            return False, f"Incompatible action: Target '{target_str}' is input[type='{el_type}'], cannot be filled", ActionFailureReason.INCOMPATIBLE_ACTION, {"duration": duration}
                except Exception:
                    pass

                fill_value = value if value is not None else ""
                try:
                    locator.fill(fill_value, timeout=timeout_ms)
                    session.settle()
                except Exception as fill_err:
                    duration = round(time.time() - t0, 2)
                    err_str = str(fill_err)
                    reason = ActionFailureReason.TIMEOUT if "Timeout" in err_str else ActionFailureReason.EXECUTION_ERROR
                    return False, f"Fill failed: {err_str[:120]}", reason, {"duration": duration, "error": err_str}

                duration = round(time.time() - t0, 2)
                return True, f"Filled '{target_str}' with '{fill_value}' via {strat}", None, {"duration": duration, "strategy": strat}

            # 4. SELECT_OPTION
            elif action_type == "SELECT_OPTION":
                locator, strat = resolve_semantic_target(p, target)
                if not locator:
                    return False, f"Target '{target_str}' could not be resolved for SELECT", ActionFailureReason.TARGET_ABSENT, {"duration": 0.0}

                opt_val = value if value is not None else ""
                try:
                    locator.select_option(opt_val, timeout=timeout_ms)
                    session.settle()
                except Exception as sel_err:
                    duration = round(time.time() - t0, 2)
                    return False, f"Select option failed: {str(sel_err)[:120]}", ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                duration = round(time.time() - t0, 2)
                return True, f"Selected option '{opt_val}' on '{target_str}' via {strat}", None, {"duration": duration, "strategy": strat}

            # 5. CHECK
            elif action_type == "CHECK":
                locator, strat = resolve_semantic_target(p, target)
                if not locator:
                    return False, f"Target '{target_str}' could not be resolved for CHECK", ActionFailureReason.TARGET_ABSENT, {"duration": 0.0}
                try:
                    locator.check(timeout=timeout_ms)
                    session.settle()
                except Exception as chk_err:
                    duration = round(time.time() - t0, 2)
                    return False, f"Check failed: {str(chk_err)[:120]}", ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                duration = round(time.time() - t0, 2)
                return True, f"Checked '{target_str}' via {strat}", None, {"duration": duration, "strategy": strat}

            # 6. UNCHECK
            elif action_type == "UNCHECK":
                locator, strat = resolve_semantic_target(p, target)
                if not locator:
                    return False, f"Target '{target_str}' could not be resolved for UNCHECK", ActionFailureReason.TARGET_ABSENT, {"duration": 0.0}
                try:
                    locator.uncheck(timeout=timeout_ms)
                    session.settle()
                except Exception as unchk_err:
                    duration = round(time.time() - t0, 2)
                    return False, f"Uncheck failed: {str(unchk_err)[:120]}", ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                duration = round(time.time() - t0, 2)
                return True, f"Unchecked '{target_str}' via {strat}", None, {"duration": duration, "strategy": strat}

            # 7. PRESS
            elif action_type == "PRESS":
                press_key = key or value or "Enter"
                locator, strat = resolve_semantic_target(p, target)
                try:
                    if locator and locator.count() > 0:
                        locator.press(press_key, timeout=timeout_ms)
                    else:
                        p.keyboard.press(press_key)
                    session.settle()
                except Exception as press_err:
                    duration = round(time.time() - t0, 2)
                    return False, f"Key press failed: {str(press_err)[:120]}", ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                duration = round(time.time() - t0, 2)
                return True, f"Pressed key '{press_key}'", None, {"duration": duration}

            # 8. SUBMIT
            elif action_type == "SUBMIT":
                # Prefer clicking resolved forward-progression submit button, excluding cancel/back controls
                form_selector = target_str if (target_str and target_str != "#form" and (target_str.startswith("#") or target_str.startswith("form"))) else "form"
                try:
                    cancel_filter = ":not([id*='cancel']):not([name*='cancel']):not([class*='cancel']):not([value*='Cancel']):not([value*='cancel']):not([id*='back']):not([name*='back']):not([class*='back'])"
                    sub_btn = p.locator(
                        f"{form_selector} input[type=submit]{cancel_filter}, "
                        f"{form_selector} button[type=submit]{cancel_filter}, "
                        f"{form_selector} input[name*='continue']{cancel_filter}, "
                        f"{form_selector} button[name*='continue']{cancel_filter}, "
                        f"{form_selector} button[id*='continue']{cancel_filter}, "
                        f"{form_selector} button:not([type=button]){cancel_filter}, "
                        f"input[type=submit]{cancel_filter}, "
                        f"button[type=submit]{cancel_filter}"
                    )
                    matched_sub = None
                    if sub_btn.count() > 0:
                        for i in range(sub_btn.count()):
                            cand = sub_btn.nth(i)
                            if cand.is_visible():
                                txt = (cand.inner_text() or cand.get_attribute("value") or "").strip().lower()
                                if any(bad in txt for bad in ("cancel", "back", "abort", "dismiss", "reset")):
                                    continue
                                matched_sub = cand
                                break

                    if matched_sub:
                        matched_sub.click(timeout=timeout_ms)
                    else:
                        p.evaluate("""(sel) => {
                            let f = document.querySelector(sel);
                            if (!f) f = document.querySelector('form');
                            if (f) {
                                if (typeof f.requestSubmit === 'function') f.requestSubmit();
                                else f.submit();
                            }
                        }""", form_selector)
                    session.settle()
                except Exception as sub_err:
                    duration = round(time.time() - t0, 2)
                    return False, f"Submit failed: {str(sub_err)[:120]}", ActionFailureReason.EXECUTION_ERROR, {"duration": duration}

                duration = round(time.time() - t0, 2)
                return True, f"Submitted form '{form_selector}'", None, {"duration": duration, "url_after": p.url}

            # 9. SET_NOVALIDATE
            elif action_type == "SET_NOVALIDATE":
                form_selector = target_str if (target_str and target_str != "#form") else "form"
                try:
                    p.evaluate("""(sel) => {
                        let forms = sel ? document.querySelectorAll(sel) : [];
                        if (!forms || forms.length === 0) forms = document.querySelectorAll('form');
                        for (const f of forms) {
                            f.noValidate = true;
                            f.setAttribute('novalidate', '');
                        }
                    }""", form_selector)
                except Exception as e:
                    return False, f"Failed to set noValidate: {str(e)[:100]}", ActionFailureReason.EXECUTION_ERROR, {}

                duration = round(time.time() - t0, 2)
                return True, f"Set novalidate=true on form '{form_selector}'", None, {"duration": duration}

            # 10. RELOAD
            elif action_type == "RELOAD":
                p.reload()
                session.settle()
                duration = round(time.time() - t0, 2)
                return True, f"Reloaded page {p.url}", None, {"duration": duration}

            # 11. GO_BACK
            elif action_type == "GO_BACK":
                p.go_back()
                session.settle()
                duration = round(time.time() - t0, 2)
                return True, f"Navigated back to {p.url}", None, {"duration": duration}

            # 12. OBSERVE / WAIT / WAIT_FOR_LOAD
            elif action_type in ("OBSERVE", "WAIT", "WAIT_FOR_LOAD"):
                session.settle()
                duration = round(time.time() - t0, 2)
                return True, f"Observed page state at {p.url}", None, {"duration": duration, "url_after": p.url}

            else:
                return False, f"Unsupported action type: {action_type}", ActionFailureReason.EXECUTION_ERROR, {}

        except Exception as e:
            duration = round(time.time() - t0, 2)
            err = f"Action execution error: {str(e)[:120]}"
            return False, err, ActionFailureReason.EXECUTION_ERROR, {"duration": duration, "error": err}
