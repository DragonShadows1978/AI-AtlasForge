/**
 * Dashboard Knowledge Base Analytics Module
 * KB analytics widget with charts, themes, chains, comparison
 * Dependencies: core.js, api.js
 */

// =============================================================================
// KB ANALYTICS STATE
// =============================================================================

let kbAnalyticsData = null;
let kbSelectedMissions = [];
let kbAllMissions = [];
let kbChainLoaded = false;
let kbChainObserver = null;
let kbLastFocusedElement = null;

// =============================================================================
// LOADING HELPERS
// =============================================================================

function showKBLoading(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = '<div class="kb-loading" role="status" aria-label="Loading"><div class="kb-spinner"></div></div>';
    }
}

function showKBSkeleton(containerId, type = 'list') {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (type === 'list') {
        container.innerHTML = Array(4).fill(0).map(() =>
            '<div class="kb-skeleton kb-skeleton-bar"></div>'
        ).join('');
    } else if (type === 'stat') {
        container.innerHTML = '<div class="kb-skeleton kb-skeleton-stat"></div>';
    }
}

function showKBError(containerId, message, retryFn = null) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const retryBtn = retryFn ? `<button class="kb-retry-btn" onclick="${retryFn}">Retry</button>` : '';
    container.innerHTML = `
        <div class="kb-error" role="alert" aria-live="polite">
            <span class="kb-error-icon">!</span>
            <span class="kb-error-message">${escapeHtml(message)}</span>
            ${retryBtn}
        </div>
    `;
}

// =============================================================================
// LAZY LOADING FOR CHAIN GRAPH
// =============================================================================

function initKBChainLazyLoad() {
    const chainContainer = document.getElementById('kb-chain-container');
    if (!chainContainer || kbChainObserver) return;

    kbChainObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !kbChainLoaded) {
                loadKBChainData();
            }
        });
    }, { threshold: 0.1, rootMargin: '100px' });

    kbChainObserver.observe(chainContainer);
}

async function loadKBChainData() {
    if (kbChainLoaded) return;

    const svg = document.getElementById('kb-chain-graph');
    svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">Loading chains...</text>';

    try {
        const data = await api('/api/knowledge-base/analytics/chains');
        if (data.error) {
            svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--red)" font-size="8">Failed to load chains</text>';
            return;
        }
        renderKBChainGraph(data.chains || data || []);
        kbChainLoaded = true;
        if (kbChainObserver) kbChainObserver.disconnect();
    } catch (e) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--red)" font-size="8">Failed to load chains</text>';
        console.error('KB Chain load error:', e);
    }
}

// =============================================================================
// MAIN REFRESH
// =============================================================================

async function refreshKBAnalyticsWidget() {
    showKBSkeleton('kb-themes-list', 'list');
    const svg = document.getElementById('kb-accumulation-svg');
    if (svg) svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="10">Loading...</text>';

    try {
        const filter = document.getElementById('kb-time-filter').value;
        let url = '/api/knowledge-base/analytics';

        if (filter !== 'all' && filter !== 'custom') {
            const endDate = new Date();
            const startDate = new Date();
            startDate.setDate(startDate.getDate() - parseInt(filter));
            url += `?start_date=${startDate.toISOString()}&end_date=${endDate.toISOString()}`;
        } else if (filter === 'custom') {
            const startDate = document.getElementById('kb-start-date').value;
            const endDate = document.getElementById('kb-end-date').value;
            if (startDate) url += `?start_date=${startDate}`;
            if (endDate) url += (url.includes('?') ? '&' : '?') + `end_date=${endDate}`;
        }

        const data = await api(url);
        if (data.error) {
            console.error('KB Analytics error:', data.error);
            showKBError('kb-themes-list', 'Unable to load themes', 'refreshKBAnalyticsWidget()');
            return;
        }

        kbAnalyticsData = data;

        // Update summary stats
        const accum = data.accumulation || {};
        const transfer = data.transfer_rate || {};

        document.getElementById('kb-total-learnings').textContent = accum.total_learnings || 0;
        document.getElementById('kb-transfer-rate').textContent = (transfer.transfer_rate || 0) + '%';

        // Render charts
        renderKBLineChart(accum.missions || []);
        renderKBTypePieChart(data.type_distribution || {});
        renderKBThemesList(data.top_themes || {});

        // Initialize lazy loading for chains
        kbChainLoaded = false;
        initKBChainLazyLoad();

        // Update transfer details
        document.getElementById('kb-chain-count').textContent = transfer.chain_count || 0;
        document.getElementById('kb-domain-continuity').textContent = (transfer.domain_continuity || 0) + '%';

        // Load mission list for comparison
        await loadKBMissionList();

    } catch (e) {
        console.error('KB Analytics widget error:', e);
        showKBError('kb-themes-list', 'Connection error. Please try again.', 'refreshKBAnalyticsWidget()');
    }
}

// =============================================================================
// SVG LINE CHART
// =============================================================================

function renderKBLineChart(missions) {
    const svg = document.getElementById('kb-accumulation-svg');
    const tooltip = document.getElementById('kb-line-tooltip');

    if (!missions || missions.length === 0) {
        svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="10">No data</text>';
        return;
    }

    const values = missions.map(m => m.cumulative || m.count || 0);
    const maxVal = Math.max(...values, 1);
    const padding = 15;
    const width = 300 - padding * 2;
    const height = 100 - padding * 2;

    const points = missions.map((m, i) => {
        const x = padding + (missions.length === 1 ? width / 2 : (i / (missions.length - 1)) * width);
        const y = height + padding - (values[i] / maxVal) * height;
        return { x, y, data: m };
    });

    const pathD = points.map((p, i) =>
        (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1)
    ).join(' ');

    const areaD = pathD +
        ` L${(padding + width).toFixed(1)},${(height + padding).toFixed(1)}` +
        ` L${padding},${(height + padding).toFixed(1)} Z`;

    let content = `
        <defs>
            <linearGradient id="kbLineGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" style="stop-color:#58a6ff;stop-opacity:0.3"/>
                <stop offset="100%" style="stop-color:#58a6ff;stop-opacity:0"/>
            </linearGradient>
        </defs>
        <path d="${areaD}" fill="url(#kbLineGradient)"/>
        <path d="${pathD}" fill="none" stroke="#58a6ff" stroke-width="2"/>
    `;

    points.forEach((p, i) => {
        content += `
            <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5"
                    fill="#58a6ff" class="kb-line-point"
                    data-idx="${i}"/>
        `;
    });

    content += `
        <text x="${padding - 2}" y="${padding + 3}" text-anchor="end" fill="var(--text-dim)" font-size="8">${maxVal}</text>
        <text x="${padding - 2}" y="${height + padding}" text-anchor="end" fill="var(--text-dim)" font-size="8">0</text>
    `;

    svg.innerHTML = content;
    svg.kbData = missions;

    // Hover events
    svg.querySelectorAll('.kb-line-point').forEach(circle => {
        circle.addEventListener('mouseenter', (e) => {
            const idx = parseInt(e.target.dataset.idx);
            const m = missions[idx];
            const date = m.timestamp ? new Date(m.timestamp).toLocaleDateString() : 'N/A';
            tooltip.innerHTML = `
                <strong>${m.mission_id}</strong><br>
                <span style="color: var(--text-dim);">${date}</span><br>
                ${m.count} new learnings<br>
                <span style="color: var(--green);">Total: ${m.cumulative}</span>
            `;
            tooltip.style.display = 'block';
            tooltip.style.left = (e.pageX + 15) + 'px';
            tooltip.style.top = (e.pageY - 40) + 'px';
            e.target.setAttribute('r', '7');
        });
        circle.addEventListener('mouseleave', (e) => {
            tooltip.style.display = 'none';
            e.target.setAttribute('r', '5');
        });
        circle.addEventListener('mousemove', (e) => {
            tooltip.style.left = (e.pageX + 15) + 'px';
            tooltip.style.top = (e.pageY - 40) + 'px';
        });
    });
}

// =============================================================================
// PIE CHART
// =============================================================================

function renderKBTypePieChart(typeData) {
    const svg = document.getElementById('kb-type-pie');
    const legend = document.getElementById('kb-type-legend');

    const distribution = typeData.distribution || {};
    const entries = Object.entries(distribution);
    const total = entries.reduce((sum, [, count]) => sum + count, 0);

    if (total === 0) {
        svg.innerHTML = '<circle cx="50" cy="50" r="40" fill="var(--bg)" stroke="var(--border)"/>';
        legend.innerHTML = '<div style="color: var(--text-dim);">No data</div>';
        return;
    }

    const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#a371f7'];
    const circumference = 2 * Math.PI * 35;
    let offset = 0;

    let svgContent = '';
    entries.forEach(([type, count], i) => {
        const percent = count / total;
        const dash = percent * circumference;
        svgContent += `
            <circle cx="50" cy="50" r="35"
                    fill="none"
                    stroke="${colors[i % colors.length]}"
                    stroke-width="10"
                    stroke-dasharray="${dash} ${circumference - dash}"
                    stroke-dashoffset="${-offset}"
                    transform="rotate(-90 50 50)"/>
        `;
        offset += dash;
    });

    svg.innerHTML = svgContent;

    const legendHtml = entries.map(([type, count], i) => `
        <div class="kb-pie-legend-item">
            <span class="kb-pie-legend-dot" style="background: ${colors[i % colors.length]};"></span>
            <span>${type}</span>
            <span style="color: var(--text-dim); margin-left: auto;">${count}</span>
        </div>
    `).join('');

    legend.innerHTML = legendHtml;
}

// =============================================================================
// THEMES LIST
// =============================================================================

function renderKBThemesList(themesData) {
    const list = document.getElementById('kb-themes-list');
    const themes = themesData.themes || [];

    if (themes.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em; text-align: center;" role="status">No themes</div>';
        return;
    }

    const maxCount = Math.max(...themes.map(t => t.count), 1);

    const html = themes.slice(0, 8).map((t, i) => {
        const barWidth = Math.max(5, (t.count / maxCount) * 60);
        const escapedTheme = escapeHtml(t.theme);
        return `
            <div class="kb-theme-item"
                 role="button"
                 tabindex="0"
                 aria-label="Theme: ${escapedTheme}, ${t.count} occurrences. Press Enter for details."
                 onclick="openKBThemeModal('${escapedTheme}', 'domain')"
                 onkeydown="handleKBThemeKeyDown(event, '${escapedTheme}')">
                <span class="kb-theme-name">${escapedTheme}</span>
                <div class="kb-theme-bar" style="width: ${barWidth}px;" aria-hidden="true"></div>
                <span class="kb-theme-count" aria-hidden="true">${t.count}</span>
            </div>
        `;
    }).join('');

    list.innerHTML = html;
}

function handleKBThemeKeyDown(e, theme) {
    const items = Array.from(document.querySelectorAll('.kb-theme-item'));
    const currentIdx = items.indexOf(e.target);

    switch(e.key) {
        case 'ArrowDown':
            e.preventDefault();
            if (currentIdx < items.length - 1) items[currentIdx + 1].focus();
            break;
        case 'ArrowUp':
            e.preventDefault();
            if (currentIdx > 0) items[currentIdx - 1].focus();
            break;
        case 'Enter':
        case ' ':
            e.preventDefault();
            openKBThemeModal(theme, 'domain');
            break;
        case 'Escape':
            e.target.blur();
            break;
    }
}

// =============================================================================
// THEME MODAL
// =============================================================================

function openKBThemeModal(theme, themeType) {
    kbLastFocusedElement = document.activeElement;
    showThemeDetails(theme, themeType);
}

async function showThemeDetails(theme, themeType) {
    const modal = document.getElementById('kb-theme-modal');
    const title = document.getElementById('kb-theme-modal-title');
    const body = document.getElementById('kb-theme-modal-body');

    title.textContent = `Theme: ${theme}`;
    body.innerHTML = '<div class="kb-loading" role="status" aria-label="Loading theme details"><div class="kb-spinner"></div></div>';
    modal.style.display = 'flex';

    setupKBModalFocusTrap();

    setTimeout(() => {
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) closeBtn.focus();
    }, 100);

    try {
        const data = await api(`/api/knowledge-base/analytics/learnings-by-theme?theme=${encodeURIComponent(theme)}&type=${themeType}`);

        if (data.error) {
            body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(data.error)}</span></div>`;
            return;
        }

        let html = `<div class="kb-theme-stats" role="region" aria-label="Theme statistics">
            <span><strong>${data.total_learnings}</strong> learnings</span>
            <span><strong>${data.mission_count}</strong> missions</span>
        </div>`;

        if (Object.keys(data.by_mission || {}).length === 0) {
            html += '<div style="color: var(--text-dim);" role="status">No learnings found for this theme.</div>';
        } else {
            for (const [mission, learnings] of Object.entries(data.by_mission)) {
                html += `<div class="kb-theme-mission" role="region" aria-label="Mission ${escapeHtml(mission)}">
                    <div class="kb-theme-mission-header">${escapeHtml(mission)} (${learnings.length})</div>
                    <ul class="kb-theme-learnings" role="list">`;

                for (const l of learnings) {
                    const typeClass = (l.type || 'unknown').toLowerCase();
                    html += `<li role="listitem">
                        <span class="kb-learning-type ${typeClass}" aria-label="Type: ${l.type || 'unknown'}">${l.type || 'unknown'}</span>
                        <span class="kb-learning-title">${escapeHtml(l.title || 'Untitled')}</span>
                    </li>`;
                }

                html += `</ul></div>`;
            }
        }

        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(e.message)}</span></div>`;
    }
}

function closeKBThemeModal() {
    const modal = document.getElementById('kb-theme-modal');
    modal.style.display = 'none';

    if (kbLastFocusedElement) {
        kbLastFocusedElement.focus();
        kbLastFocusedElement = null;
    }
}

function setupKBModalFocusTrap() {
    const modal = document.getElementById('kb-theme-modal');

    modal.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeKBThemeModal();
            return;
        }

        if (e.key !== 'Tab') return;

        const focusable = modal.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );

        if (focusable.length === 0) return;

        const firstEl = focusable[0];
        const lastEl = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === firstEl) {
            e.preventDefault();
            lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
            e.preventDefault();
            firstEl.focus();
        }
    });
}

// =============================================================================
// CHAIN GRAPH
// =============================================================================

function renderKBChainGraph(chains) {
    const svg = document.getElementById('kb-chain-graph');
    const legend = document.getElementById('kb-chain-legend');

    if (!chains || chains.length === 0) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">No chains</text>';
        legend.innerHTML = '';
        return;
    }

    const missions = new Set();
    const edges = [];
    const chainColors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff'];

    chains.slice(0, 5).forEach((chain, chainIdx) => {
        const chainMissions = chain.missions || [];
        chainMissions.forEach(m => missions.add(m));
        for (let i = 0; i < chainMissions.length - 1; i++) {
            edges.push({
                source: chainMissions[i],
                target: chainMissions[i + 1],
                theme: chain.theme || 'unknown',
                color: chainColors[chainIdx % chainColors.length]
            });
        }
    });

    const missionArray = Array.from(missions);
    const nodeCount = missionArray.length;

    if (nodeCount === 0) {
        svg.innerHTML = '<text x="100" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="8">No chains</text>';
        return;
    }

    const centerX = 100;
    const centerY = 50;
    const radius = Math.min(40, 30 + nodeCount * 2);

    const nodes = missionArray.map((id, i) => ({
        id,
        x: centerX + Math.cos((i * 2 * Math.PI / nodeCount) - Math.PI/2) * radius,
        y: centerY + Math.sin((i * 2 * Math.PI / nodeCount) - Math.PI/2) * radius
    }));

    let svgContent = '';

    edges.forEach(e => {
        const s = nodes.find(n => n.id === e.source);
        const t = nodes.find(n => n.id === e.target);
        if (s && t) {
            svgContent += `<line x1="${s.x.toFixed(1)}" y1="${s.y.toFixed(1)}"
                                x2="${t.x.toFixed(1)}" y2="${t.y.toFixed(1)}"
                                stroke="${e.color}" stroke-width="1.5" opacity="0.7"/>`;
        }
    });

    nodes.forEach(n => {
        svgContent += `
            <circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="6" fill="#3fb950"
                    class="kb-chain-node" data-mission="${n.id}"/>
            <text x="${n.x.toFixed(1)}" y="${(n.y + 14).toFixed(1)}" text-anchor="middle"
                  font-size="5" fill="var(--text-dim)">${n.id.slice(-8)}</text>
        `;
    });

    svg.innerHTML = svgContent;

    const legendHtml = chains.slice(0, 5).map((chain, i) => `
        <div class="kb-chain-legend-item">
            <span class="kb-chain-legend-dot" style="background: ${chainColors[i % chainColors.length]};"></span>
            <span>${escapeHtml((chain.theme || 'Chain ' + (i+1)).substring(0, 20))}</span>
        </div>
    `).join('');

    legend.innerHTML = legendHtml;
}

// =============================================================================
// TIME FILTER
// =============================================================================

function applyKBTimeFilter() {
    const filter = document.getElementById('kb-time-filter').value;
    const startInput = document.getElementById('kb-start-date');
    const endInput = document.getElementById('kb-end-date');

    if (filter === 'custom') {
        startInput.style.display = 'inline-block';
        endInput.style.display = 'inline-block';
    } else {
        startInput.style.display = 'none';
        endInput.style.display = 'none';
    }

    refreshKBAnalyticsWidget();
}

// =============================================================================
// MISSION COMPARISON
// =============================================================================

async function loadKBMissionList() {
    try {
        const data = await api('/api/knowledge-base/analytics/missions');
        kbAllMissions = data.missions || [];

        const container = document.getElementById('kb-mission-checkboxes');
        if (kbAllMissions.length === 0) {
            container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em;">No missions with learnings</div>';
            return;
        }

        const html = kbAllMissions.slice(0, 15).map(m => `
            <div class="kb-mission-checkbox">
                <input type="checkbox" id="kb-cmp-${m.mission_id}"
                       onchange="toggleMissionComparison('${m.mission_id}')"
                       ${kbSelectedMissions.includes(m.mission_id) ? 'checked' : ''}>
                <label for="kb-cmp-${m.mission_id}">${m.mission_id}</label>
                <span class="count">${m.learning_count}</span>
            </div>
        `).join('');

        container.innerHTML = html;
    } catch (e) {
        console.error('Load mission list error:', e);
    }
}

async function toggleMissionComparison(missionId) {
    const idx = kbSelectedMissions.indexOf(missionId);
    if (idx >= 0) {
        kbSelectedMissions.splice(idx, 1);
    } else if (kbSelectedMissions.length < 3) {
        kbSelectedMissions.push(missionId);
    } else {
        document.getElementById(`kb-cmp-${missionId}`).checked = false;
        return;
    }

    await updateMissionComparison();
}

async function updateMissionComparison() {
    const section = document.getElementById('kb-comparison-section');
    const container = document.getElementById('kb-comparison-container');

    if (kbSelectedMissions.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    container.innerHTML = '<div style="color: var(--text-dim);">Loading...</div>';

    try {
        const profiles = await Promise.all(
            kbSelectedMissions.map(id =>
                api(`/api/knowledge-base/analytics/mission-profile?mission_id=${encodeURIComponent(id)}`)
            )
        );

        const html = profiles.map(p => {
            if (p.error) {
                return `<div class="kb-mission-card">
                    <div class="kb-mission-card-header">
                        ${escapeHtml(p.mission_id || 'Error')}
                        <span class="remove" onclick="removeMissionComparison('${p.mission_id}')">&times;</span>
                    </div>
                    <div style="color: var(--red);">Error: ${p.error}</div>
                </div>`;
            }

            const types = Object.entries(p.type_distribution || {})
                .map(([t, c]) => `${t}: ${c}`)
                .join(', ') || 'None';

            const themes = (p.top_themes || [])
                .slice(0, 3)
                .map(t => t.theme)
                .join(', ') || 'None';

            return `<div class="kb-mission-card">
                <div class="kb-mission-card-header">
                    ${escapeHtml(p.mission_id)}
                    <span class="remove" onclick="removeMissionComparison('${p.mission_id}')">&times;</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Learnings</span>
                    <span class="value">${p.total_learnings}</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Types</span>
                    <span class="value" style="font-size: 0.8em;">${types}</span>
                </div>
                <div class="kb-mission-card-stat">
                    <span class="label">Themes</span>
                    <span class="value" style="font-size: 0.8em;">${themes}</span>
                </div>
            </div>`;
        }).join('');

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div style="color: var(--red);">Error: ${e.message}</div>`;
    }
}

function removeMissionComparison(missionId) {
    const idx = kbSelectedMissions.indexOf(missionId);
    if (idx >= 0) {
        kbSelectedMissions.splice(idx, 1);
        const checkbox = document.getElementById(`kb-cmp-${missionId}`);
        if (checkbox) checkbox.checked = false;
        updateMissionComparison();
    }
}

function clearMissionComparison() {
    kbSelectedMissions = [];
    document.querySelectorAll('#kb-mission-checkboxes input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    document.getElementById('kb-comparison-section').style.display = 'none';
}

// Debug: mark kb-analytics module loaded
console.log('KB Analytics module loaded');
