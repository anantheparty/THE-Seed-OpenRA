from __future__ import annotations
from typing import Any, Dict, List
from collections import Counter
import time

from openra_api.game_api import GameAPI
from openra_api.models import Actor, TargetsQueryParam
from the_seed.utils import LogManager

logger = LogManager.get_logger()


class OpenRAEnv:
    """
    OpenRA 观测包装器。
    """

    def __init__(self, api: GameAPI) -> None:
        self.api = api

    def observe(self) -> str:
        """返回当前游戏状态的文本概要。"""
        snapshot = self._collect_snapshot()
        text = self._format_snapshot(snapshot)
        # logger.debug("OpenRAEnv snapshot=%s", snapshot)
        return text

    def register_actions(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - legacy shim
        logger.warning("register_actions 已废弃：OpenRAEnv 仅提供字符串观测。调用将被忽略。")

    # ---------------- Internal helpers ---------------- #
    def _collect_snapshot(self) -> Dict[str, Any]:
        base = self._safe_base_info()
        friendly_units = self._summarize_units(self._query_units("自己"))
        enemy_units = self._summarize_units(self._query_units("敌方"))
        report = self._build_report(base, friendly_units, enemy_units)
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "economy": base,
            "friendly_units": friendly_units,
            "enemy_units": enemy_units,
            "report": report,
        }

    def _safe_base_info(self) -> Dict[str, Any]:
        try:
            base = self.api.player_base_info_query()
        except Exception as exc:  # noqa: BLE001
            logger.error("player_base_info_query failed: %s", exc)
            return {"error": str(exc)}

        power_provided = getattr(base, "PowerProvided", getattr(base, "Power", 0))
        power_drained = getattr(base, "PowerDrained", 0)
        power_surplus = power_provided - power_drained
        return {
            "cash": getattr(base, "Cash", 0),
            "resources": getattr(base, "Resources", 0),
            "power": getattr(base, "Power", 0),
            "power_surplus": power_surplus,
            "power_status": "stable" if power_surplus >= 0 else "low_power",
        }

    def _query_units(self, faction: str) -> List[Actor]:
        if not hasattr(self.api, "query_actor"):
            return []
        try:
            return self.api.query_actor(TargetsQueryParam(faction=faction)) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("query_actor failed for faction=%s: %s", faction, exc)
            return []

    def _summarize_units(self, units: List[Actor]) -> Dict[str, Any]:
        counts: Counter[str] = Counter()
        for unit in units:
            counts[(unit.type or "unknown")] += 1
        total = sum(counts.values())
        return {"total": total, "by_type": dict(counts)}

    def _build_report(self, economy: Dict[str, Any], friendly: Dict[str, Any], enemy: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "economy": economy,
            "friendly": friendly,
            "enemy": enemy,
        }

    def _format_snapshot(self, snapshot: Dict[str, Any]) -> str:
        report = snapshot.get("report", {})
        econ = report.get("economy", {})
        friendly = report.get("friendly", {})
        enemy = report.get("enemy", {})

        lines = [
            f"[Timestamp] {snapshot.get('timestamp')}",
            "[Economy]",
            f"  cash={econ.get('cash')} resources={econ.get('resources')} power={econ.get('power')} "
            f"surplus={econ.get('power_surplus')} status={econ.get('power_status')}",
            "[Friendly Units]",
            self._format_unit_line(friendly),
            "[Enemy Units]",
            self._format_unit_line(enemy),
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_unit_line(summary: Dict[str, Any]) -> str:
        by_type = summary.get("by_type") or {}
        if not by_type:
            detail = "none"
        else:
            detail = ", ".join(f"{k}:{v}" for k, v in by_type.items())
        return f"  total={summary.get('total', 0)} ({detail})"
