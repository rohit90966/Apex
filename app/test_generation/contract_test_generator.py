"""
VerityQA Contract-Driven Test-Case Generator (v1.0.0).
======================================================
Consumes the versioned `InvestigationHandoffContract` artifact produced by the
frozen Investigation Engine and generates structured, evidence-grounded QA test cases.

Supports:
1. Workflow tests (verified multi-step user journeys from state-transition graph)
2. Transition tests (individual verified state changes and postconditions)
3. Validation tests (negative tests from observed form constraints & validation feedback)
4. State-dependent tests (controls conditioned on authentication, cart state, or active options)
5. Regression candidates (reusable regression scenarios from confirmed defects & invariants)
6. Coverage tracking (mapping tests to motivating states, transitions, and findings)
7. Evidence traceability (strict validation against contract evidence ledger)
8. Uncertainty handling (flagging unverified/blocked behaviors as hypotheses requiring confirmation)
9. Safe execution metadata (marking credentials, sandbox-only, or restricted actions)
"""

import re
import time
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Tuple
from pydantic import BaseModel, Field

from app.investigation_agent.handoff_contract import (
    CONTRACT_SCHEMA_VERSION,
    InvestigationHandoffContract,
    ContractStateRecord,
    ContractTransitionRecord,
    ContractDefectFinding,
)
from app.investigation_agent.governor import SafetyGovernor

logger = logging.getLogger(__name__)


class ExecutionSafetyClassification(str, Enum):
    SAFE_READ_ONLY = "SAFE_READ_ONLY"
    SAFE_REVERSIBLE = "SAFE_REVERSIBLE"
    REQUIRES_AUTH_CREDENTIALS = "REQUIRES_AUTH_CREDENTIALS"
    SANDBOX_ONLY = "SANDBOX_ONLY"
    RESTRICTED_DO_NOT_AUTO_RUN = "RESTRICTED_DO_NOT_AUTO_RUN"


class TestCategoryType(str, Enum):
    __test__ = False
    WORKFLOW = "workflow"
    TRANSITION = "transition"
    VALIDATION = "validation"
    STATE_DEPENDENT = "state_dependent"
    REGRESSION = "regression"
    EXPLORATORY_HYPOTHESIS = "exploratory_hypothesis"


class GroundedTestStep(BaseModel):
    step_number: int
    action_type: str
    selector: Optional[str] = None
    semantic_locator: Optional[Dict[str, Optional[str]]] = None
    input_value: Optional[str] = None
    description: str
    source_state_id: Optional[str] = None
    target_state_id: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)


class ExpectedResultSpec(BaseModel):
    description: str
    verification_type: str  # URL_CONTAINS, STATE_REACHED, VALIDATION_ERROR_PRESENT, DOM_MUTATION_OBSERVED, DEFECT_ABSENT, HYPOTHESIS_CONFIRMATION_REQUIRED
    expected_value: Optional[str] = None
    is_verified_by_evidence: bool = True
    is_hypothesis_requiring_confirmation: bool = False
    evidence_refs: List[str] = Field(default_factory=list)


class SafetyExecutionMetadata(BaseModel):
    classification: ExecutionSafetyClassification
    requires_authorization: bool = False
    requires_test_credentials: bool = False
    requires_sandbox: bool = False
    contains_destructive_or_financial_action: bool = False
    safe_for_unattended_execution: bool = True
    safety_notes: Optional[str] = None


class GeneratedContractTestCase(BaseModel):
    test_id: str
    title: str
    category: TestCategoryType
    objective: str
    priority: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    preconditions: List[str] = Field(default_factory=list)
    steps: List[GroundedTestStep] = Field(default_factory=list)
    expected_results: List[ExpectedResultSpec] = Field(default_factory=list)
    postconditions: List[str] = Field(default_factory=list)
    cleanup_requirements: List[str] = Field(default_factory=list)
    source_state_ids: List[str] = Field(default_factory=list)
    source_transition_ids: List[str] = Field(default_factory=list)
    source_finding_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    unresolved_assumptions: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    safety_metadata: SafetyExecutionMetadata


class TestGenerationCoverageMap(BaseModel):
    states_covered: Dict[str, List[str]] = Field(default_factory=dict)
    transitions_covered: Dict[str, List[str]] = Field(default_factory=dict)
    findings_covered: Dict[str, List[str]] = Field(default_factory=dict)
    unverified_hypotheses_flagged: List[str] = Field(default_factory=list)
    total_states_in_contract: int = 0
    covered_states_count: int = 0
    state_coverage_ratio: float = 0.0
    total_transitions_in_contract: int = 0
    covered_transitions_count: int = 0
    transition_coverage_ratio: float = 0.0


class ContractTestSuiteArtifact(BaseModel):
    schema_version: str = "1.0.0"
    source_investigation_id: str
    source_contract_version: str = CONTRACT_SCHEMA_VERSION
    target_url: str
    generated_at: float = Field(default_factory=time.time)
    generation_strategy: str = "DETERMINISTIC_GROUNDED"
    test_cases: List[GeneratedContractTestCase] = Field(default_factory=list)
    coverage_map: TestGenerationCoverageMap
    rejected_candidates_count: int = 0
    rejection_reasons: List[str] = Field(default_factory=list)


def _is_positive_counter(val: Any) -> bool:
    """Safely checks if counter_value is positive, handling str ('1', 'c1') and int (1)."""
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        val_clean = val.strip().lower().replace("c", "")
        if val_clean.isdigit():
            return int(val_clean) > 0
    return False


class ContractTestValidator:
    """
    Deterministic validation layer around generated test candidates.
    Rejects malformed cases, invalid evidence references, unsafe actions,
    ambiguous locators, missing preconditions, incorrect classifications,
    and unsupported expected results.
    """

    AMBIGUOUS_BARE_TAGS = {"div", "span", "button", "a", "input", "p", "section", "ul", "li"}

    @classmethod
    def validate_candidate(
        cls,
        candidate: GeneratedContractTestCase,
        contract: InvestigationHandoffContract,
    ) -> Tuple[bool, Optional[str]]:
        # 1. Structural completeness check
        if not candidate.test_id or not candidate.title.strip() or not candidate.objective.strip():
            return False, "MALFORMED_CASE: Missing test_id, title, or objective"
        if not candidate.steps:
            return False, f"MALFORMED_CASE [{candidate.test_id}]: Test case has no executable steps"
        if not candidate.expected_results:
            return False, f"MALFORMED_CASE [{candidate.test_id}]: Test case has no expected_results"

        known_ev_ids = {e.evidence_id for e in contract.evidence_ledger}
        state_map = {s.state_id: s for s in contract.states}
        known_st_ids = set(state_map.keys())
        known_tr_ids = {t.transition_id for t in contract.verified_transitions}
        finding_map = {d.finding_id: d for d in contract.defect_findings}
        known_df_ids = set(finding_map.keys())

        # 2. Unsafe action check (fail-closed: never permit live financial/destructive steps)
        for step in candidate.steps:
            combined_step = f"{step.action_type} {step.selector or ''} {step.description}".lower()
            for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS:
                if kw in combined_step:
                    return False, f"UNSAFE_ACTION_REJECTED [{candidate.test_id}]: Step {step.step_number} contains prohibited terminal action keyword '{kw}'"

        # 3. Traceability & referential integrity check
        if not candidate.source_state_ids and not candidate.source_transition_ids and not candidate.source_finding_ids:
            return False, f"MISSING_TRACEABILITY [{candidate.test_id}]: Test does not reference any source state, transition, or finding"

        for sid in candidate.source_state_ids:
            if sid not in known_st_ids:
                return False, f"INVALID_STATE_REF [{candidate.test_id}]: Unknown source_state_id '{sid}'"

        for tid in candidate.source_transition_ids:
            if tid not in known_tr_ids:
                return False, f"INVALID_TRANSITION_REF [{candidate.test_id}]: Unknown source_transition_id '{tid}'"

        for fid in candidate.source_finding_ids:
            if fid not in known_df_ids:
                return False, f"INVALID_FINDING_REF [{candidate.test_id}]: Unknown source_finding_id '{fid}'"

        for eid in candidate.evidence_ids:
            if eid not in known_ev_ids:
                return False, f"INVALID_EVIDENCE_REF [{candidate.test_id}]: Non-existent evidence_id '{eid}'"

        # 4. Expected result support & uncertainty check
        for exp in candidate.expected_results:
            for eid in exp.evidence_refs:
                if eid not in known_ev_ids:
                    return False, f"INVALID_EVIDENCE_REF [{candidate.test_id}]: Expected result references unknown evidence_id '{eid}'"
            if exp.is_verified_by_evidence:
                if not exp.evidence_refs and not candidate.evidence_ids:
                    return False, f"UNSUPPORTED_EXPECTED_RESULT [{candidate.test_id}]: Claimed verified expected result without backing evidence_refs"
            else:
                if not exp.is_hypothesis_requiring_confirmation:
                    return False, f"UNSUPPORTED_EXPECTED_RESULT [{candidate.test_id}]: Unverified expected result must be flagged with is_hypothesis_requiring_confirmation=True"

        # 5. Classification integrity check (hypotheses or tentative leads must never be classified as REGRESSION)
        if candidate.category == TestCategoryType.REGRESSION:
            for exp in candidate.expected_results:
                if exp.is_hypothesis_requiring_confirmation or not exp.is_verified_by_evidence:
                    return False, f"INCORRECT_CLASSIFICATION [{candidate.test_id}]: Exploratory hypothesis or unverified expectation cannot be classified as REGRESSION"
            for fid in candidate.source_finding_ids:
                f_rec = finding_map.get(fid)
                if f_rec and f_rec.classification != "CONFIRMED_DEFECT":
                    return False, f"INCORRECT_CLASSIFICATION [{candidate.test_id}]: Tentative/unverified finding '{fid}' ({f_rec.classification}) cannot be classified as REGRESSION"

        # 6. Preconditions completeness check for authenticated or state-conditioned source states
        first_step = candidate.steps[0]
        first_act = first_step.action_type.split(":")[0].upper()
        init_sid = first_step.source_state_id or (candidate.source_state_ids[0] if candidate.source_state_ids else None)
        init_st = state_map.get(init_sid) if init_sid else None
        if init_st and candidate.category != TestCategoryType.EXPLORATORY_HYPOTHESIS:
            needs_auth = init_st.requires_authentication and first_act not in ("AUTHENTICATE", "NAVIGATE", "GOTO")
            needs_cart = _is_positive_counter(init_st.in_page_state.get("counter_value"))
            if (needs_auth or needs_cart) and not candidate.preconditions:
                return False, f"MISSING_PRECONDITIONS [{candidate.test_id}]: Source state '{init_st.state_id}' requires authentication or cart state preconditions, but preconditions list is empty"

        # 7. Ambiguous / ungrounded locator check
        for step in candidate.steps:
            for eid in step.evidence_refs:
                if eid not in known_ev_ids:
                    return False, f"INVALID_EVIDENCE_REF [{candidate.test_id}]: Step {step.step_number} references unknown evidence_id '{eid}'"

            sel_raw = (step.selector or "").strip()
            act_base = step.action_type.split(":")[0].upper()
            if not sel_raw and act_base not in ("AUTHENTICATE", "RELOAD"):
                return False, f"AMBIGUOUS_LOCATOR [{candidate.test_id}]: Step {step.step_number} has empty selector"
            if sel_raw.startswith(("cls_", "st_")):
                return False, f"AMBIGUOUS_LOCATOR [{candidate.test_id}]: Step {step.step_number} uses internal state/class identifier '{sel_raw}' as DOM locator"
            if sel_raw.lower() in cls.AMBIGUOUS_BARE_TAGS:
                sem = step.semantic_locator or {}
                if not sem.get("name") and not sem.get("label"):
                    return False, f"AMBIGUOUS_LOCATOR [{candidate.test_id}]: Step {step.step_number} uses bare generic tag '{sel_raw}' without distinguishing attributes or accessible name"
            if "," in sel_raw:
                parts = [p.strip() for p in sel_raw.split(",") if p.strip()]
                if len(parts) > 1 and len(set(parts)) < len(parts):
                    return False, f"AMBIGUOUS_LOCATOR [{candidate.test_id}]: Step {step.step_number} contains duplicate selector parts '{sel_raw}'"

        return True, None


class ContractTestGenerator:
    """
    Generates structured, evidence-grounded QA test cases directly from a validated
    `InvestigationHandoffContract`.
    """

    def __init__(self, use_llm_enrichment: bool = False, provider: Optional[Any] = None):
        self.use_llm_enrichment = use_llm_enrichment
        self.provider = provider

    def generate_suite(self, contract: InvestigationHandoffContract) -> ContractTestSuiteArtifact:
        # Ensure contract passes schema invariants first
        contract.validate_contract_invariants()

        state_map: Dict[str, ContractStateRecord] = {s.state_id: s for s in contract.states}
        candidates: List[GeneratedContractTestCase] = []

        # 1. Workflow Tests (multi-step verified journeys from state-transition graph)
        candidates.extend(self._generate_workflow_tests(contract, state_map))

        # 2. Transition Tests (individual state transitions and verified postconditions)
        candidates.extend(self._generate_transition_tests(contract, state_map))

        # 3. Validation Tests (negative form constraint and validation feedback tests)
        candidates.extend(self._generate_validation_tests(contract, state_map))

        # 4. State-Dependent Tests (controls conditioned on auth, cart, drawer, or sort options)
        candidates.extend(self._generate_state_dependent_tests(contract, state_map))

        # 5. Regression Candidates (confirmed defect regressions and verified invariants)
        candidates.extend(self._generate_regression_tests(contract, state_map))

        # 6. Exploratory Hypothesis Tests (for unverified suspicions or pending/gated workflow steps)
        candidates.extend(self._generate_exploratory_hypothesis_tests(contract, state_map))

        strategy = "DETERMINISTIC_GROUNDED"
        if self.use_llm_enrichment:
            llm_candidates = self._propose_llm_candidates(contract)
            if llm_candidates:
                candidates.extend(llm_candidates)
                strategy = "HYBRID_LLM_DETERMINISTIC"

        # Validate all candidates deterministically and deduplicate identical step+category signatures
        accepted: List[GeneratedContractTestCase] = []
        rejection_reasons: List[str] = []
        seen_signatures: Set[Tuple[Any, ...]] = set()
        seen_ids: Set[str] = set()

        for cand in candidates:
            ok, reason = ContractTestValidator.validate_candidate(cand, contract)
            if not ok:
                rejection_reasons.append(reason or f"Rejected {cand.test_id}")
                continue

            step_sig = (
                cand.category.value,
                tuple(cand.preconditions),
                tuple((s.action_type, s.selector, s.source_state_id, s.target_state_id) for s in cand.steps),
            )
            if cand.test_id in seen_ids or step_sig in seen_signatures:
                rejection_reasons.append(f"DUPLICATE_TEST_REJECTED [{cand.test_id}]: Duplicate test signature {step_sig[0]}")
                continue

            seen_ids.add(cand.test_id)
            seen_signatures.add(step_sig)
            accepted.append(cand)

        coverage_map = self._build_coverage_map(accepted, contract)

        return ContractTestSuiteArtifact(
            schema_version="1.0.0",
            source_investigation_id=contract.investigation_id,
            source_contract_version=contract.schema_version,
            target_url=contract.target_url,
            generation_strategy=strategy,
            test_cases=accepted,
            coverage_map=coverage_map,
            rejected_candidates_count=len(rejection_reasons),
            rejection_reasons=rejection_reasons,
        )

    def _propose_llm_candidates(self, contract: InvestigationHandoffContract) -> List[GeneratedContractTestCase]:
        """
        Optional LLM-assisted test candidate generation constrained by the handoff contract.
        All returned candidates are subsequently gated by `ContractTestValidator.validate_candidate`.
        """
        try:
            from app.llm import get_llm_provider
            prov = self.provider or get_llm_provider()
            if not prov:
                return []
            prompt = (
                "Generate additional grounded QA test cases in JSON from this contract summary.\n"
                f"Origin: {contract.application_origin}\n"
                f"States: {[s.state_id for s in contract.states]}\n"
                f"Transitions: {[t.transition_id for t in contract.verified_transitions]}\n"
                f"Evidence IDs: {[e.evidence_id for e in contract.evidence_ledger[:10]]}\n"
            )
            raw = prov.generate_json(prompt) if hasattr(prov, "generate_json") else None
            if not isinstance(raw, list):
                return []
            out: List[GeneratedContractTestCase] = []
            for item in raw:
                if isinstance(item, dict):
                    try:
                        out.append(GeneratedContractTestCase(**item))
                    except Exception:
                        continue
            return out
        except Exception as e:
            logger.debug("[ContractTestGenerator] LLM enrichment skipped: %s", e)
            return []

    def _classify_safety(
        self,
        requires_auth: bool,
        steps: List[GroundedTestStep],
        is_mutating: bool = False,
    ) -> SafetyExecutionMetadata:
        combined = " ".join(f"{s.action_type} {s.selector or ''} {s.description}".lower() for s in steps)
        needs_sandbox = any(kw in combined for kw in SafetyGovernor.SANDBOX_SYNTHETIC_CHECKOUT_KEYWORDS)
        has_terminal = any(kw in combined for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS)

        if has_terminal:
            return SafetyExecutionMetadata(
                classification=ExecutionSafetyClassification.RESTRICTED_DO_NOT_AUTO_RUN,
                requires_authorization=True,
                requires_test_credentials=requires_auth,
                requires_sandbox=True,
                contains_destructive_or_financial_action=True,
                safe_for_unattended_execution=False,
                safety_notes="Contains high-impact terminal keyword; blocked from automated execution.",
            )
        if needs_sandbox:
            return SafetyExecutionMetadata(
                classification=ExecutionSafetyClassification.SANDBOX_ONLY,
                requires_authorization=True,
                requires_test_credentials=requires_auth,
                requires_sandbox=True,
                contains_destructive_or_financial_action=False,
                safe_for_unattended_execution=False,
                safety_notes="Requires explicit sandbox authorization on approved local/test origin.",
            )
        if requires_auth:
            return SafetyExecutionMetadata(
                classification=ExecutionSafetyClassification.REQUIRES_AUTH_CREDENTIALS,
                requires_authorization=True,
                requires_test_credentials=True,
                requires_sandbox=False,
                contains_destructive_or_financial_action=False,
                safe_for_unattended_execution=True,
                safety_notes="Requires authorized test account credentials.",
            )
        if is_mutating:
            return SafetyExecutionMetadata(
                classification=ExecutionSafetyClassification.SAFE_REVERSIBLE,
                requires_authorization=False,
                requires_test_credentials=False,
                requires_sandbox=False,
                contains_destructive_or_financial_action=False,
                safe_for_unattended_execution=True,
            )
        return SafetyExecutionMetadata(
            classification=ExecutionSafetyClassification.SAFE_READ_ONLY,
            requires_authorization=False,
            requires_test_credentials=False,
            requires_sandbox=False,
            contains_destructive_or_financial_action=False,
            safe_for_unattended_execution=True,
        )

    def _generate_workflow_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """Reconstructs verified multi-step user journeys from the state-transition graph."""
        tests: List[GeneratedContractTestCase] = []
        verified_trs = [
            t for t in contract.verified_transitions
            if t.verification_outcome == "VERIFIED" and t.destination_state_id
        ]
        if not verified_trs:
            return tests

        # Build adjacency list of verified transitions to find longest/meaningful multi-step chains
        chains: List[List[ContractTransitionRecord]] = []
        by_src: Dict[str, List[ContractTransitionRecord]] = {}
        for tr in verified_trs:
            by_src.setdefault(tr.source_state_id, []).append(tr)

        def _dfs(curr_chain: List[ContractTransitionRecord], visited_states: Set[str]):
            last_tr = curr_chain[-1]
            next_st = last_tr.destination_state_id
            if not next_st or len(curr_chain) >= 6:
                chains.append(list(curr_chain))
                return
            next_candidates = [
                t for t in by_src.get(next_st, [])
                if t.destination_state_id and t.destination_state_id not in visited_states
            ]
            if not next_candidates:
                chains.append(list(curr_chain))
                return
            for nxt in next_candidates:
                # Entity consistency check:
                # If nxt targets an entity-specific control (e.g. in cart or detail view), ensure that entity was added/viewed earlier in curr_chain
                nxt_sel = (nxt.grounded_control.selector or "").lower()
                nxt_desc = (nxt.grounded_control.target_description or "").lower()
                chain_text = " ".join(f"{t.grounded_control.selector or ''} {t.grounded_control.target_description or ''}".lower() for t in curr_chain)

                if "cart" in (nxt.source_state_id or "").lower() and ("remove" in nxt_sel or "item" in nxt_sel or "title" in nxt_sel):
                    m = re.search(r'(?:remove[-_]|item[-_])([a-z0-9_-]+)', nxt_sel + " " + nxt_desc)
                    if m:
                        ent = m.group(1)
                        if ent not in chain_text and not any(kw in chain_text for kw in ent.split("-") if len(kw) > 3):
                            continue
                elif "remove" in nxt_sel or "remove" in nxt_desc:
                    m = re.search(r'remove[-_]([a-z0-9_-]+)', nxt_sel + " " + nxt_desc)
                    if m:
                        ent = m.group(1)
                        if ent not in chain_text and not any(kw in chain_text for kw in ent.split("-") if len(kw) > 3):
                            continue

                visited_states.add(nxt.destination_state_id)
                curr_chain.append(nxt)
                _dfs(curr_chain, visited_states)
                curr_chain.pop()
                visited_states.remove(nxt.destination_state_id)

        # Prioritize workflows starting from root entry points or unauthenticated initial states
        entry_trs = [
            t for t in verified_trs
            if t.actual_effect != "VALIDATION_FEEDBACK"
            and (
                t.source_state_id == "st_root_view_view"
                or (t.source_state_id in state_map and not state_map[t.source_state_id].requires_authentication)
                or (state_map.get(t.source_state_id) and state_map[t.source_state_id].url == contract.target_url)
            )
        ]
        if not entry_trs:
            entry_trs = [t for t in verified_trs if t.actual_effect != "VALIDATION_FEEDBACK"]

        for start_tr in entry_trs:
            _dfs([start_tr], {start_tr.source_state_id, start_tr.destination_state_id or ""})

        # Sort chains by length descending
        chains.sort(key=lambda c: len(c), reverse=True)
        selected_chains: List[List[ContractTransitionRecord]] = []
        covered_end_states: Set[str] = set()

        for ch in chains:
            if len(ch) < 2:
                continue
            end_st = ch[-1].destination_state_id or ""
            if end_st not in covered_end_states:
                selected_chains.append(ch)
                covered_end_states.add(end_st)
            if len(selected_chains) >= 3:
                break

        # If no 2+ chain was formed (e.g. star topology from same state), combine sequential verified route transitions
        if not selected_chains and len(verified_trs) >= 2:
            non_val = [t for t in verified_trs if t.actual_effect != "VALIDATION_FEEDBACK"]
            if len(non_val) >= 2:
                selected_chains.append(non_val[:4])

        for idx, chain in enumerate(selected_chains):
            start_state = state_map.get(chain[0].source_state_id)
            end_state = state_map.get(chain[-1].destination_state_id or "")
            start_url = start_state.url if start_state else contract.target_url

            steps: List[GroundedTestStep] = [
                GroundedTestStep(
                    step_number=1,
                    action_type="NAVIGATE",
                    selector=start_url,
                    semantic_locator={"role": "document", "name": start_state.page_title if start_state else "Entry", "label": None, "css": None},
                    description=f"Navigate to initial workflow entry point {start_url}",
                    source_state_id=chain[0].source_state_id,
                    target_state_id=chain[0].source_state_id,
                    evidence_refs=chain[0].evidence_ids[:1],
                )
            ]

            all_st_ids: List[str] = []
            all_tr_ids: List[str] = []
            all_ev_ids: List[str] = []
            expected_results: List[ExpectedResultSpec] = []
            requires_auth = False

            for s_idx, tr in enumerate(chain, start=2):
                if tr.source_state_id not in all_st_ids:
                    all_st_ids.append(tr.source_state_id)
                if tr.destination_state_id and tr.destination_state_id not in all_st_ids:
                    all_st_ids.append(tr.destination_state_id)
                all_tr_ids.append(tr.transition_id)
                for eid in tr.evidence_ids:
                    if eid not in all_ev_ids:
                        all_ev_ids.append(eid)

                if "AUTHENTICATE" in tr.action_type.upper() or "Requires authenticated session" in tr.required_preconditions:
                    requires_auth = True

                ctrl = tr.grounded_control
                steps.append(
                    GroundedTestStep(
                        step_number=s_idx,
                        action_type=tr.action_type,
                        selector=ctrl.selector or ctrl.target_description,
                        semantic_locator={
                            "role": ctrl.role,
                            "name": ctrl.accessible_name,
                            "label": None,
                            "css": ctrl.selector,
                        },
                        description=f"Execute {tr.action_type} on '{ctrl.target_description}' to transition from {tr.source_state_id} to {tr.destination_state_id}",
                        source_state_id=tr.source_state_id,
                        target_state_id=tr.destination_state_id,
                        evidence_refs=list(tr.evidence_ids),
                    )
                )

                dst_rec = state_map.get(tr.destination_state_id or "")
                post_desc = tr.observed_postconditions[0] if tr.observed_postconditions else f"Transition to {tr.destination_state_id}"
                expected_results.append(
                    ExpectedResultSpec(
                        description=post_desc,
                        verification_type="STATE_REACHED" if tr.actual_effect != "ROUTE_TRANSITION" else "URL_CONTAINS",
                        expected_value=dst_rec.normalized_route if dst_rec else tr.destination_state_id,
                        is_verified_by_evidence=True,
                        is_hypothesis_requiring_confirmation=False,
                        evidence_refs=list(tr.evidence_ids),
                    )
                )

            safety_meta = self._classify_safety(requires_auth=requires_auth, steps=steps, is_mutating=True)
            end_desc = end_state.description if end_state else chain[-1].destination_state_id

            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-WF-{idx + 1:02d}",
                    title=f"End-to-End Workflow: {start_state.page_title if start_state else 'Start'} to {end_desc}",
                    category=TestCategoryType.WORKFLOW,
                    objective=f"Verify multi-step user workflow across {len(chain)} verified transitions reaching {end_desc}.",
                    priority="HIGH",
                    preconditions=list(dict.fromkeys(chain[0].required_preconditions or [f"Application reachable at {start_url}"])),
                    steps=steps,
                    expected_results=expected_results,
                    postconditions=[f"Browser reaches verified target state '{chain[-1].destination_state_id}'"],
                    cleanup_requirements=["Reset session / clear cart state after workflow execution"],
                    source_state_ids=all_st_ids,
                    source_transition_ids=all_tr_ids,
                    evidence_ids=all_ev_ids,
                    confidence=1.0,
                    unresolved_assumptions=[],
                    tags=["workflow", "e2e", "verified_journey"],
                    safety_metadata=safety_meta,
                )
            )

        return tests

    def _generate_transition_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """Generates individual state-transition tests from verified transitions."""
        tests: List[GeneratedContractTestCase] = []
        for idx, tr in enumerate(contract.verified_transitions):
            if tr.verification_outcome != "VERIFIED" or not tr.destination_state_id:
                continue

            # Skip if transition contains terminal forbidden action
            ctrl = tr.grounded_control
            combined_ctrl = f"{tr.action_type} {ctrl.selector or ''} {ctrl.target_description}".lower()
            if any(kw in combined_ctrl for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS):
                continue

            src_st = state_map.get(tr.source_state_id)
            dst_st = state_map.get(tr.destination_state_id)
            requires_auth = bool(
                (src_st and src_st.requires_authentication)
                or "AUTHENTICATE" in tr.action_type.upper()
                or "Requires authenticated session" in tr.required_preconditions
            )

            step = GroundedTestStep(
                step_number=1,
                action_type=tr.action_type,
                selector=ctrl.selector or ctrl.target_description,
                semantic_locator={
                    "role": ctrl.role,
                    "name": ctrl.accessible_name,
                    "label": None,
                    "css": ctrl.selector,
                },
                description=f"From state '{tr.source_state_id}', perform {tr.action_type} on '{ctrl.target_description}'",
                source_state_id=tr.source_state_id,
                target_state_id=tr.destination_state_id,
                evidence_refs=list(tr.evidence_ids),
            )

            ver_type = "URL_CONTAINS" if tr.actual_effect == "ROUTE_TRANSITION" else (
                "VALIDATION_ERROR_PRESENT" if tr.actual_effect == "VALIDATION_FEEDBACK" else "DOM_MUTATION_OBSERVED"
            )
            exp_val = dst_st.url if dst_st else tr.destination_state_id
            post_summary = "; ".join(tr.observed_postconditions) if tr.observed_postconditions else f"Verify transition to {tr.destination_state_id}"

            safety_meta = self._classify_safety(
                requires_auth=requires_auth,
                steps=[step],
                is_mutating=(tr.actual_effect in ("IN_PAGE_STATE_MUTATION", "ROUTE_TRANSITION")),
            )

            tr_preconds = [f"Browser is in state '{tr.source_state_id}' ({src_st.url if src_st else ''})"] + list(tr.required_preconditions)
            if src_st and _is_positive_counter(src_st.in_page_state.get("counter_value")):
                c_pre = f"Cart/counter badge value = {src_st.in_page_state['counter_value']}"
                if c_pre not in tr_preconds:
                    tr_preconds.append(c_pre)
            if "remove" in (step.selector or "").lower():
                rem_pre = "Prerequisite: Target item is added to cart prior to executing remove control"
                if rem_pre not in tr_preconds:
                    tr_preconds.append(rem_pre)
            if "cart" in (src_st.url if src_st else "").lower() or any("cart" in s.lower() for s in [tr.source_state_id, tr.destination_state_id or ""]):
                item_m = re.search(r'item[-_]?(\d+)', (step.selector or "") + " " + (ctrl.target_description or ""))
                if item_m:
                    it_pre = f"Prerequisite: Item '{item_m.group(0)}' is added to cart prior to test execution"
                    if it_pre not in tr_preconds:
                        tr_preconds.append(it_pre)
            if src_st and src_st.in_page_state.get("menu_open"):
                menu_pre = "In-page navigation/filter drawer is open"
                if menu_pre not in tr_preconds:
                    tr_preconds.append(menu_pre)

            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-TR-{idx + 1:02d}",
                    title=f"State Transition: {tr.action_type} on {ctrl.target_description} ({tr.source_state_id} -> {tr.destination_state_id})",
                    category=TestCategoryType.TRANSITION,
                    objective=f"Verify that executing {tr.action_type} on '{ctrl.target_description}' transitions from '{tr.source_state_id}' to '{tr.destination_state_id}'.",
                    priority="HIGH" if tr.actual_effect == "ROUTE_TRANSITION" else "MEDIUM",
                    preconditions=tr_preconds,
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description=post_summary,
                            verification_type=ver_type,
                            expected_value=exp_val,
                            is_verified_by_evidence=True,
                            is_hypothesis_requiring_confirmation=False,
                            evidence_refs=list(tr.evidence_ids),
                        )
                    ],
                    postconditions=[f"Active state is '{tr.destination_state_id}'"],
                    cleanup_requirements=[],
                    source_state_ids=list(dict.fromkeys([tr.source_state_id, tr.destination_state_id])),
                    source_transition_ids=[tr.transition_id],
                    evidence_ids=list(tr.evidence_ids),
                    confidence=tr.confidence,
                    unresolved_assumptions=[],
                    tags=["transition", tr.actual_effect.lower()],
                    safety_metadata=safety_meta,
                )
            )

        return tests

    def _generate_validation_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """Generates negative validation tests from actually observed form constraints and validation feedback."""
        tests: List[GeneratedContractTestCase] = []
        val_idx = 0

        # 1. From transitions that produced VALIDATION_FEEDBACK
        for tr in contract.verified_transitions:
            dst_st = state_map.get(tr.destination_state_id or "")
            has_val_err = bool(dst_st and dst_st.in_page_state.get("has_validation_error"))
            if tr.actual_effect != "VALIDATION_FEEDBACK" and not has_val_err:
                continue

            val_idx += 1
            src_st = state_map.get(tr.source_state_id)
            val_msg = (dst_st.in_page_state.get("validation_error_message") if dst_st else None) or "Validation error displayed"
            val_loc = (dst_st.in_page_state.get("validation_error_locator") if dst_st else None) or tr.grounded_control.selector

            step = GroundedTestStep(
                step_number=1,
                action_type=tr.action_type,
                selector=tr.grounded_control.selector or tr.grounded_control.target_description,
                semantic_locator={
                    "role": tr.grounded_control.role or "form",
                    "name": tr.grounded_control.accessible_name,
                    "label": None,
                    "css": tr.grounded_control.selector,
                },
                input_value="",
                description=f"Submit invalid/blank input via '{tr.grounded_control.target_description}' on {src_st.url if src_st else tr.source_state_id}",
                source_state_id=tr.source_state_id,
                target_state_id=tr.destination_state_id,
                evidence_refs=list(tr.evidence_ids),
            )

            safety_meta = self._classify_safety(
                requires_auth=bool(src_st and src_st.requires_authentication),
                steps=[step],
                is_mutating=False,
            )

            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-VAL-{val_idx:02d}",
                    title=f"Form Validation Rejection: {tr.grounded_control.target_description} on {src_st.normalized_route if src_st else tr.source_state_id}",
                    category=TestCategoryType.VALIDATION,
                    objective=f"Verify form validation rejects invalid/empty submission with observed message: '{val_msg}'.",
                    priority="HIGH",
                    preconditions=[f"Navigate to form at {src_st.url if src_st else contract.target_url}"],
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description=f"Form submission is rejected and displays validation feedback: '{val_msg}' (locator: {val_loc})",
                            verification_type="VALIDATION_ERROR_PRESENT",
                            expected_value=val_msg,
                            is_verified_by_evidence=True,
                            is_hypothesis_requiring_confirmation=False,
                            evidence_refs=list(tr.evidence_ids),
                        )
                    ],
                    postconditions=["User remains on form view with error feedback visible"],
                    cleanup_requirements=[],
                    source_state_ids=list(dict.fromkeys([s for s in (tr.source_state_id, tr.destination_state_id) if s])),
                    source_transition_ids=[tr.transition_id],
                    evidence_ids=list(tr.evidence_ids),
                    confidence=1.0,
                    unresolved_assumptions=[],
                    tags=["validation", "negative_test", "form_constraints"],
                    safety_metadata=safety_meta,
                )
            )

        # 2. From states with observed validation errors not yet covered by a transition
        covered_states = {sid for t in tests for sid in t.source_state_ids}
        for st in contract.states:
            if st.in_page_state.get("has_validation_error") and st.state_id not in covered_states:
                val_msg = st.in_page_state.get("validation_error_message") or "Validation constraint enforced"
                matched_ev = [
                    e.evidence_id for e in contract.evidence_ledger
                    if e.associated_state_id == st.state_id or e.after_state_id == st.state_id
                ]
                if not matched_ev and contract.evidence_ledger:
                    matched_ev = [contract.evidence_ledger[0].evidence_id]
                if not matched_ev:
                    continue

                val_idx += 1
                form_sel = st.forms[0].form_selector if st.forms else "form"
                step = GroundedTestStep(
                    step_number=1,
                    action_type="SUBMIT",
                    selector=form_sel,
                    semantic_locator={"role": "form", "name": None, "label": None, "css": form_sel},
                    input_value="",
                    description=f"Submit form '{form_sel}' with missing required fields at {st.url}",
                    source_state_id=st.state_id,
                    target_state_id=st.state_id,
                    evidence_refs=matched_ev,
                )
                safety_meta = self._classify_safety(requires_auth=st.requires_authentication, steps=[step], is_mutating=False)
                tests.append(
                    GeneratedContractTestCase(
                        test_id=f"TC-VAL-{val_idx:02d}",
                        title=f"Observed Validation Constraint on {st.normalized_route}",
                        category=TestCategoryType.VALIDATION,
                        objective=f"Verify validation error '{val_msg}' is enforced on {st.normalized_route}.",
                        priority="MEDIUM",
                        preconditions=[f"Navigate to {st.url}"],
                        steps=[step],
                        expected_results=[
                            ExpectedResultSpec(
                                description=f"Validation feedback displayed: '{val_msg}'",
                                verification_type="VALIDATION_ERROR_PRESENT",
                                expected_value=val_msg,
                                is_verified_by_evidence=True,
                                is_hypothesis_requiring_confirmation=False,
                                evidence_refs=matched_ev,
                            )
                        ],
                        postconditions=["Validation error remains visible"],
                        cleanup_requirements=[],
                        source_state_ids=[st.state_id],
                        source_transition_ids=[],
                        evidence_ids=matched_ev,
                        confidence=0.95,
                        unresolved_assumptions=[],
                        tags=["validation", "negative_test"],
                        safety_metadata=safety_meta,
                    )
                )

        return tests

    def _generate_state_dependent_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """
        Generates tests for controls whose behavior depends on authentication,
        cart contents, active sort options, or in-page drawer state.
        """
        tests: List[GeneratedContractTestCase] = []
        sd_idx = 0

        for st in contract.states:
            has_state_condition = bool(
                st.requires_authentication
                or st.in_page_state.get("menu_open")
                or st.in_page_state.get("selected_option")
                or st.in_page_state.get("counter_value") is not None
                or st.url_parameters
            )
            if not has_state_condition:
                continue

            # Prioritize verified transitions originating from this conditioned state
            outgoing_trs = [
                t for t in contract.verified_transitions
                if t.source_state_id == st.state_id and t.verification_outcome == "VERIFIED"
            ]
            incoming_trs = [
                t for t in contract.verified_transitions
                if t.destination_state_id == st.state_id and t.verification_outcome == "VERIFIED"
            ]

            if outgoing_trs:
                tr = outgoing_trs[0]
                is_outgoing = True
            elif incoming_trs:
                tr = incoming_trs[0]
                is_outgoing = False
            else:
                continue

            ctrl = tr.grounded_control
            combined_ctrl = f"{tr.action_type} {ctrl.selector or ''} {ctrl.target_description}".lower()
            if any(kw in combined_ctrl for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS):
                continue

            sd_idx += 1
            cond_parts: List[str] = []
            if is_outgoing:
                cond_parts.append(f"Browser is in state '{st.state_id}' ({st.url})")
                if st.requires_authentication:
                    cond_parts.append("Authenticated user session active")
                if st.in_page_state.get("selected_option"):
                    cond_parts.append(f"Selected sort/filter option = '{st.in_page_state['selected_option']}'")
                if st.in_page_state.get("menu_open"):
                    cond_parts.append("In-page navigation/filter drawer is open")
                if st.in_page_state.get("counter_value") is not None:
                    cond_parts.append(f"Cart/counter badge value = {st.in_page_state['counter_value']}")
                if st.url_parameters:
                    cond_parts.append(f"URL state parameters = {st.url_parameters}")

                exp_desc = f"State-conditioned transition succeeds and reaches target state '{tr.destination_state_id}'"
                exp_val = tr.destination_state_id
            else:
                src_st = state_map.get(tr.source_state_id)
                src_url = src_st.url if src_st else st.url
                cond_parts.append(f"Browser is in state '{tr.source_state_id}' ({src_url})")
                if src_st and src_st.requires_authentication:
                    cond_parts.append("Authenticated user session active")
                if src_st and src_st.url_parameters:
                    cond_parts.append(f"URL state parameters = {src_st.url_parameters}")

                exp_desc = f"Transition establishes conditioned state '{st.state_id}' with distinguishing observations: {st.distinguishing_observations}"
                exp_val = st.state_id

            step = GroundedTestStep(
                step_number=1,
                action_type=tr.action_type,
                selector=ctrl.selector or ctrl.target_description,
                semantic_locator={
                    "role": ctrl.role,
                    "name": ctrl.accessible_name,
                    "label": None,
                    "css": ctrl.selector,
                },
                description=f"Under state condition ({'; '.join(cond_parts)}), execute {tr.action_type} on '{ctrl.target_description}'",
                source_state_id=tr.source_state_id,
                target_state_id=tr.destination_state_id,
                evidence_refs=list(tr.evidence_ids),
            )

            safety_meta = self._classify_safety(
                requires_auth=st.requires_authentication,
                steps=[step],
                is_mutating=True,
            )

            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-SD-{sd_idx:02d}",
                    title=f"State-Dependent Behavior on {st.normalized_route} ({', '.join(st.distinguishing_observations[:2]) or st.state_id})",
                    category=TestCategoryType.STATE_DEPENDENT,
                    objective=f"Verify state-dependent behavior of '{ctrl.target_description}' when {'; '.join(cond_parts)}.",
                    priority="MEDIUM",
                    preconditions=cond_parts,
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description=exp_desc,
                            verification_type="STATE_REACHED",
                            expected_value=exp_val,
                            is_verified_by_evidence=True,
                            is_hypothesis_requiring_confirmation=False,
                            evidence_refs=list(tr.evidence_ids),
                        )
                    ],
                    postconditions=[f"State '{st.state_id}' invariants hold"],
                    cleanup_requirements=["Restore default view/session state"],
                    source_state_ids=list(dict.fromkeys([s for s in (st.state_id, tr.source_state_id, tr.destination_state_id) if s])),
                    source_transition_ids=[tr.transition_id],
                    evidence_ids=list(tr.evidence_ids),
                    confidence=tr.confidence,
                    unresolved_assumptions=[],
                    tags=["state_dependent", "contextual_state"],
                    safety_metadata=safety_meta,
                )
            )

        return tests

    def _generate_regression_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """Generates reusable regression test scenarios from confirmed defects and verified invariants."""
        tests: List[GeneratedContractTestCase] = []
        reg_idx = 0

        # 1. Confirmed defects -> Defect Regression Scenarios
        for df in contract.defect_findings:
            if df.classification != "CONFIRMED_DEFECT":
                continue

            reg_idx += 1
            matched_state = next(
                (s for s in contract.states if s.state_class_id == df.state_class or s.state_id == df.state_class),
                contract.states[0] if contract.states else None,
            )
            st_id = matched_state.state_id if matched_state else (contract.states[0].state_id if contract.states else "initial")

            steps: List[GroundedTestStep] = []
            for s_num, raw_s in enumerate(df.reproduction_steps, start=1):
                act = raw_s.get("action_type", "CLICK_CONTROL")
                sel = raw_s.get("raw_target") or (raw_s.get("target", {}) or {}).get("css") or df.target_control
                if any(kw in f"{act} {sel or ''}".lower() for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS):
                    continue
                steps.append(
                    GroundedTestStep(
                        step_number=s_num,
                        action_type=act,
                        selector=sel,
                        semantic_locator=raw_s.get("target") if isinstance(raw_s.get("target"), dict) else {"role": None, "name": None, "label": None, "css": sel},
                        input_value=raw_s.get("resolved_value"),
                        description=f"Execute reproduction step {s_num}: {act} on '{sel}'",
                        source_state_id=st_id,
                        target_state_id=st_id,
                        evidence_refs=list(df.evidence_ids),
                    )
                )

            if not steps:
                steps.append(
                    GroundedTestStep(
                        step_number=1,
                        action_type="CLICK_CONTROL",
                        selector=df.target_control or "body",
                        semantic_locator={"role": None, "name": None, "label": None, "css": df.target_control},
                        description=f"Trigger defect scenario '{df.title}' on {df.target_control or st_id}",
                        source_state_id=st_id,
                        target_state_id=st_id,
                        evidence_refs=list(df.evidence_ids),
                    )
                )

            safety_meta = self._classify_safety(
                requires_auth=bool(matched_state and matched_state.requires_authentication),
                steps=steps,
                is_mutating=True,
            )

            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-REG-{reg_idx:02d}",
                    title=f"Defect Regression: {df.title}",
                    category=TestCategoryType.REGRESSION,
                    objective=f"Prevent regression of confirmed defect '{df.title}' (Observed actual: {df.actual_behavior}).",
                    priority="HIGH",
                    preconditions=[f"Navigate to state '{st_id}' ({matched_state.url if matched_state else contract.target_url})"],
                    steps=steps,
                    expected_results=[
                        ExpectedResultSpec(
                            description=f"Expected: {df.expected_behavior or 'Clean interaction without error'} (Confirmed defect currently produces: {df.actual_behavior})",
                            verification_type="DEFECT_ABSENT",
                            expected_value=df.expected_behavior or "No error or NaN anomaly",
                            is_verified_by_evidence=True,
                            is_hypothesis_requiring_confirmation=False,
                            evidence_refs=list(df.evidence_ids),
                        )
                    ],
                    postconditions=["Application renders valid output without technical or calculation anomalies"],
                    cleanup_requirements=[],
                    source_state_ids=[st_id] if st_id in state_map else [],
                    source_transition_ids=[],
                    source_finding_ids=[df.finding_id],
                    evidence_ids=list(df.evidence_ids),
                    confidence=df.confidence,
                    unresolved_assumptions=[],
                    tags=["regression", "confirmed_defect", df.oracle_type.lower()],
                    safety_metadata=safety_meta,
                )
            )

        # 2. Always generate at least one Verified Behavior Regression Baseline from high-confidence route/in-page transitions
        if not tests and contract.verified_transitions:
            tr = contract.verified_transitions[0]
            reg_idx += 1
            src_st = state_map.get(tr.source_state_id)
            step = GroundedTestStep(
                step_number=1,
                action_type=tr.action_type,
                selector=tr.grounded_control.selector or tr.grounded_control.target_description,
                semantic_locator={
                    "role": tr.grounded_control.role,
                    "name": tr.grounded_control.accessible_name,
                    "label": None,
                    "css": tr.grounded_control.selector,
                },
                description=f"Regression baseline check: {tr.action_type} on '{tr.grounded_control.target_description}'",
                source_state_id=tr.source_state_id,
                target_state_id=tr.destination_state_id,
                evidence_refs=list(tr.evidence_ids),
            )
            safety_meta = self._classify_safety(
                requires_auth=bool(src_st and src_st.requires_authentication),
                steps=[step],
                is_mutating=False,
            )
            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-REG-{reg_idx:02d}",
                    title=f"Behavioral Regression Baseline: {tr.grounded_control.target_description}",
                    category=TestCategoryType.REGRESSION,
                    objective=f"Ensure verified behavior from '{tr.source_state_id}' to '{tr.destination_state_id}' does not regress.",
                    priority="MEDIUM",
                    preconditions=[f"Start at '{tr.source_state_id}'"],
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description=tr.observed_postconditions[0] if tr.observed_postconditions else f"Reaches {tr.destination_state_id}",
                            verification_type="STATE_REACHED",
                            expected_value=tr.destination_state_id,
                            is_verified_by_evidence=True,
                            is_hypothesis_requiring_confirmation=False,
                            evidence_refs=list(tr.evidence_ids),
                        )
                    ],
                    postconditions=[f"State '{tr.destination_state_id}' verified"],
                    cleanup_requirements=[],
                    source_state_ids=list(dict.fromkeys([s for s in (tr.source_state_id, tr.destination_state_id) if s])),
                    source_transition_ids=[tr.transition_id],
                    evidence_ids=list(tr.evidence_ids),
                    confidence=1.0,
                    unresolved_assumptions=[],
                    tags=["regression", "baseline_invariant"],
                    safety_metadata=safety_meta,
                )
            )

        return tests

    def _generate_exploratory_hypothesis_tests(
        self,
        contract: InvestigationHandoffContract,
        state_map: Dict[str, ContractStateRecord],
    ) -> List[GeneratedContractTestCase]:
        """
        Generates explicitly labeled exploratory/hypothesis tests for unverified suspicions
        or unresolved frontier candidates WITHOUT fabricating expected results.
        """
        tests: List[GeneratedContractTestCase] = []
        hyp_idx = 0

        # 1. Unverified suspicions
        for df in contract.defect_findings:
            if df.classification != "UNVERIFIED_SUSPICION":
                continue
            hyp_idx += 1
            matched_state = next(
                (s for s in contract.states if s.state_class_id == df.state_class or s.state_id == df.state_class),
                contract.states[0] if contract.states else None,
            )
            st_id = matched_state.state_id if matched_state else (contract.states[0].state_id if contract.states else "initial")
            step = GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL",
                selector=df.target_control or "body",
                semantic_locator={"role": None, "name": None, "label": None, "css": df.target_control},
                description=f"Exploratory probe of unverified suspicion: {df.title}",
                source_state_id=st_id,
                target_state_id=None,
                evidence_refs=list(df.evidence_ids),
            )
            safety_meta = self._classify_safety(
                requires_auth=bool(matched_state and matched_state.requires_authentication),
                steps=[step],
                is_mutating=False,
            )
            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-HYP-{hyp_idx:02d}",
                    title=f"Exploratory Hypothesis: Confirm or Falsify '{df.title}'",
                    category=TestCategoryType.EXPLORATORY_HYPOTHESIS,
                    objective=f"Investigate unverified suspicion '{df.title}' to determine actual reproducible behavior.",
                    priority="LOW",
                    preconditions=[f"Navigate to '{st_id}'"],
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description="HYPOTHESIS REQUIRING CONFIRMATION: Behavior was not deterministically verified during investigation; manual or isolated confirmation required.",
                            verification_type="HYPOTHESIS_CONFIRMATION_REQUIRED",
                            expected_value=None,
                            is_verified_by_evidence=False,
                            is_hypothesis_requiring_confirmation=True,
                            evidence_refs=list(df.evidence_ids),
                        )
                    ],
                    postconditions=["Record empirical observation to resolve hypothesis"],
                    cleanup_requirements=[],
                    source_state_ids=[st_id] if st_id in state_map else [],
                    source_transition_ids=[],
                    source_finding_ids=[df.finding_id],
                    evidence_ids=list(df.evidence_ids),
                    confidence=df.confidence,
                    unresolved_assumptions=[df.limitations or "Unverified outcome requires isolated confirmation"],
                    tags=["exploratory", "hypothesis", "unverified"],
                    safety_metadata=safety_meta,
                )
            )

        # 2. Top pending frontier candidate (if any safe candidate remains unexplored)
        for cand in contract.completeness.pending_frontier_candidates[:2]:
            if any(kw in cand.target.lower() for kw in SafetyGovernor.TERMINAL_FORBIDDEN_KEYWORDS):
                continue
            matched_state = next(
                (s for s in contract.states if s.state_class_id == cand.parent_state_id or s.state_id == cand.parent_state_id),
                contract.states[0] if contract.states else None,
            )
            if not matched_state or not contract.evidence_ledger:
                continue

            hyp_idx += 1
            ev_ref = [contract.evidence_ledger[0].evidence_id]
            step = GroundedTestStep(
                step_number=1,
                action_type="CLICK_CONTROL" if cand.candidate_type == "controls" else "NAVIGATE_LINK",
                selector=cand.target,
                semantic_locator={"role": None, "name": None, "label": None, "css": cand.target},
                description=f"Exploratory test of unprobed frontier candidate '{cand.target}' ({cand.unresolved_reason})",
                source_state_id=matched_state.state_id,
                target_state_id=None,
                evidence_refs=ev_ref,
            )
            safety_meta = self._classify_safety(
                requires_auth=matched_state.requires_authentication,
                steps=[step],
                is_mutating=False,
            )
            tests.append(
                GeneratedContractTestCase(
                    test_id=f"TC-HYP-{hyp_idx:02d}",
                    title=f"Exploratory Frontier Hypothesis: {cand.target} on {matched_state.normalized_route}",
                    category=TestCategoryType.EXPLORATORY_HYPOTHESIS,
                    objective=f"Explore pending candidate '{cand.target}' that remained unprobed due to {cand.unresolved_reason}.",
                    priority="LOW",
                    preconditions=[f"Reach state '{matched_state.state_id}' at {matched_state.url}"],
                    steps=[step],
                    expected_results=[
                        ExpectedResultSpec(
                            description="HYPOTHESIS REQUIRING CONFIRMATION: Candidate was not executed before investigation stopping condition; expected outcome must be confirmed empirically.",
                            verification_type="HYPOTHESIS_CONFIRMATION_REQUIRED",
                            expected_value=None,
                            is_verified_by_evidence=False,
                            is_hypothesis_requiring_confirmation=True,
                            evidence_refs=ev_ref,
                        )
                    ],
                    postconditions=["Document observed outcome"],
                    cleanup_requirements=[],
                    source_state_ids=[matched_state.state_id],
                    source_transition_ids=[],
                    source_finding_ids=[],
                    evidence_ids=ev_ref,
                    confidence=0.4,
                    unresolved_assumptions=[f"Candidate unresolved during investigation ({cand.unresolved_reason}); postcondition is unverified"],
                    tags=["exploratory", "hypothesis", "pending_frontier"],
                    safety_metadata=safety_meta,
                )
            )

        return tests

    def _build_coverage_map(
        self,
        test_cases: List[GeneratedContractTestCase],
        contract: InvestigationHandoffContract,
    ) -> TestGenerationCoverageMap:
        states_cov: Dict[str, List[str]] = {}
        trans_cov: Dict[str, List[str]] = {}
        findings_cov: Dict[str, List[str]] = {}
        hyp_flagged: List[str] = []

        for tc in test_cases:
            for sid in tc.source_state_ids:
                states_cov.setdefault(sid, []).append(tc.test_id)
            for tid in tc.source_transition_ids:
                trans_cov.setdefault(tid, []).append(tc.test_id)
            for fid in tc.source_finding_ids:
                findings_cov.setdefault(fid, []).append(tc.test_id)
            if any(er.is_hypothesis_requiring_confirmation for er in tc.expected_results):
                hyp_flagged.append(tc.test_id)

        total_st = len(contract.states)
        cov_st = len(states_cov)
        total_tr = len(contract.verified_transitions)
        cov_tr = len(trans_cov)

        return TestGenerationCoverageMap(
            states_covered=states_cov,
            transitions_covered=trans_cov,
            findings_covered=findings_cov,
            unverified_hypotheses_flagged=hyp_flagged,
            total_states_in_contract=total_st,
            covered_states_count=cov_st,
            state_coverage_ratio=round(cov_st / max(total_st, 1), 3),
            total_transitions_in_contract=total_tr,
            covered_transitions_count=cov_tr,
            transition_coverage_ratio=round(cov_tr / max(total_tr, 1), 3),
        )
