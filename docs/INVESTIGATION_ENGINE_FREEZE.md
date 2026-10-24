# VerityQA Investigation Engine — Feature-Freeze & Handoff Specification

**Milestone Status**: `FEATURE-FROZEN` (`INVESTIGATION_ENGINE_FROZEN = True`)  
**Engine Version**: `1.0.0-frozen`  
**Handoff Contract Schema Version**: `1.0.0`

---

## 1. Frozen Public Interface

All consumers (including the FastAPI backend, CLI scripts, and the Test-Case Generation module) MUST interact with the investigation engine exclusively through the public functions and versioned schemas exported by [`app/investigation_agent/__init__.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/__init__.py) and [`app/investigation_agent/handoff_contract.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/handoff_contract.py). Downstream modules must never depend on internal LangGraph node state keys.

### 1.1 Starting an Autonomous Investigation with Handoff Contract Export

```python
from app.investigation_agent import (
    run_investigation_with_handoff,
    run_stateful_investigation,
    export_investigation_handoff,
    InvestigationHandoffContract,
    CONTRACT_SCHEMA_VERSION,
)

report, contract = await run_investigation_with_handoff(
    url="http://127.0.0.1:8995/login",
    description="Optional user context",
    requirements=["Optional requirement list"],
    auth_credentials={"username": "...", "password": "..."},
    max_actions=14,
    max_time_seconds=180.0,
    headless=True,
    exploration_policy="workflow_depth",  # "balanced" | "workflow_depth" | "breadth"
    sandbox_mode=False,                   # Disabled by default; True only for approved local/test origins
    sandbox_origins=[],                   # Explicit origin allowlist, e.g. ["http://127.0.0.1:8995"]
)
```

### 1.2 Synchronous Orchestrator & Contract Export Interface

```python
from app.investigation_agent import (
    InvestigationOrchestrator,
    build_investigation_workflow,
    export_investigation_handoff,
)

orchestrator = InvestigationOrchestrator(
    headless=True,
    sandbox_mode=False,
    sandbox_origins=[],
)
graph = build_investigation_workflow(orchestrator)
final_state = graph.invoke(initial_state)
contract = export_investigation_handoff(final_state)
```

---

## 2. Versioned Handoff Contract (`v1.0.0`)

Defined in [`app/investigation_agent/handoff_contract.py`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/handoff_contract.py) via `InvestigationHandoffContract`:

- **`schema_version`**: `"1.0.0"`
- **A. Application Model (`states`)**: `List[ContractStateRecord]` — state IDs, state class IDs, sanitized URLs, normalized routes, distinguishing DOM observations, authentication requirements, known preconditions, URL parameters, in-page state (`menu_open`, `has_validation_error`, `selected_option`, `counter_value`), grounded interactive controls, and sanitized form descriptors.
- **B. Verified Transitions (`verified_transitions`)**: `List[ContractTransitionRecord]` — source/destination state IDs, action type, grounded control identity (`selector`, `role`, `accessible_name`), preconditions, observed postconditions, actual effect, verification outcome (`VERIFIED`, `BLOCKED`, `UNCERTAIN`), repeatability, and backing `evidence_ids`.
- **C. Evidence Ledger (`evidence_ledger`)**: `List[ContractEvidenceRecord]` — stable `evidence_id`, observation timestamp, associated/before/after states, DOM/a11y observation summary, verification status, validation message/locator, and verified disk paths for screenshots (`None` unless the file exists on disk).
- **D. Defect Findings (`defect_findings`)**: `List[ContractDefectFinding]` — distinguishes `CONFIRMED_DEFECT` (strictly requiring `reproduction_verified=True` via 2-of-2 isolated context replay) from `UNVERIFIED_SUSPICION`, with reproduction steps, expected/actual behavior, `evidence_ids`, confidence, and limitations.
- **E. Investigation Completeness (`completeness`)**: `ContractCompletenessSummary` — discovered state and transition counts, pending frontier candidates with unresolved reasons, safety-blocked actions, authentication boundary summary, uncertainty score, and stopping reason.

### Enforced Validation Invariants
1. Schema version must equal `"1.0.0"`.
2. Every `evidence_id` referenced by any transition or defect finding must exist in `evidence_ledger`.
3. Every `source_state_id` and non-null `destination_state_id` in `verified_transitions` must exist in `states`.
4. `CONFIRMED_DEFECT` findings must have `reproduction_verified=True`.
5. `screenshot_artifact_path` is populated only when the file exists on disk.
6. Passwords, secrets, session cookies (`bench_session`), and tokens are automatically redacted.

---

## 3. Sandbox Safety Governance Boundary

Implemented in [`SafetyGovernor`](file:///d:/Hackathon/Code%20Apex/app/investigation_agent/governor.py):
- **Default Fail-Closed**: `sandbox_mode` defaults to `False`. Without explicit opt-in, all keywords in `FORBIDDEN_KEYWORDS` (including `"checkout"`) are blocked across all hosts.
- **Origin & Destination Verification**: When `sandbox_mode=True`, only explicitly listed local/test origins (`127.0.0.1`, `localhost`, `::1`, `.local`, `.test`, `testserver`) in `sandbox_origins` are accepted. External domains, wildcards (`*`), and embedded credentials are rejected. Both the current page origin and any resolved navigation/href destination origin must match the approved sandbox origin.
- **Unconditional Terminal Block**: Even inside an authorized local sandbox, `TERMINAL_FORBIDDEN_KEYWORDS` (`"place order"`, `"finish"`, `"pay now"`, `"complete purchase"`, `"confirm payment"`, `"buy now"`, `"delete account"`, `"wipe"`, `"drop database"`, `"sudo"`, `"transfer money"`, `"execute code"`, `"cancel account"`, `"remove user"`, `"terminate subscription"`) remain unconditionally blocked.

---

## 4. Known Engineering Limitations (Recorded at Freeze)

1. **Terminal Order Completion Gating**: Because `SafetyGovernor` unconditionally blocks terminal purchase/order-placement buttons (`"Place Order"` / `"finish"`), final post-order confirmation views (such as `ST_ORDER_CONFIRMED` at `/order-confirmation`) remain intentionally unreached by automated probing and are recorded as safety-gated boundaries.
2. **Multi-Option Dropdown Exhaustion under Tight Probe Budgets**: For `<select>` controls with $>2$ options, the control probe generator tests a representative non-default permutation first (`sort=za`) and prioritizes unvisited high-yield routes/controls before exhausting every secondary dropdown option (`sort=lohi`, `sort=hilo`), leaving secondary options in `pending_frontier_candidates` when probe budgets are bounded.
3. **Single-Session Stateful Continuity**: Authentication and shopping cart mutations are tracked in a single active browser context during exploration; isolated defect reproduction (`BugReproducer`) replays from the recorded `source_url` of the finding, which relies on the target route being reachable or statelessly reproducible in a fresh session.
4. **Freeze Policy**: The investigation engine is now closed to new graph nodes, heuristic re-weights, or architectural changes. It may only be reopened if a reproducible defect compromises safety, evidence integrity, or the `v1.0.0` handoff contract.
