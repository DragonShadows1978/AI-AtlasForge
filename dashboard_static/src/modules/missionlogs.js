/**
 * Dashboard Mission Logs Module (ES6)
 * Mission logs tab for viewing historical mission reports
 * Dependencies: core.js, api.js
 */

import { escapeHtml, showToast, downloadJSON } from '../core.js';
import { api } from '../api.js';

// =============================================================================
// MISSION LOGS STATE
// =============================================================================

let missionLogsData = [];
let selectedMissionLogId = null;

// =============================================================================
// MISSION LOGS FUNCTIONS
// =============================================================================

export async function loadMissionLogsTabData() {
    try {
        const data = await api('/api/mission-logs');
        missionLogsData = data.logs || [];

        // Update dropdown
        const select = document.getElementById('missionlogs-select');
        if (select) {
            select.innerHTML = '<option value="">Select a mission log...</option>';
            missionLogsData.forEach(log => {
                const date = log.completed_at ? new Date(log.completed_at).toLocaleDateString() : 'Unknown';
                const cycles = log.total_cycles ? ` (${log.total_cycles} cycles)` : '';
                // Use DOM API — avoids mission_id XSS via innerHTML injection
                const opt = document.createElement('option');
                opt.value = log.mission_id;
                opt.textContent = `${log.mission_id}${cycles} - ${date}`;
                select.appendChild(opt);
            });
        }

        // Update stats
        const totalCycles = missionLogsData.reduce((sum, l) => sum + (l.total_cycles || 0), 0);
        const statsEl = document.getElementById('missionlogs-stats');
        if (statsEl) {
            statsEl.innerHTML = `
                <div class="glassbox-stat">
                    <div class="glassbox-stat-value">${missionLogsData.length}</div>
                    <div class="glassbox-stat-label">Mission Logs</div>
                </div>
                <div class="glassbox-stat">
                    <div class="glassbox-stat-value">${totalCycles}</div>
                    <div class="glassbox-stat-label">Total Cycles</div>
                </div>
            `;
        }

        // Render list
        renderMissionLogsList(missionLogsData);

    } catch (e) {
        console.error('Mission logs error:', e);
        const listEl = document.getElementById('missionlogs-list');
        if (listEl) {
            listEl.innerHTML = '<div style="color: var(--red);">Error loading logs: ' + (e.message || e) + '</div>';
        }
    }
}

function renderMissionLogsList(logs) {
    const container = document.getElementById('missionlogs-list');
    if (!container) return;

    if (!logs || logs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No mission logs found</div>';
        return;
    }

    const html = logs.map(log => {
        // Use the fields from the API response
        const date = log.completed_at ? new Date(log.completed_at).toLocaleDateString() : 'Unknown';
        const cycles = log.total_cycles || 0;
        const missionPreview = (log.original_mission || 'Unknown mission').substring(0, 80);

        const isSelected = log.mission_id === selectedMissionLogId ? ' selected' : '';
        return `
            <div class="glassbox-agent-item${isSelected}"
                 data-mission-id="${escapeHtml(log.mission_id)}">
                <div class="glassbox-agent-id">${escapeHtml(log.mission_id)}</div>
                <div class="glassbox-agent-meta">${escapeHtml(missionPreview)}${missionPreview.length >= 80 ? '...' : ''}</div>
                <div class="glassbox-agent-meta">${cycles} cycle(s) | ${date}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
    // Delegated click — avoids inline onclick with unescaped mission_id
    container.addEventListener('click', function _logClickHandler(e) {
        const item = e.target.closest('[data-mission-id]');
        if (item) selectMissionLog(item.dataset.missionId);
    }, { once: true });
}

export async function selectMissionLog(missionId) {
    selectedMissionLogId = missionId;

    // Update dropdown to match
    const select = document.getElementById('missionlogs-select');
    if (select) select.value = missionId;

    renderMissionLogsList(missionLogsData);

    try {
        const data = await api('/api/mission-logs/' + missionId);
        renderMissionLogDetails(data);
    } catch (e) {
        console.error('Mission log details error:', e);
        const container = document.getElementById('missionlogs-details');
        if (container) {
            container.innerHTML = `<div style="color: var(--red);">Error loading mission log: ${e.message || e}</div>`;
        }
    }
}

// Called by the dropdown select
export async function loadMissionLog() {
    const select = document.getElementById('missionlogs-select');
    const missionId = select ? select.value : '';

    if (!missionId) {
        const container = document.getElementById('missionlogs-details');
        if (container) {
            container.innerHTML = '<div style="color: var(--text-dim);">Select a mission log to view details</div>';
        }
        return;
    }

    await selectMissionLog(missionId);
}

function renderMissionLogDetails(data) {
    const container = document.getElementById('missionlogs-details');
    if (!container) return;

    if (!data || data.error) {
        container.innerHTML = '<div style="color: var(--text-dim);">Select a mission to view details</div>';
        return;
    }

    // Header info
    let html = `
        <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
            <h4 style="color: var(--accent); margin-bottom: 10px;">${escapeHtml(data.mission_id || 'Unknown')}</h4>
            <div style="font-size: 0.9em; margin-bottom: 10px;">
                <strong>Original Mission:</strong><br>
                <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px;">
                    ${escapeHtml(data.original_mission || 'Unknown')}
                </div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 0.85em;">
                <div><span style="color: var(--text-dim);">Started:</span> ${data.started_at ? new Date(data.started_at).toLocaleString() : 'Unknown'}</div>
                <div><span style="color: var(--text-dim);">Completed:</span> ${data.completed_at ? new Date(data.completed_at).toLocaleString() : 'Unknown'}</div>
                <div><span style="color: var(--text-dim);">Total Cycles:</span> ${data.total_cycles || 0}</div>
                <div><span style="color: var(--text-dim);">Total Iterations:</span> ${data.total_iterations || 0}</div>
            </div>
        </div>
    `;

    // Final summary
    if (data.final_summary) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Final Summary</h4>
                <div style="font-size: 0.9em;">${escapeHtml(data.final_summary)}</div>
            </div>
        `;
    }

    // Cycles
    if (data.cycles && data.cycles.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Cycle History</h4>
        `;
        data.cycles.forEach((cycle, idx) => {
            html += `
                <div style="margin-bottom: 10px; padding: 10px; background: var(--panel); border-radius: 4px; border-left: 3px solid var(--accent);">
                    <strong style="color: var(--accent);">Cycle ${cycle.cycle || idx + 1}</strong>
                    <div style="font-size: 0.85em; margin-top: 5px;">${escapeHtml(cycle.summary || 'No summary')}</div>
                    ${cycle.files_generated && cycle.files_generated.length > 0 ? `
                        <div style="font-size: 0.8em; margin-top: 5px; color: var(--text-dim);">
                            Files: ${cycle.files_generated.join(', ')}
                        </div>
                    ` : ''}
                </div>
            `;
        });
        html += '</div>';
    }

    // Deliverables / All Files
    if (data.deliverables && data.deliverables.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Deliverables</h4>
                <div style="font-size: 0.85em;">
                    ${data.deliverables.map(f => `<div style="padding: 3px 0;">- ${escapeHtml(f)}</div>`).join('')}
                </div>
            </div>
        `;
    }

    if (data.all_files && data.all_files.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">All Files Created (${data.all_files.length})</h4>
                <div style="font-size: 0.85em; max-height: 200px; overflow-y: auto;">
                    ${data.all_files.map(f => `<div style="padding: 3px 0;">- ${escapeHtml(f)}</div>`).join('')}
                </div>
            </div>
        `;
    }

    container.innerHTML = html;

    // Actions — use DOM API to avoid mission_id inline onclick injection
    const actionsDiv = document.createElement('div');
    actionsDiv.style.cssText = 'display: flex; gap: 10px;';
    const exportBtn = document.createElement('button');
    exportBtn.className = 'btn';
    exportBtn.textContent = 'Export JSON';
    exportBtn.addEventListener('click', () => exportMissionLog(data.mission_id));
    const rawBtn = document.createElement('button');
    rawBtn.className = 'btn';
    rawBtn.textContent = 'View Raw';
    rawBtn.addEventListener('click', () => viewMissionLogRaw(data.mission_id));
    actionsDiv.appendChild(exportBtn);
    actionsDiv.appendChild(rawBtn);
    container.appendChild(actionsDiv);
}

export async function exportMissionLog(missionId) {
    try {
        const data = await api('/api/mission-logs/' + missionId);
        downloadJSON(data, `mission_${missionId}.json`);
        showToast('Mission log exported');
    } catch (e) {
        showToast('Export error: ' + e.message, 'error');
    }
}

export async function viewMissionLogRaw(missionId) {
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

export function searchMissionLogs() {
    const searchInput = document.getElementById('missionlogs-search');
    const query = searchInput ? searchInput.value.trim().toLowerCase() : '';

    if (!query) {
        renderMissionLogsList(missionLogsData);
        return;
    }

    const filtered = missionLogsData.filter(log =>
        (log.mission_id || '').toLowerCase().includes(query) ||
        (log.original_mission || '').toLowerCase().includes(query)
    );

    renderMissionLogsList(filtered);
}

export async function refreshMissionLogs() {
    await loadMissionLogsTabData();
    showToast('Mission logs refreshed');
}

// Debug: mark missionlogs module loaded
console.log('Mission logs ES6 module loaded');
