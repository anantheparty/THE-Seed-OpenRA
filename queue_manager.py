"""Singleton shared-queue manager for stalled ready buildings."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Literal, Optional, Protocol

from logging_system import get_logger

logger = logging.getLogger(__name__)
slog = get_logger("kernel")

QueueManagerMode = Literal["off", "warn", "auto_place"]


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class GameAPILike(Protocol):
    def place_building(
        self,
        queue_type: str,
        location: Any = None,
        owner_actor_id: Optional[int] = None,
    ) -> None: ...


class NotificationSink(Protocol):
    def __call__(
        self,
        notification_type: str,
        content: str,
        *,
        data: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None: ...


@dataclass(slots=True)
class QueueManagerConfig:
    mode: QueueManagerMode = "auto_place"
    ready_timeout_s: float = 5.0


@dataclass(slots=True)
class _ReadyState:
    first_seen_at: float
    last_notified_at: float = 0.0
    handled: bool = False


class QueueManager:
    """Monitors shared production queues for stuck ready buildings.

    This is intentionally a singleton runtime service, not a per-task Expert job.
    """

    def __init__(
        self,
        *,
        world_model: WorldModelLike,
        game_api: GameAPILike,
        notify: NotificationSink,
        config: Optional[QueueManagerConfig] = None,
    ) -> None:
        self.world_model = world_model
        self.game_api = game_api
        self.notify = notify
        self.config = config or QueueManagerConfig()
        self._ready_states: dict[tuple[str, str, int | None], _ReadyState] = {}

    def set_mode(self, mode: QueueManagerMode) -> None:
        self.config.mode = mode

    def tick(self, *, now: float) -> None:
        if self.config.mode == "off":
            self._ready_states.clear()
            return

        queues = self.world_model.query("production_queues")
        if not isinstance(queues, dict):
            self._ready_states.clear()
            return

        active_keys: set[tuple[str, str, int | None]] = set()
        for queue_type, queue in queues.items():
            if not isinstance(queue, dict) or not queue.get("has_ready_item"):
                continue
            items = list(queue.get("items", []))
            ready_item = next((item for item in items if bool(item.get("done"))), None)
            if not isinstance(ready_item, dict):
                continue
            key = (
                str(queue_type),
                str(ready_item.get("name") or ""),
                ready_item.get("owner_actor_id"),
            )
            active_keys.add(key)
            state = self._ready_states.get(key)
            if state is None:
                self._ready_states[key] = _ReadyState(first_seen_at=now)
                continue

            if state.handled:
                continue
            if now - state.first_seen_at < self.config.ready_timeout_s:
                continue

            display_name = str(ready_item.get("display_name") or ready_item.get("name") or queue_type)
            if self.config.mode == "warn":
                self._notify_once(
                    state,
                    now=now,
                    notification_type="queue_ready_stuck",
                    content=f"{display_name} 已就绪但超过 {self.config.ready_timeout_s:.0f}s 未放置",
                    data={
                        "queue_type": queue_type,
                        "item_name": ready_item.get("name"),
                        "display_name": display_name,
                        "owner_actor_id": ready_item.get("owner_actor_id"),
                        "mode": self.config.mode,
                    },
                )
                continue

            try:
                self.game_api.place_building(
                    str(queue_type),
                    owner_actor_id=ready_item.get("owner_actor_id"),
                )
            except Exception as exc:
                self._notify_once(
                    state,
                    now=now,
                    notification_type="queue_auto_place_failed",
                    content=f"{display_name} 已就绪但自动放置失败",
                    data={
                        "queue_type": queue_type,
                        "item_name": ready_item.get("name"),
                        "display_name": display_name,
                        "owner_actor_id": ready_item.get("owner_actor_id"),
                        "mode": self.config.mode,
                        "error": str(exc),
                    },
                )
                logger.warning("QueueManager auto-place failed for %s/%s: %s", queue_type, display_name, exc)
                slog.warn(
                    "Queue manager auto-place failed",
                    event="queue_auto_place_failed",
                    queue_type=queue_type,
                    item_name=ready_item.get("name"),
                    display_name=display_name,
                    owner_actor_id=ready_item.get("owner_actor_id"),
                    error=str(exc),
                )
                continue

            state.handled = True
            state.last_notified_at = now
            self.notify(
                "queue_auto_placed",
                f"已自动放置卡住的建筑：{display_name}",
                data={
                    "queue_type": queue_type,
                    "item_name": ready_item.get("name"),
                    "display_name": display_name,
                    "owner_actor_id": ready_item.get("owner_actor_id"),
                    "mode": self.config.mode,
                },
                timestamp=now,
            )
            slog.info(
                "Queue manager auto-placed ready building",
                event="queue_auto_placed",
                queue_type=queue_type,
                item_name=ready_item.get("name"),
                display_name=display_name,
                owner_actor_id=ready_item.get("owner_actor_id"),
            )

        stale_keys = [key for key in self._ready_states if key not in active_keys]
        for key in stale_keys:
            self._ready_states.pop(key, None)

    def _notify_once(
        self,
        state: _ReadyState,
        *,
        now: float,
        notification_type: str,
        content: str,
        data: dict[str, Any],
    ) -> None:
        if state.last_notified_at:
            return
        state.last_notified_at = now
        self.notify(notification_type, content, data=data, timestamp=now)
        slog.warn(
            "Queue manager reported ready-item stall",
            event=notification_type,
            **data,
        )
