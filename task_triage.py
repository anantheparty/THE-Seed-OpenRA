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
from task_agent.workflows import (
    PRODUCE_UNITS_THEN_ATTACK,
    PRODUCE_UNITS_THEN_RECON,
    classify_managed_workflow,
    workflow_phase as derive_workflow_phase,
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
    "directive_pending": "收到持续目标，待推进",
    "idle": "待机",
}

_WORKFLOW_STATUS_TEXT = {
    PRODUCE_UNITS_THEN_RECON: {
        "request_units_first": "工作流：produce_units_then_recon | 先请求执行单位",
        "waiting_for_units": "工作流：produce_units_then_recon | 等待单位请求/预留完成",
        "ready_to_recon": "工作流：produce_units_then_recon | 执行单位已到位，可开始侦察",
        "recon_running": "工作流：produce_units_then_recon | 侦察执行中",
    },
    PRODUCE_UNITS_THEN_ATTACK: {
        "request_units_first": "工作流：produce_units_then_attack | 先请求执行单位",
        "waiting_for_units": "工作流：produce_units_then_attack | 等待单位请求/预留完成",
        "ready_to_attack": "工作流：produce_units_then_attack | 执行单位已到位，可开始进攻",
        "attack_running": "工作流：produce_units_then_attack | 进攻执行中",
    },
}

_UNIT_PIPELINE_BLOCKING_REASONS = {
    "missing_prerequisite",
    "disabled_prerequisite",
    "producer_disabled",
    "queue_blocked",
    "deploy_required",
    "low_power",
    "insufficient_funds",
}

_UNIT_PIPELINE_BOOTSTRAP_REASONS = {
    "bootstrap_in_progress",
    "reinforcement_bootstrapping",
}

_UNIT_PIPELINE_DISPATCH_REASONS = {
    "waiting_dispatch",
    "reinforcement_waiting_dispatch",
    "inference_pending",
}

_UNIT_PIPELINE_DELIVERY_REASONS = {
    "start_package_released",
    "reinforcement_after_start",
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


def _unit_pipeline_label(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    if request is not None:
        return _request_display_name(request)
    if reservation is not None:
        return _reservation_display_name(reservation)
    return "单位"


def _unit_pipeline_reason(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    for item in (request, reservation):
        if isinstance(item, dict):
            reason = str(item.get("reason") or "").strip()
            if reason:
                return reason
    return ""


def unit_pipeline_reason_text(reason: str) -> str:
    return {
        "missing_prerequisite": "缺少前置",
        "disabled_prerequisite": "前置离线",
        "producer_disabled": "生产建筑离线",
        "queue_blocked": "队列阻塞",
        "deploy_required": "需先展开基地车",
        "low_power": "低电",
        "insufficient_funds": "资金不足",
        "world_sync_stale": "等待世界同步恢复",
        "bootstrap_in_progress": "前置生产中",
        "reinforcement_bootstrapping": "增援生产中",
        "waiting_dispatch": "待分发",
        "reinforcement_waiting_dispatch": "增援待分发",
        "inference_pending": "等待解析",
        "start_package_released": "待交付",
        "reinforcement_after_start": "增援待交付",
    }.get(str(reason or ""), str(reason or ""))


def classify_unit_pipeline_reason(reason: str) -> tuple[str, str, str, str]:
    reason = str(reason or "").strip()
    if reason == "world_sync_stale":
        return ("degraded", "world_sync", reason, reason)
    if reason in _UNIT_PIPELINE_BOOTSTRAP_REASONS:
        return ("running", "bootstrapping", reason, "")
    if reason in _UNIT_PIPELINE_DISPATCH_REASONS:
        return ("running", "dispatch", reason, "")
    if reason in _UNIT_PIPELINE_BLOCKING_REASONS:
        return ("blocked", "blocked", reason, reason)
    if reason in _UNIT_PIPELINE_DELIVERY_REASONS:
        return ("waiting_units", "reservation", reason, "")
    return ("waiting_units", "reservation", "unit_reservation", "")


def _unit_pipeline_count_text(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    remaining = _remaining_count(item)
    return f" × {remaining}" if remaining > 0 else ""


def build_unit_pipeline_preview(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    item = request if request is not None else reservation
    label = _unit_pipeline_label(request, reservation)
    preview = f"{label}{_unit_pipeline_count_text(item)}"
    reason = _unit_pipeline_reason(request, reservation)
    reason_text = unit_pipeline_reason_text(reason)
    if reason_text and reason not in _UNIT_PIPELINE_DELIVERY_REASONS:
        preview += f" · {reason_text}"
    return preview


def _unit_pipeline_progress_fields(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> dict[str, Any]:
    item = request if request is not None else reservation
    request_payload = request if isinstance(request, dict) else {}
    reservation_payload = reservation if isinstance(reservation, dict) else {}
    assigned_actor_ids = list(reservation_payload.get("assigned_actor_ids", []) or [])
    produced_actor_ids = list(reservation_payload.get("produced_actor_ids", []) or [])
    return {
        "reservation_status": str(
            reservation_payload.get("status")
            or request_payload.get("reservation_status")
            or ""
        ),
        "remaining_count": _remaining_count(item if isinstance(item, dict) else {}),
        "assigned_count": len([actor_id for actor_id in assigned_actor_ids if actor_id is not None]),
        "produced_count": len([actor_id for actor_id in produced_actor_ids if actor_id is not None]),
        "start_released": bool(
            request_payload.get("start_released")
            if request is not None
            else reservation_payload.get("start_released", False)
        ),
        "bootstrap_job_id": str(
            request_payload.get("bootstrap_job_id")
            or reservation_payload.get("bootstrap_job_id")
            or ""
        ),
    }


def build_unit_pipeline_progress_summary(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    if request is None and reservation is None:
        return ""
    progress = _unit_pipeline_progress_fields(request, reservation)
    parts = [
        f"assigned={int(progress.get('assigned_count', 0) or 0)}",
        f"produced={int(progress.get('produced_count', 0) or 0)}",
    ]
    status = str(progress.get("reservation_status") or "").strip()
    if status:
        parts.append(f"status={status}")
    if bool(progress.get("start_released", False)):
        parts.append("start=ready")
    return " ".join(parts)


def _match_request_for_reservation(
    requests: list[dict[str, Any]],
    reservation: dict[str, Any],
) -> dict[str, Any] | None:
    request_id = str(reservation.get("request_id") or "").strip()
    if not request_id:
        return None
    for request in requests:
        if str(request.get("request_id") or "").strip() == request_id:
            return request
    return None


def _unit_pipeline_reason_priority(reason: str) -> int:
    reason = str(reason or "").strip()
    if reason == "world_sync_stale":
        return 0
    if reason in _UNIT_PIPELINE_BLOCKING_REASONS:
        return 1
    if reason in _UNIT_PIPELINE_BOOTSTRAP_REASONS:
        return 2
    if reason in _UNIT_PIPELINE_DISPATCH_REASONS:
        return 3
    if reason in _UNIT_PIPELINE_DELIVERY_REASONS:
        return 4
    return 5


def _unit_pipeline_focus_sort_key(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> tuple[Any, ...]:
    item = request if request is not None else reservation if reservation is not None else {}
    reason = _unit_pipeline_reason(request, reservation)
    sync_failures = 0
    for source in (request, reservation):
        if not isinstance(source, dict):
            continue
        try:
            sync_failures = max(sync_failures, int(source.get("world_sync_consecutive_failures", 0) or 0))
        except Exception:
            continue
    try:
        updated_at = float((reservation or {}).get("updated_at", 0) or 0)
    except Exception:
        updated_at = 0.0
    blocking = bool((item or {}).get("blocking", True))
    return (
        _unit_pipeline_reason_priority(reason),
        -sync_failures,
        0 if blocking else 1,
        -_remaining_count(item if isinstance(item, dict) else {}),
        -updated_at,
        str((item or {}).get("task_label") or ""),
        str((item or {}).get("request_id") or ""),
        str((item or {}).get("reservation_id") or ""),
    )


def _runtime_unit_pipeline_candidates(
    runtime_state: RuntimeStateSnapshot | dict[str, Any] | None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[tuple[dict[str, Any] | None, dict[str, Any] | None]],
]:
    snapshot = RuntimeStateSnapshot.from_mapping(runtime_state)
    requests = [
        dict(item)
        for item in list(snapshot.unfulfilled_requests or [])
        if isinstance(item, dict)
    ]
    reservations = [
        dict(item)
        for item in list(snapshot.unit_reservations or [])
        if isinstance(item, dict)
    ]
    if not requests and not reservations:
        return [], [], []

    matched_request_ids: set[str] = set()
    candidates: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []

    for reservation in reservations:
        request = _match_request_for_reservation(requests, reservation)
        if request is not None:
            request_id = str(request.get("request_id") or "").strip()
            if request_id:
                matched_request_ids.add(request_id)
        candidates.append((request, reservation))

    for request in requests:
        request_id = str(request.get("request_id") or "").strip()
        if request_id and request_id in matched_request_ids:
            continue
        candidates.append((request, None))

    return requests, reservations, candidates


def _select_runtime_unit_pipeline_focus(
    runtime_state: RuntimeStateSnapshot | dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    requests, reservations, candidates = _runtime_unit_pipeline_candidates(runtime_state)
    if not candidates:
        return requests, reservations, None, None
    request, reservation = min(candidates, key=lambda pair: _unit_pipeline_focus_sort_key(pair[0], pair[1]))
    return requests, reservations, request, reservation


def build_runtime_unit_pipeline_preview(runtime_state: RuntimeStateSnapshot | dict[str, Any] | None) -> str:
    _requests, _reservations, request, reservation = _select_runtime_unit_pipeline_focus(runtime_state)
    if request is None and reservation is None:
        return ""
    return build_unit_pipeline_preview(request, reservation)


def build_runtime_unit_pipeline_preview_items(
    runtime_state: RuntimeStateSnapshot | dict[str, Any] | None,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    _requests, _reservations, candidates = _runtime_unit_pipeline_candidates(runtime_state)
    if not candidates:
        return []
    items: list[dict[str, Any]] = []
    for request, reservation in sorted(candidates, key=lambda pair: _unit_pipeline_focus_sort_key(pair[0], pair[1]))[:limit]:
        item = request if request is not None else reservation
        if not isinstance(item, dict):
            continue
        reason = _unit_pipeline_reason(request, reservation)
        items.append({
            "preview": build_unit_pipeline_preview(request, reservation),
            "task_id": str(item.get("task_id") or ""),
            "task_label": str(item.get("task_label") or ""),
            "reason": reason,
            "reason_text": unit_pipeline_reason_text(reason),
            "reservation_status": str(
                (reservation or {}).get("status")
                or (request or {}).get("reservation_status")
                or ""
            ),
        })
    return items


def build_runtime_unit_pipeline_focus(runtime_state: RuntimeStateSnapshot | dict[str, Any] | None) -> dict[str, Any]:
    requests, reservations, request, reservation = _select_runtime_unit_pipeline_focus(runtime_state)
    if request is None and reservation is None:
        return {}

    item = request if request is not None else reservation
    preview = build_unit_pipeline_preview(request, reservation)
    detail = _unit_pipeline_reason_detail(request, reservation) or preview
    reason = _unit_pipeline_reason(request, reservation)
    return {
        "preview": preview,
        "detail": detail,
        "reason": reason,
        "reason_text": unit_pipeline_reason_text(reason),
        "task_id": str((item or {}).get("task_id") or ""),
        "task_label": str((item or {}).get("task_label") or ""),
        "request_count": len(requests),
        "reservation_count": len(reservations),
        **_unit_pipeline_progress_fields(request, reservation),
    }


def build_task_unit_pipeline_focus(
    task_id: str,
    runtime_state: RuntimeStateSnapshot | dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact unit-pipeline truth for one task only."""
    task_id = str(task_id or "")
    if not task_id:
        return {}
    requests, reservations, candidates = _runtime_unit_pipeline_candidates(runtime_state)
    task_requests = [
        request
        for request in requests
        if isinstance(request, dict) and str(request.get("task_id") or "") == task_id
    ]
    task_reservations = [
        reservation
        for reservation in reservations
        if isinstance(reservation, dict) and str(reservation.get("task_id") or "") == task_id
    ]
    task_candidates = [
        (request, reservation)
        for request, reservation in candidates
        if str(((request or reservation) or {}).get("task_id") or "") == task_id
    ]
    if not task_candidates:
        return {}
    request, reservation = min(task_candidates, key=lambda pair: _unit_pipeline_focus_sort_key(pair[0], pair[1]))
    item = request if request is not None else reservation
    preview = build_unit_pipeline_preview(request, reservation)
    detail = _unit_pipeline_reason_detail(request, reservation) or preview
    reason = _unit_pipeline_reason(request, reservation)
    return {
        "preview": preview,
        "detail": detail,
        "reason": reason,
        "reason_text": unit_pipeline_reason_text(reason),
        "task_id": str((item or {}).get("task_id") or ""),
        "task_label": str((item or {}).get("task_label") or ""),
        "request_count": len(task_requests),
        "reservation_count": len(task_reservations),
        **_unit_pipeline_progress_fields(request, reservation),
    }


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


def _find_request_for_reservation(
    requests: list[dict[str, Any]],
    reservation: dict[str, Any],
) -> dict[str, Any] | None:
    request_id = str(reservation.get("request_id") or "").strip()
    if request_id:
        for request in requests:
            if str(request.get("request_id") or "").strip() == request_id:
                return request
    return requests[0] if requests else None


def _world_sync_error_text(world_sync: dict[str, Any]) -> str:
    raw = str(
        world_sync.get("last_error")
        or world_sync.get("last_refresh_error")
        or world_sync.get("error")
        or ""
    ).strip()
    if not raw:
        return ""
    compact = " ".join(raw.split())
    return f"{compact[:77]}..." if len(compact) > 80 else compact


def _world_sync_failure_count(world_sync: dict[str, Any]) -> int:
    for key in ("consecutive_failures", "consecutive_refresh_failures", "failures"):
        try:
            value = int(world_sync.get(key, 0) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _world_sync_failure_threshold(world_sync: dict[str, Any]) -> int:
    try:
        return max(int(world_sync.get("failure_threshold", 0) or 0), 0)
    except Exception:
        return 0


def _capability_blocker_detail(
    *,
    blocker: str,
    runtime_snapshot: RuntimeStateSnapshot,
    runtime_facts: Optional[dict[str, Any]] = None,
) -> str:
    runtime_facts = dict(runtime_facts or {})
    if blocker == "faction_roster_unsupported":
        faction = str(runtime_facts.get("faction") or "unknown").strip() or "unknown"
        return f"faction={faction} demo capability roster 未覆盖"
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


def _unit_pipeline_reason_detail(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    item = request if request is not None else reservation
    if not isinstance(item, dict):
        return ""
    label = _unit_pipeline_label(request, reservation)
    count_text = _unit_pipeline_count_text(item)
    reason = _unit_pipeline_reason(request, reservation)
    if reason == "missing_prerequisite":
        prerequisites = [
            demo_prompt_display_name_for(str(entry).lower())
            for entry in list(item.get("prerequisites", []) or [])
            if entry
        ]
        if prerequisites:
            return f"{label}{count_text} <- {' + '.join(prerequisites[:3])}"
    if reason == "disabled_prerequisite":
        disabled = [str(entry) for entry in list(item.get("disabled_prerequisites", []) or []) if entry]
        if disabled:
            return f"{label}{count_text} <- {' + '.join(disabled[:3])}"
    if reason == "producer_disabled":
        disabled = [str(entry) for entry in list(item.get("disabled_producers", []) or []) if entry]
        if disabled:
            return f"{label}{count_text} <- {' + '.join(disabled[:3])}"
    if reason == "queue_blocked":
        ready_items = [
            str(entry.get("display_name") or entry.get("unit_type") or "?")
            for entry in list(item.get("queue_blocked_items", []) or [])
            if isinstance(entry, dict)
        ]
        queue_reason = str(item.get("queue_blocked_reason") or "").strip()
        suffix = queue_reason
        if ready_items:
            preview = "、".join(ready_items[:2])
            suffix = f"{suffix}:{preview}" if suffix else preview
        return f"{label}{count_text} <- {suffix}" if suffix else f"{label}{count_text}"
    if reason == "deploy_required":
        return f"{label}{count_text} <- 需先展开基地车"
    if reason == "inference_pending":
        return f"{label}{count_text} <- 等待解析具体单位"
    if reason in {"waiting_dispatch", "reinforcement_waiting_dispatch"}:
        return f"{label}{count_text} <- 待分发"
    if reason in _UNIT_PIPELINE_BOOTSTRAP_REASONS:
        return f"{label}{count_text} <- {unit_pipeline_reason_text(reason)}"
    if reason == "world_sync_stale":
        failures = int(item.get("world_sync_consecutive_failures", 0) or 0)
        threshold = int(item.get("world_sync_failure_threshold", 0) or 0)
        error = str(item.get("world_sync_last_error") or "").strip()
        detail = f"{label}{count_text} <- 等待世界同步恢复"
        if failures:
            detail += f" failures={failures}"
            if threshold:
                detail += f"/{threshold}"
        if error:
            detail += f" | {error}"
        return detail
    if reason == "low_power":
        return f"{label}{count_text} <- 当前低电"
    if reason == "insufficient_funds":
        return f"{label}{count_text} <- 资金不足"
    return ""


def build_unit_pipeline_status_line(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> str:
    item = request if request is not None else reservation
    label = _unit_pipeline_label(request, reservation)
    count_text = _unit_pipeline_count_text(item)
    reason = _unit_pipeline_reason(request, reservation)

    if reason == "missing_prerequisite":
        status_line = f"等待能力模块补前置：{label}{count_text}"
    elif reason == "disabled_prerequisite":
        status_line = f"等待能力模块恢复前置：{label}{count_text}"
    elif reason == "producer_disabled":
        status_line = f"等待能力模块恢复生产建筑：{label}{count_text}"
    elif reason == "queue_blocked":
        status_line = f"等待能力模块解除队列阻塞：{label}{count_text}"
    elif reason == "deploy_required":
        status_line = f"等待能力模块先展开基地车：{label}{count_text}"
    elif reason == "low_power":
        status_line = f"等待能力模块恢复电力：{label}{count_text}"
    elif reason == "insufficient_funds":
        status_line = f"等待能力模块补足资金：{label}{count_text}"
    elif reason == "world_sync_stale":
        status_line = f"等待能力模块恢复世界同步：{label}{count_text}"
    elif reason == "inference_pending":
        status_line = f"等待能力模块解析具体单位：{label}{count_text}"
    elif reason in {"waiting_dispatch", "reinforcement_waiting_dispatch"}:
        status_line = f"等待能力模块分发单位：{label}{count_text}"
    elif reason in _UNIT_PIPELINE_BOOTSTRAP_REASONS:
        status_line = f"能力链推进中：{label}{count_text}"
    else:
        status_line = f"等待能力模块交付单位：{label}{count_text}"

    detail = _unit_pipeline_reason_detail(request, reservation)
    if detail and detail not in status_line:
        status_line += f" | {detail}"
    progress_summary = build_unit_pipeline_progress_summary(request, reservation)
    if progress_summary:
        status_line += f" | {progress_summary}"
    return status_line


def _unit_pipeline_world_sync_detail(
    request: dict[str, Any] | None,
    reservation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    reason = _unit_pipeline_reason(request, reservation)
    if reason != "world_sync_stale":
        return None
    item = request if isinstance(request, dict) else reservation
    if not isinstance(item, dict):
        return None
    return {
        "error": str(item.get("world_sync_last_error") or "").strip(),
        "failures": int(item.get("world_sync_consecutive_failures", 0) or 0),
        "failure_threshold": int(item.get("world_sync_failure_threshold", 0) or 0),
    }


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


def capability_blocker_status_text(
    capability_status: CapabilityStatusSnapshot | dict[str, Any],
    *,
    blocker_override: str = "",
) -> str:
    """Return a short human-readable blocker string for capability status."""
    snapshot = CapabilityStatusSnapshot.from_mapping(capability_status)
    blocker = str(blocker_override or snapshot.blocker)
    if blocker == "request_inference_pending":
        count = snapshot.inference_pending_count
        return f"等待解析请求 ({count})" if count else "等待解析请求"
    if blocker == "world_sync_stale":
        count = snapshot.world_sync_stale_count
        return f"等待世界同步恢复 ({count})" if count else "等待世界同步恢复"
    if blocker == "deploy_required":
        count = snapshot.deploy_required_count
        return f"等待展开基地车 ({count})" if count else "等待展开基地车"
    if blocker == "missing_prerequisite":
        count = snapshot.prerequisite_gap_count
        return f"缺少前置建筑 ({count})" if count else "缺少前置建筑"
    if blocker == "disabled_prerequisite":
        count = snapshot.disabled_prerequisite_count
        return f"前置建筑离线 ({count})" if count else "前置建筑离线"
    if blocker == "low_power":
        count = snapshot.low_power_count
        return f"低电受阻 ({count})" if count else "低电受阻"
    if blocker == "producer_disabled":
        count = snapshot.producer_disabled_count
        return f"生产建筑离线 ({count})" if count else "生产建筑离线"
    if blocker == "queue_blocked":
        count = snapshot.queue_blocked_count
        return f"队列阻塞 ({count})" if count else "队列阻塞"
    if blocker == "insufficient_funds":
        count = snapshot.insufficient_funds_count
        return f"资金不足 ({count})" if count else "资金不足"
    if blocker == "pending_requests_waiting_dispatch":
        count = snapshot.dispatch_request_count
        return f"请求待分发 ({count})" if count else "请求待分发"
    if blocker == "faction_roster_unsupported":
        return "阵营能力真值未覆盖"
    if blocker == "bootstrap_in_progress":
        count = snapshot.bootstrapping_request_count
        return f"前置生产中 ({count})" if count else "前置生产中"
    return blocker or "受阻"


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
    if blocker == "disabled_prerequisite":
        return {
            "code": "capability_disabled_prerequisite",
            "severity": "warning",
            "text": f"能力层存在 {snapshot.disabled_prerequisite_count} 个前置离线请求",
            "target_label": task_label,
        }
    if blocker == "deploy_required":
        return {
            "code": "capability_deploy_required",
            "severity": "warning",
            "text": "能力层等待展开基地车后继续补链",
            "target_label": task_label,
        }
    if blocker == "world_sync_stale":
        return {
            "code": "capability_world_sync_stale",
            "severity": "warning",
            "text": "能力层等待世界同步恢复",
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
    if blocker == "low_power":
        return {
            "code": "capability_low_power",
            "severity": "warning",
            "text": f"能力层有 {snapshot.low_power_count} 个请求受低电影响",
            "target_label": task_label,
        }
    if blocker == "queue_blocked":
        return {
            "code": "capability_queue_blocked",
            "severity": "warning",
            "text": f"能力层有 {snapshot.queue_blocked_count} 个请求被队列阻塞",
            "target_label": task_label,
        }
    if blocker == "insufficient_funds":
        return {
            "code": "capability_insufficient_funds",
            "severity": "info",
            "text": f"能力层有 {snapshot.insufficient_funds_count} 个请求因资金不足待处理",
            "target_label": task_label,
        }
    if blocker == "request_inference_pending":
        return {
            "code": "capability_inference_pending",
            "severity": "info",
            "text": f"能力层仍有 {snapshot.inference_pending_count} 个请求待解析",
            "target_label": task_label,
        }
    if blocker == "bootstrap_in_progress":
        return {
            "code": "capability_bootstrap_in_progress",
            "severity": "info",
            "text": f"能力层有 {snapshot.bootstrapping_request_count} 个请求正在补前置",
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


def _job_explicit_group_requested(job: Any) -> int:
    config = getattr(job, "config", None)
    actor_ids = list(getattr(config, "actor_ids", []) or [])
    return len([actor_id for actor_id in actor_ids if actor_id is not None])


def collect_task_triage_inputs(
    *,
    task_id: str,
    jobs: Optional[list[Any]] = None,
    world_sync: Optional[dict[str, Any]] = None,
    runtime_facts: Optional[dict[str, Any]] = None,
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
        runtime_facts=dict(runtime_facts or {}),
        pending_question=pending_question,
        latest_warning=latest_warning,
        latest_info=latest_info,
        primary_summary=describe_job(primary_job) if primary_job is not None else "",
        unit_mix=list(unit_mix or []),
        explicit_group_requested=_job_explicit_group_requested(primary_job) if primary_job is not None else 0,
    )


def build_task_triage_from_artifacts(
    *,
    task: Any,
    runtime_task: Optional[dict[str, Any]],
    runtime_state: dict[str, Any],
    task_id: str,
    jobs: Optional[list[Any]] = None,
    world_sync: Optional[dict[str, Any]] = None,
    runtime_facts: Optional[dict[str, Any]] = None,
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
        runtime_facts=runtime_facts,
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
    runtime_facts: Optional[dict[str, Any]] = None,
    pending_questions: list[Any],
    task_messages: list[Any],
    world_sync: Optional[dict[str, Any]] = None,
    world_stale: Optional[bool] = None,
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
        world_sync=(
            dict(world_sync or {})
            if world_sync is not None
            else {"stale": bool(world_stale)}
        ),
        runtime_facts=runtime_facts,
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
    runtime_facts: Optional[dict[str, Any]] = None,
    list_pending_questions: Callable[[], list[Any]],
    list_task_messages: Callable[..., list[Any]],
    world_sync: Optional[dict[str, Any]] = None,
    world_stale: Optional[bool] = None,
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
        runtime_facts=runtime_facts,
        pending_questions=list_pending_questions(),
        task_messages=task_messages,
        world_sync=world_sync,
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
    runtime_facts = dict(inputs.runtime_facts or {})
    pending_question = inputs.pending_question
    latest_warning = str(inputs.latest_warning or "")
    latest_info = str(inputs.latest_info or "")
    primary_summary = str(inputs.primary_summary or "")
    unit_mix = list(inputs.unit_mix or [])
    explicit_group_requested = max(int(inputs.explicit_group_requested or 0), 0)

    def with_unit_mix(status_line: str) -> str:
        if not unit_mix or "×" in status_line:
            return status_line
        suffix = ", ".join(unit_mix[:3])
        return f"{status_line} | {suffix}" if status_line else suffix

    def with_explicit_group_progress(status_line: str) -> str:
        if explicit_group_requested <= 0:
            return status_line
        suffix = f"group={active_group_size}/{explicit_group_requested}"
        return f"{status_line} | {suffix}" if status_line else suffix

    active_group_size = int(
        runtime_task.get("active_group_size", getattr(task, "active_group_size", 0)) or 0
    )
    ownership_fields = {
        "has_active_group": active_group_size > 0,
        "active_group_size": active_group_size,
    }
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
    task_requests = [
        request
        for request in runtime_snapshot.unfulfilled_requests
        if isinstance(request, dict) and str(request.get("task_id", "")) == task_id
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

    workflow_template = ""
    workflow_phase = ""
    if not is_capability:
        workflow_template = str(classify_managed_workflow(str(getattr(task, "raw_text", "") or "")) or "")
        if workflow_template:
            active_actor_ids = [
                int(actor_id)
                for actor_id in list(runtime_task.get("active_actor_ids", runtime_facts.get("active_actor_ids", [])) or [])
                if actor_id is not None
            ]
            workflow_runtime_facts = dict(runtime_facts or {})
            workflow_runtime_facts["unfulfilled_requests"] = list(task_requests)
            workflow_runtime_facts["unit_reservations"] = list(reservations)
            workflow_phase = str(
                derive_workflow_phase(
                    workflow_template,
                    runtime_facts=workflow_runtime_facts,
                    active_actor_ids=active_actor_ids,
                    jobs=[
                        {
                            "expert_type": str(job.get("expert_type", "") or ""),
                            "status": str(job.get("status", "") or ""),
                        }
                        for job in active_jobs
                    ],
                )
                or ""
            )

    workflow_fields = {
        "workflow_template": workflow_template,
        "workflow_phase": workflow_phase,
    }

    def workflow_status_line() -> str:
        return _WORKFLOW_STATUS_TEXT.get(workflow_template, {}).get(workflow_phase, "")

    if status in _TERMINAL_STATUS_LINE:
        return TaskTriageSnapshot(
            state="completed",
            phase=status,
            status_line=_TERMINAL_STATUS_LINE[status],
            reservation_ids=reservation_ids,
            world_stale=bool(world_sync.get("stale")),
            **ownership_fields,
            **workflow_fields,
        )

    if bool(world_sync.get("stale")):
        world_sync_error = _world_sync_error_text(world_sync)
        world_sync_failures = _world_sync_failure_count(world_sync)
        world_sync_failure_threshold = _world_sync_failure_threshold(world_sync)
        status_line = "世界状态同步异常，等待恢复"
        if world_sync_failures:
            if world_sync_failure_threshold:
                status_line += f" | failures={world_sync_failures}/{world_sync_failure_threshold}"
            else:
                status_line += f" | failures={world_sync_failures}"
        if world_sync_error:
            status_line += f" | {world_sync_error}"
        return TaskTriageSnapshot(
            state="degraded",
            phase="world_sync",
            status_line=status_line,
            waiting_reason="world_stale",
            blocking_reason="world_stale",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            world_stale=True,
            world_sync_error=world_sync_error,
            world_sync_failures=world_sync_failures,
            world_sync_failure_threshold=world_sync_failure_threshold,
            **ownership_fields,
            **workflow_fields,
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
            **ownership_fields,
            **workflow_fields,
        )

    if latest_warning:
        return TaskTriageSnapshot(
            state="blocked",
            phase="warning",
            status_line=with_explicit_group_progress(with_unit_mix(latest_warning)),
            blocking_reason="task_warning",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            **ownership_fields,
            **workflow_fields,
        )

    if is_capability:
        pending_request_count = capability_status.pending_request_count
        blocking_request_count = capability_status.blocking_request_count
        start_released_request_count = capability_status.start_released_request_count
        reinforcement_request_count = capability_status.reinforcement_request_count
        active_job_types = list(capability_status.active_job_types)
        phase = capability_status.phase or ("dispatch" if active_job_types else "idle")
        status_blocker = str(capability_status.blocker or "").strip()
        structural_blocker = str(runtime_facts.get("capability_truth_blocker") or "").strip()
        blocker = status_blocker or structural_blocker
        waiting_reason = blocker or (
            "start_package_released"
            if start_released_request_count
            else "reinforcement"
            if reinforcement_request_count
            else "pending_requests"
            if pending_request_count
            else ""
        )

        phase_text = _CAPABILITY_PHASE_TEXT.get(phase, phase or "进行中")
        if structural_blocker == "faction_roster_unsupported" and phase in {"", "idle"}:
            phase_text = "真值受限"
        status_line = f"能力处理中：{phase_text}"
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
            status_line += f" | blocker={capability_blocker_status_text(capability_status, blocker_override=blocker)}"
            detail = _capability_blocker_detail(
                blocker=blocker,
                runtime_snapshot=runtime_snapshot,
                runtime_facts=runtime_facts,
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
                "blocked"
                if structural_blocker
                else "running"
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
            **ownership_fields,
            **workflow_fields,
        )

    if task_requests or reservations:
        first_reservation = reservations[0] if reservations else None
        first_request = (
            _find_request_for_reservation(task_requests, first_reservation)
            if first_reservation is not None
            else task_requests[0]
        ) if task_requests else None
        reason = _unit_pipeline_reason(first_request, first_reservation)
        state, phase, waiting_reason, blocking_reason = classify_unit_pipeline_reason(reason)
        world_sync_detail = _unit_pipeline_world_sync_detail(first_request, first_reservation)
        return TaskTriageSnapshot(
            state=state,
            phase=phase,
            status_line=with_unit_mix(build_unit_pipeline_status_line(first_request, first_reservation)),
            waiting_reason=waiting_reason,
            blocking_reason=blocking_reason,
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            reservation_preview=build_unit_pipeline_preview(first_request, first_reservation),
            world_stale=world_sync_detail is not None,
            world_sync_error=str((world_sync_detail or {}).get("error") or ""),
            world_sync_failures=int((world_sync_detail or {}).get("failures", 0) or 0),
            world_sync_failure_threshold=int((world_sync_detail or {}).get("failure_threshold", 0) or 0),
            **ownership_fields,
            **_unit_pipeline_progress_fields(first_request, first_reservation),
            **workflow_fields,
        )

    if waiting_jobs:
        status_line = primary_summary or latest_info or workflow_status_line() or f"等待执行条件满足：{active_expert or '任务'}"
        return TaskTriageSnapshot(
            state="waiting",
            phase="job_waiting",
            status_line=with_explicit_group_progress(with_unit_mix(status_line)),
            waiting_reason="job_waiting",
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            **ownership_fields,
            **workflow_fields,
        )

    if running_jobs:
        status_line = primary_summary or latest_info or workflow_status_line() or f"运行中：{active_expert or '任务'}"
        return TaskTriageSnapshot(
            state="running",
            phase="job_running",
            status_line=with_explicit_group_progress(with_unit_mix(status_line)),
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            **ownership_fields,
            **workflow_fields,
        )

    if active_group_size > 0:
        status_line = latest_info or workflow_status_line() or f"执行中 | group={active_group_size}"
        if not latest_info and not workflow_status_line() and unit_mix:
            status_line += f" | {', '.join(unit_mix[:3])}"
        return TaskTriageSnapshot(
            state="running",
            phase="task_active",
            status_line=with_explicit_group_progress(status_line),
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            **ownership_fields,
            **workflow_fields,
        )

    if latest_info:
        return TaskTriageSnapshot(
            state="running",
            phase="task_active",
            status_line=with_unit_mix(latest_info),
            active_expert=active_expert,
            active_job_id=active_job_id,
            reservation_ids=reservation_ids,
            **ownership_fields,
            **workflow_fields,
        )

    return TaskTriageSnapshot(
        state="idle",
        phase="task_active",
        status_line=with_unit_mix(workflow_status_line() or "等待调度"),
        waiting_reason="scheduler",
        active_expert=active_expert,
        active_job_id=active_job_id,
        reservation_ids=reservation_ids,
        **ownership_fields,
        **workflow_fields,
    )
