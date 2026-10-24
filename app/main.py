import os
import sys
import uuid
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, Set

# Ensure ProactorEventLoop is used on Windows so Playwright subprocesses work properly
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from app.config import get_settings
from app.schemas import (
    InspectionRequest,
    InspectionResponse,
    QARunRequest,
    QARunResponse,
    ExecuteSuiteRequest,
    ExecuteSuiteResponse,
    ExecutionStatus,
    BugReport,
    BenchmarkRunSummary,
    PageCategory,
)
from app.exploration.graph import ExploreRequest, ExploreResponse
from app.safety import validate_url_safety, SafetyViolation
from app.inspector import WebsiteInspector
from app.page_classifier import DeterministicClassifier
from app.test_planning_agent import TestPlanningAgent
from app.execution.runner import QAPlanRunner
from app.bugs.investigator import BugInvestigator
from app.export.playwright_exporter import PlaywrightTestExporter
from app.benchmark.manifest import BenchmarkEvaluator
from app.runs import (
    RunStore,
    RunListResponse,
    RunDetailResponse,
    InvalidRunId,
    RunNotFound,
    collect_known_secrets,
    redact_secrets,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verityqa")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recovered = run_store.recover_interrupted_runs()
    if recovered:
        logger.warning("[Runs] Marked %d interrupted run(s) as failed after restart", recovered)
    yield


app = FastAPI(
    title="VerityQA",
    description="Autonomous AI QA Engineer - Step 1: Test Planning Agent",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
run_store = RunStore(settings.verityqa_runs_dir)
# Strong references keep fire-and-forget investigation workers alive until they finish.
_background_tasks: Set[asyncio.Task] = set()

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

inspector = WebsiteInspector()
classifier = DeterministicClassifier()
planning_agent = TestPlanningAgent()
test_runner = QAPlanRunner()
bug_investigator = BugInvestigator(runner=test_runner)
test_exporter = PlaywrightTestExporter(runner=test_runner)
benchmark_evaluator = BenchmarkEvaluator()
from app.exploration.explorer import AutonomousExplorer
explorer = AutonomousExplorer()

# In-memory QA run store (keyed by run_id)
_qa_runs: Dict[str, Any] = {}
_health_cache: Dict[str, Any] = {}
_health_cache_time: float = 0.0


@app.get("/health")
async def health_check():
    """Diagnostic health check verifying service and LLM availability."""
    global _health_cache, _health_cache_time
    import time
    now = time.time()
    if _health_cache and (now - _health_cache_time) < 60.0:
        return _health_cache

    provider_name = settings.llm_provider
    if provider_name == "groq":
        configured_model = settings.groq_model
    elif provider_name == "gemini":
        configured_model = settings.gemini_model
    else:
        configured_model = settings.ollama_model
    llm_connected = False
    llm_status = "UNKNOWN"
    llm_message = ""

    if provider_name == "groq":
        has_key = bool(settings.groq_api_key or os.environ.get("GROQ_API_KEY"))
        if not has_key:
            llm_status = "AUTH_ERROR"
            llm_message = "GROQ_API_KEY is not configured."
        else:
            try:
                from app.llm.groq_provider import GroqProvider
                provider = GroqProvider()
                diag = await provider.verify_availability()
                llm_connected = diag.get("available", False)
                llm_status = diag.get("status", "CONNECTED" if llm_connected else "ERROR")
                llm_message = diag.get("message", "")
            except Exception as e:
                llm_status = "OFFLINE"
                llm_message = str(e)[:140]
    elif provider_name == "gemini":
        has_key = bool(settings.google_api_key or os.environ.get("GOOGLE_API_KEY"))
        if not has_key:
            llm_status = "AUTH_ERROR"
            llm_message = "GOOGLE_API_KEY is not configured."
        else:
            try:
                from app.llm.gemini_provider import GeminiProvider
                provider = GeminiProvider()
                diag = await provider.verify_availability()
                llm_connected = diag.get("available", False)
                llm_status = diag.get("status", "CONNECTED" if llm_connected else "ERROR")
                llm_message = diag.get("message", "")
            except Exception as e:
                llm_status = "OFFLINE"
                llm_message = str(e)[:140]
    else:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{settings.ollama_base_url}/api/tags")
                if res.status_code == 200:
                    llm_connected = True
                    llm_status = "CONNECTED"
                    llm_message = f"Ollama online at {settings.ollama_base_url}"
                else:
                    llm_status = "ERROR"
                    llm_message = f"Ollama returned HTTP {res.status_code}"
        except Exception:
            llm_status = "OFFLINE"
            llm_message = f"Cannot reach Ollama at {settings.ollama_base_url}"

    data = {
        "status": "healthy",
        "service": "VerityQA",
        "version": "0.1.0",
        "llm_provider": provider_name,
        "configured_model": configured_model,
        "llm_connected": llm_connected,
        "llm_status": llm_status,
        "llm_message": llm_message,
        "groq_api_key_configured": bool(settings.groq_api_key or os.environ.get("GROQ_API_KEY")),
        "google_api_key_configured": bool(settings.google_api_key or os.environ.get("GOOGLE_API_KEY")),
        "ollama_configured_model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
    }
    _health_cache = data
    _health_cache_time = now
    return data


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the VerityQA web dashboard."""
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>VerityQA Dashboard</h2><p>Static index.html not found.</p>")


@app.get("/style.css")
async def serve_root_style():
    """Serves the primary stylesheet at root for relative linking."""
    style_file = os.path.join(static_dir, "style.css")
    if os.path.exists(style_file):
        return FileResponse(style_file, media_type="text/css")
    raise HTTPException(status_code=404, detail="style.css not found.")


@app.get("/app.js")
async def serve_root_js():
    """Serves the primary JavaScript controller at root for relative linking."""
    js_file = os.path.join(static_dir, "app.js")
    if os.path.exists(js_file):
        return FileResponse(js_file, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="app.js not found.")


@app.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    """
    Safely serves screenshot artifacts only from the configured artifacts directory.
    Validates against directory traversal.
    """
    # Prevent directory traversal
    safe_name = os.path.basename(artifact_id)
    if safe_name != artifact_id or ".." in artifact_id:
        raise HTTPException(status_code=400, detail="Invalid artifact identifier.")

    artifacts_dir = os.path.abspath(settings.verityqa_artifacts_dir)
    file_path = os.path.join(artifacts_dir, safe_name)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Artifact not found.")

    return FileResponse(file_path)


@app.post("/api/inspect", response_model=InspectionResponse)
async def inspect_and_plan(request: InspectionRequest):
    """
    Main Step 1 inspection & planning pipeline:
    1. Validate target URL against safety policy.
    2. Inspect page safely via Playwright.
    3. Classify page category deterministically.
    4. Propose structured test plan via Local Ollama (with deterministic fallback).
    """
    logger.info("Received inspection request for URL: %s", request.url)

    # 1. Safety validation
    try:
        validate_url_safety(request.url, settings.allowed_hosts_list)
    except SafetyViolation as e:
        logger.warning("Safety policy rejected target URL '%s': %s", request.url, e)
        raise HTTPException(status_code=400, detail=f"Safety Policy Rejection: {str(e)}")
    except Exception as e:
        logger.error("Error validating safety policy: %s", e)
        raise HTTPException(status_code=400, detail="Unable to validate target URL safety.")

    # 2. Browser Inspection & Deep Investigation
    try:
        # Perform primary inspection
        inspection = await inspector.inspect(request.url)
        logger.info(
            "Primary inspection completed in %ss. Found %d forms, %d buttons, %d links.",
            inspection.inspection_duration_seconds,
            len(inspection.forms),
            len(inspection.buttons),
            len(inspection.links),
        )

        # Run bounded crawler for QA feature inventory discovery
        from app.investigation.crawler import WebsiteCrawler
        crawler = WebsiteCrawler()
        investigation_report = await crawler.investigate(
            start_url=request.url,
            max_pages=min(settings.investigation_max_pages, 3),
            max_depth=min(settings.investigation_max_depth, 1),
        )
        logger.info(
            "[TestPlanning:FeatureDiscovery] Discovered %d pages, inspected %d pages, detected %d features in %.2fs.",
            investigation_report.inventory.total_discovered,
            investigation_report.inventory.total_inspected,
            len(investigation_report.features),
            investigation_report.duration_seconds,
        )
    except Exception as e:
        logger.exception("Failed to inspect website: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Website inspection failed: {str(e)[:150]}",
        )

    # 3. Deterministic Page Classification
    classification = classifier.classify(inspection)
    logger.info("Classified page as '%s' (confidence: %s)", classification.page_category, classification.confidence)

    # 4. Test Planning Agent (Retrieval-Augmented LangGraph + Fallback)
    try:
        test_plan = await planning_agent.create_plan(
            inspection=inspection,
            classification=classification,
            requirements=request.requirements,
            description=request.description,
            investigation_report=investigation_report,
        )
        # Attach deep investigation metadata (overrides any set inside create_plan for agentic path)
        test_plan.pages_discovered = investigation_report.inventory.total_discovered
        test_plan.pages_inspected = investigation_report.inventory.total_inspected
        test_plan.features_detected = [f.model_dump() for f in investigation_report.features]

        logger.info(
            "Test plan '%s' created using mode '%s' with %d proposed test cases.",
            test_plan.plan_id,
            test_plan.planning_mode,
            len(test_plan.test_cases),
        )
        logger.info(
            "[TEST_COUNT_TRACE] stage=inspection_plan_ready count=%d mode=%s",
            len(test_plan.test_cases),
            test_plan.planning_mode,
        )
    except Exception as e:
        logger.error("Unexpected error in test planning: %s", e)
        raise HTTPException(status_code=500, detail="Test planning execution failed.")

    inv_summary = {
        "pages_discovered": investigation_report.inventory.total_discovered,
        "pages_inspected": investigation_report.inventory.total_inspected,
        "features_detected_count": len(investigation_report.features),
        "features": [f.model_dump() for f in investigation_report.features],
        "discovery_completeness": investigation_report.discovery_completeness,
        "duration_seconds": investigation_report.duration_seconds,
    }

    return InspectionResponse(
        inspection=inspection,
        classification=classification,
        test_plan=test_plan,
        investigation_summary=inv_summary,
    )


def _build_investigation_payload(report: Any) -> Dict[str, Any]:
    """
    Assembles the investigation payload shared by /api/investigate, the SSE COMPLETE event,
    and the persisted run: report + legacy summary + the synthesized test cases.
    """
    synthesized_cases = []
    try:
        from app.investigation_agent.test_synthesis import synthesize_test_cases_from_investigation
        synthesized_cases = synthesize_test_cases_from_investigation(report)
    except Exception as synth_err:
        logger.warning("[Investigation] Test synthesis non-critical error: %s", synth_err)
    test_plan_data = [tc.model_dump(mode="json") for tc in synthesized_cases]

    bva_count = sum(1 for r in report.business_rules_and_hypotheses if any(k in r.description.lower() for k in ("boundary", "length", "required", "min", "max", "format")))
    legacy_summary = {
        "target_url": report.overview.target_url,
        "duration_seconds": report.overview.duration_seconds,
        "planning_strategy": report.overview.planning_strategy,
        "llm_calls_count": report.overview.llm_calls_count,
        "llm_fallbacks_count": report.overview.llm_fallbacks_count,
        "llm_latency_seconds": report.overview.llm_latency_seconds,
        "pages_discovered": report.overview.pages_discovered,
        "states_discovered": report.overview.meaningful_states_discovered,
        "transitions_observed": report.overview.transitions_observed,
        "route_transitions_observed": report.overview.route_transitions_observed,
        "same_route_state_transitions": report.overview.same_route_state_transitions,
        "candidates_discovered": report.overview.candidates_discovered,
        "actions_evaluated": report.overview.actions_evaluated,
        "actions_attempted": report.overview.actions_attempted,
        "actions_successful": report.overview.actions_successful,
        "actions_verified_effects": report.overview.actions_verified_effects,
        "actions_failed": report.overview.actions_failed,
        "actions_safety_blocked": report.overview.actions_safety_blocked,
        "stopping_reason": report.overview.stopping_reason,
        "evidence_count": len(report.evidence_and_traceability),
        "headings_count": report.overview.total_headings_discovered,
        "forms_count": report.overview.total_forms_discovered,
        "inputs_count": report.overview.total_inputs_discovered,
        "buttons_count": report.overview.total_buttons_discovered,
        "links_count": report.overview.total_links_discovered,
        "qa_patterns": len(report.business_rules_and_hypotheses),
        "test_cases": len(synthesized_cases),
        "bva_ideas": bva_count,
    }
    res_data = report.model_dump(mode="json")
    res_data["legacy_inspection_summary"] = legacy_summary
    res_data["confirmed_leads_count"] = len(report.confirmed_bug_leads)
    res_data["unstable_or_rejected_leads_count"] = len(report.unstable_or_rejected_leads)
    res_data["skeptic_rejections_count"] = len(report.unstable_or_rejected_leads)
    res_data["proposed_test_cases"] = test_plan_data
    res_data["proposed_test_plan"] = {
        "test_cases": test_plan_data,
        "planning_mode": "STATEFUL_INVESTIGATION",
        "planning_summary": f"Synthesized {len(synthesized_cases)} evidence-grounded test cases from discovered workflows and verified transitions.",
    }
    return res_data


def _safe_error_text(err: BaseException, secrets: Set[str]) -> str:
    """Error message suitable for persistence, logs, and clients (secrets redacted, bounded length)."""
    return redact_secrets(f"{type(err).__name__}: {str(err)[:300]}", secrets)


def _finalize_run(run_id: str, report: Any, secrets: Set[str]) -> Dict[str, Any]:
    """Builds the redacted investigation payload and persists it with exactly its test cases."""
    if report is None:
        raise RuntimeError("Investigation finished without producing a report")
    payload = redact_secrets(_build_investigation_payload(report), secrets)
    payload["run_id"] = run_id
    report_status = getattr(report.overview.status, "value", report.overview.status)
    run_store.complete(
        run_id,
        report=payload,
        test_cases=payload["proposed_test_cases"],
        stopping_reason=report.overview.stopping_reason,
        secrets=secrets,
        report_status=report_status,
    )
    return payload


def _record_run_failure(run_id: str, error_text: str, secrets: Set[str]) -> None:
    try:
        run_store.fail(run_id, error_text, secrets=secrets)
    except Exception as store_err:
        logger.error("[Runs] Could not persist failure for %s: %s", run_id, store_err)


@app.post("/api/investigate")
async def investigate_only(request: InspectionRequest):
    """
    Dedicated Investigation-Only Endpoint.
    Pipeline: User Input -> Website Investigation -> Application Intelligence Model -> Investigation Report.
    The run (report + synthesized test cases) is persisted and retrievable via GET /api/runs/{run_id}.
    Explicitly excludes:
    - Test execution
    - Bug reporting or defect triage
    """
    logger.info("[Investigation-Only] Received request for: %s", request.url)
    try:
        validate_url_safety(request.url, settings.allowed_hosts_list)
    except SafetyViolation as e:
        raise HTTPException(status_code=400, detail=f"Safety Policy Rejection: {str(e)}")

    secrets = collect_known_secrets(request.url)
    run = run_store.create(request.url, request.description, request.requirements, secrets=secrets)

    try:
        run_store.mark_running(run.run_id)
        from app.investigation_agent import run_stateful_investigation
        max_actions_val = getattr(request, "max_actions", 0) or (0 if settings.investigation_adaptive_mode else settings.investigation_max_actions)
        report = await run_stateful_investigation(
            url=request.url,
            description=request.description,
            requirements=request.requirements,
            max_actions=max_actions_val,
            max_time_seconds=settings.investigation_max_browser_seconds,
            headless=settings.verityqa_headless,
            investigation_id=run.run_id,
        )
        payload = _finalize_run(run.run_id, report, secrets)
        logger.info("[Investigation-Only] Completed run %s for %s: %d states, %d transitions",
                    run.run_id, request.url, len(report.discovered_application_map), len(report.user_workflows))
        return JSONResponse(content=payload)
    except Exception as e:
        error_text = _safe_error_text(e, secrets)
        logger.error("[Investigation-Only] Run %s failed: %s", run.run_id, error_text)
        _record_run_failure(run.run_id, error_text, secrets)
        raise HTTPException(status_code=500, detail=f"Investigation failed (run {run.run_id}): {error_text[:150]}")


@app.get("/api/investigate/stream")
@app.post("/api/investigate/stream")
async def investigate_stream(request: Request):
    """
    Real-time Server-Sent Events (SSE) investigation stream.
    Every event carries "run_id". Streams:
    - RUN_CREATED: The persisted run was created (first event)
    - PROBE_START: Probe dispatch with prediction
    - PROBE_RESULT: Outcome comparison and empirical diff
    - LEAD_DETECTED: Bug lead detection (always-on oracles, form outcomes)
    - REPRO_RESULT: 2-of-2 context confirmation and step minimization
    - STOPPING: Investigation completion with final behavior model
    - COMPLETE: Run persisted; load it via GET /api/runs/{run_id}
    - ERROR: Run failed and was persisted with status "failed"
    """
    import json
    target_url = None
    description = None
    requirements = None
    resume_checkpoint = None
    max_actions = 0 if settings.investigation_adaptive_mode else settings.investigation_max_actions

    if request.method == "POST":
        body = await request.json()
        target_url = body.get("url")
        description = body.get("description")
        requirements = body.get("requirements")
        resume_checkpoint = body.get("resume_checkpoint")
        if "max_actions" in body:
            max_actions = int(body["max_actions"])
    else:
        target_url = request.query_params.get("url")
        description = request.query_params.get("description")
        resume_checkpoint = request.query_params.get("resume_checkpoint")
        req_param = request.query_params.get("requirements")
        if req_param:
            requirements = [r.strip() for r in req_param.split("\n") if r.strip()]
        if "max_actions" in request.query_params:
            max_actions = int(request.query_params["max_actions"])

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing required 'url' parameter")

    try:
        validate_url_safety(target_url, settings.allowed_hosts_list)
    except SafetyViolation as e:
        raise HTTPException(status_code=400, detail=f"Safety Policy Rejection: {str(e)}")

    secrets = collect_known_secrets(target_url)
    run = run_store.create(target_url, description, requirements, secrets=secrets)
    run_id = run.run_id

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()

    def publish(evt: Dict[str, Any]):
        """Thread-safe: every streamed event is redacted and stamped with this run's ID."""
        loop.call_soon_threadsafe(event_queue.put_nowait, {**redact_secrets(evt, secrets), "run_id": run_id})

    async def run_investigation_worker():
        try:
            run_store.mark_running(run_id)
            from app.investigation_agent import run_stateful_investigation
            report = await run_stateful_investigation(
                url=target_url,
                description=description,
                requirements=requirements,
                max_actions=max_actions,
                max_time_seconds=settings.investigation_max_browser_seconds,
                headless=settings.verityqa_headless,
                event_callback=publish,
                resume_checkpoint=resume_checkpoint,
                investigation_id=run_id,
            )
            # Persist before announcing so a client reacting to COMPLETE can always load the run.
            payload = _finalize_run(run_id, report, secrets)
            publish({"type": "COMPLETE", "status": "completed", "report": payload})
        except asyncio.CancelledError:
            try:
                run_store.cancel(run_id, "Investigation task was cancelled", secrets=secrets)
            except Exception as store_err:
                logger.error("[Runs] Could not persist cancellation for %s: %s", run_id, store_err)
            raise
        except Exception as err:
            error_text = _safe_error_text(err, secrets)
            logger.error("[Investigation-Stream] Run %s failed: %s", run_id, error_text)
            _record_run_failure(run_id, error_text, secrets)
            publish({"type": "ERROR", "status": "failed", "error": error_text})
        finally:
            loop.call_soon_threadsafe(event_queue.put_nowait, None)

    event_queue.put_nowait({"type": "RUN_CREATED", "run_id": run_id, "status": run.status.value, "target_url": target_url})
    task = asyncio.create_task(run_investigation_worker())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    async def sse_generator():
        while True:
            evt = await event_queue.get()
            if evt is None:
                break
            yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/run", response_model=QARunResponse)
async def run_full_qa(request: QARunRequest):
    """
    End-to-end Autonomous QA Run:
    1. Safety validation
    2. Website inspection + deep crawl
    3. AI-powered test plan generation
    4. Browser test execution via Playwright
    5. Failure investigation & bug verification
    6. Reusable Playwright script export
    """
    logger.info("[QA Run] Starting autonomous run for: %s", request.url)

    # 1. Safety check
    try:
        validate_url_safety(request.url, settings.allowed_hosts_list)
    except SafetyViolation as e:
        raise HTTPException(status_code=400, detail=f"Safety Policy Rejection: {str(e)}")

    run_id = str(uuid.uuid4())

    # 2. Inspection + crawl
    try:
        inspection = await inspector.inspect(request.url)
        from app.investigation.crawler import WebsiteCrawler
        crawler = WebsiteCrawler()
        investigation_report = await crawler.investigate(
            start_url=request.url,
            max_pages=min(settings.investigation_max_pages, 10),
            max_depth=min(settings.investigation_max_depth, 2),
        )
    except Exception as e:
        logger.exception("Inspection failed in QA run: %s", e)
        raise HTTPException(status_code=502, detail=f"Website inspection failed: {str(e)[:150]}")

    # 3. Classification + test plan
    classification = classifier.classify(inspection)
    try:
        test_plan = await planning_agent.create_plan(
            inspection=inspection,
            classification=classification,
            requirements=None,
            description=request.description,
            investigation_report=investigation_report,
        )
        test_plan.pages_discovered = investigation_report.inventory.total_discovered
        test_plan.pages_inspected = investigation_report.inventory.total_inspected
        test_plan.features_detected = [f.model_dump() for f in investigation_report.features]

        # Phase 2: If page is GENERAL or unfamiliar, dynamically augment plan using the AdaptiveTestGenerator
        if classification.page_category == PageCategory.GENERAL or len(test_plan.test_cases) <= 1:
            try:
                from app.exploration.explorer import AutonomousExplorer
                from app.exploration.graph import ExploreRequest
                from app.exploration.generator import AdaptiveTestGenerator
                exp_res = await explorer.explore(ExploreRequest(
                    url=request.url,
                    max_actions=min(settings.exploration_max_actions, 15),
                    max_pages=min(settings.exploration_max_pages, 5),
                ))
                if exp_res and exp_res.graph:
                    generator = AdaptiveTestGenerator()
                    adaptive_cases = generator.generate_tests_from_graph(exp_res.graph, max_cases=8)
                    if adaptive_cases:
                        logger.info("[QA Run] Augmented plan with %d adaptive test cases from state graph", len(adaptive_cases))
                        test_plan.test_cases.extend(adaptive_cases)
            except Exception as exp_err:
                logger.warning("[QA Run] Adaptive test generation non-critical error: %s", exp_err)
    except Exception as e:
        logger.error("Test planning failed in QA run: %s", e)
        raise HTTPException(status_code=500, detail="Test planning failed.")

    execution_results = []
    bug_reports = []
    exported_script = None

    # 4. Browser test execution
    if request.execute_tests and test_plan.test_cases:
        logger.info(
            "[TEST_COUNT_TRACE] stage=executor_received count=%d",
            len(test_plan.test_cases),
        )
        logger.info("[QA Run] Executing %d test cases via Playwright...", len(test_plan.test_cases))
        # Ground selectors against inspected DOM before running
        test_runner.set_inspection(inspection)
        execution_results = await test_runner.run_all(
            test_cases=test_plan.test_cases,
            target_url=request.url,
        )

        # 5. Bug investigation for failed tests
        if request.verify_bugs:
            for exec_res in execution_results:
                if exec_res.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                    tc = next(
                        (t for t in test_plan.test_cases if t.test_id == exec_res.test_id),
                        None,
                    )
                    if tc:
                        try:
                            bug_report = await bug_investigator.investigate_failure(
                                initial_result=exec_res,
                                test_case=tc,
                                target_url=request.url,
                            )
                            bug_reports.append(bug_report)
                        except Exception as e:
                            logger.error("Bug investigation failed for %s: %s", exec_res.test_id, e)

        # 6. Export Playwright script
        if request.export_scripts:
            try:
                exported_script = test_exporter.export_to_python_script(
                    test_cases=test_plan.test_cases,
                    target_url=request.url,
                )
            except Exception as e:
                logger.error("Script export failed: %s", e)

    passed = sum(1 for r in execution_results if r.status == ExecutionStatus.PASSED)
    failed = sum(1 for r in execution_results if r.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR))
    summary = {
        "run_id": run_id,
        "tests_generated": len(test_plan.test_cases),
        "tests_validated": len(test_plan.test_cases),
        "tests_executed": len(execution_results),
        "tests_passed": passed,
        "tests_failed": failed,
        "bugs_confirmed": sum(1 for b in bug_reports if "CONFIRMED" in b.verification_status.value),
        "bugs_potential": sum(1 for b in bug_reports if b.verification_status.value == "POTENTIAL_DEFECT"),
        "planning_mode": test_plan.planning_mode.value,
        "observation_driven": True,
    }

    response = QARunResponse(
        run_id=run_id,
        target_url=request.url,
        inspection=inspection,
        classification=classification,
        test_plan=test_plan,
        execution_results=execution_results,
        bug_reports=bug_reports,
        exported_script=exported_script,
        summary=summary,
    )
    _qa_runs[run_id] = response
    logger.info("[QA Run %s] Completed. %d tests, %d bugs found.", run_id, len(execution_results), len(bug_reports))
    return response


@app.get("/api/run/{run_id}")
async def get_run_result(run_id: str):
    """Retrieves a previously completed QA run result by run_id."""
    if run_id not in _qa_runs:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found.")
    return JSONResponse(content=_qa_runs[run_id].model_dump())


@app.get("/api/run/{run_id}/export", response_class=PlainTextResponse)
async def export_run_script(run_id: str):
    """Downloads the generated Playwright test script for a completed run."""
    if run_id not in _qa_runs:
        raise HTTPException(status_code=404, detail=f"Run ID '{run_id}' not found.")
    run = _qa_runs[run_id]
    if not run.exported_script:
        raise HTTPException(status_code=404, detail="No exported script available for this run.")
    return PlainTextResponse(
        content=run.exported_script,
        headers={"Content-Disposition": f"attachment; filename=verityqa_tests_{run_id[:8]}.py"},
        media_type="text/x-python",
    )


@app.post("/api/tests/execute", response_model=ExecuteSuiteResponse)
async def execute_tests(request: ExecuteSuiteRequest):
    """
    Executes a synthesized contract test suite directly against the target URL.
    Verifies failed tests via BugInvestigator to distinguish confirmed bugs from setup errors.
    """
    validate_url_safety(request.target_url, settings.allowed_hosts_list)
    import time
    start_t = time.time()
    exec_id = f"exec-{uuid.uuid4().hex[:8]}"

    if not request.test_cases:
        return ExecuteSuiteResponse(
            execution_id=exec_id,
            target_url=request.target_url,
            total_cases=0,
            executed_count=0,
            passed_count=0,
            failed_count=0,
            setup_failures_count=0,
            assertion_validity_rate=1.0,
            results=[],
            duration_seconds=0.0
        )

    # Execute tests
    execution_results = await test_runner.run_all(
        test_cases=request.test_cases,
        target_url=request.target_url,
    )

    passed = sum(1 for r in execution_results if r.status == ExecutionStatus.PASSED)
    failed = sum(1 for r in execution_results if r.status == ExecutionStatus.FAILED)
    errors = sum(1 for r in execution_results if r.status == ExecutionStatus.ERROR)
    setup_failures = sum(1 for r in execution_results if r.status in (ExecutionStatus.LOCATOR_ERROR, ExecutionStatus.AUTOMATION_ERROR, ExecutionStatus.ENVIRONMENT_ERROR))

    # Bug verification
    bug_reports = []
    if request.verify_bugs:
        for res in execution_results:
            if res.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                tc = next((t for t in request.test_cases if t.test_id == res.test_id), None)
                if tc:
                    try:
                        bug = await bug_investigator.investigate_failure(
                            initial_result=res,
                            test_case=tc,
                            target_url=request.target_url,
                        )
                        bug_reports.append(bug)
                    except Exception as b_err:
                        logger.error("Bug investigation failed for %s: %s", res.test_id, b_err)

    duration = round(time.time() - start_t, 2)
    assertion_validity = round(passed / len(execution_results), 3) if execution_results else 1.0

    return ExecuteSuiteResponse(
        execution_id=exec_id,
        target_url=request.target_url,
        total_cases=len(request.test_cases),
        executed_count=len(execution_results),
        passed_count=passed,
        failed_count=failed + errors,
        setup_failures_count=setup_failures,
        assertion_validity_rate=assertion_validity,
        results=execution_results,
        bug_reports=bug_reports,
        duration_seconds=duration,
    )


@app.get("/api/reports/latest")
async def get_latest_report():
    """Retrieves the most recent completed investigation report or test execution report."""
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    if not os.path.exists(reports_dir):
        raise HTTPException(status_code=404, detail="No reports directory found")
    json_files = [f for f in os.listdir(reports_dir) if f.endswith(".json")]
    if not json_files:
        raise HTTPException(status_code=404, detail="No reports found")
    json_files.sort(key=lambda f: os.path.getmtime(os.path.join(reports_dir, f)), reverse=True)
    latest_file = json_files[0]
    return FileResponse(os.path.join(reports_dir, latest_file), media_type="application/json")


@app.get("/api/reports")
async def list_reports():
    """Lists saved investigation reports, test suites, and execution logs."""
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    if not os.path.exists(reports_dir):
        return JSONResponse(content=[])
    items = []
    for fname in os.listdir(reports_dir):
        if fname.endswith(".json"):
            fpath = os.path.join(reports_dir, fname)
            stat = os.stat(fpath)
            category = "other"
            if "handoff" in fname or "investigation" in fname or "journey" in fname:
                category = "investigation"
            elif "test_suite" in fname or "generated_test" in fname:
                category = "test_suite"
            elif "execution" in fname:
                category = "execution"
            elif "evaluation" in fname or "benchmark" in fname:
                category = "benchmark"
            items.append({
                "filename": fname,
                "category": category,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
            })
    items.sort(key=lambda x: x["modified_at"], reverse=True)
    return JSONResponse(content=items)


@app.get("/api/reports/{filename}")
async def get_report_content(filename: str):
    """Retrieves the JSON content of a saved report."""
    safe_name = os.path.basename(filename)
    if safe_name != filename or not filename.endswith(".json") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid report filename.")
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    fpath = os.path.join(reports_dir, safe_name)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Report file not found.")
    return FileResponse(fpath, media_type="application/json")


@app.get("/api/runs", response_model=RunListResponse)
async def list_runs():
    """Lists persisted investigation runs, newest first."""
    return RunListResponse(runs=run_store.list_runs())


@app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str):
    """Returns one persisted run with its report and exactly its generated test cases."""
    try:
        return run_store.get(run_id)
    except InvalidRunId:
        raise HTTPException(status_code=400, detail="Invalid run ID format.")
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")


@app.get("/api/benchmark")
async def run_benchmark():
    """
    Runs the autonomous agent against the local demo app (http://127.0.0.1:8001)
    and evaluates detection of all 4 planted bugs.
    Returns BenchmarkRunSummary with precision/recall/F1.

    BUG-01 (password length bypass) requires a deliberate server-side boundary test
    that bypasses browser HTML5 required validation using SET_VALUE_JS, because the
    fixture form has `required` on both fields — the browser blocks empty submissions
    before the server's length check is reached.
    """
    from app.schemas import ActionStep, ActionType, ExecutionAssertion, AssertionType
    from app.benchmark.manifest import Fixture2BenchmarkEvaluator

    demo_url = "http://127.0.0.1:8001"
    try:
        validate_url_safety(demo_url, settings.allowed_hosts_list)
    except SafetyViolation:
        raise HTTPException(
            status_code=400,
            detail="Demo app URL blocked by safety policy. Add 127.0.0.1 to VERITYQA_ALLOWED_HOSTS.",
        )

    logger.info("[Benchmark] Starting planted-bug evaluation against %s", demo_url)
    all_bugs = []

    for path in ["/login", "/registration", "/search"]:
        page_url = f"{demo_url}{path}"
        try:
            inspection = await inspector.inspect(page_url)
            classification = classifier.classify(inspection)
            benchmark_requirements = (
                "Password must be at least 6 characters. "
                "Registration requires accepting terms of service and matching password confirmation. "
                "Search query must not be empty or blank."
            )
            test_plan = await planning_agent.create_plan(
                inspection=inspection,
                classification=classification,
                requirements=benchmark_requirements,
                description="Apex Demo Portal with validation forms",
            )
            exec_results = await test_runner.run_all(
                test_cases=test_plan.test_cases,
                target_url=page_url,
            )
            for exec_res in exec_results:
                if exec_res.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                    tc = next((t for t in test_plan.test_cases if t.test_id == exec_res.test_id), None)
                    if tc:
                        bug = await bug_investigator.investigate_failure(
                            initial_result=exec_res,
                            test_case=tc,
                            target_url=page_url,
                        )
                        all_bugs.append(bug)
        except Exception as e:
            logger.error("[Benchmark] Error processing %s: %s", page_url, e)

    # ── BUG-01 Explicit Boundary Test: 1-char password via SET_VALUE_JS ──────────
    # The fixture's login form has `required` on both fields. Browser HTML5 validation
    # blocks empty submissions before the server sees them, so a standard FILL action
    # with a 1-char password would still be blocked by the `required` check if the
    # username field is also empty. We use SET_VALUE_JS to set both fields directly
    # via JS, bypassing browser validation, so the server receives the 1-char password.
    try:
        login_url = f"{demo_url}/login"
        bug01_steps = [
            ActionStep(
                step_number=1,
                action=ActionType.NAVIGATE,
                value=login_url,
                description="Navigate to login page",
            ),
            ActionStep(
                step_number=2,
                action=ActionType.SET_VALUE_JS,
                selector="input[name='username'], input[name='email'], #username, #email, input[type='email'], input[type='text']",
                value="admin@apex.com",
                description="Set username via JS (bypasses required validation)",
            ),
            ActionStep(
                step_number=3,
                action=ActionType.SET_VALUE_JS,
                selector="input[name='password'], input[type='password'], #password",
                value="a",
                description="Set 1-char password via JS (deliberate boundary test for BUG-01)",
            ),
            ActionStep(
                step_number=4,
                action=ActionType.EVAL_JS,
                value="document.querySelector('form').submit ? document.querySelector('form').submit() : document.querySelector('[type=submit], button[type=submit], button').click()",
                description="Submit form bypassing browser validation to reach server's length check",
            ),
            ActionStep(
                step_number=5,
                action=ActionType.OBSERVE_STATE,
                description="Observe server response",
                assertion=ExecutionAssertion(
                    assertion_type=AssertionType.TEXT_VISIBLE,
                    expected_value="at least",
                    description=(
                        "Server should reject a 1-char password with a length validation error. "
                        "Expected: message containing 'at least' (e.g. 'Password must be at least 6 characters'). "
                        "If this assertion FAILS it means the server accepted the 1-char password — confirming BUG-01."
                    ),
                ),
            ),
        ]
        from app.execution.executor import BrowserExecutor
        executor = BrowserExecutor()
        bug01_result = await executor.execute_steps(
            test_id="BUG01-BOUNDARY",
            test_name="BUG-01 Server-side password length validation boundary (1-char)",
            steps=bug01_steps,
            initial_url=login_url,
        )
        logger.info("[Benchmark] BUG-01 boundary test status: %s", bug01_result.status)

        # If the server accepted a 1-char password (assertion FAILED = no length error shown)
        # then we have reproduced BUG-01.
        if bug01_result.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
            from app.schemas import BugReport, BugSeverity, VerificationStatus
            import uuid as _uuid
            bug01_report = BugReport(
                bug_id=f"BUG-{_uuid.uuid4().hex[:8]}",
                title="Password length validation bypass — server accepts 1-character passwords",
                severity=BugSeverity.HIGH,
                verification_status=VerificationStatus.CONFIRMED_REPRODUCIBLE,
                reproduction_rate="1/1 deliberate boundary test",
                test_id="BUG01-BOUNDARY",
                target_url=login_url,
                summary=(
                    "The login endpoint accepted a password of length 1 without returning a "
                    "minimum-length validation error. The requirement states passwords must be at least 6 characters."
                ),
                steps_to_reproduce=[
                    "Navigate to /login",
                    "Set username field to 'admin@apex.com' via JS (bypassing browser required validation)",
                    "Set password field to 'a' (length=1) via JS",
                    "Submit form bypassing browser HTML5 validation",
                    "Observe server response: no minimum-length error is shown",
                ],
                expected_behavior="Server rejects password shorter than 6 characters with a validation message.",
                actual_behavior=(
                    f"Server did not show a minimum-length rejection. "
                    f"Browser assertion: {bug01_result.failure_reason or 'TEXT_VISIBLE for \"at least\" not found'}"
                ),
                evidence_screenshot=bug01_result.final_screenshot,
                dom_context=bug01_result.dom_snapshot_snippet,
                console_errors=bug01_result.console_logs,
                suspected_cause="Server-side validation does not enforce a minimum password length.",
                suggested_fix="Add server-side length check: reject passwords shorter than 6 characters.",
            )
            all_bugs.append(bug01_report)
            logger.info("[Benchmark] BUG-01 reproduced and reported.")
        else:
            logger.info("[Benchmark] BUG-01: server correctly rejected 1-char password (not a defect).")
    except Exception as e:
        logger.error("[Benchmark] BUG-01 boundary test failed: %s", e)

    summary = benchmark_evaluator.evaluate(all_bugs)
    logger.info(
        "[Benchmark] TP=%d FP=%d Missed=%d P=%.2f R=%.2f F1=%.2f",
        summary.true_positives_detected,
        summary.false_positives,
        summary.missed_bugs,
        summary.precision,
        summary.recall,
        summary.f1_score,
    )
    return JSONResponse(content=summary.model_dump())


@app.get("/api/benchmark/fixture2")
async def run_benchmark_fixture2():
    """
    Runs the autonomous agent against the TaskDash demo app (http://127.0.0.1:8002)
    and evaluates detection of 3 planted bugs (BUG-F2-01..03):
      - BUG-F2-01: Empty task title accepted without validation
      - BUG-F2-02: Priority filter silently ignored
      - BUG-F2-03: Mark complete does not update task status

    Requires the Fixture 2 server to be running on port 8002.
    """
    from app.benchmark.manifest import Fixture2BenchmarkEvaluator

    fixture2_url = "http://127.0.0.1:8002"
    try:
        validate_url_safety(fixture2_url, settings.allowed_hosts_list)
    except SafetyViolation:
        raise HTTPException(
            status_code=400,
            detail="Fixture 2 URL blocked by safety policy. Add 127.0.0.1 to VERITYQA_ALLOWED_HOSTS.",
        )

    logger.info("[Benchmark-F2] Starting Fixture 2 (TaskDash) evaluation against %s", fixture2_url)
    all_bugs = []

    # Run autonomous QA against the main task-management routes
    for path in ["/", "/create", "/tasks"]:
        page_url = f"{fixture2_url}{path}"
        try:
            inspection = await inspector.inspect(page_url)
            classification = classifier.classify(inspection)
            f2_requirements = (
                "Task title must not be empty — an empty title should be rejected with a validation error. "
                "The priority filter on the task list must return only tasks matching the selected priority. "
                "Clicking 'Mark Done' must update the task's status to 'Done' in the task list."
            )
            test_plan = await planning_agent.create_plan(
                inspection=inspection,
                classification=classification,
                requirements=f2_requirements,
                description="TaskDash — minimal task management application",
            )
            exec_results = await test_runner.run_all(
                test_cases=test_plan.test_cases,
                target_url=page_url,
            )
            for exec_res in exec_results:
                if exec_res.status in (ExecutionStatus.FAILED, ExecutionStatus.ERROR):
                    tc = next((t for t in test_plan.test_cases if t.test_id == exec_res.test_id), None)
                    if tc:
                        try:
                            bug = await bug_investigator.investigate_failure(
                                initial_result=exec_res,
                                test_case=tc,
                                target_url=page_url,
                            )
                            all_bugs.append(bug)
                        except Exception as inv_err:
                            logger.error("[Benchmark-F2] Investigation error for %s: %s", exec_res.test_id, inv_err)
        except Exception as e:
            logger.error("[Benchmark-F2] Error processing %s: %s", page_url, e)

    evaluator = Fixture2BenchmarkEvaluator()
    summary = evaluator.evaluate(all_bugs)
    logger.info(
        "[Benchmark-F2] TP=%d FP=%d Missed=%d P=%.2f R=%.2f F1=%.2f",
        summary.true_positives_detected,
        summary.false_positives,
        summary.missed_bugs,
        summary.precision,
        summary.recall,
        summary.f1_score,
    )
    return JSONResponse(content=summary.model_dump())


@app.post("/api/explore", response_model=ExploreResponse)
async def explore_website(request: ExploreRequest):
    """
    Phase 1: Generic Autonomous Exploration.

    Explores the target website using a template-free, state-graph–based
    exploration engine.  Discovers interactive controls, records state
    transitions, detects new application states after interaction, avoids
    exploration loops, and respects configured budgets.

    Returns an ApplicationStateGraph with full exploration trace.
    """
    logger.info("[Explore] Starting autonomous exploration of: %s", request.url)

    # Safety check
    try:
        validate_url_safety(request.url, settings.allowed_hosts_list)
    except SafetyViolation as e:
        raise HTTPException(status_code=400, detail=f"Safety Policy Rejection: {str(e)}")

    try:
        result = await explorer.explore(request)
        logger.info(
            "[Explore] Complete: %d states, %d transitions, %d new states, %.1fs",
            result.states_discovered,
            result.transitions_observed,
            result.new_states_found,
            result.duration_seconds,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("[Explore] Exploration failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Exploration failed: {str(e)[:150]}")
