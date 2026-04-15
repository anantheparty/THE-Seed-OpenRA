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
from runtime_views import BattlefieldSnapshot, CapabilityStatusSnapshot, RuntimeStateSnapshot
from adjutant import (
    Adjutant, AdjutantConfig, AdjutantContext, ClassificationResult, InputType,
    CLASSIFICATION_SYSTEM_PROMPT,
    NotificationManager, format_notification, notification_to_text, notification_to_dict,
)
from adjutant.runtime_nlu import DirectNLUStep
from tests.schema_assertions import assert_mapping_superset, assert_object_surface


# --- Mocks ---

_WORLD_SUMMARY_REQUIRED_KEYS = {"economy", "military", "map", "known_enemy", "timestamp"}
_WORLD_SUMMARY_ECONOMY_KEYS = {
    "cash",
    "income",
    "low_power",
    "queue_blocked",
    "queue_blocked_reason",
    "queue_blocked_queue_types",
    "queue_blocked_items",
    "disabled_structure_count",
    "powered_down_structure_count",
    "low_power_disabled_structure_count",
    "power_outage_structure_count",
    "disabled_structures",
}
_WORLD_SUMMARY_MILITARY_KEYS = {
    "self_units",
    "enemy_units",
    "self_combat_value",
    "enemy_combat_value",
    "idle_self_units",
}
_WORLD_SUMMARY_MAP_KEYS = {"explored_pct"}
_WORLD_SUMMARY_ENEMY_KEYS = {"units_spotted", "bases", "frozen_count"}
_BATTLEFIELD_SNAPSHOT_KEYS = set(BattlefieldSnapshot().to_dict().keys())
_RUNTIME_STATE_KEYS = set(RuntimeStateSnapshot().to_dict().keys())
_CAPABILITY_STATUS_KEYS = set(CapabilityStatusSnapshot().to_dict().keys())
_KERNEL_REQUIRED_CALLABLES = {
    "create_task",
    "start_job",
    "submit_player_response",
    "list_pending_questions",
    "list_task_messages",
    "list_tasks",
    "jobs_for_task",
    "cancel_task",
    "is_direct_managed",
    "inject_player_message",
    "runtime_state",
}
_KERNEL_REQUIRED_ATTRS = {"capability_task_id"}
_WORLD_MODEL_REQUIRED_CALLABLES = {"world_summary", "query", "refresh_health"}

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
        self._task_messages: list[TaskMessage] = []
        self._runtime_state_override: dict[str, Any] | None = None
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

    def list_task_messages(self, task_id=None):
        messages = list(self._task_messages)
        if task_id is None:
            return messages
        return [message for message in messages if message.task_id == task_id]

    def jobs_for_task(self, task_id):
        jobs = []
        for item in self.started_jobs:
            if item["task_id"] != task_id:
                continue
            jobs.append(
                SimpleNamespace(
                    job_id=item["job_id"],
                    task_id=item["task_id"],
                    expert_type=item["expert_type"],
                    config=item["config"],
                )
            )
        return jobs

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
        self._task_messages.append(
            TaskMessage(
                message_id=f"m_{len(self._task_messages) + 1}",
                task_id=task_id,
                type=TaskMessageType.TASK_INFO,
                content=text,
            )
        )
        return True

    def runtime_state(self):
        if isinstance(self._runtime_state_override, dict):
            return dict(self._runtime_state_override)
        capability = next((t for t in self._tasks if getattr(t, "is_capability", False)), None)
        non_capability = next((t for t in self._tasks if not getattr(t, "is_capability", False)), None)
        capability_status = CapabilityStatusSnapshot(
            task_id=capability.task_id if capability else "",
            task_label=getattr(capability, "label", "") if capability else "",
            status=getattr(getattr(capability, "status", None), "value", "") if capability else "",
            phase="bootstrapping" if capability else "",
            blocker="bootstrap_in_progress" if capability else "",
            active_job_types=["EconomyExpert"] if capability else [],
            pending_request_count=2 if capability else 0,
            blocking_request_count=1 if capability else 0,
            bootstrapping_request_count=1 if capability else 0,
            recent_directives=["发展经济", "优先补电"] if capability else [],
        )
        active_tasks = {}
        for index, task in enumerate(self._tasks):
            active_tasks[task.task_id] = {
                "raw_text": task.raw_text,
                "label": task.label,
                "status": getattr(getattr(task, "status", None), "value", ""),
                "is_capability": bool(getattr(task, "is_capability", False)),
                "active_group_size": 0 if getattr(task, "is_capability", False) else 2,
            }
            if not getattr(task, "is_capability", False) and index == 1:
                active_tasks[task.task_id]["active_actor_ids"] = [401, 402]
        return RuntimeStateSnapshot(
            active_tasks=active_tasks,
            active_jobs={},
            resource_bindings={},
            constraints=[],
            capability_status=capability_status,
            unit_reservations=[{"reservation_id": "res_1"}] if capability or non_capability else [],
            timestamp=time.time(),
        ).to_dict()

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
        summary = {
            "economy": {"cash": 5000, "income": 200},
            "military": {"self_units": 15, "enemy_units": 8, "self_combat_value": 2500},
            "map": {"explored_pct": 0.45},
            "known_enemy": {"units_spotted": 8, "bases": 1},
            "timestamp": time.time(),
        }
        summary["economy"].update(
            {
                "low_power": False,
                "queue_blocked": False,
                "queue_blocked_reason": "",
                "queue_blocked_queue_types": [],
                "queue_blocked_items": [],
                "disabled_structure_count": 0,
                "powered_down_structure_count": 0,
                "low_power_disabled_structure_count": 0,
                "power_outage_structure_count": 0,
                "disabled_structures": [],
            }
        )
        summary["military"].update(
            {
                "enemy_combat_value": 1200,
                "idle_self_units": 6,
            }
        )
        summary["known_enemy"].update({"frozen_count": 0})
        assert_mapping_superset(summary, _WORLD_SUMMARY_REQUIRED_KEYS, label="MockWorldModel.world_summary")
        assert_mapping_superset(summary["economy"], _WORLD_SUMMARY_ECONOMY_KEYS, label="MockWorldModel.world_summary.economy")
        assert_mapping_superset(summary["military"], _WORLD_SUMMARY_MILITARY_KEYS, label="MockWorldModel.world_summary.military")
        assert_mapping_superset(summary["map"], _WORLD_SUMMARY_MAP_KEYS, label="MockWorldModel.world_summary.map")
        assert_mapping_superset(summary["known_enemy"], _WORLD_SUMMARY_ENEMY_KEYS, label="MockWorldModel.world_summary.known_enemy")
        return summary

    def query(self, query_type, params=None):
        self.query_counts[query_type] = self.query_counts.get(query_type, 0) + 1
        if query_type == "battlefield_snapshot":
            capability_status = CapabilityStatusSnapshot(
                task_id="t_cap",
                task_label="001",
                status="running",
                phase="bootstrapping",
                blocker="bootstrap_in_progress",
                active_job_types=["EconomyExpert"],
                pending_request_count=2,
                bootstrapping_request_count=1,
                blocking_request_count=1,
                recent_directives=["发展经济", "优先补电"],
            ).to_dict()
            snapshot = {
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
                "queue_blocked_reason": "",
                "queue_blocked_queue_types": [],
                "queue_blocked_items": [],
                "disabled_structure_count": 0,
                "powered_down_structure_count": 0,
                "low_power_disabled_structure_count": 0,
                "power_outage_structure_count": 0,
                "disabled_structures": [],
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
                "unit_pipeline_preview": "重坦 × 2 · 待分发",
                "stale": False,
                "capability_status": capability_status,
                "timestamp": time.time(),
            }
            assert_mapping_superset(snapshot, _BATTLEFIELD_SNAPSHOT_KEYS, label="MockWorldModel.battlefield_snapshot")
            assert_mapping_superset(
                snapshot["capability_status"],
                _CAPABILITY_STATUS_KEYS,
                label="MockWorldModel.battlefield_snapshot.capability_status",
            )
            return snapshot
        if query_type == "runtime_state":
            runtime_state = {
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
                "active_jobs": {},
                "resource_bindings": {},
                "constraints": [],
                "unfulfilled_requests": [],
                "capability_status": CapabilityStatusSnapshot(
                    task_id="t_cap",
                    task_label="001",
                    status="running",
                    phase="bootstrapping",
                    blocker="bootstrap_in_progress",
                    active_job_types=["EconomyExpert"],
                    pending_request_count=2,
                    bootstrapping_request_count=1,
                    blocking_request_count=1,
                    recent_directives=["发展经济", "优先补电"],
                ).to_dict(),
                "unit_reservations": [{"reservation_id": "res_1"}],
                "timestamp": time.time(),
            }
            assert_mapping_superset(runtime_state, _RUNTIME_STATE_KEYS, label="MockWorldModel.runtime_state")
            assert_mapping_superset(
                runtime_state["capability_status"],
                _CAPABILITY_STATUS_KEYS,
                label="MockWorldModel.runtime_state.capability_status",
            )
            return runtime_state
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
        if query_type == "my_actors" and params == {"type": "建造厂"}:
            return {
                "actors": [
                    {"actor_id": 130, "type": "建造厂", "position": [520, 420]},
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

def test_mock_adjutant_fixtures_match_minimum_protocol_surface():
    kernel = MockKernel()
    world_model = MockWorldModel()

    assert_object_surface(
        kernel,
        required_callables=_KERNEL_REQUIRED_CALLABLES,
        required_attrs=_KERNEL_REQUIRED_ATTRS,
        label="MockKernel",
    )
    assert_object_surface(
        world_model,
        required_callables=_WORLD_MODEL_REQUIRED_CALLABLES,
        label="MockWorldModel",
    )

    runtime_state = kernel.runtime_state()
    assert_mapping_superset(runtime_state, _RUNTIME_STATE_KEYS, label="MockKernel.runtime_state")
    assert_mapping_superset(
        runtime_state["capability_status"],
        _CAPABILITY_STATUS_KEYS,
        label="MockKernel.runtime_state.capability_status",
    )

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


def test_runtime_nlu_production_merges_to_capability_when_available():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    cap = MockTask("t_cap", "发展经济")
    cap.label = "001"
    cap.is_capability = True
    kernel._tasks.append(cap)
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("步兵3")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "EconomyExpert"
        assert result["existing_task_id"] == "t_cap"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 0
    assert len(kernel.started_jobs) == 0
    assert getattr(cap, "_injected_messages", []) == ["步兵3"]
    print("  PASS: runtime_nlu_production_merges_to_capability_when_available")


def test_runtime_nlu_build_merges_to_capability_when_available():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    cap = MockTask("t_cap", "发展经济")
    cap.label = "001"
    cap.is_capability = True
    kernel._tasks.append(cap)
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("兵营")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["merged"] is True
        assert result["existing_task_id"] == "t_cap"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 0
    assert len(kernel.started_jobs) == 0
    assert getattr(cap, "_injected_messages", []) == ["兵营"]
    print("  PASS: runtime_nlu_build_merges_to_capability_when_available")


def test_runtime_nlu_composite_sequence_merges_all_steps_to_capability_when_available():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    cap = MockTask("t_cap", "发展经济")
    cap.label = "001"
    cap.is_capability = True
    kernel._tasks.append(cap)
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("建造电厂兵营，然后建造五个步兵")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["task_id"] == "t_cap"
        assert result["step_count"] == 3

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.created_tasks) == 0
    assert len(kernel.started_jobs) == 0
    injected = getattr(cap, "_injected_messages", [])
    assert len(injected) == 3
    assert any("电厂" in message for message in injected)
    assert any("兵营" in message for message in injected)
    assert any("步兵" in message for message in injected)
    print("  PASS: runtime_nlu_composite_sequence_merges_all_steps_to_capability_when_available")


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

        def compute_runtime_facts(self, task_id, include_buildable=False):
            facts = super().compute_runtime_facts(task_id, include_buildable=include_buildable)
            facts["mcv_count"] = 0
            facts["has_construction_yard"] = True
            return facts

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

        def compute_runtime_facts(self, task_id, include_buildable=False):
            facts = super().compute_runtime_facts(task_id, include_buildable=include_buildable)
            facts["mcv_count"] = 0
            facts["has_construction_yard"] = False
            return facts

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


def test_deploy_feedback_uses_runtime_facts_for_construction_yard_truth():
    class RuntimeFactMismatchWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [{"actor_id": 130, "type": "建造厂"}], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 0, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = RuntimeFactMismatchWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_deploy_missing_mcv"
        assert "没有可部署的基地车" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_uses_runtime_facts_for_construction_yard_truth")


def test_deploy_feedback_refuses_ambiguous_mcv_truth():
    class AmbiguousDeployWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 1, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = AmbiguousDeployWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "deploy_truth_ambiguous"
        assert "状态同步中" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_ambiguous_mcv_truth")


def test_deploy_feedback_refuses_malformed_mcv_query_payload():
    class MalformedMcvWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {
                    "actors": [{"actor_id": "ghost", "category": "mcv", "position": [500, 400]}],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 1, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MalformedMcvWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "deploy_truth_ambiguous"
        assert "状态同步中" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_malformed_mcv_query_payload")


def test_deploy_feedback_ignores_untrusted_construction_yard_payload():
    class UntrustedConstructionYardWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {
                    "actors": [{"actor_id": "stale-yard", "type": "建造厂", "position": [520, 420]}],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 0}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = UntrustedConstructionYardWorldModel()
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
    print("  PASS: deploy_feedback_ignores_untrusted_construction_yard_payload")


def test_deploy_feedback_ignores_dead_construction_yard_actor_payload():
    class DeadConstructionYardWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params is None:
                return {
                    "actors": [
                        {"actor_id": 130, "type": "建造厂", "is_alive": False, "position": [520, 420]},
                    ],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {
                    "actors": [
                        {"actor_id": 130, "type": "建造厂", "is_alive": False, "position": [520, 420]},
                    ],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 0, "has_construction_yard": True}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = DeadConstructionYardWorldModel()
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
    print("  PASS: deploy_feedback_ignores_dead_construction_yard_actor_payload")


def test_deploy_feedback_refuses_unsynced_world_truth():
    class UnsyncedWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": False,
                "consecutive_failures": 0,
                "total_failures": 0,
                "last_error": None,
                "failure_threshold": 3,
                "timestamp": 0.0,
            }

        def query(self, query_type, params=None):
            if query_type == "my_actors":
                return {
                    "actors": [{"actor_id": 130, "type": "建造厂", "display_name": "建造厂", "category": "building"}],
                    "timestamp": 0.0,
                }
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = UnsyncedWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "deploy_truth_ambiguous"
        assert "状态同步中" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_unsynced_world_truth")


def test_deploy_feedback_uses_atomic_actor_snapshot_for_short_circuit():
    class SplitDeployTruthWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and not params:
                return {
                    "actors": [
                        {
                            "actor_id": 99,
                            "type": "mcv",
                            "display_name": "基地车",
                            "category": "mcv",
                            "position": [500, 400],
                        }
                    ],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {
                    "actors": [{"actor_id": 130, "type": "建造厂", "display_name": "建造厂", "category": "building"}],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 1, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = SplitDeployTruthWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "DeployExpert"
        assert result["nlu_route_intent"] == "deploy_mcv"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].actor_id == 99
    print("  PASS: deploy_feedback_uses_atomic_actor_snapshot_for_short_circuit")


def test_deploy_feedback_falls_back_to_targeted_queries_when_full_snapshot_missing():
    class TargetedOnlyDeployTruthWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and not params:
                return {"data": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {
                    "actors": [
                        {
                            "actor_id": 99,
                            "type": "mcv",
                            "display_name": "基地车",
                            "category": "mcv",
                            "position": [500, 400],
                        }
                    ],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 1, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = TargetedOnlyDeployTruthWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "nlu"
        assert result["expert_type"] == "DeployExpert"
        assert result["nlu_route_intent"] == "deploy_mcv"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["config"].actor_id == 99
    print("  PASS: deploy_feedback_falls_back_to_targeted_queries_when_full_snapshot_missing")


def test_deploy_feedback_treats_sentence_final_particles_as_query():
    class AlreadyDeployedWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {"actors": [], "timestamp": time.time()}
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [{"actor_id": 130, "type": "建造厂"}], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, include_buildable=False):
            facts = super().compute_runtime_facts(task_id, include_buildable=include_buildable)
            facts["mcv_count"] = 0
            facts["has_construction_yard"] = True
            return facts

    adjutant = Adjutant(llm=MockProvider(responses=[]), kernel=MockKernel(), world_model=AlreadyDeployedWorldModel())

    assert adjutant._maybe_handle_deploy_feedback("基地车展开了吗") is None
    assert adjutant._try_rule_match("基地车展开了吗") is None
    print("  PASS: deploy_feedback_treats_sentence_final_particles_as_query")


def test_deploy_feedback_refuses_query_actor_mcv_when_runtime_facts_disagree():
    class GhostMcvWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {
                    "actors": [{"actor_id": 99, "type": "基地车", "position": [10, 10]}],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 0, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = GhostMcvWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "deploy_truth_ambiguous"
        assert "状态同步中" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_query_actor_mcv_when_runtime_facts_disagree")


def test_deploy_feedback_refuses_dead_mcv_actor_payload():
    class DeadMcvWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "my_actors" and params is None:
                return {
                    "actors": [
                        {
                            "actor_id": 99,
                            "type": "基地车",
                            "display_name": "基地车",
                            "category": "mcv",
                            "is_alive": False,
                            "position": [10, 10],
                        }
                    ],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"category": "mcv"}:
                return {
                    "actors": [
                        {
                            "actor_id": 99,
                            "type": "基地车",
                            "display_name": "基地车",
                            "category": "mcv",
                            "is_alive": False,
                            "position": [10, 10],
                        }
                    ],
                    "timestamp": time.time(),
                }
            if query_type == "my_actors" and params == {"type": "建造厂"}:
                return {"actors": [], "timestamp": time.time()}
            return super().query(query_type, params)

        def compute_runtime_facts(self, task_id, *, include_buildable=False):
            del task_id, include_buildable
            return {"mcv_count": 1, "has_construction_yard": False}

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = DeadMcvWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("展开基地车")
        assert result["type"] == "command"
        assert result["ok"] is False
        assert result["routing"] == "rule"
        assert result["reason"] == "deploy_truth_ambiguous"
        assert "状态同步中" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: deploy_feedback_refuses_dead_mcv_actor_payload")


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
        assert "连续失败 9/3" in result["response_text"]
        assert "actors:COMMAND_EXECUTION_ERROR" in result["response_text"]
        assert result["world_sync_failures"] == 9
        assert result["world_sync_failure_threshold"] == 3
        assert result["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"

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
        assert "连续失败 7/3" in result["response_text"]
        assert "economy:COMMAND_EXECUTION_ERROR" in result["response_text"]
        assert result["world_sync_failures"] == 7
        assert result["world_sync_failure_threshold"] == 3
        assert result["world_sync_error"] == "economy:COMMAND_EXECUTION_ERROR"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: stale_world_blocks_query_and_skips_llm")


def test_stale_world_guard_uses_last_refresh_error_fallback() -> None:
    class StaleWorldModel(MockWorldModel):
        def refresh_health(self):
            return {
                "stale": True,
                "consecutive_failures": 4,
                "total_failures": 4,
                "last_refresh_error": "actors:COMMAND_EXECUTION_ERROR",
                "failure_threshold": 3,
                "timestamp": time.time(),
            }

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = StaleWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("现在战况如何？")
        assert result["type"] == "query"
        assert result["ok"] is False
        assert result["routing"] == "stale_guard"
        assert result["reason"] == "world_sync_stale"
        assert "连续失败 4/3" in result["response_text"]
        assert "actors:COMMAND_EXECUTION_ERROR" in result["response_text"]
        assert result["world_sync_failures"] == 4
        assert result["world_sync_failure_threshold"] == 3
        assert result["world_sync_error"] == "actors:COMMAND_EXECUTION_ERROR"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.created_tasks == []
    assert kernel.started_jobs == []
    print("  PASS: stale_world_guard_uses_last_refresh_error_fallback")


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


def test_building_repair_does_not_require_repair_facility():
    class BuildingRepairWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            params = params or {}
            if query_type == "my_actors" and params == {"name": "电厂"}:
                return {
                    "actors": [
                        {
                            "actor_id": 601,
                            "name": "POWR",
                            "display_name": "电厂",
                            "category": "building",
                            "hp": 70,
                            "hp_max": 100,
                        }
                    ],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = BuildingRepairWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理电厂")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["expert_type"] == "RepairExpert"

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs[0]["expert_type"] == "RepairExpert"
    assert kernel.started_jobs[0]["config"].actor_ids == [601]
    print("  PASS: building_repair_does_not_require_repair_facility")


def test_building_repair_feedback_prefers_no_damaged_target_over_missing_facility():
    class HealthyBuildingWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            params = params or {}
            if query_type == "my_actors" and params == {"name": "电厂"}:
                return {
                    "actors": [
                        {
                            "actor_id": 602,
                            "name": "POWR",
                            "display_name": "电厂",
                            "category": "building",
                            "hp": 100,
                            "hp_max": 100,
                        }
                    ],
                    "timestamp": time.time(),
                }
            return super().query(query_type, params)

    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = HealthyBuildingWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("修理电厂")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "rule"
        assert result["reason"] == "rule_repair_no_damaged_target"
        assert "没有需要回修的受损发电厂" in result["response_text"]

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert kernel.started_jobs == []
    print("  PASS: building_repair_feedback_prefers_no_damaged_target_over_missing_facility")


def test_build_repair_facility_not_misrouted_as_repair_feedback():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("建造修理厂")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["expert_type"] == "EconomyExpert"
        assert result["routing"] in {"nlu", "rule", "capability_merge"}

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.started_jobs) == 1
    assert kernel.started_jobs[0]["expert_type"] == "EconomyExpert"
    assert kernel.started_jobs[0]["config"].unit_type == "fix"
    assert kernel.started_jobs[0]["config"].queue_type == "Building"
    print("  PASS: build_repair_facility_not_misrouted_as_repair_feedback")


def test_repair_facility_phrase_merges_to_capability_instead_of_repair_shortcircuit():
    mock_llm = MockProvider(responses=[])
    kernel = MockKernel()
    cap = MockTask("t_cap", "发展经济")
    cap.label = "001"
    cap.is_capability = True
    kernel._tasks.append(cap)
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("科技中心和修理厂")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["merged"] is True
        assert result["existing_task_id"] == "t_cap"
        assert "没有维修厂" not in result.get("response_text", "")

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.started_jobs) == 0
    assert getattr(cap, "_injected_messages", []) == ["科技中心和修理厂"]
    print("  PASS: repair_facility_phrase_merges_to_capability_instead_of_repair_shortcircuit")


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


def test_explicit_multi_reply_routes_multiple_pending_questions_without_llm():
    """Explicitly segmented replies should answer multiple pending questions in priority order."""
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续", "放弃"], priority=60)

    mock_llm = MockProvider([])
    wm = MockWorldModel()
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=wm)

    async def run():
        result = await adjutant.handle_player_input("放弃；否")
        assert result["type"] == "reply"
        assert result["ok"] is True
        assert result["status"] == "delivered_multi"
        assert result["answered_count"] == 2
        assert result["question_count"] == 2

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.submitted_responses) == 2
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].task_id == "t1"
    assert kernel.submitted_responses[0].answer == "放弃"
    assert kernel.submitted_responses[1].message_id == "msg_low"
    assert kernel.submitted_responses[1].task_id == "t2"
    assert kernel.submitted_responses[1].answer == "否"
    print("  PASS: explicit_multi_reply_routes_multiple_pending_questions_without_llm")


def test_collect_multi_reply_matches_rejects_segment_count_overflow():
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续", "放弃"], priority=60)
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)

    adjutant = Adjutant(llm=MockProvider([]), kernel=kernel, world_model=MockWorldModel())

    matches = adjutant._collect_multi_reply_matches("放弃；否；继续", kernel.list_pending_questions())

    assert matches == []
    print("  PASS: collect_multi_reply_matches_rejects_segment_count_overflow")


def test_collect_multi_reply_matches_rejects_unmatched_segment():
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续进攻", "放弃"], priority=60)
    kernel.add_pending_question("msg_low", "t2", "要改变方向吗？", ["是", "否"], priority=40)

    adjutant = Adjutant(llm=MockProvider([]), kernel=kernel, world_model=MockWorldModel())

    matches = adjutant._collect_multi_reply_matches("你好；否", kernel.list_pending_questions())

    assert matches == []
    print("  PASS: collect_multi_reply_matches_rejects_unmatched_segment")


def test_explicit_multi_reply_uses_unique_synonym_bridge_without_llm():
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "是否切换路线？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续进攻", "放弃"], priority=60)

    mock_llm = MockProvider([])
    adjutant = Adjutant(llm=mock_llm, kernel=kernel, world_model=MockWorldModel())

    async def run():
        result = await adjutant.handle_player_input("继续；否")
        assert result["type"] == "reply"
        assert result["ok"] is True
        assert result["status"] == "delivered_multi"
        assert result["answered_count"] == 2

    asyncio.run(run())

    assert len(mock_llm.call_log) == 0
    assert len(kernel.submitted_responses) == 2
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].answer == "继续进攻"
    assert kernel.submitted_responses[1].message_id == "msg_low"
    assert kernel.submitted_responses[1].answer == "否"
    print("  PASS: explicit_multi_reply_uses_unique_synonym_bridge_without_llm")


def test_handle_reply_without_target_uses_multi_reply_before_priority_fallback():
    kernel = MockKernel()
    kernel._tasks.append(MockTask("t1", "进攻"))
    kernel._tasks.append(MockTask("t2", "侦察"))
    kernel.add_pending_question("msg_low", "t2", "是否切换路线？", ["是", "否"], priority=40)
    kernel.add_pending_question("msg_high", "t1", "继续还是放弃？", ["继续进攻", "放弃"], priority=60)

    adjutant = Adjutant(llm=MockProvider([]), kernel=kernel, world_model=MockWorldModel())
    classification = ClassificationResult(
        input_type=InputType.REPLY,
        confidence=0.9,
        raw_text="继续；否",
    )

    async def run():
        result = await adjutant._handle_reply(classification)
        assert result["type"] == "reply"
        assert result["ok"] is True
        assert result["status"] == "delivered_multi"
        assert result["answered_count"] == 2

    asyncio.run(run())

    assert len(kernel.submitted_responses) == 2
    assert kernel.submitted_responses[0].message_id == "msg_high"
    assert kernel.submitted_responses[0].answer == "继续进攻"
    assert kernel.submitted_responses[1].message_id == "msg_low"
    assert kernel.submitted_responses[1].answer == "否"
    print("  PASS: handle_reply_without_target_uses_multi_reply_before_priority_fallback")


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
    """Dialogue history is recorded and hard-capped to max_dialogue_history."""
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
        assert len(adjutant._dialogue_history) == 3
        assert [item["content"] for item in adjutant._dialogue_history] == [
            "收到指令，已创建任务 t_1",
            "第二条",
            "收到指令，已创建任务 t_2",
        ]

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


def test_notification_manager_history_is_hard_capped():
    """NotificationManager should retain only the most recent delivered notifications."""
    class MockNotifKernel:
        def __init__(self):
            self._notifications: list[dict] = []

        def list_player_notifications(self):
            return list(self._notifications)

        def add(self, idx: int):
            self._notifications.append(
                {"type": "FRONTLINE_WEAK", "content": f"弱-{idx}", "data": {}, "timestamp": float(idx)}
            )

    kernel = MockNotifKernel()
    for idx in range(5):
        kernel.add(idx)

    manager = NotificationManager(kernel=kernel, sink=None, max_history=3)

    async def run():
        result = await manager.poll_and_push()
        assert len(result) == 5
        assert [item.content for item in manager.history] == ["弱-2", "弱-3", "弱-4"]

    asyncio.run(run())
    print("  PASS: notification_manager_history_is_hard_capped")


def test_notification_manager_retry_does_not_duplicate_history():
    """Failed sink retries should not append duplicate entries into delivered history."""
    pushed: list[dict] = []
    fail_once = {"done": False}

    class MockNotifKernel:
        def list_player_notifications(self):
            return [{"type": "FRONTLINE_WEAK", "content": "弱", "data": {}, "timestamp": 100.0}]

    async def sink(notification):
        if not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("temporary failure")
        pushed.append(notification)

    manager = NotificationManager(kernel=MockNotifKernel(), sink=sink, max_history=5)

    async def run():
        first = await manager.poll_and_push()
        assert len(first) == 1
        assert pushed == []
        assert manager.history == []

        second = await manager.poll_and_push()
        assert len(second) == 1
        assert len(pushed) == 1
        assert len(manager.history) == 1
        assert manager.history[0].content == "弱"

    asyncio.run(run())
    print("  PASS: notification_manager_retry_does_not_duplicate_history")


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


def test_record_dialogue_caps_storage_at_max_history():
    """_dialogue_history itself should remain hard-bounded by max_dialogue_history."""
    adjutant = Adjutant(
        llm=MockProvider(),
        kernel=MockKernel(),
        world_model=MockWorldModel(),
        config=AdjutantConfig(max_dialogue_history=3),
    )

    for idx in range(7):
        adjutant._record_dialogue("player", f"msg-{idx}")

    assert len(adjutant._dialogue_history) == 3
    assert [item["content"] for item in adjutant._dialogue_history] == ["msg-4", "msg-5", "msg-6"]
    print("  PASS: record_dialogue_caps_storage_at_max_history")


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
    assert any("队列里有待处理成品：发电厂" in str(alert.get("text", "")) for alert in ctx.coordinator_snapshot["alerts"])
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


def test_build_context_surfaces_unit_pipeline_focus_in_coordinator_alerts():
    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    wait_task = kernel.create_task("前线补坦克", "managed", 60)
    wait_task.task_id = "t_wait"
    wait_task.label = "002"
    kernel._runtime_state_override = RuntimeStateSnapshot(
        active_tasks={
            "t_cap": {
                "raw_text": "发展经济",
                "label": "001",
                "status": "running",
                "is_capability": True,
                "active_group_size": 0,
            },
            "t_wait": {
                "raw_text": "前线补坦克",
                "label": "002",
                "status": "running",
                "is_capability": False,
                "active_group_size": 0,
            },
        },
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[
            {
                "request_id": "req_1",
                "reservation_id": "res_1",
                "task_id": "t_wait",
                "task_label": "002",
                "unit_type": "3tnk",
                "count": 2,
                "fulfilled": 0,
                "reason": "missing_prerequisite",
                "prerequisites": ["weap"],
            }
        ],
        capability_status=CapabilityStatusSnapshot(
            task_id="t_cap",
            task_label="001",
            status="running",
            phase="dispatch",
            blocker="missing_prerequisite",
            active_job_types=["EconomyExpert"],
            pending_request_count=1,
            dispatch_request_count=1,
            blocking_request_count=1,
            prerequisite_gap_count=1,
            recent_directives=["发展经济"],
        ),
        unit_reservations=[
            {
                "reservation_id": "res_1",
                "request_id": "req_1",
                "task_id": "t_wait",
                "task_label": "002",
                "unit_type": "3tnk",
                "count": 2,
                "assigned_count": 0,
                "remaining_count": 2,
                "status": "pending",
                "reason": "missing_prerequisite",
                "prerequisites": ["weap"],
            }
        ],
        timestamp=time.time(),
    ).to_dict()

    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=MockWorldModel())

    ctx = adjutant._build_context("继续发展")

    focus = ctx.coordinator_snapshot["unit_pipeline_focus"]
    assert focus["task_id"] == "t_wait"
    assert focus["task_label"] == "002"
    assert focus["reason"] == "missing_prerequisite"
    assert focus["request_count"] == 1
    assert focus["reservation_count"] == 1
    assert "战车工厂" in focus["detail"]

    reservation_alert = next(
        alert
        for alert in ctx.coordinator_snapshot["alerts"]
        if alert.get("code") == "reservation_waiting"
    )
    assert "#002" in reservation_alert["text"]
    assert "战车工厂" in reservation_alert["text"]
    assert "补位" in reservation_alert["text"]
    assert "战车工厂" in ctx.coordinator_snapshot["status_line"]
    print("  PASS: build_context_surfaces_unit_pipeline_focus_in_coordinator_alerts")


def test_build_context_prefers_highest_severity_unit_pipeline_focus():
    kernel = MockKernel()
    kernel._runtime_state_override = RuntimeStateSnapshot(
        active_tasks={},
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[
            {
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "002",
                "category": "infantry",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "步兵",
                "blocking": True,
                "reason": "waiting_dispatch",
            },
            {
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "重坦",
                "blocking": True,
                "reason": "world_sync_stale",
                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                "world_sync_consecutive_failures": 4,
                "world_sync_failure_threshold": 3,
            },
            {
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "防空车",
                "blocking": True,
                "reason": "bootstrap_in_progress",
            },
        ],
        capability_status=CapabilityStatusSnapshot(),
        unit_reservations=[
            {
                "reservation_id": "res_wait",
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "002",
                "unit_type": "e1",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "waiting_dispatch",
                "updated_at": 10.0,
            },
            {
                "reservation_id": "res_stale",
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "unit_type": "3tnk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "world_sync_stale",
                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                "world_sync_consecutive_failures": 4,
                "world_sync_failure_threshold": 3,
                "updated_at": 20.0,
            },
            {
                "reservation_id": "res_boot",
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "unit_type": "ftrk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "bootstrap_in_progress",
                "updated_at": 15.0,
            },
        ],
        timestamp=time.time(),
    ).to_dict()

    class RuntimeOnlyWorldModel(MockWorldModel):
        def query(self, query_type, params=None):
            if query_type == "battlefield_snapshot":
                return {}
            return super().query(query_type, params)

    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=RuntimeOnlyWorldModel())

    ctx = adjutant._build_context("继续发展")

    focus = ctx.coordinator_snapshot["unit_pipeline_focus"]
    assert focus["task_id"] == "t_stale"
    assert focus["task_label"] == "003"
    assert focus["reason"] == "world_sync_stale"
    assert focus["request_count"] == 3
    assert focus["reservation_count"] == 3
    assert "等待世界同步恢复" in focus["detail"]
    assert "economy:COMMAND_EXECUTION_ERROR" in focus["detail"]
    preview_items = ctx.coordinator_snapshot["unit_pipeline_preview_items"]
    assert [item["task_label"] for item in preview_items] == ["003", "004", "002"]
    assert [item["reason"] for item in preview_items] == [
        "world_sync_stale",
        "bootstrap_in_progress",
        "waiting_dispatch",
    ]
    assert "重坦" in ctx.coordinator_snapshot["status_line"]
    assert "其他在途" in ctx.coordinator_snapshot["status_line"]
    assert "#004 防空车 × 1 · 前置生产中" in ctx.coordinator_snapshot["status_line"]
    assert "#002 步兵 × 1 · 待分发" in ctx.coordinator_snapshot["status_line"]
    assert ctx.coordinator_snapshot["status_line"].count("#003") == 1


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
    kernel._runtime_state_override = RuntimeStateSnapshot(
        active_tasks={
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
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[
            {
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "重坦",
                "blocking": True,
                "reason": "world_sync_stale",
                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                "world_sync_consecutive_failures": 4,
                "world_sync_failure_threshold": 3,
            },
            {
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "防空车",
                "blocking": True,
                "reason": "bootstrap_in_progress",
            },
            {
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "005",
                "category": "infantry",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "步兵",
                "blocking": True,
                "reason": "waiting_dispatch",
            },
        ],
        capability_status=CapabilityStatusSnapshot(
            task_id="t_cap",
            task_label="001",
            status="running",
            phase="bootstrapping",
            blocker="bootstrap_in_progress",
            active_job_types=["EconomyExpert"],
            pending_request_count=2,
            blocking_request_count=1,
            bootstrapping_request_count=1,
            recent_directives=["发展经济", "优先补电"],
        ),
        unit_reservations=[
            {
                "reservation_id": "res_stale",
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "unit_type": "3tnk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "world_sync_stale",
            },
            {
                "reservation_id": "res_boot",
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "unit_type": "ftrk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "bootstrap_in_progress",
            },
            {
                "reservation_id": "res_wait",
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "005",
                "unit_type": "e1",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "waiting_dispatch",
            },
        ],
        timestamp=time.time(),
    ).to_dict()
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
    assert [item["task_label"] for item in payload["coordinator_snapshot"]["unit_pipeline_preview_items"]] == ["003", "004", "005"]
    assert len(payload["coordinator_snapshot"]["unit_pipeline_preview_items"]) == 3
    assert payload["coordinator_hints"]["suggested_disposition"] == "merge"
    assert payload["active_tasks"][0]["status_line"]
    print("  PASS: classify_input_sends_coordinator_snapshot_to_llm")


def test_economy_command_merge_reports_capability_phase_and_blocker():
    kernel = MockKernel()
    cap_task = kernel.create_task("发展经济", "managed", 80)
    cap_task.task_id = "t_cap"
    cap_task.label = "001"
    cap_task.is_capability = True
    kernel._runtime_state_override = RuntimeStateSnapshot(
        active_tasks={
            "t_cap": {
                "raw_text": "发展经济",
                "label": "001",
                "status": "running",
                "is_capability": True,
                "active_group_size": 0,
            },
        },
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[
            {
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "重坦",
                "blocking": True,
                "reason": "world_sync_stale",
                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                "world_sync_consecutive_failures": 4,
                "world_sync_failure_threshold": 3,
            },
            {
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "category": "vehicle",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "防空车",
                "blocking": True,
                "reason": "bootstrap_in_progress",
            },
            {
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "005",
                "category": "infantry",
                "count": 1,
                "fulfilled": 0,
                "remaining_count": 1,
                "hint": "步兵",
                "blocking": True,
                "reason": "waiting_dispatch",
            },
        ],
        capability_status=CapabilityStatusSnapshot(
            task_id="t_cap",
            task_label="001",
            status="running",
            phase="bootstrapping",
            blocker="bootstrap_in_progress",
            active_job_types=["EconomyExpert"],
            pending_request_count=2,
            blocking_request_count=1,
            bootstrapping_request_count=1,
        ),
        unit_reservations=[
            {
                "reservation_id": "res_stale",
                "request_id": "req_stale",
                "task_id": "t_stale",
                "task_label": "003",
                "unit_type": "3tnk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "world_sync_stale",
            },
            {
                "reservation_id": "res_boot",
                "request_id": "req_boot",
                "task_id": "t_boot",
                "task_label": "004",
                "unit_type": "ftrk",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "bootstrap_in_progress",
            },
            {
                "reservation_id": "res_wait",
                "request_id": "req_wait",
                "task_id": "t_wait",
                "task_label": "005",
                "unit_type": "e1",
                "count": 1,
                "remaining_count": 1,
                "status": "pending",
                "blocking": True,
                "reason": "waiting_dispatch",
            },
        ],
        timestamp=time.time(),
    ).to_dict()
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=MockWorldModel())

    async def run():
        return await adjutant.handle_player_input("发展经济")

    result = asyncio.run(run())
    assert result["merged"] is True
    assert "补齐前置" in result["response_text"]
    assert "待处理请求 2" in result["response_text"]
    assert "阻塞请求 1" in result["response_text"]
    assert "其他在途 #004 防空车 × 1 · 前置生产中" in result["response_text"]
    assert "#005 步兵 × 1 · 待分发" in result["response_text"]
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
    world_model = PrereqWorldModel()
    kernel._runtime_state_override = world_model.query("runtime_state")
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

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
    world_model = FulfillingWorldModel()
    kernel._runtime_state_override = world_model.query("runtime_state")
    adjutant = Adjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

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


def test_command_without_disposition_prefers_ready_composite_workflow_task():
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
    plain_recon = MockTask("t_plain", "探索地图")
    plain_recon.label = "002"
    combat = MockTask("t_combat", "攻击敌人")
    combat.label = "003"
    combo = MockTask("t_combo", "整点步兵再探索地图")
    combo.label = "004"
    kernel._tasks.extend([plain_recon, combat, combo])
    kernel._runtime_state_override = RuntimeStateSnapshot(
        active_tasks={
            "t_plain": {
                "raw_text": "探索地图",
                "label": "002",
                "status": "running",
                "is_capability": False,
                "active_group_size": 0,
                "active_actor_ids": [],
            },
            "t_combat": {
                "raw_text": "攻击敌人",
                "label": "003",
                "status": "running",
                "is_capability": False,
                "active_group_size": 3,
                "active_actor_ids": [],
            },
            "t_combo": {
                "raw_text": "整点步兵再探索地图",
                "label": "004",
                "status": "running",
                "is_capability": False,
                "active_group_size": 2,
                "active_actor_ids": [401, 402],
            },
        },
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[],
        capability_status=CapabilityStatusSnapshot(),
        unit_reservations=[],
        timestamp=time.time(),
    ).to_dict()

    world_model = MockWorldModel()
    adjutant = HintOnlyAdjutant(llm=MockProvider(), kernel=kernel, world_model=world_model)

    async def run():
        result = await adjutant.handle_player_input("继续")
        assert result["type"] == "command"
        assert result["ok"] is True
        assert result["routing"] == "command_merge"
        assert result["target_task_id"] == "004"

    asyncio.run(run())

    assert len(kernel.created_tasks) == 0
    assert getattr(combo, "_injected_messages", []) == ["继续"]
    assert getattr(plain_recon, "_injected_messages", []) == []
    assert getattr(combat, "_injected_messages", []) == []
    assert world_model.query_counts.get("battlefield_snapshot") == 1
    print("  PASS: command_without_disposition_prefers_ready_composite_workflow_task")


def test_system_prompt_has_dialogue_context_awareness_section():
    """CLASSIFICATION_SYSTEM_PROMPT contains the dialogue context awareness section."""
    assert "Dialogue context awareness" in CLASSIFICATION_SYSTEM_PROMPT
    assert "recent_completed_tasks" in CLASSIFICATION_SYSTEM_PROMPT
    assert "failed" in CLASSIFICATION_SYSTEM_PROMPT
    print("  PASS: system_prompt_has_dialogue_context_awareness_section")


# --- Run all tests ---

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
