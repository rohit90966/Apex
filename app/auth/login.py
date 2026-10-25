"""
Site-agnostic login: discover the login form in the live DOM, submit the origin's credentials, and decide the
authentication state from observable evidence. Used by the investigation browser (sync Playwright), the test
executor (async Playwright) and the bug reproducer, so every path logs in the same way.

No selectors are assumed: the form is found from password/identity fields, labels and the submit control in the
page. A configured profile may name selectors when a page is ambiguous; every selector is validated against the
live DOM before use. CAPTCHA/MFA challenges and ambiguous forms stop with AUTH_UNRESOLVED (never bypassed).
Credential values are only ever passed to page.fill(); they are never logged or returned.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthState(str, Enum):
    UNKNOWN = "UNKNOWN"
    AUTH_NOT_REQUIRED = "AUTH_NOT_REQUIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CREDENTIALS_MISSING = "AUTH_CREDENTIALS_MISSING"
    LOGIN_IN_PROGRESS = "LOGIN_IN_PROGRESS"
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_FAILED = "LOGIN_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    AUTH_UNRESOLVED = "AUTH_UNRESOLVED"


@dataclass
class AuthOutcome:
    state: AuthState
    reason: str = ""
    evidence: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state in (AuthState.AUTHENTICATED, AuthState.AUTH_NOT_REQUIRED)


# One pass over the page: candidate login forms (with DOM-derived selectors), challenges, errors, session cues.
DISCOVERY_SCRIPT = r"""
() => {
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const esc = v => (window.CSS && CSS.escape) ? CSS.escape(v) : v;
  // Structural path for elements without id/name (validated against the DOM before use by the caller).
  const path = el => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      if (n.id) { parts.unshift('#' + esc(n.id)); return parts.join(' > '); }
      const same = Array.from(n.parentElement ? n.parentElement.children : []).filter(k => k.tagName === n.tagName);
      parts.unshift(n.tagName.toLowerCase() + (same.length > 1 ? ':nth-of-type(' + (same.indexOf(n) + 1) + ')' : ''));
    }
    return 'body > ' + parts.join(' > ');
  };
  const sel = el => {
    if (el.id) return '#' + esc(el.id);
    if (el.name && document.querySelectorAll(el.tagName.toLowerCase() + '[name="' + el.name + '"]').length === 1)
      return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    return path(el);
  };
  const labelOf = el => {
    const l = el.id ? document.querySelector('label[for="' + esc(el.id) + '"]') : null;
    return ((l && l.innerText) || el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '').trim().toLowerCase();
  };
  const containers = Array.from(document.querySelectorAll('form'));
  // Pages without <form>: treat the nearest common ancestor of a lone password field as the container.
  if (!containers.length) {
    const p = Array.from(document.querySelectorAll('input[type=password]')).find(visible);
    if (p) containers.push(p.closest('div, section, main, body'));
  }
  const forms = containers.map((f, i) => {
    const inputs = Array.from(f.querySelectorAll('input, select, textarea')).filter(visible);
    const pw = inputs.filter(x => (x.type || '').toLowerCase() === 'password');
    const ident = inputs.filter(x => ['text', 'email', 'tel', ''].includes((x.type || '').toLowerCase()) && x.tagName === 'INPUT');
    const user = ident.find(x => /user|email|login|account|name|mail|id/.test(labelOf(x))) || ident[0] || null;
    const submit = Array.from(f.querySelectorAll('button, input[type=submit], input[type=button], [role=button]'))
      .filter(visible).find(b => (b.type || '').toLowerCase() === 'submit' || /log\s*in|sign\s*in|continue|submit|enter/i.test(b.innerText || b.value || ''));
    const words = ((f.getAttribute && (f.getAttribute('action') || '') + ' ' + (f.id || '') + ' ' + (f.className || '')) || '').toLowerCase();
    return {
      index: i, password_count: pw.length, identity_count: ident.length,
      username_selector: user ? sel(user) : null, password_selector: pw[0] ? sel(pw[0]) : null,
      submit_selector: submit ? sel(submit) : null,
      submit_text: submit ? (submit.innerText || submit.value || '').trim().slice(0, 40) : '',
      login_words: /log\s*in|sign\s*in|signin|login|session|auth/.test(words + ' ' + (submit ? (submit.innerText || submit.value || '') : '')),
    };
  });
  const challenge = (() => {
    if (document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, [data-sitekey]')) return 'CAPTCHA';
    const mfa = Array.from(document.querySelectorAll('input')).filter(visible).find(x =>
      (x.autocomplete || '').toLowerCase() === 'one-time-code' || /otp|totp|2fa|mfa|one.?time|verification.?code|authenticator/i.test((x.name || '') + ' ' + (x.id || '') + ' ' + labelOf(x)));
    return mfa ? 'MFA' : null;
  })();
  const errSel = '[role="alert"], [data-test=error], .error-message, .error, .alert-danger, .alert-error, .invalid-feedback, .field-error';
  const err = Array.from(document.querySelectorAll(errSel)).find(e => visible(e) && (e.innerText || '').trim());
  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible)
    .filter(a => /log\s*in|sign\s*in/i.test(a.innerText || '') || /login|signin|sign-in/i.test(a.getAttribute('href')))
    .map(a => a.href).slice(0, 3);
  return {
    forms: forms, challenge: challenge, error: err ? err.innerText.trim().slice(0, 120) : null, login_links: links,
    password_visible: Array.from(document.querySelectorAll('input[type=password]')).some(visible),
    logout_visible: Array.from(document.querySelectorAll('a, button')).some(el => visible(el) && /log\s*out|sign\s*out/i.test(el.textContent || el.id || '')),
  };
}
"""


def choose_login_form(discovery: Dict[str, Any], creds: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Picks the login form from a discovery snapshot. Returns {"form": {...}} or {"state": AuthState, "reason": str}.
    A login form has exactly one visible password field and an identity field. Configured selectors win.
    """
    creds = creds or {}
    if discovery.get("challenge"):
        return {"state": AuthState.AUTH_UNRESOLVED,
                "reason": f"{discovery['challenge']} challenge present; automated login is not attempted"}
    if creds.get("password_selector") and creds.get("username_selector"):
        return {"form": {"username_selector": creds["username_selector"], "password_selector": creds["password_selector"],
                         "submit_selector": creds.get("submit_selector"), "configured": True}}
    candidates = [f for f in discovery.get("forms") or []
                  if f.get("password_count") == 1 and f.get("identity_count", 0) >= 1
                  and f.get("username_selector") and f.get("password_selector")]
    if len(candidates) > 1:
        labelled = [f for f in candidates if f.get("login_words")]
        candidates = labelled if len(labelled) == 1 else candidates
    if not candidates:
        return {"state": AuthState.AUTH_UNRESOLVED, "reason": "no login form (one password field plus an identity field) on the page"}
    if len(candidates) > 1:
        return {"state": AuthState.AUTH_UNRESOLVED,
                "reason": f"{len(candidates)} plausible login forms; configure *_USERNAME_SELECTOR/_PASSWORD_SELECTOR for this profile"}
    return {"form": candidates[0]}


def judge_submission(after: Dict[str, Any]) -> AuthOutcome:
    """Authentication state after submitting credentials, from the page that followed."""
    if after.get("challenge"):
        return AuthOutcome(AuthState.AUTH_UNRESOLVED, f"{after['challenge']} challenge after submitting credentials")
    if after.get("error"):
        return AuthOutcome(AuthState.LOGIN_FAILED, f"login error shown: {after['error']}")
    if after.get("password_visible"):
        return AuthOutcome(AuthState.LOGIN_FAILED, "login form still present after submission")
    evidence = ["login form dismissed"] + (["logout control present"] if after.get("logout_visible") else [])
    return AuthOutcome(AuthState.AUTHENTICATED, "; ".join(evidence), evidence)


def looks_like_login_gate(discovery: Dict[str, Any]) -> bool:
    """The current page asks for credentials (a login form or a challenge), i.e. access needs authentication."""
    return bool(discovery.get("challenge")) or any(
        f.get("password_count") == 1 and f.get("identity_count", 0) >= 1 for f in discovery.get("forms") or [])


# ── Sync (investigation browser, reproducer) ─────────────────────────────────────────────────────────────

def login_sync(page: Any, creds: Optional[Dict[str, str]], login_url: Optional[str] = None,
               navigate: Any = None) -> AuthOutcome:
    """Logs in on `page` (sync Playwright). `navigate(url)` must apply the caller's host/SSRF checks."""
    if not creds or not creds.get("username") or not creds.get("password"):
        return AuthOutcome(AuthState.CREDENTIALS_MISSING, "no credentials are configured for this origin")
    target = creds.get("login_url") or login_url
    if target and navigate is not None:
        ok = navigate(target)
        if ok is False:
            return AuthOutcome(AuthState.AUTH_UNRESOLVED, f"login page {target} could not be opened")
    choice = choose_login_form(page.evaluate(DISCOVERY_SCRIPT), creds)
    if "form" not in choice:
        return AuthOutcome(choice["state"], choice["reason"])
    form = choice["form"]
    for key in ("username_selector", "password_selector"):
        if not page.query_selector(form[key]):
            return AuthOutcome(AuthState.AUTH_UNRESOLVED, f"{key.replace('_', ' ')} '{form[key]}' not present")
    page.fill(form["username_selector"], creds["username"])
    page.fill(form["password_selector"], creds["password"])
    if form.get("submit_selector") and page.query_selector(form["submit_selector"]):
        page.click(form["submit_selector"])
    else:
        page.press(form["password_selector"], "Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=7000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    outcome = judge_submission(page.evaluate(DISCOVERY_SCRIPT))
    if outcome.state == AuthState.LOGIN_FAILED and "still present" in outcome.reason:
        page.wait_for_timeout(1500)
        outcome = judge_submission(page.evaluate(DISCOVERY_SCRIPT))
    return outcome


# ── Async (test executor) ────────────────────────────────────────────────────────────────────────────────

async def login_async(page: Any, creds: Optional[Dict[str, str]], login_url: Optional[str] = None,
                      timeout_ms: int = 8000) -> AuthOutcome:
    if not creds or not creds.get("username") or not creds.get("password"):
        return AuthOutcome(AuthState.CREDENTIALS_MISSING, "no credentials are configured for this origin")
    target = creds.get("login_url") or login_url
    if target:
        await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
    choice = choose_login_form(await page.evaluate(DISCOVERY_SCRIPT), creds)
    if "form" not in choice:
        return AuthOutcome(choice["state"], choice["reason"])
    form = choice["form"]
    for key in ("username_selector", "password_selector"):
        if not await page.query_selector(form[key]):
            return AuthOutcome(AuthState.AUTH_UNRESOLVED, f"{key.replace('_', ' ')} '{form[key]}' not present")
    await page.fill(form["username_selector"], creds["username"], timeout=timeout_ms)
    await page.fill(form["password_selector"], creds["password"], timeout=timeout_ms)
    if form.get("submit_selector") and await page.query_selector(form["submit_selector"]):
        await page.click(form["submit_selector"], timeout=timeout_ms)
    else:
        await page.press(form["password_selector"], "Enter")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(500)
    outcome = judge_submission(await page.evaluate(DISCOVERY_SCRIPT))
    if outcome.state == AuthState.LOGIN_FAILED and "still present" in outcome.reason:
        await page.wait_for_timeout(1500)
        outcome = judge_submission(await page.evaluate(DISCOVERY_SCRIPT))
    return outcome
