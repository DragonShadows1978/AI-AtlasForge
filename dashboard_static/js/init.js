/**
 * Dashboard Initialization Module
 * Orchestrates initialization of all modules on page load
 * Dependencies: All other modules (loads last)
 */

// =============================================================================
// INITIALIZATION
// =============================================================================

document.addEventListener('DOMContentLoaded', async function() {
    console.log('Dashboard initializing...');

    // Load saved card states
    if (typeof loadCardStates === 'function') {
        loadCardStates();
    }

    // Initialize tabs
    if (typeof initTabs === 'function') {
        initTabs();
    }

    // Initial data refresh
    if (typeof refresh === 'function') {
        await refresh();
    }

    // Check for crash recovery
    if (typeof checkForRecovery === 'function') {
        checkForRecovery();
    }

    // Set up periodic refreshes
    setupPeriodicRefreshes();

    console.log('Dashboard initialized');
});

// =============================================================================
// PERIODIC REFRESHES
// =============================================================================

function setupPeriodicRefreshes() {
    // Main status refresh - every 5 seconds
    if (typeof refresh === 'function') {
        setInterval(refresh, 5000);
    }

    // Analytics widget - every 30 seconds
    if (typeof refreshAnalyticsWidget === 'function') {
        refreshAnalyticsWidget();
        setInterval(refreshAnalyticsWidget, 30000);
    }

    // Git status widget - every 15 seconds
    if (typeof refreshGitStatusWidget === 'function') {
        refreshGitStatusWidget();
        setInterval(refreshGitStatusWidget, 15000);
    }

    // Multi-repo status - every 30 seconds
    if (typeof refreshRepoStatusWidget === 'function') {
        refreshRepoStatusWidget();
        setInterval(refreshRepoStatusWidget, 30000);
    }

    // Git analytics - every 60 seconds
    if (typeof refreshGitAnalyticsWidget === 'function') {
        refreshGitAnalyticsWidget();
        setInterval(refreshGitAnalyticsWidget, 60000);
    }

    // Decision graph - every 10 seconds
    if (typeof refreshDecisionGraph === 'function') {
        refreshDecisionGraph();
        setInterval(refreshDecisionGraph, 10000);
    }
}

// =============================================================================
// TAB SWITCH HOOKS
// =============================================================================

// Hook tab switching to load data when tabs are selected
const originalSwitchTabFn = typeof switchTab === 'function' ? switchTab : null;

if (originalSwitchTabFn) {
    window.switchTab = function(tabName) {
        originalSwitchTabFn(tabName);
        onTabSwitch(tabName);
    };
}

function onTabSwitch(tabName) {
    if (tabName === 'analytics' && typeof refreshFullAnalytics === 'function') {
        refreshFullAnalytics();
    } else if (tabName === 'lessons' && typeof loadAllLessons === 'function') {
        loadAllLessons();
    } else if (tabName === 'glassbox' && typeof loadGlassboxTabData === 'function') {
        loadGlassboxTabData();
    } else if (tabName === 'missionlogs' && typeof loadMissionLogsTabData === 'function') {
        loadMissionLogsTabData();
    } else if (tabName === 'bugbounty' && typeof refreshBugBountyData === 'function') {
        refreshBugBountyData();
    } else if (tabName === 'narrative' && typeof initNarrativeTab === 'function') {
        initNarrativeTab();
    }
}

// Debug: mark init module loaded
console.log('Init module loaded');
