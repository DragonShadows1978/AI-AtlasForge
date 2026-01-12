/**
 * Dashboard API Module (ES6)
 * API fetch wrapper and related utilities
 * Dependencies: core.js
 */

import { showNotification } from './core.js';

// =============================================================================
// API WRAPPER
// =============================================================================

/**
 * Make an API call
 * @param {string} endpoint - API endpoint
 * @param {string|object} methodOrOptions - HTTP method or fetch options object
 * @param {object} body - Request body (optional)
 * @returns {Promise<object>} - Response JSON
 */
export async function api(endpoint, methodOrOptions = 'GET', body = null) {
    let opts;

    // Support both api(url, method, body) and api(url, {method, body, headers})
    if (typeof methodOrOptions === 'string') {
        opts = { method: methodOrOptions };
        if (body) {
            opts.headers = {'Content-Type': 'application/json'};
            opts.body = JSON.stringify(body);
        }
    } else {
        opts = methodOrOptions;
    }

    const resp = await fetch(endpoint, opts);
    return resp.json();
}

/**
 * API with error handling and notification
 * @param {string} endpoint - API endpoint
 * @param {object} options - Fetch options
 * @returns {Promise<object>} - Response JSON or null on error
 */
export async function apiSafe(endpoint, options = {}) {
    try {
        const resp = await fetch(endpoint, options);
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }
        return await resp.json();
    } catch (e) {
        console.error('API Error:', e);
        showNotification('API Error: ' + e.message, 'error');
        return null;
    }
}
