/**
 * Dashboard Recovery Module
 * Crash recovery detection and restoration
 * Dependencies: core.js, api.js
 */

// =============================================================================
// RECOVERY STATE
// =============================================================================

let recoveryData = null;

// =============================================================================
// RECOVERY FUNCTIONS
// =============================================================================

async function checkForRecovery() {
    try {
        const data = await api('/api/recovery/check');
        recoveryData = data;

        if (data.recovery_available) {
            const banner = document.getElementById('recovery-banner');
            document.getElementById('recovery-banner-stage').textContent = data.stage || 'Unknown';
            document.getElementById('recovery-banner-mission').textContent = data.mission_id || 'Unknown';
            banner.classList.add('show');
        }
    } catch (e) {
        console.error('Recovery check error:', e);
    }
}

function showRecoveryModal() {
    if (!recoveryData || !recoveryData.recovery_available) return;

    document.getElementById('recovery-modal-mission-id').textContent = recoveryData.mission_id || '-';
    document.getElementById('recovery-modal-stage').textContent = recoveryData.stage || '-';
    document.getElementById('recovery-modal-iteration').textContent = recoveryData.iteration || '-';
    document.getElementById('recovery-modal-cycle').textContent = recoveryData.cycle || '-';
    document.getElementById('recovery-modal-hint').textContent = recoveryData.recovery_hint || 'No hint available';

    // Calculate time since crash
    if (recoveryData.timestamp) {
        const crashTime = new Date(recoveryData.timestamp);
        const now = new Date();
        const diffMs = now - crashTime;
        const diffMins = Math.round(diffMs / 60000);
        document.getElementById('recovery-modal-time').textContent = diffMins + ' minutes ago';
    } else {
        document.getElementById('recovery-modal-time').textContent = 'Unknown';
    }

    // Files
    const filesList = document.getElementById('recovery-modal-files');
    if (recoveryData.files_created && recoveryData.files_created.length > 0) {
        filesList.innerHTML = recoveryData.files_created.map(f =>
            `<li style="padding: 2px 0;">${escapeHtml(f)}</li>`
        ).join('');
        document.getElementById('recovery-modal-files-section').style.display = 'block';
    } else {
        document.getElementById('recovery-modal-files-section').style.display = 'none';
    }

    document.getElementById('recovery-modal').classList.add('show');
}

function closeRecoveryModal() {
    document.getElementById('recovery-modal').classList.remove('show');
}

async function dismissRecovery() {
    try {
        await api('/api/recovery/dismiss', { method: 'POST' });
        document.getElementById('recovery-banner').classList.remove('show');
        closeRecoveryModal();
        showToast('Recovery dismissed - starting fresh');
    } catch (e) {
        console.error('Dismiss recovery error:', e);
    }
}

function dismissRecoveryFromModal() {
    dismissRecovery();
}

async function applyRecovery() {
    // Just close modal - the rd_engine will handle recovery on next start
    closeRecoveryModal();
    document.getElementById('recovery-banner').classList.remove('show');
    showToast('Resume mode enabled - start mission to continue');
}

// Debug: mark recovery module loaded
console.log('Recovery module loaded');
