from af_engine.stages.analyzing import AnalyzingStageHandler
from af_engine.stages.base import StageContext


def _context(tmp_path, problem_statement):
    workspace = tmp_path / "workspace"
    artifacts = workspace / "artifacts"
    research = workspace / "research"
    tests = workspace / "tests"
    artifacts.mkdir(parents=True)
    research.mkdir()
    tests.mkdir()
    return StageContext(
        mission={
            "mission_id": "mission_spec_guard",
            "problem_statement": problem_statement,
            "mission_type": "full_rd",
        },
        mission_id="mission_spec_guard",
        original_mission=problem_statement,
        problem_statement=problem_statement,
        workspace_dir=str(workspace),
        artifacts_dir=str(artifacts),
        research_dir=str(research),
        tests_dir=str(tests),
        cycle_number=1,
        cycle_budget=1,
        iteration=0,
        max_iterations=10,
        history=[],
        cycle_history=[],
    )


def test_analyzing_rejects_selective_precision_false_premise(tmp_path):
    handler = AnalyzingStageHandler()
    problem = (
        "Compute full-precision dots ONLY for the tail interactions. "
        "No redundant computation. No computing full precision and throwing it away."
    )
    context = _context(tmp_path, problem)

    (tmp_path / "workspace" / "artifacts" / "implementation_plan.md").write_text(
        "## Estimated Files\n- core/true_apa_attention.py\n"
    )
    code_dir = tmp_path / "workspace" / "core"
    code_dir.mkdir()
    (code_dir / "true_apa_attention.py").write_text(
        "\n".join([
            "def true_apa_attention(query_data, key_data, bulk_scores, refine_mask, xp):",
            "    full_scores = xp.einsum('bhid,bhjd->bhij', query_data, key_data)",
            "    return xp.where(refine_mask, full_scores, bulk_scores)",
        ])
    )

    response = {
        "status": "success",
        "recommendation": "COMPLETE",
        "issues_found": [],
        "proposed_fixes": [],
        "continuation_missions": [],
    }

    result = handler.process_response(response, context)

    assert result.next_stage == "BUILDING"
    assert result.status == "needs_revision"
    assert result.output_data["_increment_iteration"] is True
    assert any("Selective/full-precision-only" in issue for issue in result.output_data["issues_found"])


def test_analyzing_allows_success_without_selective_contract(tmp_path):
    handler = AnalyzingStageHandler()
    problem = "Build a small utility and verify tests pass."
    context = _context(tmp_path, problem)
    (tmp_path / "workspace" / "artifacts" / "implementation_plan.md").write_text(
        "## Estimated Files\n- core/utility.py\n"
    )
    code_dir = tmp_path / "workspace" / "core"
    code_dir.mkdir()
    (code_dir / "utility.py").write_text("def utility():\n    return 1\n")

    response = {
        "status": "success",
        "recommendation": "COMPLETE",
        "issues_found": [],
        "proposed_fixes": [],
        "spec_compliance": {
            "hard_requirements": [
                {
                    "source": "mission",
                    "source_excerpt": problem,
                    "requirement": "Build the requested utility and verify tests pass.",
                    "status": "met",
                    "evidence": ["core/utility.py implements the utility"],
                    "deviation": None,
                    "mission_authorized_deviation": False,
                }
            ],
            "all_hard_requirements_met": True,
            "unmet_requirements": [],
            "unverifiable_requirements": [],
            "unauthorized_deviations": [],
        },
        "continuation_missions": [],
    }

    result = handler.process_response(response, context)

    assert result.next_stage == "CYCLE_END"
    assert result.output_data.get("_increment_iteration") is not True


def test_analyzing_rejects_missing_general_spec_ledger(tmp_path):
    handler = AnalyzingStageHandler()
    problem = "MUST produce a JSON report and DO NOT modify source files."
    context = _context(tmp_path, problem)
    (tmp_path / "workspace" / "artifacts" / "implementation_plan.md").write_text(
        "## Success Criteria\n- JSON report exists\n- Source files remain unchanged\n"
    )

    response = {
        "status": "success",
        "recommendation": "COMPLETE",
        "issues_found": [],
        "proposed_fixes": [],
        "continuation_missions": [],
    }

    result = handler.process_response(response, context)

    assert result.next_stage == "BUILDING"
    assert result.status == "needs_revision"
    assert any("spec_compliance" in issue for issue in result.output_data["issues_found"])


def test_analyzing_rejects_unmet_general_spec_ledger(tmp_path):
    handler = AnalyzingStageHandler()
    problem = "MUST produce a JSON report and DO NOT modify source files."
    context = _context(tmp_path, problem)
    (tmp_path / "workspace" / "artifacts" / "implementation_plan.md").write_text(
        "## Success Criteria\n- JSON report exists\n- Source files remain unchanged\n"
    )

    response = {
        "status": "success",
        "recommendation": "COMPLETE",
        "issues_found": [],
        "proposed_fixes": [],
        "spec_compliance": {
            "hard_requirements": [
                {
                    "source": "mission",
                    "source_excerpt": problem,
                    "requirement": "Produce JSON report and leave source files unchanged.",
                    "status": "unmet",
                    "evidence": [],
                    "deviation": "Used markdown instead of JSON because it is the industry standard.",
                    "mission_authorized_deviation": False,
                }
            ],
            "all_hard_requirements_met": False,
            "unmet_requirements": ["JSON report missing"],
            "unverifiable_requirements": [],
            "unauthorized_deviations": ["markdown instead of JSON"],
        },
        "continuation_missions": [],
    }

    result = handler.process_response(response, context)

    assert result.next_stage == "BUILDING"
    assert result.status == "needs_revision"
    assert any("unmet" in issue or "Unauthorized" in issue for issue in result.output_data["issues_found"])


def test_analyzing_prompt_makes_spec_authoritative(tmp_path):
    handler = AnalyzingStageHandler()
    context = _context(tmp_path, "Do the exact thing specified.")

    prompt = handler.get_prompt(context)

    assert "SPEC AUTHORITY" in prompt
    assert "Industry standard" in prompt
    assert "false-premise success" in prompt
