"""Tests for Information Experts: BaseStateExpert and ThreatAssessor."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experts.info_base_state import BaseStateExpert
from experts.info_threat import ThreatAssessor


# --- BaseStateExpert ---

def _base_facts(**overrides) -> dict:
    base = {
        "has_construction_yard": True,
        "has_power": True,
        "has_barracks": True,
        "has_refinery": True,
        "has_war_factory": False,
        "has_radar": False,
        "tech_level": 2,
    }
    base.update(overrides)
    return base


def test_base_state_established():
    """Full base → base_established=True, summary='established'."""
    expert = BaseStateExpert()
    result = expert.analyze(_base_facts(), enemy_actors=[], recent_events=[])

    assert result["base_established"] is True
    assert result["base_health_summary"] == "established"
    assert result["has_production"] is True
    print("  PASS: base_state_established")


def test_base_state_no_cy():
    """No construction yard → critical summary."""
    expert = BaseStateExpert()
    result = expert.analyze(
        _base_facts(has_construction_yard=False),
        enemy_actors=[],
        recent_events=[],
    )
    assert result["base_established"] is False
    assert "critical" in result["base_health_summary"]
    print("  PASS: base_state_no_cy")


def test_base_state_no_power():
    """CY present but no power → degraded summary."""
    expert = BaseStateExpert()
    result = expert.analyze(
        _base_facts(has_power=False),
        enemy_actors=[],
        recent_events=[],
    )
    assert result["base_established"] is False
    assert "degraded" in result["base_health_summary"]
    print("  PASS: base_state_no_power")


def test_base_state_no_refinery():
    """CY + power but no refinery → developing summary."""
    expert = BaseStateExpert()
    result = expert.analyze(
        _base_facts(has_refinery=False),
        enemy_actors=[],
        recent_events=[],
    )
    assert result["base_established"] is False
    assert "developing" in result["base_health_summary"]
    print("  PASS: base_state_no_refinery")


def test_base_state_economy_only():
    """CY + power + refinery but no combat production → economy-only summary."""
    expert = BaseStateExpert()
    result = expert.analyze(
        _base_facts(has_barracks=False, has_war_factory=False),
        enemy_actors=[],
        recent_events=[],
    )
    assert result["base_established"] is True  # CY+power+refinery = established
    assert result["has_production"] is False
    assert "economy-only" in result["base_health_summary"]
    print("  PASS: base_state_economy_only")


# --- ThreatAssessor ---

def test_threat_low_no_enemy():
    """No enemies and no events → threat_level=low."""
    expert = ThreatAssessor()
    result = expert.analyze({}, enemy_actors=[], recent_events=[])

    assert result["threat_level"] == "low"
    assert result["enemy_count"] == 0
    assert result["threat_direction"] is None
    assert result["base_under_attack"] is False
    print("  PASS: threat_low_no_enemy")


def test_threat_medium_few_enemies():
    """4 enemy units (= threshold) → medium."""
    expert = ThreatAssessor()
    enemies = [{"category": "infantry", "position": (1000, 1000)}] * 4
    result = expert.analyze({}, enemy_actors=enemies, recent_events=[])

    assert result["threat_level"] == "medium"
    assert result["enemy_count"] == 4
    print("  PASS: threat_medium_few_enemies")


def test_threat_high_many_enemies():
    """10 enemy units → high."""
    expert = ThreatAssessor()
    enemies = [{"category": "vehicle", "position": (4000, 4000)}] * 10
    result = expert.analyze({}, enemy_actors=enemies, recent_events=[])

    assert result["threat_level"] == "high"
    assert result["enemy_count"] == 10
    print("  PASS: threat_high_many_enemies")


def test_threat_critical_base_under_attack():
    """BASE_UNDER_ATTACK event → critical regardless of enemy count."""
    expert = ThreatAssessor()
    result = expert.analyze(
        {},
        enemy_actors=[{"category": "vehicle", "position": (3000, 3000)}],
        recent_events=[{"type": "BASE_UNDER_ATTACK"}],
    )
    assert result["threat_level"] == "critical"
    assert result["base_under_attack"] is True
    print("  PASS: threat_critical_base_under_attack")


def test_threat_direction_northwest():
    """Enemy at (1000, 1000) → northwest quadrant."""
    expert = ThreatAssessor()
    result = expert.analyze(
        {},
        enemy_actors=[{"category": "infantry", "position": (1000, 1000)}],
        recent_events=[],
    )
    assert result["threat_direction"] == "northwest"
    print("  PASS: threat_direction_northwest")


def test_threat_direction_southeast():
    """Enemy at (3000, 3000) → southeast quadrant."""
    expert = ThreatAssessor()
    result = expert.analyze(
        {},
        enemy_actors=[{"category": "vehicle", "position": (3000, 3000)}],
        recent_events=[],
    )
    assert result["threat_direction"] == "southeast"
    print("  PASS: threat_direction_southeast")


def test_threat_composition_mixed():
    """Mixed enemy force produces correct composition summary."""
    expert = ThreatAssessor()
    enemies = (
        [{"category": "infantry", "position": None}] * 3
        + [{"category": "vehicle", "position": None}] * 2
    )
    result = expert.analyze({}, enemy_actors=enemies, recent_events=[])

    assert result["enemy_composition_summary"]["infantry"] == 3
    assert result["enemy_composition_summary"]["vehicle"] == 2
    print("  PASS: threat_composition_mixed")


def test_threat_medium_on_enemy_discovered_event():
    """ENEMY_DISCOVERED event with 0 visible enemies → medium (discovery signal)."""
    expert = ThreatAssessor()
    result = expert.analyze(
        {},
        enemy_actors=[],
        recent_events=[{"type": "ENEMY_DISCOVERED"}],
    )
    assert result["threat_level"] == "medium"
    print("  PASS: threat_medium_on_enemy_discovered_event")


# --- WorldModel integration ---

def test_world_model_info_experts_injected():
    """register_info_expert → compute_runtime_facts includes info_experts key."""
    from world_model import WorldModel
    from unittest.mock import MagicMock

    # Build a minimal WorldModel with a mock source
    mock_source = MagicMock()
    mock_source.get_actors.return_value = []
    mock_source.get_economy.return_value = {}
    mock_source.get_production_queues.return_value = {}
    mock_source.get_map_info.return_value = MagicMock(map_size=None, name="test")

    wm = WorldModel(mock_source)
    wm.register_info_expert(BaseStateExpert())
    wm.register_info_expert(ThreatAssessor())

    facts = wm.compute_runtime_facts("task_1")

    assert "info_experts" in facts, "info_experts key missing from runtime_facts"
    ie = facts["info_experts"]
    # BaseStateExpert fields
    assert "base_established" in ie
    assert "base_health_summary" in ie
    assert "has_production" in ie
    # ThreatAssessor fields
    assert "threat_level" in ie
    assert "enemy_count" in ie
    print("  PASS: world_model_info_experts_injected")


# --- Run all tests ---

if __name__ == "__main__":
    print("Running Information Expert tests...\n")

    test_base_state_established()
    test_base_state_no_cy()
    test_base_state_no_power()
    test_base_state_no_refinery()
    test_base_state_economy_only()
    test_threat_low_no_enemy()
    test_threat_medium_few_enemies()
    test_threat_high_many_enemies()
    test_threat_critical_base_under_attack()
    test_threat_direction_northwest()
    test_threat_direction_southeast()
    test_threat_composition_mixed()
    test_threat_medium_on_enemy_discovered_event()
    test_world_model_info_experts_injected()

    print("\nAll 14 Information Expert tests passed!")
