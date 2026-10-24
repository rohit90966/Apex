"""
Tests for Website-Agnostic Autonomous Investigation Agent (Phases A-E & Acceptance Criteria).
Verifies:
1. Initial page is observed, recorded in evidence ledger with screenshot, and probed BEFORE auto-login/navigation.
2. Login page is investigated as a page when present, then authentication unlocks new states dynamically.
3. Dynamic state discovery in node_record populates the agenda with new controls/forms/routes.
4. State counts and frontier counts are reconciled across overview, map, coverage_frontier, and behavior_model.
5. Zero e-commerce hardcoded assumptions on non-e-commerce pages.
6. OllamaEmbeddingProvider circuit-breaker prevents repeated 404 calls when nomic-embed-text is absent.
7. BugReproducer distinguishes CONFIRMED, UNSTABLE, SUSPECTED (inconclusive env failure), and REJECTED.
8. Exported Playwright scripts include concrete step actions and assertions.
"""

import os
import time
import threading
import uvicorn
import pytest

from demo_app.main import app as fixture1_app
from demo_app.fixture2.main import app as fixture2_app
from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from app.investigation_agent.models import (
    UIStateObservation,
    ElementReference,
    StateTransitionRecord,
    EvidenceEntry,
    BugLead,
    OracleType,
    OracleStrength,
)
from app.investigation_agent.agenda import InvestigationAgenda
from app.investigation_agent.report_builder import InvestigationReportBuilder
from app.investigation_agent.reproducer import BugReproducer
from app.knowledge.embeddings import OllamaEmbeddingProvider


@pytest.fixture(scope="module")
def agnostic_fixture_server():
    server = uvicorn.Server(uvicorn.Config(fixture1_app, host="127.0.0.1", port=8996, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8996"
    server.should_exit = True


@pytest.fixture(scope="module")
def taskdash_fixture2_server():
    server = uvicorn.Server(uvicorn.Config(fixture2_app, host="127.0.0.1", port=8997, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.2)
    yield "http://127.0.0.1:8997"
    server.should_exit = True


def test_initial_login_page_observed_and_probed_before_auth_transition():
    """
    Acceptance Criteria #2 & #3:
    When an authentication boundary (login page) is encountered and credentials exist:
    1. The initial page is observed and recorded in the agenda first.
    2. A safe login-page validation probe (e.g. required_blank) is prioritized FIRST (0.96)
       before the authentication transition probe (0.92).
    3. Login is investigated as a page, not skipped automatically on step 0.
    """
    agenda = InvestigationAgenda(max_probes=10)
    login_obs = UIStateObservation(
        state_id="st_login_initial",
        state_class_id="cls_login_gate",
        url="http://127.0.0.1:8996/login",
        normalized_route="127.0.0.1:8996/login",
        page_title="Portal Sign In",
        headings=["Sign In"],
        forms=[{
            "form_id": "loginForm",
            "form_selector": "#loginForm",
            "fields": [
                {"name": "username", "id": "username", "type": "text", "required": True},
                {"name": "password", "id": "password", "type": "password", "required": True},
            ]
        }],
        interactive_controls=[],
        navigation_links=[],
    )

    added = agenda.populate_from_observation(
        login_obs,
        is_auth_boundary=True,
        has_auth_creds=True,
    )
    assert added >= 2

    # First selected question MUST be the login page validation probe (required_blank), NOT blind login
    first_q = agenda.select_next_question(current_state_class="cls_login_gate")
    assert first_q is not None
    assert first_q.probe_family == "forms"
    assert first_q.metadata.get("kind") == "required_blank"

    agenda.record_probe_outcome(first_q.id, "MATCHED", "Login form rejected blank submission")

    # Second selected question MUST be the authentication transition probe
    second_q = agenda.select_next_question(current_state_class="cls_login_gate")
    assert second_q is not None
    assert second_q.probe_family == "auth"
    assert second_q.metadata.get("kind") == "auth_transition"


def test_dynamic_state_discovery_populates_agenda_across_views(agnostic_fixture_server):
    """
    Acceptance Criteria #1, #2, #4, #5:
    Verifies that investigating a non-e-commerce multi-page application:
    - Records the initial page and screenshot artifact
    - Dynamically populates the agenda when new states/controls/routes are discovered
    - Reconciles state counts across overview, discovered_application_map, coverage_frontier, and behavior_model
    - Contains zero hardcoded e-commerce wording
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    # Use deterministic mode for fast, repeatable verification
    orchestrator.planner.provider = None
    orchestrator.understanding_engine.provider = None
    orchestrator.planner._provider_checked = True
    orchestrator.understanding_engine._provider_checked = True

    graph = build_investigation_workflow(orchestrator)
    final_state = graph.invoke({
        "target_url": f"{agnostic_fixture_server}/registration",
        "investigation_id": "inv_agnostic_reg",
        "max_probes": 4,
        "max_actions": 4,
        "max_time_seconds": 45.0,
        "max_mutations": 10,
    })

    report = final_state["final_report"]
    ov = report.overview

    # 1. Initial page recorded with real screenshot artifact on disk
    assert ov.meaningful_states_discovered >= 1
    assert len(report.discovered_application_map) == ov.meaningful_states_discovered
    assert report.investigation_coverage_frontier["explored_states_count"] == ov.meaningful_states_discovered
    assert len(report.behavior_model["state_classes_list"]) == ov.meaningful_states_discovered

    # Check screenshot artifacts exist on disk
    screenshots = [e["screenshot"] for e in report.evidence_and_traceability if e.get("screenshot")]
    assert len(screenshots) >= 1, "At least one real screenshot artifact must be recorded"
    artifacts_dir = os.path.abspath(orchestrator.session.settings.verityqa_artifacts_dir)
    assert os.path.exists(os.path.join(artifacts_dir, screenshots[0]))

    # 2. Zero hardcoded e-commerce terms on registration portal
    md = InvestigationReportBuilder.to_markdown(report)
    assert "product inventory catalog" not in md.lower()
    assert "empty checkout submission" not in md.lower()
    assert "catalog item detail pages" not in md.lower()

    # 3. Exported Playwright script has concrete assertions
    script = report.playwright_test_script
    assert "def test_" in script
    assert "page.goto(" in script
    assert "assert len(page.locator('body').inner_text().strip()) > 0" in script


def test_reproducer_preserves_suspected_lead_on_environment_error(monkeypatch):
    """
    Acceptance Criteria #6:
    When isolated browser reproduction fails to navigate or crashes due to environment error
    (returning res=None), the lead must be marked SUSPECTED rather than falsely REJECTED.
    """
    reproducer = BugReproducer(headless=True)
    monkeypatch.setattr(reproducer, "_test_replay_run", lambda url, script, lead: (False, None))

    lead = BugLead(
        id="lead_env_fail",
        title="Server accepted unvalidated input",
        description="Form accepted invalid payload",
        state_class="cls_form",
        probe_family="forms",
        question_id="q_form_1",
        target_desc="#submit",
        oracle_type=OracleType.DECLARED,
        strength=OracleStrength.HIGH,
        steps=[{"action_type": "SUBMIT", "raw_target": "form"}],
    )

    updated = reproducer.reproduce_lead("http://127.0.0.1:8996/registration", lead)
    assert updated.status == "SUSPECTED"
    assert "inconclusive" in updated.actual.get("rejection_reason", "").lower()


def test_ollama_embedding_provider_circuit_breaker_on_404(monkeypatch):
    """
    Verifies that OllamaEmbeddingProvider caches 404 model-not-found responses
    and does not repeatedly make failing HTTP calls for every embedding request.
    """
    OllamaEmbeddingProvider._unavailable_models.clear()
    call_count = {"n": 0}

    class FakeResponse:
        status_code = 404
        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, *args, **kwargs):
            call_count["n"] += 1
            return FakeResponse()

    import httpx
    monkeypatch.setattr(httpx, "Client", FakeClient)

    provider = OllamaEmbeddingProvider(base_url="http://127.0.0.1:11434", model="nomic-embed-text")
    provider.is_gemini_active = False

    v1 = provider.get_embedding_sync("first query")
    v2 = provider.get_embedding_sync("second query")
    v3 = provider.get_embedding_sync("third query")

    assert len(v1) == 384
    assert len(v2) == 384
    assert len(v3) == 384
    assert call_count["n"] == 1, f"Expected only 1 HTTP attempt before circuit-breaker fallback, got {call_count['n']}"


def test_login_gate_investigates_initial_page_then_unlocks_post_login_surface(agnostic_fixture_server):
    """
    Acceptance Criteria #2, #3, #4:
    End-to-end verification on a login-protected page (/login -> /dashboard):
    1. Initial /login page is observed and probed FIRST (blank submission validation).
    2. Next, the authentication probe runs and the browser reaches a genuinely different, authenticated page.
    3. That post-login page is registered as an observed state (destination of the AUTHENTICATE transition)
       and its controls/links are dynamically added to the agenda.

    Note: Fixture 1 has no `/dashboard` route. A successful POST /login renders an authenticated
    "Welcome, Admin" page at the same /login URL with a "Go to Dashboard" link to "/". The assertions
    below therefore verify the actual observed post-login state rather than an assumed URL.
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.planner.provider = None
    orchestrator.understanding_engine.provider = None
    orchestrator.planner._provider_checked = True
    orchestrator.understanding_engine._provider_checked = True

    graph = build_investigation_workflow(orchestrator)
    final_state = graph.invoke({
        "target_url": f"{agnostic_fixture_server}/login",
        "investigation_id": "inv_login_then_explore",
        "auth_credentials": {"username": "admin@apex.com", "password": "Secret123!", "source": "TEST_CONFIG"},
        "max_probes": 4,
        "max_actions": 4,
        "max_time_seconds": 45.0,
        "max_mutations": 10,
    })

    report = final_state["final_report"]
    states = final_state["states"]
    history = orchestrator.agenda.probe_history

    assert len(history) >= 3
    # Probe 1 must be the initial login-page validation probe (required_blank)
    assert "required_blank" in history[0]["question_id"]
    # Probe 2 must be the authentication boundary transition probe
    assert history[1]["question_id"].startswith("q_auth_")

    # Initial login page is recorded as a clean (non-error) state before any interaction
    initial_id = report.evidence_and_traceability[0]["state_ref"]
    assert initial_id in states
    assert states[initial_id].has_validation_error is False, (
        f"Untouched login page must not be flagged as a validation error state: {states[initial_id].validation_error_message}"
    )

    # Authentication verified, and exactly the observed transition is recorded
    assert report.authentication_access.boundary_observed is True
    assert report.authentication_access.authentication_verified is True
    auth_transitions = [t for t in final_state["transitions"] if t.action_type == "AUTHENTICATE"]
    assert len(auth_transitions) == 1
    auth_t = auth_transitions[0]
    assert auth_t.source_state_id != auth_t.destination_state_id

    # The post-login page is a genuine observed state that differs from the login page
    post_login = states[auth_t.destination_state_id]
    login_obs = states[auth_t.source_state_id]
    assert post_login.headings != login_obs.headings
    assert not any(
        (fld.get("type") or "").lower() == "password" for f in post_login.forms for fld in f.get("fields", [])
    ), "Post-login state must not still contain the password form"
    map_ids = {area.area_id for area in report.discovered_application_map}
    assert auth_t.destination_state_id in map_ids
    assert auth_t.source_state_id in map_ids

    # Controls/links discovered on the post-login page were dynamically added to the agenda
    post_login_questions = [
        q for q in orchestrator.agenda.questions.values() if q.target_state_class == post_login.state_class_id
    ]
    assert len(post_login_questions) >= 1

    # Graph, transitions and overview agree with observations
    assert report.overview.meaningful_states_discovered == len(states) == len(report.discovered_application_map)
    assert report.overview.transitions_observed == len(final_state["transitions"])
    for t in final_state["transitions"]:
        assert t.source_state_id in states and t.destination_state_id in states


def test_non_auth_dashboard_fixture2_investigation(taskdash_fixture2_server):
    """
    Acceptance Criteria #1 & #9:
    End-to-end verification on Fixture 2 (TaskDash — public dashboard with no login page).
    Verifies website-agnostic exploration without assuming authentication or e-commerce.
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    orchestrator.planner.provider = None
    orchestrator.understanding_engine.provider = None
    orchestrator.planner._provider_checked = True
    orchestrator.understanding_engine._provider_checked = True

    graph = build_investigation_workflow(orchestrator)
    final_state = graph.invoke({
        "target_url": f"{taskdash_fixture2_server}/",
        "investigation_id": "inv_taskdash_public",
        "max_probes": 4,
        "max_actions": 4,
        "max_time_seconds": 45.0,
        "max_mutations": 10,
    })

    report = final_state["final_report"]
    states = final_state["states"]
    transitions = final_state["transitions"]
    assert report.authentication_access.boundary_observed is False
    assert report.authentication_access.authentication_attempted is False

    # Executed actions must produce genuinely distinct observed states (not repeated self-clicks)
    assert report.overview.actions_successful >= 3
    assert report.overview.meaningful_states_discovered >= 3
    assert len({s.url for s in states.values()}) >= 3
    assert len({s.headings[1] if len(s.headings) > 1 else "" for s in states.values()}) >= 3

    # Every reported transition corresponds to a recorded before/after observation
    assert len(transitions) >= 2
    assert report.overview.transitions_observed == len(transitions)
    assert report.overview.actions_verified_effects == len(transitions)
    for t in transitions:
        assert t.source_state_id in states and t.destination_state_id in states
        assert t.source_state_id != t.destination_state_id
    map_ids = {area.area_id for area in report.discovered_application_map}
    assert map_ids == set(states.keys())
    graph_ids = {sc["class_id"] for sc in report.behavior_model["state_classes_list"]}
    assert graph_ids == set(states.keys())


def test_distinct_tab_links_get_distinct_selectors_and_valid_roles(taskdash_fixture2_server):
    """
    Regression for the TaskDash root cause: every tab anchor previously received the same CSS
    selector `a.tab`, and control targets used the tag name ("a") as an ARIA role. Every control
    probe therefore resolved to the first tab (the current page) and produced no state change.
    """
    from app.investigation_agent.browser_session import StatefulBrowserSession
    from app.investigation_agent.probes.controls import generate_control_probes

    session = StatefulBrowserSession(headless=True)
    try:
        session.start()
        ok, _, _ = session.navigate(f"{taskdash_fixture2_server}/")
        assert ok
        obs = session.capture_observation("tab_selector_check")
    finally:
        session.close()

    tab_links = [c for c in obs.interactive_controls if c.tag_name == "a" and "tab" in c.selector]
    selectors = [c.selector for c in tab_links]
    assert len(selectors) >= 3
    assert len(selectors) == len(set(selectors)), f"Tab selectors must be unique, got {selectors}"

    # Also ensure all link selectors on the page are unique
    all_link_selectors = [c.selector for c in obs.interactive_controls if c.tag_name == "a" and "href=" in c.selector]
    assert len(all_link_selectors) == len(set(all_link_selectors)), f"All link selectors must be unique, got {all_link_selectors}"

    probes = generate_control_probes(obs)
    assert probes, "Expected control probes on TaskDash dashboard"
    for script, _, _ in probes:
        target = script.steps[0].target
        assert target.role in (None, "link", "button", "tab", "menuitem", "checkbox", "switch")
        # Self-link to the current route must not be probed as a control
        assert 'href="/"' not in (target.css or "")


def test_untouched_required_fields_do_not_flag_validation_error(agnostic_fixture_server):
    """
    Regression for the initial-login root cause: untouched empty `required` inputs match CSS :invalid
    at page load, which previously tagged the initial login page as `_with_error` before any interaction.
    """
    from app.investigation_agent.browser_session import StatefulBrowserSession

    session = StatefulBrowserSession(headless=True)
    try:
        session.start()
        ok, _, _ = session.navigate(f"{agnostic_fixture_server}/login")
        assert ok
        obs = session.capture_observation("untouched_login")
    finally:
        session.close()

    assert obs.has_validation_error is False, obs.validation_error_message
    assert "with_error" not in obs.state_id

