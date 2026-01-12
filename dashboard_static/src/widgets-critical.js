/**
 * Dashboard Critical Widget Functions (ES6)
 *
 * Contains only the widget refresh functions that are called during page initialization.
 * These are inlined in the main bundle (not lazy-loaded) to ensure they're available
 * immediately when the RDE tab loads.
 *
 * Critical functions:
 * - refreshGitStatusWidget() - Git branch/changes display (called from init, 15s interval)
 * - refreshKBAnalyticsWidget() - KB themes/analytics display (called from init, 60s interval)
 *
 * Full tab-specific functionality is still lazy-loaded from:
 * - modules/git-analytics.js (for timeline, comparison, churn analysis)
 * - modules/kb-analytics.js (for chain graph, mission comparison)
 */

import { escapeHtml } from './core.js';
import { api } from './api.js';

// =============================================================================
// CRITICAL GIT STATUS WIDGET
// =============================================================================

/**
 * Refresh the git status widget on the RDE tab sidebar
 * Shows current branch, ahead/behind status, and uncommitted changes count
 */
export async function refreshGitStatusWidget() {
    try {
        const data = await api('/api/git/status');
        if (data.error) {
            console.log('Git status error:', data.error);
            return;
        }

        const status = data.status || {};

        // Update branch name
        const branchEl = document.getElementById('git-branch-name');
        if (branchEl) branchEl.textContent = status.current_branch || '-';

        // Update remote branch
        const remoteEl = document.getElementById('git-branch-remote');
        if (remoteEl) remoteEl.textContent = status.remote_branch ? `→ ${status.remote_branch}` : '';

        // Update ahead/behind stats
        const aheadEl = document.getElementById('git-ahead');
        const behindEl = document.getElementById('git-behind');
        if (aheadEl) {
            aheadEl.textContent = status.commits_ahead || 0;
            aheadEl.className = 'git-stat-value ahead' + (status.commits_ahead > 0 ? ' has-ahead' : '');
        }
        if (behindEl) {
            behindEl.textContent = status.commits_behind || 0;
            behindEl.className = 'git-stat-value behind' + (status.commits_behind > 0 ? ' has-behind' : '');
        }

        // Update uncommitted/untracked counts
        const uncommittedEl = document.getElementById('git-uncommitted');
        const untrackedEl = document.getElementById('git-untracked');
        if (uncommittedEl) uncommittedEl.textContent = status.uncommitted_changes || 0;
        if (untrackedEl) untrackedEl.textContent = status.untracked_files || 0;

        // Update health badge
        const healthBadge = document.getElementById('git-health-badge');
        const healthText = document.getElementById('git-health-text');
        if (healthBadge && healthText) {
            const health = status.sync_health || 'unknown';
            healthBadge.className = 'git-health-badge ' + health;
            const healthLabels = {
                'healthy': 'In Sync',
                'degraded': 'Behind Remote',
                'critical': 'Conflicts Detected',
                'unknown': 'Unknown'
            };
            healthText.textContent = healthLabels[health] || health;
        }

        // Update push badge
        const pushBadge = document.getElementById('git-push-badge');
        const pushCount = document.getElementById('git-push-badge-count');
        if (pushBadge && pushCount) {
            const ahead = status.commits_ahead || 0;
            if (ahead > 0) {
                pushBadge.style.display = 'inline-flex';
                pushCount.textContent = ahead;
            } else {
                pushBadge.style.display = 'none';
            }
        }
    } catch (e) {
        console.log('Git status refresh error:', e);
    }
}

// =============================================================================
// CRITICAL KB ANALYTICS WIDGET
// =============================================================================

// State for KB widget
let kbAnalyticsData = null;

/**
 * Show loading skeleton in KB themes list
 */
function showKBSkeleton(containerId) {
    const container = document.getElementById(containerId);
    if (container) {
        container.innerHTML = Array(4).fill(0).map(() =>
            '<div class="kb-skeleton kb-skeleton-bar"></div>'
        ).join('');
    }
}

/**
 * Show error in KB widget container
 */
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

/**
 * Render the KB themes list
 */
function renderKBThemesList(themesData) {
    const list = document.getElementById('kb-themes-list');
    if (!list) return;

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

/**
 * Render KB line chart (accumulation over time)
 */
function renderKBLineChart(missions) {
    const svg = document.getElementById('kb-accumulation-svg');
    const tooltip = document.getElementById('kb-line-tooltip');

    if (!svg) return;

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
    if (tooltip) {
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
}

/**
 * Render KB type pie chart
 */
function renderKBTypePieChart(typeData) {
    const svg = document.getElementById('kb-type-pie');
    const legend = document.getElementById('kb-type-legend');

    if (!svg || !legend) return;

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

/**
 * Refresh the KB analytics widget on the RDE tab sidebar
 * Shows total learnings, transfer rate, top themes, and accumulation chart
 */
export async function refreshKBAnalyticsWidget() {
    showKBSkeleton('kb-themes-list');
    const svg = document.getElementById('kb-accumulation-svg');
    if (svg) svg.innerHTML = '<text x="150" y="50" text-anchor="middle" fill="var(--text-dim)" font-size="10">Loading...</text>';

    try {
        const filterEl = document.getElementById('kb-time-filter');
        const filter = filterEl ? filterEl.value : 'all';
        const sourceFilterEl = document.getElementById('kb-source-filter');
        const sourceFilter = sourceFilterEl ? sourceFilterEl.value : '';

        let url = '/api/knowledge-base/analytics';
        const params = [];

        if (filter !== 'all' && filter !== 'custom') {
            const endDate = new Date();
            const startDate = new Date();
            startDate.setDate(startDate.getDate() - parseInt(filter));
            params.push(`start_date=${startDate.toISOString()}`);
            params.push(`end_date=${endDate.toISOString()}`);
        } else if (filter === 'custom') {
            const startDateEl = document.getElementById('kb-start-date');
            const endDateEl = document.getElementById('kb-end-date');
            const startDate = startDateEl ? startDateEl.value : '';
            const endDate = endDateEl ? endDateEl.value : '';
            if (startDate) params.push(`start_date=${startDate}`);
            if (endDate) params.push(`end_date=${endDate}`);
        }

        // Add source_type filter if set (Investigation-KB Integration)
        if (sourceFilter) {
            params.push(`source_type=${encodeURIComponent(sourceFilter)}`);
        }

        if (params.length > 0) {
            url += '?' + params.join('&');
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

        const totalEl = document.getElementById('kb-total-learnings');
        const transferEl = document.getElementById('kb-transfer-rate');
        if (totalEl) totalEl.textContent = accum.total_learnings || 0;
        if (transferEl) transferEl.textContent = (transfer.transfer_rate || 0) + '%';

        // Render charts
        renderKBLineChart(accum.missions || []);
        renderKBTypePieChart(data.type_distribution || {});
        renderKBThemesList(data.top_themes || {});

        // Update transfer details
        const chainCountEl = document.getElementById('kb-chain-count');
        const continuityEl = document.getElementById('kb-domain-continuity');
        if (chainCountEl) chainCountEl.textContent = transfer.chain_count || 0;
        if (continuityEl) continuityEl.textContent = (transfer.domain_continuity || 0) + '%';

    } catch (e) {
        console.error('KB Analytics widget error:', e);
        showKBError('kb-themes-list', 'Connection error. Please try again.', 'refreshKBAnalyticsWidget()');
    }
}

// Export accessor for state (if needed by lazy-loaded modules)
export function getKBAnalyticsData() {
    return kbAnalyticsData;
}

// =============================================================================
// CRITICAL REPO STATUS WIDGET
// =============================================================================

/**
 * Refresh the multi-repo status widget on the RDE tab sidebar
 * Shows status of tracked repositories
 */
export async function refreshRepoStatusWidget() {
    try {
        const data = await api('/api/repo-status');
        if (data.error) {
            console.log('Repo status error:', data.error);
            return;
        }

        const tbody = document.getElementById('repo-status-tbody');
        if (!tbody) return;

        const reposObj = data.repos || {};
        if (Object.keys(reposObj).length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-dim);">No repos tracked</td></tr>';
            return;
        }

        tbody.innerHTML = Object.entries(reposObj).map(([id, repo]) => {
            let statusBadge = '';
            if (!repo.has_git) {
                statusBadge = '<span class="repo-status-badge no-git">No Git</span>';
            } else if (!repo.remote) {
                statusBadge = '<span class="repo-status-badge no-remote">No Remote</span>';
            } else if (repo.has_changes) {
                statusBadge = '<span class="repo-status-badge changes">Changes</span>';
            } else {
                statusBadge = '<span class="repo-status-badge ok">OK</span>';
            }

            const changes = repo.changes_count !== undefined ? repo.changes_count : '-';

            let aheadBadge = '-';
            if (repo.has_git) {
                const ahead = repo.ahead !== undefined ? repo.ahead : 0;
                const behind = repo.behind !== undefined ? repo.behind : 0;

                if (behind > 0) {
                    aheadBadge = '<span class="repo-commit-badge behind" title="Behind remote">-' + behind + '</span>';
                } else if (ahead > 0) {
                    aheadBadge = '<span class="repo-commit-badge ahead" title="Ahead of remote">+' + ahead + '</span>';
                } else {
                    aheadBadge = '<span class="repo-commit-badge synced" title="Synced">&#10003;</span>';
                }
            }

            const logBtn = repo.has_git
                ? '<button class="repo-log-btn" onclick="showRepoLog(\'' + escapeHtml(id) + '\')" title="View recent commits">Log</button>'
                : '';

            return '<tr>' +
                '<td class="repo-status-name" title="' + escapeHtml(repo.path || '') + '">' + escapeHtml(repo.name) + '</td>' +
                '<td>' + statusBadge + '</td>' +
                '<td>' + changes + '</td>' +
                '<td>' + aheadBadge + '</td>' +
                '<td>' + logBtn + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        console.log('Repo status refresh error:', e);
    }
}

// =============================================================================
// CRITICAL GIT ANALYTICS WIDGET
// =============================================================================

// State for git analytics widget
let gitAnalyticsData = null;

/**
 * Refresh the git analytics summary widget on the RDE tab sidebar
 * Shows commit counts, churn stats, and mission selector
 */
export async function refreshGitAnalyticsWidget() {
    try {
        const data = await api('/api/git-analytics/dashboard');
        if (data.error) {
            console.log('Git analytics error:', data.error);
            return;
        }

        gitAnalyticsData = data;

        // Extract from churn_summary
        const summary = data.churn_summary || {};

        // Update summary stats
        const commitsEl = document.getElementById('git-analytics-commits');
        const additionsEl = document.getElementById('git-analytics-additions');
        const deletionsEl = document.getElementById('git-analytics-deletions');

        if (commitsEl) commitsEl.textContent = summary.total_commits || 0;
        if (additionsEl) additionsEl.textContent = '+' + (summary.total_additions || 0);
        if (deletionsEl) deletionsEl.textContent = '-' + (summary.total_deletions || 0);

        // Populate mission selector
        const missions = data.missions || [];
        const select = document.getElementById('git-analytics-mission-select');

        if (select) {
            const currentValue = select.value;
            select.innerHTML = '<option value="">Select Mission</option>' +
                missions.map(m => {
                    const shortId = m.mission_id.replace('mission_', '').slice(0, 8);
                    return `<option value="${m.mission_id}">${shortId} (${m.started_at?.split('T')[0] || 'N/A'})</option>`;
                }).join('');
            if (currentValue) select.value = currentValue;
        }

        // Load high-churn alerts
        loadHighChurnAlerts();
    } catch (e) {
        console.error('Git analytics error:', e);
    }
}

/**
 * Load high-churn file alerts
 */
async function loadHighChurnAlerts() {
    try {
        const data = await api('/api/git-analytics/high-churn-alerts?threshold_commits=5&threshold_missions=2');

        const badge = document.getElementById('git-analytics-alert-badge');
        const count = document.getElementById('git-analytics-alert-count');

        if (data.alerts && data.alerts.length > 0) {
            if (badge) {
                badge.style.display = 'inline-block';
                badge.setAttribute('data-alerts', JSON.stringify(data.alerts));
            }
            if (count) count.textContent = data.alerts.length;
        } else {
            if (badge) badge.style.display = 'none';
        }
    } catch (e) {
        console.error('Error loading high-churn alerts:', e);
    }
}

// Export accessor for git analytics state
export function getGitAnalyticsData() {
    return gitAnalyticsData;
}

// =============================================================================
// KB THEME MODAL HANDLERS (for theme list interaction)
// =============================================================================

let kbLastFocusedElement = null;

/**
 * Handle keyboard navigation on theme list items
 */
export function handleKBThemeKeyDown(e, theme) {
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

/**
 * Open modal to show theme details
 */
export function openKBThemeModal(theme, themeType) {
    kbLastFocusedElement = document.activeElement;
    showThemeDetails(theme, themeType);
}

async function showThemeDetails(theme, themeType) {
    const modal = document.getElementById('kb-theme-modal');
    const title = document.getElementById('kb-theme-modal-title');
    const body = document.getElementById('kb-theme-modal-body');

    if (title) title.textContent = `Theme: ${theme}`;
    if (body) body.innerHTML = '<div class="kb-loading" role="status" aria-label="Loading theme details"><div class="kb-spinner"></div></div>';
    if (modal) modal.style.display = 'flex';

    setupKBModalFocusTrap();

    setTimeout(() => {
        if (modal) {
            const closeBtn = modal.querySelector('.modal-close');
            if (closeBtn) closeBtn.focus();
        }
    }, 100);

    try {
        const data = await api(`/api/knowledge-base/analytics/learnings-by-theme?theme=${encodeURIComponent(theme)}&type=${themeType}`);

        if (data.error) {
            if (body) body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(data.error)}</span></div>`;
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

        if (body) body.innerHTML = html;
    } catch (e) {
        if (body) body.innerHTML = `<div class="kb-error" role="alert"><span class="kb-error-icon">!</span><span class="kb-error-message">${escapeHtml(e.message)}</span></div>`;
    }
}

/**
 * Close KB theme modal
 */
export function closeKBThemeModal() {
    const modal = document.getElementById('kb-theme-modal');
    if (modal) modal.style.display = 'none';

    if (kbLastFocusedElement) {
        kbLastFocusedElement.focus();
        kbLastFocusedElement = null;
    }
}

function setupKBModalFocusTrap() {
    const modal = document.getElementById('kb-theme-modal');
    if (!modal) return;

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
// CRITICAL RECOMMENDATIONS WIDGET (Investigation-KB Integration)
// =============================================================================

// Recommendations pagination state
const recState = {
    page: 1,
    perPage: 20,
    total: 0,
    pages: 0,
    filters: {
        complexity: '',
        priority: '',
        investigation_id: '',
        start_date: '',
        end_date: '',
        search: ''
    }
};

// Debounce timer for search
let recSearchTimer = null;

/**
 * Load saved filter state from localStorage
 */
function loadRecFilterState() {
    try {
        const saved = localStorage.getItem('rec_filter_state');
        if (saved) {
            const parsed = JSON.parse(saved);
            Object.assign(recState, parsed);
            // Restore UI state
            const complexityEl = document.getElementById('rec-complexity-filter');
            const priorityEl = document.getElementById('rec-priority-filter');
            const investigationEl = document.getElementById('rec-investigation-filter');
            const startDateEl = document.getElementById('rec-start-date');
            const endDateEl = document.getElementById('rec-end-date');
            const searchEl = document.getElementById('rec-search');
            const perPageEl = document.getElementById('rec-per-page');

            if (complexityEl) complexityEl.value = recState.filters.complexity || '';
            if (priorityEl) priorityEl.value = recState.filters.priority || '';
            if (investigationEl) investigationEl.value = recState.filters.investigation_id || '';
            if (startDateEl) startDateEl.value = recState.filters.start_date || '';
            if (endDateEl) endDateEl.value = recState.filters.end_date || '';
            if (searchEl) searchEl.value = recState.filters.search || '';
            if (perPageEl) perPageEl.value = recState.perPage.toString();
        }
    } catch (e) {
        console.log('Could not load rec filter state:', e);
    }
}

/**
 * Save filter state to localStorage
 */
function saveRecFilterState() {
    try {
        const stateToSave = {
            page: recState.page,
            perPage: recState.perPage,
            filters: recState.filters
        };
        localStorage.setItem('rec_filter_state', JSON.stringify(stateToSave));
    } catch (e) {
        console.log('Could not save rec filter state:', e);
    }
}

/**
 * Populate the investigation filter dropdown
 */
async function populateInvestigationFilter() {
    try {
        const data = await api('/api/knowledge-base/recommendations/investigations');
        const investigations = data.investigations || [];
        const select = document.getElementById('rec-investigation-filter');
        if (!select) return;

        // Keep the "All Investigations" option
        select.innerHTML = '<option value="">All Investigations</option>';

        investigations.forEach(inv => {
            const query = inv.query || inv.investigation_id;
            const shortQuery = query.length > 40 ? query.substring(0, 40) + '...' : query;
            const option = document.createElement('option');
            option.value = inv.investigation_id;
            option.textContent = shortQuery;
            option.title = query;  // Full text on hover
            select.appendChild(option);
        });

        // Restore saved value if any
        if (recState.filters.investigation_id) {
            select.value = recState.filters.investigation_id;
        }
    } catch (e) {
        console.log('Could not populate investigation filter:', e);
    }
}

/**
 * Build API URL with current filters
 */
function buildRecApiUrl() {
    const params = new URLSearchParams();
    params.set('page', recState.page.toString());
    params.set('per_page', recState.perPage.toString());

    if (recState.filters.complexity) params.set('complexity', recState.filters.complexity);
    if (recState.filters.priority) params.set('priority', recState.filters.priority);
    if (recState.filters.investigation_id) params.set('investigation_id', recState.filters.investigation_id);
    if (recState.filters.start_date) params.set('start_date', recState.filters.start_date);
    if (recState.filters.end_date) params.set('end_date', recState.filters.end_date);
    if (recState.filters.search) params.set('search', recState.filters.search);

    return `/api/knowledge-base/recommendations?${params.toString()}`;
}

/**
 * Render pagination controls
 */
function renderRecPagination() {
    const prevBtn = document.getElementById('rec-prev-btn');
    const nextBtn = document.getElementById('rec-next-btn');
    const pageNumbers = document.getElementById('rec-page-numbers');
    const pageInfo = document.getElementById('rec-page-info');

    // Update page info text
    if (pageInfo) {
        const start = recState.total === 0 ? 0 : (recState.page - 1) * recState.perPage + 1;
        const end = Math.min(recState.page * recState.perPage, recState.total);
        pageInfo.textContent = `Showing ${start}-${end} of ${recState.total}`;
    }

    // Update prev/next buttons
    if (prevBtn) {
        prevBtn.disabled = !recState.pages || recState.page <= 1;
    }
    if (nextBtn) {
        nextBtn.disabled = !recState.pages || recState.page >= recState.pages;
    }

    // Render page numbers
    if (pageNumbers) {
        if (recState.pages <= 1) {
            pageNumbers.innerHTML = '';
            return;
        }

        let html = '';
        const maxVisible = 5;
        let startPage = Math.max(1, recState.page - Math.floor(maxVisible / 2));
        let endPage = Math.min(recState.pages, startPage + maxVisible - 1);

        // Adjust start if we're near the end
        if (endPage - startPage < maxVisible - 1) {
            startPage = Math.max(1, endPage - maxVisible + 1);
        }

        // First page + ellipsis
        if (startPage > 1) {
            html += `<button class="rec-page-btn" onclick="goToRecPage(1)">1</button>`;
            if (startPage > 2) {
                html += `<span class="rec-page-ellipsis">...</span>`;
            }
        }

        // Page numbers
        for (let i = startPage; i <= endPage; i++) {
            const active = i === recState.page ? ' active' : '';
            html += `<button class="rec-page-btn${active}" onclick="goToRecPage(${i})"${active ? ' aria-current="page"' : ''}>${i}</button>`;
        }

        // Ellipsis + last page
        if (endPage < recState.pages) {
            if (endPage < recState.pages - 1) {
                html += `<span class="rec-page-ellipsis">...</span>`;
            }
            html += `<button class="rec-page-btn" onclick="goToRecPage(${recState.pages})">${recState.pages}</button>`;
        }

        pageNumbers.innerHTML = html;
    }
}

/**
 * Navigate to a specific page
 */
export function goToRecPage(pageOrDirection) {
    if (pageOrDirection === 'prev') {
        recState.page = Math.max(1, recState.page - 1);
    } else if (pageOrDirection === 'next') {
        recState.page = Math.min(recState.pages, recState.page + 1);
    } else if (typeof pageOrDirection === 'number') {
        recState.page = Math.max(1, Math.min(recState.pages, pageOrDirection));
    }
    saveRecFilterState();
    refreshRecommendations();
}

/**
 * Apply filter changes (resets to page 1)
 */
export function applyRecFilters() {
    const complexityEl = document.getElementById('rec-complexity-filter');
    const priorityEl = document.getElementById('rec-priority-filter');
    const investigationEl = document.getElementById('rec-investigation-filter');
    const startDateEl = document.getElementById('rec-start-date');
    const endDateEl = document.getElementById('rec-end-date');

    recState.filters.complexity = complexityEl ? complexityEl.value : '';
    recState.filters.priority = priorityEl ? priorityEl.value : '';
    recState.filters.investigation_id = investigationEl ? investigationEl.value : '';
    recState.filters.start_date = startDateEl ? startDateEl.value : '';
    recState.filters.end_date = endDateEl ? endDateEl.value : '';

    // Reset to page 1 when filters change
    recState.page = 1;
    saveRecFilterState();
    refreshRecommendations();
}

/**
 * Debounced search handler
 */
export function debounceRecSearch() {
    if (recSearchTimer) clearTimeout(recSearchTimer);
    recSearchTimer = setTimeout(() => {
        const searchEl = document.getElementById('rec-search');
        recState.filters.search = searchEl ? searchEl.value : '';
        recState.page = 1;
        saveRecFilterState();
        refreshRecommendations();
    }, 300);
}

/**
 * Change items per page
 */
export function changeRecPerPage() {
    const perPageEl = document.getElementById('rec-per-page');
    recState.perPage = perPageEl ? parseInt(perPageEl.value) : 20;
    recState.page = 1;  // Reset to first page
    saveRecFilterState();
    refreshRecommendations();
}

/**
 * Clear all filters
 */
export function clearRecFilters() {
    recState.filters = {
        complexity: '',
        priority: '',
        investigation_id: '',
        start_date: '',
        end_date: '',
        search: ''
    };
    recState.page = 1;

    // Reset UI
    const complexityEl = document.getElementById('rec-complexity-filter');
    const priorityEl = document.getElementById('rec-priority-filter');
    const investigationEl = document.getElementById('rec-investigation-filter');
    const startDateEl = document.getElementById('rec-start-date');
    const endDateEl = document.getElementById('rec-end-date');
    const searchEl = document.getElementById('rec-search');

    if (complexityEl) complexityEl.value = '';
    if (priorityEl) priorityEl.value = '';
    if (investigationEl) investigationEl.value = '';
    if (startDateEl) startDateEl.value = '';
    if (endDateEl) endDateEl.value = '';
    if (searchEl) searchEl.value = '';

    saveRecFilterState();
    refreshRecommendations();
}

/**
 * Initialize recommendations widget
 */
export async function initRecommendationsWidget() {
    loadRecFilterState();
    await populateInvestigationFilter();
    await refreshRecommendations();
}

/**
 * Refresh the recommendations widget on the RDE tab sidebar
 * Shows pending recommendations from investigations with pagination
 */
export async function refreshRecommendations() {
    const list = document.getElementById('recommendations-list');
    const badge = document.getElementById('recommendations-badge');
    const footer = document.getElementById('recommendations-footer');

    if (list) list.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 20px;">Loading...</div>';

    try {
        const url = buildRecApiUrl();
        const data = await api(url);

        // Update state from response
        recState.total = data.total || 0;
        recState.pages = data.pages || 1;
        recState.page = data.page || 1;

        const recommendations = data.recommendations || [];

        // Update badge with total count
        if (badge) {
            if (recState.total > 0) {
                badge.textContent = recState.total;
                badge.style.display = 'inline';
            } else {
                badge.style.display = 'none';
            }
        }

        // Render pagination
        renderRecPagination();

        // Footer is no longer used - controls are now in the top bar (rec-controls-bar)
        // Keep for backwards compatibility but hide
        if (footer) {
            footer.style.display = 'none';
        }

        if (!list) return;

        if (recommendations.length === 0) {
            const hasFilters = recState.filters.complexity || recState.filters.priority ||
                              recState.filters.investigation_id || recState.filters.start_date ||
                              recState.filters.end_date || recState.filters.search;

            list.innerHTML = `
                <div style="color: var(--text-dim); text-align: center; padding: 20px;">
                    ${hasFilters
                        ? 'No recommendations match your filters.<br><span style="font-size: 0.85em;">Try adjusting or clearing filters.</span>'
                        : 'No pending recommendations.<br><span style="font-size: 0.85em;">Run investigations to generate mission ideas.</span>'
                    }
                </div>
            `;
            return;
        }

        const html = recommendations.map(rec => {
            const statusClass = rec.status === 'pending' ? '' : rec.status === 'accepted' ? 'accepted' : 'rejected';
            const complexityClass = rec.estimated_complexity || 'medium';
            const priorityBadge = rec.priority ? `<span class="rec-priority-badge priority-${rec.priority}">P${rec.priority}</span>` : '';
            const complexityBadge = `<span class="rec-complexity-badge ${complexityClass}">${complexityClass}</span>`;

            return `
                <div class="rec-item ${statusClass}" onclick="showRecommendationDetail('${rec.recommendation_id}')">
                    <div class="rec-item-content">
                        <div class="rec-item-title">${escapeHtml(rec.title || 'Untitled')}</div>
                        <div class="rec-item-preview">${escapeHtml((rec.problem_statement || '').substring(0, 100))}</div>
                    </div>
                    <div class="rec-item-meta">
                        ${priorityBadge}
                        ${complexityBadge}
                        <span class="rec-source">${escapeHtml(rec.source_investigation_id || 'unknown').substring(0, 12)}</span>
                    </div>
                </div>
            `;
        }).join('');

        list.innerHTML = html;
    } catch (e) {
        if (list) list.innerHTML = `<div style="color: var(--red); padding: 20px;">Error: ${e.message}</div>`;
        console.error('Refresh recommendations error:', e);
    }
}

// State for recommendation modal
let currentRecommendationId = null;

/**
 * Generate new recommendations from all investigations
 * Clears existing pending recommendations first
 */
export async function generateNewRecommendations() {
    const { showToast } = await import('./core.js');
    showToast('Generating recommendations from investigations...');

    try {
        const result = await api('/api/knowledge-base/recommendations/generate', {
            method: 'POST'
        });

        if (result.success !== false) {
            const cleared = result.recommendations_cleared || 0;
            const created = result.recommendations_created || result.recommendations_generated || 0;

            if (cleared > 0) {
                showToast(`Cleared ${cleared} old, generated ${created} new recommendations`);
            } else {
                showToast(`Generated ${created} recommendations`);
            }
            refreshRecommendations();
        } else {
            showToast('Failed to generate: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        const { showToast } = await import('./core.js');
        showToast('Error: ' + e.message);
        console.error('Generate recommendations error:', e);
    }
}

/**
 * Delete all pending recommendations with confirmation
 */
export async function deleteAllRecommendations() {
    const { showToast } = await import('./core.js');

    // Confirm deletion
    if (!confirm('Are you sure you want to delete ALL pending recommendations? This cannot be undone.')) {
        return;
    }

    try {
        const result = await api('/api/knowledge-base/recommendations/delete-all', {
            method: 'DELETE'
        });

        if (result.success) {
            showToast(`Deleted ${result.deleted_count} recommendations`);
            refreshRecommendations();
        } else {
            showToast('Failed to delete: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        console.error('Delete all recommendations error:', e);
    }
}

/**
 * Show recommendation detail modal
 */
export async function showRecommendationDetail(recommendationId) {
    currentRecommendationId = recommendationId;
    const modal = document.getElementById('recommendation-modal');
    const body = document.getElementById('recommendation-modal-body');
    const actions = document.getElementById('recommendation-modal-footer');

    if (body) body.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 20px;">Loading...</div>';
    if (modal) modal.style.display = 'flex';

    // Add modal-open class to body for mobile z-index handling
    document.body.classList.add('modal-open');

    try {
        const data = await api(`/api/knowledge-base/recommendations/${recommendationId}`);

        if (!data || data.error) {
            if (body) body.innerHTML = `<div style="color: var(--red);">Error: ${data?.error || 'Not found'}</div>`;
            return;
        }

        const html = `
            <div class="rec-detail-header">
                <h3>${escapeHtml(data.title || 'Untitled')}</h3>
                <span class="rec-complexity ${data.estimated_complexity || 'medium'}">${data.estimated_complexity || 'medium'}</span>
            </div>
            <div class="rec-detail-section">
                <label>Problem Statement</label>
                <p>${escapeHtml(data.problem_statement || '')}</p>
            </div>
            <div class="rec-detail-section">
                <label>Rationale</label>
                <p>${escapeHtml(data.rationale || '')}</p>
            </div>
            <div class="rec-detail-section">
                <label>Source Investigation</label>
                <p>${escapeHtml(data.investigation_query || data.source_investigation_id || 'Unknown')}</p>
            </div>
            ${data.tags && data.tags.length > 0 ? `
            <div class="rec-detail-section">
                <label>Tags</label>
                <div class="rec-tags">${data.tags.map(t => `<span class="rec-tag">${escapeHtml(t)}</span>`).join('')}</div>
            </div>
            ` : ''}
        `;

        if (body) body.innerHTML = html;

        // Show appropriate actions based on status
        if (actions) {
            if (data.status === 'pending') {
                actions.innerHTML = `
                    <button class="btn" onclick="closeRecommendationModal()">Close</button>
                    <button class="btn danger" onclick="rejectRecommendation()">Reject</button>
                    <button class="btn" onclick="queueRecommendation()">Add to Queue</button>
                    <button class="btn primary" onclick="acceptRecommendation()">Accept</button>
                    <button class="btn success" onclick="convertToMission()">Convert to Mission</button>
                `;
            } else {
                actions.innerHTML = `
                    <button class="btn" onclick="closeRecommendationModal()">Close</button>
                    <span style="color: var(--text-dim);">Status: ${data.status}</span>
                `;
            }
        }
    } catch (e) {
        if (body) body.innerHTML = `<div style="color: var(--red);">Error loading recommendation: ${e.message}</div>`;
        console.error('Show recommendation detail error:', e);
    }
}

/**
 * Close the recommendation modal
 */
export function closeRecommendationModal() {
    const modal = document.getElementById('recommendation-modal');
    if (modal) modal.style.display = 'none';
    currentRecommendationId = null;

    // Remove modal-open class from body
    document.body.classList.remove('modal-open');
}

/**
 * Accept a recommendation
 */
export async function acceptRecommendation() {
    if (!currentRecommendationId) return;

    try {
        const result = await api(`/api/knowledge-base/recommendations/${currentRecommendationId}/accept`, {
            method: 'POST'
        });

        const { showToast } = await import('./core.js');
        if (result.success !== false) {
            showToast('Recommendation accepted');
            closeRecommendationModal();
            refreshRecommendations();
        } else {
            showToast('Failed to accept: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        const { showToast } = await import('./core.js');
        showToast('Error: ' + e.message);
        console.error('Accept recommendation error:', e);
    }
}

/**
 * Reject a recommendation
 */
export async function rejectRecommendation() {
    if (!currentRecommendationId) return;

    try {
        const result = await api(`/api/knowledge-base/recommendations/${currentRecommendationId}/reject`, {
            method: 'POST'
        });

        const { showToast } = await import('./core.js');
        if (result.success !== false) {
            showToast('Recommendation rejected');
            closeRecommendationModal();
            refreshRecommendations();
        } else {
            showToast('Failed to reject: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        const { showToast } = await import('./core.js');
        showToast('Error: ' + e.message);
        console.error('Reject recommendation error:', e);
    }
}

/**
 * Add a recommendation to the mission queue
 */
export async function queueRecommendation() {
    if (!currentRecommendationId) return;

    const { showToast } = await import('./core.js');

    try {
        const result = await api('/api/queue/from-kb-recommendation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ recommendation_id: currentRecommendationId })
        });

        if (result.status === 'added') {
            showToast(`Added to queue (position ${result.queue_length})`);
            closeRecommendationModal();
            refreshRecommendations();

            // Refresh queue widget if available
            if (typeof window.refreshQueueWidget === 'function') {
                window.refreshQueueWidget();
            }
        } else {
            showToast('Failed to add to queue: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        console.error('Queue recommendation error:', e);
    }
}

/**
 * Convert a recommendation to a mission
 */
export async function convertToMission() {
    if (!currentRecommendationId) return;

    const { showToast } = await import('./core.js');

    try {
        // Step 1: Convert KB recommendation to mission format
        const result = await api(`/api/knowledge-base/recommendations/${currentRecommendationId}/convert`, {
            method: 'POST'
        });

        if (result.success === false || result.error || !result.mission_data) {
            showToast('Failed to convert: ' + (result.error || 'No mission data returned'));
            return;
        }

        // Step 2: Add to Mission Suggestions list
        const addResult = await api('/api/recommendations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mission_title: result.mission_data.title,
                mission_description: result.mission_data.problem_statement,
                suggested_cycles: result.mission_data.cycle_budget,
                source_mission_id: result.mission_data.metadata?.source_investigation_id,
                source_mission_summary: result.mission_data.metadata?.investigation_query,
                rationale: result.mission_data.metadata?.rationale
            })
        });

        if (addResult.success) {
            showToast('Recommendation added to Mission Suggestions!');
            closeRecommendationModal();
            refreshRecommendations();  // Refresh KB list (should remove completed)

            // Refresh Mission Suggestions list
            if (typeof window.loadRecommendations === 'function') {
                window.loadRecommendations();
            }
        } else {
            showToast('Failed to add to suggestions: ' + (addResult.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error: ' + e.message);
        console.error('Convert to mission error:', e);
    }
}

// =============================================================================
// EXPANDED RECOMMENDATIONS MODAL (Multi-select & Bulk Clear)
// =============================================================================

// Track selected recommendations
const expandedRecState = {
    page: 1,
    perPage: 50,
    total: 0,
    pages: 0,
    selected: new Set(),
    recommendations: []
};

/**
 * Open the expanded recommendations modal
 */
export async function openExpandedRecModal() {
    const modal = document.getElementById('expanded-recommendations-modal');
    if (modal) modal.style.display = 'flex';

    // Add modal-open class to body for mobile z-index handling
    document.body.classList.add('modal-open');

    // Reset state
    expandedRecState.page = 1;
    expandedRecState.selected.clear();
    updateSelectedCount();

    await loadExpandedRecommendations();
}

/**
 * Close the expanded recommendations modal
 */
export function closeExpandedRecModal() {
    const modal = document.getElementById('expanded-recommendations-modal');
    if (modal) modal.style.display = 'none';

    // Remove modal-open class from body
    document.body.classList.remove('modal-open');

    // Refresh main widget to reflect any changes
    refreshRecommendations();
}

/**
 * Load recommendations into expanded modal with checkboxes
 */
async function loadExpandedRecommendations() {
    const list = document.getElementById('expanded-recommendations-list');
    if (!list) return;

    list.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 20px;">Loading...</div>';

    try {
        const params = new URLSearchParams();
        params.set('page', expandedRecState.page.toString());
        params.set('per_page', expandedRecState.perPage.toString());

        const data = await api(`/api/knowledge-base/recommendations?${params.toString()}`);

        expandedRecState.total = data.total || 0;
        expandedRecState.pages = data.pages || 1;
        expandedRecState.recommendations = data.recommendations || [];

        renderExpandedRecList();
        updateExpandedRecPagination();
    } catch (e) {
        list.innerHTML = `<div style="color: var(--red); padding: 20px;">Error: ${e.message}</div>`;
    }
}

/**
 * Render the expanded recommendations list with checkboxes
 */
function renderExpandedRecList() {
    const list = document.getElementById('expanded-recommendations-list');
    if (!list) return;

    if (expandedRecState.recommendations.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 40px;">No recommendations</div>';
        return;
    }

    const html = expandedRecState.recommendations.map(rec => {
        const isChecked = expandedRecState.selected.has(rec.recommendation_id);
        return `
            <div class="expanded-rec-item ${isChecked ? 'selected' : ''}">
                <label class="expanded-rec-checkbox-label">
                    <input type="checkbox"
                           class="expanded-rec-checkbox"
                           data-id="${rec.recommendation_id}"
                           ${isChecked ? 'checked' : ''}
                           onchange="toggleRecSelection('${rec.recommendation_id}', this.checked)">
                </label>
                <div class="expanded-rec-content" onclick="showRecommendationDetail('${rec.recommendation_id}')">
                    <div class="expanded-rec-title">${escapeHtml(rec.title || 'Untitled')}</div>
                    <div class="expanded-rec-preview">${escapeHtml((rec.problem_statement || '').substring(0, 150))}</div>
                    <div class="expanded-rec-meta">
                        <span class="rec-complexity-badge ${rec.estimated_complexity || 'medium'}">${rec.estimated_complexity || 'medium'}</span>
                        <span class="rec-source">${escapeHtml(rec.source_investigation_id || 'unknown').substring(0, 16)}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    list.innerHTML = html;
}

/**
 * Toggle selection of a recommendation
 */
export function toggleRecSelection(recId, isSelected) {
    if (isSelected) {
        expandedRecState.selected.add(recId);
    } else {
        expandedRecState.selected.delete(recId);
    }

    // Update visual state
    const item = document.querySelector(`[data-id="${recId}"]`)?.closest('.expanded-rec-item');
    if (item) {
        item.classList.toggle('selected', isSelected);
    }

    updateSelectedCount();
}

/**
 * Select all recommendations on current page
 */
export function selectAllRecs() {
    expandedRecState.recommendations.forEach(rec => {
        expandedRecState.selected.add(rec.recommendation_id);
    });
    renderExpandedRecList();
    updateSelectedCount();
}

/**
 * Deselect all recommendations
 */
export function deselectAllRecs() {
    expandedRecState.selected.clear();
    renderExpandedRecList();
    updateSelectedCount();
}

/**
 * Update the selected count display
 */
function updateSelectedCount() {
    const countEl = document.getElementById('expanded-rec-selected-count');
    const clearBtn = document.getElementById('expanded-rec-clear-btn');
    const count = expandedRecState.selected.size;

    if (countEl) countEl.textContent = `${count} selected`;
    if (clearBtn) clearBtn.disabled = count === 0;
}

/**
 * Clear/delete selected recommendations
 */
export async function clearSelectedRecs() {
    if (expandedRecState.selected.size === 0) return;

    const count = expandedRecState.selected.size;
    if (!confirm(`Delete ${count} selected recommendation${count > 1 ? 's' : ''}?`)) return;

    const { showToast } = await import('./core.js');

    try {
        const ids = Array.from(expandedRecState.selected);
        const result = await api('/api/knowledge-base/recommendations/bulk-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
        });

        if (result.success) {
            showToast(`Deleted ${result.deleted_count} recommendations`);
            expandedRecState.selected.clear();
            await loadExpandedRecommendations();
        } else {
            showToast('Failed to delete: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

/**
 * Update pagination display in expanded modal
 */
function updateExpandedRecPagination() {
    const pageInfo = document.getElementById('expanded-rec-page-info');
    const pageNumber = document.getElementById('expanded-rec-page-number');

    if (pageInfo) {
        const start = expandedRecState.total === 0 ? 0 : (expandedRecState.page - 1) * expandedRecState.perPage + 1;
        const end = Math.min(expandedRecState.page * expandedRecState.perPage, expandedRecState.total);
        pageInfo.textContent = `Showing ${start}-${end} of ${expandedRecState.total}`;
    }

    if (pageNumber) {
        pageNumber.textContent = `${expandedRecState.page} / ${expandedRecState.pages}`;
    }
}

/**
 * Navigate expanded modal pages
 */
export async function goToExpandedRecPage(direction) {
    if (direction === 'prev' && expandedRecState.page > 1) {
        expandedRecState.page--;
    } else if (direction === 'next' && expandedRecState.page < expandedRecState.pages) {
        expandedRecState.page++;
    }
    await loadExpandedRecommendations();
}
