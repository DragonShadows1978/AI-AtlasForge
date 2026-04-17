/**
 * Dashboard Widgets Module (ES6)
 * AtlasForge widgets, analytics, file handling, collapsible cards, journal
 * Dependencies: core.js, api.js
 */

import { showToast, escapeHtml, formatBytes, formatNumber, formatTimeAgo, stages, downloadFileViaFetch } from './core.js';
import { handleMissionAgentEvent, handleInvestigationAgentEvent } from './modules/agent-activity.js';
import { initSubagentPoolWidget, handlePoolStatusEvent } from './modules/subagent-pool.js';
import { api } from './api.js';
import { setFullMissionText } from './modals.js';
import { getConnectionState, forceReconnect } from './socket.js';

// =============================================================================
// STATE
// =============================================================================

let fullMissionText = '';
let analyticsData = null;

// Track which data has been received via WebSocket initial_data to skip duplicate REST calls
const _wsInitialReceived = new Set();

// =============================================================================
// ACTIVITY LOG MESSAGE STYLING (Cycle 3)
// Parse [RESTART:*], [ERROR:*], [FATAL], [CONTEXT] prefixes for visual styling
// =============================================================================

/**
 * Parse message content to determine its type and styling class
 * @param {string} content - The message content
 * @returns {object} - { class: string, icon: string, displayType: string, cleanContent: string }
 */
function parseActivityMessageType(content) {
    if (!content) return { class: 'msg-default', icon: '', displayType: '', cleanContent: content || '' };

    // Check for [RESTART:*] prefix - graceful handoffs
    const restartMatch = content.match(/^\[RESTART:([A-Z_]+)\]/);
    if (restartMatch) {
        const restartType = restartMatch[1];
        return {
            class: 'msg-restart',
            icon: '↻',
            displayType: `RESTART:${restartType}`,
            cleanContent: content.substring(restartMatch[0].length).trim()
        };
    }

    // Check for [ERROR:*] prefix - retriable errors
    const errorMatch = content.match(/^\[ERROR:([A-Z_]+)\]/);
    if (errorMatch) {
        const errorType = errorMatch[1];
        return {
            class: 'msg-error',
            icon: '⚠',
            displayType: `ERROR:${errorType}`,
            cleanContent: content.substring(errorMatch[0].length).trim()
        };
    }

    // Check for [FATAL] prefix - mission halted
    if (content.startsWith('[FATAL]')) {
        return {
            class: 'msg-fatal',
            icon: '✖',
            displayType: 'FATAL',
            cleanContent: content.substring(7).trim()
        };
    }

    // Check for [CONTEXT] prefix - context usage info
    if (content.startsWith('[CONTEXT]')) {
        return {
            class: 'msg-context',
            icon: '📊',
            displayType: 'CONTEXT',
            cleanContent: content.substring(9).trim()
        };
    }

    // Check for [HAIKU] prefix - Haiku model processing
    if (content.startsWith('[HAIKU]')) {
        return {
            class: 'msg-haiku',
            icon: '🤖',
            displayType: 'HAIKU',
            cleanContent: content.substring(7).trim()
        };
    }

    // Default - no special styling
    return {
        class: 'msg-default',
        icon: '•',
        displayType: '',
        cleanContent: content
    };
}

/**
 * Render a journal entry with appropriate message type styling
 * @param {object} j - Journal entry object
 * @returns {string} - HTML string for the entry
 */
function renderStyledJournalEntry(j) {
    const message = j.message || j.status || '';
    const fullMessage = j.full_message || message;
    const parsed = parseActivityMessageType(message);
    const parsedFull = parseActivityMessageType(fullMessage);
    const isTruncated = j.is_truncated;

    // Use parsed displayType if available, otherwise fall back to j.type
    const displayType = parsed.displayType || j.type || 'unknown';

    if (isTruncated) {
        return `
            <div class="journal-entry expandable ${parsed.class}" data-entry-id="${escapeHtml(j.timestamp || '')}" onclick="window.toggleJournalEntry(this)">
                <span class="journal-type">${parsed.icon ? `<span class="msg-icon">${escapeHtml(parsed.icon)}</span>` : ''}${escapeHtml(displayType)}</span>
                <span class="journal-time">${j.timestamp ? new Date(j.timestamp).toLocaleTimeString() : ''}</span>
                <div class="preview-message journal-message">${escapeHtml(parsed.cleanContent)}...<span class="expand-indicator">[+]</span></div>
                <div class="full-message journal-message">${escapeHtml(parsedFull.cleanContent)}<span class="collapse-indicator">[-]</span></div>
            </div>
        `;
    } else {
        return `
            <div class="journal-entry ${parsed.class}" data-entry-id="${escapeHtml(j.timestamp || '')}">
                <span class="journal-type">${parsed.icon ? `<span class="msg-icon">${escapeHtml(parsed.icon)}</span>` : ''}${escapeHtml(displayType)}</span>
                <span class="journal-time">${j.timestamp ? new Date(j.timestamp).toLocaleTimeString() : ''}</span>
                <div class="journal-message">${escapeHtml(parsed.cleanContent)}</div>
            </div>
        `;
    }
}

// Export for use in other modules
export { parseActivityMessageType, renderStyledJournalEntry };

// =============================================================================
// COLLAPSIBLE CARD FUNCTIONALITY
// =============================================================================

export function toggleCard(cardId) {
    const card = document.getElementById(cardId + '-card');
    if (card) {
        const content = card.querySelector('.card-content');
        card.classList.toggle('collapsed');
        saveCardState(cardId, card.classList.contains('collapsed'));
        if (card.classList.contains('collapsed')) {
            // Explicitly set 0 so the CSS transition animates from current height to 0.
            // After animation, remove the inline style so CSS class rule fully owns it.
            if (content) {
                content.style.maxHeight = '0px';
                content.style.opacity = '0';
                setTimeout(() => {
                    content.style.removeProperty('max-height');
                    content.style.removeProperty('opacity');
                }, 300); // matches --dur-slow CSS token
            }
        } else {
            // Expanding: recalculate and apply height
            if (content) {
                setTimeout(() => _setCardHeight(content,
                    window.visualViewport?.height || window.innerHeight), 50);
            }
        }
    }
}

function saveCardState(cardId, collapsed) {
    try {
        const states = JSON.parse(localStorage.getItem('cardStates') || '{}');
        states[cardId] = collapsed;
        localStorage.setItem('cardStates', JSON.stringify(states));
    } catch (e) {}
}

export function loadCardStates() {
    try {
        const states = JSON.parse(localStorage.getItem('cardStates') || '{}');
        for (const [cardId, collapsed] of Object.entries(states)) {
            if (collapsed) {
                const card = document.getElementById(cardId + '-card');
                if (card) card.classList.add('collapsed');
            }
        }
    } catch (e) {}
}
// =============================================================================
// DYNAMIC CARD HEIGHT SYSTEM
// =============================================================================

const WIDGET_HEIGHT_OVERRIDES = {
    'queue': 0.70,
    'mission-suggestions': 0.70,
    'recommendations-widget': 0.65,
    'files': 0.50,
};
const DEFAULT_HEIGHT_FRACTION = 0.60;

export function initDynamicCardHeights() {
    applyDynamicHeights();
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(applyDynamicHeights, 150);
    });
}

export function applyDynamicHeights() {
    const viewportH = window.visualViewport?.height || window.innerHeight;
    document.querySelectorAll('.card:not(.collapsed) .card-content').forEach(el => {
        _setCardHeight(el, viewportH);
    });
}

function _setCardHeight(el, viewportH) {
    const card = el.closest('.card');
    const cardId = card?.id?.replace('-card', '') || '';
    const fraction = WIDGET_HEIGHT_OVERRIDES[cardId] ?? DEFAULT_HEIGHT_FRACTION;
    // Mobile: cap fraction at 0.45 on narrow viewports (< 768px)
    const effectiveFraction = window.innerWidth < 768 ? Math.min(fraction, 0.45) : fraction;
    const cap = viewportH * effectiveFraction;

    // Lift constraint to measure natural height
    el.style.transition = 'none';
    el.style.maxHeight = 'none';
    const naturalH = el.scrollHeight;

    // Apply min(content, cap)
    el.style.maxHeight = Math.min(naturalH, cap) + 'px';
    // Double-rAF: ensures maxHeight is committed to render pipeline before
    // re-enabling transition, preventing any edge-case same-frame measurement+transition.
    requestAnimationFrame(() => {
        requestAnimationFrame(() => { el.style.transition = ''; });
    });
}

export function recalcCardHeight(cardId) {
    const card = document.getElementById(cardId + '-card');
    if (!card || card.classList.contains('collapsed')) return;
    const content = card.querySelector('.card-content');
    if (content) _setCardHeight(content, window.visualViewport?.height || window.innerHeight);
}

// =============================================================================
// JOURNAL FUNCTIONS
// =============================================================================

export function renderJournalEntries(entries) {
    const container = document.getElementById('journal-entries');
    if (!container) return;

    const expandedStates = loadJournalExpandedStates();

    container.innerHTML = entries.map((e, idx) => {
        const isExpanded = expandedStates[idx] || false;
        const content = e.full_message || e.message || '';
        const stage = e.stage || e.status || e.type || 'UNKNOWN';
        const shouldTruncate = content.length > 300;
        const displayContent = shouldTruncate && !isExpanded
            ? content.substring(0, 300) + '...'
            : content;

        return `
            <div class="journal-entry ${isExpanded ? 'expanded' : ''}" data-index="${idx}"
                 onclick="window.toggleJournalEntry(this)">
                <div class="journal-timestamp">${e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ''}</div>
                <div class="journal-stage">${escapeHtml(stage)}</div>
                <div class="journal-message">${escapeHtml(displayContent)}</div>
                ${shouldTruncate ? '<div class="journal-expand-hint">Click to ' + (isExpanded ? 'collapse' : 'expand') + '</div>' : ''}
            </div>
        `;
    }).join('');
}

export function toggleJournalEntry(el) {
    el.classList.toggle('expanded');
    saveJournalExpandedStates();
}

function saveJournalExpandedStates() {
    try {
        const expanded = [];
        document.querySelectorAll('.journal-entry.expanded').forEach(el => {
            const id = el.dataset.entryId;
            if (id) expanded.push(id);
        });
        localStorage.setItem('journalExpandedEntries', JSON.stringify(expanded));
    } catch (e) {}
}

function loadJournalExpandedStates() {
    try {
        return JSON.parse(localStorage.getItem('journalExpandedEntries') || '[]');
    } catch (e) {
        return [];
    }
}

function getJournalExpandedStates() {
    try {
        return JSON.parse(localStorage.getItem('journalExpandedEntries') || '[]');
    } catch (e) {
        return [];
    }
}

function restoreJournalExpandedStates() {
    const expandedIds = getJournalExpandedStates();
    if (expandedIds.length === 0) return;

    const expandedSet = new Set(expandedIds);
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        const id = el.dataset.entryId;
        if (expandedSet.has(id)) {
            el.classList.add('expanded');
        }
    });
}

export function expandAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.add('expanded');
    });
    saveJournalExpandedStates();
}

export function collapseAllJournal() {
    document.querySelectorAll('.journal-entry.expandable').forEach(el => {
        el.classList.remove('expanded');
    });
    saveJournalExpandedStates();
}

// =============================================================================
// CONTROLS
// =============================================================================

function normalizeProvider(provider) {
    const value = (provider || '').toString().trim().toLowerCase();
    if (value === 'codex' || value === 'gemini') {
        return value;
    }
    return 'claude';
}

function getProviderSelectElements() {
    const ids = ['llm-provider-select', 'investigation-llm-provider-select'];
    return ids
        .map((id) => document.getElementById(id))
        .filter(Boolean);
}

export async function setLlmProvider(provider, showSuccessToast = true) {
    const selectEls = getProviderSelectElements();
    const normalized = normalizeProvider(provider);

    try {
        selectEls.forEach((selectEl) => {
            selectEl.disabled = true;
        });
        const result = await api('/api/llm-provider', 'POST', { provider: normalized });
        if (!result.success) {
            showToast(result.message || 'Failed to set provider', 'error');
            return;
        }
        if (showSuccessToast) {
            showToast(result.message || `Provider set to ${result.provider}`, 'success');
        }
    } catch (e) {
        console.error('Failed to set provider:', e);
        showToast('Failed to set provider', 'error');
    } finally {
        selectEls.forEach((selectEl) => {
            selectEl.disabled = false;
        });
    }

    await refresh();
}

async function syncProviderControls(statusProvider) {
    const selectEls = getProviderSelectElements();
    if (selectEls.length === 0) return;

    selectEls.forEach((selectEl) => {
        if (!selectEl.dataset.wired) {
            selectEl.dataset.wired = '1';
            selectEl.addEventListener('change', async (event) => {
                await setLlmProvider(event.target.value, true);
            });
        }
    });

    let provider = statusProvider;
    if (!provider) {
        try {
            const providerData = await api('/api/llm-provider');
            provider = providerData?.provider;
        } catch (e) {
            provider = 'claude';
        }
    }

    const normalized = normalizeProvider(provider);
    selectEls.forEach((selectEl) => {
        if (selectEl.value !== normalized) {
            selectEl.value = normalized;
        }
    });
}

export async function startClaude(mode) {
    // Get mission input text
    const missionInput = document.getElementById('mission-input');
    const missionText = missionInput ? missionInput.value.trim() : '';

    // Get current mission state
    let currentMission = {};
    try {
        currentMission = await api('/api/mission', 'GET');
    } catch (e) {
        console.error('Failed to get current mission:', e);
    }
    const currentStage = currentMission.current_stage || 'COMPLETE';
    const isComplete = (currentStage === 'COMPLETE' || currentStage === '' || !currentStage);

    if (missionText) {
        // Case 1: Text box has content - create new mission and start
        const _cbParsed0 = parseInt(document.getElementById('cycle-budget-input')?.value);
        const cycleBudget = Number.isNaN(_cbParsed0) ? 1 : _cbParsed0;
        const projectNameInput = document.getElementById('project-name-input');
        const projectName = projectNameInput ? projectNameInput.value.trim() : '';

        // If replacing an active mission, ask for confirmation
        if (!isComplete) {
            const confirm1 = confirm(
                `Current mission is in stage: ${currentStage}\n\n` +
                `This will OVERWRITE the current mission and start the new one!\n\n` +
                `Are you sure?`
            );
            if (!confirm1) return;

            const confirm2 = confirm(
                `SECOND CONFIRMATION\n\n` +
                `You are about to PERMANENTLY overwrite:\n` +
                `"${(currentMission.problem_statement || '').substring(0, 100)}..."\n\n` +
                `This cannot be undone. Proceed?`
            );
            if (!confirm2) return;
        }

        // Create the new mission
        const maxIterations = parseInt(document.getElementById('max-iterations-input')?.value) || 10;
        const payload = { mission: missionText.substring(0, 5000), cycle_budget: cycleBudget, max_iterations: maxIterations };
        if (projectName) payload.project_name = projectName;

        const setResult = await api('/api/mission', 'POST', payload);
        if (!setResult.success) {
            showToast(`Failed to set mission: ${setResult.message}`, 'error');
            return;
        }

        // Clear inputs
        missionInput.value = '';
        if (projectNameInput) projectNameInput.value = '';

        // Now start Claude
        const startResult = await api(`/api/start/${mode}`, 'POST');
        showToast(`Mission set and started: ${startResult.message}`, 'success');
        refresh();

        // Auto-reconnect WebSocket if disconnected so live updates flow immediately
        const connState = getConnectionState();
        if (connState.main !== 'connected' || connState.widgets !== 'connected') {
            console.log('[Mission] Socket disconnected after submit, forcing reconnect...');
            forceReconnect();
        }

    } else if (!isComplete) {
        // Case 2: Empty text box, mission in progress - restart/resume
        const data = await api(`/api/start/${mode}`, 'POST');
        showToast(data.message);
        refresh();

    } else {
        // Case 3: Empty text box, no active mission - error
        showToast('No mission to start. Enter a mission description first.', 'error');
    }
}

export async function stopClaude() {
    const data = await api('/api/stop', 'POST');
    showToast(data.message);
    refresh();
}

export async function setMission() {
    const missionInput = document.getElementById('mission-input');
    const mission = missionInput?.value?.trim() ?? '';
    if (!mission) return;

    const _cbInput = document.getElementById('cycle-budget-input');
    if (!_cbInput) console.warn('setMission: cycle-budget-input element not found');
    const _cbParsed1 = parseInt(_cbInput?.value);
    const cycleBudget = Number.isNaN(_cbParsed1) ? 1 : _cbParsed1;
    const projectNameInput = document.getElementById('project-name-input');
    if (!projectNameInput) console.warn('setMission: project-name-input element not found');
    const projectName = projectNameInput ? projectNameInput.value.trim() : '';

    let currentMission;
    try {
        currentMission = await api('/api/mission', 'GET');
    } catch (err) {
        console.warn('setMission: failed to fetch current mission', err);
        currentMission = {};
    }
    const currentStage = currentMission?.current_stage || 'COMPLETE';

    if (currentStage !== 'COMPLETE') {
        const confirm1 = confirm(
            `Current mission is in stage: ${currentStage}\n\n` +
            `This will OVERWRITE the current mission!\n\n` +
            `Are you sure you want to replace it?`
        );
        if (!confirm1) return;

        const confirm2 = confirm(
            `SECOND CONFIRMATION\n\n` +
            `You are about to PERMANENTLY overwrite:\n` +
            `"${(currentMission.problem_statement || '').substring(0, 100)}..."\n\n` +
            `This cannot be undone. Proceed?`
        );
        if (!confirm2) return;
    }

    const maxIterations = parseInt(document.getElementById('max-iterations-input')?.value) || 10;
    const payload = {mission: mission.substring(0, 5000), cycle_budget: cycleBudget, max_iterations: maxIterations};
    if (projectName) {
        payload.project_name = projectName;
    }

    const data = await api('/api/mission', 'POST', payload);
    showToast(data.message);
    if (missionInput) missionInput.value = '';
    if (projectNameInput) projectNameInput.value = '';
    refresh();

    // Auto-reconnect WebSocket if disconnected so live updates flow immediately
    const smConnState = getConnectionState();
    if (smConnState.main !== 'connected' || smConnState.widgets !== 'connected') {
        console.log('[Mission] Socket disconnected after setMission, forcing reconnect...');
        forceReconnect();
    }
}

export async function resetMission() {
    const data = await api('/api/mission/reset', 'POST');
    showToast(data.message);
    refresh();
}

export async function queueMission() {
    // Get mission input text
    const missionInput = document.getElementById('mission-input');
    const missionText = missionInput ? missionInput.value.trim() : '';

    if (!missionText) {
        showToast('Enter a mission description to queue.', 'error');
        return;
    }

    const _cbParsed2 = parseInt(document.getElementById('cycle-budget-input')?.value);
    const cycleBudget = Number.isNaN(_cbParsed2) ? 1 : _cbParsed2;
    const maxIterations = parseInt(document.getElementById('max-iterations-input')?.value) || 10;
    const projectNameInput = document.getElementById('project-name-input');
    const projectName = projectNameInput ? projectNameInput.value.trim() : '';

    // Add to queue via API
    try {
        const payload = {
            problem_statement: missionText.substring(0, 5000),
            cycle_budget: cycleBudget,
            max_iterations: maxIterations,
            priority: 0,
            source: 'dashboard'
        };
        if (projectName) payload.project_name = projectName;

        const data = await api('/api/queue/add', 'POST', payload);
        if (data.status === 'added') {
            showToast(`Mission queued at position ${data.queue_length}`, 'success');
            // Clear inputs
            missionInput.value = '';
            if (projectNameInput) projectNameInput.value = '';
            // Immediately refresh queue widget (WebSocket may have delay)
            if (typeof window.refreshQueueWidget === 'function') {
                await window.refreshQueueWidget();
            }
            refresh();
        } else if (data.error) {
            showToast(`Failed to queue: ${data.error}`, 'error');
        }
    } catch (e) {
        console.error('Failed to queue mission:', e);
        showToast('Failed to queue mission', 'error');
    }
}

// =============================================================================
// MISSION FILE UPLOAD
// =============================================================================

export function uploadMissionFile() {
    const fileInput = document.getElementById('mission-file-input');
    if (!fileInput) return;
    fileInput.click();
}

export function handleMissionFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // BUG-2: Enforce 500 KB size limit to prevent memory/DoS issues
    if (file.size > 500_000) {
        showToast('File too large (max 500 KB)', 'error');
        input.value = '';
        return;
    }

    // BUG-3 (hardened): Allowlist MIME types — empty MIME ('') also rejected to prevent bypass
    // Explicit allowlist — no trailing 'text/' catch-all to prevent text/html from matching.
    const ALLOWED_MIME_PREFIXES = ['text/plain', 'text/markdown', 'text/x-markdown', 'text/csv', 'text/yaml'];
    const mimeOk = file.type === '' ? false : ALLOWED_MIME_PREFIXES.some(m => file.type.startsWith(m));
    // Empty MIME (unknown extension) — allow only if extension is .txt or .md
    const nameOk = /\.(txt|md|markdown|rst|csv|log|yaml|yml|json|xml|html|htm|js|ts|py|sh|css)$/i.test(file.name);
    if (!mimeOk && !nameOk) {
        showToast('Only text files are supported (.txt, .md, etc.)', 'error');
        input.value = '';
        return;
    }

    // BUG-5: Disable button while read is in progress (race condition guard)
    const uploadBtn = document.getElementById('upload-mission-btn') ||
                      document.querySelector('button[onclick="uploadMissionFile()"]');
    if (uploadBtn) uploadBtn.disabled = true;

    const reader = new FileReader();
    reader.onload = function(e) {
        const content = e.target.result;
        // BUG-4 (fixed order): Check whitespace BEFORE populating textarea
        if (!content.trim()) {
            showToast(`"${file.name}" appears empty or whitespace-only`, 'warning');
            input.value = '';
            if (uploadBtn) uploadBtn.disabled = false;
            return;
        }
        const missionInput = document.getElementById('mission-input');
        if (missionInput) {
            missionInput.value = content;
            missionInput.dispatchEvent(new Event('input')); // trigger project name suggestion
        }
        showToast(`Loaded "${file.name}" (${content.length} chars)`, 'success');
        input.value = ''; // reset so same file can be re-uploaded
        if (uploadBtn) uploadBtn.disabled = false;
    };
    reader.onabort = function() {
        if (uploadBtn) uploadBtn.disabled = false;
    };
    reader.onerror = function() {
        showToast(`Failed to read file: ${file.name}`, 'error');
        input.value = '';
        if (uploadBtn) uploadBtn.disabled = false;
    };
    reader.readAsText(file);
}

// =============================================================================
// PROJECT NAME SUGGESTION
// =============================================================================

let projectSuggestTimeout = null;

/**
 * Initialize project name suggestion on mission input
 * Call this after DOM is ready
 */
export function initProjectNameSuggestion() {
    const missionInput = document.getElementById('mission-input');
    const projectInput = document.getElementById('project-name-input');

    if (!missionInput || !projectInput) return;

    missionInput.addEventListener('input', function() {
        clearTimeout(projectSuggestTimeout);

        const text = this.value.trim();
        if (text.length < 15) {
            projectInput.placeholder = 'Auto-detect from mission';
            return;
        }

        // Debounce - wait 500ms after typing stops
        projectSuggestTimeout = setTimeout(async () => {
            try {
                const result = await api('/api/suggest-project-name', 'POST', {
                    problem_statement: text.substring(0, 5000)
                });

                if (result.suggested_name && !projectInput.value) {
                    projectInput.placeholder = `Suggested: ${result.suggested_name}`;
                    // Store suggestion for use if field left empty
                    projectInput.dataset.suggested = result.suggested_name;
                }
            } catch (e) {
                console.log('Project name suggestion failed:', e);
            }
        }, 500);
    });

    // Clear suggestion when user types in project name field
    projectInput.addEventListener('input', function() {
        if (this.value) {
            this.placeholder = 'Auto-detect from mission';
            delete this.dataset.suggested;
        }
    });
}

// =============================================================================
// FILE HANDLING
// =============================================================================

// =============================================================================
// DATA APPLICATION HELPERS (used by parallel refresh())
// =============================================================================

/**
 * Apply pre-fetched files data to the files widget.
 * Called by refresh() after parallel Promise.all fetch.
 */
function _applyFilesData(files) {
    try {
        if (!Array.isArray(files)) return;
        const container = document.getElementById('files-list');
        const countEl = document.getElementById('files-count');
        if (countEl) countEl.textContent = files.length;
        if (!container) return;
        if (files.length === 0) {
            container.innerHTML = '<div class="no-files">No files yet</div>';
            return;
        }
        container.innerHTML = files.slice(0, 20).map(f => `
            <div class="file-item">
                <div class="file-info">
                    <a href="#" class="download-link file-name"
                       data-content-url="${escapeHtml(f.content_url || '')}"
                       data-download-url="${escapeHtml(f.download_url || '')}"
                       data-filename="${escapeHtml(f.name || '')}"
                       data-file-type="${escapeHtml(f.file_type || 'binary')}"
                       title="${escapeHtml(f.path || '')}">${escapeHtml(f.name || '')}</a>
                    <span class="file-meta">${formatBytes(f.size)} - ${formatTimeAgo(f.modified)}</span>
                </div>
            </div>
        `).join('');
        container.querySelectorAll('.download-link[data-content-url]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                openFilePreviewModal(
                    link.dataset.filename,
                    link.dataset.contentUrl,
                    link.dataset.downloadUrl,
                    link.dataset.fileType
                );
            });
        });
    } catch (e) {
        console.warn('[Widgets] _applyFilesData error:', e);
    }
}

/**
 * Apply pre-fetched AtlasForge exploration data to widgets.
 * Called by refresh() after parallel Promise.all fetch.
 */
function _applyAtlasForgeData(data) {
    try {
        if (!data || data.error) return;
        if (data.exploration) {
            const fileCount = (data.exploration.nodes_by_type || {}).file || 0;
            const filesEl = document.getElementById('atlasforge-files-count');
            const insightsEl = document.getElementById('atlasforge-insights-count');
            const edgesEl = document.getElementById('atlasforge-edges-count');
            if (filesEl) filesEl.textContent = fileCount;
            if (insightsEl) insightsEl.textContent = data.exploration.total_insights || 0;
            if (edgesEl) edgesEl.textContent = data.exploration.total_edges || 0;
        }
        const coverage = data.coverage_pct || 0;
        const coveragePctEl = document.getElementById('atlasforge-coverage-pct');
        const coverageBarEl = document.getElementById('atlasforge-coverage-bar');
        if (coveragePctEl) coveragePctEl.textContent = coverage + '%';
        if (coverageBarEl) coverageBarEl.style.width = coverage + '%';
        if (typeof updateDriftChart === 'function') updateDriftChart(data.drift_history || []);
        if (typeof updateRecentExplorations === 'function') updateRecentExplorations(data.recent_explorations || []);
    } catch (e) {
        console.warn('[Widgets] _applyAtlasForgeData error:', e);
    }
}


export async function loadFiles() {
    try {
        const files = await api('/api/files');
        const container = document.getElementById('files-list');
        if (!container) return;
        const countEl = document.getElementById('files-count');
        if (countEl) countEl.textContent = files.length;

        if (files.length === 0) {
            container.innerHTML = '<div class="no-files">No files yet</div>';
            return;
        }

        container.innerHTML = files.slice(0, 20).map(f => `
            <div class="file-item">
                <div class="file-info">
                    <a href="#" class="download-link file-name"
                       data-content-url="${escapeHtml(f.content_url || '')}"
                       data-download-url="${escapeHtml(f.download_url || '')}"
                       data-filename="${escapeHtml(f.name || '')}"
                       data-file-type="${escapeHtml(f.file_type || 'binary')}"
                       title="${escapeHtml(f.path || '')}">${escapeHtml(f.name || '')}</a>
                    <span class="file-meta">${formatBytes(f.size)} - ${formatTimeAgo(f.modified)}</span>
                </div>
            </div>
        `).join('');

        // Open preview modal on click instead of downloading
        container.querySelectorAll('.download-link[data-content-url]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                openFilePreviewModal(
                    link.dataset.filename,
                    link.dataset.contentUrl,
                    link.dataset.downloadUrl,
                    link.dataset.fileType
                );
            });
        });
    } catch (e) {
        console.error('Error loading files:', e);
    }
}

// =============================================================================
// FILE PREVIEW MODAL
// =============================================================================

let _fpCurrentDownloadUrl = null;
let _fpCurrentTextContent = null;

export function openFilePreviewModal(name, contentUrl, downloadUrl, fileType) {
    const modal = document.getElementById('file-preview-modal');
    if (!modal) return;
    const title = document.getElementById('file-preview-title');
    const body = document.getElementById('file-preview-body');
    const copyBtn = document.getElementById('file-preview-copy-btn');
    const dlBtn = document.getElementById('file-preview-download-btn');

    title.textContent = name;
    body.innerHTML = '<div class="file-preview-loading">Loading\u2026</div>';
    copyBtn.style.display = 'none';
    dlBtn.href = downloadUrl;
    _fpCurrentDownloadUrl = downloadUrl;
    _fpCurrentTextContent = null;

    modal.style.display = 'flex';
    document.body.classList.add('modal-open');

    const _fpController = new AbortController();
    const _fpTimeout = setTimeout(() => _fpController.abort(), 30000);

    fetch(contentUrl, { signal: _fpController.signal })
        .then(r => {
            clearTimeout(_fpTimeout);
            if (!r.ok) throw new Error(`Server returned ${r.status}: ${r.statusText}`);
            return r.json();
        })
        .then(data => {
            if (data.error) throw new Error(data.error);
            if (data.file_type === 'image') {
                body.innerHTML = `<img src="data:${data.mime_type};base64,${data.content}" alt="${_escFp(name)}">`;
                copyBtn.style.display = 'none';
            } else if (data.file_type === 'text') {
                const truncNote = data.truncated
                    ? '<div class="file-preview-truncated">\u26a0 File truncated \u2014 showing first 100\u202fKB</div>'
                    : '';
                body.innerHTML = `<pre>${_escFp(data.content)}</pre>${truncNote}`;
                _fpCurrentTextContent = data.content;
                copyBtn.style.display = '';
            } else {
                body.innerHTML = `<div class="file-preview-binary">
                    <p>Binary file \u2014 cannot display inline.</p>
                    <p style="color:var(--text-dim);font-size:0.85em;">${_escFp(name)}</p>
                </div>`;
                copyBtn.style.display = 'none';
            }
        })
        .catch(err => {
            clearTimeout(_fpTimeout);
            const msg = err.name === 'AbortError' ? 'Request timed out' : (err.message || 'Unknown error');
            body.innerHTML = `<div class="file-preview-binary">Error loading file: ${_escFp(msg)}</div>`;
        });
}

export function closeFilePreviewModal() {
    const modal = document.getElementById('file-preview-modal');
    if (modal) modal.style.display = 'none';
    _fpCurrentTextContent = null;
    _fpCurrentDownloadUrl = null;
    const anyOpen = document.querySelectorAll('.modal.show, .modal[style*="display: flex"], .modal[style*="display:flex"], .restart-modal-overlay.visible').length > 0;
    if (!anyOpen) document.body.classList.remove('modal-open');
}

export function copyFileContent() {
    if (!_fpCurrentTextContent) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(_fpCurrentTextContent)
            .then(() => showToast('Copied to clipboard', 'success'))
            .catch(() => _fpFallbackCopy(_fpCurrentTextContent));
    } else {
        _fpFallbackCopy(_fpCurrentTextContent);
    }
}

export function downloadCurrentFile() {
    if (_fpCurrentDownloadUrl) {
        downloadFileViaFetch(_fpCurrentDownloadUrl, null).catch(() => {});
    }
    return false;
}

function _fpFallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
        document.execCommand('copy');
        showToast('Copied to clipboard', 'success');
    } catch (e) {
        showToast('Copy failed', 'error');
    }
    document.body.removeChild(ta);
}

function _escFp(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// =============================================================================
// STAGE INDICATOR
// =============================================================================

export function updateStageIndicator(currentStage) {
    const stageEls = document.querySelectorAll('.stage');
    const currentIdx = stages.indexOf(currentStage);

    stageEls.forEach((el, idx) => {
        el.classList.remove('active', 'complete');
        if (idx < currentIdx) el.classList.add('complete');
        else if (idx === currentIdx) el.classList.add('active');
    });
}

// =============================================================================
// STATUS BAR UPDATE
// =============================================================================

export function updateStatusBar(data) {
    // Update AtlasForge service status indicator in header
    updateAtlasForgeServiceStatus(data.running, data.mode);

    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    // stat-mode removed - only R&D mode exists
    setEl('stat-stage', data.rd_stage || '-');
    setEl('stat-project-name', data.project_name || '-');
    setEl('stat-provider', normalizeProvider(data.provider));
    setEl('stat-iteration', data.rd_iteration);
    setEl('stat-mission-cycle', `${data.current_cycle || 1}/${data.cycle_budget || 1}`);
    setEl('stat-cycles', data.total_cycles);
    setEl('stat-boots', data.boot_count);

    updateStageIndicator(data.rd_stage);
}

/**
 * Update the AtlasForge service status indicator in the header
 * @param {boolean} running - Whether AtlasForge is running
 * @param {string} mode - Current mode (rd, free, etc.)
 */
export function updateAtlasForgeServiceStatus(running, mode) {
    const container = document.getElementById('atlasforge-service-status');
    const stateEl = document.getElementById('atlasforge-service-state');

    if (!container || !stateEl) return;

    // Determine status class and text
    container.classList.remove('online', 'offline', 'busy', 'working');

    if (running) {
        // Use 'working' class for breathing animation when active
        container.classList.add('working');
        stateEl.textContent = 'Working';
    } else {
        container.classList.add('offline');
        stateEl.textContent = 'Offline';
    }
}

/**
 * Update the Investigation service status indicator in the header
 * @param {boolean} running - Whether Investigation is running
 * @param {string} status - Investigation status (analyzing, exploring, etc.)
 */
export function updateInvestigationServiceStatus(running, status) {
    const container = document.getElementById('investigation-service-status');
    const stateEl = document.getElementById('investigation-service-state');

    if (!container || !stateEl) return;

    // Determine status class and text
    container.classList.remove('online', 'offline', 'busy');

    if (running) {
        // Map status to display values
        const statusLabels = {
            'pending': 'Pending',
            'analyzing': 'Analyzing',
            'spawning_subagents': 'Spawning',
            'exploring': 'Exploring',
            'synthesizing': 'Synthesizing',
            'completed': 'Complete',
            'failed': 'Failed'
        };

        const displayStatus = statusLabels[status] || status || 'Running';

        if (status === 'completed') {
            container.classList.add('online');
        } else if (status === 'failed') {
            container.classList.add('offline');
        } else {
            container.classList.add('busy');
        }
        stateEl.textContent = displayStatus;
    } else {
        container.classList.add('offline');
        stateEl.textContent = 'Offline';
    }
}

// =============================================================================
// TERMINAL SERVICE STATUS (Port 5002)
// =============================================================================

let pendingRestartService = null;

/**
 * Update the Terminal service status indicator in the service bar
 * @param {boolean} online - Whether service is online
 * @param {boolean} restarting - Whether service is currently restarting
 */
export function updateTerminalServiceStatus(online, restarting = false) {
    const container = document.getElementById('terminal-service');
    if (!container) return;

    container.classList.remove('online', 'offline', 'restarting');

    if (restarting) {
        container.classList.add('restarting');
    } else if (online) {
        container.classList.add('online');
    } else {
        container.classList.add('offline');
    }
}

/**
 * Fetch and update all service statuses
 */
export async function refreshServiceStatuses() {
    try {
        const statuses = await api('/api/services/status');
        if (statuses.terminal) {
            updateTerminalServiceStatus(statuses.terminal.online);
        }
    } catch (e) {
        console.error('Failed to fetch service statuses:', e);
        updateTerminalServiceStatus(false);
    }
}

/**
 * Show restart confirmation modal
 * @param {string} serviceId - Service to restart
 */
export function showRestartModal(serviceId) {
    pendingRestartService = serviceId;
    const overlay = document.getElementById('restart-modal-overlay');
    const textEl = document.getElementById('restart-modal-text');

    const serviceNames = {
        'terminal': 'Web Terminal service on port 5002'
    };

    textEl.textContent = `Are you sure you want to restart the ${serviceNames[serviceId] || serviceId}?`;
    overlay.classList.add('visible');
}

/**
 * Hide restart confirmation modal
 */
export function hideRestartModal() {
    pendingRestartService = null;
    const overlay = document.getElementById('restart-modal-overlay');
    overlay.classList.remove('visible');
}

/**
 * Confirm and execute service restart
 */
export async function confirmRestart() {
    if (!pendingRestartService) return;

    const serviceId = pendingRestartService;
    hideRestartModal();

    // Show restarting state
    updateTerminalServiceStatus(false, true);
    showToast(`Restarting ${serviceId} service...`, 'info');

    try {
        const result = await api(`/api/services/restart/${serviceId}`, 'POST');

        if (result.success) {
            showToast(result.message, 'success');
            updateTerminalServiceStatus(true);
        } else {
            showToast(result.error || 'Failed to restart service', 'error');
            updateTerminalServiceStatus(false);
        }
    } catch (e) {
        console.error('Failed to restart service:', e);
        showToast('Failed to restart service: ' + e.message, 'error');
        updateTerminalServiceStatus(false);
    }

    // Refresh status after a short delay
    setTimeout(refreshServiceStatuses, 2000);
}

// =============================================================================
// MAIN REFRESH
// =============================================================================

export async function refresh() {
    try {
        // Skip REST calls for data already received via WebSocket initial_data
        const wsHasStatus = _wsInitialReceived.has('mission_status');

        // Only fetch what WebSocket hasn't already provided
        const [data, invStatus, journal, files, atlasData] = await Promise.all([
            wsHasStatus ? Promise.resolve(null) : api('/api/status'),
            api('/api/investigation/status').catch(() => null),
            wsHasStatus ? Promise.resolve(null) : api('/api/journal').catch(() => []),
            api('/api/files').catch(() => []),
            api('/api/atlasforge/exploration-stats').catch(() => ({}))
        ]);

        // If WebSocket already handled status, skip the REST-driven DOM updates
        if (!data) {
            // Still update files and atlas data from REST
            _applyFilesData(files);
            _applyAtlasForgeData(atlasData);

            // Investigation status still needs REST (no WS room for it)
            try {
                const isInvRunning = invStatus && invStatus.investigation_id &&
                    invStatus.status !== 'completed' && invStatus.status !== 'failed' && invStatus.status !== 'idle';
                updateInvestigationServiceStatus(isInvRunning, invStatus?.status);
            } catch (e) {
                updateInvestigationServiceStatus(false, null);
            }
            return;
        }

        // Update AtlasForge service status in header
        updateAtlasForgeServiceStatus(data.running, data.mode);

        // Update Investigation status from parallel result
        try {
            const isInvRunning = invStatus && invStatus.investigation_id &&
                invStatus.status !== 'completed' && invStatus.status !== 'failed' && invStatus.status !== 'idle';
            updateInvestigationServiceStatus(isInvRunning, invStatus?.status);
        } catch (e) {
            updateInvestigationServiceStatus(false, null);
        }

        // Update external service statuses (terminal server, etc.) — fire and forget
        refreshServiceStatuses();

        // Helper function for safe DOM updates
        const setEl = (id, value) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = value;
            } else {
                console.warn(`[Widgets] Element not found: ${id}`);
            }
        };

        // stat-mode removed - only R&D mode exists
        setEl('stat-stage', data.rd_stage || '-');
        setEl('stat-project-name', data.project_name || '-');
        setEl('stat-provider', normalizeProvider(data.provider));
        setEl('stat-iteration', data.rd_iteration);
        setEl('stat-mission-cycle', `${data.current_cycle || 1}/${data.cycle_budget || 1}`);
        setEl('stat-cycles', data.total_cycles);
        setEl('stat-boots', data.boot_count);

    await syncProviderControls(data.provider);

    fullMissionText = data.mission || 'No mission set';
    setFullMissionText(fullMissionText);
    const missionEl = document.getElementById('current-mission');
    const preview = data.mission_preview || data.mission || 'No mission set';
    // BUG-1: Build DOM nodes instead of using innerHTML to prevent stored XSS
    missionEl.textContent = '';
    const spanOuter = document.createElement('span');
    spanOuter.onclick = () => window.openMissionModal();
    spanOuter.style.cursor = 'pointer';
    spanOuter.title = 'Click to view full mission';
    spanOuter.textContent = preview;
    if (data.mission && data.mission.length > 100) {
        const expandSpan = document.createElement('span');
        expandSpan.style.color = 'var(--accent)';
        expandSpan.textContent = ' [expand]';
        spanOuter.appendChild(expandSpan);
    }
    missionEl.appendChild(spanOuter);

    updateStageIndicator(data.rd_stage);

    // Use journal data fetched in parallel
    const journalEl = document.getElementById('journal');
    if (journalEl) journalEl.innerHTML = (Array.isArray(journal) ? journal : []).map(j => renderStyledJournalEntry(j)).join('') || '<div style="color: var(--text-dim)">No activity yet</div>';

    restoreJournalExpandedStates();

    if (typeof window.loadRecommendations === 'function') {
        await window.loadRecommendations();
    }

    // Use files data fetched in parallel (populate files list directly)
    _applyFilesData(files);

    // Use atlasData fetched in parallel
    _applyAtlasForgeData(atlasData);

    if (!window.lastKBRefresh || Date.now() - window.lastKBRefresh > 15000) {
        if (typeof window.refreshKBAnalyticsWidget === 'function') {
            await window.refreshKBAnalyticsWidget();
        }
        window.lastKBRefresh = Date.now();
    }

    // Refresh Recommendations widget (Investigation-KB integration)
    // Use initRecommendationsWidget for first load (populates filters, loads state)
    // Falls back to refreshRecommendations for subsequent refreshes
    if (typeof window.initRecommendationsWidget === 'function') {
        await window.initRecommendationsWidget();
    } else if (typeof window.refreshRecommendations === 'function') {
        await window.refreshRecommendations();
    }
    } catch (e) {
        console.error('[Widgets] refresh() error:', e);
    }
}

// =============================================================================
// ATLASFORGE ENHANCEMENT WIDGETS
// =============================================================================

export async function refreshAtlasForgeWidgets() {
    try {
        const data = await api('/api/atlasforge/exploration-stats');
        if (data.error) {
            console.log('AtlasForge data not available:', data.error);
            return;
        }

        if (data.exploration) {
            const fileCount = (data.exploration.nodes_by_type || {}).file || 0;
            const filesEl = document.getElementById('atlasforge-files-count');
            const insightsEl = document.getElementById('atlasforge-insights-count');
            const edgesEl = document.getElementById('atlasforge-edges-count');
            if (filesEl) filesEl.textContent = fileCount;
            if (insightsEl) insightsEl.textContent = data.exploration.total_insights || 0;
            if (edgesEl) edgesEl.textContent = data.exploration.total_edges || 0;
        }

        const coverage = data.coverage_pct || 0;
        const coveragePctEl = document.getElementById('atlasforge-coverage-pct');
        const coverageBarEl = document.getElementById('atlasforge-coverage-bar');
        if (coveragePctEl) coveragePctEl.textContent = coverage + '%';
        if (coverageBarEl) coverageBarEl.style.width = coverage + '%';

        updateDriftChart(data.drift_history || []);
        updateRecentExplorations(data.recent_explorations || []);

        if (!window.lastGraphRefresh || Date.now() - window.lastGraphRefresh > 30000) {
            if (typeof window.refreshGraphVisualization === 'function') {
                window.refreshGraphVisualization();
            }
            window.lastGraphRefresh = Date.now();
        }
    } catch (e) {
        console.log('Error loading AtlasForge widgets:', e);
    }
}

function updateDriftChart(driftHistory) {
    const chart = document.getElementById('atlasforge-drift-chart');
    const simEl = document.getElementById('atlasforge-drift-similarity');
    const sevEl = document.getElementById('atlasforge-drift-severity');
    // Guard against missing DOM elements to prevent TypeError
    if (!chart || !simEl || !sevEl) return;
    // Bug C analog: skip render when card is collapsed (offsetParent===null)
    if (chart.offsetParent === null) return;

    if (!driftHistory || driftHistory.length === 0) {
        chart.innerHTML = '<div style="color: var(--text-dim); font-size: 0.8em; width: 100%; text-align: center;">No drift data yet</div>';
        simEl.textContent = 'N/A';
        sevEl.textContent = 'N/A';
        return;
    }

    const recentHistory = driftHistory.slice(-10);
    const bars = recentHistory.map(h => {
        const sim = h.similarity ?? 1.0;
        const height = Math.max(10, sim * 100);
        let colorClass = 'green';
        if (h.alert === 'YELLOW') colorClass = 'yellow';
        else if (h.alert === 'RED' || h.alert === 'ORANGE') colorClass = 'red';

        return `<div class="atlasforge-drift-bar ${colorClass}" style="height: ${height}%" title="Cycle ${escapeHtml(String(h.cycle))}: ${(sim * 100).toFixed(0)}%"></div>`;
    }).join('');

    chart.innerHTML = bars;

    const latest = recentHistory[recentHistory.length - 1];
    const sim = ((latest.similarity ?? 0) * 100).toFixed(1);
    simEl.textContent = sim + '%';
    simEl.className = 'value ' + getAlertColor(latest.alert);
    sevEl.textContent = latest.severity || 'N/A';
    sevEl.className = 'value ' + getAlertColor(latest.alert);
}

function getAlertColor(alert) {
    if (alert === 'GREEN') return 'green';
    if (alert === 'YELLOW') return 'yellow';
    return 'red';
}

function updateRecentExplorations(explorations) {
    const list = document.getElementById('atlasforge-recent-list');

    if (!explorations || explorations.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No explorations yet</div>';
        return;
    }

    const items = explorations.slice(0, 8).map(e => {
        const name = escapeHtml(e.name || e.path || 'Unknown');
        const type = escapeHtml(e.type || 'file');
        return `
            <div class="atlasforge-exploration-item" title="${escapeHtml(e.summary || '')}">
                <span class="atlasforge-exploration-name">${name}</span>
                <span class="atlasforge-exploration-type">${type}</span>
            </div>
        `;
    }).join('');

    list.innerHTML = items;
}

// =============================================================================
// ANALYTICS WIDGET
// =============================================================================

export async function refreshAnalyticsWidget() {
    try {
        console.log('[Analytics] refreshAnalyticsWidget called');
        const current = await api('/api/analytics/current');
        console.log('[Analytics] Current data:', current);
        if (!current.error) {
            const tokensEl = document.getElementById('analytics-tokens');
            const costEl = document.getElementById('analytics-cost');
            console.log('[Analytics] Elements found:', { tokensEl: !!tokensEl, costEl: !!costEl });
            if (tokensEl) tokensEl.textContent = formatNumber(current.tokens || 0);
            if (costEl) costEl.textContent = '$' + (current.cost || 0).toFixed(4);
            console.log('[Analytics] Updated current:', { tokens: current.tokens, cost: current.cost });
        }

        const summary = await api('/api/analytics/summary');
        console.log('[Analytics] Summary data:', summary ? 'received' : 'null');
        if (!summary.error && summary.aggregate_30d) {
            const agg30d = summary.aggregate_30d.totals || summary.aggregate_30d;
            const tokens30dEl = document.getElementById('analytics-30d-tokens');
            const cost30dEl = document.getElementById('analytics-30d-cost');
            if (tokens30dEl) tokens30dEl.textContent = formatNumber(agg30d.total_tokens || 0);
            if (cost30dEl) cost30dEl.textContent = '$' + (agg30d.total_cost_usd || agg30d.total_cost || 0).toFixed(2);
            console.log('[Analytics] Updated 30d:', { tokens: agg30d.total_tokens, cost: agg30d.total_cost_usd });

            updateAnalyticsTrendWidget(summary.recent_missions || []);
        }
    } catch (e) {
        console.error('[Analytics] Widget error:', e);
    }
}

function updateAnalyticsTrendWidget(missions) {
    const chart = document.getElementById('analytics-trend-chart');
    if (!chart) return;
    // Bug C analog: skip render when widget is collapsed (offsetParent===null)
    if (chart.offsetParent === null) return;

    if (!missions || missions.length === 0) {
        chart.innerHTML = '<div style="color: var(--text-dim); font-size: 0.75em; width: 100%; text-align: center;">No trend data</div>';
        return;
    }

    const costs = missions.slice(-10).map(m => m.cost || 0);
    const maxCost = Math.max(...costs, 0.01);

    const bars = costs.map(cost => {
        const height = Math.max(5, (cost / maxCost) * 100);
        return `<div class="analytics-trend-bar" style="height: ${height}%;" title="$${cost.toFixed(4)}"></div>`;
    }).join('');

    chart.innerHTML = bars;
}

export function showMissionAnalytics(missionId) {
    openMissionAnalyticsModal(missionId);
}

// =============================================================================
// WEB PROXY WIDGET (WEB_PROXY_INVESTIGATION_01 cycle 2)
// =============================================================================

export async function refreshWebProxyWidget() {
    try {
        const stats = await api('/api/web-proxy/stats');
        const badge = document.getElementById('web-proxy-status-badge');
        const provDiv = document.getElementById('web-proxy-providers');
        const setValue = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = formatNumber(value || 0);
        };

        if (stats && stats.status === 'ok') {
            if (badge) {
                badge.textContent = 'LIVE';
                badge.className = 'badge badge-success';
            }
            setValue('web-proxy-searches', stats.cached_searches);
            setValue('web-proxy-fetches', stats.cached_fetches);
            setValue('web-proxy-image-searches', stats.cached_image_searches);
            setValue('web-proxy-images', stats.cached_images);
            const providers = stats.providers || {};
            const entries = Object.entries(providers).sort((a, b) => b[1] - a[1]);
            if (provDiv) {
                provDiv.innerHTML = entries.length === 0
                    ? '<span style="color: var(--text-dim);">No provider data</span>'
                    : entries.map(([p, n]) =>
                        `<span style="margin-right: 10px;">${escapeHtml(p)}: <b>${formatNumber(n)}</b></span>`
                      ).join('');
            }
        } else {
            if (badge) {
                badge.textContent = 'OFFLINE';
                badge.className = 'badge badge-error';
            }
            const msg = (stats && stats.error) || 'proxy unreachable';
            if (provDiv) {
                provDiv.innerHTML = `<span style="color: var(--text-dim);">${escapeHtml(msg)}</span>`;
            }
        }
    } catch (e) {
        console.error('[WebProxy] refresh error:', e);
        const badge = document.getElementById('web-proxy-status-badge');
        if (badge) {
            badge.textContent = 'OFFLINE';
            badge.className = 'badge badge-error';
        }
    }
}

// =============================================================================
// ARTIFACT HEALTH WIDGET
// =============================================================================

export async function refreshArtifactHealthWidget() {
    try {
        const summary = await api('/api/artifact-health/summary');
        if (summary.error) {
            console.warn('[ArtifactHealth] API error:', summary.error);
            return;
        }

        // Update health score and bar
        const healthPercent = Math.round(summary.overall_health || 0);
        const healthBar = document.getElementById('artifact-health-bar');
        const healthPercentEl = document.getElementById('artifact-health-percent');
        if (healthBar) {
            healthBar.style.width = healthPercent + '%';
        }
        if (healthPercentEl) {
            healthPercentEl.textContent = healthPercent;
            // Color code based on health
            if (healthPercent >= 80) {
                healthPercentEl.style.color = 'var(--green)';
            } else if (healthPercent >= 60) {
                healthPercentEl.style.color = 'var(--yellow)';
            } else {
                healthPercentEl.style.color = 'var(--red)';
            }
        }

        // Update stats
        const totalFilesEl = document.getElementById('artifact-total-files');
        const categoriesEl = document.getElementById('artifact-categories');
        const orphansEl = document.getElementById('artifact-orphans');
        const duplicatesEl = document.getElementById('artifact-duplicates');
        const staleEl = document.getElementById('artifact-stale');
        const recsEl = document.getElementById('artifact-recommendations');

        if (totalFilesEl) totalFilesEl.textContent = summary.total_files || 0;
        if (categoriesEl) categoriesEl.textContent = summary.categories || 0;
        if (orphansEl) orphansEl.textContent = summary.orphans || 0;
        if (duplicatesEl) duplicatesEl.textContent = summary.duplicates || 0;
        if (staleEl) staleEl.textContent = summary.stale_files || 0;
        if (recsEl) recsEl.textContent = summary.recommendations_count || 0;
    } catch (e) {
        console.error('[ArtifactHealth] Widget error:', e);
    }
}

// =============================================================================
// MISSION PARAMS WIDGET
// =============================================================================

export async function refreshMissionParamsWidget() {
    try {
        const data = await api('/api/mission/parameters');
        updateMissionParamsWidget(data);
    } catch (e) {
        console.error('[MissionParams] Widget error:', e);
    }
}

export function updateMissionParamsWidget(data) {
    const setEl = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val ?? '-';
    };

    if (!data || !data.mission_id) {
        setEl('mission-params-id', 'No active mission');
        return;
    }

    const p = data.parameters || {};
    setEl('mission-params-id', data.mission_id);
    setEl('mission-params-cycle-budget', p.cycle_budget ?? '-');
    setEl('mission-params-max-iter', p.max_iterations ?? '-');
    setEl('mission-params-provider', p.llm_provider ?? '-');
    setEl('mission-params-cycle', p.current_cycle ?? '-');
    setEl('mission-params-stage', p.current_stage ?? '-');

    const auditEl = document.getElementById('mission-params-audit');
    if (auditEl && data.audit_summary) {
        const a = data.audit_summary;
        const count = a.override_count || 0;
        if (count > 0) {
            auditEl.textContent = `${count} override(s) applied`;
            auditEl.className = 'mission-params-audit-warn';
        } else {
            auditEl.textContent = 'Parameters validated, no overrides';
            auditEl.className = 'mission-params-audit-ok';
        }

        if (a.overrides && a.overrides.length > 0) {
            _renderOverrideTable(a.overrides);
        } else {
            const tbl = document.getElementById('param-override-table');
            if (tbl) { tbl.style.display = 'none'; tbl.innerHTML = ''; }
        }
    } else if (auditEl) {
        auditEl.textContent = '';
        const tbl = document.getElementById('param-override-table');
        if (tbl) { tbl.style.display = 'none'; tbl.innerHTML = ''; }
    }

    // Update health badge from WebSocket push data
    _applyHealthBadge(data.health);

    // Show "was: X" original values if they differ from current
    const origCB = p.original_cycle_budget;
    const origMI = p.original_max_iterations;
    const origCBEl = document.getElementById('mission-params-cycle-budget-original');
    if (origCBEl) {
        origCBEl.textContent = (origCB !== undefined && origCB !== null && origCB !== p.cycle_budget) ? `was: ${origCB}` : '';
    }
    const origMIEl = document.getElementById('mission-params-max-iter-original');
    if (origMIEl) {
        origMIEl.textContent = (origMI !== undefined && origMI !== null && origMI !== p.max_iterations) ? `was: ${origMI}` : '';
    }

    // Show edit button only for in-progress missions (not COMPLETE, not empty)
    const editBtn = document.getElementById('mission-params-edit-btn');
    if (editBtn) {
        const stage = p.current_stage || '';
        editBtn.style.display = (stage && stage !== 'COMPLETE') ? 'inline-block' : 'none';
    }
}

// =============================================================================
// MISSION PARAMS LIVE-EDIT
// =============================================================================

export function toggleMissionParamEdit() {
    const panel = document.getElementById('mission-params-edit-panel');
    if (!panel) return;
    if (panel.style.display !== 'none') {
        closeMissionParamEdit();
    } else {
        openMissionParamEdit();
    }
}

function openMissionParamEdit() {
    const panel = document.getElementById('mission-params-edit-panel');
    if (!panel) return;

    // Pre-fill inputs with current displayed values
    const cbEl = document.getElementById('mission-params-cycle-budget');
    const miEl = document.getElementById('mission-params-max-iter');
    const editCB = document.getElementById('edit-cycle-budget');
    const editMI = document.getElementById('edit-max-iterations');
    if (editCB && cbEl && cbEl.textContent !== '-') editCB.value = cbEl.textContent.trim();
    if (editMI && miEl && miEl.textContent !== '-') editMI.value = miEl.textContent.trim();

    // Show range hints
    const hintCB = document.getElementById('edit-cycle-budget-hint');
    if (hintCB) hintCB.textContent = '(1–10)';
    const hintMI = document.getElementById('edit-max-iterations-hint');
    if (hintMI) hintMI.textContent = '(1–50)';

    const statusEl = document.getElementById('mission-params-edit-status');
    if (statusEl) statusEl.textContent = '';

    panel.style.display = 'block';
}

export function closeMissionParamEdit() {
    const panel = document.getElementById('mission-params-edit-panel');
    if (panel) panel.style.display = 'none';
}

export async function applyMissionParamEdit() {
    const editCB = document.getElementById('edit-cycle-budget');
    const editMI = document.getElementById('edit-max-iterations');
    const statusEl = document.getElementById('mission-params-edit-status');

    const payload = {};
    if (editCB && editCB.value !== '') payload.cycle_budget = parseInt(editCB.value, 10);
    if (editMI && editMI.value !== '') payload.max_iterations = parseInt(editMI.value, 10);

    if (Object.keys(payload).length === 0) {
        if (statusEl) { statusEl.textContent = 'No changes to apply'; statusEl.style.color = 'var(--text-dim)'; }
        return;
    }

    if (statusEl) { statusEl.textContent = 'Applying…'; statusEl.style.color = 'var(--text-dim)'; }

    try {
        const result = await api('/api/mission/parameters', 'PATCH', payload);
        if (result.success) {
            if (statusEl) { statusEl.textContent = `✓ ${result.message}`; statusEl.style.color = 'var(--success, #4caf50)'; }
            setTimeout(() => closeMissionParamEdit(), 1500);
            // Refresh widget immediately to show new values + "was:" labels
            await refreshMissionParamsWidget();
        } else {
            const errMsg = result.errors ? result.errors.join(', ') : (result.error || 'Failed');
            if (statusEl) { statusEl.textContent = `✗ ${errMsg}`; statusEl.style.color = 'var(--danger, #f44336)'; }
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = `✗ ${e.message}`; statusEl.style.color = 'var(--danger, #f44336)'; }
    }
}

function _renderOverrideTable(overrides) {
    const container = document.getElementById('param-override-table');
    if (!container) return;

    const reasonLabels = {
        min_clamp: 'below min',
        max_clamp: 'above max',
        type_coercion: 'type fix',
        default_applied: 'default',
        env_resolution: 'from env',
        invalid_rejected: 'invalid\u2192default',
    };

    let html = '<table class="param-override-table"><thead><tr>'
        + '<th>Param</th><th>Submitted</th><th>Applied</th><th>Reason</th>'
        + '</tr></thead><tbody>';

    for (const ov of overrides) {
        const submitted = (ov.submitted_value === null || ov.submitted_value === undefined)
            ? '<em>null</em>' : escapeHtml(String(ov.submitted_value));
        const applied = (ov.applied_value === null || ov.applied_value === undefined)
            ? '<em>null</em>' : escapeHtml(String(ov.applied_value));
        const reason = escapeHtml(reasonLabels[ov.reason] || ov.reason || '');
        html += `<tr><td>${escapeHtml(ov.param || '')}</td><td>${submitted}</td><td>${applied}</td>`
            + `<td class="override-reason">${reason}</td></tr>`;
    }

    html += '</tbody></table>';
    container.innerHTML = html;
    container.style.display = 'block';
}

function _applyHealthBadge(health) {
    const badge = document.getElementById('param-health-badge');
    if (!badge || !health) return;
    badge.className = `health-badge health-${health}`;
    const labels = { green: 'Healthy — no overrides', yellow: 'Overrides detected', red: 'Issues detected', unknown: 'No audit data yet' };
    badge.title = `Parameter health: ${labels[health] || health}`;
}

// Trend chart state
let _paramTrendVisible = false;
let _paramTrendRendered = false;
let _paramTrendResizeObserver = null;
// Last data payload for ResizeObserver re-render
let _lastParamTrendArgs = null;

export function toggleParamTrend() {
    const container = document.getElementById('param-trend-container');
    if (!container) return;
    _paramTrendVisible = !_paramTrendVisible;
    container.style.display = _paramTrendVisible ? 'block' : 'none';
    if (_paramTrendVisible && !_paramTrendRendered) {
        _loadParamTrendChart();
    }
    if (_paramTrendVisible) {
        // ResizeObserver: observe canvas for resize events so chart repaints correctly
        const canvas = document.getElementById('param-trend-chart');
        if (canvas && !_paramTrendResizeObserver) {
            _paramTrendResizeObserver = new ResizeObserver(() => {
                if (_lastParamTrendArgs && _paramTrendVisible) {
                    const [labels, cb, mi, op] = _lastParamTrendArgs;
                    _renderParamTrendCanvas(canvas, labels, cb, mi, op);
                }
            });
            _paramTrendResizeObserver.observe(canvas);
        }
    } else {
        // When closing, reset rendered flag so next open refreshes data
        _paramTrendRendered = false;
        // ResizeObserver: disconnect to prevent zombie observers
        if (_paramTrendResizeObserver) {
            _paramTrendResizeObserver.disconnect();
            _paramTrendResizeObserver = null;
        }
    }
    // Recalc card height so maxHeight expands/contracts to fit the trend chart.
    // The chart container is display:none when the card height is first measured,
    // so without this call the canvas gets clipped by the card's maxHeight constraint.
    recalcCardHeight('mission-params-widget');
}

async function _loadParamTrendChart() {
    try {
        const resp = await fetch('/api/mission/parameter-audit/history?limit=20');
        const json = await resp.json();
        const history = json.history || [];

        // Sync health badge
        _applyHealthBadge(json.health);

        if (!history.length) return;

        const labels = history.map(h => (h.mission_id || '').slice(-8));
        const cycleBudgets = history.map(h => h.cycle_budget ?? null);
        const maxIters = history.map(h => h.max_iterations ?? null);
        const overridePoints = history.map(h => h.override_count > 0 ? h.cycle_budget : null);

        const canvas = document.getElementById('param-trend-chart');
        if (!canvas) return;
        // Store last args so ResizeObserver can re-render on container resize
        _lastParamTrendArgs = [labels, cycleBudgets, maxIters, overridePoints];
        _renderParamTrendCanvas(canvas, labels, cycleBudgets, maxIters, overridePoints);
        _paramTrendRendered = true;
    } catch (e) {
        console.error('[ParamTrend] Failed to load trend:', e);
    }
}

function _renderParamTrendCanvas(canvas, labels, cycleBudgets, maxIters, overridePoints) {
    const ctx = canvas.getContext('2d');
    // canvas.offsetWidth can be 0 when transitioning from display:none; fall back to parent width
    const W = canvas.offsetWidth || canvas.parentElement?.clientWidth || 280;
    const H = canvas.height || 110;
    canvas.width = W;
    canvas.height = H;

    const pad = { top: 14, right: 12, bottom: 28, left: 28 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    const allVals = [...cycleBudgets, ...maxIters].filter(v => v !== null);
    if (!allVals.length) return;
    const maxV = Math.max(...allVals) + 1;

    const xScale = (i) => pad.left + (i / Math.max(labels.length - 1, 1)) * plotW;
    const yScale = (v) => pad.top + plotH - (v / maxV) * plotH;

    // Grid lines
    ctx.strokeStyle = 'rgba(128,128,128,0.2)';
    ctx.lineWidth = 1;
    [0, Math.ceil(maxV / 2), maxV].forEach(v => {
        const y = yScale(v);
        ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + plotW, y); ctx.stroke();
        ctx.fillStyle = 'rgba(160,160,160,0.8)';
        ctx.font = '9px monospace';
        ctx.fillText(v, 2, y + 3);
    });

    const drawLine = (data, color) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let started = false;
        data.forEach((v, i) => {
            if (v === null) return;
            const x = xScale(i), y = yScale(v);
            if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        });
        ctx.stroke();
    };

    drawLine(cycleBudgets, '#4a9eff');
    drawLine(maxIters, '#28a745');

    // Override markers (red dots)
    overridePoints.forEach((v, i) => {
        if (v === null) return;
        ctx.beginPath();
        ctx.arc(xScale(i), yScale(v), 4, 0, Math.PI * 2);
        ctx.fillStyle = '#dc3545';
        ctx.fill();
    });

    // X-axis labels (sparse)
    ctx.fillStyle = 'rgba(160,160,160,0.8)';
    ctx.font = '8px monospace';
    labels.forEach((lbl, i) => {
        if (i % 4 !== 0 && i !== labels.length - 1) return;
        ctx.fillText(lbl, xScale(i) - 12, H - 4);
    });

    // Legend
    const leg = pad.left;
    ctx.fillStyle = '#4a9eff'; ctx.fillRect(leg, 2, 10, 3);
    ctx.fillStyle = 'rgba(160,160,160,0.8)'; ctx.font = '8px monospace'; ctx.fillText('budget', leg + 13, 7);
    ctx.fillStyle = '#28a745'; ctx.fillRect(leg + 58, 2, 10, 3);
    ctx.fillStyle = 'rgba(160,160,160,0.8)'; ctx.fillText('max_iter', leg + 71, 7);
    ctx.beginPath(); ctx.arc(leg + 130, 4, 3, 0, Math.PI * 2);
    ctx.fillStyle = '#dc3545'; ctx.fill();
    ctx.fillStyle = 'rgba(160,160,160,0.8)'; ctx.fillText('override', leg + 137, 7);
}

// Export fullMissionText getter
export function getFullMissionText() {
    return fullMissionText;
}

// =============================================================================
// ENHANCED ANALYTICS TAB FUNCTIONS
// =============================================================================

// Bug C: visibility / render state flags for analytics-trend-canvas
// setAnalyticsTabVisible() is called when the analytics tab opens/closes
let _analyticsTabVisible = false;
let _analyticsTrendRendered = false;
let _analyticsTrendResizeObserver = null;
// Last analytics data payload for ResizeObserver re-render
let _lastAnalyticsData = { daily: null, summary: null };

/** Call this when the analytics tab becomes visible or hidden.
 *  Exported so the tab-switch handler can notify us. */
export function setAnalyticsTabVisible(visible) {
    _analyticsTabVisible = visible;
    if (visible) {
        // ResizeObserver: observe the analytics trend canvas when tab becomes visible
        const canvas = document.getElementById('analytics-trend-canvas');
        if (canvas && !_analyticsTrendResizeObserver) {
            _analyticsTrendResizeObserver = new ResizeObserver(() => {
                if (_lastAnalyticsData.daily && _analyticsTabVisible) {
                    renderEnhancedTrendChart(_lastAnalyticsData.daily, _lastAnalyticsData.summary);
                }
            });
            _analyticsTrendResizeObserver.observe(canvas);
        }
    } else {
        // Bug C: reset rendered flag on close so next open refreshes data
        _analyticsTrendRendered = false;
        // ResizeObserver: disconnect to prevent zombie observers
        if (_analyticsTrendResizeObserver) {
            _analyticsTrendResizeObserver.disconnect();
            _analyticsTrendResizeObserver = null;
        }
    }
}

/** Get analytics trend render state - exported for test use */
export function getAnalyticsTrendState() {
    return { tabVisible: _analyticsTabVisible, rendered: _analyticsTrendRendered };
}

let currentAnalyticsPeriod = 30;
let analyticsCache = {
    summary: null,
    daily: null,
    stages: null,
    models: null
};

const ANALYTICS_FILTER_KEY = 'analytics_filter_state';
const ANALYTICS_SCHEMA_VERSION = 1;

function saveAnalyticsFilterState() {
    try {
        const select = document.getElementById('analytics-period-filter');
        localStorage.setItem(ANALYTICS_FILTER_KEY, JSON.stringify({
            version: ANALYTICS_SCHEMA_VERSION,
            period: select ? select.value : '30',
        }));
    } catch (e) {}
}

function loadAnalyticsFilterState() {
    try {
        const saved = localStorage.getItem(ANALYTICS_FILTER_KEY);
        if (!saved) return;
        const parsed = JSON.parse(saved);
        if (!parsed.version || parsed.version !== ANALYTICS_SCHEMA_VERSION) {
            localStorage.removeItem(ANALYTICS_FILTER_KEY); return;
        }
        const el = document.getElementById('analytics-period-filter');
        if (el && parsed.period) el.value = parsed.period;
    } catch (e) {}
}

export async function applyAnalyticsPeriodFilter() {
    const select = document.getElementById('analytics-period-filter');
    currentAnalyticsPeriod = parseInt(select.value) || 30;
    saveAnalyticsFilterState();
    const periodLabel = document.getElementById('analytics-trend-period');
    if (periodLabel) {
        if (currentAnalyticsPeriod === 0) {
            periodLabel.textContent = '(All Time)';
        } else {
            periodLabel.textContent = `(${currentAnalyticsPeriod} Days)`;
        }
    }
    await refreshFullAnalytics();
}

export async function refreshFullAnalytics() {
    // Restore saved period filter (no-op if already applied via applyAnalyticsPeriodFilter)
    loadAnalyticsFilterState();
    const _sel = document.getElementById('analytics-period-filter');
    if (_sel && _sel.value) currentAnalyticsPeriod = parseInt(_sel.value) || 30;

    try {
        // Fetch all analytics data in parallel
        const [summary, daily, stages, models] = await Promise.all([
            api('/api/analytics/summary'),
            api(`/api/analytics/daily?days=${currentAnalyticsPeriod}`),
            api(`/api/analytics/by-stage?days=${currentAnalyticsPeriod}`),
            api(`/api/analytics/by-model?days=${currentAnalyticsPeriod}`)
        ]);

        analyticsCache = { summary, daily, stages, models };
        analyticsData = summary;

        // Update header stats
        if (!summary.error) {
            const allTimeRaw = summary.all_time || {};
            const allTime = allTimeRaw.totals || allTimeRaw;
            const missionCount = allTime.missions || allTime.mission_count || 0;
            const totalTokens = allTime.total_tokens || 0;
            const totalCost = allTime.total_cost_usd || allTime.total_cost || 0;

            document.getElementById('analytics-total-missions').textContent = missionCount;
            document.getElementById('analytics-total-tokens').textContent = formatNumber(totalTokens);
            document.getElementById('analytics-total-cost').textContent = '$' + totalCost.toFixed(2);

            // Avg per mission
            const avgCost = missionCount > 0 ? (totalCost / missionCount) : 0;
            document.getElementById('analytics-avg-per-mission').textContent = '$' + avgCost.toFixed(2);

            // Cache rate
            const cacheRead = allTime.cache_read_tokens || 0;
            const inputTokens = allTime.input_tokens || 0;
            const cacheRate = inputTokens > 0 ? ((cacheRead / inputTokens) * 100).toFixed(1) : 0;
            document.getElementById('analytics-cache-rate').textContent = cacheRate + '%';

            // Today's cost
            if (daily && daily.daily && daily.daily.length > 0) {
                const today = new Date().toISOString().split('T')[0];
                const todayData = daily.daily.find(d => d.date === today);
                const todayCost = todayData ? todayData.cost : 0;
                document.getElementById('analytics-today-cost').textContent = '$' + todayCost.toFixed(2);
            } else {
                document.getElementById('analytics-today-cost').textContent = '$0.00';
            }

            // Token breakdown
            document.getElementById('analytics-input-tokens').textContent = formatNumber(allTime.input_tokens || 0);
            document.getElementById('analytics-output-tokens').textContent = formatNumber(allTime.output_tokens || 0);
            document.getElementById('analytics-cache-read-tokens').textContent = formatNumber(allTime.cache_read_tokens || 0);
            document.getElementById('analytics-cache-write-tokens').textContent = formatNumber(allTime.cache_write_tokens || 0);

            // Render mission list
            renderEnhancedMissionList(summary.recent_missions || []);

            // Render token breakdown donut
            renderTokenBreakdownDonut({
                input: allTime.input_tokens || 0,
                output: allTime.output_tokens || 0,
                cache_read: allTime.cache_read_tokens || 0,
                cache_write: allTime.cache_write_tokens || 0
            });
        }

        // Render daily trend chart
        if (daily && !daily.error) {
            // Store last data so ResizeObserver can re-render on container resize
            _lastAnalyticsData = { daily: daily.daily || [], summary: daily.summary || {} };
            renderEnhancedTrendChart(daily.daily || [], daily.summary || {});
        }

        // Render stage analysis
        if (stages && !stages.error) {
            renderStageAnalysis(stages.stages || {});
        }

        // Render model comparison
        if (models && !models.error) {
            renderModelComparison(models.models || {});
        }

    } catch (e) {
        console.error('Full analytics error:', e);
    }
}

function renderEnhancedMissionList(missions) {
    const list = document.getElementById('analytics-missions-list');
    if (!missions || missions.length === 0) {
        list.innerHTML = '<div style="color: var(--text-dim);">No mission data</div>';
        return;
    }

    const html = missions.map(m => {
        const dateStr = m.started_at ? new Date(m.started_at).toLocaleDateString() : '-';
        const safeId = escapeHtml(m.mission_id || 'Unknown');
        const safeStatus = escapeHtml(m.status || 'unknown');
        const statusClass = (m.status || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '_');
        return `
            <div class="analytics-mission-item" data-mission-id="${safeId}">
                <div class="analytics-mission-id">${safeId}</div>
                <div class="analytics-mission-meta">
                    <span class="analytics-mission-cost">$${(m.cost_usd || m.cost || 0).toFixed(4)}</span>
                    <span class="analytics-mission-tokens">${formatNumber(m.total_tokens || m.tokens || 0)} tokens</span>
                </div>
                <div class="analytics-mission-date">
                    ${dateStr}
                    <span class="analytics-mission-status ${statusClass}">${safeStatus}</span>
                </div>
            </div>
        `;
    }).join('');

    list.innerHTML = html;

    // Attach click handlers via event listeners (no inline JS — prevents XSS)
    list.querySelectorAll('.analytics-mission-item[data-mission-id]').forEach(el => {
        el.addEventListener('click', () => {
            window.showMissionAnalytics(el.dataset.missionId);
        });
    });
}

function renderTokenBreakdownDonut(data) {
    const svg = document.getElementById('analytics-breakdown-svg');
    const totalEl = document.getElementById('analytics-breakdown-total');
    const legendEl = document.getElementById('analytics-breakdown-legend');

    if (!svg) return;

    const total = (data.input || 0) + (data.output || 0) + (data.cache_read || 0) + (data.cache_write || 0);
    if (total === 0) {
        svg.innerHTML = '<text x="100" y="100" text-anchor="middle" fill="#8b949e" font-size="14">No data</text>';
        totalEl.textContent = '0';
        legendEl.innerHTML = '';
        return;
    }

    // Format total for display
    totalEl.textContent = formatNumber(total);

    const segments = [
        { name: 'Input', value: data.input || 0, color: '#58a6ff' },
        { name: 'Output', value: data.output || 0, color: '#3fb950' },
        { name: 'Cache Read', value: data.cache_read || 0, color: '#d29922' },
        { name: 'Cache Write', value: data.cache_write || 0, color: '#bc8cff' }
    ].filter(s => s.value > 0);

    const cx = 100, cy = 100, r = 70;
    const strokeWidth = 20;
    const circumference = 2 * Math.PI * r;

    // Start at top of circle (rotate -90 degrees via transform)
    let offset = circumference * 0.25;  // Start at 12 o'clock
    let svgContent = '';

    segments.forEach((seg, i) => {
        const pct = seg.value / total;
        const dashLength = pct * circumference;

        svgContent += `
            <circle
                cx="${cx}" cy="${cy}" r="${r}"
                fill="none"
                stroke="${seg.color}"
                stroke-width="${strokeWidth}"
                stroke-dasharray="${dashLength} ${circumference - dashLength}"
                stroke-dashoffset="${offset}"
                data-name="${seg.name}"
                data-value="${seg.value}"
                data-pct="${(pct * 100).toFixed(1)}"
                style="transition: stroke-dashoffset 0.3s ease;"
            />
        `;

        offset -= dashLength;
    });

    svg.innerHTML = svgContent;

    // Render legend with percentages
    legendEl.innerHTML = segments.map(seg => {
        const pct = ((seg.value / total) * 100).toFixed(1);
        return `
            <div class="analytics-legend-item">
                <div class="analytics-legend-color" style="background: ${seg.color};"></div>
                <span>${escapeHtml(seg.name)} (${pct}%)</span>
            </div>
        `;
    }).join('');
}

function renderEnhancedTrendChart(daily, summary) {
    const canvas = document.getElementById('analytics-trend-canvas');
    if (!canvas) return;

    // Bug C: skip render when analytics tab is not visible OR canvas is hidden
    // Fix: use || (OR) not && (AND) — skip if EITHER condition is true
    if (!_analyticsTabVisible || canvas.offsetParent === null) return;

    const ctx = canvas.getContext('2d');
    const rect = canvas.getBoundingClientRect();

    // Bug B: Ensure canvas has size (may be 0 if tab not visible)
    // Bug B: getBoundingClientRect fallback -> parentElement -> CSS computed -> hardcoded
    let w = rect.width || canvas.offsetWidth || canvas.parentElement?.clientWidth || 400;
    let h = rect.height || canvas.offsetHeight || canvas.parentElement?.clientHeight || 200;

    // Bug B: tightened threshold from w<50 to w<10 to reduce false fallbacks
    if (w < 10) {
        const computedStyle = window.getComputedStyle(canvas);
        w = parseFloat(computedStyle.width) || 400;
        h = parseFloat(computedStyle.height) || 200;
    }

    canvas.width = w * window.devicePixelRatio;
    canvas.height = h * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

    const padding = { top: 20, right: 20, bottom: 40, left: 60 };

    // Clear
    ctx.fillStyle = '#161b22';
    ctx.fillRect(0, 0, w, h);

    if (!daily || daily.length === 0) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '14px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('No data available', w / 2, h / 2);
        return;
    }

    const costs = daily.map(d => d.cost || 0);
    const maxCost = Math.max(...costs, 0.01);

    const graphWidth = w - padding.left - padding.right;
    const graphHeight = h - padding.top - padding.bottom;
    const barWidth = Math.max(4, (graphWidth / costs.length) - 2);

    // Draw grid lines
    ctx.strokeStyle = '#30363d';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (graphHeight * i / 4);
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(w - padding.right, y);
        ctx.stroke();
    }

    // Draw Y-axis labels
    ctx.fillStyle = '#8b949e';
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
        const y = padding.top + (graphHeight * i / 4);
        const val = maxCost * (1 - i / 4);
        ctx.fillText('$' + val.toFixed(2), padding.left - 5, y + 3);
    }

    // Draw bars with gradient
    const gradient = ctx.createLinearGradient(0, padding.top, 0, h - padding.bottom);
    gradient.addColorStop(0, '#58a6ff');
    gradient.addColorStop(1, '#1f6feb');

    const today = new Date().toISOString().split('T')[0];

    costs.forEach((cost, i) => {
        const barHeight = (cost / maxCost) * graphHeight;
        const x = padding.left + i * (barWidth + 2);
        const y = h - padding.bottom - barHeight;

        // Highlight today
        if (daily[i] && daily[i].date === today) {
            ctx.fillStyle = '#3fb950';
        } else {
            ctx.fillStyle = gradient;
        }

        ctx.fillRect(x, y, barWidth, barHeight);
    });

    // Draw X-axis labels (show every Nth label to avoid crowding)
    const labelStep = Math.ceil(daily.length / 8);
    ctx.fillStyle = '#8b949e';
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';

    daily.forEach((d, i) => {
        if (i % labelStep === 0 || i === daily.length - 1) {
            const x = padding.left + i * (barWidth + 2) + barWidth / 2;
            const label = d.date ? d.date.slice(5) : ''; // MM-DD
            ctx.fillText(label, x, h - padding.bottom + 15);
        }
    });

    // Update summary stats (with null checks)
    if (summary) {
        const dailyAvgEl = document.getElementById('analytics-daily-avg');
        const peakDayEl = document.getElementById('analytics-peak-day');
        const peakCostEl = document.getElementById('analytics-peak-cost');

        if (dailyAvgEl) dailyAvgEl.textContent = '$' + (summary.avg_daily_cost || 0).toFixed(2);
        if (peakDayEl) peakDayEl.textContent = summary.peak_day || '-';
        if (peakCostEl) peakCostEl.textContent = '$' + (summary.peak_cost || 0).toFixed(2);
    }

    // Bug C: mark render complete
    _analyticsTrendRendered = true;

    // Store data for hover (attach to canvas)
    canvas._chartData = daily;
    canvas._chartBounds = { w, h, padding, barWidth, maxCost, graphHeight };

    // Add hover handler if not already added
    if (!canvas._hasHover) {
        canvas._hasHover = true;
        canvas.addEventListener('mousemove', handleTrendChartHover);
        canvas.addEventListener('mouseleave', hideTrendChartTooltip);
    }
}

function handleTrendChartHover(e) {
    const canvas = e.target;
    const tooltip = document.getElementById('analytics-trend-tooltip');
    if (!canvas._chartData || !tooltip) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const { padding, barWidth, graphHeight, maxCost, w, h } = canvas._chartBounds;
    const daily = canvas._chartData;

    // Find which bar we're over
    const barAreaX = x - padding.left;
    const barIndex = Math.floor(barAreaX / (barWidth + 2));

    if (barIndex >= 0 && barIndex < daily.length && x > padding.left && x < w - padding.right) {
        const d = daily[barIndex];
        tooltip.innerHTML = `
            <div class="tooltip-date">${escapeHtml(d.date || '-')}</div>
            <div class="tooltip-cost">$${(d.cost || 0).toFixed(4)}</div>
            <div class="tooltip-tokens">${formatNumber(d.total_tokens || 0)} tokens</div>
        `;
        tooltip.style.display = 'block';
        tooltip.style.left = (e.clientX - rect.left + 10) + 'px';
        tooltip.style.top = (e.clientY - rect.top - 30) + 'px';
    } else {
        tooltip.style.display = 'none';
    }
}

function hideTrendChartTooltip() {
    const tooltip = document.getElementById('analytics-trend-tooltip');
    if (tooltip) tooltip.style.display = 'none';
}

function renderStageAnalysis(stages) {
    const container = document.getElementById('analytics-stage-chart');
    if (!container) return;

    const stageOrder = ['PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'CYCLE_END', 'COMPLETE'];
    const stageEntries = Object.entries(stages);

    if (stageEntries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No stage data available</div>';
        return;
    }

    // Sort stages by order, unknown stages at end
    stageEntries.sort((a, b) => {
        const idxA = stageOrder.indexOf(a[0]);
        const idxB = stageOrder.indexOf(b[0]);
        return (idxA === -1 ? 999 : idxA) - (idxB === -1 ? 999 : idxB);
    });

    // Find max tokens for scaling
    const maxTokens = Math.max(...stageEntries.map(([_, s]) => s.total_tokens || 0), 1);

    const html = stageEntries.map(([stageName, data]) => {
        const pct = ((data.total_tokens || 0) / maxTokens) * 100;
        const cost = data.cost || 0;
        const tokens = data.total_tokens || 0;

        return `
            <div class="analytics-stage-bar">
                <div class="analytics-stage-label">${stageName}</div>
                <div class="analytics-stage-track">
                    <div class="analytics-stage-fill ${stageName}" style="width: ${pct}%;">
                        ${pct > 15 ? `<span>${formatNumber(tokens)}</span>` : ''}
                    </div>
                </div>
                <div class="analytics-stage-value">
                    <span class="analytics-stage-cost">$${cost.toFixed(2)}</span>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function renderModelComparison(models) {
    const container = document.getElementById('analytics-model-grid');
    if (!container) return;

    const modelEntries = Object.entries(models);

    if (modelEntries.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No model data available</div>';
        return;
    }

    // Sort by cost descending
    modelEntries.sort((a, b) => (b[1].cost || 0) - (a[1].cost || 0));

    const maxCost = Math.max(...modelEntries.map(([_, m]) => m.cost || 0), 0.01);

    const html = modelEntries.map(([modelId, data], idx) => {
        const displayName = data.display_name || modelId;
        const isPrimary = idx === 0;
        const isEfficient = modelId.includes('haiku');

        return `
            <div class="analytics-model-card">
                <div class="analytics-model-info">
                    <div class="analytics-model-name">
                        ${displayName}
                        ${isPrimary ? '<span class="analytics-model-badge primary">Primary</span>' : ''}
                        ${isEfficient ? '<span class="analytics-model-badge efficient">Efficient</span>' : ''}
                    </div>
                    <div class="analytics-model-tokens">${formatNumber(data.total_tokens || 0)} tokens</div>
                    <div class="analytics-model-events">${data.event_count || 0} API calls</div>
                </div>
                <div class="analytics-model-cost">$${(data.cost || 0).toFixed(2)}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

export async function openMissionAnalyticsModal(missionId) {
    const modal = document.getElementById('mission-analytics-modal');
    const body = document.getElementById('mission-analytics-modal-body');

    if (!modal || !body) return;

    modal.style.display = 'flex';
    body.innerHTML = '<div style="color: var(--text-dim);">Loading...</div>';

    try {
        const data = await api(`/api/analytics/mission/${missionId}/stages`);

        if (data.error) {
            body.innerHTML = `<div style="color: var(--red);">Error: ${escapeHtml(data.error)}</div>`;
            return;
        }

        const summary = data.summary || {};
        const stages = data.stages || [];

        let stagesHtml = '';
        if (stages.length > 0) {
            const maxTokens = Math.max(...stages.map(s => s.total_tokens || 0), 1);

            stagesHtml = stages.map(s => {
                const pct = ((s.total_tokens || 0) / maxTokens) * 100;
                // Stage names from the server are fixed enum values; escape defensively
                const safeStage = escapeHtml(s.stage || '');
                const stageClass = (s.stage || '').replace(/[^a-zA-Z0-9_-]/g, '_');
                return `
                    <div class="mission-stage-item">
                        <div class="mission-stage-name analytics-stage-fill ${stageClass}" style="padding: 4px 8px; border-radius: 4px;">${safeStage}</div>
                        <div class="mission-stage-bar">
                            <div class="mission-stage-bar-fill analytics-stage-fill ${stageClass}" style="width: ${pct}%;"></div>
                        </div>
                        <div class="mission-stage-stats">
                            ${formatNumber(s.total_tokens || 0)} tokens | $${(s.cost || 0).toFixed(4)}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            stagesHtml = '<div style="color: var(--text-dim);">No stage data available</div>';
        }

        body.innerHTML = `
            <div class="mission-analytics-header">
                <div>
                    <strong style="color: var(--accent);">${escapeHtml(missionId)}</strong>
                    <div style="color: var(--text-dim); font-size: 0.85em; margin-top: 4px;">
                        ${escapeHtml(data.status || 'Unknown status')}
                    </div>
                </div>
                <div style="text-align: right;">
                    <div style="color: var(--text-dim); font-size: 0.8em;">Started</div>
                    <div>${data.started_at ? new Date(data.started_at).toLocaleString() : '-'}</div>
                </div>
            </div>

            <div class="mission-analytics-summary">
                <div class="mission-analytics-stat">
                    <div class="value cost">$${(summary.total_cost || 0).toFixed(4)}</div>
                    <div class="label">Total Cost</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${formatNumber(summary.total_tokens || 0)}</div>
                    <div class="label">Total Tokens</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${summary.stage_count || 0}</div>
                    <div class="label">Stages</div>
                </div>
                <div class="mission-analytics-stat">
                    <div class="value">${stages.reduce((sum, s) => sum + (s.event_count || 0), 0)}</div>
                    <div class="label">API Calls</div>
                </div>
            </div>

            <div class="mission-analytics-stages">
                <h4>Stage Breakdown</h4>
                <div class="mission-stage-timeline">
                    ${stagesHtml}
                </div>
            </div>

            ${data.problem_statement ? `
                <div style="margin-top: 20px;">
                    <h4 style="color: var(--accent); margin-bottom: 10px; font-size: 0.9em;">Problem Statement</h4>
                    <div style="background: var(--bg); padding: 12px; border-radius: 6px; font-size: 0.9em;">
                        ${escapeHtml(data.problem_statement.substring(0, 500))}${data.problem_statement.length > 500 ? '...' : ''}
                    </div>
                </div>
            ` : ''}
        `;
    } catch (e) {
        body.innerHTML = `<div style="color: var(--red);">Error loading mission data: ${escapeHtml(e.message || String(e))}</div>`;
    }
}

export function closeMissionAnalyticsModal() {
    const modal = document.getElementById('mission-analytics-modal');
    if (modal) modal.style.display = 'none';
}

export async function exportAnalyticsCSV() {
    try {
        const summary = analyticsCache.summary || await api('/api/analytics/summary');
        const missions = summary.recent_missions || [];

        if (missions.length === 0) {
            showToast('No data to export');
            return;
        }

        // Build CSV
        let csv = 'Mission ID,Started At,Status,Tokens,Cost (USD),Duration (s)\n';
        missions.forEach(m => {
            csv += `"${m.mission_id}","${m.started_at || ''}","${m.status || ''}",${m.total_tokens || m.tokens || 0},${m.cost_usd || m.cost || 0},${m.duration_seconds || 0}\n`;
        });

        // Download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `analytics_export_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast('Analytics exported to CSV');
    } catch (e) {
        console.error('Export error:', e);
        showToast('Export failed: ' + e.message);
    }
}

// =============================================================================
// WEBSOCKET EVENT HANDLERS FOR REAL-TIME UPDATES
// =============================================================================

/**
 * Handle file creation events from WebSocket
 * @param {object} data - Event data with file info
 */
export function handleFileEvent(data) {
    const eventData = data.data || data;
    const event = eventData.event;

    if (event === 'file_created') {
        const fileName = eventData.file_name || 'Unknown file';
        const fileType = eventData.file_type || 'file';
        showToast(`New ${fileType}: ${fileName}`, 'info');
        // Refresh files list and highlight the files card
        loadFiles();
        // Add visual feedback to files card
        const filesCard = document.getElementById('files-card');
        if (filesCard) {
            filesCard.classList.add('ws-updated');
            setTimeout(() => filesCard.classList.remove('ws-updated'), 1000);
        }
    } else if (event === 'file_modified') {
        // Refresh files list for modifications with subtle feedback
        loadFiles();
        const filesCard = document.getElementById('files-card');
        if (filesCard) {
            filesCard.classList.add('ws-updated');
            setTimeout(() => filesCard.classList.remove('ws-updated'), 1000);
        }
    }
}

/**
 * Handle GlassBox archive events from WebSocket
 * @param {object} data - Event data with archive info
 */
export function handleGlassboxArchiveEvent(data) {
    const eventData = data.data || data;

    if (eventData.event === 'transcript_archived') {
        const count = eventData.transcript_count || 0;
        const missionId = eventData.mission_id || 'Unknown';
        showToast(`Archived ${count} transcripts for ${missionId}`, 'success');

        // Refresh GlassBox widget if the function exists
        if (typeof window.refreshGlassboxWidget === 'function') {
            window.refreshGlassboxWidget();
        }
    }
}

/**
 * Handle generic GlassBox events from WebSocket (manifest_enhanced, etc.)
 * @param {object} data - Event data with glassbox info
 */
export function handleGlassboxEvent(data) {
    const eventData = data.data || data;

    if (eventData.event === 'manifest_enhanced') {
        const missionId = eventData.mission_id || 'Unknown';
        showToast(`Enhanced manifest for ${missionId}`, 'info');

        // Refresh GlassBox widget if the function exists
        if (typeof window.refreshGlassboxWidget === 'function') {
            window.refreshGlassboxWidget();
        }
    } else if (eventData.event === 'archive_created') {
        const missionId = eventData.mission_id || 'Unknown';
        showToast(`Archive created for ${missionId}`, 'success');

        if (typeof window.refreshGlassboxWidget === 'function') {
            window.refreshGlassboxWidget();
        }
    }
}

/**
 * Handle recommendation events from WebSocket
 * @param {object} data - Event data with recommendation info
 */
export function handleRecommendationEvent(data) {
    const eventData = data.data || data;

    if (eventData.event === 'new_recommendation') {
        const rec = eventData.recommendation || {};
        const title = rec.title || 'New Mission Recommendation';
        showToast(`New recommendation: ${title}`, 'info');

        // Refresh recommendations widget
        if (typeof window.loadRecommendations === 'function') {
            window.loadRecommendations();
        }
        if (typeof window.refreshRecommendations === 'function') {
            window.refreshRecommendations();
        }

        // Add visual feedback to recommendations card
        const recCard = document.getElementById('recommendations-card');
        if (recCard) {
            recCard.classList.add('ws-updated');
            setTimeout(() => recCard.classList.remove('ws-updated'), 1000);
        }
    }
}

/**
 * Validate a mission_status payload against the canonical schema.
 * Logs warnings in development if required fields are missing or have wrong types.
 * Does NOT throw — this is a development aid, not a hard gate.
 *
 * @param {object} data - Incoming mission status payload
 * @returns {object} The same data object (passthrough)
 */
function normalizeMissionStatus(data) {
    if (!data || typeof data !== 'object') {
        console.warn('[MissionStatus] Invalid payload: expected object, got', typeof data);
        return data;
    }
    const required = {
        rd_stage: 'string',
        rd_iteration: 'number',
        current_cycle: 'number',
        cycle_budget: 'number',
        running: 'boolean',
    };
    for (const [field, expectedType] of Object.entries(required)) {
        if (!(field in data)) {
            console.warn(`[MissionStatus] Missing required field: '${field}'`);
        } else if (typeof data[field] !== expectedType) {
            console.warn(`[MissionStatus] Wrong type for '${field}': expected ${expectedType}, got ${typeof data[field]}`);
        }
    }
    return data;
}

/**
 * Handle mission status events from WebSocket.
 *
 * All server-side emission paths now use the canonical schema
 * (mission_status_schema.py), so payloads always arrive with canonical
 * field names: rd_stage, rd_iteration, current_cycle, cycle_budget.
 * No fallback chains needed.
 *
 * @param {object} data - Canonical mission status payload
 */
export function handleMissionStatusEvent(data) {
    if (!data) return;
    _wsInitialReceived.add('mission_status');
    normalizeMissionStatus(data);

    // Canonical fields — no legacy fallbacks needed
    const stage = data.rd_stage;
    if (stage) {
        updateStageIndicator(stage);
        const stageEl = document.getElementById('stat-stage');
        if (stageEl) stageEl.textContent = stage;
    }

    if (data.rd_iteration !== undefined) {
        const iterEl = document.getElementById('stat-iteration');
        if (iterEl) iterEl.textContent = data.rd_iteration;
    }

    if (data.current_cycle && data.cycle_budget) {
        const cycleEl = document.getElementById('stat-mission-cycle');
        if (cycleEl) cycleEl.textContent = `${data.current_cycle}/${data.cycle_budget}`;
    }

    if (data.project_name !== undefined) {
        const projEl = document.getElementById('stat-project-name');
        if (projEl) projEl.textContent = data.project_name || '-';
    }

    // Toast for stage transitions
    const eventName = data.event;
    if ((eventName === 'stage_change' || eventName === 'engine_stage_change'
            || eventName === 'mission_stage_change') && stage) {
        const prevStage = data.old_stage || '';
        if (prevStage && prevStage !== stage) {
            showToast(`Stage: ${prevStage} → ${stage}`, 'info');
        }
        const statusCard = document.getElementById('status-card');
        if (statusCard) {
            statusCard.classList.add('ws-updated');
            setTimeout(() => statusCard.classList.remove('ws-updated'), 1000);
        }
    }
}

/**
 * Handle journal events from WebSocket
 * @param {object} data - Event data with journal entry
 */
export function handleJournalEvent(data) {
    const eventData = data.data || data;

    if (eventData.event === 'new_entry' && eventData.entry) {
        const entry = eventData.entry;
        const journalEl = document.getElementById('journal');

        if (journalEl) {
            // Prepend new entry to journal
            const entryHtml = `
                <div class="journal-entry" data-entry-id="${entry.timestamp}">
                    <span class="journal-type">${escapeHtml(entry.type || 'unknown')}</span>
                    <span class="journal-time">${entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : ''}</span>
                    <div>${escapeHtml(entry.message || '')}</div>
                </div>
            `;
            journalEl.insertAdjacentHTML('afterbegin', entryHtml);

            // Keep only last 20 entries visible
            const entries = journalEl.querySelectorAll('.journal-entry');
            if (entries.length > 20) {
                for (let i = 20; i < entries.length; i++) {
                    entries[i].remove();
                }
            }
        }
    }
}

/**
 * Initialize WebSocket event handlers
 * Call this after socket.js is initialized
 */
export function initWebSocketHandlers() {
    // Import registerHandler from socket module if available
    // Note: mission_status, journal, mission_agents, investigation_agents, pool_status
    // are registered in main.js::setupWebSocketHandlers() — do NOT register them here
    // to avoid duplicate handler execution.
    if (typeof window.registerSocketHandler === 'function') {
        window.registerSocketHandler('file_events', handleFileEvent);
        window.registerSocketHandler('glassbox_archive', handleGlassboxArchiveEvent);
        window.registerSocketHandler('glassbox', handleGlassboxEvent);
        window.registerSocketHandler('recommendations', handleRecommendationEvent);
        console.log('[Widgets] WebSocket event handlers registered');
    }
}

// Initialize subagent pool widget on load
try {
    initSubagentPoolWidget();
} catch (e) {
    console.debug('[Widgets] Pool widget init deferred:', e.message);
}

// Make handlers available globally for socket.js integration
window.handleFileEvent = handleFileEvent;
window.handleGlassboxArchiveEvent = handleGlassboxArchiveEvent;
window.handleGlassboxEvent = handleGlassboxEvent;
window.handleRecommendationEvent = handleRecommendationEvent;
window.handleMissionStatusEvent = handleMissionStatusEvent;
window.handleJournalEvent = handleJournalEvent;
window.initWebSocketHandlers = initWebSocketHandlers;
window.handleMissionAgentEvent = handleMissionAgentEvent;
window.handleInvestigationAgentEvent = handleInvestigationAgentEvent;
window.toggleMissionParamEdit = toggleMissionParamEdit;
window.closeMissionParamEdit = closeMissionParamEdit;
window.applyMissionParamEdit = applyMissionParamEdit;

// =============================================================================
// TOKEN INTEGRITY WIDGET
// =============================================================================

export async function refreshTokenIntegrityWidget() {
    const badge = document.getElementById('token-integrity-badge');
    const summary = document.getElementById('token-integrity-summary');
    const anomalyList = document.getElementById('token-integrity-anomaly-list');
    if (!badge || !summary || !anomalyList) return;

    try {
        const resp = await fetch('/api/glassbox/integrity');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();

        const zeroCt = (data.summary || {}).zero_token_count || 0;
        const lowCt = (data.summary || {}).low_token_count || 0;
        const anomalyCt = zeroCt + lowCt;
        const scanned = (data.summary || {}).total_scanned || 0;
        const normal = (data.summary || {}).normal_count || 0;

        badge.textContent = anomalyCt + ' anomalous';
        badge.className = 'badge ' + (anomalyCt > 0 ? 'badge-danger' : 'badge-success');

        summary.innerHTML =
            '<div class="atlasforge-stat-box"><div class="atlasforge-stat-value">' + scanned + '</div><div class="atlasforge-stat-label">Scanned</div></div>' +
            '<div class="atlasforge-stat-box"><div class="atlasforge-stat-value">' + normal + '</div><div class="atlasforge-stat-label">Normal</div></div>' +
            '<div class="atlasforge-stat-box"><div class="atlasforge-stat-value" style="color:' + (zeroCt > 0 ? 'var(--danger,#f85149)' : 'var(--accent-green,#3fb950)') + '">' + zeroCt + '</div><div class="atlasforge-stat-label">Zero-Token</div></div>';

        const anomalies = (data.anomalies || []).filter(a => !a.mission_id.startsWith('test_'));
        if (anomalies.length === 0) {
            anomalyList.innerHTML = '<div style="color:var(--accent-green,#3fb950);font-size:0.85em;">All missions healthy</div>';
        } else {
            anomalyList.innerHTML = anomalies.map(a => {
                const date = escapeHtml((a.created_at || '').slice(0, 10));
                const cat = a.category === 'zero_token' ? 'ZERO' : 'LOW';
                const col = a.category === 'zero_token' ? 'var(--danger,#f85149)' : 'var(--accent-yellow,#d29922)';
                return '<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid var(--border,#30363d);font-size:0.8em;">' +
                    '<span style="color:var(--text-primary,#c9d1d9);font-family:monospace;">' + escapeHtml(a.mission_id || '-') + '</span>' +
                    '<span><span style="color:' + col + ';margin-right:6px;">[' + cat + ']</span><span style="color:var(--text-dim,#8b949e);">' + date + '</span></span>' +
                    '</div>';
            }).join('');
        }
    } catch (e) {
        if (badge) { badge.textContent = 'error'; badge.className = 'badge badge-danger'; }
    }
}
