"""Helpers for unit-request registration and idle fast-path fulfillment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from logging_system import get_logger
from models import Task, UnitRequest
from .unit_request_lifecycle import release_ready_task_requests

slog = get_logger("kernel")


def try_fulfill_from_idle(
    req: UnitRequest,
    *,
    world_model: Any,
    category_to_actor_category: dict[str, str],
    hint_match_score: Callable[[Any, str], int],
    bind_actor_to_request: Callable[[UnitRequest, Any], None],
) -> bool:
    """Try to satisfy a request from currently idle, unbound field units."""
    if req.category == "building":
        return False
    actor_category = category_to_actor_category.get(req.category)
    if actor_category is None:
        return False
    idle = world_model.find_actors(
        owner="self",
        idle_only=True,
        unbound_only=True,
        category=actor_category,
    )
    if not idle:
        return False
    idle.sort(key=lambda actor: hint_match_score(actor, req.hint), reverse=True)
    to_bind = idle[: req.count - req.fulfilled]
    for actor in to_bind:
        bind_actor_to_request(req, actor)
    return req.fulfilled >= req.count


def register_unit_request(
    *,
    task_id: str,
    category: str,
    count: int,
    urgency: str,
    hint: str,
    blocking: bool,
    min_start_package: int,
    tasks: Mapping[str, Task],
    unit_requests: MutableMapping[str, UnitRequest],
    infer_unit_type_for_request: Callable[[str, str], tuple[str | None, str | None]],
    ensure_reservation_for_request: Callable[[UnitRequest, str], Any],
    reservation_for_request: Callable[[UnitRequest], Any],
    try_fulfill_from_idle: Callable[[UnitRequest], bool],
    update_request_status_from_progress: Callable[[UnitRequest], None],
    request_can_start: Callable[[UnitRequest], bool],
    handoff_request_assignments: Callable[[UnitRequest], list[int]],
    bootstrap_production_for_request: Callable[[UnitRequest], Any],
    sync_world_runtime: Callable[[], None],
    notify_capability_unfulfilled: Callable[[UnitRequest], None],
    suspend_agent_for_requests: Callable[[str], None],
    unit_request_result: Callable[[UnitRequest, str], dict[str, Any]],
    gen_id: Callable[[str], str],
    now: Callable[[], float],
) -> dict[str, Any]:
    """Register a request: idle matching → bootstrap → wait."""
    task = tasks.get(task_id)
    if task is None:
        return {"status": "error", "message": f"Task {task_id} not found"}

    normalized_min_start = max(1, min(int(min_start_package), int(count)))
    if category == "building" and not bool(getattr(task, "is_capability", False)):
        return {
            "status": "error",
            "message": "普通任务不能直接请求建筑前置，请请求所需单位并等待 Capability 处理",
        }

    request_id = gen_id("req_")
    req = UnitRequest(
        request_id=request_id,
        task_id=task_id,
        task_label=task.label,
        task_summary=task.raw_text[:60],
        category=category,
        count=count,
        urgency=urgency,
        hint=hint,
        blocking=bool(blocking),
        min_start_package=normalized_min_start,
    )
    unit_requests[request_id] = req

    reservation = None
    unit_type, queue_type = infer_unit_type_for_request(req.category, req.hint)
    if unit_type is not None and queue_type is not None:
        reservation = ensure_reservation_for_request(req, unit_type)

    if try_fulfill_from_idle(req):
        update_request_status_from_progress(req)
        released_actor_ids = _release_and_log_ready_request(
            req,
            task_id=task_id,
            reservation_for_request=reservation_for_request,
            request_can_start=request_can_start,
            handoff_request_assignments=handoff_request_assignments,
            now=now,
        )
        sync_world_runtime()
        slog.info(
            "Unit request fulfilled from idle",
            event="unit_request_fulfilled",
            task_id=task_id,
            request_id=request_id,
            reservation_id=reservation.reservation_id if reservation is not None else "",
            actor_ids=req.assigned_actor_ids,
            reservation_status=reservation.status.value if reservation is not None else "",
            assigned_count=len(reservation.assigned_actor_ids) if reservation is not None else len(req.assigned_actor_ids),
            produced_count=len(reservation.produced_actor_ids) if reservation is not None else 0,
        )
        result = unit_request_result(req, "fulfilled")
        result["actor_ids"] = released_actor_ids or list(req.assigned_actor_ids)
        return result

    update_request_status_from_progress(req)
    released_actor_ids = _release_and_log_ready_request(
        req,
        task_id=task_id,
        reservation_for_request=reservation_for_request,
        request_can_start=request_can_start,
        handoff_request_assignments=handoff_request_assignments,
        now=now,
    )
    bootstrap_outcome = bootstrap_production_for_request(req)
    sync_world_runtime()

    if bootstrap_outcome.notify_capability:
        notify_capability_unfulfilled(req)

    suspend_agent_for_requests(task_id)

    slog.info(
        "Unit request registered",
        event="unit_request",
        task_id=task_id,
        request_id=request_id,
        category=category,
        count=count,
        urgency=urgency,
        hint=hint,
        fulfilled=req.fulfilled,
        status=req.status,
        blocking=req.blocking,
        min_start_package=req.min_start_package,
    )
    result = unit_request_result(req, "waiting")
    if released_actor_ids:
        result["actor_ids"] = list(released_actor_ids)
    return result


def _release_and_log_ready_request(
    req: UnitRequest,
    *,
    task_id: str,
    reservation_for_request: Callable[[UnitRequest], Any],
    request_can_start: Callable[[UnitRequest], bool],
    handoff_request_assignments: Callable[[UnitRequest], list[int]],
    now: Callable[[], float],
) -> list[int]:
    released_actor_ids, _fully_fulfilled, released_transitions = release_ready_task_requests(
        [req],
        task_id,
        reservation_for_request=reservation_for_request,
        request_can_start=request_can_start,
        handoff_request_assignments=handoff_request_assignments,
        now=now,
    )
    for transition in released_transitions:
        slog.info(
            "Unit request start released",
            event="unit_request_start_released",
            **transition,
        )
    return released_actor_ids
