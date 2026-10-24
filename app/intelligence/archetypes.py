"""
VerityQA Website Archetype Classification & Application Intelligence Model.
==========================================================================
Classifies investigated web applications into domain archetypes (e.g., E-Commerce,
Banking/FinTech, SaaS/CRUD, Booking/Reservations, Authentication, Generic Web),
identifies fine-grained functional capabilities, extracts domain entities,
and constructs a unified Application Intelligence Model for knowledge retrieval
and grounded test synthesis.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WebsiteArchetype(str, Enum):
    """Primary domain archetypes recognized by VerityQA."""
    ECOMMERCE = "ECOMMERCE"
    BANKING_FINTECH = "BANKING_FINTECH"
    SAAS_CRUD = "SAAS_CRUD"
    BOOKING_RESERVATIONS = "BOOKING_RESERVATIONS"
    AUTHENTICATION = "AUTHENTICATION"
    GENERIC_WEB = "GENERIC_WEB"


class ApplicationCapability(str, Enum):
    """Fine-grained functional capabilities detected during exploration."""
    CATALOG_BROWSING = "CATALOG_BROWSING"
    CART_MANAGEMENT = "CART_MANAGEMENT"
    CHECKOUT_FLOW = "CHECKOUT_FLOW"
    USER_AUTHENTICATION = "USER_AUTHENTICATION"
    PROFILE_MANAGEMENT = "PROFILE_MANAGEMENT"
    ITEM_SEARCH_FILTER = "ITEM_SEARCH_FILTER"
    FINANCIAL_TRANSACTION = "FINANCIAL_TRANSACTION"
    FORM_VALIDATION = "FORM_VALIDATION"
    DRAWER_NAVIGATION = "DRAWER_NAVIGATION"
    CRUD_OPERATIONS = "CRUD_OPERATIONS"
    MULTI_STEP_WIZARD = "MULTI_STEP_WIZARD"
    PAGINATION_SORTING = "PAGINATION_SORTING"


class ApplicationIntelligenceModel(BaseModel):
    """
    Unified Application Intelligence Model.
    Synthesizes empirical investigation evidence, structural topology,
    archetype classifications, and discovered invariants.
    """
    target_url: str = Field(..., description="Target base URL of the investigated application")
    primary_archetype: WebsiteArchetype = Field(
        default=WebsiteArchetype.GENERIC_WEB,
        description="Dominant domain archetype classification"
    )
    secondary_archetypes: List[WebsiteArchetype] = Field(
        default_factory=list,
        description="Secondary or supporting archetypes detected"
    )
    confidence_scores: Dict[WebsiteArchetype, float] = Field(
        default_factory=dict,
        description="Normalized confidence score per recognized archetype [0.0 - 1.0]"
    )
    capabilities: List[ApplicationCapability] = Field(
        default_factory=list,
        description="Detected functional capabilities"
    )
    entities: List[str] = Field(
        default_factory=list,
        description="Discovered domain entities (e.g., Product, Cart, User, Order)"
    )
    workflows: List[str] = Field(
        default_factory=list,
        description="Discovered multi-state workflows and journeys"
    )
    invariants: List[str] = Field(
        default_factory=list,
        description="Observed domain invariants and business rules"
    )
    state_transitions_count: int = Field(default=0, description="Total verified state transitions discovered")
    controls_discovered_count: int = Field(default=0, description="Total actionable controls discovered")
    evidence_references: List[str] = Field(
        default_factory=list,
        description="Associated investigation evidence identifiers"
    )
    uncertainties: List[str] = Field(
        default_factory=list,
        description="Unresolved hypotheses or unconfirmed behaviors"
    )
    # Provenance: which observations support each capability, and how strongly.
    capability_evidence: Dict[str, List[str]] = Field(default_factory=dict)
    capability_confidence: Dict[str, float] = Field(default_factory=dict)
    # Capabilities with only weak or conflicting signals; never used to justify a test.
    unknown_capabilities: List[str] = Field(default_factory=list)


class ArchetypeClassifier:
    """
    Deterministic evidence-based classifier with optional LLM semantic disambiguation.
    Analyzes URLs, DOM controls, form inputs, landmark headings, and state transitions
    to produce an ApplicationIntelligenceModel.
    """

    # Keyword and token dictionaries for deterministic feature extraction
    ECOMMERCE_SIGNALS = {
        "urls": {"cart", "checkout", "inventory", "product", "products", "item", "items", "shop", "catalog", "order", "store"},
        "selectors": {"add-to-cart", "cart-badge", "shopping-cart", "product-sort", "price", "subtotal", "tax", "checkout-button", "finish"},
        "text": {"add to cart", "your cart", "shopping cart", "checkout", "shipping", "subtotal", "tax", "order total", "inventory", "products", "item total"},
    }

    BANKING_SIGNALS = {
        "urls": {"account", "accounts", "transfer", "statement", "statements", "balance", "loan", "wire", "deposit", "bill-pay"},
        "selectors": {"account-balance", "transfer-fund", "routing-number", "account-number", "recipient", "payee", "amount"},
        "text": {"account balance", "current balance", "available balance", "transfer funds", "routing number", "wire transfer", "statement", "make a payment"},
    }

    SAAS_SIGNALS = {
        "urls": {"dashboard", "project", "projects", "workspace", "settings", "team", "members", "organization", "table", "crud", "manage"},
        "selectors": {"create-btn", "edit-btn", "delete-btn", "save-btn", "filter-input", "status-select", "workspace-picker"},
        "text": {"dashboard", "new project", "create record", "edit record", "delete record", "workspace settings", "invite member", "manage team"},
    }

    BOOKING_SIGNALS = {
        "urls": {"book", "booking", "reservation", "reservations", "hotel", "flight", "room", "schedule", "calendar", "event"},
        "selectors": {"check-in", "check-out", "guests", "rooms", "calendar-picker", "seat-map", "reserve-btn"},
        "text": {"check-in", "check-out", "guests", "reservation", "book now", "select date", "departure", "arrival", "room selection"},
    }

    AUTH_SIGNALS = {
        "urls": {"login", "signin", "auth", "register", "signup", "forgot-password", "reset-password"},
        "selectors": {"user-name", "username", "password", "login-button", "signin-btn", "remember-me"},
        "text": {"username", "password", "login", "sign in", "sign up", "forgot password", "log in"},
    }

    def __init__(self, llm_provider: Optional[Any] = None):
        self.llm_provider = llm_provider

    def classify_from_investigation(self, report: Any) -> ApplicationIntelligenceModel:
        """
        Classifies archetype and builds the ApplicationIntelligenceModel from an InvestigationReport.
        """
        overview = getattr(report, "overview", None)
        target_url = getattr(overview, "target_url", "") if overview else ""
        if not target_url:
            target_url = getattr(report, "target_url", "https://example.com")

        if getattr(report, "observed_inventory", None):
            from app.intelligence.target_context import inventory_from_report
            inventory = inventory_from_report(report)
            journeys = getattr(report, "inferred_journeys", []) or []
            titles = [j.get("name") for j in journeys if isinstance(j, dict) and j.get("name")]
            rules = [getattr(r, "description", "") for r in (getattr(report, "business_rules_and_hypotheses", []) or [])]
            return self.classify_from_inventory(inventory, target_url, titles, [r for r in rules if r])

        # Aggregate evidence tokens from all investigation artifacts (legacy report shape)
        discovered_urls: List[str] = []
        controls: List[Dict[str, Any]] = []
        headings: List[str] = []
        body_snippets: List[str] = []
        evidence_ids: List[str] = []

        # 1. State inventory (mock, legacy, and real InvestigationReport)
        seen_urls: Set[str] = set()
        seen_sel: Set[str] = set()

        def add_url(u: str, h: str = ""):
            url_str = str(u or "").strip()
            if url_str and url_str not in seen_urls:
                seen_urls.add(url_str)
                discovered_urls.append(url_str)
            if h:
                headings.append(str(h))

        def add_ctrl(sel: str, act: str = "CLICK", txt: str = ""):
            s = str(sel or "").strip()
            if not s or s in seen_sel or s.startswith("http") or s.startswith("/"):
                return
            seen_sel.add(s)
            controls.append({"selector": s, "action_type": act.upper() if act else "CLICK", "text": txt or s})

        state_map = getattr(report, "state_map", None) or getattr(report, "states", [])
        if isinstance(state_map, dict):
            state_list = state_map.get("states", [])
        elif isinstance(state_list := getattr(state_map, "states", None), list):
            pass
        else:
            state_list = state_map if isinstance(state_map, list) else []

        for st in state_list:
            u = getattr(st, "url", "") or (st.get("url") if isinstance(st, dict) else "")
            h = getattr(st, "heading", "") or (st.get("heading") if isinstance(st, dict) else "")
            add_url(u, h)

        # Discovered Application Map from InvestigationReport
        for area in getattr(report, "discovered_application_map", []) or []:
            u = getattr(area, "route_or_url", "") or (area.get("route_or_url") if isinstance(area, dict) else "")
            p = getattr(area, "apparent_purpose", "") or (area.get("apparent_purpose") if isinstance(area, dict) else "")
            add_url(u, p)
            for ctrl_desc in (getattr(area, "relevant_controls_summary", []) or (area.get("relevant_controls_summary") if isinstance(area, dict) else [])):
                body_snippets.append(str(ctrl_desc))

        # Behavior Model state_classes and transitions
        bm = getattr(report, "behavior_model", None)
        if isinstance(bm, dict):
            for sc in bm.get("state_classes", {}).values():
                if isinstance(sc, dict):
                    add_url(sc.get("representative_route", ""), sc.get("primary_purpose", ""))
                    for kc in sc.get("key_controls", []):
                        add_ctrl(kc)
            for tr in bm.get("transitions", []):
                if isinstance(tr, dict):
                    add_ctrl(tr.get("target", ""), tr.get("action", "CLICK"))

        # 2. Controls inventory
        for ctrl in getattr(report, "controls", []) or getattr(report, "discovered_controls", []):
            if isinstance(ctrl, dict):
                add_ctrl(ctrl.get("selector", ""), ctrl.get("action_type", "CLICK"), ctrl.get("text", ""))
            else:
                add_ctrl(getattr(ctrl, "selector", ""), getattr(ctrl, "action_type", "CLICK"), getattr(ctrl, "text", ""))

        # User workflows from InvestigationReport
        for wf in getattr(report, "user_workflows", []) or []:
            t = getattr(wf, "target_element", "") or (wf.get("target_element") if isinstance(wf, dict) else "")
            act = getattr(wf, "action_taken", "") or (wf.get("action_taken") if isinstance(wf, dict) else "")
            add_ctrl(t, act)

        # Evidence records
        for ev in getattr(report, "evidence_and_traceability", []) or []:
            u = ev.get("url") if isinstance(ev, dict) else getattr(ev, "url", "")
            sel = ev.get("element_selector") if isinstance(ev, dict) else getattr(ev, "element_selector", "")
            if u:
                add_url(u)
            if sel:
                add_ctrl(sel)

        # Application understanding text
        app_und = getattr(report, "application_understanding", "")
        if app_und:
            body_snippets.append(str(app_und))

        # 3. Transitions
        transition_list = getattr(report, "transitions", []) or []
        for tr in transition_list:
            ev = getattr(tr, "evidence_id", "") or (tr.get("evidence_id") if isinstance(tr, dict) else "")
            if ev:
                evidence_ids.append(ev)

        # 4. Inferred journeys
        journeys = getattr(report, "inferred_journeys", []) or []
        workflow_titles: List[str] = []
        for j in journeys:
            name = j.get("name") if isinstance(j, dict) else getattr(j, "name", "")
            if name:
                workflow_titles.append(name)
            for ev in (j.get("evidence_refs", []) if isinstance(j, dict) else getattr(j, "evidence_refs", [])):
                evidence_ids.append(ev)

        # 5. Invariants and rules
        invariants: List[str] = []
        for r in (getattr(report, "business_rules_and_hypotheses", []) or []):
            desc = getattr(r, "description", "") or (r.get("description") if isinstance(r, dict) else "")
            if desc:
                invariants.append(desc)

        # 6. Uncertainties
        uncertainties: List[str] = []
        for hyp in (getattr(report, "unverified_hypotheses", []) or []):
            desc = getattr(hyp, "description", "") or (hyp.get("description") if isinstance(hyp, dict) else "")
            if desc:
                uncertainties.append(desc)

        return self.classify_from_signals(
            target_url=target_url,
            urls=discovered_urls,
            controls=controls,
            headings=headings,
            workflow_titles=workflow_titles,
            invariants=invariants,
            uncertainties=uncertainties,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            state_count=len(state_list),
            transition_count=len(transition_list),
        )

    def classify_from_signals(
        self,
        target_url: str,
        urls: List[str],
        controls: List[Dict[str, Any]],
        headings: List[str],
        workflow_titles: List[str] = None,
        invariants: List[str] = None,
        uncertainties: List[str] = None,
        evidence_ids: List[str] = None,
        state_count: int = 0,
        transition_count: int = 0,
    ) -> ApplicationIntelligenceModel:
        """
        Calculates archetype affinity scores and discovers capabilities from empirical signals.
        """
        workflow_titles = workflow_titles or []
        invariants = invariants or []
        uncertainties = uncertainties or []
        evidence_ids = evidence_ids or []

        # Token extraction
        all_url_tokens: Set[str] = set()
        for u in [target_url] + urls:
            parsed = urlparse(u)
            path_parts = re.split(r"[/._\-?=&]", parsed.path.lower() + " " + parsed.query.lower())
            all_url_tokens.update(p for p in path_parts if p)

        all_control_tokens: Set[str] = set()
        for c in controls:
            sel = (c.get("selector") or "").lower()
            txt = (c.get("text") or "").lower()
            all_control_tokens.update(re.split(r"[/._\-#\[\]= \"\':]", sel + " " + txt))

        all_text_tokens = " ".join([h.lower() for h in headings] + [w.lower() for w in workflow_titles] + invariants)

        # Compute raw scores for each domain archetype
        scores: Dict[WebsiteArchetype, float] = {
            WebsiteArchetype.ECOMMERCE: 0.0,
            WebsiteArchetype.BANKING_FINTECH: 0.0,
            WebsiteArchetype.SAAS_CRUD: 0.0,
            WebsiteArchetype.BOOKING_RESERVATIONS: 0.0,
            WebsiteArchetype.AUTHENTICATION: 0.0,
            WebsiteArchetype.GENERIC_WEB: 0.1,  # Baseline
        }

        # Full text and control strings for robust substring and token matching
        control_str = " ".join([(c.get("selector") or "") + " " + (c.get("text") or "") for c in controls]).lower()
        url_str = " ".join([target_url] + urls).lower()

        def _count_hits(signal_dict):
            u_hits = sum(1 for kw in signal_dict["urls"] if kw in url_str or kw in all_url_tokens)
            c_hits = sum(1 for kw in signal_dict["selectors"] if kw in control_str or kw in all_control_tokens)
            t_hits = sum(1 for kw in signal_dict["text"] if kw in all_text_tokens)
            return u_hits, c_hits, t_hits

        # 1. E-Commerce signals
        ecom_u, ecom_c, ecom_t = _count_hits(self.ECOMMERCE_SIGNALS)
        scores[WebsiteArchetype.ECOMMERCE] += (ecom_u * 1.5) + (ecom_c * 2.0) + (ecom_t * 1.0)

        # 2. Banking signals
        bank_u, bank_c, bank_t = _count_hits(self.BANKING_SIGNALS)
        scores[WebsiteArchetype.BANKING_FINTECH] += (bank_u * 2.0) + (bank_c * 2.5) + (bank_t * 1.2)

        # 3. SaaS CRUD signals
        saas_u, saas_c, saas_t = _count_hits(self.SAAS_SIGNALS)
        scores[WebsiteArchetype.SAAS_CRUD] += (saas_u * 1.5) + (saas_c * 1.8) + (saas_t * 1.0)

        # 4. Booking signals
        book_u, book_c, book_t = _count_hits(self.BOOKING_SIGNALS)
        scores[WebsiteArchetype.BOOKING_RESERVATIONS] += (book_u * 1.8) + (book_c * 2.0) + (book_t * 1.0)

        # 5. Authentication signals
        auth_u, auth_c, auth_t = _count_hits(self.AUTH_SIGNALS)
        scores[WebsiteArchetype.AUTHENTICATION] += (auth_u * 1.0) + (auth_c * 1.5) + (auth_t * 0.8)

        # Normalize confidence scores to sum or max bounds [0.0 - 1.0]
        max_score = max(scores.values()) if scores else 1.0
        normalized_confidences: Dict[WebsiteArchetype, float] = {}
        for arch, raw_val in scores.items():
            if max_score > 0:
                normalized_confidences[arch] = round(min(1.0, raw_val / max(max_score, 5.0)), 2)
            else:
                normalized_confidences[arch] = 0.0

        # Determine Primary and Secondary Archetypes
        sorted_archetypes = sorted(
            [a for a in WebsiteArchetype if a != WebsiteArchetype.GENERIC_WEB],
            key=lambda a: scores.get(a, 0.0),
            reverse=True,
        )

        top_archetype = sorted_archetypes[0]
        if scores[top_archetype] >= 2.5:
            primary_arch = top_archetype
        else:
            primary_arch = WebsiteArchetype.GENERIC_WEB

        secondary_archs: List[WebsiteArchetype] = []
        for cand in sorted_archetypes:
            if cand != primary_arch and scores[cand] >= 2.0:
                secondary_archs.append(cand)

        # Capability Detection
        capabilities = self._detect_capabilities(
            urls=urls + [target_url],
            all_url_tokens=all_url_tokens,
            all_control_tokens=all_control_tokens,
            all_text_tokens=all_text_tokens,
            primary=primary_arch,
        )

        # Extract domain entities
        entities = self._extract_entities(primary_arch, capabilities, all_text_tokens)

        # If invariants are empty, derive sensible default invariants from archetype
        if not invariants:
            invariants = self._derive_baseline_invariants(primary_arch, capabilities)

        return ApplicationIntelligenceModel(
            target_url=target_url,
            primary_archetype=primary_arch,
            secondary_archetypes=secondary_archs,
            confidence_scores=normalized_confidences,
            capabilities=capabilities,
            entities=entities,
            workflows=workflow_titles,
            invariants=invariants,
            state_transitions_count=transition_count,
            controls_discovered_count=len(controls),
            evidence_references=evidence_ids[:10],
            uncertainties=uncertainties,
            # Keyword signals only: weaker than structural evidence (see classify_from_inventory).
            capability_evidence={c.value: ["keyword signal in observed URLs/controls/headings"] for c in capabilities},
            capability_confidence={c.value: 0.5 for c in capabilities},
        )

    def _detect_capabilities(
        self,
        urls: List[str],
        all_url_tokens: Set[str],
        all_control_tokens: Set[str],
        all_text_tokens: str,
        primary: WebsiteArchetype,
    ) -> List[ApplicationCapability]:
        """Maps discovered tokens and controls to explicit application capabilities."""
        caps: Set[ApplicationCapability] = set()

        # Auth
        if any(tok in all_control_tokens or tok in all_url_tokens for tok in ("login", "username", "password", "user-name", "signin")):
            caps.add(ApplicationCapability.USER_AUTHENTICATION)

        # Catalog Browsing
        if any(tok in all_url_tokens or tok in all_control_tokens for tok in ("inventory", "products", "catalog", "items", "shop")):
            caps.add(ApplicationCapability.CATALOG_BROWSING)

        # Cart Management
        if any(tok in all_control_tokens or tok in all_url_tokens for tok in ("cart", "add-to-cart", "remove", "shopping-cart-badge", "shopping-cart-link")):
            caps.add(ApplicationCapability.CART_MANAGEMENT)

        # Checkout Flow
        if any(tok in all_control_tokens or tok in all_url_tokens for tok in ("checkout", "step-one", "step-two", "finish", "shipping", "subtotal", "tax")):
            caps.add(ApplicationCapability.CHECKOUT_FLOW)

        # Sorting & Filtering
        if any(tok in all_control_tokens for tok in ("sort", "product-sort", "sort-container", "filter", "search", "az", "za", "lohi", "hilo")):
            caps.add(ApplicationCapability.PAGINATION_SORTING)

        # Drawer Navigation
        if any(tok in all_control_tokens for tok in ("burger", "react-burger-menu-btn", "drawer", "sidebar", "menu-wrap")):
            caps.add(ApplicationCapability.DRAWER_NAVIGATION)

        # Form Validation
        if any(tok in all_control_tokens or tok in all_text_tokens for tok in ("error", "alert", "required", "validation", "invalid")):
            caps.add(ApplicationCapability.FORM_VALIDATION)

        # Profile Management
        if any(tok in all_url_tokens or tok in all_control_tokens for tok in ("profile", "account", "settings", "password-change")):
            caps.add(ApplicationCapability.PROFILE_MANAGEMENT)

        # Financial Transactions
        if primary == WebsiteArchetype.BANKING_FINTECH or any(tok in all_control_tokens for tok in ("transfer", "routing", "deposit", "wire")):
            caps.add(ApplicationCapability.FINANCIAL_TRANSACTION)

        # CRUD
        if primary == WebsiteArchetype.SAAS_CRUD or any(tok in all_control_tokens for tok in ("create", "edit", "delete", "destroy", "update", "new")):
            caps.add(ApplicationCapability.CRUD_OPERATIONS)

        # Multi-step Wizard
        if any("step" in u.lower() for u in urls) or any(tok in all_text_tokens for tok in ("step 1", "step 2", "next", "continue", "finish")):
            caps.add(ApplicationCapability.MULTI_STEP_WIZARD)

        return sorted(list(caps), key=lambda c: c.value)

    # ── Evidence-backed classification from a run's observed inventory ─────────────
    _SORT_WORDS = ("sort", "order by", "orderby", "order")
    _FILTER_WORDS = ("filter", "status", "priority", "category", "type", "tag")

    @staticmethod
    def _tokens(*texts: str) -> Set[str]:
        out: Set[str] = set()
        for t in texts:
            out.update(w for w in re.split(r"[^a-z0-9]+", (t or "").lower()) if w)
        return out

    def structural_capability_evidence(self, inventory: Any) -> Dict[ApplicationCapability, List[Tuple[str, float]]]:
        """Capabilities proven by structured observations (forms, fields, control types, routes)."""
        ev: Dict[ApplicationCapability, List[Tuple[str, float]]] = {}

        def add(cap: ApplicationCapability, why: str, conf: float) -> None:
            ev.setdefault(cap, []).append((why, conf))

        for form in getattr(inventory, "forms", []):
            if form.is_login_form:
                add(ApplicationCapability.USER_AUTHENTICATION, f"login form (1 password field) at {form.route}", 1.0)
            if any(f.required or f.maxlength or f.minlength or f.pattern or f.min or f.max for f in form.fields):
                add(ApplicationCapability.FORM_VALIDATION, f"constrained fields in form {form.form_selector} at {form.route}", 0.9)
            form_tokens = self._tokens(form.action, form.form_id, form.submit_selector, form.route)
            if form.method == "POST" and form_tokens & {"create", "new", "add", "edit", "update", "save"}:
                add(ApplicationCapability.CRUD_OPERATIONS, f"POST form {form.form_selector} ({form.action or form.route})", 0.8)
            if any(f.field_type == "search" or f.name.lower() in ("q", "query", "search") for f in form.fields):
                add(ApplicationCapability.ITEM_SEARCH_FILTER, f"search field in {form.form_selector} at {form.route}", 0.9)
            for f in form.fields:
                if f.field_type.startswith("select") and len(f.options) >= 2:
                    words = self._tokens(f.name, f.field_id, f.label)
                    if words & {"sort", "order", "orderby"}:
                        add(ApplicationCapability.PAGINATION_SORTING, f"sort select {f.selector} at {form.route}", 0.9)
                    elif form.method == "GET" or words & set(self._FILTER_WORDS):
                        add(ApplicationCapability.ITEM_SEARCH_FILTER, f"filter select {f.selector} at {form.route}", 0.8)
        if any(s.validation_message for s in getattr(inventory, "states", [])):
            add(ApplicationCapability.FORM_VALIDATION, "validation message observed", 1.0)

        for c in getattr(inventory, "controls", []):
            words = self._tokens(c.selector, c.name)
            if c.tag == "select" and len(c.options) >= 2 and words & {"sort", "order", "orderby"}:
                add(ApplicationCapability.PAGINATION_SORTING, f"sort control {c.selector} at {c.route}", 0.9)
            if c.control_type == "search":
                add(ApplicationCapability.ITEM_SEARCH_FILTER, f"search input {c.selector} at {c.route}", 0.9)
            if "cart" in words and (words & {"add", "remove"}):
                add(ApplicationCapability.CART_MANAGEMENT, f"cart control {c.selector} at {c.route}", 0.9)
            if "checkout" in words:
                add(ApplicationCapability.CHECKOUT_FLOW, f"checkout control {c.selector} at {c.route}", 0.8)
            if words & {"burger", "drawer", "hamburger"} or ("menu" in words and c.tag == "button"):
                add(ApplicationCapability.DRAWER_NAVIGATION, f"menu toggle {c.selector} at {c.route}", 0.7)
            if words & {"delete", "edit"} and c.tag in ("button", "a", "input"):
                add(ApplicationCapability.CRUD_OPERATIONS, f"record action {c.selector} at {c.route}", 0.6)
            if words & {"transfer", "payee", "deposit", "withdraw"}:
                add(ApplicationCapability.FINANCIAL_TRANSACTION, f"financial control {c.selector} at {c.route}", 0.8)
            if c.href and re.search(r"[?&]page=\d+", c.href):
                add(ApplicationCapability.PAGINATION_SORTING, f"pagination link {c.href} at {c.route}", 0.7)

        for s in getattr(inventory, "states", []):
            words = self._tokens(s.route)
            if words & {"cart"}:
                add(ApplicationCapability.CART_MANAGEMENT, f"cart route {s.route}", 0.8)
            if words & {"checkout"}:
                add(ApplicationCapability.CHECKOUT_FLOW, f"checkout route {s.route}", 0.8)
            if words & {"inventory", "products", "product", "catalog", "shop"}:
                add(ApplicationCapability.CATALOG_BROWSING, f"catalog route {s.route}", 0.7)
            if words & {"profile", "account", "settings"}:
                add(ApplicationCapability.PROFILE_MANAGEMENT, f"profile route {s.route}", 0.7)
            if words & {"step", "wizard"}:
                add(ApplicationCapability.MULTI_STEP_WIZARD, f"step route {s.route}", 0.6)
        return ev

    def classify_from_inventory(self, inventory: Any, target_url: str = "",
                                workflow_titles: Optional[List[str]] = None,
                                invariants: Optional[List[str]] = None) -> ApplicationIntelligenceModel:
        """
        Archetype affinity from observed signals; capabilities only from structural evidence.
        Keyword-only capability signals are kept as unknown capabilities, never as facts.
        """
        model = self.classify_from_signals(
            target_url=target_url or getattr(inventory, "origin", ""),
            urls=[s.url for s in inventory.states],
            controls=[{"selector": c.selector, "text": c.name} for c in inventory.controls],
            headings=[h for s in inventory.states for h in s.headings],
            workflow_titles=workflow_titles or [],
            invariants=invariants or [],
            state_count=len(inventory.states),
            transition_count=len(inventory.transitions),
        )
        structural = self.structural_capability_evidence(inventory)
        model.capability_evidence = {cap.value: [why for why, _ in items][:5] for cap, items in structural.items()}
        model.capability_confidence = {cap.value: round(max(c for _, c in items), 2) for cap, items in structural.items()}
        token_caps = set(model.capabilities)
        model.capabilities = sorted(structural.keys(), key=lambda c: c.value)
        model.unknown_capabilities = sorted(c.value for c in token_caps - set(structural.keys()))
        model.entities = self._extract_entities(model.primary_archetype, model.capabilities, "")
        if not invariants:
            model.invariants = []  # domain-default invariants are hypotheses, not observations
        return model

    def _extract_entities(
        self,
        primary: WebsiteArchetype,
        capabilities: List[ApplicationCapability],
        text_context: str,
    ) -> List[str]:
        """Extracts high-level domain entities based on archetype and capabilities."""
        entities: List[str] = []
        if ApplicationCapability.USER_AUTHENTICATION in capabilities:
            entities.append("UserSession")
        if primary == WebsiteArchetype.ECOMMERCE:
            entities.extend(["ProductItem", "ShoppingCart", "OrderSummary"])
            if ApplicationCapability.CHECKOUT_FLOW in capabilities:
                entities.extend(["ShippingAddress", "PaymentOverview"])
        elif primary == WebsiteArchetype.BANKING_FINTECH:
            entities.extend(["BankAccount", "TransactionRecord", "FundTransfer"])
        elif primary == WebsiteArchetype.SAAS_CRUD:
            entities.extend(["Workspace", "ProjectRecord", "OrganizationMember"])
        elif primary == WebsiteArchetype.BOOKING_RESERVATIONS:
            entities.extend(["ReservationSlot", "GuestProfile", "BookingConfirmation"])
        else:
            entities.extend(["WebResource", "FormSubmission"])
        return entities

    def _derive_baseline_invariants(
        self,
        primary: WebsiteArchetype,
        capabilities: List[ApplicationCapability],
    ) -> List[str]:
        """Derives core domain invariants to be validated during test execution."""
        invariants: List[str] = []
        if ApplicationCapability.USER_AUTHENTICATION in capabilities:
            invariants.append("Unauthenticated requests to protected member routes must redirect to login.")
            invariants.append("Empty credentials must display explicit validation error without page crash.")
        if primary == WebsiteArchetype.ECOMMERCE:
            invariants.append("Adding an item to the shopping cart must increment the visual badge count.")
            invariants.append("Removing an item from the shopping cart must decrement the badge count.")
            invariants.append("Cart contents and badge counts must persist across in-app navigations.")
            if ApplicationCapability.CHECKOUT_FLOW in capabilities:
                invariants.append("Checkout form submission requires all mandatory contact/address fields.")
                invariants.append("Order total must equal Item Total plus calculated Tax without NaN anomalies.")
        elif primary == WebsiteArchetype.BANKING_FINTECH:
            invariants.append("Transfers exceeding current available balance must be rejected.")
            invariants.append("Account numbers and sensitive financial tokens must not leak in client logs.")
        elif primary == WebsiteArchetype.SAAS_CRUD:
            invariants.append("Deleting a record must require confirmation or provide undo semantics.")
            invariants.append("Editing a record must persist updated attributes upon refresh.")
        return invariants
