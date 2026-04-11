"""Kernel-side task runtime lifecycle helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from models import JobStatus


class RuntimeLike(Protocol):
    agent: Any
    runner: Any


class JobLike(Protocol):
    job_id: str
    task_id: str
    status: JobStatus
    resources: list[str]


def maybe_start_agent(runtime: RuntimeLike, *, auto_start_agents: bool) -> None:
    if not auto_start_agents:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    runtime.runner = loop.create_task(runtime.agent.run())


def stop_task_runtime(task_runtimes: Mapping[str, RuntimeLike], task_id: str) -> None:
    runtime = task_runtimes.get(task_id)
    if runtime is None:
        return
    runtime.agent.stop()
    if runtime.runner is not None:
        runtime.runner.cancel()
        runtime.runner = None


def release_job_resources(
    controller: JobLike,
    *,
    unbind_resource: Callable[[str], None],
) -> None:
    resource_ids = list(controller.resources)
    if controller.status != JobStatus.ABORTED and hasattr(controller, "on_resource_revoked"):
        controller.on_resource_revoked(resource_ids)  # type: ignore[attr-defined]
    else:
        controller.resources = []
    for resource_id in resource_ids:
        unbind_resource(resource_id)


def release_task_job_resources(
    jobs: Mapping[str, JobLike],
    task_id: str,
    *,
    release_job_resources_fn: Callable[[JobLike], None],
    on_job_released: Callable[[str], None],
) -> None:
    for controller in jobs.values():
        if controller.task_id != task_id:
            continue
        if controller.resources:
            release_job_resources_fn(controller)
        on_job_released(controller.job_id)
