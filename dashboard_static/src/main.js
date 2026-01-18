/**
 * Dashboard Main Entry Point (ES6)
 * Orchestrates initialization of all modules
 * This file is the entry point for the bundler
 */

// =============================================================================
// CORE IMPORTS - Always loaded
// =============================================================================

import * as core from './core.js';
import * as api from './api.js';
import * as socket from './socket.js';
import * as tabs from './tabs.js';
import * as modals from './modals.js';
import * as charts from './charts.js';
import * as chat from './chat.js';
import * as widgets from './widgets.js';
import * as exploration from './exploration.js';
import * as decisionGraph from './decision-graph.js';
import * as recovery from './recovery.js';
import * as investigation from './modules/investigation.js';
import * as dragDrop from './drag-drop.js';
import * as queue from './modules/queue.js';
import * as backupStatus from './modules/backup-status.js';
import * as activityFeed from './modules/activity-feed.js';
import * as semantic from './modules/semantic.js';

// Import new socket functions for WebSocket push
import {
    subscribeToRoom,
    unsubscribeFromRoom,
    registerHandler,
    getConnectionState,
    forceReconnect,
    loadInitialChatHistory
} from './socket.js';

// =============================================================================
// CRITICAL WIDGET IMPORTS - Loaded directly (NOT lazy) for immediate availability
// These functions are called during page initialization on the AtlasForge tab
// =============================================================================

import {
    refreshKBAnalyticsWidget,
    openKBThemeModal,
    closeKBThemeModal,
    handleKBThemeKeyDown,
    refreshRecommendations,
    generateNewRecommendations,
    showRecommendationDetail,
    closeRecommendationModal,
    acceptRecommendation,
    rejectRecommendation,
    convertToMission,
    queueRecommendation,
    deleteAllRecommendations,
    // New pagination/filter functions
    initRecommendationsWidget,
    goToRecPage,
    applyRecFilters,
    debounceRecSearch,
    changeRecPerPage,
    clearRecFilters,
    // Expanded recommendations modal functions
    openExpandedRecModal,
    closeExpandedRecModal,
    toggleRecSelection,
    selectAllRecs,
    deselectAllRecs,
    clearSelectedRecs,
    goToExpandedRecPage
} from './widgets-critical.js';

// =============================================================================
// EXPOSE TO GLOBAL SCOPE (for onclick handlers in HTML)
// =============================================================================

// Core utilities
window.escapeHtml = core.escapeHtml;
window.showToast = core.showToast;
window.showNotification = core.showNotification;
window.formatBytes = core.formatBytes;
window.formatNumber = core.formatNumber;
window.formatDuration = core.formatDuration;
window.formatTimeAgo = core.formatTimeAgo;
window.formatDate = core.formatDate;
window.copyToClipboard = core.copyToClipboard;
window.downloadJSON = core.downloadJSON;
window.downloadCSV = core.downloadCSV;

// API
window.api = api.api;

// Socket
window.getSocket = socket.getSocket;
window.getWidgetSocket = socket.getWidgetSocket;
window.subscribeToRoom = subscribeToRoom;
window.unsubscribeFromRoom = unsubscribeFromRoom;
window.registerHandler = registerHandler;
window.getConnectionState = getConnectionState;
window.forceReconnect = forceReconnect;

// Tabs
window.switchTab = tabs.switchTab;
window.registerTabLoader = tabs.registerTabLoader;
window.showKeyboardShortcuts = tabs.showKeyboardShortcuts;

// Modals
window.openMissionModal = modals.openMissionModal;
window.closeMissionModal = modals.closeMissionModal;
window.copyMission = modals.copyMission;
window.loadRecommendations = modals.loadRecommendations;
window.openRecModal = modals.openRecModal;
window.closeRecModal = modals.closeRecModal;
window.deleteRecommendation = modals.deleteRecommendation;
window.setMissionFromRec = modals.setMissionFromRec;
window.queueMissionSuggestion = modals.queueMissionSuggestion;
window.closeGlassboxModal = modals.closeGlassboxModal;
window.closeRepoLogModal = modals.closeRepoLogModal;
// Edit mode functions
window.toggleEditMode = modals.toggleEditMode;
window.saveRecChanges = modals.saveRecChanges;
window.cancelEditMode = modals.cancelEditMode;
// Similarity audit functions
window.openSimilarityAudit = modals.openSimilarityAudit;
window.closeSimilarityModal = modals.closeSimilarityModal;
window.selectGroupForMerge = modals.selectGroupForMerge;
window.toggleMergeSelection = modals.toggleMergeSelection;
// Merge functions
window.openMergeModal = modals.openMergeModal;
window.closeMergeModal = modals.closeMergeModal;
window.executeMerge = modals.executeMerge;
// Sorting and health functions
window.sortRecommendations = modals.sortRecommendations;
window.loadHealthSummary = modals.loadHealthSummary;
// Merge candidates auto-prompt functions
window.showMergeCandidatesPrompt = modals.showMergeCandidatesPrompt;
window.closeMergeCandidatesModal = modals.closeMergeCandidatesModal;
window.proceedToMerge = modals.proceedToMerge;
window.addNewSuggestion = modals.addNewSuggestion;
// Filter, pagination, and quick-add functions (Cycle 3)
window.filterByTag = modals.filterByTag;
window.filterByHealth = modals.filterByHealth;
window.clearAllFilters = modals.clearAllFilters;
window.submitQuickAdd = modals.submitQuickAdd;
window.refreshAllTags = modals.refreshAllTags;
window.goToRecPage = modals.goToRecPage;

// Charts
window.chartColors = charts.chartColors;
window.chartColorPalette = charts.chartColorPalette;
window.destroyChart = charts.destroyChart;
window.createBarChart = charts.createBarChart;
window.createLineChart = charts.createLineChart;
window.createDonutChart = charts.createDonutChart;

// Chat
window.addMessage = chat.addMessage;
window.copyMessageText = chat.copyMessageText;
window.clearChat = chat.clearChat;

// Widgets
window.toggleCard = widgets.toggleCard;
window.loadCardStates = widgets.loadCardStates;
window.renderJournalEntries = widgets.renderJournalEntries;
window.toggleJournalEntry = widgets.toggleJournalEntry;
window.expandAllJournal = widgets.expandAllJournal;
window.collapseAllJournal = widgets.collapseAllJournal;
window.startClaude = widgets.startClaude;
window.stopClaude = widgets.stopClaude;
window.setMission = widgets.setMission;
window.resetMission = widgets.resetMission;
window.queueMission = widgets.queueMission;
window.loadFiles = widgets.loadFiles;
window.updateStageIndicator = widgets.updateStageIndicator;
window.updateStatusBar = widgets.updateStatusBar;
window.updateAtlasForgeServiceStatus = widgets.updateAtlasForgeServiceStatus;
window.updateInvestigationServiceStatus = widgets.updateInvestigationServiceStatus;
window.updateTerminalServiceStatus = widgets.updateTerminalServiceStatus;
window.refreshServiceStatuses = widgets.refreshServiceStatuses;
window.showRestartModal = widgets.showRestartModal;
window.hideRestartModal = widgets.hideRestartModal;
window.confirmRestart = widgets.confirmRestart;
window.refresh = widgets.refresh;
window.refreshAtlasForgeWidgets = widgets.refreshAtlasForgeWidgets;
window.refreshAnalyticsWidget = widgets.refreshAnalyticsWidget;
window.refreshFullAnalytics = widgets.refreshFullAnalytics;
window.showMissionAnalytics = widgets.showMissionAnalytics;
window.applyAnalyticsPeriodFilter = widgets.applyAnalyticsPeriodFilter;
window.exportAnalyticsCSV = widgets.exportAnalyticsCSV;
window.openMissionAnalyticsModal = widgets.openMissionAnalyticsModal;
window.closeMissionAnalyticsModal = widgets.closeMissionAnalyticsModal;

// Exploration
window.GraphRenderer = exploration.GraphRenderer;
window.refreshGraphVisualization = exploration.refreshGraphVisualization;
window.searchInsights = exploration.searchInsights;

// Decision Graph
window.refreshDecisionGraph = decisionGraph.refreshDecisionGraph;
window.showDecisionNodeDetails = decisionGraph.showDecisionNodeDetails;

// Recovery
window.checkForRecovery = recovery.checkForRecovery;
window.showRecoveryModal = recovery.showRecoveryModal;
window.closeRecoveryModal = recovery.closeRecoveryModal;
window.dismissRecovery = recovery.dismissRecovery;
window.dismissRecoveryFromModal = recovery.dismissRecoveryFromModal;

// Drag-Drop Widget Layout
window.resetWidgetLayout = dragDrop.resetToDefault;
window.applyRecovery = recovery.applyRecovery;

// Investigation Mode
window.toggleInvestigationMode = investigation.toggleInvestigationMode;
window.startInvestigation = investigation.startInvestigation;
window.stopInvestigation = investigation.stopInvestigation;
window.showInvestigationStatus = investigation.showInvestigationStatus;
window.hideInvestigationStatus = investigation.hideInvestigationStatus;
window.viewInvestigationReport = investigation.viewInvestigationReport;
window.closeInvestigationReportModal = investigation.closeInvestigationReportModal;
window.copyInvestigationReport = investigation.copyInvestigationReport;
window.showInvestigationBanner = investigation.showInvestigationBanner;
window.hideInvestigationBanner = investigation.hideInvestigationBanner;
window.scrollToInvestigationCard = investigation.scrollToInvestigationCard;
window.isInvestigationActive = investigation.isInvestigationActive;

// Drift Monitoring Widget

// Predictive Drift Prevention Widget

// Validation Stats Widget

// Source Quality

// Mission Queue Widget
window.initQueueWidget = queue.initQueueWidget;
window.refreshQueueWidget = queue.refreshQueueWidget;
window.addToQueue = queue.addToQueue;
window.removeQueueItem = queue.removeFromQueue;
window.clearQueue = queue.clearQueue;
window.moveQueueItem = queue.moveQueueItem;

// GitHub Status Widget

// GitHub Activity Feed Widget
window.initActivityFeed = activityFeed.initActivityFeed;
window.refreshActivityFeed = activityFeed.refreshActivityFeed;
window.startNextFromQueue = queue.startNextFromQueue;

// Semantic Search Widgets
window.initSemanticWidgets = semantic.initSemanticWidgets;
window.refreshSemanticWidgets = semantic.refreshSemanticWidgets;
window.refreshSemanticMiniWidgets = semantic.refreshSemanticMiniWidgets;
window.initSemanticWebSocketHandlers = semantic.initSemanticWebSocketHandlers;
window.showSemanticToast = semantic.showSemanticToast;
window.performSemanticSearch = async function(event) {
    if (event) event.preventDefault();
    const queryInput = document.getElementById('semantic-search-query');
    const resultsContainer = document.getElementById('semantic-search-results');
    if (!queryInput || !resultsContainer) return;

    const query = queryInput.value.trim();
    if (!query) {
        resultsContainer.innerHTML = '<div class="empty-state">Enter a search query</div>';
        return;
    }

    resultsContainer.innerHTML = '<div class="loading">Searching...</div>';
    try {
        const results = await semantic.SemanticAPI.search(query, 10);
        if (!results || results.length === 0) {
            resultsContainer.innerHTML = '<div class="empty-state">No results found</div>';
            return;
        }
        resultsContainer.innerHTML = results.map(r => `
            <div class="search-result-item">
                <div class="result-title">${window.escapeHtml(r.title || r.id || 'Untitled')}</div>
                <div class="result-score">Score: ${(r.score || 0).toFixed(3)}</div>
                <div class="result-snippet">${window.escapeHtml(r.snippet || r.content?.substring(0, 200) || '')}</div>
            </div>
        `).join('');
    } catch (err) {
        resultsContainer.innerHTML = `<div class="error">Search failed: ${err.message}</div>`;
    }
};
window.quickAddToQueue = async function() {
    const input = document.getElementById('queue-add-input');
    if (!input || !input.value.trim()) return;
    await queue.addToQueue(input.value.trim(), { source: 'dashboard' });
    input.value = '';
};

// =============================================================================
// CRITICAL WIDGET FUNCTIONS - Available immediately (NOT lazy-loaded)
// These are called during page initialization on AtlasForge tab
// =============================================================================

window.refreshKBAnalyticsWidget = refreshKBAnalyticsWidget;
window.openKBThemeModal = openKBThemeModal;
window.closeKBThemeModal = closeKBThemeModal;
window.handleKBThemeKeyDown = handleKBThemeKeyDown;
window.refreshRecommendations = refreshRecommendations;
window.generateNewRecommendations = generateNewRecommendations;
window.deleteAllRecommendations = deleteAllRecommendations;
window.showRecommendationDetail = showRecommendationDetail;
window.closeRecommendationModal = closeRecommendationModal;
window.acceptRecommendation = acceptRecommendation;
window.rejectRecommendation = rejectRecommendation;
window.convertToMission = convertToMission;
window.queueRecommendation = queueRecommendation;
// Pagination/filter functions
window.initRecommendationsWidget = initRecommendationsWidget;
window.goToRecPage = goToRecPage;
window.applyRecFilters = applyRecFilters;
window.debounceRecSearch = debounceRecSearch;
window.changeRecPerPage = changeRecPerPage;
window.clearRecFilters = clearRecFilters;
// Expanded recommendations modal functions
window.openExpandedRecModal = openExpandedRecModal;
window.closeExpandedRecModal = closeExpandedRecModal;
window.toggleRecSelection = toggleRecSelection;
window.selectAllRecs = selectAllRecs;
window.deselectAllRecs = deselectAllRecs;
window.clearSelectedRecs = clearSelectedRecs;
window.goToExpandedRecPage = goToExpandedRecPage;

// =============================================================================
// LAZY LOADING FOR TAB-SPECIFIC MODULES
// =============================================================================

// Module cache for lazy-loaded modules
const loadedModules = new Map();

/**
 * Lazy load a module
 * @param {string} moduleName - Module name
 * @returns {Promise} - Module promise
 */
async function lazyLoad(moduleName) {
    if (loadedModules.has(moduleName)) {
        return loadedModules.get(moduleName);
    }

    const startTime = performance.now();
    let module;

    switch (moduleName) {
        case 'glassbox':
            module = await import('./modules/glassbox.js');
            // Initialize module (it exposes its own window functions)
            if (module.init) module.init();
            // Expose tab-specific entry points to global (not widget functions - those use wrappers)
            window.loadGlassboxTabData = module.loadGlassboxTabData;
            window.selectGlassboxMission = module.selectGlassboxMission;
            window.viewAgentTranscript = module.viewAgentTranscript;
            window.closeGlassboxModal = module.closeGlassboxModal;
            window.refreshGlassbox = module.refreshGlassbox;
            window.glassboxSearch = module.glassboxSearch;
            window.glassboxDateFilter = module.glassboxDateFilter;
            window.glassboxPrevPage = module.glassboxPrevPage;
            window.glassboxNextPage = module.glassboxNextPage;
            break;

        case 'missionlogs':
            module = await import('./modules/missionlogs.js');
            // Expose to global for onclick handlers
            window.loadMissionLogsTabData = module.loadMissionLogsTabData;
            window.selectMissionLog = module.selectMissionLog;
            window.searchMissionLogs = module.searchMissionLogs;
            window.refreshMissionLogs = module.refreshMissionLogs;
            window.exportMissionLog = module.exportMissionLog;
            window.viewMissionLogRaw = module.viewMissionLogRaw;
            break;

        case 'lessons':
            module = await import('./modules/lessons.js');
            // Expose to global
            window.loadLessonsTabData = module.loadLessonsTabData;
            window.loadAllLessons = module.loadAllLessons;
            window.searchLessons = module.searchLessons;
            window.showLearningDetails = module.showLearningDetails;
            window.showLessonsSubtab = module.showLessonsSubtab;
            window.loadLearningChains = module.loadLearningChains;
            window.toggleChain = module.toggleChain;
            window.loadClusters = module.loadClusters;
            window.toggleCluster = module.toggleCluster;
            window.loadDuplicates = module.loadDuplicates;
            window.mergeDuplicates = module.mergeDuplicates;
            window.mergeAllDuplicates = module.mergeAllDuplicates;
            window.rebuildIndex = module.rebuildIndex;
            window.exportLearnings = module.exportLearnings;
            window.toggleAnalytics = module.toggleAnalytics;
            window.toggleBatchDeleteMode = module.toggleBatchDeleteMode;
            window.exitBatchMode = module.exitBatchMode;
            window.toggleLearningSelection = module.toggleLearningSelection;
            window.selectAllLearnings = module.selectAllLearnings;
            window.clearSelection = module.clearSelection;
            window.showBatchDeleteConfirm = module.showBatchDeleteConfirm;
            window.closeBatchDeleteModal = module.closeBatchDeleteModal;
            window.confirmBatchDelete = module.confirmBatchDelete;
            break;

        case 'kb-analytics':
            module = await import('./modules/kb-analytics.js');
            // Expose to global
            window.loadKBAnalyticsTabData = module.loadKBAnalyticsTabData;
            window.refreshKBAnalyticsWidget = module.refreshKBAnalyticsWidget;
            window.openKBThemeModal = module.openKBThemeModal;
            window.closeKBThemeModal = module.closeKBThemeModal;
            window.handleKBThemeKeyDown = module.handleKBThemeKeyDown;
            window.applyKBTimeFilter = module.applyKBTimeFilter;
            window.toggleMissionComparison = module.toggleMissionComparison;
            window.removeMissionComparison = module.removeMissionComparison;
            window.clearMissionComparison = module.clearMissionComparison;
            // Investigation-KB integration functions
            window.applyKBSourceFilter = module.applyKBSourceFilter;
            window.refreshRecommendations = module.refreshRecommendations;
            window.showRecommendationDetail = module.showRecommendationDetail;
            window.closeRecommendationModal = module.closeRecommendationModal;
            window.acceptRecommendation = module.acceptRecommendation;
            window.rejectRecommendation = module.rejectRecommendation;
            window.convertToMission = module.convertToMission;
            window.generateNewRecommendations = module.generateNewRecommendations;
            window.deleteAllRecommendations = module.deleteAllRecommendations;
            break;

        case 'investigation-history':
            module = await import('./modules/investigation-history.js');
            // Expose to global
            window.initInvestigationHistory = module.initInvestigationHistory;
            window.refreshInvestigationHistory = module.refreshInvestigationHistory;
            window.loadInvestigations = module.loadInvestigations;
            window.loadInvestigationStats = module.loadInvestigationStats;
            // Note: Other functions are exposed directly to window in the module
            break;

        default:
            throw new Error(`Unknown module: ${moduleName}`);
    }

    loadedModules.set(moduleName, module);
    const elapsed = performance.now() - startTime;
    console.log(`Lazy loaded ${moduleName} in ${elapsed.toFixed(1)}ms`);

    return module;
}

// Register lazy loaders for tabs
tabs.registerTabLoader('glassbox', () => lazyLoad('glassbox'));
tabs.registerTabLoader('missionlogs', () => lazyLoad('missionlogs'));
tabs.registerTabLoader('bugbounty', () => lazyLoad('bugbounty'));
tabs.registerTabLoader('narrative', () => lazyLoad('narrative'));
tabs.registerTabLoader('lessons', () => lazyLoad('lessons'));
tabs.registerTabLoader('git-analytics', () => lazyLoad('git-analytics'));
tabs.registerTabLoader('kb-analytics', () => lazyLoad('kb-analytics'));
tabs.registerTabLoader('investigations', () => lazyLoad('investigation-history'));
tabs.registerTabLoader('semantic', () => Promise.resolve(semantic));

// =============================================================================
// ATLASFORGE SIDEBAR GLASSBOX DROPDOWN (loaded separately from lazy-loaded module)
// =============================================================================

async function populateAtlasForgeSidebarGlassbox() {
    try {
        const response = await api.api('/api/glassbox/missions?limit=100');
        const missions = response.missions || [];
        const total = response.pagination?.total || missions.length;

        // Update the badge count
        const countBadge = document.getElementById('glassbox-mission-count');
        if (countBadge) countBadge.textContent = total;

        // Populate the sidebar dropdown with token counts
        const sidebarSelect = document.getElementById('glassbox-mission-select');
        if (sidebarSelect) {
            sidebarSelect.innerHTML = '<option value="">Select archived mission...</option>';
            missions.forEach(m => {
                const tokens = core.formatNumber(m.total_tokens || 0);
                sidebarSelect.innerHTML += `<option value="${m.mission_id}">${m.mission_id} (${tokens} tokens)</option>`;
            });
        }

        console.log(`AtlasForge sidebar GlassBox populated with ${total} missions`);
    } catch (e) {
        console.log('Could not populate AtlasForge sidebar GlassBox:', e);
    }
}

// Wrapper for loadGlassboxMission that ensures module is loaded first
async function loadGlassboxMissionWrapper() {
    // Load the glassbox module if not already loaded
    const module = await lazyLoad('glassbox');
    // Call the actual function from the module directly
    if (module && module.loadGlassboxMission) {
        await module.loadGlassboxMission();
    }
}

// Expose wrapper to window immediately (will be available before module loads)
window.loadGlassboxMission = loadGlassboxMissionWrapper;
window.closeGlassboxWidgetPopup = async () => {
    const module = loadedModules.get('glassbox');
    if (module && module.closeGlassboxWidgetPopup) {
        module.closeGlassboxWidgetPopup();
    }
};
window.viewGlassboxMissionInTab = async () => {
    const module = loadedModules.get('glassbox');
    if (module && module.viewGlassboxMissionInTab) {
        module.viewGlassboxMissionInTab();
    }
};

// =============================================================================
// INITIALIZATION
// =============================================================================

document.addEventListener('DOMContentLoaded', async function() {
    const startTime = performance.now();
    console.log('Dashboard initializing...');

    // Initialize sockets
    socket.initializeSockets();

    // Initialize WebSocket event handlers for widgets (real-time push updates)
    if (typeof widgets.initWebSocketHandlers === 'function') {
        widgets.initWebSocketHandlers();
        console.log('WebSocket handlers initialized for widget updates');
    }

    // Load initial chat history (handles race condition with WebSocket)
    socket.loadInitialChatHistory();

    // Load saved card states
    widgets.loadCardStates();

    // Initialize drag-drop for widget columns
    dragDrop.initDragDrop();

    // Initialize tabs
    tabs.initTabs();

    // Setup insight search
    exploration.setupInsightSearch();

    // Initial data refresh
    await widgets.refresh();

    // Populate AtlasForge sidebar GlassBox dropdown (independent of lazy-loaded module)
    populateAtlasForgeSidebarGlassbox();

    // Check for crash recovery
    recovery.checkForRecovery();

    // Check for running investigation
    investigation.checkForRunningInvestigation();

    // Initialize drift monitoring widget

    // Initialize drift intervention system

    // Initialize predictive drift prevention widget

    // Initialize validation stats widget

    // Initialize source quality widget

    // Initialize mission queue widget
    queue.initQueueWidget();

    // Initialize project name suggestion on mission input
    if (typeof widgets.initProjectNameSuggestion === 'function') {
        widgets.initProjectNameSuggestion();
    }

    // Initialize backup status widget
    backupStatus.initBackupStatus();

    // Initialize GitHub status widget

    // Initialize GitHub activity feed widget
    activityFeed.initActivityFeed();

    // Initialize keyboard shortcuts
    initKeyboardShortcuts();

    // Set up periodic refreshes
    setupPeriodicRefreshes();

    // Register service worker
    registerServiceWorker();

    // Initialize mobile scroll dots
    initMobileScrollDots();

    // Initialize modal z-index fix for mobile
    initMobileModalFix();

    const elapsed = performance.now() - startTime;
    console.log(`Dashboard initialized in ${elapsed.toFixed(0)}ms`);
});

// =============================================================================
// MOBILE MODAL Z-INDEX FIX
// =============================================================================

/**
 * Initialize MutationObserver to automatically add/remove modal-open class
 * This ensures modals stay on top of scrolled columns on mobile
 */
function initMobileModalFix() {
    // Only needed on mobile devices
    if (window.innerWidth > 600) return;

    // Function to check if any modal is visible
    function updateModalOpenClass() {
        const visibleModals = document.querySelectorAll(
            '.modal.show, .modal[style*="display: flex"], .modal[style*="display:flex"]'
        );
        if (visibleModals.length > 0) {
            document.body.classList.add('modal-open');
        } else {
            document.body.classList.remove('modal-open');
        }
    }

    // Observe all modals for style/class changes
    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            if (mutation.type === 'attributes' &&
                (mutation.attributeName === 'style' || mutation.attributeName === 'class')) {
                updateModalOpenClass();
                break;
            }
        }
    });

    // Observe all existing modals
    document.querySelectorAll('.modal').forEach(modal => {
        observer.observe(modal, {
            attributes: true,
            attributeFilter: ['style', 'class']
        });
    });

    // Also observe for new modals being added to DOM
    const bodyObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            if (mutation.addedNodes.length > 0) {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === Node.ELEMENT_NODE && node.classList?.contains('modal')) {
                        observer.observe(node, {
                            attributes: true,
                            attributeFilter: ['style', 'class']
                        });
                    }
                });
            }
        }
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });

    // Run initial check
    updateModalOpenClass();

    console.log('Mobile modal z-index fix initialized');
}

// =============================================================================
// KEYBOARD SHORTCUTS
// =============================================================================

/**
 * Initialize global keyboard shortcuts
 * - Ctrl+Shift+S: Create manual snapshot
 */
function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Ctrl+Shift+S - Quick Snapshot
        if (e.ctrlKey && e.shiftKey && (e.key === 'S' || e.key === 's')) {
            e.preventDefault();
            e.stopPropagation();

            // Check if backupStatusModule is available
            if (window.backupStatusModule && typeof window.backupStatusModule.createSnapshot === 'function') {
                console.log('[Keyboard] Ctrl+Shift+S pressed - creating snapshot');
                window.backupStatusModule.createSnapshot();
            } else {
                console.warn('[Keyboard] backupStatusModule not available');
                core.showToast('Backup module not ready', 'warning');
            }
            return false;
        }
    });

    console.log('Keyboard shortcuts initialized (Ctrl+Shift+S for snapshot)');
}

// =============================================================================
// MOBILE SCROLL DOTS
// =============================================================================

/**
 * Initialize mobile scroll indicator dots
 * Shows which column is currently in view and allows tapping to navigate
 */
function initMobileScrollDots() {
    // On mobile (<= 600px), the scroll container is #atlasforge-tab, not .container
    // because CSS sets overflow-x: scroll on #atlasforge-tab
    const atlasforgeTab = document.getElementById('atlasforge-tab');
    const container = document.querySelector('#atlasforge-tab .container');
    const dotsContainer = document.getElementById('mobile-scroll-dots');
    const dots = document.querySelectorAll('#mobile-scroll-dots .scroll-dot');

    if (!atlasforgeTab || !container || !dotsContainer || dots.length === 0) {
        console.log('Mobile scroll dots: missing elements', {
            atlasforgeTab: !!atlasforgeTab,
            container: !!container,
            dotsContainer: !!dotsContainer,
            dots: dots.length
        });
        return;
    }

    // Get the scroll container - #atlasforge-tab on mobile, .container on tablet
    function getScrollContainer() {
        return window.innerWidth <= 600 ? atlasforgeTab : container;
    }

    // Only show dots on mobile (handled by CSS, but also check here)
    function updateDotsVisibility() {
        const isMobile = window.innerWidth <= 600;
        dotsContainer.style.display = isMobile ? 'flex' : 'none';
    }

    // Update active dot based on scroll position
    function updateActiveDot() {
        if (window.innerWidth > 600) return; // Skip on desktop

        const scrollContainer = getScrollContainer();
        const scrollLeft = scrollContainer.scrollLeft;
        // Each column is approximately 1 viewport width on mobile
        const columnWidth = window.innerWidth;
        const activeIndex = Math.round(scrollLeft / columnWidth);

        dots.forEach((dot, index) => {
            const isActive = index === Math.min(activeIndex, 2);
            dot.classList.toggle('active', isActive);
            // Update ARIA attributes for accessibility
            dot.setAttribute('aria-selected', isActive ? 'true' : 'false');
            dot.setAttribute('tabindex', isActive ? '0' : '-1');
        });
    }

    // Scroll to a specific column
    window.scrollToColumn = function(columnIndex) {
        if (window.innerWidth > 600) return; // Skip on desktop

        const scrollContainer = getScrollContainer();
        // Each column is 100vw (viewport width) on mobile
        const columnWidth = window.innerWidth;
        scrollContainer.scrollTo({
            left: columnWidth * columnIndex,
            behavior: 'smooth'
        });
    };

    // Listen for scroll events on BOTH containers (handles resize/orientation change)
    atlasforgeTab.addEventListener('scroll', updateActiveDot, { passive: true });
    container.addEventListener('scroll', updateActiveDot, { passive: true });

    // Listen for resize events
    window.addEventListener('resize', () => {
        updateDotsVisibility();
        updateActiveDot();
    });

    // Initial state
    updateDotsVisibility();
    updateActiveDot();

    console.log('Mobile scroll dots initialized');
}

// =============================================================================
// PERIODIC REFRESHES (WebSocket-aware)
// =============================================================================

let pollingIntervals = [];
let wsPollingMode = false;

function setupPeriodicRefreshes() {
    // Check if WebSocket is connected - if so, reduce polling
    const connectionState = getConnectionState();

    if (connectionState.isConnected) {
        // WebSocket connected - minimal polling for backup
        console.log('WebSocket connected - using push updates with minimal polling backup');
        setupWebSocketHandlers();
        setupMinimalPolling();
    } else {
        // No WebSocket - use full polling
        console.log('WebSocket not connected - using full polling mode');
        setupFullPolling();
    }

    // Monitor connection state changes
    registerHandler('connection_status', (status) => {
        if (status.status === 'connected' && wsPollingMode === true) {
            console.log('WebSocket connected - switching to push mode');
            clearPollingIntervals();
            setupMinimalPolling();
            wsPollingMode = false;
        } else if (status.status !== 'connected' && wsPollingMode === false) {
            console.log('WebSocket disconnected - switching to polling mode');
            clearPollingIntervals();
            setupFullPolling();
            wsPollingMode = true;
        }
    });
}

function setupWebSocketHandlers() {
    // Register handlers for WebSocket push updates
    registerHandler('mission_status', (data) => {
        widgets.updateStatusBar(data);
        widgets.updateStageIndicator(data.rd_stage);
    });

    registerHandler('journal', (data) => {
        if (data.entries && Array.isArray(data.entries)) {
            window.renderJournalEntries && window.renderJournalEntries(data.entries);
        }
    });

    registerHandler('atlasforge_stats', (data) => {
        updateAtlasForgeWidgetsFromPush(data);
    });

    registerHandler('analytics', (data) => {
        updateAnalyticsFromPush(data);
    });

    // Investigation mode real-time updates
    registerHandler('investigation_progress', (data) => {
        investigation.handleInvestigationProgress(data);
    });

    registerHandler('investigation_complete', (data) => {
        investigation.handleInvestigationComplete(data);
    });
}

function updateAtlasForgeWidgetsFromPush(data) {
    if (data.exploration) {
        const setEl = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };

        const fileCount = (data.exploration.nodes_by_type || {}).file || 0;
        setEl('atlasforge-files-count', fileCount);
        setEl('atlasforge-insights-count', data.exploration.total_insights || 0);
        setEl('atlasforge-edges-count', data.exploration.total_edges || 0);
    }

    if (data.coverage_pct !== undefined) {
        const coverage = data.coverage_pct;
        const pctEl = document.getElementById('atlasforge-coverage-pct');
        const barEl = document.getElementById('atlasforge-coverage-bar');
        if (pctEl) pctEl.textContent = coverage + '%';
        if (barEl) barEl.style.width = coverage + '%';
    }
}

function updateAnalyticsFromPush(data) {
    if (!data) return;

    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setEl('analytics-tokens', core.formatNumber(data.tokens || 0));
    setEl('analytics-cost', '$' + (data.cost || 0).toFixed(4));
}

function setupMinimalPolling() {
    wsPollingMode = false;

    // Initial calls for git widgets (even in WebSocket mode, we need the first load)

    // Initial call for analytics widget (must be called on page load)
    widgets.refreshAnalyticsWidget();

    // Very infrequent polling as backup when WebSocket is connected
    // These only trigger if WebSocket hasn't sent updates recently

    pollingIntervals.push(setInterval(() => {
        // Only refresh if no WebSocket update in last 30 seconds
        const state = getConnectionState();
        const timeSinceUpdate = Date.now() - state.lastUpdate;
        if (timeSinceUpdate > 30000) {
            widgets.refresh();
        }
    }, 30000));

    // Analytics backup polling
    pollingIntervals.push(setInterval(() => {
        widgets.refreshAnalyticsWidget();
    }, 60000));

    // Git widgets backup polling (infrequent when WebSocket is connected)
    pollingIntervals.push(setInterval(() => {
        const state = getConnectionState();
        const timeSinceUpdate = Date.now() - state.lastUpdate;
        if (timeSinceUpdate > 30000) {
        }
    }, 60000));
}

function setupFullPolling() {
    wsPollingMode = true;

    // Main status refresh - every 5 seconds
    pollingIntervals.push(setInterval(widgets.refresh, 5000));

    // Analytics widget - every 30 seconds
    widgets.refreshAnalyticsWidget();
    pollingIntervals.push(setInterval(widgets.refreshAnalyticsWidget, 30000));

    // Decision graph refresh - every 10 seconds
    decisionGraph.refreshDecisionGraph();
    pollingIntervals.push(setInterval(decisionGraph.refreshDecisionGraph, 10000));

    // Git status widget - every 15 seconds

    // Multi-repo status - every 30 seconds

    // Git analytics - every 60 seconds
}

function clearPollingIntervals() {
    pollingIntervals.forEach(id => clearInterval(id));
    pollingIntervals = [];
}

// =============================================================================
// SERVICE WORKER REGISTRATION
// =============================================================================

async function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        try {
            const registration = await navigator.serviceWorker.register('/static/sw.js', {
                scope: '/'
            });
            console.log('Service Worker registered:', registration.scope);

            // Check for updates
            registration.addEventListener('updatefound', () => {
                const newWorker = registration.installing;
                newWorker.addEventListener('statechange', () => {
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        core.showToast('New version available! Refresh to update.', 5000);
                    }
                });
            });
        } catch (e) {
            console.log('Service Worker registration failed:', e);
        }
    }
}

// =============================================================================
// PERFORMANCE MONITORING
// =============================================================================

// Log initial page load metrics
window.addEventListener('load', () => {
    if (window.performance) {
        const timing = window.performance.timing;
        const pageLoad = timing.loadEventEnd - timing.navigationStart;
        const domReady = timing.domContentLoadedEventEnd - timing.navigationStart;
        console.log(`Page load: ${pageLoad}ms, DOM ready: ${domReady}ms`);
    }
});

export { lazyLoad };
