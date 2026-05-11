"""Tests for af_engine.mission_profiles and MissionConfig integration.

Covers acceptance criterion #9: profile application, stage gate logic,
prompt modifier injection, and conductor stop behavior for non-full-rd profiles.
"""

import pytest

from af_engine.mission_profiles import (
    MISSION_TYPE_PROFILES,
    DEFAULT_MISSION_TYPE,
    STAGE_ORDER,
    apply_mission_type_profile,
    stage_allowed_for_mission,
    next_enabled_stage,
    is_valid_mission_type,
    get_profile,
)
from af_engine.mission_config import MissionConfig, MissionValidationError


# ---------------------------------------------------------------------------
# Profile map shape
# ---------------------------------------------------------------------------

EXPECTED_PROFILES = {
    "full_rd",
    "plan_only",
    "build_only",
    "test_red_team",
    "bug_hunt",
    "research_only",
    "review_existing",
}


def test_profile_map_completeness():
    """All 7 profiles exist with required keys."""
    assert set(MISSION_TYPE_PROFILES.keys()) == EXPECTED_PROFILES
    for key, profile in MISSION_TYPE_PROFILES.items():
        assert "label" in profile, f"{key} missing label"
        assert "start_stage" in profile, f"{key} missing start_stage"
        assert "enabled_stages" in profile, f"{key} missing enabled_stages"
        assert "prompt_modifier" in profile, f"{key} missing prompt_modifier"


def test_profile_map_stages_are_canonical():
    """All start_stage / enabled_stages values are canonical AtlasForge stages."""
    canonical = {"PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"}
    for key, profile in MISSION_TYPE_PROFILES.items():
        assert profile["start_stage"] in canonical, f"{key} start_stage not canonical"
        for s in profile["enabled_stages"]:
            assert s in canonical, f"{key} enabled_stages contains non-canonical {s}"


# ---------------------------------------------------------------------------
# apply_mission_type_profile
# ---------------------------------------------------------------------------

def test_apply_profile_full_rd():
    """full_rd: PLANNING start, all 5 stages enabled, no stop."""
    mission = {}
    apply_mission_type_profile(mission, "full_rd")
    assert mission["mission_type"] == "full_rd"
    assert mission["mission_type_label"] == "Full R&D"
    assert mission["current_stage"] == "PLANNING"
    assert mission["enabled_stages"] == [
        "PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"
    ]
    assert mission["stop_after_profile_complete"] is False
    assert "mission_profile" in mission


def test_apply_profile_bug_hunt():
    """bug_hunt: TESTING start, [TESTING, ANALYZING], stop=True, modifier present."""
    mission = {}
    apply_mission_type_profile(mission, "bug_hunt")
    assert mission["mission_type"] == "bug_hunt"
    assert mission["current_stage"] == "TESTING"
    assert mission["enabled_stages"] == ["TESTING", "ANALYZING"]
    assert mission["stop_after_profile_complete"] is True
    assert mission["mission_profile"]["prompt_modifier"]
    assert "concrete bugs" in mission["mission_profile"]["prompt_modifier"]
    assert mission["mission_profile"].get("allow_implementation") == "optional_patch_if_small"


def test_apply_profile_review_existing():
    """review_existing: ANALYZING start, [ANALYZING] only."""
    mission = {}
    apply_mission_type_profile(mission, "review_existing")
    assert mission["current_stage"] == "ANALYZING"
    assert mission["enabled_stages"] == ["ANALYZING"]
    assert mission["stop_after_profile_complete"] is True
    assert mission["mission_profile"].get("allow_code_writes") is False


def test_apply_profile_plan_only():
    """plan_only: PLANNING start, then ANALYZING creates the gated build follow-up."""
    mission = {}
    apply_mission_type_profile(mission, "plan_only")
    assert mission["current_stage"] == "PLANNING"
    assert mission["enabled_stages"] == ["PLANNING", "ANALYZING"]
    assert mission["stop_after_profile_complete"] is True
    assert mission["mission_profile"].get("allow_code_writes") is False


def test_apply_profile_build_only():
    """build_only: BUILDING start, [BUILDING] only, requires_existing_plan."""
    mission = {}
    apply_mission_type_profile(mission, "build_only")
    assert mission["current_stage"] == "BUILDING"
    assert mission["enabled_stages"] == ["BUILDING"]
    assert mission["mission_profile"].get("requires_existing_plan") is True


def test_apply_unknown_profile_falls_back_to_full_rd():
    """Unknown key → full_rd profile applied."""
    mission = {}
    apply_mission_type_profile(mission, "this_does_not_exist")
    assert mission["mission_type"] == "full_rd"
    assert mission["current_stage"] == "PLANNING"
    assert mission["stop_after_profile_complete"] is False


def test_apply_none_profile_falls_back_to_full_rd():
    """None key → full_rd profile applied."""
    mission = {}
    apply_mission_type_profile(mission, None)
    assert mission["mission_type"] == "full_rd"


def test_apply_profile_does_not_drop_existing_label_or_start_stage_in_mission_profile():
    """mission_profile dict excludes label/start_stage/enabled_stages."""
    mission = {}
    apply_mission_type_profile(mission, "bug_hunt")
    mp = mission["mission_profile"]
    assert "label" not in mp
    assert "start_stage" not in mp
    assert "enabled_stages" not in mp
    # But other profile fields are preserved
    assert "prompt_modifier" in mp


# ---------------------------------------------------------------------------
# stage_allowed_for_mission
# ---------------------------------------------------------------------------

def test_stage_allowed_empty_enabled_stages_returns_true():
    """Backwards-compat: missing enabled_stages → always True.

    Empty list, however, is treated semantically as "no stages enabled" (False).
    Only None / missing key falls back to unrestricted.
    """
    assert stage_allowed_for_mission({}, "PLANNING") is True
    assert stage_allowed_for_mission({"enabled_stages": None}, "TESTING") is True
    # Empty list: explicit "no stages" → False (semantic fix per red team)
    assert stage_allowed_for_mission({"enabled_stages": []}, "BUILDING") is False
    # Non-list (corrupted) → fail open for backwards-compat
    assert stage_allowed_for_mission({"enabled_stages": "PLANNING"}, "PLANNING") is True


def test_stage_allowed_in_list_returns_true():
    mission = {"enabled_stages": ["TESTING", "ANALYZING"]}
    assert stage_allowed_for_mission(mission, "TESTING") is True
    assert stage_allowed_for_mission(mission, "ANALYZING") is True


def test_stage_allowed_not_in_list_returns_false():
    mission = {"enabled_stages": ["TESTING", "ANALYZING"]}
    assert stage_allowed_for_mission(mission, "PLANNING") is False
    assert stage_allowed_for_mission(mission, "BUILDING") is False
    assert stage_allowed_for_mission(mission, "CYCLE_END") is False


# ---------------------------------------------------------------------------
# next_enabled_stage
# ---------------------------------------------------------------------------

def test_next_enabled_stage_finds_later_stage():
    """[TESTING, ANALYZING] from PLANNING → TESTING."""
    mission = {"enabled_stages": ["TESTING", "ANALYZING"]}
    assert next_enabled_stage(mission, "PLANNING") == "TESTING"
    assert next_enabled_stage(mission, "BUILDING") == "TESTING"


def test_next_enabled_stage_skips_to_furthest_enabled():
    """[ANALYZING] from PLANNING → ANALYZING (skipping BUILDING/TESTING)."""
    mission = {"enabled_stages": ["ANALYZING"]}
    assert next_enabled_stage(mission, "PLANNING") == "ANALYZING"


def test_next_enabled_stage_returns_none_when_no_later_enabled():
    """current=ANALYZING and only [TESTING, ANALYZING] enabled → None."""
    mission = {"enabled_stages": ["TESTING", "ANALYZING"]}
    assert next_enabled_stage(mission, "ANALYZING") is None


def test_next_enabled_stage_legacy_mission_walks_stage_order():
    """Missing enabled_stages (legacy mission) → walk STAGE_ORDER as if all enabled.

    Legacy missions have no profile and must remain unrestricted, so
    next_enabled_stage walks STAGE_ORDER unconditionally and returns the
    immediate successor. Distinguishes legacy from explicit-empty (which
    returns None — see test below).
    """
    assert next_enabled_stage({}, "PLANNING") == "BUILDING"
    assert next_enabled_stage({}, "BUILDING") == "TESTING"
    assert next_enabled_stage({}, "ANALYZING") == "CYCLE_END"
    # End of STAGE_ORDER: no later stage exists.
    assert next_enabled_stage({}, "CYCLE_END") is None


def test_next_enabled_stage_explicit_empty_list_returns_none():
    """Explicit enabled_stages=[] (all stages disabled) → None.

    Distinguishable from a legacy mission: legacy walks STAGE_ORDER,
    explicit-empty cannot find any enabled stage so returns None.
    """
    assert next_enabled_stage({"enabled_stages": []}, "PLANNING") is None
    assert next_enabled_stage({"enabled_stages": []}, "BUILDING") is None


def test_next_enabled_stage_non_list_enabled_treated_as_legacy():
    """Non-list enabled_stages (corrupt) → fail open like missing key (walk STAGE_ORDER)."""
    assert next_enabled_stage({"enabled_stages": "PLANNING"}, "PLANNING") == "BUILDING"
    assert next_enabled_stage({"enabled_stages": {"a": 1}}, "PLANNING") == "BUILDING"


def test_next_enabled_stage_unknown_current_returns_none():
    """current=COMPLETE (not in STAGE_ORDER) → None."""
    mission = {"enabled_stages": ["PLANNING", "BUILDING"]}
    assert next_enabled_stage(mission, "COMPLETE") is None
    # Same for legacy mission
    assert next_enabled_stage({}, "COMPLETE") is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_is_valid_mission_type():
    for key in EXPECTED_PROFILES:
        assert is_valid_mission_type(key) is True
    assert is_valid_mission_type("nope") is False
    assert is_valid_mission_type(None) is False
    assert is_valid_mission_type("") is False
    assert is_valid_mission_type(123) is False


def test_get_profile_returns_default_for_unknown():
    p = get_profile("not_a_key")
    assert p["label"] == MISSION_TYPE_PROFILES[DEFAULT_MISSION_TYPE]["label"]


def test_get_profile_returns_default_for_none():
    p = get_profile(None)
    assert p["label"] == MISSION_TYPE_PROFILES[DEFAULT_MISSION_TYPE]["label"]


def test_stage_order_excludes_complete():
    """STAGE_ORDER must not include the terminal COMPLETE stage."""
    assert "COMPLETE" not in STAGE_ORDER
    assert STAGE_ORDER == ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"]


# ---------------------------------------------------------------------------
# MissionConfig integration
# ---------------------------------------------------------------------------

def test_mission_config_accepts_mission_type():
    """MissionConfig.from_request honors a valid mission_type."""
    config, audit = MissionConfig.from_request({
        "problem_statement": "Test problem statement here",
        "mission_type": "plan_only",
    })
    assert config.mission_type == "plan_only"


def test_mission_config_default_mission_type_is_full_rd():
    """Missing mission_type defaults to full_rd."""
    config, audit = MissionConfig.from_request({
        "problem_statement": "Test problem statement here",
    })
    assert config.mission_type == "full_rd"


def test_mission_config_rejects_invalid_mission_type():
    """Unknown mission_type raises MissionValidationError."""
    with pytest.raises(MissionValidationError):
        MissionConfig.from_request({
            "problem_statement": "Test problem statement here",
            "mission_type": "not_a_real_profile",
        })


def test_to_mission_dict_applies_profile_plan_only():
    """to_mission_dict() for plan_only sets profile fields correctly."""
    config, audit = MissionConfig.from_request({
        "problem_statement": "Plan a feature for me here",
        "mission_type": "plan_only",
    })
    mission = config.to_mission_dict(
        mission_id="test_1",
        mission_workspace="/tmp/ws",
        mission_dir="/tmp/md",
        audit=audit,
    )
    assert mission["mission_type"] == "plan_only"
    assert mission["current_stage"] == "PLANNING"
    assert mission["enabled_stages"] == ["PLANNING", "ANALYZING"]
    assert mission["stop_after_profile_complete"] is True
    assert mission["mission_profile"]["prompt_modifier"]


def test_to_mission_dict_default_full_rd_backward_compat():
    """Missing mission_type produces shape identical to today's default."""
    config, audit = MissionConfig.from_request({
        "problem_statement": "Backwards-compat test mission here",
    })
    mission = config.to_mission_dict(
        mission_id="test_2",
        mission_workspace="/tmp/ws",
        mission_dir="/tmp/md",
        audit=audit,
    )
    assert mission["current_stage"] == "PLANNING"
    assert mission["enabled_stages"] == [
        "PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"
    ]
    assert mission["stop_after_profile_complete"] is False
    assert mission["mission_type"] == "full_rd"


def test_to_mission_dict_review_existing_starts_at_analyzing():
    """review_existing mission starts at ANALYZING, not PLANNING."""
    config, audit = MissionConfig.from_request({
        "problem_statement": "Review my existing code please please",
        "mission_type": "review_existing",
    })
    mission = config.to_mission_dict(
        mission_id="test_3",
        mission_workspace="/tmp/ws",
        mission_dir="/tmp/md",
        audit=audit,
    )
    assert mission["current_stage"] == "ANALYZING"
    assert mission["enabled_stages"] == ["ANALYZING"]


# ---------------------------------------------------------------------------
# Prompt modifier injection (synthesizes the conductor's behavior)
# ---------------------------------------------------------------------------

def _inject_modifier(prompt: str, mission: dict) -> str:
    """Mirror conductor's prompt-modifier injection logic."""
    modifier = (mission.get("mission_profile") or {}).get("prompt_modifier")
    if modifier:
        return f"{prompt.rstrip()}\n\n## MISSION TYPE INSTRUCTIONS\n{modifier}\n"
    return prompt


def test_prompt_modifier_injection_adds_header():
    """Synthesized injection: bug_hunt mission gets header + modifier text."""
    mission = {}
    apply_mission_type_profile(mission, "bug_hunt")
    base_prompt = "Original prompt body."
    final = _inject_modifier(base_prompt, mission)
    assert "## MISSION TYPE INSTRUCTIONS" in final
    assert "concrete bugs" in final
    assert final.startswith("Original prompt body.")


def test_prompt_modifier_injection_skips_when_empty_modifier():
    """full_rd has empty modifier → no header is added."""
    mission = {}
    apply_mission_type_profile(mission, "full_rd")
    base_prompt = "Original prompt body."
    final = _inject_modifier(base_prompt, mission)
    assert "## MISSION TYPE INSTRUCTIONS" not in final
    assert final == base_prompt


def test_prompt_modifier_injection_no_mission_profile_skips():
    """Mission without mission_profile (legacy) → no injection."""
    mission = {}  # no profile applied
    base_prompt = "Original prompt body."
    final = _inject_modifier(base_prompt, mission)
    assert "## MISSION TYPE INSTRUCTIONS" not in final


# ---------------------------------------------------------------------------
# Conductor stop-behavior for a non-full-rd profile (synthesized)
# ---------------------------------------------------------------------------

def test_conductor_stop_logic_for_review_existing_after_analyzing():
    """After ANALYZING completes, conductor should mark mission COMPLETE.

    Synthesizes the conductor's stage-gate loop: when a profile-restricted
    mission's current stage is not in enabled_stages and no later enabled
    stage exists, stop_after_profile_complete=True triggers COMPLETE.
    """
    mission = {}
    apply_mission_type_profile(mission, "review_existing")
    # Simulate ANALYZING handler advancing to CYCLE_END (out of enabled_stages)
    mission["current_stage"] = "CYCLE_END"

    current_stage = mission["current_stage"]
    if not stage_allowed_for_mission(mission, current_stage):
        nxt = next_enabled_stage(mission, current_stage)
        if nxt is None and mission.get("stop_after_profile_complete"):
            mission["current_stage"] = "COMPLETE"

    assert mission["current_stage"] == "COMPLETE"


def test_conductor_skip_logic_for_bug_hunt_from_planning():
    """A bug_hunt mission starting at PLANNING should advance to TESTING.

    (This shouldn't happen in practice because apply_mission_type_profile
    sets current_stage=TESTING, but if a stale on-disk mission has
    current_stage=PLANNING, the gate should advance it.)
    """
    mission = {}
    apply_mission_type_profile(mission, "bug_hunt")
    mission["current_stage"] = "PLANNING"  # simulate stale state

    current_stage = mission["current_stage"]
    if not stage_allowed_for_mission(mission, current_stage):
        nxt = next_enabled_stage(mission, current_stage)
        if nxt:
            mission["current_stage"] = nxt

    assert mission["current_stage"] == "TESTING"


def test_plan_only_building_target_redirects_to_analyzing():
    """Planning completion for plan_only must reach ANALYZING so build follow-up is created."""
    mission = {}
    apply_mission_type_profile(mission, "plan_only")

    handler_target = "BUILDING"
    assert stage_allowed_for_mission(mission, handler_target) is False
    assert next_enabled_stage(mission, handler_target) == "ANALYZING"


def test_full_rd_does_not_trigger_skip_or_stop():
    """full_rd allows all stages — gate should never advance/stop."""
    mission = {}
    apply_mission_type_profile(mission, "full_rd")

    for stage in ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END"]:
        assert stage_allowed_for_mission(mission, stage) is True
