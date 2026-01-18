#!/usr/bin/env python3
"""
Timeout Budget Management for Hierarchical Agent Execution

Provides hierarchical timeout allocation for multi-level agent spawning.
When a parent spawns child agents, each child gets a portion of the parent's
remaining budget, with reserves maintained for aggregation and cleanup.

Design principles:
- Total budget set at mission level (default: 60 minutes)
- Each level maintains 10% reserve for aggregation
- Children share remaining budget equally or by weight
- Dynamic reallocation when children complete early
- Tracking of actual vs allocated time for optimization
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger("timeout_budget")


class TimeoutPolicy(Enum):
    """Policies for allocating timeouts to child agents."""
    EQUAL = "equal"           # Split equally among children (for sequential execution)
    WEIGHTED = "weighted"     # Split by provided weights
    FIRST_COME = "first_come" # Each child gets full remaining budget (sequential)
    FIXED = "fixed"           # Each child gets a fixed amount
    PARALLEL = "parallel"     # Each child gets full timeout (parallel execution)


@dataclass
class TimeAllocation:
    """Tracks time allocation for a single agent."""
    agent_id: str
    allocated_seconds: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    used_seconds: float = 0.0

    @property
    def is_started(self) -> bool:
        return self.started_at is not None

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def elapsed(self) -> float:
        """Seconds elapsed since start (or total if complete)."""
        if not self.is_started:
            return 0.0
        if self.is_complete:
            return self.completed_at - self.started_at
        return time.time() - self.started_at

    @property
    def remaining(self) -> float:
        """Seconds remaining in allocation."""
        return max(0.0, self.allocated_seconds - self.elapsed)

    @property
    def is_over_budget(self) -> bool:
        return self.elapsed > self.allocated_seconds


@dataclass
class TimeoutBudget:
    """
    Manages timeout budgets hierarchically.

    Usage:
        # Create root budget for mission
        budget = TimeoutBudget(total_seconds=3600)  # 60 minutes

        # Allocate for child agents
        allocations = budget.allocate_children(["agent_1", "agent_2", "agent_3"])

        # Start an agent
        budget.start_agent("agent_1")

        # Check remaining time
        remaining = budget.get_remaining("agent_1")

        # Mark complete
        budget.complete_agent("agent_1")

        # Get stats
        stats = budget.get_summary()
    """

    total_seconds: float
    reserve_ratio: float = 0.10  # 10% reserve for aggregation
    min_child_timeout: float = 30.0  # Minimum seconds per child
    policy: TimeoutPolicy = TimeoutPolicy.EQUAL

    # Internal state
    _allocations: Dict[str, TimeAllocation] = field(default_factory=dict)
    _created_at: float = field(default_factory=time.time)
    _parent_id: Optional[str] = None

    def __post_init__(self):
        self._allocations = {}
        self._created_at = time.time()

    @property
    def usable_seconds(self) -> float:
        """Total seconds available for work (after reserve)."""
        return self.total_seconds * (1.0 - self.reserve_ratio)

    @property
    def reserve_seconds(self) -> float:
        """Seconds reserved for aggregation/cleanup."""
        return self.total_seconds * self.reserve_ratio

    @property
    def elapsed(self) -> float:
        """Total seconds elapsed since budget creation."""
        return time.time() - self._created_at

    @property
    def remaining(self) -> float:
        """Total seconds remaining in budget."""
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def is_expired(self) -> bool:
        """Whether the total budget has expired."""
        return self.remaining <= 0

    @property
    def allocated_total(self) -> float:
        """Total seconds allocated to children."""
        return sum(a.allocated_seconds for a in self._allocations.values())

    @property
    def unallocated(self) -> float:
        """Seconds not yet allocated (within usable budget)."""
        return max(0.0, self.usable_seconds - self.allocated_total)

    def allocate_children(
        self,
        agent_ids: List[str],
        weights: Optional[Dict[str, float]] = None,
        fixed_seconds: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Allocate timeout budget to child agents.

        Args:
            agent_ids: List of child agent IDs
            weights: Optional dict of agent_id -> weight (for WEIGHTED policy)
            fixed_seconds: Fixed seconds per child (for FIXED policy)

        Returns:
            Dict mapping agent_id -> allocated seconds
        """
        n = len(agent_ids)
        if n == 0:
            return {}

        available = self.unallocated
        allocations = {}

        if self.policy == TimeoutPolicy.PARALLEL:
            # PARALLEL: Each child gets the full usable timeout (for concurrent execution)
            # Since agents run in parallel, they don't consume each other's time
            for agent_id in agent_ids:
                allocations[agent_id] = self.usable_seconds

        elif self.policy == TimeoutPolicy.FIXED and fixed_seconds:
            # Each child gets fixed amount (if available)
            per_child = min(fixed_seconds, available / n)
            for agent_id in agent_ids:
                allocations[agent_id] = per_child

        elif self.policy == TimeoutPolicy.WEIGHTED and weights:
            # Distribute by weight
            total_weight = sum(weights.get(aid, 1.0) for aid in agent_ids)
            for agent_id in agent_ids:
                weight = weights.get(agent_id, 1.0)
                allocations[agent_id] = available * (weight / total_weight)

        elif self.policy == TimeoutPolicy.FIRST_COME:
            # Each child gets full remaining budget (sequential execution)
            for agent_id in agent_ids:
                allocations[agent_id] = available

        else:  # EQUAL
            # Split equally
            per_child = available / n
            for agent_id in agent_ids:
                allocations[agent_id] = per_child

        # Enforce minimum timeout
        for agent_id in allocations:
            allocations[agent_id] = max(
                self.min_child_timeout,
                allocations[agent_id]
            )

        # Create allocation records
        for agent_id, seconds in allocations.items():
            self._allocations[agent_id] = TimeAllocation(
                agent_id=agent_id,
                allocated_seconds=seconds
            )
            logger.info(f"Allocated {seconds:.1f}s to {agent_id}")

        return allocations

    def start_agent(self, agent_id: str):
        """Mark an agent as started (begin consuming its allocation)."""
        if agent_id in self._allocations:
            self._allocations[agent_id].started_at = time.time()
            logger.info(f"Started agent: {agent_id}")

    def complete_agent(self, agent_id: str):
        """Mark an agent as completed."""
        if agent_id in self._allocations:
            alloc = self._allocations[agent_id]
            alloc.completed_at = time.time()
            if alloc.started_at:
                alloc.used_seconds = alloc.completed_at - alloc.started_at
            logger.info(f"Completed agent: {agent_id} (used {alloc.used_seconds:.1f}s)")

    def get_remaining(self, agent_id: str) -> float:
        """Get remaining seconds for a specific agent."""
        if agent_id in self._allocations:
            return self._allocations[agent_id].remaining
        return 0.0

    def get_allocation(self, agent_id: str) -> Optional[TimeAllocation]:
        """Get full allocation info for an agent."""
        return self._allocations.get(agent_id)

    def is_agent_over_budget(self, agent_id: str) -> bool:
        """Check if an agent has exceeded its allocation."""
        if agent_id in self._allocations:
            return self._allocations[agent_id].is_over_budget
        return True

    def get_timeout_for_cli(self, agent_id: str) -> int:
        """
        Get timeout value suitable for passing to CLI/subprocess.
        Returns integer seconds, capped at remaining allocation.
        """
        if agent_id in self._allocations:
            alloc = self._allocations[agent_id]
            # If started, use remaining; if not, use full allocation
            if alloc.is_started:
                return int(alloc.remaining)
            return int(alloc.allocated_seconds)
        return int(self.min_child_timeout)

    def reclaim_unused(self) -> float:
        """
        Reclaim unused time from completed agents.
        Returns the amount reclaimed.
        """
        reclaimed = 0.0
        for alloc in self._allocations.values():
            if alloc.is_complete:
                unused = alloc.allocated_seconds - alloc.used_seconds
                if unused > 0:
                    reclaimed += unused
        return reclaimed

    def create_child_budget(
        self,
        agent_id: str,
        reserve_ratio: Optional[float] = None
    ) -> 'TimeoutBudget':
        """
        Create a new TimeoutBudget for a child agent to use with its subagents.

        Args:
            agent_id: The agent receiving the budget
            reserve_ratio: Override reserve ratio for child (default: inherit)

        Returns:
            A new TimeoutBudget scoped to the child's allocation
        """
        if agent_id not in self._allocations:
            raise ValueError(f"No allocation for agent: {agent_id}")

        alloc = self._allocations[agent_id]
        child_budget = TimeoutBudget(
            total_seconds=alloc.remaining,
            reserve_ratio=reserve_ratio if reserve_ratio is not None else self.reserve_ratio,
            min_child_timeout=self.min_child_timeout,
            policy=self.policy
        )
        child_budget._parent_id = agent_id

        return child_budget

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of budget status."""
        completed = [a for a in self._allocations.values() if a.is_complete]
        active = [a for a in self._allocations.values() if a.is_started and not a.is_complete]
        pending = [a for a in self._allocations.values() if not a.is_started]

        return {
            "total_seconds": self.total_seconds,
            "elapsed_seconds": self.elapsed,
            "remaining_seconds": self.remaining,
            "usable_seconds": self.usable_seconds,
            "reserve_seconds": self.reserve_seconds,
            "allocated_seconds": self.allocated_total,
            "unallocated_seconds": self.unallocated,
            "is_expired": self.is_expired,
            "agents": {
                "total": len(self._allocations),
                "completed": len(completed),
                "active": len(active),
                "pending": len(pending),
                "over_budget": sum(1 for a in self._allocations.values() if a.is_over_budget)
            },
            "reclaimed_seconds": self.reclaim_unused()
        }

    def get_agent_summary(self) -> List[Dict[str, Any]]:
        """Get detailed summary for each agent."""
        return [
            {
                "agent_id": a.agent_id,
                "allocated": a.allocated_seconds,
                "elapsed": a.elapsed,
                "remaining": a.remaining,
                "used": a.used_seconds,
                "is_started": a.is_started,
                "is_complete": a.is_complete,
                "is_over_budget": a.is_over_budget
            }
            for a in self._allocations.values()
        ]


# Recommended timeout configurations
class TimeoutPresets:
    """Preset timeout configurations for common scenarios."""

    @staticmethod
    def quick_task() -> TimeoutBudget:
        """For quick tasks: 5 minutes total."""
        return TimeoutBudget(total_seconds=300)

    @staticmethod
    def standard_task() -> TimeoutBudget:
        """For standard tasks: 30 minutes total."""
        return TimeoutBudget(total_seconds=1800)

    @staticmethod
    def complex_task() -> TimeoutBudget:
        """For complex tasks: 60 minutes total."""
        return TimeoutBudget(total_seconds=3600)

    @staticmethod
    def extended_task() -> TimeoutBudget:
        """For extended tasks: 90 minutes total."""
        return TimeoutBudget(total_seconds=5400)

    @staticmethod
    def multi_agent_parallel(
        num_agents: int,
        per_agent_minutes: int = 10
    ) -> TimeoutBudget:
        """
        For parallel multi-agent work.
        Each agent gets per_agent_minutes since they run concurrently.
        Total budget is per_agent_minutes + 20% overhead for coordination.
        """
        total = per_agent_minutes * 60 * 1.2  # 20% overhead
        return TimeoutBudget(
            total_seconds=total,
            policy=TimeoutPolicy.PARALLEL  # Each agent gets full timeout
        )

    @staticmethod
    def hierarchical(
        max_agents: int = 5,
        max_subagents: int = 10,
        per_agent_minutes: int = 10
    ) -> TimeoutBudget:
        """
        For hierarchical multi-agent work.

        Since agents AND subagents run in PARALLEL, each agent gets per_agent_minutes.
        The total budget = per_agent_minutes + overhead for coordination.

        Args:
            max_agents: Maximum parallel agents (for info/logging, doesn't affect budget)
            max_subagents: Maximum subagents per agent (for info/logging)
            per_agent_minutes: Time budget for EACH agent (not divided by count!)

        Returns:
            TimeoutBudget configured for parallel execution
        """
        # Each agent gets per_agent_minutes since they run in parallel
        # Add 30% overhead for coordination/synthesis
        total = per_agent_minutes * 60 * 1.3
        # Account for 10% reserve
        total = total / 0.9

        return TimeoutBudget(
            total_seconds=total,
            reserve_ratio=0.10,
            min_child_timeout=60.0,  # At least 1 minute per child
            policy=TimeoutPolicy.PARALLEL  # Each agent gets full timeout (parallel execution)
        )


if __name__ == "__main__":
    # Self-test
    print("Timeout Budget - Self Test")
    print("=" * 50)

    # Create a hierarchical budget
    # per_agent_minutes=10 means each parallel agent gets 10 minutes
    budget = TimeoutPresets.hierarchical(max_agents=5, max_subagents=10, per_agent_minutes=10)
    print(f"Created budget: {budget.total_seconds:.0f}s ({budget.total_seconds/60:.1f} minutes)")
    print(f"Usable: {budget.usable_seconds:.0f}s, Reserve: {budget.reserve_seconds:.0f}s")

    # Allocate to agents
    agent_ids = ["agent_1", "agent_2", "agent_3"]
    allocations = budget.allocate_children(agent_ids)
    print(f"\nAllocated to agents:")
    for aid, secs in allocations.items():
        print(f"  {aid}: {secs:.1f}s ({secs/60:.1f} minutes)")

    # Simulate agent execution
    budget.start_agent("agent_1")
    time.sleep(0.1)  # Simulate work
    budget.complete_agent("agent_1")

    # Check summary
    summary = budget.get_summary()
    print(f"\nSummary:")
    print(f"  Elapsed: {summary['elapsed_seconds']:.1f}s")
    print(f"  Remaining: {summary['remaining_seconds']:.1f}s")
    print(f"  Agents: {summary['agents']}")

    # Create child budget
    child_budget = budget.create_child_budget("agent_2")
    print(f"\nChild budget for agent_2: {child_budget.total_seconds:.1f}s")

    # Allocate to subagents
    subagent_ids = [f"subagent_{i}" for i in range(5)]
    sub_allocations = child_budget.allocate_children(subagent_ids)
    print("Subagent allocations:")
    for sid, secs in sub_allocations.items():
        print(f"  {sid}: {secs:.1f}s")

    print("\nTimeout Budget self-test complete!")
