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

import logging
import os
import types
from pathlib import PurePosixPath
from typing import Any, FrozenSet, List, Mapping, Tuple
from urllib.parse import unquote
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_STAGE_REPR_MAX = 64

# Workspace roots: ABSOLUTE paths under which writes are permitted (subject to
# stage policy). The deny-list `_SYSTEM_ROOT_DIRS` approach was inverted in
# iter-3: enumeration of "bad" roots leaves /opt, /srv, /mnt, /tmp,
# /home/<other_user>, /media, etc. as bypass surfaces. The correct model is
# allow-list: a write target must resolve under a configured workspace root.
#
# `ATLASFORGE_ROOT` overrides the default. Multiple roots can be specified
# colon-separated via `ATLASFORGE_WORKSPACE_ROOTS` for environments with
# secondary workspaces (CI runners, mounted volumes).
def _compute_workspace_roots() -> List[str]:
    raw = os.environ.get("ATLASFORGE_WORKSPACE_ROOTS", "")
    extras = [p.strip() for p in raw.split(":") if p.strip()]
    primary = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    roots = [primary] + extras
    # /tmp/atlasforge is always permitted (no username component, no widening).
    roots.append("/tmp/atlasforge")
    # Iter-6 H6: pytest tmp_path is a TEST-ONLY convenience. With USER unset
    # (containers, CI, cron) the previous unconditional append produced the
    # literal `/tmp/pytest-of-` prefix, which `_is_under_workspace` matches
    # against `/tmp/pytest-of-anyone/...` — widening writes to world-writable
    # tmp. Require BOTH a non-empty USER AND explicit opt-in via
    # ATLASFORGE_ALLOW_PYTEST_TMP=1 before adding the per-user pytest root.
    user = os.environ.get("USER", "").strip()
    allow_pytest = os.environ.get("ATLASFORGE_ALLOW_PYTEST_TMP", "0") == "1"
    if user and allow_pytest:
        roots.append(f"/tmp/pytest-of-{user}")
    # Normalize: rstrip trailing slashes, drop duplicates, drop empty.
    seen, out = set(), []
    for r in roots:
        rr = r.rstrip("/")
        if rr and rr not in seen:
            seen.add(rr)
            out.append(rr)
    return out


_WORKSPACE_ROOTS: List[str] = _compute_workspace_roots()

# Legacy system-root deny-list, retained as a defense-in-depth fast-reject for
# obvious system paths. The primary gate is `_is_under_workspace`.
_SYSTEM_ROOT_DIRS = frozenset({
    "etc", "root", "boot", "bin", "sbin", "usr", "var", "sys", "proc",
    "dev", "lib", "lib64",
})


def _is_under_workspace(path: str) -> bool:
    """Return True iff `path` resolves under a configured workspace root.

    Used for ABSOLUTE paths only; relative paths are joined against cwd
    via `os.path.realpath` before resolution. Symlink resolution is
    deliberate to defeat symlink-out-of-workspace attacks.
    """
    if not isinstance(path, str) or not path:
        return False
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for root in _WORKSPACE_ROOTS:
        if resolved == root or resolved.startswith(root + "/"):
            return True
    return False


def _safe_stage_repr(stage: Any) -> str:
    """Produce a bounded, control-char-escaped representation of a stage value.

    Prevents reason-bloat and log-injection from oversized or multiline
    inputs. Iter-3: escapes ALL C0 controls (\\x00-\\x1f) plus \\x7f, not
    just \\r and \\n. This keeps log lines parseable even when an attacker
    sprays ANSI-escape, BEL, or VT into stage names.
    """
    s = str(stage)
    if len(s) > _STAGE_REPR_MAX:
        s = s[:_STAGE_REPR_MAX] + "..."
    out_chars = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x20 or cp == 0x7f:
            out_chars.append("\\x%02x" % cp)
        else:
            out_chars.append(ch)
    return "".join(out_chars)


class RDStage(Enum):
    """R&D Engine stages (6-stage workflow with cycle iteration)."""
    PLANNING = "PLANNING"
    BUILDING = "BUILDING"
    TESTING = "TESTING"
    ANALYZING = "ANALYZING"
    CYCLE_END = "CYCLE_END"
    COMPLETE = "COMPLETE"


# Iter-4 C4: the universe-set against which --disallowedTools is computed.
# Missing entries here silently become "not blocked" in every stage because
# the per-stage blocklist is derived as (ALL_KNOWN_TOOLS - allowed) | blocked.
# A tool absent from this set can NEVER end up in the disallowed string.
# Includes:
#   - core file/shell tools (Read, Write, Edit, MultiEdit, Notebook, Bash*)
#   - search (Glob, Grep) + web (WebFetch, WebSearch)
#   - agent/task (Task, TaskOutput, TaskStop) + UX (TodoWrite, AskUserQuestion)
#   - workflow/control (SlashCommand, Skill, ToolSearch)
#   - monitoring/worktree/remote/cron
#   - well-known MCP tool aliases used by this project's web proxy
ALL_KNOWN_TOOLS: FrozenSet[str] = frozenset({
    # Core file/shell
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Bash", "BashOutput", "KillBash",
    # Search & exploration
    "Glob", "Grep",
    # Web (Anthropic built-ins, routed via MCP in this project)
    "WebFetch", "WebSearch",
    # Agent / task
    "Task", "TaskOutput", "TaskStop",
    # UX / dialog
    "TodoWrite", "AskUserQuestion",
    # Plan-mode (always blocked by base_blocked)
    "EnterPlanMode", "ExitPlanMode",
    # Workflow / control
    "SlashCommand", "Skill", "ToolSearch",
    # Monitoring / worktree / remote
    "Monitor", "EnterWorktree", "ExitWorktree", "RemoteTrigger",
    "PushNotification", "ScheduleWakeup",
    # Cron scheduler
    "CronCreate", "CronDelete", "CronList",
    # Well-known MCP aliases for the AtlasForge web proxy
    "mcp__atlasforge-web-proxy__WebSearch",
    "mcp__atlasforge-web-proxy__WebFetch",
    "mcp__atlasforge-web-proxy__WebResearch",
    "mcp__atlasforge-web-proxy__ImageSearch",
    # Iter-6 H1: Gemini-style tool aliases referenced in STAGE_POLICIES. Must
    # appear here so the fail-closed disallow set actually contains the
    # destructive ones (write_file, replace, run_shell_command). Without them,
    # a Gemini-backed subagent on an unknown stage retained full write/shell
    # access because tools absent from this set never enter the disallow string.
    "write_file", "replace", "run_shell_command",
    "read_file", "list_directory", "search_file_content",
})

# Read-only tools kept allowed when failing closed on unknown stages. Any tool
# NOT in this set is treated as capable of causing side effects and therefore
# blocked on the fail-closed path. Iter-6 H1: read_file/list_directory/
# search_file_content are the Gemini equivalents of Read/Glob/Grep — same
# semantic, so they remain allowed in fail-closed mode too.
_READ_ONLY_TOOLS: FrozenSet[str] = frozenset({
    "Read", "Glob", "Grep",
    "read_file", "list_directory", "search_file_content",
})


@dataclass(frozen=True)
class StageToolPolicy:
    """Defines which tools are allowed/blocked for a stage.

    Iter-4 H4: `frozen=True` + `frozenset`/`tuple` fields make the policy
    permanently immutable. Previously `policy.allowed_tools.add(...)` or
    `policy.write_paths_allowed.append(...)` silently corrupted process-wide
    policy, since STAGE_POLICIES is a module-level singleton.
    """
    stage: RDStage
    allowed_tools: FrozenSet[str] = field(default_factory=frozenset)
    blocked_tools: FrozenSet[str] = field(default_factory=frozenset)
    write_paths_allowed: Tuple[str, ...] = field(default_factory=tuple)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is allowed in this stage."""
        if self.blocked_tools and tool_name in self.blocked_tools:
            return False
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return True  # Default allow if no restrictions

    def can_write_path(self, path: str) -> bool:
        """Check if writing to a path is allowed.

        Uses PurePosixPath.match semantics so that `*` does NOT cross `/`
        boundaries. This prevents patterns like `*implementation_plan.md`
        from matching `/etc/shadow/implementation_plan.md`.

        Iter-3 hardening:
          - Reject NUL bytes (TOCTOU between Python and C consumers).
          - Reject leading/trailing whitespace (`"  /etc/..."` would
            otherwise bypass `startswith("/")` system-root check).
          - Reject URL-schemed inputs.
          - Case-fold the system-root check.
          - Workspace allow-list for absolute paths.

        Iter-4 hardening (H1/H2/H3/H8/H12):
          - Relative paths resolve against `os.getcwd()` before workspace check.
          - Workspace allow-list ALWAYS fires, even when `write_paths_allowed`
            contains `"*"` (the `*` is a pattern-match wildcard, not a scope
            escape hatch).
          - URL-encoded path segments are `unquote`d once before the `..`
            segment check, so `%2e%2e` and `%2f` can't slip past.
        """
        if not isinstance(path, str) or not path:
            return False
        if not self.write_paths_allowed:
            return False

        # H9: NUL byte → TOCTOU split between Python (rejects) and C-level
        # consumers (truncate). Reject early.
        if "\x00" in path:
            return False

        # C3: leading/trailing whitespace → bypasses startswith("/") branch
        # while still parsing as a system path after a writer's strip().
        if path != path.strip():
            return False

        # C4: URL-schemed paths → split into parts matching `*/artifacts/*`
        # despite obviously not being a filesystem path.
        if "://" in path:
            return False

        # Iter-4 H8 / Iter-5 CRIT-1: URL-encoded sequences (e.g. `%2e%2e`)
        # create an asymmetry between the validator (decodes) and the writer
        # (does not decode) — fixed filesystems write the literal `%2e%2e`
        # directory, while the validator saw `..`. Reject any `%XX` escape
        # outright rather than decoding-and-reassigning. Legitimate filesystem
        # paths do not contain percent-encoded bytes; callers that need such
        # characters must decode upstream before calling this validator.
        decoded = unquote(path)
        if decoded != path:
            return False

        # Reject path traversal outright; `..` should never be a legitimate
        # write target regardless of pattern. Check BOTH the parts list and
        # any segment that contains `..` as a substring (defensive against
        # weird PurePath splits on exotic inputs).
        try:
            parts = PurePosixPath(path).parts
        except Exception:
            return False
        if ".." in parts:
            return False
        if any(".." in part for part in parts if part not in ("/",)):
            return False

        try:
            p = PurePosixPath(path)
        except Exception:
            return False

        # Iter-4 H1/H3: workspace check for BOTH absolute and relative paths.
        # Resolve relative paths against cwd so the workspace check is
        # meaningful. For absolute paths, keep the original check.
        if path.startswith("/"):
            # C2: case-fold the system-root deny-list.
            top = (parts[1].lower() if len(parts) > 1 else "")
            if top in _SYSTEM_ROOT_DIRS:
                return False
            abs_path = path
        else:
            try:
                abs_path = os.path.abspath(os.path.join(os.getcwd(), path))
            except (OSError, ValueError):
                return False
            # After resolution, re-check system-root for the absolute form
            # (cwd may itself be inside /etc on broken deployments).
            try:
                abs_parts = PurePosixPath(abs_path).parts
            except Exception:
                return False
            abs_top = (abs_parts[1].lower() if len(abs_parts) > 1 else "")
            if abs_top in _SYSTEM_ROOT_DIRS:
                return False

        # Iter-4 H2: workspace allow-list ALWAYS fires, even for `["*"]`
        # policies. BUILDING/TESTING can still write to any file UNDER the
        # workspace, but they cannot escape into /home/<other>/, /opt/, etc.
        if not _is_under_workspace(abs_path):
            return False

        for pattern in self.write_paths_allowed:
            if not isinstance(pattern, str) or not pattern:
                continue
            if pattern == "*":
                # Iter-4 H2: the `*` pattern now means "any path under
                # workspace" (workspace check already passed above).
                return True
            # Direct match against the full path (PurePath.match treats `*` as
            # non-slash-crossing; `**` as recursive).
            try:
                if p.match(pattern):
                    return True
            except Exception:
                pass
            # Backward-compat for `*/`-prefixed patterns meaning "anywhere
            # in the tree". Translate to `**/` form. Patterns NOT starting
            # with `*/` are no longer mangled into a recursive variant —
            # iter-4 H7/H12 requires glob patterns to be explicit, so
            # `implementation_plan.md` means ONLY the cwd-level file, and
            # `**/implementation_plan.md` is the recursive form.
            if pattern.startswith("*/") and len(pattern) > 2:
                recursive = "**/" + pattern[2:].lstrip("/")
                try:
                    if p.match(recursive):
                        return True
                except Exception:
                    pass
        return False


# Iter-4 H7/H12: exact-basename + `**/`-anchored recursive form for every
# report/plan filename. Previous patterns like `*implementation_plan.md`
# relied on PurePath's non-slash-crossing `*` semantics, but `*` still
# matches ANY prefix, so `evil_implementation_plan.md` passed. Replacing
# the leading `*` with either the bare basename (cwd-level match) plus
# `**/<basename>` (recursive-anywhere) anchors the filename end-to-end.
_PLANNING_FILENAMES: Tuple[str, ...] = (
    "implementation_plan.md",
    "**/implementation_plan.md",
)
_ANALYZING_FILENAMES: Tuple[str, ...] = (
    "analysis.md", "**/analysis.md",
    "report.md", "**/report.md",
    "test_results.md", "**/test_results.md",
)
_CYCLE_END_FILENAMES: Tuple[str, ...] = (
    "report.md", "**/report.md",
    "report.json", "**/report.json",
    "cycle_report.md", "**/cycle_report.md",
    "cycle_report.json", "**/cycle_report.json",
    "cycle_report.txt", "**/cycle_report.txt",
    # Allow numbered cycle reports like `cycle_report_2.md` with a literal
    # underscore separator, NOT an arbitrary prefix (blocks `my_cycle_report.md`
    # style matches).
    "cycle_report_*.md", "**/cycle_report_*.md",
    "cycle_report_*.json", "**/cycle_report_*.json",
    "cycle_report_*.txt", "**/cycle_report_*.txt",
)

# Stage-specific tool policies.
# Iter-4 H4: wrapped in MappingProxyType so the dict itself is read-only.
# Combined with the frozen dataclass above, this makes STAGE_POLICIES
# immutable at runtime: `STAGE_POLICIES[...].allowed_tools.add(...)` now
# raises AttributeError, and `STAGE_POLICIES[RDStage.X] = ...` raises
# TypeError.
STAGE_POLICIES: Mapping[RDStage, StageToolPolicy] = types.MappingProxyType({
    RDStage.PLANNING: StageToolPolicy(
        stage=RDStage.PLANNING,
        allowed_tools=frozenset({
            "Read", "read_file",
            "Glob", "list_directory",
            "Grep", "search_file_content",
            "WebFetch",
            "WebSearch",
            "Task",  # For research subagents
            "Write", "write_file",  # Only for artifacts
            "Edit", "replace",   # Only for artifacts
            "Bash", "run_shell_command",  # Read-only bash commands allowed for exploration
        }),
        blocked_tools=frozenset({
            "NotebookEdit",  # No notebook changes
        }),
        write_paths_allowed=(
            "*/artifacts/*",
            "*/research/*",
        ) + _PLANNING_FILENAMES,
    ),

    RDStage.BUILDING: StageToolPolicy(
        stage=RDStage.BUILDING,
        allowed_tools=frozenset(),  # All tools allowed
        blocked_tools=frozenset(),  # None blocked
        write_paths_allowed=("*",),  # Any path UNDER workspace (iter-4 H2)
    ),

    RDStage.TESTING: StageToolPolicy(
        stage=RDStage.TESTING,
        allowed_tools=frozenset(),  # All tools allowed
        blocked_tools=frozenset(),
        write_paths_allowed=("*",),
    ),

    RDStage.ANALYZING: StageToolPolicy(
        stage=RDStage.ANALYZING,
        allowed_tools=frozenset({
            "Read", "read_file",
            "Glob", "list_directory",
            "Grep", "search_file_content",
            "WebFetch",
            "WebSearch",
            "Task",
            "Write", "write_file",  # Only for reports
            "Edit", "replace",   # Only for reports
            # Iter-6 H5: the ANALYZING system prompt invites the agent to use
            # Bash for read-only verification (running tests, inspecting logs).
            # The Gemini alias `run_shell_command` was present but the Claude
            # `Bash`/`BashOutput`/`KillBash` family was missing — so a Claude
            # agent following its own prompt had Bash silently added to
            # --disallowedTools. Keep vendor parity.
            "Bash", "BashOutput", "KillBash",
            "run_shell_command",
        }),
        blocked_tools=frozenset(),
        write_paths_allowed=(
            "*/artifacts/*",
            "*/research/*",
        ) + _ANALYZING_FILENAMES,
    ),

    RDStage.CYCLE_END: StageToolPolicy(
        stage=RDStage.CYCLE_END,
        allowed_tools=frozenset({
            "Read", "read_file",
            "Glob", "list_directory",
            "Grep", "search_file_content",
            "Write", "write_file",  # Only for reports and continuation prompts
            "Edit", "replace",   # Only for reports
            "Task",   # For research subagents if needed
        }),
        blocked_tools=frozenset(),  # Path restrictions handle write blocking
        write_paths_allowed=(
            "*/artifacts/*",
            "*/research/*",
            "*mission_logs/*",
        ) + _CYCLE_END_FILENAMES,
    ),

    RDStage.COMPLETE: StageToolPolicy(
        stage=RDStage.COMPLETE,
        allowed_tools=frozenset({
            "Read", "read_file",
            "Glob", "list_directory",
            "Grep", "search_file_content",
        }),
        blocked_tools=frozenset({
            "Edit", "replace",
            "Write", "write_file",
            "NotebookEdit",
            "Bash", "run_shell_command",
        }),
        write_paths_allowed=(),
    ),
})


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
        except (ValueError, TypeError):
            logger.warning(
                "Unknown stage %r passed to get_blocked_tools", _safe_stage_repr(stage)
            )
        return []

    @staticmethod
    def get_allowed_tools(stage: str = "PLANNING") -> List[str]:
        """Get list of allowed tools for a stage."""
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)
            if policy and policy.allowed_tools:
                return list(policy.allowed_tools)
        except (ValueError, TypeError):
            logger.warning(
                "Unknown stage %r passed to get_allowed_tools", _safe_stage_repr(stage)
            )
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
- Using `NotebookEdit` or Gemini `replace` on code files is FORBIDDEN
- Writing anywhere except artifacts/ or research/ is FORBIDDEN

### ALLOWED ACTIONS:
- Reading any files to understand the codebase (Claude `Read`, Gemini `read_file`)
- Searching and grepping for relevant code (Claude `Glob`/`Grep`, Gemini `list_directory`/`search_file_content`)
- Using `Bash` or Gemini `run_shell_command` for read-only commands (ls, git status, pwd, etc.)
- Writing to `artifacts/implementation_plan.md` (Claude `Write`, Gemini `write_file`)
- Writing to `research/*.md` (Claude `Write`, Gemini `write_file`)
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
- Modifying source code (FORBIDDEN Claude `Edit`, Gemini `replace`)
- Creating new features (FORBIDDEN Claude `Write`, Gemini `write_file`)
- Bug fixes (those go in next BUILDING iteration)

### ALLOWED in ANALYZING:
- Reading any files (Claude `Read`, Gemini `read_file`)
- Running tests (read-only verification via Claude `Bash`, Gemini `run_shell_command`)
- Writing analysis to `research/analysis.md` (Claude `Write`, Gemini `write_file`)
- Writing test results to `artifacts/test_results.md` (Claude `Write`, Gemini `write_file`)

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
- Modifying source code is FORBIDDEN (FORBIDDEN Claude `Edit`, Gemini `replace`)
- All code changes belong in the BUILDING stage of the next cycle

### ALLOWED ACTIONS:
- Reading any files to gather cycle summary information (Claude `Read`, Gemini `read_file`)
- Writing cycle reports to artifacts/ (Claude `Write`, Gemini `write_file`)
- Writing to mission_logs/ for archival (Claude `Write`, Gemini `write_file`)
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
    def get_disallowed_tools_for_cli(stage: str) -> str:
        """Get comma-separated disallowed tools string for Claude CLI --disallowedTools flag.

        This is the PRIMARY enforcement mechanism. The Claude CLI will refuse to invoke
        any tool listed here, preventing the LLM from even attempting blocked operations.

        For stages where Write/Edit are allowed but path-restricted (PLANNING, ANALYZING,
        CYCLE_END), those tools are NOT blocked here - path enforcement is handled by
        the stage_gate_hook.py PreToolUse hook as defense-in-depth.

        Args:
            stage: Current R&D stage name

        Returns:
            Comma-separated string of tool names to block (for --disallowedTools flag)
        """
        # Always block plan mode tools regardless of stage.
        # WebSearch/WebFetch are always blocked too — they route through the
        # AtlasForge web proxy MCP server instead (see af_engine/web_proxy_cli.py).
        # The proxy's MCP tools advertise themselves under the same names, so
        # blocking the built-ins here only silences Anthropic's filtered backend;
        # the MCP tools remain callable and give the subagent unfiltered web.
        base_blocked = frozenset({"EnterPlanMode", "ExitPlanMode", "WebSearch", "WebFetch"})

        # Iter-4 C4: use the module-level ALL_KNOWN_TOOLS universe-set. A tool
        # absent from this set can NEVER end up in the computed disallowed
        # string, so keeping it current is security-critical. See ALL_KNOWN_TOOLS
        # definition above for the full list of side-effecting Claude CLI tools.
        all_known_tools = ALL_KNOWN_TOOLS
        read_only_tools = _READ_ONLY_TOOLS

        def _fail_closed() -> str:
            return ",".join(sorted(base_blocked | (all_known_tools - read_only_tools)))

        stage_repr = _safe_stage_repr(stage)
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)

            if not policy:
                logger.warning(
                    "No policy for stage %r in get_disallowed_tools_for_cli; failing closed",
                    stage_repr,
                )
                return _fail_closed()

            # C1 fix: blocklist and allowlist are NOT mutually exclusive.
            # PLANNING has BOTH blocked={NotebookEdit} AND a constrained
            # allowed set. Previously the `if blocked_tools: return` short-
            # circuit skipped the allowlist branch, so tools NOT in the
            # allowlist (TodoWrite, AskUserQuestion) silently passed.
            # Combine BOTH constraints into a single denied set.
            stage_blocked = set(base_blocked)
            if policy.blocked_tools:
                stage_blocked |= policy.blocked_tools
            if policy.allowed_tools:
                stage_blocked |= (all_known_tools - policy.allowed_tools)
            return ",".join(sorted(stage_blocked))

        except (ValueError, TypeError):
            logger.warning(
                "Unknown stage %r passed to get_disallowed_tools_for_cli; failing closed",
                stage_repr,
            )
            return _fail_closed()

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
        if not isinstance(tool_name, str):
            logger.warning(
                "Non-string tool_name (%s) passed to validate_tool_usage; failing closed",
                type(tool_name).__name__,
            )
            return False, f"tool_name must be str, got {type(tool_name).__name__}"

        stage_repr = _safe_stage_repr(stage)
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)

            if not policy:
                logger.warning(
                    "No policy for stage %r in validate_tool_usage; failing closed",
                    stage_repr,
                )
                return False, f"Unknown stage: {stage_repr}"

            if not policy.is_tool_allowed(tool_name):
                return False, f"Tool '{tool_name}' is blocked in {stage_repr} stage"

            return True, "Allowed"

        except (ValueError, TypeError):
            logger.warning(
                "Unknown stage %r passed to validate_tool_usage; failing closed",
                stage_repr,
            )
            return False, f"Unknown stage: {stage_repr}"

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
        if not isinstance(path, str):
            logger.warning(
                "Non-string path (%s) passed to validate_write_path; failing closed",
                type(path).__name__,
            )
            return False, f"path must be str, got {type(path).__name__}"

        stage_repr = _safe_stage_repr(stage)
        try:
            rd_stage = RDStage(stage)
            policy = STAGE_POLICIES.get(rd_stage)

            if not policy:
                logger.warning(
                    "No policy for stage %r in validate_write_path; failing closed",
                    stage_repr,
                )
                return False, f"Unknown stage: {stage_repr}"

            if not policy.can_write_path(path):
                return False, f"Writing to '{path}' not allowed in {stage_repr} stage"

            return True, "Allowed"

        except (ValueError, TypeError):
            logger.warning(
                "Unknown stage %r passed to validate_write_path; failing closed",
                stage_repr,
            )
            return False, f"Unknown stage: {stage_repr}"


# Convenience function for quick checks
def is_write_allowed(stage: str) -> bool:
    """Quick check if any writing is allowed in stage."""
    stage_repr = _safe_stage_repr(stage)
    try:
        rd_stage = RDStage(stage)
        policy = STAGE_POLICIES.get(rd_stage)
        if policy:
            return bool(policy.write_paths_allowed)
    except (ValueError, TypeError):
        logger.warning(
            "Unknown stage %r passed to is_write_allowed; failing closed", stage_repr
        )
    return False


def get_stage_restrictions(stage: str) -> dict:
    """Get a dict describing restrictions for a stage."""
    stage_repr = _safe_stage_repr(stage)
    try:
        rd_stage = RDStage(stage)
        policy = STAGE_POLICIES.get(rd_stage)
        if policy:
            return {
                "stage": stage_repr,
                "allowed_tools": list(policy.allowed_tools) if policy.allowed_tools else "all",
                "blocked_tools": list(policy.blocked_tools) if policy.blocked_tools else "none",
                "write_paths": policy.write_paths_allowed if policy.write_paths_allowed else "none",
            }
    except (ValueError, TypeError):
        logger.warning("Unknown stage %r passed to get_stage_restrictions", stage_repr)
    return {"stage": stage_repr, "error": "unknown stage"}


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
        ("UNKNOWN_STAGE", "Edit", False),  # Unknown stage now fails closed
        ("PLANING", "Write", False),       # Typo of PLANNING fails closed
    ]

    print("\nTool validation tests:")
    for stage, tool, expected in tests:
        allowed, reason = guard.validate_tool_usage(stage, tool)
        status = "PASS" if allowed == expected else "FAIL"
        print(f"  {status}: {stage}/{tool} -> {allowed} ({reason})")

    # Test path validation. Absolute paths must resolve under a configured
    # workspace root (iter-3); we use the AtlasForge root for self-test.
    af_root = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    path_tests = [
        ("PLANNING", f"{af_root}/workspace/proj/artifacts/plan.md", True),
        ("PLANNING", f"{af_root}/workspace/proj/src/code.py", False),
        ("BUILDING", f"{af_root}/workspace/proj/src/code.py", True),
        ("ANALYZING", f"{af_root}/workspace/proj/research/analysis.md", True),
        # Iter-3 hardening: out-of-workspace path rejected even when filename matches
        ("PLANNING", "/opt/evil/artifacts/plan.md", False),
        # Iter-3 hardening: case-folded system root rejection
        ("PLANNING", "/ETC/artifacts/plan.md", False),
        # Iter-3 hardening: URL-schemed path rejected
        ("PLANNING", "http://evil.com/artifacts/plan.md", False),
        # Iter-3 hardening: whitespace-prefixed path rejected
        ("PLANNING", "  /etc/artifacts/plan.md", False),
        # Iter-3 hardening: NUL byte rejected
        ("PLANNING", "/home/x\x00/artifacts/plan.md", False),
        # Iter-4 H2: `["*"]` allow-list still rejects out-of-workspace paths
        ("BUILDING", "/home/otheruser/id_rsa", False),
        ("BUILDING", "/opt/secrets/token", False),
        # Iter-4 H7/H12: glob prefix-match no longer accepts evil-prefixed names
        ("PLANNING", f"{af_root}/workspace/proj/evil_implementation_plan.md", False),
        ("ANALYZING", f"{af_root}/workspace/proj/evil_analysis.md", False),
        # Iter-4 H8: URL-encoded `..` rejected
        ("PLANNING", f"{af_root}/workspace/proj/artifacts/%2e%2e/etc/plan.md", False),
    ]
    print("\nPath validation tests:")

    for stage, path, expected in path_tests:
        allowed, reason = guard.validate_write_path(stage, path)
        status = "PASS" if allowed == expected else "FAIL"
        print(f"  {status}: {stage}/{path} -> {allowed}")

    # Show system prompts
    print("\nSystem prompt for PLANNING (first 200 chars):")
    print(guard.get_planning_system_prompt()[:200] + "...")

    print("\nStage Guard self-test complete!")
