import logging
from typing import Optional
from langgraph.graph import StateGraph, START, END
from app.agents.state import AgentWorkflowState, WorkflowStatus
from app.agents.requirement_analyst import RequirementAnalystNode
from app.agents.evidence_interpreter import EvidenceInterpreterNode
from app.agents.risk_analyst import RiskAnalystNode
from app.agents.strategy_planner import StrategyPlannerNode
from app.agents.plan_generator import PlanGeneratorNode
from app.agents.qa_critic import QACriticNode
from app.agents.plan_validator import PlanValidatorNode
from app.agents.deterministic_derivation import DeterministicDerivationNode
from app.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


def should_revise(state: AgentWorkflowState) -> str:
    """
    Conditional routing edge after QA Critic:
    - Only re-invoke expensive LLM generation if critical explicit requirements are missing.
    - Cosmetic phrasing, element assertions, or duplicates are handled deterministically by PlanValidatorNode.
    - Otherwise -> route to plan_validator.
    """
    critique = state.get("critique_result")
    rev_count = state.get("revision_count", 0)
    max_revs = state.get("max_revisions", 2)

    if critique and rev_count < max_revs:
        # Only revise if critical explicit user requirements were missed
        if "MISSING_REQUIREMENT" in critique.issue_categories:
            logger.info(
                "Critic triggered revision attempt %d of %d for missing requirements: %s",
                rev_count + 1,
                max_revs,
                getattr(critique, "missing_requirement_ids", []),
            )
            state["revision_count"] = rev_count + 1
            return "strategy_planner"

    return "plan_validator"


import inspect
import time

def _wrap_timed_node(node_name: str, fn):
    """Wraps a sync or async workflow node function with execution timing and error attribution."""
    is_async = inspect.iscoroutinefunction(fn)

    if is_async:
        async def async_wrapper(state: AgentWorkflowState):
            t0 = time.time()
            try:
                res = await fn(state)
            except Exception as exc:
                logger.error("[Workflow Node '%s'] failed after %.2fs: %s", node_name, time.time() - t0, exc)
                state["error_code"] = f"{node_name}: {str(exc)}"
                raise
            dur = round(time.time() - t0, 3)
            logger.info("[Workflow Node '%s'] completed in %.3fs", node_name, dur)
            # Accumulate stage timings
            timings = dict(state.get("stage_timings") or {})
            timings[node_name] = dur
            if isinstance(res, dict):
                res["stage_timings"] = timings
            return res
        return async_wrapper
    else:
        def sync_wrapper(state: AgentWorkflowState):
            t0 = time.time()
            try:
                res = fn(state)
            except Exception as exc:
                logger.error("[Workflow Node '%s'] failed after %.2fs: %s", node_name, time.time() - t0, exc)
                state["error_code"] = f"{node_name}: {str(exc)}"
                raise
            dur = round(time.time() - t0, 3)
            logger.info("[Workflow Node '%s'] completed in %.3fs", node_name, dur)
            timings = dict(state.get("stage_timings") or {})
            timings[node_name] = dur
            if isinstance(res, dict):
                res["stage_timings"] = timings
            return res
        return sync_wrapper


def build_qa_planning_workflow(provider: Optional[BaseLLMProvider] = None) -> StateGraph:
    """
    Constructs and compiles the LangGraph QA Planning Workflow with node timing instrumentation:
    START
    -> requirement_analyst
    -> evidence_interpreter
    -> risk_analyst
    -> knowledge_retrieval
    -> deterministic_derivation
    -> strategy_planner
    -> plan_generator
    -> qa_critic
    -> [conditional router: revise or validate]
    -> plan_validator
    -> END
    """
    workflow = StateGraph(AgentWorkflowState)

    # 1. Instantiate Nodes
    req_analyst = RequirementAnalystNode()
    evidence_interp = EvidenceInterpreterNode()
    risk_analyst = RiskAnalystNode()
    from app.agents.knowledge_retrieval import KnowledgeRetrievalNode
    know_retrieval = KnowledgeRetrievalNode()
    det_derivation = DeterministicDerivationNode()
    strat_planner = StrategyPlannerNode()
    plan_gen = PlanGeneratorNode(provider=provider)
    qa_critic = QACriticNode()
    plan_val = PlanValidatorNode()

    # 2. Add Timed Nodes to Graph
    workflow.add_node("requirement_analyst", _wrap_timed_node("requirement_analyst", req_analyst.run))
    workflow.add_node("evidence_interpreter", _wrap_timed_node("evidence_interpreter", evidence_interp.run))
    workflow.add_node("risk_analyst", _wrap_timed_node("risk_analyst", risk_analyst.run))
    workflow.add_node("knowledge_retrieval", _wrap_timed_node("knowledge_retrieval", know_retrieval.run))
    workflow.add_node("deterministic_derivation", _wrap_timed_node("deterministic_derivation", det_derivation.run))
    workflow.add_node("strategy_planner", _wrap_timed_node("strategy_planner", strat_planner.run))
    workflow.add_node("plan_generator", _wrap_timed_node("plan_generator", plan_gen.run))
    workflow.add_node("qa_critic", _wrap_timed_node("qa_critic", qa_critic.run))
    workflow.add_node("plan_validator", _wrap_timed_node("plan_validator", plan_val.run))

    # 3. Add Edges
    workflow.add_edge(START, "requirement_analyst")
    workflow.add_edge("requirement_analyst", "evidence_interpreter")
    workflow.add_edge("evidence_interpreter", "risk_analyst")
    workflow.add_edge("risk_analyst", "knowledge_retrieval")
    workflow.add_edge("knowledge_retrieval", "deterministic_derivation")
    workflow.add_edge("deterministic_derivation", "strategy_planner")
    workflow.add_edge("strategy_planner", "plan_generator")
    workflow.add_edge("plan_generator", "qa_critic")

    # 4. Conditional Edge after QA Critic
    workflow.add_conditional_edges(
        "qa_critic",
        should_revise,
        {
            "strategy_planner": "strategy_planner",
            "plan_generator": "plan_generator",
            "plan_validator": "plan_validator",
        },
    )

    # 5. Terminal Edge
    workflow.add_edge("plan_validator", END)

    return workflow.compile()

