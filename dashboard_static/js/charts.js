/**
 * Dashboard Charts Module
 * Chart.js helpers and common configurations
 * Dependencies: core.js (Chart.js loaded from CDN)
 */

// =============================================================================
// CHART COLOR SCHEMES
// =============================================================================

const chartColors = {
    primary: '#58a6ff',
    success: '#3fb950',
    warning: '#d29922',
    danger: '#f85149',
    purple: '#bc8cff',
    violet: '#a371f7',
    grid: 'rgba(48, 54, 61, 0.5)',
    text: '#8b949e',
    background: '#161b22'
};

const chartColorPalette = [
    chartColors.primary,
    chartColors.success,
    chartColors.warning,
    chartColors.danger,
    chartColors.purple,
    chartColors.violet
];

// =============================================================================
// COMMON CHART OPTIONS
// =============================================================================

const commonChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
        legend: {
            display: false
        }
    },
    scales: {
        x: {
            grid: { color: chartColors.grid },
            ticks: { color: chartColors.text, font: { size: 9 } }
        },
        y: {
            grid: { color: chartColors.grid },
            ticks: { color: chartColors.text, font: { size: 9 } },
            beginAtZero: true
        }
    }
};

// =============================================================================
// CHART HELPERS
// =============================================================================

/**
 * Destroy a Chart.js instance safely
 * @param {Chart} chart - Chart.js instance to destroy
 * @returns {null}
 */
function destroyChart(chart) {
    if (chart) {
        chart.destroy();
    }
    return null;
}

/**
 * Create a bar chart
 * @param {string} canvasId - Canvas element ID
 * @param {object} data - Chart data { labels, datasets }
 * @param {object} options - Additional options
 * @returns {Chart} - Chart.js instance
 */
function createBarChart(canvasId, data, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const ctx = canvas.getContext('2d');

    return new Chart(ctx, {
        type: 'bar',
        data: data,
        options: {
            ...commonChartOptions,
            ...options
        }
    });
}

/**
 * Create a line chart
 * @param {string} canvasId - Canvas element ID
 * @param {object} data - Chart data { labels, datasets }
 * @param {object} options - Additional options
 * @returns {Chart} - Chart.js instance
 */
function createLineChart(canvasId, data, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const ctx = canvas.getContext('2d');

    return new Chart(ctx, {
        type: 'line',
        data: data,
        options: {
            ...commonChartOptions,
            ...options
        }
    });
}

/**
 * Create a donut/pie chart
 * @param {string} canvasId - Canvas element ID
 * @param {object} data - Chart data { labels, datasets }
 * @param {object} options - Additional options
 * @returns {Chart} - Chart.js instance
 */
function createDonutChart(canvasId, data, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    const ctx = canvas.getContext('2d');

    return new Chart(ctx, {
        type: 'doughnut',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: { color: chartColors.text }
                }
            },
            ...options
        }
    });
}

// Debug: mark charts module loaded
console.log('Charts module loaded');
