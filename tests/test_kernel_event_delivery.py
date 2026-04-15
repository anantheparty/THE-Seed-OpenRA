"""Tests for kernel event delivery helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.event_delivery import (
    append_player_notification,
    broadcast_event,
    deliver_player_response,
)
from models import Event, EventType, PlayerResponse, TaskStatus


def test_append_player_notification_maps_known_events() -> None:
    notifications: list[dict] = []
    append_player_notification(
        notifications,
        Event(type=EventType.FRONTLINE_WEAK, timestamp=12.5),
    )
    assert notifications == [
        {
            "type": EventType.FRONTLINE_WEAK.value,
            "content": "我方前线空虚",
            "data": {},
            "timestamp": 12.5,
        }
    ]
    print("  PASS: append_player_notification_maps_known_events")


def test_append_player_notification_maps_low_power() -> None:
    notifications: list[dict] = []
    append_player_notification(
        notifications,
        Event(
            type=EventType.LOW_POWER,
            data={"power_provided": 50, "power_drained": 120, "deficit": 70},
            timestamp=14.0,
        ),
    )
    assert notifications == [
        {
            "type": EventType.LOW_POWER.value,
            "content": "当前低电，部分生产与建筑能力会受影响",
            "data": {"power_provided": 50, "power_drained": 120, "deficit": 70},
            "timestamp": 14.0,
        }
    ]
    print("  PASS: append_player_notification_maps_low_power")


def test_append_player_notification_maps_base_under_attack() -> None:
    notifications: list[dict] = []
    append_player_notification(
        notifications,
        Event(
            type=EventType.BASE_UNDER_ATTACK,
            actor_id=20,
            data={"attacker_count": 3},
            timestamp=42.0,
        ),
    )

    assert notifications == [
        {
            "type": EventType.BASE_UNDER_ATTACK.value,
            "content": "基地受到攻击，开始反击",
            "data": {"attacker_count": 3},
            "timestamp": 42.0,
        }
    ]
    print("  PASS: append_player_notification_maps_base_under_attack")


def test_broadcast_event_skips_terminal_tasks() -> None:
    running_agent = SimpleNamespace(events=[])
    running_agent.push_event = lambda event: running_agent.events.append(event)
    done_agent = SimpleNamespace(events=[])
    done_agent.push_event = lambda event: done_agent.events.append(event)
    runtimes = {
        "t_run": SimpleNamespace(task=SimpleNamespace(status=TaskStatus.RUNNING), agent=running_agent),
        "t_done": SimpleNamespace(task=SimpleNamespace(status=TaskStatus.SUCCEEDED), agent=done_agent),
    }

    event = Event(type=EventType.ENEMY_DISCOVERED, actor_id=201)
    broadcast_event(event, task_runtimes=runtimes)

    assert running_agent.events == [event]
    assert done_agent.events == []
    print("  PASS: broadcast_event_skips_terminal_tasks")


def test_deliver_player_response_records_and_pushes() -> None:
    delivered: dict[str, list[PlayerResponse]] = {}
    agent = SimpleNamespace(responses=[])
    agent.push_player_response = lambda response: agent.responses.append(response)
    runtimes = {
        "t_1": SimpleNamespace(task=SimpleNamespace(status=TaskStatus.RUNNING), agent=agent),
    }
    response = PlayerResponse(message_id="msg_1", task_id="t_1", answer="继续", timestamp=10.0)

    deliver_player_response(delivered, runtimes, response)

    assert delivered == {"t_1": [response]}
    assert agent.responses == [response]
    print("  PASS: deliver_player_response_records_and_pushes")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
