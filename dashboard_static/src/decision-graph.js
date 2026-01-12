/**
 * Dashboard Decision Graph Module (ES6)
 * Canvas-based decision graph visualization for tool invocations
 * Dependencies: core.js, api.js
 */

import { escapeHtml } from './core.js';
import { api } from './api.js';

// =============================================================================
// DECISION GRAPH STATE
// =============================================================================

let decisionGraphData = null;
let decisionGraphNodes = [];

// =============================================================================
// DECISION GRAPH FUNCTIONS
// =============================================================================

export async function refreshDecisionGraph() {
    try {
        const data = await api('/api/decision-graph/current');
        decisionGraphData = data;

        const invocationCount = document.getElementById('decision-invocation-count');
        const errorCount = document.getElementById('decision-error-count');

        if (invocationCount) invocationCount.textContent = data.stats?.total || 0;
        if (errorCount) errorCount.textContent = data.stats?.errors || 0;

        renderDecisionGraph(data);
    } catch (e) {
        console.error('Decision graph error:', e);
    }
}

function renderDecisionGraph(data) {
    const canvas = document.getElementById('decision-graph-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, w, h);

    const nodes = data.nodes || [];
    const edges = data.edges || [];

    if (nodes.length === 0) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('No tool invocations yet', w / 2, h / 2);
        return;
    }

    const padding = 20;
    const nodeRadius = 8;
    const nodesPerRow = Math.ceil(Math.sqrt(nodes.length));
    const spacingX = (w - padding * 2) / (nodesPerRow + 1);
    const spacingY = (h - padding * 2) / (Math.ceil(nodes.length / nodesPerRow) + 1);

    decisionGraphNodes = nodes.map((node, i) => {
        const row = Math.floor(i / nodesPerRow);
        const col = i % nodesPerRow;
        return {
            ...node,
            x: padding + (col + 1) * spacingX,
            y: padding + (row + 1) * spacingY
        };
    });

    // Draw edges
    ctx.strokeStyle = '#30363d';
    ctx.lineWidth = 1;
    edges.forEach(edge => {
        const source = decisionGraphNodes.find(n => n.id === edge.source);
        const target = decisionGraphNodes.find(n => n.id === edge.target);
        if (source && target) {
            ctx.beginPath();
            ctx.moveTo(source.x, source.y);
            ctx.lineTo(target.x, target.y);
            ctx.stroke();
        }
    });

    // Draw nodes
    decisionGraphNodes.forEach(node => {
        let color = '#3fb950';  // success
        if (node.has_error || node.status === 'error') {
            color = '#f85149';  // error
        } else if (node.tool_name === 'Read' || node.tool_name === 'Glob' || node.tool_name === 'Grep') {
            color = '#58a6ff';  // read
        } else if (node.tool_name === 'Write' || node.tool_name === 'Edit') {
            color = '#a371f7';  // write
        } else if (node.tool_name === 'Bash') {
            color = '#d29922';  // bash
        }

        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, nodeRadius, 0, Math.PI * 2);
        ctx.fill();
    });

    // Click handler
    canvas.onclick = function(e) {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const clicked = decisionGraphNodes.find(node => {
            const dx = node.x - x;
            const dy = node.y - y;
            return Math.sqrt(dx*dx + dy*dy) <= nodeRadius + 4;
        });

        if (clicked) {
            showDecisionNodeDetails(clicked);
        }
    };
}

export function showDecisionNodeDetails(node) {
    const details = document.getElementById('decision-node-details');
    if (!details) return;

    let errorHtml = '';
    if (node.has_error || node.status === 'error') {
        errorHtml = `<div class="node-error">Error: ${escapeHtml(node.error_message || 'Unknown error')}</div>`;
    }

    details.innerHTML = `
        <div class="node-title">${escapeHtml(node.tool_name)} (#${node.sequence})</div>
        <div class="node-meta">${escapeHtml(node.stage)} | ${node.duration_ms || 0}ms</div>
        ${errorHtml}
    `;
}
