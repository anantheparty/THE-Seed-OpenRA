from __future__ import annotations

from dataclasses import dataclass

from .intel.names import normalize_unit_name
from .models import Actor, Location


@dataclass(frozen=True)
class ActorView:
    """Actor 的轻量快照视图"""

    id: str
    type: str
    faction: str
    pos: Location
    hp_percent: int
    # 由服务端 query_actor 返回（C# 侧 ServerCommands.cs 填充）
    activity: str = ""
    order: str = ""

    @classmethod
    def from_actor(cls, actor: Actor) -> "ActorView":
        actor_id = getattr(actor, "actor_id", getattr(actor, "id", None))
        actor_type = normalize_unit_name(getattr(actor, "type", getattr(actor, "unit_type", "未知")) or "未知")
        faction = getattr(actor, "faction", "未知") or "未知"
        hp_percent = getattr(actor, "hp_percent", getattr(actor, "hppercent", -1))
        activity = getattr(actor, "activity", "") or ""
        order = getattr(actor, "order", "") or ""

        raw_pos = getattr(actor, "position", None)
        if isinstance(raw_pos, Location):
            pos = raw_pos
        elif isinstance(raw_pos, dict):
            pos = Location(raw_pos.get("x", 0), raw_pos.get("y", 0))
        else:
            x = getattr(raw_pos, "x", 0)
            y = getattr(raw_pos, "y", 0)
            pos = Location(x, y)

        return cls(
            id=str(actor_id) if actor_id is not None else "unknown",
            type=str(actor_type),
            faction=str(faction),
            pos=pos,
            hp_percent=int(hp_percent) if isinstance(hp_percent, (int, float)) else -1,
            activity=str(activity),
            order=str(order),
        )

