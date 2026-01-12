/**
 * Dashboard Modals Module
 * Modal dialogs, mission modal, recommendations, etc.
 * Dependencies: core.js, api.js
 */

// =============================================================================
// MISSION MODAL STATE
// =============================================================================

let fullMissionText = '';

// =============================================================================
// MISSION MODAL FUNCTIONS
// =============================================================================

function openMissionModal() {
    document.getElementById('mission-full-text').textContent = fullMissionText;
    document.getElementById('mission-modal').classList.add('show');
}

function closeMissionModal() {
    document.getElementById('mission-modal').classList.remove('show');
}

function copyMission() {
    navigator.clipboard.writeText(fullMissionText).then(() => {
        showToast('Mission copied to clipboard');
    });
}

// =============================================================================
// RECOMMENDATIONS STATE & FUNCTIONS
// =============================================================================

let recommendations = [];
let selectedRecId = null;

async function loadRecommendations() {
    const data = await api('/api/recommendations');
    recommendations = data.items || [];
    renderRecommendations();
    updateRecCount();
}

function renderRecommendations() {
    const container = document.getElementById('recommendations-list');
    if (!container) return;

    if (recommendations.length === 0) {
        container.innerHTML = '<div class="rec-placeholder">No recommendations yet. Complete a mission to get suggestions.</div>';
        return;
    }

    container.innerHTML = recommendations.map(rec => `
        <div class="rec-item" onclick="openRecModal('${rec.id}')">
            <div class="rec-item-content">
                <div class="rec-item-title">${escapeHtml(rec.mission_title)}</div>
                <div class="rec-item-preview">${escapeHtml((rec.mission_description || '').substring(0, 100))}${(rec.mission_description || '').length > 100 ? '...' : ''}</div>
            </div>
            <div class="rec-item-meta">
                <span class="rec-cycles-badge">${rec.suggested_cycles || 3} cycles</span>
                <span>${formatDate(rec.created_at)}</span>
            </div>
        </div>
    `).join('');
}

function openRecModal(recId) {
    selectedRecId = recId;
    const rec = recommendations.find(r => r.id === recId);
    if (!rec) return;

    document.getElementById('rec-modal-title').textContent = 'Mission Recommendation';
    document.getElementById('rec-modal-mission-title').textContent = rec.mission_title || 'Untitled';
    document.getElementById('rec-modal-description').textContent = rec.mission_description || 'No description';
    document.getElementById('rec-modal-rationale').textContent = rec.rationale || 'No rationale provided';
    document.getElementById('rec-modal-source').textContent = rec.source_mission_id
        ? `From: ${rec.source_mission_id}${rec.source_mission_summary ? ' - ' + rec.source_mission_summary.substring(0, 100) : ''}`
        : 'Manual recommendation';

    // Set suggested cycles
    const cyclesSelect = document.getElementById('rec-modal-cycles');
    const suggestedCycles = rec.suggested_cycles || 3;
    cyclesSelect.value = suggestedCycles;

    document.getElementById('rec-modal').style.display = 'flex';
}

function closeRecModal() {
    document.getElementById('rec-modal').style.display = 'none';
    selectedRecId = null;
}

async function deleteRecommendation() {
    if (!selectedRecId) return;

    if (!confirm('Delete this recommendation?')) return;

    await api('/api/recommendations/' + selectedRecId, 'DELETE');
    showToast('Recommendation deleted');
    closeRecModal();
    await loadRecommendations();
}

async function setMissionFromRec() {
    if (!selectedRecId) return;

    const cycleBudget = parseInt(document.getElementById('rec-modal-cycles').value) || 3;

    const data = await api('/api/recommendations/' + selectedRecId + '/set-mission', 'POST', {
        cycle_budget: cycleBudget
    });

    if (data.success) {
        showToast(data.message);
        closeRecModal();
        await loadRecommendations();
        if (typeof refresh === 'function') {
            refresh();
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

// Close modal on click outside
document.addEventListener('click', function(e) {
    const modal = document.getElementById('rec-modal');
    if (e.target === modal) {
        closeRecModal();
    }
});

// =============================================================================
// GLASSBOX MODAL
// =============================================================================

function closeGlassboxModal() {
    document.getElementById('glassbox-modal').classList.remove('show');
}

// =============================================================================
// REPO LOG MODAL
// =============================================================================

function closeRepoLogModal(event) {
    // If event provided, check if clicking outside content
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('repo-log-modal').style.display = 'none';
}

// Debug: mark modals module loaded
console.log('Modals module loaded');
