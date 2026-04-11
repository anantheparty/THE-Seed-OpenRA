"""Tests for kernel read-only query helpers."""

from __future__ import annotations

import pytest
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.query_views import (
    active_jobs,
    get_task_agent,
    jobs_for_task,
    list_jobs,
    list_player_notifications,
    list_task_messages,
    list_tasks,
    runtime_state,
)
from models import JobStatus, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus


class _Controller:
    def __init__(self, job_id: str, task_id: str, status: JobStatus) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self.status = status

    def to_model(self) -> SimpleNamespace:
        return SimpleNamespace(job_id=self.job_id, task_id=self.task_id, status=self.status)


def test_get_task_agent_returns_runtime_agent_or_none() -> None:
    agent = object()
    assert get_task_agent("t_1", task_runtimes={"t_1": SimpleNamespace(agent=agent)}) is agent
    assert get_task_agent("missing", task_runtimes={}) is None
    print("  PASS: get_task_agent_returns_runtime_agent_or_none")


def test_job_queries_preserve_sorting_and_terminal_filter() -> None:
    controllers = [
        _Controller("j_2", "t_1", JobStatus.RUNNING),
        _Controller("j_1", "t_1", JobStatus.WAITING),
        _Controller("j_3", "t_2", JobStatus.SUCCEEDED),
    ]

    task_jobs = jobs_for_task("t_1", jobs=controllers)
    active = active_jobs(
        jobs=controllers,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
    )
    all_jobs = list_jobs(jobs=controllers)

    assert [job.job_id for job in task_jobs] == ["j_1", "j_2"]
    assert [job.job_id for job in active] == ["j_1", "j_2"]
    assert [job.job_id for job in all_jobs] == ["j_1", "j_2", "j_3"]
    print("  PASS: job_queries_preserve_sorting_and_terminal_filter")


def test_task_message_and_runtime_views_preserve_copy_semantics() -> None:
    older = Task(
        task_id="t_old",
        raw_text="older",
        kind=TaskKind.MANAGED,
        priority=10,
        status=TaskStatus.RUNNING,
        label="001",
        created_at=1.0,
    )
    newer = Task(
        task_id="t_new",
        raw_text="newer",
        kind=TaskKind.MANAGED,
        priority=20,
        status=TaskStatus.RUNNING,
        label="002",
        created_at=2.0,
    )
    messages = [
        TaskMessage(
            message_id="m1",
            task_id="t_old",
            type=TaskMessageType.TASK_INFO,
            content="old",
        ),
        TaskMessage(
            message_id="m2",
            task_id="t_new",
            type=TaskMessageType.TASK_INFO,
            content="new",
        ),
    ]
    notifications = [{"type": "info"}]
    wm = SimpleNamespace(runtime_state=lambda: {"active_tasks": {"t_new": {}}})

    assert [task.task_id for task in list_tasks(tasks=[newer, older])] == ["t_old", "t_new"]
    assert [msg.message_id for msg in list_task_messages("t_new", task_messages=messages)] == ["m2"]
    copied_notifications = list_player_notifications(player_notifications=notifications)
    copied_notifications.append({"type": "new"})
    assert notifications == [{"type": "info"}]
    state = runtime_state(world_model=wm)
    state["mutated"] = True
    assert wm.runtime_state() == {"active_tasks": {"t_new": {}}}
    print("  PASS: task_message_and_runtime_views_preserve_copy_semantics")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
