// Display helpers must never invent values. Run with: node --test tests/frontend/runDisplay.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  NO_DATA,
  displayValue,
  executionSummary,
  pickCount,
  reportOutcome,
  reportStatusBadgeClass,
  reportStatusLabel,
} from '../../app/static/src/runDisplay.mjs';

test('no execution report means no execution statistics', () => {
  assert.equal(executionSummary(null), null);
  assert.equal(executionSummary(undefined), null);
  assert.equal(executionSummary({}), null);
  assert.equal(executionSummary({ results: [] }), null);
});

test('execution statistics come only from the real response', () => {
  const summary = executionSummary({
    executed_count: 6, passed_count: 3, failed_count: 3, setup_failures_count: 0, assertion_validity_rate: 0.5,
  });
  assert.deepEqual(summary, {
    executed: 6, passed: 3, failed: 3, setupFailures: 0, passRate: '50.0', assertionValidity: '50.0',
  });
});

test('an empty execution has no pass rate rather than 100%', () => {
  assert.equal(executionSummary({ executed_count: 0, passed_count: 0, failed_count: 0 }).passRate, null);
});

test('counts prefer real non-zero values, keep a real zero, and never invent one', () => {
  assert.equal(pickCount(undefined, 6, 0), 6);
  assert.equal(pickCount(0, 8), 8);
  assert.equal(pickCount(0, 0), 0);
  assert.equal(pickCount(undefined, null), null);
  assert.equal(pickCount(Number.NaN), null);
});

test('missing values render as an em dash', () => {
  assert.equal(displayValue(null), NO_DATA);
  assert.equal(displayValue(undefined), NO_DATA);
  assert.equal(displayValue(''), NO_DATA);
  assert.equal(displayValue(0), '0');
  assert.equal(displayValue('DETERMINISTIC'), 'DETERMINISTIC');
});

test('a blocked report is not styled or labelled as a success', () => {
  assert.equal(reportOutcome('blocked'), 'unsuccessful');
  assert.equal(reportStatusLabel('blocked'), 'BLOCKED');
  assert.notEqual(reportStatusBadgeClass('blocked'), reportStatusBadgeClass('completed'));
  assert.equal(reportStatusBadgeClass('completed'), 'badge-emerald');
  assert.equal(reportStatusBadgeClass('budget_exhausted'), 'badge-amber');
});

test('a missing status is unknown, never COMPLETED', () => {
  assert.equal(reportStatusLabel(undefined), NO_DATA);
  assert.equal(reportOutcome(undefined), 'unknown');
  assert.equal(reportStatusBadgeClass(undefined), 'badge-neutral');
});
