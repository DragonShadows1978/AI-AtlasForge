"""
URL Handlers Blueprint

Provides utility routes for handling various URL patterns:
- Mission artifact downloads
- Investigation report viewing
- File serving utilities
"""

from flask import Blueprint, jsonify, request, send_file, abort, make_response
from pathlib import Path
import logging
import mimetypes

logger = logging.getLogger(__name__)

# Create Blueprint
url_handlers_bp = Blueprint('url_handlers', __name__)

# Base paths - use centralized configuration
from atlasforge_config import BASE_DIR, MISSIONS_DIR, WORKSPACE_DIR
INVESTIGATIONS_DIR = BASE_DIR / "investigations"

# Import io_utils for workspace resolution
import io_utils


@url_handlers_bp.route('/missions/<mission_id>/artifacts/<path:filename>')
def serve_mission_artifact(mission_id, filename):
    """Serve mission artifacts.

    Supports both shared workspaces (project_workspace) and legacy per-mission workspaces.
    """
    try:
        # Security: prevent directory traversal
        if '..' in mission_id or '..' in filename:
            abort(400, "Invalid path")

        # Use centralized workspace resolver for correct path
        from .workspace_resolver import resolve_mission_workspace
        workspace = resolve_mission_workspace(mission_id, MISSIONS_DIR, WORKSPACE_DIR, io_utils)
        artifact_path = workspace / "artifacts" / filename

        if not artifact_path.exists():
            # Try without workspace subfolder (fallback for legacy direct artifacts)
            artifact_path = MISSIONS_DIR / mission_id / "artifacts" / filename

        if not artifact_path.exists():
            abort(404, f"Artifact not found: {filename}")

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(artifact_path))
        if content_type is None:
            content_type = 'application/octet-stream'

        return send_file(
            artifact_path,
            mimetype=content_type,
            as_attachment=False
        )
    except Exception as e:
        logger.error(f"Error serving artifact: {e}")
        abort(500, str(e))


@url_handlers_bp.route('/investigations/<investigation_id>/report')
def serve_investigation_report(investigation_id):
    """Serve investigation report."""
    try:
        # Security: prevent directory traversal
        if '..' in investigation_id:
            abort(400, "Invalid investigation ID")

        inv_dir = INVESTIGATIONS_DIR / investigation_id
        if not inv_dir.exists():
            abort(404, f"Investigation not found: {investigation_id}")

        # Try multiple report formats
        report_formats = [
            inv_dir / "artifacts" / "investigation_report.md",
            inv_dir / "artifacts" / "investigation_report.html",
            inv_dir / "artifacts" / "investigation_report.json",
            inv_dir / "investigation_report.md"
        ]

        for report_path in report_formats:
            if report_path.exists():
                content_type, _ = mimetypes.guess_type(str(report_path))
                if content_type is None:
                    content_type = 'text/plain'

                return send_file(
                    report_path,
                    mimetype=content_type,
                    as_attachment=False
                )

        abort(404, "Investigation report not found")
    except Exception as e:
        logger.error(f"Error serving investigation report: {e}")
        abort(500, str(e))


@url_handlers_bp.route('/workspace/artifacts/<path:filename>')
def serve_workspace_artifact(filename):
    """Serve workspace artifacts."""
    try:
        # Security: prevent directory traversal
        if '..' in filename:
            abort(400, "Invalid path")

        artifact_path = WORKSPACE_DIR / "artifacts" / filename

        if not artifact_path.exists():
            abort(404, f"Workspace artifact not found: {filename}")

        content_type, _ = mimetypes.guess_type(str(artifact_path))
        if content_type is None:
            content_type = 'application/octet-stream'

        return send_file(
            artifact_path,
            mimetype=content_type,
            as_attachment=False
        )
    except Exception as e:
        logger.error(f"Error serving workspace artifact: {e}")
        abort(500, str(e))
