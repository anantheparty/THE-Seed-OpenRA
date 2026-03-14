"""DeployExpert — single-action deploy (design.md §3).

DeployJob calls GameAPI deploy once → success/failure → task_complete.
Used for MCV deployment, building placement, etc.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from models import DeployJobConfig, JobStatus, SignalKind

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback

logger = logging.getLogger(__name__)


class GameAPILike(Protocol):
    def deploy_actor(self, actor_id: int) -> bool: ...


class DeployJob(BaseJob):
    """Single-action deploy job."""

    tick_interval = 0.5  # Only needs one tick really

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: DeployJobConfig,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
        game_api: GameAPILike,
    ) -> None:
        super().__init__(
            job_id=job_id,
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )
        self.game_api = game_api
        self._deployed = False

    @property
    def expert_type(self) -> str:
        return "DeployExpert"

    def tick(self) -> None:
        if self._deployed:
            return

        config: DeployJobConfig = self.config  # type: ignore[assignment]
        self._deployed = True

        try:
            success = self.game_api.deploy_actor(config.actor_id)
        except Exception as e:
            logger.warning("DeployJob deploy_actor failed: %s", e)
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Deploy failed: {e}",
                result="failed",
                data={"actor_id": config.actor_id, "error": str(e)},
            )
            return

        if success:
            self.status = JobStatus.SUCCEEDED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Deployed actor {config.actor_id} at {config.target_position}",
                result="succeeded",
                data={
                    "actor_id": config.actor_id,
                    "position": list(config.target_position),
                    "building_type": config.building_type,
                },
            )
        else:
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Deploy failed for actor {config.actor_id}",
                result="failed",
                data={"actor_id": config.actor_id, "position": list(config.target_position)},
            )


class DeployExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike) -> None:
        self.game_api = game_api

    @property
    def expert_type(self) -> str:
        return "DeployExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> DeployJob:
        return DeployJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
        )
