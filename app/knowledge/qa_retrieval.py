"""
Structured QA knowledge: loading, validation, evidence profiling and retrieval.

The knowledge base (app/knowledge/qa/*.json) describes defect classes, test-design strategies, oracles and
application patterns. Retrieval is deterministic: each entry declares applicability predicates over an
*evidence profile* computed from the current investigation (never from previous runs or other sites).
Entries are ranked by specificity, risk, automation status and (capped) historical effectiveness, and
every selection records which predicates matched so tests can be traced back to the knowledge used.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.knowledge.schema import QA_KNOWLEDGE_KINDS, QAKnowledgeEntry

logger = logging.getLogger(__name__)

QA_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "qa")

SORT_WORDS = {"sort", "sorting", "order", "orderby", "sortby"}
FILTER_WORDS = {"filter", "status", "priority", "category", "type", "tag", "state"}
CONFIRM_WORDS = {"confirm", "confirmation", "repeat", "retype", "verify", "again"}

# Every predicate the knowledge base may reference, with what it means.
PREDICATES: Dict[str, str] = {
    "has_forms": "at least one form was observed",
    "has_post_form": "a POST form was observed",
    "has_get_form": "a GET form was observed",
    "has_login_form": "a form with exactly one password field and a username/email field",
    "has_required_field": "a non-checkbox field declares required",
    "has_required_checkbox": "a checkbox/radio declares required",
    "has_length_constraint": "a text field declares minlength or maxlength",
    "has_numeric_range": "a number/range field declares min or max",
    "has_date_range": "a date field declares min or max",
    "has_pattern_constraint": "a field declares a pattern",
    "has_email_field": "an email-type field",
    "has_confirmation_pair": "two fields where one is semantically the confirmation of the other",
    "has_select_field": "a select with at least two options",
    "has_sort_control": "a select whose name/label denotes sorting",
    "has_filter_control": "a select in a GET form or named as a filter",
    "has_search_field": "a search-type or q/query/search field",
    "has_create_form": "a POST form whose action/submit denotes create/new/add/save",
    "has_record_command": "a field-less POST form whose action targets one record (an instance path), i.e. a per-record command",
    "has_collection": "repeated record controls (a list/table of records)",
    "has_links": "same-origin links",
    "has_multiple_routes": "two or more routes reachable by navigation",
    "has_auth_boundary": "a login boundary was observed",
    "has_authenticated_area": "states observed inside an authenticated session",
    "has_requirements": "explicit requirements were supplied",
    "has_field_requirements": "at least one requirement maps to an observed field rule",
    "has_observed_validation_message": "a validation message was observed",
    "has_confirmed_leads": "the investigation confirmed (reproduced) at least one lead",
    "has_interactive_controls": "interactive controls were observed",
    "has_transitions": "observed state transitions",
    "has_cart": "cart routes or controls",
    "has_checkout": "checkout routes or controls",
    "has_multistep_route": "routes naming steps/wizards",
    "has_file_input": "a file input",
    "has_pagination": "page-number links",
}

# Planner strategy keys an entry may name as its implementation (plus oracle/assertion names).
PLANNER_IMPLEMENTATIONS = {
    "ROUTE_REACHABILITY", "OBSERVED_TRANSITIONS", "FIELD_BOUNDARIES", "SERVER_SIDE_ENFORCEMENT",
    "CROSS_FIELD_CONFIRMATION", "SORT_ORDER", "FILTER_RESULTS", "SUBMISSION_REFLECTION", "RENDER_ANOMALY_INVARIANT",
    "ACCESSIBLE_NAMES", "LEAD_REGRESSION", "OBSERVED_VALIDATION", "TEMPLATE_PATTERNS", "RECORD_COMMAND_EFFECT",
}

_RISK_WEIGHT = {"HIGH": 0.3, "MEDIUM": 0.2, "LOW": 0.1}
_AUTOMATION_WEIGHT = {"AUTOMATED": 0.15, "PARTIAL": 0.05, "NOT_AUTOMATED": 0.0}


def _words(*texts: str) -> Set[str]:
    out: Set[str] = set()
    for t in texts:
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t or "")
        out.update(w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w)
    return out


# Commands that need not change the record they are issued on: read-only ones, and ones whose effect is
# elsewhere (a message sent, a copy created, an external sync). A RECORD_CHANGED check would misreport them.
READ_ONLY_COMMAND_WORDS = {"view", "details", "detail", "open", "show", "download", "export", "print", "copy", "share",
                           "preview", "resend", "send", "notify", "remind", "email", "refresh", "sync", "duplicate",
                           "clone", "invite", "test", "check", "verify", "validate", "retry"}


def is_record_command_form(form: Any) -> bool:
    """A POST form without user-editable fields whose action path names one record (e.g. /items/7/archive)."""
    from app.intelligence.target_context import is_instance_route, route_of
    editable = [f for f in getattr(form, "fields", []) or [] if getattr(f, "field_type", "") not in ("hidden", "submit", "button")]
    action = getattr(form, "action", "") or ""
    if getattr(form, "method", "") != "POST" or editable or not action or not getattr(form, "submit_selector", ""):
        return False
    words = _words(action, getattr(form, "submit_selector", ""))
    return is_instance_route(route_of(action)) and not (words & READ_ONLY_COMMAND_WORDS)


def find_confirmation_pairs(fields: Iterable[Any]) -> List[Tuple[Any, Any]]:
    """
    (source, confirmation) pairs inferred from field semantics: the confirmation field's name/id/label
    contains a confirm-word and the remaining terms equal another field's terms (e.g. password /
    confirm_password, email / email_confirmation).
    """
    fields = [f for f in fields if getattr(f, "field_type", "") not in ("checkbox", "radio", "submit", "hidden")]
    pairs = []
    for conf in fields:
        words = _words(conf.name, conf.field_id, conf.label)
        if not words & CONFIRM_WORDS:
            continue
        base = {w for w in words - CONFIRM_WORDS if len(w) >= 3}
        if not base:
            continue
        for src in fields:
            if src is conf or _words(src.name, src.field_id, src.label) & CONFIRM_WORDS:
                continue
            if base & _words(src.name, src.field_id, src.label) and src.field_type == conf.field_type:
                pairs.append((src, conf))
                break
    return pairs


class EvidenceProfile(BaseModel):
    predicates: Dict[str, bool] = Field(default_factory=dict)
    evidence: Dict[str, List[str]] = Field(default_factory=dict)

    def holds(self, name: str) -> bool:
        return bool(self.predicates.get(name))


def evidence_profile(inventory: Any, requirements: Optional[List[str]] = None, confirmed_leads: int = 0) -> EvidenceProfile:
    """Deterministic profile of what the current investigation observed (single origin, single run)."""
    ev: Dict[str, List[str]] = {k: [] for k in PREDICATES}
    forms = list(getattr(inventory, "forms", []) or [])
    controls = list(getattr(inventory, "controls", []) or [])
    states = list(getattr(inventory, "states", []) or [])

    def add(name: str, why: str) -> None:
        if len(ev[name]) < 5:
            ev[name].append(why)

    for form in forms:
        add("has_forms", f"{form.form_selector} on {form.route}")
        add("has_post_form" if form.method == "POST" else "has_get_form", f"{form.method} {form.form_selector} on {form.route}")
        if form.is_login_form:
            add("has_login_form", f"login form on {form.route}")
        if form.method == "POST" and _words(form.action, form.form_id, form.submit_selector) & {"create", "new", "add", "save"} and not form.is_login_form:
            add("has_create_form", f"{form.action or form.route}")
        for f in form.fields:
            tag = f"{f.selector} on {form.route}"
            if f.required and f.field_type in ("checkbox", "radio"):
                add("has_required_checkbox", tag)
            elif f.required:
                add("has_required_field", tag)
            if (f.minlength or f.maxlength) and f.field_type not in ("checkbox", "radio", "select", "select-one"):
                add("has_length_constraint", tag)
            if f.field_type in ("number", "range") and (f.min or f.max):
                add("has_numeric_range", tag)
            if f.field_type == "date" and (f.min or f.max):
                add("has_date_range", tag)
            if f.pattern:
                add("has_pattern_constraint", tag)
            if f.field_type == "email":
                add("has_email_field", tag)
            if f.field_type == "file":
                add("has_file_input", tag)
            if f.field_type == "search" or f.name.lower() in ("q", "query", "search"):
                add("has_search_field", tag)
            if f.field_type.startswith("select") and len(f.options) >= 2:
                add("has_select_field", tag)
                words = _words(f.name, f.field_id, f.label)
                if words & SORT_WORDS:
                    add("has_sort_control", tag)
                elif form.method == "GET" or words & FILTER_WORDS:
                    add("has_filter_control", tag)
        if is_record_command_form(form):
            add("has_record_command", f"POST {form.action} ({form.submit_selector}) on {form.route}")
        for src, conf in find_confirmation_pairs(form.fields):
            add("has_confirmation_pair", f"{conf.selector} confirms {src.selector} on {form.route}")

    names = Counter((c.route, (c.name or "").strip().lower()) for c in controls if (c.name or "").strip())
    for (route, name), n in names.items():
        if n >= 3:
            add("has_collection", f"{n}x '{name}' on {route}")
    for c in controls:
        words = _words(c.selector, c.name)
        add("has_interactive_controls", f"{c.selector} on {c.route}")
        if c.tag == "select" and len(c.options) >= 2:
            add("has_select_field", f"{c.selector} on {c.route}")
            if words & SORT_WORDS:
                add("has_sort_control", f"{c.selector} on {c.route}")
            elif words & FILTER_WORDS:
                add("has_filter_control", f"{c.selector} on {c.route}")
        if c.href and c.href.startswith("/"):
            add("has_links", f"{c.href} from {c.route}")
        if c.href and re.search(r"[?&]page=\d+", c.href):
            add("has_pagination", c.href)
        if "cart" in words:
            add("has_cart", f"{c.selector} on {c.route}")
        if "checkout" in words:
            add("has_checkout", f"{c.selector} on {c.route}")
    for s in states:
        rw = _words(s.route)
        if "cart" in rw:
            add("has_cart", s.route)
        if "checkout" in rw:
            add("has_checkout", s.route)
        if rw & {"step", "wizard"}:
            add("has_multistep_route", s.route)
        if s.validation_message:
            add("has_observed_validation_message", f"'{s.validation_message[:60]}' on {s.route}")
        if s.authenticated:
            add("has_authenticated_area", s.route)
    navigable = inventory.navigable_routes() if hasattr(inventory, "navigable_routes") else []
    if len(navigable) >= 2:
        add("has_multiple_routes", ", ".join(navigable[:5]))
    if getattr(getattr(inventory, "auth", None), "boundary_observed", False):
        add("has_auth_boundary", "login boundary observed")
    for t in list(getattr(inventory, "transitions", []) or [])[:5]:
        add("has_transitions", f"{t.action_type[:40]} {t.target[:40]}")
    reqs = [r for r in (requirements or []) if r]
    if reqs:
        add("has_requirements", f"{len(reqs)} supplied")
        from app.test_planning.field_constraints import align_requirements
        alignment = align_requirements(reqs, inventory)
        for row in alignment["requirements"]:
            if row["status"] == "MATCHED":
                add("has_field_requirements", row["requirement"][:80])
    if confirmed_leads:
        add("has_confirmed_leads", f"{confirmed_leads} confirmed lead(s)")
    return EvidenceProfile(predicates={k: bool(v) for k, v in ev.items()}, evidence={k: v for k, v in ev.items() if v})


# ── Knowledge base ────────────────────────────────────────────────────────────

class KnowledgeValidationError(ValueError):
    pass


class QAKnowledgeBase:
    def __init__(self, directory: Optional[str] = None):
        self.directory = directory or QA_KNOWLEDGE_DIR
        self.entries: Dict[str, QAKnowledgeEntry] = {}
        self.load()

    def load(self) -> None:
        self.entries.clear()
        for name in sorted(os.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.directory, name), "r", encoding="utf-8") as fh:
                for raw in json.load(fh):
                    entry = QAKnowledgeEntry.model_validate(raw)
                    if entry.id in self.entries:
                        raise KnowledgeValidationError(f"Duplicate knowledge id {entry.id} in {name}")
                    self.entries[entry.id] = entry
        problems = self.validate()
        if problems:
            raise KnowledgeValidationError("; ".join(problems))
        logger.info("Loaded %d QA knowledge entries from %s", len(self.entries), self.directory)

    def validate(self) -> List[str]:
        problems = []
        for e in self.entries.values():
            if e.kind not in QA_KNOWLEDGE_KINDS:
                problems.append(f"{e.id}: unknown kind {e.kind}")
            for p in e.applicability.all + e.applicability.any + e.applicability.none:
                if p not in PREDICATES:
                    problems.append(f"{e.id}: unknown predicate {p}")
            for rid in e.related_ids:
                if rid not in self.entries:
                    problems.append(f"{e.id}: related id {rid} does not exist")
            impl = e.automation.implementation
            if e.kind in ("DEFECT_CLASS", "TEST_STRATEGY") and e.automation.status != "NOT_AUTOMATED" and impl \
                    and impl not in PLANNER_IMPLEMENTATIONS:
                problems.append(f"{e.id}: implementation {impl} is not a planner strategy")
        return problems

    def get(self, entry_id: str) -> Optional[QAKnowledgeEntry]:
        return self.entries.get(entry_id)

    def by_kind(self, kind: str) -> List[QAKnowledgeEntry]:
        return [e for e in self.entries.values() if e.kind == kind]


_KB: Optional[QAKnowledgeBase] = None


def get_qa_knowledge_base() -> QAKnowledgeBase:
    global _KB
    if _KB is None:
        _KB = QAKnowledgeBase()
    return _KB


# ── Retrieval ─────────────────────────────────────────────────────────────────

class RetrievedQAEntry(BaseModel):
    entry: QAKnowledgeEntry
    score: float
    matched_predicates: List[str] = Field(default_factory=list)
    rationale: str = ""


class ExcludedQAEntry(BaseModel):
    id: str
    reason: str


class QAKnowledgeRetriever:
    """Selects knowledge entries applicable to an evidence profile and ranks them."""

    def __init__(self, knowledge_base: Optional[QAKnowledgeBase] = None):
        self.kb = knowledge_base or get_qa_knowledge_base()
        self.last_excluded: List[ExcludedQAEntry] = []

    @staticmethod
    def applies(entry: QAKnowledgeEntry, profile: EvidenceProfile) -> Tuple[bool, List[str], str]:
        a = entry.applicability
        contradicting = [p for p in a.none if profile.holds(p)]
        if contradicting:
            return False, [], f"contradicting evidence: {contradicting}"
        missing = [p for p in a.all if not profile.holds(p)]
        if missing:
            return False, [], f"missing required evidence: {missing}"
        any_hits = [p for p in a.any if profile.holds(p)]
        if a.any and not any_hits:
            return False, [], f"none of {a.any} observed"
        return True, list(a.all) + any_hits, ""

    def retrieve(self, profile: EvidenceProfile, kinds: Iterable[str] = ("TEST_STRATEGY",),
                 effectiveness: Optional[Dict[str, float]] = None, max_entries: int = 40,
                 include_not_automated: bool = False) -> List[RetrievedQAEntry]:
        kinds = set(kinds)
        selected: List[RetrievedQAEntry] = []
        self.last_excluded = []
        for entry in self.kb.entries.values():
            if entry.kind not in kinds:
                continue
            ok, matched, reason = self.applies(entry, profile)
            if not ok:
                self.last_excluded.append(ExcludedQAEntry(id=entry.id, reason=reason))
                continue
            if entry.automation.status == "NOT_AUTOMATED" and not include_not_automated:
                self.last_excluded.append(ExcludedQAEntry(id=entry.id, reason="applicable but not automated"))
                continue
            specificity = min(1.0, len(matched) / 3.0)
            bonus = max(-0.1, min(0.1, (effectiveness or {}).get(entry.id, 0.0)))  # history never gates
            score = round(0.5 * specificity + _RISK_WEIGHT.get(entry.risk, 0.2)
                          + _AUTOMATION_WEIGHT.get(entry.automation.status, 0.0) + bonus, 3)
            selected.append(RetrievedQAEntry(
                entry=entry, score=score, matched_predicates=matched,
                rationale=f"evidence: {', '.join(matched) or 'none required'}" + (f"; history {bonus:+.2f}" if bonus else ""),
            ))
        selected.sort(key=lambda r: (-r.score, r.entry.id))
        return selected[:max_entries]
