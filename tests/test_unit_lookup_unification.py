"""Regression tests for shared unit/name/category lookup."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openra_api.models import Actor, Location, MapQueryResult, PlayerBaseInfo
from openra_api.actor_view import ActorView
from openra_api.intel.service import IntelService
from openra_api.production_names import production_name_category, production_name_entry, production_name_unit_id, production_name_variants
from openra_state.data import CombatData, StructureData, UnitCategory
from world_model import WorldModel


class _Frame:
    def __init__(self, self_actors: list[Actor]) -> None:
        self.self_actors = self_actors
        self.enemy_actors = []
        self.economy = PlayerBaseInfo(Cash=1000, Resources=0, Power=0, PowerDrained=0, PowerProvided=0)
        self.map_info = MapQueryResult(MapWidth=1, MapHeight=1, Height=[[0]], IsVisible=[[False]], IsExplored=[[False]], Terrain=[["clear"]], ResourcesType=[["ore"]], Resources=[[0]])
        self.queues = {}


class _Source:
    def __init__(self, frame: _Frame) -> None:
        self.frame = frame

    def fetch_self_actors(self) -> list[Actor]:
        return self.frame.self_actors

    def fetch_enemy_actors(self) -> list[Actor]:
        return self.frame.enemy_actors

    def fetch_frozen_enemies(self):
        return []

    def fetch_economy(self) -> PlayerBaseInfo:
        return self.frame.economy

    def fetch_map(self, fields=None) -> MapQueryResult:
        return self.frame.map_info

    def fetch_production_queues(self) -> dict[str, dict]:
        return self.frame.queues


def test_shared_production_lookup_resolves_aliases_and_ambiguous_names() -> None:
    entry = production_name_entry("power plant")

    assert entry is not None
    assert entry.unit_id == "POWR"
    assert production_name_unit_id("ore collector") == "harv"
    assert production_name_category("con yard") == "building"
    variants = production_name_variants("兵营")
    assert "苏军兵营" in variants
    assert "盟军兵营" in variants
    print("  PASS: shared_production_lookup_resolves_aliases_and_ambiguous_names")


def test_data_layer_resolution_delegates_to_shared_lookup() -> None:
    assert StructureData.is_valid_structure("power plant") is True
    assert StructureData.get_info("power plant")["type"] == "powr"
    assert CombatData.resolve_id("flak truck") == "ftrk"
    assert CombatData.get_combat_info("flak truck") == (UnitCategory.AFV, 5.0)
    print("  PASS: data_layer_resolution_delegates_to_shared_lookup")


def test_world_model_classifies_registry_aliases() -> None:
    world = WorldModel(
        _Source(
            _Frame(
                [
                    Actor(actor_id=1, type="power plant", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
                    Actor(actor_id=2, type="con yard", faction="自己", position=Location(12, 10), hppercent=100, activity="Idle"),
                    Actor(actor_id=3, type="ore collector", faction="自己", position=Location(14, 10), hppercent=100, activity="Idle"),
                    Actor(actor_id=4, type="flak truck", faction="自己", position=Location(16, 10), hppercent=100, activity="Idle"),
                ]
            )
        )
    )

    world.refresh(force=True)
    actors = {item["actor_id"]: item for item in world.query("my_actors")["actors"]}

    assert actors[1]["category"] == "building"
    assert actors[2]["category"] == "building"
    assert actors[3]["category"] == "harvester"
    assert actors[4]["category"] == "vehicle"
    assert world.find_actors(owner="self", category="building", name="power plant")[0].actor_id == 1
    print("  PASS: world_model_classifies_registry_aliases")


def test_intel_service_uses_shared_lookup_for_aliases() -> None:
    service = IntelService(object())
    snapshot = {
        "my_actors": [
            Actor(actor_id=1, type="power plant", faction="自己", position=Location(10, 10), hppercent=100, activity="Idle"),
            Actor(actor_id=2, type="ore collector", faction="自己", position=Location(30, 30), hppercent=100, activity="Idle"),
            Actor(actor_id=3, type="flak truck", faction="自己", position=Location(50, 50), hppercent=100, activity="Idle"),
        ]
    }

    base_center = service.get_base_center(snapshot)
    summary = service._summarize_actors([ActorView.from_actor(actor) for actor in snapshot["my_actors"]])

    assert base_center == Location(10, 10)
    assert service._categorize_unit("power plant") == "building"
    assert service._categorize_unit("ore collector") == "harvester"
    assert service._categorize_unit("flak truck") == "vehicle"
    assert summary["buildings"]["power plant"] == 1
    assert summary["units"]["ore collector"] == 1
    print("  PASS: intel_service_uses_shared_lookup_for_aliases")


def test_intel_service_tech_summary_uses_demo_truth_instead_of_probe_calls() -> None:
    service = IntelService(object())
    tech = service._summarize_tech(
        {
            "buildings": {"建造厂": 1, "发电厂": 1},
            "units": {},
        }
    )

    assert tech["can_build"] == ["发电厂", "矿场", "兵营"], tech
    assert tech["can_train"] == [], tech
    assert tech["owned_key_buildings"]["兵营"] == 0, tech
    assert tech["tech_level_est"] == 0, tech
    print("  PASS: intel_service_tech_summary_uses_demo_truth_instead_of_probe_calls")


if __name__ == "__main__":
    print("Running shared unit lookup tests...\n")
    test_shared_production_lookup_resolves_aliases_and_ambiguous_names()
    test_data_layer_resolution_delegates_to_shared_lookup()
    test_world_model_classifies_registry_aliases()
    test_intel_service_uses_shared_lookup_for_aliases()
    test_intel_service_tech_summary_uses_demo_truth_instead_of_probe_calls()
    print("\nAll shared unit lookup tests passed!")
