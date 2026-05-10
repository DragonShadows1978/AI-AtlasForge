"""
af_engine.mission_config - Canonical Mission Configuration

Single source of truth for all mission parameters:
- MissionConfig dataclass with validation, defaults, and range checks
- MissionParamAudit dataclass for submitted-vs-applied tracking
- MissionValidationError for pre-flight rejection (hard failures)
- validate_mission_params() for lightweight pre-flight checks
- from_request() and from_queue_item() factory class methods
- to_mission_dict() to build the full mission JSON dict
- save_audit_log() to persist audit records per-mission
- migrate_config() for backward-compatible schema evolution
- prune_old_audit_logs() for audit log rotation/pruning

All code paths that create missions MUST route through this module.
Zero dashboard dependencies — imports only stdlib.

Schema Versions:
  0 = pre-Cycle-2, no config_version field
  2 = Cycle 2+ (config_version embedded in config and audit files)
"""

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (canonical defaults — single definition used everywhere)
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_VERSION: int = 2  # Increment when schema changes

DEFAULT_CYCLE_BUDGET: int = 3
DEFAULT_MAX_ITERATIONS: int = 10
DEFAULT_LLM_PROVIDER: str = "claude"
VALID_LLM_PROVIDERS: frozenset = frozenset({"claude", "codex", "gemini"})
MIN_CYCLE_BUDGET: int = 1
MAX_CYCLE_BUDGET: int = 10
MIN_MAX_ITERATIONS: int = 1
MAX_MAX_ITERATIONS: int = 50
MIN_PROBLEM_STATEMENT_LEN: int = 10
MAX_PROJECT_NAME_LEN: int = 64
PROJECT_NAME_PATTERN: re.Pattern = re.compile(r'^[a-zA-Z0-9_\-]+$')
ZERO_WIDTH_TRANSLATION: Dict[int, None] = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d"),
    None,
)

# Audit reason vocabulary (canonical)
REASON_MIN_CLAMP = "min_clamp"
REASON_MAX_CLAMP = "max_clamp"
REASON_TYPE_COERCION = "type_coercion"
REASON_DEFAULT_APPLIED = "default_applied"
REASON_ENV_RESOLUTION = "env_resolution"
REASON_INVALID_REJECTED = "invalid_rejected"


def _strip_problem_statement(value: str) -> str:
    """Trim visible whitespace and zero-width space variants from mission text."""
    return value.translate(ZERO_WIDTH_TRANSLATION).strip()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class MissionValidationError(ValueError):
    """Raised when mission parameters fail hard validation.

    Unlike clamping (soft fix), this exception represents an input that cannot
    be automatically corrected — e.g. cycle_budget=0, empty problem_statement.

    Attributes:
        errors: All collected validation failure messages.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors = errors
        super().__init__(self.__str__())

    def __str__(self) -> str:
        header = f"Mission validation failed ({len(self.errors)} error(s)):"
        lines = "\n".join(f"  - {e}" for e in self.errors)
        return f"{header}\n{lines}"


# ---------------------------------------------------------------------------
# Audit dataclass
# ---------------------------------------------------------------------------

@dataclass
class MissionParamAudit:
    """Records submitted vs applied parameters for a single mission creation.

    Populated by MissionConfig.from_request() / from_queue_item() and written
    to missions/{mission_id}/parameter_audit.json by save_audit_log().
    """

    mission_id: str
    submitted: Dict[str, Any]          # Raw caller-provided values
    applied: Dict[str, Any]            # Final normalised values
    overrides: List[Dict[str, Any]]    # Each: {param, submitted_value, applied_value, reason}
    timestamp: str
    is_valid: bool
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return JSON-serialisable dict."""
        return {
            "mission_id": self.mission_id,
            "submitted": self.submitted,
            "applied": self.applied,
            "overrides": self.overrides,
            "override_count": len(self.overrides),
            "timestamp": self.timestamp,
            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors,
            "config_version": CONFIG_SCHEMA_VERSION,
        }

    def summary(self) -> Dict[str, Any]:
        """Return compact summary suitable for embedding in mission.json."""
        return {
            "is_valid": self.is_valid,
            "override_count": len(self.overrides),
            "overrides": self.overrides,
            "submitted_at": self.timestamp,
            "validation_errors": self.validation_errors,
        }


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------

@dataclass
class MissionConfig:
    """Canonical mission parameters with validation and defaults.

    Construction validates ALL fields, collecting errors before raising.
    Hard failures -> MissionValidationError (must be fixed by caller).
    Soft fixes (type coercion, env resolution) are recorded in the audit.

    Class-level constants (DEFAULT_*, MIN_*, MAX_*) are importable directly
    so state_manager.py and other modules stay in sync without duplicating
    magic numbers.
    """

    # Class-level re-exports of module constants (for import convenience)
    DEFAULT_CYCLE_BUDGET = DEFAULT_CYCLE_BUDGET
    DEFAULT_MAX_ITERATIONS = DEFAULT_MAX_ITERATIONS
    DEFAULT_LLM_PROVIDER = DEFAULT_LLM_PROVIDER
    VALID_LLM_PROVIDERS = VALID_LLM_PROVIDERS

    # Required
    problem_statement: str

    # With validated defaults
    cycle_budget: int = DEFAULT_CYCLE_BUDGET
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    llm_provider: str = DEFAULT_LLM_PROVIDER
    mission_type: str = "full_rd"

    # Optional
    project_name: Optional[str] = None
    preferences: Dict[str, Any] = field(default_factory=dict)
    success_criteria: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalise all fields. Raises MissionValidationError on hard failures."""
        errors: List[str] = []

        # --- problem_statement ---
        ps = self.problem_statement
        if not isinstance(ps, str):
            ps = str(ps)
            self.problem_statement = ps
        ps_stripped = _strip_problem_statement(ps)
        if len(ps_stripped) < MIN_PROBLEM_STATEMENT_LEN:
            errors.append(
                f"problem_statement must be at least {MIN_PROBLEM_STATEMENT_LEN} characters, "
                f"got {len(ps_stripped)}"
            )
        else:
            self.problem_statement = ps_stripped

        # --- cycle_budget ---
        self.cycle_budget = self._validate_int_field(
            "cycle_budget", self.cycle_budget,
            MIN_CYCLE_BUDGET, MAX_CYCLE_BUDGET, errors
        )

        # --- max_iterations ---
        self.max_iterations = self._validate_int_field(
            "max_iterations", self.max_iterations,
            MIN_MAX_ITERATIONS, MAX_MAX_ITERATIONS, errors
        )

        # --- llm_provider ---
        if isinstance(self.llm_provider, str):
            self.llm_provider = self.llm_provider.strip().lower()
        if self.llm_provider not in VALID_LLM_PROVIDERS:
            errors.append(
                f"llm_provider must be one of {sorted(VALID_LLM_PROVIDERS)}, "
                f"got {self.llm_provider!r}"
            )

        # --- mission_type ---
        try:
            from af_engine.mission_profiles import MISSION_TYPE_PROFILES
        except ImportError:
            MISSION_TYPE_PROFILES = {"full_rd": {}}
        if not isinstance(self.mission_type, str):
            errors.append(
                f"mission_type must be a string, got {type(self.mission_type).__name__}"
            )
        else:
            self.mission_type = self.mission_type.strip()
            if self.mission_type not in MISSION_TYPE_PROFILES:
                errors.append(
                    f"mission_type must be one of {sorted(MISSION_TYPE_PROFILES.keys())}, "
                    f"got {self.mission_type!r}"
                )

        # --- project_name ---
        if self.project_name is not None:
            pn = str(self.project_name).strip()
            if len(pn) > MAX_PROJECT_NAME_LEN:
                errors.append(
                    f"project_name exceeds {MAX_PROJECT_NAME_LEN} characters"
                )
            elif pn and not PROJECT_NAME_PATTERN.match(pn):
                errors.append(
                    f"project_name must contain only alphanumeric characters, underscores, "
                    f"and hyphens, got {pn!r}"
                )
            else:
                self.project_name = pn or None

        # --- collections ---
        if not isinstance(self.preferences, dict):
            self.preferences = {}
        if not isinstance(self.success_criteria, list):
            # int/float/bool are not iterable but list(...) accepts strings/bytes
            # silently and explodes them character-by-character. Reject all
            # non-iterable / scalar types via MissionValidationError instead of
            # letting list() raise TypeError or producing nonsense data.
            sc = self.success_criteria
            if sc is None or sc == "":
                self.success_criteria = []
            elif isinstance(sc, (int, float, bool, bytes)):
                errors.append(
                    f"success_criteria must be a list, got {type(sc).__name__}: {sc!r}"
                )
                self.success_criteria = []
            elif isinstance(sc, str):
                # A bare string is almost certainly a single criterion, not a
                # sequence of single-character criteria.
                self.success_criteria = [sc]
            else:
                try:
                    self.success_criteria = list(sc)
                except TypeError:
                    errors.append(
                        f"success_criteria must be a list, got {type(sc).__name__}"
                    )
                    self.success_criteria = []
        if not isinstance(self.metadata, dict):
            self.metadata = {}

        if errors:
            raise MissionValidationError(errors)

    @staticmethod
    def _validate_int_field(
        name: str,
        value: Any,
        min_val: int,
        max_val: int,
        errors: List[str],
    ) -> int:
        """Validate an integer range field. Records error for hard failures, clamps for range."""
        # Type coercion. bool is a subclass of int, but accepting True as 1
        # hides invalid request data and bypasses type-coercion auditing.
        # int(float('inf')) raises OverflowError which (ValueError, TypeError)
        # does NOT catch, so OverflowError must be in the except tuple.
        if isinstance(value, bool):
            errors.append(f"{name} must be an integer, got boolean {value!r}")
            return min_val
        if not isinstance(value, int):
            try:
                value = int(value)
            except (ValueError, TypeError, OverflowError):
                errors.append(f"{name} must be an integer, got {value!r}")
                return min_val  # Placeholder; error will prevent construction

        # Hard rejection: zero/negative iteration controls are caller errors,
        # not values to silently clamp into a runnable mission.
        if name in {"cycle_budget", "max_iterations"} and value <= 0:
            errors.append(
                f"{name} must be >= {min_val}, got {value}. "
                f"Use {name}=1 for the minimum allowed mission setting."
            )
            return min_val

        # Range clamp (soft fix recorded in audit separately)
        return max(min_val, min(max_val, value))

    # -----------------------------------------------------------------------
    # Factory methods
    # -----------------------------------------------------------------------

    @classmethod
    def from_request(
        cls,
        data: Dict[str, Any],
        mission_id: str = "unknown",
    ) -> Tuple["MissionConfig", MissionParamAudit]:
        """Build a MissionConfig from an HTTP request body or similar dict.

        Parameters
        ----------
        data:
            Raw request data. Accepts both 'mission' and 'problem_statement'
            as keys for the problem statement (dashboard uses 'mission').
        mission_id:
            Used only for the audit record; does not affect config.

        Returns
        -------
        (config, audit) tuple. Raises MissionValidationError on hard failure.
        """
        if not isinstance(data, dict):
            raise MissionValidationError(
                [f"from_request() requires a dict, got {type(data).__name__}: {data!r}"]
            )
        submitted = dict(data)

        # Normalise problem statement key
        problem_statement = (
            data.get("problem_statement") or
            data.get("mission") or
            ""
        )

        overrides: List[Dict[str, Any]] = []

        # --- cycle_budget ---
        cb_raw = data.get("cycle_budget")
        if cb_raw is None:
            cb_value = DEFAULT_CYCLE_BUDGET
            overrides.append({
                "param": "cycle_budget",
                "submitted_value": None,
                "applied_value": DEFAULT_CYCLE_BUDGET,
                "reason": REASON_DEFAULT_APPLIED,
            })
        else:
            try:
                if isinstance(cb_raw, bool):
                    raise TypeError("boolean is not an integer parameter")
                cb_value = int(cb_raw)
                if cb_value != cb_raw or type(cb_raw) is not int:
                    overrides.append({
                        "param": "cycle_budget",
                        "submitted_value": cb_raw,
                        "applied_value": cb_value,
                        "reason": REASON_TYPE_COERCION,
                    })
            except (ValueError, TypeError, OverflowError):
                cb_value = cb_raw  # Let __post_init__ catch this

        # --- max_iterations ---
        mi_raw = data.get("max_iterations")
        if mi_raw is None:
            mi_value = DEFAULT_MAX_ITERATIONS
            overrides.append({
                "param": "max_iterations",
                "submitted_value": None,
                "applied_value": DEFAULT_MAX_ITERATIONS,
                "reason": REASON_DEFAULT_APPLIED,
            })
        else:
            try:
                if isinstance(mi_raw, bool):
                    raise TypeError("boolean is not an integer parameter")
                mi_value = int(mi_raw)
                if mi_value != mi_raw or type(mi_raw) is not int:
                    overrides.append({
                        "param": "max_iterations",
                        "submitted_value": mi_raw,
                        "applied_value": mi_value,
                        "reason": REASON_TYPE_COERCION,
                    })
            except (ValueError, TypeError, OverflowError):
                mi_value = mi_raw

        # --- llm_provider ---
        llm_raw = data.get("llm_provider")
        if llm_raw is None:
            env_provider = str(os.environ.get("ATLASFORGE_LLM_PROVIDER", "")).strip().lower()
            llm_value = env_provider if env_provider in VALID_LLM_PROVIDERS else DEFAULT_LLM_PROVIDER
            overrides.append({
                "param": "llm_provider",
                "submitted_value": None,
                "applied_value": llm_value,
                "reason": REASON_ENV_RESOLUTION if env_provider in VALID_LLM_PROVIDERS else REASON_DEFAULT_APPLIED,
            })
        else:
            llm_value = llm_raw

        # --- mission_type ---
        mt_raw = data.get("mission_type")
        if mt_raw is None:
            mt_value = "full_rd"
            overrides.append({
                "param": "mission_type",
                "submitted_value": None,
                "applied_value": mt_value,
                "reason": REASON_DEFAULT_APPLIED,
            })
        else:
            mt_value = mt_raw

        # Pre-clamp values for detecting range clamping after construction
        pre_clamp_cb = cb_value if isinstance(cb_value, int) else None
        pre_clamp_mi = mi_value if isinstance(mi_value, int) else None

        config = cls(
            problem_statement=problem_statement,
            cycle_budget=cb_value,
            max_iterations=mi_value,
            llm_provider=llm_value,
            mission_type=mt_value,
            project_name=data.get("project_name"),
            preferences=data.get("preferences", {}),
            success_criteria=data.get("success_criteria", []),
            metadata=data.get("metadata", {}),
        )

        # Detect range clamping
        if pre_clamp_cb is not None and config.cycle_budget != pre_clamp_cb:
            reason = REASON_MIN_CLAMP if pre_clamp_cb < MIN_CYCLE_BUDGET else REASON_MAX_CLAMP
            overrides.append({
                "param": "cycle_budget",
                "submitted_value": pre_clamp_cb,
                "applied_value": config.cycle_budget,
                "reason": reason,
            })
        if pre_clamp_mi is not None and config.max_iterations != pre_clamp_mi:
            reason = REASON_MIN_CLAMP if pre_clamp_mi < MIN_MAX_ITERATIONS else REASON_MAX_CLAMP
            overrides.append({
                "param": "max_iterations",
                "submitted_value": pre_clamp_mi,
                "applied_value": config.max_iterations,
                "reason": reason,
            })

        audit = MissionParamAudit(
            mission_id=mission_id,
            submitted=submitted,
            applied=config._to_param_dict(),
            overrides=overrides,
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_valid=True,
            validation_errors=[],
        )

        return config, audit

    @classmethod
    def from_queue_item(
        cls,
        queue_item: Dict[str, Any],
        mission_id: str = "unknown",
    ) -> Tuple["MissionConfig", MissionParamAudit]:
        """Build a MissionConfig from a queue item dict.

        Queue items use different key names (mission_description, mission_title)
        but the validation rules are identical.
        """
        if not isinstance(queue_item, dict):
            raise MissionValidationError([
                f"from_queue_item() requires a dict, got {type(queue_item).__name__}: {queue_item!r}"
            ])
        # Normalise to standard keys
        problem_statement = (
            queue_item.get("mission_description") or
            queue_item.get("problem_statement") or
            queue_item.get("mission_title") or
            ""
        )

        normalised = dict(queue_item)
        normalised["problem_statement"] = problem_statement

        # Propagate metadata about queue origin. Coerce non-dict metadata
        # (string, list, int, ...) to {} rather than letting dict() blow up
        # with ValueError. __post_init__ will set metadata={} for non-dicts
        # in from_request, but the `dict(metadata)` call here happens before
        # construction.
        _md_raw = normalised.get("metadata")
        metadata = dict(_md_raw) if isinstance(_md_raw, dict) else {}
        metadata["queued"] = True
        if queue_item.get("queued_at"):
            metadata["queued_at"] = queue_item["queued_at"]
        if queue_item.get("id"):
            metadata["source_queue_item_id"] = queue_item["id"]
        normalised["metadata"] = metadata

        return cls.from_request(normalised, mission_id=mission_id)

    # -----------------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------------

    def _to_param_dict(self) -> Dict[str, Any]:
        """Compact dict of just the validated parameters."""
        return {
            "problem_statement": self.problem_statement,
            "cycle_budget": self.cycle_budget,
            "max_iterations": self.max_iterations,
            "llm_provider": self.llm_provider,
            "mission_type": self.mission_type,
            "project_name": self.project_name,
        }

    def to_mission_dict(
        self,
        mission_id: str,
        mission_workspace,
        mission_dir,
        *,
        resolved_project_name: Optional[str] = None,
        source_queue_item_id: Optional[str] = None,
        source_recommendation_id: Optional[str] = None,
        audit: Optional[MissionParamAudit] = None,
    ) -> Dict[str, Any]:
        """Build the full mission JSON dict ready to write to mission.json.

        This is the single place where the mission dict schema is defined.
        All call sites that previously built this dict inline now call here.
        """
        now = datetime.now(timezone.utc).isoformat()
        project = resolved_project_name or self.project_name

        extra_metadata = dict(self.metadata)
        if source_queue_item_id:
            extra_metadata["source_queue_item_id"] = source_queue_item_id
        if source_recommendation_id:
            extra_metadata["source_recommendation_id"] = source_recommendation_id

        mission: Dict[str, Any] = {
            "mission_id": mission_id,
            "problem_statement": self.problem_statement,
            "original_problem_statement": self.problem_statement,
            "preferences": self.preferences,
            "success_criteria": self.success_criteria,
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": self.max_iterations,
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": now,
            "last_updated": now,
            "cycle_started_at": now,
            "cycle_budget": self.cycle_budget,
            "current_cycle": 1,
            "cycle_history": [],
            "mission_workspace": str(mission_workspace),
            "mission_dir": str(mission_dir),
            "project_name": project,
            "llm_provider": self.llm_provider,
            "metadata": extra_metadata,
        }

        if source_queue_item_id:
            mission["source_queue_item_id"] = source_queue_item_id
        if source_recommendation_id:
            mission["source_recommendation_id"] = source_recommendation_id
        if audit:
            mission["parameter_audit_summary"] = audit.summary()

        # Apply mission type profile (sets current_stage, enabled_stages,
        # stop_after_profile_complete, mission_profile, mission_type_label).
        # full_rd profile sets current_stage="PLANNING" — identical to today.
        try:
            from af_engine.mission_profiles import apply_mission_type_profile
            apply_mission_type_profile(mission, self.mission_type)
        except ImportError:
            logger.debug("mission_profiles not available — skipping profile injection")

        return mission

    def to_config_dict(
        self,
        mission_id: str,
        mission_workspace,
        *,
        resolved_project_name: Optional[str] = None,
        source_queue_item_id: Optional[str] = None,
        source_recommendation_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build the compact mission_config.json dict (stored in mission dir)."""
        project = resolved_project_name or self.project_name
        config: Dict[str, Any] = {
            "mission_id": mission_id,
            "problem_statement": self.problem_statement,
            "cycle_budget": self.cycle_budget,
            "max_iterations": self.max_iterations,
            "llm_provider": self.llm_provider,
            "mission_type": self.mission_type,
            "created_at": created_at or datetime.now().isoformat(),
            "config_version": CONFIG_SCHEMA_VERSION,
        }
        if project:
            config["project_name"] = project
            config["project_workspace"] = str(mission_workspace)
        if source_queue_item_id:
            config["source_queue_item_id"] = source_queue_item_id
        if source_recommendation_id:
            config["source_recommendation_id"] = source_recommendation_id
        return config


# ---------------------------------------------------------------------------
# Pre-flight validation (lightweight, no object construction)
# ---------------------------------------------------------------------------

def validate_mission_params(raw: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Lightweight pre-flight check for use before full MissionConfig construction.

    Returns (is_valid, error_messages). Does NOT construct a MissionConfig object.
    Used by API endpoints to return HTTP 400 before any mission creation starts.

    Parameters
    ----------
    raw:
        Dict from request.get_json() or similar.

    Returns
    -------
    (True, []) on success, (False, [error_str, ...]) on failure.
    """
    errors: List[str] = []

    # problem_statement
    ps = (raw.get("problem_statement") or raw.get("mission") or "")
    if not isinstance(ps, str):
        ps = str(ps)
    ps_stripped = _strip_problem_statement(ps)
    if len(ps_stripped) < MIN_PROBLEM_STATEMENT_LEN:
        errors.append(
            f"problem_statement must be at least {MIN_PROBLEM_STATEMENT_LEN} characters "
            f"(got {len(ps_stripped)})"
        )

    # cycle_budget -- hard reject 0 or negative; allow None (defaults to 3)
    cb = raw.get("cycle_budget")
    if cb is not None:
        try:
            if isinstance(cb, bool):
                raise TypeError("boolean is not an integer parameter")
            cb_int = int(cb)
            if cb_int <= 0:
                errors.append(
                    f"cycle_budget must be >= {MIN_CYCLE_BUDGET}, got {cb_int}. "
                    f"Use cycle_budget=1 for a single-cycle mission."
                )
            elif cb_int > MAX_CYCLE_BUDGET:
                errors.append(
                    f"cycle_budget must be <= {MAX_CYCLE_BUDGET}, got {cb_int}"
                )
        except (ValueError, TypeError):
            errors.append(f"cycle_budget must be an integer, got {cb!r}")

    # max_iterations -- allow None (defaults to 10)
    mi = raw.get("max_iterations")
    if mi is not None:
        try:
            if isinstance(mi, bool):
                raise TypeError("boolean is not an integer parameter")
            mi_int = int(mi)
            if mi_int < MIN_MAX_ITERATIONS:
                errors.append(
                    f"max_iterations must be >= {MIN_MAX_ITERATIONS}, got {mi_int}"
                )
            elif mi_int > MAX_MAX_ITERATIONS:
                errors.append(
                    f"max_iterations must be <= {MAX_MAX_ITERATIONS}, got {mi_int}"
                )
        except (ValueError, TypeError):
            errors.append(f"max_iterations must be an integer, got {mi!r}")

    # llm_provider -- allow None (resolved from env)
    llm = raw.get("llm_provider")
    if llm is not None:
        llm_norm = str(llm).strip().lower()
        if llm_norm not in VALID_LLM_PROVIDERS:
            errors.append(
                f"llm_provider must be one of {sorted(VALID_LLM_PROVIDERS)}, got {llm!r}"
            )

    # mission_type -- allow None (defaults to full_rd)
    mt = raw.get("mission_type")
    if mt is not None:
        if not isinstance(mt, str):
            errors.append(f"mission_type must be a string, got {type(mt).__name__}")
        else:
            mt_norm = mt.strip()
            try:
                from af_engine.mission_profiles import is_valid_mission_type
                valid_mission_type = is_valid_mission_type(mt_norm)
            except ImportError:
                valid_mission_type = mt_norm == "full_rd"
            if not valid_mission_type:
                errors.append(f"mission_type must be a known profile, got {mt!r}")

    # project_name -- allow None
    pn = raw.get("project_name")
    if pn is not None:
        pn_str = str(pn).strip()
        if len(pn_str) > MAX_PROJECT_NAME_LEN:
            errors.append(
                f"project_name exceeds {MAX_PROJECT_NAME_LEN} characters"
            )
        elif pn_str and not PROJECT_NAME_PATTERN.match(pn_str):
            errors.append(
                f"project_name must contain only alphanumeric characters, underscores, "
                f"and hyphens"
            )

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Audit persistence
# ---------------------------------------------------------------------------

def save_audit_log(audit: MissionParamAudit, mission_dir: Path) -> bool:
    """Write parameter_audit.json to the mission directory.

    Args:
        audit: The audit record to persist.
        mission_dir: Path to the mission's directory (e.g. missions/mission_abc123/).

    Returns:
        True on success, False on failure (non-fatal -- mission still created).
    """
    try:
        mission_dir = Path(mission_dir)
        mission_dir.mkdir(parents=True, exist_ok=True)
        audit_path = mission_dir / "parameter_audit.json"
        with open(audit_path, "w") as f:
            json.dump(audit.to_dict(), f, indent=2)
        logger.info(
            f"[MissionConfig] Audit log written: {audit_path} "
            f"({len(audit.overrides)} override(s))"
        )
        return True
    except Exception as e:
        logger.error(f"[MissionConfig] Failed to write audit log: {e}")
        return False


def load_audit_log(mission_dir: Path) -> Optional[Dict[str, Any]]:
    """Load parameter_audit.json from a mission directory.

    Returns the audit dict with config_version normalised (0 if absent), or None if not found.
    """
    try:
        audit_path = Path(mission_dir) / "parameter_audit.json"
        if not audit_path.exists():
            return None
        with open(audit_path) as f:
            data = json.load(f)
        # Backward-compat: add config_version if absent (pre-Cycle-2 audit)
        if "config_version" not in data:
            data["config_version"] = 0
        return data
    except Exception as e:
        logger.warning(f"[MissionConfig] Failed to load audit log from {mission_dir}: {e}")
        return None


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def migrate_config(data: dict) -> dict:
    """Migrate a mission_config.json dict to the current schema version.

    Safe to call on any version — no-ops if already at CONFIG_SCHEMA_VERSION.
    Never raises; missing fields are filled with defaults.

    Args:
        data: Raw dict from mission_config.json (may lack config_version).

    Returns:
        New dict with all required fields present and config_version set.
    """
    try:
        version = int(data.get("config_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= CONFIG_SCHEMA_VERSION:
        return data
    data = dict(data)
    # v0 -> v2: fill fields missing from pre-Cycle-2 configs
    data.setdefault("max_iterations", DEFAULT_MAX_ITERATIONS)
    data.setdefault("llm_provider", DEFAULT_LLM_PROVIDER)
    # Cycle 2: pre-cycle-2 configs lack mission_type. Code paths that read
    # data["mission_type"] directly (e.g. dashboard surfacing) would KeyError.
    data.setdefault("mission_type", "full_rd")
    data["config_version"] = CONFIG_SCHEMA_VERSION
    return data


# ---------------------------------------------------------------------------
# Audit log rotation / pruning
# ---------------------------------------------------------------------------

def prune_old_audit_logs(
    missions_dir: Path,
    max_missions: int = 200,
    max_total_mb: float = 500.0,
    dry_run: bool = False,
) -> List[str]:
    """Prune oldest mission directories when count or size thresholds exceeded.

    Only removes mission directories that contain mission_config.json.
    Never touches test dirs, budget dirs, or any dir lacking mission_config.json.

    Args:
        missions_dir: Path to the missions/ directory.
        max_missions: Maximum number of mission dirs to retain.
        max_total_mb: Maximum total size in megabytes to retain.
        dry_run: If True, return what WOULD be pruned without deleting.

    Returns:
        List of mission_id strings that were (or would be) pruned.
    """
    missions_dir = Path(missions_dir)
    if not missions_dir.exists():
        return []

    # Defensive coercion: a negative max_missions is meaningless and used to
    # let `len(candidates) > max_missions` evaluate to True for *every*
    # candidate, then `candidates.pop(0)` IndexError'd when the list ran dry
    # only after deleting everything. Fail loud instead of silently pruning all.
    try:
        max_missions = int(max_missions)
    except (TypeError, ValueError):
        max_missions = 200
    if max_missions < 0:
        raise ValueError("max_missions must be >= 0")
    try:
        max_total_mb = max(0.0, float(max_total_mb))
    except (TypeError, ValueError):
        max_total_mb = 500.0

    # Collect only dirs that have mission_config.json
    candidates: List[Tuple[float, str, Path]] = []
    for entry in missions_dir.iterdir():
        if not entry.is_dir():
            continue
        config_file = entry / "mission_config.json"
        if not config_file.exists():
            continue
        try:
            with open(config_file) as f:
                cfg = json.load(f)
            created_at_str = cfg.get("created_at", "")
            try:
                ts = datetime.fromisoformat(created_at_str).timestamp()
            except (ValueError, TypeError):
                ts = config_file.stat().st_mtime
        except Exception:
            ts = config_file.stat().st_mtime
        candidates.append((ts, entry.name, entry))

    # Sort oldest-first
    candidates.sort(key=lambda x: x[0])

    pruned: List[str] = []

    # Prune by count
    while len(candidates) > max_missions:
        ts, mission_id, path = candidates.pop(0)
        pruned.append(mission_id)
        if not dry_run:
            try:
                shutil.rmtree(path)
                logger.info(f"[MissionConfig] Pruned mission dir (count): {path}")
            except Exception as e:
                logger.error(f"[MissionConfig] Failed to prune {path}: {e}")

    # Prune by total size — O(N) pre-compute then O(N) subtraction loop
    max_bytes = max_total_mb * 1024 * 1024

    # Pre-compute per-directory sizes once (O(N * files)) instead of recomputing all on each iteration
    dir_sizes: dict = {}
    for _, _, p in candidates:
        if p.exists():
            dir_sizes[str(p)] = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        else:
            dir_sizes[str(p)] = 0

    total_bytes = sum(dir_sizes.values())

    while candidates and total_bytes > max_bytes:
        ts, mission_id, path = candidates.pop(0)
        pruned.append(mission_id)
        deleted_size = dir_sizes.get(str(path), 0)
        if not dry_run:
            try:
                shutil.rmtree(path)
                logger.info(f"[MissionConfig] Pruned mission dir (size): {path}")
            except Exception as e:
                logger.error(f"[MissionConfig] Failed to prune {path}: {e}")
                deleted_size = 0  # failed deletion — don't subtract size
        total_bytes -= deleted_size

    return pruned
