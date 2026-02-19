"""
af_engine.integrations.token_sanity_check - Post-Mission Token Sanity Check

This integration runs after TranscriptArchivalIntegration (BACKGROUND priority)
to verify that a completed mission's archived transcript has non-zero token counts.
If the token count falls below the configurable threshold, it logs a WARNING and
emits a WebSocket event so the anomaly is immediately visible in the dashboard.

This catches the class of regression that occurred on 2/15/2026, where
orchestrator.py emitted MISSION_COMPLETED without workspace fields, causing
transcript archival to search the wrong directory and record zero tokens.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)

# Minimum expected tokens for any non-trivial mission.
MIN_TOKEN_THRESHOLD = 500


class TokenSanityCheckIntegration(BaseIntegrationHandler):
    """
    Post-mission sanity check that verifies archived token counts.

    Runs at BACKGROUND priority (after TranscriptArchivalIntegration at LOW priority)
    to ensure the transcript has been written before we check it.

    On a zero or anomalously low token count:
    - Logs a WARNING with mission_id, token count, and threshold
    - Emits a WebSocket 'token_anomaly' event for the dashboard widget
    - Does NOT block mission completion or other integrations

    Configuration:
        MIN_TOKEN_THRESHOLD (module-level): minimum acceptable token count (default 500)
        _archive_dir_override: optional Path for testing (defaults to TRANSCRIPTS_ARCHIVE_DIR)
    """

    name = "token_sanity_check"
    priority = IntegrationPriority.BACKGROUND
    subscriptions = [StageEvent.MISSION_COMPLETED]

    _ARCHIVAL_WAIT_SECONDS = 5
    _MAX_RETRIES = 3
    _RETRY_INTERVAL_SECONDS = 2

    def __init__(self, archive_dir_override: Optional[Path] = None):
        self._archive_dir_override = archive_dir_override
        self._websocket_emit = None
        super().__init__()

    def _get_archive_dir(self) -> Path:
        if self._archive_dir_override:
            return self._archive_dir_override
        try:
            import sys
            root_dir = Path(__file__).resolve().parent.parent.parent
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))
            from workspace.glassbox.mission_archiver import TRANSCRIPTS_ARCHIVE_DIR
            return TRANSCRIPTS_ARCHIVE_DIR
        except ImportError:
            root_dir = Path(__file__).resolve().parent.parent.parent
            return root_dir / "workspace" / "artifacts" / "transcripts"

    def _get_websocket_emit(self):
        if self._websocket_emit is not None:
            return self._websocket_emit
        try:
            import sys
            root_dir = Path(__file__).resolve().parent.parent.parent
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))
            from websocket_events import emit_glassbox_event
            self._websocket_emit = emit_glassbox_event
            return self._websocket_emit
        except ImportError:
            return None

    def _read_manifest(self, mission_id: str) -> Optional[dict]:
        archive_dir = self._get_archive_dir()
        manifest_path = archive_dir / mission_id / "manifest.json"

        for attempt in range(self._MAX_RETRIES):
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r") as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(
                        "[TokenSanityCheck] Failed to parse manifest for %s (attempt %d): %s",
                        mission_id, attempt + 1, e,
                    )
                    return None

            if attempt < self._MAX_RETRIES - 1:
                logger.debug(
                    "[TokenSanityCheck] Manifest not found for %s, waiting %ds (attempt %d/%d)",
                    mission_id, self._RETRY_INTERVAL_SECONDS, attempt + 1, self._MAX_RETRIES,
                )
                time.sleep(self._RETRY_INTERVAL_SECONDS)

        logger.warning(
            "[TokenSanityCheck] Manifest not found for mission %s after %d retries at %s",
            mission_id, self._MAX_RETRIES, manifest_path,
        )
        return None

    def on_mission_completed(self, event: Event) -> None:
        mission_id = event.mission_id
        logger.info("[TokenSanityCheck] Starting token count verification for %s", mission_id)

        logger.debug(
            "[TokenSanityCheck] Waiting %ds for archival to complete...",
            self._ARCHIVAL_WAIT_SECONDS,
        )
        time.sleep(self._ARCHIVAL_WAIT_SECONDS)

        manifest = self._read_manifest(mission_id)
        if manifest is None:
            logger.warning(
                "[TokenSanityCheck] Cannot verify tokens for %s: manifest not found. "
                "Transcript archival may have failed entirely.",
                mission_id,
            )
            self._emit_anomaly_event(mission_id, token_count=0, reason="manifest_missing")
            return

        total_tokens = manifest.get("total_tokens", 0)

        if total_tokens < MIN_TOKEN_THRESHOLD:
            logger.warning(
                "[TokenSanityCheck] ANOMALY DETECTED: Mission %s has only %d tokens "
                "(threshold: %d). Transcript archival may have recorded wrong directory. "
                "Check orchestrator MISSION_COMPLETED event fields.",
                mission_id, total_tokens, MIN_TOKEN_THRESHOLD,
            )
            reason = "zero_tokens" if total_tokens == 0 else "low_tokens"
            self._emit_anomaly_event(mission_id, token_count=total_tokens, reason=reason)
        else:
            logger.info(
                "[TokenSanityCheck] OK: mission %s has %d tokens (threshold: %d)",
                mission_id, total_tokens, MIN_TOKEN_THRESHOLD,
            )

    def _emit_anomaly_event(self, mission_id: str, token_count: int, reason: str) -> None:
        emit_fn = self._get_websocket_emit()
        if emit_fn is None:
            return
        try:
            emit_fn("token_anomaly", mission_id, {
                "token_count": token_count,
                "threshold": MIN_TOKEN_THRESHOLD,
                "reason": reason,
            })
            logger.debug(
                "[TokenSanityCheck] Emitted token_anomaly WebSocket event for %s", mission_id
            )
        except Exception as e:
            logger.debug("[TokenSanityCheck] WebSocket emit failed (non-critical): %s", e)
