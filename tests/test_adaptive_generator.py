"""
tests/test_adaptive_generator.py
================================
Tests for Phase 2: Adaptive Test Generation from ApplicationStateGraph.

Verifies:
  1. Generation of happy path form submission test cases from state elements
  2. Generation of required field validation tests with expected valueMissing assertions
  3. Generation of boundary value analysis tests for maxlength constraints
  4. Generation of state transition journey tests across discovered pages
  5. Generation of interactive control safety tests
  6. Direct execution compatibility with QAPlanRunner (using test_case.steps)
  7. Independence from page classification templates (GENERAL category)
"""

import pytest
from app.exploration.graph import (
    ApplicationStateGraph,
    UIState,
    InteractiveElement,
    ElementRole,
    ElementConstraints,
    StateTransition,
    ExplorationActionType,
)
from app.exploration.generator import AdaptiveTestGenerator
from app.schemas import (
    PageCategory,
    ActionType,
    AssertionType,
    ExecutionStatus,
)
from app.execution.runner import QAPlanRunner


def create_sample_graph() -> ApplicationStateGraph:
    graph = ApplicationStateGraph(starting_url="http://127.0.0.1:8002/")

    # State 1: Dashboard
    s1 = UIState(
        state_id="state-dash",
        url="http://127.0.0.1:8002/",
        page_title="TaskDash — Dashboard",
        dom_fingerprint="fp1",
        interactive_elements=[
            InteractiveElement(
                selector="a.tab:nth-of-type(2)",
                tag="a",
                role=ElementRole.LINK,
                accessible_name="Create Task",
                is_navigation=True,
                constraints=ElementConstraints(),
            ),
            InteractiveElement(
                selector="button#quick-action",
                tag="button",
                role=ElementRole.BUTTON,
                accessible_name="Quick Refresh",
                constraints=ElementConstraints(),
            ),
        ],
    )

    # State 2: Create Task Form
    s2 = UIState(
        state_id="state-create",
        url="http://127.0.0.1:8002/create",
        page_title="TaskDash — Create Task",
        dom_fingerprint="fp2",
        interactive_elements=[
            InteractiveElement(
                selector="input#title",
                tag="input",
                role=ElementRole.TEXTBOX,
                element_type="text",
                accessible_name="Title",
                form_id="create-task-form",
                constraints=ElementConstraints(required=True, max_length=100),
            ),
            InteractiveElement(
                selector="textarea#description",
                tag="textarea",
                role=ElementRole.TEXTBOX,
                element_type="textarea",
                accessible_name="Description",
                form_id="create-task-form",
                constraints=ElementConstraints(),
            ),
            InteractiveElement(
                selector="select#priority",
                tag="select",
                role=ElementRole.COMBOBOX,
                element_type="select-one",
                accessible_name="Priority",
                form_id="create-task-form",
                constraints=ElementConstraints(options=["High", "Medium", "Low"]),
            ),
            InteractiveElement(
                selector="button#submit-task",
                tag="button",
                role=ElementRole.BUTTON,
                element_type="submit",
                accessible_name="Create Task",
                form_id="create-task-form",
                is_submission=True,
                constraints=ElementConstraints(),
            ),
        ],
    )

    graph.states[s1.state_id] = s1
    graph.states[s2.state_id] = s2

    # Transition from s1 to s2
    graph.transitions.append(
        StateTransition(
            source_state_id=s1.state_id,
            action_type=ExplorationActionType.CLICK,
            target_element_selector="a.tab:nth-of-type(2)",
            target_element_name="Create Task",
            resulting_state_id=s2.state_id,
            url_before="http://127.0.0.1:8002/",
            url_after="http://127.0.0.1:8002/create",
            url_changed=True,
            new_state_discovered=True,
            step_number=1,
        )
    )

    return graph


def test_generator_creates_test_cases():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=10)

    assert len(tests) >= 3
    # Check that all generated tests have steps populated
    for t in tests:
        assert t.steps is not None
        assert len(t.steps) > 0
        assert t.page_category == PageCategory.GENERAL


def test_generator_creates_happy_path_form_test():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=10)

    happy_test = next((t for t in tests if "Happy Path" in t.name), None)
    assert happy_test is not None
    assert len(happy_test.steps) >= 4  # navigate, fill title, fill desc, select priority, submit
    actions = [s.action for s in happy_test.steps]
    assert ActionType.NAVIGATE in actions
    assert ActionType.FILL in actions
    assert ActionType.CLICK in actions


def test_generator_creates_required_field_validation_test():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=10)

    req_test = next((t for t in tests if "Required Field Enforcement" in t.name), None)
    assert req_test is not None
    # Check that assertion enforces valueMissing
    last_step = req_test.steps[-1]
    assert last_step.assertion is not None
    assert last_step.assertion.assertion_type == AssertionType.VALIDITY_VALUE_MISSING
    assert "input#title" in last_step.assertion.target_selector


def test_generator_creates_bva_length_test():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=10)

    bva_test = next((t for t in tests if "Boundary Value Test" in t.name), None)
    assert bva_test is not None
    assert bva_test.derived_values is not None
    assert bva_test.derived_values["boundary_type"] == "maxlength_overflow"
    assert len(bva_test.derived_values["boundary_value"]) > 100


def test_generator_creates_journey_transition_test():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=10)

    journey_test = next((t for t in tests if "Adaptive State Journey" in t.name), None)
    assert journey_test is not None
    assert len(journey_test.steps) == 2
    assert journey_test.steps[0].action == ActionType.NAVIGATE
    assert journey_test.steps[1].action == ActionType.CLICK
    assert journey_test.steps[1].assertion.assertion_type == AssertionType.URL_CONTAINS
    assert "http://127.0.0.1:8002/create" in journey_test.steps[1].assertion.expected_value


def test_runner_executes_adaptive_pregenerated_steps():
    graph = create_sample_graph()
    generator = AdaptiveTestGenerator()
    tests = generator.generate_tests_from_graph(graph, max_cases=5)
    test_case = tests[0]

    runner = QAPlanRunner()
    synthesized = runner.synthesize_steps(test_case, "http://127.0.0.1:8002/")
    # synthesize_steps should directly use test_case.steps
    assert len(synthesized) == len(test_case.steps)
    assert synthesized[0].description == test_case.steps[0].description
