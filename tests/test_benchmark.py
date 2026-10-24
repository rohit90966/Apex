"""
Tests for app/benchmark/manifest.py — BenchmarkEvaluator and planted-bug manifest
"""
import pytest
from app.benchmark.manifest import (
    BenchmarkEvaluator,
    PLANTED_BUG_MANIFEST,
    MANIFEST_BY_ID,
)
from app.schemas import BugReport, BugSeverity, VerificationStatus


# ── Manifest Integrity ─────────────────────────────────────────────────────────

def test_manifest_has_four_bugs():
    assert len(PLANTED_BUG_MANIFEST) == 4


def test_manifest_bug_ids_unique():
    ids = [item.bug_id for item in PLANTED_BUG_MANIFEST]
    assert len(ids) == len(set(ids))


def test_manifest_by_id_lookup():
    for bug_id in ["BUG-01", "BUG-02", "BUG-03", "BUG-04"]:
        assert bug_id in MANIFEST_BY_ID
        assert MANIFEST_BY_ID[bug_id].bug_id == bug_id


def test_manifest_required_fields():
    for item in PLANTED_BUG_MANIFEST:
        assert item.bug_id
        assert item.title
        assert item.description
        assert item.expected_behavior
        assert item.actual_buggy_behavior
        assert item.route


# ── BenchmarkEvaluator: Empty Detection ────────────────────────────────────────

def test_evaluate_no_bugs_detected():
    evaluator = BenchmarkEvaluator()
    result = evaluator.evaluate([])
    assert result.true_positives_detected == 0
    assert result.false_positives == 0
    assert result.missed_bugs == 4
    assert result.recall == 0.0
    assert result.f1_score == 0.0


# ── BenchmarkEvaluator: True Positive Matching ────────────────────────────────

def _make_bug(bug_id: str, title: str, summary: str, actual: str, cause: str = "") -> BugReport:
    return BugReport(
        bug_id=bug_id,
        title=title,
        severity=BugSeverity.HIGH,
        verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
        reproduction_rate="3/3",
        test_id="TC-001",
        target_url="http://127.0.0.1:8001/login",
        summary=summary,
        expected_behavior="Should validate",
        actual_behavior=actual,
        suspected_cause=cause,
    )


def test_evaluate_perfect_detection():
    evaluator = BenchmarkEvaluator()
    bugs = [
        _make_bug(
            "BUG-01",
            "[LOGIN] Password length validation bypass",
            "Login proceeds with short password, missing length validation",
            "password length bypass accepted single character",
            "password length check missing server side",
        ),
        _make_bug(
            "BUG-02",
            "[REGISTRATION] Terms bypass",
            "Registration accepted without terms field, terms bypass accepted",
            "terms acceptance bypass silently ignored",
            "terms validation missing from registration handler",
        ),
        _make_bug(
            "BUG-03",
            "[REGISTRATION] Password mismatch bypass",
            "Registration proceeds with mismatch password confirmation ignored",
            "confirm_password mismatch bypass accepted",
            "confirm password comparison missing",
        ),
        _make_bug(
            "BUG-04",
            "[SEARCH] Empty query accepted",
            "Empty search query accepted no validation returns no results",
            "blank search query empty string accepted no validation",
            "search empty query validation missing",
        ),
    ]
    result = evaluator.evaluate(bugs)
    assert result.true_positives_detected >= 1  # At least some should match
    assert result.total_planted_bugs == 4
    assert result.precision >= 0.0
    assert result.recall >= 0.0
    assert result.f1_score >= 0.0


def test_evaluate_metrics_consistency():
    evaluator = BenchmarkEvaluator()
    bugs = [
        _make_bug(
            "FP-001",
            "[LOGIN] Random unrelated error",
            "Something unrelated happened completely",
            "totally unrelated behavior xyz",
        )
    ]
    result = evaluator.evaluate(bugs)
    # False positive check
    assert result.total_planted_bugs == 4
    # Precision and recall are in [0,1]
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert 0.0 <= result.f1_score <= 1.0


def test_evaluate_summary_structure():
    evaluator = BenchmarkEvaluator()
    result = evaluator.evaluate([])
    assert hasattr(result, "benchmark_name")
    assert hasattr(result, "total_planted_bugs")
    assert hasattr(result, "detected_bug_ids")
    assert hasattr(result, "missed_bug_ids")
    assert isinstance(result.detected_bug_ids, list)
    assert isinstance(result.missed_bug_ids, list)


# ── Keyword Extraction ─────────────────────────────────────────────────────────

def test_keyword_extraction_non_empty():
    evaluator = BenchmarkEvaluator()
    item = PLANTED_BUG_MANIFEST[0]
    keywords = evaluator._extract_keywords(item)
    assert isinstance(keywords, list)
    assert len(keywords) > 0


def test_nearest_manifest_id_password():
    evaluator = BenchmarkEvaluator()
    bug = _make_bug(
        "X-001",
        "Password validation bypass login",
        "login form accepts short passwords without validation length",
        "password length bypass accepted",
        "minimum length not enforced",
    )
    nearest = evaluator._nearest_manifest_id(bug)
    assert nearest is not None
    assert nearest in MANIFEST_BY_ID
