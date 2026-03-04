/**
 * Subagent Pool Widget
 * ====================
 *
 * Live utilization widget showing which subagents are active, idle, or queued
 * across all concurrent investigations.
 *
 * Polls /api/pool/status every 3 seconds as fallback.
 * Also listens to 'pool_status' SocketIO room for real-time updates.
 *
 * Exports:
 *   initSubagentPoolWidget()       - call once on page load
 *   handlePoolStatusEvent(data)    - socket.io handler
 *   renderPoolStatus(status)       - public, for testing
 *
 * Cycle 2 additions:
 *   - Visual slot grid: 50 boxes (green=active, yellow=queued, gray=idle)
 *   - Throughput display: slots released per minute
 *   - Historical sparkline: last 60s of pool utilization as SVG polyline
 */

const POLL_INTERVAL_MS = 3000;
let _pollTimer = null;

export function initSubagentPoolWidget() {
    _fetchAndRender();
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(_fetchAndRender, POLL_INTERVAL_MS);
}

export function handlePoolStatusEvent(data) {
    renderPoolStatus(data);
}

async function _fetchAndRender() {
    try {
        const resp = await fetch('/api/pool/status');
        if (!resp.ok) return;
        const data = await resp.json();
        renderPoolStatus(data);
    } catch (e) {
        // Pool may not be available; silently ignore
    }
}

export function renderPoolStatus(status) {
    const body = document.getElementById('subagent-pool-body');
    if (!body) return;

    _updateBadge(status);

    const totalSlots = status.total_slots || 50;
    const activeSlots = status.active_slots || 0;
    const idleSlots = status.idle_slots !== undefined ? status.idle_slots : (totalSlots - activeSlots);
    const investigations = status.investigations || [];
    const globalPct = totalSlots > 0 ? Math.round((activeSlots / totalSlots) * 100) : 0;
    const throughput = status.throughput || 0;
    const history = status.utilization_history || [];

    // Compute total queued from investigations
    const queuedSlots = investigations.reduce((sum, inv) => sum + (inv.queued || 0), 0);

    let html = `
        <div class="pool-global-metrics">
            <div class="pool-metric-row">
                <span class="pool-metric-label">Pool Utilization</span>
                <span class="pool-metric-value">${activeSlots}/${totalSlots}</span>
                ${throughput > 0 ? `<span class="pool-throughput">${throughput} slots/min</span>` : ''}
            </div>
            <div class="pool-bar-track">
                <div class="pool-bar-fill pool-bar-active" style="width:${globalPct}%"></div>
            </div>
            <div class="pool-metric-sub">
                <span class="pool-badge pool-badge-active">${activeSlots} active</span>
                <span class="pool-badge pool-badge-idle">${idleSlots} idle</span>
                ${queuedSlots > 0 ? `<span class="pool-badge pool-badge-queued">${queuedSlots} queued</span>` : ''}
                <span class="pool-badge pool-badge-investigations">${investigations.length} investigation${investigations.length !== 1 ? 's' : ''}</span>
            </div>
        </div>
        ${_renderSlotGrid(activeSlots, queuedSlots, totalSlots)}
        ${history.length > 1 ? _renderSparkline(history, totalSlots) : ''}
    `;

    if (investigations.length === 0) {
        html += `<div class="pool-empty-state">No active investigations</div>`;
    } else {
        html += `<div class="pool-investigations">`;
        for (const inv of investigations) {
            html += _renderInvestigationBar(inv, totalSlots);
        }
        html += `</div>`;
    }

    if (status.timestamp) {
        const ts = new Date(status.timestamp);
        html += `<div class="pool-timestamp">Updated ${ts.toLocaleTimeString()}</div>`;
    }

    body.innerHTML = html;
}

/**
 * Render a 50-box visual slot grid.
 * Boxes are colored: green (active), yellow (queued), gray (idle).
 * Layout: 10 boxes per row × 5 rows.
 */
function _renderSlotGrid(activeSlots, queuedSlots, totalSlots) {
    const total = totalSlots || 50;
    let boxes = '';
    for (let i = 0; i < total; i++) {
        let cls = 'slot-box slot-box-idle';
        if (i < activeSlots) {
            cls = 'slot-box slot-box-active';
        } else if (i < activeSlots + queuedSlots) {
            cls = 'slot-box slot-box-queued';
        }
        boxes += `<div class="${cls}" title="Slot ${i + 1}"></div>`;
    }
    return `
        <div class="pool-slot-grid" title="${activeSlots} active / ${queuedSlots} queued / ${total - activeSlots - queuedSlots} idle">
            ${boxes}
        </div>`;
}

/**
 * Render a sparkline SVG showing pool utilization over the last 60 seconds.
 * history: array of [timestamp_float, active_slots]
 */
function _renderSparkline(history, totalSlots) {
    if (!history || history.length < 2) return '';

    const W = 220, H = 36, PAD = 2;
    const total = totalSlots || 50;

    const timestamps = history.map(h => h[0]);
    const values = history.map(h => h[1]);
    const tMin = timestamps[0];
    const tMax = timestamps[timestamps.length - 1];
    const tRange = Math.max(tMax - tMin, 1);

    const points = history.map(([t, v], i) => {
        const x = PAD + ((t - tMin) / tRange) * (W - PAD * 2);
        const y = H - PAD - (v / total) * (H - PAD * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    const maxVal = Math.max(...values);
    const maxPct = total > 0 ? Math.round((maxVal / total) * 100) : 0;
    const lastVal = values[values.length - 1];
    const lastPct = total > 0 ? Math.round((lastVal / total) * 100) : 0;

    return `
        <div class="pool-sparkline-container">
            <span class="pool-sparkline-label">60s utilization</span>
            <svg class="pool-sparkline" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
                <rect width="${W}" height="${H}" fill="#1a1a2e" rx="3"/>
                <polyline points="${points}" fill="none" stroke="#00c8a0" stroke-width="1.5" stroke-linejoin="round"/>
                <text x="${W - 3}" y="${H - 3}" text-anchor="end" font-size="9" fill="#888">${lastPct}%</text>
            </svg>
            <span class="pool-sparkline-peak">peak ${maxPct}%</span>
        </div>`;
}

function _renderInvestigationBar(inv, totalSlots) {
    const active = inv.active || 0;
    const queued = inv.queued || 0;
    const quota = inv.quota || 0;
    const activePct = totalSlots > 0 ? Math.round((active / totalSlots) * 100) : 0;
    const queuedPct = totalSlots > 0 ? Math.round((queued / totalSlots) * 100) : 0;
    const quotaPct = totalSlots > 0 ? Math.round((quota / totalSlots) * 100) : 0;

    const priorityClass = _priorityClass(inv.priority_label);
    const queryPreview = _escapeHtml(inv.query_preview || 'Unknown');

    let startedStr = '';
    if (inv.started_at) {
        try { startedStr = new Date(inv.started_at).toLocaleTimeString(); } catch (e) {}
    }

    const invIdShort = _escapeHtml((inv.investigation_id || '').slice(-8));

    return `
        <div class="pool-investigation" title="${queryPreview}">
            <div class="pool-inv-header">
                <span class="pool-inv-id">${invIdShort}</span>
                <span class="pool-inv-priority pool-priority-${priorityClass}">${_escapeHtml(inv.priority_label || 'Normal')}</span>
                <span class="pool-inv-slots">${active} active${queued > 0 ? ` / ${queued} queued` : ''}</span>
            </div>
            <div class="pool-inv-query">${queryPreview}</div>
            <div class="pool-bar-track">
                <div class="pool-bar-fill pool-bar-active" style="width:${activePct}%" title="${active} active slots"></div>
                <div class="pool-bar-fill pool-bar-queued" style="width:${queuedPct}%;margin-left:${activePct}%" title="${queued} queued slots"></div>
            </div>
            <div class="pool-inv-footer">
                <span class="pool-inv-quota">Quota: ${quota} slots (${quotaPct}% of pool)</span>
                ${startedStr ? `<span class="pool-inv-time">Started ${startedStr}</span>` : ''}
            </div>
        </div>
    `;
}

function _updateBadge(status) {
    const badge = document.getElementById('pool-status-badge');
    if (!badge) return;
    const active = status.active_slots || 0;
    const total = status.total_slots || 50;
    badge.textContent = `${active}/${total}`;
    badge.className = 'badge ' + (active > 0 ? 'badge-active' : 'badge-idle');
}

function _priorityClass(label) {
    if (!label) return 'normal';
    const l = label.toLowerCase();
    if (l === 'critical') return 'critical';
    if (l === 'high') return 'high';
    if (l === 'low') return 'low';
    return 'normal';
}

function _escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// CSS injected at runtime so the widget is self-contained
(function _injectStyles() {
    if (document.getElementById('pool-widget-styles')) return;
    const style = document.createElement('style');
    style.id = 'pool-widget-styles';
    style.textContent = `
        /* Slot grid */
        .pool-slot-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 2px;
            padding: 6px 0;
        }
        .slot-box {
            width: 10px;
            height: 10px;
            border-radius: 2px;
            flex-shrink: 0;
        }
        .slot-box-active  { background: #00c8a0; }
        .slot-box-queued  { background: #f0c040; }
        .slot-box-idle    { background: #2a2a4a; }

        /* Throughput badge */
        .pool-throughput {
            font-size: 11px;
            color: #00c8a0;
            margin-left: 8px;
            font-style: italic;
        }

        /* Sparkline */
        .pool-sparkline-container {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 0;
        }
        .pool-sparkline-label, .pool-sparkline-peak {
            font-size: 10px;
            color: #888;
            white-space: nowrap;
        }
        .pool-sparkline {
            border-radius: 3px;
        }
    `;
    document.head && document.head.appendChild(style);
})();
