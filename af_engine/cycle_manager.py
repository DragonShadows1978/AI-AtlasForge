"""
af_engine.cycle_manager - Multi-Cycle Iteration Logic

This module provides the CycleManager class for handling multi-cycle
mission iteration. It manages:
- Cycle budget tracking
- Continuation prompt generation
- Cycle history management
- Drift validation coordination
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from .state_manager import StateManager
from .integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class CycleManager:
    """
    Manages multi-cycle mission iteration.

    The CycleManager coordinates:
    - Cycle advancement and budget tracking
    - Continuation prompt generation for next cycles
    - Cycle history and progress tracking
    - Integration with drift validation
    """

    def __init__(self, state_manager: StateManager):
        """
        Initialize the cycle manager.

        Args:
            state_manager: StateManager instance for state persistence
        """
        self.state = state_manager

    @property
    def current_cycle(self) -> int:
        """Get current cycle number."""
        return self.state.cycle_number

    @property
    def cycle_budget(self) -> int:
        """Get total cycle budget."""
        return self.state.cycle_budget

    @property
    def cycles_remaining(self) -> int:
        """Get number of cycles remaining."""
        return max(0, self.cycle_budget - self.current_cycle)

    @property
    def is_last_cycle(self) -> bool:
        """Check if this is the last cycle."""
        return self.current_cycle >= self.cycle_budget

    @property
    def cycle_history(self) -> List[Dict]:
        """Get cycle history."""
        return self.state.cycle_history

    def should_continue(self) -> bool:
        """
        Determine if another cycle should be started.

        Returns:
            True if more cycles can be started, False otherwise
        """
        return self.current_cycle < self.cycle_budget

    def advance_cycle(self, continuation_prompt: str) -> Dict[str, Any]:
        """
        Advance to the next cycle.

        Args:
            continuation_prompt: Prompt for the next cycle

        Returns:
            Dictionary with cycle advancement details
        """
        old_cycle = self.current_cycle
        new_cycle = self.state.advance_cycle(continuation_prompt)

        result = {
            "old_cycle": old_cycle,
            "new_cycle": new_cycle,
            "cycles_remaining": self.cycles_remaining,
            "continuation_prompt": continuation_prompt,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(f"Advanced from cycle {old_cycle} to {new_cycle}")
        return result

    def generate_continuation_prompt(
        self,
        cycle_summary: str,
        findings: List[str],
        next_objectives: List[str],
    ) -> str:
        """
        Generate a continuation prompt for the next cycle.

        Args:
            cycle_summary: Summary of what was accomplished in this cycle
            findings: Key findings from this cycle
            next_objectives: Objectives for the next cycle

        Returns:
            Formatted continuation prompt
        """
        original_mission = self.state.get_field("original_problem_statement")
        if not original_mission:
            original_mission = self.state.get_field("problem_statement", "No mission defined")

        findings_text = "\n".join(f"- {f}" for f in findings) if findings else "None documented"
        objectives_text = "\n".join(f"- {o}" for o in next_objectives) if next_objectives else "Continue from previous cycle"

        prompt = f"""=== CONTINUATION: Cycle {self.current_cycle + 1} of {self.cycle_budget} ===

ORIGINAL MISSION:
{original_mission}

PREVIOUS CYCLE SUMMARY:
{cycle_summary}

KEY FINDINGS FROM CYCLE {self.current_cycle}:
{findings_text}

OBJECTIVES FOR THIS CYCLE:
{objectives_text}

Continue the mission, building on the work from the previous cycle.
Focus on the objectives above and address any outstanding issues.
"""
        return prompt

    def record_cycle_completion(
        self,
        summary: str,
        status: str = "completed",
        metrics: Optional[Dict] = None,
    ) -> None:
        """
        Record cycle completion details.

        Args:
            summary: Summary of the cycle
            status: Completion status ('completed', 'failed', 'partial')
            metrics: Optional performance metrics
        """
        cycle_record = {
            "cycle": self.current_cycle,
            "status": status,
            "summary": summary,
            "completed_at": datetime.now().isoformat(),
            "iterations": self.state.iteration,
        }

        if metrics:
            cycle_record["metrics"] = metrics

        # Add to cycle history
        history = self.state.get_field("cycle_history", [])
        history.append(cycle_record)
        self.state.set_field("cycle_history", history)

        logger.info(f"Recorded cycle {self.current_cycle} completion: {status}")

    def get_cycle_context(self) -> Dict[str, Any]:
        """
        Get context for the current cycle.

        Returns:
            Dictionary with current cycle context
        """
        return {
            "current_cycle": self.current_cycle,
            "cycle_budget": self.cycle_budget,
            "cycles_remaining": self.cycles_remaining,
            "is_last_cycle": self.is_last_cycle,
            "iteration": self.state.iteration,
            "cycle_history": self.cycle_history,
        }

    def validate_cycle_progress(
        self,
        expected_deliverables: List[str],
        artifacts_dir: Path,
    ) -> Dict[str, Any]:
        """
        Validate that expected deliverables were created in this cycle.

        Args:
            expected_deliverables: List of expected file patterns
            artifacts_dir: Directory to check for artifacts

        Returns:
            Validation result with found/missing deliverables
        """
        found = []
        missing = []

        for pattern in expected_deliverables:
            matches = list(artifacts_dir.glob(pattern))
            if matches:
                found.extend(str(m) for m in matches)
            else:
                missing.append(pattern)

        return {
            "valid": len(missing) == 0,
            "found": found,
            "missing": missing,
            "cycle": self.current_cycle,
        }

    def create_cycle_started_event(self) -> Event:
        """Create a CYCLE_STARTED event."""
        return Event(
            type=StageEvent.CYCLE_STARTED,
            stage="PLANNING",
            mission_id=self.state.mission_id,
            data=self.get_cycle_context(),
            source="cycle_manager",
        )

    def create_cycle_completed_event(
        self,
        summary: str,
        next_stage: str,
    ) -> Event:
        """
        Create a CYCLE_COMPLETED event.

        Args:
            summary: Cycle summary
            next_stage: Stage to transition to after cycle
        """
        return Event(
            type=StageEvent.CYCLE_COMPLETED,
            stage=next_stage,
            mission_id=self.state.mission_id,
            data={
                **self.get_cycle_context(),
                "summary": summary,
                "next_stage": next_stage,
            },
            source="cycle_manager",
        )

    def get_cycle_report(self) -> str:
        """
        Generate a cycle progress report.

        Returns:
            Formatted cycle report string
        """
        lines = [
            f"=== Cycle Progress Report ===",
            f"Current Cycle: {self.current_cycle} of {self.cycle_budget}",
            f"Iterations in Cycle: {self.state.iteration}",
            f"Cycles Remaining: {self.cycles_remaining}",
            "",
        ]

        if self.cycle_history:
            lines.append("Previous Cycles:")
            for cycle in self.cycle_history:
                cycle_num = cycle.get('cycle', '?')
                status = cycle.get('status', 'unknown')
                summary = cycle.get('summary', 'No summary')[:100]
                lines.append(f"  Cycle {cycle_num} [{status}]: {summary}...")
        else:
            lines.append("No previous cycles.")

        return "\n".join(lines)

    def format_cycle_history_for_prompt(self, max_cycles: int = 5) -> str:
        """
        Format cycle history for inclusion in prompts.

        Args:
            max_cycles: Maximum number of recent cycles to include

        Returns:
            Formatted string of cycle history
        """
        if not self.cycle_history:
            return "No previous cycles completed."

        recent = self.cycle_history[-max_cycles:]
        lines = []

        for cycle in recent:
            cycle_num = cycle.get('cycle', '?')
            status = cycle.get('status', 'unknown')
            summary = cycle.get('summary', 'No summary')
            iterations = cycle.get('iterations', '?')

            lines.append(f"Cycle {cycle_num} ({status}, {iterations} iterations):")
            lines.append(f"  {summary}")

        return "\n".join(lines)
