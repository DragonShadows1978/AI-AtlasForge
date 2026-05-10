/**
 * activity-atlasforge.js — AtlasForge activity panel.
 *
 * Consumes the existing chat socket (rendered by other code into
 * #chat-messages). This module is intentionally minimal — it only injects
 * the shared styles and stays out of the way.
 *
 * Independent component: no shared state with mission/investigation panels.
 */

let _initialized = false;

export function initAtlasforgePanel() {
    if (_initialized) return;
    _initialized = true;
    _injectStyles();
}

function _injectStyles() {
    if (document.getElementById('activity-atlasforge-styles')) return;
    const style = document.createElement('style');
    style.id = 'activity-atlasforge-styles';
    style.textContent = `
        .activity-toggle-header {
            display: flex;
            gap: 5px;
            align-items: center;
            padding: 0 0 8px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 8px;
            flex-shrink: 0;
            flex-wrap: wrap;
            min-width: 0;
        }
        .activity-tab-btn {
            padding: 4px 8px;
            border-radius: 4px;
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-dim);
            cursor: pointer;
            font-size: 0.78em;
            white-space: nowrap;
            min-width: 0;
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
    `;
    document.head.appendChild(style);
}
