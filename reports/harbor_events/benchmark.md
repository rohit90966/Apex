# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T205802Z-8655c2`
- Timestamp: 2026-10-24T20:58:02.025078+00:00
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
| `bench-harbor_events-buggy-r1-c70eb0` | harbor_events | buggy | AUTHENTICATION | 8 | 11 | 72 | 35 | 29 | 6 | 0 | 0 | 0 | 0 | 0 | 199/199 | 35/35 | 32/35 |
| `bench-harbor_events-baseline-r1-ae06f5` | harbor_events | baseline | AUTHENTICATION | 8 | 11 | 72 | 35 | 34 | 1 | 0 | 0 | 0 | 0 | 0 | 199/199 | 35/35 | 35/35 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-c70eb0` | harbor_events | buggy | 7 | 1 | 0 | 6 | 1.000 | 0.143 | 0.250 | HE-07 | - | HE-07 | HE-01, HE-02, HE-03, HE-04, HE-05, HE-06 | HE-03, HE-04 | undefined |
| `bench-harbor_events-baseline-r1-ae06f5` | harbor_events | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | - | - | 0 |

## Per-defect pipeline stages (bug-enabled builds)

| Run | Defect | Route discovered | Test generated | Test executed | Reproduced (by) | Class | Persisted & shown in UI data | Regression test | Missed at |
|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-c70eb0` | HE-01 | yes | yes | yes (FAILED) | no (-) | - | no | yes | reproduced |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-02 | yes | yes | yes (FAILED, PASSED) | no (-) | - | no | yes | reproduced |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-03 | yes | yes | yes (FAILED) | no (-) | - | no | yes | reproduced |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-04 | yes | yes | yes (FAILED) | no (-) | - | no | yes | reproduced |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-05 | yes | no | no () | no (-) | - | no | no | test_generated |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-06 | yes | no | no () | no (-) | - | no | no | test_generated |
| `bench-harbor_events-buggy-r1-c70eb0` | HE-07 | yes | no | no () | yes (investigation) | CONFIRMED_DEFECT | yes | no | - |

## Aggregate (reproduced findings = confirmed + likely, bug-enabled builds; sums over repeats)

- TP 1, FP 0, FN 6; precision 1.000, recall 0.143, F1 0.250
- Detected with confirmed-only findings: 1
- False alarms on defect-free (baseline) builds: 0 (confirmed-only: 0)
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 67/70

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-c70eb0` | 45 | 2 | 3 | 1 | 0.750 | 2/7 | 6/7 | 1.000 |
| `bench-harbor_events-baseline-r1-ae06f5` | 45 | 2 | 3 | 1 | 0.750 | 2/7 | 6/7 | 1.000 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-c70eb0` | 88.62 | 0.041 | 67.77 | 32.05 | 37.91 | 9 / 0 / 19.254 | 0 / 0 / - |
| `bench-harbor_events-baseline-r1-ae06f5` | 58.43 | 0.018 | 69.27 | 4.39 | 33.27 | 9 / 0 / 17.326 | 0 / 0 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
