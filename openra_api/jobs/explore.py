from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from ..action.move import MoveAction
from ..models import Actor, Location, MapQueryResult
from .base import ActorAssignment, Job, TickContext
from .utils import actor_pos, clamp_location


# ----------------------------
# Grid helpers
# ----------------------------

def _is_explored(exp: List[List[bool]], x: int, y: int, w: int, h: int, layout: str) -> bool:
    if x < 0 or y < 0 or x >= w or y >= h:
        return False
    try:
        if layout == "col_major":
            if x >= len(exp):
                return False
            col = exp[x] or []
            if y >= len(col):
                return False
            return bool(col[y])
        if y >= len(exp):
            return False
        row = exp[y] or []
        if x >= len(row):
            return False
        return bool(row[x])
    except Exception:
        return False


def _detect_origin(scout_positions: List[Location], w: int, h: int) -> int:
    if not scout_positions or not w or not h:
        return 1
    xs = [p.x for p in scout_positions]
    ys = [p.y for p in scout_positions]
    if any(v == 0 for v in xs + ys):
        return 0
    if any(v == w or v == h for v in xs + ys):
        return 1
    if max(xs) <= w - 1 and max(ys) <= h - 1:
        return 0
    return 1


def _choose_layout(exp: List[List[bool]], w: int, h: int, origin: int, scouts: List[Location]) -> str:
    # 用“侦察兵脚下通常应当是 explored”来判 row/col（方形图避免你之前的斜角坑）
    def score(layout: str) -> int:
        s = 0
        for p in scouts:
            gx = max(0, min(w - 1, p.x - origin))
            gy = max(0, min(h - 1, p.y - origin))
            s += 1000 if _is_explored(exp, gx, gy, w, h, layout) else -1000
            # 再加一点局部密度
            cnt = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    x, y = gx + dx, gy + dy
                    if 0 <= x < w and 0 <= y < h and _is_explored(exp, x, y, w, h, layout):
                        cnt += 1
            s += cnt
        return s

    return "row_major" if score("row_major") >= score("col_major") else "col_major"


def _manhattan(a: Location, b: Location) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def _bresenham(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
    # 4-connected 采样用直线格子序列
    pts: List[Tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        pts.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
    return pts


def _unexplored_ratio_on_line(
    exp: List[List[bool]],
    w: int,
    h: int,
    layout: str,
    origin: int,
    cur: Location,
    tgt: Location,
) -> float:
    x0 = max(0, min(w - 1, cur.x - origin))
    y0 = max(0, min(h - 1, cur.y - origin))
    x1 = max(0, min(w - 1, tgt.x - origin))
    y1 = max(0, min(h - 1, tgt.y - origin))

    pts = _bresenham(x0, y0, x1, y1)
    if len(pts) <= 1:
        return 0.0

    # 跳过起点（起点大概率已探索，不然阈值会很难达成）
    total = 0
    unexp = 0
    for x, y in pts[1:]:
        total += 1
        if not _is_explored(exp, x, y, w, h, layout):
            unexp += 1
    return float(unexp) / float(total) if total > 0 else 0.0


def _xorshift32(v: int) -> int:
    v &= 0xFFFFFFFF
    v ^= (v << 13) & 0xFFFFFFFF
    v ^= (v >> 17) & 0xFFFFFFFF
    v ^= (v << 5) & 0xFFFFFFFF
    return v & 0xFFFFFFFF


def _rand01(seed: int) -> float:
    # [0,1)
    v = _xorshift32(seed)
    return (v & 0xFFFFFF) / float(1 << 24)


def _hash_seed(*xs: int) -> int:
    v = 2166136261
    for x in xs:
        v ^= (x & 0xFFFFFFFF)
        v = (v * 16777619) & 0xFFFFFFFF
    return v


# ----------------------------
# Job state
# ----------------------------

@dataclass
class _ScoutState:
    target: Optional[Location] = None
    visited: Set[str] = None
    last_pos: Optional[Location] = None
    stuck_ticks: int = 0
    base_angle: float = 0.0  # 每个 actor 固定的“主方向”，天然分散
    last_pick_at: float = 0.0

    def __post_init__(self) -> None:
        if self.visited is None:
            self.visited = set()


# ----------------------------
# Explore Job (random ray)
# ----------------------------

class ExploreJob(Job):
    """
    简单探索分配（按你的 1/2/3）：
    1) 在范围内沿随机方向找未探索格（找不到则扩大范围）
    2) 要求“当前位置->目标”的直线采样里，未探索比例 >= 阈值；扩大范围时阈值递减
    3) 选定后粘住：到达/接近/卡住前不换目标
    """

    NAME = "explore"

    def __init__(
        self,
        job_id: str = "explore",
        base_radius: int = 18,
        radius_step: int = 8,
        max_radius: int = 60,
        threshold_start: float = 0.80,
        threshold_drop_per_expand: float = 0.07,
        threshold_min: float = 0.35,
        tries_per_expand: int = 18,
        stick_distance: int = 2,
        stuck_threshold_ticks: int = 10,
        repulsion_radius: int = 10,
    ) -> None:
        super().__init__(job_id=job_id)
        self.base_radius = int(base_radius)
        self.radius_step = int(radius_step)
        self.max_radius = int(max_radius)

        self.threshold_start = float(threshold_start)
        self.threshold_drop_per_expand = float(threshold_drop_per_expand)
        self.threshold_min = float(threshold_min)

        self.tries_per_expand = int(tries_per_expand)
        self.stick_distance = int(stick_distance)
        self.stuck_threshold_ticks = int(stuck_threshold_ticks)
        self.repulsion_radius = int(repulsion_radius)

        self._scout_state: Dict[int, _ScoutState] = {}

    def on_unassigned(self, actor_id: int) -> None:
        super().on_unassigned(actor_id)
        self._scout_state.pop(int(actor_id), None)

    def _tick_impl(self, ctx: TickContext, actors: List[Actor]) -> None:
        map_info: Optional[MapQueryResult] = ctx.intel.get_map_info(force=False)
        if not map_info:
            self.last_summary = "无法获取地图信息"
            return

        scouts: List[Actor] = list(actors or [])
        if not scouts:
            self.last_summary = "暂无分配到 ExploreJob 的单位"
            return

        w = int(getattr(map_info, "MapWidth", 0) or 0)
        h = int(getattr(map_info, "MapHeight", 0) or 0)
        exp = getattr(map_info, "IsExplored", None) or []
        if not w or not h or not exp:
            self.last_summary = "地图维度/IsExplored 异常"
            return

        scouts_sorted = sorted(
            scouts,
            key=lambda a: int(getattr(a, "actor_id", getattr(a, "id", 0)) or 0),
        )

        scout_info: List[Tuple[int, Actor, Location]] = []
        for s in scouts_sorted:
            sid = int(getattr(s, "actor_id", getattr(s, "id", -1)))
            p = actor_pos(s)
            if sid >= 0 and p is not None:
                scout_info.append((sid, s, p))

        if not scout_info:
            self.last_summary = "侦察兵位置不可用"
            return

        origin = _detect_origin([p for _, _, p in scout_info], w, h)
        layout = _choose_layout(exp, w, h, origin, [p for _, _, p in scout_info])

        # 本 tick 已选目标（用于 repulsion）
        chosen_targets: List[Location] = []
        retargeted = 0
        issued = 0

        for sid, actor, cur in scout_info:
            st = self._scout_state.get(sid)
            if st is None:
                st = _ScoutState()
                # 给每个 actor 一个固定主方向：golden-angle 分散 + id hash
                golden = 2.399963229728653  # radians
                st.base_angle = (sid * golden) % (math.tau)
                self._scout_state[sid] = st

            # stuck 检测
            if st.last_pos is not None and _manhattan(cur, st.last_pos) <= 1:
                st.stuck_ticks += 1
            else:
                st.stuck_ticks = 0
            st.last_pos = cur

            def key_of(loc: Location) -> str:
                return f"{loc.x},{loc.y}"

            def is_unexplored_world(loc: Location) -> bool:
                gx = loc.x - origin
                gy = loc.y - origin
                if gx < 0 or gy < 0 or gx >= w or gy >= h:
                    return False
                return not _is_explored(exp, gx, gy, w, h, layout)

            def too_close_to_others(loc: Location) -> bool:
                for ot in chosen_targets:
                    if abs(loc.x - ot.x) + abs(loc.y - ot.y) < self.repulsion_radius:
                        return True
                return False

            # 3) 粘住：到达/接近前不换；除非卡住
            keep = False
            if st.target is not None:
                if _manhattan(cur, st.target) > self.stick_distance and st.stuck_ticks < self.stuck_threshold_ticks:
                    keep = True

            # 到达/接近：标记 visited，允许重新选
            if st.target is not None and _manhattan(cur, st.target) <= self.stick_distance:
                st.visited.add(key_of(st.target))
                st.target = None
                keep = False

            if not keep:
                picked = self._pick_target_random_ray(
                    ctx=ctx,
                    sid=sid,
                    cur=cur,
                    exp=exp,
                    w=w,
                    h=h,
                    layout=layout,
                    origin=origin,
                    visited=st.visited,
                    base_angle=st.base_angle,
                    chosen_targets=chosen_targets,
                    is_unexplored_world=is_unexplored_world,
                    too_close_to_others=too_close_to_others,
                )
                if picked is not None:
                    st.target = picked
                    st.last_pick_at = ctx.now
                    retargeted += 1

            if st.target is None:
                continue

            chosen_targets.append(st.target)

            # 下发移动
            self.assignments[sid] = ActorAssignment(kind="move", target_pos=st.target, note=f"ray_explore:{layout}")
            ass = self.assignments[sid]
            if ctx.now - ass.issued_at < ass.cooldown_s:
                continue

            MoveAction(api=ctx.api, actors=[actor], location=ass.target_pos, attack_move=False).run()
            ass.issued_at = ctx.now
            issued += 1

        self.last_summary = f"layout={layout} origin={origin} scouts={len(scout_info)} retarget={retargeted} issued={issued}"

    def _pick_target_random_ray(
        self,
        ctx: TickContext,
        sid: int,
        cur: Location,
        exp: List[List[bool]],
        w: int,
        h: int,
        layout: str,
        origin: int,
        visited: Set[str],
        base_angle: float,
        chosen_targets: List[Location],
        is_unexplored_world,
        too_close_to_others,
    ) -> Optional[Location]:
        def key_of(loc: Location) -> str:
            return f"{loc.x},{loc.y}"

        expands = max(1, int((self.max_radius - self.base_radius) / max(1, self.radius_step)) + 1)

        # 用一个时间桶让“尝试方向”不会每 tick 抖得太厉害，但也不会永远固定
        t_bucket = int(ctx.now // 1.0)  # 1s 一桶

        for ei in range(expands):
            radius = self.base_radius + ei * self.radius_step
            if radius > self.max_radius:
                break

            thr = max(self.threshold_min, self.threshold_start - ei * self.threshold_drop_per_expand)

            for ti in range(self.tries_per_expand):
                seed = _hash_seed(sid, t_bucket, ei, ti)
                # 方向：主方向 + 抖动 + 少量偏移（避免大家同桶同向）
                jitter = (_rand01(seed) - 0.5) * (math.pi / 3.0)  # +-60°
                angle = (base_angle + jitter + (ti * 0.35)) % math.tau

                # 距离：偏向外圈一点
                r01 = _rand01(seed ^ 0x9E3779B9)
                dist = int(radius * (0.65 + 0.35 * r01))

                tx = int(round(cur.x + math.cos(angle) * dist))
                ty = int(round(cur.y + math.sin(angle) * dist))
                tgt = clamp_location(Location(tx, ty), w, h)

                if key_of(tgt) in visited:
                    continue
                if not is_unexplored_world(tgt):
                    continue
                if too_close_to_others(tgt):
                    continue

                ratio = _unexplored_ratio_on_line(exp, w, h, layout, origin, cur, tgt)
                if ratio >= thr:
                    return tgt

            # 1) 找不到就扩大范围；同时 2) 阈值按 ei 自然下降（thr 已下降）

        return None