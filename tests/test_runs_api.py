"""
API tests for persisted investigation runs:
- SSE stream creates a run, stamps every event with its run_id, and persists completion/failure.
- GET /api/runs and GET /api/runs/{run_id} return the canonical run, report, and test cases.
- Description and requirements reach the investigation engine and the run record.
- Nothing persisted or streamed contains known test credentials or authorization secrets.
The investigation engine and test synthesis are mocked; no browser, LLM, or external site is used.
"""

import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import app
from app.runs import RunStore
from app.schemas import TestCase, PageCategory, Priority, ExpectedBehaviorSource
from app.investigation_agent.authenticator import AuthenticationManager
from app.investigation_agent.models import (
    InvestigationReport,
    ReportOverview,
    InvestigationStatus,
    ReportAuthentication,
)

TARGET = "http://127.0.0.1:8001/"
SECRET = "Pw-Run-Secret-5512"
BEARER = "Bearer eyJhbGciOi.payload.signature"

client = TestClient(app)


def _report(password: str) -> InvestigationReport:
    return InvestigationReport(
        overview=ReportOverview(
            target_url=TARGET,
            investigation_id="placeholder",
            start_time="2026-10-24T12:00:00Z",
            end_time="2026-10-24T12:01:00Z",
            status=InvestigationStatus.COMPLETED,
            pages_discovered=2,
            meaningful_states_discovered=3,
            stopping_reason="Investigation converged",
        ),
        application_understanding=f"Login page; request carried Authorization: {BEARER}",
        user_provided_context_distinction="Distinct",
        authentication_access=ReportAuthentication(
            boundary_observed=True,
            credentials_available=True,
            authentication_attempted=True,
            authentication_verified=True,
        ),
        inferred_journeys=[{
            "name": "Login journey",
            "steps": [
                {"action": "FILL", "target": "#username", "value": "qa_user"},
                {"action": "FILL", "target": "#password", "value": password},
            ],
        }],
        playwright_test_script=f'page.fill("#password", "{password}")',
    )


def _cases(password: str):
    common = dict(
        page_category=PageCategory.LOGIN,
        action_description="Fill and submit login",
        expected_outcome="Dashboard visible",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT,
        priority=Priority.HIGH,
        selection_reason="Observed login journey",
        confidence=0.9,
    )
    return [
        TestCase(test_id="TC-INV-JOURNEY-01", name="Login journey", objective="Log in",
                 steps=[{"step_number": 1, "action": "FILL", "selector": "#password", "value": password,
                         "description": "Fill password"}], **common),
        TestCase(test_id="TC-INV-RULE-01", name="Rule check", objective="Verify rule", **common),
    ]


class FakeEngine:
    """Stands in for run_stateful_investigation; records its kwargs and emits real event types."""

    def __init__(self, password=SECRET, fail_with=None, report_status=InvestigationStatus.COMPLETED):
        self.password = password
        self.fail_with = fail_with
        self.report_status = report_status
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        emit = kwargs.get("event_callback") or (lambda evt: None)
        emit({"type": "PROBE_START", "probe_id": "p1", "description": f"fill password {self.password}"})
        emit({"type": "PROBE_RESULT", "probe_id": "p1", "verdict": "MATCHED", "summary": "ok"})
        emit({"type": "LEAD_DETECTED", "lead_id": "lead_1", "title": "Bypass", "severity": "MEDIUM"})
        emit({"type": "REPRO_RESULT", "confirmed_count": 1})
        emit({"type": "STOPPING", "reason": "Investigation converged"})
        if self.fail_with:
            raise self.fail_with
        report = _report(self.password)
        report.overview.investigation_id = kwargs.get("investigation_id")
        report.overview.status = self.report_status
        return report


@pytest.fixture
def run_store(tmp_path, monkeypatch):
    store = RunStore(str(tmp_path / "runs"))
    monkeypatch.setattr(main, "run_store", store)
    monkeypatch.setenv("VERITYQA_TEST_USERNAME", "qa_user")
    monkeypatch.setenv("VERITYQA_TEST_PASSWORD", SECRET)
    return store


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr("app.investigation_agent.run_stateful_investigation", fake)
    monkeypatch.setattr(
        "app.investigation_agent.test_synthesis.synthesize_test_cases_from_investigation",
        lambda report, **kw: _cases(fake.password),
    )
    return fake


def _stream(query: str):
    """Runs the SSE endpoint to completion and returns the parsed events."""
    events = []
    with client.stream("GET", f"/api/investigate/stream?{query}") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def _query(**params):
    return urllib.parse.urlencode(params)


def _all_persisted_text(store: RunStore, run_id: str) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted((store.root / run_id).iterdir()))


def test_stream_events_carry_run_id_and_completion_is_persisted(run_store, engine):
    events = _stream(_query(url=TARGET))

    types = [e["type"] for e in events]
    assert types == ["RUN_CREATED", "PROBE_START", "PROBE_RESULT", "LEAD_DETECTED", "REPRO_RESULT", "STOPPING", "COMPLETE"]
    run_id = events[0]["run_id"]
    assert all(e["run_id"] == run_id for e in events)
    assert engine.calls[0]["investigation_id"] == run_id

    complete = events[-1]
    assert complete["status"] == "completed"
    assert complete["report"]["run_id"] == run_id

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["run_id"] == run_id
    assert detail["run"]["status"] == "completed"
    assert detail["run"]["stopping_reason"] == "Investigation converged"
    assert detail["run"]["started_at"] and detail["run"]["finished_at"]
    assert detail["report"]["overview"]["investigation_id"] == run_id
    assert [tc["test_id"] for tc in detail["test_cases"]] == ["TC-INV-JOURNEY-01", "TC-INV-RULE-01"]
    assert detail["test_cases"] == detail["report"]["proposed_test_cases"]
    assert detail["run"]["test_case_count"] == 2
    assert detail["run"]["report_status"] == "completed"
    assert detail["run"]["report_outcome"] == "succeeded"


def test_blocked_report_is_not_presented_as_success(run_store, monkeypatch):
    fake = FakeEngine(report_status=InvestigationStatus.BLOCKED)
    monkeypatch.setattr("app.investigation_agent.run_stateful_investigation", fake)
    monkeypatch.setattr(
        "app.investigation_agent.test_synthesis.synthesize_test_cases_from_investigation",
        lambda report, **kw: [],
    )

    events = _stream(_query(url=TARGET))
    run_id = events[0]["run_id"]

    run = client.get(f"/api/runs/{run_id}").json()["run"]
    assert run["status"] == "completed"
    assert run["report_status"] == "blocked"
    assert run["report_outcome"] == "unsuccessful"
    listed = {r["run_id"]: r for r in client.get("/api/runs").json()["runs"]}
    assert listed[run_id]["report_outcome"] == "unsuccessful"


def _break(store, method_name, exc):
    def broken(*args, **kwargs):
        raise exc
    setattr(store, method_name, broken)


def test_post_investigate_storage_failure_on_start_is_recorded(run_store, engine):
    _break(run_store, "mark_running", OSError("disk full"))

    response = client.post("/api/investigate", json={"url": TARGET})

    assert response.status_code == 500
    assert "disk full" in response.json()["detail"]
    run = run_store.list_runs()[0]
    assert run.status == "failed"
    assert "disk full" in run.error
    assert engine.calls == []


def test_stream_storage_failure_on_completion_is_recorded_and_streamed(run_store, engine):
    _break(run_store, "complete", OSError("disk full"))

    events = _stream(_query(url=TARGET))

    assert events[-1]["type"] == "ERROR"
    assert "disk full" in events[-1]["error"]
    run = run_store.get_record(events[0]["run_id"])
    assert run.status == "failed"
    assert "disk full" in run.error


def test_storage_failure_is_still_surfaced_when_failure_cannot_be_recorded(run_store, engine):
    _break(run_store, "mark_running", OSError("disk full"))
    _break(run_store, "fail", OSError("disk still full"))

    response = client.post("/api/investigate", json={"url": TARGET})
    assert response.status_code == 500
    assert "disk full" in response.json()["detail"]

    events = _stream(_query(url=TARGET))
    assert events[-1]["type"] == "ERROR"
    assert "disk full" in events[-1]["error"]


def test_list_runs_includes_completed_run(run_store, engine):
    run_id = _stream(_query(url=TARGET))[0]["run_id"]

    body = client.get("/api/runs").json()
    assert set(body.keys()) == {"runs"}
    listed = {r["run_id"]: r for r in body["runs"]}
    assert listed[run_id]["status"] == "completed"
    assert listed[run_id]["target_url"] == TARGET
    assert listed[run_id]["created_at"] and listed[run_id]["finished_at"]


def test_run_survives_new_store_instance(run_store, engine):
    """A fresh store over the same directory (as after a backend restart) still returns the run."""
    run_id = _stream(_query(url=TARGET))[0]["run_id"]
    reopened = RunStore(str(run_store.root))
    assert reopened.get(run_id).run.status == "completed"
    assert len(reopened.get(run_id).test_cases) == 2


def test_failed_investigation_is_persisted_and_streamed(run_store, monkeypatch):
    fake = FakeEngine(fail_with=RuntimeError(f"browser crashed while typing {SECRET}"))
    monkeypatch.setattr("app.investigation_agent.run_stateful_investigation", fake)

    events = _stream(_query(url=TARGET))

    run_id = events[0]["run_id"]
    error = events[-1]
    assert error["type"] == "ERROR"
    assert error["run_id"] == run_id
    assert error["status"] == "failed"
    assert "browser crashed" in error["error"]
    assert SECRET not in error["error"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "failed"
    assert "browser crashed" in detail["run"]["error"]
    assert detail["report"] is None
    assert SECRET not in _all_persisted_text(run_store, run_id)


def test_description_and_requirements_reach_engine_and_run(run_store, engine):
    events = _stream(_query(url=TARGET, description="Demo portal", requirements="Must log in\nCart persists"))

    call = engine.calls[0]
    assert call["description"] == "Demo portal"
    assert call["requirements"] == ["Must log in", "Cart persists"]
    run = client.get(f"/api/runs/{events[0]['run_id']}").json()["run"]
    assert run["description"] == "Demo portal"
    assert run["requirements"] == ["Must log in", "Cart persists"]


def test_unknown_run_returns_404(run_store):
    response = client.get("/api/runs/inv-0123456789ab")
    assert response.status_code == 404


@pytest.mark.parametrize("bad_id", ["not-a-run", "..%2F..%2Fetc", "inv-ABCDEF123456", "%2E%2E"])
def test_invalid_or_traversal_run_ids_are_rejected(run_store, bad_id):
    response = client.get(f"/api/runs/{bad_id}")
    assert response.status_code in (400, 404)
    assert "report" not in response.text


def test_streamed_and_persisted_output_contains_no_secrets(run_store, engine):
    events = _stream(_query(url=TARGET))
    run_id = events[0]["run_id"]

    streamed = json.dumps(events)
    persisted = _all_persisted_text(run_store, run_id)
    served = client.get(f"/api/runs/{run_id}").text
    for text in (streamed, persisted, served):
        assert SECRET not in text
        assert "eyJhbGciOi.payload.signature" not in text


def test_demo_registry_password_is_redacted_without_env_override(run_store, engine, monkeypatch):
    monkeypatch.delenv("VERITYQA_TEST_USERNAME")
    monkeypatch.delenv("VERITYQA_TEST_PASSWORD")
    registry_password = AuthenticationManager.discover_credentials(TARGET, None)["password"]
    engine.password = registry_password

    events = _stream(_query(url=TARGET))
    run_id = events[0]["run_id"]

    assert registry_password not in json.dumps(events)
    assert registry_password not in _all_persisted_text(run_store, run_id)


def test_post_investigate_persists_run(run_store, engine):
    response = client.post("/api/investigate", json={"url": TARGET, "description": "d", "requirements": ["r1"]})

    assert response.status_code == 200
    body = response.json()
    run_id = body["run_id"]
    assert engine.calls[0]["investigation_id"] == run_id
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "completed"
    assert detail["test_cases"] == body["proposed_test_cases"]
    assert SECRET not in response.text
