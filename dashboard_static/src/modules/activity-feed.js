/**
 * GitHub Activity Feed Widget
 * ============================
 * Displays a scrollable timeline of recent commits, PRs, and issues
 * across all configured GitHub repos.
 *
 * Features:
 * - Real-time activity timeline
 * - Activity type icons
 * - Relative timestamps
 * - Auto-refresh every 2 minutes
 */

// Module-level state
let activityState = {
    activities: [],
    lastUpdate: null,
    updateInterval: null
};

/**
 * Initialize the activity feed widget
 */
export function initActivityFeed() {
    const container = document.getElementById('github-activity-feed');
    if (!container) return;

    container.innerHTML = renderActivityFeedWidget();
    refreshActivityFeed();

    // Refresh every 2 minutes
    activityState.updateInterval = setInterval(refreshActivityFeed, 120000);
}

/**
 * Render the activity feed widget structure
 */
function renderActivityFeedWidget() {
    return `
        <div class="activity-feed-header">
            <span class="activity-feed-title">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                </svg>
                Activity Feed
            </span>
            <button class="btn btn-xs" onclick="window.refreshActivityFeed()" title="Refresh">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="23 4 23 10 17 10"></polyline>
                    <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
                </svg>
            </button>
        </div>
        <div class="activity-list" id="activity-list">
            <div class="activity-empty">Loading...</div>
        </div>
        <div class="activity-feed-footer" id="activity-last-update">Never updated</div>
    `;
}

/**
 * Refresh the activity feed from API
 */
async function refreshActivityFeed() {
    try {
        const response = await fetch('/api/github/activity?limit=15');
        const data = await response.json();

        activityState.lastUpdate = new Date();

        if (data.success) {
            activityState.activities = data.activities || [];
            renderActivities(activityState.activities);
        } else {
            renderActivitiesError(data.error || 'Failed to load activities');
        }

        updateActivityLastUpdate();
    } catch (error) {
        console.warn('Failed to refresh activity feed:', error);
        renderActivitiesError('Network error');
    }
}

/**
 * Render the activities list
 */
function renderActivities(activities) {
    const list = document.getElementById('activity-list');
    if (!list) return;

    if (!activities || activities.length === 0) {
        list.innerHTML = '<div class="activity-empty">No recent activity</div>';
        return;
    }

    list.innerHTML = activities.map(a => renderActivityItem(a)).join('');
}

/**
 * Render error state
 */
function renderActivitiesError(error) {
    const list = document.getElementById('activity-list');
    if (!list) return;

    list.innerHTML = `<div class="activity-empty activity-error">${error}</div>`;
}

/**
 * Render a single activity item
 */
function renderActivityItem(activity) {
    const icon = getActivityIcon(activity.type);
    const desc = getActivityDescription(activity);
    const time = formatTimeAgo(activity.created_at);
    const repoShort = activity.repo ? activity.repo.split('/').pop() : '';

    return `
        <div class="activity-item">
            <span class="activity-icon">${icon}</span>
            <div class="activity-content">
                <div class="activity-main">
                    <span class="activity-actor">${activity.actor || 'unknown'}</span>
                    <span class="activity-desc">${desc}</span>
                </div>
                <div class="activity-meta">
                    <span class="activity-repo">${repoShort}</span>
                    <span class="activity-time">${time}</span>
                </div>
            </div>
        </div>
    `;
}

/**
 * Get icon for activity type
 */
function getActivityIcon(type) {
    const icons = {
        'PushEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>',
        'PullRequestEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="18" r="3"></circle><circle cx="6" cy="6" r="3"></circle><path d="M13 6h3a2 2 0 0 1 2 2v7"></path><line x1="6" y1="9" x2="6" y2="21"></line></svg>',
        'IssuesEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
        'CreateEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>',
        'DeleteEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
        'IssueCommentEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path></svg>',
        'PullRequestReviewEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>',
        'ForkEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="18" r="3"></circle><circle cx="6" cy="6" r="3"></circle><circle cx="18" cy="6" r="3"></circle><path d="M18 9v1a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9"></path><line x1="12" y1="12" x2="12" y2="15"></line></svg>',
        'WatchEvent': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>'
    };
    return icons[type] || '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle></svg>';
}

/**
 * Get description for activity
 */
function getActivityDescription(activity) {
    const type = activity.type;
    const payload = activity.payload || {};

    switch (type) {
        case 'PushEvent':
            const size = payload.size || payload.commits?.length || 0;
            return `pushed ${size} commit${size !== 1 ? 's' : ''}`;
        case 'PullRequestEvent':
            return `${payload.action || 'updated'} PR #${payload.number || '?'}`;
        case 'IssuesEvent':
            return `${payload.action || 'updated'} issue #${payload.number || '?'}`;
        case 'CreateEvent':
            return `created ${payload.ref_type || 'ref'} ${payload.ref || ''}`;
        case 'DeleteEvent':
            return `deleted ${payload.ref_type || 'ref'} ${payload.ref || ''}`;
        case 'IssueCommentEvent':
            return `commented on #${payload.issue_number || '?'}`;
        case 'PullRequestReviewEvent':
            return `reviewed PR #${payload.pr_number || '?'}`;
        case 'ForkEvent':
            return 'forked the repository';
        case 'WatchEvent':
            return 'starred the repository';
        default:
            return type.replace('Event', '').toLowerCase();
    }
}

/**
 * Format timestamp as relative time
 */
function formatTimeAgo(isoStr) {
    if (!isoStr) return '';

    const date = new Date(isoStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;

    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;

    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;

    const diffWeeks = Math.floor(diffDays / 7);
    return `${diffWeeks}w ago`;
}

/**
 * Update the last update timestamp
 */
function updateActivityLastUpdate() {
    const el = document.getElementById('activity-last-update');
    if (el && activityState.lastUpdate) {
        const time = activityState.lastUpdate.toLocaleTimeString();
        el.textContent = `Updated: ${time}`;
    }
}

/**
 * Cleanup on module unload
 */
export function destroyActivityFeed() {
    if (activityState.updateInterval) {
        clearInterval(activityState.updateInterval);
        activityState.updateInterval = null;
    }
}

// Expose to window for onclick handlers
window.refreshActivityFeed = refreshActivityFeed;

// Export for external use
export {
    refreshActivityFeed,
    activityState
};
