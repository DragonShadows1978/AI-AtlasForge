"""
Subagent Pool Manager — Dynamic Resource-Aware Subagent Allocation
==================================================================

Provides intelligent, fair-share allocation of subagent slots across
concurrent investigations. Features:

1. ResourceDetector: auto-detects CPU/RAM/GPU and suggests optimal counts
2. SubagentPoolManager (singleton): semaphore-backed pool with priority queue
3. AllocationLearner: persists outcomes to MissionKnowledgeBase for learning

Architecture:
- Pool size: 50 slots (matches dashboard cap)
- Monopoly cap: 60% of pool per investigation when others are sharing
- Fair-share quota: pool_size // active_investigation_count
- Priority levels: 0=CRITICAL, 5=HIGH, 10=NORMAL, 20=LOW
- State persisted to state/pool_state.json for cross-process visibility

Usage:
    from subagent_pool_manager import get_pool_manager, get_resource_detector

    pool = get_pool_manager()
    slot_req = pool.request_slots("inv_abc", count=8, priority=10, query="Research X")
    # ... run slot_req.granted subagents ...
    pool.release_slots("inv_abc", 1)  # per completed subagent
    pool.notify_investigation_complete("inv_abc", elapsed_sec=45.0, success=True)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger("subagent_pool_manager")

# ---------------------------------------------------------------------------
# Path bootstrap — same pattern as investigation_engine.py
# ---------------------------------------------------------------------------
try:
    from atlasforge_config import BASE_DIR, STATE_DIR  # type: ignore
except ImportError:
    BASE_DIR = Path(__file__).parent
    STATE_DIR = BASE_DIR / "state"

STATE_DIR.mkdir(exist_ok=True)

POOL_STATE_FILE = STATE_DIR / "pool_state.json"

# ---------------------------------------------------------------------------
# Priority Labels
# ---------------------------------------------------------------------------
PRIORITY_LABELS = {
    0: "CRITICAL",
    5: "High",
    10: "Normal",
    20: "Low",
}


def _priority_label(priority: int) -> str:
    """Return a human-readable label for a numeric priority."""
    for threshold, label in sorted(PRIORITY_LABELS.items()):
        if priority <= threshold:
            return label
    return "Low"


# ===========================================================================
# Dataclasses
# ===========================================================================

@dataclass
class ResourceProfile:
    """Snapshot of available system resources."""
    cpu_threads: int
    available_memory_gb: float
    total_memory_gb: float
    cpu_percent: float
    gpu_available: bool
    gpu_vram_free_mb: int
    gpu_model: str


@dataclass
class SuggestedAllocation:
    """Resource-based subagent count recommendation."""
    suggested: int
    min_suggested: int
    max_suggested: int
    rationale: str
    resource_profile: ResourceProfile

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resource_profile"] = asdict(self.resource_profile)
        return d


@dataclass
class SlotRequest:
    """Result of a pool slot request."""
    investigation_id: str
    requested: int
    granted: int
    queued: int
    denied: int
    quota_limit: int
    reason: str  # granted_full | partial_quota | partial_pool | queued | none_available


@dataclass
class InvestigationSlotTracker:
    """Tracks slot allocation for a single active investigation."""
    investigation_id: str
    priority: int
    requested_slots: int
    active_slots: int = 0
    queued_slots: int = 0
    allocated_quota: int = 0
    started_at: float = field(default_factory=time.time)
    query_preview: str = ""

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "priority": self.priority,
            "priority_label": _priority_label(self.priority),
            "requested_slots": self.requested_slots,
            "active_slots": self.active_slots,
            "queued_slots": self.queued_slots,
            "allocated_quota": self.allocated_quota,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(),
            "query_preview": self.query_preview,
        }


@dataclass
class InvestigationSlotStatus:
    """External-facing status for a single investigation in the pool."""
    investigation_id: str
    query_preview: str
    priority_label: str
    active: int
    queued: int
    quota: int
    started_at: str


@dataclass
class PoolStatus:
    """Live snapshot of the entire pool."""
    total_slots: int
    active_slots: int
    idle_slots: int
    active_investigations: int
    investigations: List[InvestigationSlotStatus]
    timestamp: str
    utilization_history: List[Tuple[float, int]] = field(default_factory=list)
    throughput: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_slots": self.total_slots,
            "active_slots": self.active_slots,
            "idle_slots": self.idle_slots,
            "active_investigations": self.active_investigations,
            "investigations": [
                {
                    "investigation_id": inv.investigation_id,
                    "query_preview": inv.query_preview,
                    "priority_label": inv.priority_label,
                    "active": inv.active,
                    "queued": inv.queued,
                    "quota": inv.quota,
                    "started_at": inv.started_at,
                }
                for inv in self.investigations
            ],
            "utilization_history": self.utilization_history,
            "throughput": self.throughput,
            "timestamp": self.timestamp,
        }


# ===========================================================================
# ResourceDetector
# ===========================================================================

class ResourceDetector:
    """
    Detects available system resources and suggests an optimal subagent count.

    LLM subagents are I/O-bound (network calls to API), so the formula is:
        base = min(cpu_threads * 2, 20)
        memory_cap = int(available_gb / 2)    # 2 GB headroom per subagent
        gpu_bonus = 2 if gpu_available else 0
        suggested = min(base + gpu_bonus, memory_cap, 50)
    """

    GPU_BONUS = 2  # bonus slots when a GPU/Ollama is available

    def detect(self) -> ResourceProfile:
        """Return a ResourceProfile from live system introspection."""
        cpu_threads = os.cpu_count() or 4
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        cpu_percent = psutil.cpu_percent(interval=0.1)

        gpu_available, gpu_vram_free_mb, gpu_model = self._detect_gpu()

        return ResourceProfile(
            cpu_threads=cpu_threads,
            available_memory_gb=round(available_gb, 2),
            total_memory_gb=round(total_gb, 2),
            cpu_percent=round(cpu_percent, 1),
            gpu_available=gpu_available,
            gpu_vram_free_mb=gpu_vram_free_mb,
            gpu_model=gpu_model,
        )

    def _detect_gpu(self) -> tuple:
        """Try nvidia-smi to get GPU info. Returns (available, vram_free_mb, model)."""
        try:
            import subprocess
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().splitlines()
                parts = lines[0].split(",")
                if len(parts) >= 2:
                    gpu_model = parts[0].strip()
                    vram_free = int(parts[1].strip())
                    return True, vram_free, gpu_model
        except Exception:
            pass

        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                idx = torch.cuda.current_device()
                free, _ = torch.cuda.mem_get_info(idx)
                model = torch.cuda.get_device_name(idx)
                return True, free // (1024 * 1024), model
        except Exception:
            pass

        return False, 0, ""

    def suggest_optimal_count(
        self, profile: Optional[ResourceProfile] = None, query: str = ""
    ) -> SuggestedAllocation:
        """
        Suggest the optimal number of subagents based on available resources.

        Args:
            profile: Pre-detected ResourceProfile (detect() is called if None)
            query: Query string (used for rationale annotation only)

        Returns:
            SuggestedAllocation with suggested count, range, and rationale
        """
        if profile is None:
            profile = self.detect()

        # I/O-bound formula: CPU threads x 2, capped at 20
        io_bound_base = min(profile.cpu_threads * 2, 20)

        # Memory cap: assume each subagent needs ~2 GB headroom
        memory_cap = max(2, int(profile.available_memory_gb / 2))

        # GPU bonus for Ollama / local model use
        gpu_bonus = self.GPU_BONUS if profile.gpu_available else 0

        suggested_raw = io_bound_base + gpu_bonus

        # Apply caps
        suggested = min(suggested_raw, memory_cap, 50)
        suggested = max(suggested, 2)  # minimum 2

        min_suggested = max(2, suggested // 2)
        max_suggested = min(50, suggested * 2)

        # Build rationale string
        parts = [
            f"{profile.cpu_threads} CPU threads x2 (I/O-bound) = {io_bound_base}",
        ]
        if gpu_bonus:
            parts.append(f"+{gpu_bonus} GPU bonus (local model available)")
        parts.append(
            f"capped by {profile.available_memory_gb:.1f}GB RAM -> {suggested} subagents"
        )
        rationale = ", ".join(parts)

        return SuggestedAllocation(
            suggested=suggested,
            min_suggested=min_suggested,
            max_suggested=max_suggested,
            rationale=rationale,
            resource_profile=profile,
        )


# ===========================================================================
# AllocationLearner
# ===========================================================================

class AllocationLearner:
    """
    Persists allocation outcomes to MissionKnowledgeBase so the system learns
    which subagent counts work best for different query types over time.
    """

    DOMAIN = "subagent_allocation"
    LEARNING_TYPE = "technique"

    def record_allocation(
        self,
        inv_id: str,
        query: str,
        granted: int,
        elapsed_sec: float,
        success: bool,
        cpu_threads: int = 0,
        mem_gb: float = 0.0,
    ) -> None:
        """Store an allocation outcome to the KB."""
        try:
            from mission_knowledge_base import MissionKnowledgeBase, MissionLearning  # type: ignore

            kb = MissionKnowledgeBase()
            category = self._categorize_query(query)
            outcome = "success" if success else "failure"

            learning = MissionLearning(
                learning_id=f"pool_alloc_{inv_id}_{int(time.time())}",
                mission_id="pool_manager",
                learning_type=self.LEARNING_TYPE,
                title=f"Pool allocation: {query[:60]}",
                description=(
                    f"query_type={category} count={granted} "
                    f"elapsed={elapsed_sec:.0f}s success={success} "
                    f"cpu={cpu_threads}cores mem={mem_gb:.1f}GB"
                ),
                problem_domain=self.DOMAIN,
                outcome=outcome,
                relevance_keywords=["subagent", "allocation", "pool", category],
                lesson_source="pool_manager",
                source_type="investigation",
                source_investigation_id=inv_id,
                investigation_query=query[:200],
            )
            kb._store_learning(learning)
            logger.debug(f"Recorded pool allocation for {inv_id} to KB")
        except Exception as exc:
            logger.warning(f"AllocationLearner.record_allocation failed: {exc}")

    def recommend_count(self, query: str) -> Optional[int]:
        """
        Query the KB for past successful allocations on similar queries.
        Returns the median successful count, or None if no history.
        """
        try:
            from mission_knowledge_base import MissionKnowledgeBase  # type: ignore

            kb = MissionKnowledgeBase()
            learnings = kb.query_relevant_learnings(
                problem_statement=f"subagent allocation {query}",
                top_k=10,
                learning_types=[self.LEARNING_TYPE],
            )

            counts = []
            for l in learnings:
                desc = l.get("description", "")
                if "subagent_allocation" not in l.get("problem_domain", ""):
                    continue
                if "success=True" not in desc:
                    continue
                for token in desc.split():
                    if token.startswith("count="):
                        try:
                            counts.append(int(token.split("=", 1)[1]))
                        except ValueError:
                            pass

            if not counts:
                return None

            counts.sort()
            median_count = counts[len(counts) // 2]
            return max(2, min(median_count, 50))
        except Exception as exc:
            logger.warning(f"AllocationLearner.recommend_count failed: {exc}")
            return None

    def get_history(self, limit: int = 20) -> List[dict]:
        """Return recent allocation history from KB."""
        try:
            from mission_knowledge_base import MissionKnowledgeBase  # type: ignore

            kb = MissionKnowledgeBase()
            learnings = kb.query_relevant_learnings(
                problem_statement="subagent pool allocation",
                top_k=limit,
                learning_types=[self.LEARNING_TYPE],
            )
            return [
                l for l in learnings
                if "subagent_allocation" in l.get("problem_domain", "")
            ]
        except Exception as exc:
            logger.warning(f"AllocationLearner.get_history failed: {exc}")
            return []

    def _categorize_query(self, query: str) -> str:
        """Rough categorization of query type for grouping."""
        q = query.lower()
        if any(k in q for k in ["code", "bug", "fix", "implement", "refactor"]):
            return "software"
        if any(k in q for k in ["research", "explain", "what", "how", "why"]):
            return "research"
        if any(k in q for k in ["game", "play", "bot", "strategy"]):
            return "gaming"
        if any(k in q for k in ["data", "csv", "analysis", "statistics"]):
            return "data_analysis"
        return "general"


# ===========================================================================
# SubagentPoolManager
# ===========================================================================

class SubagentPoolManager:
    """
    Thread-safe singleton pool manager for investigation subagents.

    Maintains a semaphore of MAX_POOL_SIZE (50) slots shared across all
    concurrent investigations. Applies fair-share quotas and a per-investigation
    monopoly cap to prevent one investigation from consuming all slots.

    Key invariants:
    - _total_active tracks the number of semaphore units currently held
    - _investigations maps investigation_id -> InvestigationSlotTracker
    - Slots are acquired with a timeout (30s) to avoid deadlocks
    """

    MAX_POOL_SIZE: int = 50
    MONOPOLY_CAP_FRACTION: float = 0.6   # max 60% of pool per investigation
    ACQUIRE_TIMEOUT: float = 30.0         # max wait per slot acquisition

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pool_sem = threading.Semaphore(self.MAX_POOL_SIZE)
        self._investigations: Dict[str, InvestigationSlotTracker] = {}
        self._total_active: int = 0
        self._socketio = None  # set by set_socketio()
        self._learner = AllocationLearner()
        self._detector = ResourceDetector()
        # Ring buffers for sparkline / throughput (60-second window)
        self._utilization_history: deque = deque()  # (timestamp_float, active_slots)
        self._release_history: deque = deque()       # (timestamp_float, released_count)
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_slots(
        self,
        inv_id: str,
        count: int,
        priority: int = 10,
        query: str = "",
    ) -> SlotRequest:
        """
        Request `count` pool slots for investigation `inv_id`.

        Applies fair-share quota and monopoly cap.  Returns immediately with
        the number of slots granted (may be < requested).

        Args:
            inv_id: Unique investigation identifier
            count: Number of slots requested
            priority: 0=CRITICAL, 5=HIGH, 10=NORMAL, 20=LOW
            query: Query text (for display and KB learning)

        Returns:
            SlotRequest with granted/queued/denied counts and reason
        """
        count = max(1, int(count))

        with self._lock:
            if inv_id not in self._investigations:
                self._investigations[inv_id] = InvestigationSlotTracker(
                    investigation_id=inv_id,
                    priority=priority,
                    requested_slots=count,
                    query_preview=query[:80],
                )

            self._recalculate_quotas()
            tracker = self._investigations[inv_id]
            quota = tracker.allocated_quota
            monopoly_cap = int(self.MAX_POOL_SIZE * self.MONOPOLY_CAP_FRACTION)

            already_active = tracker.active_slots
            effective_max = min(quota, monopoly_cap) - already_active
            effective_max = max(0, effective_max)

            to_grant = min(count, effective_max)

        # Acquire slots from semaphore (outside _lock to avoid contention)
        granted = 0
        for _ in range(to_grant):
            acquired = self._pool_sem.acquire(blocking=True, timeout=self.ACQUIRE_TIMEOUT)
            if acquired:
                granted += 1
            else:
                logger.warning(f"[PoolManager] Semaphore acquire timeout for {inv_id}")
                break

        queued = max(0, count - granted)

        # Determine reason
        if granted == count:
            reason = "granted_full"
        elif granted > 0:
            reason = "partial_quota" if effective_max < count else "partial_pool"
        elif count > 0:
            reason = "queued"
            queued = count
        else:
            reason = "none_available"

        with self._lock:
            if inv_id in self._investigations:
                self._investigations[inv_id].active_slots += granted
                self._investigations[inv_id].queued_slots = queued
                self._total_active += granted

        self._save_state()
        self._emit_status()

        logger.info(
            f"[PoolManager] {inv_id}: requested={count} granted={granted} "
            f"queued={queued} quota={quota} reason={reason}"
        )
        return SlotRequest(
            investigation_id=inv_id,
            requested=count,
            granted=granted,
            queued=queued,
            denied=0,
            quota_limit=quota,
            reason=reason,
        )

    def release_slots(self, inv_id: str, count: int = 1) -> None:
        """
        Release `count` slots back to the pool for investigation `inv_id`.
        Should be called once per completed/failed subagent.
        """
        count = max(0, int(count))
        if count == 0:
            return

        with self._lock:
            if inv_id in self._investigations:
                tracker = self._investigations[inv_id]
                actual_release = min(count, tracker.active_slots)
                tracker.active_slots = max(0, tracker.active_slots - actual_release)
                self._total_active = max(0, self._total_active - actual_release)
                count = actual_release

        for _ in range(count):
            self._pool_sem.release()

        # Record release event for throughput calculation
        with self._lock:
            now = time.time()
            self._release_history.append((now, count))
            self._push_history_sample()

        self._save_state()
        self._emit_status()

    def notify_investigation_complete(
        self,
        inv_id: str,
        elapsed_sec: float = 0.0,
        success: bool = True,
    ) -> None:
        """
        Mark an investigation as complete, releasing all remaining slots
        and recording an allocation outcome to the KB.
        """
        query = ""
        granted = 0

        with self._lock:
            tracker = self._investigations.get(inv_id)
            if tracker:
                query = tracker.query_preview
                granted = tracker.requested_slots
                remaining_active = tracker.active_slots
                self._total_active = max(0, self._total_active - remaining_active)
                for _ in range(remaining_active):
                    self._pool_sem.release()
                del self._investigations[inv_id]

        def _record():
            try:
                profile = self._detector.detect()
                self._learner.record_allocation(
                    inv_id=inv_id,
                    query=query,
                    granted=granted,
                    elapsed_sec=elapsed_sec,
                    success=success,
                    cpu_threads=profile.cpu_threads,
                    mem_gb=profile.total_memory_gb,
                )
            except Exception as exc:
                logger.warning(f"[PoolManager] KB record failed for {inv_id}: {exc}")

        threading.Thread(target=_record, daemon=True).start()

        self._save_state()
        self._emit_status()
        logger.info(f"[PoolManager] Investigation {inv_id} complete, slots released")

    def get_status(self) -> PoolStatus:
        """Return a live snapshot of pool utilization."""
        with self._lock:
            self._push_history_sample()
            investigations = [
                InvestigationSlotStatus(
                    investigation_id=t.investigation_id,
                    query_preview=t.query_preview,
                    priority_label=_priority_label(t.priority),
                    active=t.active_slots,
                    queued=t.queued_slots,
                    quota=t.allocated_quota,
                    started_at=datetime.fromtimestamp(t.started_at).isoformat(),
                )
                for t in self._investigations.values()
            ]
            active = self._total_active
            idle = max(0, self.MAX_POOL_SIZE - active)
            history_snapshot = list(self._utilization_history)
            throughput = self._compute_throughput()

            return PoolStatus(
                total_slots=self.MAX_POOL_SIZE,
                active_slots=active,
                idle_slots=idle,
                active_investigations=len(self._investigations),
                investigations=investigations,
                timestamp=datetime.now().isoformat(),
                utilization_history=history_snapshot,
                throughput=throughput,
            )

    def set_socketio(self, socketio_instance) -> None:
        """Inject SocketIO instance for real-time event emission."""
        self._socketio = socketio_instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recalculate_quotas(self) -> None:
        """Recompute fair-share quotas for all active investigations."""
        n = len(self._investigations)
        if n == 0:
            return

        base_quota = max(2, self.MAX_POOL_SIZE // n)
        monopoly_cap = int(self.MAX_POOL_SIZE * self.MONOPOLY_CAP_FRACTION)

        for tracker in self._investigations.values():
            prio = tracker.priority
            if prio == 0:        # CRITICAL
                q = min(int(base_quota * 2), self.MAX_POOL_SIZE)
            elif prio <= 5:      # HIGH
                q = min(int(base_quota * 1.5), self.MAX_POOL_SIZE)
            else:                # NORMAL / LOW
                q = base_quota

            tracker.allocated_quota = min(q, monopoly_cap)

    def _save_state(self) -> None:
        """Atomically persist pool state to disk for cross-process visibility."""
        try:
            with self._lock:
                state = {
                    "total_slots": self.MAX_POOL_SIZE,
                    "active_slots": self._total_active,
                    "investigations": {
                        inv_id: tracker.to_dict()
                        for inv_id, tracker in self._investigations.items()
                    },
                    "last_updated": datetime.now().isoformat(),
                }
                # Use thread-id + object-id to prevent concurrent tmp-file collisions
                tmp_path = POOL_STATE_FILE.parent / f"pool_state_{id(self)}_{threading.get_ident()}.tmp"
                tmp_path.write_text(json.dumps(state, indent=2))
                tmp_path.replace(POOL_STATE_FILE)
        except Exception as exc:
            logger.warning(f"[PoolManager] Failed to save state: {exc}")

    def _load_state(self) -> None:
        """Load persisted state on startup. Slots are reset since semaphore is fresh."""
        if not POOL_STATE_FILE.exists():
            return

        try:
            data = json.loads(POOL_STATE_FILE.read_text())
            investigations = data.get("investigations", {})

            for inv_id, inv_data in investigations.items():
                self._investigations[inv_id] = InvestigationSlotTracker(
                    investigation_id=inv_id,
                    priority=inv_data.get("priority", 10),
                    requested_slots=inv_data.get("requested_slots", 0),
                    active_slots=0,   # reset on restart
                    queued_slots=0,
                    allocated_quota=inv_data.get("allocated_quota", 0),
                    started_at=time.time(),
                    query_preview=inv_data.get("query_preview", ""),
                )

            logger.info(
                f"[PoolManager] Loaded state: "
                f"{len(investigations)} investigations restored (slots reset)"
            )
        except Exception as exc:
            logger.warning(f"[PoolManager] Failed to load state: {exc}")

    def _push_history_sample(self) -> None:
        """Record current active slot count; trim entries older than 60s. MUST hold _lock."""
        now = time.time()
        self._utilization_history.append((now, self._total_active))
        cutoff = now - 60.0
        while self._utilization_history and self._utilization_history[0][0] < cutoff:
            self._utilization_history.popleft()
        # Also trim release history
        while self._release_history and self._release_history[0][0] < cutoff:
            self._release_history.popleft()

    def _compute_throughput(self) -> float:
        """Compute slots released per minute over the last 60s. MUST hold _lock."""
        now = time.time()
        cutoff = now - 60.0
        total_released = sum(
            count for ts, count in self._release_history if ts >= cutoff
        )
        return round(total_released, 1)  # slots per last 60s (= slots/min)

    def _emit_status(self) -> None:
        """Emit pool_status WebSocket event if SocketIO is configured."""
        if self._socketio is None:
            return
        try:
            status = self.get_status()
            self._socketio.emit("pool_status", status.to_dict(), room="pool_status")
        except Exception as exc:
            logger.debug(f"[PoolManager] SocketIO emit failed: {exc}")


# ===========================================================================
# Module-level singletons
# ===========================================================================

_pool_manager: Optional[SubagentPoolManager] = None
_pool_manager_lock = threading.Lock()

_resource_detector: Optional[ResourceDetector] = None
_detector_lock = threading.Lock()

_allocation_learner: Optional[AllocationLearner] = None
_learner_lock = threading.Lock()


def get_pool_manager() -> SubagentPoolManager:
    """Return (or create) the global pool manager singleton."""
    global _pool_manager
    if _pool_manager is None:
        with _pool_manager_lock:
            if _pool_manager is None:
                _pool_manager = SubagentPoolManager()
    return _pool_manager


def get_resource_detector() -> ResourceDetector:
    """Return (or create) the global resource detector singleton."""
    global _resource_detector
    if _resource_detector is None:
        with _detector_lock:
            if _resource_detector is None:
                _resource_detector = ResourceDetector()
    return _resource_detector


def get_allocation_learner() -> AllocationLearner:
    """Return (or create) the global allocation learner singleton."""
    global _allocation_learner
    if _allocation_learner is None:
        with _learner_lock:
            if _allocation_learner is None:
                _allocation_learner = AllocationLearner()
    return _allocation_learner
