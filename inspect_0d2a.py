import json

data = json.load(open("data/runs/inv-d9e7cb600f1a/executions/exec-0d2a6011.json", encoding="utf-8"))
for r in data.get("results", []):
    tid = r.get("test_id")
    status = r.get("status")
    if status in ("ERROR", "BLOCKED", "INCOMPLETE", "FAILED"):
        print(f"=== {tid} ({status}) ===")
        print("Failure reason:", r.get("failure_reason"))
        for s in r.get("step_results", []):
            if s.get("status") != "PASSED":
                print(f"  Step {s.get('step_number')}: {s.get('action')} -> {s.get('status')}: {s.get('error_message')}")
