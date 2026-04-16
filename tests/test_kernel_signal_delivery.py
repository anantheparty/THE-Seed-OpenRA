"""Tests for kernel expert signal delivery helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.signal_delivery import route_expert_signal
from models import ExpertSignal, SignalKind, TaskMessageType, TaskStatus


def test_route_expert_signal_registers_blocked_warning_and_pushes() -> None:
    agent = SimpleNamespace(signals=[])
    agent.push_signal = lambda signal: agent.signals.append(signal)
    messages = []
    signal = ExpertSignal(task_id="t_1", job_id="j_1", kind=SignalKind.BLOCKED, summary="电力不足")

    handled = route_expert_signal(
        signal,
        tasks={"t_1": SimpleNamespace(status=TaskStatus.RUNNING, priority=50)},
        task_runtimes={"t_1": SimpleNamespace(agent=agent)},
        is_direct_managed=lambda task_id: False,
        register_task_message=lambda message: messages.append(message) or True,
        complete_task=lambda *args: True,
        gen_message_id=lambda prefix: f"{prefix}1",
    )

    assert handled is True
    assert len(messages) == 1
    assert messages[0].content == "电力不足"
    assert agent.signals == [signal]
    print("  PASS: route_expert_signal_registers_blocked_warning_and_pushes")


def test_route_expert_signal_auto_completes_direct_managed_tasks() -> None:
    completed = []
    signal = ExpertSignal(
        task_id="t_1",
        job_id="j_1",
        kind=SignalKind.TASK_COMPLETE,
        summary="直接任务完成",
        result="aborted",
    )

    handled = route_expert_signal(
        signal,
        tasks={"t_1": SimpleNamespace(status=TaskStatus.RUNNING, priority=50)},
        task_runtimes={"t_1": SimpleNamespace(agent=SimpleNamespace(push_signal=lambda signal: None))},
        is_direct_managed=lambda task_id: True,
        register_task_message=lambda message: True,
        complete_task=lambda task_id, result, summary: completed.append((task_id, result, summary)) or True,
        gen_message_id=lambda prefix: f"{prefix}1",
    )

    assert handled is True
    assert completed == [("t_1", "failed", "直接任务完成")]
    print("  PASS: route_expert_signal_auto_completes_direct_managed_tasks")


def test_route_expert_signal_surfaces_explicit_movement_resource_loss_as_warning() -> None:
    agent = SimpleNamespace(signals=[])
    agent.push_signal = lambda signal: agent.signals.append(signal)
    messages = []
    signal = ExpertSignal(
        task_id="t_1",
        job_id="j_1",
        kind=SignalKind.RESOURCE_LOST,
        summary="Missing 3 actor resource(s); waiting for replacement | group=2/5 | missing=3",
        data={
            "source_expert": "MovementExpert",
            "explicit_group": True,
            "requested_total": 5,
            "bound_count": 2,
            "missing_count": 3,
        },
    )

    handled = route_expert_signal(
        signal,
        tasks={"t_1": SimpleNamespace(status=TaskStatus.RUNNING, priority=50)},
        task_runtimes={"t_1": SimpleNamespace(agent=agent)},
        is_direct_managed=lambda task_id: False,
        register_task_message=lambda message: messages.append(message) or True,
        complete_task=lambda *args: True,
        gen_message_id=lambda prefix: f"{prefix}1",
    )

    assert handled is True
    assert len(messages) == 1
    assert messages[0].type == TaskMessageType.TASK_WARNING
    assert "group=2/5" in messages[0].content
    assert agent.signals == [signal]
    print("  PASS: route_expert_signal_surfaces_explicit_movement_resource_loss_as_warning")


def test_route_expert_signal_surfaces_explicit_movement_progress_as_info() -> None:
    agent = SimpleNamespace(signals=[])
    agent.push_signal = lambda signal: agent.signals.append(signal)
    messages = []
    signal = ExpertSignal(
        task_id="t_1",
        job_id="j_1",
        kind=SignalKind.PROGRESS,
        summary="Moving 2 units to (46, 56) | group=2/5 | missing=3",
        data={
            "source_expert": "MovementExpert",
            "explicit_group": True,
            "requested_total": 5,
            "bound_count": 2,
            "missing_count": 3,
        },
    )

    handled = route_expert_signal(
        signal,
        tasks={"t_1": SimpleNamespace(status=TaskStatus.RUNNING, priority=50)},
        task_runtimes={"t_1": SimpleNamespace(agent=agent)},
        is_direct_managed=lambda task_id: False,
        register_task_message=lambda message: messages.append(message) or True,
        complete_task=lambda *args: True,
        gen_message_id=lambda prefix: f"{prefix}1",
    )

    assert handled is True
    assert len(messages) == 1
    assert messages[0].type == TaskMessageType.TASK_INFO
    assert "missing=3" in messages[0].content
    assert agent.signals == [signal]
    print("  PASS: route_expert_signal_surfaces_explicit_movement_progress_as_info")


def test_route_expert_signal_ignores_terminal_or_missing_task() -> None:
    signal = ExpertSignal(task_id="t_1", job_id="j_1", kind=SignalKind.PROGRESS, summary="halfway")

    assert route_expert_signal(
        signal,
        tasks={"t_1": SimpleNamespace(status=TaskStatus.SUCCEEDED, priority=50)},
        task_runtimes={"t_1": SimpleNamespace(agent=SimpleNamespace(push_signal=lambda signal: None))},
        is_direct_managed=lambda task_id: False,
        register_task_message=lambda message: True,
        complete_task=lambda *args: True,
        gen_message_id=lambda prefix: f"{prefix}1",
    ) is False
    print("  PASS: route_expert_signal_ignores_terminal_or_missing_task")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
