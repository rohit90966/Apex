"""
Per-run findings registry.

Builds the run's findings from persisted artifacts only (report, test cases, every saved execution), so the
result is reproducible and survives restarts:

  * investigation leads confirmed by the reproducer   -> source "investigation"
  * bug reports produced when executed tests failed  -> source "execution"

Identity: a finding is keyed by (origin, route, subject, rule). A regression test generated from a lead
(derived_values.source_lead_id) is attached to that lead's finding instead of creating a new one.
Classification is the strongest supported by any source (app/findings/lifecycle.py); passing or inconclusive
re-executions are kept as observations and never erase a confirmed/likely finding. No evidence is dropped
when sources are merged.
"""

from __future__ import annotations

import hashlib
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional

from app.findings import lifecycle
from app.intelligence.target_context import route_template

FINDINGS_SCHEMA_VERSION = "1.0"


def _route(url: str) -> str:
    try:
        return urllib.parse.urlparse(url or "").path or "/"
    except ValueError:
        return "/"


def _origin(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url or "")
        return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""
    except ValueError:
        return ""


def finding_id(origin: str, route: str, subject: str, rule: str) -> str:
    raw = "|".join([origin, route, subject, rule]).lower()
    return "F-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _lead_subject(lead: Dict[str, Any]) -> str:
    target = str(lead.get("target_desc") or "")
    for s in list(lead.get("setup_steps") or []) + list(lead.get("steps") or []):
        tgt = s.get("target") if isinstance(s.get("target"), dict) else {}
        if target and target in (tgt.get("name"), s.get("raw_target")):
            return tgt.get("css") or s.get("raw_target") or target
    return target or "page"


def _template_subject(subject: str) -> str:
    """A subject that is a page URL/path is identified by its route template (one symptom across item pages)."""
    if subject.startswith("/") or "://" in subject:
        parsed = urllib.parse.urlparse(subject)
        return route_template(parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return subject


def _lead_occurrence(lead: Dict[str, Any]) -> Dict[str, Any]:
    """One observation of a (possibly grouped) symptom, with its own identity and evidence."""
    actual = lead.get("actual") or {}
    return {"lead_id": lead.get("id"), "status": lead.get("status"),
            "url": lead.get("source_url") or (actual.get("source_url") if isinstance(actual, dict) else None),
            "observed": actual.get("text") if isinstance(actual, dict) else str(actual),
            "evidence_ids": list(lead.get("evidence_ids") or []),
            "screenshots": [x for x in (lead.get("before_screenshot_id"), lead.get("after_screenshot_id")) if x],
            "reproduction": lead.get("confirmation"), "reproduction_failure_reason": lead.get("reproduction_failure_reason")}


def _lead_key(origin: str, lead: Dict[str, Any]) -> (str, str, str, str):
    """Grouping key: same origin, page template, subject and rule = same underlying symptom."""
    route = route_template(_route(lead.get("source_url") or ""))
    subject = _template_subject(_lead_subject(lead))
    rule = _lead_rule(lead)
    return finding_id(origin, route, subject, rule), route, subject, rule


def _lead_rule(lead: Dict[str, Any]) -> str:
    expected = lead.get("expected") or {}
    text = expected.get("text", "") if isinstance(expected, dict) else str(expected)
    return f"{lead.get('probe_family', '')}:{(text or lead.get('title', ''))[:80]}"


def _case_route(case: Dict[str, Any]) -> str:
    navs = [s.get("value") for s in case.get("steps") or [] if s.get("action") == "NAVIGATE" and s.get("value")]
    return _route(navs[-1]) if navs else "/"


def _case_subject_rule(case: Dict[str, Any]) -> (str, str):
    d = case.get("derived_values") or {}
    subject = d.get("field_selector") or d.get("control") or case.get("test_id", "")
    rule = d.get("constraint_kind") or case.get("oracle_class") or ""
    if d.get("input_class"):
        rule = f"{rule}:{d['input_class']}"
    if d.get("layer"):
        rule = f"{rule}:{d['layer']}"
    return subject, rule


class _Builder:
    def __init__(self, origin: str):
        self.origin = origin
        self.findings: Dict[str, Dict[str, Any]] = {}
        self.lead_to_finding: Dict[str, str] = {}

    def upsert(self, fid: str, base: Dict[str, Any], source: Dict[str, Any]) -> None:
        f = self.findings.get(fid)
        if f is None:
            f = {"finding_id": fid, "classification": None, "sources": [], "observations": [], "history": [],
                 "regression_test_ids": [], "knowledge_ids": [], **base}
            self.findings[fid] = f
        cls = source.get("classification")
        if cls in lifecycle.DEFECT_CLASSES:
            f["sources"].append(source)
            new = lifecycle.stronger(f["classification"], cls)
            if new != f["classification"]:
                f["history"].append({"classification": new, "because": source.get("ref"), "at": source.get("at")})
            f["classification"] = new
        else:
            f["observations"].append(source)  # e.g. a passing or inconclusive regression run
        for k in source.get("knowledge_ids") or []:
            if k not in f["knowledge_ids"]:
                f["knowledge_ids"].append(k)


def build_findings(report: Optional[Dict[str, Any]], test_cases: Iterable[Dict[str, Any]],
                   executions: Iterable[Dict[str, Any]], target_url: str = "") -> Dict[str, Any]:
    report = report or {}
    cases = {c.get("test_id"): c for c in test_cases or []}
    origin = _origin(target_url or ((report.get("overview") or {}).get("target_url") or ""))
    b = _Builder(origin)

    # 1. Investigation leads (only reproducer-confirmed leads are findings; others are not listed).
    for lead in report.get("confirmed_bug_leads") or []:
        cls = lifecycle.classify_investigation_lead(lead)
        if lead.get("status") != "CONFIRMED":
            continue
        fid, route, subject, rule = _lead_key(origin, lead)
        b.lead_to_finding[str(lead.get("id"))] = fid
        actual = lead.get("actual") or {}
        expected = lead.get("expected") or {}
        b.upsert(fid, {"title": lead.get("title"), "route": route, "subject": subject, "rule": rule}, {
            "kind": "investigation", "ref": f"lead:{lead.get('id')}", "classification": cls.value,
            "oracle": str(lead.get("oracle_type")), "reproduction": lead.get("confirmation"),
            "expected": expected.get("text") if isinstance(expected, dict) else str(expected),
            "observed": actual.get("text") if isinstance(actual, dict) else str(actual),
            "evidence_ids": list(lead.get("evidence_ids") or []),
            "screenshots": [x for x in (lead.get("before_screenshot_id"), lead.get("after_screenshot_id")) if x],
            "occurrence": _lead_occurrence(lead),
        })

    # 2. Executed tests: failures through their bug reports; regression runs of a lead as observations.
    for ex in executions or []:
        exec_id = ex.get("execution_id")
        bugs = {bug.get("test_id"): bug for bug in ex.get("bug_reports") or []}
        for res in ex.get("results") or []:
            case = cases.get(res.get("test_id")) or {}
            d = case.get("derived_values") or {}
            bug = bugs.get(res.get("test_id"))
            lead_fid = b.lead_to_finding.get(str(d.get("source_lead_id"))) if d.get("source_lead_id") else None
            if bug is None and lead_fid is None:
                continue  # passing/blocked tests that relate to no finding
            if bug is not None:
                cls = bug.get("classification") or lifecycle.ANOMALY
            else:
                cls = lifecycle.classify_execution_result(
                    type("R", (), {"status": res.get("status"), "failure_step": res.get("failure_step")})(), None).value
            subject, rule = _case_subject_rule(case)
            route = _case_route(case)
            fid = lead_fid or finding_id(origin, route, subject, rule)
            source = {
                "kind": "execution", "ref": f"{exec_id}:{res.get('test_id')}", "execution_id": exec_id,
                "test_id": res.get("test_id"), "status": res.get("status"), "classification": cls,
                "expected_source": (bug or {}).get("expected_source"), "reproduction": (bug or {}).get("reproduction_rate"),
                "expected": (bug or {}).get("expected_behavior") or case.get("expected_outcome"),
                "observed": (bug or {}).get("actual_behavior") or res.get("failure_reason"),
                "screenshots": [x for x in [res.get("final_screenshot"), (bug or {}).get("evidence_screenshot")] if x],
                "knowledge_ids": case.get("knowledge_ids") or [],
            }
            b.upsert(fid, {"title": (bug or {}).get("title") or case.get("name"), "route": route, "subject": subject, "rule": rule}, source)
            if case.get("oracle_class") == "DEFECT_REGRESSION" or lead_fid:
                tid = res.get("test_id")
                if tid and tid not in b.findings[fid]["regression_test_ids"]:
                    b.findings[fid]["regression_test_ids"].append(tid)

    # Regression tests planned for a lead but not executed yet are still linked.
    for case in cases.values():
        lead_id = (case.get("derived_values") or {}).get("source_lead_id")
        fid = b.lead_to_finding.get(str(lead_id)) if lead_id else None
        if fid and case.get("test_id") not in b.findings[fid]["regression_test_ids"]:
            b.findings[fid]["regression_test_ids"].append(case.get("test_id"))

    findings = [f for f in b.findings.values() if f["classification"] is not None]
    for f in findings:
        f["occurrences"] = [s["occurrence"] for s in f["sources"] if s.get("occurrence")]

    # Leads that were observed but not verified stay visible as unresolved (never as disproven); leads whose
    # replays reached the oracle and did not reproduce are listed as disproven. Grouped like findings.
    unresolved: Dict[str, Dict[str, Any]] = {}
    disproven: Dict[str, Dict[str, Any]] = {}
    for lead in report.get("unstable_or_rejected_leads") or []:
        status = str(lead.get("status") or "")
        bucket = disproven if status == "REJECTED" else unresolved
        fid, route, subject, rule = _lead_key(origin, lead)
        entry = bucket.setdefault(fid, {"finding_id": fid, "title": lead.get("title"), "route": route, "subject": subject,
                                        "rule": rule, "statuses": [], "reasons": [], "occurrences": []})
        entry["occurrences"].append(_lead_occurrence(lead))
        if status not in entry["statuses"]:
            entry["statuses"].append(status)
        reason = lead.get("reproduction_failure_reason")
        if reason and reason not in entry["reasons"]:
            entry["reasons"].append(reason)
    findings.sort(key=lambda f: (-lifecycle.RANK.get(f["classification"], 0), f["finding_id"]))
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f["classification"]] = counts.get(f["classification"], 0) + 1
    return {"schema_version": FINDINGS_SCHEMA_VERSION, "counts": counts, "findings": findings,
            "unresolved": sorted(unresolved.values(), key=lambda e: e["finding_id"]),
            "disproven": sorted(disproven.values(), key=lambda e: e["finding_id"]),
            "sources": {"investigation": sum(1 for f in findings if any(s["kind"] == "investigation" for s in f["sources"])),
                        "execution": sum(1 for f in findings if any(s["kind"] == "execution" for s in f["sources"]))}}
