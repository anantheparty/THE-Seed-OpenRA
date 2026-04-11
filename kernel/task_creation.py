"""Helpers for kernel task creation and task-entry messaging."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, MutableSet
from dataclasses import dataclass
from typing import Any, Optional

from benchmark import span as bm_span
from logging_system import get_logger
from models import Event, EventType, Task, TaskKind, TaskStatus

slog = get_logger("kernel")


@dataclass(frozen=True, slots=True)
class CreateTaskResult:
    task: Task
    task_seq: int


@dataclass(frozen=True, slots=True)
class EnsureCapabilityTaskResult:
    task_id: Optional[str]
    capability_recent_inputs: list[dict[str, Any]]
    created: bool = False


def create_task(
    *,
    raw_text: str,
    kind: TaskKind | str,
    priority: int,
    is_capability: bool = False,
    info_subscriptions: list | None,
    skip_agent: bool,
    task_seq: int,
    tasks: MutableMapping[str, Task],
    task_runtimes: MutableMapping[str, Any],
    direct_managed_tasks: MutableSet[str],
    task_agent_factory: Callable[..., Any],
    build_tool_executor: Callable[[Task], Any],
    jobs_provider: Callable[[str], list[Any]],
    world_summary_provider: Callable[[], Any],
    runtime_factory: Callable[[Task, Any, Any], Any],
    maybe_start_agent: Callable[[Any], None],
    world_model: Any,
    current_capability_task_id: Callable[[], Optional[str]],
    other_active_tasks_for: Callable[[str], list[dict]],
    sync_world_runtime: Callable[[], None],
    gen_id: Callable[[str], str],
) -> CreateTaskResult:
    with bm_span("tool_exec", name="kernel:create_task"):
        task_kind = kind if isinstance(kind, TaskKind) else TaskKind(kind)
        new_task_seq = task_seq + 1
        task_label = f"{new_task_seq:03d}"
        task = Task(
            task_id=gen_id("t_"),
            raw_text=raw_text,
            kind=task_kind,
            priority=priority,
            status=TaskStatus.RUNNING,
            label=task_label,
            info_subscriptions=list(info_subscriptions) if info_subscriptions else [],
            is_capability=is_capability,
        )
        tool_executor = build_tool_executor(task)
        agent = task_agent_factory(
            task,
            tool_executor,
            jobs_provider,
            world_summary_provider,
        )
        if hasattr(agent, "set_runtime_facts_provider"):
            agent.set_runtime_facts_provider(
                lambda task_id, _task=task: world_model.compute_runtime_facts(
                    task_id,
                    include_buildable=bool(
                        getattr(_task, "is_capability", False)
                        or task_id == current_capability_task_id()
                    ),
                )
            )
        if hasattr(agent, "set_active_tasks_provider"):
            agent.set_active_tasks_provider(other_active_tasks_for)

        runtime = runtime_factory(task, agent, tool_executor)
        tasks[task.task_id] = task
        task_runtimes[task.task_id] = runtime
        if skip_agent:
            direct_managed_tasks.add(task.task_id)
        sync_world_runtime()
        if not skip_agent:
            maybe_start_agent(runtime)

        from logging_system import current_session_dir as _csd

        session_dir = _csd()
        log_path = (
            str(session_dir / "tasks" / f"{task.task_id}.jsonl")
            if session_dir
            else f"tasks/{task.task_id}.jsonl"
        )
        slog.info(
            "Task created",
            event="task_created",
            task_id=task.task_id,
            task_label=task_label,
            raw_text=raw_text,
            kind=task.kind.value,
            priority=priority,
            task_log_path=log_path,
        )
        return CreateTaskResult(task=task, task_seq=new_task_seq)


def ensure_capability_task(
    *,
    enable_capability_task: bool,
    capability_task_id: Optional[str],
    tasks: MutableMapping[str, Task],
    create_task_fn: Callable[..., Task],
) -> EnsureCapabilityTaskResult:
    if not enable_capability_task:
        return EnsureCapabilityTaskResult(
            task_id=capability_task_id,
            capability_recent_inputs=[],
            created=False,
        )
    if capability_task_id and capability_task_id in tasks:
        task = tasks[capability_task_id]
        if task.status == TaskStatus.RUNNING:
            return EnsureCapabilityTaskResult(
                task_id=capability_task_id,
                capability_recent_inputs=[],
                created=False,
            )

    task = create_task_fn(
        raw_text="EconomyCapability — 持久经济规划",
        kind=TaskKind.MANAGED,
        priority=90,
        is_capability=True,
        info_subscriptions=["base_state", "threat", "production"],
    )
    return EnsureCapabilityTaskResult(
        task_id=task.task_id,
        capability_recent_inputs=[],
        created=True,
    )


def is_direct_managed(task_id: str, *, direct_managed_tasks: set[str]) -> bool:
    return task_id in direct_managed_tasks


def inject_player_message(
    *,
    task_id: str,
    text: str,
    tasks: Mapping[str, Task],
    task_runtimes: Mapping[str, Any],
    direct_managed_tasks: set[str],
    capability_task_id: Optional[str],
    capability_recent_inputs: list[dict[str, Any]],
    sync_world_runtime: Callable[[], None],
    now: Callable[[], float],
) -> bool:
    task = tasks.get(task_id)
    if task is None:
        return False
    if task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING):
        return False
    if task_id in direct_managed_tasks:
        return False
    runtime = task_runtimes.get(task_id)
    if runtime is None:
        return False
    timestamp = now()
    event = Event(
        type=EventType.PLAYER_MESSAGE,
        data={"text": text, "timestamp": timestamp},
    )
    runtime.agent.push_event(event)
    if task_id == capability_task_id:
        capability_recent_inputs.append({"text": text, "timestamp": timestamp})
        del capability_recent_inputs[:-5]
        sync_world_runtime()
    slog.info(
        "Player message injected",
        event="player_message_injected",
        task_id=task_id,
        text=text[:80],
    )
    return True
