import json

def audit_saucedemo_reports():
    suite = json.load(open("reports/saucedemo_generated_test_suite_v1.json", encoding="utf-8"))
    contract = json.load(open("reports/saucedemo_handoff_contract_v1.json", encoding="utf-8"))
    
    print("=== TEST SUITE CASES ===")
    for tc in suite.get("test_cases", []):
        print(f"Test: {tc.get('test_id')} | Name: {tc.get('name')} | Start URL: {tc.get('start_url')}")
        for s in tc.get("steps", []):
            print(f"   Step {s.get('step_number')}: {s.get('action_type')} on {s.get('target')} (Desc: {s.get('description')})")
            
    print("\n=== CONTRACT VERIFIED TRANSITIONS ===")
    for t in contract.get("verified_transitions", []):
        print(f"Transition: {t.get('source_state_id')} -> {t.get('target_state_id')} | Action: {t.get('action_type')} on {t.get('target_locator')} (Success: {t.get('is_successful')})")
        
    print("\n=== INFERRED JOURNEYS ===")
    for j in contract.get("inferred_journeys", []):
        print(f"Journey: {j.get('journey_name')} | Status: {j.get('status')} | Completed: {j.get('completed_steps')}/{j.get('total_steps')}")
        for step in j.get("steps", []):
            print(f"   Step: {step.get('step_name')} | Status: {step.get('status')} | Action: {step.get('action_type')} | Target: {step.get('target')}")

if __name__ == "__main__":
    audit_saucedemo_reports()

def audit_inv_7c048b06():
    data = json.load(open('artifacts/checkpoints/inv-7c048b06.json'))
    history = data.get('agenda', {}).get('probe_history', [])
    print(f"Total probes: {len(history)}")
    for idx, p in enumerate(history):
        q_id = p.get('question_id', '')
        tgt = p.get('target', '')
        verdict = p.get('verdict', '')
        summary = p.get('summary', '')[:100]
        print(f"[{idx+1}] Q: {q_id} | Tgt: {tgt} | Verdict: {verdict} | Sum: {summary}")

if __name__ == "__main__":
    audit_inv_7c048b06()
