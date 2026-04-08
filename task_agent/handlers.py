"""Task Agent tool handlers — bridge between LLM tool calls and Kernel/WorldModel.

Each handler implements the async (name, args) -> result interface expected by
ToolExecutor. Handlers call Kernel and WorldModel methods to produce real side effects.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable, Optional, Protocol

from experts import query_planner as run_planner_query
from models import (
    Constraint,
    ConstraintEnforcement,
    ExpertConfig,
    Job,
    Task,
    TaskMessage,
    TaskMessageType,
)
from task_agent.context import _SUBSCRIPTION_KEYS as _VALID_SUBSCRIPTION_KEYS
from models.configs import (
    CombatJobConfig,
    DeployJobConfig,
    EconomyJobConfig,
    EXPERT_CONFIG_REGISTRY,
    MovementJobConfig,
    ReconJobConfig,
)
from models.enums import EngagementMode, MoveMode
from .tools import ToolExecutor

_TYPE_MAP = {
    "info": TaskMessageType.TASK_INFO,
    "warning": TaskMessageType.TASK_WARNING,
    "question": TaskMessageType.TASK_QUESTION,
    "complete_report": TaskMessageType.TASK_COMPLETE_REPORT,
}


class KernelLike(Protocol):
    """Minimal Kernel interface used by tool handlers."""

    def start_job(self, task_id: str, expert_type: str, config: ExpertConfig) -> Job: ...
    def patch_job(self, job_id: str, params: dict[str, Any]) -> bool: ...
    def pause_job(self, job_id: str) -> bool: ...
    def resume_job(self, job_id: str) -> bool: ...
    def abort_job(self, job_id: str) -> bool: ...
    def complete_task(self, task_id: str, result: str, summary: str) -> bool: ...
    def cancel_tasks(self, filters: dict[str, Any]) -> int: ...
    def register_task_message(self, message: TaskMessage) -> bool: ...
    def jobs_for_task(self, task_id: str) -> list[Job]: ...
    def register_unit_request(self, task_id: str, category: str, count: int, urgency: str, hint: str) -> dict[str, Any]: ...
    def task_active_actor_ids(self, task_id: str) -> list[int]: ...
    def task_has_running_actor_job(self, task_id: str) -> bool: ...


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
        task: Task,
        kernel: KernelLike,
        world_model: WorldModelLike,
    ) -> None:
        self.task = task
        self.task_id = task.task_id
        self.kernel = kernel
        self.world_model = world_model

    def register_all(self, executor: ToolExecutor) -> None:
        """Register all tool handlers into the given ToolExecutor.

        Includes both LLM-exposed tools (from TOOL_DEFINITIONS) and the
        internal start_job handler used by bootstrap paths in agent.py.
        """
        executor.register_all({
            # Expert action tools (LLM-facing)
            "deploy_mcv": self.handle_deploy_mcv,
            "scout_map": self.handle_scout_map,
            "produce_units": self.handle_produce_units,
            "request_units": self.handle_request_units,
            "move_units": self.handle_move_units,
            "attack": self.handle_attack,
            # Job management
            "patch_job": self.handle_patch_job,
            "pause_job": self.handle_pause_job,
            "resume_job": self.handle_resume_job,
            "abort_job": self.handle_abort_job,
            # Task control
            "complete_task": self.handle_complete_task,
            # Constraints
            "create_constraint": self.handle_create_constraint,
            "remove_constraint": self.handle_remove_constraint,
            # Queries
            "query_world": self.handle_query_world,
            "query_planner": self.handle_query_planner,
            # Bulk ops / comms
            "cancel_tasks": self.handle_cancel_tasks,
            "send_task_message": self.handle_send_task_message,
            # Subscription management
            "update_subscriptions": self.handle_update_subscriptions,
            # Internal bootstrap tool — not in TOOL_DEFINITIONS, used by agent.py bootstrap paths
            "start_job": self.handle_start_job,
        })

    # --- Expert action tools (LLM-facing, one per Expert) ---

    async def handle_deploy_mcv(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        raw_pos = args.get("target_position")
        config = DeployJobConfig(
            actor_id=int(args["actor_id"]),
            target_position=tuple(raw_pos) if raw_pos else (0, 0),
        )
        job = self.kernel.start_job(self.task_id, "DeployExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_scout_map(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = list(args["actor_ids"]) if args.get("actor_ids") else self._default_actor_ids()
        config = ReconJobConfig(
            search_region=args["search_region"],
            target_type=args["target_type"],
            target_owner=args.get("target_owner", "enemy"),
            retreat_hp_pct=float(args.get("retreat_hp_pct", 0.3)),
            avoid_combat=bool(args.get("avoid_combat", True)),
            actor_ids=actor_ids,
            scout_count=int(args.get("scout_count", 1)),
        )
        job = self.kernel.start_job(self.task_id, "ReconExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_produce_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        config = EconomyJobConfig(
            unit_type=args["unit_type"],
            count=int(args["count"]),
            queue_type=args["queue_type"],
            repeat=bool(args.get("repeat", False)),
        )
        job = self.kernel.start_job(self.task_id, "EconomyExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_request_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Request units from Kernel — idle match or backend fulfillment.

        Returns the kernel's reservation/request contract payload so the LLM
        can see the inferred unit_type, queue_type, reservation_id, and any
        active bootstrap job tied to the request.
        """
        result = self.kernel.register_unit_request(
            task_id=self.task_id,
            category=args["category"],
            count=int(args["count"]),
            urgency=args.get("urgency", "medium"),
            hint=args.get("hint", ""),
        )
        return result

    async def handle_move_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = list(args["actor_ids"]) if args.get("actor_ids") else self._default_actor_ids()
        config = MovementJobConfig(
            target_position=tuple(args["target_position"]),
            move_mode=MoveMode(args.get("move_mode", "move")),
            arrival_radius=int(args.get("arrival_radius", 5)),
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "MovementExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_attack(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = list(args["actor_ids"]) if args.get("actor_ids") else self._default_actor_ids()
        config = CombatJobConfig(
            target_position=tuple(args["target_position"]),
            engagement_mode=EngagementMode(args.get("engagement_mode", "assault")),
            max_chase_distance=int(args.get("max_chase_distance", 20)),
            retreat_threshold=float(args.get("retreat_threshold", 0.3)),
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "CombatExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    def _default_actor_ids(self) -> Optional[list[int]]:
        """Reuse the task's current active unit group when safe."""
        if self.kernel.task_has_running_actor_job(self.task_id):
            return None
        actor_ids = self.kernel.task_active_actor_ids(self.task_id)
        return actor_ids or None

    # --- Internal bootstrap tool (not in TOOL_DEFINITIONS, called by agent.py bootstrap paths) ---

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
        result: dict[str, Any] = {"ok": ok, "timestamp": time.time()}
        # Warn if no Jobs succeeded — helps LLM reconsider partial/failed on its next turn.
        jobs = self.kernel.jobs_for_task(self.task_id)
        if jobs and not any(j.status.value == "succeeded" for j in jobs):
            job_statuses = ", ".join(f"{j.job_id}={j.status.value}" for j in jobs)
            result["job_status_warning"] = (
                f"注意：你管辖的 Job 均未成功完成（{job_statuses}）。"
                "如果任务目标已在世界中存在，可能是其他任务的成果，建议在 summary 中说明。"
            )
        return result

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

    # --- Subscription management ---

    async def handle_update_subscriptions(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        add = [k for k in (args.get("add") or []) if k in _VALID_SUBSCRIPTION_KEYS]
        remove = [k for k in (args.get("remove") or []) if k in _VALID_SUBSCRIPTION_KEYS]
        current = set(self.task.info_subscriptions)
        current.update(add)
        current.difference_update(remove)
        self.task.info_subscriptions = sorted(current)
        return {"subscriptions": self.task.info_subscriptions, "timestamp": time.time()}

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

    # --- Player communication ---

    async def handle_send_task_message(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        msg_type_str = args["type"]
        msg_type = _TYPE_MAP.get(msg_type_str)
        if msg_type is None:
            return {"ok": False, "error": f"Unknown type: {msg_type_str}", "timestamp": time.time()}

        options: Optional[list[str]] = args.get("options")
        timeout_s: Optional[float] = args.get("timeout_s")
        default_option: Optional[str] = args.get("default_option")

        if msg_type == TaskMessageType.TASK_QUESTION:
            if not options:
                return {"ok": False, "error": "type='question' requires options list", "timestamp": time.time()}
            if timeout_s is None:
                timeout_s = 60.0
            if default_option is None:
                default_option = options[0]
            elif default_option not in options:
                return {"ok": False, "error": "default_option must be one of options", "timestamp": time.time()}

        message = TaskMessage(
            message_id=f"tm_{uuid.uuid4().hex[:8]}",
            task_id=self.task_id,
            type=msg_type,
            content=args["content"],
            options=options,
            timeout_s=timeout_s,
            default_option=default_option,
        )
        ok = self.kernel.register_task_message(message)
        return {"ok": ok, "message_id": message.message_id, "timestamp": time.time()}
