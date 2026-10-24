import logging
from typing import List
from app.schemas import Priority, ExpectedBehaviorSource, PageCategory
from app.agents.models import TestStrategy, StrategyScenario
from app.agents.state import AgentWorkflowState, WorkflowStatus

logger = logging.getLogger(__name__)


class StrategyPlannerNode:
    """
    Node D: Test Strategy Planner
    Chooses relevant test categories, defines scenario objectives, and maps requirements
    and risks to candidate test cases before test plan generation.
    """

    @staticmethod
    def run(state: AgentWorkflowState) -> dict:
        req_analysis = state.get("requirement_analysis")
        risk_analysis = state.get("risk_analysis")
        evidence = state.get("evidence_analysis")
        classification = state.get("page_classification")
        critique = state.get("critique_result")

        category = classification.page_category if classification else PageCategory.GENERAL
        scenarios: List[StrategyScenario] = []
        focus_areas: List[str] = []
        untestable: List[str] = []

        # 1. Map explicit requirements into high-priority scenarios
        if req_analysis and req_analysis.explicit_requirements:
            focus_areas.append("Explicit User Requirements")
            for req in req_analysis.explicit_requirements:
                scenarios.append(
                    StrategyScenario(
                        scenario_id=f"STRAT-{req.id}",
                        title=f"Verify requirement: {req.text[:40]}",
                        objective=f"Ensure compliance with user requirement: '{req.text}'",
                        category=category.value,
                        priority=Priority.HIGH,
                        provenance=ExpectedBehaviorSource.REQUIREMENT,
                        supporting_evidence=["User testing requirements"],
                        mapped_requirement_ids=[req.id],
                    )
                )

        # 2. Map identified risks into scenarios
        if risk_analysis:
            for risk in risk_analysis.risks:
                if any(s.title.lower() in [m.lower() for m in risk.mitigating_scenarios] for s in scenarios):
                    continue
                scenarios.append(
                    StrategyScenario(
                        scenario_id=f"STRAT-{risk.risk_id}",
                        title=risk.mitigating_scenarios[0] if risk.mitigating_scenarios else risk.title,
                        objective=risk.description,
                        category=risk.category,
                        priority=risk.severity,
                        provenance=ExpectedBehaviorSource.INVARIANT if risk.category in ("security", "accessibility") else ExpectedBehaviorSource.INFERRED,
                        supporting_evidence=risk.target_elements,
                        mapped_requirement_ids=[],
                    )
                )
                if risk.category not in focus_areas:
                    focus_areas.append(risk.category)

        # 3. Step 3: Map retrieved QA knowledge patterns into targeted scenarios (as optional suggestions)
        retrieved_patterns = state.get("retrieved_patterns") or []
        for pat in retrieved_patterns:
            pat_title = getattr(pat, "title", "QA Pattern")
            if any(s.title.lower() == pat_title.lower() for s in scenarios):
                continue
            scenarios.append(
                StrategyScenario(
                    scenario_id=f"STRAT-{getattr(pat, 'pattern_id', 'PAT')}",
                    title=pat_title,
                    objective=getattr(pat, "scenario_guidance", getattr(pat, "description", "")),
                    category=getattr(pat, "testing_technique", "exploratory_heuristic"),
                    priority=Priority.LOW,
                    provenance=ExpectedBehaviorSource.INFERRED,
                    supporting_evidence=getattr(pat, "required_evidence", []),
                    mapped_requirement_ids=[],
                )
            )
            if "Knowledge Pattern (Optional Heuristic)" not in focus_areas:
                focus_areas.append("Knowledge Pattern (Optional Heuristic)")

        # 4. Step 4: Map deterministic test ideas into strategy scenarios
        test_ideas = state.get("test_ideas") or []
        for idea in test_ideas:
            if any(s.title.lower() == idea.name.lower() or idea.name.lower() in s.title.lower() for s in scenarios):
                continue
            cat_name = "security" if "mask" in idea.rule_id.lower() else ("accessibility" if "a11y" in idea.rule_id.lower() else "functional")
            scenarios.append(
                StrategyScenario(
                    scenario_id=f"STRAT-{idea.idea_id}",
                    title=idea.name,
                    objective=idea.objective,
                    category=cat_name,
                    priority=Priority.HIGH if idea.priority == "HIGH" else Priority.MEDIUM,
                    provenance=ExpectedBehaviorSource.OBSERVED_INVARIANT if "INVAR" in idea.rule_id else ExpectedBehaviorSource.INVARIANT,
                    supporting_evidence=idea.evidence_ids,
                    mapped_requirement_ids=[],
                )
            )
            if "Deterministic Invariant" not in focus_areas:
                focus_areas.append("Deterministic Invariant")

        # Incorporate critique feedback if revising
        if critique and critique.issues_found:
            logger.info("Strategy planner incorporating critique recommendations: %s", critique.revision_recommendations)

        strategy = TestStrategy(
            focus_areas=focus_areas,
            scenarios=scenarios,
            untestable_requirements=untestable,
            rationale=f"Strategy aligns {len(scenarios)} target scenarios with observed risks, {len(retrieved_patterns)} retrieved QA patterns, and {category.value} page patterns.",
        )

        return {
            "test_strategy": strategy,
            "current_stage": "STRATEGY_PLANNING",
            "workflow_status": WorkflowStatus.ANALYZING,
        }
