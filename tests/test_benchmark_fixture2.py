"""
tests/test_benchmark_fixture2.py
==================================
Tests for Fixture2BenchmarkEvaluator — the planted-bug evaluator for TaskDash.

Verifies:
- BUG-F2-01 (empty title accepted) is matched correctly
- BUG-F2-02 (priority filter broken) is matched correctly
- BUG-F2-03 (mark-done status not updated) is matched correctly
- False positives are correctly excluded
- Precision / Recall / F1 are computed correctly
"""
import pytest
from app.benchmark.manifest import Fixture2BenchmarkEvaluator, FIXTURE2_BUG_MANIFEST
from app.schemas import BugReport, BugSeverity, VerificationStatus


def _bug(title="", summary="", actual="", cause="", url="http://127.0.0.1:8002", test_id="TC-X"):
    return BugReport(
        bug_id="BUG-TEST",
        title=title,
        severity=BugSeverity.HIGH,
        verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
        reproduction_rate="1/1",
        test_id=test_id,
        target_url=url,
        summary=summary,
        expected_behavior="Expected",
        actual_behavior=actual,
        suspected_cause=cause,
    )


class TestFixture2Manifest:
    def test_manifest_has_three_bugs(self):
        assert len(FIXTURE2_BUG_MANIFEST) == 3

    def test_manifest_ids(self):
        ids = [item.bug_id for item in FIXTURE2_BUG_MANIFEST]
        assert "BUG-F2-01" in ids
        assert "BUG-F2-02" in ids
        assert "BUG-F2-03" in ids


class TestFixture2Matching:
    def setup_method(self):
        self.ev = Fixture2BenchmarkEvaluator()

    def test_matches_bug_f2_01_empty_title(self):
        bug = _bug(title="Empty task title accepted", actual="task created with empty title")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-01"

    def test_matches_bug_f2_01_no_title(self):
        bug = _bug(title="Task created with no title", summary="empty title accepted by server")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-01"

    def test_matches_bug_f2_02_priority_filter(self):
        bug = _bug(title="Priority filter ignored", actual="all tasks shown regardless of filter")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-02"

    def test_matches_bug_f2_02_filter_not_working(self):
        bug = _bug(summary="priority filter not working", actual="filter does not reduce task list")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-02"

    def test_matches_bug_f2_03_mark_done(self):
        bug = _bug(title="Mark done button does not update status", actual="status remains Open")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-03"

    def test_matches_bug_f2_03_status_unchanged(self):
        bug = _bug(summary="status not updated after marking complete", actual="status unchanged")
        assert self.ev._match_manifest_bug(bug) == "BUG-F2-03"

    def test_no_match_for_unrelated_bug(self):
        bug = _bug(title="Login page XSS vulnerability", actual="script executed")
        assert self.ev._match_manifest_bug(bug) is None

    def test_no_match_for_oracle_defect(self):
        bug = BugReport(
            bug_id="BUG-FP",
            title="Empty title accepted",
            severity=BugSeverity.LOW,
            verification_status=VerificationStatus.TEST_ORACLE_DEFECT,
            reproduction_rate="0/1",
            test_id="TC-X",
            target_url="http://127.0.0.1:8002",
            summary="empty title",
            expected_behavior="title required",
            actual_behavior="title accepted",
        )
        assert self.ev._match_manifest_bug(bug) is None


class TestFixture2Evaluate:
    def setup_method(self):
        self.ev = Fixture2BenchmarkEvaluator()

    def test_all_three_detected(self):
        bugs = [
            _bug(title="Empty task title accepted", actual="no title validation"),
            _bug(title="Priority filter ignored", actual="all tasks shown"),
            _bug(title="Mark done does not update status", actual="status remains Open"),
        ]
        result = self.ev.evaluate(bugs)
        assert result.true_positives_detected == 3
        assert result.false_positives == 0
        assert result.missed_bugs == 0
        assert result.precision == 1.0
        assert result.recall == 1.0
        assert result.f1_score == 1.0

    def test_partial_detection(self):
        bugs = [
            _bug(title="Empty task title accepted"),  # F2-01
            _bug(title="Mark done does not update status"),  # F2-03
        ]
        result = self.ev.evaluate(bugs)
        assert result.true_positives_detected == 2
        assert result.missed_bugs == 1
        assert "BUG-F2-02" in result.missed_bug_ids
        assert result.precision == 1.0
        assert abs(result.recall - 2/3) < 0.01

    def test_false_positive_counted(self):
        bugs = [
            _bug(title="Random network error", actual="connection refused"),
        ]
        result = self.ev.evaluate(bugs)
        assert result.true_positives_detected == 0
        assert result.false_positives == 1
        assert result.precision == 0.0
        assert result.recall == 0.0

    def test_oracle_defect_excluded_from_metrics(self):
        bugs = [
            BugReport(
                bug_id="BUG-OD",
                title="Empty title accepted",
                severity=BugSeverity.LOW,
                verification_status=VerificationStatus.TEST_ORACLE_DEFECT,
                reproduction_rate="0/1",
                test_id="TC-OD",
                target_url="http://127.0.0.1:8002",
                summary="oracle defect",
                expected_behavior="x",
                actual_behavior="y",
            )
        ]
        result = self.ev.evaluate(bugs)
        # Oracle defects are excluded — should count as 0 TP and 0 FP
        assert result.true_positives_detected == 0
        assert result.false_positives == 0

    def test_empty_bug_list(self):
        result = self.ev.evaluate([])
        assert result.true_positives_detected == 0
        assert result.false_positives == 0
        assert result.missed_bugs == 3
        assert result.precision == 0.0
        assert result.recall == 0.0
        assert result.f1_score == 0.0

    def test_benchmark_name(self):
        result = self.ev.evaluate([])
        assert "Fixture2" in result.benchmark_name or "TaskDash" in result.benchmark_name
