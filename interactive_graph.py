#!/usr/bin/env python3
"""
Interactive Exploration Graph - Enhanced visualization module.

This module extends the ExplorationGraph with:
1. Edge detection integration
2. Filtering by edge type, time window, node type
3. Code journey playback data generation
4. Export to DOT and GraphML formats

Part of the Interactive Exploration Graph Enhancement mission.
"""

import ast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Any

# Try to import networkx for GraphML export
try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False


# =============================================================================
# EDGE TYPE DEFINITIONS
# =============================================================================

EDGE_TYPES = {
    'import': {
        'color': '#58a6ff',      # blue
        'style': 'solid',
        'description': 'Python import statement',
        'directed': True
    },
    'grep_to_read': {
        'color': '#3fb950',      # green
        'style': 'dashed',
        'description': 'Grep result led to file read',
        'directed': True
    },
    'explored_next': {
        'color': '#8b949e',      # gray
        'style': 'dotted',
        'description': 'Sequential exploration',
        'directed': True
    },
    'reference': {
        'color': '#d29922',      # orange
        'style': 'solid',
        'description': 'File path reference in content',
        'directed': True
    },
    'test_of': {
        'color': '#f85149',      # red
        'style': 'solid',
        'description': 'Test file for source',
        'directed': True
    }
}

NODE_TYPES = {
    'file': {'color': '#58a6ff', 'shape': 'circle'},
    'concept': {'color': '#3fb950', 'shape': 'diamond'},
    'pattern': {'color': '#d29922', 'shape': 'triangle'},
    'decision': {'color': '#f85149', 'shape': 'square'}
}


# =============================================================================
# FILTERING
# =============================================================================

@dataclass
class GraphFilter:
    """Filter configuration for exploration graph."""
    edge_types: Optional[List[str]] = None       # Filter by edge relationship type
    node_types: Optional[List[str]] = None       # Filter by node type
    time_start: Optional[datetime] = None        # Filter by exploration time
    time_end: Optional[datetime] = None
    min_exploration_count: int = 0               # Minimum times explored
    path_pattern: Optional[str] = None           # Regex pattern for file paths


def filter_graph_data(
    nodes: List[Dict],
    edges: List[Dict],
    filter_config: GraphFilter
) -> Tuple[List[Dict], List[Dict]]:
    """
    Apply filters to graph data.

    Returns filtered (nodes, edges).
    """
    filtered_nodes = nodes
    filtered_edges = edges

    # Filter nodes by type
    if filter_config.node_types:
        filtered_nodes = [
            n for n in filtered_nodes
            if n.get('type') in filter_config.node_types
        ]

    # Filter nodes by exploration count
    if filter_config.min_exploration_count > 0:
        filtered_nodes = [
            n for n in filtered_nodes
            if n.get('exploration_count', 0) >= filter_config.min_exploration_count
        ]

    # Filter nodes by time
    if filter_config.time_start or filter_config.time_end:
        def in_time_range(node):
            try:
                explored = datetime.fromisoformat(node.get('last_explored', ''))
                if filter_config.time_start and explored < filter_config.time_start:
                    return False
                if filter_config.time_end and explored > filter_config.time_end:
                    return False
                return True
            except (ValueError, TypeError):
                return True
        filtered_nodes = [n for n in filtered_nodes if in_time_range(n)]

    # Filter nodes by path pattern
    if filter_config.path_pattern:
        try:
            pattern = re.compile(filter_config.path_pattern)
            filtered_nodes = [
                n for n in filtered_nodes
                if not n.get('path') or pattern.search(n['path'])
            ]
        except re.error:
            pass

    # Get remaining node IDs
    remaining_node_ids = {n['id'] for n in filtered_nodes}

    # Filter edges by type
    if filter_config.edge_types:
        filtered_edges = [
            e for e in filtered_edges
            if e.get('relationship') in filter_config.edge_types
        ]

    # Filter edges to only include those between remaining nodes
    filtered_edges = [
        e for e in filtered_edges
        if e.get('source') in remaining_node_ids and e.get('target') in remaining_node_ids
    ]

    return filtered_nodes, filtered_edges


def extract_subgraph(
    nodes: List[Dict],
    edges: List[Dict],
    center_node_id: str,
    depth: int = 1
) -> Tuple[List[Dict], List[Dict]]:
    """
    Extract a subgraph centered on a specific node.

    Returns all nodes connected to the center node (directly or up to `depth` hops)
    and all edges between them.

    Args:
        nodes: All nodes in the graph
        edges: All edges in the graph
        center_node_id: The node to center the subgraph on
        depth: How many hops away to include (1 = immediate neighbors only)

    Returns:
        Tuple of (filtered_nodes, filtered_edges)
    """
    if depth < 1:
        depth = 1

    # Build adjacency maps
    outgoing = {}  # node_id -> list of connected node_ids
    incoming = {}  # node_id -> list of connected node_ids

    for edge in edges:
        source = edge.get('source')
        target = edge.get('target')

        if source:
            if source not in outgoing:
                outgoing[source] = []
            outgoing[source].append(target)

        if target:
            if target not in incoming:
                incoming[target] = []
            incoming[target].append(source)

    # BFS to find all nodes within depth
    included_nodes = {center_node_id}
    frontier = {center_node_id}

    for _ in range(depth):
        next_frontier = set()
        for node_id in frontier:
            # Add all connected nodes
            for neighbor in outgoing.get(node_id, []):
                if neighbor not in included_nodes:
                    next_frontier.add(neighbor)
                    included_nodes.add(neighbor)
            for neighbor in incoming.get(node_id, []):
                if neighbor not in included_nodes:
                    next_frontier.add(neighbor)
                    included_nodes.add(neighbor)
        frontier = next_frontier
        if not frontier:
            break

    # Filter nodes
    filtered_nodes = [n for n in nodes if n.get('id') in included_nodes]

    # Filter edges - only include edges where both endpoints are in the subgraph
    filtered_edges = [
        e for e in edges
        if e.get('source') in included_nodes and e.get('target') in included_nodes
    ]

    return filtered_nodes, filtered_edges


# =============================================================================
# CODE JOURNEY PLAYBACK
# =============================================================================

@dataclass
class JourneyEvent:
    """A single event in the code exploration journey."""
    sequence: int
    timestamp: str
    event_type: str  # 'read', 'search', 'modify', 'create'
    file_path: Optional[str]
    pattern: Optional[str]  # For grep events
    summary: str
    tool_name: str


def generate_journey_events(
    mission_id: str,
    decision_logger=None
) -> List[Dict]:
    """
    Generate chronologically ordered exploration events for playback.

    Uses decision graph data if available.
    """
    events = []

    if decision_logger is None:
        # Try to import decision graph
        try:
            from decision_graph import get_decision_logger
            decision_logger = get_decision_logger()
        except ImportError:
            return events

    try:
        # Get invocations for the mission
        invocations = decision_logger.get_invocations(mission_id, limit=1000)

        for i, inv in enumerate(invocations):
            tool_name = inv.get('tool_name', '') if isinstance(inv, dict) else getattr(inv, 'tool_name', '')
            input_summary = inv.get('input_summary', {}) if isinstance(inv, dict) else getattr(inv, 'input_summary', {})
            timestamp = inv.get('timestamp', '') if isinstance(inv, dict) else getattr(inv, 'timestamp', '')

            event = None

            if tool_name == "Read":
                file_path = input_summary.get("file_path", "")
                if file_path:
                    event = {
                        "sequence": i,
                        "timestamp": timestamp,
                        "type": "read",
                        "file_path": file_path,
                        "summary": f"Read {Path(file_path).name}",
                        "tool_name": tool_name
                    }

            elif tool_name == "Grep":
                pattern = input_summary.get("pattern", "")
                event = {
                    "sequence": i,
                    "timestamp": timestamp,
                    "type": "search",
                    "pattern": pattern,
                    "summary": f"Searched for '{pattern[:30]}'",
                    "tool_name": tool_name
                }

            elif tool_name == "Write":
                file_path = input_summary.get("file_path", "")
                if file_path:
                    event = {
                        "sequence": i,
                        "timestamp": timestamp,
                        "type": "create",
                        "file_path": file_path,
                        "summary": f"Created {Path(file_path).name}",
                        "tool_name": tool_name
                    }

            elif tool_name == "Edit":
                file_path = input_summary.get("file_path", "")
                if file_path:
                    event = {
                        "sequence": i,
                        "timestamp": timestamp,
                        "type": "modify",
                        "file_path": file_path,
                        "summary": f"Modified {Path(file_path).name}",
                        "tool_name": tool_name
                    }

            elif tool_name == "Glob":
                pattern = input_summary.get("pattern", "")
                event = {
                    "sequence": i,
                    "timestamp": timestamp,
                    "type": "search",
                    "pattern": pattern,
                    "summary": f"Globbed for '{pattern}'",
                    "tool_name": tool_name
                }

            if event:
                events.append(event)

    except Exception as e:
        print(f"Error generating journey events: {e}")

    return events


def generate_journey_from_graph(graph) -> List[Dict]:
    """
    Generate journey events from exploration graph nodes.

    Fallback when decision graph is not available.
    """
    events = []

    # Get nodes sorted by first_explored timestamp
    nodes = sorted(
        graph.nodes.values(),
        key=lambda n: n.first_explored
    )

    for i, node in enumerate(nodes):
        if node.node_type == 'file' and node.path:
            events.append({
                "sequence": i,
                "timestamp": node.first_explored,
                "type": "read",
                "file_path": node.path,
                "summary": f"Explored {node.name}",
                "tool_name": "Read"
            })
        elif node.node_type == 'concept':
            events.append({
                "sequence": i,
                "timestamp": node.first_explored,
                "type": "discover",
                "concept": node.name,
                "summary": f"Discovered concept: {node.name}",
                "tool_name": "N/A"
            })

    return events


# =============================================================================
# EXPORT FUNCTIONS
# =============================================================================

def export_to_dot(
    nodes: List[Dict],
    edges: List[Dict],
    graph_name: str = "ExplorationGraph"
) -> str:
    """
    Export graph to DOT format (Graphviz).

    Returns DOT string.
    """
    lines = [f'digraph {graph_name} {{']
    lines.append('  rankdir=LR;')
    lines.append('  node [shape=box, fontname="Arial"];')
    lines.append('  edge [fontname="Arial", fontsize=10];')
    lines.append('')

    # Node color mapping
    type_colors = {
        'file': '#58a6ff',
        'concept': '#3fb950',
        'pattern': '#d29922',
        'decision': '#f85149'
    }

    # Edge style mapping
    edge_styles = {
        'import': 'solid',
        'grep_to_read': 'dashed',
        'explored_next': 'dotted',
        'reference': 'solid',
        'test_of': 'bold'
    }

    edge_colors = {
        'import': '#58a6ff',
        'grep_to_read': '#3fb950',
        'explored_next': '#8b949e',
        'reference': '#d29922',
        'test_of': '#f85149'
    }

    # Write nodes
    for node in nodes:
        node_id = node.get('id', '') or ''
        # Handle explicit None values for name - fallback to node_id
        node_name = node.get('name')
        label = (node_name if node_name is not None else node_id) or node_id or 'unknown'
        label = str(label)[:30].replace('"', '\\"')
        node_type = node.get('type', 'file')
        color = type_colors.get(node_type, '#8b949e')

        # Escape special characters
        label = label.replace('\\', '\\\\').replace('"', '\\"')

        attrs = [
            f'label="{label}"',
            f'style="filled"',
            f'fillcolor="{color}"',
            f'fontcolor="white"'
        ]

        if node.get('path'):
            tooltip = node['path'].replace('"', '\\"')
            attrs.append(f'tooltip="{tooltip}"')

        lines.append(f'  "{node_id}" [{", ".join(attrs)}];')

    lines.append('')

    # Write edges
    for edge in edges:
        source = edge.get('source', '')
        target = edge.get('target', '')
        relationship = edge.get('relationship', 'related')
        strength = edge.get('strength', 1.0)

        style = edge_styles.get(relationship, 'solid')
        color = edge_colors.get(relationship, '#8b949e')

        attrs = [
            f'label="{relationship}"',
            f'style="{style}"',
            f'color="{color}"',
            f'penwidth="{max(1, strength * 2)}"'
        ]

        lines.append(f'  "{source}" -> "{target}" [{", ".join(attrs)}];')

    lines.append('}')
    return '\n'.join(lines)


def export_to_graphml(
    nodes: List[Dict],
    edges: List[Dict]
) -> str:
    """
    Export graph to GraphML format.

    Uses NetworkX if available, otherwise generates XML manually.
    """
    if NETWORKX_AVAILABLE:
        return _export_graphml_networkx(nodes, edges)
    else:
        return _export_graphml_manual(nodes, edges)


def _export_graphml_networkx(nodes: List[Dict], edges: List[Dict]) -> str:
    """Export using NetworkX library."""
    G = nx.DiGraph()

    # Helper to ensure values are GraphML-compatible (no None values)
    def safe_str(val, default=''):
        if val is None:
            return default
        return str(val)

    def safe_int(val, default=0):
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def safe_float(val, default=1.0):
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # Add nodes with attributes
    for node in nodes:
        node_id = safe_str(node.get('id'), 'unknown')
        G.add_node(
            node_id,
            label=safe_str(node.get('name'), node_id),
            node_type=safe_str(node.get('type'), 'file'),
            path=safe_str(node.get('path'), ''),
            summary=safe_str(node.get('summary'), '')[:500],
            exploration_count=safe_int(node.get('exploration_count'), 0),
            first_explored=safe_str(node.get('first_explored'), ''),
            last_explored=safe_str(node.get('last_explored'), '')
        )

    # Add edges with attributes
    for edge in edges:
        G.add_edge(
            safe_str(edge.get('source'), ''),
            safe_str(edge.get('target'), ''),
            relationship=safe_str(edge.get('relationship'), 'related'),
            strength=safe_float(edge.get('strength'), 1.0),
            context=safe_str(edge.get('context'), '')[:200]
        )

    # Write to string
    from io import BytesIO
    buffer = BytesIO()
    nx.write_graphml(G, buffer)
    return buffer.getvalue().decode('utf-8')


def _export_graphml_manual(nodes: List[Dict], edges: List[Dict]) -> str:
    """Export GraphML without NetworkX dependency."""
    def escape_xml(s):
        if s is None:
            return ''
        return (str(s)
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&apos;'))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <!-- Node attributes -->',
        '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="node_type" for="node" attr.name="node_type" attr.type="string"/>',
        '  <key id="path" for="node" attr.name="path" attr.type="string"/>',
        '  <key id="summary" for="node" attr.name="summary" attr.type="string"/>',
        '  <key id="exploration_count" for="node" attr.name="exploration_count" attr.type="int"/>',
        '  <!-- Edge attributes -->',
        '  <key id="relationship" for="edge" attr.name="relationship" attr.type="string"/>',
        '  <key id="strength" for="edge" attr.name="strength" attr.type="double"/>',
        '  <key id="context" for="edge" attr.name="context" attr.type="string"/>',
        '  <graph id="ExplorationGraph" edgedefault="directed">'
    ]

    # Nodes
    for node in nodes:
        node_id = escape_xml(node.get('id', ''))
        lines.append(f'    <node id="{node_id}">')
        lines.append(f'      <data key="label">{escape_xml(node.get("name", ""))}</data>')
        lines.append(f'      <data key="node_type">{escape_xml(node.get("type", "file"))}</data>')
        lines.append(f'      <data key="path">{escape_xml(node.get("path", ""))}</data>')
        lines.append(f'      <data key="summary">{escape_xml(node.get("summary", "")[:500])}</data>')
        lines.append(f'      <data key="exploration_count">{node.get("exploration_count", 0)}</data>')
        lines.append('    </node>')

    # Edges
    for i, edge in enumerate(edges):
        source = escape_xml(edge.get('source', ''))
        target = escape_xml(edge.get('target', ''))
        lines.append(f'    <edge id="e{i}" source="{source}" target="{target}">')
        lines.append(f'      <data key="relationship">{escape_xml(edge.get("relationship", ""))}</data>')
        lines.append(f'      <data key="strength">{edge.get("strength", 1.0)}</data>')
        lines.append(f'      <data key="context">{escape_xml(edge.get("context", "")[:200])}</data>')
        lines.append('    </edge>')

    lines.extend([
        '  </graph>',
        '</graphml>'
    ])

    return '\n'.join(lines)


# =============================================================================
# IMPORT ANALYSIS
# =============================================================================

def analyze_python_imports(file_path: str, content: str) -> List[str]:
    """
    Extract import statements from Python code using AST.

    Returns list of imported module names.
    """
    imports = []

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
            # Also capture individual imports: from X import Y
            for alias in node.names:
                if node.module:
                    imports.append(f"{node.module}.{alias.name}")

    return imports


def resolve_import_to_file(
    import_name: str,
    source_file: str,
    explored_files: Set[str]
) -> Optional[str]:
    """
    Try to resolve an import name to a file path.

    Checks if the resolved path is in the set of explored files.
    """
    # Convert module.submodule to path
    parts = import_name.split('.')
    source_dir = Path(source_file).parent

    # Try different resolution strategies
    candidates = []

    # Direct file: module.py
    candidates.append(source_dir / (parts[0] + '.py'))

    # Nested: module/submodule.py
    if len(parts) > 1:
        candidates.append(source_dir / '/'.join(parts[:-1]) / (parts[-1] + '.py'))
        candidates.append(source_dir / '/'.join(parts) / '__init__.py')

    # Package: module/__init__.py
    candidates.append(source_dir / parts[0] / '__init__.py')

    # Try parent directories
    for parent in [source_dir.parent, source_dir.parent.parent]:
        candidates.append(parent / (parts[0] + '.py'))
        candidates.append(parent / '/'.join(parts) + '.py')

    # Check which candidates are in explored files
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in explored_files:
            return candidate_str

        # Try normalized paths
        try:
            resolved = str(candidate.resolve())
            if resolved in explored_files:
                return resolved
        except (OSError, ValueError):
            pass

    return None


def extract_file_references(content: str, source_file: str) -> List[str]:
    """
    Extract file path references from content.

    Looks for:
    - String literals that look like paths
    - open() calls
    - Path() constructors
    """
    references = []

    # Patterns to match file paths
    patterns = [
        r'["\']([/\w.-]+\.(?:py|js|ts|json|yaml|yml|md|txt|sh|html|css))["\']',
        r'open\s*\(\s*["\']([^"\']+)["\']',
        r'Path\s*\(\s*["\']([^"\']+)["\']',
        r'with\s+open\s*\(\s*["\']([^"\']+)["\']',
        r'import\s+([^\s]+)\s+from\s+["\']([^"\']+)["\']',  # JS imports
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            if isinstance(match, tuple):
                references.extend(m for m in match if m)
            else:
                references.append(match)

    # Normalize paths relative to source file
    source_dir = Path(source_file).parent
    normalized = []

    for ref in references:
        if ref.startswith('/'):
            normalized.append(ref)
        else:
            full_path = str(source_dir / ref)
            normalized.append(full_path)

    return list(set(normalized))


# =============================================================================
# D3.JS VISUALIZATION DATA
# =============================================================================

def generate_d3_visualization_data(
    nodes: List[Dict],
    edges: List[Dict],
    width: float = 800,
    height: float = 600
) -> Dict:
    """
    Generate data optimized for D3.js force-directed visualization.

    Returns dict with nodes, edges, and visualization metadata.
    """
    # Calculate node sizes based on exploration count
    max_count = max((n.get('exploration_count', 1) for n in nodes), default=1)

    viz_nodes = []
    for node in nodes:
        count = node.get('exploration_count', 1)
        # Scale size: min 10, max 40
        size = 10 + (count / max(max_count, 1)) * 30

        viz_nodes.append({
            'id': node.get('id'),
            'name': node.get('name'),
            'type': node.get('type', 'file'),
            'path': node.get('path'),
            'summary': node.get('summary', '')[:200],
            'exploration_count': count,
            'tags': node.get('tags', [])[:5],
            'size': size,
            'color': NODE_TYPES.get(node.get('type', 'file'), {}).get('color', '#8b949e')
        })

    viz_edges = []
    for edge in edges:
        edge_type = edge.get('relationship', 'related')
        edge_config = EDGE_TYPES.get(edge_type, {})

        viz_edges.append({
            'source': edge.get('source'),
            'target': edge.get('target'),
            'relationship': edge_type,
            'strength': edge.get('strength', 1.0),
            'context': edge.get('context', ''),
            'color': edge_config.get('color', '#8b949e'),
            'style': edge_config.get('style', 'solid'),
            'directed': edge_config.get('directed', True)
        })

    return {
        'nodes': viz_nodes,
        'edges': viz_edges,
        'metadata': {
            'width': width,
            'height': height,
            'node_types': NODE_TYPES,
            'edge_types': EDGE_TYPES,
            'total_nodes': len(viz_nodes),
            'total_edges': len(viz_edges),
            'generated_at': datetime.now().isoformat()
        }
    }


# =============================================================================
# INTEGRATION WITH EXPLORATION GRAPH
# =============================================================================

def enhance_graph_with_edges(graph, detector=None):
    """
    Enhance an ExplorationGraph with detected edges.

    Analyzes file nodes for imports and references.
    """
    if detector is None:
        try:
            from edge_detector import EdgeDetector
            detector = EdgeDetector()
        except ImportError:
            return

    # Sync explored files
    explored = set()
    for node in graph.nodes.values():
        if node.path:
            explored.add(node.path)
    detector.set_explored_files(explored)

    # For each file node, try to detect edges
    # (This requires file content which may not be available)
    # The edge detection is primarily done at read-time via hooks

    pass


def get_filtered_visualization_data(
    graph,
    width: float = 800,
    height: float = 600,
    edge_types: Optional[List[str]] = None,
    node_types: Optional[List[str]] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    min_exploration_count: int = 0
) -> Dict:
    """
    Get visualization data with filtering applied.
    """
    # Get base visualization data
    base_data = graph.export_for_visualization(width, height)

    # Parse time filters
    ts_start = None
    ts_end = None
    if time_start:
        try:
            ts_start = datetime.fromisoformat(time_start)
        except ValueError:
            pass
    if time_end:
        try:
            ts_end = datetime.fromisoformat(time_end)
        except ValueError:
            pass

    # Create filter config
    filter_config = GraphFilter(
        edge_types=edge_types,
        node_types=node_types,
        time_start=ts_start,
        time_end=ts_end,
        min_exploration_count=min_exploration_count
    )

    # Apply filters
    filtered_nodes, filtered_edges = filter_graph_data(
        base_data.get('nodes', []),
        base_data.get('edges', []),
        filter_config
    )

    # Generate D3 visualization data
    result = generate_d3_visualization_data(filtered_nodes, filtered_edges, width, height)

    # Add filter info
    result['filters_applied'] = {
        'edge_types': edge_types,
        'node_types': node_types,
        'time_start': time_start,
        'time_end': time_end,
        'min_exploration_count': min_exploration_count
    }

    return result


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Interactive Exploration Graph - Demo")
    print("=" * 50)

    # Test DOT export
    test_nodes = [
        {'id': 'n1', 'name': 'api.py', 'type': 'file', 'path': '/src/api.py'},
        {'id': 'n2', 'name': 'models.py', 'type': 'file', 'path': '/src/models.py'},
        {'id': 'n3', 'name': 'Auth Concept', 'type': 'concept', 'path': None}
    ]

    test_edges = [
        {'source': 'n1', 'target': 'n2', 'relationship': 'import', 'strength': 1.0},
        {'source': 'n1', 'target': 'n3', 'relationship': 'reference', 'strength': 0.7}
    ]

    print("\n--- DOT Export ---")
    dot_output = export_to_dot(test_nodes, test_edges)
    print(dot_output[:500] + "...")

    print("\n--- GraphML Export ---")
    graphml_output = export_to_graphml(test_nodes, test_edges)
    print(graphml_output[:500] + "...")

    print("\n--- D3 Visualization Data ---")
    viz_data = generate_d3_visualization_data(test_nodes, test_edges)
    print(json.dumps(viz_data['metadata'], indent=2))

    print("\n--- Import Analysis ---")
    test_content = """
import os
import sys
from pathlib import Path
from src.models import User, Session
from .utils import helper_function
"""
    imports = analyze_python_imports('/src/api.py', test_content)
    print(f"Imports found: {imports}")

    print("\nDemo complete!")
