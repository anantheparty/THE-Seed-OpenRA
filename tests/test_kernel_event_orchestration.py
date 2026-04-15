"""Focused tests for kernel event orchestration helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.event_orchestration import handle_game_reset, route_runtime_event
from models import Event, EventType


class _Agent:
    def __init__(self) -> None:
        self.events = []
        self.stopped = False

    def push_event(self, event) -> None:
        self.events.append(event)

    def stop(self) -> None:
        self.stopped = True


def test_route_runtime_event_sends_low_power_to_capability_and_player_notification() -> None:
    cap_agent = _Agent()
    other_agent = _Agent()
    player_notifications: list[dict] = []
    task_runtimes = {
        "cap": SimpleNamespace(agent=cap_agent, task=SimpleNamespace(status="running")),
        "other": SimpleNamespace(agent=other_agent, task=SimpleNamespace(status="running")),
    }
    event = Event(
        type=EventType.LOW_POWER,
        data={"power_provided": 50, "power_drained": 120, "deficit": 70},
        timestamp=99.0,
    )

    route_runtime_event(
        event,
        apply_auto_response_rules=lambda event: None,
        handle_game_reset=lambda event: None,
        jobs={},
        task_runtimes=task_runtimes,
        world_model=SimpleNamespace(),
        is_terminal_job_status=lambda status: False,
        rebalance_resources=lambda: (_ for _ in ()).throw(AssertionError("should not rebalance")),
        sync_world_runtime=lambda: (_ for _ in ()).throw(AssertionError("should not sync")),
        capability_task_id="cap",
        player_notifications=player_notifications,
        fulfill_unit_requests=lambda: (_ for _ in ()).throw(AssertionError("should not fulfill")),
    )

    assert cap_agent.events == [event]
    assert other_agent.events == []
    assert player_notifications == [
        {
            "type": EventType.LOW_POWER.value,
            "content": "当前低电，部分生产与建筑能力会受影响",
            "data": {"power_provided": 50, "power_drained": 120, "deficit": 70},
            "timestamp": 99.0,
        }
    ]


def test_route_runtime_event_sends_base_under_attack_to_player_and_broadcasts() -> None:
    cap_agent = _Agent()
    other_agent = _Agent()
    player_notifications: list[dict] = []
    task_runtimes = {
        "cap": SimpleNamespace(agent=cap_agent, task=SimpleNamespace(status="running")),
        "other": SimpleNamespace(agent=other_agent, task=SimpleNamespace(status="running")),
    }
    event = Event(
        type=EventType.BASE_UNDER_ATTACK,
        actor_id=20,
        data={"attacker_count": 2},
        timestamp=123.0,
    )

    route_runtime_event(
        event,
        apply_auto_response_rules=lambda event: None,
        handle_game_reset=lambda event: None,
        jobs={},
        task_runtimes=task_runtimes,
        world_model=SimpleNamespace(),
        is_terminal_job_status=lambda status: False,
        rebalance_resources=lambda: (_ for _ in ()).throw(AssertionError("should not rebalance")),
        sync_world_runtime=lambda: (_ for _ in ()).throw(AssertionError("should not sync")),
        capability_task_id="cap",
        player_notifications=player_notifications,
        fulfill_unit_requests=lambda: (_ for _ in ()).throw(AssertionError("should not fulfill")),
    )

    assert cap_agent.events == [event]
    assert other_agent.events == [event]
    assert player_notifications == [
        {
            "type": EventType.BASE_UNDER_ATTACK.value,
            "content": "基地受到攻击，开始反击",
            "data": {"attacker_count": 2},
            "timestamp": 123.0,
        }
    ]


def test_route_runtime_event_production_complete_runs_rebalance_then_fulfill() -> None:
    calls = []
    event = Event(type=EventType.PRODUCTION_COMPLETE)

    route_runtime_event(
        event,
        apply_auto_response_rules=lambda event: calls.append("rules"),
        handle_game_reset=lambda event: calls.append("reset"),
        jobs={},
        task_runtimes={},
        world_model=SimpleNamespace(),
        is_terminal_job_status=lambda status: False,
        rebalance_resources=lambda: calls.append("rebalance"),
        sync_world_runtime=lambda: calls.append("sync"),
        capability_task_id=None,
        player_notifications=[],
        fulfill_unit_requests=lambda: calls.append("fulfill"),
    )

    assert calls == ["rules", "rebalance", "fulfill"]


def test_handle_game_reset_clears_and_notifies() -> None:
    task_runtimes = {"t1": SimpleNamespace(agent=_Agent())}
    tasks = {"t1": object()}
    jobs = {"j1": object()}
    constraints = {"c1": object()}
    resource_needs = {"j1": []}
    resource_loss_notified = {"j1"}
    player_notifications = [{"type": "old"}]
    task_messages = [object()]
    delivered_player_responses = {"t1": []}
    unit_requests = {"req": object()}
    unit_reservations = {"res": object()}
    request_reservations = {"req": "res"}
    task_actor_groups = {"t1": [10]}
    direct_managed_tasks = {"t1"}
    capability_recent_inputs = [{"text": "old"}]
    calls = []
    runtime_states = []
    capability_task_id = "cap_old"
    event = Event(type=EventType.GAME_RESET, data={"reason": "new game"}, timestamp=123.0)

    def set_capability_task_id(value):
        nonlocal capability_task_id
        capability_task_id = value

    handle_game_reset(
        event,
        task_runtimes=task_runtimes,
        tasks=tasks,
        jobs=jobs,
        constraints=constraints,
        resource_needs=resource_needs,
        resource_loss_notified=resource_loss_notified,
        player_notifications=player_notifications,
        task_messages=task_messages,
        reset_questions=lambda: calls.append("reset_questions"),
        delivered_player_responses=delivered_player_responses,
        unit_requests=unit_requests,
        unit_reservations=unit_reservations,
        request_reservations=request_reservations,
        task_actor_groups=task_actor_groups,
        direct_managed_tasks=direct_managed_tasks,
        capability_recent_inputs=capability_recent_inputs,
        stop_task_runtime_fn=lambda runtimes, task_id: (calls.append(f"stop:{task_id}"), runtimes.pop(task_id, None)),
        set_capability_task_id=set_capability_task_id,
        set_runtime_state=lambda **kwargs: runtime_states.append(kwargs),
        push_player_notification=lambda notification_type, content, **kwargs: calls.append((notification_type, content, kwargs)),
        ensure_capability_task=lambda: calls.append("ensure_capability"),
    )

    assert task_runtimes == {}
    assert tasks == {}
    assert jobs == {}
    assert constraints == {}
    assert resource_needs == {}
    assert resource_loss_notified == set()
    assert task_messages == []
    assert delivered_player_responses == {}
    assert unit_requests == {}
    assert unit_reservations == {}
    assert request_reservations == {}
    assert task_actor_groups == {}
    assert direct_managed_tasks == set()
    assert capability_recent_inputs == []
    assert capability_task_id is None
    assert runtime_states == [{
        "active_tasks": {},
        "active_jobs": {},
        "resource_bindings": {},
        "constraints": [],
        "capability_status": {},
        "unit_reservations": [],
    }]
    assert calls[0] == "stop:t1"
    assert "reset_questions" in calls
    assert ("game_reset", "检测到对局已重置，已清理旧任务状态", {"data": {"reason": "new game"}, "timestamp": 123.0}) in calls
    assert "ensure_capability" in calls

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
