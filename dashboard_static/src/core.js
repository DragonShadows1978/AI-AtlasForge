/**
 * Dashboard Core Module (ES6)
 * Global utilities, constants, and helper functions shared across all modules
 * Dependencies: None
 */

// =============================================================================
// CONSTANTS
// =============================================================================

export const stages = ['PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'CYCLE_END', 'COMPLETE'];

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

/**
 * Escape HTML to prevent XSS attacks
 * @param {string} text - Text to escape
 * @returns {string} - Escaped HTML string
 */
export function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show a toast notification
 * @param {string} msg - Message to display
 * @param {string|number} typeOrDuration - Either 'success'|'error'|'info'|'warning' or duration in ms
 * @param {number} duration - Duration in milliseconds (default 3000) if type was specified
 */
export function showToast(msg, typeOrDuration = 3000, duration = 3000) {
    const t = document.getElementById('toast');
    if (!t) return;

    // Handle backwards compatibility: if second arg is number, treat as duration
    let toastType = 'info';
    let toastDuration = 3000;

    if (typeof typeOrDuration === 'number') {
        toastDuration = typeOrDuration;
    } else if (typeof typeOrDuration === 'string') {
        toastType = typeOrDuration;
        toastDuration = duration;
    }

    // Remove any previous type classes
    t.classList.remove('toast-success', 'toast-error', 'toast-info', 'toast-warning');

    // Add type class if not default info
    if (toastType !== 'info') {
        t.classList.add(`toast-${toastType}`);
    }

    t.innerHTML = msg;
    t.classList.add('show');
    setTimeout(() => {
        t.classList.remove('show');
        t.classList.remove('toast-success', 'toast-error', 'toast-info', 'toast-warning');
    }, toastDuration);
}

/**
 * Show a notification (alias for showToast with type support)
 * @param {string} message - Message to display
 * @param {string} type - Notification type (success, error, info, warning)
 */
export function showNotification(message, type = 'info') {
    showToast(message);
}

/**
 * Format bytes to human readable string
 * @param {number} bytes - Number of bytes
 * @returns {string} - Formatted string (e.g., "1.5 MB")
 */
export function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Format number to human readable string (K, M)
 * @param {number} n - Number to format
 * @returns {string} - Formatted string
 */
export function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
}

/**
 * Format duration in seconds to human readable string
 * @param {number} seconds - Duration in seconds
 * @returns {string} - Formatted string (e.g., "1.5h")
 */
export function formatDuration(seconds) {
    if (!seconds) return '0s';
    if (seconds < 60) return seconds.toFixed(0) + 's';
    if (seconds < 3600) return (seconds / 60).toFixed(1) + 'm';
    return (seconds / 3600).toFixed(1) + 'h';
}

/**
 * Format timestamp to time ago string
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {string} - Formatted string (e.g., "5m ago")
 */
export function formatTimeAgo(timestamp) {
    const seconds = Math.floor((Date.now() / 1000) - timestamp);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
}

/**
 * Format date to short string
 * @param {string} isoDate - ISO date string
 * @returns {string} - Formatted string (e.g., "Dec 15")
 */
export function formatDate(isoDate) {
    if (!isoDate) return '';
    const date = new Date(isoDate);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/**
 * Escape CSV field
 * @param {string} str - String to escape
 * @returns {string} - Escaped CSV string
 */
export function escapeCSV(str) {
    if (str === null || str === undefined) return '';
    str = String(str);
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
        return '"' + str.replace(/"/g, '""') + '"';
    }
    return str;
}

/**
 * Trigger file download
 * @param {Blob} blob - File blob
 * @param {string} filename - File name
 */
export function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/**
 * Download a file via fetch + blob (bypasses Chrome's strict cert checks for native downloads)
 * This is the preferred method for self-signed SSL certificates
 * @param {string} url - URL to download from
 * @param {string} [filename] - Optional filename (extracted from URL if not provided)
 * @returns {Promise<void>}
 */
export async function downloadFileViaFetch(url, filename = null) {
    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`Download failed: ${response.status} ${response.statusText}`);
        }

        // Extract filename from Content-Disposition header or URL
        if (!filename) {
            const contentDisposition = response.headers.get('Content-Disposition');
            if (contentDisposition) {
                const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
                if (match && match[1]) {
                    filename = match[1].replace(/['"]/g, '');
                }
            }
            // Fallback: extract from URL
            if (!filename) {
                filename = url.split('/').pop().split('?')[0] || 'download';
            }
        }

        const blob = await response.blob();
        triggerDownload(blob, filename);
    } catch (error) {
        console.error('Download error:', error);
        showToast(`Download failed: ${error.message}`, 'error');
        throw error;
    }
}

/**
 * Download data as JSON file
 * @param {object} data - Data to download
 * @param {string} filename - File name
 */
export function downloadJSON(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    triggerDownload(blob, filename);
}

/**
 * Download data as CSV file
 * @param {Array} data - Array of objects to download
 * @param {string} filename - File name
 */
export function downloadCSV(data, filename) {
    if (!data.length) return;

    const headers = ['learning_id', 'title', 'description', 'learning_type', 'problem_domain', 'outcome', 'mission_id'];
    const csvRows = [headers.join(',')];

    for (const item of data) {
        const row = headers.map(h => escapeCSV(item[h] || ''));
        csvRows.push(row.join(','));
    }

    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
    triggerDownload(blob, filename);
}

/**
 * Copy text to clipboard
 * @param {string} text - Text to copy
 */
export function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copied: ' + text.substring(0, 8) + '...', 'success');
    }).catch(err => {
        console.error('Copy failed:', err);
    });
}

// =============================================================================
// FILTER STATE PERSISTENCE
// =============================================================================

const FILTER_STATE_KEY = 'atlasforge_filter_state';

/**
 * Save a filter value to localStorage
 * @param {string} filterId - Unique identifier for the filter (e.g., 'kb-source-filter')
 * @param {string} value - The filter value to save
 */
export function saveFilterState(filterId, value) {
    try {
        const state = JSON.parse(localStorage.getItem(FILTER_STATE_KEY) || '{}');
        state[filterId] = value;
        localStorage.setItem(FILTER_STATE_KEY, JSON.stringify(state));
    } catch (e) {
        console.warn('Failed to save filter state:', e);
    }
}

/**
 * Get a saved filter value from localStorage
 * @param {string} filterId - Unique identifier for the filter
 * @param {string} defaultValue - Default value if not found (default: '')
 * @returns {string} - The saved filter value or default
 */
export function getFilterState(filterId, defaultValue = '') {
    try {
        const state = JSON.parse(localStorage.getItem(FILTER_STATE_KEY) || '{}');
        return state[filterId] !== undefined ? state[filterId] : defaultValue;
    } catch (e) {
        console.warn('Failed to get filter state:', e);
        return defaultValue;
    }
}

/**
 * Clear all saved filter states
 */
export function clearFilterState() {
    try {
        localStorage.removeItem(FILTER_STATE_KEY);
    } catch (e) {
        console.warn('Failed to clear filter state:', e);
    }
}

/**
 * Restore filter value to a select element
 * @param {string} elementId - ID of the select element
 * @param {string} filterId - Key used to store the filter value (defaults to elementId)
 */
export function restoreFilterToElement(elementId, filterId = null) {
    const element = document.getElementById(elementId);
    if (!element) return;

    const savedValue = getFilterState(filterId || elementId);
    if (savedValue && savedValue !== element.value) {
        element.value = savedValue;
    }
}
