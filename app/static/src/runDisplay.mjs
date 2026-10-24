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
    assertionValidity: typeof validity === 'number' ? (validity * 100).toFixed(1) : null,
  };
}
