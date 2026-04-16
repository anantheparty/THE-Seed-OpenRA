"""Helpers for kernel task lifecycle transitions."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, MutableSequence
from typing import Any

from benchmark import span as bm_span
from logging_system import get_logger
from models import JobStatus, Task, TaskMessage, TaskMessageType, TaskStatus

slog = get_logger("kernel")

_TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.ABORTED,
    TaskStatus.PARTIAL,
}
_TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.ABORTED,
}


def close_pending_questions_for_task(question_store: Any, task_id: str) -> None:
    question_store.close_for_task(task_id)


def task_matches_filters(task: Task, filters: dict[str, Any]) -> bool:
    task_ids = filters.get("task_ids")
    if task_ids and task.task_id not in set(task_ids):
        return False
    kind = filters.get("kind")
    if kind and task.kind.value != kind:
        return False
    priority_below = filters.get("priority_below")
    if priority_below is not None and task.priority >= int(priority_below):
        return False
    status = filters.get("status")
    if status and task.status.value != status:
        return False
    return True


def cancel_task(
    *,
    task_id: str,
    tasks: Mapping[str, Task],
    jobs: Mapping[str, Any],
    unit_requests: Mapping[str, Any],
    task_actor_groups: MutableMapping[str, list[int]],
    task_runtimes: MutableMapping[str, Any],
    question_store: Any,
    abort_job: Callable[[str], bool],
    release_task_job_resources: Callable[[str], None],
    cancel_unit_request: Callable[[str], bool],
    stop_task_runtime: Callable[[MutableMapping[str, Any], str], None],
    sync_world_runtime: Callable[[], None],
    now: Callable[[], float],
) -> bool:
    with bm_span("tool_exec", name="kernel:cancel_task"):
        task = tasks.get(task_id)
        if task is None:
            return False
        if task.status in _TERMINAL_TASK_STATUSES:
            return False
        task.status = TaskStatus.ABORTED
        task.timestamp = now()
        for job in list(jobs.values()):
            if job.task_id == task_id and job.status not in _TERMINAL_JOB_STATUSES:
                abort_job(job.job_id)
        release_task_job_resources(task_id)
        close_pending_questions_for_task(question_store, task_id)
        for req in list(unit_requests.values()):
            if req.task_id == task_id and req.status in ("pending", "partial"):
                cancel_unit_request(req.request_id)
        task_actor_groups.pop(task_id, None)
        stop_task_runtime(task_runtimes, task_id)
        sync_world_runtime()
        slog.info(
            "Task cancelled",
            event="task_cancelled",
            task_id=task_id,
            result="aborted",
            summary="任务已取消",
        )
        return True


def cancel_tasks(
    *,
    filters: dict[str, Any],
    tasks: Mapping[str, Task],
    cancel_task_fn: Callable[[str], bool],
) -> int:
    with bm_span("tool_exec", name="kernel:cancel_tasks"):
        count = 0
        for task in list(tasks.values()):
            if task_matches_filters(task, filters):
                count += int(cancel_task_fn(task.task_id))
        return count


def complete_task(
    *,
    task_id: str,
    result: str,
    summary: str,
    tasks: Mapping[str, Task],
    jobs: Mapping[str, Any],
    task_messages: MutableSequence[TaskMessage],
    task_actor_groups: MutableMapping[str, list[int]],
    task_runtimes: MutableMapping[str, Any],
    question_store: Any,
    abort_job: Callable[[str], bool],
    release_task_job_resources: Callable[[str], None],
    stop_task_runtime: Callable[[MutableMapping[str, Any], str], None],
    sync_world_runtime: Callable[[], None],
    now: Callable[[], float],
    gen_id: Callable[[str], str],
) -> bool:
    with bm_span("tool_exec", name="kernel:complete_task", metadata={"result": result}):
        task = tasks.get(task_id)
        if task is None:
            return False
        if result == "succeeded":
            task.status = TaskStatus.SUCCEEDED
        elif result == "failed":
            task.status = TaskStatus.FAILED
        elif result == "partial":
            task.status = TaskStatus.PARTIAL
        else:
            raise ValueError(f"Unsupported task result: {result}")
        task.timestamp = now()
        for job in list(jobs.values()):
            if job.task_id == task_id and job.status not in _TERMINAL_JOB_STATUSES:
                abort_job(job.job_id)
        release_task_job_resources(task_id)
        close_pending_questions_for_task(question_store, task_id)
        task_messages.append(
            TaskMessage(
                message_id=gen_id("msg_"),
                task_id=task_id,
                type=TaskMessageType.TASK_COMPLETE_REPORT,
                content=summary,
                priority=task.priority,
            )
        )
        task_actor_groups.pop(task_id, None)
        stop_task_runtime(task_runtimes, task_id)
        sync_world_runtime()
        slog.info(
            "Task completed",
            event="task_completed",
            task_id=task_id,
            result=result,
            summary=summary,
        )
        return True
