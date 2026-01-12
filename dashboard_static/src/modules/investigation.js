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

    const subagents = parseInt(document.getElementById('investigation-subagents').value) || 5;
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
    modal.style.display = 'flex';
}

/**
 * Close the investigation report modal
 */
export function closeInvestigationReportModal() {
    const modal = document.getElementById('investigation-report-modal');
    if (modal) {
        modal.style.display = 'none';
    }
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

        if (status && status.investigation_id && status.status !== 'completed' && status.status !== 'failed' && status.status !== 'idle') {
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
        } else {
            // Not running - ensure header shows offline
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
    }
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
