"""Helpers for kernel resource matching, claim, preemption, and loss signaling."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableSet
from typing import Any, Optional, Protocol

from logging_system import get_logger
from models import JobStatus, ResourceKind, ResourceNeed, SignalKind, Task, TaskStatus

slog = get_logger("kernel")


class ControllerLike(Protocol):
    job_id: str
    task_id: str
    status: JobStatus
    resources: list[str]

    def abort(self) -> None:
        ...

    def on_resource_granted(self, resources: list[str]) -> None:
        ...


def actor_matches_need(actor: Any, need: ResourceNeed) -> bool:
    predicates = need.predicates
    actor_category = getattr(actor.category, "value", actor.category)
    actor_mobility = getattr(actor.mobility, "value", actor.mobility)
    explicitly_requests_static_actor = (
        predicates.get("category") == "building" or predicates.get("mobility") == "static"
    )

    if not explicitly_requests_static_actor and (
        actor_category == "building" or actor_mobility == "static"
    ):
        return False

    for key, value in predicates.items():
        if key == "owner" and getattr(actor.owner, "value", actor.owner) != value:
            return False
        if key == "category" and actor_category != value:
            return False
        if key == "mobility" and actor_mobility != value:
            return False
        if key == "can_attack" and bool(actor.can_attack) != (str(value).lower() == "true"):
            return False
        if key == "can_harvest" and bool(actor.can_harvest) != (str(value).lower() == "true"):
            return False
        if key == "name" and actor.name != value:
            return False
        if key == "actor_id" and str(actor.actor_id) != str(value):
            return False
    return True


def resource_matches_need(
    resource_id: str,
    need: ResourceNeed,
    *,
    actors_by_id: Mapping[int, Any],
) -> bool:
    if need.kind == ResourceKind.ACTOR:
        if not resource_id.startswith("actor:"):
            return False
        actor_id = int(resource_id.split(":", 1)[1])
        actor = actors_by_id.get(actor_id)
        if actor is None:
            return False
        return actor_matches_need(actor, need)
    if not resource_id.startswith("queue:"):
        return False
    queue_type = resource_id.split(":", 1)[1]
    return need.predicates.get("queue_type") == queue_type


def resources_for_need(
    controller: ControllerLike,
    need: ResourceNeed,
    *,
    actors_by_id: Mapping[int, Any],
) -> list[str]:
    return [
        resource_id
        for resource_id in controller.resources
        if resource_matches_need(resource_id, need, actors_by_id=actors_by_id)
    ]


def find_unbound_resource(
    need: ResourceNeed,
    *,
    world_model: Any,
) -> Optional[str]:
    if need.kind == ResourceKind.ACTOR:
        actors = world_model.find_actors(owner="self", idle_only=True, unbound_only=True)
        for actor in actors:
            if actor_matches_need(actor, need):
                return f"actor:{actor.actor_id}"
        return None
    queue_type = need.predicates.get("queue_type")
    if queue_type is None:
        return None
    resource_id = f"queue:{queue_type}"
    if resource_id in world_model.resource_bindings:
        return None
    queues = world_model.query("production_queues")
    if queue_type in queues:
        return resource_id
    return None


def find_preemptable_resource(
    requester: ControllerLike,
    need: ResourceNeed,
    *,
    tasks: Mapping[str, Task],
    jobs: Mapping[str, ControllerLike],
    world_model: Any,
) -> Optional[dict[str, Any]]:
    requester_priority = tasks[requester.task_id].priority
    candidates: list[tuple[int, str, ControllerLike]] = []
    if need.kind == ResourceKind.ACTOR:
        actors = world_model.find_actors(owner="self", idle_only=False, unbound_only=False)
        for actor in actors:
            if not actor_matches_need(actor, need):
                continue
            resource_id = f"actor:{actor.actor_id}"
            holder_job_id = world_model.resource_bindings.get(resource_id)
            if holder_job_id is None or holder_job_id == requester.job_id:
                continue
            holder = jobs.get(holder_job_id)
            if holder is None:
                continue
            holder_priority = tasks[holder.task_id].priority
            if holder_priority >= requester_priority:
                continue
            candidates.append((holder_priority, resource_id, holder))
    else:
        queue_type = need.predicates.get("queue_type")
        if queue_type is None:
            return None
        resource_id = f"queue:{queue_type}"
        holder_job_id = world_model.resource_bindings.get(resource_id)
        if holder_job_id is None or holder_job_id == requester.job_id:
            return None
        holder = jobs.get(holder_job_id)
        if holder is None:
            return None
        holder_priority = tasks[holder.task_id].priority
        if holder_priority >= requester_priority:
            return None
        candidates.append((holder_priority, resource_id, holder))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[2].job_id))
    _, resource_id, holder = candidates[0]
    return {"resource_id": resource_id, "holder": holder}


def preempt_resource(
    holder: ControllerLike,
    resource_id: str,
    *,
    release_job_resources: Callable[[ControllerLike], None],
    unbind_resource: Callable[[str], None],
) -> None:
    slog.warn(
        "Kernel preempting resource",
        event="resource_preempted",
        holder_job_id=holder.job_id,
        holder_task_id=holder.task_id,
        resource_id=resource_id,
    )
    if len(holder.resources) <= 1:
        holder.abort()
        release_job_resources(holder)
        return
    if hasattr(holder, "on_resource_revoked"):
        holder.on_resource_revoked([resource_id])
    elif resource_id in holder.resources:
        holder.resources.remove(resource_id)
    unbind_resource(resource_id)


def grant_resource(
    controller: ControllerLike,
    resource_id: str,
    *,
    bind_resource: Callable[[str, str], None],
    set_task_actor_group: Callable[[str, list[int]], None],
) -> None:
    bind_resource(resource_id, controller.job_id)
    controller.on_resource_granted([resource_id])
    if resource_id.startswith("actor:"):
        try:
            actor_id = int(resource_id.split(":", 1)[1])
        except (TypeError, ValueError):
            actor_id = None
        if actor_id is not None:
            set_task_actor_group(controller.task_id, [actor_id])
    slog.info(
        "Kernel granted resource",
        event="resource_granted",
        job_id=controller.job_id,
        task_id=controller.task_id,
        resource_id=resource_id,
    )


def claim_resource(
    controller: ControllerLike,
    need: ResourceNeed,
    *,
    world_model: Any,
    tasks: Mapping[str, Task],
    jobs: Mapping[str, ControllerLike],
    release_job_resources: Callable[[ControllerLike], None],
    set_task_actor_group: Callable[[str, list[int]], None],
) -> Optional[str]:
    unbound = find_unbound_resource(need, world_model=world_model)
    if unbound is not None:
        grant_resource(
            controller,
            unbound,
            bind_resource=world_model.bind_resource,
            set_task_actor_group=set_task_actor_group,
        )
        return unbound

    preemptable = find_preemptable_resource(
        controller,
        need,
        tasks=tasks,
        jobs=jobs,
        world_model=world_model,
    )
    if preemptable is None:
        return None
    preempt_resource(
        preemptable["holder"],
        preemptable["resource_id"],
        release_job_resources=release_job_resources,
        unbind_resource=world_model.unbind_resource,
    )
    grant_resource(
        controller,
        preemptable["resource_id"],
        bind_resource=world_model.bind_resource,
        set_task_actor_group=set_task_actor_group,
    )
    return preemptable["resource_id"]


def notify_resource_loss(
    controller: Any,
    need: ResourceNeed,
    missing: int,
    *,
    resource_loss_notified: MutableSet[str],
) -> None:
    if controller.job_id in resource_loss_notified:
        return
    if not hasattr(controller, "emit_signal"):
        return
    summary = f"Missing {missing} {need.kind.value} resource(s); waiting for replacement"
    controller.emit_signal(  # type: ignore[attr-defined]
        kind=SignalKind.RESOURCE_LOST,
        summary=summary,
        decision={
            "options": ["wait_for_production", "use_alternative", "abort"],
            "default_if_timeout": "wait_for_production",
            "deadline_s": 3.0,
        },
    )
    resource_loss_notified.add(controller.job_id)


def rebalance_resources(
    *,
    jobs: Mapping[str, ControllerLike],
    tasks: Mapping[str, Task],
    resource_needs: Mapping[str, list[ResourceNeed]],
    world_model: Any,
    is_terminal_status: Callable[[JobStatus], bool],
    release_job_resources: Callable[[ControllerLike], None],
    set_task_actor_group: Callable[[str, list[int]], None],
    resource_loss_notified: MutableSet[str],
    sync_world_runtime: Callable[[], None],
) -> None:
    requests: list[tuple[int, float, ControllerLike, ResourceNeed, int]] = []
    for controller in jobs.values():
        if is_terminal_status(controller.status):
            continue
        task = tasks.get(controller.task_id)
        if task is None or task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.ABORTED,
            TaskStatus.PARTIAL,
        }:
            continue
        for need in resource_needs.get(controller.job_id, []):
            current = resources_for_need(
                controller,
                need,
                actors_by_id=world_model.state.actors,
            )
            missing = max(0, need.count - len(current))
            if missing > 0:
                requests.append((task.priority, task.created_at, controller, need, missing))

    requests.sort(key=lambda item: (-item[0], item[1], item[2].job_id))

    for _, _, controller, need, missing in requests:
        while missing > 0:
            claimed = claim_resource(
                controller,
                need,
                world_model=world_model,
                tasks=tasks,
                jobs=jobs,
                release_job_resources=release_job_resources,
                set_task_actor_group=set_task_actor_group,
            )
            if claimed is None:
                break
            missing -= 1

        remaining = max(
            0,
            need.count - len(resources_for_need(controller, need, actors_by_id=world_model.state.actors)),
        )
        if remaining > 0:
            if not controller.resources and not is_terminal_status(controller.status):
                controller.status = JobStatus.WAITING
            notify_resource_loss(
                controller,
                need,
                remaining,
                resource_loss_notified=resource_loss_notified,
            )
        else:
            resource_loss_notified.discard(controller.job_id)

    sync_world_runtime()
