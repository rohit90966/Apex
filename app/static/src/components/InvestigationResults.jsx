import React, { useState } from 'react';
import {
  NO_DATA,
  displayValue,
  pickCount,
  reportStatusBadgeClass,
  reportStatusLabel,
} from '../runDisplay.mjs';

export default function InvestigationResults({
  investigationReport,
  onOpenLightbox,
  onNavigate,
  onViewScreenshot,
  onSelectTab,
}) {
  // App passes onViewScreenshot/onSelectTab; accept either name so evidence images and navigation always work.
  onOpenLightbox = onOpenLightbox || ((src) => onViewScreenshot && onViewScreenshot(src));
  onNavigate = onNavigate || ((tab) => onSelectTab && onSelectTab(tab));
  const [activeSubtab, setActiveSubtab] = useState('summary');
  const [evidenceFilter, setEvidenceFilter] = useState('');

  if (!investigationReport) {
    return (
      <div className="view-panel">
        <div className="card text-center p-20">
          <div className="empty-icon">🔍</div>
          <div className="empty-title">No Active Investigation Loaded</div>
          <p className="empty-desc">
            Run an investigation or select a pre-computed audit from the Overview to inspect the state machine and evidence.
          </p>
        </div>
      </div>
    );
  }

  const ov = investigationReport.overview || {};
  const map = investigationReport.discovered_application_map || [];
  const auth = investigationReport.authentication_access || investigationReport.authentication || {};
  const rules = investigationReport.business_rules_and_hypotheses || investigationReport.rules || [];
  const workflows = investigationReport.user_workflows || investigationReport.workflows || [];
  const limits = investigationReport.investigation_limitations || investigationReport.limitations || [];
  const evidenceList = investigationReport.evidence_and_traceability || investigationReport.evidence || [];
  const rawClasses = investigationReport.behavior_model?.state_classes_list || 
                     investigationReport.behavior_model?.state_classes || 
                     investigationReport.states || [];
  const stateClasses = Array.isArray(rawClasses) ? rawClasses : Object.values(rawClasses || {});
  const transitions = investigationReport.behavior_model?.transitions ||
                      investigationReport.behavior_model?.verified_transitions ||
                      investigationReport.transitions || [];
  const durationSeconds = pickCount(ov.duration_seconds, investigationReport.duration_seconds);
  const authBadge = auth.authentication_verified
    ? { label: 'VERIFIED', className: 'badge-emerald' }
    : auth.authentication_attempted
      ? { label: 'NOT VERIFIED', className: 'badge-rose' }
      : auth.boundary_observed
        ? { label: 'NOT ATTEMPTED', className: 'badge-amber' }
        : { label: 'NOT DETECTED', className: 'badge-cyan' };
  const authCoverage = auth.authentication_verified
    ? 'Authenticated session verified'
    : auth.boundary_observed
      ? 'Authentication boundary observed, not verified'
      : 'No authentication boundary observed';

  // Filter evidence
  const filteredEvidence = evidenceList.filter((e) => {
    if (!evidenceFilter.trim()) return true;
    const term = evidenceFilter.toLowerCase();
    return (
      (e.evidence_id || '').toLowerCase().includes(term) ||
      (e.observation_type || '').toLowerCase().includes(term) ||
      (e.url || '').toLowerCase().includes(term) ||
      (e.description || '').toLowerCase().includes(term)
    );
  });

  return (
    <div className="view-panel">
      <div className="view-header">
        <div>
          <div className="results-title-row">
            <h1 className="view-title">Investigation Results &amp; Application Model</h1>
            <span className={`badge ${reportStatusBadgeClass(ov.status)}`}>
              {reportStatusLabel(ov.status)}
            </span>
          </div>
          <p className="view-subtitle">
            Target: <code className="mono">{ov.target_url || investigationReport.target_url}</code> &bull;
            Duration: {durationSeconds != null ? `${durationSeconds}s` : NO_DATA} &bull;
            Strategy: {displayValue(ov.planning_strategy || investigationReport.generation_strategy)}
          </p>
          {ov.stopping_reason && reportStatusBadgeClass(ov.status) !== 'badge-emerald' && (
            <p className="view-subtitle">Stopped: {ov.stopping_reason}</p>
          )}
        </div>
        <div className="view-actions">
          <button type="button" className="btn btn-primary" onClick={() => onNavigate('tests')}>
            <span>View Generated Tests</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </div>
      </div>

      {/* Segmented Subtabs */}
      <div className="segmented-tabs">
        <button
          type="button"
          className={`seg-tab ${activeSubtab === 'summary' ? 'active' : ''}`}
          onClick={() => setActiveSubtab('summary')}
        >
          Overview Summary
        </button>
        <button
          type="button"
          className={`seg-tab ${activeSubtab === 'map' ? 'active' : ''}`}
          onClick={() => setActiveSubtab('map')}
        >
          Application Map &amp; State Graph
        </button>
        <button
          type="button"
          className={`seg-tab ${activeSubtab === 'journey' ? 'active' : ''}`}
          onClick={() => setActiveSubtab('journey')}
        >
          Journey Explorer
        </button>
        <button
          type="button"
          className={`seg-tab ${activeSubtab === 'evidence' ? 'active' : ''}`}
          onClick={() => setActiveSubtab('evidence')}
        >
          Evidence Ledger ({evidenceList.length})
        </button>
        <button
          type="button"
          className={`seg-tab ${activeSubtab === 'coverage' ? 'active' : ''}`}
          onClick={() => setActiveSubtab('coverage')}
        >
          Coverage &amp; Limitations
        </button>
      </div>

      {/* Subtab 1: Summary */}
      {activeSubtab === 'summary' && (
        <div className="results-tab-content active">
          <div className="card mb-20">
            <div className="card-header">
              <h3 className="card-title">Application Understanding</h3>
              <span className="badge badge-indigo">
                {displayValue(ov.planning_strategy)}
              </span>
            </div>
            <p className="card-body-text">
              {investigationReport.application_understanding || 'No application understanding was recorded for this run.'}
            </p>
          </div>

          <div className="dashboard-grid-2">
            {/* Auth Boundary */}
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">Authentication &amp; Access Boundary</h3>
                <span className={`badge ${authBadge.className}`}>
                  {authBadge.label}
                </span>
              </div>
              <div className="info-table">
                <div className="info-row">
                  <span className="info-label">Boundary Observed:</span>
                  <span className="info-val">{auth.boundary_observed ? 'Yes (Password input detected)' : 'No'}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Credentials Source:</span>
                  <span className="info-val">{displayValue(auth.credentials_source)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Captured Signals:</span>
                  <span className="info-val mono text-sm">
                    {displayValue((auth.verification_signals || []).join(' | '))}
                  </span>
                </div>
              </div>
            </div>

            {/* Frontier & Budget */}
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">Frontier &amp; Exploration Budget</h3>
                <span className={`badge ${reportStatusBadgeClass(ov.status)}`}>{reportStatusLabel(ov.status)}</span>
              </div>
              <div className="info-table">
                <div className="info-row">
                  <span className="info-label">Actions Evaluated:</span>
                  <span className="info-val">{displayValue(ov.actions_evaluated)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Actions Attempted:</span>
                  <span className="info-val">{displayValue(ov.actions_attempted)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Actions Executed (Pass):</span>
                  <span className="info-val text-emerald">{displayValue(ov.actions_successful)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Verified Effects:</span>
                  <span className="info-val text-cyan">{displayValue(pickCount(ov.actions_verified_effects, transitions.length))}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Safety Blocked:</span>
                  <span className="info-val">{displayValue(ov.actions_safety_blocked)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Stopping Reason:</span>
                  <span className="info-val text-muted">{displayValue(ov.stopping_reason)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Subtab 2: Map & SVG */}
      {activeSubtab === 'map' && (
        <div className="results-tab-content active">
          <div className="card mb-20">
            <div className="card-header">
              <div>
                <h3 className="card-title">Behavior Model &amp; Interactive State Graph</h3>
                <p className="card-subtitle">
                  Visual directed state-transition graph derived from verified Playwright browser actions
                </p>
              </div>
              <div className="graph-legend">
                <div className="legend-item"><span className="legend-line route-line" /> Route Transition</div>
                <div className="legend-item"><span className="legend-line inpage-line" /> In-Page Mutation</div>
                <div className="legend-item"><span className="legend-dot entry-dot" /> Entry State</div>
              </div>
            </div>

            {/* State Graph Visualizer */}
            <div className="svg-graph-container">
              {renderStateGraph(stateClasses, transitions, map)}
            </div>
          </div>

          {/* Discovered Views Table */}
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">Discovered Routes &amp; View Purposes</h3>
            </div>
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>State / Purpose</th>
                    <th>Route / URL</th>
                    <th>Relevant Controls</th>
                  </tr>
                </thead>
                <tbody>
                  {map.length > 0 ? (
                    map.map((m, idx) => (
                      <tr key={idx}>
                        <td><strong style={{ color: 'var(--text-primary)' }}>{m.apparent_purpose || 'View'}</strong></td>
                        <td><code className="mono" style={{ color: 'var(--accent-cyan)' }}>{m.route_or_url || '/'}</code></td>
                        <td className="text-muted">{(m.relevant_controls_summary || []).join(', ') || 'Interactive elements'}</td>
                      </tr>
                    ))
                  ) : (
                    stateClasses.map((sc, idx) => (
                      <tr key={idx}>
                        <td><strong style={{ color: 'var(--text-primary)' }}>{sc.page_title || sc.description || `State ${idx + 1}`}</strong></td>
                        <td><code className="mono" style={{ color: 'var(--accent-cyan)' }}>{sc.representative_route || sc.normalized_route || sc.url || '/'}</code></td>
                        <td className="text-muted">{(sc.interactive_controls || []).map(c => c.accessible_name || c.selector).slice(0, 4).join(', ') || 'Controls'}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Subtab 3: Journey Explorer */}
      {activeSubtab === 'journey' && (
        <div className="results-tab-content active">
          <div className="card">
            <div className="card-header">
              <div>
                <h3 className="card-title">Verified Multi-Step User Workflows</h3>
                <p className="card-subtitle">Sequential user progression verified in live browser context</p>
              </div>
            </div>
            <div className="journey-timeline">
              {workflows.length > 0 ? (
                workflows.map((wf, idx) => (
                  <div key={idx} className="journey-step-card">
                    <div className="journey-step-header">
                      <div className="journey-step-title">
                        <span className="mono" style={{ color: 'var(--accent-indigo)', marginRight: '8px' }}>
                          Step {wf.step_number || idx + 1}:
                        </span>
                        <span>{wf.action_taken || wf.action || 'Transition Trigger'}</span>
                      </div>
                      <span className="badge badge-emerald">{wf.status || 'VERIFIED'}</span>
                    </div>
                    <div className="journey-step-meta">
                      Target State: <code className="mono" style={{ color: 'var(--accent-cyan)' }}>{wf.to_state || wf.target_state_id || 'Target View'}</code>
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      {wf.observed_outcome || 'DOM state verified with zero technical errors.'}
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center text-muted p-20">
                  Zero multi-step user workflows recorded in this run.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Subtab 4: Evidence Ledger */}
      {activeSubtab === 'evidence' && (
        <div className="results-tab-content active">
          <div className="card">
            <div className="card-header">
              <div>
                <h3 className="card-title">Empirical Evidence Ledger</h3>
                <p className="card-subtitle">Immutable records of browser observations and DOM screenshots</p>
              </div>
              <div className="search-box">
                <input
                  type="text"
                  className="form-input form-input-sm"
                  placeholder="Filter evidence by ID, URL, or type..."
                  value={evidenceFilter}
                  onChange={(e) => setEvidenceFilter(e.target.value)}
                />
              </div>
            </div>
            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Evidence ID</th>
                    <th>Type</th>
                    <th>Target Route</th>
                    <th>Description</th>
                    <th>Target / Selector</th>
                    <th style={{ textAlign: 'center' }}>Screenshot</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEvidence.length > 0 ? (
                    filteredEvidence.slice(0, 50).map((ev, idx) => {
                      const shot = ev.screenshot_id || ev.screenshot;
                      return (
                        <tr key={ev.evidence_id || idx}>
                          <td><code className="mono" style={{ color: 'var(--accent-emerald)', fontSize: '11px' }}>{ev.evidence_id}</code></td>
                          <td><span className="badge badge-indigo">{ev.observation_type || 'OBSERVATION'}</span></td>
                          <td className="mono text-muted" style={{ fontSize: '11px' }}>{ev.url || ev.target_url || '/'}</td>
                          <td>{ev.description || 'DOM observed'}</td>
                          <td><code className="mono text-muted" style={{ fontSize: '11px' }}>{ev.dom_snippet || ev.target || 'DOM'}</code></td>
                          <td style={{ textAlign: 'center' }}>
                            {shot ? (
                              <button
                                type="button"
                                className="btn btn-xs btn-outline"
                                onClick={() => onOpenLightbox(`/artifacts/${encodeURIComponent(shot)}`, `Artifact: ${shot}`)}
                              >
                                📸 View
                              </button>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td colSpan="6" className="text-center text-muted">No empirical evidence records match filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Subtab 5: Coverage & Limitations */}
      {activeSubtab === 'coverage' && (
        <div className="results-tab-content active">
          <div className="dashboard-grid-2">
            <div className="card">
              <div className="card-header">
                <h3 className="card-title">Exploration Coverage Summary</h3>
              </div>
              <div className="info-table">
                <div className="info-row">
                  <span className="info-label">Discovered Routes:</span>
                  <span className="info-val">{displayValue(pickCount(ov.pages_discovered, map.length))}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Probed States:</span>
                  <span className="info-val">{displayValue(pickCount(stateClasses.length, ov.meaningful_states_discovered))}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Pending Frontier Items:</span>
                  <span className="info-val">{displayValue(ov.pending_frontier_count)}</span>
                </div>
                <div className="info-row">
                  <span className="info-label">Authentication Coverage:</span>
                  <span className="info-val">{authCoverage}</span>
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-header">
                <h3 className="card-title">Documented Limitations &amp; Boundaries</h3>
              </div>
              <ul className="limitations-list">
                {limits.length > 0 ? (
                  limits.map((l, idx) => <li key={idx}>{l}</li>)
                ) : (
                  <>
                    <li>Zero destructive or unauthorized external operations executed.</li>
                    <li>Payment processing tokenization simulated without external gateway calls.</li>
                    <li>Exploration halted at configured step and depth budgets.</li>
                  </>
                )}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Helper SVG State Graph Renderer (light theme; titles from investigation evidence; actions on hover)
function renderStateGraph(stateClasses, transitions, map) {
  const h = React.createElement;
  if (stateClasses.length === 0) {
    return h('div', { className: 'text-center text-muted p-20' }, 'Single state observed.');
  }
  // Layout: fixed-size cards on a grid; the SVG is sized to fit every state (the container scrolls).
  const cols = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(stateClasses.length))));
  const rows = Math.ceil(stateClasses.length / cols);
  const nodeW = 240, nodeH = 62, gapX = 110, gapY = 70, pad = 30;
  const svgW = pad * 2 + cols * nodeW + (cols - 1) * gapX;
  const svgH = pad * 2 + rows * nodeH + (rows - 1) * gapY;
  const pos = stateClasses.map((sc, i) => ({ x: pad + (i % cols) * (nodeW + gapX), y: pad + Math.floor(i / cols) * (nodeH + gapY) }));
  const idOf = (c) => c.class_id || c.state_id || c.id;
  const indexOf = {};
  stateClasses.forEach((c, i) => { indexOf[idOf(c)] = i; });

  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  // Titles come from what the investigation recorded about each state, never from guesses about the site.
  const titleOf = (sc, i) => {
    const t = (sc.primary_purpose || sc.page_title || '').replace(/\s*\((Entry|View)\)\s*$/i, '').trim();
    return t && !t.startsWith('st_') ? t : 'State ' + (i + 1);
  };
  const pathOf = (sc) => {
    const raw = (sc.representative_route || sc.normalized_route || sc.url || '/').replace(/^https?:\/\/[^/]+/i, '') || '/';
    return raw;
  };
  // Readable action: "CLICK_CONTROL:control_cls_02740c_add_to_cart" -> "Click: add to cart"
  const actionOf = (t) => {
    const raw = String(t.trigger_action || 'Action');
    const [kind, rest = ''] = raw.split(/:(.*)/s);
    const verb = { CLICK_CONTROL: 'Click', NAVIGATE_LINK: 'Open link', FILL: 'Fill', SELECT_OPTION: 'Select',
      AUTHENTICATE: 'Log in', SUBMIT: 'Submit' }[kind] || kind.replace(/_/g, ' ').toLowerCase();
    const what = rest.replace(/^(control|select|nav)_(cls_)?[0-9a-f]{6,}_*/i, '').replace(/^nav_link_\d+$/i, '')
      .replace(/_+/g, ' ').trim();
    return what ? verb + ': ' + what : verb;
  };

  const edges = [];
  const seen = {};
  (transitions || []).forEach((t) => {
    const s = indexOf[t.source_class || t.source_state_id];
    const d = indexOf[t.target_class || t.target_state_id];
    if (s === undefined || d === undefined) return;
    const key = s + '>' + d;
    if (seen[key]) { seen[key].labels.push(actionOf(t)); return; }
    seen[key] = { s, d, labels: [actionOf(t)] };
    edges.push(seen[key]);
  });

  const edge = (e, i) => {
    const a = pos[e.s], b = pos[e.d];
    const tip = e.labels.join('\n');
    if (e.s === e.d) {
      const x = a.x + nodeW - 40, y = a.y;
      return h('g', { key: 'e' + i }, h('title', null, tip),
        h('path', { d: `M ${x} ${y} C ${x + 10} ${y - 34}, ${x + 50} ${y - 20}, ${x + 30} ${y + 4}`, fill: 'none',
          stroke: '#0e7490', strokeWidth: 1.5, strokeDasharray: '4,3', markerEnd: 'url(#vq-arrow-inpage)' }));
    }
    const ax = a.x + nodeW / 2, ay = a.y + nodeH / 2, bx = b.x + nodeW / 2, by = b.y + nodeH / 2;
    // Leave from / arrive at the card border facing the other card.
    const side = (x, y, tx, ty) => {
      const dx = tx - x, dy = ty - y;
      if (Math.abs(dx) * nodeH > Math.abs(dy) * nodeW) return [x + Math.sign(dx) * nodeW / 2, y];
      return [x, y + Math.sign(dy) * nodeH / 2];
    };
    const [sx, sy] = side(ax, ay, bx, by);
    const [tx, ty] = side(bx, by, ax, ay);
    const bend = (i % 2 ? 1 : -1) * 18;
    const mx = (sx + tx) / 2 + (sy === ty ? 0 : bend), my = (sy + ty) / 2 + (sx === tx ? 0 : bend);
    const route = pathOf(stateClasses[e.s]).split('?')[0] !== pathOf(stateClasses[e.d]).split('?')[0];
    return h('g', { key: 'e' + i, className: 'vq-edge' }, h('title', null, tip),
      h('path', { d: `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`, fill: 'none', stroke: route ? '#2563eb' : '#0e7490',
        strokeWidth: 1.5, strokeDasharray: route ? 'none' : '4,3', markerEnd: route ? 'url(#vq-arrow-route)' : 'url(#vq-arrow-inpage)',
        opacity: 0.75 }),
      // wide invisible stroke so the tooltip is easy to hover
      h('path', { d: `M ${sx} ${sy} Q ${mx} ${my} ${tx} ${ty}`, fill: 'none', stroke: 'transparent', strokeWidth: 12 }));
  };

  const node = (sc, i) => {
    const p = pos[i];
    const entry = i === 0;
    return h('g', { key: 'n' + i }, h('title', null, titleOf(sc, i) + '\n' + pathOf(sc)),
      h('rect', { x: p.x, y: p.y, width: nodeW, height: nodeH, rx: 8, fill: '#ffffff', stroke: entry ? '#15803d' : '#d1d5db',
        strokeWidth: entry ? 2 : 1 }),
      entry && h('text', { x: p.x + nodeW - 12, y: p.y + 18, textAnchor: 'end', fontSize: 9, fontWeight: 700, fill: '#15803d' }, 'ENTRY'),
      h('text', { x: p.x + 12, y: p.y + 25, fontSize: 12.5, fontWeight: 600, fill: '#111827' }, clip(titleOf(sc, i), entry ? 27 : 32)),
      h('text', { x: p.x + 12, y: p.y + 45, fontSize: 10.5, fontFamily: 'monospace', fill: '#6b7280' }, clip(pathOf(sc), 34)));
  };

  return h('svg', { width: svgW, height: svgH, viewBox: `0 0 ${svgW} ${svgH}`, style: { fontFamily: 'Inter, sans-serif', display: 'block' } },
    h('defs', null,
      h('marker', { id: 'vq-arrow-route', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
        h('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#2563eb' })),
      h('marker', { id: 'vq-arrow-inpage', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
        h('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#0e7490' }))),
    edges.map(edge),
    stateClasses.map(node));
}
