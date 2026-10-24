import json

with open("reports/phase1_journey_investigation_report.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Find decision snapshot 8 (step_number 8)
snapshots = data.get("decision_snapshots", [])
for s in snapshots:
    if s.get("step_number") == 8:
        print("=== DECISION SNAPSHOT 8 ===")
        print(json.dumps(s, indent=2))

# Find the form probe in questions or elsewhere
# Check checkpoints to see the full probe script and result!
import os
checkpoints = os.listdir("checkpoints") if os.path.exists("checkpoints") else []
print("\nCheckpoints:", checkpoints)
if checkpoints:
    latest_cp = os.path.join("checkpoints", sorted(checkpoints)[-1])
    print(f"Reading latest checkpoint: {latest_cp}")
    with open(latest_cp, "r", encoding="utf-8") as f:
        cp_data = json.load(f)
    print("Checkpoint keys:", list(cp_data.keys()))
    # Check transitions
    for tr in cp_data.get("transitions", []):
        if "boundary_password" in str(tr):
            print("\n=== TRANSITION IN CHECKPOINT ===")
            print(json.dumps(tr, indent=2))
    # Check evidence ledger
    for ev in cp_data.get("evidence_ledger", []):
        if "boundary_password" in str(ev):
            print("\n=== EVIDENCE IN CHECKPOINT ===")
            print(json.dumps(ev, indent=2))
