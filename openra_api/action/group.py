from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from ..game_api import GameAPI, GameAPIError
from ..models import Actor
from .base import Action, ActionResult


@dataclass
class GroupAction(Action):
    """编组：把一组 actor 编入指定 group_id。"""

    api: GameAPI
    actors: Iterable[Actor]
    group_id: int

    NAME = "group"

    def execute(self) -> ActionResult:
        actors_list: List[Actor] = list(self.actors or [])
        if not actors_list:
            return ActionResult(ok=False, name=self.NAME, message="没有可编组的单位")
        try:
            self.api.form_group(actors_list, int(self.group_id))
            return ActionResult(
                ok=True,
                name=self.NAME,
                message="已编组",
                data={"group_id": int(self.group_id), "actor_ids": [a.actor_id for a in actors_list]},
            )
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="编组失败", error=str(exc))


