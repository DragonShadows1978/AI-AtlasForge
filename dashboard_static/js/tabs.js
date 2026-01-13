/**
 * Dashboard Tabs Module
 * Tab navigation and switching logic
 * Dependencies: core.js
 */

// =============================================================================
// TAB SWITCHING
// =============================================================================

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
    if (tabName === 'glassbox' && typeof loadGlassboxTabData === 'function') {
        loadGlassboxTabData();
    }

    // Load Mission Logs data if switching to that tab
    if (tabName === 'missionlogs' && typeof loadMissionLogsTabData === 'function') {
        loadMissionLogsTabData();
    }

    // Load Bug Bounty data if switching to that tab
    if (tabName === 'bugbounty' && typeof refreshBugBountyData === 'function') {
        refreshBugBountyData();
    }

    // Load Narrative data if switching to that tab
    if (tabName === 'narrative' && typeof initNarrativeTab === 'function') {
        initNarrativeTab();
    }

    // Load Analytics if switching to that tab
    if (tabName === 'analytics' && typeof refreshFullAnalytics === 'function') {
        refreshFullAnalytics();
    }

    // Load Lessons if switching to that tab
    if (tabName === 'lessons' && typeof loadAllLessons === 'function') {
        loadAllLessons();
    }
}

/**
 * Initialize tabs from localStorage on page load
 */
function initTabs() {
    const savedTab = localStorage.getItem('activeTab') || 'atlasforge';
    switchTab(savedTab);
}

// =============================================================================
// KEYBOARD SHORTCUTS
// =============================================================================

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
        if (typeof toggleCard === 'function') toggleCard('af-exploration');
    } else if (e.key === 'd' || e.key === 'D') {
        if (typeof toggleCard === 'function') toggleCard('af-drift');
    } else if (e.key === 'r' || e.key === 'R') {
        if (typeof refreshAtlasForgeWidgets === 'function') {
            refreshAtlasForgeWidgets();
            showToast('AtlasForge widgets refreshed');
        }
    } else if (e.key === 'g' || e.key === 'G') {
        switchTab('glassbox');
        showToast('Switched to GlassBox');
    } else if (e.key === '?' && e.shiftKey) {
        showKeyboardShortcuts();
    }
});

/**
 * Show keyboard shortcuts help
 */
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

// Debug: mark tabs module loaded
console.log('Tabs module loaded');
