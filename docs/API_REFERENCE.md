# VerityQA — API Reference

VerityQA exposes a synchronous and asynchronous RESTful API via FastAPI (`app/main.py`), running by default on `http://127.0.0.1:8000`.

All request and response bodies use JSON encoding conforming to Pydantic v2 data models defined in [`app/schemas.py`](file:///d:/Hackathon/Code%20Apex/app/schemas.py) and [`app/models.py`](file:///d:/Hackathon/Code%20Apex/app/models.py).

---

## 1. Overview of Endpoints

| Method | Endpoint | Description | Request Model | Response Model |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/health` | Liveness and health probe | None | `HealthCheckResponse` |
| `POST` | `/api/inspect` | Inspects DOM, classifies page, crawls graph, and generates evidence-grounded test plan | `InspectionRequest` | `InspectionResponse` |
| `POST` | `/api/run` | Full autonomous QA pipeline: inspect $\to$ plan $\to$ execute in Playwright $\to$ verify oracle $\to$ triage bugs $\to$ export script | `RunRequest` | `RunResponse` |
| `GET` | `/api/run/{run_id}/export` | Downloads the generated standalone executable Playwright Python script | Path param `run_id` | `text/x-python` attachment |
| `POST` | `/api/benchmark/run` | Executes benchmark against planted defect test fixtures | `BenchmarkRunRequest` | `BenchmarkRunResult` |
| `GET` | `/` and `/{path:path}` | Serves frontend single-page dashboard (`app/static/index.html`, `app.js`, `style.css`) | HTTP GET | HTML / Static Files |

---

## 2. Detailed Endpoint Documentation

### 2.1 `GET /health`
Liveness health probe to verify API readiness.

- **Request**: No parameters or body.
- **Status Code**: `200 OK`
- **Response Schema (`HealthCheckResponse`)**:
```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

### 2.2 `POST /api/inspect`
Inspects a target website, crawls reachable pages up to depth and page limits, determines semantic page classification with confidence metrics, runs the 7-node LangGraph test planning agent, and validates the generated test plan.

- **URL Validation Rules**:
  - Host must be permitted by [`app/config.py`](file:///d:/Hackathon/Code%20Apex/app/config.py) safety policies (`ALLOW_EXTERNAL_HOSTS` or `ALLOWED_HOSTS` including `127.0.0.1`, `localhost`, `testfire.net`, `saucedemo.com`).
  - Scheme must be `http` or `https`.
  - Non-resolving or forbidden hosts return `400 Bad Request`.

- **Request Schema (`InspectionRequest`)**:
```json
{
  "url": "http://127.0.0.1:8002",
  "website_description": "TaskDash task management dashboard application",
  "requirements": [
    "Verify task creation under max length",
    "Verify search filter functionality"
  ],
  "max_pages": 5,
  "max_depth": 2,
  "llm_provider": "gemini"
}
```
*Field descriptions:*
- `url` (*str*, required): The target website entry URL.
- `website_description` (*str*, optional): High-level context of target domain.
- `requirements` (*list[str]*, optional): Explicit functional requirements or acceptance criteria.
- `max_pages` (*int*, optional, default: 5): Maximum pages to discover during deep crawl.
- `max_depth` (*int*, optional, default: 2): Maximum BFS link distance from entry point.
- `llm_provider` (*str*, optional, default: "gemini"): Inference provider (`gemini` or `ollama`).

- **Response Schema (`InspectionResponse`)**:
```json
{
  "page_title": "TaskDash — Dashboard",
  "final_url": "http://127.0.0.1:8002/",
  "http_status": 200,
  "inspection_duration_seconds": 2.85,
  "page_classification": "GENERAL",
  "classification_confidence": 0.60,
  "classification_evidence": [
    "Broad navigation links and heading layout present."
  ],
  "headings": ["Task Overview", "Recent Activities"],
  "forms": [
    {
      "id": "new-task-form",
      "action": "/api/tasks",
      "method": "POST",
      "inputs": [
        {
          "name": "title",
          "type": "text",
          "placeholder": "Task title...",
          "required": true,
          "maxlength": 100
        }
      ]
    }
  ],
  "inputs": [
    {
      "name": "title",
      "type": "text",
      "id": "task-title-input",
      "placeholder": "Task title...",
      "required": true,
      "maxlength": 100
    }
  ],
  "buttons": [
    {
      "id": "submit-btn",
      "text": "Add Task",
      "type": "submit"
    }
  ],
  "links": [
    {
      "text": "Settings",
      "href": "/settings"
    }
  ],
  "discovered_pages": [
    {
      "url": "http://127.0.0.1:8002/",
      "title": "TaskDash — Dashboard",
      "depth": 0
    },
    {
      "url": "http://127.0.0.1:8002/settings",
      "title": "TaskDash — Settings",
      "depth": 1
    }
  ],
  "test_plan": {
    "target_url": "http://127.0.0.1:8002",
    "test_cases": [
      {
        "id": "TC-01",
        "title": "Dashboard Page Load and Console Integrity",
        "objective": "Verify dashboard loads without unhandled exceptions",
        "action": "Navigate to http://127.0.0.1:8002 and monitor console errors",
        "expected_outcome": "DOM reaches complete with zero console errors",
        "priority": "HIGH",
        "category": "INFERRED",
        "status": "PROPOSED",
        "relevant_elements": ["document", "window"],
        "evidence_to_collect": ["DOM attribute", "Client UI state"],
        "selection_rationale": "Derived from strategy and DOM evidence"
      }
    ]
  },
  "planning_diagnostics": {
    "total_planning_seconds": 21.07,
    "tokens_per_second": 55.9,
    "current_stage": "PLAN_VALIDATION",
    "critic_revisions_count": 0,
    "proposed_test_cases_count": 6,
    "patterns_applied": 1,
    "constraint_records_count": 1,
    "deterministic_ideas_count": 3
  },
  "critic_evaluation": {
    "passed": true,
    "review_feedback": "Critic review passed: test plan is grounded in evidence.",
    "issues_found": []
  },
  "screenshot_path": "reports/screenshots/inspect_abc123.png"
}
```

---

### 2.3 `POST /api/run`
Executes an end-to-end autonomous QA run. Combines inspection, graph crawling, test planning, Playwright execution, oracle verification, failure triage, deduplication, and export generation.

- **Request Schema (`RunRequest`)**:
```json
{
  "url": "https://www.saucedemo.com/",
  "website_description": "E-commerce login portal",
  "requirements": [
    "Verify presence and visibility of login form controls",
    "Verify password masking invariant",
    "Verify empty credentials validation"
  ],
  "max_pages": 3,
  "max_depth": 2,
  "headless": true,
  "browser": "chromium",
  "llm_provider": "gemini"
}
```

- **Response Schema (`RunResponse`)**:
```json
{
  "run_id": "run_20261017_123456_abc",
  "status": "COMPLETED",
  "duration_seconds": 42.15,
  "target_url": "https://www.saucedemo.com/",
  "inspection_summary": { ... },
  "test_plan": { ... },
  "execution_results": [
    {
      "test_case_id": "TC-01",
      "title": "Verify presence and visibility of essential login form controls",
      "status": "PASSED",
      "duration_seconds": 3.12,
      "logs": [
        "Navigating to https://www.saucedemo.com/",
        "Resolved input[name='user-name'] via PRIMARY_CSS",
        "Element input[name='user-name'] is visible"
      ],
      "error_message": null,
      "stack_trace": null,
      "network_logs": [],
      "console_errors": [],
      "screenshot_path": "reports/screenshots/TC-01_passed.png"
    }
  ],
  "bug_reports": [
    {
      "id": "BUG-01",
      "title": "Uncaught TypeError on submitting empty form",
      "severity": "HIGH",
      "category": "CRASH",
      "description": "Submitting login form causes uncaught script error in bundle.js",
      "reproduction_steps": [
        "1. Navigate to https://www.saucedemo.com/",
        "2. Click Login without typing credentials"
      ],
      "expected_outcome": "Validation error displayed gracefully",
      "actual_outcome": "Uncaught TypeError: Cannot read property of undefined",
      "evidence_artifacts": [
        "reports/screenshots/TC-03_failed.png",
        "reports/logs/TC-03_console.log"
      ],
      "suspected_component": "app/login.js:42",
      "is_duplicate": false
    }
  ],
  "metrics": {
    "total_tests": 6,
    "passed_tests": 5,
    "failed_tests": 1,
    "error_tests": 0,
    "pass_rate": 83.3,
    "bugs_detected": 1,
    "unique_bugs": 1
  },
  "export_script_url": "/api/run/run_20261017_123456_abc/export"
}
```

---

### 2.4 `GET /api/run/{run_id}/export`
Downloads a self-contained, standalone Python script using `playwright.sync_api` or `playwright.async_api` that can be run outside of VerityQA.

- **Path Parameters**:
  - `run_id` (*str*): The unique run identifier returned from `POST /api/run`.
- **Response**:
  - `Content-Type: text/x-python`
  - `Content-Disposition: attachment; filename="verityqa_test_{run_id}.py"`
  - Body: Python source code implementing all generated test cases with assertions, locators, and screenshots.
- **Error Codes**:
  - `404 Not Found`: If `run_id` does not exist in local session storage or memory.

---

### 2.5 `POST /api/benchmark/run`
Executes autonomous evaluation against built-in benchmark fixtures containing planted defects. Computes precision, recall, bug detection rate, and execution efficiency.

- **Request Schema (`BenchmarkRunRequest`)**:
```json
{
  "fixture_name": "fixture1",
  "iterations": 1,
  "headless": true
}
```
*Supported fixture names: `fixture1` (Buggy Auth & Search), `fixture2` (TaskDash Dashboard).*

- **Response Schema (`BenchmarkRunResult`)**:
```json
{
  "benchmark_id": "bm_20261017_120000",
  "fixture_name": "fixture1",
  "duration_seconds": 18.4,
  "planted_bugs_count": 4,
  "detected_bugs_count": 4,
  "detection_rate": 1.0,
  "false_positive_count": 0,
  "precision": 1.0,
  "recall": 1.0,
  "f1_score": 1.0,
  "detailed_findings": [
    {
      "planted_id": "BUG-PLANTED-01",
      "detected": true,
      "matched_bug_title": "SQL Syntax Error displayed on special characters input",
      "severity": "CRITICAL"
    }
  ]
}
```

---

## 3. Error Handling and Status Codes

VerityQA adheres to standard HTTP status codes:

| Code | Meaning | Cause | Example Response Body |
| :--- | :--- | :--- | :--- |
| `200` | OK | Successful operation | JSON payload according to endpoint schema |
| `400` | Bad Request | Disallowed URL host, malformed payload, missing target URL | `{"detail": "Host 'malicious-site.com' not allowed by safety policy"}` |
| `404` | Not Found | Export requested for non-existent `run_id` | `{"detail": "Run run_invalid_123 not found"}` |
| `422` | Unprocessable Entity | Pydantic schema validation failure | `{"detail": [{"loc": ["body", "url"], "msg": "field required"}]}` |
| `500` | Internal Server Error | Uncaught browser crash or upstream LLM outage | `{"detail": "Playwright navigation timeout after 30000ms"}` |
