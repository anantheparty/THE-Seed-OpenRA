"""Tests for Planner Experts."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.planners import ProductionAdvisor, query_planner
from experts.knowledge import (
    opening_build_order,
    tech_prerequisites_for,
    counter_recommendation,
    placement_hint_for,
)


def make_world_state(
    *,
    enemy_actors=None,
    low_power: bool = False,
    queue_blocked: bool = False,
    my_actors=None,
    queues=None,
):
    return {
        "world_summary": {
            "economy": {"queue_blocked": queue_blocked},
            "military": {},
            "known_enemy": {},
            "timestamp": 1.0,
        },
        "economy": {
            "low_power": low_power,
            "power": 20,
            "power_drained": 80 if low_power else 10,
            "timestamp": 1.0,
        },
        "production_queues": queues or {
            "Building": {"queue_type": "Building", "items": [], "has_ready_item": False},
            "Vehicle": {"queue_type": "Vehicle", "items": [], "has_ready_item": False},
        },
        "my_actors": {"actors": list(my_actors or []), "timestamp": 1.0},
        "enemy_actors": {"actors": list(enemy_actors or []), "timestamp": 1.0},
    }


def test_production_advisor_returns_scout_first_when_no_visible_enemy() -> None:
    planner = ProductionAdvisor()

    # Base must be non-empty so the empty-base check doesn't fire first
    established_base = [
        {"actor_id": 1, "name": "发电厂", "display_name": "发电厂", "category": "building"},
        {"actor_id": 2, "name": "矿场",  "display_name": "矿场",  "category": "building"},
    ]
    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "attack"},
        make_world_state(enemy_actors=[], my_actors=established_base),
    )

    recommendation = proposal["recommendation"]
    assert proposal["status"] == "ok"
    assert recommendation["action"] == "scout_first"
    assert recommendation["reason"] == "no_visible_enemy"
    assert recommendation["recommended_expert"] == "ReconExpert"
    print("  PASS: production_advisor_returns_scout_first_when_no_visible_enemy")


def test_production_advisor_returns_power_tech_when_low_power() -> None:
    planner = ProductionAdvisor()

    established_base = [
        {"actor_id": 1, "name": "矿场", "display_name": "矿场", "category": "building"},
    ]
    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "economy"},
        make_world_state(enemy_actors=[{"actor_id": 9}], low_power=True, my_actors=established_base),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "tech_up"
    assert recommendation["unit_type"] == "powr"
    assert recommendation["queue_type"] == "Building"
    assert recommendation["reason"] == "low_power"
    assert recommendation["roles"] == ["power_recovery"]
    assert recommendation["downstream_unlocks"] == ["anypower"]
    print("  PASS: production_advisor_returns_power_tech_when_low_power")


def test_production_advisor_returns_hold_when_queue_blocked() -> None:
    planner = ProductionAdvisor()

    established_base = [
        {"actor_id": 1, "name": "矿场", "display_name": "矿场", "category": "building"},
    ]
    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "economy"},
        make_world_state(enemy_actors=[{"actor_id": 9}], queue_blocked=True, my_actors=established_base),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "hold"
    assert recommendation["reason"] == "queue_blocked"
    assert recommendation["recommended_expert"] is None
    assert recommendation["queue_scope"] == "player_shared"
    print("  PASS: production_advisor_returns_hold_when_queue_blocked")


def test_production_advisor_recommends_demo_mobile_scout_for_mobile_scout_need() -> None:
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "recon", "need_mobile_scout": True},
        make_world_state(
            enemy_actors=[{"actor_id": 9}],
            my_actors=[
                # War factory present (vehicle gateway) so base is non-empty and scout path is valid
                {"actor_id": 2, "name": "战车工厂", "display_name": "战车工厂", "category": "building"},
                {"actor_id": 1, "category": "infantry", "mobility": "slow", "is_alive": True},
            ],
        ),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "produce"
    assert recommendation["unit_type"] == "ftrk"
    assert recommendation["queue_type"] == "Vehicle"
    assert recommendation["prerequisites"] == ["weap"]
    assert recommendation["reason"] == "need_mobile_scout"
    print("  PASS: production_advisor_recommends_demo_mobile_scout_for_mobile_scout_need")


def test_production_advisor_recommends_weap_before_mobile_scout_when_no_vehicle_gateway() -> None:
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "recon", "need_mobile_scout": True},
        make_world_state(
            enemy_actors=[{"actor_id": 9}],
            my_actors=[{"actor_id": 1, "name": "矿场", "display_name": "矿场", "category": "building"}],
            queues={"Building": {"queue_type": "Building", "items": [], "has_ready_item": False}},
        ),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "tech_up"
    assert recommendation["unit_type"] == "weap"
    assert recommendation["reason"] == "need_vehicle_gateway"
    assert recommendation["roles"] == ["vehicle_gateway", "tech_gateway"]
    assert "mobile_scout_transition" in recommendation["downstream_unlocks"]
    print("  PASS: production_advisor_recommends_weap_before_mobile_scout_when_no_vehicle_gateway")


def test_production_advisor_empty_base_recommends_opening() -> None:
    """Empty base (no meaningful buildings) → recommend first opening build step."""
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "economy", "faction": "allied"},
        make_world_state(enemy_actors=[{"actor_id": 5}], my_actors=[]),
    )

    recommendation = proposal["recommendation"]
    assert proposal["status"] == "ok"
    assert recommendation["action"] == "build_opening"
    assert recommendation["unit_type"] == "powr"        # power first
    assert recommendation["queue_type"] == "Building"
    assert recommendation["build_order_step"] == 1
    assert recommendation["build_order_total"] == 4
    assert recommendation["recommended_expert"] == "EconomyExpert"
    print("  PASS: production_advisor_empty_base_recommends_opening")


def test_production_advisor_empty_base_ignores_no_enemy():
    """Empty base check fires before no-visible-enemy, ensuring build order starts."""
    planner = ProductionAdvisor()

    # No enemy visible, but base is also empty — should still recommend build_opening
    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "attack"},
        make_world_state(enemy_actors=[], my_actors=[]),
    )

    assert proposal["recommendation"]["action"] == "build_opening"
    print("  PASS: production_advisor_empty_base_ignores_no_enemy")


def test_production_advisor_counter_infantry_heavy() -> None:
    """Infantry-heavy enemy (≥60%) → recommend demo-safe rocket infantry counter."""
    planner = ProductionAdvisor()

    enemy = [{"actor_id": i, "category": "infantry"} for i in range(7)]
    enemy += [{"actor_id": 10, "category": "vehicle"}]  # 7/8 = 87.5% infantry

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "attack", "faction": "soviet"},
        make_world_state(
            enemy_actors=enemy,
            my_actors=[{"actor_id": 1, "name": "矿场", "display_name": "矿场", "category": "building"}],
        ),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "produce"
    assert recommendation["unit_type"] == "e3"
    assert recommendation["reason"] == "infantry_heavy_counter_rocket"
    print("  PASS: production_advisor_counter_infantry_heavy")


def test_production_advisor_counter_vehicle_heavy() -> None:
    """Vehicle-heavy enemy (≥50%) → recommend demo-safe V2 counter."""
    planner = ProductionAdvisor()

    enemy = [{"actor_id": i, "category": "vehicle"} for i in range(5)]
    enemy += [{"actor_id": 10, "category": "infantry"}]  # 5/6 = 83% vehicle

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "attack", "faction": "soviet"},
        make_world_state(
            enemy_actors=enemy,
            my_actors=[{"actor_id": 1, "name": "矿场", "display_name": "矿场", "category": "building"}],
        ),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "produce"
    assert recommendation["unit_type"] == "v2rl"
    assert recommendation["reason"] == "vehicle_heavy_counter_v2"
    print("  PASS: production_advisor_counter_vehicle_heavy")


def test_opening_build_order_allied() -> None:
    """Demo opening order follows normalized capability broad phase."""
    order = opening_build_order("allied")
    assert len(order) == 4
    types = [step["unit_type"] for step in order]
    assert types == ["powr", "proc", "barr", "weap"]
    print("  PASS: opening_build_order_allied")


def test_tech_prerequisites_weap() -> None:
    """War factory requires refinery first."""
    prereqs = tech_prerequisites_for("weap")
    assert any(p["unit_type"] == "proc" for p in prereqs)
    print("  PASS: tech_prerequisites_weap")


def test_counter_recommendation_no_enemy() -> None:
    """Empty enemy list → no counter recommendation."""
    result = counter_recommendation([])
    assert result is None
    print("  PASS: counter_recommendation_no_enemy")


def test_counter_recommendation_requires_safe_faction_for_faction_locked_units() -> None:
    """Faction-locked counters should not be proposed when side is unknown."""
    vehicle_heavy = [{"actor_id": i, "category": "vehicle"} for i in range(5)] + [{"actor_id": 9, "category": "infantry"}]
    assert counter_recommendation(vehicle_heavy) is None
    assert counter_recommendation(vehicle_heavy, faction="soviet")["unit_type"] == "v2rl"
    print("  PASS: counter_recommendation_requires_safe_faction_for_faction_locked_units")


def test_placement_hint_refinery() -> None:
    """Refinery should be placed near ore field."""
    hint = placement_hint_for("proc")
    assert hint is not None
    assert hint["near"] == "ore_field"
    print("  PASS: placement_hint_refinery")


def test_query_planner_reports_not_supported_for_other_planners() -> None:
    proposal = query_planner("AttackRoutePlanner", {}, make_world_state())

    assert proposal["status"] == "not_supported"
    assert proposal["planner_type"] == "AttackRoutePlanner"
    print("  PASS: query_planner_reports_not_supported_for_other_planners")


if __name__ == "__main__":
    print("Running Planner tests...\n")
    test_production_advisor_returns_scout_first_when_no_visible_enemy()
    test_production_advisor_returns_power_tech_when_low_power()
    test_production_advisor_returns_hold_when_queue_blocked()
    test_production_advisor_recommends_demo_mobile_scout_for_mobile_scout_need()
    test_production_advisor_recommends_weap_before_mobile_scout_when_no_vehicle_gateway()
    test_query_planner_reports_not_supported_for_other_planners()
    test_production_advisor_empty_base_recommends_opening()
    test_production_advisor_empty_base_ignores_no_enemy()
    test_production_advisor_counter_infantry_heavy()
    test_production_advisor_counter_vehicle_heavy()
    test_opening_build_order_allied()
    test_tech_prerequisites_weap()
    test_counter_recommendation_no_enemy()
    test_counter_recommendation_requires_safe_faction_for_faction_locked_units()
    test_placement_hint_refinery()
    print("\nAll 15 Planner tests passed!")
