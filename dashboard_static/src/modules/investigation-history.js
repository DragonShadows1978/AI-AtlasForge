/**
 * Investigation History Module
 *
 * Provides UI for browsing, searching, filtering, and managing past investigations.
 * Features:
 * - Card-based display of investigations
 * - Search by query text
 * - Filter by status, date range, tags
 * - Sort by timestamp, duration, subagent count
 * - Tag management
 * - Report viewing with markdown rendering
 * - Export as markdown/JSON
 * - Re-run investigations
 */

import { api } from '../api.js';
import { escapeHtml, showToast } from '../core.js';

// State
let investigations = [];
let allTags = [];
let currentOffset = 0;
let currentLimit = 20;
let totalCount = 0;
let selectedTags = [];
let currentInvestigationId = null;
let searchDebounceTimer = null;

// Selection mode state
let selectedInvestigations = new Set();
let selectionModeActive = false;

// Saved searches state
let savedSearches = [];

// Tag colors for consistent coloring based on tag name hash
const TAG_COLORS = [
    { bg: 'rgba(88, 166, 255, 0.2)', border: '#58a6ff', text: '#58a6ff' },   // Blue
    { bg: 'rgba(63, 185, 80, 0.2)', border: '#3fb950', text: '#3fb950' },    // Green
    { bg: 'rgba(210, 153, 34, 0.2)', border: '#d29922', text: '#d29922' },   // Yellow
    { bg: 'rgba(248, 81, 73, 0.2)', border: '#f85149', text: '#f85149' },    // Red
    { bg: 'rgba(188, 140, 255, 0.2)', border: '#bc8cff', text: '#bc8cff' },  // Purple
    { bg: 'rgba(255, 166, 87, 0.2)', border: '#ffa657', text: '#ffa657' },   // Orange
    { bg: 'rgba(121, 192, 255, 0.2)', border: '#79c0ff', text: '#79c0ff' },  // Light Blue
    { bg: 'rgba(255, 123, 114, 0.2)', border: '#ff7b72', text: '#ff7b72' }   // Coral
];

/**
 * Get consistent color for a tag based on its name hash
 */
function getTagColor(tag) {
    let hash = 0;
    for (let i = 0; i < tag.length; i++) {
        hash = ((hash << 5) - hash) + tag.charCodeAt(i);
        hash = hash & hash;
    }
    return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

/**
 * Render a tag with consistent color
 */
function renderColoredTag(tag, showRemove = false, onRemove = null) {
    const color = getTagColor(tag);
    const removeSpan = showRemove
        ? `<span class="remove" onclick="event.stopPropagation(); ${onRemove}">&times;</span>`
        : '';
    return `<span class="inv-tag" style="background: ${color.bg}; border-color: ${color.border}; color: ${color.text}">${escapeHtml(tag)}${removeSpan}</span>`;
}

/**
 * Highlight search term in text (XSS-safe)
 */
function highlightMatch(text, searchTerm) {
    if (!searchTerm || !text) return escapeHtml(text);

    // Escape special regex characters
    const escapedTerm = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escapedTerm})`, 'gi');

    // Escape HTML first, then apply highlighting
    const escaped = escapeHtml(text);
    return escaped.replace(regex, '<mark style="background: var(--yellow); color: var(--bg); padding: 0 2px; border-radius: 2px;">$1</mark>');
}

// ============================================================================
// Initialization
// ============================================================================

/**
 * Initialize the investigation history tab.
 * Called when the tab is first shown.
 */
export async function initInvestigationHistory() {
    await Promise.all([
        loadInvestigationStats(),
        loadAllTags(),
        loadSavedSearches(),
        loadInvestigations()
    ]);

    // Set up keyboard shortcuts
    setupInvestigationKeyboardShortcuts();
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * Load investigation statistics
 */
async function loadInvestigationStats() {
    try {
        const data = await api('/api/investigation/history/stats');
        if (data.error) {
            console.error('Failed to load investigation stats:', data.error);
            return;
        }

        // Update header stats
        const totalEl = document.getElementById('inv-total-count');
        const completedEl = document.getElementById('inv-completed-count');
        const failedEl = document.getElementById('inv-failed-count');
        const successRateEl = document.getElementById('inv-success-rate');

        if (totalEl) totalEl.textContent = data.total_investigations || 0;
        if (completedEl) completedEl.textContent = data.completed_count || 0;
        if (failedEl) failedEl.textContent = data.failed_count || 0;
        if (successRateEl) successRateEl.textContent = `${data.success_rate || 0}%`;
    } catch (e) {
        console.error('Error loading investigation stats:', e);
    }
}

/**
 * Load all available tags for filtering
 */
async function loadAllTags() {
    try {
        const data = await api('/api/investigation/tags');
        if (data.error) {
            console.error('Failed to load tags:', data.error);
            return;
        }

        allTags = data.tags || [];
        renderTagsBar();
    } catch (e) {
        console.error('Error loading tags:', e);
    }
}

/**
 * Load investigations with current filters
 */
async function loadInvestigations(append = false) {
    const grid = document.getElementById('inv-cards-grid');
    if (!append && grid) {
        grid.innerHTML = '<div style="color: var(--text-dim); grid-column: 1 / -1; text-align: center; padding: 40px;">Loading...</div>';
    }

    try {
        // Build query params
        const params = new URLSearchParams();
        params.set('limit', currentLimit);
        params.set('offset', append ? currentOffset : 0);

        const searchInput = document.getElementById('inv-search-input');
        if (searchInput && searchInput.value.trim()) {
            params.set('search', searchInput.value.trim());
        }

        const statusFilter = document.getElementById('inv-status-filter');
        if (statusFilter && statusFilter.value) {
            params.set('status', statusFilter.value);
        }

        const sortBy = document.getElementById('inv-sort-by');
        if (sortBy && sortBy.value) {
            params.set('sort_by', sortBy.value);
        }

        const sortOrder = document.getElementById('inv-sort-order');
        if (sortOrder && sortOrder.value) {
            params.set('sort_order', sortOrder.value);
        }

        if (selectedTags.length > 0) {
            params.set('tags', selectedTags.join(','));
        }

        // Date range filter
        const dateFrom = document.getElementById('inv-date-from');
        if (dateFrom && dateFrom.value) {
            params.set('date_from', dateFrom.value);
        }

        const dateTo = document.getElementById('inv-date-to');
        if (dateTo && dateTo.value) {
            params.set('date_to', dateTo.value);
        }

        // Content search toggle
        const searchContent = document.getElementById('inv-search-content');
        if (searchContent && searchContent.checked) {
            params.set('search_content', 'true');
        }

        const data = await api(`/api/investigation/history?${params.toString()}`);
        if (data.error) {
            showError('Failed to load investigations: ' + data.error);
            return;
        }

        if (append) {
            investigations = [...investigations, ...(data.investigations || [])];
        } else {
            investigations = data.investigations || [];
        }

        totalCount = data.total || 0;
        currentOffset = data.offset || 0;

        renderInvestigationCards();
        updatePagination(data.has_more);
    } catch (e) {
        console.error('Error loading investigations:', e);
        showError('Failed to load investigations');
    }
}

// ============================================================================
// Rendering Functions
// ============================================================================

/**
 * Render the tags filter bar with colored tags
 */
function renderTagsBar() {
    const container = document.getElementById('inv-tags-list');
    if (!container) return;

    if (allTags.length === 0) {
        container.innerHTML = '<span style="color: var(--text-dim); font-size: 0.85em;">No tags yet</span>';
        return;
    }

    container.innerHTML = allTags.map(t => {
        const isActive = selectedTags.includes(t.tag);
        const color = getTagColor(t.tag);
        const activeStyle = isActive
            ? `background: ${color.border}; color: var(--bg); border-color: ${color.border};`
            : `background: ${color.bg}; border-color: ${color.border}; color: ${color.text};`;
        return `
            <span class="inv-filter-tag ${isActive ? 'active' : ''}"
                  style="${activeStyle}"
                  onclick="toggleTagFilter('${escapeHtml(t.tag)}')"
                  title="${t.count} investigations">
                ${escapeHtml(t.tag)} (${t.count})
            </span>
        `;
    }).join('');
}

/**
 * Render investigation cards
 */
function renderInvestigationCards() {
    const grid = document.getElementById('inv-cards-grid');
    if (!grid) return;

    if (investigations.length === 0) {
        grid.innerHTML = `
            <div class="inv-empty-state">
                <div class="icon">🔍</div>
                <p>No investigations found</p>
                <button class="btn primary" onclick="switchTab('atlasforge'); document.getElementById('investigation-mode-checkbox').checked = true; toggleInvestigationMode();">
                    Start an Investigation
                </button>
            </div>
        `;
        return;
    }

    grid.innerHTML = investigations.map(inv => renderCard(inv)).join('');
}

/**
 * Render a single investigation card with search highlighting, colored tags, and selection
 */
function renderCard(inv) {
    const statusClass = inv.status || 'pending';
    const statusLabel = statusClass.charAt(0).toUpperCase() + statusClass.slice(1);

    const tags = (inv.tags || []).slice(0, 3);
    const hasMoreTags = (inv.tags || []).length > 3;

    // Get current search term for highlighting
    const searchInput = document.getElementById('inv-search-input');
    const searchTerm = searchInput ? searchInput.value.trim() : '';

    // Apply search highlighting to query
    const queryText = inv.query_truncated || inv.query || 'No query';
    const queryDisplay = highlightMatch(queryText, searchTerm);

    // Check if this card is selected
    const isSelected = selectedInvestigations.has(inv.investigation_id);
    const selectedClass = isSelected ? 'inv-card-selected' : '';
    const selectedStyle = isSelected ? 'border-color: var(--accent); background: rgba(88, 166, 255, 0.1);' : '';

    // Selection checkbox (only show in selection mode)
    const checkbox = selectionModeActive ? `
        <div class="inv-card-checkbox" onclick="toggleInvestigationSelection('${inv.investigation_id}', event)">
            <input type="checkbox" ${isSelected ? 'checked' : ''} style="cursor: pointer;">
        </div>
    ` : '';

    // Attachment indicator
    const attachmentBadge = inv.has_attachments ? `
        <span class="inv-attachment-badge" title="${inv.attachment_count} file(s) attached">
            <span class="icon">📎</span>
            <span>${inv.attachment_count}</span>
        </span>
    ` : '';

    return `
        <div class="inv-card ${selectedClass}" style="${selectedStyle}"
             onclick="${selectionModeActive ? `toggleInvestigationSelection('${inv.investigation_id}', event)` : `showInvestigationDetail('${inv.investigation_id}')`}"
             data-investigation-id="${inv.investigation_id}">
            ${checkbox}
            <div class="inv-card-actions" onclick="event.stopPropagation()">
                <button class="btn" onclick="openTagModal('${inv.investigation_id}')" title="Edit tags">🏷</button>
                <button class="btn primary" onclick="rerunInvestigation('${inv.investigation_id}')" title="Re-run">↻</button>
            </div>
            <div class="inv-card-header">
                <span class="inv-card-status ${statusClass}">${statusLabel}</span>
                ${attachmentBadge}
                <span class="inv-card-id">${inv.investigation_id}</span>
            </div>
            <div class="inv-card-query">${queryDisplay}</div>
            <div class="inv-card-meta">
                <div class="inv-card-meta-item">
                    <span class="icon">📅</span>
                    <span>${inv.timestamp_relative || inv.timestamp_display || '-'}</span>
                </div>
                <div class="inv-card-meta-item">
                    <span class="icon">⏱</span>
                    <span>${inv.elapsed_display || '-'}</span>
                </div>
                <div class="inv-card-meta-item">
                    <span class="icon">🤖</span>
                    <span>${inv.subagent_count || 0} agents</span>
                </div>
                <div class="inv-card-meta-item">
                    <span class="icon">📍</span>
                    <span>${inv.timestamp_display || '-'}</span>
                </div>
            </div>
            ${tags.length > 0 ? `
            <div class="inv-card-tags">
                ${tags.map(t => renderColoredTag(t)).join('')}
                ${hasMoreTags ? `<span class="inv-tag" style="background: var(--bg);">+${inv.tags.length - 3}</span>` : ''}
            </div>
            ` : ''}
        </div>
    `;
}

// Intersection Observer for infinite scroll
let infiniteScrollObserver = null;

/**
 * Update pagination controls and set up infinite scroll
 */
function updatePagination(hasMore) {
    const paginationDiv = document.getElementById('inv-pagination');
    const infoSpan = document.getElementById('inv-pagination-info');
    const loadMoreBtn = document.getElementById('inv-load-more-btn');

    if (!paginationDiv) return;

    if (hasMore) {
        paginationDiv.style.display = 'block';
        if (loadMoreBtn) loadMoreBtn.style.display = 'inline-block';

        // Set up infinite scroll with Intersection Observer
        setupInfiniteScroll();
    } else {
        if (loadMoreBtn) loadMoreBtn.style.display = 'none';
        teardownInfiniteScroll();
    }

    if (infoSpan) {
        infoSpan.textContent = `Showing ${investigations.length} of ${totalCount} investigations`;
    }

    if (investigations.length > 0) {
        paginationDiv.style.display = 'block';
    } else {
        paginationDiv.style.display = 'none';
    }
}

/**
 * Set up Intersection Observer for infinite scroll
 */
function setupInfiniteScroll() {
    teardownInfiniteScroll(); // Clean up any existing observer

    const loadMoreBtn = document.getElementById('inv-load-more-btn');
    if (!loadMoreBtn) return;

    infiniteScrollObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                loadMoreInvestigations();
            }
        });
    }, {
        root: null,
        rootMargin: '100px',
        threshold: 0.1
    });

    infiniteScrollObserver.observe(loadMoreBtn);
}

/**
 * Teardown infinite scroll observer
 */
function teardownInfiniteScroll() {
    if (infiniteScrollObserver) {
        infiniteScrollObserver.disconnect();
        infiniteScrollObserver = null;
    }
}

// Variable to prevent duplicate loading
let isLoadingMore = false;

/**
 * Load more investigations (pagination) - with debounce
 */
window.loadMoreInvestigations = function() {
    if (isLoadingMore) return;
    isLoadingMore = true;

    currentOffset = investigations.length;
    loadInvestigations(true).finally(() => {
        isLoadingMore = false;
    });
};

/**
 * Show error message
 */
function showError(message) {
    const grid = document.getElementById('inv-cards-grid');
    if (grid) {
        grid.innerHTML = `
            <div class="inv-empty-state">
                <div class="icon">⚠️</div>
                <p style="color: var(--red);">${escapeHtml(message)}</p>
                <button class="btn" onclick="refreshInvestigationHistory()">Retry</button>
            </div>
        `;
    }
}

// ============================================================================
// Filter Functions
// ============================================================================

/**
 * Toggle a tag filter
 */
window.toggleTagFilter = function(tag) {
    const idx = selectedTags.indexOf(tag);
    if (idx >= 0) {
        selectedTags.splice(idx, 1);
    } else {
        selectedTags.push(tag);
    }
    renderTagsBar();
    currentOffset = 0;
    loadInvestigations();
};

/**
 * Debounce search input
 */
window.debounceInvestigationSearch = function() {
    if (searchDebounceTimer) {
        clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
        currentOffset = 0;
        loadInvestigations();
    }, 300);
};

/**
 * Apply all filters and reload
 */
window.applyInvestigationFilters = function() {
    currentOffset = 0;
    loadInvestigations();
};

/**
 * Refresh investigation history
 */
window.refreshInvestigationHistory = async function() {
    currentOffset = 0;
    await Promise.all([
        loadInvestigationStats(),
        loadAllTags(),
        loadInvestigations()
    ]);
    showToast('Investigation history refreshed');
};

// ============================================================================
// Detail Modal Functions
// ============================================================================

/**
 * Show investigation detail modal
 */
window.showInvestigationDetail = async function(investigationId) {
    currentInvestigationId = investigationId;

    const modal = document.getElementById('inv-detail-modal');
    const body = document.getElementById('inv-detail-modal-body');
    const title = document.getElementById('inv-detail-modal-title');

    if (!modal || !body) return;

    if (title) title.textContent = `Investigation: ${investigationId}`;
    body.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-dim);">Loading...</div>';
    modal.style.display = 'flex';

    try {
        // Load investigation status
        const status = await api(`/api/investigation/status/${investigationId}`);
        if (status.error) {
            body.innerHTML = `<div style="color: var(--red);">Error: ${escapeHtml(status.error)}</div>`;
            return;
        }

        // Load report if completed
        let reportContent = '';
        if (status.status === 'completed' && status.report_path) {
            try {
                const reportData = await api(`/api/investigation/report/${investigationId}`);
                if (reportData.report_content) {
                    reportContent = reportData.report_content;
                }
            } catch (e) {
                console.log('Could not load report:', e);
            }
        }

        // Load tags
        const tagsData = await api(`/api/investigation/${investigationId}/tags`);
        const tags = tagsData.tags || [];

        // Render detail view
        body.innerHTML = `
            <div class="inv-detail-meta">
                <div class="inv-detail-meta-item">
                    <div class="value">${status.status || '-'}</div>
                    <div class="label">Status</div>
                </div>
                <div class="inv-detail-meta-item">
                    <div class="value">${formatDuration(status.elapsed_seconds)}</div>
                    <div class="label">Duration</div>
                </div>
                <div class="inv-detail-meta-item">
                    <div class="value">${status.subagent_count || 0}</div>
                    <div class="label">Subagents</div>
                </div>
                <div class="inv-detail-meta-item">
                    <div class="value">${formatDate(status.completed_at || status.started_at)}</div>
                    <div class="label">Date</div>
                </div>
            </div>

            <div style="margin-bottom: 15px;">
                <label style="color: var(--text-dim); font-size: 0.85em; display: block; margin-bottom: 8px;">Query:</label>
                <div style="background: var(--bg); padding: 12px; border-radius: 6px; font-size: 0.9em;">
                    ${escapeHtml(status.query || 'No query')}
                </div>
            </div>

            <div class="inv-detail-tags">
                <span style="color: var(--text-dim); font-size: 0.85em; margin-right: 10px;">Tags:</span>
                ${tags.length > 0 ? tags.map(t => `<span class="inv-tag">${escapeHtml(t)}</span>`).join('') : '<span style="color: var(--text-dim);">No tags</span>'}
                <button class="btn" style="padding: 3px 8px; font-size: 0.75em; margin-left: 10px;" onclick="openTagModal('${investigationId}')">Edit Tags</button>
            </div>

            ${status.error ? `
            <div style="background: rgba(248, 81, 73, 0.1); border: 1px solid var(--red); border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                <label style="color: var(--red); font-size: 0.85em; display: block; margin-bottom: 5px;">Error:</label>
                <div style="color: var(--text); font-size: 0.9em;">${escapeHtml(status.error)}</div>
            </div>
            ` : ''}

            ${status.has_attachments ? `
            <div style="background: rgba(88, 166, 255, 0.1); border: 1px solid rgba(88, 166, 255, 0.3); border-radius: 6px; padding: 12px; margin-bottom: 15px;">
                <label style="color: var(--accent); font-size: 0.85em; display: block; margin-bottom: 8px;">
                    <span style="margin-right: 5px;">📎</span>Attachments (${status.attachment_count}):
                </label>
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${(status.attachments || []).map(att => `
                        <div style="display: flex; justify-content: space-between; align-items: center; background: var(--bg); padding: 8px 10px; border-radius: 4px;">
                            <span style="font-family: monospace; font-size: 0.85em;">${escapeHtml(att.filename)}</span>
                            <span style="font-size: 0.75em; color: ${att.has_text ? 'var(--green)' : 'var(--text-dim)'};">
                                ${att.has_text ? 'Extracted' : (att.extraction_error || 'Binary')}
                            </span>
                        </div>
                    `).join('')}
                </div>
            </div>
            ` : ''}

            ${reportContent ? `
            <div>
                <label style="color: var(--text-dim); font-size: 0.85em; display: block; margin-bottom: 8px;">Report:</label>
                <div class="inv-detail-report">
                    <pre>${escapeHtml(reportContent)}</pre>
                </div>
            </div>
            ` : `
            <div style="text-align: center; padding: 30px; color: var(--text-dim);">
                No report available
            </div>
            `}
        `;
    } catch (e) {
        body.innerHTML = `<div style="color: var(--red);">Error loading investigation: ${escapeHtml(e.message)}</div>`;
    }
};

/**
 * Close the investigation detail modal
 */
window.closeInvestigationDetailModal = function() {
    const modal = document.getElementById('inv-detail-modal');
    if (modal) modal.style.display = 'none';
    currentInvestigationId = null;
};

// ============================================================================
// Tag Modal Functions
// ============================================================================

let currentTagModalInvestigationId = null;

/**
 * Open tag edit modal
 */
window.openTagModal = async function(investigationId) {
    currentTagModalInvestigationId = investigationId;

    const modal = document.getElementById('inv-tag-modal');
    const tagsContainer = document.getElementById('inv-tag-modal-tags');
    const suggestionsContainer = document.getElementById('inv-tag-suggestions');
    const input = document.getElementById('inv-tag-modal-input');

    if (!modal) return;

    modal.style.display = 'flex';
    if (input) input.value = '';

    // Load current tags
    try {
        const data = await api(`/api/investigation/${investigationId}/tags`);
        const tags = data.tags || [];

        if (tagsContainer) {
            tagsContainer.innerHTML = tags.length > 0
                ? tags.map(t => `
                    <span class="inv-tag">
                        ${escapeHtml(t)}
                        <span class="remove" onclick="removeTagFromInvestigation('${escapeHtml(t)}')">&times;</span>
                    </span>
                `).join('')
                : '<span style="color: var(--text-dim);">No tags</span>';
        }

        // Show tag suggestions
        if (suggestionsContainer) {
            const existingTags = new Set(tags.map(t => t.toLowerCase()));
            const suggestions = allTags
                .filter(t => !existingTags.has(t.tag.toLowerCase()))
                .slice(0, 6);

            suggestionsContainer.innerHTML = suggestions.length > 0
                ? '<label style="font-size: 0.8em; color: var(--text-dim); margin-bottom: 5px; display: block;">Suggestions:</label>' +
                  suggestions.map(t => `
                    <span class="inv-tag-suggestion" onclick="addTagToInvestigation('${escapeHtml(t.tag)}')">${escapeHtml(t.tag)}</span>
                  `).join('')
                : '';
        }
    } catch (e) {
        console.error('Error loading tags for modal:', e);
    }
};

/**
 * Close tag modal
 */
window.closeTagModal = function() {
    const modal = document.getElementById('inv-tag-modal');
    if (modal) modal.style.display = 'none';
    currentTagModalInvestigationId = null;
    // Refresh the cards to show updated tags
    loadInvestigations();
    loadAllTags();
};

/**
 * Add tag from modal input
 */
window.addTagFromModal = async function() {
    const input = document.getElementById('inv-tag-modal-input');
    if (!input || !input.value.trim()) return;

    await addTagToInvestigation(input.value.trim());
    input.value = '';
};

/**
 * Add a tag to the current investigation
 */
window.addTagToInvestigation = async function(tag) {
    if (!currentTagModalInvestigationId) return;

    try {
        const result = await api(`/api/investigation/${currentTagModalInvestigationId}/tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag: tag })
        });

        if (result.success) {
            showToast(`Tag "${tag}" added`);
            // Refresh the modal
            await openTagModal(currentTagModalInvestigationId);
        } else {
            showToast('Failed to add tag: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error adding tag: ' + e.message);
    }
};

/**
 * Remove a tag from the current investigation
 */
window.removeTagFromInvestigation = async function(tag) {
    if (!currentTagModalInvestigationId) return;

    try {
        const result = await api(`/api/investigation/${currentTagModalInvestigationId}/tags/${encodeURIComponent(tag)}`, {
            method: 'DELETE'
        });

        if (result.success) {
            showToast(`Tag "${tag}" removed`);
            // Refresh the modal
            await openTagModal(currentTagModalInvestigationId);
        } else {
            showToast('Failed to remove tag: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error removing tag: ' + e.message);
    }
};

// ============================================================================
// Date Filter Functions
// ============================================================================

/**
 * Clear the date range filter
 */
window.clearInvestigationDateFilter = function() {
    const from = document.getElementById('inv-date-from');
    const to = document.getElementById('inv-date-to');
    if (from) from.value = '';
    if (to) to.value = '';
    currentOffset = 0;
    loadInvestigations();
};

// ============================================================================
// Selection Mode Functions
// ============================================================================

/**
 * Toggle selection mode on/off
 */
window.toggleInvestigationSelectionMode = function() {
    selectionModeActive = !selectionModeActive;
    const btn = document.getElementById('inv-selection-mode-btn');
    if (btn) {
        btn.textContent = selectionModeActive ? 'Done' : 'Select';
        btn.classList.toggle('primary', selectionModeActive);
    }

    if (!selectionModeActive) {
        selectedInvestigations.clear();
        updateBulkActionBar();
    }

    renderInvestigationCards();
};

/**
 * Toggle selection of a single investigation
 */
window.toggleInvestigationSelection = function(id, event) {
    if (event) event.stopPropagation();

    if (selectedInvestigations.has(id)) {
        selectedInvestigations.delete(id);
    } else {
        selectedInvestigations.add(id);
    }

    updateBulkActionBar();
    renderInvestigationCards();
};

/**
 * Clear all selections
 */
window.clearInvestigationSelection = function() {
    selectedInvestigations.clear();
    updateBulkActionBar();
    renderInvestigationCards();
};

/**
 * Update the bulk action bar visibility and count
 */
function updateBulkActionBar() {
    const bar = document.getElementById('inv-bulk-action-bar');
    const count = document.getElementById('inv-selection-count');

    if (selectedInvestigations.size > 0) {
        bar.style.display = 'flex';
        count.textContent = `${selectedInvestigations.size} selected`;
    } else {
        bar.style.display = 'none';
    }
}

// ============================================================================
// Bulk Action Functions
// ============================================================================

/**
 * Add a tag to all selected investigations
 */
window.bulkAddTag = async function() {
    if (selectedInvestigations.size === 0) {
        showToast('No investigations selected');
        return;
    }

    const tag = prompt('Enter tag to add to selected investigations:');
    if (!tag || !tag.trim()) return;

    try {
        const result = await api('/api/investigation/bulk/tags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(selectedInvestigations),
                tag: tag.trim()
            })
        });

        if (result.success) {
            showToast(`Tag "${tag}" added to ${result.tagged_count} investigations`);
            loadInvestigations();
            loadAllTags();
        } else {
            showToast('Failed to add tags: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error adding tags: ' + e.message);
    }
};

/**
 * Export all selected investigations as JSON bundle
 */
window.bulkExport = async function() {
    if (selectedInvestigations.size === 0) {
        showToast('No investigations selected');
        return;
    }

    try {
        const result = await api('/api/investigation/bulk/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids: Array.from(selectedInvestigations)
            })
        });

        if (result.success) {
            // Create a downloadable JSON file
            const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `investigations_export_${Date.now()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            showToast(`Exported ${result.count} investigations`);
        } else {
            showToast('Export failed: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Export failed: ' + e.message);
    }
};

// ============================================================================
// Comparison Functions
// ============================================================================

/**
 * Compare two selected investigations
 */
window.compareSelectedInvestigations = async function() {
    if (selectedInvestigations.size !== 2) {
        showToast('Select exactly 2 investigations to compare');
        return;
    }

    const ids = Array.from(selectedInvestigations);

    try {
        // Fetch both investigations
        const [inv1, inv2] = await Promise.all([
            api(`/api/investigation/status/${ids[0]}`),
            api(`/api/investigation/status/${ids[1]}`)
        ]);

        if (inv1.error || inv2.error) {
            showToast('Failed to load investigation details');
            return;
        }

        renderComparisonModal(inv1, inv2);
    } catch (e) {
        showToast('Failed to compare: ' + e.message);
    }
};

/**
 * Render the comparison modal with two investigations
 */
function renderComparisonModal(inv1, inv2) {
    const modal = document.getElementById('inv-compare-modal');
    const body = document.getElementById('inv-compare-body');

    if (!modal || !body) return;

    // Calculate differences
    const elapsed1 = inv1.elapsed_seconds || 0;
    const elapsed2 = inv2.elapsed_seconds || 0;
    const durationDiff = elapsed1 - elapsed2;
    const durationDiffText = durationDiff > 0 ? `+${formatDuration(durationDiff)}` : formatDuration(Math.abs(durationDiff));

    const subagents1 = inv1.subagent_count || 0;
    const subagents2 = inv2.subagent_count || 0;
    const subagentDiff = subagents1 - subagents2;

    body.innerHTML = `
        <div class="compare-column" style="background: var(--bg); padding: 15px; border-radius: 8px;">
            <h4 style="color: var(--accent); margin-bottom: 15px;">${inv1.investigation_id}</h4>
            <div style="margin-bottom: 15px;">
                <label style="color: var(--text-dim); font-size: 0.8em;">Query:</label>
                <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px; font-size: 0.9em;">
                    ${escapeHtml(inv1.query || 'No query')}
                </div>
            </div>
            <div class="compare-stats" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Status</span>
                    <div style="font-weight: 500;">${inv1.status}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Duration</span>
                    <div style="font-weight: 500;">${formatDuration(elapsed1)}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Subagents</span>
                    <div style="font-weight: 500;">${subagents1}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Started</span>
                    <div style="font-weight: 500; font-size: 0.85em;">${formatDate(inv1.started_at)}</div>
                </div>
            </div>
        </div>
        <div class="compare-column" style="background: var(--bg); padding: 15px; border-radius: 8px;">
            <h4 style="color: var(--accent); margin-bottom: 15px;">${inv2.investigation_id}</h4>
            <div style="margin-bottom: 15px;">
                <label style="color: var(--text-dim); font-size: 0.8em;">Query:</label>
                <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px; font-size: 0.9em;">
                    ${escapeHtml(inv2.query || 'No query')}
                </div>
            </div>
            <div class="compare-stats" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Status</span>
                    <div style="font-weight: 500;">${inv2.status}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Duration</span>
                    <div style="font-weight: 500;">${formatDuration(elapsed2)}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Subagents</span>
                    <div style="font-weight: 500;">${subagents2}</div>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Started</span>
                    <div style="font-weight: 500; font-size: 0.85em;">${formatDate(inv2.started_at)}</div>
                </div>
            </div>
        </div>
        <div class="compare-diff" style="grid-column: 1 / -1; background: var(--panel); padding: 15px; border-radius: 8px; margin-top: 10px;">
            <h4 style="color: var(--text-dim); margin-bottom: 10px; font-size: 0.9em;">Difference Summary</h4>
            <div style="display: flex; gap: 30px; flex-wrap: wrap;">
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Duration Diff:</span>
                    <span style="margin-left: 5px; color: ${durationDiff > 0 ? 'var(--red)' : 'var(--green)'}; font-weight: 500;">
                        ${durationDiff > 0 ? '+' : ''}${formatDuration(durationDiff)}
                    </span>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Subagent Diff:</span>
                    <span style="margin-left: 5px; color: ${subagentDiff > 0 ? 'var(--yellow)' : 'var(--text)'}; font-weight: 500;">
                        ${subagentDiff > 0 ? '+' : ''}${subagentDiff}
                    </span>
                </div>
                <div>
                    <span style="color: var(--text-dim); font-size: 0.8em;">Same Query:</span>
                    <span style="margin-left: 5px; color: ${inv1.query === inv2.query ? 'var(--green)' : 'var(--yellow)'}; font-weight: 500;">
                        ${inv1.query === inv2.query ? 'Yes' : 'No'}
                    </span>
                </div>
            </div>
        </div>
    `;

    modal.style.display = 'flex';
}

/**
 * Close the comparison modal
 */
window.closeCompareModal = function() {
    const modal = document.getElementById('inv-compare-modal');
    if (modal) modal.style.display = 'none';
};

// ============================================================================
// Export Functions
// ============================================================================

/**
 * Export current investigation
 */
window.exportCurrentInvestigation = async function(format) {
    if (!currentInvestigationId) return;

    try {
        // Open in new tab for download
        window.open(`/api/investigation/${currentInvestigationId}/export?format=${format}&download=true`, '_blank');
        showToast(`Exporting as ${format.toUpperCase()}...`);
    } catch (e) {
        showToast('Export failed: ' + e.message);
    }
};

// ============================================================================
// Re-run Functions
// ============================================================================

/**
 * Re-run current investigation
 */
window.rerunCurrentInvestigation = function() {
    if (!currentInvestigationId) return;
    rerunInvestigation(currentInvestigationId);
};

/**
 * Re-run a specific investigation
 */
window.rerunInvestigation = async function(investigationId) {
    try {
        // Get the investigation details first
        const status = await api(`/api/investigation/status/${investigationId}`);
        if (status.error) {
            showToast('Cannot re-run: ' + status.error);
            return;
        }

        const query = status.query;
        const subagentCount = status.subagent_count || 5;

        // Close the detail modal if open
        closeInvestigationDetailModal();

        // Switch to AtlasForge tab
        if (typeof window.switchTab === 'function') {
            window.switchTab('atlasforge');
        }

        // Enable investigation mode and populate the form
        const checkbox = document.getElementById('investigation-mode-checkbox');
        if (checkbox && !checkbox.checked) {
            checkbox.checked = true;
            if (typeof window.toggleInvestigationMode === 'function') {
                window.toggleInvestigationMode();
            }
        }

        // Set the query in the mission input
        const missionInput = document.getElementById('mission-input');
        if (missionInput) {
            missionInput.value = query;
        }

        // Set the subagent count
        const subagentSelect = document.getElementById('investigation-subagents');
        if (subagentSelect) {
            // Find the closest option value
            const options = Array.from(subagentSelect.options);
            const closest = options.reduce((prev, curr) => {
                return Math.abs(parseInt(curr.value) - subagentCount) < Math.abs(parseInt(prev.value) - subagentCount) ? curr : prev;
            });
            subagentSelect.value = closest.value;
        }

        showToast('Investigation query loaded. Click "Start Investigation" to run.');
    } catch (e) {
        showToast('Error preparing re-run: ' + e.message);
    }
};

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Format duration in seconds to human-readable string
 */
function formatDuration(seconds) {
    if (seconds === undefined || seconds === null) return '-';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.round(seconds % 60);
        return `${mins}m ${secs}s`;
    }
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}

/**
 * Format ISO date to readable string
 */
function formatDate(isoString) {
    if (!isoString) return '-';
    try {
        const date = new Date(isoString);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return isoString.substring(0, 16);
    }
}

// ============================================================================
// Keyboard Shortcuts
// ============================================================================

/**
 * Set up keyboard shortcuts for investigation history
 */
function setupInvestigationKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Only handle when investigation tab is active
        const tab = document.getElementById('investigations-tab');
        if (!tab || !tab.classList.contains('active')) return;

        // Escape to close modals
        if (e.key === 'Escape') {
            closeInvestigationDetailModal();
            closeTagModal();
            closeCompareModal();
            closeTagStatsModal();
            closeSaveSearchModal();
        }

        // Enter to submit in inputs
        if (e.key === 'Enter') {
            const tagInput = document.getElementById('inv-tag-modal-input');
            if (document.activeElement === tagInput && tagInput.value.trim()) {
                e.preventDefault();
                addTagFromModal();
            }

            const saveSearchName = document.getElementById('inv-save-search-name');
            if (document.activeElement === saveSearchName && saveSearchName.value.trim()) {
                e.preventDefault();
                saveCurrentSearch();
            }
        }
    });
}

// ============================================================================
// Tag Statistics Functions
// ============================================================================

/**
 * Open tag statistics modal
 */
window.openTagStatsModal = async function() {
    const modal = document.getElementById('inv-tag-stats-modal');
    const body = document.getElementById('inv-tag-stats-body');

    if (!modal || !body) return;

    modal.style.display = 'flex';
    body.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-dim);">Loading tag analytics...</div>';

    try {
        const data = await api('/api/investigation/tags/stats');
        if (data.error) {
            body.innerHTML = `<div style="color: var(--red);">Error: ${escapeHtml(data.error)}</div>`;
            return;
        }

        renderTagStats(body, data);
    } catch (e) {
        body.innerHTML = `<div style="color: var(--red);">Failed to load tag statistics: ${escapeHtml(e.message)}</div>`;
    }
};

/**
 * Render tag statistics charts
 */
function renderTagStats(container, data) {
    const tagCounts = data.tag_counts || [];
    const coOccurrence = data.co_occurrence || [];
    const usageByMonth = data.usage_by_month || {};

    if (tagCounts.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-dim);">No tag data available yet. Add some tags to investigations to see analytics.</div>';
        return;
    }

    // Find max count for bar chart scaling
    const maxCount = Math.max(...tagCounts.map(t => t.count));

    // Build tag usage bar chart
    const barChart = tagCounts.slice(0, 15).map(t => {
        const color = getTagColor(t.tag);
        const pct = (t.count / maxCount) * 100;
        return `
            <div class="tag-stat-bar" style="display: flex; align-items: center; margin: 6px 0; gap: 10px;">
                <span style="width: 100px; font-size: 0.85em; color: ${color.text}; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${escapeHtml(t.tag)}</span>
                <div style="flex: 1; height: 20px; background: var(--bg); border-radius: 4px; overflow: hidden;">
                    <div style="width: ${pct}%; height: 100%; background: ${color.bg}; border-right: 2px solid ${color.border};"></div>
                </div>
                <span style="width: 30px; text-align: right; font-size: 0.85em; color: var(--text-dim);">${t.count}</span>
            </div>
        `;
    }).join('');

    // Build co-occurrence matrix
    let coOccurrenceHtml = '';
    if (coOccurrence.length > 0) {
        const maxCoCount = Math.max(...coOccurrence.map(c => c.count));
        coOccurrenceHtml = coOccurrence.slice(0, 10).map(c => {
            const intensity = Math.round((c.count / maxCoCount) * 255);
            const color1 = getTagColor(c.tag1);
            const color2 = getTagColor(c.tag2);
            return `
                <div style="display: flex; align-items: center; padding: 6px 10px; margin: 3px 0; background: rgba(${intensity}, ${intensity}, 255, 0.1); border-radius: 4px; gap: 8px;">
                    <span style="color: ${color1.text}; font-size: 0.85em;">${escapeHtml(c.tag1)}</span>
                    <span style="color: var(--text-dim);">+</span>
                    <span style="color: ${color2.text}; font-size: 0.85em;">${escapeHtml(c.tag2)}</span>
                    <span style="margin-left: auto; color: var(--accent); font-weight: 500;">${c.count}x</span>
                </div>
            `;
        }).join('');
    } else {
        coOccurrenceHtml = '<div style="color: var(--text-dim); font-size: 0.85em; padding: 10px;">No co-occurrences yet (need investigations with 2+ tags)</div>';
    }

    // Build monthly trends
    let trendsHtml = '';
    const allMonths = new Set();
    Object.values(usageByMonth).forEach(months => Object.keys(months).forEach(m => allMonths.add(m)));
    const sortedMonths = Array.from(allMonths).sort().slice(-6); // Last 6 months

    if (sortedMonths.length > 0) {
        // Show top 5 tags over time
        const topTags = tagCounts.slice(0, 5).map(t => t.tag);
        trendsHtml = `
            <div class="tag-trends-legend" style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px;">
                ${topTags.map(tag => {
                    const color = getTagColor(tag);
                    return `<span style="color: ${color.text}; font-size: 0.8em;">● ${escapeHtml(tag)}</span>`;
                }).join('')}
            </div>
            <div class="tag-trends-chart" style="display: flex; align-items: flex-end; gap: 4px; height: 80px; padding-top: 10px;">
                ${sortedMonths.map(month => {
                    const monthLabel = month.split('-')[1];
                    return `
                        <div style="flex: 1; display: flex; flex-direction: column; align-items: center;">
                            <div style="display: flex; flex-direction: column-reverse; width: 100%; gap: 2px;">
                                ${topTags.map(tag => {
                                    const count = (usageByMonth[tag] || {})[month] || 0;
                                    const color = getTagColor(tag);
                                    return count > 0 ? `<div style="height: ${count * 8}px; background: ${color.bg}; border: 1px solid ${color.border}; border-radius: 2px;" title="${tag}: ${count}"></div>` : '';
                                }).join('')}
                            </div>
                            <span style="font-size: 0.7em; color: var(--text-dim); margin-top: 4px;">${monthLabel}</span>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } else {
        trendsHtml = '<div style="color: var(--text-dim); font-size: 0.85em; padding: 10px;">No trend data available</div>';
    }

    container.innerHTML = `
        <div class="tag-stats-grid" style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div class="tag-stats-section">
                <h4 style="color: var(--accent); margin-bottom: 15px; font-size: 0.9em;">Most Used Tags</h4>
                ${barChart}
            </div>
            <div class="tag-stats-section">
                <h4 style="color: var(--accent); margin-bottom: 15px; font-size: 0.9em;">Tag Co-occurrence</h4>
                ${coOccurrenceHtml}
            </div>
        </div>
        <div class="tag-stats-section" style="margin-top: 20px;">
            <h4 style="color: var(--accent); margin-bottom: 15px; font-size: 0.9em;">Usage Trends (Last 6 Months)</h4>
            ${trendsHtml}
        </div>
    `;
}

/**
 * Close tag statistics modal
 */
window.closeTagStatsModal = function() {
    const modal = document.getElementById('inv-tag-stats-modal');
    if (modal) modal.style.display = 'none';
};

// ============================================================================
// Saved Searches Functions
// ============================================================================

/**
 * Load saved searches from API
 */
async function loadSavedSearches() {
    try {
        const data = await api('/api/investigation/saved-searches');
        savedSearches = data.searches || [];
        renderSavedSearchesDropdown();
    } catch (e) {
        console.error('Error loading saved searches:', e);
    }
}

/**
 * Render saved searches dropdown
 */
function renderSavedSearchesDropdown() {
    const select = document.getElementById('inv-saved-searches');
    if (!select) return;

    if (savedSearches.length === 0) {
        select.innerHTML = '<option value="">No saved searches</option>';
        select.disabled = true;
        return;
    }

    select.disabled = false;
    select.innerHTML = '<option value="">Load saved search...</option>' +
        savedSearches.map(s => `
            <option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>
        `).join('');
}

/**
 * Open save search modal
 */
window.openSaveSearchModal = function() {
    const modal = document.getElementById('inv-save-search-modal');
    const input = document.getElementById('inv-save-search-name');

    if (!modal) return;

    modal.style.display = 'flex';
    if (input) {
        input.value = '';
        input.focus();
    }
};

/**
 * Close save search modal
 */
window.closeSaveSearchModal = function() {
    const modal = document.getElementById('inv-save-search-modal');
    if (modal) modal.style.display = 'none';
};

/**
 * Save current search configuration
 */
window.saveCurrentSearch = async function() {
    const nameInput = document.getElementById('inv-save-search-name');
    const name = nameInput ? nameInput.value.trim() : '';

    if (!name) {
        showToast('Please enter a name for this search');
        return;
    }

    // Gather current filter state
    const searchInput = document.getElementById('inv-search-input');
    const statusFilter = document.getElementById('inv-status-filter');
    const sortBy = document.getElementById('inv-sort-by');
    const sortOrder = document.getElementById('inv-sort-order');
    const dateFrom = document.getElementById('inv-date-from');
    const dateTo = document.getElementById('inv-date-to');
    const searchContent = document.getElementById('inv-search-content');

    const searchConfig = {
        name: name,
        search: searchInput ? searchInput.value : '',
        status: statusFilter ? statusFilter.value : '',
        sort_by: sortBy ? sortBy.value : 'timestamp',
        sort_order: sortOrder ? sortOrder.value : 'desc',
        date_from: dateFrom ? dateFrom.value : '',
        date_to: dateTo ? dateTo.value : '',
        tags: [...selectedTags],
        search_content: searchContent ? searchContent.checked : false
    };

    try {
        const result = await api('/api/investigation/saved-searches', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(searchConfig)
        });

        if (result.success) {
            showToast(`Saved search "${name}"`);
            closeSaveSearchModal();
            loadSavedSearches();
        } else {
            showToast('Failed to save search: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error saving search: ' + e.message);
    }
};

/**
 * Apply a saved search configuration
 */
window.applySavedSearch = async function(searchId) {
    if (!searchId) return;

    const search = savedSearches.find(s => s.id === searchId);
    if (!search) return;

    // Apply filters from saved search
    const searchInput = document.getElementById('inv-search-input');
    const statusFilter = document.getElementById('inv-status-filter');
    const sortBy = document.getElementById('inv-sort-by');
    const sortOrder = document.getElementById('inv-sort-order');
    const dateFrom = document.getElementById('inv-date-from');
    const dateTo = document.getElementById('inv-date-to');
    const searchContent = document.getElementById('inv-search-content');

    if (searchInput) searchInput.value = search.search || '';
    if (statusFilter) statusFilter.value = search.status || '';
    if (sortBy) sortBy.value = search.sort_by || 'timestamp';
    if (sortOrder) sortOrder.value = search.sort_order || 'desc';
    if (dateFrom) dateFrom.value = search.date_from || '';
    if (dateTo) dateTo.value = search.date_to || '';
    if (searchContent) searchContent.checked = search.search_content || false;

    // Update selected tags
    selectedTags = search.tags || [];
    renderTagsBar();

    // Reset the dropdown
    const select = document.getElementById('inv-saved-searches');
    if (select) select.value = '';

    showToast(`Applied search: ${search.name}`);
    currentOffset = 0;
    loadInvestigations();
};

/**
 * Delete a saved search
 */
window.deleteSavedSearch = async function(searchId) {
    if (!confirm('Delete this saved search?')) return;

    try {
        const result = await api(`/api/investigation/saved-searches/${searchId}`, {
            method: 'DELETE'
        });

        if (result.success) {
            showToast('Saved search deleted');
            loadSavedSearches();
        } else {
            showToast('Failed to delete: ' + (result.error || 'Unknown error'));
        }
    } catch (e) {
        showToast('Error deleting search: ' + e.message);
    }
};

// ============================================================================
// Enhanced Tag Modal with Query Suggestions
// ============================================================================

// Store original openTagModal and enhance it
const originalOpenTagModal = window.openTagModal;

window.openTagModal = async function(investigationId) {
    currentTagModalInvestigationId = investigationId;

    const modal = document.getElementById('inv-tag-modal');
    const tagsContainer = document.getElementById('inv-tag-modal-tags');
    const suggestionsContainer = document.getElementById('inv-tag-suggestions');
    const querySuggestionsContainer = document.getElementById('inv-query-tag-suggestions');
    const input = document.getElementById('inv-tag-modal-input');

    if (!modal) return;

    modal.style.display = 'flex';
    if (input) input.value = '';

    // Get investigation query for suggestions
    const inv = investigations.find(i => i.investigation_id === investigationId);
    const query = inv?.query || '';

    try {
        // Load current tags
        const data = await api(`/api/investigation/${investigationId}/tags`);
        const tags = data.tags || [];

        if (tagsContainer) {
            tagsContainer.innerHTML = tags.length > 0
                ? tags.map(t => renderColoredTag(t, true, `removeTagFromInvestigation('${escapeHtml(t)}')`)).join('')
                : '<span style="color: var(--text-dim);">No tags</span>';
        }

        // Get query-based suggestions
        if (query && querySuggestionsContainer) {
            try {
                const suggestResp = await api(`/api/investigation/tags/suggest?query=${encodeURIComponent(query)}&exclude=${tags.join(',')}`);
                const querySuggestions = suggestResp.suggestions || [];

                if (querySuggestions.length > 0) {
                    querySuggestionsContainer.innerHTML = `
                        <label style="font-size: 0.8em; color: var(--accent); margin-bottom: 5px; display: block;">Suggested for this query:</label>
                        <div style="display: flex; gap: 6px; flex-wrap: wrap;">
                            ${querySuggestions.map(t => {
                                const color = getTagColor(t);
                                return `<span class="inv-tag-suggestion" onclick="addTagToInvestigation('${escapeHtml(t)}')"
                                    style="padding: 4px 10px; border-radius: 12px; cursor: pointer; font-size: 0.85em;
                                           background: ${color.bg}; border: 1px dashed ${color.border}; color: ${color.text};">
                                    ${escapeHtml(t)}
                                </span>`;
                            }).join('')}
                        </div>
                    `;
                    querySuggestionsContainer.style.display = 'block';
                } else {
                    querySuggestionsContainer.style.display = 'none';
                }
            } catch (e) {
                console.log('Could not load query suggestions:', e);
                querySuggestionsContainer.style.display = 'none';
            }
        }

        // Show general tag suggestions
        if (suggestionsContainer) {
            const existingTags = new Set(tags.map(t => t.toLowerCase()));
            const suggestions = allTags
                .filter(t => !existingTags.has(t.tag.toLowerCase()))
                .slice(0, 6);

            suggestionsContainer.innerHTML = suggestions.length > 0
                ? '<label style="font-size: 0.8em; color: var(--text-dim); margin-bottom: 5px; display: block;">Popular tags:</label>' +
                  '<div style="display: flex; gap: 6px; flex-wrap: wrap;">' +
                  suggestions.map(t => {
                      const color = getTagColor(t.tag);
                      return `<span class="inv-tag-suggestion" onclick="addTagToInvestigation('${escapeHtml(t.tag)}')"
                          style="padding: 4px 10px; border-radius: 12px; cursor: pointer; font-size: 0.85em;
                                 background: ${color.bg}; border: 1px solid ${color.border}; color: ${color.text};">
                          ${escapeHtml(t.tag)}
                      </span>`;
                  }).join('') + '</div>'
                : '';
        }
    } catch (e) {
        console.error('Error loading tags for modal:', e);
    }
};

// ============================================================================
// Enhanced Comparison Modal with Report Diff
// ============================================================================

/**
 * Compute simple line-based diff between two texts
 */
function computeSimpleDiff(text1, text2) {
    const lines1 = new Set(text1.split('\n').map(l => l.trim()).filter(l => l));
    const lines2 = new Set(text2.split('\n').map(l => l.trim()).filter(l => l));

    const added = [...lines2].filter(l => !lines1.has(l));
    const removed = [...lines1].filter(l => !lines2.has(l));

    return {
        added: added.length,
        removed: removed.length,
        lines: [
            ...added.map(l => ({ type: 'added', line: l })),
            ...removed.map(l => ({ type: 'removed', line: l }))
        ]
    };
}

/**
 * Render diff lines
 */
function renderDiffLines(lines) {
    return lines.slice(0, 30).map(l => `
        <div style="padding: 2px 8px; margin: 1px 0; background: ${l.type === 'added' ? 'rgba(63,185,80,0.2)' : 'rgba(248,81,73,0.2)'}; border-radius: 3px;">
            <span style="color: ${l.type === 'added' ? 'var(--green)' : 'var(--red)'}; margin-right: 5px;">
                ${l.type === 'added' ? '+' : '-'}
            </span>
            ${escapeHtml(l.line.substring(0, 100))}${l.line.length > 100 ? '...' : ''}
        </div>
    `).join('') + (lines.length > 30 ? `<div style="color: var(--text-dim); padding: 5px;">...and ${lines.length - 30} more lines</div>` : '');
}

/**
 * Toggle diff view visibility
 */
window.toggleDiffView = function() {
    const content = document.getElementById('inv-diff-content');
    if (content) {
        content.style.display = content.style.display === 'none' ? 'block' : 'none';
    }
};

// Enhanced comparison modal that adds report diff
const originalRenderComparisonModal = typeof renderComparisonModal === 'function' ? renderComparisonModal : null;

// Override compareSelectedInvestigations to add diff functionality
const originalCompareSelected = window.compareSelectedInvestigations;

window.compareSelectedInvestigations = async function() {
    if (selectedInvestigations.size !== 2) {
        showToast('Select exactly 2 investigations to compare');
        return;
    }

    const ids = Array.from(selectedInvestigations);

    try {
        // Fetch both investigations and their reports
        const [inv1, inv2] = await Promise.all([
            api(`/api/investigation/status/${ids[0]}`),
            api(`/api/investigation/status/${ids[1]}`)
        ]);

        if (inv1.error || inv2.error) {
            showToast('Failed to load investigation details');
            return;
        }

        // Render comparison modal
        const modal = document.getElementById('inv-compare-modal');
        const body = document.getElementById('inv-compare-body');

        if (!modal || !body) return;

        // Calculate differences
        const elapsed1 = inv1.elapsed_seconds || 0;
        const elapsed2 = inv2.elapsed_seconds || 0;
        const durationDiff = elapsed1 - elapsed2;

        const subagents1 = inv1.subagent_count || 0;
        const subagents2 = inv2.subagent_count || 0;
        const subagentDiff = subagents1 - subagents2;

        let diffSection = '';

        // Try to load reports for diff if both completed
        if (inv1.status === 'completed' && inv2.status === 'completed') {
            try {
                const [report1, report2] = await Promise.all([
                    api(`/api/investigation/report/${ids[0]}`),
                    api(`/api/investigation/report/${ids[1]}`)
                ]);

                if (report1.report_content && report2.report_content) {
                    const diff = computeSimpleDiff(report1.report_content, report2.report_content);

                    diffSection = `
                        <div class="compare-diff" style="grid-column: 1 / -1; margin-top: 15px; background: var(--panel); padding: 15px; border-radius: 8px;">
                            <h4 style="color: var(--text-dim); margin-bottom: 10px; font-size: 0.9em;">Report Differences</h4>
                            <div style="display: flex; gap: 20px; margin-bottom: 10px;">
                                <span style="color: var(--green);">+${diff.added} lines added</span>
                                <span style="color: var(--red);">-${diff.removed} lines removed</span>
                            </div>
                            <button class="btn" onclick="toggleDiffView()" style="margin-bottom: 10px;">Show Full Diff</button>
                            <div id="inv-diff-content" style="display: none; max-height: 300px; overflow-y: auto; background: var(--bg); padding: 10px; border-radius: 4px;">
                                ${renderDiffLines(diff.lines)}
                            </div>
                        </div>
                    `;
                }
            } catch (e) {
                console.log('Could not load reports for diff:', e);
            }
        }

        body.innerHTML = `
            <div class="compare-column" style="background: var(--bg); padding: 15px; border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 15px;">${inv1.investigation_id}</h4>
                <div style="margin-bottom: 15px;">
                    <label style="color: var(--text-dim); font-size: 0.8em;">Query:</label>
                    <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px; font-size: 0.9em;">
                        ${escapeHtml(inv1.query || 'No query')}
                    </div>
                </div>
                <div class="compare-stats" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Status</span>
                        <div style="font-weight: 500;">${inv1.status}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Duration</span>
                        <div style="font-weight: 500;">${formatDuration(elapsed1)}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Subagents</span>
                        <div style="font-weight: 500;">${subagents1}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Started</span>
                        <div style="font-weight: 500; font-size: 0.85em;">${formatDate(inv1.started_at)}</div>
                    </div>
                </div>
            </div>
            <div class="compare-column" style="background: var(--bg); padding: 15px; border-radius: 8px;">
                <h4 style="color: var(--accent); margin-bottom: 15px;">${inv2.investigation_id}</h4>
                <div style="margin-bottom: 15px;">
                    <label style="color: var(--text-dim); font-size: 0.8em;">Query:</label>
                    <div style="margin-top: 5px; padding: 10px; background: var(--panel); border-radius: 4px; font-size: 0.9em;">
                        ${escapeHtml(inv2.query || 'No query')}
                    </div>
                </div>
                <div class="compare-stats" style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Status</span>
                        <div style="font-weight: 500;">${inv2.status}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Duration</span>
                        <div style="font-weight: 500;">${formatDuration(elapsed2)}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Subagents</span>
                        <div style="font-weight: 500;">${subagents2}</div>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Started</span>
                        <div style="font-weight: 500; font-size: 0.85em;">${formatDate(inv2.started_at)}</div>
                    </div>
                </div>
            </div>
            <div class="compare-diff" style="grid-column: 1 / -1; background: var(--panel); padding: 15px; border-radius: 8px; margin-top: 10px;">
                <h4 style="color: var(--text-dim); margin-bottom: 10px; font-size: 0.9em;">Difference Summary</h4>
                <div style="display: flex; gap: 30px; flex-wrap: wrap;">
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Duration Diff:</span>
                        <span style="margin-left: 5px; color: ${durationDiff > 0 ? 'var(--red)' : 'var(--green)'}; font-weight: 500;">
                            ${durationDiff > 0 ? '+' : ''}${formatDuration(durationDiff)}
                        </span>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Subagent Diff:</span>
                        <span style="margin-left: 5px; color: ${subagentDiff > 0 ? 'var(--yellow)' : 'var(--text)'}; font-weight: 500;">
                            ${subagentDiff > 0 ? '+' : ''}${subagentDiff}
                        </span>
                    </div>
                    <div>
                        <span style="color: var(--text-dim); font-size: 0.8em;">Same Query:</span>
                        <span style="margin-left: 5px; color: ${inv1.query === inv2.query ? 'var(--green)' : 'var(--yellow)'}; font-weight: 500;">
                            ${inv1.query === inv2.query ? 'Yes' : 'No'}
                        </span>
                    </div>
                </div>
            </div>
            ${diffSection}
        `;

        modal.style.display = 'flex';
    } catch (e) {
        showToast('Failed to compare: ' + e.message);
    }
};

// ============================================================================
// Export Module
// ============================================================================

export {
    loadInvestigations,
    loadInvestigationStats,
    loadAllTags,
    loadSavedSearches
};
