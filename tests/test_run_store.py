"""
Unit tests for the persistent RunStore: lifecycle, retrieval, path safety, and secret redaction.
"""

import json
import os
import re
from pathlib import Path

import pytest

from app.runs import (
    REPORT_OUTCOMES,
    ReportOutcome,
    RunStore,
    RunStatus,
    InvalidRunId,
    RunNotFound,
    redact_secrets,
    report_outcome_for,
)
from app.runs.store import RUN_FILE, REPORT_FILE, TEST_CASES_FILE
from app.investigation_agent.models import InvestigationStatus
from app.investigation_agent.workflow import InvestigationOrchestrator

SECRET = "Pw-Store-Secret-7731"


@pytest.fixture
def store(tmp_path):
    return RunStore(str(tmp_path / "runs"))


def test_create_persists_pending_run(store):
    run = store.create("http://127.0.0.1:8001/", "Demo portal", "Req A\n\nReq B ")

    assert run.status == RunStatus.PENDING
    assert run.run_id.startswith("inv-")
    on_disk = json.loads((store.root / run.run_id / RUN_FILE).read_text(encoding="utf-8"))
    assert on_disk["target_url"] == "http://127.0.0.1:8001/"
    assert on_disk["description"] == "Demo portal"
    assert on_disk["requirements"] == ["Req A", "Req B"]
    assert on_disk["status"] == "pending"
    assert on_disk["created_at"] and on_disk["started_at"] is None


def test_complete_persists_report_and_exact_test_cases(store):
    run = store.create("http://127.0.0.1:8001/")
    store.mark_running(run.run_id)
    report = {"overview": {"stopping_reason": "Budget"}, "proposed_test_cases": [{"test_id": "TC-1"}]}
    cases = [{"test_id": "TC-1", "name": "first"}, {"test_id": "TC-2", "name": "second"}]

    record = store.complete(run.run_id, report, cases, stopping_reason="Budget")

    assert record.status == RunStatus.COMPLETED
    assert record.started_at and record.finished_at
    assert record.test_case_count == 2
    detail = store.get(run.run_id)
    assert detail.run.status == RunStatus.COMPLETED
    assert detail.report == report
    assert detail.test_cases == cases


@pytest.mark.parametrize("report_status, outcome", [
    ("completed", ReportOutcome.SUCCEEDED),
    ("budget_exhausted", ReportOutcome.LIMITED),
    ("blocked", ReportOutcome.UNSUCCESSFUL),
    ("failed", ReportOutcome.UNSUCCESSFUL),
    ("something_new", ReportOutcome.UNKNOWN),
    (None, ReportOutcome.UNKNOWN),
])
def test_complete_records_report_status_separately_from_lifecycle(store, report_status, outcome):
    run = store.create("http://127.0.0.1:8099/")
    store.complete(run.run_id, {"overview": {"status": report_status}}, [], report_status=report_status)

    record = store.get_record(run.run_id)
    assert record.status == RunStatus.COMPLETED
    assert record.report_status == report_status
    assert record.report_outcome == outcome
    on_disk = json.loads((store.root / run.run_id / RUN_FILE).read_text(encoding="utf-8"))
    assert on_disk["report_outcome"] == outcome.value


def test_every_investigation_status_has_an_outcome():
    assert {s.value for s in InvestigationStatus} == set(REPORT_OUTCOMES)
    assert report_outcome_for("BLOCKED") == ReportOutcome.UNSUCCESSFUL


def test_frontend_outcome_mapping_matches_backend():
    """app/static/src/runDisplay.mjs mirrors REPORT_OUTCOMES; the two must not drift apart."""
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "src" / "runDisplay.mjs").read_text(encoding="utf-8")
    block = re.search(r"export const REPORT_OUTCOMES = \{(.*?)\};", source, re.S).group(1)
    frontend = dict(re.findall(r"(\w+):\s*'(\w+)'", block))
    assert frontend == {status: outcome.value for status, outcome in REPORT_OUTCOMES.items()}


def test_failed_run_remains_inspectable(store):
    run = store.create("http://127.0.0.1:8001/")
    store.mark_running(run.run_id)
    store.fail(run.run_id, "RuntimeError: browser crashed")

    detail = store.get(run.run_id)
    assert detail.run.status == RunStatus.FAILED
    assert detail.run.error == "RuntimeError: browser crashed"
    assert detail.run.finished_at
    assert detail.report is None and detail.test_cases == []


def test_list_runs_newest_first(store):
    first = store.create("http://127.0.0.1:8001/a")
    second = store.create("http://127.0.0.1:8001/b")
    store.complete(second.run_id, {}, [])

    listed = store.list_runs()
    assert [r.run_id for r in listed] == [second.run_id, first.run_id]
    assert listed[0].status == RunStatus.COMPLETED


def test_unknown_run_raises_not_found(store):
    with pytest.raises(RunNotFound):
        store.get("inv-0123456789ab")


@pytest.mark.parametrize("bad_id", [
    "../outside",
    "..\\outside",
    "inv-../../etc",
    "inv-0123456789ab/../../x",
    "/etc/passwd",
    "C:\\Windows",
    "inv-ABCDEF123456",
    "",
    None,
])
def test_run_ids_cannot_escape_storage_dir(store, tmp_path, bad_id):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / RUN_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(InvalidRunId):
        store.get(bad_id)


def test_writes_are_atomic_and_leave_no_temp_files(store):
    run = store.create("http://127.0.0.1:8001/")
    store.complete(run.run_id, {"k": "v"}, [{"test_id": "TC-1"}])
    names = sorted(os.listdir(store.root / run.run_id))
    assert names == sorted([RUN_FILE, REPORT_FILE, TEST_CASES_FILE])


def test_recover_interrupted_runs_marks_them_failed(store):
    running = store.create("http://127.0.0.1:8001/")
    store.mark_running(running.run_id)
    done = store.create("http://127.0.0.1:8001/")
    store.complete(done.run_id, {}, [])

    assert store.recover_interrupted_runs() == 1
    assert store.get_record(running.run_id).status == RunStatus.FAILED
    assert store.get_record(done.run_id).status == RunStatus.COMPLETED


def test_persisted_files_never_contain_known_secrets(store):
    run = store.create("http://127.0.0.1:8001/", f"login with {SECRET}", [f"use {SECRET}"], secrets={SECRET})
    report = {
        "inferred_journeys": [{"steps": [{"action": "FILL", "target": "#password", "value": SECRET}]}],
        "auth_credentials": {"username": "qa_user", "password": SECRET},
        "headers": {"Authorization": "Bearer abc.def.ghi"},
        "password_masking_verified": True,
    }
    cases = [{"test_id": "TC-1", "steps": [{"action": "FILL", "value": SECRET}]}]
    store.complete(run.run_id, report, cases, secrets={SECRET})

    for name in (RUN_FILE, REPORT_FILE, TEST_CASES_FILE):
        text = (store.root / run.run_id / name).read_text(encoding="utf-8")
        assert SECRET not in text
        assert "abc.def.ghi" not in text
    detail = store.get(run.run_id)
    assert detail.report["auth_credentials"]["username"] == "qa_user"
    assert detail.report["password_masking_verified"] is True


def test_redact_secrets_keeps_structure():
    data = {"token_count": 3, "api_key": "k-123456", "nested": [{"value": "x-SECRET-x"}]}
    out = redact_secrets(data, {"SECRET"})
    assert out["token_count"] == 3
    assert out["api_key"] == "[REDACTED_SECRET]"
    assert out["nested"][0]["value"] == "x-[REDACTED_SECRET]-x"


def test_checkpoint_never_writes_password(tmp_path):
    orchestrator = InvestigationOrchestrator(headless=True)
    path = orchestrator.save_session_checkpoint(
        {
            "investigation_id": "inv-0123456789ab",
            "target_url": "http://127.0.0.1:8001/",
            "auth_credentials": {"username": "qa_user", "password": SECRET, "source": "ENVIRONMENT"},
        },
        filepath=str(tmp_path / "checkpoint.json"),
    )
    text = open(path, encoding="utf-8").read()
    assert SECRET not in text
    assert json.loads(text)["auth_credentials"] == {"username": "qa_user", "source": "ENVIRONMENT"}
