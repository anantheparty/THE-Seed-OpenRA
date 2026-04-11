"""Tests for UnitRegistry loading and lookup."""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unit_registry import UnitRegistry


def test_registry_loads_core_entries() -> None:
    registry = UnitRegistry.load()

    powr = registry.get("powr")
    e1 = registry.get("E1")
    sam = registry.get("sam")

    assert powr is not None
    assert powr.unit_id == "POWR"
    assert powr.queue_type == "Building"
    assert powr.category == "building"
    assert powr.cost == 300
    assert "电厂" in powr.aliases

    assert e1 is not None
    assert e1.display_name == "步兵"
    assert e1.queue_type == "Infantry"
    assert e1.cost == 100

    assert sam is not None
    assert sam.category == "defense"
    assert sam.queue_type == "Defense"
    print("  PASS: registry_loads_core_entries")


def test_registry_resolve_name_and_aliases() -> None:
    registry = UnitRegistry.load()

    assert registry.resolve_name("电厂").unit_id == "POWR"
    assert registry.resolve_name("power plant").unit_id == "POWR"
    assert registry.resolve_name("步兵").unit_id == "E1"
    assert registry.resolve_name("重坦").unit_id == "3TNK"
    assert registry.resolve_name("unknown unit") is None
    print("  PASS: registry_resolve_name_and_aliases")


def test_registry_list_buildable_filters_by_queue_and_faction() -> None:
    registry = UnitRegistry.load()

    allied_vehicles = {entry.unit_id for entry in registry.list_buildable("Vehicle", "allies")}
    soviet_vehicles = {entry.unit_id for entry in registry.list_buildable("Vehicle", "soviet")}
    infantry = {entry.unit_id for entry in registry.list_buildable("Infantry", "allies")}

    assert "1TNK" in allied_vehicles
    assert "1TNK" not in soviet_vehicles
    assert "3TNK" in soviet_vehicles
    assert "3TNK" not in allied_vehicles
    assert "E1" in infantry
    print("  PASS: registry_list_buildable_filters_by_queue_and_faction")


def test_registry_match_in_text_uses_registry_aliases() -> None:
    registry = UnitRegistry.load()

    assert registry.match_in_text("建造发电厂", queue_types=("Building",)).unit_id == "POWR"
    assert registry.match_in_text("建造兵营", queue_types=("Building",)).unit_id == "BARR"
    assert registry.match_in_text("生产3个步兵", queue_types=("Infantry",)).unit_id == "E1"
    print("  PASS: registry_match_in_text_uses_registry_aliases")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
