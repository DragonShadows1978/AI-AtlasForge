#!/usr/bin/env python3
"""
Queue Notification System - Email and Webhook notifications for Mission Queue

Provides notifications for:
1. Queue empty (all missions completed)
2. Mission failed during queue execution
3. Mission completed successfully
4. Queue processing disabled/enabled
5. High-priority mission added

Notification channels:
- Email (via email_inbox.py SMTP)
- Webhooks (HTTP POST to configured endpoints)
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request
import urllib.error

# Configure logging
logger = logging.getLogger("queue_notifications")

# Paths - use centralized configuration
from atlasforge_config import STATE_DIR
NOTIFICATION_STATE_PATH = STATE_DIR / "queue_notification_state.json"
NOTIFICATION_CONFIG_PATH = STATE_DIR / "queue_notification_config.json"


class NotificationType(Enum):
    """Types of queue notifications."""
    QUEUE_EMPTY = "queue_empty"
    MISSION_FAILED = "mission_failed"
    MISSION_COMPLETED = "mission_completed"
    QUEUE_DISABLED = "queue_disabled"
    QUEUE_ENABLED = "queue_enabled"
    HIGH_PRIORITY_ADDED = "high_priority_added"
    DEPENDENCY_BLOCKED = "dependency_blocked"


@dataclass
class NotificationConfig:
    """Configuration for queue notifications."""
    # Email settings
    email_enabled: bool = False
    email_recipients: List[str] = field(default_factory=list)
    email_on_events: List[str] = field(default_factory=lambda: [
        "queue_empty", "mission_failed"
    ])

    # Webhook settings
    webhook_enabled: bool = False
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    webhook_on_events: List[str] = field(default_factory=lambda: [
        "queue_empty", "mission_failed", "mission_completed"
    ])

    # General settings
    throttle_minutes: int = 5  # Don't send same notification within this time
    include_mission_details: bool = True

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "NotificationConfig":
        """Create config from dictionary."""
        return cls(
            email_enabled=data.get("email_enabled", False),
            email_recipients=data.get("email_recipients", []),
            email_on_events=data.get("email_on_events", ["queue_empty", "mission_failed"]),
            webhook_enabled=data.get("webhook_enabled", False),
            webhook_url=data.get("webhook_url"),
            webhook_secret=data.get("webhook_secret"),
            webhook_on_events=data.get("webhook_on_events", ["queue_empty", "mission_failed", "mission_completed"]),
            throttle_minutes=data.get("throttle_minutes", 5),
            include_mission_details=data.get("include_mission_details", True)
        )


@dataclass
class NotificationRecord:
    """Record of a sent notification."""
    id: str
    notification_type: str
    timestamp: str
    channel: str  # "email" or "webhook"
    success: bool
    error: Optional[str] = None
    mission_id: Optional[str] = None
    details: Optional[Dict] = None


class QueueNotifier:
    """
    Notification system for mission queue events.

    Sends email and webhook notifications based on configuration.
    """

    def __init__(self, io_utils_module=None):
        """Initialize notifier."""
        self.io_utils = io_utils_module
        if not self.io_utils:
            try:
                import io_utils as _io
                self.io_utils = _io
            except ImportError:
                self.io_utils = None

        self._throttle_cache: Dict[str, datetime] = {}
        self._lock = threading.Lock()

    def _load_config(self) -> NotificationConfig:
        """Load notification configuration."""
        try:
            if NOTIFICATION_CONFIG_PATH.exists():
                with open(NOTIFICATION_CONFIG_PATH, 'r') as f:
                    data = json.load(f)
                return NotificationConfig.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load notification config: {e}")
        return NotificationConfig()

    def _save_config(self, config: NotificationConfig):
        """Save notification configuration."""
        NOTIFICATION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFICATION_CONFIG_PATH, 'w') as f:
            json.dump(config.to_dict(), f, indent=2)

    def get_config(self) -> NotificationConfig:
        """Get current notification configuration."""
        return self._load_config()

    def update_config(self, updates: Dict) -> NotificationConfig:
        """Update notification configuration."""
        config = self._load_config()

        for key, value in updates.items():
            if hasattr(config, key):
                setattr(config, key, value)

        self._save_config(config)
        return config

    def _should_throttle(self, event_type: str, mission_id: Optional[str] = None) -> bool:
        """Check if notification should be throttled."""
        config = self._load_config()
        throttle_key = f"{event_type}:{mission_id or 'none'}"

        with self._lock:
            last_sent = self._throttle_cache.get(throttle_key)
            if last_sent:
                elapsed = (datetime.now() - last_sent).total_seconds() / 60
                if elapsed < config.throttle_minutes:
                    logger.debug(f"Throttling notification {throttle_key}")
                    return True

            self._throttle_cache[throttle_key] = datetime.now()
            return False

    def _log_notification(self, record: NotificationRecord):
        """Log notification to state file."""
        try:
            state_path = NOTIFICATION_STATE_PATH
            if state_path.exists():
                with open(state_path, 'r') as f:
                    state = json.load(f)
            else:
                state = {"notifications": [], "stats": {}}

            # Add record
            state["notifications"].append(asdict(record))

            # Keep only last 100 records
            state["notifications"] = state["notifications"][-100:]

            # Update stats
            stats = state.get("stats", {})
            stats[record.notification_type] = stats.get(record.notification_type, 0) + 1
            if not record.success:
                stats["failures"] = stats.get("failures", 0) + 1
            state["stats"] = stats

            # Save
            with open(state_path, 'w') as f:
                json.dump(state, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to log notification: {e}")

    def notify_queue_empty(self, last_mission_id: Optional[str] = None):
        """Send notification that queue is now empty."""
        self._send_notification(
            NotificationType.QUEUE_EMPTY,
            title="Mission Queue Empty",
            message="All queued missions have completed. The queue is now empty.",
            mission_id=last_mission_id,
            details={"remaining_items": 0}
        )

    def notify_mission_failed(
        self,
        mission_id: str,
        mission_title: str,
        failure_reason: Optional[str] = None,
        stage: Optional[str] = None
    ):
        """Send notification that a mission failed."""
        self._send_notification(
            NotificationType.MISSION_FAILED,
            title=f"Mission Failed: {mission_title}",
            message=f"Mission '{mission_title}' failed during {stage or 'execution'}.\n"
                    f"Reason: {failure_reason or 'Unknown'}",
            mission_id=mission_id,
            details={
                "mission_title": mission_title,
                "failure_reason": failure_reason,
                "stage": stage
            }
        )

    def notify_mission_completed(
        self,
        mission_id: str,
        mission_title: str,
        cycles_used: int = 1,
        remaining_in_queue: int = 0
    ):
        """Send notification that a mission completed successfully."""
        self._send_notification(
            NotificationType.MISSION_COMPLETED,
            title=f"Mission Completed: {mission_title}",
            message=f"Mission '{mission_title}' completed successfully.\n"
                    f"Cycles used: {cycles_used}\n"
                    f"Remaining in queue: {remaining_in_queue}",
            mission_id=mission_id,
            details={
                "mission_title": mission_title,
                "cycles_used": cycles_used,
                "remaining_in_queue": remaining_in_queue
            }
        )

    def notify_high_priority_added(
        self,
        mission_id: str,
        mission_title: str,
        position: int
    ):
        """Send notification that a high-priority mission was added."""
        self._send_notification(
            NotificationType.HIGH_PRIORITY_ADDED,
            title=f"High Priority Mission Added: {mission_title}",
            message=f"High-priority mission '{mission_title}' added to queue at position {position}.",
            mission_id=mission_id,
            details={
                "mission_title": mission_title,
                "position": position,
                "priority": "high"
            }
        )

    def notify_dependency_blocked(
        self,
        mission_id: str,
        mission_title: str,
        dependency_id: str
    ):
        """Send notification that a mission is blocked by failed dependency."""
        self._send_notification(
            NotificationType.DEPENDENCY_BLOCKED,
            title=f"Mission Blocked: {mission_title}",
            message=f"Mission '{mission_title}' is blocked because its dependency "
                    f"mission '{dependency_id}' failed.",
            mission_id=mission_id,
            details={
                "mission_title": mission_title,
                "dependency_id": dependency_id
            }
        )

    def _send_notification(
        self,
        notification_type: NotificationType,
        title: str,
        message: str,
        mission_id: Optional[str] = None,
        details: Optional[Dict] = None
    ):
        """Send notification via configured channels."""
        config = self._load_config()
        event_name = notification_type.value

        # Check throttling
        if self._should_throttle(event_name, mission_id):
            return

        # Send via email if configured
        if config.email_enabled and event_name in config.email_on_events:
            self._send_email(title, message, mission_id, details, config)

        # Send via webhook if configured
        if config.webhook_enabled and event_name in config.webhook_on_events:
            self._send_webhook(notification_type, title, message, mission_id, details, config)

    def _send_email(
        self,
        title: str,
        message: str,
        mission_id: Optional[str],
        details: Optional[Dict],
        config: NotificationConfig
    ):
        """Send email notification."""
        if not config.email_recipients:
            logger.warning("No email recipients configured")
            return

        try:
            # Import email config from email_inbox module
            try:
                from email_inbox import get_email_credentials, get_server_config
                email_addr, password = get_email_credentials()
                _, _, smtp_host, smtp_port = get_server_config()
            except ImportError:
                logger.error("email_inbox module not available for SMTP")
                return

            # Create email
            msg = MIMEMultipart()
            msg['From'] = email_addr
            msg['To'] = ", ".join(config.email_recipients)
            msg['Subject'] = f"[RDE Queue] {title}"

            # Build body
            body = f"{message}\n\n"
            body += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            if mission_id:
                body += f"Mission ID: {mission_id}\n"
            if details and config.include_mission_details:
                body += "\nDetails:\n"
                for key, value in details.items():
                    body += f"  {key}: {value}\n"

            msg.attach(MIMEText(body, 'plain'))

            # Send email
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(email_addr, password)
                server.send_message(msg)

            logger.info(f"Email notification sent: {title}")
            self._log_notification(NotificationRecord(
                id=f"email_{int(time.time())}",
                notification_type=title,
                timestamp=datetime.now().isoformat(),
                channel="email",
                success=True,
                mission_id=mission_id,
                details=details
            ))

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            self._log_notification(NotificationRecord(
                id=f"email_{int(time.time())}",
                notification_type=title,
                timestamp=datetime.now().isoformat(),
                channel="email",
                success=False,
                error=str(e),
                mission_id=mission_id
            ))

    def _send_webhook(
        self,
        notification_type: NotificationType,
        title: str,
        message: str,
        mission_id: Optional[str],
        details: Optional[Dict],
        config: NotificationConfig
    ):
        """Send webhook notification."""
        if not config.webhook_url:
            logger.warning("No webhook URL configured")
            return

        try:
            # Build payload
            payload = {
                "event": notification_type.value,
                "title": title,
                "message": message,
                "timestamp": datetime.now().isoformat(),
                "mission_id": mission_id,
            }
            if details and config.include_mission_details:
                payload["details"] = details

            # Add signature if secret is configured
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "RDE-Queue-Notifier/1.0"
            }
            if config.webhook_secret:
                import hmac
                import hashlib
                signature = hmac.new(
                    config.webhook_secret.encode(),
                    json.dumps(payload).encode(),
                    hashlib.sha256
                ).hexdigest()
                headers["X-RDE-Signature"] = f"sha256={signature}"

            # Send request
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                config.webhook_url,
                data=data,
                headers=headers,
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                if 200 <= status < 300:
                    logger.info(f"Webhook notification sent: {title}")
                    self._log_notification(NotificationRecord(
                        id=f"webhook_{int(time.time())}",
                        notification_type=notification_type.value,
                        timestamp=datetime.now().isoformat(),
                        channel="webhook",
                        success=True,
                        mission_id=mission_id,
                        details=details
                    ))
                else:
                    raise Exception(f"Webhook returned status {status}")

        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
            self._log_notification(NotificationRecord(
                id=f"webhook_{int(time.time())}",
                notification_type=notification_type.value,
                timestamp=datetime.now().isoformat(),
                channel="webhook",
                success=False,
                error=str(e),
                mission_id=mission_id
            ))

    def get_notification_history(self, limit: int = 50) -> List[Dict]:
        """Get recent notification history."""
        try:
            if NOTIFICATION_STATE_PATH.exists():
                with open(NOTIFICATION_STATE_PATH, 'r') as f:
                    state = json.load(f)
                return state.get("notifications", [])[-limit:]
        except Exception:
            pass
        return []

    def get_notification_stats(self) -> Dict:
        """Get notification statistics."""
        try:
            if NOTIFICATION_STATE_PATH.exists():
                with open(NOTIFICATION_STATE_PATH, 'r') as f:
                    state = json.load(f)
                return state.get("stats", {})
        except Exception:
            pass
        return {}


# Singleton instance
_notifier_instance: Optional[QueueNotifier] = None


def get_notifier() -> QueueNotifier:
    """Get singleton notifier instance."""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = QueueNotifier()
    return _notifier_instance


# Convenience functions
def notify_queue_empty(last_mission_id: Optional[str] = None):
    """Notify that queue is empty."""
    get_notifier().notify_queue_empty(last_mission_id)


def notify_mission_failed(
    mission_id: str,
    mission_title: str,
    failure_reason: Optional[str] = None,
    stage: Optional[str] = None
):
    """Notify that a mission failed."""
    get_notifier().notify_mission_failed(mission_id, mission_title, failure_reason, stage)


def notify_mission_completed(
    mission_id: str,
    mission_title: str,
    cycles_used: int = 1,
    remaining_in_queue: int = 0
):
    """Notify that a mission completed."""
    get_notifier().notify_mission_completed(
        mission_id, mission_title, cycles_used, remaining_in_queue
    )


def get_notification_config() -> Dict:
    """Get current notification configuration."""
    return get_notifier().get_config().to_dict()


def update_notification_config(updates: Dict) -> Dict:
    """Update notification configuration."""
    return get_notifier().update_config(updates).to_dict()
