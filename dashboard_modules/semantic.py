"""
Semantic Search Dashboard Module - Flask Blueprint wrapper.

Wraps the semantic_api.py from mission workspace with:
- TTL-based caching for expensive operations
- WebSocket real-time alerts
- Background worker for re-embedding
- Rate limiting

Note: This module does NOT import mission workspace code directly.
Instead, it proxies requests to the semantic API and adds caching/alerting.
"""

import time
import threading
import hashlib
import json
import logging
from pathlib import Path
from functools import wraps
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# Create Blueprint
semantic_bp = Blueprint('semantic', __name__, url_prefix='/api/semantic')

# Configuration - set via init function
_config = {
    'mission_workspace': None,
    'socketio': None,
    'io_utils': None,
    'initialized': False
}

# =============================================================================
# TTL CACHE IMPLEMENTATION
# =============================================================================

class TTLCache:
    """Simple TTL cache with per-key expiration."""

    # Cache TTL configuration (seconds)
    CACHE_CONFIG = {
        'visualization_embeddings': 300,  # 5 minutes
        'clusters': 120,                  # 2 minutes
        'drift_history': 60,              # 1 minute
        'quality_stats': 30,              # 30 seconds
        'feedback_stats': 60,             # 1 minute
        'default': 60                     # Default TTL
    }

    def __init__(self):
        self._cache = {}
        self._timestamps = {}
        self._lock = threading.Lock()

    def _get_ttl(self, key_type: str) -> int:
        """Get TTL for a key type."""
        return self.CACHE_CONFIG.get(key_type, self.CACHE_CONFIG['default'])

    def _make_key(self, key_type: str, params: dict) -> str:
        """Create cache key from type and params."""
        param_str = json.dumps(params, sort_keys=True)
        param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
        return f"{key_type}:{param_hash}"

    def get(self, key_type: str, params: dict = None):
        """Get value from cache if not expired."""
        key = self._make_key(key_type, params or {})
        with self._lock:
            if key in self._cache:
                age = time.time() - self._timestamps[key]
                ttl = self._get_ttl(key_type)
                if age < ttl:
                    logger.debug(f"Cache hit: {key} (age: {age:.1f}s, ttl: {ttl}s)")
                    return self._cache[key]
                else:
                    # Expired
                    del self._cache[key]
                    del self._timestamps[key]
            return None

    def set(self, key_type: str, params: dict, value):
        """Set value in cache."""
        key = self._make_key(key_type, params or {})
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
            logger.debug(f"Cache set: {key}")

    def invalidate(self, key_type: str = None):
        """Invalidate cache entries. If key_type is None, clear all."""
        with self._lock:
            if key_type is None:
                self._cache.clear()
                self._timestamps.clear()
                logger.info("Cache cleared completely")
            else:
                # Remove all keys matching the type
                to_remove = [k for k in self._cache if k.startswith(f"{key_type}:")]
                for k in to_remove:
                    del self._cache[k]
                    del self._timestamps[k]
                logger.info(f"Cache invalidated for type: {key_type}")

    def get_stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            return {
                'entries': len(self._cache),
                'types': list(set(k.split(':')[0] for k in self._cache))
            }


# Global cache instance
_cache = TTLCache()


# =============================================================================
# RATE LIMITER
# =============================================================================

class RateLimiter:
    """Simple rate limiter for API endpoints."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = {}  # ip -> list of timestamps
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str) -> bool:
        """Check if request is allowed."""
        now = time.time()
        with self._lock:
            if client_id not in self._requests:
                self._requests[client_id] = []

            # Remove old requests
            self._requests[client_id] = [
                ts for ts in self._requests[client_id]
                if now - ts < self.window_seconds
            ]

            # Check limit
            if len(self._requests[client_id]) >= self.max_requests:
                return False

            # Record request
            self._requests[client_id].append(now)
            return True


_rate_limiter = RateLimiter()


def rate_limited(f):
    """Decorator to apply rate limiting."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        client_id = request.remote_addr or 'unknown'
        if not _rate_limiter.is_allowed(client_id):
            return jsonify({
                'error': 'Rate limit exceeded',
                'retry_after': 60
            }), 429
        return f(*args, **kwargs)
    return wrapper


# =============================================================================
# BACKGROUND RE-EMBEDDER
# =============================================================================

class BackgroundReembedder:
    """Background worker for re-embedding anomalous entries."""

    def __init__(self):
        self.running = False
        self.progress = 0
        self.total = 0
        self._thread = None
        self._lock = threading.Lock()

    def start(self, entry_ids: list) -> dict:
        """Start re-embedding task in background."""
        with self._lock:
            if self.running:
                return {
                    'status': 'already_running',
                    'progress': self.progress,
                    'total': self.total
                }

            self.running = True
            self.progress = 0
            self.total = len(entry_ids)

        self._thread = threading.Thread(
            target=self._run_reembed,
            args=(entry_ids,),
            daemon=True
        )
        self._thread.start()

        return {
            'status': 'started',
            'total': self.total
        }

    def _run_reembed(self, entry_ids: list):
        """Background re-embedding worker."""
        try:
            engine = _get_semantic_engine()
            if engine is None:
                return

            for i, entry_id in enumerate(entry_ids):
                if not self.running:
                    break

                # Re-embed logic would go here
                # For now, just track progress
                with self._lock:
                    self.progress = i + 1

                # Emit progress update via WebSocket
                if _config['socketio']:
                    _config['socketio'].emit('reembed_progress', {
                        'progress': self.progress,
                        'total': self.total,
                        'entry_id': entry_id
                    }, room='semantic_updates', namespace='/widgets')

                time.sleep(0.1)  # Avoid overwhelming

        finally:
            with self._lock:
                self.running = False

            # Emit completion
            if _config['socketio']:
                _config['socketio'].emit('reembed_complete', {
                    'completed': self.progress,
                    'total': self.total
                }, room='semantic_updates', namespace='/widgets')

    def get_status(self) -> dict:
        """Get current status."""
        with self._lock:
            return {
                'running': self.running,
                'progress': self.progress,
                'total': self.total
            }

    def stop(self):
        """Stop background task."""
        with self._lock:
            self.running = False


_reembedder = BackgroundReembedder()


# =============================================================================
# SCHEDULED SNAPSHOT SCHEDULER
# =============================================================================

class SnapshotScheduler:
    """Background scheduler for automatic drift snapshot capture."""

    def __init__(self, interval_hours: float = 4.0):
        self.interval_hours = interval_hours
        self.running = False
        self._thread = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_capture = None
        self._next_capture = None
        self._capture_count = 0
        self._state_file = Path(__file__).parent.parent / 'state' / 'snapshot_scheduler.json'

    def _load_state(self):
        """Load scheduler state from disk."""
        try:
            if self._state_file.exists():
                with open(self._state_file) as f:
                    state = json.load(f)
                self.interval_hours = state.get('interval_hours', self.interval_hours)
                self._capture_count = state.get('capture_count', 0)
                last_ts = state.get('last_capture')
                if last_ts:
                    self._last_capture = last_ts
                return True
        except Exception as e:
            logger.debug(f"Could not load scheduler state: {e}")
        return False

    def _save_state(self):
        """Save scheduler state to disk."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, 'w') as f:
                json.dump({
                    'interval_hours': self.interval_hours,
                    'capture_count': self._capture_count,
                    'last_capture': self._last_capture,
                    'enabled': self.running
                }, f)
        except Exception as e:
            logger.debug(f"Could not save scheduler state: {e}")

    def start(self, interval_hours: float = None):
        """Start the scheduler."""
        with self._lock:
            if self.running:
                return {'status': 'already_running', 'interval_hours': self.interval_hours}

            if interval_hours is not None:
                self.interval_hours = max(0.1, min(168, interval_hours))  # 6 min to 1 week

            self._load_state()
            self.running = True
            self._stop_event.clear()
            self._next_capture = time.time() + (self.interval_hours * 3600)

        self._thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self._thread.start()

        self._save_state()
        logger.info(f"Snapshot scheduler started, interval: {self.interval_hours}h")

        return {
            'status': 'started',
            'interval_hours': self.interval_hours,
            'next_capture_in': self.interval_hours * 3600
        }

    def stop(self):
        """Stop the scheduler."""
        with self._lock:
            if not self.running:
                return {'status': 'not_running'}

            self.running = False
            self._stop_event.set()

        self._save_state()
        logger.info("Snapshot scheduler stopped")
        return {'status': 'stopped'}

    def _run_scheduler(self):
        """Background scheduler loop."""
        while not self._stop_event.is_set():
            # Wait for interval or stop event
            wait_time = self.interval_hours * 3600
            if self._stop_event.wait(timeout=wait_time):
                break  # Stop event received

            if not self.running:
                break

            # Capture snapshot
            try:
                self._capture_snapshot()
            except Exception as e:
                logger.error(f"Scheduled snapshot capture failed: {e}")

    def _capture_snapshot(self):
        """Capture a drift snapshot."""
        engine = _get_semantic_engine()
        if engine is None:
            logger.warning("Cannot capture snapshot: engine not available")
            return

        graph = _get_exploration_graph()
        embeddings, _, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            logger.info("No embeddings available for scheduled snapshot")
            return

        snapshot_id = engine.drift_monitor.capture_snapshot(embeddings)
        self._last_capture = time.time()
        self._capture_count += 1
        self._next_capture = time.time() + (self.interval_hours * 3600)

        self._save_state()

        # Invalidate drift cache
        _cache.invalidate('drift_history')

        # Emit WebSocket notification
        if _config['socketio']:
            _config['socketio'].emit('snapshot_captured', {
                'snapshot_id': snapshot_id,
                'embedding_count': len(embeddings),
                'scheduled': True
            }, room='semantic_updates', namespace='/widgets')

        logger.info(f"Scheduled snapshot captured: {snapshot_id}")

    def get_status(self) -> dict:
        """Get scheduler status."""
        with self._lock:
            return {
                'running': self.running,
                'interval_hours': self.interval_hours,
                'last_capture': self._last_capture,
                'next_capture': self._next_capture,
                'capture_count': self._capture_count,
                'time_until_next': max(0, self._next_capture - time.time()) if self._next_capture else None
            }

    def set_interval(self, interval_hours: float) -> dict:
        """Update capture interval."""
        with self._lock:
            self.interval_hours = max(0.1, min(168, interval_hours))
            if self.running:
                self._next_capture = time.time() + (self.interval_hours * 3600)
            self._save_state()
        return {'interval_hours': self.interval_hours, 'status': 'updated'}


_snapshot_scheduler = SnapshotScheduler()


# =============================================================================
# INITIALIZATION
# =============================================================================

def init_semantic_blueprint(mission_workspace: str = None, socketio=None, io_utils=None):
    """Initialize the semantic blueprint with required dependencies.

    Args:
        mission_workspace: Path to mission workspace containing semantic_api.py
        socketio: SocketIO instance for real-time updates
        io_utils: IO utilities module
    """
    global _config
    _config['mission_workspace'] = mission_workspace
    _config['socketio'] = socketio
    _config['io_utils'] = io_utils
    _config['initialized'] = True
    logger.info(f"Semantic blueprint initialized, workspace: {mission_workspace}")


def _get_semantic_engine():
    """Get the semantic search engine instance.

    Uses centralized workspace resolver to handle both shared and legacy workspaces.
    """
    try:
        import sys
        workspace = _config.get('mission_workspace')

        # If workspace not set, try to get it from mission.json dynamically
        if not workspace:
            try:
                from pathlib import Path
                import io_utils
                base_dir = Path(__file__).parent.parent
                mission_path = base_dir / 'state' / 'mission.json'
                if mission_path.exists():
                    mission_data = io_utils.atomic_read_json(str(mission_path), {})
                    if mission_data.get('mission_workspace'):
                        workspace = mission_data['mission_workspace']
                    elif mission_data.get('mission_id'):
                        # Use centralized workspace resolver
                        from .workspace_resolver import resolve_mission_workspace
                        missions_dir = base_dir / 'missions'
                        workspace_dir = base_dir / 'workspace'
                        workspace = str(resolve_mission_workspace(
                            mission_data['mission_id'],
                            missions_dir,
                            workspace_dir,
                            io_utils,
                            mission_data
                        ))
            except Exception as e:
                logger.debug(f"Could not read mission.json: {e}")

        if workspace and str(workspace) not in sys.path:
            sys.path.insert(0, str(workspace))

        from semantic_search_engine import get_semantic_search_engine
        return get_semantic_search_engine()
    except ImportError as e:
        logger.warning(f"Could not import semantic search engine: {e}")
        return None


def _get_exploration_graph():
    """Get the exploration graph instance."""
    try:
        from atlasforge_enhancements.exploration_graph import ExplorationGraph
        return ExplorationGraph()
    except ImportError:
        logger.debug("ExplorationGraph not available")
        return None


# Demo/test data storage
_demo_data = {
    'embeddings': None,
    'entry_ids': [],
    'names': [],
    'texts': []
}


def _get_embeddings_from_graph(graph):
    """Extract embeddings and metadata from exploration graph."""
    try:
        import numpy as np
    except ImportError:
        return None, [], [], []

    embeddings = []
    entry_ids = []
    names = []
    texts = []

    # First try exploration graph
    if graph is not None:
        for node_id, node in graph.nodes.items():
            if node.embedding is not None:
                embeddings.append(node.embedding)
                entry_ids.append(node_id)
                names.append(node.name)
                texts.append(node.get_searchable_text())

    # Fallback to demo data if no graph embeddings
    if not embeddings and _demo_data['embeddings'] is not None:
        return (
            _demo_data['embeddings'],
            _demo_data['entry_ids'],
            _demo_data['names'],
            _demo_data['texts']
        )

    if not embeddings:
        return None, [], [], []

    return np.array(embeddings, dtype=np.float32), entry_ids, names, texts


def _generate_demo_embeddings(n_embeddings=50, n_clusters=3, dim=384):
    """Generate synthetic embeddings for demo/testing."""
    import numpy as np
    np.random.seed(42)

    embeddings = []
    entry_ids = []
    names = []
    texts = []

    cluster_themes = [
        ('authentication', 'login, user, session, token'),
        ('data_processing', 'parse, transform, validate, filter'),
        ('api_handlers', 'request, response, endpoint, route')
    ]

    for cluster in range(n_clusters):
        theme_name, theme_keywords = cluster_themes[cluster % len(cluster_themes)]
        center = np.random.randn(dim)
        cluster_size = n_embeddings // n_clusters + (1 if cluster < n_embeddings % n_clusters else 0)

        for i in range(cluster_size):
            emb = center + np.random.randn(dim) * 0.3
            embeddings.append(emb)
            entry_ids.append(f'{theme_name}_{i}')
            names.append(f'{theme_name.replace("_", " ").title()} {i}')
            texts.append(f'{theme_keywords} function implementation {i}')

    return np.array(embeddings, dtype=np.float32), entry_ids, names, texts


# =============================================================================
# WEBSOCKET EMITTERS
# =============================================================================

def emit_drift_alert(data: dict):
    """Emit drift alert to semantic_updates room."""
    if _config['socketio']:
        _config['socketio'].emit('drift_alert', data,
                                  room='semantic_updates',
                                  namespace='/widgets')
        logger.info(f"Emitted drift alert: {data.get('message', 'unknown')}")


def emit_quality_warning(data: dict):
    """Emit quality warning to semantic_updates room."""
    if _config['socketio']:
        _config['socketio'].emit('quality_warning', data,
                                  room='semantic_updates',
                                  namespace='/widgets')
        logger.info(f"Emitted quality warning: {data.get('message', 'unknown')}")


# =============================================================================
# STATUS ENDPOINTS
# =============================================================================

@semantic_bp.route('/status')
@rate_limited
def api_semantic_status():
    """Get semantic search engine status."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({
                'available': False,
                'error': 'Semantic search engine not available',
                'cache_stats': _cache.get_stats(),
                'reembedder_status': _reembedder.get_status()
            })

        status = engine.get_status()
        status['available'] = True
        status['cache_stats'] = _cache.get_stats()
        status['reembedder_status'] = _reembedder.get_status()
        status['exploration_graph_available'] = _get_exploration_graph() is not None
        return jsonify(status)
    except Exception as e:
        logger.exception("Error getting semantic status")
        return jsonify({'error': str(e), 'available': False}), 500


# =============================================================================
# EMBEDDING QUALITY ENDPOINTS
# =============================================================================

@semantic_bp.route('/embeddings/quality')
@rate_limited
def api_embedding_quality():
    """Get embedding quality statistics (cached)."""
    # Check cache first
    cached = _cache.get('quality_stats')
    if cached is not None:
        cached['cached'] = True
        return jsonify(cached)

    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            result = {
                'error': 'No embeddings available',
                'stats': None,
                'anomalous_entries': [],
                'total_count': 0
            }
        else:
            result = engine.validate_embeddings(embeddings, entry_ids)
            result['total_count'] = len(embeddings)

            # Check for quality warning
            if result.get('stats', {}).get('anomaly_rate', 0) > 0.1:
                emit_quality_warning({
                    'message': f"High anomaly rate: {result['stats']['anomaly_rate']:.1%}",
                    'anomaly_count': len(result.get('anomalous_entries', [])),
                    'total_count': len(embeddings)
                })

        result['cached'] = False
        _cache.set('quality_stats', {}, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("Error getting embedding quality")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/embeddings/anomalies')
@rate_limited
def api_embedding_anomalies():
    """Get list of entries with anomalous embeddings."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available', 'anomalies': []}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, names, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({'anomalies': [], 'count': 0})

        anomalous_ids = engine.quality_validator.get_anomalous_entries(embeddings, entry_ids)

        # Enrich with node info
        anomalies = []
        for entry_id in anomalous_ids:
            node = graph.nodes.get(entry_id) if graph else None
            if node:
                anomalies.append({
                    'entry_id': entry_id,
                    'name': node.name,
                    'path': node.path,
                    'node_type': node.node_type
                })

        return jsonify({'anomalies': anomalies, 'count': len(anomalies)})
    except Exception as e:
        logger.exception("Error getting anomalies")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/embeddings/revalidate', methods=['POST'])
@rate_limited
def api_revalidate_embeddings():
    """Trigger background re-embedding of anomalous entries."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        if graph is None:
            return jsonify({'error': 'Exploration graph not available'}), 503

        embeddings, entry_ids, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({'reembedded': 0, 'message': 'No embeddings to validate'})

        # Get anomalous entries
        anomalous_ids = engine.quality_validator.get_anomalous_entries(embeddings, entry_ids)

        if not anomalous_ids:
            return jsonify({'reembedded': 0, 'message': 'No anomalous entries found'})

        # Start background re-embedding
        result = _reembedder.start(anomalous_ids)

        # Invalidate quality cache
        _cache.invalidate('quality_stats')

        return jsonify(result)
    except Exception as e:
        logger.exception("Error starting revalidation")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/embeddings/revalidate/status')
def api_revalidate_status():
    """Get re-embedding background task status."""
    return jsonify(_reembedder.get_status())


# =============================================================================
# CLUSTERING ENDPOINTS
# =============================================================================

@semantic_bp.route('/clusters')
@rate_limited
def api_get_clusters():
    """Get HDBSCAN clustering results (cached)."""
    # Check cache
    cached = _cache.get('clusters')
    if cached is not None:
        cached['cached'] = True
        return jsonify(cached)

    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, _, texts = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({
                'error': 'No embeddings available',
                'n_clusters': 0,
                'clusters': []
            })

        result = engine.cluster_embeddings(embeddings, entry_ids, texts)

        if 'error' in result:
            return jsonify(result), 500

        # Build cluster details
        clusters = []
        for cluster_id, size in result.get('cluster_sizes', {}).items():
            theme = result.get('themes', {}).get(cluster_id, f"Cluster {cluster_id}")
            members = engine.clusterer.get_cluster_entry_ids(cluster_id) if engine.clusterer else []

            # Get member names
            member_names = []
            for mid in members[:10]:
                node = graph.nodes.get(mid) if graph else None
                if node:
                    member_names.append(node.name)

            clusters.append({
                'id': cluster_id,
                'size': size,
                'theme': theme,
                'sample_members': member_names
            })

        response = {
            'n_clusters': result.get('n_clusters', 0),
            'n_noise': result.get('n_noise', 0),
            'silhouette_score': result.get('silhouette_score'),
            'clusters': clusters,
            'cached': False
        }

        _cache.set('clusters', {}, response)
        return jsonify(response)
    except Exception as e:
        logger.exception("Error getting clusters")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/clusters/<int:cluster_id>')
@rate_limited
def api_get_cluster_detail(cluster_id):
    """Get detailed information about a specific cluster."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, _, texts = _get_embeddings_from_graph(graph)

        if embeddings is None:
            return jsonify({'error': 'No embeddings available'}), 404

        # Ensure clustering is done
        engine.cluster_embeddings(embeddings, entry_ids, texts)

        if engine.clusterer is None:
            return jsonify({'error': 'Clustering not available'}), 503

        member_ids = engine.clusterer.get_cluster_entry_ids(cluster_id)
        if not member_ids:
            return jsonify({'error': f'Cluster {cluster_id} not found'}), 404

        # Get theme
        theme_keywords = engine.clusterer.extract_cluster_themes(cluster_id, texts, top_n=10)

        # Get member details
        members = []
        for entry_id in member_ids:
            node = graph.nodes.get(entry_id) if graph else None
            if node:
                members.append({
                    'entry_id': entry_id,
                    'name': node.name,
                    'path': node.path,
                    'node_type': node.node_type,
                    'summary': node.summary[:200] if node.summary else ""
                })

        return jsonify({
            'cluster_id': cluster_id,
            'size': len(member_ids),
            'theme_keywords': [{'keyword': kw, 'frequency': freq} for kw, freq in theme_keywords],
            'members': members
        })
    except Exception as e:
        logger.exception(f"Error getting cluster {cluster_id}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# SIMILAR CODE ENDPOINTS
# =============================================================================

@semantic_bp.route('/similar-code', methods=['POST'])
@rate_limited
def api_find_similar_code():
    """Find code entries similar to a given snippet."""
    try:
        data = request.get_json() or {}
        code = data.get('code', '')
        top_k = min(data.get('top_k', 10), 100)
        min_similarity = max(0.0, min(1.0, data.get('min_similarity', 0.5)))

        if not code.strip():
            return jsonify({'error': 'Code snippet required', 'results': []}), 400

        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({'results': [], 'message': 'No embeddings to search'})

        results = engine.find_similar_code(code, embeddings, entry_ids, top_k, min_similarity)

        # Enrich results
        enriched = []
        for item in results:
            entry_id = item['entry_id']
            node = graph.nodes.get(entry_id) if graph else None
            if node:
                enriched.append({
                    'entry_id': entry_id,
                    'name': node.name,
                    'path': node.path,
                    'node_type': node.node_type,
                    'similarity': item['similarity'],
                    'summary': node.summary[:200] if node.summary else ""
                })

        return jsonify({'results': enriched, 'count': len(enriched)})
    except Exception as e:
        logger.exception("Error finding similar code")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# DRIFT DETECTION ENDPOINTS
# =============================================================================

@semantic_bp.route('/drift/status')
@rate_limited
def api_drift_status():
    """Get current embedding drift status."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available', 'status': 'unavailable'}), 503

        graph = _get_exploration_graph()
        embeddings, _, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({
                'status': 'no_data',
                'message': 'No embeddings available for drift analysis'
            })

        drift = engine.check_drift(embeddings)

        if drift is None:
            snapshot_id = engine.drift_monitor.capture_snapshot(embeddings)
            return jsonify({
                'status': 'baseline_created',
                'snapshot_id': snapshot_id,
                'message': 'First snapshot captured as baseline'
            })

        # Check for drift alert
        if drift.get('overall_drift', 0) > 0.5:
            emit_drift_alert({
                'message': f"High drift detected: {drift['overall_drift']:.2f}",
                'drift_score': drift['overall_drift'],
                'alert_level': drift.get('alert_level', 'warning')
            })

        return jsonify({
            'status': 'analyzed',
            **drift
        })
    except Exception as e:
        logger.exception("Error checking drift")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/drift/history')
@rate_limited
def api_drift_history():
    """Get drift history over time (cached)."""
    days = min(request.args.get('days', 30, type=int), 365)

    # Check cache
    cached = _cache.get('drift_history', {'days': days})
    if cached is not None:
        cached['cached'] = True
        return jsonify(cached)

    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        history = engine.drift_monitor.get_drift_history(days=days)

        response = {'history': history, 'days': days, 'cached': False}
        _cache.set('drift_history', {'days': days}, response)

        return jsonify(response)
    except Exception as e:
        logger.exception("Error getting drift history")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/drift/snapshot', methods=['POST'])
@rate_limited
def api_capture_snapshot():
    """Manually capture a drift snapshot."""
    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, _, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({'error': 'No embeddings to snapshot'}), 400

        snapshot_id = engine.drift_monitor.capture_snapshot(embeddings)

        # Invalidate drift history cache
        _cache.invalidate('drift_history')

        return jsonify({
            'snapshot_id': snapshot_id,
            'embedding_count': len(embeddings)
        })
    except Exception as e:
        logger.exception("Error capturing snapshot")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# FEEDBACK ENDPOINTS
# =============================================================================

@semantic_bp.route('/feedback', methods=['POST'])
@rate_limited
def api_submit_feedback():
    """Submit search relevance feedback."""
    try:
        data = request.get_json() or {}
        query = data.get('query', '')
        result_id = data.get('result_id', '')
        feedback_type = data.get('feedback', '')
        user_id = data.get('user_id', 'anonymous')

        if not query or not result_id or not feedback_type:
            return jsonify({'error': 'query, result_id, and feedback required'}), 400

        if feedback_type not in ('helpful', 'not_helpful', 'irrelevant'):
            return jsonify({'error': "feedback must be 'helpful', 'not_helpful', or 'irrelevant'"}), 400

        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        feedback_id = engine.submit_feedback(query, result_id, feedback_type, user_id)

        # Invalidate feedback stats cache
        _cache.invalidate('feedback_stats')

        return jsonify({
            'feedback_id': feedback_id,
            'message': f"Feedback recorded: {feedback_type}"
        })
    except Exception as e:
        logger.exception("Error submitting feedback")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/feedback/stats')
@rate_limited
def api_feedback_stats():
    """Get feedback statistics (cached)."""
    cached = _cache.get('feedback_stats')
    if cached is not None:
        cached['cached'] = True
        return jsonify(cached)

    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        stats = engine.feedback_loop.get_feedback_stats()
        stats['cached'] = False

        _cache.set('feedback_stats', {}, stats)
        return jsonify(stats)
    except Exception as e:
        logger.exception("Error getting feedback stats")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# VISUALIZATION ENDPOINTS
# =============================================================================

@semantic_bp.route('/visualization/embeddings')
@rate_limited
def api_visualization():
    """Get UMAP/t-SNE visualization data (cached)."""
    dimensions = min(max(request.args.get('dimensions', 2, type=int), 2), 3)
    include_clusters = request.args.get('include_clusters', 'true').lower() == 'true'
    method = request.args.get('method', 'umap').lower()

    params = {
        'dimensions': dimensions,
        'include_clusters': include_clusters,
        'method': method
    }

    # Check cache
    cached = _cache.get('visualization_embeddings', params)
    if cached is not None:
        cached['cached'] = True
        return jsonify(cached)

    try:
        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, names, texts = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({
                'error': 'No embeddings available',
                'points': [],
                'clusters': [],
                'metadata': {}
            })

        if len(embeddings) < 3:
            return jsonify({
                'error': 'Need at least 3 embeddings for visualization',
                'points': [],
                'clusters': [],
                'metadata': {}
            })

        result = engine.get_visualization(
            embeddings=embeddings,
            entry_ids=entry_ids,
            names=names,
            dimensions=dimensions,
            include_clusters=include_clusters,
            texts=texts if include_clusters else None
        )

        result['cached'] = False
        _cache.set('visualization_embeddings', params, result)

        return jsonify(result)
    except Exception as e:
        logger.exception("Error generating visualization")
        return jsonify({'error': str(e)}), 500


@semantic_bp.route('/visualization/invalidate-cache', methods=['POST'])
def api_invalidate_viz_cache():
    """Invalidate visualization cache."""
    _cache.invalidate('visualization_embeddings')
    return jsonify({'message': 'Visualization cache invalidated'})


# =============================================================================
# SEARCH ENDPOINT
# =============================================================================

@semantic_bp.route('/search', methods=['POST'])
@rate_limited
def api_search():
    """Perform semantic search with feedback adjustment."""
    try:
        data = request.get_json() or {}
        query = data.get('query') or ''  # Handle None explicitly
        top_k = min(data.get('top_k', 10), 100)
        min_similarity = max(0.0, min(1.0, data.get('min_similarity', 0.3)))
        use_feedback = data.get('use_feedback', True)

        if not isinstance(query, str) or not query.strip():
            return jsonify({'error': 'Query required', 'results': []}), 400

        engine = _get_semantic_engine()
        if engine is None:
            return jsonify({'error': 'Engine not available'}), 503

        graph = _get_exploration_graph()
        embeddings, entry_ids, _, _ = _get_embeddings_from_graph(graph)

        if embeddings is None or len(embeddings) == 0:
            return jsonify({'results': [], 'message': 'No embeddings to search'})

        # Generate query embedding
        query_embedding = engine.similar_finder.embed_code(query)
        if query_embedding is None:
            return jsonify({'error': 'Could not generate query embedding'}), 500

        if use_feedback:
            results = engine.search_with_feedback(
                query_embedding, embeddings, entry_ids, query, top_k, min_similarity
            )
        else:
            raw_results = engine.similar_finder.find_similar(
                query_embedding, embeddings, entry_ids, top_k, min_similarity
            )
            results = [{'entry_id': eid, 'score': score, 'feedback_adjusted': False}
                      for eid, score in raw_results]

        # Enrich with node info
        enriched = []
        for item in results:
            entry_id = item['entry_id']
            node = graph.nodes.get(entry_id) if graph else None
            if node:
                enriched.append({
                    **item,
                    'name': node.name,
                    'path': node.path,
                    'node_type': node.node_type,
                    'summary': node.summary[:200] if node.summary else ""
                })

        return jsonify({
            'query': query,
            'results': enriched,
            'count': len(enriched),
            'feedback_applied': use_feedback
        })
    except Exception as e:
        logger.exception("Error in search")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# CACHE MANAGEMENT ENDPOINTS
# =============================================================================

@semantic_bp.route('/cache/stats')
def api_cache_stats():
    """Get cache statistics."""
    return jsonify(_cache.get_stats())


@semantic_bp.route('/cache/clear', methods=['POST'])
def api_cache_clear():
    """Clear all cache entries."""
    _cache.invalidate()
    return jsonify({'message': 'Cache cleared'})


# =============================================================================
# SCHEDULED SNAPSHOT ENDPOINTS
# =============================================================================

@semantic_bp.route('/scheduler/status')
def api_scheduler_status():
    """Get snapshot scheduler status."""
    return jsonify(_snapshot_scheduler.get_status())


@semantic_bp.route('/scheduler/start', methods=['POST'])
def api_scheduler_start():
    """Start the snapshot scheduler.

    Optional JSON body:
        interval_hours: Capture interval in hours (default: 4.0)
    """
    data = request.get_json() or {}
    interval_hours = data.get('interval_hours')
    result = _snapshot_scheduler.start(interval_hours=interval_hours)
    return jsonify(result)


@semantic_bp.route('/scheduler/stop', methods=['POST'])
def api_scheduler_stop():
    """Stop the snapshot scheduler."""
    result = _snapshot_scheduler.stop()
    return jsonify(result)


@semantic_bp.route('/scheduler/interval', methods=['POST'])
def api_scheduler_set_interval():
    """Update scheduler interval.

    JSON body:
        interval_hours: New interval in hours (0.1 to 168)
    """
    data = request.get_json() or {}
    interval_hours = data.get('interval_hours')
    if interval_hours is None:
        return jsonify({'error': 'interval_hours required'}), 400
    result = _snapshot_scheduler.set_interval(interval_hours)
    return jsonify(result)


# =============================================================================
# DEMO DATA ENDPOINTS (for testing when no real embeddings exist)
# =============================================================================

@semantic_bp.route('/demo/generate', methods=['POST'])
def api_generate_demo_data():
    """Generate demo embedding data for testing.

    Useful when no exploration graph data exists.
    """
    global _demo_data

    data = request.get_json() or {}
    n_embeddings = min(data.get('n_embeddings', 50), 500)
    n_clusters = min(data.get('n_clusters', 3), 10)

    embeddings, entry_ids, names, texts = _generate_demo_embeddings(
        n_embeddings=n_embeddings,
        n_clusters=n_clusters
    )

    _demo_data['embeddings'] = embeddings
    _demo_data['entry_ids'] = entry_ids
    _demo_data['names'] = names
    _demo_data['texts'] = texts

    # Invalidate caches
    _cache.invalidate()

    # Also capture a drift snapshot for the demo data
    engine = _get_semantic_engine()
    if engine:
        engine.drift_monitor.capture_snapshot(embeddings)

    return jsonify({
        'message': 'Demo data generated',
        'n_embeddings': len(entry_ids),
        'n_clusters': n_clusters,
        'entry_ids_sample': entry_ids[:5]
    })


@semantic_bp.route('/demo/clear', methods=['POST'])
def api_clear_demo_data():
    """Clear demo data."""
    global _demo_data
    _demo_data = {
        'embeddings': None,
        'entry_ids': [],
        'names': [],
        'texts': []
    }
    _cache.invalidate()
    return jsonify({'message': 'Demo data cleared'})
