/**
 * Dashboard GlassBox Module (Legacy)
 * GlassBox tab for viewing archived mission transcripts and agent hierarchy
 * Dependencies: core.js, api.js
 */

// =============================================================================
// GLASSBOX STATE
// =============================================================================

var glassboxMissions = [];
var selectedMissionId = null;
var glassboxCurrentPage = 1;
var glassboxPageLimit = 20;
var glassboxSearchQuery = '';
var glassboxDateFrom = '';
var glassboxDateTo = '';

// =============================================================================
// MAIN LOAD FUNCTION (called when GlassBox tab is shown)
// =============================================================================

async function loadGlassboxTabData() {
    try {
        // Build URL with filters
        var url = '/api/glassbox/missions?page=' + glassboxCurrentPage + '&limit=' + glassboxPageLimit;
        if (glassboxSearchQuery) url += '&search=' + encodeURIComponent(glassboxSearchQuery);
        if (glassboxDateFrom) url += '&from=' + glassboxDateFrom;
        if (glassboxDateTo) url += '&to=' + glassboxDateTo;

        // Load missions list and stats in parallel
        var missionsData = await api(url);
        var statsData = await api('/api/glassbox/stats');

        glassboxMissions = missionsData.missions || [];

        // Update stats in header
        updateGlassboxStats(statsData);

        // Update pagination info
        updateGlassboxPagination(missionsData.pagination || {});

        // Populate mission dropdown
        renderMissionDropdown(glassboxMissions);

        // Update mission count badge
        var countBadge = document.getElementById('glassbox-mission-count');
        if (countBadge) countBadge.textContent = (missionsData.pagination && missionsData.pagination.total) || glassboxMissions.length;

        // If we have missions and none selected, select the first one
        if (glassboxMissions.length > 0 && !selectedMissionId) {
            await selectGlassboxMission(glassboxMissions[0].mission_id);
        } else if (selectedMissionId) {
            // Reload selected mission data
            await loadMissionDetails(selectedMissionId);
        } else {
            // No missions - show placeholder
            showGlassboxEmptyState();
        }

    } catch (e) {
        console.error('GlassBox data error:', e);
        showToast('Error loading GlassBox: ' + e.message, 'error');
    }
}

function updateGlassboxStats(stats) {
    var statsEl = document.getElementById('glassbox-tab-stats');
    if (statsEl) {
        statsEl.innerHTML =
            '<span class="stat"><strong>' + (stats.total_missions || 0) + '</strong> missions</span>' +
            '<span class="stat"><strong>' + formatNumber(stats.total_tokens || 0) + '</strong> tokens</span>' +
            '<span class="stat"><strong>' + (stats.total_transcripts || 0) + '</strong> transcripts</span>';
    }
}

function updateGlassboxPagination(pagination) {
    var paginationEl = document.getElementById('glassbox-pagination');
    if (!paginationEl) return;

    var page = pagination.page || 1;
    var pages = pagination.pages || 1;
    var total = pagination.total || 0;
    var hasPrev = pagination.has_prev || false;
    var hasNext = pagination.has_next || false;

    paginationEl.innerHTML =
        '<button class="btn btn-sm" onclick="glassboxPrevPage()" ' + (!hasPrev ? 'disabled' : '') + '>← Prev</button>' +
        '<span style="color: var(--text-dim); font-size: 0.85em;">Page ' + page + ' of ' + pages + ' (' + total + ' total)</span>' +
        '<button class="btn btn-sm" onclick="glassboxNextPage()" ' + (!hasNext ? 'disabled' : '') + '>Next →</button>';
}

function renderMissionDropdown(missions) {
    var select = document.getElementById('glassbox-tab-mission-select');
    if (!select) return;

    var html = '<option value="">Select a mission...</option>';
    missions.forEach(function(m) {
        html += '<option value="' + m.mission_id + '"' + (m.mission_id === selectedMissionId ? ' selected' : '') + '>' +
            m.mission_id + ' (' + (m.transcript_count || 0) + ' transcripts)</option>';
    });
    select.innerHTML = html;
}

function showGlassboxEmptyState() {
    var stagesEl = document.getElementById('glassbox-tab-stages');
    var agentsEl = document.getElementById('glassbox-tab-agents');
    var logEl = document.getElementById('glassbox-tab-log');

    var emptyHtml = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No archived missions found</div>';

    if (stagesEl) stagesEl.innerHTML = emptyHtml;
    if (agentsEl) agentsEl.innerHTML = emptyHtml;
    if (logEl) logEl.innerHTML = emptyHtml;
}

// =============================================================================
// MISSION SELECTION AND DETAILS
// =============================================================================

async function selectGlassboxMission(missionId) {
    if (!missionId) return;

    selectedMissionId = missionId;

    // Update dropdown selection
    var select = document.getElementById('glassbox-tab-mission-select');
    if (select) select.value = missionId;

    await loadMissionDetails(missionId);
}

async function loadMissionDetails(missionId) {
    try {
        // Load timeline, agents, and decision log
        var timelineData = await api('/api/glassbox/missions/' + missionId + '/stages');
        var agentsData = await api('/api/glassbox/missions/' + missionId + '/agents');
        var logData = await api('/api/glassbox/missions/' + missionId + '/decision-log?limit=50');

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
    var container = document.getElementById('glassbox-tab-stages');
    if (!container) return;

    var stages = data.stages || [];

    if (stages.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No stage data available</div>';
        return;
    }

    // Calculate relative widths for Gantt-style display
    var totalDuration = data.total_duration_seconds || stages.reduce(function(sum, s) { return sum + (s.duration_seconds || 0); }, 0);

    var stageColors = {
        'PLANNING': 'var(--blue)',
        'BUILDING': 'var(--green)',
        'TESTING': 'var(--yellow)',
        'ANALYZING': 'var(--purple)',
        'CYCLE_END': 'var(--cyan)',
        'COMPLETE': 'var(--accent)'
    };

    var html = stages.map(function(s, idx) {
        var widthPct = totalDuration > 0 ? Math.max(5, (s.duration_seconds / totalDuration) * 100) : 100 / stages.length;
        var color = stageColors[s.stage] || 'var(--text-dim)';
        var formattedDuration = formatDuration(s.duration_seconds || 0);
        var tokens = formatNumber(s.tokens_used || 0);

        return '<div class="stage-bar" style="flex: 0 0 ' + widthPct + '%; background: ' + color + '; padding: 8px; margin: 2px; border-radius: 4px; cursor: pointer;" ' +
               'title="' + s.stage + ': ' + formattedDuration + ', ' + tokens + ' tokens">' +
               '<div style="font-weight: bold; font-size: 0.8em;">' + s.stage + '</div>' +
               '<div style="font-size: 0.7em; opacity: 0.8;">' + formattedDuration + '</div></div>';
    }).join('');

    container.innerHTML =
        '<div style="display: flex; align-items: stretch; min-height: 60px; margin-bottom: 10px;">' + html + '</div>' +
        '<div style="color: var(--text-dim); font-size: 0.8em; text-align: center;">Total: ' + formatDuration(totalDuration) + ' across ' + stages.length + ' stages</div>';
}

// =============================================================================
// AGENTS HIERARCHY RENDERER
// =============================================================================

function renderAgentsHierarchy(data) {
    var container = document.getElementById('glassbox-tab-agents');
    if (!container) return;

    var agents = data.all_agents || [];

    if (agents.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No agent data available</div>';
        return;
    }

    // Build agent tree
    var agentMap = {};
    agents.forEach(function(a) {
        agentMap[a.agent_id] = Object.assign({}, a, { children: [] });
    });

    // Link children to parents
    agents.forEach(function(a) {
        if (a.parent_agent_id && agentMap[a.parent_agent_id]) {
            agentMap[a.parent_agent_id].children.push(agentMap[a.agent_id]);
        }
    });

    // Find root agents
    var roots = agents.filter(function(a) { return !a.parent_agent_id || !agentMap[a.parent_agent_id]; });

    function renderAgentNode(agent, depth) {
        var node = agentMap[agent.agent_id] || agent;
        var indent = depth * 20;
        var hasChildren = node.children && node.children.length > 0;
        var sessionId = (agent.session_id || agent.agent_id).substring(0, 20);

        var html = '<div class="agent-node" style="margin-left: ' + indent + 'px; padding: 8px; border-left: 2px solid var(--accent); margin-bottom: 4px; cursor: pointer;" ' +
                   'onclick="viewAgentTranscript(\'' + selectedMissionId + '\', \'' + agent.agent_id + '\')">' +
                   '<div style="display: flex; justify-content: space-between; align-items: center;">' +
                   '<span style="font-weight: bold;">' + (hasChildren ? '▼' : '•') + ' ' + escapeHtml(sessionId) + '...</span>' +
                   '<span style="font-size: 0.8em; color: var(--text-dim);">' + (agent.model || 'unknown') + '</span></div>' +
                   '<div style="font-size: 0.8em; color: var(--text-dim); margin-top: 4px;">' +
                   formatNumber(agent.total_tokens || 0) + ' tokens | ' +
                   formatDuration(agent.duration_seconds || 0) + ' | ' +
                   (agent.tool_calls_count || 0) + ' tool calls</div></div>';

        if (hasChildren) {
            node.children.forEach(function(child) {
                html += renderAgentNode(child, depth + 1);
            });
        }

        return html;
    }

    var treeHtml = roots.map(function(r) { return renderAgentNode(r, 0); }).join('');

    container.innerHTML =
        '<div style="max-height: 400px; overflow-y: auto;">' + treeHtml + '</div>' +
        '<div style="color: var(--text-dim); font-size: 0.8em; text-align: center; margin-top: 10px;">' + agents.length + ' agents total</div>';
}

// =============================================================================
// DECISION LOG RENDERER
// =============================================================================

function renderDecisionLog(data) {
    var container = document.getElementById('glassbox-tab-log');
    if (!container) return;

    var events = data.events || [];

    if (events.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No decision log available</div>';
        return;
    }

    var eventIcons = {
        'stage_transition': '🔄',
        'file_write': '📝',
        'file_read': '📖',
        'error': '❌',
        'tool_call': '🔧',
        'agent_spawn': '🚀'
    };

    var html = events.map(function(e) {
        return '<div class="log-entry" style="padding: 8px; border-bottom: 1px solid var(--border); font-size: 0.85em;">' +
               '<div style="display: flex; justify-content: space-between; align-items: center;">' +
               '<span>' + (eventIcons[e.event_type] || '•') + ' ' + escapeHtml(e.event_type) + '</span>' +
               '<span style="color: var(--text-dim); font-size: 0.8em;">' + (e.timestamp ? formatTimeAgo(e.timestamp) : '') + '</span></div>' +
               '<div style="color: var(--text-dim); margin-top: 4px;">' + escapeHtml(e.description || '') + '</div></div>';
    }).join('');

    container.innerHTML =
        '<div style="max-height: 400px; overflow-y: auto;">' + html + '</div>' +
        '<div style="color: var(--text-dim); font-size: 0.8em; text-align: center; margin-top: 10px;">Showing ' + events.length + ' of ' + (data.total_count || events.length) + ' events</div>';
}

// =============================================================================
// TRANSCRIPT VIEWER
// =============================================================================

async function viewAgentTranscript(missionId, agentId) {
    var modal = document.getElementById('glassbox-modal');
    var title = document.getElementById('glassbox-modal-title');
    var content = document.getElementById('glassbox-modal-content');

    if (!modal || !content) return;

    title.textContent = 'Transcript: ' + agentId.substring(0, 30) + '...';
    content.innerHTML = '<div style="text-align: center; color: var(--text-dim);">Loading transcript...</div>';
    modal.style.display = 'flex';

    try {
        var data = await api('/api/glassbox/missions/' + missionId + '/transcripts/' + agentId);

        if (data.error) {
            content.innerHTML = '<div style="color: var(--red);">' + escapeHtml(data.error) + '</div>';
            return;
        }

        var messages = data.messages || [];

        if (messages.length === 0) {
            content.innerHTML = '<div style="color: var(--text-dim);">No messages in transcript</div>';
            return;
        }

        var html = messages.map(function(m) {
            var isAssistant = m.type === 'assistant';
            var bgColor = isAssistant ? 'var(--bg-card)' : 'var(--bg)';

            var contentHtml = '';
            if (Array.isArray(m.content)) {
                contentHtml = m.content.map(function(block) {
                    if (block.type === 'text') {
                        return '<div style="white-space: pre-wrap;">' + escapeHtml(block.text || '') + '</div>';
                    } else if (block.type === 'tool_use') {
                        return '<div style="background: var(--bg); padding: 8px; border-radius: 4px; margin: 4px 0;">' +
                               '<strong>Tool: ' + escapeHtml(block.name || 'unknown') + '</strong>' +
                               '<pre style="font-size: 0.8em; overflow-x: auto;">' + escapeHtml(JSON.stringify(block.input || {}, null, 2)) + '</pre></div>';
                    } else if (block.type === 'tool_result') {
                        return '<div style="background: var(--bg); padding: 8px; border-radius: 4px; margin: 4px 0; border-left: 3px solid var(--green);">' +
                               '<strong>Result</strong>' +
                               '<pre style="font-size: 0.8em; overflow-x: auto;">' + escapeHtml(String(block.content || '').substring(0, 1000)) + '</pre></div>';
                    }
                    return '';
                }).join('');
            } else {
                contentHtml = '<div style="white-space: pre-wrap;">' + escapeHtml(String(m.content || '')) + '</div>';
            }

            var usageInfo = m.usage ? '(' + formatNumber(m.usage.input_tokens || 0) + ' in, ' + formatNumber(m.usage.output_tokens || 0) + ' out)' : '';

            return '<div class="transcript-message" style="background: ' + bgColor + '; padding: 12px; border-radius: 6px; margin-bottom: 8px;">' +
                   '<div style="display: flex; justify-content: space-between; margin-bottom: 8px;">' +
                   '<span style="font-weight: bold; color: ' + (isAssistant ? 'var(--accent)' : 'var(--green)') + ';">' +
                   (isAssistant ? '🤖 Assistant' : '👤 User') + '</span>' +
                   '<span style="font-size: 0.8em; color: var(--text-dim);">' + (m.model || '') + ' ' + usageInfo + '</span></div>' +
                   contentHtml + '</div>';
        }).join('');

        content.innerHTML =
            '<div style="margin-bottom: 10px; color: var(--text-dim); font-size: 0.85em;">' +
            (data.message_count || messages.length) + ' messages | ' + formatNumber(data.total_tokens || 0) + ' tokens</div>' +
            '<div style="max-height: 60vh; overflow-y: auto;">' + html + '</div>';

    } catch (e) {
        content.innerHTML = '<div style="color: var(--red);">Error: ' + escapeHtml(e.message) + '</div>';
    }
}

function closeGlassboxModal() {
    var modal = document.getElementById('glassbox-modal');
    if (modal) modal.style.display = 'none';
}

// =============================================================================
// SEARCH AND FILTERS
// =============================================================================

function glassboxSearch(query) {
    glassboxSearchQuery = query;
    glassboxCurrentPage = 1;
    loadGlassboxTabData();
}

function glassboxDateFilter() {
    var fromEl = document.getElementById('glassbox-date-from');
    var toEl = document.getElementById('glassbox-date-to');

    glassboxDateFrom = fromEl ? fromEl.value : '';
    glassboxDateTo = toEl ? toEl.value : '';
    glassboxCurrentPage = 1;
    loadGlassboxTabData();
}

function glassboxPrevPage() {
    if (glassboxCurrentPage > 1) {
        glassboxCurrentPage--;
        loadGlassboxTabData();
    }
}

function glassboxNextPage() {
    glassboxCurrentPage++;
    loadGlassboxTabData();
}

// =============================================================================
// TAB DROPDOWN HANDLER
// =============================================================================

function loadGlassboxTabMission() {
    var select = document.getElementById('glassbox-tab-mission-select');
    if (select && select.value) {
        selectGlassboxMission(select.value);
    }
}

// =============================================================================
// WIDGET CARD FUNCTIONS (for AtlasForge tab widget)
// =============================================================================

async function loadGlassboxMission() {
    var select = document.getElementById('glassbox-mission-select');
    if (!select || !select.value) return;

    var missionId = select.value;

    try {
        var data = await api('/api/glassbox/missions/' + missionId + '/timeline');

        var contentEl = document.getElementById('glassbox-content');
        if (!contentEl) return;

        var stages = data.stages || [];
        var agents = data.all_agents || [];

        contentEl.innerHTML =
            '<div style="font-size: 0.85em;">' +
            '<div><strong>Stages:</strong> ' + stages.length + '</div>' +
            '<div><strong>Agents:</strong> ' + agents.length + '</div>' +
            '<div><strong>Total Tokens:</strong> ' + formatNumber(data.total_tokens || 0) + '</div>' +
            '<div><strong>Duration:</strong> ' + formatDuration(data.total_duration_seconds || 0) + '</div></div>';

    } catch (e) {
        console.error('GlassBox widget error:', e);
    }
}

async function refreshGlassbox() {
    await loadGlassboxTabData();
    showToast('GlassBox refreshed');
}

// Debug: mark glassbox module loaded
console.log('GlassBox module loaded');
