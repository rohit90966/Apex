# VerityQA — Autonomous AI QA Engineering Platform

**Problem Statement:** iMocha CODE APEX 3.0 — IM-02: Autonomous AI QA Engineer  
**Status:** See [`docs/MULTI_WEBSITE_GENERALIZATION_REPORT.md`](docs/MULTI_WEBSITE_GENERALIZATION_REPORT.md) for the latest measured regression-suite and multi-website benchmark results  
**Primary Engine:** Google Gemini (`gemini-flash-latest`) + 7-Node Cyclic LangGraph + Microsoft Playwright  
**Local Engine (Optional):** Ollama (`llama3.2:3b` / `qwen2.5:7b`)  

---

## 📖 Comprehensive Documentation Suite

Complete, code-grounded technical documentation is organized in the [`docs/`](docs/) directory:

- 🗺️ **[`docs/CODEBASE_MAP.md`](docs/CODEBASE_MAP.md)**: Recursive repository map, component taxonomy, and subsystem status.
- 🔄 **[`docs/END_TO_END_EXECUTION.md`](docs/END_TO_END_EXECUTION.md)**: Sequence diagrams, LangGraph execution lifecycle trace, and diagnostic flow.
- 🏛️ **[`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)**: Node-by-node agent specifications, state machine, and technology rationale.
- 🧪 **[`docs/TEST_AND_VALIDATION_REPORT.md`](docs/TEST_AND_VALIDATION_REPORT.md)**: Complete test audit showing all 296 passing regression tests.
- 📘 **[`docs/FINAL_IMPLEMENTATION_GUIDE.md`](docs/FINAL_IMPLEMENTATION_GUIDE.md)**: Detailed engineering reference for all subsystems.
- 🌐 **[`docs/API_REFERENCE.md`](docs/API_REFERENCE.md)**: FastAPI endpoints, Pydantic v2 schemas, parameters, and status codes.
- ⚙️ **[`docs/CONFIGURATION_AND_SETUP.md`](docs/CONFIGURATION_AND_SETUP.md)**: Installation, environment flags, port map, and startup commands.
- 🏆 **[`docs/DEMO_AND_JUDGE_DEFENSE.md`](docs/DEMO_AND_JUDGE_DEFENSE.md)**: 2-minute live demo script, 5-minute presentation, and 25+ judge Q&As.
- ⚠️ **[`docs/CURRENT_LIMITATIONS.md`](docs/CURRENT_LIMITATIONS.md)**: Verified technical boundaries and future post-hackathon roadmap.
- 📌 **[`docs/PROJECT_FACTS.md`](docs/PROJECT_FACTS.md)**: Ultra-compact single source of truth for all project metrics and facts.

---

## ⚡ Quick Start

### 1. Prerequisites & Installation
```powershell
# Clone and enter workspace
git clone <repo-url>
cd "Code Apex"

# Activate your Python environment (Python 3.10+)
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment (`.env`)
Create a `.env` file in the project root:
```dotenv
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-flash-latest
LLM_PROVIDER=gemini
APP_HOST=127.0.0.1
APP_PORT=8000
DEFAULT_TIMEOUT_MS=30000
VERITYQA_MAX_TEST_CASES=6
ALLOW_EXTERNAL_HOSTS=true
ALLOWED_HOSTS=127.0.0.1,localhost,testfire.net,saucedemo.com
REPORT_DIR=reports
```

### 3. Launch Services

#### Terminal 1: VerityQA Backend & Dashboard
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
👉 **Dashboard:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

#### Terminal 2: Demo Fixture 1 (Auth & Search with Planted Defects)
```powershell
python -m uvicorn demo_app.main:app --host 127.0.0.1 --port 8001
```
👉 Target: [http://127.0.0.1:8001/](http://127.0.0.1:8001/)

#### Terminal 3: Demo Fixture 2 (TaskDash Multi-Page Dashboard)
```powershell
python -m uvicorn demo_app.fixture2.main:app --host 127.0.0.1 --port 8002
```
👉 Target: [http://127.0.0.1:8002/](http://127.0.0.1:8002/)

---

## 🧪 Automated Regression & Benchmark Suites

Run the full automated regression test suite:
```powershell
python -m pytest tests/ -q
```

Run the multi-website benchmark (starts the local fixtures itself, bug-enabled and baseline builds,
then scores findings against evaluator-only ground truth in `benchmarks/ground_truth.py`):
```powershell
python scripts/run_multi_website_benchmark.py                          # all local fixtures
python scripts/run_multi_website_benchmark.py --targets taskdash --repeats 2
python scripts/run_multi_website_benchmark.py --include-public         # adds SauceDemo (network)
```
Outputs: `reports/multi_website_benchmark.{json,md}`, `reports/multi_website_findings.csv`,
`reports/benchmark_exports/<run_id>.py` (the exported Playwright suites, executed as part of the run).

Each fixture supports `VERITYQA_FIXTURE_MODE=baseline` (defect-free build). Test credentials are scoped
to one origin via `VERITYQA_TEST_CREDENTIALS_ORIGIN` (see `.env.example`).
