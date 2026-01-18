from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..game_api import GameAPI, GameAPIError
from .base import Action, ActionResult


@dataclass
class ProduceAction(Action):
    """生产：发起生产请求，返回 waitId（不在 action 内轮询等待）。"""

    api: GameAPI
    unit_type: str
    quantity: int = 1
    auto_place_building: bool = False

    NAME = "produce"

    def execute(self) -> ActionResult:
        try:
            wait_id: Optional[int] = self.api.produce(
                self.unit_type, int(self.quantity), auto_place_building=bool(self.auto_place_building)
            )
            ok = wait_id is not None
            msg = "已发起生产" if ok else "生产请求失败"
            return ActionResult(
                ok=ok,
                name=self.NAME,
                message=msg,
                data={
                    "unit_type": self.unit_type,
                    "quantity": int(self.quantity),
                    "auto_place_building": bool(self.auto_place_building),
                    "wait_id": wait_id,
                },
            )
        except GameAPIError as exc:
            return ActionResult(ok=False, name=self.NAME, message="生产失败", error=str(exc))


