"""Task Agent tool handlers — bridge between LLM tool calls and Kernel/WorldModel.

Each handler implements the async (name, args) -> result interface expected by
ToolExecutor. Handlers call Kernel and WorldModel methods to produce real side effects.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional, Protocol

from experts import query_planner as run_planner_query
from models import (
    Constraint,
    ConstraintEnforcement,
    ExpertConfig,
    Job,
    Task,
)
from models.configs import EXPERT_CONFIG_REGISTRY
from .tools import ToolExecutor


class KernelLike(Protocol):
    """Minimal Kernel interface used by tool handlers."""

    def start_job(self, task_id: str, expert_type: str, config: ExpertConfig) -> Job: ...
    def patch_job(self, job_id: str, params: dict[str, Any]) -> bool: ...
    def pause_job(self, job_id: str) -> bool: ...
    def resume_job(self, job_id: str) -> bool: ...
    def abort_job(self, job_id: str) -> bool: ...
    def complete_task(self, task_id: str, result: str, summary: str) -> bool: ...
    def cancel_tasks(self, filters: dict[str, Any]) -> int: ...


class ConstraintStoreLike(Protocol):
    """Minimal constraint store interface (typically WorldModel or Kernel)."""

    def set_constraint(self, constraint: Constraint) -> None: ...
    def remove_constraint(self, constraint_id: str) -> None: ...


class WorldModelLike(Protocol):
    """Minimal WorldModel interface used by tool handlers."""

    def query(self, query_type: str, params: Optional[dict[str, Any]] = None) -> Any: ...
    def set_constraint(self, constraint: Constraint) -> None: ...
    def remove_constraint(self, constraint_id: str) -> None: ...


class TaskToolHandlers:
    """Standalone tool handler set for one Task Agent.

    Wraps Kernel and WorldModel methods into the async handler interface
    expected by ToolExecutor. Can be registered into any ToolExecutor.
    """

    def __init__(
        self,
        task_id: str,
        kernel: KernelLike,
        world_model: WorldModelLike,
    ) -> None:
        self.task_id = task_id
        self.kernel = kernel
        self.world_model = world_model

    def register_all(self, executor: ToolExecutor) -> None:
        """Register all 11 tool handlers into the given ToolExecutor."""
        executor.register_all({
            "start_job": self.handle_start_job,
            "patch_job": self.handle_patch_job,
            "pause_job": self.handle_pause_job,
            "resume_job": self.handle_resume_job,
            "abort_job": self.handle_abort_job,
            "complete_task": self.handle_complete_task,
            "create_constraint": self.handle_create_constraint,
            "remove_constraint": self.handle_remove_constraint,
            "query_world": self.handle_query_world,
            "query_planner": self.handle_query_planner,
            "cancel_tasks": self.handle_cancel_tasks,
        })

    # --- Job lifecycle ---

    async def handle_start_job(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        expert_type = args["expert_type"]
        config_cls = EXPERT_CONFIG_REGISTRY[expert_type]
        config = config_cls(**args["config"])
        job = self.kernel.start_job(self.task_id, expert_type, config)
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "timestamp": job.timestamp,
        }

    async def handle_patch_job(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = self.kernel.patch_job(args["job_id"], args["params"])
        return {"ok": ok, "timestamp": time.time()}

    async def handle_pause_job(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = self.kernel.pause_job(args["job_id"])
        return {"ok": ok, "timestamp": time.time()}

    async def handle_resume_job(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = self.kernel.resume_job(args["job_id"])
        return {"ok": ok, "timestamp": time.time()}

    async def handle_abort_job(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = self.kernel.abort_job(args["job_id"])
        return {"ok": ok, "timestamp": time.time()}

    # --- Task completion ---

    async def handle_complete_task(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        ok = self.kernel.complete_task(self.task_id, args["result"], args["summary"])
        return {"ok": ok, "timestamp": time.time()}

    # --- Constraints ---

    async def handle_create_constraint(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        import uuid
        constraint_id = f"c_{uuid.uuid4().hex[:8]}"
        constraint = Constraint(
            constraint_id=constraint_id,
            kind=args["kind"],
            scope=args["scope"],
            params=dict(args.get("params", {})),
            enforcement=ConstraintEnforcement(args["enforcement"]),
        )
        self.world_model.set_constraint(constraint)
        return {"constraint_id": constraint_id, "timestamp": time.time()}

    async def handle_remove_constraint(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        constraint_id = args["constraint_id"]
        self.world_model.remove_constraint(constraint_id)
        return {"ok": True, "constraint_id": constraint_id, "timestamp": time.time()}

    # --- Queries ---

    async def handle_query_world(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        query_type = args["query_type"]
        # Map tool query types to WorldModel query types
        mapping = {
            "my_actors": "my_actors",
            "enemy_actors": "enemy_actors",
            "enemy_bases": "find_actors",
            "economy_status": "economy",
            "map_control": "map",
            "threat_assessment": "world_summary",
        }
        wm_query = mapping.get(query_type)
        if wm_query is None:
            return {"error": f"Unsupported query_world type: {query_type}", "timestamp": time.time()}

        params = dict(args.get("params") or {})
        if query_type == "enemy_bases":
            params.setdefault("owner", "enemy")
            params.setdefault("category", "building")

        data = self.world_model.query(wm_query, params)
        return {"data": data, "timestamp": time.time()}

    async def handle_query_planner(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        world_state = {
            "world_summary": self.world_model.query("world_summary"),
            "economy": self.world_model.query("economy"),
            "production_queues": self.world_model.query("production_queues"),
            "my_actors": self.world_model.query("my_actors"),
            "enemy_actors": self.world_model.query("enemy_actors"),
        }
        return {
            "proposal": run_planner_query(args["planner_type"], args.get("params"), world_state),
            "timestamp": time.time(),
        }

    # --- Bulk operations ---

    async def handle_cancel_tasks(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        count = self.kernel.cancel_tasks(args["filters"])
        return {"count": count, "timestamp": time.time()}
