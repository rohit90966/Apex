# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T174324Z-2d4ee4`
- Timestamp: 2026-10-24T17:43:24.116257+00:00
- Code commit: `d0d2fdd3cea7fe1341034ec3242fc78db9ec1751` (working tree dirty: True)
- Configuration identity: `24cfdbc6a55dcd51`
- Suite version: `multi-website-benchmark/1.0`

## Target availability

| Target | Class | Status | Note |
|---|---|---|---|
| Apex Demo Portal (fixture 1) | REPOSITORY_FIXTURE | AVAILABLE |  |
| TaskDash (fixture 2) | REPOSITORY_FIXTURE | AVAILABLE |  |
| Intelligence benchmark shop (controlled synthetic e-commerce) | CONTROLLED_SYNTHETIC | AVAILABLE |  |

## Per-run results

| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-7e455f` | apex_portal | buggy | AUTHENTICATION | 6 | 7 | 30 | 22 | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-apex_portal-baseline-r1-07ee6a` | apex_portal | baseline | AUTHENTICATION | 6 | 7 | 30 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-taskdash-buggy-r1-8b2296` | taskdash | buggy | GENERIC_WEB | 7 | 7 | 51 | 27 | 21 | 5 | 1 | 0 | 0 | 0 | 1 | 49/49 | 27/27 | 27/27 |
| `bench-taskdash-baseline-r1-183e07` | taskdash | baseline | GENERIC_WEB | 7 | 7 | 51 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 37/37 | 22/22 | 22/22 |
| `bench-benchmark_shop-buggy-r1-2c0297` | benchmark_shop | buggy | ECOMMERCE | 6 | 11 | 43 | 27 | 23 | 3 | 1 | 0 | 0 | 0 | 1 | 93/93 | 27/27 | 26/27 |
| `bench-benchmark_shop-baseline-r1-df00a2` | benchmark_shop | baseline | ECOMMERCE | 6 | 11 | 43 | 25 | 19 | 1 | 5 | 0 | 0 | 0 | 5 | 88/88 | 25/25 | 25/25 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-7e455f` | apex_portal | buggy | 4 | 3 | 0 | 1 | 1.000 | 0.750 | 0.857 | BUG-01, BUG-02, BUG-04 | BUG-03 | - | undefined |
| `bench-apex_portal-baseline-r1-07ee6a` | apex_portal | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-taskdash-buggy-r1-8b2296` | taskdash | buggy | 3 | 1 | 0 | 2 | 1.000 | 0.333 | 0.500 | BUG-F2-01 | BUG-F2-02, BUG-F2-03 | BUG-F2-02 | undefined |
| `bench-taskdash-baseline-r1-183e07` | taskdash | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-benchmark_shop-buggy-r1-2c0297` | benchmark_shop | buggy | 2 | 0 | 0 | 2 | undefined | 0.000 | undefined | - | BUG-BENCH-01, BUG-BENCH-02 | - | undefined |
| `bench-benchmark_shop-baseline-r1-df00a2` | benchmark_shop | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |

## Aggregate (confirmed findings only, buggy builds)

- TP 4, FP 0, FN 5; precision 1.000, recall 0.444, F1 0.615
- False alarms on defect-free (baseline) builds: 0
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 144/145

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-7e455f` | 30 | 8 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-apex_portal-baseline-r1-07ee6a` | 22 | 0 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-taskdash-buggy-r1-8b2296` | 30 | 3 | 1 | 1 | 0.750 | 2/2 | 1/1 | 0.974 |
| `bench-taskdash-baseline-r1-183e07` | 30 | 8 | 1 | 1 | 0.750 | 2/2 | 1/1 | 1.000 |
| `bench-benchmark_shop-buggy-r1-2c0297` | 28 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 0.967 |
| `bench-benchmark_shop-baseline-r1-df00a2` | 26 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 0.854 |

## Timing and LLM usage

| Run | Investigation s | Planning s | Execution s | Verification s | Exported run s | Investigation LLM calls / fallbacks / latency s | LLM test proposals: proposed / accepted / error |
|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-7e455f` | 95.09 | 14.633 | 36.61 | 17.22 | 19.99 | 9 / 0 / 17.363 | 8 / 0 / - |
| `bench-apex_portal-baseline-r1-07ee6a` | 89.3 | 24.512 | 31.88 | 0.0 | 19.48 | 9 / 0 / 16.193 | 0 / 0 / LLMResponseError: Groq API error: Error code: 400 - {'error': {'message': "Failed to generate JSON. Please adjust your prompt. See 'failed_generation' for more details.", 'ty |
| `bench-taskdash-buggy-r1-8b2296` | 46.48 | 47.035 | 49.42 | 20.71 | 31.52 | 8 / 0 / 17.014 | 8 / 5 / - |
| `bench-taskdash-baseline-r1-183e07` | 41.58 | 22.03 | 38.95 | 0.0 | 29.64 | 8 / 1 / 18.707 | 8 / 0 / - |
| `bench-benchmark_shop-buggy-r1-2c0297` | 86.53 | 34.137 | 49.24 | 12.1 | 42.74 | 12 / 0 / 49.693 | 8 / 8 / - |
| `bench-benchmark_shop-baseline-r1-df00a2` | 78.17 | 19.182 | 73.6 | 2.42 | 58.51 | 12 / 0 / 48.963 | 6 / 6 / - |

## Limitations

- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
