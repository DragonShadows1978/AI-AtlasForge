"""
Artifact Health API Routes Blueprint

Contains routes for:
- Quick health summary
- Full status with index info
- Detailed health report
"""

from flask import Blueprint, jsonify
from pathlib import Path
import sys

# Create Blueprint
artifact_health_bp = Blueprint('artifact_health', __name__, url_prefix='/api/artifact-health')

# Configuration - set via init function
ARTIFACTS_DIR = None


def init_artifact_health_blueprint(artifacts_dir=None):
    """Initialize the artifact health blueprint with required dependencies."""
    global ARTIFACTS_DIR
    ARTIFACTS_DIR = artifacts_dir or Path("/home/vader/AI-AtlasForge/workspace/artifacts")


def _get_artifact_module():
    """Import artifact manager module with proper path setup."""
    # Add workspace to path if needed
    workspace_path = Path("/home/vader/AI-AtlasForge/workspace/AtlasForge")
    if str(workspace_path) not in sys.path:
        sys.path.insert(0, str(workspace_path))

    try:
        from artifact_manager.cycle_hook import ArtifactCycleHook
        from artifact_manager.health_reporter import ArtifactHealthReporter
        return ArtifactCycleHook, ArtifactHealthReporter
    except ImportError as e:
        return None, None


@artifact_health_bp.route('/summary')
def api_artifact_health_summary():
    """Get quick health summary."""
    try:
        ArtifactCycleHook, ArtifactHealthReporter = _get_artifact_module()
        if ArtifactHealthReporter is None:
            return jsonify({
                "error": "Artifact manager not available",
                "overall_health": 0,
                "total_files": 0,
                "orphans": 0,
                "duplicates": 0,
                "stale_files": 0,
                "categories": 0,
                "recommendations_count": 0
            })

        reporter = ArtifactHealthReporter(ARTIFACTS_DIR)
        summary = reporter.get_quick_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "overall_health": 0,
            "total_files": 0,
            "orphans": 0,
            "duplicates": 0,
            "stale_files": 0,
            "categories": 0,
            "recommendations_count": 0
        })


@artifact_health_bp.route('/status')
def api_artifact_health_status():
    """Get full status with index info."""
    try:
        ArtifactCycleHook, _ = _get_artifact_module()
        if ArtifactCycleHook is None:
            return jsonify({
                "error": "Artifact manager not available",
                "artifacts_dir": str(ARTIFACTS_DIR) if ARTIFACTS_DIR else None,
                "index_summary": {},
                "health": {},
                "auto_move_enabled": False,
                "auto_fix_names_enabled": False
            })

        hook = ArtifactCycleHook(artifacts_dir=ARTIFACTS_DIR)
        status = hook.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "artifacts_dir": str(ARTIFACTS_DIR) if ARTIFACTS_DIR else None,
            "index_summary": {},
            "health": {},
            "auto_move_enabled": False,
            "auto_fix_names_enabled": False
        })


@artifact_health_bp.route('/report')
def api_artifact_health_report():
    """Get detailed health report."""
    try:
        ArtifactCycleHook, ArtifactHealthReporter = _get_artifact_module()
        if ArtifactHealthReporter is None:
            return jsonify({
                "error": "Artifact manager not available",
                "report": None,
                "markdown": ""
            })

        reporter = ArtifactHealthReporter(ARTIFACTS_DIR)
        report = reporter.generate_report()
        markdown = reporter.generate_report_markdown(report)

        # Convert report to dict for JSON serialization
        report_dict = {
            "orphans": [
                {
                    "file_path": str(o.file_path),
                    "reason": o.reason,
                    "suggested_category": o.suggested_category
                }
                for o in report.orphans
            ],
            "duplicates": [
                {
                    "files": [str(f) for f in d.files],
                    "similarity_type": d.similarity_type,
                    "hash_or_pattern": d.hash_or_pattern
                }
                for d in report.duplicates
            ],
            "stale_files": [
                {
                    "file_path": str(s.file_path),
                    "category": s.category,
                    "age_days": s.age_days,
                    "last_modified": s.last_modified
                }
                for s in report.stale_files
            ],
            "category_health": {
                cat: {
                    "name": h.name,
                    "file_count": h.file_count,
                    "avg_age_days": h.avg_age_days,
                    "stale_count": h.stale_count,
                    "naming_issues": h.naming_issues,
                    "health_score": h.health_score
                }
                for cat, h in report.category_health.items()
            },
            "total_files": report.total_files,
            "total_categories": report.total_categories,
            "overall_health_score": report.overall_health_score,
            "generated_at": report.generated_at,
            "recommendations": report.recommendations
        }

        return jsonify({
            "report": report_dict,
            "markdown": markdown
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "report": None,
            "markdown": ""
        })
