/**
 * Mission Queue Widget Module (ES6)
 * Provides UI for managing the mission queue with intelligent scheduling
 * Dependencies: core.js, api.js
 *
 * Features:
 * - Priority levels (critical, high, normal, low) with visual indicators
 * - Estimated duration tracking based on historical cycle data
 * - Smart reordering suggestions based on mission dependencies
 * - Time-based scheduling (start at specific time, or 'idle after 5pm')
 * - Queue pause/resume functionality with persistence
 * - Browser notifications for scheduled missions (Notification API)
 * - Drag-and-drop reordering interface
 * - Dependency chain visualization (tree/graph view)
 * - Queue health dashboard (blocked, stale, conflicts)
 * - Bulk operations (multi-select, change priority, delete)
 */

import { showToast, escapeHtml, formatTimeAgo } from '../core.js';
import { api } from '../api.js';
import { registerHandler } from '../socket.js';

// =============================================================================
// STATE
// =============================================================================

let queueData = {
    missions: [],
    settings: {},
    atlasforge_running: false,
    paused: false,
    paused_at: null,
    pause_reason: null
};
let refreshInterval = null;
let suggestions = [];

// Browser notification state
let notificationsEnabled = localStorage.getItem('queue_notifications') === 'true';

// Bulk selection state
let selectedItems = new Set();

// Drag-and-drop state
let draggedItem = null;

// =============================================================================
// INITIALIZATION
// =============================================================================

// Track auto-start processing state
let autoStartProcessing = false;

/**
 * Initialize the queue widget
 */
export async function initQueueWidget() {
    console.log('Initializing Mission Queue widget...');

    // Initial load
    await refreshQueueWidget();

    // Register WebSocket handlers
    registerHandler('queue_updated', (data) => {
        console.log('Queue updated via WebSocket:', data);
        queueData = { ...queueData, ...data };
        renderQueueItems();
        updatePauseBanner();
    });

    registerHandler('queue_paused', (data) => {
        console.log('Queue paused:', data);
        queueData.paused = data.paused;
        queueData.paused_at = data.paused_at;
        queueData.pause_reason = data.pause_reason;
        updatePauseBanner();
        showToast('Queue paused', 'info');
    });

    registerHandler('queue_resumed', (data) => {
        console.log('Queue resumed:', data);
        queueData.paused = false;
        queueData.paused_at = null;
        queueData.pause_reason = null;
        updatePauseBanner();
        showToast('Queue resumed', 'success');
    });

    registerHandler('suggestions_available', (data) => {
        console.log('Suggestions available:', data);
        showToast(`💡 ${data.count} reordering suggestion(s) available`, 'info');
        updateSuggestionsBadge(data.count);
    });

    registerHandler('mission_status', (data) => {
        // Update running state when mission status changes
        const wasRunning = queueData.atlasforge_running;
        queueData.atlasforge_running = data.rd_stage && data.rd_stage !== 'COMPLETE';
        if (wasRunning !== queueData.atlasforge_running) {
            renderQueueItems();
            // Hide auto-start indicator if mission is now running
            if (queueData.atlasforge_running && autoStartProcessing) {
                hideAutoStartIndicator();
            }
        }
    });

    // Listen for queue mission started event (from auto-start watcher)
    // This is emitted by the main socket, not the widget socket
    const socket = window.getSocket && window.getSocket();
    if (socket) {
        socket.on('queue_mission_started', (data) => {
            console.log('Queue mission started via auto-start:', data);
            showAutoStartNotification(data);
            hideAutoStartIndicator();
            refreshQueueWidget();
        });
    }

    // Set up enable/disable checkbox
    const checkbox = document.getElementById('queue-enabled-checkbox');
    if (checkbox) {
        checkbox.addEventListener('change', toggleQueueAutoStart);
    }

    // Start periodic refresh (every 30 seconds as backup)
    refreshInterval = setInterval(refreshQueueWidget, 30000);

    // Check for scheduled missions every minute
    setInterval(checkScheduledMissions, 60000);
    // Also check immediately on load
    checkScheduledMissions();

    // Initialize drag-and-drop handlers
    initDragAndDrop();

    // Initialize notification icon state
    updateNotificationIcon();

    // Load queue health on startup
    loadQueueHealth();

    console.log('Mission Queue widget initialized');
}

/**
 * Cleanup the queue widget
 */
export function cleanupQueueWidget() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}

// =============================================================================
// DATA FETCHING
// =============================================================================

/**
 * Refresh queue data from API
 */
export async function refreshQueueWidget() {
    try {
        const data = await api('/api/queue/status');
        if (data && !data.error) {
            queueData = data;
            // Also get pause state
            const settings = data.settings || {};
            queueData.paused = settings.paused || false;
            queueData.paused_at = settings.paused_at;
            queueData.pause_reason = settings.pause_reason;

            renderQueueItems();
            updateQueueCount();
            updateQueueCheckbox();
            updatePauseBanner();
        }
    } catch (e) {
        console.error('Failed to refresh queue:', e);
    }
}

// =============================================================================
// RENDERING
// =============================================================================

/**
 * Render queue items in the list
 */
function renderQueueItems() {
    const container = document.getElementById('queue-items-list');
    if (!container) return;

    const missions = queueData.missions || [];

    if (missions.length === 0) {
        container.innerHTML = `
            <div class="queue-placeholder">
                <span class="queue-placeholder-icon">📋</span>
                <span>No missions queued</span>
            </div>
        `;
        // Hide bulk action bar when no items
        updateBulkActionBar();
        return;
    }

    container.innerHTML = missions.map((m, index) => {
        const priorityClass = getPriorityClass(m.priority);
        const sourceIcon = getSourceIcon(m.source);
        const truncatedStatement = truncateText(m.problem_statement, 100);
        const timeAgo = formatTimeAgo(m.added_at);
        const estimatedTime = formatEstimatedTime(m.estimated_minutes);
        const scheduledInfo = getScheduledInfo(m);
        const isSelected = selectedItems.has(m.id);
        const dependencyBadge = m.depends_on ? `<span class="queue-dep-badge" title="Depends on: ${m.depends_on}">🔗</span>` : '';

        return `
            <div class="queue-item ${priorityClass} ${isSelected ? 'selected' : ''}" data-queue-id="${m.id}" draggable="true">
                <div class="queue-item-drag-handle" title="Drag to reorder">⋮⋮</div>
                <input type="checkbox" class="queue-item-checkbox" data-queue-id="${m.id}"
                       ${isSelected ? 'checked' : ''} onclick="toggleItemSelection('${m.id}')">
                <div class="queue-item-main">
                    <div class="queue-item-header">
                        <span class="queue-item-priority" style="background: ${getPriorityBgColor(m.priority)}; color: ${getPriorityColor(m.priority)}">
                            ${getPriorityLabel(m.priority)}
                        </span>
                        <span class="queue-item-source" title="Source: ${m.source}">${sourceIcon}</span>
                        ${scheduledInfo ? `<span class="queue-item-scheduled" title="${scheduledInfo.title}">${scheduledInfo.icon}</span>` : ''}
                        ${dependencyBadge}
                        <span class="queue-item-time">${timeAgo}</span>
                    </div>
                    <div class="queue-item-content" title="${escapeHtml(m.problem_statement)}">
                        ${escapeHtml(truncatedStatement)}
                    </div>
                    <div class="queue-item-meta">
                        <span class="queue-item-cycles">${m.cycle_budget || 3} cycles</span>
                        ${estimatedTime ? `<span class="queue-item-estimate" title="Estimated duration">⏱ ${estimatedTime}</span>` : ''}
                        <span class="queue-item-status">${m.status || 'pending'}</span>
                    </div>
                </div>
                <div class="queue-item-actions">
                    <button class="btn-icon" onclick="editQueueItem('${m.id}')" title="Edit priority/schedule">✎</button>
                    <button class="btn-icon danger" onclick="removeQueueItem('${m.id}')" title="Remove">×</button>
                </div>
            </div>
        `;
    }).join('');

    // Update bulk action bar visibility
    updateBulkActionBar();
}

/**
 * Update the queue count badge
 */
function updateQueueCount() {
    const badge = document.getElementById('queue-count');
    if (badge) {
        const count = (queueData.missions || []).length;
        badge.textContent = count;
        badge.style.display = count > 0 ? 'inline-block' : 'none';
    }
}

/**
 * Update the auto-start checkbox state
 */
function updateQueueCheckbox() {
    const checkbox = document.getElementById('queue-enabled-checkbox');
    if (checkbox && queueData.settings) {
        checkbox.checked = queueData.settings.auto_start || false;
    }
}

// =============================================================================
// QUEUE OPERATIONS
// =============================================================================

/**
 * Add a mission to the queue
 */
export async function addToQueue(problemStatement, options = {}) {
    try {
        const payload = {
            problem_statement: problemStatement,
            cycle_budget: options.cycle_budget || 3,
            priority: options.priority || 0,
            source: options.source || 'dashboard'
        };

        const data = await api('/api/queue/add', 'POST', payload);
        if (data.status === 'added') {
            showToast(`Mission added to queue (position ${data.queue_length})`);
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Failed to add: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to add to queue:', e);
        showToast('Failed to add mission to queue', 'error');
    }
}

/**
 * Remove a mission from the queue
 */
export async function removeFromQueue(queueId) {
    try {
        const data = await api(`/api/queue/remove/${queueId}`, 'DELETE');
        if (data.status === 'removed') {
            showToast('Mission removed from queue');
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Failed to remove: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to remove from queue:', e);
        showToast('Failed to remove mission', 'error');
    }
}

/**
 * Clear all missions from the queue
 */
export async function clearQueue() {
    if (!confirm('Are you sure you want to clear the entire queue?')) return;

    try {
        const data = await api('/api/queue/clear', 'POST');
        if (data.status === 'cleared') {
            showToast(`Cleared ${data.cleared_count} missions from queue`);
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Failed to clear: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to clear queue:', e);
        showToast('Failed to clear queue', 'error');
    }
}

/**
 * Move a queue item up or down
 */
export async function moveQueueItem(queueId, direction) {
    const missions = queueData.missions || [];
    const currentIndex = missions.findIndex(m => m.id === queueId);

    if (currentIndex === -1) return;

    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1;
    if (newIndex < 0 || newIndex >= missions.length) return;

    // Create new order array
    const newOrder = missions.map(m => m.id);
    [newOrder[currentIndex], newOrder[newIndex]] = [newOrder[newIndex], newOrder[currentIndex]];

    try {
        const data = await api('/api/queue/reorder', 'POST', { order: newOrder });
        if (data.status === 'reordered') {
            await refreshQueueWidget();
        }
    } catch (e) {
        console.error('Failed to reorder queue:', e);
        showToast('Failed to reorder queue', 'error');
    }
}

/**
 * Start the next mission in the queue
 */
export async function startNextFromQueue() {
    // Always fetch fresh status before deciding (cache could be stale)
    try {
        const statusData = await api('/api/queue/status');
        if (statusData.atlasforge_running) {
            showToast('AtlasForge is currently running a mission', 'warning');
            return;
        }
        // Update our local cache with fresh data
        queueData = statusData;
    } catch (e) {
        console.warn('Could not verify AtlasForge status, proceeding anyway:', e);
    }

    // Check if there are missions in the queue
    if (!queueData.missions || queueData.missions.length === 0) {
        showToast('No missions in queue', 'warning');
        return;
    }

    // Get the first mission's title for the indicator
    const nextMission = queueData.missions[0];
    const missionTitle = nextMission?.problem_statement?.substring(0, 50) || 'Next Mission';

    // Show processing indicator
    showAutoStartIndicator(missionTitle);

    try {
        const data = await api('/api/queue/next', 'POST');
        if (data.status === 'started') {
            showToast(`🚀 Started mission: ${data.mission_id}`);
            await refreshQueueWidget();
        } else if (data.error) {
            hideAutoStartIndicator();
            showToast(data.error, 'error');
        }
    } catch (e) {
        hideAutoStartIndicator();
        console.error('Failed to start next mission:', e);
        showToast('Failed to start next mission', 'error');
    }
}

/**
 * Toggle auto-start setting
 */
async function toggleQueueAutoStart() {
    const checkbox = document.getElementById('queue-enabled-checkbox');
    if (!checkbox) return;

    try {
        const data = await api('/api/queue/settings', 'PUT', {
            auto_start: checkbox.checked
        });
        if (data.status === 'updated') {
            showToast(`Queue auto-start ${checkbox.checked ? 'enabled' : 'disabled'}`);
            queueData.settings = data.settings;
        }
    } catch (e) {
        console.error('Failed to update settings:', e);
        checkbox.checked = !checkbox.checked; // Revert
    }
}

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

function truncateText(text, maxLength) {
    if (!text) return '';
    return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
}

function getPriorityClass(priority) {
    // Handle both string and numeric priority
    const p = typeof priority === 'string' ? priority.toLowerCase() : priority;
    if (p === 'critical' || p >= 3) return 'priority-critical';
    if (p === 'high' || p >= 2) return 'priority-high';
    if (p === 'normal' || p === 0 || p === 1) return 'priority-normal';
    if (p === 'low' || p < 0) return 'priority-low';
    return 'priority-normal';
}

function getPriorityLabel(priority) {
    const p = typeof priority === 'string' ? priority.toLowerCase() : priority;
    if (p === 'critical' || p >= 3) return 'CRITICAL';
    if (p === 'high' || p >= 2) return 'High';
    if (p === 'normal' || p === 0 || p === 1) return 'Normal';
    if (p === 'low' || p < 0) return 'Low';
    return 'Normal';
}

function getPriorityColor(priority) {
    const p = typeof priority === 'string' ? priority.toLowerCase() : priority;
    if (p === 'critical' || p >= 3) return '#dc3545';  // Red
    if (p === 'high' || p >= 2) return '#fd7e14';      // Orange
    if (p === 'normal' || p === 0 || p === 1) return '#0d6efd';  // Blue
    if (p === 'low' || p < 0) return '#6c757d';        // Gray
    return '#0d6efd';
}

function getPriorityBgColor(priority) {
    const color = getPriorityColor(priority);
    // Return a lighter version for background
    return color.replace('#', 'rgba(') + color.slice(1, 3) + ', ' +
           parseInt(color.slice(3, 5), 16) + ', ' +
           parseInt(color.slice(5, 7), 16) + ', 0.15)';
}

function getSourceIcon(source) {
    switch (source) {
        case 'recommendation': return '💡';
        case 'kb_recommendation': return '🧠';
        case 'email': return '📧';
        case 'investigation': return '🔍';
        default: return '📝';
    }
}

function formatEstimatedTime(minutes) {
    if (!minutes) return null;
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

function getScheduledInfo(mission) {
    if (mission.scheduled_start) {
        try {
            const dt = new Date(mission.scheduled_start);
            return {
                icon: '📅',
                title: `Scheduled: ${dt.toLocaleString()}`
            };
        } catch (e) {
            return null;
        }
    }
    if (mission.start_condition) {
        if (mission.start_condition.startsWith('idle_after:')) {
            const time = mission.start_condition.split(':').slice(1).join(':');
            return {
                icon: '🕐',
                title: `Start after ${time} when idle`
            };
        }
        if (mission.start_condition.startsWith('after_mission:')) {
            return {
                icon: '⏳',
                title: `Waiting for dependent mission`
            };
        }
    }
    if (mission.depends_on) {
        return {
            icon: '🔗',
            title: `Depends on: ${mission.depends_on}`
        };
    }
    return null;
}

// =============================================================================
// AUTO-START VISUAL FEEDBACK
// =============================================================================

/**
 * Show a visual indicator that auto-start is processing a queued mission
 */
function showAutoStartIndicator(missionTitle) {
    autoStartProcessing = true;

    // Update the queue header to show processing state
    const queueHeader = document.getElementById('queue-widget-header');
    if (queueHeader) {
        const existingIndicator = queueHeader.querySelector('.queue-autostart-indicator');
        if (!existingIndicator) {
            const indicator = document.createElement('span');
            indicator.className = 'queue-autostart-indicator';
            indicator.innerHTML = `
                <span class="queue-autostart-spinner"></span>
                <span class="queue-autostart-text">Auto-starting...</span>
            `;
            queueHeader.appendChild(indicator);
        }
    }

    // Also show a toast notification
    showToast(`Auto-starting queued mission: ${missionTitle || 'Unknown'}`, 'info');
}

/**
 * Hide the auto-start processing indicator
 */
function hideAutoStartIndicator() {
    autoStartProcessing = false;

    const indicator = document.querySelector('.queue-autostart-indicator');
    if (indicator) {
        indicator.remove();
    }
}

/**
 * Show notification when queue mission starts successfully
 */
function showAutoStartNotification(data) {
    const title = data.mission_title || data.mission_id || 'Queued Mission';
    showToast(`🚀 Started: ${title}`, 'success');
}

// =============================================================================
// PAUSE/RESUME FUNCTIONALITY
// =============================================================================

/**
 * Toggle queue pause state
 */
export async function toggleQueuePause() {
    try {
        if (queueData.paused) {
            await resumeQueueState();
        } else {
            await pauseQueueState();
        }
    } catch (e) {
        console.error('Failed to toggle pause:', e);
        showToast('Failed to toggle queue pause', 'error');
    }
}

/**
 * Pause the queue
 */
async function pauseQueueState(reason = null) {
    try {
        const data = await api('/api/queue/pause', 'POST', { reason });
        if (data.status === 'paused') {
            queueData.paused = true;
            queueData.paused_at = data.paused_at;
            queueData.pause_reason = data.pause_reason;
            updatePauseBanner();
            showToast('Queue paused');
        }
    } catch (e) {
        console.error('Failed to pause queue:', e);
        throw e;
    }
}

/**
 * Resume the queue
 */
async function resumeQueueState() {
    try {
        const data = await api('/api/queue/resume', 'POST');
        if (data.status === 'resumed') {
            queueData.paused = false;
            queueData.paused_at = null;
            queueData.pause_reason = null;
            updatePauseBanner();
            showToast('Queue resumed', 'success');
        }
    } catch (e) {
        console.error('Failed to resume queue:', e);
        throw e;
    }
}

/**
 * Update the pause banner visibility and content
 */
function updatePauseBanner() {
    let banner = document.getElementById('queue-pause-banner');

    if (queueData.paused) {
        if (!banner) {
            // Create banner
            const container = document.getElementById('queue-card');
            if (container) {
                banner = document.createElement('div');
                banner.id = 'queue-pause-banner';
                banner.className = 'queue-pause-banner';
                const content = container.querySelector('.card-content');
                if (content) {
                    content.insertBefore(banner, content.firstChild);
                }
            }
        }
        if (banner) {
            const pausedTime = queueData.paused_at ? formatTimeAgo(queueData.paused_at) : 'recently';
            banner.innerHTML = `
                <span class="pause-icon">⏸️</span>
                <span class="pause-text">Queue is PAUSED ${queueData.pause_reason ? `(${queueData.pause_reason})` : ''}</span>
                <span class="pause-time">since ${pausedTime}</span>
                <button class="btn btn-small" onclick="toggleQueuePause()">Resume</button>
            `;
            banner.style.display = 'flex';
        }
    } else {
        if (banner) {
            banner.style.display = 'none';
        }
    }

    // Update pause button in controls
    const pauseBtn = document.getElementById('queue-pause-btn');
    if (pauseBtn) {
        pauseBtn.textContent = queueData.paused ? 'Resume' : 'Pause';
        pauseBtn.title = queueData.paused ? 'Resume queue processing' : 'Pause queue processing';
    }
}

// =============================================================================
// EDIT QUEUE ITEM
// =============================================================================

/**
 * Edit a queue item (show edit modal)
 */
export async function editQueueItem(queueId) {
    const mission = (queueData.missions || []).find(m => m.id === queueId);
    if (!mission) {
        showToast('Queue item not found', 'error');
        return;
    }

    // Create or show edit modal
    showEditModal(mission);
}

/**
 * Show the edit modal for a queue item
 */
function showEditModal(mission) {
    // Remove existing modal if any
    const existingModal = document.getElementById('queue-edit-modal');
    if (existingModal) existingModal.remove();

    // Build options for depends_on dropdown (other missions in queue)
    const otherMissions = (queueData.missions || [])
        .filter(m => m.id !== mission.id)
        .map(m => {
            const selected = mission.depends_on === m.id ? 'selected' : '';
            const title = truncateText(m.problem_statement || m.mission_title || 'Untitled', 50);
            return `<option value="${m.id}" ${selected}>${escapeHtml(title)}</option>`;
        })
        .join('');

    const modal = document.createElement('div');
    modal.id = 'queue-edit-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content queue-edit-modal-content">
            <div class="modal-header">
                <h3>Edit Queue Item</h3>
                <button class="modal-close" onclick="closeQueueEditModal()">×</button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>Priority</label>
                    <select id="edit-priority" class="form-input">
                        <option value="critical" ${mission.priority === 'critical' ? 'selected' : ''}>🔴 Critical</option>
                        <option value="high" ${mission.priority === 'high' ? 'selected' : ''}>🟠 High</option>
                        <option value="normal" ${mission.priority === 'normal' || !mission.priority ? 'selected' : ''}>🔵 Normal</option>
                        <option value="low" ${mission.priority === 'low' ? 'selected' : ''}>⚪ Low</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Cycle Budget</label>
                    <input type="number" id="edit-cycles" class="form-input" min="1" max="10" value="${mission.cycle_budget || 3}">
                </div>
                <div class="form-group">
                    <label>Depends On (optional)</label>
                    <select id="edit-depends-on" class="form-input">
                        <option value="">None - No dependency</option>
                        ${otherMissions}
                    </select>
                    <small class="form-help">This mission will wait until the selected mission completes.</small>
                </div>
                <div class="form-group">
                    <label>Scheduled Start (optional)</label>
                    <input type="datetime-local" id="edit-scheduled" class="form-input" value="${mission.scheduled_start ? mission.scheduled_start.slice(0, 16) : ''}">
                </div>
                <div class="form-group">
                    <label>Start Condition (optional)</label>
                    <select id="edit-condition" class="form-input">
                        <option value="">None</option>
                        <option value="idle_after:09:00" ${mission.start_condition === 'idle_after:09:00' ? 'selected' : ''}>Start when idle after 9 AM</option>
                        <option value="idle_after:12:00" ${mission.start_condition === 'idle_after:12:00' ? 'selected' : ''}>Start when idle after 12 PM</option>
                        <option value="idle_after:17:00" ${mission.start_condition === 'idle_after:17:00' ? 'selected' : ''}>Start when idle after 5 PM</option>
                        <option value="idle_after:21:00" ${mission.start_condition === 'idle_after:21:00' ? 'selected' : ''}>Start when idle after 9 PM</option>
                    </select>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="closeQueueEditModal()">Cancel</button>
                <button class="btn primary" onclick="saveQueueItemEdit('${mission.id}')">Save Changes</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

/**
 * Save edits to a queue item
 */
export async function saveQueueItemEdit(queueId) {
    const priority = document.getElementById('edit-priority')?.value;
    const cycles = parseInt(document.getElementById('edit-cycles')?.value) || 3;
    const dependsOn = document.getElementById('edit-depends-on')?.value;
    const scheduled = document.getElementById('edit-scheduled')?.value;
    const condition = document.getElementById('edit-condition')?.value;

    try {
        const data = await api(`/api/queue/update/${queueId}`, 'PUT', {
            priority,
            cycle_budget: cycles,
            depends_on: dependsOn || null,
            scheduled_start: scheduled || null,
            start_condition: condition || null
        });

        if (data.status === 'updated') {
            showToast('Queue item updated');
            closeQueueEditModal();
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Failed to update: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to save edit:', e);
        showToast('Failed to save changes', 'error');
    }
}

/**
 * Close the edit modal
 */
function closeQueueEditModal() {
    const modal = document.getElementById('queue-edit-modal');
    if (modal) modal.remove();
}

// =============================================================================
// DEPENDENCY SUGGESTIONS
// =============================================================================

/**
 * Fetch and show dependency suggestions
 */
export async function showSuggestions() {
    try {
        const data = await api('/api/queue/suggestions');
        if (data.error) {
            showToast(`Failed to get suggestions: ${data.error}`, 'error');
            return;
        }

        suggestions = data.suggestions || [];

        if (suggestions.length === 0) {
            showToast('No reordering suggestions found', 'info');
            return;
        }

        showSuggestionsModal(suggestions);
    } catch (e) {
        console.error('Failed to get suggestions:', e);
        showToast('Failed to get suggestions', 'error');
    }
}

/**
 * Show suggestions modal
 */
function showSuggestionsModal(suggestions) {
    const existingModal = document.getElementById('queue-suggestions-modal');
    if (existingModal) existingModal.remove();

    const modal = document.createElement('div');
    modal.id = 'queue-suggestions-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content suggestions-modal-content">
            <div class="modal-header">
                <h3>Reordering Suggestions</h3>
                <button class="modal-close" onclick="closeSuggestionsModal()">×</button>
            </div>
            <div class="modal-body">
                ${suggestions.map((s, i) => `
                    <div class="suggestion-item" data-index="${i}">
                        <div class="suggestion-missions">
                            <span class="suggestion-mission-a" title="${escapeHtml(s.mission_a_title || s.mission_a)}">${truncateText(s.mission_a_title || s.mission_a, 30)}</span>
                            <span class="suggestion-arrow">→</span>
                            <span class="suggestion-mission-b" title="${escapeHtml(s.mission_b_title || s.mission_b)}">${truncateText(s.mission_b_title || s.mission_b, 30)}</span>
                            <span class="confidence-badge ${s.confidence_label || 'medium'}" title="Confidence: ${(s.confidence * 100).toFixed(0)}%">${s.confidence_label || 'medium'}</span>
                        </div>
                        <div class="suggestion-reason">${escapeHtml(s.reason)}</div>
                        <button class="btn btn-small" onclick="applySuggestion(${i})">Apply</button>
                    </div>
                `).join('')}
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="closeSuggestionsModal()">Close</button>
                <button class="btn primary" onclick="applyAllSuggestions()">Apply All</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

/**
 * Apply a single suggestion
 */
export async function applySuggestion(index) {
    if (index < 0 || index >= suggestions.length) return;

    const suggestion = suggestions[index];
    try {
        const data = await api('/api/queue/suggestions/apply', 'POST', suggestion);
        if (data.reordered) {
            showToast('Suggestion applied');
            await refreshQueueWidget();
            // Remove applied suggestion from list
            suggestions.splice(index, 1);
            if (suggestions.length === 0) {
                closeSuggestionsModal();
            } else {
                showSuggestionsModal(suggestions);
            }
        } else {
            showToast('No change needed', 'info');
        }
    } catch (e) {
        console.error('Failed to apply suggestion:', e);
        showToast('Failed to apply suggestion', 'error');
    }
}

/**
 * Apply all suggestions
 */
export async function applyAllSuggestions() {
    for (let i = 0; i < suggestions.length; i++) {
        try {
            await api('/api/queue/suggestions/apply', 'POST', suggestions[i]);
        } catch (e) {
            console.error('Failed to apply suggestion:', e);
        }
    }
    showToast(`Applied ${suggestions.length} suggestions`);
    suggestions = [];
    closeSuggestionsModal();
    await refreshQueueWidget();
}

/**
 * Close suggestions modal
 */
function closeSuggestionsModal() {
    const modal = document.getElementById('queue-suggestions-modal');
    if (modal) modal.remove();
}

// =============================================================================
// TIMELINE VISUALIZATION
// =============================================================================

/**
 * Toggle timeline visibility
 */
export function toggleQueueTimeline() {
    const timeline = document.getElementById('queue-timeline');
    if (timeline) {
        timeline.classList.toggle('collapsed');
        const toggleIcon = timeline.querySelector('.toggle-icon');
        if (toggleIcon) {
            toggleIcon.textContent = timeline.classList.contains('collapsed') ? '▼' : '▲';
        }
        // Load timeline data when expanded
        if (!timeline.classList.contains('collapsed')) {
            renderQueueTimeline();
        }
    }
}

/**
 * Render queue timeline (Gantt-style chart)
 */
async function renderQueueTimeline() {
    const body = document.getElementById('timeline-body');
    if (!body) return;

    try {
        const data = await api('/api/queue/timeline');
        if (!data.timeline || data.timeline.length === 0) {
            body.innerHTML = '<div class="timeline-empty">No missions to display</div>';
            return;
        }

        const now = new Date();
        const maxHours = 4; // 4-hour window
        const maxMs = maxHours * 3600000;

        body.innerHTML = data.timeline.slice(0, 8).map((item, index) => {
            const start = new Date(item.estimated_start);
            const end = new Date(item.estimated_end);

            // Calculate position and width as percentages
            const offsetMs = Math.max(0, start - now);
            const offsetPercent = Math.min(95, (offsetMs / maxMs) * 100);
            const durationMs = end - start;
            const widthPercent = Math.max(5, Math.min(100 - offsetPercent, (durationMs / maxMs) * 100));

            // Determine row based on index
            const row = index % 4;

            // Determine readiness class
            const readyClass = item.is_ready ? 'ready' : 'waiting';

            return `
                <div class="timeline-bar priority-${item.priority} ${readyClass}"
                     style="left: ${offsetPercent}%; width: ${widthPercent}%; top: ${row * 28 + 4}px;"
                     title="${escapeHtml(item.mission_title)} (${item.duration_minutes}m)"
                     data-mission-id="${item.id}">
                    <span class="timeline-bar-label">${truncateText(item.mission_title, 15)}</span>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to render timeline:', e);
        body.innerHTML = '<div class="timeline-empty">Failed to load timeline</div>';
    }
}

// =============================================================================
// QUEUE ANALYTICS
// =============================================================================

/**
 * Toggle analytics panel visibility
 */
export function toggleQueueAnalytics() {
    const panel = document.getElementById('queue-analytics');
    if (panel) {
        panel.classList.toggle('collapsed');
        const toggleIcon = panel.querySelector('.toggle-icon');
        if (toggleIcon) {
            toggleIcon.textContent = panel.classList.contains('collapsed') ? '▼' : '▲';
        }
        // Load analytics when expanded
        if (!panel.classList.contains('collapsed')) {
            loadQueueAnalytics();
        }
    }
}

/**
 * Load queue analytics data
 */
async function loadQueueAnalytics() {
    try {
        const data = await api('/api/queue/analytics');
        if (data.error) {
            console.warn('Analytics error:', data.error);
            return;
        }

        // Update analytics display
        const throughput = document.getElementById('analytics-throughput');
        const duration = document.getElementById('analytics-duration');
        const success = document.getElementById('analytics-success');
        const week = document.getElementById('analytics-week');

        if (throughput) throughput.textContent = `${data.throughput_daily}/day`;
        if (duration) duration.textContent = formatEstimatedTime(data.avg_duration_minutes);
        if (success) success.textContent = `${data.success_rate_percent}%`;
        if (week) week.textContent = data.missions_7d;

        // Update utilization bar if present
        const utilBar = document.getElementById('analytics-util-bar');
        if (utilBar) {
            utilBar.style.width = `${data.utilization_percent}%`;
        }
    } catch (e) {
        console.error('Failed to load analytics:', e);
    }
}

// =============================================================================
// NOTIFICATION HANDLING
// =============================================================================

let scheduledMissionAlerts = new Set(); // Track which missions we've already alerted about

/**
 * Check for missions that are about to start
 */
function checkScheduledMissions() {
    const now = new Date();
    const fiveMinutes = 5 * 60 * 1000;

    (queueData.missions || []).forEach(m => {
        if (m.scheduled_start) {
            const scheduled = new Date(m.scheduled_start);
            const diff = scheduled - now;

            // Alert if within 5 minutes and haven't already alerted
            if (diff > 0 && diff <= fiveMinutes && !scheduledMissionAlerts.has(m.id)) {
                scheduledMissionAlerts.add(m.id);
                const minutes = Math.round(diff / 60000);
                const missionTitle = truncateText(m.problem_statement || 'Queued Mission', 50);

                // Use browser notification with toast fallback
                showBrowserNotification(
                    'Scheduled Mission Starting Soon',
                    `${missionTitle} starts in ${minutes} minute${minutes !== 1 ? 's' : ''}`,
                    m.id
                );
            }

            // Clean up old alerts for passed schedules
            if (diff < 0) {
                scheduledMissionAlerts.delete(m.id);
            }
        }
    });
}

/**
 * Update the suggestions badge
 */
function updateSuggestionsBadge(count) {
    const btn = document.querySelector('.queue-suggestions-btn');
    if (btn) {
        let badge = btn.querySelector('.suggestion-badge');
        if (!badge && count > 0) {
            badge = document.createElement('span');
            badge.className = 'suggestion-badge';
            btn.appendChild(badge);
        }
        if (badge) {
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline-block' : 'none';
        }
    }
}

// =============================================================================
// ENHANCED QUICK ADD
// =============================================================================

/**
 * Quick add with priority selection
 */
export async function quickAddEnhanced() {
    const input = document.getElementById('queue-add-input');
    const prioritySelect = document.getElementById('queue-add-priority');

    if (!input || !input.value.trim()) {
        showToast('Please enter a mission description', 'warning');
        return;
    }

    const priority = prioritySelect?.value || 'normal';

    try {
        const data = await api('/api/queue/add-enhanced', 'POST', {
            problem_statement: input.value.trim(),
            priority: priority,
            cycle_budget: 3,
            source: 'dashboard'
        });

        if (data.status === 'added') {
            showToast(`Mission added at position ${data.position} (est. ${formatEstimatedTime(data.estimated_minutes)})`);
            input.value = '';
            if (prioritySelect) prioritySelect.value = 'normal';
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Failed: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to add:', e);
        showToast('Failed to add mission', 'error');
    }
}

// =============================================================================
// BROWSER NOTIFICATIONS
// =============================================================================

/**
 * Request browser notification permission
 */
export async function requestNotificationPermission() {
    if (!('Notification' in window)) {
        showToast('Browser notifications not supported', 'warning');
        return false;
    }

    if (Notification.permission === 'granted') {
        notificationsEnabled = true;
        localStorage.setItem('queue_notifications', 'true');
        updateNotificationIcon();
        showToast('Notifications already enabled', 'success');
        return true;
    }

    if (Notification.permission === 'denied') {
        showToast('Notifications blocked. Enable in browser settings.', 'warning');
        return false;
    }

    try {
        const result = await Notification.requestPermission();
        notificationsEnabled = result === 'granted';
        localStorage.setItem('queue_notifications', notificationsEnabled.toString());
        updateNotificationIcon();

        if (notificationsEnabled) {
            showToast('Browser notifications enabled!', 'success');
            // Send a test notification
            new Notification('Queue Notifications Enabled', {
                body: 'You will now receive alerts for scheduled missions.',
                icon: '/static/favicon.ico',
                tag: 'queue-test'
            });
        } else {
            showToast('Notification permission denied', 'warning');
        }
        return notificationsEnabled;
    } catch (e) {
        console.error('Failed to request notification permission:', e);
        showToast('Failed to request notification permission', 'error');
        return false;
    }
}

/**
 * Show browser notification with fallback to toast
 */
function showBrowserNotification(title, body, missionId = null) {
    // Always show toast as backup
    showToast(body, 'info');

    // Try browser notification if enabled
    if (notificationsEnabled && Notification.permission === 'granted') {
        try {
            const notification = new Notification(title, {
                body: body,
                icon: '/static/favicon.ico',
                tag: missionId || 'queue-notification',
                requireInteraction: true
            });

            notification.onclick = () => {
                window.focus();
                notification.close();
                // Optionally scroll to the mission in the queue
                if (missionId) {
                    const item = document.querySelector(`.queue-item[data-queue-id="${missionId}"]`);
                    if (item) item.scrollIntoView({ behavior: 'smooth' });
                }
            };
        } catch (e) {
            console.error('Failed to show browser notification:', e);
        }
    }
}

/**
 * Update notification icon state
 */
function updateNotificationIcon() {
    const icon = document.getElementById('queue-notification-icon');
    if (!icon) return;

    if (!('Notification' in window)) {
        icon.textContent = '🔕';
        icon.title = 'Browser notifications not supported';
        icon.classList.add('disabled');
    } else if (Notification.permission === 'granted' && notificationsEnabled) {
        icon.textContent = '🔔';
        icon.title = 'Browser notifications enabled - click to disable';
        icon.classList.remove('disabled');
        icon.classList.add('enabled');
    } else if (Notification.permission === 'denied') {
        icon.textContent = '🔕';
        icon.title = 'Notifications blocked by browser';
        icon.classList.add('disabled');
    } else {
        icon.textContent = '🔔';
        icon.title = 'Click to enable browser notifications';
        icon.classList.remove('enabled', 'disabled');
    }
}

/**
 * Toggle notification enabled state
 */
export function toggleNotifications() {
    if (Notification.permission === 'granted') {
        notificationsEnabled = !notificationsEnabled;
        localStorage.setItem('queue_notifications', notificationsEnabled.toString());
        updateNotificationIcon();
        showToast(`Browser notifications ${notificationsEnabled ? 'enabled' : 'disabled'}`, 'info');
    } else {
        requestNotificationPermission();
    }
}

// =============================================================================
// DRAG-AND-DROP REORDERING
// =============================================================================

/**
 * Initialize drag-and-drop event handlers
 */
function initDragAndDrop() {
    const container = document.getElementById('queue-items-list');
    if (!container) return;

    container.addEventListener('dragstart', handleDragStart);
    container.addEventListener('dragover', handleDragOver);
    container.addEventListener('dragenter', handleDragEnter);
    container.addEventListener('dragleave', handleDragLeave);
    container.addEventListener('drop', handleDrop);
    container.addEventListener('dragend', handleDragEnd);
}

function handleDragStart(e) {
    const item = e.target.closest('.queue-item');
    if (!item) return;

    draggedItem = item;
    item.classList.add('dragging');

    // Set drag data
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', item.dataset.queueId);

    // Create ghost image
    setTimeout(() => {
        item.style.opacity = '0.4';
    }, 0);
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';

    const container = document.getElementById('queue-items-list');
    const afterElement = getDragAfterElement(container, e.clientY);

    if (draggedItem) {
        if (afterElement == null) {
            container.appendChild(draggedItem);
        } else if (afterElement !== draggedItem) {
            container.insertBefore(draggedItem, afterElement);
        }
    }
}

function handleDragEnter(e) {
    const item = e.target.closest('.queue-item');
    if (item && item !== draggedItem) {
        item.classList.add('drag-over');
    }
}

function handleDragLeave(e) {
    const item = e.target.closest('.queue-item');
    if (item) {
        item.classList.remove('drag-over');
    }
}

async function handleDrop(e) {
    e.preventDefault();
    const item = e.target.closest('.queue-item');
    if (item) {
        item.classList.remove('drag-over');
    }
}

async function handleDragEnd() {
    if (!draggedItem) return;

    draggedItem.classList.remove('dragging');
    draggedItem.style.opacity = '';

    // Clear all drag-over classes
    document.querySelectorAll('.queue-item.drag-over').forEach(el => {
        el.classList.remove('drag-over');
    });

    // Get new order from DOM
    const container = document.getElementById('queue-items-list');
    const newOrder = [...container.querySelectorAll('.queue-item')]
        .map(item => item.dataset.queueId)
        .filter(id => id);

    // Send reorder request to API
    if (newOrder.length > 0) {
        try {
            const data = await api('/api/queue/reorder', 'POST', { order: newOrder });
            if (data.status === 'reordered') {
                showToast('Queue reordered', 'success');
                await refreshQueueWidget();
            }
        } catch (e) {
            console.error('Failed to reorder queue:', e);
            showToast('Failed to reorder queue', 'error');
            await refreshQueueWidget(); // Revert to server state
        }
    }

    draggedItem = null;
}

/**
 * Get the element after which the dragged item should be inserted
 */
function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('.queue-item:not(.dragging)')];

    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;

        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

// =============================================================================
// BULK OPERATIONS
// =============================================================================

/**
 * Toggle selection of a single item
 */
export function toggleItemSelection(queueId) {
    if (selectedItems.has(queueId)) {
        selectedItems.delete(queueId);
    } else {
        selectedItems.add(queueId);
    }
    updateSelectionUI();
}

/**
 * Select all items
 */
export function selectAllItems() {
    const missions = queueData.missions || [];
    if (selectedItems.size === missions.length) {
        selectedItems.clear();
    } else {
        missions.forEach(m => selectedItems.add(m.id));
    }
    updateSelectionUI();
}

/**
 * Clear all selections
 */
export function clearSelection() {
    selectedItems.clear();
    updateSelectionUI();
}

/**
 * Update selection UI (checkboxes and action bar)
 */
function updateSelectionUI() {
    // Update checkboxes
    document.querySelectorAll('.queue-item-checkbox').forEach(cb => {
        cb.checked = selectedItems.has(cb.dataset.queueId);
    });

    // Update item selection class
    document.querySelectorAll('.queue-item').forEach(item => {
        if (selectedItems.has(item.dataset.queueId)) {
            item.classList.add('selected');
        } else {
            item.classList.remove('selected');
        }
    });

    // Update bulk action bar
    updateBulkActionBar();
}

/**
 * Update bulk action bar visibility and count
 */
function updateBulkActionBar() {
    const actionBar = document.getElementById('bulk-action-bar');
    if (!actionBar) return;

    if (selectedItems.size > 0) {
        actionBar.style.display = 'flex';
        const countSpan = actionBar.querySelector('.selection-count');
        if (countSpan) {
            countSpan.textContent = `${selectedItems.size} selected`;
        }
    } else {
        actionBar.style.display = 'none';
    }
}

/**
 * Bulk change priority for selected items
 */
export async function bulkChangePriority(priority) {
    if (selectedItems.size === 0) {
        showToast('No items selected', 'warning');
        return;
    }

    try {
        const data = await api('/api/queue/bulk/priority', 'POST', {
            queue_ids: [...selectedItems],
            priority: priority
        });

        if (data.status === 'updated') {
            showToast(`Updated priority for ${data.count} items`, 'success');
            selectedItems.clear();
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to bulk update priority:', e);
        showToast('Failed to update priority', 'error');
    }
}

/**
 * Bulk delete selected items
 */
export async function bulkDelete() {
    if (selectedItems.size === 0) {
        showToast('No items selected', 'warning');
        return;
    }

    if (!confirm(`Are you sure you want to delete ${selectedItems.size} items?`)) {
        return;
    }

    try {
        const data = await api('/api/queue/bulk/delete', 'POST', {
            queue_ids: [...selectedItems]
        });

        if (data.status === 'deleted') {
            showToast(`Deleted ${data.count} items`, 'success');
            selectedItems.clear();
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to bulk delete:', e);
        showToast('Failed to delete items', 'error');
    }
}

/**
 * Bulk add dependency to selected items
 */
export async function bulkAddDependency(dependsOn) {
    if (selectedItems.size === 0) {
        showToast('No items selected', 'warning');
        return;
    }

    try {
        const data = await api('/api/queue/bulk/dependency', 'POST', {
            queue_ids: [...selectedItems],
            depends_on: dependsOn
        });

        if (data.status === 'updated') {
            showToast(`Added dependency to ${data.count} items`, 'success');
            selectedItems.clear();
            await refreshQueueWidget();
        } else if (data.error) {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to bulk add dependency:', e);
        showToast('Failed to add dependency', 'error');
    }
}

/**
 * Show bulk action modal for priority or dependency selection
 */
export function showBulkActionModal(action) {
    if (selectedItems.size === 0) {
        showToast('No items selected', 'warning');
        return;
    }

    const existingModal = document.getElementById('bulk-action-modal');
    if (existingModal) existingModal.remove();

    let content = '';
    if (action === 'priority') {
        content = `
            <div class="form-group">
                <label>Select new priority for ${selectedItems.size} items:</label>
                <select id="bulk-priority-select" class="form-input">
                    <option value="critical">🔴 Critical</option>
                    <option value="high">🟠 High</option>
                    <option value="normal" selected>🔵 Normal</option>
                    <option value="low">⚪ Low</option>
                </select>
            </div>
        `;
    } else if (action === 'dependency') {
        const otherMissions = (queueData.missions || [])
            .filter(m => !selectedItems.has(m.id))
            .map(m => `<option value="${m.id}">${escapeHtml(truncateText(m.problem_statement || '', 50))}</option>`)
            .join('');
        content = `
            <div class="form-group">
                <label>Select dependency for ${selectedItems.size} items:</label>
                <select id="bulk-dependency-select" class="form-input">
                    <option value="">None</option>
                    ${otherMissions}
                </select>
            </div>
        `;
    }

    const modal = document.createElement('div');
    modal.id = 'bulk-action-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content bulk-action-modal-content">
            <div class="modal-header">
                <h3>Bulk ${action === 'priority' ? 'Change Priority' : 'Add Dependency'}</h3>
                <button class="modal-close" onclick="closeBulkActionModal()">×</button>
            </div>
            <div class="modal-body">
                ${content}
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="closeBulkActionModal()">Cancel</button>
                <button class="btn primary" onclick="applyBulkAction('${action}')">Apply</button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
}

/**
 * Apply bulk action from modal
 */
export async function applyBulkAction(action) {
    if (action === 'priority') {
        const priority = document.getElementById('bulk-priority-select')?.value || 'normal';
        await bulkChangePriority(priority);
    } else if (action === 'dependency') {
        const dependsOn = document.getElementById('bulk-dependency-select')?.value || null;
        await bulkAddDependency(dependsOn);
    }
    closeBulkActionModal();
}

/**
 * Close bulk action modal
 */
function closeBulkActionModal() {
    const modal = document.getElementById('bulk-action-modal');
    if (modal) modal.remove();
}

// =============================================================================
// QUEUE HEALTH DASHBOARD
// =============================================================================

let healthData = null;

/**
 * Toggle queue health panel visibility
 */
export function toggleQueueHealth() {
    const panel = document.getElementById('queue-health');
    if (panel) {
        panel.classList.toggle('collapsed');
        const toggleIcon = panel.querySelector('.toggle-icon');
        if (toggleIcon) {
            toggleIcon.textContent = panel.classList.contains('collapsed') ? '▼' : '▲';
        }
        // Load health data when expanded
        if (!panel.classList.contains('collapsed')) {
            loadQueueHealth();
        }
    }
}

/**
 * Load queue health data
 */
async function loadQueueHealth() {
    try {
        const data = await api('/api/queue/health');
        if (data.error) {
            console.warn('Health check error:', data.error);
            return;
        }

        healthData = data;
        renderQueueHealth(data);
    } catch (e) {
        console.error('Failed to load queue health:', e);
    }
}

/**
 * Render queue health data
 */
function renderQueueHealth(data) {
    // Update health badge
    const badge = document.getElementById('health-badge');
    if (badge) {
        const totalIssues = data.total_issues || 0;
        badge.textContent = totalIssues;
        badge.className = 'health-badge ' + (totalIssues === 0 ? 'healthy' : totalIssues <= 2 ? 'warning' : 'critical');
    }

    // Update health score
    const score = document.getElementById('health-score');
    if (score) {
        score.textContent = `${data.health_score || 100}%`;
        score.className = 'health-score ' + (data.health_score >= 80 ? 'healthy' : data.health_score >= 50 ? 'warning' : 'critical');
    }

    // Render blocked missions
    const blockedSection = document.getElementById('health-blocked');
    if (blockedSection) {
        const blocked = data.blocked || [];
        blockedSection.innerHTML = blocked.length > 0 ? `
            <div class="health-section-header">🚫 Blocked (${blocked.length})</div>
            ${blocked.map(b => `
                <div class="health-issue blocked">
                    <span class="issue-id">${truncateText(b.id, 20)}</span>
                    <span class="issue-reason">${escapeHtml(b.reason)}</span>
                </div>
            `).join('')}
        ` : '';
    }

    // Render stale missions
    const staleSection = document.getElementById('health-stale');
    if (staleSection) {
        const stale = data.stale || [];
        staleSection.innerHTML = stale.length > 0 ? `
            <div class="health-section-header">⏰ Stale (${stale.length})</div>
            ${stale.map(s => `
                <div class="health-issue stale">
                    <span class="issue-id">${truncateText(s.id, 20)}</span>
                    <span class="issue-reason">${s.hours_queued}h in queue</span>
                </div>
            `).join('')}
        ` : '';
    }

    // Render conflicts
    const conflictsSection = document.getElementById('health-conflicts');
    if (conflictsSection) {
        const conflicts = data.conflicts || [];
        conflictsSection.innerHTML = conflicts.length > 0 ? `
            <div class="health-section-header">⚠️ Conflicts (${conflicts.length})</div>
            ${conflicts.map(c => `
                <div class="health-issue conflict">
                    <span class="issue-reason">${escapeHtml(c.description || 'Scheduling conflict')}</span>
                </div>
            `).join('')}
        ` : '';
    }
}

// =============================================================================
// DEPENDENCY CHAIN VISUALIZATION
// =============================================================================

/**
 * Toggle dependency graph visibility
 */
export function toggleDependencyGraph() {
    const panel = document.getElementById('queue-dependency-graph');
    if (panel) {
        panel.classList.toggle('collapsed');
        const toggleIcon = panel.querySelector('.toggle-icon');
        if (toggleIcon) {
            toggleIcon.textContent = panel.classList.contains('collapsed') ? '▼' : '▲';
        }
        // Load dependency data when expanded
        if (!panel.classList.contains('collapsed')) {
            renderDependencyGraph();
        }
    }
}

/**
 * Render dependency graph as SVG
 */
async function renderDependencyGraph() {
    const container = document.getElementById('dependency-graph-container');
    if (!container) return;

    try {
        const data = await api('/api/queue/dependency-tree');
        if (data.error) {
            container.innerHTML = '<div class="graph-error">Failed to load dependency data</div>';
            return;
        }

        const nodes = data.nodes || [];
        const edges = data.edges || [];

        if (nodes.length === 0) {
            container.innerHTML = '<div class="graph-empty">No missions in queue</div>';
            return;
        }

        // Build tree structure
        const tree = buildDependencyTree(nodes, edges);

        // Render as SVG
        const svg = renderTreeSVG(tree, nodes);
        container.innerHTML = svg;
    } catch (e) {
        console.error('Failed to render dependency graph:', e);
        container.innerHTML = '<div class="graph-error">Error loading graph</div>';
    }
}

/**
 * Build tree structure from nodes and edges
 */
function buildDependencyTree(nodes, edges) {
    const nodeMap = {};
    nodes.forEach(n => {
        nodeMap[n.id] = { ...n, children: [] };
    });

    edges.forEach(e => {
        if (nodeMap[e.from] && nodeMap[e.to]) {
            nodeMap[e.from].children.push(nodeMap[e.to]);
        }
    });

    // Find roots (nodes with no incoming edges)
    const hasParent = new Set(edges.map(e => e.to));
    const roots = nodes.filter(n => !hasParent.has(n.id)).map(n => nodeMap[n.id]);

    return roots;
}

/**
 * Render tree as SVG
 */
function renderTreeSVG(roots, allNodes) {
    const nodeWidth = 140;
    const nodeHeight = 40;
    const levelGap = 60;
    const nodeGap = 20;

    // Calculate positions using a simple layout
    let currentY = 10;
    const positions = {};

    function layoutTree(node, level, xOffset) {
        const x = xOffset;
        const y = level * (nodeHeight + levelGap);

        positions[node.id] = { x, y, node };

        let childXOffset = xOffset;
        node.children.forEach((child, i) => {
            layoutTree(child, level + 1, childXOffset);
            childXOffset += nodeWidth + nodeGap;
        });

        // Center parent over children
        if (node.children.length > 0) {
            const firstChild = positions[node.children[0].id];
            const lastChild = positions[node.children[node.children.length - 1].id];
            positions[node.id].x = (firstChild.x + lastChild.x) / 2;
        }

        return childXOffset - xOffset || nodeWidth;
    }

    let totalWidth = 10;
    roots.forEach((root, i) => {
        totalWidth += layoutTree(root, 0, totalWidth);
        totalWidth += nodeGap;
    });

    // Find max depth
    const maxDepth = Math.max(...Object.values(positions).map(p => p.y)) / (nodeHeight + levelGap);
    const svgHeight = (maxDepth + 1) * (nodeHeight + levelGap) + 20;
    const svgWidth = totalWidth + 20;

    // Generate SVG
    let svg = `<svg width="${svgWidth}" height="${svgHeight}" class="dependency-tree-svg">`;

    // Draw edges first (so they're behind nodes)
    Object.values(positions).forEach(pos => {
        const node = pos.node;
        node.children.forEach(child => {
            const childPos = positions[child.id];
            if (childPos) {
                const x1 = pos.x + nodeWidth / 2;
                const y1 = pos.y + nodeHeight;
                const x2 = childPos.x + nodeWidth / 2;
                const y2 = childPos.y;

                svg += `<path d="M${x1},${y1} C${x1},${y1 + 20} ${x2},${y2 - 20} ${x2},${y2}"
                        fill="none" stroke="var(--text-dim)" stroke-width="2" class="dep-edge"/>`;
            }
        });
    });

    // Draw nodes
    Object.values(positions).forEach(pos => {
        const node = pos.node;
        const priorityClass = getPriorityClass(node.priority);
        const statusClass = node.status === 'blocked' ? 'blocked' : 'ready';

        svg += `
            <g class="dep-node ${priorityClass} ${statusClass}" transform="translate(${pos.x}, ${pos.y})">
                <rect width="${nodeWidth}" height="${nodeHeight}" rx="4"/>
                <text x="${nodeWidth / 2}" y="${nodeHeight / 2 + 4}" text-anchor="middle">${escapeHtml(truncateText(node.title, 15))}</text>
            </g>
        `;
    });

    svg += '</svg>';
    return svg;
}

// =============================================================================
// ENHANCED SCHEDULED MISSION CHECK WITH BROWSER NOTIFICATIONS
// =============================================================================

// Override checkScheduledMissions to use browser notifications
const originalCheckScheduledMissions = checkScheduledMissions;

// =============================================================================
// GLOBAL EXPORTS (for onclick handlers in HTML)
// =============================================================================

// These functions need to be globally accessible for HTML onclick handlers
window.moveQueueItem = moveQueueItem;
window.removeQueueItem = removeFromQueue;
window.clearQueue = clearQueue;
window.startNextFromQueue = startNextFromQueue;
window.refreshQueueWidget = refreshQueueWidget;
window.addToQueue = addToQueue;
window.showAutoStartIndicator = showAutoStartIndicator;
window.hideAutoStartIndicator = hideAutoStartIndicator;
window.toggleQueuePause = toggleQueuePause;
window.editQueueItem = editQueueItem;
window.saveQueueItemEdit = saveQueueItemEdit;
window.closeQueueEditModal = closeQueueEditModal;
window.showSuggestions = showSuggestions;
window.applySuggestion = applySuggestion;
window.applyAllSuggestions = applyAllSuggestions;
window.closeSuggestionsModal = closeSuggestionsModal;
window.quickAddEnhanced = quickAddEnhanced;
window.toggleQueueTimeline = toggleQueueTimeline;
window.toggleQueueAnalytics = toggleQueueAnalytics;

// New exports for Cycle 3 features
window.toggleNotifications = toggleNotifications;
window.requestNotificationPermission = requestNotificationPermission;
window.toggleItemSelection = toggleItemSelection;
window.selectAllItems = selectAllItems;
window.clearSelection = clearSelection;
window.bulkDelete = bulkDelete;
window.showBulkActionModal = showBulkActionModal;
window.applyBulkAction = applyBulkAction;
window.closeBulkActionModal = closeBulkActionModal;
window.toggleQueueHealth = toggleQueueHealth;
window.toggleDependencyGraph = toggleDependencyGraph;

// Export additional module references (functions already exported at definition)
export {
    queueData,
    addToQueue as addMissionToQueue,
    showAutoStartIndicator,
    hideAutoStartIndicator
};
