"""Shared runtime task triage helpers.

This module keeps task state synthesis aligned between the dashboard/runtime
bridge and Adjutant coordinator context. Both surfaces need the same answer to
"what is this task doing right now?" or they drift and start telling different
stories to the player.
"""

from __future__ import annotations

from typing import Any, Optional


_TERMINAL_STATUS_LINE = {
    "succeeded": "任务已完成",
    "failed": "任务已失败",
    "aborted": "任务已终止",
    "partial": "任务已部分完成",
}

_CAPABILITY_PHASE_TEXT = {
    "dispatch": "分发请求中",
    "bootstrapping": "补前置中",
    "fulfilling": "交付单位中",
    "executing": "执行中",
    "idle": "待机",
}


def capability_blocker_status_text(capability_status: dict[str, Any]) -> str:
    """Return a short human-readable blocker string for capability status."""
    blocker = str(capability_status.get("blocker", "") or "")
    if blocker == "request_inference_pending":
        count = int(capability_status.get("inference_pending_count", 0) or 0)
        return f"等待解析请求 ({count})" if count else "等待解析请求"
    if blocker == "missing_prerequisite":
        count = int(capability_status.get("prerequisite_gap_count", 0) or 0)
        return f"缺少前置建筑 ({count})" if count else "缺少前置建筑"
    if blocker == "pending_requests_waiting_dispatch":
        count = int(capability_status.get("dispatch_request_count", 0) or 0)
        return f"请求待分发 ({count})" if count else "请求待分发"
    if blocker == "bootstrap_in_progress":
        count = int(capability_status.get("bootstrapping_request_count", 0) or 0)
        return f"前置生产中 ({count})" if count else "前置生产中"
    return blocker


def build_task_triage(
    *,
    task: Any,
    runtime_task: Optional[dict[str, Any]],
    runtime_state: dict[str, Any],
    world_sync: Optional[dict[str, Any]] = None,
    pending_question: Optional[dict[str, Any]] = None,
    latest_warning: Optional[str] = None,
    primary_summary: str = "",
) -> dict[str, Any]:
    """Build a shared task triage payload from runtime state.

    `main.py` and `Adjutant` have different local concerns, but the runtime
    state transition logic itself should stay identical.
    """
    task_id = str(getattr(task, "task_id", ""))
    status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or "")
    runtime_task = dict(runtime_task or {})
    runtime_state = dict(runtime_state or {})
    world_sync = dict(world_sync or {})

    active_group_size = int(
        runtime_task.get("active_group_size", getattr(task, "active_group_size", 0)) or 0
    )
    is_capability = bool(runtime_task.get("is_capability", getattr(task, "is_capability", False)))

    active_jobs = [
        info
        for info in (runtime_state.get("active_jobs") or {}).values()
        if isinstance(info, dict) and str(info.get("task_id", "")) == task_id
    ]
    waiting_jobs = [job for job in active_jobs if str(job.get("status", "")) == "waiting"]
    running_jobs = [job for job in active_jobs if str(job.get("status", "")) == "running"]
    primary_job = running_jobs[0] if running_jobs else waiting_jobs[0] if waiting_jobs else active_jobs[0] if active_jobs else {}
    active_expert = str(primary_job.get("expert_type", "") or "")
    active_job_id = str(primary_job.get("job_id", "") or "")

    reservations = [
        reservation
        for reservation in (runtime_state.get("unit_reservations") or [])
        if isinstance(reservation, dict) and str(reservation.get("task_id", "")) == task_id
    ]
    reservation_ids = [
        str(reservation.get("reservation_id", ""))
        for reservation in reservations
        if reservation.get("reservation_id")
    ]

    capability_status = {}
    raw_capability_status = runtime_state.get("capability_status")
    if isinstance(raw_capability_status, dict):
        capability_task_id = str(
            raw_capability_status.get("task_id")
            or raw_capability_status.get("taskId")
            or ""
        )
        capability_label = str(raw_capability_status.get("label") or raw_capability_status.get("task_label") or "")
        task_label = str(getattr(task, "label", "") or runtime_task.get("label", "") or "")
        if is_capability and (
            not capability_task_id
            or capability_task_id == task_id
            or (task_label and capability_label and capability_label == task_label)
        ):
            capability_status = dict(raw_capability_status)

    if status in _TERMINAL_STATUS_LINE:
        return {
            "state": "completed",
            "phase": status,
            "status_line": _TERMINAL_STATUS_LINE[status],
            "waiting_reason": "",
            "blocking_reason": "",
            "active_expert": "",
            "active_job_id": "",
            "reservation_ids": reservation_ids,
            "world_stale": bool(world_sync.get("stale")),
            "active_group_size": active_group_size,
        }

    if bool(world_sync.get("stale")):
        return {
            "state": "degraded",
            "phase": "world_sync",
            "status_line": "世界状态同步异常，等待恢复",
            "waiting_reason": "world_stale",
            "blocking_reason": "world_stale",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": True,
            "active_group_size": active_group_size,
        }

    if pending_question is not None:
        question = str(pending_question.get("question", "")).strip()
        question = question[:36] + "..." if len(question) > 36 else question
        return {
            "state": "waiting_player",
            "phase": "question",
            "status_line": f"等待玩家回复：{question}" if question else "等待玩家回复",
            "waiting_reason": "player_response",
            "blocking_reason": "",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if latest_warning:
        return {
            "state": "blocked",
            "phase": "warning",
            "status_line": latest_warning,
            "waiting_reason": "",
            "blocking_reason": "task_warning",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if is_capability:
        pending_request_count = int(capability_status.get("pending_request_count", 0) or 0)
        blocking_request_count = int(capability_status.get("blocking_request_count", 0) or 0)
        start_released_request_count = int(capability_status.get("start_released_request_count", 0) or 0)
        reinforcement_request_count = int(capability_status.get("reinforcement_request_count", 0) or 0)
        active_job_types = list(capability_status.get("active_job_types", []) or [])
        phase = str(capability_status.get("phase", "") or ("dispatch" if active_job_types else "idle"))
        blocker = str(capability_status.get("blocker", "") or "")
        waiting_reason = blocker or (
            "start_package_released"
            if start_released_request_count
            else "reinforcement"
            if reinforcement_request_count
            else "pending_requests"
            if pending_request_count
            else ""
        )

        status_line = f"能力处理中：{_CAPABILITY_PHASE_TEXT.get(phase, phase or '进行中')}"
        if active_job_types:
            status_line += f" | {','.join(active_job_types[:3])}"
        if pending_request_count:
            status_line += f" | pending={pending_request_count}"
        if blocking_request_count:
            status_line += f" | blocking={blocking_request_count}"
        if start_released_request_count:
            status_line += f" | ready={start_released_request_count}"
        if reinforcement_request_count:
            status_line += f" | reinforce={reinforcement_request_count}"
        if blocker:
            status_line += f" | blocker={capability_blocker_status_text(capability_status)}"

        return {
            "state": (
                "running"
                if phase in {"bootstrapping", "dispatch", "fulfilling", "executing"}
                or active_job_types
                or pending_request_count
                or start_released_request_count
                or reinforcement_request_count
                else "idle"
            ),
            "phase": phase,
            "status_line": status_line,
            "waiting_reason": waiting_reason,
            "blocking_reason": blocker,
            "active_expert": ",".join(active_job_types[:3]) if active_job_types else active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if reservations:
        first = reservations[0]
        unit_name = str(first.get("unit_type") or first.get("hint") or first.get("category") or "单位")
        remaining = max(int(first.get("remaining_count", 0) or 0), 0)
        status_line = (
            f"等待能力模块交付单位：{unit_name} × {remaining}"
            if remaining > 0
            else f"等待能力模块交付单位：{unit_name}"
        )
        return {
            "state": "waiting_units",
            "phase": "reservation",
            "status_line": status_line,
            "waiting_reason": "unit_reservation",
            "blocking_reason": "",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if waiting_jobs:
        status_line = primary_summary or f"等待执行条件满足：{active_expert or '任务'}"
        return {
            "state": "waiting",
            "phase": "job_waiting",
            "status_line": status_line,
            "waiting_reason": "job_waiting",
            "blocking_reason": "",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if running_jobs:
        status_line = primary_summary or f"运行中：{active_expert or '任务'}"
        return {
            "state": "running",
            "phase": "job_running",
            "status_line": status_line,
            "waiting_reason": "",
            "blocking_reason": "",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    if active_group_size > 0:
        return {
            "state": "running",
            "phase": "task_active",
            "status_line": f"执行中 | group={active_group_size}",
            "waiting_reason": "",
            "blocking_reason": "",
            "active_expert": active_expert,
            "active_job_id": active_job_id,
            "reservation_ids": reservation_ids,
            "world_stale": False,
            "active_group_size": active_group_size,
        }

    return {
        "state": "idle",
        "phase": "task_active",
        "status_line": "等待调度",
        "waiting_reason": "scheduler",
        "blocking_reason": "",
        "active_expert": active_expert,
        "active_job_id": active_job_id,
        "reservation_ids": reservation_ids,
        "world_stale": False,
        "active_group_size": active_group_size,
    }
