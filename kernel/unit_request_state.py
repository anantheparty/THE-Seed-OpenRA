"""State-transition helpers for unit requests and reservations."""

from __future__ import annotations

from typing import Callable, Optional

from models import UnitReservation, UnitRequest
from models.enums import ReservationStatus


def update_request_status_from_progress(req: UnitRequest) -> None:
    """Update request status from current fulfillment progress."""
    if req.fulfilled >= req.count:
        req.status = "fulfilled"
    elif req.fulfilled > 0:
        req.status = "partial"


def bind_actor_to_request_state(
    req: UnitRequest,
    reservation: Optional[UnitReservation],
    *,
    actor_id: int,
    produced: bool,
    now: Callable[[], float],
) -> None:
    """Record an actor assignment on a request / reservation pair."""
    newly_assigned = actor_id not in req.assigned_actor_ids
    if newly_assigned:
        req.assigned_actor_ids.append(actor_id)
        req.fulfilled += 1
    if reservation is None:
        return
    if actor_id not in reservation.assigned_actor_ids:
        reservation.assigned_actor_ids.append(actor_id)
    if produced and actor_id not in reservation.produced_actor_ids:
        reservation.produced_actor_ids.append(actor_id)
    reservation.status = (
        ReservationStatus.ASSIGNED
        if req.fulfilled >= req.count
        else ReservationStatus.PARTIAL
    )
    reservation.updated_at = now()


def cancel_request_state(
    req: UnitRequest,
    reservation: Optional[UnitReservation],
    *,
    now: Callable[[], float],
) -> None:
    """Mark a request/reservation pair as cancelled."""
    req.status = "cancelled"
    if reservation is None:
        return
    timestamp = now()
    reservation.status = ReservationStatus.CANCELLED
    reservation.cancelled_at = timestamp
    reservation.updated_at = timestamp
