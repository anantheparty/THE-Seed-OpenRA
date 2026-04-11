"""Tests for extracted kernel job lifecycle helpers."""

from __future__ import annotations

import pytest
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.job_lifecycle import (
    abort_job,
    pause_job,
    patch_job,
    require_job,
    resume_job,
    start_job,
)
from models import JobStatus, ReconJobConfig, ResourceKind, ResourceNeed, Task, TaskKind, TaskStatus


@dataclass
class _Controller:
    job_id: str
    task_id: str
    expert_type: str
    config: ReconJobConfig
    status: JobStatus = JobStatus.WAITING
    resources: list[str] = None  # type: ignore[assignment]
    patched: list[dict] = None  # type: ignore[assignment]
    aborted: bool = False
    paused: bool = False
    resumed: bool = False

    def __post_init__(self) -> None:
        if self.resources is None:
            self.resources = []
        if self.patched is None:
            self.patched = []

    def to_model(self):
        return type("JobModel", (), {"job_id": self.job_id, "status": self.status})()

    def abort(self) -> None:
        self.aborted = True
        self.status = JobStatus.ABORTED

    def patch(self, params: dict) -> None:
        self.patched.append(params)
        for key, value in params.items():
            setattr(self.config, key, value)

    def pause(self) -> None:
        self.paused = True
        self.status = JobStatus.WAITING

    def resume(self) -> None:
        self.resumed = True
        self.status = JobStatus.RUNNING


def test_require_job_raises_for_unknown_id() -> None:
    try:
        require_job("missing", jobs={})
    except KeyError as exc:
        assert "Unknown job_id" in str(exc)
    else:
        raise AssertionError("require_job should raise for missing jobs")


def test_start_patch_abort_pause_resume_job_lifecycle() -> None:
    task = Task(
        task_id="t1",
        raw_text="侦察",
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus.PENDING,
        label="001",
    )
    tasks = {"t1": task}
    jobs: dict[str, _Controller] = {}
    resource_needs: dict[str, list[ResourceNeed]] = {}
    resource_loss_notified = {"job_1"}
    calls: list[str] = []

    def make_job_controller(task_id: str, expert_type: str, config: ReconJobConfig) -> _Controller:
        return _Controller(job_id="job_1", task_id=task_id, expert_type=expert_type, config=config)

    config = ReconJobConfig(
        search_region="enemy_half",
        target_type="base",
        target_owner="enemy",
    )

    job = start_job(
        task_id="t1",
        expert_type="ReconExpert",
        config=config,
        tasks=tasks,
        jobs=jobs,
        resource_needs=resource_needs,
        make_job_controller=make_job_controller,
        now=lambda: 123.0,
        rebalance_resources=lambda: calls.append("rebalance"),
        sync_world_runtime=lambda: calls.append("sync"),
    )

    assert job.job_id == "job_1"
    assert task.status == TaskStatus.RUNNING
    assert task.timestamp == 123.0
    need = resource_needs["job_1"][0]
    assert need.kind == ResourceKind.ACTOR
    assert need.count == 1
    assert need.predicates == {"owner": "self"}
    assert need.job_id == "job_1"
    assert calls == ["rebalance", "sync"]

    calls.clear()
    assert patch_job(
        job_id="job_1",
        params={"search_region": "full_map"},
        jobs=jobs,
        resource_needs=resource_needs,
        rebalance_resources=lambda: calls.append("rebalance"),
        sync_world_runtime=lambda: calls.append("sync"),
    )
    assert jobs["job_1"].patched == [{"search_region": "full_map"}]
    assert jobs["job_1"].config.search_region == "full_map"
    assert calls == ["rebalance", "sync"]

    calls.clear()
    assert pause_job(
        job_id="job_1",
        jobs=jobs,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        sync_world_runtime=lambda: calls.append("sync"),
    )
    assert jobs["job_1"].paused is True
    assert calls == ["sync"]

    calls.clear()
    assert resume_job(
        job_id="job_1",
        jobs=jobs,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        rebalance_resources=lambda: calls.append("rebalance"),
        sync_world_runtime=lambda: calls.append("sync"),
    )
    assert jobs["job_1"].resumed is True
    assert calls == ["rebalance", "sync"]

    calls.clear()
    assert abort_job(
        job_id="job_1",
        jobs=jobs,
        resource_loss_notified=resource_loss_notified,
        release_job_resources=lambda controller: calls.append(f"release:{controller.job_id}"),
        rebalance_resources=lambda: calls.append("rebalance"),
        sync_world_runtime=lambda: calls.append("sync"),
    )
    assert jobs["job_1"].aborted is True
    assert "job_1" not in resource_loss_notified
    assert calls == ["release:job_1", "rebalance", "sync"]


def test_pause_resume_terminal_job_return_false() -> None:
    config = ReconJobConfig(
        search_region="enemy_half",
        target_type="base",
        target_owner="enemy",
    )
    controller = _Controller(
        job_id="job_done",
        task_id="t1",
        expert_type="ReconExpert",
        config=config,
        status=JobStatus.SUCCEEDED,
    )

    assert pause_job(
        job_id="job_done",
        jobs={"job_done": controller},
        is_terminal_status=lambda status: status == JobStatus.SUCCEEDED,
        sync_world_runtime=lambda: (_ for _ in ()).throw(AssertionError("should not sync")),
    ) is False
    assert resume_job(
        job_id="job_done",
        jobs={"job_done": controller},
        is_terminal_status=lambda status: status == JobStatus.SUCCEEDED,
        rebalance_resources=lambda: (_ for _ in ()).throw(AssertionError("should not rebalance")),
        sync_world_runtime=lambda: (_ for _ in ()).throw(AssertionError("should not sync")),
    ) is False

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
