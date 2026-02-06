/**
 * Dashboard Socket Module (ES6)
 * WebSocket connection manager with real-time push support and polling fallback
 * Dependencies: core.js
 */

import { showToast } from './core.js';

// =============================================================================
// CONFIGURATION
// =============================================================================

const CONFIG = {
    // Reconnection settings
    reconnect: {
        baseDelay: 1000,       // 1 second initial delay
        maxDelay: 30000,       // 30 seconds max delay
        maxAttempts: 10,       // Max reconnection attempts before giving up
        jitterFactor: 0.25     // Random jitter to prevent thundering herd
    },
    // Health check settings
    health: {
        pingInterval: 30000,    // Ping every 30 seconds
        pongTimeout: 5000,      // Expect pong within 5 seconds
        staleThreshold: 60000   // Consider connection stale after 60 seconds
    },
    // Polling fallback settings (when WebSocket unavailable)
    polling: {
        interval: 5000,         // Poll every 5 seconds
        endpoints: {
            mission_status: '/api/status',
            journal: '/api/journal',
            atlasforge_stats: '/api/atlasforge/exploration-stats',
            analytics: '/api/analytics/current',
            chat_history: '/api/chat-history'  // Chat fallback polling
        }
    },
    // Chat-specific settings
    chat: {
        pollInterval: 3000,     // Poll chat every 3 seconds when fallback active
        seenMessages: new Set() // Track already-displayed messages
    }
};

// =============================================================================
// CONNECTION STATE
// =============================================================================

// Main chat socket
let socket = null;

// Widget push socket (for real-time widget updates)
let widgetSocket = null;

// Connection state
let connectionState = {
    main: 'disconnected',     // disconnected, connecting, connected, reconnecting
    widgets: 'disconnected',
    clientId: null,
    subscribedRooms: new Set(),
    lastPong: 0,
    lastUpdate: 0,
    reconnectAttempts: 0,
    reconnectTimer: null,
    healthCheckTimer: null,
    pollingTimer: null,
    pollingEnabled: false
};

// Event handlers registry
const eventHandlers = {
    mission_status: [],
    journal: [],
    atlasforge_stats: [],
    glassbox: [],
    analytics: [],
    exploration: [],
    connection_status: [],
    backup_status: [],
    backup_stale_alert: [],
    file_events: [],
    glassbox_archive: [],
    recommendations: [],
    queue_updated: [],
    queue_paused: [],
    queue_resumed: []
};

// =============================================================================
// CONNECTION STATUS INDICATOR
// =============================================================================

function updateConnectionIndicator(status, message = null) {
    const indicator = document.getElementById('ws-connection-indicator');
    const statusText = document.getElementById('ws-connection-status');

    if (indicator) {
        indicator.className = 'ws-indicator ws-' + status;
        indicator.setAttribute('data-status', status);
    }

    if (statusText && message) {
        statusText.textContent = message;
    }

    // Notify connection status handlers
    eventHandlers.connection_status.forEach(handler => {
        try {
            handler({ status, message, timestamp: Date.now() });
        } catch (e) {
            console.error('Connection status handler error:', e);
        }
    });
}

function updateOfflineIndicator(offline, customMessage = null) {
    const indicator = document.getElementById('offline-indicator');
    if (!indicator) return;

    if (offline) {
        indicator.classList.add('visible');
        indicator.textContent = customMessage ||
            `Connection Lost - Reconnecting (attempt ${connectionState.reconnectAttempts + 1})...`;
        updateConnectionIndicator('disconnected', 'Offline');
    } else {
        indicator.classList.remove('visible');
        updateConnectionIndicator('connected', 'Connected');
    }
}

// =============================================================================
// EXPONENTIAL BACKOFF RECONNECTION
// =============================================================================

function getReconnectDelay() {
    const { baseDelay, maxDelay, jitterFactor } = CONFIG.reconnect;
    const delay = Math.min(
        baseDelay * Math.pow(2, connectionState.reconnectAttempts),
        maxDelay
    );
    // Add jitter
    return delay + Math.random() * delay * jitterFactor;
}

function scheduleReconnect() {
    if (connectionState.reconnectTimer) {
        clearTimeout(connectionState.reconnectTimer);
    }

    if (connectionState.reconnectAttempts >= CONFIG.reconnect.maxAttempts) {
        console.warn('Max reconnect attempts reached, falling back to polling');
        updateConnectionIndicator('error', 'Connection failed');
        enablePollingFallback();
        return;
    }

    const delay = getReconnectDelay();
    console.log(`Scheduling reconnect attempt ${connectionState.reconnectAttempts + 1} in ${Math.round(delay)}ms`);
    updateConnectionIndicator('reconnecting', `Reconnecting (${connectionState.reconnectAttempts + 1}/${CONFIG.reconnect.maxAttempts})`);

    connectionState.reconnectTimer = setTimeout(() => {
        connectionState.reconnectAttempts++;
        attemptReconnect();
    }, delay);
}

async function attemptReconnect() {
    try {
        const response = await fetch('/api/health', {
            method: 'GET',
            cache: 'no-cache'
        });

        if (response.ok) {
            // Server is back, reinitialize sockets
            connectionState.reconnectAttempts = 0;
            disablePollingFallback();
            initializeSockets();
            updateOfflineIndicator(false);
            showToast('Connection restored!');
        } else {
            scheduleReconnect();
        }
    } catch (e) {
        console.warn('Reconnect attempt failed:', e.message);
        scheduleReconnect();
    }
}

// =============================================================================
// HEALTH CHECK (PING/PONG)
// =============================================================================

function startHealthCheck() {
    if (connectionState.healthCheckTimer) {
        clearInterval(connectionState.healthCheckTimer);
    }

    connectionState.healthCheckTimer = setInterval(() => {
        if (widgetSocket && widgetSocket.connected) {
            // Send ping
            const pingTime = Date.now();
            widgetSocket.emit('ping');

            // Check for stale connection
            if (connectionState.lastPong > 0) {
                const timeSinceLastPong = pingTime - connectionState.lastPong;
                if (timeSinceLastPong > CONFIG.health.staleThreshold) {
                    console.warn('Connection appears stale, forcing reconnect');
                    widgetSocket.disconnect();
                }
            }
        }
    }, CONFIG.health.pingInterval);
}

function stopHealthCheck() {
    if (connectionState.healthCheckTimer) {
        clearInterval(connectionState.healthCheckTimer);
        connectionState.healthCheckTimer = null;
    }
}

// =============================================================================
// POLLING FALLBACK
// =============================================================================

function enablePollingFallback() {
    if (connectionState.pollingEnabled) return;

    console.log('Enabling polling fallback');
    connectionState.pollingEnabled = true;
    updateConnectionIndicator('polling', 'Polling mode');

    connectionState.pollingTimer = setInterval(async () => {
        await pollAllEndpoints();
    }, CONFIG.polling.interval);

    // Initial poll
    pollAllEndpoints();
}

function disablePollingFallback() {
    if (!connectionState.pollingEnabled) return;

    console.log('Disabling polling fallback');
    connectionState.pollingEnabled = false;

    if (connectionState.pollingTimer) {
        clearInterval(connectionState.pollingTimer);
        connectionState.pollingTimer = null;
    }
}

async function pollAllEndpoints() {
    const { endpoints } = CONFIG.polling;

    for (const [room, url] of Object.entries(endpoints)) {
        try {
            const response = await fetch(url);
            if (response.ok) {
                const data = await response.json();

                // Special handling for chat_history - render messages directly
                if (room === 'chat_history' && data.messages) {
                    handleChatHistoryPoll(data.messages);
                } else {
                    dispatchUpdate(room, data);
                }
            }
        } catch (e) {
            // Polling endpoint failed, continue with others
        }
    }
}

/**
 * Handle chat history poll - display messages not already seen
 * @param {Array} messages - Chat messages from API
 */
function handleChatHistoryPoll(messages) {
    if (!Array.isArray(messages) || typeof window.addMessage !== 'function') {
        return;
    }

    messages.forEach(msg => {
        // Create unique message ID
        const msgId = `${msg.timestamp || ''}:${(msg.content || '').substring(0, 50)}`;

        // Only add if not already seen
        if (!CONFIG.chat.seenMessages.has(msgId)) {
            CONFIG.chat.seenMessages.add(msgId);
            window.addMessage(msg.role, msg.content, msg.timestamp, {
                displayRole: msg.display_role,
                provider: msg.provider
            });
        }
    });

    // Prevent memory leak - keep only last 500 message IDs
    if (CONFIG.chat.seenMessages.size > 500) {
        const arr = Array.from(CONFIG.chat.seenMessages);
        CONFIG.chat.seenMessages = new Set(arr.slice(-250));
    }
}

// Chat polling fallback timer
let chatPollingTimer = null;

/**
 * Start chat-specific polling fallback when main socket disconnects
 */
function startChatPollingFallback() {
    // Don't start if already running
    if (chatPollingTimer) return;

    console.log('Starting chat polling fallback');
    updateChatConnectionIndicator('polling');

    chatPollingTimer = setInterval(async () => {
        try {
            const response = await fetch('/api/chat-history');
            if (response.ok) {
                const data = await response.json();
                if (data.messages) {
                    handleChatHistoryPoll(data.messages);
                }
            }
        } catch (e) {
            console.warn('Chat polling failed:', e.message);
        }
    }, CONFIG.chat.pollInterval);

    // Immediate first poll
    fetch('/api/chat-history')
        .then(r => r.json())
        .then(data => {
            if (data.messages) handleChatHistoryPoll(data.messages);
        })
        .catch(() => {});
}

/**
 * Stop chat polling fallback (when socket reconnects)
 */
function stopChatPollingFallback() {
    if (chatPollingTimer) {
        console.log('Stopping chat polling fallback');
        clearInterval(chatPollingTimer);
        chatPollingTimer = null;
        updateChatConnectionIndicator('connected');
    }
}

/**
 * Update the chat socket connection indicator
 * @param {string} status - connected, polling, disconnected, error
 */
function updateChatConnectionIndicator(status) {
    const indicator = document.getElementById('chat-socket-indicator');
    if (indicator) {
        indicator.className = 'ws-indicator ws-' + status;
        indicator.setAttribute('title', `Chat: ${status}`);
    }
}

// =============================================================================
// EVENT DISPATCHING
// =============================================================================

function dispatchUpdate(room, data) {
    connectionState.lastUpdate = Date.now();

    const handlers = eventHandlers[room];
    if (handlers && handlers.length > 0) {
        handlers.forEach(handler => {
            try {
                handler(data);
            } catch (e) {
                console.error(`Error in ${room} handler:`, e);
            }
        });
    }
}

// =============================================================================
// SOCKET INITIALIZATION
// =============================================================================

function initializeSockets() {
    initMainSocket();
    initWidgetSocket();
}

function initMainSocket() {
    try {
        if (typeof io === 'undefined') {
            console.warn('Socket.io not loaded, skipping main socket');
            return;
        }

        if (socket) {
            socket.disconnect();
        }

        connectionState.main = 'connecting';

        socket = io({
            reconnection: true,
            reconnectionDelay: CONFIG.reconnect.baseDelay,
            reconnectionDelayMax: CONFIG.reconnect.maxDelay,
            reconnectionAttempts: CONFIG.reconnect.maxAttempts
        });

        socket.on('connect', () => {
            console.log('Main socket connected');
            connectionState.main = 'connected';
            connectionState.reconnectAttempts = 0;
            updateOfflineIndicator(false);

            // Stop chat polling fallback when socket reconnects
            stopChatPollingFallback();
            updateChatConnectionIndicator('connected');
        });

        socket.on('disconnect', (reason) => {
            console.log('Main socket disconnected:', reason);
            connectionState.main = 'disconnected';

            // Start chat polling fallback when main socket disconnects
            startChatPollingFallback();

            if (reason === 'io server disconnect') {
                socket.connect();
            }
        });

        socket.on('connect_error', (error) => {
            console.warn('Socket connection error:', error.message);
            connectionState.main = 'disconnected';
            updateOfflineIndicator(true);

            // Start chat polling fallback on connection error
            startChatPollingFallback();
        });

        socket.on('message', (data) => {
            if (typeof window.addMessage === 'function') {
                // Track message as seen to prevent duplicate when polling
                const msgId = `${data.timestamp || ''}:${(data.content || '').substring(0, 50)}`;
                CONFIG.chat.seenMessages.add(msgId);

                window.addMessage(data.role, data.content, data.timestamp, {
                    displayRole: data.display_role,
                    provider: data.provider
                });
            }
        });
    } catch (e) {
        console.error('Main socket init error:', e);
    }
}

function initWidgetSocket() {
    try {
        if (typeof io === 'undefined') {
            console.warn('Socket.io not loaded, enabling polling fallback');
            enablePollingFallback();
            return;
        }

        if (widgetSocket) {
            widgetSocket.disconnect();
        }

        connectionState.widgets = 'connecting';
        updateConnectionIndicator('connecting', 'Connecting...');

        widgetSocket = io('/widgets', {
            reconnection: true,
            reconnectionDelay: CONFIG.reconnect.baseDelay,
            reconnectionDelayMax: CONFIG.reconnect.maxDelay,
            reconnectionAttempts: CONFIG.reconnect.maxAttempts
        });

        // Connection events
        widgetSocket.on('connect', () => {
            console.log('Widget socket connected');
            connectionState.widgets = 'connected';
            connectionState.reconnectAttempts = 0;
            disablePollingFallback();
            updateConnectionIndicator('connected', 'Connected');
            startHealthCheck();

            // Subscribe to all rooms we were subscribed to before
            resubscribeToRooms();
        });

        widgetSocket.on('connected', (data) => {
            console.log('Widget socket handshake complete:', data);
            connectionState.clientId = data.client_id;
        });

        widgetSocket.on('disconnect', (reason) => {
            console.log('Widget socket disconnected:', reason);
            connectionState.widgets = 'disconnected';
            stopHealthCheck();
            updateConnectionIndicator('disconnected', 'Disconnected');

            if (reason === 'io server disconnect') {
                // Server kicked us, try to reconnect
                scheduleReconnect();
            }
        });

        widgetSocket.on('connect_error', (error) => {
            console.warn('Widget socket error:', error.message);
            connectionState.widgets = 'disconnected';
            updateConnectionIndicator('error', 'Connection error');
        });

        // Subscription events
        widgetSocket.on('subscribed', (data) => {
            console.log('Subscribed to room:', data.room);
            connectionState.subscribedRooms.add(data.room);

            // Dispatch initial data if available
            if (data.initial_data) {
                dispatchUpdate(data.room, data.initial_data);
            }
        });

        widgetSocket.on('subscribed_all', (data) => {
            console.log('Subscribed to all rooms:', data.rooms);
            data.rooms.forEach(room => connectionState.subscribedRooms.add(room));
        });

        widgetSocket.on('unsubscribed', (data) => {
            console.log('Unsubscribed from room:', data.room);
            connectionState.subscribedRooms.delete(data.room);
        });

        // Update events
        widgetSocket.on('update', (data) => {
            const { room, data: payload, timestamp } = data;
            dispatchUpdate(room, payload);
        });

        widgetSocket.on('state_change', (data) => {
            const { event, room, data: payload, timestamp } = data;
            console.log(`State change: ${event} in ${room}`);
            dispatchUpdate(room, payload);
        });

        // Health check events
        widgetSocket.on('pong', (data) => {
            connectionState.lastPong = Date.now();
        });

        widgetSocket.on('error', (data) => {
            console.error('Widget socket error:', data.message);
        });

    } catch (e) {
        console.error('Widget socket init error:', e);
        enablePollingFallback();
    }
}

function resubscribeToRooms() {
    if (connectionState.subscribedRooms.size === 0) {
        // Subscribe to default rooms
        subscribeToRoom('mission_status');
        subscribeToRoom('journal');
        subscribeToRoom('atlasforge_stats');
        subscribeToRoom('analytics');
        // Subscribe to new real-time push rooms
        subscribeToRoom('file_events');
        subscribeToRoom('glassbox_archive');
        subscribeToRoom('glassbox');
        subscribeToRoom('recommendations');
        // Subscribe to queue events
        subscribeToRoom('queue_updated');
    } else {
        // Resubscribe to previously subscribed rooms
        connectionState.subscribedRooms.forEach(room => {
            widgetSocket.emit('subscribe', { room });
        });
    }
}

// =============================================================================
// PUBLIC API
// =============================================================================

/**
 * Subscribe to updates from a specific room
 * @param {string} room - Room name to subscribe to
 * @param {function} handler - Optional handler for updates from this room
 */
export function subscribeToRoom(room, handler = null) {
    if (handler) {
        registerHandler(room, handler);
    }

    if (widgetSocket && widgetSocket.connected) {
        widgetSocket.emit('subscribe', { room });
    }
    connectionState.subscribedRooms.add(room);
}

/**
 * Unsubscribe from a room
 * @param {string} room - Room name to unsubscribe from
 */
export function unsubscribeFromRoom(room) {
    if (widgetSocket && widgetSocket.connected) {
        widgetSocket.emit('unsubscribe', { room });
    }
    connectionState.subscribedRooms.delete(room);
}

/**
 * Subscribe to all available rooms
 */
export function subscribeToAll() {
    if (widgetSocket && widgetSocket.connected) {
        widgetSocket.emit('subscribe_all');
    }
}

/**
 * Register an event handler for a specific room
 * @param {string} room - Room name
 * @param {function} handler - Handler function
 */
export function registerHandler(room, handler) {
    if (eventHandlers[room]) {
        eventHandlers[room].push(handler);
    } else {
        console.warn(`Unknown room: ${room}`);
    }
}

/**
 * Unregister an event handler
 * @param {string} room - Room name
 * @param {function} handler - Handler function to remove
 */
export function unregisterHandler(room, handler) {
    if (eventHandlers[room]) {
        const idx = eventHandlers[room].indexOf(handler);
        if (idx > -1) {
            eventHandlers[room].splice(idx, 1);
        }
    }
}

/**
 * Get current connection state
 * @returns {object} Connection state info
 */
export function getConnectionState() {
    return {
        main: connectionState.main,
        widgets: connectionState.widgets,
        clientId: connectionState.clientId,
        subscribedRooms: Array.from(connectionState.subscribedRooms),
        lastUpdate: connectionState.lastUpdate,
        pollingEnabled: connectionState.pollingEnabled,
        isConnected: connectionState.widgets === 'connected'
    };
}

/**
 * Get the main chat socket
 * @returns {Socket} Main socket instance
 */
export function getSocket() {
    return socket;
}

/**
 * Get the widget push socket
 * @returns {Socket} Widget socket instance
 */
export function getWidgetSocket() {
    return widgetSocket;
}

/**
 * Check if connection is offline
 * @returns {boolean} True if offline
 */
export function isConnectionOffline() {
    return connectionState.widgets !== 'connected' && !connectionState.pollingEnabled;
}

/**
 * Get reconnect attempts count
 * @returns {number} Number of reconnect attempts
 */
export function getReconnectAttempts() {
    return connectionState.reconnectAttempts;
}

/**
 * Force reconnection
 */
export function forceReconnect() {
    connectionState.reconnectAttempts = 0;
    if (widgetSocket) {
        widgetSocket.disconnect();
    }
    if (socket) {
        socket.disconnect();
    }
    setTimeout(initializeSockets, 500);
}

/**
 * Enable or disable widget updates
 * @param {boolean} enabled - Whether updates are enabled
 */
export function setWidgetUpdateEnabled(enabled) {
    // When disabled, just don't dispatch updates
    // This is now handled by the individual widgets
}

/**
 * Load initial chat history via REST API
 * This handles the race condition where WebSocket messages might arrive
 * before window.addMessage is defined.
 */
export async function loadInitialChatHistory() {
    try {
        const response = await fetch('/api/chat-history');
        if (response.ok) {
            const data = await response.json();
            if (data.messages && Array.isArray(data.messages)) {
                handleChatHistoryPoll(data.messages);
                console.log(`Loaded ${data.messages.length} initial chat messages`);
                updateChatConnectionIndicator('connected');
            }
        }
    } catch (e) {
        console.warn('Failed to load initial chat history:', e.message);
    }
}

// =============================================================================
// BROWSER EVENTS
// =============================================================================

// Handle online/offline events
window.addEventListener('online', () => {
    console.log('Browser went online');
    updateOfflineIndicator(false);
    if (connectionState.widgets !== 'connected') {
        attemptReconnect();
    }
});

window.addEventListener('offline', () => {
    console.log('Browser went offline');
    updateOfflineIndicator(true, 'No internet connection');
});

// Handle visibility change (tab focus)
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        // Tab became visible, check connection health
        if (connectionState.widgets !== 'connected' && !connectionState.pollingEnabled) {
            attemptReconnect();
        }
    }
});

// =============================================================================
// GLOBAL REGISTRATION FOR CROSS-MODULE ACCESS
// =============================================================================

// Make registerHandler available globally for widgets.js integration
window.registerSocketHandler = registerHandler;
window.unregisterSocketHandler = unregisterHandler;
window.subscribeToSocketRoom = subscribeToRoom;
window.unsubscribeFromSocketRoom = unsubscribeFromRoom;
window.getSocketConnectionState = getConnectionState;

// =============================================================================
// EXPORTS
// =============================================================================

export {
    initializeSockets,
    updateOfflineIndicator,
    dispatchUpdate,
    enablePollingFallback,
    disablePollingFallback
};
