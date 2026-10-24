"""
Autonomous Investigation of ShopSphere using VerityQA v1.0.0-frozen Engine.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.investigation_agent import run_investigation_with_handoff, INVESTIGATION_ENGINE_VERSION

async def main():
    target_url = "http://127.0.0.1:5173/"
    print(f"[VerityQA] Starting autonomous investigation of {target_url} using engine {INVESTIGATION_ENGINE_VERSION}...")

    report, contract = await run_investigation_with_handoff(
        url=target_url,
        description="ShopSphere E-Commerce Application",
        max_actions=16,
        max_time_seconds=120.0,
        headless=True,
        sandbox_mode=True,
        sandbox_origins=["http://127.0.0.1:5173"],
        exploration_policy="balanced",
    )

    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)

    contract_path = reports_dir / "shopsphere_handoff_contract_v1.json"
    contract_json = contract.model_dump_json(indent=2)
    contract_path.write_text(contract_json, encoding="utf-8")
    print(f"[VerityQA] Saved handoff contract to {contract_path} ({len(contract_json)} bytes)")

    report_path = reports_dir / "shopsphere_investigation_report.json"
    report_json = report.model_dump_json(indent=2)
    report_path.write_text(report_json, encoding="utf-8")
    print(f"[VerityQA] Saved investigation report to {report_path} ({len(report_json)} bytes)")

    print("\n=== INVESTIGATION SUMMARY ===")
    print(f"Investigation ID: {contract.investigation_id}")
    print(f"Discovered States: {len(contract.states)}")
    for s in contract.states:
        print(f" - State: {s.state_id} | Route: {s.route_or_url} | Title: {s.title}")

    print(f"\nVerified Transitions: {len(contract.verified_transitions)}")
    for t in contract.verified_transitions:
        print(f" - Transition: {t.transition_id} | {t.from_state_id} -> [{t.action_summary}] -> {t.to_state_id}")

    print(f"\nEvidence Ledger Count: {len(contract.evidence_ledger)}")
    print(f"Defect Findings: {len(contract.defect_findings)}")
    for df in contract.defect_findings:
        print(f" - Defect: {df.finding_id} | {df.title} | {df.description}")

if __name__ == "__main__":
    asyncio.run(main())
