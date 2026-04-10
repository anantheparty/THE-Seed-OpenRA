"""Focused coordinator-slice tests for Adjutant."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adjutant import Adjutant
from llm import MockProvider


class _MockStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _MockTask:
    def __init__(self, task_id: str, label: str, raw_text: str, *, status: str = "running", is_capability: bool = False) -> None:
        self.task_id = task_id
        self.label = label
        self.raw_text = raw_text
        self.status = _MockStatus(status)
        self.is_capability = is_capability


class _Kernel:
    def __init__(self) -> None:
        self._tasks = [
            _MockTask("t_cap", "001", "发展经济", is_capability=True),
            _MockTask("t_recon", "002", "探索地图"),
            _MockTask("t_combat", "003", "攻击敌人"),
        ]

    def list_tasks(self):
        return list(self._tasks)

    def list_pending_questions(self):
        return []

    @property
    def capability_task_id(self):
        return "t_cap"


class _WorldModel:
    def world_summary(self):
        return {
            "economy": {"cash": 5000, "low_power": False, "queue_blocked": False},
            "military": {"self_units": 8, "enemy_units": 5},
            "map": {"explored_pct": 0.42},
            "known_enemy": {"bases": 1, "units_spotted": 5},
            "timestamp": time.time(),
        }

    def query(self, query_type: str, params=None):
        if query_type == "battlefield_snapshot":
            return {
                "summary": "我方8 / 敌方5，探索42.0%",
                "disposition": "stable",
                "focus": "recon",
                "self_units": 8,
                "enemy_units": 5,
                "self_combat_value": 1200,
                "enemy_combat_value": 900,
                "idle_self_units": 2,
                "low_power": False,
                "queue_blocked": False,
                "recommended_posture": "satisfy_requests",
                "threat_level": "medium",
                "threat_direction": "west",
                "base_under_attack": False,
                "base_health_summary": "stable",
                "has_production": True,
                "explored_pct": 0.42,
                "enemy_bases": 1,
                "enemy_spotted": 5,
                "frozen_enemy_count": 2,
                "pending_request_count": 3,
                "bootstrapping_request_count": 1,
                "reservation_count": 1,
                "stale": False,
            }
        if query_type == "runtime_state":
            return {
                "active_tasks": {
                    "t_cap": {
                        "label": "001",
                        "raw_text": "发展经济",
                        "status": "running",
                        "is_capability": True,
                        "active_group_size": 0,
                    },
                    "t_recon": {
                        "label": "002",
                        "raw_text": "探索地图",
                        "status": "running",
                        "is_capability": False,
                        "active_group_size": 0,
                    },
                    "t_combat": {
                        "label": "003",
                        "raw_text": "攻击敌人",
                        "status": "running",
                        "is_capability": False,
                        "active_group_size": 3,
                    },
                },
                "active_jobs": {
                    "j_cap_1": {"task_id": "t_cap", "expert_type": "EconomyExpert", "status": "running", "job_id": "j_cap_1"},
                    "j_recon_1": {"task_id": "t_recon", "expert_type": "ReconExpert", "status": "waiting", "job_id": "j_recon_1"},
                    "j_combat_1": {"task_id": "t_combat", "expert_type": "CombatExpert", "status": "running", "job_id": "j_combat_1"},
                },
                "capability_status": {
                    "task_id": "t_cap",
                    "label": "001",
                    "status": "running",
                    "phase": "dispatch",
                    "blocker": "pending_requests_waiting_dispatch",
                    "active_job_types": ["EconomyExpert"],
                    "pending_request_count": 3,
                    "bootstrapping_request_count": 1,
                    "blocking_request_count": 2,
                },
                "unit_reservations": [
                    {"reservation_id": "res_1", "task_id": "t_recon"},
                ],
                "timestamp": time.time(),
            }
        return {}

    def compute_runtime_facts(self, task_id: str, include_buildable: bool = False):
        assert task_id == "__adjutant__"
        assert include_buildable is False
        return {
            "has_construction_yard": True,
            "mcv_count": 1,
            "mcv_idle": True,
            "power_plant_count": 1,
            "refinery_count": 1,
            "barracks_count": 1,
            "war_factory_count": 1,
            "radar_count": 1,
            "repair_facility_count": 0,
            "airfield_count": 0,
            "tech_center_count": 0,
            "harvester_count": 2,
            "info_experts": {
                "threat_level": "medium",
                "threat_direction": "west",
                "enemy_count": 5,
                "base_under_attack": False,
                "base_health_summary": "stable",
                "has_production": True,
            },
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


class _WorldModelMissingPrereq(_WorldModel):
    def query(self, query_type: str, params=None):
        result = super().query(query_type, params)
        if query_type == "runtime_state":
            result["capability_status"]["blocker"] = "missing_prerequisite"
            result["capability_status"]["prerequisite_gap_count"] = 2
            result["capability_status"]["dispatch_request_count"] = 2
        return result


class _WorldModelFulfilling(_WorldModel):
    def query(self, query_type: str, params=None):
        result = super().query(query_type, params)
        if query_type == "runtime_state":
            result["capability_status"].pop("label", None)
            result["capability_status"]["task_label"] = "001"
            result["capability_status"]["phase"] = "fulfilling"
            result["capability_status"]["blocker"] = ""
            result["capability_status"]["start_released_request_count"] = 1
            result["capability_status"]["reinforcement_request_count"] = 2
        return result


def test_battlefield_snapshot_prefers_runtime_query() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModel())

    snapshot = adjutant._battlefield_snapshot()

    assert snapshot["focus"] == "recon"
    assert snapshot["pending_request_count"] == 3
    assert snapshot["frozen_enemy_count"] == 2
    assert snapshot["recommended_posture"] == "satisfy_requests"
    assert snapshot["threat_level"] == "medium"
    assert snapshot["reservation_count"] == 1
    print("  PASS: battlefield_snapshot_prefers_runtime_query")


def test_build_context_includes_task_triage_fields() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModel())

    context = adjutant._build_context("继续")
    by_label = {task["label"]: task for task in context.active_tasks}

    assert by_label["001"]["phase"] == "dispatch"
    assert by_label["001"]["blocking_reason"] == "pending_requests_waiting_dispatch"
    assert by_label["001"]["active_expert"] == "EconomyExpert"
    assert "blocking=2" in by_label["001"]["status_line"]
    assert by_label["002"]["state"] == "waiting_units"
    assert by_label["002"]["waiting_reason"] == "unit_reservation"
    assert by_label["002"]["reservation_ids"] == ["res_1"]
    assert by_label["003"]["state"] == "running"
    assert by_label["003"]["active_expert"] == "CombatExpert"
    assert by_label["003"]["active_group_size"] == 3
    assert context.coordinator_snapshot["recommended_posture"] == "satisfy_requests"
    assert context.coordinator_snapshot["battlefield"]["threat_direction"] == "west"
    assert context.coordinator_snapshot["capability"]["phase"] == "dispatch"
    assert context.coordinator_snapshot["capability"]["blocker"] == "pending_requests_waiting_dispatch"
    assert context.coordinator_snapshot["task_overview"]["active_count"] == 3
    assert context.coordinator_snapshot["task_overview"]["reservation_wait_count"] == 1
    assert context.coordinator_snapshot["task_overview"]["combat_group_count"] == 1
    battle_groups = context.coordinator_snapshot["battle_groups"]
    assert [group["label"] for group in battle_groups] == ["003", "002"]
    assert battle_groups[0]["active_expert"] == "CombatExpert"
    assert battle_groups[0]["active_group_size"] == 3
    assert battle_groups[1]["state"] == "waiting_units"
    print("  PASS: build_context_includes_task_triage_fields")


def test_build_context_surfaces_prerequisite_gap_blocker_text() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModelMissingPrereq())

    context = adjutant._build_context("继续")
    capability = next(task for task in context.active_tasks if task["label"] == "001")

    assert context.coordinator_snapshot["capability"]["prerequisite_gap_count"] == 2
    assert capability["blocking_reason"] == "missing_prerequisite"
    assert "缺少前置建筑" in capability["status_line"]
    print("  PASS: build_context_surfaces_prerequisite_gap_blocker_text")


def test_coordinator_hints_merge_capability_followup_on_prerequisite_gap() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModelMissingPrereq())

    context = adjutant._build_context("那就先补前置")

    assert context.coordinator_hints["suggested_disposition"] == "merge"
    assert context.coordinator_hints["likely_target_label"] == "001"
    assert context.coordinator_hints["reason"] == "capability_followup_missing_prerequisite"
    print("  PASS: coordinator_hints_merge_capability_followup_on_prerequisite_gap")


def test_build_context_uses_capability_task_label_fallback_and_fulfilling_counts() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModelFulfilling())

    context = adjutant._build_context("继续补兵")
    capability = next(task for task in context.active_tasks if task["label"] == "001")

    assert context.coordinator_snapshot["capability"]["label"] == "001"
    assert context.coordinator_snapshot["capability"]["phase"] == "fulfilling"
    assert context.coordinator_snapshot["capability"]["start_released_request_count"] == 1
    assert context.coordinator_snapshot["capability"]["reinforcement_request_count"] == 2
    assert "ready=1" in capability["status_line"]
    assert "reinforce=2" in capability["status_line"]
    print("  PASS: build_context_uses_capability_task_label_fallback_and_fulfilling_counts")


def test_coordinator_hints_merge_capability_followup_on_fulfilling_phase() -> None:
    adjutant = Adjutant(llm=MockProvider(), kernel=_Kernel(), world_model=_WorldModelFulfilling())

    context = adjutant._build_context("继续发展经济")

    assert context.coordinator_hints["suggested_disposition"] == "merge"
    assert context.coordinator_hints["likely_target_label"] == "001"
    assert context.coordinator_hints["reason"] == "capability_phase_fulfilling"
    print("  PASS: coordinator_hints_merge_capability_followup_on_fulfilling_phase")


if __name__ == "__main__":
    print("Running Adjutant coordinator tests...\n")
    test_battlefield_snapshot_prefers_runtime_query()
    test_build_context_includes_task_triage_fields()
    test_build_context_surfaces_prerequisite_gap_blocker_text()
    test_coordinator_hints_merge_capability_followup_on_prerequisite_gap()
    test_build_context_uses_capability_task_label_fallback_and_fulfilling_counts()
    test_coordinator_hints_merge_capability_followup_on_fulfilling_phase()
    print("\nAll Adjutant coordinator tests passed!")
