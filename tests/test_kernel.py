"""Tests for Kernel task/job lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
from experts.base import BaseJob, ExecutionExpert
from kernel import Kernel, KernelConfig
from models import (
    CombatJobConfig,
    EngagementMode,
    Event,
    EventType,
    ExpertSignal,
    Job,
    JobStatus,
    ReconJobConfig,
    SignalKind,
    Task,
    TaskKind,
    TaskStatus,
)
from task_agent import ToolExecutor, WorldSummary
from world_model import WorldModel

from tests.test_world_model import MockWorldSource, make_frames


class RecordingAgent:
    def __init__(
        self,
        task: Task,
        tool_executor: ToolExecutor,
        jobs_provider,
        world_summary_provider,
    ) -> None:
        self.task = task
        self.tool_executor = tool_executor
        self.jobs_provider = jobs_provider
        self.world_summary_provider = world_summary_provider
        self.signals: list[ExpertSignal] = []
        self.events: list[Event] = []
        self.run_calls = 0
        self.stopped = False

    async def run(self) -> None:
        self.run_calls += 1
        await asyncio.sleep(0)

    def stop(self) -> None:
        self.stopped = True

    def push_signal(self, signal: ExpertSignal) -> None:
        self.signals.append(signal)

    def push_event(self, event: Event) -> None:
        self.events.append(event)


class MockReconJob(BaseJob):
    tick_interval = 1.0

    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def tick(self) -> None:
        return None


class MockReconExpert(ExecutionExpert):
    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def create_job(self, task_id, config, signal_callback, constraint_provider=None):
        return MockReconJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )


def make_kernel() -> Kernel:
    world = WorldModel(MockWorldSource(make_frames()))
    world.refresh(now=100.0, force=True)
    return Kernel(
        world_model=world,
        expert_registry={"ReconExpert": MockReconExpert()},
        task_agent_factory=lambda task, tool_executor, jobs_provider, world_summary_provider: RecordingAgent(
            task,
            tool_executor,
            jobs_provider,
            world_summary_provider,
        ),
        config=KernelConfig(auto_start_agents=False),
    )


def test_create_task_and_task_agent_registration() -> None:
    benchmark.clear()
    kernel = make_kernel()
    task = kernel.create_task("探索地图，找到敌人基地", TaskKind.MANAGED, 50)

    agent = kernel.get_task_agent(task.task_id)
    runtime = kernel.world_model.query("runtime_state")

    assert task.status == TaskStatus.RUNNING
    assert isinstance(agent, RecordingAgent)
    assert runtime["active_tasks"][task.task_id]["priority"] == 50
    assert any(record.name == "kernel:create_task" for record in benchmark.query(tag="tool_exec"))
    print("  PASS: create_task_and_task_agent_registration")


def test_start_job_validates_and_lifecycle_controls() -> None:
    kernel = make_kernel()
    task = kernel.create_task("侦察敌方基地", TaskKind.MANAGED, 40)

    job = kernel.start_job(
        task.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )

    assert job.status == JobStatus.RUNNING
    assert kernel.jobs_for_task(task.task_id)[0].expert_type == "ReconExpert"

    kernel.pause_job(job.job_id)
    assert kernel.list_jobs()[0].status == JobStatus.WAITING

    kernel.resume_job(job.job_id)
    assert kernel.list_jobs()[0].status == JobStatus.RUNNING

    kernel.patch_job(job.job_id, {"search_region": "full_map"})
    patched = kernel.list_jobs()[0]
    assert patched.config.search_region == "full_map"

    try:
        kernel.start_job(
            task.task_id,
            "CombatExpert",
            ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
        )
        raise AssertionError("Expected config validation failure")
    except TypeError:
        pass
    print("  PASS: start_job_validates_and_lifecycle_controls")


def test_cancel_task_and_cancel_tasks_abort_jobs() -> None:
    kernel = make_kernel()
    task1 = kernel.create_task("侦察", TaskKind.MANAGED, 30)
    task2 = kernel.create_task("撤退", TaskKind.INSTANT, 80)
    job = kernel.start_job(
        task1.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )

    assert kernel.cancel_task(task1.task_id) is True
    assert kernel.tasks[task1.task_id].status == TaskStatus.ABORTED
    assert kernel.list_jobs()[0].status == JobStatus.ABORTED

    cancelled = kernel.cancel_tasks({"kind": "instant"})
    assert cancelled == 1
    assert kernel.tasks[task2.task_id].status == TaskStatus.ABORTED
    print("  PASS: cancel_task_and_cancel_tasks_abort_jobs")


def test_tool_handlers_complete_task_and_route_signal() -> None:
    kernel = make_kernel()
    task = kernel.create_task("侦察", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(agent, RecordingAgent)

    async def run() -> None:
        start = await agent.tool_executor.execute(
            "tc_start",
            "start_job",
            '{"expert_type":"ReconExpert","config":{"search_region":"enemy_half","target_type":"base","target_owner":"enemy"}}',
        )
        assert start.error is None
        assert "job_id" in start.result

        complete = await agent.tool_executor.execute(
            "tc_complete",
            "complete_task",
            '{"result":"succeeded","summary":"done"}',
        )
        assert complete.result["ok"] is True

    asyncio.run(run())

    signal = ExpertSignal(
        task_id=task.task_id,
        job_id="j_demo",
        kind=SignalKind.PROGRESS,
        summary="halfway",
    )
    kernel.route_signal(signal)

    assert kernel.tasks[task.task_id].status == TaskStatus.SUCCEEDED
    assert len(agent.signals) == 0
    print("  PASS: tool_handlers_complete_task_and_route_signal")


def test_route_events_batches_through_route_event() -> None:
    kernel = make_kernel()
    received: list[Event] = []

    def capture(event: Event) -> None:
        received.append(event)

    kernel.route_event = capture  # type: ignore[method-assign]
    events = [
        Event(type=EventType.ENEMY_DISCOVERED, actor_id=201),
        Event(type=EventType.UNIT_DAMAGED, actor_id=57),
    ]

    kernel.route_events(events)

    assert received == events
    print("  PASS: route_events_batches_through_route_event")


def main() -> None:
    test_create_task_and_task_agent_registration()
    test_start_job_validates_and_lifecycle_controls()
    test_cancel_task_and_cancel_tasks_abort_jobs()
    test_tool_handlers_complete_task_and_route_signal()
    test_route_events_batches_through_route_event()
    print("OK: 5 Kernel tests passed")


if __name__ == "__main__":
    main()
