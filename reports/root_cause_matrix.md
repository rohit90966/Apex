# Root-cause matrix — Harbor Events audit

Baseline run analysed: `reports/harbor_events/benchmark.json` (persisted runs `inv-7b840e16b3b8` buggy,
`inv-c549a289d070` clean), commit `c58eb5d` plus the uncommitted working tree recorded in
`reports/architecture_audit.md` §1. Every row below was checked against the persisted report/inventory and the
code path that produced it. Confidence: **Confirmed** = mechanism shown in code *and* reproduced (offline
re-plan of the persisted report, or a controlled experiment / regression test); **Probable** = code evidence
only; **Hypothesis** = needs an experiment.

Defect presence was verified independently of VerityQA (HTTP requests against both modes, see audit §3):
all seven defects are present with bugs on and absent in the clean build.

## Planted defects

| Defect | Discovered? | Test generated? | Prereqs | Executed correctly? | Oracle independent? | Result (baseline) | Responsible component | Root cause | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| HE-01 server accepts malformed email on registration | yes (`/register`, `#email`) | yes (TC-SRV-05) | none | 1st run yes; retries **no** | yes (HTML `type=email`, server layer) | INCONCLUSIVE (1/3 failed) | test data (`field_constraints.valid_filler`, fixed values) + retry semantics (`bugs/investigator.py`) | RC-3: every attempt registered the same identity. Attempt 1 created the account (the defect); attempts 2–3 hit "account already exists", which is feedback mentioning *email*, so the attributable-feedback oracle passed. RC-4: the disagreement was resolved by majority, so a reproduced failure became INCONCLUSIVE. | Confirmed |
| HE-02 password confirmation mismatch accepted | yes | yes (TC-XF-01) | none | 1st run yes; retries no | convention (confirmation pair) | INCONCLUSIVE (1/3) | same as HE-01 | RC-3 + RC-4 (the default filler email is shared by every registration test in the suite, so even the first run could collide with an earlier test). | Confirmed |
| HE-03 price sort compares formatted text | yes (`#sort`) | yes (TC-SORT-02/03) | none | yes | **no**: key and direction both read from the option label; values read from the whole row text | UNVERIFIED_ANOMALY | `test_planning/planner._sort_cases`, `execution/executor` LIST_SORTED | RC-6: the only oracle was label-derived (`DERIVED_INFERENCE`), which the lifecycle correctly caps at "anomaly". A label-independent check (the order is not monotonic in the column the option names) did not exist. | Confirmed |
| HE-04 featured events bypass the category filter | yes (`#category`) | yes (TC-FILTER-02) | none | yes | **no**: "row text contains the option label" | UNVERIFIED_ANOMALY | `planner._filter_cases`, executor LIST_ALL_CONTAIN | RC-6: substring match on the whole record (a title containing the word would pass; the check cannot tell which field the filter ranges over), labelled inference. | Confirmed |
| HE-05 ticket quantity range not enforced by server | yes — booking form observed on `/events/1..3` (logged in) | **no** (0 candidates) | login (available) | – | – | missed | `intelligence/target_context.navigable_routes` used by every form generator | RC-1: routes containing an identifier (`/events/1`) were excluded from planning, so a form that only exists on item pages could never be tested. Also: server-enforcement tests had no numeric-range rule (only required/length/pattern/email). RC-8: once item pages were planned (offline re-plan), the 35-case cap dropped 9 of 10 booking-form cases because candidates were ordered form-by-form. | Confirmed (offline re-plan: 0 → 12 candidates, 1 → 5 selected) |
| HE-06 cancel shows success but booking unchanged | yes — `#cancel-booking-1/2` and the POST `/bookings/1/cancel` form observed | **no** | login | – | – | missed | planner (no strategy), investigation budget | RC-7: no strategy verified the effect of a per-record command; transitions on the same page with the same headings were skipped. RC-2: investigation never clicked Cancel because the "mutating action" budget (15) had been consumed by plain navigations (budget counted any significant DOM diff). | Confirmed |
| HE-07 price breakdown renders `$NaN` | yes | via investigation lead | none | yes (reproducer) | yes (generic rendering invariant) | **CONFIRMED_DEFECT** | — | Worked after the earlier reproducer fix (navigate to the lead's page after setup). | Confirmed |

## Clean-build failure

| Observation | Responsible component | Root cause | Confidence |
|---|---|---|---|
| TC-FILTER-01 "Filter = 'All categories' shows only matching records" failed on the clean build | `investigation_agent/browser_session.py` and `perception.py` option capture | RC-5: options were captured as `o.value \|\| o.text`, so the reset option `<option value="">All categories</option>` was recorded as the value "All categories"; the planner then tested the reset option as a filter value. Verified in the persisted inventory (`#category` options start with "All categories"). | Confirmed (regression test `test_placeholder_option_is_not_recorded_as_a_value`) |

## Cross-cutting causes

| ID | Broken contract | Evidence | Fix |
|---|---|---|---|
| RC-1 | "Routes a test can reach" ≠ "routes without identifiers" | `navigable_routes()` dropped `is_instance_route`; booking form produced 0 candidates | `ObservedInventory.linked_instance_routes()` / `form_routes()`: instance pages that were reached by navigation **and linked from a stable page**, one representative per route template; route-reachability tests still exclude instances |
| RC-2 | Mutation budget = side effects on the application | stop reason "Max mutating action budget reached (15/15)" after 15 transitions, all navigation | `workflow.probe_sent_state_changing_request`: only probes that sent a non-GET/HEAD/OPTIONS request count; DOM-only change still counts as *productive* |
| RC-3 | A repeated execution must re-test the same precondition | attempts 2–3 of TC-SRV-05/TC-XF-01 passed on "already exists" | `app/test_data.py`: identity fillers carry `${RUN_UNIQUE}`; runner resolves a fresh, length-preserving token per attempt; exporter does the same at run time; namespace recorded as evidence |
| RC-4 | Retry outcome is evidence only when the oracle ran on the same failure | ERROR/INCONCLUSIVE retries counted in the denominator; failure at another step counted as reproduction | `BugInvestigator`: only PASSED/FAILED retries count; FAILED counts as reproduction only at the same step; every attempt is recorded in `BugReport.attempts` with a `verdict_rationale` |
| RC-5 | `<option value="">` is a placeholder, not a value | persisted inventory | capture `option.value` (the DOM already falls back to text when no value attribute exists); drop empty values |
| RC-6 | An oracle must not rely on the label for its whole expectation | sort/filter verdicts were all "anomaly" | `execution/list_oracles.py` shared by executor and exporter: sort reads the column whose header the option names and checks monotonicity (convention → likely) separately from direction (label → anomaly); filter compares each record's own field drawn from the filter's option domain, otherwise not evaluable |
| RC-7 | A per-record command must change its record | no strategy existed; investigation did not exercise Cancel | `TS-RECORD-COMMAND` (knowledge base) → `planner._record_command_cases` → `RECORD_CHANGED` oracle (executor + exporter); gated by the safety governor |
| RC-8 | A size cap must keep coverage diverse | 9/10 booking-form cases dropped in re-plan | round-robin inside each strategy bucket over page templates (`_interleave_by_subject`) |
| RC-9 | Same-heading transitions and their prerequisites | shop "Calculate Tax"/"Remove" produced no tests; replaying from the route alone cannot reach a state created by a click | same-page transitions get the generic rendering invariant; `_replayable_path` replays the recorded click path (plain clicks only) to the source state |

## Not fixed / unresolved

| Item | Status |
|---|---|
| Counter/badge consistency (legacy shop BUG-BENCH-02) | Not automated: needs a cross-element invariant (badge equals number of items). Documented in the knowledge base as not automated. |
| `/api/benchmark` and `/api/benchmark/fixture2` contain a hand-written BUG-01 test and import a planted-bug manifest from `app/benchmark/manifest.py`; the dashboard calls them with POST while they are registered as GET | Not on the investigation/planning/execution path; documented as a P1 remediation (move to `benchmarks/`, remove answer-specific logic). Not changed: removing an endpoint the dashboard exposes is a product decision. |
| `qa_knowledge_integration.py`, `probes/accessibility.py` | Disconnected (no production import). Wiring script-less agenda questions would add no probes; not wired. |
| Investigation-side exploration of item pages and record commands depends on the frontier choosing them within the probe budget | Hypothesis: with RC-2 fixed, the frontier reaches them; measured in `reports/benchmark_comparison.md`. |

## Validation round (follow-up audit)

| ID | Finding | Evidence | Status |
|---|---|---|---|
| RC-10 | A prerequisite (login prelude) failure was scored as a failure of the behaviour under test; infra errors on a retry overwrote a failure that had reached its oracle | Screenshots of the failing attempts (`artifacts/step_TC-XF-01_4_1792878740.png`: password empty, browser required-field block; `step_TC-FILTER-01_4_1792878912.png`: "Invalid email or password") | **Fixed & tested**: `ActionStep.setup`; executor → BLOCKED + `setup_failure` evidence; setup fills verified; investigator `SETUP_FAILED` verdict, BLOCKED/infra retries recorded not counted. Tests: `test_setup_*`, `test_blocked_initial_result_*`, `test_setup_failed_and_infrastructure_retries_*` |
| RC-10a | Why the prelude lost/garbled the password | not reproduced in 120 isolated runs, 70 replayed tests, 2 benchmarks (141 logins logged server-side) | **Unresolved hypothesis** (load-related input loss in the browser). Now detectable at the failing step. |
| RC-11 | Convention-level sort check fell back to "first number / title in the row" when no column matched | code review | **Fixed (unit-tested only)**: requires a named column or exactly one amount per record, else not evaluable |
| RC-12 | Per-record commands whose effect is elsewhere (resend, duplicate, sync…) would be misreported by RECORD_CHANGED | code review | **Fixed (unit-tested only)**: excluded from `has_record_command` |

Evidence requirements by class (unchanged, verified in code): CONFIRMED needs a failing oracle against an
established expectation (REQUIREMENT, browser-layer HTML constraint, OBSERVED) reproduced in every evaluated
attempt; LIKELY needs the same reproduction against a strong but non-established source (HTML_DECLARED server
layer, SEMANTIC_CONVENTION, GENERIC_INVARIANT, ACCESSIBILITY_HEURISTIC, investigation convention leads);
anything inferred from labels alone stays UNVERIFIED_ANOMALY; disagreement between evaluated attempts is
INCONCLUSIVE; attempts that never reached the oracle are recorded and excluded.
