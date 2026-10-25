"""
Investigation memory: experience across runs, kept separate from the curated knowledge base.

  strategy_stats.json   generic, site-independent counters per knowledge entry (tests produced, executed,
                        reproduced findings, anomalies, inconclusive, infrastructure errors). Used only as a
                        small, capped ranking adjustment (strategy_effectiveness); it never makes a strategy
                        applicable or inapplicable and never states that a target has a defect.
  sites/<hash>.json     per-origin run summaries (run id, time, strategies applied, outcome counts). Reported
                        for the same origin only; never used to plan tests for another origin.

Nothing secret is stored: no input values, credentials, cookies, URLs with query strings or page text, only
identifiers and counts. Writes are atomic; memory can be disabled with VERITYQA_MEMORY_DISABLED=1 (benchmarks
do this so evaluations are not influenced by earlier evaluations).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

_LOCK = threading.Lock()
MAX_SITE_RUNS = 50
MIN_EXECUTIONS_FOR_EFFECT = 3


def memory_dir() -> str:
    return os.environ.get("VERITYQA_MEMORY_DIR") or os.path.join("data", "memory")


def enabled() -> bool:
    return os.environ.get("VERITYQA_MEMORY_DISABLED") != "1"


def _read(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _site_key(origin: str) -> str:
    return hashlib.sha256((origin or "").lower().encode("utf-8")).hexdigest()[:16]


_INFRA = {"LOCATOR_ERROR", "AUTOMATION_ERROR", "ENVIRONMENT_ERROR", "ERROR"}


def record_execution(run_id: str, origin: str, test_cases: Iterable[Any], results: Iterable[Any],
                     bug_reports: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    """Updates strategy counters and the origin's run summary from one execution. Returns the site entry."""
    if not enabled():
        return None
    get = lambda o, k, d=None: o.get(k, d) if isinstance(o, dict) else getattr(o, k, d)  # noqa: E731
    cases = {get(tc, "test_id"): tc for tc in test_cases}
    bugs = {get(b, "test_id"): get(b, "classification") for b in bug_reports}
    with _LOCK:
        stats_path = os.path.join(memory_dir(), "strategy_stats.json")
        stats = _read(stats_path, {})
        outcome_counts: Dict[str, int] = {}
        strategies_used: set = set()
        for res in results:
            tid = get(res, "test_id")
            status = str(getattr(get(res, "status"), "value", get(res, "status")))
            outcome_counts[status] = outcome_counts.get(status, 0) + 1
            for kid in (get(cases.get(tid, {}), "knowledge_ids") or []):
                if not kid.startswith("TS-"):
                    continue
                strategies_used.add(kid)
                s = stats.setdefault(kid, {"tests": 0, "executed": 0, "reproduced_findings": 0, "anomalies": 0,
                                           "inconclusive": 0, "infrastructure_errors": 0, "blocked": 0})
                s["tests"] += 1
                if status in ("BLOCKED", "TEST_DEFINITION_ERROR", "SKIPPED"):
                    s["blocked"] += 1
                    continue
                s["executed"] += 1
                if status in _INFRA:
                    s["infrastructure_errors"] += 1
                elif status == "INCONCLUSIVE":
                    s["inconclusive"] += 1
                cls = bugs.get(tid)
                if cls in ("CONFIRMED_DEFECT", "LIKELY_DEFECT"):
                    s["reproduced_findings"] += 1
                elif cls == "UNVERIFIED_ANOMALY":
                    s["anomalies"] += 1
        _write(stats_path, stats)

        site_path = os.path.join(memory_dir(), "sites", f"{_site_key(origin)}.json")
        site = _read(site_path, {"origin": origin, "runs": []})
        entry = {"run_id": run_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "strategies": sorted(strategies_used),
                 "outcomes": outcome_counts,
                 "findings": {c: sum(1 for v in bugs.values() if v == c) for c in set(v for v in bugs.values() if v)}}
        site["runs"] = (site.get("runs") or [])[-(MAX_SITE_RUNS - 1):] + [entry]
        _write(site_path, site)
        return entry


def strategy_effectiveness() -> Dict[str, float]:
    """Capped ranking adjustment in [-0.1, 0.1] per strategy; only strategies with enough executions."""
    if not enabled():
        return {}
    stats = _read(os.path.join(memory_dir(), "strategy_stats.json"), {})
    out: Dict[str, float] = {}
    for kid, s in stats.items():
        executed = s.get("executed", 0)
        if executed < MIN_EXECUTIONS_FOR_EFFECT:
            continue
        value = 0.3 * s.get("reproduced_findings", 0) / executed - 0.2 * s.get("infrastructure_errors", 0) / executed
        out[kid] = round(max(-0.1, min(0.1, value)), 3)
    return out


def site_history(origin: str) -> List[Dict[str, Any]]:
    """Previous run summaries for exactly this origin (reporting only)."""
    return _read(os.path.join(memory_dir(), "sites", f"{_site_key(origin)}.json"), {}).get("runs", [])
