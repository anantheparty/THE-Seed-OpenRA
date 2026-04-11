"""Runtime projection helpers for unit request / reservation state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Optional

from models import ReservationStatus, UnitReservation, UnitRequest
from openra_state.data.dataset import demo_prerequisites_for, queue_type_for_unit_type


def reservation_actor_total(reservation: UnitReservation) -> int:
    """Count unique actors already attached to a reservation."""
    assigned = {int(actor_id) for actor_id in (reservation.assigned_actor_ids or [])}
    produced = {int(actor_id) for actor_id in (reservation.produced_actor_ids or [])}
    return len(assigned | produced)


def request_reason(
    req: UnitRequest,
    reservation: Optional[UnitReservation],
    unit_type: str,
    *,
    production_readiness_for: Callable[[str, Optional[str]], dict[str, Any]],
) -> str:
    """Return the current runtime reason for a still-unfulfilled unit request."""
    queue_type = queue_type_for_unit_type(unit_type)
    if req.start_released:
        return "reinforcement_after_start" if not req.blocking else "start_package_released"
    if req.bootstrap_job_id:
        return "reinforcement_bootstrapping" if not req.blocking else "bootstrap_in_progress"
    if not unit_type or not queue_type:
        return "inference_pending"

    readiness = production_readiness_for(unit_type, queue_type)
    readiness_reason = str(readiness.get("reason") or "")
    if readiness_reason:
        return readiness_reason
    return "reinforcement_waiting_dispatch" if not req.blocking else "waiting_dispatch"


def build_unfulfilled_request_payloads(
    requests: Iterable[UnitRequest],
    *,
    reservation_for_request: Callable[[UnitRequest], Optional[UnitReservation]],
    request_reservation_id: Callable[[str], str],
    production_readiness_for: Callable[[str, Optional[str]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize still-open unit requests for runtime facts / capability context."""
    unfulfilled: list[dict[str, Any]] = []
    for req in requests:
        if req.status not in ("pending", "partial"):
            continue
        reservation = reservation_for_request(req)
        unit_type = reservation.unit_type if reservation is not None else ""
        queue_type = queue_type_for_unit_type(unit_type)
        readiness = production_readiness_for(unit_type, queue_type) if unit_type and queue_type else {}
        unfulfilled.append(
            {
                "request_id": req.request_id,
                "reservation_id": request_reservation_id(req.request_id),
                "task_id": req.task_id,
                "task_label": req.task_label,
                "task_summary": req.task_summary,
                "category": req.category,
                "unit_type": unit_type,
                "count": req.count,
                "fulfilled": req.fulfilled,
                "remaining_count": max(req.count - req.fulfilled, 0),
                "urgency": req.urgency,
                "hint": req.hint,
                "blocking": req.blocking,
                "min_start_package": req.min_start_package,
                "start_released": req.start_released,
                "bootstrap_job_id": req.bootstrap_job_id,
                "bootstrap_task_id": req.bootstrap_task_id,
                "queue_type": queue_type,
                "prerequisites": demo_prerequisites_for(unit_type) if unit_type else [],
                "reservation_status": reservation.status.value if reservation is not None else "",
                "reason": request_reason(
                    req,
                    reservation,
                    unit_type,
                    production_readiness_for=production_readiness_for,
                ),
                "disabled_producers": list(readiness.get("disabled_producers", [])),
            }
        )
    return unfulfilled


def build_active_reservation_payloads(
    reservations: Iterable[UnitReservation],
    *,
    requests_by_id: dict[str, UnitRequest],
    production_readiness_for: Callable[[str, Optional[str]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize active reservation contracts for runtime state."""
    active_reservations: list[dict[str, Any]] = []
    for reservation in reservations:
        if reservation.status in {ReservationStatus.CANCELLED, ReservationStatus.EXPIRED}:
            continue
        req = requests_by_id.get(reservation.request_id)
        if req is not None and req.status not in ("pending", "partial"):
            continue
        active_reservations.append(
            {
                "reservation_id": reservation.reservation_id,
                "request_id": reservation.request_id,
                "task_id": reservation.task_id,
                "task_label": reservation.task_label,
                "category": reservation.category,
                "unit_type": reservation.unit_type,
                "count": reservation.count,
                "urgency": reservation.urgency,
                "hint": reservation.hint,
                "blocking": reservation.blocking,
                "min_start_package": reservation.min_start_package,
                "start_released": reservation.start_released,
                "status": reservation.status.value,
                "request_status": req.status if req is not None else "",
                "assigned_actor_ids": list(reservation.assigned_actor_ids),
                "produced_actor_ids": list(reservation.produced_actor_ids),
                "bootstrap_job_id": reservation.bootstrap_job_id,
                "bootstrap_task_id": reservation.bootstrap_task_id,
                "updated_at": reservation.updated_at,
                "remaining_count": max(reservation.count - reservation_actor_total(reservation), 0),
                "queue_type": queue_type_for_unit_type(reservation.unit_type),
                "reason": (
                    request_reason(
                        req,
                        reservation,
                        reservation.unit_type,
                        production_readiness_for=production_readiness_for,
                    )
                    if req is not None
                    else ""
                ),
            }
        )
    return active_reservations
