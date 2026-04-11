"""Kernel-side event and response delivery helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, MutableSequence
from typing import Any, Protocol, TYPE_CHECKING

from models import Event, EventType, JobStatus, PlayerResponse, TaskStatus

if TYPE_CHECKING:
    from world_model import WorldModel


class JobLike(Protocol):
    task_id: str
    status: JobStatus
    resources: list[str]


class RuntimeLike(Protocol):
    task: Any
    agent: Any


def deliver_event_to_job(controller: JobLike, event: Event) -> None:
    if hasattr(controller, "on_event"):
        controller.on_event(event)  # type: ignore[attr-defined]
    elif hasattr(controller, "handle_event"):
        controller.handle_event(event)  # type: ignore[attr-defined]


def route_actor_event(
    event: Event,
    *,
    jobs: Mapping[str, JobLike],
    task_runtimes: Mapping[str, RuntimeLike],
    world_model: WorldModel,
    is_terminal_job_status: Callable[[JobStatus], bool],
    rebalance_resources: Callable[[], None],
    sync_world_runtime: Callable[[], None],
) -> None:
    if event.actor_id is None:
        return
    resource_id = f"actor:{event.actor_id}"
    matched_jobs = [
        controller
        for controller in jobs.values()
        if resource_id in controller.resources and not is_terminal_job_status(controller.status)
    ]
    routed_task_ids: set[str] = set()
    for controller in matched_jobs:
        deliver_event_to_job(controller, event)
        runtime = task_runtimes.get(controller.task_id)
        if runtime is not None and controller.task_id not in routed_task_ids:
            runtime.agent.push_event(event)
            routed_task_ids.add(controller.task_id)

    if event.type == EventType.UNIT_DIED and matched_jobs:
        for controller in matched_jobs:
            if hasattr(controller, "on_resource_revoked"):
                controller.on_resource_revoked([resource_id])  # type: ignore[attr-defined]
            world_model.unbind_resource(resource_id)
        rebalance_resources()
    sync_world_runtime()


def broadcast_event(
    event: Event,
    *,
    task_runtimes: Mapping[str, RuntimeLike],
) -> None:
    terminal = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}
    for runtime in task_runtimes.values():
        if runtime.task.status in terminal:
            continue
        runtime.agent.push_event(event)


def append_player_notification(
    player_notifications: MutableSequence[dict[str, Any]],
    event: Event,
) -> None:
    content_map = {
        EventType.ENEMY_EXPANSION: "发现敌人在扩张",
        EventType.FRONTLINE_WEAK: "我方前线空虚",
        EventType.ECONOMY_SURPLUS: "经济充裕，可以考虑进攻",
    }
    player_notifications.append(
        {
            "type": event.type.value,
            "content": content_map.get(event.type, event.type.value),
            "data": dict(event.data),
            "timestamp": event.timestamp,
        }
    )


def deliver_player_response(
    delivered_player_responses: MutableMapping[str, list[PlayerResponse]],
    task_runtimes: Mapping[str, RuntimeLike],
    response: PlayerResponse,
) -> None:
    delivered_player_responses.setdefault(response.task_id, []).append(response)
    runtime = task_runtimes.get(response.task_id)
    if runtime is None:
        return
    if hasattr(runtime.agent, "push_player_response"):
        runtime.agent.push_player_response(response)
