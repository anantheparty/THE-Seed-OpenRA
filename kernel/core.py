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
from models import (
    Constraint,
    ConstraintEnforcement,
    Event,
    ExpertConfig,
    ExpertSignal,
    Job,
    JobStatus,
    Task,
    TaskKind,
    TaskStatus,
    validate_job_config,
)
from models.configs import EXPERT_CONFIG_REGISTRY
from task_agent import AgentConfig, TaskAgent, ToolExecutor, WorldSummary
from world_model import WorldModel


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
            task.status = TaskStatus.ABORTED
            task.timestamp = _now()
            self._stop_agent(task_id)
            self._sync_world_runtime()
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
            self._stop_agent(task_id)
            self._sync_world_runtime()
            return True

    def start_job(self, task_id: str, expert_type: str, config: ExpertConfig) -> Job:
        with bm_span("tool_exec", name="kernel:start_job", metadata={"expert_type": expert_type}):
            task = self._require_task(task_id)
            validate_job_config(expert_type, config)
            controller = self._make_job_controller(task_id, expert_type, config)
            self._jobs[controller.job_id] = controller
            task.status = TaskStatus.RUNNING
            task.timestamp = _now()
            self._sync_world_runtime()
            return controller.to_model()

    def abort_job(self, job_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:abort_job"):
            controller = self._jobs.get(job_id)
            if controller is None:
                return False
            controller.abort()
            self._release_job_resources(controller)
            self._sync_world_runtime()
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
            controller.pause()
            controller.status = JobStatus.WAITING
            self._sync_world_runtime()
            return True

    def resume_job(self, job_id: str) -> bool:
        with bm_span("tool_exec", name="kernel:resume_job"):
            controller = self._require_job(job_id)
            controller.resume()
            controller.status = JobStatus.RUNNING
            self._sync_world_runtime()
            return True

    def route_event(self, event: Event) -> None:
        """Placeholder for 1.3c event routing."""
        return None

    def route_events(self, events: list[Event]) -> None:
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
