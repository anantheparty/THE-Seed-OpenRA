"""Bookkeeping helpers for unit requests and reservations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from models import UnitReservation, UnitRequest
from models.enums import ReservationStatus
from openra_state.data.dataset import queue_type_for_unit_type


def ensure_reservation_for_request(
    req: UnitRequest,
    unit_type: str,
    *,
    request_reservations: dict[str, str],
    unit_reservations: dict[str, UnitReservation],
    gen_id: Callable[[str], str],
) -> UnitReservation:
    """Create or return the reservation associated with a unit request."""
    reservation_id = request_reservations.get(req.request_id)
    if reservation_id and reservation_id in unit_reservations:
        return unit_reservations[reservation_id]
    reservation = UnitReservation(
        reservation_id=gen_id("res_"),
        request_id=req.request_id,
        task_id=req.task_id,
        task_label=req.task_label,
        task_summary=req.task_summary,
        category=req.category,
        unit_type=unit_type,
        count=req.count,
        urgency=req.urgency,
        hint=req.hint,
        blocking=req.blocking,
        min_start_package=req.min_start_package,
    )
    unit_reservations[reservation.reservation_id] = reservation
    request_reservations[req.request_id] = reservation.reservation_id
    return reservation


def reservation_for_request(
    req: UnitRequest,
    *,
    request_reservations: dict[str, str],
    unit_reservations: dict[str, UnitReservation],
) -> Optional[UnitReservation]:
    """Return the reservation associated with a request, if any."""
    reservation_id = request_reservations.get(req.request_id)
    if not reservation_id:
        return None
    return unit_reservations.get(reservation_id)


def clear_request_bootstrap_refs(
    req: UnitRequest,
    reservation: Optional[UnitReservation],
    *,
    now: Callable[[], float],
) -> None:
    """Clear fast-path bootstrap references on a request/reservation pair."""
    req.bootstrap_job_id = None
    req.bootstrap_task_id = None
    if reservation is not None:
        reservation.bootstrap_job_id = None
        reservation.bootstrap_task_id = None
        reservation.updated_at = now()


def request_start_goal(req: UnitRequest) -> int:
    """Return the number of fulfilled units needed before the request may start."""
    return max(1, min(int(req.count), int(req.min_start_package or 1)))


def request_can_start(req: UnitRequest) -> bool:
    """Return True when the request reached its minimum start package."""
    return req.fulfilled >= request_start_goal(req)


def build_unit_request_result(
    req: UnitRequest,
    *,
    reservation: Optional[UnitReservation],
    infer_unit_type: Callable[[str, str], tuple[Optional[str], Optional[str]]],
) -> dict[str, object]:
    """Serialize a unit request result payload for kernel/tool responses."""
    unit_type = reservation.unit_type if reservation is not None else ""
    queue_type = queue_type_for_unit_type(unit_type)
    if not queue_type:
        inferred_unit_type, inferred_queue_type = infer_unit_type(req.category, req.hint)
        if not unit_type and inferred_unit_type:
            unit_type = inferred_unit_type
        if not queue_type and inferred_queue_type:
            queue_type = inferred_queue_type
    return {
        "request_id": req.request_id,
        "remaining_count": max(req.count - req.fulfilled, 0),
        "fulfilled": req.fulfilled,
        "count": req.count,
        "urgency": req.urgency,
        "hint": req.hint,
        "blocking": req.blocking,
        "min_start_package": req.min_start_package,
        "start_released": req.start_released,
        "reservation_id": reservation.reservation_id if reservation is not None else "",
        "reservation_status": reservation.status.value if reservation is not None else "",
        "bootstrap_job_id": req.bootstrap_job_id or (reservation.bootstrap_job_id if reservation is not None else ""),
        "bootstrap_task_id": req.bootstrap_task_id or (reservation.bootstrap_task_id if reservation is not None else ""),
        "unit_type": unit_type,
        "queue_type": queue_type,
    }
