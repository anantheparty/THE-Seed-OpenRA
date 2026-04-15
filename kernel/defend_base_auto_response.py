"""Kernel helpers for the defend-base auto-response feature island."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Optional, Protocol, TYPE_CHECKING

from models import (
    DEFAULT_GENERIC_COMBAT_UNIT_COUNT,
    CombatJobConfig,
    EngagementMode,
    Event,
    JobStatus,
    Task,
    TaskKind,
    TaskStatus,
)

if TYPE_CHECKING:
    from world_model import WorldModel


class JobLike(Protocol):
    job_id: str
    task_id: str
    expert_type: str
    status: JobStatus
    config: Any

    def patch(self, params: dict[str, Any]) -> None:
        ...


def ensure_defend_base_task(
    tasks: Iterable[Task],
    *,
    last_created: float,
    now: float,
    cooldown_s: float,
    create_task: Callable[[str, TaskKind, int], Task],
) -> tuple[Optional[Task], float]:
    """Return an existing defend_base task or create one if cooldown allows."""
    terminal = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}
    for task in tasks:
        if task.raw_text == "defend_base" and task.status not in terminal:
            return task, last_created
    if now - last_created < cooldown_s:
        return None, last_created
    return create_task("defend_base", TaskKind.MANAGED, 80), now


def active_task_jobs(
    jobs: Mapping[str, JobLike],
    task_id: str,
    *,
    expert_type: Optional[str] = None,
    is_terminal_status: Callable[[JobStatus], bool],
) -> list[JobLike]:
    result: list[JobLike] = []
    for controller in jobs.values():
        if controller.task_id != task_id:
            continue
        if expert_type is not None and controller.expert_type != expert_type:
            continue
        if is_terminal_status(controller.status):
            continue
        result.append(controller)
    result.sort(key=lambda item: item.job_id)
    return result


def resolve_defend_base_target_position(
    world_model: WorldModel,
    event: Event,
) -> Optional[tuple[int, int]]:
    if event.position is not None:
        return (int(event.position[0]), int(event.position[1]))

    if event.actor_id is not None:
        actor = world_model.state.actors.get(event.actor_id)
        if actor is not None:
            return (int(actor.position[0]), int(actor.position[1]))

    buildings = world_model.find_actors(owner="self", category="building")
    if buildings:
        x = round(sum(actor.position[0] for actor in buildings) / len(buildings))
        y = round(sum(actor.position[1] for actor in buildings) / len(buildings))
        return (int(x), int(y))

    actors = world_model.find_actors(owner="self")
    if actors:
        x = round(sum(actor.position[0] for actor in actors) / len(actors))
        y = round(sum(actor.position[1] for actor in actors) / len(actors))
        return (int(x), int(y))

    return None


def ensure_immediate_defend_base_job(
    task: Task,
    event: Event,
    *,
    world_model: WorldModel,
    jobs: Mapping[str, JobLike],
    is_terminal_status: Callable[[JobStatus], bool],
    start_job: Callable[[str, str, Any], Any],
    sync_world_runtime: Callable[[], None],
) -> None:
    target_position = resolve_defend_base_target_position(world_model, event)
    if target_position is None:
        return

    existing_jobs = active_task_jobs(
        jobs,
        task.task_id,
        expert_type="CombatExpert",
        is_terminal_status=is_terminal_status,
    )
    if existing_jobs:
        for controller in existing_jobs:
            current_target = getattr(controller.config, "target_position", None)
            if current_target != target_position:
                controller.patch({"target_position": target_position})
        sync_world_runtime()
        return

    start_job(
        task.task_id,
        "CombatExpert",
        CombatJobConfig(
            target_position=target_position,
            engagement_mode=EngagementMode.HOLD,
            max_chase_distance=12,
            retreat_threshold=0.4,
            unit_count=DEFAULT_GENERIC_COMBAT_UNIT_COUNT,
        ),
    )
