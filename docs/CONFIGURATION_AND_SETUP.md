# VerityQA — Configuration, Environment Setup, and Deployment Guide

This guide details all prerequisites, environment variables, startup commands, port allocations, and testing workflows for VerityQA.

---

## 1. System Requirements & Prerequisites

- **Operating System**: Windows 10/11, macOS 12+, or Ubuntu 20.04+ (Windows tested in this repository).
- **Python**: Python 3.10, 3.11, or 3.12 (Micromamba / Conda environment `langgraph` used during validation).
- **Node / Browser Binaries**: Microsoft Playwright browser binaries (Chromium required).
- **LLM Access**: Google Gemini API key (`GEMINI_API_KEY`) or a local running Ollama instance (`http://127.0.0.1:11434`).

---

## 2. Environment Variables Configuration

Create or update `.env` in the repository root (`d:\Hackathon\Code Apex\.env`). Below are the supported configuration flags defined in [`app/config.py`](file:///d:/Hackathon/Code%20Apex/app/config.py):

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | *(None / Required for Gemini)* | Google Gemini API authorization token |
| `GEMINI_MODEL` | `gemini-flash-latest` | Model used for agent reasoning and planning |
| `LLM_PROVIDER` | `gemini` | Primary LLM provider (`gemini` or `ollama`) |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Endpoint for local Ollama server if active |
| `OLLAMA_MODEL` | `llama3.2:3b` | Local model name if running via Ollama |
| `APP_HOST` | `127.0.0.1` | Host address for FastAPI backend |
| `APP_PORT` | `8000` | Port for FastAPI backend |
| `DEFAULT_TIMEOUT_MS` | `30000` | Playwright navigation and selector timeout (ms) |
| `VERITYQA_MAX_TEST_CASES` | `6` | Upper limit of generated test cases per plan |
| `ALLOW_EXTERNAL_HOSTS` | `true` | Allows external target sites (`saucedemo.com`, etc.) |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost,testfire.net,saucedemo.com` | Comma-separated whitelist of allowed inspection domains |
| `REPORT_DIR` | `reports` | Directory where logs, screenshots, and export files are saved |

### Example `.env` File
```bash
GEMINI_API_KEY=AIzaSy...YourKeyHere...
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

---

## 3. Installation Steps

### 3.1 Setup Virtual Environment
Using Conda / Micromamba:
```powershell
conda create -n langgraph python=3.11 -y
conda activate langgraph
```

Or using standard Python venv:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3.2 Install Dependencies
Install the required packages:
```powershell
pip install -r requirements.txt
```

### 3.3 Install Playwright Browser Binaries
VerityQA requires Playwright Chromium:
```powershell
playwright install chromium
```

---

## 4. Port Allocation & Services Architecture

VerityQA consists of the main application backend and optional local target fixtures for reproducible benchmarking and demonstrations:

| Service / App | Default Port | Entry Script | Purpose |
| :--- | :--- | :--- | :--- |
| **VerityQA Platform** | `8000` | `app/main.py` | FastAPI REST API + UI Dashboard |
| **Demo Fixture 1** | `8001` | `demo_app/main.py` | Authentication & Search Target (contains planted bugs) |
| **Demo Fixture 2** | `8002` | `demo_app/fixture2/main.py` | TaskDash Dashboard & Form Target (`maxlength=100`) |

---

## 5. Startup Commands

Open separate terminal windows for the services you wish to run:

### Terminal 1: Start Main VerityQA Server
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Once started, access the Web Dashboard at:  
👉 **`http://127.0.0.1:8000/`**

### Terminal 2: Start Demo Fixture 1 (Auth & Search)
```powershell
python -m uvicorn demo_app.main:app --host 127.0.0.1 --port 8001
```
Access target at: `http://127.0.0.1:8001/`

### Terminal 3: Start Demo Fixture 2 (TaskDash Dashboard)
```powershell
python -m uvicorn demo_app.fixture2.main:app --host 127.0.0.1 --port 8002
```
Access target at: `http://127.0.0.1:8002/`

---

## 6. Running Tests & Benchmarks

### 6.1 Run the Full Regression Suite
The test suite consists of 296 unit, integration, agentic, and locator tests:
```powershell
python -m pytest tests/ -q
```
*Expected Output: `296 passed in ~85-90s`.*

### 6.2 Run Targeted Agentic Tests
To verify the LangGraph 7-node planning pipeline specifically:
```powershell
python -m pytest tests/test_agent_workflow.py tests/test_planning_agent.py -q
```

### 6.3 Run Targeted Execution & Locator Tests
To verify Playwright resilient element locators and step synthesis:
```powershell
python -m pytest tests/test_execution_runner.py tests/test_locator_resilience.py -q
```

### 6.4 Run the Planted Defect Benchmark
To execute the automated benchmark against the demo fixtures:
```powershell
python -m pytest tests/test_benchmark_runner.py -q
```
Or trigger it via curl:
```powershell
curl -X POST http://127.0.0.1:8000/api/benchmark/run -H "Content-Type: application/json" -d '{"fixture_name": "fixture1"}'
```
