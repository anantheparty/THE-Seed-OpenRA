"""Focused tests for unit-request entry helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.unit_request_entry import register_unit_request, try_fulfill_from_idle
from models import ReservationStatus, UnitReservation
from models import Task, TaskKind, TaskStatus, UnitRequest


def test_try_fulfill_from_idle_prefers_hint_matches() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="防守",
        category="infantry",
        count=1,
        urgency="medium",
        hint="火箭兵",
    )
    bound = []
    actors = [
        SimpleNamespace(actor_id=13, name="步枪兵", category=SimpleNamespace(value="infantry")),
        SimpleNamespace(actor_id=15, name="火箭兵", category=SimpleNamespace(value="infantry")),
    ]
    world_model = SimpleNamespace(
        find_actors=lambda **kwargs: list(actors),
    )

    def bind_actor(request, actor) -> None:
        request.fulfilled += 1
        request.assigned_actor_ids.append(actor.actor_id)
        bound.append(actor.actor_id)

    ok = try_fulfill_from_idle(
        req,
        world_model=world_model,
        category_to_actor_category={"infantry": "infantry"},
        hint_match_score=lambda actor, hint: 2 if actor.name == hint else 0,
        bind_actor_to_request=bind_actor,
    )

    assert ok is True
    assert bound == [15]
    assert req.assigned_actor_ids == [15]


def test_try_fulfill_from_idle_rejects_category_only_vehicle_mismatch() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        count=1,
        urgency="medium",
        hint="重坦",
    )
    bound = []
    actors = [
        SimpleNamespace(actor_id=21, name="V2火箭车", display_name="V2火箭车", category=SimpleNamespace(value="vehicle")),
        SimpleNamespace(actor_id=22, name="防空车", display_name="防空车", category=SimpleNamespace(value="vehicle")),
    ]
    world_model = SimpleNamespace(
        find_actors=lambda **kwargs: list(actors),
    )

    def bind_actor(request, actor) -> None:
        request.fulfilled += 1
        request.assigned_actor_ids.append(actor.actor_id)
        bound.append(actor.actor_id)

    ok = try_fulfill_from_idle(
        req,
        world_model=world_model,
        category_to_actor_category={"vehicle": "vehicle"},
        hint_match_score=lambda actor, hint: 2 if actor.name == hint or actor.display_name == hint else 0,
        bind_actor_to_request=bind_actor,
    )

    assert ok is False
    assert bound == []
    assert req.assigned_actor_ids == []


def test_register_unit_request_waiting_path_syncs_and_suspends() -> None:
    task = Task(
        task_id="t1",
        raw_text="进攻",
        kind=TaskKind.MANAGED,
        priority=60,
        status=TaskStatus.RUNNING,
        label="001",
    )
    unit_requests = {}
    calls = []

    result = register_unit_request(
        task_id="t1",
        category="vehicle",
        count=2,
        urgency="high",
        hint="重坦",
        blocking=True,
        min_start_package=1,
        tasks={"t1": task},
        unit_requests=unit_requests,
        infer_unit_type_for_request=lambda category, hint: ("3tnk", "Vehicle"),
        ensure_reservation_for_request=lambda req, unit_type: calls.append(("reserve", req.request_id, unit_type)) or object(),
        reservation_for_request=lambda req: None,
        try_fulfill_from_idle=lambda req: False,
        update_request_status_from_progress=lambda req: calls.append(("progress", req.request_id)),
        request_can_start=lambda req: False,
        handoff_request_assignments=lambda req: [],
        bootstrap_production_for_request=lambda req: SimpleNamespace(notify_capability=True),
        sync_world_runtime=lambda: calls.append(("sync",)),
        notify_capability_unfulfilled=lambda req: calls.append(("notify", req.request_id)),
        suspend_agent_for_requests=lambda task_id: calls.append(("suspend", task_id)),
        unit_request_result=lambda req, status: {"status": status, "request_id": req.request_id},
        gen_id=lambda prefix: f"{prefix}abc123",
        now=lambda: 123.0,
    )

    assert result == {"status": "waiting", "request_id": "req_abc123"}
    assert "req_abc123" in unit_requests
    assert calls == [
        ("reserve", "req_abc123", "3tnk"),
        ("progress", "req_abc123"),
        ("sync",),
        ("notify", "req_abc123"),
        ("suspend", "t1"),
    ]


def test_register_unit_request_rejects_building_for_non_capability() -> None:
    task = Task(
        task_id="t1",
        raw_text="造建筑",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        label="001",
    )

    result = register_unit_request(
        task_id="t1",
        category="building",
        count=1,
        urgency="high",
        hint="兵营",
        blocking=True,
        min_start_package=1,
        tasks={"t1": task},
        unit_requests={},
        infer_unit_type_for_request=lambda category, hint: ("barr", "Building"),
        ensure_reservation_for_request=lambda req, unit_type: object(),
        reservation_for_request=lambda req: None,
        try_fulfill_from_idle=lambda req: False,
        update_request_status_from_progress=lambda req: None,
        request_can_start=lambda req: False,
        handoff_request_assignments=lambda req: [],
        bootstrap_production_for_request=lambda req: SimpleNamespace(notify_capability=False),
        sync_world_runtime=lambda: None,
        notify_capability_unfulfilled=lambda req: None,
        suspend_agent_for_requests=lambda task_id: None,
        unit_request_result=lambda req, status: {"status": status},
        gen_id=lambda prefix: f"{prefix}x",
        now=lambda: 123.0,
    )

    assert result["status"] == "error"
    assert "普通任务不能直接请求建筑前置" in result["message"]


def test_register_unit_request_releases_start_package_reached_from_idle() -> None:
    task = Task(
        task_id="t1",
        raw_text="装甲推进",
        kind=TaskKind.MANAGED,
        priority=60,
        status=TaskStatus.RUNNING,
        label="001",
    )
    unit_requests = {}
    calls = []
    reservation_holder: dict[str, UnitReservation] = {}

    def ensure_reservation(req, unit_type):
        reservation = UnitReservation(
            reservation_id="res_1",
            request_id=req.request_id,
            task_id=req.task_id,
            task_label=req.task_label,
            task_summary=req.task_summary,
            category=req.category,
            unit_type=unit_type,
            count=req.count,
            status=ReservationStatus.PARTIAL,
            assigned_actor_ids=[10, 11],
        )
        reservation_holder["value"] = reservation
        return reservation

    def partially_fulfill(req):
        req.fulfilled = 2
        req.status = "partial"
        req.assigned_actor_ids = [10, 11]
        return False

    result = register_unit_request(
        task_id="t1",
        category="vehicle",
        count=3,
        urgency="high",
        hint="重坦",
        blocking=True,
        min_start_package=2,
        tasks={"t1": task},
        unit_requests=unit_requests,
        infer_unit_type_for_request=lambda category, hint: ("3tnk", "Vehicle"),
        ensure_reservation_for_request=ensure_reservation,
        reservation_for_request=lambda req: reservation_holder["value"],
        try_fulfill_from_idle=partially_fulfill,
        update_request_status_from_progress=lambda req: calls.append(("progress", req.request_id)),
        request_can_start=lambda req: req.fulfilled >= req.min_start_package,
        handoff_request_assignments=lambda req: calls.append(("handoff", req.request_id)) or [10, 11],
        bootstrap_production_for_request=lambda req: SimpleNamespace(notify_capability=True),
        sync_world_runtime=lambda: calls.append(("sync",)),
        notify_capability_unfulfilled=lambda req: calls.append(("notify", req.request_id)),
        suspend_agent_for_requests=lambda task_id: calls.append(("suspend", task_id)),
        unit_request_result=lambda req, status: {
            "status": status,
            "request_id": req.request_id,
            "start_released": req.start_released,
        },
        gen_id=lambda prefix: f"{prefix}abc123",
        now=lambda: 123.0,
    )

    reservation = reservation_holder["value"]
    req = unit_requests["req_abc123"]

    assert result == {
        "status": "waiting",
        "request_id": "req_abc123",
        "start_released": True,
        "actor_ids": [10, 11],
    }
    assert req.start_released is True
    assert reservation.start_released is True
    assert reservation.updated_at == 123.0
    assert calls == [
        ("progress", "req_abc123"),
        ("handoff", "req_abc123"),
        ("sync",),
        ("notify", "req_abc123"),
        ("suspend", "t1"),
    ]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
