// Contract tests for the dashboard's investigation stream handling.
// Run with: node --test tests/frontend/
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildInvestigationStreamUrl,
  completionMessage,
  createRunStreamHandler,
  parseRunEvent,
  fetchRun,
  fetchLatestCompletedRun,
} from '../../app/static/src/runEvents.mjs';

const RUN_ID = 'inv-0123456789ab';
const sse = (obj) => JSON.stringify({ run_id: RUN_ID, ...obj });

function recordingHandler() {
  const calls = [];
  const record = (name) => (parsed) => calls.push([name, parsed]);
  const handle = createRunStreamHandler({
    onCreated: record('created'),
    onProgress: record('progress'),
    onComplete: record('complete'),
    onError: record('error'),
    onUnknown: record('unknown'),
  });
  return { calls, handle };
}

test('handles the uppercase event types the backend actually emits', () => {
  const { calls, handle } = recordingHandler();
  const sequence = ['RUN_CREATED', 'PROBE_START', 'PROBE_RESULT', 'LEAD_DETECTED', 'REPRO_RESULT', 'STOPPING', 'COMPLETE'];
  sequence.forEach((type) => handle(sse({ type })));

  assert.deepEqual(calls.map(([name]) => name),
    ['created', 'progress', 'progress', 'progress', 'progress', 'progress', 'complete']);
  assert.ok(calls.every(([, parsed]) => parsed.runId === RUN_ID));
  assert.equal(calls[1][1].event.type, 'PROBE_START');
});

test('legacy lowercase names are not treated as completion', () => {
  const { calls, handle } = recordingHandler();
  handle(JSON.stringify({ type: 'complete', status: 'completed' }));
  handle(JSON.stringify({ type: 'event', data: {} }));
  assert.deepEqual(calls.map(([name]) => name), ['unknown', 'unknown']);
});

test('errors surface the backend error text and run id', () => {
  const { calls, handle } = recordingHandler();
  handle(sse({ type: 'ERROR', status: 'failed', error: 'RuntimeError: browser crashed' }));
  assert.equal(calls[0][0], 'error');
  assert.equal(calls[0][1].runId, RUN_ID);
  assert.equal(calls[0][1].event.error, 'RuntimeError: browser crashed');
});

test('unknown and unparseable messages produce a diagnostic instead of being silently dropped', () => {
  assert.match(parseRunEvent(sse({ type: 'NEW_THING' })).diagnostic, /NEW_THING/);
  const invalid = parseRunEvent('not json');
  assert.equal(invalid.kind, 'invalid');
  assert.ok(invalid.diagnostic);
});

test('stream URL carries description and requirements', () => {
  const url = buildInvestigationStreamUrl({
    url: ' http://127.0.0.1:8001/ ',
    description: 'Demo portal',
    requirements: ['Must log in', '', 'Cart persists'],
  });
  const params = new URL(url, 'http://localhost').searchParams;
  assert.equal(new URL(url, 'http://localhost').pathname, '/api/investigate/stream');
  assert.equal(params.get('url'), 'http://127.0.0.1:8001/');
  assert.equal(params.get('description'), 'Demo portal');
  assert.equal(params.get('requirements'), 'Must log in\nCart persists');
});

test('stream URL omits empty description and requirements', () => {
  const params = new URL(buildInvestigationStreamUrl({ url: 'http://x.test/' }), 'http://localhost').searchParams;
  assert.equal(params.has('description'), false);
  assert.equal(params.has('requirements'), false);
});

test('completed run is loaded by id from /api/runs/{run_id}', async () => {
  const requested = [];
  const fakeFetch = async (path) => {
    requested.push(path);
    return { ok: true, status: 200, json: async () => ({ run: { run_id: RUN_ID }, report: {}, test_cases: [] }) };
  };
  const detail = await fetchRun(RUN_ID, fakeFetch);
  assert.deepEqual(requested, [`/api/runs/${RUN_ID}`]);
  assert.equal(detail.run.run_id, RUN_ID);
});

test('fetchRun reports HTTP failures', async () => {
  const fakeFetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
  await assert.rejects(fetchRun(RUN_ID, fakeFetch), /HTTP 404/);
});

test('latest completed run skips running and failed runs and never reads committed report files', async () => {
  const requested = [];
  const fakeFetch = async (path) => {
    requested.push(path);
    if (path === '/api/runs') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          runs: [
            { run_id: 'inv-aaaaaaaaaaaa', status: 'running' },
            { run_id: 'inv-bbbbbbbbbbbb', status: 'failed' },
            { run_id: RUN_ID, status: 'completed' },
          ],
        }),
      };
    }
    return { ok: true, status: 200, json: async () => ({ run: { run_id: RUN_ID } }) };
  };
  const detail = await fetchLatestCompletedRun(fakeFetch);
  assert.equal(detail.run.run_id, RUN_ID);
  assert.deepEqual(requested, ['/api/runs', `/api/runs/${RUN_ID}`]);
  assert.ok(!requested.some((p) => p.includes('/api/reports')));
});

test('a blocked run is announced as unsuccessful, not completed', () => {
  const { tone, message } = completionMessage({
    run_id: RUN_ID,
    status: 'completed',
    report_status: 'blocked',
    report_outcome: 'unsuccessful',
    stopping_reason: 'All candidate actions (1) blocked by safety policy',
  });
  assert.equal(tone, 'danger');
  assert.match(message, /did not succeed \(status BLOCKED\)/);
  assert.match(message, /blocked by safety policy/);
  assert.doesNotMatch(message, / completed\.$/);
});

test('only a succeeded outcome is announced as a success', () => {
  assert.equal(completionMessage({ run_id: RUN_ID, report_outcome: 'succeeded' }).tone, 'success');
  assert.equal(completionMessage({ run_id: RUN_ID, report_outcome: 'limited', report_status: 'budget_exhausted' }).tone, 'warning');
  assert.equal(completionMessage({ run_id: RUN_ID }).tone, 'warning');
});

test('no completed runs yields null', async () => {
  const fakeFetch = async () => ({ ok: true, status: 200, json: async () => ({ runs: [] }) });
  assert.equal(await fetchLatestCompletedRun(fakeFetch), null);
});
