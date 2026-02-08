from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..game_api import GameAPI
from ..intel.service import IntelService
from ..models import Actor
from .base import Job, TickContext


class JobManager:
    def __init__(self, api: GameAPI, intel: Optional[IntelService] = None) -> None:
        self.api = api
        self.intel = intel or IntelService(api)
        self.jobs: List[Job] = []
        self._jobs_by_id: Dict[str, Job] = {}

        # actor_id -> job_id（一个 actor 同时最多处于一个 job）
        self.actor_job: Dict[int, str] = {}
        # actor_id -> Actor 实例（用于持续 update / 下发命令）
        self.actors: Dict[int, Actor] = {}
        # actor_id -> 最近一次 update 结果
        self.actor_alive: Dict[int, bool] = {}

    def add_job(self, job: Job) -> None:
        self.jobs.append(job)
        self._jobs_by_id[job.job_id] = job

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs_by_id.get(job_id)

    def get_actor_ids_for_job(self, job_id: str, *, alive_only: bool = True) -> List[int]:
        """返回当前绑定到某个 job 的 actor_id 列表。"""
        if job_id not in self._jobs_by_id:
            return []
        ids: List[int] = []
        for aid, jid in self.actor_job.items():
            if jid != job_id:
                continue
            if alive_only and not bool(self.actor_alive.get(aid, True)):
                continue
            ids.append(int(aid))
        ids.sort()
        return ids

    def get_actors_for_job(self, job_id: str, *, alive_only: bool = True) -> List[Actor]:
        """返回当前绑定到某个 job 的 actor 实例列表。"""
        actors: List[Actor] = []
        for aid in self.get_actor_ids_for_job(job_id, alive_only=alive_only):
            actor = self.actors.get(aid)
            if actor is not None:
                actors.append(actor)
        return actors

    def assign_actor_to_job(self, actor: Actor, job_id: str) -> None:
        """显式分配：把一个 actor 绑定到某个 job（会自动从旧 job 解绑）。"""
        if job_id not in self._jobs_by_id:
            raise ValueError(f"未知 job_id: {job_id}")
        actor_id = int(getattr(actor, "actor_id", getattr(actor, "id", -1)))
        if actor_id < 0:
            raise ValueError("actor 缺少有效 actor_id")

        # 若已在其他 job 中，先解绑
        old = self.actor_job.get(actor_id)
        if old and old != job_id:
            old_job = self._jobs_by_id.get(old)
            if old_job:
                old_job.on_unassigned(actor_id)
        self.actor_job[actor_id] = job_id
        self.actors[actor_id] = actor
        self.actor_alive[actor_id] = True

    def assign_actor_id_to_job(self, actor_id: int, job_id: str) -> bool:
        """按 id 分配（会尝试 resolve actor）。返回是否成功。"""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"未知 job_id: {job_id}")
        a = self.api.get_actor_by_id(int(actor_id))
        if a is None:
            return False
        self.assign_actor_to_job(a, job_id)
        return True

    def unassign_actor(self, actor_id: int) -> None:
        """显式解绑 actor（不会删除 job）。"""
        actor_id = int(actor_id)
        old = self.actor_job.pop(actor_id, None)
        if old:
            job = self._jobs_by_id.get(old)
            if job:
                job.on_unassigned(actor_id)
        self.actors.pop(actor_id, None)
        self.actor_alive.pop(actor_id, None)

    def tick_jobs(self) -> None:
        ctx = TickContext(api=self.api, intel=self.intel, now=time.time())
        # 1) 更新所有已分配 actor 的存活状态
        dead: List[int] = []
        for aid, actor in list(self.actors.items()):
            try:
                alive = bool(self.api.update_actor(actor))
            except Exception:
                alive = False
            self.actor_alive[aid] = alive
            if not alive:
                dead.append(aid)

        for aid in dead:
            self.unassign_actor(aid)

        # 2) 按 job 分组，把 actor 列表传给 job.tick
        job_to_actors: Dict[str, List[Actor]] = {}
        for aid, jid in self.actor_job.items():
            actor = self.actors.get(aid)
            if actor is None:
                continue
            job_to_actors.setdefault(jid, []).append(actor)

        for job in list(self.jobs):
            actors = job_to_actors.get(job.job_id, [])
            job.tick(ctx, actors)

    def jobs_status(self) -> Dict[str, Any]:
        return {
            "t": time.time(),
            "jobs": [j.status_dict() for j in self.jobs],
            "actor_job": {str(k): v for k, v in sorted(self.actor_job.items())},
            "actor_alive": {str(k): bool(v) for k, v in sorted(self.actor_alive.items())},
        }

