import json

with open("reports/saucedemo_test_execution_report_v2.json", "r", encoding="utf-8") as f:
    exec_rep = json.load(f)

with open("reports/saucedemo_generated_test_suite_v2.json", "r", encoding="utf-8") as f:
    suite = json.load(f)

test_map = {tc["test_id"]: tc for tc in suite.get("test_cases", [])}

lines = []
lines.append(f"Summary: total={exec_rep.get('total_generated_cases')}, executed={exec_rep.get('executed_count')}, passed={exec_rep.get('passed_count')}, failed={exec_rep.get('failed_count')}, rate={exec_rep.get('assertion_validity_rate')}")

for i, r in enumerate(exec_rep.get("results", [])):
    tid = r.get("test_id")
    tc = test_map.get(tid, {})
    status = r.get("status")
    cat = r.get("category") or tc.get("category")
    title = r.get("title") or tc.get("title")
    err_msg = r.get("error_message")
    failure_phase = r.get("failure_phase")
    ev_ids = r.get("source_evidence_ids") or tc.get("evidence_ids", [])
    assertions_eval = r.get("assertions_evaluated")
    assertions_passed = r.get("assertions_passed")
    steps = tc.get("steps", [])
    expected_results = tc.get("expected_results", [])
    preconditions = tc.get("preconditions", [])
    
    lines.append(f"\n[{i+1}] ID: {tid} | Status: {status} | Category: {cat}")
    lines.append(f"    Title: {title}")
    if err_msg or failure_phase:
        lines.append(f"    Failure Phase: {failure_phase} | Error: {err_msg}")
    lines.append(f"    Preconditions: {preconditions}")
    lines.append(f"    Source States: {tc.get('source_state_ids')} | Transitions: {tc.get('source_transition_ids')}")
    lines.append(f"    Evidence IDs: {ev_ids}")
    lines.append(f"    Steps ({len(steps)}): {[s.get('action_type') + ' ' + str(s.get('target')) for s in steps]}")
    lines.append(f"    Expected Results: {expected_results}")
    lines.append(f"    Assertions: evaluated={assertions_eval}, passed={assertions_passed}")

with open("scratch/audit_output_utf8.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Wrote audit_output_utf8.txt successfully")
