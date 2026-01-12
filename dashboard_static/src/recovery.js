/**
 * Dashboard Recovery Module (ES6)
 * Crash recovery detection and restoration
 * Dependencies: core.js, api.js
 */

import { showToast, escapeHtml } from './core.js';
import { api } from './api.js';

// =============================================================================
// RECOVERY STATE
// =============================================================================

let recoveryData = null;
let recoveryModeVisible = false;

// =============================================================================
// RECOVERY MODE FULL-SCREEN FUNCTIONS
// =============================================================================

/**
 * Show the full-screen Recovery Mode startup screen
 */
function showRecoveryModeScreen(data) {
    const screen = document.getElementById('recovery-mode-screen');
    if (!screen) return;

    // Populate details
    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setEl('recovery-mode-mission', data.mission_id || 'Unknown');
    setEl('recovery-mode-stage', data.stage || 'Unknown');
    setEl('recovery-mode-cycle', `${data.cycle || 1} / Iteration ${data.iteration || 0}`);

    // Calculate time since crash
    if (data.timestamp) {
        const crashTime = new Date(data.timestamp);
        const now = new Date();
        const diffMs = now - crashTime;
        const diffMins = Math.round(diffMs / 60000);
        if (diffMins < 60) {
            setEl('recovery-mode-time', `${diffMins} minutes ago`);
        } else {
            const hours = Math.floor(diffMins / 60);
            const mins = diffMins % 60;
            setEl('recovery-mode-time', `${hours}h ${mins}m ago`);
        }
    } else {
        setEl('recovery-mode-time', 'Unknown');
    }

    // Show the screen
    screen.classList.add('show');
    recoveryModeVisible = true;
    console.log('[Recovery] Recovery Mode screen shown');
}

/**
 * Hide the Recovery Mode screen
 */
function hideRecoveryModeScreen() {
    const screen = document.getElementById('recovery-mode-screen');
    if (screen) {
        screen.classList.remove('show');
    }
    recoveryModeVisible = false;
}

/**
 * Resume from crash - close recovery mode and enable resume
 */
export function recoveryModeResume() {
    hideRecoveryModeScreen();
    const banner = document.getElementById('recovery-banner');
    if (banner) banner.classList.remove('show');
    showToast('Resume mode enabled - start mission to continue', 'info');
}

/**
 * Show snapshot list for restoration
 */
export async function recoveryModeRestore() {
    const optionsDiv = document.querySelector('.recovery-mode-options');
    const snapshotsDiv = document.getElementById('recovery-mode-snapshots');
    const itemsDiv = document.getElementById('recovery-mode-snapshot-items');

    if (!optionsDiv || !snapshotsDiv || !itemsDiv) return;

    try {
        const response = await api('/api/recovery/snapshots');
        const snapshots = response.snapshots || [];

        if (snapshots.length === 0) {
            showToast('No snapshots available for restoration', 'warning');
            return;
        }

        // Build snapshot list
        itemsDiv.innerHTML = snapshots.slice(0, 10).map(s => {
            const time = new Date(s.timestamp).toLocaleString();
            return `
                <div class="recovery-snapshot-item" onclick="window.restoreFromRecoveryMode('${s.snapshot_id}')">
                    <div class="recovery-snapshot-info">
                        <div class="recovery-snapshot-id">${s.snapshot_id.slice(0, 40)}...</div>
                        <div class="recovery-snapshot-time">${time}</div>
                    </div>
                    <span class="recovery-snapshot-stage">${s.stage}</span>
                </div>
            `;
        }).join('');

        // Show snapshots, hide options
        optionsDiv.style.display = 'none';
        snapshotsDiv.style.display = 'block';
    } catch (e) {
        console.error('Error loading snapshots:', e);
        showToast('Failed to load snapshots', 'error');
    }
}

/**
 * Hide snapshot list, show options
 */
export function hideRecoveryModeSnapshots() {
    const optionsDiv = document.querySelector('.recovery-mode-options');
    const snapshotsDiv = document.getElementById('recovery-mode-snapshots');

    if (optionsDiv) optionsDiv.style.display = 'flex';
    if (snapshotsDiv) snapshotsDiv.style.display = 'none';
}

/**
 * Restore from a snapshot in recovery mode
 */
export async function restoreFromRecoveryMode(snapshotId) {
    try {
        showToast('Restoring from snapshot...', 'info');
        const response = await api('/api/recovery/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ snapshot_id: snapshotId })
        });

        if (response.success) {
            showToast('Restored successfully!', 'success');
            hideRecoveryModeScreen();
            // Dismiss banner
            const banner = document.getElementById('recovery-banner');
            if (banner) banner.classList.remove('show');
            // Reload after short delay
            setTimeout(() => window.location.reload(), 1000);
        } else {
            showToast('Restore failed: ' + (response.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        console.error('Restore error:', e);
        showToast('Restore failed: ' + e.message, 'error');
    }
}

/**
 * Start fresh - dismiss recovery and close modal
 */
export async function recoveryModeStartFresh() {
    try {
        await api('/api/recovery/dismiss', { method: 'POST' });
        hideRecoveryModeScreen();
        const banner = document.getElementById('recovery-banner');
        if (banner) banner.classList.remove('show');
        showToast('Recovery dismissed - starting fresh', 'info');
    } catch (e) {
        console.error('Dismiss error:', e);
        showToast('Failed to dismiss recovery', 'error');
    }
}

// Expose to global for onclick handlers
window.recoveryModeResume = recoveryModeResume;
window.recoveryModeRestore = recoveryModeRestore;
window.recoveryModeStartFresh = recoveryModeStartFresh;
window.hideRecoveryModeSnapshots = hideRecoveryModeSnapshots;
window.restoreFromRecoveryMode = restoreFromRecoveryMode;

// =============================================================================
// RECOVERY FUNCTIONS
// =============================================================================

export async function checkForRecovery() {
    try {
        const data = await api('/api/recovery/check');
        recoveryData = data;

        if (data.recovery_available) {
            // Show full-screen recovery mode on startup
            showRecoveryModeScreen(data);

            // Also update banner (will be hidden behind modal)
            const banner = document.getElementById('recovery-banner');
            const bannerStage = document.getElementById('recovery-banner-stage');
            const bannerMission = document.getElementById('recovery-banner-mission');

            if (bannerStage) bannerStage.textContent = data.stage || 'Unknown';
            if (bannerMission) bannerMission.textContent = data.mission_id || 'Unknown';
            if (banner) banner.classList.add('show');
        }
    } catch (e) {
        console.error('Recovery check error:', e);
    }
}

export function showRecoveryModal() {
    if (!recoveryData || !recoveryData.recovery_available) return;

    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setEl('recovery-modal-mission-id', recoveryData.mission_id || '-');
    setEl('recovery-modal-stage', recoveryData.stage || '-');
    setEl('recovery-modal-iteration', recoveryData.iteration || '-');
    setEl('recovery-modal-cycle', recoveryData.cycle || '-');
    setEl('recovery-modal-hint', recoveryData.recovery_hint || 'No hint available');

    if (recoveryData.timestamp) {
        const crashTime = new Date(recoveryData.timestamp);
        const now = new Date();
        const diffMs = now - crashTime;
        const diffMins = Math.round(diffMs / 60000);
        setEl('recovery-modal-time', diffMins + ' minutes ago');
    } else {
        setEl('recovery-modal-time', 'Unknown');
    }

    const filesList = document.getElementById('recovery-modal-files');
    const filesSection = document.getElementById('recovery-modal-files-section');

    if (filesList && recoveryData.files_created && recoveryData.files_created.length > 0) {
        filesList.innerHTML = recoveryData.files_created.map(f =>
            `<li style="padding: 2px 0;">${escapeHtml(f)}</li>`
        ).join('');
        if (filesSection) filesSection.style.display = 'block';
    } else if (filesSection) {
        filesSection.style.display = 'none';
    }

    const modal = document.getElementById('recovery-modal');
    if (modal) modal.classList.add('show');
}

export function closeRecoveryModal() {
    const modal = document.getElementById('recovery-modal');
    if (modal) modal.classList.remove('show');
}

export async function dismissRecovery() {
    try {
        await api('/api/recovery/dismiss', { method: 'POST' });
        const banner = document.getElementById('recovery-banner');
        if (banner) banner.classList.remove('show');
        closeRecoveryModal();
        showToast('Recovery dismissed - starting fresh');
    } catch (e) {
        console.error('Dismiss recovery error:', e);
    }
}

export function dismissRecoveryFromModal() {
    dismissRecovery();
}

export async function applyRecovery() {
    closeRecoveryModal();
    const banner = document.getElementById('recovery-banner');
    if (banner) banner.classList.remove('show');
    showToast('Resume mode enabled - start mission to continue');
}

export function getRecoveryData() {
    return recoveryData;
}
