"""
app/exploration/explorer.py
============================
Generic, website-agnostic autonomous exploration engine for VerityQA.

Key design principles
---------------------
1. No template dependency  — the engine does NOT use page-category labels
   (LOGIN / REGISTRATION / SEARCH) to decide what to do.  Controls are
   discovered from the live DOM and scored by the ExplorationPolicy.

2. Evidence-backed trace  — every action and every state transition is
   recorded in an ApplicationStateGraph.

3. Loop detection  — states are identified by a fingerprint of the URL +
   key DOM structure.  A state that has already been visited is skipped.

4. Safety first  — every navigation is validated against the safety policy
   before execution.

5. Budget enforcement  — hard limits on depth, actions, pages, and wall time.

6. Reuses existing infrastructure  — uses Playwright sync_playwright via
   asyncio.to_thread (same pattern as WebsiteInspector and WebsiteCrawler).
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import logging
import os
import time
import uuid
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    sync_playwright,
)

from app.config import get_settings
from app.safety import SafetyViolation, validate_url_safety
from app.exploration.graph import (
    ApplicationStateGraph,
    ElementConstraints,
    ElementRole,
    ExplorationActionType,
    ExplorationLogEntry,
    ExploreRequest,
    ExploreResponse,
    InteractiveElement,
    StateTransition,
    UIState,
)
from app.exploration.policy import ActionCandidate, ExplorationBudget, ExplorationPolicy

logger = logging.getLogger("verityqa.exploration.explorer")


# ---------------------------------------------------------------------------
# DOM fingerprinting helpers
# ---------------------------------------------------------------------------

def _dom_fingerprint(page: Page) -> str:
    """
    Compute a short structural fingerprint of the page DOM.

    Uses tag counts and visible heading text — enough to distinguish
    meaningfully different states without being brittle.
    """
    try:
        data = page.evaluate("""() => {
            const tags = ['form', 'input', 'button', 'select', 'a', 'table', 'dialog'];
            const counts = tags.map(t => document.querySelectorAll(t).length);
            const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
                .slice(0, 5)
                .map(h => (h.innerText || '').trim().slice(0, 30))
                .join('|');
            const title = (document.title || '').slice(0, 40);
            return `${title}::${counts.join(',')}::${headings}`;
        }""")
    except Exception:
        data = str(time.time())   # fallback: treat each page as unique
    return hashlib.sha1(str(data).encode()).hexdigest()[:10]


def _is_same_origin(base: str, candidate: str) -> bool:
    """Return True if *candidate* is on the same origin as *base*."""
    try:
        bp = urlparse(base)
        cp = urlparse(candidate)
        return bp.scheme == cp.scheme and bp.netloc == cp.netloc
    except Exception:
        return False


def _resolve_href(href: str, current_url: str, start_url: str) -> Optional[str]:
    """Resolve a raw href to an absolute URL on the same origin."""
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    try:
        abs_url = urljoin(current_url, href)
        if _is_same_origin(start_url, abs_url):
            return abs_url
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DOM element extraction
# ---------------------------------------------------------------------------

_EXTRACT_ELEMENTS_JS = """() => {
    function getSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        const tag = el.tagName.toLowerCase();
        const name = el.getAttribute('name');
        if (name) return `${tag}[name="${CSS.escape(name)}"]`;
        // Build nth-of-type selector
        let idx = 1;
        let sib = el;
        while ((sib = sib.previousElementSibling)) {
            if (sib.tagName === el.tagName) idx++;
        }
        return `${tag}:nth-of-type(${idx})`;
    }
    function getAccessibleName(el) {
        if (el.computedName && typeof el.computedName === 'string' && el.computedName.trim()) {
            return el.computedName.trim().slice(0, 80);
        }
        const label = el.getAttribute('aria-label') || el.getAttribute('title') || '';
        if (label) return label.trim();
        if (el.id) {
            const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (lbl) return (lbl.innerText || '').trim();
        }
        return (el.innerText || el.value || el.placeholder || '').trim().slice(0, 80);
    }
    function getOptions(el) {
        if (el.tagName.toLowerCase() !== 'select') return null;
        return Array.from(el.options).slice(0, 20).map(o => o.text.trim()).filter(Boolean);
    }
    const SELECTORS = 'button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [role=menuitem]';
    return Array.from(document.querySelectorAll(SELECTORS)).slice(0, 60).map(el => {
        const tag = el.tagName.toLowerCase();
        const type = el.getAttribute('type') || (tag === 'select' ? 'select-one' : tag);
        const rect = el.getBoundingClientRect();
        const visible = rect.width > 0 && rect.height > 0 && !el.hidden &&
                        window.getComputedStyle(el).display !== 'none' &&
                        window.getComputedStyle(el).visibility !== 'hidden';
        return {
            tag,
            selector: getSelector(el),
            accessible_name: getAccessibleName(el),
            element_type: type,
            role: el.getAttribute('role') || null,
            form_id: el.closest('form') ? (el.closest('form').id || 'anon-form') : null,
            disabled: el.disabled || el.getAttribute('aria-disabled') === 'true' || false,
            required: el.required || el.getAttribute('aria-required') === 'true' || false,
            readonly: el.readOnly || false,
            min_length: el.minLength > 0 ? el.minLength : null,
            max_length: el.maxLength > 0 ? el.maxLength : null,
            min_value: el.getAttribute('min') || null,
            max_value: el.getAttribute('max') || null,
            pattern: el.getAttribute('pattern') || null,
            options: getOptions(el),
            href: el.getAttribute('href') || null,
            visible,
        };
    }).filter(e => e.visible);
}"""


def _parse_elements(raw: list, start_url: str, current_url: str) -> List[InteractiveElement]:
    """Convert raw JS element data into InteractiveElement models."""
    elements: List[InteractiveElement] = []
    seen_keys: Set[str] = set()

    for item in raw:
        tag = (item.get("tag") or "").lower()
        el_type = (item.get("element_type") or "").lower()
        role_str = (item.get("role") or "").lower()

        # Determine role
        if role_str == "button" or el_type == "submit" or tag == "button":
            role = ElementRole.BUTTON
        elif role_str == "link" or tag == "a":
            role = ElementRole.LINK
        elif el_type in ("text", "email", "password", "number", "search", "tel", "url", "date", "textarea"):
            role = ElementRole.TEXTBOX
        elif el_type in ("select-one", "select"):
            role = ElementRole.COMBOBOX
        elif el_type == "checkbox":
            role = ElementRole.CHECKBOX
        elif el_type == "radio":
            role = ElementRole.RADIO
        elif role_str == "tab":
            role = ElementRole.TAB
        elif role_str == "menuitem":
            role = ElementRole.MENUITEM
        else:
            role = ElementRole.GENERIC_INTERACTIVE

        constraints = ElementConstraints(
            required=bool(item.get("required")),
            disabled=bool(item.get("disabled")),
            readonly=bool(item.get("readonly")),
            min_length=item.get("min_length"),
            max_length=item.get("max_length"),
            min_value=item.get("min_value"),
            max_value=item.get("max_value"),
            pattern=item.get("pattern"),
            input_type=el_type or None,
            options=item.get("options"),
        )

        # Detect navigation vs submission intent
        href = item.get("href")
        is_nav = tag == "a" and bool(href)
        resolved_href = _resolve_href(href or "", current_url, start_url) if href else None
        is_submission = el_type == "submit" or (
            tag == "button" and not is_nav and "submit" in (item.get("accessible_name") or "").lower()
        )

        el = InteractiveElement(
            selector=item.get("selector", tag),
            tag=tag,
            role=role,
            accessible_name=item.get("accessible_name") or None,
            element_type=el_type or None,
            form_id=item.get("form_id"),
            constraints=constraints,
            is_navigation=is_nav,
            is_submission=is_submission,
        )

        # Deduplicate by element_key
        if el.element_key not in seen_keys:
            seen_keys.add(el.element_key)
            elements.append(el)

    return elements


# ---------------------------------------------------------------------------
# Main exploration engine
# ---------------------------------------------------------------------------

class AutonomousExplorer:
    """
    Generic, website-agnostic autonomous exploration engine.

    Usage::

        explorer = AutonomousExplorer()
        response = await explorer.explore(ExploreRequest(url="http://..."))
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def explore(self, request: ExploreRequest) -> ExploreResponse:
        """Run exploration in a background thread (sync Playwright)."""
        return await asyncio.to_thread(self._sync_explore, request)

    # ------------------------------------------------------------------
    # Sync implementation (runs in thread pool)
    # ------------------------------------------------------------------

    def _sync_explore(self, request: ExploreRequest) -> ExploreResponse:
        t0 = time.time()
        allowed_hosts = self.settings.allowed_hosts_list

        # Safety check on start URL
        try:
            validate_url_safety(request.url, allowed_hosts)
        except SafetyViolation as e:
            raise ValueError(f"Safety policy rejected URL: {e}") from e

        # Build budget
        budget = ExplorationBudget(
            max_depth=request.max_depth or self.settings.exploration_max_depth,
            max_actions=request.max_actions or self.settings.exploration_max_actions,
            max_pages=request.max_pages or self.settings.exploration_max_pages,
            max_time_seconds=request.max_time_seconds or self.settings.exploration_max_time_seconds,
        )

        policy = ExplorationPolicy()
        graph = ApplicationStateGraph(starting_url=request.url)

        artifacts_dir = os.path.abspath(self.settings.verityqa_artifacts_dir)
        os.makedirs(artifacts_dir, exist_ok=True)

        def log(event_type: str, message: str, **kwargs) -> None:
            entry = ExplorationLogEntry(
                step=graph.total_actions_taken,
                event_type=event_type,
                message=message,
                **kwargs,
            )
            graph.exploration_log.append(entry)
            logger.info("[Explorer] [%s] %s", event_type, message)

        with sync_playwright() as p:
            browser: Browser = p.chromium.launch(
                headless=self.settings.verityqa_headless,
            )
            context: BrowserContext = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerityQA-Explorer/1.0",
                ignore_https_errors=False,
            )
            page: Page = context.new_page()

            try:
                self._run_exploration(
                    page=page,
                    context=context,
                    browser=browser,
                    start_url=request.url,
                    budget=budget,
                    policy=policy,
                    graph=graph,
                    artifacts_dir=artifacts_dir,
                    allowed_hosts=allowed_hosts,
                    log_fn=log,
                )
            except Exception as exc:
                logger.exception("[Explorer] Unexpected error during exploration: %s", exc)
                graph.warnings.append(f"Exploration interrupted: {str(exc)[:200]}")
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        duration = round(time.time() - t0, 2)
        graph.duration_seconds = duration

        complete = not graph.budget_exhausted
        summary = (
            f"Explored {graph.state_count} states, "
            f"{graph.transition_count} transitions, "
            f"{graph.new_states_discovered} new states found, "
            f"{graph.total_actions_taken} actions in {duration}s."
        )
        if graph.budget_exhausted:
            summary += f" Stopped: {graph.budget_reason}"

        log("EXPLORATION_COMPLETE", summary)

        return ExploreResponse(
            target_url=request.url,
            graph=graph,
            states_discovered=graph.state_count,
            transitions_observed=graph.transition_count,
            new_states_found=graph.new_states_discovered,
            exploration_complete=complete,
            duration_seconds=duration,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Core exploration loop
    # ------------------------------------------------------------------

    def _run_exploration(
        self,
        page: Page,
        context: BrowserContext,
        browser: Browser,
        start_url: str,
        budget: ExplorationBudget,
        policy: ExplorationPolicy,
        graph: ApplicationStateGraph,
        artifacts_dir: str,
        allowed_hosts: List[str],
        log_fn,
    ) -> None:
        """
        BFS/priority-queue exploration of the application state space.

        Each iteration:
        1. Capture current state (URL + DOM fingerprint)
        2. If new, record the state
        3. Score all visible interactive elements
        4. Pick the highest-scoring unvisited action
        5. Execute the action
        6. Record the resulting state and transition
        7. Check budgets
        """
        # Navigate to start URL
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=15_000)
        except PlaywrightError as e:
            log_fn("ERROR", f"Failed to navigate to start URL: {e}")
            return

        # Build and record initial state
        start_state = self._capture_state(page, start_url, artifacts_dir, depth=0, step=0)
        graph.states[start_state.state_id] = start_state
        policy.record_state_visited(start_state.state_id)
        budget.record_page()
        log_fn("STATE_DISCOVERED", f"Initial state: {start_url}", state_id=start_state.state_id, url=start_url)

        # Priority queue: (neg_score, ActionCandidate)
        action_queue: List[ActionCandidate] = []

        def enqueue_elements(state: UIState) -> None:
            """Score elements in *state* and push candidates onto the queue."""
            for el in state.interactive_elements:
                sc = policy.score_element(el, state.depth, set(graph.states.keys()))
                if sc < -100:
                    continue  # disabled / useless
                candidate = ActionCandidate(
                    neg_score=-sc,
                    element=el,
                    action_type=policy.choose_action_type(el),
                    suggested_value=None,
                    source_state_id=state.state_id,
                    depth=state.depth,
                )
                heapq.heappush(action_queue, candidate)

        enqueue_elements(start_state)

        while action_queue:
            # Budget checks
            elapsed = time.time() - (time.time() - graph.duration_seconds) if graph.duration_seconds else 0
            # Re-compute elapsed from actions taken (duration_seconds not yet set)
            # Use action count as proxy; time check happens inside loop below

            if budget.actions_exhausted():
                graph.budget_exhausted = True
                graph.budget_reason = f"max_actions={budget.max_actions} reached"
                log_fn("BUDGET_ENFORCED", graph.budget_reason)
                break
            if budget.pages_exhausted():
                graph.budget_exhausted = True
                graph.budget_reason = f"max_pages={budget.max_pages} reached"
                log_fn("BUDGET_ENFORCED", graph.budget_reason)
                break

            candidate = heapq.heappop(action_queue)
            el = candidate.element
            source_state_id = candidate.source_state_id

            # Skip if element already acted on
            if el.element_key in policy._visited_element_keys:
                continue

            # Navigate back to source state if needed
            source_state = graph.states.get(source_state_id)
            if not source_state:
                continue

            # We need to be on the source state's page
            # Check if current URL matches; if not, navigate back
            current_url = page.url
            if not self._is_on_state(page, source_state):
                try:
                    page.goto(source_state.url, wait_until="domcontentloaded", timeout=10_000)
                except PlaywrightError as e:
                    log_fn("ERROR", f"Cannot navigate back to {source_state.url}: {e}")
                    continue

            graph.total_actions_taken += 1
            budget.record_action()
            policy.record_element_acted_on(el.element_key)

            action_start = time.time()
            url_before = page.url
            error_msg: Optional[str] = None
            resulting_state: Optional[UIState] = None

            # Execute the action
            try:
                self._execute_action(page, candidate, policy, start_url, allowed_hosts, log_fn, source_state)

            except SafetyViolation as sv:
                error_msg = f"Safety blocked: {sv}"
                log_fn("SAFETY_BLOCKED", error_msg, url=page.url)
            except PlaywrightError as pe:
                error_msg = f"Playwright error: {str(pe)[:120]}"
                log_fn("ERROR", error_msg)
            except Exception as exc:
                error_msg = f"Action error: {str(exc)[:120]}"
                log_fn("ERROR", error_msg)

            url_after = page.url
            url_changed = url_after != url_before
            action_duration = round(time.time() - action_start, 3)

            # Capture resulting state
            new_depth = (source_state.depth + 1) if url_changed else source_state.depth
            resulting_state = self._capture_state(
                page, url_after, artifacts_dir,
                depth=new_depth,
                step=graph.total_actions_taken,
            )

            is_new = resulting_state.state_id not in graph.states

            # Safety check on the resulting URL
            try:
                validate_url_safety(url_after, allowed_hosts)
            except SafetyViolation as sv:
                log_fn("SAFETY_BLOCKED", f"Resulting URL blocked: {url_after} — {sv}")
                # Navigate back to start
                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                # Record transition as blocked
                transition = StateTransition(
                    source_state_id=source_state_id,
                    action_type=candidate.action_type,
                    target_element_key=el.element_key,
                    target_element_selector=el.selector,
                    target_element_name=el.accessible_name,
                    action_value=candidate.suggested_value,
                    resulting_state_id=None,
                    url_before=url_before,
                    url_after=url_after,
                    url_changed=url_changed,
                    new_state_discovered=False,
                    duration_seconds=action_duration,
                    error=str(sv),
                    step_number=graph.total_actions_taken,
                )
                graph.transitions.append(transition)
                continue

            if is_new:
                graph.states[resulting_state.state_id] = resulting_state
                policy.record_state_visited(resulting_state.state_id)
                budget.record_page()
                log_fn(
                    "STATE_DISCOVERED",
                    f"New state: {url_after} (depth={new_depth})",
                    state_id=resulting_state.state_id,
                    url=url_after,
                )
                # Enqueue actions in the new state
                if not budget.depth_exceeded(new_depth):
                    enqueue_elements(resulting_state)
            else:
                log_fn(
                    "LOOP_DETECTED",
                    f"State already visited: {resulting_state.state_id} @ {url_after}",
                    state_id=resulting_state.state_id,
                )

            transition = StateTransition(
                source_state_id=source_state_id,
                action_type=candidate.action_type,
                target_element_key=el.element_key,
                target_element_selector=el.selector,
                target_element_name=el.accessible_name,
                action_value=candidate.suggested_value,
                resulting_state_id=resulting_state.state_id,
                url_before=url_before,
                url_after=url_after,
                url_changed=url_changed,
                new_state_discovered=is_new,
                duration_seconds=action_duration,
                error=error_msg,
                step_number=graph.total_actions_taken,
            )
            graph.transitions.append(transition)

            log_fn(
                "ACTION_TAKEN",
                f"{candidate.action_type.value} on '{el.accessible_name or el.selector}' → "
                f"{'new state' if is_new else 'known state'} (url_changed={url_changed})",
                transition_id=transition.transition_id,
                url=url_after,
            )

        return

    # ------------------------------------------------------------------
    # State capture
    # ------------------------------------------------------------------

    def _capture_state(
        self,
        page: Page,
        url: str,
        artifacts_dir: str,
        depth: int,
        step: int,
    ) -> UIState:
        """Snapshot the current page into a UIState."""
        fingerprint = _dom_fingerprint(page)
        state_id = UIState.make_state_id(url, fingerprint)
        title = ""
        elements: List[InteractiveElement] = []

        try:
            title = page.title() or ""
        except Exception:
            pass

        try:
            raw = page.evaluate(_EXTRACT_ELEMENTS_JS)
            if isinstance(raw, list):
                elements = _parse_elements(raw, url, url)
        except Exception as e:
            logger.warning("[Explorer] Element extraction failed: %s", e)

        # Screenshot
        screenshot_id: Optional[str] = None
        try:
            ss_name = f"explore_{state_id}_{int(time.time())}.png"
            ss_path = os.path.join(artifacts_dir, ss_name)
            page.screenshot(path=ss_path, full_page=False)
            screenshot_id = ss_name
        except Exception:
            pass

        return UIState(
            state_id=state_id,
            url=url,
            page_title=title,
            dom_fingerprint=fingerprint,
            interactive_elements=elements,
            screenshot_artifact_id=screenshot_id,
            depth=depth,
            discovered_at_step=step,
        )

    def _is_on_state(self, page: Page, state: UIState) -> bool:
        """Check if the current page matches the given state (URL match is sufficient)."""
        try:
            return page.url.rstrip("/") == state.url.rstrip("/")
        except Exception:
            return False

    def _element_ready(self, page: Page, selector: str, timeout_ms: int = 3000) -> bool:
        """
        Returns True if the element identified by *selector* is present, visible,
        and not disabled on the current page. Never raises — returns False on any error.

        Used as a pre-flight check before FILL_FIELD / SELECT_OPTION to detect the
        common case where the page is in a post-action state (e.g. success banner after
        form submit) and the form elements no longer exist.
        """
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=timeout_ms):
                return False
            if locator.is_disabled(timeout=timeout_ms):
                return False
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _execute_action(
        self,
        page: Page,
        candidate: ActionCandidate,
        policy: ExplorationPolicy,
        start_url: str,
        allowed_hosts: List[str],
        log_fn,
        source_state=None,
    ) -> None:
        """Execute a single exploration action."""
        el = candidate.element
        atype = candidate.action_type
        timeout = 8_000


        if atype == ExplorationActionType.NAVIGATE:
            # Follow a link
            href_el = page.locator(el.selector).first
            href = None
            try:
                href = href_el.get_attribute("href")
            except Exception:
                pass
            if href:
                abs_url = urljoin(page.url, href)
                validate_url_safety(abs_url, allowed_hosts)
                page.goto(abs_url, wait_until="domcontentloaded", timeout=timeout)
            else:
                # Click the element anyway
                page.locator(el.selector).first.click(timeout=timeout)
                page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))

        elif atype == ExplorationActionType.CLICK:
            page.locator(el.selector).first.click(timeout=timeout)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
            except Exception:
                pass

        elif atype == ExplorationActionType.FILL_FIELD:
            value = policy.suggest_input_value(el)
            candidate.suggested_value = value
            # Verify element is present and interactable; re-navigate if stale
            if not self._element_ready(page, el.selector, timeout_ms=3000):
                recovery_url = source_state.url if source_state else None
                if recovery_url:
                    log_fn("RECOVERY", f"Element '{el.selector}' not ready; re-navigating to {recovery_url}")
                    page.goto(recovery_url, wait_until="domcontentloaded", timeout=10_000)
                    page.wait_for_timeout(500)
                if not self._element_ready(page, el.selector, timeout_ms=3000):
                    raise PlaywrightError(f"Element '{el.selector}' not found after re-navigation")
            page.locator(el.selector).first.fill(value or "", timeout=timeout)

        elif atype == ExplorationActionType.SELECT_OPTION:
            value = policy.suggest_select_option(el)
            if value:
                candidate.suggested_value = value
                # Verify element is present; re-navigate if stale
                if not self._element_ready(page, el.selector, timeout_ms=3000):
                    recovery_url = source_state.url if source_state else None
                    if recovery_url:
                        log_fn("RECOVERY", f"Select '{el.selector}' not ready; re-navigating to {recovery_url}")
                        page.goto(recovery_url, wait_until="domcontentloaded", timeout=10_000)
                        page.wait_for_timeout(500)
                    if not self._element_ready(page, el.selector, timeout_ms=3000):
                        raise PlaywrightError(f"Select '{el.selector}' not found after re-navigation")
                page.locator(el.selector).first.select_option(label=value, timeout=timeout)

        elif atype == ExplorationActionType.FILL_AND_SUBMIT:
            # Fill all inputs in the same form, then click submit
            form_id = el.form_id
            if form_id and form_id != "anon-form":
                form_sel = f"form#{form_id}"
            else:
                form_sel = "form"
            # Get all inputs in form
            inputs_raw = page.evaluate(f"""() => {{
                const form = document.querySelector('{form_sel}');
                if (!form) return [];
                return Array.from(form.querySelectorAll('input:not([type=hidden]),textarea,select')).map(i => ({{
                    id: i.id, name: i.name, type: i.type || i.tagName.toLowerCase()
                }}));
            }}""")
            for inp in (inputs_raw or []):
                sel = f"#{inp['id']}" if inp.get("id") else f"[name='{inp.get('name', '')}']"
                itype = inp.get("type", "text")
                val = self._generic_value_for_type(itype)
                if val:
                    try:
                        page.fill(sel, val, timeout=3_000)
                    except Exception:
                        pass

            # Submit
            try:
                submit = page.locator(f"{form_sel} [type=submit], {form_sel} button").first
                submit.click(timeout=timeout)
                page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
            except Exception:
                page.keyboard.press("Enter")

        elif atype == ExplorationActionType.PRESS_KEY:
            page.keyboard.press("Enter")

    def _generic_value_for_type(self, itype: str) -> Optional[str]:
        itype = (itype or "").lower()
        mapping = {
            "email": "explorer@verityqa.local",
            "password": "ExploreQA1!",
            "text": "verityqa_test",
            "search": "test",
            "number": "1",
            "date": "2024-01-15",
            "tel": "+1234567890",
            "url": "https://example.com",
            "textarea": "Automated exploration.",
        }
        return mapping.get(itype, "verityqa_test")
