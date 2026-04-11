"""End-to-end Adjutant interaction tests (Task 4.5, T9-T11).

Scenarios covered:
  - T9: player query is answered directly by Adjutant and bypasses Kernel task creation
  - T10: player reply to a pending question is routed back to the owning Task Agent
  - T11: mixed scenario where a pending question exists but a new player command creates a new Task,
         while the old question later times out to its default response
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from adjutant import Adjutant
from benchmark import clear as benchmark_clear
from benchmark import export_json as benchmark_export_json
from benchmark import query as benchmark_query
from experts.base import BaseJob, ExecutionExpert
from game_loop import GameLoop, GameLoopConfig
from kernel import Kernel, KernelConfig
from llm import LLMProvider, LLMResponse, ToolCall
from models import Event, TaskKind, TaskMessage, TaskMessageType
from models.configs import EXPERT_CONFIG_REGISTRY
from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from task_agent import AgentConfig, TaskAgent, ToolExecutor, WorldSummary
from tests.test_world_model import Frame, MockWorldSource, make_map
from world_model import RefreshPolicy, WorldModel

pytestmark = pytest.mark.mock_integration


@dataclass
class DecisionJobConfig:
    decision_response: str | None = None


class DecisionJob(BaseJob):
    tick_interval = 0.2

    @property
    def expert_type(self) -> str:
        return "DecisionExpert"

    def tick(self) -> None:
        return None


class DecisionExpert(ExecutionExpert):
    @property
    def expert_type(self) -> str:
        return "DecisionExpert"

    def create_job(self, task_id, config, signal_callback, constraint_provider=None):
        return DecisionJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )


class ReplyAwareTaskAgent(TaskAgent):
    """Test-only adapter that lets Kernel-delivered PlayerResponse wake the real TaskAgent loop."""

    def push_player_response(self, response) -> None:
        self.push_event(
            Event(
                type="player_response",  # type: ignore[arg-type]
                data={
                    "message_id": response.message_id,
                    "task_id": response.task_id,
                    "answer": response.answer,
                },
                timestamp=response.timestamp,
            )
        )


class ScenarioTaskAgentProvider(LLMProvider):
    """Mock TaskAgent LLM that only reacts to routed player responses."""

    def __init__(self) -> None:
        self.call_log: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.call_log.append({"messages": messages, "tools": tools})

        if messages and messages[-1].get("role") == "tool":
            return LLMResponse(text="已处理玩家回复。", model="scenario-task-agent")

        context = self._extract_context(messages)
        jobs = context["context_packet"]["jobs"]
        recent_events = context["context_packet"]["recent_events"]
        player_responses = [evt for evt in recent_events if evt.get("type") == "player_response"]
        if player_responses and jobs:
            answer = player_responses[-1]["data"]["answer"]
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="tc_patch_from_player",
                        name="patch_job",
                        arguments=json.dumps(
                            {
                                "job_id": jobs[0]["job_id"],
                                "params": {"decision_response": answer},
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
                model="scenario-task-agent",
            )

        return LLMResponse(text="无新决策，保持监控。", model="scenario-task-agent")

    @staticmethod
    def _extract_context(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for msg in reversed(messages):
            content = msg.get("content")
            if msg.get("role") == "user" and isinstance(content, str) and content.startswith("[CONTEXT UPDATE]"):
                # JSON header is on the second line (index 1); remaining lines are compact text.
                json_line = content.split("\n")[1]
                return json.loads(json_line)
        raise AssertionError("No context packet found in ScenarioTaskAgentProvider call")


class ScenarioAdjutantProvider(LLMProvider):
    """Mock Adjutant/query LLM for routing tests."""

    def __init__(self) -> None:
        self.call_log: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 800,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.call_log.append({"messages": messages, "tools": tools})
        system = messages[0]["content"]
        payload = json.loads(messages[-1]["content"])

        if "classify player input" in system:
            player_input = payload["player_input"]
            if player_input == "战况如何？":
                return LLMResponse(text='{"type":"query","confidence":0.98}', model="scenario-adjutant")
            if player_input == "继续":
                return LLMResponse(text='{"type":"reply","confidence":0.96}', model="scenario-adjutant")
            return LLMResponse(text='{"type":"command","confidence":0.97}', model="scenario-adjutant")

        summary = payload["world_summary"]
        active_tasks = payload["active_tasks"]
        answer = (
            f"当前现金{summary['economy']['cash']}，我方兵力{summary['military']['self_units']}，"
            f"已知敌方基地{summary['known_enemy']['bases']}处，当前活跃任务{len(active_tasks)}个。"
        )
        return LLMResponse(text=answer, model="scenario-adjutant")


def _make_world() -> WorldModel:
    frames = [
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
    world = WorldModel(
        MockWorldSource(frames),
        refresh_policy=RefreshPolicy(actors_s=0.05, economy_s=0.05, map_s=0.05),
    )
    world.refresh(now=100.0, force=True)
    return world


def _make_kernel(world: WorldModel, task_llm: ScenarioTaskAgentProvider) -> Kernel:
    def factory(
        task,
        tool_executor: ToolExecutor,
        jobs_provider,
        world_summary_provider: callable[[], WorldSummary],
    ) -> ReplyAwareTaskAgent:
        return ReplyAwareTaskAgent(
            task=task,
            llm=task_llm,
            tool_executor=tool_executor,
            jobs_provider=jobs_provider,
            world_summary_provider=world_summary_provider,
            config=AgentConfig(review_interval=10.0, max_retries=0, llm_timeout=1.0, max_turns=4),
            message_callback=None,
        )

    return Kernel(
        world_model=world,
        expert_registry={"DecisionExpert": DecisionExpert()},
        task_agent_factory=factory,
        config=KernelConfig(auto_start_agents=True),
    )


def _register_runtime(game_loop: GameLoop, kernel: Kernel, task_id: str, *, review_interval: float = 0.25) -> None:
    runtime = kernel._task_runtimes[task_id]  # type: ignore[attr-defined]
    game_loop.register_agent(task_id, runtime.agent.queue, review_interval=review_interval)
    for controller in kernel._jobs.values():  # type: ignore[attr-defined]
        if controller.task_id == task_id:
            game_loop.register_job(controller)


async def _wait_until(predicate, *, timeout: float, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for condition")


async def _stop_game_loop(loop: GameLoop, loop_task: asyncio.Task[Any]) -> None:
    loop.stop()
    try:
        await asyncio.wait_for(loop_task, timeout=0.5)
    except asyncio.TimeoutError:
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


@contextmanager
def _registered_decision_expert() -> Iterator[None]:
    previous = EXPERT_CONFIG_REGISTRY.get("DecisionExpert")
    EXPERT_CONFIG_REGISTRY["DecisionExpert"] = DecisionJobConfig
    try:
        yield
    finally:
        if previous is None:
            EXPERT_CONFIG_REGISTRY.pop("DecisionExpert", None)
        else:
            EXPERT_CONFIG_REGISTRY["DecisionExpert"] = previous


def test_e2e_t9_query_bypasses_kernel_task_creation() -> None:
    benchmark_clear()

    async def run() -> None:
        with _registered_decision_expert():
            world = _make_world()
            task_llm = ScenarioTaskAgentProvider()
            kernel = _make_kernel(world, task_llm)
            adjutant = Adjutant(llm=ScenarioAdjutantProvider(), kernel=kernel, world_model=world)

            existing_task = kernel.create_task("防守前线", TaskKind.MANAGED, 60)
            task_ids_before = {task.task_id for task in kernel.list_tasks()}

            game_loop = GameLoop(world, kernel, config=GameLoopConfig(tick_hz=30.0))
            _register_runtime(game_loop, kernel, existing_task.task_id)

            loop_task = asyncio.create_task(game_loop.start())
            try:
                await asyncio.sleep(0.08)
                result = await adjutant.handle_player_input("战况如何？")
            finally:
                await _stop_game_loop(game_loop, loop_task)

            assert result["type"] == "query"
            assert result["ok"] is True
            assert "当前现金3200" in result["response_text"]
            assert {task.task_id for task in kernel.list_tasks()} == task_ids_before
            assert kernel.list_pending_questions() == []

            llm_records = benchmark_query(tag="llm_call")
            world_records = benchmark_query(tag="world_refresh")
            assert llm_records, "expected llm_call benchmark records"
            assert world_records, "expected world_refresh benchmark records"
            assert any(record.name == "adjutant:handle_input" for record in llm_records)
            assert any(record.name == "adjutant:query" for record in llm_records)

    asyncio.run(run())
    print("  PASS: e2e_t9_query_bypasses_kernel_task_creation")


def test_e2e_t10_reply_routes_back_to_task_agent_patch() -> None:
    benchmark_clear()

    async def run() -> None:
        with _registered_decision_expert():
            world = _make_world()
            task_llm = ScenarioTaskAgentProvider()
            kernel = _make_kernel(world, task_llm)
            adjutant = Adjutant(llm=ScenarioAdjutantProvider(), kernel=kernel, world_model=world)

            task = kernel.create_task("继续推进前线", TaskKind.MANAGED, 55)
            job = kernel.start_job(task.task_id, "DecisionExpert", DecisionJobConfig())

            kernel.register_task_message(
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

            game_loop = GameLoop(world, kernel, config=GameLoopConfig(tick_hz=40.0))
            _register_runtime(game_loop, kernel, task.task_id)

            loop_task = asyncio.create_task(game_loop.start())
            try:
                result = await adjutant.handle_player_input("继续")
                assert result["type"] == "reply"
                assert result["ok"] is True
                await _wait_until(
                    lambda: kernel._jobs[job.job_id].config.decision_response == "继续",  # type: ignore[attr-defined]
                    timeout=1.0,
                )
            finally:
                await _stop_game_loop(game_loop, loop_task)

            patched = kernel._jobs[job.job_id].config.decision_response  # type: ignore[attr-defined]
            assert patched == "继续"
            assert kernel.list_pending_questions() == []
            assert len(kernel.list_tasks()) == 1

            llm_records = benchmark_query(tag="llm_call")
            tool_records = benchmark_query(tag="tool_exec")
            job_records = benchmark_query(tag="job_tick")
            assert llm_records, "expected llm_call benchmark records"
            assert tool_records, "expected tool_exec benchmark records"
            assert job_records, "expected job_tick benchmark records"
            assert any(record.name == "kernel:submit_player_response" for record in tool_records)
            assert any(record.name == "tool:patch_job" for record in tool_records)

    asyncio.run(run())
    print("  PASS: e2e_t10_reply_routes_back_to_task_agent_patch")


def test_e2e_t11_new_command_during_pending_question_then_timeout_default() -> None:
    benchmark_clear()

    async def run() -> None:
        with _registered_decision_expert():
            world = _make_world()
            task_llm = ScenarioTaskAgentProvider()
            kernel = _make_kernel(world, task_llm)
            adjutant = Adjutant(llm=ScenarioAdjutantProvider(), kernel=kernel, world_model=world)

            task_a = kernel.create_task("压制右路", TaskKind.MANAGED, 60)
            job_a = kernel.start_job(task_a.task_id, "DecisionExpert", DecisionJobConfig())

            kernel.register_task_message(
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

            game_loop = GameLoop(world, kernel, config=GameLoopConfig(tick_hz=50.0))
            _register_runtime(game_loop, kernel, task_a.task_id)

            loop_task = asyncio.create_task(game_loop.start())
            try:
                result = await adjutant.handle_player_input("生产5辆坦克")
                assert result["type"] == "command"
                assert result["ok"] is True
                new_task_id = result["task_id"]
                _register_runtime(game_loop, kernel, new_task_id)

                assert len(kernel.list_tasks()) == 2
                assert len(kernel.list_pending_questions()) == 1
                assert kernel.tasks[new_task_id].raw_text == "生产5辆坦克"

                await _wait_until(
                    lambda: kernel._jobs[job_a.job_id].config.decision_response == "放弃",  # type: ignore[attr-defined]
                    timeout=1.5,
                )
            finally:
                await _stop_game_loop(game_loop, loop_task)

            assert kernel._jobs[job_a.job_id].config.decision_response == "放弃"  # type: ignore[attr-defined]
            assert kernel.list_pending_questions() == []
            assert any(task.raw_text == "生产5辆坦克" for task in kernel.list_tasks())

            llm_records = benchmark_query(tag="llm_call")
            tool_records = benchmark_query(tag="tool_exec")
            world_records = benchmark_query(tag="world_refresh")
            job_records = benchmark_query(tag="job_tick")
            assert llm_records, "expected llm_call benchmark records"
            assert tool_records, "expected tool_exec benchmark records"
            assert world_records, "expected world_refresh benchmark records"
            assert job_records, "expected job_tick benchmark records"
            assert any(record.name == "kernel:create_task" for record in tool_records)
            assert any(record.name == "kernel:tick" for record in tool_records)
            assert any(record.name == "tool:patch_job" for record in tool_records)

            with tempfile.TemporaryDirectory() as tmpdir:
                output_path = os.path.join(tmpdir, "adjutant_e2e_benchmark.json")
                exported = benchmark_export_json(output_path)
                payload = json.loads(exported)
                assert payload
                tags = {item["tag"] for item in payload}
                assert {"llm_call", "tool_exec", "world_refresh", "job_tick"}.issubset(tags)
                with open(output_path, "r", encoding="utf-8") as handle:
                    written = json.load(handle)
                assert written == payload

    asyncio.run(run())
    print("  PASS: e2e_t11_new_command_during_pending_question_then_timeout_default")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
