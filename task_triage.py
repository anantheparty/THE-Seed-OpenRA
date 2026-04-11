"""Shared runtime task triage helpers.

This module keeps task state synthesis aligned between the dashboard/runtime
bridge and Adjutant coordinator context. Both surfaces need the same answer to
"what is this task doing right now?" or they drift and start telling different
stories to the player.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

from models.enums import TaskMessageType
from openra_state.data.dataset import demo_prompt_display_name_for
from runtime_views import (
    CapabilityStatusSnapshot,
    RuntimeStateSnapshot,
    TaskTriageInputs,
    TaskTriageSnapshot,
)


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


def _request_display_name(request: dict[str, Any]) -> str:
    hint = str(request.get("hint") or "").strip()
    if hint:
        return hint
    unit_type = str(request.get("unit_type") or "").strip().lower()
    if unit_type:
        return demo_prompt_display_name_for(unit_type)
    category = str(request.get("category") or "").strip()
    return category or "请求"


def _reservation_display_name(reservation: dict[str, Any]) -> str:
    unit_type = str(reservation.get("unit_type") or "").strip().lower()
    if unit_type:
        return demo_prompt_display_name_for(unit_type)
    return str(reservation.get("category") or "").strip() or "单位"


def _remaining_count(item: dict[str, Any]) -> int:
    if "remaining_count" in item:
        try:
            return max(int(item.get("remaining_count", 0) or 0), 0)
        except Exception:
            return 0
    try:
        count = int(item.get("count", 0) or 0)
    except Exception:
        count = 0
    try:
        fulfilled = int(item.get("fulfilled", 0) or 0)
    except Exception:
        fulfilled = 0
    return max(count - fulfilled, 0)


def _capability_blocker_detail(
    *,
    blocker: str,
    runtime_snapshot: RuntimeStateSnapshot,
) -> str:
    requests = list(runtime_snapshot.unfulfilled_requests or [])
    relevant = [
        item
        for item in requests
        if isinstance(item, dict) and str(item.get("reason") or "") == blocker
    ]
    if blocker in {"pending_requests_waiting_dispatch", "bootstrap_in_progress"}:
        relevant = [
            item
            for item in requests
            if isinstance(item, dict)
        ]
    if not relevant:
        return ""
    request = relevant[0]
    label = _request_display_name(request)
    remaining = _remaining_count(request)
    count_text = f"×{remaining}" if remaining > 1 else ""

    if blocker == "missing_prerequisite":
        prerequisites = [
            demo_prompt_display_name_for(str(item).lower())
            for item in list(request.get("prerequisites", []) or [])
            if item
        ]
        if prerequisites:
            return f"{label}{count_text} <- {' + '.join(prerequisites[:3])}"
    if blocker == "disabled_prerequisite":
        disabled = [str(item) for item in list(request.get("disabled_prerequisites", []) or []) if item]
        if disabled:
            return f"{label}{count_text} <- {' + '.join(disabled[:3])}"
    if blocker == "producer_disabled":
        disabled = [str(item) for item in list(request.get("disabled_producers", []) or []) if item]
        if disabled:
            return f"{label}{count_text} <- {' + '.join(disabled[:3])}"
    if blocker == "queue_blocked":
        ready_items = [
            str(item.get("display_name") or item.get("unit_type") or "?")
            for item in list(request.get("queue_blocked_items", []) or [])
            if isinstance(item, dict)
        ]
        queue_reason = str(request.get("queue_blocked_reason") or "").strip()
        suffix = ""
        if queue_reason:
            suffix = queue_reason
        if ready_items:
            ready_preview = "、".join(ready_items[:2])
            suffix = f"{suffix}:{ready_preview}" if suffix else ready_preview
        return f"{label}{count_text} <- {suffix}" if suffix else f"{label}{count_text}"
    if blocker == "deploy_required":
        return f"{label}{count_text} <- 需先展开基地车"
    if blocker == "request_inference_pending":
        return f"{label}{count_text} <- 等待解析具体单位"
    if blocker == "world_sync_stale":
        return f"{label}{count_text} <- 等待世界同步恢复"
    if blocker == "low_power":
        return f"{label}{count_text} <- 当前低电"
    if blocker == "insufficient_funds":
        return f"{label}{count_text} <- 资金不足"
    if blocker == "pending_requests_waiting_dispatch":
        return f"{label}{count_text} <- 待分发"
    if blocker == "bootstrap_in_progress":
        return f"{label}{count_text} <- fast-path 处理中"
    return ""


def _capability_fulfilling_detail(
    *,
    task_id: str,
    runtime_snapshot: RuntimeStateSnapshot,
) -> str:
    reservations = [
        item
        for item in runtime_snapshot.unit_reservations
        if isinstance(item, dict) and str(item.get("task_id", "")) == task_id
    ]
    if not reservations:
        return ""
    reservation = reservations[0]
    label = _reservation_display_name(reservation)
    remaining = _remaining_count(reservation)
    status = str(reservation.get("status") or "").strip()
    suffix = f"×{remaining}" if remaining > 1 else ""
    if status:
        return f"{label}{suffix} ({status})"
    return f"{label}{suffix}"


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
    if blocker == "producer_disabled":
        count = snapshot.producer_disabled_count
        return f"生产建筑离线 ({count})" if count else "生产建筑离线"
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
    if blocker == "producer_disabled":
        return {
            "code": "capability_producer_disabled",
            "severity": "warning",
            "text": f"能力层发现 {snapshot.producer_disabled_count} 个请求缺少在线生产建筑",
            "target_label": task_label,
        }
    return None


def _job_status_value(job: Any) -> str:
    return str(getattr(getattr(job, "status", None), "value", getattr(job, "status", "")) or "")


def describe_job(job: Any) -> str:
    """Return the shared human-readable job summary used by UI and Adjutant."""
    describe = getattr(job, "describe", None)
    if callable(describe):
        return str(describe() or "")

    config = getattr(job, "config", None)
    if config is None:
        return ""
    if is_dataclass(config):
        config_data = asdict(config)
    elif isinstance(config, dict):
        config_data = dict(config)
    else:
        return str(config)
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
    latest_info = ""
    for message in reversed(list(task_messages or [])):
        if getattr(message, "task_id", None) != task_id:
            continue
        if getattr(message, "type", None) == TaskMessageType.TASK_WARNING:
            latest_warning = str(getattr(message, "content", "") or "")
            break
        if not latest_info and getattr(message, "type", None) == TaskMessageType.TASK_INFO:
            latest_info = str(getattr(message, "content", "") or "")

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
        latest_info=latest_info,
        primary_summary=describe_job(primary_job) if primary_job is not None else "",
        unit_mix=list(unit_mix or []),
    )


def build_task_triage_from_artifacts(
    *,
    task: Any,
    runtime_task: Optional[dict[str, Any]],
    runtime_state: dict[str, Any],
    task_id: str,
    jobs: Optional[list[Any]] = None,
    world_sync: Optional[dict[str, Any]] = None,
    pending_questions: Optional[list[dict[str, Any]]] = None,
    task_messages: Optional[list[Any]] = None,
    unit_mix: Optional[list[str]] = None,
) -> TaskTriageSnapshot:
    """Build task triage directly from live runtime artifacts.

    This is the shared integration path for surfaces such as the bridge and
    Adjutant so they do not each reassemble side inputs differently.
    """
    inputs = collect_task_triage_inputs(
        task_id=str(task_id or ""),
        jobs=jobs,
        world_sync=world_sync,
        pending_questions=pending_questions,
        task_messages=task_messages,
        unit_mix=unit_mix,
    )
    return build_task_triage(
        task=task,
        runtime_task=runtime_task,
        runtime_state=runtime_state,
        inputs=inputs,
    )


def job_to_dict(job: Any) -> dict[str, Any]:
    """Job controller -> dashboard dict."""
    return {
        "job_id": job.job_id,
        "expert_type": job.expert_type,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "resources": list(getattr(job, "resources", []) or []),
        "timestamp": getattr(job, "timestamp", None),
        "summary": describe_job(job),
    }


def task_to_dict(
    task: Any,
    jobs: list[Any],
    *,
    runtime_state: dict[str, Any],
    pending_questions: list[Any],
    task_messages: list[Any],
    world_stale: bool,
    log_session_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Task + jobs -> dashboard dict with shared triage."""
    task_id = getattr(task, "task_id", "")
    runtime_snapshot = RuntimeStateSnapshot.from_mapping(runtime_state)
    runtime_task = runtime_snapshot.active_tasks.get(task_id)
    log_path = str(log_session_dir / "tasks" / f"{task_id}.jsonl") if log_session_dir else None
    triage = build_task_triage_from_artifacts(
        task=task,
        runtime_task=runtime_task,
        runtime_state=runtime_state,
        task_id=str(task_id or ""),
        jobs=jobs,
        world_sync={"stale": world_stale},
        pending_questions=pending_questions,
        task_messages=task_messages,
    ).to_dict()
    return {
        "task_id": task_id,
        "raw_text": task.raw_text,
        "kind": task.kind.value,
        "priority": task.priority,
        "status": task.status.value,
        "timestamp": task.timestamp,
        "created_at": task.created_at,
        "label": getattr(task, "label", ""),
        "is_capability": getattr(task, "is_capability", False),
        "log_path": log_path,
        "jobs": [job_to_dict(job) for job in jobs],
        "job_count": len(jobs),
        "triage": triage,
    }


def build_live_task_payload(
    task: Any,
    jobs: list[Any],
    *,
    runtime_state: Optional[dict[str, Any]],
    list_pending_questions: Callable[[], list[Any]],
    list_task_messages: Callable[..., list[Any]],
    world_stale: bool,
    log_session_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Build a dashboard/task payload from live runtime providers."""
    runtime_state = runtime_state or {}
    task_id = getattr(task, "task_id", "")
    try:
        task_messages = list_task_messages(task_id)
    except TypeError:
        task_messages = list_task_messages()
    return task_to_dict(
        task,
        jobs,
        runtime_state=runtime_state,
        pending_questions=list_pending_questions(),
        task_messages=task_messages,
        world_stale=world_stale,
        log_session_dir=log_session_dir,
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
    latest_info: Optional[str] = None,
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
    runtime_snapshot = RuntimeStateSnapshot.from_mapping(runtime_state)
    inputs = inputs or TaskTriageInputs(
        world_sync=dict(world_sync or {}),
        pending_question=pending_question,
        latest_warning=str(latest_warning or ""),
        latest_info=str(latest_info or ""),
        primary_summary=str(primary_summary or ""),
        unit_mix=list(unit_mix or []),
    )
    world_sync = dict(inputs.world_sync or {})
    pending_question = inputs.pending_question
    latest_warning = str(inputs.latest_warning or "")
    latest_info = str(inputs.latest_info or "")
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
        for info in runtime_snapshot.active_jobs.values()
        if isinstance(info, dict) and str(info.get("task_id", "")) == task_id
    ]
    waiting_jobs = [job for job in active_jobs if str(job.get("status", "")) == "waiting"]
    running_jobs = [job for job in active_jobs if str(job.get("status", "")) == "running"]
    primary_job = running_jobs[0] if running_jobs else waiting_jobs[0] if waiting_jobs else active_jobs[0] if active_jobs else {}
    active_expert = str(primary_job.get("expert_type", "") or "")
    active_job_id = str(primary_job.get("job_id", "") or "")

    reservations = [
        reservation
        for reservation in runtime_snapshot.unit_reservations
        if isinstance(reservation, dict) and str(reservation.get("task_id", "")) == task_id
    ]
    reservation_ids = [
        str(reservation.get("reservation_id", ""))
        for reservation in reservations
        if reservation.get("reservation_id")
    ]

    capability_status = CapabilityStatusSnapshot()
    candidate_capability = runtime_snapshot.capability_status
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
            detail = _capability_blocker_detail(
                blocker=blocker,
                runtime_snapshot=runtime_snapshot,
            )
            if detail:
                status_line += f" | {detail}"
        elif phase == "fulfilling":
            detail = _capability_fulfilling_detail(
                task_id=task_id,
                runtime_snapshot=runtime_snapshot,
            )
            if detail:
                status_line += f" | {detail}"

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
        status_line = primary_summary or latest_info or f"等待执行条件满足：{active_expert or '任务'}"
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
        status_line = primary_summary or latest_info or f"运行中：{active_expert or '任务'}"
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
        status_line = latest_info or f"执行中 | group={active_group_size}"
        if not latest_info and unit_mix:
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

    if latest_info:
        return TaskTriageSnapshot(
            state="running",
            phase="task_active",
            status_line=with_unit_mix(latest_info),
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
