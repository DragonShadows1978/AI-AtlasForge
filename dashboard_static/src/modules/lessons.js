/**
 * Dashboard Lessons Learned Module (ES6)
 * Lessons tab with list, details, clusters, duplicates, chains, batch operations
 * Dependencies: core.js, api.js
 */

import { escapeHtml, showToast, downloadJSON, downloadCSV, saveFilterState, getFilterState, restoreFilterToElement } from '../core.js';
import { api } from '../api.js';

// =============================================================================
// LESSONS STATE
// =============================================================================

let lessonsData = [];
let selectedLearningId = null;
let currentLessonsSubtab = 'list';
let clustersData = [];
let duplicatesData = [];
let chainsData = [];
let selectedLearnings = new Set();
let batchDeleteMode = false;
let analyticsVisible = false;
let selectedMergeTarget = {};

// =============================================================================
// MAIN LESSONS FUNCTIONS
// =============================================================================

export async function loadAllLessons() {
    try {
        // Restore saved filter states before reading values
        restoreFilterToElement('lessons-source-filter');
        restoreFilterToElement('lessons-domain-filter');
        restoreFilterToElement('lessons-type-filter');

        const domainEl = document.getElementById('lessons-domain-filter');
        const typeEl = document.getElementById('lessons-type-filter');
        const sourceEl = document.getElementById('lessons-source-filter');

        const domain = domainEl ? domainEl.value : '';
        const type = typeEl ? typeEl.value : '';
        const sourceType = sourceEl ? sourceEl.value : '';

        let url = '/api/knowledge-base/learnings?limit=100';
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);
        if (sourceType) url += '&source_type=' + encodeURIComponent(sourceType);

        const data = await api(url);
        lessonsData = data.learnings || [];
        renderLessonsList(lessonsData);

        // Update stats
        const stats = await api('/api/knowledge-base/stats');
        if (!stats.error) {
            const totalEl = document.getElementById('lessons-total-count');
            const missionsEl = document.getElementById('lessons-missions-count');
            if (totalEl) totalEl.textContent = stats.total_learnings || 0;
            if (missionsEl) missionsEl.textContent = stats.total_missions || 0;
        }

        // Load domains for filter
        const domains = await api('/api/knowledge-base/domains');
        if (domains.domains && domains.domains.length > 0) {
            const select = document.getElementById('lessons-domain-filter');
            if (select) {
                const currentValue = select.value;
                select.innerHTML = '<option value="">All Domains</option>' +
                    domains.domains.map(d => `<option value="${d}">${d}</option>`).join('');
                select.value = currentValue;
            }
        }
    } catch (e) {
        console.error('Load lessons error:', e);
    }
}

export async function searchLessons() {
    const searchInput = document.getElementById('lessons-search-input');
    const query = searchInput ? searchInput.value.trim() : '';

    if (!query) {
        loadAllLessons();
        return;
    }

    try {
        const domainEl = document.getElementById('lessons-domain-filter');
        const typeEl = document.getElementById('lessons-type-filter');
        const sourceEl = document.getElementById('lessons-source-filter');

        const domain = domainEl ? domainEl.value : '';
        const type = typeEl ? typeEl.value : '';
        const sourceType = sourceEl ? sourceEl.value : '';

        let url = '/api/knowledge-base/search?q=' + encodeURIComponent(query);
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);
        if (sourceType) url += '&source_type=' + encodeURIComponent(sourceType);

        const data = await api(url);
        lessonsData = data.results || [];
        renderLessonsList(lessonsData);
    } catch (e) {
        console.error('Search lessons error:', e);
    }
}

function renderLessonsList(lessons) {
    const list = document.getElementById('lessons-list');
    if (!list) return;

    if (!lessons || lessons.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learnings found</div>';
        return;
    }

    const html = lessons.map(l => `
        <div class="learning-item ${l.learning_id === selectedLearningId ? 'active' : ''}"
             onclick="showLearningDetails('${l.learning_id}')">
            <div class="learning-item-title">${escapeHtml(l.title || 'Untitled')}</div>
            <div class="learning-item-meta">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
                ${l.problem_domain || ''}
            </div>
        </div>
    `).join('');

    list.innerHTML = html;
}

export async function showLearningDetails(learningId) {
    selectedLearningId = learningId;
    renderLessonsList(lessonsData);

    try {
        const data = await api('/api/knowledge-base/learnings/' + learningId);
        if (data.error) {
            const detailsEl = document.getElementById('lessons-details');
            if (detailsEl) detailsEl.innerHTML = '<div style="color: var(--red);">Error loading learning</div>';
            return;
        }

        // Check if sourced from investigation
        const isFromInvestigation = data.source_type === 'investigation';
        const sourceLabel = isFromInvestigation ? 'Investigation' : 'Mission';
        const sourceColor = isFromInvestigation ? 'var(--accent)' : 'var(--green)';

        // Investigation report link (if available)
        let investigationReportLink = '';
        if (isFromInvestigation && data.source_investigation_id) {
            investigationReportLink = `
                <button class="btn btn-sm" onclick="viewInvestigationReport('${data.source_investigation_id}')"
                        style="margin-top: 8px; font-size: 0.8em;">
                    View Investigation Report
                </button>
            `;
        }

        const html = `
            <div class="learning-detail-panel">
                <div class="learning-detail-section">
                    <h4>Title</h4>
                    <p>${escapeHtml(data.title || 'Untitled')}</p>
                </div>
                <div class="learning-detail-section">
                    <h4>Type & Domain</h4>
                    <p>
                        <span class="learning-type-badge ${data.learning_type || ''}">${data.learning_type || 'unknown'}</span>
                        ${escapeHtml(data.problem_domain || 'No domain')}
                    </p>
                </div>
                <div class="learning-detail-section">
                    <h4>Source</h4>
                    <p>
                        <span style="padding: 2px 8px; background: ${sourceColor}; color: var(--bg); border-radius: 4px; font-size: 0.8em; margin-right: 8px;">${sourceLabel}</span>
                        <span style="color: var(--text-dim); font-size: 0.85em;">${escapeHtml(data.mission_id || data.source_investigation_id || 'Unknown')}</span>
                    </p>
                    ${isFromInvestigation && data.investigation_query ? `
                    <p style="color: var(--text-dim); font-size: 0.8em; margin-top: 4px;">
                        Query: "${escapeHtml(data.investigation_query)}"
                    </p>
                    ` : ''}
                    ${investigationReportLink}
                </div>
                <div class="learning-detail-section">
                    <h4>Description</h4>
                    <p style="white-space: pre-wrap;">${escapeHtml(data.description || 'No description')}</p>
                </div>
                <div class="learning-detail-section">
                    <h4>Outcome</h4>
                    <p>${escapeHtml(data.outcome || 'Unknown')}</p>
                </div>
                ${data.relevance_keywords && data.relevance_keywords.length > 0 ? `
                <div class="learning-detail-section">
                    <h4>Keywords</h4>
                    <div class="learning-keywords">
                        ${data.relevance_keywords.map(k => `<span class="learning-keyword">${escapeHtml(k)}</span>`).join('')}
                    </div>
                </div>
                ` : ''}
                <div class="learning-detail-section related-learnings-section">
                    <h4>Related Learnings</h4>
                    <div id="related-learnings-list" class="related-learnings-list">
                        <div class="kb-loading" style="padding: 10px;">
                            <div class="kb-spinner" style="width: 16px; height: 16px;"></div>
                            <span style="margin-left: 8px; color: var(--text-dim); font-size: 0.85em;">Loading related learnings...</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const detailsEl = document.getElementById('lessons-details');
        if (detailsEl) detailsEl.innerHTML = html;

        // Load related learnings asynchronously
        loadRelatedLearnings(learningId, data.problem_domain, data.relevance_keywords || []);
    } catch (e) {
        console.error('Show learning details error:', e);
    }
}

// View investigation report in a modal
export async function viewInvestigationReport(investigationId) {
    const modal = document.getElementById('investigation-report-modal');
    const body = document.getElementById('investigation-report-body');
    const title = document.getElementById('investigation-report-title');

    if (!modal) {
        // Fallback: open in new tab
        window.open(`/api/knowledge-base/investigations/${investigationId}/report`, '_blank');
        return;
    }

    modal.style.display = 'flex';
    if (title) title.textContent = `Investigation Report: ${investigationId}`;
    if (body) body.innerHTML = '<div style="color: var(--text-dim); text-align: center; padding: 40px;">Loading report...</div>';

    try {
        const data = await api(`/api/knowledge-base/investigations/${investigationId}/report`);
        if (data.error) {
            if (body) body.innerHTML = `<div style="color: var(--red); padding: 20px;">Error: ${escapeHtml(data.error)}</div>`;
            return;
        }

        // Prefer HTML content if available, otherwise show raw markdown
        if (data.html_content) {
            if (body) body.innerHTML = `<div class="investigation-report-content">${data.html_content}</div>`;
        } else {
            if (body) body.innerHTML = `<pre style="white-space: pre-wrap; font-size: 0.85em;">${escapeHtml(data.content)}</pre>`;
        }
    } catch (e) {
        if (body) body.innerHTML = `<div style="color: var(--red); padding: 20px;">Error loading report: ${e.message}</div>`;
    }
}

export function closeInvestigationReportModal() {
    const modal = document.getElementById('investigation-report-modal');
    if (modal) modal.style.display = 'none';
}

// =============================================================================
// RELATED LEARNINGS (Cross-Reference)
// =============================================================================

async function loadRelatedLearnings(currentLearningId, domain, keywords) {
    const container = document.getElementById('related-learnings-list');
    if (!container) return;

    try {
        // Use the search API to find similar learnings by domain
        let url = '/api/knowledge-base/search?limit=6';
        if (domain) {
            url += '&q=' + encodeURIComponent(domain);
        } else if (keywords && keywords.length > 0) {
            url += '&q=' + encodeURIComponent(keywords.slice(0, 3).join(' '));
        } else {
            container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No keywords available for finding related learnings.</div>';
            return;
        }

        const data = await api(url);
        const results = (data.results || []).filter(l => l.learning_id !== currentLearningId);

        if (results.length === 0) {
            container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No related learnings found.</div>';
            return;
        }

        // Group by source type
        const fromMissions = results.filter(l => l.source_type !== 'investigation');
        const fromInvestigations = results.filter(l => l.source_type === 'investigation');

        let html = '';

        if (fromMissions.length > 0) {
            html += `<div class="related-group">
                <div style="color: var(--green); font-size: 0.8em; margin-bottom: 4px;">From Missions (${fromMissions.length})</div>
                ${fromMissions.slice(0, 3).map(l => `
                    <div class="related-learning-item" onclick="showLearningDetails('${l.learning_id}')" style="cursor: pointer; padding: 4px 8px; margin: 2px 0; background: var(--bg); border-radius: 4px; font-size: 0.85em;">
                        <span class="learning-type-badge ${l.learning_type || ''}" style="font-size: 0.75em; margin-right: 4px;">${l.learning_type || '?'}</span>
                        ${escapeHtml((l.title || 'Untitled').substring(0, 50))}
                    </div>
                `).join('')}
            </div>`;
        }

        if (fromInvestigations.length > 0) {
            html += `<div class="related-group" style="margin-top: 8px;">
                <div style="color: var(--accent); font-size: 0.8em; margin-bottom: 4px;">From Investigations (${fromInvestigations.length})</div>
                ${fromInvestigations.slice(0, 3).map(l => `
                    <div class="related-learning-item" onclick="showLearningDetails('${l.learning_id}')" style="cursor: pointer; padding: 4px 8px; margin: 2px 0; background: var(--bg); border-radius: 4px; font-size: 0.85em;">
                        <span class="learning-type-badge ${l.learning_type || ''}" style="font-size: 0.75em; margin-right: 4px;">${l.learning_type || '?'}</span>
                        ${escapeHtml((l.title || 'Untitled').substring(0, 50))}
                    </div>
                `).join('')}
            </div>`;
        }

        container.innerHTML = html || '<div style="color: var(--text-dim); font-size: 0.85em;">No related learnings found.</div>';
    } catch (e) {
        container.innerHTML = '<div style="color: var(--red); font-size: 0.85em;">Error loading related learnings</div>';
        console.error('Load related learnings error:', e);
    }
}

// =============================================================================
// SUBTAB NAVIGATION
// =============================================================================

export function showLessonsSubtab(subtab) {
    currentLessonsSubtab = subtab;

    // Update subtab buttons
    document.querySelectorAll('.lessons-subtab').forEach(btn => {
        btn.classList.remove('active');
        if (btn.textContent.toLowerCase() === subtab) {
            btn.classList.add('active');
        }
    });

    // Show/hide content
    document.querySelectorAll('.subtab-content').forEach(el => {
        el.classList.remove('active');
    });
    const content = document.getElementById('lessons-' + subtab + '-content');
    if (content) content.classList.add('active');

    // Load data for selected subtab
    if (subtab === 'clusters') {
        loadClusters();
    } else if (subtab === 'duplicates') {
        loadDuplicates();
    } else if (subtab === 'chains') {
        loadLearningChains();
    }
}

// =============================================================================
// LEARNING CHAINS
// =============================================================================

export async function loadLearningChains() {
    try {
        const data = await api('/api/knowledge-base/learning-chains?min_length=2');
        chainsData = data.chains || [];
        const countEl = document.getElementById('chains-count');
        if (countEl) countEl.textContent = chainsData.length > 0 ? `(${chainsData.length} chains found)` : '';
        renderChainsPanel(chainsData);
    } catch (e) {
        console.error('Load learning chains error:', e);
        const panel = document.getElementById('chains-panel');
        if (panel) panel.innerHTML = '<div style="color: var(--red);">Error loading learning chains</div>';
    }
}

function renderChainsPanel(chains) {
    const container = document.getElementById('chains-panel');
    if (!container) return;

    if (!chains || chains.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learning chains found. Chains require at least 2 related learnings across different missions.</div>';
        return;
    }

    container.innerHTML = chains.map((chain, idx) => `
        <div class="chain-node">
            <div class="chain-header" onclick="toggleChain(${idx})">
                <span class="expand-icon">&#9654;</span>
                <span class="chain-theme">${escapeHtml(chain.theme || 'Unnamed Chain')}</span>
                <span class="coherence-badge">coherence: ${(chain.coherence || 0).toFixed(2)}</span>
                <span class="chain-size">(${chain.learnings?.length || 0} learnings, ${chain.missions?.length || 0} missions)</span>
            </div>
            <div class="chain-content" id="chain-content-${idx}">
                ${renderChainLearnings(chain.learnings || [])}
                ${chain.missions && chain.missions.length > 0 ? `
                    <div class="chain-missions" style="margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);">
                        <span style="color: var(--text-dim); font-size: 0.85em;">Missions: </span>
                        ${chain.missions.map(m => `<span class="mission-badge">${m}</span>`).join(' ')}
                    </div>
                ` : ''}
            </div>
        </div>
    `).join('');
}

function renderChainLearnings(learnings) {
    if (!learnings || learnings.length === 0) return '';
    return learnings.map((l, i) => `
        <div class="chain-learning" onclick="showLearningDetails('${l.learning_id}')" style="display: flex; align-items: center; padding: 8px; cursor: pointer;">
            <span class="chain-step" style="min-width: 24px; color: var(--accent);">${i + 1}.</span>
            <span class="learning-type-badge ${l.learning_type || ''}" style="margin-right: 8px;">${l.learning_type || 'unknown'}</span>
            <span class="learning-item-title" style="flex: 1;">${escapeHtml(l.title || 'Untitled')}</span>
            <span class="chain-mission-id" style="color: var(--text-dim); font-size: 0.8em;">${l.mission_id || ''}</span>
        </div>
    `).join('');
}

export function toggleChain(idx) {
    const headers = document.querySelectorAll('.chain-header');
    const content = document.getElementById('chain-content-' + idx);
    if (headers[idx]) {
        headers[idx].classList.toggle('expanded');
    }
    if (content) {
        content.classList.toggle('expanded');
    }
}

// =============================================================================
// CLUSTERS
// =============================================================================

export async function loadClusters() {
    try {
        const data = await api('/api/knowledge-base/clusters?threshold=0.7');
        clustersData = data.clusters || [];
        renderClustersTree(clustersData);
    } catch (e) {
        console.error('Load clusters error:', e);
        const tree = document.getElementById('clusters-tree');
        if (tree) tree.innerHTML = '<div style="color: var(--red);">Error loading clusters</div>';
    }
}

function renderClustersTree(clusters) {
    const container = document.getElementById('clusters-tree');
    if (!container) return;

    if (!clusters || clusters.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No clusters found. Try clicking "Show All" first.</div>';
        return;
    }

    container.innerHTML = clusters.map((c, idx) => `
        <div class="cluster-node">
            <div class="cluster-header" onclick="toggleCluster(${idx})">
                <span class="expand-icon">&#9654;</span>
                <span class="cluster-theme">${escapeHtml(c.theme || 'Unnamed')}</span>
                <span class="coherence-badge">coherence: ${(c.coherence || 0).toFixed(2)}</span>
                <span class="cluster-size">(${c.size || c.learning_ids?.length || 0} learnings)</span>
            </div>
            <div class="cluster-content" id="cluster-content-${idx}">
                ${renderClusterLearnings(c.learning_ids || [], c.learnings || [])}
            </div>
        </div>
    `).join('');
}

function renderClusterLearnings(learningIds, learnings) {
    if (learnings && learnings.length > 0) {
        return learnings.map(l => `
            <div class="learning-item" onclick="showLearningDetails('${l.learning_id}')">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
                <span class="learning-item-title">${escapeHtml(l.title || 'Untitled')}</span>
            </div>
        `).join('');
    }
    return learningIds.map(id => `
        <div class="learning-item" onclick="showLearningDetails('${id}')">
            <span style="color: var(--text-dim);">${id}</span>
        </div>
    `).join('');
}

export function toggleCluster(idx) {
    const headers = document.querySelectorAll('.cluster-header');
    const content = document.getElementById('cluster-content-' + idx);
    if (headers[idx]) {
        headers[idx].classList.toggle('expanded');
    }
    if (content) {
        content.classList.toggle('expanded');
    }
}

// =============================================================================
// DUPLICATES
// =============================================================================

export async function loadDuplicates() {
    try {
        const data = await api('/api/knowledge-base/duplicates?threshold=0.85');
        duplicatesData = data.duplicate_groups || [];
        const countEl = document.getElementById('duplicates-count');
        if (countEl) countEl.textContent = duplicatesData.length > 0 ? `(${duplicatesData.length} groups)` : '';
        renderDuplicatesPanel(duplicatesData);
    } catch (e) {
        console.error('Load duplicates error:', e);
        const panel = document.getElementById('duplicates-panel');
        if (panel) panel.innerHTML = '<div style="color: var(--red);">Error loading duplicates</div>';
    }
}

function renderDuplicatesPanel(duplicates) {
    const panel = document.getElementById('duplicates-panel');
    if (!panel) return;

    if (!duplicates || duplicates.length === 0) {
        panel.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No duplicates detected at 85% similarity threshold</div>';
        return;
    }

    panel.innerHTML = duplicates.map((group, idx) => {
        const learnings = group.learnings || [];
        const similarity = group.similarity || 0;
        const representative = group.representative;

        return `
            <div class="duplicate-group">
                <div class="duplicate-header">
                    <span>Similarity: ${(similarity * 100).toFixed(0)}%</span>
                    <button class="btn btn-sm" onclick="mergeDuplicates(${idx})">Merge Selected</button>
                </div>
                <div class="duplicate-items">
                    ${learnings.map(l => `
                        <label class="duplicate-item">
                            <input type="radio" name="dup-${idx}" value="${l.learning_id}"
                                   ${l.learning_id === representative ? 'checked' : ''}
                                   onchange="window._selectedMergeTarget[${idx}]='${l.learning_id}'">
                            <span>
                                <strong>${escapeHtml(l.title || 'Untitled')}</strong>
                                <div style="font-size: 0.8em; color: var(--text-dim);">${l.mission_id || ''}</div>
                            </span>
                        </label>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

export async function mergeDuplicates(groupIdx) {
    const group = duplicatesData[groupIdx];
    if (!group) return;

    const learnings = group.learnings || [];
    const keepId = selectedMergeTarget[groupIdx] || group.representative;
    const mergeIds = learnings.filter(l => l.learning_id !== keepId).map(l => l.learning_id);

    if (mergeIds.length === 0) {
        showToast('Select a learning to keep first');
        return;
    }

    try {
        const result = await api('/api/knowledge-base/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds })
        });

        if (result.success) {
            showToast(`Merged ${result.merged} duplicate learnings`);
            loadDuplicates();
            loadAllLessons();
        } else {
            showToast('Merge failed: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

export async function mergeAllDuplicates() {
    if (!confirm('Merge all duplicate groups? This will keep the representative learning from each group.')) return;

    try {
        const data = await api('/api/knowledge-base/duplicates?threshold=0.85');
        const groups = data.duplicate_groups || [];

        if (groups.length === 0) {
            showToast('No duplicates found');
            return;
        }

        let merged = 0;
        for (const group of groups) {
            const learnings = group.learnings || [];
            if (learnings.length < 2) continue;

            const keepId = group.representative || learnings[0].learning_id;
            const mergeIds = learnings.filter(l => l.learning_id !== keepId).map(l => l.learning_id);

            const result = await api('/api/knowledge-base/merge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds })
            });
            if (result.success) merged += mergeIds.length;
        }

        showToast(`Merged ${merged} duplicate learnings`);
        loadAllLessons();
        if (currentLessonsSubtab === 'duplicates') loadDuplicates();
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

// =============================================================================
// QUICK ACTIONS
// =============================================================================

export async function rebuildIndex() {
    showToast('Rebuilding index...');
    try {
        const result = await api('/api/knowledge-base/rebuild-index', { method: 'POST' });
        if (result.success) {
            showToast('Index rebuilt successfully');
        } else {
            showToast('Index rebuild failed: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

export async function exportLearnings(format) {
    try {
        const data = await api('/api/knowledge-base/learnings?limit=10000');
        const learnings = data.learnings || [];

        if (learnings.length === 0) {
            showToast('No learnings to export');
            return;
        }

        const filename = 'learnings_export_' + new Date().toISOString().slice(0,10) + '.' + format;

        if (format === 'json') {
            downloadJSON(learnings, filename);
        } else if (format === 'csv') {
            downloadCSV(learnings, filename);
        }

        showToast('Exported ' + learnings.length + ' learnings');
    } catch (e) {
        showToast('Export error: ' + e.message);
    }
}

// =============================================================================
// ANALYTICS TOGGLE
// =============================================================================

export function toggleAnalytics() {
    const panel = document.getElementById('analytics-panel');
    if (!panel) return;

    analyticsVisible = !analyticsVisible;
    panel.style.display = analyticsVisible ? 'grid' : 'none';

    if (analyticsVisible) {
        loadAnalyticsData();
    }
}

async function loadAnalyticsData() {
    try {
        const stats = await api('/api/knowledge-base/stats');
        renderDomainBarChart(stats.learnings_by_domain || {});
        renderTypeDonutChart(stats.learnings_by_type || {});
    } catch (e) {
        console.error('Analytics load error:', e);
    }
}

function renderDomainBarChart(domains) {
    const container = document.getElementById('domain-bar-chart');
    if (!container) return;

    const entries = Object.entries(domains).sort((a, b) => b[1] - a[1]).slice(0, 5);
    const max = Math.max(...entries.map(e => e[1]), 1);

    if (entries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); text-align: center;">No data</div>';
        return;
    }

    container.innerHTML = entries.map(([domain, count], i) => `
        <div class="bar-row">
            <span class="bar-label">${escapeHtml(domain.replace(/_/g, ' '))}</span>
            <div class="bar-track">
                <div class="bar-fill domain-${i % 5}" style="width: ${(count / max * 100).toFixed(1)}%"></div>
            </div>
            <span class="bar-value">${count}</span>
        </div>
    `).join('');
}

function renderTypeDonutChart(types) {
    const svg = document.querySelector('#type-donut-chart .donut-chart-svg');
    const legend = document.getElementById('type-legend');
    const totalEl = document.getElementById('donut-total');

    if (!svg || !legend || !totalEl) return;

    const entries = Object.entries(types);
    const total = entries.reduce((sum, [, count]) => sum + count, 0);
    totalEl.textContent = total;

    if (total === 0) {
        svg.innerHTML = '';
        legend.innerHTML = '<div style="color: var(--text-dim); text-align: center;">No data</div>';
        return;
    }

    const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff'];
    const circumference = 2 * Math.PI * 35;
    let offset = 0;

    svg.innerHTML = entries.map(([type, count], i) => {
        const percent = count / total;
        const dash = percent * circumference;
        const segment = `
            <circle class="donut-segment"
                    cx="50" cy="50" r="35"
                    stroke="${colors[i % colors.length]}"
                    stroke-dasharray="${dash} ${circumference - dash}"
                    stroke-dashoffset="${-offset}"
            />
        `;
        offset += dash;
        return segment;
    }).join('');

    legend.innerHTML = '<div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;">' +
        entries.map(([type, count], i) => `
            <span style="display: flex; align-items: center; gap: 5px; font-size: 0.8em;">
                <span style="width: 12px; height: 12px; border-radius: 2px; background: ${colors[i % colors.length]};"></span>
                ${type}: ${count}
            </span>
        `).join('') + '</div>';
}

// =============================================================================
// BATCH DELETE
// =============================================================================

export function toggleBatchDeleteMode() {
    batchDeleteMode = !batchDeleteMode;
    selectedLearnings.clear();
    updateBatchUI();

    if (batchDeleteMode) {
        const bar = document.getElementById('batch-action-bar');
        if (bar) bar.classList.add('show');
        showToast('Batch mode: Click checkboxes to select learnings');
        renderLessonsListWithCheckboxes();
    } else {
        exitBatchMode();
    }
}

export function exitBatchMode() {
    batchDeleteMode = false;
    selectedLearnings.clear();
    const bar = document.getElementById('batch-action-bar');
    if (bar) bar.classList.remove('show');
    renderLessonsList(lessonsData);
}

export function toggleLearningSelection(learningId, event) {
    if (event) event.stopPropagation();
    if (selectedLearnings.has(learningId)) {
        selectedLearnings.delete(learningId);
    } else {
        selectedLearnings.add(learningId);
    }
    updateBatchUI();
}

export function selectAllLearnings() {
    lessonsData.forEach(l => selectedLearnings.add(l.learning_id));
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

export function clearSelection() {
    selectedLearnings.clear();
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

function updateBatchUI() {
    const countEl = document.getElementById('selected-count');
    if (countEl) countEl.textContent = selectedLearnings.size;
}

function renderLessonsListWithCheckboxes() {
    const list = document.getElementById('lessons-list');
    if (!list) return;

    if (!lessonsData || lessonsData.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); padding: 20px; text-align: center;">No learnings found</div>';
        return;
    }

    const html = lessonsData.map(l => `
        <div class="learning-item ${l.learning_id === selectedLearningId ? 'active' : ''}"
             onclick="showLearningDetails('${l.learning_id}')">
            <input type="checkbox" class="batch-checkbox"
                   ${selectedLearnings.has(l.learning_id) ? 'checked' : ''}
                   onclick="toggleLearningSelection('${l.learning_id}', event)"
                   style="margin-right: 10px;">
            <div class="learning-item-title">${escapeHtml(l.title || 'Untitled')}</div>
            <div class="learning-item-meta">
                <span class="learning-type-badge ${l.learning_type || ''}">${l.learning_type || 'unknown'}</span>
            </div>
        </div>
    `).join('');

    list.innerHTML = html;
}

export function showBatchDeleteConfirm() {
    if (selectedLearnings.size === 0) {
        showToast('No learnings selected');
        return;
    }

    const selected = Array.from(selectedLearnings);
    const learnings = lessonsData.filter(l => selected.includes(l.learning_id));

    const countEl = document.getElementById('delete-count');
    if (countEl) countEl.textContent = learnings.length;

    const listEl = document.getElementById('delete-confirm-list');
    if (listEl) {
        listEl.innerHTML = learnings.slice(0, 20).map(l => `
            <div class="delete-confirm-item">
                <strong>${escapeHtml(l.title || 'Untitled')}</strong>
                <div style="font-size: 0.85em; color: var(--text-dim);">${escapeHtml((l.description || '').substring(0, 100))}</div>
            </div>
        `).join('') + (learnings.length > 20 ? '<div style="color: var(--text-dim); padding: 8px;">...and ' + (learnings.length - 20) + ' more</div>' : '');
    }

    const modal = document.getElementById('batch-delete-modal');
    if (modal) modal.classList.add('show');
}

export function closeBatchDeleteModal() {
    const modal = document.getElementById('batch-delete-modal');
    if (modal) modal.classList.remove('show');
}

export async function confirmBatchDelete() {
    closeBatchDeleteModal();

    const ids = Array.from(selectedLearnings);
    if (ids.length === 0) return;

    try {
        const result = await api('/api/knowledge-base/batch-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ learning_ids: ids })
        });

        if (result.error) {
            showToast('Delete failed: ' + result.error);
            return;
        }

        showToast('Deleted ' + (result.deleted || ids.length) + ' learnings');
        selectedLearnings.clear();
        exitBatchMode();
        loadAllLessons();
    } catch (e) {
        showToast('Delete error: ' + e.message);
    }
}

// Alias for tab data loading
export const loadLessonsTabData = loadAllLessons;

// =============================================================================
// FILTER CHANGE HANDLERS (for persistence)
// =============================================================================

export function onLessonsFilterChange(filterName) {
    const element = document.getElementById(filterName);
    if (element) {
        saveFilterState(filterName, element.value);
    }
    loadAllLessons();
}

// Expose selectedMergeTarget to window for radio button changes
if (typeof window !== 'undefined') {
    window._selectedMergeTarget = selectedMergeTarget;
}

// Debug: mark lessons module loaded
console.log('Lessons ES6 module loaded');
