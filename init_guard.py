#!/usr/bin/env python3
"""
Stage Guard: Enforces stage-specific restrictions in the R&D workflow.

The R&D Engine uses a 6-stage workflow with cycle iteration:
    PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE
        ^                                  |              |
        |__________________________________|              |
                 (if tests fail)                          |
        |_________________________________________________|
                  (if more cycles remain)

Each stage has specific tool restrictions to ensure clean execution:
- PLANNING: Read-only exploration + write to artifacts/research only
- BUILDING: Full write access
- TESTING: Full write access
- ANALYZING: Write only to reports/analysis
- CYCLE_END: Write only to reports/artifacts (generates cycle reports)
- COMPLETE: Read-only

This module provides:
1. System prompt additions for each stage
2. Tool blocking rules
3. Validation utilities
"""

from typing import List, Set
from enum import Enum
from dataclasses import dataclass


class RDStage(Enum):
    """R&D Engine stages (6-stage workflow with cycle iteration)."""
    PLANNING = "PLANNING"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    ANALYZING = "ANALYZING"
    CYCLE_END = "CYCLE_END"
    COMPLETE = "COMPLETE"


@dataclass
class StageToolPolicy:
    """Defines which tools are allowed/blocked for a stage."""
    stage: RDStage
    allowed_tools: Set[str]
    blocked_tools: Set[str]
    write_paths_allowed: List[str]  # Globs for allowed write paths

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed in this stage."""
        if self.blocked_tools and tool_name in self.blocked_tools:
            return False
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return True  # Default allow if no restrictions

    def can_write_path(self, path: str) -> bool:
        """Check if writing to a path is allowed."""
        import fnmatch
        if not self.write_paths_allowed:
            return False
        return any(fnmatch.fnmatch(path, pattern) for pattern in self.write_paths_allowed)


# Stage-specific tool policies
STAGE_POLICIES = {
    RDStage.PLANNING: StageToolPolicy(
        stage=RDStage.PLANNING,
        allowed_tools={
            "Read",
            "Glob",
            "Grep",
            "WebFetch",
            "WebSearch",
            "Task",  # For research subagents
            "Write",  # Only for artifacts
            "Edit",   # Only for artifacts
            "Bash",  # Read-only bash commands allowed for exploration
        },
        blocked_tools={
            "NotebookEdit",  # No notebook changes
        },
        write_paths_allowed=[
            "*/artifacts/*",
            "*/research/*",
            "*implementation_plan.md",
        ]
    ),

    RDStage.BUILDING: StageToolPolicy(
        stage=RDStage.BUILDING,
        allowed_tools=set(),  # All tools allowed
        blocked_tools=set(),  # None blocked
        write_paths_allowed=["*"]  # Can write anywhere
    ),

    RDStage.TESTING: StageToolPolicy(
        stage=RDStage.TESTING,
        allowed_tools=set(),  # All tools allowed
        blocked_tools=set(),
        write_paths_allowed=["*"]
    ),

    RDStage.ANALYZING: StageToolPolicy(
        stage=RDStage.ANALYZING,
        allowed_tools={
            "Read",
            "Glob",
            "Grep",
            "WebFetch",
            "WebSearch",
            "Task",
            "Write",  # Only for reports
            "Edit",   # Only for reports
        },
        blocked_tools=set(),
        write_paths_allowed=[
            "*/artifacts/*",
            "*/research/*",
            "*analysis.md",
            "*report.md",
            "*test_results.md",
        ]
    ),

    RDStage.CYCLE_END: StageToolPolicy(
        stage=RDStage.CYCLE_END,
        allowed_tools={
            "Read",
            "Glob",
            "Grep",
            "Write",  # Only for reports and continuation prompts
            "Edit",   # Only for reports
            "Task",   # For research subagents if needed
        },
        blocked_tools=set(),  # Path restrictions handle write blocking
        write_paths_allowed=[
            "*/artifacts/*",
            "*/research/*",
            "*report.md",
            "*report.json",
            "*cycle_report*",
            "*mission_logs/*",
        ]
    ),

    RDStage.COMPLETE: StageToolPolicy(
        stage=RDStage.COMPLETE,
        allowed_tools={
            "Read",
            "Glob",
            "Grep",
        },
        blocked_tools={
            "Edit",
            "Write",
            "NotebookEdit",
            "Bash",
        },
        write_paths_allowed=[]
    ),
}


class InitGuard:
    """
    Guards against inappropriate tool usage in each R&D stage.

    Usage in atlasforge_engine.py:
        from init_guard import InitGuard

        guard = InitGuard()
        if stage == "PLANNING":
            additional_prompt = guard.get_planning_system_prompt()
    """

    @staticmethod
    def get_blocked_tools(stage: str = "PLANNING") -> List[str]:
        """Get list of blocked tools for a stage."""
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)
            if policy:
                return list(policy.blocked_tools)
        except ValueError:
            pass
        return []

    @staticmethod
    def get_allowed_tools(stage: str = "PLANNING") -> List[str]:
        """Get list of allowed tools for a stage."""
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)
            if policy and policy.allowed_tools:
                return list(policy.allowed_tools)
        except ValueError:
            pass
        return []

    @staticmethod
    def get_planning_system_prompt() -> str:
        """Get the system prompt addition for PLANNING stage."""
        return """
## PLANNING STAGE - UNDERSTAND & PLAN

You are in the PLANNING stage. This is the FIRST stage of the R&D workflow.
Your job is to understand the mission AND create an implementation plan.

### WRITE RESTRICTIONS:
- You may write **ONLY to artifacts/ or research/ directories**
- Creating or modifying code files (*.py, *.js, *.ts, etc.) is FORBIDDEN
- Using `NotebookEdit` is FORBIDDEN
- Writing anywhere except artifacts/ or research/ is FORBIDDEN

### ALLOWED ACTIONS:
- Reading any files to understand the codebase
- Searching and grepping for relevant code
- Using `Bash` for read-only commands (ls, git status, pwd, etc.)
- Writing to `artifacts/implementation_plan.md`
- Writing to `research/*.md`
- Spawning research subagents with `Task`
- Using `WebFetch` and `WebSearch` for research

### Your GOALS in PLANNING (in order):
1. **READ** and understand the mission statement
2. **EXPLORE** the codebase to understand existing patterns
3. **IDENTIFY** key requirements and constraints
4. **MAKE** reasonable assumptions for any ambiguities (you are AUTONOMOUS)
5. **DESIGN** the implementation approach
6. **WRITE** a detailed plan to `artifacts/implementation_plan.md`
7. **RESPOND** with the planning complete JSON

### Why this matters:
The PLANNING stage combines mission understanding with implementation design.
This prevents wasted tokens on a separate "understanding" phase.
All actual code implementation happens in the BUILDING stage.

**Focus on UNDERSTANDING + PLANNING, not implementing. Save code for BUILDING stage.**
"""

    @staticmethod
    def get_analyzing_system_prompt() -> str:
        """Get the system prompt addition for ANALYZING stage."""
        return """
## ANALYZING STAGE - WRITE RESTRICTIONS

You are in the ANALYZING stage. You may write **ONLY reports and analysis**.

### FORBIDDEN in ANALYZING:
- Modifying source code
- Creating new features
- Bug fixes (those go in next BUILDING iteration)

### ALLOWED in ANALYZING:
- Reading any files
- Running tests (read-only verification)
- Writing analysis to `research/analysis.md`
- Writing test results to `artifacts/test_results.md`

### Your ONLY goals in ANALYZING:
1. Evaluate test results
2. Analyze what worked and what didn't
3. Determine if mission is complete or needs revision
4. Write analysis and respond with recommendation

**Analyze, don't fix. If fixes are needed, recommend BUILDING stage.**
"""

    @staticmethod
    def get_cycle_end_system_prompt() -> str:
        """Get the system prompt addition for CYCLE_END stage."""
        return """
## CYCLE_END STAGE - REPORT & CONTINUE

You are in the CYCLE_END stage. This stage generates cycle reports and continuation prompts.

### WRITE RESTRICTIONS:
- You may write **ONLY to artifacts/, research/, or mission_logs/ directories**
- Modifying source code is FORBIDDEN
- All code changes belong in the BUILDING stage of the next cycle

### ALLOWED ACTIONS:
- Reading any files to gather cycle summary information
- Writing cycle reports to artifacts/
- Writing to mission_logs/ for archival
- Generating continuation prompts for the next cycle

### Your GOALS in CYCLE_END:
1. **CATALOG** all files created or modified during this cycle
2. **SUMMARIZE** what was accomplished and any issues encountered
3. **GENERATE** a cycle report (JSON or Markdown)
4. If cycles remain: **WRITE** a continuation prompt for the next cycle
5. If final cycle: **GENERATE** a comprehensive final mission report

### Cycle Report Contents:
- Summary of achievements
- List of all files created/modified
- Issues encountered and how they were resolved
- What remains to be done (if continuing)

**Focus on DOCUMENTING and PLANNING the next cycle, not implementing.**
"""

    @staticmethod
    def get_stage_prompt(stage: str) -> str:
        """Get the appropriate system prompt for any stage."""
        if stage == "PLANNING":
            return InitGuard.get_planning_system_prompt()
        elif stage == "ANALYZING":
            return InitGuard.get_analyzing_system_prompt()
        elif stage == "CYCLE_END":
            return InitGuard.get_cycle_end_system_prompt()
        return ""

    @staticmethod
    def validate_tool_usage(stage: str, tool_name: str) -> tuple[bool, str]:
        """
        Validate if a tool usage is allowed in a stage.

        Args:
            stage: Current R&D stage
            tool_name: Name of the tool being used

        Returns:
            Tuple of (is_allowed, reason)
        """
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)

            if not policy:
                return True, "No policy for stage"

            if not policy.is_tool_allowed(tool_name):
                return False, f"Tool '{tool_name}' is blocked in {stage} stage"

            return True, "Allowed"

        except ValueError:
            return True, f"Unknown stage: {stage}"

    @staticmethod
    def validate_write_path(stage: str, path: str) -> tuple[bool, str]:
        """
        Validate if writing to a path is allowed in a stage.

        Args:
            stage: Current R&D stage
            path: Path being written to

        Returns:
            Tuple of (is_allowed, reason)
        """
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)

            if not policy:
                return True, "No policy for stage"

            if not policy.can_write_path(path):
                return False, f"Writing to '{path}' not allowed in {stage} stage"

            return True, "Allowed"

        except ValueError:
            return True, f"Unknown stage: {stage}"


# Convenience function for quick checks
def is_write_allowed(stage: str) -> bool:
    """Quick check if any writing is allowed in stage."""
    try:
        rd_stage = RDStage(stage)
        policy = STAGE_POLICIES.get(rd_stage)
        if policy:
            return bool(policy.write_paths_allowed)
    except ValueError:
        pass
    return True


def get_stage_restrictions(stage: str) -> dict:
    """Get a dict describing restrictions for a stage."""
    try:
        rd_stage = RDStage(stage)
        policy = STAGE_POLICIES.get(rd_stage)
        if policy:
            return {
                "stage": stage,
                "allowed_tools": list(policy.allowed_tools) if policy.allowed_tools else "all",
                "blocked_tools": list(policy.blocked_tools) if policy.blocked_tools else "none",
                "write_paths": policy.write_paths_allowed if policy.write_paths_allowed else "none",
            }
    except ValueError:
        pass
    return {"stage": stage, "error": "unknown stage"}


if __name__ == "__main__":
    # Self-test
    print("Stage Guard - Self Test")
    print("=" * 50)

    guard = InitGuard()

    # Test PLANNING restrictions (now the first stage)
    print("\nPLANNING Stage:")
    print(f"  Blocked tools: {guard.get_blocked_tools('PLANNING')}")
    print(f"  Allowed tools: {guard.get_allowed_tools('PLANNING')}")

    # Test validations
    tests = [
        ("PLANNING", "Edit", True),   # Allowed but path-restricted
        ("PLANNING", "Read", True),
        ("PLANNING", "Write", True),  # Allowed but path-restricted
        ("PLANNING", "Grep", True),
        ("PLANNING", "NotebookEdit", False),  # Blocked
        ("BUILDING", "Edit", True),
        ("BUILDING", "Write", True),
        ("BUILDING", "NotebookEdit", True),
    ]

    print("\nTool validation tests:")
    for stage, tool, expected in tests:
        allowed, reason = guard.validate_tool_usage(stage, tool)
        status = "PASS" if allowed == expected else "FAIL"
        print(f"  {status}: {stage}/{tool} -> {allowed} ({reason})")

    # Test path validation
    print("\nPath validation tests:")
    path_tests = [
        ("PLANNING", "/home/user/project/artifacts/plan.md", True),
        ("PLANNING", "/home/user/project/src/code.py", False),
        ("BUILDING", "/home/user/project/src/code.py", True),
        ("ANALYZING", "/home/user/project/research/analysis.md", True),
    ]

    for stage, path, expected in path_tests:
        allowed, reason = guard.validate_write_path(stage, path)
        status = "PASS" if allowed == expected else "FAIL"
        print(f"  {status}: {stage}/{path} -> {allowed}")

    # Show system prompts
    print("\nSystem prompt for PLANNING (first 200 chars):")
    print(guard.get_planning_system_prompt()[:200] + "...")

    print("\nStage Guard self-test complete!")
