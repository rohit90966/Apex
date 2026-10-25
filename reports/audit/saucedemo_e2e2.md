# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261025T015142Z-eb03c5`
- Timestamp: 2026-10-25T01:51:42.487398+00:00
- Code commit: `5de38db371587bd9390ed4944313edfba429186f` (working tree dirty: True)
- Configuration identity: `81d9f46d81f720bb`
- Suite version: `multi-website-benchmark/1.1`

## Target availability

| Target | Class | Status | Note |
|---|---|---|---|
| SauceDemo (public demo) | PUBLIC_INDEPENDENT | AVAILABLE | Public target; no ground truth, single mode |

## Per-run results

| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-saucedemo-public-r1-5d7bea` | saucedemo | public | ECOMMERCE | 5 | 11 | 63 | 31 | 31 | 0 | 0 | 0 | 0 | 0 | 0 | 49/49 | 31/31 | 31/31 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected (unique) | By executed tests | By investigation | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-saucedemo-public-r1-5d7bea` | saucedemo | public | n/a | | | | | | | | | | No ground truth for this target; detection metrics undefined. |

## Aggregate (reproduced findings = confirmed + likely, bug-enabled builds; sums over repeats)

- TP 0, FP 0, FN 0; precision undefined, recall undefined, F1 undefined
- Detected with confirmed-only findings: 0
- False alarms on defect-free (baseline) builds: 0 (confirmed-only: 0)
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 31/31

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-saucedemo-public-r1-5d7bea` | 33 | 2 | 4 | 3 | 0.750 | 2/2 | 0/0 | 0.915 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-saucedemo-public-r1-5d7bea` | 65.9 | 0.023 | 113.88 | 0.16 | 31.8 | 7 / 0 / 12.643 | 0 / 0 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
