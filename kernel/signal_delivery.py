"""Kernel-side expert signal delivery helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from models import ExpertSignal, SignalKind, TaskMessage, TaskMessageType, TaskStatus


class RuntimeLike(Protocol):
    agent: Any


def _task_message_from_signal(
    signal: ExpertSignal,
    *,
    priority: int,
    gen_message_id: Callable[[str], str],
) -> TaskMessage | None:
    data = dict(signal.data or {})
    if not bool(data.get("explicit_group")) or str(data.get("source_expert") or "") != "MovementExpert":
        return None
    if signal.kind == SignalKind.RESOURCE_LOST:
        message_type = TaskMessageType.TASK_WARNING
    elif signal.kind == SignalKind.RISK_ALERT:
        message_type = TaskMessageType.TASK_WARNING
    elif signal.kind == SignalKind.PROGRESS:
        if int(data.get("missing_count", 0) or 0) <= 0:
            return None
        message_type = TaskMessageType.TASK_INFO
    else:
        return None
    return TaskMessage(
        message_id=gen_message_id("msg_"),
        task_id=signal.task_id,
        type=message_type,
        content=signal.summary,
        priority=priority,
    )


def route_expert_signal(
    signal: ExpertSignal,
    *,
    tasks: Mapping[str, Any],
    task_runtimes: Mapping[str, RuntimeLike],
    is_direct_managed: Callable[[str], bool],
    register_task_message: Callable[[TaskMessage], bool],
    complete_task: Callable[[str, str, str], bool],
    gen_message_id: Callable[[str], str],
) -> bool:
    task = tasks.get(signal.task_id)
    if task is None or task.status in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.ABORTED,
        TaskStatus.PARTIAL,
    }:
        return False
    runtime = task_runtimes.get(signal.task_id)
    if runtime is None:
        return False

    if signal.kind == SignalKind.BLOCKED:
        register_task_message(
            TaskMessage(
                message_id=gen_message_id("msg_"),
                task_id=signal.task_id,
                type=TaskMessageType.TASK_WARNING,
                content=signal.summary,
                priority=task.priority,
            )
        )
    else:
        mirrored = _task_message_from_signal(signal, priority=task.priority, gen_message_id=gen_message_id)
        if mirrored is not None:
            register_task_message(mirrored)

    if signal.kind == SignalKind.TASK_COMPLETE and is_direct_managed(signal.task_id):
        result_map = {"succeeded": "succeeded", "failed": "failed", "aborted": "failed"}
        result = result_map.get(signal.result, "succeeded")
        complete_task(signal.task_id, result, signal.summary or "direct job completed")
        return True

    runtime.agent.push_signal(signal)
    return True
