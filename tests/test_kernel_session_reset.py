"""Tests for kernel session reset helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.session_reset import (
    abort_and_release_all_jobs,
    clear_kernel_runtime_collections,
    stop_all_task_runtimes,
)
from models import JobStatus


def test_stop_all_task_runtimes_visits_all_task_ids() -> None:
    visited: list[str] = []
    stop_all_task_runtimes(
        {"t_1": SimpleNamespace(), "t_2": SimpleNamespace()},
        stop_task_runtime_fn=lambda task_runtimes, task_id: visited.append(task_id),
    )
    assert visited == ["t_1", "t_2"]
    print("  PASS: stop_all_task_runtimes_visits_all_task_ids")


def test_abort_and_release_all_jobs_handles_terminal_and_live_jobs() -> None:
    aborted: list[str] = []
    released: list[str] = []
    jobs = {
        "j_1": SimpleNamespace(status=JobStatus.RUNNING, resources=["actor:10"], abort=lambda: aborted.append("j_1")),
        "j_2": SimpleNamespace(status=JobStatus.SUCCEEDED, resources=["actor:11"], abort=lambda: aborted.append("j_2")),
        "j_3": SimpleNamespace(status=JobStatus.RUNNING, resources=[], abort=lambda: aborted.append("j_3")),
    }

    abort_and_release_all_jobs(
        jobs,
        is_terminal_status=lambda status: status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED},
        release_job_resources_fn=lambda controller: released.append(
            next(job_id for job_id, candidate in jobs.items() if candidate is controller)
        ),
    )

    assert aborted == ["j_1", "j_3"]
    assert released == ["j_1", "j_2"]
    print("  PASS: abort_and_release_all_jobs_handles_terminal_and_live_jobs")


def test_clear_kernel_runtime_collections_respects_notification_flags() -> None:
    calls = []
    tasks = {"t": object()}
    task_runtimes = {"t": object()}
    jobs = {"j": object()}
    constraints = {"c": object()}
    resource_needs = {"j": object()}
    resource_loss_notified = {"j"}
    player_notifications = [{"type": "info"}]
    task_messages = [{"id": "m"}]
    delivered = {"t": ["resp"]}
    unit_requests = {"r": object()}
    unit_reservations = {"u": object()}
    request_reservations = {"r": "u"}
    task_actor_groups = {"t": {1}}
    direct_managed_tasks = {"t"}
    capability_recent_inputs = [{"text": "hi"}]

    clear_kernel_runtime_collections(
        tasks=tasks,
        task_runtimes=task_runtimes,
        jobs=jobs,
        constraints=constraints,
        resource_needs=resource_needs,
        resource_loss_notified=resource_loss_notified,
        player_notifications=player_notifications,
        task_messages=task_messages,
        reset_questions=lambda: calls.append("reset_questions"),
        delivered_player_responses=delivered,
        unit_requests=unit_requests,
        unit_reservations=unit_reservations,
        request_reservations=request_reservations,
        task_actor_groups=task_actor_groups,
        direct_managed_tasks=direct_managed_tasks,
        capability_recent_inputs=capability_recent_inputs,
        clear_player_notifications=False,
        clear_task_messages=True,
    )

    assert tasks == {}
    assert task_runtimes == {}
    assert jobs == {}
    assert constraints == {}
    assert resource_needs == {}
    assert resource_loss_notified == set()
    assert player_notifications == [{"type": "info"}]
    assert task_messages == []
    assert delivered == {}
    assert unit_requests == {}
    assert unit_reservations == {}
    assert request_reservations == {}
    assert task_actor_groups == {}
    assert direct_managed_tasks == set()
    assert capability_recent_inputs == []
    assert calls == ["reset_questions"]
    print("  PASS: clear_kernel_runtime_collections_respects_notification_flags")
