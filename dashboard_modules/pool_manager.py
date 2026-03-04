"""
Pool Manager Dashboard Blueprint
=================================

Flask Blueprint providing API endpoints for the Subagent Pool Manager.

Endpoints:
    GET  /api/pool/status           - Live pool utilization snapshot
    GET  /api/pool/recommend        - Resource-based + KB subagent count recommendation
    POST /api/pool/notify-complete  - Mark investigation complete, release all its slots
    GET  /api/pool/history          - Past allocation outcomes from KB
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger("dashboard.pool_manager")

pool_manager_bp = Blueprint("pool_manager", __name__)

_socketio = None


def init_pool_manager_blueprint(socketio_instance=None) -> None:
    """Initialize the pool manager blueprint with SocketIO instance."""
    global _socketio
    _socketio = socketio_instance

    try:
        from subagent_pool_manager import get_pool_manager
        pool = get_pool_manager()
        if socketio_instance is not None:
            pool.set_socketio(socketio_instance)
        logger.info("[PoolManagerBP] Initialized; pool manager ready")
    except Exception as exc:
        logger.warning(f"[PoolManagerBP] Could not initialize pool manager: {exc}")


@pool_manager_bp.route("/api/pool/status")
def api_pool_status():
    """GET /api/pool/status — Live pool utilization snapshot."""
    try:
        from subagent_pool_manager import get_pool_manager
        pool = get_pool_manager()
        status = pool.get_status()
        return jsonify(status.to_dict())
    except Exception as exc:
        logger.error(f"[PoolManagerBP] /api/pool/status error: {exc}")
        return jsonify({
            "total_slots": 50,
            "active_slots": 0,
            "idle_slots": 50,
            "active_investigations": 0,
            "investigations": [],
            "timestamp": "",
            "error": str(exc),
        })


@pool_manager_bp.route("/api/pool/recommend")
def api_pool_recommend():
    """
    GET /api/pool/recommend?query=<text>

    Returns a resource-based subagent count recommendation, optionally
    augmented with a KB-learned recommendation from past allocations.
    """
    query = request.args.get("query", "").strip()

    try:
        from subagent_pool_manager import get_resource_detector, get_allocation_learner

        detector = get_resource_detector()
        suggestion = detector.suggest_optimal_count(query=query)
        result = suggestion.to_dict()

        kb_rec = {"available": False}
        if query:
            try:
                learner = get_allocation_learner()
                kb_count = learner.recommend_count(query)
                if kb_count is not None:
                    kb_rec = {
                        "available": True,
                        "suggested": kb_count,
                        "note": "Based on past successful allocations for similar queries",
                    }
            except Exception as kb_exc:
                logger.debug(f"[PoolManagerBP] KB recommend failed: {kb_exc}")

        result["kb_recommendation"] = kb_rec
        return jsonify(result)

    except Exception as exc:
        logger.error(f"[PoolManagerBP] /api/pool/recommend error: {exc}")
        return jsonify({
            "suggested": 5,
            "min_suggested": 2,
            "max_suggested": 10,
            "rationale": f"Fallback (resource detection failed: {exc})",
            "resource_profile": {},
            "kb_recommendation": {"available": False},
            "error": str(exc),
        })


@pool_manager_bp.route("/api/pool/notify-complete", methods=["POST"])
def api_pool_notify_complete():
    """
    POST /api/pool/notify-complete

    Mark an investigation as complete, releasing all its pool slots and
    recording an allocation outcome to the KB.

    Request body:
        {"investigation_id": "inv_abc123", "elapsed_sec": 45.2, "success": true}
    """
    data = request.get_json() or {}
    inv_id = data.get("investigation_id", "").strip()

    if not inv_id:
        return jsonify({"success": False, "error": "investigation_id required"}), 400

    try:
        from subagent_pool_manager import get_pool_manager
        pool = get_pool_manager()
        pool.notify_investigation_complete(
            inv_id=inv_id,
            elapsed_sec=float(data.get("elapsed_sec", 0.0)),
            success=bool(data.get("success", True)),
        )
        return jsonify({"success": True, "investigation_id": inv_id})
    except Exception as exc:
        logger.error(f"[PoolManagerBP] /api/pool/notify-complete error: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@pool_manager_bp.route("/api/pool/history")
def api_pool_history():
    """GET /api/pool/history — Past allocation outcomes from KB."""
    limit = min(int(request.args.get("limit", 20)), 100)

    try:
        from subagent_pool_manager import get_allocation_learner
        learner = get_allocation_learner()
        history = learner.get_history(limit=limit)

        cleaned = [
            {
                "title": entry.get("title", ""),
                "description": entry.get("description", ""),
                "outcome": entry.get("outcome", ""),
                "timestamp": entry.get("timestamp", ""),
                "confidence_score": entry.get("confidence_score", 0.0),
            }
            for entry in history
        ]

        return jsonify({"history": cleaned, "count": len(cleaned)})
    except Exception as exc:
        logger.error(f"[PoolManagerBP] /api/pool/history error: {exc}")
        return jsonify({"history": [], "count": 0, "error": str(exc)})
