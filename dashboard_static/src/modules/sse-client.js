/**
 * sse-client.js — Thin EventSource wrapper for AtlasForge agent activity panels.
 *
 * Native EventSource gives us reconnect + Last-Event-ID for free. This wrapper
 * adds:
 *   - Page-reload resume via localStorage (?since=lastSeq on first connect).
 *   - Per-event-name handler registration matching SSE `event:` field.
 *   - Backpressure recovery via /api/agents/<id>/history?since=N on `gap`.
 *   - close() teardown for panel switching.
 */

const SSE_LAST_SEQ_PREFIX = 'sse:lastSeq:';

export class StreamClient {
    /**
     * @param {string} baseUrl - e.g. '/api/agents/stream?context=mission'
     * @param {string} storageKey - localStorage key for page-reload resume
     */
    constructor(baseUrl, storageKey) {
        this.baseUrl = baseUrl;
        this.storageKey = SSE_LAST_SEQ_PREFIX + storageKey;
        this._handlers = new Map(); // eventName -> Set<handler>
        this._gapHandler = null;
        this._es = null;
        this._lastSeq = this._readPersistedSeq();
        this._closed = false;
    }

    on(eventName, handler) {
        if (!this._handlers.has(eventName)) {
            this._handlers.set(eventName, new Set());
        }
        this._handlers.get(eventName).add(handler);
        // If already open, attach a native EventSource listener now.
        if (this._es) this._attachListener(eventName);
    }

    onGap(handler) {
        this._gapHandler = handler;
    }

    open() {
        if (this._es) return;
        this._closed = false;
        const sep = this.baseUrl.includes('?') ? '&' : '?';
        const url = this._lastSeq > 0
            ? `${this.baseUrl}${sep}since=${this._lastSeq}`
            : this.baseUrl;
        this._es = new EventSource(url);
        // Re-attach listeners that were registered before open()
        for (const eventName of this._handlers.keys()) {
            this._attachListener(eventName);
        }
        this._attachListener('gap');
        this._es.onerror = () => {
            // Native EventSource auto-reconnects with Last-Event-ID; nothing to do.
        };
    }

    close() {
        this._closed = true;
        if (this._es) {
            try { this._es.close(); } catch (_) { /* ignore */ }
            this._es = null;
        }
    }

    _attachListener(eventName) {
        if (!this._es) return;
        // EventSource's addEventListener silently ignores duplicate (target,name,handler) tuples,
        // but we use a single bound dispatcher per name to keep that property.
        if (this._es[`__bound_${eventName}`]) return;
        this._es[`__bound_${eventName}`] = true;
        this._es.addEventListener(eventName, (msg) => {
            let data = null;
            try {
                data = JSON.parse(msg.data);
            } catch (e) {
                return;
            }
            // Track the highest seq we've seen so reload can resume.
            const seq = parseInt(msg.lastEventId, 10);
            if (!Number.isNaN(seq) && seq > this._lastSeq) {
                this._lastSeq = seq;
                this._persistSeq(seq);
            }
            if (eventName === 'gap') {
                if (this._gapHandler) this._gapHandler(data);
                this._recoverGap(data);
                return;
            }
            const handlers = this._handlers.get(eventName);
            if (!handlers) return;
            for (const h of handlers) {
                try { h(data); } catch (_) { /* swallow */ }
            }
        });
    }

    async _recoverGap(gapPayload) {
        // Re-fetch missing range via the history endpoint.
        const aid = gapPayload && gapPayload.agent_id;
        if (!aid) return;
        const since = (gapPayload.dropped_from || this._lastSeq) - 1;
        try {
            const resp = await fetch(`/api/agents/${encodeURIComponent(aid)}/history?since=${Math.max(0, since)}`);
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data || !Array.isArray(data.events)) return;
            for (const evt of data.events) {
                const name = evt.event === 'agent_stream_line' || !evt.event ? 'stream_line' : evt.event;
                const handlers = this._handlers.get(name);
                if (!handlers) continue;
                for (const h of handlers) {
                    try { h(evt); } catch (_) { /* swallow */ }
                }
            }
        } catch (_) {
            // Non-critical — next live event will move us past the gap.
        }
    }

    _readPersistedSeq() {
        try {
            const v = window.localStorage.getItem(this.storageKey);
            const n = parseInt(v || '0', 10);
            return Number.isFinite(n) && n > 0 ? n : 0;
        } catch (_) { return 0; }
    }

    _persistSeq(seq) {
        try { window.localStorage.setItem(this.storageKey, String(seq)); } catch (_) { /* ignore */ }
    }
}
