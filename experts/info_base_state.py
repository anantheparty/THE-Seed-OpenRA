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
        has_power = bool(runtime_facts.get("has_power"))
        has_refinery = bool(runtime_facts.get("has_refinery"))
        has_barracks = bool(runtime_facts.get("has_barracks"))
        has_war_factory = bool(runtime_facts.get("has_war_factory"))

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
