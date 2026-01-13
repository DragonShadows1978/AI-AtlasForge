/**
 * Dashboard Exploration Graph Module (ES6)
 * Canvas-based exploration graph visualization
 * Dependencies: core.js, api.js
 */

import { api } from './api.js';

// =============================================================================
// GRAPH RENDERER CLASS
// =============================================================================

export class GraphRenderer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.nodes = [];
        this.edges = [];
        this.selectedNode = null;
        this.hoveredNode = null;
        this.scale = 1.0;
        this.offsetX = 0;
        this.offsetY = 0;
        this.tooltip = document.getElementById('graph-tooltip');

        this.colors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };

        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleHover(e));
        this.canvas.addEventListener('mouseleave', () => this.hideTooltip());
    }

    applyForceLayout(iterations = 50) {
        if (this.nodes.length === 0) return;

        const width = this.canvas.width;
        const height = this.canvas.height;
        const padding = 40;
        const minNodeDistance = 60;

        this.nodes.forEach((node, i) => {
            if (node.x === undefined || node.y === undefined) {
                const angle = (2 * Math.PI * i) / this.nodes.length;
                const radius = Math.min(width, height) * 0.35;
                node.x = width / 2 + radius * Math.cos(angle);
                node.y = height / 2 + radius * Math.sin(angle);
            }
        });

        const adjacency = new Map();
        this.edges.forEach(e => {
            if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
            if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
            adjacency.get(e.source).add(e.target);
            adjacency.get(e.target).add(e.source);
        });

        for (let iter = 0; iter < iterations; iter++) {
            const forces = new Map();
            this.nodes.forEach(n => forces.set(n.id, { fx: 0, fy: 0 }));

            for (let i = 0; i < this.nodes.length; i++) {
                for (let j = i + 1; j < this.nodes.length; j++) {
                    const n1 = this.nodes[i];
                    const n2 = this.nodes[j];
                    const dx = n2.x - n1.x;
                    const dy = n2.y - n1.y;
                    const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                    const repulsion = (minNodeDistance * minNodeDistance) / dist;
                    const fx = (dx / dist) * repulsion;
                    const fy = (dy / dist) * repulsion;
                    forces.get(n1.id).fx -= fx;
                    forces.get(n1.id).fy -= fy;
                    forces.get(n2.id).fx += fx;
                    forces.get(n2.id).fy += fy;
                }
            }

            this.edges.forEach(e => {
                const source = this.nodes.find(n => n.id === e.source);
                const target = this.nodes.find(n => n.id === e.target);
                if (!source || !target) return;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
                const attraction = dist * 0.01;
                const fx = (dx / dist) * attraction;
                const fy = (dy / dist) * attraction;
                forces.get(source.id).fx += fx;
                forces.get(source.id).fy += fy;
                forces.get(target.id).fx -= fx;
                forces.get(target.id).fy -= fy;
            });

            this.nodes.forEach(n => {
                const dx = width / 2 - n.x;
                const dy = height / 2 - n.y;
                forces.get(n.id).fx += dx * 0.002;
                forces.get(n.id).fy += dy * 0.002;
            });

            const damping = 0.8 - (iter / iterations) * 0.3;
            this.nodes.forEach(n => {
                const f = forces.get(n.id);
                n.x += f.fx * damping;
                n.y += f.fy * damping;
                n.x = Math.max(padding, Math.min(width - padding, n.x));
                n.y = Math.max(padding, Math.min(height - padding, n.y));
            });
        }
    }

    loadData(graphData) {
        if (!graphData) return;
        this.nodes = graphData.nodes || [];
        this.edges = graphData.edges || [];

        if (this.nodes.length > 0) {
            this.applyForceLayout(80);
            this.scale = 1.0;
            this.offsetX = 0;
            this.offsetY = 0;
        }

        this.render();

        const nodeCount = document.getElementById('graph-node-count');
        const edgeCount = document.getElementById('graph-edge-count');
        if (nodeCount) nodeCount.textContent = this.nodes.length;
        if (edgeCount) edgeCount.textContent = this.edges.length;
    }

    transformX(x) {
        return x * this.scale + this.offsetX;
    }

    transformY(y) {
        return y * this.scale + this.offsetY;
    }

    render() {
        if (!this.ctx) return;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.edges.forEach(edge => this.drawEdge(edge));
        this.nodes.forEach(node => this.drawNode(node));
    }

    drawNode(node) {
        const x = this.transformX(node.x);
        const y = this.transformY(node.y);
        const size = Math.max(6, (node.size || 15) * this.scale * 0.6);

        const isSelected = this.selectedNode && this.selectedNode.id === node.id;
        const isHovered = this.hoveredNode && this.hoveredNode.id === node.id;

        this.ctx.beginPath();
        this.ctx.arc(x, y, size, 0, Math.PI * 2);
        this.ctx.fillStyle = this.colors[node.type] || '#8b949e';
        this.ctx.fill();

        if (isSelected || isHovered) {
            this.ctx.strokeStyle = '#fff';
            this.ctx.lineWidth = 2;
            this.ctx.stroke();
        }

        if (this.canvas.width > 300 || isSelected) {
            this.ctx.fillStyle = '#c9d1d9';
            this.ctx.font = Math.max(8, 10 * this.scale) + 'px sans-serif';
            this.ctx.textAlign = 'center';
            const label = node.name.substring(0, 12);
            this.ctx.fillText(label, x, y + size + 10);
        }
    }

    drawEdge(edge) {
        const source = this.nodes.find(n => n.id === edge.source);
        const target = this.nodes.find(n => n.id === edge.target);
        if (!source || !target) return;

        const x1 = this.transformX(source.x);
        const y1 = this.transformY(source.y);
        const x2 = this.transformX(target.x);
        const y2 = this.transformY(target.y);

        this.ctx.beginPath();
        this.ctx.moveTo(x1, y1);
        this.ctx.lineTo(x2, y2);
        this.ctx.strokeStyle = 'rgba(139, 148, 158, 0.4)';
        this.ctx.lineWidth = Math.max(1, (edge.strength || 1) * 2 * this.scale);
        this.ctx.stroke();
    }

    handleClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const clickedNode = this.findNodeAt(x, y);
        if (clickedNode) {
            this.selectedNode = clickedNode;
            this.showNodeDetails(clickedNode);
            this.render();
        }
    }

    handleHover(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const node = this.findNodeAt(x, y);
        if (node !== this.hoveredNode) {
            this.hoveredNode = node;
            this.canvas.style.cursor = node ? 'pointer' : 'default';
            this.render();

            if (node) {
                this.showTooltip(node, e.clientX, e.clientY);
            } else {
                this.hideTooltip();
            }
        }
    }

    showTooltip(node, mouseX, mouseY) {
        if (!this.tooltip) return;

        const typeColors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };

        this.tooltip.querySelector('.tt-name').textContent = node.name;
        this.tooltip.querySelector('.tt-name').style.color = typeColors[node.type] || '#8b949e';
        this.tooltip.querySelector('.tt-type').textContent = `Type: ${node.type} | Explored: ${node.exploration_count || 0}x`;
        this.tooltip.querySelector('.tt-path').textContent = node.path ? `Path: ${node.path}` : '';
        this.tooltip.querySelector('.tt-summary').textContent = node.summary || '';

        this.tooltip.style.left = (mouseX + 15) + 'px';
        this.tooltip.style.top = (mouseY + 15) + 'px';
        this.tooltip.classList.add('visible');
    }

    hideTooltip() {
        if (this.tooltip) {
            this.tooltip.classList.remove('visible');
        }
    }

    findNodeAt(x, y) {
        for (const node of this.nodes) {
            const nx = this.transformX(node.x);
            const ny = this.transformY(node.y);
            const size = Math.max(6, (node.size || 15) * this.scale * 0.6);

            const dx = x - nx;
            const dy = y - ny;
            if (Math.sqrt(dx*dx + dy*dy) < size + 5) {
                return node;
            }
        }
        return null;
    }

    showNodeDetails(node) {
        const details = document.getElementById('graph-node-details');
        if (!details) return;

        const typeColors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };
        const color = typeColors[node.type] || '#8b949e';

        details.innerHTML = `
            <div style="font-weight: 500; color: ${color}; margin-bottom: 5px;">${node.name}</div>
            <div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Type:</span>
                <span class="stat-value">${node.type}</span>
            </div>
            ${node.path ? `<div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Path:</span>
                <span class="stat-value" style="word-break: break-all;">${node.path}</span>
            </div>` : ''}
            <div class="stat-row" style="font-size: 0.8em;">
                <span class="stat-label">Explored:</span>
                <span class="stat-value">${node.exploration_count}x</span>
            </div>
            <div style="font-size: 0.75em; color: var(--text-dim); margin-top: 5px;">${node.summary || ''}</div>
        `;
    }
}

// =============================================================================
// GLOBAL INSTANCE AND REFRESH
// =============================================================================

let graphRenderer = null;

export async function refreshGraphVisualization() {
    try {
        const data = await api('/api/atlasforge/exploration-graph?width=800&height=600');
        if (data.error && data.nodes && data.nodes.length === 0) {
            console.log('No graph data:', data.error);
            return;
        }

        if (!graphRenderer) {
            graphRenderer = new GraphRenderer('exploration-graph-canvas');
        }

        if (graphRenderer) {
            graphRenderer.loadData(data);
        }
    } catch (e) {
        console.log('Error loading graph:', e);
    }
}

// =============================================================================
// INSIGHT SEARCH
// =============================================================================

export async function searchInsights() {
    const input = document.getElementById('insight-search-input');
    const results = document.getElementById('insight-search-results');
    const query = input.value.trim();

    if (!query) {
        results.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">Enter a query to search insights</div>';
        return;
    }

    try {
        const data = await api('/api/atlasforge/search-insights?q=' + encodeURIComponent(query));
        if (data.error) {
            results.innerHTML = `<div style="color: var(--red); font-size: 0.85em;">${data.error}</div>`;
            return;
        }

        const insights = data.insights || [];
        if (insights.length === 0) {
            results.innerHTML = '<div style="color: var(--text-dim); font-size: 0.85em;">No insights found</div>';
            return;
        }

        const html = insights.map(i => `
            <div class="atlasforge-exploration-item" title="${i.description || ''}">
                <div>
                    <span style="font-weight: 500;">${i.title}</span>
                    <div style="font-size: 0.75em; color: var(--text-dim);">
                        ${i.type} | ${(i.similarity * 100).toFixed(0)}% match
                    </div>
                </div>
                <span class="atlasforge-exploration-type">${(i.confidence * 100).toFixed(0)}%</span>
            </div>
        `).join('');

        results.innerHTML = html;
    } catch (e) {
        results.innerHTML = `<div style="color: var(--red); font-size: 0.85em;">Error: ${e.message}</div>`;
    }
}

// Setup insight search on enter
export function setupInsightSearch() {
    const input = document.getElementById('insight-search-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') searchInsights();
        });
    }
}
