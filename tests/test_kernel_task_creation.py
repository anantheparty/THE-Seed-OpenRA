"""Tests for extracted kernel task creation helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.task_creation import (
    create_task,
    ensure_capability_task,
    inject_player_message,
    is_direct_managed,
)
from models import Task, TaskKind, TaskStatus


class _Agent:
    def __init__(self) -> None:
        self.runtime_facts_provider = None
        self.active_tasks_provider = None
        self.events = []

    def set_runtime_facts_provider(self, provider) -> None:
        self.runtime_facts_provider = provider

    def set_active_tasks_provider(self, provider) -> None:
        self.active_tasks_provider = provider

    def push_event(self, event) -> None:
        self.events.append(event)


@dataclass
class _Runtime:
    task: Task
    agent: _Agent
    tool_executor: object


class _WorldModel:
    def __init__(self) -> None:
        self.calls = []

    def compute_runtime_facts(self, task_id: str, *, include_buildable: bool):
        self.calls.append((task_id, include_buildable))
        return {"task_id": task_id, "include_buildable": include_buildable}


def test_create_task_registers_runtime_and_providers() -> None:
    tasks = {}
    task_runtimes = {}
    direct_managed_tasks = set()
    world_model = _WorldModel()
    sync_calls = []
    start_calls = []
    agent = _Agent()

    result = create_task(
        raw_text="侦察",
        kind=TaskKind.MANAGED,
        priority=50,
        info_subscriptions=["map"],
        skip_agent=False,
        task_seq=0,
        tasks=tasks,
        task_runtimes=task_runtimes,
        direct_managed_tasks=direct_managed_tasks,
        task_agent_factory=lambda task, tool_executor, jobs_provider, world_summary_provider: agent,
        build_tool_executor=lambda task: {"task_id": task.task_id},
        jobs_provider=lambda task_id: [],
        world_summary_provider=lambda: {},
        runtime_factory=lambda task, agent, tool_executor: _Runtime(task=task, agent=agent, tool_executor=tool_executor),
        maybe_start_agent=lambda runtime: start_calls.append(runtime.task.task_id),
        world_model=world_model,
        current_capability_task_id=lambda: None,
        other_active_tasks_for=lambda task_id: [{"task_id": task_id}],
        sync_world_runtime=lambda: sync_calls.append("sync"),
        gen_id=lambda prefix: f"{prefix}abc12345",
    )

    task = result.task
    assert result.task_seq == 1
    assert task.task_id == "t_abc12345"
    assert task.label == "001"
    assert tasks[task.task_id] is task
    assert task_runtimes[task.task_id].agent is agent
    assert sync_calls == ["sync"]
    assert start_calls == [task.task_id]
    assert agent.active_tasks_provider(task.task_id) == [{"task_id": task.task_id}]
    assert agent.runtime_facts_provider(task.task_id) == {
        "task_id": task.task_id,
        "include_buildable": False,
    }
    assert world_model.calls == [(task.task_id, False)]


def test_create_task_skip_agent_marks_direct_managed() -> None:
    tasks = {}
    task_runtimes = {}
    direct_managed_tasks = set()

    result = create_task(
        raw_text="直接任务",
        kind=TaskKind.MANAGED,
        priority=40,
        info_subscriptions=None,
        skip_agent=True,
        task_seq=1,
        tasks=tasks,
        task_runtimes=task_runtimes,
        direct_managed_tasks=direct_managed_tasks,
        task_agent_factory=lambda task, tool_executor, jobs_provider, world_summary_provider: _Agent(),
        build_tool_executor=lambda task: object(),
        jobs_provider=lambda task_id: [],
        world_summary_provider=lambda: {},
        runtime_factory=lambda task, agent, tool_executor: _Runtime(task=task, agent=agent, tool_executor=tool_executor),
        maybe_start_agent=lambda runtime: (_ for _ in ()).throw(AssertionError("should not start agent")),
        world_model=_WorldModel(),
        current_capability_task_id=lambda: None,
        other_active_tasks_for=lambda task_id: [],
        sync_world_runtime=lambda: None,
        gen_id=lambda prefix: f"{prefix}skip0001",
    )

    assert result.task.task_id in direct_managed_tasks
    assert is_direct_managed(result.task.task_id, direct_managed_tasks=direct_managed_tasks) is True


def test_ensure_capability_task_creates_and_reuses_running_task() -> None:
    tasks = {}
    created = []

    def create_task_fn(**kwargs):
        task = Task(
            task_id="t_cap",
            raw_text=kwargs["raw_text"],
            kind=kwargs["kind"],
            priority=kwargs["priority"],
            status=TaskStatus.RUNNING,
            label="001",
            info_subscriptions=list(kwargs["info_subscriptions"]),
        )
        tasks[task.task_id] = task
        created.append(task.task_id)
        return task

    result = ensure_capability_task(
        enable_capability_task=True,
        capability_task_id=None,
        tasks=tasks,
        create_task_fn=create_task_fn,
    )
    assert result.task_id == "t_cap"
    assert tasks["t_cap"].is_capability is True
    assert result.capability_recent_inputs == []

    reused = ensure_capability_task(
        enable_capability_task=True,
        capability_task_id="t_cap",
        tasks=tasks,
        create_task_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not recreate")),
    )
    assert reused.task_id == "t_cap"
    assert created == ["t_cap"]


def test_inject_player_message_updates_capability_recent_inputs() -> None:
    agent = _Agent()
    task = Task(
        task_id="t_cap",
        raw_text="经济",
        kind=TaskKind.MANAGED,
        priority=90,
        status=TaskStatus.RUNNING,
        label="001",
    )
    recent_inputs = [{"text": f"old-{i}", "timestamp": float(i)} for i in range(5)]
    sync_calls = []

    ok = inject_player_message(
        task_id="t_cap",
        text="新指令",
        tasks={"t_cap": task},
        task_runtimes={"t_cap": _Runtime(task=task, agent=agent, tool_executor=object())},
        direct_managed_tasks=set(),
        capability_task_id="t_cap",
        capability_recent_inputs=recent_inputs,
        sync_world_runtime=lambda: sync_calls.append("sync"),
        now=lambda: 10.0,
    )

    assert ok is True
    assert len(agent.events) == 1
    assert agent.events[0].data["text"] == "新指令"
    assert recent_inputs == [
        {"text": "old-1", "timestamp": 1.0},
        {"text": "old-2", "timestamp": 2.0},
        {"text": "old-3", "timestamp": 3.0},
        {"text": "old-4", "timestamp": 4.0},
        {"text": "新指令", "timestamp": 10.0},
    ]
    assert sync_calls == ["sync"]


def test_inject_player_message_rejects_direct_managed_task() -> None:
    task = Task(
        task_id="t1",
        raw_text="直接任务",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        label="001",
    )
    ok = inject_player_message(
        task_id="t1",
        text="hello",
        tasks={"t1": task},
        task_runtimes={},
        direct_managed_tasks={"t1"},
        capability_task_id=None,
        capability_recent_inputs=[],
        sync_world_runtime=lambda: None,
        now=lambda: 1.0,
    )
    assert ok is False
