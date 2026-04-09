"""RepairExpert — send task-owned damaged units to repair immediately."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from models import JobStatus, RepairJobConfig, ResourceKind, ResourceNeed, SignalKind
from openra_api.models import Actor

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class RepairJob(BaseJob):
    """One-shot repair order for currently assigned damaged actors."""

    tick_interval = 0.2

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: RepairJobConfig,
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

    @property
    def expert_type(self) -> str:
        return "RepairExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        config: RepairJobConfig = self.config  # type: ignore[assignment]
        if config.actor_ids:
            return [
                ResourceNeed(
                    job_id=self.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(aid), "owner": "self"},
                )
                for aid in config.actor_ids
            ]
        count = config.unit_count
        if count <= 0:
            count = 999
        return [
            ResourceNeed(
                job_id=self.job_id,
                kind=ResourceKind.ACTOR,
                count=count,
                predicates={"owner": "self"},
            )
        ]

    def tick(self) -> None:
        if self._issued or not self.resources:
            return
        damaged = self._get_damaged_actors()
        if not damaged:
            self._issued = True
            self.status = JobStatus.SUCCEEDED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary="No damaged units need repair",
                result="succeeded",
                data={"actor_ids": [], "damaged_count": 0},
            )
            return

        self.game_api.repair_units(damaged)
        self._issued = True
        self.status = JobStatus.SUCCEEDED
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"Sent {len(damaged)} units to repair",
            result="succeeded",
            data={"actor_ids": [actor.actor_id for actor in damaged], "damaged_count": len(damaged)},
        )

    def _get_damaged_actors(self) -> list[Actor]:
        actor_ids: list[int] = []
        for resource in self.resources:
            if not resource.startswith("actor:"):
                continue
            try:
                actor_ids.append(int(resource.split(":", 1)[1]))
            except ValueError:
                continue

        damaged: list[Actor] = []
        for actor_id in actor_ids:
            actor = self.game_api.get_actor_by_id(actor_id)
            if actor is None:
                continue
            hppercent = getattr(actor, "hppercent", None)
            if hppercent is None or hppercent < 100:
                damaged.append(actor)
        return damaged


class RepairExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "RepairExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> RepairJob:
        return RepairJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
            world_model=self.world_model,
        )
