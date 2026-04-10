"""Kernel v1: deterministic Task / Job lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Awaitable, Callable, Optional, Protocol

from benchmark import span as bm_span
from experts.base import BaseJob, ExecutionExpert
from experts.planners import query_planner as run_planner_query
from llm import LLMProvider
from logging_system import get_logger
from models import (
    CombatJobConfig,
    Constraint,
    ConstraintEnforcement,
    EconomyJobConfig,
    EngagementMode,
    Event,
    EventType,
    ExpertConfig,
    ExpertSignal,
    Job,
    JobStatus,
    PlayerResponse,
    ReservationStatus,
    ResourceKind,
    ResourceNeed,
    SignalKind,
    Task,
    TaskKind,
    TaskMessage,
    TaskMessageType,
    TaskStatus,
    UnitReservation,
    UnitRequest,
    validate_job_config,
)
from models.configs import EXPERT_CONFIG_REGISTRY
from runtime_views import CapabilityStatusSnapshot
from task_agent import AgentConfig, TaskAgent, TaskToolHandlers, ToolExecutor, WorldSummary
from world_model import WorldModel

slog = get_logger("kernel")

# --- Unit request hint → unit_type mapping ---

_HINT_TO_UNIT: dict[str, tuple[str, str]] = {
    # (unit_type, queue_type)
    "重坦": ("3tnk", "Vehicle"), "重型坦克": ("3tnk", "Vehicle"), "坦克": ("3tnk", "Vehicle"),
    "天启": ("4tnk", "Vehicle"), "天启坦克": ("4tnk", "Vehicle"),
    "磁暴": ("ttnk", "Vehicle"), "磁暴坦克": ("ttnk", "Vehicle"),
    "火箭车": ("v2rl", "Vehicle"), "V2": ("v2rl", "Vehicle"), "v2rl": ("v2rl", "Vehicle"),
    "矿车": ("harv", "Vehicle"), "采矿车": ("harv", "Vehicle"),
    "地雷": ("mnly", "Vehicle"),
    "步兵": ("e1", "Infantry"), "步枪兵": ("e1", "Infantry"),
    "火箭兵": ("e3", "Infantry"), "火箭步兵": ("e3", "Infantry"),
    "工程师": ("e6", "Infantry"),
    "狗": ("dog", "Infantry"), "军犬": ("dog", "Infantry"),
    "电厂": ("powr", "Building"), "发电厂": ("powr", "Building"),
    "兵营": ("barr", "Building"),
    "矿场": ("proc", "Building"), "精炼厂": ("proc", "Building"),
    "战车工厂": ("weap", "Building"), "坦克厂": ("weap", "Building"),
    "雷达": ("dome", "Building"), "雷达站": ("dome", "Building"),
}

_UNIT_TYPE_TO_QUEUE: dict[str, str] = {}
for _unit_type, _queue_type in _HINT_TO_UNIT.values():
    _UNIT_TYPE_TO_QUEUE.setdefault(_unit_type, _queue_type)

_CATEGORY_DEFAULTS: dict[str, tuple[str, str]] = {
    "infantry": ("e1", "Infantry"),
    "vehicle": ("3tnk", "Vehicle"),
    "building": ("powr", "Building"),
}

_CATEGORY_TO_ACTOR_CATEGORY: dict[str, str] = {
    "infantry": "infantry",
    "vehicle": "vehicle",
    "building": "building",
}

_UNIT_TO_QUEUE_TYPE: dict[str, str] = {
    # buildings
    "powr": "Building", "apwr": "Building", "proc": "Building", "barr": "Building",
    "weap": "Building", "dome": "Building", "fix": "Building", "kenn": "Building",
    "silo": "Building",
    # infantry
    "e1": "Infantry", "e2": "Infantry", "e3": "Infantry", "e6": "Infantry", "dog": "Infantry",
    # vehicles
    "ftrk": "Vehicle", "v2rl": "Vehicle", "3tnk": "Vehicle", "4tnk": "Vehicle",
    "harv": "Vehicle", "mcv": "Vehicle", "mnly": "Vehicle", "ttnk": "Vehicle",
    "jeep": "Vehicle", "2tnk": "Vehicle",
    # aircraft
    "mig": "Aircraft", "yak": "Aircraft",
}

_URGENCY_WEIGHT: dict[str, int] = {
    "critical": 4, "high": 3, "medium": 2, "low": 1,
}


def _now() -> float:
    return time.time()


def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _infer_queue_type(unit_type: Optional[str]) -> Optional[str]:
    if not unit_type:
        return None
    return _UNIT_TO_QUEUE_TYPE.get(unit_type.lower())


def _queue_type_for_unit_type(unit_type: Optional[str]) -> str:
    if not unit_type:
        return ""
    return _UNIT_TYPE_TO_QUEUE.get(unit_type, "")


class TaskAgentLike(Protocol):
    task: Task
    @property
    def is_suspended(self) -> bool: ...

    async def run(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def push_signal(self, signal: ExpertSignal) -> None:
        ...

    def push_event(self, event: Event) -> None:
        ...

    def suspend(self) -> None:
        ...

    def resume_with_event(self, event: Event) -> None:
        ...


TaskAgentFactory = Callable[[Task, ToolExecutor, Callable[[str], list[Job]], Callable[[], WorldSummary]], TaskAgentLike]


@dataclass(slots=True)
class KernelConfig:
    auto_start_agents: bool = True
    default_agent_config: AgentConfig = field(default_factory=AgentConfig)


class _ManagedJob:
    """Fallback runtime job used before real Experts are attached."""

    def __init__(
        self,
        job_id: str,
        task_id: str,
        expert_type: str,
        config: ExpertConfig,
        signal_callback: Callable[[ExpertSignal], None],
    ) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self._expert_type = expert_type
        self.config = config
        self.resources: list[str] = []
        self.status = JobStatus.RUNNING
        self._paused = False
        self._signal_callback = signal_callback
        self._timestamp = _now()

    @property
    def expert_type(self) -> str:
        return self._expert_type

    @property
    def is_paused(self) -> bool:
        return self._paused

    def patch(self, params: dict[str, Any]) -> None:
        for key, value in params.items():
            if hasattr(self.config, key):
                object.__setattr__(self.config, key, value)
        self._timestamp = _now()

    def pause(self) -> None:
        self._paused = True
        self.status = JobStatus.WAITING
        self._timestamp = _now()

    def resume(self) -> None:
        self._paused = False
        self.status = JobStatus.RUNNING
        self._timestamp = _now()

    def abort(self) -> None:
        self.status = JobStatus.ABORTED
        self._timestamp = _now()

    def on_resource_granted(self, resources: list[str]) -> None:
        self.resources.extend(resources)
        if self.status == JobStatus.WAITING and not self._paused:
            self.status = JobStatus.RUNNING
        self._timestamp = _now()

    def on_resource_revoked(self, resources: list[str]) -> None:
        for resource in resources:
            if resource in self.resources:
                self.resources.remove(resource)
        self._timestamp = _now()

    def to_model(self) -> Job:
        return Job(
            job_id=self.job_id,
            task_id=self.task_id,
            expert_type=self.expert_type,
            config=self.config,
            resources=list(self.resources),
            status=self.status,
            timestamp=self._timestamp,
        )


@dataclass(slots=True)
class _TaskRuntime:
    task: Task
    agent: TaskAgentLike
    tool_executor: ToolExecutor
    runner: Optional[asyncio.Task[Any]] = None


@dataclass(slots=True)
class _PendingQuestion:
    message: TaskMessage
    deadline_at: float
    default_option: str


@dataclass(slots=True)
class _AutoResponseRule:
    rule_id: str
    event_type: EventType
    handler: Callable[[Event], None]


class Kernel:
    """Deterministic orchestration layer for Tasks and Jobs."""

    def __init__(
        self,
        *,
        world_model: WorldModel,
        llm: Optional[LLMProvider] = None,
        expert_registry: Optional[dict[str, ExecutionExpert]] = None,
        task_agent_factory: Optional[TaskAgentFactory] = None,
        config: Optional[KernelConfig] = None,
    ) -> None:
        self.world_model = world_model
        self.llm = llm
        self.expert_registry = dict(expert_registry or {})
        self.task_agent_factory = task_agent_factory or self._default_task_agent_factory
        self.config = config or KernelConfig()

        self._task_seq: int = 0  # monotone counter for human-readable task labels
        self.tasks: dict[str, Task] = {}
        self._task_runtimes: dict[str, _TaskRuntime] = {}
        self._jobs: dict[str, BaseJob | _ManagedJob] = {}
        self._constraints: dict[str, Constraint] = {}
        self._resource_needs: dict[str, list[ResourceNeed]] = {}
        self._resource_loss_notified: set[str] = set()
        self.player_notifications: list[dict[str, Any]] = []
        self.task_messages: list[TaskMessage] = []
        self._pending_questions: dict[str, _PendingQuestion] = {}
        self._timed_out_questions: set[str] = set()
        self._closed_questions: set[str] = set()
        self._delivered_player_responses: dict[str, list[PlayerResponse]] = {}
        self._auto_response_rules: dict[EventType, list[_AutoResponseRule]] = {}
        self._direct_managed_tasks: set[str] = set()  # tasks with skip_agent=True (NLU direct)
        self._capability_task_id: Optional[str] = None
        self._capability_recent_inputs: list[dict[str, Any]] = []
        self._unit_requests: dict[str, UnitRequest] = {}
        self._unit_reservations: dict[str, UnitReservation] = {}
        self._request_reservations: dict[str, str] = {}
        self._task_actor_groups: dict[str, set[int]] = {}
        self._defend_base_last_created: float = 0.0
        self.register_auto_response_rule(
            "base_under_attack_defend_base",
            EventType.BASE_UNDER_ATTACK,
            self._handle_base_under_attack_auto_response,
        )

    def create_task(self, raw_text: str, kind: TaskKind | str, priority: int, info_subscriptions: list | None = None, *, skip_agent: bool = False) -> Task:
        with bm_span("tool_exec", name="kernel:create_task"):
            task_kind = kind if isinstance(kind, TaskKind) else TaskKind(kind)
            self._task_seq += 1
            task_label = f"{self._task_seq:03d}"
            task = Task(
                task_id=_gen_id("t_"),
                raw_text=raw_text,
                kind=task_kind,
                priority=priority,
                status=TaskStatus.RUNNING,
                label=task_label,
                info_subscriptions=list(info_subscriptions) if info_subscriptions else [],
            )
            tool_executor = self._build_tool_executor(task)
            agent = self.task_agent_factory(
                task,
                tool_executor,
                self.jobs_for_task,
                self._task_world_summary,
            )
            # Wire runtime_facts provider if the agent supports it (TaskAgent does).
            if hasattr(agent, "set_runtime_facts_provider"):
                agent.set_runtime_facts_provider(
                    lambda task_id, _task=task: self.world_model.compute_runtime_facts(
                        task_id,
                        include_buildable=bool(
                            getattr(_task, "is_capability", False)
                            or task_id == self._capability_task_id
                        ),
                    )
                )
            # Wire active_tasks provider for multi-task scope awareness.
            if hasattr(agent, "set_active_tasks_provider"):
                agent.set_active_tasks_provider(self._other_active_tasks_for)
            runtime = _TaskRuntime(task=task, agent=agent, tool_executor=tool_executor)
            self.tasks[task.task_id] = task
            self._task_runtimes[task.task_id] = runtime
            if skip_agent:
                self._direct_managed_tasks.add(task.task_id)
            self._sync_world_runtime()
            if not skip_agent:
                self._maybe_start_agent(runtime)
            from logging_system import current_session_dir as _csd
            _sess = _csd()
            _log_path = str(_sess / "tasks" / f"{task.task_id}.jsonl") if _sess else f"tasks/{task.task_id}.jsonl"
            slog.info("Task created", event="task_created", task_id=task.task_id, task_label=task_label, raw_text=raw_text, kind=task.kind.value, priority=priority, task_log_path=_log_path)
            return task

    def cancel_task(self, task_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:cancel_task"):
            task = self.tasks.get(task_id)
            if task is None:
                return False
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                return False
            for job in list(self._jobs.values()):
                if job.task_id == task_id and job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
                    self.abort_job(job.job_id)
            self._release_task_job_resources(task_id)
            self._close_pending_questions_for_task(task_id)
            # Cancel any pending unit requests for this task
            for req in list(self._unit_requests.values()):
                if req.task_id == task_id and req.status in ("pending", "partial"):
                    self.cancel_unit_request(req.request_id)
            task.status = TaskStatus.ABORTED
            task.timestamp = _now()
            self._task_actor_groups.pop(task_id, None)
            self._stop_agent(task_id)
            self._sync_world_runtime()
            slog.info("Task cancelled", event="task_cancelled", task_id=task_id)
            return True

    def cancel_tasks(self, filters: dict[str, Any]) -> int:
        with bm_span("tool_exec", name="kernel:cancel_tasks"):
            count = 0
            for task in list(self.tasks.values()):
                if self._task_matches_filters(task, filters):
                    count += int(self.cancel_task(task.task_id))
            return count

    @property
    def capability_task_id(self) -> Optional[str]:
        """Return the task_id of the EconomyCapability, or None."""
        return self._capability_task_id

    def ensure_capability_task(self) -> str:
        """Create the EconomyCapability task if it doesn't exist. Returns task_id."""
        if self._capability_task_id and self._capability_task_id in self.tasks:
            task = self.tasks[self._capability_task_id]
            if task.status == TaskStatus.RUNNING:
                return self._capability_task_id
        task = self.create_task(
            raw_text="EconomyCapability — 持久经济规划",
            kind=TaskKind.MANAGED,
            priority=90,
            info_subscriptions=["base_state", "threat", "production"],
        )
        task.is_capability = True
        self._capability_task_id = task.task_id
        self._capability_recent_inputs = []
        self._sync_world_runtime()
        slog.info(
            "EconomyCapability created",
            event="capability_created",
            task_id=task.task_id,
        )
        return task.task_id

    def is_direct_managed(self, task_id: str) -> bool:
        """Return True if this task has no TaskAgent (skip_agent mode)."""
        return task_id in self._direct_managed_tasks

    def inject_player_message(self, task_id: str, text: str) -> bool:
        """Inject a player message into a running LLM-managed task's event queue.

        Returns True if the message was injected, False if task not found or invalid.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return False
        if task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING):
            return False
        if task_id in self._direct_managed_tasks:
            return False
        runtime = self._task_runtimes.get(task_id)
        if runtime is None:
            return False
        event = Event(
            type=EventType.PLAYER_MESSAGE,
            data={"text": text, "timestamp": _now()},
        )
        runtime.agent.push_event(event)
        if task_id == self._capability_task_id:
            self._capability_recent_inputs.append({"text": text, "timestamp": _now()})
            self._capability_recent_inputs = self._capability_recent_inputs[-5:]
            self._sync_world_runtime()
        slog.info("Player message injected", event="player_message_injected",
                  task_id=task_id, text=text[:80])
        return True

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
    ) -> dict[str, Any]:
        """Register a unit request: idle matching → fast-path bootstrap → waiting.

        Returns:
            {"status": "fulfilled", "actor_ids": [...]}  — idle units matched
            {"status": "waiting", "request_id": "..."}   — production needed
        """
        task = self.tasks.get(task_id)
        if task is None:
            return {"status": "error", "message": f"Task {task_id} not found"}
        normalized_min_start = max(1, min(int(min_start_package), int(count)))
        if category == "building" and not bool(getattr(task, "is_capability", False)):
            return {
                "status": "error",
                "message": "普通任务不能直接请求建筑前置，请请求所需单位并等待 Capability 处理",
            }
        request_id = _gen_id("req_")
        req = UnitRequest(
            request_id=request_id,
            task_id=task_id,
            task_label=task.label,
            task_summary=task.raw_text[:60],
            category=category,
            count=count,
            urgency=urgency,
            hint=hint,
            blocking=bool(blocking),
            min_start_package=normalized_min_start,
        )
        self._unit_requests[request_id] = req
        unit_type, queue_type = self._infer_unit_type(req.category, req.hint)
        if unit_type is not None and queue_type is not None:
            self._ensure_reservation_for_request(req, unit_type)

        # Step 1: idle matching
        if self._try_fulfill_from_idle(req):
            req.status = "fulfilled"
            slog.info("Unit request fulfilled from idle", event="unit_request_fulfilled",
                      task_id=task_id, request_id=request_id, actor_ids=req.assigned_actor_ids)
            result = self._unit_request_result(req, status="fulfilled")
            result["actor_ids"] = list(req.assigned_actor_ids)
            return result

        # Partial idle match updates status
        if req.fulfilled > 0:
            req.status = "partial"

        # Step 2: fast-path bootstrap production for remaining
        self._bootstrap_production_for_request(req)

        # Sync so Capability sees the new request in runtime_facts
        self._sync_world_runtime()

        # If fast-path couldn't handle it, notify Capability
        if req.fulfilled < req.count and req.bootstrap_job_id is None:
            self._notify_capability_unfulfilled(req)

        # Suspend requesting agent if there are pending requests
        self._suspend_agent_for_requests(task_id)

        slog.info("Unit request registered", event="unit_request",
                  task_id=task_id, request_id=request_id,
                  category=category, count=count, urgency=urgency, hint=hint,
                  fulfilled=req.fulfilled, status=req.status,
                  blocking=req.blocking, min_start_package=req.min_start_package)
        return self._unit_request_result(req, status="waiting")

    def cancel_unit_request(self, request_id: str) -> bool:
        """Cancel a pending unit request."""
        req = self._unit_requests.get(request_id)
        if req is None or req.status in ("fulfilled", "cancelled"):
            return False
        reservation = self._reservation_for_request(req)
        bootstrap_job_id = req.bootstrap_job_id or (reservation.bootstrap_job_id if reservation is not None else None)
        if bootstrap_job_id:
            job = self._jobs.get(bootstrap_job_id)
            if job is not None and job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
                self.abort_job(bootstrap_job_id)
        req.status = "cancelled"
        if reservation is not None:
            reservation.status = ReservationStatus.CANCELLED
            reservation.cancelled_at = _now()
            reservation.updated_at = _now()
        self._sync_world_runtime()
        return True

    def list_unit_requests(self, status: Optional[str] = None) -> list[UnitRequest]:
        """List unit requests, optionally filtered by status."""
        reqs = list(self._unit_requests.values())
        if status is not None:
            reqs = [r for r in reqs if r.status == status]
        return reqs

    def list_unit_reservations(self, status: Optional[str] = None) -> list[UnitReservation]:
        reservations = list(self._unit_reservations.values())
        if status is not None:
            reservations = [r for r in reservations if r.status.value == status]
        reservations.sort(key=lambda item: item.created_at)
        return reservations

    def _unit_request_result(self, req: UnitRequest, *, status: str) -> dict[str, Any]:
        reservation = self._reservation_for_request(req)
        unit_type = reservation.unit_type if reservation is not None else ""
        queue_type = _queue_type_for_unit_type(unit_type)
        if not queue_type:
            inferred_unit_type, inferred_queue_type = self._infer_unit_type(req.category, req.hint)
            if not unit_type and inferred_unit_type:
                unit_type = inferred_unit_type
            if not queue_type and inferred_queue_type:
                queue_type = inferred_queue_type
        remaining = max(req.count - req.fulfilled, 0)
        result: dict[str, Any] = {
            "status": status,
            "request_id": req.request_id,
            "remaining_count": remaining,
            "fulfilled": req.fulfilled,
            "count": req.count,
            "urgency": req.urgency,
            "hint": req.hint,
            "blocking": req.blocking,
            "min_start_package": req.min_start_package,
            "start_released": req.start_released,
            "reservation_id": reservation.reservation_id if reservation is not None else "",
            "reservation_status": reservation.status.value if reservation is not None else "",
            "bootstrap_job_id": req.bootstrap_job_id or (reservation.bootstrap_job_id if reservation is not None else ""),
            "bootstrap_task_id": req.bootstrap_task_id or (reservation.bootstrap_task_id if reservation is not None else ""),
            "unit_type": unit_type,
            "queue_type": queue_type,
        }
        return result

    # --- Unit request internals ---

    def _ensure_reservation_for_request(self, req: UnitRequest, unit_type: str) -> UnitReservation:
        reservation_id = self._request_reservations.get(req.request_id)
        if reservation_id and reservation_id in self._unit_reservations:
            return self._unit_reservations[reservation_id]
        reservation = UnitReservation(
            reservation_id=_gen_id("res_"),
            request_id=req.request_id,
            task_id=req.task_id,
            task_label=req.task_label,
            task_summary=req.task_summary,
            category=req.category,
            unit_type=unit_type,
            count=req.count,
            urgency=req.urgency,
            hint=req.hint,
            blocking=req.blocking,
            min_start_package=req.min_start_package,
        )
        self._unit_reservations[reservation.reservation_id] = reservation
        self._request_reservations[req.request_id] = reservation.reservation_id
        return reservation

    def _reservation_for_request(self, req: UnitRequest) -> Optional[UnitReservation]:
        reservation_id = self._request_reservations.get(req.request_id)
        if not reservation_id:
            return None
        return self._unit_reservations.get(reservation_id)

    def _clear_request_bootstrap_refs(
        self,
        req: UnitRequest,
        reservation: Optional[UnitReservation],
    ) -> None:
        req.bootstrap_job_id = None
        req.bootstrap_task_id = None
        if reservation is not None:
            reservation.bootstrap_job_id = None
            reservation.bootstrap_task_id = None
            reservation.updated_at = _now()

    @staticmethod
    def _reservation_actor_total(reservation: UnitReservation) -> int:
        assigned = {int(actor_id) for actor_id in (reservation.assigned_actor_ids or [])}
        produced = {int(actor_id) for actor_id in (reservation.produced_actor_ids or [])}
        return len(assigned | produced)

    def _reconcile_request_bootstrap(self, req: UnitRequest) -> None:
        """Shrink or clear internal bootstrap production after new idle assignments.

        This keeps future-unit ownership closer to reality when a request that already
        started fast-path bootstrap later picks up live idle actors.
        """
        reservation = self._reservation_for_request(req)
        bootstrap_job_id = req.bootstrap_job_id or (reservation.bootstrap_job_id if reservation is not None else None)
        if not bootstrap_job_id:
            return
        controller = self._jobs.get(bootstrap_job_id)
        if controller is None or self._is_terminal_status(controller.status):
            self._clear_request_bootstrap_refs(req, reservation)
            return
        if controller.expert_type != "EconomyExpert":
            return
        config = getattr(controller, "config", None)
        issued_count = int(getattr(controller, "issued_count", 0) or 0)
        produced_count = int(getattr(controller, "produced_count", 0) or 0)
        if config is None or not hasattr(config, "count"):
            return
        current_target = int(getattr(config, "count", 0) or 0)
        desired_remaining = max(req.count - req.fulfilled, 0)
        new_target = max(desired_remaining, issued_count, produced_count, 0)
        if new_target >= current_target:
            return

        if new_target == 0 and issued_count == 0:
            if controller.resources:
                self._release_job_resources(controller)
            controller.resources = []
            controller.status = JobStatus.ABORTED
            self._resource_loss_notified.discard(bootstrap_job_id)
            self._clear_request_bootstrap_refs(req, reservation)
            slog.info(
                "Bootstrap job cleared after idle fulfillment",
                event="bootstrap_reconciled",
                request_id=req.request_id,
                reservation_id=reservation.reservation_id if reservation is not None else "",
                job_id=bootstrap_job_id,
                desired_remaining=desired_remaining,
                previous_target=current_target,
                new_target=0,
                mode="clear",
            )
            return

        controller.patch({"count": new_target})
        if reservation is not None:
            reservation.updated_at = _now()
        slog.info(
            "Bootstrap job reconciled after idle fulfillment",
            event="bootstrap_reconciled",
            request_id=req.request_id,
            reservation_id=reservation.reservation_id if reservation is not None else "",
            job_id=bootstrap_job_id,
            desired_remaining=desired_remaining,
            previous_target=current_target,
            new_target=new_target,
            issued_count=issued_count,
            produced_count=produced_count,
            mode="shrink",
        )

    def _request_runtime_reason(
        self,
        req: UnitRequest,
        reservation: Optional[UnitReservation],
        buildable: dict[str, list[str]],
    ) -> str:
        """Return a compact reason string for why a request is still open."""
        unit_type = reservation.unit_type if reservation is not None else ""
        queue_type = _queue_type_for_unit_type(unit_type)
        buildable_units = buildable.get(queue_type, []) if queue_type else []

        if req.bootstrap_job_id:
            return "bootstrap_in_progress" if req.blocking else "reinforcement_bootstrapping"
        if req.start_released:
            return "start_package_released" if req.blocking else "reinforcement_after_start"
        if not unit_type or not queue_type:
            return "inference_pending"
        if unit_type not in buildable_units:
            return "missing_prerequisite"
        return "waiting_dispatch" if req.blocking else "reinforcement_waiting_dispatch"

    def _try_fulfill_from_idle(self, req: UnitRequest) -> bool:
        """Try to fulfill a request from idle, unbound units on the field."""
        if req.category == "building":
            return False
        actor_category = _CATEGORY_TO_ACTOR_CATEGORY.get(req.category)
        if actor_category is None:
            return False
        idle = self.world_model.find_actors(
            owner="self", idle_only=True, unbound_only=True,
            category=actor_category,
        )
        if not idle:
            return False
        # Sort by hint relevance (exact name match first)
        idle.sort(key=lambda a: self._hint_match_score(a, req.hint), reverse=True)
        to_bind = idle[:req.count - req.fulfilled]
        for actor in to_bind:
            self._bind_actor_to_request(req, actor)
        return req.fulfilled >= req.count

    def _bind_actor_to_request(self, req: UnitRequest, actor: Any, *, produced: bool = False) -> None:
        """Bind an actor to a unit request."""
        resource_id = f"actor:{actor.actor_id}"
        self.world_model.bind_resource(resource_id, f"req:{req.request_id}")
        if actor.actor_id not in req.assigned_actor_ids:
            req.assigned_actor_ids.append(actor.actor_id)
        req.fulfilled += 1
        reservation = self._reservation_for_request(req)
        if reservation is not None:
            if actor.actor_id not in reservation.assigned_actor_ids:
                reservation.assigned_actor_ids.append(actor.actor_id)
            if produced and actor.actor_id not in reservation.produced_actor_ids:
                reservation.produced_actor_ids.append(actor.actor_id)
            reservation.status = (
                ReservationStatus.ASSIGNED
                if req.fulfilled >= req.count
                else ReservationStatus.PARTIAL
            )
            reservation.updated_at = _now()

    @staticmethod
    def _request_start_goal(req: UnitRequest) -> int:
        return max(1, min(int(req.count), int(req.min_start_package or 1)))

    def _request_can_start(self, req: UnitRequest) -> bool:
        return req.fulfilled >= self._request_start_goal(req)

    def _task_has_blocking_wait(self, task_id: str) -> bool:
        for req in self._unit_requests.values():
            if req.task_id != task_id:
                continue
            if req.status in ("fulfilled", "cancelled"):
                continue
            if not req.blocking:
                continue
            if req.start_released:
                continue
            if self._request_can_start(req):
                continue
            return True
        return False

    def _handoff_request_assignments(self, req: UnitRequest) -> list[int]:
        transferred: list[int] = []
        for actor_id in req.assigned_actor_ids:
            resource_id = f"actor:{actor_id}"
            if self.world_model.resource_bindings.get(resource_id) == f"req:{req.request_id}":
                self.world_model.unbind_resource(resource_id)
                transferred.append(actor_id)
        if transferred:
            self._set_task_actor_group(req.task_id, transferred)
        return transferred

    @staticmethod
    def _agent_is_suspended(agent: Any) -> bool:
        flag = getattr(agent, "is_suspended", None)
        if flag is not None:
            return bool(flag)
        return bool(getattr(agent, "_suspended", False))

    @staticmethod
    def _hint_match_score(actor: Any, hint: str) -> int:
        """Score how well an actor matches the hint. Higher = better."""
        if not hint:
            return 0
        name = getattr(actor, "name", "") or ""
        display = getattr(actor, "display_name", "") or ""
        if name and name in hint:
            return 2
        if display and display in hint:
            return 2
        return 0

    def _infer_unit_type(self, category: str, hint: str) -> tuple[Optional[str], Optional[str]]:
        """Infer concrete (unit_type, queue_type) from category + hint.

        Returns (None, None) if no inference possible — leaves it for Capability.
        """
        # Try hint keywords first
        for keyword, (unit_type, queue_type) in _HINT_TO_UNIT.items():
            if keyword in hint:
                return unit_type, queue_type
        # Fall back to category default
        default = _CATEGORY_DEFAULTS.get(category)
        if default:
            return default
        return None, None

    def _bootstrap_production_for_request(self, req: UnitRequest) -> None:
        """Start a direct EconomyJob for remaining unfulfilled count."""
        remaining = req.count - req.fulfilled
        if remaining <= 0:
            return
        unit_type, queue_type = self._infer_unit_type(req.category, req.hint)
        if unit_type is None or queue_type is None:
            return  # Can't infer — leave for Capability
        reservation = self._ensure_reservation_for_request(req, unit_type)
        reservation.updated_at = _now()

        # Check buildable via world_model derived data
        buildable = self.world_model.runtime_facts_buildable()
        queue_items = buildable.get(queue_type, [])
        if unit_type not in queue_items:
            return  # Not producible — leave for Capability

        bootstrap_task_id = req.task_id
        if self._capability_task_id:
            capability_task = self.tasks.get(self._capability_task_id)
            if capability_task is not None and capability_task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING}:
                bootstrap_task_id = capability_task.task_id

        # Start shared production on the capability task when available so
        # requesters remain consumers of units instead of accidental owners of
        # EconomyExpert jobs.
        config = EconomyJobConfig(
            unit_type=unit_type,
            count=remaining,
            queue_type=queue_type,
            request_id=req.request_id,
            reservation_id=reservation.reservation_id,
        )
        try:
            job = self.start_job(bootstrap_task_id, "EconomyExpert", config)
            req.bootstrap_job_id = job.job_id
            req.bootstrap_task_id = bootstrap_task_id
            reservation.bootstrap_job_id = job.job_id
            reservation.bootstrap_task_id = bootstrap_task_id
            if reservation.status == ReservationStatus.PENDING and req.fulfilled > 0:
                reservation.status = ReservationStatus.PARTIAL
            reservation.updated_at = _now()
            slog.info("Bootstrap production for request", event="bootstrap_production",
                      request_id=req.request_id, unit_type=unit_type, count=remaining,
                      job_id=job.job_id, bootstrap_task_id=bootstrap_task_id)
        except Exception as exc:
            slog.warning("Bootstrap production failed", event="bootstrap_production_failed",
                         request_id=req.request_id, error=str(exc))
            return

        # Notify Capability of the fast-path production
        if self._capability_task_id:
            self.inject_player_message(
                self._capability_task_id,
                f"[Kernel fast-path] 已为 Task#{req.task_label} 启动生产: "
                f"{unit_type}×{remaining} (REQ-{req.request_id})",
            )

    def _notify_capability_unfulfilled(self, req: UnitRequest) -> None:
        """Push UNIT_REQUEST_UNFULFILLED event to wake Capability."""
        if not self._capability_task_id:
            return
        runtime = self._task_runtimes.get(self._capability_task_id)
        if runtime is None:
            return
        event = Event(
            type=EventType.UNIT_REQUEST_UNFULFILLED,
            data={
                "request_id": req.request_id,
                "task_label": req.task_label,
                "category": req.category,
                "count": req.count,
                "fulfilled": req.fulfilled,
                "urgency": req.urgency,
                "hint": req.hint,
                "blocking": req.blocking,
                "min_start_package": req.min_start_package,
            },
        )
        runtime.agent.push_event(event)
        slog.info("Capability notified of unfulfilled request",
                  event="capability_notify_unfulfilled", request_id=req.request_id)

    def _fulfill_unit_requests(self) -> None:
        """Scan idle units and assign to pending requests by priority."""
        if not self._unit_requests:
            return
        idle = self.world_model.find_actors(
            owner="self", idle_only=True, unbound_only=True,
        )
        if not idle:
            return

        def _available_match_count(req: UnitRequest) -> int:
            actor_category = _CATEGORY_TO_ACTOR_CATEGORY.get(req.category)
            matched = [
                actor for actor in idle
                if actor_category is None or actor.category.value == actor_category
            ]
            return len(matched)

        pending = sorted(
            [r for r in self._unit_requests.values() if r.status in ("pending", "partial")],
            key=lambda r: (
                -_URGENCY_WEIGHT.get(r.urgency, 1),
                -int(bool(r.blocking)),
                -int((r.fulfilled + _available_match_count(r)) >= max(1, r.min_start_package)),
                -(self.tasks[r.task_id].priority if r.task_id in self.tasks else 0),
                r.created_at,
            ),
        )
        if not pending:
            return

        for req in pending:
            remaining = req.count - req.fulfilled
            if remaining <= 0:
                continue
            if req.category == "building":
                continue
            actor_category = _CATEGORY_TO_ACTOR_CATEGORY.get(req.category)
            matched = [a for a in idle if actor_category is None
                       or a.category.value == actor_category]
            matched.sort(key=lambda a: self._hint_match_score(a, req.hint), reverse=True)
            for actor in matched[:remaining]:
                # These actors came from the live idle pool, not from an explicit
                # produced-unit handoff path.
                self._bind_actor_to_request(req, actor, produced=False)
                idle.remove(actor)

            if req.fulfilled >= req.count:
                req.status = "fulfilled"
            elif req.fulfilled > 0:
                req.status = "partial"
            self._reconcile_request_bootstrap(req)
            self._wake_waiting_agent(req.task_id)

            if not idle:
                break

    def _suspend_agent_for_requests(self, task_id: str) -> None:
        """If the task has waiting requests, suspend its agent."""
        if not self._task_has_blocking_wait(task_id):
            return
        runtime = self._task_runtimes.get(task_id)
        if runtime is None:
            return
        runtime.agent.suspend()

    def _wake_waiting_agent(self, task_id: str) -> None:
        """Resume a task once blocking requests have reached their start package."""
        if self._task_has_blocking_wait(task_id):
            return
        runtime = self._task_runtimes.get(task_id)
        if runtime is None:
            return
        assigned_ids: list[int] = []
        for r in self._unit_requests.values():
            if r.task_id != task_id:
                continue
            if r.status in ("cancelled",):
                continue
            if not r.start_released and (r.status == "fulfilled" or self._request_can_start(r)):
                r.start_released = True
                reservation = self._reservation_for_request(r)
                if reservation is not None:
                    reservation.start_released = True
                    reservation.updated_at = _now()
            if r.start_released:
                assigned_ids.extend(self._handoff_request_assignments(r))
        if not assigned_ids:
            return
        message = "请求单位已达到可启动数量"
        if all(
            req.status in ("fulfilled", "cancelled")
            for req in self._unit_requests.values()
            if req.task_id == task_id and req.blocking
        ):
            message = "所有请求的单位已到位"
        event = Event(
            type=EventType.UNIT_ASSIGNED,
            data={"message": message, "actor_ids": assigned_ids},
        )
        if self._agent_is_suspended(runtime.agent):
            runtime.agent.resume_with_event(event)
        else:
            runtime.agent.push_event(event)
        self._sync_world_runtime()
        slog.info("Agent woken after request fulfillment",
                  event="agent_woken_requests_fulfilled", task_id=task_id,
                  actor_ids=assigned_ids, fully_fulfilled=message == "所有请求的单位已到位")

    def complete_task(self, task_id: str, result: str, summary: str) -> bool:
        with bm_span("tool_exec", name="kernel:complete_task", metadata={"result": result}):
            task = self.tasks.get(task_id)
            if task is None:
                return False
            if result == "succeeded":
                task.status = TaskStatus.SUCCEEDED
            elif result == "failed":
                task.status = TaskStatus.FAILED
            elif result == "partial":
                task.status = TaskStatus.PARTIAL
            else:
                raise ValueError(f"Unsupported task result: {result}")
            task.timestamp = _now()
            for job in list(self._jobs.values()):
                if job.task_id == task_id and job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
                    self.abort_job(job.job_id)
            self._release_task_job_resources(task_id)
            self._close_pending_questions_for_task(task_id)
            # Register TASK_COMPLETE_REPORT before stopping the agent —
            # appended directly (not via register_task_message which rejects terminal status).
            self.task_messages.append(TaskMessage(
                message_id=_gen_id("msg_"),
                task_id=task_id,
                type=TaskMessageType.TASK_COMPLETE_REPORT,
                content=summary,
                priority=task.priority,
            ))
            self._task_actor_groups.pop(task_id, None)
            self._stop_agent(task_id)
            self._sync_world_runtime()
            slog.info("Task completed", event="task_completed", task_id=task_id, result=result, summary=summary)
            return True

    def start_job(self, task_id: str, expert_type: str, config: ExpertConfig) -> Job:
        with bm_span("tool_exec", name="kernel:start_job", metadata={"expert_type": expert_type}):
            task = self._require_task(task_id)
            validate_job_config(expert_type, config)
            controller = self._make_job_controller(task_id, expert_type, config)
            self._jobs[controller.job_id] = controller
            self._resource_needs[controller.job_id] = self._build_resource_needs(controller, config)
            task.status = TaskStatus.RUNNING
            task.timestamp = _now()
            slog.info("Job started", event="job_started", task_id=task_id, job_id=controller.job_id, expert_type=expert_type, config=config)
            self._rebalance_resources()
            self._sync_world_runtime()
            return controller.to_model()

    def abort_job(self, job_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:abort_job"):
            controller = self._jobs.get(job_id)
            if controller is None:
                return False
            controller.abort()
            self._release_job_resources(controller)
            self._resource_loss_notified.discard(job_id)
            self._rebalance_resources()
            self._sync_world_runtime()
            slog.warn("Job aborted by Kernel", event="job_aborted", job_id=job_id, task_id=controller.task_id)
            return True

    def patch_job(self, job_id: str, params: dict[str, Any]) -> bool:
        with bm_span("tool_exec", name="kernel:patch_job"):
            controller = self._require_job(job_id)
            controller.patch(params)
            # Refresh resource needs in case config changed (e.g. scout_count).
            config = getattr(controller, "config", None)
            if config is not None:
                self._resource_needs[job_id] = self._build_resource_needs(controller, config)
            self._rebalance_resources()
            self._sync_world_runtime()
            return True

    def pause_job(self, job_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:pause_job"):
            controller = self._require_job(job_id)
            if self._is_terminal_status(controller.status):
                return False
            controller.pause()
            self._sync_world_runtime()
            return True

    def resume_job(self, job_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:resume_job"):
            controller = self._require_job(job_id)
            if self._is_terminal_status(controller.status):
                return False
            controller.resume()
            self._rebalance_resources()
            self._sync_world_runtime()
            slog.info("Job resumed by Kernel", event="job_resumed", job_id=job_id, task_id=controller.task_id)
            return True

    def route_event(self, event: Event) -> None:
        with bm_span("tool_exec", name=f"kernel:route_event:{event.type.value}"):
            slog.info("Kernel routing event", event="event_routed", event_type=event.type.value, actor_id=event.actor_id, position=event.position, data=event.data)
            self._apply_auto_response_rules(event)
            if event.type == EventType.GAME_RESET:
                self._handle_game_reset(event)
                return
            if event.type in {EventType.UNIT_DIED, EventType.UNIT_DAMAGED}:
                self._route_actor_event(event)
                return
            if event.type in {EventType.ENEMY_DISCOVERED, EventType.STRUCTURE_LOST}:
                self._broadcast_event(event)
                return
            if event.type == EventType.BASE_UNDER_ATTACK:
                self._broadcast_event(event)
                return
            if event.type == EventType.LOW_POWER:
                # Push to Capability so it can build a power plant
                if self._capability_task_id:
                    runtime = self._task_runtimes.get(self._capability_task_id)
                    if runtime is not None:
                        runtime.agent.push_event(event)
                return
            if event.type in {EventType.ENEMY_EXPANSION, EventType.FRONTLINE_WEAK, EventType.ECONOMY_SURPLUS}:
                self._push_player_notification(event)
                return
            if event.type == EventType.PRODUCTION_COMPLETE:
                self._rebalance_resources()
                self._fulfill_unit_requests()
                return
            return None

    def _handle_game_reset(self, event: Event) -> None:
        for task_id in list(self._task_runtimes):
            self._stop_agent(task_id)
        self.tasks.clear()
        self._task_runtimes.clear()
        self._jobs.clear()
        self._constraints.clear()
        self._resource_needs.clear()
        self._resource_loss_notified.clear()
        self._pending_questions.clear()
        self._timed_out_questions.clear()
        self._closed_questions.clear()
        self._delivered_player_responses.clear()
        self._unit_requests.clear()
        self._unit_reservations.clear()
        self._request_reservations.clear()
        self._task_actor_groups.clear()
        self._direct_managed_tasks.clear()
        self._capability_task_id = None
        self._capability_recent_inputs.clear()
        self.task_messages.clear()
        self.world_model.set_runtime_state(
            active_tasks={},
            active_jobs={},
            resource_bindings={},
            constraints=[],
            capability_status={},
            unit_reservations=[],
        )
        self.push_player_notification(
            "game_reset",
            "检测到对局已重置，已清理旧任务状态",
            data=event.data,
            timestamp=event.timestamp,
        )
        slog.warn("Kernel cleared stale runtime after game reset", event="game_reset_handled", data=event.data)
        self.ensure_capability_task()

    def route_events(self, events: list[Event]) -> None:
        with bm_span("tool_exec", name="kernel:route_events", metadata={"count": len(events)}):
            for event in events:
                self.route_event(event)

    def route_signal(self, signal: ExpertSignal) -> None:
        task = self.tasks.get(signal.task_id)
        if task is None or task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.ABORTED,
            TaskStatus.PARTIAL,
        }:
            return
        runtime = self._task_runtimes.get(signal.task_id)
        if runtime is None:
            return
        slog.info("Kernel routed expert signal", event="signal_routed", task_id=signal.task_id, job_id=signal.job_id, signal_kind=signal.kind.value, result=signal.result)
        if signal.kind == SignalKind.BLOCKED:
            self.register_task_message(
                TaskMessage(
                    message_id=_gen_id("msg_"),
                    task_id=signal.task_id,
                    type=TaskMessageType.TASK_WARNING,
                    content=signal.summary,
                    priority=task.priority,
                )
            )

        # Direct-managed tasks have no running agent — auto-close on TASK_COMPLETE
        if signal.kind == SignalKind.TASK_COMPLETE and self.is_direct_managed(signal.task_id):
            result_map = {"succeeded": "succeeded", "failed": "failed", "aborted": "failed"}
            result = result_map.get(signal.result, "succeeded")
            self.complete_task(signal.task_id, result, signal.summary or "direct job completed")
            return

        runtime.agent.push_signal(signal)

    def get_task_agent(self, task_id: str) -> Optional[TaskAgentLike]:
        runtime = self._task_runtimes.get(task_id)
        return runtime.agent if runtime else None

    def jobs_for_task(self, task_id: str) -> list[Job]:
        jobs = [controller.to_model() for controller in self._jobs.values() if controller.task_id == task_id]
        jobs.sort(key=lambda item: item.job_id)
        return jobs

    def active_jobs(self) -> tuple[BaseJob | _ManagedJob, ...]:
        """Return a read-only snapshot of non-terminal job controllers."""
        jobs = [controller for controller in self._jobs.values() if not self._is_terminal_status(controller.status)]
        jobs.sort(key=lambda item: item.job_id)
        return tuple(jobs)

    def list_tasks(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda item: item.created_at)

    def list_jobs(self) -> list[Job]:
        jobs = [controller.to_model() for controller in self._jobs.values()]
        jobs.sort(key=lambda item: item.job_id)
        return jobs

    def list_player_notifications(self) -> list[dict[str, Any]]:
        return list(self.player_notifications)

    def push_player_notification(
        self,
        notification_type: str,
        content: str,
        *,
        data: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        self.player_notifications.append(
            {
                "type": notification_type,
                "content": content,
                "data": dict(data or {}),
                "timestamp": _now() if timestamp is None else timestamp,
            }
        )
        slog.info("Player notification queued", event="player_notification", notification_type=notification_type, content=content, data=data or {})

    def list_task_messages(self, task_id: Optional[str] = None) -> list[TaskMessage]:
        if task_id is None:
            return list(self.task_messages)
        return [message for message in self.task_messages if message.task_id == task_id]

    def list_pending_questions(self) -> list[dict[str, Any]]:
        pending = sorted(self._pending_questions.values(), key=lambda item: (item.message.priority, item.message.timestamp), reverse=True)
        return [
            {
                "message_id": item.message.message_id,
                "task_id": item.message.task_id,
                "question": item.message.content,
                "options": list(item.message.options or []),
                "default_option": item.message.default_option,
                "priority": item.message.priority,
                "asked_at": item.message.timestamp,
                "timeout_s": item.message.timeout_s,
                "deadline_at": item.deadline_at,
            }
            for item in pending
        ]

    def reset_session(self) -> None:
        for runtime in list(self._task_runtimes.values()):
            runtime.agent.stop()
            if runtime.runner is not None:
                runtime.runner.cancel()

        for controller in list(self._jobs.values()):
            if not self._is_terminal_status(controller.status):
                controller.abort()
            if controller.resources:
                self._release_job_resources(controller)

        self.tasks.clear()
        self._task_runtimes.clear()
        self._jobs.clear()
        self._constraints.clear()
        self._resource_needs.clear()
        self._resource_loss_notified.clear()
        self.player_notifications.clear()
        self.task_messages.clear()
        self._pending_questions.clear()
        self._timed_out_questions.clear()
        self._closed_questions.clear()
        self._delivered_player_responses.clear()
        self._unit_requests.clear()
        self._unit_reservations.clear()
        self._request_reservations.clear()
        self._task_actor_groups.clear()
        self._direct_managed_tasks.clear()
        self._capability_task_id = None
        self._capability_recent_inputs.clear()
        self._sync_world_runtime()
        self.ensure_capability_task()

    def register_task_message(self, message: TaskMessage) -> bool:
        with bm_span("tool_exec", name=f"kernel:register_task_message:{message.type.value}"):
            task = self.tasks.get(message.task_id)
            if task is None or task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                return False
            self.task_messages.append(message)
            slog.info("Task message registered", event="task_message_registered", task_id=message.task_id, message_id=message.message_id, message_type=message.type.value, priority=message.priority)
            if message.type == TaskMessageType.TASK_QUESTION:
                if message.timeout_s is None or message.default_option is None:
                    raise ValueError("task_question requires timeout_s and default_option")
                self._pending_questions[message.message_id] = _PendingQuestion(
                    message=message,
                    deadline_at=message.timestamp + message.timeout_s,
                    default_option=message.default_option,
                )
                self._timed_out_questions.discard(message.message_id)
                self._closed_questions.discard(message.message_id)
            return True

    def cancel_pending_question(self, message_id: str) -> bool:
        pending = self._pending_questions.pop(message_id, None)
        if pending is None:
            return False
        self._closed_questions.add(message_id)
        return True

    def submit_player_response(
        self,
        response: PlayerResponse,
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        with bm_span("tool_exec", name="kernel:submit_player_response"):
            timestamp = _now() if now is None else now
            pending = self._pending_questions.get(response.message_id)
            if pending is None:
                if response.message_id in self._timed_out_questions:
                    return {
                        "ok": False,
                        "status": "timed_out",
                        "message": "已按默认处理，如需更改请重新下令",
                        "timestamp": timestamp,
                    }
                if response.message_id in self._closed_questions:
                    return {
                        "ok": False,
                        "status": "closed",
                        "message": "任务已结束，请重新下令",
                        "timestamp": timestamp,
                    }
                return {
                    "ok": False,
                    "status": "unknown_message",
                    "message": "未找到对应问题",
                    "timestamp": timestamp,
                }
            if pending.deadline_at <= timestamp:
                self._expire_pending_question(response.message_id, timestamp)
                return {
                    "ok": False,
                    "status": "timed_out",
                    "message": "已按默认处理，如需更改请重新下令",
                    "timestamp": timestamp,
                }
            if pending.message.task_id != response.task_id:
                return {
                    "ok": False,
                    "status": "task_mismatch",
                    "message": "回复与任务不匹配",
                    "timestamp": timestamp,
                }
            self._pending_questions.pop(response.message_id, None)
            self._deliver_player_response(
                PlayerResponse(
                    message_id=response.message_id,
                    task_id=response.task_id,
                    answer=response.answer,
                    timestamp=timestamp,
                )
            )
            return {"ok": True, "status": "delivered", "timestamp": timestamp}

    def tick(self, *, now: Optional[float] = None) -> int:
        with bm_span("tool_exec", name="kernel:tick"):
            timestamp = _now() if now is None else now
            expired_ids = [
                message_id
                for message_id, pending in self._pending_questions.items()
                if pending.deadline_at <= timestamp
            ]
            for message_id in expired_ids:
                self._expire_pending_question(message_id, timestamp)
            return len(expired_ids)

    def register_auto_response_rule(
        self,
        rule_id: str,
        event_type: EventType,
        handler: Callable[[Event], None],
    ) -> None:
        rules = self._auto_response_rules.setdefault(event_type, [])
        rules[:] = [rule for rule in rules if rule.rule_id != rule_id]
        rules.append(_AutoResponseRule(rule_id=rule_id, event_type=event_type, handler=handler))

    def _default_task_agent_factory(
        self,
        task: Task,
        tool_executor: ToolExecutor,
        jobs_provider: Callable[[str], list[Job]],
        world_summary_provider: Callable[[], WorldSummary],
    ) -> TaskAgentLike:
        if self.llm is None:
            raise ValueError("Kernel default TaskAgent factory requires an llm provider.")
        return TaskAgent(
            task=task,
            llm=self.llm,
            tool_executor=tool_executor,
            jobs_provider=jobs_provider,
            world_summary_provider=world_summary_provider,
            config=self.config.default_agent_config,
            message_callback=self.register_task_message,
        )

    def _build_tool_executor(self, task: Task) -> ToolExecutor:
        """Build the ToolExecutor for a Task via TaskToolHandlers (single source of truth)."""
        executor = ToolExecutor()
        TaskToolHandlers(task, self, self.world_model).register_all(executor)
        return executor

    def _make_job_controller(self, task_id: str, expert_type: str, config: ExpertConfig) -> BaseJob | _ManagedJob:
        expert = self.expert_registry.get(expert_type)
        if expert is None:
            return _ManagedJob(
                job_id=_gen_id("j_"),
                task_id=task_id,
                expert_type=expert_type,
                config=config,
                signal_callback=self.route_signal,
            )
        return expert.create_job(
            task_id=task_id,
            config=config,
            signal_callback=self.route_signal,
            constraint_provider=self._constraints_for_scope,
        )

    def _require_task(self, task_id: str) -> Task:
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return task

    def _require_job(self, job_id: str) -> BaseJob | _ManagedJob:
        controller = self._jobs.get(job_id)
        if controller is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        return controller

    def _maybe_start_agent(self, runtime: _TaskRuntime) -> None:
        if not self.config.auto_start_agents:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        runtime.runner = loop.create_task(runtime.agent.run())

    def _stop_agent(self, task_id: str) -> None:
        runtime = self._task_runtimes.get(task_id)
        if runtime is None:
            return
        runtime.agent.stop()
        if runtime.runner is not None:
            runtime.runner.cancel()
            runtime.runner = None

    def _release_job_resources(self, controller: BaseJob | _ManagedJob) -> None:
        resource_ids = list(controller.resources)
        if controller.status != JobStatus.ABORTED and hasattr(controller, "on_resource_revoked"):
            controller.on_resource_revoked(resource_ids)
        else:
            controller.resources = []
        for resource_id in resource_ids:
            self.world_model.unbind_resource(resource_id)

    def _release_task_job_resources(self, task_id: str) -> None:
        for controller in self._jobs.values():
            if controller.task_id != task_id:
                continue
            if controller.resources:
                self._release_job_resources(controller)
            self._resource_loss_notified.discard(controller.job_id)

    def _sync_world_runtime(self) -> None:
        # Compute per-task job stats including terminal jobs (for runtime_facts).
        job_stats: dict[str, Any] = {}
        for controller in self._jobs.values():
            tid = controller.task_id
            etype = controller.expert_type
            status = controller.to_model().status
            stats = job_stats.setdefault(tid, {"failed_count": 0, "expert_attempts": {}})
            stats["expert_attempts"][etype] = stats["expert_attempts"].get(etype, 0) + 1
            if status == JobStatus.FAILED:
                stats["failed_count"] += 1

        # Serialize pending/partial unit requests for Capability context.
        # Also include requests with bootstrap_job_id (production in progress).
        buildable = self.world_model.runtime_facts_buildable()

        def _request_reason(req: UnitRequest, reservation: Optional[UnitReservation], unit_type: str) -> str:
            queue_type = _queue_type_for_unit_type(unit_type)
            buildable_units = buildable.get(queue_type, []) if queue_type else []
            if req.start_released:
                return "reinforcement_after_start" if not req.blocking else "start_package_released"
            if req.bootstrap_job_id:
                return "reinforcement_bootstrapping" if not req.blocking else "bootstrap_in_progress"
            if not unit_type or not queue_type:
                return "inference_pending"
            if unit_type not in buildable_units:
                return "missing_prerequisite"
            return "reinforcement_waiting_dispatch" if not req.blocking else "waiting_dispatch"

        unfulfilled = []
        for req in self._unit_requests.values():
            if req.status not in ("pending", "partial"):
                continue
            reservation = self._reservation_for_request(req)
            unit_type = reservation.unit_type if reservation is not None else ""
            unfulfilled.append(
                {
                    "request_id": req.request_id,
                    "reservation_id": (self._request_reservations.get(req.request_id) or ""),
                    "task_label": req.task_label,
                    "task_summary": req.task_summary,
                    "category": req.category,
                    "unit_type": unit_type,
                    "count": req.count,
                    "fulfilled": req.fulfilled,
                    "remaining_count": max(req.count - req.fulfilled, 0),
                    "urgency": req.urgency,
                    "hint": req.hint,
                    "blocking": req.blocking,
                    "min_start_package": req.min_start_package,
                    "start_released": req.start_released,
                    "bootstrap_job_id": req.bootstrap_job_id,
                    "bootstrap_task_id": req.bootstrap_task_id,
                    "queue_type": _queue_type_for_unit_type(unit_type),
                    "reservation_status": reservation.status.value if reservation is not None else "",
                    "reason": _request_reason(req, reservation, unit_type),
                }
            )
        if unfulfilled:
            slog.info("Syncing unfulfilled requests", event="sync_unfulfilled",
                      count=len(unfulfilled),
                      requests=[r["request_id"] for r in unfulfilled])

        capability_status = CapabilityStatusSnapshot()
        if self._capability_task_id:
            capability_task = self.tasks.get(self._capability_task_id)
            if capability_task and capability_task.status not in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.ABORTED,
                TaskStatus.PARTIAL,
            }:
                capability_jobs = [
                    controller for controller in self._jobs.values()
                    if controller.task_id == capability_task.task_id
                    and controller.to_model().status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}
                ]
                capability_requests = [
                    req for req in self._unit_requests.values()
                    if req.status in ("pending", "partial")
                ]
                blocking_request_count = sum(1 for req in capability_requests if req.blocking)
                dispatch_request_count = sum(1 for req in capability_requests if not req.bootstrap_job_id and not req.start_released)
                bootstrap_wait_request_count = sum(
                    1 for req in capability_requests if req.bootstrap_job_id and not req.start_released
                )
                start_released_request_count = sum(1 for req in capability_requests if req.start_released)
                reinforcement_request_count = sum(1 for req in capability_requests if not req.blocking)
                inference_pending_count = sum(1 for item in unfulfilled if item.get("reason") == "inference_pending")
                prerequisite_gap_count = sum(1 for item in unfulfilled if item.get("reason") == "missing_prerequisite")
                if dispatch_request_count:
                    capability_phase = "dispatch"
                elif bootstrap_wait_request_count:
                    capability_phase = "bootstrapping"
                elif start_released_request_count or reinforcement_request_count:
                    capability_phase = "fulfilling"
                elif capability_jobs:
                    capability_phase = "executing"
                else:
                    capability_phase = "idle"

                blocker = ""
                if inference_pending_count:
                    blocker = "request_inference_pending"
                elif prerequisite_gap_count:
                    blocker = "missing_prerequisite"
                elif dispatch_request_count:
                    blocker = "pending_requests_waiting_dispatch"
                elif bootstrap_wait_request_count:
                    blocker = "bootstrap_in_progress"

                capability_status = CapabilityStatusSnapshot(
                    task_id=capability_task.task_id,
                    task_label=capability_task.label,
                    status=capability_task.status.value,
                    phase=capability_phase,
                    blocker=blocker,
                    active_job_count=len(capability_jobs),
                    active_job_types=[controller.expert_type for controller in capability_jobs],
                    pending_request_count=len(capability_requests),
                    blocking_request_count=blocking_request_count,
                    dispatch_request_count=dispatch_request_count,
                    bootstrapping_request_count=bootstrap_wait_request_count,
                    start_released_request_count=start_released_request_count,
                    reinforcement_request_count=reinforcement_request_count,
                    inference_pending_count=inference_pending_count,
                    prerequisite_gap_count=prerequisite_gap_count,
                    recent_directives=[str(item.get("text", "")) for item in self._capability_recent_inputs if item.get("text")],
                )

        active_reservations = []
        for reservation in self._unit_reservations.values():
            if reservation.status in {ReservationStatus.CANCELLED, ReservationStatus.EXPIRED}:
                continue
            req = self._unit_requests.get(reservation.request_id)
            if req is not None and req.status not in ("pending", "partial"):
                continue
            active_reservations.append(
                {
                    "reservation_id": reservation.reservation_id,
                    "request_id": reservation.request_id,
                    "task_id": reservation.task_id,
                    "task_label": reservation.task_label,
                    "category": reservation.category,
                    "unit_type": reservation.unit_type,
                    "count": reservation.count,
                    "urgency": reservation.urgency,
                    "hint": reservation.hint,
                    "blocking": reservation.blocking,
                    "min_start_package": reservation.min_start_package,
                    "start_released": reservation.start_released,
                    "status": reservation.status.value,
                    "request_status": req.status if req is not None else "",
                    "assigned_actor_ids": list(reservation.assigned_actor_ids),
                    "produced_actor_ids": list(reservation.produced_actor_ids),
                    "bootstrap_job_id": reservation.bootstrap_job_id,
                    "bootstrap_task_id": reservation.bootstrap_task_id,
                    "updated_at": reservation.updated_at,
                    "remaining_count": max(
                        reservation.count - self._reservation_actor_total(reservation),
                        0,
                    ),
                    "queue_type": _queue_type_for_unit_type(reservation.unit_type),
                    "reason": _request_reason(req, reservation, reservation.unit_type) if req is not None else "",
                }
            )

        self.world_model.set_runtime_state(
            active_tasks={
                task.task_id: {
                    "raw_text": task.raw_text,
                    "label": task.label,
                    "kind": task.kind.value,
                    "priority": task.priority,
                    "status": task.status.value,
                    "is_capability": bool(getattr(task, "is_capability", False)),
                    "active_actor_ids": self.task_active_actor_ids(task.task_id),
                    "active_group_size": len(self.task_active_actor_ids(task.task_id)),
                }
                for task in self.tasks.values()
                if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}
            },
            active_jobs={
                controller.job_id: {
                    "task_id": controller.task_id,
                    "expert_type": controller.expert_type,
                    "status": controller.to_model().status.value,
                }
                for controller in self._jobs.values()
                if controller.to_model().status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}
            },
            resource_bindings=dict(self.world_model.resource_bindings),
            constraints=list(self._constraints.values()),
            job_stats_by_task=job_stats,
            unfulfilled_requests=unfulfilled,
            capability_status=capability_status.to_dict(),
            unit_reservations=active_reservations,
        )

    def _set_task_actor_group(self, task_id: str, actor_ids: list[int]) -> None:
        if not actor_ids:
            return
        group = self._task_actor_groups.setdefault(task_id, set())
        group.update(int(actor_id) for actor_id in actor_ids)
        self._prune_task_actor_group(task_id)

    def _prune_task_actor_group(self, task_id: str) -> None:
        group = self._task_actor_groups.get(task_id)
        if not group:
            self._task_actor_groups.pop(task_id, None)
            return
        alive_actor_ids = {
            actor.actor_id
            for actor in self.world_model.state.actors.values()
            if actor.owner.value == "self" and actor.is_alive
        }
        group.intersection_update(alive_actor_ids)
        if not group:
            self._task_actor_groups.pop(task_id, None)

    def task_active_actor_ids(self, task_id: str) -> list[int]:
        self._prune_task_actor_group(task_id)
        group = self._task_actor_groups.get(task_id, set())
        return sorted(group)

    def task_has_running_actor_job(self, task_id: str) -> bool:
        actor_job_types = {"MovementExpert", "ReconExpert", "CombatExpert"}
        for controller in self._jobs.values():
            if controller.task_id != task_id:
                continue
            if controller.expert_type not in actor_job_types:
                continue
            if controller.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
                continue
            return True
        return False

    def _task_world_summary(self) -> WorldSummary:
        summary = self.world_model.world_summary()
        return WorldSummary(
            economy=summary.get("economy", {}),
            military=summary.get("military", {}),
            map=summary.get("map", {}),
            known_enemy=summary.get("known_enemy", {}),
            timestamp=summary.get("timestamp", _now()),
        )

    def _other_active_tasks_for(self, task_id: str) -> list[dict]:
        """Return sibling tasks (active + recently completed), excluding self.

        Includes:
        - Active tasks with their running jobs (prevents duplicated actions)
        - Recently completed tasks with their summary (cross-task memory)
        """
        terminal = {"succeeded", "failed", "aborted", "partial"}
        result = []
        # Active tasks
        for t in self.tasks.values():
            if t.task_id == task_id or t.status.value in terminal:
                continue
            entry: dict[str, Any] = {"label": t.label, "raw_text": t.raw_text, "status": t.status.value}
            # Attach compact job summaries
            jobs_summary = []
            for controller in self._jobs.values():
                if controller.task_id != t.task_id or self._is_terminal_status(controller.status):
                    continue
                job_info: dict[str, str] = {"expert": controller.expert_type}
                cfg = controller.config
                if hasattr(cfg, "unit_type"):
                    job_info["unit"] = cfg.unit_type
                if hasattr(cfg, "queue_type"):
                    job_info["queue"] = cfg.queue_type
                if hasattr(cfg, "count"):
                    job_info["count"] = str(cfg.count)
                if hasattr(cfg, "search_region"):
                    job_info["region"] = cfg.search_region
                jobs_summary.append(job_info)
            if jobs_summary:
                entry["jobs"] = jobs_summary
            result.append(entry)
        # Recent task reports from other tasks (cross-task memory)
        _REPORT_TYPES = {TaskMessageType.TASK_INFO, TaskMessageType.TASK_COMPLETE_REPORT}
        recent_reports = []
        for msg in reversed(self.task_messages):
            if msg.task_id == task_id:
                continue
            if msg.type not in _REPORT_TYPES:
                continue
            task_label = ""
            t = self.tasks.get(msg.task_id)
            if t:
                task_label = t.label
            recent_reports.append({
                "task_label": task_label,
                "type": msg.type.value,
                "content": msg.content[:120],
            })
            if len(recent_reports) >= 8:
                break
        if recent_reports:
            result.append({"_recent_reports": list(reversed(recent_reports))})
        return result

    def _constraints_for_scope(self, scope: str) -> list[Constraint]:
        return [constraint for constraint in self._constraints.values() if constraint.active and constraint.scope == scope]

    def _close_pending_questions_for_task(self, task_id: str) -> None:
        closed_ids = [
            message_id
            for message_id, pending in self._pending_questions.items()
            if pending.message.task_id == task_id
        ]
        for message_id in closed_ids:
            self._pending_questions.pop(message_id, None)
            self._closed_questions.add(message_id)

    def _task_matches_filters(self, task: Task, filters: dict[str, Any]) -> bool:
        task_ids = filters.get("task_ids")
        if task_ids and task.task_id not in set(task_ids):
            return False
        kind = filters.get("kind")
        if kind and task.kind.value != kind:
            return False
        priority_below = filters.get("priority_below")
        if priority_below is not None and task.priority >= int(priority_below):
            return False
        status = filters.get("status")
        if status and task.status.value != status:
            return False
        return True

    def _config_from_payload(self, expert_type: str, payload: dict[str, Any]) -> ExpertConfig:
        config_cls = EXPERT_CONFIG_REGISTRY[expert_type]
        return config_cls(**payload)

    def _build_resource_needs(self, controller: BaseJob | _ManagedJob, config: ExpertConfig) -> list[ResourceNeed]:
        if hasattr(controller, "get_resource_needs"):
            needs = list(controller.get_resource_needs())  # type: ignore[call-arg]
        elif hasattr(controller, "resource_needs"):
            needs = list(getattr(controller, "resource_needs"))
        else:
            needs = self._infer_resource_needs(controller, config)
        normalized: list[ResourceNeed] = []
        for need in needs:
            normalized.append(
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=need.kind,
                    count=need.count,
                    predicates=dict(need.predicates),
                    timestamp=need.timestamp,
                )
            )
        return normalized

    def _infer_resource_needs(self, controller: BaseJob | _ManagedJob, config: ExpertConfig) -> list[ResourceNeed]:
        if controller.expert_type == "ReconExpert":
            actor_ids = getattr(config, "actor_ids", None)
            if actor_ids:
                return [
                    ResourceNeed(
                        job_id=controller.job_id,
                        kind=ResourceKind.ACTOR,
                        count=1,
                        predicates={"actor_id": str(actor_id), "owner": "self"},
                    )
                    for actor_id in actor_ids
                ]
            # Soft constraint: any mobile unit works for scouting.
            # Kernel's allocation logic should prefer faster units.
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"owner": "self"},
                )
            ]
        if controller.expert_type == "CombatExpert":
            actor_ids = getattr(config, "actor_ids", None)
            if actor_ids:
                return [
                    ResourceNeed(
                        job_id=controller.job_id,
                        kind=ResourceKind.ACTOR,
                        count=1,
                        predicates={"actor_id": str(actor_id), "owner": "self"},
                    )
                    for actor_id in actor_ids
                ]
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=3,
                    predicates={"can_attack": "true", "owner": "self"},
                )
            ]
        if controller.expert_type == "MovementExpert":
            actor_ids = getattr(config, "actor_ids", None)
            if actor_ids:
                return [
                    ResourceNeed(
                        job_id=controller.job_id,
                        kind=ResourceKind.ACTOR,
                        count=1,
                        predicates={"actor_id": str(actor_id), "owner": "self"},
                    )
                    for actor_id in actor_ids
                ]
            unit_count = getattr(config, "unit_count", 0)
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=unit_count if unit_count > 0 else 999,
                    predicates={"owner": "self"},
                )
            ]
        if controller.expert_type == "DeployExpert":
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
                    predicates={"actor_id": str(getattr(config, "actor_id")), "owner": "self"},
                )
            ]
        if controller.expert_type == "EconomyExpert":
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.PRODUCTION_QUEUE,
                    count=1,
                    predicates={"queue_type": str(getattr(config, "queue_type"))},
                )
            ]
        return []

    def _rebalance_resources(self) -> None:
        requests: list[tuple[int, float, BaseJob | _ManagedJob, ResourceNeed, int]] = []
        for controller in self._jobs.values():
            if self._is_terminal_status(controller.status):
                continue
            task = self.tasks.get(controller.task_id)
            if task is None or task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                continue
            for need in self._resource_needs.get(controller.job_id, []):
                current = self._resources_for_need(controller, need)
                missing = max(0, need.count - len(current))
                if missing > 0:
                    requests.append((task.priority, task.created_at, controller, need, missing))

        requests.sort(key=lambda item: (-item[0], item[1], item[2].job_id))

        for _, _, controller, need, missing in requests:
            while missing > 0:
                claimed = self._claim_resource(controller, need)
                if claimed is None:
                    break
                missing -= 1

            remaining = max(0, need.count - len(self._resources_for_need(controller, need)))
            if remaining > 0:
                if not controller.resources and not self._is_terminal_status(controller.status):
                    controller.status = JobStatus.WAITING
                self._notify_resource_loss(controller, need, remaining)
            else:
                self._resource_loss_notified.discard(controller.job_id)

        self._sync_world_runtime()

    def _claim_resource(self, controller: BaseJob | _ManagedJob, need: ResourceNeed) -> Optional[str]:
        unbound = self._find_unbound_resource(need)
        if unbound is not None:
            self._grant_resource(controller, unbound)
            return unbound

        preemptable = self._find_preemptable_resource(controller, need)
        if preemptable is None:
            return None
        self._preempt_resource(preemptable["holder"], preemptable["resource_id"])
        self._grant_resource(controller, preemptable["resource_id"])
        return preemptable["resource_id"]

    def _find_unbound_resource(self, need: ResourceNeed) -> Optional[str]:
        if need.kind == ResourceKind.ACTOR:
            actors = self.world_model.find_actors(owner="self", idle_only=True, unbound_only=True)
            for actor in actors:
                if self._actor_matches_need(actor, need):
                    return f"actor:{actor.actor_id}"
            return None
        queue_type = need.predicates.get("queue_type")
        if queue_type is None:
            return None
        resource_id = f"queue:{queue_type}"
        if resource_id in self.world_model.resource_bindings:
            return None
        queues = self.world_model.query("production_queues")
        if queue_type in queues:
            return resource_id
        return None

    def _find_preemptable_resource(self, requester: BaseJob | _ManagedJob, need: ResourceNeed) -> Optional[dict[str, Any]]:
        requester_priority = self.tasks[requester.task_id].priority
        candidates: list[tuple[int, str, BaseJob | _ManagedJob]] = []
        if need.kind == ResourceKind.ACTOR:
            actors = self.world_model.find_actors(owner="self", idle_only=False, unbound_only=False)
            for actor in actors:
                if not self._actor_matches_need(actor, need):
                    continue
                resource_id = f"actor:{actor.actor_id}"
                holder_job_id = self.world_model.resource_bindings.get(resource_id)
                if holder_job_id is None or holder_job_id == requester.job_id:
                    continue
                holder = self._jobs.get(holder_job_id)
                if holder is None:
                    continue
                holder_priority = self.tasks[holder.task_id].priority
                if holder_priority >= requester_priority:
                    continue
                candidates.append((holder_priority, resource_id, holder))
        else:
            queue_type = need.predicates.get("queue_type")
            if queue_type is None:
                return None
            resource_id = f"queue:{queue_type}"
            holder_job_id = self.world_model.resource_bindings.get(resource_id)
            if holder_job_id is None or holder_job_id == requester.job_id:
                return None
            holder = self._jobs.get(holder_job_id)
            if holder is None:
                return None
            holder_priority = self.tasks[holder.task_id].priority
            if holder_priority >= requester_priority:
                return None
            candidates.append((holder_priority, resource_id, holder))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[2].job_id))
        _, resource_id, holder = candidates[0]
        return {"resource_id": resource_id, "holder": holder}

    def _preempt_resource(self, holder: BaseJob | _ManagedJob, resource_id: str) -> None:
        slog.warn("Kernel preempting resource", event="resource_preempted", holder_job_id=holder.job_id, holder_task_id=holder.task_id, resource_id=resource_id)
        if len(holder.resources) <= 1:
            holder.abort()
            self._release_job_resources(holder)
            return
        if hasattr(holder, "on_resource_revoked"):
            holder.on_resource_revoked([resource_id])
        else:
            if resource_id in holder.resources:
                holder.resources.remove(resource_id)
        self.world_model.unbind_resource(resource_id)

    def _grant_resource(self, controller: BaseJob | _ManagedJob, resource_id: str) -> None:
        self.world_model.bind_resource(resource_id, controller.job_id)
        controller.on_resource_granted([resource_id])
        if resource_id.startswith("actor:"):
            try:
                actor_id = int(resource_id.split(":", 1)[1])
            except (TypeError, ValueError):
                actor_id = None
            if actor_id is not None:
                self._set_task_actor_group(controller.task_id, [actor_id])
        slog.info("Kernel granted resource", event="resource_granted", job_id=controller.job_id, task_id=controller.task_id, resource_id=resource_id)

    def _resources_for_need(self, controller: BaseJob | _ManagedJob, need: ResourceNeed) -> list[str]:
        return [resource_id for resource_id in controller.resources if self._resource_matches_need(resource_id, need)]

    def _resource_matches_need(self, resource_id: str, need: ResourceNeed) -> bool:
        if need.kind == ResourceKind.ACTOR:
            if not resource_id.startswith("actor:"):
                return False
            actor_id = int(resource_id.split(":", 1)[1])
            actor = self.world_model.state.actors.get(actor_id)
            if actor is None:
                return False
            return self._actor_matches_need(actor, need)
        if not resource_id.startswith("queue:"):
            return False
        queue_type = resource_id.split(":", 1)[1]
        return need.predicates.get("queue_type") == queue_type

    def _actor_matches_need(self, actor: Any, need: ResourceNeed) -> bool:
        predicates = need.predicates
        actor_category = getattr(actor.category, "value", actor.category)
        actor_mobility = getattr(actor.mobility, "value", actor.mobility)
        explicitly_requests_static_actor = (
            predicates.get("category") == "building" or predicates.get("mobility") == "static"
        )

        # Soft actor needs such as {"owner": "self"} should not capture
        # immobile structures. Building/static actors are only allocatable when
        # the need explicitly asks for them.
        if not explicitly_requests_static_actor and (
            actor_category == "building" or actor_mobility == "static"
        ):
            return False

        for key, value in predicates.items():
            if key == "owner" and getattr(actor.owner, "value", actor.owner) != value:
                return False
            if key == "category" and actor_category != value:
                return False
            if key == "mobility" and actor_mobility != value:
                return False
            if key == "can_attack" and bool(actor.can_attack) != (str(value).lower() == "true"):
                return False
            if key == "can_harvest" and bool(actor.can_harvest) != (str(value).lower() == "true"):
                return False
            if key == "name" and actor.name != value:
                return False
            if key == "actor_id" and str(actor.actor_id) != str(value):
                return False
        return True

    def _notify_resource_loss(self, controller: BaseJob | _ManagedJob, need: ResourceNeed, missing: int) -> None:
        if controller.job_id in self._resource_loss_notified:
            return
        if not hasattr(controller, "emit_signal"):
            return
        summary = f"Missing {missing} {need.kind.value} resource(s); waiting for replacement"
        controller.emit_signal(  # type: ignore[attr-defined]
            kind=SignalKind.RESOURCE_LOST,
            summary=summary,
            decision={
                "options": ["wait_for_production", "use_alternative", "abort"],
                "default_if_timeout": "wait_for_production",
                "deadline_s": 3.0,
            },
        )
        self._resource_loss_notified.add(controller.job_id)

    def _route_actor_event(self, event: Event) -> None:
        if event.actor_id is None:
            return
        resource_id = f"actor:{event.actor_id}"
        matched_jobs = [
            controller
            for controller in self._jobs.values()
            if resource_id in controller.resources and not self._is_terminal_status(controller.status)
        ]
        routed_task_ids: set[str] = set()
        for controller in matched_jobs:
            self._deliver_event_to_job(controller, event)
            runtime = self._task_runtimes.get(controller.task_id)
            if runtime is not None and controller.task_id not in routed_task_ids:
                runtime.agent.push_event(event)
                routed_task_ids.add(controller.task_id)

        if event.type == EventType.UNIT_DIED and matched_jobs:
            for controller in matched_jobs:
                if hasattr(controller, "on_resource_revoked"):
                    controller.on_resource_revoked([resource_id])
                self.world_model.unbind_resource(resource_id)
            self._rebalance_resources()
        self._sync_world_runtime()

    def _broadcast_event(self, event: Event) -> None:
        for runtime in self._task_runtimes.values():
            if runtime.task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                continue
            runtime.agent.push_event(event)

    def _deliver_event_to_job(self, controller: BaseJob | _ManagedJob, event: Event) -> None:
        if hasattr(controller, "on_event"):
            controller.on_event(event)  # type: ignore[attr-defined]
        elif hasattr(controller, "handle_event"):
            controller.handle_event(event)  # type: ignore[attr-defined]

    _DEFEND_BASE_COOLDOWN_S = 10.0

    def _ensure_defend_base_task(self) -> Optional[Task]:
        # Return existing active task if one exists.
        for task in self.tasks.values():
            if task.raw_text == "defend_base" and task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                return task
        # Cooldown: don't create a new task if one was recently created (even if it failed).
        now = _now()
        if now - self._defend_base_last_created < self._DEFEND_BASE_COOLDOWN_S:
            return None
        self._defend_base_last_created = now
        return self.create_task("defend_base", TaskKind.MANAGED, 80)

    def _active_task_jobs(self, task_id: str, *, expert_type: Optional[str] = None) -> list[BaseJob | _ManagedJob]:
        jobs: list[BaseJob | _ManagedJob] = []
        for controller in self._jobs.values():
            if controller.task_id != task_id:
                continue
            if expert_type is not None and controller.expert_type != expert_type:
                continue
            if self._is_terminal_status(controller.status):
                continue
            jobs.append(controller)
        jobs.sort(key=lambda item: item.job_id)
        return jobs

    def _resolve_defend_base_target_position(self, event: Event) -> Optional[tuple[int, int]]:
        if event.position is not None:
            return (int(event.position[0]), int(event.position[1]))

        if event.actor_id is not None:
            actor = self.world_model.state.actors.get(event.actor_id)
            if actor is not None:
                return (int(actor.position[0]), int(actor.position[1]))

        buildings = self.world_model.find_actors(owner="self", category="building")
        if buildings:
            x = round(sum(actor.position[0] for actor in buildings) / len(buildings))
            y = round(sum(actor.position[1] for actor in buildings) / len(buildings))
            return (int(x), int(y))

        actors = self.world_model.find_actors(owner="self")
        if actors:
            x = round(sum(actor.position[0] for actor in actors) / len(actors))
            y = round(sum(actor.position[1] for actor in actors) / len(actors))
            return (int(x), int(y))

        return None

    def _ensure_immediate_defend_base_job(self, task: Task, event: Event) -> None:
        target_position = self._resolve_defend_base_target_position(event)
        if target_position is None:
            return

        existing_jobs = self._active_task_jobs(task.task_id, expert_type="CombatExpert")
        if existing_jobs:
            for controller in existing_jobs:
                current_target = getattr(controller.config, "target_position", None)
                if current_target != target_position:
                    controller.patch({"target_position": target_position})
            self._sync_world_runtime()
            return

        self.start_job(
            task.task_id,
            "CombatExpert",
            CombatJobConfig(
                target_position=target_position,
                engagement_mode=EngagementMode.HOLD,
                max_chase_distance=12,
                retreat_threshold=0.4,
            ),
        )

    def _handle_base_under_attack_auto_response(self, event: Event) -> None:
        task = self._ensure_defend_base_task()
        if task is None:
            return  # Cooldown active — suppress duplicate defend_base creation
        self._ensure_immediate_defend_base_job(task, event)

    def _apply_auto_response_rules(self, event: Event) -> None:
        for rule in self._auto_response_rules.get(event.type, []):
            rule.handler(event)

    def _push_player_notification(self, event: Event) -> None:
        content_map = {
            EventType.ENEMY_EXPANSION: "发现敌人在扩张",
            EventType.FRONTLINE_WEAK: "我方前线空虚",
            EventType.ECONOMY_SURPLUS: "经济充裕，可以考虑进攻",
        }
        self.player_notifications.append(
            {
                "type": event.type.value,
                "content": content_map.get(event.type, event.type.value),
                "data": dict(event.data),
                "timestamp": event.timestamp,
            }
        )

    @staticmethod
    def _is_terminal_status(status: JobStatus) -> bool:
        return status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}

    def _expire_pending_question(self, message_id: str, timestamp: float) -> None:
        pending = self._pending_questions.pop(message_id, None)
        if pending is None:
            return
        self._timed_out_questions.add(message_id)
        self._deliver_player_response(
            PlayerResponse(
                message_id=message_id,
                task_id=pending.message.task_id,
                answer=pending.default_option,
                timestamp=timestamp,
            )
        )

    def _deliver_player_response(self, response: PlayerResponse) -> None:
        self._delivered_player_responses.setdefault(response.task_id, []).append(response)
        runtime = self._task_runtimes.get(response.task_id)
        if runtime is None:
            return
        if hasattr(runtime.agent, "push_player_response"):
            runtime.agent.push_player_response(response)  # type: ignore[attr-defined]

    def _tool_start_job(self, task_id: str) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def handler(_: str, args: dict[str, Any]) -> dict[str, Any]:
            config = self._config_from_payload(args["expert_type"], args["config"])
            job = self.start_job(task_id, args["expert_type"], config)
            return {"job_id": job.job_id, "status": job.status.value}

        return handler

    async def _tool_patch_job(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": self.patch_job(args["job_id"], args["params"])}

    async def _tool_pause_job(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": self.pause_job(args["job_id"])}

    async def _tool_resume_job(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": self.resume_job(args["job_id"])}

    async def _tool_abort_job(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": self.abort_job(args["job_id"])}

    def _tool_complete_task(self, task_id: str) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def handler(_: str, args: dict[str, Any]) -> dict[str, Any]:
            return {"ok": self.complete_task(task_id, args["result"], args["summary"])}

        return handler

    async def _tool_create_constraint(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        constraint = Constraint(
            constraint_id=_gen_id("c_"),
            kind=args["kind"],
            scope=args["scope"],
            params=dict(args["params"]),
            enforcement=ConstraintEnforcement(args["enforcement"]),
        )
        self._constraints[constraint.constraint_id] = constraint
        self.world_model.set_constraint(constraint)
        self._sync_world_runtime()
        return {"constraint_id": constraint.constraint_id}

    async def _tool_remove_constraint(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        constraint_id = args["constraint_id"]
        removed = self._constraints.pop(constraint_id, None)
        if removed is not None:
            self.world_model.remove_constraint(constraint_id)
            self._sync_world_runtime()
        return {"ok": removed is not None}

    async def _tool_query_world(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        query_type = args["query_type"]
        mapping = {
            "my_actors": "my_actors",
            "enemy_actors": "enemy_actors",
            "enemy_bases": "find_actors",
            "economy_status": "economy",
            "map_control": "map",
            "threat_assessment": "world_summary",
        }
        target_query = mapping.get(query_type)
        if target_query is None:
            raise ValueError(f"Unsupported query_world type: {query_type}")
        params = dict(args.get("params") or {})
        if query_type == "enemy_bases":
            params.setdefault("owner", "enemy")
            params.setdefault("category", "building")
        result = self.world_model.query(target_query, params)
        return {"data": result}

    async def _tool_query_planner(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        world_state = {
            "world_summary": self.world_model.query("world_summary"),
            "economy": self.world_model.query("economy"),
            "production_queues": self.world_model.query("production_queues"),
            "my_actors": self.world_model.query("my_actors"),
            "enemy_actors": self.world_model.query("enemy_actors"),
        }
        return {"proposal": run_planner_query(args["planner_type"], args.get("params"), world_state)}

    async def _tool_cancel_tasks(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"count": self.cancel_tasks(args["filters"])}
