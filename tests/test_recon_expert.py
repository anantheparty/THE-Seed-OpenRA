"""Tests for ReconExpert and ReconJob — random-ray exploration algorithm."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import JobStatus, ResourceKind, SignalKind, ReconJobConfig
from experts.recon import ReconExpert, ReconJob, _ScoutState, _is_explored_cell, _bresenham_pts, _unexplored_ratio


class MockGameAPI:
    def __init__(self) -> None:
        self.moves: list[dict] = []

    def move_units_by_location(self, actors, location, attack_move: bool = False) -> None:
        self.moves.append(
            {
                "actor_ids": [actor.actor_id for actor in actors],
                "location": (location.x, location.y),
                "attack_move": attack_move,
            }
        )


def _make_is_explored(width: int, height: int, explored_fn) -> list:
    """Build an is_explored grid (row-major: rows[y][x]) using a predicate."""
    return [[explored_fn(x, y) for x in range(width)] for y in range(height)]


class MockWorldModel:
    def __init__(self) -> None:
        self.map_info = {
            "width": 2000, "height": 1000,
            "explored_pct": 0.2, "visible_pct": 0.1,
            "is_explored": [],   # empty by default — algorithm treats all cells as unexplored
        }
        self.self_actors = {
            57: {
                "actor_id": 57,
                "name": "jeep",
                "display_name": "Jeep",
                "category": "vehicle",
                "position": [120, 820],
                "hp": 100,
                "hp_max": 100,
                "mobility": "fast",
            },
            11: {
                "actor_id": 11,
                "name": "proc",
                "display_name": "Refinery",
                "category": "building",
                "position": [220, 780],
                "hp": 100,
                "hp_max": 100,
                "mobility": "static",
            },
        }
        self.enemy_actors: list[dict] = []

    def query(self, query_type: str, params: dict | None = None):
        params = params or {}
        if query_type == "actor_by_id":
            actor = self.self_actors.get(params["actor_id"])
            return {"actor": dict(actor) if actor else None, "timestamp": 1.0}
        if query_type == "enemy_actors":
            return {"actors": [dict(a) for a in self.enemy_actors], "timestamp": 1.0}
        if query_type == "my_actors":
            actors = [dict(a) for a in self.self_actors.values()]
            category = params.get("category")
            if category is not None:
                actors = [a for a in actors if a.get("category") == category]
            return {"actors": actors, "timestamp": 1.0}
        if query_type in ("map", "map_raw"):
            return dict(self.map_info)
        raise ValueError(f"Unsupported query_type: {query_type}")


def make_config(**overrides) -> ReconJobConfig:
    base = {
        "search_region": "enemy_half",
        "target_type": "base",
        "target_owner": "enemy",
        "retreat_hp_pct": 0.3,
        "avoid_combat": True,
    }
    base.update(overrides)
    return ReconJobConfig(**base)


# -----------------------------------------------------------------------
# Grid helper unit tests
# -----------------------------------------------------------------------

def test_grid_helpers_is_explored_cell() -> None:
    """_is_explored_cell correctly indexes into row-major and col-major grids."""
    # Row-major: exp[y][x]
    exp_rm = [[False, False, True], [True, True, False]]  # h=2, w=3
    assert not _is_explored_cell(exp_rm, 0, 0, 3, 2, "row_major")
    assert _is_explored_cell(exp_rm, 2, 0, 3, 2, "row_major")   # exp_rm[0][2]=True
    assert _is_explored_cell(exp_rm, 0, 1, 3, 2, "row_major")   # exp_rm[1][0]=True

    # Out-of-bounds returns False
    assert not _is_explored_cell(exp_rm, -1, 0, 3, 2, "row_major")
    assert not _is_explored_cell(exp_rm, 0, 5, 3, 2, "row_major")
    print("  PASS: grid_helpers_is_explored_cell")


def test_grid_helpers_bresenham() -> None:
    """_bresenham_pts includes start, end, and intermediate cells."""
    pts = _bresenham_pts(0, 0, 3, 0)
    assert pts == [(0, 0), (1, 0), (2, 0), (3, 0)]
    pts_diag = _bresenham_pts(0, 0, 2, 2)
    assert (0, 0) in pts_diag
    assert (2, 2) in pts_diag
    print("  PASS: grid_helpers_bresenham")


def test_grid_helpers_unexplored_ratio() -> None:
    """_unexplored_ratio returns fraction of unexplored cells on the path."""
    w, h = 10, 10
    # Row-major grid: all explored
    exp_all = [[True] * w for _ in range(h)]
    ratio_all = _unexplored_ratio(exp_all, w, h, "row_major", 0, (0, 0), (5, 0))
    assert ratio_all == 0.0

    # All unexplored
    exp_none = [[False] * w for _ in range(h)]
    ratio_none = _unexplored_ratio(exp_none, w, h, "row_major", 0, (0, 0), (5, 0))
    assert ratio_none == 1.0

    print("  PASS: grid_helpers_unexplored_ratio")


# -----------------------------------------------------------------------
# ReconExpert factory
# -----------------------------------------------------------------------

def test_recon_expert_creates_job_with_fast_vehicle_need() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    expert = ReconExpert(game_api=api, world_model=world)
    signals = []

    job = expert.create_job("t1", make_config(), signals.append)

    assert isinstance(job, ReconJob)
    assert job.expert_type == "ReconExpert"
    needs = job.get_resource_needs()
    assert len(needs) == 1
    assert needs[0].kind == ResourceKind.ACTOR
    assert needs[0].count == 1           # scout_count defaults to 1
    assert needs[0].predicates == {"owner": "self"}
    assert job.tick_interval == 1.0
    print("  PASS: recon_expert_creates_job_with_fast_vehicle_need")


def test_recon_job_scout_count_controls_resource_needs() -> None:
    """scout_count in config determines how many actors are requested."""
    api = MockGameAPI()
    world = MockWorldModel()
    expert = ReconExpert(game_api=api, world_model=world)
    signals = []

    job = expert.create_job("t1", make_config(scout_count=3), signals.append)
    needs = job.get_resource_needs()
    assert needs[0].count == 3
    print("  PASS: recon_job_scout_count_controls_resource_needs")


def test_recon_job_actor_ids_override_generic_scout_needs() -> None:
    """Explicit actor_ids should produce precise resource needs."""
    api = MockGameAPI()
    world = MockWorldModel()
    expert = ReconExpert(game_api=api, world_model=world)
    signals = []

    job = expert.create_job("t1", make_config(actor_ids=[57, 83], scout_count=99), signals.append)
    needs = job.get_resource_needs()
    assert len(needs) == 2
    assert [n.predicates["actor_id"] for n in needs] == ["57", "83"]
    print("  PASS: recon_job_actor_ids_override_generic_scout_needs")


# -----------------------------------------------------------------------
# Search behaviour
# -----------------------------------------------------------------------

def test_recon_job_searches_and_issues_move() -> None:
    """Searching phase issues a move command to the actor."""
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="northeast"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    job.tick()

    assert job.phase == "searching"
    assert len(api.moves) == 1
    assert api.moves[0]["actor_ids"] == [57]
    assert api.moves[0]["attack_move"] is False
    # Awareness signal emitted (no radar in mock)
    assert signals[0].kind == SignalKind.PROGRESS
    assert signals[0].data["awareness"]["status"] == "degraded"
    print("  PASS: recon_job_searches_and_issues_move")


def test_recon_job_random_ray_targets_unexplored_area() -> None:
    """Random-ray algorithm picks a target in the unexplored portion of the map."""
    api = MockGameAPI()
    world = MockWorldModel()

    # Small map: 40×20, left half explored, right half unexplored
    W, H = 40, 20
    world.map_info = {
        "width": W, "height": H,
        "explored_pct": 0.5, "visible_pct": 0.3,
        "is_explored": _make_is_explored(W, H, lambda x, y: x < W // 2),
    }
    # Actor near the explored/unexplored boundary, slightly left
    world.self_actors = {
        5: {
            "actor_id": 5,
            "name": "jeep", "display_name": "Jeep", "category": "vehicle",
            "position": [8, 10],   # left side (explored area)
            "hp": 100, "hp_max": 100, "mobility": "fast",
        },
    }
    world.self_actors[11] = {
        "actor_id": 11, "name": "proc", "display_name": "Refinery",
        "category": "building", "position": [5, 15],
        "hp": 100, "hp_max": 100, "mobility": "static",
    }

    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="northeast"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:5"])

    job.tick()

    assert job.phase == "searching"
    assert len(api.moves) >= 1
    x, y = api.moves[0]["location"]
    # Target must be in the unexplored right half (x >= W//2)
    assert x >= W // 2, f"Expected target in unexplored half (x >= {W//2}), got x={x}"
    print(f"  PASS: recon_job_random_ray_targets_unexplored_area (target=({x},{y}))")


def test_recon_job_keeps_same_destination_until_arrival() -> None:
    """Scout holds the same target across ticks until it arrives."""
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="enemy_half"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    job.tick()
    first_move = api.moves[-1]["location"]
    job.tick()   # actor still far from target → same destination

    assert job.phase == "searching"
    assert len(api.moves) == 1   # no second move issued (same destination dedup)
    assert api.moves[0]["location"] == first_move
    print("  PASS: recon_job_keeps_same_destination_until_arrival")


def test_recon_job_stuck_detection_forces_retarget() -> None:
    """Stuck for _stuck_threshold_ticks ticks causes the actor to abandon target."""
    api = MockGameAPI()
    world = MockWorldModel()
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="full_map"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    # First tick picks a target
    job.tick()
    assert len(api.moves) == 1
    first_target = api.moves[0]["location"]

    # Simulate stuck: actor doesn't move (position unchanged for threshold ticks)
    st = job._scout_states[57]
    st.stuck_ticks = job._stuck_threshold_ticks  # trigger retarget on next tick

    # Clear last_destination so the move is re-issued even if to same coords
    job._last_destinations.clear()
    job.tick()

    # Should have picked a new target and issued a new move
    assert len(api.moves) == 2
    print(f"  PASS: recon_job_stuck_detection_forces_retarget")


def test_recon_job_multi_actor_repulsion() -> None:
    """Two actors get different targets due to repulsion."""
    api = MockGameAPI()
    world = MockWorldModel()

    # Add a second actor close to first
    world.self_actors[83] = {
        "actor_id": 83,
        "name": "jeep", "display_name": "Jeep", "category": "vehicle",
        "position": [140, 820],
        "hp": 100, "hp_max": 100, "mobility": "fast",
    }

    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="full_map", scout_count=2),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57", "actor:83"])

    job.tick()

    # Both actors should have received move orders
    actor_ids_moved = {m["actor_ids"][0] for m in api.moves}
    assert 57 in actor_ids_moved
    assert 83 in actor_ids_moved

    # Their targets should differ
    targets = [m["location"] for m in api.moves]
    assert targets[0] != targets[1], f"Expected different targets, both got {targets[0]}"
    print(f"  PASS: recon_job_multi_actor_repulsion (targets={targets})")


# -----------------------------------------------------------------------
# Tracking / retreat / completion (unchanged behaviour)
# -----------------------------------------------------------------------

def test_recon_job_tracks_clue_then_completes_on_base_found() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.enemy_actors = [
        {
            "actor_id": 201,
            "name": "harv",
            "display_name": "Harvester",
            "category": "harvester",
            "position": [1800, 420],
            "can_attack": False,
        }
    ]
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(target_type="base"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    job.tick()

    assert job.phase == "tracking"
    assert len(api.moves) == 1
    assert api.moves[0]["location"] == (1800, 420)
    tracking_signals = [
        s for s in signals
        if s.kind == SignalKind.PROGRESS and s.expert_state.get("phase") == "tracking"
    ]
    assert tracking_signals

    world.enemy_actors = [
        {
            "actor_id": 301,
            "name": "proc",
            "display_name": "Refinery",
            "category": "building",
            "position": [1820, 430],
            "can_attack": False,
        }
    ]
    job.tick()

    assert job.status == JobStatus.SUCCEEDED
    assert signals[-2].kind == SignalKind.TARGET_FOUND
    assert signals[-1].kind == SignalKind.TASK_COMPLETE
    assert signals[-1].result == "succeeded"
    assert signals[-1].world_delta["target"]["position"] == [1820, 430]
    print("  PASS: recon_job_tracks_clue_then_completes_on_base_found")


def test_recon_job_retreats_when_hp_below_threshold() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.self_actors[57]["hp"] = 20
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(retreat_hp_pct=0.3),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    job.tick()

    assert job.phase == "retreating"
    assert len(api.moves) == 1
    assert api.moves[0]["attack_move"] is False
    target_x, _ = api.moves[0]["location"]
    assert target_x <= 220
    assert signals[-1].kind == SignalKind.RISK_ALERT
    assert signals[-1].data["hp_ratio"] == 0.2
    print("  PASS: recon_job_retreats_when_hp_below_threshold")


def test_recon_job_no_auto_timeout_emits_progress() -> None:
    """ReconJob does not auto-timeout; it keeps running and emits progress signals."""
    api = MockGameAPI()
    world = MockWorldModel()
    world.map_info["explored_pct"] = 0.20
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(target_type="base"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    # Simulate 60 seconds elapsed — should NOT auto-terminate.
    job._created_at -= 60.0
    job._last_progress_s = 0.0  # Reset to ensure progress emits
    world.map_info["explored_pct"] = 0.30
    job.tick()

    assert job.status == JobStatus.RUNNING, f"Expected RUNNING, got {job.status}"
    progress = [s for s in signals if s.kind == SignalKind.PROGRESS and "侦察进行中" in s.summary]
    assert progress, "Expected a progress signal"
    assert "探索度" in progress[-1].summary
    assert progress[-1].data["elapsed_s"] >= 60.0
    print("  PASS: recon_job_no_auto_timeout_emits_progress")


def test_recon_job_reports_mobile_scout_policy_when_radar_exists() -> None:
    api = MockGameAPI()
    world = MockWorldModel()
    world.self_actors[99] = {
        "actor_id": 99,
        "name": "雷达站",
        "display_name": "雷达站",
        "category": "building",
        "position": [300, 760],
        "hp": 100,
        "hp_max": 100,
        "mobility": "static",
    }
    signals = []
    job = ReconJob(
        job_id="j1",
        task_id="t1",
        config=make_config(search_region="enemy_half"),
        signal_callback=signals.append,
        game_api=api,
        world_model=world,
    )
    job.on_resource_granted(["actor:57"])

    job.tick()

    assert api.moves[-1]["actor_ids"] == [57]
    assert signals == []
    assert job._scout_policy(world.self_actors[57])["stage"] == "mobile_deep_recon"
    print("  PASS: recon_job_reports_mobile_scout_policy_when_radar_exists")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
