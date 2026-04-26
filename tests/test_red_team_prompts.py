def _focus_section(prompt: str) -> str:
    return prompt.split("## Your hunt focus for this session", 1)[1].split("## FINDINGS FILE", 1)[0]


def test_primary_red_team_profile_order_is_injection_boundary_logic():
    from adversarial_testing import blind_agent_runner as runner

    assert [profile.label for profile in runner._ATTACK_PROFILES[:3]] == [
        "Red Team - Injection",
        "Red Team - Boundary",
        "Red Team - Logic",
    ]
    assert [[category.value for category in profile.categories] for profile in runner._ATTACK_PROFILES[:3]] == [
        ["injection"],
        ["boundary", "type_confusion"],
        ["logic", "state_corruption"],
    ]


def test_red_team_work_units_get_distinct_focus_prompts(tmp_path):
    from adversarial_testing import blind_agent_runner as runner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    results_dir = workspace / "tests"

    orchestrator = runner.RedTeamOrchestrator(timeout=60, n_agents=3, work_budget=3000, safety_timeout=60)
    work_units = orchestrator._build_work_units(
        profiles=runner._ATTACK_PROFILES[:3],
        workspace_dir=workspace,
        mission_desc="mission",
        results_dir=results_dir,
    )

    focus_sections = [_focus_section(unit.prompt) for unit in work_units]
    assert "INJECTION VULNERABILITIES" in focus_sections[0]
    assert "BOUNDARY CONDITIONS and TYPE CONFUSION" in focus_sections[1]
    assert "LOGIC FLAWS and STATE CORRUPTION" in focus_sections[2]
    assert [unit.metadata["agent_kind"] for unit in work_units] == ["red_team", "red_team", "red_team"]
    assert [unit.metadata["profile_label"] for unit in work_units] == [
        "Red Team - Injection",
        "Red Team - Boundary",
        "Red Team - Logic",
    ]


def test_hierarchical_framework_does_not_append_build_prompt_to_red_team_unit(tmp_path):
    from adversarial_testing import blind_agent_runner as runner
    from hierarchical_framework import HierarchicalConfig, HierarchicalExperiment

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    orchestrator = runner.RedTeamOrchestrator(timeout=60, n_agents=1, work_budget=1000, safety_timeout=60)
    work_unit = orchestrator._build_work_units(
        profiles=runner._ATTACK_PROFILES[:1],
        workspace_dir=workspace,
        mission_desc="mission",
        results_dir=workspace / "tests",
    )[0]

    experiment = object.__new__(HierarchicalExperiment)
    experiment.config = HierarchicalConfig(
        mission_id="test-red-team",
        description="red team",
        max_agents=1,
        enable_streaming=False,
    )

    prompt = experiment._build_agent_prompt(work_unit)

    assert prompt == work_unit.prompt
    assert "build_complete" not in prompt
    assert "Completion Requirements" not in prompt
    assert "INJECTION VULNERABILITIES" in prompt
