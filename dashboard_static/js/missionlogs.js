/**
 * Dashboard Mission Logs Module
 * Mission logs tab for viewing historical mission reports
 * Dependencies: core.js, api.js
 */

// =============================================================================
// MISSION LOGS STATE
// =============================================================================

let missionLogsData = [];
let selectedMissionLogId = null;

// =============================================================================
// MISSION LOGS FUNCTIONS
// =============================================================================

async function loadMissionLogsTabData() {
    try {
        const data = await api('/api/mission-logs');
        missionLogsData = data.logs || [];

        // Update count
        document.getElementById('missionlogs-count').textContent = missionLogsData.length;

        // Render list
        renderMissionLogsList(missionLogsData);

    } catch (e) {
        console.error('Mission logs error:', e);
    }
}

function renderMissionLogsList(logs) {
    const container = document.getElementById('missionlogs-list');

    if (!logs || logs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No mission logs found</div>';
        return;
    }

    const html = logs.map(log => {
        const statusClass = log.success ? 'success' : (log.failed ? 'failed' : 'unknown');
        const date = log.timestamp ? new Date(log.timestamp).toLocaleDateString() : 'Unknown';
        const shortId = (log.mission_id || '').replace('mission_', '').slice(0, 8);

        return `
            <div class="missionlog-item ${log.mission_id === selectedMissionLogId ? 'selected' : ''}"
                 onclick="selectMissionLog('${log.mission_id}')">
                <div class="missionlog-header">
                    <span class="missionlog-id">${shortId}</span>
                    <span class="missionlog-status ${statusClass}">${log.outcome || 'Unknown'}</span>
                </div>
                <div class="missionlog-meta">
                    <span>${date}</span>
                    <span>${log.cycles || 0} cycles</span>
                    <span>${log.duration || 'Unknown'}</span>
                </div>
                <div class="missionlog-preview">${escapeHtml((log.summary || log.mission || '').substring(0, 80))}...</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

async function selectMissionLog(missionId) {
    selectedMissionLogId = missionId;
    renderMissionLogsList(missionLogsData);

    try {
        const data = await api('/api/mission-logs/' + missionId);
        renderMissionLogDetails(data);
    } catch (e) {
        console.error('Mission log details error:', e);
    }
}

function renderMissionLogDetails(data) {
    const container = document.getElementById('missionlogs-details');

    if (!data || data.error) {
        container.innerHTML = '<div style="color: var(--text-dim);">Select a mission to view details</div>';
        return;
    }

    const statusClass = data.success ? 'success' : (data.failed ? 'failed' : 'unknown');

    // Files created/modified
    const filesHtml = (data.files_created || []).length > 0 ? `
        <div class="missionlog-detail-section">
            <h4>Files Created</h4>
            <ul class="files-list">
                ${data.files_created.map(f => `<li>${escapeHtml(f)}</li>`).join('')}
            </ul>
        </div>
    ` : '';

    // Stage history
    const stagesHtml = (data.stage_history || []).length > 0 ? `
        <div class="missionlog-detail-section">
            <h4>Stage History</h4>
            <div class="stages-timeline">
                ${data.stage_history.map(s => `
                    <div class="stage-item">
                        <span class="stage-name">${s.stage}</span>
                        <span class="stage-time">${formatDuration(s.duration || 0)}</span>
                    </div>
                `).join('')}
            </div>
        </div>
    ` : '';

    // Learnings
    const learningsHtml = (data.learnings || []).length > 0 ? `
        <div class="missionlog-detail-section">
            <h4>Learnings (${data.learnings.length})</h4>
            <div class="learnings-list">
                ${data.learnings.slice(0, 5).map(l => `
                    <div class="learning-preview">
                        <span class="learning-type-badge ${l.type || ''}">${l.type || 'unknown'}</span>
                        ${escapeHtml(l.title || 'Untitled')}
                    </div>
                `).join('')}
                ${data.learnings.length > 5 ? `<div style="color: var(--text-dim);">...and ${data.learnings.length - 5} more</div>` : ''}
            </div>
        </div>
    ` : '';

    container.innerHTML = `
        <div class="missionlog-detail-header">
            <h3>${escapeHtml(data.mission_id || 'Unknown Mission')}</h3>
            <span class="missionlog-status ${statusClass}">${data.outcome || 'Unknown'}</span>
        </div>

        <div class="missionlog-detail-section">
            <h4>Summary</h4>
            <p>${escapeHtml(data.summary || data.mission || 'No summary available')}</p>
        </div>

        <div class="missionlog-detail-section">
            <h4>Statistics</h4>
            <div class="stats-grid">
                <div class="stat-item">
                    <span class="label">Duration:</span>
                    <span class="value">${data.duration || 'Unknown'}</span>
                </div>
                <div class="stat-item">
                    <span class="label">Cycles:</span>
                    <span class="value">${data.cycles || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="label">Iterations:</span>
                    <span class="value">${data.total_iterations || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="label">Tokens:</span>
                    <span class="value">${formatNumber(data.total_tokens || 0)}</span>
                </div>
                <div class="stat-item">
                    <span class="label">Cost:</span>
                    <span class="value">$${(data.total_cost || 0).toFixed(4)}</span>
                </div>
            </div>
        </div>

        ${filesHtml}
        ${stagesHtml}
        ${learningsHtml}

        <div class="missionlog-actions">
            <button class="btn" onclick="exportMissionLog('${data.mission_id}')">Export JSON</button>
            <button class="btn" onclick="viewMissionLogRaw('${data.mission_id}')">View Raw</button>
        </div>
    `;
}

async function exportMissionLog(missionId) {
    try {
        const data = await api('/api/mission-logs/' + missionId);
        downloadJSON(data, `mission_${missionId}.json`);
        showToast('Mission log exported');
    } catch (e) {
        showToast('Export error: ' + e.message, 'error');
    }
}

async function viewMissionLogRaw(missionId) {
    try {
        const data = await api('/api/mission-logs/' + missionId);
        const content = JSON.stringify(data, null, 2);

        // Create modal
        const modalHtml = `
            <div id="raw-log-modal" class="modal" style="display: flex;">
                <div class="modal-content" style="max-width: 800px; max-height: 80vh;">
                    <div class="modal-header">
                        <h3>Raw Mission Log: ${missionId}</h3>
                        <button class="modal-close" onclick="document.getElementById('raw-log-modal').remove()">&times;</button>
                    </div>
                    <div class="modal-body" style="overflow: auto;">
                        <pre style="font-size: 0.85em; white-space: pre-wrap; word-break: break-word;">${escapeHtml(content)}</pre>
                    </div>
                </div>
            </div>
        `;

        const existing = document.getElementById('raw-log-modal');
        if (existing) existing.remove();

        document.body.insertAdjacentHTML('beforeend', modalHtml);
    } catch (e) {
        showToast('Error loading raw log: ' + e.message, 'error');
    }
}

async function searchMissionLogs() {
    const query = document.getElementById('missionlogs-search').value.trim().toLowerCase();

    if (!query) {
        renderMissionLogsList(missionLogsData);
        return;
    }

    const filtered = missionLogsData.filter(log =>
        (log.mission_id || '').toLowerCase().includes(query) ||
        (log.summary || '').toLowerCase().includes(query) ||
        (log.mission || '').toLowerCase().includes(query)
    );

    renderMissionLogsList(filtered);
}

async function refreshMissionLogs() {
    await loadMissionLogsTabData();
    showToast('Mission logs refreshed');
}

// Debug: mark missionlogs module loaded
console.log('Mission logs module loaded');
