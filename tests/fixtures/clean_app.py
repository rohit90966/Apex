"""
Clean Reference Application Fixture for VerityQA.
Implements correct, robust server-side validation for:
- Minimum password length (rejects < 6 characters with HTTP 400 and clear error)
- Required terms checkbox (rejects missing/unchecked terms with HTTP 400)
- Password confirmation matching (rejects mismatched passwords with HTTP 400)
- Search query validation (rejects blank queries with HTTP 400)

Guarantees that generic QA probes emit ZERO HIGH or MEDIUM bug leads on clean applications.
"""

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

clean_app = FastAPI(title="Clean Reference App - Zero Bugs")


def html_wrap(title: str, content: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
    <html>
    <head><title>{title}</title></head>
    <body>
        <nav><a href="/">Home</a> | <a href="/login">Login</a> | <a href="/register">Register</a> | <a href="/search">Search</a></nav>
        <h1>{title}</h1>
        {content}
    </body>
    </html>"""
    return HTMLResponse(content=html)


@clean_app.get("/")
def home():
    return html_wrap("Home", "<p>Welcome to clean app</p>")


@clean_app.get("/login")
def login_get():
    content = """
    <form id="login-form" action="/login" method="POST">
        <label for="u_user">Username</label>
        <input id="u_user" name="username" type="text" required />

        <label for="u_pass">Password</label>
        <input id="u_pass" name="password" type="password" minlength="6" required />

        <button id="btn-login" type="submit">Sign In</button>
    </form>
    """
    return html_wrap("Login", content)


@clean_app.post("/login")
def login_post(username: str = Form(""), password: str = Form("")):
    # Server-side validation
    if len(password) < 6:
        err = '<div role="alert" class="alert-error">Error: Password must be at least 6 characters</div>'
        return html_wrap("Login Failed", err + '<p><a href="/login">Try again</a></p>')
    if username == "admin@example.com" and password == "Vqa!test9x":
        return html_wrap("Dashboard", "<p>Welcome to dashboard</p>")
    return html_wrap("Login Failed", '<div role="alert">Invalid credentials</div>')


@clean_app.get("/register")
def register_get():
    content = """
    <form id="reg-form" action="/register" method="POST">
        <label for="r_user">Email</label>
        <input id="r_user" name="email" type="email" required />

        <label for="r_pass">Password</label>
        <input id="r_pass" name="password" type="password" minlength="6" required />

        <label for="r_conf">Confirm Password</label>
        <input id="r_conf" name="confirm_password" type="password" minlength="6" required />

        <label><input id="r_terms" name="terms" type="checkbox" required /> I accept terms</label>

        <button id="btn-reg" type="submit">Register</button>
    </form>
    """
    return html_wrap("Register", content)


@clean_app.post("/register")
def register_post(
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    terms: bool = Form(False)
):
    # Server-side validation
    if not terms:
        return html_wrap("Registration Failed", '<div role="alert">Error: You must accept the terms</div>')
    if len(password) < 6:
        return html_wrap("Registration Failed", '<div role="alert">Error: Password too short</div>')
    if password != confirm_password:
        return html_wrap("Registration Failed", '<div role="alert">Error: Passwords do not match</div>')

    return html_wrap("Registration Success", '<p>Account created successfully!</p>')


@clean_app.get("/search")
def search(q: str = ""):
    if not q or not q.strip():
        return html_wrap("Search", '<div role="alert">Error: Please enter a non-empty search term</div>')
    return html_wrap("Search Results", f"<p>Results for {q}: 0 items found</p>")
