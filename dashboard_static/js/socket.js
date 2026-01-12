/**
 * Dashboard Socket Module
 * Socket.io connection and event handlers
 * Dependencies: core.js
 */

// =============================================================================
// SOCKET INITIALIZATION
// =============================================================================

// Main socket
let socket = null;
let widgetSocket = null;
let widgetUpdateEnabled = true;

// Safe socket initialization
try {
    if (typeof io !== 'undefined') {
        socket = io();
        console.log('Main socket created');
    } else {
        console.warn('Socket.io not loaded');
    }
} catch (e) {
    console.error('Socket init error:', e);
}

// Socket events (only if socket exists)
if (socket) {
    socket.on('connect', () => console.log('Connected'));
    socket.on('message', (data) => {
        if (typeof addMessage === 'function') {
            addMessage(data.role, data.content, data.timestamp);
        }
    });
}

// =============================================================================
// WIDGET SOCKET
// =============================================================================

try {
    if (typeof io !== 'undefined') {
        widgetSocket = io('/widgets');
        console.log('Widget socket created');
    }
} catch (e) {
    console.error('Widget socket init error:', e);
}

if (widgetSocket) {
    widgetSocket.on('connect', () => {
        console.log('Widget socket connected');
        // Subscribe to widget rooms
        widgetSocket.emit('subscribe', {room: 'mission_status'});
        widgetSocket.emit('subscribe', {room: 'journal'});
        widgetSocket.emit('subscribe', {room: 'rde_stats'});
        widgetSocket.emit('subscribe', {room: 'decision_graph'});
    });

    widgetSocket.on('subscribed', (data) => {
        console.log('Subscribed to widget room:', data.room);
    });

    widgetSocket.on('update', (data) => {
        if (!widgetUpdateEnabled) return;

        const room = data.room;
        const payload = data.data;

        if (room === 'mission_status') {
            // Update mission status UI from WebSocket
            if (payload.rd_stage && typeof updateStatusBar === 'function') {
                updateStatusBar(payload);
            }
        } else if (room === 'journal') {
            // Update journal entries from WebSocket
            if (payload.entries && Array.isArray(payload.entries) && typeof renderJournalEntries === 'function') {
                renderJournalEntries(payload.entries);
            }
        }
    });
}

// =============================================================================
// OFFLINE DETECTION & RECONNECTION
// =============================================================================

let isOffline = false;
let offlineCheckInterval = null;

function updateOfflineIndicator(offline) {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator) return;

    if (offline && !isOffline) {
        indicator.classList.add('visible');
        isOffline = true;
        // Start reconnection attempts
        if (!offlineCheckInterval) {
            offlineCheckInterval = setInterval(checkOnlineStatus, 3000);
        }
    } else if (!offline && isOffline) {
        indicator.classList.remove('visible');
        isOffline = false;
        showToast('Connection restored!');
        // Stop reconnection attempts
        if (offlineCheckInterval) {
            clearInterval(offlineCheckInterval);
            offlineCheckInterval = null;
        }
        // Refresh data
        if (typeof refresh === 'function') {
            refresh();
        }
    }
}

async function checkOnlineStatus() {
    try {
        const response = await fetch('/api/status', { timeout: 5000 });
        if (response.ok) {
            updateOfflineIndicator(false);
        }
    } catch (e) {
        // Still offline
    }
}

// Monitor API errors for offline detection
const originalFetch = window.fetch;
window.fetch = async function(...args) {
    try {
        const response = await originalFetch.apply(this, args);
        updateOfflineIndicator(false);
        return response;
    } catch (e) {
        if (e.name === 'TypeError' && e.message.includes('Failed to fetch')) {
            updateOfflineIndicator(true);
        }
        throw e;
    }
};

// Browser online/offline events
window.addEventListener('online', () => updateOfflineIndicator(false));
window.addEventListener('offline', () => updateOfflineIndicator(true));

// Debug: mark socket module loaded
console.log('Socket module loaded');
