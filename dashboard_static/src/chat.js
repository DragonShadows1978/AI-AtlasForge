/**
 * Dashboard Chat Module (ES6)
 * Chat panel functionality and message rendering
 * Dependencies: core.js
 */

import { showToast, downloadFileViaFetch } from './core.js';

// =============================================================================
// FILE PATH DETECTION
// =============================================================================

// Matches workspace file paths in messages
// Supports both absolute paths (with dynamic base) and relative workspace paths
const filePathRegex = /(?:\/[^\s]+\/workspace\/|workspace\/|artifacts\/|research\/|tests\/)([\w\-\/.]+\.\w+)/g;

/**
 * Process message content for download links
 * @param {string} content - Message content
 * @returns {string} - Content with file paths converted to download links
 */
function processMessageForDownloads(content) {
    return content.replace(filePathRegex, (match, pathPart) => {
        let relativePath = pathPart;

        if (match.includes('/workspace/')) {
            relativePath = pathPart;
        } else if (match.startsWith('workspace/')) {
            relativePath = pathPart;
        } else if (match.startsWith('artifacts/')) {
            relativePath = 'artifacts/' + pathPart;
        } else if (match.startsWith('research/')) {
            relativePath = 'research/' + pathPart;
        } else if (match.startsWith('tests/')) {
            relativePath = 'tests/' + pathPart;
        }

        const filename = relativePath.split('/').pop();
        // Use data attributes for fetch-based download (bypasses Chrome's strict cert checks for self-signed SSL)
        return `<a href="#" class="download-link chat-download" data-download-url="/api/download/${relativePath}" data-filename="${filename}" title="${relativePath}">${filename}</a>`;
    });
}

/**
 * Initialize chat download link handlers using event delegation
 * This handles dynamically added download links in chat messages
 */
export function initChatDownloadHandlers() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    container.addEventListener('click', async (e) => {
        const link = e.target.closest('.chat-download[data-download-url]');
        if (!link) return;

        e.preventDefault();
        const url = link.dataset.downloadUrl;
        const filename = link.dataset.filename;
        try {
            await downloadFileViaFetch(url, filename);
        } catch (err) {
            // Error already shown via toast in downloadFileViaFetch
        }
    });
}

// =============================================================================
// MESSAGE FUNCTIONS
// =============================================================================

/**
 * Add a message to the chat panel
 * @param {string} role - Message role (user, claude, system)
 * @param {string} content - Message content
 * @param {string} timestamp - Optional timestamp
 */
export function addMessage(role, content, timestamp = null, metadata = null) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const normalizedRole = (role || '').toString().trim().toLowerCase();
    const cssRole = normalizedRole === 'codex' ? 'claude' : normalizedRole;

    const div = document.createElement('div');
    div.className = `message ${cssRole}`;

    const time = timestamp
        ? new Date(timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
        : new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});

    let processedContent = content;
    if (normalizedRole === 'claude' || normalizedRole === 'codex') {
        processedContent = processMessageForDownloads(content);
    }

    div.dataset.rawContent = content;

    const meta = metadata || {};
    const metaProvider = (meta.provider || '').toString().trim().toLowerCase();
    const metaDisplayRole = (meta.display_role || meta.displayRole || '').toString().trim().toLowerCase();
    const displayRole = (
        metaDisplayRole ||
        (normalizedRole === 'claude' && metaProvider === 'codex' ? 'codex' : normalizedRole)
    ) || 'unknown';

    div.innerHTML = `<button class="message-copy-btn" onclick="window.copyMessageText(this)">Copy</button><div class="message-meta">${displayRole} - ${time}</div>${processedContent}`;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

/**
 * Copy message text to clipboard
 * @param {HTMLElement} btn - Copy button element
 */
export function copyMessageText(btn) {
    const message = btn.parentElement;
    const text = message.dataset.rawContent;

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => {
            showCopySuccess(btn);
        }).catch(() => {
            fallbackCopy(text, btn);
        });
    } else {
        fallbackCopy(text, btn);
    }
}

/**
 * Fallback copy method using execCommand
 * @param {string} text - Text to copy
 * @param {HTMLElement} btn - Copy button element
 */
function fallbackCopy(text, btn) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showCopySuccess(btn);
    } catch (e) {
        console.error('Copy failed:', e);
        btn.textContent = 'Failed';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    }
    document.body.removeChild(textarea);
}

/**
 * Show copy success feedback
 * @param {HTMLElement} btn - Copy button element
 */
function showCopySuccess(btn) {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
        btn.textContent = 'Copy';
        btn.classList.remove('copied');
    }, 1500);
}

/**
 * Clear all chat messages
 */
export function clearChat() {
    const container = document.getElementById('chat-messages');
    if (container) {
        container.innerHTML = '';
    }
}
