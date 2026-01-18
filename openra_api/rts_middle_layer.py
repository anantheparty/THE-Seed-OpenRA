from __future__ import annotations

from typing import Any, Dict

from .game_api import GameAPI
from .intel.serializer import IntelSerializer
from .intel.service import IntelService
from .macro_actions import MacroActions


class RTSMiddleLayer:
    """RTS 中间层门面"""

    def __init__(self, api: GameAPI, cache_ttl: float = 0.25) -> None:
        self.api = api
        self.intel_service = IntelService(api, cache_ttl=cache_ttl)
        # MacroActions 是对 GameAPI 的薄封装；如需 jobs，可在外部注入 JobManager
        self.skills = MacroActions(api, intel=self.intel_service)

    def intel(self, force: bool = False, mode: str = "brief") -> Dict[str, Any]:
        """默认 brief（LLM 决策摘要），debug 输出完整结构"""
        model = self.intel_service.get_intel(force=force)
        if mode == "debug":
            return IntelSerializer.to_debug(model)
        return IntelSerializer.to_brief(model)

    def intel_debug(self, force: bool = False) -> Dict[str, Any]:
        return self.intel(force=force, mode="debug")

    def battle_details(self, force: bool = False) -> Dict[str, Any]:
        model = self.intel_service.get_intel(force=force)
        return model.battle

    def map_control_details(self, force: bool = False) -> Dict[str, Any]:
        model = self.intel_service.get_intel(force=force)
        return model.map_control

