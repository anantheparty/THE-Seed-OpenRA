"""Lifecycle helpers for unit-request wait / release transitions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Optional

from models import Event, EventType, UnitReservation, UnitRequest


def task_has_blocking_wait(
    requests: Iterable[UnitRequest],
    task_id: str,
    *,
    request_can_start: Callable[[UnitRequest], bool],
) -> bool:
    """Return True when a task still has a blocking request below its start package."""
    for req in requests:
        if req.task_id != task_id:
            continue
        if req.status in ("fulfilled", "cancelled"):
            continue
        if not req.blocking:
            continue
        if req.start_released:
            continue
        if request_can_start(req):
            continue
        return True
    return False


def release_ready_task_requests(
    requests: Iterable[UnitRequest],
    task_id: str,
    *,
    reservation_for_request: Callable[[UnitRequest], Optional[UnitReservation]],
    request_can_start: Callable[[UnitRequest], bool],
    handoff_request_assignments: Callable[[UnitRequest], list[int]],
    now: Callable[[], float],
) -> tuple[list[int], bool, list[dict[str, Any]]]:
    """Release ready requests for a task and hand off any assigned actors."""
    assigned_ids: list[int] = []
    blocking_requests: list[UnitRequest] = []
    released_transitions: list[dict[str, Any]] = []

    for req in requests:
        if req.task_id != task_id:
            continue
        if req.status == "cancelled":
            continue
        if req.blocking:
            blocking_requests.append(req)
        if not req.start_released and (req.status == "fulfilled" or request_can_start(req)):
            req.start_released = True
            reservation = reservation_for_request(req)
            timestamp = now()
            if reservation is not None:
                reservation.start_released = True
                reservation.updated_at = timestamp
            released_transitions.append(
                {
                    "request_id": req.request_id,
                    "reservation_id": reservation.reservation_id if reservation is not None else "",
                    "task_id": req.task_id,
                    "status": reservation.status.value if reservation is not None else "",
                    "start_released": True,
                    "assigned_count": len(reservation.assigned_actor_ids) if reservation is not None else len(req.assigned_actor_ids),
                    "produced_count": len(reservation.produced_actor_ids) if reservation is not None else 0,
                    "remaining_count": max(req.count - req.fulfilled, 0),
                    "timestamp": timestamp,
                }
            )
        if req.start_released:
            assigned_ids.extend(handoff_request_assignments(req))

    fully_fulfilled = all(req.status in ("fulfilled", "cancelled") for req in blocking_requests)
    return assigned_ids, fully_fulfilled, released_transitions


def build_capability_unfulfilled_event(req: UnitRequest) -> Event:
    """Build the event pushed to Capability for an unfulfilled request."""
    return Event(
        type=EventType.UNIT_REQUEST_UNFULFILLED,
        data={
            "request_id": req.request_id,
            "task_label": req.task_label,
            "category": req.category,
            "count": req.count,
            "fulfilled": req.fulfilled,
            "urgency": req.urgency,
            "hint": req.hint,
            "blocking": req.blocking,
            "min_start_package": req.min_start_package,
        },
    )


def build_unit_assigned_event(*, assigned_ids: list[int], fully_fulfilled: bool) -> Event:
    """Build the wake event delivered to a task after request release."""
    message = "所有请求的单位已到位" if fully_fulfilled else "请求单位已达到可启动数量"
    return Event(
        type=EventType.UNIT_ASSIGNED,
        data={"message": message, "actor_ids": assigned_ids},
    )
