"""
Recovery and Decision Graph API Routes Blueprint

Contains routes for:
- Crash recovery check and management
- Decision graph visualization
- Decision graph export
"""

from flask import Blueprint, jsonify, request, Response
from datetime import datetime
import json

# Create Blueprint
recovery_bp = Blueprint('recovery', __name__)

# Configuration - set via init function
MISSION_PATH = None
io_utils = None


def init_recovery_blueprint(mission_path, io_utils_module):
    """Initialize the recovery blueprint with required dependencies."""
    global MISSION_PATH, io_utils
    MISSION_PATH = mission_path
    io_utils = io_utils_module


# =============================================================================
# CRASH RECOVERY ROUTES
# =============================================================================

@recovery_bp.route('/api/recovery/check')
def api_recovery_check():
    """Check for incomplete missions that need recovery."""
    try:
        from stage_checkpoint_recovery import MissionRecoveryManager, detect_incomplete_mission

        # Check current mission
        recovery = detect_incomplete_mission()
        if recovery:
            checkpoint = recovery.get_latest_checkpoint()
            if checkpoint:
                return jsonify({
                    "recovery_available": True,
                    "mission_id": checkpoint.mission_id,
                    "stage": checkpoint.stage,
                    "timestamp": checkpoint.timestamp,
                    "files_created": checkpoint.files_created,
                    "recovery_hint": checkpoint.recovery_hint,
                    "iteration": checkpoint.iteration,
                    "cycle": checkpoint.cycle
                })

        # Also check for any incomplete missions (but don't trigger recovery modal)
        manager = MissionRecoveryManager()
        incomplete = manager.detect_incomplete_missions()

        if incomplete:
            checkpoints = []
            for mission_id, stage, checkpoint in incomplete:
                checkpoints.append({
                    "mission_id": mission_id,
                    "stage": stage,
                    "timestamp": checkpoint.timestamp,
                    "recovery_hint": checkpoint.recovery_hint
                })
            # NOTE: recovery_available=False because current mission doesn't need recovery
            # The incomplete_missions list is for informational purposes only
            return jsonify({
                "recovery_available": False,
                "incomplete_missions": checkpoints
            })

        return jsonify({"recovery_available": False})
    except Exception as e:
        return jsonify({"error": str(e), "recovery_available": False})


@recovery_bp.route('/api/recovery/context')
def api_recovery_context():
    """Get the recovery context for current mission."""
    try:
        from stage_checkpoint_recovery import get_recovery_context
        context = get_recovery_context()
        return jsonify({"context": context, "has_context": bool(context)})
    except Exception as e:
        return jsonify({"error": str(e), "context": "", "has_context": False})


@recovery_bp.route('/api/recovery/dismiss', methods=['POST'])
def api_recovery_dismiss():
    """Dismiss recovery and start fresh."""
    try:
        from stage_checkpoint_recovery import clear_current_checkpoint
        clear_current_checkpoint()
        return jsonify({"success": True, "message": "Recovery checkpoint cleared"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@recovery_bp.route('/api/recovery/clean', methods=['POST'])
def api_recovery_clean():
    """Clean old checkpoints."""
    try:
        from stage_checkpoint_recovery import MissionRecoveryManager
        data = request.get_json() or {}
        max_age_days = data.get('max_age_days', 7)

        manager = MissionRecoveryManager()
        manager.clean_old_checkpoints(max_age_days=max_age_days)
        return jsonify({"success": True, "message": f"Cleaned checkpoints older than {max_age_days} days"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# =============================================================================
# DECISION GRAPH ROUTES
# =============================================================================

@recovery_bp.route('/api/decision-graph/missions')
def api_decision_graph_missions():
    """Get list of missions with decision graph data."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        missions = logger.get_missions_with_graphs(limit=20)
        return jsonify({"missions": missions})
    except Exception as e:
        return jsonify({"error": str(e), "missions": []})


@recovery_bp.route('/api/decision-graph/<mission_id>')
def api_decision_graph(mission_id):
    """Get decision graph for a mission."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        graph = logger.get_mission_graph(mission_id)
        return jsonify(graph)
    except Exception as e:
        return jsonify({"error": str(e), "nodes": [], "edges": []})


@recovery_bp.route('/api/decision-graph/<mission_id>/summary')
def api_decision_graph_summary(mission_id):
    """Get decision graph summary for a mission."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        summary = logger.get_mission_summary(mission_id)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)})


@recovery_bp.route('/api/decision-graph/<mission_id>/node/<node_id>')
def api_decision_graph_node(mission_id, node_id):
    """Get details for a specific node in decision graph."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        invocation = logger.get_invocation_details(node_id)
        if invocation:
            return jsonify(invocation.to_dict())
        return jsonify({"error": "Node not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)})


@recovery_bp.route('/api/decision-graph/<mission_id>/failures')
def api_decision_graph_failures(mission_id):
    """Get failure points in decision graph."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        failures = logger.get_failure_points(mission_id)
        return jsonify({"failures": failures})
    except Exception as e:
        return jsonify({"error": str(e), "failures": []})


@recovery_bp.route('/api/decision-graph/<mission_id>/patterns')
def api_decision_graph_patterns(mission_id):
    """Get unusual patterns detected in decision graph."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        patterns = logger.get_unusual_patterns(mission_id)
        return jsonify({"patterns": patterns})
    except Exception as e:
        return jsonify({"error": str(e), "patterns": []})


@recovery_bp.route('/api/decision-graph/current')
def api_decision_graph_current():
    """Get decision graph for current mission."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()

        # Try current mission first
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get("mission_id")

        if mission_id:
            graph = logger.get_mission_graph(mission_id)
            if graph.get('nodes'):
                graph['mission_id'] = mission_id
                graph['is_current'] = True
                return jsonify(graph)

        # Fall back to most recent mission with data
        missions = logger.get_missions_with_graphs()
        if missions:
            best_mission = max(missions, key=lambda m: m.get('total_invocations', 0))
            fallback_id = best_mission['mission_id']
            graph = logger.get_mission_graph(fallback_id)
            graph['mission_id'] = fallback_id
            graph['is_current'] = False
            graph['note'] = f"Showing data from {fallback_id} (current mission has no decision data yet)"
            return jsonify(graph)

        return jsonify({
            "nodes": [],
            "edges": [],
            "stats": {"total": 0, "errors": 0},
            "note": "No decision graph data available. Data is populated from archived transcripts."
        })
    except Exception as e:
        return jsonify({"error": str(e), "nodes": [], "edges": []})


@recovery_bp.route('/api/decision-graph/<mission_id>/export')
def api_decision_graph_export(mission_id):
    """Export decision graph data as JSON or CSV."""
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
        graph = logger.get_mission_graph(mission_id)

        export_format = request.args.get('format', 'json').lower()

        if export_format == 'csv':
            import csv
            import io

            output = io.StringIO()
            fieldnames = ['id', 'tool_name', 'stage', 'timestamp', 'status', 'duration_ms', 'error_message']
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()

            for node in graph.get('nodes', []):
                writer.writerow({
                    'id': node.get('id', ''),
                    'tool_name': node.get('tool_name', ''),
                    'stage': node.get('stage', ''),
                    'timestamp': node.get('timestamp', ''),
                    'status': node.get('status', ''),
                    'duration_ms': node.get('duration_ms', 0),
                    'error_message': node.get('error_message', '')
                })

            csv_content = output.getvalue()
            return Response(
                csv_content,
                mimetype='text/csv',
                headers={
                    'Content-Disposition': f'attachment; filename={mission_id}_decision_graph.csv'
                }
            )

        # Default to JSON
        return jsonify({
            'mission_id': mission_id,
            'exported_at': datetime.now().isoformat(),
            'graph': graph
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# MISSION SNAPSHOT ROUTES
# =============================================================================

@recovery_bp.route('/api/recovery/snapshots')
def api_recovery_snapshots():
    """List all available snapshots."""
    try:
        from mission_snapshot_manager import get_snapshot_manager
        manager = get_snapshot_manager()
        snapshots = manager.list_snapshots()
        return jsonify({
            "snapshots": [s.to_dict() for s in snapshots],
            "count": len(snapshots)
        })
    except ImportError:
        return jsonify({"error": "Snapshot module not available", "snapshots": []})
    except Exception as e:
        return jsonify({"error": str(e), "snapshots": []})


@recovery_bp.route('/api/recovery/snapshots/<snapshot_id>')
def api_recovery_snapshot_detail(snapshot_id):
    """Get details for a specific snapshot."""
    try:
        from mission_snapshot_manager import get_snapshot_manager
        manager = get_snapshot_manager()
        snapshot = manager.get_snapshot_by_id(snapshot_id)
        if snapshot:
            # Include verification status
            verified = manager.verify_snapshot(snapshot_id)
            data = snapshot.to_dict()
            data['verified'] = verified
            return jsonify(data)
        return jsonify({"error": "Snapshot not found"}), 404
    except ImportError:
        return jsonify({"error": "Snapshot module not available"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@recovery_bp.route('/api/recovery/restore', methods=['POST'])
def api_recovery_restore():
    """Restore mission state from a snapshot."""
    try:
        from mission_snapshot_manager import get_snapshot_manager
        data = request.get_json() or {}
        snapshot_id = data.get('snapshot_id')

        if not snapshot_id:
            return jsonify({"success": False, "error": "snapshot_id required"})

        manager = get_snapshot_manager()

        # Verify integrity before restore
        if not manager.verify_snapshot(snapshot_id):
            return jsonify({
                "success": False,
                "error": "Snapshot integrity verification failed"
            })

        success = manager.restore_snapshot(snapshot_id)
        if success:
            return jsonify({
                "success": True,
                "message": f"Restored from snapshot {snapshot_id}"
            })
        return jsonify({"success": False, "error": "Restore failed"})
    except ImportError:
        return jsonify({"success": False, "error": "Snapshot module not available"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@recovery_bp.route('/api/recovery/backup-status')
def api_recovery_backup_status():
    """Get current backup health status."""
    try:
        from mission_snapshot_manager import (
            get_snapshot_manager,
            get_stale_backup_monitor,
            StaleBackupMonitor
        )

        manager = get_snapshot_manager()
        monitor = get_stale_backup_monitor()

        latest = manager.get_latest_snapshot()
        is_stale = monitor.check_staleness()

        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        is_active = mission.get('current_stage') not in (None, '', 'COMPLETE')

        return jsonify({
            "latest_snapshot": latest.to_dict() if latest else None,
            "is_stale": is_stale,
            "stale_threshold_seconds": StaleBackupMonitor.STALE_THRESHOLD,
            "is_mission_active": is_active,
            "snapshot_count": len(manager.list_snapshots()),
            "timestamp": datetime.now().isoformat()
        })
    except ImportError:
        return jsonify({"error": "Snapshot module not available"})
    except Exception as e:
        return jsonify({"error": str(e)})


@recovery_bp.route('/api/recovery/create-snapshot', methods=['POST'])
def api_recovery_create_snapshot():
    """Manually create a snapshot."""
    try:
        from mission_snapshot_manager import get_snapshot_manager
        data = request.get_json() or {}
        stage_hint = data.get('stage_hint', 'Manual snapshot via API')

        manager = get_snapshot_manager()
        snapshot = manager.create_snapshot(stage_hint=stage_hint)

        if snapshot:
            return jsonify({
                "success": True,
                "snapshot": snapshot.to_dict()
            })
        return jsonify({"success": False, "error": "Failed to create snapshot"})
    except ImportError:
        return jsonify({"success": False, "error": "Snapshot module not available"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@recovery_bp.route('/api/recovery/coordinated-snapshot', methods=['POST'])
def api_recovery_coordinated_snapshot():
    """Create a coordinated snapshot including sub-repo states."""
    try:
        from mission_snapshot_manager import get_snapshot_manager
        data = request.get_json() or {}
        sub_repos = data.get('sub_repos')

        manager = get_snapshot_manager()
        snapshot = manager.create_coordinated_snapshot(sub_repos=sub_repos)

        if snapshot:
            return jsonify({
                "success": True,
                "snapshot": snapshot.to_dict()
            })
        return jsonify({"success": False, "error": "Failed to create coordinated snapshot"})
    except ImportError:
        return jsonify({"success": False, "error": "Snapshot module not available"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@recovery_bp.route('/api/recovery/diff/<snapshot_id>')
def api_recovery_diff(snapshot_id):
    """Get diff between current mission state and snapshot."""
    try:
        from mission_snapshot_manager import get_snapshot_manager

        manager = get_snapshot_manager()
        snapshot = manager.get_snapshot_by_id(snapshot_id)

        if not snapshot:
            return jsonify({"error": "Snapshot not found"}), 404

        # Read current mission.json
        current = io_utils.atomic_read_json(MISSION_PATH, {})

        # Read snapshot mission state
        snapshot_state = manager.get_snapshot_content(snapshot_id)
        if not snapshot_state:
            return jsonify({"error": "Could not read snapshot content"}), 500

        # Calculate differences
        changes = []
        all_keys = set(current.keys()) | set(snapshot_state.keys())

        for key in sorted(all_keys):
            current_val = current.get(key)
            snapshot_val = snapshot_state.get(key)

            if current_val != snapshot_val:
                changes.append({
                    "field": key,
                    "current": str(current_val)[:200] if current_val is not None else "(missing)",
                    "snapshot": str(snapshot_val)[:200] if snapshot_val is not None else "(missing)",
                    "type": "modified" if key in current and key in snapshot_state else
                            ("added" if key not in snapshot_state else "removed")
                })

        return jsonify({
            "snapshot_id": snapshot_id,
            "snapshot_stage": snapshot.stage,
            "snapshot_timestamp": snapshot.timestamp,
            "changes": changes,
            "change_count": len(changes)
        })
    except ImportError:
        return jsonify({"error": "Snapshot module not available"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
