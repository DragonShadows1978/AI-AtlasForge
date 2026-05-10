"""
af_engine.mission_profiles - Mission Type Execution Profiles

Defines MISSION_TYPE_PROFILES (the 7 intent-based profiles) and the helpers
that the conductor and dashboard use to apply them.

The profile map is the single source of truth (verbatim from the reference doc
ATLASFORGE_MISSION_TYPE_PROFILES_2026-04-28.md). Helpers here mutate / read
mission dicts; they do not own state.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile map (verbatim from the reference doc)
# ---------------------------------------------------------------------------

MISSION_TYPE_PROFILES: Dict[str, Dict[str, Any]] = {
    "full_rd": {
        "label": "Full R&D",
        "start_stage": "PLANNING",
        "enabled_stages": [
            "PLANNING",
            "BUILDING",
            "TESTING",
            "ANALYZING",
            "CYCLE_END",
        ],
        "stop_after_profile_complete": False,
        "prompt_modifier": "",
    },

    "plan_only": {
        "label": "Plan Only",
        "start_stage": "PLANNING",
        "enabled_stages": ["PLANNING"],
        "stop_after_profile_complete": True,
        "allow_code_writes": False,
        "prompt_modifier": (
            "Produce a clear implementation plan and stop. "
            "Do not implement code changes."
        ),
    },

    "build_only": {
        "label": "Build Only",
        "start_stage": "BUILDING",
        "enabled_stages": ["BUILDING"],
        "stop_after_profile_complete": True,
        "requires_existing_plan": True,
        "prompt_modifier": (
            "Use the supplied plan/research as the source of truth. "
            "Implement only the requested build work. Do not run broad red-team analysis."
        ),
    },

    "test_red_team": {
        "label": "Test / Red Team Only",
        "start_stage": "TESTING",
        "enabled_stages": ["TESTING", "ANALYZING"],
        "stop_after_profile_complete": True,
        "allow_implementation": False,
        "prompt_modifier": (
            "Focus on validation, adversarial testing, regressions, edge cases, "
            "security risks, and failure modes in existing code. Report findings clearly."
        ),
    },

    "bug_hunt": {
        "label": "Bug Hunt",
        "start_stage": "TESTING",
        "enabled_stages": ["TESTING", "ANALYZING"],
        "stop_after_profile_complete": True,
        "allow_implementation": "optional_patch_if_small",
        "prompt_modifier": (
            "Prioritize finding concrete bugs. Prefer reproduction steps, failing tests, "
            "risk notes, and minimal patches only when the fix is obvious and scoped."
        ),
    },

    "research_only": {
        "label": "Research Only",
        "start_stage": "PLANNING",
        "enabled_stages": ["PLANNING", "ANALYZING"],
        "stop_after_profile_complete": True,
        "allow_code_writes": False,
        "prompt_modifier": (
            "Research and synthesize. Do not implement. Produce findings, options, "
            "tradeoffs, and recommended next steps."
        ),
    },

    "review_existing": {
        "label": "Review Existing Code",
        "start_stage": "ANALYZING",
        "enabled_stages": ["ANALYZING"],
        "stop_after_profile_complete": True,
        "allow_code_writes": False,
        "prompt_modifier": (
            "Review existing code. Prioritize bugs, regressions, missing tests, "
            "security issues, and maintenance risks. Do not modify files."
        ),
    },
}

DEFAULT_MISSION_TYPE = "full_rd"

# Canonical stage order for next-stage walks. Excludes COMPLETE (terminal).
STAGE_ORDER = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_valid_mission_type(profile_key: Any) -> bool:
    """Return True if `profile_key` is a known profile key string."""
    return isinstance(profile_key, str) and profile_key in MISSION_TYPE_PROFILES


def get_profile(profile_key: Optional[str]) -> Dict[str, Any]:
    """Return the profile dict for `profile_key`, or the default profile.

    Logs a warning when an unknown key is coerced. Non-string inputs (lists,
    dicts, ints, etc.) are treated as unknown rather than raising — the queue
    scheduler and conductor read this from possibly-corrupt mission.json files.
    """
    if not isinstance(profile_key, str) or not profile_key:
        if profile_key:
            logger.warning(
                "[mission_profiles] Non-string mission_type %r — falling back to %r",
                profile_key, DEFAULT_MISSION_TYPE,
            )
        return MISSION_TYPE_PROFILES[DEFAULT_MISSION_TYPE]
    if profile_key in MISSION_TYPE_PROFILES:
        return MISSION_TYPE_PROFILES[profile_key]
    logger.warning(
        "[mission_profiles] Unknown mission_type %r — falling back to %r",
        profile_key, DEFAULT_MISSION_TYPE,
    )
    return MISSION_TYPE_PROFILES[DEFAULT_MISSION_TYPE]


def apply_mission_type_profile(mission: Dict[str, Any], profile_key: Optional[str]) -> Dict[str, Any]:
    """Inject profile fields into a mission dict.

    Sets:
      - mission_type            (resolved key, "full_rd" if unknown/missing)
      - mission_type_label
      - enabled_stages
      - stop_after_profile_complete
      - mission_profile         (all profile fields except label/start_stage/enabled_stages)
      - current_stage           (set to profile.start_stage)

    Mutates and returns the mission dict (verbatim from the reference doc).
    Non-string profile_key inputs (list, dict, int, etc.) fall back to the
    default profile rather than raising — the queue scheduler reads this
    field from possibly-corrupt queued items.
    """
    if isinstance(profile_key, str) and profile_key in MISSION_TYPE_PROFILES:
        resolved_key = profile_key
        profile = MISSION_TYPE_PROFILES[profile_key]
    else:
        if profile_key:
            logger.warning(
                "[mission_profiles] Unknown mission_type %r — falling back to %r",
                profile_key, DEFAULT_MISSION_TYPE,
            )
        resolved_key = DEFAULT_MISSION_TYPE
        profile = MISSION_TYPE_PROFILES[DEFAULT_MISSION_TYPE]

    mission["mission_type"] = resolved_key
    mission["mission_type_label"] = profile["label"]
    mission["enabled_stages"] = list(profile["enabled_stages"])
    mission["stop_after_profile_complete"] = profile.get("stop_after_profile_complete", False)
    mission["mission_profile"] = {
        key: value for key, value in profile.items()
        if key not in {"label", "start_stage", "enabled_stages"}
    }
    mission["current_stage"] = profile["start_stage"]
    return mission


def stage_allowed_for_mission(mission: Dict[str, Any], stage: str) -> bool:
    """Return True if `stage` is enabled for this mission.

    Missing `enabled_stages` (key absent or None) returns True for backwards-compat:
    pre-profile missions have no enabled_stages and must remain unrestricted.

    Non-list `enabled_stages` (string, dict, etc.) is treated as missing — fail open
    so a corrupted field doesn't break legacy missions. An explicit empty list is
    treated as "no stages enabled" (returns False).
    """
    if not isinstance(mission, dict):
        return True
    enabled = mission.get("enabled_stages")
    if enabled is None:
        return True
    if not isinstance(enabled, list):
        return True
    return stage in enabled


def next_enabled_stage(mission: Dict[str, Any], current_stage: str) -> Optional[str]:
    """Return the next stage in STAGE_ORDER (after `current_stage`) that is enabled.

    Returns None if no later enabled stage exists, if mission is invalid, if
    `current_stage` is not in STAGE_ORDER, or — for explicit `enabled_stages=[]` —
    if all stages are disabled.

    Disambiguation rules:
      - Missing key / non-list `enabled_stages` (legacy mission): treat as
        "all stages enabled" — return the next stage in STAGE_ORDER, matching
        `stage_allowed_for_mission`'s legacy fail-open behavior. A caller that
        receives a stage name back is in the legacy regime.
      - Explicit `enabled_stages=[]` (profile says all disabled): return None.
        Callers can distinguish "no profile" from "empty profile" by checking
        the field directly when the return is None.
    """
    if not isinstance(mission, dict):
        return None

    try:
        idx = STAGE_ORDER.index(current_stage)
    except ValueError:
        return None

    enabled = mission.get("enabled_stages")
    # Legacy / corrupt: walk STAGE_ORDER unconditionally (all stages "allowed").
    if enabled is None or not isinstance(enabled, list):
        next_stages = STAGE_ORDER[idx + 1:]
        return next_stages[0] if next_stages else None

    # Explicit empty list: all stages disabled, no later stage exists.
    if not enabled:
        return None

    enabled_set = set(enabled)
    for stage in STAGE_ORDER[idx + 1:]:
        if stage in enabled_set:
            return stage
    return None


# ---------------------------------------------------------------------------
# Profile flag accessors (cycle 2)
# ---------------------------------------------------------------------------

# Soft cap on patch size for profiles that allow only "small" patches
# (e.g., bug_hunt). Measured as added lines per file write.
SMALL_PATCH_LINE_CAP = 50
_SOURCE_FILE_SUFFIXES = frozenset({
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".css", ".scss", ".sass", ".html", ".go", ".rs", ".java", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".swift",
    ".kt", ".kts", ".sh", ".bash", ".zsh", ".ps1", ".sql",
})

# Path-fragment markers identifying writes that are always allowed regardless
# of profile flags (artifacts/research/notes/test outputs). Stage policy still
# applies on top of this — a stage that disallows a path always wins over a
# profile that would have allowed it.
_ALWAYS_ALLOWED_PATH_MARKERS = (
    "/artifacts/",
    "/research/",
    "/tests/",
    "/test/",
    "/mission_logs/",
)


def _profile_dict(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Return the embedded `mission_profile` dict, or {}."""
    if not isinstance(mission, dict):
        return {}
    profile = mission.get("mission_profile")
    return profile if isinstance(profile, dict) else {}


def allow_code_writes(mission: Dict[str, Any]) -> Optional[bool]:
    """Return the mission's `allow_code_writes` flag.

    None  -> no opinion (defer to stage policy).
    False -> explicit ban on writing source files.
    True  -> explicit allow (rare; equivalent to None for stage-policy purposes).
    """
    profile = _profile_dict(mission)
    if "allow_code_writes" not in profile:
        return None
    return bool(profile["allow_code_writes"])


def allow_implementation(mission: Dict[str, Any]) -> Any:
    """Return the mission's `allow_implementation` flag value.

    Possible values:
      - None / missing -> no restriction (defer to stage policy)
      - False -> implementation forbidden entirely (test_red_team)
      - "optional_patch_if_small" -> allowed if patch is small (bug_hunt)
      - True -> explicit allow
    """
    profile = _profile_dict(mission)
    return profile.get("allow_implementation")


def requires_existing_plan(mission: Dict[str, Any]) -> bool:
    """Return True if the active profile requires an existing implementation plan
    before BUILDING can run (build_only).
    """
    profile = _profile_dict(mission)
    return bool(profile.get("requires_existing_plan"))


def _path_marker_match(path: str, markers: tuple = _ALWAYS_ALLOWED_PATH_MARKERS) -> bool:
    """Return True if `path` contains any of the always-allowed markers.

    Markers are compared with leading and trailing slashes to avoid matching
    e.g. `/foo/researcher/` for `research`. Also accepts the path STARTING with
    a marker (without leading slash), e.g. `tests/foo.py`.

    SECURITY: the path is normalized via `os.path.normpath` before marker
    comparison so `/tests/../src/secret.py` collapses to `/src/secret.py`
    and does NOT spuriously match `/tests/`. Without normalization a writer
    could embed `/tests/` anywhere in the path to bypass the
    `allow_implementation` and `allow_code_writes` flags.
    """
    if not isinstance(path, str) or not path:
        return False
    # Normalize separators and resolve `..` segments. We deliberately do NOT
    # call os.path.realpath (which touches the filesystem) — pure lexical
    # normalization is enough to defang `..` traversal in the marker check.
    import os as _os
    norm = _os.path.normpath(path).replace("\\", "/")
    if path.startswith("/") and not norm.startswith("/"):
        norm = "/" + norm
    for marker in markers:
        if marker in norm:
            return True
        bare = marker.strip("/") + "/"
        if norm.startswith(bare):
            return True
    return False


def _looks_like_source_file(path: str) -> bool:
    """Return True for source-like file extensions, without touching the filesystem."""
    if not isinstance(path, str) or not path:
        return False
    import os as _os
    norm = _os.path.normpath(path).replace("\\", "/")
    suffix = _os.path.splitext(norm)[1].lower()
    return suffix in _SOURCE_FILE_SUFFIXES


def effective_write_paths(mission: Dict[str, Any], stage: str) -> Optional[tuple]:
    """Return the profile-imposed write-path restriction tuple for `stage`.

    Returns:
      None  -> profile imposes no extra restriction; stage policy applies as-is.
      tuple -> profile narrows writes to these path patterns (in addition to
               whatever the stage policy already permits, which always wins on
               disallow).

    Currently the only profile-driven restriction is `allow_code_writes=False`,
    which forces non-source writes only. The returned patterns mirror the
    `_ALWAYS_ALLOWED_PATH_MARKERS` set used by `enforce_profile_implementation`
    so the two helpers agree on which non-source paths the profile permits
    (artifacts, research, tests, mission_logs).
    """
    if allow_code_writes(mission) is False:
        return (
            "*/artifacts/*",
            "*/research/*",
            "*/tests/*",
            "*/test/*",
            "*/mission_logs/*",
        )
    return None


def enforce_profile_implementation(
    mission: Dict[str, Any],
    stage: str,
    path: str,
    added_lines: Optional[int] = None,
) -> tuple:
    """Check `allow_implementation` against a proposed write to `path`.

    The flag is meaningful only during BUILDING / TESTING (the implementation
    stages). Other stages defer entirely to stage policy.

    Args:
      mission: mission dict (looks up mission_profile.allow_implementation)
      stage:   active stage name (e.g. "BUILDING", "TESTING")
      path:    proposed write target
      added_lines: optional count of new lines being added (used for the
                   "optional_patch_if_small" branch; None means "unknown",
                   in which case the small-patch branch passes through).

    Returns:
      (allowed: bool, reason: str)
    """
    if not isinstance(path, str) or not path:
        return False, "invalid path"

    flag = allow_implementation(mission)
    if flag is None or flag is True:
        if allow_code_writes(mission) is False and _looks_like_source_file(path):
            return False, (
                "Profile forbids code writes; source-like files are blocked "
                "even under artifacts/research"
            )
        return True, "no profile restriction"

    impl_stages = {"PLANNING", "BUILDING", "TESTING"}
    if stage not in impl_stages:
        return True, f"profile flag inactive for stage {stage}"

    # Always allow artifacts / research / tests / mission logs regardless of flag.
    if _path_marker_match(path):
        return True, "non-implementation path (artifacts/research/tests)"

    if flag is False:
        return False, (
            "Profile forbids implementation; "
            "writes to source files outside artifacts/research/tests are blocked"
        )

    if flag == "optional_patch_if_small":
        if added_lines is None:
            return False, "small-patch profile: added line count is required"
        if added_lines < 0:
            return False, "small-patch profile: added_lines cannot be negative"
        if added_lines <= SMALL_PATCH_LINE_CAP:
            return True, f"small-patch profile: {added_lines} <= {SMALL_PATCH_LINE_CAP}"
        return False, (
            f"small-patch profile: {added_lines} added lines exceeds "
            f"{SMALL_PATCH_LINE_CAP}-line cap"
        )

    # Unknown value — fail closed for safety.
    return False, f"unknown allow_implementation value: {flag!r}"
