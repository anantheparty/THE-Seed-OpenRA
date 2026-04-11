"""Helpers for live unit-request fulfillment and task wake-up."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from logging_system import get_logger
from models import Event, UnitRequest

from .unit_request_lifecycle import build_unit_assigned_event, release_ready_task_requests
from .unit_request_matching import hint_match_score, matching_idle_actors, sort_pending_requests
from .unit_request_state import update_request_status_from_progress

slog = get_logger("kernel")


def agent_is_suspended(agent: Any) -> bool:
    flag = getattr(agent, "is_suspended", None)
    if flag is not None:
        return bool(flag)
    return bool(getattr(agent, "_suspended", False))


def suspend_agent_for_requests(
    task_id: str,
    *,
    task_has_blocking_wait: Callable[[str], bool],
    task_runtimes: Mapping[str, Any],
) -> None:
    """Suspend a task agent while blocking requests are still unresolved."""
    if not task_has_blocking_wait(task_id):
        return
    runtime = task_runtimes.get(task_id)
    if runtime is None:
        return
    runtime.agent.suspend()


def wake_waiting_agent(
    task_id: str,
    *,
    task_has_blocking_wait: Callable[[str], bool],
    task_runtimes: Mapping[str, Any],
    unit_requests: Iterable[UnitRequest],
    reservation_for_request: Callable[[UnitRequest], Any],
    request_can_start: Callable[[UnitRequest], bool],
    handoff_request_assignments: Callable[[UnitRequest], list[int]],
    now: Callable[[], float],
    sync_world_runtime: Callable[[], None],
) -> None:
    """Resume a task once blocking requests have reached their start package."""
    if task_has_blocking_wait(task_id):
        return
    runtime = task_runtimes.get(task_id)
    if runtime is None:
        return
    assigned_ids, fully_fulfilled = release_ready_task_requests(
        unit_requests,
        task_id,
        reservation_for_request=reservation_for_request,
        request_can_start=request_can_start,
        handoff_request_assignments=handoff_request_assignments,
        now=now,
    )
    if not assigned_ids:
        return
    event: Event = build_unit_assigned_event(
        assigned_ids=assigned_ids,
        fully_fulfilled=fully_fulfilled,
    )
    if agent_is_suspended(runtime.agent):
        runtime.agent.resume_with_event(event)
    else:
        runtime.agent.push_event(event)
    sync_world_runtime()
    slog.info(
        "Agent woken after request fulfillment",
        event="agent_woken_requests_fulfilled",
        task_id=task_id,
        actor_ids=assigned_ids,
        fully_fulfilled=fully_fulfilled,
    )


def fulfill_unit_requests(
    *,
    unit_requests: Mapping[str, UnitRequest],
    world_model: Any,
    category_to_actor_category: dict[str, str],
    urgency_weight: dict[str, int],
    task_priority_for: Callable[[str], int],
    request_start_goal: Callable[[UnitRequest], int],
    bind_actor_to_request: Callable[[UnitRequest, Any], None],
    reconcile_request_bootstrap: Callable[[UnitRequest], None],
    wake_waiting_agent: Callable[[str], None],
    sync_world_runtime: Callable[[], None],
) -> None:
    """Scan live idle units and assign them to pending requests by priority."""
    if not unit_requests:
        return
    idle = world_model.find_actors(owner="self", idle_only=True, unbound_only=True)
    if not idle:
        return
    runtime_dirty = False

    pending = sort_pending_requests(
        [request for request in unit_requests.values() if request.status in ("pending", "partial")],
        idle,
        category_to_actor_category=category_to_actor_category,
        urgency_weight=urgency_weight,
        task_priority_for=task_priority_for,
        request_start_goal=request_start_goal,
    )
    if not pending:
        return

    for req in pending:
        remaining = req.count - req.fulfilled
        if remaining <= 0 or req.category == "building":
            continue
        matched = matching_idle_actors(
            req,
            idle,
            category_to_actor_category=category_to_actor_category,
        )
        matched.sort(key=lambda actor: hint_match_score(actor, req.hint), reverse=True)
        for actor in matched[:remaining]:
            bind_actor_to_request(req, actor)
            idle.remove(actor)
            runtime_dirty = True

        update_request_status_from_progress(req)
        reconcile_request_bootstrap(req)
        wake_waiting_agent(req.task_id)

        if not idle:
            break

    if runtime_dirty:
        sync_world_runtime()
