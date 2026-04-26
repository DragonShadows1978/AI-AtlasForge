/**
 * activity-investigation.js — Investigation agent activity panel.
 *
 * Independent component with its own EventSource and state. Lazy-opens
 * its SSE connection when the user switches to the Investigation tab.
 */

import { StreamPanel } from './activity-stream-panel.js';
import { onPanelChange } from './activity-panel-bar.js';

let _panel = null;

export function initInvestigationPanel() {
    if (_panel) return;
    _panel = new StreamPanel({
        sseUrl: '/api/agents/stream?context=investigation',
        storageKey: 'investigation',
        idleMessage: 'No active investigations.',
        tabsId: 'investigation-agent-tabs',
        streamId: 'investigation-agent-stream',
        headerId: 'investigation-stream-header',
    });

    // Keep this connection warm so investigation activity is already buffered
    // when the user switches tabs.
    _panel.open();

    onPanelChange((active) => {
        if (active === 'investigation') _panel.open();
    });
}
