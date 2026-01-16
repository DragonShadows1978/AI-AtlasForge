/**
 * Dashboard Service Worker v2
 * Provides offline capability, caching, and performance optimizations
 *
 * Version: 2.0.0
 * Features:
 * - Stale-while-revalidate for static assets
 * - Pre-caching of critical resources
 * - Network-first for API with cache fallback
 * - Background sync for failed requests
 */

const CACHE_VERSION = 'v5';
const CACHE_NAME = `atlasforge-dashboard-${CACHE_VERSION}`;
const STATIC_CACHE = `atlasforge-static-${CACHE_VERSION}`;
const API_CACHE = `atlasforge-api-${CACHE_VERSION}`;
const CDN_CACHE = `atlasforge-cdn-${CACHE_VERSION}`;

// Critical assets - pre-cache on install (required for app to function)
const CRITICAL_ASSETS = [
    '/',
    '/static/dist/bundle.min.js',
    '/static/dist/bundle.min.css'
];

// Static assets to cache on demand
const STATIC_ASSETS = [
    '/static/css/main.css'
];

// CDN resources - cache on first use with long TTL
const CDN_RESOURCES = [
    'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js',
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js',
    'https://cdn.jsdelivr.net/npm/vis-timeline@7.7.3/standalone/umd/vis-timeline-graph2d.min.js',
    'https://cdn.jsdelivr.net/npm/vis-timeline@7.7.3/styles/vis-timeline-graph2d.min.css',
    'https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css',
    'https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js',
    'https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js'
];

// API endpoints that can be cached
const CACHEABLE_API = [
    '/api/status',
    '/api/journal',
    '/api/files',
    '/api/recommendations',
    '/api/analytics/summary',
    '/api/knowledge-base/learnings'
];

// =============================================================================
// INSTALL EVENT
// =============================================================================

self.addEventListener('install', (event) => {
    console.log('[SW] Installing service worker v2');

    event.waitUntil(
        Promise.all([
            // Cache critical assets (required)
            caches.open(STATIC_CACHE).then((cache) => {
                console.log('[SW] Caching critical assets');
                return cache.addAll(CRITICAL_ASSETS);
            }),
            // Cache CDN resources (best effort)
            caches.open(CDN_CACHE).then((cache) => {
                console.log('[SW] Pre-caching CDN resources');
                return Promise.allSettled(
                    CDN_RESOURCES.map(url =>
                        cache.add(url).catch(err => {
                            console.log(`[SW] Failed to cache CDN ${url}:`, err.message);
                        })
                    )
                );
            })
        ])
        .then(() => {
            console.log('[SW] Installation complete');
            return self.skipWaiting();
        })
    );
});

// =============================================================================
// ACTIVATE EVENT
// =============================================================================

self.addEventListener('activate', (event) => {
    console.log('[SW] Activating service worker v2');

    const VALID_CACHES = [STATIC_CACHE, API_CACHE, CDN_CACHE];

    event.waitUntil(
        caches.keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames
                        .filter((name) => {
                            // Delete any cache that starts with 'atlasforge-' but isn't in our valid list
                            return name.startsWith('atlasforge-') && !VALID_CACHES.includes(name);
                        })
                        .map((name) => {
                            console.log('[SW] Deleting old cache:', name);
                            return caches.delete(name);
                        })
                );
            })
            .then(() => {
                console.log('[SW] Claiming clients');
                return self.clients.claim();
            })
    );
});

// =============================================================================
// FETCH EVENT
// =============================================================================

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Skip WebSocket requests
    if (url.protocol === 'ws:' || url.protocol === 'wss:') {
        return;
    }

    // Skip chrome-extension and other non-http(s) protocols
    if (!url.protocol.startsWith('http')) {
        return;
    }

    // Handle different request types with appropriate caching strategies
    if (url.pathname.startsWith('/api/')) {
        // API requests: Network first, cache fallback (for offline support)
        event.respondWith(handleApiRequest(event.request));
    } else if (url.pathname.startsWith('/static/dist/')) {
        // Bundled assets: Stale-while-revalidate (fast + fresh)
        event.respondWith(handleStaleWhileRevalidate(event.request, STATIC_CACHE));
    } else if (url.pathname.startsWith('/static/')) {
        // Other static assets: Cache first, network fallback
        event.respondWith(handleStaticRequest(event.request));
    } else if (url.hostname !== location.hostname) {
        // CDN resources: Cache first with long TTL
        event.respondWith(handleCdnRequest(event.request));
    } else {
        // HTML pages: Network first with cache fallback
        event.respondWith(handlePageRequest(event.request));
    }
});

// =============================================================================
// REQUEST HANDLERS
// =============================================================================

/**
 * Handle API requests - Network first, cache fallback
 */
async function handleApiRequest(request) {
    const url = new URL(request.url);
    const isCacheable = CACHEABLE_API.some(path => url.pathname.startsWith(path));

    try {
        const response = await fetch(request);

        // Cache successful responses for cacheable endpoints
        if (response.ok && isCacheable) {
            const cache = await caches.open(API_CACHE);
            cache.put(request, response.clone());
        }

        return response;
    } catch (error) {
        console.log('[SW] Network failed for API, trying cache:', url.pathname);

        // Try cache
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }

        // Return offline response for known endpoints
        return new Response(
            JSON.stringify({ error: 'offline', message: 'You are offline' }),
            {
                status: 503,
                headers: { 'Content-Type': 'application/json' }
            }
        );
    }
}

/**
 * Handle static assets - Cache first, network fallback
 */
async function handleStaticRequest(request) {
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
        return cachedResponse;
    }

    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(STATIC_CACHE);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        console.log('[SW] Network failed for static:', request.url);
        return new Response('', { status: 503 });
    }
}

/**
 * Handle CDN resources - Cache first, network fallback
 */
async function handleCdnRequest(request) {
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
        return cachedResponse;
    }

    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CDN_CACHE);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        console.log('[SW] CDN request failed:', request.url);
        return new Response('', { status: 503 });
    }
}

/**
 * Stale-while-revalidate strategy
 * Returns cached response immediately, then updates cache in background
 */
async function handleStaleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cachedResponse = await cache.match(request);

    // Start network fetch (don't await initially)
    const networkPromise = fetch(request).then((response) => {
        if (response.ok) {
            cache.put(request, response.clone());
        }
        return response;
    }).catch((error) => {
        console.log('[SW] Background revalidation failed:', request.url);
        return null;
    });

    // Return cached response if available, otherwise wait for network
    if (cachedResponse) {
        return cachedResponse;
    }

    const networkResponse = await networkPromise;
    if (networkResponse) {
        return networkResponse;
    }

    return new Response('', { status: 503 });
}

/**
 * Handle page requests - Network first, cache fallback
 */
async function handlePageRequest(request) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(STATIC_CACHE);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        console.log('[SW] Network failed for page, trying cache');

        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }

        // Return offline page
        return new Response(
            `<!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Offline - AtlasForge Dashboard</title>
                <style>
                    body {
                        font-family: system-ui, sans-serif;
                        background: #0d1117;
                        color: #c9d1d9;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                    }
                    .offline-message {
                        text-align: center;
                        padding: 40px;
                    }
                    h1 { color: #58a6ff; }
                    button {
                        background: #238636;
                        color: white;
                        border: none;
                        padding: 10px 20px;
                        border-radius: 6px;
                        cursor: pointer;
                        margin-top: 20px;
                    }
                    button:hover { background: #2ea043; }
                </style>
            </head>
            <body>
                <div class="offline-message">
                    <h1>You're Offline</h1>
                    <p>The AtlasForge Dashboard requires a network connection.</p>
                    <p>Please check your connection and try again.</p>
                    <button onclick="location.reload()">Retry</button>
                </div>
            </body>
            </html>`,
            {
                status: 503,
                headers: { 'Content-Type': 'text/html' }
            }
        );
    }
}

// =============================================================================
// MESSAGE HANDLING
// =============================================================================

self.addEventListener('message', (event) => {
    if (event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }

    if (event.data.type === 'CLEAR_CACHE') {
        caches.keys().then((names) => {
            names.forEach((name) => caches.delete(name));
        });
    }
});

// =============================================================================
// PERFORMANCE UTILITIES
// =============================================================================

/**
 * Pre-cache resources on network idle
 * Called via message from main thread
 */
async function precacheOnIdle(urls) {
    const cache = await caches.open(CDN_CACHE);
    for (const url of urls) {
        try {
            if (!(await cache.match(url))) {
                await cache.add(url);
                console.log('[SW] Pre-cached:', url);
            }
        } catch (e) {
            console.log('[SW] Pre-cache failed:', url);
        }
    }
}

self.addEventListener('message', (event) => {
    if (event.data.type === 'PRECACHE_IDLE') {
        precacheOnIdle(event.data.urls);
    }
});

console.log('[SW] Service worker v2 loaded');
