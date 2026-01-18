#!/usr/bin/env python3
"""
Hierarchical Experiment Framework for Multi-Agent Claude Execution

This framework extends the base experiment_framework.py to support:
1. Parallel spawning of multiple Claude agents
2. Hierarchical agent structure (agents can spawn subagents)
3. Checkpoint-based synchronization
4. Variable timeout management
5. Result aggregation from parallel agents

Architecture:
    HierarchicalExperiment
        ├── WorkUnit Agent 1 ──┬── Subagent 1.1
        │                      ├── Subagent 1.2
        │                      └── ...up to 10
        ├── WorkUnit Agent 2 ──┬── Subagent 2.1
        │                      └── ...
        └── ...up to 5 parallel

Key Features:
- ThreadPoolExecutor for parallel agent execution
- Checkpoint-based completion detection
- Hierarchical timeout budgeting
- Automatic result aggregation
"""

import json
import subprocess
import time
import os
import logging
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid

# Import our supporting modules
from checkpoint_manager import CheckpointManager, CheckpointStatus, quick_checkpoint
from timeout_budget import TimeoutBudget, TimeoutPresets, TimeoutPolicy
from mission_splitter import MissionSplitter, WorkUnit, SplitStrategy
from experiment_framework import ModelType, TrialResult, invoke_fresh_claude

logger = logging.getLogger("hierarchical_framework")

# Base paths - use centralized configuration
from atlasforge_config import BASE_DIR
EXPERIMENTS_DIR = BASE_DIR / "experiments"
HIERARCHICAL_RESULTS_DIR = EXPERIMENTS_DIR / "hierarchical_results"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"

# Ensure directories exist
HIERARCHICAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINTS_DIR.mkdir(exist_ok=True)


class AgentRole(Enum):
    """Roles for agents in the hierarchy."""
    ORCHESTRATOR = "orchestrator"  # Top-level coordinator
    WORKER = "worker"              # Primary work unit agent
    SUBAGENT = "subagent"          # Subagent spawned by worker


@dataclass
class HierarchicalConfig:
    """Configuration for hierarchical experiments."""
    mission_id: str
    description: str = ""
    total_timeout: int = 3600          # 60 minutes default
    max_agents: int = 5                 # Max parallel workers
    max_subagents_per_agent: int = 10   # Max subagents per worker
    model: ModelType = ModelType.CLAUDE_SONNET
    subagent_model: ModelType = ModelType.CLAUDE_HAIKU  # Cheaper for subagents
    timeout_reserve_ratio: float = 0.10  # Reserve for aggregation
    poll_interval: float = 5.0          # Seconds between status checks
    system_prompt: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

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
        """Save results to JSON file."""
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
        """Get a summary of the results."""
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


class HierarchicalExperiment:
    """
    Manages hierarchical multi-agent experiments.

    Usage:
        # 1. Configure
        config = HierarchicalConfig(
            mission_id="feature_xyz",
            total_timeout=3600,  # 60 minutes
            max_agents=5,
            max_subagents_per_agent=10
        )

        # 2. Create experiment
        exp = HierarchicalExperiment(config)

        # 3. Split mission into work units
        splitter = MissionSplitter()
        work_units = splitter.split(mission_text, max_units=config.max_agents)

        # 4. Run
        results = exp.run(work_units)

        # 5. Save
        results.save()
    """

    def __init__(self, config: HierarchicalConfig):
        self.config = config
        self.checkpoint_mgr = CheckpointManager(config.mission_id)
        # Use PARALLEL policy: each agent gets the full timeout since they run concurrently
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
            progress_callback(f"Starting hierarchical experiment: {self.config.mission_id}")
            progress_callback(f"Work units: {len(work_units)}, Timeout: {self.config.total_timeout}s")

        # Allocate timeout budget to work units
        agent_ids = [wu.id for wu in work_units]
        self.timeout_budget.allocate_children(agent_ids)

        # Run work units in parallel
        self.agent_results = self._run_parallel_agents(work_units, progress_callback)

        # Aggregate results
        aggregated = self._aggregate_results(self.agent_results)

        completed_at = datetime.now().isoformat()
        total_elapsed = time.time() - start_time

        if progress_callback:
            progress_callback(f"Experiment complete. Elapsed: {total_elapsed:.1f}s")

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.max_agents) as executor:
            # Submit all work units
            future_to_wu = {
                executor.submit(self._run_single_agent, wu): wu
                for wu in work_units
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_wu):
                wu = future_to_wu[future]
                try:
                    result = future.result()
                    results.append(result)
                    if progress_callback:
                        progress_callback(f"Agent {wu.id} completed: {result.status}")
                except Exception as e:
                    logger.error(f"Agent {wu.id} failed with exception: {e}")
                    # Create failed result
                    results.append(AgentResult(
                        agent_id=wu.id,
                        role=AgentRole.WORKER,
                        status="failed",
                        response="",
                        parsed_result=None,
                        elapsed_seconds=0,
                        error=str(e)
                    ))

        return results

    def _run_single_agent(self, work_unit: WorkUnit) -> AgentResult:
        """Execute a single work unit agent."""
        agent_id = work_unit.id
        start_time = time.time()

        # Create checkpoint
        self.checkpoint_mgr.create_checkpoint(agent_id, CheckpointStatus.IN_PROGRESS)
        self.timeout_budget.start_agent(agent_id)

        # Get timeout for this agent
        timeout = self.timeout_budget.get_timeout_for_cli(agent_id)

        # Build the prompt with subagent spawning capability
        prompt = self._build_agent_prompt(work_unit)

        try:
            # Invoke Claude
            response, response_time_ms = invoke_fresh_claude(
                prompt=prompt,
                model=self.config.model,
                system_prompt=self.config.system_prompt,
                timeout=timeout
            )

            elapsed = time.time() - start_time
            self.timeout_budget.complete_agent(agent_id)

            # Parse response
            parsed = self._parse_response(response)

            # Determine status
            if response.startswith("ERROR: Timeout"):
                status = "timeout"
            elif response.startswith("ERROR:"):
                status = "failed"
            elif parsed and parsed.get("status") == "completed":
                status = "completed"
            else:
                status = "completed"  # Default to completed if we got a response

            # Update checkpoint
            self.checkpoint_mgr.mark_completed(agent_id, {
                "status": status,
                "response": response[:500],
                "elapsed": elapsed
            })

            return AgentResult(
                agent_id=agent_id,
                role=AgentRole.WORKER,
                status=status,
                response=response,
                parsed_result=parsed,
                elapsed_seconds=elapsed,
                files_created=parsed.get("files_created", []) if parsed else [],
                files_modified=parsed.get("files_modified", []) if parsed else []
            )

        except Exception as e:
            elapsed = time.time() - start_time
            self.checkpoint_mgr.mark_failed(agent_id, str(e))

            return AgentResult(
                agent_id=agent_id,
                role=AgentRole.WORKER,
                status="failed",
                response="",
                parsed_result=None,
                elapsed_seconds=elapsed,
                error=str(e)
            )

    def _build_agent_prompt(self, work_unit: WorkUnit) -> str:
        """Build the full prompt for an agent, including subagent capability."""
        base_prompt = work_unit.prompt

        subagent_instructions = f"""

# Subagent Spawning Capability

You have the ability to spawn up to {self.config.max_subagents_per_agent} subagents for parallel work.

To spawn subagents, use the experiment_framework:

```python
from experiment_framework import Experiment, ExperimentConfig, ModelType

config = ExperimentConfig(
    name="subagent_task",
    description="Parallel subtask execution",
    conditions=["subtask_1", "subtask_2", ...],
    model=ModelType.{self.config.subagent_model.name},
    timeout_seconds=180,  # 3 minutes per subtask
    trials_per_condition=1
)

exp = Experiment(config)
exp.add_condition("subtask_1", lambda: "First subtask prompt...")
exp.add_condition("subtask_2", lambda: "Second subtask prompt...")

results = exp.run()
```

Use subagents when:
- Task can be parallelized into independent subtasks
- Multiple files need similar modifications
- You need to try multiple approaches

Do NOT use subagents for:
- Simple, sequential tasks
- Tasks requiring shared state
- Very quick operations

# Checkpoint Signaling

When complete, your result will be automatically recorded. Ensure your final
response is valid JSON with at minimum:
- "status": "completed" or "failed"
- "summary": Description of what was done

"""
        return base_prompt + subagent_instructions

    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse JSON response from agent."""
        def is_valid_dict(obj):
            """Check if obj is a dict (not just any JSON value)."""
            return isinstance(obj, dict)

        try:
            # Try to find JSON in response
            # Look for ```json blocks first
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(1))
                if is_valid_dict(parsed):
                    return parsed

            # Try parsing entire response as JSON
            parsed = json.loads(response)
            if is_valid_dict(parsed):
                return parsed
        except json.JSONDecodeError:
            pass

        # Try to find any JSON object in response
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                parsed = json.loads(response[start:end])
                if is_valid_dict(parsed):
                    return parsed
        except json.JSONDecodeError:
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
            "errors": []
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

        # Deduplicate file lists
        aggregated["all_files_created"] = list(set(aggregated["all_files_created"]))
        aggregated["all_files_modified"] = list(set(aggregated["all_files_modified"]))

        return aggregated

    def wait_for_completion(
        self,
        agent_ids: List[str],
        timeout: Optional[int] = None
    ) -> bool:
        """
        Wait for all specified agents to complete.
        Wrapper around checkpoint manager for external use.
        """
        if timeout is None:
            timeout = int(self.timeout_budget.remaining)

        return self.checkpoint_mgr.wait_for_all(
            agent_ids,
            timeout=timeout,
            poll_interval=self.config.poll_interval
        )


# Convenience functions for common patterns
def run_parallel_mission(
    mission: str,
    max_agents: int = 5,
    timeout_minutes: int = 60,
    model: ModelType = ModelType.CLAUDE_SONNET,
    progress_callback: Optional[Callable[[str], None]] = None
) -> HierarchicalResults:
    """
    Convenience function to run a mission in parallel.

    Args:
        mission: The mission text
        max_agents: Maximum parallel agents
        timeout_minutes: Total timeout in minutes
        model: Model to use
        progress_callback: Optional progress callback

    Returns:
        HierarchicalResults
    """
    mission_id = f"mission_{uuid.uuid4().hex[:8]}"

    config = HierarchicalConfig(
        mission_id=mission_id,
        description=mission[:100],
        total_timeout=timeout_minutes * 60,
        max_agents=max_agents,
        model=model
    )

    # Split mission
    splitter = MissionSplitter()
    work_units = splitter.split(mission, max_units=max_agents)

    if progress_callback:
        progress_callback(f"Split mission into {len(work_units)} work units")

    # Run
    exp = HierarchicalExperiment(config)
    return exp.run(work_units, progress_callback)


def run_with_subagents(
    mission: str,
    num_agents: int = 3,
    subagents_per_agent: int = 5,
    timeout_minutes: int = 45,
    progress_callback: Optional[Callable[[str], None]] = None
) -> HierarchicalResults:
    """
    Run a mission with agents that can spawn subagents.

    Args:
        mission: The mission text
        num_agents: Number of primary agents
        subagents_per_agent: Max subagents each agent can spawn
        timeout_minutes: Total timeout
        progress_callback: Optional progress callback

    Returns:
        HierarchicalResults
    """
    mission_id = f"subagent_mission_{uuid.uuid4().hex[:8]}"

    config = HierarchicalConfig(
        mission_id=mission_id,
        description=mission[:100],
        total_timeout=timeout_minutes * 60,
        max_agents=num_agents,
        max_subagents_per_agent=subagents_per_agent,
        model=ModelType.CLAUDE_SONNET,
        subagent_model=ModelType.CLAUDE_HAIKU
    )

    # Split mission
    splitter = MissionSplitter()
    work_units = splitter.split(mission, max_units=num_agents)

    # Run
    exp = HierarchicalExperiment(config)
    return exp.run(work_units, progress_callback)


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

        # Spawn subagents
        subagent_ids = spawner.spawn([
            {"id": "sub_1", "prompt": "Do task 1"},
            {"id": "sub_2", "prompt": "Do task 2"},
        ])

        # Wait for completion
        spawner.wait_for_all()

        # Get results
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
        """
        Spawn subagents for the given tasks.

        Args:
            tasks: List of {"id": str, "prompt": str} dicts
            parallel: If True, run in parallel; otherwise sequential

        Returns:
            List of subagent IDs
        """
        tasks = tasks[:self.max_subagents]  # Enforce limit

        if parallel:
            return self._spawn_parallel(tasks)
        else:
            return self._spawn_sequential(tasks)

    def _spawn_parallel(self, tasks: List[Dict[str, str]]) -> List[str]:
        """Spawn subagents in parallel."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {}
            for task in tasks:
                subagent_id = f"{self.parent_id}_{task['id']}"
                self.subagent_ids.append(subagent_id)
                self.checkpoint_mgr.create_checkpoint(
                    subagent_id,
                    CheckpointStatus.IN_PROGRESS
                )

                futures[executor.submit(
                    self._run_subagent,
                    subagent_id,
                    task['prompt']
                )] = subagent_id

            for future in concurrent.futures.as_completed(futures):
                subagent_id = futures[future]
                try:
                    result = future.result()
                    self.results[subagent_id] = result
                except Exception as e:
                    logger.error(f"Subagent {subagent_id} failed: {e}")
                    self.results[subagent_id] = AgentResult(
                        agent_id=subagent_id,
                        role=AgentRole.SUBAGENT,
                        status="failed",
                        response="",
                        parsed_result=None,
                        elapsed_seconds=0,
                        error=str(e)
                    )

        return self.subagent_ids

    def _spawn_sequential(self, tasks: List[Dict[str, str]]) -> List[str]:
        """Spawn subagents sequentially."""
        for task in tasks:
            subagent_id = f"{self.parent_id}_{task['id']}"
            self.subagent_ids.append(subagent_id)
            self.checkpoint_mgr.create_checkpoint(
                subagent_id,
                CheckpointStatus.IN_PROGRESS
            )

            try:
                result = self._run_subagent(subagent_id, task['prompt'])
                self.results[subagent_id] = result
            except Exception as e:
                logger.error(f"Subagent {subagent_id} failed: {e}")
                self.results[subagent_id] = AgentResult(
                    agent_id=subagent_id,
                    role=AgentRole.SUBAGENT,
                    status="failed",
                    response="",
                    parsed_result=None,
                    elapsed_seconds=0,
                    error=str(e)
                )

        return self.subagent_ids

    def _run_subagent(self, subagent_id: str, prompt: str) -> AgentResult:
        """Run a single subagent."""
        start_time = time.time()

        response, response_time_ms = invoke_fresh_claude(
            prompt=prompt,
            model=self.model,
            timeout=self.timeout_per_subagent
        )

        elapsed = time.time() - start_time

        # Determine status
        if response.startswith("ERROR: Timeout"):
            status = "timeout"
        elif response.startswith("ERROR:"):
            status = "failed"
        else:
            status = "completed"

        # Update checkpoint
        self.checkpoint_mgr.update_checkpoint(
            subagent_id,
            status=CheckpointStatus.COMPLETED if status == "completed" else CheckpointStatus.FAILED,
            result={"response": response[:500]}
        )

        return AgentResult(
            agent_id=subagent_id,
            role=AgentRole.SUBAGENT,
            status=status,
            response=response,
            parsed_result=None,
            elapsed_seconds=elapsed
        )

    def wait_for_all(self, timeout: int = 600) -> bool:
        """Wait for all subagents to complete."""
        return self.checkpoint_mgr.wait_for_all(self.subagent_ids, timeout=timeout)

    def get_results(self) -> Dict[str, AgentResult]:
        """Get all subagent results."""
        return self.results


if __name__ == "__main__":
    # Self-test
    print("Hierarchical Framework - Self Test")
    print("=" * 50)

    # Simple test configuration
    config = HierarchicalConfig(
        mission_id="test_hierarchical",
        description="Test hierarchical framework",
        total_timeout=60,  # 1 minute for test
        max_agents=2,
        max_subagents_per_agent=2,
        model=ModelType.CLAUDE_HAIKU  # Fast model for testing
    )

    print(f"Config: {config.to_dict()}")

    # Create work units manually for test
    work_units = [
        WorkUnit(
            id="test_wu_1",
            description="Test work unit 1",
            prompt="What is 2+2? Reply with just a JSON object: {\"answer\": <number>}",
            estimated_complexity=1
        ),
        WorkUnit(
            id="test_wu_2",
            description="Test work unit 2",
            prompt="What is 3+3? Reply with just a JSON object: {\"answer\": <number>}",
            estimated_complexity=1
        )
    ]

    print(f"\nCreated {len(work_units)} work units")

    # Run experiment
    exp = HierarchicalExperiment(config)

    def progress(msg):
        print(f"  Progress: {msg}")

    print("\nRunning experiment...")
    results = exp.run(work_units, progress_callback=progress)

    # Show results
    print(f"\nResults summary:")
    summary = results.get_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # Save results
    filepath = results.save()
    print(f"\nResults saved to: {filepath}")

    print("\nHierarchical Framework self-test complete!")
