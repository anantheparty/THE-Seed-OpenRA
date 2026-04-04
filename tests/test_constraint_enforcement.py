"""Tests for Constraint enforcement across Experts (T13).

Covers:
  - CONSTRAINT_VIOLATED signal for ESCALATE enforcement
  - ReconJob defend_base: CLAMP filters candidates; ESCALATE emits signal
  - EconomyJob economy_first: CLAMP blocks production; ESCALATE emits signal
  - MovementJob do_not_chase: CLAMP skips move; ESCALATE emits signal
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from models import (
    Constraint,
    ConstraintEnforcement,
    EconomyJobConfig,
    ExpertSignal,
    MovementJobConfig,
    MoveMode,
    ReconJobConfig,
    SignalKind,
)
from openra_api.models import Actor, Location

from experts.recon import ReconJob
from experts.economy import EconomyJob
from experts.movement import MovementJob


# --- Shared helpers ---

def _make_constraint(kind: str, enforcement: ConstraintEnforcement, params: dict) -> Constraint:
    return Constraint(
        constraint_id=f"c_{kind}",
        kind=kind,
        scope="global",
        params=params,
        enforcement=enforcement,
    )


def _provider(constraints: list[Constraint]):
    def _p(scope: str) -> list[Constraint]:
        return [c for c in constraints if c.scope == scope or c.scope == "global"]
    return _p


# --- ReconJob helpers ---

class MockReconGameAPI:
    def __init__(self):
        self.move_calls: list[tuple] = []

    def move_units_by_location(self, actors, location, attack_move=False):
        self.move_calls.append((actors, location, attack_move))


class MockReconWorldModel:
    def __init__(self, actor_pos=(100, 100), building_positions=None, map_size=(2000, 2000)):
        self._actor_pos = actor_pos
        self._buildings = building_positions or [(500, 500)]
        self._map_size = map_size

    def query(self, query_type: str, params: Optional[dict] = None) -> Any:
        if query_type == "map":
            return {"width": self._map_size[0], "height": self._map_size[1]}
        if query_type == "my_actors":
            return {"actors": [{"position": list(p), "category": "building"} for p in self._buildings]}
        if query_type == "actor_by_id":
            actor_id = (params or {}).get("actor_id")
            return {"actor": {"actor_id": actor_id, "position": list(self._actor_pos), "is_alive": True, "hp_ratio": 1.0}}
        if query_type == "enemy_actors":
            return {"actors": []}
        if query_type == "events":
            return {"events": []}
        return {}

    def refresh_health(self):
        return {"stale": False}


def _make_recon_job(constraints=None, actor_pos=(100, 100), building_pos=(500, 500)):
    signals: list[ExpertSignal] = []
    api = MockReconGameAPI()
    wm = MockReconWorldModel(actor_pos=actor_pos, building_positions=[building_pos])
    job = ReconJob(
        job_id="rj1",
        task_id="t1",
        config=ReconJobConfig(
            search_region="enemy_half",
            target_type="base",
            target_owner="enemy",
        ),
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
        constraint_provider=_provider(constraints or []),
    )
    job.on_resource_granted(["actor:57"])
    return job, signals, api, wm


# --- EconomyJob helpers ---

class MockEconomyGameAPI:
    def __init__(self):
        self.produce_calls: list[tuple] = []

    def can_produce(self, unit_type: str) -> bool:
        return True

    def produce(self, unit_type, count, auto_place_building=False):
        self.produce_calls.append((unit_type, count))

    def place_building(self, queue_type, location=None):
        pass

    def manage_production(self, queue_type, action, *, owner_actor_id=None, item_name=None, count=1):
        pass


class MockEconomyWorldModel:
    def __init__(self, queue_type: str = "Infantry"):
        self._queue_type = queue_type

    def query(self, query_type: str, params: Optional[dict] = None) -> Any:
        if query_type == "economy":
            return {"total_credits": 5000, "low_power": False}
        if query_type == "production_queues":
            return {
                self._queue_type: {"queue_type": self._queue_type, "items": [], "has_ready_item": False}
            }
        if query_type == "events":
            return {"events": []}
        if query_type == "my_actors":
            return {"actors": []}
        return {}


def _make_economy_job(unit_type: str, queue_type: str, constraints=None):
    signals: list[ExpertSignal] = []
    api = MockEconomyGameAPI()
    wm = MockEconomyWorldModel(queue_type=queue_type)
    job = EconomyJob(
        job_id="ej1",
        task_id="t1",
        config=EconomyJobConfig(unit_type=unit_type, count=2, queue_type=queue_type, repeat=False),
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
        constraint_provider=_provider(constraints or []),
    )
    # Grant a production queue resource so tick() proceeds past _waiting_reason_for
    job.on_resource_granted([f"queue:{queue_type}"])
    return job, signals, api, wm


# --- MovementJob helpers ---

class MockMovementGameAPI:
    def __init__(self):
        self.move_calls: list[tuple] = []

    def move_units_by_location(self, actors, location, attack_move=False):
        self.move_calls.append((actors, location, attack_move))


class MockMovementWorldModel:
    def __init__(self, actor_pos=(100, 100)):
        self._pos = actor_pos

    def query(self, query_type: str, params: Optional[dict] = None) -> Any:
        if query_type == "actor_by_id":
            return {"actor": {"actor_id": 1, "position": list(self._pos)}}
        return {}


def _make_movement_job(target: tuple, actor_pos=(100, 100), constraints=None):
    signals: list[ExpertSignal] = []
    api = MockMovementGameAPI()
    wm = MockMovementWorldModel(actor_pos=actor_pos)
    job = MovementJob(
        job_id="mj1",
        task_id="t1",
        config=MovementJobConfig(target_position=target, move_mode=MoveMode.MOVE),
        signal_callback=signals.append,
        game_api=api,
        world_model=wm,
        constraint_provider=_provider(constraints or []),
    )
    job.on_resource_granted(["actor:1"])
    return job, signals, api, wm


# ===== RECON TESTS =====

def test_recon_defend_base_clamp_filters_far_candidates():
    """defend_base CLAMP: candidates beyond max_distance from base are removed."""
    # Base at (500, 500); max_distance=300; far candidates (enemy_half) are ~1600,1600
    job, signals, api, wm = _make_recon_job(
        constraints=[_make_constraint("defend_base", ConstraintEnforcement.CLAMP, {"max_distance": 300})],
        building_pos=(500, 500),
    )
    actor = {"position": [100, 100]}
    candidates = [(1600, 400), (1560, 1440), (1200, 1000), (400, 450)]  # last one is close to base
    result = job._apply_defend_base_constraint(candidates, actor)
    # Only (400, 450) is within 300 units of base (500,500)
    assert all(ReconJob._distance(p, (500, 500)) <= 300 for p in result), f"Far candidates not filtered: {result}"
    assert len(result) < len(candidates), "No candidates were filtered"
    print("  PASS: recon_defend_base_clamp_filters_far_candidates")


def test_recon_defend_base_escalate_emits_signal():
    """defend_base ESCALATE: emits CONSTRAINT_VIOLATED signal when far candidates exist."""
    job, signals, api, wm = _make_recon_job(
        constraints=[_make_constraint("defend_base", ConstraintEnforcement.ESCALATE, {"max_distance": 100})],
        building_pos=(500, 500),
    )
    actor = {"position": [100, 100]}
    far_candidates = [(1600, 400), (1560, 1440)]
    job._apply_defend_base_constraint(far_candidates, actor)

    violated = [s for s in signals if s.kind == SignalKind.CONSTRAINT_VIOLATED]
    assert len(violated) >= 1, "Expected CONSTRAINT_VIOLATED signal"
    assert violated[0].data.get("constraint_kind") == "defend_base" or \
           violated[0].expert_state.get("constraint_kind") == "defend_base"
    print("  PASS: recon_defend_base_escalate_emits_signal")


def test_recon_no_constraint_all_candidates_pass():
    """No defend_base constraint: all candidates returned unchanged."""
    job, signals, api, wm = _make_recon_job()
    actor = {"position": [100, 100]}
    candidates = [(1600, 400), (1560, 1440)]
    result = job._apply_defend_base_constraint(candidates, actor)
    assert result == candidates
    print("  PASS: recon_no_constraint_all_candidates_pass")


# ===== ECONOMY TESTS =====

def test_economy_first_clamp_blocks_non_economy():
    """economy_first CLAMP: blocks Infantry production, no produce calls."""
    job, signals, api, wm = _make_economy_job(
        unit_type="e1",
        queue_type="Infantry",
        constraints=[_make_constraint("economy_first", ConstraintEnforcement.CLAMP, {})],
    )
    job.tick()
    assert len(api.produce_calls) == 0, "Produce should be blocked by economy_first constraint"
    print("  PASS: economy_first_clamp_blocks_non_economy")


def test_economy_first_escalate_emits_signal():
    """economy_first ESCALATE: emits CONSTRAINT_VIOLATED but allows production."""
    job, signals, api, wm = _make_economy_job(
        unit_type="e1",
        queue_type="Infantry",
        constraints=[_make_constraint("economy_first", ConstraintEnforcement.ESCALATE, {})],
    )
    job.tick()
    violated = [s for s in signals if s.kind == SignalKind.CONSTRAINT_VIOLATED]
    assert len(violated) >= 1, "Expected CONSTRAINT_VIOLATED signal"
    assert violated[0].expert_state.get("constraint_kind") == "economy_first"
    # ESCALATE notifies but does not block
    assert len(api.produce_calls) == 1, "ESCALATE should not block production"
    print("  PASS: economy_first_escalate_emits_signal")


def test_economy_first_allows_economy_units():
    """economy_first constraint does NOT block economy units (proc/harv)."""
    job, signals, api, wm = _make_economy_job(
        unit_type="proc",
        queue_type="Building",
        constraints=[_make_constraint("economy_first", ConstraintEnforcement.CLAMP, {})],
    )
    job.tick()
    assert len(api.produce_calls) == 1, "Economy units should not be blocked"
    print("  PASS: economy_first_allows_economy_units")


def test_economy_first_no_constraint_produces_normally():
    """Without economy_first constraint, Infantry production proceeds."""
    job, signals, api, wm = _make_economy_job(unit_type="e1", queue_type="Infantry")
    job.tick()
    assert len(api.produce_calls) == 1
    print("  PASS: economy_first_no_constraint_produces_normally")


# ===== MOVEMENT TESTS =====

def test_movement_do_not_chase_clamp_skips_far_move():
    """do_not_chase CLAMP: move is not issued when target exceeds max_distance."""
    # Actor at (100,100), target at (2000,2000) — distance ~2687, max=500
    job, signals, api, wm = _make_movement_job(
        target=(2000, 2000),
        actor_pos=(100, 100),
        constraints=[_make_constraint("do_not_chase", ConstraintEnforcement.CLAMP, {"max_distance": 500})],
    )
    job.tick()
    assert len(api.move_calls) == 0, "Move should be skipped when target exceeds max_distance (CLAMP)"
    print("  PASS: movement_do_not_chase_clamp_skips_far_move")


def test_movement_do_not_chase_escalate_emits_signal():
    """do_not_chase ESCALATE: emits CONSTRAINT_VIOLATED and still moves."""
    job, signals, api, wm = _make_movement_job(
        target=(2000, 2000),
        actor_pos=(100, 100),
        constraints=[_make_constraint("do_not_chase", ConstraintEnforcement.ESCALATE, {"max_distance": 500})],
    )
    job.tick()
    violated = [s for s in signals if s.kind == SignalKind.CONSTRAINT_VIOLATED]
    assert len(violated) >= 1, "Expected CONSTRAINT_VIOLATED signal"
    assert violated[0].expert_state.get("constraint_kind") == "do_not_chase"
    # ESCALATE notifies but does not block
    assert len(api.move_calls) == 1, "ESCALATE should not block the move"
    print("  PASS: movement_do_not_chase_escalate_emits_signal")


def test_movement_do_not_chase_allows_close_target():
    """do_not_chase constraint allows movement within max_distance."""
    # Actor at (100,100), target at (200,200) — distance ~141, max=500
    job, signals, api, wm = _make_movement_job(
        target=(200, 200),
        actor_pos=(100, 100),
        constraints=[_make_constraint("do_not_chase", ConstraintEnforcement.CLAMP, {"max_distance": 500})],
    )
    job.tick()
    assert len(api.move_calls) == 1, "Close target should not be blocked"
    print("  PASS: movement_do_not_chase_allows_close_target")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Constraint Enforcement tests...\n")

    test_recon_defend_base_clamp_filters_far_candidates()
    test_recon_defend_base_escalate_emits_signal()
    test_recon_no_constraint_all_candidates_pass()
    test_economy_first_clamp_blocks_non_economy()
    test_economy_first_escalate_emits_signal()
    test_economy_first_allows_economy_units()
    test_economy_first_no_constraint_produces_normally()
    test_movement_do_not_chase_clamp_skips_far_move()
    test_movement_do_not_chase_escalate_emits_signal()
    test_movement_do_not_chase_allows_close_target()

    print("\nAll 10 Constraint Enforcement tests passed!")
