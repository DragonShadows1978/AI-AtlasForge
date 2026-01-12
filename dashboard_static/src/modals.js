/**
 * Dashboard Modals Module (ES6)
 * Modal dialogs, mission modal, recommendations, etc.
 * Dependencies: core.js, api.js
 */

import { showToast, escapeHtml, formatDate } from './core.js';
import { api } from './api.js';

// =============================================================================
// MODAL STATE
// =============================================================================

let fullMissionText = '';
let recommendations = [];
let selectedRecId = null;
let isEditMode = false;
let editedRecData = null;
let selectedForMerge = new Set();

// Store scroll position when modal opens (for mobile)
let savedScrollX = 0;
let savedScrollY = 0;

// Sort order state
let currentSortField = 'priority_score';

// Merge candidates state (for auto-prompt)
let pendingMergeCandidates = [];
let newSuggestionId = null;

// Filter state
let currentTagFilter = '';
let currentHealthFilter = '';
let allRecommendations = [];

// Pagination state
let currentPage = 1;
const itemsPerPage = 25;

// =============================================================================
// MISSION MODAL FUNCTIONS
// =============================================================================

export function setFullMissionText(text) {
    fullMissionText = text;
}

export function openMissionModal() {
    document.getElementById('mission-full-text').textContent = fullMissionText;
    document.getElementById('mission-modal').classList.add('show');
    // Add modal-open to body for mobile z-index fix
    // Save scroll position first
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;
    document.body.classList.add('modal-open');
}

export function closeMissionModal() {
    document.getElementById('mission-modal').classList.remove('show');
    // Remove modal-open from body
    removeModalOpenClass();
}

// Helper to remove modal-open class only if no modals are visible
function removeModalOpenClass() {
    // Check if any modal is still visible
    const visibleModals = document.querySelectorAll('.modal.show, .modal[style*="display: flex"], .modal[style*="display:flex"]');
    if (visibleModals.length === 0) {
        document.body.classList.remove('modal-open');
        // Restore scroll position after removing body.modal-open (which uses position:fixed)
        window.scrollTo(savedScrollX, savedScrollY);
    }
}

export function copyMission() {
    navigator.clipboard.writeText(fullMissionText).then(() => {
        showToast('Mission copied to clipboard');
    });
}

// =============================================================================
// RECOMMENDATIONS FUNCTIONS
// =============================================================================

export async function loadRecommendations() {
    // Use analyze endpoint to get auto-tagged, prioritized, health-checked suggestions
    try {
        const data = await api('/api/recommendations/analyze');
        recommendations = data.items || [];
        // Store health report for potential UI display
        if (data.health_report) {
            window._recHealthReport = data.health_report;
        }
    } catch (e) {
        // Fallback to basic recommendations if analyze fails
        console.warn('Analyze failed, falling back to basic load:', e);
        const data = await api('/api/recommendations');
        recommendations = data.items || [];
    }
    // Store all recommendations for filtering
    allRecommendations = [...recommendations];
    // Reset pagination on reload
    currentPage = 1;
    // Apply current sort before rendering
    applySortToRecommendations();
    renderRecommendations();
    updateRecCount();
    // Update health summary bar
    updateHealthSummaryBar();
    // Update pagination controls
    updatePaginationControls();
}

function renderRecommendations() {
    const container = document.getElementById('legacy-recommendations-list');
    if (!container) return;

    if (recommendations.length === 0) {
        container.innerHTML = '<div class="rec-placeholder">No suggestions yet. Complete a mission to get suggestions.</div>';
        return;
    }

    // Get paginated recommendations (only if > 25 items)
    const paginatedRecs = recommendations.length > itemsPerPage ? getPaginatedRecommendations() : recommendations;

    container.innerHTML = paginatedRecs.map(rec => {
        const isDriftHalt = rec.source_type === 'drift_halt';
        const isMerged = rec.source_type === 'merged';
        const itemClass = isDriftHalt ? 'rec-item drift-halt' : (isMerged ? 'rec-item merged' : 'rec-item');
        const sourceBadge = isDriftHalt
            ? '<span class="rec-source-badge drift">From Drift</span>'
            : (rec.source_type === 'successful_completion'
                ? '<span class="rec-source-badge success">Follow-up</span>'
                : (isMerged
                    ? `<span class="rec-source-badge merged">Merged (${(rec.merged_from || []).length})</span>`
                    : ''));

        // Auto-tags badges
        const tagBadges = (rec.auto_tags || []).slice(0, 3).map(tag =>
            `<span class="rec-tag-badge ${tag}">${tag}</span>`
        ).join('');

        // Priority score indicator
        const priorityScore = rec.priority_score || 0;
        const priorityClass = priorityScore >= 70 ? 'high' : (priorityScore >= 40 ? 'medium' : 'low');
        const priorityBadge = priorityScore > 0
            ? `<span class="rec-priority-score ${priorityClass}"><span class="score-value">${Math.round(priorityScore)}</span></span>`
            : '';

        // Health status badge
        const healthStatus = rec.health_status || 'healthy';
        const healthBadge = healthStatus !== 'healthy'
            ? `<span class="rec-health-badge ${healthStatus}">${healthStatus.replace('_', ' ')}</span>`
            : '';

        return `
            <div class="${itemClass}" onclick="window.openRecModal('${rec.id}')">
                <div class="rec-item-content">
                    <div class="rec-item-title">
                        ${escapeHtml(rec.mission_title)}
                        ${sourceBadge}
                        ${healthBadge}
                    </div>
                    <div class="rec-tags-container">${tagBadges}</div>
                    <div class="rec-item-preview">${escapeHtml((rec.mission_description || '').substring(0, 100))}${(rec.mission_description || '').length > 100 ? '...' : ''}</div>
                </div>
                <div class="rec-item-meta">
                    ${priorityBadge}
                    <span class="rec-cycles-badge">${rec.suggested_cycles || 3} cycles</span>
                    <span>${formatDate(rec.created_at)}</span>
                </div>
            </div>
        `;
    }).join('');
}

export function openRecModal(recId) {
    selectedRecId = recId;
    const rec = recommendations.find(r => r.id === recId);
    if (!rec) return;

    const isDriftHalt = rec.source_type === 'drift_halt';

    // Set modal title with source indicator
    document.getElementById('rec-modal-title').textContent =
        isDriftHalt ? 'Mission Suggestion (From Drift Analysis)' : 'Mission Recommendation';
    document.getElementById('rec-modal-mission-title').textContent = rec.mission_title || 'Untitled';
    document.getElementById('rec-modal-description').textContent = rec.mission_description || 'No description';
    document.getElementById('rec-modal-rationale').textContent = rec.rationale || 'No rationale provided';
    document.getElementById('rec-modal-source').textContent = rec.source_mission_id
        ? `From: ${rec.source_mission_id}${rec.source_mission_summary ? ' - ' + rec.source_mission_summary.substring(0, 100) : ''}`
        : 'Manual recommendation';

    // Drift context display
    const driftContextEl = document.getElementById('rec-modal-drift-context');
    if (driftContextEl) {
        if (isDriftHalt && rec.drift_context) {
            const ctx = rec.drift_context;
            driftContextEl.style.display = 'block';
            driftContextEl.innerHTML = `
                <div class="drift-context">
                    <div class="drift-context-header">Drift Analysis Details</div>
                    <div class="drift-metrics">
                        <div class="drift-metric">
                            <span class="drift-metric-label">Failures:</span>
                            <span class="drift-metric-value">${ctx.drift_failures || 0}</span>
                        </div>
                        <div class="drift-metric">
                            <span class="drift-metric-label">Similarity:</span>
                            <span class="drift-metric-value">${((ctx.average_similarity || 0) * 100).toFixed(1)}%</span>
                        </div>
                        <div class="drift-metric">
                            <span class="drift-metric-label">Halted at Cycle:</span>
                            <span class="drift-metric-value">${ctx.halted_at_cycle || 'N/A'}</span>
                        </div>
                    </div>
                    ${ctx.pattern_analysis ? buildPatternAnalysisHTML(ctx.pattern_analysis) : ''}
                </div>
            `;
        } else {
            driftContextEl.style.display = 'none';
            driftContextEl.innerHTML = '';
        }
    }

    const cyclesSelect = document.getElementById('rec-modal-cycles');
    const suggestedCycles = rec.suggested_cycles || 3;
    cyclesSelect.value = suggestedCycles;

    // Save scroll position first before opening modal
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;

    document.getElementById('rec-modal').style.display = 'flex';
    // Add modal-open to body for mobile z-index fix
    document.body.classList.add('modal-open');
}

/**
 * Build HTML for pattern analysis section in drift context
 */
function buildPatternAnalysisHTML(pattern) {
    if (!pattern) return '';

    let html = '<details class="drift-pattern-details"><summary>Pattern Analysis</summary><div class="pattern-content">';

    if (pattern.consistently_added_scope && pattern.consistently_added_scope.length > 0) {
        html += '<div class="pattern-section"><strong>Scope Expansions:</strong><ul>';
        pattern.consistently_added_scope.slice(0, 3).forEach(item => {
            const itemText = typeof item === 'object' ? (item.item || JSON.stringify(item)) : item;
            const count = typeof item === 'object' ? (item.count || 1) : 1;
            html += `<li>${escapeHtml(itemText)} (${count}x)</li>`;
        });
        html += '</ul></div>';
    }

    if (pattern.consistently_lost_focus && pattern.consistently_lost_focus.length > 0) {
        html += '<div class="pattern-section"><strong>Lost Focus On:</strong><ul>';
        pattern.consistently_lost_focus.slice(0, 3).forEach(item => {
            const itemText = typeof item === 'object' ? (item.item || JSON.stringify(item)) : item;
            const count = typeof item === 'object' ? (item.count || 1) : 1;
            html += `<li>${escapeHtml(itemText)} (${count}x)</li>`;
        });
        html += '</ul></div>';
    }

    if (pattern.drift_accelerating) {
        html += '<div class="pattern-warning">⚠ Drift was accelerating</div>';
    }

    html += '</div></details>';
    return html;
}

export function closeRecModal() {
    document.getElementById('rec-modal').style.display = 'none';
    selectedRecId = null;
    // Remove modal-open from body
    removeModalOpenClass();
}

export async function deleteRecommendation() {
    if (!selectedRecId) return;

    if (!confirm('Delete this recommendation?')) return;

    await api('/api/recommendations/' + selectedRecId, 'DELETE');
    showToast('Recommendation deleted');
    closeRecModal();
    await loadRecommendations();
}

export async function setMissionFromRec() {
    if (!selectedRecId) return;

    const cycleBudget = parseInt(document.getElementById('rec-modal-cycles').value) || 3;

    const data = await api('/api/recommendations/' + selectedRecId + '/set-mission', 'POST', {
        cycle_budget: cycleBudget
    });

    if (data.success) {
        showToast(data.message);
        closeRecModal();
        await loadRecommendations();
        if (typeof window.refresh === 'function') {
            window.refresh();
        }
    } else {
        showToast('Error: ' + (data.error || 'Failed to set mission'));
    }
}

function updateRecCount() {
    const el = document.getElementById('rec-count');
    if (el) {
        el.textContent = recommendations.length;
    }
}

// =============================================================================
// EDIT MODE FUNCTIONS
// =============================================================================

export function toggleEditMode() {
    isEditMode = !isEditMode;
    const rec = recommendations.find(r => r.id === selectedRecId);
    if (!rec) return;

    const editBtn = document.getElementById('rec-edit-toggle-btn');
    const viewContainer = document.getElementById('rec-view-container');
    const editContainer = document.getElementById('rec-edit-container');
    const saveBtn = document.getElementById('rec-save-btn');
    const cancelBtn = document.getElementById('rec-cancel-edit-btn');

    if (isEditMode) {
        // Switch to edit mode
        editedRecData = {
            mission_title: rec.mission_title || '',
            mission_description: rec.mission_description || '',
            rationale: rec.rationale || '',
            suggested_cycles: rec.suggested_cycles || 3
        };

        // Populate edit fields
        document.getElementById('rec-edit-title').value = editedRecData.mission_title;
        document.getElementById('rec-edit-description').value = editedRecData.mission_description;
        document.getElementById('rec-edit-rationale').value = editedRecData.rationale;
        document.getElementById('rec-modal-cycles').value = editedRecData.suggested_cycles;

        // Show edit container, hide view container
        if (viewContainer) viewContainer.style.display = 'none';
        if (editContainer) editContainer.style.display = 'block';
        if (editBtn) editBtn.textContent = 'Cancel';
        if (saveBtn) saveBtn.style.display = 'inline-block';
        if (cancelBtn) cancelBtn.style.display = 'inline-block';
    } else {
        // Switch back to view mode
        if (viewContainer) viewContainer.style.display = 'block';
        if (editContainer) editContainer.style.display = 'none';
        if (editBtn) editBtn.textContent = 'Edit';
        if (saveBtn) saveBtn.style.display = 'none';
        if (cancelBtn) cancelBtn.style.display = 'none';
        editedRecData = null;
    }
}

export async function saveRecChanges() {
    if (!selectedRecId) return;

    const data = {
        mission_title: document.getElementById('rec-edit-title').value,
        mission_description: document.getElementById('rec-edit-description').value,
        rationale: document.getElementById('rec-edit-rationale').value,
        suggested_cycles: parseInt(document.getElementById('rec-modal-cycles').value)
    };

    try {
        const result = await api('/api/recommendations/' + selectedRecId, 'PUT', data);

        // Check for validation errors
        if (result.success === false) {
            showToast(result.error || 'Validation failed', 'error');
            return;
        }

        // Flash the modal body to show save success
        const modalBody = document.querySelector('#rec-modal .modal-body');
        if (modalBody) {
            modalBody.classList.add('saved-flash');
            setTimeout(() => modalBody.classList.remove('saved-flash'), 800);
        }

        showToast('Suggestion updated');
        isEditMode = false;
        await loadRecommendations();
        // Re-open modal with updated data
        openRecModal(selectedRecId);
    } catch (e) {
        showToast('Error saving: ' + e.message, 'error');
    }
}

export function cancelEditMode() {
    isEditMode = true; // Will be toggled to false
    toggleEditMode();
}

// =============================================================================
// SIMILARITY AUDIT FUNCTIONS
// =============================================================================

export async function openSimilarityAudit() {
    const modal = document.getElementById('similarity-modal');
    const body = document.getElementById('similarity-modal-body');

    if (!modal || !body) {
        showToast('Similarity modal not found', 'error');
        return;
    }

    body.innerHTML = '<div class="loading-spinner">Analyzing suggestions...</div>';
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');

    try {
        const threshold = parseFloat(document.getElementById('similarity-threshold')?.value || 0.3);
        const data = await api('/api/recommendations/similarity-analysis?threshold=' + threshold);

        if (data.groups && data.groups.length > 0) {
            renderSimilarityGroups(data.groups, data.threshold);
        } else {
            body.innerHTML = `
                <div class="similarity-empty">
                    <p>${data.message || 'No similar suggestions found at this threshold.'}</p>
                    <p>Total suggestions: ${data.total_items || 0}</p>
                    <p>Try lowering the similarity threshold.</p>
                </div>
            `;
        }
    } catch (e) {
        body.innerHTML = '<div class="similarity-error">Error analyzing: ' + escapeHtml(e.message) + '</div>';
    }
}

function renderSimilarityGroups(groups, threshold) {
    const body = document.getElementById('similarity-modal-body');
    selectedForMerge.clear();

    let html = `
        <div class="similarity-header">
            <p>Found ${groups.length} group(s) of similar suggestions (threshold: ${(threshold * 100).toFixed(0)}%)</p>
        </div>
        <div class="similarity-groups">
    `;

    groups.forEach((group, groupIdx) => {
        html += `
            <div class="similarity-group" data-group="${groupIdx}">
                <div class="similarity-group-header">
                    <span class="group-label">Group ${groupIdx + 1}</span>
                    <span class="group-similarity">Avg similarity: ${(group.avg_similarity * 100).toFixed(1)}%</span>
                    <button class="btn btn-small" onclick="window.selectGroupForMerge(${groupIdx})">Select All</button>
                </div>
                <div class="similarity-group-items">
        `;

        group.items.forEach(item => {
            html += `
                <div class="similarity-item">
                    <label class="similarity-item-checkbox">
                        <input type="checkbox" data-rec-id="${item.id}" onchange="window.toggleMergeSelection('${item.id}')">
                    </label>
                    <div class="similarity-item-content">
                        <div class="similarity-item-title">${escapeHtml(item.mission_title)}</div>
                        <div class="similarity-item-preview">${escapeHtml(item.mission_description || '')}</div>
                        <div class="similarity-item-meta">
                            <span class="rec-cycles-badge">${item.suggested_cycles} cycles</span>
                            <span class="similarity-score">${(item.similarity_score * 100).toFixed(1)}% match</span>
                        </div>
                    </div>
                </div>
            `;
        });

        html += `
                </div>
            </div>
        `;
    });

    html += `
        </div>
        <div class="similarity-actions">
            <span id="merge-selection-count">0 selected</span>
            <button class="btn primary" id="merge-selected-btn" onclick="window.openMergeModal()" disabled>Merge Selected</button>
        </div>
    `;

    body.innerHTML = html;
}

export function closeSimilarityModal() {
    const modal = document.getElementById('similarity-modal');
    if (modal) modal.style.display = 'none';
    selectedForMerge.clear();
    removeModalOpenClass();
}

export function selectGroupForMerge(groupIdx) {
    const groupEl = document.querySelector(`.similarity-group[data-group="${groupIdx}"]`);
    if (!groupEl) return;

    const checkboxes = groupEl.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = true;
        selectedForMerge.add(cb.dataset.recId);
    });
    updateMergeSelectionCount();
}

export function toggleMergeSelection(recId) {
    if (selectedForMerge.has(recId)) {
        selectedForMerge.delete(recId);
    } else {
        selectedForMerge.add(recId);
    }
    updateMergeSelectionCount();
}

function updateMergeSelectionCount() {
    const countEl = document.getElementById('merge-selection-count');
    const mergeBtn = document.getElementById('merge-selected-btn');

    if (countEl) countEl.textContent = `${selectedForMerge.size} selected`;
    if (mergeBtn) mergeBtn.disabled = selectedForMerge.size < 2;
}

// =============================================================================
// MERGE MODAL FUNCTIONS
// =============================================================================

export function openMergeModal() {
    if (selectedForMerge.size < 2) {
        showToast('Select at least 2 suggestions to merge', 'error');
        return;
    }

    const modal = document.getElementById('merge-modal');
    const body = document.getElementById('merge-modal-body');

    if (!modal || !body) {
        showToast('Merge modal not found', 'error');
        return;
    }

    // Get selected recommendations
    const selectedRecs = recommendations.filter(r => selectedForMerge.has(r.id));

    // Generate combined title and description
    const combinedTitle = selectedRecs.map(r => r.mission_title).join(' + ');
    const combinedDescription = selectedRecs.map(r => `## ${r.mission_title}\n${r.mission_description || ''}`).join('\n\n');
    const maxCycles = Math.max(...selectedRecs.map(r => r.suggested_cycles || 3));

    body.innerHTML = `
        <div class="merge-preview">
            <h4>Merging ${selectedRecs.length} suggestions:</h4>
            <ul class="merge-source-list">
                ${selectedRecs.map(r => `<li>${escapeHtml(r.mission_title)}</li>`).join('')}
            </ul>
        </div>
        <div class="merge-form">
            <div class="form-group">
                <label>Combined Title:</label>
                <input type="text" id="merge-title" class="form-input" value="${escapeHtml(combinedTitle)}">
            </div>
            <div class="form-group">
                <label>Combined Description:</label>
                <textarea id="merge-description" class="form-textarea" rows="8">${escapeHtml(combinedDescription)}</textarea>
                <small class="form-hint">This description becomes your mission summary. The full text from all ${selectedRecs.length} sources will be preserved and used when the mission runs.</small>
            </div>
            <div class="form-group">
                <label>Rationale:</label>
                <textarea id="merge-rationale" class="form-textarea" rows="3">Merged from ${selectedRecs.length} similar suggestions for efficiency.</textarea>
            </div>
            <div class="form-group-row">
                <div class="form-group">
                    <label>Cycle Budget:</label>
                    <select id="merge-cycles" class="form-select">
                        <option value="1">1 cycle</option>
                        <option value="2">2 cycles</option>
                        <option value="3" ${maxCycles === 3 ? 'selected' : ''}>3 cycles</option>
                        <option value="5" ${maxCycles >= 5 ? 'selected' : ''}>5 cycles</option>
                        <option value="10" ${maxCycles >= 10 ? 'selected' : ''}>10 cycles</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" id="merge-delete-sources" checked>
                        Delete original suggestions after merge
                    </label>
                </div>
            </div>
        </div>
    `;

    modal.style.display = 'flex';
}

export function closeMergeModal() {
    const modal = document.getElementById('merge-modal');
    if (modal) modal.style.display = 'none';
    removeModalOpenClass();
}

export async function executeMerge() {
    const sourceIds = Array.from(selectedForMerge);
    const mergedData = {
        mission_title: document.getElementById('merge-title').value,
        mission_description: document.getElementById('merge-description').value,
        rationale: document.getElementById('merge-rationale').value,
        suggested_cycles: parseInt(document.getElementById('merge-cycles').value)
    };
    const deleteSources = document.getElementById('merge-delete-sources').checked;

    // Confirmation dialog before merging with delete sources option
    if (deleteSources) {
        const confirmed = confirm(
            `This will merge ${sourceIds.length} suggestions into one and DELETE the original ${sourceIds.length} suggestions.\n\n` +
            `Are you sure you want to proceed?`
        );
        if (!confirmed) return;
    }

    try {
        const result = await api('/api/recommendations/merge', 'POST', {
            source_ids: sourceIds,
            merged_data: mergedData,
            delete_sources: deleteSources
        });

        if (result.success) {
            showToast('Suggestions merged successfully');
            closeMergeModal();
            closeSimilarityModal();
            await loadRecommendations();
        } else {
            showToast('Merge failed: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error merging: ' + e.message, 'error');
    }
}

/**
 * Add a Mission Suggestion to the queue
 */
export async function queueMissionSuggestion() {
    if (!selectedRecId) return;

    const rec = recommendations.find(r => r.id === selectedRecId);
    if (!rec) {
        showToast('Recommendation not found', 'error');
        return;
    }

    const cycleBudget = parseInt(document.getElementById('rec-modal-cycles').value) || 3;

    try {
        const data = await api('/api/queue/add', 'POST', {
            problem_statement: rec.mission_description || rec.mission_title,
            cycle_budget: cycleBudget,
            priority: 0,
            source: 'recommendation'
        });

        if (data.status === 'added') {
            showToast(`Added to queue (position ${data.queue_length})`);
            closeRecModal();

            // Refresh queue widget if available
            if (typeof window.refreshQueueWidget === 'function') {
                window.refreshQueueWidget();
            }
        } else {
            showToast('Failed to add: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        console.error('Queue suggestion error:', e);
        showToast('Error: ' + e.message, 'error');
    }
}

// =============================================================================
// GLASSBOX MODAL
// =============================================================================

export function closeGlassboxModal() {
    document.getElementById('glassbox-modal').classList.remove('show');
    // Remove modal-open from body
    removeModalOpenClass();
}

// =============================================================================
// REPO LOG MODAL
// =============================================================================

export function closeRepoLogModal(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('repo-log-modal').style.display = 'none';
    // Remove modal-open from body
    removeModalOpenClass();
}

// =============================================================================
// SORTING FUNCTIONS
// =============================================================================

/**
 * Sort recommendations by selected field and re-render
 */
export function sortRecommendations() {
    const sortSelect = document.getElementById('rec-sort-select');
    if (sortSelect) {
        currentSortField = sortSelect.value;
    }
    applySortToRecommendations();
    renderRecommendations();
}

/**
 * Apply current sort order to recommendations array
 */
function applySortToRecommendations() {
    recommendations.sort((a, b) => {
        if (currentSortField === 'priority_score') {
            return (b.priority_score || 0) - (a.priority_score || 0);
        } else if (currentSortField === 'created_at') {
            return new Date(b.created_at || 0) - new Date(a.created_at || 0);
        } else if (currentSortField === 'health_status') {
            // Order: hot > needs_review > healthy > stale > orphaned
            const order = { hot: 0, needs_review: 1, healthy: 2, stale: 3, orphaned: 4 };
            return (order[a.health_status] || 5) - (order[b.health_status] || 5);
        }
        return 0;
    });
}

// =============================================================================
// HEALTH SUMMARY FUNCTIONS
// =============================================================================

/**
 * Update the health summary bar with counts from the health report
 */
function updateHealthSummaryBar() {
    const summaryEl = document.getElementById('rec-health-summary');
    if (!summaryEl) return;

    // Get health report from window or calculate from recommendations
    const healthReport = window._recHealthReport;
    if (healthReport && healthReport.counts) {
        const counts = healthReport.counts;
        summaryEl.style.display = counts.total > 0 ? 'flex' : 'none';

        const hotEl = document.getElementById('health-hot-count');
        const staleEl = document.getElementById('health-stale-count');
        const reviewEl = document.getElementById('health-review-count');
        const healthyEl = document.getElementById('health-healthy-count');

        if (hotEl) hotEl.textContent = counts.hot || 0;
        if (staleEl) staleEl.textContent = counts.stale || 0;
        if (reviewEl) reviewEl.textContent = counts.needs_review || 0;
        if (healthyEl) healthyEl.textContent = counts.healthy || 0;
    } else if (recommendations.length > 0) {
        // Calculate counts from recommendations directly
        const counts = { hot: 0, stale: 0, needs_review: 0, healthy: 0 };
        recommendations.forEach(rec => {
            const status = rec.health_status || 'healthy';
            if (counts.hasOwnProperty(status)) {
                counts[status]++;
            }
        });
        summaryEl.style.display = 'flex';

        const hotEl = document.getElementById('health-hot-count');
        const staleEl = document.getElementById('health-stale-count');
        const reviewEl = document.getElementById('health-review-count');
        const healthyEl = document.getElementById('health-healthy-count');

        if (hotEl) hotEl.textContent = counts.hot;
        if (staleEl) staleEl.textContent = counts.stale;
        if (reviewEl) reviewEl.textContent = counts.needs_review;
        if (healthyEl) healthyEl.textContent = counts.healthy;
    } else {
        summaryEl.style.display = 'none';
    }
}

/**
 * Load health summary from API (standalone call)
 */
export async function loadHealthSummary() {
    try {
        const data = await api('/api/recommendations/health-report');
        window._recHealthReport = data;
        updateHealthSummaryBar();
    } catch (e) {
        console.warn('Failed to load health summary:', e);
    }
}

// =============================================================================
// MERGE CANDIDATES AUTO-PROMPT FUNCTIONS
// =============================================================================

/**
 * Show merge candidates prompt after adding a new suggestion
 */
export function showMergeCandidatesPrompt(newRecId, candidateIds) {
    if (!candidateIds || candidateIds.length === 0) return;

    pendingMergeCandidates = candidateIds;
    newSuggestionId = newRecId;

    // Find the candidate recommendations
    const candidates = recommendations.filter(r => candidateIds.includes(r.id));
    const newRec = recommendations.find(r => r.id === newRecId);

    const body = document.getElementById('merge-candidates-body');
    if (!body) return;

    body.innerHTML = `
        <div class="rec-merge-prompt">
            <div class="rec-merge-prompt-title">Your new suggestion is similar to ${candidates.length} existing suggestion(s):</div>
            <ul class="rec-merge-prompt-list">
                ${candidates.map(c => `<li><strong>${escapeHtml(c.mission_title)}</strong></li>`).join('')}
            </ul>
            <p>Would you like to merge these into a single suggestion?</p>
        </div>
    `;

    // Save scroll position before showing modal
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;

    document.getElementById('merge-candidates-modal').style.display = 'flex';
    document.body.classList.add('modal-open');
}

/**
 * Close the merge candidates prompt modal
 */
export function closeMergeCandidatesModal() {
    const modal = document.getElementById('merge-candidates-modal');
    if (modal) modal.style.display = 'none';
    pendingMergeCandidates = [];
    newSuggestionId = null;
    removeModalOpenClass();
}

/**
 * Proceed from merge candidates prompt to actual merge modal
 */
export function proceedToMerge() {
    // Add both the new suggestion and candidates to selectedForMerge
    selectedForMerge.clear();
    if (newSuggestionId) selectedForMerge.add(newSuggestionId);
    pendingMergeCandidates.forEach(id => selectedForMerge.add(id));

    closeMergeCandidatesModal();
    openMergeModal();
}

/**
 * Add a new suggestion via API and check for merge candidates
 */
export async function addNewSuggestion(title, description = '') {
    try {
        const result = await api('/api/recommendations', 'POST', {
            mission_title: title,
            mission_description: description || title,
            suggested_cycles: 3
        });

        if (result.success) {
            showToast('Suggestion added');
            await loadRecommendations();

            // Check for merge candidates
            if (result.has_similar && result.merge_candidates && result.merge_candidates.length > 0) {
                showMergeCandidatesPrompt(result.recommendation.id, result.merge_candidates);
            }
        } else {
            showToast('Error: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error adding suggestion: ' + e.message, 'error');
    }
}

// =============================================================================
// FILTERING FUNCTIONS
// =============================================================================

/**
 * Filter recommendations by selected auto-tag
 */
export function filterByTag() {
    const select = document.getElementById('rec-tag-filter');
    currentTagFilter = select ? select.value : '';
    applyFilters();
}

/**
 * Filter recommendations by health status (clickable from health summary bar)
 */
export function filterByHealth(status) {
    // Toggle: click same status again to clear
    if (currentHealthFilter === status) {
        currentHealthFilter = '';
    } else {
        currentHealthFilter = status;
    }

    // Update visual active state
    document.querySelectorAll('.rec-health-stat').forEach(el => {
        el.classList.remove('active');
    });
    if (currentHealthFilter) {
        const activeEl = document.querySelector(`.rec-health-stat.${currentHealthFilter.replace('_', '-')}`);
        if (activeEl) activeEl.classList.add('active');
    }

    applyFilters();
}

/**
 * Clear all active filters
 */
export function clearAllFilters() {
    currentTagFilter = '';
    currentHealthFilter = '';

    const tagSelect = document.getElementById('rec-tag-filter');
    if (tagSelect) tagSelect.value = '';

    document.querySelectorAll('.rec-health-stat').forEach(el => {
        el.classList.remove('active');
    });

    currentPage = 1;
    applyFilters();
}

/**
 * Apply current filters to recommendations and re-render
 */
function applyFilters() {
    // Start with all recommendations
    let filtered = [...allRecommendations];

    // Apply tag filter
    if (currentTagFilter) {
        filtered = filtered.filter(r =>
            (r.auto_tags || []).includes(currentTagFilter)
        );
    }

    // Apply health filter
    if (currentHealthFilter) {
        filtered = filtered.filter(r =>
            r.health_status === currentHealthFilter
        );
    }

    recommendations = filtered;
    applySortToRecommendations();
    renderRecommendations();
    updateFilterIndicator();
    updatePaginationControls();
}

/**
 * Update the filter count indicator
 */
function updateFilterIndicator() {
    const count = allRecommendations.length - recommendations.length;
    const countEl = document.getElementById('rec-filter-count');
    if (countEl) {
        if (count > 0) {
            countEl.textContent = `(${recommendations.length} of ${allRecommendations.length} shown)`;
            countEl.style.display = 'inline';
        } else {
            countEl.textContent = '';
            countEl.style.display = 'none';
        }
    }
}

// =============================================================================
// PAGINATION FUNCTIONS
// =============================================================================

/**
 * Navigate to previous/next page of recommendations
 */
export function goToRecPage(direction) {
    const totalPages = Math.ceil(recommendations.length / itemsPerPage);

    if (direction === 'prev' && currentPage > 1) {
        currentPage--;
    } else if (direction === 'next' && currentPage < totalPages) {
        currentPage++;
    }

    renderRecommendations();
    updatePaginationControls();
}

/**
 * Update pagination controls visibility and state
 */
function updatePaginationControls() {
    const totalPages = Math.ceil(recommendations.length / itemsPerPage);
    const paginationEl = document.getElementById('rec-pagination');

    if (paginationEl) {
        paginationEl.style.display = totalPages > 1 ? 'flex' : 'none';
    }

    const pageInfo = document.getElementById('rec-page-info');
    if (pageInfo) {
        pageInfo.textContent = `Page ${currentPage} of ${Math.max(totalPages, 1)}`;
    }

    const prevBtn = document.getElementById('rec-prev-btn');
    const nextBtn = document.getElementById('rec-next-btn');
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
}

/**
 * Get paginated slice of recommendations for rendering
 */
function getPaginatedRecommendations() {
    const start = (currentPage - 1) * itemsPerPage;
    const end = start + itemsPerPage;
    return recommendations.slice(start, end);
}

// =============================================================================
// QUICK-ADD FUNCTIONS
// =============================================================================

/**
 * Submit quick-add form to create new suggestion
 */
export function submitQuickAdd() {
    const input = document.getElementById('rec-quick-add-title');
    if (!input || !input.value.trim()) {
        showToast('Please enter a suggestion title', 'error');
        return;
    }
    addNewSuggestion(input.value.trim());
    input.value = '';
}

/**
 * Refresh all tags by re-running auto-tagging on all suggestions
 */
export async function refreshAllTags() {
    try {
        showToast('Re-tagging all suggestions...');
        const result = await api('/api/recommendations/auto-tag', 'POST');
        showToast(`Tagged ${result.tagged_count || 0} suggestions`);
        await loadRecommendations();
    } catch (e) {
        showToast('Error refreshing tags: ' + e.message, 'error');
    }
}

// =============================================================================
// MODAL EVENT HANDLERS
// =============================================================================

// Close modal on click outside
document.addEventListener('click', function(e) {
    const modal = document.getElementById('rec-modal');
    if (e.target === modal) {
        closeRecModal();
    }
});

// Close similarity modal on click outside
document.addEventListener('click', function(e) {
    const simModal = document.getElementById('similarity-modal');
    if (e.target === simModal) {
        closeSimilarityModal();
    }
    const mergeModal = document.getElementById('merge-modal');
    if (e.target === mergeModal) {
        closeMergeModal();
    }
    const mergeCandidatesModal = document.getElementById('merge-candidates-modal');
    if (e.target === mergeCandidatesModal) {
        closeMergeCandidatesModal();
    }
});

// Export state getters
export function getRecommendations() {
    return recommendations;
}
