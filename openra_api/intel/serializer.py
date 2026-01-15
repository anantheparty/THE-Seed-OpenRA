from __future__ import annotations

from typing import Any, Dict

from .model import IntelModel


class IntelSerializer:
    """负责将 IntelModel 序列化为对外结构（brief/debug）。"""

    @staticmethod
    def to_debug(model: IntelModel) -> Dict[str, Any]:
        return {
            "t": model.meta.get("game_time"),
            "meta": model.meta,
            "economy": model.economy,
            "tech": model.tech,
            "forces": model.forces,
            "battle": model.battle,
            "opportunities": model.opportunities,
            "map_control": model.map_control,
            "alerts": list(model.alerts),
            "actors_actions": model.actors_actions,
            "legacy": model.legacy,
        }

    @staticmethod
    def to_brief(model: IntelModel) -> Dict[str, Any]:
        economy = model.economy or {}
        tech = model.tech or {}
        forces = model.forces or {}
        battle = model.battle or {}
        opportunities = model.opportunities or []
        map_control = model.map_control or {}
        alerts = list(model.alerts or [])

        tech_level = tech.get("tech_level_est", 0) or 0
        tier = min(max(int(tech_level), 0), 4)
        if tier <= 1:
            stage = "opening"
        elif tier == 2:
            stage = "mid"
        else:
            stage = "late"

        key_order = ("兵营", "车间", "雷达", "科技中心")
        owned_keys = tech.get("owned_key_buildings", {}) or {}
        next_missing = None
        for name in key_order:
            if owned_keys.get(name, 0) <= 0:
                next_missing = name
                break

        queue_blocked = "none"
        queues = economy.get("production_queues") or {}
        for q in queues.values():
            reason = q.get("queue_blocked_reason")
            if reason in ("ready_not_placed", "paused"):
                queue_blocked = reason
                if reason == "ready_not_placed":
                    break
            elif reason and queue_blocked == "none":
                queue_blocked = "unknown"

        power = economy.get("power") or {}
        power_ok = True
        surplus = power.get("surplus")
        if isinstance(surplus, (int, float)):
            power_ok = surplus >= 0

        miners = economy.get("miners")
        refineries = economy.get("refineries", 0)

        my_force = forces.get("my", {}) or {}
        enemy_force = forces.get("enemy", {}) or {}
        my_value = int(my_force.get("army_value_est", 0) or 0)
        enemy_visible = enemy_force.get("visible_units", 0) or 0
        enemy_value = None if enemy_visible == 0 else int(enemy_force.get("army_value_est", 0) or 0)

        threats = battle.get("threats_to_base") or []
        threat_near_base = "none"
        if threats:
            top = threats[0]
            dist = top.get("distance", 999)
            score = top.get("threat_score", 0)
            if dist <= 12 or score >= 220:
                threat_near_base = "high"
            elif dist <= 20 or score >= 140:
                threat_near_base = "med"
            else:
                threat_near_base = "low"

        engagements = battle.get("engagements") or {}
        engaged = bool(engagements.get("engaged_units", 0))

        best_target = None
        best_score = None
        if opportunities:
            best = opportunities[0]
            best_target = {"type": best.get("type"), "pos": best.get("pos")}
            best_score = int(best.get("opportunity_score", 0) or 0)

        explored = map_control.get("explored_ratio")
        scout_need = bool(model.meta.get("scout_stalled")) or ("侦察停滞" in alerts)
        nearest_resource = None
        rs = map_control.get("resource_summary")
        if rs and isinstance(rs, dict) and rs.get("nearest_to_base"):
            nearest_resource = rs.get("nearest_to_base")

        brief_alerts = alerts[:3]

        return {
            "t": model.meta.get("game_time"),
            "stage": stage,
            "economy": {
                "cash": economy.get("cash"),
                "power_ok": power_ok,
                "miners": miners if miners is not None else None,
                "refineries": refineries,
                "queue_blocked": queue_blocked,
            },
            "tech": {
                "tier": min(tier, 3),
                "next_missing": next_missing,
            },
            "combat": {
                "my_value": my_value,
                "enemy_value": enemy_value,
                "threat_near_base": threat_near_base,
                "engaged": engaged,
            },
            "opportunity": {
                "best_target": best_target,
                "best_score": best_score,
            },
            "map": {
                "explored": explored,
                "scout_need": scout_need,
                "nearest_resource": nearest_resource,
            },
            "alerts": brief_alerts,
        }


