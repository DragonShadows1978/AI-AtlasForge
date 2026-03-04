"""
Artifact Health API Routes Blueprint

Provides fast, cached artifact health data from:
1. /summary - reads artifact_manifest.json (fast, 60s TTL cache)
2. /status  - manifest metadata + last health report filename
3. /report  - live ArtifactHealthAnalyzer scan (always fresh)

Cache: module-level dict with timestamps, 60-second TTL.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

# Create Blueprint
artifact_health_bp = Blueprint('artifact_health', __name__, url_prefix='/api/artifact-health')

# ── Configuration ─────────────────────────────────────────────────────────────
# Default workspace/artifacts directory; overrideable via init function
_WORKSPACE_DIR: Path = Path("/home/vader/AI-AtlasForge/workspace/AtlasForge")
_ARTIFACTS_DIR: Optional[Path] = None

# ── 60-second TTL cache ───────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_TTL: float = 60.0


def _cache_get(key: str):
    entry = _CACHE.get(key)
    if entry and (time.monotonic() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _CACHE[key] = {"data": data, "ts": time.monotonic()}


def init_artifact_health_blueprint(artifacts_dir=None):
    """
    Initialize the artifact health blueprint with required dependencies.

    Accepts either the generic workspace/artifacts dir or workspace/AtlasForge/artifacts.
    If the provided directory does not contain artifact_manifest.json but
    workspace/AtlasForge/artifacts does, that path is preferred.
    """
    global _ARTIFACTS_DIR
    candidate = Path(artifacts_dir) if artifacts_dir else _WORKSPACE_DIR / "artifacts"
    # Prefer workspace/AtlasForge/artifacts when the candidate has no manifest
    atlasforge_artifacts = candidate.parent / "AtlasForge" / "artifacts"
    if (
        not (candidate / "artifact_manifest.json").exists()
        and atlasforge_artifacts.exists()
    ):
        candidate = atlasforge_artifacts
    _ARTIFACTS_DIR = candidate


def _get_artifacts_dir() -> Path:
    return _ARTIFACTS_DIR if _ARTIFACTS_DIR else (_WORKSPACE_DIR / "artifacts")


def _load_manifest() -> Optional[dict]:
    """Read artifact_manifest.json. Returns None if missing/malformed."""
    manifest_path = _get_artifacts_dir() / "artifact_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("artifact_health blueprint: failed to read manifest: %s", exc)
        return None


def _manifest_to_summary(manifest: dict) -> dict:
    """Convert manifest JSON to the /summary shape expected by the dashboard widget."""
    artifacts = manifest.get("artifacts", [])
    total = len(artifacts)

    categories: dict = {}
    naming_issues = 0
    for a in artifacts:
        cat = a.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        if not a.get("naming_valid", True):
            naming_issues += 1

    return {
        "total_files": total,
        "overall_health": 100 if total == 0 else max(0, 100 - naming_issues * 5),
        "orphans": 0,        # manifest doesn't store orphan counts; use /report for detail
        "duplicates": 0,
        "stale_files": 0,
        "categories": len(categories),
        "category_breakdown": categories,
        "naming_issues": naming_issues,
        "recommendations_count": naming_issues,
        "mission_id": manifest.get("mission_id", ""),
        "generated_at": manifest.get("generated_at", ""),
        "artifact_count": total,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@artifact_health_bp.route('/summary')
def api_artifact_health_summary():
    """
    Quick health summary — served from manifest + 60s TTL cache.

    This route never re-scans; it reads the pre-written artifact_manifest.json
    that ArtifactManagerIntegration produces at each cycle end.
    """
    cached = _cache_get("summary")
    if cached is not None:
        return jsonify(cached)

    manifest = _load_manifest()
    if manifest is None:
        result = {
            "error": "artifact_manifest.json not found — run a mission first",
            "overall_health": 0,
            "total_files": 0,
            "orphans": 0,
            "duplicates": 0,
            "stale_files": 0,
            "categories": 0,
            "recommendations_count": 0,
        }
        return jsonify(result)

    summary = _manifest_to_summary(manifest)
    _cache_set("summary", summary)
    return jsonify(summary)


@artifact_health_bp.route('/status')
def api_artifact_health_status():
    """
    Full status: manifest metadata + last health report filename.
    """
    cached = _cache_get("status")
    if cached is not None:
        return jsonify(cached)

    manifest = _load_manifest()
    artifacts_dir = _get_artifacts_dir()

    # Find the most-recent health report
    reports = sorted(artifacts_dir.glob("artifact_health_report_*.md")) if artifacts_dir.exists() else []
    last_report = reports[-1].name if reports else None

    result = {
        "artifacts_dir": str(artifacts_dir),
        "manifest_exists": manifest is not None,
        "artifact_count": manifest.get("artifact_count", 0) if manifest else 0,
        "mission_id": manifest.get("mission_id", "") if manifest else "",
        "generated_at": manifest.get("generated_at", "") if manifest else "",
        "last_health_report": last_report,
        "health_reports": [r.name for r in reports],
        "auto_move_enabled": False,
        "auto_fix_names_enabled": False,
    }
    _cache_set("status", result)
    return jsonify(result)


@artifact_health_bp.route('/report')
def api_artifact_health_report():
    """
    Live health report — always runs a fresh ArtifactHealthAnalyzer scan.
    """
    try:
        import sys
        af_root = Path("/home/vader/AI-AtlasForge")
        if str(af_root) not in sys.path:
            sys.path.insert(0, str(af_root))

        from af_engine.integrations.artifact_health import ArtifactHealthAnalyzer

        artifacts_dir = _get_artifacts_dir()
        if not artifacts_dir.exists():
            return jsonify({
                "error": f"Artifacts directory not found: {artifacts_dir}",
                "report": None,
                "markdown": "",
            })

        all_paths = [p for p in artifacts_dir.rglob("*") if p.is_file()]
        analyzer = ArtifactHealthAnalyzer()
        report = analyzer.generate_health_report(
            artifact_paths=all_paths,
            workspace_dir=artifacts_dir.parent,
        )
        markdown = analyzer.render_markdown(report)

        report_dict = {
            "total_artifacts": report.total_artifacts,
            "orphans": [
                {"path": str(o.path), "reason": o.reason}
                for o in report.orphans
            ],
            "duplicate_groups": [
                {"md5": g.md5_hash, "paths": [str(p) for p in g.paths], "size_bytes": g.size_bytes}
                for g in report.duplicate_groups
            ],
            "stale_files": [
                {"path": str(s.path), "days_old": s.days_old, "last_modified": s.last_modified}
                for s in report.stale_files
            ],
            "archive_candidates": [
                {
                    "path": str(c.path),
                    "score": c.score,
                    "days_old": c.days_old,
                    "reference_count": c.reference_count,
                    "reason": c.reason,
                }
                for c in report.archive_candidates
            ],
            "naming_violations": [
                {"path": str(v.path), "suggestion": v.suggestion}
                for v in report.naming_violations
            ],
            "is_healthy": report.is_healthy,
            "generated_at": report.generated_at,
        }

        return jsonify({"report": report_dict, "markdown": markdown})

    except Exception as exc:
        logger.exception("artifact_health /report error")
        return jsonify({"error": str(exc), "report": None, "markdown": ""})
