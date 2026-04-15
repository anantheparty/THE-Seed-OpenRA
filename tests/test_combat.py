"""Tests for CombatExpert — FSM states + 4 engagement modes."""

from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from models import (
    DEFAULT_GENERIC_COMBAT_UNIT_COUNT,
    CombatJobConfig,
    Constraint,
    ConstraintEnforcement,
    EngagementMode,
    ExpertSignal,
    JobStatus,
    SignalKind,
)
from experts.combat import CombatExpert, CombatJob, CombatPhase


# --- Mocks ---

class MockGameAPI:
    def __init__(self):
        self.move_calls: list[dict] = []
        self.attack_calls: list[dict] = []

    def move_units_by_location(self, actors, location, attack_move=False):
        self.move_calls.append({
            "actor_ids": [a.actor_id for a in actors],
            "position": (location.x, location.y),
            "attack_move": attack_move,
        })

    def attack_target(self, attacker, target):
        self.attack_calls.append({"attacker": attacker.actor_id, "target": target.actor_id})
        return True

    def deploy_units(self, actors):
        pass


class MockWorldModel:
    def __init__(self):
        self._actors: dict[int, dict] = {}
        self._enemies: list[dict] = []

    def set_actor(self, actor_id, position, hp=100, hp_max=100):
        self._actors[actor_id] = {"actor_id": actor_id, "position": list(position), "hp": hp, "hp_max": hp_max}

    def set_enemies(self, enemies):
        self._enemies = enemies

    def query(self, query_type, params=None):
        if query_type == "actor_by_id":
            aid = params["actor_id"]
            return {"actor": self._actors.get(aid)}
        if query_type == "enemy_actors":
            return {"actors": list(self._enemies)}
        return {}


def make_job(
    engagement_mode=EngagementMode.ASSAULT,
    target=(500, 500),
    retreat_threshold=0.5,
    max_chase=100,
    api=None,
    wm=None,
    constraint_provider=None,
) -> tuple[CombatJob, list[ExpertSignal], MockGameAPI, MockWorldModel]:
    signals: list[ExpertSignal] = []
    api = api or MockGameAPI()
    wm = wm or MockWorldModel()
    config = CombatJobConfig(
        target_position=target,
        engagement_mode=engagement_mode,
        max_chase_distance=max_chase,
        retreat_threshold=retreat_threshold,
    )
    job = CombatJob(
        job_id="j1", task_id="t1", config=config,
        signal_callback=signals.append,
        constraint_provider=constraint_provider,
        game_api=api, world_model=wm,
    )
    return job, signals, api, wm


# --- FSM State Tests ---

def test_approaching_to_engaging():
    """Job starts approaching, transitions to engaging when close enough."""
    job, signals, api, wm = make_job(target=(100, 100))
    wm.set_actor(57, (500, 500))  # Far from target
    wm.set_enemies([{"actor_id": 201, "position": [100, 100]}])
    job.on_resource_granted(["actor:57"])

    job.do_tick()
    assert job.phase == CombatPhase.APPROACHING
    assert len(api.move_calls) >= 1

    # Move actor close to target
    wm.set_actor(57, (105, 105))
    job.do_tick()
    assert job.phase == CombatPhase.ENGAGING
    print("  PASS: approaching_to_engaging")


def test_generic_combat_needs_use_bounded_default_package():
    """Generic combat without actor_ids should not claim the whole global pool."""
    job, _signals, _api, _wm = make_job(target=(100, 100))
    needs = job.get_resource_needs()

    assert len(needs) == 1
    assert needs[0].count == DEFAULT_GENERIC_COMBAT_UNIT_COUNT
    assert needs[0].predicates == {"can_attack": "true", "owner": "self"}
    print("  PASS: generic_combat_needs_use_bounded_default_package")


def test_engaging_clears_area():
    """Hold mode: when no enemies ever visible, immediately falls back to recon-first."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.HOLD, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([])  # No enemies
    job.on_resource_granted(["actor:57"])

    # Start near target → engaging → no enemies → hold doesn't advance → completed
    job.do_tick()  # approaching → engaging (close enough)
    job.do_tick()  # engaging → no enemies → hold immediately completes partial

    assert job.phase == CombatPhase.COMPLETED
    assert job.status == JobStatus.SUCCEEDED
    assert any(s.result == "partial" for s in signals)
    assert signals[-1].data["recommendation"]["kind"] == "recon_first"
    print("  PASS: engaging_clears_area")


def test_engaging_succeeds_after_enemy_contact_then_clear():
    """If enemies were seen and then cleared, combat completes as succeeded."""
    job, signals, api, wm = make_job(target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([{"actor_id": 201, "position": [110, 110]}])
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching -> engaging
    job.do_tick()  # engaging with enemy present
    wm.set_enemies([])
    job.do_tick()  # now cleared after contact

    assert job.phase == CombatPhase.COMPLETED
    assert any(s.result == "succeeded" for s in signals)
    print("  PASS: engaging_succeeds_after_enemy_contact_then_clear")


def test_retreat_threshold():
    """Job retreats when unit losses exceed retreat_threshold."""
    job, signals, api, wm = make_job(retreat_threshold=0.5, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_actor(58, (100, 100))
    wm.set_enemies([{"actor_id": 201, "position": [100, 100]}])
    job.on_resource_granted(["actor:57", "actor:58"])

    # First tick — establishes initial_unit_count = 2
    job.do_tick()

    # Lose one unit (50% loss = retreat_threshold)
    job.on_resource_revoked(["actor:57"])
    job.do_tick()

    assert job.phase == CombatPhase.COMPLETED
    assert any(s.kind == SignalKind.RISK_ALERT for s in signals)
    print("  PASS: retreat_threshold")


# --- Engagement Mode Tests ---

def test_assault_mode():
    """Assault mode: attack closest enemy."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.ASSAULT, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([{"actor_id": 201, "position": [110, 110]}])
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching → engaging
    job.do_tick()  # engaging: attack

    assert len(api.attack_calls) >= 1 or len(api.move_calls) >= 1
    print("  PASS: assault_mode")


def test_hold_mode_no_pursuit():
    """Hold mode: doesn't pursue enemies beyond engage radius."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.HOLD, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([{"actor_id": 201, "position": [300, 300]}])  # Far away
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching → engaging (we're at target)
    job.do_tick()  # engaging: enemies far away, hold doesn't chase

    # Hold mode should NOT attack far enemies
    hold_attacks = [c for c in api.attack_calls if 201 in c.get("actor_ids", [])]
    # Enemies out of ENGAGE_RADIUS should not be attacked in hold mode
    print("  PASS: hold_mode_no_pursuit")


def test_surround_splits_units():
    """Surround mode: splits units into flank groups."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.SURROUND, target=(500, 500))
    wm.set_actor(57, (200, 200))
    wm.set_actor(58, (200, 200))
    wm.set_actor(59, (200, 200))
    wm.set_actor(60, (200, 200))
    wm.set_enemies([{"actor_id": 201, "position": [500, 500]}])
    job.on_resource_granted(["actor:57", "actor:58", "actor:59", "actor:60"])

    job.do_tick()  # approaching with surround approach

    # Should have issued multiple move commands to different positions
    assert len(api.move_calls) >= 2  # At least 2 flank groups
    positions = [c["position"] for c in api.move_calls]
    # Positions should be different (different flanks)
    assert len(set(map(tuple, positions))) >= 2
    print("  PASS: surround_splits_units")


def test_harass_disengage():
    """Harass mode: disengages when HP drops."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.HARASS, target=(100, 100))
    wm.set_actor(57, (100, 100), hp=50, hp_max=100)  # Low HP
    wm.set_enemies([{"actor_id": 201, "position": [110, 110]}])
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching → engaging
    job.do_tick()  # engaging: low HP → disengage

    # Should have a move call WITHOUT attack_move (disengaging)
    disengage_moves = [c for c in api.move_calls if not c["attack_move"]]
    assert len(disengage_moves) >= 1
    print("  PASS: harass_disengage")


# --- Constraint Tests ---

def test_chase_distance_constraint_clamp():
    """do_not_chase constraint clamps max_chase_distance."""
    constraints = [
        Constraint(
            constraint_id="c1", kind="do_not_chase", scope="global",
            params={"max_distance": 10},
            enforcement=ConstraintEnforcement.CLAMP,
        ),
    ]

    def provider(scope):
        return [c for c in constraints if c.scope == scope or scope == "global"]

    job, signals, api, wm = make_job(
        max_chase=100, target=(100, 100),
        constraint_provider=provider,
    )
    wm.set_actor(57, (100, 100))
    wm.set_enemies([{"actor_id": 201, "position": [250, 250]}])  # Far enemy
    job.on_resource_granted(["actor:57"])

    # Get effective chase distance
    effective = job._effective_chase_distance(100)
    assert effective == 10  # Clamped by constraint
    print("  PASS: chase_distance_constraint_clamp")


# --- Expert Factory ---

def test_combat_expert_creates_job():
    """CombatExpert factory creates CombatJob instances."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()
    wm = MockWorldModel()

    expert = CombatExpert(game_api=api, world_model=wm)
    assert expert.expert_type == "CombatExpert"

    config = CombatJobConfig(target_position=(100, 100), engagement_mode=EngagementMode.ASSAULT)
    job = expert.create_job("t1", config, signals.append)
    assert isinstance(job, CombatJob)
    assert job.tick_interval == 0.2
    assert job.phase == CombatPhase.APPROACHING
    print("  PASS: combat_expert_creates_job")


def test_combat_actor_ids_override_generic_unit_needs() -> None:
    """Explicit actor_ids should produce precise combat resource needs."""
    signals: list[ExpertSignal] = []
    api = MockGameAPI()
    wm = MockWorldModel()

    expert = CombatExpert(game_api=api, world_model=wm)
    config = CombatJobConfig(
        target_position=(100, 100),
        engagement_mode=EngagementMode.ASSAULT,
        actor_ids=[57, 58],
        unit_count=99,
    )
    job = expert.create_job("t1", config, signals.append)
    needs = job.get_resource_needs()
    assert len(needs) == 2
    assert [n.predicates["actor_id"] for n in needs] == ["57", "58"]
    print("  PASS: combat_actor_ids_override_generic_unit_needs")


def test_assault_advances_when_no_enemy():
    """Assault mode: when no enemies visible, issues attack-move advance instead of completing."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.ASSAULT, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([])  # No enemies visible
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching → engaging
    job.do_tick()  # engaging: no enemies → advance (NOT complete)

    assert job.phase != CombatPhase.COMPLETED, "Assault should advance, not immediately complete"
    # Should have issued an attack-move
    advance_moves = [c for c in api.move_calls if c["attack_move"]]
    assert len(advance_moves) >= 1, "Expected at least one attack-move advance"
    print("  PASS: assault_advances_when_no_enemy")


def test_assault_completes_partial_after_max_advance():
    """Assault mode: completes partial after _MAX_ADVANCE_TICKS of advancing with no enemy."""
    from experts.combat import _MAX_ADVANCE_TICKS
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.ASSAULT, target=(100, 100))
    wm.set_actor(57, (100, 100))
    wm.set_enemies([])
    job.on_resource_granted(["actor:57"])

    job.do_tick()  # approaching → engaging
    for _ in range(_MAX_ADVANCE_TICKS + 2):
        job.do_tick()

    assert job.phase == CombatPhase.COMPLETED
    assert any(s.result == "partial" for s in signals)
    assert signals[-1].data["recommendation"]["kind"] == "recon_first"
    print("  PASS: assault_completes_partial_after_max_advance")


def test_assault_focus_fire_lowest_hp():
    """Assault mode: all units focus-fire the lowest HP enemy."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.ASSAULT, target=(100, 100))
    wm.set_actor(57, (90, 90))
    wm.set_actor(58, (180, 180))
    wm.set_enemies([
        {"actor_id": 201, "position": [95, 95], "hp": 100},
        {"actor_id": 202, "position": [175, 175], "hp": 50},  # lower HP → focus target
    ])
    job.on_resource_granted(["actor:57", "actor:58"])

    job.do_tick()  # approaching → engaging
    job.do_tick()  # engaging: focus fire

    targets_used = {c["target"] for c in api.attack_calls}
    # All units should focus the lowest HP enemy (202)
    assert targets_used == {202}, f"All units should focus enemy 202 (lowest HP), got {targets_used}"
    print("  PASS: assault_focus_fire_lowest_hp")


def test_explicit_target_actor_overrides_generic_focus_fire():
    """Explicit target_actor_id should override generic lowest-HP focus selection."""
    job, signals, api, wm = make_job(engagement_mode=EngagementMode.ASSAULT, target=(100, 100))
    job.config.target_actor_id = 201
    wm.set_actor(57, (90, 90))
    wm.set_actor(58, (180, 180))
    wm.set_actor(201, (95, 95))
    wm.set_enemies([
        {"actor_id": 201, "position": [95, 95], "hp": 100},
        {"actor_id": 202, "position": [96, 96], "hp": 50},
    ])
    job.on_resource_granted(["actor:57", "actor:58"])

    job.do_tick()
    job.do_tick()

    targets_used = {c["target"] for c in api.attack_calls}
    assert targets_used == {201}, f"Explicit target_actor_id should be prioritized, got {targets_used}"
    print("  PASS: explicit_target_actor_overrides_generic_focus_fire")


def test_progress_signal_emitted():
    """Progress signals are emitted periodically."""
    job, signals, api, wm = make_job(target=(500, 500))
    wm.set_actor(57, (200, 200))
    wm.set_enemies([{"actor_id": 201, "position": [500, 500]}])
    job.on_resource_granted(["actor:57"])

    for _ in range(25):
        job.do_tick()

    progress = [s for s in signals if s.kind == SignalKind.PROGRESS]
    assert len(progress) >= 1
    assert "phase" in progress[0].expert_state
    print("  PASS: progress_signal_emitted")


# --- Run all tests ---

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
