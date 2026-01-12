/**
 * Dashboard Lessons Learned Module
 * Lessons tab with list, details, clusters, duplicates, chains, batch operations
 * Dependencies: core.js, api.js
 */

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

async function loadAllLessons() {
    try {
        const domain = document.getElementById('lessons-domain-filter').value;
        const type = document.getElementById('lessons-type-filter').value;

        let url = '/api/knowledge-base/learnings?limit=100';
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);

        const data = await api(url);
        lessonsData = data.learnings || [];
        renderLessonsList(lessonsData);

        // Update stats
        const stats = await api('/api/knowledge-base/stats');
        if (!stats.error) {
            document.getElementById('lessons-total-count').textContent = stats.total_learnings || 0;
            document.getElementById('lessons-missions-count').textContent = stats.total_missions || 0;
        }

        // Load domains for filter
        const domains = await api('/api/knowledge-base/domains');
        if (domains.domains && domains.domains.length > 0) {
            const select = document.getElementById('lessons-domain-filter');
            const currentValue = select.value;
            select.innerHTML = '<option value="">All Domains</option>' +
                domains.domains.map(d => `<option value="${d}">${d}</option>`).join('');
            select.value = currentValue;
        }
    } catch (e) {
        console.error('Load lessons error:', e);
    }
}

async function searchLessons() {
    const query = document.getElementById('lessons-search-input').value.trim();
    if (!query) {
        loadAllLessons();
        return;
    }

    try {
        const domain = document.getElementById('lessons-domain-filter').value;
        const type = document.getElementById('lessons-type-filter').value;

        let url = '/api/knowledge-base/search?q=' + encodeURIComponent(query);
        if (domain) url += '&domain=' + encodeURIComponent(domain);
        if (type) url += '&type=' + encodeURIComponent(type);

        const data = await api(url);
        lessonsData = data.results || [];
        renderLessonsList(lessonsData);
    } catch (e) {
        console.error('Search lessons error:', e);
    }
}

function renderLessonsList(lessons) {
    const list = document.getElementById('lessons-list');
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

async function showLearningDetails(learningId) {
    selectedLearningId = learningId;
    renderLessonsList(lessonsData);

    try {
        const data = await api('/api/knowledge-base/learnings/' + learningId);
        if (data.error) {
            document.getElementById('lessons-details').innerHTML =
                '<div style="color: var(--red);">Error loading learning</div>';
            return;
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
                <div class="learning-detail-section">
                    <h4>Source Mission</h4>
                    <p style="color: var(--text-dim); font-size: 0.85em;">${escapeHtml(data.mission_id || 'Unknown')}</p>
                </div>
            </div>
        `;

        document.getElementById('lessons-details').innerHTML = html;
    } catch (e) {
        console.error('Show learning details error:', e);
    }
}

// =============================================================================
// SUBTAB NAVIGATION
// =============================================================================

function showLessonsSubtab(subtab) {
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

async function loadLearningChains() {
    try {
        const data = await api('/api/knowledge-base/learning-chains?min_length=2');
        chainsData = data.chains || [];
        document.getElementById('chains-count').textContent =
            chainsData.length > 0 ? `(${chainsData.length} chains found)` : '';
        renderChainsPanel(chainsData);
    } catch (e) {
        console.error('Load learning chains error:', e);
        document.getElementById('chains-panel').innerHTML =
            '<div style="color: var(--red);">Error loading learning chains</div>';
    }
}

function renderChainsPanel(chains) {
    const container = document.getElementById('chains-panel');
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

function toggleChain(idx) {
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

async function loadClusters() {
    try {
        const data = await api('/api/knowledge-base/clusters?threshold=0.7');
        clustersData = data.clusters || [];
        renderClustersTree(clustersData);
    } catch (e) {
        console.error('Load clusters error:', e);
        document.getElementById('clusters-tree').innerHTML =
            '<div style="color: var(--red);">Error loading clusters</div>';
    }
}

function renderClustersTree(clusters) {
    const container = document.getElementById('clusters-tree');
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

function toggleCluster(idx) {
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

async function loadDuplicates() {
    try {
        const data = await api('/api/knowledge-base/duplicates?threshold=0.85');
        duplicatesData = data.duplicate_groups || [];
        document.getElementById('duplicates-count').textContent =
            duplicatesData.length > 0 ? `(${duplicatesData.length} groups)` : '';
        renderDuplicatesPanel(duplicatesData);
    } catch (e) {
        console.error('Load duplicates error:', e);
        document.getElementById('duplicates-panel').innerHTML =
            '<div style="color: var(--red);">Error loading duplicates</div>';
    }
}

function renderDuplicatesPanel(duplicates) {
    const panel = document.getElementById('duplicates-panel');
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
                                   onchange="selectedMergeTarget[${idx}]='${l.learning_id}'">
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

async function mergeDuplicates(groupIdx) {
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

async function mergeAllDuplicates() {
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

async function rebuildIndex() {
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

async function exportLearnings(format) {
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

function toggleAnalytics() {
    const panel = document.getElementById('analytics-panel');
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

function toggleBatchDeleteMode() {
    batchDeleteMode = !batchDeleteMode;
    selectedLearnings.clear();
    updateBatchUI();

    if (batchDeleteMode) {
        document.getElementById('batch-action-bar').classList.add('show');
        showToast('Batch mode: Click checkboxes to select learnings');
        renderLessonsListWithCheckboxes();
    } else {
        exitBatchMode();
    }
}

function exitBatchMode() {
    batchDeleteMode = false;
    selectedLearnings.clear();
    document.getElementById('batch-action-bar').classList.remove('show');
    renderLessonsList(lessonsData);
}

function toggleLearningSelection(learningId, event) {
    if (event) event.stopPropagation();
    if (selectedLearnings.has(learningId)) {
        selectedLearnings.delete(learningId);
    } else {
        selectedLearnings.add(learningId);
    }
    updateBatchUI();
}

function selectAllLearnings() {
    lessonsData.forEach(l => selectedLearnings.add(l.learning_id));
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

function clearSelection() {
    selectedLearnings.clear();
    renderLessonsListWithCheckboxes();
    updateBatchUI();
}

function updateBatchUI() {
    document.getElementById('selected-count').textContent = selectedLearnings.size;
}

function renderLessonsListWithCheckboxes() {
    const list = document.getElementById('lessons-list');
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

function showBatchDeleteConfirm() {
    if (selectedLearnings.size === 0) {
        showToast('No learnings selected');
        return;
    }

    const selected = Array.from(selectedLearnings);
    const learnings = lessonsData.filter(l => selected.includes(l.learning_id));

    document.getElementById('delete-count').textContent = learnings.length;

    const listEl = document.getElementById('delete-confirm-list');
    listEl.innerHTML = learnings.slice(0, 20).map(l => `
        <div class="delete-confirm-item">
            <strong>${escapeHtml(l.title || 'Untitled')}</strong>
            <div style="font-size: 0.85em; color: var(--text-dim);">${escapeHtml((l.description || '').substring(0, 100))}</div>
        </div>
    `).join('') + (learnings.length > 20 ? '<div style="color: var(--text-dim); padding: 8px;">...and ' + (learnings.length - 20) + ' more</div>' : '');

    document.getElementById('batch-delete-modal').classList.add('show');
}

function closeBatchDeleteModal() {
    document.getElementById('batch-delete-modal').classList.remove('show');
}

async function confirmBatchDelete() {
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

// Debug: mark lessons module loaded
console.log('Lessons module loaded');
