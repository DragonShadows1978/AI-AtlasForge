/**
 * activity-panel-bar.js — Toggle button bar at the top of the activity card.
 *
 * The only thing the three independent panel components share. Owns the
 * AtlasForge / Mission / Investigation tab buttons and the panel show/hide.
 *
 * Each panel component (activity-mission, activity-investigation, activity-atlasforge)
 * subscribes to onPanelChange() so it can lazily open/close its EventSource
 * (browsers cap to ~6 connections per origin).
 */

const PANEL_NAMES = ['atlasforge', 'mission', 'investigation'];

const _state = {
    active: 'atlasforge',
    listeners: new Set(),
};

export function initActivityPanelBar() {
    _injectMobileNavSafetyStyles();
    window.switchActivityPanel = switchActivityPanel;
}

export function getActivePanel() {
    return _state.active;
}

export function onPanelChange(handler) {
    _state.listeners.add(handler);
    // Fire once immediately so the panel can sync to current state.
    try { handler(_state.active); } catch (_) { /* ignore */ }
    return () => _state.listeners.delete(handler);
}

export function switchActivityPanel(panelName) {
    if (!PANEL_NAMES.includes(panelName)) return;
    _state.active = panelName;

    PANEL_NAMES.forEach(name => {
        const btn = document.getElementById(`activity-btn-${name}`);
        if (btn) btn.classList.toggle('active', name === panelName);
        const panel = document.getElementById(`${name}-activity-panel`);
        if (panel) panel.style.display = name === panelName ? 'flex' : 'none';
    });

    for (const h of _state.listeners) {
        try { h(panelName); } catch (_) { /* ignore */ }
    }
}

function _injectMobileNavSafetyStyles() {
    if (document.getElementById('activity-panel-bar-styles')) return;
    const style = document.createElement('style');
    style.id = 'activity-panel-bar-styles';
    // Acceptance criterion 8: mobile nav buttons must remain tappable even when
    // a modal sets body.modal-open (which disables pointer-events on container).
    style.textContent = `
        body.modal-open .activity-toggle-header,
        body.modal-open .activity-tab-btn {
            pointer-events: auto !important;
            z-index: 100002;
            position: relative;
        }
    `;
    document.head.appendChild(style);
}

window.switchActivityPanel = switchActivityPanel;
