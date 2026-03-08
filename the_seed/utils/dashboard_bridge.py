from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger("AI_Assistant.dashboard")


class DashboardBridge:
    _instance: Optional["DashboardBridge"] = None

    def __new__(cls) -> "DashboardBridge":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.running = False
        self.command_handler: Optional[Callable[..., Any]] = None
        self.events: List[Dict[str, Any]] = []

    def start(self, host: str = "127.0.0.1", port: int = 8080, command_handler: Callable[..., Any] = None, **_: Any) -> None:
        self.running = True
        self.command_handler = command_handler
        logger.info("DashboardBridge shim started on ws://%s:%s", host, port)

    def broadcast(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append({"type": event_type, "payload": payload})

    def send_log(self, level: str, message: str) -> None:
        self.broadcast("log", {"level": level, "message": message})

    def update_fsm_state(self, fsm: Any) -> None:
        payload = {
            "fsm_state": str(getattr(fsm, "state", "")),
            "goal": getattr(getattr(fsm, "ctx", None), "goal", ""),
        }
        self.broadcast("fsm_state", payload)

    def track_llm_call(self, tokens: int = 0) -> None:
        self.broadcast("llm_call", {"tokens": tokens})

    def track_action(self, action_name: str, success: bool = True) -> None:
        self.broadcast("action", {"action_name": action_name, "success": success})


def hook_fsm_transition(_fsm_cls: Any) -> None:
    return None
