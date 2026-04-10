"""Tests for kernel runtime projection helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.runtime_projection import build_capability_status_snapshot
from models import JobStatus, Task, TaskKind, TaskStatus, UnitRequest


class _Controller:
    def __init__(self, task_id: str, expert_type: str, status: JobStatus) -> None:
        self.task_id = task_id
        self.expert_type = expert_type
        self._status = status

    def to_model(self):
        return SimpleNamespace(status=self._status)


def test_build_capability_status_snapshot_tracks_fulfilling_phase() -> None:
    task = Task(
        task_id="t_cap",
        raw_text="发展经济",
        kind=TaskKind.MANAGED,
        priority=80,
        status=TaskStatus.RUNNING,
        label="001",
        is_capability=True,
    )
    request = UnitRequest(
        request_id="req_1",
        task_id="t_other",
        task_label="002",
        task_summary="补步兵",
        category="infantry",
        count=3,
        urgency="high",
        hint="步兵",
        blocking=False,
        start_released=True,
    )
    snapshot = build_capability_status_snapshot(
        capability_task=task,
        capability_jobs=[_Controller("t_cap", "EconomyExpert", JobStatus.RUNNING)],
        capability_requests=[request],
        unfulfilled_requests=[{"reason": "reinforcement_after_start"}],
        recent_directives=["发展经济"],
    )

    assert snapshot.phase == "fulfilling"
    assert snapshot.blocker == ""
    assert snapshot.start_released_request_count == 1
    assert snapshot.reinforcement_request_count == 1
    assert snapshot.active_job_types == ["EconomyExpert"]
    assert snapshot.recent_directives == ["发展经济"]
    print("  PASS: build_capability_status_snapshot_tracks_fulfilling_phase")


def test_build_capability_status_snapshot_marks_inference_pending() -> None:
    task = Task(
        task_id="t_cap",
        raw_text="发展经济",
        kind=TaskKind.MANAGED,
        priority=80,
        status=TaskStatus.RUNNING,
        label="001",
        is_capability=True,
    )
    request = UnitRequest(
        request_id="req_1",
        task_id="t_other",
        task_label="002",
        task_summary="补兵",
        category="infantry",
        count=1,
        urgency="high",
        hint="来点兵",
    )
    snapshot = build_capability_status_snapshot(
        capability_task=task,
        capability_jobs=[],
        capability_requests=[request],
        unfulfilled_requests=[{"reason": "inference_pending"}],
        recent_directives=[],
    )

    assert snapshot.phase == "dispatch"
    assert snapshot.blocker == "request_inference_pending"
    assert snapshot.inference_pending_count == 1
    assert snapshot.dispatch_request_count == 1
    print("  PASS: build_capability_status_snapshot_marks_inference_pending")
