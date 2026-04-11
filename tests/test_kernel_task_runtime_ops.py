"""Tests for kernel task runtime lifecycle helpers."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.task_runtime_ops import (
    maybe_start_agent,
    release_job_resources,
    release_task_job_resources,
    stop_task_runtime,
)
from models import JobStatus


class _Agent:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.run_calls = 0

    async def run(self) -> None:
        self.run_calls += 1
        await asyncio.sleep(0)

    def stop(self) -> None:
        self.stop_calls += 1


def test_stop_task_runtime_cancels_runner_and_stops_agent() -> None:
    agent = _Agent()
    runner = SimpleNamespace(cancelled=False)
    runner.cancel = lambda: setattr(runner, "cancelled", True)
    runtime = SimpleNamespace(agent=agent, runner=runner)

    stop_task_runtime({"t_1": runtime}, "t_1")

    assert agent.stop_calls == 1
    assert runner.cancelled is True
    assert runtime.runner is None
    print("  PASS: stop_task_runtime_cancels_runner_and_stops_agent")


def test_release_job_resources_revokes_and_unbinds() -> None:
    revoked: list[list[str]] = []
    unbound: list[str] = []
    controller = SimpleNamespace(
        status=JobStatus.RUNNING,
        resources=["actor:10", "queue:Vehicle"],
        on_resource_revoked=lambda resources: revoked.append(list(resources)),
    )

    release_job_resources(controller, unbind_resource=unbound.append)

    assert revoked == [["actor:10", "queue:Vehicle"]]
    assert unbound == ["actor:10", "queue:Vehicle"]
    print("  PASS: release_job_resources_revokes_and_unbinds")


def test_release_task_job_resources_filters_by_task_and_clears_markers() -> None:
    released: list[str] = []
    cleared: list[str] = []
    jobs = {
        "j_1": SimpleNamespace(job_id="j_1", task_id="t_1", resources=["actor:10"]),
        "j_2": SimpleNamespace(job_id="j_2", task_id="t_2", resources=["actor:11"]),
        "j_3": SimpleNamespace(job_id="j_3", task_id="t_1", resources=[]),
    }

    release_task_job_resources(
        jobs,
        "t_1",
        release_job_resources_fn=lambda controller: released.append(controller.job_id),
        on_job_released=cleared.append,
    )

    assert released == ["j_1"]
    assert cleared == ["j_1", "j_3"]
    print("  PASS: release_task_job_resources_filters_by_task_and_clears_markers")


def test_maybe_start_agent_starts_runner_inside_event_loop() -> None:
    async def _run() -> None:
        agent = _Agent()
        runtime = SimpleNamespace(agent=agent, runner=None)
        maybe_start_agent(runtime, auto_start_agents=True)
        assert runtime.runner is not None
        await runtime.runner
        assert agent.run_calls == 1

    asyncio.run(_run())
    print("  PASS: maybe_start_agent_starts_runner_inside_event_loop")
