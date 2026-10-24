# VerityQA — Project Facts and Single Source of Truth

This document serves as the ultra-compact, authoritative single source of truth for all facts, paths, classes, endpoints, configuration parameters, and verified metrics for the VerityQA project.

---

## 1. Project Identity

- **Project Name**: VerityQA
- **Problem Statement**: iMocha CODE APEX 3.0 — IM-02, Autonomous AI QA Engineer
- **Core Value Proposition**: Evidence-grounded autonomous QA engineering using deterministic DOM analysis, boundary value derivation, multi-node LangGraph planning with an adversarial critic, resilient Playwright execution, oracle verification, and standalone script export.

---

## 2. Directory & File Reference

| Purpose | File Path | Key Classes / Functions |
| :--- | :--- | :--- |
| **FastAPI App** | `app/main.py` | `app`, `inspect_url()`, `run_qa()`, `export_script()`, `run_benchmark()` |
| **Configuration** | `app/config.py` | `Settings`, `get_settings()` |
| **Data Schemas** | `app/schemas.py` | `InspectionRequest`, `InspectionResponse`, `RunRequest`, `RunResponse` |
| **Execution Runner** | `app/execution/runner.py` | `ExecutionRunner`, `synthesize_steps()`, `execute_plan()` |
| **Resilient Locators** | `app/execution/locator.py` | `ResilientLocator`, `resolve_locator()` |
| **Script Exporter** | `app/execution/exporter.py` | `ScriptExporter`, `export_python_script()` |
| **DOM Inspector** | `app/inspector.py` | `DOMInspector`, `inspect_url()`, `classify_page()` |
| **Crawler & Graph** | `app/crawler.py`, `app/exploration/` | `Crawler`, `WebsiteExplorer`, `ExplorationGraph` |
| **Boundary Value Engine**| `app/knowledge/bva.py` | `BoundaryValueEngine`, `derive_boundary_values()` |
| **QA Pattern Store** | `app/knowledge/retriever.py` | `PatternRetriever`, `retrieve_patterns()` |
| **LangGraph State** | `app/agents/state.py` | `AgentState` |
| **LangGraph Workflow** | `app/agents/workflow.py` | `create_planning_workflow()`, `should_revise()` |
| **Planning Nodes** | `app/agents/` | `RequirementAnalystNode`, `EvidenceInterpreterNode`, `RiskAnalystNode`, `DeterministicDerivationNode`, `PlanGeneratorNode`, `QACriticNode`, `PlanValidatorNode` |
| **LLM Provider** | `app/llm/` | `GeminiProvider`, `OllamaProvider`, `get_llm()` |
| **Failure Investigator**| `app/bugs/investigator.py` | `BugInvestigator`, `investigate_failure()` |
| **Oracle Verifier** | `app/evidence/oracle_verifier.py` | `OracleVerifier`, `verify_assertion()` |
| **Benchmark Harness** | `app/benchmark/` | `BenchmarkRunner`, `BenchmarkManifest` |
| **Frontend UI** | `app/static/` | `index.html`, `app.js`, `style.css` |
| **Demo Fixture 1** | `demo_app/main.py` | Port 8001: Buggy Auth & Search target |
| **Demo Fixture 2** | `demo_app/fixture2/main.py` | Port 8002: TaskDash Dashboard target |

---

## 3. LangGraph Planning Pipeline

The test planning agent executes a stateful 7-node cyclic graph:
1. `requirement_analysis` (`app/agents/requirement_analysis.py`)
2. `evidence_interpretation` (`app/agents/evidence_interpretation.py`)
3. `risk_analysis` (`app/agents/risk_analysis.py`)
4. `deterministic_derivation` (`app/agents/deterministic_derivation.py`)
5. `plan_generation` (`app/agents/plan_generator.py`)
6. `qa_critic` (`app/agents/qa_critic.py`)
   - *Conditional Edge*: `should_revise` $\to$ loops to `plan_generation` if issues found and `critic_revisions_count < max_revisions`.
7. `plan_validation` (`app/agents/plan_validator.py`) $\to$ `END`

---

## 4. API Endpoints

- `GET /health`: Liveness probe.
- `POST /api/inspect`: Crawl + inspect + plan generation.
- `POST /api/run`: Full autonomous end-to-end QA execution.
- `GET /api/run/{run_id}/export`: Download standalone Playwright Python script.
- `POST /api/benchmark/run`: Benchmark execution against planted defect test fixtures.
- `GET /`: Dashboard single page application.

---

## 5. Ports & Services

- **VerityQA Core Server**: Port `8000` (`http://127.0.0.1:8000`)
- **Demo Fixture 1 (Auth & Search)**: Port `8001` (`http://127.0.0.1:8001`)
- **Demo Fixture 2 (TaskDash)**: Port `8002` (`http://127.0.0.1:8002`)
- **Ollama Local LLM (Optional)**: Port `11434` (`http://127.0.0.1:11434`)

---

## 6. Verified Test Metrics

- **Total Automated Tests**: 296 tests in `tests/`.
- **Pass Rate**: 100% (296 passed, 0 failed, 0 skipped).
- **Execution Time**: ~87 seconds across the full suite.
- **Planted Defect Detection Recall**: 100% against benchmark test fixtures.
- **Inference Planning Speed**: ~50–55 tokens/second on Gemini Flash.
- **Planning Cycle Latency**: ~20–45 seconds for full 7-node LangGraph execution with critic evaluation.
