from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import pytest
from kernel.unit_request_lifecycle import (
    build_capability_unfulfilled_event,
    build_unit_assigned_event,
    release_ready_task_requests,
    task_has_blocking_wait,
)
from models import ReservationStatus, UnitReservation, UnitRequest


def test_task_has_blocking_wait_only_for_unreleased_blocking_requests() -> None:
    pending = UnitRequest(
        request_id="req_pending",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=3,
        urgency="high",
        hint="tank",
        fulfilled=1,
        min_start_package=2,
    )
    released = UnitRequest(
        request_id="req_released",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=2,
        urgency="high",
        hint="tank",
        fulfilled=1,
        min_start_package=1,
        start_released=True,
    )
    nonblocking = UnitRequest(
        request_id="req_nonblocking",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=2,
        urgency="medium",
        hint="tank",
        blocking=False,
    )

    assert task_has_blocking_wait(
        [pending, released, nonblocking],
        "t_1",
        request_can_start=lambda req: req.fulfilled >= req.min_start_package,
    ) is True

    pending.fulfilled = 2
    assert task_has_blocking_wait(
        [pending, released, nonblocking],
        "t_1",
        request_can_start=lambda req: req.fulfilled >= req.min_start_package,
    ) is False


def test_release_ready_task_requests_updates_start_release_and_handoffs() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        count=3,
        urgency="high",
        hint="tank",
        fulfilled=2,
        status="partial",
        min_start_package=2,
        assigned_actor_ids=[10, 11],
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t_1",
        task_label="001",
        task_summary="attack",
        category="vehicle",
        unit_type="3tnk",
        count=3,
        status=ReservationStatus.PARTIAL,
        assigned_actor_ids=[10, 11],
    )
    handoff_calls: list[str] = []

    assigned_ids, fully_fulfilled = release_ready_task_requests(
        [req],
        "t_1",
        reservation_for_request=lambda _: reservation,
        request_can_start=lambda current: current.fulfilled >= current.min_start_package,
        handoff_request_assignments=lambda current: handoff_calls.append(current.request_id) or [10, 11],
        now=lambda: 123.0,
    )

    assert assigned_ids == [10, 11]
    assert fully_fulfilled is False
    assert handoff_calls == ["req_1"]
    assert req.start_released is True
    assert reservation.start_released is True
    assert reservation.updated_at == 123.0


def test_lifecycle_event_builders_preserve_request_semantics() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t_1",
        task_label="007",
        task_summary="attack",
        category="vehicle",
        count=4,
        urgency="critical",
        hint="重坦",
        fulfilled=1,
        blocking=False,
        min_start_package=2,
    )

    event = build_capability_unfulfilled_event(req)
    assert event.type.value == "UNIT_REQUEST_UNFULFILLED"
    assert event.data["request_id"] == "req_1"
    assert event.data["blocking"] is False
    assert event.data["min_start_package"] == 2

    wake = build_unit_assigned_event(assigned_ids=[10], fully_fulfilled=False)
    assert wake.type.value == "UNIT_ASSIGNED"
    assert wake.data["message"] == "请求单位已达到可启动数量"
    assert wake.data["actor_ids"] == [10]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
