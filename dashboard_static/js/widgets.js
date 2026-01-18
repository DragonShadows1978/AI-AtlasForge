/**
 * Dashboard Widgets Module
 * AtlasForge widgets, analytics, git status, file handling, collapsible cards, journal
 * Dependencies: core.js, api.js, modals.js
 */

// =============================================================================
// COLLAPSIBLE CARD FUNCTIONALITY
// =============================================================================

function toggleCard(cardId) {
    const card = document.getElementById(cardId + '-card');
    if (card) {
        card.classList.toggle('collapsed');
        saveCardState(cardId, card.classList.contains('collapsed'));
    }
}

function saveCardState(cardId, collapsed) {
    try {
        const states = JSON.parse(localStorage.getItem('cardStates') || '{}');
        states[cardId] = collapsed;
        localStorage.setItem('cardStates', JSON.stringify(states));
    } catch (e) {}
}

function loadCardStates() {
    try {
        const states = JSON.parse(localStorage.getItem('cardStates') || '{}');
        for (const [cardId, collapsed] of Object.entries(states)) {
            if (collapsed) {
                const card = document.getElementById(cardId + '-card');
                if (card) card.classList.add('collapsed');
            }
        }
    } catch (e) {}
}

// =============================================================================
// JOURNAL FUNCTIONS
// =============================================================================

// Helper to render journal entries (extracted for WebSocket updates)
function renderJournalEntries(entries) {
    const container = document.getElementById('journal-entries');
    if (!container) return;

    const expandedStates = loadJournalExpandedStates();

    container.innerHTML = entries.map((e, idx) => {
        const isExpanded = expandedStates[idx] || false;
        const content = e.content || '';
        const shouldTruncate = content.length > 300;
        const displayContent = shouldTruncate && !isExpanded
            ? content.substring(0, 300) + '...'
            : content;

        return `
            <div class="journal-entry ${isExpanded ? 'expanded' : ''}" data-index="${idx}"
                 onclick="toggleJournalEntry(this)">
                <div class="journal-timestamp">${e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}</div>
                <div class="journal-stage">${e.stage || 'UNKNOWN'}</div>
                <div class="journal-message">${escapeHtml(displayContent)}</div>
                ${shouldTruncate ? '<div class="journal-expand-hint">Click to ' + (isExpanded ? 'collapse' : 'expand') + '</div>' : ''}
            </div>
        `;
    }).join('');
}

function toggleJournalEntry(el) {
    el.classList.toggle('expanded');
    saveJournalExpandedStates();
}

function saveJournalExpandedStates() {
    try {
        const expanded = [];
        document.querySelectorAll('.journal-entry.expanded').forEach(el => {
            const id = el.dataset.entryId;
            if (id) expanded.push(id);
        });
        localStorage.setItem('journalExpandedEntries', JSON.stringify(expanded));
    } catch (e) {}
}

function loadJournalExpandedStates() {
    try {
        return JSON.parse(localStorage.getItem('journalExpandedEntries') || '[]');
    } catch (e) {
        return [];
    }
}

function getJournalExpandedStates() {
    try {
        return JSON.parse(localStorage.getItem('journalExpandedEntries') || '[]');
    } catch (e) {
        return [];
    }
}

function restoreJournalExpandedStates() {
    const expandedIds = getJournalExpandedStates();
    if (expandedIds.length === 0) return;

    const expandedSet = new Set(expandedIds);
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        const id = el.dataset.entryId;
        if (expandedSet.has(id)) {
            el.classList.add('expanded');
        }
    });
}

function expandAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.add('expanded');
    });
    saveJournalExpandedStates();
}

function collapseAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.remove('expanded');
    });
    saveJournalExpandedStates();
}

// =============================================================================
// CONTROLS
// =============================================================================

async function startClaude(mode) {
    // Get mission input text
    const missionInput = document.getElementById('mission-input');
    const missionText = missionInput ? missionInput.value.trim() : '';

    // Get current mission state
    let currentMission = {};
    try {
        currentMission = await api('/api/mission', 'GET');
    } catch (e) {
        console.error('Failed to get current mission:', e);
    }
    const currentStage = currentMission.current_stage || 'COMPLETE';
    const isComplete = (currentStage === 'COMPLETE' || currentStage === '' || !currentStage);

    if (missionText) {
        // Case 1: Text box has content - create new mission and start
        const cycleBudget = parseInt(document.getElementById('cycle-budget-input')?.value) || 1;
        const projectNameInput = document.getElementById('project-name-input');
        const projectName = projectNameInput ? projectNameInput.value.trim() : '';

        // If replacing an active mission, ask for confirmation
        if (!isComplete) {
            const confirm1 = confirm(
                `⚠️ Current mission is in stage: ${currentStage}\n\n` +
                `This will OVERWRITE the current mission and start the new one!\n\n` +
                `Are you sure?`
            );
            if (!confirm1) return;

            const confirm2 = confirm(
                `🚨 SECOND CONFIRMATION 🚨\n\n` +
                `You are about to PERMANENTLY overwrite:\n` +
                `"${(currentMission.problem_statement || '').substring(0, 100)}..."\n\n` +
                `This cannot be undone. Proceed?`
            );
            if (!confirm2) return;
        }

        // Create the new mission
        const payload = { mission: missionText, cycle_budget: cycleBudget };
        if (projectName) payload.project_name = projectName;

        const setResult = await api('/api/mission', 'POST', payload);
        if (!setResult.success) {
            showToast(`Failed to set mission: ${setResult.message}`, 'error');
            return;
        }

        // Clear inputs
        missionInput.value = '';
        if (projectNameInput) projectNameInput.value = '';

        // Now start Claude
        const startResult = await api(`/api/start/${mode}`, 'POST');
        showToast(`Mission set and started: ${startResult.message}`, 'success');
        refresh();

    } else if (!isComplete) {
        // Case 2: Empty text box, mission in progress - restart/resume
        const data = await api(`/api/start/${mode}`, 'POST');
        showToast(data.message);
        refresh();

    } else {
        // Case 3: Empty text box, no active mission - error
        showToast('No mission to start. Enter a mission description first.', 'error');
    }
}

async function stopClaude() {
    const data = await api('/api/stop', 'POST');
    showToast(data.message);
    refresh();
}

async function queueMission() {
    // Get mission input text
    const missionInput = document.getElementById('mission-input');
    const missionText = missionInput ? missionInput.value.trim() : '';

    if (!missionText) {
        showToast('Enter a mission description to queue.', 'error');
        return;
    }

    const cycleBudget = parseInt(document.getElementById('cycle-budget-input')?.value) || 1;
    const projectNameInput = document.getElementById('project-name-input');
    const projectName = projectNameInput ? projectNameInput.value.trim() : '';

    // Add to queue via API
    try {
        const payload = {
            problem_statement: missionText,
            cycle_budget: cycleBudget,
            priority: 0,
            source: 'dashboard'
        };
        if (projectName) payload.project_name = projectName;

        const data = await api('/api/queue/add', 'POST', payload);
        if (data.status === 'added') {
            showToast(`Mission queued at position ${data.queue_length}`, 'success');
            // Clear inputs
            missionInput.value = '';
            if (projectNameInput) projectNameInput.value = '';
            refresh();
            // Refresh queue widget if available
            if (typeof refreshQueueWidget === 'function') {
                refreshQueueWidget();
            }
        } else if (data.error) {
            showToast(`Failed to queue: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to queue mission:', e);
        showToast('Failed to queue mission', 'error');
    }
}

async function setMission() {
    const mission = document.getElementById('mission-input').value.trim();
    if (!mission) return;

    const cycleBudget = parseInt(document.getElementById('cycle-budget-input').value) || 1;

    // First, check current mission status
    const currentMission = await api('/api/mission', 'GET');
    const currentStage = currentMission.current_stage || 'COMPLETE';

    // If mission is not COMPLETE, require double confirmation
    if (currentStage !== 'COMPLETE') {
        const confirm1 = confirm(
            `⚠️ Current mission is in stage: ${currentStage}\n\n` +
            `This will OVERWRITE the current mission!\n\n` +
            `Are you sure you want to replace it?`
        );
        if (!confirm1) return;

        const confirm2 = confirm(
            `🚨 SECOND CONFIRMATION 🚨\n\n` +
            `You are about to PERMANENTLY overwrite:\n` +
            `"${(currentMission.problem_statement || '').substring(0, 100)}..."\n\n` +
            `This cannot be undone. Proceed?`
        );
        if (!confirm2) return;
    }

    const data = await api('/api/mission', 'POST', {mission, cycle_budget: cycleBudget});
    showToast(data.message);
    document.getElementById('mission-input').value = '';
    refresh();
}

async function resetMission() {
    const data = await api('/api/mission/reset', 'POST');
    showToast(data.message);
    refresh();
}

// =============================================================================
// FILE HANDLING
// =============================================================================

async function loadFiles() {
    try {
        const files = await api('/api/files');
        const container = document.getElementById('files-list');
        document.getElementById('files-count').textContent = files.length;

        if (files.length === 0) {
            container.innerHTML = '<div class="no-files">No files yet</div>';
            return;
        }

        container.innerHTML = files.slice(0, 20).map(f => `
            <div class="file-item">
                <div class="file-info">
                    <a href="${f.download_url}" class="download-link file-name" download title="${f.path}">${f.name}</a>
                    <span class="file-meta">${formatBytes(f.size)} · ${formatTimeAgo(f.modified)}</span>
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error('Error loading files:', e);
    }
}

// =============================================================================
// STAGE INDICATOR
// =============================================================================

function updateStageIndicator(currentStage) {
    const stageEls = document.querySelectorAll('.stage');
    const currentIdx = stages.indexOf(currentStage);

    stageEls.forEach((el, idx) => {
        el.classList.remove('active', 'complete');
        if (idx < currentIdx) el.classList.add('complete');
        else if (idx === currentIdx) el.classList.add('active');
    });
}

// =============================================================================
// STATUS BAR UPDATE
// =============================================================================

function updateStatusBar(data) {
    // Update status badge
    const badge = document.getElementById('status-badge');
    if (badge) {
        badge.textContent = data.running ? `Running (${data.mode})` : 'Offline';
        badge.className = `status-badge ${data.running ? 'on' : 'off'}`;
    }

    // Update stats
    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setEl('stat-mode', data.mode || '-');
    setEl('stat-stage', data.rd_stage || '-');
    setEl('stat-iteration', data.rd_iteration);
    setEl('stat-mission-cycle', `${data.current_cycle || 1}/${data.cycle_budget || 1}`);
    setEl('stat-cycles', data.total_cycles);
    setEl('stat-boots', data.boot_count);

    // Update stage indicator
    updateStageIndicator(data.rd_stage);
}

// =============================================================================
// MAIN REFRESH
// =============================================================================

async function refresh() {
    const data = await api('/api/status');

    // Status badge
    const badge = document.getElementById('status-badge');
    badge.textContent = data.running ? `Running (${data.mode})` : 'Offline';
    badge.className = `status-badge ${data.running ? 'on' : 'off'}`;

    // Stats
    document.getElementById('stat-mode').textContent = data.mode || '-';
    document.getElementById('stat-stage').textContent = data.rd_stage || '-';
    document.getElementById('stat-iteration').textContent = data.rd_iteration;
    document.getElementById('stat-mission-cycle').textContent = `${data.current_cycle || 1}/${data.cycle_budget || 1}`;
    document.getElementById('stat-cycles').textContent = data.total_cycles;
    document.getElementById('stat-boots').textContent = data.boot_count;

    // Mission - Store full text and show preview with expand option
    fullMissionText = data.mission || 'No mission set';
    const missionEl = document.getElementById('current-mission');
    const preview = data.mission_preview || data.mission || 'No mission set';
    missionEl.innerHTML = `
        <span onclick="openMissionModal()" style="cursor: pointer;" title="Click to view full mission">
            ${preview}
            ${data.mission && data.mission.length > 100 ? ' <span style="color: var(--accent);">[expand]</span>' : ''}
        </span>
    `;

    // Stage indicator
    updateStageIndicator(data.rd_stage);

    // Journal
    const journal = await api('/api/journal');
    document.getElementById('journal').innerHTML = journal.map(j => {
        if (j.is_truncated) {
            return `
                <div class="journal-entry expandable" data-entry-id="${j.timestamp}" onclick="toggleJournalEntry(this)">
                    <span class="journal-type">${escapeHtml(j.type)}</span>
                    <span class="journal-time">${j.timestamp ? new Date(j.timestamp).toLocaleTimeString() : ''}</span>
                    <div class="preview-message">${escapeHtml(j.message)}...<span class="expand-indicator">[+]</span></div>
                    <div class="full-message">${escapeHtml(j.full_message)}<span class="collapse-indicator">[−]</span></div>
                </div>
            `;
        } else {
            return `
                <div class="journal-entry" data-entry-id="${j.timestamp}">
                    <span class="journal-type">${escapeHtml(j.type)}</span>
                    <span class="journal-time">${j.timestamp ? new Date(j.timestamp).toLocaleTimeString() : ''}</span>
                    <div>${escapeHtml(j.message || j.status || '')}</div>
                </div>
            `;
        }
    }).join('') || '<div style="color: var(--text-dim)">No activity yet</div>';

    // Restore journal expansion states after rendering
    restoreJournalExpandedStates();

    // Load recommendations
    await loadRecommendations();

    // Load files
    await loadFiles();

    // Load AtlasForge enhancement widgets
    await refreshAtlasForgeWidgets();

    // Load KB Analytics widget (less frequently - every 3rd refresh)
    if (!window.lastKBRefresh || Date.now() - window.lastKBRefresh > 15000) {
        if (typeof refreshKBAnalyticsWidget === 'function') {
            await refreshKBAnalyticsWidget();
        }
        window.lastKBRefresh = Date.now();
    }
}

// =============================================================================
// AtlasForge ENHANCEMENT WIDGETS
// =============================================================================

async function refreshAtlasForgeWidgets() {
    try {
        const data = await api('/api/atlasforge/exploration-stats');
        if (data.error) {
            console.log('AtlasForge data not available:', data.error);
            return;
        }

        // Update exploration stats
        if (data.exploration) {
            const fileCount = (data.exploration.nodes_by_type || {}).file || 0;
            document.getElementById('af-files-count').textContent = fileCount;
            document.getElementById('af-insights-count').textContent = data.exploration.total_insights || 0;
            document.getElementById('af-edges-count').textContent = data.exploration.total_edges || 0;
        }

        // Update coverage
        const coverage = data.coverage_pct || 0;
        document.getElementById('af-coverage-pct').textContent = coverage + '%';
        document.getElementById('af-coverage-bar').style.width = coverage + '%';

        // Update drift chart
        updateDriftChart(data.drift_history || []);

        // Update recent explorations
        updateRecentExplorations(data.recent_explorations || []);

        // Update graph visualization (less frequently)
        if (!window.lastGraphRefresh || Date.now() - window.lastGraphRefresh > 30000) {
            if (typeof refreshGraphVisualization === 'function') {
                refreshGraphVisualization();
            }
            window.lastGraphRefresh = Date.now();
        }
    } catch (e) {
        console.log('Error loading AtlasForge widgets:', e);
    }
}

function updateDriftChart(driftHistory) {
    const chart = document.getElementById('af-drift-chart');
    const simEl = document.getElementById('af-drift-similarity');
    const sevEl = document.getElementById('af-drift-severity');

    if (!driftHistory || driftHistory.length === 0) {
        chart.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em; width: 100%; text-align: center;">No drift data yet</div>';
        simEl.textContent = 'N/A';
        sevEl.textContent = 'N/A';
        return;
    }

    // Build bar chart (max 10 bars)
    const recentHistory = driftHistory.slice(-10);
    const bars = recentHistory.map(h => {
        const sim = h.similarity || 1.0;
        const height = Math.max(10, sim * 100);
        let colorClass = 'green';
        if (h.alert === 'YELLOW') colorClass = 'yellow';
        else if (h.alert === 'RED' || h.alert === 'ORANGE') colorClass = 'red';

        return `<div class="af-drift-bar ${colorClass}" style="height: ${height}%" title="Cycle ${h.cycle}: ${(sim * 100).toFixed(0)}%"></div>`;
    }).join('');

    chart.innerHTML = bars;

    // Update current status
    const latest = recentHistory[recentHistory.length - 1];
    const sim = (latest.similarity * 100).toFixed(1);
    simEl.textContent = sim + '%';
    simEl.className = 'value ' + getAlertColor(latest.alert);
    sevEl.textContent = latest.severity || 'N/A';
    sevEl.className = 'value ' + getAlertColor(latest.alert);
}

function getAlertColor(alert) {
    if (alert === 'GREEN') return 'green';
    if (alert === 'YELLOW') return 'yellow';
    return 'red';
}

function updateRecentExplorations(explorations) {
    const list = document.getElementById('af-recent-list');

    if (!explorations || explorations.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No explorations yet</div>';
        return;
    }

    const items = explorations.slice(0, 8).map(e => {
        const name = e.name || e.path || 'Unknown';
        const type = e.type || 'file';
        return `
            <div class="af-exploration-item" title="${e.summary || ''}">
                <span class="af-exploration-name">${name}</span>
                <span class="af-exploration-type">${type}</span>
            </div>
        `;
    }).join('');

    list.innerHTML = items;
}

// =============================================================================
// ANALYTICS WIDGET
// =============================================================================

let analyticsData = null;

async function refreshAnalyticsWidget() {
    try {
        // Current mission analytics
        const current = await api('/api/analytics/current');
        if (!current.error) {
            document.getElementById('analytics-tokens').textContent = formatNumber(current.tokens || 0);
            document.getElementById('analytics-cost').textContent = '$' + (current.cost || 0).toFixed(4);
        }

        // 30-day aggregate
        const summary = await api('/api/analytics/summary');
        if (!summary.error && summary.aggregate_30d) {
            const agg30d = summary.aggregate_30d.totals || summary.aggregate_30d;
            document.getElementById('analytics-30d-tokens').textContent = formatNumber(agg30d.total_tokens || 0);
            document.getElementById('analytics-30d-cost').textContent = '$' + (agg30d.total_cost_usd || agg30d.total_cost || 0).toFixed(2);

            // Update trend chart
            updateAnalyticsTrendWidget(summary.recent_missions || []);
        }
    } catch (e) {
        console.error('Analytics widget error:', e);
    }
}

function updateAnalyticsTrendWidget(missions) {
    const chart = document.getElementById('analytics-trend-chart');
    if (!missions || missions.length === 0) {
        chart.innerHTML = '<div style="color: var(--text-dim); font-size: 0.75em; width: 100%; text-align: center;">No trend data</div>';
        return;
    }

    const costs = missions.slice(-10).map(m => m.cost || 0);
    const maxCost = Math.max(...costs, 0.01);

    const bars = costs.map(cost => {
        const height = Math.max(5, (cost / maxCost) * 100);
        return `<div class="analytics-trend-bar" style="height: ${height}%;" title="$${cost.toFixed(4)}"></div>`;
    }).join('');

    chart.innerHTML = bars;
}

async function refreshFullAnalytics() {
    try {
        const summary = await api('/api/analytics/summary');
        analyticsData = summary;

        if (!summary.error) {
            // Update header stats
            const allTimeRaw = summary.all_time || {};
            const allTime = allTimeRaw.totals || allTimeRaw;
            document.getElementById('analytics-total-missions').textContent = allTime.missions || allTime.mission_count || 0;
            document.getElementById('analytics-total-tokens').textContent = formatNumber(allTime.total_tokens || 0);
            document.getElementById('analytics-total-cost').textContent = '$' + (allTime.total_cost_usd || allTime.total_cost || 0).toFixed(2);

            // Update breakdown
            document.getElementById('analytics-input-tokens').textContent = formatNumber(allTime.input_tokens || 0);
            document.getElementById('analytics-output-tokens').textContent = formatNumber(allTime.output_tokens || 0);

            const missionCount = allTime.missions || allTime.mission_count || 0;
            const totalCost = allTime.total_cost_usd || allTime.total_cost || 0;
            const avgCost = missionCount > 0 ? (totalCost / missionCount) : 0;
            document.getElementById('analytics-avg-cost').textContent = '$' + avgCost.toFixed(4);

            // Mission list
            renderAnalyticsMissionList(summary.recent_missions || []);

            // Trend chart
            renderAnalyticsTrendChart(summary.recent_missions || []);
        }
    } catch (e) {
        console.error('Full analytics error:', e);
    }
}

function renderAnalyticsMissionList(missions) {
    const list = document.getElementById('analytics-missions-list');
    if (!missions || missions.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim);">No mission data</div>';
        return;
    }

    const html = missions.map(m => `
        <div class="learning-item" onclick="showMissionAnalytics('${m.mission_id}')">
            <div class="learning-item-title">${m.mission_id || 'Unknown'}</div>
            <div class="learning-item-meta">
                <span style="color: var(--yellow);">$${(m.cost || 0).toFixed(4)}</span> |
                ${formatNumber(m.tokens || 0)} tokens
            </div>
        </div>
    `).join('');

    list.innerHTML = html;
}

function renderAnalyticsTrendChart(missions) {
    const canvas = document.getElementById('analytics-trend-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();

    // Set actual size
    canvas.width = rect.width * window.devicePixelRatio;
    canvas.height = rect.height * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const w = rect.width;
    const h = rect.height;
    const padding = 40;

    // Clear
    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, 0, w, h);

    if (!missions || missions.length === 0) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '14px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('No data available', w / 2, h / 2);
        return;
    }

    const costs = missions.slice(-20).map(m => m.cost || 0);
    const maxCost = Math.max(...costs, 0.01);

    const barWidth = (w - padding * 2) / costs.length - 4;
    const graphHeight = h - padding * 2;

    ctx.fillStyle = '#58a6ff';
    costs.forEach((cost, i) => {
        const barHeight = (cost / maxCost) * graphHeight;
        const x = padding + i * (barWidth + 4);
        const y = h - padding - barHeight;
        ctx.fillRect(x, y, barWidth, barHeight);
    });

    // Y axis
    ctx.strokeStyle = '#30363d';
    ctx.beginPath();
    ctx.moveTo(padding, padding);
    ctx.lineTo(padding, h - padding);
    ctx.stroke();

    // Labels
    ctx.fillStyle = '#8b949e';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    ctx.fillText('$' + maxCost.toFixed(2), padding - 5, padding + 10);
    ctx.fillText('$0', padding - 5, h - padding);
}

async function showMissionAnalytics(missionId) {
    // Could show more detail - for now just highlight
    showToast('Mission: ' + missionId);
}

// Debug: mark widgets module loaded
console.log('Widgets module loaded');
