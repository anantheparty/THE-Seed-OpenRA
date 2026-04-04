"""ThreatAssessor — derives threat level and enemy composition from world state."""

from __future__ import annotations

from typing import Any, Optional


def _classify_direction(position: Any) -> Optional[str]:
    """Classify enemy position as a cardinal quadrant label, or None."""
    if not position:
        return None
    try:
        if isinstance(position, (list, tuple)) and len(position) >= 2:
            x, y = float(position[0]), float(position[1])
        elif isinstance(position, dict):
            x, y = float(position.get("x", 0)), float(position.get("y", 0))
        else:
            return None
    except (TypeError, ValueError):
        return None

    # Simple quadrant mapping.  OpenRA world coords: +x → east, +y → south.
    # Using 2500 as a rough mid-map reference for typical RA maps (~5000×5000).
    _MID = 2500.0
    if x < _MID and y < _MID:
        return "northwest"
    if x >= _MID and y < _MID:
        return "northeast"
    if x < _MID and y >= _MID:
        return "southwest"
    return "southeast"


class ThreatAssessor:
    """Information Expert: derives threat assessment from enemy actors and events.

    Output fields injected into info_experts:
      threat_level               str   — low | medium | high | critical
      threat_direction           str?  — cardinal quadrant of nearest threat, or None
      enemy_count                int   — total visible enemy units
      enemy_composition_summary  dict  — {category: count}
      base_under_attack          bool  — recent BASE_UNDER_ATTACK event seen
    """

    # Thresholds for threat escalation
    _HIGH_ENEMY_COUNT = 10
    _MEDIUM_ENEMY_COUNT = 4

    def analyze(
        self,
        runtime_facts: dict[str, Any],
        *,
        enemy_actors: list[dict[str, Any]],
        recent_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del runtime_facts  # not needed by this expert

        base_under_attack = any(
            e.get("type") == "BASE_UNDER_ATTACK" for e in recent_events
        )
        enemy_discovered = any(
            e.get("type") == "ENEMY_DISCOVERED" for e in recent_events
        )

        enemy_count = len(enemy_actors)

        # Threat level escalation
        if base_under_attack:
            threat_level = "critical"
        elif enemy_count >= self._HIGH_ENEMY_COUNT:
            threat_level = "high"
        elif enemy_count >= self._MEDIUM_ENEMY_COUNT or enemy_discovered:
            threat_level = "medium"
        else:
            threat_level = "low"

        # Enemy composition
        composition: dict[str, int] = {}
        for actor in enemy_actors:
            cat = str(actor.get("category") or "unknown").lower()
            composition[cat] = composition.get(cat, 0) + 1

        # Threat direction from first visible enemy
        threat_direction: Optional[str] = None
        for actor in enemy_actors:
            pos = actor.get("position") or actor.get("location")
            direction = _classify_direction(pos)
            if direction:
                threat_direction = direction
                break

        return {
            "threat_level": threat_level,
            "threat_direction": threat_direction,
            "enemy_count": enemy_count,
            "enemy_composition_summary": composition,
            "base_under_attack": base_under_attack,
        }
