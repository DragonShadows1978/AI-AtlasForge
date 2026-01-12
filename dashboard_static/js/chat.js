/**
 * Dashboard Chat Module
 * Chat panel functionality and message rendering
 * Dependencies: core.js, socket.js
 */

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
    // Replace workspace file paths with download links
    return content.replace(filePathRegex, (match, pathPart) => {
        // Determine the relative path for the download URL
        let relativePath = pathPart;

        // Handle full paths
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
        return `<a href="/api/download/${relativePath}" class="download-link" download title="${relativePath}">${filename}</a>`;
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
function addMessage(role, content, timestamp = null) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const div = document.createElement('div');
    div.className = `message ${role}`;

    // Use provided timestamp or fall back to current time
    const time = timestamp
        ? new Date(timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
        : new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});

    // Process content for download links (only for Claude messages)
    let processedContent = content;
    if (role === 'claude') {
        processedContent = processMessageForDownloads(content);
    }

    // Store raw content for copy functionality
    div.dataset.rawContent = content;

    div.innerHTML = `<button class="message-copy-btn" onclick="copyMessageText(this)">Copy</button><div class="message-meta">${role} - ${time}</div>${processedContent}`;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

/**
 * Copy message text to clipboard
 * @param {HTMLElement} btn - Copy button element
 */
function copyMessageText(btn) {
    const message = btn.parentElement;
    const text = message.dataset.rawContent;

    // Try modern clipboard API first, fallback to execCommand
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

// Debug: mark chat module loaded
console.log('Chat module loaded');
