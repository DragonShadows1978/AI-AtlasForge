#!/usr/bin/env python3
"""
Investigation Engine - Parallel Research Mode for AtlasForge Missions

This module provides a simplified, single-cycle investigation workflow that:
1. Takes an investigation query
2. Spawns a lead agent (Sonnet) to analyze and decompose the query
3. Lead agent spawns 3-5 parallel subagents (Haiku) to explore different aspects
4. Synthesizes findings into a comprehensive report

This is COMPLETELY SEPARATE from the standard R&D engine - no stages,
no mission.json modifications, no iterative cycles.
"""

import json
import os
import re
import subprocess
import time
import logging
import uuid
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

# URL Handler integration - deferred import to avoid circular deps
_url_handlers_available = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investigation_engine")

# Base paths - use centralized configuration
from atlasforge_config import BASE_DIR, STATE_DIR
INVESTIGATION_STATE_PATH = STATE_DIR / "investigation_state.json"
LLM_PROVIDER_PATH = STATE_DIR / "llm_provider.json"
from ground_rules_loader import load_ground_rules

# Agent streaming support
import threading as _threading
import uuid as _uuid
_investigation_agent_counter = 0
_investigation_agent_counter_lock = _threading.Lock()

def _next_investigation_agent_id() -> tuple:
    """Return (agent_id, label) for a new investigation agent."""
    global _investigation_agent_counter
    with _investigation_agent_counter_lock:
        _investigation_agent_counter += 1
        n = _investigation_agent_counter
    agent_id = f"inv_{_uuid.uuid4().hex[:8]}"
    label = f"Sub-{n}"
    return agent_id, label

# Shared provider routing (dashboard toggle)
SUPPORTED_LLM_PROVIDERS = {"claude", "codex", "gemini"}
DEFAULT_LLM_PROVIDER = "claude"

# Model name allowlist regex — prevents shell-injection via env-supplied model names
_MODEL_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_./:@\[\]-]{0,127}$')


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    """Parse a boolean env flag with tolerant true/false values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _codex_web_search_enabled() -> bool:
    """Enable Codex native web_search only when explicitly requested.

    Proxy-only search depends on omitting Codex's top-level `--search` flag.
    AtlasForge loads the local WebProxy MCP server instead.
    """
    return _env_flag_enabled("ATLASFORGE_CODEX_WEB_SEARCH", default=False)


def _codex_autonomous_enabled() -> bool:
    """Run Codex with no approval/sandbox prompts by default."""
    return _env_flag_enabled("ATLASFORGE_CODEX_AUTONOMOUS", default=True)


def _gemini_autonomous_enabled() -> bool:
    """Run Gemini with no approval prompts by default."""
    return _env_flag_enabled("ATLASFORGE_GEMINI_AUTONOMOUS", default=True)


def _normalize_provider(provider: Optional[str]) -> str:
    """Normalize provider identifier to supported values."""
    if not provider:
        return DEFAULT_LLM_PROVIDER
    normalized = str(provider).strip().lower()
    if normalized in SUPPORTED_LLM_PROVIDERS:
        return normalized
    return DEFAULT_LLM_PROVIDER


def _get_active_llm_provider() -> str:
    """Get provider from env first, then dashboard state file."""
    env_provider = os.environ.get("ATLASFORGE_LLM_PROVIDER")
    if env_provider:
        return _normalize_provider(env_provider)

    try:
        with open(LLM_PROVIDER_PATH, 'r') as f:
            data = json.load(f)
        return _normalize_provider(data.get("provider"))
    except Exception:
        return DEFAULT_LLM_PROVIDER


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class InvestigationStatus(Enum):
    """Status of an investigation."""
    PENDING = "pending"
    ANALYZING = "analyzing"
    SPAWNING_SUBAGENTS = "spawning_subagents"
    EXPLORING = "exploring"
    VALIDATING = "validating"  # Adversarial fact-checking of citations
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelType(Enum):
    """Available model types."""
    CLAUDE_SONNET = "sonnet"
    CLAUDE_OPUS = "opus"
    CLAUDE_HAIKU = "haiku"


@dataclass
class InvestigationConfig:
    """Configuration for an investigation."""
    query: str
    investigation_id: str = field(default_factory=lambda: f"inv_{uuid.uuid4().hex[:8]}")
    max_subagents: int = 5
    timeout_minutes: int = 10
    lead_model: ModelType = ModelType.CLAUDE_SONNET
    subagent_model: ModelType = ModelType.CLAUDE_HAIKU
    synthesis_model: ModelType = ModelType.CLAUDE_OPUS
    workspace_dir: Optional[Path] = None
    deliverable_format: Optional[str] = None  # e.g., "HTML", "JSON", "markdown", "PDF"
    source: str = "dashboard"  # "dashboard" | "email" | "api" - tracks origin of investigation
    skip_global_state: bool = False  # When True, don't write to investigation_state.json (for email concurrency)

    # Adversarial validation settings
    enable_validation: bool = True  # Enable fact-checking before synthesis
    validation_filter_mode: str = "balanced"  # "strict", "annotated", or "balanced"

    def __post_init__(self):
        if self.timeout_minutes < 1:
            self.timeout_minutes = 1
        if self.max_subagents < 1:
            self.max_subagents = 1
        if self.workspace_dir is None:
            self.workspace_dir = BASE_DIR / "investigations" / self.investigation_id
        elif isinstance(self.workspace_dir, str):
            self.workspace_dir = Path(self.workspace_dir)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "investigation_id": self.investigation_id,
            "max_subagents": self.max_subagents,
            "timeout_minutes": self.timeout_minutes,
            "lead_model": self.lead_model.value,
            "subagent_model": self.subagent_model.value,
            "synthesis_model": self.synthesis_model.value,
            "workspace_dir": str(self.workspace_dir),
            "deliverable_format": self.deliverable_format,
            "source": self.source,
            "skip_global_state": self.skip_global_state,
            "enable_validation": self.enable_validation,
            "validation_filter_mode": self.validation_filter_mode,
        }


@dataclass
class SubagentResult:
    """Result from a single subagent exploration."""
    subagent_id: str
    focus_area: str
    findings: str
    elapsed_seconds: float
    status: str = "completed"
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvestigationResult:
    """Complete result from an investigation."""
    investigation_id: str
    query: str
    status: InvestigationStatus
    subagent_results: List[SubagentResult]
    synthesis: Optional[str]
    report_path: Optional[Path]
    started_at: str
    completed_at: Optional[str]
    elapsed_seconds: float
    error: Optional[str] = None

    # Validation metadata
    validation_stats: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        result = {
            "investigation_id": self.investigation_id,
            "query": self.query,
            "status": self.status.value,
            "subagent_results": [r.to_dict() for r in self.subagent_results],
            "synthesis": self.synthesis,
            "report_path": str(self.report_path) if self.report_path else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error
        }
        if self.validation_stats:
            result["validation_stats"] = self.validation_stats
        return result


# =============================================================================
# GROUND RULES LOADING
# =============================================================================

# =============================================================================
# URL HANDLER INTEGRATION
# =============================================================================

def _check_url_handlers():
    """Check if URL handlers are available."""
    global _url_handlers_available
    if _url_handlers_available is None:
        try:
            from url_handlers import classify_url, extract_metadata, extract_all_metadata
            _url_handlers_available = True
        except ImportError as e:
            logger.warning(f"URL handlers not available: {e}")
            _url_handlers_available = False
    return _url_handlers_available


def detect_and_extract_urls(query: str) -> List[Dict[str, Any]]:
    """
    Detect URLs in query and extract metadata using specialized handlers.

    Returns list of metadata dicts for each detected URL that has a handler.
    This enables pre-fetching GitHub stars, GitLab metrics, doc structure, etc.
    BEFORE the lead agent runs, so it can incorporate that data.
    """
    if not _check_url_handlers():
        return []

    try:
        from url_handlers import extract_all_metadata
        results = extract_all_metadata(query)
        if results:
            logger.info(f"Extracted metadata for {len(results)} URLs from query")
        return results
    except Exception as e:
        logger.warning(f"URL metadata extraction failed: {e}")
        return []


def get_url_handler_prompt(url: str, metadata: Dict[str, Any]) -> Optional[str]:
    """Get specialized analysis prompt for a URL based on its handler."""
    if not _check_url_handlers():
        return None

    try:
        from url_handlers import classify_url, get_handler
        handler_type = classify_url(url)
        if handler_type:
            handler = get_handler(handler_type)
            if handler:
                return handler.get_analysis_prompt(metadata)
    except Exception as e:
        logger.debug(f"Could not get handler prompt: {e}")
    return None


def format_url_metadata_for_prompt(url_metadata: List[Dict[str, Any]]) -> str:
    """
    Format extracted URL metadata as a section for the lead agent prompt.

    This gives the lead agent pre-fetched data about GitHub repos, GitLab projects,
    or documentation sites so it can make informed decisions about research directions.
    """
    if not url_metadata or not _check_url_handlers():
        return ""

    try:
        from url_handlers import classify_url, get_handler

        sections = []
        for meta in url_metadata:
            url = meta.get('url', '')
            handler_type = classify_url(url)
            if handler_type:
                handler = get_handler(handler_type)
                if handler:
                    section = handler.format_metadata_section(meta)
                    if section:
                        sections.append(section)

        if sections:
            return "\n\n## Pre-Fetched URL Metadata\n\n" + "\n\n---\n\n".join(sections)

    except Exception as e:
        logger.warning(f"Could not format URL metadata: {e}")

    return ""


def format_url_executive_summaries(url_metadata: List[Dict[str, Any]], findings: str = "") -> str:
    """
    Generate executive summaries for all URLs using their handlers.

    Returns formatted markdown suitable for inclusion at the top of reports.
    """
    if not url_metadata or not _check_url_handlers():
        return ""

    try:
        from url_handlers import classify_url, get_handler

        summaries = []
        for meta in url_metadata:
            url = meta.get('url', '')
            handler_type = classify_url(url)
            if handler_type:
                handler = get_handler(handler_type)
                if handler:
                    summary = handler.format_executive_summary(meta, findings)
                    if summary:
                        summaries.append(summary)

        if summaries:
            return "\n".join(summaries)

    except Exception as e:
        logger.warning(f"Could not generate executive summaries: {e}")

    return ""


# =============================================================================
# GROUND RULES LOADING
# =============================================================================

def load_investigation_ground_rules() -> str:
    """
    Load investigation ground rules from provider-aware files.

    Returns base investigation rules plus an optional provider overlay.
    """
    try:
        provider = _get_active_llm_provider()
        content, base_path, overlay_path, _ = load_ground_rules(
            provider=provider,
            investigation=True
        )
        if content:
            if overlay_path:
                logger.info(
                    "Loaded investigation ground rules from "
                    f"{base_path.name} + {overlay_path.name}"
                )
            else:
                logger.info(f"Loaded investigation ground rules from {base_path.name}")
            return content
        logger.warning(
            "Investigation ground rules file not found. "
            f"Tried base path: {base_path}"
        )
    except Exception as e:
        logger.warning(f"Failed to load investigation ground rules: {e}")
    return ""


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_investigation_state() -> dict:
    """Load current investigation state from file with shared file locking."""
    import fcntl
    try:
        if INVESTIGATION_STATE_PATH.exists():
            with open(INVESTIGATION_STATE_PATH, 'r') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"Failed to load investigation state: {e}")
    return {
        "current": None,
        "history": []
    }


def save_investigation_state(state: dict):
    """Save investigation state to file with atomic write."""
    import fcntl
    import tempfile
    try:
        STATE_DIR.mkdir(exist_ok=True)
        # Write to temp file first, then atomically rename to prevent truncation races
        fd, tmp_path = tempfile.mkstemp(dir=str(STATE_DIR), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, str(INVESTIGATION_STATE_PATH))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.error(f"Failed to save investigation state: {e}")


ALLOWED_STATUS_EXTRA_KEYS = {
    "progress",
    "error",
    "report_path",
    "workspace_dir",
    "provider",
    "model",
    "findings_count",
    "lead_agent_id",
    "subagent_count",
    "stage",
    "completed_at",
    "elapsed_seconds",
}


def update_investigation_status(investigation_id: str, status: InvestigationStatus, extra: dict = None):
    """Update the status of an investigation with exclusive file lock."""
    import fcntl
    STATE_DIR.mkdir(exist_ok=True)
    lock_path = STATE_DIR / ".investigation_state.lock"
    with open(lock_path, 'w') as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            state = load_investigation_state()
            if state.get("current") and state["current"].get("investigation_id") == investigation_id:
                state["current"]["status"] = status.value
                state["current"]["last_updated"] = datetime.now().isoformat()
                if extra:
                    filtered = {k: v for k, v in extra.items() if k in ALLOWED_STATUS_EXTRA_KEYS}
                    state["current"].update(filtered)
                save_investigation_state(state)
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write a text artifact owned by the investigation runner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{_threading.get_ident()}")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_json(path: Path, payload: dict) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _normalize_source_url(url: Optional[str]) -> str:
    if not isinstance(url, str):
        return ""
    normalized = url.strip()
    if not normalized:
        return ""
    normalized = normalized.split("#", 1)[0].rstrip("/")
    if normalized.startswith("http://"):
        normalized = "https://" + normalized[len("http://"):]
    return normalized.lower()


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists() or path.is_symlink():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _text_from_webproxy_payload(payload: dict) -> str:
    text = payload.get("text") or payload.get("content") or ""
    if text:
        return str(text)
    local_text_path = payload.get("local_text_path")
    if isinstance(local_text_path, str) and local_text_path:
        try:
            path = Path(local_text_path).resolve()
            allowed_roots = [
                (BASE_DIR / "WebProxy").resolve(),
                (BASE_DIR / "atlasforge_data").resolve(),
                Path("/mnt/ForgeRealm/AI-AtlasForge/WebProxy").resolve(),
                Path("/home/vader/AI-AtlasForge/WebProxy").resolve(),
            ]
            if any(_is_relative_to(path, root) for root in allowed_roots) and path.exists() and not path.is_symlink():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def archive_current_investigation():
    """
    Move the current investigation to history if it's completed or failed.

    This ensures completed investigations persist in history rather than
    being overwritten when a new investigation starts.
    Uses exclusive file lock across the full read-modify-write cycle.
    """
    import fcntl
    STATE_DIR.mkdir(exist_ok=True)
    lock_path = STATE_DIR / ".investigation_state.lock"
    with open(lock_path, 'w') as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            state = load_investigation_state()
            current = state.get("current")

            if not current:
                return False

            # Only archive if the investigation is in a terminal state
            terminal_states = [InvestigationStatus.COMPLETED.value, InvestigationStatus.FAILED.value]
            if current.get("status") not in terminal_states:
                return False

            # Move current to history
            if "history" not in state:
                state["history"] = []

            # Guard against double-archive: skip if this investigation_id is already in history
            current_id = current.get("investigation_id")
            if any(h.get("investigation_id") == current_id for h in state["history"]):
                state["current"] = None
                save_investigation_state(state)
                return False

            # Add to history (most recent first)
            state["history"].insert(0, current)

            # Clear current
            state["current"] = None

            save_investigation_state(state)
            logger.info(f"Archived investigation {current.get('investigation_id')} to history")
            return True
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def delete_investigation(investigation_id: str, delete_files: bool = False) -> dict:
    """
    Delete an investigation from history.

    Args:
        investigation_id: The ID of the investigation to delete
        delete_files: If True, also delete the workspace directory

    Returns:
        dict with 'success', 'message', and optionally 'files_deleted'
    """
    state = load_investigation_state()

    # Check if it's the current running investigation
    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        current_status = state["current"].get("status")
        terminal_states = [InvestigationStatus.COMPLETED.value, InvestigationStatus.FAILED.value]
        if current_status not in terminal_states:
            return {
                "success": False,
                "message": "Cannot delete a running investigation. Stop it first."
            }
        # It's current but completed/failed - archive it first then delete from history
        archive_current_investigation()
        state = load_investigation_state()  # Reload after archive

    # Find and remove from history
    history = state.get("history", [])
    original_len = len(history)

    # Find the investigation to get its workspace_dir before removal
    workspace_dir = None
    for inv in history:
        if inv.get("investigation_id") == investigation_id:
            workspace_dir = inv.get("workspace_dir")
            break

    # Remove from history
    state["history"] = [inv for inv in history if inv.get("investigation_id") != investigation_id]

    if len(state["history"]) == original_len:
        return {
            "success": False,
            "message": f"Investigation {investigation_id} not found"
        }

    save_investigation_state(state)

    result = {
        "success": True,
        "message": f"Investigation {investigation_id} deleted"
    }

    # Optionally delete workspace files
    if delete_files and workspace_dir:
        try:
            import shutil
            workspace_path = Path(workspace_dir).resolve()
            allowed_base = (BASE_DIR / "investigations").resolve()
            if not str(workspace_path).startswith(str(allowed_base) + os.sep):
                result["files_deleted"] = False
                result["file_error"] = "Path validation failed: workspace outside investigations directory"
                logger.warning(f"Path traversal blocked in delete_investigation: {workspace_dir}")
            elif workspace_path.exists():
                shutil.rmtree(workspace_path)
                result["files_deleted"] = True
                result["message"] += f" (workspace deleted: {workspace_dir})"
                logger.info(f"Deleted workspace directory: {workspace_dir}")
        except Exception as e:
            result["files_deleted"] = False
            result["file_error"] = "Failed to delete workspace files"
            logger.exception(f"Failed to delete workspace {workspace_dir}: {e}")

    return result


def delete_investigations_bulk(investigation_ids: list, delete_files: bool = False) -> dict:
    """
    Delete multiple investigations from history.

    Args:
        investigation_ids: List of investigation IDs to delete
        delete_files: If True, also delete workspace directories

    Returns:
        dict with 'success', 'deleted_count', 'failed', 'message'
    """
    if not isinstance(investigation_ids, (list, tuple)):
        return {"success": False, "deleted_count": 0, "deleted": [], "failed": [],
                "message": "investigation_ids must be a list"}

    deleted = []
    failed = []

    for inv_id in investigation_ids:
        if not isinstance(inv_id, str):
            failed.append({"id": str(inv_id), "reason": "ID must be a string"})
            continue
        result = delete_investigation(inv_id, delete_files=delete_files)
        if result["success"]:
            deleted.append(inv_id)
        else:
            failed.append({"id": inv_id, "reason": result["message"]})

    return {
        "success": len(deleted) > 0,
        "deleted_count": len(deleted),
        "deleted": deleted,
        "failed": failed,
        "message": f"Deleted {len(deleted)} investigation(s)" + (f", {len(failed)} failed" if failed else "")
    }


# =============================================================================
# CLAUDE INVOCATION
# =============================================================================

def invoke_claude(
    prompt: str,
    model: ModelType = ModelType.CLAUDE_SONNET,
    system_prompt: Optional[str] = None,
    timeout: int = 120,
    cwd: Optional[Path] = None,
    artifact_event_file: Optional[Path] = None,
    artifact_sources_file: Optional[Path] = None,
    artifact_label: Optional[str] = None,
    stream_context: str = "investigation",
) -> tuple[str, float]:
    """
    Invoke Claude CLI with the given prompt.

    Returns:
        Tuple of (response_text, elapsed_seconds)
    """
    if cwd is None:
        cwd = BASE_DIR

    start_time = time.time()
    provider = _get_active_llm_provider()
    env = os.environ.copy()
    # Prevent "Claude Code cannot be launched inside another Claude Code session" error
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    if provider == "codex":
        from WebProxy import codex_proxy_cli_args
        cmd = ["codex"]
        cmd.extend(codex_proxy_cli_args())
        if _codex_web_search_enabled():
            # Native Responses web_search. Off by default so proxy MCP is authoritative.
            cmd.append("--search")
        cmd.extend([
            "exec",
            "--color", "never",
        ])
        if _codex_autonomous_enabled():
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        # Optional explicit model override for Codex provider.
        codex_model = os.environ.get("ATLASFORGE_CODEX_MODEL", "").strip()
        if codex_model and _MODEL_NAME_RE.match(codex_model):
            cmd.extend(["--model", codex_model])
        cmd.append("-")

        full_prompt = prompt
        if system_prompt:
            # Codex exec path here does not expose a dedicated system prompt flag.
            full_prompt = (
                "System instructions:\n"
                f"{system_prompt}\n\n"
                "User task:\n"
                f"{prompt}"
            )
    elif provider == "gemini":
        cmd = ["gemini"]
        if _gemini_autonomous_enabled():
            cmd.append("--yolo")
        cmd.extend(["--output-format", "json"])

        # Model resolution for Gemini
        gemini_model = os.environ.get("ATLASFORGE_GEMINI_MODEL", "").strip()
        if not gemini_model:
            # Map Claude model tiers to Gemini tiers if no global override
            if model == ModelType.CLAUDE_HAIKU:
                gemini_model = os.environ.get("ATLASFORGE_GEMINI_MODEL_FAST", "").strip()
            elif model == ModelType.CLAUDE_OPUS:
                gemini_model = os.environ.get("ATLASFORGE_GEMINI_MODEL_POWERFUL", "").strip()
            else:  # CLAUDE_SONNET or default
                gemini_model = os.environ.get("ATLASFORGE_GEMINI_MODEL_BALANCED", "").strip()

        if gemini_model and _MODEL_NAME_RE.match(gemini_model):
            cmd.extend(["-m", gemini_model])

        full_prompt = prompt
        if system_prompt:
            full_prompt = (
                "System instructions:\n"
                f"{system_prompt}\n\n"
                "User task:\n"
                f"{prompt}"
            )
        # Gemini CLI may rely on GEMINI_API_KEY in non-interactive/headless mode.
        if not env.get("GEMINI_API_KEY"):
            google_api_key = env.get("GOOGLE_API_KEY", "").strip()
            if google_api_key:
                env["GEMINI_API_KEY"] = google_api_key
        # Keep HOME explicit so Gemini resolves local auth/config consistently.
        env.setdefault("HOME", str(Path.home()))
    else:
        cmd = ["claude", "-p", "--dangerously-skip-permissions"]
        if model:
            cmd.extend(["--model", model.value])
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        # Route WebSearch/WebFetch through the AtlasForge local proxy MCP.
        # Fail loudly if routing can't be set up — silent fallback to the
        # built-in (filtered) web tools produces mysteriously degraded
        # research output and contradicts the CLAUDE.md guarantee.
        from WebProxy import proxy_cli_args
        cmd.extend(proxy_cli_args(""))
        # Add stream-json for real-time agent activity visibility
        cmd.extend(["--output-format", "stream-json", "--verbose"])
        full_prompt = prompt

    # Claude provider: use streaming Popen for real-time agent activity
    if provider == "claude":
        _agent_id, _agent_label = _next_investigation_agent_id()
        _stream_file = None
        _comp = None
        _recon = None
        try:
            from agent_stream_manager import (
                register_agent as _reg,
                update_agent_pid as _upd_pid,
                complete_agent as _comp_fn,
                stream_stdout_to_file as _stream_fn,
                reconstruct_text_from_stream_file as _recon_fn,
            )
            if stream_context not in {"mission", "investigation"}:
                stream_context = "investigation"
            _stream_file = _reg(stream_context, _agent_id, _agent_label, pid=None)
            _comp = _comp_fn
            _recon = _recon_fn
        except Exception as e:
            logger.warning(f"Stream registration failed: {e}")
            _agent_id = None
            _stream_file = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(cwd),
                env=env,
                start_new_session=True
            )
        except Exception as e:
            return f"ERROR: {str(e)}", time.time() - start_time

        if _stream_file and _agent_id:
            try:
                _upd_pid(_agent_id, proc.pid)
                t = _threading.Thread(
                    target=_stream_fn,
                    args=(
                        proc,
                        _stream_file,
                        _agent_id,
                        artifact_event_file,
                        artifact_sources_file,
                        artifact_label,
                    ),
                    daemon=True,
                    name=f"stream-{_agent_id}"
                )
                t.start()
            except Exception as e:
                logger.warning(f"Streaming thread setup failed: {e}")
                _stream_file = None
                _agent_id = None

        _prompt_delivered = True
        try:
            proc.stdin.write(full_prompt)
            proc.stdin.close()
        except BrokenPipeError:
            logger.warning("BrokenPipeError writing to subprocess stdin", exc_info=True)
            _prompt_delivered = False
        except Exception:
            logger.warning("Error writing to subprocess stdin", exc_info=True)
            _prompt_delivered = False

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try: proc.kill(); proc.wait(timeout=5)
            except Exception as e: logger.warning(f"Failed to kill subprocess: {e}")
            if _agent_id and _comp:
                try: _comp(_agent_id, error='timeout')
                except Exception: pass
            return "ERROR: Timeout", time.time() - start_time

        elapsed = time.time() - start_time
        try:
            stderr_text = proc.stderr.read()
        except Exception:
            stderr_text = ""

        if proc.returncode == 0:
            if not _prompt_delivered:
                # Prompt was not fully delivered; output is unreliable
                logger.warning("Subprocess exited 0 but prompt delivery was partial — discarding output")
                return "ERROR: Partial prompt delivery", elapsed
            if _stream_file and _recon:
                response = _recon(_stream_file, provider='claude') or ""
            else:
                # Fallback: read stdout directly when stream wasn't available
                try:
                    response = proc.stdout.read() if proc.stdout else ""
                except Exception:
                    response = ""
            if _agent_id and _comp:
                try: _comp(_agent_id)
                except Exception: pass
            return response, elapsed
        else:
            if _agent_id and _comp:
                try: _comp(_agent_id, error=f'rc:{proc.returncode}')
                except Exception: pass
            return f"ERROR: {stderr_text}", elapsed

    # Non-Claude providers: use original subprocess.run() path
    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=env,
            start_new_session=True  # Prevent FD inheritance blocking from background processes
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:
            response = result.stdout.strip()
            # Handle Gemini CLI JSON wrapper
            if provider == "gemini":
                try:
                    data = json.loads(response)
                    if isinstance(data, dict) and "response" in data:
                        response = data["response"]
                except json.JSONDecodeError:
                    pass
            return response, elapsed
        else:
            if provider == "gemini":
                stderr_text = (result.stderr or "").strip()
                lines = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
                noise_prefixes = (
                    "YOLO mode is enabled.",
                    "Hook registry initialized",
                    "Both GOOGLE_API_KEY and GEMINI_API_KEY are set.",
                )
                filtered = [ln for ln in lines if not ln.startswith(noise_prefixes)]
                candidate_lines = filtered or lines
                best_line = next(
                    (ln for ln in candidate_lines if "error" in ln.lower() or "failed" in ln.lower()),
                    candidate_lines[0] if candidate_lines else "Gemini CLI failed",
                )
                lower = stderr_text.lower()
                if "error authenticating" in lower or "listen eperm" in lower:
                    return (
                        "ERROR: Gemini authentication failed in headless mode. "
                        "Set GEMINI_API_KEY for the dashboard process (or run `gemini` login in the same runtime environment).",
                        elapsed,
                    )
                if "fetch failed" in lower or "error generating content via api" in lower:
                    return (
                        "ERROR: Gemini API request failed (network/API). "
                        "Verify outbound internet access and API key validity for this runtime.",
                        elapsed,
                    )
                return (f"ERROR: {best_line}", elapsed)
            return f"ERROR: {result.stderr}", elapsed
    except subprocess.TimeoutExpired:
        return "ERROR: Timeout", time.time() - start_time
    except Exception as e:
        return f"ERROR: {str(e)}", time.time() - start_time


# =============================================================================
# INVESTIGATION PROMPTS
# =============================================================================

def build_lead_agent_prompt(query: str, max_subagents: int, deliverable_format: str = None, ground_rules: str = "") -> str:
    """Build the prompt for the lead investigation agent."""

    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    deliverable_instruction = ""
    if deliverable_format:
        deliverable_instruction = f"""
## Deliverable Format Requested
The user has requested the final output in: **{deliverable_format}**
Ensure your research directions account for gathering information needed to produce this deliverable.
"""

    return f"""{ground_rules_section}You are a lead investigation agent conducting a deep-dive research analysis.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH agent, NOT limited to software development.
You can and SHOULD investigate ANY topic including but not limited to:
- Gaming (builds, strategies, mechanics, lore)
- Science (physics, biology, chemistry, mathematics)
- Technology (hardware, software, engineering)
- History, geography, culture
- Business, economics, market research
- Creative topics (art, music, writing)
- Sports, fitness, health
- Any other domain the user asks about

If the query is about software/code, investigate software.
If the query is about gaming, investigate gaming.
If the query is about physics, investigate physics.
**NEVER refuse to investigate a topic because it's "outside your scope."**

## Investigation Query
{query}
{deliverable_instruction}
## Your Task

1. **Analyze the Query**: Understand what exactly needs to be investigated and why. Accept the query as-is - do NOT suggest the user has the wrong tool or should go elsewhere.

2. **Decompose into Research Directions**: Identify {max_subagents} independent areas that should be explored to fully understand this topic. Each should be a distinct, parallelizable research direction.

3. **For each research direction, provide**:
   - A clear focus area name (2-5 words)
   - A specific research prompt for a subagent (detailed enough that the subagent can work independently)
   - Whether the subagent should prioritize web research vs local file exploration

## Output Format

Respond with a JSON object in this EXACT format:

```json
{{
    "understanding": "Brief summary of what this investigation is about",
    "domain": "The domain of this query (e.g., 'gaming', 'physics', 'software', 'general')",
    "key_questions": ["Question 1", "Question 2", ...],
    "research_directions": [
        {{
            "focus_area": "Area name",
            "prompt": "Detailed research prompt for the subagent...",
            "research_type": "web" | "local" | "both"
        }},
        ...
    ]
}}
```

Important:
- Provide exactly {max_subagents} research directions
- Each prompt should be self-contained and specific
- Focus on exploration and understanding
- Subagents have access to: web search, file reading, and documentation lookup
- For non-software topics, prioritize web research
- For software/codebase topics, prioritize local file exploration
"""


def build_subagent_prompt(focus_area: str, base_prompt: str, investigation_query: str, research_type: str = "both", ground_rules: str = "") -> str:
    """Build the prompt for a subagent exploration."""

    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    # Build research guidelines based on type
    if research_type == "web":
        research_guidelines = """
## Research Guidelines

1. **Use WebSearch and WebFetch tools** to find authoritative sources on this topic
2. **Use PaperFetch for paper/PDF sources before quoting a paper** (arXiv, direct PDFs, open-access papers). WebFetch is for webpages; PaperFetch downloads the paper artifact and extracts paper text.
3. Search for recent information, guides, documentation, and expert opinions
4. Cross-reference multiple sources for accuracy
5. Focus on finding practical, actionable information
6. Document your sources with URLs where possible
7. Provide clear, well-researched insights
"""
    elif research_type == "local":
        research_guidelines = """
## Research Guidelines

1. **Use Read, Glob, and Grep tools** to explore the local codebase/files
2. Focus on understanding the structure and implementation
3. Document relevant files and their purposes
4. Do NOT make any code changes or create files
5. Provide clear, actionable insights
"""
    else:  # both
        research_guidelines = """
## Research Guidelines

1. **Use ALL available tools** as appropriate for this topic:
   - WebSearch/WebFetch for external information, guides, and documentation
   - PaperFetch for arXiv/direct PDF/open-access paper sources before quoting papers
   - Read/Glob/Grep for local codebase or file exploration
2. Combine web research with local exploration when relevant
3. Cross-reference sources for accuracy
4. Focus on finding practical, actionable information
5. Document your sources (URLs or file paths as applicable)
6. Provide clear, well-researched insights
"""

    return f"""{ground_rules_section}You are a research subagent exploring a specific aspect of an investigation.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH agent. You can investigate ANY topic - gaming, science, software,
history, sports, business, or any other domain. Your job is to thoroughly research your assigned
focus area, not to question whether it's appropriate.

## Original Investigation Query
{investigation_query}

## Your Focus Area
{focus_area}

## Your Task
{base_prompt}
{research_guidelines}
## Output Format

Respond with a JSON object:

```json
{{
    "focus_area": "{focus_area}",
    "key_findings": [
        "Finding 1",
        "Finding 2",
        ...
    ],
    "sources": [
        {{"type": "web|file", "reference": "URL or file path", "relevance": "why it matters"}}
    ],
    "insights": "Your analysis and understanding of this area",
    "recommendations": ["Actionable recommendation 1", "Recommendation 2"],
    "follow_up_questions": ["Question 1", "Question 2"]
}}
```
"""


def build_synthesis_prompt(
    query: str,
    subagent_results: List[SubagentResult],
    deliverable_format: str = None,
    source: str = "dashboard",
    ground_rules: str = "",
    evidence_index: str = "",
) -> str:
    """Build the prompt for synthesizing subagent findings.

    Args:
        query: The original investigation query
        subagent_results: Results from parallel subagent explorations
        deliverable_format: Optional format (HTML, JSON, markdown)
        source: Origin of investigation - "dashboard", "email", or "api"
               When "email", removes mission-style language (phases, timelines, next steps)
        ground_rules: Investigation ground rules to include in prompt
        evidence_index: Compact index of investigation-owned source payloads
    """
    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    findings_text = "\n\n".join([
        f"### {r.focus_area}\n{r.findings}"
        for r in subagent_results if r.status == "completed"
    ])

    # Build format-specific instructions
    if deliverable_format:
        format_lower = deliverable_format.lower()
        if "html" in format_lower:
            # Truncate query for title (escape HTML special chars — & first to avoid double-escape)
            title_query = query[:50].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            format_instruction = f"""
=== CRITICAL: OUTPUT FORMAT REQUIREMENT ===

Your response MUST be a complete HTML document. This is MANDATORY.

REQUIRED FORMAT:
1. Your response MUST start with exactly: <!DOCTYPE html>
2. Your response MUST include: <html>, <head>, <body> tags
3. Your response MUST include <style> block with CSS

FORBIDDEN:
- DO NOT use markdown syntax (no #, ##, **, -, etc.)
- DO NOT wrap your HTML in ```html code fences
- DO NOT include any text before <!DOCTYPE html>
- DO NOT output anything that is not valid HTML

TEMPLATE TO FOLLOW:
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {title_query}...</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; background: #f8f9fa; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.5rem; }}
        h2 {{ color: #34495e; margin-top: 2rem; }}
        .summary {{ background: #e8f4f8; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .finding {{ background: white; padding: 1rem; margin: 0.5rem 0; border-left: 4px solid #3498db; }}
        ul {{ padding-left: 1.5rem; }}
        li {{ margin: 0.5rem 0; }}
    </style>
</head>
<body>
    <h1>Investigation Report</h1>
    <!-- Your content here using ONLY HTML tags -->
</body>
</html>

=== END FORMAT REQUIREMENT ===
"""
        elif "json" in format_lower:
            format_instruction = f"""
## Deliverable Format: JSON

The user requested a **JSON** deliverable. Create a well-structured JSON object containing:
- All key findings as structured data
- Recommendations as an array
- Sources/references
- Any other relevant data in machine-readable format

Start your response with a valid JSON object.
"""
        elif "pdf" in format_lower or "document" in format_lower:
            format_instruction = f"""
## Deliverable Format: Formatted Document

The user requested a formatted document. Create a well-structured markdown report that:
- Uses clear headings and subheadings
- Includes tables where appropriate
- Is suitable for conversion to PDF
- Is professional in tone and presentation
"""
        else:
            format_instruction = f"""
## Deliverable Format: {deliverable_format}

The user requested the output in **{deliverable_format}** format. Adapt your output to best match this format while including all relevant research findings.
"""
    else:
        # Email investigations get a streamlined format - NO mission language
        if source == "email":
            format_instruction = """
## Output Format

Create a focused research report with these sections:

# Investigation Report

## Summary
(2-3 paragraphs summarizing the key findings)

## Key Findings
(Bulleted list of the most important discoveries)

## Detailed Analysis
(Synthesized analysis organized by theme, not by subagent)

## Sources & References
(List of sources used in the research)

DO NOT include:
- "Next Steps" or action items
- "Phase 1, 2, 3" or project phases
- "Weeks 1-2" or any timeline estimates
- "If this becomes a mission..." or similar language
- Recommendations for further work
- Any references to missions or projects

Just report the FINDINGS. This is a standalone research report, not a mission proposal.
"""
        else:
            format_instruction = """
## Output Format

Create a comprehensive markdown report with these sections:

# Investigation Report

## Executive Summary
(2-3 paragraphs summarizing the key findings)

## Key Findings
(Bulleted list of the most important discoveries)

## Detailed Analysis
(Synthesized analysis organized by theme, not by subagent)

## Recommendations
(Specific actionable recommendations based on findings)

## Follow-up Questions
(Important questions that remain unanswered)

## Next Steps
(If this becomes a full mission, what should be done?)
"""

    return f"""{ground_rules_section}You are synthesizing research findings from multiple parallel investigations.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH synthesizer. Your job is to combine findings from any domain -
gaming, science, software, business, or any other topic. Produce a complete, useful deliverable
that answers the original query. Do NOT question whether this is the right tool for the job.
DELIVER what the user asked for.

## Original Investigation Query
{query}

## Subagent Findings

{findings_text}

## Investigation-Owned Source Evidence

{evidence_index or "No runner-pinned WebProxy source payloads were captured for this investigation."}

## Your Task

Synthesize these findings into a comprehensive deliverable that fully addresses the original query.
Include specific, actionable information - not just suggestions to "consult other resources."
{format_instruction}
## Important

- Include CONCRETE information from the research, not just pointers to other resources
- If the query asks for a specific build, config, or setup - PROVIDE IT based on research
- If information is incomplete, note what's missing but still provide the best answer possible
- Use the Investigation-Owned Source Evidence index as the audit trail for fetched sources.
- Do not quote a source unless the quoted material is present in subagent findings or validated findings and traceable to a listed evidence payload.
- Do NOT tell the user "this is outside my scope" - that is NEVER true for this system
- Do NOT suggest the user use a different tool - YOU are the research tool
"""


def build_synthesis_prompt_validated(
    query: str,
    validated_findings_text: str,
    validation_stats: dict,
    deliverable_format: str = None,
    source: str = "dashboard",
    ground_rules: str = "",
    evidence_index: str = "",
) -> str:
    """Build the prompt for synthesizing VALIDATED subagent findings.

    This version uses pre-validated findings where claims have been fact-checked
    and filtered/annotated based on source verification.

    Args:
        query: The original investigation query
        validated_findings_text: Pre-processed findings text with validation markers
        validation_stats: Dictionary with validation statistics
        deliverable_format: Optional format (HTML, JSON, markdown)
        source: Origin of investigation - "dashboard", "email", or "api"
        ground_rules: Investigation ground rules to include in prompt
        evidence_index: Compact index of investigation-owned source payloads
    """
    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""
    # Build validation summary
    total = validation_stats.get("total_claims", 0)
    supported = validation_stats.get("supported_claims", 0)
    unsupported = validation_stats.get("unsupported_claims", 0)
    unverifiable = validation_stats.get("unverifiable_claims", 0)

    validation_note = f"""
## IMPORTANT: Citation Validation Applied

These findings have been fact-checked by independent validator agents:
- **{supported}/{total}** claims verified by cited sources
- **{unsupported}** claims contradicted or unsupported by sources (marked/removed)
- **{unverifiable}** claims could not be verified (source unavailable)

Findings marked with:
- ✅ = Verified by source
- 🔶 = Partially verified
- ⚠️ = Unverified (source inaccessible)
- ❌ = Disputed (source contradicts claim)

ONLY synthesize information that has been verified or partially verified.
Treat unverified claims with appropriate skepticism.
Do NOT include disputed claims in your synthesis.
"""

    # Build format-specific instructions (reuse same logic)
    if deliverable_format:
        format_lower = deliverable_format.lower()
        if "html" in format_lower:
            title_query = query[:50].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            format_instruction = f"""
=== CRITICAL: OUTPUT FORMAT REQUIREMENT ===

Your response MUST be a complete HTML document. This is MANDATORY.

REQUIRED FORMAT:
1. Your response MUST start with exactly: <!DOCTYPE html>
2. Your response MUST include: <html>, <head>, <body> tags
3. Your response MUST include <style> block with CSS

=== END FORMAT REQUIREMENT ===
"""
        elif "json" in format_lower:
            format_instruction = """
## Deliverable Format: JSON
Output a well-structured JSON object with all verified findings.
"""
        else:
            format_instruction = f"""
## Deliverable Format: {deliverable_format}
"""
    else:
        if source == "email":
            format_instruction = """
## Output Format

Create a focused research report with these sections:
# Investigation Report
## Summary
## Verified Findings (from fact-checked sources)
## Detailed Analysis
## Sources & References

DO NOT include: "Next Steps", timelines, or mission-style language.
"""
        else:
            format_instruction = """
## Output Format

Create a comprehensive markdown report with:
# Investigation Report
## Executive Summary
## Verified Key Findings
## Detailed Analysis
## Recommendations
## Data Quality Note (mention validation stats)
"""

    return f"""{ground_rules_section}You are synthesizing VALIDATED research findings.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH synthesizer. Produce a complete, useful deliverable
that answers the original query based on VERIFIED information.

## Original Investigation Query
{query}
{validation_note}
## Validated Subagent Findings

{validated_findings_text}

## Investigation-Owned Source Evidence

{evidence_index or "No runner-pinned WebProxy source payloads were captured for this investigation."}

## Your Task

Synthesize these VALIDATED findings into a comprehensive deliverable.
Prioritize verified information. Note confidence levels where relevant.
{format_instruction}
## Important

- Prioritize VERIFIED claims over unverified ones
- Do NOT include disputed/unsupported claims in your synthesis
- If critical information is unverified, note this caveat
- The validation ensures you're working with fact-checked information
- Use the Investigation-Owned Source Evidence index as the audit trail for fetched sources.
- Do not quote a source unless the quoted material is present in validated findings and traceable to a listed evidence payload.
"""


# =============================================================================
# HTML FORMAT VALIDATION AND CONVERSION
# =============================================================================

def validate_html_format(response: str) -> tuple:
    """
    Validate that a response is proper HTML format.

    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    import re as regex_module

    issues = []
    response_stripped = response.strip()

    # Check for DOCTYPE
    if not response_stripped.lower().startswith('<!doctype html'):
        issues.append("Missing <!DOCTYPE html> declaration at start")

    # Check for HTML structure
    if '<html' not in response_stripped.lower():
        issues.append("Missing <html> tag")
    if '<head' not in response_stripped.lower():
        issues.append("Missing <head> tag")
    if '<body' not in response_stripped.lower():
        issues.append("Missing <body> tag")

    # Check for markdown contamination (only if outside of code blocks)
    # First, remove any code/pre blocks to avoid false positives
    clean_response = regex_module.sub(r'<code>.*?</code>', '', response_stripped, flags=regex_module.DOTALL)
    clean_response = regex_module.sub(r'<pre>.*?</pre>', '', clean_response, flags=regex_module.DOTALL)

    markdown_patterns = [
        (r'^# ', "Contains markdown header (# )"),
        (r'^## ', "Contains markdown header (## )"),
        (r'\*\*[^*]+\*\*', "Contains markdown bold (**)"),
        (r'^```', "Contains markdown code fence (```)"),
    ]
    for pattern, message in markdown_patterns:
        if regex_module.search(pattern, clean_response, regex_module.MULTILINE):
            issues.append(message)

    return len(issues) == 0, issues


def markdown_to_html(markdown_text: str, query: str) -> str:
    """
    Convert markdown text to HTML as a fallback.

    Args:
        markdown_text: The markdown content to convert
        query: The original investigation query for the title

    Returns:
        Complete HTML document
    """
    import re as regex_module

    content = markdown_text

    # HTML-escape helper for markdown content
    def _esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')

    # Remove code fences first (may wrap entire response)
    content = regex_module.sub(r'^```html\s*\n?', '', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^```\w*\s*\n?', '', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'\n?```$', '', content, flags=regex_module.MULTILINE)

    # Extract markdown links and bare URLs BEFORE escaping so we can process them safely
    # Placeholder map: replace links with tokens, escape everything else, restore links as safe HTML
    _link_placeholders = {}
    _placeholder_counter = [0]

    def _extract_md_link(match):
        text = _esc(match.group(1))
        url = match.group(2).strip()
        # Decode percent-encoded chars for scheme check (blocks %6Aavascript: etc.)
        try:
            from urllib.parse import unquote
            url_decoded = unquote(url)
        except Exception:
            url_decoded = url
        # Strip ASCII control chars that browsers ignore but could bypass scheme checks
        url_clean = re.sub(r'[\x00-\x1f]', '', url_decoded)
        url_lower = url_clean.lower().lstrip()
        # Only allow safe URL schemes
        if url_lower.startswith(('javascript:', 'data:', 'vbscript:', 'file:')):
            safe_html = f'<a href="#">{text}</a>'
        else:
            safe_url = _esc(url)
            safe_html = f'<a href="{safe_url}">{text}</a>'
        token = f'\x00LINK{_placeholder_counter[0]}\x00'
        _link_placeholders[token] = safe_html
        _placeholder_counter[0] += 1
        return token

    def _extract_bare_url(match):
        raw_url = match.group(1)
        safe_url = _esc(raw_url)
        safe_html = f'<a href="{safe_url}">{safe_url}</a>'
        token = f'\x00LINK{_placeholder_counter[0]}\x00'
        _link_placeholders[token] = safe_html
        _placeholder_counter[0] += 1
        return token

    # Extract links into placeholders (before escaping)
    content = regex_module.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        _extract_md_link,
        content
    )
    content = regex_module.sub(
        r'(?<!\()(https?://[^\s<>\[\]()]+)',
        _extract_bare_url,
        content
    )

    # Process markdown elements BEFORE HTML-escaping (so # and * are still intact),
    # but escape captured content inside each element to prevent XSS.
    def _header_replace(m, tag):
        return f'<{tag}>{_esc(m.group(1))}</{tag}>'

    content = regex_module.sub(r'^### (.+)$', lambda m: _header_replace(m, 'h3'), content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^## (.+)$', lambda m: _header_replace(m, 'h2'), content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^# (.+)$', lambda m: _header_replace(m, 'h1'), content, flags=regex_module.MULTILINE)

    # Bold and italic before escape (markdown * chars get escaped otherwise)
    content = regex_module.sub(r'\*\*(.+?)\*\*', lambda m: f'<strong>{_esc(m.group(1))}</strong>', content)
    content = regex_module.sub(r'\*(.+?)\*', lambda m: f'<em>{_esc(m.group(1))}</em>', content)

    # Process bullet points before escape (- and * prefixes get escaped otherwise)
    lines = content.split('\n')
    in_list = False
    converted_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith('- ') or stripped_line.startswith('* '):
            if not in_list:
                converted_lines.append('<ul>')
                in_list = True
            # Don't re-escape: bullet content may already contain safe HTML tags
            # (<strong>, <em>) from bold/italic processing above
            bullet_text = stripped_line[2:]
            # Only escape if no pre-processed HTML tags present
            if '<strong>' not in bullet_text and '<em>' not in bullet_text:
                bullet_text = _esc(bullet_text)
            converted_lines.append(f'<li>{bullet_text}</li>')
        else:
            if in_list:
                converted_lines.append('</ul>')
                in_list = False
            converted_lines.append(line)
    if in_list:
        converted_lines.append('</ul>')
    content = '\n'.join(converted_lines)

    # Now HTML-escape all remaining content (safe because links are placeholders,
    # and markdown elements have already been converted to HTML tags)
    # We need to escape only non-tag content
    _SAFE_TAG_RE = regex_module.compile(r'^<(h[1-6]|strong|em|ul|li|a|p)([ >])')
    _SAFE_CLOSE_RE = regex_module.compile(r'^</(h[1-6]|strong|em|ul|li|a|p)>$')

    def _escape_non_tags(text):
        """Escape HTML in text but preserve already-created HTML tags and their pre-escaped content."""
        result = []
        i = 0
        while i < len(text):
            if text[i] == '<':
                end = text.find('>', i)
                if end != -1:
                    tag_content = text[i:end + 1]
                    open_m = _SAFE_TAG_RE.match(tag_content)
                    close_m = _SAFE_CLOSE_RE.match(tag_content)
                    if open_m or close_m:
                        if open_m:
                            tag_name = open_m.group(1)
                            # For inline/block tags with content already escaped,
                            # find the matching close tag and pass through verbatim
                            close_tag = f'</{tag_name}>'
                            close_pos = text.find(close_tag, end + 1)
                            if close_pos != -1:
                                # Emit: open tag + inner content (already escaped) + close tag
                                result.append(text[i:close_pos + len(close_tag)])
                                i = close_pos + len(close_tag)
                                continue
                        # Standalone close tag or open with no close — just pass it through
                        result.append(tag_content)
                        i = end + 1
                        continue
            result.append(_esc(text[i]))
            i += 1
        return ''.join(result)

    content = _escape_non_tags(content)

    # Restore link placeholders
    for token, safe_html in _link_placeholders.items():
        content = content.replace(token, safe_html)

    # Wrap non-empty, non-tag lines in paragraphs
    lines = content.split('\n')
    final_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith('<'):
            final_lines.append(f'<p>{line}</p>')
        else:
            final_lines.append(line)
    content = '\n'.join(final_lines)

    # Escape query for title (& must be escaped first to avoid double-escape)
    title_query = query[:50].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    # Wrap in HTML template
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {title_query}...</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; background: #f8f9fa; color: #333; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.5rem; }}
        h2 {{ color: #34495e; margin-top: 2rem; }}
        h3 {{ color: #7f8c8d; }}
        p {{ margin: 1rem 0; }}
        ul {{ padding-left: 1.5rem; }}
        li {{ margin: 0.5rem 0; }}
        strong {{ color: #2c3e50; }}
    </style>
</head>
<body>
{content}
</body>
</html>'''


# =============================================================================
# PDF GENERATION
# =============================================================================

def generate_pdf_from_markdown(markdown_text: str, query: str) -> bytes:
    """
    Convert markdown report to PDF with clickable hyperlinks.

    Uses weasyprint for high-quality PDF generation with proper CSS styling
    and clickable hyperlinks.

    Args:
        markdown_text: The markdown content of the report
        query: The original investigation query (for title)

    Returns:
        PDF content as bytes
    """
    try:
        from weasyprint import HTML
    except ImportError:
        logger.warning("weasyprint not available, falling back to basic HTML")
        # Return None to signal fallback needed
        return None

    import io

    # Convert markdown to HTML first
    html_content = markdown_to_html(markdown_text, query)

    # Enhance HTML with better PDF-specific styling
    pdf_style = """
    <style>
        @page {
            margin: 2cm;
            size: A4;
            @bottom-center {
                content: counter(page);
                font-size: 10pt;
                color: #666;
            }
        }
        body {
            font-family: 'Helvetica', 'Arial', sans-serif;
            font-size: 11pt;
            line-height: 1.6;
            color: #333;
        }
        h1 {
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 0.3em;
            font-size: 20pt;
            margin-top: 1em;
        }
        h2 {
            color: #34495e;
            margin-top: 1.5em;
            font-size: 16pt;
        }
        h3 {
            color: #5d6d7e;
            margin-top: 1.2em;
            font-size: 13pt;
        }
        a {
            color: #2980b9;
            text-decoration: underline;
        }
        a:hover {
            color: #1a5276;
        }
        .summary-box {
            background: #ecf0f1;
            padding: 1em;
            border-radius: 4px;
            margin: 1em 0;
            border-left: 4px solid #3498db;
        }
        ul, ol {
            padding-left: 1.5em;
        }
        li {
            margin: 0.3em 0;
        }
        code {
            background: #f4f4f4;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 10pt;
        }
        pre {
            background: #f4f4f4;
            padding: 1em;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 9pt;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 1em 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background: #f5f5f5;
        }
        blockquote {
            border-left: 4px solid #bdc3c7;
            padding-left: 1em;
            margin-left: 0;
            color: #666;
            font-style: italic;
        }
    </style>
    """

    # Inject PDF-specific style into head
    if '<head>' in html_content:
        html_content = html_content.replace('<head>', f'<head>{pdf_style}')
    else:
        # If no head tag, wrap content
        html_content = f'<!DOCTYPE html><html><head>{pdf_style}</head><body>{html_content}</body></html>'

    # Generate PDF
    try:
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()
        logger.info(f"Generated PDF: {len(pdf_bytes)} bytes")
        return pdf_bytes
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        return None


def extract_short_summary(synthesis: str, max_sentences: int = 3) -> str:
    """
    Extract a short, natural summary from the full synthesis.

    Returns 2-3 sentences suitable for an email body.

    Args:
        synthesis: Full synthesis text (markdown or plain text)
        max_sentences: Maximum number of sentences to extract

    Returns:
        Short summary string
    """
    import re

    if not synthesis:
        return "Investigation completed."

    # Remove markdown formatting that doesn't read well in plain text
    clean_text = synthesis

    # Remove headers (# ## ###)
    clean_text = re.sub(r'^#+\s+.*$', '', clean_text, flags=re.MULTILINE)

    # Remove bold/italic markers
    clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_text)
    clean_text = re.sub(r'\*([^*]+)\*', r'\1', clean_text)

    # Remove bullet points
    clean_text = re.sub(r'^\s*[-*]\s+', '', clean_text, flags=re.MULTILINE)

    # Remove links but keep text
    clean_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean_text)

    # Try to find an executive summary section
    summary_match = re.search(
        r'(?:Executive Summary|Summary|Overview)[:\s]*\n(.*?)(?:\n##|\n\n\n|\Z)',
        synthesis,
        re.DOTALL | re.IGNORECASE
    )

    if summary_match:
        summary_text = summary_match.group(1).strip()
        # Clean the extracted summary
        summary_text = re.sub(r'^#+\s+.*$', '', summary_text, flags=re.MULTILINE)
        summary_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', summary_text)
        summary_text = re.sub(r'\*([^*]+)\*', r'\1', summary_text)
        summary_text = re.sub(r'^\s*[-*]\s+', '', summary_text, flags=re.MULTILINE)
        summary_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', summary_text)
    else:
        # Fall back to first few paragraphs
        paragraphs = [p.strip() for p in clean_text.split('\n\n') if p.strip()]
        # Skip empty or header-only paragraphs
        paragraphs = [p for p in paragraphs if len(p) > 20 and not p.startswith('#')]
        summary_text = paragraphs[0] if paragraphs else clean_text[:500]

    # Clean up whitespace
    summary_text = ' '.join(summary_text.split())

    # Extract first N sentences
    sentences = re.split(r'(?<=[.!?])\s+', summary_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    short_summary = ' '.join(sentences[:max_sentences])

    # Truncate if still too long
    if len(short_summary) > 500:
        short_summary = short_summary[:497] + '...'

    return short_summary if short_summary else "Investigation completed."


# =============================================================================
# INVESTIGATION RUNNER
# =============================================================================

class InvestigationRunner:
    """Runs a complete investigation workflow."""

    def __init__(self, config: InvestigationConfig):
        self.config = config
        self.subagent_results: List[SubagentResult] = []
        self.started_at: Optional[str] = None
        self.progress_callback: Optional[Callable[[str], None]] = None
        self._last_lead_error: Optional[str] = None
        # Load ground rules at startup
        self.ground_rules = load_investigation_ground_rules()
        # URL metadata extracted from query (GitHub repos, GitLab projects, docs, etc.)
        self.url_metadata: List[Dict[str, Any]] = []
        # Runner-owned WebProxy evidence loaded from subagent source_payloads.
        self.pinned_evidence_sources: Dict[str, Any] = {}
        self.pinned_evidence_records: List[Dict[str, Any]] = []
        # Check validator availability at startup (non-fatal warning if missing)
        self._validator_available = self._check_validator_health()

    def _check_validator_health(self) -> bool:
        """Check that investigation_validator is importable at startup.

        Returns True if the validator package exists and imports cleanly.
        Logs a warning (non-fatal) if not available.
        """
        try:
            import importlib
            validator_path = BASE_DIR / "investigation_validator"
            if not validator_path.exists() or not validator_path.is_dir():
                logger.warning(
                    f"investigation_validator not found at {validator_path}. "
                    "Validation will be skipped. To fix: ensure the directory exists and is accessible."
                )
                return False
            importlib.import_module("investigation_validator.orchestrator")
            importlib.import_module("investigation_validator.models")
            return True
        except ImportError as e:
            logger.warning(f"investigation_validator import check failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"investigation_validator health check error: {e}")
            return False

    def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> InvestigationResult:
        """
        Execute the complete investigation.

        Args:
            progress_callback: Optional function called with status updates

        Returns:
            InvestigationResult with all findings
        """
        self.progress_callback = progress_callback
        self.started_at = datetime.now().isoformat()
        start_time = time.time()

        # Create workspace
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = self.config.workspace_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        # Initialize state - SKIP for email investigations to enable true concurrency
        # Email investigations track state via email_inbox_state.json, not investigation_state.json
        if not self.config.skip_global_state:
            state = load_investigation_state()
            state["current"] = {
                "investigation_id": self.config.investigation_id,
                "query": self.config.query,
                "status": InvestigationStatus.ANALYZING.value,
                "started_at": self.started_at,
                "workspace_dir": str(self.config.workspace_dir),
                "source": self.config.source  # Track origin: "dashboard", "email", or "api"
            }
            save_investigation_state(state)

        try:
            # Step 0: Extract URL metadata (GitHub repos, GitLab projects, docs, etc.)
            # This happens BEFORE the lead agent, so we can inject pre-fetched data
            self._log("Detecting URLs and extracting metadata...")
            self.url_metadata = detect_and_extract_urls(self.config.query)
            if self.url_metadata:
                self._log(f"Extracted metadata for {len(self.url_metadata)} URLs: "
                         f"{', '.join(m.get('type', 'unknown') for m in self.url_metadata)}")

            # Step 1: Lead agent analyzes query and decomposes
            self._log("Starting investigation analysis...")
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.ANALYZING)

            research_directions = self._run_lead_agent()
            if not research_directions:
                raise Exception(self._last_lead_error or "Lead agent failed to provide research directions")

            # Step 2: Spawn parallel subagents
            self._log(f"Spawning {len(research_directions)} subagents...")
            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.SPAWNING_SUBAGENTS,
                    {"subagent_count": len(research_directions)}
                )

            # Step 3: Run subagents in parallel
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.EXPLORING)
            self.subagent_results = self._run_subagents(research_directions)
            self.pinned_evidence_sources, self.pinned_evidence_records = self._load_pinned_webproxy_evidence()
            if self.pinned_evidence_records:
                self._log(
                    f"Loaded {len(self.pinned_evidence_records)} runner-pinned WebProxy source payloads"
                )

            # Step 3.5: Adversarial validation (fact-check citations before synthesis)
            validated_findings = None
            if self.config.enable_validation:
                self._log("Running adversarial validation on findings...")
                if not self.config.skip_global_state:
                    update_investigation_status(self.config.investigation_id, InvestigationStatus.VALIDATING)
                validated_findings = self._validate_findings()

            # Step 4: Synthesize findings
            self._log("Synthesizing findings...")
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.SYNTHESIZING)
            synthesis = self._synthesize_findings(validated_findings)

            # Step 5: Write report (with appropriate file extension)
            if self.config.deliverable_format:
                fmt = self.config.deliverable_format.lower()
                if "html" in fmt:
                    report_path = artifacts_dir / "investigation_report.html"
                elif "json" in fmt:
                    report_path = artifacts_dir / "investigation_report.json"
                else:
                    report_path = artifacts_dir / "investigation_report.md"
            else:
                report_path = artifacts_dir / "investigation_report.md"

            report_path.write_text(synthesis)
            self._log(f"Report written to {report_path}")

            # Save findings JSON with validation metadata and URL handler results
            findings_path = artifacts_dir / "findings.json"
            findings_data = {
                "investigation_id": self.config.investigation_id,
                "query": self.config.query,
                "subagent_results": [r.to_dict() for r in self.subagent_results],
                "subagent_artifacts": [
                    {
                        "subagent_id": r.subagent_id,
                        "artifact_dir": str(artifacts_dir / "subagents" / r.subagent_id),
                        "events_path": str(artifacts_dir / "subagents" / r.subagent_id / "events.jsonl"),
                        "sources_path": str(artifacts_dir / "subagents" / r.subagent_id / "sources.jsonl"),
                        "final_path": str(artifacts_dir / "subagents" / r.subagent_id / "final.md"),
                        "result_path": str(artifacts_dir / "subagents" / r.subagent_id / "result.json"),
                    }
                    for r in self.subagent_results
                ],
                "validation": {
                    "enabled": self.config.enable_validation,
                    "filter_mode": self.config.validation_filter_mode,
                },
                "pinned_source_evidence": self.pinned_evidence_records,
                # Include URL handler metadata for rich reports
                "url_metadata": self.url_metadata if self.url_metadata else [],
            }

            # Add validation stats if validation was performed
            if validated_findings:
                findings_data["validation"]["stats"] = validated_findings.to_dict()
                findings_data["validation"]["total_claims"] = validated_findings.total_claims
                findings_data["validation"]["supported"] = validated_findings.supported_claims
                findings_data["validation"]["unsupported"] = validated_findings.unsupported_claims
                findings_data["validation"]["unverifiable"] = validated_findings.unverifiable_claims
                # Include flagged claims for audit trail
                findings_data["validation"]["flagged_claims"] = [
                    {"id": c.id, "text": c.text, "reason": c.flag_reason}
                    for c in validated_findings.claims if c.flagged
                ]

            with open(findings_path, 'w') as f:
                json.dump(findings_data, f, indent=2)

            elapsed = time.time() - start_time
            completed_at = datetime.now().isoformat()

            # Update final state (skip for email investigations)
            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.COMPLETED,
                    {
                        "completed_at": completed_at,
                        "elapsed_seconds": elapsed,
                        "report_path": str(report_path)
                    }
                )

            self._log(f"Investigation completed in {elapsed:.1f}s")

            # Ingest investigation findings into Knowledge Base
            try:
                self._ingest_to_knowledge_base()
            except Exception as kb_error:
                logger.warning(f"KB ingestion failed (non-fatal): {kb_error}")

            # Extract code blocks and inject to AI-AfterImage Code DB
            try:
                self._extract_and_inject_code_to_afterimage()
            except Exception as code_error:
                logger.warning(f"Code extraction/injection failed (non-fatal): {code_error}")

            # Archive completed investigation to history (for persistence)
            if not self.config.skip_global_state:
                archive_current_investigation()

            return InvestigationResult(
                investigation_id=self.config.investigation_id,
                query=self.config.query,
                status=InvestigationStatus.COMPLETED,
                subagent_results=self.subagent_results,
                synthesis=synthesis,
                report_path=report_path,
                started_at=self.started_at,
                completed_at=completed_at,
                elapsed_seconds=elapsed,
                validation_stats=validated_findings.to_dict() if validated_findings else None
            )

        except Exception:
            logger.exception("Investigation failed")
            elapsed = time.time() - start_time

            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.FAILED,
                    {
                        "error": "Investigation failed due to an internal error",
                        "completed_at": datetime.now().isoformat(),
                        "elapsed_seconds": elapsed,
                    }
                )
                # Archive failed investigation to history (for persistence)
                archive_current_investigation()

            return InvestigationResult(
                investigation_id=self.config.investigation_id,
                query=self.config.query,
                status=InvestigationStatus.FAILED,
                subagent_results=self.subagent_results,
                synthesis=None,
                report_path=None,
                started_at=self.started_at,
                completed_at=datetime.now().isoformat(),
                elapsed_seconds=elapsed,
                error="An internal error occurred during investigation"
            )

    def _log(self, message: str):
        """Log a message and call progress callback if set."""
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def _run_lead_agent(self) -> List[Dict[str, str]]:
        """Run the lead agent to decompose the query."""
        self._last_lead_error = None
        prompt = build_lead_agent_prompt(
            self.config.query,
            self.config.max_subagents,
            self.config.deliverable_format,
            self.ground_rules
        )

        # Inject pre-fetched URL metadata if available
        if self.url_metadata:
            metadata_section = format_url_metadata_for_prompt(self.url_metadata)
            if metadata_section:
                # Insert metadata before "## Your Task" section
                if "## Your Task" in prompt:
                    prompt = prompt.replace(
                        "## Your Task",
                        f"{metadata_section}\n\n## Your Task"
                    )
                else:
                    prompt = f"{prompt}\n\n{metadata_section}"

        timeout = max(
            300,
            int(self.config.timeout_minutes * 60 * 0.75),
            self.config.max_subagents * 20,
            len(self.config.query) // 60,
        )

        response, elapsed = invoke_claude(
            prompt=prompt,
            model=self.config.lead_model,
            timeout=timeout,
            cwd=self.config.workspace_dir
        )

        self._log(f"Lead agent completed in {elapsed:.1f}s")

        # Parse response
        try:
            stripped = (response or "").strip()
            if stripped.startswith("ERROR:"):
                self._last_lead_error = stripped.splitlines()[0]
                return []

            # Try to extract JSON from response
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', stripped, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
            else:
                # Try parsing entire response — use balanced brace matching
                start = stripped.find('{')
                if start >= 0:
                    depth = 0
                    data = None
                    for i in range(start, len(stripped)):
                        if stripped[i] == '{':
                            depth += 1
                        elif stripped[i] == '}':
                            depth -= 1
                            if depth == 0:
                                data = json.loads(stripped[start:i + 1])
                                break
                    if data is None:
                        raise ValueError("No JSON found in response")
                else:
                    raise ValueError("No JSON found in response")
            directions = data.get("research_directions", []) if isinstance(data, dict) else []
            if not isinstance(directions, list) or not directions:
                if isinstance(data, dict) and data.get("error"):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = str(err.get("message") or err.get("type") or "Unknown lead-agent error")
                    else:
                        msg = str(err)
                    if not msg or msg == "[object Object]":
                        msg = "Gemini returned an opaque error payload (likely API/network/auth issue)"
                    self._last_lead_error = f"Lead agent error: {msg}"
                else:
                    self._last_lead_error = "Lead agent returned no research directions"
                return []

            return directions

        except Exception as e:
            logger.error(f"Failed to parse lead agent response: {e}")
            logger.error(f"Response was: {response[:500]}")
            response_head = (response or "").strip()
            if response_head.startswith("ERROR:"):
                first_line = response_head.splitlines()[0]
                self._last_lead_error = first_line
            else:
                self._last_lead_error = "Lead agent failed to provide parseable JSON research directions"
            return []

    def _run_subagents(self, research_directions: List[Dict[str, str]]) -> List[SubagentResult]:
        """Run subagents in parallel, managed by the global SubagentPoolManager."""
        results = []

        # Since subagents run in PARALLEL, each agent gets the full time budget
        # (not divided by agent count). Use 45% of total budget for each
        # subagent invocation; high requested counts are handled in waves below.
        timeout_per_agent = max(60, int(self.config.timeout_minutes * 60 * 0.45))  # 45% of budget for subagents

        requested = len(research_directions)
        inv_id = self.config.investigation_id
        inv_start = time.time()

        # Request slots from pool manager (graceful fallback if unavailable)
        pool = None
        granted = requested
        try:
            from subagent_pool_manager import get_pool_manager
            pool = get_pool_manager()
            slot_req = pool.request_slots(
                inv_id=inv_id,
                count=requested,
                priority=10,  # NORMAL priority
                query=self.config.query,
            )
            granted = slot_req.granted
            if granted < requested:
                self._log(
                    f"[Pool] Granted {granted}/{requested} slots "
                    f"(quota={slot_req.quota_limit}, reason={slot_req.reason})"
                )
            else:
                self._log(f"[Pool] Granted all {granted} slots")
        except Exception as pool_exc:
            logger.warning(f"[Pool] Pool manager unavailable, running unconstrained: {pool_exc}")

        pending = list(enumerate(research_directions))
        wave = 0

        while pending:
            wave += 1
            if wave == 1:
                current_grant = granted
            elif pool is None:
                current_grant = len(pending)
            else:
                try:
                    slot_req = pool.request_slots(
                        inv_id=inv_id,
                        count=len(pending),
                        priority=10,
                        query=self.config.query,
                    )
                    current_grant = slot_req.granted
                    if current_grant < len(pending):
                        self._log(
                            f"[Pool] Wave {wave}: granted {current_grant}/{len(pending)} slots "
                            f"(quota={slot_req.quota_limit}, reason={slot_req.reason})"
                        )
                    else:
                        self._log(f"[Pool] Wave {wave}: granted all {current_grant} remaining slots")
                except Exception as pool_exc:
                    logger.warning(f"[Pool] Pool manager unavailable mid-run, running remaining unconstrained: {pool_exc}")
                    pool = None
                    current_grant = len(pending)

            if current_grant <= 0:
                self._log(f"[Pool] Wave {wave}: no slots available; waiting for pool capacity")
                time.sleep(5)
                continue

            active_items = pending[:current_grant]
            pending = pending[current_grant:]

            if pending:
                self._log(
                    f"Running subagent wave {wave}: {len(active_items)} active, "
                    f"{len(pending)} queued"
                )
            elif wave > 1:
                self._log(f"Running final subagent wave {wave}: {len(active_items)} active")

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_items)) as executor:
                futures = {}

                for i, direction in active_items:
                    focus_area = direction.get("focus_area", f"Area {i+1}")
                    base_prompt = direction.get("prompt", "Explore this area")
                    research_type = direction.get("research_type", "both")

                    subagent_id = f"{inv_id}_sub_{i}"
                    full_prompt = build_subagent_prompt(
                        focus_area,
                        base_prompt,
                        self.config.query,
                        research_type,
                        self.ground_rules
                    )

                    future = executor.submit(
                        self._run_single_subagent,
                        subagent_id,
                        focus_area,
                        full_prompt,
                        timeout_per_agent
                    )
                    futures[future] = (subagent_id, focus_area)

                for future in concurrent.futures.as_completed(futures):
                    subagent_id, focus_area = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        self._log(f"Subagent '{focus_area}' completed")
                    except Exception as e:
                        logger.error(f"Subagent {subagent_id} failed: {e}")
                        results.append(SubagentResult(
                            subagent_id=subagent_id,
                            focus_area=focus_area,
                            findings="",
                            elapsed_seconds=0,
                            status="failed",
                            error=str(e)
                        ))
                    finally:
                        # Release one slot per completed subagent so queued waves can start.
                        if pool is not None:
                            try:
                                pool.release_slots(inv_id, 1)
                            except Exception:
                                pass

        # Notify pool manager that investigation is done
        if pool is not None:
            try:
                elapsed = time.time() - inv_start
                success = any(r.status not in ("failed", "skipped") for r in results)
                pool.notify_investigation_complete(inv_id, elapsed_sec=elapsed, success=success)
            except Exception:
                pass

        return results

    def _run_single_subagent(
        self,
        subagent_id: str,
        focus_area: str,
        prompt: str,
        timeout: int
    ) -> SubagentResult:
        """Run a single subagent."""
        start_time = time.time()
        subagent_dir = self.config.workspace_dir / "artifacts" / "subagents" / subagent_id
        events_path = subagent_dir / "events.jsonl"
        sources_path = subagent_dir / "sources.jsonl"
        final_path = subagent_dir / "final.md"
        result_path = subagent_dir / "result.json"
        metadata_path = subagent_dir / "metadata.json"

        _atomic_write_json(metadata_path, {
            "investigation_id": self.config.investigation_id,
            "subagent_id": subagent_id,
            "focus_area": focus_area,
            "model": self.config.subagent_model.value if isinstance(self.config.subagent_model, ModelType) else str(self.config.subagent_model),
            "timeout_seconds": timeout,
            "started_at": datetime.now().isoformat(),
            "artifact_schema": "runner-owned-subagent-v1",
            "files": {
                "events": str(events_path),
                "sources": str(sources_path),
                "final": str(final_path),
                "result": str(result_path),
            },
        })

        response, _ = invoke_claude(
            prompt=prompt,
            model=self.config.subagent_model,
            timeout=timeout,
            cwd=self.config.workspace_dir,
            artifact_event_file=events_path,
            artifact_sources_file=sources_path,
            artifact_label=subagent_id,
        )

        elapsed = time.time() - start_time

        if response and response.startswith("ERROR:"):
            result = SubagentResult(
                subagent_id=subagent_id,
                focus_area=focus_area,
                findings="",
                elapsed_seconds=elapsed,
                status="failed",
                error=response
            )
            _atomic_write_text(final_path, "")
            _atomic_write_json(result_path, result.to_dict())
            return result

        result = SubagentResult(
            subagent_id=subagent_id,
            focus_area=focus_area,
            findings=response,
            elapsed_seconds=elapsed,
            status="completed"
        )
        _atomic_write_text(final_path, response or "")
        _atomic_write_json(result_path, result.to_dict())
        return result

    def _load_pinned_webproxy_evidence(self) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Load investigation-owned WebProxy JSON payloads captured from subagent
        tool results.

        Returns:
            (sources_by_url, evidence_records). `sources_by_url` maps source URL
            to FetchedSource objects for the validator. `evidence_records` is a
            compact audit index for findings.json and synthesis prompts.
        """
        try:
            from investigation_validator.models import FetchedSource
        except Exception as exc:
            logger.warning("Unable to import FetchedSource for pinned evidence: %s", exc)
            return {}, []

        artifacts_dir = self.config.workspace_dir / "artifacts" / "subagents"
        if not artifacts_dir.exists():
            return {}, []

        sources_by_url: Dict[str, Any] = {}
        records: List[Dict[str, Any]] = []
        seen_evidence_paths: set[str] = set()

        for sources_path in sorted(artifacts_dir.glob("*/sources.jsonl")):
            subagent_id = sources_path.parent.name
            try:
                lines = sources_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("artifact_type") != "web_proxy_cache_json":
                    continue

                evidence_json_path = record.get("evidence_json_path")
                if not isinstance(evidence_json_path, str) or not evidence_json_path:
                    continue
                evidence_path = Path(evidence_json_path).resolve()
                if str(evidence_path) in seen_evidence_paths:
                    continue
                if not _is_relative_to(evidence_path, self.config.workspace_dir.resolve()):
                    logger.warning("Ignoring pinned evidence outside investigation workspace: %s", evidence_path)
                    continue
                payload = _safe_read_json(evidence_path)
                if not isinstance(payload, dict):
                    continue

                seen_evidence_paths.add(str(evidence_path))
                url = str(payload.get("url") or payload.get("pdf_url") or record.get("source_url") or "").strip()
                if not url:
                    continue
                text = _text_from_webproxy_payload(payload)
                original_length = int(
                    payload.get("original_text_length")
                    or payload.get("text_length")
                    or payload.get("original_length")
                    or len(text)
                )
                content_type = str(
                    payload.get("content_type")
                    or ("application/pdf" if payload.get("type") == "paper" else "text/html")
                )
                fetched = FetchedSource(
                    url=url,
                    content=text,
                    accessible=bool(text),
                    error=None if text else "Pinned WebProxy JSON did not contain extracted text",
                    content_type=content_type,
                    truncated=bool(payload.get("truncated", False)),
                    original_length=original_length,
                )
                sources_by_url[url] = fetched
                normalized = _normalize_source_url(url)
                if normalized and normalized not in sources_by_url:
                    sources_by_url[normalized] = fetched

                pdf_url = payload.get("pdf_url")
                if isinstance(pdf_url, str) and pdf_url and pdf_url != url:
                    sources_by_url[pdf_url] = fetched
                    normalized_pdf = _normalize_source_url(pdf_url)
                    if normalized_pdf and normalized_pdf not in sources_by_url:
                        sources_by_url[normalized_pdf] = fetched

                title = payload.get("title") or payload.get("paper_title") or ""
                records.append({
                    "subagent_id": subagent_id,
                    "url": url,
                    "pdf_url": pdf_url if isinstance(pdf_url, str) else None,
                    "title": str(title)[:200] if title else "",
                    "type": payload.get("type") or "fetch",
                    "content_type": content_type,
                    "text_length": len(text),
                    "original_text_length": original_length,
                    "truncated": bool(payload.get("truncated", False)),
                    "sha256": record.get("sha256") or payload.get("sha256"),
                    "byte_length": record.get("byte_length"),
                    "cache_json_path": record.get("cache_json_path"),
                    "evidence_json_path": str(evidence_path),
                    "local_pdf_path": payload.get("local_pdf_path"),
                    "local_text_path": payload.get("local_text_path"),
                })

        return sources_by_url, records

    def _build_pinned_evidence_index(self, max_records: int = 80) -> str:
        """Return a compact text index of pinned source payloads for synthesis."""
        records = getattr(self, "pinned_evidence_records", []) or []
        if not records:
            return ""

        lines = [
            "The runner captured the following full WebProxy source JSON payloads for this investigation.",
            "Use these as the audit trail for URLs, papers, and source text used by the subagents.",
            "",
        ]
        for idx, record in enumerate(records[:max_records], start=1):
            url = record.get("url") or ""
            title = record.get("title") or ""
            evidence_path = record.get("evidence_json_path") or ""
            source_type = record.get("type") or "fetch"
            text_length = record.get("text_length") or 0
            original_length = record.get("original_text_length") or text_length
            truncated = " truncated" if record.get("truncated") else ""
            label = f"{idx}. [{source_type}] {url}"
            if title:
                label += f" — {title}"
            lines.append(label)
            lines.append(
                f"   evidence_json: {evidence_path}; text_length={text_length}; "
                f"original_text_length={original_length}{truncated}"
            )
            if record.get("local_pdf_path"):
                lines.append(f"   local_pdf: {record.get('local_pdf_path')}")
            if record.get("local_text_path"):
                lines.append(f"   local_text: {record.get('local_text_path')}")
        if len(records) > max_records:
            lines.append(f"... {len(records) - max_records} additional pinned source payloads omitted from prompt index.")
        return "\n".join(lines)

    def _validate_findings(self):
        """
        Run adversarial validation on subagent findings.

        Spawns blind validator agents to fact-check cited sources.
        Returns ValidatedFindings object with filtered/annotated claims.
        """
        try:
            # Import the validator (deferred to avoid circular imports)
            from investigation_validator.orchestrator import ValidationOrchestrator
            from investigation_validator.models import ValidationConfig, FilterMode

            # Create validation config from investigation config
            filter_mode_map = {
                "strict": FilterMode.STRICT,
                "annotated": FilterMode.ANNOTATED,
                "balanced": FilterMode.BALANCED,
            }
            filter_mode = filter_mode_map.get(
                self.config.validation_filter_mode,
                FilterMode.BALANCED
            )

            val_config = ValidationConfig(
                enabled=True,
                model="haiku",  # Use fast model for validators
                filter_mode=filter_mode,
                parallel_validators=10,
            )

            # Run validation pipeline
            orchestrator = ValidationOrchestrator(val_config)
            validated = orchestrator.validate(
                self.subagent_results,
                evidence_sources=getattr(self, "pinned_evidence_sources", {}) or {},
                progress_callback=self.progress_callback
            )

            # Log stats
            stats = orchestrator.get_stats()
            self._log(f"Validation stats: {stats.supported}/{stats.total_claims} claims supported")

            return validated

        except Exception as e:
            logger.warning(f"Validation failed (non-fatal): {e}")
            self._log(f"Validation error: {e} - proceeding without validation")
            return None

    def _synthesize_findings(self, validated_findings=None) -> str:
        """Synthesize all subagent findings into a report with format validation."""
        MAX_RETRIES = 2
        previous_issues = ""

        # Generate executive summaries from URL metadata if available
        executive_summaries = ""
        if self.url_metadata:
            # Get raw findings text for context
            raw_findings = "\n\n".join([
                r.findings for r in self.subagent_results
                if r.status == "completed" and r.findings
            ])
            executive_summaries = format_url_executive_summaries(self.url_metadata, raw_findings)

        for attempt in range(MAX_RETRIES + 1):
            evidence_index = self._build_pinned_evidence_index()
            # Use validated findings text if available, otherwise use raw findings
            if validated_findings and validated_findings.filtered_findings_text:
                prompt = build_synthesis_prompt_validated(
                    self.config.query,
                    validated_findings.filtered_findings_text,
                    validated_findings.to_dict(),
                    self.config.deliverable_format,
                    self.config.source,
                    self.ground_rules,
                    evidence_index
                )
            else:
                prompt = build_synthesis_prompt(
                    self.config.query,
                    self.subagent_results,
                    self.config.deliverable_format,
                    self.config.source,  # Pass source to control mission-style language
                    self.ground_rules,
                    evidence_index
                )

            # Inject executive summaries from URL handlers
            if executive_summaries:
                prompt = f"""{prompt}

## Pre-Generated Executive Summaries (from URL metadata)

Include the following handler-generated summaries at the TOP of your report,
BEFORE your own synthesis. These provide structured metadata about the URLs
analyzed in this investigation:

{executive_summaries}

Incorporate these summaries, then add your deeper analysis below them."""

            # Add retry context if not first attempt
            if attempt > 0:
                prompt = f"""CRITICAL: Your previous response was REJECTED because it was not valid HTML.

You MUST output raw HTML starting with <!DOCTYPE html>. No markdown. No code fences.

Previous attempt errors: {previous_issues}

{prompt}"""

            # Increase timeout for synthesis - needs more time for complex HTML output
            # Use 40% of budget instead of 20%, minimum 120 seconds
            timeout = max(60, int(self.config.timeout_minutes * 60 * 0.30))  # 30% of budget for synthesis

            response, elapsed = invoke_claude(
                prompt=prompt,
                model=self.config.synthesis_model,
                timeout=timeout,
                cwd=self.config.workspace_dir
            )

            self._log(f"Synthesis attempt {attempt + 1} completed in {elapsed:.1f}s")

            if response and response.startswith("ERROR:"):
                # API error or timeout
                self._log(f"Synthesis error: {response}")
                if "html" in (self.config.deliverable_format or "").lower():
                    # For HTML format, try to convert raw findings to HTML
                    self._log("Creating HTML fallback from raw findings")
                    raw_report = self._create_fallback_report()
                    return markdown_to_html(raw_report, self.config.query)
                else:
                    # For other formats, use markdown fallback
                    return self._create_fallback_report()

            # Validate HTML format if HTML was requested
            if self.config.deliverable_format and "html" in self.config.deliverable_format.lower():
                is_valid, issues = validate_html_format(response)
                if is_valid:
                    self._log("HTML validation passed")
                    return response
                else:
                    self._log(f"HTML validation failed (attempt {attempt + 1}): {issues}")
                    previous_issues = "; ".join(issues)
                    if attempt == MAX_RETRIES:
                        # Final attempt failed - convert markdown to HTML
                        self._log("Converting markdown to HTML as fallback")
                        return markdown_to_html(response, self.config.query)
            else:
                # Non-HTML format - return as-is
                return response

        return response

    def _create_fallback_report(self) -> str:
        """Create basic report from raw findings when API errors occur."""
        report = f"# Investigation Report\n\n"
        report += f"## Query\n{self.config.query}\n\n"
        report += "## Raw Findings\n\n"
        for r in self.subagent_results:
            if r.status == "completed":
                report += f"### {r.focus_area}\n{r.findings}\n\n"
        return report

    def _ingest_to_knowledge_base(self):
        """
        Ingest investigation findings into the Knowledge Base.

        This extracts learnings from the investigation and stores them
        in the KB for cross-referencing with mission learnings.

        For email investigations: Ingest to KB but do NOT generate recommendations.
        Email investigations are standalone research, not mission proposals.
        """
        try:
            from mission_knowledge_base import get_knowledge_base

            kb = get_knowledge_base()
            result = kb.ingest_investigation(self.config.workspace_dir)

            if result.get("status") == "success":
                learnings_count = result.get("learnings_extracted", 0)
                self._log(f"Ingested {learnings_count} learnings into Knowledge Base")

                # Generate recommendations ONLY for non-email investigations
                # Email investigations are standalone research, not mission proposals
                if learnings_count > 0 and self.config.source != "email":
                    try:
                        from mission_recommendations import get_recommendation_engine
                        engine = get_recommendation_engine()
                        recommendations = engine.generate_from_investigation(
                            self.config.investigation_id
                        )
                        if recommendations:
                            self._log(f"Generated {len(recommendations)} mission recommendations")
                    except Exception as rec_error:
                        logger.warning(f"Recommendation generation failed (non-fatal): {rec_error}")
                elif self.config.source == "email":
                    self._log("Skipping recommendation generation for email investigation (findings retained in KB)")
            else:
                logger.warning(f"KB ingestion returned non-success: {result}")

        except ImportError:
            logger.warning("Knowledge base module not available for ingestion")
        except Exception as e:
            logger.error(f"Failed to ingest investigation to KB: {e}")
            raise

    def _extract_and_inject_code_to_afterimage(self):
        """
        Extract code blocks from investigation outputs and inject to AI-AfterImage.

        This method:
        1. Scans investigation artifacts for code blocks
        2. Extracts code with semantic context
        3. Injects to AI-AfterImage Code DB

        The semantic connection between research context and code is preserved.
        """
        try:
            # Import the code extraction pipeline
            import sys
            investigation_module_path = str(BASE_DIR / "workspace" / "Investigation")
            if investigation_module_path not in sys.path:
                sys.path.insert(0, investigation_module_path)

            from afterimage_injector import ResearchCodePipeline, InjectionConfig

            # Configure the pipeline
            config = InjectionConfig(
                min_confidence=0.4,
                session_id=self.config.investigation_id,
            )

            pipeline = ResearchCodePipeline(injector_config=config)

            # Process the investigation
            result = pipeline.process_investigation(
                investigation_dir=self.config.workspace_dir,
                investigation_id=self.config.investigation_id,
                query=self.config.query
            )

            if result.injected_count > 0:
                self._log(f"Injected {result.injected_count} code blocks to AI-AfterImage Code DB")
            elif result.total_blocks > 0:
                self._log(f"Found {result.total_blocks} code blocks, {result.skipped_count} skipped")
            # No log if no code blocks found (common case)

            pipeline.close()

        except ImportError as e:
            # AI-AfterImage or extraction module not available - this is non-fatal
            logger.debug(f"Code extraction skipped (module not available): {e}")
        except Exception as e:
            # Log but don't fail the investigation
            logger.warning(f"Code extraction/injection failed (non-fatal): {e}")


# =============================================================================
# PUBLIC API
# =============================================================================

def run_investigation(
    query: str,
    max_subagents: int = 5,
    timeout_minutes: int = 10,
    progress_callback: Optional[Callable[[str], None]] = None,
    deliverable_format: Optional[str] = None
) -> InvestigationResult:
    """
    Run a complete investigation.

    This is the main entry point for starting an investigation.
    The investigation engine can research ANY topic - not just software.

    Args:
        query: The investigation query/topic (can be ANY domain: gaming, science, etc.)
        max_subagents: Maximum number of parallel subagents (default 5)
        timeout_minutes: Total timeout in minutes (default 10)
        progress_callback: Optional callback for progress updates
        deliverable_format: Optional format for output ("HTML", "JSON", "markdown", etc.)

    Returns:
        InvestigationResult with all findings

    Examples:
        # Software investigation
        run_investigation("How does the authentication module work?")

        # Gaming investigation
        run_investigation(
            "Best Destiny 2 Solar Warlock grenade build",
            deliverable_format="HTML"
        )

        # Science investigation
        run_investigation("Explain quantum entanglement for beginners")

        # General research
        run_investigation(
            "Compare electric vs gas vehicles for 2024",
            deliverable_format="markdown"
        )
    """
    config = InvestigationConfig(
        query=query,
        max_subagents=max_subagents,
        timeout_minutes=timeout_minutes,
        deliverable_format=deliverable_format
    )

    runner = InvestigationRunner(config)
    return runner.run(progress_callback=progress_callback)


def get_investigation_status(investigation_id: Optional[str] = None) -> dict:
    """
    Get the status of an investigation.

    Args:
        investigation_id: Optional specific investigation ID. If None, returns current.

    Returns:
        Status dict or None if not found
    """
    state = load_investigation_state()

    if investigation_id is None:
        return state.get("current")

    # Check current
    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        return state["current"]

    # Check history
    for inv in state.get("history", []):
        if inv.get("investigation_id") == investigation_id:
            return inv

    return None


def stop_investigation(investigation_id: str) -> bool:
    """
    Request to stop an ongoing investigation.

    Note: This only updates state - the running processes may continue
    until they check the state or hit timeout.

    Returns:
        True if investigation was found and marked for stopping
    """
    state = load_investigation_state()

    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        state["current"]["status"] = InvestigationStatus.FAILED.value
        state["current"]["error"] = "Stopped by user"
        state["current"]["completed_at"] = datetime.now().isoformat()

        # Move to history
        state.setdefault("history", []).append(state["current"])
        state["current"] = None

        save_investigation_state(state)
        return True

    return False


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "What are the key components of this codebase and how do they interact?"

    print("=" * 60)
    print("Investigation Engine - Test Run")
    print("=" * 60)
    print(f"Query: {query}")
    print("-" * 60)

    def progress(msg):
        print(f"  >> {msg}")

    result = run_investigation(query, max_subagents=3, timeout_minutes=5, progress_callback=progress)

    print("-" * 60)
    print(f"Status: {result.status.value}")
    print(f"Elapsed: {result.elapsed_seconds:.1f}s")
    if result.report_path:
        print(f"Report: {result.report_path}")
    if result.error:
        print(f"Error: {result.error}")

    print("=" * 60)
