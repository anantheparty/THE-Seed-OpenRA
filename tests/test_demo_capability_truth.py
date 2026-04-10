"""Consistency tests for the demo capability truth table."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.knowledge import counter_recommendation, display_name_for, knowledge_for_target, tech_prerequisites_for
from openra_state.data.dataset import (
    demo_capability_truth_for,
    demo_display_name_for,
    demo_mobile_scout_unit_type,
    demo_prompt_display_name_for,
    demo_prompt_roster_lines,
    demo_queue_type_for,
    filter_demo_capability_buildable,
)
from task_agent.context import _capability_runtime_facts_view


def test_demo_dataset_helpers_expose_capability_truth() -> None:
    assert demo_queue_type_for("powr") == "Building"
    assert demo_queue_type_for("e3") == "Infantry"
    assert demo_queue_type_for("ftrk") == "Vehicle"
    assert demo_queue_type_for("yak") == "Aircraft"
    assert demo_queue_type_for("kenn") is None
    assert demo_display_name_for("apwr") == "核电站"
    assert demo_display_name_for("harv") == "采矿车"
    assert demo_prompt_display_name_for("harv") == "矿车"
    assert demo_prompt_display_name_for("4tnk") == "猛犸坦克"
    assert demo_mobile_scout_unit_type() == "ftrk"
    truth = demo_capability_truth_for("4tnk")
    assert truth is not None
    assert truth.queue_type == "Vehicle"
    assert truth.display_name == "超重型坦克"
    assert truth.prompt_display_name == "猛犸坦克"
    assert truth.prerequisites == ("fix", "stek", "weap")
    assert truth.faction == "soviet"
    assert truth.in_demo_roster is True
    print("  PASS: demo_dataset_helpers_expose_capability_truth")


def test_demo_prompt_roster_lines_follow_truth_table() -> None:
    lines = demo_prompt_roster_lines(include_buildings=False)
    assert any("e1=步兵" in line for line in lines)
    assert any("ftrk=防空履带车" in line for line in lines)
    assert any("4tnk=猛犸坦克" in line for line in lines)
    assert not any("powr=电厂" in line for line in lines)
    print("  PASS: demo_prompt_roster_lines_follow_truth_table")


def test_capability_runtime_view_derives_queue_type_from_dataset() -> None:
    filtered = _capability_runtime_facts_view(
        {
            "unit_reservations": [
                {
                    "reservation_id": "r1",
                    "request_id": "u1",
                    "task_id": "t1",
                    "task_label": "001",
                    "unit_type": "v2rl",
                    "count": 1,
                    "assigned_actor_ids": [],
                    "produced_actor_ids": [],
                    "status": "pending",
                }
            ]
        }
    )

    reservations = filtered["unit_reservations"]
    assert reservations[0]["queue_type"] == "Vehicle"
    print("  PASS: capability_runtime_view_derives_queue_type_from_dataset")


def test_filter_demo_capability_buildable_strips_non_demo_entries() -> None:
    filtered = filter_demo_capability_buildable(
        {
            "Building": ["powr", "proc", "kenn", "silo"],
            "Infantry": ["e1", "e3", "e6", "dog"],
            "Vehicle": ["ftrk", "v2rl", "3tnk", "jeep"],
            "Aircraft": ["mig", "yak", "heli"],
        }
    )

    assert filtered == {
        "Building": ["powr", "proc"],
        "Infantry": ["e1", "e3"],
        "Vehicle": ["ftrk", "v2rl", "3tnk"],
        "Aircraft": ["mig", "yak"],
    }
    print("  PASS: filter_demo_capability_buildable_strips_non_demo_entries")


def test_knowledge_display_and_prerequisites_follow_dataset_truth() -> None:
    assert display_name_for("apwr") == "核电站"
    prereqs = [item["unit_type"] for item in tech_prerequisites_for("4tnk")]
    assert prereqs == ["fix", "stek", "weap"]
    print("  PASS: knowledge_display_and_prerequisites_follow_dataset_truth")


def test_knowledge_downstream_unlocks_stay_within_demo_truth() -> None:
    dome = knowledge_for_target("dome", "Building")
    afld = knowledge_for_target("afld", "Building")
    stek = knowledge_for_target("stek", "Building")

    assert dome["queue_type"] == "Building"
    assert dome["in_demo_roster"] is True
    assert dome["display_name"] == "雷达站"
    assert dome["prerequisites"] == ["proc", "fact"]
    assert dome["downstream_unlocks"] == ["afld", "stek"]
    assert afld["roles"] == ["air_gateway", "tech_gateway"]
    assert afld["downstream_unlocks"] == ["mig", "yak"]
    assert stek["downstream_unlocks"] == ["4tnk"]
    print("  PASS: knowledge_downstream_unlocks_stay_within_demo_truth")


def test_counter_recommendation_stays_within_demo_roster() -> None:
    air_heavy = [{"actor_id": i, "category": "aircraft"} for i in range(4)]
    inf_heavy = [{"actor_id": i, "category": "infantry"} for i in range(7)] + [{"actor_id": 9, "category": "vehicle"}]
    vehicle_heavy = [{"actor_id": i, "category": "vehicle"} for i in range(5)] + [{"actor_id": 9, "category": "infantry"}]

    assert counter_recommendation(air_heavy)["unit_type"] == "ftrk"
    assert counter_recommendation(inf_heavy)["unit_type"] == "e3"
    assert counter_recommendation(vehicle_heavy)["unit_type"] == "v2rl"
    print("  PASS: counter_recommendation_stays_within_demo_roster")


if __name__ == "__main__":
    print("Running demo capability truth tests...\n")
    test_demo_dataset_helpers_expose_capability_truth()
    test_demo_prompt_roster_lines_follow_truth_table()
    test_capability_runtime_view_derives_queue_type_from_dataset()
    test_filter_demo_capability_buildable_strips_non_demo_entries()
    test_knowledge_display_and_prerequisites_follow_dataset_truth()
    test_knowledge_downstream_unlocks_stay_within_demo_truth()
    test_counter_recommendation_stays_within_demo_roster()
    print("\nAll demo capability truth tests passed!")
