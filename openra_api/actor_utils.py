from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .intel.names import normalize_unit_name
from .models import Actor, Location


SUPPORT_UNITS: Set[str] = {"矿车", "工程师", "mcv", "基地车"}
SCOUT_PRIORITY: Tuple[str, ...] = ("步兵", "狗", "工程师", "火箭兵")
MOBILE_UNIT_EXCEPTIONS: Set[str] = {
    "mcv",
    "基地车",
    "矿车",
    "采矿车",
    "步兵",
    "火箭兵",
    "工程师",
    "防空车",
    "装甲车",
    "重坦",
    "v2",
    "猛犸坦克",
}
EXPLICIT_STATIC_STRUCTURES: Set[str] = {
    "电厂",
    "兵营",
    "矿场",
    "车间",
    "雷达",
    "维修中心",
    "核电",
    "科技中心",
    "机场",
    "建造厂",
    "基地",
}
STATIC_SUFFIXES: Tuple[str, ...] = ("厂", "站", "中心", "塔", "炮")


def actor_pos(actor: Actor) -> Optional[Location]:
    pos = getattr(actor, "position", None)
    return pos if isinstance(pos, Location) else None


def is_likely_static_structure(unit_type: Optional[str]) -> bool:
    t = normalize_unit_name(unit_type)
    if not t or t == "未知":
        return False
    tl = t.lower()
    if tl in MOBILE_UNIT_EXCEPTIONS or t in MOBILE_UNIT_EXCEPTIONS:
        return False
    if tl in EXPLICIT_STATIC_STRUCTURES or t in EXPLICIT_STATIC_STRUCTURES:
        return True
    return any(t.endswith(suffix) for suffix in STATIC_SUFFIXES)


def is_support_unit(unit_type: Optional[str]) -> bool:
    t = normalize_unit_name(unit_type)
    return t in SUPPORT_UNITS or t.lower() in SUPPORT_UNITS


def select_scouts(actors: Sequence[Actor], max_scouts: int = 1) -> List[Actor]:
    """优先选便宜/快速单位做侦察。"""

    def priority(t: Optional[str]) -> int:
        t = normalize_unit_name(t)
        try:
            return SCOUT_PRIORITY.index(t)
        except ValueError:
            return len(SCOUT_PRIORITY)

    sorted_units = sorted(actors, key=lambda a: priority(getattr(a, "type", None)))
    selected: List[Actor] = []
    for a in sorted_units:
        if len(selected) >= max_scouts:
            break
        if actor_pos(a) is None:
            continue
        if is_likely_static_structure(getattr(a, "type", None)):
            continue
        selected.append(a)
    return selected


def select_combat_units(actors: Iterable[Actor]) -> List[Actor]:
    """排除 support 单位（矿车/工程师/MCV 等）。"""
    res: List[Actor] = []
    for a in actors:
        t = getattr(a, "type", None)
        if not t:
            continue
        if is_support_unit(t):
            continue
        if actor_pos(a) is None:
            continue
        if is_likely_static_structure(t):
            continue
        res.append(a)
    return res

