"""
Run and record Journey-Aware Autonomous Investigation on ShopSphere.
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# Add root directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.investigation_agent.workflow import InvestigationOrchestrator, build_investigation_workflow


def run_journey_investigation():
    target_url = "http://127.0.0.1:5173/"
    print(f"\n[VerityQA Phase 1] Launching Journey-Aware Autonomous Investigation on: {target_url}")
    
    orchestrator = InvestigationOrchestrator(headless=True)
    graph = build_investigation_workflow(orchestrator)

    state = {
        "target_url": target_url,
        "investigation_id": "inv_shopsphere_phase1_journey",
        "max_actions": 12,
        "max_probes": 12,
        "max_time_seconds": 180.0,
        "max_mutations": 25,
        "sandbox_mode": True,
        "sandbox_origins": ["http://127.0.0.1:5173"],
        "exploration_policy": "balanced",
    }

    try:
        final_state = graph.invoke(state)
        report = final_state.get("final_report")
        
        print("\n" + "="*70)
        print("INVESTIGATION COMPLETE — HONEST RESULTS REPORT")
        print("="*70)
        
        # 1. State and Route Discovery
        states = final_state.get("states", {})
        routes = {s.url for s in states.values()}
        print(f"Discovered States Count: {len(states)}")
        print(f"Discovered Routes ({len(routes)}):")
        for r in sorted(routes):
            print(f"  • {r}")
            
        # 2. Transitions
        transitions = final_state.get("transitions", [])
        print(f"\nRecorded Transitions ({len(transitions)}):")
        for t in transitions:
            print(f"  [{t.action_type}] {t.source_state_id} -> {t.destination_state_id} | Outcome: {t.actual_outcome}")
            
        # 3. User Journeys
        journeys = report.journeys if (report and hasattr(report, "journeys")) else []
        print(f"\nTracked User Journeys ({len(journeys)}):")
        for j in journeys:
            print(f"\nJourney: {j.name} (Status: {j.status.value})")
            print(f"Description: {j.description}")
            if j.interruption_reason:
                print(f"Interruption Reason: {j.interruption_reason}")
            print("Steps:")
            for s in j.steps:
                status_symbol = "[X]" if s.completed else "[ ]"
                print(f"  {status_symbol} Step {s.step_index}: {s.name} ({s.action_type}) | Executed Q: {s.executed_question_id or '-'} | Failure: {s.failure_reason or '-'}")

        # 4. Save results to reports/
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        report_out = reports_dir / "phase1_journey_investigation_report.json"
        
        # Export clean dict
        report_dict = report.model_dump() if hasattr(report, "model_dump") else {}
        with open(report_out, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2, default=str)
        print(f"\n[Artifact Saved] Investigation report written to {report_out}")

        return report

    finally:
        orchestrator.close()


if __name__ == "__main__":
    run_journey_investigation()
