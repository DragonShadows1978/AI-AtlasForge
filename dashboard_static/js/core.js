/**
 * Dashboard Core Module
 * Global utilities, constants, and helper functions shared across all modules
 * Dependencies: None (loads first)
 */

// =============================================================================
// CONSTANTS
// =============================================================================

const stages = ['PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'CYCLE_END', 'COMPLETE'];

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

/**
 * Escape HTML to prevent XSS attacks
 * @param {string} text - Text to escape
 * @returns {string} - Escaped HTML string
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show a toast notification
 * @param {string} msg - Message to display
 * @param {number} duration - Duration in milliseconds (default 3000)
 */
function showToast(msg, duration = 3000) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.innerHTML = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), duration);
}

/**
 * Show a notification (alias for showToast with type support)
 * @param {string} message - Message to display
 * @param {string} type - Notification type (success, error, info, warning)
 */
function showNotification(message, type = 'info') {
    showToast(message);
}

/**
 * Format bytes to human readable string
 * @param {number} bytes - Number of bytes
 * @returns {string} - Formatted string (e.g., "1.5 MB")
 */
function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Format number to human readable string (K, M)
 * @param {number} n - Number to format
 * @returns {string} - Formatted string
 */
function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
}

/**
 * Format duration in seconds to human readable string
 * @param {number} seconds - Duration in seconds
 * @returns {string} - Formatted string (e.g., "1.5h")
 */
function formatDuration(seconds) {
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
function formatTimeAgo(timestamp) {
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
function formatDate(isoDate) {
    if (!isoDate) return '';
    const date = new Date(isoDate);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/**
 * Escape CSV field
 * @param {string} str - String to escape
 * @returns {string} - Escaped CSV string
 */
function escapeCSV(str) {
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
function triggerDownload(blob, filename) {
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
 * Download data as JSON file
 * @param {object} data - Data to download
 * @param {string} filename - File name
 */
function downloadJSON(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    triggerDownload(blob, filename);
}

/**
 * Download data as CSV file
 * @param {Array} data - Array of objects to download
 * @param {string} filename - File name
 */
function downloadCSV(data, filename) {
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
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copied: ' + text.substring(0, 8) + '...', 'success');
    }).catch(err => {
        console.error('Copy failed:', err);
    });
}

// Debug: mark core module loaded
console.log('Core module loaded');
