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
import signal
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
# Allowed workspace roots — path traversal guard
# ---------------------------------------------------------------------------
_ALLOWED_WORKSPACE_ROOTS = [
    Path("/home/vader/AI-AtlasForge/workspace").resolve(),
    Path("/home/vader/AI-AtlasForge").resolve(),
    Path("/tmp").resolve(),
]

# ---------------------------------------------------------------------------
# Adaptive timeout tiers based on .py file count in workspace
# ---------------------------------------------------------------------------
_TIMEOUT_TIERS = [
    (50,    300),    # < 50 .py files  → 300 s
    (200,   600),    # 50–200 .py files → 600 s
    (999999, 900),   # > 200 .py files  → 900 s
]

# ---------------------------------------------------------------------------
# Parent directory on path so we can import AtlasForge top-level modules
# ---------------------------------------------------------------------------
_ATLASFORGE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ATLASFORGE_ROOT))

# ---------------------------------------------------------------------------
# Optional dashboard integration (agent_stream_manager)
# ---------------------------------------------------------------------------
try:
    from agent_stream_manager import (
        register_agent as _asm_register,
        update_agent_pid as _asm_update_pid,
        complete_agent as _asm_complete,
        stream_stdout_to_file as _asm_stream,
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
        index=1,
        categories=[AttackCategory.INJECTION, AttackCategory.ERROR_HANDLING],
        label="Red Team - Injection",
        focus_description=(
            "INJECTION VULNERABILITIES and ERROR HANDLING GAPS.\n"
            "Look for command injection (os.system, subprocess with user input), "
            "path traversal, SQL injection, format string injection.\n"
            "Also look for bare except clauses, swallowed exceptions, "
            "missing error propagation, functions that silently return None on failure."
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

_BLIND_AGENT_PROMPT = """\
You are a BLIND adversarial security researcher and code quality analyst.

You have NO prior knowledge of how this code was built, what the developer intended,
or what the original tests check. You only know the workspace path below.

Your mission: Explore the codebase at {workspace_dir}, understand what it does,
and then BREAK it. Find bugs, vulnerabilities, and logic flaws.

## Workspace
{workspace_dir}

## What this code is supposed to do
{mission_desc}

## Your attack focus for this session
{focus_description}

## Instructions

1. **Explore first** — use your file tools (Read, Glob, Grep) to understand the code
   structure. Look at the entry points, key modules, and existing tests.

2. **Run existing tests** — if a test suite exists, run it:
   ```
   cd {workspace_dir} && python3 -m pytest tests/ -v 2>&1 | head -100
   ```
   Or for other languages/frameworks, use the appropriate test command.

3. **Attack the code** — based on your focus area, try adversarial inputs:
   - Import key modules and call functions with malformed/edge-case inputs
   - Run scripts with adversarial arguments
   - Look for code that will crash, hang, or produce wrong output

4. **Document findings** — for each issue you find, note:
   - Which file and line (or function name)
   - What category of bug it is
   - How severe you assess it (critical / high / medium / low / info)
   - Steps to reproduce
   - A suggested fix

5. **Write your findings** to this JSON file:
   {results_file}

   Use this EXACT JSON structure:
   ```json
   {{
       "findings": [
           {{
               "category": "boundary",
               "severity": "high",
               "title": "Short descriptive title",
               "description": "Detailed description of the issue",
               "reproduction_steps": ["step 1", "step 2"],
               "affected_code": "filename.py:linenum or function_name",
               "suggested_fix": "What should be done to fix this",
               "confidence": 0.85
           }}
       ],
       "attack_vectors_tried": [
           "empty input to function X",
           "None passed to Y",
           "..."
       ]
   }}
   ```

6. If you find NO issues, write the JSON with an empty findings list and list what
   you tried in attack_vectors_tried.

Begin your investigation now. Be thorough. Be adversarial. Break things.
"""


# ---------------------------------------------------------------------------
# New Red Team Hunt Prompt — writes findings incrementally as discovered
# ---------------------------------------------------------------------------

_RED_TEAM_HUNT_PROMPT = """\
You are a Red Team bug hunter. Your ONLY job is to find bugs in the code at the
workspace path below. You do NOT fix bugs. You do NOT need to find solutions.
You just find everything that is wrong and write it down immediately.

## Workspace
{workspace_dir}

## What this code is supposed to do
{mission_desc}

## Your hunt focus for this session
{focus_description}

## FINDINGS FILE — write every finding here immediately when found
{findings_file}

## Instructions

1. **Explore first** — use Read, Glob, Grep to understand the code structure.
   Start with entry points, key modules, and existing tests.

2. **Run existing tests** to see what already fails:
   ```
   cd {workspace_dir} && python3 -m pytest tests/ -v 2>&1 | head -100
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

   **For a confirmed bug, write to {findings_file} using EXACTLY this format:**

   If the file is empty or does not exist yet, use the Write tool to create it:

   ```
   # Red Team Findings — {agent_id}

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
{results_file}

Use this EXACT JSON structure:
```json
{{
    "findings": [
        {{
            "category": "boundary",
            "severity": "high",
            "title": "Short descriptive title",
            "description": "Detailed description of the issue",
            "reproduction_steps": ["step 1", "step 2"],
            "affected_code": "filename.py:linenum or function_name",
            "suggested_fix": "What should be done to fix this",
            "confidence": 0.85
        }}
    ],
    "attack_vectors_tried": [
        "empty input to function X",
        "None passed to Y",
        "..."
    ]
}}
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

    def __init__(self, timeout: int = 900, n_agents: int = 3):
        """
        Args:
            timeout: Per-agent timeout in seconds (default 15 min)
            n_agents: Number of parallel agents to launch (1-4)
        """
        self.timeout = timeout
        self.n_agents = min(max(1, n_agents), len(_ATTACK_PROFILES))

    # ------------------------------------------------------------------
    # Fix 3 — Path Traversal Guard
    # ------------------------------------------------------------------
    def _validate_workspace_dir(self, path: Path) -> None:
        """Raise ValueError if path is not within any allowed workspace root."""
        for allowed_root in _ALLOWED_WORKSPACE_ROOTS:
            try:
                path.relative_to(allowed_root)
                return  # within an allowed root — OK
            except ValueError:
                continue
        raise ValueError(
            f"workspace_dir {path!r} is not within any allowed root: "
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
        except Exception:
            return self.timeout  # fall back to configured default

    def launch_parallel_team(
        self,
        workspace_dir: Path,
        mission_desc: str = "Unknown — explore and determine from code",
        n_agents: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> RedTeamResult:
        """
        Launch N parallel blind agents against the workspace.

        Args:
            workspace_dir: Path to the codebase to attack
            mission_desc: Brief description of what the codebase does
            n_agents: Override number of agents (uses self.n_agents if None)
            timeout: Override per-agent timeout in seconds

        Returns:
            Aggregated RedTeamResult with all findings from all agents
        """
        workspace_dir = Path(workspace_dir).resolve()
        # Fix 3 — path traversal guard
        self._validate_workspace_dir(workspace_dir)

        n = n_agents if n_agents is not None else self.n_agents
        n = min(max(1, n), len(_ATTACK_PROFILES))
        # Fix 7 — adaptive timeout
        if timeout is not None:
            t = timeout
        else:
            t = self._estimate_timeout(workspace_dir)
            logger.info(f"Adaptive timeout selected: {t}s")

        profiles = _ATTACK_PROFILES[:n]
        session_id = f"brt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        results_dir = workspace_dir / "tests"
        results_dir.mkdir(parents=True, exist_ok=True)

        # Create Red_Team directory for incremental markdown findings files
        red_team_dir = results_dir / "Red_Team"
        red_team_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"BlindAgentRedTeam: launching {n} agents against {workspace_dir} "
            f"(session={session_id}, timeout={t}s, red_team_dir={red_team_dir})"
        )

        # Run agents in parallel
        futures_map: Dict[Any, AttackProfile] = {}
        agent_results: List[Dict] = []
        _suite_start = time.time()  # wall-clock start for accurate duration_ms

        with ThreadPoolExecutor(max_workers=n) as executor:
            for profile in profiles:
                results_file = results_dir / f"red_team_agent_{profile.index}.json"
                findings_file = red_team_dir / f"agent{profile.index}_findings.md"
                # Fix 1 — TOCTOU: delete any stale results/findings files before spawning
                results_file.unlink(missing_ok=True)
                findings_file.unlink(missing_ok=True)
                # Pre-create empty findings file so agent can append immediately
                findings_file.touch()
                future = executor.submit(
                    self._spawn_single_agent,
                    profile=profile,
                    workspace_dir=workspace_dir,
                    mission_desc=mission_desc,
                    results_file=results_file,
                    findings_file=findings_file,
                    session_id=session_id,
                    timeout=t,
                )
                futures_map[future] = profile
                logger.info(f"Submitted agent {profile.index}: {profile.label}")

            # Fix 5 — zombie agents: catch outer TimeoutError, collect partial results
            import concurrent.futures as _cf
            try:
                for future in as_completed(futures_map, timeout=t + 30):
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
                        agent_results.append({
                            "success": False,
                            "error": str(e),
                            "findings": [],
                            "attack_vectors_tried": [],
                            "profile_index": profile.index,
                        })
            except _cf.TimeoutError:
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

        _suite_elapsed = time.time() - _suite_start
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
    ) -> Dict:
        """
        Spawn a single blind agent as a Claude CLI subprocess.

        Registers with agent_stream_manager (→ Mission Activity tab) if available.
        Waits for process to exit, then reads results_file.
        """
        import subprocess

        agent_id = f"{session_id}_agent{profile.index}"
        start_time = time.time()

        # Build prompt — escape braces in mission_desc and focus_description to prevent
        # format string KeyError when either contains literal { or } chars (Bug 5 fix).
        safe_mission_desc = mission_desc.replace('{', '{{').replace('}', '}}')
        safe_focus = profile.focus_description.replace('{', '{{').replace('}', '}}')
        prompt = _RED_TEAM_HUNT_PROMPT.format(
            workspace_dir=str(workspace_dir),
            mission_desc=safe_mission_desc,
            focus_description=safe_focus,
            results_file=str(results_file),
            findings_file=str(findings_file),
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

        # Build CLI command
        command = [
            "claude", "-p",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]

        # Clear CLAUDECODE to avoid nested-session error
        env = os.environ.copy()
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
                except Exception:
                    pass

            # Start streaming thread (initialized to None before outer try for leak-safe except)
            if streaming_enabled and stream_file:
                stream_thread = threading.Thread(
                    target=_asm_stream,
                    args=(proc, stream_file, agent_id),
                    daemon=True,
                    name=f"brt-stream-{agent_id}",
                )
                stream_thread.start()

            # Send prompt via stdin — Fix 6: abort agent on stdin write failure
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except Exception as e:
                logger.error(f"Failed to write prompt to agent {agent_id}: {e} — aborting agent")
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
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
                except Exception:
                    pass

            _use_drain_thread = bool(streaming_enabled and stream_file)
            if _use_drain_thread:
                _stderr_thread = threading.Thread(
                    target=_drain_stderr_bg, args=(proc.stderr,), daemon=True,
                    name=f"brt-stderr-{agent_id}",
                )
                _stderr_thread.start()

            stdout_text = ""
            try:
                if streaming_enabled and stream_file:
                    proc.wait(timeout=timeout)
                    # C3-6: join stream_thread before draining stdout — prevents concurrent readers
                    if stream_thread is not None:
                        stream_thread.join(timeout=5)
                    # Drain residual buffered stdout so pipe never fills and deadlocks
                    try:
                        proc.stdout.read()
                    except Exception:
                        pass
                else:
                    stdout_text, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"Agent {agent_id} timed out after {timeout}s, killing")
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
                # On timeout: read whatever was written to findings_file
                _md_content = ""
                try:
                    if findings_file.exists():
                        _md_content = findings_file.read_text(encoding="utf-8")
                except Exception:
                    pass
                _md_count = len(self._parse_markdown_findings(_md_content)) if _md_content.strip() else 0
                if _md_count > 0:
                    logger.info(f"Agent {agent_id} timed out — {_md_count} findings captured from markdown file")

            # Join stderr drain thread only if it was started (streaming path only)
            if _use_drain_thread:
                _stderr_thread.join(timeout=5)

            elapsed = time.time() - start_time

            # Mark complete in dashboard
            if streaming_enabled and stream_file:
                try:
                    time.sleep(0.3)  # Let streaming thread flush
                    _asm_complete(agent_id, error=None)
                except Exception:
                    pass

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Agent {agent_id} subprocess failed: {e}")
            # Join any threads that may have been started before the exception
            if stream_thread is not None:
                stream_thread.join(timeout=5)
            if _use_drain_thread:
                _stderr_thread.join(timeout=5)
            if streaming_enabled and stream_file:
                try:
                    _asm_complete(agent_id, error=str(e))
                except Exception:
                    pass
            return {
                "success": False,
                "error": str(e),
                "findings": [],
                "attack_vectors_tried": [],
                "profile_index": profile.index,
                "elapsed_seconds": elapsed,
            }
        finally:
            # Fix 5b — zombie cleanup: ensure process is dead regardless of exit path
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except Exception:
                        pass

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

        if results_file.exists():
            try:
                data = json.loads(results_file.read_text())
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
        # BA-H10: empty findings [] must not be counted as agent success.
        # Success requires either partial_success (markdown findings present) OR
        # the JSON results file parsed cleanly AND contained at least one finding.
        json_reported_findings = parse_error is None and len(findings_raw) > 0
        success = partial_success or json_reported_findings

        return {
            "success": success,
            "partial_success": partial_success,
            "error": parse_error if not partial_success else None,
            "findings": combined_findings,
            "attack_vectors_tried": attack_vectors_tried,
            "profile_index": profile.index,
            "elapsed_seconds": elapsed,
            "findings_file": str(findings_file),
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
                any_error = agent_data.get("error", "agent failed")

            all_vectors.extend(agent_data.get("attack_vectors_tried", []))

            # Always read findings_file to capture incremental markdown findings,
            # regardless of whether the agent completed or timed out (Bug 1 fix).
            extra_md_findings: List[dict] = []
            findings_file_path = agent_data.get("findings_file")
            if findings_file_path:
                try:
                    fpath = Path(findings_file_path)
                    if fpath.exists():
                        content = fpath.read_text(encoding="utf-8")
                        if content.strip():
                            extra_md_findings = self._parse_markdown_findings(content)
                except Exception:
                    pass

            # Merge JSON findings + markdown findings; deduplicate by (title, affected_code)
            # so findings present in both sources are not double-counted (Bug 3 fix).
            json_findings = agent_data.get("findings", [])
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
        return RedTeamResult(
            session_id=session_id,
            code_analyzed=f"workspace: {workspace_path}",
            agent_model="claude-blind-agent-team",
            timestamp=datetime.now().isoformat(),
            duration_ms=total_elapsed * 1000,
            findings=all_findings,
            attack_vectors_tried=list(dict.fromkeys(all_vectors)),  # deduplicate
            success=any_success,
            error=any_error,
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

        def _field(name: str, text: str) -> str:
            # Bug 2 fix: use [\s\S]+? with lookahead that only stops at field-name patterns
            # (e.g. "File:", "Severity:") — not at every newline+word-char.
            m = re.search(rf'^{name}:\s*([\s\S]+?)(?=\n[A-Za-z][\w]*:|\Z)', text, re.MULTILINE)
            return m.group(1).strip() if m else ""

        results: List[dict] = []

        for raw in re.findall(r'---BUG---\s*(.*?)\s*---END BUG---', content, re.DOTALL):
            try:
                file_val = _field("File", raw)
                line_val = _field("Line", raw)
                sev_raw = (_field("Severity", raw).upper().split() or ["HIGH"])[0]
                type_val = _field("Type", raw) or "logic_error"
                desc_val = _field("Description", raw)
                repro_val = _field("Reproduction", raw)
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

        for raw in re.findall(r'---SUSPECTED---\s*(.*?)\s*---END SUSPECTED---', content, re.DOTALL):
            try:
                file_val = _field("File", raw)
                line_val = _field("Line", raw)
                desc_val = _field("Description", raw)
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

        suspected = [f for f in findings if getattr(f, 'title', '').startswith('[SUSPECTED]')]
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
) -> str:
    """
    Shared prompt builder used by both BlindAgentRedTeam and RedTeamOrchestrator.

    Escaped format-string injection in mission_desc is handled here.
    """
    safe_mission_desc = mission_desc.replace('{', '{{').replace('}', '}}')
    safe_focus = profile.focus_description.replace('{', '{{').replace('}', '}}')
    return _BLIND_AGENT_PROMPT.format(
        workspace_dir=str(workspace_dir),
        mission_desc=safe_mission_desc,
        focus_description=safe_focus,
        results_file=str(results_file),
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

    def __init__(self, timeout: int = 900, n_agents: int = 4):
        """
        Args:
            timeout: Per-agent timeout in seconds (default 15 min)
            n_agents: Number of parallel attack agents (1-6)
        """
        self.timeout = timeout
        self.n_agents = min(max(1, n_agents), len(_EXTENDED_ATTACK_PROFILES))

    def _validate_workspace_dir(self, path: Path) -> None:
        """Raise ValueError if path is not within any allowed workspace root."""
        for allowed_root in _ALLOWED_WORKSPACE_ROOTS:
            try:
                path.relative_to(allowed_root)
                return
            except ValueError:
                continue
        raise ValueError(
            f"workspace_dir {path!r} is not within any allowed root: "
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
            results_file.unlink(missing_ok=True)   # TOCTOU: clear stale JSON
            findings_file.unlink(missing_ok=True)  # TOCTOU: clear stale markdown
            findings_file.touch()                  # pre-create so agent can append

            agent_id = f"rto_agent{profile.index}"
            safe_mission_desc = mission_desc.replace('{', '{{').replace('}', '}}')
            safe_focus = profile.focus_description.replace('{', '{{').replace('}', '}}')
            prompt = _RED_TEAM_HUNT_PROMPT.format(
                workspace_dir=str(workspace_dir),
                mission_desc=safe_mission_desc,
                focus_description=safe_focus,
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
                    "profile_index": profile.index,
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
    ) -> RedTeamResult:
        """
        Launch N parallel blind agents against the workspace via HierarchicalExperiment.

        Args:
            workspace_dir: Path to the codebase to attack
            mission_desc: Brief description of what the codebase does
            n_agents: Number of parallel agents (overrides self.n_agents)
            timeout: Per-agent timeout in seconds (overrides self.timeout)

        Returns:
            Aggregated RedTeamResult with findings from all completed agents.
            Partial results are returned if some agents time out.
        """
        workspace_dir = Path(workspace_dir).resolve()
        self._validate_workspace_dir(workspace_dir)

        n = min(max(1, n_agents if n_agents is not None else self.n_agents),
                len(_EXTENDED_ATTACK_PROFILES))
        t = timeout if timeout is not None else self.timeout

        profiles = _EXTENDED_ATTACK_PROFILES[:n]
        session_id = f"rto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        results_dir = workspace_dir / "tests"
        results_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"RedTeamOrchestrator: launching {n} agents against {workspace_dir} "
            f"(session={session_id}, per-agent-timeout={t}s)"
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
            team = BlindAgentRedTeam(timeout=t, n_agents=n)
            return team.launch_parallel_team(
                workspace_dir=workspace_dir,
                mission_desc=mission_desc,
                n_agents=n,
                timeout=t,
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
            for prof in profiles:
                # BA-M2: use exact word-boundary match, not substring.
                # Substring "1" in "agent10" would incorrectly match profile 1.
                import re as _re
                if _re.search(r'(?<!\d)' + str(prof.index) + r'(?!\d)', agent_id):
                    profile_index = prof.index
                    break

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
                        data = json.loads(results_file.read_text())
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
            if (status == 'completed' or found) and findings_raw:
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
            agent_model="claude-red-team-orchestrator",
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

        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
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
) -> RedTeamResult:
    """
    Quick convenience wrapper: launch blind agents against a workspace.

    Args:
        workspace_dir: Directory containing the codebase to test
        mission_desc: Brief description of what the code does
        n_agents: Number of parallel agents (default 2 for speed)
        timeout: Per-agent timeout in seconds

    Returns:
        RedTeamResult with aggregated findings
    """
    orchestrator = RedTeamOrchestrator(timeout=timeout, n_agents=n_agents)
    return orchestrator.launch(
        workspace_dir=Path(workspace_dir),
        mission_desc=mission_desc,
        n_agents=n_agents,
        timeout=timeout,
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
