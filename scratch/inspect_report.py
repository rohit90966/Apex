import json
from pathlib import Path

report_path = Path("reports/phase1_journey_investigation_report.json")
if not report_path.exists():
    print("Report file does not exist!")
    exit(1)

with open(report_path, "r", encoding="utf-8") as f:
    data = json.load(f)

print("=" * 60)
print("OVERVIEW:")
print("=" * 60)
overview = data.get("overview", {})
for k in ["target_url", "final_url", "status", "stopping_reason", "actions_attempted", "actions_successful", "actions_verified_effects", "meaningful_states_discovered", "transitions_observed"]:
    print(f"  {k}: {overview.get(k)}")

print("\n" + "=" * 60)
print("JOURNEYS:")
print("=" * 60)
journeys = data.get("journeys", [])
print(f"Total journeys: {len(journeys)}")
for j in journeys:
    print(f"\nJourney ID: {j.get('journey_id')} | Name: {j.get('name')}")
    print(f"Status: {j.get('status')} | Current Step Index: {j.get('current_step_index')}")
    print(f"Interruption Reason: {j.get('interruption_reason')}")
    print(f"Evidence Refs: {j.get('evidence_refs')}")
    for s in j.get("steps", []):
        print(f"  Step {s.get('step_index')}: {s.get('name')}")
        print(f"    action_type: {s.get('action_type')}")
        print(f"    target_pattern: {s.get('target_pattern')}")
        print(f"    completed: {s.get('completed')}")
        print(f"    executed_question_id: {s.get('executed_question_id')}")
        print(f"    evidence_id: {s.get('evidence_id')}")
        print(f"    failure_reason: {s.get('failure_reason')}")

print("\n" + "=" * 60)
print("DECISION SNAPSHOTS:")
print("=" * 60)
snapshots = data.get("decision_snapshots", [])
for snap in snapshots:
    print(f"  Step {snap.get('step_number')}: Q={snap.get('question_id')}")
    print(f"    Action: {snap.get('selected_action')} ({snap.get('probe_family')})")
    print(f"    From: {snap.get('from_state_id')} ({snap.get('from_url')})")
    print(f"    To:   {snap.get('to_state_id')} ({snap.get('to_url')})")
    print(f"    Verdict: {snap.get('prediction_verdict')} | Effect: {snap.get('observed_effect')}")
