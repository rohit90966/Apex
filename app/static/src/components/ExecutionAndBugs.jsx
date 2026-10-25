import React, { useState } from 'react';
import {
  DISPLAY_STATUSES,
  NO_DATA,
  bugFindings,
  displayExecutionStatus,
  executionStatusBadgeClass,
  executionStatusCounts,
} from '../runDisplay.mjs';

export default function ExecutionAndBugs({
  investigationReport,
  executionReport,
  onRunTestSuite,
  executingTests,
  canExecute,
  onSelectTab,
  onViewScreenshot,
  findings
}) {
  const [selectedResult, setSelectedResult] = useState(null);
  const [filterStatus, setFilterStatus] = useState('ALL');

  const results = executionReport?.results || [];
  const counts = executionStatusCounts(executionReport);
  const { verified, unverified } = bugFindings(investigationReport, executionReport);

  const filteredResults = results.filter(r => filterStatus === 'ALL' || displayExecutionStatus(r.status) === filterStatus);

  const metricCards = [
    { label: 'Attempted', value: executionReport ? results.length : NO_DATA, sub: 'Test cases started', color: null },
    { label: 'Assertion reached', value: executionReport ? results.filter((r) => (r.assertions_evaluated || 0) > 0).length : NO_DATA, sub: 'Setup succeeded and the test's own assertions ran', color: null },
    { label: 'Pass', value: executionReport ? counts.PASS : NO_DATA, sub: 'All stated assertions satisfied', color: 'var(--color-success)' },
    { label: 'Fail', value: executionReport ? counts.FAIL : NO_DATA, sub: 'An assertion was not satisfied', color: 'var(--color-danger)' },
    { label: 'Error', value: executionReport ? counts.ERROR : NO_DATA, sub: 'Runner, locator, or environment error', color: 'var(--color-warning)' },
    { label: 'Blocked', value: executionReport ? counts.BLOCKED : NO_DATA, sub: 'A prerequisite (login, navigation, credentials) failed before the assertion', color: null },
    { label: 'Invalid', value: executionReport ? counts.INVALID : NO_DATA, sub: 'Test case not executable as defined', color: null },
    { label: 'Incomplete', value: executionReport ? counts.INCOMPLETE : NO_DATA, sub: 'Ran but verified nothing', color: null },
  ];

  return (
    <div className="tab-pane active fade-in">
      {/* Header and Controls */}
      <div className="panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 600 }}>Test Suite Execution & Bug Verification</h2>
            <p style={{ margin: '0.25rem 0 0 0', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Results of executing this run's generated test cases with the Playwright runner. Execution is started manually.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              className="btn btn-primary"
              onClick={onRunTestSuite}
              disabled={executingTests || !canExecute}
              id="btn-execute-tests"
            >
              {executingTests ? (
                <>
                  <span className="spinner" style={{ width: '14px', height: '14px', marginRight: '6px', display: 'inline-block' }}></span>
                  Executing Suite...
                </>
              ) : (
                <>▶ Run Generated Test Suite</>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Unified findings (investigation + execution), persisted with the run */}
      {findings && (
        <div className="panel" id="findings-panel" style={{ marginBottom: '1.5rem' }}>
          <h3 style={{ margin: '0 0 0.5rem 0' }}>Findings ({findings.findings.length})</h3>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
            {Object.entries(findings.counts || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'No reproduced findings for this run.'}
          </div>
          {findings.findings.length > 0 && (
            <table className="data-table" style={{ width: '100%', fontSize: '0.85rem' }}>
              <thead>
                <tr><th>Classification</th><th>Finding</th><th>Where</th><th>Evidence sources</th><th>Regression tests</th></tr>
              </thead>
              <tbody>
                {findings.findings.map((f) => (
                  <tr key={f.finding_id} data-finding-id={f.finding_id}>
                    <td><span className={`badge ${f.classification === 'CONFIRMED_DEFECT' ? 'badge-danger' : f.classification === 'LIKELY_DEFECT' ? 'badge-amber' : 'badge-neutral'}`}>{f.classification}</span></td>
                    <td>{f.title}</td>
                    <td className="mono">{f.route} {f.subject}</td>
                    <td>{f.sources.map((s) => `${s.kind}: ${s.classification}`).join('; ')}{f.observations && f.observations.length ? ` (+${f.observations.length} later observation(s))` : ''}</td>
                    <td className="mono">{(f.regression_test_ids || []).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Metrics Row */}
      <div className="metrics-grid" style={{ marginBottom: '1.5rem' }}>
        {metricCards.map(card => (
          <div key={card.label} className="metric-card" style={card.color ? { borderLeft: `4px solid ${card.color}` } : undefined}>
            <div className="metric-label">{card.label}</div>
            <div className="metric-value" style={card.color ? { color: card.color } : undefined}>{card.value}</div>
            <div className="metric-sub">{card.sub}</div>
          </div>
        ))}
      </div>

      {/* Verified findings */}
      <div className="panel" id="verified-findings" style={{ marginBottom: '1.5rem', border: verified.length > 0 ? '1px solid rgba(239, 68, 68, 0.4)' : undefined }}>
        <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem', color: verified.length > 0 ? 'var(--color-danger)' : 'inherit' }}>
              <span>🚨</span> Verified Findings ({verified.length})
            </h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Confirmed by the investigation's reproducer, or by repeated assertion failures during test execution.
            </span>
          </div>
        </div>

        {verified.length === 0 ? (
          <div className="empty-state" style={{ padding: '2rem 1rem' }}>
            <p style={{ margin: 0, fontWeight: 500 }}>No verified findings</p>
            <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Unverified leads, if any, are listed below and are not bugs until reproduced.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1rem' }}>
            {verified.map((bug, idx) => (
              <div
                key={bug.id || idx}
                className="card verified-finding"
                style={{ border: '1px solid var(--border-color)', background: 'rgba(239, 68, 68, 0.04)', padding: '1.25rem' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem', flexWrap: 'wrap' }}>
                  <span className="badge badge-danger" style={{ fontWeight: 700 }}>{bug.id || `FINDING-${idx + 1}`}</span>
                  <span className="badge badge-neutral">{bug.verdict}</span>
                  <span className="badge badge-neutral">{bug.source}</span>
                  {bug.severity && <span className="badge badge-warning">{bug.severity}</span>}
                  {bug.reproduction && <span className="badge badge-neutral">Reproduction: {bug.reproduction}</span>}
                </div>
                <h4 style={{ margin: '0.5rem 0', fontSize: '1.05rem', color: 'var(--color-danger)' }}>{bug.title || NO_DATA}</h4>
                {bug.detail && <p style={{ margin: 0, fontSize: '0.9rem' }}>{bug.detail}</p>}
                {bug.evidence_ids && bug.evidence_ids.length > 0 && (
                  <div style={{ marginTop: '0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    Evidence: <code className="mono">{bug.evidence_ids.join(', ')}</code>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Unverified leads */}
      <div className="panel" id="unverified-leads" style={{ marginBottom: '1.5rem' }}>
        <div className="panel-header">
          <div>
            <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span>⚖️</span> Unverified Leads ({unverified.length})
            </h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Candidate anomalies that were rejected or could not be reproduced. These are not confirmed bugs.
            </span>
          </div>
        </div>

        {unverified.length === 0 ? (
          <div style={{ padding: '1rem', color: 'var(--text-muted)', fontSize: '0.9rem', textAlign: 'center' }}>
            No unverified leads.
          </div>
        ) : (
          <div className="table-container" style={{ maxHeight: '240px', overflowY: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Source</th>
                  <th>Verdict</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {unverified.map((item, idx) => (
                  <tr key={item.id || idx}>
                    <td style={{ fontWeight: 500 }}>{item.title || NO_DATA}</td>
                    <td style={{ fontSize: '0.85rem' }}>{item.source}</td>
                    <td><span className="badge badge-neutral">{item.verdict}</span></td>
                    <td style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{item.reason || 'No reason recorded'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Test Execution Results Table */}
      <div className="panel">
        <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
          <div>
            <h3 style={{ margin: 0 }}>Test Execution Results</h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              {executionReport?.execution_id
                ? `Execution ${executionReport.execution_id}${executionReport.run_id ? ` for run ${executionReport.run_id}` : ''}`
                : 'Per-test status, assertion counts, and step traces.'}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            {['ALL', ...DISPLAY_STATUSES].map(st => (
              <button
                key={st}
                className={`btn btn-sm ${filterStatus === st ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setFilterStatus(st)}
              >
                {st}
              </button>
            ))}
          </div>
        </div>

        {results.length === 0 ? (
          <div className="empty-state" style={{ padding: '3rem 1rem' }}>
            <div style={{ fontSize: '2.5rem', marginBottom: '0.5rem' }}>🧪</div>
            <p style={{ margin: 0, fontWeight: 500 }}>No execution results yet</p>
            <p style={{ margin: '0.25rem 0 1rem 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              {canExecute
                ? 'Run the generated test suite to execute this run\'s test cases.'
                : 'Complete an investigation with generated test cases to enable execution.'}
            </p>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Test ID</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Assertions</th>
                  <th>Duration</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredResults.map((res, rIdx) => {
                  const isExpanded = selectedResult?.test_id === res.test_id;
                  return (
                    <React.Fragment key={res.test_id || rIdx}>
                      <tr
                        className="execution-result-row"
                        style={{ cursor: 'pointer', background: isExpanded ? 'rgba(99, 102, 241, 0.08)' : undefined }}
                        onClick={() => setSelectedResult(isExpanded ? null : res)}
                      >
                        <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{res.test_id}</td>
                        <td>{res.test_name || NO_DATA}</td>
                        <td>
                          <span className={`badge ${executionStatusBadgeClass(res.status)}`} title={res.status}>
                            {displayExecutionStatus(res.status)}
                          </span>
                        </td>
                        <td style={{ fontSize: '0.85rem' }}>
                          {res.assertions_satisfied ?? 0} / {res.assertions_evaluated ?? 0}
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                          {res.duration_seconds != null ? `${Number(res.duration_seconds).toFixed(2)}s` : NO_DATA}
                        </td>
                        <td>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedResult(isExpanded ? null : res);
                            }}
                          >
                            {isExpanded ? 'Hide Details' : 'View Trace'}
                          </button>
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr>
                          <td colSpan="6" style={{ padding: '1rem', background: 'rgba(0, 0, 0, 0.2)' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                              {res.failure_reason && (
                                <div style={{
                                  padding: '0.75rem',
                                  background: 'rgba(239, 68, 68, 0.1)',
                                  borderLeft: '4px solid var(--color-danger)',
                                  borderRadius: '4px',
                                  fontFamily: 'monospace',
                                  fontSize: '0.85rem',
                                  color: '#fca5a5'
                                }}>
                                  <strong>{displayExecutionStatus(res.status)}:</strong> {res.failure_reason}
                                </div>
                              )}

                              {res.step_results && res.step_results.length > 0 && (
                                <div>
                                  <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem', color: 'var(--text-muted)' }}>
                                    STEP EXECUTION TRACE:
                                  </div>
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                    {res.step_results.map((st, idx) => (
                                      <div
                                        key={idx}
                                        style={{
                                          display: 'flex',
                                          justifyContent: 'space-between',
                                          alignItems: 'center',
                                          padding: '0.4rem 0.6rem',
                                          background: 'rgba(255, 255, 255, 0.03)',
                                          borderRadius: '4px',
                                          fontSize: '0.85rem'
                                        }}
                                      >
                                        <div>
                                          <span style={{ color: 'var(--text-muted)', marginRight: '0.5rem' }}>#{st.step_number ?? idx + 1}</span>
                                          <strong>{st.action}</strong>: {st.description || NO_DATA}
                                          {st.assertion_expected && (
                                            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                              Expected: {st.assertion_expected}{st.assertion_actual ? ` | Actual: ${st.assertion_actual}` : ''}
                                            </div>
                                          )}
                                          {st.error_message && (
                                            <div style={{ fontSize: '0.8rem', color: '#fca5a5' }}>{st.error_message}</div>
                                          )}
                                        </div>
                                        <span className={`badge ${executionStatusBadgeClass(st.status)}`} style={{ fontSize: '0.75rem' }}>
                                          {displayExecutionStatus(st.status)}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {res.final_screenshot && (
                                <button
                                  className="btn btn-secondary btn-sm"
                                  style={{ alignSelf: 'flex-start' }}
                                  onClick={() => onViewScreenshot(`/artifacts/${encodeURIComponent(res.final_screenshot)}`)}
                                >
                                  📸 View Final Screenshot
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
