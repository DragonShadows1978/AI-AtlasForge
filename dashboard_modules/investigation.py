"""
Investigation Mode Dashboard Routes Blueprint

Provides API endpoints for Investigation Mode - a simplified single-cycle
research workflow that runs parallel subagents to explore a topic.

This is COMPLETELY SEPARATE from the standard R&D engine workflow.

Endpoints:
- POST /api/investigation/start - Start new investigation
- GET /api/investigation/status - Get current investigation status
- GET /api/investigation/status/<id> - Get specific investigation status
- GET /api/investigation/report/<id> - Get investigation report
- POST /api/investigation/stop/<id> - Stop an investigation
- GET /api/investigation/history - List past investigations (with search/filter/sort)
- GET /api/investigation/history/search - Search investigations by query text
- GET /api/investigation/history/stats - Get investigation statistics
- POST /api/investigation/<id>/tags - Add/update tags for an investigation
- DELETE /api/investigation/<id>/tags/<tag> - Remove a tag from an investigation
- GET /api/investigation/<id>/export - Export investigation report as markdown/JSON
"""

import threading
import re
from flask import Blueprint, jsonify, request, Response
from pathlib import Path
from datetime import datetime, timedelta
import json

# Create Blueprint
investigation_bp = Blueprint('investigation', __name__)

# Module-level references (set by init function)
BASE_DIR = None
STATE_DIR = None
io_utils = None
socketio = None

# Track running investigation thread
_investigation_thread = None


def init_investigation_blueprint(base_dir, state_dir, io_utils_module, socketio_instance=None):
    """Initialize the investigation blueprint with required dependencies."""
    global BASE_DIR, STATE_DIR, io_utils, socketio
    BASE_DIR = base_dir
    STATE_DIR = state_dir
    io_utils = io_utils_module
    socketio = socketio_instance


# =============================================================================
# START INVESTIGATION
# =============================================================================

@investigation_bp.route('/api/investigation/start', methods=['POST'])
def api_investigation_start():
    """
    Start a new investigation.

    Request body:
    {
        "query": "What to investigate",
        "max_subagents": 5,           // optional, default 5
        "timeout_minutes": 10,         // optional, default 10
        "deliverable_format": "HTML"   // optional, e.g. "HTML", "JSON", "markdown"
    }

    The investigation engine can research ANY topic - not just software.
    Examples: gaming builds, physics explanations, market research, etc.

    Returns:
    {
        "success": true/false,
        "investigation_id": "inv_xxx",
        "message": "..."
    }
    """
    global _investigation_thread

    data = request.get_json() or {}
    query = data.get('query', '').strip()

    if not query:
        return jsonify({
            "success": False,
            "message": "No query provided"
        }), 400

    max_subagents = min(int(data.get('max_subagents', 5)), 10)  # Cap at 10
    timeout_minutes = min(int(data.get('timeout_minutes', 10)), 30)  # Cap at 30
    deliverable_format = data.get('deliverable_format')  # e.g., "HTML", "JSON", "markdown"

    # Check if an investigation is already running
    from investigation_engine import load_investigation_state, InvestigationStatus

    state = load_investigation_state()
    if state.get("current"):
        current_status = state["current"].get("status")
        if current_status not in [
            InvestigationStatus.COMPLETED.value,
            InvestigationStatus.FAILED.value
        ]:
            return jsonify({
                "success": False,
                "message": f"Investigation already running: {state['current'].get('investigation_id')}"
            }), 409

    # Start investigation in background thread
    from investigation_engine import InvestigationConfig, InvestigationRunner

    config = InvestigationConfig(
        query=query,
        max_subagents=max_subagents,
        timeout_minutes=timeout_minutes,
        deliverable_format=deliverable_format
    )

    def run_investigation_thread():
        """Run investigation in background."""
        runner = InvestigationRunner(config)

        def progress_callback(msg):
            if socketio:
                socketio.emit('investigation_progress', {
                    'investigation_id': config.investigation_id,
                    'message': msg,
                    'timestamp': datetime.now().isoformat()
                }, namespace='/widgets', room='investigation')

        result = runner.run(progress_callback=progress_callback)

        # Emit completion
        if socketio:
            socketio.emit('investigation_complete', {
                'investigation_id': result.investigation_id,
                'status': result.status.value,
                'report_path': str(result.report_path) if result.report_path else None,
                'elapsed_seconds': result.elapsed_seconds,
                'error': result.error
            }, namespace='/widgets', room='investigation')

    _investigation_thread = threading.Thread(target=run_investigation_thread, daemon=True)
    _investigation_thread.start()

    format_msg = f" (output: {deliverable_format})" if deliverable_format else ""
    return jsonify({
        "success": True,
        "investigation_id": config.investigation_id,
        "message": f"Investigation started with {max_subagents} subagents{format_msg}",
        "workspace_dir": str(config.workspace_dir),
        "deliverable_format": deliverable_format
    })


# =============================================================================
# GET STATUS
# =============================================================================

@investigation_bp.route('/api/investigation/status')
@investigation_bp.route('/api/investigation/status/<investigation_id>')
def api_investigation_status(investigation_id=None):
    """
    Get investigation status.

    If no ID provided, returns current investigation status.
    Includes attachment metadata from investigation config.json.
    """
    from investigation_engine import get_investigation_status

    status = get_investigation_status(investigation_id)

    if status is None:
        if investigation_id:
            return jsonify({
                "error": f"Investigation {investigation_id} not found"
            }), 404
        return jsonify({
            "status": "idle",
            "current": None,
            "message": "No investigation running"
        })

    # Enrich with attachment metadata from config.json
    inv_id = status.get("investigation_id") or investigation_id
    if inv_id and BASE_DIR:
        config_path = BASE_DIR / "investigations" / inv_id / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                status["has_attachments"] = config.get("has_attachments", False)
                status["attachment_count"] = config.get("attachment_count", 0)
                status["attachments"] = config.get("attachments", [])
            except Exception:
                pass

    return jsonify(status)


# =============================================================================
# GET REPORT
# =============================================================================

@investigation_bp.route('/api/investigation/report/<investigation_id>')
def api_investigation_report(investigation_id):
    """
    Get the investigation report content.

    Returns the markdown report content if available.
    """
    from investigation_engine import get_investigation_status, InvestigationStatus

    status = get_investigation_status(investigation_id)

    if status is None:
        return jsonify({"error": "Investigation not found"}), 404

    if status.get("status") != InvestigationStatus.COMPLETED.value:
        return jsonify({
            "error": "Investigation not completed",
            "status": status.get("status")
        }), 400

    report_path = status.get("report_path")
    if not report_path:
        return jsonify({"error": "No report available"}), 404

    report_file = Path(report_path)
    if not report_file.exists():
        return jsonify({"error": "Report file not found"}), 404

    try:
        report_content = report_file.read_text()

        # Also try to load findings.json
        findings_path = report_file.parent / "findings.json"
        findings = None
        if findings_path.exists():
            with open(findings_path, 'r') as f:
                findings = json.load(f)

        return jsonify({
            "investigation_id": investigation_id,
            "report_path": str(report_path),
            "report_content": report_content,
            "findings": findings
        })
    except Exception as e:
        return jsonify({"error": f"Failed to read report: {e}"}), 500


# =============================================================================
# STOP INVESTIGATION
# =============================================================================

@investigation_bp.route('/api/investigation/stop/<investigation_id>', methods=['POST'])
def api_investigation_stop(investigation_id):
    """
    Request to stop an ongoing investigation.

    Note: This marks the investigation for stopping but running Claude
    processes may continue until they complete or timeout.
    """
    from investigation_engine import stop_investigation

    if stop_investigation(investigation_id):
        return jsonify({
            "success": True,
            "message": f"Investigation {investigation_id} marked for stopping"
        })

    return jsonify({
        "success": False,
        "message": "Investigation not found or already completed"
    }), 404


# =============================================================================
# TAGS STORAGE (in-memory + persistent)
# =============================================================================

# Tags are stored in investigation_tags.json
INVESTIGATION_TAGS_PATH = None

def _get_tags_path():
    """Get the path to investigation tags storage."""
    global INVESTIGATION_TAGS_PATH
    if INVESTIGATION_TAGS_PATH is None:
        INVESTIGATION_TAGS_PATH = STATE_DIR / "investigation_tags.json"
    return INVESTIGATION_TAGS_PATH


def _load_investigation_tags() -> dict:
    """Load investigation tags from storage."""
    tags_path = _get_tags_path()
    try:
        if tags_path.exists():
            with open(tags_path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_investigation_tags(tags: dict):
    """Save investigation tags to storage."""
    tags_path = _get_tags_path()
    try:
        with open(tags_path, 'w') as f:
            json.dump(tags, f, indent=2)
    except Exception as e:
        print(f"Failed to save investigation tags: {e}")


def _enrich_investigation_with_metadata(inv: dict) -> dict:
    """Add computed fields and tags to an investigation record."""
    enriched = inv.copy()

    # Add tags
    tags = _load_investigation_tags()
    inv_id = inv.get("investigation_id", "")
    enriched["tags"] = tags.get(inv_id, [])

    # Add attachment metadata from investigation config.json
    if inv_id and BASE_DIR:
        config_path = BASE_DIR / "investigations" / inv_id / "config.json"
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                enriched["has_attachments"] = config.get("has_attachments", False)
                enriched["attachment_count"] = config.get("attachment_count", 0)
                enriched["attachments"] = config.get("attachments", [])
            except Exception:
                enriched["has_attachments"] = False
                enriched["attachment_count"] = 0
                enriched["attachments"] = []
        else:
            enriched["has_attachments"] = False
            enriched["attachment_count"] = 0
            enriched["attachments"] = []
    else:
        enriched["has_attachments"] = False
        enriched["attachment_count"] = 0
        enriched["attachments"] = []

    # Compute elapsed time in human-readable format
    elapsed_seconds = inv.get("elapsed_seconds")
    if elapsed_seconds is not None:
        if elapsed_seconds < 60:
            enriched["elapsed_display"] = f"{int(elapsed_seconds)}s"
        elif elapsed_seconds < 3600:
            mins = int(elapsed_seconds / 60)
            secs = int(elapsed_seconds % 60)
            enriched["elapsed_display"] = f"{mins}m {secs}s"
        else:
            hours = int(elapsed_seconds / 3600)
            mins = int((elapsed_seconds % 3600) / 60)
            enriched["elapsed_display"] = f"{hours}h {mins}m"
    else:
        enriched["elapsed_display"] = "-"

    # Add truncated query for cards
    query = inv.get("query", "")
    enriched["query_truncated"] = query[:100] + "..." if len(query) > 100 else query

    # Format timestamp for display
    completed_at = inv.get("completed_at") or inv.get("started_at")
    if completed_at:
        try:
            dt = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
            enriched["timestamp_display"] = dt.strftime("%Y-%m-%d %H:%M")
            enriched["timestamp_relative"] = _relative_time(dt)
        except Exception:
            enriched["timestamp_display"] = completed_at[:16] if completed_at else "-"
            enriched["timestamp_relative"] = "-"
    else:
        enriched["timestamp_display"] = "-"
        enriched["timestamp_relative"] = "-"

    return enriched


def _relative_time(dt: datetime) -> str:
    """Convert datetime to relative time string."""
    now = datetime.now()
    if dt.tzinfo:
        now = datetime.now(dt.tzinfo)

    diff = now - dt

    if diff.days > 365:
        years = diff.days // 365
        return f"{years}y ago"
    elif diff.days > 30:
        months = diff.days // 30
        return f"{months}mo ago"
    elif diff.days > 0:
        return f"{diff.days}d ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours}h ago"
    elif diff.seconds > 60:
        mins = diff.seconds // 60
        return f"{mins}m ago"
    else:
        return "just now"


# =============================================================================
# HISTORY (Enhanced with search/filter/sort)
# =============================================================================

@investigation_bp.route('/api/investigation/history')
def api_investigation_history():
    """
    Get list of past investigations with search, filter, and sort capabilities.

    Query params:
    - limit: Max number to return (default 50)
    - offset: Pagination offset (default 0)
    - search: Search query text (searches in investigation query)
    - search_content: If 'true', also search in report content (slower)
    - status: Filter by status (completed/failed)
    - date_from: Filter by start date (ISO format)
    - date_to: Filter by end date (ISO format)
    - sort_by: Sort field (timestamp, elapsed, subagent_count) (default: timestamp)
    - sort_order: asc or desc (default: desc)
    - tags: Comma-separated list of tags to filter by
    - source: Filter by source (dashboard, email, api). If not specified, excludes 'email' by default.
    - include_email: If 'true', include email-triggered investigations (default: false)

    NOTE: Email investigations are filtered out by default.
    They appear on the Email Monitor tab instead.
    """
    from investigation_engine import load_investigation_state

    # Parse query params
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    search = request.args.get('search', '').strip().lower()
    search_content = request.args.get('search_content', 'false').lower() == 'true'
    status_filter = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    sort_by = request.args.get('sort_by', 'timestamp')
    sort_order = request.args.get('sort_order', 'desc')
    tags_filter = request.args.get('tags', '')
    source_filter = request.args.get('source', '')
    include_email = request.args.get('include_email', 'false').lower() == 'true'

    state = load_investigation_state()

    # Include current investigation if exists (for viewing running investigations)
    all_investigations = []
    if state.get("current"):
        all_investigations.append(state["current"])
    all_investigations.extend(state.get("history", []))

    # Filter out email-sourced investigations by default (they appear on Email Monitor tab)
    # Email investigations are separated to avoid UI clutter on the Investigations tab
    if not include_email and not source_filter:
        all_investigations = [
            inv for inv in all_investigations
            if inv.get("source", "dashboard") != "email"
        ]
    elif source_filter:
        all_investigations = [
            inv for inv in all_investigations
            if inv.get("source", "dashboard") == source_filter
        ]

    # Enrich all investigations with metadata
    enriched = [_enrich_investigation_with_metadata(inv) for inv in all_investigations]

    # Apply filters
    filtered = enriched

    # Search filter (with optional content search)
    if search:
        def matches_search(inv):
            # Always search query and ID
            if search in inv.get("query", "").lower():
                return True
            if search in inv.get("investigation_id", "").lower():
                return True
            # Optionally search in report content
            if search_content:
                return _search_in_report_content(inv, search)
            return False

        filtered = [inv for inv in filtered if matches_search(inv)]

    # Status filter
    if status_filter:
        filtered = [inv for inv in filtered if inv.get("status") == status_filter]

    # Date range filter
    if date_from:
        try:
            from_dt = datetime.fromisoformat(date_from)
            filtered = [inv for inv in filtered
                       if inv.get("started_at") and datetime.fromisoformat(inv["started_at"]) >= from_dt]
        except Exception:
            pass

    if date_to:
        try:
            to_dt = datetime.fromisoformat(date_to)
            filtered = [inv for inv in filtered
                       if inv.get("started_at") and datetime.fromisoformat(inv["started_at"]) <= to_dt]
        except Exception:
            pass

    # Tags filter
    if tags_filter:
        tag_list = [t.strip().lower() for t in tags_filter.split(',') if t.strip()]
        if tag_list:
            filtered = [inv for inv in filtered
                       if any(t.lower() in [x.lower() for x in inv.get("tags", [])] for t in tag_list)]

    # Sort
    def sort_key(inv):
        if sort_by == 'elapsed':
            return inv.get("elapsed_seconds") or 0
        elif sort_by == 'subagent_count':
            return inv.get("subagent_count") or 0
        else:  # timestamp
            return inv.get("completed_at") or inv.get("started_at") or ""

    reverse = sort_order == 'desc'
    filtered.sort(key=sort_key, reverse=reverse)

    # Pagination
    total = len(filtered)
    paginated = filtered[offset:offset + limit]

    return jsonify({
        "investigations": paginated,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total
    })


# =============================================================================
# INVESTIGATION WORKSPACE FILES
# =============================================================================

@investigation_bp.route('/api/investigation/<investigation_id>/files')
def api_investigation_files(investigation_id):
    """
    List files in an investigation's workspace.
    """
    from investigation_engine import get_investigation_status

    status = get_investigation_status(investigation_id)

    if status is None:
        return jsonify({"error": "Investigation not found"}), 404

    workspace_dir = status.get("workspace_dir")
    if not workspace_dir:
        return jsonify({"error": "No workspace directory"}), 404

    workspace = Path(workspace_dir)
    if not workspace.exists():
        return jsonify({"error": "Workspace not found"}), 404

    files = []
    for f in workspace.rglob("*"):
        if f.is_file():
            try:
                rel_path = f.relative_to(workspace)
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "path": str(rel_path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime
                })
            except Exception:
                continue

    files.sort(key=lambda x: x["modified"], reverse=True)

    return jsonify({
        "investigation_id": investigation_id,
        "workspace": str(workspace),
        "files": files
    })


# =============================================================================
# STATISTICS
# =============================================================================

@investigation_bp.route('/api/investigation/history/stats')
def api_investigation_stats():
    """
    Get aggregate statistics about investigations.

    Returns:
    - total_investigations
    - completed_count
    - failed_count
    - avg_elapsed_seconds
    - total_subagents_spawned
    - unique_tags
    - investigations_by_month
    """
    from investigation_engine import load_investigation_state

    state = load_investigation_state()
    all_investigations = []
    if state.get("current"):
        all_investigations.append(state["current"])
    all_investigations.extend(state.get("history", []))

    tags = _load_investigation_tags()

    # Compute stats
    total = len(all_investigations)
    completed = sum(1 for inv in all_investigations if inv.get("status") == "completed")
    failed = sum(1 for inv in all_investigations if inv.get("status") == "failed")

    elapsed_times = [inv.get("elapsed_seconds", 0) for inv in all_investigations if inv.get("elapsed_seconds")]
    avg_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0

    total_subagents = sum(inv.get("subagent_count", 0) for inv in all_investigations)

    # Unique tags
    all_tags = set()
    for inv_tags in tags.values():
        all_tags.update(inv_tags)

    # Investigations by month
    by_month = {}
    for inv in all_investigations:
        started = inv.get("started_at", "")
        if started:
            try:
                dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                month_key = dt.strftime("%Y-%m")
                by_month[month_key] = by_month.get(month_key, 0) + 1
            except Exception:
                pass

    return jsonify({
        "total_investigations": total,
        "completed_count": completed,
        "failed_count": failed,
        "success_rate": round((completed / total * 100) if total > 0 else 0, 1),
        "avg_elapsed_seconds": round(avg_elapsed, 1),
        "total_subagents_spawned": total_subagents,
        "unique_tags": list(all_tags),
        "investigations_by_month": by_month
    })


# =============================================================================
# TAGGING
# =============================================================================

@investigation_bp.route('/api/investigation/<investigation_id>/tags', methods=['GET'])
def api_get_investigation_tags(investigation_id):
    """Get tags for a specific investigation."""
    tags = _load_investigation_tags()
    return jsonify({
        "investigation_id": investigation_id,
        "tags": tags.get(investigation_id, [])
    })


@investigation_bp.route('/api/investigation/<investigation_id>/tags', methods=['POST'])
def api_set_investigation_tags(investigation_id):
    """
    Set tags for an investigation.

    Request body:
    {
        "tags": ["tag1", "tag2"]
    }

    Or to add a single tag:
    {
        "tag": "new-tag"
    }
    """
    from investigation_engine import get_investigation_status

    # Verify investigation exists
    status = get_investigation_status(investigation_id)
    if status is None:
        return jsonify({"error": "Investigation not found"}), 404

    data = request.get_json() or {}
    tags_data = _load_investigation_tags()

    current_tags = set(tags_data.get(investigation_id, []))

    # Handle both array replacement and single tag add
    if "tags" in data:
        # Replace all tags
        new_tags = [t.strip() for t in data["tags"] if t.strip()]
        tags_data[investigation_id] = new_tags
    elif "tag" in data:
        # Add single tag
        new_tag = data["tag"].strip()
        if new_tag:
            current_tags.add(new_tag)
            tags_data[investigation_id] = list(current_tags)

    _save_investigation_tags(tags_data)

    return jsonify({
        "success": True,
        "investigation_id": investigation_id,
        "tags": tags_data.get(investigation_id, [])
    })


@investigation_bp.route('/api/investigation/<investigation_id>/tags/<tag>', methods=['DELETE'])
def api_delete_investigation_tag(investigation_id, tag):
    """Remove a specific tag from an investigation."""
    tags_data = _load_investigation_tags()

    if investigation_id not in tags_data:
        return jsonify({"error": "No tags found for investigation"}), 404

    current_tags = tags_data[investigation_id]
    tag_lower = tag.lower()

    # Find and remove tag (case-insensitive match)
    updated_tags = [t for t in current_tags if t.lower() != tag_lower]

    if len(updated_tags) == len(current_tags):
        return jsonify({"error": "Tag not found"}), 404

    tags_data[investigation_id] = updated_tags
    _save_investigation_tags(tags_data)

    return jsonify({
        "success": True,
        "investigation_id": investigation_id,
        "tags": updated_tags
    })


# =============================================================================
# EXPORT
# =============================================================================

def _build_pdf_html(investigation_id, status, enriched, report_content):
    """Build HTML optimized for PDF export."""
    # Escape HTML entities in report content
    import html
    escaped_report = html.escape(report_content) if report_content else 'No report available'

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {investigation_id}</title>
</head>
<body>
    <h1>Investigation Report</h1>
    <div class="meta">
        <p><strong>ID:</strong> {investigation_id}</p>
        <p><strong>Query:</strong> {html.escape(status.get("query", "N/A"))}</p>
        <p><strong>Status:</strong> {status.get("status", "N/A")}</p>
        <p><strong>Completed:</strong> {enriched.get("timestamp_display", "N/A")}</p>
        <p><strong>Duration:</strong> {enriched.get("elapsed_display", "N/A")}</p>
        <p><strong>Subagents:</strong> {status.get("subagent_count", "N/A")}</p>
        <p><strong>Tags:</strong> {', '.join(enriched.get("tags", [])) or 'None'}</p>
    </div>
    <hr>
    <div class="report">
        <pre>{escaped_report}</pre>
    </div>
</body>
</html>"""


def _get_pdf_styles():
    """Return CSS for PDF rendering."""
    return """
    body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }
    h1 { color: #333; border-bottom: 2px solid #58a6ff; padding-bottom: 10px; }
    .meta { background: #f5f5f5; padding: 15px; border-radius: 6px; margin-bottom: 20px; }
    .meta p { margin: 5px 0; }
    pre { background: #f0f0f0; padding: 15px; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word; font-size: 0.9em; }
    """


@investigation_bp.route('/api/investigation/<investigation_id>/export')
def api_export_investigation(investigation_id):
    """
    Export an investigation report.

    Query params:
    - format: 'markdown' (default), 'json', 'html', or 'pdf'
    - download: 'true' to trigger file download

    Returns the report in the requested format.
    """
    from investigation_engine import get_investigation_status, InvestigationStatus

    status = get_investigation_status(investigation_id)
    if status is None:
        return jsonify({"error": "Investigation not found"}), 404

    export_format = request.args.get('format', 'markdown')
    download = request.args.get('download', 'false').lower() == 'true'

    # Get report content
    report_path = status.get("report_path")
    report_content = ""

    if report_path:
        report_file = Path(report_path)
        if report_file.exists():
            report_content = report_file.read_text()

    # Load findings if available
    findings = None
    workspace_dir = status.get("workspace_dir")
    if workspace_dir:
        findings_path = Path(workspace_dir) / "artifacts" / "findings.json"
        if findings_path.exists():
            try:
                with open(findings_path, 'r') as f:
                    findings = json.load(f)
            except Exception:
                pass

    # Enrich status with tags
    enriched = _enrich_investigation_with_metadata(status)

    # PDF export
    if export_format == 'pdf':
        try:
            from weasyprint import HTML, CSS
        except ImportError:
            return jsonify({
                "error": "PDF export not available",
                "reason": "WeasyPrint is not installed",
                "install_hint": "Run: pip install weasyprint && apt-get install libpango-1.0-0 libpangocairo-1.0-0"
            }), 503

        try:
            html_content = _build_pdf_html(investigation_id, status, enriched, report_content)
            pdf = HTML(string=html_content).write_pdf(
                stylesheets=[CSS(string=_get_pdf_styles())]
            )

            return Response(
                pdf,
                mimetype='application/pdf',
                headers={
                    'Content-Disposition': f'attachment; filename=investigation_{investigation_id}.pdf'
                }
            )
        except Exception as e:
            return jsonify({
                "error": f"PDF generation failed: {str(e)}",
                "fallback_suggestion": "Try exporting as HTML instead"
            }), 500

    # Format response based on requested format
    if export_format == 'json':
        export_data = {
            "investigation_id": investigation_id,
            "query": status.get("query"),
            "status": status.get("status"),
            "started_at": status.get("started_at"),
            "completed_at": status.get("completed_at"),
            "elapsed_seconds": status.get("elapsed_seconds"),
            "subagent_count": status.get("subagent_count"),
            "tags": enriched.get("tags", []),
            "report_content": report_content,
            "findings": findings
        }

        if download:
            return Response(
                json.dumps(export_data, indent=2),
                mimetype='application/json',
                headers={
                    'Content-Disposition': f'attachment; filename=investigation_{investigation_id}.json'
                }
            )
        return jsonify(export_data)

    elif export_format == 'html':
        # Convert markdown to simple HTML
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {investigation_id}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 900px; margin: 40px auto; padding: 20px; background: #0d1117; color: #c9d1d9; }}
        h1, h2, h3 {{ color: #58a6ff; }}
        pre {{ background: #161b22; padding: 15px; border-radius: 6px; overflow-x: auto; }}
        code {{ background: #161b22; padding: 2px 6px; border-radius: 4px; }}
        .meta {{ color: #8b949e; margin-bottom: 20px; }}
        .tag {{ background: #30363d; padding: 3px 8px; border-radius: 12px; font-size: 0.85em; margin-right: 5px; }}
    </style>
</head>
<body>
    <h1>Investigation Report</h1>
    <div class="meta">
        <p><strong>ID:</strong> {investigation_id}</p>
        <p><strong>Query:</strong> {status.get("query", "N/A")}</p>
        <p><strong>Status:</strong> {status.get("status", "N/A")}</p>
        <p><strong>Completed:</strong> {enriched.get("timestamp_display", "N/A")}</p>
        <p><strong>Duration:</strong> {enriched.get("elapsed_display", "N/A")}</p>
        <p><strong>Subagents:</strong> {status.get("subagent_count", "N/A")}</p>
        <p><strong>Tags:</strong> {''.join(f'<span class="tag">{t}</span>' for t in enriched.get("tags", [])) or 'None'}</p>
    </div>
    <hr>
    <div class="report">
        <pre>{report_content}</pre>
    </div>
</body>
</html>"""

        if download:
            return Response(
                html_content,
                mimetype='text/html',
                headers={
                    'Content-Disposition': f'attachment; filename=investigation_{investigation_id}.html'
                }
            )
        return Response(html_content, mimetype='text/html')

    else:  # markdown (default)
        # Build markdown with metadata header
        md_header = f"""# Investigation Report: {investigation_id}

## Metadata
- **Query:** {status.get("query", "N/A")}
- **Status:** {status.get("status", "N/A")}
- **Started:** {status.get("started_at", "N/A")}
- **Completed:** {status.get("completed_at", "N/A")}
- **Duration:** {enriched.get("elapsed_display", "N/A")}
- **Subagents:** {status.get("subagent_count", "N/A")}
- **Tags:** {', '.join(enriched.get("tags", [])) or 'None'}

---

"""
        full_markdown = md_header + report_content

        if download:
            return Response(
                full_markdown,
                mimetype='text/markdown',
                headers={
                    'Content-Disposition': f'attachment; filename=investigation_{investigation_id}.md'
                }
            )
        return Response(full_markdown, mimetype='text/markdown')


# =============================================================================
# ALL TAGS (for autocomplete/filtering)
# =============================================================================

@investigation_bp.route('/api/investigation/tags')
def api_all_investigation_tags():
    """
    Get all unique tags used across investigations.

    Returns list of tags with usage counts.
    """
    tags_data = _load_investigation_tags()

    # Count tag usage
    tag_counts = {}
    for inv_id, inv_tags in tags_data.items():
        for tag in inv_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Sort by usage count
    sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))

    return jsonify({
        "tags": [{"tag": t, "count": c} for t, c in sorted_tags],
        "total_unique": len(tag_counts)
    })


# =============================================================================
# BULK OPERATIONS
# =============================================================================

@investigation_bp.route('/api/investigation/bulk/tags', methods=['POST'])
def api_bulk_add_tags():
    """Add a tag to multiple investigations."""
    data = request.get_json() or {}
    ids = data.get('ids', [])
    tag = data.get('tag', '').strip()

    if not ids or not tag:
        return jsonify({"error": "ids and tag required"}), 400

    tags_data = _load_investigation_tags()
    count = 0

    for inv_id in ids:
        if inv_id not in tags_data:
            tags_data[inv_id] = []
        if tag not in tags_data[inv_id]:
            tags_data[inv_id].append(tag)
            count += 1

    _save_investigation_tags(tags_data)

    return jsonify({
        "success": True,
        "tagged_count": count,
        "tag": tag
    })


@investigation_bp.route('/api/investigation/bulk/export', methods=['POST'])
def api_bulk_export():
    """Export multiple investigations as JSON bundle."""
    from investigation_engine import get_investigation_status

    data = request.get_json() or {}
    ids = data.get('ids', [])

    if not ids:
        return jsonify({"error": "ids required"}), 400

    exports = []
    for inv_id in ids:
        status = get_investigation_status(inv_id)
        if status:
            exports.append(_enrich_investigation_with_metadata(status))

    return jsonify({
        "success": True,
        "investigations": exports,
        "count": len(exports)
    })


# =============================================================================
# TAG STATISTICS
# =============================================================================

@investigation_bp.route('/api/investigation/tags/stats')
def api_tag_statistics():
    """
    Get tag analytics data.

    Returns:
    {
        "tag_counts": [{"tag": "name", "count": N}, ...],
        "co_occurrence": [{"tag1": "a", "tag2": "b", "count": N}, ...],
        "usage_by_month": {"tag": {"2025-12": N, ...}, ...}
    }
    """
    from investigation_engine import load_investigation_state

    tags_data = _load_investigation_tags()
    state = load_investigation_state()

    # Get all investigations for date info
    all_investigations = []
    if state.get("current"):
        all_investigations.append(state["current"])
    all_investigations.extend(state.get("history", []))

    # Map investigation_id to started_at date
    inv_dates = {}
    for inv in all_investigations:
        inv_id = inv.get("investigation_id", "")
        started = inv.get("started_at", "")
        if inv_id and started:
            try:
                dt = datetime.fromisoformat(started.replace('Z', '+00:00'))
                inv_dates[inv_id] = dt.strftime("%Y-%m")
            except Exception:
                pass

    # 1. Tag counts
    tag_counts = {}
    for inv_id, inv_tags in tags_data.items():
        for tag in inv_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    sorted_counts = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
    tag_counts_list = [{"tag": t, "count": c} for t, c in sorted_counts]

    # 2. Co-occurrence matrix (pairs of tags that appear together)
    co_occurrence = {}
    for inv_id, inv_tags in tags_data.items():
        if len(inv_tags) >= 2:
            # Sort tags for consistent pair naming
            sorted_tags = sorted(inv_tags)
            for i, t1 in enumerate(sorted_tags):
                for t2 in sorted_tags[i+1:]:
                    pair_key = f"{t1}|{t2}"
                    co_occurrence[pair_key] = co_occurrence.get(pair_key, 0) + 1

    co_occurrence_list = []
    for pair, count in sorted(co_occurrence.items(), key=lambda x: -x[1]):
        t1, t2 = pair.split("|")
        co_occurrence_list.append({"tag1": t1, "tag2": t2, "count": count})

    # 3. Usage by month per tag
    usage_by_month = {}
    for inv_id, inv_tags in tags_data.items():
        month = inv_dates.get(inv_id)
        if month:
            for tag in inv_tags:
                if tag not in usage_by_month:
                    usage_by_month[tag] = {}
                usage_by_month[tag][month] = usage_by_month[tag].get(month, 0) + 1

    return jsonify({
        "tag_counts": tag_counts_list,
        "co_occurrence": co_occurrence_list[:20],  # Top 20 pairs
        "usage_by_month": usage_by_month
    })


# =============================================================================
# SAVED SEARCHES
# =============================================================================

SAVED_SEARCHES_PATH = None


def _get_saved_searches_path():
    """Get the path to saved searches storage."""
    global SAVED_SEARCHES_PATH
    if SAVED_SEARCHES_PATH is None:
        SAVED_SEARCHES_PATH = STATE_DIR / "investigation_saved_searches.json"
    return SAVED_SEARCHES_PATH


def _load_saved_searches() -> list:
    """Load saved searches from storage."""
    path = _get_saved_searches_path()
    try:
        if path.exists():
            with open(path, 'r') as f:
                data = json.load(f)
                return data.get("searches", [])
    except Exception:
        pass
    return []


def _save_saved_searches(searches: list):
    """Save searches to storage."""
    path = _get_saved_searches_path()
    try:
        with open(path, 'w') as f:
            json.dump({"searches": searches}, f, indent=2)
    except Exception as e:
        print(f"Failed to save searches: {e}")


@investigation_bp.route('/api/investigation/saved-searches')
def api_list_saved_searches():
    """Return list of saved search configurations."""
    searches = _load_saved_searches()
    return jsonify({
        "searches": searches,
        "count": len(searches)
    })


@investigation_bp.route('/api/investigation/saved-searches', methods=['POST'])
def api_save_search():
    """Save current filter configuration with a name."""
    import uuid

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()

    if not name:
        return jsonify({"error": "Name is required"}), 400

    searches = _load_saved_searches()

    new_search = {
        "id": f"search_{uuid.uuid4().hex[:8]}",
        "name": name,
        "created_at": datetime.now().isoformat(),
        "search": data.get('search', ''),
        "status": data.get('status', ''),
        "date_from": data.get('date_from', ''),
        "date_to": data.get('date_to', ''),
        "sort_by": data.get('sort_by', 'timestamp'),
        "sort_order": data.get('sort_order', 'desc'),
        "tags": data.get('tags', []),
        "search_content": data.get('search_content', False)
    }

    searches.append(new_search)
    _save_saved_searches(searches)

    return jsonify({
        "success": True,
        "search": new_search
    })


@investigation_bp.route('/api/investigation/saved-searches/<search_id>', methods=['DELETE'])
def api_delete_saved_search(search_id):
    """Delete a saved search by ID."""
    searches = _load_saved_searches()
    original_len = len(searches)

    searches = [s for s in searches if s.get("id") != search_id]

    if len(searches) == original_len:
        return jsonify({"error": "Search not found"}), 404

    _save_saved_searches(searches)

    return jsonify({
        "success": True,
        "deleted_id": search_id
    })


# =============================================================================
# CONTENT SEARCH (with caching)
# =============================================================================

import time

# Cache for report content
_report_content_cache = {}
_cache_timestamps = {}
_cache_max_age = 300  # 5 minutes


def _get_report_content(inv: dict) -> str:
    """Get report content with caching."""
    inv_id = inv.get("investigation_id")
    now = time.time()

    # Check cache validity
    if inv_id in _report_content_cache:
        if now - _cache_timestamps.get(inv_id, 0) < _cache_max_age:
            return _report_content_cache[inv_id]

    # Load from file
    report_path = inv.get("report_path")
    if report_path and Path(report_path).exists():
        try:
            content = Path(report_path).read_text()
            _report_content_cache[inv_id] = content
            _cache_timestamps[inv_id] = now
            return content
        except Exception:
            pass

    return ""


def _search_in_report_content(inv: dict, search_term: str) -> bool:
    """Check if search term appears in report content."""
    content = _get_report_content(inv)
    return search_term.lower() in content.lower()


# =============================================================================
# TAG AUTO-SUGGESTIONS
# =============================================================================

STOPWORDS = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
             'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
             'would', 'could', 'should', 'may', 'might', 'must', 'shall',
             'can', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
             'from', 'as', 'into', 'through', 'during', 'before', 'after',
             'and', 'but', 'if', 'or', 'because', 'until', 'while',
             'what', 'which', 'who', 'this', 'that', 'how', 'why', 'when',
             'there', 'here', 'its', 'it', 'i', 'me', 'my', 'we', 'you',
             'your', 'all', 'any', 'some', 'no', 'not', 'more', 'most'}


@investigation_bp.route('/api/investigation/tags/suggest')
def api_suggest_tags():
    """
    Suggest tags based on query content.

    Query params:
    - query: Investigation query text
    - exclude: Comma-separated tags to exclude (already applied)

    Returns top 5 suggested tags.
    """
    query = request.args.get('query', '').strip().lower()
    exclude = set(t.strip().lower() for t in request.args.get('exclude', '').split(',') if t.strip())

    if not query:
        return jsonify({"suggestions": []})

    # Extract keywords from query (remove stopwords, keep words > 2 chars)
    words = []
    for word in re.split(r'[\s\-_.,!?:;]+', query):
        word = word.strip()
        if len(word) > 2 and word not in STOPWORDS:
            words.append(word)

    if not words:
        return jsonify({"suggestions": [], "query_keywords": []})

    # Get all existing tags
    tags_data = _load_investigation_tags()
    all_tags = set()
    for inv_tags in tags_data.values():
        all_tags.update(inv_tags)

    # Score tags by relevance to query words
    scored_tags = []
    for tag in all_tags:
        if tag.lower() in exclude:
            continue

        tag_lower = tag.lower()
        score = 0

        for word in words:
            if word == tag_lower:
                score += 10  # Exact match
            elif word in tag_lower or tag_lower in word:
                score += 5   # Partial match
            elif any(w in tag_lower for w in word.split()):
                score += 2   # Sub-word match

        if score > 0:
            scored_tags.append((tag, score))

    # Sort by score, return top 5
    scored_tags.sort(key=lambda x: -x[1])
    suggestions = [t[0] for t in scored_tags[:5]]

    return jsonify({
        "suggestions": suggestions,
        "query_keywords": words[:10]  # Return extracted keywords for debugging
    })
