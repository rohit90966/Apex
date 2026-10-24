import uuid
from typing import Any, List, Optional
from app.schemas import (
    PageCategory,
    PageClassification,
    TestCase,
    TestPlan,
    InspectionResult,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
    PlanningMode,
)
from app.evidence.derivation.boundary_value import BoundaryValueEngine
from app.evidence.models import OracleClass


class DeterministicPlanner:
    """
    Generates structured, reproducible test plans without an LLM.
    Uses reusable, page-specific QA patterns and deterministic boundary-value derivation
    grounded strictly in observed inspection evidence.
    """

    @staticmethod
    def generate_plan(
        inspection: InspectionResult,
        classification: PageClassification,
        requirements: Optional[List[str]] = None,
        description: Optional[str] = None,
        max_cases: int = 15,
        planning_mode: PlanningMode = PlanningMode.RULE_BASED_FALLBACK,
        warning_reason: Optional[str] = None,
        features: Optional[List[Any]] = None,
    ) -> TestPlan:
        cases: List[TestCase] = []
        assumptions: List[str] = []
        uncertainties: List[str] = []
        warnings: List[str] = []

        if warning_reason:
            warnings.append(warning_reason)

        category = classification.page_category
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        # Derive boundary value test cases from observed HTML constraints
        fields_to_derive = []
        for form in inspection.forms:
            fields_to_derive.extend(form.fields)

        ev_records, test_ideas = BoundaryValueEngine.derive_from_inspection(
            fields=fields_to_derive,
            page_url=inspection.target_url,
            page_id=inspection.inspection_id,
        )

        for idea in test_ideas:
            if len(cases) >= max_cases:
                break
            cases.append(
                TestCase(
                    test_id=idea.idea_id,
                    name=idea.name,
                    objective=idea.objective,
                    page_category=category,
                    relevant_elements=[inspection.target_url],
                    action_description=idea.action_description,
                    expected_outcome=idea.expected_outcome,
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["DOM constraint state", "Form submission validation state"],
                    priority=Priority(idea.priority) if idea.priority in Priority.__members__ else Priority.MEDIUM,
                    preconditions=["DOM loaded and interactive"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason=f"Derived deterministically from rule '{idea.rule_id}'",
                    confidence=0.98,
                    status=TestCaseStatus.PROPOSED,
                    evidence_ids=idea.evidence_ids,
                    rule_ids=[idea.rule_id],
                    oracle_class=idea.oracle_class.value if hasattr(idea.oracle_class, "value") else str(idea.oracle_class),
                    derived_values=idea.derived_values,
                )
            )

        # Requirement-driven tests
        if requirements:
            for i, req in enumerate(requirements):
                if len(cases) >= max_cases:
                    break
                cases.append(
                    TestCase(
                        test_id=f"TC-REQ-{i+1:02d}",
                        name=f"Verify explicit requirement: {req[:40]}...",
                        objective=f"Ensure application adheres to user-specified requirement: '{req}'",
                        page_category=category,
                        relevant_elements=[btn for btn in inspection.buttons[:2]],
                        action_description=f"Validate functional flow according to requirement: {req}",
                        expected_outcome="System behaves exactly as specified in user requirement.",
                        expected_behavior_source=ExpectedBehaviorSource.REQUIREMENT,
                        evidence_to_collect=["DOM state", "Network traffic", "User feedback/alert message"],
                        priority=Priority.HIGH,
                        preconditions=["Page is loaded"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason="Directly derived from user-provided testing requirements.",
                        confidence=0.90,
                        status=TestCaseStatus.PROPOSED,
                    )
                )

        # 1. LOGIN Patterns
        if category == PageCategory.LOGIN:
            assumptions.append("Authentication endpoint expects standard credential combination.")
            uncertainties.append("Valid credential pair is unknown without test account provisioning.")

            cases.append(
                TestCase(
                    test_id=f"TC-LOGIN-{len(cases)+1:02d}",
                    name="Validate login form element visibility and accessibility",
                    objective="Ensure required login input fields and submission controls are properly rendered and accessible.",
                    page_category=category,
                    relevant_elements=[f.name or f.input_type or "input" for form in inspection.forms for f in form.fields],
                    action_description="Check that username/email and password fields are present and labelled.",
                    expected_outcome="All expected login form controls are visible and have accessible names.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["Element accessibility tree", "Computed styles", "Form DOM"],
                    priority=Priority.HIGH,
                    preconditions=["User navigates to login page"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Observed login form elements on inspected page.",
                    confidence=0.95,
                    status=TestCaseStatus.PROPOSED,
                )
            )

            # Password masking
            has_pw = any(
                (f.input_type == "password" or "password" in (f.name or "").lower())
                for form in inspection.forms for f in form.fields
            )
            if has_pw:
                cases.append(
                    TestCase(
                        test_id=f"TC-LOGIN-{len(cases)+1:02d}",
                        name="Verify password field masking invariant",
                        objective="Ensure sensitive credentials entered in the password field are masked by default.",
                        page_category=category,
                        relevant_elements=["input[type=password]"],
                        action_description="Inspect password field type attribute and visual presentation.",
                        expected_outcome="Field type attribute is 'password' and characters are obscured.",
                        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                        evidence_to_collect=["Input type attribute", "Computed text visibility"],
                        priority=Priority.HIGH,
                        preconditions=["Login form rendered"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason="Identified password input on page requiring security masking invariant.",
                        confidence=0.95,
                        status=TestCaseStatus.PROPOSED,
                    )
                )

            # Empty submission
            cases.append(
                TestCase(
                    test_id=f"TC-LOGIN-{len(cases)+1:02d}",
                    name="Verify empty submission client-side validation",
                    objective="Ensure submitting empty login credentials prevents unhandled submission and indicates error.",
                    page_category=category,
                    relevant_elements=inspection.buttons[:1] or ["form[submit]"],
                    action_description="Attempt submission with blank username and password.",
                    expected_outcome="Client-side validation highlights missing fields without submitting invalid network request.",
                    expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                    evidence_to_collect=["HTML5 validation state", "Error message elements", "Network tab abort/absence"],
                    priority=Priority.MEDIUM,
                    preconditions=["Empty form state"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Standard defensive validation pattern for login interfaces.",
                    confidence=0.85,
                    status=TestCaseStatus.PROPOSED,
                )
            )

            # Password minimum length policy (only when minlength attribute is actually observed in DOM)
            has_pw_minlen = any(
                f.minlength is not None
                for form in inspection.forms for f in form.fields
                if f.input_type == "password" or "password" in (f.name or "").lower()
            )
            if has_pw_minlen:
                cases.append(
                    TestCase(
                        test_id=f"TC-LOGIN-{len(cases)+1:02d}",
                        name="Verify password minimum length policy enforcement",
                        objective="Ensure entering a password below the minimum length threshold is rejected with a clear length validation error.",
                        page_category=category,
                        relevant_elements=["input[type=password]"],
                        action_description="Enter valid username and single character password ('a') to verify minimum length policy rejection.",
                        expected_outcome="Validation explicitly rejects short password with clear length error message.",
                        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                        evidence_to_collect=["Validation error element text", "Form submit disabled state"],
                        priority=Priority.HIGH,
                        preconditions=["Login form rendered"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason="Observed password input requiring minimum length boundary enforcement.",
                        confidence=0.92,
                        status=TestCaseStatus.PROPOSED,
                    )
                )
            else:
                cases.append(
                    TestCase(
                        test_id=f"TC-LOGIN-{len(cases)+1:02d}",
                        name="Verify login with whitespace or missing password handling",
                        objective="Ensure submitting username with blank or whitespace-only password does not bypass client validation.",
                        page_category=category,
                        relevant_elements=[f.name or "field" for form in inspection.forms for f in form.fields],
                        action_description="Enter valid username format and leave password empty or whitespace, then attempt submit.",
                        expected_outcome="Client validation blocks submission or indicates password required without navigating away.",
                        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                        evidence_to_collect=["DOM error message state", "URL stability"],
                        priority=Priority.HIGH,
                        preconditions=["Login form rendered"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason="Negative input validation pattern for authentication interface without HTML length constraint.",
                        confidence=0.88,
                        status=TestCaseStatus.PROPOSED,
                    )
                )

        # 2. REGISTRATION Patterns
        elif category == PageCategory.REGISTRATION:
            assumptions.append("Registration requires unique credentials not currently registered.")
            uncertainties.append("Password complexity policy details are not fully visible from static attributes.")

            cases.append(
                TestCase(
                    test_id=f"TC-REG-{len(cases)+1:02d}",
                    name="Verify registration form fields and required attributes",
                    objective="Ensure required registration inputs (email, username, password) contain proper validation constraints.",
                    page_category=category,
                    relevant_elements=[f.name or "field" for form in inspection.forms for f in form.fields],
                    action_description="Examine presence of required attribute or aria-required markings on key inputs.",
                    expected_outcome="All mandatory fields enforce completion before registration request dispatch.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["Input required attributes", "Accessibility aria-required flags"],
                    priority=Priority.HIGH,
                    preconditions=["Registration page loaded"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Registration forms must defensively validate completeness.",
                    confidence=0.90,
                    status=TestCaseStatus.PROPOSED,
                )
            )

            # Confirmation field
            pw_fields = [
                f for form in inspection.forms for f in form.fields
                if f.input_type == "password" or "password" in (f.name or "").lower()
            ]
            if len(pw_fields) > 1:
                cases.append(
                    TestCase(
                        test_id=f"TC-REG-{len(cases)+1:02d}",
                        name="Verify password confirmation mismatch validation",
                        objective="Ensure entering mismatched passwords in primary and confirmation fields triggers immediate validation error.",
                        page_category=category,
                        relevant_elements=[f.name or "password" for f in pw_fields],
                        action_description="Enter differing strings in password and confirm password fields.",
                        expected_outcome="System indicates password mismatch and blocks account creation.",
                        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                        evidence_to_collect=["Validation error element text", "Form submit disabled state"],
                        priority=Priority.HIGH,
                        preconditions=["Registration form filled with differing passwords"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason="Observed multiple password fields indicative of confirmation pairing.",
                        confidence=0.95,
                        status=TestCaseStatus.PROPOSED,
                    )
                )

            # Terms of service acceptance
            cases.append(
                TestCase(
                    test_id=f"TC-REG-{len(cases)+1:02d}",
                    name="Verify mandatory Terms of Service acceptance enforcement",
                    objective="Ensure registration attempt without accepting terms of service is rejected and prevents account creation.",
                    page_category=category,
                    relevant_elements=["input#terms", "input[name=terms]"],
                    action_description="Complete registration details without checking terms checkbox, then submit registration form.",
                    expected_outcome="Server and client block registration, displaying mandatory terms acceptance validation requirement.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["Validation error text", "Account creation prevention confirmation"],
                    priority=Priority.HIGH,
                    preconditions=["Registration form loaded"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Identified mandatory terms of service agreement control.",
                    confidence=0.95,
                    status=TestCaseStatus.PROPOSED,
                )
            )

        # 3. SEARCH Patterns
        elif category == PageCategory.SEARCH:
            cases.append(
                TestCase(
                    test_id=f"TC-SEARCH-{len(cases)+1:02d}",
                    name="Verify search input responsiveness and query parameter mapping",
                    objective="Ensure search input accepts alphanumeric characters and submits query to results handler.",
                    page_category=category,
                    relevant_elements=[f.name or "search" for form in inspection.forms for f in form.fields if "search" in (f.name or "").lower()] or ["input[type=search]"],
                    action_description="Type standard alphanumeric query string and initiate search.",
                    expected_outcome="Query string is reflected in results header or request query parameters without error.",
                    expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                    evidence_to_collect=["URL parameters", "Search result container heading", "HTTP status"],
                    priority=Priority.HIGH,
                    preconditions=["Search input visible and focused"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Observed explicit search controls on page.",
                    confidence=0.90,
                    status=TestCaseStatus.PROPOSED,
                )
            )
            cases.append(
                TestCase(
                    test_id=f"TC-SEARCH-{len(cases)+1:02d}",
                    name="Verify empty and whitespace search query input validation",
                    objective="Ensure executing a search with empty or whitespace input requires query input and prompts for search terms.",
                    page_category=category,
                    relevant_elements=["search input", "submit button"],
                    action_description="Submit search form with whitespace or empty input.",
                    expected_outcome="Application prompts user with clear message requiring search terms and does not accept blank query as valid search.",
                    expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                    evidence_to_collect=["Empty state message", "Validation prompt element text"],
                    priority=Priority.MEDIUM,
                    preconditions=["Search input accessible"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Boundary case for observed search functionality.",
                    confidence=0.88,
                    status=TestCaseStatus.PROPOSED,
                )
            )

        # 4. CONTACT_FORM Patterns
        elif category == PageCategory.CONTACT_FORM:
            cases.append(
                TestCase(
                    test_id=f"TC-CONTACT-{len(cases)+1:02d}",
                    name="Verify mandatory contact fields validation",
                    objective="Ensure contact form requires message body and valid return contact email.",
                    page_category=category,
                    relevant_elements=[f.name or "field" for form in inspection.forms for f in form.fields],
                    action_description="Attempt submission missing message or sender email.",
                    expected_outcome="Validation alerts user to complete required contact details.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["Validation feedback tooltip/banner", "Blocked network dispatch"],
                    priority=Priority.HIGH,
                    preconditions=["Blank contact form"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Identified contact inquiry form with submission controls.",
                    confidence=0.90,
                    status=TestCaseStatus.PROPOSED,
                )
            )

        # Baseline GENERAL / INVARIANT Patterns
        if len(cases) < max_cases:
            cases.append(
                TestCase(
                    test_id=f"TC-GEN-{len(cases)+1:02d}",
                    name="Verify page load integrity and absence of console errors",
                    objective="Ensure the page renders completely without uncaught JavaScript exceptions or broken resource links.",
                    page_category=category,
                    relevant_elements=["document body", "main headings"],
                    action_description="Navigate to page and monitor browser console and network response codes.",
                    expected_outcome="HTTP status is 200, main content is rendered, and no severe console errors are logged.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["HTTP status code", "Console error log", "Screenshot"],
                    priority=Priority.HIGH,
                    preconditions=["Browser navigation to target URL"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Universal quality assurance baseline invariant.",
                    confidence=0.98,
                    status=TestCaseStatus.PROPOSED,
                )
            )

        # Accessible controls invariant
        if inspection.buttons and len(cases) < max_cases:
            cases.append(
                TestCase(
                    test_id=f"TC-GEN-{len(cases)+1:02d}",
                    name="Verify interactive controls have accessible names",
                    objective="Ensure buttons and links have accessible text labels for assistive technologies.",
                    page_category=category,
                    relevant_elements=inspection.buttons[:5],
                    action_description="Inspect accessibility tree and aria attributes of buttons.",
                    expected_outcome="Interactive buttons have non-empty accessible name and compliant contrast.",
                    expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                    evidence_to_collect=["Accessibility tree names", "Aria-label attributes"],
                    priority=Priority.MEDIUM,
                    preconditions=["DOM loaded"],
                    requires_test_account=False,
                    safe_for_future_automation=True,
                    selection_reason="Observed interactive button elements on page.",
                    confidence=0.90,
                    status=TestCaseStatus.PROPOSED,
                )
            )

        # Feature-driven test case derivation
        if features:
            for feat in features:
                feat_name = getattr(feat, "feature_name", None) or getattr(feat, "name", None) or str(feat)
                feat_type = getattr(feat, "feature_type", None) or getattr(feat, "category", "feature")
                feat_elements = getattr(feat, "target_elements", []) or getattr(feat, "elements", [])

                cases.append(
                    TestCase(
                        test_id=f"TC-FEAT-{len(cases)+1:02d}",
                        name=f"Verify feature functionality: {feat_name}",
                        objective=f"Ensure discovered feature '{feat_name}' ({feat_type}) is interactive and satisfies standard UI expectations.",
                        page_category=category,
                        relevant_elements=feat_elements[:3] if feat_elements else [inspection.target_url],
                        action_description=f"Interact with controls associated with feature '{feat_name}' and verify response.",
                        expected_outcome=f"Feature '{feat_name}' operates reliably without unhandled DOM or console exceptions.",
                        expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                        evidence_to_collect=["DOM state", "Console logs", "UI response"],
                        priority=Priority.MEDIUM,
                        preconditions=["Page is loaded and target feature is rendered"],
                        requires_test_account=False,
                        safe_for_future_automation=True,
                        selection_reason=f"Derived directly from discovered application feature: {feat_name}",
                        confidence=0.92,
                        status=TestCaseStatus.PROPOSED,
                    )
                )

        # Semantic Deduplication BEFORE enforcing max_cases
        seen_keys = set()
        seen_ids = set()
        deduped_cases = []

        def get_semantic_key(tc: TestCase) -> str:
            norm_name = "".join(ch for ch in tc.name.lower() if ch.isalnum() or ch.isspace()).strip()
            norm_obj = "".join(ch for ch in tc.objective.lower() if ch.isalnum() or ch.isspace()).strip()
            rules = ",".join(sorted(tc.rule_ids or []))
            elems = ",".join(sorted(tc.relevant_elements or []))
            if rules and elems:
                return f"rule:{rules}|elems:{elems}"
            return f"name:{norm_name}|obj:{norm_obj}"

        for c in cases:
            sem_key = get_semantic_key(c)
            if sem_key in seen_keys:
                continue
            seen_keys.add(sem_key)

            # Ensure test_id uniqueness
            unique_id = c.test_id
            counter = 1
            while unique_id in seen_ids:
                counter += 1
                unique_id = f"{c.test_id}-{counter}"
            c.test_id = unique_id
            seen_ids.add(unique_id)

            deduped_cases.append(c)

        # Enforce maximum test cases
        final_cases = deduped_cases[:max_cases]

        summary = (
            f"Generated {len(final_cases)} deterministic test cases using verified QA patterns for "
            f"category '{category.value}' grounded in observed DOM evidence. "
            f"Derived {len(test_ideas)} boundary value test idea(s) from {len(ev_records)} constraint record(s)."
        )

        return TestPlan(
            plan_id=plan_id,
            inspection_id=inspection.inspection_id,
            page_classification=classification,
            test_cases=final_cases,
            planning_mode=planning_mode,
            assumptions=assumptions,
            missing_requirements_or_uncertainties=uncertainties,
            planning_summary=summary,
            warnings=warnings,
            evidence_ledger_count=len(ev_records),
            deterministic_test_count=len(test_ideas),
            unknowns_count=0,
        )
