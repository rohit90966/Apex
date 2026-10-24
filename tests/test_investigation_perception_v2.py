"""
Tests for Phase 1: Perception v2
Validates:
- Network capture recording 404, redacting auth query params, keeping first 200 chars of body.
- Mark and events_since semantics for network and error events.
- Mutation observer recording dynamically inserted toast/elements.
- Extended form extraction (required, minlength, pattern, novalidate, label, submit_control_selector).
- State class identity: identical for /item/1 and /item/2, but distinct for different form field sets.
- Text anomaly scanning (undefined, NaN, [object Object], tracebacks).
- Storage snapshot captures keys/names only (no values).
- No site-specific strings remain in perception.py and browser_session.py.
"""

import pytest
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from app.investigation_agent.perception import (
    redact_url_params,
    scan_text_anomalies,
    compute_state_class_and_instance,
)
from app.investigation_agent.browser_session import StatefulBrowserSession
from app.investigation_agent.models import UIStateObservation, NetworkEvent, ConsoleOrPageError


class PerceptionTestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Error 404: The requested resource could not be found.")
            return

        # Main test page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "session_id=secret_cookie_val; Path=/")
        self.end_headers()
        html = """<!DOCTYPE html>
        <html>
        <head><title>Perception Test App</title></head>
        <body>
            <h1>Customer Registration</h1>
            <div id="counter-badge" class="badge">3</div>
            <form id="reg-form" action="/submit" method="POST" novalidate>
                <label for="f_email">Email Address</label>
                <input id="f_email" name="email" type="email" required placeholder="user@example.com" />

                <label for="f_code">Postal Code</label>
                <input id="f_code" name="code" type="text" minlength="5" maxlength="10" pattern="[0-9]+" />

                <label><input id="f_agree" name="agree" type="checkbox" checked /> I agree</label>

                <select id="f_role" name="role">
                    <option value="user">Standard User</option>
                    <option value="admin">Administrator</option>
                </select>

                <button id="btn_submit" type="submit">Complete Sign Up</button>
            </form>
            <div id="toast-container"></div>
            <script>
                try {
                    localStorage.setItem("user_theme", "dark");
                } catch(e) {}
            </script>
        </body>
        </html>"""
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress console logging in tests


@pytest.fixture(scope="module")
def local_test_server():
    server = HTTPServer(("127.0.0.1", 0), PerceptionTestHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_auth_query_parameter_redaction():
    url = "https://example.com/api/v1/resource?token=secret123&user=alice&password=mypassword&jwt=xyz"
    redacted = redact_url_params(url)
    assert "token=%5BREDACTED%5D" in redacted or "token=[REDACTED]" in redacted
    assert "password=%5BREDACTED%5D" in redacted or "password=[REDACTED]" in redacted
    assert "jwt=%5BREDACTED%5D" in redacted or "jwt=[REDACTED]" in redacted
    assert "user=alice" in redacted
    assert "secret123" not in redacted
    assert "mypassword" not in redacted


def test_text_anomaly_detection():
    clean_text = "Welcome to our customer portal. Please submit your application."
    assert scan_text_anomalies(clean_text) == []

    buggy_text = "Status: undefined. Total calculated: NaN. User object: [object Object]. Server: Internal Server Error"
    anomalies = scan_text_anomalies(buggy_text)
    assert "undefined" in anomalies
    assert "NaN" in anomalies
    assert "[object Object]" in anomalies
    assert "Internal Server Error" in anomalies

    trace_text = 'Exception in route:\nFile "app/main.py", line 42, in handler\nTypeError: cannot unpack None'
    trace_anomalies = scan_text_anomalies(trace_text)
    assert any("line 42" in a for a in trace_anomalies)
    assert any("TypeError" in a for a in trace_anomalies)


def test_two_level_state_identity_template_and_field_sets():
    headings = ["Product Details"]
    forms_a = [{
        "form_id": "cart-form",
        "fields": [{"name": "quantity", "type": "number"}, {"name": "variant", "type": "select"}]
    }]
    signals_empty = {"has_active_modal": False, "counter_value": "0"}

    # Class ID must be identical for item/1 and item/2
    class_id_1, inst_id_1 = compute_state_class_and_instance(
        url="https://shop.test/item/1",
        headings=headings,
        forms=forms_a,
        dynamic_signals=signals_empty,
        a11y_snapshot="heading 'Product Details'\nbutton 'Add to Cart'"
    )

    class_id_2, inst_id_2 = compute_state_class_and_instance(
        url="https://shop.test/item/2",
        headings=headings,
        forms=forms_a,
        dynamic_signals=signals_empty,
        a11y_snapshot="heading 'Product Details'\nbutton 'Add to Cart'"
    )

    assert class_id_1 == class_id_2, "State class ID must generalize over route numeric segments"
    assert inst_id_1 == inst_id_2

    # Different form field set must produce a different class ID
    forms_b = [{
        "form_id": "review-form",
        "fields": [{"name": "rating", "type": "number"}, {"name": "review_text", "type": "textarea"}]
    }]
    class_id_diff, _ = compute_state_class_and_instance(
        url="https://shop.test/item/1",
        headings=headings,
        forms=forms_b,
        dynamic_signals=signals_empty,
        a11y_snapshot="heading 'Product Details'\nbutton 'Add to Cart'"
    )
    assert class_id_1 != class_id_diff, "Different form structures must have distinct state class IDs"


def test_browser_session_perception_pipeline(local_test_server):
    """Validates real browser session DOM extraction, mutations, network capture, and form extraction."""
    session = StatefulBrowserSession(headless=True)
    try:
        session.start()
        ok, current_url, status = session.navigate(local_test_server)
        assert ok is True
        assert status == 200

        # Mark network events
        initial_mark = session.mark()

        # 1. Capture observation
        obs = session.capture_observation(state_prefix="test")
        assert obs.page_title == "Perception Test App"
        assert obs.state_class_id.startswith("cls_")
        assert obs.state_id.startswith("st_")
        assert len(obs.forms) == 1

        form = obs.forms[0]
        assert form["novalidate"] is True
        assert form["submit_control_selector"] == "#btn_submit"
        fields = {f["name"]: f for f in form["fields"]}
        assert fields["email"]["required"] is True
        assert fields["email"]["label"] == "Email Address"
        assert fields["code"]["minlength"] == 5
        assert fields["code"]["maxlength"] == 10
        assert fields["code"]["pattern"] == "[0-9]+"
        assert fields["agree"]["checkbox_state"] is True
        assert "admin" in fields["role"]["select_options"]

        # 2. Check storage snapshot keys only
        storage = session.storage_snapshot()
        assert "user_theme" in storage["local_storage"]
        assert "session_id" in storage["cookies"]
        # Values must NOT be stored
        assert "dark" not in storage["local_storage"]
        assert "secret_cookie_val" not in storage["cookies"]

        # 3. Dynamic mutation recording (toast injection)
        session.drain_mutations()
        session.page.evaluate("""() => {
            const toast = document.createElement('div');
            toast.className = 'toast-alert';
            toast.setAttribute('role', 'status');
            toast.innerText = 'Profile updated successfully!';
            document.getElementById('toast-container').appendChild(toast);
        }""")
        mutations = session.drain_mutations()
        assert len(mutations) >= 1
        added = [m for m in mutations if m.get("type") == "added"]
        assert any("Profile updated successfully!" in m.get("text", "") for m in added)

        # 4. A11y snapshot captured
        assert obs.a11y_snapshot is not None
        assert "Customer Registration" in obs.a11y_snapshot or "button" in obs.a11y_snapshot

        # 5. Network capture records a 404 with body snippet
        mark_before_404 = session.mark()
        session.page.evaluate("(url) => fetch(url).then(r => r.text()).catch(e => '')", f"{local_test_server}/missing?token=secret99")
        session.settle()
        recent_events = session.events_since(mark_before_404)
        evt_404 = [e for e in recent_events if e.status == 404]
        assert len(evt_404) == 1
        assert "token=%5BREDACTED%5D" in evt_404[0].url or "token=[REDACTED]" in evt_404[0].url
        assert "secret99" not in evt_404[0].url
        assert evt_404[0].response_snippet is not None
        assert "Error 404" in evt_404[0].response_snippet

    finally:
        session.close()


def test_site_agnostic_code_guard():
    """Guarantees that browser_session.py and perception.py do not contain forbidden site-specific identifiers."""
    import inspect
    import app.investigation_agent.perception as perc_mod
    import app.investigation_agent.browser_session as sess_mod

    perc_src = inspect.getsource(perc_mod)
    sess_src = inspect.getsource(sess_mod)

    forbidden = ["saucedemo", "shopping_cart", "inventory_list", "data-test", "BUG-0", "BUG-F2"]
    for word in forbidden:
        assert word not in perc_src, f"perception.py contains forbidden site-specific string: {word}"
        assert word not in sess_src, f"browser_session.py contains forbidden site-specific string: {word}"
