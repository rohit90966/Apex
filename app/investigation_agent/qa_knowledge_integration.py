"""
app/investigation_agent/qa_knowledge_integration.py
====================================================
Wires the structured QA knowledge base (app/knowledge/qa_retrieval.py) into the
investigation workflow's agenda-building phase.

The integration is explicitly one-directional:
  Evidence profile (this run only) → knowledge retrieval → agenda enrichment.

Knowledge entries never carry assumptions from previous runs, other sites, or
hard-coded expectations about specific applications.  Every selected entry is
logged with the predicates that matched so the investigation can be traced back
to the knowledge that guided it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def enrich_agenda_from_knowledge(
    agenda: Any,
    inventory: Any,
    requirements: Optional[List[str]] = None,
    confirmed_leads: int = 0,
    max_entries: int = 20,
    effectiveness: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Retrieve applicable QA knowledge entries for the current investigation and
    inject corresponding investigation questions into the agenda.

    Parameters
    ----------
    agenda       : InvestigationAgenda — mutated in place.
    inventory    : ObservedInventory / any object with .forms, .controls, .states.
    requirements : Optional explicit requirements supplied by the operator.
    confirmed_leads : Count of already-confirmed leads (governs 'has_confirmed_leads').
    max_entries  : Maximum knowledge entries to retrieve.
    effectiveness : Optional {entry_id: float} bonus from historical runs (capped ±0.1).

    Returns a diagnostics dict with retrieved and skipped entries for observability.
    """
    try:
        from app.knowledge.qa_retrieval import (
            QAKnowledgeRetriever,
            evidence_profile,
            get_qa_knowledge_base,
        )
        from app.investigation_agent.agenda import InvestigationQuestion
    except ImportError as exc:
        logger.warning("[KA] QA knowledge integration import failed: %s", exc)
        return {"status": "import_error", "error": str(exc)}

    # 1. Build the evidence profile for this run.
    try:
        profile = evidence_profile(inventory, requirements=requirements, confirmed_leads=confirmed_leads)
    except Exception as exc:
        logger.warning("[KA] Evidence profiling failed: %s", exc)
        return {"status": "profile_error", "error": str(exc)}

    # 2. Retrieve applicable, automated entries.
    try:
        kb = get_qa_knowledge_base()
        retriever = QAKnowledgeRetriever(knowledge_base=kb)
        retrieved = retriever.retrieve(
            profile=profile,
            kinds=("TEST_STRATEGY", "DEFECT_CLASS"),
            effectiveness=effectiveness,
            max_entries=max_entries,
            include_not_automated=False,
        )
    except Exception as exc:
        logger.warning("[KA] Knowledge retrieval failed: %s", exc)
        return {"status": "retrieval_error", "error": str(exc)}

    # 3. Map knowledge entries to investigation questions and add to agenda.
    added = 0
    skipped_dup = 0

    _IMPL_TO_PROBE_FAMILY = {
        "FIELD_BOUNDARIES": "forms",
        "SERVER_SIDE_ENFORCEMENT": "forms",
        "CROSS_FIELD_CONFIRMATION": "forms",
        "ROUTE_REACHABILITY": "nav",
        "OBSERVED_TRANSITIONS": "nav",
        "SORT_ORDER": "lists",
        "FILTER_RESULTS": "lists",
        "SUBMISSION_REFLECTION": "forms",
        "RENDER_ANOMALY_INVARIANT": "invariants",
        "ACCESSIBLE_NAMES": "accessibility",
        "LEAD_REGRESSION": "forms",
        "OBSERVED_VALIDATION": "forms",
        "TEMPLATE_PATTERNS": "forms",
    }

    for r in retrieved:
        entry = r.entry
        impl = entry.automation.implementation or ""
        probe_family = _IMPL_TO_PROBE_FAMILY.get(impl, "controls")
        question_id = f"kaq_{entry.id}"

        # Avoid duplicating questions already on the agenda.
        if hasattr(agenda, "questions") and question_id in (agenda.questions or {}):
            skipped_dup += 1
            continue

        # Build a minimal InvestigationQuestion from the knowledge entry.
        try:
            question = InvestigationQuestion(
                id=question_id,
                hypothesis=entry.description,
                category=entry.category,
                target_element=None,  # concrete target resolved by probe generators
                target_state_class="*",  # applicable to any state with the observed evidence
                probe_family=probe_family,
                priority=r.score,
                # Store the knowledge ID so callers can trace back to the entry.
                metadata={
                    "knowledge_id": entry.id,
                    "knowledge_kind": entry.kind,
                    "matched_predicates": r.matched_predicates,
                    "rationale": r.rationale,
                    "planner_strategy": impl,
                    "false_positive_warnings": entry.false_positive_warnings,
                    "provenance": f"QA_KNOWLEDGE:{entry.id}",
                },
            )
            if hasattr(agenda, "add_question"):
                agenda.add_question(question)
            elif hasattr(agenda, "questions") and isinstance(agenda.questions, dict):
                agenda.questions[question_id] = question
            added += 1
        except Exception as exc:
            logger.debug("[KA] Failed to create question for %s: %s", entry.id, exc)

    excluded_count = len(retriever.last_excluded)
    logger.info(
        "[KA] Knowledge agenda enrichment: %d retrieved, %d added, %d excluded, %d duplicate-skipped",
        len(retrieved), added, excluded_count, skipped_dup,
    )

    return {
        "status": "ok",
        "profile_predicates": {k: v for k, v in profile.predicates.items() if v},
        "retrieved_count": len(retrieved),
        "added_count": added,
        "excluded_count": excluded_count,
        "skipped_duplicate_count": skipped_dup,
        "retrieved_entries": [
            {
                "id": r.entry.id,
                "title": r.entry.title,
                "score": r.score,
                "matched": r.matched_predicates,
                "strategy": r.entry.automation.implementation,
            }
            for r in retrieved[:10]
        ],
        "excluded_entries": [
            {"id": e.id, "reason": e.reason}
            for e in retriever.last_excluded[:10]
        ],
    }


def get_knowledge_summary_for_report(
    inventory: Any,
    requirements: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Returns a concise knowledge-coverage summary suitable for the investigation report,
    without modifying any agenda.  Safe to call after investigation completes.
    """
    try:
        from app.knowledge.qa_retrieval import (
            QAKnowledgeRetriever,
            evidence_profile,
            get_qa_knowledge_base,
        )
        profile = evidence_profile(inventory, requirements=requirements)
        retriever = QAKnowledgeRetriever(get_qa_knowledge_base())
        retrieved = retriever.retrieve(profile=profile, kinds=("TEST_STRATEGY", "DEFECT_CLASS"),
                                       max_entries=50, include_not_automated=True)
        automated = [r for r in retrieved if r.entry.automation.status == "AUTOMATED"]
        not_auto = [r for r in retrieved if r.entry.automation.status == "NOT_AUTOMATED"]
        return {
            "applicable_strategies": len(retrieved),
            "automated_strategies": len(automated),
            "not_automated_strategies": len(not_auto),
            "top_strategies": [{"id": r.entry.id, "title": r.entry.title, "score": r.score}
                                for r in retrieved[:5]],
            "active_predicates": sorted(k for k, v in profile.predicates.items() if v),
        }
    except Exception as exc:
        logger.debug("[KA] Knowledge summary failed: %s", exc)
        return {"status": "error", "error": str(exc)}
