"""
Phase 2: Adaptive Exploration Quality Audit Test Suite.
======================================================
Runs controlled, comparable experiments across four policies:
  A. Baseline Settings (6 probes, 60s, 'balanced' policy)
  B. Larger Operational Budget (12 probes, 150s, 'balanced' policy)
  C. Workflow-Depth Prioritized Policy (12 probes, 150s, 'workflow_depth' policy)
  D. Breadth-Oriented Policy (12 probes, 150s, 'breadth' policy)

Evaluates state discovery recall, transition accuracy, workflow depth,
redundancy rate, decision quality, and stopping reasons using the independent evaluator.
"""

import time
import socket
import pytest
import threading
import uvicorn

from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from tests.benchmark_evaluator import BenchmarkEvaluator, BenchmarkEvaluationResult


class AuditServerRunner:
    def __init__(self, port: int = 8992):
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

        start_t = time.time()
        while time.time() - start_t < 10.0:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.1)
        raise RuntimeError(f"Audit server failed to bind to port {self.port}")

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3.0)


@pytest.fixture(scope="module")
def audit_server():
    runner = AuditServerRunner(port=8992)
    runner.start()
    yield f"http://127.0.0.1:{runner.port}"
    runner.stop()


def _run_investigation_experiment(target_url: str, max_probes: int, max_time: float, policy: str):
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)
    try:
        state = {
            "target_url": f"{target_url}/login",
            "auth_credentials": {"username": "benchmark_user", "password": "secret_benchmark"},
            "max_actions": max_probes,
            "max_probes": max_probes,
            "max_time_seconds": max_time,
            "exploration_policy": policy,
        }
        final_state = graph.invoke(state)
        report = final_state.get("final_report")
        result = BenchmarkEvaluator.evaluate(
            report=report,
            decision_snapshots=final_state.get("decision_snapshots"),
            final_state=final_state,
        )
        return final_state, report, result
    finally:
        orchestrator.close()


def test_experiment_a_baseline(audit_server):
    """Experiment A: Baseline settings (6 probes, 60s, balanced policy)."""
    state, report, result = _run_investigation_experiment(audit_server, max_probes=6, max_time=60.0, policy="balanced")
    assert result.state_discovery_recall >= 0.30
    assert result.transition_accuracy == 1.0
    assert result.evidence_support_ratio == 1.0
    assert result.benign_warning_correctly_ignored is True
    assert result.redundant_exploration_rate <= 0.15
    assert result.adaptive_decision_quality >= 0.85
    assert "Configured probe ceiling reached" in result.stopping_reason or "time" in result.stopping_reason.lower()


def test_experiment_b_larger_budget(audit_server):
    """Experiment B: Larger budget with the same balanced decision policy (10 probes, 120s)."""
    state, report, result = _run_investigation_experiment(audit_server, max_probes=10, max_time=120.0, policy="balanced")
    # Expanding budget allows discovering more ground-truth states
    assert result.discovered_states_count >= 5
    assert result.state_discovery_recall >= 0.40
    assert result.transition_accuracy == 1.0
    assert result.redundant_exploration_rate <= 0.15


def test_experiment_c_workflow_depth(audit_server):
    """Experiment C: Workflow-depth prioritized policy (10 probes, 120s)."""
    state, report, result = _run_investigation_experiment(audit_server, max_probes=10, max_time=120.0, policy="workflow_depth")
    assert result.workflow_completion_rate >= 0.15  # Reaches cart or deeper
    assert result.transition_accuracy == 1.0
    assert result.adaptive_decision_quality >= 0.85


def test_experiment_d_breadth_policy(audit_server):
    """Experiment D: Breadth-oriented policy (10 probes, 120s)."""
    state, report, result = _run_investigation_experiment(audit_server, max_probes=10, max_time=120.0, policy="breadth")
    assert result.state_discovery_recall >= 0.35
    assert result.transition_accuracy == 1.0
    assert result.adaptive_decision_quality >= 0.85
