"""
AfterImage pattern quality dashboard API.

The routes fail closed with empty data when AfterImage is unavailable so the
dashboard can start even if the knowledge backend is offline.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request


logger = logging.getLogger(__name__)
pattern_quality_bp = Blueprint("pattern_quality", __name__, url_prefix="/api/pattern-quality")

_config: Dict[str, Any] = {
    "base_dir": None,
    "backend_factory": None,
}


def init_pattern_quality_blueprint(
    base_dir: Optional[Path] = None,
    backend_factory: Optional[Callable[[], Any]] = None,
) -> None:
    """Initialize optional dependencies for tests or runtime discovery."""
    _config["base_dir"] = Path(base_dir) if base_dir else None
    _config["backend_factory"] = backend_factory


def _empty_payload(error: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    payload = {
        "available": False,
        "summary": {
            "total_clusters": 0,
            "canonical_clusters": 0,
            "duplicate_clusters": 0,
            "total_occurrences": 0,
            "average_quality_score": 0.0,
        },
        "top_patterns": [],
        "duplicate_clusters": [],
        "decay_curve": [],
    }
    if error:
        payload["error"] = error
    payload.update(extra)
    return payload


def _candidate_afterimage_paths() -> List[Path]:
    base_dir = _config.get("base_dir")
    candidates = [
        Path("/mnt/ForgeRealm/AI-AfterImage"),
        Path("/mnt/ForgeRealm/AI-AtlasForge/workspace/AI-AfterImage"),
        Path.home() / "AI-AfterImage",
    ]
    if base_dir:
        candidates.extend([
            Path(base_dir).parent / "AI-AfterImage",
            Path(base_dir) / "workspace" / "AI-AfterImage",
        ])
    return [path for path in candidates if path.exists() and (path / "afterimage").exists()]


def _get_backend():
    factory = _config.get("backend_factory")
    if factory:
        return factory()

    for candidate in _candidate_afterimage_paths():
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)

    config_module = importlib.import_module("afterimage.config")
    return config_module.get_storage_backend()


def _with_backend(operation: Callable[[Any], Any], fallback: Any):
    backend = None
    try:
        backend = _get_backend()
        return operation(backend)
    except Exception as exc:
        logger.debug("Pattern quality backend unavailable: %s", exc)
        return fallback
    finally:
        try:
            if backend is not None:
                backend.close()
        except Exception:
            pass


def _int_arg(name: str, default: int, low: int = 1, high: int = 100) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except Exception as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < low or value > high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _bad_request(message: str):
    return jsonify({"available": False, "error": message}), 400


@pattern_quality_bp.route("/summary")
def api_pattern_quality_summary():
    """Return aggregate quality statistics plus compact previews."""
    def load(backend):
        summary = backend.get_pattern_quality_summary()
        return {
            "available": True,
            "summary": summary,
            "top_patterns": backend.get_top_patterns(limit=5),
            "duplicate_clusters": backend.get_duplicate_clusters(limit=5),
            "decay_curve": backend.get_decay_curve(days=180, points=12),
        }

    return jsonify(_with_backend(load, _empty_payload("AfterImage backend unavailable")))


@pattern_quality_bp.route("/top")
def api_pattern_quality_top():
    """Return top-ranked or canonical pattern clusters."""
    try:
        limit = _int_arg("limit", 10, high=50)
    except ValueError as exc:
        return _bad_request(str(exc))
    canonical_only = request.args.get("canonical", "").lower() in {"1", "true", "yes"}
    data = _with_backend(
        lambda backend: backend.get_top_patterns(limit=limit, canonical_only=canonical_only),
        [],
    )
    return jsonify({"available": bool(data), "top_patterns": data})


@pattern_quality_bp.route("/clusters")
def api_pattern_quality_clusters():
    """Return duplicate clusters with recent member examples."""
    try:
        limit = _int_arg("limit", 10, high=50)
    except ValueError as exc:
        return _bad_request(str(exc))
    data = _with_backend(lambda backend: backend.get_duplicate_clusters(limit=limit), [])
    return jsonify({"available": bool(data), "duplicate_clusters": data})


@pattern_quality_bp.route("/decay")
def api_pattern_quality_decay():
    """Return decay curve samples for a selected cluster or generic pattern."""
    try:
        days = _int_arg("days", 180, high=730)
        points = _int_arg("points", 12, low=2, high=60)
    except ValueError as exc:
        return _bad_request(str(exc))
    cluster_id = request.args.get("cluster") or None
    data = _with_backend(
        lambda backend: backend.get_decay_curve(cluster_id=cluster_id, days=days, points=points),
        [],
    )
    return jsonify({"available": bool(data), "decay_curve": data})
