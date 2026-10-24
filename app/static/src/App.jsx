import React, { useState, useEffect, useRef } from 'react';
import Overview from './components/Overview.jsx';
import Investigation from './components/Investigation.jsx';
import InvestigationResults from './components/InvestigationResults.jsx';
import GeneratedTests from './components/GeneratedTests.jsx';
import ExecutionAndBugs from './components/ExecutionAndBugs.jsx';
import Reports from './components/Reports.jsx';
import {
  buildInvestigationStreamUrl,
  completionMessage,
  createRunStreamHandler,
  fetchRun,
  fetchLatestCompletedRun,
} from './runEvents.mjs';
import { buildExecuteRequest } from './runDisplay.mjs';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [targetUrl, setTargetUrl] = useState('');
  const [requirements, setRequirements] = useState('');
  const [isInvestigating, setIsInvestigating] = useState(false);
  const [liveEvents, setLiveEvents] = useState([]);
  const [investigationReport, setInvestigationReport] = useState(null);
  const [generatedTests, setGeneratedTests] = useState(null);
  const [executingTests, setExecutingTests] = useState(false);
  const [executionReport, setExecutionReport] = useState(null);
  const [benchmarkRunning, setBenchmarkRunning] = useState(null);
  const [benchmarkResults, setBenchmarkResults] = useState(null);
  const [savedReports, setSavedReports] = useState([]);
  const [toast, setToast] = useState(null);
  const [selectedScreenshot, setSelectedScreenshot] = useState(null);

  const eventSourceRef = useRef(null);
  const activeRunIdRef = useRef(null);
  const streamFinishedRef = useRef(false);

  const showToast = (message, type = 'info') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast(null);
    }, 4500);
  };

  // Load saved reports list and the most recent persisted run on mount
  useEffect(() => {
    fetchSavedReports();
    loadLatestRun();
  }, []);

  const fetchSavedReports = async () => {
    try {
      const res = await fetch('/api/reports');
      if (res.ok) {
        const data = await res.json();
        setSavedReports(data.reports || []);
      }
    } catch (err) {
      console.warn('Failed to fetch saved reports list:', err);
    }
  };

  // Populates report and test-case state from the canonical GET /api/runs/{run_id} response.
  const applyRun = (detail) => {
    const testCases = detail.test_cases || [];
    const runId = detail.run?.run_id;
    setInvestigationReport(detail.report);
    if (detail.run?.target_url) setTargetUrl(detail.run.target_url);
    setGeneratedTests({ test_cases: testCases, python_code: null, run_id: runId });
    // Show the run's last persisted execution (or none), never one from a different run.
    setExecutionReport(detail.latest_execution || null);
    // The downloadable script comes from the server exporter (same steps/assertions the runner executes).
    if (runId && testCases.length > 0) {
      fetch(`/api/runs/${encodeURIComponent(runId)}/export`)
        .then((r) => (r.ok ? r.text() : null))
        .then((code) => {
          if (code) setGeneratedTests((prev) => (prev && prev.run_id === runId ? { ...prev, python_code: code } : prev));
        })
        .catch(() => {});
    }
  };

  const loadLatestRun = async () => {
    try {
      const detail = await fetchLatestCompletedRun();
      if (detail) applyRun(detail);
    } catch (err) {
      console.warn('Failed to load latest investigation run:', err);
    }
  };

  const extractTestsFromReport = (rep) => {
    if (!rep) return;
    
    // Synthesize tests or use existing generated tests from report
    let tests = [];
    if (rep.test_cases && rep.test_cases.length > 0) {
      tests = rep.test_cases;
    } else if (rep.inferred_journeys && rep.inferred_journeys.length > 0) {
      // Create test cases from inferred journeys
      tests = rep.inferred_journeys.map((j, idx) => ({
        test_id: `TC-JOURNEY-${idx + 1}`,
        name: j.name || `Autonomous Journey ${idx + 1}`,
        intent: j.description || j.intent || 'Verify journey integrity',
        target_url: j.target_url || rep.target_url || '',
        preconditions: j.preconditions || [],
        action_steps: (j.steps || []).map((s, sIdx) => ({
          step_id: `step_${sIdx + 1}`,
          action: s.action_type || s.action || 'click',
          target: s.target_selector || s.selector || s.target || 'target',
          value: s.input_value || s.value || '',
          description: s.description || `Execute ${s.action_type || 'action'}`
        })),
        assertions: (j.assertions || []).map((a, aIdx) => ({
          assertion_id: `ast_${aIdx + 1}`,
          type: a.type || 'state_verified',
          target: a.selector || a.target || 'body',
          expected: a.expected || 'visible',
          evidence_id: a.evidence_id || `ev_auto_${aIdx + 1}`
        }))
      }));
    }

    // Saved report files carry no run ID, so only a script stored with the report can be offered.
    setGeneratedTests({
      test_cases: tests,
      python_code: rep.generated_code || rep.playwright_code || null
    });
  };

  const loadSavedReport = async (filename) => {
    try {
      showToast(`Loading report ${filename}...`, 'info');
      const res = await fetch(`/api/reports/${encodeURIComponent(filename)}`);
      if (res.ok) {
        const data = await res.json();
        setInvestigationReport(data);
        if (data.target_url) setTargetUrl(data.target_url);
        extractTestsFromReport(data);
        showToast(`Loaded ${filename} successfully!`, 'success');
        setActiveTab('results');
      } else {
        showToast(`Failed to load ${filename}`, 'danger');
      }
    } catch (err) {
      console.error(err);
      showToast('Error loading report file', 'danger');
    }
  };

  const handleStartInvestigation = (params = {}) => {
    const url = (params.url || targetUrl || '').trim();
    if (!url) {
      showToast('Please provide a valid Target URL.', 'warning');
      return;
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    activeRunIdRef.current = null;
    streamFinishedRef.current = false;
    // A new run must never display the previous run's results as current.
    setInvestigationReport(null);
    setGeneratedTests(null);
    setExecutionReport(null);
    setIsInvestigating(true);
    setLiveEvents([]);
    showToast('Autonomous investigation started...', 'info');

    const streamUrl = buildInvestigationStreamUrl({
      url,
      description: params.description,
      requirements: params.requirements ?? requirements,
    });
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    const finishStream = () => {
      streamFinishedRef.current = true;
      es.close();
      setIsInvestigating(false);
    };

    const handleStreamMessage = createRunStreamHandler({
      onCreated: ({ runId }) => {
        activeRunIdRef.current = runId;
      },
      onProgress: ({ runId, event: evt }) => {
        if (runId) activeRunIdRef.current = runId;
        setLiveEvents(prev => [...prev, evt]);
      },
      onComplete: async ({ runId }) => {
        finishStream();
        try {
          const detail = await fetchRun(runId);
          applyRun(detail);
          const { message, tone } = completionMessage(detail.run);
          showToast(message, tone);
          setActiveTab('results');
        } catch (err) {
          showToast(`Investigation ${runId} completed, but its results could not be loaded: ${err.message}`, 'danger');
        }
      },
      onError: ({ runId, event: evt }) => {
        finishStream();
        showToast(`Investigation ${runId || ''} failed: ${evt.error || 'Unknown failure'}`, 'danger');
      },
      onUnknown: ({ diagnostic }) => {
        console.warn('[VerityQA] Ignored stream message:', diagnostic);
      },
    });
    es.onmessage = (event) => handleStreamMessage(event.data);

    es.onerror = () => {
      if (streamFinishedRef.current) return;
      finishStream();
      const runId = activeRunIdRef.current;
      if (!runId) {
        showToast('Could not start the investigation stream. The URL may be rejected by the safety policy, or the server is unavailable.', 'danger');
        return;
      }
      fetchRun(runId)
        .then((detail) => {
          if (detail.run.status === 'completed') {
            applyRun(detail);
            setActiveTab('results');
            const { message, tone } = completionMessage(detail.run);
            showToast(`Live stream closed. ${message}`, tone);
          } else {
            showToast(`Live stream closed; run ${runId} is ${detail.run.status}.`, 'warning');
          }
        })
        .catch((err) => showToast(`Live stream closed; run ${runId} could not be loaded: ${err.message}`, 'danger'));
    };
  };

  const handleStopInvestigation = () => {
    if (eventSourceRef.current) {
      streamFinishedRef.current = true;
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsInvestigating(false);
    const runId = activeRunIdRef.current;
    showToast(runId
      ? `Stopped following live progress. Run ${runId} continues on the server and will be saved when it finishes.`
      : 'Stopped following live progress.', 'warning');
  };

  const handleRunTestSuite = async () => {
    if (!investigationReport && (!generatedTests?.test_cases || generatedTests.test_cases.length === 0)) {
      showToast('No tests available to execute. Run an investigation first.', 'warning');
      return;
    }

    setExecutingTests(true);
    showToast('Executing Synthesized Test Suite via Playwright Runner...', 'info');
    setActiveTab('execution');

    try {
      const payload = buildExecuteRequest(generatedTests, targetUrl);

      const res = await fetch('/api/tests/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        const result = await res.json();
        setExecutionReport(result);
        showToast(`Suite Execution Complete: ${result.summary?.passed || 0} passed, ${result.summary?.failed || 0} failed.`, 'success');
      } else {
        const errText = await res.text();
        showToast(`Test execution failed: ${errText}`, 'danger');
      }
    } catch (err) {
      console.error(err);
      showToast(`Execution request error: ${err.message}`, 'danger');
    } finally {
      setExecutingTests(false);
    }
  };

  const handleRunBenchmark = async (fixtureKey) => {
    setBenchmarkRunning(fixtureKey);
    const endpoint = fixtureKey === 'fixture2' ? '/api/benchmark/fixture2' : '/api/benchmark';
    showToast(`Executing Benchmark ${fixtureKey}...`, 'info');

    try {
      const res = await fetch(endpoint, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setBenchmarkResults(data);
        showToast('Benchmark run completed successfully!', 'success');
      } else {
        const errText = await res.text();
        showToast(`Benchmark error: ${errText}`, 'danger');
      }
    } catch (err) {
      showToast(`Benchmark failure: ${err.message}`, 'danger');
    } finally {
      setBenchmarkRunning(null);
    }
  };

  return (
    <div className="app-container">
      {/* Toast Alert Notification */}
      {toast && (
        <div 
          className={`toast toast-${toast.type} fade-in`}
          style={{
            position: 'fixed',
            top: '1.25rem',
            right: '1.25rem',
            zIndex: 9999,
            padding: '0.75rem 1.25rem',
            borderRadius: '6px',
            backgroundColor: toast.type === 'success' ? '#059669' : toast.type === 'danger' ? '#dc2626' : toast.type === 'warning' ? '#d97706' : '#2563eb',
            color: '#fff',
            fontWeight: 500,
            boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem'
          }}
        >
          <span>{toast.type === 'success' ? '✓' : toast.type === 'danger' ? '✕' : 'ℹ'}</span>
          <span>{toast.message}</span>
        </div>
      )}

      {/* Screenshot Lightbox Modal */}
      {selectedScreenshot && (
        <div 
          className="modal-overlay" 
          onClick={() => setSelectedScreenshot(null)}
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 10000,
            padding: '2rem'
          }}
        >
          <div 
            className="modal-content"
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'relative',
              maxWidth: '90vw',
              maxHeight: '90vh',
              background: 'var(--bg-secondary)',
              borderRadius: '8px',
              overflow: 'hidden',
              boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.75)'
            }}
          >
            <div style={{ padding: '0.75rem 1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)' }}>
              <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>Investigation Screenshot Trace</span>
              <button 
                className="btn btn-secondary btn-sm"
                onClick={() => setSelectedScreenshot(null)}
              >
                ✕ Close
              </button>
            </div>
            <img 
              src={selectedScreenshot} 
              alt="Investigation Screenshot" 
              style={{ maxWidth: '100%', maxHeight: 'calc(90vh - 60px)', display: 'block', objectFit: 'contain' }}
            />
          </div>
        </div>
      )}

      {/* Main Top Header */}
      <header className="app-header">
        <div className="header-left">
          <div className="brand-logo" style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            <span style={{ fontSize: '1.75rem' }}>⚡</span>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="brand-title" style={{ fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                  VERITY<span style={{ color: 'var(--color-primary)' }}>QA</span>
                </span>
                <span className="badge badge-primary" style={{ fontSize: '0.65rem', textTransform: 'uppercase' }}>
                  AUTONOMOUS AI QA
                </span>
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                Observe · Understand · Model · Plan · Test · Verify
              </div>
            </div>
          </div>
        </div>

        <div className="header-right" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {isInvestigating && (
            <div className="investigation-indicator" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'rgba(99, 102, 241, 0.15)', padding: '0.35rem 0.75rem', borderRadius: '20px', border: '1px solid var(--color-primary)' }}>
              <span className="spinner" style={{ width: '12px', height: '12px' }}></span>
              <span style={{ fontSize: '0.8rem', color: 'var(--color-primary)', fontWeight: 600 }}>Autonomous Probe Active</span>
            </div>
          )}

          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span>Target:</span>
            <span style={{ color: 'var(--text-color)', fontFamily: 'monospace', fontWeight: 600 }}>
              {targetUrl ? new URL(targetUrl.startsWith('http') ? targetUrl : `https://${targetUrl}`).hostname : 'None'}
            </span>
          </div>
        </div>
      </header>

      {/* Navigation Tabs Bar */}
      <nav className="tab-nav">
        {[
          { id: 'overview', label: 'Overview', icon: '🏠' },
          { id: 'investigation', label: 'Investigation', icon: '🔍', badge: isInvestigating ? 'Active' : null },
          { id: 'results', label: 'Investigation Results', icon: '🗺️', count: investigationReport?.states?.length },
          { id: 'tests', label: 'Generated Tests', icon: '🧪', count: generatedTests?.test_cases?.length },
          { id: 'execution', label: 'Execution & Bug Findings', icon: '🚨', count: investigationReport?.verified_defects?.length || executionReport?.summary?.failed },
          { id: 'reports', label: 'Reports & Benchmarks', icon: '📊' }
        ].map(tab => (
          <button
            key={tab.id}
            className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span style={{ marginRight: '0.35rem' }}>{tab.icon}</span>
            <span>{tab.label}</span>
            {tab.badge && (
              <span className="badge badge-primary" style={{ marginLeft: '0.4rem', fontSize: '0.65rem' }}>
                {tab.badge}
              </span>
            )}
            {tab.count != null && tab.count > 0 && (
              <span className="badge badge-neutral" style={{ marginLeft: '0.4rem', fontSize: '0.65rem' }}>
                {tab.count}
              </span>
            )}
          </button>
        ))}
      </nav>

      {/* Main Tab View Router */}
      <main className="tab-content" style={{ padding: '1.5rem', maxWidth: '1440px', margin: '0 auto', width: '100%' }}>
        {activeTab === 'overview' && (
          <Overview
            investigationReport={investigationReport}
            generatedTests={generatedTests}
            executionReport={executionReport}
            savedReports={savedReports}
            onSelectTab={setActiveTab}
            onLoadReport={loadSavedReport}
            targetUrl={targetUrl}
          />
        )}

        {activeTab === 'investigation' && (
          <Investigation
            targetUrl={targetUrl}
            setTargetUrl={setTargetUrl}
            requirements={requirements}
            setRequirements={setRequirements}
            isInvestigating={isInvestigating}
            onStartInvestigation={handleStartInvestigation}
            onStopInvestigation={handleStopInvestigation}
            liveEvents={liveEvents}
          />
        )}

        {activeTab === 'results' && (
          <InvestigationResults
            investigationReport={investigationReport}
            onSelectTab={setActiveTab}
            onViewScreenshot={(src) => setSelectedScreenshot(src)}
          />
        )}

        {activeTab === 'tests' && (
          <GeneratedTests
            generatedTests={generatedTests}
            investigationReport={investigationReport}
            onExecuteSuite={handleRunTestSuite}
            onSelectTab={setActiveTab}
          />
        )}

        {activeTab === 'execution' && (
          <ExecutionAndBugs
            investigationReport={investigationReport}
            executionReport={executionReport}
            onRunTestSuite={handleRunTestSuite}
            executingTests={executingTests}
            onSelectTab={setActiveTab}
            onViewScreenshot={(src) => setSelectedScreenshot(src)}
          />
        )}

        {activeTab === 'reports' && (
          <Reports
            investigationReport={investigationReport}
            generatedTests={generatedTests}
            executionReport={executionReport}
            onRunBenchmark={handleRunBenchmark}
            benchmarkRunning={benchmarkRunning}
            benchmarkResults={benchmarkResults}
          />
        )}
      </main>
    </div>
  );
}
