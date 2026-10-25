"""
VerityQA Local Demo Target Application — Apex Demo Portal
Serves HTML forms with POST handlers and 4 deterministic planted bugs
that the autonomous QA agent should detect.

Planted Bug Manifest
─────────────────────────────────────────────────────────────────────
BUG-01 (CRITICAL)  /login  POST      Password length bypass: login accepted
                                      even when password < 6 chars (missing len check)
BUG-02 (HIGH)      /register POST    Terms bypass: registration succeeds without
                                      the "terms" checkbox field being present
BUG-03 (HIGH)      /register POST    Password mismatch bypass: confirm_password
                                      is ignored; any values accepted
BUG-04 (MEDIUM)    /search  GET      Empty query allowed: search proceeds with
                                      blank q, returning no validation response
─────────────────────────────────────────────────────────────────────
"""

import os
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Optional

app = FastAPI(title="VerityQA Local Demo Target App — Apex Portal with Planted Bugs")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def planted_bugs_enabled() -> bool:
    """VERITYQA_FIXTURE_MODE=baseline serves the defect-free behaviour (default: bug-enabled)."""
    return os.environ.get("VERITYQA_FIXTURE_MODE", "buggy").strip().lower() != "baseline"


def ground_truth_disclosed() -> bool:
    """The planted-bug manifest is only served to evaluators that opt in explicitly."""
    return os.environ.get("VERITYQA_FIXTURE_DISCLOSE_BUGS") == "1"


def load_template(filename: str) -> str:
    path = os.path.join(TEMPLATES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def page_wrapper(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: sans-serif; margin: 40px; background: #fafafa; }}
    .card {{ max-width: 500px; margin: 40px auto; background: white; padding: 30px;
             border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); }}
    .success {{ color: #28a745; font-weight: bold; border: 1px solid #28a745; padding: 12px;
                border-radius: 4px; margin-top: 14px; }}
    .error   {{ color: #dc3545; font-weight: bold; border: 1px solid #dc3545; padding: 12px;
                border-radius: 4px; margin-top: 14px; }}
    .info    {{ color: #0066cc; padding: 12px; border: 1px solid #ccc; border-radius: 4px;
                margin-top: 14px; }}
    nav a {{ margin-right: 15px; font-weight: bold; color: #0066cc; text-decoration: none; }}
    nav {{ margin-bottom: 24px; }}
  </style>
</head>
<body>
  <nav>
    <a href="/">Home</a><a href="/login">Login</a>
    <a href="/registration">Register</a><a href="/search">Search</a>
  </nav>
  <div class="card">{body}</div>
</body>
</html>"""


# ── Static GET Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(load_template("index.html"))


@app.get("/login", response_class=HTMLResponse)
async def login_get():
    return HTMLResponse(load_template("login.html"))


@app.get("/registration", response_class=HTMLResponse)
async def registration_get():
    return HTMLResponse(load_template("registration.html"))


@app.get("/forgot", response_class=HTMLResponse)
async def forgot():
    body = "<h2>Password Reset</h2><p>Enter your email to receive a reset link.</p>"
    return HTMLResponse(page_wrapper("Forgot Password", body))


# ── POST Handlers with Planted Bugs ───────────────────────────────────────────

@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    """
    BUG-01 PLANTED: Password length check is missing.
    A correct server should reject passwords shorter than 6 characters.
    This implementation accepts any non-empty password regardless of length.
    """
    if not username.strip() or not password.strip():
        body = """
        <h2>Sign In to Apex</h2>
        <div class="error">Both username and password are required.</div>
        <p><a href="/login">Try again</a></p>
        """
        return HTMLResponse(page_wrapper("Login Error", body))

    # ── BUG-01: Should enforce len(password) >= 6 but intentionally does NOT ──
    # Correct code: if len(password) < 6: return error
    # Actual code: proceeds regardless ← PLANTED BUG (baseline mode enforces the rule)
    if not planted_bugs_enabled() and len(password) < 6:
        body = """
        <h2>Sign In to Apex</h2>
        <div class="error" id="loginError">Password must be at least 6 characters.</div>
        <p><a href="/login">Try again</a></p>
        """
        return HTMLResponse(page_wrapper("Login Error", body))

    # Demo: only admit one specific credential pair as valid
    if username.strip().lower() == "admin@apex.com" and password == "Secret123!":
        body = """
        <h2>Welcome, Admin</h2>
        <div class="success">Login successful. Redirecting to dashboard...</div>
        <p id="dashboard-link"><a href="/">Go to Dashboard</a></p>
        """
    else:
        # BUG-01 surface: SHORT password is accepted with the generic "Invalid credentials" path
        # instead of a dedicated "password too short" validation error
        body = f"""
        <h2>Sign In to Apex</h2>
        <div class="error" id="loginError">Invalid credentials. Please try again.</div>
        <p><a href="/login">Try again</a></p>
        """

    return HTMLResponse(page_wrapper("Login", body))


@app.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    fullname: str = Form(default=""),
    email: str = Form(default=""),
    password: str = Form(default=""),
    confirm_password: str = Form(default=""),
    terms: Optional[str] = Form(default=None),
):
    """
    BUG-02 PLANTED: Terms bypass — registration succeeds even when terms=None.
    BUG-03 PLANTED: Password mismatch bypass — confirm_password is not compared to password.
    """
    errors = []

    if not fullname.strip():
        errors.append("Full name is required.")
    if not email.strip() or "@" not in email:
        errors.append("Valid email address is required.")
    if not password.strip():
        errors.append("Password is required.")

    # ── BUG-03 PLANTED: Should check password == confirm_password, but does NOT ──
    # Correct: if password != confirm_password: errors.append(...)
    # Actual: comparison is completely absent ← PLANTED BUG

    # ── BUG-02 PLANTED: Should enforce terms is not None, but does NOT ──
    # Correct: if not terms: errors.append("You must accept the Terms of Service.")
    # Actual: terms value is silently ignored ← PLANTED BUG (baseline mode enforces both rules)
    if not planted_bugs_enabled():
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if not terms:
            errors.append("You must accept the Terms of Service.")

    if errors:
        bullets = "".join(f"<li>{e}</li>" for e in errors)
        body = f"""
        <h2>Registration Error</h2>
        <div class="error"><ul>{bullets}</ul></div>
        <p><a href="/registration">Try again</a></p>
        """
        return HTMLResponse(page_wrapper("Registration Error", body))

    body = f"""
    <h2>Account Created</h2>
    <div class="success">
      Welcome, {fullname}! Your Apex account has been created successfully.
      <br>A confirmation email has been sent to {email}.
    </div>
    <p><a href="/login">Sign in now</a></p>
    """
    return HTMLResponse(page_wrapper("Registration Success", body))


# ── Search GET handler with BUG-04 ────────────────────────────────────────────

@app.get("/search", response_class=HTMLResponse)
async def search_get(q: Optional[str] = None):
    """
    BUG-04 PLANTED: Empty search query is accepted and returns a generic
    "No results found" instead of a validation error prompting for input.
    A correct implementation should reject blank queries with a clear message.
    """
    if q is None:
        # No query submitted — show search form
        return HTMLResponse(load_template("search.html"))

    # ── BUG-04 PLANTED: Should reject empty q with validation message ──
    # Correct: if not q.strip(): return validation error
    # Actual: empty string proceeds to "search" ← PLANTED BUG (baseline mode rejects it)
    if not planted_bugs_enabled() and not q.strip():
        body = """
        <h2>Catalog Search</h2>
        <div class="error" role="alert">Please enter a search term.</div>
        <form action="/search" method="GET">
          <input type="search" name="q" value="" placeholder="Search..." style="width:75%;padding:8px;">
          <button type="submit" id="searchBtn">Search</button>
        </form>
        """
        return HTMLResponse(page_wrapper("Search", body))

    CATALOG = [
        {"id": "MS-001", "name": "Microservices Architecture Guide"},
        {"id": "SC-002", "name": "Security Scanner Toolkit"},
        {"id": "CI-003", "name": "Continuous Integration Agent"},
        {"id": "API-004", "name": "API Gateway Module"},
        {"id": "OBS-005", "name": "Observability Dashboard Plugin"},
    ]

    q_lower = q.strip().lower()
    if q_lower:
        matches = [item for item in CATALOG if q_lower in item["name"].lower()]
    else:
        # BUG-04: blank query produces "no results" instead of validation error
        matches = []

    if matches:
        items_html = "".join(
            f'<li class="result-item"><strong>{m["id"]}</strong> — {m["name"]}</li>'
            for m in matches
        )
        results_html = f"""
        <h3>Search Results for "{q}"</h3>
        <ul style="list-style:none;padding:0">{items_html}</ul>
        """
    else:
        results_html = f"""
        <h3>No results found for "{q}"</h3>
        <p class="info">Try different keywords or browse the categories below.</p>
        """

    body = f"""
    <h2>Catalog Search</h2>
    <form action="/search" method="GET">
      <input type="search" name="q" value="{q}" placeholder="Search..." style="width:75%;padding:8px;">
      <button type="submit" id="searchBtn" style="padding:8px 14px;background:#0066cc;color:white;border:none;border-radius:4px;">Search</button>
    </form>
    {results_html}
    """
    return HTMLResponse(page_wrapper(f"Search: {q}", body))


# ── Debug/Info endpoint ────────────────────────────────────────────────────────

@app.get("/api/bugs", response_class=JSONResponse)
async def list_planted_bugs():
    """Exposes the planted bug manifest to evaluators that opt in (never to the agent by default)."""
    if not ground_truth_disclosed():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return JSONResponse({
        "planted_bugs": [
            {
                "bug_id": "BUG-01",
                "route": "POST /login",
                "severity": "CRITICAL",
                "category": "Authentication",
                "title": "Password length validation bypass",
                "description": "Server accepts passwords of any length including 1 character",
                "expected_behavior": "Reject password shorter than 6 characters with validation error",
                "actual_buggy_behavior": "Login attempted regardless of password length",
            },
            {
                "bug_id": "BUG-02",
                "route": "POST /register",
                "severity": "HIGH",
                "category": "Registration",
                "title": "Terms of Service acceptance bypass",
                "description": "Registration succeeds without terms checkbox being checked",
                "expected_behavior": "Reject registration if terms field is absent or unchecked",
                "actual_buggy_behavior": "Registration succeeds silently ignoring terms field",
            },
            {
                "bug_id": "BUG-03",
                "route": "POST /register",
                "severity": "HIGH",
                "category": "Registration",
                "title": "Password confirmation mismatch bypass",
                "description": "confirm_password is never compared to password",
                "expected_behavior": "Reject registration when password != confirm_password",
                "actual_buggy_behavior": "Registration succeeds with mismatched passwords",
            },
            {
                "bug_id": "BUG-04",
                "route": "GET /search",
                "severity": "MEDIUM",
                "category": "Search",
                "title": "Empty search query accepted without validation",
                "description": "Blank search query returns generic 'no results' instead of validation error",
                "expected_behavior": "Return clear validation message when query is empty",
                "actual_buggy_behavior": "Returns 'No results found for \\\"\\\"' without guiding the user",
            },
        ]
    })
