# Multi-Website Generalization Audit (Phase Zero)

Audited checkout: branch `main`, commit `d0d2fdd` ("Test case quantity improved"), clean working tree.
Date: 2026-10-24.

This is the source-level diagnosis written **before** any refactoring. Each issue lists the source
location, how to reproduce it, severity, and the fix that was then implemented (see
`docs/MULTI_WEBSITE_GENERALIZATION_REPORT.md` for the outcome).

## 0. Environment facts that constrain the task

| Fact | Evidence |
|---|---|
| **TaskFlow (fixture 3, `http://127.0.0.1:8003/`) does not exist in this repository or on this machine.** No `demo_app/fixture3`, no `BENCHMARK_GROUND_TRUTH.md`, nothing listening on port 8003, no TaskFlow directory found under `D:\` or the user profile. | `git ls-files`, port scan, disk search |
| No persisted TaskFlow run exists. `data/runs/` holds one run (`inv-33407727e3b2`, SauceDemo, `failed: TargetClosedError`). | `data/runs/*/run.json` |
| Existing local fixtures: **Apex Demo Portal** (`demo_app/main.py`, port 8001, 4 planted bugs), **TaskDash** (`demo_app/fixture2/main.py`, port 8002, 3 planted bugs), **Intelligence benchmark shop** (`demo_app/benchmark_app/main.py`, 2 seeded bugs, tax bug toggle). | source |
| `benchmark_private/ground_truth.json` describes *ShopSphere*, whose application is not in this repository. | file content |
| The esbuild toolchain referenced by `scripts/build_frontend.js` (`shopsphere/frontend/node_modules`) is missing; `app/static/app.js` is a committed, unminified bundle. | `npx esbuild` fails |

Consequently the TaskFlow symptoms were **traced statically** through the canonical code path. Every
observed artefact has a deterministic source in the planner, runner or UI, independent of TaskFlow's
actual DOM, so the diagnosis does not depend on reproducing TaskFlow itself. Regression tests
reproduce each root cause with a synthetic non-SauceDemo investigation report.

## 1. Canonical dashboard path (as found)

```
UI (runEvents.mjs)  POST /api/investigate/stream           app/main.py:476
  -> run_stateful_investigation()                          app/investigation_agent/__init__.py:88
  -> InvestigationReportBuilder.build_report()             app/investigation_agent/report_builder.py:70
  -> _finalize_run -> _build_investigation_payload         app/main.py:338
       -> synthesize_test_cases_from_investigation()       app/investigation_agent/test_synthesis.py:74
            -> KnowledgeGuidedTestPlanner.plan_test_suite_sync()   app/test_planning/planner.py:196
                 ArchetypeClassifier -> KnowledgePatternRetriever -> 5 synthesizers -> GroundedTestValidator
  -> RunStore.complete (persists test cases)              app/runs/store.py
UI "Execute"  POST /api/tests/execute {run_id}             app/main.py:762
  -> QAPlanRunner.run_all -> run_test                      app/execution/runner.py:644
       synthesize_steps -> _ensure_authenticated_session -> _resolve_credential_placeholders
  -> BrowserExecutor.execute_steps                         app/execution/executor.py:44
  -> BugInvestigator (FAILED only)                         app/bugs/investigator.py
UI "Playwright code" panel                                 app/static/src/components/GeneratedTests.jsx:246
  -> generatePlaywrightScript()  (client-side, NOT the server exporter)
```

Other test-generating paths: `POST /api/inspect` (TestPlanningAgent / LangGraph `app/agents/*`),
`POST /api/run` (crawler + TestPlanningAgent + AdaptiveTestGenerator), `GET /api/benchmark*`
(hand-written benchmark steps), `app/test_generation/contract_test_generator.py` (+ its own exporter
`app/test_generation/playwright_exporter.py`). These are legacy/auxiliary; the dashboard uses only the
path above.

## 2. Root causes of the observed TaskFlow artefacts

| # | Observed artefact | Root cause (source) | Reproduction | Severity |
|---|---|---|---|---|
| R1 | `/inventory.html` in TaskFlow tests | `planner.py:705-717` `make_transition_steps` navigates to `{target}/inventory.html` whenever no auth prelude exists; `planner.py:516` defaults URL assertions to `"inventory"`; `planner.py:587,633` inject `/inventory.html` navigations. | Any report with a select whose selector contains `sort` and no login form. | Critical |
| R2 | `#task-sort-select` with options `za`, `lohi`, `hilo` | `planner.py:719-751` emits three sort tests with SauceDemo option values for **any** selector containing `sort` (or any target classified with `PAGINATION_SORTING`), regardless of the options observed on the control; default selector `[data-test='product-sort-container']`. Assertion is only `body` visible, so the order is never checked. | Report with `#task-sort-select` whose observed options are e.g. `due`,`priority`. | Critical |
| R3 | `#user-name`, `#password`, `#login-button`, `standard_user`, empty-username/password tests | `planner.py:1101-1206` `_synthesize_form_validations` **unconditionally** emits two login tests with SauceDemo fallback selectors, `secret_sauce` and `standard_user`, even when no login form was observed. `planner.py:369-439` auth prelude falls back to `standard_user` + SauceDemo selectors and asserts `URL contains "inventory"`; it is triggered by `primary_archetype == ECOMMERCE` or any selector containing `submit`/`email`. | Report with no password field at all -> 2 login tests. | Critical |
| R4 | `#new-project`, `#project-name`, `#save-btn` | `planner.py:1666-1679` `_resolve_selector` returns `hints[0]` from the knowledge pattern when no observed control matches, so pattern selector hints (`app/knowledge/patterns/saas_crud.json`) become test selectors on any `SAAS_CRUD`-classified target. `retriever_v2.py:56-61` admits every AUTHENTICATION/GENERIC pattern for every target. | Report classified SAAS_CRUD without those ids. | Critical |
| R5 | 3 BLOCKED results | `runner.py:559-642` `_ensure_authenticated_session` injects a SauceDemo login prelude into any test whose selectors contain `sort`, `continue`, `checkout`, etc. or that navigates to `cart`; the placeholder password then cannot be resolved for `127.0.0.1:8003` -> BLOCKED. | Run a sort test on a host without registry credentials. | High |
| R6 | Exported code with `print` and no assertions; empty-field tests fill non-empty values | The dashboard script is produced client-side by `GeneratedTests.jsx:246-307`: it emits no assertions, prints `[PASS]` unconditionally, replaces `""` with `"test"` (`s.input_value \|\| s.value \|\| 'test'`), hardcodes `secret_sauce`, and defaults selects to `za`. The server exporter (`app/export/playwright_exporter.py`) is never used by the dashboard; it also lacks `import os`, re-injects the runner's SauceDemo prelude and bakes the password into the file. | Open "Generated Tests" for any run. | Critical |
| R7 | "2 passed" without meaningful verification | Planner pads every test with `ELEMENT_VISIBLE body` assertions (`planner.py:604-609, 650-655, 1063-1068`); executor counts them as satisfied assertions; `CUSTOM` assertions always pass (`executor.py:646`). | Any synthesized transition test. | High |
| R8 | "Unverified locator-related leads: 3" | `executor.py:160-162` maps every `PlaywrightError` (including selector timeouts) to `FAILED`, which `/api/tests/execute` forwards to `BugInvestigator` as a product failure. | Execute a test with a non-existent selector. | High |

## 3. Additional cross-target contamination and integrity defects

| # | Issue | Location | Severity | Fix |
|---|---|---|---|---|
| C1 | Environment credentials (`TARGET_USERNAME/PASSWORD`, `VERITYQA_TEST_*`) are returned for **every** host; demo registry matches by substring (`"127.0.0.1:8001" in url`). Credentials can flow to unrelated targets. | `authenticator.py:100-126` | High | Exact-origin scoping; env credentials require `VERITYQA_TEST_CREDENTIALS_ORIGIN`. |
| C2 | Runner legacy synthesis hardcodes fixture-1 credentials `admin@apex.com`/`Secret123!`, a `"a"` password "to expose BUG-01", `standard_user`, `/inventory` success URL, `Microservices` search query, `Account Created`, `Passwords do not match`, `Please enter a search term` expectations. Ground-truth knowledge inside the agent. | `runner.py:229-556` | High | Replace with scoped credential placeholders; expectations only from requirements/observations. |
| C3 | `/api/benchmark` hand-writes the BUG-01 reproduction (1-char password via JS) inside the product API. | `main.py:958-1131` | Medium | Documented as evaluator-side legacy endpoint; new benchmark harness does not use it. |
| C4 | Ground truth exposed on the agent-observable surface: TaskDash `/about` lists the planted defects; Apex fixture `/api/bugs` returns the manifest. | `fixture2/main.py:377-381`, `demo_app/main.py:242` | High (benchmark validity) | Gated behind `VERITYQA_FIXTURE_DISCLOSE_BUGS=1`. |
| C5 | No baseline (defect-free) mode for fixtures 1 and 2, so false-alarm rates cannot be measured. | demo apps | Medium | `VERITYQA_FIXTURE_MODE=baseline`. |
| C6 | Archetype classifier contains SauceDemo brand text (`"sauce labs"`) and substring matching (`"za"` in any token, `"new"` in any token => CRUD), capability presence without provenance/confidence. | `archetypes.py:108, 423-464` | Medium | Token-based evidence with provenance and confidence; unknown capabilities represented. |
| C7 | Validator accepts selectors, routes and literal expectations never observed on the target; dedup only by exact signature; first-step rule only. | `validator.py` | High | Evidence-grounding validator (selectors, origins, routes, expected texts, meaningful oracles). |
| C8 | Empty-field tests depend on the step description containing "blank"/"empty" to set `""` (`planner.py:957-964`). | planner | Medium | Field-constraint engine produces explicit input classes. |
| C9 | Precision/recall reported as `0.0` when undefined. | `benchmark/manifest.py:98-100` | Low | New evaluator reports `null` (undefined). |
| C10 | Dashboard defaults target to `https://www.saucedemo.com/` when a run has no URL. | `GeneratedTests.jsx:13` | Low | Removed. |
| C11 | Governor keys checkout completion on the literal text `finish`/`checkout`. | `governor.py:33-43` | Medium | Kept (it only *restricts*), documented as residual keyword policy. |
| C12 | `investigation stale-evidence`: the planner consumes only the report object of the current run (no cross-run cache), and `RunStore` keys runs by `run_id`; the knowledge repository is a process singleton but read-only. No evidence of cross-run DOM reuse was found; contamination is from **hardcoded defaults**, not from caches. | `runs/store.py`, `knowledge/repository.py` | Info | Invariant tests added (order-independence). |

## 4. Assertion/oracle accounting

* `ExecutionResult.assertions_evaluated` is counted at execution time (good), but trivially-true
  assertions (`body` visible) and `CUSTOM` inflate it.
* `PASS` was possible with only trivial assertions.
* Planned assertions are not re-used for totals (good).

## 5. Plan (implemented in this order)

1. Target context + observed inventory in the investigation report (versioned).
2. Evidence-grounded planner rewrite (no hardcoded routes/selectors/credentials/options).
3. Grounding validator + capability-gated, selector-grounded knowledge retrieval.
4. Field-constraint boundary engine (HTML constraints and explicit requirements).
5. Executor outcome classification (LOCATOR/ENVIRONMENT/INCOMPLETE/INVALID), meaningful-assertion
   accounting, new assertion types (list ordering, value length, validation feedback).
6. Server-side exporter used by the dashboard; semantic parity tests; exported scripts executed.
7. Fixture baseline modes, ground-truth isolation, multi-website benchmark harness + evaluator.
