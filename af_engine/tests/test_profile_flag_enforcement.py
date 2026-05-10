"""Profile-flag runtime enforcement tests (cycle 2).

Verifies that the three mission_profile flags actually constrain behavior at
the layers that matter:

  * `allow_code_writes=False`  -> InitGuard.validate_write_path narrows BUILDING
                                  and TESTING to artifacts/research-only paths
                                  AND get_disallowed_tools_for_cli treats those
                                  stages as PLANNING-equivalent for tool blocking.
  * `allow_implementation`     -> InitGuard.validate_write_path enforces the
                                  False/optional_patch_if_small variants.
  * `requires_existing_plan`   -> Conductor preflight check (here exercised via
                                  the mission_profiles helper + a plan-presence
                                  fixture).

These are the enforcement seams the cycle-2 plan calls for. Tests rely on
real path validation against a workspace under ATLASFORGE_ROOT, not mocks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import init_guard as _init_guard
from af_engine.mission_profiles import (
    SMALL_PATCH_LINE_CAP,
    allow_code_writes,
    allow_implementation,
    apply_mission_type_profile,
    effective_write_paths,
    enforce_profile_implementation,
    requires_existing_plan,
)
from init_guard import InitGuard


# ---------------------------------------------------------------------------
# Path fixtures: build absolute paths inside the configured workspace root so
# can_write_path's _is_under_workspace gate succeeds and we exercise the
# overlay logic, not the workspace gate.
# ---------------------------------------------------------------------------

AF_ROOT = os.environ.get(
    "ATLASFORGE_ROOT",
    str(Path(__file__).resolve().parents[2]),
)
AF_ROOT = os.path.realpath(AF_ROOT)
_init_guard._WORKSPACE_ROOTS = [AF_ROOT, "/tmp/atlasforge"]


def _ws(rel: str) -> str:
    """Return an absolute path under the AtlasForge workspace root."""
    return f"{AF_ROOT}/workspace/missiontype_test_{os.getpid()}/{rel.lstrip('/')}"


SRC_PATH = _ws("src/foo.py")
ARTIFACTS_PATH = _ws("artifacts/plan.md")
ARTIFACTS_SOURCE_PATH = _ws("artifacts/generated.py")
RESEARCH_PATH = _ws("research/notes.md")
RESEARCH_SOURCE_PATH = _ws("research/probe.js")
TESTS_PATH = _ws("tests/test_foo.py")


# ---------------------------------------------------------------------------
# allow_code_writes flag (test 1-4)
# ---------------------------------------------------------------------------


def test_allow_code_writes_false_blocks_building_source_writes():
    """plan_only profile must block source writes during BUILDING."""
    mission = apply_mission_type_profile({}, "plan_only")
    assert allow_code_writes(mission) is False

    ok, reason = InitGuard.validate_write_path("BUILDING", SRC_PATH, mission=mission)
    assert ok is False, "BUILDING source write must be blocked under allow_code_writes=False"
    assert "allow_code_writes" in reason or "Profile" in reason


def test_allow_code_writes_false_allows_artifacts_research_writes():
    """plan_only profile must still allow artifacts/research writes."""
    mission = apply_mission_type_profile({}, "plan_only")

    ok_a, _ = InitGuard.validate_write_path("BUILDING", ARTIFACTS_PATH, mission=mission)
    ok_r, _ = InitGuard.validate_write_path("BUILDING", RESEARCH_PATH, mission=mission)
    assert ok_a is True
    assert ok_r is True


def test_allow_code_writes_false_blocks_source_like_artifacts_research_files():
    """No-code profiles may write notes/plans, but not source files hidden there."""
    mission = apply_mission_type_profile({}, "plan_only")

    ok_a, reason_a = InitGuard.validate_write_path(
        "BUILDING", ARTIFACTS_SOURCE_PATH, mission=mission
    )
    ok_r, reason_r = InitGuard.validate_write_path(
        "BUILDING", RESEARCH_SOURCE_PATH, mission=mission
    )

    assert ok_a is False
    assert ok_r is False
    assert "code writes" in reason_a
    assert "code writes" in reason_r


def test_allow_code_writes_false_planning_unaffected():
    """PLANNING already restricts to artifacts/research. The profile overlay
    must not break that path - artifacts/research writes still allowed,
    source writes still blocked (by stage policy, not the overlay)."""
    mission = apply_mission_type_profile({}, "plan_only")

    ok_a, _ = InitGuard.validate_write_path("PLANNING", ARTIFACTS_PATH, mission=mission)
    assert ok_a is True

    ok_src, reason = InitGuard.validate_write_path("PLANNING", SRC_PATH, mission=mission)
    assert ok_src is False
    # Stage policy fires first ("not allowed in PLANNING stage"), not the overlay.
    assert "PLANNING" in reason


def test_no_profile_means_no_profile_restriction():
    """Backwards-compat: missing mission_profile means stage policy alone applies."""
    legacy_mission: dict = {}  # no mission_profile key
    ok, _ = InitGuard.validate_write_path("BUILDING", SRC_PATH, mission=legacy_mission)
    assert ok is True, "legacy missions must keep full BUILDING write permissions"

    # Same when mission=None entirely (stage policy only).
    ok_none, _ = InitGuard.validate_write_path("BUILDING", SRC_PATH)
    assert ok_none is True


# ---------------------------------------------------------------------------
# allow_implementation flag (test 5-9)
# ---------------------------------------------------------------------------


def test_allow_implementation_false_blocks_testing_source_writes():
    """test_red_team profile must block source writes during TESTING."""
    mission = apply_mission_type_profile({}, "test_red_team")
    assert allow_implementation(mission) is False

    ok, reason = InitGuard.validate_write_path("TESTING", SRC_PATH, mission=mission)
    assert ok is False
    assert "implementation" in reason.lower() or "profile" in reason.lower()


def test_allow_implementation_false_blocks_planning_source_writes():
    """test_red_team profile must block source writes even if resumed into PLANNING."""
    mission = apply_mission_type_profile({}, "test_red_team")

    ok, reason = enforce_profile_implementation(mission, "PLANNING", SRC_PATH)

    assert ok is False
    assert "implementation" in reason.lower() or "profile" in reason.lower()


def test_allow_implementation_false_allows_tests_research_writes():
    """test_red_team must still allow tests/research writes."""
    mission = apply_mission_type_profile({}, "test_red_team")

    ok_t, _ = InitGuard.validate_write_path("TESTING", TESTS_PATH, mission=mission)
    ok_r, _ = InitGuard.validate_write_path("TESTING", RESEARCH_PATH, mission=mission)
    ok_a, _ = InitGuard.validate_write_path("TESTING", ARTIFACTS_PATH, mission=mission)
    assert ok_t is True
    assert ok_r is True
    assert ok_a is True


def test_optional_patch_if_small_allows_small_source_patch():
    """bug_hunt with a 30-line patch must be allowed."""
    mission = apply_mission_type_profile({}, "bug_hunt")

    small = SMALL_PATCH_LINE_CAP - 20  # 30
    ok, reason = InitGuard.validate_write_path(
        "TESTING", SRC_PATH, mission=mission, added_lines=small
    )
    assert ok is True, reason


def test_optional_patch_if_small_blocks_large_source_patch():
    """bug_hunt with a 60-line patch must be blocked."""
    mission = apply_mission_type_profile({}, "bug_hunt")

    too_big = SMALL_PATCH_LINE_CAP + 10  # 60
    ok, reason = InitGuard.validate_write_path(
        "TESTING", SRC_PATH, mission=mission, added_lines=too_big
    )
    assert ok is False
    assert str(too_big) in reason or "exceeds" in reason


def test_optional_patch_if_small_rejects_negative_added_lines():
    """Negative line counts are invalid, not a tiny patch."""
    mission = apply_mission_type_profile({}, "bug_hunt")

    ok, reason = enforce_profile_implementation(
        mission, "TESTING", SRC_PATH, added_lines=-1
    )

    assert ok is False
    assert "negative" in reason


def test_optional_patch_if_small_requires_added_line_count_for_source_patch():
    """Unknown patch size must fail closed for bug_hunt source edits."""
    mission = apply_mission_type_profile({}, "bug_hunt")

    ok, reason = enforce_profile_implementation(
        mission, "TESTING", SRC_PATH, added_lines=None
    )

    assert ok is False
    assert "line count" in reason


def test_allow_implementation_missing_means_unrestricted():
    """full_rd has no allow_implementation - TESTING source writes pass through."""
    mission = apply_mission_type_profile({}, "full_rd")
    assert allow_implementation(mission) is None

    ok, _ = InitGuard.validate_write_path(
        "TESTING", SRC_PATH, mission=mission, added_lines=10000
    )
    assert ok is True


# ---------------------------------------------------------------------------
# requires_existing_plan flag (test 10-12)
# ---------------------------------------------------------------------------


def test_requires_existing_plan_true_when_plan_missing(tmp_path: Path):
    """build_only requires the plan; helper reports True for the flag."""
    mission = apply_mission_type_profile({}, "build_only")
    mission["mission_workspace"] = str(tmp_path)
    mission["iteration"] = 0

    assert requires_existing_plan(mission) is True

    # No artifacts/implementation_plan.md exists -> conductor should refuse.
    plan_path = tmp_path / "artifacts" / "implementation_plan.md"
    assert not plan_path.exists(), "fixture sanity"


def test_requires_existing_plan_satisfied_when_plan_present(tmp_path: Path):
    """build_only with the plan present - conductor preflight should proceed."""
    mission = apply_mission_type_profile({}, "build_only")
    mission["mission_workspace"] = str(tmp_path)
    mission["iteration"] = 0

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "implementation_plan.md").write_text("# plan")

    assert requires_existing_plan(mission) is True
    assert (tmp_path / "artifacts" / "implementation_plan.md").exists()


def test_conductor_requires_existing_plan_on_resume_iteration(tmp_path: Path):
    """Crash-recovery resumes still need the required plan before BUILDING."""
    import atlasforge_conductor as conductor

    mission = apply_mission_type_profile({}, "build_only")
    mission["mission_workspace"] = str(tmp_path)
    mission["iteration"] = 3

    assert conductor._requires_existing_plan_missing(mission, "BUILDING") is True


def test_conductor_redirects_disabled_stage_without_later_stage_to_complete():
    """Disabled post-handler targets must complete even when stop_after is false."""
    import atlasforge_conductor as conductor

    mission = {
        "enabled_stages": ["PLANNING"],
        "stop_after_profile_complete": False,
    }

    assert conductor._redirect_disabled_profile_stage(
        mission, "PLANNING", "BUILDING"
    ) == "COMPLETE"


def test_requires_existing_plan_missing_for_other_profiles():
    """full_rd / plan_only / bug_hunt do not require an existing plan."""
    for key in ("full_rd", "plan_only", "bug_hunt", "test_red_team",
                "research_only", "review_existing"):
        mission = apply_mission_type_profile({}, key)
        assert requires_existing_plan(mission) is False, key


# ---------------------------------------------------------------------------
# full_rd remains unrestricted (test 13)
# ---------------------------------------------------------------------------


def test_full_rd_profile_no_profile_restrictions():
    """Belt-and-suspenders: full_rd never narrows stage policy."""
    mission = apply_mission_type_profile({}, "full_rd")
    assert allow_code_writes(mission) is None
    assert allow_implementation(mission) is None
    assert requires_existing_plan(mission) is False

    for stage, path in (
        ("BUILDING", SRC_PATH),
        ("TESTING", SRC_PATH),
        ("BUILDING", ARTIFACTS_PATH),
        ("TESTING", TESTS_PATH),
    ):
        ok, reason = InitGuard.validate_write_path(stage, path, mission=mission)
        assert ok is True, f"full_rd unexpectedly blocked {stage}/{path}: {reason}"


# ---------------------------------------------------------------------------
# Helper-level coverage (effective_write_paths and enforce_profile_implementation)
# ---------------------------------------------------------------------------


_NO_CODE_WRITES_PATHS = (
    "*/artifacts/*",
    "*/research/*",
    "*/tests/*",
    "*/test/*",
    "*/mission_logs/*",
)


@pytest.mark.parametrize("key,expected", [
    ("plan_only", _NO_CODE_WRITES_PATHS),
    ("research_only", _NO_CODE_WRITES_PATHS),
    ("review_existing", _NO_CODE_WRITES_PATHS),
    ("full_rd", None),
    ("build_only", None),
    ("bug_hunt", None),
    ("test_red_team", None),
])
def test_effective_write_paths_per_profile(key, expected):
    mission = apply_mission_type_profile({}, key)
    assert effective_write_paths(mission, "BUILDING") == expected


def test_effective_write_paths_includes_tests_and_mission_logs():
    """Profiles with allow_code_writes=False MUST include tests/ and mission_logs/.

    Otherwise init_guard.validate_write_path's profile overlay would block test
    writes that enforce_profile_implementation would have permitted via
    _path_marker_match. Returned patterns must match the marker set so the two
    helpers agree.
    """
    mission = apply_mission_type_profile({}, "plan_only")
    paths = effective_write_paths(mission, "BUILDING")
    assert paths is not None
    assert "*/tests/*" in paths
    assert "*/test/*" in paths
    assert "*/mission_logs/*" in paths
    assert "*/artifacts/*" in paths
    assert "*/research/*" in paths


def test_enforce_profile_implementation_unknown_value_fails_closed():
    """Defensive: unknown allow_implementation value is treated as deny."""
    mission = {"mission_profile": {"allow_implementation": "not-a-real-value"}}
    ok, reason = enforce_profile_implementation(mission, "TESTING", SRC_PATH)
    assert ok is False
    assert "unknown" in reason.lower()


def test_enforce_profile_implementation_non_implementation_stage_passes():
    """The flag is no-op outside PLANNING/BUILDING/TESTING."""
    mission = apply_mission_type_profile({}, "test_red_team")

    ok, _ = enforce_profile_implementation(mission, "ANALYZING", SRC_PATH)
    assert ok is True


# ---------------------------------------------------------------------------
# get_disallowed_tools_for_cli overlay
# ---------------------------------------------------------------------------


def test_disallowed_tools_overlay_treats_building_as_planning_under_no_code_writes():
    """plan_only + BUILDING must produce the same disallowedTools as PLANNING."""
    plan_only = apply_mission_type_profile({}, "plan_only")
    overlay = InitGuard.get_disallowed_tools_for_cli("BUILDING", mission=plan_only)
    planning_baseline = InitGuard.get_disallowed_tools_for_cli("PLANNING")
    assert overlay == planning_baseline


def test_disallowed_tools_no_mission_unchanged():
    """Backwards-compat: omitting mission gives the same result as today."""
    legacy = InitGuard.get_disallowed_tools_for_cli("BUILDING")
    explicit_none = InitGuard.get_disallowed_tools_for_cli("BUILDING", mission=None)
    assert legacy == explicit_none


def test_disallowed_tools_full_rd_unchanged():
    """full_rd produces the same disallowed list as no-mission."""
    full = apply_mission_type_profile({}, "full_rd")
    overlayed = InitGuard.get_disallowed_tools_for_cli("BUILDING", mission=full)
    baseline = InitGuard.get_disallowed_tools_for_cli("BUILDING")
    assert overlayed == baseline


# ---------------------------------------------------------------------------
# Cycle-2 red-team regression: path-traversal bypass in _path_marker_match
# ---------------------------------------------------------------------------


def test_path_marker_match_resists_dotdot_traversal():
    """Red team agent 2 (cycle 2) found that `_path_marker_match` accepted
    paths like `/tests/../src/secret.py` because it did a bare substring
    check. After normpath() is applied, the traversal collapses to
    `/src/secret.py` and the marker no longer matches.
    """
    from af_engine.mission_profiles import _path_marker_match

    # All of these previously bypassed the marker; now correctly rejected.
    assert _path_marker_match("/tests/../src/secret.py") is False
    assert _path_marker_match("/foo/research/../src/secret.py") is False
    assert _path_marker_match("/artifacts/../etc/passwd") is False
    assert _path_marker_match("./tests/../private.py") is False

    # Sanity: legitimate paths still pass.
    assert _path_marker_match("/workspace/abc/tests/foo.py") is True
    assert _path_marker_match("research/notes.md") is True
    assert _path_marker_match("./artifacts/plan.md") is True
    assert _path_marker_match("/workspace/x/research/findings.md") is True


def test_enforce_profile_implementation_blocks_traversal_bypass():
    """Profile flag enforcement was bypassable: an agent could write
    `/workspace/x/tests/../src/secret.py` to evade `allow_implementation=False`.
    Verify that the normpath fix in _path_marker_match propagates to
    enforce_profile_implementation.
    """
    mission_no_impl = {"mission_profile": {"allow_implementation": False}}
    mission_no_writes = {"mission_profile": {"allow_code_writes": False}}

    bypass_paths = [
        "/workspace/x/tests/../src/secret.py",
        "/foo/research/../source.py",
        "/artifacts/../sensitive_config.py",
        "tests/../src/leak.py",
    ]
    for p in bypass_paths:
        ok, reason = enforce_profile_implementation(mission_no_impl, "TESTING", p)
        assert ok is False, f"traversal bypass for {p!r}: {reason!r}"
        assert "forbids implementation" in reason or "blocked" in reason
