import React, { useState, useEffect, useRef } from 'react';
import Overview from './components/Overview.jsx';
import Investigation from './components/Investigation.jsx';
import InvestigationResults from './components/InvestigationResults.jsx';
import GeneratedTests from './components/GeneratedTests.jsx';
import ExecutionAndBugs from './components/ExecutionAndBugs.jsx';
import Reports from './components/Reports.jsx';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [targetUrl, setTargetUrl] = useState('https://www.saucedemo.com/');
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

  const showToast = (message, type = 'info') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast(null);
    }, 4500);
  };

  // Load saved reports list and latest report on mount
  useEffect(() => {
    fetchSavedReports();
    fetchLatestReport();
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

  const fetchLatestReport = async () => {
    try {
      const res = await fetch('/api/reports/latest');
      if (res.ok) {
        const data = await res.json();
        if (data && !data.error && data.status !== 'no_reports_found') {
          setInvestigationReport(data);
          if (data.target_url) setTargetUrl(data.target_url);
          extractTestsFromReport(data);
        }
      }
    } catch (err) {
      console.warn('Failed to fetch latest report:', err);
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
        target_url: j.target_url || rep.target_url || 'https://www.saucedemo.com/',
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

    setGeneratedTests({
      test_cases: tests,
      python_code: rep.generated_code || rep.playwright_code || generatePlaywrightCode(tests, rep.target_url)
    });
  };

  const generatePlaywrightCode = (tests, baseUrl) => {
    return `"""
VerityQA Synthesized Autonomous Playwright Test Suite
Generated from empirical evidence and state transitions.
Target: ${baseUrl || 'https://www.saucedemo.com/'}
"""
import pytest
from playwright.sync_api import sync_playwright, expect

@pytest.fixture(scope="session")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        yield context
        browser.close()

${tests.map((t, idx) => `
def test_${(t.name || `journey_${idx + 1}`).toLowerCase().replace(/[^a-z0-9]+/g, '_')}(browser_context):
    """
    Test ID: ${t.test_id}
    Intent: ${t.intent || 'Verify autonomous flow'}
    """
    page = browser_context.new_page()
    page.goto("${t.target_url || baseUrl || 'https://www.saucedemo.com/'}", wait_until="networkidle")
    
    # Step Execution Trace
    ${(t.action_steps || []).map(s => {
      if (s.action === 'fill' || s.action === 'input') {
        return `page.locator("${s.target}").fill("${s.value || 'standard_user'}")`;
      } else if (s.action === 'click') {
        return `page.locator("${s.target}").click()`;
      } else {
        return `# Step: ${s.action} on ${s.target}`;
      }
    }).join('\n    ')}
    
    # Empirical Assertions Grounded in Investigation Evidence
    ${(t.assertions || []).map(a => {
      return `expect(page.locator("${a.target}")).to_be_visible()  # Evidence: ${a.evidence_id || 'verified'}`;
    }).join('\n    ')}
    
    page.close()
`).join('\n')}`;
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

  const handleStartInvestigation = () => {
    if (!targetUrl.trim()) {
      showToast('Please provide a valid Target URL.', 'warning');
      return;
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    setIsInvestigating(true);
    setLiveEvents([]);
    showToast('Autonomous investigation started...', 'info');

    const streamUrl = `/api/investigate/stream?url=${encodeURIComponent(targetUrl.trim())}`;
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        
        if (msg.type === 'event' || msg.event) {
          const item = msg.data || msg.event;
          setLiveEvents(prev => [...prev, item]);
        } else if (msg.type === 'complete' || msg.status === 'completed') {
          const report = msg.data || msg.report;
          if (report) {
            setInvestigationReport(report);
            extractTestsFromReport(report);
          }
          setIsInvestigating(false);
          showToast('Autonomous Investigation Completed!', 'success');
          es.close();
          fetchSavedReports();
          setActiveTab('results');
        } else if (msg.type === 'error' || msg.status === 'error') {
          showToast(`Investigation error: ${msg.message || 'Unknown failure'}`, 'danger');
          setIsInvestigating(false);
          es.close();
        }
      } catch (e) {
        // Fallback for raw text events
        setLiveEvents(prev => [...prev, {
          timestamp: new Date().toISOString(),
          type: 'INFO',
          message: event.data
        }]);
      }
    };

    es.onerror = (err) => {
      console.error('SSE Error:', err);
      // Don't close immediately if reconnecting, but handle terminal error
      setIsInvestigating(false);
      es.close();
      showToast('Live stream closed. Fetching latest completed report...', 'info');
      fetchLatestReport();
    };
  };

  const handleStopInvestigation = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsInvestigating(false);
    showToast('Investigation paused by user.', 'warning');
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
      const payload = {
        target_url: targetUrl,
        test_cases: generatedTests?.test_cases || []
      };

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
