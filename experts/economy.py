"""EconomyExpert and EconomyJob implementation."""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from benchmark import span as bm_span
from models import EventType, JobStatus, EconomyJobConfig, ResourceKind, ResourceNeed, SignalKind
from openra_api.game_api import GameAPIError
from openra_api.production_names import production_name_matches

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .knowledge import (
    buildable_economy_recovery_options,
    buildable_power_recovery_options,
    knowledge_for_target,
    low_power_impacts,
)


logger = logging.getLogger(__name__)


class GameAPILike(Protocol):
    def can_produce(self, unit_type: str) -> bool:
        ...

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = False) -> Optional[int]:
        ...

    def place_building(self, queue_type: str, location: Any = None) -> None:
        ...

    def manage_production(
        self,
        queue_type: str,
        action: str,
        *,
        owner_actor_id: Optional[int] = None,
        item_name: Optional[str] = None,
        count: int = 1,
    ) -> None:
        ...


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any:
        ...


class EconomyJob(BaseJob):
    """Production queue controller for deterministic macro tasks."""

    tick_interval = 5.0

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: EconomyJobConfig,
        signal_callback: SignalCallback,
        game_api: GameAPILike,
        world_model: WorldModelLike,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> None:
        super().__init__(
            job_id=job_id,
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )
        self.game_api = game_api
        self.world_model = world_model
        self.phase = "producing"
        self.produced_count = 0
        self.issued_count = 0
        self._last_seen_event_ts = 0.0
        self._waiting_reason: Optional[str] = None
        self._counted_ready_items_pending_placement = 0
        self._known_matching_actor_ids = self._matching_self_actor_ids()
        self._knowledge = knowledge_for_target(self.config.unit_type, self.config.queue_type)

    @property
    def expert_type(self) -> str:
        return "EconomyExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        return [
            ResourceNeed(
                job_id=self.job_id,
                kind=ResourceKind.PRODUCTION_QUEUE,
                count=1,
                predicates={"queue_type": self.config.queue_type},
            )
        ]

    def do_tick(self) -> None:
        """Economy jobs continue light recovery checks while waiting."""
        if self._paused or self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
            return
        with bm_span("job_tick", name=f"{self.expert_type}:{self.job_id}"):
            self.tick()

    def abort(self) -> None:
        self._cleanup_queue_on_abort()
        super().abort()

    def tick(self) -> None:
        queue = self._queue_state()
        economy = self.world_model.query("economy")

        self._apply_completion_events()
        self._sync_direct_actor_completions()
        queue = self._queue_state()
        if self.status == JobStatus.SUCCEEDED:
            return

        placement_state = self._maybe_place_ready_building(queue)
        if placement_state == "placed":
            self.phase = "placing"
            self.status = JobStatus.RUNNING
            queue = self._queue_state()
            if self.produced_count >= self.config.count:
                self._finish_succeeded()
                return
        if placement_state == "blocked":
            return

        if self.produced_count >= self.config.count:
            self._finish_succeeded()
            return

        reason = self._waiting_reason_for(queue, economy)
        if reason is not None:
            self._enter_waiting(reason)
            return

        if self.status == JobStatus.WAITING:
            self.status = JobStatus.RUNNING
            self.phase = "producing"
            self._waiting_reason = None

        active_items = self._matching_queue_items(queue, include_done=False)
        if active_items:
            self.phase = "producing"
            return

        if self.issued_count >= self.config.count:
            return

        with bm_span("expert_logic", name=f"economy:{self.job_id}:produce"):
            self.game_api.produce(
                self.config.unit_type,
                1,
                auto_place_building=self.config.queue_type == "Building",
            )
        self.issued_count += 1
        self.phase = "producing"
        self.status = JobStatus.RUNNING

    def _apply_completion_events(self) -> None:
        history = self.world_model.query("events", {"limit": 50})
        events = history.get("events", []) if isinstance(history, dict) else []
        new_events = [
            event
            for event in events
            if event.get("type") == EventType.PRODUCTION_COMPLETE.value
            and float(event.get("timestamp", 0.0) or 0.0) > self._last_seen_event_ts
        ]
        new_events.sort(key=lambda item: float(item.get("timestamp", 0.0) or 0.0))
        for event in new_events:
            data = event.get("data") or {}
            queue_type = data.get("queue_type")
            name = data.get("name")
            display_name = data.get("display_name")
            if queue_type != self.config.queue_type:
                continue
            if not production_name_matches(self.config.unit_type, name, display_name):
                continue
            if self.produced_count >= self.config.count:
                continue
            self.produced_count += 1
            if self.config.queue_type == "Building":
                self._counted_ready_items_pending_placement += 1
            self.phase = "producing"
            self.status = JobStatus.RUNNING
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=f"生产完成 {self.produced_count}/{self.config.count}: {display_name or name}",
                expert_state={
                    "phase": self.phase,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "queue_type": self.config.queue_type,
                    "queue_scope": self._knowledge["queue_scope"],
                    "roles": self._knowledge["roles"],
                },
                data={
                    "unit_type": name,
                    "display_name": display_name,
                    "queue_type": queue_type,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "knowledge": self._knowledge,
                },
            )
        if new_events:
            self._last_seen_event_ts = max(float(event.get("timestamp", 0.0) or 0.0) for event in new_events)
        if self.config.queue_type != "Building" and self.produced_count >= self.config.count:
            self._finish_succeeded()

    def _waiting_reason_for(self, queue: Optional[dict[str, Any]], economy: dict[str, Any]) -> Optional[str]:
        if not self.resources:
            return "queue_unassigned"
        if queue is None:
            return "queue_missing"
        if (
            self.config.queue_type == "Building"
            and bool(queue.get("has_ready_item"))
            and not self._has_matching_ready_item(queue)
        ):
            return "queue_ready_item_pending"
        if bool(economy.get("low_power")) and not self._is_power_recovery_job():
            return "low_power"
        if float(economy.get("total_credits", 0) or 0) <= 0 and not self._matching_queue_items(queue, include_done=False):
            return "no_funds"
        if not self.game_api.can_produce(self.config.unit_type):
            return "cannot_produce"
        items = list(queue.get("items", []))
        if items and all(bool(item.get("paused")) for item in items):
            return "queue_paused"
        return None

    def _enter_waiting(self, reason: str) -> None:
        self.phase = "waiting"
        self.status = JobStatus.WAITING
        if reason == self._waiting_reason:
            return
        self._waiting_reason = reason
        guidance = self._guidance_for(reason)
        info_summary_map = {
            "queue_unassigned": "等待生产队列资源分配",
        }
        blocked_summary_map = {
            "queue_missing": f"生产队列 {self.config.queue_type} 不可用，等待工厂恢复",
            "low_power": guidance.get("summary") or "电力不足，生产暂停等待恢复",
            "no_funds": guidance.get("summary") or "资金不足，生产暂停等待资源恢复",
            "cannot_produce": f"当前无法生产 {self.config.unit_type}，等待前置条件恢复",
            "queue_paused": f"{self.config.queue_type} 队列暂停，等待恢复",
            "queue_ready_item_pending": guidance.get("summary") or "建造队列里有待放置建筑，等待先清空队列",
            "ready_item_not_placeable": "建筑已就绪但无法自动放置，等待人工清理或腾出位置",
        }
        if reason in info_summary_map:
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=info_summary_map[reason],
                expert_state={
                    "phase": self.phase,
                    "reason": reason,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "queue_scope": self._knowledge["queue_scope"],
                    "roles": self._knowledge["roles"],
                },
                data={
                    "reason": reason,
                    "queue_type": self.config.queue_type,
                    "knowledge": self._knowledge,
                    **guidance,
                },
            )
            return
        self.emit_signal(
            kind=SignalKind.BLOCKED,
            summary=blocked_summary_map.get(reason, "生产等待中"),
            expert_state={
                "phase": self.phase,
                "reason": reason,
                "produced_count": self.produced_count,
                "requested_count": self.config.count,
                "queue_scope": self._knowledge["queue_scope"],
                "roles": self._knowledge["roles"],
            },
            data={
                "reason": reason,
                "queue_type": self.config.queue_type,
                "knowledge": self._knowledge,
                **guidance,
            },
        )

    def _finish_succeeded(self) -> None:
        if self.status == JobStatus.SUCCEEDED:
            return
        self.phase = "completed"
        self.status = JobStatus.SUCCEEDED
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"生产完成 {self.produced_count}/{self.config.count}: {self.config.unit_type}",
            expert_state={
                "phase": self.phase,
                "produced_count": self.produced_count,
                "queue_scope": self._knowledge["queue_scope"],
                "roles": self._knowledge["roles"],
            },
            result="succeeded",
            data={
                "unit_type": self.config.unit_type,
                "queue_type": self.config.queue_type,
                "produced_count": self.produced_count,
                "repeat": self.config.repeat,
                "knowledge": self._knowledge,
            },
        )

    def _queue_state(self) -> Optional[dict[str, Any]]:
        queues = self.world_model.query("production_queues")
        if not isinstance(queues, dict):
            return None
        queue = queues.get(self.config.queue_type)
        return dict(queue) if isinstance(queue, dict) else None

    def _matching_self_actor_ids(self) -> set[int]:
        try:
            payload = self.world_model.query("my_actors")
        except Exception:
            return set()
        actors = payload.get("actors", []) if isinstance(payload, dict) else []
        matching_ids: set[int] = set()
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            if production_name_matches(
                self.config.unit_type,
                actor.get("name"),
                actor.get("display_name"),
            ):
                actor_id = actor.get("actor_id")
                if actor_id is not None:
                    matching_ids.add(int(actor_id))
        return matching_ids

    def _sync_direct_actor_completions(self) -> None:
        if self.status == JobStatus.SUCCEEDED:
            return
        matching_ids = self._matching_self_actor_ids()
        new_ids = matching_ids - self._known_matching_actor_ids
        self._known_matching_actor_ids = matching_ids
        if not new_ids:
            return
        while new_ids and self.produced_count < min(self.issued_count, self.config.count):
            actor_id = new_ids.pop()
            self.produced_count += 1
            self.phase = "placing" if self.config.queue_type == "Building" else "producing"
            self.status = JobStatus.RUNNING
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=(
                    f"建筑已落地 {self.produced_count}/{self.config.count}: {self.config.unit_type}"
                    if self.config.queue_type == "Building"
                    else f"单位已到场 {self.produced_count}/{self.config.count}: {self.config.unit_type}"
                ),
                expert_state={
                    "phase": self.phase,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "queue_type": self.config.queue_type,
                    "queue_scope": self._knowledge["queue_scope"],
                    "roles": self._knowledge["roles"],
                },
                data={
                    "actor_id": actor_id,
                    "unit_type": self.config.unit_type,
                    "queue_type": self.config.queue_type,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "knowledge": self._knowledge,
                },
            )

    def _has_matching_ready_item(self, queue: Optional[dict[str, Any]]) -> bool:
        return any(bool(item.get("done")) for item in self._matching_queue_items(queue, include_done=True))

    def _is_power_recovery_job(self) -> bool:
        if self.config.queue_type != "Building":
            return False
        return any(
            production_name_matches(self.config.unit_type, code, display_name)
            for code, display_name in (
                ("powr", "发电厂"),
                ("apwr", "高级发电厂"),
            )
        )

    def _guidance_for(self, reason: str) -> dict[str, Any]:
        if reason == "low_power":
            recovery_options = buildable_power_recovery_options(self.game_api)
            if recovery_options:
                names = "或".join(option["display_name"] for option in recovery_options)
                summary = f"电力不足，生产会变慢且部分建筑会离线，建议补建{names}"
            else:
                summary = "电力不足，生产会变慢且部分建筑会离线，建议优先恢复供电建筑"
            return {
                "summary": summary,
                "impact": low_power_impacts(),
                "recommendation": {
                    "kind": "power_recovery",
                    "queue_type": "Building",
                    "queue_scope": self._knowledge["queue_scope"],
                    "options": recovery_options,
                },
            }
        if reason == "no_funds":
            recovery_options = buildable_economy_recovery_options(self.game_api)
            guidance: dict[str, Any] = {
                "impact": {"kind": "economy_weak", "effects": ["income_insufficient"]},
                "recommendation": {
                    "kind": "econ_recovery",
                    "queue_scope": self._knowledge["queue_scope"],
                    "options": recovery_options,
                },
            }
            if recovery_options:
                names = "或".join(option["display_name"] for option in recovery_options)
                guidance["summary"] = f"资金不足，建议优先恢复经济：{names}"
            return guidance
        if reason == "queue_ready_item_pending":
            return {
                "summary": "共享建造队列里有待放置建筑，建议先清空队列",
                "impact": {"kind": "queue_blocked", "effects": ["ready_item_pending"]},
                "recommendation": {
                    "kind": "clear_ready_building",
                    "queue_scope": self._knowledge["queue_scope"],
                },
            }
        return {}

    def _maybe_place_ready_building(self, queue: Optional[dict[str, Any]]) -> Optional[str]:
        if self.config.queue_type != "Building" or queue is None:
            return None
        if not self._has_matching_ready_item(queue):
            return None
        try:
            with bm_span("expert_logic", name=f"economy:{self.job_id}:place_building"):
                self.game_api.place_building(self.config.queue_type)
        except GameAPIError:
            self._enter_waiting("ready_item_not_placeable")
            return "blocked"
        if self._counted_ready_items_pending_placement > 0:
            self._counted_ready_items_pending_placement -= 1
        elif self.produced_count < self.config.count:
            self.produced_count += 1
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=f"已放置待建成建筑 {self.produced_count}/{self.config.count}: {self.config.unit_type}",
                expert_state={
                    "phase": "placing",
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "queue_type": self.config.queue_type,
                    "queue_scope": self._knowledge["queue_scope"],
                    "roles": self._knowledge["roles"],
                },
                data={
                    "unit_type": self.config.unit_type,
                    "queue_type": self.config.queue_type,
                    "produced_count": self.produced_count,
                    "requested_count": self.config.count,
                    "knowledge": self._knowledge,
                },
            )
        return "placed"

    def _matching_queue_items(self, queue: Optional[dict[str, Any]], *, include_done: bool) -> list[dict[str, Any]]:
        if queue is None:
            return []
        items = []
        for item in queue.get("items", []):
            if not production_name_matches(
                self.config.unit_type,
                item.get("name"),
                item.get("display_name"),
            ):
                continue
            if not include_done and bool(item.get("done")):
                continue
            items.append(dict(item))
        return items

    def _cleanup_queue_on_abort(self) -> None:
        queue = self._queue_state()
        if queue is None:
            return
        items = list(queue.get("items", []))
        if not items:
            return
        first_item = items[0]
        if not production_name_matches(
            self.config.unit_type,
            first_item.get("name"),
            first_item.get("display_name"),
        ):
            return
        try:
            self.game_api.manage_production(
                self.config.queue_type,
                "cancel",
                owner_actor_id=first_item.get("owner_actor_id"),
                item_name=first_item.get("name"),
                count=1,
            )
        except GameAPIError as exc:
            logger.warning(
                "EconomyJob abort queue cleanup failed: job_id=%s queue=%s unit=%s error=%s",
                self.job_id,
                self.config.queue_type,
                self.config.unit_type,
                exc,
            )
            slog.warn(
                "Economy job abort queue cleanup failed",
                event="economy_abort_queue_cleanup_failed",
                job_id=self.job_id,
                task_id=self.task_id,
                queue_type=self.config.queue_type,
                unit_type=self.config.unit_type,
                error=str(exc),
            )
            return
        slog.warn(
            "Economy job cleaned queue item on abort",
            event="economy_abort_queue_cleanup",
            job_id=self.job_id,
            task_id=self.task_id,
            queue_type=self.config.queue_type,
            unit_type=self.config.unit_type,
            item_name=first_item.get("name"),
            item_done=bool(first_item.get("done")),
        )


class EconomyExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "EconomyExpert"

    def create_job(
        self,
        task_id: str,
        config: EconomyJobConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> EconomyJob:
        return EconomyJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            game_api=self.game_api,
            world_model=self.world_model,
            constraint_provider=constraint_provider,
        )
