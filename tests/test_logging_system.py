from __future__ import annotations

import asyncio
import json
from pathlib import Path
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import logging_system
from kernel import Kernel, KernelConfig
from models import Event, Task, TaskKind
from openra_api.models import MapQueryResult, PlayerBaseInfo
from task_agent import ToolExecutor
from world_model import RefreshPolicy, WorldModel


class DummyAgent:
    def __init__(self, task, tool_executor, jobs_provider, world_summary_provider):
        self.task = task

    async def run(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def push_signal(self, signal) -> None:
        return None

    def push_event(self, event) -> None:
        return None


class SimpleWorldSource:
    def fetch_self_actors(self):
        return []

    def fetch_enemy_actors(self):
        return []

    def fetch_economy(self):
        return PlayerBaseInfo(Cash=1000, Resources=500, Power=100, PowerDrained=30, PowerProvided=130)

    def fetch_map(self):
        size = 2
        return MapQueryResult(
            MapWidth=size,
            MapHeight=size,
            Height=[[0] * size for _ in range(size)],
            IsVisible=[[True] * size for _ in range(size)],
            IsExplored=[[True] * size for _ in range(size)],
            Terrain=[["clear"] * size for _ in range(size)],
            ResourcesType=[["ore"] * size for _ in range(size)],
            Resources=[[10] * size for _ in range(size)],
        )

    def fetch_production_queues(self):
        return {"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}}


def setup_function() -> None:
    benchmark.clear()
    logging_system.clear()


def test_log_query_and_export() -> None:
    logger = logging_system.get_logger("kernel")
    logger.info("Task created", event="task_created", task_id="t_1")
    time.sleep(0.001)
    logger.warn("Task completed with warning", event="task_completed", task_id="t_1")

    records = logging_system.query(component="kernel")
    assert len(records) == 2
    assert records[0].event == "task_created"
    assert records[1].level == "WARN"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "logs.json"
        payload = logging_system.export_json(path, component="kernel")
        written = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.loads(payload)

    assert written == serialized
    assert written[0]["component"] == "kernel"


def test_benchmark_summary_and_logging_integration() -> None:
    with benchmark.span("tool_exec", name="fast"):
        time.sleep(0.001)
    with benchmark.span("tool_exec", name="slow"):
        time.sleep(0.002)
    with benchmark.span("llm_call", name="chat"):
        time.sleep(0.001)

    summary = logging_system.summarize_benchmarks()
    by_tag = {item["tag"]: item for item in summary}
    assert by_tag["tool_exec"]["count"] == 2
    assert by_tag["tool_exec"]["max_ms"] >= by_tag["tool_exec"]["avg_ms"]
    assert by_tag["llm_call"]["count"] == 1

    bench_logs = logging_system.query(component="benchmark", event="benchmark_recorded")
    assert len(bench_logs) >= 3


def test_runtime_components_emit_structured_logs() -> None:
    world = WorldModel(SimpleWorldSource(), refresh_policy=RefreshPolicy(actors_s=0.01, economy_s=0.01, map_s=0.01))
    world.refresh(force=True)

    kernel = Kernel(
        world_model=world,
        task_agent_factory=lambda task, te, jp, wsp: DummyAgent(task, te, jp, wsp),
        config=KernelConfig(auto_start_agents=False),
    )
    task = kernel.create_task("侦察地图", TaskKind.MANAGED, 60)
    assert kernel.complete_task(task.task_id, "succeeded", "done")

    world_logs = logging_system.query(component="world_model", event="world_refresh_completed")
    kernel_logs = logging_system.query(component="kernel")

    assert len(world_logs) >= 1
    assert any(record.event == "task_created" for record in kernel_logs)
    assert any(record.event == "task_completed" for record in kernel_logs)


def test_tool_executor_emits_structured_logs() -> None:
    executor = ToolExecutor()

    async def echo(_name: str, args: dict[str, object]) -> dict[str, object]:
        return {"echo": args["value"]}

    executor.register("echo", echo)
    result = asyncio.run(executor.execute("tc_1", "echo", '{"value":"ok"}'))

    assert result.error is None
    logs = logging_system.query(component="task_agent")
    assert any(record.event == "tool_execute" for record in logs)
    assert any(record.event == "tool_execute_completed" for record in logs)


if __name__ == "__main__":
    print("Running structured logging tests...\n")
    test_log_query_and_export()
    print("  PASS: log_query_and_export")
    setup_function()
    test_benchmark_summary_and_logging_integration()
    print("  PASS: benchmark_summary_and_logging_integration")
    setup_function()
    test_runtime_components_emit_structured_logs()
    print("  PASS: runtime_components_emit_structured_logs")
    setup_function()
    test_tool_executor_emits_structured_logs()
    print("  PASS: tool_executor_emits_structured_logs")
    print("\nAll 4 structured logging tests passed!")
