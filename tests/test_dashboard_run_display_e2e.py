"""
Browser regression tests for how the dashboard (built app.js served by the real FastAPI app) presents runs:
- no invented execution statistics or SauceDemo-specific content for an unrelated target,
- a blocked report is shown as unsuccessful, never as a completed success,
- execution metrics appear only after a real execution response,
- starting a new run immediately clears the previous run's report and test cases.
The run store is a temp directory and the investigation engine is a stub; no external site or LLM is used.
"""

import asyncio
import re
import socket
import threading
import time

import pytest
import uvicorn

import app.main as main
from app.runs import RunStore
from app.schemas import (
    ExecutionResult,
    ExecutionStatus,
    TestCase,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
)
from app.investigation_agent.models import (
    InvestigationReport,
    ReportOverview,
    InvestigationStatus,
    ReportAuthentication,
)

UNRELATED_TARGET = "http://127.0.0.1:8001/"
FABRICATED = ["26/27", "96.3", "100% Assertion", "100% Grounded", "2/2 Repro", "Swag Labs", "Session Preservation",
              "/inventory.html", "inventory & cart", "Full Post-Auth Funnel", "47s"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _report(status: InvestigationStatus, reason: str) -> InvestigationReport:
    return InvestigationReport(
        overview=ReportOverview(
            target_url=UNRELATED_TARGET,
            investigation_id="placeholder",
            start_time="2026-10-24T12:00:00Z",
            end_time="2026-10-24T12:00:05Z",
            status=status,
            pages_discovered=1,
            meaningful_states_discovered=1,
            stopping_reason=reason,
        ),
        application_understanding="",
        user_provided_context_distinction="Distinct",
        authentication_access=ReportAuthentication(
            boundary_observed=False,
            credentials_available=False,
            authentication_attempted=False,
            authentication_verified=False,
        ),
    )


def _case(test_id: str) -> TestCase:
    return TestCase(
        test_id=test_id, name=f"Case {test_id}", objective="Verify page", page_category=PageCategory.LOGIN,
        action_description="Open page", expected_outcome="Page renders",
        expected_behavior_source=ExpectedBehaviorSource.OBSERVED_INVARIANT, priority=Priority.MEDIUM,
        selection_reason="Observed", confidence=0.5,
    )


def _persist(store: RunStore, status: InvestigationStatus, reason: str, case_ids) -> str:
    report = _report(status, reason)
    run = store.create(UNRELATED_TARGET)
    report.overview.investigation_id = run.run_id
    payload = report.model_dump(mode="json")
    payload["proposed_test_cases"] = [_case(c).model_dump(mode="json") for c in case_ids]
    store.complete(run.run_id, payload, payload["proposed_test_cases"], stopping_reason=reason,
                   report_status=status.value)
    return run.run_id


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    store = RunStore(str(tmp_path / "runs"))
    monkeypatch.setattr(main, "run_store", store)
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=_free_port(), log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "dashboard server did not start"
    yield f"http://127.0.0.1:{server.config.port}/", store
    server.should_exit = True
    thread.join(timeout=10)


def _open_tab(page, index: int) -> str:
    page.locator("button.tab-btn").nth(index).click()
    page.wait_for_timeout(300)
    return page.locator("main").inner_text()


def _load(page, base: str, run_id: str):
    with page.expect_response(lambda r: r.url.endswith(f"/api/runs/{run_id}")):
        page.goto(base)
    page.wait_for_timeout(300)


def test_blocked_run_shows_no_fabricated_content(dashboard, page):
    base, store = dashboard
    run_id = _persist(store, InvestigationStatus.BLOCKED, "All candidate actions (1) blocked by safety policy", ["TC-INV-RULE-01"])
    _load(page, base, run_id)

    overview = _open_tab(page, 0)
    badge = page.locator(".view-header .badge").first
    assert "STATUS: BLOCKED" in badge.inner_text()
    assert "badge-rose" in badge.get_attribute("class")
    assert "No execution data yet" in overview
    assert "Execution Pass Rate" in overview

    results = _open_tab(page, 2)
    assert "BLOCKED" in results
    assert "COMPLETED" not in results
    assert "blocked by safety policy" in results
    assert "NOT DETECTED" in results

    page.get_by_role("button", name="Coverage & Limitations").click()
    page.wait_for_timeout(300)
    coverage = page.locator("main").inner_text()
    assert "No authentication boundary observed" in coverage

    for text in (overview, results, coverage):
        for fabricated in FABRICATED:
            assert fabricated not in text, f"fabricated content shown: {fabricated!r}"


def test_execution_metrics_appear_only_after_a_real_execution(dashboard, page, monkeypatch):
    base, store = dashboard
    run_id = _persist(store, InvestigationStatus.COMPLETED, "Investigation converged", ["TC-INV-RULE-01", "TC-INV-RULE-02"])

    async def fake_run_all(test_cases, target_url, max_concurrent=1):
        return [
            ExecutionResult(test_id=tc.test_id, test_name=tc.name, total_steps=1, steps_completed=1,
                            status=ExecutionStatus.PASSED if i == 0 else ExecutionStatus.FAILED)
            for i, tc in enumerate(test_cases)
        ]

    async def fake_investigate_failure(**kwargs):
        raise RuntimeError("bug verification not exercised in this test")

    monkeypatch.setattr(main.test_runner, "run_all", fake_run_all)
    monkeypatch.setattr(main.bug_investigator, "investigate_failure", fake_investigate_failure)
    _load(page, base, run_id)
    assert "No execution data yet" in _open_tab(page, 0)

    _open_tab(page, 3)
    with page.expect_response(lambda r: r.url.endswith("/api/tests/execute")) as resp:
        page.get_by_role("button", name=re.compile("Execute", re.I)).first.click()
    assert resp.value.status == 200
    page.wait_for_timeout(500)

    overview = _open_tab(page, 0)
    assert "1/2 Pass" in overview
    assert "50.0%" in overview
    assert "No execution data yet" not in overview


def test_new_run_clears_previous_results_immediately(dashboard, page, monkeypatch):
    base, store = dashboard
    old_run = _persist(store, InvestigationStatus.COMPLETED, "Investigation converged", ["TC-OLD-01"])

    async def slow_engine(**kwargs):
        kwargs["event_callback"]({"type": "PROBE_START", "probe_id": "p1", "name": "probe"})
        await asyncio.sleep(4)
        report = _report(InvestigationStatus.COMPLETED, "Investigation converged")
        report.overview.investigation_id = kwargs["investigation_id"]
        return report

    monkeypatch.setattr("app.investigation_agent.run_stateful_investigation", slow_engine)
    monkeypatch.setattr(
        "app.investigation_agent.test_synthesis.synthesize_test_cases_from_investigation",
        lambda report, **kw: [_case("TC-NEW-01")],
    )
    _load(page, base, old_run)
    assert "TC-OLD-01" in _open_tab(page, 3)

    _open_tab(page, 1)
    page.fill("#targetUrlInput", UNRELATED_TARGET)
    page.click("button[type=submit]")
    page.wait_for_selector(".stream-card", timeout=10_000)

    assert "No Active Investigation Loaded" in _open_tab(page, 2)
    assert "TC-OLD-01" not in _open_tab(page, 3)
    overview = _open_tab(page, 0)
    assert "STATUS: —" in overview
    assert "No execution data yet" in overview

    page.wait_for_function("() => document.querySelector('button.tab-btn.active')?.innerText.includes('Investigation Results')",
                           timeout=30_000)
    tests_tab = _open_tab(page, 3)
    assert "TC-NEW-01" in tests_tab
    assert "TC-OLD-01" not in tests_tab
