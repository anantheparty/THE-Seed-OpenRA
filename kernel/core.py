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
from .unit_request_entry import (
    register_unit_request as register_unit_request_runtime,
    try_fulfill_from_idle as try_fulfill_from_idle_runtime,
)
from .unit_request_fulfillment import (
    agent_is_suspended as agent_is_suspended_runtime,
    fulfill_unit_requests as fulfill_unit_requests_runtime,
    suspend_agent_for_requests as suspend_agent_for_requests_runtime,
    wake_waiting_agent as wake_waiting_agent_runtime,
)
from .unit_request_bootstrap import (
    active_bootstrap_job_id,
    bootstrap_production_for_request,
    BootstrapStartOutcome,
    reconcile_request_bootstrap,
)
from .unit_request_matching import hint_match_score
from .unit_request_lifecycle import (
    build_capability_unfulfilled_event,
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
from .event_orchestration import (
    handle_game_reset as handle_game_reset_runtime,
    route_runtime_event,
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
from .resource_assignment import (
    actor_matches_need as actor_matches_need_runtime,
    claim_resource as claim_resource_runtime,
    find_preemptable_resource as find_preemptable_resource_runtime,
    find_unbound_resource as find_unbound_resource_runtime,
    grant_resource as grant_resource_runtime,
    notify_resource_loss as notify_resource_loss_runtime,
    preempt_resource as preempt_resource_runtime,
    rebalance_resources as rebalance_resources_runtime,
    resource_matches_need as resource_matches_need_runtime,
    resources_for_need as resources_for_need_runtime,
)
from .session_reset import (
    abort_and_release_all_jobs,
    clear_kernel_runtime_collections,
    reset_kernel_session,
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
from .player_interaction import (
    push_player_notification as push_player_notification_runtime,
    register_task_message as register_task_message_runtime,
    submit_player_response as submit_player_response_runtime,
    tick_question_timeouts,
)
from .query_views import (
    active_jobs as active_jobs_runtime,
    get_task_agent as get_task_agent_runtime,
    jobs_for_task as jobs_for_task_runtime,
    list_jobs as list_jobs_runtime,
    list_player_notifications as list_player_notifications_runtime,
    list_task_messages as list_task_messages_runtime,
    list_tasks as list_tasks_runtime,
    runtime_state as runtime_state_view,
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

    def emit_signal(
        self,
        *,
        kind: SignalKind,
        summary: str,
        world_delta: Optional[dict[str, Any]] = None,
        expert_state: Optional[dict[str, Any]] = None,
        result: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        decision: Optional[dict[str, Any]] = None,
    ) -> None:
        self._signal_callback(
            ExpertSignal(
                task_id=self.task_id,
                job_id=self.job_id,
                kind=kind,
                summary=summary,
                world_delta=world_delta or {},
                expert_state=expert_state or {},
                result=result,
                data=data,
                decision=decision,
            )
        )
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

    def create_task(
        self,
        raw_text: str,
        kind: TaskKind | str,
        priority: int,
        info_subscriptions: list | None = None,
        *,
        skip_agent: bool = False,
        is_capability: bool = False,
    ) -> Task:
        result = create_task_runtime(
            raw_text=raw_text,
            kind=kind,
            priority=priority,
            is_capability=is_capability,
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

    def record_capability_note(self, text: str) -> bool:
        """Append capability-local note/history without waking the agent as a new player message."""
        if not self._capability_task_id:
            return False
        task = self.tasks.get(self._capability_task_id)
        if task is None or task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING):
            return False
        self._capability_recent_inputs.append({"text": text, "timestamp": _now()})
        del self._capability_recent_inputs[:-5]
        self._sync_world_runtime()
        slog.info(
            "Capability note recorded",
            event="capability_note_recorded",
            task_id=self._capability_task_id,
            text=text[:80],
        )
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
        return register_unit_request_runtime(
            task_id=task_id,
            category=category,
            count=count,
            urgency=urgency,
            hint=hint,
            blocking=blocking,
            min_start_package=min_start_package,
            tasks=self.tasks,
            unit_requests=self._unit_requests,
            infer_unit_type_for_request=infer_unit_type_for_request,
            ensure_reservation_for_request=self._ensure_reservation_for_request,
            reservation_for_request=self._reservation_for_request,
            try_fulfill_from_idle=self._try_fulfill_from_idle,
            update_request_status_from_progress=update_request_status_from_progress,
            request_can_start=self._request_can_start,
            handoff_request_assignments=self._handoff_request_assignments,
            bootstrap_production_for_request=self._bootstrap_production_for_request,
            sync_world_runtime=self._sync_world_runtime,
            notify_capability_unfulfilled=self._notify_capability_unfulfilled,
            suspend_agent_for_requests=self._suspend_agent_for_requests,
            unit_request_result=lambda req, status: self._unit_request_result(req, status=status),
            gen_id=_gen_id,
            now=_now,
        )

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
        slog.info(
            "Unit request cancelled",
            event="unit_request_cancelled",
            task_id=req.task_id,
            request_id=req.request_id,
            reservation_id=reservation.reservation_id if reservation is not None else "",
            remaining_count=max(req.count - req.fulfilled, 0),
            fulfilled=req.fulfilled,
            reservation_status=reservation.status.value if reservation is not None else "",
            bootstrap_job_id=bootstrap_job_id or "",
        )
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

    def _clear_bootstrap_refs_for_job(self, job_id: str) -> None:
        for req in self._unit_requests.values():
            reservation = self._reservation_for_request(req)
            if active_bootstrap_job_id(req, reservation) != job_id:
                continue
            self._clear_request_bootstrap_refs(req, reservation)

    def _reconcile_request_bootstrap(self, req: UnitRequest) -> None:
        reconcile_request_bootstrap(
            req,
            reservation_for_request=self._reservation_for_request,
            jobs=self._jobs,
            is_terminal_status=self._is_terminal_status,
            clear_request_bootstrap_refs=self._clear_request_bootstrap_refs,
            release_job_resources=self._release_job_resources,
            resource_loss_notified=self._resource_loss_notified,
            now=_now,
        )

    def _try_fulfill_from_idle(self, req: UnitRequest) -> bool:
        return try_fulfill_from_idle_runtime(
            req,
            world_model=self.world_model,
            category_to_actor_category=_CATEGORY_TO_ACTOR_CATEGORY,
            hint_match_score=hint_match_score,
            bind_actor_to_request=lambda request, actor: self._bind_actor_to_request(
                request,
                actor,
            ),
        )

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

    def _consume_request_linked_signal(self, signal: ExpertSignal) -> bool:
        data = signal.data or {}
        request_id = str(data.get("request_id") or "")
        actor_id = data.get("actor_id")
        if not request_id or actor_id is None:
            return False
        req = self._unit_requests.get(request_id)
        if req is None or req.status in {"fulfilled", "cancelled"}:
            return False
        try:
            actor_id_int = int(actor_id)
        except (TypeError, ValueError):
            return False
        if actor_id_int in req.assigned_actor_ids:
            return False
        actors = self.world_model.find_actors(owner="self", actor_ids=[actor_id_int], unbound_only=False)
        if not actors:
            return False
        actor = actors[0]
        if not bool(getattr(actor, "is_idle", False)):
            return False
        resource_id = f"actor:{actor_id_int}"
        existing_owner = self.world_model.resource_bindings.get(resource_id)
        if existing_owner and existing_owner != f"req:{req.request_id}":
            holder = self._jobs.get(existing_owner)
            if holder is None:
                return False
            self._preempt_resource(holder, resource_id)
        self._bind_actor_to_request(req, actor, produced=True)
        update_request_status_from_progress(req)
        self._reconcile_request_bootstrap(req)
        self._wake_waiting_agent(req.task_id)
        self._sync_world_runtime()
        slog.info(
            "Request-linked production actor credited",
            event="unit_request_produced_actor_bound",
            request_id=req.request_id,
            task_id=req.task_id,
            actor_id=actor_id_int,
            job_id=signal.job_id,
            signal_kind=signal.kind.value,
        )
        return True

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
        return agent_is_suspended_runtime(agent)

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
        return bootstrap_production_for_request(
            req,
            infer_unit_type=infer_unit_type_for_request,
            production_readiness_for=lambda unit_type, queue_type: self.world_model.production_readiness_for(
                unit_type,
                queue_type=queue_type,
            ),
            ensure_reservation_for_request=self._ensure_reservation_for_request,
            ensure_capability_task=self.ensure_capability_task,
            tasks=self.tasks,
            start_job=self.start_job,
            inject_player_message=self.inject_player_message,
            now=_now,
        )

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
        fulfill_unit_requests_runtime(
            unit_requests=self._unit_requests,
            world_model=self.world_model,
            category_to_actor_category=_CATEGORY_TO_ACTOR_CATEGORY,
            urgency_weight=_URGENCY_WEIGHT,
            task_priority_for=lambda task_id: self.tasks[task_id].priority if task_id in self.tasks else 0,
            request_start_goal=self._request_start_goal,
            bind_actor_to_request=lambda req, actor: self._bind_actor_to_request(
                req,
                actor,
                produced=False,
            ),
            reconcile_request_bootstrap=self._reconcile_request_bootstrap,
            wake_waiting_agent=self._wake_waiting_agent,
            sync_world_runtime=self._sync_world_runtime,
        )

    def _suspend_agent_for_requests(self, task_id: str) -> None:
        suspend_agent_for_requests_runtime(
            task_id,
            task_has_blocking_wait=self._task_has_blocking_wait,
            task_runtimes=self._task_runtimes,
        )

    def _wake_waiting_agent(self, task_id: str) -> None:
        wake_waiting_agent_runtime(
            task_id,
            task_has_blocking_wait=self._task_has_blocking_wait,
            task_runtimes=self._task_runtimes,
            unit_requests=self._unit_requests.values(),
            reservation_for_request=self._reservation_for_request,
            request_can_start=self._request_can_start,
            handoff_request_assignments=self._handoff_request_assignments,
            now=_now,
            sync_world_runtime=self._sync_world_runtime,
        )

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
        aborted = abort_job_runtime(
            job_id=job_id,
            jobs=self._jobs,
            resource_loss_notified=self._resource_loss_notified,
            release_job_resources=self._release_job_resources,
            rebalance_resources=self._rebalance_resources,
            sync_world_runtime=lambda: None,
        )
        if not aborted:
            return False
        self._clear_bootstrap_refs_for_job(job_id)
        self._sync_world_runtime()
        return True

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
            route_runtime_event(
                event,
                apply_auto_response_rules=self._apply_auto_response_rules,
                handle_game_reset=self._handle_game_reset,
                jobs=self._jobs,
                task_runtimes=self._task_runtimes,
                world_model=self.world_model,
                is_terminal_job_status=self._is_terminal_status,
                rebalance_resources=self._rebalance_resources,
                sync_world_runtime=self._sync_world_runtime,
                capability_task_id=self._capability_task_id,
                player_notifications=self.player_notifications,
                fulfill_unit_requests=self._fulfill_unit_requests,
            )
            return None

    def _handle_game_reset(self, event: Event) -> None:
        handle_game_reset_runtime(
            event,
            task_runtimes=self._task_runtimes,
            tasks=self.tasks,
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
            stop_task_runtime_fn=stop_task_runtime,
            set_capability_task_id=lambda value: setattr(self, "_capability_task_id", value),
            set_runtime_state=self.world_model.set_runtime_state,
            push_player_notification=self.push_player_notification,
            ensure_capability_task=self.ensure_capability_task,
        )

    def route_events(self, events: list[Event]) -> None:
        with bm_span("tool_exec", name="kernel:route_events", metadata={"count": len(events)}):
            for event in events:
                self.route_event(event)

    def route_signal(self, signal: ExpertSignal) -> None:
        slog.info("Kernel routed expert signal", event="signal_routed", task_id=signal.task_id, job_id=signal.job_id, signal_kind=signal.kind.value, result=signal.result)
        self._consume_request_linked_signal(signal)
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
        return get_task_agent_runtime(task_id, task_runtimes=self._task_runtimes)

    def jobs_for_task(self, task_id: str) -> list[Job]:
        return jobs_for_task_runtime(task_id, jobs=self._jobs.values())

    def active_jobs(self) -> tuple[BaseJob | _ManagedJob, ...]:
        """Return a read-only snapshot of non-terminal job controllers."""
        return active_jobs_runtime(
            jobs=self._jobs.values(),
            is_terminal_status=self._is_terminal_status,
        )

    def list_tasks(self) -> list[Task]:
        return list_tasks_runtime(tasks=self.tasks.values())

    def list_jobs(self) -> list[Job]:
        return list_jobs_runtime(jobs=self._jobs.values())

    def list_player_notifications(self) -> list[dict[str, Any]]:
        return list_player_notifications_runtime(
            player_notifications=self.player_notifications,
        )

    def runtime_state(self) -> dict[str, Any]:
        """Return the latest runtime projection synchronized into WorldModel."""
        return runtime_state_view(world_model=self.world_model)

    def push_player_notification(
        self,
        notification_type: str,
        content: str,
        *,
        data: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        push_player_notification_runtime(
            notification_type=notification_type,
            content=content,
            data=data,
            timestamp=timestamp,
            player_notifications=self.player_notifications,
            now=_now,
        )

    def list_task_messages(self, task_id: Optional[str] = None) -> list[TaskMessage]:
        return list_task_messages_runtime(
            task_id,
            task_messages=self.task_messages,
        )

    def list_pending_questions(self) -> list[dict[str, Any]]:
        return list(self._question_store.list_pending_questions())

    def reset_session(self) -> None:
        reset_kernel_session(
            task_runtimes=self._task_runtimes,
            jobs=self._jobs,
            tasks=self.tasks,
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
            stop_task_runtime_fn=stop_task_runtime,
            is_terminal_status=self._is_terminal_status,
            release_job_resources_fn=self._release_job_resources,
            set_capability_task_id=lambda task_id: setattr(self, "_capability_task_id", task_id),
            sync_world_runtime=self._sync_world_runtime,
            ensure_capability_task=self.ensure_capability_task,
        )

    def register_task_message(self, message: TaskMessage) -> bool:
        return register_task_message_runtime(
            message,
            tasks=self.tasks,
            task_messages=self.task_messages,
            question_store=self._question_store,
        )

    def cancel_pending_question(self, message_id: str) -> bool:
        return self._question_store.cancel(message_id)

    def submit_player_response(
        self,
        response: PlayerResponse,
        *,
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        return submit_player_response_runtime(
            response,
            now=now,
            current_time=_now,
            question_store=self._question_store,
            delivered_player_responses=self._delivered_player_responses,
            task_runtimes=self._task_runtimes,
        )

    def tick(self, *, now: Optional[float] = None) -> int:
        return tick_question_timeouts(
            now=now,
            current_time=_now,
            question_store=self._question_store,
            delivered_player_responses=self._delivered_player_responses,
            task_runtimes=self._task_runtimes,
        )

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
        rebalance_resources_runtime(
            jobs=self._jobs,
            tasks=self.tasks,
            resource_needs=self._resource_needs,
            world_model=self.world_model,
            is_terminal_status=self._is_terminal_status,
            release_job_resources=self._release_job_resources,
            set_task_actor_group=self._set_task_actor_group,
            task_owner_for_actor=lambda actor_id: next(
                (task_id for task_id, group in self._task_actor_groups.items() if actor_id in group),
                None,
            ),
            resource_loss_notified=self._resource_loss_notified,
            sync_world_runtime=self._sync_world_runtime,
        )

    def _claim_resource(self, controller: BaseJob | _ManagedJob, need: ResourceNeed) -> Optional[str]:
        return claim_resource_runtime(
            controller,
            need,
            world_model=self.world_model,
            tasks=self.tasks,
            jobs=self._jobs,
            release_job_resources=self._release_job_resources,
            set_task_actor_group=self._set_task_actor_group,
            task_owner_for_actor=lambda actor_id: next(
                (task_id for task_id, group in self._task_actor_groups.items() if actor_id in group),
                None,
            ),
        )

    def _find_unbound_resource(self, need: ResourceNeed) -> Optional[str]:
        return find_unbound_resource_runtime(
            need,
            world_model=self.world_model,
            controller_task_id=None,
            task_owner_for_actor=lambda actor_id: next(
                (task_id for task_id, group in self._task_actor_groups.items() if actor_id in group),
                None,
            ),
        )

    def _find_preemptable_resource(self, requester: BaseJob | _ManagedJob, need: ResourceNeed) -> Optional[dict[str, Any]]:
        return find_preemptable_resource_runtime(
            requester,
            need,
            tasks=self.tasks,
            jobs=self._jobs,
            world_model=self.world_model,
            task_owner_for_actor=lambda actor_id: next(
                (task_id for task_id, group in self._task_actor_groups.items() if actor_id in group),
                None,
            ),
        )

    def _preempt_resource(self, holder: BaseJob | _ManagedJob, resource_id: str) -> None:
        preempt_resource_runtime(
            holder,
            resource_id,
            release_job_resources=self._release_job_resources,
            unbind_resource=self.world_model.unbind_resource,
        )

    def _grant_resource(self, controller: BaseJob | _ManagedJob, resource_id: str) -> None:
        grant_resource_runtime(
            controller,
            resource_id,
            bind_resource=self.world_model.bind_resource,
            set_task_actor_group=self._set_task_actor_group,
        )

    def _resources_for_need(self, controller: BaseJob | _ManagedJob, need: ResourceNeed) -> list[str]:
        return resources_for_need_runtime(
            controller,
            need,
            actors_by_id=self.world_model.state.actors,
        )

    def _resource_matches_need(self, resource_id: str, need: ResourceNeed) -> bool:
        return resource_matches_need_runtime(
            resource_id,
            need,
            actors_by_id=self.world_model.state.actors,
        )

    def _actor_matches_need(self, actor: Any, need: ResourceNeed) -> bool:
        return actor_matches_need_runtime(actor, need)

    def _notify_resource_loss(self, controller: BaseJob | _ManagedJob, need: ResourceNeed, missing: int) -> None:
        notify_resource_loss_runtime(
            controller,
            need,
            missing,
            resource_loss_notified=self._resource_loss_notified,
        )

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
