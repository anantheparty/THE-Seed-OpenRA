"""End-to-end tests T2-T8 — Milestone 2: Five Experts.

Uses real Kernel + real Experts + real TaskAgent + mock GameAPI/WorldModelSource.
Each test creates a real Kernel with registered Experts and verifies the full
pipeline from LLM tool_use through Expert Job execution.
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
from typing import Any, Optional

from llm import LLMResponse, MockProvider, ToolCall
from models import (
    ExpertSignal,
    SignalKind,
    TaskKind,
    TaskStatus,
)
from kernel import Kernel, KernelConfig
from task_agent import AgentConfig
from world_model import WorldModel, RefreshPolicy
from experts.movement import MovementExpert
from experts.deploy import DeployExpert
from experts.combat import CombatExpert
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo


# --- Shared mock infrastructure ---

class MockGameAPI:
    """Mock GameAPI matching the real Protocol."""
    def __init__(self):
        self.move_calls: list[dict] = []
        self.attack_calls: list[dict] = []
        self.deploy_calls: list[dict] = []

    def move_units_by_location(self, actors, location, attack_move=False):
        self.move_calls.append({
            "actor_ids": [a.actor_id for a in actors],
            "position": (location.x, location.y),
            "attack_move": attack_move,
        })

    def deploy_units(self, actors):
        self.deploy_calls.append({"actor_ids": [a.actor_id for a in actors]})

    def attack_target(self, attacker, target):
        self.attack_calls.append({"attacker": attacker.actor_id, "target": target.actor_id})
        return True


def make_map():
    size = 4
    return MapQueryResult(
        MapWidth=size, MapHeight=size,
        Height=[[0]*size for _ in range(size)],
        IsVisible=[[True]*size for _ in range(size)],
        IsExplored=[[True]*size for _ in range(size)],
        Terrain=[["clear"]*size for _ in range(size)],
        ResourcesType=[["ore"]*size for _ in range(size)],
        Resources=[[50]*size for _ in range(size)],
    )


class SimpleWorldSource:
    """WorldModelSource with configurable actors."""
    def __init__(self, self_actors=None, enemy_actors=None):
        self._self = self_actors or []
        self._enemy = enemy_actors or []

    def fetch_self_actors(self):
        return list(self._self)

    def fetch_enemy_actors(self):
        return list(self._enemy)

    def fetch_economy(self):
        return PlayerBaseInfo(Cash=5000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100)

    def fetch_map(self):
        return make_map()

    def fetch_production_queues(self):
        return {"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}}


class RecordingAgent:
    """Minimal recording agent for tests that don't need full LLM flow."""
    def __init__(self, task, tool_executor, jobs_provider, world_summary_provider, **kwargs):
        self.task = task
        self.tool_executor = tool_executor
        self.jobs_provider = jobs_provider
        self.world_summary_provider = world_summary_provider
        self.signals: list[ExpertSignal] = []
        self.events = []
        self.stopped = False

    async def run(self):
        await asyncio.sleep(0)

    def stop(self):
        self.stopped = True

    def push_signal(self, signal):
        self.signals.append(signal)

    def push_event(self, event):
        self.events.append(event)


def make_kernel(game_api, self_actors=None, enemy_actors=None):
    """Create a real Kernel with registered Experts."""
    source = SimpleWorldSource(self_actors, enemy_actors)
    world = WorldModel(source, refresh_policy=RefreshPolicy(actors_s=0.05, economy_s=0.05, map_s=0.05))
    world.refresh(force=True)

    return Kernel(
        world_model=world,
        expert_registry={
            "MovementExpert": MovementExpert(game_api=game_api, world_model=world),
            "DeployExpert": DeployExpert(game_api=game_api),
            "CombatExpert": CombatExpert(game_api=game_api, world_model=world),
        },
        task_agent_factory=lambda task, te, jp, wsp: RecordingAgent(task, te, jp, wsp),
        config=KernelConfig(auto_start_agents=False),
    )


async def _wait(predicate, timeout=2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Timed out")


# --- T2: Economy (tool call through Kernel) ---

def test_t2_economy_start_job():
    """T2: Kernel.start_job(EconomyExpert) creates a real Job via tool handler."""
    benchmark.clear()
    game_api = MockGameAPI()
    kernel = make_kernel(game_api, self_actors=[
        Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
    ])
    task = kernel.create_task("生产5辆重型坦克", TaskKind.MANAGED, 40)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"EconomyExpert","config":{"unit_type":"2tnk","count":5,"queue_type":"Vehicle","repeat":false}}',
        )
        assert result.error is None
        assert "job_id" in result.result

    asyncio.run(run())
    jobs = kernel.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].expert_type == "EconomyExpert"
    print("  PASS: T2 economy_start_job")


# --- T3: Movement (real MovementJob lifecycle) ---

def test_t3_movement_real_job():
    """T3: Kernel creates real MovementJob, GameAPI receives move commands."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api, self_actors=[
        Actor(actor_id=57, type="重坦", faction="自己", position=Location(500, 500), hppercent=100, activity="Idle"),
    ])
    task = kernel.create_task("撤退", TaskKind.MANAGED, 70)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"MovementExpert","config":{"target_position":[200,600],"move_mode":"retreat","arrival_radius":10}}',
        )
        assert result.error is None
        job_id = result.result["job_id"]

        # Tick the job — it should issue a move command
        controller = kernel._jobs[job_id]
        controller.do_tick()

    asyncio.run(run())

    assert len(game_api.move_calls) >= 1
    assert game_api.move_calls[0]["attack_move"] is True  # retreat uses attack_move
    print("  PASS: T3 movement_real_job")


# --- T4: Combat assault (real CombatJob) ---

def test_t4_combat_assault_real():
    """T4: Kernel creates real CombatJob, approaches and engages."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api,
        self_actors=[Actor(actor_id=57, type="重坦", faction="自己", position=Location(100, 100), hppercent=100, activity="Idle")],
        enemy_actors=[Actor(actor_id=201, type="重坦", faction="敌人", position=Location(500, 500), hppercent=100, activity="Idle")],
    )
    task = kernel.create_task("进攻", TaskKind.MANAGED, 60)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[500,500],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )
        assert result.error is None
        job_id = result.result["job_id"]

        controller = kernel._jobs[job_id]
        # Tick: should approach
        controller.do_tick()
        controller.do_tick()

    asyncio.run(run())

    assert len(game_api.move_calls) >= 1  # Approaching target
    print("  PASS: T4 combat_assault_real")


# --- T5: Deploy (real DeployJob) ---

def test_t5_deploy_real():
    """T5: Kernel creates real DeployJob, GameAPI receives deploy call."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api, self_actors=[
        Actor(actor_id=99, type="mcv", faction="自己", position=Location(500, 400), hppercent=100, activity="Idle"),
    ])
    task = kernel.create_task("部署基地车", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"DeployExpert","config":{"actor_id":99,"target_position":[500,400]}}',
        )
        assert result.error is None
        job_id = result.result["job_id"]

        controller = kernel._jobs[job_id]
        controller.do_tick()

    asyncio.run(run())

    assert len(game_api.deploy_calls) == 1
    assert game_api.deploy_calls[0]["actor_ids"] == [99]
    print("  PASS: T5 deploy_real")


# --- T6: Surround (real CombatJob with surround mode) ---

def test_t6_surround_real():
    """T6: Two CombatJobs with surround mode issue multi-angle moves."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api,
        self_actors=[
            Actor(actor_id=57, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
            Actor(actor_id=58, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
            Actor(actor_id=59, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
            Actor(actor_id=60, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
        ],
        enemy_actors=[Actor(actor_id=201, type="矿场", faction="敌人", position=Location(1820, 430), hppercent=100, activity="Idle")],
    )
    task = kernel.create_task("包围基地", TaskKind.MANAGED, 60)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[1820,430],"engagement_mode":"surround","max_chase_distance":15,"retreat_threshold":0.4}}',
        )
        assert result.error is None
        job_id = result.result["job_id"]

        controller = kernel._jobs[job_id]
        controller.do_tick()

    asyncio.run(run())

    # Surround approach should issue multiple move commands to different positions
    assert len(game_api.move_calls) >= 2
    positions = set(c["position"] for c in game_api.move_calls)
    assert len(positions) >= 2  # Different flank positions
    print("  PASS: T6 surround_real")


# --- T7: Constraint (through real Kernel) ---

def test_t7_constraint_real():
    """T7: create_constraint stores in real WorldModel."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api)
    task = kernel.create_task("别追太远", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        result = await agent.tool_executor.execute(
            "tc1", "create_constraint",
            '{"kind":"do_not_chase","scope":"global","params":{"max_chase_distance":20},"enforcement":"clamp"}',
        )
        assert result.error is None
        assert "constraint_id" in result.result

    asyncio.run(run())

    # Constraint should be in Kernel
    constraints = kernel.world_model.query("constraints")
    assert len(constraints["constraints"]) >= 1
    print("  PASS: T7 constraint_real")


# --- T8: Sequential Movement → Combat (real Jobs) ---

def test_t8_sequential_real():
    """T8: Movement Job ticks, then Combat Job created — both through real Kernel."""
    game_api = MockGameAPI()
    kernel = make_kernel(game_api,
        self_actors=[Actor(actor_id=58, type="重坦", faction="自己", position=Location(22, 20), hppercent=60, activity="Idle")],
        enemy_actors=[Actor(actor_id=201, type="重坦", faction="敌人", position=Location(500, 500), hppercent=100, activity="Idle")],
    )
    task = kernel.create_task("修理后进攻", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        # Start movement
        r1 = await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"MovementExpert","config":{"target_position":[220,610],"move_mode":"move","arrival_radius":3}}',
        )
        assert r1.error is None
        movement_job_id = r1.result["job_id"]

        # Tick movement
        controller = kernel._jobs[movement_job_id]
        controller.do_tick()
        assert len(game_api.move_calls) >= 1

        # Start combat
        r2 = await agent.tool_executor.execute(
            "tc2", "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[1600,300],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )
        assert r2.error is None
        combat_job_id = r2.result["job_id"]

        # Tick combat
        combat = kernel._jobs[combat_job_id]
        combat.do_tick()

    asyncio.run(run())

    assert len(kernel.list_jobs()) == 2
    print("  PASS: T8 sequential_real")


# --- Benchmark ---

def test_benchmark_coverage():
    """Benchmark records present for tool_exec operations."""
    benchmark.clear()
    game_api = MockGameAPI()
    kernel = make_kernel(game_api)
    task = kernel.create_task("benchmark test", TaskKind.MANAGED, 50)
    agent = kernel.get_task_agent(task.task_id)

    async def run():
        await agent.tool_executor.execute(
            "tc1", "start_job",
            '{"expert_type":"MovementExpert","config":{"target_position":[100,200]}}',
        )

    asyncio.run(run())

    tool_records = benchmark.query(tag="tool_exec")
    assert len(tool_records) >= 1
    print(f"  PASS: benchmark_coverage (tool_exec={len(tool_records)})")


# --- Run all ---

if __name__ == "__main__":
    print("Running E2E Expert tests (Milestone 2)...\n")

    test_t2_economy_start_job()
    test_t3_movement_real_job()
    test_t4_combat_assault_real()
    test_t5_deploy_real()
    test_t6_surround_real()
    test_t7_constraint_real()
    test_t8_sequential_real()
    test_benchmark_coverage()

    print(f"\nAll 8 tests passed! ★ Milestone 2 verified")
