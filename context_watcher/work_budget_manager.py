"""
work_budget_manager.py — Coordinator-level token work budget tracking for AtlasForge.

Responsibilities:
  - Track per-session output token production at the conductor level
  - Emit continuation nudge messages when work target is not yet met
  - Detect diminishing returns (low output delta across multiple continuations)
  - Provide model-aware default budgets (Opus larger, Sonnet/Haiku smaller)
  - Report a clear stop_reason on every session exit

Correct precedence enforced externally (in conductor):
  1. Emergency context pressure (always wins — ContextWatcher owns this)
  2. Graceful context pressure   (ContextWatcher)
  3. Work budget complete / diminishing returns  (THIS MODULE)
  4. Time fallback               (circuit-breaker only)
  5. Hard timeout                (kill for broken sessions)

This module is COORDINATOR-LEVEL ONLY. It is NOT pushed into sub-agents.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import List, Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model-aware defaults
# ---------------------------------------------------------------------------

# Output token budget per top-level conductor session
_MODEL_BUDGETS: dict[str, int] = {
    "claude-opus-4-6":    80_000,
    "claude-opus-4":      60_000,
    "claude-sonnet-4-6":  40_000,
    "claude-sonnet-4-5":  35_000,
    "claude-haiku-4-5":   20_000,
}

# Maximum continuations before diminishing-returns check begins
_MODEL_MAX_CONTINUATIONS: dict[str, int] = {
    "claude-opus-4-6":   8,
    "claude-opus-4":     7,
    "claude-sonnet-4-6": 5,
    "claude-sonnet-4-5": 5,
    "claude-haiku-4-5":  3,
}

# Minimum output-token delta per continuation to count as productive
_MODEL_LOW_DELTA_THRESHOLD: dict[str, int] = {
    "claude-opus-4-6":   2_000,
    "claude-opus-4":     1_800,
    "claude-sonnet-4-6": 1_500,
    "claude-sonnet-4-5": 1_200,
    "claude-haiku-4-5":    500,
}

_DEFAULT_BUDGET = 40_000
_DEFAULT_MAX_CONTINUATIONS = 5
_DEFAULT_LOW_DELTA_THRESHOLD = 1_500
_CONSECUTIVE_LOW_DELTA_REQUIRED = 2

STOP_REASON_BUDGET = "work_budget_complete"
STOP_REASON_DIMINISHING = "diminishing_returns"


def _resolve_budget_for_model(model: str) -> int:
    """Return budget_tokens for the given model string (substring match).

    BUG-M5: Sort keys by descending length so longer (more specific) keys are
    checked first — prevents "claude-opus-4" from matching "claude-opus-4-6".
    """
    if model is None:
        model = ''
    env_override = os.environ.get("WORK_BUDGET_TOKENS")
    if env_override:
        try:
            val = int(env_override)
            if val > 0:
                return val
        except ValueError:
            pass
    for key in sorted(_MODEL_BUDGETS, key=len, reverse=True):
        if key in model.lower():
            return _MODEL_BUDGETS[key]
    return _DEFAULT_BUDGET


def _resolve_max_continuations(model: str) -> int:
    if model is None:
        model = ''
    for key in sorted(_MODEL_MAX_CONTINUATIONS, key=len, reverse=True):
        if key in model.lower():
            return _MODEL_MAX_CONTINUATIONS[key]
    return _DEFAULT_MAX_CONTINUATIONS


def _resolve_low_delta_threshold(model: str) -> int:
    if model is None:
        model = ''
    for key in sorted(_MODEL_LOW_DELTA_THRESHOLD, key=len, reverse=True):
        if key in model.lower():
            return _MODEL_LOW_DELTA_THRESHOLD[key]
    return _DEFAULT_LOW_DELTA_THRESHOLD


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------

@dataclass
class WorkBudgetDecision:
    """Decision returned by WorkBudgetManager.check()."""
    action: Literal["continue", "stop"]
    reason: str          # "" | "work_budget_complete" | "diminishing_returns"
    nudge_message: Optional[str]
    continuation_count: int
    pct: float           # output_tokens / budget_tokens * 100
    output_tokens: int
    budget_tokens: int


# ---------------------------------------------------------------------------
# WorkBudgetManager
# ---------------------------------------------------------------------------

class WorkBudgetManager:
    """
    Coordinator-level token work budget manager.

    Usage (in conductor session loop):

        mgr = WorkBudgetManager(model="claude-sonnet-4-6")

        # After each LLM response:
        decision = mgr.check(output_tokens_from_response)
        if decision.action == 'stop':
            break  # budget complete or diminishing returns
        # inject decision.nudge_message if not None, then continue

    Thread-safe: all internal mutations are protected by a lock.
    """

    def __init__(self, model: str, budget_tokens: Optional[int] = None):
        """
        Args:
            model: Model ID string (e.g. "claude-sonnet-4-6").
                   Used for model-aware defaults.
            budget_tokens: Explicit output token budget. When None, resolves
                           from WORK_BUDGET_TOKENS env var or model default.
        """
        # BUG-H5: coerce non-string model to str before strip/lower
        self._model = str(model).strip().lower() if model is not None else ""
        if budget_tokens is not None and isinstance(budget_tokens, bool):
            raise TypeError(f"budget_tokens must be an int, got bool {budget_tokens!r}")
        if budget_tokens is not None and isinstance(budget_tokens, float):
            raise TypeError(f"budget_tokens must be an int, got float {budget_tokens!r}")
        if budget_tokens is not None and budget_tokens <= 0:
            raise ValueError(f"budget_tokens must be positive, got {budget_tokens!r}")
        _resolved = budget_tokens if budget_tokens is not None else _resolve_budget_for_model(self._model)
        if _resolved <= 0:
            logger.warning(
                "_resolve_budget_for_model returned %d for model %r — using default %d",
                _resolved, self._model or "(unset)", _DEFAULT_BUDGET
            )
            self._budget = _DEFAULT_BUDGET
        else:
            self._budget = _resolved
        self._max_continuations = _resolve_max_continuations(self._model)
        self._low_delta_threshold = _resolve_low_delta_threshold(self._model)
        self._lock = threading.Lock()

        # Session state
        self._output_tokens: int = 0
        self._continuation_count: int = 0
        self._token_history: List[int] = []
        self._consecutive_low_delta: int = 0
        self._stop_reason: str = ""

        logger.info(
            "WorkBudgetManager init: model=%s budget=%d max_cont=%d low_delta=%d",
            self._model or "(unset)", self._budget,
            self._max_continuations, self._low_delta_threshold,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, output_tokens: int) -> WorkBudgetDecision:
        """
        Update cumulative output token count and return a WorkBudgetDecision.

        Args:
            output_tokens: Cumulative output tokens produced so far this session.
                           (Pass the running total, not the delta.)

        Returns:
            WorkBudgetDecision with action='continue' or action='stop'.
        """
        with self._lock:
            # Guard against bool (subclasses int but semantically invalid as token count)
            if isinstance(output_tokens, bool):
                raise TypeError(f"output_tokens must be a non-negative int, got bool {output_tokens!r}")
            # Guard against non-numeric input (None, str, etc.)
            if not isinstance(output_tokens, (int, float)):
                raise TypeError(f"output_tokens must be a non-negative int or float, got {type(output_tokens).__name__} {output_tokens!r}")
            # H9: guard against inf/nan — int(float('inf')) raises OverflowError
            import math as _math
            if isinstance(output_tokens, float) and not _math.isfinite(output_tokens):
                raise ValueError(f"output_tokens must be finite, got {output_tokens!r}")
            # Guard against huge finite floats that overflow int conversion
            if isinstance(output_tokens, float) and output_tokens > 2**53:
                raise ValueError(f"output_tokens value too large: {output_tokens!r}")
            output_tokens = max(0, int(output_tokens))

            # BUG-H7: only count continuations that produced new tokens.
            # Skipping zero-delta calls prevents empty responses from triggering
            # false diminishing-returns decisions.
            if output_tokens < self._output_tokens:
                # Decreasing token count — likely a stale/reset watermark from a new agent turn.
                # Log warning to surface false budget exhaustion risk, keep existing high watermark.
                logger.warning(
                    "Decreasing output_tokens detected: %d -> %d (stale watermark kept; "
                    "budget enforcement continues against prior high watermark)",
                    self._output_tokens, output_tokens,
                )
            if output_tokens <= self._output_tokens:
                # No new output — but still enforce budget if already exhausted (H4).
                # A stale/equal token count must not return "continue" if budget is done.
                pct = (self._output_tokens / self._budget * 100) if self._budget > 0 else 0.0
                if self._stop_reason:
                    return WorkBudgetDecision(
                        action="stop",
                        reason=self._stop_reason,
                        nudge_message=None,
                        continuation_count=self._continuation_count,
                        pct=pct,
                        output_tokens=self._output_tokens,
                        budget_tokens=self._budget,
                    )
                nudge = self._build_nudge_message(pct)
                return WorkBudgetDecision(
                    action="continue",
                    reason="",
                    nudge_message=nudge,
                    continuation_count=self._continuation_count,
                    pct=pct,
                    output_tokens=self._output_tokens,
                    budget_tokens=self._budget,
                )

            self._output_tokens = output_tokens
            self._continuation_count += 1
            self._token_history.append(self._output_tokens)

            pct = (self._output_tokens / self._budget * 100) if self._budget > 0 else 0.0

            # --- Rule 1: budget exhausted ---
            if self._output_tokens >= self._budget:
                self._stop_reason = STOP_REASON_BUDGET
                logger.info(
                    "WorkBudgetManager: STOP work_budget_complete "
                    "output=%d budget=%d (%.1f%%)",
                    self._output_tokens, self._budget, pct,
                )
                return WorkBudgetDecision(
                    action="stop",
                    reason=STOP_REASON_BUDGET,
                    nudge_message=None,
                    continuation_count=self._continuation_count,
                    pct=pct,
                    output_tokens=self._output_tokens,
                    budget_tokens=self._budget,
                )

            # --- Rule 2: diminishing returns (only after min continuations) ---
            if self._continuation_count >= self._max_continuations:
                if self._check_diminishing_returns():
                    self._stop_reason = STOP_REASON_DIMINISHING
                    logger.info(
                        "WorkBudgetManager: STOP diminishing_returns "
                        "output=%d cont=%d consecutive_low=%d",
                        self._output_tokens, self._continuation_count,
                        self._consecutive_low_delta,
                    )
                    return WorkBudgetDecision(
                        action="stop",
                        reason=STOP_REASON_DIMINISHING,
                        nudge_message=None,
                        continuation_count=self._continuation_count,
                        pct=pct,
                        output_tokens=self._output_tokens,
                        budget_tokens=self._budget,
                    )

            # --- Continue ---
            nudge = self._build_nudge_message(pct)
            logger.debug(
                "WorkBudgetManager: continue output=%d budget=%d (%.1f%%) cont=%d",
                self._output_tokens, self._budget, pct, self._continuation_count,
            )
            return WorkBudgetDecision(
                action="continue",
                reason="",
                nudge_message=nudge,
                continuation_count=self._continuation_count,
                pct=pct,
                output_tokens=self._output_tokens,
                budget_tokens=self._budget,
            )

    def get_nudge_message(self) -> str:
        """Return nudge message based on current progress (thread-safe)."""
        with self._lock:
            pct = (self._output_tokens / self._budget * 100) if self._budget > 0 else 0.0
            return self._build_nudge_message(pct)

    def get_stop_reason(self) -> str:
        """Return the last stop reason (empty string if session is still running)."""
        with self._lock:
            return self._stop_reason

    def reset(self) -> None:
        """Reset all session state for a new session."""
        with self._lock:
            self._output_tokens = 0
            self._continuation_count = 0
            self._token_history = []
            self._consecutive_low_delta = 0
            self._stop_reason = ""
        logger.info("WorkBudgetManager: reset for new session")

    @property
    def budget_tokens(self) -> int:
        return self._budget

    @property
    def output_tokens(self) -> int:
        with self._lock:
            return self._output_tokens

    @property
    def continuation_count(self) -> int:
        with self._lock:
            return self._continuation_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_diminishing_returns(self) -> bool:
        """
        Return True if output delta is below threshold for
        _CONSECUTIVE_LOW_DELTA_REQUIRED consecutive checks.

        Called only after _continuation_count >= _max_continuations.
        Lock must be held by caller.
        """
        if len(self._token_history) < 2:
            # M8: reset counter on early return so it doesn't leak into future checks
            self._consecutive_low_delta = 0
            return False
        delta = self._token_history[-1] - self._token_history[-2]
        if delta < self._low_delta_threshold:
            self._consecutive_low_delta += 1
        else:
            self._consecutive_low_delta = 0
        return self._consecutive_low_delta >= _CONSECUTIVE_LOW_DELTA_REQUIRED

    def _build_nudge_message(self, pct: float) -> str:
        """Format the continuation nudge message."""
        return (
            f"Stopped at {pct:.0f}% of work target "
            f"({self._output_tokens:,} / {self._budget:,} tokens). "
            "Keep working — do not summarize."
        )
