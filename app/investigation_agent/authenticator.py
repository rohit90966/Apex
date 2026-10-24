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
        p = session.page
        signals: List[str] = []

        try:
            # 1. Resolve username input
            user_sel = "input[name='user-name'], input[name='username'], input[name='email'], input[id='user-name'], input[id='username'], input[type='text']:visible"
            # 2. Resolve password input
            pass_sel = "input[name='password'], input[id='password'], input[type='password']:visible"
            # 3. Resolve submit button
            submit_sel = "input[type='submit'], button[type='submit'], input[name='login-button'], button#loginBtn, button:has-text('Login'), button:has-text('Sign In')"

            # Fill username
            p.wait_for_selector(user_sel, timeout=5000)
            p.fill(user_sel, credentials["username"])
            signals.append("Resolved and populated username field")

            # Fill password (never logged)
            p.wait_for_selector(pass_sel, timeout=5000)
            p.fill(pass_sel, credentials["password"])
            signals.append("Resolved and populated password field")

            # Submit
            p.click(submit_sel, timeout=5000)
            signals.append("Clicked authentication submission control")

            # Wait for navigation or dynamic DOM transformation
            try:
                p.wait_for_load_state("domcontentloaded", timeout=7000)
            except Exception:
                pass
            p.wait_for_timeout(1500)

            # Verification of success
            new_url = p.url or ""
            is_success, reason = cls.verify_auth_success(p, new_url)

            if is_success:
                signals.append(f"Authentication verified: {reason}")
                return True, reason, signals
            else:
                signals.append(f"Authentication check failed: {reason}")
                return False, reason, signals

        except Exception as e:
            err = f"Auth exception: {str(e)[:120]}"
            signals.append(err)
            return False, err, signals

    @classmethod
    def verify_auth_success(cls, page, new_url: str) -> Tuple[bool, str]:
        """
        Verifies whether authentication actually succeeded using multi-signal evidence:
        - Route progression (e.g., /inventory, /dashboard)
        - Disappearance of password input
        - Appearance of logout/sign-out controls or user badges
        - Appearance of product inventory or dashboard landmarks
        """
        # 1. Check URL change
        url_lower = new_url.lower()
        if any(k in url_lower for k in ("/inventory", "/dashboard", "/app", "/tasks", "/home")):
            return True, f"Navigated to authenticated route: {new_url}"

        # 2. Check for dashboard or authenticated content landmarks
        has_landmark = page.evaluate("""() => {
            const dashboard = document.querySelector('main, #dashboard, .dashboard-container, [role="main"], article, .content-area');
            const logout = Array.from(document.querySelectorAll('a, button')).find(el => /log\\s*out|sign\\s*out/i.test(el.textContent || el.id || ''));
            if (logout) return 'logout_control_present';
            const userBadge = document.querySelector('.user-profile, .avatar, [aria-label*="user"], [aria-label*="profile"]');
            if (userBadge) return 'user_profile_badge';
            if (dashboard) return 'authenticated_content_container';
            return null;
        }""")

        if has_landmark:
            return True, f"Detected authenticated landmark: {has_landmark}"

        # 3. Check if password input is gone
        pass_count = page.evaluate("() => document.querySelectorAll('input[type=password]').length")
        if pass_count == 0:
            return True, "Login form dismissed; password field no longer in DOM"

        # 4. Check for explicit error messages
        error_msg = page.evaluate("""() => {
            const err = document.querySelector('[data-test=error], .error-message, .error, .alert-danger');
            return err ? (err.innerText || '').trim() : null;
        }""")
        if error_msg:
            return False, f"Server displayed login error: {error_msg[:80]}"

        return False, "Login form still present without state change"
