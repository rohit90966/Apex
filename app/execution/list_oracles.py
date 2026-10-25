"""
Collection oracles (ordering and filtering) shared by the executor and exported Playwright scripts.

Both functions take the record collection gathered in the page by COLLECTION_SCRIPT and return
(verdict, actual, message) with verdict "pass", "fail" or "not_evaluable". They depend only on `re`
so the exporter can embed this module's source verbatim and keep identical semantics.

Ordering: when the records have column headers and the sort option names a header ("Price: Low to
High" -> the "Price" column), values are read from that column only; otherwise from each record's
text. `any_direction` checks only that the order is monotonic in that key, which does not depend
on reading a direction from the option label.

Filtering: the selected option must equal the value of one of each record's own fields. The field
is identified from the data: it is the field whose value is one of the filter's options in every
record (the attribute the filter ranges over). Without such a field the result is not evaluable,
rather than falling back to a substring match on the whole record.
"""

import re

_NUMBER_RE = re.compile(r"[$€£₹]\s*-?\d[\d,]*(?:\.\d+)?|-?\d[\d,]*(?:\.\d+)?")


def _number(text):
    text = str(text or "")
    m = re.search(r"[$€£₹]\s*-?\d[\d,]*(?:\.\d+)?", text) or re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    return float(re.sub(r"[^\d.\-]", "", m.group(0))) if m else None


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def column_values(data, key, column_hint):
    """Values of the column whose header contains `column_hint`; None when no such column exists."""
    headers = [_norm(h) for h in (data or {}).get("headers") or []]
    hint = _norm(column_hint)
    if not hint or not headers:
        return None
    idx = next((i for i, h in enumerate(headers) if hint in h), None)
    fields = (data or {}).get("fields") or []
    if idx is None or not fields or any(len(f) <= idx for f in fields):
        return None
    raw = [f[idx] for f in fields]
    values = [_number(v) for v in raw] if key == "number" else [str(v).strip() for v in raw]
    return values if all(v is not None and v != "" for v in values) else None


def _single_amounts(data):
    """One currency amount per record (an unambiguous numeric key without column headers), else None."""
    out = []
    for t in (data or {}).get("texts") or []:
        amounts = re.findall(r"[$\u20ac\u00a3\u20b9]\s*-?\d[\d,]*(?:\.\d+)?", str(t))
        if len(amounts) != 1:
            return None
        out.append(float(re.sub(r"[^\d.\-]", "", amounts[0])))
    return out or None


def evaluate_sorted(data, spec):
    key = spec.get("key", "text")
    values = column_values(data, key, spec.get("column_hint"))
    source = f"column '{spec.get('column_hint')}'" if values is not None else "record text"
    if values is None and spec.get("any_direction"):
        # The convention-level check needs an unambiguous key: a named column, or exactly one amount per record.
        if key == "number":
            values, source = _single_amounts(data), "the single amount in each record"
        else:
            titles = [str(v).strip() for v in (data or {}).get("values") or []]
            ok = len(titles) >= 2 and all(titles) and len(set(t.casefold() for t in titles)) == len(titles)
            values, source = (titles if ok else None), "each record's title"
        if values is None:
            return "not_evaluable", None, ("the sort key could not be located unambiguously in the records "
                                           "(no matching column header; not exactly one amount per record)")
    if values is None:
        values = list((data or {}).get("values") or [])
    if len(values) < 2:
        return "not_evaluable", None, "could not identify at least two records to compare"
    sort_key = (lambda v: v) if key == "number" else (lambda v: str(v).casefold())
    asc = sorted(values, key=sort_key)
    sample = ", ".join(str(v) for v in values[:6])
    if spec.get("any_direction"):
        if values == asc or values == asc[::-1]:
            return "pass", f"{len(values)} records monotonic by {key} ({source}): {sample}", None
        return "fail", sample, f"Records are not ordered by {key} in either direction ({source}): {sample}"
    direction = spec.get("direction", "asc")
    expected = asc if direction == "asc" else asc[::-1]
    if values == expected:
        return "pass", f"{len(values)} records ordered {key} {direction} ({source}): {sample}", None
    return "fail", sample, f"Records are not ordered by {key} {direction} ({source}): {sample}"


def evaluate_filter(data, expected, domain):
    texts = (data or {}).get("texts") or []
    fields = (data or {}).get("fields") or []
    if not texts or not fields:
        return "not_evaluable", None, "could not identify any listed records"
    options = {_norm(d) for d in domain or [] if _norm(d)}
    wanted = _norm(expected)
    values = []
    for f in fields:
        in_domain = [v for v in f if _norm(v) in options]
        if not in_domain:
            return "not_evaluable", None, ("could not identify which field of each record the filter ranges over "
                                           "(a record has no field whose value is one of the filter options)")
        values.append(in_domain)
    mismatched = [texts[i][:80] for i, vals in enumerate(values) if wanted not in {_norm(v) for v in vals}]
    if mismatched:
        return "fail", f"{len(mismatched)}/{len(texts)} records have a different value", \
            f"Filter '{expected}' shows records whose value differs: {mismatched[:3]}"
    return "pass", f"all {len(texts)} records have the value '{expected}'", None
