const API_ROOT = '/api/pattern-quality';

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function number(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? n.toLocaleString() : '0';
}

function quality(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? n.toFixed(2) : '0.00';
}

function renderDecayCurve(points) {
    if (!Array.isArray(points) || points.length < 2) {
        return '<div class="pattern-quality-empty">No decay data</div>';
    }
    const width = 220;
    const height = 58;
    const maxAge = Math.max(...points.map(p => Number(p.age_days || 0)), 1);
    const path = points.map((point, index) => {
        const x = (Number(point.age_days || 0) / maxAge) * width;
        const y = height - (Number(point.decay_factor || 0) * height);
        return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(' ');
    return `
        <svg class="pattern-quality-curve" viewBox="0 0 ${width} ${height}" role="img" aria-label="Pattern decay curve">
            <path d="${path}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"></path>
            <line x1="0" y1="${height - 1}" x2="${width}" y2="${height - 1}" stroke="currentColor" stroke-opacity="0.25"></line>
            <line x1="0" y1="1" x2="${width}" y2="1" stroke="currentColor" stroke-opacity="0.16"></line>
        </svg>
    `;
}

function renderTopPatterns(patterns) {
    const container = document.getElementById('pattern-quality-top');
    if (!container) return;
    if (!Array.isArray(patterns) || patterns.length === 0) {
        container.innerHTML = '<div class="pattern-quality-empty">No ranked patterns</div>';
        return;
    }
    container.innerHTML = patterns.slice(0, 5).map(pattern => {
        const canonical = pattern.is_canonical ? '<span class="pattern-quality-tag">canonical</span>' : '';
        const path = pattern.representative_file_path || pattern.cluster_id || '-';
        return `
            <div class="pattern-quality-row">
                <div class="pattern-quality-row-main">
                    <span class="pattern-quality-score">${quality(pattern.quality_score)}</span>
                    <span class="pattern-quality-path" title="${escapeHtml(path)}">${escapeHtml(path.split('/').slice(-3).join('/'))}</span>
                    ${canonical}
                </div>
                <div class="pattern-quality-meta">x${number(pattern.occurrence_count)} · ${number(pattern.distinct_mission_count)} missions · decay ${quality(pattern.decay_factor)}</div>
            </div>
        `;
    }).join('');
}

function renderDuplicateClusters(clusters) {
    const container = document.getElementById('pattern-quality-clusters');
    if (!container) return;
    if (!Array.isArray(clusters) || clusters.length === 0) {
        container.innerHTML = '<div class="pattern-quality-empty">No duplicate clusters</div>';
        return;
    }
    container.innerHTML = clusters.slice(0, 4).map(cluster => {
        const sample = (cluster.normalized_code || '').slice(0, 92);
        return `
            <div class="pattern-quality-cluster">
                <div><strong>x${number(cluster.occurrence_count)}</strong> across ${number(cluster.distinct_mission_count)} missions</div>
                <code>${escapeHtml(sample || cluster.cluster_id || '-')}</code>
            </div>
        `;
    }).join('');
}

function applySummary(data) {
    const summary = data?.summary || {};
    setText('pattern-quality-clusters-count', number(summary.total_clusters));
    setText('pattern-quality-canonical-count', number(summary.canonical_clusters));
    setText('pattern-quality-duplicates-count', number(summary.duplicate_clusters));
    setText('pattern-quality-occurrences-count', number(summary.total_occurrences));
    setText('pattern-quality-badge', data?.available ? 'live' : 'offline');

    const badge = document.getElementById('pattern-quality-badge');
    if (badge) {
        badge.className = `badge ${data?.available ? 'badge-success' : 'badge-warning'}`;
    }

    renderTopPatterns(data?.top_patterns || []);
    renderDuplicateClusters(data?.duplicate_clusters || []);

    const curve = document.getElementById('pattern-quality-decay');
    if (curve) curve.innerHTML = renderDecayCurve(data?.decay_curve || []);
}

export async function refreshPatternQualityWidget() {
    const card = document.getElementById('pattern-quality-widget-card');
    if (!card) return;
    try {
        const response = await fetch(`${API_ROOT}/summary`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        applySummary(data);
    } catch (error) {
        applySummary({ available: false, summary: {}, top_patterns: [], duplicate_clusters: [], decay_curve: [] });
    }
}

export function initPatternQualityWidget() {
    refreshPatternQualityWidget();
}
