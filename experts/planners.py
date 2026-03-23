"""Planner Experts — proposal generators using deterministic rules/scoring."""

from __future__ import annotations

from typing import Any, Optional

from .base import PlannerExpert


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
        if self._no_visible_enemy(params, enemy_actors):
            return {
                "action": "scout_first",
                "unit_type": None,
                "queue_type": None,
                "count": None,
                "prerequisites": [],
                "reason": "no_visible_enemy",
                "recommended_expert": "ReconExpert",
            }

        if self._is_low_power(economy):
            return {
                "action": "tech_up",
                "unit_type": "powr",
                "queue_type": "Building",
                "count": 1,
                "prerequisites": [],
                "reason": "low_power",
                "recommended_expert": "EconomyExpert",
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
            }

        if self._needs_mobile_scout(params, my_actors, queues):
            return {
                "action": "produce",
                "unit_type": "jeep",
                "queue_type": "Vehicle",
                "count": 1,
                "prerequisites": [],
                "reason": "need_mobile_scout",
                "recommended_expert": "EconomyExpert",
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
