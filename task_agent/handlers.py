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
    OccupyJobConfig,
    RallyJobConfig,
    RepairJobConfig,
    ReconJobConfig,
    StopJobConfig,
)
from models.enums import EngagementMode, MoveMode
from .tools import ToolExecutor
from .workflows import (
    PRODUCE_UNITS_THEN_ATTACK,
    PRODUCE_UNITS_THEN_RECON,
    classify_managed_workflow,
    workflow_phase,
)

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
    def register_unit_request(
        self,
        task_id: str,
        category: str,
        count: int,
        urgency: str,
        hint: str,
        *,
        blocking: bool = True,
        min_start_package: int = 1,
    ) -> dict[str, Any]: ...
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
        Registration is task-aware so capability-only tools are not even
        available to ordinary managed tasks.
        """
        handlers = {
            # Expert action tools (LLM-facing)
            "deploy_mcv": self.handle_deploy_mcv,
            "scout_map": self.handle_scout_map,
            "move_units": self.handle_move_units,
            "move_units_by_path": self.handle_move_units_by_path,
            "stop_units": self.handle_stop_units,
            "repair_units": self.handle_repair_units,
            "occupy_target": self.handle_occupy_target,
            "attack": self.handle_attack,
            "attack_actor": self.handle_attack_actor,
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
        }
        if getattr(self.task, "is_capability", False):
            handlers["produce_units"] = self.handle_produce_units
            handlers["set_rally_point"] = self.handle_set_rally_point
        else:
            handlers["request_units"] = self.handle_request_units
        executor.register_all(handlers)

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
        explicit_actor_ids = list(args.get("actor_ids") or [])
        workflow_actor_ids = explicit_actor_ids or self.kernel.task_active_actor_ids(self.task_id) or None
        self._enforce_workflow_for_recon(workflow_actor_ids)
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="scout_map")
        config = ReconJobConfig(
            search_region=args["search_region"],
            target_type=args["target_type"],
            target_owner=args.get("target_owner", "enemy"),
            retreat_hp_pct=float(args.get("retreat_hp_pct", 0.3)),
            avoid_combat=bool(args.get("avoid_combat", True)),
            wait_for_full_group=bool(args.get("wait_for_full_group", False)),
            min_ready_count=int(args.get("min_ready_count", 0)),
            actor_ids=actor_ids,
            scout_count=int(args.get("scout_count", 1)),
        )
        job = self.kernel.start_job(self.task_id, "ReconExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_produce_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.task, "is_capability", False):
            raise ValueError("produce_units is capability-only")
        unit_type = str(args["unit_type"])
        queue_type = str(args["queue_type"])
        self._guard_capability_produce_units(unit_type=unit_type, queue_type=queue_type)
        config = EconomyJobConfig(
            unit_type=unit_type,
            count=int(args["count"]),
            queue_type=queue_type,
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
        if getattr(self.task, "is_capability", False):
            raise ValueError("request_units is unavailable for capability tasks")
        result = self.kernel.register_unit_request(
            task_id=self.task_id,
            category=args["category"],
            count=int(args["count"]),
            urgency=args.get("urgency", "medium"),
            hint=args.get("hint", ""),
            blocking=bool(args.get("blocking", True)),
            min_start_package=int(args.get("min_start_package", 1)),
        )
        return result

    async def handle_move_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="move_units")
        config = MovementJobConfig(
            target_position=tuple(args["target_position"]),
            move_mode=MoveMode(args.get("move_mode", "move")),
            arrival_radius=int(args.get("arrival_radius", 5)),
            wait_for_full_group=bool(
                args.get(
                    "wait_for_full_group",
                    MoveMode(args.get("move_mode", "move")) != MoveMode.RETREAT,
                )
            ),
            min_ready_count=int(args.get("min_ready_count", 0)),
            min_complete_count=int(args.get("min_complete_count", 0)),
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "MovementExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_move_units_by_path(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = list(args.get("path") or [])
        if not raw_path:
            raise ValueError("move_units_by_path requires a non-empty path")
        normalized_path = [(int(point[0]), int(point[1])) for point in raw_path]
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="move_units_by_path")
        config = MovementJobConfig(
            target_position=normalized_path[-1],
            move_mode=MoveMode(args.get("move_mode", "move")),
            arrival_radius=int(args.get("arrival_radius", 5)),
            path=normalized_path,
            wait_for_full_group=bool(
                args.get(
                    "wait_for_full_group",
                    MoveMode(args.get("move_mode", "move")) != MoveMode.RETREAT,
                )
            ),
            min_ready_count=int(args.get("min_ready_count", 0)),
            min_complete_count=int(args.get("min_complete_count", 0)),
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "MovementExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_stop_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="stop_units")
        config = StopJobConfig(
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "StopExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_repair_units(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="repair_units")
        config = RepairJobConfig(
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "RepairExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_occupy_target(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        target_actor_id = int(args["target_actor_id"])
        target_result = self.world_model.query("actor_by_id", {"actor_id": target_actor_id})
        target_actor = target_result.get("actor") if isinstance(target_result, dict) else None
        if not target_actor:
            raise ValueError("occupy_target requires a visible/known target actor")
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="occupy_target")
        config = OccupyJobConfig(actor_ids=actor_ids, target_actor_id=target_actor_id)
        job = self.kernel.start_job(self.task_id, "OccupyExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_set_rally_point(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.task, "is_capability", False):
            raise ValueError("set_rally_point is capability-only")
        actor_ids = list(args.get("actor_ids") or [])
        if not actor_ids:
            raise ValueError("set_rally_point requires explicit production-building actor_ids")
        raw_pos = args.get("target_position")
        if not raw_pos or len(raw_pos) != 2:
            raise ValueError("set_rally_point requires target_position=[x, y]")
        config = RallyJobConfig(
            actor_ids=actor_ids,
            target_position=(int(raw_pos[0]), int(raw_pos[1])),
        )
        job = self.kernel.start_job(self.task_id, "RallyExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_attack(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        explicit_actor_ids = list(args.get("actor_ids") or [])
        workflow_actor_ids = explicit_actor_ids or self.kernel.task_active_actor_ids(self.task_id) or None
        self._enforce_workflow_for_attack(workflow_actor_ids)
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="attack")
        config = CombatJobConfig(
            target_position=tuple(args["target_position"]),
            engagement_mode=EngagementMode(args.get("engagement_mode", "assault")),
            max_chase_distance=int(args.get("max_chase_distance", 20)),
            retreat_threshold=float(args.get("retreat_threshold", 0.3)),
            wait_for_full_group=bool(args.get("wait_for_full_group", False)),
            min_ready_count=int(args.get("min_ready_count", 0)),
            actor_ids=actor_ids,
            unit_count=int(args.get("unit_count", 0)),
        )
        job = self.kernel.start_job(self.task_id, "CombatExpert", config)
        return {"job_id": job.job_id, "status": job.status.value, "timestamp": job.timestamp}

    async def handle_attack_actor(self, _name: str, args: dict[str, Any]) -> dict[str, Any]:
        target_actor_id = int(args["target_actor_id"])
        target_result = self.world_model.query("actor_by_id", {"actor_id": target_actor_id})
        target_actor = target_result.get("actor") if isinstance(target_result, dict) else None
        target_position = target_actor.get("position") if isinstance(target_actor, dict) else None
        if not target_position or len(target_position) != 2:
            raise ValueError("attack_actor requires a visible/known target actor with position")

        explicit_actor_ids = list(args.get("actor_ids") or [])
        workflow_actor_ids = explicit_actor_ids or self.kernel.task_active_actor_ids(self.task_id) or None
        self._enforce_workflow_for_attack(workflow_actor_ids)
        actor_ids = self._resolve_unit_actor_ids(args, tool_name="attack_actor")
        config = CombatJobConfig(
            target_position=(int(target_position[0]), int(target_position[1])),
            engagement_mode=EngagementMode(args.get("engagement_mode", "assault")),
            max_chase_distance=int(args.get("max_chase_distance", 20)),
            retreat_threshold=float(args.get("retreat_threshold", 0.3)),
            target_actor_id=target_actor_id,
            wait_for_full_group=bool(args.get("wait_for_full_group", False)),
            min_ready_count=int(args.get("min_ready_count", 0)),
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

    def _guard_capability_produce_units(self, *, unit_type: str, queue_type: str) -> None:
        """Reject duplicate or phase-invalid capability production before starting a new EconomyJob."""
        for job in self.kernel.jobs_for_task(self.task_id):
            if getattr(job, "expert_type", "") != "EconomyExpert":
                continue
            status_value = str(getattr(getattr(job, "status", ""), "value", getattr(job, "status", "")) or "").lower()
            if status_value not in {"running", "waiting"}:
                continue
            config = getattr(job, "config", None)
            if str(getattr(config, "unit_type", "") or "") != unit_type:
                continue
            if str(getattr(config, "queue_type", "") or "") != queue_type:
                continue
            raise ValueError(
                f"produce_units duplicate blocked: existing_job_id={job.job_id} unit_type={unit_type} queue_type={queue_type}"
            )

        production_queues = self.world_model.query("production_queues")
        queue_state = production_queues.get(queue_type) if isinstance(production_queues, dict) else None
        for item in self._queue_items(queue_state):
            item_unit_type = str(item.get("name") or item.get("unit_type") or "").strip()
            if item_unit_type != unit_type:
                continue
            status_value = str(item.get("status") or "").strip().lower()
            is_ready = bool(item.get("done")) or status_value in {"done", "completed", "ready"}
            if is_ready:
                raise ValueError(
                    f"produce_units blocked: ready_queue_item queue_type={queue_type} unit_type={unit_type} awaiting placement"
                )
            raise ValueError(
                f"produce_units duplicate blocked: production_queue queue_type={queue_type} unit_type={unit_type}"
            )

        capability_status = self.world_model.query("capability_status")
        if isinstance(capability_status, dict):
            status_task_id = str(capability_status.get("task_id") or "").strip()
            if status_task_id and status_task_id != self.task_id:
                return
            blocker = str(capability_status.get("blocker") or "").strip()
            if blocker in {"world_sync_stale", "queue_blocked"}:
                raise ValueError(
                    f"produce_units blocked: capability blocker={blocker} unit_type={unit_type} queue_type={queue_type}"
                )

    def _queue_items(self, queue_state: Any) -> list[dict[str, Any]]:
        if isinstance(queue_state, dict):
            items = queue_state.get("items")
            if isinstance(items, list):
                return [dict(item) for item in items if isinstance(item, dict)]
        if isinstance(queue_state, list):
            return [dict(item) for item in queue_state if isinstance(item, dict)]
        return []

    def _resolve_unit_actor_ids(self, args: dict[str, Any], *, tool_name: str) -> Optional[list[int]]:
        """Resolve actor_ids for unit-consuming tools without silently grabbing idle units."""
        explicit_actor_ids = list(args.get("actor_ids") or [])
        if explicit_actor_ids:
            return explicit_actor_ids

        actor_ids = self._default_actor_ids()
        if actor_ids or getattr(self.task, "is_capability", False):
            return actor_ids

        active_actor_ids = self.kernel.task_active_actor_ids(self.task_id)
        if active_actor_ids and self.kernel.task_has_running_actor_job(self.task_id):
            raise ValueError(
                f"{tool_name} requires explicit actor_ids while another actor-based job is running"
            )
        raise ValueError(
            f"{tool_name} requires explicit actor_ids, task-owned units, or request_units before use"
        )

    def _enforce_workflow_for_recon(self, actor_ids: Optional[list[int]]) -> None:
        """Prevent bounded ordinary workflows from skipping the unit-acquisition phase."""
        if getattr(self.task, "is_capability", False):
            return
        workflow = classify_managed_workflow(getattr(self.task, "raw_text", ""))
        if workflow != PRODUCE_UNITS_THEN_RECON:
            return
        current_actor_ids = list(actor_ids or [])
        phase = workflow_phase(
            workflow,
            active_actor_ids=current_actor_ids,
            jobs=[
                {"expert_type": job.expert_type}
                for job in self.kernel.jobs_for_task(self.task_id)
            ],
        )
        if phase in {"request_units_first", "waiting_for_units"} and not current_actor_ids:
            raise ValueError(
                "workflow produce_units_then_recon requires request_units/assigned actors before scout_map"
            )

    def _enforce_workflow_for_attack(self, actor_ids: Optional[list[int]]) -> None:
        """Prevent bounded ordinary attack workflows from skipping the unit-acquisition phase."""
        if getattr(self.task, "is_capability", False):
            return
        workflow = classify_managed_workflow(getattr(self.task, "raw_text", ""))
        if workflow != PRODUCE_UNITS_THEN_ATTACK:
            return
        current_actor_ids = list(actor_ids or [])
        phase = workflow_phase(
            workflow,
            active_actor_ids=current_actor_ids,
            jobs=[
                {"expert_type": job.expert_type}
                for job in self.kernel.jobs_for_task(self.task_id)
            ],
        )
        if phase in {"request_units_first", "waiting_for_units"} and not current_actor_ids:
            raise ValueError(
                "workflow produce_units_then_attack requires request_units/assigned actors before attack"
            )

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
