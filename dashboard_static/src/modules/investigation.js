/**
 * Investigation Mode Module
 *
 * Handles UI interactions for investigation mode - a simplified single-cycle
 * research workflow that runs parallel subagents.
 */

import { api } from '../api.js';
import { showToast } from '../core.js';

// Track current investigation
let currentInvestigationId = null;
let investigationPolling = null;
let isInvestigationRunning = false;

// Store scroll position when modal opens (for mobile)
let savedScrollX = 0;
let savedScrollY = 0;

// Auto-archive state
let autoArchiveTimer = null;
let countdownInterval = null;
let isPinned = false;
let investigationSettings = {
    auto_archive_enabled: true,
    auto_archive_delay_seconds: 5
};

/**
 * Toggle between R&D mode and Investigation mode
 */
export function toggleInvestigationMode() {
    const checkbox = document.getElementById('investigation-mode-checkbox');
    const rdControls = document.getElementById('rd-mode-controls');
    const investigationControls = document.getElementById('investigation-mode-controls');
    const hint = document.getElementById('investigation-mode-hint');

    if (checkbox.checked) {
        // Investigation mode ON
        rdControls.style.display = 'none';
        investigationControls.style.display = 'block';
        hint.textContent = 'Single-cycle deep dive research with parallel subagents';
        hint.style.color = 'var(--accent)';
    } else {
        // Standard R&D mode
        rdControls.style.display = 'block';
        investigationControls.style.display = 'none';
        hint.textContent = 'Single-cycle deep dive research';
        hint.style.color = 'var(--text-dim)';
    }
}

/**
 * Start a new investigation
 */
export async function startInvestigation() {
    // Check if investigation is already running (lock-out check)
    if (isInvestigationRunning) {
        showToast('Investigation already running. Please wait or stop it first.', 'error');
        return;
    }

    const queryInput = document.getElementById('mission-input');
    const query = queryInput.value.trim();

    if (!query) {
        showToast('Please enter an investigation query');
        return;
    }

    const subagentsSelect = document.getElementById('investigation-subagents');
    let subagents;
    if (subagentsSelect && subagentsSelect.value === 'custom') {
        const customInput = document.getElementById('investigation-subagents-custom');
        subagents = parseInt(customInput ? customInput.value : 5) || 5;
    } else {
        subagents = parseInt(subagentsSelect ? subagentsSelect.value : '5') || 5;
    }
    const timeout = parseInt(document.getElementById('investigation-timeout').value) || 10;

    try {
        const result = await api('/api/investigation/start', 'POST', {
            query: query,
            max_subagents: subagents,
            timeout_minutes: timeout
        });

        if (result.success) {
            currentInvestigationId = result.investigation_id;
            isInvestigationRunning = true;

            // Clear the input field on successful start
            queryInput.value = '';

            showToast(`Investigation started: ${result.investigation_id}`);

            // Show status card and banner
            showInvestigationStatus(result.investigation_id);
            showInvestigationBanner(result.investigation_id, 'Starting...');

            // Update header service status indicator
            if (typeof window.updateInvestigationServiceStatus === 'function') {
                window.updateInvestigationServiceStatus(true, 'pending');
            }

            // Start polling for updates
            startInvestigationPolling(result.investigation_id);

            // Update button states
            document.getElementById('stop-investigation-btn').style.display = 'inline-block';

            // Disable the start button to prevent double-starts
            updateInvestigationControlsState(true);
        } else {
            showToast(result.message || 'Failed to start investigation', 'error');
        }
    } catch (err) {
        console.error('Failed to start investigation:', err);
        showToast('Failed to start investigation: ' + err.message, 'error');
    }
}

/**
 * Stop the current investigation
 */
export async function stopInvestigation() {
    if (!currentInvestigationId) {
        showToast('No investigation running');
        return;
    }

    try {
        const result = await api(`/api/investigation/stop/${currentInvestigationId}`, 'POST');

        if (result.success) {
            showToast('Investigation stop requested');
            stopInvestigationPolling();
            isInvestigationRunning = false;

            // Hide banner and re-enable controls
            hideInvestigationBanner();
            updateInvestigationControlsState(false);

            // Update header service status indicator
            if (typeof window.updateInvestigationServiceStatus === 'function') {
                window.updateInvestigationServiceStatus(false, null);
            }

            // Hide stop button
            document.getElementById('stop-investigation-btn').style.display = 'none';
        } else {
            showToast(result.message || 'Failed to stop investigation', 'error');
        }
    } catch (err) {
        console.error('Failed to stop investigation:', err);
        showToast('Failed to stop investigation', 'error');
    }
}

/**
 * Show the investigation status card
 */
export function showInvestigationStatus(investigationId) {
    const card = document.getElementById('investigation-status-card');
    card.style.display = 'block';

    document.getElementById('investigation-id').textContent = investigationId;
    document.getElementById('investigation-status').textContent = 'Starting...';
    document.getElementById('investigation-progress').textContent = 'Initializing';
    document.getElementById('investigation-log').innerHTML = '';
    document.getElementById('view-report-btn').style.display = 'none';
}

/**
 * Hide the investigation status card
 */
export function hideInvestigationStatus() {
    const card = document.getElementById('investigation-status-card');
    card.style.display = 'none';
    stopInvestigationPolling();
}

/**
 * Start polling for investigation updates
 */
function startInvestigationPolling(investigationId) {
    stopInvestigationPolling();  // Clear any existing polling

    investigationPolling = setInterval(async () => {
        try {
            const status = await api(`/api/investigation/status/${investigationId}`);

            if (status.error) {
                console.error('Investigation status error:', status.error);
                return;
            }

            updateInvestigationUI(status);

            // Stop polling if investigation is complete or failed
            if (status.status === 'completed' || status.status === 'failed') {
                stopInvestigationPolling();
                document.getElementById('stop-investigation-btn').style.display = 'none';

                if (status.status === 'completed') {
                    showToast('Investigation completed!');
                    document.getElementById('view-report-btn').style.display = 'inline-block';
                } else {
                    showToast('Investigation failed: ' + (status.error || 'Unknown error'), 'error');
                }
            }
        } catch (err) {
            console.error('Failed to get investigation status:', err);
        }
    }, 2000);  // Poll every 2 seconds
}

/**
 * Stop polling for investigation updates
 */
function stopInvestigationPolling() {
    if (investigationPolling) {
        clearInterval(investigationPolling);
        investigationPolling = null;
    }
}

/**
 * Update the investigation UI with current status
 */
function updateInvestigationUI(status) {
    const statusEl = document.getElementById('investigation-status');
    const progressEl = document.getElementById('investigation-progress');

    // Status with color
    const statusColors = {
        'pending': 'var(--text-dim)',
        'analyzing': 'var(--yellow)',
        'spawning_subagents': 'var(--yellow)',
        'exploring': 'var(--accent)',
        'synthesizing': 'var(--accent)',
        'completed': 'var(--green)',
        'failed': 'var(--red)'
    };

    statusEl.textContent = status.status || 'Unknown';
    statusEl.style.color = statusColors[status.status] || 'var(--text)';

    // Progress
    const subagentCount = status.subagent_count || '?';
    let progressText = status.status;

    switch (status.status) {
        case 'analyzing':
            progressText = 'Lead agent analyzing query...';
            break;
        case 'spawning_subagents':
            progressText = `Spawning ${subagentCount} subagents...`;
            break;
        case 'exploring':
            progressText = `${subagentCount} subagents exploring...`;
            break;
        case 'synthesizing':
            progressText = 'Synthesizing findings...';
            break;
        case 'completed':
            const elapsed = status.elapsed_seconds ? status.elapsed_seconds.toFixed(1) : '?';
            progressText = `Completed in ${elapsed}s`;
            break;
        case 'failed':
            progressText = status.error || 'Failed';
            break;
    }

    progressEl.textContent = progressText;

    // Also update the banner at the top
    updateInvestigationBanner(status.status, progressText);

    // Update the header service status indicator
    const isRunning = status.status !== 'completed' && status.status !== 'failed' && status.status !== 'idle';
    if (typeof window.updateInvestigationServiceStatus === 'function') {
        window.updateInvestigationServiceStatus(isRunning, status.status);
    }

    // Show dismiss button when investigation is completed or failed
    const dismissBtn = document.getElementById('dismiss-investigation-btn');
    if (dismissBtn) {
        if (status.status === 'completed' || status.status === 'failed') {
            dismissBtn.style.display = 'inline-block';
            // Re-enable controls since investigation is done
            updateInvestigationControlsState(false);
            isInvestigationRunning = false;
        } else {
            dismissBtn.style.display = 'none';
        }
    }
}

/**
 * Add a log entry to the investigation log
 */
export function addInvestigationLog(message) {
    const logEl = document.getElementById('investigation-log');
    const timestamp = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.innerHTML = `<span style="color: var(--text-dim)">[${timestamp}]</span> ${message}`;
    logEl.appendChild(entry);
    logEl.scrollTop = logEl.scrollHeight;
}

/**
 * View the investigation report
 */
export async function viewInvestigationReport() {
    if (!currentInvestigationId) {
        showToast('No investigation to view');
        return;
    }

    try {
        const result = await api(`/api/investigation/report/${currentInvestigationId}`);

        if (result.error) {
            showToast(result.error, 'error');
            return;
        }

        // Create and show a modal with the report
        showReportModal(result.report_content, result.investigation_id);
    } catch (err) {
        console.error('Failed to load report:', err);
        showToast('Failed to load report', 'error');
    }
}

// Helper to remove modal-open class only if no modals are visible
function removeModalOpenClass() {
    const visibleModals = document.querySelectorAll('.modal.show, .modal[style*="display: flex"], .modal[style*="display:flex"]');
    if (visibleModals.length === 0) {
        document.body.classList.remove('modal-open');
        window.scrollTo(savedScrollX, savedScrollY);
    }
}

/**
 * Show the investigation report in a modal
 */
function showReportModal(reportContent, investigationId) {
    // Check if modal exists, create if not
    let modal = document.getElementById('investigation-report-modal');

    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'investigation-report-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 900px; max-height: 80vh; overflow-y: auto;">
                <div class="modal-header">
                    <h3 id="investigation-report-title">Investigation Report</h3>
                    <button class="modal-close" onclick="closeInvestigationReportModal()">&times;</button>
                </div>
                <div class="modal-body" id="investigation-report-body" style="white-space: pre-wrap; font-family: monospace; font-size: 0.85em; line-height: 1.5;">
                </div>
                <div class="modal-footer">
                    <button class="btn" onclick="copyInvestigationReport()">Copy Report</button>
                    <button class="btn" onclick="closeInvestigationReportModal()">Close</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    document.getElementById('investigation-report-title').textContent = `Investigation Report: ${investigationId}`;
    document.getElementById('investigation-report-body').textContent = reportContent;

    // Save scroll position before showing modal (for mobile)
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;

    modal.style.display = 'flex';

    // Add modal-open class to body for mobile touch handling
    document.body.classList.add('modal-open');
}

/**
 * Close the investigation report modal
 */
export function closeInvestigationReportModal() {
    const modal = document.getElementById('investigation-report-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    // Remove modal-open class safely (only if no other modals visible)
    removeModalOpenClass();
}

/**
 * Copy the investigation report to clipboard
 */
export async function copyInvestigationReport() {
    const reportBody = document.getElementById('investigation-report-body');
    if (reportBody) {
        try {
            await navigator.clipboard.writeText(reportBody.textContent);
            showToast('Report copied to clipboard');
        } catch (err) {
            showToast('Failed to copy report', 'error');
        }
    }
}

/**
 * Check for running investigation on page load
 */
export async function checkForRunningInvestigation() {
    try {
        const status = await api('/api/investigation/status');

        if (status && status.investigation_id) {
            // Check if investigation is still running
            const isRunning = status.status !== 'completed' && status.status !== 'failed' && status.status !== 'idle';

            if (isRunning) {
                // Investigation is actively running
                currentInvestigationId = status.investigation_id;
                isInvestigationRunning = true;
                showInvestigationStatus(status.investigation_id);
                showInvestigationBanner(status.investigation_id, status.status || 'Running');
                startInvestigationPolling(status.investigation_id);
                document.getElementById('stop-investigation-btn').style.display = 'inline-block';

                // Update header service status indicator
                if (typeof window.updateInvestigationServiceStatus === 'function') {
                    window.updateInvestigationServiceStatus(true, status.status);
                }

                // Enable investigation mode checkbox
                document.getElementById('investigation-mode-checkbox').checked = true;
                toggleInvestigationMode();

                // Disable controls since an investigation is already running
                updateInvestigationControlsState(true);
            } else if (status.status === 'completed' || status.status === 'failed') {
                // Investigation completed/failed but not dismissed - show results with dismiss option
                currentInvestigationId = status.investigation_id;
                isInvestigationRunning = false;  // Not running, just completed
                showInvestigationStatus(status.investigation_id);

                // Update UI to show completed state
                updateInvestigationUI(status);

                // Show appropriate buttons
                document.getElementById('stop-investigation-btn').style.display = 'none';
                if (status.status === 'completed') {
                    document.getElementById('view-report-btn').style.display = 'inline-block';
                }
                const dismissBtn = document.getElementById('dismiss-investigation-btn');
                if (dismissBtn) dismissBtn.style.display = 'inline-block';

                // Update header service status indicator (not running)
                if (typeof window.updateInvestigationServiceStatus === 'function') {
                    window.updateInvestigationServiceStatus(false, status.status);
                }

                // Enable investigation mode checkbox to show the panel
                document.getElementById('investigation-mode-checkbox').checked = true;
                toggleInvestigationMode();

                // Controls should be ENABLED since investigation is not running
                updateInvestigationControlsState(false);

                // Start auto-archive countdown (investigation already done)
                isPinned = false;
                startAutoArchiveCountdown();
            } else {
                // Idle or unknown status - ensure header shows offline
                if (typeof window.updateInvestigationServiceStatus === 'function') {
                    window.updateInvestigationServiceStatus(false, null);
                }
            }
        } else {
            // No current investigation - ensure header shows offline
            if (typeof window.updateInvestigationServiceStatus === 'function') {
                window.updateInvestigationServiceStatus(false, null);
            }
        }
    } catch (err) {
        console.log('No running investigation');
        // On error, ensure header shows offline
        if (typeof window.updateInvestigationServiceStatus === 'function') {
            window.updateInvestigationServiceStatus(false, null);
        }
    }
}

// WebSocket handler for real-time updates
export function handleInvestigationProgress(data) {
    if (data.investigation_id === currentInvestigationId) {
        addInvestigationLog(data.message);
    }
}

export function handleInvestigationComplete(data) {
    if (data.investigation_id === currentInvestigationId) {
        stopInvestigationPolling();
        isInvestigationRunning = false;
        document.getElementById('stop-investigation-btn').style.display = 'none';

        // Re-enable controls
        updateInvestigationControlsState(false);

        // Update banner to completed state
        hideInvestigationBanner();

        // Update header service status indicator
        if (typeof window.updateInvestigationServiceStatus === 'function') {
            window.updateInvestigationServiceStatus(false, data.status);
        }

        if (data.status === 'completed') {
            showToast('Investigation completed!');
            document.getElementById('view-report-btn').style.display = 'inline-block';

            updateInvestigationUI({
                status: 'completed',
                elapsed_seconds: data.elapsed_seconds
            });
        } else {
            showToast('Investigation failed: ' + (data.error || 'Unknown error'), 'error');
            updateInvestigationUI({
                status: 'failed',
                error: data.error
            });
        }

        // Start auto-archive countdown
        isPinned = false;
        startAutoArchiveCountdown();
    }
}

// =============================================================================
// AUTO-ARCHIVE FUNCTIONS
// =============================================================================

/**
 * Load investigation settings from server
 */
export async function loadInvestigationSettings() {
    try {
        const settings = await api('/api/investigation/settings');
        if (settings) {
            investigationSettings = settings;
            syncSettingsToUI(settings);
        }
    } catch (err) {
        console.warn('Could not load investigation settings:', err);
    }
}

/**
 * Save investigation settings to server and update local cache
 */
export async function saveInvestigationSettings(updates) {
    try {
        const result = await api('/api/investigation/settings', 'POST', updates);
        if (result && result.settings) {
            investigationSettings = result.settings;
        }
        return result;
    } catch (err) {
        console.error('Failed to save investigation settings:', err);
        return null;
    }
}

/**
 * Sync settings object to the settings UI controls
 */
function syncSettingsToUI(settings) {
    const enabledCheckbox = document.getElementById('auto-archive-enabled');
    const delaySelect = document.getElementById('auto-archive-delay');
    if (enabledCheckbox) enabledCheckbox.checked = settings.auto_archive_enabled;
    if (delaySelect) delaySelect.value = String(settings.auto_archive_delay_seconds);
}

/**
 * Start auto-archive countdown after investigation completes
 */
export function startAutoArchiveCountdown() {
    if (!investigationSettings.auto_archive_enabled) return;
    if (isPinned) return;

    const delay = investigationSettings.auto_archive_delay_seconds;
    let remaining = delay;

    updateCountdownDisplay(remaining);

    const countdownEl = document.getElementById('auto-archive-countdown');
    if (countdownEl) countdownEl.style.display = 'flex';

    // Show pin button when countdown starts
    const pinBtn = document.getElementById('pin-investigation-btn');
    if (pinBtn) pinBtn.style.display = 'inline-block';

    countdownInterval = setInterval(() => {
        remaining--;
        updateCountdownDisplay(remaining);
        if (remaining <= 0) {
            clearCountdown();
        }
    }, 1000);

    autoArchiveTimer = setTimeout(() => {
        if (!isPinned) {
            dismissInvestigation();
        }
        clearCountdown();
    }, delay * 1000);
}

/**
 * Update the countdown display text and progress bar
 */
function updateCountdownDisplay(remaining) {
    const textEl = document.getElementById('auto-archive-seconds');
    const barEl = document.getElementById('auto-archive-progress-bar');

    if (textEl) textEl.textContent = remaining;

    if (barEl) {
        const total = investigationSettings.auto_archive_delay_seconds;
        const pct = (remaining / total) * 100;
        barEl.style.width = pct + '%';
    }
}

/**
 * Clear countdown timers and hide countdown UI
 */
function clearCountdown() {
    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
    if (autoArchiveTimer) {
        clearTimeout(autoArchiveTimer);
        autoArchiveTimer = null;
    }
    const countdownEl = document.getElementById('auto-archive-countdown');
    if (countdownEl) countdownEl.style.display = 'none';
}

/**
 * Pin the current investigation (prevent auto-archive)
 */
export function pinInvestigation() {
    isPinned = true;
    clearCountdown();

    const pinBtn = document.getElementById('pin-investigation-btn');
    const unpinBtn = document.getElementById('unpin-investigation-btn');
    if (pinBtn) pinBtn.style.display = 'none';
    if (unpinBtn) unpinBtn.style.display = 'inline-block';

    showToast('Investigation pinned — will not auto-archive');
}

/**
 * Unpin the current investigation (re-enable auto-archive)
 */
export function unpinInvestigation() {
    isPinned = false;

    const pinBtn = document.getElementById('pin-investigation-btn');
    const unpinBtn = document.getElementById('unpin-investigation-btn');
    if (pinBtn) pinBtn.style.display = 'inline-block';
    if (unpinBtn) unpinBtn.style.display = 'none';

    // Restart countdown if investigation still completed
    if (currentInvestigationId && !isInvestigationRunning) {
        startAutoArchiveCountdown();
    }
}

/**
 * Handle change of auto-archive settings controls in the UI
 */
export async function handleAutoArchiveSettingChange() {
    const enabledCheckbox = document.getElementById('auto-archive-enabled');
    const delaySelect = document.getElementById('auto-archive-delay');

    const updates = {
        auto_archive_enabled: enabledCheckbox ? enabledCheckbox.checked : true,
        auto_archive_delay_seconds: delaySelect ? parseInt(delaySelect.value) : 5
    };

    await saveInvestigationSettings(updates);
    showToast(`Auto-archive ${updates.auto_archive_enabled ? 'enabled (' + updates.auto_archive_delay_seconds + 's)' : 'disabled'}`);
}

// =============================================================================
// INVESTIGATION BANNER (TOP STATUS DISPLAY)
// =============================================================================

/**
 * Show the investigation banner at the top of the sidebar
 */
export function showInvestigationBanner(investigationId, progress = 'Starting...') {
    const banner = document.getElementById('investigation-banner');
    if (!banner) return;

    banner.style.display = 'block';

    const idEl = document.getElementById('investigation-banner-id');
    const progressEl = document.getElementById('investigation-banner-progress');
    const statusEl = document.getElementById('investigation-banner-status');

    if (idEl) idEl.textContent = investigationId || '-';
    if (progressEl) progressEl.textContent = progress;
    if (statusEl) {
        statusEl.textContent = 'Running';
        statusEl.className = 'status-badge on';
    }
}

/**
 * Update the investigation banner with current status
 */
export function updateInvestigationBanner(status, progress) {
    const progressEl = document.getElementById('investigation-banner-progress');
    const statusEl = document.getElementById('investigation-banner-status');

    if (progressEl) progressEl.textContent = progress || status || '-';
    if (statusEl) {
        // Map status to display values
        const statusLabels = {
            'pending': 'Pending',
            'analyzing': 'Analyzing',
            'spawning_subagents': 'Spawning',
            'exploring': 'Exploring',
            'synthesizing': 'Synthesizing',
            'completed': 'Complete',
            'failed': 'Failed'
        };
        statusEl.textContent = statusLabels[status] || status || 'Running';

        // Update badge style based on status
        if (status === 'completed') {
            statusEl.className = 'status-badge on';
        } else if (status === 'failed') {
            statusEl.className = 'status-badge off';
        } else {
            statusEl.className = 'status-badge on';
        }
    }
}

/**
 * Hide the investigation banner
 */
export function hideInvestigationBanner() {
    const banner = document.getElementById('investigation-banner');
    if (banner) {
        banner.style.display = 'none';
    }
}

/**
 * Scroll to the investigation status card
 */
export function scrollToInvestigationCard() {
    const card = document.getElementById('investigation-status-card');
    if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Briefly highlight the card
        card.style.boxShadow = '0 0 10px var(--accent)';
        setTimeout(() => {
            card.style.boxShadow = '';
        }, 2000);
    }
}

/**
 * Update investigation controls state (disable/enable start button)
 */
function updateInvestigationControlsState(disabled) {
    const startBtn = document.querySelector('#investigation-mode-controls .btn.primary');
    if (startBtn) {
        startBtn.disabled = disabled;
        startBtn.style.opacity = disabled ? '0.5' : '1';
        startBtn.style.cursor = disabled ? 'not-allowed' : 'pointer';
    }
}

/**
 * Check if an investigation is currently running
 */
export function isInvestigationActive() {
    return isInvestigationRunning;
}

/**
 * Dismiss a completed/failed investigation
 * Clears the current investigation and allows starting a new one
 */
export async function dismissInvestigation() {
    // Clear any pending auto-archive timers first
    clearCountdown();
    isPinned = false;

    // Hide pin/unpin buttons
    const pinBtn = document.getElementById('pin-investigation-btn');
    const unpinBtn = document.getElementById('unpin-investigation-btn');
    if (pinBtn) pinBtn.style.display = 'none';
    if (unpinBtn) unpinBtn.style.display = 'none';

    try {
        const result = await api('/api/investigation/dismiss', 'POST');

        if (result.success) {
            showToast('Investigation dismissed');

            // Reset frontend state
            currentInvestigationId = null;
            isInvestigationRunning = false;

            // Hide status card and banner
            hideInvestigationStatus();
            hideInvestigationBanner();

            // Re-enable controls
            updateInvestigationControlsState(false);

            // Update header service status
            if (typeof window.updateInvestigationServiceStatus === 'function') {
                window.updateInvestigationServiceStatus(false, null);
            }

            // Hide buttons
            document.getElementById('view-report-btn').style.display = 'none';
            document.getElementById('stop-investigation-btn').style.display = 'none';
            const dismissBtn = document.getElementById('dismiss-investigation-btn');
            if (dismissBtn) dismissBtn.style.display = 'none';
        } else {
            showToast(result.message || 'Failed to dismiss', 'error');
        }
    } catch (err) {
        console.error('Failed to dismiss investigation:', err);
        showToast('Failed to dismiss investigation', 'error');
    }
}
