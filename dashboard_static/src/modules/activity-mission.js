/**
 * activity-mission.js — Mission agent activity panel.
 *
 * Independent component with its own EventSource and state. Subscribes to
 * the panel toggle bar so it can lazily open/close its SSE connection
 * (browsers cap to ~6 connections per origin).
 */

import { StreamPanel } from './activity-stream-panel.js';
import { onPanelChange } from './activity-panel-bar.js';

let _panel = null;
let _researchHideTimer = null;
const _research = { sources_found: 0, total_topics: 0, topics: [] };

export function initMissionPanel() {
    if (_panel) return;
    _panel = new StreamPanel({
        sseUrl: '/api/agents/stream?context=mission',
        storageKey: 'mission',
        idleMessage: 'Waiting for agents...',
        tabsId: 'mission-agent-tabs',
        streamId: 'mission-agent-stream',
        headerId: 'mission-stream-header',
        badgeBtnId: 'activity-btn-mission',
    });

    // Keep the mission stream warm even while the AtlasForge chat panel is
    // visible. Agent activity is time-sensitive; cold-opening the EventSource
    // only after the user clicks Mission makes the panel feel broken during
    // active runs.
    _panel.open();

    onPanelChange((active) => {
        if (active === 'mission') _panel.open();
    });
}

/**
 * Research progress is still pushed via the existing socket.io 'mission_status'-
 * style channel for now; the legacy registerHandler('mission_agents', ...) call
 * site routed research_progress through here. We expose a public function so
 * widgets.js can keep that wiring without re-introducing a shared `context` flag.
 */
export function handleResearchProgress(data) {
    if (!data) return;
    _research.sources_found = data.sources_found || _research.sources_found;
    _research.total_topics = data.total_topics || _research.total_topics;
    if (typeof data.topic_index === 'number') {
        const idx = data.topic_index;
        while (_research.topics.length <= idx) {
            _research.topics.push({ index: _research.topics.length, label: '', status: 'pending', sources: 0 });
        }
        _research.topics[idx].status = (data.status === 'complete') ? 'done' : 'active';
        _research.topics[idx].sources = data.sources_found || 0;
        if (data.message) _research.topics[idx].label = data.message.slice(0, 60);
        for (let i = 0; i < idx; i++) {
            if (_research.topics[i].status === 'active') _research.topics[i].status = 'done';
        }
    }
    _renderResearch(data);
    if (data.status === 'complete' || data.status === 'error') {
        if (_researchHideTimer) clearTimeout(_researchHideTimer);
        _researchHideTimer = setTimeout(() => {
            const w = document.getElementById('research-progress-widget');
            if (w) w.style.display = 'none';
        }, 30000);
    }
}

function _renderResearch(data) {
    const w = document.getElementById('research-progress-widget');
    if (!w) return;
    w.style.display = 'block';
    const badge = document.getElementById('research-status-badge');
    if (badge) {
        badge.textContent = data.status || 'running';
        badge.className = 'research-badge ' + (data.status || 'running');
    }
    const srcs = document.getElementById('research-sources');
    if (srcs) srcs.textContent = _research.sources_found + ' source' + (_research.sources_found !== 1 ? 's' : '');
    const msg = document.getElementById('research-message');
    if (msg) msg.textContent = (data.message || '').slice(0, 120);
    const topicList = document.getElementById('research-topics-list');
    if (topicList) {
        topicList.innerHTML = '';
        const total = _research.total_topics || _research.topics.length;
        for (let i = 0; i < total; i++) {
            const t = _research.topics[i] || { index: i, label: 'Topic ' + (i + 1), status: 'pending' };
            const icon = t.status === 'done' ? '✓' : t.status === 'active' ? '▶' : '○';
            const li = document.createElement('div');
            li.className = 'research-topic-item ' + t.status;
            li.textContent = icon + ' ' + (t.label || ('Topic ' + (i + 1)));
            topicList.appendChild(li);
        }
    }
}
