"""
Browser Session Manager for Stateful Investigation.
Maintains a single persistent Playwright Chromium browser context and page across
the entire investigation lifecycle, ensuring session cookies, storage state,
and authentication tokens are preserved between graph nodes.

Provides robust Perception v2 capabilities:
- Network event ring buffer with auth query redaction and 4xx/5xx snippet capture.
- Console and page error capture with mark/since semantics.
- A11y tree snapshots.
- Extended generic form metadata extraction.
- Generic dynamic signals and mutation observation.
- Text anomaly scanning.
- Two-level state identity computation (state_class_id and state_id).
- Storage key snapshots (names only, no values).
- Network and mutation settle mechanics.
"""

import os
import uuid
import time
import re
import logging
from typing import Optional, Dict, Any, List, Tuple
from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
)
from app.config import get_settings
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    NetworkEvent,
    ConsoleOrPageError,
)
from app.investigation_agent.perception import (
    MUTATION_OBSERVER_INIT_SCRIPT,
    DYNAMIC_SIGNALS_SCRIPT,
    EXTENDED_FORMS_SCRIPT,
    redact_url_params,
    scan_text_anomalies,
    compute_state_class_and_instance,
)
from app.safety import validate_url_safety, SafetyViolation

logger = logging.getLogger(__name__)


class StatefulBrowserSession:
    """
    Encapsulates a persistent Playwright browser instance and context.
    Safely runs synchronous Playwright commands in isolation.
    """

    def __init__(self, headless: Optional[bool] = None):
        self.settings = get_settings()
        self.headless = headless if headless is not None else self.settings.verityqa_headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        # Backwards compatible string logs
        self.console_logs: List[str] = []
        self.page_errors: List[str] = []

        # Perception v2 telemetry
        self.network_events: List[NetworkEvent] = []
        self._network_seq: int = 0
        self.error_events: List[ConsoleOrPageError] = []
        self._error_seq: int = 0
        self._in_flight_requests: int = 0
        self._last_network_activity: float = time.time()
        self._last_http_status: Optional[int] = None

    def start(self):
        if self._playwright is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="VerityQA-Investigator/2.0",
                ignore_https_errors=False,
            )
            # Install mutation observer before any page script executes
            self._context.add_init_script(MUTATION_OBSERVER_INIT_SCRIPT)

            # Security: Intercept navigations to block unauthorized redirects BEFORE destinations can be accessed
            def _filter_route(route: Any, request: Any):
                if request.is_navigation_request():
                    nav_url = request.url
                    if nav_url.startswith("http://") or nav_url.startswith("https://"):
                        try:
                            validate_url_safety(nav_url, self.settings.allowed_hosts_list)
                        except SafetyViolation as sv:
                            logger.warning("[Safety] Intercepted navigation/redirect to unauthorized host '%s': %s", nav_url, sv)
                            try:
                                route.abort("accessdenied")
                            except Exception:
                                pass
                            return
                try:
                    route.continue_()
                except Exception:
                    pass

            self._context.route("**/*", _filter_route)

            self._page = self._context.new_page()

            # Attach event listeners
            self._page.on("console", self._handle_console)
            self._page.on("pageerror", self._handle_page_error)
            self._page.on("request", self._handle_request)
            self._page.on("response", self._handle_response)
            self._page.on("requestfailed", self._handle_request_failed)

    def _handle_console(self, msg):
        if msg.type == "error":
            txt = msg.text
            self.console_logs.append(txt)
            self._error_seq += 1
            err = ConsoleOrPageError(
                seq=self._error_seq,
                error_type="console",
                message=txt,
                timestamp=time.time(),
            )
            self.error_events.append(err)
            if len(self.error_events) > 200:
                self.error_events.pop(0)

    def _handle_page_error(self, err):
        txt = str(err)
        self.page_errors.append(txt)
        self._error_seq += 1
        err_evt = ConsoleOrPageError(
            seq=self._error_seq,
            error_type="pageerror",
            message=txt,
            timestamp=time.time(),
        )
        self.error_events.append(err_evt)
        if len(self.error_events) > 200:
            self.error_events.pop(0)

    def _handle_request(self, request: Request):
        self._in_flight_requests += 1
        self._last_network_activity = time.time()

    def _handle_response(self, response: Response):
        self._in_flight_requests = max(0, self._in_flight_requests - 1)
        self._last_network_activity = time.time()
        self._network_seq += 1

        url = redact_url_params(response.url)
        status = response.status
        ok = response.ok

        # Response body snippet for 4xx/5xx responses (capped at 200 characters)
        snippet: Optional[str] = None
        if status >= 400:
            try:
                raw_text = response.text()
                if raw_text:
                    snippet = raw_text[:200]
            except Exception:
                snippet = None

        method = ""
        resource_type = ""
        try:
            req = response.request
            method = req.method
            resource_type = req.resource_type
        except Exception:
            method = "GET"
            resource_type = "document"

        evt = NetworkEvent(
            seq=self._network_seq,
            method=method,
            url=url,
            resource_type=resource_type,
            status=status,
            ok=ok,
            duration_ms=None,
            failure=None,
            response_snippet=snippet,
            timestamp=time.time(),
        )
        self.network_events.append(evt)
        if len(self.network_events) > 200:
            self.network_events.pop(0)

    def _handle_request_failed(self, request: Request):
        self._in_flight_requests = max(0, self._in_flight_requests - 1)
        self._last_network_activity = time.time()
        self._network_seq += 1

        url = redact_url_params(request.url)
        failure_text = request.failure if request.failure else "Request Failed"

        evt = NetworkEvent(
            seq=self._network_seq,
            method=request.method,
            url=url,
            resource_type=request.resource_type,
            status=None,
            ok=False,
            duration_ms=None,
            failure=str(failure_text),
            response_snippet=None,
            timestamp=time.time(),
        )
        self.network_events.append(evt)
        if len(self.network_events) > 200:
            self.network_events.pop(0)

    def mark(self) -> int:
        """Returns the current network event sequence mark."""
        return self._network_seq

    def mark_network(self) -> int:
        """Returns the current network event sequence mark."""
        return self._network_seq

    def events_since(self, mark: int) -> List[NetworkEvent]:
        """Returns all network events recorded strictly after the given mark."""
        return [e for e in self.network_events if e.seq > mark]

    def mark_errors(self) -> int:
        """Returns the current error sequence mark."""
        return self._error_seq

    def errors_since(self, mark: int) -> List[ConsoleOrPageError]:
        """Returns all console/page errors recorded strictly after the given mark."""
        return [e for e in self.error_events if e.seq > mark]

    def drain_mutations(self) -> List[Dict[str, Any]]:
        """Drains and resets the buffered DOM mutations recorded by the MutationObserver."""
        p = self.page
        if not p:
            return []
        try:
            return p.evaluate("() => { const m = window.__vqa_mut || []; window.__vqa_mut = []; return m; }") or []
        except Exception:
            return []

    def storage_snapshot(self) -> Dict[str, List[str]]:
        """Returns cookie names and localStorage keys only (no values for security)."""
        cookies: List[str] = []
        if self._context:
            try:
                cookies = [c.get("name", "") for c in self._context.cookies() if c.get("name")]
            except Exception:
                cookies = []

        local_storage: List[str] = []
        if self._page:
            try:
                local_storage = self._page.evaluate("() => Object.keys(localStorage)") or []
            except Exception:
                local_storage = []

        return {
            "cookies": sorted(list(set(cookies))),
            "local_storage": sorted(list(set(local_storage))),
        }

    def settle(self, timeout_seconds: float = 2.0):
        """
        Settles page: waits until no in-flight requests for 300 ms (cap 2.0s)
        and no DOM mutations for 200 ms (cap 1.5s).
        """
        p = self._page
        if not p:
            return
        start = time.time()
        quiet_net_start: Optional[float] = None
        last_mut_len = 0
        quiet_mut_start: Optional[float] = None

        while (time.time() - start) < timeout_seconds:
            now = time.time()

            # Network quiescence check (300 ms)
            if self._in_flight_requests == 0:
                if quiet_net_start is None:
                    quiet_net_start = now
            else:
                quiet_net_start = None

            # Mutation quiescence check (200 ms)
            try:
                curr_mut_len = p.evaluate("() => (window.__vqa_mut ? window.__vqa_mut.length : 0)")
            except Exception:
                curr_mut_len = 0

            if curr_mut_len == last_mut_len:
                if quiet_mut_start is None:
                    quiet_mut_start = now
            else:
                last_mut_len = curr_mut_len
                quiet_mut_start = None

            net_ready = (quiet_net_start is not None) and ((now - quiet_net_start) >= 0.3)
            mut_ready = (quiet_mut_start is not None) and ((now - quiet_mut_start) >= 0.2)

            if net_ready and mut_ready:
                break

            time.sleep(0.05)

    def close(self):
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    @property
    def page(self) -> Page:
        if self._page is None:
            self.start()
        return self._page

    def navigate(self, url: str) -> Tuple[bool, str, Optional[int]]:
        """Safely navigates to URL after security validation."""
        # 1. Fragment check - fragments are in-page, do not validate as external host
        if url.startswith("#"):
            try:
                self.page.evaluate("(frag) => { window.location.hash = frag; }", url)
                return True, self.page.url, 200
            except Exception as e:
                return False, f"Fragment navigation failed: {str(e)[:100]}", None

        # 2. Safety validation for HTTP/HTTPS
        try:
            validate_url_safety(url, self.settings.allowed_hosts_list)
        except SafetyViolation as e:
            return False, f"SAFETY_BLOCKED: {str(e)}", 403

        last_err = None
        for attempt in range(2):
            try:
                res = self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if (self.page.url or "").startswith("chrome-error://") or self.page.url == "about:blank":
                    if attempt == 0:
                        time.sleep(0.8)
                        continue
                status = res.status if res else None
                self._last_http_status = status
                try:
                    self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
                try:
                    self.page.wait_for_function(
                        "() => document.querySelectorAll('input, button, a, form, select, h1, h2').length > 0",
                        timeout=2000,
                    )
                except Exception:
                    pass

                # 3. Post-navigation redirect check: ensure we didn't end up on an unauthorized host
                final_url = self.page.url
                try:
                    validate_url_safety(final_url, self.settings.allowed_hosts_list)
                except SafetyViolation:
                    logger.warning("[Session] Navigation redirected to unauthorized host: %s", final_url)
                    try:
                        self.page.go_back()
                    except Exception:
                        pass
                    return False, f"SAFETY_BLOCKED: Redirected to unauthorized host ({final_url})", 403

                return True, self.page.url, status
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "accessdenied" in err_str or "err_access_denied" in err_str:
                    return False, "SAFETY_BLOCKED: Navigation route aborted by safety policy", 403
                if attempt == 0:
                    time.sleep(0.8)
        return False, str(last_err), None

    def resolve_target(self, selector: str) -> Dict[str, Any]:
        """
        Non-blocking inspection of the current live DOM for a target selector.
        Returns presence, visibility, enabled state, and semantic status.
        """
        p = self.page
        if not p:
            return {"present": False, "visible": False, "enabled": False, "reason": "No active page"}
        try:
            return p.evaluate("""(sel) => {
                let el = null;
                try {
                    el = document.querySelector(sel);
                } catch (e) {
                    return { present: false, visible: false, enabled: false, reason: "Invalid selector: " + e.message };
                }
                if (!el) {
                    return { present: false, visible: false, enabled: false, reason: "Element not present in current DOM" };
                }
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const isVisible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                const isEnabled = !el.disabled && !el.hasAttribute('disabled');

                let reason = "Ready";
                if (!isVisible) reason = "Element is present but hidden or 0-size in current DOM";
                else if (!isEnabled) reason = "Element is present but disabled";

                return {
                    present: true,
                    visible: isVisible,
                    enabled: isEnabled,
                    reason: reason,
                    tag_name: el.tagName.toLowerCase(),
                    text: (el.innerText || el.getAttribute('aria-label') || '').slice(0, 50)
                };
            }""", selector)
        except Exception as e:
            return {"present": False, "visible": False, "enabled": False, "reason": f"Evaluation error: {str(e)[:100]}"}

    def capture_observation(self, state_prefix: str = "state") -> UIStateObservation:
        """
        Perception v2: Extracts rich, site-agnostic observation of current live DOM,
        including a11y snapshot, extended form metadata, text anomaly scan, generic signals,
        and two-level state identity (class + instance).
        """
        # Settle page before capture
        self.settle()

        p = self.page
        current_url = p.url or ""
        title = p.title() or ""

        # Capture screenshot artifact
        artifacts_dir = os.path.abspath(self.settings.verityqa_artifacts_dir)
        os.makedirs(artifacts_dir, exist_ok=True)
        screenshot_filename = f"investigate_{state_prefix}_{uuid.uuid4().hex[:8]}.png"
        screenshot_path = os.path.join(artifacts_dir, screenshot_filename)
        try:
            p.screenshot(path=screenshot_path, full_page=False)
        except Exception:
            screenshot_filename = None

        # A11y tree snapshot
        a11y_snapshot: Optional[str] = None
        try:
            a11y_raw = p.locator("body").aria_snapshot()
            if a11y_raw:
                a11y_snapshot = a11y_raw[:6000]
        except Exception:
            a11y_snapshot = None

        # Extract headings (stable semantic landmarks, excluding dynamic alert/error containers)
        headings = p.evaluate("""() => {
            return Array.from(document.querySelectorAll('h1, h2, h3, .title, [role="heading"]'))
                .filter(el => !el.closest('[role="alert"], [role="status"], .error-message-container, .alert-danger, .invalid-feedback, .field-error'))
                .map(el => (el.innerText || '').trim())
                .filter(t => t.length > 0 && t.length < 150)
                .slice(0, 15);
        }""") or []

        # Extract visible text snippet & scan for text anomalies
        text_snippet = p.evaluate("""() => {
            const body = document.body;
            if (!body) return '';
            return (body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 1200);
        }""") or ""
        text_anomalies = scan_text_anomalies(text_snippet)

        # Extended form extraction
        forms = p.evaluate(EXTENDED_FORMS_SCRIPT) or []

        # Interactive controls (generic, site-agnostic)
        controls_raw = p.evaluate("""() => {
            const results = [];
            const selector = 'button, a, input:not([type=hidden]), select, textarea, [role="button"], [role="tab"], [role="link"]';
            const nodes = document.querySelectorAll(selector);

            let idx = 0;
            for (const el of nodes) {
                if (idx >= 60) break;
                if (el.closest('[aria-hidden="true"], [hidden]')) continue;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const isVisible = rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                if (!isVisible) continue;

                let name = el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || el.id || '';
                name = String(name).trim().replace(/\\s+/g, ' ').slice(0, 80);

                let sel = '';
                if (el.id) {
                    sel = '#' + el.id;
                } else if (el.name) {
                    sel = el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                } else if (el.dataset && Object.keys(el.dataset).length > 0) {
                    const k = Object.keys(el.dataset)[0];
                    const dataAttr = k.replace(/([A-Z])/g, '-$1').toLowerCase();
                    sel = `[data-${dataAttr}="${el.dataset[k]}"]`;
                } else if (el.tagName.toLowerCase() === 'a' && el.getAttribute('href') && el.getAttribute('href') !== '#') {
                    const href = el.getAttribute('href');
                    const firstClass = (el.className && typeof el.className === 'string') ? el.className.trim().split(/\\s+/)[0] : '';
                    if (firstClass && !firstClass.includes(':')) {
                        sel = `a.${firstClass}[href="${href}"]`;
                    } else {
                        sel = `a[href="${href}"]`;
                    }
                } else if (el.className && typeof el.className === 'string') {
                    const firstClass = el.className.trim().split(/\\s+/)[0];
                    if (firstClass && !firstClass.includes(':')) sel = el.tagName.toLowerCase() + '.' + firstClass;
                    else sel = el.tagName.toLowerCase();
                } else {
                    sel = el.tagName.toLowerCase();
                }

                let selectOptions = [];
                let selectOptionLabels = [];
                if (el.tagName.toLowerCase() === 'select' && el.options) {
                    // option.value already falls back to the text when no value attribute exists; an
                    // explicitly empty value is a placeholder/reset ("All", "Choose..."), not a selectable value.
                    const opts = Array.from(el.options)
                        .filter(o => (o.value || '').trim())
                        .slice(0, 10);
                    selectOptions = opts.map(o => o.value.trim());
                    selectOptionLabels = opts.map(o => (o.text || o.value || '').trim());
                }
                const rawInnerText = (el.innerText || '').trim();
                const isImageOnlyLink = (el.tagName.toLowerCase() === 'a' && !rawInnerText && !!el.querySelector('img'));

                results.push({
                    element_id: 'ctrl_' + idx,
                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                    accessible_name: name || sel,
                    tag_name: el.tagName.toLowerCase(),
                    selector: sel,
                    element_type: el.getAttribute('type') || el.tagName.toLowerCase(),
                    is_enabled: !el.disabled,
                    is_visible: true,
                    declared_constraints: {
                        required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
                        maxlength: el.getAttribute('maxlength') ? parseInt(el.getAttribute('maxlength')) : null,
                        minlength: el.getAttribute('minlength') ? parseInt(el.getAttribute('minlength')) : null,
                        select_options: selectOptions,
                        select_option_labels: selectOptionLabels,
                        is_image_only_link: isImageOnlyLink,
                        href: el.getAttribute('href') || ''
                    }
                });
                idx++;
            }
            return results;
        }""") or []

        interactive_controls = [ElementReference(**c) for c in controls_raw]

        # Extract navigation links
        nav_links = p.evaluate("""() => {
            const links = [];
            for (const a of document.querySelectorAll('a[href]')) {
                if (a.closest('[aria-hidden="true"], [hidden]')) continue;
                const rect = a.getBoundingClientRect();
                const style = window.getComputedStyle(a);
                if (rect.width <= 0 || rect.height <= 0 || rect.right <= 0 || rect.bottom <= 0 || style.display === 'none' || style.visibility === 'hidden') {
                    continue;
                }
                const href = a.getAttribute('href');
                if (!href || href.startsWith('javascript:') || href === '#') {
                    continue;
                }
                const text = (a.innerText || a.getAttribute('aria-label') || '').trim();
                const isFragment = href.startsWith('#');
                const isExternal = href.startsWith('http://') || href.startsWith('https://');
                links.push({
                    text: text.slice(0, 60) || href,
                    href: href,
                    is_fragment: isFragment,
                    is_external: isExternal
                });
                if (links.length >= 30) break;
            }
            return links;
        }""") or []

        # Generic Dynamic Signals
        dynamic_signals = p.evaluate(DYNAMIC_SIGNALS_SCRIPT) or {}

        # Compute Two-Level State Identity
        state_class_id, state_instance_id = compute_state_class_and_instance(
            url=current_url,
            headings=headings,
            forms=forms,
            dynamic_signals=dynamic_signals,
            a11y_snapshot=a11y_snapshot
        )

        # Storage snapshot (cookie names and localStorage keys only)
        storage_keys = self.storage_snapshot()

        return UIStateObservation(
            state_id=state_instance_id,
            state_class_id=state_class_id,
            url=current_url,
            normalized_route=current_url.split("?")[0].replace("https://", "").replace("http://", ""),
            page_title=title,
            http_status=getattr(self, "_last_http_status", None),
            headings=headings,
            interactive_controls=interactive_controls,
            forms=forms,
            navigation_links=nav_links,
            visible_text_snippet=text_snippet,
            dynamic_signals=dynamic_signals,
            has_validation_error=bool(dynamic_signals.get("has_validation_error")),
            validation_error_message=dynamic_signals.get("validation_error_message"),
            validation_error_locator=dynamic_signals.get("validation_error_locator"),
            menu_open=bool(dynamic_signals.get("menu_open")),
            is_authenticated=False,
            a11y_snapshot=a11y_snapshot,
            text_anomalies=text_anomalies,
            storage_keys=storage_keys,
            console_error_messages=list(self.console_logs),
            screenshot_artifact_id=screenshot_filename,
        )

    @staticmethod
    def compute_state_identity(
        url: str,
        headings: List[str],
        dynamic_signals: Dict[str, Any],
        forms: List[Dict[str, Any]],
        a11y_snapshot: Optional[str] = None
    ) -> str:
        """
        Backwards-compatible wrapper returning state_instance_id.
        """
        _, instance_id = compute_state_class_and_instance(
            url=url,
            headings=headings,
            forms=forms,
            dynamic_signals=dynamic_signals,
            a11y_snapshot=a11y_snapshot
        )
        return instance_id
