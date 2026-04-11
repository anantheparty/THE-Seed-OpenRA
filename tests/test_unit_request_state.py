from __future__ import annotations

from kernel.unit_request_state import (
    bind_actor_to_request_state,
    cancel_request_state,
    update_request_status_from_progress,
)
from models import ReservationStatus, UnitReservation, UnitRequest


def test_update_request_status_from_progress() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=3,
        urgency="high",
        hint="tank",
    )

    update_request_status_from_progress(req)
    assert req.status == "pending"

    req.fulfilled = 1
    update_request_status_from_progress(req)
    assert req.status == "partial"

    req.fulfilled = 3
    update_request_status_from_progress(req)
    assert req.status == "fulfilled"


def test_bind_actor_to_request_state_updates_request_and_reservation() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=2,
        urgency="high",
        hint="tank",
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        unit_type="3tnk",
        count=2,
    )

    bind_actor_to_request_state(
        req,
        reservation,
        actor_id=10,
        produced=True,
        now=lambda: 123.0,
    )

    assert req.fulfilled == 1
    assert req.assigned_actor_ids == [10]
    assert reservation.assigned_actor_ids == [10]
    assert reservation.produced_actor_ids == [10]
    assert reservation.status == ReservationStatus.PARTIAL
    assert reservation.updated_at == 123.0


def test_cancel_request_state_marks_reservation_cancelled() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=2,
        urgency="high",
        hint="tank",
        status="partial",
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        unit_type="3tnk",
        count=2,
        status=ReservationStatus.PARTIAL,
    )

    cancel_request_state(req, reservation, now=lambda: 321.0)

    assert req.status == "cancelled"
    assert reservation.status == ReservationStatus.CANCELLED
    assert reservation.cancelled_at == 321.0
    assert reservation.updated_at == 321.0
