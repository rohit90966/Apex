import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.investigation_agent import run_stateful_investigation

async def main():
    report = await run_stateful_investigation(
        url="https://www.saucedemo.com/",
        max_actions=0,
        max_time_seconds=120,
        headless=True
    )
    print("=== SECTION A: OVERVIEW ===")
    print(json.dumps(report.overview.model_dump(), indent=2))

    print("\n=== SECTION D: DISCOVERED APPLICATION MAP ===")
    for m in report.discovered_application_map:
        print(f" - State: {m.area_id} | URL: {m.route_or_url} | Title: {m.apparent_purpose}")

    print("\n=== SECTION E: USER WORKFLOWS (TRANSITIONS) ===")
    for w in report.user_workflows:
        print(f" Step {w.step_number} [{w.workflow_branch}]: {w.from_state} --> [{w.action_taken}] --> {w.to_state}")
        print(f"   Outcome: {w.observed_outcome}")

    print("\n=== SECTION G: BUSINESS RULES & HYPOTHESES ===")
    for r in report.business_rules_and_hypotheses:
        print(f" - [{r.category.value}] ({r.confidence:.0%}) {r.rule_id}: {r.description}")
        print(f"   Source: {r.source}")

    print("\n=== EVIDENCE LEDGER TRACE (Sample Entries) ===")
    for ev in report.evidence_and_traceability[:10]:
        print(f" - [{ev.get('type')}] ID: {ev.get('evidence_id')} (State: {ev.get('state_ref')}): {ev.get('summary')}")

if __name__ == "__main__":
    asyncio.run(main())
