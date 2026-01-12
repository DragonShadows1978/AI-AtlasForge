/**
 * Dashboard API Module
 * API fetch wrapper and related utilities
 * Dependencies: core.js
 */

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
async function api(endpoint, methodOrOptions = 'GET', body = null) {
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

// Debug: mark api module loaded
console.log('API module loaded');
