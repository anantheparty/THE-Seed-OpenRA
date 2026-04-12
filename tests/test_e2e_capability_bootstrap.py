"""Focused E2E smoke for ordinary-task request -> capability bootstrap -> delivery."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from llm import MockProvider
from main import ApplicationRuntime, RuntimeConfig
from models import TaskKind
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from task_agent import AgentConfig
from kernel import KernelConfig

pytestmark = pytest.mark.mock_integration


class CapabilityBootstrapGameAPI:
    def __init__(self) -> None:
        self.produce_requests = 0
        self.last_produce: dict[str, Any] | None = None

    def can_produce(self, unit_type: str) -> bool:
        return unit_type == "3tnk"

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> int:
        self.produce_requests += quantity
        self.last_produce = {
            "unit_type": unit_type,
            "quantity": quantity,
            "auto_place_building": auto_place_building,
        }
        return self.produce_requests

    def place_building(self, queue_type: str, location: Any = None) -> None:
        raise AssertionError(f"place_building should not be called in vehicle bootstrap smoke: {queue_type=} {location=}")

    def manage_production(
        self,
        queue_type: str,
        action: str,
        *,
        owner_actor_id: int | None = None,
        item_name: str | None = None,
        count: int = 1,
    ) -> None:
        raise AssertionError(
            f"manage_production should not be called in vehicle bootstrap smoke: "
            f"{queue_type=} {action=} {owner_actor_id=} {item_name=} {count=}"
        )


class CapabilityBootstrapWorldSource:
    def __init__(self, game_api: CapabilityBootstrapGameAPI) -> None:
        self.game_api = game_api
        self.queue_refreshes = 0

    def fetch_self_actors(self) -> list[Actor]:
        actors = [
            Actor(actor_id=1, type="建造厂", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="发电厂", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="矿场", faction="自己", position=Location(22, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=4, type="战车工厂", faction="自己", position=Location(28, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=5, type="维修厂", faction="自己", position=Location(34, 10), hppercent=100, activity="Idle"),
        ]
        if self.game_api.produce_requests and self.queue_refreshes >= 3:
            actors.append(
                Actor(actor_id=20, type="重坦", faction="自己", position=Location(32, 12), hppercent=100, activity="Idle")
            )
        return actors

    def fetch_enemy_actors(self) -> list[Actor]:
        return []

    def fetch_frozen_enemies(self) -> list[Actor]:
        return []

    def fetch_economy(self) -> PlayerBaseInfo:
        return PlayerBaseInfo(Cash=5000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100)

    def fetch_map(self, fields=None) -> MapQueryResult:
        size = 4
        return MapQueryResult(
            MapWidth=size,
            MapHeight=size,
            Height=[[0] * size for _ in range(size)],
            IsVisible=[[True] * size for _ in range(size)],
            IsExplored=[[True] * size for _ in range(size)],
            Terrain=[["clear"] * size for _ in range(size)],
            ResourcesType=[["ore"] * size for _ in range(size)],
            Resources=[[0] * size for _ in range(size)],
        )

    def fetch_production_queues(self) -> dict[str, dict]:
        self.queue_refreshes += 1
        items: list[dict[str, Any]] = []
        if self.game_api.produce_requests:
            done = self.queue_refreshes >= 3
            items = [
                {
                    "queue_type": "Vehicle",
                    "name": "3tnk",
                    "display_name": "重型坦克",
                    "progress": 100 if done else 50,
                    "status": "Done" if done else "Building",
                    "paused": False,
                    "owner_actor_id": 4,
                    "remaining_time": 0 if done else 1,
                    "total_time": 1,
                    "done": done,
                }
            ]
        return {"Vehicle": {"queue_type": "Vehicle", "items": items, "has_ready_item": False}}


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition")


async def _tick_until(runtime: ApplicationRuntime, predicate, *, max_ticks: int = 40, step_s: float = 0.25) -> None:
    now = time.time()
    for _ in range(max_ticks):
        now += step_s
        await asyncio.to_thread(runtime.world_model.refresh, now=now, force=True)
        events = runtime.world_model.detect_events(clear=True)
        if events:
            runtime.kernel.route_events(events)
        runtime.kernel.tick(now=now)
        await runtime.game_loop._tick_jobs(now)
        runtime.bridge.sync_runtime()
        if predicate():
            return
    raise AssertionError("Timed out waiting for predicate across manual game-loop ticks")


def _runtime_config(root: str) -> RuntimeConfig:
    return RuntimeConfig(
        tick_hz=30.0,
        review_interval=10.0,
        enable_ws=False,
        verify_game_api=False,
        llm_provider="mock",
        llm_model="mock",
        benchmark_records_path=os.path.join(root, "capability_bootstrap_records.json"),
        benchmark_summary_path=os.path.join(root, "capability_bootstrap_summary.json"),
        log_export_path=os.path.join(root, "capability_bootstrap_logs.json"),
    )


@pytest.mark.mock_integration
def test_capability_bootstrap_request_smoke() -> None:
    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            game_api = CapabilityBootstrapGameAPI()
            runtime = ApplicationRuntime(
                config=_runtime_config(tmpdir),
                task_llm=MockProvider([]),
                adjutant_llm=MockProvider([]),
                api=game_api,
                world_source=CapabilityBootstrapWorldSource(game_api),
                kernel_config=KernelConfig(
                    auto_start_agents=False,
                    default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
                ),
            )
            try:
                await asyncio.to_thread(runtime.world_model.refresh, force=True)
                runtime.bridge.sync_runtime()
                assert runtime.kernel.capability_task_id is None
                assert not any(task.is_capability for task in runtime.kernel.list_tasks())
                task = runtime.kernel.create_task("前线补一辆重坦", TaskKind.MANAGED, 60)
                agent = runtime.kernel.get_task_agent(task.task_id)
                assert agent is not None

                result = await agent.tool_executor.execute(
                    "tc_cap_bootstrap",
                    "request_units",
                    '{"category":"vehicle","count":1,"urgency":"high","hint":"重坦"}',
                )
                assert result.error is None
                payload = result.result
                assert payload["status"] == "waiting"
                assert payload["unit_type"] == "3tnk"
                assert payload["queue_type"] == "Vehicle"
                assert payload["bootstrap_job_id"] is not None
                capability_task_id = runtime.kernel.capability_task_id
                assert capability_task_id is not None
                assert payload["bootstrap_task_id"] == capability_task_id

                capability_task = runtime.kernel.tasks[capability_task_id]
                assert capability_task.is_capability is True
                assert capability_task.raw_text == "EconomyCapability — 持久经济规划"
                assert runtime.kernel.get_task_agent(capability_task_id) is not None

                runtime.kernel._jobs[payload["bootstrap_job_id"]].tick_interval = 0.2  # type: ignore[attr-defined]
                runtime.bridge.sync_runtime()

                await _tick_until(runtime, lambda: game_api.produce_requests == 1, max_ticks=12)
                await _tick_until(
                    runtime,
                    lambda: runtime.kernel._unit_requests[payload["request_id"]].status == "fulfilled",
                    max_ticks=24,
                )

                request = runtime.kernel._unit_requests[payload["request_id"]]
                reservation = runtime.kernel.list_unit_reservations()[0]

                assert game_api.last_produce == {
                    "unit_type": "3tnk",
                    "quantity": 1,
                    "auto_place_building": False,
                }
                assert request.assigned_actor_ids == [20]
                assert request.bootstrap_job_id is None
                assert request.bootstrap_task_id is None
                assert reservation.assigned_actor_ids == [20]
                assert reservation.bootstrap_job_id is None
                assert reservation.bootstrap_task_id is None
                assert runtime.kernel.task_active_actor_ids(task.task_id) == [20]
                runtime_state = runtime.kernel.world_model.query("runtime_state")
                assert runtime_state["active_tasks"][task.task_id]["active_actor_ids"] == [20]
                assert runtime_state["capability_status"]["task_id"] == capability_task_id
            finally:
                api_close = getattr(runtime.api, "close", None)
                if callable(api_close):
                    await asyncio.to_thread(api_close)

    asyncio.run(run())

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
