import React, { useState } from 'react';

export default function ExecutionAndBugs({
  investigationReport,
  executionReport,
  onRunTestSuite,
  executingTests,
  onSelectTab,
  onViewScreenshot
}) {
  const [selectedResult, setSelectedResult] = useState(null);
  const [filterStatus, setFilterStatus] = useState('ALL');
  const [selectedBug, setSelectedBug] = useState(null);

  const results = executionReport?.results || [];
  const summary = executionReport?.summary || {};
  
  // Confirmed defects and skeptic rejected leads from investigation report and execution
  const confirmedBugs = investigationReport?.verified_defects || 
                        investigationReport?.confirmed_bugs || 
                        executionReport?.confirmed_bugs || [];

  const skepticRejections = investigationReport?.skeptic_rejections || 
                           investigationReport?.rejected_leads || [];

  const filteredResults = results.filter(r => {
    if (filterStatus === 'ALL') return true;
    return r.status?.toUpperCase() === filterStatus;
  });

  const total = summary.total_tests ?? results.length;
  const passed = summary.passed ?? results.filter(r => r.status === 'PASSED').length;
  const failed = summary.failed ?? results.filter(r => r.status === 'FAILED').length;
  const errors = summary.errors ?? results.filter(r => r.status === 'ERROR' || r.status === 'SETUP_FAILURE').length;
  const validityRate = summary.assertion_validity_rate != null 
    ? `${(summary.assertion_validity_rate * 100).toFixed(1)}%` 
    : (total > 0 ? `${((passed / total) * 100).toFixed(1)}%` : 'N/A');

  return (
    <div className="tab-pane active fade-in">
      {/* Header and Controls */}
      <div className="panel" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 600 }}>Test Suite Execution & Bug Verification</h2>
            <p style={{ margin: '0.25rem 0 0 0', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              Real Playwright runner execution results, assertion validity metrics, and skeptic-filtered defect verifications.
            </p>
          </div>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            <button
              className="btn btn-primary"
              onClick={onRunTestSuite}
              disabled={executingTests || !investigationReport}
              id="btn-execute-tests"
            >
              {executingTests ? (
                <>
                  <span className="spinner" style={{ width: '14px', height: '14px', marginRight: '6px', display: 'inline-block' }}></span>
                  Executing Suite...
                </>
              ) : (
                <>▶ Run Synthesized Test Suite</>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Metrics Row */}
      <div className="metrics-grid" style={{ marginBottom: '1.5rem' }}>
        <div className="metric-card">
          <div className="metric-label">Total Executed</div>
          <div className="metric-value">{total}</div>
          <div className="metric-sub">Synthesized contract tests</div>
        </div>
        <div className="metric-card" style={{ borderLeft: '4px solid var(--color-success)' }}>
          <div className="metric-label">Passed Tests</div>
          <div className="metric-value" style={{ color: 'var(--color-success)' }}>{passed}</div>
          <div className="metric-sub">Verified assertions</div>
        </div>
        <div className="metric-card" style={{ borderLeft: '4px solid var(--color-danger)' }}>
          <div className="metric-label">Defects / Failed</div>
          <div className="metric-value" style={{ color: 'var(--color-danger)' }}>{failed}</div>
          <div className="metric-sub">Assertion or flow failures</div>
        </div>
        <div className="metric-card" style={{ borderLeft: '4px solid var(--color-warning)' }}>
          <div className="metric-label">Setup / Errors</div>
          <div className="metric-value" style={{ color: 'var(--color-warning)' }}>{errors}</div>
          <div className="metric-sub">Precondition misses</div>
        </div>
        <div className="metric-card" style={{ borderLeft: '4px solid var(--color-primary)' }}>
          <div className="metric-label">Assertion Validity</div>
          <div className="metric-value" style={{ color: 'var(--color-primary)' }}>{validityRate}</div>
          <div className="metric-sub">Empirically grounded</div>
        </div>
      </div>

      {/* Confirmed Defects Section (Top Priority for QA Engineer) */}
      <div className="panel" style={{ marginBottom: '1.5rem', border: confirmedBugs.length > 0 ? '1px solid rgba(239, 68, 68, 0.4)' : undefined }}>
        <div className="panel-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem', color: confirmedBugs.length > 0 ? 'var(--color-danger)' : 'inherit' }}>
              <span>🚨</span> Confirmed Application Defects ({confirmedBugs.length})
            </h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Verified through 2-of-2 independent reproduction runs in fresh, isolated browser contexts.
            </span>
          </div>
          {confirmedBugs.length > 0 && (
            <span className="badge badge-danger">Verified Defects</span>
          )}
        </div>

        {confirmedBugs.length === 0 ? (
          <div className="empty-state" style={{ padding: '2rem 1rem' }}>
            <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🛡️</div>
            <p style={{ margin: 0, fontWeight: 500 }}>No Confirmed Defects Detected</p>
            <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              All executed actions conformed to expected invariants, or candidate anomalies were resolved as non-reproducible.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1rem' }}>
            {confirmedBugs.map((bug, idx) => {
              const bugId = bug.id || bug.defect_id || `BUG-${idx + 1}`;
              const title = bug.title || bug.headline || bug.summary || 'Unspecified Defect';
              const reproRate = bug.reproduction_rate || bug.repro_rate || '—';
              const severity = bug.severity || 'UNSPECIFIED';
              const steps = bug.reproduction_steps || bug.steps || [];
              const evidenceRefs = bug.evidence_ids || bug.evidence_references || [];
              const screenshotPath = bug.screenshot_path || bug.screenshot;

              return (
                <div 
                  key={bugId} 
                  className="card" 
                  style={{ 
                    border: '1px solid var(--border-color)', 
                    background: 'rgba(239, 68, 68, 0.04)',
                    padding: '1.25rem' 
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '0.5rem' }}>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                        <span className="badge badge-danger" style={{ fontWeight: 700 }}>{bugId}</span>
                        <span className={`badge badge-${severity === 'CRITICAL' ? 'danger' : 'warning'}`}>{severity}</span>
                        <span className="badge badge-neutral" title="Reproduced in fresh contexts">
                          🔄 Repro Rate: <strong>{reproRate}</strong>
                        </span>
                      </div>
                      <h4 style={{ margin: '0.5rem 0', fontSize: '1.05rem', color: 'var(--color-danger)' }}>
                        {title}
                      </h4>
                      <p style={{ margin: 0, fontSize: '0.9rem', color: 'var(--text-color)' }}>
                        {bug.description || bug.details || 'Anomaly confirmed through isolated reproduction runs.'}
                      </p>
                    </div>

                    {screenshotPath && (
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => onViewScreenshot(screenshotPath)}
                        style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}
                      >
                        📸 View Screenshot
                      </button>
                    )}
                  </div>

                  {/* Reproduction Steps */}
                  {steps.length > 0 && (
                    <div style={{ marginTop: '1rem' }}>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
                        REPRODUCTION RECIPE (MINIMIZED TRACE):
                      </div>
                      <ol style={{ margin: 0, paddingLeft: '1.25rem', fontSize: '0.85rem', color: 'var(--text-color)' }}>
                        {steps.map((st, sIdx) => (
                          <li key={sIdx} style={{ marginBottom: '0.25rem' }}>
                            {typeof st === 'string' ? st : `${st.action || st.type} on ${st.selector || st.target || 'target'}`}
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {/* Evidence IDs */}
                  {evidenceRefs.length > 0 && (
                    <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Evidence Traces:</span>
                      {evidenceRefs.map(eid => (
                        <span key={eid} className="badge badge-neutral" style={{ fontSize: '0.75rem', fontFamily: 'monospace' }}>
                          {eid}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Skeptic Rejection Ledger (Transparency & Zero-Hallucination Proof) */}
      <div className="panel" style={{ marginBottom: '1.5rem' }}>
        <div className="panel-header">
          <div>
            <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span>⚖️</span> Skeptic Filter Ledger ({skepticRejections.length})
            </h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Candidate anomalies rigorously rejected to eliminate false positives (e.g., non-reproducible 404 probes or transient errors).
            </span>
          </div>
        </div>

        {skepticRejections.length === 0 ? (
          <div style={{ padding: '1rem', color: 'var(--text-muted)', fontSize: '0.9rem', textAlign: 'center' }}>
            No candidate anomalies required skeptic rejection.
          </div>
        ) : (
          <div className="table-container" style={{ maxHeight: '240px', overflowY: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Lead / Symptom</th>
                  <th>Observed Location</th>
                  <th>Rejection Reason</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {skepticRejections.map((item, idx) => (
                  <tr key={idx}>
                    <td style={{ fontWeight: 500 }}>{item.lead || item.symptom || item.title || 'Candidate Lead'}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>{item.url || item.location || '-'}</td>
                    <td style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                      {item.rejection_reason || item.reason || 'No rejection reason recorded'}
                    </td>
                    <td>
                      <span className="badge badge-neutral" style={{ color: 'var(--color-warning)' }}>
                        REJECTED (FALSE POSITIVE)
                      </span>
                    </td>
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
            <h3 style={{ margin: 0 }}>Detailed Test Execution Logs</h3>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Inspect individual test run metrics, assertion evaluations, and browser logs.
            </span>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {['ALL', 'PASSED', 'FAILED', 'ERROR'].map(st => (
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
            <p style={{ margin: 0, fontWeight: 500 }}>No Test Execution Data Available</p>
            <p style={{ margin: '0.25rem 0 1rem 0', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              Click "Run Synthesized Test Suite" above to execute all generated contract tests against the application.
            </p>
            <button
              className="btn btn-primary"
              onClick={onRunTestSuite}
              disabled={executingTests || !investigationReport}
            >
              ▶ Run Synthesized Test Suite
            </button>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Test ID</th>
                  <th>Name / Intent</th>
                  <th>Status</th>
                  <th>Assertions</th>
                  <th>Duration</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredResults.map(res => {
                  const isExpanded = selectedResult?.test_id === res.test_id;
                  const statusClass = res.status === 'PASSED' 
                    ? 'badge-success' 
                    : res.status === 'FAILED' 
                      ? 'badge-danger' 
                      : 'badge-warning';

                  return (
                    <React.Fragment key={res.test_id || Math.random()}>
                      <tr 
                        style={{ cursor: 'pointer', background: isExpanded ? 'rgba(99, 102, 241, 0.08)' : undefined }}
                        onClick={() => setSelectedResult(isExpanded ? null : res)}
                      >
                        <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{res.test_id}</td>
                        <td>{res.name || res.test_name || 'Contract Test'}</td>
                        <td>
                          <span className={`badge ${statusClass}`}>
                            {res.status || 'UNKNOWN'}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: '0.85rem' }}>
                            {res.assertions_passed ?? 0} / {res.assertions_total ?? (res.assertions?.length || 0)}
                          </span>
                        </td>
                        <td style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                          {res.duration_ms != null ? `${(res.duration_ms / 1000).toFixed(2)}s` : (res.duration ? `${res.duration}s` : '-')}
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

                      {/* Expanded Details Row */}
                      {isExpanded && (
                        <tr>
                          <td colSpan="6" style={{ padding: '1rem', background: 'rgba(0, 0, 0, 0.2)' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                              {res.error_message && (
                                <div style={{ 
                                  padding: '0.75rem', 
                                  background: 'rgba(239, 68, 68, 0.1)', 
                                  borderLeft: '4px solid var(--color-danger)',
                                  borderRadius: '4px',
                                  fontFamily: 'monospace',
                                  fontSize: '0.85rem',
                                  color: '#fca5a5'
                                }}>
                                  <strong>Execution Failure:</strong> {res.error_message}
                                </div>
                              )}

                              {/* Action trace steps */}
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
                                          <span style={{ color: 'var(--text-muted)', marginRight: '0.5rem' }}>#{idx + 1}</span>
                                          <strong>{st.action || st.action_type || 'Action'}</strong>: {st.target_selector || st.target || st.url || '-'}
                                        </div>
                                        <span className={`badge ${st.status === 'PASSED' || st.success ? 'badge-success' : 'badge-danger'}`} style={{ fontSize: '0.75rem' }}>
                                          {st.status || (st.success ? 'PASSED' : 'FAILED')}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Assertion details */}
                              {res.assertions && res.assertions.length > 0 && (
                                <div>
                                  <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem', color: 'var(--text-muted)' }}>
                                    ASSERTION EVALUATIONS:
                                  </div>
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                    {res.assertions.map((ast, idx) => (
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
                                          <span style={{ color: 'var(--text-muted)', marginRight: '0.5rem' }}>#{idx + 1}</span>
                                          <span>{ast.type || 'ASSERTION'}: {ast.description || ast.expected || ast.field}</span>
                                          {ast.evidence_id && (
                                            <span className="badge badge-neutral" style={{ marginLeft: '0.5rem', fontSize: '0.7rem' }}>
                                              Ref: {ast.evidence_id}
                                            </span>
                                          )}
                                        </div>
                                        <span className={`badge ${ast.passed || ast.result === 'PASSED' ? 'badge-success' : 'badge-danger'}`} style={{ fontSize: '0.75rem' }}>
                                          {ast.passed || ast.result === 'PASSED' ? 'PASSED' : 'FAILED'}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
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
