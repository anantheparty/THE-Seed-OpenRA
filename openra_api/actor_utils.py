from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .intel.names import normalize_unit_name
from .models import Actor, Location


SUPPORT_UNITS: Set[str] = {"矿车", "工程师", "mcv", "基地车"}
SCOUT_PRIORITY: Tuple[str, ...] = ("步兵", "狗", "工程师", "火箭兵")


def actor_pos(actor: Actor) -> Optional[Location]:
    pos = getattr(actor, "position", None)
    return pos if isinstance(pos, Location) else None


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
        res.append(a)
    return res


