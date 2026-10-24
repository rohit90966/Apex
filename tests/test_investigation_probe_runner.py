"""
Tests for Phase 2: Action Vocabulary, Probe Runner, and Safety Governor.
Validates:
- ValueSpec synthetic data generation for all spec types (literal, unique_email, valid_password, etc.).
- Synthetic credentials never looking real (@example.test domain).
- StateDiff computation correctness (routes, classes, alerts, counters, auth, network).
- SafetyGovernor blocking prohibited keywords, unauthorized mutating actions, and budget exhaustion.
- ProbeRunner executing a multi-step probe on a local test page.
"""

import pytest
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from app.investigation_agent.models import (
    UIStateObservation,
    SemanticTarget,
    ValueSpec,
    ValueSpecType,
    ProbeStep,
    ProbeScript,
    NetworkEvent,
    ConsoleOrPageError,
    ActionRisk,
)
from app.investigation_agent.governor import SafetyGovernor
from app.investigation_agent.probe_runner import (
    generate_synthetic_value,
    compute_diff,
    ProbeRunner,
)
from app.investigation_agent.browser_session import StatefulBrowserSession


class LocalProbeServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html = """<!DOCTYPE html>
        <html>
        <head><title>Form Probe App</title></head>
        <body>
            <h1>Customer Portal</h1>
            <div id="badge-count" class="badge">0</div>
            <div id="msg-box"></div>
            <form id="signup-form" action="/submit" method="POST">
                <label for="u_email">Email</label>
                <input id="u_email" name="email" type="email" />

                <label for="u_pass">Password</label>
                <input id="u_pass" name="password" type="password" />

                <label for="u_role">Role</label>
                <select id="u_role" name="role">
                    <option value="user">User</option>
                    <option value="manager">Manager</option>
                </select>

                <label><input id="u_terms" name="terms" type="checkbox" /> Accept terms</label>

                <button id="submit-btn" type="button" onclick="submitForm()">Submit</button>
            </form>
            <script>
                function submitForm() {
                    document.getElementById('badge-count').innerText = "1";
                    document.getElementById('msg-box').innerHTML = "<div role='alert' class='alert-success'>Account Registered!</div>";
                    localStorage.setItem("session_token_key", "active");
                }
            </script>
        </body>
        </html>"""
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def local_probe_server():
    server = HTTPServer(("127.0.0.1", 0), LocalProbeServerHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_synthetic_data_generator_all_specs():
    run_id = "test_run_42"

    # 1. Unique email: must use example.test, never look real
    email = generate_synthetic_value(ValueSpec(kind=ValueSpecType.UNIQUE_EMAIL), run_id=run_id)
    assert email == f"qa+{run_id}@example.test"
    assert email.endswith(".test")

    # 2. Valid password: deterministic
    password = generate_synthetic_value(ValueSpec(kind=ValueSpecType.VALID_PASSWORD), run_id=run_id)
    assert password == f"Vqa!{run_id}9x"

    # 3. Blank
    blank = generate_synthetic_value(ValueSpec(kind=ValueSpecType.BLANK), run_id=run_id)
    assert blank == ""

    # 4. Text of length
    text_25 = generate_synthetic_value(ValueSpec(kind=ValueSpecType.TEXT_OF_LENGTH, length=25), run_id=run_id)
    assert len(text_25) == 25

    # 5. Mismatch of
    ctx = {"password": password}
    mismatch = generate_synthetic_value(
        ValueSpec(kind=ValueSpecType.MISMATCH_OF, ref_field="password"),
        run_id=run_id,
        context_values=ctx,
    )
    assert mismatch != password
    assert password in mismatch

    # 6. Constraint edge (minlength - 1)
    edge_under = generate_synthetic_value(
        ValueSpec(kind=ValueSpecType.CONSTRAINT_EDGE, constraint_type="minlength", edge_offset=-1),
        run_id=run_id,
        field_meta={"minlength": 8},
    )
    assert len(edge_under) == 7

    # 7. Constraint edge (maxlength + 1)
    edge_over = generate_synthetic_value(
        ValueSpec(kind=ValueSpecType.CONSTRAINT_EDGE, constraint_type="maxlength", edge_offset=1),
        run_id=run_id,
        field_meta={"maxlength": 15},
    )
    assert len(edge_over) == 16


def test_safety_governor_boundaries():
    # 1. Prohibited keyword blocked
    gov = SafetyGovernor()
    allowed, reason = gov.can_execute(
        action_type="CLICK_CONTROL",
        target="#btn-delete-account",
        current_url="http://127.0.0.1:8001",
        rationale="delete account",
    )
    assert allowed is False
    assert "delete account" in reason

    # 2. Untrusted host mutating block
    gov_restricted = SafetyGovernor(allow_mutating=False, trusted_hosts=["127.0.0.1"])
    allowed_mut, reason_mut = gov_restricted.can_execute(
        action_type="FILL",
        target="#user_input",
        current_url="https://external-untrusted-bank.com/transfer",
    )
    assert allowed_mut is False
    assert "Mutating interactions not permitted" in reason_mut

    # 3. Mutation budget exhaustion
    gov_budget = SafetyGovernor(allow_mutating=True, mutation_budget=2)
    ok1, _ = gov_budget.can_execute("FILL", "#input1", "http://127.0.0.1:8001")
    ok2, _ = gov_budget.can_execute("FILL", "#input2", "http://127.0.0.1:8001")
    ok3, reason3 = gov_budget.can_execute("FILL", "#input3", "http://127.0.0.1:8001")
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert "Mutation budget exhausted" in reason3


def test_state_diff_computation():
    before = UIStateObservation(
        state_id="st_root_view_1",
        state_class_id="cls_alpha",
        url="http://127.0.0.1/page1",
        normalized_route="page1",
        page_title="Page One",
        visible_text_snippet="Welcome to portal please login",
        dynamic_signals={"counter_value": "0", "alerts": []},
        storage_keys={"cookies": ["session"], "local_storage": []},
    )

    after = UIStateObservation(
        state_id="st_root_view_2",
        state_class_id="cls_alpha",
        url="http://127.0.0.1/page2",
        normalized_route="page2",
        page_title="Page Two",
        visible_text_snippet="Welcome to portal dashboard active items",
        dynamic_signals={"counter_value": "5", "alerts": ["Success!"]},
        storage_keys={"cookies": ["session", "auth_token"], "local_storage": ["user_pref"]},
        text_anomalies=["undefined"],
    )

    net_events = [
        NetworkEvent(seq=1, method="POST", url="http://127.0.0.1/api/login", resource_type="xhr", status=200, ok=True),
        NetworkEvent(seq=2, method="GET", url="http://127.0.0.1/api/items", resource_type="xhr", status=404, ok=False),
    ]

    diff = compute_diff(
        before=before,
        after=after,
        network_events=net_events,
        console_errors=[],
        mutations=[],
    )

    assert diff.route_changed is True
    assert diff.class_changed is False
    assert diff.counter_delta == 5
    assert "Success!" in diff.alerts_added
    assert "dashboard" in diff.text_added
    assert "login" in diff.text_removed
    assert "auth_token" in diff.auth_delta["cookies_added"]
    assert "user_pref" in diff.auth_delta["storage_added"]
    assert diff.network_summary["request_count"] == 2
    assert diff.network_summary["status_4xx_count"] == 1
    assert "undefined" in diff.text_anomalies_added


def test_probe_runner_local_form_execution(local_probe_server):
    session = StatefulBrowserSession(headless=True)
    try:
        session.start()
        ok, _, status = session.navigate(local_probe_server)
        assert ok is True

        # Build multi-step probe script
        script = ProbeScript(
            name="signup_happy_probe",
            family="forms",
            description="Fills and submits sign-up form",
            steps=[
                ProbeStep(
                    action_type="FILL",
                    target=SemanticTarget(role="textbox", name="Email", label="Email", css="#u_email"),
                    value_spec=ValueSpec(kind=ValueSpecType.UNIQUE_EMAIL),
                ),
                ProbeStep(
                    action_type="FILL",
                    target=SemanticTarget(role="textbox", name="Password", label="Password", css="#u_pass"),
                    value_spec=ValueSpec(kind=ValueSpecType.VALID_PASSWORD),
                ),
                ProbeStep(
                    action_type="SELECT_OPTION",
                    target=SemanticTarget(css="#u_role"),
                    value_spec=ValueSpec(kind=ValueSpecType.LITERAL, value="manager"),
                ),
                ProbeStep(
                    action_type="CHECK",
                    target=SemanticTarget(css="#u_terms"),
                ),
                ProbeStep(
                    action_type="CLICK_CONTROL",
                    target=SemanticTarget(role="button", name="Submit", css="#submit-btn"),
                ),
            ],
        )

        runner = ProbeRunner()
        result = runner.run_probe(session=session, script=script, run_id="run_test_01")

        assert result.status == "SUCCESS"
        assert result.steps_executed == 5
        assert result.diff is not None
        assert result.diff.counter_delta == 1
        assert any("Account Registered!" in alert for alert in result.diff.alerts_added)
        assert "session_token_key" in result.diff.auth_delta["storage_added"]

    finally:
        session.close()
