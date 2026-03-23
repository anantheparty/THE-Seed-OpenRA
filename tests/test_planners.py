"""Tests for Planner Experts."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.planners import ProductionAdvisor, query_planner


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

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "attack"},
        make_world_state(enemy_actors=[]),
    )

    recommendation = proposal["recommendation"]
    assert proposal["status"] == "ok"
    assert recommendation["action"] == "scout_first"
    assert recommendation["reason"] == "no_visible_enemy"
    assert recommendation["recommended_expert"] == "ReconExpert"
    print("  PASS: production_advisor_returns_scout_first_when_no_visible_enemy")


def test_production_advisor_returns_power_tech_when_low_power() -> None:
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "economy"},
        make_world_state(enemy_actors=[{"actor_id": 9}], low_power=True),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "tech_up"
    assert recommendation["unit_type"] == "powr"
    assert recommendation["queue_type"] == "Building"
    assert recommendation["reason"] == "low_power"
    print("  PASS: production_advisor_returns_power_tech_when_low_power")


def test_production_advisor_returns_hold_when_queue_blocked() -> None:
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "economy"},
        make_world_state(enemy_actors=[{"actor_id": 9}], queue_blocked=True),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "hold"
    assert recommendation["reason"] == "queue_blocked"
    print("  PASS: production_advisor_returns_hold_when_queue_blocked")


def test_production_advisor_recommends_jeep_for_mobile_scout_need() -> None:
    planner = ProductionAdvisor()

    proposal = planner.plan(
        "ProductionAdvisor",
        {"intent": "recon", "need_mobile_scout": True},
        make_world_state(
            enemy_actors=[{"actor_id": 9}],
            my_actors=[{"actor_id": 1, "category": "infantry", "mobility": "slow", "is_alive": True}],
        ),
    )

    recommendation = proposal["recommendation"]
    assert recommendation["action"] == "produce"
    assert recommendation["unit_type"] == "jeep"
    assert recommendation["queue_type"] == "Vehicle"
    assert recommendation["reason"] == "need_mobile_scout"
    print("  PASS: production_advisor_recommends_jeep_for_mobile_scout_need")


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
    test_production_advisor_recommends_jeep_for_mobile_scout_need()
    test_query_planner_reports_not_supported_for_other_planners()
    print("\nAll 5 Planner tests passed!")
