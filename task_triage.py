"""Shared runtime task triage helpers.

This module keeps task state synthesis aligned between the dashboard/runtime
bridge and Adjutant coordinator context. Both surfaces need the same answer to
"what is this task doing right now?" or they drift and start telling different
stories to the player.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Optional

from models.enums import TaskMessageType
from runtime_views import CapabilityStatusSnapshot, TaskTriageInputs, TaskTriageSnapshot


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


def capability_phase_status_text(
    capability_status: CapabilityStatusSnapshot | dict[str, Any],
    *,
    prefix: str = "",
) -> str:
    """Return a short phase string for capability status."""
    snapshot = CapabilityStatusSnapshot.from_mapping(capability_status)
    phase = str(snapshot.phase or "")
    text = _CAPABILITY_PHASE_TEXT.get(phase, phase)
    if not text:
        return ""
    return f"{prefix}{text}" if prefix else text


def capability_blocker_status_text(capability_status: CapabilityStatusSnapshot | dict[str, Any]) -> str:
    """Return a short human-readable blocker string for capability status."""
    snapshot = CapabilityStatusSnapshot.from_mapping(capability_status)
    blocker = snapshot.blocker
    if blocker == "request_inference_pending":
        count = snapshot.inference_pending_count
        return f"等待解析请求 ({count})" if count else "等待解析请求"
    if blocker == "missing_prerequisite":
        count = snapshot.prerequisite_gap_count
        return f"缺少前置建筑 ({count})" if count else "缺少前置建筑"
    if blocker == "pending_requests_waiting_dispatch":
        count = snapshot.dispatch_request_count
        return f"请求待分发 ({count})" if count else "请求待分发"
    if blocker == "bootstrap_in_progress":
        count = snapshot.bootstrapping_request_count
        return f"前置生产中 ({count})" if count else "前置生产中"
    return blocker


def capability_coordinator_alert(
    capability_status: CapabilityStatusSnapshot | dict[str, Any],
) -> Optional[dict[str, str]]:
    """Return the top-level coordinator alert implied by capability state."""
    snapshot = CapabilityStatusSnapshot.from_mapping(capability_status)
    blocker = snapshot.blocker
    task_label = snapshot.task_label
    if blocker == "missing_prerequisite":
        return {
            "code": "capability_missing_prerequisite",
            "severity": "warning",
            "text": f"能力层存在 {snapshot.prerequisite_gap_count} 个前置缺口",
            "target_label": task_label,
        }
    if blocker == "pending_requests_waiting_dispatch":
        return {
            "code": "capability_pending_dispatch",
            "severity": "info",
            "text": f"能力层仍有 {snapshot.pending_request_count} 个请求待分发",
            "target_label": task_label,
        }
    return None


def _job_status_value(job: Any) -> str:
    return str(getattr(getattr(job, "status", None), "value", getattr(job, "status", "")) or "")


def describe_job(job: Any) -> str:
    """Return the shared human-readable job summary used by UI and Adjutant."""
    config = getattr(job, "config", None)
    if config is None:
        return ""
    if is_dataclass(config):
        config_data = asdict(config)
    elif isinstance(config, dict):
        config_data = dict(config)
    else:
        return str(config)

    expert_type = getattr(job, "expert_type", "")
    if expert_type == "EconomyExpert":
        unit_type = config_data.get("unit_type")
        count = config_data.get("count")
        queue_type = config_data.get("queue_type")
        return f"{queue_type} · {unit_type} × {count}"
    if expert_type in {"ReconExpert", "CombatExpert", "MovementExpert", "StopExpert", "DeployExpert", "OccupyExpert"}:
        parts: list[str] = []
        if "target_position" in config_data and config_data["target_position"] is not None:
            parts.append(f"目标 {tuple(config_data['target_position'])}")
        if "search_region" in config_data:
            parts.append(f"区域 {config_data['search_region']}")
        if "target_type" in config_data:
            parts.append(f"目标类型 {config_data['target_type']}")
        if "engagement_mode" in config_data:
            parts.append(f"模式 {config_data['engagement_mode']}")
        if "move_mode" in config_data:
            parts.append(f"模式 {config_data['move_mode']}")
        if "target_actor_id" in config_data and config_data["target_actor_id"] is not None:
            parts.append(f"目标 actor {config_data['target_actor_id']}")
        if "actor_id" in config_data and config_data["actor_id"] is not None:
            parts.append(f"actor {config_data['actor_id']}")
        if "actor_ids" in config_data and config_data["actor_ids"]:
            parts.append(f"actors {list(config_data['actor_ids'])}")
        if expert_type == "StopExpert" and not parts:
            parts.append("停止当前任务单位")
        return " · ".join(parts)
    return ", ".join(f"{key}={value}" for key, value in config_data.items())


def collect_task_triage_inputs(
    *,
    task_id: str,
    jobs: Optional[list[Any]] = None,
    world_sync: Optional[dict[str, Any]] = None,
    pending_questions: Optional[list[dict[str, Any]]] = None,
    task_messages: Optional[list[Any]] = None,
    unit_mix: Optional[list[str]] = None,
) -> TaskTriageInputs:
    """Collect the shared triage side inputs from live runtime artifacts."""
    pending_question = None
    for question in list(pending_questions or []):
        if isinstance(question, dict) and str(question.get("task_id", "")) == str(task_id or ""):
            pending_question = question
            break

    latest_warning = ""
    for message in reversed(list(task_messages or [])):
        if getattr(message, "task_id", None) != task_id:
            continue
        if getattr(message, "type", None) == TaskMessageType.TASK_WARNING:
            latest_warning = str(getattr(message, "content", "") or "")
            break

    active_jobs = [
        job for job in list(jobs or [])
        if _job_status_value(job) not in {"succeeded", "failed", "aborted"}
    ]
    waiting_jobs = [job for job in active_jobs if _job_status_value(job) == "waiting"]
    running_jobs = [job for job in active_jobs if _job_status_value(job) == "running"]
    primary_job = running_jobs[0] if running_jobs else waiting_jobs[0] if waiting_jobs else active_jobs[0] if active_jobs else None

    return TaskTriageInputs(
        world_sync=dict(world_sync or {}),
        pending_question=pending_question,
        latest_warning=latest_warning,
        primary_summary=describe_job(primary_job) if primary_job is not None else "",
        unit_mix=list(unit_mix or []),
    )


def build_task_triage(
    *,
    task: Any,
    runtime_task: Optional[dict[str, Any]],
    runtime_state: dict[str, Any],
    inputs: Optional[TaskTriageInputs] = None,
    world_sync: Optional[dict[str, Any]] = None,
    pending_question: Optional[dict[str, Any]] = None,
    latest_warning: Optional[str] = None,
    primary_summary: str = "",
    unit_mix: Optional[list[str]] = None,
) -> TaskTriageSnapshot:
    """Build a shared task triage payload from runtime state.

    `main.py` and `Adjutant` have different local concerns, but the runtime
    state transition logic itself should stay identical.
    """
    task_id = str(getattr(task, "task_id", ""))
    status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or "")
    runtime_task = dict(runtime_task or {})
    runtime_state = dict(runtime_state or {})
    inputs = inputs or TaskTriageInputs(
        world_sync=dict(world_sync or {}),
        pending_question=pending_question,
        latest_warning=str(latest_warning or ""),
        primary_summary=str(primary_summary or ""),
        unit_mix=list(unit_mix or []),
    )
    world_sync = dict(inputs.world_sync or {})
    pending_question = inputs.pending_question
    latest_warning = str(inputs.latest_warning or "")
    primary_summary = str(inputs.primary_summary or "")
    unit_mix = list(inputs.unit_mix or [])

    def with_unit_mix(status_line: str) -> str:
        if not unit_mix or "×" in status_line:
            return status_line
        suffix = ", ".join(unit_mix[:3])
        return f"{status_line} | {suffix}" if status_line else suffix

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

    capability_status = CapabilityStatusSnapshot()
    raw_capability_status = runtime_state.get("capability_status")
    candidate_capability = CapabilityStatusSnapshot.from_mapping(raw_capability_status)
    task_label = str(getattr(task, "label", "") or runtime_task.get("label", "") or "")
    if candidate_capability.matches_task(task_id, task_label, is_capability=is_capability):
        capability_status = candidate_capability

    if status in _TERMINAL_STATUS_LINE:
        return TaskTriageSnapshot(
            state="completed",
            phase=status,
            status_line=_TERMINAL_STATUS_LINE[status],
            reservation_ids=reservation_ids,
            world_stale=bool(world_sync.get("stale")),
            active_group_size=active_group_size,
        )

    if bool(world_sync.get("stale")):
        return TaskTriageSnapshot(
            state="degraded",
            phase="world_sync",
            status_line="世界状态同步异常，等待恢复",
            waiting_reason="world_stale",
            blocking_reason="world_stale",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            world_stale=True,
            active_group_size=active_group_size,
        )

    if pending_question is not None:
        question = str(pending_question.get("question", "")).strip()
        question = question[:36] + "..." if len(question) > 36 else question
        return TaskTriageSnapshot(
            state="waiting_player",
            phase="question",
            status_line=with_unit_mix(f"等待玩家回复：{question}" if question else "等待玩家回复"),
            waiting_reason="player_response",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if latest_warning:
        return TaskTriageSnapshot(
            state="blocked",
            phase="warning",
            status_line=with_unit_mix(latest_warning),
            blocking_reason="task_warning",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if is_capability:
        pending_request_count = capability_status.pending_request_count
        blocking_request_count = capability_status.blocking_request_count
        start_released_request_count = capability_status.start_released_request_count
        reinforcement_request_count = capability_status.reinforcement_request_count
        active_job_types = list(capability_status.active_job_types)
        phase = capability_status.phase or ("dispatch" if active_job_types else "idle")
        blocker = capability_status.blocker
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

        return TaskTriageSnapshot(
            state=(
                "running"
                if phase in {"bootstrapping", "dispatch", "fulfilling", "executing"}
                or active_job_types
                or pending_request_count
                or start_released_request_count
                or reinforcement_request_count
                else "idle"
            ),
            phase=phase,
            status_line=status_line,
            waiting_reason=waiting_reason,
            blocking_reason=blocker,
            active_expert=",".join(active_job_types[:3]) if active_job_types else active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if reservations:
        first = reservations[0]
        unit_name = str(first.get("unit_type") or first.get("hint") or first.get("category") or "单位")
        remaining = max(int(first.get("remaining_count", 0) or 0), 0)
        status_line = (
            f"等待能力模块交付单位：{unit_name} × {remaining}"
            if remaining > 0
            else f"等待能力模块交付单位：{unit_name}"
        )
        return TaskTriageSnapshot(
            state="waiting_units",
            phase="reservation",
            status_line=with_unit_mix(status_line),
            waiting_reason="unit_reservation",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if waiting_jobs:
        status_line = primary_summary or f"等待执行条件满足：{active_expert or '任务'}"
        return TaskTriageSnapshot(
            state="waiting",
            phase="job_waiting",
            status_line=with_unit_mix(status_line),
            waiting_reason="job_waiting",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if running_jobs:
        status_line = primary_summary or f"运行中：{active_expert or '任务'}"
        return TaskTriageSnapshot(
            state="running",
            phase="job_running",
            status_line=with_unit_mix(status_line),
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    if active_group_size > 0:
        status_line = f"执行中 | group={active_group_size}"
        if unit_mix:
            status_line += f" | {', '.join(unit_mix[:3])}"
        return TaskTriageSnapshot(
            state="running",
            phase="task_active",
            status_line=status_line,
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            active_group_size=active_group_size,
        )

    return TaskTriageSnapshot(
        state="idle",
        phase="task_active",
        status_line=with_unit_mix("等待调度"),
        waiting_reason="scheduler",
        active_expert=active_expert,
        active_job_id=active_job_id,
        reservation_ids=reservation_ids,
        active_group_size=active_group_size,
    )
