"""
Reaching a page with the authentication it needs (test executor, async Playwright).

ensure_page(): open the requested URL; if the page turns out to be a login gate (redirected to a login form) or
the step says the page's content needs a session and none is evident, log in with the origin's credentials,
retry the URL once, and verify the requested page was actually reached. Every outcome carries an AuthState.

Session reuse: after a successful login the context's storage state is kept IN MEMORY, keyed by (origin,
profile), and later tests of the same run start a fresh browser context from a copy of it. Contexts are never
shared, storage state is never written to disk, and an origin only ever receives its own session. An expired
session (redirected to the login gate) is re-established once per test.
"""

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from app.auth import credentials as registry
from app.auth.login import DISCOVERY_SCRIPT, AuthOutcome, AuthState, login_async, looks_like_login_gate


def session_reuse_enabled() -> bool:
    return os.environ.get("VERITYQA_AUTH_SESSION_REUSE", "1").strip().lower() not in ("0", "false", "no")


class SessionCache:
    """Per-run, in-memory authenticated storage states; never persisted."""

    def __init__(self):
        self._states: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def get(self, origin: str, profile: str) -> Optional[Dict[str, Any]]:
        state = self._states.get((origin, profile))
        return copy.deepcopy(state) if state is not None else None

    def put(self, origin: str, profile: str, state: Dict[str, Any]) -> None:
        # Keep only this origin's cookies/storage: a session is never offered to another host.
        host = urlsplit(origin).hostname or ""
        cookies = [c for c in state.get("cookies", []) if (c.get("domain") or "").lstrip(".") in (host, "")
                   or host.endswith((c.get("domain") or "").lstrip("."))]
        origins = [o for o in state.get("origins", []) if o.get("origin", "").rstrip("/") == origin]
        self._states[(origin, profile)] = {"cookies": cookies, "origins": origins}

    def drop(self, origin: str, profile: str) -> None:
        self._states.pop((origin, profile), None)

    def clear(self) -> None:
        self._states.clear()


@dataclass
class PageAccess:
    ok: bool
    state: AuthState
    reason: str = ""
    profile: Optional[str] = None
    login_performed: bool = False
    session_reused: bool = False
    evidence: Dict[str, Any] = field(default_factory=dict)


def _same_page(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.path.rstrip("/") or "/") == (pb.path.rstrip("/") or "/")


async def _discover(page: Any) -> Dict[str, Any]:
    try:
        return await page.evaluate(DISCOVERY_SCRIPT)
    except Exception:
        return {}


async def ensure_page(page: Any, context: Any, url: str, *, requires_login: bool = False,
                      login_url: Optional[str] = None, cache: Optional[SessionCache] = None,
                      session_reused: bool = False, timeout_ms: int = 8000) -> PageAccess:
    origin = registry.canonical_origin(url)
    status, profile = registry.resolve(url)
    creds = profile.as_credentials() if profile else None
    pname = profile.name if profile else None

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    seen = await _discover(page)
    # Check if a login gate is present (either by redirect or rendered in-place on a protected URL)
    form_here = looks_like_login_gate(seen) and (login_url is None or not _same_page(url, login_url))
    redirected_to_gate = not _same_page(page.url, url) and form_here
    # A login form rendered in place counts as a gate only when the page is known to need a session; otherwise it is
    # a public page that happens to contain a login form (a home page or sidebar login), not a reason to log in.
    is_gate = redirected_to_gate or (form_here and requires_login)

    # A reused session is trusted until the site sends us to its login gate (then it has expired).
    if not is_gate and (not requires_login or seen.get("logout_visible") or session_reused):
        # Authenticated only if we carry a session, or the page needed one and shows session evidence; a logout link on
        # a page that needs no login is not evidence of anything.
        if session_reused or (requires_login and seen.get("logout_visible")):
            state = AuthState.AUTHENTICATED
        else:
            state = AuthState.AUTH_NOT_REQUIRED
        return PageAccess(True, state, "page reached", pname, session_reused=session_reused, evidence={"url": page.url})

    # Authentication is needed (redirected to a login gate, or the page's content needs a session).
    if session_reused and (redirected_to_gate or is_gate):
        if cache is not None and pname:
            cache.drop(origin, pname)  # the reused session expired
    if status == registry.AMBIGUOUS:
        return PageAccess(False, AuthState.AUTH_UNRESOLVED, "several credential profiles match this origin; select one", None)
    if not creds:
        return PageAccess(False, AuthState.CREDENTIALS_MISSING,
                          f"{'redirected to a login page' if redirected_to_gate else 'page content requires a session'} "
                          f"and no credentials are configured for {origin}", None)

    # Log in where the gate is, or on the observed login page.
    gate_url = page.url if (redirected_to_gate or is_gate) else (login_url or (seen.get("login_links") or [None])[0])
    if gate_url and registry.canonical_origin(gate_url) != origin:
        return PageAccess(False, AuthState.AUTH_UNRESOLVED, f"login page {registry.canonical_origin(gate_url)} is on another origin; not followed", pname)
    if gate_url and not redirected_to_gate:
        await page.goto(gate_url, wait_until="domcontentloaded", timeout=timeout_ms)
    outcome: AuthOutcome = await login_async(page, creds, None, timeout_ms=timeout_ms)
    if not outcome.ok:
        return PageAccess(False, outcome.state, outcome.reason, pname, login_performed=True)

    # Retry the intended page once and verify it was reached (a login loop is a failure, not a retry).
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    after = await _discover(page)
    if not _same_page(page.url, url):
        state = AuthState.LOGIN_FAILED if looks_like_login_gate(after) else AuthState.AUTH_UNRESOLVED
        return PageAccess(False, state, f"after login, {url} led to {page.url}", pname, login_performed=True)
    if cache is not None and session_reuse_enabled():
        cache.put(origin, pname, await context.storage_state())
    return PageAccess(True, AuthState.AUTHENTICATED, outcome.reason, pname, login_performed=True,
                      evidence={"url": page.url, "login_evidence": outcome.evidence})
