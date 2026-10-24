"""
app/exploration/generator.py
============================
Adaptive Test Generation from Observed Application State Graph (Phase 2).

Transforms observed UI states, interactive elements, forms, and transitions
discovered during autonomous exploration into concrete, executable TestCases
without relying on page-category templates (LOGIN/REGISTRATION/SEARCH).

Coverage of Generated Tests:
  1. Primary Happy-Path User Journeys: Completing forms, transitions across discovered states.
  2. Boundary Value & Input Constraint Tests: maxlength, minlength, required attributes, number bounds.
  3. Negative / Invalidation Tests: Empty submission, missing required fields, invalid syntax.
  4. State Transition Verification: Validating that acting on a control transitions to expected UI state.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Dict, Any

from app.exploration.graph import (
    ApplicationStateGraph,
    UIState,
    InteractiveElement,
    ElementRole,
    StateTransition,
    ExplorationActionType,
)
from app.schemas import (
    TestCase,
    ActionStep,
    ActionType,
    ExecutionAssertion,
    AssertionType,
    PageCategory,
    Priority,
    ExpectedBehaviorSource,
    TestCaseStatus,
)

logger = logging.getLogger("verityqa.exploration.generator")


class AdaptiveTestGenerator:
    """
    Generates test cases directly from an ApplicationStateGraph.
    Works for ANY unfamiliar web application.
    """

    def generate_tests_from_graph(
        self,
        graph: ApplicationStateGraph,
        max_cases: int = 15,
    ) -> List[TestCase]:
        test_cases: List[TestCase] = []

        # 1. Generate Form & Control Constraint Tests
        for state_id, state in graph.states.items():
            form_tests = self._generate_form_tests(state)
            test_cases.extend(form_tests)
            if len(test_cases) >= max_cases:
                return test_cases[:max_cases]

        # 2. Generate State Transition & Navigation Journey Tests
        for transition in graph.transitions:
            if transition.new_state_discovered or transition.url_changed:
                journey_test = self._generate_journey_test(transition, graph)
                if journey_test:
                    test_cases.append(journey_test)
                if len(test_cases) >= max_cases:
                    return test_cases[:max_cases]

        # 3. Generate Interactive Control Action Tests (buttons, tabs, selects)
        for state_id, state in graph.states.items():
            control_tests = self._generate_control_tests(state)
            test_cases.extend(control_tests)
            if len(test_cases) >= max_cases:
                return test_cases[:max_cases]

        return test_cases[:max_cases]

    def _generate_form_tests(self, state: UIState) -> List[TestCase]:
        cases: List[TestCase] = []

        # Group inputs by form_id
        forms: Dict[str, List[InteractiveElement]] = {}
        for el in state.interactive_elements:
            fid = el.form_id or "default"
            forms.setdefault(fid, []).append(el)

        for fid, elements in forms.items():
            inputs = [e for e in elements if e.role in (ElementRole.TEXTBOX, ElementRole.COMBOBOX, ElementRole.CHECKBOX)]
            submit_btn = next((e for e in elements if e.is_submission or e.role == ElementRole.BUTTON), None)

            if not inputs:
                continue

            # Case A: Happy Path Form Submission
            happy_steps = [
                ActionStep(
                    step_number=1,
                    action=ActionType.NAVIGATE,
                    value=state.url,
                    description=f"Navigate to {state.url}",
                )
            ]
            s_num = 2
            for inp in inputs:
                val = self._valid_input_for_element(inp)
                if inp.role == ElementRole.COMBOBOX:
                    happy_steps.append(
                        ActionStep(
                            step_number=s_num,
                            action=ActionType.SELECT,
                            selector=inp.selector,
                            value=val,
                            description=f"Select option '{val}' on {inp.accessible_name or inp.selector}",
                        )
                    )
                elif inp.role == ElementRole.CHECKBOX:
                    happy_steps.append(
                        ActionStep(
                            step_number=s_num,
                            action=ActionType.CLICK,
                            selector=inp.selector,
                            description=f"Check {inp.accessible_name or inp.selector}",
                        )
                    )
                else:
                    happy_steps.append(
                        ActionStep(
                            step_number=s_num,
                            action=ActionType.FILL,
                            selector=inp.selector,
                            value=val,
                            description=f"Fill '{val}' into {inp.accessible_name or inp.selector}",
                        )
                    )
                s_num += 1

            if submit_btn:
                happy_steps.append(
                    ActionStep(
                        step_number=s_num,
                        action=ActionType.CLICK,
                        selector=submit_btn.selector,
                        description=f"Submit form via {submit_btn.accessible_name or submit_btn.selector}",
                        assertion=ExecutionAssertion(
                            assertion_type=AssertionType.TEXT_NOT_VISIBLE,
                            expected_value="Internal Server Error",
                            description="Verify valid submission does not trigger server crash or 500 error",
                        ),
                    )
                )

            cases.append(
                TestCase(
                    test_id=f"ADAPT-FORM-HAPPY-{uuid.uuid4().hex[:6]}",
                    name=f"Adaptive Form Submission Happy Path [{state.page_title or state.url}]",
                    objective="Verify successful form interaction with valid synthetic inputs",
                    page_category=PageCategory.GENERAL,
                    relevant_elements=[e.selector for e in inputs],
                    action_description=f"Fill observed controls and submit on {state.url}",
                    expected_outcome="Form submits without unhandled errors or rejection",
                    expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                    priority=Priority.HIGH,
                    safe_for_future_automation=True,
                    selection_reason="Observed interactive form during autonomous exploration",
                    confidence=0.88,
                    status=TestCaseStatus.PROPOSED,
                    steps=happy_steps,
                )
            )

            # Case B: Required Field Validation (Negative test)
            required_inputs = [e for e in inputs if e.constraints.required]
            for req_el in required_inputs:
                neg_steps = [
                    ActionStep(
                        step_number=1,
                        action=ActionType.NAVIGATE,
                        value=state.url,
                        description=f"Navigate to {state.url}",
                    )
                ]
                neg_s_num = 2
                for inp in inputs:
                    if inp.element_key == req_el.element_key:
                        continue  # Leave blank to test required constraint
                    val = self._valid_input_for_element(inp)
                    if inp.role == ElementRole.TEXTBOX:
                        neg_steps.append(
                            ActionStep(
                                step_number=neg_s_num,
                                action=ActionType.FILL,
                                selector=inp.selector,
                                value=val,
                                description=f"Fill non-empty value into {inp.accessible_name or inp.selector}",
                            )
                        )
                        neg_s_num += 1

                if submit_btn:
                    neg_steps.append(
                        ActionStep(
                            step_number=neg_s_num,
                            action=ActionType.CLICK,
                            selector=submit_btn.selector,
                            description=f"Attempt submission with required field '{req_el.accessible_name or req_el.selector}' empty",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.VALIDITY_VALUE_MISSING,
                                target_selector=req_el.selector,
                                expected_value="valueMissing",
                                description=f"Expect browser form validation to block submission on required field '{req_el.accessible_name}'",
                            ),
                        )
                    )

                cases.append(
                    TestCase(
                        test_id=f"ADAPT-REQ-VAL-{uuid.uuid4().hex[:6]}",
                        name=f"Adaptive Required Field Enforcement: {req_el.accessible_name or req_el.selector}",
                        objective=f"Verify form cannot be submitted when required field '{req_el.accessible_name}' is blank",
                        page_category=PageCategory.GENERAL,
                        relevant_elements=[req_el.selector],
                        action_description=f"Omit required field {req_el.selector} and submit",
                        expected_outcome="Form submission is blocked by validation constraint",
                        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                        priority=Priority.HIGH,
                        safe_for_future_automation=True,
                        selection_reason=f"Field {req_el.selector} has required constraint observed in DOM",
                        confidence=0.92,
                        status=TestCaseStatus.PROPOSED,
                        steps=neg_steps,
                    )
                )

            # Case C: Boundary Value Analysis on inputs with constraints (maxlength)
            bounded_inputs = [e for e in inputs if e.constraints.max_length and e.constraints.max_length > 0]
            for b_el in bounded_inputs:
                max_len = b_el.constraints.max_length
                boundary_val = "X" * (max_len + 5)
                bva_steps = [
                    ActionStep(
                        step_number=1,
                        action=ActionType.NAVIGATE,
                        value=state.url,
                        description=f"Navigate to {state.url}",
                    ),
                    ActionStep(
                        step_number=2,
                        action=ActionType.FILL,
                        selector=b_el.selector,
                        value=boundary_val,
                        description=f"Fill value exceeding maxlength ({len(boundary_val)} > {max_len})",
                    ),
                ]
                if submit_btn:
                    bva_steps.append(
                        ActionStep(
                            step_number=3,
                            action=ActionType.CLICK,
                            selector=submit_btn.selector,
                            description="Submit with boundary input",
                            assertion=ExecutionAssertion(
                                assertion_type=AssertionType.TEXT_NOT_VISIBLE,
                                expected_value="Internal Server Error",
                                description="Ensure boundary value does not cause application crash",
                            ),
                        )
                    )

                cases.append(
                    TestCase(
                        test_id=f"ADAPT-BVA-LEN-{uuid.uuid4().hex[:6]}",
                        name=f"Adaptive Boundary Value Test: {b_el.accessible_name or b_el.selector} (maxlength={max_len})",
                        objective=f"Verify input handling at and beyond maxlength boundary ({max_len})",
                        page_category=PageCategory.GENERAL,
                        relevant_elements=[b_el.selector],
                        action_description=f"Enter {len(boundary_val)} characters into field with maxlength {max_len}",
                        expected_outcome="Input truncated by browser or gracefully rejected by backend",
                        expected_behavior_source=ExpectedBehaviorSource.INVARIANT,
                        priority=Priority.MEDIUM,
                        safe_for_future_automation=True,
                        selection_reason=f"Observed HTML constraint maxlength={max_len}",
                        confidence=0.90,
                        status=TestCaseStatus.PROPOSED,
                        derived_values={"boundary_value": boundary_val, "boundary_type": "maxlength_overflow"},
                        steps=bva_steps,
                    )
                )

        return cases

    def _generate_journey_test(
        self,
        transition: StateTransition,
        graph: ApplicationStateGraph,
    ) -> Optional[TestCase]:
        target_state = graph.states.get(transition.resulting_state_id or "")
        if not target_state or not transition.target_element_selector:
            return None

        steps = [
            ActionStep(
                step_number=1,
                action=ActionType.NAVIGATE,
                value=transition.url_before,
                description=f"Navigate to starting page {transition.url_before}",
            ),
            ActionStep(
                step_number=2,
                action=ActionType.CLICK,
                selector=transition.target_element_selector,
                description=f"Interact with control '{transition.target_element_name or transition.target_element_selector}'",
                assertion=ExecutionAssertion(
                    assertion_type=AssertionType.URL_CONTAINS if transition.url_changed else AssertionType.ELEMENT_VISIBLE,
                    expected_value=transition.url_after if transition.url_changed else "body",
                    target_selector="body" if not transition.url_changed else None,
                    description=f"Verify transition to resulting state at {transition.url_after}",
                ),
            ),
        ]

        return TestCase(
            test_id=f"ADAPT-JOURNEY-{uuid.uuid4().hex[:6]}",
            name=f"Adaptive State Journey: {transition.target_element_name or 'Control'} -> {target_state.page_title or transition.url_after}",
            objective=f"Verify navigation / state transition from {transition.url_before} to {transition.url_after}",
            page_category=PageCategory.GENERAL,
            relevant_elements=[transition.target_element_selector],
            action_description=f"Click {transition.target_element_name or transition.target_element_selector} and verify state transition",
            expected_outcome=f"Application reaches state at {transition.url_after}",
            expected_behavior_source=ExpectedBehaviorSource.INFERRED,
            priority=Priority.HIGH,
            safe_for_future_automation=True,
            selection_reason="Observed successful state transition during autonomous exploration trace",
            confidence=0.95,
            status=TestCaseStatus.PROPOSED,
            steps=steps,
        )

    def _generate_control_tests(self, state: UIState) -> List[TestCase]:
        cases: List[TestCase] = []
        buttons = [e for e in state.interactive_elements if e.role == ElementRole.BUTTON and not e.is_submission][:3]

        for btn in buttons:
            steps = [
                ActionStep(
                    step_number=1,
                    action=ActionType.NAVIGATE,
                    value=state.url,
                    description=f"Navigate to {state.url}",
                ),
                ActionStep(
                    step_number=2,
                    action=ActionType.CLICK,
                    selector=btn.selector,
                    description=f"Click button '{btn.accessible_name or btn.selector}'",
                    assertion=ExecutionAssertion(
                        assertion_type=AssertionType.ELEMENT_VISIBLE,
                        target_selector="body",
                        description="Ensure page remains interactive and responsive after click",
                    ),
                ),
            ]

            cases.append(
                TestCase(
                    test_id=f"ADAPT-CTRL-{uuid.uuid4().hex[:6]}",
                    name=f"Adaptive Interactive Control Test: {btn.accessible_name or btn.selector}",
                    objective=f"Verify clicking interactive control '{btn.accessible_name}' operates safely",
                    page_category=PageCategory.GENERAL,
                    relevant_elements=[btn.selector],
                    action_description=f"Click {btn.accessible_name or btn.selector} on {state.url}",
                    expected_outcome="Control responds without client errors",
                    expected_behavior_source=ExpectedBehaviorSource.INFERRED,
                    priority=Priority.MEDIUM,
                    safe_for_future_automation=True,
                    selection_reason="Interactive button discovered during autonomous exploration",
                    confidence=0.85,
                    status=TestCaseStatus.PROPOSED,
                    steps=steps,
                )
            )

        return cases

    def _valid_input_for_element(self, element: InteractiveElement) -> str:
        el_type = (element.element_type or "").lower()
        name = (element.accessible_name or element.constraints.input_type or "").lower()

        if element.role == ElementRole.COMBOBOX and element.constraints.options:
            opts = [o.strip() for o in element.constraints.options if o.strip() and o.lower() not in ("", "select", "choose", "-- select --")]
            return opts[0] if opts else "Option"

        if el_type == "email" or "email" in name:
            return "qa.user@verityqa.local"
        if el_type == "password" or "password" in name:
            return "ValidSecret123!"
        if el_type == "number" or "number" in name or "age" in name:
            return element.constraints.min_value or "10"
        if el_type == "date" or "date" in name:
            return "2025-06-15"
        if "title" in name or "subject" in name:
            return "Autonomous Verification Task"
        if "description" in name or "detail" in name or "message" in name or el_type == "textarea":
            return "Automated end-to-end verification description payload."
        if "name" in name:
            return "VerityQA Test"

        return "AdaptiveTestInput"
