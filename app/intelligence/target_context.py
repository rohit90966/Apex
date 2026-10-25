"""
Target Independence Contract.

Every investigation produces a TargetContext (who/what/when was investigated) and an
ObservedInventory (what was actually seen in the browser during *this* run). Test planning,
validation, execution and export consume these objects so that a plan can only reference
routes, selectors, option values and texts observed on the same origin during the same
investigation. Reusable domain knowledge never enters the inventory.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field

INVENTORY_SCHEMA_VERSION = "2.0"

PROVENANCE_LIVE = "LIVE_INVESTIGATION"
PROVENANCE_LEGACY = "LEGACY_REPORT"

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Path segments that identify a data instance rather than a route (numeric ids, uuids, hex ids).
_INSTANCE_SEGMENT = re.compile(r"^(?=.*\d)[0-9a-fA-F-]{6,}$|^\d+$")


def canonical_origin(url: str) -> str:
    """scheme://host[:port], lowercased, default ports removed. Empty string when unparseable."""
    if not url or not isinstance(url, str):
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return ""
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port and port != _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def same_origin(url_a: str, url_b: str) -> bool:
    origin_a, origin_b = canonical_origin(url_a), canonical_origin(url_b)
    return bool(origin_a) and origin_a == origin_b


def route_of(url: str) -> str:
    """Path component of a URL ('/' when empty)."""
    try:
        return urllib.parse.urlparse(url or "").path or "/"
    except ValueError:
        return "/"


def is_instance_route(route: str) -> bool:
    """True when the route embeds a data-instance identifier (e.g. /tasks/3f2a9c1b)."""
    return any(_INSTANCE_SEGMENT.match(seg) for seg in (route or "").split("/") if seg)


def route_template(route: str) -> str:
    """Route with instance identifiers replaced by '{id}' (/tasks/3f2a9c1b -> /tasks/{id})."""
    return "/" + "/".join("{id}" if _INSTANCE_SEGMENT.match(seg) else seg for seg in (route or "").split("/") if seg)


class TargetContext(BaseModel):
    """Identity of one investigation of one target."""
    schema_version: str = INVENTORY_SCHEMA_VERSION
    run_id: str
    origin: str
    start_url: str
    final_url: Optional[str] = None
    investigated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    description: Optional[str] = None
    requirements: List[str] = Field(default_factory=list)
    credentials_available: bool = False
    credential_source: Optional[str] = None  # USER_PROVIDED | ENVIRONMENT | DEMO_REGISTRY | None


class ObservedField(BaseModel):
    selector: str
    name: str = ""
    field_id: str = ""
    label: str = ""
    field_type: str = "text"
    required: bool = False
    minlength: Optional[int] = None
    maxlength: Optional[int] = None
    min: Optional[str] = None
    max: Optional[str] = None
    pattern: Optional[str] = None
    step: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    checked: Optional[bool] = None


class ObservedForm(BaseModel):
    form_id: str
    form_selector: str = "form"
    action: str = ""
    method: str = "GET"
    novalidate: bool = False
    submit_selector: str = ""
    state_id: str = ""
    url: str = ""
    route: str = "/"
    authenticated: bool = False
    fields: List[ObservedField] = Field(default_factory=list)
    seen_in_states: List[str] = Field(default_factory=list)

    @property
    def is_login_form(self) -> bool:
        return sum(1 for f in self.fields if f.field_type == "password") == 1 and any(
            f.field_type in ("text", "email") for f in self.fields
        )


class ObservedControl(BaseModel):
    selector: str
    tag: str = ""
    control_type: str = ""
    role: str = ""
    name: str = ""
    href: str = ""
    options: List[str] = Field(default_factory=list)
    option_labels: List[str] = Field(default_factory=list)
    state_id: str = ""
    url: str = ""
    route: str = "/"
    authenticated: bool = False
    seen_in_states: List[str] = Field(default_factory=list)


class ObservedState(BaseModel):
    state_id: str
    url: str
    route: str = "/"
    title: str = ""
    headings: List[str] = Field(default_factory=list)
    http_status: Optional[int] = None
    authenticated: bool = False
    text_snippet: str = ""
    validation_message: Optional[str] = None
    validation_locator: Optional[str] = None
    reached_by: str = "UNKNOWN"  # ENTRY | NAVIGATION | AUTHENTICATION | INTERACTION | FORM_SUBMISSION | UNKNOWN


class ObservedAuth(BaseModel):
    boundary_observed: bool = False
    login_route: Optional[str] = None
    credentials_available: bool = False
    credential_source: Optional[str] = None
    verified: bool = False


class ObservedTransition(BaseModel):
    transition_id: str
    source_state_id: str
    destination_state_id: Optional[str] = None
    action_type: str
    target: str = ""
    value: Optional[str] = None
    route_change: bool = False
    observed_effect: str = ""
    evidence_ids: List[str] = Field(default_factory=list)


class ObservedInventory(BaseModel):
    """Everything the current investigation actually observed on its own origin."""
    schema_version: str = INVENTORY_SCHEMA_VERSION
    run_id: str = ""
    origin: str = ""
    provenance: str = PROVENANCE_LIVE
    states: List[ObservedState] = Field(default_factory=list)
    controls: List[ObservedControl] = Field(default_factory=list)
    forms: List[ObservedForm] = Field(default_factory=list)
    transitions: List[ObservedTransition] = Field(default_factory=list)
    auth: ObservedAuth = Field(default_factory=ObservedAuth)
    requirements: List[str] = Field(default_factory=list)

    # ── Lookup helpers ─────────────────────────────────────────────────────
    def state(self, state_id: str) -> Optional[ObservedState]:
        return next((s for s in self.states if s.state_id == state_id), None)

    def selectors(self) -> Set[str]:
        out = {c.selector for c in self.controls if c.selector}
        for form in self.forms:
            out.update(f.selector for f in form.fields if f.selector)
            if form.submit_selector:
                out.add(form.submit_selector)
        return out

    def routes(self) -> Set[str]:
        return {s.route for s in self.states if s.route}

    def navigable_routes(self) -> List[str]:
        """Routes reachable by direct navigation: observed by entry/navigation, not only after a form POST."""
        by_route: Dict[str, List[ObservedState]] = {}
        for s in self.states:
            by_route.setdefault(s.route, []).append(s)
        result = []
        for route, states in by_route.items():
            if is_instance_route(route):
                continue
            if all(s.reached_by == "FORM_SUBMISSION" for s in states):
                continue
            result.append(route)
        return sorted(result)

    def linked_instance_routes(self) -> List[str]:
        """
        Instance routes (e.g. /items/42) that a test can reach: reached by navigation and linked from a
        page on a navigable (non-instance) route, so the record existed in content the test can see.
        One representative per route template, since sibling instances share the same page template.
        """
        navigable = set(self.navigable_routes())
        linked = {route_of(c.href) for c in self.controls if c.href and c.route in navigable}
        chosen: Dict[str, str] = {}
        for s in self.states:
            if (is_instance_route(s.route) and s.route in linked and s.reached_by != "FORM_SUBMISSION"
                    and route_template(s.route) not in chosen):
                chosen[route_template(s.route)] = s.route
        return sorted(chosen.values())

    def form_routes(self) -> List[str]:
        """Routes on which a test can exercise forms and controls: navigable routes plus linked instance routes."""
        return sorted(set(self.navigable_routes()) | set(self.linked_instance_routes()))

    def stable_headings(self, route: str) -> List[str]:
        """Headings present in every navigation-reached observation of a route."""
        states = [s for s in self.states if s.route == route and s.reached_by != "FORM_SUBMISSION"]
        if not states:
            return []
        common = set(states[0].headings)
        for s in states[1:]:
            common &= set(s.headings)
        return [h for h in states[0].headings if h in common]

    def requires_auth(self, route: str) -> bool:
        """True when a route was only ever observed inside an authenticated session."""
        states = [s for s in self.states if s.route == route]
        return bool(states) and all(s.authenticated for s in states)

    def observed_texts(self) -> List[str]:
        texts: List[str] = []
        for s in self.states:
            texts.append(s.title)
            texts.extend(s.headings)
            texts.append(s.text_snippet)
            if s.validation_message:
                texts.append(s.validation_message)
        for c in self.controls:
            texts.append(c.name)
            texts.extend(c.option_labels)
        for form in self.forms:
            for f in form.fields:
                texts.append(f.label)
        return [t for t in texts if t]

    def text_observed(self, text: str) -> bool:
        needle = (text or "").strip().lower()
        if not needle:
            return False
        return any(needle in t.lower() for t in self.observed_texts())

    def route_observed(self, fragment: str) -> bool:
        frag = (fragment or "").strip()
        if not frag:
            return False
        if frag.startswith("http://") or frag.startswith("https://"):
            if canonical_origin(frag) != self.origin:
                return False
            frag = route_of(frag)
        return any(frag in s.url or frag == s.route for s in self.states)

    def controls_for_route(self, route: str) -> List[ObservedControl]:
        return [c for c in self.controls if c.route == route]

    def reproducible(self, item: Any) -> bool:
        """
        True when a control/form was seen in at least one state that a test can reach by navigating
        to its route (not only on a page produced by submitting a form).
        """
        state_ids = list(getattr(item, "seen_in_states", None) or []) or [getattr(item, "state_id", "")]
        for sid in state_ids:
            st = self.state(sid)
            if st is None or st.reached_by != "FORM_SUBMISSION":
                return True
        return False

    def login_form(self) -> Optional[ObservedForm]:
        return next((f for f in self.forms if f.is_login_form), None)


# ── Builders ──────────────────────────────────────────────────────────────

def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _field_from_raw(raw: Dict[str, Any]) -> Optional[ObservedField]:
    selector = raw.get("stable_selector") or (f"#{raw['id']}" if raw.get("id") else "")
    if not selector or selector in ("input", "select", "textarea"):
        return None
    return ObservedField(
        selector=selector,
        name=raw.get("name") or "",
        field_id=raw.get("id") or "",
        label=(raw.get("label") or raw.get("placeholder") or "")[:100],
        field_type=(raw.get("type") or "text").lower(),
        required=bool(raw.get("required") or raw.get("aria_required")),
        minlength=_int_or_none(raw.get("minlength")),
        maxlength=_int_or_none(raw.get("maxlength")),
        min=str(raw["min"]) if raw.get("min") not in (None, "") else None,
        max=str(raw["max"]) if raw.get("max") not in (None, "") else None,
        pattern=raw.get("pattern") or None,
        step=str(raw["step"]) if raw.get("step") not in (None, "") else None,
        options=[str(o) for o in (raw.get("select_options") or []) if str(o).strip()],
        checked=raw.get("checkbox_state") if isinstance(raw.get("checkbox_state"), bool) else None,
    )


def _reached_by(state_id: str, entry_state_id: Optional[str], transitions: Iterable[Any]) -> str:
    if state_id == entry_state_id:
        return "ENTRY"
    kinds: Set[str] = set()
    for tr in transitions:
        if getattr(tr, "destination_state_id", None) != state_id:
            continue
        act = str(getattr(tr, "action_type", "") or "").upper()
        if any(k in act for k in ("NAVIGATE", "GOTO", "NAV_LINK", "LINK")):
            kinds.add("NAVIGATION")
        elif "AUTHENTICATE" in act:
            kinds.add("AUTHENTICATION")  # reproducible through the authorized login prelude
        elif any(k in act for k in ("SUBMIT", "FORM", "FILL", "BYPASS", "REQUIRED_BLANK")):
            kinds.add("FORM_SUBMISSION")
        else:
            kinds.add("INTERACTION")
    if "NAVIGATION" in kinds:
        return "NAVIGATION"
    if "AUTHENTICATION" in kinds:
        return "AUTHENTICATION"
    if "INTERACTION" in kinds:
        return "INTERACTION"
    if "FORM_SUBMISSION" in kinds:
        return "FORM_SUBMISSION"
    return "UNKNOWN"


def build_inventory_from_state(state: Dict[str, Any]) -> ObservedInventory:
    """Builds the inventory from the live investigation state graph (single origin only)."""
    target_url = state.get("target_url") or ""
    origin = canonical_origin(target_url)
    states_dict: Dict[str, Any] = state.get("states") or {}
    transitions = list(state.get("transitions") or [])
    entry_state_id = next(iter(states_dict.keys()), None)

    inv = ObservedInventory(
        run_id=state.get("investigation_id") or "",
        origin=origin,
        requirements=[r for r in (state.get("user_requirements") or []) if r],
    )
    seen_controls: Set[tuple] = set()
    seen_forms: Set[tuple] = set()

    # Session state: observations captured after a verified authentication belong to the authenticated
    # session (the browser keeps its cookies), so tests reaching them need the login prelude.
    # Session signal: states reachable (via observed transitions) from the destination of a verified
    # AUTHENTICATE transition. (Capture timestamps were tried and are not reliable.)
    auth_dest = [getattr(tr, "destination_state_id", None) for tr in transitions
                 if "AUTHENTICATE" in str(getattr(tr, "action_type", "")).upper() and getattr(tr, "destination_state_id", None)]
    # A login whose result is rendered at the login form's own route is a submission response, not a page a
    # test can reach by navigating there.
    login_result_states: Set[str] = set()
    for tr in transitions:
        if "AUTHENTICATE" not in str(getattr(tr, "action_type", "")).upper():
            continue
        src, dst = states_dict.get(getattr(tr, "source_state_id", "") or ""), states_dict.get(getattr(tr, "destination_state_id", "") or "")
        if src is not None and dst is not None and route_of(getattr(src, "url", "")) == route_of(getattr(dst, "url", "")):
            login_result_states.add(tr.destination_state_id)
    in_session: Set[str] = set()
    if state.get("auth_verified") and auth_dest:
        frontier = list(auth_dest)
        while frontier:
            sid = frontier.pop()
            if sid in in_session:
                continue
            in_session.add(sid)
            frontier.extend(getattr(tr, "destination_state_id", None) for tr in transitions
                            if getattr(tr, "source_state_id", None) == sid and getattr(tr, "destination_state_id", None)
                            and "AUTHENTICATE" not in str(getattr(tr, "action_type", "")).upper())

    for state_id, obs in states_dict.items():
        url = getattr(obs, "url", "") or ""
        if canonical_origin(url) != origin:
            continue  # never record evidence from another origin
        route = route_of(url)
        authenticated = bool(getattr(obs, "is_authenticated", False)) or state_id in in_session
        inv.states.append(ObservedState(
            state_id=state_id,
            url=url,
            route=route,
            title=getattr(obs, "page_title", "") or "",
            headings=[h for h in (getattr(obs, "headings", None) or []) if h][:8],
            http_status=getattr(obs, "http_status", None),
            authenticated=authenticated,
            text_snippet=(getattr(obs, "visible_text_snippet", "") or "")[:2000],
            validation_message=getattr(obs, "validation_error_message", None),
            validation_locator=getattr(obs, "validation_error_locator", None),
            reached_by="FORM_SUBMISSION" if state_id in login_result_states else _reached_by(state_id, entry_state_id, transitions),
        ))
        for ctrl in getattr(obs, "interactive_controls", None) or []:
            sel = getattr(ctrl, "selector", "") or ""
            if not sel:
                continue
            if (route, sel) in seen_controls:
                known = next(c for c in inv.controls if c.route == route and c.selector == sel)
                if state_id not in known.seen_in_states:
                    known.seen_in_states.append(state_id)
                continue
            seen_controls.add((route, sel))
            constraints = getattr(ctrl, "declared_constraints", None) or {}
            inv.controls.append(ObservedControl(
                selector=sel,
                tag=(getattr(ctrl, "tag_name", "") or "").lower(),
                control_type=(getattr(ctrl, "element_type", "") or "").lower(),
                role=getattr(ctrl, "role", "") or "",
                name=getattr(ctrl, "accessible_name", "") or "",
                href=constraints.get("href") or "",
                options=[str(o) for o in (constraints.get("select_options") or []) if str(o).strip()],
                option_labels=[str(o) for o in (constraints.get("select_option_labels") or []) if str(o).strip()],
                state_id=state_id,
                url=url,
                route=route,
                authenticated=authenticated,
                seen_in_states=[state_id],
            ))
        for raw_form in getattr(obs, "forms", None) or []:
            fields = [f for f in (_field_from_raw(r) for r in raw_form.get("fields", [])) if f]
            signature = (route, raw_form.get("form_selector"), tuple(f.selector for f in fields))
            if signature in seen_forms:
                known = next(fm for fm in inv.forms
                             if (fm.route, fm.form_selector, tuple(f.selector for f in fm.fields)) == signature)
                if state_id not in known.seen_in_states:
                    known.seen_in_states.append(state_id)
                continue
            seen_forms.add(signature)
            inv.forms.append(ObservedForm(
                form_id=str(raw_form.get("form_id") or ""),
                form_selector=raw_form.get("form_selector") or "form",
                action=raw_form.get("action") or "",
                method=(raw_form.get("method") or "GET").upper(),
                novalidate=bool(raw_form.get("novalidate")),
                submit_selector=raw_form.get("submit_control_selector") or "",
                state_id=state_id,
                url=url,
                route=route,
                authenticated=authenticated,
                fields=fields,
                seen_in_states=[state_id],
            ))

    for tr in transitions:
        if getattr(tr, "status", "confirmed") != "confirmed":
            continue
        inv.transitions.append(ObservedTransition(
            transition_id=getattr(tr, "transition_id", ""),
            source_state_id=getattr(tr, "source_state_id", ""),
            destination_state_id=getattr(tr, "destination_state_id", None),
            action_type=str(getattr(tr, "action_type", "") or ""),
            target=getattr(tr, "target_element_desc", "") or "",
            value=getattr(tr, "value", None),
            route_change=bool(getattr(tr, "is_route_change", False)),
            observed_effect=getattr(tr, "observed_effect", "") or "",
            evidence_ids=list(getattr(tr, "evidence_ids", None) or [])[:5],
        ))

    creds = state.get("auth_credentials") if isinstance(state.get("auth_credentials"), dict) else None
    login_form = inv.login_form()
    inv.auth = ObservedAuth(
        boundary_observed=bool(state.get("auth_boundary_detected")) or login_form is not None,
        login_route=login_form.route if login_form else None,
        credentials_available=bool(creds),
        credential_source=(creds or {}).get("source"),
        verified=bool(state.get("auth_verified")),
    )
    return inv


def build_target_context(state: Dict[str, Any]) -> TargetContext:
    creds = state.get("auth_credentials") if isinstance(state.get("auth_credentials"), dict) else None
    current = state.get("current_state")
    return TargetContext(
        run_id=state.get("investigation_id") or "",
        origin=canonical_origin(state.get("target_url") or ""),
        start_url=state.get("target_url") or "",
        final_url=getattr(current, "url", None) if current is not None else None,
        description=state.get("user_description"),
        requirements=[r for r in (state.get("user_requirements") or []) if r],
        credentials_available=bool(creds),
        credential_source=(creds or {}).get("source"),
    )


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def inventory_from_report(report: Any) -> ObservedInventory:
    """
    Returns the inventory carried by an investigation report.

    Reports produced by this version carry `observed_inventory`. Older reports (and test doubles)
    are reconstructed from their legacy sections with provenance LEGACY_REPORT; evidence from a
    different origin than the report's target is always discarded.
    """
    overview = _get(report, "overview")
    target_url = (_get(overview, "target_url") if overview is not None else None) or _get(report, "target_url") or ""
    origin = canonical_origin(target_url)

    raw = _get(report, "observed_inventory")
    if raw:
        inv = raw if isinstance(raw, ObservedInventory) else ObservedInventory.model_validate(raw)
        if inv.origin and origin and inv.origin != origin:
            raise ValueError(f"Inventory origin {inv.origin} does not match report target origin {origin}")
        return inv

    inv = ObservedInventory(
        run_id=(_get(overview, "investigation_id") if overview is not None else "") or "",
        origin=origin,
        provenance=PROVENANCE_LEGACY,
    )

    def add_state(url: str, heading: str = "", state_id: str = "") -> None:
        if not url:
            return
        absolute = urllib.parse.urljoin(target_url, url) if target_url else url
        if canonical_origin(absolute) != origin:
            return
        if any(s.url == absolute for s in inv.states):
            return
        inv.states.append(ObservedState(
            state_id=state_id or f"legacy_{len(inv.states)}",
            url=absolute,
            route=route_of(absolute),
            headings=[heading] if heading else [],
            reached_by="ENTRY" if not inv.states else "UNKNOWN",
        ))

    for s in _get(report, "states", None) or []:
        add_state(_get(s, "url", ""), _get(s, "heading", ""))
    for area in _get(report, "discovered_application_map", None) or []:
        add_state(_get(area, "route_or_url", ""), "", _get(area, "area_id", ""))

    seen: Set[str] = set()
    for c in _get(report, "controls", None) or []:
        sel = _get(c, "selector", "") or ""
        if not sel or sel in seen or sel.startswith(("http://", "https://", "/")):
            continue
        seen.add(sel)
        act = str(_get(c, "action_type", "") or "").upper()
        tag = "select" if act in ("SELECT", "SELECT_OPTION") else ("input" if act == "FILL" else "button")
        inv.controls.append(ObservedControl(
            selector=sel, tag=tag, name=_get(c, "text", "") or "",
            options=[str(o) for o in (_get(c, "options", None) or [])],
            route="",  # legacy reports do not record where a control was observed
        ))

    auth = _get(report, "authentication_access")
    if auth is not None:
        inv.auth = ObservedAuth(
            boundary_observed=bool(_get(auth, "boundary_observed", False)),
            credentials_available=bool(_get(auth, "credentials_available", False)),
            credential_source=_get(auth, "credentials_source", None),
            verified=bool(_get(auth, "authentication_verified", False)),
        )
    return inv
