// Display helpers for run data. They never invent values: missing data renders as NO_DATA.
// Dependency-free so it can be unit tested with `node --test`.

export const NO_DATA = '—';

// InvestigationStatus value -> outcome. Mirrors REPORT_OUTCOMES in app/runs/store.py.
export const REPORT_OUTCOMES = {
  completed: 'succeeded',
  partial: 'limited',
  timed_out: 'limited',
  budget_exhausted: 'limited',
  blocked: 'unsuccessful',
  error: 'unsuccessful',
  failed: 'unsuccessful',
  cancelled: 'unsuccessful',
  unsafe_stopped: 'unsuccessful',
};

export function reportOutcome(status) {
  if (!status) return 'unknown';
  return REPORT_OUTCOMES[String(status).toLowerCase()] || 'unknown';
}

export function reportStatusLabel(status) {
  return status ? String(status).toUpperCase() : NO_DATA;
}

const OUTCOME_BADGES = {
  succeeded: 'badge-emerald',
  limited: 'badge-amber',
  unsuccessful: 'badge-rose',
  unknown: 'badge-neutral',
};

export function reportStatusBadgeClass(status) {
  return OUTCOME_BADGES[reportOutcome(status)];
}

// First positive count among candidates; otherwise the first real zero; otherwise null (no data).
export function pickCount(...candidates) {
  const numbers = candidates.filter((c) => typeof c === 'number' && Number.isFinite(c));
  const positive = numbers.find((n) => n > 0);
  if (positive !== undefined) return positive;
  return numbers.length > 0 ? numbers[0] : null;
}

export function displayValue(value) {
  if (value === null || value === undefined || value === '') return NO_DATA;
  if (typeof value === 'number' && !Number.isFinite(value)) return NO_DATA;
  return String(value);
}

// Body for POST /api/tests/execute. A persisted run is executed by run_id, so the server runs exactly the
// run's saved test cases and saves the result against that run; ad-hoc suites (e.g. loaded report files)
// still send their test cases and are not persisted.
export function buildExecuteRequest(generatedTests, targetUrl) {
  const runId = generatedTests?.run_id;
  if (runId) return { target_url: targetUrl, run_id: runId };
  return { target_url: targetUrl, test_cases: generatedTests?.test_cases || [] };
}

// Summary of a real /api/tests/execute response, or null when no execution has happened.
export function executionSummary(executionReport) {
  const executed = executionReport?.executed_count;
  if (typeof executed !== 'number' || !Number.isFinite(executed)) return null;
  const passed = executionReport.passed_count ?? 0;
  const failed = executionReport.failed_count ?? 0;
  const validity = executionReport.assertion_validity_rate;
  return {
    executed,
    passed,
    failed,
    setupFailures: executionReport.setup_failures_count ?? 0,
    passRate: executed > 0 ? ((passed / executed) * 100).toFixed(1) : null,
    assertionValidity: executed > 0 && typeof validity === 'number' ? (validity * 100).toFixed(1) : null,
  };
}

// Backend ExecutionStatus -> display status. Mirrors ExecutionStatus in app/schemas.py.
const EXECUTION_DISPLAY_STATUS = {
  PASSED: 'PASS',
  FAILED: 'FAIL',
  PRODUCT_BEHAVIOR_DEVIATION: 'FAIL',
  ERROR: 'ERROR',
  LOCATOR_ERROR: 'ERROR',
  AUTOMATION_ERROR: 'ERROR',
  ENVIRONMENT_ERROR: 'ERROR',
  BLOCKED: 'BLOCKED',
  TEST_DEFINITION_ERROR: 'INVALID',
  INCONCLUSIVE: 'INCOMPLETE',
  SKIPPED: 'SKIPPED',
};

export const DISPLAY_STATUSES = ['PASS', 'FAIL', 'ERROR', 'BLOCKED', 'INVALID', 'INCOMPLETE'];

export function displayExecutionStatus(status) {
  return EXECUTION_DISPLAY_STATUS[String(status || '').toUpperCase()] || 'UNKNOWN';
}

export function executionStatusBadgeClass(status) {
  const display = displayExecutionStatus(status);
  if (display === 'PASS') return 'badge-success';
  if (display === 'FAIL') return 'badge-danger';
  if (display === 'INVALID' || display === 'BLOCKED') return 'badge-neutral';
  return 'badge-warning';
}

// Counts per display status, computed from the actual results.
export function executionStatusCounts(executionReport) {
  const counts = Object.fromEntries(DISPLAY_STATUSES.map((s) => [s, 0]));
  for (const r of executionReport?.results || []) {
    const display = displayExecutionStatus(r.status);
    counts[display] = (counts[display] || 0) + 1;
  }
  return counts;
}

export function resultsByTestId(executionReport) {
  const map = {};
  for (const r of executionReport?.results || []) {
    if (r.test_id) map[r.test_id] = r;
  }
  return map;
}

function textOf(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value || null;
  if (typeof value === 'object' && typeof value.text === 'string') return value.text || null;
  return null;
}

// Bug findings split into verified (independently reproduced) and unverified (candidates and rejections).
export function bugFindings(investigationReport, executionReport) {
  const verified = [];
  const unverified = [];
  for (const lead of investigationReport?.confirmed_bug_leads || []) {
    verified.push({
      id: lead.id,
      title: lead.title,
      source: 'Investigation',
      verdict: 'CONFIRMED BY REPRODUCER',
      severity: lead.severity_guess || null,
      detail: textOf(lead.actual),
      evidence_ids: lead.evidence_ids || [],
    });
  }
  for (const lead of investigationReport?.unstable_or_rejected_leads || []) {
    unverified.push({
      id: lead.id,
      title: lead.title,
      source: 'Investigation',
      verdict: lead.status || 'UNVERIFIED',
      reason: textOf(lead.reproduction_failure_reason),
    });
  }
  for (const bug of executionReport?.bug_reports || []) {
    const item = {
      id: bug.bug_id,
      title: bug.title,
      source: `Execution (${bug.test_id})`,
      verdict: bug.classification || bug.verification_status,
      severity: bug.severity || null,
      detail: textOf(bug.actual_behavior) || textOf(bug.summary),
      reproduction: textOf(bug.reproduction_rate),
      reason: textOf(bug.verdict_rationale) || textOf(bug.summary),
    };
    if (bug.verification_status === 'CONFIRMED_REPRODUCIBLE') verified.push(item);
    else unverified.push(item);
  }
  return { verified, unverified };
}
