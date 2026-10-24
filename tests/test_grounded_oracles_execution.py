"""
Executor integrity: outcome classes, meaningful-assertion accounting and the evidence-grounded oracles,
exercised in a real browser against a local static page (no network).
"""

import json
import pathlib

import pytest

from app.execution.executor import BrowserExecutor
from app.schemas import ActionStep, ActionType, AssertionType, ExecutionAssertion, ExecutionStatus

PAGE = """<!doctype html><html><body>
<h1>Catalogue</h1>
<form id="f" onsubmit="return false;">
  <input id="title" required maxlength="5">
  <input id="hours" type="number" min="1" max="40">
  <button id="go" type="submit">Save</button>
</form>
<div class="error" style="display:none" id="err">Title is required</div>
<ul id="sorted"><li class="item"><span class="name">Alpha</span> $3.00</li><li class="item"><span class="name">Bravo</span> $9.50</li><li class="item"><span class="name">Charlie</span> $12.00</li></ul>
<table><tr><th>Title</th><th>Priority</th></tr><tr><td>Fix login</td><td>High</td></tr><tr><td>Write docs</td><td>Low</td></tr></table>
</body></html>"""


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    path = tmp_path_factory.mktemp("oracle") / "page.html"
    path.write_text(PAGE, encoding="utf-8")
    return pathlib.Path(path).as_uri()


def _a(kind, selector=None, expected=None):
    return ExecutionAssertion(assertion_type=kind, target_selector=selector, expected_value=expected, description=kind.value)


async def _run(url, *steps):
    numbered = [s.model_copy(update={"step_number": i + 1}) for i, s in enumerate(steps)]
    return await BrowserExecutor(headless=True).execute_steps("T", "t", numbered, initial_url=url, timeout_ms=3000)


def _observe(assertion):
    return ActionStep(step_number=0, action=ActionType.OBSERVE_STATE, description="observe", assertion=assertion)


@pytest.mark.asyncio
async def test_trivial_only_assertions_are_incomplete_not_pass(page_url):
    res = await _run(page_url, _observe(_a(AssertionType.ELEMENT_VISIBLE, "body")))
    assert res.status == ExecutionStatus.INCONCLUSIVE
    assert res.meaningful_assertions_evaluated == 0


@pytest.mark.asyncio
async def test_missing_selector_is_locator_error_not_product_failure(page_url):
    res = await _run(page_url, ActionStep(step_number=0, action=ActionType.CLICK, selector="#does-not-exist",
                                          description="click", assertion=_a(AssertionType.TEXT_VISIBLE, None, "Catalogue")))
    assert res.status == ExecutionStatus.LOCATOR_ERROR
    assert res.assertions_evaluated == 0


@pytest.mark.asyncio
async def test_custom_assertion_never_passes(page_url):
    res = await _run(page_url, _observe(_a(AssertionType.CUSTOM, None, "anything")))
    assert res.status == ExecutionStatus.INCONCLUSIVE


@pytest.mark.asyncio
async def test_list_sorted_oracle(page_url):
    asc_num = json.dumps({"key": "number", "direction": "asc"})
    desc_text = json.dumps({"key": "text", "direction": "desc"})
    assert (await _run(page_url, _observe(_a(AssertionType.LIST_SORTED, None, asc_num)))).status == ExecutionStatus.PASSED
    assert (await _run(page_url, _observe(_a(AssertionType.LIST_SORTED, None, desc_text)))).status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_list_all_contain_uses_records_not_cells(page_url):
    res = await _run(page_url, _observe(_a(AssertionType.LIST_ALL_CONTAIN, None, "$")))
    assert res.status == ExecutionStatus.PASSED  # the three priced list items
    res = await _run(page_url, _observe(_a(AssertionType.LIST_ALL_CONTAIN, None, "Bravo")))
    assert res.status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_field_validity_and_value_length_oracles(page_url):
    fill_empty = ActionStep(step_number=0, action=ActionType.FILL, selector="#title", value="", description="empty")
    res = await _run(page_url, fill_empty, ActionStep(step_number=0, action=ActionType.CLICK, selector="#go", description="submit",
                                                      assertion=_a(AssertionType.FIELD_VALIDITY, "#title", "valueMissing")))
    assert res.status == ExecutionStatus.PASSED and res.meaningful_assertions_evaluated == 1
    too_long = ActionStep(step_number=0, action=ActionType.FILL, selector="#title", value="abcdefg", description="7 chars",
                          assertion=_a(AssertionType.ELEMENT_VALUE_LENGTH, "#title", "==5"))
    assert (await _run(page_url, too_long)).status == ExecutionStatus.PASSED
    over = ActionStep(step_number=0, action=ActionType.FILL, selector="#hours", value="41", description="41",
                      assertion=_a(AssertionType.FIELD_VALIDITY, "#hours", "rangeOverflow"))
    assert (await _run(page_url, over)).status == ExecutionStatus.PASSED
    ok = ActionStep(step_number=0, action=ActionType.FILL, selector="#hours", value="40", description="40",
                    assertion=_a(AssertionType.FIELD_VALIDITY, "#hours", "rangeOverflow"))
    assert (await _run(page_url, ok)).status == ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_validation_feedback_oracles(page_url):
    fill = ActionStep(step_number=0, action=ActionType.FILL, selector="#title", value="", description="empty")
    shown = await _run(page_url, fill, _observe(_a(AssertionType.VALIDATION_FEEDBACK_SHOWN, "#title")))
    assert shown.status == ExecutionStatus.PASSED  # the required field is invalid
    fill_ok = ActionStep(step_number=0, action=ActionType.FILL, selector="#title", value="abc", description="valid")
    none = await _run(page_url, fill_ok, _observe(_a(AssertionType.NO_VALIDATION_FEEDBACK, "#title")))
    assert none.status == ExecutionStatus.PASSED


@pytest.mark.asyncio
async def test_status_code_oracle(page_url):
    assert (await _run(page_url, _observe(_a(AssertionType.STATUS_CODE, None, "<400")))).status == ExecutionStatus.PASSED
    assert (await _run(page_url, _observe(_a(AssertionType.STATUS_CODE, None, "==404")))).status == ExecutionStatus.FAILED
    # An expectation the oracle cannot evaluate is INCOMPLETE, never a pass.
    assert (await _run(page_url, _observe(_a(AssertionType.STATUS_CODE, None, "ok")))).status == ExecutionStatus.INCONCLUSIVE
