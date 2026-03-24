/**
 * agent-activity.js — Real-time Agent Activity widget for AtlasForge dashboard.
 *
 * Cycle 5 additions:
 *   - 4-way toggle: AtlasForge / Mission / Investigation / History
 *   - Agent timing: duration_seconds displayed in tab label and stream pane header after completion
 *
 * Exported API:
 *   initAgentActivity()                  — call once on page load
 *   handleMissionAgentEvent(data)        — registered with socket.js for 'mission_agents'
 *   handleInvestigationAgentEvent(data)  — registered with socket.js for 'investigation_agents'
 *   switchActivityPanel(panelName)       — called from HTML onclick
 */

// =============================================================================
// STATE
// =============================================================================

const agentActivityState = {
    mission: {
        agents: {},
        selectedAgent: null,
        agentOrder: [],
    },
    investigation: {
        agents: {},
        selectedAgent: null,
        agentOrder: [],
    },
    activePanel: 'atlasforge', // 'atlasforge' | 'mission' | 'investigation'
    research: {
        agent_id: null,
        status: 'idle',   // 'idle' | 'running' | 'complete' | 'error'
        topics: [],       // array of {index, label, status, sources}
        total_topics: 0,
        sources_found: 0,
        last_message: '',
        started_at: null,
        _hideTimer: null,
    },
};

let _reconcileTimer = null;

const EVENT_COLORS = {
    thinking:    'stream-line-thinking',
    tool_call:   'stream-line-tool_call',
    tool_result: 'stream-line-tool_result',
    error:       'stream-line-error',
    raw:         'stream-line-raw',
};

const EVENT_ICONS = {
    thinking:    '\u{1F4AD}',
    tool_call:   '\u{1F527}',
    tool_result: '\u2705',
    error:       '\u274C',
    raw:         '\u00B7',
};

// =============================================================================
// INIT
// =============================================================================

export function initAgentActivity() {
    _injectStyles();
    window.switchActivityPanel = switchActivityPanel;
    if (typeof window.subscribeToSocketRoom === 'function') {
        window.subscribeToSocketRoom('mission_agents');
        window.subscribeToSocketRoom('investigation_agents');
    }
    // Start periodic reconciliation to close ghost tabs (agent_complete events dropped
    // during brief WebSocket lag that doesn't trigger a full reconnect cycle).
    if (_reconcileTimer) clearInterval(_reconcileTimer);
    _reconcileTimer = setInterval(_reconcileAllAgentTabs, 3000);
}

// =============================================================================
// EVENT HANDLERS
// =============================================================================

export function handleMissionAgentEvent(data) {
    if (data && data.event === 'research_progress') {
        _handleResearchProgress(data);
        return;
    }
    _updateAgentPanel('mission', data);
}

export function handleInvestigationAgentEvent(data) {
    _updateAgentPanel('investigation', data);
}

// =============================================================================
// PANEL SWITCHER
// =============================================================================

export function switchActivityPanel(panelName) {
    agentActivityState.activePanel = panelName;

    ['atlasforge', 'mission', 'investigation'].forEach(name => {
        const btn = document.getElementById(`activity-btn-${name}`);
        if (btn) btn.classList.toggle('active', name === panelName);
    });

    const panels = {
        atlasforge:    document.getElementById('atlasforge-activity-panel'),
        mission:       document.getElementById('mission-activity-panel'),
        investigation: document.getElementById('investigation-activity-panel'),
    };

    Object.entries(panels).forEach(([name, el]) => {
        if (!el) return;
        el.style.display = name === panelName ? 'flex' : 'none';
    });


    // Auto-select first agent tab when switching to mission/investigation panels
    if (panelName === 'mission' || panelName === 'investigation') {
        const state = agentActivityState[panelName];
        if (state && state.selectedAgent === null && state.agentOrder.length > 0) {
            const firstId = state.agentOrder.find(id => state.agents[id]);
            if (firstId) _selectAgentTab(panelName, firstId);
        }
    }
}

// =============================================================================
// CORE EVENT ROUTER
// =============================================================================

function _updateAgentPanel(context, data) {
    if (!data || !data.event) return;

    switch (data.event) {
        case 'agent_spawned':
            _spawnAgentTab(context, data);
            break;
        case 'agent_stream_line':
            _appendStreamLine(context, data);
            break;
        case 'agent_complete':
            _closeAgentTab(context, data.agent_id, false, data.duration_seconds, data.line_count);
            break;
        case 'agent_error':
            if (data.error === 'process_died') {
                // Process was killed externally — clean up tab quickly without error styling
                _closeAgentTab(context, data.agent_id, false, null, null);
            } else {
                _errorAgentTab(context, data.agent_id, data.error);
            }
            break;
        case 'initial_state':
            _rebuildFromState(context, data.agents || []);
            break;
        default:
            break;
    }
}

// =============================================================================
// TAB MANAGEMENT
// =============================================================================

function _spawnAgentTab(context, data) {
    const { agent_id, label } = data;
    const state = agentActivityState[context];
    if (!state) return;
    if (state.agents[agent_id]) return;

    state.agents[agent_id] = {
        label: label || agent_id,
        status: 'running',
        lines: [],
        tabEl: null,
        error: null,
        duration_seconds: null,
        line_count: null,
    };
    state.agentOrder.push(agent_id);

    const tabGrid = document.getElementById(`${context}-agent-tabs`);
    if (!tabGrid) return;

    const streamPane = document.getElementById(`${context}-agent-stream`);
    if (streamPane) {
        const idle = streamPane.querySelector('.idle-message');
        if (idle) idle.remove();
    }

    const tab = document.createElement('button');
    tab.className = 'agent-tab';
    tab.dataset.agentId = agent_id;
    tab.dataset.context = context;
    tab.innerHTML = `<span class="pulse-dot"></span><span class="agent-tab-label">${_esc(label || agent_id)}</span>`;
    tab.addEventListener('click', () => _selectAgentTab(context, agent_id));
    tabGrid.appendChild(tab);

    state.agents[agent_id].tabEl = tab;

    if (state.selectedAgent === null) {
        _selectAgentTab(context, agent_id);
    }

    if (context === 'mission') _updateMissionBadge();
}

function _selectAgentTab(context, agent_id) {
    const state = agentActivityState[context];
    if (!state || !state.agents[agent_id]) return;

    if (state.selectedAgent && state.agents[state.selectedAgent]) {
        const prevTab = state.agents[state.selectedAgent].tabEl;
        if (prevTab) prevTab.classList.remove('selected');
    }

    state.selectedAgent = agent_id;
    const tab = state.agents[agent_id].tabEl;
    if (tab) tab.classList.add('selected');

    const pane = document.getElementById(`${context}-agent-stream`);
    if (!pane) return;

    pane.innerHTML = '';
    const agent = state.agents[agent_id];
    agent.lines.forEach(line => {
        pane.appendChild(_buildStreamLineEl(line));
    });
    pane.scrollTop = pane.scrollHeight;

    // Update stream pane header with agent stats
    const header = document.getElementById(`${context}-stream-header`);
    if (header) {
        let headerText = `Agent: ${agent.label}`;
        if (agent.duration_seconds != null) headerText += ` | Duration: ${agent.duration_seconds}s`;
        if (agent.line_count != null) headerText += ` | Lines: ${agent.line_count}`;
        header.textContent = headerText;
    }
}

function _appendStreamLine(context, data) {
    const { agent_id, event_type, text, timestamp } = data;
    const state = agentActivityState[context];
    if (!state) return;

    if (!state.agents[agent_id]) {
        _spawnAgentTab(context, { agent_id, label: agent_id });
    }

    const agent = state.agents[agent_id];
    if (!agent) return;

    // Notify socket keep-alive: receiving stream lines proves the connection is alive
    if (typeof window.notifySocketConnectionActive === 'function') {
        window.notifySocketConnectionActive();
    }

    const lineData = { event_type: event_type || 'raw', text: text || '', timestamp };
    agent.lines.push(lineData);

    if (state.selectedAgent === agent_id) {
        const pane = document.getElementById(`${context}-agent-stream`);
        if (pane) {
            const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
            pane.appendChild(_buildStreamLineEl(lineData));
            if (atBottom) pane.scrollTop = pane.scrollHeight;
        }
    }
}

function _closeAgentTab(context, agent_id, isError, durationSeconds, lineCount, retain = false) {
    const state = agentActivityState[context];
    if (!state || !state.agents[agent_id]) return;

    const agent = state.agents[agent_id];
    // Set 'closing' immediately so _reconcileAgentTabs won't re-spawn this tab
    // during the 2000ms animation window. isError stays permanent on the object.
    agent.status = isError ? 'error' : 'closing';

    if (durationSeconds != null) agent.duration_seconds = durationSeconds;
    if (lineCount != null) agent.line_count = lineCount;

    const tab = agent.tabEl;
    if (!tab) return;

    if (isError) {
        return;
    }

    // Update tab label with timing
    if (durationSeconds != null) {
        const labelEl = tab.querySelector('.agent-tab-label');
        if (labelEl) {
            labelEl.textContent = `${agent.label} (${durationSeconds}s)`;
        }
        if (state.selectedAgent === agent_id) {
            const header = document.getElementById(`${context}-stream-header`);
            if (header) {
                let headerText = `Agent: ${agent.label} | Duration: ${durationSeconds}s`;
                if (lineCount != null) headerText += ` | Lines: ${lineCount}`;
                header.textContent = headerText;
            }
        }
    }

    tab.style.opacity = '0.4';
    tab.style.transition = 'opacity 0.3s ease';
    const dot = tab.querySelector('.pulse-dot');
    if (dot) dot.style.animation = 'none';

    if (retain) {
        agent.status = isError ? 'error' : 'complete';
        if (context === 'mission') _updateMissionBadge();
        return;
    }

    setTimeout(() => {
        tab.remove();
        delete state.agents[agent_id];
        state.agentOrder = state.agentOrder.filter(id => id !== agent_id);

        if (state.selectedAgent === agent_id) {
            state.selectedAgent = null;
            const nextId = state.agentOrder.find(id => state.agents[id] && state.agents[id].status === 'running');
            if (nextId) {
                _selectAgentTab(context, nextId);
            } else {
                _showIdleMessage(context);
            }
        }

        if (Object.keys(state.agents).length === 0) {
            _showIdleMessage(context);
        }

        if (context === 'mission') _updateMissionBadge();
    }, 2000);
}

function _errorAgentTab(context, agent_id, errorMsg, retain = false) {
    const state = agentActivityState[context];
    if (!state || !state.agents[agent_id]) return;

    const agent = state.agents[agent_id];
    agent.status = 'error';
    agent.error = errorMsg;

    const tab = agent.tabEl;
    if (tab) {
        tab.classList.add('error');
        const dot = tab.querySelector('.pulse-dot');
        if (dot) dot.style.animation = 'none';
    }

    if (retain) {
        if (context === 'mission') _updateMissionBadge();
        return;
    }

    const runningAgents = Object.values(state.agents).filter(a => a.status === 'running');
    if (runningAgents.length === 0) {
        setTimeout(() => {
            Object.keys(state.agents).forEach(id => {
                if (state.agents[id] && state.agents[id].status === 'error') {
                    _removeTab(context, id);
                }
            });
        }, 2000);
    }
}

function _removeTab(context, agent_id) {
    const state = agentActivityState[context];
    if (!state || !state.agents[agent_id]) return;

    const tab = state.agents[agent_id].tabEl;
    if (tab) tab.remove();

    delete state.agents[agent_id];
    state.agentOrder = state.agentOrder.filter(id => id !== agent_id);

    if (state.selectedAgent === agent_id) {
        state.selectedAgent = null;
        const nextId = state.agentOrder.find(id => state.agents[id] && state.agents[id].status === 'running');
        if (nextId) {
            _selectAgentTab(context, nextId);
        } else {
            _showIdleMessage(context);
        }
    }

    if (Object.keys(state.agents).length === 0) {
        _showIdleMessage(context);
    }

    if (context === 'mission') _updateMissionBadge();
}

function _showIdleMessage(context) {
    const msg = context === 'investigation'
        ? 'No active investigations.'
        : 'Waiting for agents...';
    const pane = document.getElementById(`${context}-agent-stream`);
    if (pane) {
        pane.innerHTML = `<div class="idle-message">${msg}</div>`;
    }
    const tabGrid = document.getElementById(`${context}-agent-tabs`);
    if (tabGrid) tabGrid.innerHTML = '';
    const header = document.getElementById(`${context}-stream-header`);
    if (header) header.textContent = '';
}

// =============================================================================
// MISSION BADGE
// =============================================================================

function _updateMissionBadge() {
    const btn = document.getElementById('activity-btn-mission');
    if (!btn) return;
    const state = agentActivityState['mission'];
    if (!state) return;
    const runningCount = Object.values(state.agents).filter(a => a.status === 'running').length;
    let badge = btn.querySelector('.mission-badge');
    if (runningCount > 0) {
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'mission-badge';
            btn.appendChild(badge);
        }
        badge.textContent = runningCount;
    } else if (badge) {
        badge.remove();
    }
}

// =============================================================================
// RECONNECT / INITIAL STATE
// =============================================================================

function _rebuildFromState(context, agents) {
    // Clear stale UI state from previous sessions before rebuilding
    const state = agentActivityState[context];
    if (state) {
        const tabGrid = document.getElementById(`${context}-agent-tabs`);
        if (tabGrid) tabGrid.innerHTML = '';
        state.agents = {};
        state.agentOrder = [];
        state.selectedAgent = null;
    }

    if (!Array.isArray(agents) || agents.length === 0) {
        _showIdleMessage(context);
        return;
    }

    agents.forEach(agentInfo => {
        _spawnAgentTab(context, {
            agent_id: agentInfo.agent_id,
            label: agentInfo.label,
        });

        if (agentInfo.stream_lines && agentInfo.stream_lines.length > 0) {
            // Lines embedded in initial_state payload — skip REST call entirely
            agentInfo.stream_lines.forEach(line => {
                _appendStreamLine(context, {
                    agent_id: agentInfo.agent_id,
                    event_type: line.event_type,
                    text: line.display_text || line.text || '',
                    timestamp: line.timestamp,
                });
            });
        } else {
            // Fallback: REST call (no embedded lines, e.g. first start with empty cache)
            _fetchAgentStreamLines(context, agentInfo.agent_id);
        }

        if (agentInfo.status === 'error') {
            _errorAgentTab(context, agentInfo.agent_id, agentInfo.error || 'Agent failed', true);
        } else if (agentInfo.status !== 'running') {
            _closeAgentTab(context, agentInfo.agent_id, false, agentInfo.duration_seconds, agentInfo.line_count, true);
        }
    });
}

async function _fetchAgentStreamLines(context, agent_id) {
    try {
        const resp = await fetch(`/api/agent-stream/${encodeURIComponent(agent_id)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.lines) return;

        data.lines.forEach(line => {
            _appendStreamLine(context, {
                agent_id,
                event_type: line.event_type,
                text: line.display_text || line.text || '',
                timestamp: line.timestamp,
            });
        });
    } catch (e) {
        // Non-critical
    }
}

// =============================================================================
// PERIODIC RECONCILIATION (ghost tab cleanup)
// =============================================================================

async function _reconcileAllAgentTabs() {
    try {
        const resp = await fetch('/api/active-agents');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success) return;
        const agentGroups = data.agents || {};
        _reconcileAgentTabs('mission', agentGroups.mission || []);
        _reconcileAgentTabs('investigation', agentGroups.investigation || []);
    } catch (e) {
        // Non-critical — skip this reconciliation cycle
    }
}

function _reconcileAgentTabs(context, activeAgents) {
    const state = agentActivityState[context];
    if (!state) return;

    const activeIds = new Set(activeAgents.map(a => a.agent_id));

    // Close ghost tabs: UI shows running but server says agent is gone
    Object.keys(state.agents).forEach(agent_id => {
        const agent = state.agents[agent_id];
        if (agent && agent.status === 'running' && !activeIds.has(agent_id)) {
            _closeAgentTab(context, agent_id, false, null, null);
        }
    });

    // Spawn missing tabs: server has running agents the UI never received agent_spawned for.
    // Primary recovery path when agent_spawned was dropped due to handler registration race.
    activeAgents.forEach(agentInfo => {
        const existing = state.agents[agentInfo.agent_id];
        if (!existing) {
            _spawnAgentTab(context, {
                agent_id: agentInfo.agent_id,
                label: agentInfo.label || agentInfo.agent_id,
            });
            _fetchAgentStreamLines(context, agentInfo.agent_id);
        }
        // If existing.status === 'closing': tab is mid-animation (2s), skip re-spawn.
        // Reconcile runs every 3s so next cycle it will be gone from state entirely.
    });
}

// =============================================================================
// DOM HELPERS
// =============================================================================

function _buildStreamLineEl(lineData) {
    const div = document.createElement('div');
    const cls = EVENT_COLORS[lineData.event_type] || EVENT_COLORS.raw;
    const icon = EVENT_ICONS[lineData.event_type] || '\u00B7';
    div.className = `stream-line ${cls}`;
    div.textContent = `${icon} ${lineData.text || ''}`;
    return div;
}

function _esc(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// =============================================================================
// INJECTED STYLES
// =============================================================================

function _injectStyles() {
    if (document.getElementById('agent-activity-styles')) return;
    const style = document.createElement('style');
    style.id = 'agent-activity-styles';
    style.textContent = `
        .activity-toggle-header {
            display: flex;
            gap: 4px;
            align-items: center;
            padding: 0 0 8px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 8px;
            flex-shrink: 0;
            flex-wrap: wrap;
        }
        .activity-tab-btn {
            padding: 4px 10px;
            border-radius: 4px;
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-dim);
            cursor: pointer;
            font-size: 0.8em;
            white-space: nowrap;
        }
        .activity-tab-btn.active {
            background: var(--accent);
            color: var(--bg);
            border-color: var(--accent);
        }
        .activity-tab-btn:hover:not(.active) {
            border-color: var(--accent);
            color: var(--text);
        }
        .activity-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
        .agent-tab-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 4px;
            padding: 4px 0;
            flex-shrink: 0;
            min-height: 28px;
        }
        .agent-tab {
            padding: 3px 6px;
            border-radius: 4px;
            background: var(--bg-secondary, #1a1a2e);
            border: 1px solid var(--border);
            color: var(--text);
            cursor: pointer;
            font-size: 0.72em;
            display: flex;
            align-items: center;
            gap: 4px;
            overflow: hidden;
            white-space: nowrap;
        }
        .agent-tab.selected {
            border-color: var(--accent);
            background: rgba(88, 166, 255, 0.1);
        }
        .agent-tab.error {
            border-color: #f85149;
            background: rgba(248, 81, 73, 0.1);
            color: #f85149;
        }
        .agent-tab-label {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex: 1;
        }
        .pulse-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--green, #3fb950);
            animation: agent-pulse 1.4s ease-in-out infinite;
            flex-shrink: 0;
        }
        .agent-tab.error .pulse-dot { background: #f85149; animation: none; }
        @keyframes agent-pulse { 0%,100%{opacity:1} 50%{opacity:0.25} }
        .agent-stream-pane {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
            font-family: monospace;
            font-size: 0.76em;
            line-height: 1.55;
            background: var(--bg, #0d1117);
            border: 1px solid var(--border);
            border-radius: 4px;
            min-height: 0;
        }
        .agent-stream-header {
            font-size: 0.78em;
            color: var(--text-dim);
            padding: 2px 0;
            flex-shrink: 0;
            min-height: 18px;
        }
        .stream-line { padding: 1px 0; word-break: break-word; }
        .stream-line-thinking    { color: #58a6ff; }
        .stream-line-tool_call   { color: #d18616; }
        .stream-line-tool_result { color: #3fb950; }
        .stream-line-error       { color: #f85149; }
        .stream-line-raw         { color: var(--text-dim, #8b949e); }
        .idle-message { color: var(--text-dim); font-style: italic; text-align: center; padding: 20px; }
        #activity-btn-mission { position: relative; }
        .mission-badge {
            position: absolute;
            top: -5px;
            right: -5px;
            background: var(--accent, #4fc3f7);
            color: var(--bg, #0d1117);
            border-radius: 50%;
            font-size: 0.65em;
            min-width: 14px;
            height: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            pointer-events: none;
            padding: 0 2px;
            box-sizing: border-box;
        }
    `;
    document.head.appendChild(style);
}

// =============================================================================
// RESEARCH WIDGET
// =============================================================================

function _handleResearchProgress(data) {
    const rs = agentActivityState.research;
    rs.agent_id = data.agent_id || rs.agent_id;
    rs.status = data.status || 'running';
    rs.last_message = data.message || '';
    rs.sources_found = data.sources_found || rs.sources_found;
    rs.total_topics = data.total_topics || rs.total_topics;

    // Track per-topic state
    if (typeof data.topic_index === 'number') {
        const idx = data.topic_index;
        while (rs.topics.length <= idx) {
            rs.topics.push({ index: rs.topics.length, label: '', status: 'pending', sources: 0 });
        }
        rs.topics[idx].status = (rs.status === 'complete') ? 'done' : 'active';
        rs.topics[idx].sources = data.sources_found || 0;
        if (data.message) rs.topics[idx].label = data.message.slice(0, 60);
        // Mark previous topics as done
        for (let i = 0; i < idx; i++) {
            if (rs.topics[i].status === 'active') rs.topics[i].status = 'done';
        }
    }

    if (rs.status === 'running' && !rs.started_at) {
        rs.started_at = Date.now();
        // Auto-switch to Mission panel when research starts
        if (agentActivityState.activePanel !== 'mission') {
            switchActivityPanel('mission');
        }
    }

    _renderResearchWidget();

    // Hide widget 30s after completion
    if (rs.status === 'complete' || rs.status === 'error') {
        if (rs._hideTimer) clearTimeout(rs._hideTimer);
        rs._hideTimer = setTimeout(() => {
            const w = document.getElementById('research-progress-widget');
            if (w) w.style.display = 'none';
            agentActivityState.research.status = 'idle';
        }, 30000);
    }
}

function _renderResearchWidget() {
    const rs = agentActivityState.research;
    const w = document.getElementById('research-progress-widget');
    if (!w) return;

    w.style.display = 'block';

    const badge = document.getElementById('research-status-badge');
    if (badge) {
        badge.textContent = rs.status;
        badge.className = 'research-badge ' + rs.status;
    }

    const srcs = document.getElementById('research-sources');
    if (srcs) srcs.textContent = rs.sources_found + ' source' + (rs.sources_found !== 1 ? 's' : '');

    const msg = document.getElementById('research-message');
    if (msg) msg.textContent = rs.last_message.slice(0, 120);

    const topicList = document.getElementById('research-topics-list');
    if (topicList) {
        topicList.innerHTML = '';
        const total = rs.total_topics || rs.topics.length;
        for (let i = 0; i < total; i++) {
            const t = rs.topics[i] || { index: i, label: 'Topic ' + (i + 1), status: 'pending', sources: 0 };
            const icon = t.status === 'done' ? '\u2713' : t.status === 'active' ? '\u25B6' : '\u25CB';
            const li = document.createElement('div');
            li.className = 'research-topic-item ' + t.status;
            li.textContent = icon + ' ' + (t.label || ('Topic ' + (i + 1)));
            topicList.appendChild(li);
        }
    }
}

// =============================================================================
// GLOBAL EXPORTS
// =============================================================================

window.handleMissionAgentEvent = handleMissionAgentEvent;
window.handleInvestigationAgentEvent = handleInvestigationAgentEvent;
window.initAgentActivity = initAgentActivity;
window.switchActivityPanel = switchActivityPanel;
