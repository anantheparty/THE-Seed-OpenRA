"""Kernel v1: deterministic Task / Job lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Awaitable, Callable, Optional, Protocol

from benchmark import span as bm_span
from experts.base import BaseJob, ExecutionExpert
from llm import LLMProvider
from logging_system import get_logger
from models import (
    CombatJobConfig,
    Constraint,
    ConstraintEnforcement,
    EngagementMode,
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
    TaskMessageType,
    TaskStatus,
    validate_job_config,
)
from models.configs import EXPERT_CONFIG_REGISTRY
from task_agent import AgentConfig, TaskAgent, ToolExecutor, WorldSummary
from world_model import WorldModel

slog = get_logger("kernel")


def _now() -> float:
    return time.time()


def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class TaskAgentLike(Protocol):
    task: Task

    async def run(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def push_signal(self, signal: ExpertSignal) -> None:
        ...

    def push_event(self, event: Event) -> None:
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
        self.register_auto_response_rule(
            "base_under_attack_defend_base",
            EventType.BASE_UNDER_ATTACK,
            self._handle_base_under_attack_auto_response,
        )

    def create_task(self, raw_text: str, kind: TaskKind | str, priority: int) -> Task:
        with bm_span("tool_exec", name="kernel:create_task"):
            task_kind = kind if isinstance(kind, TaskKind) else TaskKind(kind)
            task = Task(
                task_id=_gen_id("t_"),
                raw_text=raw_text,
                kind=task_kind,
                priority=priority,
                status=TaskStatus.RUNNING,
            )
            tool_executor = self._build_tool_executor(task.task_id)
            agent = self.task_agent_factory(
                task,
                tool_executor,
                self.jobs_for_task,
                self._task_world_summary,
            )
            runtime = _TaskRuntime(task=task, agent=agent, tool_executor=tool_executor)
            self.tasks[task.task_id] = task
            self._task_runtimes[task.task_id] = runtime
            self._sync_world_runtime()
            self._maybe_start_agent(runtime)
            slog.info("Task created", event="task_created", task_id=task.task_id, raw_text=raw_text, kind=task.kind.value, priority=priority)
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
            task.status = TaskStatus.ABORTED
            task.timestamp = _now()
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
            self._rebalance_resources()
            self._sync_world_runtime()
            slog.info("Job started", event="job_started", task_id=task_id, job_id=controller.job_id, expert_type=expert_type, config=config)
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
            if event.type in {EventType.UNIT_DIED, EventType.UNIT_DAMAGED}:
                self._route_actor_event(event)
                return
            if event.type in {EventType.ENEMY_DISCOVERED, EventType.STRUCTURE_LOST}:
                self._broadcast_event(event)
                return
            if event.type == EventType.BASE_UNDER_ATTACK:
                self._broadcast_event(event)
                return
            if event.type in {EventType.ENEMY_EXPANSION, EventType.FRONTLINE_WEAK, EventType.ECONOMY_SURPLUS}:
                self._push_player_notification(event)
                return
            if event.type == EventType.PRODUCTION_COMPLETE:
                self._rebalance_resources()
                return
            return None

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
        runtime.agent.push_signal(signal)

    def get_task_agent(self, task_id: str) -> Optional[TaskAgentLike]:
        runtime = self._task_runtimes.get(task_id)
        return runtime.agent if runtime else None

    def jobs_for_task(self, task_id: str) -> list[Job]:
        jobs = [controller.to_model() for controller in self._jobs.values() if controller.task_id == task_id]
        jobs.sort(key=lambda item: item.job_id)
        return jobs

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

    def _build_tool_executor(self, task_id: str) -> ToolExecutor:
        executor = ToolExecutor()
        executor.register_all(
            {
                "start_job": self._tool_start_job(task_id),
                "patch_job": self._tool_patch_job,
                "pause_job": self._tool_pause_job,
                "resume_job": self._tool_resume_job,
                "abort_job": self._tool_abort_job,
                "complete_task": self._tool_complete_task(task_id),
                "create_constraint": self._tool_create_constraint,
                "remove_constraint": self._tool_remove_constraint,
                "query_world": self._tool_query_world,
                "query_planner": self._tool_query_planner,
                "cancel_tasks": self._tool_cancel_tasks,
            }
        )
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
        self.world_model.set_runtime_state(
            active_tasks={
                task.task_id: {
                    "raw_text": task.raw_text,
                    "kind": task.kind.value,
                    "priority": task.priority,
                    "status": task.status.value,
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
        )

    def _task_world_summary(self) -> WorldSummary:
        summary = self.world_model.world_summary()
        return WorldSummary(
            economy=summary.get("economy", {}),
            military=summary.get("military", {}),
            map=summary.get("map", {}),
            known_enemy=summary.get("known_enemy", {}),
            timestamp=summary.get("timestamp", _now()),
        )

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
            return [
                ResourceNeed(
                    job_id=controller.job_id,
                    kind=ResourceKind.ACTOR,
                    count=1,
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

    def _ensure_defend_base_task(self) -> Task:
        for task in self.tasks.values():
            if task.raw_text == "defend_base" and task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED, TaskStatus.PARTIAL}:
                return task
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
        return {
            "proposal": {
                "planner_type": args["planner_type"],
                "status": "unimplemented",
                "reason": "Planner integration is scheduled after Kernel task lifecycle.",
            }
        }

    async def _tool_cancel_tasks(self, _: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"count": self.cancel_tasks(args["filters"])}
