"""
Dynamic, site-agnostic authentication and session management (app/auth/*), exercised through the real
execution paths: BrowserExecutor setup navigation, AuthenticationManager (investigation/reproducer) and the
credential registry. Fixture: a synthetic "Vault" app (not a benchmark site) whose login form structure,
challenges and session lifetime are switchable. No external website is contacted.
"""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import credentials as registry
from app.auth.login import AuthState
from app.execution.executor import BrowserExecutor
from app.schemas import ActionStep, ActionType, AssertionType, ExecutionAssertion, ExecutionStatus

USER, PASSWORD = "vault-user", "vault-pass-7781"

FORMS = {
    "standard": ('<form method="post" action="/signin"><label for="user">Username</label><input id="user" name="user">'
                 '<label for="pw">Password</label><input id="pw" name="pw" type="password"><button type="submit">Sign in</button></form>'),
    "different": ('<form method="post" action="/signin"><input name="email" placeholder="Work email or username">'
                  '<input name="secret" type="password" aria-label="Secret"><input type="submit" value="Continue"></form>'),
    "captcha": ('<form method="post" action="/signin"><input id="user" name="user"><input id="pw" name="pw" type="password">'
                '<div class="g-recaptcha" data-sitekey="x"></div><button type="submit">Sign in</button></form>'),
    "mfa": ('<form method="post" action="/signin"><input id="user" name="user"><input id="pw" name="pw" type="password">'
            '<input id="otp" name="otp" autocomplete="one-time-code"><button type="submit">Sign in</button></form>'),
    "ambiguous": ('<form method="post" action="/a"><input id="u1" name="u1"><input id="p1" name="p1" type="password"><button>Go</button></form>'
                  '<form method="post" action="/b"><input id="u2" name="u2"><input id="p2" name="p2" type="password"><button>Go</button></form>'),
}


def vault_app(form="standard", password=PASSWORD) -> FastAPI:
    app = FastAPI()
    app.state.sessions = set()
    app.state.login_posts = []          # (user, password_length, ok) — the fixture's own server log

    def authed(request: Request) -> bool:
        return request.cookies.get("vault") in app.state.sessions

    def page(body, request):
        nav = "<a href='/logout' id='logout'>Log out</a>" if authed(request) else "<a href='/signin'>Sign in</a>"
        return HTMLResponse(f"<html><body><nav>{nav}</nav><main>{body}</main></body></html>")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page("<h1>Vault</h1><a href='/public'>Public</a>", request)

    @app.get("/public", response_class=HTMLResponse)
    def public(request: Request):
        return page("<h1>Public notes</h1>", request)

    @app.get("/signin", response_class=HTMLResponse)
    def signin_page(request: Request, error: str = ""):
        alert = "<div role='alert'>Invalid login</div>" if error else ""
        return page(f"<h1>Sign in</h1>{alert}{FORMS[form]}", request)

    @app.post("/signin")
    async def signin(request: Request):
        data = await request.form()
        user = data.get("user") or data.get("email")
        pwd = data.get("pw") or data.get("secret") or ""
        ok = user == USER and pwd == password
        app.state.login_posts.append((user, len(pwd), ok))
        if not ok:
            return RedirectResponse("/signin?error=1", status_code=303)
        token = f"t{len(app.state.login_posts)}"
        app.state.sessions.add(token)
        resp = RedirectResponse("/vault", status_code=303)
        resp.set_cookie("vault", token)
        return resp

    @app.get("/vault", response_class=HTMLResponse)
    def vault(request: Request):
        if not authed(request):
            return RedirectResponse("/signin?next=/vault", status_code=303)
        return page("<h1>Your vault</h1><p id='balance'>Balance: 42</p>", request)

    @app.get("/logout")
    def logout(request: Request):
        app.state.sessions.discard(request.cookies.get("vault"))
        return RedirectResponse("/", status_code=303)

    # Cart that needs an item before "Remove" exists (prerequisite through the UI)
    @app.get("/shop", response_class=HTMLResponse)
    def shop(request: Request):
        return page("<h1>Shop</h1><form method='post' action='/cart/add'><button id='add'>Add to cart</button></form>", request)

    @app.post("/cart/add")
    def add(request: Request):
        resp = RedirectResponse("/cart", status_code=303)
        resp.set_cookie("cart", "1")
        return resp

    @app.get("/cart", response_class=HTMLResponse)
    def cart(request: Request):
        items = "<li class='item'>Widget <button id='remove'>Remove</button></li>" if request.cookies.get("cart") else ""
        return page(f"<h1>Cart</h1><ul>{items}</ul><p id='count'>{1 if items else 0} item(s)</p>", request)

    return app


@pytest.fixture
def vault(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("VERITYQA_CREDENTIALS_") or k.startswith("VERITYQA_TEST_") or k in ("TARGET_USERNAME", "TARGET_PASSWORD"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VERITYQA_ENV_FILE", "__no_env_file__")
    servers = []

    def start(**kw):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        app = vault_app(**kw)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        servers.append(server)
        return f"http://127.0.0.1:{port}", app

    yield start
    for s in servers:
        s.should_exit = True


def configure(monkeypatch, name, origin, password=PASSWORD, user=USER):
    monkeypatch.setenv(f"VERITYQA_CREDENTIALS_{name}_ORIGIN", origin)
    monkeypatch.setenv(f"VERITYQA_CREDENTIALS_{name}_USERNAME", user)
    monkeypatch.setenv(f"VERITYQA_CREDENTIALS_{name}_PASSWORD", password)


def open_and_check(base, path="/vault", text="Your vault", requires_login=False):
    return [ActionStep(step_number=1, action=ActionType.NAVIGATE, value=base + path, setup=True, description="open",
                       requires_login=requires_login, login_url=base + "/signin"),
            ActionStep(step_number=2, action=ActionType.OBSERVE_STATE, description="check",
                       assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE, expected_value=text,
                                                    description="page content"))]


def run(executor, steps, test_id="T"):
    return asyncio.run(executor.execute_steps(test_id, test_id, steps))


def auth_of(res):
    return res.evidence_retained.get("auth", [{}])[0]


# 1 + 5. Protected page redirects to login; configured profile logs in and the page is reached
def test_login_with_configured_profile_reaches_protected_page(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    res = run(BrowserExecutor(), open_and_check(base))
    assert res.status == ExecutionStatus.PASSED, res.failure_reason
    a = auth_of(res)
    assert a["state"] == "AUTHENTICATED" and a["login_performed"] and a["profile"] == "VAULT"
    assert app.state.login_posts == [(USER, len(PASSWORD), True)]


# 2. Missing credentials
def test_missing_credentials_is_blocked_not_attempted(vault):
    base, app = vault()
    res = run(BrowserExecutor(), open_and_check(base))
    assert res.status == ExecutionStatus.BLOCKED and "AUTH_CREDENTIALS_MISSING" in res.failure_reason
    assert res.assertions_evaluated == 0 and app.state.login_posts == []     # no fabricated login attempt


# 3 + 14. Incorrect credentials: setup failure stays BLOCKED
def test_incorrect_credentials_are_blocked(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base, password="wrong-password")
    res = run(BrowserExecutor(), open_and_check(base))
    assert res.status == ExecutionStatus.BLOCKED and "LOGIN_FAILED" in res.failure_reason
    assert res.assertions_evaluated == 0 and res.evidence_retained["setup_failure"]["underlying_status"] == "BLOCKED"


# 4. Public pages are not forced through login
def test_public_page_needs_no_login(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    res = run(BrowserExecutor(), open_and_check(base, "/public", "Public notes"))
    assert res.status == ExecutionStatus.PASSED and auth_of(res)["state"] == "AUTH_NOT_REQUIRED"
    assert app.state.login_posts == []


# 6. Session reuse within one run
def test_session_is_reused_across_tests_of_a_run(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    ex = BrowserExecutor()
    first, second = run(ex, open_and_check(base), "T1"), run(ex, open_and_check(base), "T2")
    assert first.status == second.status == ExecutionStatus.PASSED
    assert auth_of(second)["session_reused"] and not auth_of(second)["login_performed"]
    assert len(app.state.login_posts) == 1


# 7. Session expiry: bounded re-authentication
def test_expired_session_is_reestablished_once(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    ex = BrowserExecutor()
    assert run(ex, open_and_check(base), "T1").status == ExecutionStatus.PASSED
    app.state.sessions.clear()                                                  # server expires every session
    res = run(ex, open_and_check(base), "T2")
    assert res.status == ExecutionStatus.PASSED and auth_of(res)["login_performed"]
    assert len(app.state.login_posts) == 2


# 8. A different form structure is discovered from the DOM
def test_login_form_with_different_structure_is_discovered(vault, monkeypatch):
    base, app = vault(form="different")
    configure(monkeypatch, "VAULT", base)
    res = run(BrowserExecutor(), open_and_check(base))
    assert res.status == ExecutionStatus.PASSED, res.failure_reason
    assert app.state.login_posts[-1][2] is True


# 9. Multiple origins keep separate credentials and sessions
def test_multiple_origins_use_their_own_credentials(vault, monkeypatch):
    base_a, app_a = vault(password="alpha-pass-1")
    base_b, app_b = vault(password="bravo-pass-2")
    configure(monkeypatch, "ALPHA", base_a, password="alpha-pass-1")
    configure(monkeypatch, "BRAVO", base_b, password="bravo-pass-2")
    ex = BrowserExecutor()
    assert run(ex, open_and_check(base_a), "A").status == ExecutionStatus.PASSED
    res_b = run(ex, open_and_check(base_b), "B")
    assert res_b.status == ExecutionStatus.PASSED and not auth_of(res_b)["session_reused"]   # A's session not offered to B
    assert app_a.state.login_posts == [(USER, len("alpha-pass-1"), True)]
    assert app_b.state.login_posts == [(USER, len("bravo-pass-2"), True)]


def test_similar_hostname_never_matches_a_profile(monkeypatch):
    monkeypatch.setenv("VERITYQA_ENV_FILE", "__no_env_file__")
    configure(monkeypatch, "SITE", "http://localhost:8005")
    assert registry.resolve("http://localhost:8005/x")[0] == registry.FOUND
    assert registry.resolve("http://127.0.0.1:8005/x")[0] == registry.MISSING     # same port, different host
    assert registry.resolve("http://localhost:8006/x")[0] == registry.MISSING
    configure(monkeypatch, "OTHER", "http://localhost:8005")
    assert registry.resolve("http://localhost:8005/")[0] == registry.AMBIGUOUS       # never guessed
    assert registry.resolve("http://localhost:8005/", profile="other")[1].name == "OTHER"


# 10. A required cart item is created through the UI before the action under test
def test_prerequisite_cart_item_is_created_first(vault):
    base, _ = vault()
    steps = [ActionStep(step_number=1, action=ActionType.NAVIGATE, value=base + "/shop", setup=True, description="open shop"),
             ActionStep(step_number=2, action=ActionType.CLICK, selector="#add", setup=True, description="prerequisite: add item"),
             ActionStep(step_number=3, action=ActionType.CLICK, selector="#remove", description="remove",
                        assertion=ExecutionAssertion(assertion_type=AssertionType.URL_CONTAINS, expected_value="/cart", description="still on cart"))]
    assert run(BrowserExecutor(), steps).status == ExecutionStatus.PASSED
    # Without the prerequisite the control is absent: a locator problem, never a product failure
    missing = [ActionStep(step_number=1, action=ActionType.NAVIGATE, value=base + "/cart", setup=True, description="open cart"),
               steps[2]]
    res = run(BrowserExecutor(), missing)
    assert res.status != ExecutionStatus.FAILED and res.status != ExecutionStatus.PASSED


# 11. CAPTCHA / MFA / ambiguous forms: safe BLOCKED, never bypassed
@pytest.mark.parametrize("form,reason", [("captcha", "CAPTCHA"), ("mfa", "MFA"), ("ambiguous", "plausible login forms")])
def test_challenges_and_ambiguous_forms_are_blocked(vault, monkeypatch, form, reason):
    base, app = vault(form=form)
    configure(monkeypatch, "VAULT", base)
    res = run(BrowserExecutor(), open_and_check(base))
    assert res.status == ExecutionStatus.BLOCKED and "AUTH_UNRESOLVED" in res.failure_reason and reason in res.failure_reason
    assert app.state.login_posts == []


# 12. Credentials never appear in results, evidence or the registry description
def test_credentials_are_redacted_everywhere(vault, monkeypatch):
    base, _ = vault()
    configure(monkeypatch, "VAULT", base)
    ok = run(BrowserExecutor(), open_and_check(base))
    configure(monkeypatch, "VAULT", base, password="wrong-password-xyz")
    bad = run(BrowserExecutor(), open_and_check(base))
    blob = json.dumps([ok.model_dump(mode="json"), bad.model_dump(mode="json"), registry.describe()])
    assert PASSWORD not in blob and "wrong-password-xyz" not in blob
    from app.runs.redaction import collect_known_secrets
    assert "wrong-password-xyz" in collect_known_secrets(base)             # the redactor knows profile secrets


# 13. Independent tests do not contaminate each other's authentication
def test_logout_in_one_test_does_not_break_the_next(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    ex = BrowserExecutor()
    logout = open_and_check(base) + [ActionStep(step_number=3, action=ActionType.CLICK, selector="#logout", description="log out",
                                                assertion=ExecutionAssertion(assertion_type=AssertionType.TEXT_VISIBLE,
                                                                             expected_value="Vault", description="home"))]
    assert run(ex, logout, "T1").status == ExecutionStatus.PASSED            # server session invalidated here
    res = run(ex, open_and_check(base), "T2")
    assert res.status == ExecutionStatus.PASSED and len(app.state.login_posts) == 2


def test_session_reuse_can_be_disabled_for_fresh_contexts(vault, monkeypatch):
    base, app = vault()
    configure(monkeypatch, "VAULT", base)
    monkeypatch.setenv("VERITYQA_AUTH_SESSION_REUSE", "0")
    ex = BrowserExecutor()
    run(ex, open_and_check(base), "T1"); run(ex, open_and_check(base), "T2")
    assert len(app.state.login_posts) == 2


# Investigation / reproducer path uses the same discovery and the profile registry
def test_investigation_login_uses_profile_and_dom_discovery(vault, monkeypatch):
    from app.investigation_agent.authenticator import AuthenticationManager
    from app.investigation_agent.browser_session import StatefulBrowserSession
    base, app = vault(form="different")
    configure(monkeypatch, "VAULT", base)
    creds = AuthenticationManager.discover_credentials(base + "/vault", None)
    assert creds["profile"] == "VAULT" and creds["source"] == "ENVIRONMENT"
    session = StatefulBrowserSession(headless=True)
    session.start()
    try:
        session.navigate(base + "/signin")
        ok, why, signals = AuthenticationManager.attempt_authentication(session, creds)
        assert ok, why
        assert PASSWORD not in json.dumps(signals) and PASSWORD not in why
    finally:
        session.close()


def test_planner_entry_navigation_is_auth_aware():
    from test_multi_website_generalization import shop_report
    from app.test_planning.planner import KnowledgeGuidedTestPlanner
    cases, _, _ = KnowledgeGuidedTestPlanner().plan_test_suite_sync(shop_report(), target_count=300)
    entries = [c.steps[0] for c in cases if c.steps and c.steps[0].action == ActionType.NAVIGATE and c.steps[0].setup]
    assert entries and all("${CREDENTIAL" not in (s.value or "") for c in cases for s in c.steps if s.setup)
    protected = [e for e in entries if e.requires_login]
    assert protected and all(e.login_url for e in protected)
