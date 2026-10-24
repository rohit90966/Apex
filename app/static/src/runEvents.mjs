// Client side of the investigation run contract:
// - SSE stream: GET /api/investigate/stream?url=&description=&requirements=  (every event carries run_id)
// - Canonical run: GET /api/runs/{run_id} -> { run, report, test_cases }
// Dependency-free so it can be unit tested with `node --test`.

export const PROGRESS_EVENT_TYPES = new Set([
  'PROBE_START',
  'PROBE_RESULT',
  'LEAD_DETECTED',
  'REPRO_RESULT',
  'STOPPING',
]);

export function buildInvestigationStreamUrl({ url, description, requirements } = {}) {
  const params = new URLSearchParams({ url: String(url || '').trim() });
  const desc = String(description || '').trim();
  if (desc) params.set('description', desc);
  const reqText = (Array.isArray(requirements) ? requirements : String(requirements || '').split('\n'))
    .map((r) => String(r).trim())
    .filter(Boolean)
    .join('\n');
  if (reqText) params.set('requirements', reqText);
  return `/api/investigate/stream?${params.toString()}`;
}

// Classifies one raw SSE `data` payload into { kind, runId, event, diagnostic }.
// kind: 'created' | 'progress' | 'complete' | 'error' | 'unknown' | 'invalid'
export function parseRunEvent(rawData) {
  let event;
  try {
    event = JSON.parse(rawData);
  } catch (e) {
    return { kind: 'invalid', runId: null, event: null, diagnostic: 'Unparseable stream message' };
  }
  if (!event || typeof event !== 'object') {
    return { kind: 'invalid', runId: null, event: null, diagnostic: 'Stream message is not an object' };
  }
  const runId = typeof event.run_id === 'string' ? event.run_id : null;
  const type = event.type;
  if (type === 'RUN_CREATED') return { kind: 'created', runId, event };
  if (PROGRESS_EVENT_TYPES.has(type)) return { kind: 'progress', runId, event };
  if (type === 'COMPLETE') return { kind: 'complete', runId, event };
  if (type === 'ERROR') return { kind: 'error', runId, event };
  return { kind: 'unknown', runId, event, diagnostic: `Unrecognized stream event type: ${String(type)}` };
}

// Returns a function taking raw SSE data and dispatching to the matching callback.
export function createRunStreamHandler({ onCreated, onProgress, onComplete, onError, onUnknown } = {}) {
  return (rawData) => {
    const parsed = parseRunEvent(rawData);
    const handlers = {
      created: onCreated,
      progress: onProgress,
      complete: onComplete,
      error: onError,
      unknown: onUnknown,
      invalid: onUnknown,
    };
    const handler = handlers[parsed.kind];
    if (handler) handler(parsed);
    return parsed;
  };
}

// Toast text for a finished run. A lifecycle-completed run is only announced as a success when
// its report outcome is "succeeded" (see report_outcome in GET /api/runs/{run_id}).
export function completionMessage(run = {}) {
  const runId = run.run_id || '';
  const status = String(run.report_status || 'unknown').toUpperCase();
  const reason = run.stopping_reason ? `: ${run.stopping_reason}` : '';
  switch (run.report_outcome) {
    case 'succeeded':
      return { tone: 'success', message: `Investigation ${runId} completed.` };
    case 'limited':
      return { tone: 'warning', message: `Investigation ${runId} finished with status ${status}${reason}.` };
    case 'unsuccessful':
      return { tone: 'danger', message: `Investigation ${runId} did not succeed (status ${status})${reason}.` };
    default:
      return { tone: 'warning', message: `Investigation ${runId} finished; its report status is unknown.` };
  }
}

export async function fetchRun(runId, fetchImpl = fetch) {
  if (!runId) throw new Error('Missing run ID');
  const res = await fetchImpl(`/api/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) throw new Error(`Could not load run ${runId} (HTTP ${res.status})`);
  return res.json();
}

// Most recent completed run, or null when none has been persisted yet.
export async function fetchLatestCompletedRun(fetchImpl = fetch) {
  const res = await fetchImpl('/api/runs');
  if (!res.ok) throw new Error(`Could not list runs (HTTP ${res.status})`);
  const data = await res.json();
  const latest = (data.runs || []).find((r) => r.status === 'completed');
  return latest ? fetchRun(latest.run_id, fetchImpl) : null;
}
