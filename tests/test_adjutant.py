"""Tests for Adjutant — mock LLM, Kernel, WorldModel covering all 3 input paths."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

import logging_system
from llm import LLMResponse, MockProvider
from models import PlayerResponse, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus
from adjutant import (
    Adjutant, AdjutantConfig, AdjutantContext, InputType,
    CLASSIFICATION_SYSTEM_PROMPT,
    NotificationManager, format_notification, notification_to_text, notification_to_dict,
)


# --- Mocks ---

class MockTask:
    def __init__(self, task_id, raw_text, status="running"):
        self.task_id = task_id
        self.raw_text = raw_text
        self.status = type("S", (), {"value": status})()
        self.kind = TaskKind.MANAGED
        self.priority = 50
        self.created_at = time.time()
        self.timestamp = time.time()


class MockKernel:
    def __init__(self):
        self.created_tasks: list[dict] = []
        self.started_jobs: list[dict] = []
        self.submitted_responses: list[PlayerResponse] = []
        self._pending_questions: list[dict] = []
        self._tasks: list[MockTask] = []
        self._task_counter = 0
        self._job_counter = 0

    def create_task(self, raw_text, kind, priority, info_subscriptions=None):
        self._task_counter += 1
        task = MockTask(f"t_{self._task_counter}", raw_text)
        self.created_tasks.append({"raw_text": raw_text, "kind": kind, "priority": priority})
        self._tasks.append(task)
        return task

    def start_job(self, task_id, expert_type, config):
        self._job_counter += 1
        job_id = f"j_{self._job_counter}"
        self.started_jobs.append(
            {"task_id": task_id, "expert_type": expert_type, "config": config, "job_id": job_id}
        )
        return type("MockJob", (), {"job_id": job_id})()

    def submit_player_response(self, response, *, now=None):
        self.submitted_responses.append(response)
        return {"ok": True, "status": "delivered"}

    def list_pending_questions(self):
        # Real Kernel sorts by priority descending
        return sorted(self._pending_questions, key=lambda q: q.get("priority", 0), reverse=True)

    def list_tasks(self):
        return list(self._tasks)

    def add_pending_question(self, message_id, task_id, question, options, priority=50):
        self._pending_questions.append({
            "message_id": message_id,
            "task_id": task_id,
            "question": question,
            "options": options,
            "default_option": options[0] if options else None,
            "priority": priority,
            "asked_at": time.time(),
            "timeout_s": 30.0,
        })


class MockWorldModel:
    def world_summary(self):
        return {
            "economy": {"cash": 5000, "income": 200},
            "military": {"self_units": 15, "enemy_units": 8, "self_combat_value": 2500},
            "map": {"explored_pct": 0.45},
            "known_enemy": {"units_spotted": 8, "bases": 1},
            "timestamp": time.time(),
        }

    def query(self, query_type, params=None):
        if query_type == "my_actors" and params == {"category": "mcv"}:
            return {
                "actors": [
                    {
                        "actor_id": 99,
                        "category": "mcv",
                        "position": [500, 400],
                    }
                ],
                "timestamp": time.time(),
            }
        if query_type == "find_actors":
            owner = (params or {}).get("owner")
            name = (params or {}).get("name")
            actors = []
            if owner == "self" and name == "步兵":
                actors = [
                    {"actor_id": 11, "name": "步兵"},
                    {"actor_id": 12, "name": "步兵"},
                ]
            return {"actors": actors, "timestamp": time.time()}
        if query_type == "my_actors" and params == {"category": "harvester"}:
            return {
                "actors": [
                    {"actor_id": 301, "category": "harvester"},
                    {"actor_id": 302, "category": "harvester"},
                ],
                "timestamp": time.time(),
            }
        if query_type == "my_actors" and params == {"name": None, "can_attack": True}:
            return {
                "actors": [
                    {"actor_id": 401, "name": "步兵"},
                    {"actor_id": 402, "name": "坦克"},
                ],
                "timestamp": time.time(),
            }
        if query_type == "my_actors" and params == {"name": "步兵", "can_attack": True}:
            return {
                "actors": [
                    {"actor_id": 401, "name": "步兵"},
                    {"actor_id": 403, "name": "步兵"},
                ],
                "timestamp": time.time(),
            }
        return {"data": [], "timestamp": time.time()}

    def refresh_health(self):
        return {
            "stale": False,
            "consecutive_failures": 0,
            "total_failures": 0,
            "last_error": None,
            "failure_threshold": 3,
            "timestamp": time.time(),
        }


class MockGameAPI:
    def __init__(self):
        self.deployed_units: list[list[int]] = []
        self.stopped_units: list[list[int]] = []

    def deploy_units(self, actors):
        self.deployed_units.append([actor.actor_id for actor in actors])

    def stop(self, actors):
        self.stopped_units.append([actor.actor_id for actor in actors])


# --- Tests ---

def test_command_classification():
    """New command input is routed to Kernel.create_task."""
    # LLM classifies as command
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.95}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("生产5辆坦克")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert "task_id" in result

    asyncio.run(run())

    assert len(kernel.created_tasks) == 1
    assert kernel.created_tasks[0]["raw_text"] == "生产5辆坦克"
    print("  PASS: command_classification")


def test_nlu_routed_build_skips_llm_and_starts_economy_job():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    captured: dict[str, object] = {}

    async def run():
        result = await adjutant.handle_player_input("建造电厂")
        captured.update(result)
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "EconomyExpert"
        assert result["nlu_route_intent"] == "produce"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 1
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["expert_type"] == "EconomyExpert"
    assert kernel.started_jobs[0]["config"].unit_type == "powr"
    assert kernel.started_jobs[0]["config"].queue_type == "Building"
    assert captured["nlu_source"] == "nlu_route"
    print("  PASS: nlu_routed_build_skips_llm_and_starts_economy_job")


def test_nlu_routed_production_parses_count_and_skips_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("生产3个步兵")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].unit_type == "e1"
    assert kernel.started_jobs[0]["config"].count == 3
    assert kernel.started_jobs[0]["config"].queue_type == "Infantry"
    print("  PASS: nlu_routed_production_parses_count_and_skips_llm")


def test_runtime_nlu_routes_shorthand_production_without_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("步兵3")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "EconomyExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 1
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["config"].unit_type == "e1"
    assert kernel.started_jobs[0]["config"].count == 3
    assert kernel.started_jobs[0]["config"].queue_type == "Infantry"
    print("  PASS: runtime_nlu_routes_shorthand_production_without_llm")


def test_runtime_nlu_routes_safe_composite_sequence_into_multiple_direct_jobs():
    """composite_sequence: only first step started immediately; rest queued and advanced on completion."""
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("建造电厂，兵营，步兵")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        # First step only — task_id (not task_ids) and 2 steps pending
        assert "task_id" in result
        assert result.get("pending_steps") == 2

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    # Only first task created so far
    assert len(kernel.created_tasks) == 1
    assert kernel.started_jobs[0]["config"].unit_type == "powr"
    first_task_id = kernel._tasks[0].task_id

    # Simulate first task completing → second step should start
    adjutant.notify_task_completed(
        label=first_task_id, raw_text="建造电厂", result="succeeded", summary="done", task_id=first_task_id
    )
    assert len(kernel.created_tasks) == 2
    assert kernel.started_jobs[1]["config"].unit_type == "barr"
    second_task_id = kernel._tasks[1].task_id

    # Simulate second task completing → third step should start
    adjutant.notify_task_completed(
        label=second_task_id, raw_text="建造兵营", result="succeeded", summary="done", task_id=second_task_id
    )
    assert len(kernel.created_tasks) == 3
    assert kernel.started_jobs[2]["config"].unit_type == "e1"
    assert adjutant._pending_sequence == []
    assert adjutant._sequence_task_id is not None  # still tracking last step

    print("  PASS: runtime_nlu_routes_safe_composite_sequence_into_multiple_direct_jobs")


def test_runtime_nlu_query_actor_returns_direct_query_response():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("查看己方步兵")
        assert result["type"] == "query"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["nlu_route_intent"] == "query_actor"
        assert "己方步兵共 2 个" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 0
    print("  PASS: runtime_nlu_query_actor_returns_direct_query_response")


def test_runtime_nlu_mine_uses_game_api_without_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    game_api = MockGameAPI()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm, game_api=game_api)

    async def run():
        result = await adjutant.handle_player_input("让矿车去采矿")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert "恢复采矿" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert game_api.deployed_units == [[301, 302]]
    assert len(kernel.created_tasks) == 0
    print("  PASS: runtime_nlu_mine_uses_game_api_without_llm")


def test_runtime_nlu_stop_attack_uses_game_api_without_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    game_api = MockGameAPI()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm, game_api=game_api)

    async def run():
        result = await adjutant.handle_player_input("停止攻击")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert "停止 2 个单位" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert game_api.stopped_units == [[401, 402]]
    assert len(kernel.created_tasks) == 0
    print("  PASS: runtime_nlu_stop_attack_uses_game_api_without_llm")


def test_nlu_routed_deploy_uses_mcv_query():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    captured: dict[str, object] = {}

    async def run():
        result = await adjutant.handle_player_input("部署基地车")
        captured.update(result)
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "DeployExpert"
        assert result["nlu_route_intent"] == "deploy_mcv"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].actor_id == 99
    assert kernel.started_jobs[0]["config"].target_position == (500, 400)
    assert captured["nlu_source"] == "nlu_route"
    print("  PASS: nlu_routed_deploy_uses_mcv_query")


def test_nlu_routed_expand_mcv_uses_deploy_path():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    captured: dict[str, object] = {}

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        captured.update(result)
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "DeployExpert"
        assert result["nlu_route_intent"] == "deploy_mcv"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].actor_id == 99
    assert captured["nlu_source"] == "nlu_route"
    print("  PASS: nlu_routed_expand_mcv_uses_deploy_path")


def test_deploy_without_mcv_but_with_construction_yard_returns_immediate_feedback():
    class AlreadyDeployedWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [{"actor_id": 130, "type": "建造厂"}], "timestamp": time.time()}
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = AlreadyDeployedWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("部署基地车")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_deploy_already_deployed"
        assert "建造厂已存在" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_without_mcv_but_with_construction_yard_returns_immediate_feedback")


def test_deploy_without_mcv_returns_missing_feedback():
    class MissingMcvWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params in ({"category": "mcv"}, {"type": "建造厂"}):
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MissingMcvWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("部署基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_deploy_missing_mcv"
        assert "没有可部署的基地车" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_without_mcv_returns_missing_feedback")


def test_deploy_feedback_refuses_stale_world_assertions():
    class StaleWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": True,
                "consecutive_failures": 12,
                "total_failures": 12,
                "last_error": "actors:COMMAND_EXECUTION_ERROR",
                "failure_threshold": 3,
                "timestamp": time.time(),
            }

        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [{"actor_id": 130, "type": "建造厂"}], "timestamp": time.time()}
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = StaleWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "world_sync_stale"
        assert "状态同步异常" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_stale_world_assertions")


def test_nlu_routed_recon_skips_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    captured: dict[str, object] = {}

    async def run():
        result = await adjutant.handle_player_input("探索地图")
        captured.update(result)
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "ReconExpert"
        assert result["nlu_route_intent"] == "explore"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].search_region == "enemy_half"
    assert kernel.started_jobs[0]["config"].target_type == "base"
    assert captured["nlu_source"] == "nlu_route"
    print("  PASS: nlu_routed_recon_skips_llm")


def test_unmatched_command_still_uses_llm_path():
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.95}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理后进攻")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert "routing" not in result

    asyncio.run(run())

    assert len(mock_llm.call_log) == 1
    assert len(kernel.started_jobs) == 0
    assert len(kernel.created_tasks) == 1
    print("  PASS: unmatched_command_still_uses_llm_path")


def test_reply_classification():
    """Reply to pending question is routed to Kernel.submit_player_response."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "包围右边基地"))
    kernel.add_pending_question("msg_1", "t1", "兵力不足，继续还是放弃？", ["继续", "放弃"], priority=60)

    # LLM classifies as reply matching msg_1
    mock_llm = MockProvider(responses=[
        LLMResponse(
            text='{"type":"reply","target_message_id":"msg_1","target_task_id":"t1","confidence":0.9}',
            model="mock",
        ),
    ])
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("继续")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())

    assert len(kernel.submitted_responses) == 1
    assert kernel.submitted_responses[0].message_id == "msg_1"
    assert kernel.submitted_responses[0].task_id == "t1"
    assert kernel.submitted_responses[0].answer == "继续"
    print("  PASS: reply_classification")


def test_reply_fallback_highest_priority():
    """Ambiguous reply without target_message_id matches highest-priority question."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续", "放弃"], priority=60)

    # LLM classifies as reply but no specific target
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"reply","confidence":0.7}', model="mock"),
    ])
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("放弃")
        assert result["type"] == "reply"
        assert result["ok"] is True

    asyncio.run(run())

    # Should match highest-priority question (msg_high, priority=60)
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].answer == "放弃"
    print("  PASS: reply_fallback_highest_priority")


def test_query_classification():
    """Query input gets direct LLM+WorldModel answer, no Task created."""
    # First call: classification. Second call: query answer.
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"query","confidence":0.95}', model="mock"),
        LLMResponse(text="当前经济良好(cash:5000)，兵力优势(15 vs 8)，建议进攻", model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is True
        assert "经济" in result["response_text"] or "兵力" in result["response_text"]

    asyncio.run(run())

    # No task created
    assert len(kernel.created_tasks) == 0
    print("  PASS: query_classification")


def test_classification_failure_defaults_to_command():
    """If LLM classification fails, defaults to command."""
    class FailingLLM(MockProvider):
        call_count = 0
        async def chat(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError("classification failed")
            return LLMResponse(text="ok", model="mock")

    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=FailingLLM(), kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("探索地图")
        assert result["type"] == "command"

    asyncio.run(run())
    assert len(kernel.created_tasks) == 1
    print("  PASS: classification_failure_defaults_to_command")


def test_task_message_formatting():
    """TaskMessage formatting works for all types in text and card mode."""
    info = TaskMessage(
        message_id="m1", task_id="t1", type=TaskMessageType.TASK_INFO,
        content="已找到敌人基地 (1820,430)",
    )
    warning = TaskMessage(
        message_id="m2", task_id="t1", type=TaskMessageType.TASK_WARNING,
        content="侦察兵血量低",
    )
    question = TaskMessage(
        message_id="m3", task_id="t1", type=TaskMessageType.TASK_QUESTION,
        content="兵力不足，继续进攻还是放弃？", options=["继续", "放弃"],
        timeout_s=30.0, default_option="放弃",
    )
    complete = TaskMessage(
        message_id="m4", task_id="t1", type=TaskMessageType.TASK_COMPLETE_REPORT,
        content="包围成功，敌人基地已摧毁",
    )

    # Text mode
    assert "[任务 t1]" in Adjutant.format_task_message(info, "text")
    assert "⚠" in Adjutant.format_task_message(warning, "text")
    assert "❓" in Adjutant.format_task_message(question, "text")
    assert "继续 / 放弃" in Adjutant.format_task_message(question, "text")
    assert "✓" in Adjutant.format_task_message(complete, "text")

    # Card mode (JSON)
    card = json.loads(Adjutant.format_task_message(question, "card"))
    assert card["type"] == "task_question"
    assert card["options"] == ["继续", "放弃"]
    assert card["timeout_s"] == 30.0
    print("  PASS: task_message_formatting")


def test_dialogue_history():
    """Dialogue history is recorded and trimmed."""
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(
        llm=mock_llm, kernel=kernel, world_model=wm,
        config=AdjutantConfig(max_dialogue_history=3),
    )

    async def run():
        await adjutant.handle_player_input("第一条")
        await adjutant.handle_player_input("第二条")
        assert len(adjutant._dialogue_history) == 4  # 2 player + 2 adjutant

    asyncio.run(run())
    print("  PASS: dialogue_history")


def test_notification_formatting():
    """Notifications are formatted with correct icon and severity."""
    raw = {"type": "ENEMY_EXPANSION", "content": "发现敌人在(1200,300)扩张", "data": {"pos": [1200, 300]}, "timestamp": 100.0}
    formatted = format_notification(raw)
    assert formatted.severity == "info"
    assert formatted.icon == "🔍"
    assert formatted.content == "发现敌人在(1200,300)扩张"
    assert formatted.data["pos"] == [1200, 300]

    text = notification_to_text(formatted)
    assert "🔍" in text
    assert "扩张" in text

    d = notification_to_dict(formatted)
    assert d["type"] == "ENEMY_EXPANSION"
    assert d["severity"] == "info"
    assert "timestamp" in d

    # Warning type
    raw_warn = {"type": "FRONTLINE_WEAK", "content": "我方前线空虚", "data": {}, "timestamp": 101.0}
    warn = format_notification(raw_warn)
    assert warn.severity == "warning"
    assert warn.icon == "⚠"
    print("  PASS: notification_formatting")


def test_notification_manager_poll_and_push():
    """NotificationManager polls new notifications and pushes via sink."""
    pushed: list[dict] = []

    class MockNotifKernel:
        def __init__(self):
            self._notifications: list[dict] = []

        def list_player_notifications(self):
            return list(self._notifications)

        def add(self, ntype, content, data=None):
            self._notifications.append({"type": ntype, "content": content, "data": data or {}, "timestamp": time.time()})

    kernel = MockNotifKernel()

    async def sink(notification):
        pushed.append(notification)

    manager = NotificationManager(kernel=kernel, sink=sink)

    async def run():
        # No notifications yet
        result = await manager.poll_and_push()
        assert result == []
        assert pushed == []

        # Add 2 notifications
        kernel.add("ENEMY_EXPANSION", "发现敌人扩张")
        kernel.add("FRONTLINE_WEAK", "前线空虚")

        result = await manager.poll_and_push()
        assert len(result) == 2
        assert len(pushed) == 2
        assert pushed[0]["type"] == "ENEMY_EXPANSION"
        assert pushed[1]["type"] == "FRONTLINE_WEAK"

        # Poll again — no new ones
        result = await manager.poll_and_push()
        assert result == []
        assert len(pushed) == 2  # No duplicates

        # Add one more
        kernel.add("ECONOMY_SURPLUS", "经济充裕")
        result = await manager.poll_and_push()
        assert len(result) == 1
        assert len(pushed) == 3
        assert pushed[2]["type"] == "ECONOMY_SURPLUS"

        assert manager.total_pushed == 3

    asyncio.run(run())
    print("  PASS: notification_manager_poll_and_push")


def test_notification_manager_no_sink():
    """NotificationManager works without a sink (just tracks history)."""
    class MockNotifKernel:
        def list_player_notifications(self):
            return [{"type": "FRONTLINE_WEAK", "content": "弱", "data": {}, "timestamp": 100.0}]

    manager = NotificationManager(kernel=MockNotifKernel(), sink=None)

    async def run():
        result = await manager.poll_and_push()
        assert len(result) == 1
        assert len(manager.history) == 1

    asyncio.run(run())
    print("  PASS: notification_manager_no_sink")


# --- T11: _rule_based_classify reply detection ---

def _make_failing_llm():
    """LLM that raises TimeoutError on the first call (classification), succeeds after."""
    class FailingLLM(MockProvider):
        call_count = 0
        async def chat(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError("classification failed")
            return LLMResponse(text="ok", model="mock")
    return FailingLLM()


def test_rule_based_classify_reply_exact_match():
    """LLM failure + pending question: exact option text → REPLY routed to kernel."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "包围右边基地"))
    kernel.add_pending_question("msg_1", "t1", "兵力不足，继续还是放弃？", ["继续", "放弃"], priority=60)

    adjutant = Adjutant(llm=_make_failing_llm(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("继续")
        assert result["type"] == "reply", f"Expected reply, got {result['type']}"
        assert result["ok"] is True

    asyncio.run(run())
    assert len(kernel.submitted_responses) == 1
    assert kernel.submitted_responses[0].message_id == "msg_1"
    assert kernel.submitted_responses[0].task_id == "t1"
    assert kernel.submitted_responses[0].answer == "继续"
    print("  PASS: rule_based_classify_reply_exact_match")


def test_rule_based_classify_reply_fuzzy_match():
    """LLM failure + pending question: fuzzy word '好' → REPLY routed to kernel."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel.add_pending_question("msg_1", "t1", "继续进攻吗？", ["继续", "撤退"], priority=50)

    adjutant = Adjutant(llm=_make_failing_llm(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("好")
        assert result["type"] == "reply", f"Expected reply, got {result['type']}"
        assert result["ok"] is True

    asyncio.run(run())
    assert len(kernel.submitted_responses) == 1
    assert kernel.submitted_responses[0].message_id == "msg_1"
    assert kernel.submitted_responses[0].answer == "好"
    print("  PASS: rule_based_classify_reply_fuzzy_match")


def test_rule_based_classify_no_pending_command():
    """LLM failure + no pending question: input is NOT misclassified as reply → COMMAND."""
    kernel = MockKernel()
    adjutant = Adjutant(llm=_make_failing_llm(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("继续")
        # No pending question → should become a command, not a reply
        assert result["type"] == "command", f"Expected command, got {result['type']}"

    asyncio.run(run())
    assert len(kernel.submitted_responses) == 0
    print("  PASS: rule_based_classify_no_pending_command")


def test_rule_based_classify_query_fallback():
    """LLM failure + no pending question + query keyword → QUERY."""
    kernel = MockKernel()

    class TwoCallLLM(MockProvider):
        call_count = 0
        async def chat(self, messages, **kwargs):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError("classification failed")
            return LLMResponse(text="当前形势良好", model="mock")

    adjutant = Adjutant(llm=TwoCallLLM(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("战况如何？")
        assert result["type"] == "query", f"Expected query, got {result['type']}"

    asyncio.run(run())
    print("  PASS: rule_based_classify_query_fallback")


def test_rule_based_classify_reply_highest_priority():
    """LLM failure + multiple pending questions: routes reply to highest-priority question."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续进攻？", ["继续", "放弃"], priority=70)

    adjutant = Adjutant(llm=_make_failing_llm(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("放弃")
        assert result["type"] == "reply", f"Expected reply, got {result['type']}"
        assert result["ok"] is True

    asyncio.run(run())
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].answer == "放弃"
    print("  PASS: rule_based_classify_reply_highest_priority")


# --- T9: Observability — slog coverage ---

def _events_from_log(before_count: int) -> list[str]:
    """Collect event names from structured log records added after before_count."""
    all_records = logging_system.records()
    return [getattr(r, "event", None) for r in all_records[before_count:] if getattr(r, "event", None)]


def test_observability_nlu_path_has_three_logs():
    """NLU path: player_input + nlu_routed_command + route_result ≥ 3 logs."""
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=wm)

    before = len(logging_system.records())

    async def run():
        await adjutant.handle_player_input("侦察敌方基地")

    asyncio.run(run())

    events = _events_from_log(before)
    assert "player_input" in events, f"player_input missing: {events}"
    assert "nlu_routed_command" in events, f"nlu_routed_command missing: {events}"
    assert "route_result" in events, f"route_result missing: {events}"
    assert len(events) >= 3, f"Expected ≥3 events, got {len(events)}: {events}"
    print("  PASS: observability_nlu_path_has_three_logs")


def test_observability_llm_path_has_three_logs():
    """LLM classification path: player_input + input_classified + route_decision ≥ 3 logs."""
    kernel = MockKernel()
    wm = MockWorldModel()
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.9}', model="mock"),
        LLMResponse(text="任务执行中", model="mock"),
    ])
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    before = len(logging_system.records())

    async def run():
        # Long compound input — bypasses NLU and rule matchers
        await adjutant.handle_player_input("打下右边那个基地然后再扩张一下经济吧")

    asyncio.run(run())

    events = _events_from_log(before)
    assert "player_input" in events, f"player_input missing: {events}"
    assert "input_classified" in events, f"input_classified missing: {events}"
    assert "route_decision" in events, f"route_decision missing: {events}"
    assert len(events) >= 3, f"Expected ≥3 events, got {len(events)}: {events}"
    print("  PASS: observability_llm_path_has_three_logs")


def test_observability_rule_path_has_three_logs():
    """Rule path: player_input + rule_routed_command + route_result ≥ 3 logs.

    Uses a subclass that forces NLU to return None so _try_rule_match runs.
    """
    class NoNLUAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None  # force rule path

    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = NoNLUAdjutant(llm=MockProvider(), kernel=kernel, world_model=wm)

    before = len(logging_system.records())

    async def run():
        await adjutant.handle_player_input("建造矿场")  # triggers _match_build

    asyncio.run(run())

    events = _events_from_log(before)
    assert "player_input" in events, f"player_input missing: {events}"
    assert "rule_routed_command" in events, f"rule_routed_command missing: {events}"
    assert "route_result" in events, f"route_result missing: {events}"
    assert len(events) >= 3, f"Expected ≥3 events, got {len(events)}: {events}"
    print("  PASS: observability_rule_path_has_three_logs")


# --- Dialogue context enhancement tests ---

def test_acknowledgment_words_bypass_nlu_and_llm():
    """BUG-B: 'ok'/'好的' etc. return ack immediately without creating a task."""
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=wm)

    for word in ["ok", "好", "好的", "收到", "知道了", "嗯", "行"]:
        async def run(w=word):
            return await adjutant.handle_player_input(w)
        result = asyncio.run(run())
        assert result["type"] == "ack", f"'{word}' should be ack, got {result['type']}"
        assert result["ok"] is True
        assert not kernel.created_tasks, f"'{word}' should not create a task"
        kernel.created_tasks.clear()

    print("  PASS: acknowledgment_words_bypass_nlu_and_llm")


def test_acknowledgment_passes_through_when_pending_question():
    """BUG-B: ack detection skipped when there is a pending question (it might be a reply)."""
    kernel = MockKernel()
    kernel.add_pending_question("m1", "t1", "继续吗？", ["继续", "放弃"])
    wm = MockWorldModel()
    mock_llm = MockProvider(responses=[LLMResponse(
        text='{"type":"reply","target_message_id":"m1","target_task_id":"t1","confidence":0.9}',
        model="mock"
    )])
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        return await adjutant.handle_player_input("好")

    result = asyncio.run(run())
    # "好" with a pending question should NOT be ack — should pass through to reply path
    assert result["type"] != "ack", f"'好' with pending question should not be ack"
    print("  PASS: acknowledgment_passes_through_when_pending_question")


def test_question_words_bypass_nlu_routing():
    """BUG-C: question sentences skip NLU and go to LLM classification, never become commands."""
    question_inputs = [
        "为什么探索地图一直waiting",
        "怎么还没建完",
        "吗",
        "这样行吗",
        "什么时候能造完",
        "如何提升采矿效率",
    ]

    class NoNLUAdjutant(Adjutant):
        """Force NLU path only; stub _handle_query to avoid a second LLM call per question."""
        def _try_runtime_nlu(self, text):
            return super()._try_runtime_nlu(text)  # let my question check fire

        async def _handle_query(self, text, context):
            return {"type": "query", "ok": True, "response_text": "stubbed"}

    kernel = MockKernel()
    wm = MockWorldModel()
    # Each question: 1 LLM call for classification → "query" → stubbed _handle_query
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"query","confidence":0.9}', model="mock")
        for _ in range(len(question_inputs))
    ])
    adjutant = NoNLUAdjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    for q in question_inputs:
        async def run(text=q):
            return await adjutant.handle_player_input(text)
        asyncio.run(run())

    # No tasks should have been created by NLU mis-routing
    assert not kernel.created_tasks, f"Questions should not create tasks, got: {kernel.created_tasks}"
    print("  PASS: question_words_bypass_nlu_routing")


def test_notify_task_completed_records_in_dialogue_history():
    """notify_task_completed appends a system entry to dialogue history."""
    adjutant = Adjutant(llm=MockProvider(), kernel=MockKernel(), world_model=MockWorldModel())
    assert adjutant._dialogue_history == []
    adjutant.notify_task_completed(label="005", raw_text="发展科技", result="failed", summary="前置建筑不满足")
    assert len(adjutant._dialogue_history) == 1
    entry = adjutant._dialogue_history[0]
    assert entry["from"] == "system"
    assert "005" in entry["content"]
    assert "发展科技" in entry["content"]
    assert "failed" in entry["content"]
    assert "前置建筑不满足" in entry["content"]
    print("  PASS: notify_task_completed_records_in_dialogue_history")


def test_notify_task_completed_caps_recent_completed_at_five():
    """_recent_completed keeps only the last 5 entries."""
    adjutant = Adjutant(llm=MockProvider(), kernel=MockKernel(), world_model=MockWorldModel())
    for i in range(7):
        adjutant.notify_task_completed(label=f"{i:03d}", raw_text=f"task {i}", result="succeeded", summary="ok")
    assert len(adjutant._recent_completed) == 5
    # Most recent 5
    assert adjutant._recent_completed[0]["label"] == "002"
    assert adjutant._recent_completed[-1]["label"] == "006"
    print("  PASS: notify_task_completed_caps_recent_completed_at_five")


def test_build_context_includes_recent_completed_tasks():
    """_build_context returns recent_completed_tasks from _recent_completed."""
    adjutant = Adjutant(llm=MockProvider(), kernel=MockKernel(), world_model=MockWorldModel())
    adjutant.notify_task_completed(label="001", raw_text="建造兵营", result="succeeded", summary="兵营已建完")
    adjutant.notify_task_completed(label="002", raw_text="发展科技", result="failed", summary="前置建筑不满足")
    ctx = adjutant._build_context("你根据需求建造啊")
    assert len(ctx.recent_completed_tasks) == 2
    assert ctx.recent_completed_tasks[0]["label"] == "001"
    assert ctx.recent_completed_tasks[1]["result"] == "failed"
    print("  PASS: build_context_includes_recent_completed_tasks")


def test_classify_input_sends_recent_completed_to_llm():
    """_classify_input includes recent_completed_tasks in the JSON sent to LLM."""
    captured: list[dict] = []

    class CapturingProvider:
        async def chat(self, messages, **_kw):
            captured.extend(messages)
            return LLMResponse(text='{"type":"command","confidence":0.9}', model="mock")

    # Subclass to force LLM path (bypass NLU / rule matching)
    class LLMOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None
        def _try_rule_match(self, text):
            return None

    adjutant = LLMOnlyAdjutant(llm=CapturingProvider(), kernel=MockKernel(), world_model=MockWorldModel())
    adjutant.notify_task_completed(label="003", raw_text="发展科技", result="failed", summary="缺雷达站")

    async def run():
        await adjutant.handle_player_input("你根据需求处理吧")

    asyncio.run(run())
    assert len(captured) >= 2, f"Expected LLM to be called, captured={captured}"
    user_msg = next(m for m in captured if m["role"] == "user")
    payload = json.loads(user_msg["content"])
    assert "recent_completed_tasks" in payload
    completed = payload["recent_completed_tasks"]
    assert len(completed) == 1
    assert completed[0]["label"] == "003"
    assert completed[0]["result"] == "failed"
    print("  PASS: classify_input_sends_recent_completed_to_llm")


def test_system_prompt_has_dialogue_context_awareness_section():
    """CLASSIFICATION_SYSTEM_PROMPT contains the dialogue context awareness section."""
    assert "Dialogue context awareness" in CLASSIFICATION_SYSTEM_PROMPT
    assert "recent_completed_tasks" in CLASSIFICATION_SYSTEM_PROMPT
    assert "failed" in CLASSIFICATION_SYSTEM_PROMPT
    print("  PASS: system_prompt_has_dialogue_context_awareness_section")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Adjutant tests...\n")

    test_command_classification()
    test_nlu_routed_build_skips_llm_and_starts_economy_job()
    test_nlu_routed_production_parses_count_and_skips_llm()
    test_runtime_nlu_routes_shorthand_production_without_llm()
    test_runtime_nlu_routes_safe_composite_sequence_into_multiple_direct_jobs()
    test_runtime_nlu_query_actor_returns_direct_query_response()
    test_runtime_nlu_mine_uses_game_api_without_llm()
    test_runtime_nlu_stop_attack_uses_game_api_without_llm()
    test_nlu_routed_deploy_uses_mcv_query()
    test_nlu_routed_expand_mcv_uses_deploy_path()
    test_deploy_without_mcv_but_with_construction_yard_returns_immediate_feedback()
    test_deploy_without_mcv_returns_missing_feedback()
    test_deploy_feedback_refuses_stale_world_assertions()
    test_nlu_routed_recon_skips_llm()
    test_unmatched_command_still_uses_llm_path()
    test_reply_classification()
    test_reply_fallback_highest_priority()
    test_query_classification()
    test_classification_failure_defaults_to_command()
    test_task_message_formatting()
    test_dialogue_history()
    test_notification_formatting()
    test_notification_manager_poll_and_push()
    test_notification_manager_no_sink()
    test_rule_based_classify_reply_exact_match()
    test_rule_based_classify_reply_fuzzy_match()
    test_rule_based_classify_no_pending_command()
    test_rule_based_classify_query_fallback()
    test_rule_based_classify_reply_highest_priority()
    test_observability_nlu_path_has_three_logs()
    test_observability_llm_path_has_three_logs()
    test_observability_rule_path_has_three_logs()
    test_acknowledgment_words_bypass_nlu_and_llm()
    test_acknowledgment_passes_through_when_pending_question()
    test_question_words_bypass_nlu_routing()
    test_notify_task_completed_records_in_dialogue_history()
    test_notify_task_completed_caps_recent_completed_at_five()
    test_build_context_includes_recent_completed_tasks()
    test_classify_input_sends_recent_completed_to_llm()
    test_system_prompt_has_dialogue_context_awareness_section()

    print(f"\nAll 40 tests passed!")
