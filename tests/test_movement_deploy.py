"""Tests for MovementExpert + DeployExpert."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from models import (
    DeployJobConfig,
    ExpertSignal,
    JobStatus,
    MovementJobConfig,
    MoveMode,
    SignalKind,
)
from experts.movement import MovementExpert, MovementJob
from experts.deploy import DeployExpert, DeployJob


# --- Mocks ---

class MockGameAPI:
    def __init__(self, deploy_result: bool = True):
        self.move_calls: list[dict] = []
        self.deploy_calls: list[int] = []
        self._deploy_result = deploy_result

    def move_actors(self, actor_ids, position, *, attack_move=False):
        self.move_calls.append({"actor_ids": actor_ids, "position": position, "attack_move": attack_move})

    def deploy_actor(self, actor_id):
        self.deploy_calls.append(actor_id)
        return self._deploy_result


class MockWorldModel:
    def __init__(self, actor_positions: Optional[dict[int, tuple[int, int]]] = None):
        self._positions = actor_positions or {}

    def query(self, query_type, params=None):
        if query_type == "actor_by_id":
            aid = params["actor_id"]
            pos = self._positions.get(aid)
            if pos is None:
                return {"actor": None}
            return {"actor": {"actor_id": aid, "position": list(pos)}}
        return {}

    def set_position(self, actor_id: int, position: tuple[int, int]):
        self._positions[actor_id] = position


# --- MovementExpert Tests ---

def test_movement_arrival_detection():
    """MovementJob detects arrival when all actors are within radius."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (100, 200)})
    api = MockGameAPI()

    config = MovementJobConfig(target_position=(100, 200), move_mode=MoveMode.MOVE, arrival_radius=5)
    job = MovementJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57"])

    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert len(signals) == 1
    assert signals[0].kind == SignalKind.TASK_COMPLETE
    assert signals[0].result == "succeeded"
    print("  PASS: movement_arrival_detection")


def test_movement_moves_then_arrives():
    """MovementJob issues move command, then detects arrival on later tick."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (500, 500)})  # Far from target
    api = MockGameAPI()

    config = MovementJobConfig(target_position=(100, 200), move_mode=MoveMode.MOVE, arrival_radius=10)
    job = MovementJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57"])

    # Tick 1: not arrived, issues move
    job.do_tick()
    assert job.status == JobStatus.RUNNING
    assert len(api.move_calls) == 1
    assert api.move_calls[0]["attack_move"] is False

    # Simulate actor arriving
    wm.set_position(57, (102, 198))

    # Tick 2: arrived
    job.do_tick()
    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].result == "succeeded"
    print("  PASS: movement_moves_then_arrives")


def test_movement_attack_move():
    """attack_move mode sets attack_move=True on GameAPI call."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (500, 500)})
    api = MockGameAPI()

    config = MovementJobConfig(target_position=(100, 200), move_mode=MoveMode.ATTACK_MOVE, arrival_radius=10)
    job = MovementJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57"])

    job.do_tick()
    assert api.move_calls[0]["attack_move"] is True
    print("  PASS: movement_attack_move")


def test_movement_retreat_mode():
    """retreat mode uses attack_move=True for defensive movement."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (500, 500)})
    api = MockGameAPI()

    config = MovementJobConfig(target_position=(100, 200), move_mode=MoveMode.RETREAT, arrival_radius=10)
    job = MovementJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57"])

    job.do_tick()
    assert api.move_calls[0]["attack_move"] is True
    print("  PASS: movement_retreat_mode")


def test_movement_multiple_actors():
    """All actors must arrive for completion."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (500, 500), 58: (100, 200)})  # 58 arrived, 57 not
    api = MockGameAPI()

    config = MovementJobConfig(target_position=(100, 200), arrival_radius=10)
    job = MovementJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57", "actor:58"])

    job.do_tick()
    assert job.status == JobStatus.RUNNING  # 57 not arrived yet

    wm.set_position(57, (103, 198))
    job.do_tick()
    assert job.status == JobStatus.SUCCEEDED
    print("  PASS: movement_multiple_actors")


def test_movement_expert_creates_job():
    """MovementExpert factory creates MovementJob instances."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()
    wm = MockWorldModel()

    expert = MovementExpert(game_api=api, world_model=wm)
    assert expert.expert_type == "MovementExpert"

    config = MovementJobConfig(target_position=(100, 200))
    job = expert.create_job("t1", config, signals.append)
    assert isinstance(job, MovementJob)
    assert job.expert_type == "MovementExpert"
    assert job.job_id.startswith("j_")
    print("  PASS: movement_expert_creates_job")


# --- DeployExpert Tests ---

def test_deploy_success():
    """DeployJob succeeds on first tick."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI(deploy_result=True)

    config = DeployJobConfig(actor_id=99, target_position=(500, 400), building_type="ConstructionYard")
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert len(signals) == 1
    assert signals[0].result == "succeeded"
    assert signals[0].data["actor_id"] == 99
    assert signals[0].data["building_type"] == "ConstructionYard"
    assert api.deploy_calls == [99]
    print("  PASS: deploy_success")


def test_deploy_failure():
    """DeployJob fails when GameAPI returns False."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI(deploy_result=False)

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()

    assert job.status == JobStatus.FAILED
    assert signals[0].result == "failed"
    print("  PASS: deploy_failure")


def test_deploy_exception():
    """DeployJob handles GameAPI exception gracefully."""
    signals: list[ExpertSignal] = []

    class FailingAPI:
        def deploy_actor(self, actor_id):
            raise ConnectionError("GameAPI disconnected")

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=FailingAPI(),
    )

    job.do_tick()

    assert job.status == JobStatus.FAILED
    assert signals[0].result == "failed"
    assert "GameAPI disconnected" in signals[0].data["error"]
    print("  PASS: deploy_exception")


def test_deploy_only_fires_once():
    """DeployJob only deploys on first tick, subsequent ticks are no-op."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI(deploy_result=True)

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()
    job.do_tick()  # Should be no-op
    job.do_tick()  # Should be no-op

    assert len(api.deploy_calls) == 1
    assert len(signals) == 1
    print("  PASS: deploy_only_fires_once")


def test_deploy_expert_creates_job():
    """DeployExpert factory creates DeployJob instances."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()

    expert = DeployExpert(game_api=api)
    assert expert.expert_type == "DeployExpert"

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = expert.create_job("t1", config, signals.append)
    assert isinstance(job, DeployJob)
    assert job.expert_type == "DeployExpert"
    print("  PASS: deploy_expert_creates_job")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running MovementExpert + DeployExpert tests...\n")

    # Movement
    test_movement_arrival_detection()
    test_movement_moves_then_arrives()
    test_movement_attack_move()
    test_movement_retreat_mode()
    test_movement_multiple_actors()
    test_movement_expert_creates_job()

    # Deploy
    test_deploy_success()
    test_deploy_failure()
    test_deploy_exception()
    test_deploy_only_fires_once()
    test_deploy_expert_creates_job()

    print(f"\nAll 11 tests passed!")
