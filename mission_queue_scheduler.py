#!/usr/bin/env python3
"""
Mission Queue Scheduler - Extended Queue Management with Priority and Scheduling

This module extends the existing mission queue infrastructure with:
1. Priority levels (High/Normal/Low) affecting queue order
2. Scheduled start times (start_after timestamps)
3. Mission dependencies (run after mission X completes)
4. Estimated cycle time calculations for timeline visualization
5. Queue sorting and filtering logic

Dependencies:
- io_utils for atomic JSON operations
- datetime for scheduling
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import uuid as uuid_module

# Configure logging
logger = logging.getLogger("mission_queue_scheduler")

# Paths - use centralized configuration
from atlasforge_config import MISSION_QUEUE_PATH, MISSION_PATH, MISSIONS_DIR
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"


class Priority(Enum):
    """Mission priority levels affecting queue order."""
    CRITICAL = "critical"  # Immediate, drop everything
    HIGH = "high"          # Jump ahead of normal/low priority
    NORMAL = "normal"      # Default priority
    LOW = "low"            # Run after high/normal are complete

    @classmethod
    def from_string(cls, value: str) -> "Priority":
        """Convert string to Priority enum, defaulting to NORMAL."""
        value_lower = value.lower() if value else "normal"
        for p in cls:
            if p.value == value_lower:
                return p
        return cls.NORMAL

    def weight(self) -> int:
        """Return sorting weight (lower = higher priority)."""
        weights = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 5,
            Priority.NORMAL: 10,
            Priority.LOW: 20
        }
        return weights.get(self, 10)

    @property
    def css_color(self) -> str:
        """Return CSS color for this priority."""
        colors = {
            Priority.CRITICAL: "#dc3545",  # Red
            Priority.HIGH: "#fd7e14",      # Orange
            Priority.NORMAL: "#0d6efd",    # Blue
            Priority.LOW: "#6c757d"        # Gray
        }
        return colors.get(self, "#6c757d")

    @property
    def display_label(self) -> str:
        """Return display label for this priority."""
        labels = {
            Priority.CRITICAL: "CRITICAL",
            Priority.HIGH: "High",
            Priority.NORMAL: "Normal",
            Priority.LOW: "Low"
        }
        return labels.get(self, "Normal")


class DependencyStatus(Enum):
    """Status of mission dependency checks."""
    READY = "ready"           # All dependencies satisfied
    WAITING = "waiting"       # Dependencies not yet complete
    BLOCKED = "blocked"       # Dependency failed
    NOT_FOUND = "not_found"   # Dependency mission doesn't exist


@dataclass
class QueueItem:
    """Extended queue item with priority and scheduling."""
    id: str
    recommendation_id: Optional[str]
    mission_title: str
    mission_description: str
    cycle_budget: int
    queued_at: str
    source_recommendation: Optional[Dict] = None

    # Extended fields
    priority: str = "normal"
    scheduled_start: Optional[str] = None  # ISO timestamp - don't start before this time
    start_condition: Optional[str] = None  # Expression like "idle_after:17:00" or "after_mission:xyz"
    depends_on: Optional[str] = None       # Mission ID that must complete first
    estimated_minutes: Optional[int] = None  # Estimated cycle time

    # Metadata
    created_by: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "recommendation_id": self.recommendation_id,
            "mission_title": self.mission_title,
            "mission_description": self.mission_description,
            "cycle_budget": self.cycle_budget,
            "queued_at": self.queued_at,
            "source_recommendation": self.source_recommendation,
            "priority": self.priority,
            "scheduled_start": self.scheduled_start,
            "start_condition": self.start_condition,
            "depends_on": self.depends_on,
            "estimated_minutes": self.estimated_minutes,
            "created_by": self.created_by,
            "tags": self.tags
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "QueueItem":
        """Create QueueItem from dictionary."""
        return cls(
            id=data.get("id", f"queue_{uuid_module.uuid4().hex[:8]}"),
            recommendation_id=data.get("recommendation_id"),
            mission_title=data.get("mission_title", "Untitled"),
            mission_description=data.get("mission_description", ""),
            cycle_budget=data.get("cycle_budget", 3),
            queued_at=data.get("queued_at", datetime.now().isoformat()),
            source_recommendation=data.get("source_recommendation"),
            priority=data.get("priority", "normal"),
            scheduled_start=data.get("scheduled_start"),
            start_condition=data.get("start_condition"),
            depends_on=data.get("depends_on"),
            estimated_minutes=data.get("estimated_minutes"),
            created_by=data.get("created_by"),
            tags=data.get("tags", [])
        )

    def get_priority_enum(self) -> Priority:
        """Get Priority enum for this item."""
        return Priority.from_string(self.priority)


@dataclass
class QueueState:
    """Full queue state with extended metadata."""
    queue: List[Dict]
    enabled: bool = True
    last_processed_at: Optional[str] = None

    # Extended settings
    auto_estimate_time: bool = True
    default_priority: str = "normal"
    notification_settings: Optional[Dict] = None

    # Pause/resume functionality
    paused: bool = False
    paused_at: Optional[str] = None
    pause_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "queue": self.queue,
            "enabled": self.enabled,
            "last_processed_at": self.last_processed_at,
            "auto_estimate_time": self.auto_estimate_time,
            "default_priority": self.default_priority,
            "notification_settings": self.notification_settings,
            "paused": self.paused,
            "paused_at": self.paused_at,
            "pause_reason": self.pause_reason
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "QueueState":
        """Create QueueState from dictionary."""
        return cls(
            queue=data.get("queue", []),
            enabled=data.get("enabled", True),
            last_processed_at=data.get("last_processed_at"),
            auto_estimate_time=data.get("auto_estimate_time", True),
            default_priority=data.get("default_priority", "normal"),
            notification_settings=data.get("notification_settings"),
            paused=data.get("paused", False),
            paused_at=data.get("paused_at"),
            pause_reason=data.get("pause_reason")
        )


class MissionQueueScheduler:
    """
    Scheduler for mission queue with priority and dependency management.

    Features:
    - Priority-based sorting (High > Normal > Low)
    - Scheduled start times (don't run before specified time)
    - Mission dependencies (wait for another mission to complete)
    - Estimated time calculations for timeline visualization
    """

    # Default cycle time estimates (minutes) by complexity indicators
    DEFAULT_CYCLE_TIME = 45  # Default if no estimate
    CYCLE_TIME_BY_CYCLES = {
        1: 30,   # Simple tasks
        2: 45,   # Medium tasks
        3: 60,   # Standard complexity
        4: 75,
        5: 90,
        6: 100,
        7: 110,
        8: 120,
        9: 130,
        10: 140
    }

    def __init__(self, io_utils_module=None):
        """Initialize scheduler with optional io_utils module."""
        self.io_utils = io_utils_module
        if not self.io_utils:
            try:
                import io_utils as _io
                self.io_utils = _io
            except ImportError:
                self.io_utils = None

    def _load_queue(self) -> QueueState:
        """Load queue state from disk."""
        if self.io_utils:
            data = self.io_utils.atomic_read_json(
                MISSION_QUEUE_PATH,
                {"queue": [], "enabled": True}
            )
        else:
            try:
                with open(MISSION_QUEUE_PATH, 'r') as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"queue": [], "enabled": True}
        return QueueState.from_dict(data)

    def _save_queue(self, state: QueueState):
        """Save queue state to disk."""
        if self.io_utils:
            self.io_utils.atomic_write_json(MISSION_QUEUE_PATH, state.to_dict())
        else:
            MISSION_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MISSION_QUEUE_PATH, 'w') as f:
                json.dump(state.to_dict(), f, indent=2)

    def get_queue(self) -> QueueState:
        """Get current queue state."""
        return self._load_queue()

    def add_to_queue(
        self,
        mission_title: str,
        mission_description: str,
        cycle_budget: int = 3,
        priority: str = "normal",
        scheduled_start: Optional[str] = None,
        depends_on: Optional[str] = None,
        recommendation_id: Optional[str] = None,
        source_recommendation: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        created_by: Optional[str] = None
    ) -> Tuple[QueueItem, int]:
        """
        Add a new item to the queue.

        Returns:
            Tuple of (QueueItem, position_in_queue)
        """
        state = self._load_queue()

        # Create queue item
        item = QueueItem(
            id=f"queue_{uuid_module.uuid4().hex[:8]}",
            recommendation_id=recommendation_id,
            mission_title=mission_title,
            mission_description=mission_description,
            cycle_budget=max(1, min(10, cycle_budget)),
            queued_at=datetime.now().isoformat(),
            source_recommendation=source_recommendation,
            priority=Priority.from_string(priority).value,
            scheduled_start=scheduled_start,
            depends_on=depends_on,
            estimated_minutes=self.estimate_cycle_time(cycle_budget, mission_description),
            created_by=created_by,
            tags=tags or []
        )

        # Add to queue
        state.queue.append(item.to_dict())

        # Sort queue by priority and scheduling
        sorted_queue = self.sort_queue(state.queue)
        state.queue = sorted_queue

        self._save_queue(state)

        # Find position of new item
        position = next(
            (i + 1 for i, q in enumerate(sorted_queue) if q.get("id") == item.id),
            len(sorted_queue)
        )

        return item, position

    def estimate_cycle_time(self, cycle_budget: int, description: str = "") -> int:
        """
        Estimate total mission time in minutes.

        Uses cycle budget as primary indicator, with adjustments
        based on description complexity indicators.
        """
        base_time = self.CYCLE_TIME_BY_CYCLES.get(cycle_budget, self.DEFAULT_CYCLE_TIME)

        # Adjust based on complexity indicators in description
        description_lower = description.lower()

        # Complexity adjustments
        if any(kw in description_lower for kw in ["simple", "quick", "minor", "small"]):
            base_time = int(base_time * 0.7)
        elif any(kw in description_lower for kw in ["complex", "comprehensive", "full", "extensive"]):
            base_time = int(base_time * 1.3)
        elif any(kw in description_lower for kw in ["refactor", "rewrite", "overhaul"]):
            base_time = int(base_time * 1.5)

        return base_time

    def sort_queue(self, queue_items: List[Dict]) -> List[Dict]:
        """
        Sort queue by priority, then by scheduled time, then by queued time.

        Sorting order:
        1. Priority (HIGH > NORMAL > LOW)
        2. Scheduled start time (earlier first, None = ready now)
        3. Original queue order (queued_at timestamp)
        """
        now = datetime.now()

        def sort_key(item: Dict) -> Tuple:
            priority = Priority.from_string(item.get("priority", "normal"))

            # Scheduled start: treat None as "now" (ready immediately)
            scheduled = item.get("scheduled_start")
            if scheduled:
                try:
                    scheduled_dt = datetime.fromisoformat(scheduled)
                except (ValueError, TypeError):
                    scheduled_dt = now
            else:
                scheduled_dt = now

            # Original queued time
            queued = item.get("queued_at", "")

            return (priority.weight(), scheduled_dt, queued)

        return sorted(queue_items, key=sort_key)

    def get_next_ready_item(self) -> Optional[QueueItem]:
        """
        Get the next queue item that is ready to run.

        An item is ready if:
        1. Queue is enabled AND not paused
        2. Scheduled start time has passed (or is None)
        3. Start condition is satisfied (or is None)
        4. All dependencies are satisfied
        """
        state = self._load_queue()

        if not state.enabled:
            return None

        if state.paused:
            return None

        now = datetime.now()

        for item_dict in state.queue:
            item = QueueItem.from_dict(item_dict)

            # Check scheduled start
            if item.scheduled_start:
                try:
                    scheduled_dt = datetime.fromisoformat(item.scheduled_start)
                    if scheduled_dt > now:
                        continue  # Not ready yet
                except (ValueError, TypeError):
                    pass  # Invalid date, treat as ready

            # Check start_condition
            if item.start_condition:
                if not self._evaluate_start_condition(item.start_condition):
                    continue  # Condition not satisfied

            # Check dependencies
            if item.depends_on:
                dep_status = self.check_dependency(item.depends_on)
                if dep_status == DependencyStatus.WAITING:
                    continue  # Dependency not complete
                elif dep_status == DependencyStatus.BLOCKED:
                    logger.warning(f"Queue item {item.id} blocked: dependency {item.depends_on} failed")
                    continue

            return item

        return None

    def _evaluate_start_condition(self, condition: str) -> bool:
        """
        Evaluate a start condition expression.

        Supported conditions:
        - "idle_after:HH:MM" - After specified time AND RDE is idle
        - "at:YYYY-MM-DDTHH:MM" - At exact datetime
        - "after_mission:mission_id" - After specific mission completes
        """
        if not condition:
            return True

        now = datetime.now()

        try:
            if condition.startswith("idle_after:"):
                # Format: idle_after:17:00
                time_str = condition.split(":", 1)[1]
                hour, minute = map(int, time_str.split(":"))
                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # If target time is in the past today, it's already satisfied (for idle_after)
                if now < target_time:
                    return False

                # Also check if RDE is idle
                return self._is_rde_idle()

            elif condition.startswith("at:"):
                # Format: at:2026-01-02T17:00
                dt_str = condition.split(":", 1)[1]
                target_dt = datetime.fromisoformat(dt_str)
                return now >= target_dt

            elif condition.startswith("after_mission:"):
                # Format: after_mission:mission_abc123
                mission_id = condition.split(":", 1)[1]
                dep_status = self.check_dependency(mission_id)
                return dep_status == DependencyStatus.READY

        except Exception as e:
            logger.warning(f"Failed to parse start_condition '{condition}': {e}")

        return True  # Default to ready if condition can't be parsed

    def _is_rde_idle(self) -> bool:
        """Check if RDE is currently idle (not running a mission)."""
        if self.io_utils:
            current = self.io_utils.atomic_read_json(MISSION_PATH, {})
        else:
            try:
                with open(MISSION_PATH, 'r') as f:
                    current = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return True  # No mission file = idle

        stage = current.get("current_stage", "")
        return stage in ("COMPLETE", "", None)

    def check_dependency(self, mission_id: str) -> DependencyStatus:
        """
        Check if a dependency mission has completed successfully.

        Returns:
            DependencyStatus indicating if dependency is satisfied
        """
        # Check mission logs for completed missions
        if MISSION_LOGS_DIR.exists():
            for log_file in MISSION_LOGS_DIR.glob(f"{mission_id}*.json"):
                try:
                    with open(log_file, 'r') as f:
                        log_data = json.load(f)

                    # Check if mission completed successfully
                    final_stage = log_data.get("final_stage", "")
                    if final_stage == "COMPLETE":
                        return DependencyStatus.READY
                    elif final_stage in ("FAILED", "ABORTED"):
                        return DependencyStatus.BLOCKED

                    return DependencyStatus.READY  # Has a log = completed
                except Exception:
                    pass

        # Check current mission state
        if self.io_utils:
            current = self.io_utils.atomic_read_json(MISSION_PATH, {})
        else:
            try:
                with open(MISSION_PATH, 'r') as f:
                    current = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                current = {}

        if current.get("mission_id") == mission_id:
            stage = current.get("current_stage", "")
            if stage == "COMPLETE":
                return DependencyStatus.READY
            else:
                return DependencyStatus.WAITING

        return DependencyStatus.NOT_FOUND

    def remove_item(self, queue_id: str) -> bool:
        """Remove an item from the queue by ID."""
        state = self._load_queue()
        original_len = len(state.queue)
        state.queue = [q for q in state.queue if q.get("id") != queue_id]

        if len(state.queue) < original_len:
            self._save_queue(state)
            return True
        return False

    def update_item(self, queue_id: str, updates: Dict) -> Optional[QueueItem]:
        """
        Update a queue item's properties.

        Args:
            queue_id: ID of queue item to update
            updates: Dict of fields to update (priority, scheduled_start, depends_on, etc.)

        Returns:
            Updated QueueItem or None if not found
        """
        state = self._load_queue()

        for i, item in enumerate(state.queue):
            if item.get("id") == queue_id:
                # Apply updates
                for key, value in updates.items():
                    if key in ["priority", "scheduled_start", "depends_on",
                              "estimated_minutes", "tags", "mission_title",
                              "mission_description", "cycle_budget"]:
                        item[key] = value

                # Re-sort queue
                state.queue = self.sort_queue(state.queue)
                self._save_queue(state)

                return QueueItem.from_dict(item)

        return None

    def reorder_queue(self, new_order: List[str]) -> bool:
        """
        Reorder queue by list of IDs.

        Note: This manual reordering will be overwritten by priority sort
        unless priorities are equal.
        """
        state = self._load_queue()
        id_to_item = {q["id"]: q for q in state.queue}

        reordered = []
        for qid in new_order:
            if qid in id_to_item:
                reordered.append(id_to_item[qid])
                del id_to_item[qid]

        # Append any remaining items
        for remaining in id_to_item.values():
            reordered.append(remaining)

        state.queue = reordered
        self._save_queue(state)
        return True

    def clear_queue(self) -> int:
        """Clear all items from queue. Returns count of removed items."""
        state = self._load_queue()
        count = len(state.queue)
        state.queue = []
        self._save_queue(state)
        return count

    def toggle_enabled(self, enabled: Optional[bool] = None) -> bool:
        """Toggle or set queue enabled state. Returns new state."""
        state = self._load_queue()
        if enabled is None:
            state.enabled = not state.enabled
        else:
            state.enabled = enabled
        self._save_queue(state)
        return state.enabled

    def get_queue_timeline(self) -> List[Dict]:
        """
        Generate timeline data for Gantt-style visualization.

        Returns list of items with estimated start/end times.
        """
        state = self._load_queue()
        timeline = []

        # Start from now or scheduled time
        now = datetime.now()
        current_time = now

        # Sort queue for accurate timeline
        sorted_queue = self.sort_queue(state.queue)

        for item_dict in sorted_queue:
            item = QueueItem.from_dict(item_dict)

            # Determine start time
            start_time = current_time

            if item.scheduled_start:
                try:
                    scheduled_dt = datetime.fromisoformat(item.scheduled_start)
                    if scheduled_dt > start_time:
                        start_time = scheduled_dt
                except (ValueError, TypeError):
                    pass

            # Calculate end time
            duration_minutes = item.estimated_minutes or self.DEFAULT_CYCLE_TIME
            end_time = start_time + timedelta(minutes=duration_minutes)

            # Check dependency status
            dep_status = None
            if item.depends_on:
                dep_status = self.check_dependency(item.depends_on).value

            timeline.append({
                "id": item.id,
                "mission_title": item.mission_title,
                "priority": item.priority,
                "estimated_start": start_time.isoformat(),
                "estimated_end": end_time.isoformat(),
                "duration_minutes": duration_minutes,
                "scheduled_start": item.scheduled_start,
                "depends_on": item.depends_on,
                "dependency_status": dep_status,
                "is_ready": self._is_item_ready(item),
                "cycle_budget": item.cycle_budget
            })

            # Next item starts after this one ends
            current_time = end_time

        return timeline

    def _is_item_ready(self, item: QueueItem) -> bool:
        """Check if a queue item is ready to run now."""
        now = datetime.now()

        # Check scheduled start
        if item.scheduled_start:
            try:
                scheduled_dt = datetime.fromisoformat(item.scheduled_start)
                if scheduled_dt > now:
                    return False
            except (ValueError, TypeError):
                pass

        # Check dependencies
        if item.depends_on:
            dep_status = self.check_dependency(item.depends_on)
            if dep_status != DependencyStatus.READY:
                return False

        return True

    def get_statistics(self) -> Dict:
        """Get queue statistics for dashboard display."""
        state = self._load_queue()

        # Count by priority
        priority_counts = {"high": 0, "normal": 0, "low": 0}
        for item in state.queue:
            priority = item.get("priority", "normal")
            if priority in priority_counts:
                priority_counts[priority] += 1

        # Count ready vs waiting
        ready_count = 0
        waiting_count = 0
        blocked_count = 0

        now = datetime.now()
        for item_dict in state.queue:
            item = QueueItem.from_dict(item_dict)

            # Check if waiting on schedule
            if item.scheduled_start:
                try:
                    if datetime.fromisoformat(item.scheduled_start) > now:
                        waiting_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # Check if waiting on dependency
            if item.depends_on:
                dep_status = self.check_dependency(item.depends_on)
                if dep_status == DependencyStatus.WAITING:
                    waiting_count += 1
                    continue
                elif dep_status == DependencyStatus.BLOCKED:
                    blocked_count += 1
                    continue

            ready_count += 1

        # Calculate total estimated time
        total_minutes = sum(
            item.get("estimated_minutes", self.DEFAULT_CYCLE_TIME)
            for item in state.queue
        )

        return {
            "total_items": len(state.queue),
            "enabled": state.enabled,
            "paused": state.paused,
            "paused_at": state.paused_at,
            "pause_reason": state.pause_reason,
            "by_priority": priority_counts,
            "ready": ready_count,
            "waiting": waiting_count,
            "blocked": blocked_count,
            "total_estimated_minutes": total_minutes,
            "total_estimated_hours": round(total_minutes / 60, 1)
        }

    # =========================================================================
    # PAUSE/RESUME FUNCTIONALITY
    # =========================================================================

    def pause_queue(self, reason: Optional[str] = None) -> Dict:
        """
        Pause the queue. No new missions will start until resumed.

        Args:
            reason: Optional reason for pausing

        Returns:
            Dict with new pause state
        """
        state = self._load_queue()
        state.paused = True
        state.paused_at = datetime.now().isoformat()
        state.pause_reason = reason or "Manually paused"
        self._save_queue(state)

        logger.info(f"Queue paused: {state.pause_reason}")

        return {
            "paused": True,
            "paused_at": state.paused_at,
            "pause_reason": state.pause_reason
        }

    def resume_queue(self) -> Dict:
        """
        Resume the queue after being paused.

        Returns:
            Dict with new pause state
        """
        state = self._load_queue()
        was_paused = state.paused
        paused_at = state.paused_at

        state.paused = False
        state.paused_at = None
        state.pause_reason = None
        self._save_queue(state)

        if was_paused:
            logger.info(f"Queue resumed (was paused since {paused_at})")

        return {
            "paused": False,
            "was_paused": was_paused,
            "resumed_at": datetime.now().isoformat()
        }

    def get_pause_state(self) -> Dict:
        """Get current pause state."""
        state = self._load_queue()
        return {
            "paused": state.paused,
            "paused_at": state.paused_at,
            "pause_reason": state.pause_reason
        }

    # =========================================================================
    # HISTORICAL DURATION ESTIMATION
    # =========================================================================

    def estimate_duration_from_history(self, description: str, cycle_budget: int) -> int:
        """
        Estimate mission duration based on historical data from completed missions.

        Uses mission_analytics.py data and mission logs to calculate average
        duration per cycle, then applies keyword-based adjustments.

        Args:
            description: Mission description for keyword analysis
            cycle_budget: Number of cycles budgeted

        Returns:
            Estimated duration in minutes
        """
        # Start with base estimate
        base_minutes = self.CYCLE_TIME_BY_CYCLES.get(cycle_budget, self.DEFAULT_CYCLE_TIME)

        # Try to get historical data
        try:
            from mission_analytics import get_analytics
            analytics = get_analytics()

            # Get recent missions for average calculation
            recent = analytics.get_recent_missions(limit=20)

            if recent:
                # Calculate average duration per cycle from completed missions
                durations = []
                for m in recent:
                    if m.get("cycles", 0) > 0 and m.get("duration_seconds", 0) > 0:
                        minutes_per_cycle = (m["duration_seconds"] / 60) / m["cycles"]
                        durations.append(minutes_per_cycle)

                if durations:
                    avg_per_cycle = sum(durations) / len(durations)
                    base_minutes = int(avg_per_cycle * cycle_budget)
                    logger.debug(f"Using historical avg: {avg_per_cycle:.1f} min/cycle")

        except Exception as e:
            logger.debug(f"Could not get historical data: {e}")

        # Also check mission logs for more accurate data
        try:
            completed_durations = []
            if MISSION_LOGS_DIR.exists():
                for log_file in sorted(MISSION_LOGS_DIR.glob("*.json"), reverse=True)[:30]:
                    try:
                        with open(log_file, 'r') as f:
                            log_data = json.load(f)

                        if log_data.get("final_stage") == "COMPLETE":
                            cycles = log_data.get("total_cycles", 1)
                            duration_sec = log_data.get("total_duration_seconds", 0)
                            if cycles > 0 and duration_sec > 0:
                                completed_durations.append(duration_sec / 60 / cycles)
                    except Exception:
                        continue

            if completed_durations:
                avg_log_per_cycle = sum(completed_durations) / len(completed_durations)
                # Blend with analytics-based estimate
                base_minutes = int((base_minutes + avg_log_per_cycle * cycle_budget) / 2)

        except Exception:
            pass

        # Apply keyword adjustments
        description_lower = description.lower()

        if any(kw in description_lower for kw in ["simple", "quick", "minor", "small", "typo", "fix"]):
            base_minutes = int(base_minutes * 0.7)
        elif any(kw in description_lower for kw in ["complex", "comprehensive", "full", "extensive", "rewrite"]):
            base_minutes = int(base_minutes * 1.4)
        elif any(kw in description_lower for kw in ["refactor", "overhaul", "redesign", "migrate"]):
            base_minutes = int(base_minutes * 1.5)

        # Ensure reasonable bounds
        return max(15, min(300, base_minutes))

    # =========================================================================
    # DEPENDENCY SUGGESTIONS WITH CONFIDENCE SCORING
    # =========================================================================

    # Confidence threshold for showing suggestions
    CONFIDENCE_THRESHOLD = 0.6

    # Create→Use relationship keywords
    CREATE_VERBS = {"add", "create", "implement", "build", "write", "introduce", "setup", "initialize", "define", "establish"}
    USE_VERBS = {"use", "extend", "modify", "update", "integrate", "enhance", "improve", "refactor", "fix", "test", "validate"}
    DEPEND_PATTERNS = {"depends on", "requires", "needs", "after", "following", "builds on", "extends", "based on"}

    def suggest_dependencies(self) -> List[Dict]:
        """
        Analyze queue items to suggest dependency ordering with confidence scoring.

        Uses multiple signals to determine dependency confidence:
        1. Create→Use relationships (high confidence: 0.5 base)
        2. Shared entity/file references (medium confidence: 0.1 per match, max 0.3)
        3. Sequential keyword patterns (low confidence: 0.2)
        4. Explicit dependency markers in text (high confidence: 0.4)

        Only returns suggestions with confidence >= CONFIDENCE_THRESHOLD (0.6)

        Returns:
            List of suggestion dicts with mission pairs, reasons, and confidence scores
        """
        state = self._load_queue()
        suggestions = []

        if len(state.queue) < 2:
            return suggestions

        # Build a map of queue items by ID
        queue_items = [QueueItem.from_dict(q) for q in state.queue]

        # Analyze each pair for potential dependencies
        for i, item_a in enumerate(queue_items):
            for j, item_b in enumerate(queue_items):
                if i >= j:
                    continue

                # Calculate confidence that A should come before B
                confidence, reasons = self._calculate_dependency_confidence(
                    item_a.mission_description,
                    item_b.mission_description
                )

                # Only include if above threshold
                if confidence >= self.CONFIDENCE_THRESHOLD:
                    suggestions.append({
                        "mission_a": item_a.id,
                        "mission_a_title": item_a.mission_title,
                        "mission_b": item_b.id,
                        "mission_b_title": item_b.mission_title,
                        "reason": "; ".join(reasons) if reasons else "Detected dependency pattern",
                        "confidence": round(confidence, 2),
                        "confidence_label": self._get_confidence_label(confidence),
                        "suggested_order": [item_a.id, item_b.id],
                        "suggestion_type": "algorithm"
                    })

        # Sort by confidence (highest first)
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        return suggestions

    def _calculate_dependency_confidence(self, mission_a: str, mission_b: str) -> Tuple[float, List[str]]:
        """
        Calculate confidence score for A→B dependency.

        Returns:
            Tuple of (confidence_score, list_of_reasons)
        """
        score = 0.0
        reasons = []

        a_lower = mission_a.lower()
        b_lower = mission_b.lower()

        # 1. Check for create→use relationship (high weight: 0.5)
        create_use_score, create_use_items = self._check_create_use_relationship(a_lower, b_lower)
        if create_use_score > 0:
            score += create_use_score
            reasons.append(f"A creates what B uses: {', '.join(list(create_use_items)[:2])}")

        # 2. Check shared entity/file references (medium weight: up to 0.3)
        shared_refs = self._find_shared_references(a_lower, b_lower)
        if shared_refs:
            ref_score = min(0.3, len(shared_refs) * 0.1)
            score += ref_score
            reasons.append(f"Shared references: {', '.join(list(shared_refs)[:3])}")

        # 3. Check for sequential keyword patterns (low weight: 0.2)
        if self._has_sequential_keywords(a_lower, b_lower):
            score += 0.2
            reasons.append("Sequential pattern detected (setup→extend)")

        # 4. Check for explicit dependency markers (high weight: 0.4)
        explicit_dep = self._check_explicit_dependency_markers(a_lower, b_lower)
        if explicit_dep:
            score += 0.4
            reasons.append(f"Explicit dependency: {explicit_dep}")

        return min(1.0, score), reasons

    def _check_create_use_relationship(self, text_a: str, text_b: str) -> Tuple[float, set]:
        """
        Check if A creates something that B uses.

        Returns:
            Tuple of (score, set of shared items)
        """
        import re

        # Extract key nouns (removing common words)
        stop_words = {"a", "an", "the", "to", "for", "and", "or", "new", "with", "in", "on", "at", "by", "of"}

        # Extract what A creates - look for nouns after create verbs
        a_creates = set()
        for verb in self.CREATE_VERBS:
            # Match: verb + optional article + noun
            pattern = rf'\b{verb}\s+(?:a\s+|an\s+|the\s+|new\s+)?(\w+)'
            matches = re.findall(pattern, text_a)
            a_creates.update(m.lower() for m in matches if len(m) > 2 and m.lower() not in stop_words)

        # Extract what B uses/modifies
        b_uses = set()
        for verb in self.USE_VERBS:
            pattern = rf'\b{verb}\s+(?:a\s+|an\s+|the\s+|new\s+)?(\w+)'
            matches = re.findall(pattern, text_b)
            b_uses.update(m.lower() for m in matches if len(m) > 2 and m.lower() not in stop_words)

        # Find direct overlap
        common = a_creates & b_uses
        if common:
            return 0.5, common

        # Also check for significant noun overlap (longer words more likely meaningful)
        noun_pattern = r'\b([A-Za-z]{5,})\b'
        a_nouns = set(m.lower() for m in re.findall(noun_pattern, text_a) if m.lower() not in stop_words)
        b_nouns = set(m.lower() for m in re.findall(noun_pattern, text_b) if m.lower() not in stop_words)

        overlap = a_nouns & b_nouns
        # Filter to more significant words
        significant_overlap = set(w for w in overlap if len(w) >= 6)

        if significant_overlap:
            return 0.3, significant_overlap

        return 0.0, set()

    def _find_shared_references(self, text_a: str, text_b: str) -> set:
        """
        Find shared file/module/component references between two texts.
        """
        import re

        # Extract file references
        file_pattern = r'\b[\w/]+\.(?:py|js|ts|tsx|jsx|css|html|json|md)\b'
        files_a = set(re.findall(file_pattern, text_a, re.IGNORECASE))
        files_b = set(re.findall(file_pattern, text_b, re.IGNORECASE))

        # Extract module/component names (CamelCase or snake_case identifiers)
        module_pattern = r'\b(?:[A-Z][a-zA-Z]+|[a-z]+_[a-z]+(?:_[a-z]+)*)\b'
        modules_a = set(m for m in re.findall(module_pattern, text_a) if len(m) > 3)
        modules_b = set(m for m in re.findall(module_pattern, text_b) if len(m) > 3)

        # Common noise words to filter
        noise = {"that", "this", "with", "from", "into", "which", "their", "there", "should", "could", "would"}

        shared_files = files_a & files_b
        shared_modules = (modules_a & modules_b) - noise

        return shared_files | shared_modules

    def _has_sequential_keywords(self, text_a: str, text_b: str) -> bool:
        """
        Check if A and B have keywords suggesting sequential execution.

        E.g., A has "setup/create/init" and B has "extend/build upon/after"
        """
        # A should be foundational
        a_is_foundational = any(kw in text_a for kw in [
            "setup", "initial", "foundation", "base", "core",
            "create", "implement", "add new", "introduce", "establish"
        ])

        # B should be dependent
        b_is_dependent = any(kw in text_b for kw in [
            "extend", "build on", "enhance", "improve", "after",
            "follow up", "continuation", "based on", "using the"
        ])

        return a_is_foundational and b_is_dependent

    def _check_explicit_dependency_markers(self, text_a: str, text_b: str) -> Optional[str]:
        """
        Check if B explicitly mentions depending on something A creates.
        """
        import re

        # Look for explicit dependency phrases in B
        for pattern in self.DEPEND_PATTERNS:
            if pattern in text_b:
                # See if what it depends on is mentioned in A
                # Pattern: "depends on X" - check if X is in A
                match = re.search(rf'{pattern}\s+(?:the\s+)?(\w+(?:\s+\w+)?)', text_b)
                if match:
                    dependent_item = match.group(1).lower()
                    if dependent_item in text_a:
                        return f"'{dependent_item}' in A, B {pattern} it"

        return None

    def _get_confidence_label(self, confidence: float) -> str:
        """Get human-readable confidence label."""
        if confidence >= 0.8:
            return "high"
        elif confidence >= 0.6:
            return "medium"
        else:
            return "low"

    def _extract_file_references(self, text: str) -> List[str]:
        """Extract file path references from text."""
        import re
        # Match common file patterns
        patterns = [
            r'\b[\w/]+\.(?:py|js|ts|tsx|jsx|css|html|json|md)\b',
            r'\bsrc/[\w/]+\b',
            r'\b(?:dashboard|workspace|mission)_[\w]+\b'
        ]

        refs = []
        for pattern in patterns:
            refs.extend(re.findall(pattern, text, re.IGNORECASE))

        return list(set(refs))

    def _extract_creation_targets(self, text: str) -> set:
        """Extract what a mission intends to create/add."""
        targets = set()

        create_patterns = [
            r'(?:add|create|implement|build|write)\s+(?:a\s+)?(\w+)',
            r'new\s+(\w+)\s+(?:feature|module|component|system)',
        ]

        import re
        for pattern in create_patterns:
            matches = re.findall(pattern, text)
            targets.update(match.lower() for match in matches if len(match) > 2)

        return targets

    def _extract_usage_targets(self, text: str) -> set:
        """Extract what a mission intends to use/modify."""
        targets = set()

        use_patterns = [
            r'(?:use|extend|modify|update|integrate with)\s+(?:the\s+)?(\w+)',
            r'(?:depends on|requires)\s+(\w+)',
        ]

        import re
        for pattern in use_patterns:
            matches = re.findall(pattern, text)
            targets.update(match.lower() for match in matches if len(match) > 2)

        return targets

    def apply_suggestion(self, suggestion: Dict) -> bool:
        """
        Apply a dependency suggestion by reordering the queue.

        Args:
            suggestion: Suggestion dict with mission_a, mission_b

        Returns:
            True if reorder was applied
        """
        mission_a_id = suggestion.get("mission_a")
        mission_b_id = suggestion.get("mission_b")

        if not mission_a_id or not mission_b_id:
            return False

        state = self._load_queue()
        queue_ids = [q["id"] for q in state.queue]

        idx_a = queue_ids.index(mission_a_id) if mission_a_id in queue_ids else -1
        idx_b = queue_ids.index(mission_b_id) if mission_b_id in queue_ids else -1

        if idx_a < 0 or idx_b < 0:
            return False

        # If A should come before B but currently comes after, swap
        if idx_a > idx_b:
            # Move A to just before B
            item_a = state.queue.pop(idx_a)
            state.queue.insert(idx_b, item_a)
            self._save_queue(state)
            return True

        return False


# Singleton instance
_scheduler_instance: Optional[MissionQueueScheduler] = None


def get_scheduler() -> MissionQueueScheduler:
    """Get singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = MissionQueueScheduler()
    return _scheduler_instance


# Convenience functions for external use
def add_to_queue(
    mission_title: str,
    mission_description: str,
    cycle_budget: int = 3,
    priority: str = "normal",
    scheduled_start: Optional[str] = None,
    depends_on: Optional[str] = None,
    **kwargs
) -> Tuple[QueueItem, int]:
    """Add item to queue. Returns (QueueItem, position)."""
    return get_scheduler().add_to_queue(
        mission_title=mission_title,
        mission_description=mission_description,
        cycle_budget=cycle_budget,
        priority=priority,
        scheduled_start=scheduled_start,
        depends_on=depends_on,
        **kwargs
    )


def get_queue_timeline() -> List[Dict]:
    """Get queue timeline for Gantt visualization."""
    return get_scheduler().get_queue_timeline()


def get_queue_statistics() -> Dict:
    """Get queue statistics."""
    return get_scheduler().get_statistics()


def check_dependency_status(mission_id: str) -> str:
    """Check dependency status. Returns status string."""
    return get_scheduler().check_dependency(mission_id).value


def pause_queue(reason: Optional[str] = None) -> Dict:
    """Pause the queue."""
    return get_scheduler().pause_queue(reason)


def resume_queue() -> Dict:
    """Resume the queue."""
    return get_scheduler().resume_queue()


def get_pause_state() -> Dict:
    """Get current pause state."""
    return get_scheduler().get_pause_state()


def get_dependency_suggestions() -> List[Dict]:
    """Get dependency suggestions for queue reordering."""
    return get_scheduler().suggest_dependencies()


def apply_dependency_suggestion(suggestion: Dict) -> bool:
    """Apply a dependency suggestion."""
    return get_scheduler().apply_suggestion(suggestion)


def estimate_duration(description: str, cycle_budget: int) -> int:
    """Estimate mission duration from historical data."""
    return get_scheduler().estimate_duration_from_history(description, cycle_budget)
