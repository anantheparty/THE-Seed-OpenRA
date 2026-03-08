from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

from ..utils import LogManager

logger = LogManager.get_logger()


@dataclass
class Blackboard:
    intel: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    scratchpad: str = ""
    game_basic_state: str = ""
    game_detail_state: str = ""
    plan: List[Dict[str, Any]] = field(default_factory=list)
    current_step: Dict[str, Any] = field(default_factory=dict)
    step_index: int = 0
    action_result: Dict[str, Any] = field(default_factory=dict)
    db_buffer: List[Dict[str, Any]] = field(default_factory=list)
    gameapi: Any = None
    midapi: Any = None
    gameapi_rules: str = ""
    runtime_globals: Dict[str, Any] = field(default_factory=dict)


class FSMState(str, Enum):
    OBSERVE = "observe"
    PLAN = "plan"
    ACTION_GEN = "action_gen"
    REVIEW = "review"
    COMMIT = "commit"
    NEED_USER = "need_user"
    STOP = "stop"
    DONE = "done"


@dataclass
class FSMContext:
    goal: str
    blackboard: Blackboard = field(default_factory=Blackboard)


class FSM:
    def __init__(self, *, ctx: FSMContext):
        self.ctx = ctx
        self.state: FSMState = FSMState.OBSERVE

    def transition(self, nxt: str) -> None:
        try:
            next_state = FSMState(str(nxt).lower())
        except ValueError:
            logger.warning("Unknown FSM state transition '%s'; falling back to stop", nxt)
            next_state = FSMState.STOP
        logger.info("FSM transition: %s -> %s", self.state, next_state)
        self.state = next_state

    def write_db(self, record: Dict[str, Any]) -> None:
        self.ctx.blackboard.db_buffer.append(record)
