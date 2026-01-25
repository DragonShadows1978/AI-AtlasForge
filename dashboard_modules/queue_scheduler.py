"""
Mission Queue Scheduler Blueprint

Provides API endpoints for managing the mission queue:
- Queue status and management
- Mission scheduling
- Priority handling
- Queue operations (add, remove, reorder)

The mission queue allows missions to be queued for execution,
either from the dashboard, email triggers, or recommendations.
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
import threading

logger = logging.getLogger(__name__)

# Create Blueprint
queue_scheduler_bp = Blueprint('queue_scheduler', __name__, url_prefix='/api/queue')

# Base paths - use centralized configuration
from atlasforge_config import MISSION_QUEUE_PATH, STATE_DIR, BASE_DIR

# Queue lock for thread safety
_queue_lock = threading.Lock()

# SocketIO instance (set by init function)
_socketio = None


def init_queue_scheduler_blueprint(socketio=None):
    """Initialize the queue scheduler blueprint with optional socketio instance."""
    global _socketio
    _socketio = socketio
    logger.info("Queue scheduler blueprint initialized")


def _emit_queue_update(queue_data, change_type='updated'):
    """Emit queue update event via WebSocket if available."""
    # Use centralized websocket_events for proper room-based emission
    try:
        from websocket_events import emit_queue_updated
        emit_queue_updated(queue_data, change_type)
    except ImportError:
        pass  # websocket_events not available
    except Exception as e:
        logger.error(f"Error emitting queue update via websocket_events: {e}")

    # Also emit via local socketio for backward compatibility
    if _socketio:
        try:
            _socketio.emit('queue_updated', {
                'queue_length': len(queue_data.get("missions", [])),
                'missions': queue_data.get("missions", []),
                'settings': queue_data.get("settings", {}),
                'last_updated': queue_data.get("last_updated"),
                'change_type': change_type
            })
        except Exception as e:
            logger.error(f"Error emitting queue update via local socketio: {e}")


def _load_queue() -> Dict[str, Any]:
    """Load the mission queue from disk."""
    try:
        if MISSION_QUEUE_PATH.exists():
            with open(MISSION_QUEUE_PATH, 'r') as f:
                data = json.load(f)
                # Normalize: the core scheduler uses 'queue', dashboard uses 'missions'
                if "queue" in data and "missions" not in data:
                    data["missions"] = data["queue"]
                if "missions" not in data:
                    data["missions"] = []
                if "settings" not in data:
                    data["settings"] = {
                        "auto_start": data.get("enabled", False),
                        "max_concurrent": 1,
                        "default_cycle_budget": 3,
                        "paused": data.get("paused", False),
                        "paused_at": data.get("paused_at"),
                        "pause_reason": data.get("pause_reason")
                    }
                return data
    except Exception as e:
        logger.error(f"Error loading queue: {e}")

    # Return default structure
    return {
        "missions": [],
        "settings": {
            "auto_start": False,
            "max_concurrent": 1,
            "default_cycle_budget": 3,
            "paused": False,
            "paused_at": None,
            "pause_reason": None
        },
        "last_updated": datetime.now().isoformat()
    }


def _save_queue(queue: Dict[str, Any]) -> bool:
    """Save the mission queue to disk."""
    try:
        queue["last_updated"] = datetime.now().isoformat()
        # Keep both 'queue' and 'missions' keys in sync for compatibility
        if "missions" in queue:
            queue["queue"] = queue["missions"]
        # Sync enabled flag with settings.auto_start
        if "settings" in queue:
            queue["enabled"] = queue["settings"].get("auto_start", False)
            queue["paused"] = queue["settings"].get("paused", False)
            queue["paused_at"] = queue["settings"].get("paused_at")
            queue["pause_reason"] = queue["settings"].get("pause_reason")
        with open(MISSION_QUEUE_PATH, 'w') as f:
            json.dump(queue, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving queue: {e}")
        return False


@queue_scheduler_bp.route('/status')
def queue_status():
    """Get queue status and statistics."""
    try:
        with _queue_lock:
            queue = _load_queue()

        # Check if AtlasForge is currently running a mission
        from io_utils import atomic_read_json
        mission = atomic_read_json(STATE_DIR / "mission.json") or {}
        current_stage = mission.get("current_stage", "COMPLETE")
        is_running = current_stage not in ["COMPLETE", None, ""]

        # Build next_up info for the first queued mission
        next_up = None
        missions = queue.get("missions", [])
        if missions:
            first = missions[0]
            problem_stmt = first.get("problem_statement", "")
            next_up = {
                "id": first.get("id"),
                "title": (problem_stmt[:60] + "...") if len(problem_stmt) > 60 else problem_stmt,
                "priority": first.get("priority", "normal"),
                "position": 1,
                "cycle_budget": first.get("cycle_budget", 3)
            }

        return jsonify({
            "queue_length": len(missions),
            "missions": missions,
            "settings": queue.get("settings", {}),
            "last_updated": queue.get("last_updated"),
            "atlasforge_running": is_running,
            "current_stage": current_stage,
            "next_up": next_up
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/lock-status')
def get_lock_status():
    """Get queue processing lock status for diagnostics."""
    try:
        from queue_processing_lock import is_queue_locked, get_queue_lock_info
        lock_info = get_queue_lock_info()

        # Add age calculation if locked
        if lock_info and lock_info.get("locked_at"):
            try:
                locked_at = datetime.fromisoformat(lock_info["locked_at"])
                lock_info["age_seconds"] = (datetime.now() - locked_at).total_seconds()
            except (ValueError, TypeError):
                lock_info["age_seconds"] = None

        return jsonify({
            "locked": is_queue_locked(),
            "lock_info": lock_info,
            "timestamp": datetime.now().isoformat()
        })
    except ImportError:
        return jsonify({
            "locked": False,
            "lock_info": None,
            "error": "Lock module not available",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/lock-metrics')
def get_lock_metrics():
    """Get lock acquisition/release timing metrics."""
    try:
        from queue_lock_metrics import get_lock_metrics as get_metrics
        metrics = get_metrics()

        return jsonify({
            "summary": metrics.get_summary(),
            "recent_events": metrics.get_recent_events(20),
            "timestamp": datetime.now().isoformat()
        })
    except ImportError:
        return jsonify({
            "summary": None,
            "recent_events": [],
            "error": "Lock metrics module not available",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/lock-release', methods=['POST'])
def force_release_lock():
    """Force release a stale queue processing lock (admin endpoint)."""
    try:
        from queue_processing_lock import force_release_stale_lock, get_queue_lock_info
        lock_info = get_queue_lock_info()
        if lock_info and lock_info.get("is_valid"):
            return jsonify({
                "released": False,
                "reason": "Lock is valid and holder is alive",
                "lock_info": lock_info
            }), 409

        released = force_release_stale_lock()
        return jsonify({
            "released": released,
            "previous_lock": lock_info,
            "timestamp": datetime.now().isoformat()
        })
    except ImportError:
        return jsonify({
            "released": False,
            "error": "Lock module not available"
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/add', methods=['POST'])
def add_to_queue():
    """Add a mission to the queue."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        problem_statement = data.get("problem_statement") or data.get("mission")
        if not problem_statement:
            return jsonify({"error": "Missing problem_statement or mission"}), 400

        # Create queue entry
        entry = {
            "id": f"queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(_load_queue().get('missions', []))}",
            "problem_statement": problem_statement,
            "cycle_budget": data.get("cycle_budget", 3),
            "priority": data.get("priority", 0),  # 0 = normal, higher = more urgent
            "source": data.get("source", "dashboard"),  # dashboard, email, recommendation
            "source_id": data.get("source_id"),  # recommendation_id, email_id, etc.
            "project_name": data.get("project_name"),  # Optional project name for workspace
            "added_at": datetime.now().isoformat(),
            "status": "pending"
        }

        with _queue_lock:
            queue = _load_queue()
            queue["missions"].append(entry)
            # Sort by priority (higher first), then by added_at
            # Handle both numeric (legacy) and string priorities
            priority_weights = {"critical": 100, "high": 50, "normal": 0, "low": -50}
            def get_priority_weight(p):
                if isinstance(p, int):
                    return p
                return priority_weights.get(str(p).lower(), 0)
            queue["missions"].sort(
                key=lambda x: (-get_priority_weight(x.get("priority", 0)), x.get("added_at", ""))
            )
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "added",
            "entry": entry,
            "queue_length": len(queue["missions"])
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/remove/<queue_id>', methods=['DELETE'])
def remove_from_queue(queue_id):
    """Remove a mission from the queue."""
    try:
        with _queue_lock:
            queue = _load_queue()
            original_length = len(queue["missions"])
            queue["missions"] = [m for m in queue["missions"] if m.get("id") != queue_id]

            if len(queue["missions"]) == original_length:
                return jsonify({"error": "Mission not found in queue"}), 404

            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "removed",
            "queue_id": queue_id,
            "queue_length": len(queue["missions"])
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/clear', methods=['POST'])
def clear_queue():
    """Clear all missions from the queue."""
    try:
        with _queue_lock:
            queue = _load_queue()
            cleared_count = len(queue["missions"])
            queue["missions"] = []
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "cleared",
            "cleared_count": cleared_count
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/reorder', methods=['POST'])
def reorder_queue():
    """Reorder missions in the queue."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        new_order = data.get("order", [])  # List of queue_ids in desired order
        if not new_order:
            return jsonify({"error": "Missing order list"}), 400

        with _queue_lock:
            queue = _load_queue()

            # Create lookup by id
            missions_by_id = {m["id"]: m for m in queue["missions"]}

            # Reorder based on provided order
            new_missions = []
            for queue_id in new_order:
                if queue_id in missions_by_id:
                    new_missions.append(missions_by_id.pop(queue_id))

            # Add any remaining missions that weren't in the order list
            new_missions.extend(missions_by_id.values())

            queue["missions"] = new_missions
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "reordered",
            "queue_length": len(queue["missions"])
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/next', methods=['POST'])
def start_next_mission():
    """Start the next mission in the queue (if AtlasForge is not busy)."""
    # Acquire queue processing lock to prevent race conditions
    lock_acquired = False
    try:
        from queue_processing_lock import acquire_queue_lock, release_queue_lock
        lock_acquired = acquire_queue_lock(source="queue_next_api", timeout=2, blocking=False)
        if not lock_acquired:
            return jsonify({
                "error": "Queue processing in progress",
                "retry_after": 5
            }), 409
    except ImportError:
        pass  # Lock module not available, proceed without lock

    try:
        # Check if AtlasForge is currently running
        from io_utils import atomic_read_json
        mission = atomic_read_json(STATE_DIR / "mission.json") or {}
        current_stage = mission.get("current_stage", "COMPLETE")

        if current_stage not in ["COMPLETE", None, ""]:
            return jsonify({
                "error": "AtlasForge is currently busy",
                "current_stage": current_stage
            }), 409

        with _queue_lock:
            queue = _load_queue()

            if not queue["missions"]:
                return jsonify({"error": "Queue is empty"}), 404

            # Get next mission
            next_mission = queue["missions"][0]

            # Remove from queue
            queue["missions"] = queue["missions"][1:]
            _save_queue(queue)

        # Start the mission by writing to mission.json
        import uuid
        from datetime import datetime

        mission_id = f"mission_{uuid.uuid4().hex[:8]}"

        # Determine workspace based on project_name
        project_name = next_mission.get("project_name")
        if project_name:
            # Use project-specific workspace in the main workspace directory
            mission_workspace = str(BASE_DIR / "workspace" / project_name)
        else:
            # Use mission-specific workspace
            mission_workspace = str(BASE_DIR / "missions" / mission_id / "workspace")

        new_mission = {
            "mission_id": mission_id,
            "problem_statement": next_mission["problem_statement"],
            "original_problem_statement": next_mission["problem_statement"],
            "preferences": {},
            "success_criteria": [],
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "cycle_started_at": datetime.now().isoformat(),
            "cycle_budget": next_mission.get("cycle_budget", 3),
            "current_cycle": 1,
            "cycle_history": [],
            "mission_workspace": mission_workspace,
            "mission_dir": str(BASE_DIR / "missions" / mission_id),
            "project_name": project_name,  # Preserve project name in mission
            "metadata": {
                "source": next_mission.get("source", "queue"),
                "source_id": next_mission.get("source_id"),
                "queue_id": next_mission.get("id")
            }
        }

        # Create mission directory
        mission_dir = Path(new_mission["mission_dir"])
        workspace_dir = Path(new_mission["mission_workspace"])
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "artifacts").mkdir(exist_ok=True)
        (workspace_dir / "research").mkdir(exist_ok=True)
        (workspace_dir / "tests").mkdir(exist_ok=True)

        # Save mission
        from io_utils import atomic_write_json
        atomic_write_json(STATE_DIR / "mission.json", new_mission)

        # Write auto-start signal so dashboard's queue_auto_start_watcher starts Claude
        signal_path = STATE_DIR / "queue_auto_start_signal.json"
        mission_title = (next_mission["problem_statement"][:80] + '...') if len(next_mission["problem_statement"]) > 80 else next_mission["problem_statement"]
        atomic_write_json(signal_path, {
            "action": "start_rd",
            "mission_id": mission_id,
            "mission_title": mission_title,
            "signaled_at": datetime.now().isoformat(),
            "source": "queue_next_button"
        })

        # Emit auto-start notification event
        try:
            from websocket_events import emit_mission_auto_started
            emit_mission_auto_started(
                mission_id=mission_id,
                mission_title=mission_title,
                queue_id=next_mission.get("id"),
                source="queue_next_button"
            )
        except ImportError:
            pass  # websocket_events not available

        # Emit WebSocket update
        _emit_queue_update(_load_queue())

        return jsonify({
            "status": "started",
            "mission_id": mission_id,
            "problem_statement": next_mission["problem_statement"][:100] + "...",
            "remaining_in_queue": len(queue["missions"])
        })
    except Exception as e:
        logger.error(f"Error starting next mission: {e}")
        return jsonify({"error": str(e)})
    finally:
        # Release queue processing lock
        if lock_acquired:
            try:
                from queue_processing_lock import release_queue_lock
                release_queue_lock()
            except ImportError:
                pass


@queue_scheduler_bp.route('/settings', methods=['GET'])
def get_settings():
    """Get queue settings."""
    try:
        queue = _load_queue()
        return jsonify(queue.get("settings", {}))
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/settings', methods=['PUT'])
def update_settings():
    """Update queue settings."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        with _queue_lock:
            queue = _load_queue()

            # Update allowed settings
            allowed = ["auto_start", "max_concurrent", "default_cycle_budget"]
            for key in allowed:
                if key in data:
                    queue["settings"][key] = data[key]

            _save_queue(queue)

        return jsonify({
            "status": "updated",
            "settings": queue["settings"]
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/from-kb-recommendation', methods=['POST'])
def add_from_kb_recommendation():
    """Add a mission to queue from a KB recommendation (Investigation-KB integration)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        recommendation_id = data.get("recommendation_id")
        if not recommendation_id:
            return jsonify({"error": "Missing recommendation_id"}), 400

        # Get KB recommendation details via the KB API
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        # Try to get the recommendation directly from the KB
        rec = None
        try:
            # The KB stores recommendations in its internal structures
            recs = kb._recommendations if hasattr(kb, '_recommendations') else {}
            rec = recs.get(recommendation_id)
        except Exception:
            pass

        # Fallback: Try the recommendations engine
        if not rec:
            try:
                from mission_recommendations import get_recommendation_engine
                engine = get_recommendation_engine()
                rec = engine.get_recommendation(recommendation_id)
            except Exception:
                pass

        # Final fallback: Load directly from recommendations.json
        if not rec:
            try:
                from io_utils import atomic_read_json
                recs_data = atomic_read_json(STATE_DIR / "kb_recommendations.json", {"recommendations": []})
                for r in recs_data.get("recommendations", []):
                    if r.get("recommendation_id") == recommendation_id:
                        rec = r
                        break
            except Exception:
                pass

        if not rec:
            return jsonify({"error": "Recommendation not found"}), 404

        # Extract problem statement from the recommendation
        problem_statement = rec.get("problem_statement") or rec.get("title") or "Untitled Mission"
        cycle_budget = data.get("cycle_budget") or rec.get("complexity_budget") or rec.get("estimated_cycles") or 3

        # Add to queue
        entry = {
            "id": f"queue_kbrec_{recommendation_id[:8]}_{datetime.now().strftime('%H%M%S')}",
            "problem_statement": problem_statement,
            "mission_title": rec.get("title", problem_statement[:80]),
            "mission_description": problem_statement,
            "cycle_budget": cycle_budget,
            "priority": data.get("priority", 1),  # KB recommendations get priority 1 by default
            "source": "kb_recommendation",
            "source_id": recommendation_id,
            "added_at": datetime.now().isoformat(),
            "status": "pending"
        }

        with _queue_lock:
            queue = _load_queue()
            queue["missions"].append(entry)
            queue["missions"].sort(
                key=lambda x: (-x.get("priority", 0), x.get("added_at", ""))
            )
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "added",
            "entry": entry,
            "queue_length": len(queue["missions"])
        })
    except Exception as e:
        logger.error(f"Error adding KB recommendation to queue: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/from-recommendation', methods=['POST'])
def add_from_recommendation():
    """Add a mission to queue from a recommendation."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        recommendation_id = data.get("recommendation_id")
        if not recommendation_id:
            return jsonify({"error": "Missing recommendation_id"}), 400

        # Get recommendation details
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()
        rec = engine.get_recommendation(recommendation_id)

        if not rec:
            return jsonify({"error": "Recommendation not found"}), 404

        # Convert to mission and add to queue
        cycle_budget = data.get("cycle_budget", rec.get("complexity_budget", 3))
        mission_data = engine.convert_to_mission(recommendation_id, cycle_budget=cycle_budget)

        if not mission_data:
            return jsonify({"error": "Failed to convert recommendation"}), 500

        # Add to queue
        entry = {
            "id": f"queue_rec_{recommendation_id}",
            "problem_statement": mission_data["problem_statement"],
            "cycle_budget": cycle_budget,
            "priority": data.get("priority", 1),  # Recommendations get priority 1 by default
            "source": "recommendation",
            "source_id": recommendation_id,
            "added_at": datetime.now().isoformat(),
            "status": "pending"
        }

        with _queue_lock:
            queue = _load_queue()
            queue["missions"].append(entry)
            queue["missions"].sort(
                key=lambda x: (-x.get("priority", 0), x.get("added_at", ""))
            )
            _save_queue(queue)

        # Mark recommendation as accepted
        engine.accept_recommendation(recommendation_id)

        return jsonify({
            "status": "added",
            "entry": entry,
            "queue_length": len(queue["missions"])
        })
    except Exception as e:
        return jsonify({"error": str(e)})


# =============================================================================
# PAUSE/RESUME ENDPOINTS
# =============================================================================

@queue_scheduler_bp.route('/pause', methods=['POST'])
def pause_queue():
    """Pause the queue - no new missions will start until resumed."""
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "Manually paused from dashboard")

        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        result = scheduler.pause_queue(reason)

        # Emit WebSocket update
        with _queue_lock:
            queue = _load_queue()
            if "settings" not in queue:
                queue["settings"] = {}
            queue["settings"]["paused"] = result["paused"]
            queue["settings"]["paused_at"] = result["paused_at"]
            queue["settings"]["pause_reason"] = result["pause_reason"]
            _save_queue(queue)

        if _socketio:
            _socketio.emit('queue_paused', result)

        return jsonify({
            "status": "paused",
            **result
        })
    except Exception as e:
        logger.error(f"Error pausing queue: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/resume', methods=['POST'])
def resume_queue():
    """Resume the queue after being paused."""
    try:
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        result = scheduler.resume_queue()

        # Emit WebSocket update
        with _queue_lock:
            queue = _load_queue()
            if "settings" not in queue:
                queue["settings"] = {}
            queue["settings"]["paused"] = False
            queue["settings"]["paused_at"] = None
            queue["settings"]["pause_reason"] = None
            _save_queue(queue)

        if _socketio:
            _socketio.emit('queue_resumed', result)

        return jsonify({
            "status": "resumed",
            **result
        })
    except Exception as e:
        logger.error(f"Error resuming queue: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/pause-state')
def get_pause_state():
    """Get current pause state."""
    try:
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        return jsonify(scheduler.get_pause_state())
    except Exception as e:
        return jsonify({"error": str(e)})


# =============================================================================
# TIMELINE AND ESTIMATION ENDPOINTS
# =============================================================================

@queue_scheduler_bp.route('/timeline')
def get_timeline():
    """Get queue timeline with estimated start/end times for each mission."""
    try:
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        timeline = scheduler.get_queue_timeline()

        return jsonify({
            "timeline": timeline,
            "generated_at": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting timeline: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/estimate/<queue_id>')
def get_duration_estimate(queue_id):
    """Get duration estimate for a specific queue item."""
    try:
        with _queue_lock:
            queue = _load_queue()

        # Find the queue item
        item = None
        for m in queue.get("missions", []):
            if m.get("id") == queue_id:
                item = m
                break

        if not item:
            return jsonify({"error": "Queue item not found"}), 404

        # Get historical estimate
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        estimated_minutes = scheduler.estimate_duration_from_history(
            item.get("problem_statement", ""),
            item.get("cycle_budget", 3)
        )

        return jsonify({
            "queue_id": queue_id,
            "estimated_minutes": estimated_minutes,
            "estimated_hours": round(estimated_minutes / 60, 2),
            "cycle_budget": item.get("cycle_budget", 3)
        })
    except Exception as e:
        logger.error(f"Error getting estimate: {e}")
        return jsonify({"error": str(e)})


# =============================================================================
# DEPENDENCY SUGGESTIONS ENDPOINTS
# =============================================================================

@queue_scheduler_bp.route('/suggestions')
def get_suggestions():
    """Get smart reordering suggestions based on mission dependencies."""
    try:
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        suggestions = scheduler.suggest_dependencies()

        # Emit WebSocket event if suggestions are available
        if _socketio and suggestions:
            _socketio.emit('suggestions_available', {
                'count': len(suggestions),
                'hint': suggestions[0].get('reason', ''),
                'top_confidence': suggestions[0].get('confidence', 0) if suggestions else 0
            })

        return jsonify({
            "suggestions": suggestions,
            "count": len(suggestions),
            "generated_at": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting suggestions: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/suggestions/apply', methods=['POST'])
def apply_suggestion():
    """Apply a dependency suggestion to reorder the queue."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        result = scheduler.apply_suggestion(data)

        if result:
            # Emit WebSocket update
            queue = _load_queue()
            _emit_queue_update(queue)

            return jsonify({
                "status": "applied",
                "reordered": True
            })
        else:
            return jsonify({
                "status": "no_change",
                "reordered": False,
                "message": "Suggestion did not require reordering"
            })
    except Exception as e:
        logger.error(f"Error applying suggestion: {e}")
        return jsonify({"error": str(e)})


# =============================================================================
# ENHANCED ADD ENDPOINT WITH PRIORITY AND SCHEDULING
# =============================================================================

@queue_scheduler_bp.route('/add-enhanced', methods=['POST'])
def add_to_queue_enhanced():
    """Add a mission to the queue with full priority and scheduling options."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        problem_statement = data.get("problem_statement") or data.get("mission")
        if not problem_statement:
            return jsonify({"error": "Missing problem_statement or mission"}), 400

        # Parse priority (accept string like "critical", "high", "normal", "low")
        priority_str = data.get("priority", "normal")
        if isinstance(priority_str, int):
            # Convert legacy numeric priority to string
            priority_map = {0: "normal", 1: "high", 2: "critical"}
            priority_str = priority_map.get(priority_str, "normal")

        # Get duration estimate
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        cycle_budget = data.get("cycle_budget", 3)
        estimated_minutes = scheduler.estimate_duration_from_history(problem_statement, cycle_budget)

        # Create enhanced queue entry
        entry = {
            "id": f"queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(_load_queue().get('missions', []))}",
            "problem_statement": problem_statement,
            "mission_title": (problem_statement[:80] + "...") if len(problem_statement) > 80 else problem_statement,
            "mission_description": problem_statement,
            "cycle_budget": cycle_budget,
            "priority": priority_str,
            "scheduled_start": data.get("scheduled_start"),  # ISO datetime or None
            "start_condition": data.get("start_condition"),  # e.g., "idle_after:17:00"
            "depends_on": data.get("depends_on"),  # mission_id
            "estimated_minutes": estimated_minutes,
            "source": data.get("source", "dashboard"),
            "source_id": data.get("source_id"),
            "project_name": data.get("project_name"),  # Optional project name for workspace
            "added_at": datetime.now().isoformat(),
            "status": "pending",
            "tags": data.get("tags", [])
        }

        # Priority weight for sorting
        priority_weights = {"critical": 100, "high": 50, "normal": 0, "low": -50}
        priority_weight = priority_weights.get(priority_str, 0)

        with _queue_lock:
            queue = _load_queue()
            queue["missions"].append(entry)

            # Sort by priority weight (higher first), then by scheduled start, then by added_at
            def sort_key(x):
                p_weight = priority_weights.get(x.get("priority", "normal"), 0)
                scheduled = x.get("scheduled_start") or "9999"  # None goes to end
                added = x.get("added_at", "")
                return (-p_weight, scheduled, added)

            queue["missions"].sort(key=sort_key)
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        # Find position in queue
        position = next(
            (i + 1 for i, m in enumerate(queue["missions"]) if m["id"] == entry["id"]),
            len(queue["missions"])
        )

        return jsonify({
            "status": "added",
            "entry": entry,
            "queue_length": len(queue["missions"]),
            "position": position,
            "estimated_minutes": estimated_minutes
        })
    except Exception as e:
        logger.error(f"Error adding to queue (enhanced): {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/update/<queue_id>', methods=['PUT'])
def update_queue_item(queue_id):
    """Update a queue item's priority, schedule, or other properties."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        with _queue_lock:
            queue = _load_queue()

            # Find the item
            item = None
            for m in queue.get("missions", []):
                if m.get("id") == queue_id:
                    item = m
                    break

            if not item:
                return jsonify({"error": "Queue item not found"}), 404

            # Update allowed fields
            allowed_fields = [
                "priority", "scheduled_start", "start_condition",
                "depends_on", "cycle_budget", "tags", "problem_statement"
            ]
            for field in allowed_fields:
                if field in data:
                    item[field] = data[field]

            # Re-estimate duration if cycle_budget changed
            if "cycle_budget" in data:
                from mission_queue_scheduler import get_scheduler
                scheduler = get_scheduler()
                item["estimated_minutes"] = scheduler.estimate_duration_from_history(
                    item.get("problem_statement", ""),
                    data["cycle_budget"]
                )

            # Re-sort queue
            priority_weights = {"critical": 100, "high": 50, "normal": 0, "low": -50}

            def sort_key(x):
                p_weight = priority_weights.get(x.get("priority", "normal"), 0)
                scheduled = x.get("scheduled_start") or "9999"
                added = x.get("added_at", "")
                return (-p_weight, scheduled, added)

            queue["missions"].sort(key=sort_key)
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            "status": "updated",
            "item": item
        })
    except Exception as e:
        logger.error(f"Error updating queue item: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/statistics')
def get_queue_statistics():
    """Get comprehensive queue statistics."""
    try:
        from mission_queue_scheduler import get_scheduler
        scheduler = get_scheduler()
        stats = scheduler.get_statistics()

        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        return jsonify({"error": str(e)})


# =============================================================================
# QUEUE ANALYTICS ENDPOINT
# =============================================================================

@queue_scheduler_bp.route('/analytics')
def get_queue_analytics():
    """
    Get queue analytics: throughput, avg duration, utilization, success rate.

    Combines data from mission_analytics and mission logs to provide
    comprehensive queue performance metrics.
    """
    try:
        from mission_analytics import get_analytics
        analytics = get_analytics()

        # Get aggregate stats for different time periods
        stats_30d = analytics.get_aggregate_stats(days=30)
        stats_7d = analytics.get_aggregate_stats(days=7)
        recent = analytics.get_recent_missions(limit=20)

        # Calculate derived metrics
        missions_7d = stats_7d.get("totals", {}).get("missions", 0)
        missions_30d = stats_30d.get("totals", {}).get("missions", 0)

        # Throughput: missions per day (7-day average)
        throughput_daily = missions_7d / 7 if missions_7d else 0

        # Average duration from 30-day data
        avg_duration_sec = stats_30d.get("totals", {}).get("avg_mission_duration_seconds", 0)
        avg_duration_min = avg_duration_sec / 60 if avg_duration_sec else 0

        # Calculate success rate from status breakdown
        status_breakdown = stats_30d.get("status_breakdown", {})
        total_missions = sum(status_breakdown.values()) if status_breakdown else 0
        completed_count = status_breakdown.get("COMPLETE", 0)
        success_rate = (completed_count / total_missions * 100) if total_missions > 0 else 0

        # Calculate average cycles per mission
        total_cycles = stats_30d.get("totals", {}).get("total_cycles", 0)
        avg_cycles = total_cycles / missions_30d if missions_30d > 0 else 0

        # Get current queue info for utilization
        with _queue_lock:
            queue = _load_queue()
        queue_length = len(queue.get("missions", []))

        # Estimate queue utilization (how much work is queued vs capacity)
        # Assume capacity is ~8 missions/day based on avg duration
        daily_capacity = 8
        utilization = min(100, (queue_length / daily_capacity * 100)) if daily_capacity > 0 else 0

        # Recent mission performance (last 5)
        recent_summary = []
        for m in (recent or [])[:5]:
            recent_summary.append({
                "mission_id": m.get("mission_id", ""),
                "status": m.get("status", "unknown"),
                "duration_minutes": round(m.get("duration_seconds", 0) / 60, 1),
                "cycles": m.get("cycles", 0),
                "completed_at": m.get("completed_at", "")
            })

        return jsonify({
            "throughput_daily": round(throughput_daily, 1),
            "avg_duration_minutes": round(avg_duration_min, 1),
            "success_rate_percent": round(success_rate, 1),
            "avg_cycles_per_mission": round(avg_cycles, 1),
            "missions_7d": missions_7d,
            "missions_30d": missions_30d,
            "queue_length": queue_length,
            "utilization_percent": round(utilization, 1),
            "by_status": status_breakdown,
            "recent_missions": recent_summary,
            "generated_at": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting queue analytics: {e}")
        return jsonify({
            "error": str(e),
            "throughput_daily": 0,
            "avg_duration_minutes": 0,
            "success_rate_percent": 0,
            "missions_7d": 0,
            "missions_30d": 0
        })


# =============================================================================
# QUEUE HEALTH ENDPOINT
# =============================================================================

@queue_scheduler_bp.route('/health')
def get_queue_health():
    """
    Get queue health metrics: blocked, stale, and conflicting missions.

    Returns:
        - blocked: Missions blocked by failed/incomplete dependencies
        - stale: Missions queued for more than the threshold (default 24h)
        - conflicts: Scheduling conflicts (overlapping scheduled_start times)
        - health_score: 0-100 score based on issues found
    """
    try:
        queue = _load_queue()
        missions = queue.get("missions", [])
        now = datetime.now()
        stale_threshold_hours = 24  # Configurable

        blocked = []
        stale = []
        conflicts = []

        # Build mission lookup for dependency checking
        mission_ids = {m['id'] for m in missions}

        for m in missions:
            # Check if blocked by dependency
            if m.get('depends_on'):
                dep_id = m['depends_on']
                # Check if dependency exists and its status
                if dep_id not in mission_ids:
                    # Dependency is not in queue - might have completed or failed
                    # Check mission logs for status
                    try:
                        from io_utils import atomic_read_json
                        mission_logs_dir = BASE_DIR / "missions" / "mission_logs"
                        if mission_logs_dir.exists():
                            # Look for the dependency in completed missions
                            dep_found = False
                            dep_status = 'unknown'
                            for log_file in mission_logs_dir.glob("*.json"):
                                try:
                                    log_data = atomic_read_json(log_file)
                                    if log_data and log_data.get("mission_id") == dep_id:
                                        dep_found = True
                                        dep_status = log_data.get("status", "unknown")
                                        break
                                except:
                                    pass

                            if dep_found and dep_status not in ["COMPLETE", "completed"]:
                                blocked.append({
                                    'id': m['id'],
                                    'reason': f"Dependency {dep_id[:12]}... failed/incomplete (status: {dep_status})"
                                })
                            elif not dep_found:
                                # Dependency not found anywhere - might be invalid
                                blocked.append({
                                    'id': m['id'],
                                    'reason': f"Dependency {dep_id[:12]}... not found"
                                })
                    except Exception as e:
                        logger.debug(f"Error checking dependency status: {e}")

            # Check if stale
            if m.get('added_at'):
                try:
                    added = datetime.fromisoformat(m['added_at'].replace('Z', '+00:00').replace('+00:00', ''))
                    hours_queued = (now - added).total_seconds() / 3600
                    if hours_queued > stale_threshold_hours:
                        stale.append({
                            'id': m['id'],
                            'hours_queued': round(hours_queued, 1)
                        })
                except Exception as e:
                    logger.debug(f"Error parsing added_at: {e}")

        # Check for scheduling conflicts (overlapping scheduled_start times)
        scheduled = [(m['id'], m['scheduled_start']) for m in missions if m.get('scheduled_start')]
        scheduled.sort(key=lambda x: x[1])

        # Detect overlaps (within 30 min window)
        for i in range(len(scheduled) - 1):
            try:
                t1 = datetime.fromisoformat(scheduled[i][1].replace('Z', '+00:00').replace('+00:00', ''))
                t2 = datetime.fromisoformat(scheduled[i+1][1].replace('Z', '+00:00').replace('+00:00', ''))
                diff_minutes = abs((t2 - t1).total_seconds() / 60)
                if diff_minutes < 30:
                    conflicts.append({
                        'missions': [scheduled[i][0], scheduled[i+1][0]],
                        'description': f"Missions scheduled within {int(diff_minutes)}min of each other",
                        'times': [scheduled[i][1], scheduled[i+1][1]]
                    })
            except Exception as e:
                logger.debug(f"Error checking scheduling conflict: {e}")

        total_issues = len(blocked) + len(stale) + len(conflicts)
        health_score = max(0, 100 - (total_issues * 15))  # -15 per issue

        return jsonify({
            'blocked': blocked,
            'stale': stale,
            'conflicts': conflicts,
            'total_issues': total_issues,
            'health_score': health_score,
            'stale_threshold_hours': stale_threshold_hours,
            'generated_at': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting queue health: {e}")
        return jsonify({
            'error': str(e),
            'blocked': [],
            'stale': [],
            'conflicts': [],
            'total_issues': 0,
            'health_score': 100
        })


# =============================================================================
# DEPENDENCY TREE ENDPOINT
# =============================================================================

@queue_scheduler_bp.route('/dependency-tree')
def get_dependency_tree():
    """Get dependency tree structure for visualization."""
    try:
        queue = _load_queue()
        missions = queue.get("missions", [])

        # Build nodes and edges
        nodes = []
        edges = []

        for m in missions:
            # Determine status
            status = 'ready'
            if m.get('depends_on'):
                # Check if dependency is in queue
                dep_in_queue = any(other['id'] == m['depends_on'] for other in missions)
                if dep_in_queue:
                    status = 'waiting'
                else:
                    status = 'blocked'  # Dependency not in queue

            nodes.append({
                'id': m['id'],
                'title': (m.get('mission_title') or m.get('problem_statement', ''))[:40],
                'priority': m.get('priority', 'normal'),
                'status': status,
                'depends_on': m.get('depends_on')
            })

            if m.get('depends_on'):
                edges.append({
                    'from': m['depends_on'],
                    'to': m['id']
                })

        return jsonify({
            'nodes': nodes,
            'edges': edges,
            'generated_at': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting dependency tree: {e}")
        return jsonify({
            'error': str(e),
            'nodes': [],
            'edges': []
        })


# =============================================================================
# BULK OPERATIONS ENDPOINTS
# =============================================================================

@queue_scheduler_bp.route('/bulk/priority', methods=['POST'])
def bulk_update_priority():
    """Update priority for multiple queue items."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        queue_ids = set(data.get('queue_ids', []))
        priority = data.get('priority', 'normal')

        if not queue_ids:
            return jsonify({"error": "No queue_ids provided"}), 400

        # Validate priority
        valid_priorities = ['critical', 'high', 'normal', 'low']
        if priority not in valid_priorities:
            return jsonify({"error": f"Invalid priority. Must be one of: {valid_priorities}"}), 400

        updated = 0
        with _queue_lock:
            queue = _load_queue()
            for m in queue.get("missions", []):
                if m['id'] in queue_ids:
                    m['priority'] = priority
                    updated += 1

            # Re-sort by priority
            priority_weights = {"critical": 100, "high": 50, "normal": 0, "low": -50}

            def sort_key(x):
                p_weight = priority_weights.get(x.get("priority", "normal"), 0)
                scheduled = x.get("scheduled_start") or "9999"
                added = x.get("added_at", "")
                return (-p_weight, scheduled, added)

            queue["missions"].sort(key=sort_key)
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            'status': 'updated',
            'count': updated,
            'priority': priority
        })
    except Exception as e:
        logger.error(f"Error in bulk priority update: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/bulk/delete', methods=['POST'])
def bulk_delete():
    """Delete multiple queue items."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        queue_ids = set(data.get('queue_ids', []))
        if not queue_ids:
            return jsonify({"error": "No queue_ids provided"}), 400

        deleted = 0
        with _queue_lock:
            queue = _load_queue()
            original = len(queue["missions"])
            queue["missions"] = [m for m in queue["missions"] if m['id'] not in queue_ids]
            deleted = original - len(queue["missions"])
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            'status': 'deleted',
            'count': deleted
        })
    except Exception as e:
        logger.error(f"Error in bulk delete: {e}")
        return jsonify({"error": str(e)})


@queue_scheduler_bp.route('/bulk/dependency', methods=['POST'])
def bulk_add_dependency():
    """Add dependency to multiple queue items."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing request body"}), 400

        queue_ids = set(data.get('queue_ids', []))
        depends_on = data.get('depends_on')

        if not queue_ids:
            return jsonify({"error": "No queue_ids provided"}), 400

        updated = 0
        with _queue_lock:
            queue = _load_queue()
            for m in queue.get("missions", []):
                if m['id'] in queue_ids and m['id'] != depends_on:
                    m['depends_on'] = depends_on if depends_on else None
                    updated += 1
            _save_queue(queue)

        # Emit WebSocket update
        _emit_queue_update(queue)

        return jsonify({
            'status': 'updated',
            'count': updated,
            'depends_on': depends_on
        })
    except Exception as e:
        logger.error(f"Error in bulk add dependency: {e}")
        return jsonify({"error": str(e)})
