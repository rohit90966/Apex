# VerityQA Multi-Website Benchmark

- Benchmark ID: `mwb-20261024T162350Z-6e0e85`
- Timestamp: 2026-10-24T16:23:50.451647+00:00
- Code commit: `d0d2fdd3cea7fe1341034ec3242fc78db9ec1751` (working tree dirty: True)
- Configuration identity: `d31e5dcfa84f5e06`
- Suite version: `multi-website-benchmark/1.0`

## Target availability

| Target | Class | Status | Note |
|---|---|---|---|
| Apex Demo Portal (fixture 1) | REPOSITORY_FIXTURE | AVAILABLE |  |
| TaskDash (fixture 2) | REPOSITORY_FIXTURE | AVAILABLE |  |
| Intelligence benchmark shop (controlled synthetic e-commerce) | CONTROLLED_SYNTHETIC | AVAILABLE |  |
| TaskFlow (fixture 3) | REPOSITORY_FIXTURE | UNAVAILABLE | nothing is serving http://127.0.0.1:8003/; fixture is not present in this repository |
| SauceDemo (public demo) | PUBLIC_INDEPENDENT | AVAILABLE | Public target; no ground truth, single mode |

## Per-run results

| Run | Target | Mode | Archetype | Routes | States | Controls | Tests | PASS | FAIL | ERROR | BLOCKED | INVALID | INCOMPLETE | Locator | Grounded selectors | Meaningful oracle | Export agreement |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-ec676b` | apex_portal | buggy | AUTHENTICATION | 6 | 7 | 30 | 22 | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-apex_portal-baseline-r1-a6f635` | apex_portal | baseline | AUTHENTICATION | 6 | 7 | 30 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-taskdash-buggy-r1-5af7d6` | taskdash | buggy | GENERIC_WEB | 7 | 7 | 51 | 22 | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 37/37 | 22/22 | 22/22 |
| `bench-taskdash-baseline-r1-884db4` | taskdash | baseline | GENERIC_WEB | 7 | 7 | 51 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 37/37 | 22/22 | 22/22 |
| `bench-benchmark_shop-buggy-r1-6fe509` | benchmark_shop | buggy | ECOMMERCE | 6 | 11 | 43 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 29/29 | 19/19 | 19/19 |
| `bench-benchmark_shop-baseline-r1-01f41b` | benchmark_shop | baseline | ECOMMERCE | 6 | 11 | 43 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 29/29 | 19/19 | 19/19 |
| `bench-saucedemo-public-r1-f040aa` | saucedemo | public | ECOMMERCE | 5 | 11 | 63 | 26 | 6 | 3 | 17 | 0 | 0 | 0 | 16 | 35/35 | 26/26 | 25/26 |
| `bench-apex_portal-buggy-r2-ccf6b0` | apex_portal | buggy | AUTHENTICATION | 6 | 7 | 30 | 22 | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-apex_portal-baseline-r2-c8a077` | apex_portal | baseline | AUTHENTICATION | 6 | 7 | 30 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 58/58 | 22/22 | 22/22 |
| `bench-taskdash-buggy-r2-695fc8` | taskdash | buggy | GENERIC_WEB | 7 | 7 | 56 | 22 | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 37/37 | 22/22 | 22/22 |
| `bench-taskdash-baseline-r2-0df770` | taskdash | baseline | GENERIC_WEB | 7 | 7 | 53 | 22 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 37/37 | 22/22 | 22/22 |
| `bench-benchmark_shop-buggy-r2-3e2f36` | benchmark_shop | buggy | ECOMMERCE | 6 | 11 | 43 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 29/29 | 19/19 | 19/19 |
| `bench-benchmark_shop-baseline-r2-1c0e37` | benchmark_shop | baseline | ECOMMERCE | 6 | 11 | 43 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 29/29 | 19/19 | 19/19 |
| `bench-saucedemo-public-r2-b3c6ab` | saucedemo | public | ECOMMERCE | 5 | 11 | 63 | 26 | 7 | 3 | 16 | 0 | 0 | 0 | 16 | 35/35 | 26/26 | 26/26 |

## Defect detection (evaluator, after the runs)

| Run | Target | Mode | Planted | TP | FP | FN | Precision | Recall | F1 | Detected | Missed | Anomaly only | False alarms (defect-free build) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-ec676b` | apex_portal | buggy | 4 | 3 | 0 | 1 | 1.000 | 0.750 | 0.857 | BUG-01, BUG-02, BUG-04 | BUG-03 | - | undefined |
| `bench-apex_portal-baseline-r1-a6f635` | apex_portal | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-taskdash-buggy-r1-5af7d6` | taskdash | buggy | 3 | 1 | 0 | 2 | 1.000 | 0.333 | 0.500 | BUG-F2-01 | BUG-F2-02, BUG-F2-03 | BUG-F2-02 | undefined |
| `bench-taskdash-baseline-r1-884db4` | taskdash | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-benchmark_shop-buggy-r1-6fe509` | benchmark_shop | buggy | 2 | 0 | 0 | 2 | undefined | 0.000 | undefined | - | BUG-BENCH-01, BUG-BENCH-02 | - | undefined |
| `bench-benchmark_shop-baseline-r1-01f41b` | benchmark_shop | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-saucedemo-public-r1-f040aa` | saucedemo | public | n/a | | | | | | | | | | No ground truth for this target; detection metrics undefined. |
| `bench-apex_portal-buggy-r2-ccf6b0` | apex_portal | buggy | 4 | 3 | 0 | 1 | 1.000 | 0.750 | 0.857 | BUG-01, BUG-02, BUG-04 | BUG-03 | - | undefined |
| `bench-apex_portal-baseline-r2-c8a077` | apex_portal | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-taskdash-buggy-r2-695fc8` | taskdash | buggy | 3 | 1 | 0 | 2 | 1.000 | 0.333 | 0.500 | BUG-F2-01 | BUG-F2-02, BUG-F2-03 | BUG-F2-02 | undefined |
| `bench-taskdash-baseline-r2-0df770` | taskdash | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-benchmark_shop-buggy-r2-3e2f36` | benchmark_shop | buggy | 2 | 0 | 0 | 2 | undefined | 0.000 | undefined | - | BUG-BENCH-01, BUG-BENCH-02 | - | undefined |
| `bench-benchmark_shop-baseline-r2-1c0e37` | benchmark_shop | baseline | 0 | 0 | 0 | 0 | undefined | undefined | undefined | - | - | - | 0 |
| `bench-saucedemo-public-r2-b3c6ab` | saucedemo | public | n/a | | | | | | | | | | No ground truth for this target; detection metrics undefined. |

## Aggregate (confirmed findings only, buggy builds)

- TP 8, FP 0, FN 10; precision 1.000, recall 0.444, F1 0.615
- False alarms on defect-free (baseline) builds: 0
- Cross-target selector leaks: 0; irrelevant patterns accepted: 0
- Export verdict agreement: 303/304

## Generation and coverage

| Run | Candidates | Rejected | Inapplicable templates | Unknown constraints | Capability coverage | Requirements covered | Constrained fields covered | Assertion evaluation rate |
|---|---|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-ec676b` | 22 | 0 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-apex_portal-baseline-r1-a6f635` | 22 | 0 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-taskdash-buggy-r1-5af7d6` | 22 | 0 | 1 | 1 | 0.750 | 2/2 | 1/1 | 1.000 |
| `bench-taskdash-baseline-r1-884db4` | 22 | 0 | 1 | 1 | 0.750 | 2/2 | 1/1 | 1.000 |
| `bench-benchmark_shop-buggy-r1-6fe509` | 20 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 1.000 |
| `bench-benchmark_shop-baseline-r1-01f41b` | 20 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 1.000 |
| `bench-saucedemo-public-r1-f040aa` | 28 | 2 | 4 | 3 | 0.750 | 2/2 | 0/0 | 0.357 |
| `bench-apex_portal-buggy-r2-ccf6b0` | 22 | 0 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-apex_portal-baseline-r2-c8a077` | 22 | 0 | 3 | 1 | 0.333 | 6/6 | 8/8 | 1.000 |
| `bench-taskdash-buggy-r2-695fc8` | 22 | 0 | 1 | 1 | 0.750 | 2/2 | 1/1 | 1.000 |
| `bench-taskdash-baseline-r2-0df770` | 22 | 0 | 1 | 1 | 0.750 | 2/2 | 1/1 | 1.000 |
| `bench-benchmark_shop-buggy-r2-3e2f36` | 20 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 1.000 |
| `bench-benchmark_shop-baseline-r2-1c0e37` | 20 | 1 | 9 | 2 | 0.500 | 2/2 | 0/0 | 1.000 |
| `bench-saucedemo-public-r2-b3c6ab` | 28 | 2 | 4 | 3 | 0.750 | 2/2 | 0/0 | 0.393 |

## Timing (seconds)

| Run | Investigation | Planning | Execution | Verification | Exported run | LLM calls |
|---|---|---|---|---|---|---|
| `bench-apex_portal-buggy-r1-ec676b` | 77.53 | 0.022 | 18.19 | 6.99 | 18.99 | 0 |
| `bench-apex_portal-baseline-r1-a6f635` | 77.19 | 0.006 | 18.16 | 0.0 | 19.62 | 0 |
| `bench-taskdash-buggy-r1-5af7d6` | 26.1 | 0.005 | 20.87 | 9.77 | 21.01 | 0 |
| `bench-taskdash-baseline-r1-884db4` | 25.98 | 0.005 | 20.57 | 0.0 | 20.98 | 0 |
| `bench-benchmark_shop-buggy-r1-6fe509` | 34.49 | 0.006 | 17.48 | 0.0 | 17.12 | 0 |
| `bench-benchmark_shop-baseline-r1-01f41b` | 29.98 | 0.006 | 16.54 | 0.0 | 17.1 | 0 |
| `bench-saucedemo-public-r1-f040aa` | 61.21 | 0.005 | 220.11 | 19.67 | 223.85 | 0 |
| `bench-apex_portal-buggy-r2-ccf6b0` | 77.06 | 0.006 | 18.87 | 7.4 | 19.89 | 0 |
| `bench-apex_portal-baseline-r2-c8a077` | 77.82 | 0.014 | 20.89 | 0.0 | 20.41 | 0 |
| `bench-taskdash-buggy-r2-695fc8` | 26.45 | 0.018 | 29.42 | 13.71 | 28.01 | 0 |
| `bench-taskdash-baseline-r2-0df770` | 25.69 | 0.005 | 22.88 | 0.0 | 20.94 | 0 |
| `bench-benchmark_shop-buggy-r2-3e2f36` | 34.17 | 0.006 | 16.77 | 0.0 | 16.85 | 0 |
| `bench-benchmark_shop-baseline-r2-1c0e37` | 29.86 | 0.006 | 17.25 | 0.0 | 16.95 | 0 |
| `bench-saucedemo-public-r2-b3c6ab` | 104.2 | 0.005 | 190.06 | 24.56 | 244.13 | 0 |

## Repeated-run reproducibility

| Target | Mode | Runs | PASS counts | FAIL counts | Detected-defect sets identical | Plan Jaccard (min) |
|---|---|---|---|---|---|---|
| apex_portal | buggy | 2 | [18, 18] | [4, 4] | True | 1.000 |
| apex_portal | baseline | 2 | [22, 22] | [0, 0] | True | 1.000 |
| taskdash | buggy | 2 | [18, 18] | [4, 4] | True | 0.913 |
| taskdash | baseline | 2 | [22, 22] | [0, 0] | True | 0.913 |
| benchmark_shop | buggy | 2 | [19, 19] | [0, 0] | True | 1.000 |
| benchmark_shop | baseline | 2 | [19, 19] | [0, 0] | True | 1.000 |
| saucedemo | public | 2 | [6, 7] | [3, 3] | True | 1.000 |

## Limitations

- TaskFlow (fixture 3) is not present in this repository or machine; no TaskFlow results are reported.
- Token usage and cost are not measured: the LLM providers expose no token telemetry to this harness.
- Investigations run with the deterministic planner when no LLM key is configured (see config.groq_configured).
- Fixture state (e.g. tasks created by tests) persists across repeats within one server process.
- TN/specificity are not reported: the fixtures define no universe of negative cases.
