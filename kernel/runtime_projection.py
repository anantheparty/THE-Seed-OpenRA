"""Shared runtime projection builders for kernel-exported state."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from models import JobStatus, Task, TaskStatus, UnitRequest
from runtime_views import CapabilityStatusSnapshot


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
    if inference_pending_count:
        blocker = "request_inference_pending"
    elif prerequisite_gap_count:
        blocker = "missing_prerequisite"
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
        recent_directives=[str(text) for text in recent_directives if str(text or "")],
    )
