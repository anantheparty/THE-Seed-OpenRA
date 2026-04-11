"""Focused tests for runtime projection helpers."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_views import (
    BattlefieldSnapshot,
    CapabilityStatusSnapshot,
    RuntimeStateSnapshot,
    TaskTriageSnapshot,
    build_battlefield_snapshot,
    build_runtime_state_snapshot,
)


def test_build_runtime_state_snapshot_normalizes_capability_status() -> None:
    snapshot = build_runtime_state_snapshot(
        active_tasks={"t1": {"label": "001"}},
        active_jobs={"j1": {"task_id": "t1", "expert_type": "EconomyExpert"}},
        resource_bindings={"actor:1": "j1"},
        constraints=[{"constraint_id": "c1", "kind": "leash"}],
        unfulfilled_requests=[{"request_id": "req1", "reason": "missing_prerequisite"}],
        capability_status={"task_id": "t_cap", "label": "001", "phase": "dispatch"},
        unit_reservations=[{"reservation_id": "res1", "task_id": "t1"}],
        timestamp=123.4,
    ).to_dict()

    assert snapshot["active_tasks"]["t1"]["label"] == "001"
    assert snapshot["active_jobs"]["j1"]["expert_type"] == "EconomyExpert"
    assert snapshot["resource_bindings"]["actor:1"] == "j1"
    assert snapshot["constraints"][0]["constraint_id"] == "c1"
    assert snapshot["unfulfilled_requests"][0]["request_id"] == "req1"
    assert snapshot["capability_status"]["task_id"] == "t_cap"
    assert snapshot["capability_status"]["phase"] == "dispatch"
    assert snapshot["unit_reservations"][0]["reservation_id"] == "res1"
    assert snapshot["timestamp"] == 123.4
    print("  PASS: build_runtime_state_snapshot_normalizes_capability_status")


def test_build_runtime_state_snapshot_accepts_capability_snapshot_object() -> None:
    capability = CapabilityStatusSnapshot(task_id="t_cap", task_label="001", phase="fulfilling")
    snapshot = build_runtime_state_snapshot(
        active_tasks={},
        active_jobs={},
        resource_bindings={},
        constraints=[],
        unfulfilled_requests=[],
        capability_status=capability,
        unit_reservations=[],
        timestamp=5.0,
    ).to_dict()

    assert snapshot["capability_status"]["task_id"] == "t_cap"
    assert snapshot["capability_status"]["label"] == "001"
    assert snapshot["capability_status"]["phase"] == "fulfilling"
    print("  PASS: build_runtime_state_snapshot_accepts_capability_snapshot_object")


def test_runtime_state_snapshot_from_mapping_normalizes_capability_and_lists() -> None:
    snapshot = RuntimeStateSnapshot.from_mapping(
        {
            "active_tasks": {"t1": {"label": "001"}},
            "active_jobs": {"j1": {"task_id": "t1", "expert_type": "ReconExpert"}},
            "resource_bindings": {"actor:1": "j1"},
            "constraints": [{"constraint_id": "c1"}],
            "unfulfilled_requests": [{"request_id": "req1", "reason": "queue_blocked"}],
            "capability_status": {"task_id": "t_cap", "phase": "dispatch"},
            "unit_reservations": [{"reservation_id": "r1", "task_id": "t1"}],
            "timestamp": "12.5",
        }
    )

    assert snapshot.active_tasks["t1"]["label"] == "001"
    assert snapshot.active_jobs["j1"]["expert_type"] == "ReconExpert"
    assert snapshot.resource_bindings["actor:1"] == "j1"
    assert snapshot.constraints[0]["constraint_id"] == "c1"
    assert snapshot.unfulfilled_requests[0]["request_id"] == "req1"
    assert snapshot.capability_status.task_id == "t_cap"
    assert snapshot.capability_status.phase == "dispatch"
    assert snapshot.unit_reservations[0]["reservation_id"] == "r1"
    assert snapshot.timestamp == 12.5
    print("  PASS: runtime_state_snapshot_from_mapping_normalizes_capability_and_lists")


def test_build_battlefield_snapshot_normalizes_numeric_fields() -> None:
    snapshot = build_battlefield_snapshot(
        summary="我方2 / 敌方1",
        disposition="stable",
        focus="recon",
        self_units=2,
        enemy_units=1,
        self_combat_value=123.456,
        enemy_combat_value=78.9,
        idle_self_units=1,
        self_combat_units=2,
        committed_combat_units=1,
        free_combat_units=1,
        low_power=False,
        queue_blocked=True,
        queue_blocked_reason="ready_not_placed",
        queue_blocked_queue_types=["Building"],
        disabled_structure_count=2,
        powered_down_structure_count=1,
        low_power_disabled_structure_count=1,
        power_outage_structure_count=0,
        disabled_structures=["雷达站(lowpower)", "防空炮(powerdown)"],
        recommended_posture="unblock_queue",
        threat_level="medium",
        threat_direction="west",
        base_under_attack=False,
        base_health_summary="stable",
        has_production=True,
        explored_pct=0.42,
        enemy_bases=1,
        enemy_spotted=3,
        frozen_enemy_count=1,
        pending_request_count=2,
        bootstrapping_request_count=1,
        reservation_count=2,
        stale=False,
        capability_status={"task_id": "t_cap"},
    ).to_dict()

    assert snapshot["self_combat_value"] == 123.46
    assert snapshot["enemy_combat_value"] == 78.9
    assert snapshot["queue_blocked"] is True
    assert snapshot["queue_blocked_reason"] == "ready_not_placed"
    assert snapshot["queue_blocked_queue_types"] == ["Building"]
    assert snapshot["disabled_structure_count"] == 2
    assert snapshot["disabled_structures"] == ["雷达站(lowpower)", "防空炮(powerdown)"]
    assert snapshot["recommended_posture"] == "unblock_queue"
    assert snapshot["capability_status"]["task_id"] == "t_cap"
    print("  PASS: build_battlefield_snapshot_normalizes_numeric_fields")


def test_build_battlefield_snapshot_accepts_capability_snapshot_object() -> None:
    capability = CapabilityStatusSnapshot(task_id="t_cap", task_label="001", phase="fulfilling")
    snapshot = build_battlefield_snapshot(
        summary="ok",
        disposition="stable",
        focus="general",
        self_units=1,
        enemy_units=0,
        self_combat_value=1.0,
        enemy_combat_value=0.0,
        idle_self_units=1,
        self_combat_units=1,
        committed_combat_units=0,
        free_combat_units=1,
        low_power=False,
        queue_blocked=False,
        recommended_posture="maintain_posture",
        threat_level="low",
        threat_direction="unknown",
        base_under_attack=False,
        base_health_summary="ok",
        has_production=False,
        explored_pct=0.1,
        enemy_bases=0,
        enemy_spotted=0,
        frozen_enemy_count=0,
        pending_request_count=0,
        bootstrapping_request_count=0,
        reservation_count=0,
        stale=False,
        capability_status=capability,
    )

    assert snapshot.capability_status.task_id == "t_cap"
    assert snapshot.capability_status.task_label == "001"
    assert snapshot.capability_status.phase == "fulfilling"
    print("  PASS: build_battlefield_snapshot_accepts_capability_snapshot_object")


def test_battlefield_snapshot_from_mapping_normalizes_query_payload() -> None:
    snapshot = BattlefieldSnapshot.from_mapping(
        {
            "summary": "压力中",
            "disposition": "under_pressure",
            "focus": "defense",
            "self_units": "5",
            "enemy_units": "14",
            "self_combat_value": "900.126",
            "enemy_combat_value": "2600",
            "queue_blocked": 1,
            "queue_blocked_queue_types": ["Building", "", None],
            "disabled_structure_count": "2",
            "disabled_structures": ["雷达站(lowpower)", "", None],
            "explored_pct": "0.42",
            "pending_request_count": "3",
            "bootstrapping_request_count": "1",
            "reservation_count": "2",
            "capability_status": {"task_id": "t_cap", "phase": "dispatch"},
        }
    )
    payload = snapshot.to_dict()

    assert snapshot.capability_status.task_id == "t_cap"
    assert snapshot.capability_status.phase == "dispatch"
    assert payload["self_units"] == 5
    assert payload["enemy_units"] == 14
    assert payload["self_combat_value"] == 900.13
    assert payload["enemy_combat_value"] == 2600.0
    assert payload["queue_blocked"] is True
    assert payload["queue_blocked_queue_types"] == ["Building"]
    assert payload["disabled_structure_count"] == 2
    assert payload["disabled_structures"] == ["雷达站(lowpower)"]
    assert payload["explored_pct"] == 0.42
    assert payload["pending_request_count"] == 3
    assert payload["bootstrapping_request_count"] == 1
    assert payload["reservation_count"] == 2
    print("  PASS: battlefield_snapshot_from_mapping_normalizes_query_payload")


def test_task_triage_snapshot_from_mapping_normalizes_fields() -> None:
    snapshot = TaskTriageSnapshot.from_mapping(
        {
            "phase": "dispatch",
            "active_expert": "ReconExpert",
            "active_group_size": "2",
            "reservation_ids": ["r1", None, "r2"],
            "world_stale": 1,
        }
    )

    assert snapshot.phase == "dispatch"
    assert snapshot.active_expert == "ReconExpert"
    assert snapshot.active_group_size == 2
    assert snapshot.reservation_ids == ["r1", "r2"]
    assert snapshot.world_stale is True
    print("  PASS: task_triage_snapshot_from_mapping_normalizes_fields")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
