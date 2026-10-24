# VerityQA — Current Limitations and Future Engineering Roadmap

This document provides a candid, technically precise audit of the verified limitations of VerityQA, boundaries of the current prototype, external dependencies, and recommended engineering solutions for post-hackathon enterprise production.

---

## 1. Verified Prototype Limitations

### 1.1 In-Process Synchronous API Execution
- **Current Behavior**: Endpoints like `POST /api/inspect` and `POST /api/run` execute within the FastAPI request-response thread loop. While asynchronous within the event loop, the HTTP client holds an open connection for 20–45 seconds while LangGraph and Playwright complete.
- **Impact**: If a user submits a massive target or network latency is high, long-running requests can time out if behind an aggressive reverse proxy (e.g., standard 30s Nginx/Cloudflare timeouts).
- **Roadmap Fix**: Introduce an asynchronous task queue (e.g., Celery or ARQ with Redis), returning a `202 Accepted` with a `task_id` and streaming progress events over Server-Sent Events (SSE) or WebSockets.

### 1.2 In-Memory Session Storage
- **Current Behavior**: Historical run artifacts and exports are referenced via in-memory dictionaries and local filesystem storage under `reports/`.
- **Impact**: If the backend process restarts, historical run states in memory are lost (though files on disk remain).
- **Roadmap Fix**: Replace in-memory dictionaries with a persistent SQLite or PostgreSQL database managed by SQLAlchemy/Alembic.

### 1.3 Canvas and WebGL Shadow DOM Limitations
- **Current Behavior**: The crawler and locator engine inspect standard HTML5 DOM trees and open Shadow DOM trees.
- **Impact**: Elements rendered inside HTML5 `<canvas>` elements (e.g., WebGL games, Google Maps, complex charting libraries) do not expose individual DOM nodes for inspection.
- **Roadmap Fix**: Integrate multimodal visual grounding models (e.g., Gemini Flash Vision coordinate-based clicking) for canvas-only surfaces.

### 1.4 Complex Multi-Factor Authentication (MFA) & CAPTCHAs
- **Current Behavior**: The crawler supports standard username/password forms, basic HTTP auth, and pre-seeded cookies/storage state.
- **Impact**: Applications protected by Cloudflare Turnstile, reCAPTCHA, or SMS/TOTP MFA cannot be autonomously traversed from a cold start.
- **Roadmap Fix**: Implement a "Seed Session" feature allowing QA engineers to log in once via an interactive browser window and persist the authenticated `storage_state.json` profile for subsequent autonomous runs.

### 1.5 Client-Side Single Page Application (SPA) Deep State Exploration
- **Current Behavior**: The crawler navigates through internal `<a>` links and form submissions.
- **Impact**: Highly interactive SPAs that alter application state exclusively via JavaScript `div` click handlers or canvas triggers without changing the URL or anchor tags require explicit requirement hints.
- **Roadmap Fix**: Implement an event-driven monkey-testing / interaction exploration mode that scans for clickable elements with `cursor: pointer` or `@click` event listeners.

---

## 2. External Dependencies & Operational Requirements

| Dependency | Purpose | Failure Mode if Unavailable | Mitigation |
| :--- | :--- | :--- | :--- |
| **Playwright Chromium** | Browser automation & DOM rendering | `playwright._impl._errors.Error: Executable doesn't exist` | Run `playwright install chromium` during installation. |
| **Google Gemini API** | LLM reasoning & test planning | `API key not found` or HTTP 429 quota exhaustion | Fall back to local Ollama instance (`LLM_PROVIDER=ollama`). |
| **Local Port 8000** | FastAPI dashboard service | `OSError: [Errno 98] Address already in use` | Configure custom port via `APP_PORT` in `.env`. |
| **Local Ports 8001/8002** | Demo fixtures | Targets unreachable if not launched | VerityQA safely reports `400 Bad Request` or target connection failure. |

---

## 3. Security & Safety Governance

- **Safety Whitelist**: VerityQA restricts crawled and tested domains to `127.0.0.1`, `localhost`, `testfire.net`, `saucedemo.com`, or domains explicitly enabled via `ALLOW_EXTERNAL_HOSTS=true`.
- **Private IP Protection**: Prevents SSRF attacks against cloud metadata services (`169.254.169.254`) and internal subnets.
- **No Secret Storage**: Passwords and credentials used in testing are sanitized or injected at runtime; no secrets are persisted into export scripts or test logs.

---

## 4. Engineering Roadmap Beyond Round 2

1. **Distributed Multi-Worker Execution**: Scale Playwright test execution across Dockerized browser pods using Selenium Grid or Playwright Browserless.
2. **Visual Regression Testing**: Integrate pixel-diff and visual perceptual hashing (SSIM) to detect styling and layout regressions across releases.
3. **Automated CI/CD GitHub Action**: Package VerityQA into a zero-configuration GitHub Action that inspects PR deployment preview URLs and blocks merging on critical regressions.
