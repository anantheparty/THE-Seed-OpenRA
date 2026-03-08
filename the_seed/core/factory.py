from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils import LogManager

logger = LogManager.get_logger()


@dataclass
class NodeOutput:
    next_state: str


class _FallbackNode:
    def run(self, fsm: Any) -> NodeOutput:
        logger.warning("Legacy NodeFactory fallback node used; stopping FSM because the-seed runtime has been removed.")
        _ = fsm
        return NodeOutput(next_state="stop")


class NodeFactory:
    def __init__(self, cfg: Any = None):
        self.cfg = cfg
        self._fallback = _FallbackNode()

    def get_node(self, node_key: Any) -> _FallbackNode:
        _ = node_key
        return self._fallback

    def create_node(self, node_key: Any) -> _FallbackNode:
        return self.get_node(node_key)
