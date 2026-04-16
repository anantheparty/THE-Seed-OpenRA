"""Tests for MovementExpert + DeployExpert."""

from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, List, Optional

from models import (
    DeployJobConfig,
    ExpertSignal,
    JobStatus,
    MovementJobConfig,
    MoveMode,
    SignalKind,
)
from experts.movement import MovementExpert, MovementJob
from experts.deploy import DeployExpert, DeployJob, _VERIFY_TIMEOUT_S
from openra_api.models import Actor, TargetsQueryParam


# --- Mocks ---

class MockGameAPI:
    """Mock GameAPI supporting move, deploy, query_actor, and get_actor_by_id."""

    def __init__(self, deploy_fail: bool = False):
        self.move_calls: list[dict] = []
        self.path_move_calls: list[dict] = []
        self.deploy_calls: list[dict] = []
        self._deploy_fail = deploy_fail
        # actor_id -> Actor (or None to simulate disappeared)
        self._actors: dict[int, Optional[Actor]] = {}

    def move_units_by_location(self, actors, location, attack_move=False):
        self.move_calls.append({
            "actor_ids": [a.actor_id for a in actors],
            "position": (location.x, location.y),
            "attack_move": attack_move,
        })

    def move_units_by_path(self, actors, path, attack_move=False):
        self.path_move_calls.append({
            "actor_ids": [a.actor_id for a in actors],
            "path": [(point.x, point.y) for point in path],
            "attack_move": attack_move,
        })

    def deploy_units(self, actors):
        if self._deploy_fail:
            raise RuntimeError("Deploy blocked")
        self.deploy_calls.append({"actor_ids": [a.actor_id for a in actors]})

    def attack_target(self, attacker, target):
        return True

    def query_actor(self, query_params: TargetsQueryParam) -> List[Actor]:
        """Return actors whose type is in query_params.type (faction ignored in mock)."""
        if query_params.type is None:
            return list(a for a in self._actors.values() if a is not None)
        return [
            a for a in self._actors.values()
            if a is not None and a.type in query_params.type
        ]

    def get_actor_by_id(self, actor_id: int) -> Optional[Actor]:
        return self._actors.get(actor_id)

    def add_actor(self, actor_id: int, actor_type: str) -> Actor:
        a = Actor(actor_id=actor_id)
        a.type = actor_type
        self._actors[actor_id] = a
        return a

    def remove_actor(self, actor_id: int) -> None:
        self._actors.pop(actor_id, None)


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


def test_movement_path_mode_uses_path_api():
    """Path-aware movement uses GameAPI.move_units_by_path and still targets final waypoint."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({57: (500, 500)})
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(120, 260),
        path=[(400, 420), (250, 300), (120, 260)],
        move_mode=MoveMode.MOVE,
        arrival_radius=8,
    )
    job = MovementJob(
        job_id="j_path", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:57"])

    job.do_tick()
    assert api.move_calls == []
    assert api.path_move_calls == [{
        "actor_ids": [57],
        "path": [(400, 420), (250, 300), (120, 260)],
        "attack_move": False,
    }]

    wm.set_position(57, (121, 259))
    job.do_tick()
    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].result == "succeeded"
    print("  PASS: movement_path_mode_uses_path_api")


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


def test_movement_progress_includes_explicit_group_truth() -> None:
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({101: (500, 500), 102: (520, 520)})
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(100, 200),
        move_mode=MoveMode.RETREAT,
        arrival_radius=10,
        actor_ids=[101, 102, 103, 104, 105],
    )
    job = MovementJob(
        job_id="j_progress", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:101", "actor:102"])
    job._tick_count = 9

    job.do_tick()

    progress = [signal for signal in signals if signal.kind == SignalKind.PROGRESS]
    assert progress
    assert "group=2/5" in progress[0].summary
    assert progress[0].data["source_expert"] == "MovementExpert"
    assert progress[0].data["explicit_group"] is True
    assert progress[0].data["requested_total"] == 5
    assert progress[0].data["bound_count"] == 2
    assert progress[0].data["missing_count"] == 3
    print("  PASS: movement_progress_includes_explicit_group_truth")


def test_movement_risk_alert_includes_explicit_group_truth() -> None:
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({101: (500, 500), 102: (520, 520)})
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(100, 200),
        move_mode=MoveMode.RETREAT,
        arrival_radius=10,
        actor_ids=[101, 102, 103, 104, 105],
    )
    job = MovementJob(
        job_id="j_risk", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api, world_model=wm,
    )
    job.on_resource_granted(["actor:101", "actor:102"])
    job._stuck_threshold = 1
    job._last_centroid = (510, 510)

    job.do_tick()

    risk = [signal for signal in signals if signal.kind == SignalKind.RISK_ALERT]
    assert risk
    assert "group=2/5" in risk[0].summary
    assert risk[0].data["source_expert"] == "MovementExpert"
    assert risk[0].data["explicit_group"] is True
    assert risk[0].data["requested_total"] == 5
    assert risk[0].data["bound_count"] == 2
    assert risk[0].data["missing_count"] == 3
    print("  PASS: movement_risk_alert_includes_explicit_group_truth")


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


def test_movement_explicit_group_full_completion_uses_configured_actor_ids() -> None:
    """Explicit-group completion must not shrink to the currently bound subset."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({
        57: (100, 200),
        58: (102, 198),
        59: (170, 260),
    })
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(100, 200),
        move_mode=MoveMode.RETREAT,
        arrival_radius=10,
        actor_ids=[57, 58, 59],
    )
    job = MovementJob(
        job_id="j_full_explicit",
        task_id="t1",
        config=config,
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
    )
    job.on_resource_granted(["actor:57", "actor:58"])

    job.do_tick()

    assert job.status == JobStatus.RUNNING
    assert signals == []
    print("  PASS: movement_explicit_group_full_completion_uses_configured_actor_ids")


def test_movement_partial_group_can_complete_without_full_arrival() -> None:
    """Partial-group movement should succeed once the minimum safe package arrives."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({
        57: (100, 200),
        58: (102, 198),
        59: (170, 260),
    })
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(100, 200),
        move_mode=MoveMode.RETREAT,
        arrival_radius=10,
        wait_for_full_group=False,
        min_complete_count=2,
        actor_ids=[57, 58, 59],
    )
    job = MovementJob(
        job_id="j_partial",
        task_id="t1",
        config=config,
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
    )
    job.on_resource_granted(["actor:57", "actor:58", "actor:59"])

    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].result == "succeeded"
    print("  PASS: movement_partial_group_can_complete_without_full_arrival")


def test_movement_explicit_partial_group_threshold_uses_configured_group_size() -> None:
    """Explicit partial-group completion should not shrink its threshold with lost bindings."""
    signals: list[ExpertSignal] = []
    wm = MockWorldModel({
        57: (100, 200),
        58: (102, 198),
        59: (170, 260),
    })
    api = MockGameAPI()

    config = MovementJobConfig(
        target_position=(100, 200),
        move_mode=MoveMode.RETREAT,
        arrival_radius=10,
        wait_for_full_group=False,
        actor_ids=[57, 58, 59],
    )
    job = MovementJob(
        job_id="j_partial_explicit",
        task_id="t1",
        config=config,
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
    )
    job.on_resource_granted(["actor:57", "actor:58"])

    job.do_tick()
    assert job.status == JobStatus.RUNNING
    assert signals == []

    job.on_resource_granted(["actor:59"])
    wm.set_position(59, (101, 201))

    job.do_tick()
    assert job.status == JobStatus.SUCCEEDED
    assert signals[-1].data["actors_arrived"] == [57, 58, 59]
    print("  PASS: movement_explicit_partial_group_threshold_uses_configured_group_size")


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

def test_deploy_sends_command_then_waits():
    """Tick 1: deploy command sent, job stays RUNNING. No signal yet."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()

    config = DeployJobConfig(actor_id=99, target_position=(500, 400), building_type="ConstructionYard")
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()

    assert job.status == JobStatus.RUNNING
    assert job._phase == "verifying"
    assert len(api.deploy_calls) == 1
    assert len(signals) == 0  # No signal until verified
    print("  PASS: deploy_sends_command_then_waits")


def test_deploy_success_on_cy_appear():
    """After deploy command, next tick with new CY → SUCCEEDED."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()

    config = DeployJobConfig(actor_id=99, target_position=(500, 400), building_type="ConstructionYard")
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    # Tick 1: deploy command sent
    job.do_tick()
    assert job.status == JobStatus.RUNNING

    # Simulate: CY appeared in game
    api.add_actor(200, "建造厂")

    # Tick 2: verify → CY found → SUCCEEDED
    job.do_tick()

    assert job.status == JobStatus.SUCCEEDED
    assert len(signals) == 1
    assert signals[0].result == "succeeded"
    assert signals[0].data["yard_actor_id"] == 200
    assert signals[0].data["actor_id"] == 99
    print("  PASS: deploy_success_on_cy_appear")


def test_deploy_success_ignores_pre_existing_cy():
    """A CY that existed before deploy does not count as verification."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()
    api.add_actor(100, "建造厂")  # Already exists before deploy

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    # Tick 1: deploy — records pre-deploy CY id=100
    job.do_tick()
    assert job._pre_deploy_yard_ids == {100}

    # Tick 2: verify — same CY (id=100) present, no new one → still waiting
    job.do_tick()
    assert job.status == JobStatus.RUNNING

    # New CY id=201 appears
    api.add_actor(201, "建造厂")
    job.do_tick()
    assert job.status == JobStatus.SUCCEEDED
    assert signals[0].data["yard_actor_id"] == 201
    print("  PASS: deploy_success_ignores_pre_existing_cy")


def test_deploy_timeout_no_yard():
    """After 5s with no CY → FAILED with deploy_command_sent_but_no_yard_appeared."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    # Tick 1: deploy command sent
    job.do_tick()
    assert job.status == JobStatus.RUNNING

    # Fake the deploy time to be past timeout
    job._deploy_sent_at -= _VERIFY_TIMEOUT_S + 0.1

    # Tick 2: elapsed > timeout → FAILED
    job.do_tick()

    assert job.status == JobStatus.FAILED
    assert len(signals) == 1
    assert signals[0].result == "failed"
    assert signals[0].data["reason"] == "deploy_command_sent_but_no_yard_appeared"
    assert signals[0].data["actor_id"] == 99
    print("  PASS: deploy_timeout_no_yard")


def test_deploy_failure_on_api_exception():
    """GameAPI exception on deploy_units → immediate FAILED with error."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI(deploy_fail=True)

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()

    assert job.status == JobStatus.FAILED
    assert signals[0].result == "failed"
    assert "Deploy blocked" in signals[0].data["error"]
    print("  PASS: deploy_failure_on_api_exception")


def test_deploy_exception_graceful():
    """DeployJob handles unexpected GameAPI exception gracefully."""
    signals: list[ExpertSignal] = []

    class FailingAPI:
        def query_actor(self, query_params): return []
        def get_actor_by_id(self, actor_id): return None
        def deploy_units(self, actors):
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
    print("  PASS: deploy_exception_graceful")


def test_deploy_no_double_command():
    """Deploy command fires exactly once; verifying ticks don't re-deploy."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()

    config = DeployJobConfig(actor_id=99, target_position=(500, 400))
    job = DeployJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append, game_api=api,
    )

    job.do_tick()  # Deploy sent
    job.do_tick()  # Verifying
    job.do_tick()  # Verifying

    assert len(api.deploy_calls) == 1  # Only one deploy command ever sent
    print("  PASS: deploy_no_double_command")


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
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
