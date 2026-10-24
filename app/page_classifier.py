import re
from typing import List, Tuple
from app.schemas import (
    PageCategory,
    PageClassification,
    InspectionResult,
)


class DeterministicClassifier:
    """
    Deterministic rule-based page classifier based on inspection evidence.
    Does not use an LLM to guarantee explainable and reproducible classification.
    """

    @staticmethod
    def classify(inspection: InspectionResult) -> PageClassification:
        evidence: List[str] = []
        score_map = {
            PageCategory.LOGIN: 0,
            PageCategory.REGISTRATION: 0,
            PageCategory.SEARCH: 0,
            PageCategory.CONTACT_FORM: 0,
            PageCategory.GENERAL: 1,  # Baseline general score
        }

        # Analyze Title & Headings
        text_corpus = " ".join([inspection.page_title] + inspection.headings).lower()

        # Check forms and input types
        has_password_field = False
        password_count = 0
        has_email_field = False
        has_search_field = False
        has_textarea = False
        has_submit_button = False

        for form in inspection.forms:
            for field in form.fields:
                itype = (field.input_type or "").lower()
                name = (field.name or "").lower()
                placeholder = (field.placeholder or "").lower()
                label = (field.label or "").lower()

                combo = f"{name} {placeholder} {label}"

                if itype == "password" or "password" in combo:
                    has_password_field = True
                    password_count += 1
                if itype == "email" or "email" in combo:
                    has_email_field = True
                if itype == "search" or "search" in combo or "query" in combo:
                    has_search_field = True
                if itype == "textarea" or "message" in combo or "comment" in combo:
                    has_textarea = True

        # Check buttons
        for btn in inspection.buttons:
            b_text = btn.lower()
            if any(term in b_text for term in ["login", "sign in", "log in"]):
                score_map[PageCategory.LOGIN] += 3
                evidence.append(f"Observed login button: '{btn}'")
            elif any(term in b_text for term in ["register", "sign up", "create account"]):
                score_map[PageCategory.REGISTRATION] += 3
                evidence.append(f"Observed registration button: '{btn}'")
            elif any(term in b_text for term in ["search", "find"]):
                score_map[PageCategory.SEARCH] += 2
                evidence.append(f"Observed search button: '{btn}'")
            elif any(term in b_text for term in ["send message", "contact us", "submit enquiry"]):
                score_map[PageCategory.CONTACT_FORM] += 3
                evidence.append(f"Observed contact submit button: '{btn}'")

        # Evaluate Password Fields
        if password_count >= 2:
            score_map[PageCategory.REGISTRATION] += 5
            evidence.append(f"Observed multiple password fields ({password_count}) indicating confirmation")
        elif password_count == 1:
            score_map[PageCategory.LOGIN] += 4
            evidence.append("Observed single password field typical of authentication")

        if has_email_field:
            score_map[PageCategory.LOGIN] += 2
            score_map[PageCategory.REGISTRATION] += 2
            score_map[PageCategory.CONTACT_FORM] += 2
            evidence.append("Observed email input field")

        if has_search_field:
            score_map[PageCategory.SEARCH] += 5
            evidence.append("Observed explicit search input field")

        if has_textarea:
            score_map[PageCategory.CONTACT_FORM] += 4
            evidence.append("Observed message/textarea input field")

        # Title / heading clues
        if re.search(r"\b(log\s*in|sign\s*in)\b", text_corpus):
            score_map[PageCategory.LOGIN] += 4
            evidence.append("Page title/heading explicitly mentions Log In / Sign In")

        if re.search(r"\b(register|sign\s*up|create account|registration)\b", text_corpus):
            score_map[PageCategory.REGISTRATION] += 4
            evidence.append("Page title/heading explicitly mentions Registration / Sign Up")

        if re.search(r"\b(search|find|results|catalog)\b", text_corpus):
            score_map[PageCategory.SEARCH] += 3
            evidence.append("Page title/heading mentions search functionality")

        if re.search(r"\b(contact|reach us|get in touch|feedback)\b", text_corpus):
            score_map[PageCategory.CONTACT_FORM] += 4
            evidence.append("Page title/heading indicates contact form")

        # Determine winner
        best_category = max(score_map, key=score_map.get)
        top_score = score_map[best_category]

        # Calculate confidence
        if top_score < 3:
            if len(inspection.links) > 2 or len(inspection.headings) > 1:
                category = PageCategory.GENERAL
                confidence = 0.60
                summary = "Page exhibits general content with navigation and informative headings."
                evidence.append("Broad navigation links and heading layout present.")
            else:
                category = PageCategory.UNKNOWN
                confidence = 0.35
                summary = "Insufficient specific structural evidence to classify page with high confidence."
        else:
            category = best_category
            # normalize confidence up to 0.95
            confidence = min(0.95, round(0.50 + (top_score * 0.05), 2))
            summary = f"Identified as {category.value} with confidence {confidence} based on structural form patterns and element semantics."

        return PageClassification(
            page_category=category,
            confidence=confidence,
            evidence=evidence,
            reasoning_summary=summary,
        )
