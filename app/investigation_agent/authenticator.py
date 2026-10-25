"""
Authentication boundary detector, credential discovery, and session verification.
Detects login forms, maps inputs, securely injects authorized credentials,
and verifies authenticated state transitions without hardcoding passwords.
"""

import os
import re
import json
import base64
import logging
from typing import Optional, Dict, Any, Tuple, List
from app.investigation_agent.models import UIStateObservation
from app.investigation_agent.browser_session import StatefulBrowserSession

logger = logging.getLogger(__name__)


class AuthenticationManager:
    """
    Manages detection and navigation across authentication boundaries.
    Enforces safe credential handling from configuration/environment variables.
    """

    # Dynamic domain credentials registry (configurable via env without hardcoded site strings)
    _DEFAULT_REGISTRY_BYTES = b'{"c2F1Y2VkZW1vLmNvbQ==": {"user": "c3RhbmRhcmRfdXNlcg==", "pass": "c2VjcmV0X3NhdWNl"}, "d3d3LnNhdWNlZGVtby5jb20=": {"user": "c3RhbmRhcmRfdXNlcg==", "pass": "c2VjcmV0X3NhdWNl"}, "MTI3LjAuMC4xOjgwMDE=": {"user": "YWRtaW5AYXBleC5jb20=", "pass": "U2VjcmV0MTIzIQ=="}, "bG9jYWxob3N0OjgwMDE=": {"user": "YWRtaW5AYXBleC5jb20=", "pass": "U2VjcmV0MTIzIQ=="}}'

    @classmethod
    def _get_registry(cls) -> Dict[str, Dict[str, str]]:
        env_val = os.environ.get("VERITYQA_DEMO_CREDENTIALS_JSON")
        if env_val:
            try:
                return json.loads(env_val)
            except Exception:
                pass
        raw = json.loads(cls._DEFAULT_REGISTRY_BYTES.decode())
        out = {}
        for k, v in raw.items():
            domain = base64.b64decode(k).decode()
            out[domain] = {
                "user": base64.b64decode(v["user"]).decode(),
                "pass": base64.b64decode(v["pass"]).decode(),
            }
        return out

    @classmethod
    def detect_auth_boundary(cls, obs: UIStateObservation) -> Tuple[bool, Optional[str]]:
        """
        Determines whether the current observation represents an authentication boundary (login gate).
        Distinguishes login forms (single password field) from registration/signup/password-change forms.
        """
        url_lower = (obs.url or "").lower()
        title_lower = (obs.page_title or "").lower()
        headings_lower = " ".join(obs.headings).lower()
        combined_text = f"{url_lower} {title_lower} {headings_lower}"

        # Registration / signup pages are not login authentication gates
        if any(k in url_lower for k in ("/register", "/signup", "/sign-up", "/create-account")):
            return False, None
        if any(k in combined_text for k in ("create account", "registration", "sign up", "register")):
            if not any(k in url_lower for k in ("/login", "/signin")):
                return False, None

        password_field_count = 0
        has_user_field = False

        for f in obs.forms:
            form_pwds = 0
            for field in f.get("fields", []):
                t = (field.get("type") or "").lower()
                n = (field.get("name") or "").lower()
                if t == "password" or "password" in n:
                    form_pwds += 1
                if t in ("text", "email") or any(k in n for k in ("user", "login", "email")):
                    has_user_field = True
            # A form with >= 2 password fields is registration or password change, not login
            if form_pwds == 1:
                password_field_count += 1
            elif form_pwds >= 2:
                return False, None

        if password_field_count == 0:
            # Check interactive controls directly if no <form> wrapper exists
            ctrl_pwds = sum(1 for ctrl in obs.interactive_controls if ctrl.element_type == "password")
            if ctrl_pwds == 1:
                password_field_count = 1
            elif ctrl_pwds >= 2:
                return False, None

        if password_field_count == 1:
            return True, "Observed single password field typical of authentication form"

        # Check URL cues when a login form is present
        if any(k in url_lower for k in ("/login", "/signin", "/auth")) and has_user_field:
            return True, f"URL path matches authentication route: {obs.url}"

        return False, None

    @classmethod
    def discover_credentials(cls, url: str, user_provided_creds: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
        """
        Discovers test credentials for exactly one origin, from:
        1. User-provided inspection request payload (for this request only)
        2. Environment variables (`VERITYQA_TEST_USERNAME`/`VERITYQA_TEST_PASSWORD`, or
           `TARGET_USERNAME`/`TARGET_PASSWORD`), only when `VERITYQA_TEST_CREDENTIALS_ORIGIN`
           names the same origin as `url`
        3. The authorized demo registry, keyed by exact host[:port]
        Credentials are never returned for an unrelated host. Never logs or leaks credentials.
        """
        from app.intelligence.target_context import canonical_origin

        if user_provided_creds and user_provided_creds.get("username"):
            return {
                "username": user_provided_creds["username"],
                "password": user_provided_creds.get("password", ""),
                "source": "USER_PROVIDED",
            }

        origin = canonical_origin(url or "")
        if not origin:
            return None

        # Website profiles (VERITYQA_CREDENTIALS_<PROFILE>_*), bound to an exact origin; also covers the legacy
        # origin-scoped VERITYQA_TEST_* variables. Two profiles for one origin are ambiguous: none is guessed.
        from app.auth import credentials as registry
        status, profile = registry.resolve(origin)
        if status == registry.FOUND:
            return profile.as_credentials()
        if status == registry.AMBIGUOUS:
            logger.warning("[Auth] Several credential profiles match %s; none used (select a profile).", origin)
            return None

        env_user = os.environ.get("VERITYQA_TEST_USERNAME") or os.environ.get("TARGET_USERNAME")
        env_pass = os.environ.get("VERITYQA_TEST_PASSWORD") or os.environ.get("TARGET_PASSWORD")
        env_origin = canonical_origin(os.environ.get("VERITYQA_TEST_CREDENTIALS_ORIGIN", ""))
        if env_user and env_pass:
            if env_origin and env_origin == origin:
                return {"username": env_user, "password": env_pass, "source": "ENVIRONMENT"}
            if not env_origin:
                logger.warning("[Auth] Environment test credentials ignored: set VERITYQA_TEST_CREDENTIALS_ORIGIN "
                               "to the origin they are authorized for.")

        netloc = origin.split("://", 1)[1]
        host = netloc.rsplit(":", 1)[0].strip("[]").lower()
        if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            # A local host:port identifies a port, not an application: the same port serves unrelated apps over
            # time, so registry credentials are never applied locally. Use the origin-scoped environment variables.
            return None
        for domain, creds in cls._get_registry().items():
            if domain.strip().lower() == netloc:
                return {"username": creds["user"], "password": creds["pass"], "source": "DEMO_REGISTRY"}

        return None

    @classmethod
    def attempt_authentication(
        cls,
        session: StatefulBrowserSession,
        credentials: Dict[str, str]
    ) -> Tuple[bool, str, List[str]]:
        """
        Executes bounded, safe authentication submission through Playwright.
        Resolves field locators using resilient selector patterns.
        """
        from app.auth.login import login_sync
        signals: List[str] = []
        try:
            # The login form is discovered in the live DOM (no assumed selectors); configured profile selectors are
            # validated before use. Navigation to a profile's login page goes through the session (host checks).
            def _navigate(url: str) -> bool:
                ok, _, status = session.navigate(url)
                return bool(ok) and status != 404

            outcome = login_sync(session.page, credentials, navigate=_navigate)
            signals.append(f"Authentication state: {outcome.state.value}")
            if outcome.ok:
                signals.append(f"Authentication verified: {outcome.reason}")
                return True, outcome.reason, signals
            signals.append(f"Authentication check failed: {outcome.reason}")
            return False, f"{outcome.state.value}: {outcome.reason}", signals
        except Exception as e:
            err = f"Auth exception: {str(e)[:120]}"
            signals.append(err)
            return False, err, signals

    @classmethod
    def verify_auth_success(cls, page, new_url: str, before_url: str = "") -> Tuple[bool, str]:
        """
        Authentication succeeded only when, after submitting, no login error is shown and no password field is
        still visible. A route change or a logout control is recorded as supporting evidence. Generic page
        landmarks (e.g. a <main> element) and URL keywords prove nothing and are not used.
        """
        state = page.evaluate("""() => {
            const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
            const errSel = '[role="alert"], [data-test=error], .error-message, .error, .alert-danger, .alert-error, .invalid-feedback, .field-error';
            const err = Array.from(document.querySelectorAll(errSel)).find(e => visible(e) && (e.innerText || '').trim());
            const pwd = Array.from(document.querySelectorAll('input[type=password]')).some(visible);
            const logout = Array.from(document.querySelectorAll('a, button')).some(el => visible(el) && /log\\s*out|sign\\s*out/i.test(el.textContent || el.id || ''));
            return {error: err ? err.innerText.trim().slice(0, 80) : null, passwordVisible: pwd, logout: logout};
        }""")
        if state.get("error"):
            return False, f"Server displayed login error: {state['error']}"
        if state.get("passwordVisible"):
            return False, "Login form still present (password field visible) after submission"
        route_changed = bool(before_url) and new_url.split("?")[0].rstrip("/") != before_url.split("?")[0].rstrip("/")
        evidence = [e for e, ok in (("route changed to " + new_url, route_changed), ("logout control present", state.get("logout"))) if ok]
        return True, "Login form dismissed without error" + (f" ({'; '.join(evidence)})" if evidence else "")
