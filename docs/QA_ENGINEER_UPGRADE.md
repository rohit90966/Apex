# VerityQA — Knowledge-Driven QA Engineer Upgrade

## 1. Architecture diagnosis (before this stage)

| Area | Finding (file evidence) |
|---|---|
| Knowledge | Two disconnected systems: `app/knowledge/retriever.py` (Chroma + Ollama embeddings, used only by the legacy LangGraph planning agent via `app/agents/knowledge_retrieval.py`; Ollama not available in this environment) and `app/knowledge/patterns/*.json` scenario templates (SauceDemo-shaped). No defect taxonomy, no oracle knowledge, no applicability model beyond archetype/capability tags. |
| Investigation | Rich probe families already existed (`app/investigation_agent/probes/forms.py`: happy, required_blank, minlength/checkbox bypass, mismatch, boundary; nav, lists, controls, invariants) with a 2-of-2 reproducer. Leads were not connected to test planning or benchmark scoring (fixed in the previous stage). |
| Planning | Evidence-grounded planner (`app/test_planning/planner.py`) with hard-wired strategy dispatch; strategies were not traceable to any knowledge. No test that separates browser blocking from application behaviour unless a requirement was supplied. No cross-field reasoning. |
| Oracles / lifecycle | Verdicts were binary: established expectation → confirmed, otherwise anomaly. No "likely defect" class; no shared definition between investigator, evaluator and UI. |
| Persistence / UI | Investigation leads and execution bug reports lived in different structures (report vs. execution JSON); no unified findings, no stable finding identity; dashboard did not reload findings after execution. |
| Memory | None (each run started from zero; no record of which strategies were productive). |
| Evaluation | Benchmark scored detections but did not show *where* a missed defect was lost. |

## 2. What was added / changed

### Knowledge base (`app/knowledge/qa/*.json`, schema in `app/knowledge/schema.py`)
* 58 versioned, validated entries: 24 defect classes (input validation, workflow, state consistency,
  authentication/authorization, API/errors, accessibility, resilience, files), 15 test-design strategies
  (EP/BVA, layer distinction, decision table/cross-field, state transition, metamorphic filter, ordering
  invariant, rendering invariant, workflow, regression-from-lead, observed validation, accessibility,
  domain templates, pairwise, idempotency), 9 oracles, 10 application patterns.
* Every entry declares applicability predicates (`all/any/none`), required observations, oracle strategy,
  false-positive warnings, safety restrictions, confidence requirements and **automation status**
  (12 defect classes automated, 6 partially, 6 explicitly not automated — reported, never implied).
* Loader validates schema, duplicate ids, unknown predicates, dangling cross-references and unknown planner
  implementations (`QAKnowledgeBase.validate`).

### Retrieval (`app/knowledge/qa_retrieval.py`)
* `evidence_profile()` — deterministic predicates over the current run's inventory only (33 predicates,
  each with the observations that justify it).
* `QAKnowledgeRetriever` — applicability filtering (missing / contradicting evidence recorded as exclusion
  reasons), ranking by specificity, risk, automation status and a capped (±0.1) historical-effectiveness term.
  No embeddings: structured predicates are sufficient and auditable; the legacy vector store is untouched.

### Planning (`app/test_planning/planner.py`)
* Generators are selected by the retrieved strategies; every test carries `knowledge_ids`.
* New reusable mechanisms:
  * **Server-side enforcement of declared constraints** (`TS-SERVER-ENFORCEMENT`): for each HTML-declared
    rule, disable client validation for that field's form and submit one violating value; expect feedback
    attributable to the field/rule. Not applied to length rules on login forms; skipped when a requirement
    already exercises the same rule.
  * **Cross-field confirmation** (`TS-CROSS-FIELD`): confirmation pairs inferred from field names/labels.
  * **Rendering-anomaly invariant** (`TS-INVARIANT`): NaN/undefined/[object Object]/tracebacks after replayed
    interactions (new `NO_TEXT_ANOMALY` oracle in executor and exporter).
  * **Accessible-name audit** (`TS-A11Y-NAMES`) using the existing heuristic oracle.
* Diagnostics record the evidence profile, selected/excluded knowledge and applied strategies.

### Finding lifecycle (`app/findings/lifecycle.py`) and registry (`app/findings/registry.py`)
* Classes: CONFIRMED_DEFECT, LIKELY_DEFECT, UNVERIFIED_ANOMALY, INCONCLUSIVE, TEST_INFRASTRUCTURE_FAILURE,
  TEST_DEFINITION_ERROR, BLOCKED, PASSED, with documented promotion rules (evidence only promotes; a later
  passing/inconclusive run is an observation and never erases a confirmed/likely finding).
* Expected-behaviour sources: established (REQUIREMENT, HTML_CONSTRAINT browser layer, OBSERVED) → confirmed;
  strong (HTML_DECLARED server layer, SEMANTIC_CONVENTION, GENERIC_INVARIANT, ACCESSIBILITY_HEURISTIC,
  investigation DECLARED/CONVENTION leads) → likely; inferred → anomaly.
* The bug investigator uses the lifecycle (new `LIKELY_DEFECT` verdict / `VerificationStatus`), and every
  bug report carries `classification` and `expected_source`.
* Registry merges investigation leads and execution failures into findings with stable ids
  (origin, route, subject, rule); a lead's regression test attaches to the lead's finding; evidence from every
  source is kept. `RunStore` writes `findings.json` on completion and after every execution and reports
  missing artifacts; older runs get findings derived read-only.

### UI
* Findings panel (Execution tab) listing classification, finding, location, evidence sources and regression
  tests; refreshed after each execution. Requirement-alignment panel on Generated Tests (previous stage).

### Memory (`app/knowledge/memory.py`)
* Generic strategy counters and per-origin run summaries, identifiers and counts only (no values, URLs with
  queries, credentials or page text). Capped ranking influence; never gates applicability; per-origin history
  is not used for planning other origins; disabled during benchmarks.

### Evaluation (`benchmarks/`, `scripts/run_multi_website_benchmark.py`)
* Findings scored as reproduced (confirmed + likely) and confirmed-only; investigation and execution sources
  separate; benchmark runs persisted through `RunStore` and findings read back from it.
* Per-defect stage analysis: route discovered → test generated → executed → reproduced → persisted →
  regression test, with the stage where a defect was missed.

## 3. Not wired / not done (explicit)
* `app/investigation_agent/qa_knowledge_integration.py` and `probes/accessibility.py` (written before the
  usage-limit interruption) are tested but **not wired into the live investigation loop**: injecting
  script-less agenda questions is untested against the live agenda, and the duplicate-accessible-name rule
  would misreport legitimate repeated row actions. Knowledge currently steers test planning, not exploration.
* Not automated: state persistence across pages, duplicate submissions, counters/totals, authorization,
  keyboard/focus accessibility, file upload, pairwise.
* No new seeded-defect fixture for auth/role-based or asynchronous (API-driven) applications was added.
