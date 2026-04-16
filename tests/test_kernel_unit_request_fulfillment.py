"""Focused tests for unit-request fulfillment helpers."""

from __future__ import annotations

import pytest
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.unit_request_fulfillment import (
    agent_is_suspended,
    fulfill_unit_requests,
    suspend_agent_for_requests,
    wake_waiting_agent,
)
from models import EventType, ReservationStatus, UnitReservation, UnitRequest


class _Agent:
    def __init__(self) -> None:
        self._suspended = False
        self.resumed = []
        self.events = []

    def suspend(self) -> None:
        self._suspended = True

    def resume_with_event(self, event) -> None:
        self._suspended = False
        self.resumed.append(event)

    def push_event(self, event) -> None:
        self.events.append(event)


@dataclass
class _Runtime:
    agent: _Agent


def test_agent_is_suspended_supports_property_and_private_flag() -> None:
    class _WithProperty:
        @property
        def is_suspended(self) -> bool:
            return True

    assert agent_is_suspended(_WithProperty()) is True
    agent = _Agent()
    assert agent_is_suspended(agent) is False
    agent._suspended = True
    assert agent_is_suspended(agent) is True


def test_suspend_agent_for_requests_only_when_blocking_wait_exists() -> None:
    agent = _Agent()
    task_runtimes = {"t1": _Runtime(agent=agent)}

    suspend_agent_for_requests(
        "t1",
        task_has_blocking_wait=lambda task_id: False,
        task_runtimes=task_runtimes,
    )
    assert agent._suspended is False

    suspend_agent_for_requests(
        "t1",
        task_has_blocking_wait=lambda task_id: True,
        task_runtimes=task_runtimes,
    )
    assert agent._suspended is True


def test_wake_waiting_agent_releases_ready_request_and_resumes_agent() -> None:
    agent = _Agent()
    runtime = _Runtime(agent=agent)
    req = UnitRequest(
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        count=2,
        urgency="high",
        hint="重坦",
        blocking=True,
        min_start_package=1,
        fulfilled=1,
        status="partial",
        assigned_actor_ids=[10],
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        unit_type="3tnk",
        count=2,
        status=ReservationStatus.PARTIAL,
        assigned_actor_ids=[10],
    )
    agent._suspended = True
    sync_calls = []

    wake_waiting_agent(
        "t1",
        task_has_blocking_wait=lambda task_id: False,
        task_runtimes={"t1": runtime},
        unit_requests=[req],
        reservation_for_request=lambda request: reservation,
        request_can_start=lambda request: True,
        handoff_request_assignments=lambda request: [10],
        now=lambda: 123.0,
        sync_world_runtime=lambda: sync_calls.append("sync"),
    )

    assert req.start_released is True
    assert reservation.start_released is True
    assert reservation.updated_at == 123.0
    assert agent._suspended is False
    assert len(agent.resumed) == 1
    assert agent.resumed[0].type == EventType.UNIT_ASSIGNED
    assert agent.resumed[0].data["actor_ids"] == [10]
    assert sync_calls == ["sync"]


def test_wake_waiting_agent_keeps_task_gate_when_other_blocking_wait_remains() -> None:
    agent = _Agent()
    runtime = _Runtime(agent=agent)
    ready_req = UnitRequest(
        request_id="req_ready",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        count=2,
        urgency="high",
        hint="重坦",
        blocking=True,
        min_start_package=1,
        fulfilled=1,
        status="partial",
        assigned_actor_ids=[10],
    )
    reservation = UnitReservation(
        reservation_id="res_ready",
        request_id="req_ready",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        unit_type="3tnk",
        count=2,
        status=ReservationStatus.PARTIAL,
        assigned_actor_ids=[10],
    )
    handoff_calls = []
    sync_calls = []

    wake_waiting_agent(
        "t1",
        task_has_blocking_wait=lambda task_id: True,
        task_runtimes={"t1": runtime},
        unit_requests=[ready_req],
        reservation_for_request=lambda request: reservation,
        request_can_start=lambda request: True,
        handoff_request_assignments=lambda request: handoff_calls.append(request.request_id) or [10],
        now=lambda: 123.0,
        sync_world_runtime=lambda: sync_calls.append("sync"),
    )

    assert ready_req.start_released is False
    assert reservation.start_released is False
    assert handoff_calls == []
    assert sync_calls == []
    assert agent.resumed == []
    assert agent.events == []


def test_wake_waiting_agent_handoffs_before_resume_and_sync() -> None:
    agent = _Agent()
    runtime = _Runtime(agent=agent)
    req = UnitRequest(
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        count=2,
        urgency="high",
        hint="重坦",
        blocking=True,
        min_start_package=1,
        fulfilled=1,
        status="partial",
        assigned_actor_ids=[10],
    )
    reservation = UnitReservation(
        reservation_id="res_1",
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        unit_type="3tnk",
        count=2,
        status=ReservationStatus.PARTIAL,
        assigned_actor_ids=[10],
    )
    agent._suspended = True
    order = []

    def handoff_request_assignments(request):
        assert request.start_released is True
        assert reservation.start_released is True
        order.append("handoff")
        return [10]

    def sync_world_runtime():
        order.append("sync")

    original_resume = agent.resume_with_event

    def resume_with_event(event):
        order.append("resume")
        original_resume(event)

    agent.resume_with_event = resume_with_event  # type: ignore[assignment]

    wake_waiting_agent(
        "t1",
        task_has_blocking_wait=lambda task_id: False,
        task_runtimes={"t1": runtime},
        unit_requests=[req],
        reservation_for_request=lambda request: reservation,
        request_can_start=lambda request: True,
        handoff_request_assignments=handoff_request_assignments,
        now=lambda: 123.0,
        sync_world_runtime=sync_world_runtime,
    )

    assert order == ["handoff", "resume", "sync"]


def test_fulfill_unit_requests_does_not_assign_category_only_vehicle_when_hint_misses() -> None:
    req = UnitRequest(
        request_id="req_1",
        task_id="t1",
        task_label="001",
        task_summary="装甲推进",
        category="vehicle",
        count=1,
        urgency="high",
        hint="重坦",
        status="pending",
    )

    class _Actor:
        def __init__(self, actor_id: int, name: str, display_name: str) -> None:
            self.actor_id = actor_id
            self.name = name
            self.display_name = display_name
            self.category = type("Category", (), {"value": "vehicle"})()

    idle_actor = _Actor(21, "V2火箭车", "V2火箭车")
    bound: list[int] = []
    sync_calls: list[str] = []
    wake_calls: list[str] = []

    fulfill_unit_requests(
        unit_requests={"req_1": req},
        world_model=type("World", (), {"find_actors": lambda self, **kwargs: [idle_actor]})(),
        category_to_actor_category={"vehicle": "vehicle"},
        urgency_weight={"high": 3},
        task_priority_for=lambda task_id: 50,
        request_start_goal=lambda request: request.min_start_package,
        bind_actor_to_request=lambda request, actor: bound.append(actor.actor_id),
        reconcile_request_bootstrap=lambda request: None,
        wake_waiting_agent=lambda task_id: wake_calls.append(task_id),
        sync_world_runtime=lambda: sync_calls.append("sync"),
    )

    assert bound == []
    assert sync_calls == []
    assert wake_calls == ["t1"]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
