"""Tests for Adjutant — mock LLM, Kernel, WorldModel covering all 3 input paths."""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

import logging_system
from llm import LLMResponse, MockProvider
from models import CombatJobConfig, PlayerResponse, Task, TaskKind, TaskMessage, TaskMessageType, TaskStatus
from openra_api.models import Actor, Location
from models.enums import EngagementMode
from adjutant import (
    Adjutant, AdjutantConfig, AdjutantContext, ClassificationResult, InputType,
    CLASSIFICATION_SYSTEM_PROMPT,
    NotificationManager, format_notification, notification_to_text, notification_to_dict,
)
from adjutant.runtime_nlu import DirectNLUStep


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
        self.label = ""
        self.is_capability = False


class MockKernel:
    def __init__(self):
        self.created_tasks: list[dict] = []
        self.started_jobs: list[dict] = []
        self.submitted_responses: list[PlayerResponse] = []
        self._pending_questions: list[dict] = []
        self._tasks: list[MockTask] = []
        self._task_counter = 0
        self._job_counter = 0

    def create_task(self, raw_text, kind, priority, info_subscriptions=None, *, skip_agent=False):
        self._task_counter += 1
        task = MockTask(f"t_{self._task_counter}", raw_text)
        task.label = f"{self._task_counter:03d}"
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

    def cancel_task(self, task_id):
        self._tasks = [t for t in self._tasks if t.task_id != task_id]
        return True

    def is_direct_managed(self, task_id):
        return False

    def inject_player_message(self, task_id, text):
        target = next((t for t in self._tasks if t.task_id == task_id), None)
        if target is None:
            return False
        if not hasattr(target, "_injected_messages"):
            target._injected_messages = []
        target._injected_messages.append(text)
        return True

    @property
    def capability_task_id(self):
        cap = next((t for t in self._tasks if getattr(t, "is_capability", False)), None)
        return cap.task_id if cap else None

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
    def __init__(self):
        self.query_counts: dict[str, int] = {}
        self.state = SimpleNamespace(
            actors={
                401: Actor(actor_id=401, type="重坦", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                402: Actor(actor_id=402, type="重坦", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
            }
        )

    def world_summary(self):
        return {
            "economy": {"cash": 5000, "income": 200},
            "military": {"self_units": 15, "enemy_units": 8, "self_combat_value": 2500},
            "map": {"explored_pct": 0.45},
            "known_enemy": {"units_spotted": 8, "bases": 1},
            "timestamp": time.time(),
        }

    def query(self, query_type, params=None):
        self.query_counts[query_type] = self.query_counts.get(query_type, 0) + 1
        if query_type == "battlefield_snapshot":
            return {
                "summary": "我方15 / 敌方8，探索45.0%",
                "disposition": "advantage",
                "focus": "attack",
                "self_units": 15,
                "enemy_units": 8,
                "self_combat_value": 2500,
                "enemy_combat_value": 1200,
                "idle_self_units": 6,
                "self_combat_units": 6,
                "committed_combat_units": 2,
                "free_combat_units": 4,
                "low_power": False,
                "queue_blocked": False,
                "recommended_posture": "satisfy_requests",
                "threat_level": "medium",
                "threat_direction": "west",
                "base_under_attack": False,
                "base_health_summary": "stable",
                "has_production": True,
                "explored_pct": 0.45,
                "enemy_bases": 1,
                "enemy_spotted": 8,
                "frozen_enemy_count": 0,
                "pending_request_count": 2,
                "bootstrapping_request_count": 1,
                "reservation_count": 1,
                "stale": False,
                "timestamp": time.time(),
            }
        if query_type == "runtime_state":
            return {
                "active_tasks": {
                    "t_cap": {
                        "raw_text": "发展经济",
                        "label": "001",
                        "status": "running",
                        "is_capability": True,
                        "active_group_size": 0,
                    },
                    "t_recon": {
                        "raw_text": "探索地图",
                        "label": "002",
                        "status": "running",
                        "is_capability": False,
                        "active_group_size": 2,
                        "active_actor_ids": [401, 402],
                    },
                },
                "capability_status": {
                    "task_id": "t_cap",
                    "label": "001",
                    "status": "running",
                    "phase": "bootstrapping",
                    "blocker": "bootstrap_in_progress",
                    "active_job_types": ["EconomyExpert"],
                    "pending_request_count": 2,
                    "bootstrapping_request_count": 1,
                    "blocking_request_count": 1,
                    "recent_directives": ["发展经济", "优先补电"],
                },
                "unit_reservations": [{"reservation_id": "res_1"}],
                "timestamp": time.time(),
            }
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
        if query_type == "my_actors" and params == {"name": "工程师"}:
            return {
                "actors": [
                    {"actor_id": 601, "name": "工程师"},
                ],
                "timestamp": time.time(),
            }
        if query_type == "enemy_actors" and params == {"category": "building"}:
            return {
                "actors": [
                    {"actor_id": 902, "name": "dome", "display_name": "雷达站", "position": [1200, 260]},
                ],
                "timestamp": time.time(),
            }
        if query_type == "enemy_actors":
            return {
                "actors": [
                    {"actor_id": 901, "name": "harv", "display_name": "矿车", "position": [900, 100]},
                    {"actor_id": 902, "name": "dome", "display_name": "雷达站", "position": [1200, 260]},
                ],
                "timestamp": time.time(),
            }
        return {"data": [], "timestamp": time.time()}

    def compute_runtime_facts(self, task_id, include_buildable=False):
        assert task_id == "__adjutant__"
        return {
            "has_construction_yard": True,
            "mcv_count": 1,
            "mcv_idle": True,
            "power_plant_count": 1,
            "refinery_count": 1,
            "barracks_count": 1,
            "war_factory_count": 0,
            "radar_count": 1,
            "repair_facility_count": 0,
            "airfield_count": 0,
            "tech_center_count": 0,
            "harvester_count": 2,
            "buildable": {
                "Building": ["weap", "dome", "fix"],
                "Vehicle": ["ftrk", "harv"],
            },
            "info_experts": {
                "threat_level": "medium",
                "threat_direction": "west",
                "enemy_count": 6,
                "base_under_attack": False,
                "base_health_summary": "stable",
                "has_production": True,
            },
            "ready_queue_items": [
                {
                    "queue_type": "Building",
                    "unit_type": "powr",
                    "display_name": "发电厂",
                    "owner_actor_id": 42,
                }
            ],
        }

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


class BlockingGameAPI(MockGameAPI):
    def __init__(self, block_s: float = 0.3):
        super().__init__()
        self.block_s = block_s

    def deploy_units(self, actors):
        time.sleep(self.block_s)
        super().deploy_units(actors)


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


def test_runtime_nlu_routes_bare_unit_short_command_without_llm():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("步兵")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "EconomyExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 1
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["config"].unit_type == "e1"
    assert kernel.started_jobs[0]["config"].count == 1
    assert kernel.started_jobs[0]["config"].queue_type == "Infantry"
    print("  PASS: runtime_nlu_routes_bare_unit_short_command_without_llm")


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


def test_runtime_nlu_mine_does_not_block_event_loop_on_game_api():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    game_api = BlockingGameAPI(block_s=0.3)
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm, game_api=game_api)

    async def run():
        task = asyncio.create_task(adjutant.handle_player_input("让矿车去采矿"))
        start = time.perf_counter()
        await asyncio.sleep(0.05)
        loop_elapsed = time.perf_counter() - start
        result = await asyncio.wait_for(task, timeout=1.0)
        return loop_elapsed, result

    loop_elapsed, result = asyncio.run(run())

    assert loop_elapsed < 0.2
    assert result["ok"] is True
    assert game_api.deployed_units == [[301, 302]]
    print("  PASS: runtime_nlu_mine_does_not_block_event_loop_on_game_api")


def test_resolve_attack_step_prefers_explicit_visible_target_name():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    step = DirectNLUStep(
        intent="attack",
        expert_type="CombatExpert",
        config=CombatJobConfig(
            target_position=(0, 0),
            engagement_mode=EngagementMode.ASSAULT,
            unit_count=0,
        ),
        reason="nlu_attack",
        source_text="重坦集火敌方矿车",
    )

    match = adjutant._resolve_runtime_nlu_step(step)
    cfg = match.config
    assert cfg.target_actor_id == 901
    assert cfg.target_position == (900, 100)
    print("  PASS: resolve_attack_step_prefers_explicit_visible_target_name")


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


def test_stale_world_blocks_rule_routed_build_and_skips_llm():
    class StaleWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": True,
                "consecutive_failures": 9,
                "total_failures": 9,
                "last_error": "actors:COMMAND_EXECUTION_ERROR",
                "failure_threshold": 3,
                "timestamp": time.time(),
            }

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = StaleWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("建造兵营")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"
        assert "状态同步异常" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: stale_world_blocks_rule_routed_build_and_skips_llm")


def test_stale_world_blocks_query_and_skips_llm():
    class StaleWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": True,
                "consecutive_failures": 7,
                "total_failures": 7,
                "last_error": "economy:COMMAND_EXECUTION_ERROR",
                "failure_threshold": 3,
                "timestamp": time.time(),
            }

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = StaleWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"
        assert "暂时无法可靠回答" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: stale_world_blocks_query_and_skips_llm")


def test_stale_world_blocks_classified_command_without_task_creation():
    class StaleWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": True,
                "consecutive_failures": 5,
                "total_failures": 5,
                "last_error": "actors:COMMAND_EXECUTION_ERROR",
                "failure_threshold": 3,
                "timestamp": time.time(),
            }

    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.95}', model="mock"),
    ])
    kernel = MockKernel()
    wm = StaleWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理后进攻")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"
        assert "状态同步异常" in result["response_text"]

    asyncio.run(run())

    # NLU now catches "修理后进攻" as attack before LLM classifier runs
    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: stale_world_blocks_classified_command_without_task_creation")


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


def test_rule_routed_repair_skips_llm_and_locks_damaged_units():
    class RepairWorldModel(MockWorldModel):
        def compute_runtime_facts(self, task_id, include_buildable=False):
            facts = dict(super().compute_runtime_facts(task_id, include_buildable=include_buildable))
            facts["repair_facility_count"] = 1
            return facts

        def query(self, query_type, params=None):
            params = params or {}
            if query_type == "my_actors" and params == {"name": "重坦"}:
                return {
                    "actors": [
                        {"actor_id": 501, "name": "3TNK", "display_name": "重坦", "category": "vehicle", "hp": 60, "hp_max": 100},
                        {"actor_id": 502, "name": "3TNK", "display_name": "重坦", "category": "vehicle", "hp": 100, "hp_max": 100},
                    ],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = RepairWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理重坦")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["expert_type"] == "RepairExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["expert_type"] == "RepairExpert"
    assert kernel.started_jobs[0]["config"].actor_ids == [501]
    print("  PASS: rule_routed_repair_skips_llm_and_locks_damaged_units")


def test_repair_feedback_when_facility_missing():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理重坦")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_repair_missing_facility"
        assert "没有维修厂" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs == []
    print("  PASS: repair_feedback_when_facility_missing")


def test_rule_routed_occupy_skips_llm_and_targets_visible_building():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("用工程师占领雷达站")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["expert_type"] == "OccupyExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["expert_type"] == "OccupyExpert"
    assert kernel.started_jobs[0]["config"].actor_ids == [601]
    assert kernel.started_jobs[0]["config"].target_actor_id == 902
    print("  PASS: rule_routed_occupy_skips_llm_and_targets_visible_building")


def test_occupy_feedback_when_engineer_missing():
    class NoEngineerWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"name": "工程师"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = NoEngineerWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("占领雷达站")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_occupy_missing_engineer"
        assert "没有可用工程师" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs == []
    print("  PASS: occupy_feedback_when_engineer_missing")


def test_rule_routed_attack_skips_llm_and_targets_visible_enemy():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("集火雷达站")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["expert_type"] == "CombatExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["expert_type"] == "CombatExpert"
    config = kernel.started_jobs[0]["config"]
    assert config.target_actor_id == 902
    assert config.target_position == (1200, 260)
    assert config.engagement_mode == EngagementMode.ASSAULT
    print("  PASS: rule_routed_attack_skips_llm_and_targets_visible_enemy")


def test_attack_feedback_when_explicit_target_not_visible():
    class NoEnemyWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "enemy_actors":
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = NoEnemyWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("集火雷达站")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_attack_missing_target"
        assert "没有可见的雷达站目标" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs == []
    print("  PASS: attack_feedback_when_explicit_target_not_visible")


def test_unmatched_command_still_uses_llm_path():
    mock_llm = MockProvider(responses=[
        LLMResponse(text='{"type":"command","confidence":0.95}', model="mock"),
    ])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("帮我想个战术方案")
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


def test_build_context_includes_coordinator_snapshot_and_task_status_lines():
    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    recon_task = kernel.create_task("探索地图", "managed", 40)
    recon_task.task_id = "t_recon"
    recon_task.label = "002"
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=MockWorldModel())

    ctx = adjutant._build_context("继续发展")

    assert ctx.coordinator_snapshot["capability"]["pending_request_count"] == 2
    assert ctx.coordinator_snapshot["capability"]["phase"] == "bootstrapping"
    assert ctx.coordinator_snapshot["capability"]["blocker"] == "bootstrap_in_progress"
    assert ctx.coordinator_snapshot["capability"]["recent_directives"] == ["发展经济", "优先补电"]
    assert ctx.coordinator_snapshot["base_state"]["has_construction_yard"] is True
    assert ctx.coordinator_snapshot["info_experts"]["threat_level"] == "medium"
    assert ctx.coordinator_snapshot["task_overview"]["active_count"] == 2
    assert ctx.coordinator_snapshot["task_overview"]["running_count"] == 2
    assert ctx.coordinator_snapshot["task_overview"]["largest_group_label"] == "002"
    assert ctx.coordinator_snapshot["capability"]["ready_queue_items"][0]["display_name"] == "发电厂"
    assert any(alert["code"] == "queue_ready_items" for alert in ctx.coordinator_snapshot["alerts"])
    assert "队列里有待处理成品：发电厂" in ctx.coordinator_snapshot["status_line"]
    assert ctx.coordinator_hints["suggested_disposition"] == "merge"
    assert ctx.coordinator_hints["likely_target_label"] == "001"
    active_by_label = {task["label"]: task for task in ctx.active_tasks}
    assert active_by_label["001"]["is_capability"] is True
    assert active_by_label["001"]["phase"] == "bootstrapping"
    assert active_by_label["001"]["blocking_reason"] == "bootstrap_in_progress"
    assert "补前置中" in active_by_label["001"]["status_line"]
    assert "pending=2" in active_by_label["001"]["status_line"]
    assert active_by_label["002"]["active_group_size"] == 2
    assert active_by_label["002"]["unit_mix"] == ["重坦×2"]
    assert "group=2" in active_by_label["002"]["status_line"]
    assert "重坦×2" in active_by_label["002"]["status_line"]
    print("  PASS: build_context_includes_coordinator_snapshot_and_task_status_lines")


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


def test_classify_input_sends_coordinator_snapshot_to_llm():
    captured: list[dict] = []

    class CapturingProvider:
        async def chat(self, messages, **_kw):
            captured.extend(messages)
            return LLMResponse(text='{"type":"command","confidence":0.9}', model="mock")

    class LLMOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None

        def _try_rule_match(self, text):
            return None

        def _is_economy_command(self, text):
            return False

    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    recon_task = kernel.create_task("探索地图", "managed", 40)
    recon_task.task_id = "t_recon"
    recon_task.label = "002"
    adjutant = LLMOnlyAdjutant(llm=CapturingProvider(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        await adjutant.handle_player_input("继续发展")

    asyncio.run(run())
    user_msg = next(m for m in captured if m["role"] == "user")
    payload = json.loads(user_msg["content"])
    assert "coordinator_snapshot" in payload
    assert "coordinator_hints" in payload
    assert payload["coordinator_snapshot"]["capability"]["pending_request_count"] == 2
    assert payload["coordinator_snapshot"]["capability"]["phase"] == "bootstrapping"
    assert payload["coordinator_snapshot"]["info_experts"]["threat_direction"] == "west"
    assert payload["coordinator_hints"]["suggested_disposition"] == "merge"
    assert payload["active_tasks"][0]["status_line"]
    print("  PASS: classify_input_sends_coordinator_snapshot_to_llm")


def test_economy_command_merge_reports_capability_phase_and_blocker():
    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        return await adjutant.handle_player_input("发展经济")

    result = asyncio.run(run())
    assert result["merged"] is True
    assert "补齐前置" in result["response_text"]
    assert "待处理请求 2" in result["response_text"]
    assert "阻塞请求 1" in result["response_text"]
    print("  PASS: economy_command_merge_reports_capability_phase_and_blocker")


def test_economy_command_merge_reports_prerequisite_gap_blocker():
    class PrereqWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            result = super().query(query_type, params)
            if query_type == "runtime_state":
                result["capability_status"]["phase"] = "dispatch"
                result["capability_status"]["blocker"] = "missing_prerequisite"
                result["capability_status"]["prerequisite_gap_count"] = 2
                result["capability_status"]["dispatch_request_count"] = 2
            return result

    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=PrereqWorldModel())

    async def run():
        return await adjutant.handle_player_input("发展经济")

    result = asyncio.run(run())
    assert result["merged"] is True
    assert "待处理请求 2" in result["response_text"]
    assert "部分请求缺少前置建筑" in result["response_text"]
    print("  PASS: economy_command_merge_reports_prerequisite_gap_blocker")


def test_economy_command_merge_reports_fulfilling_and_reinforcement_counts():
    class FulfillingWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            result = super().query(query_type, params)
            if query_type == "runtime_state":
                result["capability_status"]["phase"] = "fulfilling"
                result["capability_status"]["blocker"] = ""
                result["capability_status"]["start_released_request_count"] = 1
                result["capability_status"]["reinforcement_request_count"] = 2
            return result

    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=FulfillingWorldModel())

    async def run():
        return await adjutant.handle_player_input("发展经济")

    result = asyncio.run(run())
    assert result["merged"] is True
    assert "已满足启动条件，正在补强" in result["response_text"]
    assert "已可启动 1" in result["response_text"]
    assert "增援请求 2" in result["response_text"]
    print("  PASS: economy_command_merge_reports_fulfilling_and_reinforcement_counts")


def test_economy_command_merge_deduplicates_same_directive():
    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        return await adjutant.handle_player_input("优先补电")

    result = asyncio.run(run())
    assert result["merged"] is True
    assert result["deduplicated"] is True
    assert "已在处理中" in result["response_text"]
    assert getattr(cap_task, "_injected_messages", []) == []
    print("  PASS: economy_command_merge_deduplicates_same_directive")


def test_battlefield_snapshot_tracks_disposition_and_focus():
    class PressureWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "battlefield_snapshot":
                return {}
            return super().query(query_type, params)

        def world_summary(self):
            summary = super().world_summary()
            summary["economy"]["low_power"] = True
            summary["economy"]["queue_blocked"] = True
            summary["military"]["self_units"] = 5
            summary["military"]["enemy_units"] = 14
            summary["military"]["self_combat_value"] = 900
            summary["military"]["enemy_combat_value"] = 2600
            summary["known_enemy"]["bases"] = 2
            summary["known_enemy"]["units_spotted"] = 10
            return summary

    adjutant = Adjutant(llm=MockProvider(), kernel=MockKernel(), world_model=PressureWorldModel())
    snapshot = adjutant._battlefield_snapshot()

    assert snapshot["disposition"] == "under_pressure"
    assert snapshot["focus"] == "defense"
    assert snapshot["queue_blocked"] is True
    assert "低电" in snapshot["summary"]
    print("  PASS: battlefield_snapshot_tracks_disposition_and_focus")


def test_query_context_includes_battlefield_snapshot():
    captured: list[list[dict[str, Any]]] = []

    class CapturingProvider:
        async def chat(self, messages, **_kwargs):
            captured.append(messages)
            if len(captured) == 1:
                return LLMResponse(text='{"type":"query","confidence":0.9}', model="mock")
            return LLMResponse(text="当前态势良好", model="mock")

    class LLMOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None

        def _try_rule_match(self, text):
            return None

    adjutant = LLMOnlyAdjutant(llm=CapturingProvider(), kernel=MockKernel(), world_model=MockWorldModel())

    async def run():
        await adjutant.handle_player_input("战况如何？")

    asyncio.run(run())

    user_msg = next(msg for msg in captured[0] if msg["role"] == "user")
    payload = json.loads(user_msg["content"])
    snapshot = payload["battlefield_snapshot"]
    assert snapshot["disposition"] == "advantage"
    assert snapshot["focus"] == "attack"
    assert snapshot["free_combat_units"] == 4
    assert snapshot["committed_combat_units"] == 2
    assert "我方15 / 敌方8" in snapshot["summary"]
    print("  PASS: query_context_includes_battlefield_snapshot")


def test_info_routes_to_best_active_task_without_creating_new_task():
    class InfoOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None

        def _try_rule_match(self, text):
            return None

        async def _classify_input(self, context):
            return ClassificationResult(input_type=InputType.INFO, confidence=0.95, raw_text=context.player_input)

    kernel = MockKernel()
    first = MockTask("t1", "探索敌方基地")
    first.label = "001"
    second = MockTask("t2", "发展经济")
    second.label = "002"
    kernel._tasks.extend([first, second])

    world_model = MockWorldModel()
    adjutant = InfoOnlyAdjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

    async def run():
        result = await adjutant.handle_player_input("左下角发现敌人基地，还有两辆坦克")
        assert result["type"] == "info"
        assert result["ok"] is True
        assert result["routing"] == "info_merge"
        assert result["task_id"] == "t1"

    asyncio.run(run())

    assert len(kernel.created_tasks) == 0
    assert getattr(first, "_injected_messages", []) == ["左下角发现敌人基地，还有两辆坦克"]
    assert world_model.query_counts.get("battlefield_snapshot") == 1
    print("  PASS: info_routes_to_best_active_task_without_creating_new_task")


def test_command_disposition_merge_injects_into_existing_task():
    class MergeOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None

        def _try_rule_match(self, text):
            return None

        async def _classify_input(self, context):
            return ClassificationResult(
                input_type=InputType.COMMAND,
                confidence=0.95,
                disposition="merge",
                raw_text=context.player_input,
            )

    kernel = MockKernel()
    task = MockTask("t1", "造5辆坦克")
    task.label = "001"
    kernel._tasks.append(task)

    world_model = MockWorldModel()
    adjutant = MergeOnlyAdjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

    async def run():
        result = await adjutant.handle_player_input("再多造两辆坦克")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "command_merge"
        assert result["existing_task_id"] == "t1"

    asyncio.run(run())

    assert len(kernel.created_tasks) == 0
    assert getattr(task, "_injected_messages", []) == ["再多造两辆坦克"]
    assert world_model.query_counts.get("battlefield_snapshot") == 1
    print("  PASS: command_disposition_merge_injects_into_existing_task")


def test_command_without_disposition_uses_coordinator_hints():
    class HintOnlyAdjutant(Adjutant):
        def _try_runtime_nlu(self, text):
            return None

        def _try_rule_match(self, text):
            return None

        async def _classify_input(self, context):
            return ClassificationResult(
                input_type=InputType.COMMAND,
                confidence=0.75,
                raw_text=context.player_input,
            )

    kernel = MockKernel()
    task = MockTask("t1", "探索地图")
    task.label = "001"
    kernel._tasks.append(task)

    world_model = MockWorldModel()
    adjutant = HintOnlyAdjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

    async def run():
        result = await adjutant.handle_player_input("继续探索左下角")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "command_merge"
        assert result["target_task_id"] == "001"

    asyncio.run(run())

    assert len(kernel.created_tasks) == 0
    assert getattr(task, "_injected_messages", []) == ["继续探索左下角"]
    assert world_model.query_counts.get("battlefield_snapshot") == 1
    print("  PASS: command_without_disposition_uses_coordinator_hints")


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
    test_runtime_nlu_mine_does_not_block_event_loop_on_game_api()
    test_runtime_nlu_stop_attack_uses_game_api_without_llm()
    test_resolve_attack_step_prefers_explicit_visible_target_name()
    test_nlu_routed_deploy_uses_mcv_query()
    test_nlu_routed_expand_mcv_uses_deploy_path()
    test_deploy_without_mcv_but_with_construction_yard_returns_immediate_feedback()
    test_deploy_without_mcv_returns_missing_feedback()
    test_deploy_feedback_refuses_stale_world_assertions()
    test_stale_world_blocks_rule_routed_build_and_skips_llm()
    test_stale_world_blocks_query_and_skips_llm()
    test_stale_world_blocks_classified_command_without_task_creation()
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
    test_build_context_includes_coordinator_snapshot_and_task_status_lines()
    test_classify_input_sends_recent_completed_to_llm()
    test_classify_input_sends_coordinator_snapshot_to_llm()
    test_economy_command_merge_reports_capability_phase_and_blocker()
    test_economy_command_merge_reports_prerequisite_gap_blocker()
    test_economy_command_merge_reports_fulfilling_and_reinforcement_counts()
    test_economy_command_merge_deduplicates_same_directive()
    test_battlefield_snapshot_tracks_disposition_and_focus()
    test_query_context_includes_battlefield_snapshot()
    test_info_routes_to_best_active_task_without_creating_new_task()
    test_command_disposition_merge_injects_into_existing_task()
    test_command_without_disposition_uses_coordinator_hints()
    test_system_prompt_has_dialogue_context_awareness_section()

    print(f"\nAll 54 tests passed!")
