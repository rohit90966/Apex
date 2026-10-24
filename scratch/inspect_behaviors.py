import json

with open("reports/phase1_journey_investigation_report.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print("=== OBSERVED BEHAVIORS ===")
for i, b in enumerate(data.get("observed_behaviors", [])):
    print(f"[{i}] {b}")

print("\n=== EVIDENCE AND TRACEABILITY ===")
ev = data.get("evidence_and_traceability", {})
if isinstance(ev, dict):
    for k, v in ev.items():
        print(f"  Key {k}: {v if not isinstance(v, list) else f'list of length {len(v)}'}")
        if isinstance(v, list) and len(v) > 0:
            for item in v[:5]:
                print(f"    - {item}")
