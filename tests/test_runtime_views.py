"""Focused tests for runtime projection helpers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_views import CapabilityStatusSnapshot, build_runtime_state_snapshot


def test_build_runtime_state_snapshot_normalizes_capability_status() -> None:
    snapshot = build_runtime_state_snapshot(
        active_tasks={"t1": {"label": "001"}},
        active_jobs={"j1": {"task_id": "t1", "expert_type": "EconomyExpert"}},
        resource_bindings={"actor:1": "j1"},
        constraints=[{"constraint_id": "c1", "kind": "leash"}],
        capability_status={"task_id": "t_cap", "label": "001", "phase": "dispatch"},
        unit_reservations=[{"reservation_id": "res1", "task_id": "t1"}],
        timestamp=123.4,
    ).to_dict()

    assert snapshot["active_tasks"]["t1"]["label"] == "001"
    assert snapshot["active_jobs"]["j1"]["expert_type"] == "EconomyExpert"
    assert snapshot["resource_bindings"]["actor:1"] == "j1"
    assert snapshot["constraints"][0]["constraint_id"] == "c1"
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
        capability_status=capability,
        unit_reservations=[],
        timestamp=5.0,
    ).to_dict()

    assert snapshot["capability_status"]["task_id"] == "t_cap"
    assert snapshot["capability_status"]["label"] == "001"
    assert snapshot["capability_status"]["phase"] == "fulfilling"
    print("  PASS: build_runtime_state_snapshot_accepts_capability_snapshot_object")


if __name__ == "__main__":
    print("Running runtime_views tests...\n")
    test_build_runtime_state_snapshot_normalizes_capability_status()
    test_build_runtime_state_snapshot_accepts_capability_snapshot_object()
    print("\nAll runtime_views tests passed!")
