# VerityQA — End-to-End Execution Pipeline

## 1. Overview

This document traces the complete execution lifecycle of VerityQA, starting from a user submitting a target URL on the web dashboard to receiving verified execution results, classified bug reports, and exportable Playwright scripts.

---

## 2. End-to-End System Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User as User / QA Engineer
    participant UI as Web Dashboard (app.js)
    participant API as FastAPI Router (main.py)
    participant Safety as Safety Validator (safety.py)
    participant Insp as Inspector & Crawler (inspector.py & crawler.py)
    participant Agent as LangGraph Orchestrator (workflow.py)
    participant LLM as Gemini Flash / Fallback (gemini_provider.py)
    participant Runner as Playwright Runner (runner.py & executor.py)
    participant Browser as Headless Chromium
    participant Triage as Bug Investigator (investigator.py)
    participant Exporter as Script Exporter (playwright_exporter.py)

    User->>UI: Submit Target URL + Optional Requirements
    UI->>API: POST /api/run (QARunRequest)
    API->>Safety: validate_url_safety(url)
    Safety-->>API: URL Approved (SSRF Safe)
    
    API->>Insp: inspect(url) & investigate(url)
    Insp->>Browser: Launch Chromium & Navigate
    Browser-->>Insp: DOM Tree, Forms, Inputs, A11y Tree, Screenshot
    Insp-->>API: InspectionResult + InvestigationReport
    
    API->>Agent: create_plan(inspection, classification, requirements)
    Note over Agent: Node Execution:<br/>1. Requirement Analyst<br/>2. Evidence Interpreter<br/>3. Risk Analyst<br/>4. Knowledge Retrieval (ChromaDB)<br/>5. Deterministic Derivation (BVA Engine)<br/>6. Strategy Planner
    Agent->>LLM: generate_json(sys_prompt, user_prompt)
    LLM-->>Agent: Raw Proposed Test Cases
    Note over Agent: 7. QA Critic Node (Audit Grounding)<br/>[If issues found & rev < 2: Re-route]<br/>8. Deterministic Plan Validator
    Agent-->>API: Validated TestPlan (6+ Cases)

    API->>Runner: run_all(test_cases, target_url)
    loop For Each Test Case
        Runner->>Runner: synthesize_steps(test_case)
        Runner->>Browser: Execute Actions (Navigate, Fill, Click, Assert)
        Browser-->>Runner: StepExecutionResult + Artifacts (Screenshots, Logs)
    end
    Runner-->>API: List[ExecutionResult]

    alt If Any Test Failed
        API->>Triage: investigate_failure(failed_result, test_case)
        Triage->>Browser: Isolated Clean Re-run
        Browser-->>Triage: Reproduction Evidence & Console Logs
        Triage->>Triage: Classify (PRODUCT_DEFECT vs TEST_DEFINITION_ERROR)
        Triage-->>API: Verified BugReport
    end

    API->>Exporter: export_to_python_script(test_cases, url)
    Exporter-->>API: Standalone Python Script String
    
    API-->>UI: QARunResponse (Results, Bug Reports, Export URL)
    UI-->>User: Render Interactive Dashboard & Enable Script Download
```

---

## 3. LangGraph Workflow Flowchart

```mermaid
flowchart TD
    START([Start Workflow]) --> Req["Node A: Requirement Analyst<br/><i>app/agents/requirement_analyst.py</i>"]
    Req --> Ev["Node B: Evidence Interpreter<br/><i>app/agents/evidence_interpreter.py</i>"]
    Ev --> Risk["Node C: Risk & Coverage Analyst<br/><i>app/agents/risk_analyst.py</i>"]
    Risk --> Know["Node RAG: Knowledge Retrieval<br/><i>app/agents/knowledge_retrieval.py</i>"]
    Know --> Det["Node BVA: Deterministic Derivation<br/><i>app/agents/deterministic_derivation.py</i>"]
    Det --> Strat["Node D: Strategy Planner<br/><i>app/agents/strategy_planner.py</i>"]
    Strat --> Gen["Node E: Plan Generator (Gemini Flash)<br/><i>app/agents/plan_generator.py</i>"]
    Gen --> Critic["Node F: QA Critic<br/><i>app/agents/qa_critic.py</i>"]
    
    Critic --> Decision{"Critic Decision<br/>(should_revise)"}
    Decision -- "Issues Found &<br/>Revision < 2<br/>(Missing Requirements)" --> Strat
    Decision -- "Issues Found &<br/>Revision < 2<br/>(Unsupported Elements / Duplicates)" --> Gen
    Decision -- "Critic Passed or<br/>Max Revisions Reached" --> Val["Node G: Plan Validator<br/><i>app/agents/plan_validator.py</i>"]
    
    Val --> END([End Workflow])
    
    subgraph Fallback ["Deterministic Fallback Engine"]
        AgentFail["API Timeout / Rate Limit (429) / Parsing Error"] -.-> FallbackPlanner["DeterministicPlanner<br/><i>app/test_planner.py</i>"]
        FallbackPlanner -.-> ReturnFallback["Return Validated TestPlan"]
    end
```

---

## 4. Stage-by-Stage Execution Breakdown

### Stage 1: Request Ingestion & URL Safety
- **Entry Point:** `app/main.py:run_full_qa()` or `app/main.py:inspect_website()`
- **Input:** `QARunRequest(url, description, execute_tests, verify_bugs, export_scripts)`
- **Implementation:** `app/safety.py:validate_url_safety()`
- **Mechanism:** Parses hostname, resolves DNS IP, checks against private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, AWS metadata `169.254.169.254`), and validates against `allowed_hosts_list`.
- **Status:** **Fully Implemented & Verified**.

### Stage 2: Browser Inspection & Multi-Page Deep Crawling
- **Entry Point:** `app/inspector.py:WebsiteInspector.inspect()` and `app/investigation/crawler.py:WebsiteCrawler.investigate()`
- **Input:** Target URL string.
- **Implementation:** Launches headless Playwright Chromium. Renders page, waits for network idle, extracts forms, inputs, buttons, headings, links, and accessibility tree, and captures full-page screenshot.
- **Output:** `InspectionResult` and `InvestigationReport` schemas.
- **Status:** **Fully Implemented & Verified**.

### Stage 3: Deterministic Classification
- **Entry Point:** `app/page_classifier.py:PageClassifier.classify()`
- **Input:** `InspectionResult`.
- **Implementation:** Rule-based decision heuristic matching observed password inputs, search inputs, data tables, and link density.
- **Output:** `PageClassification(page_category, confidence, reasoning_summary)`.
- **Status:** **Fully Implemented & Verified**.

### Stage 4: LangGraph Multi-Agent Planning
- **Entry Point:** `app/test_planning_agent.py:TestPlanningAgent.create_plan()`
- **Input:** `InspectionResult`, `PageClassification`, optional user requirements.
- **Nodes Executed in Sequence:**
  1. `RequirementAnalystNode`: Extracts explicit user assertions.
  2. `EvidenceInterpreterNode`: Catalogs interactive targets.
  3. `RiskAnalystNode`: Formulates risk catalog across 5+ dimensions.
  4. `KnowledgeRetrievalNode`: Vector-queries ChromaDB for testing heuristics.
  5. `DeterministicDerivationNode`: Computes BVA boundaries via `BoundaryValueEngine` or derives baseline presence invariants.
  6. `StrategyPlannerNode`: Maps requirements and risks into strategy scenarios.
  7. `PlanGeneratorNode`: Calls Gemini 2.5 Flash with compact JSON schema requesting target test count (default 6).
  8. `QACriticNode`: Verifies DOM grounding, rejecting hallucinated elements.
  9. `PlanValidatorNode`: Pure-Python schema check, sanitizing dangerous tokens and deduplicating cases.
- **Fallback:** If Gemini returns 429 or fails, `DeterministicPlanner` seamlessly produces 6 grounded test cases.
- **Output:** `TestPlan(test_cases, planning_mode, assumptions, review_summary)`.
- **Status:** **Fully Implemented & Verified**.

### Stage 5: Playwright Step Synthesis & Execution
- **Entry Point:** `app/execution/runner.py:QAPlanRunner.run_all()`
- **Input:** `List[TestCase]`, target URL.
- **Implementation:**
  - `synthesize_steps()` maps high-level test cases into concrete `ActionStep` sequences (Navigate, Fill, Click, Assert).
  - Selectors are resolved via `SmartLocator` cascade (`data-testid` $\rightarrow$ semantic ID $\rightarrow$ `aria-label` $\rightarrow$ button text).
  - Executed inside dedicated Playwright page contexts via `app/execution/executor.py:BrowserExecutor`.
- **Output:** `List[ExecutionResult]` with step timings, statuses, and screenshots.
- **Status:** **Fully Implemented & Verified**.

### Stage 6: Defect Investigation & Root Cause Triage
- **Entry Point:** `app/bugs/investigator.py:BugInvestigator.investigate_failure()`
- **Input:** Failed `ExecutionResult` and corresponding `TestCase`.
- **Implementation:**
  - Evaluates objective alignment check: ensures non-positive tests (visibility, masking, validation) are not expecting authentication navigation.
  - Re-executes the failing action in an isolated clean browser context.
  - Inspects browser console error logs and network status codes.
  - Classifies finding into `PRODUCT_DEFECT`, `TEST_DEFINITION_ERROR`, `AUTOMATION_FLAW`, or `ENVIRONMENT_ISSUE`.
- **Output:** `BugReport` with reproduction rate, suspected cause, and suggested fix.
- **Status:** **Fully Implemented & Verified**.

### Stage 7: Reporting, Script Export, & Dashboard Rendering
- **Entry Point:** `app/export/playwright_exporter.py:PlaywrightExporter.export_to_python_script()`
- **Input:** Approved `test_cases`, target URL.
- **Implementation:** Formats Python source code importing `pytest` and `playwright.async_api`, synthesizing self-contained async test functions.
- **Output:** Exportable Python script file downloaded via `/api/run/{run_id}/export`.
- **Status:** **Fully Implemented & Verified**.
