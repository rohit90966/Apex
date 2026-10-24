# Multi-Website Generalization — Implementation and Validation Report

Companion documents: `docs/MULTI_WEBSITE_GENERALIZATION_AUDIT.md` (Phase Zero diagnosis),
`docs/RESIDUAL_SITE_SPECIFIC_REFERENCES.md` (every remaining SauceDemo reference and why it is legitimate),
`reports/multi_website_benchmark.md|json`, `reports/multi_website_findings.csv` (measured results).

## 1. Diagnosis of the original cross-target contamination

The TaskFlow artefacts were not caused by stale caches or reused runs (the planner only ever read the
current report; runs are keyed by `run_id`). They were **hardcoded SauceDemo defaults** on the canonical
dashboard path, applied whenever the current target lacked the evidence they assumed:

| Artefact seen on TaskFlow | Root cause (pre-refactor) |
|---|---|
| `/inventory.html` | `planner.py` `make_transition_steps` navigated to `{target}/inventory.html` when no login prelude existed; default URL assertions `"inventory"` |
| `#task-sort-select` with `za`/`lohi`/`hilo` | every selector containing `sort` received SauceDemo's three option values; oracle was only "body visible" |
| `#user-name`, `#password`, `#login-button`, `standard_user`, empty-username/password tests | `_synthesize_form_validations` emitted two login tests unconditionally with SauceDemo fallback selectors and `secret_sauce`/`standard_user`; auth prelude fell back to `standard_user` and asserted `inventory` |
| `#new-project`, `#project-name`, `#save-btn` | `_resolve_selector` returned the knowledge pattern's first selector hint when nothing matched |
| 3 BLOCKED | runner `_ensure_authenticated_session` injected a SauceDemo login into any test touching `sort`/`continue`/`checkout`; no password existed for `127.0.0.1:8003` |
| exported code with `print` and no assertions; "empty" tests filling values | the dashboard's script was generated client-side (`GeneratedTests.jsx`): no assertions, unconditional `[PASS]`, `"" → "test"`, `secret_sauce` |
| "passes" that verified nothing; locator problems reported as bug leads | `body`-visible filler assertions counted as passes; `CUSTOM` always passed; every Playwright error became `FAILED` and went to the bug investigator |

TaskFlow itself (fixture 3, port 8003) is **not present** in this repository or on this machine, so it
could not be re-run; each root cause is reproduced by an automated test with a TaskFlow-shaped report
(`tests/test_multi_website_generalization.py::test_taskflow_plan_contains_no_saucedemo_artefacts`).

## 2. Canonical runtime path (after the refactor)

```
Dashboard (runEvents.mjs) ─ POST /api/investigate/stream                      app/main.py
  └ validate_url_safety(url, VERITYQA_ALLOWED_HOSTS)                          app/safety.py
  └ run_stateful_investigation(url, description, requirements, run_id)        app/investigation_agent/__init__.py
      └ LangGraph investigation (perception, probes, governor, auth)          app/investigation_agent/*
      └ InvestigationReportBuilder.build_report                               report_builder.py
          ├ target_context  (run_id, origin, start/final URL, requirements, credential source)   app/intelligence/target_context.py
          └ observed_inventory (states, routes, controls+options, forms+field constraints,
                                transitions, auth, provenance)  — same origin only
  └ synthesize_test_cases_from_investigation(report, diagnostics)            app/investigation_agent/test_synthesis.py
      └ KnowledgeGuidedTestPlanner.plan_test_suite_sync                       app/test_planning/planner.py
          ├ ArchetypeClassifier.classify_from_inventory (structural capability evidence + confidence)
          ├ KnowledgePatternRetriever (eligible only if required capabilities are evidenced)
          ├ strategies over observed evidence: ROUTE_REACHABILITY, FIELD_BOUNDARIES
          │   (field_constraints.py), SORT_ORDER, FILTER_RESULTS, SUBMISSION_REFLECTION,
          │   observed transitions, grounded knowledge templates, confirmed-lead regressions
          └ GroundedTestValidator (selectors/routes/options/texts grounded, meaningful oracle,
              intent consistency, safety, semantic de-duplication)
  └ RunStore.complete(report, test_cases, test_planning diagnostics)          app/runs/store.py
Dashboard "Execute" ─ POST /api/tests/execute {run_id}
  └ QAPlanRunner.run_test: origin guard → scoped credential placeholders → BrowserExecutor
      outcome classes: PASSED | FAILED | LOCATOR_ERROR | AUTOMATION_ERROR | ENVIRONMENT_ERROR |
                       BLOCKED | TEST_DEFINITION_ERROR (INVALID) | INCONCLUSIVE (INCOMPLETE)
  └ BugInvestigator (FAILED only): retries; CONFIRMED only against REQUIREMENT / HTML_CONSTRAINT /
      OBSERVED expectations, otherwise UNVERIFIED_ANOMALY
Dashboard "Playwright code" ─ GET /api/runs/{run_id}/export  → PlaywrightTestExporter (same steps,
      same oracles via check(); credentials from origin-scoped env vars only)
```

Legacy paths retained (not used by the dashboard): `POST /api/inspect` (LangGraph planning agent),
`POST /api/run` (crawler + planning agent), `GET /api/benchmark*` (hand-written evaluator probes),
`app/test_generation/*` (contract-test generator used by older reports).

## 3. File-by-file summary

| File | Change |
|---|---|
| `app/intelligence/target_context.py` (new) | `TargetContext`, `ObservedInventory` (states, controls with option values/labels, forms with field constraints, transitions, auth, `seen_in_states`, `reached_by`), `canonical_origin`, legacy-report reconstruction with `LEGACY_REPORT` provenance, origin-mismatch rejection |
| `app/investigation_agent/models.py` | `InvestigationReport.report_schema_version`, `target_context`, `observed_inventory` |
| `app/investigation_agent/report_builder.py` | builds the inventory/context; login journey uses observed login form and a password placeholder |
| `app/investigation_agent/browser_session.py` | records select option labels |
| `app/test_planning/planner.py` (rewritten) | evidence-grounded strategies, no site defaults, diagnostics, sort-label parsing, rule-attributable feedback keywords, login-acceptance uncertainty, form-submission-only evidence excluded |
| `app/test_planning/field_constraints.py` (new) | HTML/requirement constraint extraction, EP/BVA partitions, unknown-constraint reporting, conservative requirement→field mapping |
| `app/test_planning/validator.py` (rewritten) | grounding, meaningful-oracle, intent and option checks; semantic de-duplication; rejection reasons |
| `app/intelligence/archetypes.py` | structural capability evidence with provenance/confidence, unknown capabilities, brand token removed |
| `app/knowledge/retriever_v2.py`, `schema.py`, `patterns/*.json`, `patterns/generic_strategies.json` (new) | capability-gated eligibility, rejection log, strategy/applicability/assumption fields, parameterized generic strategies, credential literals replaced by `${SAMPLE_TEXT}` |
| `app/schemas.py` | new oracles (FIELD_VALIDITY, ELEMENT_VALUE_LENGTH, VALIDATION_FEEDBACK_SHOWN, NO_VALIDATION_FEEDBACK, LIST_SORTED, LIST_ALL_CONTAIN, SELECTED_VALUE), `expected_source`, `is_meaningful_assertion`, `TestCase.investigation_id/target_origin/test_kind/spec_version`, `ExecutionResult.meaningful_assertions_evaluated` |
| `app/execution/executor.py` | action vs. assertion error separation, LOCATOR/AUTOMATION/ENVIRONMENT classes, NOT_EVALUABLE → INCOMPLETE, trivial-only → INCOMPLETE, real STATUS_CODE, CUSTOM never passes, collection detection for list oracles, SELECT settles navigation |
| `app/execution/runner.py` | SauceDemo auth injection removed; cross-origin guard; username/password placeholders resolved per origin; fixture credentials and bug-ID comment removed from legacy synthesis |
| `app/export/playwright_exporter.py` (rewritten) | exports runner steps + every assertion via `check()`, INVALID/BLOCKED/INCOMPLETE skips, no baked credentials, `export_semantics` |
| `app/main.py` | `GET /api/runs/{run_id}/export`; planning diagnostics persisted with the run |
| `app/static/src/*`, `app/static/app.js` | client-side script generators removed; script fetched from the server exporter; SauceDemo default target removed |
| `app/bugs/investigator.py` | executor outcome classes honoured; UNVERIFIED_ANOMALY for inferred expectations; legacy oracle heuristics skipped for grounded oracle classes |
| `app/investigation_agent/authenticator.py` | exact-origin credential scoping; env credentials require `VERITYQA_TEST_CREDENTIALS_ORIGIN` |
| `app/investigation_agent/governor.py` | action-semantics classes (irreversible / transaction commit / transaction entry), env-extensible (stricter only) |
| `app/investigation_agent/behavior_model.py`, `test_synthesis.py` | password placeholder rendered as env lookup; canonical synthesis with diagnostics; compact step parsing |
| `demo_app/main.py`, `demo_app/fixture2/main.py`, `demo_app/benchmark_app/main.py` | `VERITYQA_FIXTURE_MODE=baseline`; ground-truth pages gated by `VERITYQA_FIXTURE_DISCLOSE_BUGS` |
| `benchmarks/` (new) | targets (agent inputs only), evaluator-only ground truth, independent evaluator |
| `scripts/run_multi_website_benchmark.py` (new) | multi-target, multi-mode, repeatable benchmark incl. exported-script execution |
| tests | see §5 |

## 4. Benchmark results (measured, benchmark `mwb-20261024T162350Z-6e0e85`)

Source: `reports/multi_website_benchmark.{json,md}`. Code: commit `d0d2fdd` + uncommitted working tree,
**before** the late SauceDemo session fix (see §6). LLM: none (`groq_configured: false`, 0 LLM calls).

| Target | Mode | Tests | PASS | FAIL | ERROR | Detected (confirmed) | Anomaly only | Missed | Export agreement |
|---|---|---|---|---|---|---|---|---|---|
| Apex Demo Portal | buggy (×2) | 22 | 18 | 4 | 0 | BUG-01, BUG-02, BUG-04 | – | BUG-03 | 22/22 |
| Apex Demo Portal | baseline (×2) | 22 | 22 | 0 | 0 | – (0 false alarms) | – | – | 22/22 |
| TaskDash | buggy (×2) | 22 | 18 | 4 | 0 | BUG-F2-01 | BUG-F2-02 | BUG-F2-03 | 22/22 |
| TaskDash | baseline (×2) | 22 | 22 | 0 | 0 | – (0 false alarms) | – | – | 22/22 |
| Benchmark shop | buggy (×2) | 19 | 19 | 0 | 0 | – | – | BENCH-01, BENCH-02 | 19/19 |
| Benchmark shop | baseline (×2) | 19 | 19 | 0 | 0 | – | – | – | 19/19 |
| SauceDemo (public) | single (×2) | 26 | 6 / 7 | 3 / 3 | 17 / 16 | no ground truth | – | – | 25/26, 26/26 |
| TaskFlow | – | – | – | – | – | **unavailable** (not in repo, nothing on :8003) | | | |

Per repeat (buggy builds): 4 of 9 planted defects confirmed, 1 more as an unverified anomaly; 0 false
positives; 0 baseline false alarms; 0 cross-target selector leaks; all interaction selectors grounded.
The JSON `aggregate` (TP 8 / FN 10) **sums both repeats** — it is not 8 distinct defects.

## 5. Test results (measured)

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| Clean baseline, unmodified commit (detached worktree) | 534 | 91 | 6 |
| Full suite after changes (before the last fixes in §6) | 587 | 88 | 6 |
| Targeted re-run of changed areas (final code) | 115 | 0 | 0 |

No failure in the post-change run was absent from the baseline except two event-loop executor tests that
asserted a `body`-only check as PASSED; they were updated to a real oracle and pass. Five baseline
failures now pass. The remaining 86 failures are pre-existing (47 in the exported SauceDemo/ShopSphere
contract suites, which need a live public site or the ShopSphere app that is not in this repository;
others in LLM-provider, sandbox-governor and journey tests).

## 6. Known open issues

* SauceDemo: authenticated routes were planned without the login prelude (investigation never sets
  `is_authenticated`) → 16 LOCATOR_ERRORs and 3 route tests wrongly CONFIRMED. A fix (session
  reachability from the AUTHENTICATE transition) is implemented and unit-tested but **not yet verified live**.
* The full suite and benchmark were not re-run after that fix and the SELECT-navigation settle change.
* BUG-03 (password mismatch, cross-field rule), BUG-F2-03 (status not persisted) and the shop defects
  have no generic oracle yet; BUG-F2-02 is only an anomaly because its oracle is inferred.
* Legacy paths keep SauceDemo/fixture-specific strings (see `RESIDUAL_SITE_SPECIFIC_REFERENCES.md`).
