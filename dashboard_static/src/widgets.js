/**
 * Dashboard Widgets Module (ES6)
 * AtlasForge widgets, analytics, file handling, collapsible cards, journal
 * Dependencies: core.js, api.js
 */

import { showToast, escapeHtml, formatBytes, formatNumber, formatTimeAgo, stages } from './core.js';
import { api } from './api.js';
import { setFullMissionText } from './modals.js';

// =============================================================================
// STATE
// =============================================================================

let fullMissionText = '';
let analyticsData = null;

// =============================================================================
// COLLAPSIBLE CARD FUNCTIONALITY
// =============================================================================

export function toggleCard(cardId) {
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

export function loadCardStates() {
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

export function renderJournalEntries(entries) {
    const container = document.getElementById('journal-entries');
    if (!container) return;

    const expandedStates = loadJournalExpandedStates();

    container.innerHTML = entries.map((e, idx) => {
        const isExpanded = expandedStates[idx] || false;
        const content = e.full_message || e.message || '';
        const stage = e.stage || e.status || e.type || 'UNKNOWN';
        const shouldTruncate = content.length > 300;
        const displayContent = shouldTruncate && !isExpanded
            ? content.substring(0, 300) + '...'
            : content;

        return `
            <div class="journal-entry ${isExpanded ? 'expanded' : ''}" data-index="${idx}"
                 onclick="window.toggleJournalEntry(this)">
                <div class="journal-timestamp">${e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}</div>
                <div class="journal-stage">${stage}
                <div class="journal-message">${escapeHtml(displayContent)}</div>
                ${shouldTruncate ? '<div class="journal-expand-hint">Click to ' + (isExpanded ? 'collapse' : 'expand') + '</div>' : ''}
            </div>
        `;
    }).join('');
}

export function toggleJournalEntry(el) {
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

export function expandAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.add('expanded');
    });
    saveJournalExpandedStates();
}

export function collapseAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.remove('expanded');
    });
    saveJournalExpandedStates();
}

// =============================================================================
// CONTROLS
// =============================================================================

export async function startClaude(mode) {
    const data = await api(`/api/start/${mode}`, 'POST');
    showToast(data.message);
    refresh();
}

export async function stopClaude() {
    const data = await api('/api/stop', 'POST');
    showToast(data.message);
    refresh();
}

export async function setMission() {
    const mission = document.getElementById('mission-input').value.trim();
    if (!mission) return;

    const cycleBudget = parseInt(document.getElementById('cycle-budget-input').value) || 1;

    const currentMission = await api('/api/mission', 'GET');
    const currentStage = currentMission.current_stage || 'COMPLETE';

    if (currentStage !== 'COMPLETE') {
        const confirm1 = confirm(
            `Current mission is in stage: ${currentStage}\n\n` +
            `This will OVERWRITE the current mission!\n\n` +
            `Are you sure you want to replace it?`
        );
        if (!confirm1) return;

        const confirm2 = confirm(
            `SECOND CONFIRMATION\n\n` +
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

export async function resetMission() {
    const data = await api('/api/mission/reset', 'POST');
    showToast(data.message);
    refresh();
}

// =============================================================================
// FILE HANDLING
// =============================================================================

export async function loadFiles() {
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
                    <span class="file-meta">${formatBytes(f.size)} - ${formatTimeAgo(f.modified)}</span>
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

export function updateStageIndicator(currentStage) {
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

export function updateStatusBar(data) {
    // Update AtlasForge service status indicator in header
    updateAtlasForgeServiceStatus(data.running, data.mode);

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

    updateStageIndicator(data.rd_stage);
}

/**
 * Update the AtlasForge service status indicator in the header
 * @param {boolean} running - Whether AtlasForge is running
 * @param {string} mode - Current mode (rd, free, etc.)
 */
export function updateAtlasForgeServiceStatus(running, mode) {
    const container = document.getElementById('atlasforge-service-status');
    const stateEl = document.getElementById('atlasforge-service-state');

    if (!container || !stateEl) return;

    // Determine status class and text
    container.classList.remove('online', 'offline', 'busy');

    if (running) {
        if (mode === 'rd') {
            container.classList.add('online');
            stateEl.textContent = 'R&D Mode';
        } else if (mode === 'free') {
            container.classList.add('online');
            stateEl.textContent = 'Free Mode';
        } else {
            container.classList.add('online');
            stateEl.textContent = 'Online';
        }
    } else {
        container.classList.add('offline');
        stateEl.textContent = 'Offline';
    }
}

/**
 * Update the Investigation service status indicator in the header
 * @param {boolean} running - Whether Investigation is running
 * @param {string} status - Investigation status (analyzing, exploring, etc.)
 */
export function updateInvestigationServiceStatus(running, status) {
    const container = document.getElementById('investigation-service-status');
    const stateEl = document.getElementById('investigation-service-state');

    if (!container || !stateEl) return;

    // Determine status class and text
    container.classList.remove('online', 'offline', 'busy');

    if (running) {
        // Map status to display values
        const statusLabels = {
            'pending': 'Pending',
            'analyzing': 'Analyzing',
            'spawning_subagents': 'Spawning',
            'exploring': 'Exploring',
            'synthesizing': 'Synthesizing',
            'completed': 'Complete',
            'failed': 'Failed'
        };

        const displayStatus = statusLabels[status] || status || 'Running';

        if (status === 'completed') {
            container.classList.add('online');
        } else if (status === 'failed') {
            container.classList.add('offline');
        } else {
            container.classList.add('busy');
        }
        stateEl.textContent = displayStatus;
    } else {
        container.classList.add('offline');
        stateEl.textContent = 'Offline';
    }
}

// =============================================================================
// TERMINAL SERVICE STATUS (Port 5002)
// =============================================================================

let pendingRestartService = null;

/**
 * Update the Terminal service status indicator in the service bar
 * @param {boolean} online - Whether service is online
 * @param {boolean} restarting - Whether service is currently restarting
 */
export function updateTerminalServiceStatus(online, restarting = false) {
    const container = document.getElementById('terminal-service');
    if (!container) return;

    container.classList.remove('online', 'offline', 'restarting');

    if (restarting) {
        container.classList.add('restarting');
    } else if (online) {
        container.classList.add('online');
    } else {
        container.classList.add('offline');
    }
}

/**
 * Fetch and update all service statuses
 */
export async function refreshServiceStatuses() {
    try {
        const statuses = await api('/api/services/status');
        if (statuses.terminal) {
            updateTerminalServiceStatus(statuses.terminal.online);
        }
    } catch (e) {
        console.error('Failed to fetch service statuses:', e);
        updateTerminalServiceStatus(false);
    }
}

/**
 * Show restart confirmation modal
 * @param {string} serviceId - Service to restart
 */
export function showRestartModal(serviceId) {
    pendingRestartService = serviceId;
    const overlay = document.getElementById('restart-modal-overlay');
    const textEl = document.getElementById('restart-modal-text');

    const serviceNames = {
        'terminal': 'Web Terminal service on port 5002'
    };

    textEl.textContent = `Are you sure you want to restart the ${serviceNames[serviceId] || serviceId}?`;
    overlay.classList.add('visible');
}

/**
 * Hide restart confirmation modal
 */
export function hideRestartModal() {
    pendingRestartService = null;
    const overlay = document.getElementById('restart-modal-overlay');
    overlay.classList.remove('visible');
}

/**
 * Confirm and execute service restart
 */
export async function confirmRestart() {
    if (!pendingRestartService) return;

    const serviceId = pendingRestartService;
    hideRestartModal();

    // Show restarting state
    updateTerminalServiceStatus(false, true);
    showToast(`Restarting ${serviceId} service...`, 'info');

    try {
        const result = await api(`/api/services/restart/${serviceId}`, 'POST');

        if (result.success) {
            showToast(result.message, 'success');
            updateTerminalServiceStatus(true);
        } else {
            showToast(result.error || 'Failed to restart service', 'error');
            updateTerminalServiceStatus(false);
        }
    } catch (e) {
        console.error('Failed to restart service:', e);
        showToast('Failed to restart service: ' + e.message, 'error');
        updateTerminalServiceStatus(false);
    }

    // Refresh status after a short delay
    setTimeout(refreshServiceStatuses, 2000);
}

// =============================================================================
// MAIN REFRESH
// =============================================================================

export async function refresh() {
    const data = await api('/api/status');

    // Update AtlasForge service status in header
    updateAtlasForgeServiceStatus(data.running, data.mode);

    // Also check Investigation status
    try {
        const invStatus = await api('/api/investigation/status');
        const isInvRunning = invStatus && invStatus.investigation_id &&
            invStatus.status !== 'completed' && invStatus.status !== 'failed' && invStatus.status !== 'idle';
        updateInvestigationServiceStatus(isInvRunning, invStatus?.status);
    } catch (e) {
        // Investigation API not available or error
        updateInvestigationServiceStatus(false, null);
    }

    // Update external service statuses (terminal server, etc.)
    refreshServiceStatuses();

    document.getElementById('stat-mode').textContent = data.mode || '-';
    document.getElementById('stat-stage').textContent = data.rd_stage || '-';
    document.getElementById('stat-iteration').textContent = data.rd_iteration;
    document.getElementById('stat-mission-cycle').textContent = `${data.current_cycle || 1}/${data.cycle_budget || 1}`;
    document.getElementById('stat-cycles').textContent = data.total_cycles;
    document.getElementById('stat-boots').textContent = data.boot_count;

    fullMissionText = data.mission || 'No mission set';
    setFullMissionText(fullMissionText);
    const missionEl = document.getElementById('current-mission');
    const preview = data.mission_preview || data.mission || 'No mission set';
    missionEl.innerHTML = `
        <span onclick="window.openMissionModal()" style="cursor: pointer;" title="Click to view full mission">
            ${preview}
            ${data.mission && data.mission.length > 100 ? ' <span style="color: var(--accent);">[expand]</span>' : ''}
        </span>
    `;

    updateStageIndicator(data.rd_stage);

    const journal = await api('/api/journal');
    document.getElementById('journal').innerHTML = journal.map(j => {
        if (j.is_truncated) {
            return `
                <div class="journal-entry expandable" data-entry-id="${j.timestamp}" onclick="window.toggleJournalEntry(this)">
                    <span class="journal-type">${escapeHtml(j.type)}</span>
                    <span class="journal-time">${j.timestamp ? new Date(j.timestamp).toLocaleTimeString() : ''}</span>
                    <div class="preview-message">${escapeHtml(j.message)}...<span class="expand-indicator">[+]</span></div>
                    <div class="full-message">${escapeHtml(j.full_message)}<span class="collapse-indicator">[-]</span></div>
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

    restoreJournalExpandedStates();

    if (typeof window.loadRecommendations === 'function') {
        await window.loadRecommendations();
    }

    await loadFiles();
    await refreshAtlasForgeWidgets();

    if (!window.lastKBRefresh || Date.now() - window.lastKBRefresh > 15000) {
        if (typeof window.refreshKBAnalyticsWidget === 'function') {
            await window.refreshKBAnalyticsWidget();
        }
        window.lastKBRefresh = Date.now();
    }

    // Refresh Recommendations widget (Investigation-KB integration)
    // Use initRecommendationsWidget for first load (populates filters, loads state)
    // Falls back to refreshRecommendations for subsequent refreshes
    if (typeof window.initRecommendationsWidget === 'function') {
        await window.initRecommendationsWidget();
    } else if (typeof window.refreshRecommendations === 'function') {
        await window.refreshRecommendations();
    }
}

// =============================================================================
// ATLASFORGE ENHANCEMENT WIDGETS
// =============================================================================

export async function refreshAtlasForgeWidgets() {
    try {
        const data = await api('/api/atlasforge/exploration-stats');
        if (data.error) {
            console.log('AtlasForge data not available:', data.error);
            return;
        }

        if (data.exploration) {
            const fileCount = (data.exploration.nodes_by_type || {}).file || 0;
            document.getElementById('atlasforge-files-count').textContent = fileCount;
            document.getElementById('atlasforge-insights-count').textContent = data.exploration.total_insights || 0;
            document.getElementById('atlasforge-edges-count').textContent = data.exploration.total_edges || 0;
        }

        const coverage = data.coverage_pct || 0;
        document.getElementById('atlasforge-coverage-pct').textContent = coverage + '%';
        document.getElementById('atlasforge-coverage-bar').style.width = coverage + '%';

        updateDriftChart(data.drift_history || []);
        updateRecentExplorations(data.recent_explorations || []);

        if (!window.lastGraphRefresh || Date.now() - window.lastGraphRefresh > 30000) {
            if (typeof window.refreshGraphVisualization === 'function') {
                window.refreshGraphVisualization();
            }
            window.lastGraphRefresh = Date.now();
        }
    } catch (e) {
        console.log('Error loading AtlasForge widgets:', e);
    }
}

function updateDriftChart(driftHistory) {
    const chart = document.getElementById('atlasforge-drift-chart');
    const simEl = document.getElementById('atlasforge-drift-similarity');
    const sevEl = document.getElementById('atlasforge-drift-severity');

    if (!driftHistory || driftHistory.length === 0) {
        chart.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em; width: 100%; text-align: center;">No drift data yet</div>';
        simEl.textContent = 'N/A';
        sevEl.textContent = 'N/A';
        return;
    }

    const recentHistory = driftHistory.slice(-10);
    const bars = recentHistory.map(h => {
        const sim = h.similarity || 1.0;
        const height = Math.max(10, sim * 100);
        let colorClass = 'green';
        if (h.alert === 'YELLOW') colorClass = 'yellow';
        else if (h.alert === 'RED' || h.alert === 'ORANGE') colorClass = 'red';

        return `<div class="atlasforge-drift-bar ${colorClass}" style="height: ${height}%" title="Cycle ${h.cycle}: ${(sim * 100).toFixed(0)}%"></div>`;
    }).join('');

    chart.innerHTML = bars;

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
    const list = document.getElementById('atlasforge-recent-list');

    if (!explorations || explorations.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No explorations yet</div>';
        return;
    }

    const items = explorations.slice(0, 8).map(e => {
        const name = e.name || e.path || 'Unknown';
        const type = e.type || 'file';
        return `
            <div class="atlasforge-exploration-item" title="${e.summary || ''}">
                <span class="atlasforge-exploration-name">${name}</span>
                <span class="atlasforge-exploration-type">${type}</span>
            </div>
        `;
    }).join('');

    list.innerHTML = items;
}

// =============================================================================
// ANALYTICS WIDGET
// =============================================================================

export async function refreshAnalyticsWidget() {
    try {
        const current = await api('/api/analytics/current');
        if (!current.error) {
            document.getElementById('analytics-tokens').textContent = formatNumber(current.tokens || 0);
            document.getElementById('analytics-cost').textContent = '$' + (current.cost || 0).toFixed(4);
        }

        const summary = await api('/api/analytics/summary');
        if (!summary.error && summary.aggregate_30d) {
            const agg30d = summary.aggregate_30d.totals || summary.aggregate_30d;
            document.getElementById('analytics-30d-tokens').textContent = formatNumber(agg30d.total_tokens || 0);
            document.getElementById('analytics-30d-cost').textContent = '$' + (agg30d.total_cost_usd || agg30d.total_cost || 0).toFixed(2);

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

export function showMissionAnalytics(missionId) {
    openMissionAnalyticsModal(missionId);
}

// Export fullMissionText getter
export function getFullMissionText() {
    return fullMissionText;
}

// =============================================================================
// ENHANCED ANALYTICS TAB FUNCTIONS
// =============================================================================

let currentAnalyticsPeriod = 30;
let analyticsCache = {
    summary: null,
    daily: null,
    stages: null,
    models: null
};

export async function applyAnalyticsPeriodFilter() {
    const select = document.getElementById('analytics-period-filter');
    currentAnalyticsPeriod = parseInt(select.value) || 30;
    const periodLabel = document.getElementById('analytics-trend-period');
    if (periodLabel) {
        if (currentAnalyticsPeriod === 0) {
            periodLabel.textContent = '(All Time)';
        } else {
            periodLabel.textContent = `(${currentAnalyticsPeriod} Days)`;
        }
    }
    await refreshFullAnalytics();
}

export async function refreshFullAnalytics() {
    try {
        // Fetch all analytics data in parallel
        const [summary, daily, stages, models] = await Promise.all([
            api('/api/analytics/summary'),
            api(`/api/analytics/daily?days=${currentAnalyticsPeriod}`),
            api(`/api/analytics/by-stage?days=${currentAnalyticsPeriod}`),
            api(`/api/analytics/by-model?days=${currentAnalyticsPeriod}`)
        ]);

        analyticsCache = { summary, daily, stages, models };
        analyticsData = summary;

        // Update header stats
        if (!summary.error) {
            const allTimeRaw = summary.all_time || {};
            const allTime = allTimeRaw.totals || allTimeRaw;
            const missionCount = allTime.missions || allTime.mission_count || 0;
            const totalTokens = allTime.total_tokens || 0;
            const totalCost = allTime.total_cost_usd || allTime.total_cost || 0;

            document.getElementById('analytics-total-missions').textContent = missionCount;
            document.getElementById('analytics-total-tokens').textContent = formatNumber(totalTokens);
            document.getElementById('analytics-total-cost').textContent = '$' + totalCost.toFixed(2);

            // Avg per mission
            const avgCost = missionCount > 0 ? (totalCost / missionCount) : 0;
            document.getElementById('analytics-avg-per-mission').textContent = '$' + avgCost.toFixed(2);

            // Cache rate
            const cacheRead = allTime.cache_read_tokens || 0;
            const inputTokens = allTime.input_tokens || 0;
            const cacheRate = inputTokens > 0 ? ((cacheRead / inputTokens) * 100).toFixed(1) : 0;
            document.getElementById('analytics-cache-rate').textContent = cacheRate + '%';

            // Today's cost
            if (daily && daily.daily && daily.daily.length > 0) {
                const today = new Date().toISOString().split('T')[0];
                const todayData = daily.daily.find(d => d.date === today);
                const todayCost = todayData ? todayData.cost : 0;
                document.getElementById('analytics-today-cost').textContent = '$' + todayCost.toFixed(2);
            } else {
                document.getElementById('analytics-today-cost').textContent = '$0.00';
            }

            // Token breakdown
            document.getElementById('analytics-input-tokens').textContent = formatNumber(allTime.input_tokens || 0);
            document.getElementById('analytics-output-tokens').textContent = formatNumber(allTime.output_tokens || 0);
            document.getElementById('analytics-cache-read-tokens').textContent = formatNumber(allTime.cache_read_tokens || 0);
            document.getElementById('analytics-cache-write-tokens').textContent = formatNumber(allTime.cache_write_tokens || 0);

            // Render mission list
            renderEnhancedMissionList(summary.recent_missions || []);

            // Render token breakdown donut
            renderTokenBreakdownDonut({
                input: allTime.input_tokens || 0,
                output: allTime.output_tokens || 0,
                cache_read: allTime.cache_read_tokens || 0,
                cache_write: allTime.cache_write_tokens || 0
            });
        }

        // Render daily trend chart
        if (daily && !daily.error) {
            renderEnhancedTrendChart(daily.daily || [], daily.summary || {});
        }

        // Render stage analysis
        if (stages && !stages.error) {
            renderStageAnalysis(stages.stages || {});
        }

        // Render model comparison
        if (models && !models.error) {
            renderModelComparison(models.models || {});
        }

    } catch (e) {
        console.error('Full analytics error:', e);
    }
}

function renderEnhancedMissionList(missions) {
    const list = document.getElementById('analytics-missions-list');
    if (!missions || missions.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim);">No mission data</div>';
        return;
    }

    const html = missions.map(m => {
        const dateStr = m.started_at ? new Date(m.started_at).toLocaleDateString() : '-';
        const statusClass = (m.status || 'unknown').toLowerCase().replace(' ', '_');
        return `
            <div class="analytics-mission-item" onclick="window.showMissionAnalytics('${m.mission_id}')">
                <div class="analytics-mission-id">${m.mission_id || 'Unknown'}</div>
                <div class="analytics-mission-meta">
                    <span class="analytics-mission-cost">$${(m.cost_usd || m.cost || 0).toFixed(4)}</span>
                    <span class="analytics-mission-tokens">${formatNumber(m.total_tokens || m.tokens || 0)} tokens</span>
                </div>
                <div class="analytics-mission-date">
                    ${dateStr}
                    <span class="analytics-mission-status ${statusClass}">${m.status || 'unknown'}</span>
                </div>
            </div>
        `;
    }).join('');

    list.innerHTML = html;
}

function renderTokenBreakdownDonut(data) {
    const svg = document.getElementById('analytics-breakdown-svg');
    const totalEl = document.getElementById('analytics-breakdown-total');
    const legendEl = document.getElementById('analytics-breakdown-legend');

    if (!svg) return;

    const total = (data.input || 0) + (data.output || 0) + (data.cache_read || 0) + (data.cache_write || 0);
    if (total === 0) {
        svg.innerHTML = '<text x="100" y="100" text-anchor="middle" fill="#8b949e" font-size="14">No data</text>';
        totalEl.textContent = '0';
        legendEl.innerHTML = '';
        return;
    }

    // Format total for display
    totalEl.textContent = formatNumber(total);

    const segments = [
        { name: 'Input', value: data.input || 0, color: '#58a6ff' },
        { name: 'Output', value: data.output || 0, color: '#3fb950' },
        { name: 'Cache Read', value: data.cache_read || 0, color: '#d29922' },
        { name: 'Cache Write', value: data.cache_write || 0, color: '#bc8cff' }
    ].filter(s => s.value > 0);

    const cx = 100, cy = 100, r = 70;
    const strokeWidth = 20;
    const circumference = 2 * Math.PI * r;

    // Start at top of circle (rotate -90 degrees via transform)
    let offset = circumference * 0.25;  // Start at 12 o'clock
    let svgContent = '';

    segments.forEach((seg, i) => {
        const pct = seg.value / total;
        const dashLength = pct * circumference;

        svgContent += `
            <circle
                cx="${cx}" cy="${cy}" r="${r}"
                fill="none"
                stroke="${seg.color}"
                stroke-width="${strokeWidth}"
                stroke-dasharray="${dashLength} ${circumference - dashLength}"
                stroke-dashoffset="${offset}"
                data-name="${seg.name}"
                data-value="${seg.value}"
                data-pct="${(pct * 100).toFixed(1)}"
                style="transition: stroke-dashoffset 0.3s ease;"
            />
        `;

        offset -= dashLength;
    });

    svg.innerHTML = svgContent;

    // Render legend with percentages
    legendEl.innerHTML = segments.map(seg => {
        const pct = ((seg.value / total) * 100).toFixed(1);
        return `
            <div class="analytics-legend-item">
                <div class="analytics-legend-color" style="background: ${seg.color};"></div>
                <span>${seg.name} (${pct}%)</span>
            </div>
        `;
    }).join('');
}

function renderEnhancedTrendChart(daily, summary) {
    const canvas = document.getElementById('analytics-trend-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();

    // Ensure canvas has size (may be 0 if tab not visible)
    let w = rect.width || 400;
    let h = rect.height || 200;

    // If rect has no size, use canvas CSS dimensions
    if (w < 50) {
        const computedStyle = window.getComputedStyle(canvas);
        w = parseFloat(computedStyle.width) || 400;
        h = parseFloat(computedStyle.height) || 200;
    }

    canvas.width = w * window.devicePixelRatio;
    canvas.height = h * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const padding = { top: 20, right: 20, bottom: 40, left: 60 };

    // Clear
    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, 0, w, h);

    if (!daily || daily.length === 0) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '14px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('No data available', w / 2, h / 2);
        return;
    }

    const costs = daily.map(d => d.cost || 0);
    const maxCost = Math.max(...costs, 0.01);

    const graphWidth = w - padding.left - padding.right;
    const graphHeight = h - padding.top - padding.bottom;
    const barWidth = Math.max(4, (graphWidth / costs.length) - 2);

    // Draw grid lines
    ctx.strokeStyle = '#30363d';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (graphHeight * i / 4);
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();
    }

    // Draw Y-axis labels
    ctx.fillStyle = '#8b949e';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (graphHeight * i / 4);
        const val = maxCost * (1 - i / 4);
        ctx.fillText('$' + val.toFixed(2), padding.left - 5, y + 3);
    }

    // Draw bars with gradient
    const gradient = ctx.createLinearGradient(0, padding.top, 0, h - padding.bottom);
    gradient.addColorStop(0, '#58a6ff');
    gradient.addColorStop(1, '#1f6feb');

    const today = new Date().toISOString().split('T')[0];

    costs.forEach((cost, i) => {
        const barHeight = (cost / maxCost) * graphHeight;
        const x = padding.left + i * (barWidth + 2);
        const y = h - padding.bottom - barHeight;

        // Highlight today
        if (daily[i] && daily[i].date === today) {
            ctx.fillStyle = '#3fb950';
        } else {
            ctx.fillStyle = gradient;
        }

        ctx.fillRect(x, y, barWidth, barHeight);
    });

    // Draw X-axis labels (show every Nth label to avoid crowding)
    const labelStep = Math.ceil(daily.length / 8);
    ctx.fillStyle = '#8b949e';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';

    daily.forEach((d, i) => {
        if (i % labelStep === 0 || i === daily.length - 1) {
            const x = padding.left + i * (barWidth + 2) + barWidth / 2;
            const label = d.date ? d.date.slice(5) : ''; // MM-DD
            ctx.fillText(label, x, h - padding.bottom + 15);
        }
    });

    // Update summary stats (with null checks)
    if (summary) {
        const dailyAvgEl = document.getElementById('analytics-daily-avg');
        const peakDayEl = document.getElementById('analytics-peak-day');
        const peakCostEl = document.getElementById('analytics-peak-cost');

        if (dailyAvgEl) dailyAvgEl.textContent = '$' + (summary.avg_daily_cost || 0).toFixed(2);
        if (peakDayEl) peakDayEl.textContent = summary.peak_day || '-';
        if (peakCostEl) peakCostEl.textContent = '$' + (summary.peak_cost || 0).toFixed(2);
    }

    // Store data for hover (attach to canvas)
    canvas._chartData = daily;
    canvas._chartBounds = { w, h, padding, barWidth, maxCost, graphHeight };

    // Add hover handler if not already added
    if (!canvas._hasHover) {
        canvas._hasHover = true;
        canvas.addEventListener('mousemove', handleTrendChartHover);
        canvas.addEventListener('mouseleave', hideTrendChartTooltip);
    }
}

function handleTrendChartHover(e) {
    const canvas = e.target;
    const tooltip = document.getElementById('analytics-trend-tooltip');
    if (!canvas._chartData || !tooltip) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const { padding, barWidth, graphHeight, maxCost, w, h } = canvas._chartBounds;
    const daily = canvas._chartData;

    // Find which bar we're over
    const barAreaX = x - padding.left;
    const barIndex = Math.floor(barAreaX / (barWidth + 2));

    if (barIndex >= 0 && barIndex < daily.length && x > padding.left && x < w - padding.right) {
        const d = daily[barIndex];
        tooltip.innerHTML = `
            <div class="tooltip-date">${d.date || '-'}</div>
            <div class="tooltip-cost">$${(d.cost || 0).toFixed(4)}</div>
            <div class="tooltip-tokens">${formatNumber(d.total_tokens || 0)} tokens</div>
        `;
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX - rect.left + 10) + 'px';
        tooltip.style.top = (e.clientY - rect.top - 30) + 'px';
    } else {
        tooltip.style.display = 'none';
    }
}

function hideTrendChartTooltip() {
    const tooltip = document.getElementById('analytics-trend-tooltip');
    if (tooltip) tooltip.style.display = 'none';
}

function renderStageAnalysis(stages) {
    const container = document.getElementById('analytics-stage-chart');
    if (!container) return;

    const stageOrder = ['PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'CYCLE_END', 'COMPLETE'];
    const stageEntries = Object.entries(stages);

    if (stageEntries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No stage data available</div>';
        return;
    }

    // Sort stages by order, unknown stages at end
    stageEntries.sort((a, b) => {
        const idxA = stageOrder.indexOf(a[0]);
        const idxB = stageOrder.indexOf(b[0]);
        return (idxA === -1 ? 999 : idxA) - (idxB === -1 ? 999 : idxB);
    });

    // Find max tokens for scaling
    const maxTokens = Math.max(...stageEntries.map(([_, s]) => s.total_tokens || 0), 1);

    const html = stageEntries.map(([stageName, data]) => {
        const pct = ((data.total_tokens || 0) / maxTokens) * 100;
        const cost = data.cost || 0;
        const tokens = data.total_tokens || 0;

        return `
            <div class="analytics-stage-bar">
                <div class="analytics-stage-label">${stageName}</div>
                <div class="analytics-stage-track">
                    <div class="analytics-stage-fill ${stageName}" style="width: ${pct}%;">
                        ${pct > 15 ? `<span>${formatNumber(tokens)}</span>` : ''}
                    </div>
                </div>
                <div class="analytics-stage-value">
                    <span class="analytics-stage-cost">$${cost.toFixed(2)}</span>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function renderModelComparison(models) {
    const container = document.getElementById('analytics-model-grid');
    if (!container) return;

    const modelEntries = Object.entries(models);

    if (modelEntries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No model data available</div>';
        return;
    }

    // Sort by cost descending
    modelEntries.sort((a, b) => (b[1].cost || 0) - (a[1].cost || 0));

    const maxCost = Math.max(...modelEntries.map(([_, m]) => m.cost || 0), 0.01);

    const html = modelEntries.map(([modelId, data], idx) => {
        const displayName = data.display_name || modelId;
        const isPrimary = idx === 0;
        const isEfficient = modelId.includes('haiku');

        return `
            <div class="analytics-model-card">
                <div class="analytics-model-info">
                    <div class="analytics-model-name">
                        ${displayName}
                        ${isPrimary ? '<span class="analytics-model-badge primary">Primary</span>' : ''}
                        ${isEfficient ? '<span class="analytics-model-badge efficient">Efficient</span>' : ''}
                    </div>
                    <div class="analytics-model-tokens">${formatNumber(data.total_tokens || 0)} tokens</div>
                    <div class="analytics-model-events">${data.event_count || 0} API calls</div>
                </div>
                <div class="analytics-model-cost">$${(data.cost || 0).toFixed(2)}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

export async function openMissionAnalyticsModal(missionId) {
    const modal = document.getElementById('mission-analytics-modal');
    const body = document.getElementById('mission-analytics-modal-body');

    if (!modal || !body) return;

    modal.style.display = 'flex';
    body.innerHTML = '<div style="color: var(--text-dim);">Loading...</div>';

    try {
        const data = await api(`/api/analytics/mission/${missionId}/stages`);

        if (data.error) {
            body.innerHTML = `<div style="color: var(--red);">Error: ${data.error}</div>`;
            return;
        }

        const summary = data.summary || {};
        const stages = data.stages || [];

        let stagesHtml = '';
        if (stages.length > 0) {
            const maxTokens = Math.max(...stages.map(s => s.total_tokens || 0), 1);

            stagesHtml = stages.map(s => {
                const pct = ((s.total_tokens || 0) / maxTokens) * 100;
                return `
                    <div class="mission-stage-item">
                        <div class="mission-stage-name analytics-stage-fill ${s.stage}" style="padding: 4px 8px; border-radius: 4px;">${s.stage}</div>
                        <div class="mission-stage-bar">
                            <div class="mission-stage-bar-fill analytics-stage-fill ${s.stage}" style="width: ${pct}%;"></div>
                        </div>
                        <div class="mission-stage-stats">
                            ${formatNumber(s.total_tokens || 0)} tokens | $${(s.cost || 0).toFixed(4)}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            stagesHtml = '<div style="color: var(--text-dim);">No stage data available</div>';
        }

        body.innerHTML = `
            <div class="mission-analytics-header">
                <div>
                    <strong style="color: var(--accent);">${missionId}</strong>
                    <div style="color: var(--text-dim); font-size: 0.85em; margin-top: 4px;">
                        ${data.status || 'Unknown status'}
                    </div>
                </div>
                <div style="text-align: right;">
                    <div style="color: var(--text-dim); font-size: 0.8em;">Started</div>
                    <div>${data.started_at ? new Date(data.started_at).toLocaleString() : '-'}</div>
                </div>
            </div>

            <div class="mission-analytics-summary">
                <div class="mission-analytics-stat">
                    <div class="value cost">$${(summary.total_cost || 0).toFixed(4)}</div>
                    <div class="label">Total Cost</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${formatNumber(summary.total_tokens || 0)}</div>
                    <div class="label">Total Tokens</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${summary.stage_count || 0}</div>
                    <div class="label">Stages</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${stages.reduce((sum, s) => sum + (s.event_count || 0), 0)}</div>
                    <div class="label">API Calls</div>
                </div>
            </div>

            <div class="mission-analytics-stages">
                <h4>Stage Breakdown</h4>
                <div class="mission-stage-timeline">
                    ${stagesHtml}
                </div>
            </div>

            ${data.problem_statement ? `
                <div style="margin-top: 20px;">
                    <h4 style="color: var(--accent); margin-bottom: 10px; font-size: 0.9em;">Problem Statement</h4>
                    <div style="background: var(--bg); padding: 12px; border-radius: 6px; font-size: 0.9em;">
                        ${data.problem_statement.substring(0, 500)}${data.problem_statement.length > 500 ? '...' : ''}
                    </div>
                </div>
            ` : ''}
        `;
    } catch (e) {
        body.innerHTML = `<div style="color: var(--red);">Error loading mission data: ${e.message}</div>`;
    }
}

export function closeMissionAnalyticsModal() {
    const modal = document.getElementById('mission-analytics-modal');
    if (modal) modal.style.display = 'none';
}

export async function exportAnalyticsCSV() {
    try {
        const summary = analyticsCache.summary || await api('/api/analytics/summary');
        const missions = summary.recent_missions || [];

        if (missions.length === 0) {
            showToast('No data to export');
            return;
        }

        // Build CSV
        let csv = 'Mission ID,Started At,Status,Tokens,Cost (USD),Duration (s)\n';
        missions.forEach(m => {
            csv += `"${m.mission_id}","${m.started_at || ''}","${m.status || ''}",${m.total_tokens || m.tokens || 0},${m.cost_usd || m.cost || 0},${m.duration_seconds || 0}\n`;
        });

        // Download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `analytics_export_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast('Analytics exported to CSV');
    } catch (e) {
        console.error('Export error:', e);
        showToast('Export failed: ' + e.message);
    }
}
