"""Tests for Adjutant — mock LLM, Kernel, WorldModel covering all 3 input paths."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from llm import LLMResponse, MockProvider
from models import PlayerResponse, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus
from adjutant import (
    Adjutant, AdjutantConfig, InputType,
    NotificationManager, format_notification, notification_to_text, notification_to_dict,
)


# --- Mocks ---

class MockTask:
    def __init__(self, task_id, raw_text, status="running"):
        self.task_id = task_id
        self.raw_text = raw_text
        self.status = type("S", (), {"value": status})()
        self.kind = TaskKind.MANAGED
        self.priority = 50
        self.created_at = time.time()
        self.timestamp = time.time()


class MockKernel:
    def __init__(self):
        self.created_tasks: list[dict] = []
        self.submitted_responses: list[PlayerResponse] = []
        self._pending_questions: list[dict] = []
        self._tasks: list[MockTask] = []
        self._task_counter = 0

    def create_task(self, raw_text, kind, priority):
        self._task_counter += 1
        task = MockTask(f"t_{self._task_counter}", raw_text)
        self.created_tasks.append({"raw_text": raw_text, "kind": kind, "priority": priority})
        self._tasks.append(task)
        return task

    def submit_player_response(self, response, *, now=None):
        self.submitted_responses.append(response)
        return {"ok": True, "status": "delivered"}

    def list_pending_questions(self):
        # Real Kernel sorts by priority descending
        return sorted(self._pending_questions, key=lambda q: q.get("priority", 0), reverse=True)

    def list_tasks(self):
        return list(self._tasks)

    def add_pending_question(self, message_id, task_id, question, options, priority=50):
        self._pending_questions.append({
            "message_id": message_id,
            "task_id": task_id,
            "question": question,
            "options": options,
            "default_option": options[0] if options else None,
            "priority": priority,
            "asked_at": time.time(),
            "timeout_s": 30.0,
        })


class MockWorldModel:
    def world_summary(self):
        return {
            "economy": {"cash": 5000, "income": 200},
            "military": {"self_units": 15, "enemy_units": 8, "self_combat_value": 2500},
            "map": {"explored_pct": 0.45},
            "known_enemy": {"units_spotted": 8, "bases": 1},
            "timestamp": time.time(),
        }

    def query(self, query_type, params=None):
        return {"data": [], "timestamp": time.time()}


# --- Tests ---

def test_command_classification():
    """New command input is routed to Kernel.create_task."""
    # LLM classifies as command
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.95}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("生产5辆坦克")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert "task_id" in result

    asyncio.run(run())

    assert len(kernel.created_tasks) == 1
    assert kernel.created_tasks[0]["raw_text"] == "生产5辆坦克"
    print("  PASS: command_classification")


def test_reply_classification():
    """Reply to pending question is routed to Kernel.submit_player_response."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "包围右边基地"))
    kernel.add_pending_question("msg_1", "t1", "兵力不足，继续还是放弃？", ["继续", "放弃"], priority=60)

    # LLM classifies as reply matching msg_1
    mock_llm = MockProvider(responses=[
        LLMResponse(
            text='{"type":"reply","target_message_id":"msg_1","target_task_id":"t1","confidence":0.9}',
            model="mock",
        ),
    ])
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("继续")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())

    assert len(kernel.submitted_responses) == 1
    assert kernel.submitted_responses[0].message_id == "msg_1"
    assert kernel.submitted_responses[0].task_id == "t1"
    assert kernel.submitted_responses[0].answer == "继续"
    print("  PASS: reply_classification")


def test_reply_fallback_highest_priority():
    """Ambiguous reply without target_message_id matches highest-priority question."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续", "放弃"], priority=60)

    # LLM classifies as reply but no specific target
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","confidence":0.7}', model="mock"),
    ])
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("放弃")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())

    # Should match highest-priority question (msg_high, priority=60)
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].answer == "放弃"
    print("  PASS: reply_fallback_highest_priority")


def test_query_classification():
    """Query input gets direct LLM+WorldModel answer, no Task created."""
    # First call: classification. Second call: query answer.
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"query","confidence":0.95}', model="mock"),
        LLMResponse(text="当前经济良好(cash:5000)，兵力优势(15 vs 8)，建议进攻", model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is True
        assert "经济" in result["response_text"] or "兵力" in result["response_text"]

    asyncio.run(run())

    # No task created
    assert len(kernel.created_tasks) == 0
    print("  PASS: query_classification")


def test_classification_failure_defaults_to_command():
    """If LLM classification fails, defaults to command."""
    class FailingLLM(MockProvider):
        call_count = 0
        async def chat(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError("classification failed")
            return LLMResponse(text="ok", model="mock")

    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=FailingLLM(), kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("探索地图")
        assert result["type"] == "command"

    asyncio.run(run())
    assert len(kernel.created_tasks) == 1
    print("  PASS: classification_failure_defaults_to_command")


def test_task_message_formatting():
    """TaskMessage formatting works for all types in text and card mode."""
    info = TaskMessage(
        message_id="m1", task_id="t1", type=TaskMessageType.TASK_INFO,
        content="已找到敌人基地 (1820,430)",
    )
    warning = TaskMessage(
        message_id="m2", task_id="t1", type=TaskMessageType.TASK_WARNING,
        content="侦察兵血量低",
    )
    question = TaskMessage(
        message_id="m3", task_id="t1", type=TaskMessageType.TASK_QUESTION,
        content="兵力不足，继续进攻还是放弃？", options=["继续", "放弃"],
        timeout_s=30.0, default_option="放弃",
    )
    complete = TaskMessage(
        message_id="m4", task_id="t1", type=TaskMessageType.TASK_COMPLETE_REPORT,
        content="包围成功，敌人基地已摧毁",
    )

    # Text mode
    assert "[任务 t1]" in Adjutant.format_task_message(info, "text")
    assert "⚠" in Adjutant.format_task_message(warning, "text")
    assert "❓" in Adjutant.format_task_message(question, "text")
    assert "继续 / 放弃" in Adjutant.format_task_message(question, "text")
    assert "✓" in Adjutant.format_task_message(complete, "text")

    # Card mode (JSON)
    card = json.loads(Adjutant.format_task_message(question, "card"))
    assert card["type"] == "task_question"
    assert card["options"] == ["继续", "放弃"]
    assert card["timeout_s"] == 30.0
    print("  PASS: task_message_formatting")


def test_dialogue_history():
    """Dialogue history is recorded and trimmed."""
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(
        llm=mock_llm, kernel=kernel, world_model=wm,
        config=AdjutantConfig(max_dialogue_history=3),
    )

    async def run():
        await adjutant.handle_player_input("第一条")
        await adjutant.handle_player_input("第二条")
        assert len(adjutant._dialogue_history) == 4  # 2 player + 2 adjutant

    asyncio.run(run())
    print("  PASS: dialogue_history")


def test_notification_formatting():
    """Notifications are formatted with correct icon and severity."""
    raw = {"type": "ENEMY_EXPANSION", "content": "发现敌人在(1200,300)扩张", "data": {"pos": [1200, 300]}, "timestamp": 100.0}
    formatted = format_notification(raw)
    assert formatted.severity == "info"
    assert formatted.icon == "🔍"
    assert formatted.content == "发现敌人在(1200,300)扩张"
    assert formatted.data["pos"] == [1200, 300]

    text = notification_to_text(formatted)
    assert "🔍" in text
    assert "扩张" in text

    d = notification_to_dict(formatted)
    assert d["type"] == "ENEMY_EXPANSION"
    assert d["severity"] == "info"
    assert "timestamp" in d

    # Warning type
    raw_warn = {"type": "FRONTLINE_WEAK", "content": "我方前线空虚", "data": {}, "timestamp": 101.0}
    warn = format_notification(raw_warn)
    assert warn.severity == "warning"
    assert warn.icon == "⚠"
    print("  PASS: notification_formatting")


def test_notification_manager_poll_and_push():
    """NotificationManager polls new notifications and pushes via sink."""
    pushed: list[dict] = []

    class MockNotifKernel:
        def __init__(self):
            self._notifications: list[dict] = []

        def list_player_notifications(self):
            return list(self._notifications)

        def add(self, ntype, content, data=None):
            self._notifications.append({"type": ntype, "content": content, "data": data or {}, "timestamp": time.time()})

    kernel = MockNotifKernel()

    async def sink(notification):
        pushed.append(notification)

    manager = NotificationManager(kernel=kernel, sink=sink)

    async def run():
        # No notifications yet
        result = await manager.poll_and_push()
        assert result == []
        assert pushed == []

        # Add 2 notifications
        kernel.add("ENEMY_EXPANSION", "发现敌人扩张")
        kernel.add("FRONTLINE_WEAK", "前线空虚")

        result = await manager.poll_and_push()
        assert len(result) == 2
        assert len(pushed) == 2
        assert pushed[0]["type"] == "ENEMY_EXPANSION"
        assert pushed[1]["type"] == "FRONTLINE_WEAK"

        # Poll again — no new ones
        result = await manager.poll_and_push()
        assert result == []
        assert len(pushed) == 2  # No duplicates

        # Add one more
        kernel.add("ECONOMY_SURPLUS", "经济充裕")
        result = await manager.poll_and_push()
        assert len(result) == 1
        assert len(pushed) == 3
        assert pushed[2]["type"] == "ECONOMY_SURPLUS"

        assert manager.total_pushed == 3

    asyncio.run(run())
    print("  PASS: notification_manager_poll_and_push")


def test_notification_manager_no_sink():
    """NotificationManager works without a sink (just tracks history)."""
    class MockNotifKernel:
        def list_player_notifications(self):
            return [{"type": "FRONTLINE_WEAK", "content": "弱", "data": {}, "timestamp": 100.0}]

    manager = NotificationManager(kernel=MockNotifKernel(), sink=None)

    async def run():
        result = await manager.poll_and_push()
        assert len(result) == 1
        assert len(manager.history) == 1

    asyncio.run(run())
    print("  PASS: notification_manager_no_sink")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Adjutant tests...\n")

    test_command_classification()
    test_reply_classification()
    test_reply_fallback_highest_priority()
    test_query_classification()
    test_classification_failure_defaults_to_command()
    test_task_message_formatting()
    test_dialogue_history()
    test_notification_formatting()
    test_notification_manager_poll_and_push()
    test_notification_manager_no_sink()

    print(f"\nAll 10 tests passed!")
