#!/usr/bin/env python3
"""
Real-Time WebSocket Streaming for Exploration Graph

This module provides real-time streaming of exploration graph updates
via WebSockets. Features include:

1. WebSocket endpoint that pushes node/edge additions in real-time
2. Smooth D3 animation support with physics-based spring transitions
3. Edge animation showing exploration flow direction
4. Live statistics overlay (nodes/min, coverage, depth)
5. Audio feedback hooks for new discoveries
6. Time-lapse recording that can be exported as video

Part of the Interactive Exploration Graph Enhancement mission.
"""

import json
import time
import threading
import queue
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Callable, Any, Set
from pathlib import Path
from collections import deque

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class StreamEvent:
    """A single event in the real-time stream."""
    event_type: str  # 'node_added', 'edge_added', 'node_updated', 'stats_update'
    timestamp: str
    data: Dict
    sequence: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class LiveStatistics:
    """Real-time exploration statistics."""
    nodes_total: int = 0
    edges_total: int = 0
    nodes_per_minute: float = 0.0
    coverage_percent: float = 0.0
    max_depth: int = 0
    avg_depth: float = 0.0
    current_node: Optional[str] = None
    last_activity: Optional[str] = None
    session_start: Optional[str] = None
    active_connections: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TimeLapseFrame:
    """A single frame in the time-lapse recording."""
    timestamp: str
    nodes: List[Dict]
    edges: List[Dict]
    stats: Dict
    event_type: str
    highlight_node_id: Optional[str] = None
    highlight_edge_id: Optional[str] = None


# =============================================================================
# STREAMING MANAGER
# =============================================================================

class ExplorationStreamManager:
    """
    Manages real-time streaming of exploration graph updates.

    Thread-safe singleton that buffers events and broadcasts
    them to connected WebSocket clients.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True

        # Event tracking
        self.event_queue = queue.Queue(maxsize=1000)
        self.recent_events = deque(maxlen=100)
        self.sequence_counter = 0

        # Statistics tracking
        self.stats = LiveStatistics()
        self.stats.session_start = datetime.now().isoformat()

        # Rate calculation
        self.node_timestamps: deque = deque(maxlen=100)

        # Time-lapse recording
        self.recording = False
        self.recording_frames: List[TimeLapseFrame] = []
        self.recording_start: Optional[datetime] = None

        # Current graph state (for new connections)
        self.current_nodes: Dict[str, Dict] = {}
        self.current_edges: Dict[str, Dict] = {}

        # Callbacks
        self.broadcast_callback: Optional[Callable] = None

        # Exploration depth tracking
        self.node_depths: Dict[str, int] = {}
        self.root_nodes: Set[str] = set()

        # Lock for thread safety
        self._state_lock = threading.Lock()

    def set_broadcast_callback(self, callback: Callable):
        """Set the callback function for broadcasting events to WebSocket clients."""
        self.broadcast_callback = callback

    def emit_node_added(
        self,
        node_id: str,
        name: str,
        node_type: str = 'file',
        path: Optional[str] = None,
        summary: str = '',
        tags: List[str] = None,
        parent_id: Optional[str] = None,
        x: Optional[float] = None,
        y: Optional[float] = None
    ):
        """Emit a node addition event."""
        with self._state_lock:
            self.sequence_counter += 1

            # Calculate depth
            depth = 0
            if parent_id and parent_id in self.node_depths:
                depth = self.node_depths[parent_id] + 1
            elif not parent_id:
                self.root_nodes.add(node_id)
            self.node_depths[node_id] = depth

            # Update max depth
            if depth > self.stats.max_depth:
                self.stats.max_depth = depth

            node_data = {
                'id': node_id,
                'name': name,
                'type': node_type,
                'path': path,
                'summary': summary[:200] if summary else '',
                'tags': tags or [],
                'exploration_count': 1,
                'x': x,
                'y': y,
                'depth': depth,
                'size': 15,  # Initial size
                'color': self._get_node_color(node_type)
            }

            # Store in current state
            self.current_nodes[node_id] = node_data

            # Track for rate calculation
            now = datetime.now()
            self.node_timestamps.append(now)

            # Update stats
            self.stats.nodes_total = len(self.current_nodes)
            self.stats.current_node = name
            self.stats.last_activity = now.isoformat()
            self._update_node_rate()
            self._update_depth_stats()

            event = StreamEvent(
                event_type='node_added',
                timestamp=now.isoformat(),
                data=node_data,
                sequence=self.sequence_counter
            )

            self._emit_event(event)

            # Record frame if recording
            if self.recording:
                self._record_frame(event, highlight_node_id=node_id)

    def emit_edge_added(
        self,
        source_id: str,
        target_id: str,
        relationship: str = 'explored_next',
        strength: float = 1.0,
        context: str = ''
    ):
        """Emit an edge addition event."""
        with self._state_lock:
            self.sequence_counter += 1

            edge_id = f"{source_id}->{target_id}"

            edge_data = {
                'id': edge_id,
                'source': source_id,
                'target': target_id,
                'relationship': relationship,
                'strength': strength,
                'context': context[:200] if context else '',
                'color': self._get_edge_color(relationship),
                'style': self._get_edge_style(relationship),
                'animated': True  # Enable animation for new edges
            }

            # Store in current state
            self.current_edges[edge_id] = edge_data

            # Update target node depth if needed
            if source_id in self.node_depths and target_id not in self.node_depths:
                self.node_depths[target_id] = self.node_depths[source_id] + 1
                if target_id in self.current_nodes:
                    self.current_nodes[target_id]['depth'] = self.node_depths[target_id]

            # Update stats
            self.stats.edges_total = len(self.current_edges)
            self.stats.last_activity = datetime.now().isoformat()
            self._update_depth_stats()

            event = StreamEvent(
                event_type='edge_added',
                timestamp=datetime.now().isoformat(),
                data=edge_data,
                sequence=self.sequence_counter
            )

            self._emit_event(event)

            # Record frame if recording
            if self.recording:
                self._record_frame(event, highlight_edge_id=edge_id)

    def emit_node_updated(
        self,
        node_id: str,
        updates: Dict
    ):
        """Emit a node update event (e.g., increased exploration count)."""
        with self._state_lock:
            if node_id not in self.current_nodes:
                return

            self.sequence_counter += 1

            # Update stored node
            self.current_nodes[node_id].update(updates)

            # Recalculate size based on exploration count
            count = self.current_nodes[node_id].get('exploration_count', 1)
            self.current_nodes[node_id]['size'] = 15 + min(count * 3, 30)

            event = StreamEvent(
                event_type='node_updated',
                timestamp=datetime.now().isoformat(),
                data={'id': node_id, 'updates': updates},
                sequence=self.sequence_counter
            )

            self._emit_event(event)

    def emit_stats_update(self):
        """Emit a statistics update event."""
        with self._state_lock:
            self._update_node_rate()
            self._update_depth_stats()

            event = StreamEvent(
                event_type='stats_update',
                timestamp=datetime.now().isoformat(),
                data=self.stats.to_dict(),
                sequence=self.sequence_counter
            )

            self._emit_event(event)

    def _emit_event(self, event: StreamEvent):
        """Internal method to emit an event."""
        # Add to recent events
        self.recent_events.append(event)

        # Add to queue for processing
        try:
            self.event_queue.put_nowait(event.to_dict())
        except queue.Full:
            # Drop oldest event and try again
            try:
                self.event_queue.get_nowait()
                self.event_queue.put_nowait(event.to_dict())
            except queue.Empty:
                pass

        # Broadcast via callback
        if self.broadcast_callback:
            self.broadcast_callback('graph_update', event.to_dict())

    def _update_node_rate(self):
        """Update the nodes-per-minute calculation."""
        if not self.node_timestamps:
            self.stats.nodes_per_minute = 0.0
            return

        now = datetime.now()
        one_minute_ago = now - timedelta(minutes=1)

        # Count nodes added in last minute
        recent = sum(1 for ts in self.node_timestamps if ts > one_minute_ago)
        self.stats.nodes_per_minute = float(recent)

    def _update_depth_stats(self):
        """Update depth statistics."""
        if not self.node_depths:
            return

        depths = list(self.node_depths.values())
        self.stats.max_depth = max(depths)
        self.stats.avg_depth = sum(depths) / len(depths)

    def _get_node_color(self, node_type: str) -> str:
        """Get color for a node type."""
        colors = {
            'file': '#58a6ff',
            'concept': '#3fb950',
            'pattern': '#d29922',
            'decision': '#f85149'
        }
        return colors.get(node_type, '#8b949e')

    def _get_edge_color(self, relationship: str) -> str:
        """Get color for an edge type."""
        colors = {
            'import': '#58a6ff',
            'grep_to_read': '#3fb950',
            'explored_next': '#8b949e',
            'reference': '#d29922',
            'test_of': '#f85149'
        }
        return colors.get(relationship, '#8b949e')

    def _get_edge_style(self, relationship: str) -> str:
        """Get line style for an edge type."""
        styles = {
            'import': 'solid',
            'grep_to_read': 'dashed',
            'explored_next': 'dotted',
            'reference': 'solid',
            'test_of': 'solid'
        }
        return styles.get(relationship, 'solid')

    # =========================================================================
    # TIME-LAPSE RECORDING
    # =========================================================================

    def start_recording(self):
        """Start time-lapse recording."""
        with self._state_lock:
            self.recording = True
            self.recording_frames = []
            self.recording_start = datetime.now()

            # Record initial frame with current state
            initial_frame = TimeLapseFrame(
                timestamp=self.recording_start.isoformat(),
                nodes=list(self.current_nodes.values()),
                edges=list(self.current_edges.values()),
                stats=self.stats.to_dict(),
                event_type='initial'
            )
            self.recording_frames.append(initial_frame)

    def stop_recording(self) -> List[Dict]:
        """Stop time-lapse recording and return frames."""
        with self._state_lock:
            self.recording = False
            frames = [
                {
                    'timestamp': f.timestamp,
                    'nodes': f.nodes,
                    'edges': f.edges,
                    'stats': f.stats,
                    'event_type': f.event_type,
                    'highlight_node_id': f.highlight_node_id,
                    'highlight_edge_id': f.highlight_edge_id
                }
                for f in self.recording_frames
            ]
            return frames

    def _record_frame(
        self,
        event: StreamEvent,
        highlight_node_id: Optional[str] = None,
        highlight_edge_id: Optional[str] = None
    ):
        """Record a frame during time-lapse recording."""
        frame = TimeLapseFrame(
            timestamp=event.timestamp,
            nodes=list(self.current_nodes.values()),
            edges=list(self.current_edges.values()),
            stats=self.stats.to_dict(),
            event_type=event.event_type,
            highlight_node_id=highlight_node_id,
            highlight_edge_id=highlight_edge_id
        )
        self.recording_frames.append(frame)

    def get_recording_status(self) -> Dict:
        """Get current recording status."""
        with self._state_lock:
            return {
                'is_recording': self.recording,
                'frame_count': len(self.recording_frames),
                'duration_seconds': (
                    (datetime.now() - self.recording_start).total_seconds()
                    if self.recording and self.recording_start else 0
                )
            }

    # =========================================================================
    # STATE MANAGEMENT
    # =========================================================================

    def get_current_state(self) -> Dict:
        """Get the current graph state for new connections."""
        with self._state_lock:
            return {
                'nodes': list(self.current_nodes.values()),
                'edges': list(self.current_edges.values()),
                'stats': self.stats.to_dict(),
                'sequence': self.sequence_counter
            }

    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        """Get recent events."""
        with self._state_lock:
            events = list(self.recent_events)[-limit:]
            return [e.to_dict() for e in events]

    def reset(self):
        """Reset the stream manager state."""
        with self._state_lock:
            self.current_nodes.clear()
            self.current_edges.clear()
            self.node_depths.clear()
            self.root_nodes.clear()
            self.recent_events.clear()
            self.node_timestamps.clear()
            self.sequence_counter = 0
            self.stats = LiveStatistics()
            self.stats.session_start = datetime.now().isoformat()
            self.recording = False
            self.recording_frames = []


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

def get_stream_manager() -> ExplorationStreamManager:
    """Get the singleton stream manager instance."""
    return ExplorationStreamManager()


# =============================================================================
# FLASK-SOCKETIO INTEGRATION
# =============================================================================

def register_streaming_routes(app, socketio):
    """
    Register WebSocket event handlers and REST routes for streaming.

    Call this from dashboard_v2.py after creating the SocketIO instance.
    """
    from flask import jsonify, request

    stream_manager = get_stream_manager()

    # Set up broadcast callback
    def broadcast_event(event_name: str, data: Dict):
        socketio.emit(event_name, data, namespace='/graph')

    stream_manager.set_broadcast_callback(broadcast_event)

    # WebSocket event handlers
    @socketio.on('connect', namespace='/graph')
    def handle_connect():
        """Handle new WebSocket connection."""
        stream_manager.stats.active_connections += 1

        # Send current state to new connection
        state = stream_manager.get_current_state()
        socketio.emit('initial_state', state, namespace='/graph')

        # Send recent events
        recent = stream_manager.get_recent_events(20)
        socketio.emit('recent_events', {'events': recent}, namespace='/graph')

    @socketio.on('disconnect', namespace='/graph')
    def handle_disconnect():
        """Handle WebSocket disconnection."""
        stream_manager.stats.active_connections = max(
            0, stream_manager.stats.active_connections - 1
        )

    @socketio.on('request_state', namespace='/graph')
    def handle_request_state():
        """Handle request for current state."""
        state = stream_manager.get_current_state()
        socketio.emit('current_state', state, namespace='/graph')

    @socketio.on('request_stats', namespace='/graph')
    def handle_request_stats():
        """Handle request for statistics."""
        stream_manager.emit_stats_update()

    @socketio.on('start_recording', namespace='/graph')
    def handle_start_recording():
        """Handle time-lapse recording start."""
        stream_manager.start_recording()
        socketio.emit('recording_started', {
            'timestamp': datetime.now().isoformat()
        }, namespace='/graph')

    @socketio.on('stop_recording', namespace='/graph')
    def handle_stop_recording():
        """Handle time-lapse recording stop."""
        frames = stream_manager.stop_recording()
        socketio.emit('recording_stopped', {
            'frame_count': len(frames),
            'timestamp': datetime.now().isoformat()
        }, namespace='/graph')

    # REST API endpoints
    @app.route('/api/graph/stream/state')
    def api_graph_stream_state():
        """Get current graph state."""
        return jsonify(stream_manager.get_current_state())

    @app.route('/api/graph/stream/stats')
    def api_graph_stream_stats():
        """Get current statistics."""
        return jsonify(stream_manager.stats.to_dict())

    @app.route('/api/graph/stream/events')
    def api_graph_stream_events():
        """Get recent events."""
        limit = request.args.get('limit', 50, type=int)
        return jsonify({'events': stream_manager.get_recent_events(limit)})

    @app.route('/api/graph/stream/recording/status')
    def api_graph_recording_status():
        """Get recording status."""
        return jsonify(stream_manager.get_recording_status())

    @app.route('/api/graph/stream/recording/start', methods=['POST'])
    def api_graph_recording_start():
        """Start time-lapse recording."""
        stream_manager.start_recording()
        return jsonify({'status': 'started', 'timestamp': datetime.now().isoformat()})

    @app.route('/api/graph/stream/recording/stop', methods=['POST'])
    def api_graph_recording_stop():
        """Stop time-lapse recording and get frames."""
        frames = stream_manager.stop_recording()
        return jsonify({
            'status': 'stopped',
            'frame_count': len(frames),
            'frames': frames
        })

    @app.route('/api/graph/stream/reset', methods=['POST'])
    def api_graph_stream_reset():
        """Reset the stream manager."""
        stream_manager.reset()
        return jsonify({'status': 'reset'})

    return stream_manager


# =============================================================================
# EXPLORATION HOOKS INTEGRATION
# =============================================================================

def integrate_with_exploration_hooks():
    """
    Integrate streaming with exploration_hooks.py.

    This patches the exploration hooks to emit streaming events
    when files are explored.
    """
    try:
        import exploration_hooks

        stream_manager = get_stream_manager()
        original_record = exploration_hooks.record_exploration

        def streaming_record_exploration(path: str, summary: str, tags=None):
            """Wrapped record_exploration that also emits streaming events."""
            # Call original
            result = original_record(path, summary, tags)

            # Emit streaming event
            if result.get('status') == 'recorded':
                node_id = Path(path).name  # Use filename as ID
                stream_manager.emit_node_added(
                    node_id=path,
                    name=node_id,
                    node_type='file',
                    path=path,
                    summary=summary,
                    tags=tags or []
                )

            return result

        # Replace with streaming version
        exploration_hooks.record_exploration = streaming_record_exploration

        return True
    except ImportError:
        return False


# =============================================================================
# HTML/JS FOR REAL-TIME STREAMING UI
# =============================================================================

REALTIME_STREAMING_HTML = '''
<!-- Real-Time Streaming Controls -->
<div id="realtime-streaming-panel" style="margin-top: 15px; padding: 10px; background: var(--bg); border-radius: 6px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
        <h4 style="margin: 0; font-size: 0.9em;">Real-Time Streaming</h4>
        <div style="display: flex; align-items: center; gap: 8px;">
            <span id="stream-connection-status" style="font-size: 0.75em; padding: 2px 8px; border-radius: 10px; background: var(--red); color: #fff;">Disconnected</span>
            <button id="btn-stream-connect" class="btn btn-small primary" onclick="toggleStreamConnection()" style="padding: 4px 8px; font-size: 0.75em;">Connect</button>
        </div>
    </div>

    <!-- Live Statistics Overlay -->
    <div id="live-stats-overlay" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px;">
        <div class="stat-box" style="background: var(--surface); padding: 8px; border-radius: 4px; text-align: center;">
            <div style="font-size: 0.7em; color: var(--text-dim);">Nodes/min</div>
            <div id="stat-nodes-per-min" style="font-size: 1.2em; font-weight: 600; color: var(--green);">0.0</div>
        </div>
        <div class="stat-box" style="background: var(--surface); padding: 8px; border-radius: 4px; text-align: center;">
            <div style="font-size: 0.7em; color: var(--text-dim);">Coverage</div>
            <div id="stat-coverage" style="font-size: 1.2em; font-weight: 600; color: var(--accent);">0%</div>
        </div>
        <div class="stat-box" style="background: var(--surface); padding: 8px; border-radius: 4px; text-align: center;">
            <div style="font-size: 0.7em; color: var(--text-dim);">Max Depth</div>
            <div id="stat-max-depth" style="font-size: 1.2em; font-weight: 600; color: var(--yellow);">0</div>
        </div>
    </div>

    <!-- Current Activity -->
    <div id="current-activity" style="margin-bottom: 10px; padding: 8px; background: var(--surface); border-radius: 4px; min-height: 40px;">
        <div style="font-size: 0.75em; color: var(--text-dim); margin-bottom: 4px;">Current Activity</div>
        <div id="current-activity-text" style="font-size: 0.85em; color: var(--text); word-break: break-all;">Waiting for exploration...</div>
    </div>

    <!-- Audio Feedback Toggle -->
    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
        <label style="font-size: 0.8em; display: flex; align-items: center; gap: 6px; cursor: pointer;">
            <input type="checkbox" id="audio-feedback-toggle" onchange="toggleAudioFeedback(this.checked)">
            <span>Audio Feedback</span>
        </label>
        <select id="audio-sound-select" style="font-size: 0.75em; padding: 2px; background: var(--surface); border: 1px solid var(--border); color: var(--text); border-radius: 4px;">
            <option value="blip">Blip</option>
            <option value="chime">Chime</option>
            <option value="ping">Ping</option>
        </select>
    </div>

    <!-- Time-Lapse Recording -->
    <div style="display: flex; align-items: center; gap: 8px;">
        <button id="btn-record" class="btn btn-small" onclick="toggleRecording()" style="padding: 4px 8px; font-size: 0.75em;">
            <span id="record-icon">&#9679;</span> Record
        </button>
        <span id="recording-status" style="font-size: 0.75em; color: var(--text-dim);">Not recording</span>
        <button id="btn-export-video" class="btn btn-small" onclick="exportTimeLapse()" style="padding: 4px 8px; font-size: 0.75em; display: none;">Export Video</button>
    </div>
</div>
'''

REALTIME_STREAMING_JS = '''
<script>
// =============================================================================
// Real-Time Streaming Client
// =============================================================================

class RealtimeGraphStream {
    constructor(canvasId) {
        this.canvasId = canvasId;
        this.socket = null;
        this.connected = false;
        this.audioEnabled = false;
        this.audioContext = null;
        this.soundType = 'blip';
        this.recording = false;
        this.recordedFrames = [];

        // Physics simulation for smooth animations
        this.physics = {
            nodes: new Map(),  // nodeId -> {vx, vy, targetX, targetY}
            springStrength: 0.1,
            damping: 0.8,
            animating: false
        };

        // Edge animation
        this.edgeAnimation = {
            dashOffset: 0,
            animating: false
        };
    }

    connect() {
        if (this.connected) return;

        // Connect to /graph namespace
        this.socket = io('/graph');

        this.socket.on('connect', () => {
            this.connected = true;
            this.updateConnectionStatus(true);
            console.log('Connected to graph stream');
        });

        this.socket.on('disconnect', () => {
            this.connected = false;
            this.updateConnectionStatus(false);
            console.log('Disconnected from graph stream');
        });

        this.socket.on('initial_state', (data) => {
            this.handleInitialState(data);
        });

        this.socket.on('graph_update', (event) => {
            this.handleGraphUpdate(event);
        });

        this.socket.on('recent_events', (data) => {
            // Process recent events for catch-up
            if (data.events) {
                data.events.forEach(event => this.handleGraphUpdate(event, false));
            }
        });

        this.socket.on('recording_started', () => {
            this.recording = true;
            this.updateRecordingStatus();
        });

        this.socket.on('recording_stopped', (data) => {
            this.recording = false;
            this.recordedFrames = data.frames || [];
            this.updateRecordingStatus();
        });
    }

    disconnect() {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
        this.connected = false;
        this.updateConnectionStatus(false);
    }

    handleInitialState(state) {
        if (!enhancedGraphRenderer) return;

        // Load initial nodes and edges
        enhancedGraphRenderer.loadData({
            nodes: state.nodes,
            edges: state.edges
        });

        // Initialize physics for all nodes
        state.nodes.forEach(node => {
            this.physics.nodes.set(node.id, {
                vx: 0, vy: 0,
                targetX: node.x,
                targetY: node.y
            });
        });

        // Update stats
        this.updateStats(state.stats);
    }

    handleGraphUpdate(event, playSound = true) {
        if (!enhancedGraphRenderer) return;

        switch (event.event_type) {
            case 'node_added':
                this.addNodeWithAnimation(event.data);
                if (playSound) this.playSound();
                break;

            case 'edge_added':
                this.addEdgeWithAnimation(event.data);
                break;

            case 'node_updated':
                this.updateNode(event.data);
                break;

            case 'stats_update':
                this.updateStats(event.data);
                break;
        }

        // Update activity display
        if (event.data && event.data.name) {
            this.updateCurrentActivity(event.event_type, event.data.name);
        }
    }

    addNodeWithAnimation(nodeData) {
        // Calculate initial position (near center or near parent)
        const canvas = enhancedGraphRenderer.canvas;
        if (!canvas) return;

        let x = nodeData.x || canvas.width / 2 + (Math.random() - 0.5) * 100;
        let y = nodeData.y || canvas.height / 2 + (Math.random() - 0.5) * 100;

        // Add node to renderer
        const node = {
            ...nodeData,
            x: x,
            y: y,
            // Start with small size for grow animation
            _animSize: 0,
            _targetSize: nodeData.size || 15
        };

        enhancedGraphRenderer.nodes.push(node);

        // Initialize physics
        this.physics.nodes.set(nodeData.id, {
            vx: 0, vy: 0,
            targetX: x,
            targetY: y
        });

        // Trigger spring animation
        this.animateNodeEntry(nodeData.id);

        // Update stats
        enhancedGraphRenderer.updateStats();
    }

    addEdgeWithAnimation(edgeData) {
        // Add edge to renderer
        const edge = {
            ...edgeData,
            _animProgress: 0  // For draw animation
        };

        enhancedGraphRenderer.edges.push(edge);

        // Start edge animation
        this.animateEdgeEntry(edge);

        // Update stats
        enhancedGraphRenderer.updateStats();
    }

    updateNode(data) {
        const node = enhancedGraphRenderer.nodes.find(n => n.id === data.id);
        if (node && data.updates) {
            Object.assign(node, data.updates);

            // Animate pulse
            enhancedGraphRenderer.animateNodePulse(data.id, 500);
        }
    }

    // =========================================================================
    // PHYSICS-BASED ANIMATION
    // =========================================================================

    animateNodeEntry(nodeId) {
        if (!this.physics.animating) {
            this.physics.animating = true;
            this.runPhysicsLoop();
        }

        // Also run grow animation
        this.animateNodeGrow(nodeId);
    }

    animateNodeGrow(nodeId) {
        const node = enhancedGraphRenderer.nodes.find(n => n.id === nodeId);
        if (!node) return;

        const startTime = performance.now();
        const duration = 300;
        const startSize = 0;
        const endSize = node._targetSize || node.size || 15;

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease-out-elastic for bouncy effect
            const eased = this.easeOutElastic(progress);
            node._animSize = startSize + (endSize - startSize) * eased;
            node.size = node._animSize;

            enhancedGraphRenderer.render();

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        requestAnimationFrame(animate);
    }

    easeOutElastic(x) {
        const c4 = (2 * Math.PI) / 3;
        return x === 0 ? 0 : x === 1 ? 1 :
            Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * c4) + 1;
    }

    runPhysicsLoop() {
        const nodes = enhancedGraphRenderer.nodes;
        if (!nodes || nodes.length === 0) {
            this.physics.animating = false;
            return;
        }

        let totalMovement = 0;

        nodes.forEach(node => {
            const physics = this.physics.nodes.get(node.id);
            if (!physics) return;

            // Spring force toward target
            const dx = physics.targetX - node.x;
            const dy = physics.targetY - node.y;

            // Apply spring force
            physics.vx += dx * this.physics.springStrength;
            physics.vy += dy * this.physics.springStrength;

            // Apply damping
            physics.vx *= this.physics.damping;
            physics.vy *= this.physics.damping;

            // Update position
            node.x += physics.vx;
            node.y += physics.vy;

            totalMovement += Math.abs(physics.vx) + Math.abs(physics.vy);
        });

        enhancedGraphRenderer.render();

        // Continue animation if there's significant movement
        if (totalMovement > 0.1) {
            requestAnimationFrame(() => this.runPhysicsLoop());
        } else {
            this.physics.animating = false;
        }
    }

    // =========================================================================
    // EDGE ANIMATION
    // =========================================================================

    animateEdgeEntry(edge) {
        const startTime = performance.now();
        const duration = 500;

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            edge._animProgress = progress;

            enhancedGraphRenderer.render();

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        requestAnimationFrame(animate);

        // Start dash animation for this edge
        if (!this.edgeAnimation.animating) {
            this.edgeAnimation.animating = true;
            this.runEdgeDashAnimation();
        }
    }

    runEdgeDashAnimation() {
        this.edgeAnimation.dashOffset -= 0.5;

        // Update all animated edges
        enhancedGraphRenderer.edges.forEach(edge => {
            if (edge.animated) {
                edge._dashOffset = this.edgeAnimation.dashOffset;
            }
        });

        enhancedGraphRenderer.render();

        // Continue animation
        if (this.connected) {
            requestAnimationFrame(() => this.runEdgeDashAnimation());
        } else {
            this.edgeAnimation.animating = false;
        }
    }

    // =========================================================================
    // AUDIO FEEDBACK
    // =========================================================================

    initAudio() {
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
        }
    }

    playSound() {
        if (!this.audioEnabled) return;

        this.initAudio();

        const oscillator = this.audioContext.createOscillator();
        const gainNode = this.audioContext.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(this.audioContext.destination);

        // Configure sound based on type
        switch (this.soundType) {
            case 'blip':
                oscillator.type = 'sine';
                oscillator.frequency.setValueAtTime(800, this.audioContext.currentTime);
                oscillator.frequency.exponentialRampToValueAtTime(400, this.audioContext.currentTime + 0.1);
                gainNode.gain.setValueAtTime(0.1, this.audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, this.audioContext.currentTime + 0.1);
                break;

            case 'chime':
                oscillator.type = 'triangle';
                oscillator.frequency.setValueAtTime(1200, this.audioContext.currentTime);
                oscillator.frequency.exponentialRampToValueAtTime(600, this.audioContext.currentTime + 0.15);
                gainNode.gain.setValueAtTime(0.08, this.audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, this.audioContext.currentTime + 0.15);
                break;

            case 'ping':
                oscillator.type = 'sine';
                oscillator.frequency.setValueAtTime(1000, this.audioContext.currentTime);
                gainNode.gain.setValueAtTime(0.1, this.audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, this.audioContext.currentTime + 0.05);
                break;
        }

        oscillator.start(this.audioContext.currentTime);
        oscillator.stop(this.audioContext.currentTime + 0.2);
    }

    // =========================================================================
    // UI UPDATES
    // =========================================================================

    updateConnectionStatus(connected) {
        const statusEl = document.getElementById('stream-connection-status');
        const btnEl = document.getElementById('btn-stream-connect');

        if (statusEl) {
            statusEl.textContent = connected ? 'Connected' : 'Disconnected';
            statusEl.style.background = connected ? 'var(--green)' : 'var(--red)';
        }

        if (btnEl) {
            btnEl.textContent = connected ? 'Disconnect' : 'Connect';
            btnEl.classList.toggle('primary', !connected);
            btnEl.classList.toggle('danger', connected);
        }
    }

    updateStats(stats) {
        if (!stats) return;

        const setIfExists = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };

        setIfExists('stat-nodes-per-min', (stats.nodes_per_minute || 0).toFixed(1));
        setIfExists('stat-coverage', Math.round(stats.coverage_percent || 0) + '%');
        setIfExists('stat-max-depth', stats.max_depth || 0);
    }

    updateCurrentActivity(eventType, name) {
        const el = document.getElementById('current-activity-text');
        if (!el) return;

        const actions = {
            'node_added': 'Exploring',
            'edge_added': 'Connected to',
            'node_updated': 'Revisiting'
        };

        const action = actions[eventType] || 'Processing';
        el.textContent = `${action}: ${name}`;

        // Fade effect
        el.style.opacity = '1';
        setTimeout(() => el.style.opacity = '0.7', 500);
    }

    updateRecordingStatus() {
        const statusEl = document.getElementById('recording-status');
        const btnEl = document.getElementById('btn-record');
        const iconEl = document.getElementById('record-icon');
        const exportBtn = document.getElementById('btn-export-video');

        if (statusEl) {
            statusEl.textContent = this.recording ?
                'Recording...' :
                (this.recordedFrames.length > 0 ?
                    `${this.recordedFrames.length} frames recorded` :
                    'Not recording');
        }

        if (btnEl) {
            btnEl.classList.toggle('danger', this.recording);
        }

        if (iconEl) {
            iconEl.style.color = this.recording ? 'var(--red)' : 'inherit';
        }

        if (exportBtn) {
            exportBtn.style.display = (!this.recording && this.recordedFrames.length > 0) ? 'inline-block' : 'none';
        }
    }
}

// =============================================================================
// GLOBAL INSTANCE AND HELPER FUNCTIONS
// =============================================================================

let graphStream = null;

function initGraphStream() {
    if (!graphStream) {
        graphStream = new RealtimeGraphStream('exploration-graph-canvas');
    }
    return graphStream;
}

function toggleStreamConnection() {
    const stream = initGraphStream();
    if (stream.connected) {
        stream.disconnect();
    } else {
        stream.connect();
    }
}

function toggleAudioFeedback(enabled) {
    const stream = initGraphStream();
    stream.audioEnabled = enabled;
    if (enabled) {
        stream.initAudio();
        // Play test sound
        stream.playSound();
    }
}

function toggleRecording() {
    const stream = initGraphStream();
    if (!stream.connected) {
        alert('Please connect to stream first');
        return;
    }

    if (stream.recording) {
        stream.socket.emit('stop_recording');
    } else {
        stream.socket.emit('start_recording');
    }
}

async function exportTimeLapse() {
    const stream = initGraphStream();
    if (stream.recordedFrames.length === 0) {
        alert('No frames to export');
        return;
    }

    // Fetch full recording data from server
    try {
        const response = await fetch('/api/graph/stream/recording/stop', { method: 'POST' });
        const data = await response.json();

        if (data.frames && data.frames.length > 0) {
            // Create downloadable JSON (can be converted to video externally)
            const blob = new Blob([JSON.stringify(data.frames, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `exploration_timelapse_${Date.now()}.json`;
            a.click();
            URL.revokeObjectURL(url);
        }
    } catch (e) {
        console.error('Failed to export time-lapse:', e);
        alert('Failed to export time-lapse');
    }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', function() {
    // Update sound type when dropdown changes
    const soundSelect = document.getElementById('audio-sound-select');
    if (soundSelect) {
        soundSelect.addEventListener('change', function() {
            const stream = initGraphStream();
            stream.soundType = this.value;
        });
    }
});
</script>
'''


def get_streaming_injection():
    """
    Get the HTML and JS to inject into the dashboard for streaming.

    Returns tuple of (html, js) strings.
    """
    return REALTIME_STREAMING_HTML, REALTIME_STREAMING_JS


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Real-Time Graph Streaming - Demo")
    print("=" * 50)

    # Test the stream manager
    manager = get_stream_manager()

    # Simulate some events
    print("\nSimulating node additions...")
    manager.emit_node_added(
        node_id="test1",
        name="api.py",
        node_type="file",
        path="/src/api.py",
        summary="REST API handlers"
    )

    manager.emit_node_added(
        node_id="test2",
        name="models.py",
        node_type="file",
        path="/src/models.py",
        summary="Database models",
        parent_id="test1"
    )

    manager.emit_edge_added(
        source_id="test1",
        target_id="test2",
        relationship="import"
    )

    # Print state
    state = manager.get_current_state()
    print(f"\nCurrent state:")
    print(f"  Nodes: {len(state['nodes'])}")
    print(f"  Edges: {len(state['edges'])}")
    print(f"  Stats: {state['stats']}")

    # Test recording
    print("\nTesting time-lapse recording...")
    manager.start_recording()

    manager.emit_node_added(
        node_id="test3",
        name="utils.py",
        node_type="file",
        path="/src/utils.py"
    )

    frames = manager.stop_recording()
    print(f"  Recorded frames: {len(frames)}")

    print("\nDemo complete!")
    print("\nTo integrate with dashboard_v2.py:")
    print("  from realtime_graph_streaming import register_streaming_routes")
    print("  register_streaming_routes(app, socketio)")
