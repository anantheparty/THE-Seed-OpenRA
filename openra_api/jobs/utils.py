from __future__ import annotations

from typing import List, Tuple

from ..actor_utils import actor_pos, select_combat_units, select_scouts
from ..models import Location


def clamp_location(pos: Location, width: int, height: int, origin: int = 1) -> Location:
    # OpenRA 坐标确定为 1-based：默认 [1..w] / [1..h]
    min_x = int(origin)
    min_y = int(origin)
    max_x = max(int(origin + width - 1), int(origin))
    max_y = max(int(origin + height - 1), int(origin))

    x = min(max(int(pos.x), min_x), max_x)
    y = min(max(int(pos.y), min_y), max_y)
    return Location(x, y)


def spread_around(center: Location, offsets: List[Tuple[int, int]]) -> List[Location]:
    return [Location(center.x + dx, center.y + dy) for dx, dy in offsets]


