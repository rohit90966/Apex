import json

with open("reports/phase1_journey_investigation_report.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# 1. Find the question
q_id = "q_form_cls_7c6f45f964_boundary_password_length_form_2_password"
print(f"=== INVESTIGATING ANOMALOUS PROBE: {q_id} ===")

# 2. Evidence ledger entries for this question
evidence_list = data.get("evidence_ledger", [])
print(f"\nEvidence entries count: {len(evidence_list)}")
for ev in evidence_list:
    summary = ev.get("summary", "")
    if q_id in summary or "boundary_password_length" in summary:
        print("\nFound Evidence Entry:")
        print(f"  evidence_id: {ev.get('evidence_id')}")
        print(f"  source_state_id: {ev.get('source_state_id')}")
        print(f"  after_state_ref: {ev.get('after_state_ref')}")
        print(f"  summary: {summary}")
        print(f"  validation_message: {ev.get('validation_message')}")
        print(f"  locator: {ev.get('locator')}")

# 3. Transitions involving this
transitions = data.get("transitions", [])
for tr in transitions:
    action_label = tr.get("action_label", "")
    if "boundary_password" in action_label or tr.get("source_state_id") == "st_login_sign_in_to_sho_c1":
        print("\nFound Transition:")
        print(f"  action_type: {tr.get('action_type')}")
        print(f"  action_label: {action_label}")
        print(f"  source_state: {tr.get('source_state_id')}")
        print(f"  dest_state: {tr.get('destination_state_id')}")
        print(f"  actual_outcome: {tr.get('actual_outcome')}")
        print(f"  predicted_outcome: {tr.get('predicted_outcome')}")
        print(f"  is_route_change: {tr.get('is_route_change')}")
        print(f"  signals_matched: {tr.get('signals_matched')}")
        print(f"  signals_contradicted: {tr.get('signals_contradicted')}")
