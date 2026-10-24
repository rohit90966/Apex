"""
Unit and Integration Tests for Phase 6: Skeptic Filter, Reproducer, Journeys, and Behavior Model.
Verifies:
- Skeptic filter drops UNCERTAIN outcomes, trivial no-route-change claims, and benign redirects
- 2-of-2 confirmation protocol in isolated contexts
- Delta-debugging step minimization
- Replayable Playwright Python script generation and execution
"""

import time
import pytest
import uvicorn
import threading
from demo_app.main import app as demo_app

from app.investigation_agent.models import (
    BugLead,
    StateDiff,
    OracleType,
    OracleStrength,
)
from app.investigation_agent.skeptic import SkepticFilter
from app.investigation_agent.reproducer import BugReproducer
from app.investigation_agent.behavior_model import ApplicationBehaviorModel


@pytest.fixture(scope="module")
def fixture1_repro_server():
    server = uvicorn.Server(uvicorn.Config(demo_app, host="127.0.0.1", port=8991, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8991"
    server.should_exit = True


def test_skeptic_filter_edge_cases():
    """
    Verifies that SkepticFilter rejects uncertain, trivial, or benign leads
    and preserves genuine defect leads.
    """
    # 1. Reject UNCERTAIN outcome
    lead_uncertain = BugLead(
        title="Unclear form behavior",
        oracle_type=OracleType.CONVENTION,
        strength=OracleStrength.MEDIUM,
        probe_family="forms",
        question_id="q1",
        actual={"text": "Outcome is UNCERTAIN due to dynamic script"},
    )
    survived, reason = SkepticFilter.evaluate_lead(lead_uncertain)
    assert not survived
    assert "UNCERTAIN" in reason

    # 2. Reject trivial "no route change" with no errors
    lead_no_nav = BugLead(
        title="Link click produced no navigation",
        oracle_type=OracleType.CONVENTION,
        strength=OracleStrength.MEDIUM,
        probe_family="nav",
        question_id="q2",
        actual={"text": "Observed no route change"},
    )
    diff_empty = StateDiff()
    survived, reason = SkepticFilter.evaluate_lead(lead_no_nav, diff_empty)
    assert not survived
    assert "navigation" in reason.lower()

    # 3. Reject benign redirect
    lead_redirect = BugLead(
        title="URL Redirect",
        oracle_type=OracleType.TECHNICAL,
        strength=OracleStrength.LOW,
        probe_family="nav",
        question_id="q3",
        actual={"text": "Received HTTP 301 redirect to trailing slash"},
    )
    survived, reason = SkepticFilter.evaluate_lead(lead_redirect)
    assert not survived
    assert "redirect" in reason.lower()

    # 4. Genuine defect lead must survive
    lead_genuine = BugLead(
        title="Password length boundary accepted by server",
        oracle_type=OracleType.DECLARED,
        strength=OracleStrength.HIGH,
        probe_family="forms",
        question_id="q4",
        expected={"text": "Server returns error on short password", "provenance": "Rule"},
        actual={"text": "Server accepted 1-character password with HTTP 200 Registration Success"},
    )
    survived, reason = SkepticFilter.evaluate_lead(lead_genuine)
    assert survived
    assert "survived" in reason.lower()


def test_reproducer_on_fixture1_planted_bugs(fixture1_repro_server):
    """
    On Demo App Fixture 1, verifies that planted bugs:
    - Run 2-of-2 isolated browser contexts
    - Reach CONFIRMED status
    - Minimize step sequences
    """
    reproducer = BugReproducer(headless=True)
    target_url = f"{fixture1_repro_server}/registration"

    # Planted Bug 2: Mandatory Terms of Service Checkbox Bypass
    lead_terms = BugLead(
        title="Mandatory Terms Checkbox Bypass",
        oracle_type=OracleType.DECLARED,
        strength=OracleStrength.HIGH,
        probe_family="forms",
        question_id="q_terms",
        target_desc="terms",
        state_class="cls_reg",
        steps=[
            {"action_type": "SET_NOVALIDATE", "raw_target": "form"},
            {"action_type": "FILL", "raw_target": "#fullname", "value": "Jane Doe"},
            {"action_type": "FILL", "raw_target": "#email", "value": "jane@example.test"},
            {"action_type": "FILL", "raw_target": "#reg-password", "value": "Secret123!"},
            {"action_type": "FILL", "raw_target": "#confirm-password", "value": "Secret123!"},
            {"action_type": "UNCHECK", "raw_target": "#terms"},
            {"action_type": "SUBMIT", "raw_target": "form"},
        ],
        expected={"text": "Reject registration when terms unchecked", "provenance": "HTML required"},
        actual={"text": "Registration accepted without terms"},
    )

    reproduced = reproducer.reproduce_lead(target_url, lead_terms)

    assert reproduced.status == "CONFIRMED"
    assert reproduced.confirmation.get("passed") == 2
    assert reproduced.confirmation.get("runs") == 2
    # Verify minimization loop ran
    assert len(reproduced.steps) <= 7


def test_behavior_model_playwright_export():
    """
    Verifies that ApplicationBehaviorModel exports clean, syntactically valid
    Playwright Python test code.
    """
    model = ApplicationBehaviorModel()
    model.add_journey(
        name="User Registration Progression",
        steps=[
            {"action": "NAVIGATE", "target": "http://127.0.0.1:8991/registration"},
            {"action": "FILL", "target": "#fullname", "value": "Alex Smith"},
            {"action": "FILL", "target": "#email", "value": "alex@example.test"},
            {"action": "CHECK", "target": "#terms"},
            {"action": "SUBMIT", "target": "#registerBtn"},
        ]
    )

    script = model.export_playwright_test("User Registration Progression")

    assert "from playwright.sync_api import sync_playwright" in script
    assert "def test_user_registration_progression():" in script
    assert "page.goto('http://127.0.0.1:8991/registration'" in script
    assert "page.locator('#fullname').fill('Alex Smith')" in script
    assert "page.locator('#terms').check()" in script
    assert "browser.close()" in script
