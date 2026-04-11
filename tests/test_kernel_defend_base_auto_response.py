"""Tests for kernel defend-base auto-response helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.defend_base_auto_response import (
    ensure_defend_base_task,
    resolve_defend_base_target_position,
)
from models import Event, EventType, Task, TaskKind, TaskStatus


def test_ensure_defend_base_task_reuses_existing_and_respects_cooldown() -> None:
    existing = Task(
        task_id="t_defend",
        raw_text="defend_base",
        kind=TaskKind.MANAGED,
        priority=80,
        status=TaskStatus.RUNNING,
        label="001",
    )
    created: list[Task] = []

    def create_task(raw_text: str, kind: TaskKind, priority: int) -> Task:
        task = Task(
            task_id="t_new",
            raw_text=raw_text,
            kind=kind,
            priority=priority,
            status=TaskStatus.RUNNING,
            label="002",
        )
        created.append(task)
        return task

    task, last_created = ensure_defend_base_task(
        [existing],
        last_created=100.0,
        now=105.0,
        cooldown_s=10.0,
        create_task=create_task,
    )
    assert task is existing
    assert last_created == 100.0
    assert created == []

    task, last_created = ensure_defend_base_task(
        [],
        last_created=100.0,
        now=105.0,
        cooldown_s=10.0,
        create_task=create_task,
    )
    assert task is None
    assert last_created == 100.0
    assert created == []

    task, last_created = ensure_defend_base_task(
        [],
        last_created=100.0,
        now=111.0,
        cooldown_s=10.0,
        create_task=create_task,
    )
    assert task == created[0]
    assert last_created == 111.0
    print("  PASS: ensure_defend_base_task_reuses_existing_and_respects_cooldown")


def test_resolve_defend_base_target_position_follows_fallback_order() -> None:
    world = SimpleNamespace(
        state=SimpleNamespace(
            actors={
                20: SimpleNamespace(position=(18, 10)),
            }
        ),
        find_actors=lambda owner, category=None: (
            [SimpleNamespace(position=(18, 10)), SimpleNamespace(position=(22, 14))]
            if category == "building"
            else [SimpleNamespace(position=(10, 10)), SimpleNamespace(position=(30, 30))]
        ),
    )

    assert resolve_defend_base_target_position(
        world,
        Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20, position=(40, 50)),
    ) == (40, 50)
    assert resolve_defend_base_target_position(
        world,
        Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20),
    ) == (18, 10)
    assert resolve_defend_base_target_position(
        world,
        Event(type=EventType.BASE_UNDER_ATTACK),
    ) == (20, 12)
    print("  PASS: resolve_defend_base_target_position_follows_fallback_order")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
