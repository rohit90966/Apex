import os

path = os.path.join("app", "static", "app.js")
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Normalize CRLF to LF
norm_content = content.replace("\r\n", "\n")

old_overview = """      <div style="margin-top:16px;">
        <h3>1. Overview & Understanding</h3>
        <p><strong>Target URL:</strong> <code>${ov.target_url}</code></p>
        <p><strong>States Discovered:</strong> ${ov.meaningful_states_discovered} &nbsp;|&nbsp; <strong>Transitions:</strong> ${ov.transitions_observed} &nbsp;|&nbsp; <strong>Actions:</strong> ${ov.actions_attempted}</p>
        <p style="background:#f1f5f9;padding:12px;border-radius:6px;font-size:14px;">${report.application_understanding || ''}</p>
      </div>"""

new_overview = """      <div style="margin-top:16px;">
        <h3>1. Overview & Understanding</h3>
        <p><strong>Target URL:</strong> <code>${ov.target_url}</code> &nbsp;|&nbsp; <strong>Duration:</strong> ${ov.duration_seconds || 0}s</p>
        <p>
          <strong>Routes Discovered:</strong> ${ov.pages_discovered} &nbsp;|&nbsp;
          <strong>Meaningful States:</strong> ${ov.meaningful_states_discovered} &nbsp;|&nbsp;
          <strong>Confirmed Transitions:</strong> ${ov.transitions_observed} (Route: ${ov.route_transitions_observed || 0}, In-Page: ${ov.same_route_state_transitions || 0})
        </p>
        <p>
          <strong>Actions Evaluated:</strong> ${ov.actions_evaluated || 0} &nbsp;|&nbsp;
          <strong>Attempted:</strong> ${ov.actions_attempted || 0} &nbsp;|&nbsp;
          <strong>Executed:</strong> ${ov.actions_successful || 0} &nbsp;|&nbsp;
          <strong>Verified Effects:</strong> ${ov.actions_verified_effects || 0} &nbsp;|&nbsp;
          <strong>Pending Frontier:</strong> ${ov.pending_frontier_count || 0}
        </p>
        <p style="background:#f1f5f9;padding:12px;border-radius:6px;font-size:14px;">${report.application_understanding || ''}</p>
      </div>"""

old_counters = """    const resTitle = document.getElementById('resTitle');
    const resFinalUrl = document.getElementById('resFinalUrl');
    const resHttpStatus = document.getElementById('resHttpStatus');
    const resDuration = document.getElementById('resDuration');
    const resCategory = document.getElementById('resCategory');
    const resConfidence = document.getElementById('resConfidence');
    const resEvidenceList = document.getElementById('resEvidenceList');
    const planningModeBadge = document.getElementById('planningModeBadge');

    if (planningModeBadge) {
      planningModeBadge.textContent = 'Autonomous Investigation Mode';
      planningModeBadge.className = 'badge badge-success';
    }
    if (resTitle) resTitle.textContent = map.length > 0 ? (map[0].apparent_purpose || 'Application View') : 'Web Application';
    if (resFinalUrl) resFinalUrl.textContent = ov.target_url || '—';
    if (resHttpStatus) resHttpStatus.textContent = '200 OK (Browser Verified)';
    if (resDuration) resDuration.textContent = `${ov.actions_attempted || 0} actions evaluated`;
    if (resCategory) resCategory.textContent = 'Stateful Investigation';
    if (resConfidence) resConfidence.textContent = '100% Grounded in Live DOM';

    if (resEvidenceList) {
      resEvidenceList.innerHTML = `
        <li>Discovered ${ov.pages_discovered || 0} unique application page views across ${ov.meaningful_states_discovered || 0} distinct UI states.</li>
        <li>Observed ${ov.transitions_observed || 0} confirmed state transitions and ${ov.actions_successful || 0} verified actions.</li>
        <li>Collected ${(report.evidence_and_traceability || []).length} empirical evidence entries in ledger.</li>
      `;
    }

    // Update Counters
    const cntDiscoveredPages = document.getElementById('cntDiscoveredPages');
    const cntHeadings = document.getElementById('cntHeadings');
    const cntForms = document.getElementById('cntForms');
    const cntInputs = document.getElementById('cntInputs');
    const cntButtons = document.getElementById('cntButtons');
    const cntLinks = document.getElementById('cntLinks');
    const cntKnowledgePatterns = document.getElementById('cntKnowledgePatterns');
    const cntEvidenceRecords = document.getElementById('cntEvidenceRecords');
    const cntDeterministicTests = document.getElementById('cntDeterministicTests');

    if (cntDiscoveredPages) cntDiscoveredPages.textContent = ov.pages_discovered || (map.length || 1);
    if (cntHeadings) cntHeadings.textContent = ov.meaningful_states_discovered ? `${ov.meaningful_states_discovered} States` : 'Mapped';
    if (cntForms) cntForms.textContent = auth.boundary_observed ? 'Auth Form' : 'None';
    if (cntInputs) cntInputs.textContent = 'Mapped in DOM';
    if (cntButtons) cntButtons.textContent = `${ov.actions_successful || 0} Verified`;
    if (cntLinks) cntLinks.textContent = `${workflows.length} Transitions`;"""

new_counters = """    const legacy = report.legacy_inspection_summary || {};
    const resTitle = document.getElementById('resTitle');
    const resFinalUrl = document.getElementById('resFinalUrl');
    const resHttpStatus = document.getElementById('resHttpStatus');
    const resDuration = document.getElementById('resDuration');
    const resCategory = document.getElementById('resCategory');
    const resConfidence = document.getElementById('resConfidence');
    const resEvidenceList = document.getElementById('resEvidenceList');
    const planningModeBadge = document.getElementById('planningModeBadge');

    if (planningModeBadge) {
      planningModeBadge.textContent = 'Autonomous Investigation Mode';
      planningModeBadge.className = 'badge badge-success';
    }
    if (resTitle) resTitle.textContent = map.length > 0 ? (map[0].apparent_purpose || 'Application View') : 'Web Application';
    if (resFinalUrl) resFinalUrl.textContent = ov.target_url || '—';
    if (resHttpStatus) resHttpStatus.textContent = '200 OK (Browser Verified)';
    if (resDuration) resDuration.textContent = `${ov.duration_seconds || legacy.duration_seconds || 0}s`;
    if (resCategory) resCategory.textContent = 'Stateful Investigation';
    if (resConfidence) resConfidence.textContent = '100% Grounded in Live DOM';

    if (resEvidenceList) {
      resEvidenceList.innerHTML = `
        <li>Discovered ${ov.pages_discovered || legacy.pages_discovered || 0} unique application page views across ${ov.meaningful_states_discovered || legacy.states_discovered || 0} distinct UI states.</li>
        <li>Observed ${ov.transitions_observed || legacy.transitions_observed || 0} confirmed state transitions (${ov.route_transitions_observed || legacy.route_transitions_observed || 0} route changes, ${ov.same_route_state_transitions || legacy.same_route_state_transitions || 0} in-page state changes).</li>
        <li>Evaluated ${ov.actions_evaluated || legacy.actions_evaluated || 0} actions: ${ov.actions_attempted || legacy.actions_attempted || 0} attempted, ${ov.actions_successful || legacy.actions_successful || 0} executed, ${ov.actions_verified_effects || legacy.actions_verified_effects || 0} verified effects, ${ov.actions_failed || legacy.actions_failed || 0} failed, ${ov.actions_safety_blocked || legacy.actions_safety_blocked || 0} safety blocked.</li>
        <li>Collected ${(report.evidence_and_traceability || []).length} empirical evidence entries in ledger.</li>
      `;
    }

    // Update Counters - STRICT ACCURACY & SEMANTIC REPAIR
    // Populate each field ONLY with a value that has the corresponding meaning and supporting source data
    const cntDiscoveredPages = document.getElementById('cntDiscoveredPages');
    const cntHeadings = document.getElementById('cntHeadings');
    const cntForms = document.getElementById('cntForms');
    const cntInputs = document.getElementById('cntInputs');
    const cntButtons = document.getElementById('cntButtons');
    const cntLinks = document.getElementById('cntLinks');
    const cntKnowledgePatterns = document.getElementById('cntKnowledgePatterns');
    const cntEvidenceRecords = document.getElementById('cntEvidenceRecords');
    const cntDeterministicTests = document.getElementById('cntDeterministicTests');

    if (cntDiscoveredPages) cntDiscoveredPages.textContent = ov.pages_discovered || legacy.pages_discovered || 1;
    if (cntHeadings) cntHeadings.textContent = (legacy.headings_count !== undefined) ? legacy.headings_count : (ov.total_headings_discovered || 0);
    if (cntForms) cntForms.textContent = (legacy.forms_count !== undefined) ? legacy.forms_count : (ov.total_forms_discovered || 0);
    if (cntInputs) cntInputs.textContent = (legacy.inputs_count !== undefined) ? legacy.inputs_count : (ov.total_inputs_discovered || 0);
    if (cntButtons) cntButtons.textContent = (legacy.buttons_count !== undefined) ? legacy.buttons_count : (ov.total_buttons_discovered || 0);
    if (cntLinks) cntLinks.textContent = (legacy.links_count !== undefined) ? legacy.links_count : (ov.total_links_discovered || 0);"""

if old_overview in norm_content and old_counters in norm_content:
    norm_content = norm_content.replace(old_overview, new_overview)
    norm_content = norm_content.replace(old_counters, new_counters)
    with open(path, "w", encoding="utf-8") as f:
        f.write(norm_content)
    print("SUCCESS: app.js updated successfully!")
else:
    print("ERROR: strings not found in app.js")
