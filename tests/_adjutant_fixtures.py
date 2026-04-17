"""Shared Adjutant test mocks and surface constants."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from models import PlayerResponse, TaskKind, TaskMessage, TaskMessageType
from openra_api.models import Actor, Location
from runtime_views import BattlefieldSnapshot, CapabilityStatusSnapshot, RuntimeStateSnapshot
from tests.schema_assertions import assert_mapping_superset

WORLD_SUMMARY_REQUIRED_KEYS = {"economy", "military", "map", "known_enemy", "timestamp"}
WORLD_SUMMARY_ECONOMY_KEYS = {
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
WORLD_SUMMARY_MILITARY_KEYS = {
    "self_units",
    "enemy_units",
    "self_combat_value",
    "enemy_combat_value",
    "idle_self_units",
}
WORLD_SUMMARY_MAP_KEYS = {"explored_pct"}
WORLD_SUMMARY_ENEMY_KEYS = {"units_spotted", "bases", "frozen_count"}
BATTLEFIELD_SNAPSHOT_KEYS = set(BattlefieldSnapshot().to_dict().keys())
RUNTIME_STATE_KEYS = set(RuntimeStateSnapshot().to_dict().keys())
CAPABILITY_STATUS_KEYS = set(CapabilityStatusSnapshot().to_dict().keys())
KERNEL_REQUIRED_CALLABLES = {
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
KERNEL_REQUIRED_ATTRS = {"capability_task_id"}
WORLD_MODEL_REQUIRED_CALLABLES = {"world_summary", "query", "refresh_health"}


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
        self.cancelled_task_ids: list[str] = []
        self._pending_questions: list[dict] = []
        self._tasks: list[MockTask] = []
        self._task_messages: list[TaskMessage] = []
        self._runtime_state_override: dict[str, Any] | None = None
        self._task_counter = 0
        self._job_counter = 0
        self.capability_notes: list[dict[str, str]] = []

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
        return sorted(self._pending_questions, key=lambda q: q.get("priority", 0), reverse=True)

    def list_tasks(self):
        return list(self._tasks)

    def list_task_messages(self, task_id=None):
        messages = list(self._task_messages)
        if task_id is None:
            return messages
        return [message for message in messages if message.task_id == task_id]

    def register_task_message(self, message):
        self._task_messages.append(message)
        return True

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
        self.cancelled_task_ids.append(task_id)
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

    def record_capability_note(self, text):
        cap_id = self.capability_task_id
        if not cap_id:
            return False
        self.capability_notes.append({"task_id": cap_id, "text": text})
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
        self._pending_questions.append(
            {
                "message_id": message_id,
                "task_id": task_id,
                "question": question,
                "options": options,
                "default_option": options[0] if options else None,
                "priority": priority,
                "asked_at": time.time(),
                "timeout_s": 30.0,
            }
        )


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
        summary["military"].update({"enemy_combat_value": 1200, "idle_self_units": 6})
        summary["known_enemy"].update({"frozen_count": 0})
        assert_mapping_superset(summary, WORLD_SUMMARY_REQUIRED_KEYS, label="MockWorldModel.world_summary")
        assert_mapping_superset(
            summary["economy"],
            WORLD_SUMMARY_ECONOMY_KEYS,
            label="MockWorldModel.world_summary.economy",
        )
        assert_mapping_superset(
            summary["military"],
            WORLD_SUMMARY_MILITARY_KEYS,
            label="MockWorldModel.world_summary.military",
        )
        assert_mapping_superset(summary["map"], WORLD_SUMMARY_MAP_KEYS, label="MockWorldModel.world_summary.map")
        assert_mapping_superset(
            summary["known_enemy"],
            WORLD_SUMMARY_ENEMY_KEYS,
            label="MockWorldModel.world_summary.known_enemy",
        )
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
            assert_mapping_superset(snapshot, BATTLEFIELD_SNAPSHOT_KEYS, label="MockWorldModel.battlefield_snapshot")
            assert_mapping_superset(
                snapshot["capability_status"],
                CAPABILITY_STATUS_KEYS,
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
            assert_mapping_superset(runtime_state, RUNTIME_STATE_KEYS, label="MockWorldModel.runtime_state")
            assert_mapping_superset(
                runtime_state["capability_status"],
                CAPABILITY_STATUS_KEYS,
                label="MockWorldModel.runtime_state.capability_status",
            )
            return runtime_state
        if query_type == "my_actors" and params == {"category": "mcv"}:
            return {"actors": [{"actor_id": 99, "category": "mcv", "position": [500, 400]}], "timestamp": time.time()}
        if query_type == "my_actors" and params == {"type": "建造厂"}:
            return {"actors": [{"actor_id": 130, "type": "建造厂", "position": [520, 420]}], "timestamp": time.time()}
        if query_type == "find_actors":
            owner = (params or {}).get("owner")
            name = (params or {}).get("name")
            actors = []
            if owner == "self" and name == "步兵":
                actors = [{"actor_id": 11, "name": "步兵"}, {"actor_id": 12, "name": "步兵"}]
            return {"actors": actors, "timestamp": time.time()}
        if query_type == "my_actors" and params == {"category": "harvester"}:
            return {"actors": [{"actor_id": 301, "category": "harvester"}, {"actor_id": 302, "category": "harvester"}], "timestamp": time.time()}
        if query_type == "my_actors" and params == {"name": None, "can_attack": True}:
            return {"actors": [{"actor_id": 401, "name": "步兵"}, {"actor_id": 402, "name": "坦克"}], "timestamp": time.time()}
        if query_type == "my_actors" and params == {"name": "步兵", "can_attack": True}:
            return {"actors": [{"actor_id": 401, "name": "步兵"}, {"actor_id": 403, "name": "步兵"}], "timestamp": time.time()}
        if query_type == "my_actors" and params == {"name": "工程师"}:
            return {"actors": [{"actor_id": 601, "name": "工程师"}], "timestamp": time.time()}
        if query_type == "enemy_actors" and params == {"category": "building"}:
            return {
                "actors": [{"actor_id": 902, "name": "dome", "display_name": "雷达站", "position": [1200, 260]}],
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
            "buildable": {"Building": ["weap", "dome", "fix"], "Vehicle": ["ftrk", "harv"]},
            "buildable_now": {
                "Building": ["powr", "barr", "dome", "fix", "weap"],
                "Infantry": ["e1", "e3"],
                "Vehicle": ["ftrk", "harv"],
            },
            "buildable_blocked": {},
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


__all__ = [
    "BATTLEFIELD_SNAPSHOT_KEYS",
    "CAPABILITY_STATUS_KEYS",
    "KERNEL_REQUIRED_ATTRS",
    "KERNEL_REQUIRED_CALLABLES",
    "MockKernel",
    "MockTask",
    "MockWorldModel",
    "RUNTIME_STATE_KEYS",
    "WORLD_MODEL_REQUIRED_CALLABLES",
]
