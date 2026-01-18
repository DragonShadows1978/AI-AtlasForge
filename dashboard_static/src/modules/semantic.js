/**
 * Semantic Search Dashboard Module
 *
 * Provides visualization and interaction for semantic search features:
 * - EmbeddingViz: 2D/3D scatter plots using Plotly
 * - ClusterExplorer: Interactive cluster browser
 * - DriftTimeline: Historical drift chart
 * - QualityCard: Embedding health dashboard
 * - FeedbackWidget: Search result feedback UI
 */

// =============================================================================
// API CLIENT
// =============================================================================

const SemanticAPI = {
    baseUrl: '/api/semantic',

    async get(endpoint) {
        const response = await fetch(`${this.baseUrl}${endpoint}`);
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        return response.json();
    },

    async post(endpoint, data) {
        const response = await fetch(`${this.baseUrl}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        return response.json();
    },

    // Status
    getStatus: () => SemanticAPI.get('/status'),

    // Quality
    getQuality: () => SemanticAPI.get('/embeddings/quality'),
    getAnomalies: () => SemanticAPI.get('/embeddings/anomalies'),
    triggerRevalidation: () => SemanticAPI.post('/embeddings/revalidate', {}),
    getRevalidationStatus: () => SemanticAPI.get('/embeddings/revalidate/status'),

    // Clusters
    getClusters: () => SemanticAPI.get('/clusters'),
    getClusterDetail: (id) => SemanticAPI.get(`/clusters/${id}`),

    // Drift
    getDriftStatus: () => SemanticAPI.get('/drift/status'),
    getDriftHistory: (days = 30) => SemanticAPI.get(`/drift/history?days=${days}`),
    captureSnapshot: () => SemanticAPI.post('/drift/snapshot', {}),

    // Feedback
    submitFeedback: (query, resultId, feedback) =>
        SemanticAPI.post('/feedback', { query, result_id: resultId, feedback }),
    getFeedbackStats: () => SemanticAPI.get('/feedback/stats'),

    // Visualization
    getVisualization: (dimensions = 3, method = 'umap', includeClusters = true) =>
        SemanticAPI.get(`/visualization/embeddings?dimensions=${dimensions}&method=${method}&include_clusters=${includeClusters}`),
    invalidateVizCache: () => SemanticAPI.post('/visualization/invalidate-cache', {}),

    // Search
    search: (query, topK = 10, minSimilarity = 0.3, useFeedback = true) =>
        SemanticAPI.post('/search', { query, top_k: topK, min_similarity: minSimilarity, use_feedback: useFeedback }),

    // Similar code
    findSimilar: (code, topK = 10, minSimilarity = 0.5) =>
        SemanticAPI.post('/similar-code', { code, top_k: topK, min_similarity: minSimilarity }),

    // Cache
    getCacheStats: () => SemanticAPI.get('/cache/stats'),
    clearCache: () => SemanticAPI.post('/cache/clear', {})
};


// =============================================================================
// EMBEDDING VISUALIZATION
// =============================================================================

class EmbeddingViz {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.data = null;
        this.dimensions = 3;
        this.method = 'umap';
        this.selectedCluster = null;
        this.plotlyLoaded = false;
    }

    async ensurePlotly() {
        if (this.plotlyLoaded) return true;
        if (typeof Plotly !== 'undefined') {
            this.plotlyLoaded = true;
            return true;
        }

        // Try lazy loading
        if (window.loadLib) {
            try {
                await window.loadLib('plotly');
                this.plotlyLoaded = true;
                return true;
            } catch (e) {
                console.error('Failed to load Plotly:', e);
            }
        }

        // Fallback: show message
        if (this.container) {
            this.container.innerHTML = `
                <div style="padding: 20px; text-align: center; color: var(--text-dim);">
                    <p>Plotly.js not available for visualization.</p>
                    <p style="font-size: 0.8em;">Add plotly.min.js to /static/vendor/</p>
                </div>
            `;
        }
        return false;
    }

    async loadData(params = {}) {
        const dimensions = params.dimensions || this.dimensions;
        const method = params.method || this.method;

        try {
            this.data = await SemanticAPI.getVisualization(dimensions, method, true);
            return this.data;
        } catch (e) {
            console.error('Failed to load visualization data:', e);
            return null;
        }
    }

    async render() {
        if (!await this.ensurePlotly()) return;
        if (!this.data || !this.data.points || this.data.points.length === 0) {
            this.showEmpty();
            return;
        }

        if (this.dimensions === 3) {
            this.render3D(this.data);
        } else {
            this.render2D(this.data);
        }
    }

    render2D(data) {
        const points = data.points;
        const clusters = [...new Set(points.map(p => p.cluster_id))];
        const colorScale = this.generateColorScale(clusters.length);

        const traces = clusters.map((clusterId, i) => {
            const clusterPoints = points.filter(p => p.cluster_id === clusterId);
            return {
                x: clusterPoints.map(p => p.x),
                y: clusterPoints.map(p => p.y),
                mode: 'markers',
                type: 'scatter',
                name: clusterId === -1 ? 'Noise' : `Cluster ${clusterId}`,
                text: clusterPoints.map(p => `${p.name}<br>${p.entry_id}`),
                hoverinfo: 'text',
                marker: {
                    size: 8,
                    color: clusterId === -1 ? '#666' : colorScale[i % colorScale.length],
                    opacity: this.selectedCluster === null || this.selectedCluster === clusterId ? 0.8 : 0.2
                }
            };
        });

        const layout = {
            title: '',
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: { color: '#c9d1d9' },
            xaxis: { showgrid: false, zeroline: false, showticklabels: false },
            yaxis: { showgrid: false, zeroline: false, showticklabels: false },
            showlegend: true,
            legend: { x: 1, y: 1, bgcolor: 'rgba(22,27,34,0.8)' },
            margin: { l: 20, r: 20, t: 20, b: 20 },
            hovermode: 'closest'
        };

        Plotly.newPlot(this.containerId, traces, layout, { responsive: true });

        // Click handler
        this.container.on('plotly_click', (data) => {
            if (data.points.length > 0) {
                const point = data.points[0];
                this.handlePointClick({
                    entry_id: points[point.pointIndex]?.entry_id,
                    name: points[point.pointIndex]?.name,
                    cluster_id: points[point.pointIndex]?.cluster_id
                });
            }
        });
    }

    render3D(data) {
        const points = data.points;
        const clusters = [...new Set(points.map(p => p.cluster_id))];
        const colorScale = this.generateColorScale(clusters.length);

        const traces = clusters.map((clusterId, i) => {
            const clusterPoints = points.filter(p => p.cluster_id === clusterId);
            return {
                x: clusterPoints.map(p => p.x),
                y: clusterPoints.map(p => p.y),
                z: clusterPoints.map(p => p.z),
                mode: 'markers',
                type: 'scatter3d',
                name: clusterId === -1 ? 'Noise' : `Cluster ${clusterId}`,
                text: clusterPoints.map(p => `${p.name}<br>${p.entry_id}`),
                hoverinfo: 'text',
                marker: {
                    size: 4,
                    color: clusterId === -1 ? '#666' : colorScale[i % colorScale.length],
                    opacity: this.selectedCluster === null || this.selectedCluster === clusterId ? 0.8 : 0.2
                }
            };
        });

        const layout = {
            title: '',
            paper_bgcolor: 'transparent',
            scene: {
                xaxis: { showgrid: false, zeroline: false, showticklabels: false, title: '' },
                yaxis: { showgrid: false, zeroline: false, showticklabels: false, title: '' },
                zaxis: { showgrid: false, zeroline: false, showticklabels: false, title: '' },
                bgcolor: 'transparent'
            },
            font: { color: '#c9d1d9' },
            showlegend: true,
            legend: { x: 1, y: 1, bgcolor: 'rgba(22,27,34,0.8)' },
            margin: { l: 0, r: 0, t: 0, b: 0 }
        };

        Plotly.newPlot(this.containerId, traces, layout, { responsive: true });
    }

    generateColorScale(count) {
        const colors = [
            '#58a6ff', '#3fb950', '#f85149', '#d29922', '#a371f7',
            '#79c0ff', '#56d364', '#ff7b72', '#e3b341', '#bc8cff',
            '#39d353', '#f778ba', '#ffa657', '#7ee787', '#ff9f1c'
        ];
        return colors.slice(0, Math.max(count, 1));
    }

    setClusterFilter(clusterId) {
        this.selectedCluster = clusterId;
        this.render();
    }

    clearClusterFilter() {
        this.selectedCluster = null;
        this.render();
    }

    handlePointClick(point) {
        console.log('Point clicked:', point);
        // Emit custom event for external handling
        const event = new CustomEvent('semantic-point-click', { detail: point });
        document.dispatchEvent(event);
    }

    showEmpty() {
        if (this.container) {
            this.container.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-dim);">
                    <div style="text-align: center;">
                        <p>No embedding data available</p>
                        <p style="font-size: 0.8em;">Embeddings are generated during exploration</p>
                    </div>
                </div>
            `;
        }
    }

    async setDimensions(dims) {
        this.dimensions = dims;
        await this.loadData();
        await this.render();
    }

    async setMethod(method) {
        this.method = method;
        await this.loadData();
        await this.render();
    }
}


// =============================================================================
// CLUSTER EXPLORER
// =============================================================================

class ClusterExplorer {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.clusters = [];
        this.selectedCluster = null;
        this.onClusterSelect = null;
    }

    async loadClusters() {
        try {
            const data = await SemanticAPI.getClusters();
            this.clusters = data.clusters || [];
            return this.clusters;
        } catch (e) {
            console.error('Failed to load clusters:', e);
            return [];
        }
    }

    render() {
        if (!this.container) return;

        if (!this.clusters || this.clusters.length === 0) {
            this.container.innerHTML = `
                <div style="padding: 10px; color: var(--text-dim); font-size: 0.85em;">
                    No clusters found. Need more embeddings for clustering.
                </div>
            `;
            return;
        }

        const html = this.clusters.map(cluster => `
            <div class="cluster-item ${this.selectedCluster === cluster.id ? 'selected' : ''}"
                 data-cluster-id="${cluster.id}"
                 onclick="window.semanticClusterExplorer?.selectCluster(${cluster.id})">
                <div class="cluster-header">
                    <span class="cluster-name">${cluster.theme || `Cluster ${cluster.id}`}</span>
                    <span class="cluster-size badge">${cluster.size}</span>
                </div>
                <div class="cluster-members">
                    ${(cluster.sample_members || []).slice(0, 3).map(m => `<span class="member-tag">${m}</span>`).join('')}
                    ${cluster.size > 3 ? `<span class="member-more">+${cluster.size - 3} more</span>` : ''}
                </div>
            </div>
        `).join('');

        this.container.innerHTML = html;
    }

    selectCluster(clusterId) {
        this.selectedCluster = clusterId;
        this.render();

        if (this.onClusterSelect) {
            this.onClusterSelect(clusterId);
        }

        // Emit custom event
        const event = new CustomEvent('semantic-cluster-select', { detail: { clusterId } });
        document.dispatchEvent(event);
    }

    clearSelection() {
        this.selectedCluster = null;
        this.render();

        if (this.onClusterSelect) {
            this.onClusterSelect(null);
        }
    }
}


// =============================================================================
// DRIFT TIMELINE
// =============================================================================

class DriftTimeline {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.history = [];
        this.chartLoaded = false;
    }

    async ensureChart() {
        if (this.chartLoaded) return true;
        if (typeof Chart !== 'undefined') {
            this.chartLoaded = true;
            return true;
        }

        if (window.loadLib) {
            try {
                await window.loadLib('chart');
                this.chartLoaded = true;
                return true;
            } catch (e) {
                console.error('Failed to load Chart.js:', e);
            }
        }

        return false;
    }

    async loadHistory(days = 30) {
        try {
            const data = await SemanticAPI.getDriftHistory(days);
            this.history = data.history || [];
            return this.history;
        } catch (e) {
            console.error('Failed to load drift history:', e);
            return [];
        }
    }

    async render() {
        if (!await this.ensureChart()) {
            this.renderFallback();
            return;
        }

        if (!this.container) return;

        if (!this.history || this.history.length === 0) {
            this.container.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-dim); font-size: 0.85em;">
                    No drift history. Capture a snapshot to start tracking.
                </div>
            `;
            return;
        }

        // Destroy existing chart
        const existingChart = Chart.getChart(this.containerId);
        if (existingChart) {
            existingChart.destroy();
        }

        // Create canvas if needed
        let canvas = this.container.querySelector('canvas');
        if (!canvas) {
            this.container.innerHTML = '<canvas></canvas>';
            canvas = this.container.querySelector('canvas');
        }

        const labels = this.history.map(h => new Date(h.timestamp).toLocaleDateString());
        const driftData = this.history.map(h => h.overall_drift || 0);
        const alertThreshold = 0.5;

        new Chart(canvas, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Drift Score',
                    data: driftData,
                    borderColor: '#58a6ff',
                    backgroundColor: 'rgba(88, 166, 255, 0.1)',
                    fill: true,
                    tension: 0.3
                }, {
                    label: 'Alert Threshold',
                    data: labels.map(() => alertThreshold),
                    borderColor: '#f85149',
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        labels: { color: '#c9d1d9' }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#8b949e' },
                        grid: { color: '#30363d' }
                    },
                    y: {
                        min: 0,
                        max: 1,
                        ticks: { color: '#8b949e' },
                        grid: { color: '#30363d' }
                    }
                }
            }
        });
    }

    renderFallback() {
        if (!this.container) return;

        if (!this.history || this.history.length === 0) {
            this.container.innerHTML = '<p style="color: var(--text-dim);">No drift history available</p>';
            return;
        }

        // Simple text-based fallback
        const latest = this.history[this.history.length - 1];
        const html = `
            <div style="padding: 10px;">
                <div class="stat-row">
                    <span class="stat-label">Latest Drift</span>
                    <span class="stat-value ${latest.overall_drift > 0.5 ? 'text-red' : ''}">${(latest.overall_drift || 0).toFixed(3)}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Snapshots</span>
                    <span class="stat-value">${this.history.length}</span>
                </div>
            </div>
        `;
        this.container.innerHTML = html;
    }
}


// =============================================================================
// QUALITY CARD
// =============================================================================

class QualityCard {
    constructor(containerId) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.stats = null;
    }

    async refresh() {
        try {
            this.stats = await SemanticAPI.getQuality();
            this.render();
            return this.stats;
        } catch (e) {
            console.error('Failed to load quality stats:', e);
            return null;
        }
    }

    render() {
        if (!this.container) return;

        if (!this.stats || this.stats.error) {
            this.container.innerHTML = `
                <div style="color: var(--text-dim); font-size: 0.85em;">
                    ${this.stats?.error || 'No embedding data available'}
                </div>
            `;
            return;
        }

        const stats = this.stats.stats || {};
        const validityRate = stats.validity_rate || 0;
        const anomalyCount = (this.stats.anomalous_entries || []).length;
        const totalCount = this.stats.total_count || 0;

        const statusClass = validityRate >= 0.95 ? 'good' : validityRate >= 0.8 ? 'warning' : 'bad';

        this.container.innerHTML = `
            <div class="quality-stats">
                <div class="stat-row">
                    <span class="stat-label">Validity Rate</span>
                    <span class="stat-value highlight quality-${statusClass}">${(validityRate * 100).toFixed(1)}%</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Anomalies</span>
                    <span class="stat-value ${anomalyCount > 0 ? 'text-yellow' : ''}">${anomalyCount}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Total Embeddings</span>
                    <span class="stat-value">${totalCount}</span>
                </div>
                ${this.stats.cached ? '<div class="cache-indicator">Cached</div>' : ''}
            </div>
        `;
    }

    async showAnomalyList() {
        try {
            const data = await SemanticAPI.getAnomalies();
            const anomalies = data.anomalies || [];

            if (anomalies.length === 0) {
                showToast('No anomalous entries found', 'info');
                return;
            }

            // Create modal content
            const content = `
                <h3>Anomalous Embeddings (${anomalies.length})</h3>
                <div class="anomaly-list" style="max-height: 400px; overflow-y: auto;">
                    ${anomalies.map(a => `
                        <div class="anomaly-item" style="padding: 8px; border-bottom: 1px solid var(--border);">
                            <div style="font-weight: 500;">${a.name}</div>
                            <div style="font-size: 0.8em; color: var(--text-dim);">${a.path || a.entry_id}</div>
                            <div style="font-size: 0.75em; color: var(--accent);">${a.node_type}</div>
                        </div>
                    `).join('')}
                </div>
                <div class="btn-group" style="margin-top: 15px;">
                    <button class="btn primary" onclick="window.semanticQualityCard?.triggerRevalidation(); closeModal();">
                        Re-embed All
                    </button>
                    <button class="btn" onclick="closeModal()">Close</button>
                </div>
            `;

            showModal(content);
        } catch (e) {
            showToast('Failed to load anomalies: ' + e.message, 'error');
        }
    }

    async triggerRevalidation() {
        try {
            const result = await SemanticAPI.triggerRevalidation();
            if (result.status === 'started') {
                showToast(`Re-embedding ${result.total} entries...`, 'info');
            } else if (result.status === 'already_running') {
                showToast('Re-embedding already in progress', 'warning');
            } else {
                showToast(result.message || 'Re-embedding triggered', 'success');
            }
        } catch (e) {
            showToast('Failed to trigger revalidation: ' + e.message, 'error');
        }
    }
}


// =============================================================================
// FEEDBACK WIDGET
// =============================================================================

class FeedbackWidget {
    constructor() {
        this.stats = null;
    }

    async loadStats() {
        try {
            this.stats = await SemanticAPI.getFeedbackStats();
            return this.stats;
        } catch (e) {
            console.error('Failed to load feedback stats:', e);
            return null;
        }
    }

    renderStats(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!this.stats) {
            container.innerHTML = '<div style="color: var(--text-dim);">No feedback data</div>';
            return;
        }

        const total = this.stats.total_feedback || 0;
        const helpful = this.stats.helpful_count || 0;
        const positiveRate = total > 0 ? (helpful / total * 100).toFixed(1) : 0;

        container.innerHTML = `
            <div class="stat-row">
                <span class="stat-label">Total Feedback</span>
                <span class="stat-value">${total}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Positive Rate</span>
                <span class="stat-value highlight">${positiveRate}%</span>
            </div>
        `;
    }

    attachToResults(resultElements, query) {
        resultElements.forEach(el => {
            const resultId = el.dataset.resultId;
            if (!resultId) return;

            // Check if already has feedback buttons
            if (el.querySelector('.feedback-buttons')) return;

            const feedbackHtml = `
                <div class="feedback-buttons" style="margin-top: 8px;">
                    <button class="btn-icon feedback-up" onclick="window.semanticFeedback?.submitFeedback('${query}', '${resultId}', 'helpful')" title="Helpful">
                        <span>👍</span>
                    </button>
                    <button class="btn-icon feedback-down" onclick="window.semanticFeedback?.submitFeedback('${query}', '${resultId}', 'not_helpful')" title="Not helpful">
                        <span>👎</span>
                    </button>
                    <button class="btn-icon find-similar" onclick="window.semanticFeedback?.findSimilar('${resultId}')" title="Find similar">
                        <span>🔍</span>
                    </button>
                </div>
            `;

            el.insertAdjacentHTML('beforeend', feedbackHtml);
        });
    }

    async submitFeedback(query, resultId, feedbackType) {
        try {
            await SemanticAPI.submitFeedback(query, resultId, feedbackType);
            showToast(`Feedback recorded: ${feedbackType}`, 'success');

            // Visual confirmation
            const btn = document.querySelector(`[data-result-id="${resultId}"] .feedback-${feedbackType === 'helpful' ? 'up' : 'down'}`);
            if (btn) {
                btn.classList.add('active');
            }
        } catch (e) {
            showToast('Failed to submit feedback: ' + e.message, 'error');
        }
    }

    async findSimilar(resultId) {
        // This would need the code content - emit event for handling
        const event = new CustomEvent('semantic-find-similar', { detail: { resultId } });
        document.dispatchEvent(event);
    }
}


// =============================================================================
// INITIALIZATION & EXPORTS
// =============================================================================

// Global instances for easy access
window.semanticEmbeddingViz = null;
window.semanticClusterExplorer = null;
window.semanticDriftTimeline = null;
window.semanticQualityCard = null;
window.semanticFeedback = null;

/**
 * Initialize all semantic widgets
 */
async function initSemanticWidgets() {
    console.log('[Semantic] Initializing widgets...');

    // Quality card
    const qualityContainer = document.getElementById('semantic-quality-stats');
    if (qualityContainer) {
        window.semanticQualityCard = new QualityCard('semantic-quality-stats');
        await window.semanticQualityCard.refresh();
    }

    // Cluster explorer
    const clusterContainer = document.getElementById('cluster-list');
    if (clusterContainer) {
        window.semanticClusterExplorer = new ClusterExplorer('cluster-list');
        await window.semanticClusterExplorer.loadClusters();
        window.semanticClusterExplorer.render();

        // Link to visualization
        window.semanticClusterExplorer.onClusterSelect = (clusterId) => {
            if (window.semanticEmbeddingViz) {
                window.semanticEmbeddingViz.setClusterFilter(clusterId);
            }
        };
    }

    // Drift timeline
    const driftContainer = document.getElementById('drift-timeline-container');
    if (driftContainer) {
        window.semanticDriftTimeline = new DriftTimeline('drift-timeline-container');
        await window.semanticDriftTimeline.loadHistory();
        await window.semanticDriftTimeline.render();
    }

    // Feedback widget
    window.semanticFeedback = new FeedbackWidget();
    const feedbackContainer = document.getElementById('feedback-stats');
    if (feedbackContainer) {
        await window.semanticFeedback.loadStats();
        window.semanticFeedback.renderStats('feedback-stats');
    }

    // Embedding visualization (load last as it's heaviest)
    const vizContainer = document.getElementById('embedding-viz-container');
    if (vizContainer) {
        window.semanticEmbeddingViz = new EmbeddingViz('embedding-viz-container');
        await window.semanticEmbeddingViz.loadData();
        await window.semanticEmbeddingViz.render();
    }

    // Update cluster count badge
    const clusterCount = document.getElementById('cluster-count');
    if (clusterCount && window.semanticClusterExplorer) {
        clusterCount.textContent = window.semanticClusterExplorer.clusters.length;
    }

    console.log('[Semantic] Widgets initialized');
}

/**
 * Refresh all semantic widgets
 */
async function refreshSemanticWidgets() {
    if (window.semanticQualityCard) {
        await window.semanticQualityCard.refresh();
    }
    if (window.semanticClusterExplorer) {
        await window.semanticClusterExplorer.loadClusters();
        window.semanticClusterExplorer.render();
    }
    if (window.semanticDriftTimeline) {
        await window.semanticDriftTimeline.loadHistory();
        await window.semanticDriftTimeline.render();
    }
    if (window.semanticFeedback) {
        await window.semanticFeedback.loadStats();
        window.semanticFeedback.renderStats('feedback-stats');
    }
}

/**
 * Refresh mini widgets (for AtlasForge tab)
 */
async function refreshSemanticMiniWidgets() {
    try {
        const status = await SemanticAPI.getStatus();

        // Element references for the mini widget on AtlasForge tab
        const embeddingsEl = document.getElementById('semantic-total-embeddings');
        const clusterCountEl = document.getElementById('semantic-cluster-count');
        const qualityScoreEl = document.getElementById('semantic-quality-score');
        const driftStatusEl = document.getElementById('semantic-drift-status');
        const staleCountEl = document.getElementById('semantic-stale-count');

        if (!status.available) {
            if (qualityScoreEl) qualityScoreEl.textContent = 'N/A';
            if (driftStatusEl) driftStatusEl.textContent = 'Offline';
            return;
        }

        // Load quality data
        try {
            const quality = await SemanticAPI.getQuality();
            const stats = quality.stats || {};
            const rate = stats.validity_rate || 0;
            const totalCount = quality.total_count || 0;
            const anomalyCount = (quality.anomalous_entries || []).length;

            if (embeddingsEl) embeddingsEl.textContent = totalCount;
            if (qualityScoreEl) {
                const pct = (rate * 100).toFixed(0);
                qualityScoreEl.textContent = `${pct}%`;
                qualityScoreEl.className = `atlasforge-stat-value quality-${rate >= 0.95 ? 'good' : rate >= 0.8 ? 'warning' : 'bad'}`;
            }
            if (staleCountEl) staleCountEl.textContent = anomalyCount;
        } catch (e) {
            console.debug('[Semantic] Quality fetch error:', e);
        }

        // Load cluster count
        try {
            const clusters = await SemanticAPI.getClusters();
            if (clusterCountEl) clusterCountEl.textContent = (clusters.clusters || []).length;
        } catch (e) {
            console.debug('[Semantic] Clusters fetch error:', e);
        }

        // Load drift status
        try {
            const drift = await SemanticAPI.getDriftStatus();
            if (driftStatusEl) {
                if (drift.status === 'analyzed') {
                    const score = drift.overall_drift || 0;
                    driftStatusEl.textContent = score > 0.5 ? 'High' : score > 0.3 ? 'Medium' : 'Low';
                    driftStatusEl.className = `value ${score > 0.5 ? 'text-red' : score > 0.3 ? 'text-yellow' : ''}`;
                } else {
                    driftStatusEl.textContent = drift.status || 'N/A';
                }
            }
        } catch (e) {
            console.debug('[Semantic] Drift fetch error:', e);
        }

    } catch (e) {
        console.debug('[Semantic] Mini widget refresh error:', e);
    }
}

// UI action handlers
window.toggleVizDimension = async function() {
    const select = document.getElementById('viz-dimension-select');
    if (select && window.semanticEmbeddingViz) {
        await window.semanticEmbeddingViz.setDimensions(parseInt(select.value.replace('d', '')));
    }
};

window.refreshVisualization = async function() {
    const methodSelect = document.getElementById('viz-method-select');
    if (methodSelect && window.semanticEmbeddingViz) {
        await window.semanticEmbeddingViz.setMethod(methodSelect.value);
    }
};

window.showAnomalyList = function() {
    if (window.semanticQualityCard) {
        window.semanticQualityCard.showAnomalyList();
    }
};

window.triggerRevalidation = function() {
    if (window.semanticQualityCard) {
        window.semanticQualityCard.triggerRevalidation();
    }
};

window.captureSnapshot = async function() {
    try {
        const result = await SemanticAPI.captureSnapshot();
        showToast(`Snapshot captured: ${result.embedding_count} embeddings`, 'success');
        if (window.semanticDriftTimeline) {
            await window.semanticDriftTimeline.loadHistory();
            await window.semanticDriftTimeline.render();
        }
    } catch (e) {
        showToast('Failed to capture snapshot: ' + e.message, 'error');
    }
};

// Helper functions (use global ones if available)
function showToast(message, type = 'info') {
    if (window.showToast) {
        window.showToast(message, type);
    } else {
        console.log(`[${type}] ${message}`);
    }
}

function showModal(content) {
    if (window.showModal) {
        window.showModal(content);
    } else {
        alert(content.replace(/<[^>]*>/g, ''));
    }
}

function closeModal() {
    if (window.closeModal) {
        window.closeModal();
    }
}


// =============================================================================
// WEBSOCKET HANDLERS FOR REAL-TIME ALERTS
// =============================================================================

/**
 * Show a toast notification for semantic alerts
 */
function showSemanticToast(message, type = 'info', duration = 5000) {
    let container = document.getElementById('semantic-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'semantic-toast-container';
        container.style.cssText = 'position:fixed;top:70px;right:20px;z-index:10000;display:flex;flex-direction:column;gap:10px;';
        document.body.appendChild(container);
    }

    const colors = { warning: '#ff9800', error: '#f44336', success: '#4caf50', info: '#2196f3' };
    const icons = { warning: '\u26A0\uFE0F', error: '\u274C', success: '\u2713', info: '\u2139\uFE0F' };

    const toast = document.createElement('div');
    toast.innerHTML = `<div style="background:${colors[type] || colors.info};color:white;padding:12px 20px;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,0.3);display:flex;align-items:center;gap:10px;min-width:250px;"><span>${icons[type] || icons.info}</span><span>${message}</span></div>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), duration);
}

function handleDriftAlert(data) {
    console.log('[Semantic] Drift alert:', data);
    showSemanticToast(`High drift: ${((data.drift_score || 0) * 100).toFixed(1)}% - Re-embedding may be needed`, 'warning', 8000);
    refreshSemanticWidgets();
}

function handleQualityWarning(data) {
    console.log('[Semantic] Quality warning:', data);
    showSemanticToast(`Quality alert: ${((data.anomaly_rate || 0) * 100).toFixed(1)}% anomaly rate`, 'error', 8000);
    refreshSemanticWidgets();
}

function handleSnapshotCaptured(data) {
    console.log('[Semantic] Snapshot captured:', data);
    showSemanticToast(data.scheduled ? `Scheduled snapshot (${data.embedding_count} embeddings)` : `Snapshot: ${data.snapshot_id}`, 'success');
    refreshSemanticWidgets();
}

function initSemanticWebSocketHandlers() {
    if (typeof window.registerSocketHandler !== 'function') return;
    window.registerSocketHandler('drift_alert', handleDriftAlert);
    window.registerSocketHandler('quality_warning', handleQualityWarning);
    window.registerSocketHandler('snapshot_captured', handleSnapshotCaptured);
    console.log('[Semantic] WebSocket handlers registered');
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSemanticWebSocketHandlers);
} else {
    setTimeout(initSemanticWebSocketHandlers, 100);
}


// Export for module usage
export {
    SemanticAPI,
    EmbeddingViz,
    ClusterExplorer,
    DriftTimeline,
    QualityCard,
    FeedbackWidget,
    initSemanticWidgets,
    refreshSemanticWidgets,
    refreshSemanticMiniWidgets,
    initSemanticWebSocketHandlers,
    showSemanticToast
};
