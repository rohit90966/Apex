# Benchmark comparison — validation round

Harness: `scripts/run_multi_website_benchmark.py`, default settings (`--max-actions 30 --max-seconds 150`,
35-case cap, 2 confirmation retries, LLM proposals off, memory off), one repeat per mode.
At the user's request only **Harbor Events** and **Apex** were run in this round (TaskDash and Benchmark Shop
were stopped before starting). The harness summary JSON is written only at the end, so these results are read
from the persisted run store: Harbor buggy `inv-699ea5748c2c`, Harbor clean `inv-22c931241178`, Apex buggy
`inv-829b51a385d2`, Apex clean `inv-fc1b3a6140c6`.

## Verified facts

### Defect presence (independent of VerityQA)
`reports/audit/verify_harbor_defects.py` → `reports/audit/defect_verification.json`: all 7 Harbor defects present
with bugs on (inputs and observed outputs recorded) and absent on the clean build (rejection messages / correct
order / correct status / `$2.25` fee).

### Harbor Events

| | Original baseline | After first fixes | This round |
|---|---|---|---|
| Planted defects surfaced (confirmed + likely) | 1/7 | 6/7 | **7/7** |
| Confirmed (established expectation) | 1/7 (HE-07) | 1/7 | 1/7 (HE-07, 4 pages) |
| Likely | 0/7 | 5/7 | 6/7 (HE-01..06, each reproduced 3/3 with fresh data) |
| Inconclusive planted defects | 2/7 | 1/7 (HE-02) | 0/7 |
| Missed, no test generated | 2/7 | 0/7 | 0/7 |
| Clean build: defect reports (false positives) | 0 | 0 | **0** |
| Clean build: tests passing | 34/35 | 32/35 (3 login-prelude failures → inconclusive) | **35/35** |
| Setup (login prelude) failures, buggy + clean | not measured | 4 attempts | 0/70 executions (+0/71 in a separate diagnostic clean run) |

### Apex (legacy fixture, unchanged since the original run)

| | Before (session start, `reports/qa_upgrade`) | This round |
|---|---|---|
| Tests (buggy) | 31: 24 pass / 7 fail | 31: 24 pass / 7 fail |
| Planted defects surfaced | 4/4 (BUG-01, 02, 04 confirmed; BUG-03 likely) | 4/4 (same classes; BUG-03 likely 3/3 + investigation lead) |
| Clean build: defect reports | **1** (password-mismatch lead — the clean build rejects it) | **0** |
| Clean build: tests | 31/31 pass | 30: 29 pass / 1 INCONCLUSIVE (TC-FIELD-04, see unresolved) |

The Apex false alarm was removed by the perception fix (`.error` containers recognised as validation feedback),
not by any Apex-specific change.

## Changes actually tested in this round
* RC-10 setup semantics (prelude steps marked `setup`; setup failure → BLOCKED with `setup_failure` evidence;
  setup fills verified; BLOCKED and infrastructure retries recorded but not counted; infra retries no longer
  overwrite a failure that reached its oracle) — regression tests + this benchmark.
* All earlier fixes RC-1…RC-9 — exercised again by this benchmark.
* Not exercised by this benchmark (edited after it started): the tightened convention-level sort fallback and the
  extended non-self-changing command words. Covered by unit tests only; Harbor/Apex pages have column headers and
  no such commands, so the outcome there is unaffected.

## Hypotheses / unresolved
* **Intermittent login prelude failure** (earlier run): screenshots show the password field empty at submit
  (browser required-field block) in one case and a server rejection ("Invalid email or password") with the correct
  email in another. Not reproduced in: 120 isolated prelude runs (3 parallel loops), 2 full suite replays through
  the production runner (70 tests, server-side request log), 2 full clean-build benchmarks (141 logged logins, all
  correct). Root cause **not established**. Mitigation that does not mask it: setup fills are now verified, and any
  recurrence is classified SETUP_FAILED/BLOCKED with the step, URL, screenshot and lengths recorded.
* **Apex TC-FIELD-04** (whitespace-only username on the clean build): first run saw unrelated feedback
  ("Invalid credentials"), both retries saw attributable feedback. Correctly INCONCLUSIVE; cause not investigated.
* TaskDash and Benchmark Shop: **not re-run** in this round (stopped at the user's request).
* Legacy test files importing `demo_app`: import path fixed (`tests/conftest.py` adds `archive/legacy_demo_sites`);
  the 12 files were not run to completion (an earlier attempt exceeded 25 minutes with a port collision caused by
  running suites in parallel). Status **unverified**.

## Comparability notes
* Same harness and settings throughout; single repeat per mode, so per-defect stability beyond 3 attempts per test
  is not measured.
* "Before" for Harbor is the original Harbor run; "before" for Apex is the run at the start of this session
  (before the perception/reproducer fixes), the only earlier Apex run with the same settings.
* Recall counts a defect once whether found by an executed test or an investigation lead.
