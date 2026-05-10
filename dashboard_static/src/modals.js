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
let mergeSortField = 'created_at';
let mergeSortDirection = 'desc';
let manageSuggestionsCache = [];
let selectedForManageDelete = new Set();
let manageDeleteInProgress = false;
let _openRecModalInFlight = false;
let _queueInProgress = false;
let _deleteInFlight = false;
let _setMissionInFlight = false;

function normalizeCycleBudget(value, fallback = 3) {
    const parsed = parseInt(value, 10);
    if (isNaN(parsed)) return fallback;
    return Math.max(1, Math.min(10, parsed));
}

function readCycleBudget(selectEl) {
    if (!selectEl) return null;
    const parsed = parseInt(selectEl.value, 10);
    if (isNaN(parsed) || parsed < 1) return null;
    return Math.min(10, parsed);
}

async function loadActiveMissionIntoControlPanel(seedMission = null) {
    const mission = seedMission || await api('/api/mission', 'GET');
    if (!mission || typeof mission !== 'object') return;

    const missionInput = document.getElementById('mission-input');
    if (missionInput) {
        missionInput.value = mission.problem_statement || mission.original_problem_statement || '';
        missionInput.dispatchEvent(new Event('input', { bubbles: true }));
    }

    const cycleSelect = document.getElementById('cycle-budget-input');
    if (cycleSelect && mission.cycle_budget !== undefined && mission.cycle_budget !== null) {
        const cycleValue = String(normalizeCycleBudget(mission.cycle_budget, 1));
        cycleSelect.value = cycleValue;
        if (cycleSelect.value !== cycleValue) {
            const option = document.createElement('option');
            option.value = cycleValue;
            option.textContent = cycleValue;
            cycleSelect.appendChild(option);
            cycleSelect.value = cycleValue;
        }
    }

    const maxIterationsInput = document.getElementById('max-iterations-input');
    if (maxIterationsInput && mission.max_iterations !== undefined && mission.max_iterations !== null) {
        maxIterationsInput.value = String(mission.max_iterations);
    }

    const projectInput = document.getElementById('project-name-input');
    if (projectInput) {
        const projectName = mission.project_name || '';
        projectInput.value = projectName;
        if (projectName) {
            projectInput.dataset.suggested = projectName;
            projectInput.placeholder = projectName;
        }
    }

    const typeSelect = document.getElementById('mission-type-select');
    if (typeSelect && mission.mission_type) {
        typeSelect.value = mission.mission_type;
    }
}

function clearMissionControlDraft() {
    const missionInput = document.getElementById('mission-input');
    if (missionInput) {
        missionInput.value = '';
        missionInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
}

function loadMissionIntoStatusPanel(mission) {
    if (!mission || typeof mission !== 'object') return;

    const problemStatement = mission.problem_statement || mission.original_problem_statement || 'No mission set';
    setFullMissionText(problemStatement);

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    setText('stat-stage', mission.current_stage || '-');
    setText('stat-project-name', mission.project_name || '-');
    setText('stat-mission-type', mission.mission_type_label || mission.mission_type || 'Full R&D');
    setText('stat-mission-cycle', `${mission.current_cycle || 1}/${mission.cycle_budget || 1}`);

    const missionEl = document.getElementById('current-mission');
    if (!missionEl) return;
    missionEl.textContent = '';

    const span = document.createElement('span');
    span.onclick = () => window.openMissionModal();
    span.style.cursor = 'pointer';
    span.title = 'Click to view full mission';
    span.textContent = problemStatement.length > 100
        ? problemStatement.substring(0, 100) + '...'
        : problemStatement;
    if (problemStatement.length > 100) {
        const expandSpan = document.createElement('span');
        expandSpan.style.color = 'var(--accent)';
        expandSpan.textContent = ' [expand]';
        span.appendChild(expandSpan);
    }
    missionEl.appendChild(span);
}

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
const REC_SORT_SCHEMA_VERSION = 4; // v4 adds project filter

function saveRecSortState() {
    try {
        localStorage.setItem(REC_SORT_STORAGE_KEY, JSON.stringify({
            version: REC_SORT_SCHEMA_VERSION,
            sortField: currentSortField,
            sortDirection: currentSortDirection,
            tagFilter: currentTagFilter,
            searchQuery: currentSearchQuery,
            projectFilter: currentProjectFilter
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
        if (parsed.searchQuery !== undefined && typeof parsed.searchQuery === 'string') {
            currentSearchQuery = parsed.searchQuery;
            const searchInput = document.getElementById('rec-search-filter');
            if (searchInput) searchInput.value = currentSearchQuery;
        }
        if (parsed.projectFilter !== undefined && typeof parsed.projectFilter === 'string') {
            currentProjectFilter = parsed.projectFilter;
            const projectSelect = document.getElementById('rec-project-filter');
            if (projectSelect) projectSelect.value = currentProjectFilter;
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
let currentSearchQuery = '';
let currentProjectFilter = '';
let allRecommendations = [];
let recommendationProjects = [];

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
    await loadRecommendationProjects();
    // Reset pagination on reload
    currentPage = 1;
    // Restore persisted sort/filter before rendering
    loadRecSortState();
    // Apply current filters and sort before rendering
    applyFilters();
    console.log('[DEBUG] About to call updateRecCount');
    updateRecCount();
    // Update health summary bar
    updateHealthSummaryBar();
}

function renderRecommendations() {
    const container = document.getElementById('legacy-recommendations-list');
    if (!container) return;

    if (recommendations.length === 0) {
        const hasFilters = Boolean(currentTagFilter || currentSearchQuery || currentHealthFilter || currentProjectFilter);
        container.innerHTML = hasFilters && allRecommendations.length > 0
            ? '<div class="rec-placeholder">No suggestions match the current filters.</div>'
            : '<div class="rec-placeholder">No suggestions yet. Complete a mission to get suggestions.</div>';
        return;
    }

    // Get paginated recommendations (only if > 25 items)
    const paginatedRecs = recommendations.length > itemsPerPage ? getPaginatedRecommendations() : recommendations;

    container.innerHTML = paginatedRecs.map(rec => {
        const isDriftHalt = rec.source_type === 'drift_halt';
        const isMerged = rec.source_type === 'merged';
        const classification = rec.classification || rec.mission_classification || 'EXPANSION';
        const itemClass = isDriftHalt ? 'rec-item drift-halt'
            : (isMerged ? 'rec-item merged'
            : (classification === 'BUGFIX' ? 'rec-item bugfix-mission'
            : (classification === 'TECH_DEBT' ? 'rec-item tech-debt-mission'
            : 'rec-item')));
        const sourceBadge = isDriftHalt
            ? '<span class="rec-source-badge drift">From Drift</span>'
            : (rec.source_type === 'successful_completion'
                ? '<span class="rec-source-badge success">Follow-up</span>'
                : (isMerged
                    ? `<span class="rec-source-badge merged">Merged (${(rec.merged_from || []).length})</span>`
                    : ''));

        // Classification badge (BUGFIX / TECH_DEBT / COMPLETION — no badge for EXPANSION/MANUAL)
        const missionTypeBadge = (() => {
            switch (classification) {
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
        const projectBadge = rec.project_name
            ? `<span class="rec-project-badge" data-project="${escapeHtml(rec.project_slug || '')}">${escapeHtml(rec.project_name)}</span>`
            : '';

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
                        ${missionTypeBadge}${profileBadge}${projectBadge}${escapeHtml(rec.mission_title)}
                        ${sourceBadge}
                        ${healthBadge}
                    </div>
                    <div class="rec-tags-container">${tagBadges}</div>
                    <div class="rec-item-preview">${escapeHtml((rec.mission_description || '').substring(0, 100))}${(rec.mission_description || '').length > 100 ? '...' : ''}</div>
                </div>
                <div class="rec-item-meta">
                    ${priorityBadge}
                    <span class="rec-cycles-badge">${escapeHtml(String(normalizeCycleBudget(rec.suggested_cycles)))} cycles</span>
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
    const suggestedCycles = normalizeCycleBudget(rec.suggested_cycles);
    if (cyclesSelect) cyclesSelect.value = suggestedCycles;

    const missionCatSelect = document.getElementById('rec-modal-mission-type');
    if (missionCatSelect) {
        missionCatSelect.value = (rec.classification || 'EXPANSION').toUpperCase();
    }

    const typeSelect = document.getElementById('rec-modal-type');
    if (typeSelect) {
        const _CLASSIFICATION_PROFILE = { BUGFIX: 'bug_hunt', TECH_DEBT: 'build_only' };
        const storedProfile = rec.execution_profile || rec.mission_type || 'full_rd';
        const classification = (rec.classification || '').toUpperCase();
        // If the stored profile is still the generic default, apply smart mapping from classification.
        const hasMappedProfile = Object.prototype.hasOwnProperty.call(_CLASSIFICATION_PROFILE, classification);
        const effectiveProfile = (storedProfile === 'full_rd' && hasMappedProfile)
            ? _CLASSIFICATION_PROFILE[classification]
            : storedProfile;
        typeSelect.value = effectiveProfile;
    }
    const buildGate = document.getElementById('rec-build-review-gate');
    const buildNotes = document.getElementById('rec-build-review-notes');
    const buildActionRadios = document.querySelectorAll('input[name="rec-build-review-action"]');
    buildActionRadios.forEach(radio => { radio.checked = false; });
    if (buildNotes) buildNotes.value = '';
    if (buildGate) {
        buildGate.style.display = rec.requires_user_build_approval ? 'flex' : 'none';
    }

    // Reset project name field and trigger async auto-suggestion
    const projectInput = document.getElementById('rec-project-name-input');
    if (projectInput) {
        projectInput.value = rec.project_name || '';
        projectInput.placeholder = rec.project_name || 'Auto-detecting...';
        projectInput.dataset.suggested = rec.project_name || '';
        const missionText = rec.mission_description || rec.mission_title || '';
        if (!rec.project_name && missionText.length > 10) {
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
            projectInput.placeholder = rec.project_name || 'Enter project name';
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
        const cycleBudget = readCycleBudget(_cyclesEl);
        if (cycleBudget === null) {
            showToast('Invalid cycle count — must be a number >= 1', 'error');
            return;
        }

        const projectInputS = document.getElementById('rec-project-name-input');
        const projectNameS = projectInputS
            ? (projectInputS.value.trim() || projectInputS.dataset.suggested || '')
            : '';

        const typeSelectS = document.getElementById('rec-modal-type');
        let executionProfileS = typeSelectS ? typeSelectS.value : 'full_rd';
        const catSelectS = document.getElementById('rec-modal-mission-type');
        const missionCatS = catSelectS ? catSelectS.value : null;
        const rec = recommendations.find(r => String(r.id) === String(selectedRecId));
        const requiresBuildReview = !!(rec && rec.requires_user_build_approval);
        let buildApprovalAction = null;
        let buildReviewNotes = '';
        if (requiresBuildReview) {
            const checkedAction = document.querySelector('input[name="rec-build-review-action"]:checked');
            buildApprovalAction = checkedAction ? checkedAction.value : null;
            if (!buildApprovalAction) {
                showToast('Choose Approve Build or Review / Modify Plan', 'error');
                return;
            }
            if (buildApprovalAction === 'review') {
                const notesEl = document.getElementById('rec-build-review-notes');
                buildReviewNotes = notesEl ? notesEl.value.trim() : '';
                if (!buildReviewNotes) {
                    showToast('Review / Modify Plan requires instructions', 'error');
                    return;
                }
                executionProfileS = 'plan_only';
            }
        }

        // Persist chosen execution_profile and classification back to the suggestion before set-mission queues it
        try {
            const _putS = { execution_profile: executionProfileS };
            if (missionCatS) _putS.classification = missionCatS;
            if (projectNameS) _putS.project_name = projectNameS;
            await api('/api/recommendations/' + selectedRecId, 'PUT', _putS);
        } catch (_e) { /* non-blocking — main action proceeds regardless */ }

        const setPayload = { cycle_budget: cycleBudget, execution_profile: executionProfileS };
        if (projectNameS) setPayload.project_name = projectNameS;
        if (requiresBuildReview) {
            setPayload.build_approval_action = buildApprovalAction;
            if (buildReviewNotes) setPayload.build_review_notes = buildReviewNotes;
        }

        const data = await api('/api/recommendations/' + selectedRecId + '/set-mission', 'POST', setPayload);

        if (data.success) {
            showToast(data.message);
            closeRecModal();
            clearMissionControlDraft();
            loadMissionIntoStatusPanel(data.mission);
            if (typeof window.refresh === 'function') {
                window.refresh({ forceStatus: true })
                    .catch(e => console.warn('Mission status refresh failed after set-mission:', e));
            }
            loadRecommendations()
                .catch(e => console.warn('Recommendation reload failed after set-mission:', e));
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
            suggested_cycles: normalizeCycleBudget(rec.suggested_cycles)
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
        suggested_cycles: (() => { const _el = document.getElementById('rec-modal-cycles'); return readCycleBudget(_el); })(),
        ...(_typeEl ? { execution_profile: _typeEl.value } : {}),
        ...(_catEl ? { classification: _catEl.value } : {})
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
            renderMergeCandidates(data.candidates, true);
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

function renderMergeCandidates(candidates, resetSelection = false) {
    const body = document.getElementById('merge-picker-body');
    if (resetSelection) selectedForMerge.clear();

    const sortedCandidates = getSortedMergeCandidates(candidates);

    let html = `
        <div class="merge-picker-header">
            <div class="merge-picker-controls">
                <input type="text" id="merge-picker-search" class="merge-picker-search" placeholder="Search title, date, tag, or type...">
                <label class="merge-picker-sort-label" for="merge-picker-sort">Sort:</label>
                <select id="merge-picker-sort" class="merge-picker-sort">
                    <option value="created_at"${mergeSortField === 'created_at' ? ' selected' : ''}>Date Created</option>
                    <option value="priority_score"${mergeSortField === 'priority_score' ? ' selected' : ''}>Priority</option>
                    <option value="mission_title"${mergeSortField === 'mission_title' ? ' selected' : ''}>Title</option>
                </select>
                <button class="btn btn-small" data-action="toggle-merge-sort">${mergeSortDirection === 'desc' ? '\u2193 Desc' : '\u2191 Asc'}</button>
                <button class="btn btn-small" data-action="select-all">Select All</button>
                <button class="btn btn-small" data-action="deselect-all">Deselect All</button>
            </div>
            <p class="merge-picker-count">${sortedCandidates.length} suggestions available</p>
        </div>
        <div class="merge-picker-list">
    `;

    sortedCandidates.forEach(item => {
        const safeId = escapeHtml(String(item.id));
        const tags = (item.auto_tags || []).map(t => `<span class="rec-tag">${escapeHtml(t)}</span>`).join('');
        const searchText = buildSuggestionSearchText(item);
        const createdAt = item.created_at ? formatDate(item.created_at) : 'No date';
        const project = item.project_name ? `<span>${escapeHtml(item.project_name)}</span>` : '';
        const checked = selectedForMerge.has(String(item.id)) ? ' checked' : '';
        html += `
            <div class="merge-candidate-item" data-search="${escapeHtml(searchText)}" data-candidate-id="${safeId}">
                <label class="merge-candidate-checkbox">
                    <input type="checkbox" data-rec-id="${safeId}"${checked}>
                </label>
                <div class="merge-candidate-content">
                    <div class="merge-candidate-title">${escapeHtml(item.mission_title)}</div>
                    <div class="merge-candidate-preview">${escapeHtml(item.mission_description || '')}</div>
                    <div class="merge-candidate-meta">
                        <span class="rec-cycles-badge">${escapeHtml(String(normalizeCycleBudget(item.suggested_cycles)))} cycles</span>
                        <span class="merge-candidate-priority">Priority: ${item.priority_score}</span>
                        ${project}
                        <span>${escapeHtml(createdAt)}</span>
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
    const sortSelect = body.querySelector('#merge-picker-sort');
    if (sortSelect) sortSelect.addEventListener('change', e => {
        mergeSortField = e.target.value;
        renderMergeCandidates(mergeCandidatesCache, false);
    });

    if (!body._mergePickerDelegatesAttached) {
        body._mergePickerDelegatesAttached = true;
        body.addEventListener('click', function _mergePickerDelegate(e) {
            const action = e.target.closest('[data-action]');
            if (!action) return;
            const actionName = action.dataset.action;
            if (actionName === 'select-all') selectAllForMerge();
            else if (actionName === 'deselect-all') deselectAllForMerge();
            else if (actionName === 'merge-selected') openMergeModal();
            else if (actionName === 'toggle-merge-sort') {
                mergeSortDirection = mergeSortDirection === 'desc' ? 'asc' : 'desc';
                renderMergeCandidates(mergeCandidatesCache, false);
            }
        });

        body.addEventListener('change', function _mergeCheckboxDelegate(e) {
            const cb = e.target.closest('input[type="checkbox"][data-rec-id]');
            if (cb) toggleMergeSelection(cb.dataset.recId);
        });
    }
    updateMergeSelectionCount();
}

function getSortedMergeCandidates(candidates) {
    const dir = mergeSortDirection === 'asc' ? 1 : -1;
    return [...candidates].sort((a, b) => {
        if (mergeSortField === 'created_at') {
            return dir * (new Date(a.created_at || 0) - new Date(b.created_at || 0));
        }
        if (mergeSortField === 'priority_score') {
            return dir * ((a.priority_score || 0) - (b.priority_score || 0));
        }
        const left = String(a.mission_title || '').toLowerCase();
        const right = String(b.mission_title || '').toLowerCase();
        return dir * left.localeCompare(right);
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
        const searchText = item.getAttribute('data-search') || '';
        const visible = searchText.includes(lowerQuery);
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
    const maxCycles = selectedRecs.reduce((max, r) => Math.max(max, normalizeCycleBudget(r.suggested_cycles)), 3);
    const projects = Array.from(new Set(selectedRecs.map(r => r.project_name).filter(Boolean)));
    const mergedProject = projects.length === 1 ? projects[0] : '';

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
            <div class="form-group">
                <label>Project:</label>
                <input type="text" id="merge-project" class="form-input" list="rec-project-list" value="${escapeHtml(mergedProject)}" placeholder="Select or enter project name">
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
    const parsedCycles = readCycleBudget(cyclesEl);
    if (parsedCycles === null) {
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
    const mergeProject = document.getElementById('merge-project')?.value?.trim() || '';
    if (mergeProject) mergedData.project_name = mergeProject;
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
        const cycleBudget = readCycleBudget(recModalCyclesEl);
        if (cycleBudget === null) {
            showToast('Invalid cycle count — must be a number >= 1', 'error');
            return;
        }

        const projectInputQ = document.getElementById('rec-project-name-input');
        const projectNameQ = projectInputQ
            ? (projectInputQ.value.trim() || projectInputQ.dataset.suggested || '')
            : '';

        const typeSelectQ = document.getElementById('rec-modal-type');
        const executionProfileQ = typeSelectQ ? typeSelectQ.value : 'full_rd';
        const catSelectQ = document.getElementById('rec-modal-mission-type');
        const missionCatQ = catSelectQ ? catSelectQ.value : null;

        // Persist chosen execution_profile and classification back to the suggestion record
        try {
            const _putPayload = { execution_profile: executionProfileQ };
            if (missionCatQ) _putPayload.classification = missionCatQ;
            if (projectNameQ) _putPayload.project_name = projectNameQ;
            await api('/api/recommendations/' + selectedRecId, 'PUT', _putPayload);
        } catch (_e) { /* non-blocking */ }

        const queuePayload = {
            problem_statement: rec.mission_description || rec.mission_title,
            cycle_budget: cycleBudget,
            priority: 0,
            source: 'recommendation',
            source_id: selectedRecId,
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

async function loadRecommendationProjects() {
    try {
        const data = await api('/api/recommendations/projects?status=open');
        recommendationProjects = data.items || [];
        populateProjectControls();
    } catch (e) {
        recommendationProjects = [];
        populateProjectControls();
    }
}

function populateProjectControls() {
    const projectSelect = document.getElementById('rec-project-filter');
    if (projectSelect) {
        const current = currentProjectFilter || projectSelect.value || '';
        projectSelect.innerHTML = '<option value="">All Projects</option>' + recommendationProjects.map(project => {
            const value = project.project_name || '';
            const count = project.count ? ` (${project.count})` : '';
            return `<option value="${escapeHtml(value)}">${escapeHtml(value + count)}</option>`;
        }).join('');
        projectSelect.value = current;
        if (projectSelect.value !== current) {
            projectSelect.value = '';
            currentProjectFilter = '';
        }
    }

    const datalist = document.getElementById('rec-project-list');
    if (datalist) {
        datalist.innerHTML = recommendationProjects
            .map(project => `<option value="${escapeHtml(project.project_name || '')}">`)
            .join('');
    }

    const quickDatalist = document.getElementById('rec-quick-add-project-list');
    if (quickDatalist) {
        quickDatalist.innerHTML = recommendationProjects
            .map(project => `<option value="${escapeHtml(project.project_name || '')}">`)
            .join('');
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
export async function addNewSuggestion(title, description = '', executionProfile = 'full_rd', projectName = '') {
    try {
        const payload = {
            mission_title: title,
            mission_description: description || title,
            suggested_cycles: 3,
            execution_profile: executionProfile,
        };
        if (projectName) payload.project_name = projectName;
        const result = await api('/api/recommendations', 'POST', payload);

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
 * Filter recommendations by free-text search across title, description, date, tags, and type fields.
 */
export function filterRecommendationsBySearch() {
    const input = document.getElementById('rec-search-filter');
    currentSearchQuery = input ? input.value.trim().toLowerCase() : '';
    currentPage = 1;
    saveRecSortState();
    applyFilters();
}

export function filterByProject() {
    const select = document.getElementById('rec-project-filter');
    currentProjectFilter = select ? select.value : '';
    currentPage = 1;
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
    currentSearchQuery = '';
    currentProjectFilter = '';

    const tagSelect = document.getElementById('rec-tag-filter');
    if (tagSelect) tagSelect.value = '';
    const searchInput = document.getElementById('rec-search-filter');
    if (searchInput) searchInput.value = '';
    const projectSelect = document.getElementById('rec-project-filter');
    if (projectSelect) projectSelect.value = '';

    document.querySelectorAll('.rec-health-stat').forEach(el => {
        el.classList.remove('active');
    });

    currentPage = 1;
    saveRecSortState();
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

    if (currentSearchQuery) {
        filtered = filtered.filter(r => buildSuggestionSearchText(r).includes(currentSearchQuery));
    }

    if (currentProjectFilter) {
        filtered = filtered.filter(r => r.project_name === currentProjectFilter);
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

function buildSuggestionSearchText(rec) {
    const tags = Array.isArray(rec.auto_tags) ? rec.auto_tags.join(' ') : '';
    const createdAt = rec.created_at || '';
    const formattedDate = createdAt ? formatDate(createdAt) : '';
    return [
        rec.mission_title,
        rec.mission_description,
        rec.rationale,
        rec.source_type,
        rec.classification,
        rec.mission_type,
        rec.execution_profile,
        rec.project_name,
        rec.project_slug,
        rec.project_source,
        rec.health_status,
        rec.suggested_cycles ? `${normalizeCycleBudget(rec.suggested_cycles)} cycles` : '',
        tags,
        createdAt,
        formattedDate,
    ].filter(Boolean).join(' ').toLowerCase();
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
// MANAGE SUGGESTIONS FUNCTIONS
// =============================================================================

export async function openManageSuggestions() {
    const modal = document.getElementById('manage-suggestions-modal');
    const body = document.getElementById('manage-suggestions-body');

    if (!modal || !body) {
        showToast('Manage suggestions modal not found', 'error');
        return;
    }

    body.innerHTML = '<div class="loading-spinner">Loading suggestions...</div>';
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');

    try {
        if (allRecommendations.length === 0) {
            const data = await api('/api/recommendations/analyze');
            allRecommendations = data.items || [];
        }
        manageSuggestionsCache = [...allRecommendations];
        selectedForManageDelete.clear();
        renderManageSuggestions();
    } catch (e) {
        body.innerHTML = '<div class="merge-picker-error">Error loading: ' + escapeHtml(e.message) + '</div>';
    }
}

export function closeManageSuggestions() {
    const modal = document.getElementById('manage-suggestions-modal');
    if (modal) modal.style.display = 'none';
    selectedForManageDelete.clear();
    manageSuggestionsCache = [];
    removeModalOpenClass();
}

function renderManageSuggestions() {
    const body = document.getElementById('manage-suggestions-body');
    if (!body) return;

    const sorted = [...manageSuggestionsCache].sort((a, b) =>
        new Date(b.created_at || 0) - new Date(a.created_at || 0)
    );

    if (sorted.length === 0) {
        body.innerHTML = '<div class="merge-picker-empty">No suggestions to manage.</div>';
        return;
    }

    body.innerHTML = `
        <div class="manage-suggestions-header">
            <div class="merge-picker-controls">
                <input type="text" id="manage-suggestions-search" class="merge-picker-search" placeholder="Search title, date, tag, or type...">
                <button class="btn btn-small" data-action="manage-select-all">Select All</button>
                <button class="btn btn-small" data-action="manage-deselect-all">Deselect All</button>
                <button class="btn btn-small danger" id="manage-delete-selected-btn" data-action="manage-delete-selected" disabled>Delete Selected</button>
            </div>
            <p class="merge-picker-count"><span id="manage-suggestions-count">${sorted.length}</span> suggestions</p>
        </div>
        <div class="manage-suggestions-list">
            ${sorted.map(item => renderManageSuggestionItem(item)).join('')}
        </div>
    `;

    const searchInput = body.querySelector('#manage-suggestions-search');
    if (searchInput) searchInput.addEventListener('input', e => filterManageSuggestions(e.target.value));

    if (!body._manageSuggestionsDelegatesAttached) {
        body._manageSuggestionsDelegatesAttached = true;
        body.addEventListener('click', e => {
            const action = e.target.closest('[data-action]');
            if (!action) return;
            const actionName = action.dataset.action;
            if (actionName === 'manage-select-all') selectAllForManageDelete();
            else if (actionName === 'manage-deselect-all') deselectAllForManageDelete();
            else if (actionName === 'manage-delete-selected') deleteSelectedManagedSuggestions();
            else if (actionName === 'manage-delete-one') deleteManagedSuggestion(action.dataset.recId);
        });

        body.addEventListener('change', e => {
            const cb = e.target.closest('input[type="checkbox"][data-manage-rec-id]');
            if (!cb) return;
            const id = cb.dataset.manageRecId;
            if (cb.checked) selectedForManageDelete.add(id);
            else selectedForManageDelete.delete(id);
            updateManageDeleteCount();
        });
    }

    updateManageDeleteCount();
}

function renderManageSuggestionItem(item) {
    const safeId = escapeHtml(String(item.id));
    const tags = (item.auto_tags || []).slice(0, 4).map(t => `<span class="rec-tag">${escapeHtml(t)}</span>`).join('');
    const createdAt = item.created_at ? formatDate(item.created_at) : 'No date';
    const project = item.project_name ? `<span>${escapeHtml(item.project_name)}</span>` : '';
    const checked = selectedForManageDelete.has(String(item.id)) ? ' checked' : '';
    return `
        <div class="manage-suggestion-item" data-search="${escapeHtml(buildSuggestionSearchText(item))}" data-manage-id="${safeId}">
            <label class="merge-candidate-checkbox">
                <input type="checkbox" data-manage-rec-id="${safeId}"${checked}>
            </label>
            <div class="merge-candidate-content">
                <div class="merge-candidate-title">${escapeHtml(item.mission_title || 'Untitled')}</div>
                <div class="merge-candidate-preview">${escapeHtml(item.mission_description || '')}</div>
                <div class="merge-candidate-meta">
                    <span>${escapeHtml(createdAt)}</span>
                    ${project}
                    <span>${escapeHtml(item.execution_profile || item.mission_type || item.source_type || 'suggestion')}</span>
                    ${tags}
                </div>
            </div>
            <button class="btn btn-small danger manage-delete-one" data-action="manage-delete-one" data-rec-id="${safeId}">Delete</button>
        </div>
    `;
}

export function filterManageSuggestions(query) {
    const lowerQuery = query.trim().toLowerCase();
    let visibleCount = 0;
    document.querySelectorAll('.manage-suggestion-item').forEach(item => {
        const searchText = item.getAttribute('data-search') || '';
        const visible = searchText.includes(lowerQuery);
        item.style.display = visible ? '' : 'none';
        if (visible) visibleCount++;
    });
    updateManageDeleteCount(visibleCount);
}

function selectAllForManageDelete() {
    document.querySelectorAll('.manage-suggestion-item:not([style*="display: none"]) input[type="checkbox"][data-manage-rec-id]').forEach(cb => {
        cb.checked = true;
        selectedForManageDelete.add(cb.dataset.manageRecId);
    });
    updateManageDeleteCount();
}

function deselectAllForManageDelete() {
    document.querySelectorAll('input[type="checkbox"][data-manage-rec-id]').forEach(cb => {
        cb.checked = false;
    });
    selectedForManageDelete.clear();
    updateManageDeleteCount();
}

function updateManageDeleteCount(visibleCount = null) {
    const btn = document.getElementById('manage-delete-selected-btn');
    if (btn) {
        const count = selectedForManageDelete.size;
        btn.textContent = count > 0 ? `Delete Selected (${count})` : 'Delete Selected';
        btn.disabled = count === 0 || manageDeleteInProgress;
    }
    const countEl = document.getElementById('manage-suggestions-count');
    if (countEl) {
        countEl.textContent = visibleCount === null
            ? String(manageSuggestionsCache.length)
            : `${visibleCount} of ${manageSuggestionsCache.length}`;
    }
}

async function deleteManagedSuggestion(recId) {
    if (!recId || manageDeleteInProgress) return;
    manageDeleteInProgress = true;
    updateManageDeleteCount();
    try {
        const result = await api('/api/recommendations/' + encodeURIComponent(recId), 'DELETE');
        if (result.success === false) {
            showToast(result.error || 'Delete failed', 'error');
            return;
        }
        removeSuggestionFromLocalCaches(recId);
        selectedForManageDelete.delete(String(recId));
        renderManageSuggestions();
        applyFilters();
        updateRecCount();
        showToast('Suggestion deleted');
    } catch (e) {
        showToast('Error deleting: ' + e.message, 'error');
    } finally {
        manageDeleteInProgress = false;
        updateManageDeleteCount();
    }
}

async function deleteSelectedManagedSuggestions() {
    if (selectedForManageDelete.size === 0 || manageDeleteInProgress) return;
    const ids = Array.from(selectedForManageDelete);
    if (!confirm(`Delete ${ids.length} selected suggestion${ids.length === 1 ? '' : 's'}?`)) return;

    manageDeleteInProgress = true;
    updateManageDeleteCount();
    try {
        let deleted = 0;
        for (const id of ids) {
            const result = await api('/api/recommendations/' + encodeURIComponent(id), 'DELETE');
            if (result.success !== false) {
                removeSuggestionFromLocalCaches(id);
                deleted++;
            }
        }
        selectedForManageDelete.clear();
        renderManageSuggestions();
        applyFilters();
        updateRecCount();
        showToast(`Deleted ${deleted} suggestion${deleted === 1 ? '' : 's'}`);
    } catch (e) {
        showToast('Error deleting suggestions: ' + e.message, 'error');
    } finally {
        manageDeleteInProgress = false;
        updateManageDeleteCount();
    }
}

function removeSuggestionFromLocalCaches(recId) {
    const id = String(recId);
    allRecommendations = allRecommendations.filter(r => String(r.id) !== id);
    recommendations = recommendations.filter(r => String(r.id) !== id);
    manageSuggestionsCache = manageSuggestionsCache.filter(r => String(r.id) !== id);
    mergeCandidatesCache = mergeCandidatesCache.filter(r => String(r.id) !== id);
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
    const projectInput = document.getElementById('rec-quick-add-project');
    const projectName = projectInput ? projectInput.value.trim() : '';
    addNewSuggestion(input.value.trim(), '', executionProfile, projectName);
    input.value = '';
    if (projectInput) projectInput.value = '';
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
