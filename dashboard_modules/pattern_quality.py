"""
AfterImage pattern quality dashboard API.

The routes fail closed with empty data when AfterImage is unavailable so the
dashboard can start even if the knowledge backend is offline.
"""

from __future__ import annotations

import importlib
import logging
import math
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, jsonify, request


logger = logging.getLogger(__name__)
pattern_quality_bp = Blueprint("pattern_quality", __name__, url_prefix="/api/pattern-quality")
MAX_PATTERN_TEXT = 1200
MAX_PATTERN_TOKENS = 80

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


def _json_safe(value: Any) -> Any:
    """Recursively coerce backend payloads into strict JSON-safe values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if value is None or isinstance(value, (str, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _number(value: Any, default: float = 0.0, low: Optional[float] = None, high: Optional[float] = None) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except Exception:
        return default
    if not math.isfinite(parsed):
        return default
    if low is not None:
        parsed = max(low, parsed)
    if high is not None:
        parsed = min(high, parsed)
    return parsed


def _integer(value: Any, default: int = 0, low: int = 0, high: int = 1_000_000_000) -> int:
    return int(_number(value, float(default), float(low), float(high)))


def _short_text(value: Any, limit: int = MAX_PATTERN_TEXT) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _sanitize_summary(summary: Any) -> Dict[str, Any]:
    source = summary if isinstance(summary, dict) else {}
    return {
        "total_clusters": _integer(source.get("total_clusters")),
        "canonical_clusters": _integer(source.get("canonical_clusters")),
        "duplicate_clusters": _integer(source.get("duplicate_clusters")),
        "total_occurrences": _integer(source.get("total_occurrences")),
        "average_quality_score": _number(source.get("average_quality_score"), 0.0, 0.0, 10.0),
    }


def _sanitize_pattern(pattern: Any) -> Dict[str, Any]:
    source = pattern if isinstance(pattern, dict) else {}
    tokens = source.get("normalized_tokens") or []
    if not isinstance(tokens, list):
        tokens = []
    return {
        **{str(key): _json_safe(value) for key, value in source.items() if key not in {"normalized_code", "normalized_tokens"}},
        "quality_score": _number(source.get("quality_score"), 0.0, 0.0, 10.0),
        "occurrence_count": _integer(source.get("occurrence_count")),
        "distinct_mission_count": _integer(source.get("distinct_mission_count")),
        "is_canonical": source.get("is_canonical") is True,
        "decay_factor": _number(source.get("decay_factor"), 0.0, 0.0, 1.0),
        "duplicate_score": _number(source.get("duplicate_score"), 0.0, 0.0, 1.0),
        "normalized_code": _short_text(source.get("normalized_code")),
        "normalized_tokens": [str(token) for token in tokens[:MAX_PATTERN_TOKENS]],
    }


def _sanitize_cluster(cluster: Any) -> Dict[str, Any]:
    sanitized = _sanitize_pattern(cluster)
    members = cluster.get("members") if isinstance(cluster, dict) else []
    safe_members = []
    if isinstance(members, list):
        for member in members[:8]:
            item = member if isinstance(member, dict) else {}
            safe_members.append({
                **{str(key): _json_safe(value) for key, value in item.items() if key != "similarity_score"},
                "similarity_score": _number(item.get("similarity_score"), 0.0, 0.0, 1.0),
            })
    sanitized["members"] = safe_members
    return sanitized


def _sanitize_curve(curve: Any) -> List[Dict[str, Any]]:
    if not isinstance(curve, list):
        return []
    safe = []
    for point in curve[:120]:
        source = point if isinstance(point, dict) else {}
        safe.append({
            "age_days": _number(source.get("age_days"), 0.0, 0.0, 100_000.0),
            "decay_factor": _number(source.get("decay_factor"), 0.0, 0.0, 1.0),
            "is_canonical": source.get("is_canonical") is True,
        })
    return safe


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
        summary = _sanitize_summary(backend.get_pattern_quality_summary())
        return {
            "available": True,
            "summary": summary,
            "top_patterns": [_sanitize_pattern(row) for row in backend.get_top_patterns(limit=5)],
            "duplicate_clusters": [_sanitize_cluster(row) for row in backend.get_duplicate_clusters(limit=5)],
            "decay_curve": _sanitize_curve(backend.get_decay_curve(days=180, points=12)),
        }

    return jsonify(_json_safe(_with_backend(load, _empty_payload("AfterImage backend unavailable"))))


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
        None,
    )
    return jsonify(_json_safe({"available": data is not None, "top_patterns": [_sanitize_pattern(row) for row in (data or [])]}))


@pattern_quality_bp.route("/clusters")
def api_pattern_quality_clusters():
    """Return duplicate clusters with recent member examples."""
    try:
        limit = _int_arg("limit", 10, high=50)
    except ValueError as exc:
        return _bad_request(str(exc))
    data = _with_backend(lambda backend: backend.get_duplicate_clusters(limit=limit), None)
    return jsonify(_json_safe({"available": data is not None, "duplicate_clusters": [_sanitize_cluster(row) for row in (data or [])]}))


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
        None,
    )
    return jsonify(_json_safe({"available": data is not None, "decay_curve": _sanitize_curve(data or [])}))
