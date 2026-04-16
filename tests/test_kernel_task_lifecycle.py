"""Tests for extracted kernel task lifecycle helpers."""

from __future__ import annotations

import pytest
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.task_lifecycle import cancel_task, cancel_tasks, complete_task, task_matches_filters
from models import JobStatus, Task, TaskKind, TaskMessageType, TaskStatus


@dataclass
class _Job:
    job_id: str
    task_id: str
    status: JobStatus


@dataclass
class _Request:
    request_id: str
    task_id: str
    status: str


class _QuestionStore:
    def __init__(self) -> None:
        self.closed: list[str] = []

    def close_for_task(self, task_id: str) -> None:
        self.closed.append(task_id)


def test_task_matches_filters_respects_current_kernel_fields() -> None:
    task = Task(
        task_id="t1",
        raw_text="侦察",
        kind=TaskKind.MANAGED,
        priority=60,
        status=TaskStatus.RUNNING,
        label="001",
    )

    assert task_matches_filters(task, {"task_ids": ["t1"], "kind": "managed", "priority_below": 70, "status": "running"})
    assert task_matches_filters(task, {"task_ids": ["t2"]}) is False
    assert task_matches_filters(task, {"kind": "instant"}) is False
    assert task_matches_filters(task, {"priority_below": 60}) is False
    assert task_matches_filters(task, {"status": "pending"}) is False


def test_cancel_task_cleans_runtime_and_requests() -> None:
    task = Task(
        task_id="t1",
        raw_text="进攻",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.RUNNING,
        label="001",
    )
    tasks = {"t1": task}
    jobs = {
        "job_live": _Job(job_id="job_live", task_id="t1", status=JobStatus.RUNNING),
        "job_done": _Job(job_id="job_done", task_id="t1", status=JobStatus.SUCCEEDED),
    }
    requests = {
        "req_live": _Request(request_id="req_live", task_id="t1", status="pending"),
        "req_other": _Request(request_id="req_other", task_id="t2", status="pending"),
    }
    question_store = _QuestionStore()
    task_actor_groups = {"t1": [10, 11]}
    task_runtimes = {"t1": object()}
    calls: list[str] = []

    def abort_job(job_id: str) -> bool:
        calls.append(f"abort:{job_id}:{task.status.value}")
        return True

    assert cancel_task(
        task_id="t1",
        tasks=tasks,
        jobs=jobs,
        unit_requests=requests,
        task_actor_groups=task_actor_groups,
        task_runtimes=task_runtimes,
        question_store=question_store,
        abort_job=abort_job,
        release_task_job_resources=lambda task_id: calls.append(f"release:{task_id}"),
        cancel_unit_request=lambda request_id: calls.append(f"cancel_req:{request_id}") or True,
        stop_task_runtime=lambda runtimes, task_id: calls.append(f"stop:{task_id}") or runtimes.pop(task_id, None),
        sync_world_runtime=lambda: calls.append("sync"),
        now=lambda: 123.0,
    )

    assert task.status == TaskStatus.ABORTED
    assert task.timestamp == 123.0
    assert question_store.closed == ["t1"]
    assert "t1" not in task_actor_groups
    assert "t1" not in task_runtimes
    assert calls == ["abort:job_live:aborted", "release:t1", "cancel_req:req_live", "stop:t1", "sync"]


def test_cancel_tasks_uses_filter_and_counts_successes() -> None:
    tasks = {
        "t1": Task(task_id="t1", raw_text="侦察", kind=TaskKind.MANAGED, priority=50, status=TaskStatus.RUNNING, label="001"),
        "t2": Task(task_id="t2", raw_text="建设", kind=TaskKind.MANAGED, priority=80, status=TaskStatus.RUNNING, label="002"),
    }
    cancelled: list[str] = []

    count = cancel_tasks(
        filters={"priority_below": 70},
        tasks=tasks,
        cancel_task_fn=lambda task_id: cancelled.append(task_id) or True,
    )

    assert count == 1
    assert cancelled == ["t1"]


def test_complete_task_records_report_before_stop_runtime() -> None:
    task = Task(
        task_id="t1",
        raw_text="完成任务",
        kind=TaskKind.MANAGED,
        priority=70,
        status=TaskStatus.RUNNING,
        label="001",
    )
    tasks = {"t1": task}
    jobs = {"job_live": _Job(job_id="job_live", task_id="t1", status=JobStatus.RUNNING)}
    question_store = _QuestionStore()
    task_messages = []
    task_actor_groups = {"t1": [42]}
    task_runtimes = {"t1": object()}
    calls: list[str] = []

    def stop_runtime(runtimes, task_id):
        assert len(task_messages) == 1
        assert task_messages[0].type == TaskMessageType.TASK_COMPLETE_REPORT
        calls.append(f"stop:{task_id}")
        runtimes.pop(task_id, None)

    assert complete_task(
        task_id="t1",
        result="succeeded",
        summary="ok",
        tasks=tasks,
        jobs=jobs,
        task_messages=task_messages,
        task_actor_groups=task_actor_groups,
        task_runtimes=task_runtimes,
        question_store=question_store,
        abort_job=lambda job_id: calls.append(f"abort:{job_id}") or True,
        release_task_job_resources=lambda task_id: calls.append(f"release:{task_id}"),
        stop_task_runtime=stop_runtime,
        sync_world_runtime=lambda: calls.append("sync"),
        now=lambda: 456.0,
        gen_id=lambda prefix: f"{prefix}done",
    )

    assert task.status == TaskStatus.SUCCEEDED
    assert task.timestamp == 456.0
    assert question_store.closed == ["t1"]
    assert "t1" not in task_actor_groups
    assert "t1" not in task_runtimes
    assert task_messages[0].content == "ok"
    assert calls == ["abort:job_live", "release:t1", "stop:t1", "sync"]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
