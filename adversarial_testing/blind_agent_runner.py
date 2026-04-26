"""
blind_agent_runner.py — Parallel Blind Agent Red Team for AtlasForge

Spawns real Claude CLI subprocesses as adversarial agents. Each agent:
- Receives the workspace path (not raw code) so it can explore the codebase itself
- Has full tool access (Read, Grep, Bash, Write) to run tests and exercise the system
- Is registered in the Mission Activity dashboard via agent_stream_manager
- Runs in parallel alongside the other red team agents
- Writes JSON findings to a results file that the caller aggregates

This is the correct implementation of the user's original vision:
  "The Red Team adversarial agent is a launched Agent with its own workflow
   working in parallel."

Usage:
    from adversarial_testing.blind_agent_runner import BlindAgentRedTeam

    team = BlindAgentRedTeam()
    result = team.launch_parallel_team(
        workspace_dir=Path("/path/to/workspace"),
        mission_desc="AtlasLab scientific hypothesis engine",
        n_agents=3,
        timeout=300
    )
    print(f"Findings: {result.total_issues}")
"""

import json
import logging
import os
import re
import shlex
import signal
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subprocess environment allowlist — explicit safe variables only
# ---------------------------------------------------------------------------
_SAFE_ENV_EXACT = frozenset({
    'PATH', 'HOME', 'USER', 'LANG', 'TERM', 'SHELL', 'DISPLAY',
    'TMPDIR', 'TEMP', 'TMP',
    'ANTHROPIC_API_KEY',
    'OPENAI_API_KEY',
    'GEMINI_API_KEY', 'GOOGLE_API_KEY',
    'CLAUDE_MODEL', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    'ATLASFORGE_CODEX_MODEL', 'CODEX_MODEL',
    'ATLASFORGE_GEMINI_MODEL', 'ATLASFORGE_GEMINI_MODEL_BALANCED',
    'OLLAMA_URL', 'OLLAMA_MODEL',
    # ATLASFORGE_* vars needed by subprocess — explicit allowlist, NOT prefix
    'ATLASFORGE_PORT', 'ATLASFORGE_ROOT', 'ATLASFORGE_DATA_DIR',
    'ATLASFORGE_LLM_PROVIDER',
})
_SAFE_ENV_PREFIXES = ('LC_', 'XDG_')

_SUPPORTED_LLM_PROVIDERS = {"claude", "codex", "gemini"}
_DEFAULT_LLM_PROVIDER = "claude"

# ---------------------------------------------------------------------------
# Allowed workspace roots — path traversal guard
# ---------------------------------------------------------------------------
_ATLASFORGE_ROOT = Path(os.environ.get('ATLASFORGE_ROOT', '/home/vader/AI-AtlasForge')).resolve()
_ALLOWED_WORKSPACE_ROOTS = [
    (_ATLASFORGE_ROOT / 'workspace').resolve(),
]
# Sanity check: ensure allowed roots are strictly *under* the repo root,
# not equal to it (which would grant access to the entire repo).
for _root in _ALLOWED_WORKSPACE_ROOTS:
    if _root == _ATLASFORGE_ROOT:
        raise RuntimeError(
            f"SECURITY: allowed workspace root {_root} must be a subdirectory "
            f"of {_ATLASFORGE_ROOT}, not the repo root itself"
        )

# ---------------------------------------------------------------------------
# Adaptive timeout tiers based on .py file count in workspace
# ---------------------------------------------------------------------------
_TIMEOUT_TIERS = [
    (50,    300),    # < 50 .py files  → 300 s
    (200,   600),    # 50–200 .py files → 600 s
    (999999, 900),   # > 200 .py files  → 900 s
]

# ---------------------------------------------------------------------------
# Model-aware work budget defaults (output tokens per agent)
# ---------------------------------------------------------------------------
_MODEL_WORK_BUDGETS: Dict[str, int] = {
    "claude-opus-4-6":   20_000,
    "claude-opus-4":     18_000,
    "claude-sonnet-4-6": 10_000,
    "claude-sonnet-4-5": 10_000,
    "claude-haiku-4-5":   5_000,
}
_MODEL_SAFETY_TIMEOUTS: Dict[str, float] = {
    "claude-opus-4-6":   3600.0,
    "claude-opus-4":     3600.0,
    "claude-sonnet-4-6": 1800.0,
    "claude-sonnet-4-5": 1800.0,
    "claude-haiku-4-5":   900.0,
}
_DEFAULT_WORK_BUDGET = 10_000          # Sonnet-like fallback
_DEFAULT_SAFETY_TIMEOUT = 1800.0       # Sonnet-like fallback

# Diminishing returns config
# BUG-H1/M1: wrap int() in try/except; clamp to minimum 100 so zero/negative cannot silently
# disable diminishing-returns detection.
try:
    _RED_TEAM_LOW_DELTA_THRESHOLD = max(100, int(os.environ.get("RED_TEAM_LOW_DELTA_THRESHOLD", "1000")))
except (ValueError, TypeError):
    _RED_TEAM_LOW_DELTA_THRESHOLD = 1000
    logger.warning(
        "RED_TEAM_LOW_DELTA_THRESHOLD env var is not a valid integer; defaulting to %d",
        _RED_TEAM_LOW_DELTA_THRESHOLD,
    )
_RED_TEAM_MIN_CONTINUATIONS_BEFORE_DR = 1   # BUG-H4: lowered from 3; single-shot agents don't iterate.
# NOTE: Single-pass agents (continuation_count == 0 after first run) will never trigger the
# diminishing-returns gate because check_diminishing_returns() requires continuation_count >= 1.
# This is intentional: a single completion carries no trend data, so DR cannot be evaluated.
# The regression test test_dr_gate_single_pass() verifies this behavior.
_RED_TEAM_CONSECUTIVE_LOW_DELTA_REQUIRED = 2


def _detect_model() -> str:
    """Return the model string from env, lower-cased, or empty string."""
    provider = _resolve_red_team_llm_provider()
    return (_resolve_red_team_llm_model(provider) or "").strip().lower()


def _normalize_llm_provider(value: Any) -> Optional[str]:
    provider = str(value or "").strip().lower()
    if provider in _SUPPORTED_LLM_PROVIDERS:
        return provider
    return None


def _read_provider_from_json(path: Path) -> Optional[str]:
    try:
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_llm_provider(data.get("llm_provider") or data.get("provider"))


def _resolve_red_team_llm_provider() -> str:
    mission_provider = _read_provider_from_json(_ATLASFORGE_ROOT / "state" / "mission.json")
    if mission_provider:
        return mission_provider
    env_provider = _normalize_llm_provider(os.environ.get("ATLASFORGE_LLM_PROVIDER"))
    if env_provider:
        return env_provider
    state_provider = _read_provider_from_json(_ATLASFORGE_ROOT / "state" / "llm_provider.json")
    if state_provider:
        return state_provider
    return _DEFAULT_LLM_PROVIDER


def _resolve_red_team_llm_model(provider: str) -> Optional[str]:
    if provider == "codex":
        model = os.environ.get("ATLASFORGE_CODEX_MODEL") or os.environ.get("CODEX_MODEL")
    elif provider == "gemini":
        model = os.environ.get("ATLASFORGE_GEMINI_MODEL") or os.environ.get("ATLASFORGE_GEMINI_MODEL_BALANCED")
    else:
        model = os.environ.get("CLAUDE_MODEL")
    return model.strip() if model and model.strip() else None


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_red_team_llm_command(provider: str, stage: str = "TESTING") -> List[str]:
    """Build the subprocess command for blind red-team agents."""
    provider = _normalize_llm_provider(provider) or _DEFAULT_LLM_PROVIDER
    model = _resolve_red_team_llm_model(provider)

    if provider == "codex":
        from WebProxy import codex_proxy_cli_args
        import shutil as _shutil
        codex_bin = _shutil.which("codex")
        if codex_bin is None:
            raise FileNotFoundError("'codex' binary not found in PATH")
        cmd = [codex_bin]
        cmd.extend(codex_proxy_cli_args())
        if _env_flag_enabled("ATLASFORGE_CODEX_WEB_SEARCH", default=False):
            cmd.append("--search")
        cmd.extend(["exec", "--color", "never"])
        if _env_flag_enabled("ATLASFORGE_CODEX_AUTONOMOUS", default=True):
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")
        return cmd

    if provider == "gemini":
        import shutil as _shutil
        gemini_bin = _shutil.which("gemini")
        if gemini_bin is None:
            raise FileNotFoundError("'gemini' binary not found in PATH")
        cmd = [gemini_bin]
        if _env_flag_enabled("ATLASFORGE_GEMINI_AUTONOMOUS", default=True):
            cmd.append("--yolo")
        cmd.extend(["--output-format", "json"])
        if model:
            cmd.extend(["-m", model])
        return cmd

    import shutil as _shutil
    claude_bin = _shutil.which("claude")
    if claude_bin is None:
        raise FileNotFoundError("'claude' binary not found in PATH")
    command = [
        claude_bin, "-p",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
    ]
    if model:
        command[2:2] = ["--model", model]

    disallowed = ""
    try:
        from init_guard import InitGuard
        disallowed = InitGuard.get_disallowed_tools_for_cli(stage)
        disallowed = re.sub(r'[^a-zA-Z0-9_,\s]', '', disallowed)
    except Exception:
        pass

    from WebProxy import proxy_cli_args
    command.extend(proxy_cli_args(disallowed))
    return command


def _model_work_budget(model: str) -> int:
    """Return the per-agent work budget (output tokens) for a given model.

    BUG-M5: Sort keys longest-first so more specific keys (e.g. claude-opus-4-6)
    always win over shorter overlapping keys (e.g. claude-opus-4).
    """
    if model is None:
        return _DEFAULT_WORK_BUDGET
    for key in sorted(_MODEL_WORK_BUDGETS, key=len, reverse=True):
        if key in model:
            return _MODEL_WORK_BUDGETS[key]
    return _DEFAULT_WORK_BUDGET


def _model_safety_timeout(model: str) -> float:
    """Return the per-agent safety timeout (seconds) for a given model.

    BUG-M5: Sort keys longest-first so more specific keys always win.
    """
    if model is None:
        return _DEFAULT_SAFETY_TIMEOUT
    for key in sorted(_MODEL_SAFETY_TIMEOUTS, key=len, reverse=True):
        if key in model:
            return _MODEL_SAFETY_TIMEOUTS[key]
    return _DEFAULT_SAFETY_TIMEOUT


def _parse_output_tokens_from_jsonl(text: str) -> tuple[int, str]:
    """
    Parse cumulative output_tokens from a claude CLI stream-json stdout blob.

    Each line is a JSONL object. When type == "result", the "usage" field
    contains output_tokens. Accumulates across all result events found.
    Falls back to a character-count estimate (len // 4) when nothing parses.

    Returns:
        (token_count, source) where source is 'jsonl' or 'heuristic'.
    """
    if not isinstance(text, str):
        return 0, "heuristic"
    if not text:
        return 0, "heuristic"
    total = 0
    found_any = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result" and "usage" in obj:
                usage = obj["usage"]
                if isinstance(usage, dict) and "output_tokens" in usage:
                    try:
                        total += max(0, int(usage["output_tokens"]))
                        found_any = True
                    except (ValueError, TypeError):
                        pass
        except (json.JSONDecodeError, ValueError):
            continue
    if not found_any and text:
        # BUG-M4: escalate to WARNING so callers can detect the fallback in logs.
        # Fallback: rough estimate of output tokens from character count (~4 chars/token).
        # Systematic undercount (~25%) is an acknowledged limitation documented here.
        estimate = max(0, len(text) // 4)
        logger.warning(
            "output_tokens not found in JSONL stream (no 'result' event with usage field); "
            "falling back to char-count estimate: ~%d tokens (len=%d // 4). "
            "This may undercount by ~25%%.",
            estimate, len(text),
        )
        return estimate, "heuristic"
    return total, "jsonl"


@dataclass
class _AgentBudgetState:
    """Per-agent work budget and diminishing-returns tracking state."""
    work_budget: int           # target output tokens
    safety_timeout: float      # fallback kill timeout (seconds)
    output_tokens: int = 0     # tokens produced so far
    finding_count: int = 0     # findings produced
    continuation_count: int = 0
    consecutive_low_delta: int = 0
    last_token_checkpoint: int = 0   # tokens at last delta check
    stop_reason: str = "pending"     # pending|work_budget_complete|timeout|diminishing_returns|error
    token_source: str = "heuristic"  # 'jsonl' | 'heuristic'

    def __post_init__(self) -> None:
        import math as _math
        # NaN/inf comparisons with <= always return False, so check finiteness first.
        if isinstance(self.work_budget, bool) or not isinstance(self.work_budget, int) or self.work_budget <= 0:
            raise ValueError(
                f"work_budget must be a positive integer, got {self.work_budget!r}. "
                "Use a positive int value or None to use the model-aware default."
            )
        if isinstance(self.safety_timeout, bool):
            raise ValueError(
                f"safety_timeout must be a numeric value, got bool {self.safety_timeout!r}. "
                "Use a positive float/int value."
            )
        if self.safety_timeout is None or not isinstance(self.safety_timeout, (int, float)) or not _math.isfinite(self.safety_timeout) or self.safety_timeout <= 0:
            raise ValueError(
                f"safety_timeout must be a positive finite number, got {self.safety_timeout!r}. "
                "Use a positive value — 0, None, NaN, and inf are not valid."
            )

    def record_output(self, tokens: int) -> None:
        if tokens < 0:
            logger.warning("record_output: negative token count %d clamped to 0", tokens)
            tokens = 0
        self.output_tokens += tokens

    def check_diminishing_returns(self) -> bool:
        """
        Return True if diminishing returns are detected.

        Requires:
          - At least _RED_TEAM_MIN_CONTINUATIONS_BEFORE_DR continuations
          - Delta below threshold for _RED_TEAM_CONSECUTIVE_LOW_DELTA_REQUIRED checks
        """
        if self.continuation_count < _RED_TEAM_MIN_CONTINUATIONS_BEFORE_DR:
            self.last_token_checkpoint = self.output_tokens  # keep checkpoint current on early return
            return False
        delta = self.output_tokens - self.last_token_checkpoint
        if delta < _RED_TEAM_LOW_DELTA_THRESHOLD:
            self.consecutive_low_delta += 1
        else:
            self.consecutive_low_delta = 0
        self.last_token_checkpoint = self.output_tokens
        return self.consecutive_low_delta >= _RED_TEAM_CONSECUTIVE_LOW_DELTA_REQUIRED

# ---------------------------------------------------------------------------
# Parent directory on path so we can import AtlasForge top-level modules
# ---------------------------------------------------------------------------
_PACKAGE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PACKAGE_ROOT))

# ---------------------------------------------------------------------------
# Optional dashboard integration (agent_stream_manager)
# ---------------------------------------------------------------------------
try:
    from agent_stream_manager import (
        register_agent as _asm_register,
        update_agent_pid as _asm_update_pid,
        complete_agent as _asm_complete,
        stream_stdout_to_file as _asm_stream,
        stream_codex_session_to_file as _asm_stream_codex,
        reconstruct_text_from_stream_file as _asm_reconstruct,
    )
    HAS_ASM = True
except ImportError:
    HAS_ASM = False
    logger.warning("agent_stream_manager not available — red team agents won't appear in dashboard")

# Re-use data classes from red_team_agent so callers see the same types
from .red_team_agent import AttackCategory, RedTeamFinding, RedTeamResult


# ---------------------------------------------------------------------------
# Attack profiles — one per blind agent
# ---------------------------------------------------------------------------

@dataclass
class AttackProfile:
    """Defines what a single blind agent focuses on."""
    index: int
    categories: List[AttackCategory]
    label: str
    focus_description: str


_ATTACK_PROFILES = [
    AttackProfile(
        index=0,
        categories=[AttackCategory.INJECTION],
        label="Red Team - Injection",
        focus_description=(
            "INJECTION VULNERABILITIES.\n"
            "Look for command injection (os.system, subprocess with user input), "
            "path traversal, SQL injection, format string injection.\n"
            "Trace every user-controlled string that reaches a shell, filesystem path, "
            "database query, template, config loader, or dynamic import/exec boundary."
        ),
    ),
    AttackProfile(
        index=1,
        categories=[AttackCategory.BOUNDARY_TESTING, AttackCategory.TYPE_CONFUSION],
        label="Red Team - Boundary",
        focus_description=(
            "BOUNDARY CONDITIONS and TYPE CONFUSION.\n"
            "Try empty inputs, None, 0, -1, very large numbers, wrong types.\n"
            "Look for missing guard clauses, implicit type conversions, "
            "index-out-of-range, numeric overflow/underflow."
        ),
    ),
    AttackProfile(
        index=2,
        categories=[AttackCategory.LOGIC_FLAW, AttackCategory.STATE_CORRUPTION],
        label="Red Team - Logic",
        focus_description=(
            "LOGIC FLAWS and STATE CORRUPTION.\n"
            "Look for off-by-one errors, wrong comparison operators, "
            "incorrect loop bounds, missed edge cases in conditionals.\n"
            "Also look for shared mutable state, missing locks, "
            "state machine transitions that can be bypassed, "
            "invariants that can be violated by unusual call sequences."
        ),
    ),
    AttackProfile(
        index=3,
        categories=[AttackCategory.CONCURRENCY, AttackCategory.RESOURCE_EXHAUSTION],
        label="Red Team - Concurrency",
        focus_description=(
            "CONCURRENCY and RESOURCE EXHAUSTION.\n"
            "Look for race conditions, missing thread locks, "
            "file handles that are never closed, memory that is never freed.\n"
            "Try to find code paths that can loop infinitely or allocate "
            "unbounded memory/disk. Look for missing timeouts on I/O calls."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New Red Team Hunt Prompt — writes findings incrementally as discovered
# ---------------------------------------------------------------------------

_RED_TEAM_HUNT_PROMPT = """\
You are a Red Team bug hunter. Your ONLY job is to find bugs in the code at the
workspace path below. You do NOT fix bugs. You do NOT need to find solutions.
You just find everything that is wrong and write it down immediately.

## Workspace
${workspace_dir}

## What this code is supposed to do
${mission_desc}

## Your hunt focus for this session
${focus_description}

## FINDINGS FILE — write every finding here immediately when found
${findings_file}

## Instructions

1. **Explore first** — use Read, Glob, Grep to understand the code structure.
   Start with entry points, key modules, and existing tests.

2. **Run existing tests** to see what already fails:
   ```
   cd ${shell_workspace_dir} && python3 -m pytest tests/ -v 2>&1 | head -100
   ```
   Or the appropriate test command for the language/framework.

3. **Hunt for bugs** based on your focus area. Cover:
   - Logic errors and incorrect conditional logic
   - Edge cases: empty inputs, None, 0, -1, very large values, wrong types
   - Missing validation at function entry points
   - Race conditions, missing thread locks, shared mutable state
   - Off-by-one errors, wrong loop bounds
   - Unchecked return values and silently-ignored errors
   - Exception handling gaps: bare except, swallowed errors, no propagation
   - Missing null checks, undefined variable access paths
   - Incorrect assumptions about data shapes or types
   - Security issues: injection, path traversal, unchecked user input

4. **WRITE EACH FINDING IMMEDIATELY** when you find it — before moving to the
   next file or the next issue. Do NOT accumulate findings in memory.

   **For a confirmed bug, write to ${findings_file} using EXACTLY this format:**

   If the file is empty or does not exist yet, use the Write tool to create it:

   ```
   # Red Team Findings — ${agent_id}

   ---BUG---
   File: path/to/file.py
   Line: 123
   Severity: CRITICAL
   Type: logic_error
   Description: One clear sentence describing what is wrong.
   Reproduction: Steps or input that triggers this bug.
   ---END BUG---
   ```

   For subsequent findings, use the Edit tool to append after the last
   ---END BUG--- or ---END SUSPECTED--- block.

   **Severity levels:**
   - CRITICAL: data loss, crash, security breach, silent corruption
   - HIGH: wrong output, broken feature, unhandled exception path
   - MODERATE: incorrect behavior in edge cases, missing validation
   - LIGHT: poor error handling, misleading messages, minor logic gap
   - NONBLOCKING: cosmetic, style, nice-to-have

   **Type categories:**
   logic_error | missing_validation | race_condition | memory_leak |
   security | crash | data_loss | incorrect_behavior | missing_test |
   exception_handling | null_check | type_confusion | off_by_one

   **For a suspected (unconfirmed) issue:**

   ```
   ---SUSPECTED---
   File: path/to/file.py
   Line: 45-67
   Description: What looks suspicious and why you cannot confirm it.
   ---END SUSPECTED---
   ```

## Rules — READ THESE CAREFULLY

- **WRITE EACH FINDING IMMEDIATELY when found.** Do not accumulate.
- **Do NOT get stuck.** If you cannot confirm a bug within ~2 minutes of
  investigation, write a SUSPECTED entry and MOVE ON to the next file.
- **Do NOT propose fixes** — your job is finding, not fixing.
- **Do NOT write a summary at the end** — just findings and suspected entries.
- **Do NOT skip the findings file write** — if you find it and don't write it,
  it is lost forever when this session ends.
- Cover as many files as possible. Breadth over depth.

Also write a legacy JSON results file for backward compatibility:
${results_file}

Use this EXACT JSON structure:
```json
{
    "findings": [
        {
            "category": "boundary",
            "severity": "high",
            "title": "Short descriptive title",
            "description": "Detailed description of the issue",
            "reproduction_steps": ["step 1", "step 2"],
            "affected_code": "filename.py:linenum or function_name",
            "suggested_fix": "What should be done to fix this",
            "confidence": 0.85
        }
    ],
    "attack_vectors_tried": [
        "empty input to function X",
        "None passed to Y",
        "..."
    ]
}
```

Begin your hunt now. Find bugs. Write them down immediately. Move on.
"""


# ---------------------------------------------------------------------------
# BlindAgentRedTeam — main class
# ---------------------------------------------------------------------------

class BlindAgentRedTeam:
    """
    Launches parallel blind Claude agents to adversarially test a codebase.

    Each agent is a full Claude CLI subprocess:
    - Has its own tool loop (Read, Grep, Bash, Write)
    - Can read files, run tests, execute code
    - Is registered in the Mission Activity dashboard
    - Runs in parallel with other agents

    Results are written by each agent to JSON files and then aggregated.
    """

    def __init__(
        self,
        timeout: int = 900,
        n_agents: int = 3,
        work_budget: Optional[int] = None,
        safety_timeout: Optional[float] = None,
    ):
        """
        Args:
            timeout: Kept for backward compatibility. Semantically demoted to
                     fallback hint; actual per-agent kill threshold is safety_timeout.
            n_agents: Number of parallel agents to launch (1-4)
            work_budget: Target output tokens per agent before stopping.
                         None → auto-detect from CLAUDE_MODEL env var.
            safety_timeout: Seconds before forcibly killing a hung/stalled agent.
                            None → auto-detect from CLAUDE_MODEL env var.
                            Defaults are much longer than the old `timeout` (1800-3600s).
        """
        # Iter 3 Fix M9: validate timeout parameter
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        # Iter 4 Fix C1: reject inf/nan timeout
        import math as _math
        if not _math.isfinite(timeout):
            raise ValueError(f"timeout must be finite, got {timeout!r}")
        self.timeout = timeout  # kept for backward compat

        # Iter 4 Fix B4: validate n_agents parameter
        if isinstance(n_agents, bool) or not isinstance(n_agents, int):
            raise TypeError(f"n_agents must be int, got {type(n_agents).__name__}: {n_agents!r}")
        if n_agents <= 0:
            raise ValueError(f"n_agents must be positive, got {n_agents}")
        _max_profiles = len(_ATTACK_PROFILES)
        if n_agents > _max_profiles:
            logger.warning(
                "n_agents=%d exceeds maximum profile count %d; capping to %d",
                n_agents, _max_profiles, _max_profiles,
            )
        self.n_agents = min(max(1, n_agents), _max_profiles)

        # Work budget and safety timeout — model-aware defaults
        # BUG-M2: explicit safety_timeout=0.0 (or negative) is invalid and rejected.
        # NaN/inf comparisons with <= always return False, so check finiteness first.
        import math as _math
        if safety_timeout is not None:
            if isinstance(safety_timeout, bool):
                raise TypeError(f"safety_timeout must be numeric, got bool {safety_timeout!r}")
            if not isinstance(safety_timeout, (int, float)) or not _math.isfinite(safety_timeout) or safety_timeout <= 0:
                raise ValueError(f"safety_timeout must be a positive finite number, got {safety_timeout!r}")
        if work_budget is not None:
            if isinstance(work_budget, bool):
                raise TypeError(
                    f"work_budget must be an int, got bool: {work_budget!r}. "
                    "Pass None to use the model-aware default."
                )
            if not isinstance(work_budget, int):
                raise TypeError(
                    f"work_budget must be an int, got {type(work_budget).__name__}: {work_budget!r}. "
                    "Pass None to use the model-aware default."
                )
            if work_budget <= 0:
                raise ValueError(
                    f"work_budget must be positive, got {work_budget!r}. "
                    "Pass None to use the model-aware default."
                )
        _model = _detect_model()
        _wb = work_budget if work_budget is not None else _model_work_budget(_model)
        self.work_budget: int = _wb if _wb > 0 else _DEFAULT_WORK_BUDGET
        _st = safety_timeout if safety_timeout is not None else _model_safety_timeout(_model)
        self.safety_timeout: float = _st if _st > 0 else _DEFAULT_SAFETY_TIMEOUT
        logger.info(
            "BlindAgentRedTeam init: work_budget=%d tokens, safety_timeout=%.0fs, model=%s",
            self.work_budget, self.safety_timeout, _model or "(unset)",
        )

    # ------------------------------------------------------------------
    # Fix 3 — Path Traversal Guard
    # ------------------------------------------------------------------
    def _validate_workspace_dir(self, path: Path) -> None:
        """Raise ValueError if path is not within any allowed workspace root.

        Uses Path.resolve() to canonicalize symlinks and '..' components,
        then Path.relative_to() for a safe containment check (no string
        prefix matching).
        """
        resolved = path.resolve()
        for allowed_root in _ALLOWED_WORKSPACE_ROOTS:
            try:
                resolved.relative_to(allowed_root)
                return  # within an allowed root — OK
            except ValueError:
                continue
        raise ValueError(
            f"workspace_dir {resolved!r} is not within any allowed root: "
            f"{[str(r) for r in _ALLOWED_WORKSPACE_ROOTS]}"
        )

    # ------------------------------------------------------------------
    # Fix 7 — Adaptive Timeout
    # ------------------------------------------------------------------
    def _estimate_timeout(self, workspace_dir: Path) -> int:
        """Scale timeout based on .py file count in the workspace."""
        try:
            file_count = sum(1 for _ in workspace_dir.rglob("*.py"))
            logger.info(f"Adaptive timeout: workspace has ~{file_count} .py files")
            for threshold, t in _TIMEOUT_TIERS:
                if file_count < threshold:
                    return t
            return 900
        except Exception as e:
            fallback = self.timeout if isinstance(self.timeout, int) and self.timeout > 0 else 300
            logger.warning("_estimate_timeout failed (%s: %s); using configured default %ds",
                           type(e).__name__, e, fallback)
            return fallback

    def launch_parallel_team(
        self,
        workspace_dir: Path,
        mission_desc: str = "Unknown — explore and determine from code",
        n_agents: Optional[int] = None,
        timeout: Optional[int] = None,
        work_budget: Optional[int] = None,
        safety_timeout: Optional[float] = None,
    ) -> RedTeamResult:
        """
        Launch N parallel blind agents against the workspace.

        Args:
            workspace_dir: Path to the codebase to attack
            mission_desc: Brief description of what the codebase does
            n_agents: Override number of agents (uses self.n_agents if None)
            timeout: Backward-compat alias for safety_timeout (kept for callers).
            work_budget: Total output tokens across all agents.
                         Divided evenly per agent. None → use self.work_budget.
            safety_timeout: Per-agent kill timeout for hung processes.
                            None → use self.safety_timeout.

        Returns:
            Aggregated RedTeamResult with all findings from all agents
        """
        workspace_dir = Path(workspace_dir).resolve()
        # Fix 3 — path traversal guard
        self._validate_workspace_dir(workspace_dir)

        # Cycle 3 Fix P3a: validate override params (matching __init__ checks)
        if work_budget is not None:
            if isinstance(work_budget, bool) or not isinstance(work_budget, int):
                raise TypeError(
                    f"work_budget must be an int, got {type(work_budget).__name__}: {work_budget!r}. "
                    "Pass None to use the model-aware default."
                )
            if work_budget <= 0:
                raise ValueError(
                    f"work_budget must be positive, got {work_budget!r}. "
                    "Pass None to use the model-aware default."
                )
        if n_agents is not None:
            if isinstance(n_agents, bool) or not isinstance(n_agents, int):
                raise TypeError(f"n_agents must be int, got {type(n_agents).__name__}: {n_agents!r}")
            if n_agents <= 0:
                raise ValueError(f"n_agents must be positive, got {n_agents}")
        if timeout is not None:
            import math as _math
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
            if timeout <= 0:
                raise ValueError(f"timeout must be positive, got {timeout}")
            if not _math.isfinite(timeout):
                raise ValueError(f"timeout must be finite, got {timeout!r}")
        n = n_agents if n_agents is not None else self.n_agents
        # BAR-5: cap without re-emitting warning (warning already emitted in __init__)
        _max_profiles = len(_ATTACK_PROFILES)
        n = min(max(1, n), _max_profiles)

        # Resolve safety timeout: explicit arg > backward-compat `timeout` arg > self.safety_timeout
        if safety_timeout is not None:
            _safety_t = float(safety_timeout)
        elif timeout is not None:
            _safety_t = float(timeout)
        else:
            _safety_t = self.safety_timeout
        # Fix 7 — adaptive timeout (kept for legacy log, but actual kill uses _safety_t)
        _adaptive_t = self._estimate_timeout(workspace_dir)
        logger.info(
            f"launch_parallel_team: safety_timeout={_safety_t:.0f}s "
            f"(adaptive_hint={_adaptive_t}s, n_agents={n})"
        )
        t = _adaptive_t  # kept for backward-compat use in as_completed outer timeout

        # Per-agent work budget: distribute total budget evenly.
        # BUG-M3: distribute the remainder to the first agent so total budget
        # is always fully allocated (floor division loses remainder tokens).
        # BUG-A0: max(1000, ...) floor clamp can produce negative remainder when
        # total_budget < 1000 * n_agents; use plain floor division instead and
        # clamp the final per-agent budget to 1 (not 1000) to avoid under-allocation.
        _total_budget = work_budget if work_budget is not None else self.work_budget
        n = min(n, _total_budget)  # cap agents so each gets >= 1 token budget
        if n <= 0:
            n = 1  # guard against ZeroDivisionError when _total_budget rounds to 0
        _base_per_agent = max(1, _total_budget // n)
        # Clamp remainder to 0 — when total_budget < n, floor division gives 0 so
        # _base_per_agent becomes 1 via max(1, ...) but _total_budget - (1 * n) < 0.
        # A negative remainder gives agent[0] a budget of 0, failing _AgentBudgetState.
        _remainder = max(0, _total_budget - (_base_per_agent * n))
        # Per-profile list: index 0 gets the remainder, rest get base amount
        _per_agent_budgets = [_base_per_agent + _remainder] + [_base_per_agent] * (n - 1)
        logger.info(
            "Work budget: total=%d tokens, per_agent_base=%d (+%d remainder to agent[0]) across %d agents",
            _total_budget, _base_per_agent, _remainder, n,
        )

        profiles = _ATTACK_PROFILES[:n]
        session_id = f"brt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # Cycle 3 Fix (#13): resolve symlinks and verify paths stay within workspace_dir
        # to prevent symlink escape attacks (e.g., workspace_dir/tests -> /etc).
        # RT-02/RT-03 Fix: append os.sep so "/tmp/workspace" doesn't falsely match "/tmp/workspace2/evil".
        # BUG-M6: remove dead equality arm (results_dir is a subdirectory of workspace_dir,
        # never equal to it), keeping only the startswith() containment check.
        _workspace_real = str(workspace_dir.resolve())
        _workspace_real_prefix = _workspace_real + os.sep
        results_dir = (workspace_dir / "tests").resolve()
        if not str(results_dir).startswith(_workspace_real_prefix):
            raise ValueError(
                f"Resolved results_dir {results_dir!r} is outside workspace_dir "
                f"{workspace_dir!r} (possible symlink escape)"
            )
        results_dir.mkdir(parents=True, exist_ok=True)

        # Create Red_Team directory for incremental markdown findings files
        red_team_dir = (results_dir / "Red_Team").resolve()
        if not str(red_team_dir).startswith(_workspace_real_prefix):
            raise ValueError(
                f"Resolved red_team_dir {red_team_dir!r} is outside workspace_dir "
                f"{workspace_dir!r} (possible symlink escape)"
            )
        red_team_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"BlindAgentRedTeam: launching {n} agents against {workspace_dir} "
            f"(session={session_id}, timeout={t}s, red_team_dir={red_team_dir})"
        )

        # Run agents in parallel
        futures_map: Dict[Any, AttackProfile] = {}
        agent_results: List[Dict] = []
        _suite_start = time.time()  # wall-clock start for accurate duration_ms

        # Fix #10 (as_completed timeout unreachable): do NOT use 'with ThreadPoolExecutor' here.
        # ThreadPoolExecutor.__exit__ calls shutdown(wait=True) unconditionally, blocking until
        # ALL futures finish — making the TimeoutError handler unreachable in practice.
        # Explicit executor management lets shutdown(wait=False) on timeout actually work.
        import concurrent.futures as _cf
        executor = ThreadPoolExecutor(max_workers=n)
        _executor_shutdown_done = False
        try:
            for _agent_idx, profile in enumerate(profiles):
                results_file = results_dir / f"red_team_agent_{profile.index}.json"
                findings_file = red_team_dir / f"agent{profile.index}_findings.md"
                # Fix TOCTOU symlink race: use O_NOFOLLOW so open() itself
                # refuses to follow symlinks — no gap between check and open.
                _oflags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
                for _p in (results_file, findings_file):
                    try:
                        _fd = os.open(str(_p), _oflags, 0o644)
                        os.close(_fd)
                    except OSError as _e:
                        raise RuntimeError(
                            f"Cannot safely create {_p} (symlink or permission issue): {_e}"
                        ) from _e
                # BUG-M3: use per-agent budget from list (agent 0 absorbs remainder)
                _budget_state = _AgentBudgetState(
                    work_budget=_per_agent_budgets[_agent_idx],
                    safety_timeout=_safety_t,
                )
                future = executor.submit(
                    self._spawn_single_agent,
                    profile=profile,
                    workspace_dir=workspace_dir,
                    mission_desc=mission_desc,
                    results_file=results_file,
                    findings_file=findings_file,
                    session_id=session_id,
                    timeout=_safety_t,
                    budget_state=_budget_state,
                )
                futures_map[future] = profile
                logger.info(f"Submitted agent {profile.index}: {profile.label}")

            try:
                # BUG-H8: use _safety_t (real per-agent kill threshold) as the outer
                # deadline, not the adaptive file-count hint `t`.  Add a grace margin
                # proportional to n so all agents have time to terminate cleanly.
                _grace = max(10, min(60, int(_safety_t * 0.05 * n)))
                for future in as_completed(futures_map, timeout=_safety_t + _grace):
                    profile = futures_map[future]
                    try:
                        agent_result = future.result()
                        agent_results.append(agent_result)
                        logger.info(
                            f"Agent {profile.index} ({profile.label}) complete: "
                            f"success={agent_result.get('success')}, "
                            f"findings={len(agent_result.get('findings', []))}"
                        )
                    except Exception as e:
                        logger.error(f"Agent {profile.index} raised exception: {e}")
                        _err_findings_file = red_team_dir / f"agent{profile.index}_findings.md"
                        agent_results.append({
                            "success": False,
                            "error": str(e),
                            "findings": [],
                            "attack_vectors_tried": [],
                            "profile_index": profile.index,
                            "findings_file": str(_err_findings_file),
                        })
                # Normal completion path — clean shutdown
                executor.shutdown(wait=True)
                _executor_shutdown_done = True
            except _cf.TimeoutError:
                # Fix #10: this branch IS now reachable — no 'with' __exit__ blocks here.
                logger.warning("as_completed timed out — collecting partial results from completed futures")
                for future, profile in futures_map.items():
                    if future.done():
                        try:
                            agent_result = future.result()
                            agent_results.append(agent_result)
                        except Exception as e:
                            agent_results.append({
                                "success": False,
                                "error": str(e),
                                "findings": [],
                                "attack_vectors_tried": [],
                                "profile_index": profile.index,
                            })
                    else:
                        future.cancel()
                        agent_results.append({
                            "success": False,
                            "error": f"Agent {profile.index} timed out (outer executor timeout)",
                            "findings": [],
                            "attack_vectors_tried": [],
                            "profile_index": profile.index,
                        })
                # B3: non-blocking shutdown — cancel_futures stops pending work
                executor.shutdown(wait=False, cancel_futures=True)
                _executor_shutdown_done = True  # prevent double-shutdown in finally
        finally:
            # Ensure executor is released on unexpected exceptions.
            # Skip if normal shutdown already ran to avoid double-shutdown.
            if not _executor_shutdown_done:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception as exc:
                    logger.warning("executor.shutdown failed during cleanup: %s", exc)

        _suite_elapsed = time.time() - _suite_start

        # Log stop reason summary
        stop_reasons_list = [r.get("stop_reason", "unknown") for r in agent_results]
        _reason_counts: Dict[str, int] = {}
        for _sr in stop_reasons_list:
            _reason_counts[_sr] = _reason_counts.get(_sr, 0) + 1
        logger.info(
            "[RED_TEAM] SUMMARY: %s", ", ".join(f"{v}/{n} {k}" for k, v in _reason_counts.items())
        )

        return self._aggregate_results(
            agent_results, session_id, str(workspace_dir), red_team_dir, n,
            suite_elapsed=_suite_elapsed,
        )

    def _spawn_single_agent(
        self,
        profile: AttackProfile,
        workspace_dir: Path,
        mission_desc: str,
        results_file: Path,
        findings_file: Path,
        session_id: str,
        timeout: int,
        budget_state: Optional["_AgentBudgetState"] = None,
    ) -> Dict:
        """
        Spawn a single blind agent as the configured provider CLI subprocess.

        Registers with agent_stream_manager (→ Mission Activity tab) if available.
        Waits for process to exit, then reads results_file.

        Args:
            budget_state: Optional _AgentBudgetState controlling work budget and safety
                          timeout. When provided, safety_timeout replaces `timeout` as
                          the subprocess kill threshold. `timeout` is kept as the
                          as_completed outer guard only.
        """
        import subprocess

        agent_id = f"{session_id}_agent{profile.index}"
        start_time = time.time()
        elapsed = 0.0

        # Resolve effective kill threshold: budget_state.safety_timeout > legacy timeout
        _kill_timeout = budget_state.safety_timeout if budget_state is not None else float(timeout)
        _work_budget = budget_state.work_budget if budget_state is not None else _DEFAULT_WORK_BUDGET
        provider = _resolve_red_team_llm_provider()
        model = _resolve_red_team_llm_model(provider)
        logger.info(
            "[RED_TEAM] Agent[%d] starting: provider=%s model=%s work_budget=%d tokens, safety_timeout=%.0fs",
            profile.index, provider, model or "(default)", _work_budget, _kill_timeout,
        )

        # Build prompt — use string.Template.safe_substitute() to prevent attribute
        # traversal attacks via {var.__class__} syntax that str.format_map() allows.
        # Cycle 3 Fix (#12): sanitize mission_desc — strip newlines/CR to prevent
        # prompt injection via embedded instruction lines in the description.
        # Cycle 5 Fix: apply same sanitization to focus_description (Template injection).
        # BAR-NEW-1: extend to Unicode line separators U+2028, U+2029, U+0085, and null bytes.
        # C2-5a: include \t (tab) in the sanitization pattern so tab-containing strings
        # cannot inject extra columns into TSV log output or corrupt template expansion.
        _safe_mission_desc = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]', ' ', mission_desc).strip()
        _safe_focus_desc = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]', ' ', profile.focus_description).strip()
        # Sanitize workspace_dir, results_file, and findings_file: strip '$' to prevent
        # Template injection when path components contain '${var}' patterns that would be
        # substituted by Template.safe_substitute. shell_workspace_dir uses shlex.quote
        # which handles shell safety separately.
        _safe_workspace_dir = str(workspace_dir).replace('$', '_')
        _safe_results_file = str(results_file).replace('$', '_')
        _safe_findings_file = str(findings_file).replace('$', '_')
        prompt = string.Template(_RED_TEAM_HUNT_PROMPT).safe_substitute(
            workspace_dir=_safe_workspace_dir,
            shell_workspace_dir=shlex.quote(str(workspace_dir)),
            mission_desc=_safe_mission_desc,
            focus_description=_safe_focus_desc,
            results_file=_safe_results_file,
            findings_file=_safe_findings_file,
            agent_id=agent_id,
        )

        # Register with dashboard
        stream_file: Optional[Path] = None
        streaming_enabled = HAS_ASM

        if streaming_enabled:
            try:
                stream_file = _asm_register("mission", agent_id, profile.label, pid=0)
                logger.info(f"Registered agent {agent_id} ({profile.label}) in dashboard")
            except Exception as e:
                logger.warning(f"Failed to register agent {agent_id} in dashboard: {e}")
                stream_file = None
                streaming_enabled = False

        command = _build_red_team_llm_command(provider, stage="TESTING")

        # Cycle 5 Fix: explicit allowlist instead of prefix-based matching
        env = {k: v for k, v in os.environ.items()
               if k in _SAFE_ENV_EXACT or k.startswith(_SAFE_ENV_PREFIXES)}
        # Ensure CLAUDECODE is not present to avoid nested-session error
        env.pop("CLAUDECODE", None)

        proc = None
        stream_thread: Optional[threading.Thread] = None
        _use_drain_thread = False
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(_ATLASFORGE_ROOT),
                env=env,
                start_new_session=True,
            )

            # Update PID in dashboard
            if streaming_enabled and stream_file:
                try:
                    _asm_update_pid(agent_id, proc.pid)
                except Exception as e:
                    logger.debug("ASM update_pid failed: %s", e)

            # Start streaming thread (initialized to None before outer try for leak-safe except)
            if streaming_enabled and stream_file:
                stream_thread = threading.Thread(
                    target=_asm_stream_codex if provider == "codex" else _asm_stream,
                    args=(proc, stream_file, str(workspace_dir), start_time) if provider == "codex" else (proc, stream_file, agent_id),
                    daemon=True,
                    name=f"brt-stream-{agent_id}",
                )
                stream_thread.start()

            # Wait for process
            # Drain stderr in background thread ONLY on the proc.wait() path (streaming=True).
            # On the proc.communicate() path it reads stderr itself — starting a drain thread
            # there would create two concurrent readers on the same fd, causing data races.
            _stderr_chunks: list = []

            def _drain_stderr_bg(pipe):
                try:
                    for chunk in iter(lambda: pipe.read(4096), ''):
                        if chunk:
                            _stderr_chunks.append(chunk)
                except (IOError, OSError, ValueError) as _drain_err:
                    logger.debug("stderr drain thread error for agent %s: %s", agent_id, _drain_err)

            _use_drain_thread = bool(streaming_enabled and stream_file)
            # Bug 12: initialize before conditional block to prevent NameError
            # if an exception occurs between _use_drain_thread=True and Thread creation
            _stderr_thread = None
            if _use_drain_thread:
                _stderr_thread = threading.Thread(
                    target=_drain_stderr_bg, args=(proc.stderr,), daemon=True,
                    name=f"brt-stderr-{agent_id}",
                )
                _stderr_thread.start()

            stdout_text = ""
            try:
                if streaming_enabled and stream_file:
                    # Streaming path: feed stdin manually because communicate() is not used.
                    try:
                        proc.stdin.write(prompt)
                        proc.stdin.close()
                    except Exception as e:
                        logger.error(f"Failed to write prompt to agent {agent_id}: {e} — aborting agent")
                        try:
                            proc.terminate()
                            proc.wait(timeout=5)
                        except Exception as _term_err:
                            logger.debug("Failed to terminate agent %s after stdin error: %s", agent_id, _term_err)
                        if stream_thread is not None:
                            stream_thread.join(timeout=2)
                        return {
                            "success": False,
                            "error": f"stdin write failed: {e}",
                            "findings": [],
                            "attack_vectors_tried": [],
                            "profile_index": profile.index,
                            "elapsed_seconds": time.time() - start_time,
                        }
                    proc.wait(timeout=_kill_timeout)
                    # C3-6: join stream_thread before draining stdout — prevents concurrent readers
                    if stream_thread is not None:
                        stream_thread.join(timeout=5)
                        if stream_thread.is_alive():
                            # Thread still reading stdout — skip drain to avoid concurrent access
                            logger.warning(
                                "stream_thread still alive after join timeout for agent %s — skipping stdout drain",
                                agent_id,
                            )
                        else:
                            # B2: drain residual stdout with 1MB cap to prevent hang/OOM.
                            # Cycle 3 Fix (#14): capture into stdout_text so it is available
                            # for downstream logging/parsing (was silently discarded before).
                            try:
                                stdout_text = proc.stdout.read(1024 * 1024)
                            except (IOError, OSError) as _read_err:
                                logger.debug("stdout read error for agent %s: %s", agent_id, _read_err)
                else:
                    stdout_text, _ = proc.communicate(input=prompt, timeout=_kill_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "[RED_TEAM] Agent[%d] SAFETY TIMEOUT after %.0fs — process may be hung or crashed",
                    profile.index, _kill_timeout,
                )
                if budget_state is not None:
                    if budget_state.stop_reason == "pending":
                        budget_state.stop_reason = "timeout"
                    else:
                        logger.debug(
                            "[RED_TEAM] Agent[%d] secondary stop event: timeout (primary: %s)",
                            profile.index, budget_state.stop_reason,
                        )
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)
                except (ProcessLookupError, OSError):
                    pass
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        proc.wait(timeout=5)
                    except (ProcessLookupError, OSError):
                        pass
                # Join stream_thread on timeout to prevent concurrent stdout reads
                if stream_thread is not None:
                    stream_thread.join(timeout=5)
                # BUG-H3: For streaming path, drain residual stdout to capture any
                # partial JSONL output produced before timeout (enables token counting).
                # BUG-H2: For non-streaming path (communicate() timeout), drain stdout
                # and stderr to prevent pipe-buffer deadlock and zombie subprocess.
                if _use_drain_thread:
                    # Streaming path: only drain if stream_thread is confirmed done.
                    # If is_alive() is True, the thread is still reading the fd —
                    # skip drain to prevent concurrent reads on the same descriptor.
                    if stream_thread is not None and stream_thread.is_alive():
                        logger.warning(
                            "stream_thread still alive after join timeout for agent %s "
                            "on TimeoutExpired path — skipping stdout drain to prevent concurrent fd read",
                            agent_id,
                        )
                    else:
                        try:
                            _timeout_stdout = proc.stdout.read(1024 * 1024)
                            if _timeout_stdout:
                                stdout_text = _timeout_stdout
                        except (IOError, OSError) as _read_err:
                            logger.debug("stdout drain on timeout failed for agent %s: %s", agent_id, _read_err)
                else:
                    # Non-streaming path: drain both pipes to release zombie
                    try:
                        _timeout_stdout = proc.stdout.read(1024 * 1024)
                        if _timeout_stdout:
                            stdout_text = _timeout_stdout
                    except (IOError, OSError):
                        pass
                    try:
                        proc.stderr.read(64 * 1024)
                    except (IOError, OSError):
                        pass
                # On timeout: read whatever was written to findings_file
                _md_content = ""
                try:
                    if findings_file.exists():
                        _md_content = findings_file.read_text(encoding="utf-8")
                except Exception as _md_err:
                    logger.debug("Failed to read findings file on timeout for agent %s: %s", agent_id, _md_err)
                _md_count = len(self._parse_markdown_findings(_md_content)) if _md_content.strip() else 0
                if _md_count > 0:
                    logger.info(f"Agent {agent_id} timed out — {_md_count} findings captured from markdown file")

            # Join stderr drain thread only if it was actually started (Bug 12)
            if _use_drain_thread and _stderr_thread is not None:
                _stderr_thread.join(timeout=5)
                # TD-1: surface stderr from failed/timed-out agents so root-causes are visible
                if _stderr_chunks:
                    _stderr_text = "".join(_stderr_chunks)[:2000]
                    _rc = proc.returncode if proc is not None else None
                    if _rc not in (0, None):
                        logger.warning("Agent %s stderr (rc=%s): %s", agent_id, _rc, _stderr_text)
                    else:
                        logger.debug("Agent %s stderr: %s", agent_id, _stderr_text)

            elapsed = time.time() - start_time

            # Parse output tokens from JSONL stdout and update budget state
            if stdout_text and provider == "claude":
                _output_tokens, _token_source = _parse_output_tokens_from_jsonl(stdout_text)
            else:
                _output_tokens, _token_source = 0, "heuristic"
            if budget_state is not None:
                budget_state.token_source = _token_source
                budget_state.record_output(_output_tokens)
                budget_state.continuation_count += 1
                # Determine stop reason based on work budget; preserve first stop_reason
                # (e.g. "timeout" set above) — only set if still pending.
                if budget_state.stop_reason == "pending":
                    if budget_state.output_tokens >= budget_state.work_budget:
                        budget_state.stop_reason = "work_budget_complete"
                    elif budget_state.check_diminishing_returns():
                        budget_state.stop_reason = "diminishing_returns"
                    else:
                        budget_state.stop_reason = "completed"  # normal completion without hitting budget
                else:
                    # A primary stop event (e.g. timeout) already fired; log secondary as debug.
                    if budget_state.output_tokens >= budget_state.work_budget:
                        _secondary = "work_budget_complete"
                    elif budget_state.check_diminishing_returns():
                        _secondary = "diminishing_returns"
                    else:
                        _secondary = "completed"
                    logger.debug(
                        "[RED_TEAM] Agent[%d] secondary stop event: %s (primary: %s)",
                        profile.index, _secondary, budget_state.stop_reason,
                    )
            _stop_reason = budget_state.stop_reason if budget_state is not None else "completed"

            logger.info(
                "[RED_TEAM] Agent[%d] stop_reason=%s output_tokens=%d findings_written=?",
                profile.index, _stop_reason, _output_tokens,
            )

            # Mark complete in dashboard
            if streaming_enabled and stream_file:
                try:
                    if stream_thread is not None:
                        stream_thread.join(timeout=2.0)
                    _asm_complete(agent_id, error=None)
                except Exception as e:
                    logger.debug("ASM complete failed: %s", e)

        except Exception as e:
            elapsed = time.time() - start_time
            _stop_reason = "error"
            if budget_state is not None:
                if budget_state.stop_reason == "pending":
                    budget_state.stop_reason = "error"
                else:
                    logger.debug(
                        "[RED_TEAM] Agent[%d] secondary stop event: error (primary: %s): %s",
                        profile.index, budget_state.stop_reason, e,
                    )
            logger.error(f"Agent {agent_id} subprocess failed: {e}")
            # Join any threads that may have been started before the exception
            if stream_thread is not None:
                stream_thread.join(timeout=5)
            if _use_drain_thread and _stderr_thread is not None:
                _stderr_thread.join(timeout=5)
            if streaming_enabled and stream_file:
                try:
                    _asm_complete(agent_id, error=str(e))
                except Exception as e2:
                    logger.debug("ASM complete (error path) failed: %s", e2)
            return {
                "success": False,
                "error": str(e),
                "findings": [],
                "attack_vectors_tried": [],
                "profile_index": profile.index,
                "elapsed_seconds": elapsed,
                "stop_reason": _stop_reason,
            }
        finally:
            # Fix 5b — zombie cleanup: ensure process is dead regardless of exit path
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception as _term_err:
                    logger.debug(f"Agent {agent_id}: terminate failed ({_term_err}), attempting kill")
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except Exception as _kill_err:
                        logger.debug(f"Agent {agent_id}: kill also failed: {_kill_err}")

        # Read markdown findings file (primary — written incrementally)
        markdown_findings: List[dict] = []
        md_content = ""
        try:
            if findings_file.exists():
                md_content = findings_file.read_text(encoding="utf-8")
                if md_content.strip():
                    markdown_findings = self._parse_markdown_findings(md_content)
                    logger.info(f"Agent {agent_id}: {len(markdown_findings)} findings from markdown file")
        except Exception as e:
            logger.warning(f"Failed to read markdown findings for {agent_id}: {e}")

        # Read JSON results file written by agent (legacy / backward compat)
        findings_raw = []
        attack_vectors_tried = []
        parse_error = None

        try:
            _results_stat = results_file.stat()
            _results_exists = True
        except FileNotFoundError:
            _results_stat = None
            _results_exists = False

        if _results_exists:
            # C2-5b: detect empty/partial writes — a zero-byte file means the agent
            # crashed after creating the file but before writing any content.
            if _results_stat.st_size == 0:
                parse_error = f"Agent {agent_id} wrote empty results file (possible partial write or crash)"
                logger.warning(parse_error)
            else:
                try:
                    data = json.loads(results_file.read_text(encoding='utf-8'))
                    findings_raw = data.get("findings", [])
                    attack_vectors_tried = data.get("attack_vectors_tried", [])
                except Exception as e:
                    parse_error = f"Failed to parse {results_file}: {e}"
                    logger.warning(parse_error)
        else:
            parse_error = f"Agent {agent_id} did not write results to {results_file}"
            logger.warning(parse_error)

        # Merge: markdown findings take priority; JSON findings fill in if markdown empty
        combined_findings = markdown_findings if markdown_findings else findings_raw
        partial_success = len(markdown_findings) > 0
        # Success = agent ran correctly and results parsed cleanly.
        # Findings can legitimately be empty on bug-free code — that is not failure.
        # parse_error is None means the agent completed and its output was parseable.
        success = parse_error is None

        # Cycle 2 Fix (JSON parse silencing): preserve parse_error even when
        # partial_success=True so callers can distinguish clean partial success
        # (markdown found, JSON also parsed) from corrupt partial success
        # (markdown found but JSON parse failed). Previously error=None hid failures.
        _final_stop_reason = budget_state.stop_reason if budget_state is not None else "completed"
        logger.info(
            "[RED_TEAM] Agent[%d] stop_reason=%s output_tokens=%d findings=%d",
            profile.index, _final_stop_reason,
            budget_state.output_tokens if budget_state is not None else 0,
            len(combined_findings),
        )
        return {
            "success": success,
            "partial_success": partial_success,
            "error": parse_error,
            "findings": combined_findings,
            "attack_vectors_tried": attack_vectors_tried,
            "profile_index": profile.index,
            "elapsed_seconds": elapsed,
            "findings_file": str(findings_file),
            "stop_reason": _final_stop_reason,
            "output_tokens": budget_state.output_tokens if budget_state is not None else 0,
        }

    def _aggregate_results(
        self,
        agent_results: List[Dict],
        session_id: str,
        workspace_path: str,
        red_team_dir: Optional[Path] = None,
        agents_run: int = 0,
        suite_elapsed: Optional[float] = None,
    ) -> RedTeamResult:
        """
        Aggregate per-agent result dicts into a single RedTeamResult.
        Parses markdown findings from each agent's findings_file,
        then generates the aggregated report.md.
        """
        all_findings: List[RedTeamFinding] = []
        all_vectors: List[str] = []
        any_error: Optional[str] = None
        # Use wall-clock suite_elapsed when available; fall back to max(agent times)
        total_elapsed = suite_elapsed if suite_elapsed is not None else max(
            (r.get("elapsed_seconds", 0) for r in agent_results), default=0
        )

        for agent_data in agent_results:
            if not agent_data.get("success") and not agent_data.get("partial_success"):
                # Accumulate errors — don't overwrite earlier agent errors
                any_error = any_error or agent_data.get("error", "agent failed")

            all_vectors.extend(agent_data.get("attack_vectors_tried", []))

            # BAR-3: prefer JSON findings cache; only read markdown from disk when cache is empty.
            # This prevents double-parsing when findings are already captured in JSON.
            json_findings = agent_data.get("findings", [])
            extra_md_findings: List[dict] = []
            if not json_findings:
                # Only read markdown findings file when JSON cache is empty (incremental/timeout case)
                findings_file_path = agent_data.get("findings_file")
                if findings_file_path:
                    try:
                        fpath = Path(findings_file_path)
                        if fpath.exists():
                            content = fpath.read_text(encoding="utf-8")
                            if content.strip():
                                extra_md_findings = self._parse_markdown_findings(content)
                    except Exception as e:
                        logger.warning("Failed to read findings file %s: %s", findings_file_path, e)

            # Merge JSON findings + markdown findings; deduplicate by (title, affected_code)
            # so findings present in both sources are not double-counted.
            seen: set = set()
            source_findings: List[dict] = []
            for fd in list(extra_md_findings) + list(json_findings):
                key = (fd.get("title", ""), fd.get("affected_code", ""))
                if key not in seen:
                    seen.add(key)
                    source_findings.append(fd)

            for fd in source_findings:
                try:
                    cat_raw = fd.get("category", "logic")
                    try:
                        category = AttackCategory(cat_raw)
                    except ValueError:
                        category = AttackCategory.LOGIC_FLAW

                    try:
                        confidence = float(fd.get("confidence", 0.5))
                    except (ValueError, TypeError):
                        confidence = 0.5

                    finding = RedTeamFinding(
                        category=category,
                        severity=fd.get("severity", "medium"),
                        title=fd.get("title", "Untitled finding"),
                        description=fd.get("description", ""),
                        reproduction_steps=fd.get("reproduction_steps", []),
                        affected_code=fd.get("affected_code", "unknown"),
                        suggested_fix=fd.get("suggested_fix", ""),
                        confidence=confidence,
                    )
                    all_findings.append(finding)
                except Exception as e:
                    logger.warning(f"Skipping malformed finding: {e} — {fd}")

        # Generate aggregated report.md
        if red_team_dir is not None:
            try:
                self._generate_report(
                    findings=all_findings,
                    session_id=session_id,
                    agents_run=agents_run or len(agent_results),
                    red_team_dir=red_team_dir,
                )
            except Exception as e:
                logger.warning(f"Failed to generate Red Team report: {e}")

        # Fix 4 — false success: only True when at least one agent produced valid results
        any_success = any(
            r.get("success", False) or r.get("partial_success", False)
            for r in agent_results
        )
        # Surface per-agent stop reasons as a dict on the result object (non-blocking item).
        _stop_reasons_map: Dict[str, str] = {
            f"agent{r.get('profile_index', i)}": r.get("stop_reason", "unknown")
            for i, r in enumerate(agent_results)
        }
        return RedTeamResult(
            session_id=session_id,
            code_analyzed=f"workspace: {workspace_path}",
            agent_model=f"{_resolve_red_team_llm_provider()}-blind-agent-team",
            timestamp=datetime.now().isoformat(),
            duration_ms=total_elapsed * 1000,
            findings=all_findings,
            attack_vectors_tried=list(dict.fromkeys(all_vectors)),  # deduplicate
            success=any_success,
            error=any_error,
            stop_reasons=_stop_reasons_map,
        )

    # ------------------------------------------------------------------
    # Markdown findings parser — handles incremental ---BUG--- format
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_markdown_findings(content: str) -> List[dict]:
        """
        Parse a markdown findings file written by a Red Team agent.

        Extracts ---BUG---...---END BUG--- and ---SUSPECTED---...---END SUSPECTED--- blocks.
        Returns list of dicts compatible with RedTeamFinding construction.
        Malformed blocks are skipped with a warning log.
        """
        if content is None:
            return []
        import re

        _SEV_MAP = {
            "CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium",
            "LIGHT": "low", "NONBLOCKING": "info",
        }
        _TYPE_TO_CAT = {
            "logic_error": "logic", "missing_validation": "boundary",
            "race_condition": "concurrency", "memory_leak": "resource",
            "security": "injection", "crash": "boundary", "data_loss": "content_loss",
            "incorrect_behavior": "logic", "missing_test": "logic",
            "exception_handling": "error_handling", "null_check": "boundary",
            "type_confusion": "type_confusion", "off_by_one": "boundary",
        }

        _FIELD_HEADER = re.compile(
            r'^(?:File|Line|Severity|Type|Description|Reproduction|Confidence):\s'
        )
        def _field(name: str, text: str) -> str:
            # Line-splitting parser: collects lines belonging to a field by scanning
            # for the field header, then accumulating continuation lines until the
            # next known field header is encountered. This avoids the regex lookahead
            # bug where embedded field-name keywords mid-value caused silent truncation.
            lines = text.splitlines()
            collecting = False
            result_lines = []
            _header_prefix = f'{name}: '
            for line in lines:
                if line.startswith(_header_prefix):
                    collecting = True
                    result_lines.append(line[len(_header_prefix):].lstrip())
                elif collecting:
                    if _FIELD_HEADER.match(line):
                        break
                    result_lines.append(line)
            return '\n'.join(result_lines).strip()

        results: List[dict] = []

        _complete_raw_matches = re.findall(r'---BUG---\s*(.*?)\s*---END BUG---', content, re.DOTALL)
        for raw in _complete_raw_matches:
            try:
                file_val = _field("File", raw).strip()
                line_val = _field("Line", raw).strip()
                desc_val = _field("Description", raw).strip()
                # Cycle 2 Fix 5: skip blocks with no description (garbled/empty)
                if not desc_val:
                    logger.warning("Skipping ---BUG--- block with empty Description")
                    continue
                sev_raw = (_field("Severity", raw).upper().split() or ["HIGH"])[0]
                type_val = _field("Type", raw).strip() or "logic_error"
                repro_val = _field("Reproduction", raw).strip()
                severity = _SEV_MAP.get(sev_raw, "medium")
                affected = f"{file_val}:{line_val}" if file_val else "unknown"
                results.append({
                    "category": _TYPE_TO_CAT.get(type_val.lower(), "logic"),
                    "severity": severity,
                    "title": desc_val[:80] if desc_val else f"Bug at {affected}",
                    "description": desc_val,
                    "reproduction_steps": [repro_val] if repro_val else [],
                    "affected_code": affected,
                    "suggested_fix": "",
                    "confidence": 0.8,
                    "_markdown_type": type_val,
                })
            except Exception as e:
                logger.warning(f"Skipping malformed ---BUG--- block: {e}")

        # BAR-C3-1: Parse incomplete ---BUG--- blocks (agent timed out mid-output)
        # Collect already-parsed complete blocks to avoid duplicates (reuse cached matches)
        _parsed_complete = set()
        for raw in _complete_raw_matches:
            _parsed_complete.add(raw.strip())
        # Find all ---BUG--- blocks (including incomplete ones without ---END BUG---)
        # C2-Fix: Drop re.MULTILINE — ^ anchors with MULTILINE allow greedy cross-block
        # capture when alternation matches mid-line; DOTALL alone is sufficient here.
        for partial_match in re.finditer(r'---BUG---\s*(.*?)(?=---BUG---|---SUSPECTED---|\Z)', content, re.DOTALL):
            # H12: strip the ---END BUG--- suffix before dedup comparison — partial blocks
            # that are actually complete include the END marker in the captured group,
            # so without stripping, they never match _parsed_complete and get double-counted.
            partial_raw = partial_match.group(1)
            # Normalize: remove trailing ---END BUG--- marker if present before dedup check
            partial_normalized = re.sub(r'\s*---END BUG---\s*$', '', partial_raw).strip()
            if not partial_normalized or partial_normalized in _parsed_complete:
                continue  # Skip empty or already-parsed complete blocks
            # Also skip if the original match contains ---END BUG--- (already parsed above)
            if '---END BUG---' in partial_raw:
                continue
            partial = partial_normalized
            try:
                file_val = _field("File", partial)
                desc_val = _field("Description", partial)
                if desc_val:  # At minimum need a description
                    type_val = _field("Type", partial) or "logic_error"
                    sev_raw = (_field("Severity", partial).upper().split() or ["HIGH"])[0] if _field("Severity", partial) else "HIGH"
                    line_val = _field("Line", partial)
                    severity = _SEV_MAP.get(sev_raw, "medium")
                    affected = f"{file_val}:{line_val}" if file_val else "unknown"
                    results.append({
                        "category": _TYPE_TO_CAT.get(type_val.lower(), "logic"),
                        "severity": severity,
                        "title": f"[PARTIAL] {desc_val[:70]}" if desc_val else f"[PARTIAL] Bug at {affected}",
                        "description": f"[PARTIAL - agent timed out] {desc_val}",
                        "reproduction_steps": [],
                        "affected_code": affected,
                        "suggested_fix": "",
                        "confidence": 0.4,
                        "_markdown_type": type_val,
                    })
            except Exception as e:
                logger.warning("Skipping unparseable partial ---BUG--- block: %s", e)

        for raw in re.findall(r'---SUSPECTED---\s*(.*?)\s*---END SUSPECTED---', content, re.DOTALL):
            try:
                file_val = _field("File", raw)
                line_val = _field("Line", raw)
                desc_val = _field("Description", raw)
                if not desc_val:
                    logger.warning("Skipping ---SUSPECTED--- block with empty Description")
                    continue
                affected = f"{file_val}:{line_val}" if file_val else "unknown"
                results.append({
                    "category": "logic",
                    "severity": "info",
                    "title": f"[SUSPECTED] {desc_val[:70]}" if desc_val else f"Suspected issue at {affected}",
                    "description": desc_val,
                    "reproduction_steps": [],
                    "affected_code": affected,
                    "suggested_fix": "",
                    "confidence": 0.4,
                    "_markdown_type": "suspected",
                    "_suspected": True,
                })
            except Exception as e:
                logger.warning(f"Skipping malformed ---SUSPECTED--- block: {e}")

        return results

    # ------------------------------------------------------------------
    # Aggregated markdown report generator
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_report(
        findings: List[RedTeamFinding],
        session_id: str,
        agents_run: int,
        red_team_dir: Path,
    ) -> Path:
        """
        Write {red_team_dir}/report.md aggregating all agent findings by severity.
        Sections: CRITICAL, HIGH, MODERATE, LIGHT, NONBLOCKING, SUSPECTED.
        Copies to /mnt/xwing/ if available. Returns report path.
        """
        # Bug 4 fix: guard against None inputs to prevent AttributeError/TypeError
        session_id = session_id or "unknown-session"
        if red_team_dir is None:
            raise ValueError("red_team_dir must not be None")
        _SEV_ORDER = ["critical", "high", "medium", "low", "info"]
        _SEV_LABEL = {
            "critical": "CRITICAL", "high": "HIGH", "medium": "MODERATE",
            "low": "LIGHT", "info": "NONBLOCKING",
        }

        suspected = [f for f in findings if (getattr(f, 'title', '') or '').startswith('[SUSPECTED]')]
        confirmed = [f for f in findings if f not in suspected]
        buckets: Dict[str, List[RedTeamFinding]] = {s: [] for s in _SEV_ORDER}
        for f in confirmed:
            sev = f.severity.lower() if f.severity else "info"
            buckets[sev if sev in buckets else "info"].append(f)

        total = len(confirmed) + len(suspected)
        lines = [
            f"# Red Team Report — {session_id}",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Agents: {agents_run}",
            f"Total Findings: {total}",
            f"  Confirmed: {len(confirmed)}",
            f"  Suspected: {len(suspected)}",
            "",
        ]
        for sev in _SEV_ORDER:
            bucket = buckets[sev]
            lines.append(f"## {_SEV_LABEL[sev]} ({len(bucket)})")
            if not bucket:
                lines.append("_None_")
            else:
                for f in bucket:
                    lines.append(f"- **{f.title or 'Unknown issue'}**")
                    lines.append(f"  - Code: `{f.affected_code}`")
                    if f.description:
                        lines.append(f"  - {f.description}")
                    if f.reproduction_steps:
                        lines.append(f"  - Repro: {'; '.join(f.reproduction_steps)}")
            lines.append("")

        lines.append(f"## SUSPECTED ({len(suspected)})")
        if not suspected:
            lines.append("_None_")
        else:
            for f in suspected:
                lines.append(f"- **{f.title or 'Unknown issue'}**")
                lines.append(f"  - Code: `{f.affected_code}`")
                if f.description:
                    lines.append(f"  - {f.description}")
        lines.append("")

        report_content = "\n".join(lines)
        report_path = red_team_dir / "report.md"
        report_path.write_text(report_content, encoding="utf-8")
        logger.info(f"Red Team report written to {report_path} ({total} total findings)")

        return report_path


# ---------------------------------------------------------------------------
# RedTeamOrchestrator — Hierarchical parallel red team via HierarchicalExperiment
# ---------------------------------------------------------------------------

# Extended 6-profile list (adds Error Handling and Content Loss profiles)
_EXTENDED_ATTACK_PROFILES = _ATTACK_PROFILES + [
    AttackProfile(
        index=4,
        categories=[AttackCategory.ERROR_HANDLING, AttackCategory.LOGIC_FLAW],
        label="Red Team - ErrorHandling",
        focus_description=(
            "ERROR HANDLING and RECOVERY PATHS.\n"
            "Look for bare except clauses that swallow errors silently, "
            "functions that return None without signalling failure, "
            "missing try/except around I/O calls, and exception handlers "
            "that log but don't propagate. Test what happens when dependencies "
            "are unavailable, files are missing, or network calls fail."
        ),
    ),
    AttackProfile(
        index=5,
        categories=[AttackCategory.CONTENT_LOSS, AttackCategory.STATE_CORRUPTION],
        label="Red Team - ContentLoss",
        focus_description=(
            "DATA LOSS and CONTENT PRESERVATION.\n"
            "Look for merge/combine/aggregate operations that return "
            "placeholder strings like 'merged data' or 'combined result' "
            "instead of the actual merged content. Look for transform "
            "operations that succeed but discard semantic content. "
            "Verify that pipeline stages preserve source data through "
            "the full processing chain, not just the final output shape."
        ),
    ),
]


def _build_agent_prompt(
    profile: AttackProfile,
    workspace_dir: Path,
    mission_desc: str,
    results_file: Path,
    findings_file: Optional[Path] = None,
    agent_id: str = "",
) -> str:
    """
    Shared prompt builder used by both BlindAgentRedTeam and RedTeamOrchestrator.

    Uses string.Template.safe_substitute() to prevent attribute traversal attacks
    via {var.__class__} syntax that str.format_map() allows. Unknown $vars are
    left as-is with no error.
    """
    # BAR-4 / BAR-NEW-1: strip tabs, newlines, CR, and Unicode line separators from
    # mission_desc and focus_description to prevent prompt injection via multi-line
    # strings, tab characters, or Unicode line separator characters.
    _safe_desc = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]+', ' ', mission_desc).strip()
    _safe_focus = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]+', ' ', profile.focus_description).strip()
    return string.Template(_RED_TEAM_HUNT_PROMPT).safe_substitute(
        workspace_dir=str(workspace_dir),
        shell_workspace_dir=shlex.quote(str(workspace_dir)),
        mission_desc=_safe_desc,
        focus_description=_safe_focus,
        results_file=str(results_file),
        findings_file=str(findings_file) if findings_file else str(results_file),
        agent_id=agent_id or f"agent_{profile.index}",
    )


class RedTeamOrchestrator:
    """
    Hierarchical Red Team Orchestrator.

    Uses HierarchicalExperiment to dispatch each AttackProfile as an
    independent WorkUnit. This eliminates the monolithic blocking call
    of BlindAgentRedTeam and lets the hierarchical framework manage
    pool slots, dashboard registration, and timeout budgets.

    Each attack domain runs as a fully independent parallel agent tab
    in the Mission Activity dashboard. If one times out, the others
    complete and partial results are returned — directly solving the
    timeout issue.

    Replaces BlindAgentRedTeam as the preferred red team mechanism.
    Falls back to BlindAgentRedTeam if hierarchical_framework is
    unavailable or not importable.

    Supports 4-6 attack domains simultaneously:
      0 - Boundary & Type Confusion
      1 - Injection & Error Handling
      2 - Logic & State Corruption
      3 - Concurrency & Resource Exhaustion
      4 - Error Handling & Recovery
      5 - Data Loss & Content Preservation
    """

    def __init__(
        self,
        timeout: int = 900,
        n_agents: int = 4,
        work_budget: Optional[int] = None,
        safety_timeout: Optional[float] = None,
    ):
        """
        Args:
            timeout: Kept for backward compatibility. Actual kill threshold is safety_timeout.
            n_agents: Number of parallel attack agents (1-6)
            work_budget: Target output tokens per agent. None → model-aware default.
            safety_timeout: Seconds before killing a hung/stalled agent.
                            None → model-aware default (1800-3600s).
        """
        # Iter 4 Fix B5: validate timeout + n_agents (mirror BlindAgentRedTeam pattern)
        import math as _math
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")
        if not _math.isfinite(timeout):
            raise ValueError(f"timeout must be finite, got {timeout!r}")
        if isinstance(n_agents, bool) or not isinstance(n_agents, int):
            raise TypeError(f"n_agents must be int, got {type(n_agents).__name__}: {n_agents!r}")
        if n_agents <= 0:
            raise ValueError(f"n_agents must be positive, got {n_agents}")
        self.timeout = timeout
        _max_ext = len(_EXTENDED_ATTACK_PROFILES)
        if n_agents > _max_ext:
            logger.warning(
                "n_agents=%d exceeds maximum profile count %d; capping to %d",
                n_agents, _max_ext, _max_ext,
            )
        self.n_agents = min(max(1, n_agents), _max_ext)

        # Work budget and safety timeout — model-aware defaults
        if work_budget is not None and work_budget <= 0:
            raise ValueError(
                f"work_budget must be positive, got {work_budget!r}. "
                "Pass None to use the model-aware default."
            )
        _model = _detect_model()
        _wb = work_budget if work_budget is not None else _model_work_budget(_model)
        self.work_budget: int = _wb if _wb > 0 else _DEFAULT_WORK_BUDGET
        _st = safety_timeout if safety_timeout is not None else _model_safety_timeout(_model)
        self.safety_timeout: float = _st if _st > 0 else _DEFAULT_SAFETY_TIMEOUT
        logger.info(
            "RedTeamOrchestrator init: work_budget=%d tokens, safety_timeout=%.0fs, model=%s",
            self.work_budget, self.safety_timeout, _model or "(unset)",
        )

    def _validate_workspace_dir(self, path: Path) -> None:
        """Raise ValueError if path is not within any allowed workspace root.

        Uses Path.resolve() to canonicalize symlinks and '..' components,
        then Path.relative_to() for a safe containment check (no string
        prefix matching).
        """
        resolved = path.resolve()
        for allowed_root in _ALLOWED_WORKSPACE_ROOTS:
            try:
                resolved.relative_to(allowed_root)
                return
            except ValueError:
                continue
        raise ValueError(
            f"workspace_dir {resolved!r} is not within any allowed root: "
            f"{[str(r) for r in _ALLOWED_WORKSPACE_ROOTS]}"
        )

    def _build_work_units(
        self,
        profiles: list,
        workspace_dir: Path,
        mission_desc: str,
        results_dir: Path,
    ) -> list:
        """
        Convert AttackProfiles into WorkUnits for HierarchicalExperiment.

        Each WorkUnit gets both results_file (JSON legacy) and findings_file
        (markdown incremental) paths embedded in its prompt.
        All units are wave=1 so they all start in parallel.
        """
        from mission_splitter import WorkUnit, SplitStrategy

        # Create Red_Team directory for incremental markdown findings
        red_team_dir = results_dir / "Red_Team"
        red_team_dir.mkdir(parents=True, exist_ok=True)

        work_units = []
        for profile in profiles:
            results_file = results_dir / f"red_team_agent_{profile.index}.json"
            findings_file = red_team_dir / f"agent{profile.index}_findings.md"
            with open(results_file, 'w'):
                pass   # atomic truncate/create, no race window
            with open(findings_file, 'w'):
                pass   # atomic truncate/create, no race window

            agent_id = f"rto_agent{profile.index}"
            # Use string.Template.safe_substitute() to prevent attribute traversal
            # attacks via {var.__class__} syntax that str.format_map() allows.
            # Cycle 5/6: sanitize focus_description and mission_desc — strip tabs, newlines,
            # CR, and Unicode line separators to prevent TSV injection and prompt injection.
            _safe_focus = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]+', ' ', profile.focus_description).strip()
            _safe_mission = re.sub(r'[\t\r\n\u2028\u2029\x85\x00]+', ' ', mission_desc).strip()
            prompt = string.Template(_RED_TEAM_HUNT_PROMPT).safe_substitute(
                workspace_dir=str(workspace_dir),
                shell_workspace_dir=shlex.quote(str(workspace_dir)),
                mission_desc=_safe_mission,
                focus_description=_safe_focus,
                results_file=str(results_file),
                findings_file=str(findings_file),
                agent_id=agent_id,
            )

            work_units.append(WorkUnit(
                id=f"red_team_agent_{profile.index}",
                description=profile.label,
                prompt=prompt,
                dependencies=[],
                estimated_complexity=7,
                wave=1,
                strategy=SplitStrategy.TASK_BASED,
                metadata={
                    "agent_kind": "red_team",
                    "profile_index": profile.index,
                    "profile_label": profile.label,
                    "categories": [c.value for c in profile.categories],
                    "results_file": str(results_file),
                    "findings_file": str(findings_file),
                },
            ))
        return work_units

    def launch(
        self,
        workspace_dir: Path,
        mission_desc: str = "Unknown — explore and determine from code",
        n_agents: Optional[int] = None,
        timeout: Optional[int] = None,
        work_budget: Optional[int] = None,
        safety_timeout: Optional[float] = None,
    ) -> RedTeamResult:
        """
        Launch N parallel blind agents against the workspace via HierarchicalExperiment.

        Args:
            workspace_dir: Path to the codebase to attack
            mission_desc: Brief description of what the codebase does
            n_agents: Number of parallel agents (overrides self.n_agents)
            timeout: Backward-compat alias; use safety_timeout for new callers.
            work_budget: Target output tokens across all agents (overrides self.work_budget).
            safety_timeout: Per-agent kill timeout in seconds (overrides self.safety_timeout).

        Returns:
            Aggregated RedTeamResult with findings from all completed agents.
            Partial results are returned if some agents time out.
        """
        workspace_dir = Path(workspace_dir).resolve()
        self._validate_workspace_dir(workspace_dir)

        # Cycle 3 Fix P3b: validate override params (matching __init__ checks)
        if n_agents is not None:
            if isinstance(n_agents, bool) or not isinstance(n_agents, int):
                raise TypeError(f"n_agents must be int, got {type(n_agents).__name__}: {n_agents!r}")
            if n_agents <= 0:
                raise ValueError(f"n_agents must be positive, got {n_agents}")
        if timeout is not None:
            import math as _math
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError(f"timeout must be a positive number, got {type(timeout).__name__}: {timeout!r}")
            if timeout <= 0:
                raise ValueError(f"timeout must be positive, got {timeout}")
            if not _math.isfinite(timeout):
                raise ValueError(f"timeout must be finite, got {timeout!r}")
        n = min(max(1, n_agents if n_agents is not None else self.n_agents),
                len(_EXTENDED_ATTACK_PROFILES))
        # Resolve effective safety timeout
        _eff_safety = safety_timeout if safety_timeout is not None else (
            float(timeout) if timeout is not None else self.safety_timeout
        )
        # For backward compat pass-through to hierarchical framework, use safety timeout as `t`
        t = int(_eff_safety)
        _eff_budget = work_budget if work_budget is not None else self.work_budget

        profiles = _EXTENDED_ATTACK_PROFILES[:n]
        session_id = f"rto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _workspace_real = str(workspace_dir.resolve())
        _workspace_real_prefix = _workspace_real + os.sep
        results_dir = (workspace_dir / "tests").resolve()
        if not (str(results_dir) == _workspace_real or str(results_dir).startswith(_workspace_real_prefix)):
            raise ValueError(
                f"Resolved results_dir {results_dir!r} is outside workspace_dir "
                f"{workspace_dir!r} (possible symlink escape)"
            )
        results_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"RedTeamOrchestrator: launching {n} agents against {workspace_dir} "
            f"(session={session_id}, safety_timeout={_eff_safety:.0f}s, work_budget={_eff_budget})"
        )

        try:
            return self._launch_hierarchical(
                profiles=profiles,
                workspace_dir=workspace_dir,
                mission_desc=mission_desc,
                results_dir=results_dir,
                session_id=session_id,
                n=n,
                t=t,
            )
        except ImportError as exc:
            logger.warning(
                f"hierarchical_framework not available ({exc}), "
                f"falling back to BlindAgentRedTeam"
            )
            team = BlindAgentRedTeam(
                timeout=t, n_agents=n,
                work_budget=_eff_budget, safety_timeout=_eff_safety,
            )
            return team.launch_parallel_team(
                workspace_dir=workspace_dir,
                mission_desc=mission_desc,
                n_agents=n,
                work_budget=_eff_budget,
                safety_timeout=_eff_safety,
            )

    def _launch_hierarchical(
        self,
        profiles: list,
        workspace_dir: Path,
        mission_desc: str,
        results_dir: Path,
        session_id: str,
        n: int,
        t: int,
    ) -> RedTeamResult:
        """Inner implementation using HierarchicalExperiment."""
        from hierarchical_framework import HierarchicalExperiment, HierarchicalConfig
        from experiment_framework import ModelType as _ModelType

        work_units = self._build_work_units(
            profiles=profiles,
            workspace_dir=workspace_dir,
            mission_desc=mission_desc,
            results_dir=results_dir,
        )

        # total_timeout = per-agent budget * agents + 60 s overhead
        total_timeout = t * n + 60

        config = HierarchicalConfig(
            mission_id=f"red_team_{session_id}",
            description=f"Red Team analysis of {workspace_dir.name}",
            total_timeout=total_timeout,
            max_agents=n,
            max_subagents_per_agent=0,
            model=_ModelType.CLAUDE_SONNET,
            stage="TESTING",
            enable_streaming=True,
        )

        logger.info(
            f"HierarchicalConfig: total_timeout={total_timeout}s, "
            f"max_agents={n}, work_units={len(work_units)}"
        )

        exp = HierarchicalExperiment(config)
        hier_results = exp.run(work_units)

        return self._aggregate_hierarchical_results(
            hier_results=hier_results,
            profiles=profiles,
            results_dir=results_dir,
            session_id=session_id,
            workspace_dir=workspace_dir,
        )

    def _aggregate_hierarchical_results(
        self,
        hier_results,
        profiles: list,
        results_dir: Path,
        session_id: str,
        workspace_dir: Path,
    ) -> RedTeamResult:
        """
        Convert HierarchicalResults into a RedTeamResult.

        Tries three finding sources per agent (in priority order):
        1. Markdown findings_file written incrementally by agent
        2. JSON file written by agent to results_dir/red_team_agent_N.json
        3. JSON block extracted from AgentResult.response text
        """
        all_findings: List[RedTeamFinding] = []
        all_vectors: List[str] = []
        any_success = False
        any_error: Optional[str] = None
        total_elapsed = 0.0
        red_team_dir = results_dir / "Red_Team"

        for agent_result in hier_results.agent_results:
            elapsed = getattr(agent_result, 'elapsed_seconds', 0) or 0
            total_elapsed = max(total_elapsed, elapsed)

            # Match agent_id back to profile index
            agent_id = getattr(agent_result, 'agent_id', '')
            profile_index = None
            # BA-M2: use exact string match on known work-unit ID format, not regex.
            # Old regex r'(?<!\d)1(?!\d)' incorrectly matched 'agent1' in 'agent_11'.
            for prof in profiles:
                _suffix = f"_agent{prof.index}"
                # Guard against suffix collision: "_agent1" matching "_agent11" etc.
                # Check that the character immediately before the suffix (if any) is not a digit.
                if agent_id == f"red_team_agent_{prof.index}":
                    profile_index = prof.index
                    break
                elif agent_id.endswith(_suffix):
                    _pre = agent_id[:-len(_suffix)]
                    if not _pre or not _pre[-1].isdigit():
                        profile_index = prof.index
                        break
            # Fallback: anchored-end regex to extract trailing integer
            if profile_index is None:
                import re as _re
                m = _re.search(r'_agent_?(\d+)$', agent_id)
                if m:
                    idx = int(m.group(1))
                    if any(p.index == idx for p in profiles):
                        profile_index = idx

            findings_raw: List[dict] = []
            vectors_raw: List[str] = []
            found = False

            # Source 0: markdown findings_file (primary — incremental writes)
            if profile_index is not None:
                md_file = red_team_dir / f"agent{profile_index}_findings.md"
                if md_file.exists():
                    try:
                        md_content = md_file.read_text(encoding="utf-8")
                        if md_content.strip():
                            findings_raw = BlindAgentRedTeam._parse_markdown_findings(md_content)
                            if findings_raw:
                                found = True
                                logger.info(
                                    f"Agent {agent_id}: {len(findings_raw)} findings from markdown file"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to read markdown findings for {agent_id}: {e}")

            # Source 1: JSON results file (legacy / backward compat)
            if not found and profile_index is not None:
                results_file = results_dir / f"red_team_agent_{profile_index}.json"
                if results_file.exists():
                    try:
                        data = json.loads(results_file.read_text(encoding='utf-8'))
                        findings_raw = data.get("findings", [])
                        vectors_raw = data.get("attack_vectors_tried", [])
                        found = True
                        logger.info(
                            f"Agent {agent_id}: {len(findings_raw)} findings from JSON file"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to parse results file: {e}")

            # Source 2: extract from response text
            if not found:
                response_text = getattr(agent_result, 'response', '') or ''
                parsed = self._extract_json_from_response(response_text)
                if parsed:
                    findings_raw = parsed.get("findings", [])
                    vectors_raw = parsed.get("attack_vectors_tried", [])
                    found = True
                    logger.info(
                        f"Agent {agent_id}: {len(findings_raw)} findings from response"
                    )

            status = getattr(agent_result, 'status', 'unknown')
            # Bug 13: a completed agent that found no bugs is still a successful run
            if status == 'completed' or (found and findings_raw):
                any_success = True
            elif status in ('failed', 'timeout') and not found:
                err = getattr(agent_result, 'error', None) or f"agent {agent_id} {status}"
                any_error = err

            all_vectors.extend(vectors_raw)

            for fd in findings_raw:
                try:
                    cat_raw = fd.get("category", "logic")
                    try:
                        category = AttackCategory(cat_raw)
                    except ValueError:
                        category = AttackCategory.LOGIC_FLAW

                    try:
                        confidence = float(fd.get("confidence", 0.5))
                    except (ValueError, TypeError):
                        confidence = 0.5

                    finding = RedTeamFinding(
                        category=category,
                        severity=fd.get("severity", "medium"),
                        title=fd.get("title", "Untitled finding"),
                        description=fd.get("description", ""),
                        reproduction_steps=fd.get("reproduction_steps", []),
                        affected_code=fd.get("affected_code", "unknown"),
                        suggested_fix=fd.get("suggested_fix", ""),
                        confidence=confidence,
                    )
                    all_findings.append(finding)
                except Exception as e:
                    logger.warning(f"Skipping malformed finding: {e} — {fd}")

        # Generate aggregated report.md
        if red_team_dir.exists():
            try:
                BlindAgentRedTeam._generate_report(
                    findings=all_findings,
                    session_id=session_id,
                    agents_run=len(profiles),
                    red_team_dir=red_team_dir,
                )
            except Exception as e:
                logger.warning(f"Failed to generate Red Team report: {e}")

        return RedTeamResult(
            session_id=session_id,
            code_analyzed=f"workspace: {workspace_dir}",
            agent_model=f"{_resolve_red_team_llm_provider()}-red-team-orchestrator",
            timestamp=datetime.now().isoformat(),
            duration_ms=total_elapsed * 1000,
            findings=all_findings,
            attack_vectors_tried=list(dict.fromkeys(all_vectors)),
            success=any_success,
            error=any_error,
        )

    def _extract_json_from_response(self, text: str) -> Optional[dict]:
        """Extract a JSON findings block from agent response text."""
        import re

        if not text:
            return None

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # BA-M3: regex [^{}]* fails for nested JSON objects containing braces.
        # Use raw_decode to scan for the first valid JSON object in the text.
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(text):
            start = text.find('{', idx)
            if start == -1:
                break
            try:
                obj, _ = decoder.raw_decode(text, start)
                if isinstance(obj, dict) and "findings" in obj:
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass
            idx = start + 1

        return None


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def run_quick_blind_agent(
    workspace_dir: Path,
    mission_desc: str = "",
    n_agents: int = 2,
    timeout: int = 240,
    work_budget: Optional[int] = None,
    safety_timeout: Optional[float] = None,
) -> RedTeamResult:
    """
    Quick convenience wrapper: launch blind agents against a workspace.

    Args:
        workspace_dir: Directory containing the codebase to test
        mission_desc: Brief description of what the code does
        n_agents: Number of parallel agents (default 2 for speed)
        timeout: Backward-compat safety timeout hint in seconds
        work_budget: Target output tokens per agent (None = model default)
        safety_timeout: Per-agent kill timeout in seconds (None = model default)

    Returns:
        RedTeamResult with aggregated findings
    """
    orchestrator = RedTeamOrchestrator(
        timeout=timeout, n_agents=n_agents,
        work_budget=work_budget, safety_timeout=safety_timeout,
    )
    return orchestrator.launch(
        workspace_dir=Path(workspace_dir),
        mission_desc=mission_desc,
        n_agents=n_agents,
        work_budget=work_budget,
        safety_timeout=safety_timeout,
    )


# ---------------------------------------------------------------------------
# Standalone test entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RedTeamOrchestrator standalone runner")
    parser.add_argument("workspace_dir", help="Path to workspace to test")
    parser.add_argument("--desc", default="", help="Brief description of the code")
    parser.add_argument("--agents", type=int, default=2, help="Number of agents (1-4)")
    parser.add_argument("--timeout", type=int, default=240, help="Per-agent timeout (s)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print(f"RedTeamOrchestrator: launching {args.agents} agents against {args.workspace_dir}")
    result = run_quick_blind_agent(
        workspace_dir=Path(args.workspace_dir),
        mission_desc=args.desc or "Explore and determine from code",
        n_agents=args.agents,
        timeout=args.timeout,
    )

    print(f"\n=== Results ===")
    print(f"Session: {result.session_id}")
    print(f"Total findings: {result.total_issues}")
    print(f"Critical: {len(result.critical_findings)}")
    print(f"High: {len(result.high_findings)}")
    print(f"Duration: {result.duration_ms / 1000:.1f}s")

    if result.findings:
        print("\nFindings:")
        for f in result.findings:
            print(f"  [{f.severity.upper()}] {f.title or 'Unknown issue'} — {f.affected_code}")

    if result.error:
        print(f"\nErrors: {result.error}")
