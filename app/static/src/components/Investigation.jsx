import React, { useState } from 'react';

export default function Investigation({
  targetUrl,
  setTargetUrl,
  isInvestigating,
  onStartInvestigation,
  onStopInvestigation,
  liveEvents,
  currentStatus,
}) {
  const [description, setDescription] = useState(
    'E-commerce shopping application with authentication, inventory catalog, cart, and checkout funnel'
  );
  const [requirements, setRequirements] = useState(
    'Users must authenticate with standard credentials to view inventory\n' +
    'Adding an item to the cart must increment the cart badge count\n' +
    'Cart items must be preserved through the checkout funnel\n' +
    'Checkout step two requires valid first name, last name, and postal code'
  );
  const [useStreaming, setUseStreaming] = useState(true);

  const presets = [
    { label: 'SauceDemo E-Commerce', url: 'https://www.saucedemo.com/' },
    { label: 'ShopSphere Local Fixture (Port 8001)', url: 'http://127.0.0.1:8001/login' },
    { label: 'TaskDash Local Fixture (Port 8002)', url: 'http://127.0.0.1:8002' },
  ];

  const handlePreset = (url) => {
    setTargetUrl(url);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!targetUrl.trim()) return;
    onStartInvestigation({
      url: targetUrl.trim(),
      description: description.trim(),
      requirements: requirements.trim().split('\n').filter(Boolean),
      useStreaming,
    });
  };

  // Compute live statistics from events
  const probes = liveEvents.filter(e => e.type === 'PROBE_START' || e.type === 'PROBE_RESULT');
  const leads = liveEvents.filter(e => e.type === 'LEAD_DETECTED');
  const latestEvent = liveEvents.length > 0 ? liveEvents[liveEvents.length - 1] : null;

  return (
    <div className="view-panel">
      <div className="view-header">
        <div>
          <div className="results-title-row">
            <h1 className="view-title">Target Website Investigation</h1>
            {isInvestigating && (
              <span className="badge badge-emerald">
                <span className="status-pulse-dot" style={{ display: 'inline-block', marginRight: '6px' }} />
                PROBING LIVE
              </span>
            )}
          </div>
          <p className="view-subtitle">
            Autonomous exploration of application state graphs, authentication boundaries, and behavioral invariants.
          </p>
        </div>
      </div>

      <div className="investigation-setup-grid">
        {/* Left: Configuration Form */}
        <div className="card">
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="targetUrlInput" className="form-label">
                Target Application URL <span className="required">*</span>
              </label>
              <div className="input-with-icon">
                <span className="input-icon">🌐</span>
                <input
                  id="targetUrlInput"
                  type="text"
                  className="form-input mono"
                  placeholder="https://www.saucedemo.com/ or http://127.0.0.1:8001/login"
                  value={targetUrl}
                  onChange={(e) => setTargetUrl(e.target.value)}
                  disabled={isInvestigating}
                  required
                />
              </div>
              <div className="url-safety-hint">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg>
                <span>Protected by Safety Policy (External domains &amp; local demo fixtures only)</span>
              </div>
            </div>

            {/* Presets */}
            <div className="form-group">
              <span className="preset-label">Preset Environments:</span>
              <div className="preset-chips">
                {presets.map((p) => (
                  <button
                    key={p.url}
                    type="button"
                    className={`preset-chip ${targetUrl === p.url ? 'active' : ''}`}
                    onClick={() => handlePreset(p.url)}
                    disabled={isInvestigating}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="descriptionInput" className="form-label">
                Application Context (Optional Description)
              </label>
              <input
                id="descriptionInput"
                type="text"
                className="form-input"
                placeholder="Brief summary of domain and intended user journey"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={isInvestigating}
              />
            </div>

            <div className="form-group">
              <label htmlFor="requirementsInput" className="form-label">
                Testing Requirements &amp; Business Rules (One per line)
              </label>
              <textarea
                id="requirementsInput"
                className="form-textarea"
                rows={3}
                placeholder="e.g. Passwords must be validated on server-side&#10;Cart badge count matches added items"
                value={requirements}
                onChange={(e) => setRequirements(e.target.value)}
                disabled={isInvestigating}
              />
            </div>

            <div className="form-toggles-grid">
              <label className="toggle-control">
                <input
                  type="checkbox"
                  checked={useStreaming}
                  onChange={(e) => setUseStreaming(e.target.checked)}
                  disabled={isInvestigating}
                />
                <span className="toggle-box" />
                <span className="toggle-text">
                  <strong>⚡ Real-Time Live Activity Stream (SSE)</strong>
                  <small>Receive live browser events, state mutations, and probe verdicts</small>
                </span>
              </label>
            </div>

            {/* One primary action: Start Investigation */}
            <div className="investigation-actions">
              {!isInvestigating ? (
                <button type="submit" className="btn btn-primary btn-lg">
                  <span className="btn-icon">▶</span>
                  <span>Start Autonomous Investigation</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-danger btn-lg"
                  onClick={onStopInvestigation}
                >
                  <span className="btn-icon">⏹</span>
                  <span>Stop Active Investigation</span>
                </button>
              )}
            </div>
          </form>
        </div>

        {/* Right: Live Activity Stream / Status Panel */}
        <div className="card">
          <div className="card-header">
            <div>
              <h3 className="card-title">Live Exploration Activity</h3>
              <p className="card-subtitle">
                {isInvestigating ? 'Streaming live browser actions...' : 'Waiting for investigation trigger'}
              </p>
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <span className="badge badge-indigo">{probes.length} Probes</span>
              <span className="badge badge-rose">{leads.length} Leads</span>
            </div>
          </div>

          {/* Current State & Action Tracker */}
          {latestEvent && (
            <div style={{ marginBottom: '14px', padding: '10px 12px', background: 'var(--bg-card-elevated)', borderRadius: '6px' }}>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                Current Action / State:
              </div>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent-cyan)', marginTop: '2px' }}>
                {latestEvent.name || latestEvent.title || latestEvent.type}
              </div>
              <div className="mono" style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                {latestEvent.target || latestEvent.meta || ''}
              </div>
            </div>
          )}

          {/* Timeline Cards Container */}
          <div className="stream-events-timeline" style={{ maxHeight: '380px' }}>
            {liveEvents.length === 0 ? (
              <div className="stream-empty-state">
                <div className="empty-icon">🎯</div>
                <div className="empty-title">Ready for Target</div>
                <div className="empty-desc">
                  Click "Start Autonomous Investigation" to begin stateful exploration, discover interactive controls, and record verified transitions.
                </div>
              </div>
            ) : (
              liveEvents.slice().reverse().map((evt, idx) => {
                let badgeClass = 'badge-indigo';
                if (evt.verdict === 'MATCHED') badgeClass = 'badge-emerald';
                else if (evt.verdict === 'CONTRADICTED' || evt.type === 'LEAD_DETECTED') badgeClass = 'badge-rose';
                else if (evt.type === 'STOPPING') badgeClass = 'badge-amber';

                return (
                  <div key={idx} className={`stream-card event-${evt.type}`}>
                    <div>
                      <strong style={{ color: 'var(--text-primary)' }}>
                        {evt.name || evt.title || evt.type}
                      </strong>
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                        {evt.target ? `Target: ${evt.target} ` : ''}
                        {evt.predicted_outcome ? `• Expected: ${evt.predicted_outcome} ` : ''}
                        {evt.duration_ms ? `(${Math.round(evt.duration_ms)}ms)` : ''}
                        {evt.description || evt.meta || ''}
                      </div>
                    </div>
                    <span className={`badge ${badgeClass}`}>
                      {evt.verdict || evt.severity || evt.badge || evt.type}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
