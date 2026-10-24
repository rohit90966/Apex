"""
Multi-website benchmark for VerityQA.

Runs the canonical pipeline used by the dashboard against each available target and mode:
    investigate (run_stateful_investigation) -> plan (synthesize_with_diagnostics)
    -> execute (QAPlanRunner) -> verify failures (BugInvestigator)
    -> export (PlaywrightTestExporter) -> run the exported pytest file (parity check)
The agent receives only URL, description, published requirements and authorized credentials.
Ground truth is loaded by the evaluator after all runs complete.

Usage:
    python scripts/run_multi_website_benchmark.py                     # local fixtures, both modes
    python scripts/run_multi_website_benchmark.py --targets taskdash --modes buggy,baseline --repeats 2
    python scripts/run_multi_website_benchmark.py --include-public    # also SauceDemo (network)
Outputs: reports/multi_website_benchmark.json, reports/multi_website_benchmark.md,
         reports/multi_website_findings.csv, reports/benchmark_exports/<run_id>.py
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.bugs.investigator import BugInvestigator, InvestigationVerdict  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.execution.runner import QAPlanRunner  # noqa: E402
from app.export.playwright_exporter import PlaywrightTestExporter, _func_name  # noqa: E402
from app.intelligence.target_context import canonical_origin, inventory_from_report  # noqa: E402
from app.investigation_agent import run_stateful_investigation  # noqa: E402
from app.investigation_agent.test_synthesis import synthesize_with_diagnostics  # noqa: E402
from app.knowledge.repository import get_knowledge_repository  # noqa: E402
from app.schemas import ActionType, ExecutionStatus, is_meaningful_assertion  # noqa: E402
from benchmarks.targets import TARGETS, PUBLIC_INDEPENDENT, TargetSpec  # noqa: E402

SUITE_VERSION = "multi-website-benchmark/1.0"
REPORTS = os.path.join(ROOT, "reports")
EXPORTS = os.path.join(REPORTS, "benchmark_exports")

STATUS_BUCKET = {
    ExecutionStatus.PASSED: "PASS",
    ExecutionStatus.FAILED: "FAIL",
    ExecutionStatus.ERROR: "ERROR",
    ExecutionStatus.AUTOMATION_ERROR: "ERROR",
    ExecutionStatus.ENVIRONMENT_ERROR: "ERROR",
    ExecutionStatus.LOCATOR_ERROR: "ERROR",
    ExecutionStatus.BLOCKED: "BLOCKED",
    ExecutionStatus.TEST_DEFINITION_ERROR: "INVALID",
    ExecutionStatus.INCONCLUSIVE: "INCOMPLETE",
    ExecutionStatus.SKIPPED: "SKIPPED",
    ExecutionStatus.PRODUCT_BEHAVIOR_DEVIATION: "FAIL",
}
STRATEGY_CAPABILITY = {
    "FIELD_CONSTRAINT": "FORM_VALIDATION", "FORM_VALIDATION": "FORM_VALIDATION",
    "FILTER_CONSISTENCY": "ITEM_SEARCH_FILTER", "LIST_ORDERING": "PAGINATION_SORTING",
    "CRUD_STATE_CHANGE": "CRUD_OPERATIONS",
}


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _reachable(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "VerityQA-benchmark"}), timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def _start_fixture(target: TargetSpec, mode: str) -> subprocess.Popen:
    env = dict(os.environ, VERITYQA_FIXTURE_MODE=mode)
    env.pop("VERITYQA_FIXTURE_DISCLOSE_BUGS", None)  # the agent must never see the manifest
    port = target.ports[mode]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", target.app_module, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if _reachable(target.url(mode), 1.0):
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"Fixture {target.key} ({mode}) did not start on port {port}")


class ScopedCredentials:
    """Exposes a target's authorized demo credentials for exactly one origin, then removes them."""
    KEYS = ("VERITYQA_TEST_CREDENTIALS_ORIGIN", "VERITYQA_TEST_USERNAME", "VERITYQA_TEST_PASSWORD")

    def __init__(self, url: str, creds: Optional[Dict[str, str]]):
        self.url, self.creds, self.saved = url, creds, {}

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)
        if self.creds:
            os.environ["VERITYQA_TEST_CREDENTIALS_ORIGIN"] = canonical_origin(self.url)
            os.environ["VERITYQA_TEST_USERNAME"] = self.creds["username"]
            os.environ["VERITYQA_TEST_PASSWORD"] = self.creds["password"]
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run_exported(script_path: str, env_creds: Dict[str, str]) -> Dict[str, str]:
    """Runs the exported module with pytest; returns {function_name: passed|failed|skipped|error}."""
    xml_path = script_path + ".junit.xml"
    env = dict(os.environ, **env_creds, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", script_path, f"--junitxml={xml_path}"],
                   cwd=os.path.dirname(script_path), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
    outcomes: Dict[str, str] = {}
    if not os.path.exists(xml_path):
        return outcomes
    for tc in ET.parse(xml_path).getroot().iter("testcase"):
        tags = {child.tag for child in tc}
        outcomes[tc.get("name")] = ("failed" if "failure" in tags else "error" if "error" in tags
                                    else "skipped" if "skipped" in tags else "passed")
    return outcomes


def _expected_export_outcome(status: ExecutionStatus) -> set:
    bucket = STATUS_BUCKET.get(status, "ERROR")
    return {"PASS": {"passed"}, "FAIL": {"failed"}, "INCOMPLETE": {"skipped"}, "BLOCKED": {"skipped"},
            "INVALID": {"skipped"}, "ERROR": {"failed", "error"}, "SKIPPED": {"skipped"}}[bucket]


async def run_one(target: TargetSpec, mode: str, repeat: int, args) -> Dict[str, Any]:
    url = target.url(mode)
    run_id = f"bench-{target.key}-{mode}-r{repeat}-{uuid.uuid4().hex[:6]}"
    record: Dict[str, Any] = {"run_id": run_id, "target": target.key, "target_name": target.name,
                              "target_class": target.target_class, "mode": mode, "repeat": repeat,
                              "target_url": url, "target_origin": canonical_origin(url),
                              "started_at": datetime.now(timezone.utc).isoformat()}
    with ScopedCredentials(url, target.credentials):
        t0 = time.time()
        try:
            report = await run_stateful_investigation(
                url=url, description=target.description, requirements=target.requirements or None,
                max_actions=args.max_actions, max_time_seconds=args.max_seconds, headless=True, investigation_id=run_id)
        except Exception as exc:
            record.update(status="INVESTIGATION_FAILED", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return record
        t1 = time.time()
        cases, diag = synthesize_with_diagnostics(report)
        t2 = time.time()
        runner = QAPlanRunner()
        results = await runner.run_all(cases, url)
        t3 = time.time()

        investigator = BugInvestigator(runner=runner, verification_retries=2)
        findings: List[Dict[str, Any]] = []
        for tc, res in zip(cases, results):
            if res.status != ExecutionStatus.FAILED:
                continue
            bug = await investigator.investigate_failure(res, tc, url)
            verdict = (InvestigationVerdict.UNVERIFIED_ANOMALY.value if bug.title.startswith("[ANOMALY]")
                       else "CONFIRMED_REPRODUCIBLE" if bug.verification_status.value == "CONFIRMED_REPRODUCIBLE"
                       else bug.verification_status.value)
            failing = next((s for s in tc.steps if s.step_number == res.failure_step), None)
            findings.append({
                "source": "execution",
                "test_id": tc.test_id, "name": tc.name, "oracle_class": tc.oracle_class,
                "derived_values": tc.derived_values or {}, "classification": verdict,
                "verification_status": bug.verification_status.value, "reproduction_rate": bug.reproduction_rate,
                "expected_source": getattr(getattr(failing, "assertion", None), "expected_source", None),
                "failure_reason": res.failure_reason, "actual_behavior": bug.actual_behavior,
                "steps": [f"{s.action.value} {s.selector or s.value or ''}".strip() for s in tc.steps],
            })
        from benchmarks.evaluator import findings_from_investigation
        findings.extend(findings_from_investigation(report))  # confirmed (2-of-2 reproduced) investigation leads
        t4 = time.time()

        os.makedirs(EXPORTS, exist_ok=True)
        exporter = PlaywrightTestExporter(runner)
        script_path = os.path.join(EXPORTS, f"{run_id}.py")
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(exporter.export_to_python_script(cases, url))
        cred_env = {k: os.environ[k] for k in ScopedCredentials.KEYS if os.environ.get(k)}
        exported = _run_exported(script_path, cred_env) if cases else {}
        t5 = time.time()

    inv = inventory_from_report(report)
    selectors = inv.selectors()
    ov = report.overview
    tctx = report.target_context or {}

    # ── Investigation metrics
    state_keys = [(s.route, tuple(s.headings)) for s in inv.states]
    constrained_fields = {(f_.route, fld.selector) for f_ in inv.forms if f_.route in inv.navigable_routes()
                          for fld in f_.fields if fld.required or fld.maxlength or fld.minlength or fld.pattern or fld.min or fld.max}
    investigation = {
        "status": getattr(ov.status, "value", str(ov.status)),
        "target_identity_correct": tctx.get("origin") == canonical_origin(url) and inv.origin == canonical_origin(url)
                                   and all(canonical_origin(s.url) == inv.origin for s in inv.states),
        "routes_discovered": len(inv.routes()), "navigable_routes": len(inv.navigable_routes()),
        "states_discovered": len(inv.states), "interactive_controls": len(inv.controls),
        "forms": len(inv.forms), "fields": sum(len(f_.fields) for f_ in inv.forms),
        "transitions": len(inv.transitions), "actions_attempted": ov.actions_attempted,
        "evidence_records": ov.evidence_ledger_count, "duration_seconds": round(t1 - t0, 2),
        "duplicate_state_rate": round(1 - len(set(state_keys)) / len(state_keys), 3) if state_keys else None,
        "llm_calls": ov.llm_calls_count, "llm_fallbacks": ov.llm_fallbacks_count, "llm_latency_seconds": ov.llm_latency_seconds,
        "retries": ov.retries_count, "stopping_reason": ov.stopping_reason,
    }

    # ── Generation metrics
    interaction_selectors = [s.selector for tc in cases for s in tc.steps if s.action in (ActionType.CLICK, ActionType.FILL, ActionType.SELECT)]
    repo = get_knowledge_repository()
    evidenced = set(diag.get("capabilities", []))
    covered_caps = set()
    accepted_irrelevant = 0
    for tc in cases:
        if tc.oracle_class in STRATEGY_CAPABILITY:
            covered_caps.add(STRATEGY_CAPABILITY[tc.oracle_class])
        for pid in tc.rule_ids:
            pat = repo.get_pattern(pid)
            if pat is None:
                continue
            caps = {c.value for c in pat.capabilities}
            covered_caps |= caps
            if not caps <= evidenced:
                accepted_irrelevant += 1
    reqs = target.requirements
    covered_reqs = {(tc.derived_values or {}).get("constraint_detail") for tc in cases
                    if (tc.derived_values or {}).get("constraint_source") == "REQUIREMENT"}
    covered_fields = {(next((s.route for s in inv.states if s.state_id in tc.evidence_ids), None), (tc.derived_values or {}).get("field_selector"))
                      for tc in cases if tc.oracle_class == "FIELD_CONSTRAINT"}
    rejected = diag.get("rejected_cases", [])
    generation = {
        "candidates": diag.get("candidates_generated", 0), "valid": diag.get("valid_cases", 0), "selected": len(cases),
        "invalid_or_rejected": len(rejected),
        "duplicates_rejected": sum(1 for r in rejected if r["reason"].startswith("Duplicate")),
        "with_meaningful_assertion": sum(1 for tc in cases if any(is_meaningful_assertion(s.assertion) for s in tc.steps)),
        "selectors_total": len(interaction_selectors),
        "selectors_grounded": sum(1 for s in interaction_selectors if s in selectors),
        "with_evidence_refs": sum(1 for tc in cases if tc.evidence_ids),
        "with_preconditions_and_expected_outcome": sum(1 for tc in cases if tc.expected_outcome and (tc.preconditions or not tc.requires_test_account)),
        "inapplicable_templates": len(diag.get("inapplicable_templates", [])),
        "ineligible_patterns": len(diag.get("ineligible_patterns", [])),
        "irrelevant_patterns_accepted": accepted_irrelevant,
        "unknown_constraints": len(diag.get("unknown_constraints", [])),
        "exploratory_hypotheses": len(diag.get("exploratory_hypotheses", [])),
        "capabilities_evidenced": sorted(evidenced),
        "capability_coverage": round(len(covered_caps & evidenced) / len(evidenced), 3) if evidenced else None,
        "requirements_total": len(reqs), "requirements_covered": sum(1 for r in reqs if r in covered_reqs),
        "constrained_fields_total": len(constrained_fields),
        "constrained_fields_covered": len({f for f in constrained_fields if f in covered_fields or (None, f[1]) in covered_fields}),
        "by_strategy": diag.get("candidates_by_strategy", {}),
        "planning_seconds": round(t2 - t1, 3), "archetype": diag.get("archetype"),
        "llm_used_for_planning": bool(diag.get("llm_used")),
        "llm_cases_proposed": diag.get("llm_cases_proposed", 0),
        "llm_cases_accepted": diag.get("llm_cases_accepted", 0),
        "llm_planning_error": diag.get("llm_error"),
        "llm_planning_latency_seconds": diag.get("llm_latency_seconds"),
        "llm_rejection_reasons": [r["reason"][:120] for r in diag.get("llm_rejections", [])][:10],
        "secondary_archetypes": diag.get("secondary_archetypes", []),
    }

    # ── Execution metrics
    buckets = Counter(STATUS_BUCKET.get(r.status, "ERROR") for r in results)
    planned_meaningful = sum(sum(1 for s in tc.steps if is_meaningful_assertion(s.assertion)) for tc in cases)
    execution = {
        "executed": len(results), **{k: buckets.get(k, 0) for k in ("PASS", "FAIL", "ERROR", "BLOCKED", "INVALID", "INCOMPLETE", "SKIPPED")},
        "locator_failures": sum(1 for r in results if r.status == ExecutionStatus.LOCATOR_ERROR),
        "infrastructure_failures": sum(1 for r in results if r.status in (ExecutionStatus.ENVIRONMENT_ERROR, ExecutionStatus.AUTOMATION_ERROR, ExecutionStatus.ERROR)),
        "assertions_evaluated": sum(r.assertions_evaluated for r in results),
        "meaningful_assertions_evaluated": sum(r.meaningful_assertions_evaluated for r in results),
        "meaningful_assertions_planned": planned_meaningful,
        "assertion_evaluation_rate": round(sum(r.meaningful_assertions_evaluated for r in results) / planned_meaningful, 3) if planned_meaningful else None,
        "execution_seconds": round(t3 - t2, 2), "verification_seconds": round(t4 - t3, 2),
    }

    # ── Export parity (structured semantics + independent execution of the exported file)
    semantics = exporter.export_semantics(cases, url)
    structural = all(
        sem["actions"] == [(s.action.value, s.selector, s.value) for s in runner.synthesize_steps(tc, url)]
        and sem["assertions"] == [(s.assertion.assertion_type.value, s.assertion.target_selector, s.assertion.expected_value)
                                  for s in runner.synthesize_steps(tc, url) if s.assertion]
        for sem, tc in zip(semantics, cases))
    agree = 0
    disagreements = []
    for idx, (tc, res) in enumerate(zip(cases, results), 1):
        outcome = exported.get(_func_name(tc, idx))
        expected = _expected_export_outcome(res.status)
        if tc.requires_test_account and not cred_env:
            # Exports never embed credentials; without origin-scoped env credentials the export is BLOCKED.
            expected = {"skipped"}
        if outcome in expected:
            agree += 1
        else:
            disagreements.append({"test_id": tc.test_id, "internal": res.status.value, "exported": outcome})
    export = {"script": os.path.relpath(script_path, ROOT), "structural_parity": structural,
              "verdict_agreement": agree, "verdict_total": len(cases), "disagreements": disagreements,
              "exported_outcomes": dict(Counter(exported.values())), "export_run_seconds": round(t5 - t4, 2)}

    record.update(
        status="COMPLETED",
        investigation=investigation, generation=generation, execution=execution, export=export,
        findings=findings,
        plan_signatures=sorted({"|".join(f"{s.action.value}:{s.selector}:{s.value}" for s in tc.steps) for tc in cases}),
        plan_selectors=sorted({s.selector for tc in cases for s in tc.steps if s.selector}
                              | {s.assertion.target_selector for tc in cases for s in tc.steps
                                 if s.assertion and s.assertion.target_selector and s.assertion.target_selector not in ("body", "html")}),
        inventory_selectors=sorted(selectors),
        examples=[{"test_id": tc.test_id, "name": tc.name, "oracle_class": tc.oracle_class,
                   "expected_source": next((s.assertion.expected_source for s in tc.steps if s.assertion and s.assertion.expected_source), None),
                   "steps": [{"action": s.action.value, "selector": s.selector, "value": s.value,
                              "assertion": (f"{s.assertion.assertion_type.value}({s.assertion.target_selector or ''}, {s.assertion.expected_value or ''})"
                                            if s.assertion else None)} for s in tc.steps]}
                  for tc in cases],
        results=[{"test_id": r.test_id, "status": r.status.value, "failure_reason": (r.failure_reason or "")[:300],
                  "meaningful_assertions": r.meaningful_assertions_evaluated, "duration": r.duration_seconds} for r in results],
        unknown_constraints=diag.get("unknown_constraints", []),
        inapplicable_templates=diag.get("inapplicable_templates", []),
        timings={"investigation": round(t1 - t0, 2), "planning": round(t2 - t1, 3), "execution": round(t3 - t2, 2),
                 "verification": round(t4 - t3, 2), "export_run": round(t5 - t4, 2)},
    )
    return record


def _fmt(v: Any) -> str:
    if v is None:
        return "undefined"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def write_markdown(summary: Dict[str, Any], path: str) -> None:
    L = [f"# VerityQA Multi-Website Benchmark", "",
         f"- Benchmark ID: `{summary['benchmark_id']}`", f"- Timestamp: {summary['timestamp']}",
         f"- Code commit: `{summary['code_commit']}` (working tree dirty: {summary['working_tree_dirty']})",
         f"- Configuration identity: `{summary['config_identity']}`", f"- Suite version: `{SUITE_VERSION}`", ""]
    L += ["## Target availability", "", "| Target | Class | Status | Note |", "|---|---|---|---|"]
    for t in summary["targets"]:
        L.append(f"| {t['name']} | {t['target_class']} | {t['availability']} | {t.get('note', '')} |")
    runs = [r for r in summary["runs"] if r.get("status") == "COMPLETED"]
    L += ["", "## Per-run results", "",
          "| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        i, g, e, x = r["investigation"], r["generation"], r["execution"], r["export"]
        L.append(f"| `{r['run_id']}` | {r['target']} | {r['mode']} | {g['archetype']} | {i['routes_discovered']} | {i['states_discovered']} | "
                 f"{i['interactive_controls']} | {g['selected']} | {e['PASS']} | {e['FAIL']} | {e['ERROR']} | {e['BLOCKED']} | {e['INVALID']} | "
                 f"{e['INCOMPLETE']} | {e['locator_failures']} | {g['selectors_grounded']}/{g['selectors_total']} | "
                 f"{g['with_meaningful_assertion']}/{g['selected']} | {x['verdict_agreement']}/{x['verdict_total']} |")
    L += ["", "## Defect detection (evaluator, after the runs)", "",
          "| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        s = r.get("score", {})
        if not s.get("ground_truth"):
            L.append(f"| `{r['run_id']}` | {r['target']} | {r['mode']} | n/a | | | | | | | | | | {s.get('note', '')} |")
            continue
        L.append(f"| `{r['run_id']}` | {r['target']} | {r['mode']} | {s['defects_planted']} | {s['tp']} | {s['fp']} | {s['fn']} | "
                 f"{_fmt(s['precision'])} | {_fmt(s['recall'])} | {_fmt(s['f1'])} | {', '.join(s['detected_defect_ids']) or '-'} | "
                 f"{', '.join(s['detected_by_execution']) or '-'} | {', '.join(s['detected_by_investigation']) or '-'} | "
                 f"{', '.join(s['missed_defect_ids']) or '-'} | {', '.join(s['detected_as_anomaly_only']) or '-'} | "
                 f"{_fmt(s['false_alarms_on_defect_free_build'])} |")
    agg = summary["aggregate"]
    L += ["", "## Aggregate (confirmed findings only, buggy builds)", "",
          f"- TP {agg['tp']}, FP {agg['fp']}, FN {agg['fn']}; precision {_fmt(agg['precision'])}, recall {_fmt(agg['recall'])}, F1 {_fmt(agg['f1'])}",
          f"- False alarms on defect-free (baseline) builds: {agg['baseline_false_alarms']}",
          f"- Cross-target selector leaks: {agg['cross_target_selector_leaks']}; irrelevant patterns accepted: {agg['irrelevant_patterns_accepted']}",
          f"- Export verdict agreement: {agg['export_agreement']}/{agg['export_total']}", ""]
    L += ["## Generation and coverage", "",
          "| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        g, e = r["generation"], r["execution"]
        L.append(f"| `{r['run_id']}` | {g['candidates']} | {g['invalid_or_rejected']} | {g['inapplicable_templates']} | {g['unknown_constraints']} | "
                 f"{_fmt(g['capability_coverage'])} | {g['requirements_covered']}/{g['requirements_total']} | "
                 f"{g['constrained_fields_covered']}/{g['constrained_fields_total']} | {_fmt(e['assertion_evaluation_rate'])} |")
    L += ["", "## Timing and LLM usage", "",
          "| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |",
          "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        t, i, g = r["timings"], r["investigation"], r["generation"]
        L.append(f"| `{r['run_id']}` | {t['investigation']} | {t['planning']} | {t['execution']} | {t['verification']} | {t['export_run']} | "
                 f"{i['llm_calls']} / {i['llm_fallbacks']} / {i['llm_latency_seconds']} | "
                 f"{g.get('llm_cases_proposed', 0)} / {g.get('llm_cases_accepted', 0)} / {g.get('llm_planning_error') or '-'} |")
    if summary.get("reproducibility"):
        L += ["", "## Repeated-run reproducibility", "", "| Target | Mode | Runs | PASS counts | FAIL counts | Detected-defect sets identical | Plan Jaccard (min) |", "|---|---|---|---|---|---|---|"]
        for k, v in summary["reproducibility"].items():
            L.append(f"| {v['target']} | {v['mode']} | {v['runs']} | {v['pass_counts']} | {v['fail_counts']} | {v['detections_identical']} | {_fmt(v['plan_jaccard_min'])} |")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in summary["limitations"]]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets", default="apex_portal,taskdash,benchmark_shop,taskflow")
    ap.add_argument("--modes", default="buggy,baseline")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--include-public", action="store_true", help="also run public targets (network required)")
    ap.add_argument("--max-actions", type=int, default=30)
    ap.add_argument("--max-seconds", type=float, default=150.0)
    ap.add_argument("--out", default=os.path.join(REPORTS, "multi_website_benchmark"))
    args = ap.parse_args()

    wanted = [k.strip() for k in args.targets.split(",") if k.strip()]
    if args.include_public:
        wanted += [t.key for t in TARGETS if t.target_class == PUBLIC_INDEPENDENT and t.key not in wanted]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    targets = [t for t in TARGETS if t.key in wanted]

    benchmark_id = f"mwb-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    config = {"targets": wanted, "modes": modes, "repeats": args.repeats, "max_actions": args.max_actions,
              "max_seconds": args.max_seconds, "llm_provider": get_settings().llm_provider,
              "groq_configured": bool(get_settings().groq_api_key or os.environ.get("GROQ_API_KEY")),
              "groq_model": os.environ.get("GROQ_MODEL") or get_settings().groq_model,
              "llm_test_proposals": os.environ.get("VERITYQA_LLM_TEST_PROPOSALS") == "1"}
    summary: Dict[str, Any] = {
        "benchmark_id": benchmark_id, "timestamp": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git("rev-parse", "HEAD"), "working_tree_dirty": bool(_git("status", "--porcelain")),
        "config": config, "config_identity": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16],
        "suite_version": SUITE_VERSION, "targets": [], "runs": [],
    }

    procs: List[subprocess.Popen] = []
    plan: List[tuple] = []
    try:
        for t in targets:
            entry = {"key": t.key, "name": t.name, "target_class": t.target_class}
            if t.app_module:
                started = []
                for m in modes:
                    procs.append(_start_fixture(t, m))
                    started.append(m)
                entry.update(availability="AVAILABLE", modes=started)
                plan += [(t, m) for m in started]
            elif t.public_url:
                if _reachable(t.public_url, 8.0):
                    host = canonical_origin(t.public_url).split("://", 1)[1]
                    settings = get_settings()
                    if host not in settings.allowed_hosts_list:  # operator authorization for this benchmark run
                        settings.verityqa_allowed_hosts = settings.verityqa_allowed_hosts + "," + host
                    entry.update(availability="AVAILABLE", modes=["public"], note="Public target; no ground truth, single mode")
                    plan.append((t, "public"))
                else:
                    entry.update(availability="UNAVAILABLE", note="network unreachable")
            else:
                ok = _reachable(t.expected_port_url, 3.0)
                entry.update(availability="AVAILABLE" if ok else "UNAVAILABLE",
                             note="" if ok else f"nothing is serving {t.expected_port_url}; fixture is not present in this repository")
                if ok:
                    plan.append((t, "external"))
            summary["targets"].append(entry)

        for rep in range(1, args.repeats + 1):
            for t, m in plan:
                print(f"[benchmark] {t.key} {m} repeat {rep} ...", flush=True)
                rec = await run_one(t, m, rep, args)
                summary["runs"].append(rec)
                ex = rec.get("execution", {})
                print(f"[benchmark]   {rec.get('status')} tests={rec.get('generation', {}).get('selected')} "
                      f"PASS={ex.get('PASS')} FAIL={ex.get('FAIL')} ERROR={ex.get('ERROR')} findings={len(rec.get('findings', []))}", flush=True)
    finally:
        for p in procs:
            p.terminate()

    # ── Evaluation (ground truth enters only here)
    from benchmarks.evaluator import score
    completed = [r for r in summary["runs"] if r.get("status") == "COMPLETED"]
    for r in completed:
        r["score"] = score(r["target"], r["mode"], r["findings"])

    # Cross-target leaks: selectors used by a plan that this run never observed but another target did.
    inventories = {r["run_id"]: set(r["inventory_selectors"]) for r in completed}
    leaks = 0
    for r in completed:
        own = inventories[r["run_id"]]
        others = set().union(*(v for k, v in inventories.items() if k != r["run_id"] and not k.startswith(f"bench-{r['target']}-"))) if len(inventories) > 1 else set()
        used = set(r["plan_selectors"])
        r["cross_target_selector_leaks"] = sorted(s for s in used if s not in own and s in others)
        r["ungrounded_selectors"] = sorted(s for s in used if s not in own)
        leaks += len(r["cross_target_selector_leaks"])

    buggy = [r["score"] for r in completed if r["score"].get("ground_truth") and r["mode"] == "buggy"]
    tp, fp, fn = (sum(s[k] for s in buggy) for k in ("tp", "fp", "fn"))
    precision = round(tp / (tp + fp), 3) if tp + fp else None
    recall = round(tp / (tp + fn), 3) if tp + fn else None
    summary["aggregate"] = {
        "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
        "f1": round(2 * precision * recall / (precision + recall), 3) if precision and recall else None,
        "baseline_false_alarms": sum(r["score"]["fp"] for r in completed if r["score"].get("ground_truth") and r["mode"] == "baseline"),
        "cross_target_selector_leaks": leaks,
        "irrelevant_patterns_accepted": sum(r["generation"]["irrelevant_patterns_accepted"] for r in completed),
        "export_agreement": sum(r["export"]["verdict_agreement"] for r in completed),
        "export_total": sum(r["export"]["verdict_total"] for r in completed),
    }
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in completed:
        groups.setdefault(f"{r['target']}:{r['mode']}", []).append(r)
    summary["reproducibility"] = {}
    for key, rs in groups.items():
        if len(rs) < 2:
            continue
        sigs = [set(r["plan_signatures"]) for r in rs]
        jacc = [len(a & b) / len(a | b) if a | b else 1.0 for i, a in enumerate(sigs) for b in sigs[i + 1:]]
        summary["reproducibility"][key] = {
            "target": rs[0]["target"], "mode": rs[0]["mode"], "runs": len(rs),
            "pass_counts": [r["execution"]["PASS"] for r in rs], "fail_counts": [r["execution"]["FAIL"] for r in rs],
            "pass_count_stdev": round(statistics.pstdev([r["execution"]["PASS"] for r in rs]), 3),
            "detections_identical": len({tuple(r["score"].get("detected_defect_ids", [])) for r in rs}) == 1,
            "plan_jaccard_min": round(min(jacc), 3) if jacc else None,
        }
    summary["limitations"] = [
        "TaskFlow (fixture 3) is not present in this repository or machine; no TaskFlow results are reported." if any(
            t["key"] == "taskflow" and t["availability"] == "UNAVAILABLE" for t in summary["targets"]) else "",
        "Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.",
        "Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).",
        "Fixture state (e.g. tasks created by tests) persists across repeats within one server process.",
        "TN/specificity are not reported: the fixtures define no universe of negative cases.",
    ]
    summary["limitations"] = [x for x in summary["limitations"] if x]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=str)
    write_markdown(summary, args.out + ".md")
    with open(os.path.join(os.path.dirname(args.out), "multi_website_findings.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["benchmark_id", "run_id", "target", "mode", "test_id", "name", "oracle_class", "classification",
                    "expected_source", "reproduction_rate", "matched_defect", "failure_reason"])
        for r in completed:
            matched = {tid: did for did, tids in r["score"].get("matches", {}).items() for tid in tids}
            for f in r["findings"]:
                w.writerow([benchmark_id, r["run_id"], r["target"], r["mode"], f["test_id"], f["name"], f["oracle_class"],
                            f["classification"], f.get("expected_source") or f.get("source"),
                            f.get("reproduction_rate") or json.dumps(f.get("reproduction") or ""), matched.get(f["test_id"], ""),
                            (f.get("failure_reason") or "")[:200]])
    print(f"[benchmark] wrote {args.out}.json / .md and multi_website_findings.csv")


if __name__ == "__main__":
    asyncio.run(main())
