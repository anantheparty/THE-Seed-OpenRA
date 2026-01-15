from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from ..game_api import GameAPI, GameAPIError
from ..models import Actor
from .base import Action, ActionResult


@dataclass
class DeployAction(Action):
    """展开/部署：对一组 actor 执行 deploy。"""

    api: GameAPI
    actors: Iterable[Actor]

    NAME = "deploy"

    def execute(self) -> ActionResult:
        actors_list: List[Actor] = list(self.actors or [])
        if not actors_list:
            return ActionResult(ok=False, name=self.NAME, message="没有可部署的单位")
        try:
            self.api.deploy_units(actors_list)
            return ActionResult(
                ok=True,
                name=self.NAME,
                message="已下达部署指令",
                data={"actor_ids": [a.actor_id for a in actors_list]},
            )
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="部署失败", error=str(exc))


