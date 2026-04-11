"""Kernel v1: deterministic Task / Job lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Callable, Optional, Protocol

from benchmark import span as bm_span
from experts.base import BaseJob, ExecutionExpert
from llm import LLMProvider
from logging_system import get_logger
from models import (
    Constraint,
    Event,
    EventType,
    ExpertConfig,
    ExpertSignal,
    Job,
    JobStatus,
    PlayerResponse,
    ResourceKind,
    ResourceNeed,
    SignalKind,
    Task,
    TaskKind,
    TaskMessage,
    TaskStatus,
    UnitReservation,
    UnitRequest,
)
from openra_state.data.dataset import (
    _CATEGORY_TO_ACTOR_CATEGORY,
    _UNIT_TO_QUEUE_TYPE,
    infer_unit_type_for_request,
    queue_type_for_unit_type,
)
from .unit_request_runtime import (
    build_active_reservation_payloads,
    build_unfulfilled_request_payloads,
    request_reason as unit_request_reason,
)
from .unit_request_state import (
    bind_actor_to_request_state,
    cancel_request_state,
    update_request_status_from_progress,
)
from .unit_request_bookkeeping import (
    build_unit_request_result,
    clear_request_bootstrap_refs,
    ensure_reservation_for_request,
    request_can_start,
    request_start_goal,
    reservation_for_request,
)
from .unit_request_bootstrap import (
    active_bootstrap_job_id,
    build_bootstrap_config,
    build_bootstrap_player_message,
    BootstrapStartOutcome,
    compute_bootstrap_reconcile_target,
    decide_bootstrap_start,
    record_bootstrap_started,
)
from .unit_request_matching import (
    hint_match_score,
    matching_idle_actors,
    sort_pending_requests,
)
from .unit_request_lifecycle import (
    build_capability_unfulfilled_event,
    build_unit_assigned_event,
    release_ready_task_requests,
    task_has_blocking_wait,
)
from .runtime_projection import (
    build_world_runtime_state,
)
from .task_coordination import (
    build_other_active_tasks,
    build_task_world_summary,
    prune_task_actor_group,
    set_task_actor_group,
    task_active_actor_ids as collect_task_active_actor_ids,
    task_has_running_actor_job as has_running_actor_job,
)
from .defend_base_auto_response import (
    ensure_defend_base_task,
    ensure_immediate_defend_base_job,
)
from .event_delivery import (
    append_player_notification,
    broadcast_event,
    deliver_player_response,
    route_actor_event,
)
from .task_runtime_ops import (
    maybe_start_agent,
    release_job_resources as release_job_runtime_resources,
    release_task_job_resources as release_task_runtime_job_resources,
    stop_task_runtime,
)
from .job_lifecycle import (
    abort_job as abort_job_runtime,
    patch_job as patch_job_runtime,
    pause_job as pause_job_runtime,
    require_job,
    resume_job as resume_job_runtime,
    start_job as start_job_runtime,
)
from .session_reset import (
    abort_and_release_all_jobs,
    clear_kernel_runtime_collections,
    stop_all_task_runtimes,
)
from .signal_delivery import route_expert_signal
from .task_creation import (
    create_task as create_task_runtime,
    ensure_capability_task as ensure_capability_task_runtime,
    inject_player_message as inject_player_message_runtime,
    is_direct_managed as is_direct_managed_runtime,
)
from .task_lifecycle import (
    cancel_task as cancel_task_runtime,
    cancel_tasks as cancel_tasks_runtime,
    close_pending_questions_for_task,
    complete_task as complete_task_runtime,
    task_matches_filters,
)
from .task_questions import PendingQuestionStore
from task_agent import AgentConfig, TaskAgent, TaskToolHandlers, ToolExecutor, WorldSummary
from world_model import WorldModel

slog = get_logger("kernel")

_URGENCY_WEIGHT: dict[str, int] = {
    "critical": 4, "high": 3, "medium": 2, "low": 1,
}


def _now() -> float:
    return time.time()


def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


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
    enable_capability_task: bool = True
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
        self.tick_interval: float = 1.0
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

    def do_tick(self) -> None:
        """No-op tick — _ManagedJob is a placeholder until a real Expert is attached."""
        pass

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
        self._question_store = PendingQuestionStore()
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
        result = create_task_runtime(
            raw_text=raw_text,
            kind=kind,
            priority=priority,
            info_subscriptions=info_subscriptions,
            skip_agent=skip_agent,
            task_seq=self._task_seq,
            tasks=self.tasks,
            task_runtimes=self._task_runtimes,
            direct_managed_tasks=self._direct_managed_tasks,
            task_agent_factory=self.task_agent_factory,
            build_tool_executor=self._build_tool_executor,
            jobs_provider=self.jobs_for_task,
            world_summary_provider=self._task_world_summary,
            runtime_factory=lambda task, agent, tool_executor: _TaskRuntime(
                task=task,
                agent=agent,
                tool_executor=tool_executor,
            ),
            maybe_start_agent=lambda runtime: maybe_start_agent(
                runtime,
                auto_start_agents=self.config.auto_start_agents,
            ),
            world_model=self.world_model,
            current_capability_task_id=lambda: self._capability_task_id,
            other_active_tasks_for=self._other_active_tasks_for,
            sync_world_runtime=self._sync_world_runtime,
            gen_id=_gen_id,
        )
        self._task_seq = result.task_seq
        return result.task

    def cancel_task(self, task_id: str) -> bool:
        return cancel_task_runtime(
            task_id=task_id,
            tasks=self.tasks,
            jobs=self._jobs,
            unit_requests=self._unit_requests,
            task_actor_groups=self._task_actor_groups,
            task_runtimes=self._task_runtimes,
            question_store=self._question_store,
            abort_job=self.abort_job,
            release_task_job_resources=self._release_task_job_resources,
            cancel_unit_request=self.cancel_unit_request,
            stop_task_runtime=stop_task_runtime,
            sync_world_runtime=self._sync_world_runtime,
            now=_now,
        )

    def cancel_tasks(self, filters: dict[str, Any]) -> int:
        return cancel_tasks_runtime(
            filters=filters,
            tasks=self.tasks,
            cancel_task_fn=self.cancel_task,
        )

    @property
    def capability_task_id(self) -> Optional[str]:
        """Return the task_id of the EconomyCapability, or None."""
        return self._capability_task_id

    def ensure_capability_task(self) -> Optional[str]:
        result = ensure_capability_task_runtime(
            enable_capability_task=self.config.enable_capability_task,
            capability_task_id=self._capability_task_id,
            tasks=self.tasks,
            create_task_fn=self.create_task,
        )
        self._capability_task_id = result.task_id
        self._capability_recent_inputs = result.capability_recent_inputs
        if result.created and result.task_id:
            self._sync_world_runtime()
            slog.info(
                "EconomyCapability created",
                event="capability_created",
                task_id=result.task_id,
            )
        return result.task_id

    def is_direct_managed(self, task_id: str) -> bool:
        """Return True if this task has no TaskAgent (skip_agent mode)."""
        return is_direct_managed_runtime(
            task_id,
            direct_managed_tasks=self._direct_managed_tasks,
        )

    def inject_player_message(self, task_id: str, text: str) -> bool:
        """Inject a player message into a running LLM-managed task's event queue."""
        return inject_player_message_runtime(
            task_id=task_id,
            text=text,
            tasks=self.tasks,
            task_runtimes=self._task_runtimes,
            direct_managed_tasks=self._direct_managed_tasks,
            capability_task_id=self._capability_task_id,
            capability_recent_inputs=self._capability_recent_inputs,
            sync_world_runtime=self._sync_world_runtime,
            now=_now,
        )

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
        unit_type, queue_type = infer_unit_type_for_request(req.category, req.hint)
        if unit_type is not None and queue_type is not None:
            self._ensure_reservation_for_request(req, unit_type)

        # Step 1: idle matching
        if self._try_fulfill_from_idle(req):
            update_request_status_from_progress(req)
            slog.info("Unit request fulfilled from idle", event="unit_request_fulfilled",
                      task_id=task_id, request_id=request_id, actor_ids=req.assigned_actor_ids)
            result = self._unit_request_result(req, status="fulfilled")
            result["actor_ids"] = list(req.assigned_actor_ids)
            return result

        # Partial idle match updates status
        update_request_status_from_progress(req)

        # Step 2: fast-path bootstrap production for remaining
        bootstrap_outcome = self._bootstrap_production_for_request(req)

        # Sync so Capability sees the new request in runtime_facts
        self._sync_world_runtime()

        # If fast-path couldn't handle it, notify Capability
        if bootstrap_outcome.notify_capability:
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
        bootstrap_job_id = active_bootstrap_job_id(req, reservation)
        if bootstrap_job_id:
            job = self._jobs.get(bootstrap_job_id)
            if job is not None and job.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}:
                self.abort_job(bootstrap_job_id)
        cancel_request_state(req, reservation, now=_now)
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
        result = build_unit_request_result(
            req,
            reservation=reservation,
            infer_unit_type=infer_unit_type_for_request,
        )
        result["status"] = status
        return result

    # --- Unit request internals ---

    def _ensure_reservation_for_request(self, req: UnitRequest, unit_type: str) -> UnitReservation:
        return ensure_reservation_for_request(
            req,
            unit_type,
            request_reservations=self._request_reservations,
            unit_reservations=self._unit_reservations,
            gen_id=_gen_id,
        )

    def _reservation_for_request(self, req: UnitRequest) -> Optional[UnitReservation]:
        return reservation_for_request(
            req,
            request_reservations=self._request_reservations,
            unit_reservations=self._unit_reservations,
        )

    def _clear_request_bootstrap_refs(
        self,
        req: UnitRequest,
        reservation: Optional[UnitReservation],
    ) -> None:
        clear_request_bootstrap_refs(
            req,
            reservation,
            now=_now,
        )

    def _reconcile_request_bootstrap(self, req: UnitRequest) -> None:
        """Shrink or clear internal bootstrap production after new idle assignments.

        This keeps future-unit ownership closer to reality when a request that already
        started fast-path bootstrap later picks up live idle actors.
        """
        reservation = self._reservation_for_request(req)
        bootstrap_job_id = active_bootstrap_job_id(req, reservation)
        if not bootstrap_job_id:
            return
        controller = self._jobs.get(bootstrap_job_id)
        if controller is None or self._is_terminal_status(controller.status):
            self._clear_request_bootstrap_refs(req, reservation)
            return
        reconcile_target = compute_bootstrap_reconcile_target(req, controller)
        if reconcile_target is None:
            return

        if reconcile_target.clear_job:
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
                desired_remaining=reconcile_target.desired_remaining,
                previous_target=reconcile_target.current_target,
                new_target=0,
                mode="clear",
            )
            return

        controller.patch({"count": reconcile_target.new_target})
        if reservation is not None:
            reservation.updated_at = _now()
        slog.info(
            "Bootstrap job reconciled after idle fulfillment",
            event="bootstrap_reconciled",
            request_id=req.request_id,
            reservation_id=reservation.reservation_id if reservation is not None else "",
            job_id=bootstrap_job_id,
            desired_remaining=reconcile_target.desired_remaining,
            previous_target=reconcile_target.current_target,
            new_target=reconcile_target.new_target,
            issued_count=reconcile_target.issued_count,
            produced_count=reconcile_target.produced_count,
            mode="shrink",
        )

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
        idle.sort(key=lambda a: hint_match_score(a, req.hint), reverse=True)
        to_bind = idle[:req.count - req.fulfilled]
        for actor in to_bind:
            self._bind_actor_to_request(req, actor)
        return req.fulfilled >= req.count

    def _bind_actor_to_request(self, req: UnitRequest, actor: Any, *, produced: bool = False) -> None:
        """Bind an actor to a unit request."""
        resource_id = f"actor:{actor.actor_id}"
        self.world_model.bind_resource(resource_id, f"req:{req.request_id}")
        reservation = self._reservation_for_request(req)
        bind_actor_to_request_state(
            req,
            reservation,
            actor_id=actor.actor_id,
            produced=produced,
            now=_now,
        )

    @staticmethod
    def _request_start_goal(req: UnitRequest) -> int:
        return request_start_goal(req)

    def _request_can_start(self, req: UnitRequest) -> bool:
        return request_can_start(req)

    def _task_has_blocking_wait(self, task_id: str) -> bool:
        return task_has_blocking_wait(
            self._unit_requests.values(),
            task_id,
            request_can_start=self._request_can_start,
        )

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

    def _request_reason(self, req: UnitRequest, reservation: Optional[UnitReservation], unit_type: str) -> str:
        return unit_request_reason(
            req,
            reservation,
            unit_type,
            production_readiness_for=lambda name, queue_type: self.world_model.production_readiness_for(
                name,
                queue_type=queue_type,
            ),
        )

    def _bootstrap_production_for_request(self, req: UnitRequest) -> BootstrapStartOutcome:
        """Start a direct EconomyJob for remaining unfulfilled count."""
        decision = decide_bootstrap_start(
            req,
            infer_unit_type=infer_unit_type_for_request,
            production_readiness_for=lambda unit_type, queue_type: self.world_model.production_readiness_for(
                unit_type,
                queue_type=queue_type,
            ),
        )
        if decision.remaining <= 0:
            return BootstrapStartOutcome(decision=decision, started=False)
        if decision.unit_type is None or decision.queue_type is None:
            return BootstrapStartOutcome(decision=decision, started=False)
        unit_type = decision.unit_type
        queue_type = decision.queue_type
        reservation = self._ensure_reservation_for_request(req, unit_type)
        reservation.updated_at = _now()
        if not decision.can_issue_now:
            return BootstrapStartOutcome(decision=decision, started=False)

        bootstrap_task_id = self.ensure_capability_task()
        capability_task = self.tasks.get(bootstrap_task_id)
        if capability_task is None or capability_task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING}:
            return BootstrapStartOutcome(decision=decision, started=False)

        # Start shared production on the capability task when available so
        # requesters remain consumers of units instead of accidental owners of
        # EconomyExpert jobs.
        config = build_bootstrap_config(
            req,
            unit_type=unit_type,
            queue_type=queue_type,
            reservation_id=reservation.reservation_id,
        )
        try:
            job = self.start_job(bootstrap_task_id, "EconomyExpert", config)
            record_bootstrap_started(
                req,
                reservation,
                job_id=job.job_id,
                task_id=bootstrap_task_id,
                now=_now,
            )
            slog.info("Bootstrap production for request", event="bootstrap_production",
                      request_id=req.request_id, unit_type=unit_type, count=decision.remaining,
                      job_id=job.job_id, bootstrap_task_id=bootstrap_task_id)
        except Exception as exc:
            slog.warning("Bootstrap production failed", event="bootstrap_production_failed",
                         request_id=req.request_id, error=str(exc))
            return BootstrapStartOutcome(decision=decision, started=False)

        # Notify Capability of the fast-path production
        if self._capability_task_id:
            self.inject_player_message(
                self._capability_task_id,
                build_bootstrap_player_message(req, unit_type=unit_type),
            )
        return BootstrapStartOutcome(decision=decision, started=True)

    def _notify_capability_unfulfilled(self, req: UnitRequest) -> None:
        """Push UNIT_REQUEST_UNFULFILLED event to wake Capability."""
        if not self._capability_task_id:
            return
        runtime = self._task_runtimes.get(self._capability_task_id)
        if runtime is None:
            return
        event = build_capability_unfulfilled_event(req)
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
        runtime_dirty = False

        pending = sort_pending_requests(
            [r for r in self._unit_requests.values() if r.status in ("pending", "partial")],
            idle,
            category_to_actor_category=_CATEGORY_TO_ACTOR_CATEGORY,
            urgency_weight=_URGENCY_WEIGHT,
            task_priority_for=lambda task_id: self.tasks[task_id].priority if task_id in self.tasks else 0,
            request_start_goal=self._request_start_goal,
        )
        if not pending:
            return

        for req in pending:
            remaining = req.count - req.fulfilled
            if remaining <= 0:
                continue
            if req.category == "building":
                continue
            matched = matching_idle_actors(
                req,
                idle,
                category_to_actor_category=_CATEGORY_TO_ACTOR_CATEGORY,
            )
            matched.sort(key=lambda a: hint_match_score(a, req.hint), reverse=True)
            for actor in matched[:remaining]:
                # These actors came from the live idle pool, not from an explicit
                # produced-unit handoff path.
                self._bind_actor_to_request(req, actor, produced=False)
                idle.remove(actor)
                runtime_dirty = True

            update_request_status_from_progress(req)
            self._reconcile_request_bootstrap(req)
            self._wake_waiting_agent(req.task_id)

            if not idle:
                break
        if runtime_dirty:
            self._sync_world_runtime()

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
        assigned_ids, fully_fulfilled = release_ready_task_requests(
            self._unit_requests.values(),
            task_id,
            reservation_for_request=self._reservation_for_request,
            request_can_start=self._request_can_start,
            handoff_request_assignments=self._handoff_request_assignments,
            now=_now,
        )
        if not assigned_ids:
            return
        event = build_unit_assigned_event(
            assigned_ids=assigned_ids,
            fully_fulfilled=fully_fulfilled,
        )
        if self._agent_is_suspended(runtime.agent):
            runtime.agent.resume_with_event(event)
        else:
            runtime.agent.push_event(event)
        self._sync_world_runtime()
        slog.info("Agent woken after request fulfillment",
                  event="agent_woken_requests_fulfilled", task_id=task_id,
                  actor_ids=assigned_ids, fully_fulfilled=fully_fulfilled)

    def complete_task(self, task_id: str, result: str, summary: str) -> bool:
        return complete_task_runtime(
            task_id=task_id,
            result=result,
            summary=summary,
            tasks=self.tasks,
            jobs=self._jobs,
            task_messages=self.task_messages,
            task_actor_groups=self._task_actor_groups,
            task_runtimes=self._task_runtimes,
            question_store=self._question_store,
            abort_job=self.abort_job,
            release_task_job_resources=self._release_task_job_resources,
            stop_task_runtime=stop_task_runtime,
            sync_world_runtime=self._sync_world_runtime,
            now=_now,
            gen_id=_gen_id,
        )

    def start_job(self, task_id: str, expert_type: str, config: ExpertConfig) -> Job:
        return start_job_runtime(
            task_id=task_id,
            expert_type=expert_type,
            config=config,
            tasks=self.tasks,
            jobs=self._jobs,
            resource_needs=self._resource_needs,
            make_job_controller=self._make_job_controller,
            now=_now,
            rebalance_resources=self._rebalance_resources,
            sync_world_runtime=self._sync_world_runtime,
        )

    def abort_job(self, job_id: str) -> bool:
        return abort_job_runtime(
            job_id=job_id,
            jobs=self._jobs,
            resource_loss_notified=self._resource_loss_notified,
            release_job_resources=self._release_job_resources,
            rebalance_resources=self._rebalance_resources,
            sync_world_runtime=self._sync_world_runtime,
        )

    def patch_job(self, job_id: str, params: dict[str, Any]) -> bool:
        return patch_job_runtime(
            job_id=job_id,
            params=params,
            jobs=self._jobs,
            resource_needs=self._resource_needs,
            rebalance_resources=self._rebalance_resources,
            sync_world_runtime=self._sync_world_runtime,
        )

    def pause_job(self, job_id: str) -> bool:
        return pause_job_runtime(
            job_id=job_id,
            jobs=self._jobs,
            is_terminal_status=self._is_terminal_status,
            sync_world_runtime=self._sync_world_runtime,
        )

    def resume_job(self, job_id: str) -> bool:
        return resume_job_runtime(
            job_id=job_id,
            jobs=self._jobs,
            is_terminal_status=self._is_terminal_status,
            rebalance_resources=self._rebalance_resources,
            sync_world_runtime=self._sync_world_runtime,
        )

    def route_event(self, event: Event) -> None:
        with bm_span("tool_exec", name=f"kernel:route_event:{event.type.value}"):
            slog.info("Kernel routing event", event="event_routed", event_type=event.type.value, actor_id=event.actor_id, position=event.position, data=event.data)
            self._apply_auto_response_rules(event)
            if event.type == EventType.GAME_RESET:
                self._handle_game_reset(event)
                return
            if event.type in {EventType.UNIT_DIED, EventType.UNIT_DAMAGED}:
                route_actor_event(
                    event,
                    jobs=self._jobs,
                    task_runtimes=self._task_runtimes,
                    world_model=self.world_model,
                    is_terminal_job_status=self._is_terminal_status,
                    rebalance_resources=self._rebalance_resources,
                    sync_world_runtime=self._sync_world_runtime,
                )
                return
            if event.type in {EventType.ENEMY_DISCOVERED, EventType.STRUCTURE_LOST}:
                broadcast_event(event, task_runtimes=self._task_runtimes)
                return
            if event.type == EventType.BASE_UNDER_ATTACK:
                broadcast_event(event, task_runtimes=self._task_runtimes)
                return
            if event.type == EventType.LOW_POWER:
                # Push to Capability so it can build a power plant
                if self._capability_task_id:
                    runtime = self._task_runtimes.get(self._capability_task_id)
                    if runtime is not None:
                        runtime.agent.push_event(event)
                return
            if event.type in {EventType.ENEMY_EXPANSION, EventType.FRONTLINE_WEAK, EventType.ECONOMY_SURPLUS}:
                append_player_notification(self.player_notifications, event)
                return
            if event.type == EventType.PRODUCTION_COMPLETE:
                self._rebalance_resources()
                self._fulfill_unit_requests()
                return
            return None

    def _handle_game_reset(self, event: Event) -> None:
        stop_all_task_runtimes(
            self._task_runtimes,
            stop_task_runtime_fn=stop_task_runtime,
        )
        clear_kernel_runtime_collections(
            tasks=self.tasks,
            task_runtimes=self._task_runtimes,
            jobs=self._jobs,
            constraints=self._constraints,
            resource_needs=self._resource_needs,
            resource_loss_notified=self._resource_loss_notified,
            player_notifications=self.player_notifications,
            task_messages=self.task_messages,
            reset_questions=self._question_store.reset,
            delivered_player_responses=self._delivered_player_responses,
            unit_requests=self._unit_requests,
            unit_reservations=self._unit_reservations,
            request_reservations=self._request_reservations,
            task_actor_groups=self._task_actor_groups,
            direct_managed_tasks=self._direct_managed_tasks,
            capability_recent_inputs=self._capability_recent_inputs,
            clear_player_notifications=False,
            clear_task_messages=True,
        )
        self._capability_task_id = None
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
        slog.info("Kernel routed expert signal", event="signal_routed", task_id=signal.task_id, job_id=signal.job_id, signal_kind=signal.kind.value, result=signal.result)
        route_expert_signal(
            signal,
            tasks=self.tasks,
            task_runtimes=self._task_runtimes,
            is_direct_managed=self.is_direct_managed,
            register_task_message=self.register_task_message,
            complete_task=self.complete_task,
            gen_message_id=_gen_id,
        )

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

    def runtime_state(self) -> dict[str, Any]:
        """Return the latest runtime projection synchronized into WorldModel."""
        state = self.world_model.runtime_state()
        return dict(state or {})

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
        return list(self._question_store.list_pending_questions())

    def reset_session(self) -> None:
        stop_all_task_runtimes(
            self._task_runtimes,
            stop_task_runtime_fn=stop_task_runtime,
        )
        abort_and_release_all_jobs(
            self._jobs,
            is_terminal_status=self._is_terminal_status,
            release_job_resources_fn=self._release_job_resources,
        )
        clear_kernel_runtime_collections(
            tasks=self.tasks,
            task_runtimes=self._task_runtimes,
            jobs=self._jobs,
            constraints=self._constraints,
            resource_needs=self._resource_needs,
            resource_loss_notified=self._resource_loss_notified,
            player_notifications=self.player_notifications,
            task_messages=self.task_messages,
            reset_questions=self._question_store.reset,
            delivered_player_responses=self._delivered_player_responses,
            unit_requests=self._unit_requests,
            unit_reservations=self._unit_reservations,
            request_reservations=self._request_reservations,
            task_actor_groups=self._task_actor_groups,
            direct_managed_tasks=self._direct_managed_tasks,
            capability_recent_inputs=self._capability_recent_inputs,
            clear_player_notifications=True,
            clear_task_messages=True,
        )
        self._capability_task_id = None
        self._sync_world_runtime()
        self.ensure_capability_task()

    def register_task_message(self, message: TaskMessage) -> bool:
        with bm_span("tool_exec", name=f"kernel:register_task_message:{message.type.value}"):
            task = self.tasks.get(message.task_id)
            if task is None or task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                return False
            self.task_messages.append(message)
            slog.info("Task message registered", event="task_message_registered", task_id=message.task_id, message_id=message.message_id, message_type=message.type.value, priority=message.priority)
            self._question_store.register(message)
            return True

    def cancel_pending_question(self, message_id: str) -> bool:
        return self._question_store.cancel(message_id)

    def submit_player_response(
        self,
        response: PlayerResponse,
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        with bm_span("tool_exec", name="kernel:submit_player_response"):
            timestamp = _now() if now is None else now
            result = self._question_store.submit(response, timestamp)
            if result.delivered_response is not None:
                deliver_player_response(
                    self._delivered_player_responses,
                    self._task_runtimes,
                    result.delivered_response,
                )
            return result.to_payload()

    def tick(self, *, now: Optional[float] = None) -> int:
        with bm_span("tool_exec", name="kernel:tick"):
            timestamp = _now() if now is None else now
            expired_responses = self._question_store.expire_due(timestamp)
            for response in expired_responses:
                deliver_player_response(
                    self._delivered_player_responses,
                    self._task_runtimes,
                    response,
                )
            return len(expired_responses)

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
        return require_job(job_id, jobs=self._jobs)

    def _release_job_resources(self, controller: BaseJob | _ManagedJob) -> None:
        release_job_runtime_resources(
            controller,
            unbind_resource=self.world_model.unbind_resource,
        )

    def _release_task_job_resources(self, task_id: str) -> None:
        release_task_runtime_job_resources(
            self._jobs,
            task_id,
            release_job_resources_fn=self._release_job_resources,
            on_job_released=self._resource_loss_notified.discard,
        )

    def _sync_world_runtime(self) -> None:
        runtime_state = build_world_runtime_state(
            tasks=self.tasks.values(),
            controllers=self._jobs.values(),
            constraints=self._constraints.values(),
            resource_bindings=self.world_model.resource_bindings,
            active_actor_ids_for=self.task_active_actor_ids,
            unit_requests=self._unit_requests.values(),
            reservation_for_request=self._reservation_for_request,
            request_reservation_id=lambda request_id: self._request_reservations.get(request_id) or "",
            production_readiness_for=lambda unit_type, queue_type: self.world_model.production_readiness_for(
                unit_type,
                queue_type=queue_type,
            ),
            capability_task=(
                self.tasks.get(self._capability_task_id)
                if self._capability_task_id
                else None
            ),
            capability_task_id=self._capability_task_id,
            capability_recent_inputs=self._capability_recent_inputs,
            unit_reservations=self._unit_reservations.values(),
            build_unfulfilled_request_payloads=build_unfulfilled_request_payloads,
            build_active_reservation_payloads=build_active_reservation_payloads,
            requests_by_id=self._unit_requests,
        )
        unfulfilled = runtime_state["unfulfilled_requests"]
        if unfulfilled:
            slog.info("Syncing unfulfilled requests", event="sync_unfulfilled",
                      count=len(unfulfilled),
                      requests=[r["request_id"] for r in unfulfilled])
        self.world_model.set_runtime_state(**runtime_state)

    def _set_task_actor_group(self, task_id: str, actor_ids: list[int]) -> None:
        set_task_actor_group(
            self._task_actor_groups,
            world_model=self.world_model,
            task_id=task_id,
            actor_ids=actor_ids,
        )

    def _prune_task_actor_group(self, task_id: str) -> None:
        prune_task_actor_group(
            self._task_actor_groups,
            world_model=self.world_model,
            task_id=task_id,
        )

    def task_active_actor_ids(self, task_id: str) -> list[int]:
        return collect_task_active_actor_ids(
            self._task_actor_groups,
            world_model=self.world_model,
            task_id=task_id,
        )

    def task_has_running_actor_job(self, task_id: str) -> bool:
        return has_running_actor_job(self._jobs, task_id=task_id)

    def _task_world_summary(self) -> WorldSummary:
        return build_task_world_summary(
            self.world_model,
            now=_now,
        )

    def _other_active_tasks_for(self, task_id: str) -> list[dict]:
        return build_other_active_tasks(
            task_id,
            tasks=self.tasks,
            jobs=self._jobs,
            task_messages=self.task_messages,
            is_terminal_job_status=self._is_terminal_status,
        )

    def _constraints_for_scope(self, scope: str) -> list[Constraint]:
        return [constraint for constraint in self._constraints.values() if constraint.active and constraint.scope == scope]

    def _close_pending_questions_for_task(self, task_id: str) -> None:
        close_pending_questions_for_task(self._question_store, task_id)

    def _task_matches_filters(self, task: Task, filters: dict[str, Any]) -> bool:
        return task_matches_filters(task, filters)

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

    _DEFEND_BASE_COOLDOWN_S = 10.0

    def _handle_base_under_attack_auto_response(self, event: Event) -> None:
        task, last_created = ensure_defend_base_task(
            self.tasks.values(),
            last_created=self._defend_base_last_created,
            now=_now(),
            cooldown_s=self._DEFEND_BASE_COOLDOWN_S,
            create_task=self.create_task,
        )
        self._defend_base_last_created = last_created
        if task is None:
            return  # Cooldown active — suppress duplicate defend_base creation
        ensure_immediate_defend_base_job(
            task,
            event,
            world_model=self.world_model,
            jobs=self._jobs,
            is_terminal_status=self._is_terminal_status,
            start_job=self.start_job,
            sync_world_runtime=self._sync_world_runtime,
        )

    def _apply_auto_response_rules(self, event: Event) -> None:
        for rule in self._auto_response_rules.get(event.type, []):
            rule.handler(event)

    @staticmethod
    def _is_terminal_status(status: JobStatus) -> bool:
        return status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.ABORTED}
