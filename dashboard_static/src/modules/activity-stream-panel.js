/**
 * activity-stream-panel.js — Shared rendering class for SSE-driven agent panels.
 *
 * Each instance is bound to a specific context (mission|investigation), owns its
 * own state and DOM IDs, and is constructed once per panel. The two consumer
 * modules (activity-mission.js, activity-investigation.js) instantiate this and
 * never share state — the only commonality is the rendering code.
 *
 * Note: this class does not exist to bring back a shared `context` flag-arg.
 * Each instance is configured at construction with its DOM IDs and SSE URL;
 * after that, no method takes a `context` parameter.
 */

import { StreamClient } from './sse-client.js';

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
    tool_result: '✅',
    error:       '❌',
    raw:         '·',
};

export class StreamPanel {
    /**
     * @param {Object} cfg
     * @param {string} cfg.sseUrl       SSE endpoint (e.g. /api/agents/stream?context=mission)
     * @param {string} cfg.storageKey   localStorage key for page-reload resume
     * @param {string} cfg.idleMessage  Text shown when no agents are active
     * @param {string} cfg.tabsId       DOM id for tab grid container
     * @param {string} cfg.streamId     DOM id for stream pane container
     * @param {string} cfg.headerId     DOM id for stream header
     * @param {string} [cfg.badgeBtnId] Optional id of toggle button to badge with running-count
     */
    constructor(cfg) {
        this.cfg = cfg;
        this.state = { agents: {}, selectedAgent: null, agentOrder: [] };
        this.client = new StreamClient(cfg.sseUrl, cfg.storageKey);
        this._wireHandlers();
        this._stylesInjected = false;
    }

    open() {
        if (!this._stylesInjected) {
            _injectSharedStyles();
            this._stylesInjected = true;
        }
        this.client.open();
    }

    close() {
        this.client.close();
    }

    _wireHandlers() {
        this.client.on('agent_state_snapshot', (data) => this._handleSnapshot(data));
        this.client.on('agent_spawned', (data) => this._spawnTab(data));
        this.client.on('stream_line', (data) => this._appendLine(data));
        this.client.on('agent_complete', (data) => this._closeTab(data, false));
        this.client.on('agent_error', (data) => {
            if (data && data.error === 'process_died') {
                this._closeTab(data, false);
            } else {
                this._errorTab(data);
            }
        });
    }

    _handleSnapshot(data) {
        const agents = (data && data.agents) || [];
        // Clear any stale UI (could happen if user reloads while agents still active).
        const tabGrid = document.getElementById(this.cfg.tabsId);
        if (tabGrid) tabGrid.innerHTML = '';
        this.state.agents = {};
        this.state.agentOrder = [];
        this.state.selectedAgent = null;
        if (agents.length === 0) {
            this._showIdleMessage();
            return;
        }
        for (const a of agents) {
            this._spawnTab({ agent_id: a.agent_id, label: a.label });
        }
    }

    _spawnTab(data) {
        const agent_id = data && data.agent_id;
        if (!agent_id) return;
        const state = this.state;
        if (state.agents[agent_id]) return;

        state.agents[agent_id] = {
            label: data.label || agent_id,
            status: 'running',
            lines: [],
            tabEl: null,
            error: null,
            duration_seconds: null,
            line_count: null,
        };
        state.agentOrder.push(agent_id);

        const tabGrid = document.getElementById(this.cfg.tabsId);
        if (!tabGrid) return;

        const streamPane = document.getElementById(this.cfg.streamId);
        if (streamPane) {
            const idle = streamPane.querySelector('.idle-message');
            if (idle) idle.remove();
        }

        const tab = document.createElement('button');
        tab.className = 'agent-tab';
        tab.dataset.agentId = agent_id;
        tab.innerHTML = `<span class="pulse-dot"></span><span class="agent-tab-label">${_esc(data.label || agent_id)}</span>`;
        tab.addEventListener('click', () => this._selectTab(agent_id));
        tabGrid.appendChild(tab);
        state.agents[agent_id].tabEl = tab;

        if (state.selectedAgent === null) {
            this._selectTab(agent_id);
        }
        this._updateBadge();
    }

    _selectTab(agent_id) {
        const state = this.state;
        if (!state.agents[agent_id]) return;
        if (state.selectedAgent && state.agents[state.selectedAgent]) {
            const prev = state.agents[state.selectedAgent].tabEl;
            if (prev) prev.classList.remove('selected');
        }
        state.selectedAgent = agent_id;
        const tab = state.agents[agent_id].tabEl;
        if (tab) tab.classList.add('selected');

        const pane = document.getElementById(this.cfg.streamId);
        if (!pane) return;
        pane.innerHTML = '';
        const agent = state.agents[agent_id];
        agent.lines.forEach(line => pane.appendChild(_buildStreamLineEl(line)));
        pane.scrollTop = pane.scrollHeight;

        const header = document.getElementById(this.cfg.headerId);
        if (header) {
            let txt = `Agent: ${agent.label}`;
            if (agent.duration_seconds != null) txt += ` | Duration: ${agent.duration_seconds}s`;
            if (agent.line_count != null) txt += ` | Lines: ${agent.line_count}`;
            header.textContent = txt;
        }
    }

    _appendLine(data) {
        if (!data) return;
        const agent_id = data.agent_id;
        if (!agent_id) return;
        const state = this.state;
        if (!state.agents[agent_id]) {
            this._spawnTab({ agent_id, label: data.label || agent_id });
        }
        const agent = state.agents[agent_id];
        if (!agent) return;
        const lineData = {
            event_type: data.event_type || 'raw',
            text: data.text || '',
            timestamp: data.timestamp,
        };
        agent.lines.push(lineData);
        if (state.selectedAgent === agent_id) {
            const pane = document.getElementById(this.cfg.streamId);
            if (pane) {
                const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
                pane.appendChild(_buildStreamLineEl(lineData));
                if (atBottom) pane.scrollTop = pane.scrollHeight;
            }
        }
    }

    _closeTab(data, isError) {
        const agent_id = data && data.agent_id;
        if (!agent_id) return;
        const state = this.state;
        const agent = state.agents[agent_id];
        if (!agent) return;
        agent.status = isError ? 'error' : 'closing';
        if (data.duration_seconds != null) agent.duration_seconds = data.duration_seconds;
        if (data.line_count != null) agent.line_count = data.line_count;

        const tab = agent.tabEl;
        if (!tab) return;
        if (isError) return;

        if (data.duration_seconds != null) {
            const lbl = tab.querySelector('.agent-tab-label');
            if (lbl) lbl.textContent = `${agent.label} (${data.duration_seconds}s)`;
            if (state.selectedAgent === agent_id) {
                const header = document.getElementById(this.cfg.headerId);
                if (header) {
                    let txt = `Agent: ${agent.label} | Duration: ${data.duration_seconds}s`;
                    if (data.line_count != null) txt += ` | Lines: ${data.line_count}`;
                    header.textContent = txt;
                }
            }
        }
        tab.style.opacity = '0.4';
        tab.style.transition = 'opacity 0.3s ease';
        const dot = tab.querySelector('.pulse-dot');
        if (dot) dot.style.animation = 'none';

        setTimeout(() => {
            tab.remove();
            delete state.agents[agent_id];
            state.agentOrder = state.agentOrder.filter(id => id !== agent_id);
            if (state.selectedAgent === agent_id) {
                state.selectedAgent = null;
                const nextId = state.agentOrder.find(id => state.agents[id] && state.agents[id].status === 'running');
                if (nextId) this._selectTab(nextId);
                else this._showIdleMessage();
            }
            if (Object.keys(state.agents).length === 0) this._showIdleMessage();
            this._updateBadge();
        }, 2000);
    }

    _errorTab(data) {
        const agent_id = data && data.agent_id;
        if (!agent_id) return;
        const state = this.state;
        const agent = state.agents[agent_id];
        if (!agent) return;
        agent.status = 'error';
        agent.error = data.error || null;
        const tab = agent.tabEl;
        if (tab) {
            tab.classList.add('error');
            const dot = tab.querySelector('.pulse-dot');
            if (dot) dot.style.animation = 'none';
        }
        const stillRunning = Object.values(state.agents).some(a => a.status === 'running');
        if (!stillRunning) {
            setTimeout(() => {
                Object.keys(state.agents).forEach(id => {
                    const a = state.agents[id];
                    if (a && a.status === 'error' && a.tabEl) {
                        a.tabEl.remove();
                        delete state.agents[id];
                    }
                });
                state.agentOrder = state.agentOrder.filter(id => state.agents[id]);
                if (Object.keys(state.agents).length === 0) this._showIdleMessage();
                this._updateBadge();
            }, 2000);
        }
        this._updateBadge();
    }

    _showIdleMessage() {
        const pane = document.getElementById(this.cfg.streamId);
        if (pane) pane.innerHTML = `<div class="idle-message">${_esc(this.cfg.idleMessage)}</div>`;
        const tabGrid = document.getElementById(this.cfg.tabsId);
        if (tabGrid) tabGrid.innerHTML = '';
        const header = document.getElementById(this.cfg.headerId);
        if (header) header.textContent = '';
    }

    _updateBadge() {
        if (!this.cfg.badgeBtnId) return;
        const btn = document.getElementById(this.cfg.badgeBtnId);
        if (!btn) return;
        const running = Object.values(this.state.agents).filter(a => a.status === 'running').length;
        let badge = btn.querySelector('.mission-badge');
        if (running > 0) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'mission-badge';
                btn.appendChild(badge);
            }
            badge.textContent = running;
        } else if (badge) {
            badge.remove();
        }
    }
}

function _buildStreamLineEl(lineData) {
    const div = document.createElement('div');
    const cls = EVENT_COLORS[lineData.event_type] || EVENT_COLORS.raw;
    const icon = EVENT_ICONS[lineData.event_type] || '·';
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

let _stylesInjectedGlobal = false;
function _injectSharedStyles() {
    if (_stylesInjectedGlobal) return;
    _stylesInjectedGlobal = true;
    if (document.getElementById('agent-stream-panel-styles')) return;
    const style = document.createElement('style');
    style.id = 'agent-stream-panel-styles';
    style.textContent = `
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
