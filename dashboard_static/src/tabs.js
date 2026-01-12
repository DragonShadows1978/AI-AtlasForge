/**
 * Dashboard Tabs Module (ES6)
 * Tab navigation and switching logic with lazy loading support
 * Dependencies: core.js
 */

import { showToast } from './core.js';

// =============================================================================
// TAB STATE
// =============================================================================

const tabLoadCallbacks = new Map();
const loadedTabs = new Set(['rde']); // RDE is always loaded

// =============================================================================
// LAZY LOADING REGISTRATION
// =============================================================================

/**
 * Register a callback to be called when a tab is first activated
 * @param {string} tabName - Tab name
 * @param {Function} callback - Callback to execute on first load
 */
export function registerTabLoader(tabName, callback) {
    tabLoadCallbacks.set(tabName, callback);
}

/**
 * Check if a tab's module is loaded
 * @param {string} tabName - Tab name
 * @returns {boolean}
 */
export function isTabLoaded(tabName) {
    return loadedTabs.has(tabName);
}

/**
 * Mark a tab as loaded
 * @param {string} tabName - Tab name
 */
export function markTabLoaded(tabName) {
    loadedTabs.add(tabName);
}

// =============================================================================
// VALID TAB NAMES (for URL hash validation)
// =============================================================================

const VALID_TABS = ['rde', 'investigations', 'analytics', 'lessons', 'glassbox', 'missionlogs'];

/**
 * Get tab name from URL hash
 * @returns {string|null} Tab name or null if invalid
 */
function getTabFromHash() {
    const hash = window.location.hash.slice(1); // Remove #
    return VALID_TABS.includes(hash) ? hash : null;
}

/**
 * Update URL hash without triggering popstate
 * @param {string} tabName - Tab name
 */
function updateHash(tabName) {
    // Use replaceState to update URL without creating history entry for each tab switch
    // Only use pushState for programmatic changes (not initial load)
    if (window.location.hash !== `#${tabName}`) {
        history.replaceState({ tab: tabName }, '', `#${tabName}`);
    }
}

// =============================================================================
// TAB SWITCHING
// =============================================================================

/**
 * Switch to a tab
 * @param {string} tabName - Tab name to switch to
 * @param {boolean} updateUrl - Whether to update URL hash (default true)
 */
export function switchTab(tabName, updateUrl = true) {
    const startTime = performance.now();

    // Validate tab name
    if (!VALID_TABS.includes(tabName)) {
        console.warn(`Invalid tab name: ${tabName}, defaulting to rde`);
        tabName = 'rde';
    }

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

    // Update URL hash for deep linking
    if (updateUrl) {
        updateHash(tabName);
    }

    // Lazy load tab module if needed, then fire refresh
    if (!loadedTabs.has(tabName) && tabLoadCallbacks.has(tabName)) {
        const loader = tabLoadCallbacks.get(tabName);
        loader().then(() => {
            loadedTabs.add(tabName);
            console.log(`Tab ${tabName} module loaded`);
            // Fire refresh AFTER module loads
            fireTabRefresh(tabName);
        }).catch(err => {
            console.error(`Failed to load ${tabName} module:`, err);
        });
    } else {
        // Module already loaded, fire refresh immediately
        fireTabRefresh(tabName);
    }

    const elapsed = performance.now() - startTime;
    console.log(`Tab switch to ${tabName} took ${elapsed.toFixed(1)}ms`);
}

/**
 * Fire refresh functions for a specific tab
 * @param {string} tabName - Tab name
 */
function fireTabRefresh(tabName) {
    switch (tabName) {
        case 'glassbox':
            if (typeof window.loadGlassboxTabData === 'function') {
                window.loadGlassboxTabData();
            }
            break;
        case 'missionlogs':
            if (typeof window.loadMissionLogsTabData === 'function') {
                window.loadMissionLogsTabData();
            }
            break;
        case 'analytics':
            if (typeof window.refreshFullAnalytics === 'function') {
                window.refreshFullAnalytics();
            }
            break;
        case 'lessons':
            if (typeof window.loadAllLessons === 'function') {
                window.loadAllLessons();
            }
            break;
        case 'investigations':
            if (typeof window.initInvestigationHistory === 'function') {
                window.initInvestigationHistory();
            }
            break;
    }
}

/**
 * Initialize tabs from URL hash or localStorage on page load
 */
export function initTabs() {
    // Run hash check immediately - don't defer
    const hashTab = getTabFromHash();
    const savedTab = localStorage.getItem('activeTab') || 'rde';
    // URL hash takes priority over localStorage
    const initialTab = hashTab || savedTab;

    // Store debug info for troubleshooting
    window._tabInitDebug = {
        hashTab: hashTab,
        savedTab: savedTab,
        initialTab: initialTab,
        rawHash: window.location.hash,
        url: window.location.href,
        timestamp: new Date().toISOString()
    };

    console.log(`Tab init: hash=${hashTab}, saved=${savedTab}, using=${initialTab}, url=${window.location.href}`);
    switchTab(initialTab);

    // Listen for browser back/forward navigation
    window.addEventListener('popstate', (event) => {
        const hashTab = getTabFromHash();
        if (hashTab) {
            // Don't update URL on popstate (would interfere with history)
            switchTab(hashTab, false);
        }
    });

    // Also listen for direct hash changes (e.g., user editing URL)
    window.addEventListener('hashchange', () => {
        const hashTab = getTabFromHash();
        if (hashTab) {
            switchTab(hashTab, false);
        }
    });
}

// =============================================================================
// KEYBOARD SHORTCUTS
// =============================================================================

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Skip when in input fields
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

        // Escape key to close modals
        if (e.key === 'Escape') {
            const modals = document.querySelectorAll('.modal.visible, [id$="-modal"].visible');
            modals.forEach(m => m.classList.remove('visible'));
            const tooltip = document.getElementById('graph-tooltip');
            if (tooltip) tooltip.classList.remove('visible');
            return;
        }

        // Tab shortcuts: 1-6 for tab switching
        if (e.key >= '1' && e.key <= '6' && !e.ctrlKey && !e.altKey && !e.metaKey) {
            const tabs = ['rde', 'investigations', 'analytics', 'lessons', 'glassbox', 'missionlogs'];
            const idx = parseInt(e.key) - 1;
            if (tabs[idx]) {
                switchTab(tabs[idx]);
                showToast(`Switched to ${tabs[idx]} tab`);
            }
            return;
        }

        // Other shortcuts
        if (e.key === 'e' || e.key === 'E') {
            if (typeof window.toggleCard === 'function') window.toggleCard('rde-exploration');
        } else if (e.key === 'd' || e.key === 'D') {
            if (typeof window.toggleCard === 'function') window.toggleCard('rde-drift');
        } else if (e.key === 'r' || e.key === 'R') {
            if (typeof window.refreshRDEWidgets === 'function') {
                window.refreshRDEWidgets();
                showToast('RDE widgets refreshed');
            }
        } else if (e.key === 'g' || e.key === 'G') {
            switchTab('glassbox');
            showToast('Switched to GlassBox');
        } else if (e.key === '?' && e.shiftKey) {
            showKeyboardShortcuts();
        }
    });
}

/**
 * Show keyboard shortcuts help
 */
export function showKeyboardShortcuts() {
    const shortcuts = `
        <div style="text-align: left; font-size: 0.9em;">
            <p><span class="kbd">1-6</span> Switch tabs</p>
            <p><span class="kbd">E</span> Toggle exploration card</p>
            <p><span class="kbd">R</span> Refresh widgets</p>
            <p><span class="kbd">G</span> Go to GlassBox tab</p>
            <p><span class="kbd">Esc</span> Close modals</p>
            <p><span class="kbd">?</span> Show this help</p>
        </div>
    `;
    showToast(shortcuts, 5000);
}

// Initialize keyboard shortcuts
setupKeyboardShortcuts();

// Export for global access
export { fireTabRefresh };
