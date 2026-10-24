"""
Runner script to execute the 4 Phase 2 audit experiments and export the exact metrics.
"""

import os
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import json
import socket
import threading
import time
import uvicorn

from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow
from tests.benchmark_evaluator import BenchmarkEvaluator


class AuditServerRunner:
    def __init__(self, port: int = 8993):
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


def run_experiment(name: str, target_url: str, max_probes: int, max_time: float, policy: str):
    print(f"\n--- Running Experiment {name} (policy={policy}, probes={max_probes}, max_time={max_time}s) ---")
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)
    t0 = time.time()
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
        elapsed = round(time.time() - t0, 2)
        res_dict = result.model_dump()
        res_dict["experiment_name"] = name
        res_dict["policy"] = policy
        res_dict["max_probes"] = max_probes
        res_dict["elapsed_seconds"] = elapsed
        print(f"Done {name}: Discovered={result.discovered_states_count}/{result.total_ground_truth_states} ({result.state_discovery_recall*100}%), Recall={result.state_discovery_recall}, Workflow={result.workflow_completion_rate}, Accuracy={result.transition_accuracy}")
        print(f"Matched States: {result.matched_ground_truth_states}")
        print(f"Missing States: {result.missing_ground_truth_states}")
        print(f"Stopping Reason: {result.stopping_reason}")
        return res_dict
    finally:
        orchestrator.close()


def main():
    server = AuditServerRunner(port=8993)
    server.start()
    results = {}
    try:
        url = f"http://127.0.0.1:{server.port}"
        
        # Exp A: Baseline
        results["Experiment A (Baseline)"] = run_experiment("A", url, max_probes=6, max_time=60.0, policy="balanced")
        
        # Exp B: Budget Expansion
        results["Experiment B (Budget Expansion)"] = run_experiment("B", url, max_probes=10, max_time=120.0, policy="balanced")
        
        # Exp C: Workflow Depth
        results["Experiment C (Workflow Depth)"] = run_experiment("C", url, max_probes=10, max_time=120.0, policy="workflow_depth")
        
        # Exp D: Breadth Policy
        results["Experiment D (Breadth Oriented)"] = run_experiment("D", url, max_probes=10, max_time=120.0, policy="breadth")
        
        with open("phase2_metrics.json", "w") as f:
            json.dump(results, f, indent=2)
        print("\nAll experiments completed successfully. Saved to phase2_metrics.json.")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
