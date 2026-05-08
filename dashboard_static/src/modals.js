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
let mergeCandidatesCache = [];
let mergeInProgress = false;
let _openRecModalInFlight = false;
let _queueInProgress = false;
let _deleteInFlight = false;
let _setMissionInFlight = false;

// Store scroll position when modal opens (for mobile)
let savedScrollX = 0;
let savedScrollY = 0;

// Sort order state
let currentSortField = 'priority_score';
let currentSortDirection = 'desc'; // 'asc' or 'desc'

// Allowlist for health filter values — must match DB CHECK constraint in suggestion_storage.py:170
// Valid values: healthy, stale, orphaned, needs_review, hot (plus '' for "show all")
const _ALLOWED_HEALTH_FILTERS = new Set(['healthy', 'stale', 'orphaned', 'needs_review', 'hot', '']);
function _safeHealthFilter(value) {
    return _ALLOWED_HEALTH_FILTERS.has(value) ? value : '';
}

// localStorage key for sort/filter persistence
const REC_SORT_STORAGE_KEY = 'rec_sort_filter_state';
const REC_SORT_SCHEMA_VERSION = 2; // v2 adds healthFilter field

function saveRecSortState() {
    try {
        localStorage.setItem(REC_SORT_STORAGE_KEY, JSON.stringify({
            version: REC_SORT_SCHEMA_VERSION,
            sortField: currentSortField,
            sortDirection: currentSortDirection,
            tagFilter: currentTagFilter,
            healthFilter: currentHealthFilter
        }));
    } catch (e) {
        console.log('Could not save rec sort state:', e);
    }
}

function loadRecSortState() {
    try {
        const saved = localStorage.getItem(REC_SORT_STORAGE_KEY);
        if (!saved) return;
        const parsed = JSON.parse(saved);
        // Version check: discard stale schemas cleanly
        if (!parsed.version || parsed.version !== REC_SORT_SCHEMA_VERSION) {
            localStorage.removeItem(REC_SORT_STORAGE_KEY);
            return;
        }
        if (parsed.sortField) {
            const sortSelect = document.getElementById('rec-sort-select');
            if (sortSelect) {
                sortSelect.value = parsed.sortField;
                if (sortSelect.value === parsed.sortField) {
                    currentSortField = parsed.sortField;
                } else {
                    sortSelect.value = currentSortField;
                }
            } else {
                currentSortField = parsed.sortField;
            }
        }
        if (parsed.sortDirection && ['asc', 'desc'].includes(parsed.sortDirection)) {
            currentSortDirection = parsed.sortDirection;
            const btn = document.getElementById('rec-sort-dir-btn');
            if (btn) btn.textContent = currentSortDirection === 'desc' ? '\u2193 Desc' : '\u2191 Asc';
        }
        if (parsed.tagFilter !== undefined) {
            currentTagFilter = parsed.tagFilter;
            const tagSelect = document.getElementById('rec-tag-filter');
            if (tagSelect) tagSelect.value = currentTagFilter;
        }
        if (parsed.healthFilter !== undefined) {
            currentHealthFilter = _safeHealthFilter(parsed.healthFilter);
            if (currentHealthFilter) {
                const activeEl = document.querySelector(`.rec-health-stat.${CSS.escape(currentHealthFilter.replace('_', '-'))}`);
                if (activeEl) activeEl.classList.add('active');
            }
        }
    } catch (e) {
        console.log('Could not load rec sort state:', e);
    }
}

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
    const fullTextEl = document.getElementById('mission-full-text');
    if (fullTextEl) fullTextEl.textContent = fullMissionText;
    const missionModalEl = document.getElementById('mission-modal');
    if (!missionModalEl) return;
    missionModalEl.classList.add('show');
    // Add modal-open to body for mobile z-index fix
    // Save scroll position first
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;
    document.body.classList.add('modal-open');
}

export function closeMissionModal() {
    const modalEl = document.getElementById('mission-modal');
    if (modalEl) modalEl.classList.remove('show');
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
    console.log('[DEBUG] loadRecommendations called');
    // Use analyze endpoint to get auto-tagged, prioritized, health-checked suggestions
    try {
        const data = await api('/api/recommendations/analyze');
        console.log('[DEBUG] Got data from analyze:', data?.items?.length, 'items');
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
    console.log('[DEBUG] recommendations array length:', recommendations.length);
    // Store all recommendations for filtering
    allRecommendations = [...recommendations];
    // Reset pagination on reload
    currentPage = 1;
    // Restore persisted sort/filter before rendering
    loadRecSortState();
    // Apply current sort before rendering
    applySortToRecommendations();
    console.log('[DEBUG] About to call renderRecommendations');
    renderRecommendations();
    console.log('[DEBUG] About to call updateRecCount');
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
        const missionType = rec.mission_type || 'EXPANSION';
        const itemClass = isDriftHalt ? 'rec-item drift-halt'
            : (isMerged ? 'rec-item merged'
            : (missionType === 'BUGFIX' ? 'rec-item bugfix-mission'
            : (missionType === 'TECH_DEBT' ? 'rec-item tech-debt-mission'
            : 'rec-item')));
        const sourceBadge = isDriftHalt
            ? '<span class="rec-source-badge drift">From Drift</span>'
            : (rec.source_type === 'successful_completion'
                ? '<span class="rec-source-badge success">Follow-up</span>'
                : (isMerged
                    ? `<span class="rec-source-badge merged">Merged (${(rec.merged_from || []).length})</span>`
                    : ''));

        // Mission type badge (BUGFIX / TECH_DEBT / COMPLETION — no badge for EXPANSION/MANUAL)
        const missionTypeBadge = (() => {
            switch (missionType) {
                case 'BUGFIX':     return '<span class="mission-type-badge bugfix">[BUGFIX]</span>';
                case 'TECH_DEBT':  return '<span class="mission-type-badge tech-debt">[DEBT]</span>';
                case 'COMPLETION': return '<span class="mission-type-badge completion">[COMPLETE]</span>';
                default:           return '';
            }
        })();

        // Execution profile badge — visible indicator of which mission profile the
        // suggestion will run under (full_rd, plan_only, build_only, test_red_team,
        // bug_hunt, research_only, review_existing). Defaults to 'full_rd' for legacy
        // rows where the column is NULL.
        const profileBadge = (() => {
            const labels = {
                full_rd:        'Full R&D',
                plan_only:      'Plan Only',
                build_only:     'Build Only',
                test_red_team:  'Test/Red Team',
                bug_hunt:       'Bug Hunt',
                research_only:  'Research',
                review_existing:'Review',
            };
            // Use Object.prototype.hasOwnProperty.call to avoid prototype-pollution
            // lookups (e.g. labels["constructor"]) and to avoid the truthiness
            // fallback that would let data-profile carry an unrecognised key while
            // the visible label silently shifts to "Full R&D".
            const raw = rec.execution_profile;
            const profile = (typeof raw === 'string'
                && Object.prototype.hasOwnProperty.call(labels, raw))
                ? raw
                : 'full_rd';
            const label = labels[profile];
            return `<span class="rec-profile-badge" data-profile="${escapeHtml(profile)}">${escapeHtml(label)}</span>`;
        })();

        // Auto-tags badges — use data-tag attribute to avoid CSS class injection
        // (a tag containing spaces would inject extra CSS classes if used in class attr)
        const tagBadges = (rec.auto_tags || []).slice(0, 3).map(tag =>
            `<span class="rec-tag-badge" data-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</span>`
        ).join('');

        // Priority score indicator
        const priorityScore = rec.priority_score || 0;
        const priorityClass = priorityScore >= 70 ? 'high' : (priorityScore >= 40 ? 'medium' : 'low');
        const priorityBadge = priorityScore > 0
            ? `<span class="rec-priority-score ${priorityClass}"><span class="score-value">${Math.round(priorityScore)}</span></span>`
            : '';

        // Health status badge — use data-health attribute to avoid CSS class injection
        const healthStatus = rec.health_status || 'healthy';
        const healthBadge = healthStatus !== 'healthy'
            ? `<span class="rec-health-badge" data-health="${escapeHtml(healthStatus)}">${escapeHtml(healthStatus).replace('_', ' ')}</span>`
            : '';

        return `
            <div class="${itemClass}" data-rec-id="${escapeHtml(rec.id)}">
                <div class="rec-item-content">
                    <div class="rec-item-title">
                        ${missionTypeBadge}${profileBadge}${escapeHtml(rec.mission_title)}
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

    // Delegated click handler — avoids inline onclick JS injection vulnerability.
    // Guard against duplicate registration on repeated renders.
    if (!container._recClickAttached) {
        container._recClickAttached = true;
        container.addEventListener('click', function _recClickHandler(e) {
            const item = e.target.closest('[data-rec-id]');
            if (item) {
                window.openRecModal(item.dataset.recId);
            }
        });
    }
}

export async function openRecModal(recId) {
    if (_openRecModalInFlight) return;
    _openRecModalInFlight = true;
    try {
    const rec = recommendations.find(r => String(r.id) === String(recId));
    if (!rec) return;
    selectedRecId = recId;

    const isDriftHalt = rec.source_type === 'drift_halt';

    // Set modal title with source indicator
    const recTitleEl = document.getElementById('rec-modal-title');
    if (recTitleEl) recTitleEl.textContent =
        isDriftHalt ? 'Mission Suggestion (From Drift Analysis)' : 'Mission Recommendation';
    const recMissionTitleEl = document.getElementById('rec-modal-mission-title');
    if (recMissionTitleEl) recMissionTitleEl.textContent = rec.mission_title || 'Untitled';
    const recDescEl = document.getElementById('rec-modal-description');
    if (recDescEl) recDescEl.textContent = rec.mission_description || 'No description';
    const recRationaleEl = document.getElementById('rec-modal-rationale');
    if (recRationaleEl) recRationaleEl.textContent = rec.rationale || 'No rationale provided';
    const recSourceEl = document.getElementById('rec-modal-source');
    if (recSourceEl) recSourceEl.textContent = rec.source_mission_id
        ? `From: ${rec.source_mission_id}${rec.source_mission_summary ? ' - ' + rec.source_mission_summary.substring(0, 100) : ''}`
        : 'Manual recommendation';

    // Drift context display
    const driftContextEl = document.getElementById('rec-modal-drift-context');
    if (driftContextEl) {
        if (isDriftHalt && rec.drift_context) {
            const ctx = rec.drift_context;
            driftContextEl.style.display = 'block';
            // Build drift context using DOM APIs to avoid innerHTML with server-controlled data
            driftContextEl.textContent = '';
            const driftDiv = document.createElement('div');
            driftDiv.className = 'drift-context';

            const header = document.createElement('div');
            header.className = 'drift-context-header';
            header.textContent = 'Drift Analysis Details';
            driftDiv.appendChild(header);

            const metricsDiv = document.createElement('div');
            metricsDiv.className = 'drift-metrics';

            function _addMetric(label, value) {
                const metricDiv = document.createElement('div');
                metricDiv.className = 'drift-metric';
                const labelSpan = document.createElement('span');
                labelSpan.className = 'drift-metric-label';
                labelSpan.textContent = label;
                const valueSpan = document.createElement('span');
                valueSpan.className = 'drift-metric-value';
                valueSpan.textContent = value;
                metricDiv.appendChild(labelSpan);
                metricDiv.appendChild(valueSpan);
                metricsDiv.appendChild(metricDiv);
            }

            _addMetric('Failures:', String(ctx.drift_failures ?? 0));
            _addMetric('Similarity:', ((ctx.average_similarity || 0) * 100).toFixed(1) + '%');
            _addMetric('Halted at Cycle:', String(ctx.halted_at_cycle ?? 'N/A'));
            driftDiv.appendChild(metricsDiv);

            if (ctx.pattern_analysis) {
                // buildPatternAnalysisHTML uses escapeHtml internally and returns safe HTML
                const patternContainer = document.createElement('div');
                patternContainer.innerHTML = buildPatternAnalysisHTML(ctx.pattern_analysis);
                driftDiv.appendChild(patternContainer);
            }

            driftContextEl.appendChild(driftDiv);
        } else {
            driftContextEl.style.display = 'none';
            driftContextEl.textContent = '';
        }
    }

    const cyclesSelect = document.getElementById('rec-modal-cycles');
    const suggestedCycles = rec.suggested_cycles || 3;
    if (cyclesSelect) cyclesSelect.value = suggestedCycles;

    const missionCatSelect = document.getElementById('rec-modal-mission-type');
    if (missionCatSelect) {
        missionCatSelect.value = (rec.mission_type || 'EXPANSION').toUpperCase();
    }

    const typeSelect = document.getElementById('rec-modal-type');
    if (typeSelect) {
        const _MTYPE_PROFILE = { BUGFIX: 'bug_hunt', TECH_DEBT: 'build_only' };
        const storedProfile = rec.execution_profile || 'full_rd';
        const missionType = (rec.mission_type || '').toUpperCase();
        // If the stored profile is still the generic default, apply smart mapping from mission_type
        const effectiveProfile = (storedProfile === 'full_rd' && _MTYPE_PROFILE[missionType])
            ? _MTYPE_PROFILE[missionType]
            : storedProfile;
        typeSelect.value = effectiveProfile;
    }

    // Reset project name field and trigger async auto-suggestion
    const projectInput = document.getElementById('rec-project-name-input');
    if (projectInput) {
        projectInput.value = '';
        projectInput.placeholder = 'Auto-detecting...';
        projectInput.dataset.suggested = '';
        const missionText = rec.mission_description || rec.mission_title || '';
        if (missionText.length > 10) {
            api('/api/suggest-project-name', 'POST', { problem_statement: missionText })
                .then(result => {
                    if (result && result.suggested_name) {
                        projectInput.placeholder = result.suggested_name;
                        projectInput.dataset.suggested = result.suggested_name;
                    } else {
                        projectInput.placeholder = 'Enter project name (optional)';
                    }
                })
                .catch(() => { projectInput.placeholder = 'Enter project name (optional)'; });
        } else {
            projectInput.placeholder = 'Enter project name (optional)';
        }
    }

    // Save scroll position first before opening modal
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;

    const _recModalEl = document.getElementById('rec-modal');
    if (!_recModalEl) return;
    _recModalEl.style.display = 'flex';
    // Add modal-open to body for mobile z-index fix
    document.body.classList.add('modal-open');
    } finally {
        _openRecModalInFlight = false;
    }
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
            const count = parseInt(typeof item === 'object' ? (item.count || 1) : 1, 10) || 1;
            html += `<li>${escapeHtml(itemText)} (${count}x)</li>`;
        });
        html += '</ul></div>';
    }

    if (pattern.consistently_lost_focus && pattern.consistently_lost_focus.length > 0) {
        html += '<div class="pattern-section"><strong>Lost Focus On:</strong><ul>';
        pattern.consistently_lost_focus.slice(0, 3).forEach(item => {
            const itemText = typeof item === 'object' ? (item.item || JSON.stringify(item)) : item;
            const count = parseInt(typeof item === 'object' ? (item.count || 1) : 1, 10) || 1;
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
    // Reset edit mode state before closing so next open starts in view mode
    if (isEditMode) {
        isEditMode = false;
        editedRecData = null;
    }
    const recModalEl = document.getElementById('rec-modal');
    if (recModalEl) recModalEl.style.display = 'none';
    selectedRecId = null;
    // Remove modal-open from body
    removeModalOpenClass();
}

export async function deleteRecommendation() {
    if (_deleteInFlight) return;
    if (!selectedRecId) return;

    if (!confirm('Delete this recommendation?')) return;

    _deleteInFlight = true;
    try {
        await api('/api/recommendations/' + selectedRecId, 'DELETE');
        showToast('Recommendation deleted');
        closeRecModal();
        await loadRecommendations();
    } finally {
        _deleteInFlight = false;
    }
}

export async function setMissionFromRec() {
    if (_setMissionInFlight) return;
    if (!selectedRecId) return;

    _setMissionInFlight = true;
    try {
        const _cyclesEl = document.getElementById('rec-modal-cycles');
        const _rawCyclesS = _cyclesEl ? parseInt(_cyclesEl.value, 10) : NaN;
        if (isNaN(_rawCyclesS) || _rawCyclesS < 1) {
            showToast('Invalid cycle count — must be a number >= 1', 'error');
            return;
        }
        const cycleBudget = _rawCyclesS;

        const projectInputS = document.getElementById('rec-project-name-input');
        const projectNameS = projectInputS
            ? (projectInputS.value.trim() || projectInputS.dataset.suggested || '')
            : '';

        const typeSelectS = document.getElementById('rec-modal-type');
        const executionProfileS = typeSelectS ? typeSelectS.value : 'full_rd';
        const catSelectS = document.getElementById('rec-modal-mission-type');
        const missionCatS = catSelectS ? catSelectS.value : null;

        // Persist chosen execution_profile and mission_type back to the suggestion before set-mission deletes it
        try {
            const _putS = { execution_profile: executionProfileS };
            if (missionCatS) _putS.mission_type = missionCatS;
            await api('/api/recommendations/' + selectedRecId, 'PUT', _putS);
        } catch (_e) { /* non-blocking — main action proceeds regardless */ }

        const setPayload = { cycle_budget: cycleBudget, execution_profile: executionProfileS };
        if (projectNameS) setPayload.project_name = projectNameS;

        const data = await api('/api/recommendations/' + selectedRecId + '/set-mission', 'POST', setPayload);

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
    } finally {
        _setMissionInFlight = false;
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
    // Validate rec exists BEFORE mutating state — prevents isEditMode flip with no DOM update
    const rec = recommendations.find(r => String(r.id) === String(selectedRecId));
    if (!rec) return;
    isEditMode = !isEditMode;

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
        const recEditTitleEl = document.getElementById('rec-edit-title');
        if (recEditTitleEl) recEditTitleEl.value = editedRecData.mission_title;
        const recEditDescEl = document.getElementById('rec-edit-description');
        if (recEditDescEl) recEditDescEl.value = editedRecData.mission_description;
        const recEditRatEl = document.getElementById('rec-edit-rationale');
        if (recEditRatEl) recEditRatEl.value = editedRecData.rationale;
        const _cyclesEditEl = document.getElementById('rec-modal-cycles');
        if (_cyclesEditEl) _cyclesEditEl.value = editedRecData.suggested_cycles;

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
    const savedRecId = selectedRecId;  // capture before any await to avoid race with ESC close

    const _titleEl = document.getElementById('rec-edit-title');
    const _descEl = document.getElementById('rec-edit-description');
    const _ratEl = document.getElementById('rec-edit-rationale');
    if (!_titleEl || !_descEl || !_ratEl) return;

    const _typeEl = document.getElementById('rec-modal-type');
    const _catEl = document.getElementById('rec-modal-mission-type');
    const data = {
        mission_title: _titleEl.value,
        mission_description: _descEl.value,
        rationale: _ratEl.value,
        suggested_cycles: (() => { const _el = document.getElementById('rec-modal-cycles'); if (!_el) return null; const v = parseInt(_el.value, 10); return isNaN(v) ? null : v; })(),
        ...(_typeEl ? { execution_profile: _typeEl.value } : {}),
        ...(_catEl ? { mission_type: _catEl.value } : {})
    };

    try {
        const result = await api('/api/recommendations/' + savedRecId, 'PUT', data);

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
        // Use cancelEditMode() to properly reset DOM state (containers, buttons, editedRecData)
        cancelEditMode();
        await loadRecommendations();
        // Re-open modal with updated data using the captured ID (avoids ESC-then-reopen with null)
        openRecModal(savedRecId);
    } catch (e) {
        showToast('Error saving: ' + e.message, 'error');
    }
}

export function cancelEditMode() {
    if (isEditMode) {
        toggleEditMode();
    }
}

// =============================================================================
// MERGE PICKER FUNCTIONS
// =============================================================================

export async function openMergePicker() {
    const modal = document.getElementById('merge-picker-modal');
    const body = document.getElementById('merge-picker-body');

    if (!modal || !body) {
        showToast('Merge picker modal not found', 'error');
        return;
    }

    body.innerHTML = '<div class="loading-spinner">Loading suggestions...</div>';
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');

    try {
        const data = await api('/api/recommendations/merge-candidates');

        if (data.candidates && data.candidates.length >= 2) {
            mergeCandidatesCache = data.candidates;
            renderMergeCandidates(data.candidates);
        } else {
            body.innerHTML = `
                <div class="merge-picker-empty">
                    <p>Need at least 2 suggestions to merge.</p>
                    <p>Total suggestions: ${data.total || 0}</p>
                </div>
            `;
        }
    } catch (e) {
        body.innerHTML = '<div class="merge-picker-error">Error loading: ' + escapeHtml(e.message) + '</div>';
    }
}

function renderMergeCandidates(candidates) {
    const body = document.getElementById('merge-picker-body');
    selectedForMerge.clear();

    let html = `
        <div class="merge-picker-header">
            <div class="merge-picker-controls">
                <input type="text" id="merge-picker-search" class="merge-picker-search" placeholder="Filter by title...">
                <button class="btn btn-small" data-action="select-all">Select All</button>
                <button class="btn btn-small" data-action="deselect-all">Deselect All</button>
            </div>
            <p class="merge-picker-count">${candidates.length} suggestions available</p>
        </div>
        <div class="merge-picker-list">
    `;

    candidates.forEach(item => {
        const safeId = escapeHtml(String(item.id));
        const tags = (item.auto_tags || []).map(t => `<span class="rec-tag">${escapeHtml(t)}</span>`).join('');
        html += `
            <div class="merge-candidate-item" data-title="${escapeHtml(item.mission_title).toLowerCase()}" data-candidate-id="${safeId}">
                <label class="merge-candidate-checkbox">
                    <input type="checkbox" data-rec-id="${safeId}">
                </label>
                <div class="merge-candidate-content">
                    <div class="merge-candidate-title">${escapeHtml(item.mission_title)}</div>
                    <div class="merge-candidate-preview">${escapeHtml(item.mission_description || '')}</div>
                    <div class="merge-candidate-meta">
                        <span class="rec-cycles-badge">${item.suggested_cycles} cycles</span>
                        <span class="merge-candidate-priority">Priority: ${item.priority_score}</span>
                        ${tags}
                    </div>
                </div>
            </div>
        `;
    });

    html += `
        </div>
        <div class="merge-picker-actions">
            <span id="merge-selection-count">0 selected</span>
            <button class="btn primary" id="merge-selected-btn" data-action="merge-selected" disabled>Merge Selected</button>
        </div>
    `;

    body.innerHTML = html;

    // Attach delegated event listeners (replaces inline handlers to prevent XSS)
    const searchInput = body.querySelector('#merge-picker-search');
    if (searchInput) searchInput.addEventListener('input', e => filterMergeCandidates(e.target.value));

    body.addEventListener('click', function _mergePickerDelegate(e) {
        const action = e.target.closest('[data-action]');
        if (!action) return;
        const actionName = action.dataset.action;
        if (actionName === 'select-all') selectAllForMerge();
        else if (actionName === 'deselect-all') deselectAllForMerge();
        else if (actionName === 'merge-selected') openMergeModal();
    });

    body.addEventListener('change', function _mergeCheckboxDelegate(e) {
        const cb = e.target.closest('input[type="checkbox"][data-rec-id]');
        if (cb) toggleMergeSelection(cb.dataset.recId);
    });
}

export function closeMergePicker() {
    const modal = document.getElementById('merge-picker-modal');
    if (modal) modal.style.display = 'none';
    selectedForMerge.clear();
    mergeCandidatesCache = [];
    removeModalOpenClass();
}

export function selectAllForMerge() {
    const checkboxes = document.querySelectorAll('.merge-candidate-item:not([style*="display: none"]) input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = true;
        selectedForMerge.add(cb.dataset.recId);
    });
    updateMergeSelectionCount();
}

export function deselectAllForMerge() {
    const checkboxes = document.querySelectorAll('.merge-candidate-item input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = false;
    });
    selectedForMerge.clear();
    updateMergeSelectionCount();
}

export function filterMergeCandidates(query) {
    const items = document.querySelectorAll('.merge-candidate-item');
    const lowerQuery = query.toLowerCase();
    items.forEach(item => {
        const title = item.getAttribute('data-title') || '';
        const visible = title.includes(lowerQuery);
        item.style.display = visible ? '' : 'none';
        // Remove hidden items from selection to prevent invisible merges
        if (!visible) {
            const cb = item.querySelector('input[type="checkbox"]');
            if (cb) {
                cb.checked = false;
                selectedForMerge.delete(cb.dataset.recId);
            }
        }
    });
    updateMergeSelectionCount();
}

export function toggleMergeSelection(recId) {
    const key = String(recId);
    if (selectedForMerge.has(key)) {
        selectedForMerge.delete(key);
    } else {
        selectedForMerge.add(key);
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

    // Populate cache from recommendations if empty (e.g. called from filtered recommendations)
    if (mergeCandidatesCache.length === 0 && recommendations.length > 0) {
        mergeCandidatesCache = recommendations.filter(r => selectedForMerge.has(String(r.id)));
    }

    // Get selected recommendations from cached merge candidates (not filtered recommendations)
    const selectedRecs = mergeCandidatesCache.filter(r => selectedForMerge.has(String(r.id)));

    if (selectedRecs.length < 2) {
        showToast('Selected suggestions not found in cache. Please try again.', 'error');
        return;
    }

    // Generate combined title and description
    const combinedTitle = selectedRecs.map(r => r.mission_title).join(' + ');
    const combinedDescription = selectedRecs.map(r => `## ${r.mission_title}\n${r.mission_description || ''}`).join('\n\n');
    const maxCycles = selectedRecs.reduce((max, r) => Math.max(max, r.suggested_cycles || 3), 3);

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
                        ${(() => {
                            const cycleOptions = [1, 2, 3, 5, 10];
                            const bestCycle = cycleOptions.reduce((best, v) => v <= maxCycles ? v : best, 1);
                            return cycleOptions.map(v => `<option value="${v}"${bestCycle === v ? ' selected' : ''}>${v} cycle${v !== 1 ? 's' : ''}</option>`).join('');
                        })()}
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
    savedScrollX = window.scrollX || window.pageXOffset;
    savedScrollY = window.scrollY || window.pageYOffset;
    document.body.classList.add('modal-open');
}

export function closeMergeModal() {
    const modal = document.getElementById('merge-modal');
    if (modal) modal.style.display = 'none';
    mergeInProgress = false;
    const mergeBtn = document.querySelector('#merge-modal .btn.primary');
    if (mergeBtn) mergeBtn.disabled = false;
    removeModalOpenClass();
}

export async function executeMerge() {
    if (mergeInProgress) return;
    mergeInProgress = true;
    const mergeBtn = document.querySelector('#merge-modal .btn.primary');
    if (mergeBtn) mergeBtn.disabled = true;

    const sourceIds = Array.from(selectedForMerge);
    const cyclesEl = document.getElementById('merge-cycles');
    const parsedCycles = cyclesEl ? parseInt(cyclesEl.value, 10) : NaN;
    if (isNaN(parsedCycles) || parsedCycles < 1) {
        showToast('Please enter a valid cycle count (1 or more)', 'error');
        mergeInProgress = false;
        if (mergeBtn) mergeBtn.disabled = false;
        return;
    }
    const mergedData = {
        mission_title: document.getElementById('merge-title')?.value ?? '',
        mission_description: document.getElementById('merge-description')?.value ?? '',
        rationale: document.getElementById('merge-rationale')?.value ?? '',
        suggested_cycles: parsedCycles
    };
    const deleteSourcesEl = document.getElementById('merge-delete-sources');
    const deleteSources = deleteSourcesEl ? deleteSourcesEl.checked : false;

    // Confirmation dialog before merging with delete sources option
    if (deleteSources) {
        const confirmed = confirm(
            `This will merge ${sourceIds.length} suggestions into one and DELETE the original ${sourceIds.length} suggestions.\n\n` +
            `Are you sure you want to proceed?`
        );
        if (!confirmed) {
            mergeInProgress = false;
            if (mergeBtn) mergeBtn.disabled = false;
            return;
        }
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
            closeMergePicker();
            await loadRecommendations();
        } else {
            showToast('Merge failed: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error merging: ' + e.message, 'error');
    } finally {
        mergeInProgress = false;
        if (mergeBtn) mergeBtn.disabled = false;
    }
}

/**
 * Add a Mission Suggestion to the queue
 */
export async function queueMissionSuggestion() {
    if (_queueInProgress) return;
    _queueInProgress = true;
    try {
        if (!selectedRecId) return;

        const rec = recommendations.find(r => String(r.id) === String(selectedRecId));
        if (!rec) {
            showToast('Recommendation not found', 'error');
            return;
        }

        const recModalCyclesEl = document.getElementById('rec-modal-cycles');
        if (!recModalCyclesEl) {
            showToast('Cycles input not found', 'error');
            return;
        }
        const _rawCyclesQ = parseInt(recModalCyclesEl.value, 10);
        if (isNaN(_rawCyclesQ) || _rawCyclesQ < 1) {
            showToast('Invalid cycle count — must be a number >= 1', 'error');
            return;
        }
        const cycleBudget = _rawCyclesQ;

        const projectInputQ = document.getElementById('rec-project-name-input');
        const projectNameQ = projectInputQ
            ? (projectInputQ.value.trim() || projectInputQ.dataset.suggested || '')
            : '';

        const typeSelectQ = document.getElementById('rec-modal-type');
        const executionProfileQ = typeSelectQ ? typeSelectQ.value : 'full_rd';
        const catSelectQ = document.getElementById('rec-modal-mission-type');
        const missionCatQ = catSelectQ ? catSelectQ.value : null;

        // Persist chosen execution_profile and mission_type back to the suggestion record
        try {
            const _putPayload = { execution_profile: executionProfileQ };
            if (missionCatQ) _putPayload.mission_type = missionCatQ;
            await api('/api/recommendations/' + selectedRecId, 'PUT', _putPayload);
        } catch (_e) { /* non-blocking */ }

        const queuePayload = {
            problem_statement: rec.mission_description || rec.mission_title,
            cycle_budget: cycleBudget,
            priority: 0,
            source: 'recommendation',
            mission_type: executionProfileQ,
        };
        if (projectNameQ) queuePayload.project_name = projectNameQ;
        const data = await api('/api/queue/add', 'POST', queuePayload);

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
    } finally {
        _queueInProgress = false;
    }
}

// =============================================================================
// GLASSBOX MODAL
// =============================================================================

export function closeGlassboxModal() {
    const glassboxModalEl = document.getElementById('glassbox-modal');
    if (glassboxModalEl) glassboxModalEl.classList.remove('show');
    // Remove modal-open from body
    removeModalOpenClass();
}

// =============================================================================
// REPO LOG MODAL
// =============================================================================

export function closeRepoLogModal(event) {
    if (event && event.target !== event.currentTarget) return;
    const repoLogModalEl = document.getElementById('repo-log-modal');
    if (repoLogModalEl) repoLogModalEl.style.display = 'none';
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
    saveRecSortState();
    applySortToRecommendations();
    renderRecommendations();
}

/**
 * Toggle sort direction between ascending and descending
 */
export function toggleSortDirection() {
    currentSortDirection = currentSortDirection === 'desc' ? 'asc' : 'desc';
    const btn = document.getElementById('rec-sort-dir-btn');
    if (btn) {
        btn.textContent = currentSortDirection === 'desc' ? '\u2193 Desc' : '\u2191 Asc';
    }
    saveRecSortState();
    applySortToRecommendations();
    renderRecommendations();
}

/**
 * Apply current sort order to recommendations array
 */
function applySortToRecommendations() {
    const dir = currentSortDirection === 'asc' ? 1 : -1;
    recommendations.sort((a, b) => {
        if (currentSortField === 'priority_score') {
            return dir * ((a.priority_score || 0) - (b.priority_score || 0));
        } else if (currentSortField === 'created_at') {
            return dir * (new Date(a.created_at || 0) - new Date(b.created_at || 0));
        } else if (currentSortField === 'health_status') {
            // Order: hot > needs_review > healthy > stale > orphaned
            const order = { hot: 0, needs_review: 1, healthy: 2, stale: 3, orphaned: 4 };
            return dir * ((order[a.health_status] || 5) - (order[b.health_status] || 5));
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
    // API returns counts directly on health_report, not nested under .counts
    const counts = healthReport?.counts || healthReport;
    if (counts && (counts.total > 0 || counts.hot > 0 || counts.healthy > 0)) {
        summaryEl.style.display = 'flex';

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
    if (!newRecId || !candidateIds || candidateIds.length === 0) return;

    // Find the candidate recommendations
    const candidates = recommendations.filter(r => candidateIds.map(String).includes(String(r.id)));
    const newRec = recommendations.find(r => String(r.id) === String(newRecId));
    if (!newRec || candidates.length === 0) return;

    pendingMergeCandidates = candidateIds;
    newSuggestionId = newRecId;

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

    const mergeCandidatesModalEl = document.getElementById('merge-candidates-modal');
    if (mergeCandidatesModalEl) mergeCandidatesModalEl.style.display = 'flex';
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
    if (newSuggestionId) selectedForMerge.add(String(newSuggestionId));
    pendingMergeCandidates.forEach(id => selectedForMerge.add(String(id)));

    // Populate mergeCandidatesCache from recommendations so openMergeModal can find them.
    // In the auto-prompt flow (addNewSuggestion → showMergeCandidatesPrompt → proceedToMerge),
    // openMergePicker was never called, so the cache would be empty without this.
    const allIds = new Set([...selectedForMerge]);
    mergeCandidatesCache = recommendations.filter(r => allIds.has(String(r.id)));

    closeMergeCandidatesModal();
    openMergeModal();
}

/**
 * Add a new suggestion via API and check for merge candidates
 */
export async function addNewSuggestion(title, description = '', executionProfile = 'full_rd') {
    try {
        const result = await api('/api/recommendations', 'POST', {
            mission_title: title,
            mission_description: description || title,
            suggested_cycles: 3,
            execution_profile: executionProfile,
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
    saveRecSortState();
    applyFilters();
}

/**
 * Filter recommendations by health status (clickable from health summary bar)
 */
export function filterByHealth(status) {
    // Toggle: click same status again to clear; sanitize against allowlist
    const sanitizedStatus = _safeHealthFilter(status);
    if (currentHealthFilter === sanitizedStatus) {
        currentHealthFilter = '';
    } else {
        currentHealthFilter = sanitizedStatus;
    }

    // Update visual active state
    document.querySelectorAll('.rec-health-stat').forEach(el => {
        el.classList.remove('active');
    });
    const safeFilter = _safeHealthFilter(currentHealthFilter);
    if (safeFilter) {
        const activeEl = document.querySelector(`.rec-health-stat.${CSS.escape(safeFilter.replace('_', '-'))}`);
        if (activeEl) activeEl.classList.add('active');
    }

    saveRecSortState(); // persist health filter selection
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
    const typeSelect = document.getElementById('rec-quick-add-type');
    const executionProfile = typeSelect ? typeSelect.value : 'full_rd';
    addNewSuggestion(input.value.trim(), '', executionProfile);
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

// Close merge picker modal on click outside
document.addEventListener('click', function(e) {
    const pickerModal = document.getElementById('merge-picker-modal');
    if (e.target === pickerModal) {
        closeMergePicker();
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
