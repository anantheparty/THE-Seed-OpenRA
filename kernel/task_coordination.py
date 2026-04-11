"""Kernel-side task coordination helpers for agent-facing runtime views."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TYPE_CHECKING

from models import JobStatus, Task, TaskMessage, TaskMessageType
from task_agent import WorldSummary

if TYPE_CHECKING:
    from world_model import WorldModel


class JobLike(Protocol):
    task_id: str
    expert_type: str
    status: JobStatus
    config: Any


def set_task_actor_group(
    task_actor_groups: dict[str, set[int]],
    *,
    world_model: WorldModel,
    task_id: str,
    actor_ids: list[int],
) -> None:
    if not actor_ids:
        return
    group = task_actor_groups.setdefault(task_id, set())
    group.update(int(actor_id) for actor_id in actor_ids)
    prune_task_actor_group(task_actor_groups, world_model=world_model, task_id=task_id)


def prune_task_actor_group(
    task_actor_groups: dict[str, set[int]],
    world_model: WorldModel,
    *,
    task_id: str,
) -> None:
    group = task_actor_groups.get(task_id)
    if not group:
        task_actor_groups.pop(task_id, None)
        return
    alive_actor_ids = {
        actor.actor_id
        for actor in world_model.state.actors.values()
        if actor.owner.value == "self" and actor.is_alive
    }
    group.intersection_update(alive_actor_ids)
    if not group:
        task_actor_groups.pop(task_id, None)


def task_active_actor_ids(
    task_actor_groups: dict[str, set[int]],
    *,
    world_model: WorldModel,
    task_id: str,
) -> list[int]:
    prune_task_actor_group(task_actor_groups, world_model=world_model, task_id=task_id)
    group = task_actor_groups.get(task_id, set())
    return sorted(group)


def task_has_running_actor_job(
    jobs: Mapping[str, JobLike],
    *,
    task_id: str,
) -> bool:
    actor_job_types = {"MovementExpert", "ReconExpert", "CombatExpert"}
    for controller in jobs.values():
        if controller.task_id != task_id:
            continue
        if controller.expert_type not in actor_job_types:
            continue
        if controller.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
            continue
        return True
    return False


def build_task_world_summary(
    world_model: WorldModel,
    *,
    now: Callable[[], float],
) -> WorldSummary:
    summary = world_model.world_summary()
    return WorldSummary(
        economy=summary.get("economy", {}),
        military=summary.get("military", {}),
        map=summary.get("map", {}),
        known_enemy=summary.get("known_enemy", {}),
        timestamp=summary.get("timestamp", now()),
    )


def build_other_active_tasks(
    task_id: str,
    *,
    tasks: Mapping[str, Task],
    jobs: Mapping[str, JobLike],
    task_messages: Sequence[TaskMessage],
    is_terminal_job_status: Callable[[JobStatus], bool],
) -> list[dict[str, Any]]:
    """Return sibling tasks (active + recently completed), excluding self."""
    terminal = {"succeeded", "failed", "aborted", "partial"}
    result = []
    for task in tasks.values():
        if task.task_id == task_id or task.status.value in terminal:
            continue
        entry: dict[str, Any] = {
            "label": task.label,
            "raw_text": task.raw_text,
            "status": task.status.value,
        }
        jobs_summary = []
        for controller in jobs.values():
            if controller.task_id != task.task_id or is_terminal_job_status(controller.status):
                continue
            job_info: dict[str, str] = {"expert": controller.expert_type}
            cfg = controller.config
            if hasattr(cfg, "unit_type"):
                job_info["unit"] = cfg.unit_type
            if hasattr(cfg, "queue_type"):
                job_info["queue"] = cfg.queue_type
            if hasattr(cfg, "count"):
                job_info["count"] = str(cfg.count)
            if hasattr(cfg, "search_region"):
                job_info["region"] = cfg.search_region
            jobs_summary.append(job_info)
        if jobs_summary:
            entry["jobs"] = jobs_summary
        result.append(entry)

    report_types = {TaskMessageType.TASK_INFO, TaskMessageType.TASK_COMPLETE_REPORT}
    recent_reports = []
    for message in reversed(task_messages):
        if message.task_id == task_id:
            continue
        if message.type not in report_types:
            continue
        task_label = ""
        task = tasks.get(message.task_id)
        if task:
            task_label = task.label
        recent_reports.append(
            {
                "task_label": task_label,
                "type": message.type.value,
                "content": message.content[:120],
            }
        )
        if len(recent_reports) >= 8:
            break
    if recent_reports:
        result.append({"_recent_reports": list(reversed(recent_reports))})
    return result
