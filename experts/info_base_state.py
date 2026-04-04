"""BaseStateExpert — derives structured base-status fields from runtime_facts."""

from __future__ import annotations

from typing import Any


class BaseStateExpert:
    """Information Expert: enriches runtime_facts with derived base-status fields.

    Reads the existing has_* / tech_level fields produced by
    WorldModel.compute_runtime_facts() and adds higher-level derived fields
    that the TaskAgent LLM can read directly without inference.

    Output fields injected into info_experts:
      base_established     bool   — CY + power + refinery all present
      base_health_summary  str    — human-readable one-line status
      has_production       bool   — any combat production (barracks or war_factory)
    """

    def analyze(
        self,
        runtime_facts: dict[str, Any],
        *,
        enemy_actors: list[dict[str, Any]],
        recent_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        has_cy = bool(runtime_facts.get("has_construction_yard"))
        has_power = runtime_facts.get("power_plant_count", 0) > 0
        has_refinery = runtime_facts.get("refinery_count", 0) > 0
        has_barracks = runtime_facts.get("barracks_count", 0) > 0
        has_war_factory = runtime_facts.get("war_factory_count", 0) > 0

        base_established = has_cy and has_power and has_refinery
        has_production = has_barracks or has_war_factory

        if not has_cy:
            base_health_summary = "critical — no construction yard"
        elif not has_power:
            base_health_summary = "degraded — no power plant"
        elif not has_refinery:
            base_health_summary = "developing — no refinery"
        elif not has_production:
            base_health_summary = "economy-only — no combat production"
        else:
            base_health_summary = "established"

        return {
            "base_established": base_established,
            "base_health_summary": base_health_summary,
            "has_production": has_production,
        }
