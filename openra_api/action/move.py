from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..game_api import GameAPI, GameAPIError
from ..models import Actor, Location
from .base import Action, ActionResult


@dataclass
class MoveAction(Action):
    """移动：支持按位置/路径/方向移动。"""

    api: GameAPI
    actors: Iterable[Actor]
    location: Optional[Location] = None
    path: Optional[List[Location]] = None
    direction: Optional[str] = None
    distance: Optional[int] = None
    attack_move: bool = False

    NAME = "move"

    def execute(self) -> ActionResult:
        actors_list: List[Actor] = list(self.actors or [])
        if not actors_list:
            return ActionResult(ok=False, name=self.NAME, message="没有可移动的单位")

        try:
            if self.path:
                self.api.move_units_by_path(actors_list, self.path, attack_move=bool(self.attack_move))
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已下达路径移动",
                    data={
                        "actor_ids": [a.actor_id for a in actors_list],
                        "path": [p.to_dict() for p in self.path],
                        "attack_move": bool(self.attack_move),
                    },
                )

            if self.location:
                self.api.move_units_by_location(actors_list, self.location, attack_move=bool(self.attack_move))
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已下达位置移动",
                    data={
                        "actor_ids": [a.actor_id for a in actors_list],
                        "location": self.location.to_dict(),
                        "attack_move": bool(self.attack_move),
                    },
                )

            if self.direction and self.distance is not None:
                self.api.move_units_by_direction(actors_list, self.direction, int(self.distance))
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已下达方向移动",
                    data={
                        "actor_ids": [a.actor_id for a in actors_list],
                        "direction": self.direction,
                        "distance": int(self.distance),
                    },
                )

            return ActionResult(ok=False, name=self.NAME, message="缺少 location/path/direction 参数")
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="移动失败", error=str(exc))


