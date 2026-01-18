from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Union

from ..game_api import GameAPI, GameAPIError
from ..models import Actor
from .base import Action, ActionResult


@dataclass
class AttackAction(Action):
    """攻击：让一组单位攻击目标（对每个攻击者下达一次 attack）。"""

    api: GameAPI
    attackers: Iterable[Actor]
    target: Union[Actor, int]

    NAME = "attack"

    def execute(self) -> ActionResult:
        attackers_list: List[Actor] = list(self.attackers or [])
        if not attackers_list:
            return ActionResult(ok=False, name=self.NAME, message="没有攻击者")

        try:
            target_actor: Optional[Actor]
            if isinstance(self.target, Actor):
                target_actor = self.target
            else:
                target_actor = self.api.get_actor_by_id(int(self.target))

            if target_actor is None:
                return ActionResult(ok=False, name=self.NAME, message="目标不存在或不可见")

            ok_count = 0
            for a in attackers_list:
                if self.api.attack_target(a, target_actor):
                    ok_count += 1

            return ActionResult(
                ok=ok_count > 0,
                name=self.NAME,
                message="已下达攻击指令" if ok_count > 0 else "攻击指令未生效",
                data={
                    "target_actor_id": int(getattr(target_actor, "actor_id", getattr(target_actor, "id", -1))),
                    "attackers": [a.actor_id for a in attackers_list],
                    "ok_count": ok_count,
                },
            )
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="攻击失败", error=str(exc))


