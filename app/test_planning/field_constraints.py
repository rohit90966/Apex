"""
Field-level constraint extraction and boundary-value test design.

Constraints come from three sources, recorded on every generated case:
  * HTML_CONSTRAINT   - attributes observed on the live field (required, maxlength, minlength,
                        min/max, pattern, type=email/url/number/date)
  * REQUIREMENT       - an explicit requirement supplied with the investigation request
  * OBSERVED_BEHAVIOR - a validation message the investigation actually saw

A field whose rule cannot be established from those sources is reported as an UNKNOWN
constraint together with the evidence that would resolve it; no pass/fail oracle is invented
for it (e.g. type="tel" does not imply any digit count).

Partitions follow equivalence partitioning + boundary value analysis: for a limit N the cases
are N-1, N, N+1 (and N+2 / class representatives for "exactly N digits" rules).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.intelligence.target_context import ObservedField, ObservedForm
from app.test_data import UNIQUE_TOKEN, has_identity_semantics

SOURCE_HTML = "HTML_CONSTRAINT"
SOURCE_REQUIREMENT = "REQUIREMENT"
SOURCE_OBSERVED = "OBSERVED_BEHAVIOR"

EXPECT_ACCEPT = "ACCEPT"
EXPECT_REJECT = "REJECT"

_TEXT_TYPES = {"text", "email", "search", "tel", "url", "password", "textarea"}
_GENERIC_WORDS = {"the", "a", "an", "field", "input", "value", "must", "should", "be", "is", "are", "and",
                  "or", "of", "to", "only", "exactly", "at", "least", "most", "between", "characters",
                  "character", "chars", "digits", "digit", "long", "length", "required", "cannot", "empty",
                  "blank", "not", "no", "more", "than", "less", "maximum", "minimum", "max", "min", "up",
                  "contain", "have", "has", "with", "number", "numbers", "user", "users", "valid"}


class FieldConstraint(BaseModel):
    """A single rule about one field, with where the rule came from."""
    field_selector: str
    field_label: str = ""
    kind: str  # REQUIRED | MAX_LENGTH | MIN_LENGTH | EXACT_DIGITS | EXACT_LENGTH | MIN_VALUE | MAX_VALUE | PATTERN | TYPE_EMAIL
    value: Optional[str] = None
    source: str = SOURCE_HTML
    source_detail: str = ""


class BoundaryCase(BaseModel):
    field_selector: str
    field_label: str = ""
    constraint: FieldConstraint
    boundary: str            # e.g. "maxlength+1", "9 digits"
    input_class: str         # VALID | EMPTY | WHITESPACE | TOO_LONG | TOO_SHORT | NON_NUMERIC | ...
    input_value: str
    expected: str            # ACCEPT | REJECT
    oracle: str              # FIELD_VALIDITY | VALUE_LENGTH | SUBMISSION_FEEDBACK
    oracle_expected: str     # e.g. "valueMissing", "==20", "rejected"
    requires_submit: bool = True


class UnknownConstraint(BaseModel):
    field_selector: str
    field_label: str = ""
    route: str = ""
    reason: str
    evidence_needed: str


class FormBoundaryPlan(BaseModel):
    form: ObservedForm
    cases: List[BoundaryCase] = Field(default_factory=list)
    unknowns: List[UnknownConstraint] = Field(default_factory=list)
    unmapped_requirements: List[str] = Field(default_factory=list)


# ── Value generation ─────────────────────────────────────────────────────────

def text_of_length(n: int, alphabet: str = "QaBoundary") -> str:
    if n <= 0:
        return ""
    return (alphabet * (n // len(alphabet) + 1))[:n]


def digits_of_length(n: int) -> str:
    return ("1234567890" * (n // 10 + 1))[:n]


def _num(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fmt_num(value: float, step: Optional[str]) -> str:
    if float(value).is_integer() and not (step and "." in step):
        return str(int(value))
    return str(value)


def _date(value: Optional[str]) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def valid_filler(field: ObservedField) -> Optional[str]:
    """A value that satisfies every observed constraint of the field, or None when none can be derived."""
    ftype = field.field_type
    if ftype in ("select", "select-one"):
        return next((o for o in field.options if o.strip()), None)
    if ftype in ("checkbox", "radio", "hidden", "file", "submit", "button"):
        return None
    if ftype == "number" or ftype == "range":
        lo, hi = _num(field.min), _num(field.max)
        if lo is not None:
            return _fmt_num(lo, field.step)
        if hi is not None:
            return _fmt_num(min(hi, 1.0), field.step)
        return "1"
    if ftype == "date":
        lo, hi = _date(field.min), _date(field.max)
        candidate = lo or (hi if hi else _dt.date(2030, 1, 15))
        return candidate.isoformat()
    if field.pattern:
        return _pattern_candidate(field.pattern, want_match=True)
    if ftype == "email":
        base = f"qa.{UNIQUE_TOKEN}@example.com"  # unique per execution: emails are usually account keys
    elif ftype == "url":
        base = "https://example.com/qa"
    elif ftype == "tel":
        base = "5550100"
    elif ftype == "password":
        base = "QaPassw0rd!x"
    elif has_identity_semantics(field):
        base = f"qa{UNIQUE_TOKEN}"
    else:
        base = "QA Sample"
    lo = field.minlength or 0
    hi = field.maxlength
    if hi is not None and len(base) > hi:
        base = text_of_length(hi) if ftype not in ("email", "url") else None
        if base is None:
            return None
    if len(base) < lo:
        base = base + text_of_length(lo - len(base))
        if hi is not None and len(base) > hi:
            return None
    return base


_PATTERN_PROBES = ["a", "A", "1", "12", "123", "1234", "12345", "123456", "1234567890", "abc", "ABC",
                   "abc123", "a1b2c3", "test", "Test123", "!", "@@", "a-b", "a b", "00000", "ZZ99",
                   "qa.tester@example.com", "2030-01-15"]


def _pattern_candidate(pattern: str, want_match: bool) -> Optional[str]:
    """Finds a probe value that matches (or violates) an HTML pattern; HTML anchors patterns implicitly."""
    try:
        compiled = re.compile(f"^(?:{pattern})$")
    except re.error:
        return None
    for probe in _PATTERN_PROBES:
        if bool(compiled.match(probe)) == want_match:
            return probe
    return None


# ── Requirement parsing ──────────────────────────────────────────────────────

_REQ_EXACT_DIGITS = re.compile(r"(?:exactly|only)\s+(\d+)\s+(?:numeric\s+)?digits?", re.I)
_REQ_DIGITS_N = re.compile(r"\b(\d+)[-\s]digits?\b", re.I)
_REQ_EXACT_LEN = re.compile(r"exactly\s+(\d+)\s+(?:characters?|chars?)", re.I)
_REQ_MAX_LEN = re.compile(r"(?:at\s+most|no\s+more\s+than|maximum\s+(?:of\s+)?|max(?:imum)?\s+length\s+(?:of\s+)?|up\s+to|not\s+exceed(?:ing)?)\s*(\d+)\s*(?:characters?|chars?)", re.I)
_REQ_MIN_LEN = re.compile(r"(?:at\s+least|no\s+(?:fewer|less)\s+than|minimum\s+(?:of\s+)?|min(?:imum)?\s+length\s+(?:of\s+)?)\s*(\d+)\s*(?:characters?|chars?)", re.I)
_REQ_BETWEEN_LEN = re.compile(r"between\s+(\d+)\s+and\s+(\d+)\s*(?:characters?|chars?)", re.I)
_REQ_REQUIRED = re.compile(r"\b(?:is|are)\s+(?:a\s+)?(?:required|mandatory)\b|\bcannot\s+be\s+(?:empty|blank)\b|\bmust\s+not\s+be\s+(?:empty|blank)\b|\bmust\s+be\s+provided\b", re.I)


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in _GENERIC_WORDS]


def _field_terms(field: ObservedField) -> List[str]:
    raw = " ".join([field.label, field.name.replace("_", " ").replace("-", " "),
                    field.field_id.replace("_", " ").replace("-", " ")])
    return list(dict.fromkeys(_words(raw)))


def match_requirement_field(requirement: str, fields: List[ObservedField]) -> Optional[ObservedField]:
    """Maps a requirement sentence to the observed field it talks about (best term overlap; ties -> None)."""
    req_words = set(_words(requirement))
    scored: List[Tuple[int, ObservedField]] = []
    for f in fields:
        overlap = len(req_words & set(_field_terms(f)))
        if overlap:
            scored.append((overlap, f))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def parse_requirement(requirement: str, field: ObservedField) -> List[FieldConstraint]:
    """Extracts the field rules a requirement sentence states explicitly."""
    out: List[FieldConstraint] = []
    common = dict(field_selector=field.selector, field_label=field.label or field.name,
                  source=SOURCE_REQUIREMENT, source_detail=requirement.strip())
    m = _REQ_EXACT_DIGITS.search(requirement) or _REQ_DIGITS_N.search(requirement)
    if m:
        out.append(FieldConstraint(kind="EXACT_DIGITS", value=m.group(1), **common))
    else:
        m = _REQ_EXACT_LEN.search(requirement)
        if m:
            out.append(FieldConstraint(kind="EXACT_LENGTH", value=m.group(1), **common))
    m = _REQ_BETWEEN_LEN.search(requirement)
    if m:
        out.append(FieldConstraint(kind="MIN_LENGTH", value=m.group(1), **common))
        out.append(FieldConstraint(kind="MAX_LENGTH", value=m.group(2), **common))
    else:
        m = _REQ_MAX_LEN.search(requirement)
        if m:
            out.append(FieldConstraint(kind="MAX_LENGTH", value=m.group(1), **common))
        m = _REQ_MIN_LEN.search(requirement)
        if m:
            out.append(FieldConstraint(kind="MIN_LENGTH", value=m.group(1), **common))
    if _REQ_REQUIRED.search(requirement):
        out.append(FieldConstraint(kind="REQUIRED", **common))
    return out


# ── Constraint extraction ────────────────────────────────────────────────────

def html_constraints(field: ObservedField) -> List[FieldConstraint]:
    common = dict(field_selector=field.selector, field_label=field.label or field.name, source=SOURCE_HTML)
    out: List[FieldConstraint] = []
    if field.required:
        out.append(FieldConstraint(kind="REQUIRED", source_detail="required attribute", **common))
    if field.maxlength is not None and field.maxlength > 0 and field.field_type in _TEXT_TYPES:
        out.append(FieldConstraint(kind="MAX_LENGTH", value=str(field.maxlength), source_detail=f'maxlength="{field.maxlength}"', **common))
    if field.minlength is not None and field.minlength > 0 and field.field_type in _TEXT_TYPES:
        out.append(FieldConstraint(kind="MIN_LENGTH", value=str(field.minlength), source_detail=f'minlength="{field.minlength}"', **common))
    if field.field_type in ("number", "range", "date"):
        if field.min is not None:
            out.append(FieldConstraint(kind="MIN_VALUE", value=field.min, source_detail=f'min="{field.min}"', **common))
        if field.max is not None:
            out.append(FieldConstraint(kind="MAX_VALUE", value=field.max, source_detail=f'max="{field.max}"', **common))
    if field.pattern:
        out.append(FieldConstraint(kind="PATTERN", value=field.pattern, source_detail=f'pattern="{field.pattern}"', **common))
    if field.field_type == "email":
        out.append(FieldConstraint(kind="TYPE_EMAIL", source_detail='type="email"', **common))
    return out


def _unknown_for(field: ObservedField, route: str, has_rules: bool) -> Optional[UnknownConstraint]:
    if has_rules:
        return None
    if field.field_type in ("tel", "number", "date", "text", "textarea", "search", "url"):
        reason = {
            "tel": 'type="tel" declares no length, pattern or format; the valid digit count is unknown',
            "number": "numeric field declares no min/max; the valid range is unknown",
            "date": "date field declares no min/max; the accepted date range is unknown",
        }.get(field.field_type, "field declares no length or format constraint; its validation rule is unknown")
        return UnknownConstraint(
            field_selector=field.selector,
            field_label=field.label or field.name,
            route=route,
            reason=reason,
            evidence_needed="An explicit requirement, a pattern/min/max/maxlength attribute, or an observed validation message.",
        )
    return None


# ── Boundary case design ─────────────────────────────────────────────────────

def _cases_for_constraint(field: ObservedField, c: FieldConstraint, form: ObservedForm) -> List[BoundaryCase]:
    base = dict(field_selector=field.selector, field_label=field.label or field.name, constraint=c)
    browser_enforced = c.source == SOURCE_HTML and not form.novalidate
    cases: List[BoundaryCase] = []

    if c.kind == "REQUIRED":
        if browser_enforced:
            cases.append(BoundaryCase(boundary="empty", input_class="EMPTY", input_value="", expected=EXPECT_REJECT,
                                      oracle="FIELD_VALIDITY", oracle_expected="valueMissing", **base))
        elif c.source == SOURCE_REQUIREMENT:
            cases.append(BoundaryCase(boundary="empty", input_class="EMPTY", input_value="", expected=EXPECT_REJECT,
                                      oracle="SUBMISSION_FEEDBACK", oracle_expected="rejected", **base))
            cases.append(BoundaryCase(boundary="whitespace only", input_class="WHITESPACE", input_value="   ",
                                      expected=EXPECT_REJECT, oracle="SUBMISSION_FEEDBACK", oracle_expected="rejected", **base))
        return cases

    if c.kind == "MAX_LENGTH":
        n = int(c.value)
        html_max = field.maxlength
        for length, label in ((n - 1, f"maxlength-1 ({n - 1})"), (n, f"maxlength ({n})"), (n + 1, f"maxlength+1 ({n + 1})")):
            if length < 1:
                continue
            value = text_of_length(length)
            if browser_enforced or (html_max is not None and length > html_max):
                # The browser truncates input at maxlength: observable as the stored field value length.
                expect_len = min(length, html_max if html_max is not None else n)
                cases.append(BoundaryCase(boundary=label, input_class="TOO_LONG" if length > n else "VALID",
                                          input_value=value, expected=EXPECT_REJECT if length > n else EXPECT_ACCEPT,
                                          oracle="VALUE_LENGTH", oracle_expected=f"=={expect_len}",
                                          requires_submit=False, **base))
            else:
                cases.append(BoundaryCase(boundary=label, input_class="TOO_LONG" if length > n else "VALID",
                                          input_value=value, expected=EXPECT_REJECT if length > n else EXPECT_ACCEPT,
                                          oracle="SUBMISSION_FEEDBACK",
                                          oracle_expected="rejected" if length > n else "accepted", **base))
        return cases

    if c.kind == "MIN_LENGTH":
        n = int(c.value)
        for length, label in ((n - 1, f"minlength-1 ({n - 1})"), (n, f"minlength ({n})")):
            if length < 1:
                continue
            invalid = length < n
            if browser_enforced:
                cases.append(BoundaryCase(boundary=label, input_class="TOO_SHORT" if invalid else "VALID",
                                          input_value=text_of_length(length),
                                          expected=EXPECT_REJECT if invalid else EXPECT_ACCEPT, oracle="FIELD_VALIDITY",
                                          oracle_expected="tooShort" if invalid else "valid", requires_submit=False, **base))
            else:
                cases.append(BoundaryCase(boundary=label, input_class="TOO_SHORT" if invalid else "VALID",
                                          input_value=text_of_length(length),
                                          expected=EXPECT_REJECT if invalid else EXPECT_ACCEPT, oracle="SUBMISSION_FEEDBACK",
                                          oracle_expected="rejected" if invalid else "accepted", **base))
        return cases

    if c.kind in ("EXACT_DIGITS", "EXACT_LENGTH"):
        n = int(c.value)
        make = digits_of_length if c.kind == "EXACT_DIGITS" else text_of_length
        partitions = [(n - 1, "TOO_SHORT"), (n, "VALID"), (n + 1, "TOO_LONG"), (n + 2, "TOO_LONG")]
        for length, cls in partitions:
            if length < 1:
                continue
            value = make(length)
            if field.maxlength is not None and length > field.maxlength:
                cases.append(BoundaryCase(boundary=f"{length} {'digits' if c.kind == 'EXACT_DIGITS' else 'characters'}",
                                          input_class=cls, input_value=value, expected=EXPECT_REJECT,
                                          oracle="VALUE_LENGTH", oracle_expected=f"=={field.maxlength}",
                                          requires_submit=False, **base))
                continue
            cases.append(BoundaryCase(boundary=f"{length} {'digits' if c.kind == 'EXACT_DIGITS' else 'characters'}",
                                      input_class=cls, input_value=value,
                                      expected=EXPECT_ACCEPT if cls == "VALID" else EXPECT_REJECT,
                                      oracle="SUBMISSION_FEEDBACK",
                                      oracle_expected="accepted" if cls == "VALID" else "rejected", **base))
        if c.kind == "EXACT_DIGITS":
            for value, cls in ((text_of_length(n, "abcdefghij"), "NON_NUMERIC"),
                               ((digits_of_length(max(n - 2, 1)) + "-!")[:n], "MIXED_SYMBOLS")):
                cases.append(BoundaryCase(boundary=f"{cls.lower()} ({n} chars)", input_class=cls, input_value=value,
                                          expected=EXPECT_REJECT, oracle="SUBMISSION_FEEDBACK",
                                          oracle_expected="rejected", **base))
        return cases

    if c.kind in ("MIN_VALUE", "MAX_VALUE") and field.field_type in ("number", "range"):
        bound = _num(c.value)
        if bound is None:
            return cases
        step = _num(field.step) or 1.0
        is_max = c.kind == "MAX_VALUE"
        outside = bound + step if is_max else bound - step
        flag = "rangeOverflow" if is_max else "rangeUnderflow"
        cases.append(BoundaryCase(boundary=f"{'max' if is_max else 'min'} ({_fmt_num(bound, field.step)})", input_class="VALID",
                                  input_value=_fmt_num(bound, field.step), expected=EXPECT_ACCEPT, oracle="FIELD_VALIDITY",
                                  oracle_expected="valid", requires_submit=False, **base))
        cases.append(BoundaryCase(boundary=f"{'max+step' if is_max else 'min-step'} ({_fmt_num(outside, field.step)})",
                                  input_class="OUT_OF_RANGE", input_value=_fmt_num(outside, field.step),
                                  expected=EXPECT_REJECT, oracle="FIELD_VALIDITY", oracle_expected=flag,
                                  requires_submit=False, **base))
        return cases

    if c.kind in ("MIN_VALUE", "MAX_VALUE") and field.field_type == "date":
        bound = _date(c.value)
        if bound is None:
            return cases
        is_max = c.kind == "MAX_VALUE"
        outside = bound + _dt.timedelta(days=1) if is_max else bound - _dt.timedelta(days=1)
        flag = "rangeOverflow" if is_max else "rangeUnderflow"
        cases.append(BoundaryCase(boundary=f"{'max' if is_max else 'min'} date ({bound.isoformat()})", input_class="VALID",
                                  input_value=bound.isoformat(), expected=EXPECT_ACCEPT, oracle="FIELD_VALIDITY",
                                  oracle_expected="valid", requires_submit=False, **base))
        cases.append(BoundaryCase(boundary=f"{'day after max' if is_max else 'day before min'} ({outside.isoformat()})",
                                  input_class="OUT_OF_RANGE", input_value=outside.isoformat(), expected=EXPECT_REJECT,
                                  oracle="FIELD_VALIDITY", oracle_expected=flag, requires_submit=False, **base))
        return cases

    if c.kind == "PATTERN":
        bad = _pattern_candidate(c.value or "", want_match=False)
        good = _pattern_candidate(c.value or "", want_match=True)
        if bad is not None:
            cases.append(BoundaryCase(boundary="pattern mismatch", input_class="FORMAT_INVALID", input_value=bad,
                                      expected=EXPECT_REJECT, oracle="FIELD_VALIDITY", oracle_expected="patternMismatch",
                                      requires_submit=False, **base))
        if good is not None:
            cases.append(BoundaryCase(boundary="pattern match", input_class="VALID", input_value=good,
                                      expected=EXPECT_ACCEPT, oracle="FIELD_VALIDITY", oracle_expected="valid",
                                      requires_submit=False, **base))
        return cases

    if c.kind == "TYPE_EMAIL" and not field.pattern:
        cases.append(BoundaryCase(boundary="missing @", input_class="FORMAT_INVALID", input_value=f"qa.tester.{UNIQUE_TOKEN}.example.com",
                                  expected=EXPECT_REJECT, oracle="FIELD_VALIDITY", oracle_expected="typeMismatch",
                                  requires_submit=False, **base))
        return cases

    return cases


_ALIGN_STOP = _GENERIC_WORDS | {"with", "from", "that", "this", "into", "when", "after", "before", "allow", "allows",
                                "page", "view", "click", "button", "form", "application", "system", "requires", "require",
                                "correct", "correctly", "properly", "through", "each", "every", "item", "items"}


def align_requirements(requirements: List[str], inventory: Any) -> Dict[str, Any]:
    """
    Classifies each supplied requirement against what the investigation observed:
      MATCHED  - maps to one observed field and states a rule the planner can test
      WEAK     - maps to an observed field without a testable rule, maps ambiguously, or only shares
                 words with observed texts/routes/controls
      UNMATCHED- nothing observed on the target corresponds to it
    A warning is produced when no requirement (or only a minority) aligns, so coverage is never implied.
    """
    fields = [f for form in getattr(inventory, "forms", []) for f in form.fields]
    observed_words = set()
    for text in list(getattr(inventory, "observed_texts", lambda: [])()) + [s.route for s in getattr(inventory, "states", [])] \
            + [c.selector for c in getattr(inventory, "controls", [])] + [f.name + " " + f.field_id + " " + f.label for f in fields]:
        observed_words |= set(_words(text))
    rows = []
    for req in requirements or []:
        req_words = {w for w in _words(req) if len(w) >= 4 and w not in _ALIGN_STOP}
        candidates = [f for f in fields if req_words & set(_field_terms(f))]
        # Same per-form mapping the planner uses (plan_form_boundaries), so alignment matches what gets tested.
        per_form = [(form, match_requirement_field(req, form.fields)) for form in getattr(inventory, "forms", [])]
        mapped = [(form, f, parse_requirement(req, f)) for form, f in per_form if f is not None]
        testable = [(form, f, rules) for form, f, rules in mapped if rules]
        shared = sorted(req_words & observed_words)
        if testable:
            status = "MATCHED"
            evidence = "; ".join(f"{f.selector} on {form.route} ({', '.join(r.kind for r in rules)})" for form, f, rules in testable[:4])
        elif mapped:
            form, field, _ = mapped[0]
            status, evidence = "WEAK", f"field {field.selector} ({field.label or field.name}) but no testable rule was recognised"
        elif candidates:
            status, evidence = "WEAK", f"ambiguous: {', '.join(sorted({c.selector for c in candidates})[:4])}"
        elif shared:
            status, evidence = "WEAK", f"only shares words with observed content: {', '.join(shared[:6])}"
        else:
            status, evidence = "UNMATCHED", "no observed field, route, control or text corresponds to it"
        rows.append({"requirement": req, "status": status, "evidence": evidence})
    matched = sum(1 for r in rows if r["status"] == "MATCHED")
    unmatched = sum(1 for r in rows if r["status"] == "UNMATCHED")
    warning = None
    if rows and matched == 0:
        warning = (f"None of the {len(rows)} supplied requirements could be mapped to a testable rule on this application "
                   f"({unmatched} have no counterpart at all). They may describe a different application; "
                   "no requirement-based coverage is claimed.")
    elif rows and unmatched * 2 > len(rows):
        warning = f"{unmatched} of {len(rows)} supplied requirements have no counterpart on this application."
    return {"requirements": rows, "matched": matched, "weak": len(rows) - matched - unmatched,
            "unmatched": unmatched, "warning": warning}


def plan_form_boundaries(form: ObservedForm, requirements: Optional[List[str]] = None) -> FormBoundaryPlan:
    """Designs boundary cases for every field of one observed form."""
    plan = FormBoundaryPlan(form=form)
    constraints_by_field: Dict[str, List[FieldConstraint]] = {f.selector: html_constraints(f) for f in form.fields}

    for req in requirements or []:
        target = match_requirement_field(req, form.fields)
        if target is None:
            continue
        parsed = parse_requirement(req, target)
        if parsed:
            constraints_by_field[target.selector].extend(parsed)
        else:
            plan.unmapped_requirements.append(req)

    for field in form.fields:
        constraints = constraints_by_field.get(field.selector, [])
        # A requirement for the same kind supersedes the HTML attribute as the expected-behaviour source,
        # but the HTML attribute still governs what the browser physically allows (handled per case).
        kinds_from_requirements = {c.kind for c in constraints if c.source == SOURCE_REQUIREMENT}
        effective = [c for c in constraints if not (c.source == SOURCE_HTML and c.kind in kinds_from_requirements)]
        for c in effective:
            plan.cases.extend(_cases_for_constraint(field, c, form))
        unknown = _unknown_for(field, form.route, has_rules=bool(effective))
        if unknown:
            plan.unknowns.append(unknown)
    return plan
