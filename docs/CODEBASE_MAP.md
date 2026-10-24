# VerityQA — Codebase Architecture & File Map

## 1. Project Directory Tree

```
Code Apex/
├── app/                              # Core application source code
│   ├── main.py                       # FastAPI application entry point, lifecycle, and route handlers
│   ├── config.py                     # Global application settings (Pydantic Settings)
│   ├── schemas.py                    # Core shared domain models & Pydantic schemas
│   ├── safety.py                     # URL safety validator, SSRF mitigation, and allowed host checking
│   ├── inspector.py                  # Playwright page inspector: DOM, forms, buttons, links, screenshots
│   ├── page_classifier.py           # Deterministic page category classifier (Login, Search, General, etc.)
│   ├── test_planner.py               # Deterministic rule-based fallback test planner
│   ├── test_planning_agent.py        # Central LangGraph workflow orchestration facade
│   │
│   ├── agents/                       # LangGraph multi-agent workflow nodes and models
│   │   ├── workflow.py               # StateGraph construction, timed node wrappers, conditional routing
│   │   ├── state.py                  # AgentWorkflowState typed dictionary and WorkflowStatus enum
│   │   ├── models.py                 # Agent-specific schemas (RiskItem, StrategyScenario, CritiqueResult)
│   │   ├── requirement_analyst.py    # Node A: Parses explicit user testing requirements
│   │   ├── evidence_interpreter.py   # Node B: Extracts actionable DOM targets & structural features
│   │   ├── risk_analyst.py           # Node C: Identifies functional, security, accessibility, and negative input risks
│   │   ├── knowledge_retrieval.py    # Node RAG: Vector search against ChromaDB QA patterns
│   │   ├── deterministic_derivation.py# Node BVA: Pure-Python boundary value & invariant derivation
│   │   ├── strategy_planner.py       # Node D: Maps requirements, risks, and invariants into scenarios
│   │   ├── plan_generator.py         # Node E: LLM-powered structured JSON test proposal generator
│   │   ├── qa_critic.py              # Node F: Semantic auditor enforcing DOM grounding & bounded revisions
│   │   └── plan_validator.py         # Node G: Pure-Python deterministic plan & security sanitizer
│   │
│   ├── benchmark/                    # Benchmark manifest & evaluation engine
│   │   └── manifest.py               # Planted bug manifest and BenchmarkEvaluator scoring engine
│   │
│   ├── bugs/                         # Failure investigation & bug triage
│   │   └── investigator.py           # BugInvestigator: failure reproduction, oracle checking, and triage
│   │
│   ├── evidence/                     # Evidence ledger models, BVA engine, and oracle audit
│   │   ├── models.py                 # EvidenceRecord, FieldConstraintEvidence, TestIdea, UnknownItem
│   │   ├── oracle_verifier.py        # Deterministic evidence & oracle consistency verifier
│   │   └── derivation/
│   │       └── boundary_value.py     # BoundaryValueEngine: exact mathematical N-1, N, N+1 calculations
│   │
│   ├── execution/                    # Playwright test execution & step synthesis
│   │   ├── executor.py               # Low-level browser automation engine (clicks, fills, screenshots)
│   │   ├── locator.py                # Smart locator cascade & selector resolver
│   │   ├── runner.py                 # QAPlanRunner: compiles test cases into executable ActionSteps
│   │   ├── specification.py          # Specification verification engine (masking, empty submit, a11y)
│   │   └── verifiers.py              # Specialized behavior verifiers
│   │
│   ├── exploration/                  # Adaptive state exploration & dynamic test synthesis
│   │   ├── explorer.py               # AutonomousExplorer: BFS UI state traversal
│   │   ├── generator.py              # AdaptiveTestGenerator: derives tests from state graph
│   │   ├── graph.py                  # ApplicationStateGraph & transition models
│   │   └── policy.py                 # Exploration action safety & prioritization policy
│   │
│   ├── export/                       # Playwright test script export
│   │   └── playwright_exporter.py    # PlaywrightExporter: exports standalone executable Python test files
│   │
│   ├── investigation/                # Multi-page website crawling & feature detection
│   │   ├── crawler.py                # WebsiteCrawler: bounded Playwright multi-page crawler
│   │   ├── feature_detector.py       # FeatureDetector: rule-based UI feature identification
│   │   ├── models.py                 # InvestigationReport, PageEvidence, FeatureItem schemas
│   │   ├── page_inventory.py         # PageInventory models
│   │   └── url_normalizer.py         # Canonical URL normalization & origin checking
│   │
│   ├── knowledge/                    # Vector knowledge base for QA heuristics
│   │   ├── embeddings.py             # Ollama & fallback embedding generators
│   │   ├── models.py                 # QAPattern models
│   │   ├── retriever.py              # KnowledgeRetriever: semantic vector retrieval
│   │   ├── seed_data.py              # Hardcoded high-value QA testing heuristics
│   │   └── vector_store.py           # ChromaDB integration wrapper
│   │
│   ├── llm/                          # LLM abstraction layer
│   │   ├── base.py                   # BaseLLMProvider abstract interface
│   │   ├── factory.py                # Provider factory (Gemini vs. Ollama)
│   │   ├── gemini_provider.py        # Google Gemini Flash provider with exponential backoff & repair
│   │   └── ollama_provider.py        # Local Ollama provider with compact JSON repair
│   │
│   └── static/                       # Web dashboard frontend assets
│       ├── index.html                # Single-page dashboard HTML
│       ├── app.js                    # Reactive frontend logic, streaming status, and API client
│       └── style.css                 # Dark-mode dashboard styling
│
├── demo_app/                         # Integrated testing applications
│   ├── main.py                       # Fixture 1: Authentication & search demo app (Port 8001)
│   ├── templates/                    # HTML templates for Fixture 1
│   └── fixture2/
│       └── main.py                   # Fixture 2: TaskDash productivity dashboard (Port 8002)
│
├── tests/                            # Comprehensive automated test suite (296 tests)
│   ├── test_agent_nodes.py           # Individual LangGraph node unit tests
│   ├── test_agent_workflow.py        # Complete LangGraph graph compilation and execution tests
│   ├── test_boundary_value_engine.py # Pure-Python mathematical BVA engine tests
│   ├── test_bug_investigator.py      # Defect classification and reproduction tests
│   ├── test_crawler.py               # Playwright multi-page crawler tests
│   ├── test_execution_engine.py      # Playwright step synthesis and execution tests
│   ├── test_locator_grounding.py     # Smart locator cascade and grounding tests
│   ├── test_pipeline_repair_regression.py # Regression tests for visibility, masking, and defect triage
│   ├── test_test_generation_count.py # Regression tests for target test case counts (8 conditions)
│   └── ...                           # (35 test files total, covering all subsystems)
│
└── docs/                             # Complete project documentation
```

---

## 2. Component Responsibility Matrix

| Subsystem | Primary File | Key Class / Function | Responsibility |
| :--- | :--- | :--- | :--- |
| **API Entry Point** | `app/main.py` | `app = FastAPI()`, `run_full_qa()` | Exposes REST endpoints, controls execution, links services. |
| **Configuration** | `app/config.py` | `Settings`, `get_settings()` | Loads environment variables with defaults and validation. |
| **Safety Engine** | `app/safety.py` | `validate_url_safety()` | Blocks private IP ranges (SSRF) and enforces allowed host policies. |
| **Browser Inspector**| `app/inspector.py` | `WebsiteInspector.inspect()` | Launches Playwright, renders page, extracts forms/buttons/inputs/A11y. |
| **Page Classifier** | `app/page_classifier.py`| `PageClassifier.classify()` | Categorizes page (LOGIN, REGISTRATION, SEARCH, DASHBOARD, GENERAL). |
| **Crawler** | `app/investigation/crawler.py` | `WebsiteCrawler.investigate()` | Recursively follows internal links to discover multi-page layouts. |
| **BVA Engine** | `app/evidence/derivation/boundary_value.py` | `BoundaryValueEngine.derive_from_inspection()` | Calculates exact mathematical boundaries ($N-1, N, N+1$) for constraints. |
| **Deterministic Derivation** | `app/agents/deterministic_derivation.py` | `DeterministicDerivationNode.run()` | Generates presence evidence and baseline invariants for unconstrained forms. |
| **Knowledge Base** | `app/knowledge/retriever.py` | `KnowledgeRetriever.retrieve()` | Vector-based semantic search for QA testing heuristics via ChromaDB. |
| **Agent Orchestrator**| `app/test_planning_agent.py`| `TestPlanningAgent.create_plan()` | Coordinates compiled LangGraph workflow with fallback resilience. |
| **LangGraph Graph** | `app/agents/workflow.py` | `build_qa_planning_workflow()` | Compiles StateGraph with timed nodes and conditional revision loops. |
| **Plan Generator** | `app/agents/plan_generator.py`| `PlanGeneratorNode.run()` | Compact JSON prompting to Gemini Flash requesting target test counts. |
| **QA Critic** | `app/agents/qa_critic.py` | `QACriticNode.run()` | Audits generated plans against observed DOM, enforcing bounded revisions. |
| **Plan Validator** | `app/agents/plan_validator.py`| `PlanValidatorNode.run()` | Sanitizes plans, dedups scenarios, and enforces PROPOSED status. |
| **Fallback Planner** | `app/test_planner.py` | `DeterministicPlanner.generate_plan()` | Pure-Python rule-based planning if Gemini API quotas/timeouts occur. |
| **Test Runner** | `app/execution/runner.py` | `QAPlanRunner.synthesize_steps()`, `run_all()` | Translates high-level test cases into executable Playwright ActionSteps. |
| **Locator Cascade** | `app/execution/locator.py` | `SmartLocator.resolve()` | Multi-strategy selector resolution (`data-testid` $\rightarrow$ ID $\rightarrow$ A11y $\rightarrow$ text). |
| **Assertion Engine**| `app/execution/specification.py` | `SpecificationVerifier.verify_assertion()` | Defensible assertion checks (masking, valueMissing, visibility). |
| **Bug Investigator**| `app/bugs/investigator.py` | `BugInvestigator.investigate_failure()` | Re-runs failures in clean browser contexts; classifies defect types. |
| **Playwright Exporter**| `app/export/playwright_exporter.py` | `PlaywrightExporter.export_to_python_script()` | Exports verified suites as clean, standalone Python Playwright code. |
| **Benchmark Suite** | `app/benchmark/manifest.py`| `BenchmarkEvaluator.evaluate()` | Measures precision, recall, and detection accuracy against planted bugs. |

---

## 3. Implementation Status Summary

* **Fully Complete & Verified:**
  - FastAPI asynchronous server, route registration, and static dashboard serving.
  - SSRF protection and safety URL policy enforcement.
  - Playwright DOM extraction, form cataloging, and screenshot capture.
  - Multi-page bounded Playwright crawler with URL canonicalization.
  - Pure-Python Boundary Value Analysis engine (BVA) for numeric and string constraints.
  - LangGraph multi-agent planning workflow (Requirement Analyst, Risk Analyst, Strategy Planner, Generator, Critic, Validator).
  - Telemetry timing wrappers on every LangGraph node.
  - Google Gemini Flash integration with JSON schema validation.
  - Deterministic fallback planner for offline/rate-limited environments.
  - Execution engine with smart locator cascade and Playwright browser driving.
  - Defect classification triage distinguishing Product Defects from Test Definition Errors.
  - Standalone Python Playwright script exporter.
  - Automated test suite (296 passing tests across 35 test files).

* **Partially Implemented / Configured:**
  - Local Ollama LLM provider (implemented in `app/llm/ollama_provider.py`; secondary to active Gemini Flash provider).
  - ChromaDB vector store (populated from seed patterns; falls back gracefully to in-memory heuristics if embedding endpoints are unavailable).
