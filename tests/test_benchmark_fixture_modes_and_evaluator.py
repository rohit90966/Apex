"""
Fixture fault-injection modes, ground-truth isolation and the independent evaluator.
"""

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def taskdash(monkeypatch):
    def _client(mode, disclose=False):
        monkeypatch.setenv("VERITYQA_FIXTURE_MODE", mode)
        if disclose:
            monkeypatch.setenv("VERITYQA_FIXTURE_DISCLOSE_BUGS", "1")
        else:
            monkeypatch.delenv("VERITYQA_FIXTURE_DISCLOSE_BUGS", raising=False)
        from demo_app.fixture2 import main
        return TestClient(main.app)
    return _client


@pytest.fixture
def apex(monkeypatch):
    def _client(mode, disclose=False):
        monkeypatch.setenv("VERITYQA_FIXTURE_MODE", mode)
        if disclose:
            monkeypatch.setenv("VERITYQA_FIXTURE_DISCLOSE_BUGS", "1")
        else:
            monkeypatch.delenv("VERITYQA_FIXTURE_DISCLOSE_BUGS", raising=False)
        from demo_app import main
        return TestClient(main.app)
    return _client


def test_taskdash_baseline_and_buggy_modes_differ_only_in_planted_behaviour(taskdash):
    # The mode is read from the environment per request, so each block runs under its own mode.
    buggy = taskdash("buggy")
    assert "Task Created Successfully" in buggy.post("/create", data={"title": ""}).text
    assert "badge-Medium" in buggy.get("/tasks?priority=High").text.split("<table>")[1]
    base = taskdash("baseline")
    assert "Title is required." in base.post("/create", data={"title": "  "}).text
    assert "badge-Medium" not in base.get("/tasks?priority=High").text.split("<table>")[1]


def test_taskdash_about_page_hides_ground_truth_unless_disclosed(taskdash):
    assert "BUG-F2" not in taskdash("buggy").get("/about").text
    assert "BUG-F2-01" in taskdash("buggy", disclose=True).get("/about").text


def test_apex_baseline_enforces_rules_and_manifest_is_gated(apex):
    reg = {"fullname": "A B", "email": "a@b.c", "password": "Secret1!", "confirm_password": "Other1!"}
    buggy = apex("buggy")
    assert "Invalid credentials" in buggy.post("/login", data={"username": "a@b.c", "password": "abc"}).text
    assert "Account Created" in buggy.post("/register", data=reg).text
    assert "No results found" in buggy.get("/search?q=").text
    assert buggy.get("/api/bugs").status_code == 404
    base = apex("baseline")
    assert "at least 6 characters" in base.post("/login", data={"username": "a@b.c", "password": "abc"}).text
    page = base.post("/register", data=reg).text
    assert "Passwords do not match." in page and "Terms of Service" in page
    assert "Please enter a search term." in base.get("/search?q=").text
    assert apex("buggy", disclose=True).get("/api/bugs").status_code == 200


def test_agent_code_never_imports_ground_truth():
    """Nothing under app/ (the agent) may import the evaluator-only benchmark package."""
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.startswith("benchmarks") for n in names), f"{path} imports benchmark ground truth"


def test_benchmark_targets_carry_no_defect_information():
    from benchmarks.targets import TARGETS
    text = repr(TARGETS).lower()
    for token in ("bug-", "planted", "defect", "nan", "badge", "ignores"):
        assert token not in text


def test_evaluator_scores_and_reports_undefined_metrics():
    from benchmarks.evaluator import score
    hit = {"test_id": "T1", "oracle_class": "FIELD_CONSTRAINT", "classification": "CONFIRMED_REPRODUCIBLE",
           "derived_values": {"field_selector": "#title", "input_class": "EMPTY"}, "name": "Title: empty is rejected", "steps": []}
    dup = dict(hit, test_id="T2", derived_values={"field_selector": "#title", "input_class": "WHITESPACE"})
    stray = dict(hit, test_id="T3", derived_values={"field_selector": "#description", "input_class": "EMPTY"})
    anomaly = {"test_id": "T4", "oracle_class": "FILTER_CONSISTENCY", "classification": "UNVERIFIED_ANOMALY",
               "derived_values": {"control": "#priority-filter"}, "name": "Filter", "steps": []}
    s = score("taskdash", "buggy", [hit, dup, stray, anomaly])
    assert (s["tp"], s["fp"], s["fn"]) == (1, 1, 2)  # duplicate findings count once per defect
    assert s["precision"] == 0.5 and s["recall"] == 0.333
    assert s["detected_as_anomaly_only"] == ["BUG-F2-02"]
    empty = score("taskdash", "buggy", [])
    assert empty["precision"] is None and empty["recall"] == 0.0 and empty["f1"] is None
    base = score("taskdash", "baseline", [stray])
    assert base["defects_planted"] == 0 and base["false_alarms_on_defect_free_build"] == 1 and base["recall"] is None
    assert score("saucedemo", "public", [hit])["ground_truth"] is False
