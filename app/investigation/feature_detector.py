import re
from typing import List, Dict, Any
from app.investigation.models import PageEvidence, ObservedFeature


class FeatureDetector:
    """
    Hybrid feature detector combining deterministic structural rules
    and semantic heuristics to identify application capabilities across diverse web domains.
    """

    @classmethod
    def detect_features(cls, page: PageEvidence) -> List[ObservedFeature]:
        features: List[ObservedFeature] = []
        text_corpus = (
            f"{page.page_title} {' '.join(page.headings)} {page.visible_text_snippet}"
        ).lower()
        buttons_str = " ".join([b.lower() for b in page.buttons])

        # 1. Authentication (Login) Feature
        has_pw = False
        pw_count = 0
        has_email = False
        for f in page.forms:
            for fld in f.fields:
                t = (fld.input_type or "").lower()
                n = (fld.name or "").lower()
                if t == "password" or "password" in n:
                    has_pw = True
                    pw_count += 1
                if t == "email" or "email" in n:
                    has_email = True

        if has_pw and pw_count == 1:
            features.append(
                ObservedFeature(
                    feature_id=f"feat-login-{page.page_id}",
                    name="User Authentication & Login",
                    category="auth",
                    description="Standard credential entry and authentication submit control.",
                    confidence=0.92,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["MFA requirement", "Session duration limit", "Lockout policy"],
                )
            )

        # 2. Registration Feature
        if pw_count >= 2 or ("register" in text_corpus or "sign up" in text_corpus or "create account" in text_corpus):
            if has_email or pw_count >= 1:
                features.append(
                    ObservedFeature(
                        feature_id=f"feat-reg-{page.page_id}",
                        name="New User Registration",
                        category="registration",
                        description="Account registration with credential creation and profile validation.",
                        confidence=0.88,
                        associated_urls=[page.url],
                        supporting_evidence_ids=[page.page_id],
                        provenance="OBSERVED",
                        known_unknowns=["Password complexity requirements", "Email verification step"],
                    )
                )

        # 3. Search & Query Feature
        has_search_field = False
        for f in page.forms:
            for fld in f.fields:
                if (fld.input_type or "").lower() == "search" or "search" in (fld.name or "").lower() or "q" == (fld.name or "").lower():
                    has_search_field = True
                    break

        if has_search_field or "search" in buttons_str:
            features.append(
                ObservedFeature(
                    feature_id=f"feat-search-{page.page_id}",
                    name="Catalog & Content Search",
                    category="search",
                    description="Input query interface with submission to search handler.",
                    confidence=0.90,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["Faceted filtering rules", "Zero-results fallback behavior"],
                )
            )

        # 4. E-Commerce / Product & Cart UI
        ecom_signals = ["cart", "checkout", "add to cart", "buy now", "price", "subtotal", "product", "sku", "$", "₹", "€"]
        matched_ecom = [sig for sig in ecom_signals if sig in text_corpus or sig in buttons_str]
        if len(matched_ecom) >= 2 or any(term in buttons_str for term in ["add to cart", "checkout", "buy now"]):
            features.append(
                ObservedFeature(
                    feature_id=f"feat-ecom-{page.page_id}",
                    name="E-Commerce Product & Order UI",
                    category="ecommerce",
                    description="Product presentation, pricing indicators, or shopping cart controls.",
                    confidence=0.82,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["Inventory stock enforcement", "Payment gateway integration", "Tax calculation"],
                )
            )

        # 5. Booking & Date Selection
        booking_signals = ["book now", "reservation", "check-in", "check-out", "departure", "arrival", "guest", "room"]
        if any(sig in text_corpus for sig in booking_signals):
            features.append(
                ObservedFeature(
                    feature_id=f"feat-book-{page.page_id}",
                    name="Booking & Reservation Workflow",
                    category="booking",
                    description="Date-constrained scheduling, reservation, or stay booking controls.",
                    confidence=0.80,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["Availability calendar integration", "Cancellation policy"],
                )
            )

        # 6. Banking & Financial Portal
        fin_signals = ["transfer", "account balance", "routing number", "iban", "swift", "deposit", "withdraw", "statement"]
        if any(sig in text_corpus for sig in fin_signals):
            features.append(
                ObservedFeature(
                    feature_id=f"feat-fin-{page.page_id}",
                    name="Financial & Account Management",
                    category="banking",
                    description="Account balances, fund transfer forms, or transactional statements.",
                    confidence=0.84,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["Transaction limits", "Authorization tokens / OTP requirements"],
                )
            )

        # 7. Tables, Dashboards & Pagination
        if page.has_tables or page.has_pagination or "table" in text_corpus or "dashboard" in text_corpus:
            features.append(
                ObservedFeature(
                    feature_id=f"feat-data-{page.page_id}",
                    name="Data Table & Dashboard Presentation",
                    category="table_dashboard",
                    description="Structured data presentation tables, metrics, and pagination controls.",
                    confidence=0.85,
                    associated_urls=[page.url],
                    supporting_evidence_ids=[page.page_id],
                    provenance="OBSERVED",
                    known_unknowns=["Sorting persistence across pages", "Export data limits"],
                )
            )

        # 8. Navigation & Accessible Controls
        features.append(
            ObservedFeature(
                feature_id=f"feat-nav-{page.page_id}",
                name="Accessible Navigation & Layout Structure",
                category="navigation",
                description="Landmark headers, navigation links, and accessible interactive elements.",
                confidence=0.95,
                associated_urls=[page.url],
                supporting_evidence_ids=[page.page_id],
                provenance="OBSERVED",
                known_unknowns=["Responsive breakpoint compliance", "Screen reader ARIA parity"],
            )
        )

        return features
