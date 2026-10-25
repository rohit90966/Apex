# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T230425Z-efa591`
- Timestamp: 2026-10-24T23:04:25.985201+00:00
- Code commit: `c58eb5ddcde0c1c92e96f1d8da6a2281852d5980` (working tree dirty: True)
- Configuration identity: `60824317684b12e1`
- Suite version: `multi-website-benchmark/1.1`

## Target availability

| Target | Class | Status | Note |
|---|---|---|---|
| Harbor Events (demo site) | REPOSITORY_FIXTURE | AVAILABLE |  |

## Per-run results

| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-866b6a` | harbor_events | buggy | AUTHENTICATION | 10 | 14 | 90 | 35 | 25 | 9 | 0 | 0 | 0 | 1 | 0 | 146/146 | 35/35 | 35/35 |
| `bench-harbor_events-baseline-r1-5573eb` | harbor_events | baseline | AUTHENTICATION | 10 | 14 | 90 | 35 | 34 | 0 | 0 | 0 | 0 | 1 | 0 | 145/145 | 35/35 | 34/35 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-866b6a` | harbor_events | buggy | 7 | 7 | 0 | 0 | 1.000 | 1.000 | 1.000 | HE-01, HE-02, HE-03, HE-04, HE-05, HE-06, HE-07 | HE-01, HE-02, HE-03, HE-04, HE-05, HE-06, HE-07 | HE-07 | - | - | undefined |
| `bench-harbor_events-baseline-r1-5573eb` | harbor_events | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | - | - | 0 |

## Per-defect pipeline stages (bug-enabled builds)

| Run | Defect | Route discovered | Test generated | Test executed | Reproduced (by) | Class | Persisted & shown in UI data | Regression test | Missed at |
|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-866b6a` | HE-01 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-02 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-03 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-04 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-05 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-06 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-866b6a` | HE-07 | yes | yes | yes (FAILED, PASSED) | yes (execution, investigation) | CONFIRMED_DEFECT | yes | yes | - |

## Aggregate (reproduced findings = confirmed + likely, bug-enabled builds; sums over repeats)

- TP 7, FP 0, FN 0; precision 1.000, recall 1.000, F1 1.000
- Detected with confirmed-only findings: 1
- False alarms on defect-free (baseline) builds: 0 (confirmed-only: 0)
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 69/70

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-866b6a` | 66 | 4 | 3 | 1 | 0.750 | 0/7 | 5/7 | 0.593 |
| `bench-harbor_events-baseline-r1-5573eb` | 65 | 4 | 3 | 1 | 0.750 | 0/7 | 5/7 | 0.634 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-866b6a` | 116.31 | 0.127 | 122.22 | 68.43 | 46.72 | 10 / 0 / 19.265 | 0 / 0 / - |
| `bench-harbor_events-baseline-r1-5573eb` | 75.33 | 0.017 | 68.81 | 0.2 | 40.63 | 10 / 0 / 19.359 | 0 / 0 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
