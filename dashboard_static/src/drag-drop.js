/**
 * Dashboard Widget Drag-Drop Module (Enhanced)
 * Enables drag-and-drop reordering of widgets between columns
 * with visual feedback, localStorage persistence, touch support,
 * keyboard accessibility, undo/redo, and layout presets.
 *
 * Cycle 2 Enhancements:
 * - Touch/mobile support with custom touch event handlers
 * - Keyboard accessibility with ARIA and screen reader support
 * - Undo/redo history stack for layout changes
 * - Smooth CSS animations (see main.css)
 * - Layout preset system for saving/switching configurations
 */

// =============================================================================
// CONSTANTS
// =============================================================================

const STORAGE_KEY = 'rdeWidgetLayout';
const PRESETS_KEY = 'rdeLayoutPresets';
const LOCK_STORAGE_KEY = 'rdeTilesLocked';
const COLUMN_SELECTORS = ['.widget-column-1', '.widget-column-2'];
const CARD_SELECTOR = '.card[id]'; // Only cards with IDs can be dragged

// Touch constants
const TOUCH_DRAG_THRESHOLD = 10; // Pixels before drag activates
const TOUCH_LONG_PRESS_MS = 300; // Ms for long press to activate drag

// History limits
const MAX_HISTORY_LENGTH = 50;

// =============================================================================
// STATE
// =============================================================================

let draggedCard = null;
let defaultLayout = null;  // Captured on first load
let dragStartY = 0;
let currentDropTarget = null;

// Touch state
let touchState = {
    active: false,
    startX: 0,
    startY: 0,
    currentCard: null,
    dragImage: null,
    isDragging: false,
    longPressTimer: null,
    scrollLock: false
};

// Keyboard state
let keyboardState = {
    grabbedCard: null,
    isGrabbed: false,
    preGrabLayout: null  // Snapshot before grab for undo
};

// History state (undo/redo)
const layoutHistory = {
    past: [],
    present: null,
    future: []
};

// Preset state
let activePreset = null;

// Toolbar UI state
let deletePresetTarget = null;  // Name of preset pending deletion

// Tiles lock state
let tilesLocked = false;

// =============================================================================
// INITIALIZATION
// =============================================================================

/**
 * Initialize drag-and-drop functionality for widget columns.
 * Should be called once on DOMContentLoaded.
 */
export function initDragDrop() {
    // Capture default layout before any restoration
    captureDefaultLayout();

    // Restore saved layout if exists
    restoreLayout();

    // Initialize history with current layout
    initializeHistory();

    // Initialize columns as drop zones
    COLUMN_SELECTORS.forEach(selector => {
        const column = document.querySelector(selector);
        if (column) initDropZone(column);
    });

    // Initialize cards as draggable
    document.querySelectorAll(CARD_SELECTOR).forEach(card => {
        if (isInDraggableColumn(card)) {
            initDraggableCard(card);
        }
    });

    // Initialize touch support
    initTouchSupport();

    // Initialize keyboard support
    initKeyboardSupport();

    // Create ARIA live region for announcements
    createLiveRegion();

    // Initialize preset system
    initPresets();

    // Initialize tile lock system
    initTileLock();

    // Initialize toolbar UI (Cycle 3)
    initToolbarUI();

    // Expose functions to window for external use
    exposeToWindow();

    console.log('Drag-drop module initialized (enhanced with touch, keyboard, history, presets, toolbar UI)');
}

// =============================================================================
// DRAGGABLE CARD SETUP
// =============================================================================

function initDraggableCard(card) {
    // Only allow dragging via header or the card itself
    card.setAttribute('draggable', 'true');

    // Keyboard accessibility
    card.setAttribute('tabindex', '0');
    card.setAttribute('role', 'listitem');
    card.setAttribute('aria-grabbed', 'false');
    card.setAttribute('aria-describedby', 'drag-instructions');

    card.addEventListener('dragstart', handleDragStart);
    card.addEventListener('dragend', handleDragEnd);
}

function handleDragStart(e) {
    // Prevent dragging when tiles are locked
    if (tilesLocked) {
        e.preventDefault();
        return;
    }

    draggedCard = e.target.closest('.card');

    if (!draggedCard || !draggedCard.id) {
        e.preventDefault();
        return;
    }

    // Save current state for undo
    pushHistoryBeforeDrag();

    dragStartY = e.clientY;

    // Set drag data
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedCard.id);

    // Create a custom drag image (optional - browser default works fine)
    // This adds a slight rotation effect
    const rect = draggedCard.getBoundingClientRect();
    e.dataTransfer.setDragImage(draggedCard, e.clientX - rect.left, e.clientY - rect.top);

    // Add visual state with slight delay for ghost image to be captured
    setTimeout(() => {
        if (draggedCard) {
            draggedCard.classList.add('dragging');
            // Mark siblings for animation
            markSiblingsForAnimation(draggedCard);
        }
    }, 0);

    announceChange(`Picked up ${getCardLabel(draggedCard)}`);
}

function handleDragEnd(e) {
    if (draggedCard) {
        draggedCard.classList.remove('dragging');
        unmarkSiblingsForAnimation();
    }
    draggedCard = null;
    currentDropTarget = null;

    // Clear all drop indicators
    clearDropIndicators();
}

// =============================================================================
// DROP ZONE SETUP
// =============================================================================

function initDropZone(column) {
    column.setAttribute('role', 'list');
    column.setAttribute('aria-label', 'Widget column');

    column.addEventListener('dragenter', handleDragEnter);
    column.addEventListener('dragover', handleDragOver);
    column.addEventListener('dragleave', handleDragLeave);
    column.addEventListener('drop', handleDrop);
}

function handleDragEnter(e) {
    e.preventDefault();
    const column = e.currentTarget;
    column.classList.add('drag-over');
}

function handleDragOver(e) {
    e.preventDefault();  // CRITICAL: enables drop
    e.dataTransfer.dropEffect = 'move';

    const column = e.currentTarget;

    // Find insertion point
    const afterCard = getCardAtPosition(column, e.clientY);

    // Update indicator only if position changed
    if (afterCard !== currentDropTarget) {
        currentDropTarget = afterCard;
        updateDropIndicator(column, afterCard);
    }
}

function handleDragLeave(e) {
    // Only remove if actually leaving (not entering a child)
    if (!e.currentTarget.contains(e.relatedTarget)) {
        e.currentTarget.classList.remove('drag-over');
        clearDropIndicators();
    }
}

function handleDrop(e) {
    e.preventDefault();

    const column = e.currentTarget;
    column.classList.remove('drag-over');

    if (!draggedCard) return;

    // Find insertion point
    const afterCard = getCardAtPosition(column, e.clientY);

    // Insert dragged card at new position
    if (afterCard) {
        column.insertBefore(draggedCard, afterCard);
    } else {
        column.appendChild(draggedCard);
    }

    unmarkSiblingsForAnimation();
    clearDropIndicators();

    // Persist new layout
    saveLayout();

    // Show feedback
    showToast('Layout saved');
    announceChange(`Dropped ${getCardLabel(draggedCard)}`);
}

// =============================================================================
// TOUCH SUPPORT
// =============================================================================

function initTouchSupport() {
    COLUMN_SELECTORS.forEach(selector => {
        const column = document.querySelector(selector);
        if (!column) return;

        column.addEventListener('touchstart', handleTouchStart, { passive: false });
        column.addEventListener('touchmove', handleTouchMove, { passive: false });
        column.addEventListener('touchend', handleTouchEnd);
        column.addEventListener('touchcancel', handleTouchCancel);
    });
}

function handleTouchStart(e) {
    // Prevent touch drag when tiles are locked
    if (tilesLocked) return;

    const card = e.target.closest(CARD_SELECTOR);
    if (!card || !isInDraggableColumn(card)) return;

    const touch = e.touches[0];
    touchState.startX = touch.clientX;
    touchState.startY = touch.clientY;
    touchState.currentCard = card;
    touchState.active = true;
    touchState.isDragging = false;

    // Long press timer to initiate drag
    touchState.longPressTimer = setTimeout(() => {
        if (touchState.active && touchState.currentCard) {
            startTouchDrag(touchState.currentCard, touch.clientX, touch.clientY);
        }
    }, TOUCH_LONG_PRESS_MS);
}

function handleTouchMove(e) {
    if (!touchState.active || !touchState.currentCard) return;

    const touch = e.touches[0];
    const deltaX = touch.clientX - touchState.startX;
    const deltaY = touch.clientY - touchState.startY;
    const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);

    // Cancel long press if finger moved too much before drag started
    if (!touchState.isDragging && distance > TOUCH_DRAG_THRESHOLD) {
        clearTimeout(touchState.longPressTimer);

        // Start drag if moved horizontally more than vertically (avoid scroll conflict)
        if (Math.abs(deltaX) > Math.abs(deltaY) * 0.5) {
            startTouchDrag(touchState.currentCard, touch.clientX, touch.clientY);
        } else {
            // User is scrolling, abort drag
            touchState.active = false;
            return;
        }
    }

    if (touchState.isDragging) {
        e.preventDefault(); // Prevent scroll during drag
        updateTouchDragPosition(touch.clientX, touch.clientY);
    }
}

function handleTouchEnd(e) {
    clearTimeout(touchState.longPressTimer);

    if (touchState.isDragging) {
        completeTouchDrag();
    }

    resetTouchState();
}

function handleTouchCancel(e) {
    clearTimeout(touchState.longPressTimer);
    cancelTouchDrag();
    resetTouchState();
}

function startTouchDrag(card, x, y) {
    touchState.isDragging = true;

    // Save state for undo
    pushHistoryBeforeDrag();

    draggedCard = card;
    card.classList.add('dragging', 'touch-dragging');
    markSiblingsForAnimation(card);

    // Create touch drag image (visual clone that follows finger)
    createTouchDragImage(card, x, y);

    // Haptic feedback if available
    if (navigator.vibrate) {
        navigator.vibrate(30);
    }

    announceChange(`Picked up ${getCardLabel(card)}. Move finger to reposition.`);
}

function createTouchDragImage(card, x, y) {
    const rect = card.getBoundingClientRect();
    const clone = card.cloneNode(true);

    clone.id = 'touch-drag-image';
    clone.style.cssText = `
        position: fixed;
        left: ${rect.left}px;
        top: ${rect.top}px;
        width: ${rect.width}px;
        height: ${rect.height}px;
        pointer-events: none;
        z-index: 10000;
        opacity: 0.85;
        transform: rotate(2deg) scale(1.02);
        box-shadow: 0 15px 40px rgba(0, 0, 0, 0.5);
        transition: none;
    `;

    document.body.appendChild(clone);
    touchState.dragImage = clone;

    // Store offset for positioning
    touchState.offsetX = x - rect.left;
    touchState.offsetY = y - rect.top;
}

function updateTouchDragPosition(x, y) {
    if (!touchState.dragImage) return;

    const newLeft = x - touchState.offsetX;
    const newTop = y - touchState.offsetY;

    touchState.dragImage.style.left = newLeft + 'px';
    touchState.dragImage.style.top = newTop + 'px';

    // Find drop target
    const elemBelow = document.elementFromPoint(x, y);
    if (!elemBelow) return;

    const column = elemBelow.closest(COLUMN_SELECTORS.join(','));
    if (column) {
        column.classList.add('drag-over');
        const afterCard = getCardAtPosition(column, y);
        if (afterCard !== currentDropTarget) {
            currentDropTarget = afterCard;
            updateDropIndicator(column, afterCard);
        }
    }

    // Clear indicators from other columns
    COLUMN_SELECTORS.forEach(sel => {
        const col = document.querySelector(sel);
        if (col && col !== column) {
            col.classList.remove('drag-over');
        }
    });
}

function completeTouchDrag() {
    if (!draggedCard || !touchState.dragImage) return;

    const rect = touchState.dragImage.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    // Find target column
    const elemBelow = document.elementFromPoint(centerX, centerY);
    const column = elemBelow?.closest(COLUMN_SELECTORS.join(','));

    if (column) {
        const afterCard = getCardAtPosition(column, centerY);
        if (afterCard) {
            column.insertBefore(draggedCard, afterCard);
        } else {
            column.appendChild(draggedCard);
        }

        saveLayout();
        showToast('Layout saved');
        announceChange(`Dropped ${getCardLabel(draggedCard)}`);
    }

    removeTouchDragImage();

    if (draggedCard) {
        draggedCard.classList.remove('dragging', 'touch-dragging');
        draggedCard.classList.add('drop-animation');
        setTimeout(() => draggedCard?.classList.remove('drop-animation'), 300);
    }

    unmarkSiblingsForAnimation();
    clearDropIndicators();
    draggedCard = null;
}

function cancelTouchDrag() {
    removeTouchDragImage();
    if (draggedCard) {
        draggedCard.classList.remove('dragging', 'touch-dragging');
    }
    unmarkSiblingsForAnimation();
    clearDropIndicators();
    draggedCard = null;

    // Undo the history push since nothing changed
    if (layoutHistory.past.length > 0) {
        layoutHistory.past.pop();
    }
}

function removeTouchDragImage() {
    if (touchState.dragImage) {
        touchState.dragImage.remove();
        touchState.dragImage = null;
    }
}

function resetTouchState() {
    touchState = {
        active: false,
        startX: 0,
        startY: 0,
        currentCard: null,
        dragImage: null,
        isDragging: false,
        longPressTimer: null,
        scrollLock: false
    };
}

// =============================================================================
// KEYBOARD SUPPORT
// =============================================================================

function initKeyboardSupport() {
    // Global keyboard shortcuts for undo/redo
    document.addEventListener('keydown', handleGlobalKeyDown);

    // Per-card keyboard handling
    document.querySelectorAll(CARD_SELECTOR).forEach(card => {
        if (isInDraggableColumn(card)) {
            card.addEventListener('keydown', handleCardKeyDown);
        }
    });
}

function handleGlobalKeyDown(e) {
    // Undo: Ctrl+Z or Cmd+Z
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
    }
    // Redo: Ctrl+Shift+Z or Cmd+Shift+Z
    else if ((e.ctrlKey || e.metaKey) && e.key === 'z' && e.shiftKey) {
        e.preventDefault();
        redo();
    }
    // Redo: Ctrl+Y (alternative)
    else if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
        e.preventDefault();
        redo();
    }
}

function handleCardKeyDown(e) {
    // Don't intercept keyboard events from interactive form elements
    // This allows textareas, inputs, buttons, etc. to handle Enter key normally
    if (e.target.matches('input, textarea, select, button, a, [contenteditable="true"]')) {
        return;
    }

    const card = e.target.closest(CARD_SELECTOR);
    if (!card) return;

    switch (e.key) {
        case 'Enter':
        case ' ':
            e.preventDefault();
            toggleCardGrab(card);
            break;

        case 'ArrowUp':
            e.preventDefault();
            if (keyboardState.isGrabbed && keyboardState.grabbedCard === card) {
                moveCardByKeyboard(card, 'up');
            }
            break;

        case 'ArrowDown':
            e.preventDefault();
            if (keyboardState.isGrabbed && keyboardState.grabbedCard === card) {
                moveCardByKeyboard(card, 'down');
            }
            break;

        case 'ArrowLeft':
            e.preventDefault();
            if (keyboardState.isGrabbed && keyboardState.grabbedCard === card) {
                moveCardToColumn(card, 'left');
            }
            break;

        case 'ArrowRight':
            e.preventDefault();
            if (keyboardState.isGrabbed && keyboardState.grabbedCard === card) {
                moveCardToColumn(card, 'right');
            }
            break;

        case 'Escape':
            if (keyboardState.isGrabbed) {
                e.preventDefault();
                cancelCardGrab();
            }
            break;
    }
}

function toggleCardGrab(card) {
    if (keyboardState.isGrabbed && keyboardState.grabbedCard === card) {
        // Release
        releaseCardGrab(card);
    } else {
        // Grab (release any previous)
        if (keyboardState.grabbedCard) {
            releaseCardGrab(keyboardState.grabbedCard);
        }
        grabCard(card);
    }
}

function grabCard(card) {
    // Capture layout snapshot BEFORE any changes (for undo on release)
    keyboardState.preGrabLayout = getCurrentLayout();

    keyboardState.grabbedCard = card;
    keyboardState.isGrabbed = true;

    card.setAttribute('aria-grabbed', 'true');
    card.classList.add('keyboard-grabbed');

    announceChange(`Grabbed ${getCardLabel(card)}. Use arrow keys to move, Enter to drop, Escape to cancel.`);
}

function releaseCardGrab(card) {
    const currentLayout = getCurrentLayout();
    const preGrab = keyboardState.preGrabLayout;

    keyboardState.grabbedCard = null;
    keyboardState.isGrabbed = false;
    keyboardState.preGrabLayout = null;

    card.setAttribute('aria-grabbed', 'false');
    card.classList.remove('keyboard-grabbed');

    // Push to history if layout actually changed
    if (preGrab && JSON.stringify(currentLayout) !== JSON.stringify(preGrab)) {
        layoutHistory.past.push(preGrab);
        layoutHistory.present = currentLayout;
        layoutHistory.future = [];  // Clear redo stack on new action

        // Limit history length
        if (layoutHistory.past.length > MAX_HISTORY_LENGTH) {
            layoutHistory.past.shift();
        }
    }

    saveLayout();
    showToast('Layout saved');
    announceChange(`Dropped ${getCardLabel(card)}`);
}

function cancelCardGrab() {
    if (!keyboardState.grabbedCard) return;

    const card = keyboardState.grabbedCard;
    const preGrab = keyboardState.preGrabLayout;

    card.setAttribute('aria-grabbed', 'false');
    card.classList.remove('keyboard-grabbed');

    keyboardState.grabbedCard = null;
    keyboardState.isGrabbed = false;
    keyboardState.preGrabLayout = null;

    // Restore original position if layout changed
    if (preGrab) {
        applyLayout(preGrab);
        layoutHistory.present = preGrab;
    }

    announceChange('Cancelled move');
}

function moveCardByKeyboard(card, direction) {
    const column = card.closest(COLUMN_SELECTORS.join(','));
    if (!column) return;

    const cards = [...column.querySelectorAll(CARD_SELECTOR)];
    const currentIndex = cards.indexOf(card);

    if (direction === 'up' && currentIndex > 0) {
        const prevCard = cards[currentIndex - 1];
        column.insertBefore(card, prevCard);
        card.focus();
        announceChange(`Moved up to position ${currentIndex}`);
    } else if (direction === 'down' && currentIndex < cards.length - 1) {
        const nextCard = cards[currentIndex + 1];
        column.insertBefore(nextCard, card);
        card.focus();
        announceChange(`Moved down to position ${currentIndex + 2}`);
    }
}

function moveCardToColumn(card, direction) {
    const currentColumn = card.closest(COLUMN_SELECTORS.join(','));
    if (!currentColumn) return;

    const currentIndex = COLUMN_SELECTORS.findIndex(sel => currentColumn.matches(sel));
    let targetIndex;

    if (direction === 'left') {
        targetIndex = currentIndex - 1;
    } else if (direction === 'right') {
        targetIndex = currentIndex + 1;
    }

    if (targetIndex < 0 || targetIndex >= COLUMN_SELECTORS.length) {
        announceChange('Cannot move further in that direction');
        return;
    }

    const targetColumn = document.querySelector(COLUMN_SELECTORS[targetIndex]);
    if (!targetColumn) return;

    targetColumn.appendChild(card);
    card.focus();

    const columnName = direction === 'left' ? 'left column' : 'right column';
    announceChange(`Moved to ${columnName}`);
}

// =============================================================================
// ARIA / ACCESSIBILITY HELPERS
// =============================================================================

function createLiveRegion() {
    // Create hidden instructions element
    if (!document.getElementById('drag-instructions')) {
        const instructions = document.createElement('div');
        instructions.id = 'drag-instructions';
        instructions.className = 'sr-only';
        instructions.textContent = 'Press Enter or Space to grab this widget. While grabbed, use arrow keys to move it, Enter to drop, or Escape to cancel.';
        document.body.appendChild(instructions);
    }

    // Create live region for announcements
    if (!document.getElementById('drag-announcements')) {
        const liveRegion = document.createElement('div');
        liveRegion.id = 'drag-announcements';
        liveRegion.setAttribute('aria-live', 'polite');
        liveRegion.setAttribute('aria-atomic', 'true');
        liveRegion.className = 'sr-only';
        document.body.appendChild(liveRegion);
    }
}

function announceChange(message) {
    const liveRegion = document.getElementById('drag-announcements');
    if (liveRegion) {
        // Clear and re-set to ensure announcement
        liveRegion.textContent = '';
        requestAnimationFrame(() => {
            liveRegion.textContent = message;
        });
    }
}

function getCardLabel(card) {
    const header = card.querySelector('.card-header h3, h3');
    return header ? header.textContent.trim() : card.id;
}

// =============================================================================
// ANIMATION HELPERS
// =============================================================================

function markSiblingsForAnimation(card) {
    const column = card.closest(COLUMN_SELECTORS.join(','));
    if (!column) return;

    column.querySelectorAll(CARD_SELECTOR).forEach(sibling => {
        if (sibling !== card) {
            sibling.classList.add('drag-reordering');
        }
    });
}

function unmarkSiblingsForAnimation() {
    document.querySelectorAll('.drag-reordering').forEach(el => {
        el.classList.remove('drag-reordering');
    });
}

// =============================================================================
// POSITION CALCULATION
// =============================================================================

function getCardAtPosition(column, y) {
    const cards = [...column.querySelectorAll(CARD_SELECTOR + ':not(.dragging)')];

    for (const card of cards) {
        const rect = card.getBoundingClientRect();
        const midpoint = rect.top + rect.height / 2;

        if (y < midpoint) {
            return card;
        }
    }

    return null;  // Append at end
}

function updateDropIndicator(column, afterCard) {
    clearDropIndicators();

    if (afterCard && afterCard !== draggedCard) {
        afterCard.classList.add('drop-above');
    } else if (!afterCard) {
        // Show indicator at bottom of column
        const lastCard = column.querySelector(CARD_SELECTOR + ':last-child:not(.dragging)');
        if (lastCard && lastCard !== draggedCard) {
            lastCard.classList.add('drop-below');
        }
    }
}

function clearDropIndicators() {
    document.querySelectorAll('.drop-above, .drop-below').forEach(el => {
        el.classList.remove('drop-above', 'drop-below');
    });
    document.querySelectorAll('.drag-over').forEach(el => {
        el.classList.remove('drag-over');
    });
}

// =============================================================================
// UNDO/REDO HISTORY
// =============================================================================

function initializeHistory() {
    layoutHistory.present = getCurrentLayout();
    layoutHistory.past = [];
    layoutHistory.future = [];
}

function pushHistoryBeforeDrag() {
    const current = getCurrentLayout();

    // Only push if layout actually changed
    if (JSON.stringify(current) === JSON.stringify(layoutHistory.present)) {
        return;
    }

    // Push current to past
    layoutHistory.past.push(layoutHistory.present);
    layoutHistory.present = current;

    // Clear future (no redo after new action)
    layoutHistory.future = [];

    // Limit history length
    if (layoutHistory.past.length > MAX_HISTORY_LENGTH) {
        layoutHistory.past.shift();
    }
}

function getCurrentLayout() {
    const layout = [];
    COLUMN_SELECTORS.forEach((selector, columnIndex) => {
        const column = document.querySelector(selector);
        if (!column) return;

        column.querySelectorAll(CARD_SELECTOR).forEach((card, order) => {
            if (card.id) {
                layout.push({
                    id: card.id,
                    column: columnIndex,
                    order: order
                });
            }
        });
    });
    return layout;
}

/**
 * Undo last layout change
 */
export function undo() {
    if (layoutHistory.past.length === 0) {
        showToast('Nothing to undo');
        announceChange('Nothing to undo');
        return false;
    }

    // Push current to future
    layoutHistory.future.push(layoutHistory.present);

    // Pop from past
    layoutHistory.present = layoutHistory.past.pop();

    // Apply layout
    applyLayout(layoutHistory.present);
    saveLayoutToStorage(layoutHistory.present);

    showToast('Undone');
    announceChange('Undo: layout restored');
    updateToolbarState();
    return true;
}

/**
 * Redo last undone layout change
 */
export function redo() {
    if (layoutHistory.future.length === 0) {
        showToast('Nothing to redo');
        announceChange('Nothing to redo');
        return false;
    }

    // Push current to past
    layoutHistory.past.push(layoutHistory.present);

    // Pop from future
    layoutHistory.present = layoutHistory.future.pop();

    // Apply layout
    applyLayout(layoutHistory.present);
    saveLayoutToStorage(layoutHistory.present);

    showToast('Redone');
    announceChange('Redo: layout restored');
    updateToolbarState();
    return true;
}

/**
 * Check if undo is available
 */
export function canUndo() {
    return layoutHistory.past.length > 0;
}

/**
 * Check if redo is available
 */
export function canRedo() {
    return layoutHistory.future.length > 0;
}

// =============================================================================
// LAYOUT PRESETS
// =============================================================================

function initPresets() {
    // Load active preset name from storage
    try {
        const data = localStorage.getItem(PRESETS_KEY);
        if (data) {
            const parsed = JSON.parse(data);
            activePreset = parsed.activePreset || null;
        }
    } catch (e) {
        console.warn('Failed to load preset data:', e);
    }
}

/**
 * Get all saved presets
 * @returns {Object} - Map of preset names to layouts
 */
export function getPresets() {
    try {
        const data = localStorage.getItem(PRESETS_KEY);
        if (data) {
            const parsed = JSON.parse(data);
            return parsed.presets || {};
        }
    } catch (e) {
        console.warn('Failed to load presets:', e);
    }
    return {};
}

/**
 * Get the active preset name
 * @returns {string|null} - Active preset name or null
 */
export function getActivePreset() {
    return activePreset;
}

// Maximum preset name length
const MAX_PRESET_NAME_LENGTH = 100;

/**
 * Sanitize a preset name by removing HTML tags and limiting length
 * @param {string} name - Raw preset name
 * @returns {string} - Sanitized name
 */
function sanitizePresetName(name) {
    if (!name || typeof name !== 'string') return '';

    // Remove HTML tags
    const stripped = name.replace(/<[^>]*>/g, '');

    // Trim whitespace and limit length
    return stripped.trim().substring(0, MAX_PRESET_NAME_LENGTH);
}

/**
 * Validate a layout item structure
 * @param {Object} item - Layout item
 * @returns {boolean} - Whether item is valid
 */
function isValidLayoutItem(item) {
    return item &&
        typeof item === 'object' &&
        typeof item.id === 'string' &&
        typeof item.column === 'number' &&
        typeof item.order === 'number';
}

/**
 * Save current layout as a preset
 * @param {string} name - Preset name
 * @returns {boolean} - Success
 */
export function savePreset(name) {
    if (!name || typeof name !== 'string') {
        showToast('Invalid preset name');
        return false;
    }

    const sanitizedName = sanitizePresetName(name);
    if (sanitizedName.length === 0) {
        showToast('Preset name cannot be empty');
        return false;
    }

    try {
        const presets = getPresets();
        presets[sanitizedName] = getCurrentLayout();

        const data = {
            presets: presets,
            activePreset: sanitizedName
        };

        localStorage.setItem(PRESETS_KEY, JSON.stringify(data));
        activePreset = sanitizedName;

        showToast(`Preset "${sanitizedName}" saved`);
        announceChange(`Saved layout preset ${sanitizedName}`);
        updateToolbarState();
        return true;
    } catch (e) {
        console.error('Failed to save preset:', e);
        showToast('Failed to save preset');
        return false;
    }
}

/**
 * Load a preset by name
 * @param {string} name - Preset name
 * @returns {boolean} - Success
 */
export function loadPreset(name) {
    const presets = getPresets();

    if (!presets[name]) {
        showToast(`Preset "${name}" not found`);
        return false;
    }

    const targetLayout = presets[name];
    const currentLayout = getCurrentLayout();

    // Only push to history if layout will actually change
    if (JSON.stringify(currentLayout) !== JSON.stringify(targetLayout)) {
        layoutHistory.past.push(layoutHistory.present);
        layoutHistory.future = [];  // Clear redo stack

        // Limit history length
        if (layoutHistory.past.length > MAX_HISTORY_LENGTH) {
            layoutHistory.past.shift();
        }
    }

    applyLayout(targetLayout);
    saveLayoutToStorage(targetLayout);
    layoutHistory.present = targetLayout;

    // Update active preset
    try {
        const data = JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}');
        data.activePreset = name;
        localStorage.setItem(PRESETS_KEY, JSON.stringify(data));
        activePreset = name;
    } catch (e) {
        console.warn('Failed to update active preset:', e);
    }

    showToast(`Preset "${name}" loaded`);
    announceChange(`Loaded layout preset ${name}`);
    updateToolbarState();
    return true;
}

/**
 * Delete a preset
 * @param {string} name - Preset name
 * @returns {boolean} - Success
 */
export function deletePreset(name) {
    try {
        const data = JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}');
        const presets = data.presets || {};

        if (!presets[name]) {
            showToast(`Preset "${name}" not found`);
            return false;
        }

        delete presets[name];

        // Clear active if deleted
        if (data.activePreset === name) {
            data.activePreset = null;
            activePreset = null;
        }

        data.presets = presets;
        localStorage.setItem(PRESETS_KEY, JSON.stringify(data));

        showToast(`Preset "${name}" deleted`);
        updateToolbarState();
        return true;
    } catch (e) {
        console.error('Failed to delete preset:', e);
        showToast('Failed to delete preset');
        return false;
    }
}

/**
 * Rename a preset
 * @param {string} oldName - Current name
 * @param {string} newName - New name
 * @returns {boolean} - Success
 */
export function renamePreset(oldName, newName) {
    if (!newName || typeof newName !== 'string' || newName.trim().length === 0) {
        showToast('Invalid new name');
        return false;
    }

    try {
        const data = JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}');
        const presets = data.presets || {};

        if (!presets[oldName]) {
            showToast(`Preset "${oldName}" not found`);
            return false;
        }

        const sanitizedNew = newName.trim();
        if (presets[sanitizedNew] && sanitizedNew !== oldName) {
            showToast(`Preset "${sanitizedNew}" already exists`);
            return false;
        }

        presets[sanitizedNew] = presets[oldName];
        delete presets[oldName];

        if (data.activePreset === oldName) {
            data.activePreset = sanitizedNew;
            activePreset = sanitizedNew;
        }

        data.presets = presets;
        localStorage.setItem(PRESETS_KEY, JSON.stringify(data));

        showToast(`Renamed to "${sanitizedNew}"`);
        return true;
    } catch (e) {
        console.error('Failed to rename preset:', e);
        showToast('Failed to rename preset');
        return false;
    }
}

/**
 * Export all presets as JSON
 * @returns {string} - JSON string of all presets
 */
export function exportPresets() {
    const presets = getPresets();
    const json = JSON.stringify({ presets, exported: new Date().toISOString() }, null, 2);

    // Trigger download
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'widget-layout-presets.json';
    a.click();
    URL.revokeObjectURL(url);

    showToast('Presets exported');
    return json;
}

/**
 * Import presets from JSON
 * @param {string} json - JSON string of presets
 * @returns {boolean} - Success
 */
export function importPresets(json) {
    try {
        const imported = JSON.parse(json);
        if (!imported.presets || typeof imported.presets !== 'object' || Array.isArray(imported.presets)) {
            showToast('Invalid preset file format');
            return false;
        }

        // Validate and sanitize each preset
        const validatedPresets = {};
        let validCount = 0;
        let skippedCount = 0;

        for (const [name, layout] of Object.entries(imported.presets)) {
            // Sanitize preset name
            const sanitizedName = sanitizePresetName(name);
            if (!sanitizedName) {
                skippedCount++;
                continue;
            }

            // Validate layout is an array of valid items
            if (!Array.isArray(layout)) {
                skippedCount++;
                continue;
            }

            // Validate each layout item
            const validLayout = layout.filter(isValidLayoutItem);
            if (validLayout.length === 0) {
                skippedCount++;
                continue;
            }

            validatedPresets[sanitizedName] = validLayout;
            validCount++;
        }

        if (validCount === 0) {
            showToast('No valid presets found in import file');
            return false;
        }

        const currentData = JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}');
        const currentPresets = currentData.presets || {};

        // Merge presets (imported overwrite existing with same name)
        const merged = { ...currentPresets, ...validatedPresets };

        const newData = {
            presets: merged,
            activePreset: currentData.activePreset
        };

        localStorage.setItem(PRESETS_KEY, JSON.stringify(newData));

        let message = `Imported ${validCount} preset(s)`;
        if (skippedCount > 0) {
            message += ` (${skippedCount} skipped - invalid)`;
        }
        showToast(message);
        return true;
    } catch (e) {
        console.error('Failed to import presets:', e);
        showToast('Failed to import presets');
        return false;
    }
}

/**
 * Get list of preset names
 * @returns {string[]} - Array of preset names
 */
export function getPresetNames() {
    return Object.keys(getPresets());
}

// =============================================================================
// TILE LOCK SYSTEM
// =============================================================================

/**
 * Initialize tile lock system - restore saved lock state
 */
function initTileLock() {
    try {
        const saved = localStorage.getItem(LOCK_STORAGE_KEY);
        if (saved === 'true') {
            tilesLocked = true;
            applyLockState(true);
        }
    } catch (e) {
        console.warn('Failed to restore tile lock state:', e);
    }
}

/**
 * Toggle tile lock state
 * @returns {boolean} - New lock state
 */
export function toggleTileLock() {
    if (tilesLocked) {
        unlockTiles();
    } else {
        lockTiles();
    }
    return tilesLocked;
}

/**
 * Lock tiles to prevent accidental movement
 */
export function lockTiles() {
    tilesLocked = true;
    applyLockState(true);
    saveLockState(true);
    showToast('Tiles locked');
    announceChange('Tiles locked - drag and drop disabled');
}

/**
 * Unlock tiles to allow movement
 */
export function unlockTiles() {
    tilesLocked = false;
    applyLockState(false);
    saveLockState(false);
    showToast('Tiles unlocked');
    announceChange('Tiles unlocked - drag and drop enabled');
}

/**
 * Check if tiles are currently locked
 * @returns {boolean}
 */
export function areTilesLocked() {
    return tilesLocked;
}

/**
 * Apply lock state to DOM (body class + button state + draggable attributes)
 * @param {boolean} locked
 */
function applyLockState(locked) {
    // Toggle body class for CSS
    document.body.classList.toggle('tiles-locked', locked);

    // Update button visual state
    const lockBtn = document.getElementById('lock-tiles-btn');
    if (lockBtn) {
        lockBtn.classList.toggle('locked', locked);
        const icon = lockBtn.querySelector('.lock-icon');
        const text = lockBtn.querySelector('.lock-text');
        if (icon) {
            // 🔓 unlocked (128275), 🔒 locked (128274)
            icon.innerHTML = locked ? '&#128274;' : '&#128275;';
        }
        if (text) {
            text.textContent = locked ? 'Unlock' : 'Lock';
        }
        lockBtn.title = locked
            ? 'Unlock tiles to allow movement'
            : 'Lock tiles to prevent accidental movement while navigating';
    }

    // Disable/enable draggable attribute on cards
    document.querySelectorAll(CARD_SELECTOR).forEach(card => {
        if (isInDraggableColumn(card)) {
            card.setAttribute('draggable', locked ? 'false' : 'true');
        }
    });
}

/**
 * Save lock state to localStorage
 * @param {boolean} locked
 */
function saveLockState(locked) {
    try {
        localStorage.setItem(LOCK_STORAGE_KEY, locked ? 'true' : 'false');
    } catch (e) {
        console.warn('Failed to save tile lock state:', e);
    }
}

// =============================================================================
// PERSISTENCE
// =============================================================================

/**
 * Save current widget layout to localStorage.
 */
export function saveLayout() {
    const layout = getCurrentLayout();
    saveLayoutToStorage(layout);

    // Update history present
    layoutHistory.present = layout;

    // Update toolbar UI state
    updateToolbarState();
}

function saveLayoutToStorage(layout) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
        console.log('Widget layout saved:', layout.length, 'widgets');
    } catch (e) {
        console.warn('Failed to save widget layout:', e);
    }
}

/**
 * Restore widget layout from localStorage.
 */
export function restoreLayout() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return false;

    try {
        const layout = JSON.parse(saved);
        applyLayout(layout);
        console.log('Widget layout restored:', layout.length, 'widgets');
        return true;
    } catch (e) {
        console.warn('Failed to restore widget layout:', e);
        return false;
    }
}

function applyLayout(layout) {
    // Group by column
    const byColumn = {};
    layout.forEach(item => {
        if (!byColumn[item.column]) byColumn[item.column] = [];
        byColumn[item.column].push(item);
    });

    // Sort each column by order
    Object.keys(byColumn).forEach(col => {
        byColumn[col].sort((a, b) => a.order - b.order);
    });

    // Move cards to positions
    COLUMN_SELECTORS.forEach((selector, columnIndex) => {
        const column = document.querySelector(selector);
        if (!column || !byColumn[columnIndex]) return;

        byColumn[columnIndex].forEach(item => {
            const card = document.getElementById(item.id);
            if (card) {
                column.appendChild(card);
            }
        });
    });
}

function captureDefaultLayout() {
    if (defaultLayout) return;  // Only capture once

    defaultLayout = [];
    COLUMN_SELECTORS.forEach((selector, columnIndex) => {
        const column = document.querySelector(selector);
        if (!column) return;

        column.querySelectorAll(CARD_SELECTOR).forEach((card, order) => {
            if (card.id) {
                defaultLayout.push({
                    id: card.id,
                    column: columnIndex,
                    order: order
                });
            }
        });
    });

    console.log('Default layout captured:', defaultLayout.length, 'widgets');
}

/**
 * Reset to default widget layout.
 * Clears localStorage and restores original order.
 */
export function resetToDefault() {
    const currentLayout = getCurrentLayout();

    localStorage.removeItem(STORAGE_KEY);

    if (defaultLayout && defaultLayout.length > 0) {
        // Only push to history if layout will actually change
        if (JSON.stringify(currentLayout) !== JSON.stringify(defaultLayout)) {
            layoutHistory.past.push(layoutHistory.present);
            layoutHistory.future = [];  // Clear redo stack

            // Limit history length
            if (layoutHistory.past.length > MAX_HISTORY_LENGTH) {
                layoutHistory.past.shift();
            }
        }

        applyLayout(defaultLayout);
        layoutHistory.present = defaultLayout;
        showToast('Layout reset to default');
        announceChange('Layout reset to default');
        updateToolbarState();
    } else {
        // Fallback: reload page
        showToast('Reloading to restore default layout...');
        setTimeout(() => location.reload(), 500);
    }
}

// =============================================================================
// HELPERS
// =============================================================================

function isInDraggableColumn(card) {
    return COLUMN_SELECTORS.some(selector =>
        card.closest(selector) !== null
    );
}

/**
 * Show a toast notification.
 * Uses window.showToast if available, otherwise logs to console.
 */
function showToast(message) {
    if (typeof window.showToast === 'function') {
        window.showToast(message);
    } else {
        console.log('[Drag-Drop]', message);
    }
}

// =============================================================================
// TOOLBAR UI (Cycle 3)
// Undo/Redo buttons, preset selector, save button
// =============================================================================

/**
 * Initialize toolbar UI event listeners
 */
function initToolbarUI() {
    const undoBtn = document.getElementById('layout-undo-btn');
    const redoBtn = document.getElementById('layout-redo-btn');
    const presetSelector = document.getElementById('preset-selector');
    const presetDropdown = document.getElementById('preset-dropdown');
    const savePresetBtn = document.getElementById('save-preset-btn');

    // Undo button
    if (undoBtn) {
        undoBtn.addEventListener('click', () => {
            undo();
        });
    }

    // Redo button
    if (redoBtn) {
        redoBtn.addEventListener('click', () => {
            redo();
        });
    }

    // Preset selector
    if (presetSelector) {
        presetSelector.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePresetDropdown();
        });
        presetSelector.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                togglePresetDropdown();
            } else if (e.key === 'Escape') {
                hidePresetDropdown();
            }
        });
    }

    // Save preset button
    if (savePresetBtn) {
        savePresetBtn.addEventListener('click', () => {
            showSavePresetModal();
        });
    }

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('#preset-selector') && !e.target.closest('#preset-dropdown')) {
            hidePresetDropdown();
        }
    });

    // Save preset modal keyboard support
    const presetNameInput = document.getElementById('preset-name-input');
    if (presetNameInput) {
        presetNameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                confirmSavePreset();
            } else if (e.key === 'Escape') {
                closeSavePresetModal();
            }
        });
    }

    // Initial state update
    updateToolbarState();
}

/**
 * Update toolbar button states and preset name display
 */
function updateToolbarState() {
    const undoBtn = document.getElementById('layout-undo-btn');
    const redoBtn = document.getElementById('layout-redo-btn');
    const presetNameEl = document.getElementById('preset-name');
    const unsavedIndicator = document.getElementById('unsaved-indicator');

    // Update undo/redo button states
    if (undoBtn) {
        undoBtn.disabled = !canUndo();
    }
    if (redoBtn) {
        redoBtn.disabled = !canRedo();
    }

    // Update preset name display
    if (presetNameEl) {
        presetNameEl.textContent = activePreset || 'Default';
    }

    // Update unsaved indicator
    if (unsavedIndicator) {
        const hasChanges = hasUnsavedChanges();
        unsavedIndicator.classList.toggle('visible', hasChanges);
    }
}

/**
 * Check if current layout differs from active preset
 * @returns {boolean}
 */
function hasUnsavedChanges() {
    if (!activePreset) {
        // No preset active - always considered "saved" (as default)
        return false;
    }

    const presets = getPresets();
    const presetLayout = presets[activePreset];
    if (!presetLayout) return false;

    const currentLayout = getCurrentLayout();
    return JSON.stringify(currentLayout) !== JSON.stringify(presetLayout);
}

/**
 * Toggle preset dropdown visibility
 */
function togglePresetDropdown() {
    const dropdown = document.getElementById('preset-dropdown');
    const selector = document.getElementById('preset-selector');

    if (!dropdown || !selector) return;

    const isVisible = dropdown.classList.contains('show');

    if (isVisible) {
        hidePresetDropdown();
    } else {
        showPresetDropdown();
    }
}

/**
 * Show preset dropdown and populate with presets
 */
function showPresetDropdown() {
    const dropdown = document.getElementById('preset-dropdown');
    const selector = document.getElementById('preset-selector');

    if (!dropdown || !selector) return;

    // Position dropdown relative to selector
    const selectorRect = selector.getBoundingClientRect();
    dropdown.style.left = selectorRect.left + 'px';
    dropdown.style.top = (selectorRect.bottom + 4) + 'px';
    dropdown.style.minWidth = Math.max(200, selectorRect.width) + 'px';

    // Populate dropdown
    createPresetDropdownContent();

    // Show with animation
    dropdown.style.display = 'block';
    requestAnimationFrame(() => {
        dropdown.classList.add('show');
    });

    selector.setAttribute('aria-expanded', 'true');
}

/**
 * Hide preset dropdown
 */
function hidePresetDropdown() {
    const dropdown = document.getElementById('preset-dropdown');
    const selector = document.getElementById('preset-selector');

    if (dropdown) {
        dropdown.classList.remove('show');
        setTimeout(() => {
            dropdown.style.display = 'none';
        }, 150);
    }

    if (selector) {
        selector.setAttribute('aria-expanded', 'false');
    }
}

/**
 * Create preset dropdown content dynamically
 */
function createPresetDropdownContent() {
    const dropdown = document.getElementById('preset-dropdown');
    if (!dropdown) return;

    const presets = getPresets();
    const presetNames = Object.keys(presets);

    if (presetNames.length === 0) {
        dropdown.innerHTML = '<div class="preset-dropdown-empty">No saved presets</div>';
        return;
    }

    let html = '';
    presetNames.forEach(name => {
        const isActive = name === activePreset;
        const escapedName = escapeHtml(name);
        html += `
            <div class="preset-dropdown-item${isActive ? ' active' : ''}" data-preset="${escapedName}" role="option" ${isActive ? 'aria-selected="true"' : ''}>
                <span class="preset-item-name">${escapedName}</span>
                <div class="preset-item-actions">
                    <button class="preset-item-action-btn delete" onclick="event.stopPropagation(); showDeletePresetConfirm('${escapedName}')" title="Delete preset">&times;</button>
                </div>
            </div>
        `;
    });

    dropdown.innerHTML = html;

    // Add click handlers for loading presets
    dropdown.querySelectorAll('.preset-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
            if (e.target.closest('.preset-item-action-btn')) return;
            const presetName = item.dataset.preset;
            loadPreset(presetName);
            hidePresetDropdown();
        });
    });
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// =============================================================================
// SAVE PRESET MODAL
// =============================================================================

/**
 * Show save preset modal
 */
function showSavePresetModal() {
    const modal = document.getElementById('save-preset-modal');
    const input = document.getElementById('preset-name-input');

    if (!modal || !input) return;

    // Clear and focus input
    input.value = '';
    modal.style.display = 'flex';

    // Focus after display
    requestAnimationFrame(() => {
        input.focus();
    });

    // Trap focus within modal
    trapFocusInModal(modal);
}

/**
 * Close save preset modal
 */
function closeSavePresetModal() {
    const modal = document.getElementById('save-preset-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * Confirm save preset from modal
 */
function confirmSavePreset() {
    const input = document.getElementById('preset-name-input');
    if (!input) return;

    const name = input.value.trim();
    if (!name) {
        showToast('Please enter a preset name');
        input.focus();
        return;
    }

    const success = savePreset(name);
    if (success) {
        closeSavePresetModal();
    }
}

// =============================================================================
// DELETE PRESET MODAL
// =============================================================================

/**
 * Show delete preset confirmation modal
 * @param {string} name - Preset name to delete
 */
function showDeletePresetConfirm(name) {
    const modal = document.getElementById('delete-preset-modal');
    const nameSpan = document.getElementById('delete-preset-name');

    if (!modal) return;

    deletePresetTarget = name;

    if (nameSpan) {
        nameSpan.textContent = name;
    }

    modal.style.display = 'flex';
    hidePresetDropdown();

    // Trap focus within modal
    trapFocusInModal(modal);
}

/**
 * Close delete preset modal
 */
function closeDeletePresetModal() {
    const modal = document.getElementById('delete-preset-modal');
    if (modal) {
        modal.style.display = 'none';
    }
    deletePresetTarget = null;
}

/**
 * Confirm delete preset
 */
function confirmDeletePreset() {
    if (deletePresetTarget) {
        deletePreset(deletePresetTarget);
    }
    closeDeletePresetModal();
}

/**
 * Trap focus within a modal for accessibility
 */
function trapFocusInModal(modal) {
    const focusableElements = modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );

    if (focusableElements.length === 0) return;

    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    function handleTabKey(e) {
        if (e.key !== 'Tab') return;

        if (e.shiftKey && document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
        } else if (!e.shiftKey && document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
        }
    }

    modal.addEventListener('keydown', handleTabKey);

    // Cleanup when modal is hidden
    const observer = new MutationObserver(() => {
        if (modal.style.display === 'none') {
            modal.removeEventListener('keydown', handleTabKey);
            observer.disconnect();
        }
    });
    observer.observe(modal, { attributes: true, attributeFilter: ['style'] });
}

// =============================================================================
// WINDOW EXPOSURE
// =============================================================================

function exposeToWindow() {
    // Undo/redo
    window.undoWidgetLayout = undo;
    window.redoWidgetLayout = redo;
    window.canUndoWidgetLayout = canUndo;
    window.canRedoWidgetLayout = canRedo;

    // Presets
    window.getLayoutPresets = getPresets;
    window.getActiveLayoutPreset = getActivePreset;
    window.saveLayoutPreset = savePreset;
    window.loadLayoutPreset = loadPreset;
    window.deleteLayoutPreset = deletePreset;
    window.renameLayoutPreset = renamePreset;
    window.exportLayoutPresets = exportPresets;
    window.importLayoutPresets = importPresets;
    window.getLayoutPresetNames = getPresetNames;

    // Toolbar UI modal functions (Cycle 3)
    window.showSavePresetModal = showSavePresetModal;
    window.closeSavePresetModal = closeSavePresetModal;
    window.confirmSavePreset = confirmSavePreset;
    window.showDeletePresetConfirm = showDeletePresetConfirm;
    window.closeDeletePresetModal = closeDeletePresetModal;
    window.confirmDeletePreset = confirmDeletePreset;

    // Tile lock functions
    window.toggleTileLock = toggleTileLock;
    window.lockTiles = lockTiles;
    window.unlockTiles = unlockTiles;
    window.areTilesLocked = areTilesLocked;
}

// =============================================================================
// PUBLIC API
// =============================================================================

// Note: Functions are already exported inline with 'export function'
// getDraggedCard is the only additional export needed
export { draggedCard as getDraggedCard };
