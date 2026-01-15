from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..game_api import GameAPI, GameAPIError
from ..models import Location
from .base import Action, ActionResult


@dataclass
class BuildAction(Action):
    """建造：放置建造队列顶端已就绪的建筑（或防御）。"""

    api: GameAPI
    queue_type: str = "Building"  # Building / Defense
    location: Optional[Location] = None

    NAME = "build"

    def execute(self) -> ActionResult:
        try:
            self.api.place_building(self.queue_type, self.location)
            return ActionResult(
                ok=True,
                name=self.NAME,
                message="已尝试放置建筑",
                data={
                    "queue_type": self.queue_type,
                    "location": self.location.to_dict() if isinstance(self.location, Location) else None,
                },
            )
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="放置建筑失败", error=str(exc))


