from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..game_api import GameAPI
from ..intel.service import IntelService
from ..models import Actor, Location


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"


@dataclass
class TickContext:
    api: GameAPI
    intel: IntelService
    now: float = field(default_factory=lambda: time.time())


@dataclass
class ActorAssignment:
    """Job-level -> Actor-level 的分配结果。"""

    kind: str  # "move" | "attack"
    target_actor_id: Optional[int] = None
    target_pos: Optional[Location] = None
    note: str = ""
    issued_at: float = 0.0
    cooldown_s: float = 1.2  # 防止每 tick 反复刷指令


class Job:
    """长期意图（Job）：每 tick 规划+下发 action，不在内部做 while 循环。"""

    NAME = "job"

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.status: JobStatus = JobStatus.pending
        self.last_error: Optional[str] = None
        self.last_tick_at: Optional[float] = None
        self.last_summary: str = ""
        # actor_id -> assignment
        self.assignments: Dict[int, ActorAssignment] = {}

    def tick(self, ctx: TickContext, actors: List[Actor]) -> None:
        self.last_tick_at = ctx.now
        if self.status in (JobStatus.completed, JobStatus.failed, JobStatus.paused):
            return
        if self.status == JobStatus.pending:
            self.status = JobStatus.running

        try:
            self._tick_impl(ctx, actors)
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self.status = JobStatus.failed
            self.last_summary = f"失败: {exc}"

    def status_dict(self) -> Dict[str, Any]:
        return {
            "id": self.job_id,
            "name": self.NAME,
            "status": str(self.status),
            "last_tick_at": self.last_tick_at,
            "last_summary": self.last_summary,
            "last_error": self.last_error,
            "assignments": {
                str(k): {
                    "kind": v.kind,
                    "target_actor_id": v.target_actor_id,
                    "target_pos": v.target_pos.to_dict() if v.target_pos else None,
                    "note": v.note,
                    "issued_at": v.issued_at,
                }
                for k, v in list(self.assignments.items())[:50]
            },
        }

    def on_unassigned(self, actor_id: int) -> None:
        """当某个 actor 被从该 job 解绑/死亡移除时，清理 job 内部状态。"""
        self.assignments.pop(int(actor_id), None)

    # ---- override points ----
    def _tick_impl(self, ctx: TickContext, actors: List[Actor]) -> None:
        raise NotImplementedError


