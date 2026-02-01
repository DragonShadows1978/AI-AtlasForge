/**
 * Dashboard GlassBox Module (ES6) - Lazy Loaded
 * GlassBox tab for viewing archived mission transcripts and agent hierarchy
 * Dependencies: core.js, api.js
 */

import { escapeHtml, formatDuration, formatTimeAgo, showToast, formatNumber } from '../core.js';
import { api } from '../api.js';

// =============================================================================
// GLASSBOX STATE
// =============================================================================

let glassboxMissions = [];
let selectedMissionId = null;
let currentPage = 1;
let pageLimit = 20;
let searchQuery = '';
let dateFrom = '';
let dateTo = '';

// =============================================================================
// MAIN LOAD FUNCTION (called when GlassBox tab is shown)
// =============================================================================

export async function loadGlassboxTabData() {
    try {
        // Load missions list and stats in parallel
        const [missionsData, statsData] = await Promise.all([
            api(`/api/glassbox/missions?page=${currentPage}&limit=${pageLimit}${searchQuery ? '&search=' + encodeURIComponent(searchQuery) : ''}${dateFrom ? '&from=' + dateFrom : ''}${dateTo ? '&to=' + dateTo : ''}`),
            api('/api/glassbox/stats')
        ]);

        glassboxMissions = missionsData.missions || [];

        // Update stats in header
        updateGlassboxStats(statsData);

        // Update pagination info
        updateGlassboxPagination(missionsData.pagination || {});

        // Populate mission dropdown
        renderMissionDropdown(glassboxMissions);

        // Update mission count badge
        const countBadge = document.getElementById('glassbox-mission-count');
        if (countBadge) countBadge.textContent = missionsData.pagination?.total || glassboxMissions.length;

        // If we have missions and none selected, select the first one
        if (glassboxMissions.length > 0 && !selectedMissionId) {
            await selectGlassboxMission(glassboxMissions[0].mission_id);
        } else if (selectedMissionId) {
            // Reload selected mission data
            await loadMissionDetails(selectedMissionId);
        } else {
            // No missions - show placeholder
            showEmptyState();
        }

    } catch (e) {
        console.error('GlassBox data error:', e);
        showToast('Error loading GlassBox: ' + e.message, 'error');
    }
}

function updateGlassboxStats(stats) {
    const statsEl = document.getElementById('glassbox-tab-stats');
    if (statsEl) {
        statsEl.innerHTML = `
            <span class="stat"><strong>${stats.total_missions || 0}</strong> missions</span>
            <span class="stat"><strong>${formatNumber(stats.total_tokens || 0)}</strong> tokens</span>
        `;
    }
}

function updateGlassboxPagination(pagination) {
    const paginationEl = document.getElementById('glassbox-pagination');
    if (!paginationEl) return;

    const { page = 1, pages = 1, total = 0, has_prev = false, has_next = false } = pagination;

    paginationEl.innerHTML = `
        <button class="btn btn-sm" onclick="glassboxPrevPage()" ${!has_prev ? 'disabled' : ''}>← Prev</button>
        <span style="color: var(--text-dim); font-size: 0.85em;">Page ${page} of ${pages} (${total} total)</span>
        <button class="btn btn-sm" onclick="glassboxNextPage()" ${!has_next ? 'disabled' : ''}>Next →</button>
    `;
}

function renderMissionDropdown(missions) {
    const select = document.getElementById('glassbox-tab-mission-select');
    if (!select) return;

    select.innerHTML = '<option value="">Select a mission...</option>' +
        missions.map(m => `
            <option value="${m.mission_id}" ${m.mission_id === selectedMissionId ? 'selected' : ''}>
                ${m.mission_id} (${formatNumber(m.total_tokens || 0)} tokens)
            </option>
        `).join('');
}

function showEmptyState() {
    const stagesEl = document.getElementById('glassbox-tab-stages');
    const agentsEl = document.getElementById('glassbox-tab-agents');
    const logEl = document.getElementById('glassbox-tab-log');

    const emptyHtml = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No archived missions found</div>';

    if (stagesEl) stagesEl.innerHTML = emptyHtml;
    if (agentsEl) agentsEl.innerHTML = emptyHtml;
    if (logEl) logEl.innerHTML = emptyHtml;
}

// =============================================================================
// MISSION SELECTION AND DETAILS
// =============================================================================

export async function selectGlassboxMission(missionId) {
    if (!missionId) return;

    selectedMissionId = missionId;

    // Update dropdown selection
    const select = document.getElementById('glassbox-tab-mission-select');
    if (select) select.value = missionId;

    await loadMissionDetails(missionId);
}

async function loadMissionDetails(missionId) {
    try {
        // Load timeline, agents, and decision log in parallel
        const [timelineData, agentsData, logData] = await Promise.all([
            api(`/api/glassbox/missions/${missionId}/stages`),
            api(`/api/glassbox/missions/${missionId}/agents`),
            api(`/api/glassbox/missions/${missionId}/decision-log?limit=50`)
        ]);

        renderStagesTimeline(timelineData);
        renderAgentsHierarchy(agentsData);
        renderDecisionLog(logData);

    } catch (e) {
        console.error('Mission details error:', e);
        showToast('Error loading mission: ' + e.message, 'error');
    }
}

// =============================================================================
// STAGES TIMELINE RENDERER
// =============================================================================

function renderStagesTimeline(data) {
    const container = document.getElementById('glassbox-tab-stages');
    if (!container) return;

    const stages = data.stages || [];

    if (stages.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No stage data available</div>';
        return;
    }

    // Calculate relative widths for Gantt-style display
    const totalDuration = data.total_duration_seconds || stages.reduce((sum, s) => sum + (s.duration_seconds || 0), 0);

    const stageColors = {
        'PLANNING': 'var(--blue)',
        'BUILDING': 'var(--green)',
        'TESTING': 'var(--yellow)',
        'ANALYZING': 'var(--purple)',
        'CYCLE_END': 'var(--cyan)',
        'COMPLETE': 'var(--accent)'
    };

    const html = stages.map((s, idx) => {
        const widthPct = totalDuration > 0 ? Math.max(5, (s.duration_seconds / totalDuration) * 100) : 100 / stages.length;
        const color = stageColors[s.stage] || 'var(--text-dim)';
        const formattedDuration = formatDuration(s.duration_seconds || 0);
        const tokens = formatNumber(s.tokens_used || 0);

        return `
            <div class="stage-bar" style="flex: 0 0 ${widthPct}%; background: ${color}; padding: 8px; margin: 2px; border-radius: 4px; cursor: pointer;"
                 title="${s.stage}: ${formattedDuration}, ${tokens} tokens"
                 onclick="showStageDetails(${idx})">
                <div style="font-weight: bold; font-size: 0.8em;">${s.stage}</div>
                <div style="font-size: 0.7em; opacity: 0.8;">${formattedDuration}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = `
        <div style="display: flex; align-items: stretch; min-height: 60px; margin-bottom: 10px;">
            ${html}
        </div>
        <div style="color: var(--text-dim); font-size: 0.8em; text-align: center;">
            Total: ${formatDuration(totalDuration)} across ${stages.length} stages
        </div>
    `;
}

// =============================================================================
// AGENTS HIERARCHY RENDERER
// =============================================================================

function renderAgentsHierarchy(data) {
    const container = document.getElementById('glassbox-tab-agents');
    if (!container) return;

    const agents = data.all_agents || [];
    const rootAgents = data.root_agents || [];

    if (agents.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No agent data available</div>';
        return;
    }

    // Build agent tree
    const agentMap = {};
    agents.forEach(a => {
        agentMap[a.agent_id] = { ...a, children: [] };
    });

    // Link children to parents
    agents.forEach(a => {
        if (a.parent_agent_id && agentMap[a.parent_agent_id]) {
            agentMap[a.parent_agent_id].children.push(agentMap[a.agent_id]);
        }
    });

    // Find root agents (no parent or parent not in list)
    const roots = agents.filter(a => !a.parent_agent_id || !agentMap[a.parent_agent_id]);

    function renderAgentNode(agent, depth = 0) {
        const node = agentMap[agent.agent_id] || agent;
        const indent = depth * 20;
        const hasChildren = node.children && node.children.length > 0;

        let html = `
            <div class="agent-node" style="margin-left: ${indent}px; padding: 8px; border-left: 2px solid var(--accent); margin-bottom: 4px; cursor: pointer;"
                 onclick="viewAgentTranscript('${selectedMissionId}', '${agent.agent_id}')">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-weight: bold;">${hasChildren ? '▼' : '•'} ${escapeHtml(agent.session_id || agent.agent_id).substring(0, 20)}...</span>
                    <span style="font-size: 0.8em; color: var(--text-dim);">${agent.model || 'unknown'}</span>
                </div>
                <div style="font-size: 0.8em; color: var(--text-dim); margin-top: 4px;">
                    ${formatNumber(agent.total_tokens || 0)} tokens |
                    ${formatDuration(agent.duration_seconds || 0)} |
                    ${agent.tool_calls_count || 0} tool calls
                </div>
            </div>
        `;

        if (hasChildren) {
            node.children.forEach(child => {
                html += renderAgentNode(child, depth + 1);
            });
        }

        return html;
    }

    const treeHtml = roots.map(r => renderAgentNode(r)).join('');

    container.innerHTML = `
        <div style="max-height: 400px; overflow-y: auto;">
            ${treeHtml}
        </div>
        <div style="color: var(--text-dim); font-size: 0.8em; text-align: center; margin-top: 10px;">
            ${agents.length} agents total
        </div>
    `;
}

// =============================================================================
// DECISION LOG RENDERER
// =============================================================================

function renderDecisionLog(data) {
    const container = document.getElementById('glassbox-tab-log');
    if (!container) return;

    const events = data.events || [];

    if (events.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No decision log available</div>';
        return;
    }

    const eventIcons = {
        'stage_transition': '🔄',
        'file_write': '📝',
        'file_read': '📖',
        'error': '❌',
        'tool_call': '🔧',
        'agent_spawn': '🚀'
    };

    const html = events.map(e => `
        <div class="log-entry" style="padding: 8px; border-bottom: 1px solid var(--border); font-size: 0.85em;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span>${eventIcons[e.event_type] || '•'} ${escapeHtml(e.event_type)}</span>
                <span style="color: var(--text-dim); font-size: 0.8em;">${e.timestamp ? formatTimeAgo(e.timestamp) : ''}</span>
            </div>
            <div style="color: var(--text-dim); margin-top: 4px;">
                ${escapeHtml(e.description || '')}
            </div>
        </div>
    `).join('');

    container.innerHTML = `
        <div style="max-height: 400px; overflow-y: auto;">
            ${html}
        </div>
        <div style="color: var(--text-dim); font-size: 0.8em; text-align: center; margin-top: 10px;">
            Showing ${events.length} of ${data.total_count || events.length} events
        </div>
    `;
}

// =============================================================================
// TRANSCRIPT VIEWER
// =============================================================================

export async function viewAgentTranscript(missionId, agentId) {
    const modal = document.getElementById('glassbox-modal');
    const title = document.getElementById('glassbox-modal-title');
    const content = document.getElementById('glassbox-modal-content');

    if (!modal || !content) return;

    title.textContent = `Transcript: ${agentId.substring(0, 30)}...`;
    content.innerHTML = '<div style="text-align: center; color: var(--text-dim);">Loading transcript...</div>';
    modal.style.display = 'flex';

    try {
        const data = await api(`/api/glassbox/missions/${missionId}/transcripts/${agentId}`);

        if (data.error) {
            content.innerHTML = `<div style="color: var(--red);">${escapeHtml(data.error)}</div>`;
            return;
        }

        const messages = data.messages || [];

        if (messages.length === 0) {
            content.innerHTML = '<div style="color: var(--text-dim);">No messages in transcript</div>';
            return;
        }

        const html = messages.map(m => {
            const isAssistant = m.type === 'assistant';
            const bgColor = isAssistant ? 'var(--bg-card)' : 'var(--bg)';

            let contentHtml = '';
            if (Array.isArray(m.content)) {
                contentHtml = m.content.map(block => {
                    if (block.type === 'text') {
                        return `<div style="white-space: pre-wrap;">${escapeHtml(block.text || '')}</div>`;
                    } else if (block.type === 'tool_use') {
                        return `
                            <div style="background: var(--bg); padding: 8px; border-radius: 4px; margin: 4px 0;">
                                <strong>Tool: ${escapeHtml(block.name || 'unknown')}</strong>
                                <pre style="font-size: 0.8em; overflow-x: auto;">${escapeHtml(JSON.stringify(block.input || {}, null, 2))}</pre>
                            </div>
                        `;
                    } else if (block.type === 'tool_result') {
                        return `
                            <div style="background: var(--bg); padding: 8px; border-radius: 4px; margin: 4px 0; border-left: 3px solid var(--green);">
                                <strong>Result</strong>
                                <pre style="font-size: 0.8em; overflow-x: auto;">${escapeHtml(String(block.content || '').substring(0, 1000))}</pre>
                            </div>
                        `;
                    }
                    return '';
                }).join('');
            } else {
                contentHtml = `<div style="white-space: pre-wrap;">${escapeHtml(String(m.content || ''))}</div>`;
            }

            return `
                <div class="transcript-message" style="background: ${bgColor}; padding: 12px; border-radius: 6px; margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span style="font-weight: bold; color: ${isAssistant ? 'var(--accent)' : 'var(--green)'};">
                            ${isAssistant ? '🤖 Assistant' : '👤 User'}
                        </span>
                        <span style="font-size: 0.8em; color: var(--text-dim);">
                            ${m.model || ''} ${m.usage ? `(${formatNumber(m.usage.input_tokens || 0)} in, ${formatNumber(m.usage.output_tokens || 0)} out)` : ''}
                        </span>
                    </div>
                    ${contentHtml}
                </div>
            `;
        }).join('');

        content.innerHTML = `
            <div style="margin-bottom: 10px; color: var(--text-dim); font-size: 0.85em;">
                ${data.message_count || messages.length} messages | ${formatNumber(data.total_tokens || 0)} tokens
            </div>
            <div style="max-height: 60vh; overflow-y: auto;">
                ${html}
            </div>
        `;

    } catch (e) {
        content.innerHTML = `<div style="color: var(--red);">Error: ${escapeHtml(e.message)}</div>`;
    }
}

export function closeGlassboxModal() {
    const modal = document.getElementById('glassbox-modal');
    if (modal) modal.style.display = 'none';
}

// =============================================================================
// SEARCH AND FILTERS
// =============================================================================

export function glassboxSearch(query) {
    searchQuery = query;
    currentPage = 1;
    loadGlassboxTabData();
}

export function glassboxDateFilter() {
    const fromEl = document.getElementById('glassbox-date-from');
    const toEl = document.getElementById('glassbox-date-to');

    dateFrom = fromEl ? fromEl.value : '';
    dateTo = toEl ? toEl.value : '';
    currentPage = 1;
    loadGlassboxTabData();
}

export function glassboxPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        loadGlassboxTabData();
    }
}

export function glassboxNextPage() {
    currentPage++;
    loadGlassboxTabData();
}

// =============================================================================
// TAB DROPDOWN HANDLER
// =============================================================================

export function loadGlassboxTabMission() {
    const select = document.getElementById('glassbox-tab-mission-select');
    if (select && select.value) {
        selectGlassboxMission(select.value);
    }
}

// =============================================================================
// WIDGET CARD FUNCTIONS (for AtlasForge tab widget)
// =============================================================================

// Cache for widget mission summaries
let widgetMissionCache = {};

export async function loadGlassboxMission() {
    const select = document.getElementById('glassbox-mission-select');
    if (!select || !select.value) return;

    const missionId = select.value;

    try {
        const data = await api(`/api/glassbox/missions/${missionId}/timeline`);

        // Cache the data for the view button
        widgetMissionCache[missionId] = data;

        // Show the popup modal with summary
        showGlassboxWidgetPopup(missionId, data);

    } catch (e) {
        console.error('GlassBox widget error:', e);
        showToast('Error loading mission: ' + e.message, 'error');
    }
}

function showGlassboxWidgetPopup(missionId, data) {
    const stages = data.stages || [];
    const agents = data.all_agents || [];

    // Create or get popup container
    let popup = document.getElementById('glassbox-widget-popup');
    if (!popup) {
        popup = document.createElement('div');
        popup.id = 'glassbox-widget-popup';
        popup.className = 'modal';
        popup.innerHTML = `
            <div class="modal-content" style="max-width: 500px;">
                <button class="modal-close" onclick="closeGlassboxWidgetPopup()">&times;</button>
                <h3 id="glassbox-popup-title" style="margin-bottom: 15px;">Mission Summary</h3>
                <div id="glassbox-popup-content"></div>
                <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
                    <button class="btn" onclick="closeGlassboxWidgetPopup()">Close</button>
                    <button class="btn btn-primary" id="glassbox-popup-view-btn" onclick="viewGlassboxMissionInTab()">View in GlassBox →</button>
                </div>
            </div>
        `;
        document.body.appendChild(popup);
    }

    // Update content
    document.getElementById('glassbox-popup-title').textContent = `Mission: ${missionId}`;
    document.getElementById('glassbox-popup-content').innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px;">
            <div class="stat-box" style="background: var(--bg); padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: var(--accent);">${stages.length}</div>
                <div style="font-size: 0.85em; color: var(--text-dim);">Stages</div>
            </div>
            <div class="stat-box" style="background: var(--bg); padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: var(--green);">${agents.length}</div>
                <div style="font-size: 0.85em; color: var(--text-dim);">Agents</div>
            </div>
            <div class="stat-box" style="background: var(--bg); padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: var(--yellow);">${formatNumber(data.total_tokens || 0)}</div>
                <div style="font-size: 0.85em; color: var(--text-dim);">Tokens</div>
            </div>
            <div class="stat-box" style="background: var(--bg); padding: 12px; border-radius: 6px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: var(--purple);">${formatDuration(data.total_duration_seconds || 0)}</div>
                <div style="font-size: 0.85em; color: var(--text-dim);">Duration</div>
            </div>
        </div>
        ${stages.length > 0 ? `
        <div style="margin-top: 15px;">
            <h4 style="margin-bottom: 8px; font-size: 0.9em;">Stage Timeline:</h4>
            <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                ${stages.map(s => {
                    const stageColors = {
                        'PLANNING': 'var(--blue)',
                        'BUILDING': 'var(--green)',
                        'TESTING': 'var(--yellow)',
                        'ANALYZING': 'var(--purple)',
                        'CYCLE_END': 'var(--cyan)',
                        'COMPLETE': 'var(--accent)'
                    };
                    const color = stageColors[s.stage] || 'var(--text-dim)';
                    return `<span style="background: ${color}; padding: 4px 8px; border-radius: 4px; font-size: 0.75em;">${s.stage}</span>`;
                }).join('')}
            </div>
        </div>
        ` : ''}
    `;

    // Store the selected mission ID for the view button
    popup.dataset.missionId = missionId;

    // Show the popup
    popup.style.display = 'flex';
}

export function closeGlassboxWidgetPopup() {
    const popup = document.getElementById('glassbox-widget-popup');
    if (popup) {
        popup.style.display = 'none';
    }
}

export function viewGlassboxMissionInTab() {
    const popup = document.getElementById('glassbox-widget-popup');
    const missionId = popup?.dataset.missionId;

    if (!missionId) return;

    // Close the popup
    closeGlassboxWidgetPopup();

    // Set the selected mission ID so it will be loaded when tab opens
    selectedMissionId = missionId;

    // Switch to the GlassBox tab (this will trigger loadGlassboxTabData via the tab loader)
    if (typeof window.switchTab === 'function') {
        window.switchTab('glassbox');
    }
}

export async function refreshGlassbox() {
    await loadGlassboxTabData();
    showToast('GlassBox refreshed');
}

// =============================================================================
// INITIALIZATION
// =============================================================================

export function init() {
    console.log('GlassBox module initialized');

    // Expose functions to window for onclick handlers
    window.selectGlassboxMission = selectGlassboxMission;
    window.viewAgentTranscript = viewAgentTranscript;
    window.closeGlassboxModal = closeGlassboxModal;
    window.loadGlassboxTabMission = loadGlassboxTabMission;
    window.glassboxSearch = glassboxSearch;
    window.glassboxDateFilter = glassboxDateFilter;
    window.glassboxPrevPage = glassboxPrevPage;
    window.glassboxNextPage = glassboxNextPage;
    window.loadGlassboxMission = loadGlassboxMission;
    window.refreshGlassbox = refreshGlassbox;
    // Alias for WebSocket handler compatibility (widgets.js calls refreshGlassboxWidget)
    window.refreshGlassboxWidget = refreshGlassbox;
    // Widget popup functions
    window.closeGlassboxWidgetPopup = closeGlassboxWidgetPopup;
    window.viewGlassboxMissionInTab = viewGlassboxMissionInTab;
}
