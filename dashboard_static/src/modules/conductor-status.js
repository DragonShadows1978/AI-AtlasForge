/**
 * Conductor Status Widget
 * Displays real-time conductor (orchestration engine) status and metrics
 *
 * Features:
 * - Shows running/stopped status
 * - Displays PID, hostname, uptime
 * - Shows current mission and stage
 * - Shows lock collision metrics
 * - Provides restart button for takeover
 */

import { api } from '../api.js';
import { showToast, escapeHtml } from '../core.js';
import { subscribeToRoom } from '../socket.js';

let conductorState = {
    status: null,
    metrics: null,
    initialized: false,
    updateInterval: null
};

/**
 * Initialize the conductor status widget
 */
export function initConductorStatus() {
    if (conductorState.initialized) return;
    console.log('[ConductorStatus] Initializing...');

    // Subscribe to WebSocket updates (future enhancement)
    subscribeToRoom('conductor_status');

    // Load initial data
    refreshConductorStatus();

    // Polling interval (every 15 seconds for responsive status)
    conductorState.updateInterval = setInterval(refreshConductorStatus, 15000);

    conductorState.initialized = true;
}

/**
 * Refresh conductor status from API
 */
export async function refreshConductorStatus() {
    try {
        // Fetch both status and metrics in parallel
        const [statusData, metricsData] = await Promise.all([
            api('/api/conductor/status'),
            api('/api/conductor/metrics')
        ]);

        if (!statusData.error) {
            conductorState.status = statusData;
        }
        if (!metricsData.error) {
            conductorState.metrics = metricsData;
        }

        renderConductorStatus();
    } catch (e) {
        console.error('[ConductorStatus] Refresh error:', e);
        renderConductorError(e.message);
    }
}

/**
 * Request conductor takeover/restart
 */
export async function requestConductorTakeover() {
    if (!confirm('This will send a shutdown signal to the conductor. Continue?')) {
        return;
    }

    try {
        const result = await api('/api/conductor/takeover', 'POST', {});
        if (result.success) {
            showToast('Shutdown signal sent to conductor', 'success');
            setTimeout(refreshConductorStatus, 2000);
        } else {
            showToast(result.message || 'Takeover failed', 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

/**
 * Format uptime in human-readable form
 */
function formatUptime(seconds) {
    if (!seconds || seconds <= 0) return '-';

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${secs}s`;
    } else {
        return `${secs}s`;
    }
}

/**
 * Render the conductor status widget
 */
function renderConductorStatus() {
    const container = document.getElementById('conductor-status-container');
    if (!container) return;

    const status = conductorState.status || {};
    const metrics = conductorState.metrics || {};

    const isRunning = status.running;
    const statusClass = isRunning ? 'success' : 'danger';
    const statusText = isRunning ? 'Running' : (status.lock_file_exists ? 'Stale' : 'Stopped');

    // Update badge in header
    const badge = document.getElementById('conductor-status-badge');
    if (badge) {
        badge.textContent = statusText;
        badge.className = `badge ${statusClass}`;
    }

    container.innerHTML = `
        <div class="analytics-stat-grid">
            <div class="analytics-stat-box">
                <div class="analytics-stat-value ${statusClass}">
                    ${statusText}
                </div>
                <div class="analytics-stat-label">Status</div>
            </div>
            <div class="analytics-stat-box">
                <div class="analytics-stat-value">${status.pid || '-'}</div>
                <div class="analytics-stat-label">PID</div>
            </div>
        </div>
        <div class="analytics-stat-grid">
            <div class="analytics-stat-box">
                <div class="analytics-stat-value">${formatUptime(status.uptime_seconds)}</div>
                <div class="analytics-stat-label">Uptime</div>
            </div>
            <div class="analytics-stat-box">
                <div class="analytics-stat-value ${metrics.collision_count > 0 ? 'warning' : ''}">${metrics.collision_count || 0}</div>
                <div class="analytics-stat-label">Collisions</div>
            </div>
        </div>
        <div class="conductor-details">
            <div class="conductor-detail-row">
                <span class="detail-label">Host:</span>
                <span class="detail-value">${escapeHtml(status.hostname || '-')}</span>
            </div>
            <div class="conductor-detail-row">
                <span class="detail-label">Mission:</span>
                <span class="detail-value">${escapeHtml(status.mission_id || '-')}</span>
            </div>
            <div class="conductor-detail-row">
                <span class="detail-label">Stage:</span>
                <span class="detail-value">${escapeHtml(status.current_stage || '-')}</span>
            </div>
            ${status.is_stale ? `
            <div class="conductor-detail-row stale-warning">
                <span class="detail-label">Warning:</span>
                <span class="detail-value">Lock may be orphaned</span>
            </div>
            ` : ''}
        </div>
        <div class="conductor-actions">
            <button class="btn btn-small ${isRunning ? 'btn-warning' : 'btn-disabled'}"
                    onclick="window.requestConductorTakeover()"
                    ${!isRunning ? 'disabled' : ''}>
                ${isRunning ? 'Restart' : 'Not Running'}
            </button>
            <button class="btn btn-small btn-secondary" onclick="window.refreshConductorStatus()">
                Refresh
            </button>
        </div>
    `;
}

/**
 * Render error state
 */
function renderConductorError(message) {
    const container = document.getElementById('conductor-status-container');
    if (!container) return;

    const badge = document.getElementById('conductor-status-badge');
    if (badge) {
        badge.textContent = 'Error';
        badge.className = 'badge danger';
    }

    container.innerHTML = `
        <div class="conductor-error">
            <div style="color: var(--red); margin-bottom: 10px;">Failed to load conductor status</div>
            <div style="color: var(--text-dim); font-size: 0.85em;">${escapeHtml(message)}</div>
            <button class="btn btn-small btn-secondary" onclick="window.refreshConductorStatus()" style="margin-top: 10px;">
                Retry
            </button>
        </div>
    `;
}

// Cleanup function (called on page unload if needed)
export function destroyConductorStatus() {
    if (conductorState.updateInterval) {
        clearInterval(conductorState.updateInterval);
        conductorState.updateInterval = null;
    }
    conductorState.initialized = false;
}
