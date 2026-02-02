from __future__ import annotations
from typing import Any, Dict, List

from openra_api.game_api import GameAPI
from openra_api.game_midlayer import RTSMiddleLayer
from openra_api.models import TargetsQueryParam
from the_seed.utils import LogManager

logger = LogManager.get_logger()


class OpenRAEnv:
    """
    OpenRA 观测包装器。
    """

    def __init__(self, api: GameAPI) -> None:
        self.api = api
        # 复用同一个中间层实例以启用缓存
        self.mid = RTSMiddleLayer(api)

    def observe(self) -> str:
        """返回当前游戏状态的文本概要。"""
        snapshot = self._collect_snapshot()
        text = self._format_snapshot(snapshot)
        return text

    def register_actions(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - legacy shim
        logger.warning("register_actions 已废弃：OpenRAEnv 仅提供字符串观测。调用将被忽略。")

    # ---------------- Internal helpers ---------------- #
    def _collect_snapshot(self) -> Dict[str, Any]:
        report = self.mid.intel(mode="brief")
        snapshot: Dict[str, Any] = {"report": report}

        # 查询自己基地位置
        try:
            bases = self.api.query_actor(TargetsQueryParam(type=["建造厂"], faction="自己"))
            if bases and bases[0].position:
                snapshot["my_base"] = {"x": bases[0].position.x, "y": bases[0].position.y}
        except Exception:
            pass

        # 查询敌方残影（frozen actors）
        try:
            _, frozen = self.api.query_actorwithfrozen(TargetsQueryParam(faction="敌人"))
            snapshot["frozen_enemies"] = frozen
        except Exception:
            pass

        # 查询当前可见的敌人
        try:
            visible_enemies = self.api.query_actor(TargetsQueryParam(faction="敌人"))
            snapshot["visible_enemies"] = visible_enemies
        except Exception:
            pass

        # 查询电力/经济详情
        try:
            base_info = self.api.player_base_info_query()
            snapshot["base_info"] = base_info
        except Exception:
            pass

        return snapshot

    def _format_snapshot(self, snapshot: Dict[str, Any]) -> str:
        report = snapshot.get("report") or {}
        econ = report.get("economy") or {}
        tech = report.get("tech") or {}
        combat = report.get("combat") or {}
        opp = report.get("opportunity") or {}
        map_info = report.get("map") or {}
        alerts = report.get("alerts") or []

        best_target = opp.get("best_target")
        best_target_str = (
            f"{best_target.get('type')}@{best_target.get('pos')}" if isinstance(best_target, dict) else "None"
        )

        my_base = snapshot.get("my_base")
        base_str = f"x={my_base['x']},y={my_base['y']}" if my_base else "未知"

        # 电力/经济详情（来自 PlayerBaseInfo）
        base_info = snapshot.get("base_info")
        if base_info:
            power_status = "正常" if base_info.Power >= 0 else "断电!"
            power_str = (f"Cash={base_info.Cash} Resources={base_info.Resources} "
                         f"Power={base_info.Power}({power_status}) "
                         f"供电={base_info.PowerProvided} 耗电={base_info.PowerDrained}")
        else:
            power_str = "无法查询"

        lines = [
            f"[Intel] t={report.get('t')} stage={report.get('stage')}",
            f"[MyBase] position=({base_str})",
            f"[PlayerInfo] {power_str}",
            f"[Economy] miners={econ.get('miners')} "
            f"refineries={econ.get('refineries')} queue_blocked={econ.get('queue_blocked')}",
            f"[Tech] tier={tech.get('tier')} next_missing={tech.get('next_missing')}",
            f"[Combat] my_value={combat.get('my_value')} enemy_value={combat.get('enemy_value')} "
            f"threat_near_base={combat.get('threat_near_base')} engaged={combat.get('engaged')}",
            f"[Opportunity] best_target={best_target_str} best_score={opp.get('best_score')}",
            f"[Map] explored={map_info.get('explored')} scout_need={map_info.get('scout_need')} "
            f"nearest_resource={map_info.get('nearest_resource')}",
        ]

        # 敌方残影 — 之前见过但现在被迷雾覆盖的建筑/单位
        frozen = snapshot.get("frozen_enemies") or []
        if frozen:
            frozen_strs = []
            for fa in frozen[:15]:  # 最多显示15个
                pos = fa.position
                frozen_strs.append(f"{fa.type}@({pos.x},{pos.y})" if pos else fa.type or "?")
            lines.append(f"[EnemyFrozen] {len(frozen)}个残影: {', '.join(frozen_strs)}")
        else:
            lines.append("[EnemyFrozen] 无残影（未发现过敌方建筑/单位）")

        # 当前可见敌人
        visible = snapshot.get("visible_enemies") or []
        if visible:
            vis_strs = []
            for e in visible[:15]:
                pos = e.position
                hp = f" hp={e.hppercent}%" if e.hppercent is not None else ""
                vis_strs.append(f"{e.type}@({pos.x},{pos.y}){hp}" if pos else e.type or "?")
            lines.append(f"[EnemyVisible] {len(visible)}个可见: {', '.join(vis_strs)}")
        else:
            lines.append("[EnemyVisible] 当前无可见敌人")

        lines.append(f"[Alerts] {', '.join(alerts) if alerts else 'none'}")
        return "\n".join(lines)
