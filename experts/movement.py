"""MovementExpert — moves units to a target position (design.md §3).

MovementJob ticks until all assigned actors reach within arrival_radius
of target_position, then emits task_complete. Supports move, attack_move,
and retreat modes.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Protocol

from models import ConstraintEnforcement, MovementJobConfig, MoveMode, SignalKind
from openra_api.models import Actor, Location

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike

logger = logging.getLogger(__name__)


class WorldModelLike(Protocol):
    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...


class MovementJob(BaseJob):
    """Moves assigned actors to target_position."""

    tick_interval = 0.5

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        config: MovementJobConfig,
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
        self._move_issued = False
        self._tick_count = 0

    @property
    def expert_type(self) -> str:
        return "MovementExpert"

    def tick(self) -> None:
        self._tick_count += 1
        config: MovementJobConfig = self.config  # type: ignore[assignment]

        if not self.resources:
            return

        actor_ids = self._get_actor_ids()
        if not actor_ids:
            return

        # Check arrival
        if self._all_arrived(actor_ids, config.target_position, config.arrival_radius):
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=f"All units arrived at {config.target_position}",
                result="succeeded",
                data={"position": list(config.target_position), "actors_arrived": actor_ids},
            )
            from models import JobStatus
            self.status = JobStatus.SUCCEEDED
            return

        # Issue move command (re-issue periodically for stragglers)
        if not self._move_issued or self._tick_count % 5 == 0:
            attack_move = config.move_mode in (MoveMode.ATTACK_MOVE, MoveMode.RETREAT)
            target = self._effective_target(actor_ids, config)
            if target is None:
                # do_not_chase CLAMP blocked the move — skip silently
                return
            try:
                actors = [Actor(actor_id=aid) for aid in actor_ids]
                location = Location(x=target[0], y=target[1])
                self.game_api.move_units_by_location(actors, location, attack_move=attack_move)
                self._move_issued = True
            except Exception as e:
                logger.warning("MovementJob move failed: %s", e)

        # Progress report every 10 ticks
        if self._tick_count % 10 == 0:
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=f"Moving {len(actor_ids)} units to {config.target_position}",
                expert_state={"tick": self._tick_count, "actors": actor_ids},
            )

    def _get_actor_ids(self) -> list[int]:
        """Extract integer actor IDs from resource strings."""
        ids = []
        for r in self.resources:
            if r.startswith("actor:"):
                try:
                    ids.append(int(r.split(":", 1)[1]))
                except ValueError:
                    pass
        return ids

    def _actor_centroid(self, actor_ids: list[int]) -> Optional[tuple[int, int]]:
        """Compute centroid of all living actors."""
        positions = []
        for aid in actor_ids:
            result = self.world_model.query("actor_by_id", {"actor_id": aid})
            actor = result.get("actor") if isinstance(result, dict) else None
            if actor and actor.get("position"):
                positions.append(actor["position"])
        if not positions:
            return None
        avg_x = sum(p[0] for p in positions) // len(positions)
        avg_y = sum(p[1] for p in positions) // len(positions)
        return (avg_x, avg_y)

    def _effective_target(
        self,
        actor_ids: list[int],
        config: MovementJobConfig,
    ) -> Optional[tuple[int, int]]:
        """Apply do_not_chase constraint to target position.

        Returns None to signal the move should be skipped (CLAMP blocked it).
        """
        for c in self._constraints_of_kind("do_not_chase"):
            max_dist = c.params.get("max_distance")
            if max_dist is None:
                continue
            centroid = self._actor_centroid(actor_ids)
            if centroid is None:
                continue
            dist = math.dist(centroid, config.target_position)
            if dist > max_dist:
                if c.enforcement == ConstraintEnforcement.ESCALATE:
                    self.emit_constraint_violation(
                        "do_not_chase",
                        {
                            "max_distance": max_dist,
                            "actual_distance": round(dist, 1),
                            "target": list(config.target_position),
                            "centroid": list(centroid),
                        },
                    )
                elif c.enforcement == ConstraintEnforcement.CLAMP:
                    return None  # skip the move
        return config.target_position

    def _all_arrived(self, actor_ids: list[int], target: tuple[int, int], radius: int) -> bool:
        """Check if all living actors are within arrival_radius of target.

        Returns False if no living actors remain (all dead ≠ arrived).
        """
        alive_count = 0
        for aid in actor_ids:
            result = self.world_model.query("actor_by_id", {"actor_id": aid})
            actor = result.get("actor") if isinstance(result, dict) else None
            if actor is None:
                continue  # Dead actor — skip
            alive_count += 1
            pos = actor.get("position", [0, 0])
            dist = math.dist((pos[0], pos[1]), (target[0], target[1]))
            if dist > radius:
                return False
        return alive_count > 0


class MovementExpert(ExecutionExpert):
    def __init__(self, *, game_api: GameAPILike, world_model: WorldModelLike) -> None:
        self.game_api = game_api
        self.world_model = world_model

    @property
    def expert_type(self) -> str:
        return "MovementExpert"

    def create_job(
        self,
        task_id: str,
        config: Any,
        signal_callback: SignalCallback,
        constraint_provider: Optional[ConstraintProvider] = None,
    ) -> MovementJob:
        return MovementJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            game_api=self.game_api,
            world_model=self.world_model,
        )
