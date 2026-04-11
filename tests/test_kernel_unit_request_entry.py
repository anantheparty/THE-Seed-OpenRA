"""Focused tests for unit-request entry helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.unit_request_entry import register_unit_request, try_fulfill_from_idle
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
        SimpleNamespace(actor_id=13, name="步枪兵"),
        SimpleNamespace(actor_id=15, name="火箭兵"),
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
        try_fulfill_from_idle=lambda req: False,
        update_request_status_from_progress=lambda req: calls.append(("progress", req.request_id)),
        bootstrap_production_for_request=lambda req: SimpleNamespace(notify_capability=True),
        sync_world_runtime=lambda: calls.append(("sync",)),
        notify_capability_unfulfilled=lambda req: calls.append(("notify", req.request_id)),
        suspend_agent_for_requests=lambda task_id: calls.append(("suspend", task_id)),
        unit_request_result=lambda req, status: {"status": status, "request_id": req.request_id},
        gen_id=lambda prefix: f"{prefix}abc123",
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
        try_fulfill_from_idle=lambda req: False,
        update_request_status_from_progress=lambda req: None,
        bootstrap_production_for_request=lambda req: SimpleNamespace(notify_capability=False),
        sync_world_runtime=lambda: None,
        notify_capability_unfulfilled=lambda req: None,
        suspend_agent_for_requests=lambda task_id: None,
        unit_request_result=lambda req, status: {"status": status},
        gen_id=lambda prefix: f"{prefix}x",
    )

    assert result["status"] == "error"
    assert "普通任务不能直接请求建筑前置" in result["message"]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
