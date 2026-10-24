import logging
import time
import uuid
from typing import Any, List, Optional, Set
from app.config import get_settings
from app.llm.base import BaseLLMProvider
from app.llm.factory import get_llm_provider
from app.test_planner import DeterministicPlanner
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.agents.workflow import build_qa_planning_workflow
from app.schemas import (
    InspectionResult,
    PageClassification,
    TestPlan,
    PlanningMode,
)

logger = logging.getLogger(__name__)


class TestPlanningAgent:
    """
    Central orchestration agent facade.
    Uses the multi-stage LangGraph workflow for agentic test planning with:
    - Requirement analysis
    - Evidence interpretation
    - Risk & coverage analysis
    - Knowledge retrieval (RAG)
    - Deterministic derivation (BVA engine — evidence ledger, test ideas, unknowns)
    - Test strategy planning
    - Test plan generation (Gemini or Ollama)
    - QA Critic with bounded revision loops
    - Deterministic plan validation
    - Graceful fallback to DeterministicPlanner if LLM/workflow fails

    Tracks and surfaces:
    - format_retry_count: bounded 1-step JSON repair attempts (not revision counts)
    - revision_count: semantic QA critic revisions only
    - retrieved_knowledge_count / applied_knowledge_count: QA pattern observability
    - evidence_ledger_count / deterministic_test_count: BVA derivation observability
    """
    __test__ = False

    def __init__(self, provider: Optional[BaseLLMProvider] = None):
        self.settings = get_settings()
        self._provider = provider
        self._workflow = build_qa_planning_workflow(provider=self._provider) if provider else None

    @property
    def provider(self) -> BaseLLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    @property
    def workflow(self):
        if self._workflow is None:
            self._workflow = build_qa_planning_workflow(provider=self.provider)
        return self._workflow

    async def create_plan(
        self,
        inspection: InspectionResult,
        classification: PageClassification,
        requirements: Optional[List[str]] = None,
        description: Optional[str] = None,
        investigation_report: Optional[Any] = None,
    ) -> TestPlan:
        t_start = time.time()
        max_cases = self.settings.verityqa_max_test_cases
        req_id = f"req-{uuid.uuid4().hex[:8]}"

        # Collect feature inventory from investigation report if available
        feat_inv_initial = []
        if investigation_report:
            feat_inv_initial = investigation_report.features or []

        # Bound revisions when using local Ollama to avoid redundant 2+ minute re-generations
        is_ollama = (getattr(self.settings, "llm_provider", "").lower() == "ollama")
        max_revisions = 0 if is_ollama else 1

        p_name = getattr(self.settings, "llm_provider", "groq").lower()
        if p_name == "groq":
            init_mode = PlanningMode.GROQ
        elif p_name == "gemini":
            init_mode = PlanningMode.GEMINI
        else:
            init_mode = PlanningMode.OLLAMA

        # Initialize workflow state
        initial_state: AgentWorkflowState = {
            "request_id": req_id,
            "user_requirements": requirements,
            "website_description": description,
            "inspection_evidence": inspection,
            "page_classification": classification,
            "proposed_test_cases": [],
            "warnings": [],
            "revision_count": 0,
            "max_revisions": max_revisions,
            "current_stage": "INITIALIZED",
            "workflow_status": WorkflowStatus.INITIALIZED,
            "planning_mode": init_mode,
            # Pass investigation data for DeterministicDerivationNode
            "investigation_report": investigation_report,
            "feature_inventory": feat_inv_initial,
        }

        try:
            logger.info("Executing LangGraph agentic QA planning workflow for %s", inspection.target_url)
            # Invoke compiled LangGraph graph asynchronously
            final_state = await self.workflow.ainvoke(initial_state)

            val_result = final_state.get("validation_result")
            critique = final_state.get("critique_result")
            coverage = final_state.get("coverage_summary")
            cases = final_state.get("proposed_test_cases") or []

            # If validation succeeded and produced cases
            if val_result and val_result.is_valid and len(cases) > 0:
                plan_id = f"plan-agentic-{uuid.uuid4().hex[:8]}"
                cov_dict = coverage.model_dump() if coverage else None

                # Compute applied pattern metrics
                retrieved_pats = final_state.get("retrieved_patterns") or []
                retrieved_pat_ids: List[str] = [getattr(p, "pattern_id", "PAT") for p in retrieved_pats]
                retrieved_pat_id_set: Set[str] = set(retrieved_pat_ids)

                applied_pat_ids: List[str] = []
                for tc in cases:
                    for pid in (tc.applied_pattern_ids or []):
                        if pid not in applied_pat_ids:
                            applied_pat_ids.append(pid)

                applied_pat_ids = [pid for pid in applied_pat_ids if pid in retrieved_pat_id_set or pid]
                applied_count = len(applied_pat_ids)

                # Collect format retry count from plan generator state
                format_retries = final_state.get("format_retry_count", 0)

                revision_count = final_state.get("revision_count", 0)
                feat_inv = final_state.get("feature_inventory") or feat_inv_initial
                inv_report = final_state.get("investigation_report") or investigation_report

                # Evidence-grounded metadata
                evidence_ledger = final_state.get("evidence_ledger") or []
                test_ideas = final_state.get("test_ideas") or []
                unknowns = final_state.get("unknowns") or []

                # Timing and LLM stats
                dur = round(time.time() - t_start, 2)
                stage_timings = dict(final_state.get("stage_timings") or {})
                ollama_stats = final_state.get("ollama_stats") or getattr(self.provider, "last_call_stats", None)

                # Compute node vs workflow duration metrics
                sum_node_time = round(sum(v for k, v in stage_timings.items() if isinstance(v, (int, float))), 3)
                stage_timings["_total_workflow_seconds"] = dur
                stage_timings["_sum_node_durations_seconds"] = sum_node_time
                if ollama_stats and "json_repair_duration_seconds" in ollama_stats:
                    stage_timings["json_repair"] = ollama_stats["json_repair_duration_seconds"]

                logger.info(
                    "[VerityQA Workflow Timing Summary] Total: %.2fs | Sum of nodes: %.2fs | Nodes: %s",
                    dur,
                    sum_node_time,
                    stage_timings,
                )

                summary = (
                    f"Agentic LangGraph workflow generated {len(cases)} validated test cases in {dur}s "
                    f"after {revision_count} critic revision(s). "
                    f"Retrieved {len(retrieved_pats)} QA pattern(s), {applied_count} applied. "
                    f"Evidence ledger: {len(evidence_ledger)} constraint record(s), "
                    f"{len(test_ideas)} deterministic test idea(s)."
                )

                # Merge evidence missing context and explicit unknowns
                missing_or_uncertainties: List[str] = []
                if final_state.get("evidence_analysis") and final_state.get("evidence_analysis").missing_context:
                    missing_or_uncertainties.extend(final_state.get("evidence_analysis").missing_context)
                for u in unknowns:
                    u_str = f"Unknown: {u.question} (Resolution: {u.resolution_source})"
                    if u_str not in missing_or_uncertainties:
                        missing_or_uncertainties.append(u_str)

                # Determine planning mode based on provider
                is_groq = "groq" in type(self.provider).__name__.lower()
                is_gemini = getattr(self.provider, "model", "").startswith("gemini") or "gemini" in type(self.provider).__name__.lower()
                if is_groq:
                    chosen_mode = PlanningMode.GROQ
                elif is_gemini:
                    chosen_mode = PlanningMode.GEMINI
                else:
                    chosen_mode = PlanningMode.OLLAMA

                logger.info(
                    "[TEST_COUNT_TRACE] stage=workflow_return count=%d mode=%s plan_id=%s",
                    len(cases),
                    chosen_mode.value,
                    plan_id,
                )

                return TestPlan(
                    plan_id=plan_id,
                    inspection_id=inspection.inspection_id,
                    page_classification=classification,
                    test_cases=cases,
                    planning_mode=chosen_mode,
                    assumptions=[
                        f"Page category: {classification.page_category.value}",
                        "DOM elements grounded in static inspection snapshot",
                    ],
                    missing_requirements_or_uncertainties=missing_or_uncertainties,
                    planning_summary=summary,
                    warnings=final_state.get("warnings") or [],
                    workflow_status=final_state.get("workflow_status", WorkflowStatus.COMPLETED).value,
                    current_stage=final_state.get("current_stage", "COMPLETED"),
                    revision_count=revision_count,
                    format_retry_count=format_retries,
                    review_summary=critique.review_summary if critique else None,
                    coverage_summary=cov_dict,
                    pages_discovered=inv_report.inventory.total_discovered if inv_report else 1,
                    pages_inspected=inv_report.inventory.total_inspected if inv_report else 1,
                    features_detected=[f.model_dump() for f in feat_inv] if feat_inv else None,
                    retrieved_knowledge_count=len(retrieved_pats),
                    retrieved_pattern_ids=retrieved_pat_ids,
                    applied_knowledge_count=applied_count,
                    applied_pattern_ids=applied_pat_ids if applied_pat_ids else None,
                    evidence_ledger_count=len(evidence_ledger),
                    unknowns_count=len(unknowns),
                    deterministic_test_count=len(test_ideas),
                    execution_duration_seconds=dur,
                    stage_timings=stage_timings,
                    ollama_stats=ollama_stats,
                    llm_stats=ollama_stats,
                )
            else:
                raise ValueError("Agentic workflow produced invalid or empty test cases")

        except Exception as e:
            dur = round(time.time() - t_start, 2)
            err_type = type(e).__name__
            err_msg = str(e)
            
            # Attribute provider accurately based on the active provider instance or error message
            active_p = getattr(self, "_provider", None)
            is_groq = False
            if active_p is not None:
                p_cls = type(active_p).__name__.lower()
                p_mod = getattr(active_p, "model", "").lower()
                if "groq" in p_cls or "groq" in p_mod or "llama" in p_mod:
                    provider_name = "Groq"
                    is_groq = True
                elif "gemini" in p_cls or "gemini" in p_mod:
                    provider_name = "Gemini"
                elif "ollama" in p_cls or "ollama" in p_mod:
                    provider_name = "Ollama"
                else:
                    provider_name = "Groq" if getattr(self.settings, "llm_provider", "groq").lower() == "groq" else "Ollama"
                    is_groq = (provider_name == "Groq")
            elif "groq" in err_msg.lower():
                provider_name = "Groq"
                is_groq = True
            elif "ollama" in err_msg.lower():
                provider_name = "Ollama"
            else:
                provider_name = "Groq" if getattr(self.settings, "llm_provider", "groq").lower() == "groq" else "Gemini"
                is_groq = (provider_name == "Groq")

            # Requirement 5: Fail clearly if key is missing or API call fails when Groq is configured
            if is_groq:
                logger.error("[TestPlanningAgent] Groq planning execution failed clearly: %s", e)
                raise e

            if "modelnotfound" in err_type.lower() or "not_found" in err_msg.lower() or "404" in err_msg or "no longer available" in err_msg.lower():
                diagnostic = f"Gemini model unavailable (404 Not Found: {err_msg[:120]})"
            elif "auth" in err_type.lower() or "api_key" in err_msg.lower() or "google_api_key" in err_msg.lower():
                diagnostic = f"Gemini authentication failed (GOOGLE_API_KEY missing or invalid)"
            elif "rate" in err_type.lower() or "quota" in err_msg.lower() or "429" in err_msg:
                diagnostic = f"Gemini API rate limit or quota exceeded ({err_msg[:120]})"
            elif "timed out" in err_msg.lower() or "timeout" in err_type.lower():
                diagnostic = f"{provider_name} request timed out ({err_msg[:120]})"
            elif "connect" in err_msg.lower() or "connecterror" in err_type.lower():
                diagnostic = f"Failed to connect to {provider_name} ({err_msg[:120]})"
            elif "parse" in err_msg.lower() or "json" in err_msg.lower():
                diagnostic = f"{provider_name} returned malformed JSON during plan generation ({err_msg[:120]})"
            elif "truncated" in err_msg.lower():
                diagnostic = f"{provider_name} output was truncated due to token budget limit ({err_msg[:120]})"
            else:
                diagnostic = f"Agentic workflow error [{err_type}]: {err_msg[:120]}"

            fallback_warning = (
                f"{provider_name} planning unavailable or agentic workflow failed: {diagnostic} "
                f"(elapsed: {dur}s). Engaged deterministic rule-based fallback planner."
            )
            logger.warning("app.test_planning_agent: %s", fallback_warning)

            fallback_plan = DeterministicPlanner.generate_plan(
                inspection=inspection,
                classification=classification,
                requirements=requirements,
                description=description,
                max_cases=max_cases,
                planning_mode=PlanningMode.RULE_BASED_FALLBACK,
                warning_reason=fallback_warning,
            )
            fallback_plan.workflow_status = WorkflowStatus.FALLBACK.value
            fallback_plan.current_stage = "FALLBACK"
            fallback_plan.revision_count = 0
            fallback_plan.format_retry_count = 0
            fallback_plan.execution_duration_seconds = dur
            logger.info(
                "[TEST_COUNT_TRACE] stage=fallback_plan count=%d plan_id=%s",
                len(fallback_plan.test_cases),
                fallback_plan.plan_id,
            )
            return fallback_plan

