# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T214916Z-b663bd`
- Timestamp: 2026-10-24T21:49:16.494312+00:00
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
| `bench-harbor_events-buggy-r1-afcca8` | harbor_events | buggy | AUTHENTICATION | 8 | 11 | 72 | 35 | 27 | 8 | 0 | 0 | 0 | 0 | 0 | 171/171 | 35/35 | 35/35 |
| `bench-harbor_events-baseline-r1-2f5aba` | harbor_events | baseline | AUTHENTICATION | 8 | 11 | 72 | 35 | 32 | 3 | 0 | 0 | 0 | 0 | 0 | 171/171 | 35/35 | 31/35 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-afcca8` | harbor_events | buggy | 7 | 6 | 0 | 1 | 1.000 | 0.857 | 0.923 | HE-01, HE-03, HE-04, HE-05, HE-06, HE-07 | HE-01, HE-03, HE-04, HE-05, HE-06 | HE-07 | HE-02 | - | undefined |
| `bench-harbor_events-baseline-r1-2f5aba` | harbor_events | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | - | - | 0 |

## Per-defect pipeline stages (bug-enabled builds)

| Run | Defect | Route discovered | Test generated | Test executed | Reproduced (by) | Class | Persisted & shown in UI data | Regression test | Missed at |
|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-afcca8` | HE-01 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-afcca8` | HE-02 | yes | yes | yes (FAILED) | no (-) | - | no | yes | reproduced |
| `bench-harbor_events-buggy-r1-afcca8` | HE-03 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-afcca8` | HE-04 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-afcca8` | HE-05 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-afcca8` | HE-06 | yes | yes | yes (FAILED) | yes (execution) | LIKELY_DEFECT | yes | yes | - |
| `bench-harbor_events-buggy-r1-afcca8` | HE-07 | yes | no | no () | yes (investigation) | CONFIRMED_DEFECT | yes | no | - |

## Aggregate (reproduced findings = confirmed + likely, bug-enabled builds; sums over repeats)

- TP 6, FP 0, FN 1; precision 1.000, recall 0.857, F1 0.923
- Detected with confirmed-only findings: 1
- False alarms on defect-free (baseline) builds: 0 (confirmed-only: 0)
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 66/70

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-afcca8` | 63 | 4 | 3 | 1 | 0.750 | 0/7 | 6/7 | 0.977 |
| `bench-harbor_events-baseline-r1-2f5aba` | 63 | 4 | 3 | 1 | 0.750 | 0/7 | 6/7 | 0.954 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-harbor_events-buggy-r1-afcca8` | 105.87 | 0.047 | 74.02 | 35.7 | 39.57 | 10 / 2 / 25.559 | 0 / 0 / - |
| `bench-harbor_events-baseline-r1-2f5aba` | 87.6 | 0.051 | 103.79 | 20.29 | 51.47 | 10 / 10 / 24.647 | 0 / 0 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
