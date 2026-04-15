"""Kernel-level event orchestration helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, MutableSet
from typing import Any, Optional

from logging_system import get_logger
from models import Event, EventType

from .event_delivery import append_player_notification, broadcast_event, route_actor_event
from .session_reset import clear_kernel_runtime_collections, stop_all_task_runtimes

slog = get_logger("kernel")


def handle_game_reset(
    event: Event,
    *,
    task_runtimes: MutableMapping[str, Any],
    tasks: MutableMapping[str, Any],
    jobs: MutableMapping[str, Any],
    constraints: MutableMapping[str, Any],
    resource_needs: MutableMapping[str, Any],
    resource_loss_notified: MutableSet[str],
    player_notifications: MutableSequence[dict[str, Any]],
    task_messages: MutableSequence[Any],
    reset_questions: Callable[[], None],
    delivered_player_responses: MutableMapping[str, Any],
    unit_requests: MutableMapping[str, Any],
    unit_reservations: MutableMapping[str, Any],
    request_reservations: MutableMapping[str, Any],
    task_actor_groups: MutableMapping[str, Any],
    direct_managed_tasks: MutableSet[str],
    capability_recent_inputs: MutableSequence[Any],
    stop_task_runtime_fn: Callable[[Mapping[str, Any], str], None],
    set_capability_task_id: Callable[[Optional[str]], None],
    set_runtime_state: Callable[..., None],
    push_player_notification: Callable[..., None],
    ensure_capability_task: Callable[[], Optional[str]],
) -> None:
    stop_all_task_runtimes(
        task_runtimes,
        stop_task_runtime_fn=stop_task_runtime_fn,
    )
    clear_kernel_runtime_collections(
        tasks=tasks,
        task_runtimes=task_runtimes,
        jobs=jobs,
        constraints=constraints,
        resource_needs=resource_needs,
        resource_loss_notified=resource_loss_notified,
        player_notifications=player_notifications,
        task_messages=task_messages,
        reset_questions=reset_questions,
        delivered_player_responses=delivered_player_responses,
        unit_requests=unit_requests,
        unit_reservations=unit_reservations,
        request_reservations=request_reservations,
        task_actor_groups=task_actor_groups,
        direct_managed_tasks=direct_managed_tasks,
        capability_recent_inputs=capability_recent_inputs,
        clear_player_notifications=False,
        clear_task_messages=True,
    )
    set_capability_task_id(None)
    set_runtime_state(
        active_tasks={},
        active_jobs={},
        resource_bindings={},
        constraints=[],
        capability_status={},
        unit_reservations=[],
    )
    push_player_notification(
        "game_reset",
        "检测到对局已重置，已清理旧任务状态",
        data=event.data,
        timestamp=event.timestamp,
    )
    slog.warn(
        "Kernel cleared stale runtime after game reset",
        event="game_reset_handled",
        data=event.data,
    )
    ensure_capability_task()


def route_runtime_event(
    event: Event,
    *,
    apply_auto_response_rules: Callable[[Event], None],
    handle_game_reset: Callable[[Event], None],
    jobs: Mapping[str, Any],
    task_runtimes: Mapping[str, Any],
    world_model: Any,
    is_terminal_job_status: Callable[[Any], bool],
    rebalance_resources: Callable[[], None],
    sync_world_runtime: Callable[[], None],
    capability_task_id: Optional[str],
    player_notifications: MutableSequence[dict[str, Any]],
    fulfill_unit_requests: Callable[[], None],
) -> None:
    apply_auto_response_rules(event)
    if event.type == EventType.GAME_RESET:
        handle_game_reset(event)
        return
    if event.type in {EventType.UNIT_DIED, EventType.UNIT_DAMAGED}:
        route_actor_event(
            event,
            jobs=jobs,
            task_runtimes=task_runtimes,
            world_model=world_model,
            is_terminal_job_status=is_terminal_job_status,
            rebalance_resources=rebalance_resources,
            sync_world_runtime=sync_world_runtime,
        )
        return
    if event.type in {
        EventType.ENEMY_DISCOVERED,
        EventType.STRUCTURE_LOST,
        EventType.BASE_UNDER_ATTACK,
    }:
        broadcast_event(event, task_runtimes=task_runtimes)
        return
    if event.type == EventType.LOW_POWER:
        append_player_notification(player_notifications, event)
        if capability_task_id:
            runtime = task_runtimes.get(capability_task_id)
            if runtime is not None:
                runtime.agent.push_event(event)
        return
    if event.type in {
        EventType.ENEMY_EXPANSION,
        EventType.FRONTLINE_WEAK,
        EventType.ECONOMY_SURPLUS,
    }:
        append_player_notification(player_notifications, event)
        return
    if event.type == EventType.PRODUCTION_COMPLETE:
        rebalance_resources()
        fulfill_unit_requests()
        return
