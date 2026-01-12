/**
 * Backup Status Module
 *
 * Handles UI interactions for the mission backup/recovery system.
 * Provides real-time updates for backup health, stale alerts,
 * and snapshot management.
 */

import { api } from '../api.js';
import { showToast } from '../core.js';
import { registerHandler, subscribeToRoom } from '../socket.js';

// Module state
let backupStatusInitialized = false;
let lastBackupData = null;
let staleAlertShown = false;
let staleAlertDismissed = false;

/**
 * Initialize the backup status module
 */
export function initBackupStatus() {
    if (backupStatusInitialized) return;

    console.log('[BackupStatus] Initializing...');

    // Subscribe to WebSocket updates for real-time data
    subscribeToRoom('backup_status');

    // Register handlers for real-time updates
    registerHandler('backup_status', handleBackupUpdate);
    registerHandler('backup_stale_alert', handleStaleAlert);

    // Load initial data
    refreshBackupStatus();

    // Setup refresh interval (every 60 seconds)
    setInterval(refreshBackupStatus, 60000);

    backupStatusInitialized = true;
}

/**
 * Handle WebSocket updates from backup_status room
 */
function handleBackupUpdate(data) {
    console.log('[BackupStatus] Received WebSocket update:', data);
    lastBackupData = data;
    renderBackupStatus(data);
}

/**
 * Handle stale backup alerts
 */
function handleStaleAlert(data) {
    console.log('[BackupStatus] Stale backup alert:', data);
    showToast('Stale backup alert: ' + data.message, 'warning');

    // Update UI to show stale state
    const container = document.getElementById('backup-status-container');
    if (container) {
        container.classList.add('stale');
    }
}

/**
 * Refresh backup status from API
 */
export async function refreshBackupStatus() {
    try {
        const response = await api('/api/recovery/backup-status');
        if (response.error) {
            console.warn('[BackupStatus] API error:', response.error);
            renderBackupError(response.error);
            return;
        }
        lastBackupData = response;
        renderBackupStatus(response);
    } catch (error) {
        console.error('[BackupStatus] Failed to refresh:', error);
        renderBackupError(error.message);
    }
}

/**
 * Render backup status to the UI
 */
function renderBackupStatus(data) {
    const container = document.getElementById('backup-status-container');
    if (!container) return;

    // Calculate time since last backup
    let timeSinceBackup = 'Never';
    let backupAgeClass = 'stale';
    let shortBackupAge = '-';

    if (data.latest_snapshot && data.latest_snapshot.timestamp) {
        const snapshotTime = new Date(data.latest_snapshot.timestamp);
        const now = new Date();
        const ageMs = now - snapshotTime;
        const ageMinutes = Math.floor(ageMs / 60000);
        const ageHours = Math.floor(ageMinutes / 60);

        if (ageMinutes < 60) {
            timeSinceBackup = `${ageMinutes} min ago`;
            shortBackupAge = `${ageMinutes}m`;
            backupAgeClass = 'fresh';
        } else if (ageHours < 2) {
            timeSinceBackup = `${ageHours}h ${ageMinutes % 60}m ago`;
            shortBackupAge = `${ageHours}h ${ageMinutes % 60}m`;
            backupAgeClass = 'recent';
        } else {
            timeSinceBackup = `${ageHours}h ago`;
            shortBackupAge = `${ageHours}h`;
            backupAgeClass = data.is_stale ? 'stale' : 'old';
        }
    } else {
        backupAgeClass = 'no-backup';
        shortBackupAge = 'None';
    }

    // Build health indicator
    let healthClass = 'healthy';
    let healthText = 'Healthy';
    if (data.is_stale && data.is_mission_active) {
        healthClass = 'stale';
        healthText = 'Stale (>2h)';
    } else if (!data.latest_snapshot) {
        healthClass = 'no-backup';
        healthText = 'No Backups';
    } else if (!data.is_mission_active) {
        healthClass = 'inactive';
        healthText = 'Inactive';
    }

    // Update container class
    container.className = `backup-status-container ${healthClass}`;

    // Render content
    container.innerHTML = `
        <div class="backup-status-header">
            <span class="backup-health-indicator ${healthClass}"></span>
            <span class="backup-health-text">${healthText}</span>
        </div>
        <div class="backup-status-details">
            <div class="backup-stat">
                <span class="backup-stat-label">Last Backup:</span>
                <span class="backup-stat-value ${backupAgeClass}">${timeSinceBackup}</span>
            </div>
            <div class="backup-stat">
                <span class="backup-stat-label">Snapshots:</span>
                <span class="backup-stat-value">${data.snapshot_count || 0}</span>
            </div>
            <div class="backup-stat">
                <span class="backup-stat-label">Stage:</span>
                <span class="backup-stat-value">${data.latest_snapshot?.stage || '-'}</span>
            </div>
        </div>
        <div class="backup-status-actions">
            <button class="btn btn-sm" onclick="window.backupStatusModule.createSnapshot()">
                Create Snapshot
            </button>
            <button class="btn btn-sm btn-secondary" onclick="window.backupStatusModule.viewSnapshots()">
                View All
            </button>
        </div>
    `;

    // Update status card backup age badge
    updateStatusCardBackupAge(shortBackupAge, backupAgeClass);

    // Handle stale backup alert
    if (data.is_stale && data.is_mission_active && !staleAlertDismissed) {
        showStaleBackupAlert();
    } else {
        hideStaleBackupAlert();
    }
}

/**
 * Update the backup age badge in the main status card
 */
function updateStatusCardBackupAge(age, ageClass) {
    const badge = document.getElementById('stat-backup-age');
    if (badge) {
        badge.textContent = age;
        badge.className = `stat-value backup-age-badge ${ageClass}`;
    }
}

/**
 * Show stale backup alert banner
 */
function showStaleBackupAlert() {
    if (staleAlertShown) return;

    let alert = document.getElementById('stale-backup-alert');
    if (!alert) {
        // Create alert element
        alert = document.createElement('div');
        alert.id = 'stale-backup-alert';
        alert.className = 'stale-backup-alert';
        alert.innerHTML = `
            <span class="alert-icon">&#9888;</span>
            <span class="alert-text">Backup is stale! Last snapshot was over 2 hours ago.</span>
            <button class="btn btn-sm" onclick="window.backupStatusModule.createSnapshot()">Backup Now</button>
            <button class="alert-dismiss" onclick="window.backupStatusModule.dismissStaleAlert()">&times;</button>
        `;
        document.body.appendChild(alert);
    }

    alert.classList.add('show');
    staleAlertShown = true;
}

/**
 * Hide stale backup alert
 */
function hideStaleBackupAlert() {
    const alert = document.getElementById('stale-backup-alert');
    if (alert) {
        alert.classList.remove('show');
    }
    staleAlertShown = false;
}

/**
 * Dismiss stale alert (user clicked X)
 */
export function dismissStaleAlert() {
    hideStaleBackupAlert();
    staleAlertDismissed = true;
    // Reset after 30 minutes so it can show again
    setTimeout(() => {
        staleAlertDismissed = false;
    }, 30 * 60 * 1000);
}

/**
 * Render error state
 */
function renderBackupError(error) {
    const container = document.getElementById('backup-status-container');
    if (!container) return;

    container.className = 'backup-status-container error';
    container.innerHTML = `
        <div class="backup-status-error">
            <span class="backup-error-text">Error: ${error}</span>
            <button class="btn btn-sm" onclick="window.backupStatusModule.refreshBackupStatus()">
                Retry
            </button>
        </div>
    `;
}

/**
 * Create a new snapshot manually
 */
export async function createSnapshot() {
    try {
        showToast('Creating snapshot...', 'info');
        const response = await api('/api/recovery/create-snapshot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage_hint: 'Manual snapshot via dashboard' })
        });

        if (response.success) {
            showToast('Snapshot created: ' + response.snapshot.snapshot_id.slice(0, 20) + '...', 'success');
            refreshBackupStatus();
        } else {
            showToast('Failed to create snapshot: ' + response.error, 'error');
        }
    } catch (error) {
        showToast('Error creating snapshot: ' + error.message, 'error');
    }
}

/**
 * View all snapshots (opens modal or navigates to snapshots view)
 */
export async function viewSnapshots() {
    try {
        const response = await api('/api/recovery/snapshots');
        if (response.error) {
            showToast('Failed to load snapshots: ' + response.error, 'error');
            return;
        }

        // Create modal to display snapshots
        showSnapshotsModal(response.snapshots);
    } catch (error) {
        showToast('Error loading snapshots: ' + error.message, 'error');
    }
}

/**
 * Show snapshots in a modal dialog
 */
function showSnapshotsModal(snapshots) {
    // Remove existing modal if present
    const existingModal = document.getElementById('snapshots-modal');
    if (existingModal) {
        existingModal.remove();
    }

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'snapshots-modal';
    modal.className = 'modal-overlay';
    modal.onclick = (e) => {
        if (e.target === modal) modal.remove();
    };

    // Build snapshot list HTML
    let snapshotListHtml = '';
    if (snapshots.length === 0) {
        snapshotListHtml = '<p class="no-snapshots">No snapshots available</p>';
    } else {
        snapshotListHtml = snapshots.map(s => {
            const time = new Date(s.timestamp).toLocaleString();
            const hashShort = s.sha256_hash?.slice(0, 8) || '?';
            return `
                <div class="snapshot-item" data-id="${s.snapshot_id}">
                    <div class="snapshot-info">
                        <span class="snapshot-id">${s.snapshot_id.slice(0, 30)}...</span>
                        <span class="snapshot-time">${time}</span>
                        <span class="snapshot-stage badge">${s.stage}</span>
                        <span class="snapshot-hash" title="${s.sha256_hash}">Hash: ${hashShort}</span>
                    </div>
                    <div class="snapshot-actions">
                        <button class="btn btn-sm btn-primary" onclick="window.backupStatusModule.restoreSnapshot('${s.snapshot_id}')">
                            Restore
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h3>Mission Snapshots (${snapshots.length})</h3>
                <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="snapshots-list">
                    ${snapshotListHtml}
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="this.closest('.modal-overlay').remove()">Close</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

/**
 * Show diff preview before restoring from snapshot
 */
export async function restoreSnapshot(snapshotId) {
    try {
        // First, fetch the diff
        showToast('Loading diff preview...', 'info');
        const diffResponse = await api(`/api/recovery/diff/${encodeURIComponent(snapshotId)}`);

        if (diffResponse.error) {
            // Fall back to confirm dialog if diff fails
            if (!confirm(`Are you sure you want to restore from snapshot?\n\n${snapshotId}\n\nThis will overwrite the current mission state.`)) {
                return;
            }
            await performRestore(snapshotId);
            return;
        }

        // Show diff modal
        showDiffPreviewModal(snapshotId, diffResponse);
    } catch (error) {
        showToast('Error loading diff: ' + error.message, 'error');
    }
}

/**
 * Show diff preview modal
 */
function showDiffPreviewModal(snapshotId, diffData) {
    // Remove existing modal
    const existingModal = document.getElementById('diff-preview-modal');
    if (existingModal) existingModal.remove();

    // Build diff HTML
    let diffHtml = '';
    if (diffData.changes.length === 0) {
        diffHtml = '<p class="no-changes">No differences found - snapshot matches current state.</p>';
    } else {
        diffHtml = diffData.changes.map(change => {
            const typeClass = change.type === 'modified' ? 'diff-modified' :
                             change.type === 'added' ? 'diff-added' : 'diff-removed';
            return `
                <div class="diff-item ${typeClass}">
                    <div class="diff-field">${change.field}</div>
                    <div class="diff-values">
                        <div class="diff-current">
                            <span class="diff-label">Current:</span>
                            <span class="diff-value">${escapeForDiff(change.current)}</span>
                        </div>
                        <div class="diff-arrow">→</div>
                        <div class="diff-snapshot">
                            <span class="diff-label">Restore to:</span>
                            <span class="diff-value">${escapeForDiff(change.snapshot)}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // Create modal
    const modal = document.createElement('div');
    modal.id = 'diff-preview-modal';
    modal.className = 'modal-overlay';
    modal.onclick = (e) => {
        if (e.target === modal) modal.remove();
    };

    const timestamp = new Date(diffData.snapshot_timestamp).toLocaleString();

    modal.innerHTML = `
        <div class="modal-content diff-modal">
            <div class="modal-header">
                <h3>Restore Preview - ${diffData.change_count} Changes</h3>
                <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="diff-summary">
                    <span class="diff-stage">Stage: ${diffData.snapshot_stage}</span>
                    <span class="diff-time">Snapshot: ${timestamp}</span>
                </div>
                <div class="diff-list">
                    ${diffHtml}
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
                <button class="btn primary" onclick="window.backupStatusModule.confirmRestore('${snapshotId}')">
                    Restore Now
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

/**
 * Escape special characters for diff display
 */
function escapeForDiff(str) {
    if (!str) return '(empty)';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Confirm and perform restore after diff preview
 */
export async function confirmRestore(snapshotId) {
    // Close diff modal
    const diffModal = document.getElementById('diff-preview-modal');
    if (diffModal) diffModal.remove();

    await performRestore(snapshotId);
}

/**
 * Perform the actual restore
 */
async function performRestore(snapshotId) {
    try {
        showToast('Restoring from snapshot...', 'info');
        const response = await api('/api/recovery/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_id: snapshotId })
        });

        if (response.success) {
            showToast('Restored successfully from snapshot', 'success');
            // Close snapshots modal
            const modal = document.getElementById('snapshots-modal');
            if (modal) modal.remove();
            // Refresh status
            refreshBackupStatus();
            // Reload page to reflect new state
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast('Failed to restore: ' + response.error, 'error');
        }
    } catch (error) {
        showToast('Error restoring snapshot: ' + error.message, 'error');
    }
}

// Export module for global access (needed for onclick handlers)
window.backupStatusModule = {
    initBackupStatus,
    refreshBackupStatus,
    createSnapshot,
    viewSnapshots,
    restoreSnapshot,
    confirmRestore,
    dismissStaleAlert
};

export default {
    initBackupStatus,
    refreshBackupStatus,
    createSnapshot,
    viewSnapshots,
    restoreSnapshot,
    confirmRestore,
    dismissStaleAlert
};
