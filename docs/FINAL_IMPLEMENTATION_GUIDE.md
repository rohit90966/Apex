# VerityQA — Complete Implementation Guide

## 1. What is VerityQA?

VerityQA is an **observation-driven, autonomous AI Quality Assurance engineering system**. 

It eliminates the two biggest failure modes of modern automated testing:
1. **Brittle manual scripts** that break whenever UI IDs or styling change.
2. **Hallucinated AI tests** that invent nonexistent business rules, assume ungrounded error messages, or fail for the wrong reasons.

VerityQA operates on a simple, uncompromising principle:
> **The LLM proposes. Deterministic code executes. Evidence decides.**

---

## 2. High-Level Architecture & Lifecycle

VerityQA moves from an uninspected URL to a verified regression suite through an autonomous 6-stage lifecycle:

```
[Target URL]
     │
     ▼
[1. Playwright Inspection & Multi-Page Crawler]
     │   (DOM tree, forms, inputs, buttons, accessibility tree, screenshots)
     ▼
[2. Deterministic Derivation & Boundary Value Engine]
     │   (Pure-Python N-1, N, N+1 calculations; presence & masking invariants)
     ▼
[3. Multi-Agent Planning Graph (LangGraph + Gemini Flash)]
     │   (Requirement Analyst → Risk Analyst → Strategy Planner → Plan Generator → QA Critic → Validator)
     ▼
[4. Playwright Browser Execution & Smart Locator Grounding]
     │   (Action step synthesis; data-testid → semantic ID → aria-label cascade)
     ▼
[5. Autonomous Bug Investigation & Defect Triage]
     │   (Isolated re-runs; PRODUCT_DEFECT vs. TEST_DEFINITION_ERROR vs. AUTOMATION_FLAW)
     ▼
[6. Dashboard Visualization & Python Playwright Script Export]
```

---

## 3. Subsystem Breakdown

### 3.1 Website Inspection & Crawling
- **Files:** [`app/inspector.py`](file:///d:/Hackathon/Code%20Apex/app/inspector.py), [`app/investigation/crawler.py`](file:///d:/Hackathon/Code%20Apex/app/investigation/crawler.py)
- **Classes:** `WebsiteInspector`, `WebsiteCrawler`
- **Mechanism:** Launches headless Playwright Chromium. It navigates to the starting URL, waits for network idle, extracts forms, inputs, buttons, headings, and links, and builds an accessibility tree. The crawler follows internal links to a bounded depth ($D_{\text{max}}=2$, $P_{\text{max}}=10$) to build a multi-page inventory.

### 3.2 Deterministic Boundary Value Engine (BVA)
- **File:** [`app/evidence/derivation/boundary_value.py`](file:///d:/Hackathon/Code%20Apex/app/evidence/derivation/boundary_value.py)
- **Class:** `BoundaryValueEngine`
- **Mechanism:** Pure-Python mathematical calculation. Inspects HTML attributes:
  - `maxlength=100` $\rightarrow$ derives $\{99, 100, 101\}$.
  - `minlength=6` $\rightarrow$ derives $\{5, 6, 7\}$.
  - Numeric `min`/`max` $\rightarrow$ derives values below, at, and above.
  - `required` $\rightarrow$ derives empty-submission rejection.
  If an input has no constraints, it **refuses to invent numbers**, recording explicit entries in the Unknowns ledger instead.

### 3.3 Multi-Agent LangGraph Planning Workflow
- **Files:** [`app/agents/workflow.py`](file:///d:/Hackathon/Code%20Apex/app/agents/workflow.py), [`app/agents/plan_generator.py`](file:///d:/Hackathon/Code%20Apex/app/agents/plan_generator.py), [`app/agents/qa_critic.py`](file:///d:/Hackathon/Code%20Apex/app/agents/qa_critic.py), [`app/agents/plan_validator.py`](file:///d:/Hackathon/Code%20Apex/app/agents/plan_validator.py)
- **Mechanism:** Stateful computational graph using `AgentWorkflowState`.
  - `RequirementAnalystNode`: Parses explicit user instructions.
  - `RiskAnalystNode`: Identifies security, negative input, accessibility, and integrity risks.
  - `PlanGeneratorNode`: Calls Gemini 2.5 Flash requesting target test counts (default 6) covering 6 distinct dimensions.
  - `QACriticNode`: Semantic auditor enforcing DOM grounding and triggering bounded revisions (capped at 2).
  - `PlanValidatorNode`: Pure-Python schema validator stripping dangerous code execution tokens (`exec`, `eval`) and deduplicating scenarios.

### 3.4 Zero-Downtime Deterministic Fallback Planner
- **File:** [`app/test_planner.py`](file:///d:/Hackathon/Code%20Apex/app/test_planner.py)
- **Class:** `DeterministicPlanner`
- **Mechanism:** If Gemini API experiences rate limits (HTTP 429), timeouts, or network loss, the workflow instantly engages rule-based planning, synthesizing 6 verified test cases using deterministic heuristics without dropping the run.

### 3.5 Playwright Test Runner & Smart Locators
- **Files:** [`app/execution/runner.py`](file:///d:/Hackathon/Code%20Apex/app/execution/runner.py), [`app/execution/locator.py`](file:///d:/Hackathon/Code%20Apex/app/execution/locator.py), [`app/execution/executor.py`](file:///d:/Hackathon/Code%20Apex/app/execution/executor.py)
- **Classes:** `QAPlanRunner`, `SmartLocator`, `BrowserExecutor`
- **Mechanism:** Compiles high-level test cases into concrete `ActionStep` sequences.
  - Selectors are resolved via a weighted resilience cascade: `data-testid` $\rightarrow$ semantic ID $\rightarrow$ `aria-label` $\rightarrow$ button text $\rightarrow$ placeholder.
  - Step synthesis generates targeted assertions: password masking checks `INPUT_MASKED`, page load checks `ELEMENT_VISIBLE` on body, empty submit checks `VALIDITY_VALUE_MISSING`.

### 3.6 Autonomous Bug Investigator & Defect Classification
- **File:** [`app/bugs/investigator.py`](file:///d:/Hackathon/Code%20Apex/app/bugs/investigator.py)
- **Class:** `BugInvestigator`
- **Mechanism:** When an assertion fails, it performs automated triage:
  1. **Objective Alignment:** Checks whether the test objective matches the assertion (e.g. catches tests expecting `/inventory` on a visibility check).
  2. **Isolated Reproduction:** Re-runs the failing step in a clean, isolated browser context.
  3. **Console Telemetry:** Inspects browser console errors and HTTP status codes.
  4. **Classification:** Categorizes into:
     - `PRODUCT_DEFECT`: Verified reproducible application bug.
     - `TEST_DEFINITION_ERROR`: Invalid or ungrounded test expectation.
     - `AUTOMATION_FLAW`: Flaky timeout or selector drift.
     - `ENVIRONMENT_ISSUE`: Server down or network failure.

### 3.7 Standalone Playwright Script Exporter
- **File:** [`app/export/playwright_exporter.py`](file:///d:/Hackathon/Code%20Apex/app/export/playwright_exporter.py)
- **Class:** `PlaywrightExporter`
- **Mechanism:** Exports approved test cases into a standalone, executable Python script (`test_suite.py`) using `pytest` and `playwright.async_api`, ready to commit directly to GitHub Actions or Jenkins.

---

## 4. How to Run and Test the System

1. **Start the Backend:**
   ```powershell
   conda run -n langgraph python -m uvicorn app.main:app --port 8000 --reload
   ```
2. **Start the Demo Application (TaskDash Fixture):**
   ```powershell
   conda run -n langgraph python -m uvicorn demo_app.fixture2.main:app --port 8002 --host 127.0.0.1
   ```
3. **Open the Dashboard:**
   Navigate to `http://localhost:8000` in any web browser.
4. **Run the Automated Test Suite:**
   ```powershell
   conda run -n langgraph python -m pytest tests/ -q
   ```
