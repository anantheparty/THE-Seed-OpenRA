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
import logging_system
from models import (
    CombatJobConfig,
    EngagementMode,
    Event,
    EventType,
    ExpertSignal,
    Job,
    JobStatus,
    PlayerResponse,
    ReservationStatus,
    ResourceKind,
    ResourceNeed,
    ReconJobConfig,
    SignalKind,
    Task,
    TaskKind,
    TaskMessage,
    TaskMessageType,
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
        self.player_responses: list[PlayerResponse] = []
        self.run_calls = 0
        self.stopped = False
        self.runtime_facts_provider = None

    async def run(self) -> None:
        self.run_calls += 1
        await asyncio.sleep(0)

    def stop(self) -> None:
        self.stopped = True

    def push_signal(self, signal: ExpertSignal) -> None:
        self.signals.append(signal)

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_player_response(self, response: PlayerResponse) -> None:
        self.player_responses.append(response)

    def set_runtime_facts_provider(self, provider) -> None:
        self.runtime_facts_provider = provider

    def suspend(self) -> None:
        pass

    def resume_with_event(self, event: Event) -> None:
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


def test_runtime_facts_split_ordinary_and_capability() -> None:
    kernel = make_kernel()
    ordinary = kernel.create_task("普通任务", TaskKind.MANAGED, 40)
    ordinary_agent = kernel.get_task_agent(ordinary.task_id)
    assert isinstance(ordinary_agent, RecordingAgent)
    ordinary_facts = ordinary_agent.runtime_facts_provider(ordinary.task_id)
    assert "buildable" not in ordinary_facts
    assert "feasibility" not in ordinary_facts
    assert "can_afford_power_plant" not in ordinary_facts

    cap = kernel.create_task("经济总管", TaskKind.MANAGED, 80)
    cap.is_capability = True
    cap_agent = kernel.get_task_agent(cap.task_id)
    assert isinstance(cap_agent, RecordingAgent)
    cap_facts = cap_agent.runtime_facts_provider(cap.task_id)
    assert "buildable" in cap_facts
    assert "feasibility" in cap_facts
    assert "can_afford_power_plant" in cap_facts
    print("  PASS: runtime_facts_split_ordinary_and_capability")


def test_capability_task_syncs_capability_status_to_world_model() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    runtime = kernel.world_model.query("runtime_state")
    snapshot = kernel.world_model.query("battlefield_snapshot")

    assert runtime["capability_status"]["task_id"] == cap_id, runtime
    assert runtime["capability_status"]["task_label"], runtime
    assert runtime["capability_status"]["phase"] == "idle", runtime
    assert runtime["capability_status"]["blocker"] == "", runtime
    assert snapshot["capability_status"]["task_id"] == cap_id, snapshot
    print("  PASS: capability_task_syncs_capability_status_to_world_model")


def test_capability_task_registers_production_tools() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    agent = kernel.get_task_agent(cap_id)

    assert isinstance(agent, RecordingAgent)
    assert "produce_units" in agent.tool_executor._handlers
    assert "set_rally_point" in agent.tool_executor._handlers
    assert "request_units" not in agent.tool_executor._handlers
    print("  PASS: capability_task_registers_production_tools")


def test_capability_status_tracks_dispatch_phase_for_pending_requests() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    task = kernel.create_task("前线补坦克", TaskKind.MANAGED, 60)
    for actor in kernel.world_model.find_actors(owner="self", idle_only=True, category="vehicle"):
        kernel.world_model.bind_resource(f"actor:{actor.actor_id}", "other_job")
    kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    runtime = kernel.world_model.query("runtime_state")
    cap_facts = kernel.world_model.compute_runtime_facts(cap_id, include_buildable=True)

    assert runtime["capability_status"]["phase"] in {"dispatch", "bootstrapping"}
    assert runtime["capability_status"]["dispatch_request_count"] >= 0
    assert cap_facts["capability_status"]["phase"] in {"dispatch", "bootstrapping"}
    assert cap_facts["capability_status"]["blocker"] == runtime["capability_status"]["blocker"]
    print("  PASS: capability_status_tracks_dispatch_phase_for_pending_requests")


def test_capability_status_marks_inference_pending_requests() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    task = kernel.create_task("补点空军", TaskKind.MANAGED, 60)
    kernel.register_unit_request(task.task_id, "aircraft", 1, "high", "")

    runtime = kernel.world_model.query("runtime_state")
    cap_facts = kernel.world_model.compute_runtime_facts(cap_id, include_buildable=True)
    pending = cap_facts["unfulfilled_requests"]

    assert runtime["capability_status"]["blocker"] == "request_inference_pending"
    assert runtime["capability_status"]["inference_pending_count"] == 1
    assert pending[0]["reason"] == "inference_pending"
    print("  PASS: capability_status_marks_inference_pending_requests")


def test_capability_status_marks_missing_prerequisite_requests() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    task = kernel.create_task("来个猛犸", TaskKind.MANAGED, 60)
    for actor in kernel.world_model.find_actors(owner="self", idle_only=True, category="vehicle"):
        kernel.world_model.bind_resource(f"actor:{actor.actor_id}", "other_job")
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "猛犸坦克")
    req = kernel._unit_requests[result["request_id"]]
    req.bootstrap_job_id = None
    req.bootstrap_task_id = None
    kernel._sync_world_runtime()

    runtime = kernel.world_model.query("runtime_state")
    cap_facts = kernel.world_model.compute_runtime_facts(cap_id, include_buildable=True)
    pending = cap_facts["unfulfilled_requests"]

    assert runtime["capability_status"]["blocker"] == "missing_prerequisite"
    assert runtime["capability_status"]["prerequisite_gap_count"] == 1
    assert pending[0]["reason"] == "missing_prerequisite"
    assert pending[0]["prerequisites"] == ["fix", "weap"]
    print("  PASS: capability_status_marks_missing_prerequisite_requests")


def test_capability_status_marks_world_sync_stale_requests() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    task = kernel.create_task("补步兵", TaskKind.MANAGED, 60)
    kernel.world_model.state.stale = True
    kernel.register_unit_request(task.task_id, "infantry", 1, "high", "步兵")

    runtime = kernel.world_model.query("runtime_state")
    cap_facts = kernel.world_model.compute_runtime_facts(cap_id, include_buildable=True)
    pending = cap_facts["unfulfilled_requests"]

    assert runtime["capability_status"]["blocker"] == "world_sync_stale"
    assert runtime["capability_status"]["world_sync_stale_count"] == 1
    assert pending[0]["reason"] == "world_sync_stale"
    print("  PASS: capability_status_marks_world_sync_stale_requests")


def test_capability_status_tracks_fulfilling_phase_after_start_release() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    task = kernel.create_task("装甲推进", TaskKind.MANAGED, 60)
    for actor in kernel.world_model.find_actors(owner="self", idle_only=True, category="vehicle"):
        kernel.world_model.bind_resource(f"actor:{actor.actor_id}", "other_job")
    result = kernel.register_unit_request(
        task.task_id,
        "vehicle",
        3,
        "high",
        "重坦",
        min_start_package=2,
    )
    req = kernel._unit_requests[result["request_id"]]
    reservation = kernel.list_unit_reservations()[0]

    req.fulfilled = 2
    req.status = "partial"
    req.start_released = True
    req.assigned_actor_ids = [10, 11]
    reservation.status = ReservationStatus.PARTIAL
    reservation.start_released = True
    reservation.assigned_actor_ids = [10, 11]
    kernel._sync_world_runtime()

    runtime = kernel.world_model.query("runtime_state")
    cap_facts = kernel.world_model.compute_runtime_facts(cap_id, include_buildable=True)
    pending = cap_facts["unfulfilled_requests"]

    assert runtime["capability_status"]["phase"] == "fulfilling"
    assert runtime["capability_status"]["blocker"] == ""
    assert runtime["capability_status"]["start_released_request_count"] == 1
    assert runtime["capability_status"]["bootstrapping_request_count"] == 0
    assert pending[0]["reason"] == "start_package_released"
    print("  PASS: capability_status_tracks_fulfilling_phase_after_start_release")


def test_capability_status_tracks_recent_directives() -> None:
    kernel = make_kernel()
    cap_id = kernel.ensure_capability_task()

    assert kernel.inject_player_message(cap_id, "发展经济")
    assert kernel.inject_player_message(cap_id, "优先补电")

    runtime = kernel.world_model.query("runtime_state")
    directives = runtime["capability_status"]["recent_directives"]
    assert directives[-2:] == ["发展经济", "优先补电"], runtime
    print("  PASS: capability_status_tracks_recent_directives")


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


def test_reset_session_clears_runtime_memory() -> None:
    kernel = make_kernel()
    task = kernel.create_task("侦察", TaskKind.MANAGED, 50)
    kernel.start_job(
        task.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )
    kernel.push_player_notification("info", "hello")
    kernel.register_task_message(
        TaskMessage(
            message_id="msg_reset",
            task_id=task.task_id,
            type=TaskMessageType.TASK_INFO,
            content="started",
        )
    )

    assert kernel.list_tasks()
    assert kernel.list_jobs()
    assert kernel.list_player_notifications()
    assert kernel.list_task_messages()

    kernel.reset_session()

    # After reset, only the auto-created EconomyCapability task should exist
    tasks = kernel.list_tasks()
    assert len(tasks) == 1
    assert getattr(tasks[0], "is_capability", False) is True
    assert kernel.list_jobs() == []
    assert kernel.list_player_notifications() == []
    assert kernel.list_task_messages() == []
    assert kernel.list_pending_questions() == []
    print("  PASS: reset_session_clears_runtime_memory")


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


def test_blocked_signal_registers_task_warning() -> None:
    kernel = make_kernel()
    task = kernel.create_task("建造兵营", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(agent, RecordingAgent)

    signal = ExpertSignal(
        task_id=task.task_id,
        job_id="j_blocked",
        kind=SignalKind.BLOCKED,
        summary="电力不足，生产暂停等待恢复",
        data={"reason": "low_power", "queue_type": "Building"},
    )
    kernel.route_signal(signal)

    messages = kernel.list_task_messages(task.task_id)
    assert len(messages) == 1
    assert messages[0].type == TaskMessageType.TASK_WARNING
    assert "电力不足" in messages[0].content
    assert len(agent.signals) == 1
    assert agent.signals[0].kind == SignalKind.BLOCKED
    print("  PASS: blocked_signal_registers_task_warning")


def test_complete_task_releases_resources_from_terminal_jobs() -> None:
    def needs_factory(job_id, _config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "fast", "owner": "self"})]

    kernel, _ = make_resource_kernel(needs_factory)
    task1 = kernel.create_task("first strike", TaskKind.MANAGED, 50)
    job1 = kernel.start_job(
        task1.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HARASS),
    )

    controller1 = next(job for job in kernel.active_jobs() if job.job_id == job1.job_id)
    assert controller1.resources == ["actor:10"]
    controller1.status = JobStatus.SUCCEEDED
    assert all(job.job_id != job1.job_id for job in kernel.active_jobs())

    assert kernel.complete_task(task1.task_id, "succeeded", "done") is True
    assert kernel.world_model.resource_bindings == {}

    task2 = kernel.create_task("second strike", TaskKind.MANAGED, 50)
    job2 = kernel.start_job(
        task2.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HARASS),
    )
    controller2 = next(job for job in kernel.active_jobs() if job.job_id == job2.job_id)

    assert controller2.resources == ["actor:10"]
    print("  PASS: complete_task_releases_resources_from_terminal_jobs")


def test_actor_job_resource_grant_seeds_task_owned_group() -> None:
    def needs_factory(job_id, _config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "fast", "owner": "self"})]

    kernel, _ = make_resource_kernel(needs_factory)
    task = kernel.create_task("突击", TaskKind.MANAGED, 60)
    job = kernel.start_job(
        task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HARASS),
    )

    assert job.resources == ["actor:10"]
    assert kernel.task_active_actor_ids(task.task_id) == [10]
    runtime = kernel.world_model.query("runtime_state")
    assert runtime["active_tasks"][task.task_id]["active_actor_ids"] == [10]
    assert runtime["active_tasks"][task.task_id]["active_group_size"] == 1
    print("  PASS: actor_job_resource_grant_seeds_task_owned_group")


def test_other_active_tasks_view_includes_jobs_and_recent_reports() -> None:
    kernel = make_kernel()
    current = kernel.create_task("当前任务", TaskKind.MANAGED, 60)
    sibling = kernel.create_task("侦察敌方", TaskKind.MANAGED, 55)
    finished = kernel.create_task("旧任务", TaskKind.MANAGED, 40)

    kernel.start_job(
        sibling.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )
    kernel.register_task_message(
        TaskMessage(
            message_id="msg_info",
            task_id=sibling.task_id,
            type=TaskMessageType.TASK_INFO,
            content="正在向敌方半区推进侦察",
            timestamp=101.0,
        )
    )
    assert kernel.complete_task(finished.task_id, "succeeded", "旧任务已收口") is True

    view = kernel._other_active_tasks_for(current.task_id)

    sibling_entry = next(item for item in view if item.get("label") == sibling.label)
    assert sibling_entry["raw_text"] == "侦察敌方"
    assert sibling_entry["jobs"] == [
        {
            "expert": "ReconExpert",
            "region": "enemy_half",
        }
    ]
    reports_entry = next(item for item in view if "_recent_reports" in item)
    assert reports_entry["_recent_reports"] == [
        {
            "task_label": sibling.label,
            "type": TaskMessageType.TASK_INFO.value,
            "content": "正在向敌方半区推进侦察",
        },
        {
            "task_label": finished.label,
            "type": TaskMessageType.TASK_COMPLETE_REPORT.value,
            "content": "旧任务已收口",
        },
    ]
    print("  PASS: other_active_tasks_view_includes_jobs_and_recent_reports")


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


def test_game_reset_event_clears_runtime_state() -> None:
    kernel = make_kernel()
    task = kernel.create_task("侦察旧对局", TaskKind.MANAGED, 50)
    job = kernel.start_job(
        task.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )

    assert kernel.list_tasks()
    assert kernel.list_jobs()

    kernel.route_event(
        Event(
            type=EventType.GAME_RESET,
            data={"previous_self_units": 8, "current_self_units": 1},
            timestamp=200.0,
        )
    )

    # After reset, only the auto-created EconomyCapability should remain
    remaining_tasks = kernel.list_tasks()
    assert len(remaining_tasks) == 1
    assert remaining_tasks[0].is_capability is True
    assert kernel.list_jobs() == []
    runtime_state = kernel.world_model.query("runtime_state")
    assert len(runtime_state["active_tasks"]) == 1
    assert runtime_state["active_jobs"] == {}
    assert runtime_state["resource_bindings"] == {}
    assert runtime_state["constraints"] == []
    notifications = kernel.list_player_notifications()
    assert notifications[-1]["type"] == "game_reset"
    assert "已清理旧任务状态" in notifications[-1]["content"]
    print("  PASS: game_reset_event_clears_runtime_state")


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


def test_managed_job_forwards_resource_lost_signal_when_expert_missing() -> None:
    empty_frame = type("Frame", (), {
        "self_actors": [],
        "enemy_actors": [],
        "economy": None,
        "map_info": MapQueryResult(
            MapWidth=4, MapHeight=4,
            Height=[[0] * 4 for _ in range(4)],
            IsVisible=[[True] * 4 for _ in range(4)],
            IsExplored=[[True] * 4 for _ in range(4)],
            Terrain=[["clear"] * 4 for _ in range(4)],
            ResourcesType=[["ore"] * 4 for _ in range(4)],
            Resources=[[0] * 4 for _ in range(4)],
        ),
        "queues": {},
    })()
    source = MockWorldSource([empty_frame])
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)

    kernel = Kernel(
        world_model=world,
        expert_registry={},
        task_agent_factory=lambda task, te, jp, wp: RecordingAgent(task, te, jp, wp),
        config=KernelConfig(auto_start_agents=False),
    )

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    kernel.start_job(
        task.task_id,
        "CombatExpert",
        CombatJobConfig(
            target_position=(100, 100),
            engagement_mode=EngagementMode.ASSAULT,
            max_chase_distance=20,
            retreat_threshold=0.3,
        ),
    )

    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(agent, RecordingAgent)
    assert any(signal.kind == SignalKind.RESOURCE_LOST for signal in agent.signals)
    print("  PASS: managed_job_forwards_resource_lost_signal_when_expert_missing")


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


def test_soft_actor_need_does_not_claim_static_buildings() -> None:
    source = MockWorldSource(
        [
            type("Frame", (), {
                "self_actors": [
                    Actor(actor_id=20, type="矿场", faction="自己", position=Location(18, 10), hppercent=100, activity="Idle"),
                ],
                "enemy_actors": [],
                "economy": PlayerBaseInfo(Cash=4000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100),
                "map_info": make_map(),
                "queues": {},
            })(),
        ]
    )
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)
    kernel = Kernel(
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

    task = kernel.create_task("侦察", TaskKind.MANAGED, 50)
    job = kernel.start_job(
        task.task_id,
        "ReconExpert",
        ReconJobConfig(search_region="enemy_half", target_type="base", target_owner="enemy"),
    )
    runtime_job = next(item for item in kernel.list_jobs() if item.job_id == job.job_id)

    assert runtime_job.resources == []
    assert runtime_job.status == JobStatus.WAITING
    assert world.resource_bindings == {}
    print("  PASS: soft_actor_need_does_not_claim_static_buildings")


def test_explicit_building_or_static_need_can_claim_building() -> None:
    def building_needs_factory(job_id, config):
        if config.engagement_mode == EngagementMode.HOLD:
            return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"category": "building", "owner": "self"})]
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"mobility": "static", "owner": "self"})]

    kernel_building, _ = make_resource_kernel(building_needs_factory)
    building_task = kernel_building.create_task("claim building", TaskKind.MANAGED, 60)
    building_job = kernel_building.start_job(
        building_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.HOLD),
    )
    building_runtime = next(item for item in kernel_building.list_jobs() if item.job_id == building_job.job_id)

    kernel_static, _ = make_resource_kernel(building_needs_factory)
    static_task = kernel_static.create_task("claim static", TaskKind.MANAGED, 61)
    static_job = kernel_static.start_job(
        static_task.task_id,
        "CombatExpert",
        CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.ASSAULT),
    )
    static_runtime = next(item for item in kernel_static.list_jobs() if item.job_id == static_job.job_id)

    assert building_runtime.resources == ["actor:20"]
    assert static_runtime.resources == ["actor:20"]
    print("  PASS: explicit_building_or_static_need_can_claim_building")


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

    controller = next(item for item in kernel.active_jobs() if item.job_id == job.job_id)
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(controller, ResourceJob)
    assert isinstance(agent, RecordingAgent)

    kernel.route_event(Event(type=EventType.UNIT_DAMAGED, actor_id=10, data={"hp_after": 60}))
    kernel.route_event(Event(type=EventType.ENEMY_DISCOVERED, actor_id=201))
    kernel.route_event(Event(type=EventType.ENEMY_EXPANSION, actor_id=202))
    kernel.route_event(Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20))
    defend_task = next(t for t in kernel.list_tasks() if t.raw_text == "defend_base")
    defend_jobs = [item for item in kernel.list_jobs() if item.task_id == defend_task.task_id and item.expert_type == "CombatExpert"]

    assert controller.received_events[0].type == EventType.UNIT_DAMAGED
    assert agent.events[0].type == EventType.UNIT_DAMAGED
    assert any(event.type == EventType.ENEMY_DISCOVERED for event in agent.events)
    assert any(note["type"] == EventType.ENEMY_EXPANSION.value for note in kernel.list_player_notifications())
    assert len(defend_jobs) == 1
    assert defend_jobs[0].config.target_position == (18, 10)
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


def test_pending_question_timeout_and_late_reply() -> None:
    kernel = make_kernel()
    task = kernel.create_task("继续进攻还是放弃", TaskKind.MANAGED, 60)
    agent = kernel.get_task_agent(task.task_id)
    assert isinstance(agent, RecordingAgent)

    message = TaskMessage(
        message_id="msg_1",
        task_id=task.task_id,
        type=TaskMessageType.TASK_QUESTION,
        content="兵力不足，继续还是放弃？",
        options=["继续", "放弃"],
        timeout_s=3.0,
        default_option="放弃",
        priority=60,
        timestamp=100.0,
    )
    assert kernel.register_task_message(message) is True
    assert kernel.list_pending_questions()[0]["message_id"] == "msg_1"

    assert kernel.tick(now=102.0) == 0
    assert agent.player_responses == []

    assert kernel.tick(now=103.0) == 1
    assert len(agent.player_responses) == 1
    assert agent.player_responses[0].message_id == "msg_1"
    assert agent.player_responses[0].answer == "放弃"
    assert kernel.list_pending_questions() == []

    late = kernel.submit_player_response(
        PlayerResponse(message_id="msg_1", task_id=task.task_id, answer="继续", timestamp=104.0),
        now=104.0,
    )
    assert late["ok"] is False
    assert late["status"] == "timed_out"
    assert late["message"] == "已按默认处理，如需更改请重新下令"
    print("  PASS: pending_question_timeout_and_late_reply")


def test_cancel_task_closes_pending_question() -> None:
    kernel = make_kernel()
    task = kernel.create_task("等待玩家决定", TaskKind.MANAGED, 55)

    kernel.register_task_message(
        TaskMessage(
            message_id="msg_cancel",
            task_id=task.task_id,
            type=TaskMessageType.TASK_QUESTION,
            content="继续还是取消？",
            options=["继续", "取消"],
            timeout_s=10.0,
            default_option="取消",
            priority=55,
            timestamp=100.0,
        )
    )
    assert len(kernel.list_pending_questions()) == 1

    assert kernel.cancel_task(task.task_id) is True
    assert kernel.list_pending_questions() == []

    late = kernel.submit_player_response(
        PlayerResponse(message_id="msg_cancel", task_id=task.task_id, answer="继续", timestamp=101.0),
        now=101.0,
    )
    assert late["ok"] is False
    assert late["status"] == "closed"
    print("  PASS: cancel_task_closes_pending_question")


def test_auto_response_rule_registration_and_base_under_attack_dedup() -> None:
    kernel = make_kernel()
    kernel.create_task("普通进攻", TaskKind.MANAGED, 50)
    triggered: list[EventType] = []

    kernel.register_auto_response_rule(
        "record_enemy_expansion",
        EventType.ENEMY_EXPANSION,
        lambda event: triggered.append(event.type),
    )

    kernel.route_event(Event(type=EventType.ENEMY_EXPANSION, actor_id=201))
    kernel.route_event(Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20))
    kernel.route_event(Event(type=EventType.BASE_UNDER_ATTACK, actor_id=20, position=(40, 50)))

    defend_tasks = [task for task in kernel.list_tasks() if task.raw_text == "defend_base"]
    defend_jobs = [job for job in kernel.list_jobs() if job.task_id == defend_tasks[0].task_id and job.expert_type == "CombatExpert"]
    assert triggered == [EventType.ENEMY_EXPANSION]
    assert len(defend_tasks) == 1
    assert defend_tasks[0].priority == 80
    assert defend_tasks[0].kind == TaskKind.MANAGED
    assert len(defend_jobs) == 1
    assert defend_jobs[0].config.target_position == (40, 50)
    print("  PASS: auto_response_rule_registration_and_base_under_attack_dedup")


def test_job_started_logged_before_resource_lost_signal() -> None:
    """job_started log entry must precede any RESOURCE_LOST signal for the same job.

    Regression guard for T5: previously _rebalance_resources() ran before the
    job_started log, so the LLM could see resource_lost without a prior job_started.
    """
    logging_system.clear()

    # Track: index in log_records() at the moment each RESOURCE_LOST signal arrives
    resource_lost_log_positions: list[int] = []

    class OrderTrackingAgent(RecordingAgent):
        def push_signal(self, signal: ExpertSignal) -> None:
            if signal.kind == SignalKind.RESOURCE_LOST:
                resource_lost_log_positions.append(len(logging_system.records()))
            super().push_signal(signal)

    # World with NO idle actors → resource need cannot be satisfied → triggers RESOURCE_LOST
    from openra_api.models import MapQueryResult
    empty_frame = type("Frame", (), {
        "self_actors": [],
        "enemy_actors": [],
        "economy": None,
        "map_info": MapQueryResult(
            MapWidth=4, MapHeight=4,
            Height=[[0]*4 for _ in range(4)],
            IsVisible=[[True]*4 for _ in range(4)],
            IsExplored=[[True]*4 for _ in range(4)],
            Terrain=[["clear"]*4 for _ in range(4)],
            ResourcesType=[["ore"]*4 for _ in range(4)],
            Resources=[[0]*4 for _ in range(4)],
        ),
        "queues": {},
    })()

    from tests.test_world_model import MockWorldSource
    source = MockWorldSource([empty_frame])
    world = WorldModel(source)
    world.refresh(now=100.0, force=True)

    def needs_factory(job_id, config):
        return [ResourceNeed(job_id=job_id, kind=ResourceKind.ACTOR, count=1, predicates={"category": "vehicle"})]

    kernel = Kernel(
        world_model=world,
        expert_registry={"CombatExpert": ResourceExpert(needs_factory)},
        task_agent_factory=lambda task, te, jp, wp: OrderTrackingAgent(task, te, jp, wp),
        config=KernelConfig(auto_start_agents=False),
    )

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    kernel.start_job(task.task_id, "CombatExpert", CombatJobConfig(
        target_position=(100, 100),
        engagement_mode=EngagementMode.ASSAULT,
        max_chase_distance=20,
        retreat_threshold=0.3,
    ))

    all_records = logging_system.records()
    job_started_idx = next(
        (i for i, r in enumerate(all_records) if getattr(r, "event", None) == "job_started"),
        None,
    )

    assert job_started_idx is not None, "job_started was not logged"
    assert resource_lost_log_positions, "RESOURCE_LOST signal was not emitted (test precondition failed)"

    for pos in resource_lost_log_positions:
        assert job_started_idx < pos, (
            f"job_started (log idx={job_started_idx}) must come BEFORE "
            f"RESOURCE_LOST signal (log snapshot size={pos})"
        )

    print("  PASS: job_started_logged_before_resource_lost_signal")


def main() -> None:
    test_create_task_and_task_agent_registration()
    test_capability_task_syncs_capability_status_to_world_model()
    test_capability_status_tracks_dispatch_phase_for_pending_requests()
    test_capability_status_marks_inference_pending_requests()
    test_capability_status_marks_missing_prerequisite_requests()
    test_capability_status_tracks_fulfilling_phase_after_start_release()
    test_capability_status_tracks_recent_directives()
    test_start_job_validates_and_lifecycle_controls()
    test_cancel_task_and_cancel_tasks_abort_jobs()
    test_reset_session_clears_runtime_memory()
    test_tool_handlers_complete_task_and_route_signal()
    test_blocked_signal_registers_task_warning()
    test_complete_task_releases_resources_from_terminal_jobs()
    test_route_events_batches_through_route_event()
    test_game_reset_event_clears_runtime_state()
    test_resource_matching_and_priority_preemption()
    test_managed_job_forwards_resource_lost_signal_when_expert_missing()
    test_multi_resource_job_degrades_and_unit_died_auto_replaces()
    test_soft_actor_need_does_not_claim_static_buildings()
    test_explicit_building_or_static_need_can_claim_building()
    test_actor_event_routing_broadcasts_and_notifications()
    test_production_queue_matching_and_remaining_event_types()
    test_pending_question_timeout_and_late_reply()
    test_cancel_task_closes_pending_question()
    test_auto_response_rule_registration_and_base_under_attack_dedup()
    test_job_started_logged_before_resource_lost_signal()
    print("OK: 22 Kernel tests passed")


if __name__ == "__main__":
    main()
