"""StopExpert — immediate interrupt for task-owned units.

This is the minimal tactical ownership primitive: stop the task's current
unit group right now, then immediately succeed.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from models import JobStatus, ResourceKind, ResourceNeed, SignalKind, StopJobConfig
from openra_api.models import Actor

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class StopJob(BaseJob):
    """One-shot stop order for currently assigned actors."""

    tick_interval = 0.2

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: StopJobConfig,
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
        return "StopExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        config: StopJobConfig = self.config  # type: ignore[assignment]
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
        actor_ids = self._get_actor_ids()
        if not actor_ids:
            return

        self.game_api.stop([Actor(actor_id=aid) for aid in actor_ids])
        self._issued = True
        self.status = JobStatus.SUCCEEDED
        self.emit_signal(
            kind=SignalKind.TASK_COMPLETE,
            summary=f"Stopped {len(actor_ids)} units",
            result="succeeded",
            data={"actor_ids": actor_ids},
        )

    def _get_actor_ids(self) -> list[int]:
        ids: list[int] = []
        for resource in self.resources:
            if not resource.startswith("actor:"):
                continue
            try:
                ids.append(int(resource.split(":", 1)[1]))
            except ValueError:
                continue
        return ids


class StopExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "StopExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> StopJob:
        return StopJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
            world_model=self.world_model,
        )
