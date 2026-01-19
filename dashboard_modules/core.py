"""
Core Dashboard Routes Blueprint

Contains essential routes for:
- Status (Claude status, health check)
- Start/Stop controls
- Journal entries
- Mission management
- Proposals
- Recommendations
- Mission logs
- File downloads

These routes depend on functions from the main dashboard_v2.py module.
"""

from flask import Blueprint, jsonify, request, abort, send_file
from pathlib import Path
from datetime import datetime
import json
import mimetypes

# Create Blueprint
core_bp = Blueprint('core', __name__)

# Constants - will be set by init function
BASE_DIR = None
STATE_DIR = None
WORKSPACE_DIR = None
MISSION_PATH = None
PROPOSALS_PATH = None
RECOMMENDATIONS_PATH = None
MISSION_LOGS_DIR = None

# SQLite storage backend for suggestions
_suggestion_storage = None

def _get_suggestion_storage():
    """Get the SQLite suggestion storage backend (lazy import)."""
    global _suggestion_storage
    if _suggestion_storage is None:
        try:
            from suggestion_storage import get_storage
            _suggestion_storage = get_storage()
        except ImportError:
            _suggestion_storage = None
    return _suggestion_storage

# Function references - will be set by init function
io_utils = None
get_claude_status = None
start_claude = None
stop_claude = None
send_message_to_claude = None
get_recent_journal = None

# Narrative-specific functions
get_narrative_status = None
start_narrative = None
stop_narrative = None
send_message_to_narrative = None
get_narrative_chat_history = None
NARRATIVE_MISSION_PATH = None

# Mission queue
MISSION_QUEUE_PATH = None


def init_core_blueprint(
    base_dir, state_dir, workspace_dir,
    mission_path, proposals_path, recommendations_path,
    io_utils_module,
    status_fn, start_fn, stop_fn, send_msg_fn, journal_fn,
    narrative_status_fn=None, narrative_start_fn=None, narrative_stop_fn=None,
    narrative_send_msg_fn=None, narrative_chat_fn=None, narrative_mission_path=None,
    mission_queue_path=None
):
    """Initialize the core blueprint with required dependencies."""
    global BASE_DIR, STATE_DIR, WORKSPACE_DIR, MISSION_PATH, PROPOSALS_PATH, RECOMMENDATIONS_PATH
    global MISSION_LOGS_DIR, io_utils, MISSION_QUEUE_PATH
    global get_claude_status, start_claude, stop_claude, send_message_to_claude, get_recent_journal
    global get_narrative_status, start_narrative, stop_narrative, send_message_to_narrative
    global get_narrative_chat_history, NARRATIVE_MISSION_PATH

    BASE_DIR = base_dir
    STATE_DIR = state_dir
    WORKSPACE_DIR = workspace_dir
    MISSION_PATH = mission_path
    PROPOSALS_PATH = proposals_path
    RECOMMENDATIONS_PATH = recommendations_path
    MISSION_LOGS_DIR = base_dir / "missions" / "mission_logs"
    io_utils = io_utils_module

    get_claude_status = status_fn
    start_claude = start_fn
    stop_claude = stop_fn
    send_message_to_claude = send_msg_fn
    get_recent_journal = journal_fn

    # Narrative functions (optional)
    get_narrative_status = narrative_status_fn
    start_narrative = narrative_start_fn
    stop_narrative = narrative_stop_fn
    send_message_to_narrative = narrative_send_msg_fn
    get_narrative_chat_history = narrative_chat_fn
    NARRATIVE_MISSION_PATH = narrative_mission_path

    # Mission queue (optional)
    MISSION_QUEUE_PATH = mission_queue_path


# =============================================================================
# STATUS ROUTES
# =============================================================================

@core_bp.route('/api/status')
def api_status():
    return jsonify(get_claude_status())


@core_bp.route('/api/health')
def api_health():
    """Health check endpoint for verifying dashboard connectivity."""
    import time
    start = time.time()

    services = {
        "mission_file": False,
        "state_dir": False,
        "signal_file_writable": False
    }

    # Check mission file accessibility
    try:
        if MISSION_PATH.exists():
            with open(MISSION_PATH, 'r') as f:
                json.load(f)
            services["mission_file"] = True
        else:
            services["mission_file"] = True
    except Exception:
        services["mission_file"] = False

    # Check state directory is writable
    try:
        test_file = STATE_DIR / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        services["state_dir"] = True
    except Exception:
        services["state_dir"] = False

    # Check signal file can be written
    try:
        signal_path = STATE_DIR / "auto_advance_signal.json"
        if signal_path.exists():
            with open(signal_path, 'r') as f:
                json.load(f)
            services["signal_file_writable"] = True
        else:
            test_signal = STATE_DIR / ".signal_test.json"
            test_signal.write_text('{"test": true}')
            test_signal.unlink()
            services["signal_file_writable"] = True
    except Exception:
        services["signal_file_writable"] = False

    elapsed_ms = (time.time() - start) * 1000
    overall_healthy = all(services.values())

    from flask import current_app
    uptime_seconds = None
    if hasattr(current_app, '_start_time'):
        uptime_seconds = time.time() - current_app._start_time

    return jsonify({
        "healthy": overall_healthy,
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": uptime_seconds,
        "services": services,
        "latency_ms": round(elapsed_ms, 2)
    })


@core_bp.route('/api/start/<mode>', methods=['POST'])
def api_start(mode):
    success, message = start_claude(mode)
    return jsonify({"success": success, "message": message})


@core_bp.route('/api/stop', methods=['POST'])
def api_stop():
    success, message = stop_claude()
    return jsonify({"success": success, "message": message})


# =============================================================================
# NARRATIVE AUTONOMOUS ROUTES
# =============================================================================

@core_bp.route('/api/narrative-autonomous/status')
def api_narrative_status():
    """Get status of the narrative autonomous workflow."""
    if get_narrative_status:
        return jsonify(get_narrative_status())
    return jsonify({"error": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/start', methods=['POST'])
def api_narrative_start():
    """Start narrative autonomous workflow."""
    if start_narrative:
        success, message = start_narrative()
        return jsonify({"success": success, "message": message})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/stop', methods=['POST'])
def api_narrative_stop():
    """Stop narrative autonomous workflow."""
    if stop_narrative:
        success, message = stop_narrative()
        return jsonify({"success": success, "message": message})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/chat', methods=['GET', 'POST'])
def api_narrative_chat():
    """Get or send chat messages to narrative workflow."""
    if request.method == 'POST':
        if send_message_to_narrative:
            data = request.get_json()
            message = data.get('message', '')
            if message:
                send_message_to_narrative(message)
                return jsonify({"success": True, "message": "Message sent to narrative workflow"})
            return jsonify({"success": False, "message": "No message provided"})
        return jsonify({"success": False, "message": "Narrative not available"}), 503
    else:
        if get_narrative_chat_history:
            return jsonify(get_narrative_chat_history(50))
        return jsonify([])


@core_bp.route('/api/narrative-autonomous/mission', methods=['GET', 'POST'])
def api_narrative_mission():
    """Get or set the narrative mission."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"error": "Narrative not available"}), 503

    if request.method == 'POST':
        data = request.get_json()
        story_number = data.get('story_number')
        story_title = data.get('story_title')
        story_genre = data.get('story_genre')
        story_logline = data.get('story_logline')

        if not story_number or not story_title:
            return jsonify({"success": False, "message": "story_number and story_title required"})

        import uuid
        safe_title = story_title.replace(" ", "_").replace("/", "-")[:50]
        project_base = Path("/media/vader/TIE-FIGHTER/RCFT - Narrative Project/01 - Narrative Research/Completed")
        story_workspace = project_base / f"{story_number:03d}_{safe_title}"
        story_workspace.mkdir(parents=True, exist_ok=True)

        new_mission = {
            "mission_id": f"narrative_{uuid.uuid4().hex[:8]}",
            "story_number": story_number,
            "story_title": story_title,
            "story_genre": story_genre,
            "story_logline": story_logline,
            "current_step": "INIT",
            "status": "running",
            "step_results": [],
            "files_created": [],
            "story_workspace": str(story_workspace),
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "history": [],
            "approval_pending": None,
        }
        io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, new_mission)

        return jsonify({
            "success": True,
            "message": f"Narrative mission set for '{story_title}'",
            "mission": new_mission
        })
    else:
        mission = io_utils.atomic_read_json(NARRATIVE_MISSION_PATH, {})
        return jsonify(mission)


@core_bp.route('/api/narrative-autonomous/approve', methods=['POST'])
def api_narrative_approve():
    """Approve a step waiting for user approval."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"success": False, "message": "Narrative not available"}), 503

    mission = io_utils.atomic_read_json(NARRATIVE_MISSION_PATH, {})
    if mission.get("status") != "waiting_approval":
        return jsonify({"success": False, "message": "No step waiting for approval"})

    mission["status"] = "running"
    mission["approval_pending"] = None
    io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, mission)

    if send_message_to_narrative:
        send_message_to_narrative("approve")

    return jsonify({"success": True, "message": "Step approved"})


@core_bp.route('/api/narrative-autonomous/pause', methods=['POST'])
def api_narrative_pause():
    """Pause the narrative workflow."""
    if send_message_to_narrative:
        send_message_to_narrative("pause")
        return jsonify({"success": True, "message": "Pause command sent"})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/resume', methods=['POST'])
def api_narrative_resume():
    """Resume the narrative workflow."""
    if send_message_to_narrative:
        send_message_to_narrative("resume")
        return jsonify({"success": True, "message": "Resume command sent"})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/reset', methods=['POST'])
def api_narrative_reset():
    """Reset the narrative mission to initial state."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"success": False, "message": "Narrative not available"}), 503

    default_mission = {
        "mission_id": "narrative_default",
        "story_number": None,
        "story_title": None,
        "story_genre": None,
        "story_logline": None,
        "current_step": "INIT",
        "status": "pending",
        "step_results": [],
        "files_created": [],
        "story_workspace": None,
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "history": [],
        "approval_pending": None,
    }
    io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, default_mission)
    if send_message_to_narrative:
        send_message_to_narrative("reset")
    return jsonify({"success": True, "message": "Narrative mission reset"})


# =============================================================================
# JOURNAL ROUTES
# =============================================================================

@core_bp.route('/api/journal')
def api_journal():
    return jsonify(get_recent_journal(15))


# =============================================================================
# MISSION ROUTES
# =============================================================================

@core_bp.route('/api/mission', methods=['GET', 'POST'])
def api_mission():
    if request.method == 'POST':
        data = request.get_json()
        problem_statement = data.get('mission', '')
        cycle_budget = int(data.get('cycle_budget', 1))
        metadata = data.get('metadata', {})
        user_project_name = data.get('project_name')  # Optional user-specified project name

        if problem_statement:
            import uuid

            mission_id = f"mission_{uuid.uuid4().hex[:8]}"
            missions_dir = BASE_DIR / "missions"
            mission_dir = missions_dir / mission_id

            # Resolve project name for shared workspace
            resolved_project_name = None
            try:
                from project_name_resolver import resolve_project_name
                resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
                # Use shared workspace under workspace/<project_name>/
                workspace_dir = BASE_DIR / "workspace"
                mission_workspace = workspace_dir / resolved_project_name
            except ImportError:
                # Fallback to legacy per-mission workspace
                mission_workspace = mission_dir / "workspace"

            # Create mission directory (for config, analytics, drift validation)
            mission_dir.mkdir(parents=True, exist_ok=True)

            # Create workspace directories (may already exist if shared project)
            (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

            new_mission = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "original_problem_statement": problem_statement,
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
                "cycle_budget": max(1, cycle_budget),
                "current_cycle": 1,
                "cycle_history": [],
                "mission_workspace": str(mission_workspace),
                "mission_dir": str(mission_dir),
                "project_name": resolved_project_name,
                "metadata": metadata
            }
            io_utils.atomic_write_json(MISSION_PATH, new_mission)

            config_data = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "cycle_budget": max(1, cycle_budget),
                "created_at": new_mission["created_at"]
            }
            if resolved_project_name:
                config_data["project_name"] = resolved_project_name
                config_data["project_workspace"] = str(mission_workspace)

            mission_config_path = mission_dir / "mission_config.json"
            with open(mission_config_path, 'w') as f:
                json.dump(config_data, f, indent=2)

            # Register mission with analytics system
            try:
                from mission_analytics import get_analytics
                analytics = get_analytics()
                analytics.start_mission(mission_id, problem_statement)
            except Exception as e:
                import logging
                logging.warning(f"Analytics: Failed to register mission: {e}")

            response_msg = f"Mission saved with {cycle_budget} cycle(s)."
            if resolved_project_name:
                response_msg += f" Project: {resolved_project_name}."
            response_msg += " Click 'Start R&D' to begin."

            return jsonify({
                "success": True,
                "message": response_msg,
                "mission_id": mission_id,
                "mission_workspace": str(mission_workspace),
                "project_name": resolved_project_name
            })
        return jsonify({"success": False, "message": "No mission provided"})
    else:
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        return jsonify(mission)


@core_bp.route('/api/mission/reset', methods=['POST'])
def api_mission_reset():
    send_message_to_claude("reset")
    return jsonify({"success": True, "message": "Reset command sent"})


@core_bp.route('/api/suggest-project-name', methods=['POST'])
def api_suggest_project_name():
    """
    Suggest a project name based on problem statement text.
    Returns suggested name, strategies tried, and existing projects.
    """
    data = request.get_json() or {}
    problem_statement = data.get('problem_statement', '')

    if not problem_statement or len(problem_statement) < 5:
        return jsonify({"error": "Problem statement too short"}), 400

    try:
        from project_name_resolver import suggest_project_name
        result = suggest_project_name(problem_statement)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "project_name_resolver module not found"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =============================================================================
# PROPOSALS ROUTES
# =============================================================================

@core_bp.route('/api/proposals', methods=['GET'])
def api_proposals():
    """Get all pending proposals."""
    proposals = io_utils.atomic_read_json(PROPOSALS_PATH, {"pending": [], "approved": [], "rejected": []})
    return jsonify(proposals)


@core_bp.route('/api/proposals/<proposal_id>/approve', methods=['POST'])
def api_approve_proposal(proposal_id):
    """Approve a specific proposal."""
    def update_fn(proposals):
        for i, p in enumerate(proposals.get("pending", [])):
            if p.get("id") == proposal_id:
                p["status"] = "approved"
                p["approved_at"] = datetime.now().isoformat()
                proposals.setdefault("approved", []).append(p)
                proposals["pending"].pop(i)
                break
        return proposals

    io_utils.atomic_update_json(PROPOSALS_PATH, update_fn, {"pending": [], "approved": [], "rejected": []})
    return jsonify({"success": True})


@core_bp.route('/api/proposals/<proposal_id>/reject', methods=['POST'])
def api_reject_proposal(proposal_id):
    """Reject a specific proposal."""
    def update_fn(proposals):
        for i, p in enumerate(proposals.get("pending", [])):
            if p.get("id") == proposal_id:
                p["status"] = "rejected"
                p["rejected_at"] = datetime.now().isoformat()
                proposals.setdefault("rejected", []).append(p)
                proposals["pending"].pop(i)
                break
        return proposals

    io_utils.atomic_update_json(PROPOSALS_PATH, update_fn, {"pending": [], "approved": [], "rejected": []})
    return jsonify({"success": True})


# =============================================================================
# RECOMMENDATIONS ROUTES
# =============================================================================

@core_bp.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    """Get all mission recommendations with optional filtering.

    Query Parameters:
        source_type: Filter by source type ('drift_halt' or 'successful_completion')

    Returns:
        JSON with filtered or all recommendations from SQLite storage
    """
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"items": [], "error": "Storage not available"}), 503

    source_type_filter = request.args.get('source_type')

    try:
        if source_type_filter:
            items = storage.get_filtered(source_type=source_type_filter)
        else:
            items = storage.get_all()
        return jsonify({"items": items})
    except Exception as e:
        import logging
        logging.error(f"SQLite read failed: {e}")
        return jsonify({"items": [], "error": str(e)}), 500


@core_bp.route('/api/recommendations', methods=['POST'])
def api_add_recommendation():
    """Add a new mission recommendation with auto-tagging and similarity check."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json()
    import uuid

    recommendation = {
        "id": f"rec_{uuid.uuid4().hex[:8]}",
        "mission_title": data.get("mission_title", "Untitled Mission"),
        "mission_description": data.get("mission_description", ""),
        "suggested_cycles": data.get("suggested_cycles", 3),
        "source_mission_id": data.get("source_mission_id"),
        "source_mission_summary": data.get("source_mission_summary", ""),
        "rationale": data.get("rationale", ""),
        "created_at": datetime.now().isoformat(),
        "source_type": data.get("source_type", "manual")
    }

    # Auto-tag and check for similar suggestions
    similar_to = []
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        recommendation = analyzer.on_new_suggestion(recommendation)
        similar_to = recommendation.get('similar_to', [])
    except Exception as e:
        import logging
        logging.warning(f"Auto-tagging failed: {e}")

    try:
        storage.add(recommendation)
        response = {"success": True, "recommendation": recommendation}
        if similar_to:
            response["merge_candidates"] = similar_to
            response["has_similar"] = True
        return jsonify(response)
    except Exception as e:
        import logging
        logging.error(f"SQLite add failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@core_bp.route('/api/recommendations/similarity-analysis', methods=['GET'])
def api_similarity_analysis():
    """Analyze recommendations for similarity and suggest groupings."""
    recommendations = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
    items = recommendations.get("items", [])

    if len(items) < 2:
        return jsonify({"groups": [], "message": "Need at least 2 suggestions for similarity analysis"})

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return jsonify({"error": "scikit-learn not installed"}), 500

    # Build text corpus from title + description
    texts = [f"{r.get('mission_title', '')} {r.get('mission_description', '')}" for r in items]

    # Calculate TF-IDF similarity
    vectorizer = TfidfVectorizer(stop_words='english', min_df=1)
    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
        sim_matrix = cosine_similarity(tfidf_matrix)
    except ValueError:
        return jsonify({"groups": [], "message": "Not enough text content for similarity analysis"})

    threshold = float(request.args.get('threshold', 0.3))
    n = len(items)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i][j] >= threshold:
                pairs.append((i, j, sim_matrix[i][j]))

    pairs.sort(key=lambda x: x[2], reverse=True)
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, j, _ in pairs:
        union(i, j)

    group_map = {}
    for i in range(n):
        root = find(i)
        if root not in group_map:
            group_map[root] = []
        group_map[root].append(i)

    groups = []
    for indices in group_map.values():
        if len(indices) >= 2:
            group_items = []
            for idx in indices:
                item = items[idx]
                avg_sim = 0
                for other_idx in indices:
                    if other_idx != idx:
                        avg_sim += sim_matrix[idx][other_idx]
                if len(indices) > 1:
                    avg_sim /= (len(indices) - 1)
                group_items.append({
                    "id": item.get("id"),
                    "mission_title": item.get("mission_title"),
                    "mission_description": item.get("mission_description", "")[:200],
                    "suggested_cycles": item.get("suggested_cycles", 3),
                    "similarity_score": round(avg_sim, 3)
                })
            group_items.sort(key=lambda x: x["similarity_score"], reverse=True)
            groups.append({
                "items": group_items,
                "avg_similarity": round(sum(g["similarity_score"] for g in group_items) / len(group_items), 3)
            })

    groups.sort(key=lambda x: x["avg_similarity"], reverse=True)
    return jsonify({
        "groups": groups,
        "threshold": threshold,
        "total_items": len(items),
        "items_in_groups": sum(len(g["items"]) for g in groups)
    })


@core_bp.route('/api/recommendations/merge', methods=['POST'])
def api_merge_recommendations():
    """Merge multiple recommendations into one, preserving source descriptions."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    import uuid
    data = request.get_json()
    source_ids = data.get("source_ids", [])
    merged_data = data.get("merged_data", {})
    delete_sources = data.get("delete_sources", True)

    if len(source_ids) < 2:
        return jsonify({"success": False, "error": "Need at least 2 recommendations to merge"}), 400

    try:
        # Get source descriptions from database
        source_descriptions = []
        for source_id in source_ids:
            rec = storage.get_by_id(source_id)
            if rec:
                source_descriptions.append({
                    "id": rec.get("id"),
                    "title": rec.get("mission_title", ""),
                    "description": rec.get("mission_description", "")
                })

        new_rec = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": merged_data.get("mission_title", "Merged Suggestion"),
            "mission_description": merged_data.get("mission_description", ""),
            "suggested_cycles": int(merged_data.get("suggested_cycles", 3)),
            "rationale": merged_data.get("rationale", ""),
            "source_type": "merged",
            "merged_from": source_ids,
            "merged_source_descriptions": source_descriptions,
            "created_at": datetime.now().isoformat()
        }

        # Delete sources and add new merged record
        if delete_sources:
            storage.delete_multiple(source_ids)
        storage.add(new_rec)
        return jsonify({"success": True, "new_recommendation": new_rec})
    except Exception as e:
        import logging
        logging.error(f"SQLite merge failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@core_bp.route('/api/recommendations/analyze', methods=['GET'])
def api_analyze_recommendations():
    """
    Analyze all recommendations: auto-tag, prioritize, and health-check.

    Returns:
        JSON with items sorted by priority, health_report, and total count
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.analyze_all(persist=True)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@core_bp.route('/api/recommendations/auto-tag', methods=['POST'])
def api_auto_tag_recommendations():
    """
    Run auto-tagging on all suggestions.

    Returns:
        JSON with tagged_count and tag_distribution
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.auto_tag_all(persist=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@core_bp.route('/api/recommendations/health-report', methods=['GET'])
def api_health_report():
    """
    Get health summary of all suggestions.

    Returns:
        JSON with counts (healthy, stale, orphaned, needs_review, hot),
        total, stale_items, orphaned_items, needs_analysis
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.get_health_report()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['GET'])
def api_get_recommendation(rec_id):
    """Get a specific recommendation."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"error": "Storage not available"}), 503

    try:
        rec = storage.get_by_id(rec_id)
        if rec:
            return jsonify(rec)
        return jsonify({"error": "Recommendation not found"}), 404
    except Exception as e:
        import logging
        logging.error(f"SQLite get failed: {e}")
        return jsonify({"error": str(e)}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['DELETE'])
def api_delete_recommendation(rec_id):
    """Delete a recommendation."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    try:
        deleted = storage.delete(rec_id)
        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        import logging
        logging.error(f"SQLite delete failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['PUT'])
def api_update_recommendation(rec_id):
    """Update a recommendation (edit mode)."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json()

    # Input validation
    if "suggested_cycles" in data:
        try:
            cycles = int(data["suggested_cycles"])
            if cycles < 1 or cycles > 10:
                return jsonify({
                    "success": False,
                    "error": "Cycle count must be between 1 and 10"
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "error": "Cycle count must be a valid number"
            }), 400

    if "mission_title" in data:
        title = str(data["mission_title"]).strip()
        if len(title) < 3:
            return jsonify({
                "success": False,
                "error": "Mission title must be at least 3 characters"
            }), 400

    try:
        # Get current record to preserve originals
        current = storage.get_by_id(rec_id)
        if not current:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404

        updates = {}
        # Preserve originals if first edit
        if "original_mission_title" not in current:
            updates["original_mission_title"] = current.get("mission_title")
            updates["original_mission_description"] = current.get("mission_description")
            updates["original_rationale"] = current.get("rationale")
            updates["original_suggested_cycles"] = current.get("suggested_cycles")
        # Update fields
        if "mission_title" in data:
            updates["mission_title"] = str(data["mission_title"]).strip()
        if "mission_description" in data:
            updates["mission_description"] = data["mission_description"]
        if "suggested_cycles" in data:
            updates["suggested_cycles"] = int(data["suggested_cycles"])
        if "rationale" in data:
            updates["rationale"] = data["rationale"]
        updates["last_edited_at"] = datetime.now().isoformat()
        storage.update(rec_id, updates)
        return jsonify({"success": True})
    except Exception as e:
        import logging
        logging.error(f"SQLite update failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@core_bp.route('/api/recommendations/<rec_id>/set-mission', methods=['POST'])
def api_set_mission_from_recommendation(rec_id):
    """Set a mission from a recommendation and remove it from the list.

    Supports shared workspaces via project_name resolution.
    """
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json() or {}
    cycle_budget = int(data.get("cycle_budget", 3))
    user_project_name = data.get("project_name")  # Optional user-specified project name

    try:
        target_rec = storage.get_by_id(rec_id)
    except Exception as e:
        import logging
        logging.error(f"SQLite get failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    if not target_rec:
        return jsonify({"success": False, "error": "Recommendation not found"}), 404

    import uuid
    mission_id = f"mission_{uuid.uuid4().hex[:8]}"

    # Build problem statement first (needed for project name resolution)
    # Special handling for merged recommendations
    if target_rec.get("source_type") == "merged" and target_rec.get("merged_source_descriptions"):
        # For merged recommendations, combine user summary with original source descriptions
        parts = []
        user_desc = (target_rec.get("mission_description") or "").strip()
        if user_desc:
            parts.append(f"## Summary\n{user_desc}")

        for src in target_rec.get("merged_source_descriptions", []):
            src_title = src.get("title", "Source")
            src_desc = src.get("description", "")
            if src_desc:
                parts.append(f"## {src_title}\n{src_desc}")

        problem_statement = "\n\n".join(parts) if parts else target_rec.get("mission_title", "")
    else:
        # Standard: use mission_description if non-empty, otherwise fall back to mission_title
        problem_statement = target_rec.get("mission_description") or target_rec.get("mission_title") or ""

    missions_dir = BASE_DIR / "missions"
    mission_dir = missions_dir / mission_id

    # Resolve project name for shared workspace (same logic as api_mission)
    resolved_project_name = None
    try:
        from project_name_resolver import resolve_project_name
        resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
        # Use shared workspace under workspace/<project_name>/
        mission_workspace = WORKSPACE_DIR / resolved_project_name
    except ImportError:
        # Fallback to legacy per-mission workspace
        mission_workspace = mission_dir / "workspace"

    # Create mission directory (for config, analytics, drift validation)
    mission_dir.mkdir(parents=True, exist_ok=True)

    # Create workspace directories (may already exist if shared project)
    (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
    (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

    new_mission = {
        "mission_id": mission_id,
        "problem_statement": problem_statement,
        "original_problem_statement": problem_statement,
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
        "cycle_budget": max(1, cycle_budget),
        "current_cycle": 1,
        "cycle_history": [],
        "mission_workspace": str(mission_workspace),
        "mission_dir": str(mission_dir),
        "project_name": resolved_project_name,
        "source_recommendation_id": rec_id
    }
    io_utils.atomic_write_json(MISSION_PATH, new_mission)

    # Build mission config with shared workspace info
    config_data = {
        "mission_id": mission_id,
        "problem_statement": problem_statement,
        "cycle_budget": max(1, cycle_budget),
        "created_at": new_mission["created_at"],
        "source_recommendation_id": rec_id
    }
    if resolved_project_name:
        config_data["project_name"] = resolved_project_name
        config_data["project_workspace"] = str(mission_workspace)

    mission_config_path = mission_dir / "mission_config.json"
    with open(mission_config_path, 'w') as f:
        json.dump(config_data, f, indent=2)

    # Register mission with analytics system
    try:
        from mission_analytics import get_analytics
        analytics = get_analytics()
        analytics.start_mission(mission_id, problem_statement)
    except Exception as e:
        import logging
        logging.warning(f"Analytics: Failed to register mission: {e}")

    # Remove recommendation from SQLite storage
    try:
        storage.delete(rec_id)
    except Exception as e:
        import logging
        logging.warning(f"SQLite delete failed: {e}")
        # Continue anyway - mission was created successfully

    response_msg = f"Mission set with {cycle_budget} cycle(s)."
    if resolved_project_name:
        response_msg += f" Project: {resolved_project_name}."
    response_msg += " Click 'Start Mission' to begin."

    return jsonify({
        "success": True,
        "message": response_msg,
        "mission_id": mission_id,
        "mission_workspace": str(mission_workspace),
        "project_name": resolved_project_name
    })


# =============================================================================
# MISSION LOGS ROUTES
# =============================================================================

@core_bp.route('/api/mission-logs')
def api_mission_logs():
    """List all mission log files."""
    logs = []
    if MISSION_LOGS_DIR and MISSION_LOGS_DIR.exists():
        for log_file in MISSION_LOGS_DIR.glob("*_report.json"):
            try:
                with open(log_file, 'r') as f:
                    data = json.load(f)
                logs.append({
                    "mission_id": data.get("mission_id", log_file.stem),
                    "original_mission": data.get("original_mission", ""),
                    "total_cycles": data.get("total_cycles", 0),
                    "total_iterations": data.get("total_iterations", 0),
                    "started_at": data.get("started_at"),
                    "completed_at": data.get("completed_at"),
                    "file_name": log_file.name
                })
            except Exception:
                continue

    logs.sort(key=lambda x: x.get("completed_at") or "", reverse=True)
    return jsonify({"logs": logs, "total": len(logs)})


@core_bp.route('/api/mission-logs/<mission_id>')
def api_mission_log_detail(mission_id):
    """Get details of a specific mission log."""
    if not MISSION_LOGS_DIR or not MISSION_LOGS_DIR.exists():
        return jsonify({"error": "Mission logs directory not found"}), 404

    for log_file in MISSION_LOGS_DIR.glob(f"{mission_id}*.json"):
        try:
            with open(log_file, 'r') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": f"Error reading log: {e}"}), 500

    return jsonify({"error": "Mission log not found"}), 404


# =============================================================================
# FILE DOWNLOAD ROUTES
# =============================================================================

@core_bp.route('/api/download/<path:filepath>')
def download_file(filepath):
    """Serve files from workspace for download.

    Supports both global workspace and mission-specific paths:
    - /api/download/artifacts/file.txt - global workspace
    - /api/download/mission/{mission_id}/artifacts/file.txt - mission workspace

    Uses centralized workspace resolver for correct path resolution
    with both shared and legacy workspaces.
    """
    # Check if this is a mission-specific path
    if filepath.startswith('mission/'):
        parts = filepath.split('/', 2)
        if len(parts) >= 3:
            mission_id = parts[1]
            relative_path = parts[2]

            # Use centralized workspace resolver
            from .workspace_resolver import resolve_mission_workspace
            missions_dir = BASE_DIR / "missions"
            mission_workspace = resolve_mission_workspace(
                mission_id, missions_dir, WORKSPACE_DIR, io_utils
            )

            full_path = mission_workspace / relative_path
            allowed_base = mission_workspace
        else:
            abort(404)
    else:
        full_path = WORKSPACE_DIR / filepath
        allowed_base = WORKSPACE_DIR

    try:
        full_path = full_path.resolve()
    except Exception:
        abort(404)

    try:
        full_path.relative_to(allowed_base.resolve())
    except ValueError:
        abort(403)

    if not full_path.exists() or not full_path.is_file():
        abort(404)

    mime_type, _ = mimetypes.guess_type(str(full_path))

    return send_file(
        full_path,
        mimetype=mime_type or 'application/octet-stream',
        as_attachment=True,
        download_name=full_path.name
    )


@core_bp.route('/api/files')
def list_files():
    """List files in current mission workspace (or global workspace if no mission)."""
    files = []

    # Get mission workspace if an active mission exists
    mission = io_utils.atomic_read_json(MISSION_PATH, {})
    mission_workspace = mission.get('mission_workspace')
    mission_id = mission.get('mission_id')

    # Use mission-specific workspace when available
    workspace_base = Path(mission_workspace) if mission_workspace else WORKSPACE_DIR

    scan_dirs = [
        workspace_base / "artifacts",
        workspace_base / "research",
        workspace_base / "tests",
        workspace_base
    ]

    seen_paths = set()

    for dir_path in scan_dirs:
        if dir_path.exists():
            pattern = "*" if dir_path == workspace_base else "**/*"
            for f in dir_path.glob(pattern):
                if f.is_file():
                    try:
                        rel_path = f.relative_to(workspace_base)
                        path_str = str(rel_path)

                        if path_str in seen_paths:
                            continue
                        seen_paths.add(path_str)

                        stat = f.stat()
                        # Build download URL with mission context if needed
                        if mission_workspace:
                            download_path = f"mission/{mission_id}/{path_str}"
                        else:
                            download_path = path_str

                        files.append({
                            "name": f.name,
                            "path": path_str,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                            "download_url": f"/api/download/{download_path}",
                            "mission_id": mission_id if mission_workspace else None
                        })
                    except (OSError, IOError, ValueError):
                        continue

    files.sort(key=lambda x: x["modified"], reverse=True)
    return jsonify(files[:50])


# =============================================================================
# MISSING ENDPOINT STUBS (to prevent 404 console errors)
# =============================================================================

@core_bp.route('/api/vitals/batch', methods=['POST'])
def vitals_batch():
    """
    Stub endpoint for web vitals batch reporting.
    Frontend may send performance metrics here - we accept and ignore them.
    """
    return jsonify({"status": "ok", "received": True})


@core_bp.route('/favicon.ico')
def favicon():
    """Return empty favicon to prevent 404 errors."""
    # Return a minimal 1x1 transparent ICO
    # This is a valid minimal ICO file (16x16 transparent)
    ico_data = bytes([
        0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x10, 0x10,
        0x00, 0x00, 0x01, 0x00, 0x20, 0x00, 0x68, 0x04,
        0x00, 0x00, 0x16, 0x00, 0x00, 0x00
    ])
    # Pad to make a valid icon
    ico_data += bytes(1024)  # Add padding
    from flask import Response
    return Response(ico_data[:1086], mimetype='image/x-icon')
