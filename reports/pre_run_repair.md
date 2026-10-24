# Pre-Run Engineering Diagnosis: Root Cause of Blocked Actions

**Target Website**: `https://www.saucedemo.com/`  
**Symptoms Observed**: 1 route discovered, 1 meaningful state, 0 transitions, 3 actions evaluated, 0 executed, 3 safety blocked, 0 evidence ledger entries, authentication not verified, report status falsely completed.

---

## 1. Trace of Candidate Action Execution Flow

```
Page Perception (https://www.saucedemo.com/)
  │  Initial DOM observation captured in state (NOT persisted to evidence ledger)
  ▼
Agenda Update (app/investigation_agent/agenda.py)
  │  Populates 3 form boundary questions (probe_family="forms")
  ▼
Select Question (app/investigation_agent/workflow.py:node_select_question)
  │  Picks Question 1 (e.g. form boundary check)
  ▼
Plan Probe (app/investigation_agent/workflow.py:node_plan_probe)
  │  Generates probe script containing steps: [FILL(username), FILL(password), CLICK(login-button)]
  ▼
Safety Gate (app/investigation_agent/workflow.py:node_safety_gate)
  │  Calls SafetyGovernor.can_execute("FILL", target, current_url)
  │  🚨 SAFETY REJECTION TRIGGERED HERE
  ▼
Evaluate Stopping & Loop
  │  Probe outcome recorded as "SAFETY_BLOCKED"
  │  Increments agenda.safety_blocks_encountered
  │  Repeats for Question 2 & Question 3
  ▼
Termination Threshold (app/investigation_agent/agenda.py:should_stop)
  │  safety_blocks_encountered (3) >= max_safety_blocks (3)
  │  Halts investigation immediately without trying alternative safe probes
  ▼
Report Generation (app/investigation_agent/report_builder.py & app/static/app.js)
  │  Zero evidence entries written to ledger
  │  Falsely displays "STATUS: completed", "200 OK (Browser Verified)", "100% Grounded in Live DOM"
```

---

## 2. Confirmed Root Causes & File References

### Root Cause A: Disconnect between Configured Allowed Hosts and SafetyGovernor Trusted Hosts
- **Files**: `app/investigation_agent/governor.py` (lines 48-64, 142-150), `app/config.py` (lines 35-38, 56-58).
- **Diagnosis**: 
  - `app/config.py` defines `VERITYQA_ALLOWED_HOSTS` which includes `saucedemo.com` and `www.saucedemo.com`. The API endpoint validation in `app/safety.py:validate_url_safety` permits the initial navigation.
  - However, `SafetyGovernor` in `app/investigation_agent/governor.py` defines an independent, hardcoded `trusted_hosts` default containing ONLY `localhost` and `127.0.0.1`.
  - `SafetyGovernor.is_host_trusted` returns `False` for `https://www.saucedemo.com/`.
  - In `SafetyGovernor.can_execute`:
    ```python
    if is_mutating:
        if not self.allows_mutations_for_url(current_url):
            return False, f"SAFETY_BLOCKED: Mutating interactions not permitted on untrusted host ({current_url})"
    ```
  - Because `FILL` and `SUBMIT` actions are classified as mutating, every candidate action was rejected as an "untrusted host" violation.

### Root Cause B: Premature Termination on Safety Block Threshold
- **Files**: `app/investigation_agent/agenda.py` (lines 66, 327-328), `app/investigation_agent/workflow.py` (lines 255-260).
- **Diagnosis**:
  - `InvestigationAgenda` configured `max_safety_blocks = 3`.
  - Each blocked step caused the agenda to increment `safety_blocks_encountered`.
  - Once 3 probes were blocked, `agenda.should_stop()` returned `True, "Safety block threshold reached (3/3)"`, halting the entire investigation without evaluating alternative safe observation or navigation probes.

### Root Cause C: Missing Initial State & Safety Decision Evidence in Ledger
- **Files**: `app/investigation_agent/workflow.py` (lines 138-160, 240-262, 363-395).
- **Diagnosis**:
  - `evidence_ledger` was ONLY updated inside `node_record`, which runs exclusively after a successful `node_execute_probe`.
  - `node_initialize` and `node_perceive` captured observations but never appended the baseline state to `evidence_ledger`.
  - `node_safety_gate` blocked actions without logging the rejection decision, reason, target, and evidence reference to `evidence_ledger`.
  - With 0 executed probes, `evidence_ledger` remained at 0 entries.

### Root Cause D: Authentication Workflow Disconnection in v2 Graph
- **Files**: `app/investigation_agent/workflow.py` (lines 1070-1100), `app/investigation_agent/authenticator.py`.
- **Diagnosis**:
  - `node_initialize` discovered credentials via `AuthenticationManager.discover_credentials`.
  - However, the v2 LangGraph node cycle did not include an authentication execution node or question type in `InvestigationAgenda`.
  - As a result, authentication was never attempted, leaving protected authenticated areas undiscovered.

### Root Cause E: Hardcoded Frontend Display Claims
- **Files**: `app/static/app.js` (lines 622-623, 647, 566), `app/investigation_agent/report_builder.py` (lines 110-120).
- **Diagnosis**:
  - `app/static/app.js` hardcoded `resHttpStatus.textContent = '200 OK (Browser Verified)'` and `resConfidence.textContent = '100% Grounded in Live DOM'` regardless of actual observation.
  - When zero probes execute, `report_builder.py` marked status as `COMPLETED` instead of `BLOCKED` or `PARTIAL`.

---

## 3. Implementation Details & Architecture Repairs

1. **Safety Governor & Allowed Target Alignment** (`app/investigation_agent/governor.py`):
   - Reconciled `trusted_hosts` with `settings.allowed_hosts_list` and local development hosts.
   - Enhanced `is_host_trusted` with robust URL parsing, strict domain/subdomain matching, and safe fail-closed behavior for empty/malformed/protocol-spoofed URLs.
   - Enforced high-impact action prohibition (`"place order"`, `"complete purchase"`, `"delete account"`, `"confirm payment"`, `"checkout"`, `"finish"`).
   - Separated dry-run safety validation from actual mutation counting via `can_execute(..., dry_run=True/False)` and `record_mutation()`.
   - Added `can_execute_probe` helper for multi-step probe validation.

2. **Full Evidence Ledger Integrity** (`app/investigation_agent/workflow.py`):
   - Baseline DOM perception immediately records a `DOM_OBSERVATION` entry in `evidence_ledger`.
   - Safety blocks record a `SAFETY_DECISION` entry in `evidence_ledger` with exact reason, target, and state reference.
   - Discrepancy, failure, and state transitions persist full empirical details.

3. **Safe Autonomous Authentication Pipeline** (`app/investigation_agent/workflow.py` & `authenticator.py`):
   - Integrates `AuthenticationManager.attempt_authentication` when an auth boundary is detected and credentials exist in state.
   - Verifies authenticated destination state, logs `AUTH_VERIFICATION` evidence, unlocks protected functionality, and populates the agenda with authenticated views.

4. **Truthful Status & Reporting** (`app/investigation_agent/report_builder.py` & `app/static/app.js`):
   - Status resolves to `InvestigationStatus.BLOCKED` when 0 actions succeed and actions are safety blocked.
   - Status resolves to `PARTIAL` when some actions execute and others are blocked.
   - Displays actual observed `http_status` or `Unknown / Not captured`.
   - Dynamic DOM grounding percentage calculated directly from verified empirical actions.
   - Status badge displays semantic color tags (`#dc2626` BLOCKED, `#d97706` PARTIAL, `#16a34a` COMPLETED).

---

## 4. Test Verification Suite (`tests/test_pre_run_repair.py`)

All 15 regression requirements verified green:
1. `test_authorized_target_allows_low_risk_probes`: PASSED
2. `test_unapproved_host_is_rejected`: PASSED
3. `test_empty_or_malformed_url_fails_closed`: PASSED
4. `test_navigation_to_unauthorized_host_blocked`: PASSED
5. `test_high_impact_action_blocked_by_default`: PASSED
6. `test_safety_budget_shared_and_accumulates`: PASSED
7. `test_rejected_probes_create_safety_decision_evidence`: PASSED
8. `test_initial_dom_observation_creates_evidence_even_if_blocked`: PASSED
9. `test_executed_probe_records_genuine_observations`: PASSED
10. `test_zero_executed_probes_yields_blocked_status`: PASSED
11. `test_missing_http_status_not_fabricated`: PASSED
12. `test_final_url_reflects_actual_browser_url`: PASSED
13. `test_auth_not_verified_without_authenticated_evidence`: PASSED
14. `test_two_level_state_identity_deterministic`: PASSED
15. `test_settings_allowed_hosts_integration`: PASSED

### Full Regression Suite Status:
- `tests/test_pre_run_repair.py`: 15 passed in 1.41s
- `tests/test_investigation_agnostic_guard.py`: 30 passed in 1.46s
- `tests/test_investigation_evidence_semantics.py`: 9 passed
- `tests/test_investigation_stream.py`: 3 passed
- `tests/test_investigation_brain.py`: 4 passed, 1 skipped
- `tests/test_investigation_agenda.py`: 4 passed in 21.03s
- `tests/test_investigation_probe_runner.py`: 4 passed
- `tests/test_investigation_reproducer.py`: 3 passed
- `tests/test_investigation_perception_v2.py`: 5 passed
- `tests/test_investigation_phase3_oracles_leads.py`: 4 passed in 85.77s
- **Total: 87 tests passed across all investigation test suites with 0 regressions.**
