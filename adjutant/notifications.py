"""Proactive notification system (design.md §1, §4, decision 15).

Consumes Kernel player_notifications, formats them, and pushes to
the frontend via WS server. The system does NOT execute actions —
it only notifies the player (decision 15).

Notification types (from WorldModel events → Kernel):
  - ENEMY_EXPANSION: "发现敌人在扩张"
  - FRONTLINE_WEAK: "我方前线空虚"
  - ECONOMY_SURPLUS: "经济充裕，可以考虑进攻"
  - world_model_stale: "WorldModel 刷新失败"
  - Custom notifications via Kernel.push_player_notification
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional, Protocol

logger = logging.getLogger(__name__)


class KernelNotificationSource(Protocol):
    """Reads notifications from Kernel."""

    def list_player_notifications(self) -> list[dict[str, Any]]: ...


# Async push callback — sends formatted notification to WS/frontend
NotificationSink = Callable[[dict[str, Any]], Awaitable[None]]


# --- Notification formatting ---

NOTIFICATION_ICONS = {
    "ENEMY_EXPANSION": "🔍",
    "FRONTLINE_WEAK": "⚠",
    "ECONOMY_SURPLUS": "💰",
    "BASE_UNDER_ATTACK": "🚨",
    "world_model_stale": "⚙",
}

NOTIFICATION_SEVERITY = {
    "ENEMY_EXPANSION": "info",
    "FRONTLINE_WEAK": "warning",
    "ECONOMY_SURPLUS": "info",
    "BASE_UNDER_ATTACK": "critical",
    "world_model_stale": "warning",
}


@dataclass
class FormattedNotification:
    """A notification ready for frontend display."""

    type: str
    severity: str  # info / warning / critical
    content: str
    icon: str
    data: dict[str, Any]
    timestamp: float


def format_notification(raw: dict[str, Any]) -> FormattedNotification:
    """Format a raw Kernel notification for frontend display."""
    ntype = raw.get("type", "unknown")
    content = raw.get("content", ntype)
    return FormattedNotification(
        type=ntype,
        severity=NOTIFICATION_SEVERITY.get(ntype, "info"),
        content=content,
        icon=NOTIFICATION_ICONS.get(ntype, "ℹ"),
        data=raw.get("data", {}),
        timestamp=raw.get("timestamp", time.time()),
    )


def notification_to_text(notification: FormattedNotification) -> str:
    """Render notification as text for chat mode."""
    return f"{notification.icon} {notification.content}"


def notification_to_dict(notification: FormattedNotification) -> dict[str, Any]:
    """Render notification as dict for WS/frontend push."""
    return {
        "type": notification.type,
        "severity": notification.severity,
        "content": notification.content,
        "icon": notification.icon,
        "data": notification.data,
        "timestamp": notification.timestamp,
    }


# --- NotificationManager ---

class NotificationManager:
    """Polls Kernel notifications and pushes formatted versions to frontend.

    Designed to be called each GameLoop tick or on a timer. Tracks which
    notifications have already been pushed to avoid duplicates.
    """

    def __init__(
        self,
        kernel: KernelNotificationSource,
        sink: Optional[NotificationSink] = None,
        *,
        max_history: int = 100,
    ) -> None:
        self.kernel = kernel
        self._sink = sink
        self._pushed_indices: set[int] = set()  # Track by index in Kernel's append-only list
        self._max_history = max(1, int(max_history))
        self.history: list[FormattedNotification] = []

    def _append_history(self, notification: FormattedNotification) -> None:
        """Store a delivered notification in bounded local history."""
        self.history.append(notification)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history :]

    async def poll_and_push(self) -> list[FormattedNotification]:
        """Check for new notifications since last poll, format and push them.

        Uses set-based index tracking so a failed push at index N doesn't
        cause index N+1 to be skipped — each notification is independently
        tracked.

        Returns:
            List of newly formatted notifications (including retry of failed ones).
        """
        all_notifications = self.kernel.list_player_notifications()

        new_formatted = []
        for idx, raw in enumerate(all_notifications):
            if idx in self._pushed_indices:
                continue

            formatted = format_notification(raw)
            new_formatted.append(formatted)

            if self._sink:
                try:
                    await self._sink(notification_to_dict(formatted))
                    self._pushed_indices.add(idx)
                    self._append_history(formatted)
                except Exception:
                    logger.exception("Failed to push notification: %s", formatted.type)
                    # Don't add to _pushed_indices — will retry next poll
            else:
                self._pushed_indices.add(idx)
                self._append_history(formatted)

        return new_formatted

    @property
    def total_pushed(self) -> int:
        return len(self._pushed_indices)
