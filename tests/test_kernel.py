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
    ResourceKind,
    ResourceNeed,
    ReconJobConfig,
    SignalKind,
    Task,
    TaskKind,
    TaskStatus,
)
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_events: list[Event] = []

    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def tick(self) -> None:
        return None

    def on_event(self, event: Event) -> None:
        self.received_events.append(event)


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


class ResourceJob(BaseJob):
    tick_interval = 1.0

    def __init__(self, *args, resource_needs: list[ResourceNeed], **kwargs):
        super().__init__(*args, **kwargs)
        self.resource_needs = resource_needs
        self.received_events: list[Event] = []

    @property
    def expert_type(self) -> str:
        return "CombatExpert"

    def tick(self) -> None:
        return None

    def on_event(self, event: Event) -> None:
        self.received_events.append(event)


class ResourceExpert(ExecutionExpert):
    def __init__(self, needs_factory):
        self._needs_factory = needs_factory

    @property
    def expert_type(self) -> str:
        return "CombatExpert"

    def create_job(self, task_id, config, signal_callback, constraint_provider=None):
        job_id = self.generate_job_id()
        return ResourceJob(
            job_id=job_id,
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
            resource_needs=self._needs_factory(job_id, config),
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


def make_map() -> MapQueryResult:
    size = 4
    return MapQueryResult(
        MapWidth=size,
        MapHeight=size,
        Height=[[0] * size for _ in range(size)],
        IsVisible=[[True] * size for _ in range(size)],
        IsExplored=[[True] * size for _ in range(size)],
        Terrain=[["clear"] * size for _ in range(size)],
        ResourcesType=[["ore"] * size for _ in range(size)],
        Resources=[[50] * size for _ in range(size)],
    )


def make_resource_frames() -> list:
    return [
        type("Frame", (), {
            "self_actors": [
                Actor(actor_id=10, type="吉普车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=11, type="重坦", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=12, type="重坦", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=13, type="重坦", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=20, type="矿场", faction="自己", position=Location(18, 10), hppercent=100, activity="Idle"),
            ],
            "enemy_actors": [
                Actor(actor_id=201, type="重坦", faction="敌人", position=Location(100, 100), hppercent=100, activity="Idle"),
            ],
            "economy": PlayerBaseInfo(Cash=4000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100),
            "map_info": make_map(),
            "queues": {
                "Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False},
            },
        })(),
        type("Frame", (), {
            "self_actors": [
                Actor(actor_id=11, type="重坦", faction="自己", position=Location(12, 10), hppercent=80, activity="Idle"),
                Actor(actor_id=12, type="重坦", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=13, type="重坦", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=15, type="吉普车", faction="自己", position=Location(20, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=20, type="矿场", faction="自己", position=Location(18, 10), hppercent=100, activity="Idle"),
            ],
            "enemy_actors": [
                Actor(actor_id=201, type="重坦", faction="敌人", position=Location(100, 100), hppercent=100, activity="Idle"),
            ],
            "economy": PlayerBaseInfo(Cash=4500, Resources=500, Power=80, PowerDrained=40, PowerProvided=100),
            "map_info": make_map(),
            "queues": {
                "Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False},
            },
        })(),
    ]


def make_resource_kernel(needs_factory) -> tuple[Kernel, MockWorldSource]:
    source = MockWorldSource(make_resource_frames())
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)
    kernel = Kernel(
        world_model=world,
        expert_registry={
            "CombatExpert": ResourceExpert(needs_factory),
            "ReconExpert": MockReconExpert(),
        },
        task_agent_factory=lambda task, tool_executor, jobs_provider, world_summary_provider: RecordingAgent(
            task,
            tool_executor,
            jobs_provider,
            world_summary_provider,
        ),
        config=KernelConfig(auto_start_agents=False),
    )
    return kernel, source


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


def test_resource_matching_and_priority_preemption() -> None:
    def needs_factory(job_id, _config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "fast", "owner": "self"})]

    kernel, _ = make_resource_kernel(needs_factory)
    low_task = kernel.create_task("low recon attack", TaskKind.MANAGED, 50)
    low_job = kernel.start_job(
        low_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HOLD),
    )

    assert low_job.resources == ["actor:10"]

    high_task = kernel.create_task("high priority attack", TaskKind.MANAGED, 80)
    high_job = kernel.start_job(
        high_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.ASSAULT),
    )

    low_runtime = next(job for job in kernel.list_jobs() if job.job_id == low_job.job_id)
    high_runtime = next(job for job in kernel.list_jobs() if job.job_id == high_job.job_id)
    low_agent = kernel.get_task_agent(low_task.task_id)

    assert high_runtime.resources == ["actor:10"]
    assert low_runtime.status == JobStatus.ABORTED
    assert isinstance(low_agent, RecordingAgent)
    assert len(low_agent.signals) == 1
    assert low_agent.signals[0].result == "aborted"
    print("  PASS: resource_matching_and_priority_preemption")


def test_multi_resource_job_degrades_and_unit_died_auto_replaces() -> None:
    def needs_factory(job_id, config):
        if config.engagement_mode == EngagementMode.HOLD:
            return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=2, predicates={"category": "vehicle", "mobility": "medium", "owner": "self"})]
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "fast", "owner": "self"})]

    kernel, source = make_resource_kernel(needs_factory)
    low_task = kernel.create_task("hold line", TaskKind.MANAGED, 40)
    low_job = kernel.start_job(
        low_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HOLD),
    )
    low_runtime = next(job for job in kernel.list_jobs() if job.job_id == low_job.job_id)
    assert len(low_runtime.resources) == 2

    high_task = kernel.create_task("jeep strike", TaskKind.MANAGED, 70)
    high_job = kernel.start_job(
        high_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HARASS),
    )
    low_after = next(job for job in kernel.list_jobs() if job.job_id == low_job.job_id)
    high_after = next(job for job in kernel.list_jobs() if job.job_id == high_job.job_id)

    assert len(low_after.resources) == 2  # low keeps two tanks; high takes jeep
    assert high_after.resources == ["actor:10"]

    source.set_frame(1)
    kernel.world_model.refresh(now=101.0, force=True)
    kernel.route_event(Event(type=EventType.UNIT_DIED, actor_id=10))
    high_replaced = next(job for job in kernel.list_jobs() if job.job_id == high_job.job_id)

    assert high_replaced.resources == ["actor:15"]
    print("  PASS: multi_resource_job_degrades_and_unit_died_auto_replaces")


def test_actor_event_routing_broadcasts_and_notifications() -> None:
    def needs_factory(job_id, _config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "fast", "owner": "self"})]

    kernel, _ = make_resource_kernel(needs_factory)
    task = kernel.create_task("route test", TaskKind.MANAGED, 50)
    job = kernel.start_job(
        task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HARASS),
    )

    controller = kernel._jobs[job.job_id]
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(controller, ResourceJob)
    assert isinstance(agent, RecordingAgent)

    kernel.route_event(Event(type=EventType.UNIT_DAMAGED, actor_id=10, data={"hp_after": 60}))
    kernel.route_event(Event(type=EventType.ENEMY_DISCOVERED, actor_id=201))
    kernel.route_event(Event(type=EventType.ENEMY_EXPANSION, actor_id=202))
    kernel.route_event(Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20))

    assert controller.received_events[0].type == EventType.UNIT_DAMAGED
    assert agent.events[0].type == EventType.UNIT_DAMAGED
    assert any(event.type == EventType.ENEMY_DISCOVERED for event in agent.events)
    assert any(note["type"] == EventType.ENEMY_EXPANSION.value for note in kernel.list_player_notifications())
    assert any(t.raw_text == "defend_base" for t in kernel.list_tasks())
    print("  PASS: actor_event_routing_broadcasts_and_notifications")


def test_production_queue_matching_and_remaining_event_types() -> None:
    def needs_factory(job_id, _config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.PRODUCTION_QUEUE, count=1, predicates={"queue_type": "Vehicle"})]

    kernel, _ = make_resource_kernel(needs_factory)
    task = kernel.create_task("queue test", TaskKind.MANAGED, 60)
    job = kernel.start_job(
        task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HOLD),
    )
    runtime_job = next(item for item in kernel.list_jobs() if item.job_id == job.job_id)
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(agent, RecordingAgent)

    assert runtime_job.resources == ["queue:Vehicle"]

    kernel.route_event(Event(type=EventType.STRUCTURE_LOST, actor_id=20))
    kernel.route_event(Event(type=EventType.FRONTLINE_WEAK))
    kernel.route_event(Event(type=EventType.ECONOMY_SURPLUS))
    kernel.route_event(Event(type=EventType.PRODUCTION_COMPLETE, data={"queue_type": "Vehicle"}))

    assert any(event.type == EventType.STRUCTURE_LOST for event in agent.events)
    note_types = {note["type"] for note in kernel.list_player_notifications()}
    assert EventType.FRONTLINE_WEAK.value in note_types
    assert EventType.ECONOMY_SURPLUS.value in note_types
    print("  PASS: production_queue_matching_and_remaining_event_types")


def main() -> None:
    test_create_task_and_task_agent_registration()
    test_start_job_validates_and_lifecycle_controls()
    test_cancel_task_and_cancel_tasks_abort_jobs()
    test_tool_handlers_complete_task_and_route_signal()
    test_route_events_batches_through_route_event()
    test_resource_matching_and_priority_preemption()
    test_multi_resource_job_degrades_and_unit_died_auto_replaces()
    test_actor_event_routing_broadcasts_and_notifications()
    test_production_queue_matching_and_remaining_event_types()
    print("OK: 9 Kernel tests passed")


if __name__ == "__main__":
    main()
