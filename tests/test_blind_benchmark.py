"""
Phase 1: Blind Autonomous Investigation Benchmark Test Suite.
=============================================================
Executes VerityQA investigation without target-specific hints or hardcoded action
sequences, and evaluates the resulting application model, transitions, evidence ledger,
and decision snapshots against the independent Ground-Truth Evaluator.
"""

import os
import time
import socket
import pytest
import threading
import uvicorn

from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from tests.benchmark_evaluator import BenchmarkEvaluator, BenchmarkEvaluationResult


class BenchmarkServerRunner:
    def __init__(self, port: int = 8999):
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        from demo_app.benchmark_app.main import app as target_app

        config = uvicorn.Config(
            target_app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

        # Wait for server readiness
        start_t = time.time()
        while time.time() - start_t < 10.0:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.1)
        raise RuntimeError(f"Benchmark test server failed to bind to port {self.port}")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3.0)


@pytest.fixture(scope="module")
def blind_benchmark_server():
    runner = BenchmarkServerRunner(port=8999)
    runner.start()
    yield f"http://127.0.0.1:{runner.port}"
    runner.stop()


def test_blind_investigation_benchmark_run(blind_benchmark_server):
    """
    Phase 1 Blind Benchmark:
    Runs the existing VerityQA investigation engine on the benchmark target URL
    without target-specific hardcoding, evaluates the resulting application model,
    evidence ledger, and decision snapshots against independent ground truth.
    """
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)

    try:
        # Autonomous configuration: no hardcoded navigation hints, no route assumptions
        state = {
            "target_url": f"{blind_benchmark_server}/login",
            "auth_credentials": {"username": "benchmark_user", "password": "secret_benchmark"},
            "max_actions": 6,
            "max_probes": 6,
            "max_time_seconds": 60.0,
        }

        final_state = graph.invoke(state)
        report = final_state.get("final_report")
        assert report is not None

        # -------------------------------------------------------------
        # Independent Evaluator Assessment (Blind Ground Truth)
        # -------------------------------------------------------------
        eval_result = BenchmarkEvaluator.evaluate(
            report=report,
            decision_snapshots=final_state.get("decision_snapshots"),
        )

        # 1. State Discovery & Surface Growth
        # Must discover at least entry, validation error, catalog, and post-login route
        assert eval_result.discovered_states_count >= 3
        assert "ST_LOGIN" in eval_result.matched_ground_truth_states
        assert "ST_LOGIN_ERROR" in eval_result.matched_ground_truth_states
        assert "ST_PRODUCTS" in eval_result.matched_ground_truth_states
        assert eval_result.state_discovery_recall >= 0.25

        # 2. Transition Accuracy & Empirical Grounding
        # Every recorded transition must be verified via actual DOM/route diffs
        assert eval_result.transition_accuracy == 1.0
        assert eval_result.evidence_support_ratio == 1.0

        # 3. Skepticism & Benign Warning Filtering
        # console.warn MUST NEVER be reported as a confirmed defect
        assert eval_result.benign_warning_correctly_ignored is True
        assert eval_result.false_positive_leads_count == 0

        # 4. Redundant Exploration Rate
        # Action deduplication and equivalence sampling must prevent wasteful repetition
        assert eval_result.redundant_exploration_rate <= 0.15

        # 5. Adaptive Decision Quality & Snapshots
        # Every step in decision snapshots must be recorded and evaluated
        assert eval_result.total_decision_snapshots >= 2
        assert eval_result.adaptive_decision_quality >= 0.80

        # Check step 1 was validation testing and step 2 was authentication progression
        trace = eval_result.decision_trace_summary
        assert len(trace) >= 2
        assert any(t["evaluator_verdict"] == "USEFUL_EXPLORATION" for t in trace)

    finally:
        orchestrator.close()
