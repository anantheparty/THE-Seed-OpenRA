"""RallyExpert — set production-building rally points deterministically."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from models import JobStatus, RallyJobConfig, ResourceKind, ResourceNeed, SignalKind
from openra_api.models import Actor, Location

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike

_RALLY_ELIGIBLE_TYPES = frozenset({"barr", "weap", "afld"})


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class RallyJob(BaseJob):
    """One-shot rally update for explicit production buildings."""

    tick_interval = 0.2

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: RallyJobConfig,
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
        return "RallyExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        config: RallyJobConfig = self.config  # type: ignore[assignment]
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
        if self._issued or not self.resources:
            return

        config: RallyJobConfig = self.config  # type: ignore[assignment]
        eligible, ignored = self._partition_rally_actors()
        if not eligible:
            self._issued = True
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary="No eligible production buildings for rally point",
                result="failed",
                data={
                    "actor_ids": [],
                    "ignored_actor_ids": ignored,
                    "eligible_types": sorted(_RALLY_ELIGIBLE_TYPES),
                    "target_position": list(config.target_position),
                },
            )
            return

        self.game_api.set_rally_point(
            eligible,
            Location(x=config.target_position[0], y=config.target_position[1]),
        )
        self._issued = True
        self.status = JobStatus.SUCCEEDED
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"Set rally point for {len(eligible)} production buildings",
            result="succeeded",
            data={
                "actor_ids": [actor.actor_id for actor in eligible],
                "ignored_actor_ids": ignored,
                "target_position": list(config.target_position),
            },
        )

    def _partition_rally_actors(self) -> tuple[list[Actor], list[int]]:
        actor_ids: list[int] = []
        for resource in self.resources:
            if not resource.startswith("actor:"):
                continue
            try:
                actor_ids.append(int(resource.split(":", 1)[1]))
            except ValueError:
                continue

        eligible: list[Actor] = []
        ignored: list[int] = []
        for actor_id in actor_ids:
            actor = self.game_api.get_actor_by_id(actor_id)
            actor_type = (getattr(actor, "type", None) or "").lower() if actor is not None else ""
            if actor is None or actor_type not in _RALLY_ELIGIBLE_TYPES:
                ignored.append(actor_id)
                continue
            eligible.append(actor)
        return eligible, ignored


class RallyExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "RallyExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> RallyJob:
        return RallyJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
            world_model=self.world_model,
        )
