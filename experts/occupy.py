"""OccupyExpert — capture a visible target with explicit task-owned occupiers."""

from __future__ import annotations

import time
from typing import Any, Optional, Protocol

from models import JobStatus, OccupyJobConfig, ResourceKind, ResourceNeed, SignalKind
from openra_api.models import Actor

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike

_VERIFY_TIMEOUT_S = 5.0


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class OccupyJob(BaseJob):
    tick_interval = 0.5

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: OccupyJobConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
        game_api: GameAPILike,
        world_model: WorldModelLike,
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
        self._issued = False
        self._issued_at = 0.0

    @property
    def expert_type(self) -> str:
        return "OccupyExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        config: OccupyJobConfig = self.config  # type: ignore[assignment]
        return [
            ResourceNeed(
                job_id=self.job_id,
                kind=ResourceKind.ACTOR,
                count=1,
                predicates={"actor_id": str(actor_id), "owner": "self"},
            )
            for actor_id in config.actor_ids
        ]

    def tick(self) -> None:
        config: OccupyJobConfig = self.config  # type: ignore[assignment]
        if not self.resources:
            return

        if not self._issued:
            occupier_ids = self._actor_ids_from_resources()
            if not occupier_ids:
                return
            occupiers = [Actor(actor_id=actor_id) for actor_id in occupier_ids]
            target = Actor(actor_id=config.target_actor_id)
            try:
                self.game_api.occupy_units(occupiers, [target])
            except Exception as exc:
                self.status = JobStatus.FAILED
                self.emit_signal(
                    kind=SignalKind.TASK_COMPLETE,
                    summary=f"Capture command failed for target {config.target_actor_id}: {exc}",
                    result="failed",
                    data={"target_actor_id": config.target_actor_id, "error": str(exc)},
                )
                return

            self._issued = True
            self._issued_at = time.time()
            return

        result = self.world_model.query("actor_by_id", {"actor_id": config.target_actor_id})
        actor = result.get("actor") if isinstance(result, dict) else None
        if actor and actor.get("owner") == "self":
            self.status = JobStatus.SUCCEEDED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Captured target {config.target_actor_id}",
                result="succeeded",
                data={
                    "target_actor_id": config.target_actor_id,
                    "actor_ids": self._actor_ids_from_resources(),
                },
            )
            return

        elapsed = time.time() - self._issued_at
        if elapsed >= _VERIFY_TIMEOUT_S:
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Capture timeout after {_VERIFY_TIMEOUT_S}s for target {config.target_actor_id}",
                result="failed",
                data={
                    "target_actor_id": config.target_actor_id,
                    "reason": "occupy_command_sent_but_target_not_captured",
                    "elapsed_s": round(elapsed, 2),
                },
            )

    def _actor_ids_from_resources(self) -> list[int]:
        actor_ids: list[int] = []
        for resource in self.resources:
            if not resource.startswith("actor:"):
                continue
            try:
                actor_ids.append(int(resource.split(":", 1)[1]))
            except ValueError:
                continue
        return actor_ids


class OccupyExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "OccupyExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> OccupyJob:
        return OccupyJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
            world_model=self.world_model,
        )
