"""DeployExpert — single-action deploy with result verification (design.md §3).

Phase 1 (deploy): call GameAPI deploy_units, record pre-deploy Construction Yard IDs.
Phase 2 (verifying): poll until a new CY appears (success) or timeout (failure).

Success: MCV actor gone AND new Construction Yard actor_id detected.
Failure: 5 seconds elapsed with no new Construction Yard → signal contains
         reason="deploy_command_sent_but_no_yard_appeared".
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from models import DeployJobConfig, JobStatus, SignalKind
from openra_api.models import Actor, TargetsQueryParam

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike

logger = logging.getLogger(__name__)

# Construction Yard type names recognised by query_actor (Chinese display names).
_CY_TYPES = ["建造厂", "基地"]
_VERIFY_TIMEOUT_S = 5.0


class DeployJob(BaseJob):
    """Two-phase deploy job: send command then verify CY appearance."""

    tick_interval = 0.5

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
        self._phase = "deploy"  # "deploy" | "verifying"
        self._deploy_sent_at: float = 0.0
        self._pre_deploy_yard_ids: set[int] = set()

    @property
    def expert_type(self) -> str:
        return "DeployExpert"

    def tick(self) -> None:
        if self._phase == "deploy":
            self._do_deploy()
        elif self._phase == "verifying":
            self._check_verify()

    # --- Phase 1: send deploy command ---

    def _do_deploy(self) -> None:
        config: DeployJobConfig = self.config  # type: ignore[assignment]

        # Record existing CY actor IDs so we can detect a new one after deploy.
        try:
            existing = self.game_api.query_actor(
                TargetsQueryParam(type=_CY_TYPES, faction="自己")
            )
            self._pre_deploy_yard_ids = {a.actor_id for a in existing}
        except Exception as e:
            logger.warning("DeployJob: pre-deploy CY query failed (non-fatal): %s", e)
            self._pre_deploy_yard_ids = set()

        try:
            actor = Actor(actor_id=config.actor_id)
            self.game_api.deploy_units([actor])
        except Exception as e:
            logger.warning("DeployJob deploy_units failed: %s", e)
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Deploy command failed for actor {config.actor_id}: {e}",
                result="failed",
                data={"actor_id": config.actor_id, "error": str(e)},
            )
            return

        self._phase = "verifying"
        self._deploy_sent_at = time.time()

    # --- Phase 2: verify CY appeared ---

    def _check_verify(self) -> None:
        config: DeployJobConfig = self.config  # type: ignore[assignment]
        elapsed = time.time() - self._deploy_sent_at

        # Query for a new Construction Yard (one not in pre-deploy snapshot).
        try:
            current_yards = self.game_api.query_actor(
                TargetsQueryParam(type=_CY_TYPES, faction="自己")
            )
            new_yards = [a for a in current_yards if a.actor_id not in self._pre_deploy_yard_ids]
        except Exception as e:
            logger.warning("DeployJob: CY verification query failed: %s", e)
            new_yards = []

        if new_yards:
            yard = new_yards[0]
            self.status = JobStatus.SUCCEEDED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"Deploy confirmed: Construction Yard {yard.actor_id} appeared",
                result="succeeded",
                data={
                    "actor_id": config.actor_id,
                    "yard_actor_id": yard.actor_id,
                    "building_type": config.building_type,
                    "position": list(config.target_position),
                },
            )
            return

        if elapsed >= _VERIFY_TIMEOUT_S:
            self.status = JobStatus.FAILED
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=(
                    f"Deploy timeout after {_VERIFY_TIMEOUT_S}s: "
                    f"no Construction Yard appeared for actor {config.actor_id}"
                ),
                result="failed",
                data={
                    "actor_id": config.actor_id,
                    "reason": "deploy_command_sent_but_no_yard_appeared",
                    "elapsed_s": round(elapsed, 2),
                },
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
