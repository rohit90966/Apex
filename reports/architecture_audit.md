# VerityQA architecture audit (Harbor Events follow-up)

Companion documents: `reports/root_cause_matrix.md` (failure → component → evidence) and
`reports/benchmark_comparison.md` (before/after metrics).

## 1. Repository state at the start of the audit

* Branch `main`, HEAD `c58eb5ddcde0c1c92e96f1d8da6a2281852d5980` ("Test case quantity improved").
* Large uncommitted working tree (earlier sessions): planner/executor/findings/knowledge/benchmark changes,
  `demo_site/` (Harbor Events), legacy demo sites moved to `archive/legacy_demo_sites/`, untracked
  `app/findings/registry.py`, `app/knowledge/memory.py`, reports. Nothing was reset, reverted, stashed or deleted;
  all changes in this audit are additive edits to that working tree. Nothing was committed.
* A user-started dashboard/demo server (`uvicorn demo_app.main:app --port 8001`, started before the move and
  still serving from memory) was left running; benchmarks used ports 8201/8211 (Harbor) and 8101–8113 (legacy).
* Baseline artefacts preserved: `reports/harbor_events/benchmark.{json,md}`, persisted runs
  `reports/benchmark_runs/inv-7b840e16b3b8` (buggy) and `inv-c549a289d070` (clean).

## 2. Production runtime path (verified by reading the code that the entry points call)

```
Dashboard (app/static/app.js, React bundle)
 ├─ EventSource /api/investigate/stream ─┐
 │                                       ▼
 │   app/main.py investigate_stream ──► app.investigation_agent.run_stateful_investigation  (workflow.py, LangGraph)
 │       nodes: bootstrap/auth (authenticator.py) → choose question (agenda.py, frontier.py) → probe
 │       (probes/{forms,nav,lists,controls,invariants}.py, probe_runner.py, browser_session.py, perception.py)
 │       → compare (comparator.py, oracles.py) → leads (leads.py) → record (agenda budgets, transitions)
 │       → stop? (agenda.should_stop) → reproduce leads (reproducer.py, 2 isolated replays)
 │       → report_builder.build_report  ──► InvestigationReport + observed_inventory
 │                                            (intelligence/target_context.build_inventory_from_state)
 │   ──► investigation_agent.test_synthesis.synthesize_with_diagnostics
 │         └─ test_planning/planner.KnowledgeGuidedTestPlanner
 │              inventory_from_report → knowledge/qa_retrieval.evidence_profile → QAKnowledgeRetriever
 │              → strategy generators (field/server/cross-field/record/filter/sort/transition/route/a11y/
 │                lead-regression/templates; optional LLM proposals) → validator.GroundedTestValidator → cap
 │   ──► runs/store.RunStore.complete (report.json, test_cases.json, findings.json via findings/registry.py)
 ├─ fetch /api/runs/{id}                     → RunStore.get (findings derived read-only for old runs)
 ├─ fetch POST /api/tests/execute {run_id}   → execution/runner.QAPlanRunner.run_all
 │       → synthesize_steps (auth prelude, credential placeholders, unique test data)
 │       → execution/executor.BrowserExecutor.execute_steps (fresh browser context per test; oracles incl.
 │         execution/list_oracles.py) → ExecutionResult
 │       → failures: bugs/investigator.BugInvestigator.investigate_failure (classify; retries; lifecycle)
 │       → RunStore.save_execution → findings registry refresh; knowledge/memory.record_execution
 ├─ fetch /api/runs/{id}/export              → export/playwright_exporter (same steps + same oracle code)
 └─ fetch POST /api/benchmark[/fixture2]     → (legacy, see §5)

Benchmark harness: scripts/run_multi_website_benchmark.py calls the same functions in the same order
(run_stateful_investigation → synthesize_with_diagnostics → QAPlanRunner.run_all → BugInvestigator →
RunStore), then benchmarks/evaluator.score with benchmarks/ground_truth.py (never imported by app/*).
```

Data contracts between stages: `InvestigationReport` (+ `observed_inventory`, schema 2.0) → `TestCase`
(steps with `ExecutionAssertion.expected_source`) → `ExecutionResult` (status taxonomy PASSED / FAILED /
LOCATOR_ERROR / AUTOMATION_ERROR / ENVIRONMENT_ERROR / INCONCLUSIVE / BLOCKED / TEST_DEFINITION_ERROR,
`evidence_retained`) → `BugReport` (`classification`, `expected_source`, now `verdict_rationale`, `attempts`)
→ findings registry (stable finding ids) → UI.

## 3. Disconnected or parallel code (exists, not on the live path)

| Module | Status |
|---|---|
| `app/investigation_agent/qa_knowledge_integration.py` | No production import (tests only). Would add questions without probe scripts, which the workflow cannot execute. |
| `app/investigation_agent/probes/accessibility.py` | No production import; family `accessibility` has no dispatcher in the workflow. |
| `app/agents/*`, `/api/run`, `app/test_planning_agent` | Legacy pipeline; not used by the dashboard investigation flow. |
| `app/benchmark/manifest.py` + `/api/benchmark*` | Legacy benchmark inside the production package with a planted-bug manifest and a hand-written BUG-01 test in `app/main.py`. Dashboard calls these with POST; routes are GET (broken). Does not feed the agent's decisions. |
| `app/knowledge/retriever.py` (Chroma/Ollama) | Used only by the legacy agents. |

Knowledge that *does* reach production decisions: `app/knowledge/qa/*.json` via `qa_retrieval` →
planner strategy selection (each generated case carries `knowledge_ids`). The investigation agenda does not use it.

## 4. Root causes (summary — full matrix in root_cause_matrix.md)

Confirmed and fixed: RC-1 instance pages never planned; RC-2 mutation budget counted navigation; RC-3 repeated
executions reused identity data; RC-4 retry evidence mis-weighted; RC-5 placeholder options recorded as values;
RC-6 label-only sort/filter oracles; RC-7 no post-action verification of per-record commands; RC-8 cap
exhausted by the first form; RC-9 same-heading transitions and click prerequisites were not planned.

Architectural pattern behind most of them: **the planner reasoned about pages, not about states and
records.** Reachability was a property of a URL shape, expectations were read from labels, and a test's
preconditions were "navigate to the route". The fixes move each decision onto observed evidence: a page is
testable when the run *saw a link to it*; a state is reachable by *replaying the clicks that produced it*;
a filter is checked against *the record's own field*; a command is checked against *the record it acted on*;
a retry counts when *the same oracle ran on the same step* under *fresh data*.

## 5. Remediation plan (prioritised)

| P | Item | Status |
|---|---|---|
| P0 | RC-1…RC-9 | Done in this audit, with regression tests (`tests/test_architecture_root_causes.py`) |
| P1 | Move `/api/benchmark*` and `app/benchmark/manifest.py` out of the production package (or delete); fix or remove the dashboard benchmark buttons (POST vs GET) | Open — product decision |
| P1 | Cross-element invariants (counter/badge equals item count; totals equal sum of lines) | Open — needs record/aggregate identification on the page |
| P2 | Persistence across pages (record changed on list page *and* detail page) | Open |
| P2 | Investigation agenda driven by the same knowledge as the planner (currently planner only) | Open; current integration module is not wired because it creates script-less questions |
| P2 | Run-level fixture reset hook for targets that support it (fresh data per suite) | Open — the per-attempt unique data handles identity collisions, not other shared state |
| P3 | Remove or consolidate legacy `app/agents` pipeline | Open |

## 6. Safety and isolation notes

* No action budgets were raised. RC-2 changes *what* counts against the mutation budget (only requests that
  can change server state), not its size; the probe and time budgets are unchanged.
* Record-command tests are generated only when `SafetyGovernor.assess_risk` does not classify the command as
  irreversible and the host allows mutations (`allows_mutations_for_url`).
* Unique test data contains only a random hex token; it is recorded as `test_data_namespace` in execution
  evidence; no credentials or personal data are stored.
* Ground truth remains in `benchmarks/ground_truth.py`; no Harbor route, selector, credential or defect
  signature was added to `app/`.
