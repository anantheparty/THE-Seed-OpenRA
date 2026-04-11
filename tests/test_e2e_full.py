"""Phase 7 full E2E coverage for T1-T11 on one shared runtime shape."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import pytest

from kernel import KernelConfig
from llm import MockProvider
from main import ApplicationRuntime, RuntimeConfig
from models import TaskKind, TaskMessage, TaskMessageType
from openra_api.models import Actor, Location, PlayerBaseInfo
from task_agent import AgentConfig
from tests.test_e2e_adjutant import (
    DecisionExpert,
    DecisionJobConfig,
    ScenarioAdjutantProvider,
    ScenarioTaskAgentProvider,
    _registered_decision_expert,
)
from tests.test_e2e_experts import MockGameAPI as ExpertMockGameAPI, SimpleWorldSource
from tests.test_e2e_t1 import MockGameAPI as ReconMockGameAPI, ScenarioProvider as ReconScenarioProvider, ScenarioWorldSource
from tests.test_world_model import Frame, MockWorldSource, make_map

pytestmark = pytest.mark.mock_integration


class EconomyMockGameAPI(ExpertMockGameAPI):
    def __init__(self) -> None:
        super().__init__()
        self.produce_requests = 0
        self.last_unit_type = ""

    def can_produce(self, unit_type: str) -> bool:
        return True

    def produce(self, unit_type: str, quantity: int, auto_place_building: bool = True) -> int:
        del auto_place_building
        self.produce_requests += quantity
        self.last_unit_type = unit_type
        return self.produce_requests


class EconomyWorldSource(SimpleWorldSource):
    def __init__(self, game_api: EconomyMockGameAPI) -> None:
        super().__init__(
            self_actors=[
                Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                Actor(actor_id=2, type="weap", faction="自己", position=Location(30, 30), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[],
        )
        self.game_api = game_api
        self.queue_refreshes = 0

    def fetch_economy(self):
        return PlayerBaseInfo(Cash=5000, Resources=500, Power=80, PowerDrained=40, PowerProvided=100)

    def fetch_production_queues(self):
        self.queue_refreshes += 1
        items: list[dict[str, Any]] = []
        if self.game_api.produce_requests:
            done = self.queue_refreshes >= 3
            items = [
                {
                    "queue_type": "Vehicle",
                    "name": self.game_api.last_unit_type,
                    "display_name": self.game_api.last_unit_type,
                    "progress": 100 if done else 50,
                    "status": "Done" if done else "Building",
                    "paused": False,
                    "owner_actor_id": 2,
                    "remaining_time": 0 if done else 1,
                    "total_time": 1,
                    "done": done,
                }
            ]
        return {"Vehicle": {"queue_type": "Vehicle", "items": items, "has_ready_item": False}}


def _runtime_config(root: str, name: str, *, tick_hz: float = 20.0, review_interval: float = 10.0) -> RuntimeConfig:
    return RuntimeConfig(
        tick_hz=tick_hz,
        review_interval=review_interval,
        enable_ws=False,
        verify_game_api=False,
        llm_provider="mock",
        llm_model="mock",
        benchmark_records_path=os.path.join(root, f"{name}_records.json"),
        benchmark_summary_path=os.path.join(root, f"{name}_summary.json"),
        log_export_path=os.path.join(root, f"{name}_logs.json"),
    )


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition")


def _adjutant_world_source() -> MockWorldSource:
    return MockWorldSource(
        [
            Frame(
                self_actors=[
                    Actor(actor_id=1, type="矿车", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                    Actor(actor_id=2, type="吉普车", faction="自己", position=Location(20, 20), hppercent=100, activity="Idle"),
                    Actor(actor_id=3, type="矿场", faction="自己", position=Location(30, 30), hppercent=100, activity="Idle"),
                ],
                enemy_actors=[
                    Actor(actor_id=100, type="矿场", faction="敌人", position=Location(300, 300), hppercent=100, activity="Idle"),
                    Actor(actor_id=101, type="重坦", faction="敌人", position=Location(320, 320), hppercent=100, activity="Idle"),
                ],
                economy=PlayerBaseInfo(Cash=3200, Resources=400, Power=80, PowerDrained=40, PowerProvided=100),
                map_info=make_map(explored=0.55, visible=0.35),
                queues={"Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False}},
            )
        ]
    )


async def _run_t1(tmpdir: str) -> None:
    provider = ReconScenarioProvider()
    game_api = ReconMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t1", tick_hz=20.0),
        task_llm=provider,
        adjutant_llm=provider,
        api=game_api,
        world_source=ScenarioWorldSource(),
        kernel_config=KernelConfig(
            auto_start_agents=True,
            enable_capability_task=False,
            default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
        ),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("探索地图，找到敌人基地", TaskKind.MANAGED, 50)
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: runtime.kernel.tasks[task.task_id].status.value == "succeeded", timeout=6.0)
        jobs = runtime.kernel.list_jobs()
        assert len(provider.call_log) >= 3
        assert len(jobs) == 1
        assert jobs[0].expert_type == "ReconExpert"
        assert len(game_api.moves) >= 2
    finally:
        await runtime.stop()


async def _run_t2(tmpdir: str) -> None:
    game_api = EconomyMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t2", tick_hz=30.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=EconomyWorldSource(game_api),
        kernel_config=KernelConfig(auto_start_agents=False, default_agent_config=AgentConfig(review_interval=10.0)),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("生产1辆重型坦克", TaskKind.MANAGED, 40)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t2",
            "start_job",
            '{"expert_type":"EconomyExpert","config":{"unit_type":"2tnk","count":1,"queue_type":"Vehicle","repeat":false}}',
        )
        assert result.error is None
        runtime.kernel._jobs[result.result["job_id"]].tick_interval = 0.2  # type: ignore[attr-defined]
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: game_api.produce_requests >= 1, timeout=1.0)
        await _wait_until(lambda: runtime.kernel.list_jobs()[0].status.value == "succeeded", timeout=2.0)
        assert runtime.kernel.list_jobs()[0].expert_type == "EconomyExpert"
    finally:
        await runtime.stop()


async def _run_t3(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t3", tick_hz=30.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(
            self_actors=[Actor(actor_id=57, type="重坦", faction="自己", position=Location(500, 500), hppercent=100, activity="Idle")],
            enemy_actors=[],
        ),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("撤退", TaskKind.MANAGED, 70)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t3",
            "start_job",
            '{"expert_type":"MovementExpert","config":{"target_position":[200,600],"move_mode":"retreat","arrival_radius":10}}',
        )
        assert result.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(game_api.move_calls) >= 1, timeout=1.0)
        assert game_api.move_calls[0]["attack_move"] is True
    finally:
        await runtime.stop()


async def _run_t4(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t4", tick_hz=40.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(
            self_actors=[Actor(actor_id=57, type="重坦", faction="自己", position=Location(100, 100), hppercent=100, activity="Idle")],
            enemy_actors=[Actor(actor_id=201, type="重坦", faction="敌人", position=Location(500, 500), hppercent=100, activity="Idle")],
        ),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("进攻", TaskKind.MANAGED, 60)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t4",
            "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[500,500],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )
        assert result.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(game_api.move_calls) >= 1 or len(game_api.attack_calls) >= 1, timeout=1.0)
    finally:
        await runtime.stop()


async def _run_t5(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t5", tick_hz=30.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(
            self_actors=[Actor(actor_id=99, type="mcv", faction="自己", position=Location(500, 400), hppercent=100, activity="Idle")],
            enemy_actors=[],
        ),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("部署基地车", TaskKind.MANAGED, 50)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t5",
            "start_job",
            '{"expert_type":"DeployExpert","config":{"actor_id":99,"target_position":[500,400]}}',
        )
        assert result.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(game_api.deploy_calls) == 1, timeout=1.0)
        assert game_api.deploy_calls[0]["actor_ids"] == [99]
    finally:
        await runtime.stop()


async def _run_t6(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t6", tick_hz=40.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(
            self_actors=[
                Actor(actor_id=57, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
                Actor(actor_id=58, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
                Actor(actor_id=59, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
                Actor(actor_id=60, type="重坦", faction="自己", position=Location(200, 200), hppercent=100, activity="Idle"),
            ],
            enemy_actors=[Actor(actor_id=201, type="矿场", faction="敌人", position=Location(1820, 430), hppercent=100, activity="Idle")],
        ),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("包围基地", TaskKind.MANAGED, 60)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t6",
            "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[1820,430],"engagement_mode":"surround","max_chase_distance":15,"retreat_threshold":0.4}}',
        )
        assert result.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(game_api.move_calls) >= 2, timeout=1.0)
        positions = {call["position"] for call in game_api.move_calls}
        assert len(positions) >= 2
    finally:
        await runtime.stop()


async def _run_t7(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t7", tick_hz=20.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("别追太远", TaskKind.MANAGED, 50)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        result = await agent.tool_executor.execute(
            "tc_t7",
            "create_constraint",
            '{"kind":"do_not_chase","scope":"global","params":{"max_chase_distance":20},"enforcement":"clamp"}',
        )
        assert result.error is None
        constraints = runtime.kernel.world_model.query("constraints")
        assert len(constraints["constraints"]) >= 1
    finally:
        await runtime.stop()


async def _run_t8(tmpdir: str) -> None:
    game_api = ExpertMockGameAPI()
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t8", tick_hz=35.0),
        task_llm=MockProvider([]),
        adjutant_llm=MockProvider([]),
        api=game_api,
        world_source=SimpleWorldSource(
            self_actors=[Actor(actor_id=58, type="重坦", faction="自己", position=Location(22, 20), hppercent=60, activity="Idle")],
            enemy_actors=[Actor(actor_id=201, type="重坦", faction="敌人", position=Location(500, 500), hppercent=100, activity="Idle")],
        ),
        kernel_config=KernelConfig(auto_start_agents=False),
    )
    try:
        await runtime.start()
        task = runtime.kernel.create_task("修理后进攻", TaskKind.MANAGED, 50)
        agent = runtime.kernel.get_task_agent(task.task_id)
        assert agent is not None
        first = await agent.tool_executor.execute(
            "tc_t8_move",
            "start_job",
            '{"expert_type":"MovementExpert","config":{"target_position":[220,610],"move_mode":"move","arrival_radius":3}}',
        )
        assert first.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(game_api.move_calls) >= 1, timeout=1.0)
        second = await agent.tool_executor.execute(
            "tc_t8_combat",
            "start_job",
            '{"expert_type":"CombatExpert","config":{"target_position":[1600,300],"engagement_mode":"assault","max_chase_distance":25,"retreat_threshold":0.3}}',
        )
        assert second.error is None
        runtime.bridge.sync_runtime()
        await _wait_until(lambda: len(runtime.kernel.list_jobs()) == 2, timeout=1.0)
    finally:
        await runtime.stop()


async def _run_t9(tmpdir: str) -> None:
    runtime = ApplicationRuntime(
        config=_runtime_config(tmpdir, "t9", tick_hz=30.0),
        task_llm=ScenarioTaskAgentProvider(),
        adjutant_llm=ScenarioAdjutantProvider(),
        api=ExpertMockGameAPI(),
        world_source=_adjutant_world_source(),
        kernel_config=KernelConfig(auto_start_agents=True, default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4)),
    )
    try:
        await runtime.start()
        existing_task = runtime.kernel.create_task("防守前线", TaskKind.MANAGED, 60)
        runtime.bridge.sync_runtime()
        task_ids_before = {task.task_id for task in runtime.kernel.list_tasks()}
        result = await runtime.adjutant.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is True
        assert "当前现金3200" in result["response_text"]
        assert {task.task_id for task in runtime.kernel.list_tasks()} == task_ids_before
        assert existing_task.task_id in task_ids_before
    finally:
        await runtime.stop()


async def _run_t10(tmpdir: str) -> None:
    with _registered_decision_expert():
        runtime = ApplicationRuntime(
            config=_runtime_config(tmpdir, "t10", tick_hz=40.0),
            task_llm=ScenarioTaskAgentProvider(),
            adjutant_llm=ScenarioAdjutantProvider(),
            api=ExpertMockGameAPI(),
            world_source=_adjutant_world_source(),
            kernel_config=KernelConfig(auto_start_agents=True, default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4)),
        )
        runtime.kernel.expert_registry["DecisionExpert"] = DecisionExpert()
        try:
            await runtime.start()
            task = runtime.kernel.create_task("继续推进前线", TaskKind.MANAGED, 55)
            job = runtime.kernel.start_job(task.task_id, "DecisionExpert", DecisionJobConfig())
            runtime.bridge.sync_runtime()
            runtime.kernel.register_task_message(
                TaskMessage(
                    message_id="msg_t10",
                    task_id=task.task_id,
                    type=TaskMessageType.TASK_QUESTION,
                    content="继续还是放弃？",
                    options=["继续", "放弃"],
                    timeout_s=0.3,
                    default_option="放弃",
                    priority=70,
                    timestamp=time.time(),
                )
            )
            result = await runtime.adjutant.handle_player_input("继续")
            assert result["type"] == "reply"
            assert result["ok"] is True
            await _wait_until(
                lambda: runtime.kernel._jobs[job.job_id].config.decision_response == "继续",  # type: ignore[attr-defined]
                timeout=1.5,
            )
            assert runtime.kernel.list_pending_questions() == []
        finally:
            await runtime.stop()


async def _run_t11(tmpdir: str) -> None:
    with _registered_decision_expert():
        runtime = ApplicationRuntime(
            config=_runtime_config(tmpdir, "t11", tick_hz=50.0),
            task_llm=ScenarioTaskAgentProvider(),
            adjutant_llm=ScenarioAdjutantProvider(),
            api=ExpertMockGameAPI(),
            world_source=_adjutant_world_source(),
            kernel_config=KernelConfig(auto_start_agents=True, enable_capability_task=False, default_agent_config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4)),
        )
        runtime.kernel.expert_registry["DecisionExpert"] = DecisionExpert()
        try:
            await runtime.start()
            task_a = runtime.kernel.create_task("压制右路", TaskKind.MANAGED, 60)
            job_a = runtime.kernel.start_job(task_a.task_id, "DecisionExpert", DecisionJobConfig())
            runtime.bridge.sync_runtime()
            runtime.kernel.register_task_message(
                TaskMessage(
                    message_id="msg_t11",
                    task_id=task_a.task_id,
                    type=TaskMessageType.TASK_QUESTION,
                    content="继续还是放弃？",
                    options=["继续", "放弃"],
                    timeout_s=0.12,
                    default_option="放弃",
                    priority=80,
                    timestamp=time.time(),
                )
            )
            result = await runtime.adjutant.handle_player_input("生产5辆坦克")
            assert result["type"] == "command"
            assert result["ok"] is True
            await _wait_until(
                lambda: runtime.kernel._jobs[job_a.job_id].config.decision_response == "放弃",  # type: ignore[attr-defined]
                timeout=1.5,
            )
            assert any(task.raw_text == "生产5辆坦克" for task in runtime.kernel.list_tasks())
            assert runtime.kernel.list_pending_questions() == []
        finally:
            await runtime.stop()


async def run_full_suite(export_dir: Optional[str] = None) -> dict[str, Any]:
    benchmark.clear()
    with tempfile.TemporaryDirectory() as tmpdir:
        await _run_t1(tmpdir)
        await _run_t2(tmpdir)
        await _run_t3(tmpdir)
        await _run_t4(tmpdir)
        await _run_t5(tmpdir)
        await _run_t6(tmpdir)
        await _run_t7(tmpdir)
        await _run_t8(tmpdir)
        await _run_t9(tmpdir)
        await _run_t10(tmpdir)
        await _run_t11(tmpdir)

        records_json = benchmark.export_json(slowest_first=False)
        records_payload = json.loads(records_json)
        assert records_payload
        tags = {item["tag"] for item in records_payload}
        assert {"llm_call", "tool_exec", "world_refresh", "job_tick"}.issubset(tags)

        export_root = export_dir or tmpdir
        Path(export_root).mkdir(parents=True, exist_ok=True)
        records_path = os.path.join(export_root, "phase7_e2e_benchmark_records.json")
        summary_path = os.path.join(export_root, "phase7_e2e_benchmark_summary.json")
        benchmark.export_json(records_path, slowest_first=False)

        from logging_system import export_benchmark_report_json

        summary_json = export_benchmark_report_json(summary_path)
        summary_payload = json.loads(summary_json)
        assert summary_payload
        summary_tags = {item["tag"] for item in summary_payload}
        assert {"llm_call", "tool_exec", "world_refresh", "job_tick"}.issubset(summary_tags)

        markdown_path = os.path.join(export_root, "phase7_e2e_benchmark_summary.md")
        lines = [
            "# Phase 7 E2E Benchmark Summary",
            "",
            "| Tag | Count | Avg ms | P95 ms | Max ms | Total ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for item in summary_payload:
            lines.append(
                f"| {item['tag']} | {item['count']} | {item['avg_ms']:.2f} | {item['p95_ms']:.2f} | {item['max_ms']:.2f} | {item['total_ms']:.2f} |"
            )
        Path(markdown_path).write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {
            "records_path": records_path,
            "summary_path": summary_path,
            "markdown_path": markdown_path,
            "record_count": len(records_payload),
            "summary": summary_payload,
        }


def test_e2e_full_t1_t11_and_benchmarks() -> None:
    result = asyncio.run(run_full_suite())
    assert result["record_count"] > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
