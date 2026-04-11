"""Tests for kernel player interaction helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.player_interaction import (
    push_player_notification,
    register_task_message,
    submit_player_response,
    tick_question_timeouts,
)
from kernel.task_questions import PendingQuestionStore
from models import PlayerResponse, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus


def test_push_player_notification_records_payload() -> None:
    notifications: list[dict] = []

    push_player_notification(
        notification_type="info",
        content="hello",
        data={"a": 1},
        timestamp=None,
        player_notifications=notifications,
        now=lambda: 12.5,
    )

    assert notifications == [
        {
            "type": "info",
            "content": "hello",
            "data": {"a": 1},
            "timestamp": 12.5,
        }
    ]
    print("  PASS: push_player_notification_records_payload")


def test_register_task_message_rejects_terminal_task() -> None:
    task = Task(
        task_id="t_done",
        raw_text="done",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.SUCCEEDED,
        label="001",
    )
    message = TaskMessage(
        message_id="msg_done",
        task_id="t_done",
        type=TaskMessageType.TASK_INFO,
        content="ignored",
        priority=50,
    )
    messages: list[TaskMessage] = []
    question_store = PendingQuestionStore()

    ok = register_task_message(
        message,
        tasks={task.task_id: task},
        task_messages=messages,
        question_store=question_store,
    )

    assert ok is False
    assert messages == []
    print("  PASS: register_task_message_rejects_terminal_task")


def test_submit_player_response_delivers_runtime_reply() -> None:
    question_store = PendingQuestionStore()
    message = TaskMessage(
        message_id="msg_1",
        task_id="t_1",
        type=TaskMessageType.TASK_QUESTION,
        content="继续还是取消？",
        options=["继续", "取消"],
        timeout_s=5.0,
        default_option="取消",
        priority=80,
        timestamp=10.0,
    )
    question_store.register(message)
    delivered: dict[str, list[PlayerResponse]] = {}
    agent = SimpleNamespace(responses=[])
    agent.push_player_response = lambda response: agent.responses.append(response)
    runtimes = {
        "t_1": SimpleNamespace(agent=agent),
    }

    payload = submit_player_response(
        PlayerResponse(message_id="msg_1", task_id="t_1", answer="继续", timestamp=12.0),
        now=12.0,
        current_time=lambda: 999.0,
        question_store=question_store,
        delivered_player_responses=delivered,
        task_runtimes=runtimes,
    )

    assert payload["ok"] is True
    assert payload["status"] == "delivered"
    assert delivered["t_1"][0].answer == "继续"
    assert agent.responses[0].answer == "继续"
    print("  PASS: submit_player_response_delivers_runtime_reply")


def test_tick_question_timeouts_delivers_default_responses() -> None:
    question_store = PendingQuestionStore()
    message = TaskMessage(
        message_id="msg_timeout",
        task_id="t_1",
        type=TaskMessageType.TASK_QUESTION,
        content="继续还是取消？",
        options=["继续", "取消"],
        timeout_s=1.0,
        default_option="取消",
        priority=70,
        timestamp=10.0,
    )
    question_store.register(message)
    delivered: dict[str, list[PlayerResponse]] = {}
    agent = SimpleNamespace(responses=[])
    agent.push_player_response = lambda response: agent.responses.append(response)
    runtimes = {
        "t_1": SimpleNamespace(agent=agent),
    }

    expired = tick_question_timeouts(
        now=11.0,
        current_time=lambda: 999.0,
        question_store=question_store,
        delivered_player_responses=delivered,
        task_runtimes=runtimes,
    )

    assert expired == 1
    assert delivered["t_1"][0].answer == "取消"
    assert agent.responses[0].answer == "取消"
    print("  PASS: tick_question_timeouts_delivers_default_responses")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
