"""Shared runtime projection builders for kernel-exported state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Optional, Protocol

from models import JobStatus, Task, TaskStatus, UnitRequest
from runtime_views import CapabilityStatusSnapshot


class ControllerLike(Protocol):
    job_id: str
    task_id: str
    expert_type: str

    def to_model(self) -> Any:
        ...


def build_job_stats_by_task(controllers: Iterable[ControllerLike]) -> dict[str, Any]:
    """Build per-task expert attempt/failure counters for runtime facts."""
    job_stats: dict[str, Any] = {}
    for controller in controllers:
        task_id = controller.task_id
        expert_type = controller.expert_type
        status = controller.to_model().status
        stats = job_stats.setdefault(task_id, {"failed_count": 0, "expert_attempts": {}})
        stats["expert_attempts"][expert_type] = stats["expert_attempts"].get(expert_type, 0) + 1
        if status == JobStatus.FAILED:
            stats["failed_count"] += 1
    return job_stats


def build_active_tasks_projection(
    *,
    tasks: Iterable[Task],
    active_actor_ids_for: Callable[[str], list[int]],
) -> dict[str, dict[str, Any]]:
    """Build active-task runtime rows exported to world/runtime surfaces."""
    projection: dict[str, dict[str, Any]] = {}
    terminal = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}
    for task in tasks:
        if task.status in terminal:
            continue
        active_actor_ids = active_actor_ids_for(task.task_id)
        projection[task.task_id] = {
            "raw_text": task.raw_text,
            "label": task.label,
            "kind": task.kind.value,
            "priority": task.priority,
            "status": task.status.value,
            "is_capability": bool(getattr(task, "is_capability", False)),
            "active_actor_ids": active_actor_ids,
            "active_group_size": len(active_actor_ids),
        }
    return projection


def build_active_jobs_projection(controllers: Iterable[ControllerLike]) -> dict[str, dict[str, Any]]:
    """Build active-job runtime rows exported to world/runtime surfaces."""
    projection: dict[str, dict[str, Any]] = {}
    terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}
    for controller in controllers:
        status = controller.to_model().status
        if status in terminal:
            continue
        projection[controller.job_id] = {
            "task_id": controller.task_id,
            "expert_type": controller.expert_type,
            "status": status.value,
        }
    return projection


def build_capability_status_snapshot(
    *,
    capability_task: Optional[Task],
    capability_jobs: Iterable[Any],
    capability_requests: Iterable[UnitRequest],
    unfulfilled_requests: Iterable[dict[str, Any]],
    recent_directives: Iterable[str],
) -> CapabilityStatusSnapshot:
    """Build a normalized runtime snapshot for the active capability task."""
    if capability_task is None or capability_task.status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.ABORTED,
        TaskStatus.PARTIAL,
    }:
        return CapabilityStatusSnapshot()

    active_jobs = [
        controller
        for controller in capability_jobs
        if controller.to_model().status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}
    ]
    requests = [req for req in capability_requests if req.status in ("pending", "partial")]
    blocking_request_count = sum(1 for req in requests if req.blocking)
    dispatch_request_count = sum(1 for req in requests if not req.bootstrap_job_id and not req.start_released)
    bootstrap_wait_request_count = sum(
        1 for req in requests if req.bootstrap_job_id and not req.start_released
    )
    start_released_request_count = sum(1 for req in requests if req.start_released)
    reinforcement_request_count = sum(1 for req in requests if not req.blocking)
    inference_pending_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "inference_pending"
    )
    prerequisite_gap_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "missing_prerequisite"
    )
    world_sync_stale_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "world_sync_stale"
    )
    deploy_required_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "deploy_required"
    )
    disabled_prerequisite_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "disabled_prerequisite"
    )
    low_power_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "low_power"
    )
    producer_disabled_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "producer_disabled"
    )
    queue_blocked_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "queue_blocked"
    )
    insufficient_funds_count = sum(
        1 for item in unfulfilled_requests
        if isinstance(item, dict) and item.get("reason") == "insufficient_funds"
    )

    if dispatch_request_count:
        phase = "dispatch"
    elif bootstrap_wait_request_count:
        phase = "bootstrapping"
    elif start_released_request_count or reinforcement_request_count:
        phase = "fulfilling"
    elif active_jobs:
        phase = "executing"
    else:
        phase = "idle"

    blocker = ""
    if world_sync_stale_count:
        blocker = "world_sync_stale"
    elif inference_pending_count:
        blocker = "request_inference_pending"
    elif deploy_required_count:
        blocker = "deploy_required"
    elif prerequisite_gap_count:
        blocker = "missing_prerequisite"
    elif disabled_prerequisite_count:
        blocker = "disabled_prerequisite"
    elif low_power_count:
        blocker = "low_power"
    elif producer_disabled_count:
        blocker = "producer_disabled"
    elif queue_blocked_count:
        blocker = "queue_blocked"
    elif insufficient_funds_count:
        blocker = "insufficient_funds"
    elif dispatch_request_count:
        blocker = "pending_requests_waiting_dispatch"
    elif bootstrap_wait_request_count:
        blocker = "bootstrap_in_progress"

    return CapabilityStatusSnapshot(
        task_id=capability_task.task_id,
        task_label=capability_task.label,
        status=capability_task.status.value,
        phase=phase,
        blocker=blocker,
        active_job_count=len(active_jobs),
        active_job_types=[controller.expert_type for controller in active_jobs],
        pending_request_count=len(requests),
        blocking_request_count=blocking_request_count,
        dispatch_request_count=dispatch_request_count,
        bootstrapping_request_count=bootstrap_wait_request_count,
        start_released_request_count=start_released_request_count,
        reinforcement_request_count=reinforcement_request_count,
        inference_pending_count=inference_pending_count,
        prerequisite_gap_count=prerequisite_gap_count,
        world_sync_stale_count=world_sync_stale_count,
        deploy_required_count=deploy_required_count,
        disabled_prerequisite_count=disabled_prerequisite_count,
        low_power_count=low_power_count,
        producer_disabled_count=producer_disabled_count,
        queue_blocked_count=queue_blocked_count,
        insufficient_funds_count=insufficient_funds_count,
        recent_directives=[str(text) for text in recent_directives if str(text or "")],
    )


def build_world_runtime_state(
    *,
    tasks: Iterable[Task],
    controllers: Iterable[Any],
    constraints: Iterable[Any],
    resource_bindings: dict[str, str],
    active_actor_ids_for: Callable[[str], list[int]],
    unit_requests: Iterable[UnitRequest],
    reservation_for_request: Callable[[UnitRequest], Any],
    request_reservation_id: Callable[[str], str],
    production_readiness_for: Callable[[str, str | None], dict[str, Any]],
    capability_task: Optional[Task],
    capability_task_id: Optional[str],
    capability_recent_inputs: Iterable[dict[str, Any]],
    unit_reservations: Iterable[Any],
    build_unfulfilled_request_payloads: Callable[..., list[dict[str, Any]]],
    build_active_reservation_payloads: Callable[..., list[dict[str, Any]]],
    requests_by_id: dict[str, UnitRequest],
) -> dict[str, Any]:
    """Build the aggregate runtime payload exported into the world model."""
    controllers_list = list(controllers)
    unfulfilled = build_unfulfilled_request_payloads(
        unit_requests,
        reservation_for_request=reservation_for_request,
        request_reservation_id=request_reservation_id,
        production_readiness_for=production_readiness_for,
    )

    capability_status = CapabilityStatusSnapshot()
    if capability_task_id:
        capability_status = build_capability_status_snapshot(
            capability_task=capability_task,
            capability_jobs=(
                controller
                for controller in controllers_list
                if capability_task is not None and controller.task_id == capability_task.task_id
            ),
            capability_requests=unit_requests,
            unfulfilled_requests=unfulfilled,
            recent_directives=[
                item.get("text", "")
                for item in capability_recent_inputs
                if item.get("text")
            ],
        )

    active_reservations = build_active_reservation_payloads(
        unit_reservations,
        requests_by_id=requests_by_id,
        production_readiness_for=production_readiness_for,
    )

    return {
        "active_tasks": build_active_tasks_projection(
            tasks=tasks,
            active_actor_ids_for=active_actor_ids_for,
        ),
        "active_jobs": build_active_jobs_projection(controllers_list),
        "resource_bindings": dict(resource_bindings),
        "constraints": list(constraints),
        "job_stats_by_task": build_job_stats_by_task(controllers_list),
        "unfulfilled_requests": unfulfilled,
        "capability_status": capability_status.to_dict(),
        "unit_reservations": active_reservations,
    }
