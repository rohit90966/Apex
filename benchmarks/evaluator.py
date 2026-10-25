"""
Independent evaluator: scores a finished benchmark run against evaluator-only ground truth.

Evaluation unit: one planted defect per (target, mode). A defect is a true positive when at least one
confirmed finding matches it: an executed test confirmed against an established expectation, or an
investigation lead the reproducer confirmed (2-of-2). Both sources are reported separately and a defect
found by both counts once. Unmatched confirmed
findings are false positives; in a defect-free (baseline) mode every confirmed finding is a false
alarm. Behavioural anomalies (reproduced against an inferred expectation) are scored separately and
never counted as confirmed detections. Metrics with a zero denominator are reported as None.
True negatives are not reported: the benchmarks define no universe of negative cases.
"""

from typing import Any, Dict, List, Optional

from benchmarks.ground_truth import DEFECT_MODES, defects_for, has_ground_truth  # noqa: F401

CONFIRMED = "CONFIRMED_DEFECT"
LIKELY = "LIKELY_DEFECT"
ANOMALY = "UNVERIFIED_ANOMALY"
# Older labels kept readable: CONFIRMED_REPRODUCIBLE (bug-report status), INVESTIGATION_CONFIRMED (earlier harness).
_ALIASES = {"CONFIRMED_REPRODUCIBLE": CONFIRMED, "INVESTIGATION_CONFIRMED": LIKELY}
# Primary detection metric: reproduced findings (confirmed or likely). Confirmed-only is reported alongside.
DETECTION_CLASSES = {CONFIRMED, LIKELY}


def _cls(f: Dict[str, Any]) -> str:
    c = f.get("classification")
    return _ALIASES.get(c, c)


def findings_from_investigation(report: Any) -> List[Dict[str, Any]]:
    """
    Confirmed, reproduced investigation leads as scorable findings. Leads that were not confirmed by the
    reproducer (status other than CONFIRMED) are never counted.
    """
    from app.findings.lifecycle import classify_investigation_lead
    out: List[Dict[str, Any]] = []
    leads = report.get("confirmed_bug_leads") if isinstance(report, dict) else getattr(report, "confirmed_bug_leads", None)
    for lead in leads or []:
        get = (lambda k, d=None: lead.get(k, d)) if isinstance(lead, dict) else (lambda k, d=None: getattr(lead, k, d))
        if str(get("status", "")) != "CONFIRMED":
            continue
        steps = list(get("setup_steps") or []) + list(get("steps") or [])
        target_name = str(get("target_desc") or "")
        field_selector = None
        for s in steps:
            tgt = s.get("target") if isinstance(s.get("target"), dict) else {}
            if target_name and target_name in (tgt.get("name"), s.get("raw_target")):
                field_selector = tgt.get("css") or s.get("raw_target")
        actual = get("actual") or {}
        actual_text = actual.get("text", "") if isinstance(actual, dict) else str(actual)
        out.append({
            "test_id": f"LEAD:{get('id')}",
            "source": "investigation",
            "name": str(get("title") or ""),
            "oracle_class": "INVESTIGATION_LEAD",
            "derived_values": {"field_selector": field_selector, "lead_target": target_name},
            "classification": classify_investigation_lead(lead).value,
            "oracle_type": str(getattr(get("oracle_type"), "value", get("oracle_type"))),
            "failure_reason": actual_text,
            "actual_behavior": actual_text,
            "reproduction": get("confirmation"),
            "steps": [f"{s.get('action_type')} {(s.get('target') or {}).get('css') if isinstance(s.get('target'), dict) else s.get('raw_target') or ''}".strip()
                      for s in steps],
        })
    return out


def defect_stages(target_key: str, mode: str, observed_routes: List[str], planned: List[Dict[str, Any]],
                  executed_status: Dict[str, str], findings: List[Dict[str, Any]],
                  persisted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Where each planted defect was (or was not) handled:
      discovered -> test_generated -> test_executed -> reproduced -> persisted -> regression_test
    `planned` are finding-shaped views of generated test cases; `persisted` are the findings read back
    from the run store (the data the dashboard renders).
    """
    if not has_ground_truth(target_key) or mode not in DEFECT_MODES:
        return []
    rows = []
    for d in defects_for(target_key):
        matched_cases = [c for c in planned if d["match"](c)]
        executed = [c for c in matched_cases if executed_status.get(c["test_id"]) not in (None, "BLOCKED", "TEST_DEFINITION_ERROR", "SKIPPED")]
        reproduced = [f for f in findings if _cls(f) in DETECTION_CLASSES and d["match"](f)]
        via_lead = [f for f in reproduced if f.get("source") == "investigation"]
        stored = [p for p in persisted if p.get("classification") in DETECTION_CLASSES
                  and any(d["match"](s) for s in p.get("_match_views", []))]
        row = {
            "defect_id": d["id"], "title": d["title"], "route": d.get("route"),
            "discovered": d.get("route") in observed_routes,
            "test_generated": bool(matched_cases),
            "test_executed": bool(executed),
            "reproduced": bool(reproduced),
            "reproduced_by": sorted({f.get("source", "execution") for f in reproduced}),
            "classification": sorted({_cls(f) for f in reproduced}),
            "persisted": bool(stored),
            "regression_test": bool(matched_cases) or any(p.get("regression_test_ids") for p in stored),
            "matched_tests": [c["test_id"] for c in matched_cases][:6],
            "executed_statuses": sorted({executed_status.get(c["test_id"], "NOT_RUN") for c in matched_cases}),
        }
        stages = ["discovered", "test_generated", "test_executed", "reproduced", "persisted"]
        if via_lead and not row["test_generated"]:
            stages = ["discovered", "reproduced", "persisted"]  # found by investigation alone
        row["missed_at"] = next((s for s in stages if not row[s]), None)
        rows.append(row)
    return rows


def _ratio(num: int, den: int) -> Optional[float]:
    return round(num / den, 3) if den else None


def _f1(p: Optional[float], r: Optional[float]) -> Optional[float]:
    if p is None or r is None or (p + r) == 0:
        return None
    return round(2 * p * r / (p + r), 3)


def score(target_key: str, mode: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not has_ground_truth(target_key):
        return {"ground_truth": False, "note": "No ground truth for this target; detection metrics undefined."}

    defects = defects_for(target_key) if mode in DEFECT_MODES else []
    confirmed = [f for f in findings if _cls(f) in DETECTION_CLASSES]
    anomalies = [f for f in findings if _cls(f) == ANOMALY]
    strictly_confirmed = [f for f in findings if _cls(f) == CONFIRMED]

    def matches(pool: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        hits: Dict[str, List[str]] = {}
        for d in defects:
            ids = [f["test_id"] for f in pool if d["match"](f)]
            if ids:
                hits[d["id"]] = ids
        return hits

    confirmed_hits = matches(confirmed)
    anomaly_hits = matches(anomalies)
    matched_ids = {tid for ids in confirmed_hits.values() for tid in ids}
    false_positive_findings = [f["test_id"] for f in confirmed if f["test_id"] not in matched_ids]

    tp = len(confirmed_hits)
    fn = len(defects) - tp
    fp = len(false_positive_findings)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    by_source = {
        src: sorted(did for did, tids in confirmed_hits.items()
                    if any(f["test_id"] in tids and f.get("source", "execution") == src for f in confirmed))
        for src in ("execution", "investigation")
    }
    return {
        "ground_truth": True,
        "mode": mode,
        "defects_planted": len(defects),
        # A defect detected by both an executed test and an investigation lead counts once in tp.
        "detected_by_execution": by_source["execution"],
        "detected_by_investigation": by_source["investigation"],
        "detected_by_both": sorted(set(by_source["execution"]) & set(by_source["investigation"])),
        "false_positives_by_source": {
            src: [tid for tid in false_positive_findings
                  if next((f.get("source", "execution") for f in confirmed if f["test_id"] == tid), "execution") == src]
            for src in ("execution", "investigation")
        },
        # Confirmed-only view (excludes likely defects): established expectations only.
        "detected_confirmed_only": sorted(matches(strictly_confirmed)),
        "false_positives_confirmed_only": [f["test_id"] for f in strictly_confirmed
                                           if f["test_id"] not in {t for ids in matches(strictly_confirmed).values() for t in ids}],
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "detected_defect_ids": sorted(confirmed_hits),
        "missed_defect_ids": sorted(d["id"] for d in defects if d["id"] not in confirmed_hits),
        "detected_as_anomaly_only": sorted(set(anomaly_hits) - set(confirmed_hits)),
        "false_positive_test_ids": false_positive_findings,
        "false_alarms_on_defect_free_build": fp if mode not in DEFECT_MODES else None,
        "anomalies_reported": len(anomalies),
        "matches": {k: v for k, v in confirmed_hits.items()},
    }
