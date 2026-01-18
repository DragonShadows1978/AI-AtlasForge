// Debug: mark script start
console.log('Main script starting');

// Safe socket initialization
let socket = null;
let widgetSocket = null;
try {
    if (typeof io !== 'undefined') {
        socket = io();
        console.log('Main socket created');
    } else {
        console.warn('Socket.io not loaded');
    }
} catch (e) {
    console.error('Socket init error:', e);
}

const stages = ['PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'CYCLE_END', 'COMPLETE'];

// Mission modal state
let fullMissionText = '';

// Recommendations state
let recommendations = [];
let selectedRecId = null;

// Utility function to escape HTML
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Socket events (only if socket exists)
if (socket) {
    socket.on('connect', () => console.log('Connected'));
    socket.on('message', (data) => addMessage(data.role, data.content, data.timestamp));
}

// =============================================================
// WIDGET REAL-TIME UPDATES - Added in Cycle 3
// =============================================================
try {
    if (typeof io !== 'undefined') {
        widgetSocket = io('/widgets');
        console.log('Widget socket created');
    }
} catch (e) {
    console.error('Widget socket init error:', e);
}
let widgetUpdateEnabled = true;

if (widgetSocket) {
    widgetSocket.on('connect', () => {
        console.log('Widget socket connected');
    // Subscribe to widget rooms
    widgetSocket.emit('subscribe', {room: 'mission_status'});
    widgetSocket.emit('subscribe', {room: 'journal'});
    widgetSocket.emit('subscribe', {room: 'atlasforge_stats'});
    widgetSocket.emit('subscribe', {room: 'decision_graph'});
});

widgetSocket.on('subscribed', (data) => {
    console.log('Subscribed to widget room:', data.room);
});

widgetSocket.on('update', (data) => {
    if (!widgetUpdateEnabled) return;

    const room = data.room;
    const payload = data.data;

    if (room === 'mission_status') {
        // Update mission status UI from WebSocket
        if (payload.rd_stage) {
            updateStatusBar(payload);
        }
    } else if (room === 'journal') {
        // Update journal entries from WebSocket
        if (payload.entries && Array.isArray(payload.entries)) {
            renderJournalEntries(payload.entries);
        }
    }
    });
} // end if (widgetSocket)

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
// =============================================================
// END WIDGET REAL-TIME UPDATES
// =============================================================

// Journal Entry Toggle
function toggleJournalEntry(el) {
    el.classList.toggle('expanded');
    saveJournalExpandedStates();
}

// Mission Modal Functions
async function openMissionModal() {
    // Fetch fresh from API to avoid scope issues
    try {
        const data = await fetch('/api/status').then(r => r.json());
        document.getElementById('mission-full-text').textContent = data.mission || 'No mission set';
    } catch(e) {
        document.getElementById('mission-full-text').textContent = window.fullMissionText || fullMissionText || 'Error loading mission';
    }
    document.getElementById('mission-modal').classList.add('show');
}
window.openMissionModal = openMissionModal;

function closeMissionModal() {
    document.getElementById('mission-modal').classList.remove('show');
}
window.closeMissionModal = closeMissionModal;

function copyMission() {
    navigator.clipboard.writeText(fullMissionText).then(() => {
        showToast('Mission copied to clipboard');
    });
}
window.copyMission = copyMission;

// Collapsible card functionality
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

// =====================================================================
// JOURNAL EXPANSION STATE PERSISTENCE
// =====================================================================

// Save expanded journal entries to localStorage
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

// Get expanded entry IDs from localStorage
function getJournalExpandedStates() {
    try {
        return JSON.parse(localStorage.getItem('journalExpandedEntries') || '[]');
    } catch (e) {
        return [];
    }
}

// Restore expansion state after render
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

// Expand all journal entries
function expandAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.add('expanded');
    });
    saveJournalExpandedStates();
}

// Collapse all journal entries
function collapseAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.remove('expanded');
    });
    saveJournalExpandedStates();
}

// =====================================================================
// MAIN TAB SWITCHING
// =====================================================================

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.main-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === tabName + '-tab');
    });

    // Save preference
    localStorage.setItem('activeTab', tabName);

    // Load GlassBox data if switching to that tab
    if (tabName === 'glassbox') {
        loadGlassboxTabData();
    }

    // Load Mission Logs data if switching to that tab
    if (tabName === 'missionlogs') {
        loadMissionLogsTabData();
    }

    // Load Bug Bounty data if switching to that tab
    if (tabName === 'bugbounty') {
        refreshBugBountyData();
    }

    // Load Narrative data if switching to that tab
    if (tabName === 'narrative') {
        initNarrativeTab();
    }
}

// Initialize tab from localStorage on page load
function initTabs() {
    const savedTab = localStorage.getItem('activeTab') || 'atlasforge';
    switchTab(savedTab);
}

// =====================================================================
// GLASSBOX TAB FUNCTIONS
// =====================================================================

let glassboxMissions = [];
let currentGlassboxMission = null;
let glassboxPagination = { page: 1, limit: 20, total: 0, pages: 1 };
let glassboxSearchTimeout = null;
let glassboxDateFilters = { from: null, to: null };

// Restore filter state from localStorage on init
function initGlassboxFilters() {
    try {
        const saved = localStorage.getItem('glassboxFilters');
        if (saved) {
            const filters = JSON.parse(saved);
            glassboxDateFilters = filters;
            const fromEl = document.getElementById('glassbox-date-from');
            const toEl = document.getElementById('glassbox-date-to');
            if (fromEl && filters.from) fromEl.value = filters.from;
            if (toEl && filters.to) toEl.value = filters.to;
        }
    } catch (e) {}
}

// Save filter state to localStorage
function saveGlassboxFilters() {
    try {
        localStorage.setItem('glassboxFilters', JSON.stringify(glassboxDateFilters));
    } catch (e) {}
}

// Date filter handlers
function glassboxDateFilter() {
    const fromEl = document.getElementById('glassbox-date-from');
    const toEl = document.getElementById('glassbox-date-to');
    glassboxDateFilters.from = fromEl.value || null;
    glassboxDateFilters.to = toEl.value || null;
    saveGlassboxFilters();
    loadGlassboxTabData(1);
}

function clearGlassboxDateFilter() {
    const fromEl = document.getElementById('glassbox-date-from');
    const toEl = document.getElementById('glassbox-date-to');
    if (fromEl) fromEl.value = '';
    if (toEl) toEl.value = '';
    glassboxDateFilters = { from: null, to: null };
    saveGlassboxFilters();
    loadGlassboxTabData(1);
}

async function loadGlassboxTabData(page = 1, search = null) {
    try {
        // Build API URL with pagination, search, and date filters
        let url = `/api/glassbox/missions?page=${page}&limit=20`;
        if (search) {
            url += `&search=${encodeURIComponent(search)}`;
        }
        // Add date filters
        if (glassboxDateFilters.from) {
            url += `&from=${encodeURIComponent(glassboxDateFilters.from)}`;
        }
        if (glassboxDateFilters.to) {
            url += `&to=${encodeURIComponent(glassboxDateFilters.to)}`;
        }

        // Load missions list from GlassBox API
        const response = await api(url);
        const missions = response.missions || [];
        glassboxMissions = missions;
        glassboxPagination = response.pagination || { page: 1, limit: 20, total: 0, pages: 1 };

        // Update dropdown
        const select = document.getElementById('glassbox-tab-mission-select');
        select.innerHTML = '<option value="">Select archived mission...</option>';
        missions.forEach(m => {
            const date = m.completed_at ? new Date(m.completed_at).toLocaleDateString() : 'Unknown';
            const tokens = m.total_tokens ? ` (${(m.total_tokens / 1000).toFixed(1)}K tokens)` : '';
            select.innerHTML += `<option value="${m.mission_id}">${m.mission_id}${tokens} - ${date}</option>`;
        });

        // Also update the sidebar dropdown
        const sidebarSelect = document.getElementById('glassbox-mission-select');
        sidebarSelect.innerHTML = '<option value="">Select archived mission...</option>';
        missions.forEach(m => {
            const date = m.completed_at ? new Date(m.completed_at).toLocaleDateString() : 'Unknown';
            sidebarSelect.innerHTML += `<option value="${m.mission_id}">${m.mission_id} - ${date}</option>`;
        });

        // Update mission count badge (show total, not just current page)
        document.getElementById('glassbox-mission-count').textContent = glassboxPagination.total;

        // Update pagination UI
        updateGlassboxPagination();

        // Load stats
        const stats = await api('/api/glassbox/stats');
        document.getElementById('glassbox-tab-stats').innerHTML = `
            <div class="glassbox-stat">
                <div class="glassbox-stat-value">${stats.total_missions || 0}</div>
                <div class="glassbox-stat-label">Missions</div>
            </div>
            <div class="glassbox-stat">
                <div class="glassbox-stat-value">${((stats.total_tokens || 0) / 1000000).toFixed(2)}M</div>
                <div class="glassbox-stat-label">Tokens</div>
            </div>
            <div class="glassbox-stat">
                <div class="glassbox-stat-value">${stats.total_transcripts || 0}</div>
                <div class="glassbox-stat-label">Transcripts</div>
            </div>
        `;
    } catch (e) {
        console.error('Error loading GlassBox data:', e);
    }
}

function updateGlassboxPagination() {
    const paginationEl = document.getElementById('glassbox-pagination');
    if (!paginationEl) return;

    const { page, pages, total, has_prev, has_next } = glassboxPagination;

    paginationEl.innerHTML = `
        <button onclick="glassboxPageNav(-1)" ${!has_prev ? 'disabled' : ''} style="padding: 4px 8px;">&laquo; Prev</button>
        <span style="margin: 0 10px; font-size: 0.85em;">Page ${page} of ${pages} (${total} total)</span>
        <button onclick="glassboxPageNav(1)" ${!has_next ? 'disabled' : ''} style="padding: 4px 8px;">Next &raquo;</button>
    `;
}

function glassboxPageNav(delta) {
    const newPage = glassboxPagination.page + delta;
    if (newPage >= 1 && newPage <= glassboxPagination.pages) {
        const searchInput = document.getElementById('glassbox-search-input');
        const search = searchInput ? searchInput.value.trim() : null;
        loadGlassboxTabData(newPage, search || null);
    }
}

function glassboxSearch(query) {
    clearTimeout(glassboxSearchTimeout);
    glassboxSearchTimeout = setTimeout(() => {
        loadGlassboxTabData(1, query.trim() || null);
    }, 300);
}

async function refreshGlassboxMissions() {
    await loadGlassboxTabData();
    showToast('GlassBox missions refreshed');
}

async function loadGlassboxTabMission() {
    const missionId = document.getElementById('glassbox-tab-mission-select').value;
    if (!missionId) {
        document.getElementById('glassbox-tab-stages').innerHTML = '<div style="color: var(--text-dim);">Select a mission to view</div>';
        document.getElementById('glassbox-tab-agents').innerHTML = '<div style="color: var(--text-dim);">Select a mission to view</div>';
        document.getElementById('glassbox-tab-log').innerHTML = '<div style="color: var(--text-dim);">Select a mission to view</div>';
        return;
    }

    currentGlassboxMission = missionId;

    // Show loading skeletons instead of spinners
    const stagesSkeleton = `
        <div class="skeleton skeleton-bar" style="width: 85%;"></div>
        <div class="skeleton skeleton-bar" style="width: 65%;"></div>
        <div class="skeleton skeleton-bar" style="width: 75%;"></div>
        <div class="skeleton skeleton-bar" style="width: 55%;"></div>
    `;
    const agentsSkeleton = `
        <div style="padding: 8px; border-bottom: 1px solid var(--border);">
            <div class="skeleton skeleton-text skeleton-text-medium"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
        <div style="padding: 8px; border-bottom: 1px solid var(--border);">
            <div class="skeleton skeleton-text skeleton-text-medium"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
        <div style="padding: 8px;">
            <div class="skeleton skeleton-text skeleton-text-medium"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
    `;
    const logSkeleton = `
        <div style="padding: 8px 0;">
            <div class="skeleton skeleton-text skeleton-text-long"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
        <div style="padding: 8px 0;">
            <div class="skeleton skeleton-text skeleton-text-long"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
        <div style="padding: 8px 0;">
            <div class="skeleton skeleton-text skeleton-text-medium"></div>
            <div class="skeleton skeleton-text skeleton-text-short"></div>
        </div>
    `;
    document.getElementById('glassbox-tab-stages').innerHTML = stagesSkeleton;
    document.getElementById('glassbox-tab-agents').innerHTML = agentsSkeleton;
    document.getElementById('glassbox-tab-log').innerHTML = logSkeleton;

    try {
        // Load all data in parallel for faster loading
        const [stages, agents, log] = await Promise.all([
            api(`/api/glassbox/missions/${missionId}/stages`),
            api(`/api/glassbox/missions/${missionId}/agents`),
            api(`/api/glassbox/missions/${missionId}/decision-log`)
        ]);

        renderGlassboxStages(stages);
        renderGlassboxAgents(agents);
        renderGlassboxLog(log);
    } catch (e) {
        console.error('Error loading mission:', e);
        showToast('Error loading mission: ' + e.message);
        document.getElementById('glassbox-tab-stages').innerHTML = '<div style="color: var(--red);">Error loading data</div>';
        document.getElementById('glassbox-tab-agents').innerHTML = '<div style="color: var(--red);">Error loading data</div>';
        document.getElementById('glassbox-tab-log').innerHTML = '<div style="color: var(--red);">Error loading data</div>';
    }
}

// For sidebar dropdown
async function loadGlassboxMission() {
    const missionId = document.getElementById('glassbox-mission-select').value;
    if (!missionId) {
        document.getElementById('glassbox-content').innerHTML = '<div style="color: var(--text-dim);">Select a mission to view introspection data</div>';
        return;
    }

    // Show loading state
    document.getElementById('glassbox-content').innerHTML = '<div style="color: var(--accent); font-size: 0.85em;"><span class="loading-spinner"></span> Loading mission data...</div>';

    try {
        const timeline = await api(`/api/glassbox/missions/${missionId}/timeline`);
        document.getElementById('glassbox-content').innerHTML = `
            <div style="font-size: 0.85em;">
                <div class="stat-row">
                    <span class="stat-label">Stages</span>
                    <span class="stat-value">${timeline.stages?.length || 0}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Agents</span>
                    <span class="stat-value">${timeline.all_agents?.length || 0}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Total Tokens</span>
                    <span class="stat-value">${timeline.total_tokens?.toLocaleString() || 0}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Duration</span>
                    <span class="stat-value">${formatDuration(timeline.total_duration_seconds)}</span>
                </div>
                <div style="margin-top: 10px;">
                    <button class="btn btn-primary" onclick="switchTab('glassbox'); document.getElementById('glassbox-tab-mission-select').value='${missionId}'; loadGlassboxTabMission();">
                        View Full Details →
                    </button>
                </div>
            </div>
        `;
    } catch (e) {
        document.getElementById('glassbox-content').innerHTML = '<div style="color: var(--red);">Error loading mission</div>';
    }
}

function formatDuration(seconds) {
    if (!seconds) return '0s';
    if (seconds < 60) return seconds.toFixed(0) + 's';
    if (seconds < 3600) return (seconds / 60).toFixed(1) + 'm';
    return (seconds / 3600).toFixed(1) + 'h';
}

function renderGlassboxStages(data) {
    const container = document.getElementById('glassbox-tab-stages');
    if (!data.stages || data.stages.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No stage data available</div>';
        return;
    }

    const totalDuration = data.total_duration_seconds || 1;
    container.innerHTML = data.stages.map(s => {
        const pct = ((s.duration_seconds || 0) / totalDuration * 100).toFixed(1);
        const stageColors = {
            'PLANNING': 'var(--blue)',
            'BUILDING': 'var(--green)',
            'TESTING': 'var(--yellow)',
            'ANALYZING': 'var(--purple)',
            'CYCLE_END': 'var(--accent)',
            'COMPLETE': 'var(--green)'
        };
        return `
            <div class="glassbox-stage-bar">
                <div class="glassbox-stage-label">${s.stage}</div>
                <div class="glassbox-stage-fill" style="width: ${pct}%; background: ${stageColors[s.stage] || 'var(--accent)'};"></div>
                <div style="font-size: 0.75em; color: var(--text-dim);">
                    ${formatDuration(s.duration_seconds)} | ${(s.tokens_used || 0).toLocaleString()} tokens
                </div>
            </div>
        `;
    }).join('');
}

function renderGlassboxAgents(data) {
    const container = document.getElementById('glassbox-tab-agents');
    if (!data.all_agents || data.all_agents.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No agent data available</div>';
        return;
    }

    container.innerHTML = `
        <div style="margin-bottom: 10px; font-size: 0.85em; color: var(--text-dim);">
            ${data.agent_count} agents total
        </div>
        ${data.all_agents.slice(0, 20).map(a => `
            <div class="glassbox-agent-item" onclick="showAgentTranscript('${data.mission_id}', '${a.agent_id}')">
                <div class="glassbox-agent-id">${a.agent_id.substring(0, 8)}...</div>
                <div class="glassbox-agent-meta">
                    ${a.model || 'unknown'} | ${(a.total_tokens || 0).toLocaleString()} tokens | ${formatDuration(a.duration_seconds)}
                </div>
            </div>
        `).join('')}
        ${data.all_agents.length > 20 ? `<div style="color: var(--text-dim); font-size: 0.85em;">... and ${data.all_agents.length - 20} more</div>` : ''}
    `;
}

function renderGlassboxLog(data) {
    const container = document.getElementById('glassbox-tab-log');
    if (!data.events || data.events.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No decision log available</div>';
        return;
    }

    const iconMap = {
        'stage_transition': '🔄',
        'file_write': '📝',
        'file_read': '📖',
        'error': '❌',
        'tool_call': '🔧',
        'agent_spawn': '🌱'
    };

    container.innerHTML = data.events.slice(0, 50).map(e => `
        <div class="glassbox-log-item">
            <span class="glassbox-log-icon">${iconMap[e.event_type] || '📌'}</span>
            <span class="glassbox-log-time">${new Date(e.timestamp).toLocaleTimeString()}</span>
            <span>${e.description?.substring(0, 60) || e.event_type}${e.description?.length > 60 ? '...' : ''}</span>
        </div>
    `).join('');
}

async function showAgentTranscript(missionId, agentId) {
    // Show modal immediately with loading state
    document.getElementById('glassbox-modal-title').textContent = 'Loading Transcript...';
    document.getElementById('glassbox-modal-content').innerHTML = '<div style="text-align: center; padding: 40px;"><span class="loading-spinner" style="width: 24px; height: 24px; border-width: 3px;"></span><div style="margin-top: 15px; color: var(--text-dim);">Loading transcript data...</div></div>';
    document.getElementById('glassbox-modal').classList.add('show');

    try {
        const data = await api(`/api/glassbox/missions/${missionId}/transcripts/${agentId}`);

        // Update modal with transcript
        document.getElementById('glassbox-modal-title').textContent = `Transcript: ${agentId.substring(0, 12)}...`;

        const content = data.messages?.map(m => {
            if (m.type === 'assistant') {
                const text = m.content?.map(c => {
                    if (c.type === 'text') return c.text;
                    if (c.type === 'tool_use') return `[Tool: ${c.name}]`;
                    return '';
                }).join('\\n') || '';
                return `<div style="background: var(--border); padding: 10px; border-radius: 6px; margin: 5px 0;">${text.substring(0, 500)}${text.length > 500 ? '...' : ''}</div>`;
            } else if (m.type === 'user') {
                const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content).substring(0, 200);
                return `<div style="background: var(--bg); padding: 10px; border-radius: 6px; margin: 5px 0; border-left: 3px solid var(--accent);">${content}${content.length >= 200 ? '...' : ''}</div>`;
            }
            return '';
        }).join('') || 'No messages';

        document.getElementById('glassbox-modal-content').innerHTML = `
            <div style="margin-bottom: 15px;">
                <strong>Session:</strong> ${data.session_id || 'N/A'}<br>
                <strong>Messages:</strong> ${data.message_count || 0}<br>
                <strong>Tokens:</strong> ${(data.total_tokens || 0).toLocaleString()}
            </div>
            <div style="max-height: 400px; overflow-y: auto;">
                ${content}
            </div>
        `;

        document.getElementById('glassbox-modal').classList.add('show');
    } catch (e) {
        console.error('Error loading transcript:', e);
        showToast('Error loading transcript');
    }
}

function closeGlassboxModal() {
    document.getElementById('glassbox-modal').classList.remove('show');
}

// =====================================================================
// MISSION LOGS TAB FUNCTIONS
// =====================================================================

let missionLogs = [];
let currentMissionLog = null;

async function loadMissionLogsTabData() {
    try {
        // Load mission logs list
        const logs = await api('/api/mission-logs');
        missionLogs = logs;

        // Update dropdown
        const select = document.getElementById('missionlogs-select');
        select.innerHTML = '<option value="">Select a mission log...</option>';
        logs.forEach(log => {
            const date = log.completed_at ? new Date(log.completed_at).toLocaleDateString() : 'Unknown';
            const cycles = log.total_cycles ? ` (${log.total_cycles} cycles)` : '';
            select.innerHTML += `<option value="${log.mission_id}">${log.mission_id}${cycles} - ${date}</option>`;
        });

        // Update stats
        const totalCycles = logs.reduce((sum, l) => sum + (l.total_cycles || 0), 0);
        document.getElementById('missionlogs-stats').innerHTML = `
            <div class="glassbox-stat">
                <div class="glassbox-stat-value">${logs.length}</div>
                <div class="glassbox-stat-label">Mission Logs</div>
            </div>
            <div class="glassbox-stat">
                <div class="glassbox-stat-value">${totalCycles}</div>
                <div class="glassbox-stat-label">Total Cycles</div>
            </div>
        `;

        // Update the mission list panel
        renderMissionLogsList(logs);

    } catch (e) {
        console.error('Failed to load mission logs:', e);
        document.getElementById('missionlogs-list').innerHTML = '<div style="color: var(--red);">Error loading logs</div>';
    }
}

function renderMissionLogsList(logs) {
    const container = document.getElementById('missionlogs-list');

    if (!logs || logs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No mission logs found</div>';
        return;
    }

    let html = '';
    logs.forEach(log => {
        const date = log.completed_at ? new Date(log.completed_at).toLocaleDateString() : 'Unknown';
        const missionPreview = (log.original_mission || 'Unknown mission').substring(0, 50);
        const cycles = log.total_cycles || 0;

        html += `
            <div class="glassbox-agent-item" onclick="selectMissionLog('${log.mission_id}')">
                <div class="glassbox-agent-id">${log.mission_id}</div>
                <div class="glassbox-agent-meta">${missionPreview}${missionPreview.length >= 50 ? '...' : ''}</div>
                <div class="glassbox-agent-meta">${cycles} cycle(s) | ${date}</div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function selectMissionLog(missionId) {
    // Update dropdown
    document.getElementById('missionlogs-select').value = missionId;
    loadMissionLog();
}

async function loadMissionLog() {
    const select = document.getElementById('missionlogs-select');
    const missionId = select.value;

    if (!missionId) {
        document.getElementById('missionlogs-details').innerHTML =
            '<div style="color: var(--text-dim);">Select a mission log to view details</div>';
        return;
    }

    try {
        const log = await api(`/api/mission-logs/${missionId}`);
        currentMissionLog = log;
        renderMissionLogDetails(log);
    } catch (e) {
        document.getElementById('missionlogs-details').innerHTML =
            `<div style="color: var(--red);">Error loading mission log: ${e.message}</div>`;
    }
}

function renderMissionLogDetails(log) {
    const container = document.getElementById('missionlogs-details');

    // Header info
    let html = `
        <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
            <h4 style="color: var(--accent); margin-bottom: 10px;">${log.mission_id}</h4>
            <div style="font-size: 0.9em; margin-bottom: 10px;">
                <strong>Original Mission:</strong><br>
                <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px;">
                    ${escapeHtml(log.original_mission || 'Unknown')}
                </div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 0.85em;">
                <div><span style="color: var(--text-dim);">Started:</span> ${log.started_at ? new Date(log.started_at).toLocaleString() : 'Unknown'}</div>
                <div><span style="color: var(--text-dim);">Completed:</span> ${log.completed_at ? new Date(log.completed_at).toLocaleString() : 'Unknown'}</div>
                <div><span style="color: var(--text-dim);">Total Cycles:</span> ${log.total_cycles || 0}</div>
                <div><span style="color: var(--text-dim);">Total Iterations:</span> ${log.total_iterations || 0}</div>
            </div>
        </div>
    `;

    // Final summary
    if (log.final_summary) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Final Summary</h4>
                <div style="font-size: 0.9em;">${escapeHtml(log.final_summary)}</div>
            </div>
        `;
    }

    // Cycles
    if (log.cycles && log.cycles.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Cycle History</h4>
        `;
        log.cycles.forEach((cycle, idx) => {
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
    if (log.deliverables && log.deliverables.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">Deliverables</h4>
                <div style="font-size: 0.85em;">
                    ${log.deliverables.map(f => `<div style="padding: 3px 0;">• ${escapeHtml(f)}</div>`).join('')}
                </div>
            </div>
        `;
    }

    if (log.all_files && log.all_files.length > 0) {
        html += `
            <div style="margin-bottom: 20px; padding: 15px; background: var(--bg); border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 10px;">All Files Created</h4>
                <div style="font-size: 0.85em; max-height: 200px; overflow-y: auto;">
                    ${log.all_files.map(f => `<div style="padding: 3px 0;">• ${escapeHtml(f)}</div>`).join('')}
                </div>
            </div>
        `;
    }

    container.innerHTML = html;
}

function refreshMissionLogs() {
    loadMissionLogsTabData();
    showToast('Mission logs refreshed');
}

// Toast
function showToast(msg, typeOrDuration = 3000, duration = 3000) {
    const t = document.getElementById('toast');
    if (!t) return;

    // Handle backwards compatibility: if second arg is number, treat as duration
    let toastType = 'info';
    let toastDuration = 3000;

    if (typeof typeOrDuration === 'number') {
        toastDuration = typeOrDuration;
    } else if (typeof typeOrDuration === 'string') {
        toastType = typeOrDuration;
        toastDuration = duration;
    }

    // Remove any previous type classes
    t.classList.remove('toast-success', 'toast-error', 'toast-info', 'toast-warning');

    // Add type class if not default info
    if (toastType !== 'info') {
        t.classList.add(`toast-${toastType}`);
    }

    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => {
        t.classList.remove('show');
        t.classList.remove('toast-success', 'toast-error', 'toast-info', 'toast-warning');
    }, toastDuration);
}

// Recommendations Management Functions
async function loadRecommendations() {
    const data = await api('/api/recommendations');
    recommendations = data.items || [];
    renderRecommendations();
    updateRecCount();
}

function renderRecommendations() {
    const container = document.getElementById('recommendations-list');
    if (recommendations.length === 0) {
        container.innerHTML = '<div class="rec-placeholder">No recommendations yet. Complete a mission to get suggestions.</div>';
        return;
    }

    container.innerHTML = recommendations.map(rec => `
        <div class="rec-item" onclick="openRecModal('${rec.id}')">
            <div class="rec-item-content">
                <div class="rec-item-title">${escapeHtml(rec.mission_title)}</div>
                <div class="rec-item-preview">${escapeHtml((rec.mission_description || '').substring(0, 100))}${(rec.mission_description || '').length > 100 ? '...' : ''}</div>
            </div>
            <div class="rec-item-meta">
                <span class="rec-cycles-badge">${rec.suggested_cycles || 3} cycles</span>
                <span>${formatDate(rec.created_at)}</span>
            </div>
        </div>
    `).join('');
}

function formatDate(isoDate) {
    if (!isoDate) return '';
    const date = new Date(isoDate);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function openRecModal(recId) {
    selectedRecId = recId;
    const rec = recommendations.find(r => r.id === recId);
    if (!rec) return;

    document.getElementById('rec-modal-title').textContent = 'Mission Recommendation';
    document.getElementById('rec-modal-mission-title').textContent = rec.mission_title || 'Untitled';
    document.getElementById('rec-modal-description').textContent = rec.mission_description || 'No description';
    document.getElementById('rec-modal-rationale').textContent = rec.rationale || 'No rationale provided';
    document.getElementById('rec-modal-source').textContent = rec.source_mission_id
        ? `From: ${rec.source_mission_id}${rec.source_mission_summary ? ' - ' + rec.source_mission_summary.substring(0, 100) : ''}`
        : 'Manual recommendation';

    // Set suggested cycles
    const cyclesSelect = document.getElementById('rec-modal-cycles');
    const suggestedCycles = rec.suggested_cycles || 3;
    cyclesSelect.value = suggestedCycles;

    document.getElementById('rec-modal').style.display = 'flex';
}

function closeRecModal() {
    document.getElementById('rec-modal').style.display = 'none';
    selectedRecId = null;
}

async function deleteRecommendation() {
    if (!selectedRecId) return;

    if (!confirm('Delete this recommendation?')) return;

    await api('/api/recommendations/' + selectedRecId, 'DELETE');
    showToast('Recommendation deleted');
    closeRecModal();
    await loadRecommendations();
}

async function setMissionFromRec() {
    if (!selectedRecId) return;

    const cycleBudget = parseInt(document.getElementById('rec-modal-cycles').value) || 3;

    const data = await api('/api/recommendations/' + selectedRecId + '/set-mission', 'POST', {
        cycle_budget: cycleBudget
    });

    if (data.success) {
        showToast(data.message);
        closeRecModal();
        await loadRecommendations();
        refresh();
    } else {
        showToast('Error: ' + (data.error || 'Failed to set mission'));
    }
}

function updateRecCount() {
    document.getElementById('rec-count').textContent = recommendations.length;
}

// Close modal on click outside
document.addEventListener('click', function(e) {
    const modal = document.getElementById('rec-modal');
    if (e.target === modal) {
        closeRecModal();
    }
});

// API calls
async function api(endpoint, method = 'GET', body = null) {
    const opts = { method };
    if (body) {
        opts.headers = {'Content-Type': 'application/json'};
        opts.body = JSON.stringify(body);
    }
    const resp = await fetch(endpoint, opts);
    return resp.json();
}

// Controls
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
                `Current mission is in stage: ${currentStage}\n\n` +
                `This will OVERWRITE the current mission and start the new one!\n\n` +
                `Are you sure?`
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

// File Downloads
function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatTimeAgo(timestamp) {
    const seconds = Math.floor((Date.now() / 1000) - timestamp);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
}

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

// File path detection - matches workspace file paths in messages
// Supports both absolute paths (with dynamic base) and relative workspace paths
const filePathRegex = /(?:\/[^\s]+\/workspace\/|workspace\/|artifacts\/|research\/|tests\/)([\w\-\/.]+\.\w+)/g;

function processMessageForDownloads(content) {
    // Replace workspace file paths with download links
    return content.replace(filePathRegex, (match, pathPart) => {
        // Determine the relative path for the download URL
        let relativePath = pathPart;

        // Handle full paths
        if (match.includes('/workspace/')) {
            relativePath = pathPart;
        } else if (match.startsWith('workspace/')) {
            relativePath = pathPart;
        } else if (match.startsWith('artifacts/')) {
            relativePath = 'artifacts/' + pathPart;
        } else if (match.startsWith('research/')) {
            relativePath = 'research/' + pathPart;
        } else if (match.startsWith('tests/')) {
            relativePath = 'tests/' + pathPart;
        }

        const filename = relativePath.split('/').pop();
        return `<a href="/api/download/${relativePath}" class="download-link" download title="${relativePath}">${filename}</a>`;
    });
}

// Chat
function addMessage(role, content, timestamp = null) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `message ${role}`;

    // Use provided timestamp or fall back to current time
    const time = timestamp
        ? new Date(timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
        : new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});

    // Process content for download links (only for Claude messages)
    let processedContent = content;
    if (role === 'claude') {
        processedContent = processMessageForDownloads(content);
    }

    // Store raw content for copy functionality
    div.dataset.rawContent = content;

    div.innerHTML = `<button class="message-copy-btn" onclick="copyMessageText(this)">Copy</button><div class="message-meta">${role} - ${time}</div>${processedContent}`;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function copyMessageText(btn) {
    const message = btn.parentElement;
    const text = message.dataset.rawContent;

    // Try modern clipboard API first, fallback to execCommand
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => {
            showCopySuccess(btn);
        }).catch(() => {
            fallbackCopy(text, btn);
        });
    } else {
        fallbackCopy(text, btn);
    }
}

function fallbackCopy(text, btn) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showCopySuccess(btn);
    } catch (e) {
        console.error('Copy failed:', e);
        btn.textContent = 'Failed';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    }
    document.body.removeChild(textarea);
}

function showCopySuccess(btn) {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
        btn.textContent = 'Copy';
        btn.classList.remove('copied');
    }, 1500);
}

// Update stage indicator
function updateStageIndicator(currentStage) {
    const stageEls = document.querySelectorAll('.stage');
    const currentIdx = stages.indexOf(currentStage);

    stageEls.forEach((el, idx) => {
        el.classList.remove('active', 'complete');
        if (idx < currentIdx) el.classList.add('complete');
        else if (idx === currentIdx) el.classList.add('active');
    });
}

// Refresh status
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
    window.fullMissionText = fullMissionText;  // Expose for onclick handlers
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
    await refreshAFWidgets();

    // Load KB Analytics widget (less frequently - every 3rd refresh)
    if (!window.lastKBRefresh || Date.now() - window.lastKBRefresh > 15000) {
        await refreshKBAnalyticsWidget();
        window.lastKBRefresh = Date.now();
    }
}

// AtlasForge Enhancement Widget Functions
async function refreshAFWidgets() {
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
            refreshGraphVisualization();
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

// =================================================================
// GRAPH VISUALIZATION - Improved Layout
// =================================================================

class GraphRenderer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.nodes = [];
        this.edges = [];
        this.selectedNode = null;
        this.hoveredNode = null;
        this.scale = 1.0;
        this.offsetX = 0;
        this.offsetY = 0;
        this.tooltip = document.getElementById('graph-tooltip');

        // Node colors by type
        this.colors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };

        // Event handlers
        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleHover(e));
        this.canvas.addEventListener('mouseleave', () => this.hideTooltip());
    }

    // Improved force-directed layout with better node spacing
    applyForceLayout(iterations = 50) {
        if (this.nodes.length === 0) return;

        const width = this.canvas.width;
        const height = this.canvas.height;
        const padding = 40;
        const minNodeDistance = 60; // Minimum distance between nodes

        // Initialize positions if not set (use circular layout as starting point)
        this.nodes.forEach((node, i) => {
            if (node.x === undefined || node.y === undefined) {
                const angle = (2 * Math.PI * i) / this.nodes.length;
                const radius = Math.min(width, height) * 0.35;
                node.x = width / 2 + radius * Math.cos(angle);
                node.y = height / 2 + radius * Math.sin(angle);
            }
        });

        // Build adjacency for connected nodes
        const adjacency = new Map();
        this.edges.forEach(e => {
            if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
            if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
            adjacency.get(e.source).add(e.target);
            adjacency.get(e.target).add(e.source);
        });

        // Force simulation
        for (let iter = 0; iter < iterations; iter++) {
            const forces = new Map();
            this.nodes.forEach(n => forces.set(n.id, { fx: 0, fy: 0 }));

            // Repulsion between all nodes (prevents overlap)
            for (let i = 0; i < this.nodes.length; i++) {
                for (let j = i + 1; j < this.nodes.length; j++) {
                    const n1 = this.nodes[i];
                    const n2 = this.nodes[j];
                    const dx = n2.x - n1.x;
                    const dy = n2.y - n1.y;
                    const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                    const repulsion = (minNodeDistance * minNodeDistance) / dist;
                    const fx = (dx / dist) * repulsion;
                    const fy = (dy / dist) * repulsion;
                    forces.get(n1.id).fx -= fx;
                    forces.get(n1.id).fy -= fy;
                    forces.get(n2.id).fx += fx;
                    forces.get(n2.id).fy += fy;
                }
            }

            // Attraction along edges
            this.edges.forEach(e => {
                const source = this.nodes.find(n => n.id === e.source);
                const target = this.nodes.find(n => n.id === e.target);
                if (!source || !target) return;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                const attraction = dist * 0.01;
                const fx = (dx / dist) * attraction;
                const fy = (dy / dist) * attraction;
                forces.get(source.id).fx += fx;
                forces.get(source.id).fy += fy;
                forces.get(target.id).fx -= fx;
                forces.get(target.id).fy -= fy;
            });

            // Center gravity (keeps nodes from flying away)
            this.nodes.forEach(n => {
                const dx = width / 2 - n.x;
                const dy = height / 2 - n.y;
                forces.get(n.id).fx += dx * 0.002;
                forces.get(n.id).fy += dy * 0.002;
            });

            // Apply forces with damping
            const damping = 0.8 - (iter / iterations) * 0.3;
            this.nodes.forEach(n => {
                const f = forces.get(n.id);
                n.x += f.fx * damping;
                n.y += f.fy * damping;
                // Keep within bounds
                n.x = Math.max(padding, Math.min(width - padding, n.x));
                n.y = Math.max(padding, Math.min(height - padding, n.y));
            });
        }
    }

    loadData(graphData) {
        if (!graphData) return;
        this.nodes = graphData.nodes || [];
        this.edges = graphData.edges || [];

        // Apply improved layout algorithm
        if (this.nodes.length > 0) {
            this.applyForceLayout(80);
            this.scale = 1.0;
            this.offsetX = 0;
            this.offsetY = 0;
        }

        this.render();

        // Update stats
        const nodeCount = document.getElementById('graph-node-count');
        const edgeCount = document.getElementById('graph-edge-count');
        if (nodeCount) nodeCount.textContent = this.nodes.length;
        if (edgeCount) edgeCount.textContent = this.edges.length;
    }

    transformX(x) {
        return x * this.scale + this.offsetX;
    }

    transformY(y) {
        return y * this.scale + this.offsetY;
    }

    render() {
        if (!this.ctx) return;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        // Draw edges first (behind nodes)
        this.edges.forEach(edge => this.drawEdge(edge));

        // Draw nodes
        this.nodes.forEach(node => this.drawNode(node));
    }

    drawNode(node) {
        const x = this.transformX(node.x);
        const y = this.transformY(node.y);
        const size = Math.max(6, (node.size || 15) * this.scale * 0.6);

        // Highlight selected or hovered node
        const isSelected = this.selectedNode && this.selectedNode.id === node.id;
        const isHovered = this.hoveredNode && this.hoveredNode.id === node.id;

        this.ctx.beginPath();
        this.ctx.arc(x, y, size, 0, Math.PI * 2);
        this.ctx.fillStyle = this.colors[node.type] || '#8b949e';
        this.ctx.fill();

        if (isSelected || isHovered) {
            this.ctx.strokeStyle = '#fff';
            this.ctx.lineWidth = 2;
            this.ctx.stroke();
        }

        // Label (only for larger canvases or selected nodes)
        if (this.canvas.width > 300 || isSelected) {
            this.ctx.fillStyle = '#c9d1d9';
            this.ctx.font = Math.max(8, 10 * this.scale) + 'px sans-serif';
            this.ctx.textAlign = 'center';
            const label = node.name.substring(0, 12);
            this.ctx.fillText(label, x, y + size + 10);
        }
    }

    drawEdge(edge) {
        const source = this.nodes.find(n => n.id === edge.source);
        const target = this.nodes.find(n => n.id === edge.target);
        if (!source || !target) return;

        const x1 = this.transformX(source.x);
        const y1 = this.transformY(source.y);
        const x2 = this.transformX(target.x);
        const y2 = this.transformY(target.y);

        this.ctx.beginPath();
        this.ctx.moveTo(x1, y1);
        this.ctx.lineTo(x2, y2);
        this.ctx.strokeStyle = 'rgba(139, 148, 158, 0.4)';
        this.ctx.lineWidth = Math.max(1, (edge.strength || 1) * 2 * this.scale);
        this.ctx.stroke();
    }

    handleClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const clickedNode = this.findNodeAt(x, y);
        if (clickedNode) {
            this.selectedNode = clickedNode;
            this.showNodeDetails(clickedNode);
            this.render();
        }
    }

    handleHover(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const node = this.findNodeAt(x, y);
        if (node !== this.hoveredNode) {
            this.hoveredNode = node;
            this.canvas.style.cursor = node ? 'pointer' : 'default';
            this.render();

            // Update tooltip
            if (node) {
                this.showTooltip(node, e.clientX, e.clientY);
            } else {
                this.hideTooltip();
            }
        }
    }

    showTooltip(node, mouseX, mouseY) {
        if (!this.tooltip) return;

        const typeColors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };

        this.tooltip.querySelector('.tt-name').textContent = node.name;
        this.tooltip.querySelector('.tt-name').style.color = typeColors[node.type] || '#8b949e';
        this.tooltip.querySelector('.tt-type').textContent = `Type: ${node.type} | Explored: ${node.exploration_count || 0}x`;
        this.tooltip.querySelector('.tt-path').textContent = node.path ? `Path: ${node.path}` : '';
        this.tooltip.querySelector('.tt-summary').textContent = node.summary || '';

        // Position tooltip near mouse
        this.tooltip.style.left = (mouseX + 15) + 'px';
        this.tooltip.style.top = (mouseY + 15) + 'px';
        this.tooltip.classList.add('visible');
    }

    hideTooltip() {
        if (this.tooltip) {
            this.tooltip.classList.remove('visible');
        }
    }

    findNodeAt(x, y) {
        for (const node of this.nodes) {
            const nx = this.transformX(node.x);
            const ny = this.transformY(node.y);
            const size = Math.max(6, (node.size || 15) * this.scale * 0.6);

            const dx = x - nx;
            const dy = y - ny;
            if (Math.sqrt(dx*dx + dy*dy) < size + 5) {
                return node;
            }
        }
        return null;
    }

    showNodeDetails(node) {
        const details = document.getElementById('graph-node-details');
        if (!details) return;

        const typeColors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };
        const color = typeColors[node.type] || '#8b949e';

        details.innerHTML = `
            <div style="font-weight: 500; color: ${color}; margin-bottom: 5px;">${node.name}</div>
            <div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Type:</span>
                <span class="stat-value">${node.type}</span>
            </div>
            ${node.path ? `<div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Path:</span>
                <span class="stat-value" style="word-break: break-all;">${node.path}</span>
            </div>` : ''}
            <div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Explored:</span>
                <span class="stat-value">${node.exploration_count}x</span>
            </div>
            <div style="font-size: 0.75em; color: var(--text-dim); margin-top: 5px;">${node.summary || ''}</div>
        `;
    }
}

// Global graph renderer instance
let graphRenderer = null;

async function refreshGraphVisualization() {
    try {
        const data = await api('/api/atlasforge/exploration-graph?width=800&height=600');
        if (data.error && data.nodes && data.nodes.length === 0) {
            console.log('No graph data:', data.error);
            return;
        }

        if (!graphRenderer) {
            graphRenderer = new GraphRenderer('exploration-graph-canvas');
        }

        if (graphRenderer) {
            graphRenderer.loadData(data);
        }
    } catch (e) {
        console.log('Error loading graph:', e);
    }
}

// =================================================================
// INSIGHT SEARCH
// =================================================================

async function searchInsights() {
    const input = document.getElementById('insight-search-input');
    const results = document.getElementById('insight-search-results');
    const query = input.value.trim();

    if (!query) {
        results.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">Enter a query to search insights</div>';
        return;
    }

    try {
        const data = await api('/api/atlasforge/search-insights?q=' + encodeURIComponent(query));
        if (data.error) {
            results.innerHTML = `<div style="color: var(--red); font-size: 0.85em;">${data.error}</div>`;
            return;
        }

        const insights = data.insights || [];
        if (insights.length === 0) {
            results.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No insights found</div>';
            return;
        }

        const html = insights.map(i => `
            <div class="af-exploration-item" title="${i.description || ''}">
                <div>
                    <span style="font-weight: 500;">${i.title}</span>
                    <div style="font-size: 0.75em; color: var(--text-dim);">
                        ${i.type} | ${(i.similarity * 100).toFixed(0)}% match
                    </div>
                </div>
                <span class="af-exploration-type">${(i.confidence * 100).toFixed(0)}%</span>
            </div>
        `).join('');

        results.innerHTML = html;
    } catch (e) {
        results.innerHTML = `<div style="color: var(--red); font-size: 0.85em;">Error: ${e.message}</div>`;
    }
}

// Allow Enter key to search
document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('insight-search-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') searchInsights();
        });
    }
});

// Keyboard shortcuts - extended
document.addEventListener('keydown', (e) => {
    // Skip when in input fields
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    // Escape key to close modals
    if (e.key === 'Escape') {
        const modals = document.querySelectorAll('.modal.visible, [id$="-modal"].visible');
        modals.forEach(m => m.classList.remove('visible'));
        // Clear graph tooltip
        const tooltip = document.getElementById('graph-tooltip');
        if (tooltip) tooltip.classList.remove('visible');
        return;
    }

    // Tab shortcuts: 1-7 for tab switching
    if (e.key >= '1' && e.key <= '7' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        const tabs = ['atlasforge', 'analytics', 'lessons', 'glassbox', 'missionlogs', 'bugbounty', 'narrative'];
        const idx = parseInt(e.key) - 1;
        if (tabs[idx]) {
            switchTab(tabs[idx]);
            showToast(`Switched to ${tabs[idx]} tab`);
        }
        return;
    }

    // Other shortcuts
    if (e.key === 'e' || e.key === 'E') {
        toggleCard('af-exploration');
    } else if (e.key === 'd' || e.key === 'D') {
        toggleCard('af-drift');
    } else if (e.key === 'r' || e.key === 'R') {
        refreshAFWidgets();
        showToast('AtlasForge widgets refreshed');
    } else if (e.key === 'g' || e.key === 'G') {
        switchTab('glassbox');
        showToast('Switched to GlassBox');
    } else if (e.key === '?' && e.shiftKey) {
        showKeyboardShortcuts();
    }
});

// Show keyboard shortcuts help
function showKeyboardShortcuts() {
    const shortcuts = `
        <div style="text-align: left; font-size: 0.9em;">
            <p><span class="kbd">1-7</span> Switch tabs</p>
            <p><span class="kbd">E</span> Toggle exploration card</p>
            <p><span class="kbd">D</span> Toggle drift card</p>
            <p><span class="kbd">R</span> Refresh AtlasForge widgets</p>
            <p><span class="kbd">G</span> Go to GlassBox tab</p>
            <p><span class="kbd">Esc</span> Close modals</p>
            <p><span class="kbd">?</span> Show this help</p>
        </div>
    `;
    showToast(shortcuts, 5000);
}

// =================================================================
// OFFLINE DETECTION & RECONNECTION
// =================================================================

let isOffline = false;
let offlineCheckInterval = null;

function updateOfflineIndicator(offline) {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator) return;

    if (offline && !isOffline) {
        indicator.classList.add('visible');
        isOffline = true;
        // Start reconnection attempts
        if (!offlineCheckInterval) {
            offlineCheckInterval = setInterval(checkOnlineStatus, 3000);
        }
    } else if (!offline && isOffline) {
        indicator.classList.remove('visible');
        isOffline = false;
        showToast('Connection restored!');
        // Stop reconnection attempts
        if (offlineCheckInterval) {
            clearInterval(offlineCheckInterval);
            offlineCheckInterval = null;
        }
        // Refresh data
        refresh();
    }
}

async function checkOnlineStatus() {
    try {
        const response = await fetch('/api/status', { timeout: 5000 });
        if (response.ok) {
            updateOfflineIndicator(false);
        }
    } catch (e) {
        // Still offline
    }
}

// Monitor API errors for offline detection
const originalFetch = window.fetch;
window.fetch = async function(...args) {
    try {
        const response = await originalFetch.apply(this, args);
        updateOfflineIndicator(false);
        return response;
    } catch (e) {
        if (e.name === 'TypeError' && e.message.includes('Failed to fetch')) {
            updateOfflineIndicator(true);
        }
        throw e;
    }
};

// Browser online/offline events
window.addEventListener('online', () => updateOfflineIndicator(false));
window.addEventListener('offline', () => updateOfflineIndicator(true));

// Init everything on page load
document.addEventListener('DOMContentLoaded', () => {
    initGlassboxFilters();

    // Init
    refresh();
    setInterval(refresh, 5000);

    // Initialize tabs and load GlassBox data for sidebar
    initTabs();
    loadGlassboxTabData();  // Load GlassBox missions for sidebar dropdown

    // =====================================================================
    // WIDGET INITIALIZATION - All widgets must init AFTER DOM is ready
    // =====================================================================

    // Check for crash recovery on load
    checkForRecovery();

    // Refresh analytics widget periodically
    refreshAnalyticsWidget();
    setInterval(refreshAnalyticsWidget, 30000);

    // Refresh git status widget periodically
    refreshGitStatusWidget();
    setInterval(refreshGitStatusWidget, 15000);

    // Refresh multi-repo status widget periodically
    refreshRepoStatusWidget();
    setInterval(refreshRepoStatusWidget, 30000);  // Every 30s

    // Refresh git analytics widget periodically
    refreshGitAnalyticsWidget();
    setInterval(refreshGitAnalyticsWidget, 60000);  // Every 60s (less frequent)

    // Refresh decision graph periodically
    refreshDecisionGraph();
    setInterval(refreshDecisionGraph, 10000);

    // Refresh KB analytics widget
    refreshKBAnalyticsWidget();
    setInterval(refreshKBAnalyticsWidget, 60000);  // Every 60s

    console.log('[Dashboard] All widgets initialized');
});

// =====================================================================
// ANALYTICS FUNCTIONS
// =====================================================================

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

function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
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

// =====================================================================
// GIT STATUS WIDGET FUNCTIONS
// =====================================================================

const gitHealthIcons = {
    'healthy': '&#10003;',
    'ahead': '&#9650;',
    'behind': '&#9660;',
    'diverged': '&#8646;',
    'conflict': '&#9888;',
    'no_remote': '&#9634;',
    'error': '&#10006;'
};

const gitHealthLabels = {
    'healthy': 'Synced',
    'ahead': 'Ahead of Remote',
    'behind': 'Behind Remote',
    'diverged': 'Diverged',
    'conflict': 'Conflicts',
    'no_remote': 'No Remote',
    'error': 'Error'
};

async function refreshGitStatusWidget() {
    try {
        const data = await api('/api/git/status');

        if (data.error) {
            showGitError(data.error);
            return;
        }

        const status = data.status;
        if (!status) {
            showGitError('No status data');
            return;
        }

        // Update health badge
        const healthBadge = document.getElementById('git-health-badge');
        const healthText = document.getElementById('git-health-text');
        const healthIcon = healthBadge.querySelector('.git-health-icon');

        // Remove all health classes and add current
        healthBadge.className = 'git-health-badge ' + status.sync_health;
        healthIcon.innerHTML = gitHealthIcons[status.sync_health] || '&#63;';
        healthText.textContent = gitHealthLabels[status.sync_health] || status.sync_health;

        // Update branch info
        document.getElementById('git-branch-name').textContent = status.current_branch || 'unknown';
        const remoteEl = document.getElementById('git-branch-remote');
        if (status.remote_branch) {
            remoteEl.textContent = '-> ' + status.remote_branch;
            remoteEl.style.display = 'inline';
        } else {
            remoteEl.textContent = '(no upstream)';
            remoteEl.style.display = 'inline';
        }

        // Update stats
        document.getElementById('git-uncommitted').textContent = status.uncommitted_changes || 0;
        document.getElementById('git-untracked').textContent = status.untracked_files || 0;
        document.getElementById('git-ahead').textContent = status.commits_ahead || 0;
        document.getElementById('git-behind').textContent = status.commits_behind || 0;

        // Color the ahead/behind values based on value
        const aheadEl = document.getElementById('git-ahead');
        const behindEl = document.getElementById('git-behind');
        aheadEl.className = 'git-stat-value' + (status.commits_ahead > 0 ? ' ahead' : '');
        behindEl.className = 'git-stat-value' + (status.commits_behind > 0 ? ' behind' : '');

        // Update last commit
        const lastCommit = document.getElementById('git-last-commit');
        if (status.last_commit_hash) {
            lastCommit.innerHTML = 'Last: <code>' + escapeHtml(status.last_commit_hash) + '</code> ' + escapeHtml(status.last_commit_message || '');
        } else {
            lastCommit.innerHTML = 'Last: <code>-</code> No commits';
        }

        // Update changed files count
        document.getElementById('git-changed-count').textContent = data.changed_files_count || 0;

        // Update changed files list
        const filesContainer = document.getElementById('git-changed-files');
        if (data.changed_files && data.changed_files.length > 0) {
            filesContainer.innerHTML = data.changed_files.map(f => {
                const statusClass = f.status + (f.staged ? ' staged' : '');
                return '<div class="git-file-item">' +
                    '<span class="git-file-status ' + statusClass + '">' + f.status + '</span>' +
                    '<span class="git-file-path">' + escapeHtml(f.path) + '</span>' +
                    '</div>';
            }).join('');
        } else {
            filesContainer.innerHTML = '<div class="git-file-item" style="color: var(--text-dim);">No changes</div>';
        }

        // Update auth status
        const authStatus = document.getElementById('git-auth-status');
        if (data.is_authenticated) {
            authStatus.className = 'git-auth-status authenticated';
            authStatus.innerHTML = '<span>&#10003;</span> ' + escapeHtml(data.auth_status);
        } else {
            authStatus.className = 'git-auth-status not-authenticated';
            authStatus.innerHTML = '<span>&#10006;</span> ' + escapeHtml(data.auth_status || 'Not authenticated');
        }

        // Update push status
        const pushStatus = document.getElementById('git-push-status');
        if (data.can_push) {
            pushStatus.innerHTML = '<span style="color: var(--green);">&#10003;</span> ' + escapeHtml(data.push_reason);
        } else if (data.push_reason) {
            pushStatus.innerHTML = '<span style="color: var(--yellow);">&#9888;</span> ' + escapeHtml(data.push_reason);
        } else {
            pushStatus.textContent = '';
        }

        // Update checkpoint section if data is available
        if (data.checkpoint) {
            updateCheckpointSection(data.checkpoint);
        } else {
            // Try to load checkpoint data separately
            loadCheckpointData();
        }

    } catch (e) {
        console.error('Git status widget error:', e);
        showGitError('Failed to fetch status');
    }
}

async function loadCheckpointData() {
    try {
        const pushData = await api('/api/git/push-status');
        if (!pushData.error) {
            updateCheckpointSection({
                pending_checkpoints: pushData.checkpoint_count || 0,
                should_push: pushData.should_push,
                push_trigger: pushData.trigger,
                push_trigger_reason: pushData.trigger_reason,
                hours_since_push: pushData.hours_since_push,
                checkpoint_threshold: pushData.config?.checkpoint_threshold || 10,
                time_threshold_hours: pushData.config?.time_threshold_hours || 4.0
            });
        }
    } catch (e) {
        console.error('Failed to load checkpoint data:', e);
    }
}

function updateCheckpointSection(checkpoint) {
    const section = document.getElementById('git-checkpoint-section');
    if (!section) return;

    // Show the section
    section.style.display = 'block';

    // Update checkpoint count
    const pendingEl = document.getElementById('git-checkpoints-pending');
    pendingEl.textContent = checkpoint.pending_checkpoints || 0;

    // Update hours since push
    const hoursEl = document.getElementById('git-hours-since-push');
    if (checkpoint.hours_since_push !== null && checkpoint.hours_since_push !== undefined) {
        hoursEl.textContent = checkpoint.hours_since_push.toFixed(1);
    } else {
        hoursEl.textContent = '-';
    }

    // Update push recommendation
    const recommendEl = document.getElementById('git-push-recommendation');
    const pushBtn = document.getElementById('git-push-btn');

    // Update push badge in header
    const pushBadge = document.getElementById('git-push-badge');
    const pushBadgeCount = document.getElementById('git-push-badge-count');

    if (checkpoint.should_push) {
        recommendEl.innerHTML = '<span style="color: var(--yellow);">&#9888;</span> ' +
            escapeHtml(checkpoint.push_trigger_reason || 'Push recommended');
        recommendEl.style.color = 'var(--yellow)';
        pushBtn.style.display = 'block';

        // Show push badge with checkpoint count
        if (pushBadge) {
            pushBadge.style.display = 'inline-flex';
            pushBadgeCount.textContent = checkpoint.pending_checkpoints || '!';
            // Add 'critical' class if trigger is critical_files
            if (checkpoint.push_trigger === 'critical_files') {
                pushBadge.classList.add('critical');
            } else {
                pushBadge.classList.remove('critical');
            }
        }
    } else if (checkpoint.pending_checkpoints > 0) {
        const threshold = checkpoint.checkpoint_threshold || 5;
        recommendEl.innerHTML = '<span style="color: var(--text-dim);">&#9679;</span> ' +
            checkpoint.pending_checkpoints + '/' + threshold + ' checkpoints (push at threshold)';
        recommendEl.style.color = 'var(--text-dim)';
        pushBtn.style.display = 'none';

        // Hide push badge
        if (pushBadge) {
            pushBadge.style.display = 'none';
        }
    } else {
        recommendEl.innerHTML = '<span style="color: var(--green);">&#10003;</span> All pushed';
        recommendEl.style.color = 'var(--green)';
        pushBtn.style.display = 'none';

        // Hide push badge
        if (pushBadge) {
            pushBadge.style.display = 'none';
        }
    }
}

async function triggerGitPush() {
    const btn = document.getElementById('git-push-btn');
    btn.disabled = true;
    btn.textContent = 'Pushing...';

    try {
        const result = await api('/api/git/trigger-push', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({squash: true})
        });

        if (result.success) {
            showNotification('Pushed ' + result.commits_pushed + ' commit(s)' +
                (result.squashed ? ' (squashed)' : ''), 'success');
            refreshGitStatusWidget();
        } else {
            showNotification('Push failed: ' + (result.error || result.message), 'error');
        }
    } catch (e) {
        showNotification('Push failed: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Push Changes (Squash)';
    }
}

function showGitError(message) {
    const healthBadge = document.getElementById('git-health-badge');
    const healthText = document.getElementById('git-health-text');
    healthBadge.className = 'git-health-badge error';
    healthText.textContent = message || 'Error';
}

// =====================================================================
// MULTI-REPO STATUS WIDGET FUNCTIONS
// =====================================================================

let repoStatusLoadingTimeout = null;
let repoStatusErrorDismissTimeout = null;

function showRepoStatusLoading() {
    // Only show loading spinner after 300ms delay
    repoStatusLoadingTimeout = setTimeout(() => {
        document.getElementById('repo-status-loading').style.display = 'flex';
    }, 300);
}

function hideRepoStatusLoading() {
    if (repoStatusLoadingTimeout) {
        clearTimeout(repoStatusLoadingTimeout);
        repoStatusLoadingTimeout = null;
    }
    document.getElementById('repo-status-loading').style.display = 'none';
}

function showRepoStatusError(message) {
    const errorDiv = document.getElementById('repo-status-error');
    const errorMsg = document.getElementById('repo-status-error-msg');
    errorDiv.style.display = 'flex';
    errorMsg.textContent = message || 'Error loading repo status';

    // Auto-dismiss after 30 seconds
    if (repoStatusErrorDismissTimeout) {
        clearTimeout(repoStatusErrorDismissTimeout);
    }
    repoStatusErrorDismissTimeout = setTimeout(() => {
        errorDiv.style.display = 'none';
    }, 30000);
}

function hideRepoStatusError() {
    document.getElementById('repo-status-error').style.display = 'none';
    if (repoStatusErrorDismissTimeout) {
        clearTimeout(repoStatusErrorDismissTimeout);
        repoStatusErrorDismissTimeout = null;
    }
}

async function refreshRepoStatusWidget() {
    showRepoStatusLoading();

    try {
        const data = await api('/api/repo-status');

        hideRepoStatusLoading();

        if (data.error) {
            showRepoStatusError(data.error);
            return;
        }

        hideRepoStatusError();

        // Update summary stats
        document.getElementById('repo-total').textContent = data.total_repos || 0;
        document.getElementById('repo-changes').textContent = data.repos_with_changes || 0;
        document.getElementById('repo-ahead').textContent = data.repos_ahead || 0;

        // Count repos without git
        const noGit = data.repos_needing_init ? data.repos_needing_init.length : 0;
        document.getElementById('repo-no-git').textContent = noGit;

        // Update init badge
        const initBadge = document.getElementById('repo-init-badge');
        const initCount = document.getElementById('repo-init-count');
        const initBtn = document.getElementById('repo-init-all-btn');

        if (noGit > 0) {
            initBadge.style.display = 'inline-flex';
            initCount.textContent = noGit;
            initBtn.style.display = 'block';
        } else {
            initBadge.style.display = 'none';
            initBtn.style.display = 'none';
        }

        // Update push all button visibility
        const pushAllBtn = document.getElementById('repo-push-all-btn');
        if (data.repos_ahead > 0) {
            pushAllBtn.style.display = 'block';
        } else {
            pushAllBtn.style.display = 'none';
        }

        // Update repo table with commit badge and view log button
        const tbody = document.getElementById('repo-status-tbody');
        if (data.repos && Object.keys(data.repos).length > 0) {
            tbody.innerHTML = Object.entries(data.repos).map(([id, repo]) => {
                let statusBadge = '';
                if (!repo.has_git) {
                    statusBadge = '<span class="repo-status-badge no-git">No Git</span>';
                } else if (!repo.remote) {
                    statusBadge = '<span class="repo-status-badge no-remote">No Remote</span>';
                } else if (repo.has_changes) {
                    statusBadge = '<span class="repo-status-badge changes">Changes</span>';
                } else {
                    statusBadge = '<span class="repo-status-badge ok">OK</span>';
                }

                const changes = repo.changes_count !== undefined ? repo.changes_count : '-';

                // Build commit badge with color coding
                let aheadBadge = '-';
                if (repo.has_git) {
                    const ahead = repo.ahead !== undefined ? repo.ahead : 0;
                    const behind = repo.behind !== undefined ? repo.behind : 0;

                    if (behind > 0) {
                        aheadBadge = '<span class="repo-commit-badge behind" title="Behind remote">-' + behind + '</span>';
                    } else if (ahead > 0) {
                        aheadBadge = '<span class="repo-commit-badge ahead" title="Ahead of remote">+' + ahead + '</span>';
                    } else {
                        aheadBadge = '<span class="repo-commit-badge synced" title="Synced">&#10003;</span>';
                    }
                }

                // View Log button (only for repos with git)
                const logBtn = repo.has_git
                    ? '<button class="repo-log-btn" onclick="openRepoLogModal(\'' + escapeHtml(id) + '\', \'' + escapeHtml(repo.name) + '\')" title="View recent commits">Log</button>'
                    : '';

                return '<tr>' +
                    '<td class="repo-status-name" title="' + escapeHtml(repo.path || '') + '">' + escapeHtml(repo.name) + '</td>' +
                    '<td>' + statusBadge + '</td>' +
                    '<td>' + changes + '</td>' +
                    '<td>' + aheadBadge + '</td>' +
                    '<td>' + logBtn + '</td>' +
                    '</tr>';
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim);">No repos configured</td></tr>';
        }

        // Update timestamp
        document.getElementById('repo-status-updated').textContent = new Date().toLocaleTimeString();

    } catch (e) {
        hideRepoStatusLoading();
        showRepoStatusError('Failed to load: ' + e.message);
        console.error('Repo status widget error:', e);
    }
}

// Repo Log Modal functions
async function openRepoLogModal(repoId, repoName) {
    const modal = document.getElementById('repo-log-modal');
    const body = document.getElementById('repo-log-modal-body');
    const repoLabel = document.getElementById('repo-log-modal-repo');

    repoLabel.textContent = repoName;
    body.innerHTML = '<div style="text-align: center; color: var(--text-dim);">Loading...</div>';
    modal.style.display = 'flex';

    try {
        const data = await api('/api/repo-status/log/' + encodeURIComponent(repoId));

        if (data.error) {
            body.innerHTML = '<div style="color: var(--red);">Error: ' + escapeHtml(data.error) + '</div>';
            return;
        }

        if (!data.commits || data.commits.length === 0) {
            body.innerHTML = '<div style="text-align: center; color: var(--text-dim);">No commits found</div>';
            return;
        }

        body.innerHTML = data.commits.map(c => {
            const date = c.date ? new Date(c.date).toLocaleString() : '';
            return '<div class="repo-log-item">' +
                '<span class="repo-log-hash" onclick="copyToClipboard(\'' + escapeHtml(c.hash_full || c.hash) + '\')" title="Click to copy full hash">' + escapeHtml(c.hash) + '</span>' +
                '<span class="repo-log-msg" title="' + escapeHtml(c.message) + '">' + escapeHtml(c.message) + '</span>' +
                '<span class="repo-log-date">' + escapeHtml(date) + '</span>' +
                '</div>';
        }).join('');

    } catch (e) {
        body.innerHTML = '<div style="color: var(--red);">Failed to load: ' + escapeHtml(e.message) + '</div>';
    }
}

function closeRepoLogModal(event) {
    // If event provided, check if clicking outside content
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('repo-log-modal').style.display = 'none';
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copied: ' + text.substring(0, 8) + '...', 'success');
    }).catch(err => {
        console.error('Copy failed:', err);
    });
}

async function initializeAllRepos() {
    const btn = document.getElementById('repo-init-all-btn');
    btn.disabled = true;
    btn.textContent = 'Initializing...';

    try {
        const result = await api('/api/repo-status/init', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });

        if (result.initialized > 0) {
            showNotification('Initialized ' + result.initialized + ' repositories', 'success');
        } else {
            showNotification('No repositories needed initialization', 'info');
        }

        // Refresh widget
        refreshRepoStatusWidget();

    } catch (e) {
        showNotification('Init failed: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Initialize All';
    }
}

async function pushAllRepos() {
    const btn = document.getElementById('repo-push-all-btn');
    btn.disabled = true;
    btn.textContent = 'Pushing...';

    try {
        const result = await api('/api/repo-status/push-all', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });

        if (result.successful > 0) {
            showNotification('Pushed ' + result.successful + '/' + result.total + ' repositories', 'success');
        } else {
            showNotification('No repositories pushed (no remotes or commits)', 'info');
        }

        // Refresh widgets
        refreshRepoStatusWidget();
        refreshGitStatusWidget();

    } catch (e) {
        showNotification('Push failed: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Push All';
    }
}

// =====================================================================
// GIT ANALYTICS WIDGET FUNCTIONS
// =====================================================================

let gitAnalyticsData = null;
let gitAnalyticsChart = null;
let gitTimelineInstance = null;
let selectedGitMission = '';

function showGitAnalyticsLoading() {
    document.getElementById('git-analytics-loading').style.display = 'flex';
    document.getElementById('git-analytics-error').style.display = 'none';
    document.getElementById('git-analytics-content').style.display = 'none';
}

function showGitAnalyticsError(message) {
    document.getElementById('git-analytics-loading').style.display = 'none';
    document.getElementById('git-analytics-error').style.display = 'block';
    document.getElementById('git-analytics-error-msg').textContent = message;
    document.getElementById('git-analytics-content').style.display = 'none';
}

function showGitAnalyticsContent() {
    document.getElementById('git-analytics-loading').style.display = 'none';
    document.getElementById('git-analytics-error').style.display = 'none';
    document.getElementById('git-analytics-content').style.display = 'block';
}

async function refreshGitAnalyticsWidget() {
    showGitAnalyticsLoading();

    // Timeout fallback - if still loading after 15s, show error
    const timeout = setTimeout(() => {
        showGitAnalyticsError('Request timed out - API may be slow');
    }, 15000);

    try {
        const url = selectedGitMission
            ? `/api/git-analytics/mission/${selectedGitMission}`
            : '/api/git-analytics/dashboard';

        const data = await api(url);
        clearTimeout(timeout);  // Cancel timeout on success

        if (data.error) {
            showGitAnalyticsError(data.error);
            return;
        }

        gitAnalyticsData = data;
        showGitAnalyticsContent();

        // Update summary stats
        if (selectedGitMission) {
            document.getElementById('git-analytics-commits').textContent = data.commit_count || 0;
            document.getElementById('git-analytics-additions').textContent = data.churn_summary?.total_additions || 0;
            document.getElementById('git-analytics-deletions').textContent = data.churn_summary?.total_deletions || 0;
            renderGitHotspots(data.hotspots || []);

            // Load time-series churn chart for selected mission (Cycle 2)
            loadChurnTimeseries(selectedGitMission);
        } else {
            const summary = data.churn_summary || {};
            document.getElementById('git-analytics-commits').textContent = summary.total_commits || 0;
            document.getElementById('git-analytics-additions').textContent = summary.total_additions || 0;
            document.getElementById('git-analytics-deletions').textContent = summary.total_deletions || 0;
            renderGitHotspots(data.hotspots || []);
            renderGitCommitsChart(data.commits_per_mission);
            populateGitMissionSelectors(data.missions || []);

            // Hide time-series chart when no mission selected
            document.getElementById('git-analytics-churn-timeseries-container').style.display = 'none';
        }

        // Load high-churn alerts (Cycle 2)
        loadHighChurnAlerts();

    } catch (e) {
        clearTimeout(timeout);  // Cancel timeout on error
        console.error('Git Analytics widget error:', e);
        showGitAnalyticsError('Failed to load analytics: ' + e.message);
    }
}

function refreshGitAnalyticsForMission() {
    const select = document.getElementById('git-analytics-mission-select');
    selectedGitMission = select.value;
    refreshGitAnalyticsWidget();
}

function populateGitMissionSelectors(missions) {
    const selects = [
        document.getElementById('git-analytics-mission-select'),
        document.getElementById('git-timeline-mission-select')
    ];

    selects.forEach(select => {
        if (!select) return;
        const currentValue = select.value;

        // Keep first option
        const firstOption = select.options[0];
        select.innerHTML = '';
        select.appendChild(firstOption);

        // Add missions
        missions.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m.mission_id;
            const shortId = m.mission_id.replace('mission_', '').slice(0, 8);
            opt.textContent = `${shortId} (${m.started_at?.split('T')[0] || 'N/A'})`;
            select.appendChild(opt);
        });

        // Restore value
        if (currentValue) select.value = currentValue;
    });
}

function renderGitCommitsChart(chartData) {
    if (!chartData) return;

    const canvas = document.getElementById('git-analytics-commits-chart');
    if (!canvas) return;

    // Check if Chart.js is loaded (it's deferred)
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not yet loaded, will retry git chart render...');
        setTimeout(() => renderGitCommitsChart(chartData), 500);
        return;
    }

    const ctx = canvas.getContext('2d');

    // Destroy existing chart
    if (gitAnalyticsChart) {
        gitAnalyticsChart.destroy();
    }

    gitAnalyticsChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: chartData.labels || [],
            datasets: chartData.datasets || []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        afterBody: function(context) {
                            const idx = context[0].dataIndex;
                            const adds = chartData.additions?.[idx] || 0;
                            const dels = chartData.deletions?.[idx] || 0;
                            return [`+${adds} / -${dels} lines`];
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(48, 54, 61, 0.5)' },
                    ticks: { color: '#8b949e', font: { size: 9 } }
                },
                y: {
                    grid: { color: 'rgba(48, 54, 61, 0.5)' },
                    ticks: { color: '#8b949e', font: { size: 9 } },
                    beginAtZero: true
                }
            }
        }
    });
}

function renderGitHotspots(hotspots) {
    const container = document.getElementById('git-analytics-hotspots');
    const countEl = document.getElementById('git-analytics-hotspots-count');

    if (!container) return;

    countEl.textContent = hotspots.length;

    if (hotspots.length === 0) {
        container.innerHTML = '<div class="git-analytics-hotspot" style="color: var(--text-dim);">No file data</div>';
        return;
    }

    container.innerHTML = hotspots.slice(0, 10).map((f, i) => {
        const filename = f.path.split('/').pop();
        return `
            <div class="git-analytics-hotspot" title="${escapeHtml(f.path)}" onclick="showFileMissionsModal('${escapeHtml(f.path)}')" style="cursor: pointer;">
                <span class="git-analytics-hotspot-rank">#${i + 1}</span>
                <span class="git-analytics-hotspot-file" style="text-decoration: underline; text-decoration-style: dotted;">${escapeHtml(filename)}</span>
                <span class="git-analytics-hotspot-commits">${f.total_commits}</span>
                <span class="git-analytics-hotspot-churn">
                    <span class="add">+${f.total_additions || 0}</span>/<span class="del">-${f.total_deletions || 0}</span>
                </span>
            </div>
        `;
    }).join('');
}

// =====================================================================
// GIT ANALYTICS - CYCLE 2 ADDITIONS
// =====================================================================

let churnTimeseriesChart = null;

// Open full-page timeline view
function openTimelinePage() {
    const missionId = document.getElementById('git-analytics-mission-select')?.value;
    if (missionId) {
        window.open(`/timeline/${missionId}`, '_blank');
    } else {
        showToast('Please select a mission first', 'warning');
    }
}

// Load and render time-series churn chart
async function loadChurnTimeseries(missionId) {
    if (!missionId) {
        document.getElementById('git-analytics-churn-timeseries-container').style.display = 'none';
        return;
    }

    // Check if Chart.js is loaded (it's deferred)
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not yet loaded, will retry churn chart render...');
        setTimeout(() => loadChurnTimeseries(missionId), 500);
        return;
    }

    try {
        const data = await api(`/api/git-analytics/churn-timeseries/${missionId}?interval=commit`);

        if (!data.labels || data.labels.length === 0) {
            document.getElementById('git-analytics-churn-timeseries-container').style.display = 'none';
            return;
        }

        document.getElementById('git-analytics-churn-timeseries-container').style.display = 'block';

        if (churnTimeseriesChart) {
            churnTimeseriesChart.destroy();
        }

        const ctx = document.getElementById('git-analytics-churn-timeseries-chart').getContext('2d');
        churnTimeseriesChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels.map(l => l.substring(0, 7)),
                datasets: [
                    {
                        label: 'Additions',
                        data: data.additions,
                        borderColor: 'rgba(63, 185, 80, 1)',
                        backgroundColor: 'rgba(63, 185, 80, 0.1)',
                        fill: true,
                        tension: 0.3
                    },
                    {
                        label: 'Deletions',
                        data: data.deletions,
                        borderColor: 'rgba(248, 81, 73, 1)',
                        backgroundColor: 'rgba(248, 81, 73, 0.1)',
                        fill: true,
                        tension: 0.3
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: '#8b949e', boxWidth: 12, font: { size: 9 } }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(48, 54, 61, 0.5)' },
                        ticks: { color: '#8b949e', font: { size: 8 }, maxRotation: 45 }
                    },
                    y: {
                        grid: { color: 'rgba(48, 54, 61, 0.5)' },
                        ticks: { color: '#8b949e', font: { size: 9 } },
                        beginAtZero: true
                    }
                }
            }
        });
    } catch (e) {
        console.error('Error loading churn timeseries:', e);
    }
}

// Load and display high-churn alerts
async function loadHighChurnAlerts() {
    try {
        const data = await api('/api/git-analytics/high-churn-alerts?threshold_commits=5&threshold_missions=2');

        const badge = document.getElementById('git-analytics-alert-badge');
        const count = document.getElementById('git-analytics-alert-count');

        if (data.alerts && data.alerts.length > 0) {
            badge.style.display = 'inline-block';
            count.textContent = data.alerts.length;
            badge.setAttribute('data-alerts', JSON.stringify(data.alerts));
        } else {
            badge.style.display = 'none';
        }
    } catch (e) {
        console.error('Error loading high-churn alerts:', e);
    }
}

// Show high-churn alerts modal
function showHighChurnAlertsModal() {
    const badge = document.getElementById('git-analytics-alert-badge');
    const alertsJson = badge.getAttribute('data-alerts');
    if (!alertsJson) return;

    const alerts = JSON.parse(alertsJson);

    const modalHtml = `
        <div id="high-churn-modal" class="modal" style="display: flex;">
            <div class="modal-content" style="max-width: 600px;">
                <div class="modal-header">
                    <h3>High-Churn Files (${alerts.length})</h3>
                    <button class="modal-close" onclick="document.getElementById('high-churn-modal').remove()">&times;</button>
                </div>
                <div class="modal-body" style="max-height: 400px; overflow-y: auto;">
                    ${alerts.map(a => `
                        <div style="padding: 10px; border-bottom: 1px solid var(--border); cursor: pointer;" onclick="showFileMissionsModal('${escapeHtml(a.file)}')">
                            <div style="font-weight: 600; color: var(--yellow);">${escapeHtml(a.file.split('/').pop())}</div>
                            <div style="font-size: 0.8em; color: var(--text-dim);">${a.file}</div>
                            <div style="font-size: 0.85em; margin-top: 5px;">
                                <span style="color: var(--red);">Score: ${a.churn_score}</span> |
                                ${a.total_commits} commits |
                                ${a.missions_touched} missions
                            </div>
                            <div style="font-size: 0.8em; color: var(--text-dim); margin-top: 3px;">${a.recommendation}</div>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

// Show file missions modal (cross-mission file tracker)
async function showFileMissionsModal(filepath) {
    try {
        const data = await api(`/api/git-analytics/file/${encodeURIComponent(filepath)}/missions`);

        const modalHtml = `
            <div id="file-missions-modal" class="modal" style="display: flex;">
                <div class="modal-content" style="max-width: 700px;">
                    <div class="modal-header">
                        <h3>File: ${escapeHtml(filepath.split('/').pop())}</h3>
                        <button class="modal-close" onclick="document.getElementById('file-missions-modal').remove()">&times;</button>
                    </div>
                    <div class="modal-body" style="max-height: 500px; overflow-y: auto;">
                        <div style="color: var(--text-dim); font-size: 0.85em; margin-bottom: 10px;">${escapeHtml(filepath)}</div>

                        <div style="display: flex; gap: 20px; margin-bottom: 15px; padding: 10px; background: var(--bg-tertiary); border-radius: 6px;">
                            <div><strong>${data.total_commits}</strong> total commits</div>
                            <div><strong>${data.missions?.length || 0}</strong> missions</div>
                            ${data.complexity ? `
                                <div>Stability: <strong style="color: ${data.complexity.stability_score > 60 ? 'var(--green)' : 'var(--red)'};">${data.complexity.stability_score}%</strong></div>
                                <div>Status: <strong>${data.complexity.recommendation}</strong></div>
                            ` : ''}
                        </div>

                        <h4 style="margin-bottom: 10px;">Missions That Modified This File</h4>
                        <div style="border: 1px solid var(--border); border-radius: 6px; overflow: hidden;">
                            <table style="width: 100%; font-size: 0.85em;">
                                <thead style="background: var(--bg-tertiary);">
                                    <tr>
                                        <th style="padding: 8px; text-align: left;">Mission</th>
                                        <th style="padding: 8px; text-align: center;">Commits</th>
                                        <th style="padding: 8px; text-align: center;">+/-</th>
                                        <th style="padding: 8px; text-align: left;">Last Modified</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${(data.missions || []).map(m => `
                                        <tr style="border-top: 1px solid var(--border);">
                                            <td style="padding: 8px; font-family: monospace;">${m.mission_id.substring(0, 16)}...</td>
                                            <td style="padding: 8px; text-align: center;">${m.commit_count}</td>
                                            <td style="padding: 8px; text-align: center;">
                                                <span style="color: var(--green);">+${m.additions}</span>/<span style="color: var(--red);">-${m.deletions}</span>
                                            </td>
                                            <td style="padding: 8px; font-size: 0.85em;">${m.last_modified ? new Date(m.last_modified).toLocaleString() : '-'}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>

                        ${data.related_files && data.related_files.length > 0 ? `
                            <h4 style="margin: 15px 0 10px;">Related Files (Often Modified Together)</h4>
                            <div style="display: flex; flex-wrap: wrap; gap: 8px;">
                                ${data.related_files.slice(0, 8).map(r => `
                                    <span style="background: var(--bg-tertiary); padding: 4px 10px; border-radius: 15px; font-size: 0.8em; cursor: pointer;" onclick="document.getElementById('file-missions-modal').remove(); showFileMissionsModal('${escapeHtml(r.file)}')">
                                        ${escapeHtml(r.file.split('/').pop())} (${r.cooccurrence_count}x)
                                    </span>
                                `).join('')}
                            </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal if any
        const existing = document.getElementById('file-missions-modal');
        if (existing) existing.remove();

        document.body.insertAdjacentHTML('beforeend', modalHtml);
    } catch (e) {
        console.error('Error loading file missions:', e);
        showToast('Error loading file details', 'error');
    }
}

// Mission Comparison Modal (Cycle 3)
function openCompareModal() {
    // Get list of missions for selectors
    const missions = gitAnalyticsData?.missions || [];

    const modalHtml = `
        <div id="compare-modal" class="modal" style="display: flex;">
            <div class="modal-content" style="max-width: 800px;">
                <div class="modal-header">
                    <h3>Compare Missions</h3>
                    <button class="modal-close" onclick="document.getElementById('compare-modal').remove()">&times;</button>
                </div>
                <div class="modal-body">
                    <div style="display: flex; gap: 20px; margin-bottom: 20px;">
                        <div style="flex: 1;">
                            <label style="display: block; margin-bottom: 5px; color: var(--text-dim);">Mission 1</label>
                            <select id="compare-mission-1" style="width: 100%; padding: 8px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px;">
                                <option value="">Select mission...</option>
                                ${missions.map(m => {
                                    const shortId = m.mission_id.replace('mission_', '').slice(0, 8);
                                    return `<option value="${m.mission_id}">${shortId} (${m.started_at?.split('T')[0] || 'N/A'})</option>`;
                                }).join('')}
                            </select>
                        </div>
                        <div style="flex: 1;">
                            <label style="display: block; margin-bottom: 5px; color: var(--text-dim);">Mission 2</label>
                            <select id="compare-mission-2" style="width: 100%; padding: 8px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px;">
                                <option value="">Select mission...</option>
                                ${missions.map(m => {
                                    const shortId = m.mission_id.replace('mission_', '').slice(0, 8);
                                    return `<option value="${m.mission_id}">${shortId} (${m.started_at?.split('T')[0] || 'N/A'})</option>`;
                                }).join('')}
                            </select>
                        </div>
                    </div>
                    <button class="btn primary" onclick="runComparison()" style="width: 100%;">Compare</button>
                    <div id="compare-results" style="margin-top: 20px;"></div>
                </div>
            </div>
        </div>
    `;

    // Remove existing modal if any
    const existing = document.getElementById('compare-modal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

async function runComparison() {
    const mission1 = document.getElementById('compare-mission-1').value;
    const mission2 = document.getElementById('compare-mission-2').value;
    const resultsDiv = document.getElementById('compare-results');

    if (!mission1 || !mission2) {
        resultsDiv.innerHTML = '<div style="color: var(--yellow); text-align: center;">Please select both missions</div>';
        return;
    }

    if (mission1 === mission2) {
        resultsDiv.innerHTML = '<div style="color: var(--yellow); text-align: center;">Please select different missions</div>';
        return;
    }

    resultsDiv.innerHTML = '<div class="git-analytics-loading"><div class="spinner"></div><span>Comparing missions...</span></div>';

    try {
        const data = await api(`/api/git-analytics/compare?mission_1=${mission1}&mission_2=${mission2}`);

        if (data.error) {
            resultsDiv.innerHTML = `<div class="git-analytics-error">${data.error}</div>`;
            return;
        }

        const m1 = data.mission_1;
        const m2 = data.mission_2;
        const comp = data.comparison;
        const tc = data.timeline_comparison;

        resultsDiv.innerHTML = `
            <h4 style="margin-bottom: 15px; color: var(--accent);">Comparison Results</h4>

            <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                <thead>
                    <tr style="background: var(--bg-tertiary);">
                        <th style="padding: 8px; text-align: left; border: 1px solid var(--border);">Metric</th>
                        <th style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m1.mission_id.replace('mission_', '').slice(0, 8)}</th>
                        <th style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m2.mission_id.replace('mission_', '').slice(0, 8)}</th>
                        <th style="padding: 8px; text-align: center; border: 1px solid var(--border);">Diff</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="padding: 8px; border: 1px solid var(--border);">Commits</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m1.commit_count}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m2.commit_count}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: ${comp.commit_count_diff >= 0 ? 'var(--green)' : 'var(--red)'};">${comp.commit_count_diff >= 0 ? '+' : ''}${comp.commit_count_diff}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid var(--border);">Lines Added</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: var(--green);">+${m1.total_additions}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: var(--green);">+${m2.total_additions}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: ${comp.churn_diff.additions >= 0 ? 'var(--green)' : 'var(--red)'};">${comp.churn_diff.additions >= 0 ? '+' : ''}${comp.churn_diff.additions}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid var(--border);">Lines Deleted</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: var(--red);">-${m1.total_deletions}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: var(--red);">-${m2.total_deletions}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border); color: ${comp.churn_diff.deletions >= 0 ? 'var(--green)' : 'var(--red)'};">${comp.churn_diff.deletions >= 0 ? '+' : ''}${comp.churn_diff.deletions}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid var(--border);">Files Changed</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m1.files_changed}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m2.files_changed}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${m1.files_changed - m2.files_changed >= 0 ? '+' : ''}${m1.files_changed - m2.files_changed}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border: 1px solid var(--border);">Duration</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${tc.duration_1_formatted}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">${tc.duration_2_formatted}</td>
                        <td style="padding: 8px; text-align: center; border: 1px solid var(--border);">-</td>
                    </tr>
                </tbody>
            </table>

            <div style="background: var(--bg-tertiary); padding: 12px; border-radius: 6px; margin-bottom: 10px;">
                <div style="font-weight: 600; margin-bottom: 8px;">File Overlap Analysis</div>
                <div style="font-size: 0.9em;">
                    <span style="color: var(--accent);">${comp.overlap_percentage}%</span> file overlap
                    (${comp.shared_files_count} shared files)
                </div>
                <div style="font-size: 0.85em; color: var(--text-dim); margin-top: 5px;">
                    ${comp.unique_to_1_count} unique to Mission 1 |
                    ${comp.unique_to_2_count} unique to Mission 2
                </div>
            </div>

            ${comp.shared_files.length > 0 ? `
                <details style="margin-top: 10px;">
                    <summary style="cursor: pointer; color: var(--text-dim);">Shared Files (${comp.shared_files_count})</summary>
                    <div style="max-height: 150px; overflow-y: auto; margin-top: 10px;">
                        ${comp.shared_files.map(f => `<div style="padding: 3px 0; font-size: 0.85em;">${escapeHtml(f)}</div>`).join('')}
                    </div>
                </details>
            ` : ''}
        `;
    } catch (e) {
        console.error('Comparison error:', e);
        resultsDiv.innerHTML = `<div class="git-analytics-error">Failed to compare: ${e.message}</div>`;
    }
}

// Timeline Modal Functions
function showGitTimelineModal() {
    document.getElementById('git-timeline-modal').style.display = 'flex';

    // Pre-select current mission if one is selected
    const currentMission = selectedGitMission || document.getElementById('git-analytics-mission-select')?.value;
    if (currentMission) {
        document.getElementById('git-timeline-mission-select').value = currentMission;
        loadGitTimeline();
    }
}

function closeGitTimelineModal() {
    document.getElementById('git-timeline-modal').style.display = 'none';
    if (gitTimelineInstance) {
        gitTimelineInstance.destroy();
        gitTimelineInstance = null;
    }
}

async function loadGitTimeline() {
    const missionId = document.getElementById('git-timeline-mission-select').value;
    if (!missionId) {
        document.getElementById('git-timeline-container').innerHTML =
            '<div style="text-align: center; color: var(--text-dim); padding: 40px;">Select a mission to view its timeline</div>';
        document.getElementById('git-timeline-commit-list').innerHTML =
            '<div style="color: var(--text-dim); font-size: 0.85em;">No commits loaded</div>';
        return;
    }

    try {
        // Load timeline data
        const timelineData = await api(`/api/git-analytics/timeline/${missionId}`);

        // Load commit details
        const missionData = await api(`/api/git-analytics/mission/${missionId}`);

        // Render timeline
        renderGitTimeline(timelineData);

        // Render commit list
        renderGitCommitList(missionData.commits || []);

    } catch (e) {
        console.error('Error loading timeline:', e);
        document.getElementById('git-timeline-container').innerHTML =
            '<div style="text-align: center; color: var(--red); padding: 40px;">Error loading timeline</div>';
    }
}

function renderGitTimeline(data) {
    const container = document.getElementById('git-timeline-container');

    if (gitTimelineInstance) {
        gitTimelineInstance.destroy();
    }

    if (!data.items || data.items.length === 0) {
        container.innerHTML = '<div style="text-align: center; color: var(--text-dim); padding: 40px;">No timeline data</div>';
        return;
    }

    container.innerHTML = '';

    // Create timeline
    const items = new vis.DataSet(data.items);
    const groups = new vis.DataSet(data.groups);

    const options = {
        stack: true,
        showCurrentTime: false,
        zoomable: true,
        moveable: true,
        orientation: 'top',
        height: '180px',
        tooltip: {
            followMouse: true
        }
    };

    gitTimelineInstance = new vis.Timeline(container, items, groups, options);
}

function renderGitCommitList(commits) {
    const container = document.getElementById('git-timeline-commit-list');

    if (commits.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No commits found</div>';
        return;
    }

    container.innerHTML = commits.map(c => {
        const dateStr = c.timestamp ? new Date(c.timestamp).toLocaleString() : 'N/A';
        const typeClass = c.is_checkpoint ? 'checkpoint' : (c.is_auto_commit ? 'auto' : '');
        return `
            <div style="padding: 8px; border-bottom: 1px solid var(--border); font-size: 0.85em;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <code style="color: var(--accent);">${escapeHtml(c.short_sha)}</code>
                    <span style="color: var(--text-dim); font-size: 0.8em;">${dateStr}</span>
                </div>
                <div style="margin-top: 4px; color: var(--text);">${escapeHtml(c.message)}</div>
                <div style="margin-top: 4px; font-size: 0.8em;">
                    <span style="color: var(--green);">+${c.additions}</span> /
                    <span style="color: var(--red);">-${c.deletions}</span>
                    <span style="color: var(--text-dim); margin-left: 8px;">${c.files_changed?.length || 0} files</span>
                    ${c.is_checkpoint ? '<span style="margin-left: 8px; background: var(--yellow); color: #000; padding: 1px 4px; border-radius: 3px; font-size: 0.75em;">checkpoint</span>' : ''}
                </div>
            </div>
        `;
    }).join('');
}

async function exportGitAnalytics() {
    const missionId = selectedGitMission ||
        document.getElementById('git-analytics-mission-select')?.value ||
        document.getElementById('git-timeline-mission-select')?.value;

    if (!missionId) {
        showNotification('Please select a mission to export', 'error');
        return;
    }

    try {
        // Open export in new tab
        const format = 'markdown';
        window.open(`/api/git-analytics/export/${missionId}?format=${format}`, '_blank');
    } catch (e) {
        console.error('Export error:', e);
        showNotification('Export failed', 'error');
    }
}

// =====================================================================
// KB ANALYTICS WIDGET FUNCTIONS
// =====================================================================

let kbAnalyticsData = null;
let kbSelectedMissions = [];
let kbAllMissions = [];
let kbChainLoaded = false;
let kbChainObserver = null;
let kbLastFocusedElement = null;

// Loading state helpers
function showKBLoading(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '<div class="kb-loading" role="status" aria-label="Loading"><div class="kb-spinner"></div></div>';
    }
}

function showKBSkeleton(containerId, type = 'list') {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (type === 'list') {
        container.innerHTML = Array(4).fill(0).map(() =>
            '<div class="kb-skeleton kb-skeleton-bar"></div>'
        ).join('');
    } else if (type === 'stat') {
        container.innerHTML = '<div class="kb-skeleton kb-skeleton-stat"></div>';
    }
}

function showKBError(containerId, message, retryFn = null) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const retryBtn = retryFn ? `<button class="kb-retry-btn" onclick="${retryFn}">Retry</button>` : '';
    container.innerHTML = `
        <div class="kb-error" role="alert" aria-live="polite">
            <span class="kb-error-icon">!</span>
            <span class="kb-error-message">${escapeHtml(message)}</span>
            ${retryBtn}
        </div>
    `;
}

// Initialize lazy loading for chain graph
function initKBChainLazyLoad() {
    const chainContainer = document.getElementById('kb-chain-container');
    if (!chainContainer || kbChainObserver) return;

    kbChainObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !kbChainLoaded) {
                loadKBChainData();
            }
        });
    }, { threshold: 0.1, rootMargin: '100px' });

    kbChainObserver.observe(chainContainer);
}

async function loadKBChainData() {
    if (kbChainLoaded) return;

    const svg = document.getElementById('kb-chain-graph');
    svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">Loading chains...</text>';

    try {
        const data = await api('/api/knowledge-base/analytics/chains');
        if (data.error) {
            svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--red)" font-size="8">Failed to load chains</text>';
            return;
        }
        renderKBChainGraph(data.chains || data || []);
        kbChainLoaded = true;
        if (kbChainObserver) kbChainObserver.disconnect();
    } catch (e) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--red)" font-size="8">Failed to load chains</text>';
        console.error('KB Chain load error:', e);
    }
}

async function refreshKBAnalyticsWidget() {
    // Show loading states
    showKBSkeleton('kb-themes-list', 'list');
    const svg = document.getElementById('kb-accumulation-svg');
    if (svg) svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="10">Loading...</text>';

    // Timeout fallback - if still loading after 15s, show error
    const timeout = setTimeout(() => {
        if (svg) svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--red)" font-size="10">Timeout - Click to retry</text>';
        showKBError('kb-themes-list', 'Request timed out', 'refreshKBAnalyticsWidget()');
    }, 15000);

    try {
        // Get current filter settings
        const filter = document.getElementById('kb-time-filter').value;
        let url = '/api/knowledge-base/analytics';

        if (filter !== 'all' && filter !== 'custom') {
            const endDate = new Date();
            const startDate = new Date();
            startDate.setDate(startDate.getDate() - parseInt(filter));
            url += `?start_date=${startDate.toISOString()}&end_date=${endDate.toISOString()}`;
        } else if (filter === 'custom') {
            const startDate = document.getElementById('kb-start-date').value;
            const endDate = document.getElementById('kb-end-date').value;
            if (startDate) url += `?start_date=${startDate}`;
            if (endDate) url += (url.includes('?') ? '&' : '?') + `end_date=${endDate}`;
        }

        const data = await api(url);
        clearTimeout(timeout);  // Cancel timeout on success

        if (data.error) {
            console.error('KB Analytics error:', data.error);
            showKBError('kb-themes-list', 'Unable to load themes', 'refreshKBAnalyticsWidget()');
            return;
        }

        kbAnalyticsData = data;

        // Update summary stats
        const accum = data.accumulation || {};
        const transfer = data.transfer_rate || {};

        document.getElementById('kb-total-learnings').textContent = accum.total_learnings || 0;
        document.getElementById('kb-transfer-rate').textContent = (transfer.transfer_rate || 0) + '%';

        // Render charts (not chains - lazy loaded separately)
        renderKBLineChart(accum.missions || []);
        renderKBTypePieChart(data.type_distribution || {});
        renderKBThemesList(data.top_themes || {});

        // Initialize lazy loading for chains
        kbChainLoaded = false;  // Reset for filter changes
        initKBChainLazyLoad();

        // Update transfer details
        document.getElementById('kb-chain-count').textContent = transfer.chain_count || 0;
        document.getElementById('kb-domain-continuity').textContent = (transfer.domain_continuity || 0) + '%';

        // Load mission list for comparison
        await loadKBMissionList();

    } catch (e) {
        clearTimeout(timeout);  // Cancel timeout on error
        console.error('KB Analytics widget error:', e);
        showKBError('kb-themes-list', 'Connection error. Please try again.', 'refreshKBAnalyticsWidget()');
    }
}

// SVG Line Chart with interactive tooltips
function renderKBLineChart(missions) {
    const svg = document.getElementById('kb-accumulation-svg');
    const tooltip = document.getElementById('kb-line-tooltip');

    try {
        if (!svg) {
            console.error('KB Line Chart: SVG element not found');
            return;
        }

        if (!missions || missions.length === 0) {
            svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="10">No data</text>';
            return;
        }

        const values = missions.map(m => m.cumulative || m.count || 0);
    const maxVal = Math.max(...values, 1);
    const padding = 15;
    const width = 300 - padding * 2;
    const height = 100 - padding * 2;

    // Build points array
    const points = missions.map((m, i) => {
        const x = padding + (missions.length === 1 ? width / 2 : (i / (missions.length - 1)) * width);
        const y = height + padding - (values[i] / maxVal) * height;
        return { x, y, data: m };
    });

    // Build path d attribute
    const pathD = points.map((p, i) =>
        (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1)
    ).join(' ');

    // Create filled area
    const areaD = pathD +
        ` L${(padding + width).toFixed(1)},${(height + padding).toFixed(1)}` +
        ` L${padding},${(height + padding).toFixed(1)} Z`;

    // Create SVG content
    let content = `
        <defs>
            <linearGradient id="kbLineGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" style="stop-color:#58a6ff;stop-opacity:0.3"/>
                <stop offset="100%" style="stop-color:#58a6ff;stop-opacity:0"/>
            </linearGradient>
        </defs>
        <path d="${areaD}" fill="url(#kbLineGradient)"/>
        <path d="${pathD}" fill="none" stroke="#58a6ff" stroke-width="2"/>
    `;

    // Add circles for hover targets
    points.forEach((p, i) => {
        content += `
            <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5"
                    fill="#58a6ff" class="kb-line-point"
                    data-idx="${i}"/>
        `;
    });

    // Add Y-axis labels
    content += `
        <text x="${padding - 2}" y="${padding + 3}" text-anchor="end" fill="var(--text-dim)" font-size="8">${maxVal}</text>
        <text x="${padding - 2}" y="${height + padding}" text-anchor="end" fill="var(--text-dim)" font-size="8">0</text>
    `;

    svg.innerHTML = content;

    // Store data for tooltip
    svg.kbData = missions;

    // Add hover events
    svg.querySelectorAll('.kb-line-point').forEach(circle => {
        circle.addEventListener('mouseenter', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            const m = missions[idx];
            const date = m.timestamp ? new Date(m.timestamp).toLocaleDateString() : 'N/A';
            tooltip.innerHTML = `
                <strong>${m.mission_id}</strong><br>
                <span style="color: var(--text-dim);">${date}</span><br>
                ${m.count} new learnings<br>
                <span style="color: var(--green);">Total: ${m.cumulative}</span>
            `;
            tooltip.style.display = 'block';
            tooltip.style.left = (e.pageX + 15) + 'px';
            tooltip.style.top = (e.pageY - 40) + 'px';
            e.target.setAttribute('r', '7');
        });
        circle.addEventListener('mouseleave', (e) => {
            tooltip.style.display = 'none';
            e.target.setAttribute('r', '5');
        });
        circle.addEventListener('mousemove', (e) => {
            tooltip.style.left = (e.pageX + 15) + 'px';
            tooltip.style.top = (e.pageY - 40) + 'px';
        });
    });

    } catch (e) {
        console.error('KB Line Chart render error:', e);
        if (svg) svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--red)" font-size="10">Render error</text>';
    }
}

function renderKBTypePieChart(typeData) {
    const svg = document.getElementById('kb-type-pie');
    const legend = document.getElementById('kb-type-legend');

    const distribution = typeData.distribution || {};
    const entries = Object.entries(distribution);
    const total = entries.reduce((sum, [, count]) => sum + count, 0);

    if (total === 0) {
        svg.innerHTML = '<circle cx="50" cy="50" r="40" fill="var(--bg)" stroke="var(--border)"/>';
        legend.innerHTML = '<div style="color: var(--text-dim);">No data</div>';
        return;
    }

    const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#a371f7'];
    const circumference = 2 * Math.PI * 35;
    let offset = 0;

    // Build SVG segments
    let svgContent = '';
    entries.forEach(([type, count], i) => {
        const percent = count / total;
        const dash = percent * circumference;
        svgContent += `
            <circle cx="50" cy="50" r="35"
                    fill="none"
                    stroke="${colors[i % colors.length]}"
                    stroke-width="10"
                    stroke-dasharray="${dash} ${circumference - dash}"
                    stroke-dashoffset="${-offset}"
                    transform="rotate(-90 50 50)"/>
        `;
        offset += dash;
    });

    svg.innerHTML = svgContent;

    // Build legend
    const legendHtml = entries.map(([type, count], i) => `
        <div class="kb-pie-legend-item">
            <span class="kb-pie-legend-dot" style="background: ${colors[i % colors.length]};"></span>
            <span>${type}</span>
            <span style="color: var(--text-dim); margin-left: auto;">${count}</span>
        </div>
    `).join('');

    legend.innerHTML = legendHtml;
}

function renderKBThemesList(themesData) {
    const list = document.getElementById('kb-themes-list');
    const themes = themesData.themes || [];

    if (themes.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em; text-align: center;" role="status">No themes</div>';
        return;
    }

    const maxCount = Math.max(...themes.map(t => t.count), 1);

    const html = themes.slice(0, 8).map((t, i) => {
        const barWidth = Math.max(5, (t.count / maxCount) * 60);
        const escapedTheme = escapeHtml(t.theme);
        return `
            <div class="kb-theme-item"
                 role="button"
                 tabindex="0"
                 aria-label="Theme: ${escapedTheme}, ${t.count} occurrences. Press Enter for details."
                 onclick="openKBThemeModal('${escapedTheme}', 'domain')"
                 onkeydown="handleKBThemeKeyDown(event, '${escapedTheme}')">
                <span class="kb-theme-name">${escapedTheme}</span>
                <div class="kb-theme-bar" style="width: ${barWidth}px;" aria-hidden="true"></div>
                <span class="kb-theme-count" aria-hidden="true">${t.count}</span>
            </div>
        `;
    }).join('');

    list.innerHTML = html;
}

// Keyboard navigation for theme list
function handleKBThemeKeyDown(e, theme) {
    const items = Array.from(document.querySelectorAll('.kb-theme-item'));
    const currentIdx = items.indexOf(e.target);

    switch(e.key) {
        case 'ArrowDown':
            e.preventDefault();
            if (currentIdx < items.length - 1) {
                items[currentIdx + 1].focus();
            }
            break;
        case 'ArrowUp':
            e.preventDefault();
            if (currentIdx > 0) {
                items[currentIdx - 1].focus();
            }
            break;
        case 'Enter':
        case ' ':
            e.preventDefault();
            openKBThemeModal(theme, 'domain');
            break;
        case 'Escape':
            e.target.blur();
            break;
    }
}

// Theme modal with focus management
function openKBThemeModal(theme, themeType) {
    kbLastFocusedElement = document.activeElement;
    showThemeDetails(theme, themeType);
}

// Theme Drill-down Modal
async function showThemeDetails(theme, themeType) {
    const modal = document.getElementById('kb-theme-modal');
    const title = document.getElementById('kb-theme-modal-title');
    const body = document.getElementById('kb-theme-modal-body');

    title.textContent = `Theme: ${theme}`;
    body.innerHTML = '<div class="kb-loading" role="status" aria-label="Loading theme details"><div class="kb-spinner"></div></div>';
    modal.style.display = 'flex';

    // Set up focus trap
    setupKBModalFocusTrap();

    // Focus the close button
    setTimeout(() => {
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) closeBtn.focus();
    }, 100);

    try {
        const data = await api(`/api/knowledge-base/analytics/learnings-by-theme?theme=${encodeURIComponent(theme)}&type=${themeType}`);

        if (data.error) {
            body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(data.error)}</span></div>`;
            return;
        }

        let html = `<div class="kb-theme-stats" role="region" aria-label="Theme statistics">
            <span><strong>${data.total_learnings}</strong> learnings</span>
            <span><strong>${data.mission_count}</strong> missions</span>
        </div>`;

        if (Object.keys(data.by_mission || {}).length === 0) {
            html += '<div style="color: var(--text-dim);" role="status">No learnings found for this theme.</div>';
        } else {
            for (const [mission, learnings] of Object.entries(data.by_mission)) {
                html += `<div class="kb-theme-mission" role="region" aria-label="Mission ${escapeHtml(mission)}">
                    <div class="kb-theme-mission-header">${escapeHtml(mission)} (${learnings.length})</div>
                    <ul class="kb-theme-learnings" role="list">`;

                for (const l of learnings) {
                    const typeClass = (l.type || 'unknown').toLowerCase();
                    html += `<li role="listitem">
                        <span class="kb-learning-type ${typeClass}" aria-label="Type: ${l.type || 'unknown'}">${l.type || 'unknown'}</span>
                        <span class="kb-learning-title">${escapeHtml(l.title || 'Untitled')}</span>
                    </li>`;
                }

                html += `</ul></div>`;
            }
        }

        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(e.message)}</span></div>`;
    }
}

function closeKBThemeModal() {
    const modal = document.getElementById('kb-theme-modal');
    modal.style.display = 'none';

    // Return focus to the last focused element
    if (kbLastFocusedElement) {
        kbLastFocusedElement.focus();
        kbLastFocusedElement = null;
    }
}

// Focus trap for modal
function setupKBModalFocusTrap() {
    const modal = document.getElementById('kb-theme-modal');

    modal.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeKBThemeModal();
            return;
        }

        if (e.key !== 'Tab') return;

        const focusable = modal.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );

        if (focusable.length === 0) return;

        const firstEl = focusable[0];
        const lastEl = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === firstEl) {
            e.preventDefault();
            lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
            e.preventDefault();
            firstEl.focus();
        }
    });
}

// Learning Chain Graph Visualization
function renderKBChainGraph(chains) {
    const svg = document.getElementById('kb-chain-graph');
    const legend = document.getElementById('kb-chain-legend');

    if (!chains || chains.length === 0) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">No chains</text>';
        legend.innerHTML = '';
        return;
    }

    // Build nodes (missions) and edges (chains connecting them)
    const missions = new Set();
    const edges = [];
    const chainColors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff'];

    chains.slice(0, 5).forEach((chain, chainIdx) => {
        const chainMissions = chain.missions || [];
        chainMissions.forEach(m => missions.add(m));
        // Create edges between consecutive missions in chain
        for (let i = 0; i < chainMissions.length - 1; i++) {
            edges.push({
                source: chainMissions[i],
                target: chainMissions[i + 1],
                theme: chain.theme || 'unknown',
                color: chainColors[chainIdx % chainColors.length]
            });
        }
    });

    const missionArray = Array.from(missions);
    const nodeCount = missionArray.length;

    if (nodeCount === 0) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">No chains</text>';
        return;
    }

    // Position nodes in a circle
    const centerX = 100;
    const centerY = 50;
    const radius = Math.min(40, 30 + nodeCount * 2);

    const nodes = missionArray.map((id, i) => ({
        id,
        x: centerX + Math.cos((i * 2 * Math.PI / nodeCount) - Math.PI/2) * radius,
        y: centerY + Math.sin((i * 2 * Math.PI / nodeCount) - Math.PI/2) * radius
    }));

    // Render SVG
    let svgContent = '';

    // Draw edges
    edges.forEach(e => {
        const s = nodes.find(n => n.id === e.source);
        const t = nodes.find(n => n.id === e.target);
        if (s && t) {
            svgContent += `<line x1="${s.x.toFixed(1)}" y1="${s.y.toFixed(1)}"
                                x2="${t.x.toFixed(1)}" y2="${t.y.toFixed(1)}"
                                stroke="${e.color}" stroke-width="1.5" opacity="0.7"/>`;
        }
    });

    // Draw nodes
    nodes.forEach(n => {
        svgContent += `
            <circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="6" fill="#3fb950"
                    class="kb-chain-node" data-mission="${n.id}"/>
            <text x="${n.x.toFixed(1)}" y="${(n.y + 14).toFixed(1)}" text-anchor="middle"
                  font-size="5" fill="var(--text-dim)">${n.id.slice(-8)}</text>
        `;
    });

    svg.innerHTML = svgContent;

    // Build legend
    const legendHtml = chains.slice(0, 5).map((chain, i) => `
        <div class="kb-chain-legend-item">
            <span class="kb-chain-legend-dot" style="background: ${chainColors[i % chainColors.length]};"></span>
            <span>${escapeHtml((chain.theme || 'Chain ' + (i+1)).substring(0, 20))}</span>
        </div>
    `).join('');

    legend.innerHTML = legendHtml;
}

// Time Range Filter
function applyKBTimeFilter() {
    const filter = document.getElementById('kb-time-filter').value;
    const startInput = document.getElementById('kb-start-date');
    const endInput = document.getElementById('kb-end-date');

    if (filter === 'custom') {
        startInput.style.display = 'inline-block';
        endInput.style.display = 'inline-block';
    } else {
        startInput.style.display = 'none';
        endInput.style.display = 'none';
    }

    // Refresh the widget with new filter
    refreshKBAnalyticsWidget();
}

// Mission Comparison
async function loadKBMissionList() {
    try {
        const data = await api('/api/knowledge-base/analytics/missions');
        kbAllMissions = data.missions || [];

        const container = document.getElementById('kb-mission-checkboxes');
        if (kbAllMissions.length === 0) {
            container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em;">No missions with learnings</div>';
            return;
        }

        const html = kbAllMissions.slice(0, 15).map(m => `
            <div class="kb-mission-checkbox">
                <input type="checkbox" id="kb-cmp-${m.mission_id}"
                       onchange="toggleMissionComparison('${m.mission_id}')"
                       ${kbSelectedMissions.includes(m.mission_id) ? 'checked' : ''}>
                <label for="kb-cmp-${m.mission_id}">${m.mission_id}</label>
                <span class="count">${m.learning_count}</span>
            </div>
        `).join('');

        container.innerHTML = html;
    } catch (e) {
        console.error('Load mission list error:', e);
    }
}

async function toggleMissionComparison(missionId) {
    const idx = kbSelectedMissions.indexOf(missionId);
    if (idx >= 0) {
        kbSelectedMissions.splice(idx, 1);
    } else if (kbSelectedMissions.length < 3) {
        kbSelectedMissions.push(missionId);
    } else {
        // Uncheck the checkbox - max 3 missions
        document.getElementById(`kb-cmp-${missionId}`).checked = false;
        return;
    }

    await updateMissionComparison();
}

async function updateMissionComparison() {
    const section = document.getElementById('kb-comparison-section');
    const container = document.getElementById('kb-comparison-container');

    if (kbSelectedMissions.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = '<div style="color: var(--text-dim);">Loading...</div>';

    try {
        const profiles = await Promise.all(
            kbSelectedMissions.map(id =>
                api(`/api/knowledge-base/analytics/mission-profile?mission_id=${encodeURIComponent(id)}`)
            )
        );

        const html = profiles.map(p => {
            if (p.error) {
                return `<div class="kb-mission-card">
                    <div class="kb-mission-card-header">
                        ${escapeHtml(p.mission_id || 'Error')}
                        <span class="remove" onclick="removeMissionComparison('${p.mission_id}')">&times;</span>
                    </div>
                    <div style="color: var(--red);">Error: ${p.error}</div>
                </div>`;
            }

            const types = Object.entries(p.type_distribution || {})
                .map(([t, c]) => `${t}: ${c}`)
                .join(', ') || 'None';

            const themes = (p.top_themes || [])
                .slice(0, 3)
                .map(t => t.theme)
                .join(', ') || 'None';

            return `<div class="kb-mission-card">
                <div class="kb-mission-card-header">
                    ${escapeHtml(p.mission_id)}
                    <span class="remove" onclick="removeMissionComparison('${p.mission_id}')">&times;</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Learnings</span>
                    <span class="value">${p.total_learnings}</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Types</span>
                    <span class="value" style="font-size: 0.8em;">${types}</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Themes</span>
                    <span class="value" style="font-size: 0.8em;">${themes}</span>
                </div>
            </div>`;
        }).join('');

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color: var(--red);">Error: ${e.message}</div>`;
    }
}

function removeMissionComparison(missionId) {
    const idx = kbSelectedMissions.indexOf(missionId);
    if (idx >= 0) {
        kbSelectedMissions.splice(idx, 1);
        const checkbox = document.getElementById(`kb-cmp-${missionId}`);
        if (checkbox) checkbox.checked = false;
        updateMissionComparison();
    }
}

function clearMissionComparison() {
    kbSelectedMissions = [];
    document.querySelectorAll('#kb-mission-checkboxes input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    document.getElementById('kb-comparison-section').style.display = 'none';
}

// =====================================================================
// LESSONS LEARNED FUNCTIONS
// =====================================================================

let lessonsData = [];
let selectedLearningId = null;

async function loadAllLessons() {
    try {
        const domain = document.getElementById('lessons-domain-filter').value;
        const type = document.getElementById('lessons-type-filter').value;

        let url = '/api/knowledge-base/learnings?limit=100';
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);

        const data = await api(url);
        lessonsData = data.learnings || [];
        renderLessonsList(lessonsData);

        // Update stats
        const stats = await api('/api/knowledge-base/stats');
        if (!stats.error) {
            document.getElementById('lessons-total-count').textContent = stats.total_learnings || 0;
            document.getElementById('lessons-missions-count').textContent = stats.total_missions || 0;
        }

        // Load domains for filter
        const domains = await api('/api/knowledge-base/domains');
        if (domains.domains && domains.domains.length > 0) {
            const select = document.getElementById('lessons-domain-filter');
            const currentValue = select.value;
            select.innerHTML = '<option value="">All Domains</option>' +
                domains.domains.map(d => `<option value="${d}">${d}</option>`).join('');
            select.value = currentValue;
        }
    } catch (e) {
        console.error('Load lessons error:', e);
    }
}

async function searchLessons() {
    const query = document.getElementById('lessons-search-input').value.trim();
    if (!query) {
        loadAllLessons();
        return;
    }

    try {
        const domain = document.getElementById('lessons-domain-filter').value;
        const type = document.getElementById('lessons-type-filter').value;

        let url = '/api/knowledge-base/search?q=' + encodeURIComponent(query);
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);

        const data = await api(url);
        lessonsData = data.results || [];
        renderLessonsList(lessonsData);
    } catch (e) {
        console.error('Search lessons error:', e);
    }
}

function renderLessonsList(lessons) {
    const list = document.getElementById('lessons-list');
    if (!lessons || lessons.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learnings found</div>';
        return;
    }

    const html = lessons.map(l => `
        <div class="learning-item ${l.learning_id === selectedLearningId ? 'active' : ''}"
             onclick="showLearningDetails('${l.learning_id}')">
            <div class="learning-item-title">${l.title || 'Untitled'}</div>
            <div class="learning-item-meta">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
                ${l.problem_domain || ''}
            </div>
        </div>
    `).join('');

    list.innerHTML = html;
}

async function showLearningDetails(learningId) {
    selectedLearningId = learningId;
    renderLessonsList(lessonsData);  // Re-render to show active state

    try {
        const data = await api('/api/knowledge-base/learnings/' + learningId);
        if (data.error) {
            document.getElementById('lessons-details').innerHTML =
                '<div style="color: var(--red);">Error loading learning</div>';
            return;
        }

        const html = `
            <div class="learning-detail-panel">
                <div class="learning-detail-section">
                    <h4>Title</h4>
                    <p>${data.title || 'Untitled'}</p>
                </div>
                <div class="learning-detail-section">
                    <h4>Type & Domain</h4>
                    <p>
                        <span class="learning-type-badge ${data.learning_type || ''}">${data.learning_type || 'unknown'}</span>
                        ${data.problem_domain || 'No domain'}
                    </p>
                </div>
                <div class="learning-detail-section">
                    <h4>Description</h4>
                    <p style="white-space: pre-wrap;">${data.description || 'No description'}</p>
                </div>
                <div class="learning-detail-section">
                    <h4>Outcome</h4>
                    <p>${data.outcome || 'Unknown'}</p>
                </div>
                ${data.relevance_keywords && data.relevance_keywords.length > 0 ? `
                <div class="learning-detail-section">
                    <h4>Keywords</h4>
                    <div class="learning-keywords">
                        ${data.relevance_keywords.map(k => `<span class="learning-keyword">${k}</span>`).join('')}
                    </div>
                </div>
                ` : ''}
                <div class="learning-detail-section">
                    <h4>Source Mission</h4>
                    <p style="color: var(--text-dim); font-size: 0.85em;">${data.mission_id || 'Unknown'}</p>
                </div>
            </div>
        `;

        document.getElementById('lessons-details').innerHTML = html;
    } catch (e) {
        console.error('Show learning details error:', e);
    }
}

// =====================================================================
// KNOWLEDGE BASE ADVANCED FEATURES
// =====================================================================

let currentLessonsSubtab = 'list';
let clustersData = [];
let duplicatesData = [];
let selectedLearnings = new Set();
let batchDeleteMode = false;
let analyticsVisible = false;
let selectedMergeTarget = {};

function showLessonsSubtab(subtab) {
    currentLessonsSubtab = subtab;

    // Update subtab buttons
    document.querySelectorAll('.lessons-subtab').forEach(btn => {
        btn.classList.remove('active');
        if (btn.textContent.toLowerCase() === subtab) {
            btn.classList.add('active');
        }
    });

    // Show/hide content
    document.querySelectorAll('.subtab-content').forEach(el => {
        el.classList.remove('active');
    });
    document.getElementById('lessons-' + subtab + '-content').classList.add('active');

    // Load data for selected subtab
    if (subtab === 'clusters') {
        loadClusters();
    } else if (subtab === 'duplicates') {
        loadDuplicates();
    } else if (subtab === 'chains') {
        loadLearningChains();
    }
}

// --- Learning Chains ---
let chainsData = [];

async function loadLearningChains() {
    try {
        const data = await api('/api/knowledge-base/learning-chains?min_length=2');
        chainsData = data.chains || [];
        document.getElementById('chains-count').textContent =
            chainsData.length > 0 ? `(${chainsData.length} chains found)` : '';
        renderChainsPanel(chainsData);
    } catch (e) {
        console.error('Load learning chains error:', e);
        document.getElementById('chains-panel').innerHTML =
            '<div style="color: var(--red);">Error loading learning chains</div>';
    }
}

function renderChainsPanel(chains) {
    const container = document.getElementById('chains-panel');
    if (!chains || chains.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learning chains found. Chains require at least 2 related learnings across different missions.</div>';
        return;
    }

    container.innerHTML = chains.map((chain, idx) => `
        <div class="chain-node">
            <div class="chain-header" onclick="toggleChain(${idx})">
                <span class="expand-icon">&#9654;</span>
                <span class="chain-theme">${escapeHtml(chain.theme || 'Unnamed Chain')}</span>
                <span class="coherence-badge">coherence: ${(chain.coherence || 0).toFixed(2)}</span>
                <span class="chain-size">(${chain.learnings?.length || 0} learnings, ${chain.missions?.length || 0} missions)</span>
            </div>
            <div class="chain-content" id="chain-content-${idx}">
                ${renderChainLearnings(chain.learnings || [])}
                ${chain.missions && chain.missions.length > 0 ? `
                    <div class="chain-missions" style="margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);">
                        <span style="color: var(--text-dim); font-size: 0.85em;">Missions: </span>
                        ${chain.missions.map(m => `<span class="mission-badge">${m}</span>`).join(' ')}
                    </div>
                ` : ''}
            </div>
        </div>
    `).join('');
}

function renderChainLearnings(learnings) {
    if (!learnings || learnings.length === 0) return '';
    return learnings.map((l, i) => `
        <div class="chain-learning" onclick="showLearningDetails('${l.learning_id}')" style="display: flex; align-items: center; padding: 8px; cursor: pointer;">
            <span class="chain-step" style="min-width: 24px; color: var(--accent);">${i + 1}.</span>
            <span class="learning-type-badge ${l.learning_type || ''}" style="margin-right: 8px;">${l.learning_type || 'unknown'}</span>
            <span class="learning-item-title" style="flex: 1;">${escapeHtml(l.title || 'Untitled')}</span>
            <span class="chain-mission-id" style="color: var(--text-dim); font-size: 0.8em;">${l.mission_id || ''}</span>
        </div>
    `).join('');
}

function toggleChain(idx) {
    const headers = document.querySelectorAll('.chain-header');
    const content = document.getElementById('chain-content-' + idx);
    if (headers[idx]) {
        headers[idx].classList.toggle('expanded');
    }
    if (content) {
        content.classList.toggle('expanded');
    }
}

// --- Clusters ---
async function loadClusters() {
    try {
        const data = await api('/api/knowledge-base/clusters?threshold=0.7');
        clustersData = data.clusters || [];
        renderClustersTree(clustersData);
    } catch (e) {
        console.error('Load clusters error:', e);
        document.getElementById('clusters-tree').innerHTML =
            '<div style="color: var(--red);">Error loading clusters</div>';
    }
}

function renderClustersTree(clusters) {
    const container = document.getElementById('clusters-tree');
    if (!clusters || clusters.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No clusters found. Try clicking "Show All" first.</div>';
        return;
    }

    container.innerHTML = clusters.map((c, idx) => `
        <div class="cluster-node">
            <div class="cluster-header" onclick="toggleCluster(${idx})">
                <span class="expand-icon">&#9654;</span>
                <span class="cluster-theme">${escapeHtml(c.theme || 'Unnamed')}</span>
                <span class="coherence-badge">coherence: ${(c.coherence || 0).toFixed(2)}</span>
                <span class="cluster-size">(${c.size || c.learning_ids?.length || 0} learnings)</span>
            </div>
            <div class="cluster-content" id="cluster-content-${idx}">
                ${renderClusterLearnings(c.learning_ids || [], c.learnings || [])}
            </div>
        </div>
    `).join('');
}

function renderClusterLearnings(learningIds, learnings) {
    if (learnings && learnings.length > 0) {
        return learnings.map(l => `
            <div class="learning-item" onclick="showLearningDetails('${l.learning_id}')">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
                <span class="learning-item-title">${escapeHtml(l.title || 'Untitled')}</span>
            </div>
        `).join('');
    }
    return learningIds.map(id => `
        <div class="learning-item" onclick="showLearningDetails('${id}')">
            <span style="color: var(--text-dim);">${id}</span>
        </div>
    `).join('');
}

function toggleCluster(idx) {
    const headers = document.querySelectorAll('.cluster-header');
    const content = document.getElementById('cluster-content-' + idx);
    if (headers[idx]) {
        headers[idx].classList.toggle('expanded');
    }
    if (content) {
        content.classList.toggle('expanded');
    }
}

// --- Duplicates ---
async function loadDuplicates() {
    try {
        const data = await api('/api/knowledge-base/duplicates?threshold=0.85');
        duplicatesData = data.duplicate_groups || [];
        document.getElementById('duplicates-count').textContent =
            duplicatesData.length > 0 ? `(${duplicatesData.length} groups)` : '';
        renderDuplicatesPanel(duplicatesData);
    } catch (e) {
        console.error('Load duplicates error:', e);
        document.getElementById('duplicates-panel').innerHTML =
            '<div style="color: var(--red);">Error loading duplicates</div>';
    }
}

function renderDuplicatesPanel(duplicates) {
    const panel = document.getElementById('duplicates-panel');
    if (!duplicates || duplicates.length === 0) {
        panel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No duplicates detected at 85% similarity threshold</div>';
        return;
    }

    panel.innerHTML = duplicates.map((group, idx) => {
        const learnings = group.learnings || [];
        const similarity = group.similarity || 0;
        const representative = group.representative;

        return `
            <div class="duplicate-group">
                <div class="duplicate-header">
                    <span>Similarity: ${(similarity * 100).toFixed(0)}%</span>
                    <button class="btn btn-sm" onclick="mergeDuplicates(${idx})">Merge Selected</button>
                </div>
                <div class="duplicate-items">
                    ${learnings.map(l => `
                        <label class="duplicate-item">
                            <input type="radio" name="dup-${idx}" value="${l.learning_id}"
                                   ${l.learning_id === representative ? 'checked' : ''}
                                   onchange="selectedMergeTarget[${idx}]='${l.learning_id}'">
                            <span>
                                <strong>${escapeHtml(l.title || 'Untitled')}</strong>
                                <div style="font-size: 0.8em; color: var(--text-dim);">${l.mission_id || ''}</div>
                            </span>
                        </label>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

async function mergeDuplicates(groupIdx) {
    const group = duplicatesData[groupIdx];
    if (!group) return;

    const learnings = group.learnings || [];
    const keepId = selectedMergeTarget[groupIdx] || group.representative;
    const mergeIds = learnings.filter(l => l.learning_id !== keepId).map(l => l.learning_id);

    if (mergeIds.length === 0) {
        showToast('Select a learning to keep first');
        return;
    }

    try {
        const result = await api('/api/knowledge-base/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds })
        });

        if (result.success) {
            showToast(`Merged ${result.merged} duplicate learnings`);
            loadDuplicates();
            loadAllLessons();
        } else {
            showToast('Merge failed: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function mergeAllDuplicates() {
    if (!confirm('Merge all duplicate groups? This will keep the representative learning from each group.')) return;

    try {
        const data = await api('/api/knowledge-base/duplicates?threshold=0.85');
        const groups = data.duplicate_groups || [];

        if (groups.length === 0) {
            showToast('No duplicates found');
            return;
        }

        let merged = 0;
        for (const group of groups) {
            const learnings = group.learnings || [];
            if (learnings.length < 2) continue;

            const keepId = group.representative || learnings[0].learning_id;
            const mergeIds = learnings.filter(l => l.learning_id !== keepId).map(l => l.learning_id);

            const result = await api('/api/knowledge-base/merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds })
            });
            if (result.success) merged += mergeIds.length;
        }

        showToast(`Merged ${merged} duplicate learnings`);
        loadAllLessons();
        if (currentLessonsSubtab === 'duplicates') loadDuplicates();
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

// --- Quick Actions ---
async function rebuildIndex() {
    showToast('Rebuilding index...');
    try {
        const result = await api('/api/knowledge-base/rebuild-index', { method: 'POST' });
        if (result.success) {
            showToast('Index rebuilt successfully');
        } else {
            showToast('Index rebuild failed: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

async function exportLearnings(format) {
    try {
        const data = await api('/api/knowledge-base/learnings?limit=10000');
        const learnings = data.learnings || [];

        if (learnings.length === 0) {
            showToast('No learnings to export');
            return;
        }

        const filename = 'learnings_export_' + new Date().toISOString().slice(0,10) + '.' + format;

        if (format === 'json') {
            downloadJSON(learnings, filename);
        } else if (format === 'csv') {
            downloadCSV(learnings, filename);
        }

        showToast('Exported ' + learnings.length + ' learnings');
    } catch (e) {
        showToast('Export error: ' + e.message);
    }
}

function downloadJSON(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    triggerDownload(blob, filename);
}

function downloadCSV(data, filename) {
    if (!data.length) return;

    const headers = ['learning_id', 'title', 'description', 'learning_type', 'problem_domain', 'outcome', 'mission_id'];
    const csvRows = [headers.join(',')];

    for (const item of data) {
        const row = headers.map(h => escapeCSV(item[h] || ''));
        csvRows.push(row.join(','));
    }

    const blob = new Blob([csvRows.join('\\n')], { type: 'text/csv' });
    triggerDownload(blob, filename);
}

function escapeCSV(str) {
    if (str === null || str === undefined) return '';
    str = String(str);
    if (str.includes(',') || str.includes('"') || str.includes('\\n')) {
        return '"' + str.replace(/"/g, '""') + '"';
    }
    return str;
}

function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// --- Analytics ---
function toggleAnalytics() {
    const panel = document.getElementById('analytics-panel');
    analyticsVisible = !analyticsVisible;
    panel.style.display = analyticsVisible ? 'grid' : 'none';

    if (analyticsVisible) {
        loadAnalyticsData();
    }
}

async function loadAnalyticsData() {
    try {
        const stats = await api('/api/knowledge-base/stats');
        renderDomainBarChart(stats.learnings_by_domain || {});
        renderTypeDonutChart(stats.learnings_by_type || {});
    } catch (e) {
        console.error('Analytics load error:', e);
    }
}

function renderDomainBarChart(domains) {
    const container = document.getElementById('domain-bar-chart');
    const entries = Object.entries(domains).sort((a, b) => b[1] - a[1]).slice(0, 5);
    const max = Math.max(...entries.map(e => e[1]), 1);

    if (entries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); text-align: center;">No data</div>';
        return;
    }

    container.innerHTML = entries.map(([domain, count], i) => `
        <div class="bar-row">
            <span class="bar-label">${escapeHtml(domain.replace(/_/g, ' '))}</span>
            <div class="bar-track">
                <div class="bar-fill domain-${i % 5}" style="width: ${(count / max * 100).toFixed(1)}%"></div>
            </div>
            <span class="bar-value">${count}</span>
        </div>
    `).join('');
}

function renderTypeDonutChart(types) {
    const svg = document.querySelector('#type-donut-chart .donut-chart-svg');
    const legend = document.getElementById('type-legend');
    const totalEl = document.getElementById('donut-total');

    const entries = Object.entries(types);
    const total = entries.reduce((sum, [, count]) => sum + count, 0);
    totalEl.textContent = total;

    if (total === 0) {
        svg.innerHTML = '';
        legend.innerHTML = '<div style="color: var(--text-dim); text-align: center;">No data</div>';
        return;
    }

    const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff'];
    const circumference = 2 * Math.PI * 35;
    let offset = 0;

    svg.innerHTML = entries.map(([type, count], i) => {
        const percent = count / total;
        const dash = percent * circumference;
        const segment = `
            <circle class="donut-segment"
                    cx="50" cy="50" r="35"
                    stroke="${colors[i % colors.length]}"
                    stroke-dasharray="${dash} ${circumference - dash}"
                    stroke-dashoffset="${-offset}"
            />
        `;
        offset += dash;
        return segment;
    }).join('');

    legend.innerHTML = '<div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;">' +
        entries.map(([type, count], i) => `
            <span style="display: flex; align-items: center; gap: 5px; font-size: 0.8em;">
                <span style="width: 12px; height: 12px; border-radius: 2px; background: ${colors[i % colors.length]};"></span>
                ${type}: ${count}
            </span>
        `).join('') + '</div>';
}

// --- Batch Delete ---
function toggleBatchDeleteMode() {
    batchDeleteMode = !batchDeleteMode;
    selectedLearnings.clear();
    updateBatchUI();

    if (batchDeleteMode) {
        document.getElementById('batch-action-bar').classList.add('show');
        showToast('Batch mode: Click checkboxes to select learnings');
        renderLessonsListWithCheckboxes();
    } else {
        exitBatchMode();
    }
}

function exitBatchMode() {
    batchDeleteMode = false;
    selectedLearnings.clear();
    document.getElementById('batch-action-bar').classList.remove('show');
    renderLessonsList(lessonsData);
}

function toggleLearningSelection(learningId, event) {
    if (event) event.stopPropagation();
    if (selectedLearnings.has(learningId)) {
        selectedLearnings.delete(learningId);
    } else {
        selectedLearnings.add(learningId);
    }
    updateBatchUI();
}

function selectAllLearnings() {
    lessonsData.forEach(l => selectedLearnings.add(l.learning_id));
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

function clearSelection() {
    selectedLearnings.clear();
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

function updateBatchUI() {
    document.getElementById('selected-count').textContent = selectedLearnings.size;
}

function renderLessonsListWithCheckboxes() {
    const list = document.getElementById('lessons-list');
    if (!lessonsData || lessonsData.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learnings found</div>';
        return;
    }

    const html = lessonsData.map(l => `
        <div class="learning-item ${l.learning_id === selectedLearningId ? 'active' : ''}"
             onclick="showLearningDetails('${l.learning_id}')">
            <input type="checkbox" class="batch-checkbox"
                   ${selectedLearnings.has(l.learning_id) ? 'checked' : ''}
                   onclick="toggleLearningSelection('${l.learning_id}', event)"
                   style="margin-right: 10px;">
            <div class="learning-item-title">${escapeHtml(l.title || 'Untitled')}</div>
            <div class="learning-item-meta">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
            </div>
        </div>
    `).join('');

    list.innerHTML = html;
}

function showBatchDeleteConfirm() {
    if (selectedLearnings.size === 0) {
        showToast('No learnings selected');
        return;
    }

    const selected = Array.from(selectedLearnings);
    const learnings = lessonsData.filter(l => selected.includes(l.learning_id));

    document.getElementById('delete-count').textContent = learnings.length;

    const listEl = document.getElementById('delete-confirm-list');
    listEl.innerHTML = learnings.slice(0, 20).map(l => `
        <div class="delete-confirm-item">
            <strong>${escapeHtml(l.title || 'Untitled')}</strong>
            <div style="font-size: 0.85em; color: var(--text-dim);">${escapeHtml((l.description || '').substring(0, 100))}</div>
        </div>
    `).join('') + (learnings.length > 20 ? '<div style="color: var(--text-dim); padding: 8px;">...and ' + (learnings.length - 20) + ' more</div>' : '');

    document.getElementById('batch-delete-modal').classList.add('show');
}

function closeBatchDeleteModal() {
    document.getElementById('batch-delete-modal').classList.remove('show');
}

async function confirmBatchDelete() {
    closeBatchDeleteModal();

    const ids = Array.from(selectedLearnings);
    if (ids.length === 0) return;

    try {
        const result = await api('/api/knowledge-base/batch-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ learning_ids: ids })
        });

        if (result.error) {
            showToast('Delete failed: ' + result.error);
            return;
        }

        showToast('Deleted ' + (result.deleted || ids.length) + ' learnings');
        selectedLearnings.clear();
        exitBatchMode();
        loadAllLessons();
    } catch (e) {
        showToast('Delete error: ' + e.message);
    }
}

// =====================================================================
// CRASH RECOVERY FUNCTIONS
// =====================================================================

let recoveryData = null;

async function checkForRecovery() {
    try {
        const data = await api('/api/recovery/check');
        recoveryData = data;

        if (data.recovery_available) {
            const banner = document.getElementById('recovery-banner');
            document.getElementById('recovery-banner-stage').textContent = data.stage || 'Unknown';
            document.getElementById('recovery-banner-mission').textContent = data.mission_id || 'Unknown';
            banner.classList.add('show');
        }
    } catch (e) {
        console.error('Recovery check error:', e);
    }
}

function showRecoveryModal() {
    if (!recoveryData || !recoveryData.recovery_available) return;

    document.getElementById('recovery-modal-mission-id').textContent = recoveryData.mission_id || '-';
    document.getElementById('recovery-modal-stage').textContent = recoveryData.stage || '-';
    document.getElementById('recovery-modal-iteration').textContent = recoveryData.iteration || '-';
    document.getElementById('recovery-modal-cycle').textContent = recoveryData.cycle || '-';
    document.getElementById('recovery-modal-hint').textContent = recoveryData.recovery_hint || 'No hint available';

    // Calculate time since crash
    if (recoveryData.timestamp) {
        const crashTime = new Date(recoveryData.timestamp);
        const now = new Date();
        const diffMs = now - crashTime;
        const diffMins = Math.round(diffMs / 60000);
        document.getElementById('recovery-modal-time').textContent = diffMins + ' minutes ago';
    } else {
        document.getElementById('recovery-modal-time').textContent = 'Unknown';
    }

    // Files
    const filesList = document.getElementById('recovery-modal-files');
    if (recoveryData.files_created && recoveryData.files_created.length > 0) {
        filesList.innerHTML = recoveryData.files_created.map(f =>
            `<li style="padding: 2px 0;">${f}</li>`
        ).join('');
        document.getElementById('recovery-modal-files-section').style.display = 'block';
    } else {
        document.getElementById('recovery-modal-files-section').style.display = 'none';
    }

    document.getElementById('recovery-modal').classList.add('show');
}

function closeRecoveryModal() {
    document.getElementById('recovery-modal').classList.remove('show');
}

async function dismissRecovery() {
    try {
        await api('/api/recovery/dismiss', { method: 'POST' });
        document.getElementById('recovery-banner').classList.remove('show');
        closeRecoveryModal();
        showToast('Recovery dismissed - starting fresh');
    } catch (e) {
        console.error('Dismiss recovery error:', e);
    }
}

function dismissRecoveryFromModal() {
    dismissRecovery();
}

async function applyRecovery() {
    // Just close modal - the rd_engine will handle recovery on next start
    closeRecoveryModal();
    document.getElementById('recovery-banner').classList.remove('show');
    showToast('Resume mode enabled - start mission to continue');
}

// =====================================================================
// DECISION GRAPH FUNCTIONS
// =====================================================================

let decisionGraphData = null;
let decisionGraphNodes = [];

async function refreshDecisionGraph() {
    try {
        const data = await api('/api/decision-graph/current');
        decisionGraphData = data;

        // Update stats
        document.getElementById('decision-invocation-count').textContent = data.stats?.total || 0;
        document.getElementById('decision-error-count').textContent = data.stats?.errors || 0;

        // Render graph
        renderDecisionGraph(data);
    } catch (e) {
        console.error('Decision graph error:', e);
    }
}

function renderDecisionGraph(data) {
    const canvas = document.getElementById('decision-graph-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const w = canvas.width;
    const h = canvas.height;

    // Clear
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    const nodes = data.nodes || [];
    const edges = data.edges || [];

    if (nodes.length === 0) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('No tool invocations yet', w / 2, h / 2);
        return;
    }

    // Position nodes in a grid/flow layout
    const padding = 20;
    const nodeRadius = 8;
    const nodesPerRow = Math.ceil(Math.sqrt(nodes.length));
    const spacingX = (w - padding * 2) / (nodesPerRow + 1);
    const spacingY = (h - padding * 2) / (Math.ceil(nodes.length / nodesPerRow) + 1);

    decisionGraphNodes = nodes.map((node, i) => {
        const row = Math.floor(i / nodesPerRow);
        const col = i % nodesPerRow;
        return {
            ...node,
            x: padding + (col + 1) * spacingX,
            y: padding + (row + 1) * spacingY
        };
    });

    // Draw edges
    ctx.strokeStyle = '#30363d';
    ctx.lineWidth = 1;
    edges.forEach(edge => {
        const source = decisionGraphNodes.find(n => n.id === edge.source);
        const target = decisionGraphNodes.find(n => n.id === edge.target);
        if (source && target) {
            ctx.beginPath();
            ctx.moveTo(source.x, source.y);
            ctx.lineTo(target.x, target.y);
            ctx.stroke();
        }
    });

    // Draw nodes
    decisionGraphNodes.forEach(node => {
        // Color based on status/tool
        let color = '#3fb950';  // success
        if (node.has_error || node.status === 'error') {
            color = '#f85149';  // error
        } else if (node.tool_name === 'Read' || node.tool_name === 'Glob' || node.tool_name === 'Grep') {
            color = '#58a6ff';  // read
        } else if (node.tool_name === 'Write' || node.tool_name === 'Edit') {
            color = '#a371f7';  // write
        } else if (node.tool_name === 'Bash') {
            color = '#d29922';  // bash
        }

        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, nodeRadius, 0, Math.PI * 2);
        ctx.fill();
    });

    // Click handler
    canvas.onclick = function(e) {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        // Find clicked node
        const clicked = decisionGraphNodes.find(node => {
            const dx = node.x - x;
            const dy = node.y - y;
            return Math.sqrt(dx*dx + dy*dy) <= nodeRadius + 4;
        });

        if (clicked) {
            showDecisionNodeDetails(clicked);
        }
    };
}

function showDecisionNodeDetails(node) {
    const details = document.getElementById('decision-node-details');

    let errorHtml = '';
    if (node.has_error || node.status === 'error') {
        errorHtml = `<div class="node-error">Error: ${node.error_message || 'Unknown error'}</div>`;
    }

    details.innerHTML = `
        <div class="node-title">${node.tool_name} (#${node.sequence})</div>
        <div class="node-meta">${node.stage} | ${node.duration_ms || 0}ms</div>
        ${errorHtml}
    `;
}

// =====================================================================
// TAB SWITCH HOOKS
// =====================================================================

// Load analytics when tab is selected
function onTabSwitch(tabName) {
    if (tabName === 'analytics') {
        refreshFullAnalytics();
    } else if (tabName === 'lessons') {
        loadAllLessons();
    }
}

// Hook into tab switch
const originalSwitchTab = switchTab;
switchTab = function(tabName) {
    originalSwitchTab(tabName);
    onTabSwitch(tabName);
};

// =====================================================================
// BUG BOUNTY TAB FUNCTIONS
// =====================================================================

async function refreshBugBountyData() {
    try {
        const overview = await api('/api/bugbounty/overview');
        if (overview.success) {
            document.getElementById('bb-program-count').textContent = overview.programs.total_programs || 0;
            document.getElementById('bb-category-count').textContent = overview.categories.total || 0;
            document.getElementById('bb-mission-count').textContent = overview.research.total_missions || 0;
            document.getElementById('bb-programs-badge').textContent = overview.programs.total_programs || 0;
        }

        // Load categories
        const cats = await api('/api/bugbounty/categories');
        if (cats.success) {
            renderBugBountyCategories(cats.categories);
        }

        // Load missions
        const missions = await api('/api/bugbounty/missions');
        if (missions.success) {
            renderBugBountyMissions(missions.missions || []);
        }

    } catch (e) {
        console.error('Error refreshing bug bounty data:', e);
    }
}

async function fetchBugBountyPrograms() {
    try {
        document.getElementById('bb-programs-list').innerHTML = '<div style="color: var(--text-dim);">Fetching programs...</div>';

        const result = await api('/api/bugbounty/programs/fetch', 'POST', {force_refresh: false});

        if (result.success) {
            showToast(`Fetched ${result.total_programs} programs`);
            searchBugBountyPrograms();
            refreshBugBountyData();
        } else {
            showToast('Error: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('Error fetching programs: ' + e.message, 'error');
    }
}

async function searchBugBountyPrograms() {
    try {
        const query = document.getElementById('bb-program-search').value;
        const platform = document.getElementById('bb-platform-filter').value;
        const hasBounty = document.getElementById('bb-bounty-filter').checked;

        let url = `/api/bugbounty/programs/search?limit=100`;
        if (query) url += `&q=${encodeURIComponent(query)}`;
        if (platform) url += `&platform=${platform}`;
        if (hasBounty) url += `&bounties=true`;
        url += `&has_web=true`;

        const result = await api(url);
        if (result.success) {
            renderBugBountyPrograms(result.programs);
        }
    } catch (e) {
        console.error('Error searching programs:', e);
    }
}

function renderBugBountyPrograms(programs) {
    const container = document.getElementById('bb-programs-list');

    if (!programs || programs.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No programs found</div>';
        return;
    }

    const html = programs.map(p => `
        <div class="glassbox-agent-item" onclick="showProgramDetails('${p.id}')" style="cursor: pointer;">
            <div class="glassbox-agent-header">
                <span class="glassbox-agent-type" style="background: ${p.platform === 'hackerone' ? 'var(--purple)' : 'var(--blue)'};">
                    ${p.platform}
                </span>
                <span class="glassbox-agent-name">${p.name}</span>
            </div>
            <div class="glassbox-agent-meta">
                ${p.offers_bounties ? '💰 ' : ''}${p.web_targets} web targets | ${p.in_scope_count} total
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

function renderBugBountyCategories(categories) {
    const container = document.getElementById('bb-categories-list');

    const sevColors = {
        'critical': 'var(--red)',
        'high': 'var(--orange)',
        'medium': 'var(--yellow)',
        'low': 'var(--blue)',
        'informational': 'var(--text-dim)'
    };

    // Sort: OWASP first, then by severity
    const sorted = Object.entries(categories).sort((a, b) => {
        if (a[0].startsWith('A') && !b[0].startsWith('A')) return -1;
        if (!a[0].startsWith('A') && b[0].startsWith('A')) return 1;
        return 0;
    });

    const html = sorted.map(([id, cat]) => `
        <div class="glassbox-agent-item" onclick="showCategoryDetails('${id}')" style="cursor: pointer;">
            <div class="glassbox-agent-header">
                <span class="glassbox-agent-type" style="background: ${sevColors[cat.severity] || 'var(--text-dim)'};">
                    ${cat.severity}
                </span>
                <span class="glassbox-agent-name">${id}</span>
            </div>
            <div class="glassbox-agent-meta">
                ${cat.name}
                <br>
                <span style="font-size: 0.75em;">
                    ${cat.cwe_count} CWEs | ${cat.pattern_count} patterns | $${cat.bounty_range[0]}-${cat.bounty_range[1]}
                </span>
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

function renderBugBountyMissions(missions) {
    const container = document.getElementById('bb-missions-list');

    if (!missions || missions.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No missions yet. Create one by scanning code or selecting a program.</div>';
        return;
    }

    const html = missions.map(m => `
        <div class="glassbox-agent-item" style="padding: 8px;">
            <div class="glassbox-agent-header">
                <span class="glassbox-agent-type" style="background: ${m.status === 'pending' ? 'var(--yellow)' : 'var(--green)'};">
                    ${m.status}
                </span>
                <span class="glassbox-agent-name" style="font-size: 0.85em;">${m.title}</span>
            </div>
            <div class="glassbox-agent-meta">
                ${m.target_type} | ${m.cycle_budget} cycles
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

async function runQuickScan() {
    try {
        const code = document.getElementById('bb-scan-code').value;
        const language = document.getElementById('bb-scan-lang').value;

        if (!code.trim()) {
            showToast('Please enter code to scan', 'error');
            return;
        }

        const result = await api('/api/bugbounty/scan/quick', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code, language, filename: 'snippet.' + language})
        });

        if (result.success) {
            renderScanResults(result);
        } else {
            showToast('Scan error: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function renderScanResults(result) {
    const container = document.getElementById('bb-scan-results');

    if (!result.findings || result.findings.length === 0) {
        container.innerHTML = '<div style="color: var(--green); font-size: 0.85em;">✓ No vulnerabilities found</div>';
        return;
    }

    const sevColors = {
        'critical': 'var(--red)',
        'high': 'var(--orange)',
        'medium': 'var(--yellow)',
        'low': 'var(--blue)'
    };

    const html = `
        <div style="margin-bottom: 10px; padding: 8px; background: var(--bg); border-radius: 4px;">
            <strong>Found ${result.total} potential issue(s)</strong>
            <div style="font-size: 0.8em; color: var(--text-dim); margin-top: 3px;">
                ${Object.entries(result.by_severity).filter(([k,v]) => v > 0).map(([k,v]) => `${k}: ${v}`).join(' | ')}
            </div>
        </div>
        ${result.findings.slice(0, 5).map(f => `
            <div style="padding: 8px; border-left: 3px solid ${sevColors[f.severity] || 'var(--border)'}; margin-bottom: 5px; background: var(--bg); border-radius: 0 4px 4px 0;">
                <div style="font-weight: 500; font-size: 0.85em;">${f.category_name}</div>
                <div style="font-size: 0.75em; color: var(--text-dim);">Line ${f.location.line}: ${f.detection.pattern_description}</div>
                <div style="font-family: monospace; font-size: 0.75em; color: var(--text); margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${escapeHtml(f.matched_line.substring(0, 60))}
                </div>
            </div>
        `).join('')}
    `;

    container.innerHTML = html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function showProgramDetails(programId) {
    try {
        const result = await api(`/api/bugbounty/programs/${programId}`);
        if (result.success) {
            const p = result.program;
            alert(`Program: ${p.name}\\nPlatform: ${p.platform}\\nURL: ${p.url}\\nBounties: ${p.offers_bounties}\\nWeb Targets: ${p.web_target_count}\\nDomains: ${p.domains.slice(0,5).join(', ')}`);
        }
    } catch (e) {
        console.error(e);
    }
}

async function showCategoryDetails(categoryId) {
    try {
        const result = await api(`/api/bugbounty/categories/${categoryId}`);
        if (result.success) {
            const c = result.category;
            alert(`Category: ${c.name}\\nSeverity: ${c.severity}\\nCWEs: ${c.cwe_ids.length}\\n\\nDescription:\\n${c.description.substring(0, 300)}...\\n\\nRemediation:\\n${c.remediation.substring(0, 300)}...`);
        }
    } catch (e) {
        console.error(e);
    }
}

// =====================================================================
// NARRATIVE TAB FUNCTIONS (Phase 4 Enhanced)
// =====================================================================

let selectedNarrativeConcept = null;
let currentNarrativeSession = null;
let narrativeWebSocketInitialized = false;

// Initialize WebSocket listeners for narrative_progress events
function initNarrativeWebSocket() {
    if (narrativeWebSocketInitialized || typeof socket === 'undefined') return;

    socket.on('narrative_progress', (data) => {
        console.log('Narrative progress update:', data);

        // Update UI based on event type
        if (data.event_type === 'progress_update') {
            // Refresh session details if viewing this session
            if (currentNarrativeSession && currentNarrativeSession.id === data.session_id) {
                viewNarrativeSession(data.session_id);
            }
            // Refresh session list
            refreshNarrativeSessions();

            // Show toast notification
            showToast(`Chapter ${data.data.chapter}, Agent ${data.data.agent}: ${data.data.status}`);
        }
        else if (data.event_type === 'session_started') {
            refreshNarrativeData();
            showToast(`New session started: ${data.data.story_title}`);
        }
        else if (data.event_type === 'session_completed') {
            refreshNarrativeData();
            showToast(`Session completed: ${data.data.story_title}`, 'success');
        }
        else if (data.event_type === 'chapter_complete') {
            if (currentNarrativeSession && currentNarrativeSession.id === data.session_id) {
                viewNarrativeSession(data.session_id);
            }
            showToast(`Chapter ${data.data.chapter} complete!`, 'success');
        }
    });

    narrativeWebSocketInitialized = true;
    console.log('Narrative WebSocket listeners initialized');
}

async function refreshNarrativeSessions() {
    try {
        const sessions = await api('/api/narrative/sessions?status=in_progress');
        if (sessions.success) {
            renderNarrativeSessions(sessions.sessions);
        }
    } catch (e) {
        console.error('Error refreshing sessions:', e);
    }
}

async function refreshNarrativeData() {
    try {
        // Initialize WebSocket if not done
        initNarrativeWebSocket();

        // Load overview stats
        const overview = await api('/api/narrative/overview');
        if (overview.success) {
            document.getElementById('narr-concept-count').textContent = overview.concepts.total || 100;
            document.getElementById('narr-available-count').textContent = overview.concepts.available || 0;
            document.getElementById('narr-active-count').textContent = overview.sessions.active_sessions || 0;
            document.getElementById('narr-sessions-badge').textContent = overview.sessions.active_sessions || 0;
        }

        // Load concepts
        searchNarrativeConcepts();

        // Load sessions
        const sessions = await api('/api/narrative/sessions?status=in_progress');
        if (sessions.success) {
            renderNarrativeSessions(sessions.sessions);
        }

    } catch (e) {
        console.error('Error refreshing narrative data:', e);
    }
}

async function searchNarrativeConcepts() {
    try {
        const query = document.getElementById('narr-concept-search').value;
        const genre = document.getElementById('narr-genre-filter').value;
        const availableOnly = document.getElementById('narr-available-filter').checked;

        let url = '/api/narrative/concepts?';
        if (query) url += `search=${encodeURIComponent(query)}&`;
        if (genre) url += `genre=${encodeURIComponent(genre)}&`;
        if (availableOnly) url += 'available=true&';

        const result = await api(url);
        if (result.success) {
            renderNarrativeConcepts(result.concepts);
            document.getElementById('narr-concepts-badge').textContent = result.returned;
        }
    } catch (e) {
        console.error('Error searching concepts:', e);
    }
}

function renderNarrativeConcepts(concepts) {
    const container = document.getElementById('narr-concepts-list');

    if (!concepts || concepts.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); grid-column: 1 / -1;">No stories found</div>';
        return;
    }

    const genreColors = {
        'ROMANCE': '#ff69b4',
        'MYSTERY & DETECTIVE': 'var(--purple)',
        'SCIENCE FICTION': 'var(--blue)',
        'FANTASY': 'var(--green)',
        'THRILLER & SUSPENSE': 'var(--orange)',
        'HORROR': 'var(--red)',
        'HISTORICAL FICTION': '#8b4513',
        'CONTEMPORARY/LITERARY FICTION': '#008080'
    };

    const html = concepts.map(c => `
        <div class="glassbox-agent-item" onclick="selectNarrativeConcept(${c.number})"
             style="cursor: pointer; opacity: ${c.available ? 1 : 0.5}; border-left: 3px solid ${genreColors[c.genre] || 'var(--border)'};">
            <div class="glassbox-agent-header">
                <span class="glassbox-agent-type" style="background: ${genreColors[c.genre] || 'var(--text-dim)'}; font-size: 0.65em;">
                    #${c.number}
                </span>
                <span class="glassbox-agent-name" style="font-size: 0.85em;">${c.title}</span>
            </div>
            <div class="glassbox-agent-meta" style="font-size: 0.7em;">
                ${c.genre.toLowerCase()} ${c.available ? '' : '(completed)'}
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

function renderNarrativeSessions(sessions) {
    const container = document.getElementById('narr-sessions-list');

    if (!sessions || sessions.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim);">No active sessions</div>';
        return;
    }

    const html = sessions.map(s => `
        <div class="glassbox-agent-item" onclick="viewNarrativeSession('${s.id}')" style="cursor: pointer;">
            <div class="glassbox-agent-header">
                <span class="glassbox-agent-type" style="background: var(--green);">
                    #${s.story_number}
                </span>
                <span class="glassbox-agent-name">${s.story_title}</span>
            </div>
            <div class="glassbox-agent-meta">
                <div style="width: 100%; background: var(--bg); border-radius: 3px; height: 6px; margin-bottom: 5px;">
                    <div style="width: ${s.progress_percentage || 0}%; background: var(--accent); height: 100%; border-radius: 3px;"></div>
                </div>
                Chapter ${s.current_chapter}/${s.total_chapters} | Agent ${s.current_agent} | ${(s.progress_percentage || 0).toFixed(1)}%
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

async function selectNarrativeConcept(number) {
    try {
        const result = await api(`/api/narrative/concepts/${number}`);
        if (result.success) {
            selectedNarrativeConcept = result.concept;

            // Update modal content
            const modalContent = document.getElementById('narr-modal-content');
            modalContent.innerHTML = `
                <div style="margin-bottom: 15px;">
                    <strong>#${result.concept.number}: ${result.concept.title}</strong>
                    <div style="color: var(--text-dim); font-size: 0.9em; margin-top: 5px;">
                        ${result.concept.genre}
                    </div>
                    <div style="margin-top: 10px; font-size: 0.9em;">
                        ${result.concept.logline}
                    </div>
                </div>
                ${result.has_project ? `<div style="color: var(--orange); font-size: 0.85em;">Note: Project folder exists for this story.</div>` : ''}
                <div style="margin-top: 15px;">
                    <label>Total Chapters:</label>
                    <input type="number" id="narr-total-chapters" value="33" min="1" max="100"
                           style="width: 80px; padding: 5px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; color: var(--text);">
                </div>
            `;

            document.getElementById('narr-create-btn').disabled = !result.concept.available;
            showNarrativeModal();
        }
    } catch (e) {
        console.error('Error selecting concept:', e);
    }
}

async function viewNarrativeSession(sessionId) {
    try {
        const result = await api(`/api/narrative/session/${sessionId}`);
        if (result.success) {
            currentNarrativeSession = result.session;
            renderNarrativeSessionDetails(result.session);
        }
    } catch (e) {
        console.error('Error loading session:', e);
    }
}

function renderNarrativeSessionDetails(session) {
    const container = document.getElementById('narr-session-details');

    const agentNames = ['Author', 'Editor', 'Cadence', 'Punct', 'Accent'];
    const statusColors = {
        'pending': 'var(--text-dim)',
        'running': 'var(--yellow)',
        'complete': 'var(--green)',
        'error': 'var(--red)'
    };

    // Show first 10 chapters
    const chaptersHtml = (session.chapter_progress || []).slice(0, 10).map(cp => `
        <div style="display: flex; align-items: center; gap: 5px; margin-bottom: 5px;">
            <span style="width: 40px; font-size: 0.8em;">Ch ${cp.chapter_number}</span>
            ${[1,2,3,4,5].map(i => `
                <span style="width: 10px; height: 10px; border-radius: 50%; background: ${statusColors[cp['agent_' + i + '_status']] || 'var(--border)'};"
                      title="${agentNames[i-1]}: ${cp['agent_' + i + '_status']}"></span>
            `).join('')}
        </div>
    `).join('');

    // Calculate accent checklist (Agent 5 status across all chapters)
    const chapterProgress = session.chapter_progress || [];
    const accentComplete = chapterProgress.filter(cp => cp.agent_5_status === 'complete').length;
    const accentChecklist = chapterProgress.slice(0, 33).map((cp, idx) => {
        const isComplete = cp.agent_5_status === 'complete';
        return `<span style="display: inline-block; width: 16px; height: 16px; line-height: 16px; text-align: center; font-size: 10px; background: ${isComplete ? 'var(--green)' : 'var(--bg)'}; border: 1px solid var(--border); border-radius: 3px; margin: 1px;" title="Ch ${idx + 1}: ${isComplete ? 'Accent applied' : 'Pending'}">${idx + 1}</span>`;
    }).join('');

    container.innerHTML = `
        <div style="margin-bottom: 15px;">
            <strong>#${session.story_number}: ${session.story_title}</strong>
            <div style="font-size: 0.8em; color: var(--text-dim); margin-top: 5px;">
                Status: ${session.status} | Created: ${new Date(session.created_at).toLocaleDateString()}
            </div>
        </div>

        <div style="margin-bottom: 15px;">
            <div style="font-size: 0.9em; margin-bottom: 5px;">Progress: ${(session.progress_percentage || 0).toFixed(1)}%</div>
            <div style="width: 100%; background: var(--bg); border-radius: 4px; height: 10px;">
                <div style="width: ${session.progress_percentage || 0}%; background: var(--accent); height: 100%; border-radius: 4px;"></div>
            </div>
            <div style="font-size: 0.8em; color: var(--text-dim); margin-top: 5px;">
                ${session.chapters_complete || 0} of ${session.total_chapters} chapters complete
            </div>
        </div>

        <div style="margin-bottom: 15px;">
            <div style="font-size: 0.9em; margin-bottom: 5px;">Current Position</div>
            <div style="font-size: 0.85em;">
                Chapter ${session.current_chapter} / Agent ${session.current_agent} (${agentNames[session.current_agent - 1] || 'Unknown'})
            </div>
        </div>

        <div style="margin-bottom: 15px;">
            <div style="font-size: 0.9em; margin-bottom: 5px;">Chapter Progress (first 10)</div>
            ${chaptersHtml}
            ${(session.chapter_progress || []).length > 10 ? `<div style="font-size: 0.75em; color: var(--text-dim);">...and ${session.total_chapters - 10} more</div>` : ''}
        </div>

        <!-- Accent Checklist Visualization -->
        <div style="margin-bottom: 15px;">
            <div style="font-size: 0.9em; margin-bottom: 5px;">Accent Checklist (${accentComplete}/${session.total_chapters})</div>
            <div style="display: flex; flex-wrap: wrap; gap: 1px; max-width: 280px;">
                ${accentChecklist}
            </div>
        </div>

        <!-- Action Buttons -->
        <div style="display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 10px;">
            <button class="btn" onclick="pauseNarrativeSession('${session.id}')" ${session.status !== 'in_progress' ? 'disabled' : ''}>Pause</button>
            <button class="btn primary" onclick="resumeNarrativeSession('${session.id}')" ${session.status === 'in_progress' ? 'disabled' : ''}>Resume</button>
            <button class="btn danger" onclick="deleteNarrativeSession('${session.id}')">Delete</button>
        </div>

        <!-- Phase 4 Buttons: Launch Mission & Sync -->
        <div style="display: flex; gap: 5px; flex-wrap: wrap; padding-top: 10px; border-top: 1px solid var(--border);">
            <button class="btn primary" onclick="launchNarrativeMission('${session.id}')" style="background: var(--green);">
                Launch Mission
            </button>
            <button class="btn" onclick="syncFromFileSystem('${session.id}')">
                Sync from FS
            </button>
        </div>
    `;
}

// Phase 4: Launch Mission - generates mission config and starts AtlasForge
async function launchNarrativeMission(sessionId) {
    try {
        showToast('Generating mission...', 'info');

        // Get session details
        const sessionResult = await api(`/api/narrative/session/${sessionId}`);
        if (!sessionResult.success) {
            showToast('Error: ' + sessionResult.error, 'error');
            return;
        }
        const session = sessionResult.session;

        // Generate mission configuration
        const missionResult = await api('/api/narrative/workflow/generate-mission', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                story_number: session.story_number,
                story_title: session.story_title,
                folder_path: session.folder_path || '',
                resume: session.current_chapter > 1 || session.current_agent > 1,
                resume_chapter: session.current_chapter,
                resume_agent: session.current_agent,
                total_chapters: session.total_chapters
            })
        });

        if (!missionResult.success) {
            showToast('Error generating mission: ' + missionResult.error, 'error');
            return;
        }

        // Launch mission via AtlasForge API
        const missionConfig = missionResult.mission;
        const launchResult = await api('/api/mission', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                mission: missionConfig.problem_statement,
                cycle_budget: 3  // Default to 3 cycles for narrative missions
            })
        });

        if (launchResult.success) {
            showToast('Mission launched! Starting R&D engine...', 'success');

            // Update session status to in_progress
            await api(`/api/narrative/session/${sessionId}/status`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status: 'in_progress'})
            });

            // Switch to Mission tab
            switchTab('mission');
        } else {
            showToast('Error launching mission: ' + launchResult.message, 'error');
        }
    } catch (e) {
        console.error('Error launching mission:', e);
        showToast('Error: ' + e.message, 'error');
    }
}

// Phase 4: Sync from File System - infers resume point from actual files
async function syncFromFileSystem(sessionId) {
    try {
        showToast('Syncing from file system...', 'info');

        // Get session details first
        const sessionResult = await api(`/api/narrative/session/${sessionId}`);
        if (!sessionResult.success) {
            showToast('Error: ' + sessionResult.error, 'error');
            return;
        }
        const session = sessionResult.session;

        if (!session.folder_path) {
            showToast('No folder path set for this session', 'error');
            return;
        }

        // Call infer-resume endpoint
        const inferResult = await api('/api/narrative/workflow/infer-resume', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                folder_path: session.folder_path
            })
        });

        if (!inferResult.success) {
            showToast('Error inferring state: ' + inferResult.error, 'error');
            return;
        }

        const resumeContext = inferResult.resume_context;

        // Update session progress based on inferred state
        if (resumeContext.completed_chapters) {
            for (let ch = 1; ch <= resumeContext.completed_chapters; ch++) {
                for (let agent = 1; agent <= 5; agent++) {
                    await api(`/api/narrative/session/${sessionId}/progress`, {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            chapter: ch,
                            agent: agent,
                            status: 'complete'
                        })
                    });
                }
            }
        }

        showToast(`Synced! Resume at Chapter ${resumeContext.resume_chapter}, Agent ${resumeContext.resume_agent}`, 'success');

        // Refresh session view
        viewNarrativeSession(sessionId);

    } catch (e) {
        console.error('Error syncing from file system:', e);
        showToast('Error: ' + e.message, 'error');
    }
}

async function createNarrativeSession() {
    if (!selectedNarrativeConcept) return;

    try {
        const totalChapters = parseInt(document.getElementById('narr-total-chapters').value) || 33;

        const result = await api('/api/narrative/session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                story_number: selectedNarrativeConcept.number,
                story_title: selectedNarrativeConcept.title,
                total_chapters: totalChapters
            })
        });

        if (result.success) {
            showToast(`Session created for "${selectedNarrativeConcept.title}"`);
            hideNarrativeModal();
            refreshNarrativeData();
            viewNarrativeSession(result.session_id);
        } else {
            showToast('Error: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('Error creating session: ' + e.message, 'error');
    }
}

async function pauseNarrativeSession(sessionId) {
    try {
        const result = await api(`/api/narrative/session/${sessionId}/status`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({status: 'paused'})
        });

        if (result.success) {
            showToast('Session paused');
            refreshNarrativeData();
            viewNarrativeSession(sessionId);
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function resumeNarrativeSession(sessionId) {
    try {
        const result = await api(`/api/narrative/session/${sessionId}/resume`);

        if (result.success) {
            showToast(`Resuming from Chapter ${result.resume.resume_chapter}, Agent ${result.resume.resume_agent}`);
            refreshNarrativeData();
            viewNarrativeSession(sessionId);
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function deleteNarrativeSession(sessionId) {
    if (!confirm('Delete this session? This cannot be undone.')) return;

    try {
        const result = await api(`/api/narrative/session/${sessionId}`, {
            method: 'DELETE'
        });

        if (result.success) {
            showToast('Session deleted');
            refreshNarrativeData();
            document.getElementById('narr-session-details').innerHTML = '<div style="color: var(--text-dim);">Select a session to view details</div>';
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function showNarrativeModal() {
    document.getElementById('narr-modal-overlay').style.display = 'flex';
}

function hideNarrativeModal() {
    document.getElementById('narr-modal-overlay').style.display = 'none';
    selectedNarrativeConcept = null;
}

function showCreateSessionModal() {
    selectedNarrativeConcept = null;
    document.getElementById('narr-modal-content').innerHTML = '<p style="color: var(--text-dim);">Click a story concept from the grid to select it.</p>';
    document.getElementById('narr-create-btn').disabled = true;
    showNarrativeModal();
}

// Initialize narrative tab on switch
function initNarrativeTab() {
    refreshNarrativeData();
}
