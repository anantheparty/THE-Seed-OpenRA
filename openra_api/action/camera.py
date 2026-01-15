from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..game_api import GameAPI, GameAPIError
from ..models import Actor, Location
from .base import Action, ActionResult


@dataclass
class CameraMoveAction(Action):
    """移动摄像机：按 actor / location / direction 移动。"""

    api: GameAPI
    actor: Optional[Actor] = None
    location: Optional[Location] = None
    direction: Optional[str] = None
    distance: Optional[int] = None

    NAME = "camera_move"

    def execute(self) -> ActionResult:
        try:
            if self.actor is not None:
                self.api.move_camera_to(self.actor)
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已移动摄像机到单位",
                    data={"actor_id": getattr(self.actor, "actor_id", getattr(self.actor, "id", None))},
                )

            if self.location is not None:
                self.api.move_camera_by_location(self.location)
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已移动摄像机到位置",
                    data={"location": self.location.to_dict()},
                )

            if self.direction and self.distance is not None:
                self.api.move_camera_by_direction(self.direction, int(self.distance))
                return ActionResult(
                    ok=True,
                    name=self.NAME,
                    message="已按方向移动摄像机",
                    data={"direction": self.direction, "distance": int(self.distance)},
                )

            return ActionResult(ok=False, name=self.NAME, message="缺少 actor/location/direction 参数")
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="移动摄像机失败", error=str(exc))


