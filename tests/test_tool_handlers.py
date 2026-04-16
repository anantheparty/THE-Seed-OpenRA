"""Tests for TaskToolHandlers — end-to-end with mock Kernel + WorldModel."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from llm import LLMResponse, MockProvider, ToolCall
from models import (
    CombatJobConfig,
    EconomyJobConfig,
    ExpertSignal,
    Job,
    JobStatus,
    OccupyJobConfig,
    ReconJobConfig,
    SignalKind,
    StopJobConfig,
    Task,
    TaskKind,
    TaskStatus,
)
from task_agent import AgentConfig, TaskAgent, ToolExecutor, WorldSummary
from task_agent.handlers import TaskToolHandlers


# --- Mock Kernel ---

class MockKernel:
    def __init__(self):
        self.started_jobs: list[dict] = []
        self.patched_jobs: list[dict] = []
        self.paused_jobs: list[str] = []
        self.resumed_jobs: list[str] = []
        self.aborted_jobs: list[str] = []
        self.completed_tasks: list[dict] = []
        self.cancelled_filters: list[dict] = []
        self._job_counter = 0
        self._active_actor_ids: list[int] = []
        self._has_running_actor_job = False
        self._jobs_for_task: list[Job] = []

    def start_job(self, task_id: str, expert_type: str, config: Any) -> Job:
        self._job_counter += 1
        job_id = f"j_{self._job_counter}"
        self.started_jobs.append({"task_id": task_id, "expert_type": expert_type, "job_id": job_id, "config": config})
        return Job(
            job_id=job_id,
            task_id=task_id,
            expert_type=expert_type,
            config=config,
            status=JobStatus.RUNNING,
        )

    def patch_job(self, job_id: str, params: dict) -> bool:
        self.patched_jobs.append({"job_id": job_id, "params": params})
        return True

    def pause_job(self, job_id: str) -> bool:
        self.paused_jobs.append(job_id)
        return True

    def resume_job(self, job_id: str) -> bool:
        self.resumed_jobs.append(job_id)
        return True

    def abort_job(self, job_id: str) -> bool:
        self.aborted_jobs.append(job_id)
        return True

    def complete_task(self, task_id: str, result: str, summary: str) -> bool:
        self.completed_tasks.append({"task_id": task_id, "result": result, "summary": summary})
        return True

    def cancel_tasks(self, filters: dict) -> int:
        self.cancelled_filters.append(filters)
        return 1

    def jobs_for_task(self, task_id: str) -> list[Job]:
        return list(self._jobs_for_task)

    def register_task_message(self, message: Any) -> bool:
        return True

    def task_active_actor_ids(self, task_id: str) -> list[int]:
        return list(self._active_actor_ids)

    def task_has_running_actor_job(self, task_id: str) -> bool:
        return self._has_running_actor_job


class MockWorldModel:
    def __init__(self):
        self.queries: list[dict] = []
        self.constraints: dict[str, Any] = {}
        self._production_queues: dict[str, Any] = {}
        self._capability_status: dict[str, Any] = {}

    def query(self, query_type: str, params: Optional[dict] = None) -> Any:
        self.queries.append({"query_type": query_type, "params": params})
        if query_type == "actor_by_id" and params == {"actor_id": 201}:
            return {"actor": {"actor_id": 201, "position": [600, 700]}, "timestamp": time.time()}
        if query_type == "my_actors":
            return {"actors": [{"actor_id": 57, "name": "2tnk"}], "timestamp": time.time()}
        if query_type == "world_summary":
            return {"economy": {"cash": 5000}, "military": {"units": 10}, "timestamp": time.time()}
        if query_type == "production_queues":
            return dict(self._production_queues)
        if query_type == "capability_status":
            return dict(self._capability_status)
        return {"data": [], "timestamp": time.time()}

    def set_constraint(self, constraint: Any) -> None:
        self.constraints[constraint.constraint_id] = constraint

    def remove_constraint(self, constraint_id: str) -> None:
        self.constraints.pop(constraint_id, None)


# --- Tests ---

def test_handlers_register_all():
    """Ordinary managed tasks hide capability-only tool handlers."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    ordinary_visible_tools = {
        "deploy_mcv",
        "scout_map",
        "move_units",
        "move_units_by_path",
        "stop_units",
        "repair_units",
        "occupy_target",
        "attack",
        "attack_actor",
        "patch_job",
        "pause_job",
        "resume_job",
        "abort_job",
        "complete_task",
        "create_constraint",
        "remove_constraint",
        "query_world",
        "query_planner",
        "cancel_tasks",
        "send_task_message",
        "update_subscriptions",
    }

    for name in ordinary_visible_tools:
        assert name in executor._handlers, f"Missing handler: {name}"
    assert "produce_units" not in executor._handlers
    assert "set_rally_point" not in executor._handlers
    assert "request_units" in executor._handlers
    print("  PASS: handlers_register_all")


def test_capability_handlers_register_capability_only_surface():
    """Capability tasks expose production tools and hide request_units."""
    kernel = MockKernel()
    wm = MockWorldModel()
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
    task.is_capability = True
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    for name in ("produce_units", "set_rally_point", "query_world", "query_planner", "update_subscriptions", "send_task_message"):
        assert name in executor._handlers, f"Missing capability tool handler: {name}"
    assert "request_units" not in executor._handlers
    print("  PASS: capability_handlers_register_capability_only_surface")


def test_produce_units_rejects_duplicate_active_economy_job() -> None:
    kernel = MockKernel()
    kernel._jobs_for_task = [
        Job(
            job_id="j_dup",
            task_id="t_cap",
            expert_type="EconomyExpert",
            config=EconomyJobConfig(unit_type="e1", count=1, queue_type="Infantry"),
            status=JobStatus.RUNNING,
        )
    ]
    wm = MockWorldModel()
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
    task.is_capability = True
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "produce_units",
            '{"unit_type":"e1","count":1,"queue_type":"Infantry"}',
        )
        assert result.error is not None
        assert "existing_job_id=j_dup" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: produce_units_rejects_duplicate_active_economy_job")


def test_produce_units_rejects_duplicate_queue_item() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    wm._production_queues = {
        "Infantry": {
            "items": [
                {"name": "e1", "status": "building"},
            ]
        }
    }
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
    task.is_capability = True
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "produce_units",
            '{"unit_type":"e1","count":1,"queue_type":"Infantry"}',
        )
        assert result.error is not None
        assert "production_queue" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: produce_units_rejects_duplicate_queue_item")


def test_produce_units_rejects_ready_queue_item() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    wm._production_queues = {
        "Building": {
            "items": [
                {"name": "powr", "status": "done"},
            ]
        }
    }
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
    task.is_capability = True
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "produce_units",
            '{"unit_type":"powr","count":1,"queue_type":"Building"}',
        )
        assert result.error is not None
        assert "ready_queue_item" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: produce_units_rejects_ready_queue_item")


def test_produce_units_rejects_hard_capability_blockers() -> None:
    for blocker in ("world_sync_stale", "queue_blocked"):
        kernel = MockKernel()
        wm = MockWorldModel()
        wm._capability_status = {"task_id": "t_cap", "blocker": blocker}
        task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
        task.is_capability = True
        handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
        executor = ToolExecutor()
        handlers.register_all(executor)

        async def run():
            result = await executor.execute(
                f"tc_{blocker}",
                "produce_units",
                '{"unit_type":"e1","count":1,"queue_type":"Infantry"}',
            )
            assert result.error is not None
            assert blocker in result.error

        asyncio.run(run())
        assert kernel.started_jobs == []
    print("  PASS: produce_units_rejects_hard_capability_blockers")


def test_produce_units_allows_distinct_unit_when_guards_are_clear() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    wm._production_queues = {
        "Infantry": {
            "items": [
                {"name": "e3", "status": "building"},
            ]
        }
    }
    task = Task(task_id="t_cap", raw_text="发展经济", kind=TaskKind.MANAGED, priority=50)
    task.is_capability = True
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "produce_units",
            '{"unit_type":"e1","count":1,"queue_type":"Infantry"}',
        )
        assert result.error is None
        assert result.result["status"] == "running"

    asyncio.run(run())
    assert len(kernel.started_jobs) == 1
    assert isinstance(kernel.started_jobs[0]["config"], EconomyJobConfig)
    assert kernel.started_jobs[0]["config"].unit_type == "e1"
    print("  PASS: produce_units_allows_distinct_unit_when_guards_are_clear")


def test_scout_map_rejects_produce_then_recon_before_units_are_assigned() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    task = Task(task_id="t1", raw_text="整点步兵，探索一下地图", kind=TaskKind.MANAGED, priority=50)
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "scout_map",
            '{"search_region":"enemy_half","target_type":"base","avoid_combat":true}',
        )
        assert result.error is not None
        assert "produce_units_then_recon" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: scout_map_rejects_produce_then_recon_before_units_are_assigned")


def test_scout_map_allows_produce_then_recon_once_task_has_units() -> None:
    kernel = MockKernel()
    kernel._active_actor_ids = [57]
    wm = MockWorldModel()
    task = Task(task_id="t1", raw_text="整点步兵，探索一下地图", kind=TaskKind.MANAGED, priority=50)
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "scout_map",
            '{"search_region":"enemy_half","target_type":"base","avoid_combat":true}',
        )
        assert result.error is None
        assert result.result["status"] == "running"

    asyncio.run(run())
    assert len(kernel.started_jobs) == 1
    assert isinstance(kernel.started_jobs[0]["config"], ReconJobConfig)
    assert kernel.started_jobs[0]["config"].actor_ids == [57]
    print("  PASS: scout_map_allows_produce_then_recon_once_task_has_units")


def test_attack_rejects_produce_then_attack_before_units_are_assigned() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    task = Task(task_id="t1", raw_text="整一大批步兵和防空车，准备一轮进攻", kind=TaskKind.MANAGED, priority=50)
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "attack",
            '{"target_position":[30,40],"engagement_mode":"assault"}',
        )
        assert result.error is not None
        assert "produce_units_then_attack" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: attack_rejects_produce_then_attack_before_units_are_assigned")


def test_scout_map_requires_owned_units_or_explicit_actor_ids_for_ordinary_task() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    task = Task(task_id="t1", raw_text="侦察东北方向", kind=TaskKind.MANAGED, priority=50)
    handlers = TaskToolHandlers(task=task, kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1",
            "scout_map",
            '{"search_region":"enemy_half","target_type":"base","avoid_combat":true}',
        )
        assert result.error is not None
        assert "request_units" in result.error
        assert "actor_ids" in result.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: scout_map_requires_owned_units_or_explicit_actor_ids_for_ordinary_task")


def test_start_job_handler():
    """start_job handler calls Kernel.start_job with correct config."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        result = await executor.execute(
            "tc1", "start_job",
            '{"expert_type":"ReconExpert","config":{"search_region":"enemy_half","target_type":"base","target_owner":"enemy"}}',
        )
        assert result.error is None
        assert "job_id" in result.result
        assert result.result["status"] == "running"
        assert "timestamp" in result.result

    asyncio.run(run())
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["expert_type"] == "ReconExpert"
    assert kernel.started_jobs[0]["task_id"] == "t1"
    print("  PASS: start_job_handler")


def test_patch_pause_resume_abort_handlers():
    """Job lifecycle handlers call Kernel correctly."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "patch_job", '{"job_id":"j1","params":{"max_chase_distance":10}}')
        assert r.error is None and r.result["ok"]

        r = await executor.execute("tc2", "pause_job", '{"job_id":"j1"}')
        assert r.error is None and r.result["ok"]

        r = await executor.execute("tc3", "resume_job", '{"job_id":"j1"}')
        assert r.error is None and r.result["ok"]

        r = await executor.execute("tc4", "abort_job", '{"job_id":"j1"}')
        assert r.error is None and r.result["ok"]

    asyncio.run(run())
    assert kernel.patched_jobs[0]["params"]["max_chase_distance"] == 10
    assert kernel.paused_jobs == ["j1"]
    assert kernel.resumed_jobs == ["j1"]
    assert kernel.aborted_jobs == ["j1"]
    print("  PASS: patch_pause_resume_abort_handlers")


def test_complete_task_handler():
    """complete_task handler calls Kernel.complete_task."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "complete_task", '{"result":"succeeded","summary":"Found base"}')
        assert r.error is None and r.result["ok"]

    asyncio.run(run())
    assert kernel.completed_tasks[0] == {"task_id": "t1", "result": "succeeded", "summary": "Found base"}
    print("  PASS: complete_task_handler")


def test_query_world_handler():
    """query_world handler calls WorldModel.query with correct mapping."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "query_world", '{"query_type":"my_actors"}')
        assert r.error is None
        assert "data" in r.result
        assert "timestamp" in r.result

        r = await executor.execute("tc2", "query_world", '{"query_type":"threat_assessment"}')
        assert r.error is None
        assert "error" in r.result

    asyncio.run(run())
    assert wm.queries[0]["query_type"] == "my_actors"
    assert len(wm.queries) == 1
    print("  PASS: query_world_handler")


def test_query_world_blocks_global_my_actors_for_owned_force_task_without_units() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(
        task=Task(task_id="t1", raw_text="进攻敌方基地", kind=TaskKind.MANAGED, priority=50),
        kernel=kernel,
        world_model=wm,
    )
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "query_world", '{"query_type":"my_actors"}')
        assert r.error is None
        assert "error" in r.result
        assert "request_units" in r.result["error"]
        assert "task-owned units" in r.result["error"]

    asyncio.run(run())
    assert wm.queries == []
    print("  PASS: query_world_blocks_global_my_actors_for_owned_force_task_without_units")


def test_query_world_blocks_global_my_actors_for_harass_task_without_units() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(
        task=Task(task_id="t1", raw_text="骚扰敌方基地", kind=TaskKind.MANAGED, priority=50),
        kernel=kernel,
        world_model=wm,
    )
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "query_world", '{"query_type":"my_actors"}')
        assert r.error is None
        assert "error" in r.result
        assert "request_units" in r.result["error"]

    asyncio.run(run())
    assert wm.queries == []
    print("  PASS: query_world_blocks_global_my_actors_for_harass_task_without_units")


def test_query_world_allows_my_actors_for_owned_force_task_once_units_are_owned() -> None:
    kernel = MockKernel()
    kernel._active_actor_ids = [57]
    wm = MockWorldModel()
    handlers = TaskToolHandlers(
        task=Task(task_id="t1", raw_text="进攻敌方基地", kind=TaskKind.MANAGED, priority=50),
        kernel=kernel,
        world_model=wm,
    )
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "query_world", '{"query_type":"my_actors"}')
        assert r.error is None
        assert "data" in r.result

    asyncio.run(run())
    assert wm.queries[0]["query_type"] == "my_actors"
    print("  PASS: query_world_allows_my_actors_for_owned_force_task_once_units_are_owned")


def test_attack_actor_handler_creates_precise_combat_job() -> None:
    """attack_actor should create a CombatExpert job locked to a specific target actor."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(
        task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50),
        kernel=kernel,
        world_model=wm,
    )
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute(
            "tc1",
            "attack_actor",
            '{"target_actor_id":201,"engagement_mode":"assault","actor_ids":[901]}',
        )
        assert r.error is None
        assert r.result["status"] == "running"

    asyncio.run(run())
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["expert_type"] == "CombatExpert"
    cfg = kernel.started_jobs[0]["config"]
    assert isinstance(cfg, CombatJobConfig)
    assert cfg.target_actor_id == 201
    assert cfg.target_position == (600, 700)
    assert cfg.actor_ids == [901]
    print("  PASS: attack_actor_handler_creates_precise_combat_job")


def test_occupy_target_handler_creates_precise_occupy_job() -> None:
    """occupy_target should create an OccupyExpert job locked to a specific target actor."""
    kernel = MockKernel()
    kernel._active_actor_ids = [701]
    wm = MockWorldModel()
    handlers = TaskToolHandlers(
        task=Task(task_id="t1", raw_text="capture", kind=TaskKind.MANAGED, priority=50),
        kernel=kernel,
        world_model=wm,
    )
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "occupy_target", '{"target_actor_id":201}')
        assert r.error is None
        assert r.result["status"] == "running"

    asyncio.run(run())
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["expert_type"] == "OccupyExpert"
    cfg = kernel.started_jobs[0]["config"]
    assert isinstance(cfg, OccupyJobConfig)
    assert cfg.target_actor_id == 201
    assert cfg.actor_ids == [701]
    print("  PASS: occupy_target_handler_creates_precise_occupy_job")


def test_query_planner_handler_routes_to_production_advisor() -> None:
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute(
            "tc1",
            "query_planner",
            '{"planner_type":"ProductionAdvisor","params":{"intent":"attack"}}',
        )
        assert r.error is None
        proposal = r.result["proposal"]
        assert proposal["status"] == "ok"
        assert proposal["planner_type"] == "ProductionAdvisor"
        assert proposal["recommendation"]["action"] == "build_opening"
        assert proposal["recommendation"]["reason"] == "empty_base_power_first"
        assert "timestamp" in r.result

    asyncio.run(run())
    queried = [item["query_type"] for item in wm.queries]
    assert queried == ["world_summary", "economy", "production_queues", "my_actors", "enemy_actors"]
    print("  PASS: query_planner_handler_routes_to_production_advisor")


def test_cancel_tasks_handler():
    """cancel_tasks handler calls Kernel.cancel_tasks."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "cancel_tasks", '{"filters":{"kind":"managed"}}')
        assert r.error is None
        assert r.result["count"] == 1

    asyncio.run(run())
    assert kernel.cancelled_filters[0] == {"kind": "managed"}
    print("  PASS: cancel_tasks_handler")


def test_all_responses_have_timestamp():
    """Every handler response includes a timestamp field."""
    kernel = MockKernel()
    kernel._active_actor_ids = [301]
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        calls = [
            ("start_job", '{"expert_type":"ReconExpert","config":{"search_region":"full_map","target_type":"base","target_owner":"enemy"}}'),
            ("patch_job", '{"job_id":"j1","params":{}}'),
            ("pause_job", '{"job_id":"j1"}'),
            ("resume_job", '{"job_id":"j1"}'),
            ("abort_job", '{"job_id":"j1"}'),
            ("complete_task", '{"result":"succeeded","summary":"done"}'),
            ("create_constraint", '{"kind":"do_not_chase","scope":"global","params":{},"enforcement":"clamp"}'),
            ("remove_constraint", '{"constraint_id":"c1"}'),
            ("query_world", '{"query_type":"my_actors"}'),
            ("query_planner", '{"planner_type":"ReconRoutePlanner"}'),
            ("cancel_tasks", '{"filters":{}}'),
            ("stop_units", '{}'),
        ]
        for name, args in calls:
            r = await executor.execute(f"tc_{name}", name, args)
            assert "timestamp" in r.result, f"{name} response missing timestamp"

    asyncio.run(run())
    print("  PASS: all_responses_have_timestamp")


def test_constraint_handlers_side_effects():
    """create/remove_constraint handlers actually update WorldModel state."""
    kernel = MockKernel()
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        # Create constraint
        r = await executor.execute(
            "tc1", "create_constraint",
            '{"kind":"do_not_chase","scope":"global","params":{"max_distance":20},"enforcement":"clamp"}',
        )
        assert r.error is None
        constraint_id = r.result["constraint_id"]
        assert constraint_id.startswith("c_")

        # Verify constraint stored in WorldModel
        assert constraint_id in wm.constraints
        c = wm.constraints[constraint_id]
        assert c.kind == "do_not_chase"
        assert c.scope == "global"
        assert c.params["max_distance"] == 20
        assert c.enforcement.value == "clamp"

        # Remove constraint
        r = await executor.execute(
            "tc2", "remove_constraint",
            json.dumps({"constraint_id": constraint_id}),
        )
        assert r.error is None
        assert r.result["ok"] is True

        # Verify constraint removed from WorldModel
        assert constraint_id not in wm.constraints

    asyncio.run(run())
    print("  PASS: constraint_handlers_side_effects")


def test_move_handler_reuses_active_actor_group_when_safe():
    kernel = MockKernel()
    kernel._active_actor_ids = [101, 102]
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "move_units", '{"target_position":[10,20],"move_mode":"move"}')
        assert r.error is None

    asyncio.run(run())
    cfg = kernel.started_jobs[-1]["config"]
    assert cfg.actor_ids == [101, 102]
    print("  PASS: move_handler_reuses_active_actor_group_when_safe")


def test_stop_handler_reuses_active_actor_group_when_safe():
    kernel = MockKernel()
    kernel._active_actor_ids = [301, 302]
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "stop_units", "{}")
        assert r.error is None

    asyncio.run(run())
    cfg = kernel.started_jobs[-1]["config"]
    assert isinstance(cfg, StopJobConfig)
    assert cfg.actor_ids == [301, 302]
    print("  PASS: stop_handler_reuses_active_actor_group_when_safe")


def test_attack_handler_does_not_autofill_when_actor_job_running():
    kernel = MockKernel()
    kernel._active_actor_ids = [201, 202]
    kernel._has_running_actor_job = True
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute("tc1", "attack", '{"target_position":[30,40],"engagement_mode":"assault"}')
        assert r.error is not None
        assert "explicit actor_ids" in r.error

    asyncio.run(run())
    assert kernel.started_jobs == []
    print("  PASS: attack_handler_requires_explicit_actor_ids_when_actor_job_running")


def test_attack_handler_allows_explicit_actor_ids_when_actor_job_running() -> None:
    kernel = MockKernel()
    kernel._active_actor_ids = [201, 202]
    kernel._has_running_actor_job = True
    wm = MockWorldModel()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    executor = ToolExecutor()
    handlers.register_all(executor)

    async def run():
        r = await executor.execute(
            "tc1",
            "attack",
            '{"target_position":[30,40],"engagement_mode":"assault","actor_ids":[201,202]}',
        )
        assert r.error is None

    asyncio.run(run())
    cfg = kernel.started_jobs[-1]["config"]
    assert cfg.actor_ids == [201, 202]
    print("  PASS: attack_handler_allows_explicit_actor_ids_when_actor_job_running")


def test_end_to_end_agent_with_handlers():
    """Full e2e: TaskAgent receives signal → LLM calls tools → handlers execute on Kernel."""
    kernel = MockKernel()
    wm = MockWorldModel()

    task = Task(task_id="t1", raw_text="侦察东北方向", kind=TaskKind.MANAGED, priority=50)

    # Set up handlers + executor
    executor = ToolExecutor()
    handlers = TaskToolHandlers(task=Task(task_id="t1", raw_text="test", kind=TaskKind.MANAGED, priority=50), kernel=kernel, world_model=wm)
    handlers.register_all(executor)

    # Mock LLM: query_world → start_job → text
    mock_llm = MockProvider(responses=[
        # Init wake: query then start job
        LLMResponse(
            tool_calls=[ToolCall(id="tc1", name="query_world", arguments='{"query_type":"my_actors"}')],
            model="mock",
        ),
        LLMResponse(
            tool_calls=[ToolCall(id="tc2", name="start_job", arguments='{"expert_type":"ReconExpert","config":{"search_region":"northeast","target_type":"base","target_owner":"enemy"}}')],
            model="mock",
        ),
        LLMResponse(text="Job started, monitoring.", model="mock"),
        # Signal wake: complete the task
        LLMResponse(
            tool_calls=[ToolCall(id="tc3", name="complete_task", arguments='{"result":"succeeded","summary":"Found enemy base"}')],
            model="mock",
        ),
    ])

    agent = TaskAgent(
        task=task,
        llm=mock_llm,
        tool_executor=executor,
        jobs_provider=lambda tid: [],
        world_summary_provider=lambda: WorldSummary(),
        config=AgentConfig(review_interval=60.0),
    )

    async def run():
        agent_task = asyncio.create_task(agent.run())
        await asyncio.sleep(0.1)

        # Push signal to trigger second wake
        agent.push_signal(ExpertSignal(
            task_id="t1", job_id="j_1", kind=SignalKind.TARGET_FOUND,
            summary="Enemy base found",
        ))

        await asyncio.wait_for(agent_task, timeout=5.0)

    asyncio.run(run())

    # Verify real side effects on Kernel
    assert len(wm.queries) >= 1  # query_world was called
    assert len(kernel.started_jobs) == 1  # start_job was called
    assert kernel.started_jobs[0]["expert_type"] == "ReconExpert"
    assert len(kernel.completed_tasks) == 1  # complete_task was called
    assert kernel.completed_tasks[0]["result"] == "succeeded"
    assert agent._task_completed is True
    print("  PASS: end_to_end_agent_with_handlers")


# --- Run all tests ---

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
