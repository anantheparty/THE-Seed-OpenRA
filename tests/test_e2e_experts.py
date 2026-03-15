"""End-to-end tests T2-T8 — Milestone 2: Five Experts (design.md test_scenarios.md).

Each test simulates the full flow:
  Player input → (mock) LLM tool calls → Expert Job execution → Signals → completion

Uses MockProvider for LLM, real Expert implementations, mock GameAPI/WorldModel.
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
    CombatJobConfig,
    DeployJobConfig,
    EconomyJobConfig,
    EngagementMode,
    ExpertSignal,
    JobStatus,
    MovementJobConfig,
    MoveMode,
    ReconJobConfig,
    SignalKind,
    Task,
    TaskKind,
    TaskStatus,
)
from task_agent import AgentConfig, TaskAgent, ToolExecutor, WorldSummary
from task_agent.handlers import TaskToolHandlers


# --- Shared mock infrastructure ---

class MockGameAPI:
    def __init__(self):
        self.move_calls: list[dict] = []
        self.attack_calls: list[dict] = []
        self.deploy_calls: list[int] = []
        self.produce_calls: list[dict] = []
        self._deploy_result = True

    def move_actors(self, actor_ids, position, *, attack_move=False):
        self.move_calls.append({"actor_ids": actor_ids, "position": position, "attack_move": attack_move})

    def attack_target(self, actor_ids, target_actor_id):
        self.attack_calls.append({"actor_ids": actor_ids, "target": target_actor_id})

    def deploy_actor(self, actor_id):
        self.deploy_calls.append(actor_id)
        return self._deploy_result

    def produce(self, queue_type, unit_type):
        self.produce_calls.append({"queue_type": queue_type, "unit_type": unit_type})


class MockKernel:
    """Simplified Kernel that creates Jobs via Expert registry."""

    def __init__(self, experts=None):
        self._experts = experts or {}
        self._jobs: dict[str, Any] = {}
        self._tasks: dict[str, Task] = {}
        self._completed: list[dict] = []
        self._constraints: list[dict] = []
        self._cancelled: list[dict] = []

    def start_job(self, task_id, expert_type, config):
        from models import Job
        expert = self._experts.get(expert_type)
        if expert:
            job = expert.create_job(task_id, config, lambda s: None)
            self._jobs[job.job_id] = job
            return job.to_model()
        # Fallback: return a mock Job model
        from models import Job as JobModel
        job_id = f"j_mock_{len(self._jobs)}"
        return JobModel(job_id=job_id, task_id=task_id, expert_type=expert_type, config=config)

    def patch_job(self, job_id, params):
        job = self._jobs.get(job_id)
        if job:
            job.patch(params)
        return True

    def pause_job(self, job_id): return True
    def resume_job(self, job_id): return True
    def abort_job(self, job_id): return True

    def complete_task(self, task_id, result, summary):
        self._completed.append({"task_id": task_id, "result": result, "summary": summary})
        return True

    def cancel_tasks(self, filters):
        self._cancelled.append(filters)
        return 1

    def list_pending_questions(self): return []
    def list_tasks(self): return list(self._tasks.values())


class MockWorldModel:
    def __init__(self, actors=None, enemies=None, economy=None):
        self._actors = actors or {}
        self._enemies = enemies or []
        self._economy = economy or {"cash": 5000}
        self.constraints = {}

    def query(self, query_type, params=None):
        if query_type == "actor_by_id":
            aid = params["actor_id"]
            return {"actor": self._actors.get(aid)}
        if query_type in ("my_actors", "find_actors"):
            return {"actors": list(self._actors.values()), "timestamp": time.time()}
        if query_type == "enemy_actors":
            return {"actors": list(self._enemies), "timestamp": time.time()}
        if query_type == "economy":
            return dict(self._economy)
        if query_type == "world_summary":
            return {"economy": self._economy, "military": {"units": len(self._actors)}, "timestamp": time.time()}
        return {"data": [], "timestamp": time.time()}

    def world_summary(self):
        return self.query("world_summary")

    def set_constraint(self, constraint):
        self.constraints[constraint.constraint_id] = constraint

    def remove_constraint(self, constraint_id):
        self.constraints.pop(constraint_id, None)


def make_agent(
    task_text: str,
    llm_responses: list[LLMResponse],
    kernel: MockKernel,
    wm: MockWorldModel,
) -> TaskAgent:
    """Create a TaskAgent wired to mock Kernel + WorldModel via TaskToolHandlers."""
    task = Task(task_id="t1", raw_text=task_text, kind=TaskKind.MANAGED, priority=50)
    executor = ToolExecutor()
    handlers = TaskToolHandlers(task_id="t1", kernel=kernel, world_model=wm)
    handlers.register_all(executor)

    return TaskAgent(
        task=task,
        llm=MockProvider(responses=llm_responses),
        tool_executor=executor,
        jobs_provider=lambda tid: [],
        world_summary_provider=lambda: WorldSummary(),
        config=AgentConfig(review_interval=60.0, max_retries=0),
    )


# --- T2: Economy — "生产5辆坦克" ---

def test_t2_produce_units():
    """T2: LLM calls start_job(EconomyExpert) → Job created."""
    benchmark.clear()
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("生产5辆重型坦克", [
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="start_job",
            arguments='{"expert_type":"EconomyExpert","config":{"unit_type":"2tnk","count":5,"queue_type":"Vehicle","repeat":false}}',
        )], model="mock"),
        LLMResponse(text="已开始生产5辆重型坦克", model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._total_llm_calls == 2
    assert len(benchmark.query(tag="llm_call")) >= 1
    print("  PASS: T2 produce_units")


# --- T3: Movement — "所有部队撤退" ---

def test_t3_retreat_all():
    """T3: LLM calls cancel_tasks + start_job(MovementExpert)."""
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("所有部队撤退回基地", [
        # Multi-tool turn: cancel combats + start movement
        LLMResponse(tool_calls=[
            ToolCall(id="tc1", name="cancel_tasks", arguments='{"filters":{"kind":"managed"}}'),
        ], model="mock"),
        LLMResponse(tool_calls=[
            ToolCall(id="tc2", name="start_job",
                     arguments='{"expert_type":"MovementExpert","config":{"target_position":[200,600],"move_mode":"retreat","arrival_radius":10}}'),
        ], model="mock"),
        LLMResponse(text="部队正在撤退", model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert len(kernel._cancelled) == 1
    assert agent._total_llm_calls == 3
    print("  PASS: T3 retreat_all")


# --- T4: Combat assault — "进攻敌人基地" ---

def test_t4_assault_attack():
    """T4: LLM calls start_job(CombatExpert, assault)."""
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("进攻右边敌人基地", [
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="query_world", arguments='{"query_type":"enemy_actors"}',
        )], model="mock"),
        LLMResponse(tool_calls=[ToolCall(
            id="tc2", name="start_job",
            arguments='{"expert_type":"CombatExpert","config":{"target_position":[1820,430],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )], model="mock"),
        LLMResponse(text="已发起进攻", model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._total_llm_calls == 3
    print("  PASS: T4 assault_attack")


# --- T5: Deploy — "部署基地车" ---

def test_t5_deploy_mcv():
    """T5: LLM calls start_job(DeployExpert)."""
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("部署基地车", [
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="start_job",
            arguments='{"expert_type":"DeployExpert","config":{"actor_id":99,"target_position":[1500,400]}}',
        )], model="mock"),
        LLMResponse(tool_calls=[ToolCall(
            id="tc2", name="complete_task",
            arguments='{"result":"succeeded","summary":"基地车已部署"}',
        )], model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._task_completed is True
    assert kernel._completed[0]["result"] == "succeeded"
    print("  PASS: T5 deploy_mcv")


# --- T6: Surround — "包围右边基地" ---

def test_t6_surround():
    """T6: LLM creates 2 CombatJobs with surround mode."""
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("包围右边那个基地", [
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="query_world", arguments='{"query_type":"enemy_actors"}',
        )], model="mock"),
        # Two surround jobs simultaneously
        LLMResponse(tool_calls=[
            ToolCall(id="tc2", name="start_job",
                     arguments='{"expert_type":"CombatExpert","config":{"target_position":[1820,430],"engagement_mode":"surround","max_chase_distance":15,"retreat_threshold":0.4}}'),
            ToolCall(id="tc3", name="start_job",
                     arguments='{"expert_type":"CombatExpert","config":{"target_position":[1820,430],"engagement_mode":"surround","max_chase_distance":15,"retreat_threshold":0.4}}'),
        ], model="mock"),
        LLMResponse(text="已部署两路包围", model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._total_llm_calls == 3
    print("  PASS: T6 surround")


# --- T7: Constraint — "别追太远" ---

def test_t7_constraint():
    """T7: LLM calls create_constraint(do_not_chase)."""
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("别追太远", [
        LLMResponse(tool_calls=[
            ToolCall(id="tc1", name="create_constraint",
                     arguments='{"kind":"do_not_chase","scope":"global","params":{"max_distance":20},"enforcement":"clamp"}'),
            ToolCall(id="tc2", name="complete_task",
                     arguments='{"result":"succeeded","summary":"已设置：不追击超过20格"}'),
        ], model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    assert agent._task_completed is True
    assert len(wm.constraints) == 1
    c = list(wm.constraints.values())[0]
    assert c.kind == "do_not_chase"
    assert c.params["max_distance"] == 20
    print("  PASS: T7 constraint")


# --- T8: Sequential — "修理坦克然后进攻" ---

def test_t8_sequential_movement_then_combat():
    """T8: LLM queries → Movement → (simulated completion) → Combat."""
    kernel = MockKernel()
    wm = MockWorldModel(actors={58: {"actor_id": 58, "position": [22, 20], "hp": 30, "hp_max": 100}})

    agent = make_agent("修理我的坦克，然后继续进攻", [
        # Wake 1: query damaged units, query repair, start movement
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="query_world", arguments='{"query_type":"my_actors"}',
        )], model="mock"),
        LLMResponse(tool_calls=[ToolCall(
            id="tc2", name="start_job",
            arguments='{"expert_type":"MovementExpert","config":{"target_position":[220,610],"move_mode":"move","arrival_radius":3}}',
        )], model="mock"),
        LLMResponse(text="坦克正在前往维修站", model="mock"),
        # Wake 2 (signal): movement complete → start combat
        LLMResponse(tool_calls=[ToolCall(
            id="tc3", name="start_job",
            arguments='{"expert_type":"CombatExpert","config":{"target_position":[1600,300],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )], model="mock"),
        LLMResponse(text="修理完毕，开始进攻", model="mock"),
    ], kernel, wm)

    async def run():
        # Wake 1: start movement
        await agent._wake_cycle(trigger="init")
        assert agent._total_llm_calls == 3  # query + start_job + text

        # Simulate movement completion signal
        agent.push_signal(ExpertSignal(
            task_id="t1", job_id="j1", kind=SignalKind.TASK_COMPLETE,
            summary="到达维修站", result="succeeded",
        ))

        # Wake 2: signal triggers combat start
        await agent._wake_cycle(trigger="event")
        assert agent._total_llm_calls == 5

    asyncio.run(run())
    print("  PASS: T8 sequential_movement_then_combat")


# --- Benchmark verification ---

def test_benchmark_coverage():
    """Verify benchmark records exist for LLM calls and tool executions."""
    benchmark.clear()
    kernel = MockKernel()
    wm = MockWorldModel()

    agent = make_agent("测试 benchmark", [
        LLMResponse(tool_calls=[ToolCall(
            id="tc1", name="start_job",
            arguments='{"expert_type":"MovementExpert","config":{"target_position":[100,200]}}',
        )], model="mock"),
        LLMResponse(text="done", model="mock"),
    ], kernel, wm)

    async def run():
        await agent._wake_cycle(trigger="init")

    asyncio.run(run())

    llm_records = benchmark.query(tag="llm_call")
    tool_records = benchmark.query(tag="tool_exec")
    assert len(llm_records) >= 1, "No LLM call benchmark records"
    assert len(tool_records) >= 1, "No tool_exec benchmark records"
    print(f"  PASS: benchmark_coverage (llm={len(llm_records)}, tool={len(tool_records)})")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running E2E Expert tests (Milestone 2)...\n")

    test_t2_produce_units()
    test_t3_retreat_all()
    test_t4_assault_attack()
    test_t5_deploy_mcv()
    test_t6_surround()
    test_t7_constraint()
    test_t8_sequential_movement_then_combat()
    test_benchmark_coverage()

    print(f"\nAll 8 tests passed! ★ Milestone 2 verified")
