"""Planner Experts — proposal generators using deterministic rules/scoring."""

from __future__ import annotations

from typing import Any, Optional

from openra_state.data.dataset import demo_mobile_scout_unit_type, demo_queue_type_for

from .base import PlannerExpert
from .knowledge import (
    counter_recommendation,
    has_role,
    knowledge_for_target,
    opening_build_order,
    tech_prerequisites_for,
)


def _actors_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        actors = payload.get("actors")
        if isinstance(actors, list):
            return [actor for actor in actors if isinstance(actor, dict)]
    return []


class ProductionAdvisor(PlannerExpert):
    """Rule-based production/scouting advisor.

    This planner is intentionally narrow: it returns structured suggestions for
    the Task Agent, but it does not create jobs or call GameAPI directly.
    """

    def plan(
        self,
        query_type: str,
        params: dict[str, Any],
        world_state: dict[str, Any],
    ) -> dict[str, Any]:
        del query_type
        economy = dict(world_state.get("economy") or {})
        summary = dict(world_state.get("world_summary") or {})
        queues = dict(world_state.get("production_queues") or {})
        my_actors = _actors_from_payload(world_state.get("my_actors"))
        enemy_actors = _actors_from_payload(world_state.get("enemy_actors"))

        recommendation = self._recommend(
            params=params,
            economy=economy,
            summary=summary,
            queues=queues,
            my_actors=my_actors,
            enemy_actors=enemy_actors,
        )
        return {
            "planner_type": "ProductionAdvisor",
            "status": "ok",
            "recommendation": recommendation,
            "alternatives": [],
        }

    def _recommend(
        self,
        *,
        params: dict[str, Any],
        economy: dict[str, Any],
        summary: dict[str, Any],
        queues: dict[str, Any],
        my_actors: list[dict[str, Any]],
        enemy_actors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Empty base → recommend next opening build step regardless of enemy visibility
        if self._is_empty_base(my_actors):
            order = opening_build_order(params.get("faction", "allied"))
            if order:
                step = order[0]
                unit_type = step["unit_type"]
                knowledge = knowledge_for_target(unit_type, "Building")
                return {
                    "action": "build_opening",
                    "unit_type": unit_type,
                    "queue_type": step["queue_type"],
                    "count": 1,
                    "prerequisites": [p["unit_type"] for p in tech_prerequisites_for(unit_type)],
                    "reason": "empty_base_" + step["reason"],
                    "recommended_expert": "EconomyExpert",
                    "roles": knowledge["roles"],
                    "downstream_unlocks": knowledge["downstream_unlocks"],
                    "build_order_step": 1,
                    "build_order_total": len(order),
                }

        if self._no_visible_enemy(params, enemy_actors):
            recommendation = {
                "action": "scout_first",
                "unit_type": None,
                "queue_type": None,
                "count": None,
                "prerequisites": [],
                "reason": "no_visible_enemy",
                "recommended_expert": "ReconExpert",
                "impact": {"kind": "target_visibility", "effects": ["no_visible_enemy"]},
            }
            return recommendation

        if self._is_low_power(economy):
            knowledge = knowledge_for_target("powr", "Building")
            return {
                "action": "tech_up",
                "unit_type": "powr",
                "queue_type": "Building",
                "count": 1,
                "prerequisites": [],
                "reason": "low_power",
                "recommended_expert": "EconomyExpert",
                "roles": knowledge["roles"],
                "downstream_unlocks": knowledge["downstream_unlocks"],
            }

        if self._queue_blocked(summary, queues):
            return {
                "action": "hold",
                "unit_type": None,
                "queue_type": None,
                "count": None,
                "prerequisites": [],
                "reason": "queue_blocked",
                "recommended_expert": None,
                "queue_scope": "player_shared",
            }

        if self._needs_vehicle_gateway(params, my_actors, queues):
            knowledge = knowledge_for_target("weap", "Building")
            return {
                "action": "tech_up",
                "unit_type": "weap",
                "queue_type": "Building",
                "count": 1,
                "prerequisites": ["proc"],
                "reason": "need_vehicle_gateway",
                "recommended_expert": "EconomyExpert",
                "roles": knowledge["roles"],
                "downstream_unlocks": knowledge["downstream_unlocks"],
            }

        if self._needs_mobile_scout(params, my_actors, queues):
            unit_type = demo_mobile_scout_unit_type()
            if unit_type is None:
                return {
                    "action": "hold",
                    "unit_type": None,
                    "queue_type": None,
                    "count": None,
                    "prerequisites": [],
                    "reason": "demo_mobile_scout_not_configured",
                    "recommended_expert": None,
                }
            queue_type = demo_queue_type_for(unit_type) or "Vehicle"
            knowledge = knowledge_for_target(unit_type, queue_type)
            return {
                "action": "produce",
                "unit_type": unit_type,
                "queue_type": queue_type,
                "count": 1,
                "prerequisites": [p["unit_type"] for p in tech_prerequisites_for(unit_type)],
                "reason": "need_mobile_scout",
                "recommended_expert": "EconomyExpert",
                "roles": knowledge["roles"],
                "downstream_unlocks": knowledge["downstream_unlocks"],
            }

        faction = params.get("faction", "allied")
        counter = counter_recommendation(enemy_actors, faction)
        if counter:
            return {
                "action": "produce",
                "unit_type": counter["unit_type"],
                "queue_type": counter["queue_type"],
                "count": 1,
                "prerequisites": [],
                "reason": counter["reason"],
                "recommended_expert": "EconomyExpert",
                "display_name": counter["display_name"],
                "enemy_ratio": counter["enemy_ratio"],
            }

        return {
            "action": "hold",
            "unit_type": None,
            "queue_type": None,
            "count": None,
            "prerequisites": [],
            "reason": "sufficient_force",
            "recommended_expert": None,
        }

    @staticmethod
    def _is_empty_base(my_actors: list[dict[str, Any]]) -> bool:
        """True when player has no meaningful buildings (only CY or nothing built yet)."""
        meaningful_roles = {
            "power_recovery", "infantry_gateway", "economy_anchor",
            "vehicle_gateway", "repair_gateway", "awareness_gateway",
        }
        return not any(
            has_role(actor, role)
            for actor in my_actors
            for role in meaningful_roles
        )

    @staticmethod
    def _no_visible_enemy(params: dict[str, Any], enemy_actors: list[dict[str, Any]]) -> bool:
        if enemy_actors:
            return False
        intent = str(params.get("intent", "attack")).lower()
        return intent in {"attack", "assault", "harass", "engage", "pressure"}

    @staticmethod
    def _is_low_power(economy: dict[str, Any]) -> bool:
        if bool(economy.get("low_power")):
            return True
        power = int(economy.get("power", 0) or 0)
        drained = int(economy.get("power_drained", 0) or 0)
        return power > 0 and drained > power

    @staticmethod
    def _queue_blocked(summary: dict[str, Any], queues: dict[str, Any]) -> bool:
        summary_economy = dict(summary.get("economy") or {})
        if bool(summary_economy.get("queue_blocked")):
            return True
        for queue in queues.values():
            if not isinstance(queue, dict):
                continue
            if queue.get("has_ready_item"):
                return True
            items = list(queue.get("items", []))
            if items and all(bool(item.get("paused")) for item in items):
                return True
        return False

    @staticmethod
    def _needs_mobile_scout(
        params: dict[str, Any],
        my_actors: list[dict[str, Any]],
        queues: dict[str, Any],
    ) -> bool:
        if not bool(params.get("need_mobile_scout", False)):
            return False
        if "Vehicle" not in queues:
            return False
        for actor in my_actors:
            if actor.get("category") != "vehicle":
                continue
            if actor.get("mobility") != "fast":
                continue
            if actor.get("is_alive") is False:
                continue
            return False
        return True

    @staticmethod
    def _needs_vehicle_gateway(
        params: dict[str, Any],
        my_actors: list[dict[str, Any]],
        queues: dict[str, Any],
    ) -> bool:
        if not bool(params.get("need_mobile_scout", False)):
            return False
        if "Vehicle" in queues:
            return False
        has_economy_anchor = any(has_role(actor, "economy_anchor") for actor in my_actors)
        has_vehicle_gateway = any(has_role(actor, "vehicle_gateway") for actor in my_actors)
        return has_economy_anchor and not has_vehicle_gateway


_PLANNER_REGISTRY: dict[str, PlannerExpert] = {
    "ProductionAdvisor": ProductionAdvisor(),
}


def query_planner(
    planner_type: str,
    params: Optional[dict[str, Any]],
    world_state: dict[str, Any],
) -> dict[str, Any]:
    planner = _PLANNER_REGISTRY.get(planner_type)
    if planner is None:
        return {
            "planner_type": planner_type,
            "status": "not_supported",
            "reason": f"{planner_type} is not implemented in the current runtime.",
        }
    return planner.plan(planner_type, dict(params or {}), world_state)
