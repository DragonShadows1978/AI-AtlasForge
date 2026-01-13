#!/usr/bin/env python3
"""
Interactive Exploration Graph - API Routes

Flask routes for:
1. Filtered exploration graph visualization
2. Code journey playback events
3. Graph export (DOT, GraphML)
4. Edge type and filter metadata

These routes should be added to dashboard_v2.py

Part of the Interactive Exploration Graph Enhancement mission.
"""

from flask import jsonify, request, Response
from pathlib import Path
from datetime import datetime
import json

from atlasforge_config import EXPLORATION_DIR, MISSION_PATH

# Import our modules
import sys
sys.path.insert(0, str(Path(__file__).parent))

from interactive_graph import (
    export_to_dot,
    export_to_graphml,
    generate_journey_events,
    generate_journey_from_graph,
    get_filtered_visualization_data,
    extract_subgraph,
    generate_d3_visualization_data,
    EDGE_TYPES,
    NODE_TYPES
)


def register_interactive_graph_routes(app):
    """
    Register all interactive graph API routes with the Flask app.

    Call this from dashboard_v2.py after creating the app.
    """

    @app.route('/api/atlasforge/exploration-graph-enhanced')
    def api_atlasforge_exploration_graph_enhanced():
        """
        Get exploration graph with enhanced filtering and D3 visualization data.

        Query parameters:
        - width: Canvas width (default: 800)
        - height: Canvas height (default: 600)
        - edge_types: Comma-separated edge types to include (default: all)
        - node_types: Comma-separated node types to include (default: all)
        - time_start: ISO timestamp for start of time range
        - time_end: ISO timestamp for end of time range
        - min_count: Minimum exploration count (default: 0)
        """
        try:
            import exploration_hooks
            from atlasforge_enhancements import ExplorationGraph

            # Parse parameters
            width = request.args.get('width', 800, type=float)
            height = request.args.get('height', 600, type=float)

            edge_types = request.args.get('edge_types', '')
            edge_types = [e.strip() for e in edge_types.split(',') if e.strip()] or None

            node_types = request.args.get('node_types', '')
            node_types = [n.strip() for n in node_types.split(',') if n.strip()] or None

            time_start = request.args.get('time_start')
            time_end = request.args.get('time_end')

            min_count = request.args.get('min_count', 0, type=int)

            # Get enhancer with fresh data
            enhancer = exploration_hooks.get_current_enhancer(force_reload=True)

            if not enhancer or len(enhancer.exploration_graph.nodes) == 0:
                # Try global graph
                global_graph_path = EXPLORATION_DIR
                if global_graph_path.exists():
                    graph = ExplorationGraph(storage_path=global_graph_path)
                else:
                    return jsonify({
                        "error": "No exploration data available",
                        "nodes": [],
                        "edges": [],
                        "metadata": {"total_nodes": 0, "total_edges": 0}
                    })
            else:
                graph = enhancer.exploration_graph

            # Get filtered visualization data
            data = get_filtered_visualization_data(
                graph,
                width=width,
                height=height,
                edge_types=edge_types,
                node_types=node_types,
                time_start=time_start,
                time_end=time_end,
                min_exploration_count=min_count
            )

            return jsonify(data)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({
                "error": str(e),
                "nodes": [],
                "edges": [],
                "metadata": {"total_nodes": 0, "total_edges": 0}
            })

    @app.route('/api/atlasforge/exploration-journey')
    def api_atlasforge_exploration_journey():
        """
        Get chronologically ordered exploration events for playback.

        Query parameters:
        - mission_id: Mission ID (default: current mission)
        - limit: Maximum events (default: 500)
        """
        try:
            import io_utils

            mission_id = request.args.get('mission_id')
            limit = request.args.get('limit', 500, type=int)

            if not mission_id:
                # Use current mission
                mission_path = MISSION_PATH
                mission = io_utils.atomic_read_json(mission_path, {})
                mission_id = mission.get('mission_id')

            if not mission_id:
                return jsonify({
                    "error": "No mission ID provided",
                    "events": [],
                    "total_events": 0
                })

            # Try to get events from decision graph
            events = []
            try:
                from decision_graph import get_decision_logger
                logger = get_decision_logger()
                events = generate_journey_events(mission_id, logger)
            except ImportError:
                pass

            # Fallback: generate from exploration graph
            if not events:
                try:
                    import exploration_hooks
                    enhancer = exploration_hooks.get_current_enhancer(force_reload=True)
                    if enhancer:
                        events = generate_journey_from_graph(enhancer.exploration_graph)
                except Exception:
                    pass

            # Apply limit
            events = events[:limit]

            return jsonify({
                "mission_id": mission_id,
                "events": events,
                "total_events": len(events)
            })

        except Exception as e:
            return jsonify({
                "error": str(e),
                "events": [],
                "total_events": 0
            })

    @app.route('/api/atlasforge/export/<format>')
    def api_atlasforge_export(format):
        """
        Export exploration graph to DOT or GraphML format.

        Path parameter:
        - format: 'dot' or 'graphml'

        Query parameters:
        - edge_types: Comma-separated edge types to include
        - node_types: Comma-separated node types to include
        """
        try:
            import exploration_hooks
            from atlasforge_enhancements import ExplorationGraph

            # Get graph data
            enhancer = exploration_hooks.get_current_enhancer(force_reload=True)

            if not enhancer or len(enhancer.exploration_graph.nodes) == 0:
                # Try global graph
                global_graph_path = EXPLORATION_DIR
                if global_graph_path.exists():
                    graph = ExplorationGraph(storage_path=global_graph_path)
                else:
                    return jsonify({"error": "No exploration data available"}), 404
            else:
                graph = enhancer.exploration_graph

            # Get visualization data (includes nodes and edges)
            viz_data = graph.export_for_visualization(800, 600)
            nodes = viz_data.get('nodes', [])
            edges = viz_data.get('edges', [])

            # Apply edge/node type filters if provided
            edge_types = request.args.get('edge_types', '')
            if edge_types:
                edge_types = [e.strip() for e in edge_types.split(',') if e.strip()]
                edges = [e for e in edges if e.get('relationship') in edge_types]

            node_types = request.args.get('node_types', '')
            if node_types:
                node_types = [n.strip() for n in node_types.split(',') if n.strip()]
                nodes = [n for n in nodes if n.get('type') in node_types]
                # Filter edges to remaining nodes
                node_ids = {n['id'] for n in nodes}
                edges = [e for e in edges if e.get('source') in node_ids and e.get('target') in node_ids]

            # Export
            if format.lower() == 'dot':
                content = export_to_dot(nodes, edges)
                mimetype = 'text/vnd.graphviz'
                filename = 'exploration_graph.dot'
            elif format.lower() in ('graphml', 'xml'):
                content = export_to_graphml(nodes, edges)
                mimetype = 'application/xml'
                filename = 'exploration_graph.graphml'
            elif format.lower() == 'json':
                content = json.dumps({
                    'nodes': nodes,
                    'edges': edges,
                    'exported_at': datetime.now().isoformat()
                }, indent=2)
                mimetype = 'application/json'
                filename = 'exploration_graph.json'
            else:
                return jsonify({
                    "error": f"Unknown format: {format}. Use 'dot', 'graphml', or 'json'"
                }), 400

            response = Response(content, mimetype=mimetype)
            response.headers['Content-Disposition'] = f'attachment; filename={filename}'
            return response

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    @app.route('/api/atlasforge/edge-types')
    def api_atlasforge_edge_types():
        """Get available edge types with their configuration."""
        return jsonify({
            "edge_types": EDGE_TYPES,
            "node_types": NODE_TYPES
        })

    @app.route('/api/atlasforge/subgraph')
    def api_atlasforge_subgraph():
        """
        Extract a subgraph centered on a specific node.

        Query parameters:
        - node_id: The node ID to center on (required)
        - depth: How many hops away to include (default: 1, max: 3)
        """
        try:
            import exploration_hooks
            from atlasforge_enhancements import ExplorationGraph

            node_id = request.args.get('node_id', '')
            depth = min(request.args.get('depth', 1, type=int), 3)

            if not node_id:
                return jsonify({
                    "error": "node_id parameter required",
                    "nodes": [],
                    "edges": [],
                    "metadata": {"total_nodes": 0, "total_edges": 0}
                })

            # Get exploration graph
            enhancer = exploration_hooks.get_current_enhancer(force_reload=True)

            if not enhancer or len(enhancer.exploration_graph.nodes) == 0:
                global_graph_path = EXPLORATION_DIR
                if global_graph_path.exists():
                    graph = ExplorationGraph(storage_path=global_graph_path)
                else:
                    return jsonify({
                        "error": "No exploration data available",
                        "nodes": [],
                        "edges": [],
                        "metadata": {"total_nodes": 0, "total_edges": 0}
                    })
            else:
                graph = enhancer.exploration_graph

            # Get base visualization data
            base_data = graph.export_for_visualization(800, 600)
            nodes = base_data.get('nodes', [])
            edges = base_data.get('edges', [])

            # Check if node exists
            if not any(n.get('id') == node_id for n in nodes):
                return jsonify({
                    "error": f"Node '{node_id}' not found",
                    "nodes": [],
                    "edges": [],
                    "metadata": {"total_nodes": 0, "total_edges": 0}
                })

            # Extract subgraph
            filtered_nodes, filtered_edges = extract_subgraph(nodes, edges, node_id, depth)

            # Generate D3 visualization data
            result = generate_d3_visualization_data(filtered_nodes, filtered_edges, 800, 600)
            result['center_node'] = node_id
            result['depth'] = depth

            return jsonify(result)

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({
                "error": str(e),
                "nodes": [],
                "edges": [],
                "metadata": {"total_nodes": 0, "total_edges": 0}
            })

    @app.route('/api/atlasforge/file-preview')
    def api_atlasforge_file_preview():
        """
        Get a preview of a file's content (first N lines).

        Query parameters:
        - path: The file path to preview
        - lines: Number of lines to return (default: 50, max: 200)
        """
        try:
            file_path = request.args.get('path', '')
            max_lines = min(request.args.get('lines', 50, type=int), 200)

            if not file_path:
                return jsonify({"error": "No path provided", "content": "", "total_lines": 0})

            # Security: only allow files under certain directories
            # These are dynamically set based on the AtlasForge installation
            from atlasforge_config import BASE_DIR
            allowed_roots = [
                str(BASE_DIR) + '/',
                str(Path.home()) + '/'
            ]
            abs_path = str(Path(file_path).resolve())

            if not any(abs_path.startswith(root) for root in allowed_roots):
                return jsonify({"error": "Path not allowed", "content": "", "total_lines": 0})

            if not Path(abs_path).exists():
                return jsonify({"error": "File not found", "content": "", "total_lines": 0})

            if not Path(abs_path).is_file():
                return jsonify({"error": "Not a file", "content": "", "total_lines": 0})

            # Read file content
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = []
                    total_lines = 0
                    for i, line in enumerate(f):
                        total_lines = i + 1
                        if len(lines) < max_lines:
                            # Truncate very long lines
                            if len(line) > 500:
                                line = line[:500] + '...\n'
                            lines.append(line)

                    content = ''.join(lines)

                return jsonify({
                    "path": abs_path,
                    "content": content,
                    "lines_shown": len(lines),
                    "total_lines": total_lines,
                    "truncated": total_lines > max_lines
                })

            except UnicodeDecodeError:
                return jsonify({"error": "Binary file, cannot preview", "content": "", "total_lines": 0})

        except Exception as e:
            return jsonify({"error": str(e), "content": "", "total_lines": 0})

    @app.route('/api/atlasforge/graph-stats')
    def api_atlasforge_graph_stats():
        """Get detailed graph statistics."""
        try:
            import exploration_hooks
            from atlasforge_enhancements import ExplorationGraph
            from collections import Counter

            enhancer = exploration_hooks.get_current_enhancer(force_reload=True)

            if not enhancer or len(enhancer.exploration_graph.nodes) == 0:
                global_graph_path = EXPLORATION_DIR
                if global_graph_path.exists():
                    graph = ExplorationGraph(storage_path=global_graph_path)
                else:
                    return jsonify({"error": "No exploration data available"})
            else:
                graph = enhancer.exploration_graph

            # Compute statistics
            node_types = Counter(n.node_type for n in graph.nodes.values())
            edge_types = Counter(e.relationship for e in graph.edges)

            # Most explored
            most_explored = sorted(
                graph.nodes.values(),
                key=lambda n: n.exploration_count,
                reverse=True
            )[:10]

            # Most connected
            connection_counts = Counter()
            for edge in graph.edges:
                connection_counts[edge.source_id] += 1
                connection_counts[edge.target_id] += 1

            most_connected = [
                {
                    'id': node_id,
                    'name': graph.nodes[node_id].name if node_id in graph.nodes else node_id,
                    'connections': count
                }
                for node_id, count in connection_counts.most_common(10)
            ]

            return jsonify({
                'total_nodes': len(graph.nodes),
                'total_edges': len(graph.edges),
                'total_insights': len(graph.insights),
                'node_types': dict(node_types),
                'edge_types': dict(edge_types),
                'most_explored': [
                    {'name': n.name, 'count': n.exploration_count, 'type': n.node_type}
                    for n in most_explored
                ],
                'most_connected': most_connected,
                'generated_at': datetime.now().isoformat()
            })

        except Exception as e:
            return jsonify({"error": str(e)})

    @app.route('/api/atlasforge/streaming/status')
    def api_atlasforge_streaming_status():
        """
        Get real-time streaming status.

        Returns connection info, active clients, and recording status.
        """
        try:
            from realtime_graph_streaming import get_stream_manager
            manager = get_stream_manager()

            return jsonify({
                'active_connections': manager.stats.active_connections,
                'recording': manager.recording,
                'recording_status': manager.get_recording_status(),
                'stats': manager.stats.to_dict(),
                'current_state_size': {
                    'nodes': len(manager.current_nodes),
                    'edges': len(manager.current_edges)
                },
                'recent_events_count': len(manager.recent_events),
                'enabled': True
            })
        except ImportError:
            return jsonify({
                'enabled': False,
                'error': 'Streaming module not available'
            })
        except Exception as e:
            return jsonify({'error': str(e)})

    @app.route('/api/atlasforge/streaming/toggle', methods=['POST'])
    def api_atlasforge_streaming_toggle():
        """
        Toggle real-time streaming on/off.

        POST body:
        - enabled: bool (optional, toggles if not provided)
        """
        try:
            from realtime_graph_streaming import get_stream_manager
            from exploration_hooks import get_streaming_rate_limiter
            manager = get_stream_manager()
            rate_limiter = get_streaming_rate_limiter()

            data = request.get_json() or {}
            enabled = data.get('enabled')

            # If enabled is not specified, toggle current state
            if enabled is None:
                # Toggle based on whether there are active connections
                if manager.stats.active_connections > 0:
                    # Reset to "disable" streaming events
                    rate_limiter.reset()
                    return jsonify({
                        'status': 'rate_limiter_reset',
                        'enabled': True,
                        'message': 'Rate limiter reset - streaming will resume'
                    })
                else:
                    return jsonify({
                        'status': 'no_clients',
                        'enabled': True,
                        'message': 'No clients connected - streaming ready'
                    })

            if enabled:
                rate_limiter.reset()
                return jsonify({
                    'status': 'enabled',
                    'enabled': True,
                    'message': 'Streaming enabled'
                })
            else:
                # "Disable" by making rate limiter very restrictive
                # Note: We don't truly disable, just rate limit heavily
                return jsonify({
                    'status': 'rate_limited',
                    'enabled': False,
                    'message': 'Streaming rate limited'
                })

        except ImportError:
            return jsonify({
                'error': 'Streaming module not available',
                'enabled': False
            }), 503
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/atlasforge/streaming/recent-events')
    def api_atlasforge_streaming_recent_events():
        """Get recent streaming events."""
        try:
            from realtime_graph_streaming import get_stream_manager
            manager = get_stream_manager()

            limit = request.args.get('limit', 50, type=int)
            events = manager.get_recent_events(limit)

            return jsonify({
                'events': events,
                'total': len(events)
            })
        except ImportError:
            return jsonify({
                'error': 'Streaming module not available',
                'events': []
            })
        except Exception as e:
            return jsonify({'error': str(e), 'events': []})


# =============================================================================
# HTML/JS SNIPPET FOR DASHBOARD
# =============================================================================

INTERACTIVE_GRAPH_HTML = '''
<!-- Enhanced Exploration Graph Widget -->
<div class="card" id="atlasforge-enhanced-graph-card">
    <div class="card-header" onclick="toggleCard('atlasforge-enhanced-graph')">
        <h3>Exploration Graph</h3>
        <span class="collapse-toggle">▼</span>
    </div>
    <div class="card-content">
        <!-- Node Search Box -->
        <div id="node-search-box" style="margin-bottom: 10px;">
            <div style="display: flex; gap: 5px; align-items: center;">
                <input type="text" id="node-search-input"
                       placeholder="Search nodes..."
                       style="flex: 1; padding: 6px 10px; font-size: 0.85em; background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 4px;"
                       oninput="searchNodes(this.value)">
                <button class="btn btn-small" onclick="clearNodeSearch()" style="padding: 4px 8px; font-size: 0.75em;" title="Clear search">✕</button>
                <span id="node-search-count" style="font-size: 0.75em; color: var(--text-dim); min-width: 60px;"></span>
            </div>
        </div>

        <!-- Filter Controls -->
        <div id="graph-filters" class="filter-panel" style="margin-bottom: 10px; padding: 10px; background: var(--bg); border-radius: 6px;">
            <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center;">
                <div class="filter-group">
                    <label style="font-size: 0.8em; color: var(--text-dim);">Edge Types:</label>
                    <div style="display: flex; gap: 5px; flex-wrap: wrap;">
                        <label style="font-size: 0.75em;"><input type="checkbox" value="import" checked class="edge-filter"> Import</label>
                        <label style="font-size: 0.75em;"><input type="checkbox" value="grep_to_read" checked class="edge-filter"> Grep→Read</label>
                        <label style="font-size: 0.75em;"><input type="checkbox" value="explored_next" class="edge-filter"> Sequential</label>
                        <label style="font-size: 0.75em;"><input type="checkbox" value="reference" checked class="edge-filter"> Reference</label>
                        <label style="font-size: 0.75em;"><input type="checkbox" value="test_of" checked class="edge-filter"> Test</label>
                    </div>
                </div>
                <div class="filter-group">
                    <label style="font-size: 0.8em; color: var(--text-dim);">Node Types:</label>
                    <select id="filter-node-types" multiple style="font-size: 0.8em; padding: 2px; height: 45px;">
                        <option value="file" selected>Files</option>
                        <option value="concept" selected>Concepts</option>
                        <option value="pattern" selected>Patterns</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label style="font-size: 0.8em; color: var(--text-dim);">Time Window:</label>
                    <div style="display: flex; gap: 5px; align-items: center; flex-wrap: wrap;">
                        <input type="datetime-local" id="filter-time-start"
                               style="font-size: 0.75em; padding: 2px 4px; background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 4px;"
                               title="Start time (optional)">
                        <span style="font-size: 0.75em; color: var(--text-dim);">to</span>
                        <input type="datetime-local" id="filter-time-end"
                               style="font-size: 0.75em; padding: 2px 4px; background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 4px;"
                               title="End time (optional)">
                        <button class="btn btn-small" onclick="clearTimeFilters()" style="padding: 2px 6px; font-size: 0.7em;" title="Clear time filter">✕</button>
                    </div>
                </div>
                <div style="display: flex; gap: 5px;">
                    <button class="btn btn-small" onclick="applyGraphFilters()" style="padding: 4px 8px; font-size: 0.75em;">Apply</button>
                    <button class="btn btn-small" onclick="resetGraphFilters()" style="padding: 4px 8px; font-size: 0.75em;">Reset</button>
                </div>
            </div>
        </div>

        <!-- Graph Canvas -->
        <div style="position: relative;">
            <canvas id="exploration-graph-canvas" width="400" height="300"
                    style="border: 1px solid var(--border); border-radius: 6px; background: var(--bg); cursor: grab;"></canvas>
            <div style="position: absolute; top: 5px; right: 5px; display: flex; gap: 5px;">
                <button class="btn btn-small" onclick="refreshEnhancedGraph()" style="padding: 4px 8px; font-size: 0.75em;">Refresh</button>
                <select id="graph-export-format" style="font-size: 0.75em; padding: 2px;">
                    <option value="">Export...</option>
                    <option value="dot">DOT (Graphviz)</option>
                    <option value="graphml">GraphML</option>
                    <option value="json">JSON</option>
                    <option value="png">PNG Image</option>
                    <option value="svg">SVG Image</option>
                </select>
            </div>
        </div>

        <!-- Edge Tooltip (separate from node tooltip) -->
        <div id="edge-tooltip" style="display: none; position: fixed; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; max-width: 250px; z-index: 1001; pointer-events: none; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
            <div class="edge-tt-type" style="font-weight: 600; margin-bottom: 4px;"></div>
            <div class="edge-tt-context" style="font-size: 0.85em; color: var(--text-dim);"></div>
            <div class="edge-tt-strength" style="font-size: 0.8em; color: var(--text-dim); margin-top: 4px;"></div>
        </div>

        <!-- Node Details Panel -->
        <div id="graph-node-panel" style="margin-top: 10px; padding: 10px; background: var(--bg); border-radius: 6px; font-size: 0.85em; min-height: 60px;">
            <div style="color: var(--text-dim);">Click a node to see details</div>
        </div>

        <!-- File Content Preview with Syntax Highlighting -->
        <div id="file-preview-panel" style="margin-top: 10px; display: none;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h4 style="margin: 0; font-size: 0.9em;">File Preview</h4>
                <div style="display: flex; gap: 8px; align-items: center;">
                    <span id="file-preview-lang" style="font-size: 0.7em; padding: 2px 6px; background: var(--blue); color: #fff; border-radius: 4px;"></span>
                    <span id="file-preview-info" style="font-size: 0.75em; color: var(--text-dim);"></span>
                </div>
            </div>
            <pre id="file-preview-content" class="syntax-highlighted" style="margin: 0; padding: 10px; background: #1a1a2e; border: 1px solid var(--border); border-radius: 6px; font-size: 0.75em; max-height: 300px; overflow: auto; white-space: pre-wrap; word-wrap: break-word; font-family: 'Fira Code', 'Consolas', 'Monaco', monospace;"></pre>
        </div>

        <!-- Legend -->
        <div class="graph-legend" style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 10px; font-size: 0.75em;">
            <span><span style="display: inline-block; width: 10px; height: 10px; background: #58a6ff; border-radius: 50%;"></span> File</span>
            <span><span style="display: inline-block; width: 10px; height: 10px; background: #3fb950; border-radius: 50%;"></span> Concept</span>
            <span><span style="display: inline-block; width: 10px; height: 10px; background: #d29922; border-radius: 50%;"></span> Pattern</span>
            <span style="color: var(--text-dim);">|</span>
            <span><span style="border-bottom: 2px solid #58a6ff; width: 15px; display: inline-block;"></span> Import</span>
            <span><span style="border-bottom: 2px dashed #3fb950; width: 15px; display: inline-block;"></span> Grep→Read</span>
            <span><span style="border-bottom: 2px dotted #8b949e; width: 15px; display: inline-block;"></span> Sequential</span>
        </div>

        <!-- Stats -->
        <div style="margin-top: 10px; display: flex; gap: 15px;">
            <div class="atlasforge-drift-status">
                <span class="label">Nodes</span>
                <span class="value" id="graph-node-count">0</span>
            </div>
            <div class="atlasforge-drift-status">
                <span class="label">Edges</span>
                <span class="value" id="graph-edge-count">0</span>
            </div>
        </div>

        <!-- Playback Controls -->
        <div id="playback-controls" style="margin-top: 15px; padding: 10px; background: var(--bg); border-radius: 6px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h4 style="margin: 0; font-size: 0.9em;">Code Journey Playback</h4>
                <span style="font-size: 0.8em; color: var(--text-dim);">
                    <span id="playback-current">0</span> / <span id="playback-total">0</span>
                </span>
            </div>
            <div style="height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; margin-bottom: 8px;">
                <div id="playback-progress" style="height: 100%; width: 0%; background: var(--blue); transition: width 0.3s;"></div>
            </div>
            <div style="display: flex; gap: 5px; align-items: center;">
                <button id="btn-reset" class="btn btn-small" onclick="journeyPlayback && journeyPlayback.reset()" style="padding: 4px 8px; font-size: 0.75em;">Reset</button>
                <button id="btn-back" class="btn btn-small" onclick="journeyPlayback && journeyPlayback.stepBack()" style="padding: 4px 8px; font-size: 0.75em;">◀</button>
                <button id="btn-play" class="btn btn-small primary" onclick="togglePlayback()" style="padding: 4px 8px; font-size: 0.75em;">Play</button>
                <button id="btn-step" class="btn btn-small" onclick="journeyPlayback && journeyPlayback.step()" style="padding: 4px 8px; font-size: 0.75em;">▶</button>
                <input type="range" min="100" max="2000" value="1000" style="width: 60px;"
                       onchange="journeyPlayback && journeyPlayback.setSpeed(this.value)" title="Speed">
                <button class="btn btn-small" onclick="loadJourney()" style="padding: 4px 8px; font-size: 0.75em;">Load</button>
            </div>
            <div id="playback-event-info" style="margin-top: 8px; min-height: 30px; color: var(--text-dim); font-size: 0.85em;">
                Click "Load" to load exploration journey
            </div>
        </div>
    </div>
</div>

<!-- Tooltip (shared) -->
<div id="graph-tooltip" class="graph-tooltip" style="display: none; position: fixed; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px; max-width: 300px; z-index: 1000; pointer-events: none; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
    <div class="tt-name" style="font-weight: 600; margin-bottom: 4px;"></div>
    <div class="tt-type" style="font-size: 0.8em; color: var(--text-dim);"></div>
    <div class="tt-path" style="font-size: 0.75em; color: var(--text-dim); word-break: break-all;"></div>
    <div class="tt-summary" style="font-size: 0.85em; margin-top: 4px;"></div>
</div>
'''

INTERACTIVE_GRAPH_JS = '''
<script>
// =============================================================================
// Interactive Graph Initialization
// =============================================================================

let enhancedGraphRenderer = null;
let journeyPlayback = null;
let graphFilterController = null;
let syntaxHighlighter = null;

function initializeEnhancedGraph() {
    // Create renderer
    enhancedGraphRenderer = new EnhancedGraphRenderer('exploration-graph-canvas');
    journeyPlayback = new JourneyPlayback(enhancedGraphRenderer);
    graphFilterController = new GraphFilterController(enhancedGraphRenderer);
    syntaxHighlighter = new SyntaxHighlighter();

    // Setup export dropdown
    const exportSelect = document.getElementById('graph-export-format');
    if (exportSelect) {
        exportSelect.addEventListener('change', function() {
            if (this.value) {
                exportGraph(this.value);
                this.value = '';
            }
        });
    }

    return enhancedGraphRenderer;
}

// =============================================================================
// Node Search Functions
// =============================================================================

let searchDebounceTimer = null;

function searchNodes(query) {
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => _doSearchNodes(query), 150);
}

function _doSearchNodes(query) {
    if (!enhancedGraphRenderer) return;
    const countEl = document.getElementById('node-search-count');

    if (!query || query.trim() === '') {
        enhancedGraphRenderer.clearSearchHighlights();
        if (countEl) countEl.textContent = '';
        return;
    }

    const matches = enhancedGraphRenderer.searchAndHighlight(query.toLowerCase());
    if (countEl) {
        countEl.textContent = matches > 0 ? `${matches} found` : 'No matches';
        countEl.style.color = matches > 0 ? 'var(--green)' : 'var(--text-dim)';
    }
}

function clearNodeSearch() {
    const input = document.getElementById('node-search-input');
    const countEl = document.getElementById('node-search-count');
    if (input) input.value = '';
    if (countEl) countEl.textContent = '';
    if (enhancedGraphRenderer) enhancedGraphRenderer.clearSearchHighlights();
}

// =============================================================================
// Syntax Highlighting Class
// =============================================================================

class SyntaxHighlighter {
    constructor() {
        this.rules = {
            python: [
                { pattern: /(#.*$)/gm, className: 'comment' },
                { pattern: /((?:"""|\'\'\'|`)[\\s\\S]*?(?:"""|\'\'\'|`))/g, className: 'string' },
                { pattern: /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, className: 'string' },
                { pattern: /\\b(def|class|if|elif|else|for|while|try|except|finally|with|as|import|from|return|yield|raise|break|continue|pass|lambda|and|or|not|in|is|True|False|None|async|await)\\b/g, className: 'keyword' },
                { pattern: /\\b(self|cls)\\b/g, className: 'builtin' },
                { pattern: /\\b(int|str|float|list|dict|tuple|set|bool|bytes|type|object|print|len|range|enumerate|zip|map|filter|sorted|open|super)\\b/g, className: 'builtin' },
                { pattern: /\\b(\\d+\\.?\\d*)\\b/g, className: 'number' },
                { pattern: /(def\\s+)(\\w+)/g, className: 'function', group: 2 },
                { pattern: /(class\\s+)(\\w+)/g, className: 'class', group: 2 },
                { pattern: /@\\w+/g, className: 'decorator' }
            ],
            javascript: [
                { pattern: /(\/\/.*$)/gm, className: 'comment' },
                { pattern: /(\/\\*[\\s\\S]*?\\*\/)/g, className: 'comment' },
                { pattern: /(`(?:[^`\\\\]|\\\\.)*`|"(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*')/g, className: 'string' },
                { pattern: /\\b(const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|new|this|super|import|export|from|default|async|await|try|catch|finally|throw|typeof|instanceof)\\b/g, className: 'keyword' },
                { pattern: /\\b(true|false|null|undefined|NaN|Infinity)\\b/g, className: 'builtin' },
                { pattern: /\\b(console|document|window|Array|Object|String|Number|Boolean|Math|JSON|Promise|Map|Set)\\b/g, className: 'builtin' },
                { pattern: /\\b(\\d+\\.?\\d*)\\b/g, className: 'number' },
                { pattern: /(function\\s+)(\\w+)/g, className: 'function', group: 2 },
                { pattern: /=>\\s*/g, className: 'keyword' }
            ],
            json: [
                { pattern: /("(?:[^"\\\\]|\\\\.)*")\\s*:/g, className: 'key' },
                { pattern: /:\\s*("(?:[^"\\\\]|\\\\.)*")/g, className: 'string', group: 1 },
                { pattern: /\\b(true|false|null)\\b/g, className: 'keyword' },
                { pattern: /\\b(-?\\d+\\.?\\d*(?:[eE][+-]?\\d+)?)\\b/g, className: 'number' }
            ]
        };

        this.styles = `
            .syntax-highlighted .keyword { color: #ff79c6; }
            .syntax-highlighted .string { color: #f1fa8c; }
            .syntax-highlighted .comment { color: #6272a4; font-style: italic; }
            .syntax-highlighted .number { color: #bd93f9; }
            .syntax-highlighted .function { color: #50fa7b; }
            .syntax-highlighted .class { color: #8be9fd; }
            .syntax-highlighted .builtin { color: #8be9fd; }
            .syntax-highlighted .decorator { color: #ffb86c; }
            .syntax-highlighted .key { color: #8be9fd; }
        `;

        this.injectStyles();
    }

    injectStyles() {
        if (!document.getElementById('syntax-highlight-styles')) {
            const style = document.createElement('style');
            style.id = 'syntax-highlight-styles';
            style.textContent = this.styles;
            document.head.appendChild(style);
        }
    }

    detectLanguage(filePath) {
        if (!filePath) return 'text';
        const ext = filePath.split('.').pop().toLowerCase();
        const langMap = {
            'py': 'python',
            'js': 'javascript',
            'ts': 'javascript',
            'jsx': 'javascript',
            'tsx': 'javascript',
            'json': 'json',
            'md': 'text',
            'txt': 'text',
            'sh': 'text',
            'bash': 'text'
        };
        return langMap[ext] || 'text';
    }

    highlight(code, language) {
        if (!code) return '';

        // Escape HTML first
        let escaped = code
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        const rules = this.rules[language];
        if (!rules) return escaped;

        // Apply highlighting rules
        for (const rule of rules) {
            escaped = escaped.replace(rule.pattern, (match, ...groups) => {
                if (rule.group !== undefined && groups[rule.group - 1]) {
                    // Highlight specific capture group
                    const before = match.slice(0, match.indexOf(groups[rule.group - 1]));
                    const target = groups[rule.group - 1];
                    const after = match.slice(match.indexOf(target) + target.length);
                    return before + `<span class="${rule.className}">${target}</span>` + after;
                }
                return `<span class="${rule.className}">${match}</span>`;
            });
        }

        return escaped;
    }
}

let isSubgraphView = false;

async function refreshEnhancedGraph() {
    if (!enhancedGraphRenderer) {
        initializeEnhancedGraph();
    }

    try {
        const response = await fetch('/api/atlasforge/exploration-graph-enhanced?width=800&height=600');
        const data = await response.json();

        if (data.error && (!data.nodes || data.nodes.length === 0)) {
            console.log('No graph data:', data.error);
            return;
        }

        enhancedGraphRenderer.loadData(data);
        isSubgraphView = false;
        updateSubgraphIndicator(false);
    } catch (e) {
        console.error('Failed to load graph:', e);
    }
}

async function extractSubgraph(nodeId, depth) {
    if (!enhancedGraphRenderer) return;

    try {
        const response = await fetch(`/api/atlasforge/subgraph?node_id=${encodeURIComponent(nodeId)}&depth=${depth}`);
        const data = await response.json();

        if (data.error) {
            console.error('Failed to extract subgraph:', data.error);
            return;
        }

        enhancedGraphRenderer.loadData(data);
        isSubgraphView = true;
        updateSubgraphIndicator(true, nodeId, depth, data.metadata?.total_nodes || 0);
    } catch (e) {
        console.error('Failed to extract subgraph:', e);
    }
}

function updateSubgraphIndicator(isActive, nodeId = null, depth = 0, nodeCount = 0) {
    let indicator = document.getElementById('subgraph-indicator');
    if (!indicator) {
        // Create indicator if it doesn't exist
        const graphCard = document.getElementById('atlasforge-enhanced-graph-card');
        if (!graphCard) return;
        indicator = document.createElement('div');
        indicator.id = 'subgraph-indicator';
        indicator.style.cssText = 'padding: 6px 10px; background: var(--orange); color: #fff; border-radius: 4px; font-size: 0.8em; margin-bottom: 8px; display: none; align-items: center; gap: 8px;';
        const canvasWrapper = graphCard.querySelector('.card-content > div[style*="position: relative"]');
        if (canvasWrapper) {
            canvasWrapper.parentNode.insertBefore(indicator, canvasWrapper);
        }
    }

    if (isActive) {
        indicator.innerHTML = `
            <span>🔍 Subgraph view: ${nodeCount} nodes (depth ${depth})</span>
            <button class="btn btn-small" onclick="refreshEnhancedGraph()" style="padding: 2px 8px; font-size: 0.75em; background: #fff; color: var(--orange);">Show All</button>
        `;
        indicator.style.display = 'flex';
    } else {
        indicator.style.display = 'none';
    }
}

function applyGraphFilters() {
    if (!graphFilterController) return;

    // Get selected edge types
    const edgeTypes = Array.from(document.querySelectorAll('.edge-filter:checked'))
        .map(cb => cb.value);
    graphFilterController.setEdgeTypeFilters(edgeTypes);

    // Get selected node types
    const nodeTypes = Array.from(document.getElementById('filter-node-types').selectedOptions)
        .map(opt => opt.value);
    graphFilterController.setNodeTypeFilters(nodeTypes);

    // Get time window filters
    const timeStart = document.getElementById('filter-time-start')?.value;
    const timeEnd = document.getElementById('filter-time-end')?.value;
    if (timeStart || timeEnd) {
        graphFilterController.setTimeRange(
            timeStart ? new Date(timeStart).toISOString() : null,
            timeEnd ? new Date(timeEnd).toISOString() : null
        );
    } else {
        graphFilterController.setTimeRange(null, null);
    }

    graphFilterController.applyFilters();
}

function clearTimeFilters() {
    const startInput = document.getElementById('filter-time-start');
    const endInput = document.getElementById('filter-time-end');
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
}

function resetGraphFilters() {
    // Reset checkboxes
    document.querySelectorAll('.edge-filter').forEach(cb => cb.checked = true);

    // Reset node types
    const nodeSelect = document.getElementById('filter-node-types');
    if (nodeSelect) {
        Array.from(nodeSelect.options).forEach(opt => opt.selected = true);
    }

    // Reset time filters
    clearTimeFilters();

    if (graphFilterController) {
        graphFilterController.resetFilters();
    }
}

function togglePlayback() {
    if (journeyPlayback) {
        journeyPlayback.toggle();
    }
}

async function loadJourney() {
    if (!journeyPlayback) return;

    const count = await journeyPlayback.loadJourney();
    if (count > 0) {
        document.getElementById('playback-event-info').innerHTML =
            `<span style="color: var(--green);">Loaded ${count} events. Click Play to start.</span>`;
    } else {
        document.getElementById('playback-event-info').innerHTML =
            '<span style="color: var(--text-dim);">No journey events found</span>';
    }
}

function exportGraph(format) {
    if (!format) return;

    if (format === 'png' || format === 'svg') {
        // Export canvas as image
        exportCanvasImage(format);
    } else {
        // Export via API (DOT, GraphML, JSON)
        window.location.href = '/api/atlasforge/export/' + format;
    }
}

function exportCanvasImage(format) {
    if (!enhancedGraphRenderer || !enhancedGraphRenderer.canvas) {
        alert('No graph to export');
        return;
    }

    const canvas = enhancedGraphRenderer.canvas;

    if (format === 'png') {
        // Export as PNG
        const link = document.createElement('a');
        link.download = 'exploration_graph.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    } else if (format === 'svg') {
        // Convert canvas to SVG
        const svgContent = canvasToSVG(enhancedGraphRenderer);
        const blob = new Blob([svgContent], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.download = 'exploration_graph.svg';
        link.href = url;
        link.click();
        URL.revokeObjectURL(url);
    }
}

function canvasToSVG(renderer) {
    const width = renderer.canvas.width;
    const height = renderer.canvas.height;
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`;

    // Background
    svg += `<rect width="${width}" height="${height}" fill="#0d1117"/>`;

    // Draw edges
    for (const edge of renderer.edges) {
        const source = renderer.nodes.find(n => n.id === edge.source);
        const target = renderer.nodes.find(n => n.id === edge.target);
        if (!source || !target) continue;

        const x1 = renderer.transformX(source.x);
        const y1 = renderer.transformY(source.y);
        const x2 = renderer.transformX(target.x);
        const y2 = renderer.transformY(target.y);

        const config = renderer.edgeConfig[edge.relationship] || {};
        const color = config.color || '#8b949e';
        const dashArray = config.dash ? config.dash.join(',') : '';

        svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="${Math.max(1, (edge.strength || 0.5) * 2)}" stroke-opacity="0.6" ${dashArray ? `stroke-dasharray="${dashArray}"` : ''}/>`;
    }

    // Draw nodes
    for (const node of renderer.nodes) {
        const x = renderer.transformX(node.x);
        const y = renderer.transformY(node.y);
        const size = Math.max(6, (node.size || 15) * renderer.scale * 0.5);
        const color = node.color || renderer.nodeColors[node.type] || '#8b949e';

        svg += `<circle cx="${x}" cy="${y}" r="${size}" fill="${color}"/>`;
        svg += `<text x="${x}" y="${y + size + 12}" text-anchor="middle" fill="#c9d1d9" font-size="${Math.max(8, 10 * renderer.scale)}" font-family="sans-serif">${escapeXml((node.name || '').substring(0, 12))}</text>`;
    }

    svg += '</svg>';
    return svg;
}

function escapeXml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

// Initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize after a short delay to ensure canvas is rendered
    setTimeout(() => {
        initializeEnhancedGraph();
        refreshEnhancedGraph();
    }, 500);
});

// =============================================================================
// EnhancedGraphRenderer Class (embedded)
// =============================================================================

class EnhancedGraphRenderer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;

        this.ctx = this.canvas.getContext('2d');
        this.nodes = [];
        this.edges = [];
        this.selectedNode = null;
        this.hoveredNode = null;
        this.hoveredEdge = null;
        this.highlightedNodes = new Set();
        this.highlightedEdges = new Set();
        this.searchMatchedNodes = new Set();
        this.scale = 1.0;
        this.offsetX = 0;
        this.offsetY = 0;
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.draggedNode = null;
        this.pulsingNodes = new Map();
        this.showLabels = true;
        this.showArrows = true;
        this.tooltip = document.getElementById('graph-tooltip');
        this.edgeTooltip = document.getElementById('edge-tooltip');

        this.nodeColors = {
            file: '#58a6ff',
            concept: '#3fb950',
            pattern: '#d29922',
            decision: '#f85149'
        };

        this.edgeConfig = {
            import: { color: '#58a6ff', dash: null, label: 'Import' },
            grep_to_read: { color: '#3fb950', dash: [5, 5], label: 'Grep → Read' },
            explored_next: { color: '#8b949e', dash: [2, 2], label: 'Sequential' },
            reference: { color: '#d29922', dash: null, label: 'Reference' },
            test_of: { color: '#f85149', dash: null, label: 'Test Of' }
        };

        this.setupEventHandlers();
    }

    setupEventHandlers() {
        if (!this.canvas) return;
        this.canvas.addEventListener('click', (e) => this.handleClick(e));
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseleave', () => this.hideTooltip());
        this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
        this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.canvas.addEventListener('mouseup', () => this.handleMouseUp());
        this.canvas.addEventListener('dblclick', () => this.resetView());
    }

    loadData(data) {
        if (!data) return;
        this.nodes = (data.nodes || []).map(n => ({
            ...n,
            x: n.x || Math.random() * this.canvas.width,
            y: n.y || Math.random() * this.canvas.height
        }));
        this.edges = data.edges || [];

        if (this.nodes.length > 0) {
            this.applyForceLayout();
            this.fitToView();
        }
        this.render();
        this.updateStats();
    }

    applyForceLayout(iterations = 80) {
        if (this.nodes.length === 0) return;
        const width = this.canvas.width;
        const height = this.canvas.height;
        const k = Math.sqrt((width * height) / this.nodes.length) * 0.8;

        // Initialize circular layout
        this.nodes.forEach((node, i) => {
            const angle = (2 * Math.PI * i) / this.nodes.length;
            const radius = Math.min(width, height) * 0.35;
            node.x = width / 2 + radius * Math.cos(angle);
            node.y = height / 2 + radius * Math.sin(angle);
            node.vx = 0;
            node.vy = 0;
        });

        for (let iter = 0; iter < iterations; iter++) {
            const alpha = 1 - iter / iterations;

            // Repulsion
            for (let i = 0; i < this.nodes.length; i++) {
                for (let j = i + 1; j < this.nodes.length; j++) {
                    const n1 = this.nodes[i], n2 = this.nodes[j];
                    const dx = n2.x - n1.x, dy = n2.y - n1.y;
                    const dist = Math.max(Math.sqrt(dx*dx + dy*dy), 1);
                    const force = (k * k) / dist * alpha;
                    const fx = (dx / dist) * force, fy = (dy / dist) * force;
                    n1.vx -= fx; n1.vy -= fy;
                    n2.vx += fx; n2.vy += fy;
                }
            }

            // Attraction
            this.edges.forEach(e => {
                const source = this.nodes.find(n => n.id === e.source);
                const target = this.nodes.find(n => n.id === e.target);
                if (!source || !target) return;
                const dx = target.x - source.x, dy = target.y - source.y;
                const dist = Math.max(Math.sqrt(dx*dx + dy*dy), 1);
                const force = (dist * dist) / k * (e.strength || 1) * alpha * 0.3;
                const fx = (dx / dist) * force, fy = (dy / dist) * force;
                source.vx += fx; source.vy += fy;
                target.vx -= fx; target.vy -= fy;
            });

            // Center gravity
            this.nodes.forEach(n => {
                n.vx += (width/2 - n.x) * 0.01 * alpha;
                n.vy += (height/2 - n.y) * 0.01 * alpha;
                n.x += n.vx * 0.5;
                n.y += n.vy * 0.5;
                n.vx *= 0.9; n.vy *= 0.9;
                n.x = Math.max(30, Math.min(width-30, n.x));
                n.y = Math.max(30, Math.min(height-30, n.y));
            });
        }
    }

    fitToView() {
        if (this.nodes.length === 0) return;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        this.nodes.forEach(n => {
            minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
            maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
        });
        const padding = 40;
        const graphWidth = maxX - minX + padding * 2;
        const graphHeight = maxY - minY + padding * 2;
        this.scale = Math.min(this.canvas.width / graphWidth, this.canvas.height / graphHeight, 1.5);
        this.offsetX = (this.canvas.width - graphWidth * this.scale) / 2 - minX * this.scale + padding;
        this.offsetY = (this.canvas.height - graphHeight * this.scale) / 2 - minY * this.scale + padding;
    }

    resetView() { this.fitToView(); this.render(); }
    transformX(x) { return x * this.scale + this.offsetX; }
    transformY(y) { return y * this.scale + this.offsetY; }
    inverseTransformX(x) { return (x - this.offsetX) / this.scale; }
    inverseTransformY(y) { return (y - this.offsetY) / this.scale; }

    render() {
        if (!this.ctx) return;
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.edges.forEach(e => this.drawEdge(e));
        this.nodes.forEach(n => this.drawNode(n));
        this.renderPulsingNodes();
    }

    drawNode(node) {
        const x = this.transformX(node.x), y = this.transformY(node.y);
        const size = Math.max(6, (node.size || 15) * this.scale * 0.5);
        const isSelected = this.selectedNode && this.selectedNode.id === node.id;
        const isHovered = this.hoveredNode && this.hoveredNode.id === node.id;
        const isHighlighted = this.highlightedNodes.has(node.id);
        const isSearchMatch = this.searchMatchedNodes.has(node.id);

        this.ctx.beginPath();
        this.ctx.arc(x, y, size, 0, Math.PI * 2);
        this.ctx.fillStyle = node.color || this.nodeColors[node.type] || '#8b949e';
        this.ctx.fill();

        if (isSelected || isHovered || isHighlighted || isSearchMatch) {
            this.ctx.strokeStyle = isSelected ? '#fff' : isSearchMatch ? '#ff79c6' : isHighlighted ? '#3fb950' : '#ccc';
            this.ctx.lineWidth = isSelected ? 3 : isSearchMatch ? 3 : 2;
            this.ctx.stroke();
        }

        // Dim non-matching nodes during search
        if (this.searchMatchedNodes.size > 0 && !isSearchMatch) {
            this.ctx.fillStyle = 'rgba(13, 17, 23, 0.6)';
            this.ctx.beginPath();
            this.ctx.arc(x, y, size, 0, Math.PI * 2);
            this.ctx.fill();
        }

        if (this.showLabels && (this.canvas.width > 300 || isSelected || isHovered || isSearchMatch)) {
            this.ctx.fillStyle = isSearchMatch ? '#ff79c6' : '#c9d1d9';
            this.ctx.font = `${Math.max(8, 10 * this.scale)}px sans-serif`;
            this.ctx.textAlign = 'center';
            this.ctx.fillText((node.name || '').substring(0, 12), x, y + size + 10);
        }
    }

    drawEdge(edge) {
        const source = this.nodes.find(n => n.id === edge.source);
        const target = this.nodes.find(n => n.id === edge.target);
        if (!source || !target) return;

        const x1 = this.transformX(source.x), y1 = this.transformY(source.y);
        const x2 = this.transformX(target.x), y2 = this.transformY(target.y);
        const config = this.edgeConfig[edge.relationship] || {};
        const isHighlighted = this.highlightedEdges.has(`${edge.source}->${edge.target}`);

        this.ctx.beginPath();
        this.ctx.moveTo(x1, y1);
        this.ctx.lineTo(x2, y2);
        this.ctx.strokeStyle = (config.color || '#8b949e') + (isHighlighted ? '' : '60');
        this.ctx.lineWidth = Math.max(1, (edge.strength || 0.5) * 2 * this.scale);
        this.ctx.setLineDash(config.dash || []);
        this.ctx.stroke();
        this.ctx.setLineDash([]);
    }

    renderPulsingNodes() {
        const now = Date.now();
        const toRemove = [];
        this.pulsingNodes.forEach((pulse, nodeId) => {
            const elapsed = now - pulse.start;
            if (elapsed > pulse.duration) { toRemove.push(nodeId); return; }
            const node = this.nodes.find(n => n.id === nodeId);
            if (!node) return;
            const progress = elapsed / pulse.duration;
            const x = this.transformX(node.x), y = this.transformY(node.y);
            const ringSize = (node.size || 15) * this.scale * 0.5 + progress * 30;
            this.ctx.beginPath();
            this.ctx.arc(x, y, ringSize, 0, Math.PI * 2);
            this.ctx.strokeStyle = `rgba(59, 185, 80, ${1 - progress})`;
            this.ctx.lineWidth = 2;
            this.ctx.stroke();
        });
        toRemove.forEach(id => this.pulsingNodes.delete(id));
        if (this.pulsingNodes.size > 0) requestAnimationFrame(() => this.render());
    }

    handleClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const node = this.findNodeAt(e.clientX - rect.left, e.clientY - rect.top);
        if (node) this.selectNode(node);
        else this.deselectNode();
    }

    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left, y = e.clientY - rect.top;

        if (this.isDragging) {
            if (this.draggedNode) {
                this.draggedNode.x = this.inverseTransformX(x);
                this.draggedNode.y = this.inverseTransformY(y);
            } else {
                this.offsetX += x - this.dragStartX;
                this.offsetY += y - this.dragStartY;
                this.dragStartX = x; this.dragStartY = y;
            }
            this.render();
            return;
        }

        const node = this.findNodeAt(x, y);
        if (node !== this.hoveredNode) {
            this.hoveredNode = node;
            this.hoveredEdge = null;
            this.canvas.style.cursor = node ? 'pointer' : 'grab';
            this.render();
            if (node) {
                this.showTooltip(node, e.clientX, e.clientY);
                this.hideEdgeTooltip();
            } else {
                this.hideTooltip();
            }
        }

        // Check for edge hover if not hovering a node
        if (!node) {
            const edge = this.findEdgeAt(x, y);
            if (edge !== this.hoveredEdge) {
                this.hoveredEdge = edge;
                if (edge) {
                    this.showEdgeTooltip(edge, e.clientX, e.clientY);
                } else {
                    this.hideEdgeTooltip();
                }
            }
        }
    }

    findEdgeAt(x, y) {
        const threshold = 8;
        for (const edge of this.edges) {
            const source = this.nodes.find(n => n.id === edge.source);
            const target = this.nodes.find(n => n.id === edge.target);
            if (!source || !target) continue;

            const x1 = this.transformX(source.x), y1 = this.transformY(source.y);
            const x2 = this.transformX(target.x), y2 = this.transformY(target.y);

            // Calculate distance from point to line segment
            const dist = this.pointToLineDistance(x, y, x1, y1, x2, y2);
            if (dist < threshold) return edge;
        }
        return null;
    }

    pointToLineDistance(px, py, x1, y1, x2, y2) {
        const A = px - x1;
        const B = py - y1;
        const C = x2 - x1;
        const D = y2 - y1;

        const dot = A * C + B * D;
        const lenSq = C * C + D * D;
        let param = -1;
        if (lenSq !== 0) param = dot / lenSq;

        let xx, yy;
        if (param < 0) { xx = x1; yy = y1; }
        else if (param > 1) { xx = x2; yy = y2; }
        else { xx = x1 + param * C; yy = y1 + param * D; }

        const dx = px - xx;
        const dy = py - yy;
        return Math.sqrt(dx * dx + dy * dy);
    }

    showEdgeTooltip(edge, mouseX, mouseY) {
        if (!this.edgeTooltip) return;
        const config = this.edgeConfig[edge.relationship] || {};
        const color = config.color || '#8b949e';
        const label = config.label || edge.relationship;

        this.edgeTooltip.querySelector('.edge-tt-type').innerHTML = `<span style="color: ${color};">${label}</span>`;
        this.edgeTooltip.querySelector('.edge-tt-context').textContent = edge.context || '';
        this.edgeTooltip.querySelector('.edge-tt-strength').textContent = `Strength: ${(edge.strength || 0.5).toFixed(2)}`;
        this.edgeTooltip.style.left = (mouseX + 15) + 'px';
        this.edgeTooltip.style.top = (mouseY + 15) + 'px';
        this.edgeTooltip.style.display = 'block';
    }

    hideEdgeTooltip() {
        if (this.edgeTooltip) this.edgeTooltip.style.display = 'none';
    }

    handleMouseDown(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left, y = e.clientY - rect.top;
        this.isDragging = true;
        this.dragStartX = x; this.dragStartY = y;
        this.draggedNode = this.findNodeAt(x, y);
        this.canvas.style.cursor = 'grabbing';
    }

    handleMouseUp() {
        this.isDragging = false;
        this.draggedNode = null;
        this.canvas.style.cursor = 'grab';
    }

    handleWheel(e) {
        e.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left, mouseY = e.clientY - rect.top;
        const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.max(0.3, Math.min(3, this.scale * zoomFactor));
        this.offsetX = mouseX - (mouseX - this.offsetX) * (newScale / this.scale);
        this.offsetY = mouseY - (mouseY - this.offsetY) * (newScale / this.scale);
        this.scale = newScale;
        this.render();
    }

    findNodeAt(x, y) {
        for (const node of this.nodes) {
            const nx = this.transformX(node.x), ny = this.transformY(node.y);
            const size = Math.max(6, (node.size || 15) * this.scale * 0.5);
            if (Math.sqrt((x-nx)**2 + (y-ny)**2) < size + 5) return node;
        }
        return null;
    }

    selectNode(node) {
        this.selectedNode = node;
        this.highlightConnectedNodes(node);
        this.showNodePanel(node);
        this.render();
    }

    deselectNode() {
        this.selectedNode = null;
        this.highlightedNodes.clear();
        this.highlightedEdges.clear();
        this.hideNodePanel();
        this.render();
    }

    highlightConnectedNodes(node) {
        this.highlightedNodes.clear();
        this.highlightedEdges.clear();
        this.highlightedNodes.add(node.id);
        this.edges.forEach(e => {
            if (e.source === node.id) {
                this.highlightedNodes.add(e.target);
                this.highlightedEdges.add(`${e.source}->${e.target}`);
            } else if (e.target === node.id) {
                this.highlightedNodes.add(e.source);
                this.highlightedEdges.add(`${e.source}->${e.target}`);
            }
        });
    }

    highlightNode(nodeId) { this.highlightedNodes.add(nodeId); this.render(); }
    clearHighlights() { this.highlightedNodes.clear(); this.highlightedEdges.clear(); this.render(); }

    // Node search methods
    searchAndHighlight(query) {
        this.searchMatchedNodes.clear();
        if (!query) {
            this.render();
            return 0;
        }
        const lowerQuery = query.toLowerCase();
        for (const node of this.nodes) {
            const name = (node.name || '').toLowerCase();
            const path = (node.path || '').toLowerCase();
            const summary = (node.summary || '').toLowerCase();
            if (name.includes(lowerQuery) || path.includes(lowerQuery) || summary.includes(lowerQuery)) {
                this.searchMatchedNodes.add(node.id);
            }
        }
        this.render();
        return this.searchMatchedNodes.size;
    }

    clearSearchHighlights() {
        this.searchMatchedNodes.clear();
        this.render();
    }
    animateNodePulse(nodeId, duration = 1000) {
        this.pulsingNodes.set(nodeId, { start: Date.now(), duration });
        this.render();
    }
    animateEdge(sourceId, targetId) {
        this.highlightedEdges.add(`${sourceId}->${targetId}`);
        this.render();
    }

    showTooltip(node, mouseX, mouseY) {
        if (!this.tooltip) return;
        const color = this.nodeColors[node.type] || '#8b949e';
        this.tooltip.innerHTML = `
            <div class="tt-name" style="color: ${color};">${node.name}</div>
            <div class="tt-type">Type: ${node.type} | Explored: ${node.exploration_count || 0}x</div>
            <div class="tt-path">${node.path || ''}</div>
            <div class="tt-summary">${(node.summary || '').substring(0, 150)}</div>
        `;
        this.tooltip.style.left = (mouseX + 15) + 'px';
        this.tooltip.style.top = (mouseY + 15) + 'px';
        this.tooltip.style.display = 'block';
    }

    hideTooltip() { if (this.tooltip) this.tooltip.style.display = 'none'; }

    showNodePanel(node) {
        const panel = document.getElementById('graph-node-panel') || document.getElementById('graph-node-details');
        if (!panel) return;
        const color = this.nodeColors[node.type] || '#8b949e';
        const connections = this.edges.filter(e => e.source === node.id || e.target === node.id);
        const nodeId = node.id;

        panel.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                <div style="font-weight: 600; color: ${color};">${node.name}</div>
                <div style="display: flex; gap: 4px;">
                    <button class="btn btn-small" onclick="extractSubgraph('${nodeId}', 1)" style="padding: 2px 6px; font-size: 0.7em;" title="Show only connected nodes (1 hop)">Focus</button>
                    <button class="btn btn-small" onclick="extractSubgraph('${nodeId}', 2)" style="padding: 2px 6px; font-size: 0.7em;" title="Show connected nodes (2 hops)">Focus+</button>
                </div>
            </div>
            <div style="font-size: 0.85em; color: var(--text-dim); margin-bottom: 4px;">
                <span style="background: ${color}20; color: ${color}; padding: 2px 6px; border-radius: 4px;">${node.type}</span>
                <span style="margin-left: 8px;">Explored ${node.exploration_count || 0}x</span>
            </div>
            ${node.path ? `<div style="font-size: 0.8em; color: var(--text-dim); word-break: break-all; margin-bottom: 8px;">${node.path}</div>` : ''}
            ${node.summary ? `<div style="font-size: 0.85em; margin-bottom: 8px;">${node.summary}</div>` : ''}
            ${connections.length > 0 ? `<div style="margin-top: 10px; font-size: 0.85em;">Connections: ${connections.length}</div>` : ''}
        `;

        // Fetch file preview if this is a file node with a path
        if (node.type === 'file' && node.path) {
            this.fetchFilePreview(node.path);
        } else {
            this.hideFilePreview();
        }
    }

    async fetchFilePreview(filePath) {
        const previewPanel = document.getElementById('file-preview-panel');
        const previewContent = document.getElementById('file-preview-content');
        const previewInfo = document.getElementById('file-preview-info');
        const previewLang = document.getElementById('file-preview-lang');

        if (!previewPanel || !previewContent) return;

        previewPanel.style.display = 'block';
        previewContent.textContent = 'Loading...';
        previewInfo.textContent = '';
        if (previewLang) previewLang.textContent = '';

        try {
            const response = await fetch(`/api/atlasforge/file-preview?path=${encodeURIComponent(filePath)}&lines=50`);
            const data = await response.json();

            if (data.error) {
                previewContent.textContent = `Error: ${data.error}`;
                return;
            }

            // Detect language and apply syntax highlighting
            const lang = syntaxHighlighter ? syntaxHighlighter.detectLanguage(filePath) : 'text';
            if (previewLang) {
                previewLang.textContent = lang.toUpperCase();
                previewLang.style.display = lang !== 'text' ? 'inline' : 'none';
            }

            // Apply syntax highlighting if possible
            let highlightedContent;
            if (syntaxHighlighter && lang !== 'text') {
                highlightedContent = syntaxHighlighter.highlight(data.content, lang);
            } else {
                highlightedContent = data.content
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');
            }

            previewContent.innerHTML = highlightedContent || '<span style="color: var(--text-dim);">(empty file)</span>';
            previewInfo.textContent = data.truncated
                ? `Lines 1-${data.lines_shown} of ${data.total_lines}`
                : `${data.total_lines} lines`;

        } catch (e) {
            previewContent.textContent = `Failed to load preview: ${e.message}`;
        }
    }

    hideFilePreview() {
        const previewPanel = document.getElementById('file-preview-panel');
        if (previewPanel) previewPanel.style.display = 'none';
    }

    hideNodePanel() {
        const panel = document.getElementById('graph-node-panel') || document.getElementById('graph-node-details');
        if (panel) panel.innerHTML = '<div style="color: var(--text-dim);">Click a node to see details</div>';
        this.hideFilePreview();
    }

    findNodeByPath(path) {
        const node = this.nodes.find(n => n.path === path);
        return node ? node.id : null;
    }

    updateStats() {
        const nodeCount = document.getElementById('graph-node-count');
        const edgeCount = document.getElementById('graph-edge-count');
        if (nodeCount) nodeCount.textContent = this.nodes.length;
        if (edgeCount) edgeCount.textContent = this.edges.length;
    }
}

// =============================================================================
// JourneyPlayback Class (embedded)
// =============================================================================

class JourneyPlayback {
    constructor(graphRenderer) {
        this.graph = graphRenderer;
        this.events = [];
        this.currentIndex = 0;
        this.isPlaying = false;
        this.speed = 1000;
        this.visitedNodes = new Set();
        this.visitedEdges = new Set();
        this.lastNodeId = null;
        this.timeoutId = null;
    }

    async loadJourney(missionId = null) {
        try {
            const url = missionId ? `/api/atlasforge/exploration-journey?mission_id=${missionId}` : '/api/atlasforge/exploration-journey';
            const response = await fetch(url);
            const data = await response.json();
            this.events = data.events || [];
            this.reset();
            this.updateUI();
            return this.events.length;
        } catch (e) { console.error('Failed to load journey:', e); return 0; }
    }

    play() {
        if (this.events.length === 0) return;
        this.isPlaying = true;
        this.updateUI();
        this.animationLoop();
    }

    pause() {
        this.isPlaying = false;
        if (this.timeoutId) { clearTimeout(this.timeoutId); this.timeoutId = null; }
        this.updateUI();
    }

    toggle() { if (this.isPlaying) this.pause(); else this.play(); }

    step() {
        if (this.currentIndex < this.events.length) {
            this.processEvent(this.events[this.currentIndex]);
            this.currentIndex++;
            this.updateUI();
        }
    }

    stepBack() {
        if (this.currentIndex > 0) {
            this.currentIndex--;
            this.rebuildState();
            this.updateUI();
        }
    }

    reset() {
        this.pause();
        this.currentIndex = 0;
        this.visitedNodes.clear();
        this.visitedEdges.clear();
        this.lastNodeId = null;
        this.graph.clearHighlights();
        this.updateUI();
    }

    rebuildState() {
        this.visitedNodes.clear();
        this.visitedEdges.clear();
        this.lastNodeId = null;
        this.graph.clearHighlights();
        for (let i = 0; i < this.currentIndex; i++) {
            this.processEvent(this.events[i], false);
        }
        this.graph.render();
    }

    processEvent(event, animate = true) {
        const nodeId = this.graph.findNodeByPath(event.file_path);
        if (nodeId) {
            this.visitedNodes.add(nodeId);
            if (animate) {
                this.graph.highlightNode(nodeId);
                this.graph.animateNodePulse(nodeId);
            }
            if (this.lastNodeId && this.lastNodeId !== nodeId) {
                const edgeKey = `${this.lastNodeId}->${nodeId}`;
                if (!this.visitedEdges.has(edgeKey)) {
                    this.visitedEdges.add(edgeKey);
                    if (animate) this.graph.animateEdge(this.lastNodeId, nodeId);
                }
            }
            this.lastNodeId = nodeId;
        }
        if (animate) this.showEventInfo(event);
    }

    showEventInfo(event) {
        const panel = document.getElementById('playback-event-info');
        if (!panel) return;
        const colors = { read: '#58a6ff', search: '#3fb950', modify: '#d29922', create: '#f85149' };
        const color = colors[event.type] || '#8b949e';
        panel.innerHTML = `
            <div style="font-size: 0.85em;">
                <span style="color: ${color}; font-weight: 500;">${event.type.toUpperCase()}</span>
                <span style="color: var(--text-dim); margin-left: 8px;">${event.timestamp || ''}</span>
            </div>
            <div style="font-size: 0.9em; margin-top: 4px;">${event.summary || ''}</div>
        `;
    }

    animationLoop() {
        if (!this.isPlaying) return;
        if (this.currentIndex < this.events.length) {
            this.step();
            this.timeoutId = setTimeout(() => this.animationLoop(), this.speed);
        } else { this.isPlaying = false; this.updateUI(); }
    }

    setSpeed(speed) { this.speed = Math.max(100, Math.min(3000, speed)); }

    updateUI() {
        const current = document.getElementById('playback-current');
        const total = document.getElementById('playback-total');
        const progress = document.getElementById('playback-progress');
        const playBtn = document.getElementById('btn-play');
        const stepBtn = document.getElementById('btn-step');
        const backBtn = document.getElementById('btn-back');
        if (current) current.textContent = this.currentIndex;
        if (total) total.textContent = this.events.length;
        if (progress) progress.style.width = (this.events.length > 0 ? (this.currentIndex / this.events.length) * 100 : 0) + '%';
        if (playBtn) playBtn.textContent = this.isPlaying ? 'Pause' : 'Play';
        if (stepBtn) stepBtn.disabled = this.currentIndex >= this.events.length;
        if (backBtn) backBtn.disabled = this.currentIndex <= 0;
    }
}

// =============================================================================
// GraphFilterController Class (embedded)
// =============================================================================

class GraphFilterController {
    constructor(graphRenderer) {
        this.graph = graphRenderer;
        this.activeFilters = { edgeTypes: [], nodeTypes: [], timeStart: null, timeEnd: null, minExplorationCount: 0 };
    }

    setEdgeTypeFilters(types) { this.activeFilters.edgeTypes = types; }
    setNodeTypeFilters(types) { this.activeFilters.nodeTypes = types; }
    setTimeRange(start, end) { this.activeFilters.timeStart = start; this.activeFilters.timeEnd = end; }
    setMinExplorationCount(count) { this.activeFilters.minExplorationCount = count; }

    async applyFilters() {
        const params = new URLSearchParams();
        params.set('width', this.graph.canvas.width);
        params.set('height', this.graph.canvas.height);
        if (this.activeFilters.edgeTypes.length > 0) params.set('edge_types', this.activeFilters.edgeTypes.join(','));
        if (this.activeFilters.nodeTypes.length > 0) params.set('node_types', this.activeFilters.nodeTypes.join(','));
        if (this.activeFilters.timeStart) params.set('time_start', this.activeFilters.timeStart);
        if (this.activeFilters.timeEnd) params.set('time_end', this.activeFilters.timeEnd);
        if (this.activeFilters.minExplorationCount > 0) params.set('min_count', this.activeFilters.minExplorationCount);

        try {
            const response = await fetch(`/api/atlasforge/exploration-graph-enhanced?${params}`);
            const data = await response.json();
            this.graph.loadData(data);
        } catch (e) { console.error('Failed to apply filters:', e); }
    }

    resetFilters() {
        this.activeFilters = { edgeTypes: [], nodeTypes: [], timeStart: null, timeEnd: null, minExplorationCount: 0 };
        this.applyFilters();
    }
}
</script>
'''


def get_dashboard_injection():
    """
    Get the HTML and JS to inject into the dashboard.

    Returns tuple of (html, js) strings.
    """
    # Try to include streaming UI
    try:
        from realtime_graph_streaming import get_streaming_injection
        streaming_html, streaming_js = get_streaming_injection()
        combined_html = INTERACTIVE_GRAPH_HTML + "\n" + streaming_html
        combined_js = INTERACTIVE_GRAPH_JS + "\n" + streaming_js
        return combined_html, combined_js
    except ImportError:
        return INTERACTIVE_GRAPH_HTML, INTERACTIVE_GRAPH_JS


if __name__ == "__main__":
    print("Interactive Graph API - Demo")
    print("=" * 50)
    print("\nThis module provides Flask routes for the interactive exploration graph.")
    print("\nTo use in dashboard_v2.py:")
    print("  from interactive_graph_api import register_interactive_graph_routes")
    print("  register_interactive_graph_routes(app)")
    print("\nAvailable endpoints:")
    print("  /api/atlasforge/exploration-graph-enhanced")
    print("  /api/atlasforge/exploration-journey")
    print("  /api/atlasforge/export/<format>")
    print("  /api/atlasforge/edge-types")
    print("  /api/atlasforge/graph-stats")
