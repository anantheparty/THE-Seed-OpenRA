from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ActionError(RuntimeError):
    pass


@dataclass
class ActionResult:
    ok: bool
    name: str
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class Action:
    """一次性执行的控制指令：执行完成即返回结果；不在 action 内做持续循环。"""

    NAME = "action"

    def run(self) -> ActionResult:
        try:
            result = self.execute()
            if not isinstance(result, ActionResult):
                raise ActionError(f"{self.NAME}.execute 必须返回 ActionResult")
            return result
        except Exception as exc:
            return ActionResult(ok=False, name=self.NAME, message="action 失败", error=str(exc))

    def execute(self) -> ActionResult:
        raise NotImplementedError


