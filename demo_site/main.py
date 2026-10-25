"""
Harbor Events — single demo application for VerityQA (event listings, accounts, ticket bookings).

Run (bugs on):   .\\venv\\Scripts\\python.exe -m uvicorn demo_site.main:app --host 127.0.0.1 --port 8001
Run (bugs off):  $env:VERITYQA_FIXTURE_MODE='baseline'; then the same command
Demo account:    demo@harbor.test / Harbor#2026

The planted defects are listed only in benchmarks/ground_truth.py (evaluator side). This app never
serves that list, so the agent under test cannot read it. Code paths marked PLANTED behave correctly
when VERITYQA_FIXTURE_MODE=baseline.
"""

import html
import os
import re
import secrets
from datetime import date
from typing import Dict, List, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(title="Harbor Events")

# Diagnostics only: HARBOR_REQUEST_LOG=<path> appends one JSON line per POST (passwords masked by length).
if os.environ.get("HARBOR_REQUEST_LOG"):
    import json as _json
    import time as _time
    from starlette.requests import Request as _Request

    @app.middleware("http")
    async def _log_posts(request: _Request, call_next):
        body = (await request.body()).decode(errors="replace") if request.method == "POST" else ""
        response = await call_next(request)
        if request.method == "POST":
            from urllib.parse import parse_qsl
            fields = {k: (f"<{len(v)} chars, ok={v == USERS.get('demo@harbor.test', {}).get('password')}>" if "password" in k else v)
                      for k, v in parse_qsl(body, keep_blank_values=True)}
            with open(os.environ["HARBOR_REQUEST_LOG"], "a", encoding="utf-8") as fh:
                fh.write(_json.dumps({"t": _time.time(), "path": request.url.path, "status": response.status_code,
                                      "ua": request.headers.get("user-agent", "")[:40], "fields": fields}) + "\n")
        return response


def bugs_on() -> bool:
    return os.environ.get("VERITYQA_FIXTURE_MODE", "buggy").strip().lower() != "baseline"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

USERS: Dict[str, Dict[str, str]] = {
    "demo@harbor.test": {"name": "Dana Demo", "password": "Harbor#2026"},
}

EVENTS: List[Dict] = [
    {"id": 1, "title": "Harbor Jazz Night", "category": "Music", "date": date(2026, 11, 14), "price": 45.00, "featured": False},
    {"id": 2, "title": "Intro to Pottery", "category": "Workshop", "date": date(2026, 11, 20), "price": 120.00, "featured": False},
    {"id": 3, "title": "City Marathon 10K", "category": "Sports", "date": date(2026, 12, 5), "price": 18.00, "featured": True},
    {"id": 4, "title": "Indie Folk Evening", "category": "Music", "date": date(2026, 12, 12), "price": 75.00, "featured": False},
    {"id": 5, "title": "Product Design Summit", "category": "Conference", "date": date(2027, 1, 9), "price": 250.00, "featured": True},
    {"id": 6, "title": "Watercolour Basics", "category": "Workshop", "date": date(2027, 1, 16), "price": 9.50, "featured": False},
]
CATEGORIES = ["Music", "Workshop", "Sports", "Conference"]
SORTS = [("date_asc", "Date: Soonest first"), ("name_asc", "Name: A to Z"),
         ("price_asc", "Price: Low to High"), ("price_desc", "Price: High to Low")]

SESSIONS: Dict[str, str] = {}  # token -> email
BOOKINGS: List[Dict] = [
    {"id": 1, "email": "demo@harbor.test", "event_id": 1, "attendee": "Dana Demo", "quantity": 2, "status": "Confirmed"},
    {"id": 2, "email": "demo@harbor.test", "event_id": 4, "attendee": "Dana Demo", "quantity": 1, "status": "Confirmed"},
]


def _event(event_id: int) -> Optional[Dict]:
    return next((e for e in EVENTS if e["id"] == event_id), None)


def current_user(request: Request) -> Optional[str]:
    return SESSIONS.get(request.cookies.get("harbor_session", ""))


def money(v: float) -> str:
    return f"${v:,.2f}"


def page(request: Request, title: str, body: str) -> HTMLResponse:
    user = current_user(request)
    if user:
        account = (f'<a href="/bookings" id="nav-bookings">My Bookings</a>'
                   f'<span class="who">{html.escape(USERS[user]["name"])}</span>'
                   f'<a href="/logout" id="nav-logout">Log out</a>')
    else:
        account = '<a href="/login" id="nav-login">Log in</a><a href="/register" id="nav-register">Register</a>'
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)} · Harbor Events</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body {{ font-family: system-ui, sans-serif; margin: 0; background: #f6f7fb; color: #1d2433; }}
 header {{ background: #12355b; color: #fff; padding: 12px 24px; display: flex; gap: 18px; align-items: center; }}
 header a {{ color: #fff; text-decoration: none; }} header .brand {{ font-weight: 700; margin-right: auto; }}
 header .who {{ opacity: .8; }}
 main {{ max-width: 880px; margin: 24px auto; background: #fff; padding: 24px 32px; border-radius: 8px; }}
 label {{ display: block; margin-top: 12px; font-weight: 600; }}
 input[type=text], input[type=email], input[type=password], input[type=number], select {{ padding: 8px; width: 100%; max-width: 360px; box-sizing: border-box; }}
 button {{ margin-top: 16px; padding: 8px 18px; background: #12355b; color: #fff; border: 0; border-radius: 4px; cursor: pointer; }}
 table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }} th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e3e6ee; }}
 .alert {{ background: #fdecea; color: #a12622; padding: 10px 14px; border-radius: 4px; margin: 12px 0; }}
 .notice {{ background: #e7f6ec; color: #1d6b38; padding: 10px 14px; border-radius: 4px; margin: 12px 0; }}
 .inline {{ display: inline; }} .inline button {{ margin: 0; padding: 4px 10px; }}
</style></head>
<body><header><a class="brand" href="/">Harbor Events</a><a href="/events" id="nav-events">Events</a>{account}</header>
<main>{body}</main></body></html>""")


def errors_html(errors: List[str]) -> str:
    if not errors:
        return ""
    items = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
    return f'<div class="alert" role="alert"><ul>{items}</ul></div>'


# ── Home ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    upcoming = "".join(
        f'<li><a href="/events/{e["id"]}">{html.escape(e["title"])}</a> — {e["date"]:%d %b %Y}</li>'
        for e in sorted(EVENTS, key=lambda e: e["date"])[:3])
    return page(request, "Home", f"""
<h1>Welcome to Harbor Events</h1>
<p>Concerts, workshops and conferences around the harbour. Browse events and book tickets in a few clicks.</p>
<h2>Coming up</h2><ul id="upcoming">{upcoming}</ul>
<p><a href="/events" id="browse-events">Browse all events</a></p>""")


# ── Accounts ─────────────────────────────────────────────────────────────
def register_form(values: Dict[str, str], errors: List[str]) -> str:
    v = {k: html.escape(values.get(k, "")) for k in ("full_name", "email")}
    return f"""
<h1>Create an account</h1>{errors_html(errors)}
<form method="post" action="/register" id="register-form">
 <label for="full-name">Full name</label><input type="text" id="full-name" name="full_name" required maxlength="60" value="{v['full_name']}">
 <label for="email">Email</label><input type="email" id="email" name="email" required value="{v['email']}">
 <label for="password">Password</label><input type="password" id="password" name="password" required minlength="8">
 <label for="confirm-password">Confirm password</label><input type="password" id="confirm-password" name="confirm_password" required minlength="8">
 <label><input type="checkbox" id="terms" name="terms" value="yes" required> I accept the Terms of Service</label>
 <button type="submit" id="register-button">Create account</button>
</form>"""


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return page(request, "Register", register_form({}, []))


@app.post("/register", response_class=HTMLResponse)
def register(request: Request, full_name: str = Form(""), email: str = Form(""), password: str = Form(""),
             confirm_password: str = Form(""), terms: str = Form("")):
    errors = []
    if not full_name.strip():
        errors.append("Full name is required.")
    if not email.strip():
        errors.append("Email is required.")
    elif not bugs_on() and not EMAIL_RE.match(email.strip()):  # PLANTED: format only checked in the browser
        errors.append("Enter a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not bugs_on() and password != confirm_password:  # PLANTED: confirmation never compared
        errors.append("Passwords do not match.")
    if not terms:
        errors.append("You must accept the Terms of Service.")
    if email.strip().lower() in USERS:
        errors.append("An account with this email already exists.")
    if errors:
        return page(request, "Register", register_form({"full_name": full_name, "email": email}, errors))
    USERS[email.strip().lower()] = {"name": full_name.strip(), "password": password}
    return page(request, "Account created", f"""
<h1>Account created</h1><div class="notice" role="status">Welcome, {html.escape(full_name.strip())}! Your account is ready.</div>
<p><a href="/login" id="go-login">Log in</a></p>""")


def login_form(email: str, errors: List[str], next_url: str) -> str:
    return f"""
<h1>Log in</h1>{errors_html(errors)}
<form method="post" action="/login" id="login-form">
 <input type="hidden" name="next" value="{html.escape(next_url)}">
 <label for="login-email">Email</label><input type="email" id="login-email" name="email" required value="{html.escape(email)}">
 <label for="login-password">Password</label><input type="password" id="login-password" name="password" required>
 <button type="submit" id="login-button">Log in</button>
</form>
<p>No account? <a href="/register">Register</a></p>"""


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/events"):
    return page(request, "Log in", login_form("", [], next))


@app.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/events")):
    user = USERS.get(email.strip().lower())
    if not email.strip() or not password:
        return page(request, "Log in", login_form(email, ["Email and password are required."], next))
    if not user or user["password"] != password:
        return page(request, "Log in", login_form(email, ["Invalid email or password."], next))
    token = secrets.token_hex(16)
    SESSIONS[token] = email.strip().lower()
    dest = next if next.startswith("/") and not next.startswith("//") else "/events"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("harbor_session", token, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request):
    SESSIONS.pop(request.cookies.get("harbor_session", ""), None)
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("harbor_session")
    return resp


# ── Events ───────────────────────────────────────────────────────────────
@app.get("/events", response_class=HTMLResponse)
def events(request: Request, category: str = "", sort: str = "date_asc"):
    rows = list(EVENTS)
    if category:
        if bugs_on():
            rows = [e for e in rows if e["category"] == category or e["featured"]]  # PLANTED: featured bypass filter
        else:
            rows = [e for e in rows if e["category"] == category]
    if sort == "name_asc":
        rows.sort(key=lambda e: e["title"].lower())
    elif sort in ("price_asc", "price_desc"):
        key = (lambda e: money(e["price"])) if bugs_on() else (lambda e: e["price"])  # PLANTED: sorts formatted text
        rows.sort(key=key, reverse=sort == "price_desc")
    else:
        rows.sort(key=lambda e: e["date"])
    cat_opts = '<option value="">All categories</option>' + "".join(
        f'<option value="{c}"{" selected" if c == category else ""}>{c}</option>' for c in CATEGORIES)
    sort_opts = "".join(f'<option value="{k}"{" selected" if k == sort else ""}>{label}</option>' for k, label in SORTS)
    body_rows = "".join(
        f'<tr class="event-row"><td><a href="/events/{e["id"]}">{html.escape(e["title"])}</a></td>'
        f'<td class="event-category">{e["category"]}</td><td class="event-date">{e["date"]:%Y-%m-%d}</td>'
        f'<td class="event-price">{money(e["price"])}</td></tr>' for e in rows)
    return page(request, "Events", f"""
<h1>Events</h1>
<form method="get" action="/events" id="events-filter">
 <label for="category">Category</label><select id="category" name="category">{cat_opts}</select>
 <label for="sort">Sort by</label><select id="sort" name="sort">{sort_opts}</select>
 <button type="submit" id="apply-filters">Apply</button>
</form>
<table id="events-table"><thead><tr><th>Event</th><th>Category</th><th>Date</th><th>Price</th></tr></thead>
<tbody>{body_rows}</tbody></table>
<p id="result-count">{len(rows)} event(s)</p>""")


def booking_form(event: Dict, values: Dict[str, str], errors: List[str]) -> str:
    return f"""
<h2>Book tickets</h2>{errors_html(errors)}
<form method="post" action="/events/{event['id']}/book" id="booking-form">
 <label for="attendee-name">Attendee name</label><input type="text" id="attendee-name" name="attendee_name" required maxlength="60" value="{html.escape(values.get('attendee_name', ''))}">
 <label for="quantity">Tickets</label><input type="number" id="quantity" name="quantity" required min="1" max="10" value="{html.escape(values.get('quantity', '1'))}">
 <button type="submit" id="book-button">Book now</button>
</form>"""


def event_page(request: Request, event: Dict, breakdown: bool, form_html: str) -> HTMLResponse:
    fee_rate = "" if bugs_on() else "window.HARBOR_FEE_RATE = 0.05;"  # PLANTED: fee rate never configured
    breakdown_html = ""
    if breakdown:
        breakdown_html = f"""
<div id="price-breakdown"><h2>Price breakdown</h2>
<p>Ticket: <span id="bd-ticket">{money(event['price'])}</span></p>
<p>Service fee: <span id="bd-fee"></span></p><p>Total per ticket: <span id="bd-total"></span></p></div>
<script>{fee_rate}
 (function () {{
   var price = {event['price']};
   var fee = price * window.HARBOR_FEE_RATE;
   document.getElementById('bd-fee').textContent = '$' + fee.toFixed(2);
   document.getElementById('bd-total').textContent = '$' + (price + fee).toFixed(2);
 }})();
</script>"""
    return page(request, event["title"], f"""
<h1>{html.escape(event['title'])}</h1>
<p><strong>Category:</strong> {event['category']} · <strong>Date:</strong> {event['date']:%d %b %Y} · <strong>Price:</strong> {money(event['price'])}</p>
<p><a href="/events/{event['id']}?breakdown=1" id="price-breakdown-link">Show price breakdown</a></p>
{breakdown_html}{form_html}
<p><a href="/events">Back to events</a></p>""")


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_detail(request: Request, event_id: int, breakdown: int = 0):
    event = _event(event_id)
    if not event:
        return page(request, "Not found", "<h1>Event not found</h1>")
    if current_user(request):
        form_html = booking_form(event, {"attendee_name": USERS[current_user(request)]["name"]}, [])
    else:
        form_html = f'<p><a href="/login?next=/events/{event_id}" id="login-to-book">Log in to book tickets</a></p>'
    return event_page(request, event, bool(breakdown), form_html)


@app.post("/events/{event_id}/book")
def book(request: Request, event_id: int, attendee_name: str = Form(""), quantity: str = Form("")):
    user = current_user(request)
    event = _event(event_id)
    if not user:
        return RedirectResponse(f"/login?next=/events/{event_id}", status_code=303)
    if not event:
        return page(request, "Not found", "<h1>Event not found</h1>")
    errors = []
    if not attendee_name.strip():
        errors.append("Attendee name is required.")
    try:
        qty = int(quantity)
    except ValueError:
        qty = None
        errors.append("Enter the number of tickets.")
    if qty is not None and not bugs_on() and not 1 <= qty <= 10:  # PLANTED: range only enforced in the browser
        errors.append("You can book between 1 and 10 tickets.")
    if errors:
        return event_page(request, event, False, booking_form(event, {"attendee_name": attendee_name, "quantity": quantity}, errors))
    BOOKINGS.append({"id": max([b["id"] for b in BOOKINGS] + [0]) + 1, "email": user, "event_id": event_id,
                     "attendee": attendee_name.strip(), "quantity": qty, "status": "Confirmed"})
    return RedirectResponse("/bookings?flash=booked", status_code=303)


# ── Bookings ─────────────────────────────────────────────────────────────
FLASH = {"booked": "Booking confirmed.", "cancelled": "Booking cancelled."}


@app.get("/bookings", response_class=HTMLResponse)
def bookings(request: Request, flash: str = ""):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/bookings", status_code=303)
    mine = [b for b in BOOKINGS if b["email"] == user]
    rows = ""
    for b in mine:
        e = _event(b["event_id"])
        action = ("" if b["status"] == "Cancelled" else
                  f'<form class="inline" method="post" action="/bookings/{b["id"]}/cancel">'
                  f'<button type="submit" class="cancel-booking" id="cancel-booking-{b["id"]}">Cancel</button></form>')
        rows += (f'<tr class="booking-row" id="booking-{b["id"]}"><td>{html.escape(e["title"])}</td><td>{html.escape(b["attendee"])}</td>'
                 f'<td class="booking-qty">{b["quantity"]}</td><td class="booking-status">{b["status"]}</td><td>{action}</td></tr>')
    active = [b for b in mine if b["status"] != "Cancelled"]
    notice = f'<div class="notice" role="status">{FLASH[flash]}</div>' if flash in FLASH else ""
    return page(request, "My Bookings", f"""
<h1>My Bookings</h1>{notice}
<p id="ticket-total">Active tickets: {sum(b['quantity'] for b in active)}</p>
<table id="bookings-table"><thead><tr><th>Event</th><th>Attendee</th><th>Tickets</th><th>Status</th><th></th></tr></thead>
<tbody>{rows or '<tr><td colspan="5">No bookings yet.</td></tr>'}</tbody></table>""")


@app.post("/bookings/{booking_id}/cancel")
def cancel(request: Request, booking_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login?next=/bookings", status_code=303)
    booking = next((b for b in BOOKINGS if b["id"] == booking_id and b["email"] == user), None)
    if booking and not bugs_on():  # PLANTED: confirmation shown but the booking is never updated
        booking["status"] = "Cancelled"
    return RedirectResponse("/bookings?flash=cancelled", status_code=303)
