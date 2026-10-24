import json

with open("reports/phase1_journey_investigation_report.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print("Keys:", list(data.keys()))
if "evidence_ledger" in data:
    print("evidence_ledger type:", type(data["evidence_ledger"]), len(data["evidence_ledger"]))
if "evidence_summary" in data:
    print("evidence_summary:", len(data["evidence_summary"]))
if "discovered_transitions" in data:
    print("discovered_transitions:", len(data["discovered_transitions"]))
if "transitions" in data:
    print("transitions:", len(data["transitions"]))
