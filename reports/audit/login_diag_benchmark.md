# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T221127Z-8390b3`
- Timestamp: 2026-10-24T22:11:27.648333+00:00
- Code commit: `c58eb5ddcde0c1c92e96f1d8da6a2281852d5980` (working tree dirty: True)
- Configuration identity: `8fbebeb77613aec2`
- Suite version: `multi-website-benchmark/1.1`

## Target availability

| Target | Class | Status | Note |
|---|---|---|---|
| Harbor Events (demo site) | REPOSITORY_FIXTURE | AVAILABLE |  |

## Per-run results

| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-baseline-r1-178f60` | harbor_events | baseline | AUTHENTICATION | 8 | 11 | 72 | 35 | 35 | 0 | 0 | 0 | 0 | 0 | 0 | 171/171 | 35/35 | 34/35 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-baseline-r1-178f60` | harbor_events | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | - | - | 0 |

## Aggregate (reproduced findings = confirmed + likely, bug-enabled builds; sums over repeats)

- TP 0, FP 0, FN 0; precision undefined, recall undefined, F1 undefined
- Detected with confirmed-only findings: 0
- False alarms on defect-free (baseline) builds: 0 (confirmed-only: 0)
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 34/35

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-harbor_events-baseline-r1-178f60` | 63 | 4 | 3 | 1 | 0.750 | 0/7 | 6/7 | 1.000 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-harbor_events-baseline-r1-178f60` | 71.23 | 0.031 | 105.41 | 0.53 | 43.98 | 10 / 0 / 19.503 | 0 / 0 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
