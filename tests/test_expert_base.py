"""Tests for Expert and Job base classes."""

from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

from models import (
    Constraint,
    ConstraintEnforcement,
    ExpertConfig,
    ExpertSignal,
    JobStatus,
    ReconJobConfig,
    CombatJobConfig,
    EngagementMode,
    SignalKind,
)
from experts.base import (
    BaseJob,
    ExecutionExpert,
    InformationExpert,
    PlannerExpert,
    SignalCallback,
    ConstraintProvider,
)


# --- Mock implementations ---

class MockReconJob(BaseJob):
    tick_interval = 1.0
    _tick_count = 0

    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def tick(self) -> None:
        self._tick_count += 1
        if self._tick_count >= 3:
            self.emit_signal(
                kind=SignalKind.TASK_COMPLETE,
                summary="Scouting complete",
                result="succeeded",
                data={"base_found": True, "position": [500, 600]},
            )
            self.status = JobStatus.SUCCEEDED


class MockCombatJob(BaseJob):
    tick_interval = 0.2

    @property
    def expert_type(self) -> str:
        return "CombatExpert"

    def tick(self) -> None:
        constraints = self.get_active_constraints()
        for c in constraints:
            if c.kind == "do_not_chase" and "max_distance" in c.params:
                pass  # Would clamp chase distance


class MockReconExpert(ExecutionExpert):
    @property
    def expert_type(self) -> str:
        return "ReconExpert"

    def create_job(self, task_id, config, signal_callback, constraint_provider=None):
        job = MockReconJob(
            job_id=self.generate_job_id(),
            task_id=task_id,
            config=config,
            signal_callback=signal_callback,
            constraint_provider=constraint_provider,
        )
        return job


class MockInfoExpert(InformationExpert):
    def analyze(self, world_state):
        units = world_state.get("units", 0)
        enemy = world_state.get("enemy_units", 0)
        return {
            "threat_level": "high" if enemy > units else "low",
            "advantage_ratio": units / max(enemy, 1),
        }


class MockPlanner(PlannerExpert):
    def plan(self, query_type, params, world_state):
        if query_type == "recon_route":
            return {
                "waypoints": [(100, 200), (300, 400), (500, 600)],
                "estimated_time": 30.0,
                "risk": "low",
            }
        return {"error": f"Unknown query: {query_type}"}


# --- Tests ---

def test_information_expert():
    """InformationExpert produces analysis from world state."""
    expert = MockInfoExpert()
    result = expert.analyze({"units": 10, "enemy_units": 5})
    assert result["threat_level"] == "low"
    assert result["advantage_ratio"] == 2.0

    result = expert.analyze({"units": 3, "enemy_units": 10})
    assert result["threat_level"] == "high"
    print("  PASS: information_expert")


def test_planner_expert():
    """PlannerExpert produces proposals."""
    planner = MockPlanner()
    result = planner.plan("recon_route", {}, {})
    assert len(result["waypoints"]) == 3
    assert result["risk"] == "low"
    print("  PASS: planner_expert")


def test_execution_expert_creates_job():
    """ExecutionExpert creates Job instances with unique IDs."""
    signals: list[ExpertSignal] = []
    expert = MockReconExpert()
    config = ReconJobConfig(search_region="northeast", target_type="base", target_owner="enemy")

    job = expert.create_job(
        task_id="t1",
        config=config,
        signal_callback=signals.append,
    )

    assert job.job_id.startswith("j_")
    assert job.task_id == "t1"
    assert job.expert_type == "ReconExpert"
    assert job.status == JobStatus.RUNNING
    assert isinstance(job, MockReconJob)

    # Two jobs get different IDs
    job2 = expert.create_job("t2", config, signals.append)
    assert job.job_id != job2.job_id
    print("  PASS: execution_expert_creates_job")


def test_job_tick_and_signal():
    """Job ticks autonomously and emits Signals."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:57"])

    # Tick 3 times — job completes on tick 3
    job.do_tick()
    assert job._tick_count == 1
    assert len(signals) == 0

    job.do_tick()
    assert job._tick_count == 2
    assert len(signals) == 0

    job.do_tick()
    assert job._tick_count == 3
    assert len(signals) == 1
    assert signals[0].kind == SignalKind.TASK_COMPLETE
    assert signals[0].result == "succeeded"
    assert signals[0].data["base_found"] is True
    assert job.status == JobStatus.SUCCEEDED
    print("  PASS: job_tick_and_signal")


def test_job_pause_resume():
    """Paused jobs skip ticks."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )

    job.do_tick()
    assert job._tick_count == 1

    job.pause()
    assert job.is_paused is True
    job.do_tick()  # Should skip
    assert job._tick_count == 1  # Unchanged

    job.resume()
    assert job.is_paused is False
    job.do_tick()
    assert job._tick_count == 2
    print("  PASS: job_pause_resume")


def test_job_abort():
    """Abort terminates the job and sends a signal."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )

    job.abort()
    assert job.status == JobStatus.ABORTED
    assert len(signals) == 1
    assert signals[0].kind == SignalKind.TASK_COMPLETE
    assert signals[0].result == "aborted"

    # Ticks should be skipped after abort
    job.do_tick()
    assert job._tick_count == 0  # Never ticked
    print("  PASS: job_abort")


def test_job_patch():
    """Patch updates config parameters."""
    signals: list[ExpertSignal] = []
    config = CombatJobConfig(
        target_position=(100, 200),
        engagement_mode=EngagementMode.ASSAULT,
        max_chase_distance=20,
    )

    job = MockCombatJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )

    assert job.config.max_chase_distance == 20
    job.patch({"max_chase_distance": 10})
    assert job.config.max_chase_distance == 10
    print("  PASS: job_patch")


def test_resource_grant_revoke():
    """Resource callbacks update job state correctly."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )

    # Start with no resources
    assert job.resources == []

    # Grant
    job.on_resource_granted(["actor:57", "actor:58"])
    assert job.resources == ["actor:57", "actor:58"]

    # Revoke one — still has resources
    job.on_resource_revoked(["actor:57"])
    assert job.resources == ["actor:58"]
    assert job.status == JobStatus.RUNNING

    # Revoke all — goes to WAITING
    job.on_resource_revoked(["actor:58"])
    assert job.resources == []
    assert job.status == JobStatus.WAITING

    # Grant again — back to RUNNING
    job.on_resource_granted(["actor:83"])
    assert job.status == JobStatus.RUNNING
    print("  PASS: resource_grant_revoke")


def test_constraint_reading():
    """Job reads active constraints matching its scope."""
    constraints_db: dict[str, list[Constraint]] = {
        "global": [
            Constraint(constraint_id="c1", kind="do_not_chase", scope="global",
                       params={"max_distance": 20}, enforcement=ConstraintEnforcement.CLAMP),
        ],
        "expert_type:CombatExpert": [
            Constraint(constraint_id="c2", kind="economy_first", scope="expert_type:CombatExpert",
                       params={}, enforcement=ConstraintEnforcement.ESCALATE),
        ],
        "task_id:t1": [],
    }

    def provider(scope: str) -> list[Constraint]:
        return constraints_db.get(scope, [])

    signals: list[ExpertSignal] = []
    config = CombatJobConfig(
        target_position=(100, 200),
        engagement_mode=EngagementMode.HOLD,
    )

    job = MockCombatJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
        constraint_provider=provider,
    )

    constraints = job.get_active_constraints()
    assert len(constraints) == 2
    kinds = {c.kind for c in constraints}
    assert "do_not_chase" in kinds
    assert "economy_first" in kinds
    print("  PASS: constraint_reading")


def test_job_to_model():
    """Job converts to data model for context packets."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )
    job.on_resource_granted(["actor:57"])

    model = job.to_model()
    assert model.job_id == "j1"
    assert model.task_id == "t1"
    assert model.expert_type == "ReconExpert"
    assert model.resources == ["actor:57"]
    assert model.status == JobStatus.RUNNING
    print("  PASS: job_to_model")


def test_abort_then_resume_no_revive():
    """resume() after abort does not change terminal status."""
    signals: list[ExpertSignal] = []
    config = ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy")

    job = MockReconJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
    )

    job.abort()
    assert job.status == JobStatus.ABORTED

    job.resume()
    assert job.status == JobStatus.ABORTED  # Not revived
    assert job.is_paused is False  # _paused unchanged (was never paused)
    print("  PASS: abort_then_resume_no_revive")


def test_tick_intervals():
    """Different job types have different tick intervals."""
    signals: list[ExpertSignal] = []
    recon = MockReconJob(
        job_id="j1", task_id="t1",
        config=ReconJobConfig(search_region="full_map", target_type="base", target_owner="enemy"),
        signal_callback=signals.append,
    )
    combat = MockCombatJob(
        job_id="j2", task_id="t1",
        config=CombatJobConfig(target_position=(100, 200), engagement_mode=EngagementMode.ASSAULT),
        signal_callback=signals.append,
    )

    assert recon.tick_interval == 1.0
    assert combat.tick_interval == 0.2
    print("  PASS: tick_intervals")


# --- Run all tests ---

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
