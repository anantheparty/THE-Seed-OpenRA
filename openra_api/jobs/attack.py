from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..action.attack import AttackAction
from ..action.move import MoveAction
from ..intel.names import normalize_unit_name
from ..intel.rules import DEFAULT_UNIT_CATEGORY_RULES
from ..models import Actor, Location, MapQueryResult
from .base import ActorAssignment, Job, TickContext
from .utils import actor_pos, clamp_location


class AttackJob(Job):
    """攻击：有敌人则分配最近目标攻击；无敌人则谨慎前进（attack-move）。"""

    NAME = "attack"

    def __init__(self, job_id: str = "attack", step: int = 8) -> None:
        super().__init__(job_id=job_id)
        self.step = max(4, int(step))
        self._advance_anchor: Optional[Location] = None
        self._externally_controlled = False

    def set_externally_controlled(self, enabled: bool) -> None:
        """开启后由外部系统接管（如战略栈），本 Job 不再直接下发命令。"""
        self._externally_controlled = bool(enabled)

    def _tick_impl(self, ctx: TickContext, actors: List[Actor]) -> None:
        snapshot = ctx.intel.get_snapshot(force=False)
        intel_model = ctx.intel.get_intel(force=False)

        # 不再从游戏里“抢人”，只使用 manager 显式分配进来的 actors
        my_units: List[Actor] = list(actors or [])
        if not my_units:
            self.last_summary = "暂无分配到 AttackJob 的单位"
            return

        if self._externally_controlled:
            self.last_summary = f"AttackJob 已交由战略系统接管，单位数={len(my_units)}"
            return

        enemy_actions = (intel_model.actors_actions.get("enemy", {}) or {}).get("actors", [])
        mobile_targets: List[Tuple[int, Location]] = []
        building_targets: List[Tuple[int, Location]] = []
        for e in enemy_actions:
            pos = e.get("pos") or {}
            try:
                eid = int(e.get("id"))
                epos = Location(int(pos["x"]), int(pos["y"]))
            except Exception:
                continue

            etype = normalize_unit_name(str(e.get("type", "") or ""))
            category = DEFAULT_UNIT_CATEGORY_RULES.get(etype, "unknown")
            is_building = category in ("building", "defense") or etype.endswith(("厂", "站", "中心", "塔", "炮"))
            if is_building:
                building_targets.append((eid, epos))
            else:
                mobile_targets.append((eid, epos))

        # 1) 有敌人：先打敌军机动单位；清空后打建筑（显式 attack）
        if mobile_targets or building_targets:
            target_pool = mobile_targets if mobile_targets else building_targets
            phase_name = "敌军单位" if mobile_targets else "敌方建筑"
            issued = 0
            for u in my_units:
                uid = int(getattr(u, "actor_id", getattr(u, "id", -1)))
                upos = actor_pos(u)
                if uid < 0 or upos is None:
                    continue

                # 找最近目标（使用 actors_actions 带的 pos）
                best_id: Optional[int] = None
                best_dist: Optional[int] = None
                for eid, epos in target_pool:
                    d = upos.manhattan_distance(epos)
                    if best_dist is None or d < best_dist:
                        best_dist = d
                        best_id = eid

                if best_id is None:
                    continue

                self.assignments[uid] = ActorAssignment(kind="attack", target_actor_id=best_id, note="enemy_visible")
                ass = self.assignments[uid]
                if ctx.now - ass.issued_at < ass.cooldown_s:
                    continue

                AttackAction(api=ctx.api, attackers=[u], target=best_id).run()
                ass.issued_at = ctx.now
                issued += 1

            self.last_summary = (
                f"发现目标={len(target_pool)} 类型={phase_name} 作战单位={len(my_units)} 下达攻击={issued}"
            )
            return

        # 2) 无敌人：谨慎推进（attack-move），朝“可能威胁方向”
        map_info: Optional[MapQueryResult] = ctx.intel.get_map_info(force=False)
        base_center = ctx.intel.get_base_center(snapshot)
        width = int(getattr(map_info, "MapWidth", 0) or 0) if map_info else 0
        height = int(getattr(map_info, "MapHeight", 0) or 0) if map_info else 0

        # OpenRA 坐标确定为 1-based：范围 [1,1]..[w,h]
        origin = 1

        target_center = self._choose_threat_direction(ctx, base_center, width, height, origin)

        # 形成一个前进锚点：从我方单位质心朝目标走一步
        my_centroid = intel_model.forces.get("my", {}).get("centroid")
        if my_centroid and isinstance(my_centroid, dict):
            start = Location(int(my_centroid.get("x", base_center.x)), int(my_centroid.get("y", base_center.y)))
        else:
            start = base_center

        anchor = self._step_towards(start, target_center, self.step)
        if width and height:
            anchor = clamp_location(anchor, width, height)
        self._advance_anchor = anchor

        # 给每个单位一个略微分散的落点
        offsets: List[Tuple[int, int]] = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2), (2, -2)]
        issued = 0
        for idx, u in enumerate(my_units):
            uid = int(getattr(u, "actor_id", getattr(u, "id", -1)))
            if uid < 0:
                continue
            dx, dy = offsets[idx % len(offsets)]
            dst = Location(anchor.x + dx, anchor.y + dy)
            if width and height:
                dst = clamp_location(dst, width, height)

            self.assignments[uid] = ActorAssignment(kind="move", target_pos=dst, note="advance_attack_move")
            ass = self.assignments[uid]
            if ctx.now - ass.issued_at < ass.cooldown_s:
                continue

            MoveAction(api=ctx.api, actors=[u], location=dst, attack_move=True).run()
            ass.issued_at = ctx.now
            issued += 1

        self.last_summary = f"未发现敌人 作战单位={len(my_units)} 前进点={anchor.x},{anchor.y} 下达移动={issued}"

    def _choose_threat_direction(self, ctx: TickContext, base_center: Location, width: int, height: int, origin: int) -> Location:
        # 优先：敌人 last_seen 质心
        last = ctx.intel.memory.enemy_last_seen or {}
        if last:
            xs = []
            ys = []
            for v in last.values():
                pos = v.get("pos") or {}
                if "x" in pos and "y" in pos:
                    xs.append(int(pos["x"]))
                    ys.append(int(pos["y"]))
            if xs and ys:
                return Location(sum(xs) // len(xs), sum(ys) // len(ys))

        # 次选：地图中心
        if width and height:
            # origin=0 => [0..w-1]；origin=1 => [1..w]
            return Location(origin + (width // 2), origin + (height // 2))

        # 兜底：向右下推进
        return Location(base_center.x + 20, base_center.y + 20)

    def _step_towards(self, start: Location, goal: Location, step: int) -> Location:
        dx = goal.x - start.x
        dy = goal.y - start.y
        # 曼哈顿方向推进
        if abs(dx) > abs(dy):
            return Location(start.x + (step if dx > 0 else -step), start.y)
        return Location(start.x, start.y + (step if dy > 0 else -step))

