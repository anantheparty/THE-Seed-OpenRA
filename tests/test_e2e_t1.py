"""End-to-end milestone test T1: find the enemy base."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark import clear as benchmark_clear
from benchmark import export_json as benchmark_export_json
from benchmark import query as benchmark_query
from experts import ReconExpert
from game_loop import GameLoop, GameLoopConfig
from kernel import Kernel, KernelConfig
from llm import LLMProvider, LLMResponse, ToolCall
from models import EventType, TaskKind
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from task_agent import AgentConfig
from world_model import RefreshPolicy, WorldModel


class ScenarioProvider(LLMProvider):
    """Context-aware mock provider for the T1 scenario."""

    def __init__(self) -> None:
        self.call_log: list[dict[str, Any]] = []
        self._started_job = False

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.call_log.append({"messages": messages, "tools": tools})
        context = self._extract_context(messages)
        jobs = context["context_packet"]["jobs"]
        recent_signals = context["context_packet"]["recent_signals"]

        if not self._started_job:
            self._started_job = True
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tc_start_recon",
                        name="start_job",
                        arguments=json.dumps(
                            {
                                "expert_type": "ReconExpert",
                                "config": {
                                    "search_region": "enemy_half",
                                    "target_type": "base",
                                    "target_owner": "enemy",
                                    "retreat_hp_pct": 0.3,
                                    "avoid_combat": True,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                model="mock-scenario",
            )

        if any(signal.get("kind") == "task_complete" for signal in recent_signals):
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tc_complete_task",
                        name="complete_task",
                        arguments=json.dumps(
                            {
                                "result": "succeeded",
                                "summary": "找到敌人基地并完成侦察",
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                model="mock-scenario",
            )

        return LLMResponse(text="Recon job active, continue monitoring.", model="mock-scenario")

    @staticmethod
    def _extract_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for msg in reversed(messages):
            content = msg.get("content")
            if msg.get("role") == "user" and isinstance(content, str) and content.startswith("[CONTEXT UPDATE]"):
                return json.loads(content.split("\n", 1)[1])
        raise AssertionError("No context packet found in ScenarioProvider call")


class MockGameAPI:
    def __init__(self) -> None:
        self.moves: list[dict[str, Any]] = []

    def move_units_by_location(self, actors: list[Actor], location: Location, attack_move: bool = False) -> None:
        self.moves.append(
            {
                "actor_ids": [actor.actor_id for actor in actors],
                "location": (location.x, location.y),
                "attack_move": attack_move,
            }
        )


class ScenarioWorldSource:
    """Timeline source: no enemy -> harvester clue -> base discovered."""

    def __init__(self) -> None:
        self.refresh_count = 0

    def fetch_self_actors(self) -> list[Actor]:
        self.refresh_count += 1
        return [
            Actor(
                actor_id=57,
                type="jeep",
                faction="自己",
                position=Location(120, 820),
                hppercent=100,
                activity="Idle",
                order="Stop",
            ),
            Actor(
                actor_id=11,
                type="proc",
                faction="自己",
                position=Location(220, 780),
                hppercent=100,
                activity="Idle",
                order="Stop",
            ),
        ]

    def fetch_enemy_actors(self) -> list[Actor]:
        if self.refresh_count >= 24:
            return [
                Actor(
                    actor_id=201,
                    type="harv",
                    faction="敌人",
                    position=Location(1800, 420),
                    hppercent=100,
                    activity="Harvest",
                    order="Harvest",
                ),
                Actor(
                    actor_id=301,
                    type="proc",
                    faction="敌人",
                    position=Location(1820, 430),
                    hppercent=100,
                    activity="Idle",
                    order="Stop",
                ),
            ]
        if self.refresh_count >= 4:
            return [
                Actor(
                    actor_id=201,
                    type="harv",
                    faction="敌人",
                    position=Location(1800, 420),
                    hppercent=100,
                    activity="Harvest",
                    order="Harvest",
                )
            ]
        return []

    def fetch_economy(self) -> PlayerBaseInfo:
        return PlayerBaseInfo(Cash=2000, Resources=1500, Power=100, PowerDrained=20, PowerProvided=120)

    def fetch_map(self) -> MapQueryResult:
        explored = [[False for _ in range(8)] for _ in range(8)]
        visible = [[False for _ in range(8)] for _ in range(8)]
        for x in range(2):
            for y in range(8):
                explored[x][y] = True
                visible[x][y] = True
        return MapQueryResult(
            MapWidth=2000,
            MapHeight=1000,
            Height=[[0 for _ in range(8)] for _ in range(8)],
            IsVisible=visible,
            IsExplored=explored,
            Terrain=[["clear" for _ in range(8)] for _ in range(8)],
            ResourcesType=[["none" for _ in range(8)] for _ in range(8)],
            Resources=[[0 for _ in range(8)] for _ in range(8)],
        )

    def fetch_production_queues(self) -> dict[str, dict[str, Any]]:
        return {}


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition")


def test_e2e_t1_recon_flow_and_benchmark() -> None:
    benchmark_clear()

    async def run() -> None:
        source = ScenarioWorldSource()
        world = WorldModel(
            source,
            refresh_policy=RefreshPolicy(actors_s=0.05, economy_s=0.05, map_s=0.05),
        )
        world.refresh(force=True)

        provider = ScenarioProvider()
        game_api = MockGameAPI()
        kernel = Kernel(
            world_model=world,
            llm=provider,
            expert_registry={"ReconExpert": ReconExpert(game_api=game_api, world_model=world)},
            config=KernelConfig(
                default_agent_config=AgentConfig(
                    review_interval=10.0,
                    max_retries=0,
                    llm_timeout=1.0,
                    max_turns=4,
                )
            ),
        )

        task = kernel.create_task("探索地图，找到敌人基地", TaskKind.MANAGED, 50)
        await _wait_until(lambda: len(kernel.list_jobs()) == 1, timeout=1.0)

        game_loop = GameLoop(world, kernel, config=GameLoopConfig(tick_hz=20.0))
        runtime = kernel._task_runtimes[task.task_id]  # type: ignore[attr-defined]
        game_loop.register_agent(task.task_id, runtime.agent.queue, review_interval=10.0)
        for controller in kernel._jobs.values():  # type: ignore[attr-defined]
            game_loop.register_job(controller)

        loop_task = asyncio.create_task(game_loop.start())
        try:
            await _wait_until(
                lambda: kernel.tasks[task.task_id].status.value == "succeeded",
                timeout=4.0,
            )
        finally:
            game_loop.stop()
            await asyncio.wait_for(loop_task, timeout=2.0)

        # Full-chain state assertions
        assert len(provider.call_log) >= 3
        jobs = kernel.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].expert_type == "ReconExpert"
        assert jobs[0].resources == ["actor:57"]
        assert kernel.tasks[task.task_id].status.value == "succeeded"
        assert len(game_api.moves) >= 2

        event_payload = world.query("events")
        event_types = [item["type"] for item in event_payload["events"]]
        assert EventType.ENEMY_DISCOVERED.value in event_types

        # Benchmark visibility
        world_refresh_records = benchmark_query(tag="world_refresh")
        llm_records = benchmark_query(tag="llm_call")
        tool_records = benchmark_query(tag="tool_exec")
        job_records = benchmark_query(tag="job_tick")

        assert world_refresh_records, "expected world_refresh benchmark records"
        assert llm_records, "expected llm_call benchmark records"
        assert tool_records, "expected tool_exec benchmark records"
        assert job_records, "expected job_tick benchmark records"
        assert any(record.name.startswith("tool:start_job") for record in tool_records)
        assert any(record.name.startswith("tool:complete_task") for record in tool_records)
        assert any(record.name.startswith("ReconExpert:") for record in job_records)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "t1_benchmark.json")
            exported = benchmark_export_json(output_path)
            payload = json.loads(exported)
            assert payload
            tags = {item["tag"] for item in payload}
            assert {"world_refresh", "llm_call", "tool_exec", "job_tick"}.issubset(tags)
            with open(output_path, "r", encoding="utf-8") as handle:
                written = json.load(handle)
            assert written == payload

    asyncio.run(run())
    print("  PASS: e2e_t1_recon_flow_and_benchmark")


if __name__ == "__main__":
    print("Running E2E T1 test...\n")
    test_e2e_t1_recon_flow_and_benchmark()
    print("\nAll 1 E2E T1 tests passed!")
