#!/usr/bin/env python3
"""
Hierarchical Experiment Framework for Multi-Agent Claude Execution

This framework supports parallel spawning of multiple Claude agents that:
1. Appear as tabs in the Mission Activity Panel (dashboard-integrated)
2. Stream JSONL output in real-time to the dashboard
3. Respect SubagentPoolManager slot budgets
4. Use stage-appropriate tool restrictions via InitGuard
5. Aggregate results back to the calling conductor

Architecture:
    HierarchicalExperiment
        ├── WorkUnit Agent 1  → registered in AgentStreamManager → visible in Mission Activity Panel
        ├── WorkUnit Agent 2  → registered in AgentStreamManager → visible in Mission Activity Panel
        └── ...up to max_agents parallel

Key differences from original:
- Uses subprocess.Popen + stream_stdout_to_file instead of invoke_fresh_claude()
- Registers each agent with agent_stream_manager before spawn
- Updates PID after spawn; marks complete after process exits
- Integrates with SubagentPoolManager to respect global slot budget
- HierarchicalConfig gains 'stage' and 'enable_streaming' fields
"""

import json
import subprocess
import threading
import time
import os
import signal
import logging
import concurrent.futures
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid

# Supporting modules (always required)
from checkpoint_manager import CheckpointManager, CheckpointStatus
from timeout_budget import TimeoutBudget, TimeoutPresets, TimeoutPolicy
from mission_splitter import MissionSplitter, WorkUnit, SplitStrategy

# Base paths
from atlasforge_config import BASE_DIR
EXPERIMENTS_DIR = BASE_DIR / "experiments"
HIERARCHICAL_RESULTS_DIR = EXPERIMENTS_DIR / "hierarchical_results"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"

HIERARCHICAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINTS_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("hierarchical_framework")

# =============================================================================
# OPTIONAL DEPENDENCY GUARDS — dashboard integration is non-critical
# =============================================================================

try:
    from agent_stream_manager import (
        register_agent as _asm_register,
        update_agent_pid as _asm_update_pid,
        complete_agent as _asm_complete,
        stream_stdout_to_file as _asm_stream,
        reconstruct_text_from_stream_file as _asm_reconstruct,
    )
    HAS_AGENT_STREAM_MANAGER = True
except ImportError:
    HAS_AGENT_STREAM_MANAGER = False
    logger.warning("agent_stream_manager not available — parallel agents won't appear in dashboard")

try:
    from init_guard import InitGuard
    HAS_INIT_GUARD = True
except ImportError:
    HAS_INIT_GUARD = False

try:
    from subagent_pool_manager import get_pool_manager as _get_pool_mgr
    HAS_POOL_MANAGER = True
except ImportError:
    HAS_POOL_MANAGER = False

# Legacy experiment_framework import (kept for SubagentSpawner backward compat)
try:
    from experiment_framework import ModelType, invoke_fresh_claude
    HAS_EXPERIMENT_FRAMEWORK = True
except ImportError:
    HAS_EXPERIMENT_FRAMEWORK = False
    # Minimal stub
    class ModelType(Enum):  # type: ignore
        CLAUDE_SONNET = "claude-sonnet-4-6"
        CLAUDE_HAIKU = "claude-haiku-4-5-20251001"
        CLAUDE_OPUS = "claude-opus-4-6"


# =============================================================================
# DATA CLASSES
# =============================================================================

class AgentRole(Enum):
    """Roles for agents in the hierarchy."""
    ORCHESTRATOR = "orchestrator"
    WORKER = "worker"
    SUBAGENT = "subagent"


@dataclass
class HierarchicalConfig:
    """Configuration for hierarchical experiments."""
    mission_id: str
    description: str = ""
    total_timeout: int = 3600           # 60 minutes default
    max_agents: int = 5                  # Max parallel workers
    max_subagents_per_agent: int = 10    # Max subagents per worker
    model: ModelType = ModelType.CLAUDE_SONNET
    subagent_model: ModelType = ModelType.CLAUDE_HAIKU
    timeout_reserve_ratio: float = 0.10
    poll_interval: float = 5.0
    system_prompt: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    # NEW: dashboard integration fields
    stage: str = "BUILDING"              # R&D stage for tool restrictions
    enable_streaming: bool = True        # Register agents with dashboard

    def to_dict(self) -> dict:
        d = asdict(self)
        d['model'] = self.model.value
        d['subagent_model'] = self.subagent_model.value
        return d


@dataclass
class AgentResult:
    """Result from a single agent execution."""
    agent_id: str
    role: AgentRole
    status: str  # completed, failed, timeout
    response: str
    parsed_result: Optional[Dict[str, Any]]
    elapsed_seconds: float
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    error: Optional[str] = None
    subagent_results: List['AgentResult'] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['role'] = self.role.value
        d['subagent_results'] = [s.to_dict() for s in self.subagent_results]
        return d


@dataclass
class HierarchicalResults:
    """Complete results from a hierarchical experiment."""
    config: HierarchicalConfig
    work_units: List[WorkUnit]
    agent_results: List[AgentResult]
    aggregated_result: Dict[str, Any]
    started_at: str
    completed_at: str
    total_elapsed_seconds: float

    def save(self, filepath: Optional[Path] = None) -> Path:
        if filepath is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{self.config.mission_id}_{timestamp}.json"
            filepath = HIERARCHICAL_RESULTS_DIR / filename

        data = {
            "config": self.config.to_dict(),
            "work_units": [wu.to_dict() for wu in self.work_units],
            "agent_results": [ar.to_dict() for ar in self.agent_results],
            "aggregated_result": self.aggregated_result,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_elapsed_seconds": self.total_elapsed_seconds
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Results saved to: {filepath}")
        return filepath

    def get_summary(self) -> Dict[str, Any]:
        completed = sum(1 for ar in self.agent_results if ar.status == "completed")
        failed = sum(1 for ar in self.agent_results if ar.status == "failed")
        timeout = sum(1 for ar in self.agent_results if ar.status == "timeout")

        return {
            "mission_id": self.config.mission_id,
            "total_agents": len(self.agent_results),
            "completed": completed,
            "failed": failed,
            "timeout": timeout,
            "total_elapsed_seconds": self.total_elapsed_seconds,
            "total_files_created": sum(len(ar.files_created) for ar in self.agent_results),
            "total_files_modified": sum(len(ar.files_modified) for ar in self.agent_results)
        }


# =============================================================================
# THREAD-SAFE AGENT ID COUNTER (mirrors conductor's _next_mission_agent_id)
# =============================================================================

_parallel_agent_counter = 0
_parallel_agent_counter_lock = threading.Lock()


def _next_parallel_agent_id(work_unit_id: str, stage: str) -> tuple:
    """Return (agent_id, label) for a new parallel agent."""
    global _parallel_agent_counter
    with _parallel_agent_counter_lock:
        _parallel_agent_counter += 1
        n = _parallel_agent_counter
    agent_id = f"par_{uuid.uuid4().hex[:8]}"
    stage_short = (stage or 'BUILDING').upper()[:6]
    label = f"[PAR] {stage_short} {work_unit_id[:12]} #{n}"
    return agent_id, label


# =============================================================================
# MAIN EXPERIMENT CLASS
# =============================================================================

class HierarchicalExperiment:
    """
    Manages hierarchical multi-agent experiments with full dashboard integration.

    Each work unit is executed as a real Claude CLI subprocess registered with
    the AgentStreamManager so it appears as a tab in the Mission Activity Panel.

    Usage:
        config = HierarchicalConfig(
            mission_id="my_mission",
            stage="BUILDING",
            total_timeout=3600,
            max_agents=3,
            enable_streaming=True
        )
        exp = HierarchicalExperiment(config)
        splitter = MissionSplitter()
        work_units = splitter.split(prompt, max_units=3)
        results = exp.run(work_units, progress_callback=print)
    """

    def __init__(self, config: HierarchicalConfig):
        self.config = config
        self.checkpoint_mgr = CheckpointManager(config.mission_id)
        self.timeout_budget = TimeoutBudget(
            total_seconds=config.total_timeout,
            reserve_ratio=config.timeout_reserve_ratio,
            policy=TimeoutPolicy.PARALLEL
        )
        self.started_at: Optional[str] = None
        self.agent_results: List[AgentResult] = []

    def run(
        self,
        work_units: List[WorkUnit],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> HierarchicalResults:
        """
        Execute the hierarchical experiment.

        Args:
            work_units: List of WorkUnit objects to execute in parallel
            progress_callback: Optional function called with status updates

        Returns:
            HierarchicalResults containing all agent results
        """
        self.started_at = datetime.now().isoformat()
        start_time = time.time()

        if progress_callback:
            progress_callback(f"[HierarchicalFramework] Starting experiment: {self.config.mission_id}")
            progress_callback(f"[HierarchicalFramework] Work units: {len(work_units)}, Timeout: {self.config.total_timeout}s")

        # Allocate timeout budget to work units
        agent_ids = [wu.id for wu in work_units]
        self.timeout_budget.allocate_children(agent_ids)

        # Request pool slots if pool manager is available
        n_slots = len(work_units)
        pool = None
        if HAS_POOL_MANAGER:
            try:
                pool = _get_pool_mgr()
                slot_req = pool.request_slots(
                    self.config.mission_id,
                    count=len(work_units),
                    priority=10,
                    query=self.config.description or self.config.mission_id
                )
                n_slots = min(len(work_units), getattr(slot_req, 'granted', len(work_units)))
                if progress_callback:
                    progress_callback(f"[HierarchicalFramework] Pool granted {n_slots}/{len(work_units)} slots")
            except Exception as e:
                logger.warning(f"Pool manager error (non-fatal): {e}")
                pool = None
                n_slots = len(work_units)

        # Run work units in parallel (limited to n_slots)
        units_to_run = work_units[:n_slots]
        self.agent_results = self._run_parallel_agents(units_to_run, progress_callback)

        # Aggregate results
        aggregated = self._aggregate_results(self.agent_results)

        completed_at = datetime.now().isoformat()
        total_elapsed = time.time() - start_time

        # Release pool slots
        if pool is not None:
            try:
                all_success = all(ar.status == "completed" for ar in self.agent_results)
                pool.notify_investigation_complete(
                    self.config.mission_id,
                    elapsed_sec=total_elapsed,
                    success=all_success
                )
            except Exception as e:
                logger.debug(f"Pool release error (non-fatal): {e}")

        if progress_callback:
            progress_callback(f"[HierarchicalFramework] Complete. Elapsed: {total_elapsed:.1f}s")

        return HierarchicalResults(
            config=self.config,
            work_units=work_units,
            agent_results=self.agent_results,
            aggregated_result=aggregated,
            started_at=self.started_at,
            completed_at=completed_at,
            total_elapsed_seconds=total_elapsed
        )

    def _run_parallel_agents(
        self,
        work_units: List[WorkUnit],
        progress_callback: Optional[Callable[[str], None]]
    ) -> List[AgentResult]:
        """Run multiple agents in parallel using ThreadPoolExecutor."""
        results = []
        actual_workers = min(len(work_units), self.config.max_agents)

        with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as executor:
            future_to_wu = {
                executor.submit(self._run_single_agent, wu): wu
                for wu in work_units
            }

            for future in concurrent.futures.as_completed(future_to_wu):
                wu = future_to_wu[future]
                try:
                    result = future.result()
                    results.append(result)
                    if progress_callback:
                        progress_callback(
                            f"[HierarchicalFramework] Agent {wu.id} finished: {result.status}"
                        )
                except Exception as e:
                    logger.error(f"Agent {wu.id} threw exception: {e}")
                    results.append(AgentResult(
                        agent_id=wu.id,
                        role=AgentRole.WORKER,
                        status="failed",
                        response="",
                        parsed_result=None,
                        elapsed_seconds=0.0,
                        error=str(e)
                    ))

        return results

    def _run_single_agent(self, work_unit: WorkUnit) -> AgentResult:
        """
        Execute a single work unit agent via Claude CLI subprocess.

        This is the core improvement over the original implementation:
        - Registers with AgentStreamManager -> appears in Mission Activity Panel
        - Spawns real subprocess with stream-json output
        - Streams JSONL in real-time to dashboard
        - Marks complete when subprocess exits
        """
        wu_id = work_unit.id
        start_time = time.time()

        # Create checkpoint
        self.checkpoint_mgr.create_checkpoint(wu_id, CheckpointStatus.IN_PROGRESS)
        self.timeout_budget.start_agent(wu_id)
        timeout = self.timeout_budget.get_timeout_for_cli(wu_id)

        # Build the prompt with subagent capability
        prompt = self._build_agent_prompt(work_unit)

        # Dashboard agent registration
        agent_id, agent_label = _next_parallel_agent_id(wu_id, self.config.stage)
        stream_file = None
        streaming_enabled = self.config.enable_streaming and HAS_AGENT_STREAM_MANAGER

        if streaming_enabled:
            try:
                stream_file = _asm_register('mission', agent_id, agent_label, pid=0)
                logger.info(f"Registered parallel agent {agent_id} ({agent_label})")
            except Exception as e:
                logger.warning(f"Failed to register agent {agent_id} in dashboard: {e}")
                stream_file = None
                streaming_enabled = False

        # Build Claude CLI command with stage-appropriate tool restrictions
        try:
            if HAS_INIT_GUARD:
                disallowed = InitGuard.get_disallowed_tools_for_cli(self.config.stage)
            else:
                disallowed = "NotebookEdit"  # Safe minimal default

            command = [
                "claude", "-p",
                "--dangerously-skip-permissions",
                "--disallowedTools", disallowed,
                "--output-format", "stream-json",
                "--verbose",
            ]
        except Exception:
            command = [
                "claude", "-p",
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--verbose",
            ]

        # Prepare environment — must clear CLAUDECODE to avoid nested session error
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True,
            )

            # Update PID in dashboard
            if streaming_enabled and stream_file:
                try:
                    _asm_update_pid(agent_id, proc.pid)
                except Exception:
                    pass

            # Start streaming thread (daemon — reads stdout, writes to stream_file)
            if streaming_enabled and stream_file:
                stream_thread = threading.Thread(
                    target=_asm_stream,
                    args=(proc, stream_file, agent_id),
                    daemon=True,
                    name=f"hf-stream-{agent_id}",
                )
                stream_thread.start()

            # Write prompt to stdin
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except Exception:
                pass

            # Wait for process
            stdout_text = ""
            try:
                if streaming_enabled and stream_file:
                    # Streaming thread handles stdout; just wait for process
                    proc.wait(timeout=timeout)
                    proc.stderr.read() if proc.stderr else ""
                else:
                    stdout_text, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
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
                elapsed = time.time() - start_time
                self.checkpoint_mgr.mark_failed(wu_id, f"timeout:{timeout}s")
                if streaming_enabled and stream_file:
                    try:
                        _asm_complete(agent_id, error=f"timeout:{timeout}s")
                    except Exception:
                        pass
                return AgentResult(
                    agent_id=wu_id,
                    role=AgentRole.WORKER,
                    status="timeout",
                    response="",
                    parsed_result=None,
                    elapsed_seconds=elapsed,
                    error=f"timeout after {timeout}s"
                )

            elapsed = time.time() - start_time
            self.timeout_budget.complete_agent(wu_id)

            # Reconstruct response from JSONL stream file
            response = ""
            if streaming_enabled and stream_file:
                try:
                    # Give streaming thread brief time to flush
                    time.sleep(0.5)
                    response = _asm_reconstruct(stream_file, provider='claude')
                except Exception as e:
                    logger.warning(f"Failed to reconstruct response for {agent_id}: {e}")
                    response = ""
                # Mark agent complete in dashboard
                try:
                    _asm_complete(agent_id, error=None)
                except Exception:
                    pass
            else:
                response = stdout_text or ""

            # Parse JSON result
            parsed = self._parse_response(response)

            # Determine status
            if proc.returncode != 0:
                status = "failed"
            elif parsed and parsed.get("status") in ("failed", "build_blocked"):
                status = "failed"
            else:
                status = "completed"

            self.checkpoint_mgr.mark_completed(wu_id, {
                "status": status,
                "response_snippet": response[:300],
                "elapsed": elapsed,
            })

            return AgentResult(
                agent_id=wu_id,
                role=AgentRole.WORKER,
                status=status,
                response=response,
                parsed_result=parsed,
                elapsed_seconds=elapsed,
                files_created=parsed.get("files_created", []) if parsed else [],
                files_modified=parsed.get("files_modified", []) if parsed else [],
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Agent {wu_id} subprocess error: {e}")
            self.checkpoint_mgr.mark_failed(wu_id, str(e))
            if streaming_enabled and stream_file:
                try:
                    _asm_complete(agent_id, error=str(e))
                except Exception:
                    pass
            return AgentResult(
                agent_id=wu_id,
                role=AgentRole.WORKER,
                status="failed",
                response="",
                parsed_result=None,
                elapsed_seconds=elapsed,
                error=str(e)
            )

    def _build_agent_prompt(self, work_unit: WorkUnit) -> str:
        """Build the full prompt for an agent.

        If the work unit's prompt was produced by MasterBuilder (contains
        '# Project Context'), it is already an isolated prompt — only append
        the minimal completion requirements, not the full mission context.
        """
        base_prompt = work_unit.prompt

        completion_block = """
# Completion Requirements

When complete, respond with a JSON object containing AT MINIMUM:
- "status": "build_complete" | "build_blocked" | "build_in_progress"
- "summary": Brief description of what was accomplished
- "files_created": List of files you created
- "files_modified": List of files you modified

"""

        # If this is an isolated worker prompt from MasterBuilder, don't inject
        # full mission context — it would re-introduce the Bug #2 problem.
        if "# Project Context" in base_prompt:
            return base_prompt + completion_block

        # Legacy behavior: add full parallel execution context for non-MasterBuilder units
        subagent_instructions = f"""

# Parallel Execution Context

You are one of {self.config.max_agents} parallel agents working on: {self.config.description}

Your assigned work unit: {work_unit.description}
{completion_block}"""
        return base_prompt + subagent_instructions

    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON response from agent."""
        if not response:
            return None

        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
        except (json.JSONDecodeError, Exception):
            pass

        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, Exception):
            pass

        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                parsed = json.loads(response[start:end])
                if isinstance(parsed, dict):
                    return parsed
        except (json.JSONDecodeError, Exception):
            pass

        return None

    def _aggregate_results(self, results: List[AgentResult]) -> Dict[str, Any]:
        """Aggregate results from all agents."""
        aggregated = {
            "total_agents": len(results),
            "completed": 0,
            "failed": 0,
            "timeout": 0,
            "all_files_created": [],
            "all_files_modified": [],
            "summaries": [],
            "errors": [],
        }

        for result in results:
            if result.status == "completed":
                aggregated["completed"] += 1
            elif result.status == "failed":
                aggregated["failed"] += 1
            elif result.status == "timeout":
                aggregated["timeout"] += 1

            aggregated["all_files_created"].extend(result.files_created)
            aggregated["all_files_modified"].extend(result.files_modified)

            if result.parsed_result and "summary" in result.parsed_result:
                aggregated["summaries"].append({
                    "agent": result.agent_id,
                    "summary": result.parsed_result["summary"]
                })

            if result.error:
                aggregated["errors"].append({
                    "agent": result.agent_id,
                    "error": result.error
                })

        aggregated["all_files_created"] = list(set(aggregated["all_files_created"]))
        aggregated["all_files_modified"] = list(set(aggregated["all_files_modified"]))

        return aggregated

    def wait_for_completion(
        self,
        agent_ids: List[str],
        timeout: Optional[int] = None
    ) -> bool:
        """Wait for all specified agents to complete."""
        if timeout is None:
            timeout = int(self.timeout_budget.remaining)
        return self.checkpoint_mgr.wait_for_all(
            agent_ids,
            timeout=timeout,
            poll_interval=self.config.poll_interval
        )


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def run_parallel_mission(
    mission: str,
    max_agents: int = 5,
    timeout_minutes: int = 60,
    stage: str = "BUILDING",
    progress_callback: Optional[Callable[[str], None]] = None
) -> HierarchicalResults:
    """
    Convenience function to run a mission in parallel with full dashboard integration.

    Args:
        mission: The mission text / prompt
        max_agents: Maximum parallel agents
        timeout_minutes: Total timeout in minutes
        stage: R&D stage for tool restrictions
        progress_callback: Optional progress callback

    Returns:
        HierarchicalResults
    """
    mission_id = f"mission_{uuid.uuid4().hex[:8]}"
    splitter = MissionSplitter()

    # Bug #1 Fix: derive agent count from mission complexity instead of always using max_agents
    n_agents = splitter.recommend_agent_count(mission, max_agents)

    config = HierarchicalConfig(
        mission_id=mission_id,
        description=mission[:100],
        total_timeout=timeout_minutes * 60,
        max_agents=n_agents,
        stage=stage,
        enable_streaming=True,
    )

    if n_agents == 1:
        # Simple mission — skip splitting overhead, run as single unit
        work_units = [splitter._create_single_unit(mission, context=None)]
    else:
        work_units = splitter.split(mission, max_units=n_agents)

    if progress_callback:
        progress_callback(
            f"[run_parallel_mission] Complexity → {n_agents} agents "
            f"(max={max_agents}), split into {len(work_units)} work units"
        )

    exp = HierarchicalExperiment(config)
    return exp.run(work_units, progress_callback)


# =============================================================================
# SUBAGENT SPAWNER — for agents that need to spawn sub-agents of their own
# =============================================================================

class SubagentSpawner:
    """
    Helper class for agents to spawn subagents.

    Usage within an agent's execution:
        spawner = SubagentSpawner(
            parent_id="agent_1",
            mission_id="my_mission",
            max_subagents=10,
            timeout_per_subagent=180
        )
        subagent_ids = spawner.spawn([
            {"id": "sub_1", "prompt": "Do task 1"},
            {"id": "sub_2", "prompt": "Do task 2"},
        ])
        spawner.wait_for_all()
        results = spawner.get_results()
    """

    def __init__(
        self,
        parent_id: str,
        mission_id: str,
        max_subagents: int = 10,
        timeout_per_subagent: int = 180,
        model: ModelType = ModelType.CLAUDE_HAIKU
    ):
        self.parent_id = parent_id
        self.mission_id = f"{mission_id}_sub_{parent_id}"
        self.max_subagents = max_subagents
        self.timeout_per_subagent = timeout_per_subagent
        self.model = model
        self.checkpoint_mgr = CheckpointManager(self.mission_id)
        self.subagent_ids: List[str] = []
        self.results: Dict[str, AgentResult] = {}

    def spawn(
        self,
        tasks: List[Dict[str, str]],
        parallel: bool = True
    ) -> List[str]:
        """Spawn subagents for the given tasks."""
        tasks = tasks[:self.max_subagents]
        if parallel:
            return self._spawn_parallel(tasks)
        else:
            return self._spawn_sequential(tasks)

    def _spawn_parallel(self, tasks: List[Dict[str, str]]) -> List[str]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {}
            for task in tasks:
                subagent_id = f"{self.parent_id}_{task['id']}"
                self.subagent_ids.append(subagent_id)
                self.checkpoint_mgr.create_checkpoint(subagent_id, CheckpointStatus.IN_PROGRESS)
                futures[executor.submit(
                    self._run_subagent, subagent_id, task['prompt']
                )] = subagent_id

            for future in concurrent.futures.as_completed(futures):
                subagent_id = futures[future]
                try:
                    self.results[subagent_id] = future.result()
                except Exception as e:
                    logger.error(f"Subagent {subagent_id} failed: {e}")
                    self.results[subagent_id] = AgentResult(
                        agent_id=subagent_id,
                        role=AgentRole.SUBAGENT,
                        status="failed",
                        response="",
                        parsed_result=None,
                        elapsed_seconds=0.0,
                        error=str(e)
                    )
        return self.subagent_ids

    def _spawn_sequential(self, tasks: List[Dict[str, str]]) -> List[str]:
        for task in tasks:
            subagent_id = f"{self.parent_id}_{task['id']}"
            self.subagent_ids.append(subagent_id)
            self.checkpoint_mgr.create_checkpoint(subagent_id, CheckpointStatus.IN_PROGRESS)
            try:
                self.results[subagent_id] = self._run_subagent(subagent_id, task['prompt'])
            except Exception as e:
                logger.error(f"Subagent {subagent_id} failed: {e}")
                self.results[subagent_id] = AgentResult(
                    agent_id=subagent_id,
                    role=AgentRole.SUBAGENT,
                    status="failed",
                    response="",
                    parsed_result=None,
                    elapsed_seconds=0.0,
                    error=str(e)
                )
        return self.subagent_ids

    def _run_subagent(self, subagent_id: str, prompt: str) -> AgentResult:
        """Run a single subagent via subprocess."""
        start_time = time.time()

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        command = [
            "claude", "-p",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
        ]

        stream_file = None
        agent_id = subagent_id
        if HAS_AGENT_STREAM_MANAGER:
            try:
                agent_id, label = _next_parallel_agent_id(subagent_id, "SUBAGENT")
                stream_file = _asm_register('mission', agent_id, label, pid=0)
            except Exception:
                stream_file = None
                agent_id = subagent_id

        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True,
            )

            if stream_file and HAS_AGENT_STREAM_MANAGER:
                try:
                    _asm_update_pid(agent_id, proc.pid)
                    t = threading.Thread(
                        target=_asm_stream,
                        args=(proc, stream_file, agent_id),
                        daemon=True,
                    )
                    t.start()
                except Exception:
                    pass

            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except Exception:
                pass

            stdout_text = ""
            try:
                if stream_file:
                    proc.wait(timeout=self.timeout_per_subagent)
                else:
                    stdout_text, _ = proc.communicate(timeout=self.timeout_per_subagent)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=5)
                except Exception:
                    pass
                elapsed = time.time() - start_time
                self.checkpoint_mgr.update_checkpoint(
                    subagent_id,
                    status=CheckpointStatus.FAILED,
                    result={"error": "timeout"}
                )
                return AgentResult(
                    agent_id=subagent_id,
                    role=AgentRole.SUBAGENT,
                    status="timeout",
                    response="",
                    parsed_result=None,
                    elapsed_seconds=elapsed,
                    error="timeout"
                )

            elapsed = time.time() - start_time
            response = ""
            if stream_file and HAS_AGENT_STREAM_MANAGER:
                try:
                    time.sleep(0.3)
                    response = _asm_reconstruct(stream_file, provider='claude')
                    _asm_complete(agent_id, error=None)
                except Exception:
                    pass
            else:
                response = stdout_text or ""

            status = "completed" if proc.returncode == 0 else "failed"
            self.checkpoint_mgr.update_checkpoint(
                subagent_id,
                status=CheckpointStatus.COMPLETED if status == "completed" else CheckpointStatus.FAILED,
                result={"response": response[:300]}
            )

            return AgentResult(
                agent_id=subagent_id,
                role=AgentRole.SUBAGENT,
                status=status,
                response=response,
                parsed_result=None,
                elapsed_seconds=elapsed,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            return AgentResult(
                agent_id=subagent_id,
                role=AgentRole.SUBAGENT,
                status="failed",
                response="",
                parsed_result=None,
                elapsed_seconds=elapsed,
                error=str(e)
            )

    def wait_for_all(self, timeout: int = 600) -> bool:
        return self.checkpoint_mgr.wait_for_all(self.subagent_ids, timeout=timeout)

    def get_results(self) -> Dict[str, AgentResult]:
        return self.results


# =============================================================================
# MASTER BUILDER — wave-based parallel execution orchestrator (Bug #2 fix)
# =============================================================================

class MasterBuilder:
    """
    Orchestrates wave-based parallel execution during BUILDING stage.

    Reads implementation_plan.md (or any markdown plan), organizes tasks
    into dependency waves, and dispatches isolated worker prompts that contain
    ONLY the specific task + upstream contracts — NOT the full mission text.

    NEVER writes code itself — pure orchestration role.

    Wave model:
        Wave 1: tasks with no dependencies → all run in parallel
        Wave 2: tasks whose deps are all in Wave 1 → run in parallel after Wave 1
        Wave N: continues until all tasks complete

    Each worker receives:
        a) One-line project context
        b) Its specific task description
        c) Upstream contracts from completed dependency tasks (interface shapes,
           file paths, function signatures) — NOT implementation details
    """

    def __init__(
        self,
        config: HierarchicalConfig,
        plan_path: Path,
        project_context: str = "",
    ):
        self.config = config
        self.plan_path = plan_path
        self.project_context = project_context
        self.splitter = MissionSplitter()
        self.contracts: Dict[str, str] = {}  # task_id → contract string

    def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> 'HierarchicalResults':
        """Parse plan, execute waves in order, aggregate results."""
        plan_text = self.plan_path.read_text()
        tasks = self._parse_plan_to_tasks(plan_text)

        if not tasks:
            tasks = [{"id": "task_0", "description": plan_text[:2000], "deps": [], "files": []}]

        waves = self._organize_into_waves(tasks)

        if progress_callback:
            progress_callback(
                f"[MasterBuilder] Plan parsed: {len(tasks)} tasks across {len(waves)} waves"
            )

        all_results: List[AgentResult] = []
        all_work_units: List[WorkUnit] = []
        started_at = datetime.now().isoformat()
        start_time = time.time()

        for wave_num, wave_tasks in enumerate(waves, start=1):
            if progress_callback:
                progress_callback(
                    f"[MasterBuilder] Executing Wave {wave_num}/{len(waves)}: "
                    f"{len(wave_tasks)} task(s)"
                )

            wave_units = self._build_wave_work_units(wave_tasks, wave_num, len(waves))
            all_work_units.extend(wave_units)

            wave_config = HierarchicalConfig(
                mission_id=f"{self.config.mission_id}_w{wave_num}",
                description=f"Wave {wave_num}/{len(waves)}: {self.config.description[:60]}",
                total_timeout=self.config.total_timeout,
                max_agents=len(wave_units),
                stage=self.config.stage,
                enable_streaming=self.config.enable_streaming,
            )
            exp = HierarchicalExperiment(wave_config)
            wave_results = exp.run(wave_units, progress_callback)
            all_results.extend(wave_results.agent_results)

            self._extract_contracts(wave_results.agent_results, wave_tasks)

        total_elapsed = time.time() - start_time
        aggregated = self._aggregate(all_results)

        return HierarchicalResults(
            config=self.config,
            work_units=all_work_units,
            agent_results=all_results,
            aggregated_result=aggregated,
            started_at=started_at,
            completed_at=datetime.now().isoformat(),
            total_elapsed_seconds=total_elapsed,
        )

    def _parse_plan_to_tasks(self, plan_text: str) -> List[Dict[str, Any]]:
        """Delegate to MissionSplitter.parse_plan_to_tasks() — the authoritative parser."""
        return self.splitter.parse_plan_to_tasks(plan_text)

    def _extract_files_from_text(self, text: str) -> List[str]:
        """Extract backtick file references from text."""
        return [m[0] for m in re.findall(r'`([^`]+\.(py|js|ts|tsx|jsx|md|json|yaml|yml))`', text)]

    def _organize_into_waves(self, tasks: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Topological sort of tasks → wave assignment."""
        task_by_id = {t["id"]: t for t in tasks}
        assigned: Dict[str, int] = {t["id"]: 0 for t in tasks}

        changed = True
        iterations = 0
        while changed and iterations < len(tasks) + 1:
            changed = False
            iterations += 1
            for task in tasks:
                if not task["deps"]:
                    continue
                max_dep_wave = max(
                    (assigned.get(dep_id, 0) for dep_id in task["deps"] if dep_id in task_by_id),
                    default=0
                )
                new_wave = max_dep_wave + 1
                if new_wave > assigned[task["id"]]:
                    assigned[task["id"]] = new_wave
                    changed = True

        max_wave = max(assigned.values(), default=0)
        waves: List[List[Dict[str, Any]]] = [[] for _ in range(max_wave + 1)]
        for task in tasks:
            waves[assigned[task["id"]]].append(task)
        return [w for w in waves if w]

    def _build_wave_work_units(
        self,
        wave_tasks: List[Dict[str, Any]],
        wave_num: int,
        total_waves: int,
    ) -> List[WorkUnit]:
        """Build WorkUnit objects for a wave using isolated prompts."""
        units = []
        for task in wave_tasks:
            upstream = {
                dep_id: self.contracts[dep_id]
                for dep_id in task.get("deps", [])
                if dep_id in self.contracts
            }
            prompt = self.splitter._create_worker_prompt(
                project_context=self.project_context,
                task_description=task["description"],
                task_id=task["id"],
                wave=wave_num,
                total_waves=total_waves,
                upstream_contracts=upstream,
                files=task.get("files", []),
            )
            units.append(WorkUnit(
                id=task["id"],
                description=task["description"][:100],
                prompt=prompt,
                wave=wave_num,
                dependencies=task.get("deps", []),
                files=task.get("files", []),
                estimated_complexity=self.splitter._estimate_complexity(task["description"]),
                strategy=SplitStrategy.TASK_BASED,
            ))
        return units

    def _extract_contracts(
        self,
        results: List[AgentResult],
        wave_tasks: List[Dict[str, Any]],
    ) -> None:
        """Extract contract strings from agent results and store by task_id."""
        task_ids = {t["id"] for t in wave_tasks}
        for result in results:
            if result.agent_id in task_ids:
                if result.parsed_result and isinstance(result.parsed_result.get("contract"), str):
                    contract_val = result.parsed_result["contract"]
                    if contract_val:
                        self.contracts[result.agent_id] = contract_val

    def _aggregate(self, results: List[AgentResult]) -> Dict[str, Any]:
        """Aggregate all wave results into a summary dict."""
        completed = sum(1 for r in results if r.status == "completed")
        failed = sum(1 for r in results if r.status == "failed")
        timeout = sum(1 for r in results if r.status == "timeout")
        all_created: List[str] = []
        all_modified: List[str] = []
        summaries = []
        for r in results:
            all_created.extend(r.files_created)
            all_modified.extend(r.files_modified)
            if r.parsed_result and r.parsed_result.get("summary"):
                summaries.append({"agent": r.agent_id, "summary": r.parsed_result["summary"]})
        return {
            "total_agents": len(results),
            "completed": completed,
            "failed": failed,
            "timeout": timeout,
            "all_files_created": list(set(all_created)),
            "all_files_modified": list(set(all_modified)),
            "summaries": summaries,
            "contracts_collected": list(self.contracts.keys()),
        }


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Hierarchical Framework - Self Test (Dashboard Integration)")
    print("=" * 60)

    config = HierarchicalConfig(
        mission_id="test_hf_dashboard",
        description="Test dashboard-integrated hierarchical framework",
        total_timeout=120,
        max_agents=2,
        stage="BUILDING",
        enable_streaming=True,
    )

    print(f"Config: mission_id={config.mission_id}, stage={config.stage}")
    print(f"HAS_AGENT_STREAM_MANAGER: {HAS_AGENT_STREAM_MANAGER}")
    print(f"HAS_POOL_MANAGER: {HAS_POOL_MANAGER}")
    print(f"HAS_INIT_GUARD: {HAS_INIT_GUARD}")

    work_units = [
        WorkUnit(
            id="test_wu_1",
            description="Simple math question 1",
            prompt='What is 2+2? Reply ONLY with: {"status": "build_complete", "summary": "Computed 2+2=4", "files_created": [], "files_modified": [], "answer": 4}',
            estimated_complexity=1
        ),
        WorkUnit(
            id="test_wu_2",
            description="Simple math question 2",
            prompt='What is 3+3? Reply ONLY with: {"status": "build_complete", "summary": "Computed 3+3=6", "files_created": [], "files_modified": [], "answer": 6}',
            estimated_complexity=1
        ),
    ]

    print(f"\nCreated {len(work_units)} work units")

    exp = HierarchicalExperiment(config)

    def progress(msg):
        print(f"  [PROGRESS] {msg}")

    print("\nRunning experiment...")
    results = exp.run(work_units, progress_callback=progress)

    print(f"\nResults summary:")
    summary = results.get_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    for ar in results.agent_results:
        print(f"\n  Agent {ar.agent_id}: status={ar.status}, elapsed={ar.elapsed_seconds:.1f}s")
        if ar.parsed_result:
            print(f"    parsed: {ar.parsed_result}")
        if ar.error:
            print(f"    error: {ar.error}")

    filepath = results.save()
    print(f"\nResults saved to: {filepath}")
    print("\nHierarchical Framework self-test complete!")
