"""MovementExpert — moves units to a target position (design.md §3).

MovementJob ticks until all assigned actors reach within arrival_radius
of target_position, then emits task_complete. Supports move, attack_move,
and retreat modes.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Protocol

from models import ConstraintEnforcement, MovementJobConfig, MoveMode, ResourceKind, ResourceNeed, SignalKind
from openra_api.models import Actor, Location

from .base import BaseJob, ConstraintProvider, ExecutionExpert, SignalCallback
from .game_api_protocol import GameAPILike

logger = logging.getLogger(__name__)
_AUTO_PARTIAL_GROUP_COUNT = 3


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
        self._last_centroid: Optional[tuple[int, int]] = None
        self._stuck_ticks = 0
        self._stuck_threshold = 10

    @property
    def expert_type(self) -> str:
        return "MovementExpert"

    def get_resource_needs(self) -> list[ResourceNeed]:
        config: MovementJobConfig = self.config  # type: ignore[assignment]
        if config.actor_ids:
            needs = [
                ResourceNeed(
                    job_id=self.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(aid), "owner": "self"},
                )
                for aid in config.actor_ids
            ]
            if not config.wait_for_full_group:
                needs.insert(
                    0,
                    ResourceNeed(
                        job_id=self.job_id,
                        kind=ResourceKind.ACTOR,
                        count=self._normalized_min_ready_count(config.actor_ids, config.min_ready_count),
                        predicates={
                            "owner": "self",
                            "actor_ids_any": ",".join(str(aid) for aid in config.actor_ids),
                        },
                    ),
                )
            return needs
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
        self._tick_count += 1
        config: MovementJobConfig = self.config  # type: ignore[assignment]

        if not self.resources:
            return

        bound_actor_ids = self._get_actor_ids()
        if not bound_actor_ids:
            return
        completion_actor_ids = self._completion_actor_ids(config, bound_actor_ids)

        # Check arrival
        arrived_actor_ids = self._arrived_actor_ids(completion_actor_ids, config)
        arrived_count, alive_count = self._arrival_counts(completion_actor_ids, config)
        if self._arrival_complete(completion_actor_ids, config, arrived_count=arrived_count, alive_count=alive_count):
            if config.wait_for_full_group:
                summary = f"All units arrived at {config.target_position}"
            else:
                summary = (
                    f"Sufficient units arrived at {config.target_position} "
                    f"({arrived_count}/{alive_count})"
                )
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary=summary,
                result="succeeded",
                data={
                    "position": list(config.target_position),
                    "actors_arrived": arrived_actor_ids,
                    "arrived_count": arrived_count,
                    "alive_count": alive_count,
                },
            )
            from models import JobStatus
            self.status = JobStatus.SUCCEEDED
            return

        # Stuck detection: if centroid hasn't moved for _stuck_threshold ticks → fail
        centroid = self._actor_centroid(bound_actor_ids)
        if centroid is not None:
            if self._last_centroid is not None:
                dist = abs(centroid[0] - self._last_centroid[0]) + abs(centroid[1] - self._last_centroid[1])
                if dist < 2:
                    self._stuck_ticks += 1
                else:
                    self._stuck_ticks = 0
            self._last_centroid = centroid

        if self._stuck_ticks >= self._stuck_threshold:
            signal_data = self._explicit_group_signal_data(
                config,
                bound_actor_ids=bound_actor_ids,
                arrived_count=arrived_count,
            )
            summary = f"Units stuck for {self._stuck_ticks} ticks, cannot reach {config.target_position}"
            if signal_data:
                summary += (
                    f" | group={signal_data['bound_count']}/{signal_data['requested_total']}"
                    f" | missing={signal_data['missing_count']}"
                )
            self.emit_signal(
                kind=SignalKind.RISK_ALERT,
                summary=summary,
                data={
                    "position": list(centroid or (0, 0)),
                    "target": list(config.target_position),
                    **signal_data,
                },
            )
            from models import JobStatus
            self.status = JobStatus.FAILED
            return

        # Issue move command (re-issue periodically for stragglers)
        if not self._move_issued or self._tick_count % 5 == 0:
            attack_move = config.move_mode in (MoveMode.ATTACK_MOVE, MoveMode.RETREAT)
            target = self._effective_target(bound_actor_ids, config)
            if target is None:
                # do_not_chase CLAMP blocked the move — skip silently
                return
            try:
                actors = [Actor(actor_id=aid) for aid in bound_actor_ids]
                if config.path:
                    path = [Location(x=point[0], y=point[1]) for point in config.path]
                    self.game_api.move_units_by_path(actors, path, attack_move=attack_move)
                else:
                    location = Location(x=target[0], y=target[1])
                    self.game_api.move_units_by_location(actors, location, attack_move=attack_move)
                self._move_issued = True
            except Exception as e:
                logger.warning("MovementJob move failed: %s", e)

        # Progress report every 10 ticks
        if self._tick_count % 10 == 0:
            signal_data = self._explicit_group_signal_data(
                config,
                bound_actor_ids=bound_actor_ids,
                arrived_count=arrived_count,
            )
            summary = f"Moving {len(bound_actor_ids)} units to {config.target_position}"
            if signal_data:
                summary += (
                    f" | group={signal_data['bound_count']}/{signal_data['requested_total']}"
                    f" | missing={signal_data['missing_count']}"
                )
            self.emit_signal(
                kind=SignalKind.PROGRESS,
                summary=summary,
                expert_state={"tick": self._tick_count, "actors": bound_actor_ids},
                data=signal_data or None,
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

    @staticmethod
    def _explicit_group_signal_data(
        config: MovementJobConfig,
        *,
        bound_actor_ids: list[int],
        arrived_count: int = 0,
    ) -> dict[str, Any]:
        actor_ids = [int(actor_id) for actor_id in list(config.actor_ids or []) if actor_id is not None]
        if not actor_ids:
            return {}
        requested_total = len(actor_ids)
        bound_count = len(bound_actor_ids)
        missing_count = max(requested_total - bound_count, 0)
        return {
            "source_expert": "MovementExpert",
            "explicit_group": True,
            "requested_total": requested_total,
            "bound_count": bound_count,
            "missing_count": missing_count,
            "arrived_count": max(int(arrived_count or 0), 0),
        }

    @staticmethod
    def _normalized_min_ready_count(actor_ids: list[int], configured: int) -> int:
        if not actor_ids:
            return 1
        requested = int(configured or 0)
        if requested <= 0:
            requested = min(len(actor_ids), _AUTO_PARTIAL_GROUP_COUNT)
        return max(1, min(len(actor_ids), requested))

    def _arrival_counts(self, actor_ids: list[int], config: MovementJobConfig) -> tuple[int, int]:
        """Return (arrived_count, alive_count) for the current actor set."""
        alive_count = 0
        arrived_count = 0
        for aid in actor_ids:
            result = self.world_model.query("actor_by_id", {"actor_id": aid})
            actor = result.get("actor") if isinstance(result, dict) else None
            if actor is None:
                continue  # Dead actor — skip
            alive_count += 1
            pos = actor.get("position", [0, 0])
            dist = math.dist((pos[0], pos[1]), (config.target_position[0], config.target_position[1]))
            if dist <= config.arrival_radius:
                arrived_count += 1
        return arrived_count, alive_count

    def _arrived_actor_ids(self, actor_ids: list[int], config: MovementJobConfig) -> list[int]:
        """Return the subset of actor_ids currently inside arrival_radius."""
        arrived: list[int] = []
        for aid in actor_ids:
            result = self.world_model.query("actor_by_id", {"actor_id": aid})
            actor = result.get("actor") if isinstance(result, dict) else None
            if actor is None:
                continue
            pos = actor.get("position", [0, 0])
            dist = math.dist((pos[0], pos[1]), (config.target_position[0], config.target_position[1]))
            if dist <= config.arrival_radius:
                arrived.append(aid)
        return arrived

    @staticmethod
    def _completion_actor_ids(config: MovementJobConfig, bound_actor_ids: list[int]) -> list[int]:
        """Preserve explicit-group completion truth even if current bindings shrink."""
        if config.actor_ids:
            return list(config.actor_ids)
        return bound_actor_ids

    def _arrival_complete(
        self,
        actor_ids: list[int],
        config: MovementJobConfig,
        *,
        arrived_count: Optional[int] = None,
        alive_count: Optional[int] = None,
    ) -> bool:
        """Check whether enough living actors have reached the destination."""
        if arrived_count is None or alive_count is None:
            arrived_count, alive_count = self._arrival_counts(actor_ids, config)
        if alive_count <= 0:
            return False
        if config.wait_for_full_group:
            return arrived_count >= alive_count
        required = self._normalized_min_ready_count(actor_ids, config.min_complete_count or config.min_ready_count)
        return arrived_count >= min(alive_count, required)


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
