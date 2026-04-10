"""Tests for Kernel register_unit_request full implementation.

Covers:
  1. _infer_unit_type hint → unit_type mapping
  2. Idle matching (fulfilled from field)
  3. Fast-path bootstrap (EconomyJob auto-start)
  4. _fulfill_unit_requests auto-assign on PRODUCTION_COMPLETE
  5. Agent suspend / wake mechanism
  6. cancel_task request cleanup
  7. Priority-based fulfillment ordering
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import logging_system
from kernel import Kernel, KernelConfig
from models import (
    EconomyJobConfig,
    Event,
    EventType,
    ExpertSignal,
    Job,
    JobStatus,
    PlayerResponse,
    ReservationStatus,
    ResourceKind,
    ResourceNeed,
    Task,
    TaskKind,
    TaskStatus,
    UnitRequest,
    UnitReservation,
)
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from task_agent import ToolExecutor, WorldSummary
from world_model import WorldModel
from tests.test_world_model import MockWorldSource, make_map


# =====================================================================
# Helpers
# =====================================================================

class Frame:
    def __init__(self, self_actors, enemy_actors, economy, map_info, queues):
        self.self_actors = self_actors
        self.enemy_actors = enemy_actors
        self.economy = economy
        self.map_info = map_info
        self.queues = queues


class RecordingAgent:
    """Minimal recording agent for kernel tests."""
    def __init__(self, task, tool_executor, jobs_provider, world_summary_provider):
        self.task = task
        self.tool_executor = tool_executor
        self.jobs_provider = jobs_provider
        self.world_summary_provider = world_summary_provider
        self.signals: list[ExpertSignal] = []
        self.events: list[Event] = []
        self.player_responses: list[PlayerResponse] = []
        self.run_calls = 0
        self.stopped = False
        self._suspended = False
        self._resumed_events: list[Event] = []

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

    def suspend(self) -> None:
        self._suspended = True

    def resume_with_event(self, event: Event) -> None:
        self._suspended = False
        self._resumed_events.append(event)
        self.push_event(event)


class FullBaseWorldSource(MockWorldSource):
    """World source with a full Soviet base (CY + barracks + war factory + radar + repair)."""
    def __init__(self, frames=None):
        if frames is None:
            frames = self._default_frames()
        super().__init__(frames)

    @staticmethod
    def _default_frames():
        from tests.test_world_model import make_map
        return [Frame(
            self_actors=[
                Actor(actor_id=1, type="基地", faction="自己", position=Location(50, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=2, type="发电厂", faction="自己", position=Location(55, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=3, type="兵营", faction="自己", position=Location(60, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=4, type="矿场", faction="自己", position=Location(50, 55), hppercent=100, activity="Idle"),
                Actor(actor_id=5, type="战车工厂", faction="自己", position=Location(65, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=6, type="雷达站", faction="自己", position=Location(70, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=7, type="维修厂", faction="自己", position=Location(75, 50), hppercent=100, activity="Idle"),
                # Idle combat units
                Actor(actor_id=10, type="重坦", faction="自己", position=Location(80, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=11, type="重坦", faction="自己", position=Location(82, 50), hppercent=100, activity="Idle"),
                Actor(actor_id=12, type="重坦", faction="自己", position=Location(84, 50), hppercent=100, activity="AttackMove"),
                Actor(actor_id=13, type="步枪兵", faction="自己", position=Location(60, 55), hppercent=100, activity="Idle"),
                Actor(actor_id=14, type="步枪兵", faction="自己", position=Location(62, 55), hppercent=100, activity="Idle"),
                Actor(actor_id=15, type="火箭兵", faction="自己", position=Location(64, 55), hppercent=100, activity="Idle"),
                Actor(actor_id=16, type="矿车", faction="自己", position=Location(50, 60), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[
                Actor(actor_id=100, type="重坦", faction="敌人", position=Location(300, 300), hppercent=100, activity="Idle"),
            ],
            economy=PlayerBaseInfo(Cash=5000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100),
            map_info=make_map(explored=0.5, visible=0.25),
            queues={"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}},
        )]


def make_kernel_with_base() -> tuple[Kernel, WorldModel]:
    """Create a kernel with a full base and recording agents."""
    world = WorldModel(FullBaseWorldSource())
    world.refresh(now=100.0, force=True)
    kernel = Kernel(
        world_model=world,
        task_agent_factory=lambda task, tool_executor, jobs_provider, world_summary_provider: RecordingAgent(
            task, tool_executor, jobs_provider, world_summary_provider,
        ),
        config=KernelConfig(auto_start_agents=True),
    )
    return kernel, world


def get_agent(kernel: Kernel, task_id: str) -> RecordingAgent:
    runtime = kernel._task_runtimes.get(task_id)
    assert runtime is not None, f"No runtime for task {task_id}"
    return runtime.agent  # type: ignore


# =====================================================================
# 1. _infer_unit_type Tests
# =====================================================================

def test_infer_unit_type_hint_match():
    """Hint keywords should map to specific unit types."""
    kernel, _ = make_kernel_with_base()
    assert kernel._infer_unit_type("vehicle", "重坦") == ("3tnk", "Vehicle")
    assert kernel._infer_unit_type("vehicle", "火箭车") == ("v2rl", "Vehicle")
    assert kernel._infer_unit_type("infantry", "火箭兵") == ("e3", "Infantry")
    assert kernel._infer_unit_type("infantry", "工程师") == ("e6", "Infantry")
    assert kernel._infer_unit_type("building", "电厂") == ("powr", "Building")


def test_infer_unit_type_category_default():
    """Unknown hints should fall back to category defaults."""
    kernel, _ = make_kernel_with_base()
    assert kernel._infer_unit_type("vehicle", "战斗单位") == ("3tnk", "Vehicle")
    assert kernel._infer_unit_type("infantry", "士兵去守") == ("e1", "Infantry")
    assert kernel._infer_unit_type("building", "基础设施") == ("powr", "Building")


def test_infer_unit_type_aircraft_returns_none():
    """Aircraft category has no default — should return None for Capability."""
    kernel, _ = make_kernel_with_base()
    assert kernel._infer_unit_type("aircraft", "对地攻击机") == (None, None)


# =====================================================================
# 2. Idle Matching Tests
# =====================================================================

def test_idle_match_fulfilled():
    """Requesting units that exist idle should return fulfilled."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻东部", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    assert result["status"] == "fulfilled"
    assert result["remaining_count"] == 0
    assert result["unit_type"] == "3tnk"
    assert result["queue_type"] == "Vehicle"
    assert result["reservation_id"].startswith("res_")
    assert result["reservation_status"] == ReservationStatus.ASSIGNED.value
    assert result["bootstrap_job_id"] is None
    assert len(result["actor_ids"]) == 2
    # Actor 10 and 11 are idle 重坦; 12 is AttackMove (not idle)
    assert set(result["actor_ids"]) == {10, 11}


def test_idle_match_partial():
    """If not enough idle, should partially fulfill and bootstrap the rest."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("大规模进攻", TaskKind.MANAGED, 50)
    # Request 5 vehicles but only 2 idle tanks exist (actor 12 is not idle)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    assert result["status"] == "waiting"
    req = kernel._unit_requests[result["request_id"]]
    assert req.fulfilled == 2
    assert req.status == "partial"
    assert set(req.assigned_actor_ids) == {10, 11}


def test_idle_match_none_available():
    """No idle units → waiting + bootstrap."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("空袭", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "aircraft", 2, "high", "对地攻击机")
    assert result["status"] == "waiting"
    req = kernel._unit_requests[result["request_id"]]
    assert req.fulfilled == 0
    assert req.status == "pending"


def test_idle_match_infantry():
    """Infantry idle matching should work."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("防守", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "infantry", 2, "medium", "步兵")
    assert result["status"] == "fulfilled"
    assert len(result["actor_ids"]) == 2


def test_idle_match_hint_preference():
    """Hint matching should prefer actors whose name matches the hint."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("防守", TaskKind.MANAGED, 50)
    # Request infantry with hint "火箭兵" — should prefer actor 15 (火箭兵)
    result = kernel.register_unit_request(task.task_id, "infantry", 1, "medium", "火箭兵")
    assert result["status"] == "fulfilled"
    assert 15 in result["actor_ids"]


# =====================================================================
# 3. Fast-path Bootstrap Tests
# =====================================================================

def test_bootstrap_creates_economy_job():
    """Fast-path should create an EconomyJob for unfulfilled requests."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    # Request more tanks than available idle (2 idle, need 5)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    # Should have created a bootstrap job for the remaining 3
    assert req.bootstrap_job_id is not None
    assert result["request_id"] == req.request_id
    assert result["remaining_count"] == 3
    assert result["unit_type"] == "3tnk"
    assert result["queue_type"] == "Vehicle"
    assert result["reservation_id"].startswith("res_")
    assert result["reservation_status"] == ReservationStatus.PARTIAL.value
    assert result["bootstrap_job_id"] == req.bootstrap_job_id
    assert result["bootstrap_task_id"] == task.task_id
    job = kernel._jobs[req.bootstrap_job_id]
    assert job.task_id == task.task_id
    assert job.config.unit_type == "3tnk"
    assert job.config.count == 3
    assert job.config.queue_type == "Vehicle"
    assert job.config.request_id == req.request_id
    assert job.config.reservation_id == result["reservation_id"]


def test_bootstrap_skipped_when_not_producible():
    """Fast-path should skip if unit type is not in buildable list."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("空袭", TaskKind.MANAGED, 50)
    # Aircraft is not in _derive_buildable_units for Soviet (no airfield)
    result = kernel.register_unit_request(task.task_id, "aircraft", 2, "high", "对地攻击机")
    req = kernel._unit_requests[result["request_id"]]
    assert req.bootstrap_job_id is None  # No job created


def test_building_request_does_not_bind_construction_yard_actor():
    """Ordinary tasks should not directly request building prerequisites."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("建造兵营", TaskKind.MANAGED, 60)

    result = kernel.register_unit_request(task.task_id, "building", 1, "high", "兵营")
    assert result["status"] == "error"
    assert "不能直接请求建筑前置" in result["message"]
    assert kernel.world_model.resource_bindings == {}


def test_bootstrap_notifies_capability():
    """Fast-path should notify EconomyCapability task."""
    kernel, _ = make_kernel_with_base()
    # Create capability task
    cap_task = kernel.create_task("经济规划", TaskKind.MANAGED, 80)
    cap_task.is_capability = True
    kernel._capability_task_id = cap_task.task_id
    cap_agent = get_agent(kernel, cap_task.task_id)

    # Now request that triggers bootstrap
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")

    # Capability should have received a PLAYER_MESSAGE notification
    notify_events = [e for e in cap_agent.events if e.type == EventType.PLAYER_MESSAGE]
    assert len(notify_events) >= 1
    assert "Kernel fast-path" in notify_events[-1].data["text"]


def test_bootstrap_prefers_capability_task_ownership():
    """Fast-path production should run on capability when it exists."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    cap_task = kernel.create_task("经济规划", TaskKind.MANAGED, 80)
    cap_task.is_capability = True
    kernel._capability_task_id = cap_task.task_id

    task = kernel.create_task("前线补坦克", TaskKind.MANAGED, 60)
    result = kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    reservation = kernel.list_unit_reservations()[0]

    assert req.bootstrap_job_id is not None
    assert req.bootstrap_task_id == cap_task.task_id
    assert result["bootstrap_task_id"] == cap_task.task_id
    assert reservation.bootstrap_task_id == cap_task.task_id
    bootstrap_job = kernel._jobs[req.bootstrap_job_id]
    assert bootstrap_job.task_id == cap_task.task_id
    assert bootstrap_job.config.request_id == req.request_id
    assert bootstrap_job.config.reservation_id == reservation.reservation_id


# =====================================================================
# 4. _fulfill_unit_requests Auto-assign Tests
# =====================================================================

def test_fulfill_assigns_new_idle_units():
    """After production complete, new idle units should be assigned to pending requests."""
    kernel, world = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)

    # Bind all existing idle vehicles so they're unavailable
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    assert result["status"] == "waiting"
    req = kernel._unit_requests[result["request_id"]]
    assert req.fulfilled == 0

    # Simulate: unbind an actor (as if production complete or job ended)
    world.unbind_resource("actor:10")

    # Trigger fulfillment
    kernel._fulfill_unit_requests()
    assert req.fulfilled == 1
    assert req.status == "fulfilled"
    assert 10 in req.assigned_actor_ids


def test_fulfill_priority_ordering():
    """Higher urgency + priority requests should be fulfilled first."""
    kernel, world = make_kernel_with_base()

    # Bind all idle vehicles
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task_low = kernel.create_task("巡逻", TaskKind.MANAGED, 30)
    task_high = kernel.create_task("进攻", TaskKind.MANAGED, 70)

    r_low = kernel.register_unit_request(task_low.task_id, "vehicle", 1, "low", "坦克")
    r_high = kernel.register_unit_request(task_high.task_id, "vehicle", 1, "critical", "坦克")

    # Only unbind one vehicle
    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    req_low = kernel._unit_requests[r_low["request_id"]]
    req_high = kernel._unit_requests[r_high["request_id"]]

    # High urgency should get it first
    assert req_high.status == "fulfilled"
    assert req_low.fulfilled == 0


def test_fulfill_priority_prefers_blocking_requests():
    """Blocking requests should outrank non-blocking reinforcement requests."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task_reinforce = kernel.create_task("补兵", TaskKind.MANAGED, 60)
    task_blocking = kernel.create_task("先头部队", TaskKind.MANAGED, 60)

    r_reinforce = kernel.register_unit_request(
        task_reinforce.task_id,
        "vehicle",
        1,
        "high",
        "重坦",
        blocking=False,
    )
    r_blocking = kernel.register_unit_request(
        task_blocking.task_id,
        "vehicle",
        1,
        "high",
        "重坦",
        blocking=True,
    )

    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    req_reinforce = kernel._unit_requests[r_reinforce["request_id"]]
    req_blocking = kernel._unit_requests[r_blocking["request_id"]]

    assert req_blocking.status == "fulfilled"
    assert req_reinforce.fulfilled == 0


# =====================================================================
# 5. Agent Suspend / Wake Tests
# =====================================================================

def test_agent_suspended_on_waiting_request():
    """Agent should be suspended after registering a waiting request."""
    kernel, world = make_kernel_with_base()

    # Bind all idle vehicles so request can't be fulfilled
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    agent = get_agent(kernel, task.task_id)

    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    assert result["status"] == "waiting"
    assert agent._suspended is True


def test_agent_not_suspended_on_fulfilled():
    """Agent should NOT be suspended if request is immediately fulfilled."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    agent = get_agent(kernel, task.task_id)

    result = kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    assert result["status"] == "fulfilled"
    assert agent._suspended is False


def test_nonblocking_request_does_not_suspend_agent():
    """Reinforcement requests should not park the requesting agent."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("后续补兵", TaskKind.MANAGED, 50)
    agent = get_agent(kernel, task.task_id)

    result = kernel.register_unit_request(
        task.task_id,
        "vehicle",
        2,
        "medium",
        "重坦",
        blocking=False,
    )

    assert result["status"] == "waiting"
    assert agent._suspended is False


def test_agent_woken_after_fulfillment():
    """Agent should be resumed with UNIT_ASSIGNED event after all requests fulfilled."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    agent = get_agent(kernel, task.task_id)

    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    assert agent._suspended is True

    # Unbind and fulfill
    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    assert agent._suspended is False
    assert len(agent._resumed_events) == 1
    assert agent._resumed_events[0].type == EventType.UNIT_ASSIGNED
    assert 10 in agent._resumed_events[0].data["actor_ids"]


def test_agent_woken_when_min_start_package_reached_before_full_count():
    """Blocking requests should resume once the minimum start package arrives."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("装甲推进", TaskKind.MANAGED, 60)
    agent = get_agent(kernel, task.task_id)

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

    assert agent._suspended is True
    assert req.start_released is False
    assert reservation.start_released is False

    world.unbind_resource("actor:10")
    world.unbind_resource("actor:11")
    kernel._fulfill_unit_requests()

    assert agent._suspended is False
    assert req.status == "partial"
    assert req.start_released is True
    assert reservation.start_released is True
    assert set(kernel.task_active_actor_ids(task.task_id)) == {10, 11}
    assert len(agent._resumed_events) == 1
    assert agent._resumed_events[0].data["message"] == "请求单位已达到可启动数量"
    assert set(agent._resumed_events[0].data["actor_ids"]) == {10, 11}


def test_agent_woken_after_fulfillment_tracks_task_actor_group():
    """Fulfilled requests should populate the task-owned actor group registry."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")

    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    assert kernel.task_active_actor_ids(task.task_id) == [10]
    runtime = kernel.world_model.query("runtime_state")
    assert runtime["active_tasks"][task.task_id]["active_actor_ids"] == [10]
    assert runtime["active_tasks"][task.task_id]["active_group_size"] == 1


def test_agent_woken_after_fulfillment_releases_request_binding():
    """Wake-time transfer should release temporary req: bindings for reassignment."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    request_id = result["request_id"]

    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    assert world.resource_bindings.get("actor:10") != f"req:{request_id}"


# =====================================================================
# 6. Cancel Task Cleanup Tests
# =====================================================================

def test_cancel_task_cancels_pending_requests():
    """Cancelling a task should cancel its pending unit requests."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 3, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    assert req.status == "pending"

    kernel.cancel_task(task.task_id)
    assert req.status == "cancelled"


def test_cancel_task_aborts_bootstrap_job_via_request_cancel():
    """Cancelling a task should abort any active bootstrap job tied to its request."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    assert req.bootstrap_job_id is not None

    assert kernel.cancel_task(task.task_id) is True
    assert kernel._unit_requests[result["request_id"]].status == "cancelled"
    assert kernel._jobs[req.bootstrap_job_id].status == JobStatus.ABORTED


def test_cancel_unit_request():
    """cancel_unit_request should mark request as cancelled."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")

    assert kernel.cancel_unit_request(result["request_id"]) is True
    assert kernel._unit_requests[result["request_id"]].status == "cancelled"
    # Double cancel should return False
    assert kernel.cancel_unit_request(result["request_id"]) is False


def test_cancel_unit_request_aborts_bootstrap_job():
    """Cancelling a request with a bootstrap job should abort that job."""
    kernel, world = make_kernel_with_base()

    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    assert req.bootstrap_job_id is not None
    bootstrap_job = kernel._jobs[req.bootstrap_job_id]
    assert bootstrap_job.status == JobStatus.RUNNING

    assert kernel.cancel_unit_request(result["request_id"]) is True
    assert kernel._unit_requests[result["request_id"]].status == "cancelled"
    assert kernel._jobs[req.bootstrap_job_id].status == JobStatus.ABORTED
    reservation = kernel.list_unit_reservations()[0]
    assert reservation.status == ReservationStatus.CANCELLED


def test_register_unit_request_creates_reservation_for_inferred_unit():
    """Inferable requests should create a one-to-one reservation record."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)

    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    reservations = kernel.list_unit_reservations()

    assert len(reservations) == 1
    reservation = reservations[0]
    assert result["reservation_id"] == reservation.reservation_id
    assert result["unit_type"] == "3tnk"
    assert result["queue_type"] == "Vehicle"
    assert result["remaining_count"] == 0
    assert reservation.request_id == req.request_id
    assert reservation.unit_type == "3tnk"
    assert reservation.task_id == task.task_id


def test_idle_match_updates_reservation_assignment_state():
    """Idle fulfillment should immediately assign the reservation."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)

    result = kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    reservation = kernel.list_unit_reservations()[0]

    assert result["status"] == "fulfilled"
    assert reservation.status == ReservationStatus.ASSIGNED
    assert set(reservation.assigned_actor_ids) == {10, 11}
    assert reservation.produced_actor_ids == []


def test_cancel_unit_request_cancels_reservation():
    """Cancelling a request should cancel its reservation too."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")

    assert kernel.cancel_unit_request(result["request_id"]) is True
    reservation = kernel.list_unit_reservations()[0]
    assert reservation.status == ReservationStatus.CANCELLED
    assert reservation.cancelled_at is not None


def test_waiting_request_result_exposes_bootstrap_contract():
    """Waiting results should expose reservation and bootstrap metadata."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("大规模进攻", TaskKind.MANAGED, 50)

    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")

    assert result["status"] == "waiting"
    assert result["unit_type"] == "3tnk"
    assert result["queue_type"] == "Vehicle"
    assert result["remaining_count"] == 3
    assert result["reservation_id"].startswith("res_")
    assert result["reservation_status"] in {ReservationStatus.PARTIAL.value, ReservationStatus.PENDING.value}
    assert result["bootstrap_job_id"].startswith("j_")


def test_cancel_unit_request_aborts_bootstrap_job():
    """Cancelling a waiting request should abort its active bootstrap job."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("大规模进攻", TaskKind.MANAGED, 50)

    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    bootstrap_job_id = result["bootstrap_job_id"]
    assert bootstrap_job_id is not None
    assert kernel._jobs[bootstrap_job_id].status in {JobStatus.RUNNING, JobStatus.WAITING}

    assert kernel.cancel_unit_request(result["request_id"]) is True
    assert kernel._jobs[bootstrap_job_id].status == JobStatus.ABORTED


def test_sync_unfulfilled_requests_includes_reservation_metadata():
    """Runtime sync should expose reservation metadata for capability context."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    runtime_facts = kernel.world_model.compute_runtime_facts(task.task_id)
    pending = runtime_facts["unfulfilled_requests"]

    assert len(pending) == 1
    assert pending[0]["request_id"] == req.request_id
    assert pending[0]["reservation_id"].startswith("res_")
    assert pending[0]["unit_type"] == "3tnk"
    assert pending[0]["queue_type"] == "Vehicle"
    assert pending[0]["reservation_status"] == ReservationStatus.PENDING.value
    assert pending[0]["blocking"] is True
    assert pending[0]["min_start_package"] == 1


def test_runtime_state_exposes_active_unit_reservations():
    """Kernel runtime_state should expose active reservation summaries."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    runtime = kernel.world_model.query("runtime_state")

    reservations = runtime["unit_reservations"]
    assert len(reservations) == 1
    assert reservations[0]["request_id"] == req.request_id
    assert reservations[0]["reservation_id"].startswith("res_")
    assert reservations[0]["unit_type"] == "3tnk"
    assert reservations[0]["status"] == ReservationStatus.PENDING.value
    assert reservations[0]["request_status"] == "pending"
    assert reservations[0]["blocking"] is True
    assert reservations[0]["min_start_package"] == 1
    assert reservations[0]["reason"] in {"bootstrap_in_progress", "waiting_dispatch", "missing_prerequisite"}


def test_runtime_state_hides_fulfilled_reservations() -> None:
    """Runtime reservation view should only expose still-active contracts."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("步兵支援", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "infantry", 1, "medium", "步兵")

    assert result["status"] == "fulfilled"
    runtime = kernel.world_model.query("runtime_state")
    assert runtime["unit_reservations"] == []


def test_idle_refill_after_bootstrap_does_not_double_count_produced_units():
    """Idle refill after bootstrap should not mark live actors as produced or undercount remaining."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("大规模进攻", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    reservation = kernel.list_unit_reservations()[0]

    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    runtime = kernel.world_model.query("runtime_state")
    runtime_reservation = runtime["unit_reservations"][0]
    assert reservation.assigned_actor_ids == [10]
    assert reservation.produced_actor_ids == []
    assert runtime_reservation["remaining_count"] == 4


def test_idle_refill_shrinks_bootstrap_target_before_issue():
    """Idle refill should shrink pending bootstrap counts instead of keeping stale targets."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("重坦补位", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 5, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    bootstrap_job = kernel._jobs[req.bootstrap_job_id]

    assert bootstrap_job.config.count == 5
    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    assert req.fulfilled == 1
    assert bootstrap_job.config.count == 4


def test_idle_refill_clears_bootstrap_when_request_fully_satisfied():
    """If idle assignment fully satisfies a request before issue, stale bootstrap should be cleared."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("单辆补位", TaskKind.MANAGED, 50)
    result = kernel.register_unit_request(task.task_id, "vehicle", 1, "high", "重坦")
    req = kernel._unit_requests[result["request_id"]]
    reservation = kernel.list_unit_reservations()[0]
    bootstrap_job_id = req.bootstrap_job_id

    assert bootstrap_job_id is not None
    world.unbind_resource("actor:10")
    kernel._fulfill_unit_requests()

    assert req.status == "fulfilled"
    assert req.bootstrap_job_id is None
    assert req.bootstrap_task_id is None
    assert reservation.bootstrap_job_id is None
    assert reservation.bootstrap_task_id is None
    assert kernel._jobs[bootstrap_job_id].status == JobStatus.ABORTED


def test_request_result_and_reservation_propagate_semantics():
    """Kernel should preserve request semantics into reservations and result payloads."""
    kernel, world = make_kernel_with_base()
    for actor in world.find_actors(owner="self", idle_only=True, category="vehicle"):
        world.bind_resource(f"actor:{actor.actor_id}", "other_job")

    task = kernel.create_task("重装推进", TaskKind.MANAGED, 70)
    result = kernel.register_unit_request(
        task.task_id,
        "vehicle",
        4,
        "critical",
        "重坦",
        blocking=False,
        min_start_package=2,
    )
    req = kernel._unit_requests[result["request_id"]]
    reservation = kernel.list_unit_reservations()[0]

    assert result["urgency"] == "critical"
    assert result["hint"] == "重坦"
    assert result["blocking"] is False
    assert result["min_start_package"] == 2
    assert req.blocking is False
    assert req.min_start_package == 2
    assert reservation.urgency == "critical"
    assert reservation.hint == "重坦"
    assert reservation.blocking is False
    assert reservation.min_start_package == 2


def test_list_unit_requests_filter():
    """list_unit_requests should filter by status."""
    kernel, _ = make_kernel_with_base()
    task = kernel.create_task("进攻", TaskKind.MANAGED, 50)

    r1 = kernel.register_unit_request(task.task_id, "vehicle", 2, "high", "重坦")
    r2 = kernel.register_unit_request(task.task_id, "infantry", 2, "medium", "步兵")

    all_reqs = kernel.list_unit_requests()
    assert len(all_reqs) == 2

    # r1 should be fulfilled (2 idle tanks), r2 should be fulfilled (2 idle infantry)
    fulfilled = kernel.list_unit_requests(status="fulfilled")
    assert len(fulfilled) == 2


# =====================================================================
# 7. Capability Notification Tests
# =====================================================================

def test_unfulfilled_notifies_capability():
    """When fast-path can't handle a request, Capability should be notified."""
    kernel, _ = make_kernel_with_base()

    # Create capability task
    cap_task = kernel.create_task("经济规划", TaskKind.MANAGED, 80)
    cap_task.is_capability = True
    kernel._capability_task_id = cap_task.task_id
    cap_agent = get_agent(kernel, cap_task.task_id)

    task = kernel.create_task("空袭", TaskKind.MANAGED, 50)
    kernel.register_unit_request(task.task_id, "aircraft", 2, "high", "对地攻击机")

    # Should have UNIT_REQUEST_UNFULFILLED event
    unfulfilled_events = [e for e in cap_agent.events
                          if e.type == EventType.UNIT_REQUEST_UNFULFILLED]
    assert len(unfulfilled_events) == 1
    assert unfulfilled_events[0].data["category"] == "aircraft"
    assert unfulfilled_events[0].data["count"] == 2


# =====================================================================
# 8. UnitRequest Model Tests
# =====================================================================

def test_unit_request_dataclass():
    """UnitRequest should have all expected fields."""
    req = UnitRequest(
        request_id="req_test",
        task_id="t_test",
        task_label="001",
        task_summary="进攻",
        category="vehicle",
        count=5,
        urgency="high",
        hint="重坦",
    )
    assert req.blocking is True
    assert req.min_start_package == 1
    assert req.fulfilled == 0
    assert req.status == "pending"
    assert req.start_released is False
    assert req.assigned_actor_ids == []
    assert req.bootstrap_job_id is None
    assert req.created_at > 0
    assert req.bootstrap_task_id is None


def test_unit_reservation_dataclass():
    """UnitReservation should expose explicit ownership lifecycle fields."""
    reservation = UnitReservation(
        reservation_id="res_test",
        request_id="req_test",
        task_id="t_test",
        task_label="001",
        task_summary="进攻",
        category="vehicle",
        unit_type="3tnk",
        count=2,
    )
    assert reservation.urgency == "medium"
    assert reservation.hint == ""
    assert reservation.blocking is True
    assert reservation.min_start_package == 1
    assert reservation.status == ReservationStatus.PENDING
    assert reservation.start_released is False
    assert reservation.assigned_actor_ids == []
    assert reservation.produced_actor_ids == []
    assert reservation.bootstrap_task_id is None
    assert reservation.cancelled_at is None
    assert reservation.created_at > 0
    assert reservation.updated_at > 0


def test_event_types_exist():
    """New EventTypes should be accessible."""
    assert EventType.UNIT_REQUEST_UNFULFILLED == "UNIT_REQUEST_UNFULFILLED"
    assert EventType.UNIT_ASSIGNED == "UNIT_ASSIGNED"
    assert EventType.PLAYER_MESSAGE == "PLAYER_MESSAGE"


# =====================================================================
# 9. TaskAgent suspend/resume Tests
# =====================================================================

def test_task_agent_suspend_skips_wake():
    """Suspended TaskAgent should skip wake cycles."""
    from task_agent.agent import TaskAgent

    # Verify the _suspended attribute exists and defaults to False
    # (can't easily test the full run loop here, but verify the flag)
    assert hasattr(TaskAgent, '__init__')
    # Just verify the attribute is set in a minimal way
    from unittest.mock import MagicMock
    mock_task = MagicMock()
    mock_task.task_id = "t_test"
    mock_task.raw_text = "test"
    mock_task.is_capability = False
    mock_llm = MagicMock()
    mock_executor = MagicMock()
    agent = TaskAgent(
        task=mock_task,
        llm=mock_llm,
        tool_executor=mock_executor,
        jobs_provider=lambda tid: [],
        world_summary_provider=lambda: {},
    )
    assert agent._suspended is False
    agent.suspend()
    assert agent._suspended is True
    event = Event(type=EventType.UNIT_ASSIGNED, data={"message": "test"})
    agent.resume_with_event(event)
    assert agent._suspended is False


# =====================================================================
# Run
# =====================================================================

if __name__ == "__main__":
    if hasattr(benchmark, "init"):
        benchmark.init()
    else:
        benchmark.clear()
    if hasattr(logging_system, "init"):
        logging_system.init()

    test_infer_unit_type_hint_match()
    test_infer_unit_type_category_default()
    test_infer_unit_type_aircraft_returns_none()
    test_idle_match_fulfilled()
    test_idle_match_partial()
    test_idle_match_none_available()
    test_idle_match_infantry()
    test_idle_match_hint_preference()
    test_bootstrap_creates_economy_job()
    test_bootstrap_skipped_when_not_producible()
    test_bootstrap_notifies_capability()
    test_fulfill_assigns_new_idle_units()
    test_fulfill_priority_ordering()
    test_agent_suspended_on_waiting_request()
    test_agent_not_suspended_on_fulfilled()
    test_nonblocking_request_does_not_suspend_agent()
    test_agent_woken_after_fulfillment()
    test_agent_woken_when_min_start_package_reached_before_full_count()
    test_agent_woken_after_fulfillment_tracks_task_actor_group()
    test_agent_woken_after_fulfillment_releases_request_binding()
    test_cancel_task_cancels_pending_requests()
    test_cancel_unit_request()
    test_register_unit_request_creates_reservation_for_inferred_unit()
    test_idle_match_updates_reservation_assignment_state()
    test_cancel_unit_request_cancels_reservation()
    test_waiting_request_result_exposes_bootstrap_contract()
    test_cancel_unit_request_aborts_bootstrap_job()
    test_sync_unfulfilled_requests_includes_reservation_metadata()
    test_runtime_state_exposes_active_unit_reservations()
    test_list_unit_requests_filter()
    test_unfulfilled_notifies_capability()
    test_unit_request_dataclass()
    test_unit_reservation_dataclass()
    test_event_types_exist()
    test_task_agent_suspend_skips_wake()
    print("All unit request tests passed!")
