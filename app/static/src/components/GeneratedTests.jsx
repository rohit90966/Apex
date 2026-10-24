import React, { useEffect, useState } from 'react';

export default function GeneratedTests({
  testSuite,
  investigationReport,
  onExecuteSuite,
  onToast,
}) {
  const [categoryFilter, setCategoryFilter] = useState('ALL');
  const [searchTerm, setSearchTerm] = useState('');
  const [exportedScript, setExportedScript] = useState('');

  const testCases = testSuite?.test_cases || investigationReport?.proposed_test_cases || [];
  const runId = investigationReport?.run_id || testSuite?.run_id || '';
  const alignment = investigationReport?.test_planning?.requirement_alignment;

  // The script is produced server-side from exactly the steps and assertions the runner executes.
  useEffect(() => {
    let cancelled = false;
    setExportedScript('');
    if (!runId) return undefined;
    fetch(`/api/runs/${encodeURIComponent(runId)}/export`)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => { if (!cancelled) setExportedScript(text); })
      .catch((err) => { if (!cancelled) setExportedScript(`# Export unavailable for run ${runId}: ${err.message}`); });
    return () => { cancelled = true; };
  }, [runId, testCases.length]);

  // Category counts
  let wf = 0, trans = 0, func = 0, bnd = 0, a11y = 0;
  testCases.forEach((tc) => {
    const cat = (tc.category || tc.oracle_class || '').toLowerCase();
    const id = (tc.test_id || '').toLowerCase();
    if (cat.includes('workflow') || id.includes('wf') || id.includes('journey')) wf++;
    else if (cat.includes('transition') || id.includes('tr')) trans++;
    else if (cat.includes('boundary') || cat.includes('bva') || cat.includes('validation') || id.includes('val')) bnd++;
    else if (cat.includes('access') || cat.includes('a11y')) a11y++;
    else func++;
  });

  // Filter test cases
  const filtered = testCases.filter((tc) => {
    const cat = (tc.category || tc.oracle_class || '').toLowerCase();
    const id = (tc.test_id || '').toLowerCase();
    const isWf = cat.includes('workflow') || id.includes('wf') || id.includes('journey');
    const isTr = cat.includes('transition') || id.includes('tr');
    const isBnd = cat.includes('boundary') || cat.includes('bva') || cat.includes('validation') || id.includes('val');
    const isA11y = cat.includes('access') || cat.includes('a11y');

    const matchesCat =
      categoryFilter === 'ALL' ||
      (categoryFilter === 'workflow' && isWf) ||
      (categoryFilter === 'state_transition' && isTr) ||
      (categoryFilter === 'boundary' && isBnd) ||
      (categoryFilter === 'accessibility' && isA11y) ||
      (categoryFilter === 'functional' && !isWf && !isTr && !isBnd && !isA11y);

    const search = searchTerm.toLowerCase();
    const matchesSearch =
      !search ||
      `${tc.test_id || ''} ${tc.name || tc.title || ''} ${tc.objective || ''}`.toLowerCase().includes(search);

    return matchesCat && matchesSearch;
  });

  const playwrightScript = testSuite?.playwright_code || exportedScript
    || (runId ? '# Loading exported test script...' : '# The Playwright export is available once the investigation run has been saved.');

  const handleCopyCode = () => {
    navigator.clipboard.writeText(playwrightScript);
    onToast('Playwright Python test suite copied to clipboard!', 'success');
  };

  const handleDownloadScript = () => {
    const blob = new Blob([playwrightScript], { type: 'text/x-python' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `verityqa_contract_suite_${Date.now()}.py`;
    a.click();
    URL.revokeObjectURL(url);
    onToast('Downloaded Playwright test script', 'success');
  };

  return (
    <div className="view-panel">
      <div className="view-header">
        <div>
          <div className="results-title-row">
            <h1 className="view-title">Generated Contract Test Suite</h1>
            <span className="badge badge-emerald">{testCases.length} Test Cases</span>
          </div>
          <p className="view-subtitle">
            Empirically derived test cases with observable preconditions, actions, and evidence traceability.
          </p>
        </div>
        <div className="view-actions">
          <button type="button" className="btn btn-outline" onClick={handleCopyCode}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
            Copy Playwright Code
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onExecuteSuite(testCases)}>
            <span>▶ Execute Tests via Playwright</span>
          </button>
        </div>
      </div>

      {/* Requirement alignment: which supplied requirements map to something observed on this target */}
      {alignment && alignment.requirements && alignment.requirements.length > 0 && (
        <div className="card" style={{ marginBottom: '12px', borderLeft: `4px solid ${alignment.warning ? 'var(--color-warning)' : 'var(--accent-emerald)'}` }}>
          <div style={{ fontWeight: 600 }}>
            Requirement alignment: {alignment.matched} matched, {alignment.weak} weak, {alignment.unmatched} unmatched
          </div>
          {alignment.warning && <div style={{ color: 'var(--color-warning)', marginTop: '4px' }}>{alignment.warning}</div>}
          <ul style={{ margin: '6px 0 0 16px', fontSize: '12px' }}>
            {alignment.requirements.map((r, i) => (
              <li key={i}><strong>{r.status}</strong> — {r.requirement} <span className="text-muted">({r.evidence})</span></li>
            ))}
          </ul>
        </div>
      )}

      {/* Filter Bar */}
      <div className="filter-bar">
        <div className="category-chips">
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'ALL' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('ALL')}
          >
            All Categories ({testCases.length})
          </button>
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'workflow' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('workflow')}
          >
            Workflow ({wf})
          </button>
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'state_transition' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('state_transition')}
          >
            State Transition ({trans})
          </button>
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'functional' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('functional')}
          >
            Functional ({func})
          </button>
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'boundary' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('boundary')}
          >
            Boundary ({bnd})
          </button>
          <button
            type="button"
            className={`cat-chip ${categoryFilter === 'accessibility' ? 'active' : ''}`}
            onClick={() => setCategoryFilter('accessibility')}
          >
            Accessibility ({a11y})
          </button>
        </div>
        <div className="filter-search">
          <input
            type="text"
            className="form-input form-input-sm"
            placeholder="Search test name, ID, objective..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
      </div>

      {/* Two Column Layout: Catalog & Playwright Code */}
      <div className="test-suite-layout">
        {/* Left: Test Cases Cards */}
        <div className="test-catalog-col">
          {filtered.length > 0 ? (
            filtered.map((tc, idx) => {
              const id = tc.test_id || `TC-${idx + 1}`;
              const name = tc.name || tc.title || 'Contract Test';
              const cat = tc.category || 'functional';
              const pri = (tc.priority || 'MEDIUM').toUpperCase();
              const source = tc.expected_behavior_source || 'OBSERVED_INVARIANT';
              const isRequirement = source === 'REQUIREMENT';
              const isExploratory = source === 'INFERRED' || source === 'HYPOTHESIS';

              return (
                <div key={id} className="tc-card">
                  <div className="tc-header">
                    <div className="tc-title-wrap">
                      <span className="tc-id">{id}</span>
                      <span className="tc-name">{name}</span>
                    </div>
                    <div className="tc-tags">
                      <span className="source-tag">{cat}</span>
                      <span className={`pri-${pri}`}>{pri}</span>
                      {isRequirement && <span className="badge badge-emerald">REQUIREMENT</span>}
                      {isExploratory && <span className="badge badge-amber">EXPLORATORY HYPOTHESIS</span>}
                    </div>
                  </div>
                  <div className="tc-body">
                    <div><strong>Objective:</strong> {tc.objective || 'Verify behavior'}</div>
                    {tc.action_description && <div><strong>Action:</strong> {tc.action_description}</div>}
                    <div><strong>Expected:</strong> {tc.expected_outcome || tc.expected || 'Conforms to invariants'}</div>

                    {/* Preconditions */}
                    {tc.preconditions && tc.preconditions.length > 0 && (
                      <div style={{ marginTop: '4px', fontSize: '11px', color: 'var(--text-muted)' }}>
                        <strong>Preconditions:</strong> {tc.preconditions.join('; ')}
                      </div>
                    )}

                    {/* Step Sequence */}
                    {tc.steps && tc.steps.length > 0 && (
                      <div style={{ marginTop: '8px' }}>
                        <strong style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                          Action Sequence ({tc.steps.length} steps):
                        </strong>
                        <div style={{ marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                          {tc.steps.map((s, sIdx) => (
                            <div key={sIdx} className="mono" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                              {s.step_number || sIdx + 1}. <span style={{ color: 'var(--accent-cyan)' }}>{s.action_type || s.action}</span> <code>{s.selector || s.target || ''}</code>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Empirical Evidence IDs */}
                    {tc.evidence_ids && tc.evidence_ids.length > 0 && (
                      <div style={{ marginTop: '6px', fontSize: '11px', color: 'var(--text-muted)' }}>
                        <strong>Empirical Evidence:</strong> <code className="mono" style={{ color: 'var(--accent-emerald)' }}>{tc.evidence_ids.join(', ')}</code>
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="text-center text-muted p-20">No test cases match filter.</div>
          )}
        </div>

        {/* Right: Code Viewer */}
        <div className="code-viewer-col">
          <div className="card sticky-code-card">
            <div className="card-header">
              <div>
                <h3 className="card-title">Playwright Python Test Suite</h3>
                <p className="card-subtitle">Self-contained executable test runner code</p>
              </div>
              <button type="button" className="btn btn-xs btn-outline" onClick={handleDownloadScript}>
                ⬇ Download .py
              </button>
            </div>
            <div className="code-editor-box">
              <pre className="code-block">
                <code>{playwrightScript}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
